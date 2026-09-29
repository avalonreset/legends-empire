from __future__ import annotations

from collections import defaultdict
import posixpath
from pathlib import PurePosixPath

from .models import LinkRef, NoteRecord


def _stem(path: str) -> str:
    name = PurePosixPath(path).name
    if name.lower().endswith(".markdown"):
        return name[: -len(".markdown")]
    if name.lower().endswith(".md"):
        return name[:-3]
    return name


def _norm(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


def build_indexes(notes: dict[str, NoteRecord]) -> dict[str, list[str]]:
    """Map normalized lookup keys to candidate note paths."""
    index: dict[str, list[str]] = defaultdict(list)

    def add(key: str, path: str) -> None:
        if not key:
            return
        bucket = index[key]
        if path not in bucket:
            bucket.append(path)

    for path, note in notes.items():
        add(_norm(path), path)
        add(_norm(_stem(path)), path)
        no_ext = path[:-3] if path.lower().endswith(".md") else path
        if no_ext.lower().endswith(".markdown"):
            no_ext = no_ext[: -len(".markdown")]
        add(_norm(no_ext), path)
        for alias in note.aliases:
            add(_norm(alias), path)
        if note.title:
            add(_norm(str(note.title)), path)
    return index


def _lookup(index: dict[str, list[str]], key: str) -> list[str]:
    return list(index.get(_norm(key), []))


def resolve_target(target: str, source_path: str, index: dict[str, list[str]], kind: str = "wikilink") -> tuple[str, str | None, tuple[str, ...]]:
    cleaned = target.strip().replace("\\", "/")
    source_dir = str(PurePosixPath(source_path).parent)
    candidates: list[str] = []

    def consider(keys: list[str]) -> None:
        for key in keys:
            for path in _lookup(index, key):
                if path not in candidates:
                    candidates.append(path)

    def path_keys(path: str) -> list[str]:
        path = posixpath.normpath(path)
        if path == ".." or path.startswith("../"):
            return []
        # Path resolution must not consult basename/alias index entries.
        keys = [path]
        if not path.lower().endswith((".md", ".markdown")):
            keys.extend([path + ".md", path + ".markdown"])
        return keys

    def consider_paths(path: str) -> None:
        keys = path_keys(path)
        for key in keys:
            for candidate in _lookup(index, key):
                candidate_no_ext = candidate.rsplit(".", 1)[0]
                if _norm(key) in {_norm(candidate), _norm(candidate_no_ext)} and candidate not in candidates:
                    candidates.append(candidate)

    if cleaned.startswith("/"):
        consider_paths(cleaned.lstrip("/"))
    elif kind == "markdown" or cleaned.startswith(("./", "../")):
        consider_paths(posixpath.join(source_dir, cleaned))
    elif "/" in cleaned:
        consider_paths(cleaned)
        if not candidates:
            consider_paths(posixpath.join(source_dir, cleaned))
    else:
        consider_paths(posixpath.join(source_dir, cleaned))
        if not candidates:
            consider([cleaned, _stem(cleaned)])

    if len(candidates) == 1:
        return "resolved", candidates[0], tuple(candidates)
    if len(candidates) > 1:
        return "ambiguous", None, tuple(sorted(candidates))
    return "unresolved", None, ()


def resolve_notes(notes: dict[str, NoteRecord]) -> dict[str, list[str]]:
    index = build_indexes(notes)
    inbound: dict[str, list[str]] = defaultdict(list)
    for path, note in notes.items():
        updated: list[LinkRef] = []
        for link in note.outbound:
            if link.status == "attachment":
                updated.append(link)
                continue
            status, resolved, candidates = resolve_target(link.target, path, index, link.kind)
            if resolved == path:
                status = "self"
            new_link = LinkRef(
                raw=link.raw,
                target=link.target,
                kind=link.kind,
                heading=link.heading,
                alias=link.alias,
                source_path=path,
                status=status,
                resolved_path=resolved,
                candidates=candidates,
            )
            updated.append(new_link)
            if status == "resolved" and resolved:
                inbound[resolved].append(path)
        note.outbound = updated
    for path, sources in inbound.items():
        unique = sorted(set(item for item in sources if item != path))
        notes[path].inbound_from = unique
    for path, note in notes.items():
        if not note.inbound_from:
            note.inbound_from = []
    return index
