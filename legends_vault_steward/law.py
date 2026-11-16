from __future__ import annotations

import hashlib
import os
import re
import stat
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .models import Finding, SEVERITY_ORDER
from .parse import FENCE_RE, iter_unfenced_lines, parse_frontmatter
from .paths import (
    MAX_ENTRY_FILE_BYTES,
    VaultRoot,
    _is_symlink_or_junction,
    is_contained,
    is_disallowed_path_token,
    redact_home_text,
    rel_posix,
)

MAX_LAW_FILES = 512
MAX_LAW_LINE_CHARS = 4096
EXPIRED_DIRECTIVE_RE = re.compile(
    r"(?i)\b(?:through|until|ends?|expires?)\b[ \t]{1,16}[*_]{0,6}(\d{4}-\d{2}-\d{2})\b"
)
_NESTED_QUANTIFIER_RE = re.compile(r"([+*?]|\{\d+,?\d*\})\s*\)[+*?{]")
_SECRET_LIKE_RE = re.compile(
    r"(?i)(?:^sk_live_|^rk_live_|^gh[pousr]_|^aiza|^akia|^xox[baprs]-|^eyJ[A-Za-z0-9_-]{10,}|bearer\s+\S+)"
)


@dataclass(frozen=True)
class LawFile:
    display_path: str
    abs_path: Path
    text: str
    in_entry_chain: bool = False


def _looks_like_secret(value: str) -> bool:
    if len(value) > 64:
        return True
    if _SECRET_LIKE_RE.search(value):
        return True
    if len(value) >= 32 and " " not in value and any(c.isdigit() for c in value) and any(c.isalpha() for c in value):
        return True
    return False


def _read_contained_vault_file(path: Path, vault: VaultRoot) -> str | None:
    if _is_symlink_or_junction(path) or not is_contained(path, vault):
        return None
    cur = path
    while cur != vault.real and cur.parent != cur:
        if _is_symlink_or_junction(cur):
            return None
        cur = cur.parent
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_ENTRY_FILE_BYTES:
            return None
        chunks: list[bytes] = []
        rem = MAX_ENTRY_FILE_BYTES + 1
        while rem > 0:
            chunk = os.read(fd, rem)
            if not chunk:
                break
            chunks.append(chunk)
            rem -= len(chunk)
        data = b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > MAX_ENTRY_FILE_BYTES:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def collect_law_files(
    vault: VaultRoot,
    config: dict[str, Any],
    entry_chain_files: list[LawFile] | None = None,
) -> list[LawFile]:
    out: list[LawFile] = []
    seen_real: set[Path] = set()

    if entry_chain_files:
        for lf in entry_chain_files:
            try:
                resolved = lf.abs_path.resolve(strict=False)
            except OSError:
                resolved = lf.abs_path
            if resolved not in seen_real:
                seen_real.add(resolved)
                out.append(lf)
    elif not config.get("entry_harnesses"):
        for ep in config.get("entrypoints", []):
            if not isinstance(ep, str) or is_disallowed_path_token(ep, allow_tilde=False) is not None:
                continue
            cand = vault.real / ep
            try:
                resolved = cand.resolve(strict=False)
            except OSError:
                continue
            if resolved in seen_real or not cand.exists():
                continue
            text = _read_contained_vault_file(cand, vault)
            if text is not None:
                seen_real.add(resolved)
                out.append(
                    LawFile(
                        display_path=rel_posix(cand, vault),
                        abs_path=resolved,
                        text=text,
                        in_entry_chain=True,
                    )
                )

    law_globs = config.get("law_paths", [])
    added_from_globs = 0
    for pattern in law_globs:
        if not isinstance(pattern, str) or not pattern.strip():
            continue
        clean = pattern.strip().replace("\\", "/")
        if is_disallowed_path_token(clean, allow_tilde=False) is not None or Path(clean).is_absolute() or ".." in Path(clean).parts:
            continue
        try:
            matches = sorted(vault.real.glob(clean))
        except (OSError, ValueError):
            continue
        for match in matches:
            if added_from_globs >= MAX_LAW_FILES:
                break
            try:
                resolved = match.resolve(strict=False)
            except OSError:
                continue
            if resolved in seen_real:
                continue
            text = _read_contained_vault_file(match, vault)
            if text is None:
                continue
            seen_real.add(resolved)
            added_from_globs += 1
            out.append(
                LawFile(
                    display_path=rel_posix(match, vault),
                    abs_path=resolved,
                    text=text,
                    in_entry_chain=False,
                )
            )
        if added_from_globs >= MAX_LAW_FILES:
            break
    return out


def _extract_paragraphs(text: str) -> list[tuple[int, str]]:
    """Return (start_line_1based, normalized_paragraph) outside frontmatter and code fences."""
    _, body = parse_frontmatter(text)
    prefix_len = len(text) - len(body)
    line_offset = text[:prefix_len].count("\n")

    paragraphs: list[tuple[int, str]] = []
    current_lines: list[str] = []
    current_start = 1
    fence_char: str | None = None
    fence_len = 0

    def flush() -> None:
        nonlocal current_lines
        if not current_lines:
            return
        norm = re.sub(r"\s+", " ", " ".join(current_lines)).strip()
        if len(norm) >= 200:
            paragraphs.append((current_start, norm))
        current_lines = []

    for idx, line in enumerate(body.splitlines(), start=1):
        actual_line = line_offset + idx
        match = FENCE_RE.match(line)
        if match:
            flush()
            char = match.group(2)[0]
            length = len(match.group(2))
            if fence_char is None:
                fence_char = char
                fence_len = length
            elif char == fence_char and length >= fence_len:
                fence_char = None
                fence_len = 0
            continue
        if fence_char is not None:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        if not current_lines:
            current_start = actual_line
        current_lines.append(stripped)
    flush()
    return paragraphs


