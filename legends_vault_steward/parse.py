from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from .models import LinkRef

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
WIKILINK_RE = re.compile(r"(!)?\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})")
DATED_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+.*\d{4}-\d{2}-\d{2}")
SIMPLE_SCALAR = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")


def strip_fences(text: str) -> str:
    """Drop fenced code so example wikilinks do not enter the graph."""
    kept: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            char = match.group(2)[0]
            length = len(match.group(2))
            if fence_char is None:
                fence_char = char
                fence_len = length
                continue
            if char == fence_char and length >= fence_len:
                fence_char = None
                fence_len = 0
                continue
        if fence_char is None:
            kept.append(line)
    return "\n".join(kept)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    text = text.lstrip("\ufeff")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end():]
    data: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_list_key and stripped.startswith("- "):
            value = _unquote(stripped[2:].strip())
            existing = data.get(current_list_key)
            if not isinstance(existing, list):
                existing = [] if existing is None else [existing]
            existing.append(value)
            data[current_list_key] = existing
            continue
        scalar = SIMPLE_SCALAR.match(stripped)
        if not scalar:
            current_list_key = None
            continue
        key, value = scalar.group(1), scalar.group(2).strip()
        if value == "" or value == "|":
            current_list_key = key
            data[key] = []
            continue
        current_list_key = None
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [_unquote(part.strip()) for part in inner.split(",") if part.strip()]
        else:
            data[key] = _unquote(value)
    return data, body


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def dated_bullet_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if DATED_BULLET_RE.search(line))


def injection_like(text: str, patterns: list[str]) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in patterns)


def _split_wikilink(raw: str) -> tuple[str, str | None, str | None]:
    alias = None
    heading = None
    target = raw.strip()
    if "|" in target:
        target, alias = target.split("|", 1)
        target, alias = target.strip(), alias.strip()
    if "#" in target:
        target, heading = target.split("#", 1)
        target, heading = target.strip(), heading.strip() or None
    return target, heading, alias


def extract_links(body: str, source_path: str, attachment_suffixes: list[str]) -> list[LinkRef]:
    scan = strip_fences(body)
    scan = re.sub(r"<!--.*?(?:-->|\Z)", "", scan, flags=re.DOTALL)
    scan = re.sub(r"(?<!`)(`+)(?!`)(.*?)\1(?!`)", "", scan, flags=re.DOTALL)
    links: list[LinkRef] = []
    seen: set[tuple[str, str, str]] = set()
    attach = {suffix.lower() for suffix in attachment_suffixes}

    for match in WIKILINK_RE.finditer(scan):
        kind = "embed" if match.group(1) else "wikilink"
        target, heading, alias = _split_wikilink(match.group(2))
        if not target:
            continue
        key = (kind, target, heading or "")
        if key in seen:
            continue
        seen.add(key)
        status = "pending"
        lower = target.lower()
        if any(lower.endswith(suffix) for suffix in attach):
            status = "attachment"
        links.append(
            LinkRef(
                raw=match.group(0),
                target=target,
                kind=kind,
                heading=heading,
                alias=alias,
                source_path=source_path,
                status=status,
            )
        )

    for match in MD_LINK_RE.finditer(scan):
        dest = match.group(2).strip()
        # Optional Markdown titles do not form part of the destination.
        if dest.startswith("<") and ">" in dest:
            dest = dest[1:dest.index(">")]
        else:
            dest = re.split(r'\s+[\"\']', dest, maxsplit=1)[0]
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", dest) or dest.startswith("//"):
            continue
        dest = dest.split("#", 1)[0].split("?", 1)[0].strip()
        dest = unquote(dest)
        if not dest:
            continue
        lower = dest.lower()
        if any(lower.endswith(suffix) for suffix in attach):
            continue
        if not (lower.endswith(".md") or lower.endswith(".markdown") or "/" in dest or dest.endswith(".md")):
            if "." in dest.split("/")[-1]:
                continue
        key = ("markdown", dest, "")
        if key in seen:
            continue
        seen.add(key)
        links.append(
            LinkRef(
                raw=match.group(0),
                target=dest,
                kind="markdown",
                source_path=source_path,
            )
        )
    return links


def aliases_from_frontmatter(fm: dict[str, Any], title: str | None) -> tuple[str, ...]:
    values: list[str] = []
    raw = fm.get("aliases") or fm.get("alias")
    if isinstance(raw, list):
        values.extend(str(item) for item in raw if str(item).strip())
    elif isinstance(raw, str) and raw.strip():
        values.append(raw.strip())
    if title and title not in values:
        values.append(title)
    # preserve order, drop empties
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return tuple(out)
