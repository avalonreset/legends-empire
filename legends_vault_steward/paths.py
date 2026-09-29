from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

STEWARD_STATE_DIR = ".steward"


class PathEscapeError(ValueError):
    """Resolved path left the declared vault root."""


class ReportPathError(ValueError):
    """Report --out is inside the vault but not under the reserved .steward tree."""


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
    if not given.exists():
        raise FileNotFoundError(f"vault root does not exist: {given}")
    if not given.is_dir():
        raise NotADirectoryError(f"vault root is not a directory: {given}")
    real = given.resolve()
    if given.is_symlink() and not _is_contained(real, real):
        raise PathEscapeError(f"vault root symlink is not self-contained: {given}")
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
    """Return (vault-relative posix path, is_symlink) or None if the file escapes."""
    if path.is_symlink():
        try:
            target = path.resolve()
        except OSError:
            return None
        if not _is_contained(target, vault.real):
            return None
        try:
            rel = path.relative_to(vault.real).as_posix()
        except ValueError:
            return None
        return rel, True
    if not is_contained(path, vault):
        return None
    return rel_posix(path, vault), False


def _resolved_out(raw: str | os.PathLike[str]) -> Path:
    path = Path(raw).expanduser()
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

    Resolves symlinks before the check so a .steward link into inbox is refused.
    """
    resolved = _resolved_out(raw)
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
