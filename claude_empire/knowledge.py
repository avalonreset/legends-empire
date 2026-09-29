"""Attach inspectable, versioned Markdown knowledge without owning user records.

Planning only. All writes are performed by the existing Empire transaction core.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from .ledgers import strict_json_loads
from .paths import is_name_surrogate
from .transaction import (
    TransactionValidationError, _assert_portable_write_path,
    read_vault_regular, sha256_bytes,
)

PACK_SCHEMA = "legends.knowledge-pack/v1"
STATE_SCHEMA = "legends.knowledge-pack-state/v1"
MAX_FILES = 256
MAX_BYTES = 8 * 1024 * 1024


def _fail(message: str) -> None:
    raise TransactionValidationError("INVALID_KNOWLEDGE_PACK", message)


def _json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _parse(payload: bytes, label: str) -> dict:
    try:
        value = strict_json_loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        _fail(f"invalid {label}: {exc}")
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")
    return value


def _relative(value: Any, *, markdown: bool = False) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("paths must be nonempty relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(p in (".", "..") for p in path.parts):
        _fail(f"noncanonical or escaping path: {value!r}")
    _assert_portable_write_path(value)
    if markdown and path.suffix != ".md":
        _fail(f"knowledge files must be Markdown: {value}")
    return value


def _destination(value: Any) -> str:
    value = _relative(value)
    if not value.startswith("wiki/library/"):
        _fail("destination must be below wiki/library/")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        _fail("sha256 must be a lowercase 64-character digest")
    return value


def _check_names(paths) -> None:
    """Reject aliases in directory components as well as complete filenames."""
    spelling: dict[str, str] = {}
    file_keys = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            key = unicodedata.normalize("NFC", prefix).casefold()
            if key in spelling and spelling[key] != prefix:
                _fail(f"case-colliding knowledge paths: {spelling[key]}, {prefix}")
            if count < len(parts) and key in file_keys:
                _fail(f"knowledge path is both file and directory: {prefix}")
            spelling[key] = prefix
        full_key = unicodedata.normalize("NFC", path).casefold()
        if any(other.startswith(full_key + "/") for other in spelling):
            _fail(f"knowledge path is both file and directory: {path}")
        file_keys.add(full_key)


def _root(path: Path) -> Path:
    """Reject symlink/junction ancestors before using the core no-follow reader."""
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        info = ancestor.lstat()
        if is_name_surrogate(info):
            _fail(f"symlink or reparse path is not supported: {ancestor}")
    if not absolute.is_dir():
        _fail(f"not a directory: {absolute}")
    return absolute.resolve()


def _identity(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value):
        _fail("pack and module identities must be lowercase hyphenated identifiers")
    return value


def _version(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        _fail("version must be a stable X.Y.Z version")
    return value


def load_pack(manifest_path: Path | str) -> tuple[dict, dict[str, bytes], str, Path]:
    path = Path(manifest_path).expanduser().absolute()
    parent = _root(path.parent)
    raw = read_vault_regular(parent, path.name, missing_ok=False, limit=1024 * 1024)
    manifest = _parse(raw, "manifest")
    if set(manifest) != {"schema", "id", "module", "version", "entrypoint", "files", "ontology"}:
        _fail("manifest fields do not match the v1 contract")
    if manifest["schema"] != PACK_SCHEMA:
        _fail("unsupported knowledge pack schema")
    _identity(manifest["id"])
    _identity(manifest["module"])
    _version(manifest["version"])
    _relative(manifest["entrypoint"], markdown=True)
    ontology = manifest["ontology"]
    if not isinstance(ontology, dict) or set(ontology) != {"kind", "suggested_home", "purpose"}:
        _fail("ontology requires kind, suggested_home and purpose")
    if ontology["kind"] not in {"machinery", "library"}:
        _fail("ontology kind must be machinery or library")
    _destination(ontology["suggested_home"])
    if not isinstance(ontology["purpose"], str) or not ontology["purpose"].strip() or len(ontology["purpose"]) > 2000:
        _fail("ontology purpose must be a nonempty string of at most 2000 characters")
    rows = manifest["files"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        _fail(f"manifest must contain 1 through {MAX_FILES} files")
    source = _root(parent / "vault")
    contents: dict[str, bytes] = {}
    names: set[str] = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            _fail("each file requires path and sha256")
        relative = _relative(row["path"], markdown=True)
        folded = unicodedata.normalize("NFC", relative).casefold()
        if folded in names:
            _fail(f"duplicate or case-colliding knowledge path: {relative}")
        names.add(folded)
        payload = read_vault_regular(source, relative, missing_ok=False, limit=MAX_BYTES)
        total += len(payload)
        if total > MAX_BYTES:
            _fail("knowledge pack exceeds 8 MiB")
        if sha256_bytes(payload) != _digest(row["sha256"]):
            _fail(f"knowledge hash mismatch: {relative}")
        try:
            payload.decode("utf-8")
        except UnicodeError:
            _fail(f"knowledge is not UTF-8: {relative}")
        contents[relative] = payload
    if manifest["entrypoint"] not in contents:
        _fail("entrypoint is not a declared file")
    _check_names(contents)
    return manifest, contents, sha256_bytes(raw), parent


def _state(raw: bytes, manifest: dict, destination: str) -> dict:
    state = _parse(raw, "pack state")
    fields = {"schema", "id", "module", "version", "manifest_sha256", "destination", "entrypoint", "generated_at", "files", "state_sha256"}
    if set(state) != fields or state["schema"] != STATE_SCHEMA:
        _fail("unsupported or malformed pack state")
    checksum = _digest(state["state_sha256"])
    if sha256_bytes(_json({k: v for k, v in state.items() if k != "state_sha256"})) != checksum:
        _fail("pack state integrity mismatch; inspect it before continuing")
    if any(state[key] != manifest[key] for key in ("id", "module")) or state["destination"] != destination:
        _fail("destination belongs to another pack or location")
    _version(state["version"])
    _digest(state["manifest_sha256"])
    _relative(state["entrypoint"], markdown=True)
    files = state["files"]
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        _fail("invalid owned file ledger")
    names: set[str] = set()
    for path, row in files.items():
        _relative(path, markdown=True)
        folded = unicodedata.normalize("NFC", path).casefold()
        if folded in names:
            _fail("state contains case-colliding paths")
        names.add(folded)
        if not isinstance(row, dict) or set(row) != {"sha256", "obsolete"} or type(row["obsolete"]) is not bool:
            _fail("invalid owned file state")
        _digest(row["sha256"])
    if state["entrypoint"] not in files:
        _fail("state entrypoint is missing from owned files")
    return state


def attachment_bundle(vault_root: Path | str, manifest_path: Path | str, *, operation_id: str,
                      generated_at: str, destination: str | None = None) -> dict:
    """Build a complete generic transaction, or an empty writes list for a no-op."""
    try:
        stamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        _fail("generated_at must be an ISO timestamp with a timezone")
    if stamp.tzinfo is None:
        _fail("generated_at must include a timezone")
    manifest, contents, manifest_hash, source = load_pack(manifest_path)
    root = _root(Path(vault_root))
    if root == source or root in source.parents or source in root.parents:
        _fail("user vault and pack source must be separate, non-nested directories")
    destination = _destination(destination or manifest["ontology"]["suggested_home"])
    state_path, index_path = f"{destination}/pack-state.json", f"{destination}/index.md"
    old_bytes = read_vault_regular(root, state_path)
    old = _state(old_bytes, manifest, destination) if old_bytes is not None else None
    index = read_vault_regular(root, index_path)
    if old is None and index is not None:
        _fail("destination index already exists without pack ownership; select a new destination")
    if old is not None and index is None:
        _fail("attached pack index is missing; inspect the user change before continuing")
    if old and old["version"] == manifest["version"] and old["manifest_sha256"] != manifest_hash:
        _fail("same pack version has different manifest bytes; publish a new version")
    if old and old["entrypoint"] != manifest["entrypoint"]:
        _fail("entrypoint changed; explicit user-index migration is required")
    tracked = {} if old is None else dict(old["files"])
    _check_names(set(tracked) | set(contents))
    # Bind every observed owned file, including obsolete and unchanged references.
    observed: dict[str, str | None] = {state_path: sha256_bytes(old_bytes) if old_bytes is not None else None,
                                     index_path: sha256_bytes(index) if index is not None else None}
    for relative, row in tracked.items():
        target = f"{destination}/reference/{relative}"
        payload = read_vault_regular(root, target)
        digest = sha256_bytes(payload) if payload is not None else None
        if digest != row["sha256"]:
            _fail(f"owned reference was modified or removed: {relative}; preserve and review it")
        observed[target] = digest
    writes: list[dict] = []

    def write(path: str, payload: bytes, prior: str | None) -> None:
        observed[path] = prior
        writes.append({"path": path, "mode": "create" if prior is None else "replace", "content": payload.decode("utf-8")})

    for relative, payload in sorted(contents.items()):
        target = f"{destination}/reference/{relative}"
        if relative not in tracked:
            existing = read_vault_regular(root, target)
            if existing is not None:
                _fail(f"new reference collides with unowned content: {relative}")
            observed[target] = None
        digest = sha256_bytes(payload)
        if observed[target] != digest:
            write(target, payload, observed[target])
        tracked[relative] = {"sha256": digest, "obsolete": False}
    for relative in tracked.keys() - contents.keys():
        tracked[relative] = {**tracked[relative], "obsolete": True}
    if len(tracked) > MAX_FILES:
        _fail("retained references exceed the pack limit; review an explicit migration")
    if index is None:
        text = (f"# {manifest['module']}: {manifest['id']}\n\n"
                f"{manifest['ontology']['purpose']}\n\n"
                f"Read the [reference shelf](reference/{quote(manifest['entrypoint'], safe='/')}). "
                "It is a versioned offline snapshot of reusable module knowledge.\n\n"
                f"Resolve the installed module through `cto-legends handoff {manifest['module']}`. "
                "The installed version and this snapshot can differ; pack-state.json records the snapshot.\n\n"
                "This index is yours to edit. Keep private campaigns, decisions and evidence in your existing "
                "vault locations, or deliberately create a work/ folder beside this index. "
                "Pack updates never write those records. Edited references cause a conflict, never an overwrite. "
                "Retired upstream documents remain here and are marked obsolete in pack-state.json.\n")
        write(index_path, text.encode("utf-8"), None)
    changed = old is None or old["manifest_sha256"] != manifest_hash or old["files"] != tracked
    if changed:
        state = {"schema": STATE_SCHEMA, "id": manifest["id"], "module": manifest["module"],
                 "version": manifest["version"], "manifest_sha256": manifest_hash,
                 "destination": destination, "entrypoint": manifest["entrypoint"],
                 "generated_at": generated_at, "files": tracked}
        state["state_sha256"] = sha256_bytes(_json(state))
        write(state_path, _json(state), observed[state_path])
    writing = {row["path"] for row in writes}
    return {"schema": "claude-empire.transaction.v1", "operation_id": operation_id,
            "operation_type": "generic", "generated_at": generated_at,
            "expected_hashes": {path: digest for path, digest in observed.items() if path in writing},
            "read_preconditions": {path: digest for path, digest in observed.items() if path not in writing},
            "writes": writes}