def _validate_conflict_keys(conflict_keys: list[Any]) -> list[tuple[str, re.Pattern[str]]]:
    compiled_list: list[tuple[str, re.Pattern[str]]] = []
    for ck in conflict_keys:
        if not isinstance(ck, dict) or not isinstance(ck.get("id"), str) or not ck["id"].strip():
            raise ValueError("each conflict_keys item requires a non-empty string id")
        key_id = ck["id"].strip()
        pattern = ck.get("pattern")
        if not isinstance(pattern, str) or not pattern or len(pattern) > 256:
            raise ValueError(f"conflict_keys '{key_id}' pattern must be a 1..256 char string")
        if _NESTED_QUANTIFIER_RE.search(pattern):
            raise ValueError(f"conflict_keys '{key_id}' pattern contains a nested quantifier")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"conflict_keys '{key_id}' invalid regex: {exc}") from exc
        if compiled.groups != 1:
            raise ValueError(f"conflict_keys '{key_id}' pattern must have exactly 1 capture group")
        compiled_list.append((key_id, compiled))
    return compiled_list


def evaluate_laws(
    files: list[LawFile],
    config: dict[str, Any],
    scan_date: date,
) -> list[Finding]:
    findings: list[Finding] = []
    compiled_keys = _validate_conflict_keys(config.get("conflict_keys", []))

    # 1. law.expired_directive
    for lf in files:
        disp = redact_home_text(lf.display_path)
        for line_no, raw_line in iter_unfenced_lines(lf.text):
            bounded_line = raw_line[:MAX_LAW_LINE_CHARS]
            for match in EXPIRED_DIRECTIVE_RE.finditer(bounded_line):
                date_str = match.group(1)
                try:
                    directive_date = date.fromisoformat(date_str)
                except ValueError:
                    continue
                if directive_date < scan_date:
                    sev = "high" if lf.in_entry_chain else "medium"
                    findings.append(
                        Finding(
                            code="law.expired_directive",
                            severity=sev,
                            path=disp,
                            record_class=None,
                            message=(
                                f"Expired directive '{match.group(0).strip()}' at {disp}:{line_no} "
                                f"is earlier than {scan_date.isoformat()}"
                            ),
                            extra={
                                "line": line_no,
                                "date": date_str,
                                "file_line": f"{disp}:{line_no}",
                                "in_entry_chain": lf.in_entry_chain,
                            },
                        )
                    )

    # 2. law.duplicate_block (O2: entry-chain by default, plus opt-in law_paths)
    entry_only = bool(config.get("duplicate_block_entry_only", False))
    block_locations: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for lf in files:
        if entry_only and not lf.in_entry_chain:
            continue
        disp = redact_home_text(lf.display_path)
        for start_line, norm_para in _extract_paragraphs(lf.text):
            block_locations[norm_para].append((disp, start_line))

    for norm_para, locs in sorted(block_locations.items(), key=lambda kv: (kv[1][0][0], kv[1][0][1])):
        distinct_files = sorted({p for p, _ in locs})
        if len(distinct_files) < 2:
            continue
        loc_strings = [f"{p}:{ln}" for p, ln in locs]
        block_sha = hashlib.sha256(norm_para.encode("utf-8")).hexdigest()[:12]
        findings.append(
            Finding(
                code="law.duplicate_block",
                severity="low",
                path=locs[0][0],
                record_class=None,
                message=f"Duplicate paragraph block ({len(norm_para)} chars) across {len(distinct_files)} files: {', '.join(loc_strings)}",
                extra={
                    "locations": loc_strings,
                    "files": distinct_files,
                    "chars": len(norm_para),
                    "sha256": block_sha,
                },
            )
        )

    # 3. law.value_conflict
    for key_id, pattern in compiled_keys:
        by_value: dict[str, list[str]] = defaultdict(list)
        for lf in files:
            disp = redact_home_text(lf.display_path)
            for line_no, raw_line in iter_unfenced_lines(lf.text):
                bounded_line = raw_line[:MAX_LAW_LINE_CHARS]
                for match in pattern.finditer(bounded_line):
                    val = (match.group(1) or "").strip()
                    if not val or _looks_like_secret(val):
                        continue
                    loc = f"{disp}:{line_no}"
                    if loc not in by_value[val]:
                        by_value[val].append(loc)
        if len(by_value) > 1:
            ordered = {val: by_value[val] for val in sorted(by_value)}
            summary = "; ".join(f"{val} ({', '.join(locs)})" for val, locs in ordered.items())
            first_file = next(iter(ordered.values()))[0].split(":", 1)[0]
            findings.append(
                Finding(
                    code="law.value_conflict",
                    severity="medium",
                    path=first_file,
                    record_class=None,
                    message=f"Conflicting values for '{key_id}': {summary}",
                    extra={"key": key_id, "values": ordered},
                )
            )

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item.severity, 9),
            item.code,
            item.path or "",
            item.message,
        )
    )
    return findings
