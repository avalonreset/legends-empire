from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STEWARD_STATE_DIR = ".steward"
MAX_JSON_INPUT_BYTES = 1_048_576
_DARWIN_SYMLINK_DIRS = frozenset({"/var", "/tmp", "/etc"})
_CFA_DIR_NAMES = ("Desktop", "Documents", "Pictures", "Videos", "Music", "Favorites")


class PathEscapeError(ValueError):
    """Resolved path left the declared vault root."""


class ReportPathError(ValueError):
    """Report --out is inside the vault but not under the reserved .steward tree."""


def _is_symlink_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    if hasattr(path, "is_junction"):
        try:
            if path.is_junction():
                return True
        except OSError:
            return True
    return False


def check_no_symlink_chain(
    raw: str | os.PathLike[str],
    *,
    error_cls: type[Exception] = PathEscapeError,
) -> Path:
    path = Path(raw).expanduser()
    abs_path = Path(os.path.abspath(path))
    for part in (abs_path, *abs_path.parents):
        if sys.platform == "darwin" and part.as_posix() in _DARWIN_SYMLINK_DIRS:
            continue
        if _is_symlink_or_junction(part):
            raise error_cls(f"path must not traverse a symlink or junction: {raw}")
    return abs_path


def check_not_cfa_protected(resolved: Path) -> None:
    if os.name != "nt":
        return
    roots: list[Path] = []
    for env_key in ("USERPROFILE", "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        val = os.environ.get(env_key, "").strip()
        if val:
            try:
                roots.append(Path(val).resolve())
            except OSError:
                continue
    for base in roots:
        for folder in _CFA_DIR_NAMES:
            protected = base / folder
            try:
                resolved.relative_to(protected)
                raise ReportPathError(
                    f"report --out targets a Windows Controlled Folder Access protected directory: {resolved}"
                )
            except ValueError:
                continue


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key: {key}")
        obj[key] = value
    return obj


def _reject_nonfinite_constant(token: str) -> Any:
    raise ValueError(f"non-standard JSON constant is not allowed: {token}")


def read_bounded_json(
    raw: str | os.PathLike[str],
    *,
    max_bytes: int = MAX_JSON_INPUT_BYTES,
) -> Any:
    abs_path = check_no_symlink_chain(raw, error_cls=ValueError)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(abs_path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"JSON input is not a regular file: {raw}")
        if st.st_size > max_bytes:
            raise ValueError(f"JSON input exceeds {max_bytes}-byte limit: {raw}")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise ValueError(f"JSON input exceeds {max_bytes}-byte limit: {raw}")
    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_constant,
    )


@dataclass(frozen=True)
class VaultRoot:
    given: Path
    real: Path

    @property
    def as_posix(self) -> str:
        return self.real.as_posix()

    @property
    def steward_dir(self) -> Path:
        return self.real / STEWARD_STATE_DIR


def resolve_vault_root(raw: str | os.PathLike[str]) -> VaultRoot:
    given = Path(raw).expanduser()
    check_no_symlink_chain(given, error_cls=PathEscapeError)
    if not given.exists():
        raise FileNotFoundError(f"vault root does not exist: {given}")
    if not given.is_dir():
        raise NotADirectoryError(f"vault root is not a directory: {given}")
    real = given.resolve()
    return VaultRoot(given=given, real=real)


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def is_contained(path: Path, vault: VaultRoot) -> bool:
    return _is_contained(path, vault.real)


def rel_posix(path: Path, vault: VaultRoot) -> str:
    resolved = path.resolve() if path.exists() else path
    return resolved.relative_to(vault.real).as_posix()


def safe_rel_posix(path: Path, vault: VaultRoot) -> str | None:
    try:
        if not is_contained(path, vault):
            return None
        return rel_posix(path, vault)
    except (OSError, ValueError):
        return None


def inspect_file(path: Path, vault: VaultRoot) -> tuple[str, bool] | None:
    """Return (vault-relative posix path, is_symlink) or None if the file escapes or is a link."""
    if _is_symlink_or_junction(path):
        return None
    if not is_contained(path, vault):
        return None
    return rel_posix(path, vault), False


def _resolved_out(raw: str | os.PathLike[str]) -> Path:
    path = check_no_symlink_chain(raw, error_cls=ReportPathError)
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ReportPathError(f"cannot resolve report --out: {path}: {exc}") from exc


def is_reserved_steward_dir(path: Path, vault: VaultRoot) -> bool:
    try:
        rel = path.resolve(strict=False).relative_to(vault.real)
    except (OSError, ValueError):
        return False
    parts = rel.parts
    return bool(parts) and parts[0] == STEWARD_STATE_DIR


def validate_report_dir(raw: str | os.PathLike[str], vault: VaultRoot) -> Path:
    """Allow --out only outside the vault, or under <vault>/.steward/.

    Refuses symlinks/junctions in the path chain and Windows CFA protected folders.
    """
    resolved = _resolved_out(raw)
    check_not_cfa_protected(resolved)
    if resolved.exists() and not resolved.is_dir():
        raise ReportPathError(f"report --out must be a directory, not a file: {resolved}")
    if not is_contained(resolved, vault):
        return resolved
    if is_reserved_steward_dir(resolved, vault):
        return resolved
    raise ReportPathError(
        "report --out must resolve outside the vault or under "
        f"{vault.real.as_posix()}/{STEWARD_STATE_DIR}/; refused {resolved.as_posix()}"
    )
