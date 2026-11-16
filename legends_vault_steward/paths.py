from __future__ import annotations

import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STEWARD_STATE_DIR = ".steward"
MAX_JSON_INPUT_BYTES = 1_048_576
MAX_BASELINE_JSON_BYTES = 67_108_864
MAX_ENTRY_FILE_BYTES = 262_144
_DARWIN_SYMLINK_DIRS = frozenset({"/var", "/tmp", "/etc"})
_CFA_DIR_NAMES = ("Desktop", "Documents", "Pictures", "Videos", "Music", "Favorites")
_WIN_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_DRIVE_REL_RE = re.compile(r"^[A-Za-z]:(?![/\\])")
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:(?=[/\\])")
_UNC_PREFIX_RE = re.compile(r"^[/\\]{2}")


def get_home_dir() -> Path:
    return Path(os.path.expanduser("~")).resolve(strict=False)


def redact_home_text(text: str) -> str:
    if not text:
        return text
    out = str(text)
    candidates: list[str] = []
    for raw_home in (os.path.expanduser("~"), os.environ.get("USERPROFILE", ""), os.environ.get("HOME", "")):
        if not raw_home or raw_home == "~":
            continue
        for variant in (raw_home, str(Path(raw_home).resolve(strict=False))):
            norm_bs = variant.rstrip("/\\")
            norm_fs = norm_bs.replace("\\", "/")
            for item in (norm_bs, norm_fs):
                if len(item) > 1 and item not in candidates:
                    candidates.append(item)
    candidates.sort(key=len, reverse=True)
    for cand in candidates:
        pattern = re.compile(re.escape(cand), re.IGNORECASE if os.name == "nt" else 0)
        out = pattern.sub("~", out)
    return out


def is_disallowed_path_token(
    raw: str,
    *,
    allow_tilde: bool = True,
    reject_env: bool = True,
) -> str | None:
    s_raw = str(raw)
    s = s_raw.strip()
    if not s:
        return "empty path"
    if "\x00" in s_raw:
        return "null byte in path"
    if _UNC_PREFIX_RE.match(s) or s.startswith(("\\??\\", "/??/")) or re.match(r"^[/\\]\?\?[/\\]", s):
        return "UNC path is not permitted"
    if _DRIVE_REL_RE.match(s):
        return "drive-relative path is not permitted"
    rest = _DRIVE_PREFIX_RE.sub("", s, count=1)
    if ":" in rest:
        return "colon or alternate data stream in path is not permitted"
    if s.startswith("~"):
        if not allow_tilde:
            return "home-relative path is not permitted here"
        if not (s == "~" or s.startswith("~/") or s.startswith("~\\")):
            return "other-user home expansion is not permitted"
    parts = [p for p in re.split(r"[/\\]+", rest) if p]
    for part in parts:
        seg = part if part in {".", ".."} else part.rstrip(" .")
        if not seg:
            return f"trailing dot or space in path segment is not permitted: {part}"
        stem = seg.split(".", 1)[0].upper()
        if stem in _WIN_DEVICE_NAMES:
            return f"Windows device name is not permitted: {part}"
    if reject_env and parts:
        leaf = parts[-1].rstrip(" .").lower()
        if leaf == ".env" or leaf.startswith(".env.") or leaf.endswith(".env"):
            return ".env files are not permitted"
    for part in parts:
        if part not in {".", ".."} and part.rstrip(" .") != part:
            return f"trailing dot or space in path segment is not permitted: {part}"
    return None



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
        raw_str = str(path)
        if _UNC_PREFIX_RE.match(raw_str) or raw_str.startswith(("\\??\\", "/??/")):
            return False
        if os.path.splitdrive(raw_str)[0].startswith(("\\\\", "//")):
            return False
        abs_str = os.path.abspath(path)
        if _UNC_PREFIX_RE.match(abs_str) or abs_str.startswith(("\\??\\", "/??/")):
            return False
        path_drive = os.path.splitdrive(abs_str)[0]
        root_drive = os.path.splitdrive(str(root))[0] or os.path.splitdrive(os.path.abspath(root))[0]
        if path_drive.startswith(("\\\\", "//")) or path_drive.upper() != root_drive.upper():
            return False
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
