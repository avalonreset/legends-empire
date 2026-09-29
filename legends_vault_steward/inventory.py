from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import NoteRecord
from .parse import (
    aliases_from_frontmatter,
    dated_bullet_count,
    extract_links,
    injection_like,
    parse_frontmatter,
    word_count,
)
from .paths import VaultRoot, inspect_file, is_contained


@dataclass
class InventoryResult:
    notes: dict[str, NoteRecord] = field(default_factory=dict)
    skipped_escapes: list[str] = field(default_factory=list)
    skipped_unreadable: list[str] = field(default_factory=list)
    skipped_dir_symlinks: list[str] = field(default_factory=list)
    markdown_bytes: int = 0


def _should_skip_dir(rel: str, name: str, config: dict) -> bool:
    skip_names = {item.lower() for item in config.get("skip_dir_names", [])}
    if name.lower() in skip_names:
        return True
    prefixes = config.get("skip_dir_prefixes", [])
    posix = rel.replace("\\", "/")
    if posix and not posix.endswith("/"):
        posix_dir = posix + "/"
    else:
        posix_dir = posix
    for prefix in prefixes:
        needle = prefix.replace("\\", "/")
        if posix == needle.rstrip("/") or posix_dir.startswith(needle):
            return True
    return False


def walk_notes(vault: VaultRoot, config: dict) -> InventoryResult:
    result = InventoryResult()
    suffixes = tuple(item.lower() for item in config.get("note_suffixes", [".md"]))
    patterns = config.get("injection_patterns", [])
    attachments = config.get("attachment_suffixes", [])
    root = vault.real

    def walk_error(error: OSError) -> None:
        failed = Path(error.filename) if error.filename else root
        try:
            name = failed.relative_to(root).as_posix()
        except ValueError:
            name = str(failed)
        result.skipped_unreadable.append(name)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        current = Path(dirpath)
        if current != root and current.is_symlink():
            rel = current.relative_to(root).as_posix() if is_contained(current, vault) else dirpath
            result.skipped_dir_symlinks.append(str(rel))
            dirnames[:] = []
            continue
        rel_dir = "" if current == root else current.relative_to(root).as_posix()
        keep: list[str] = []
        for name in dirnames:
            child_rel = f"{rel_dir}/{name}" if rel_dir else name
            if _should_skip_dir(child_rel, name, config):
                continue
            child = current / name
            if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
                result.skipped_dir_symlinks.append(child_rel)
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)

        for name in sorted(filenames):
            if not name.lower().endswith(suffixes):
                continue
            path = current / name
            inspected = inspect_file(path, vault)
            if inspected is None:
                result.skipped_escapes.append(str(path))
                continue
            rel, is_link = inspected
            try:
                data = path.read_bytes()
                stat = path.stat()
            except OSError:
                result.skipped_unreadable.append(rel)
                continue
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                result.skipped_unreadable.append(rel)
                continue
            fm, body = parse_frontmatter(text)
            title = fm.get("title")
            if isinstance(title, list):
                title = str(title[0]) if title else None
            elif title is not None:
                title = str(title)
            updated = fm.get("updated") or fm.get("created")
            if isinstance(updated, list):
                updated = str(updated[0]) if updated else None
            elif updated is not None:
                updated = str(updated)
            note = NoteRecord(
                path=rel,
                abs_path=str(path),
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
                mtime_ns=getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
                words=word_count(text),
                title=title,
                frontmatter=fm,
                aliases=aliases_from_frontmatter(fm, title),
                outbound=extract_links(body, rel, attachments),
                injection_like=injection_like(text, patterns),
                dated_bullets=dated_bullet_count(text),
                updated=updated,
                symlink=is_link,
            )
            result.notes[rel] = note
            result.markdown_bytes += len(data)
    return result
