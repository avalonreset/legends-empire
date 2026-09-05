#!/usr/bin/env python3
"""Install per-skill links without replacing any existing user path.

No host configuration, hooks, credentials, or vault files are modified. Both
shell entry points delegate here so Windows and POSIX share one contract.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_PATHS = {
    "grok": ".grok/skills",
    "codex": ".agents/skills",
    "gemini": ".gemini/skills",
    "claude": ".claude/skills",
    "opencode": ".config/opencode/skills",
    "cursor": ".cursor/skills",
    "windsurf": ".windsurf/skills",
}
DEFAULT_HOSTS = ("grok", "codex", "gemini", "claude", "opencode")
WORKSPACE_ONLY = {"cursor", "windsurf"}


def linked(path: Path) -> bool:
    """Include junctions and dangling links, also on Python 3.11."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def parents_safe(anchor: Path, parent: Path, create: bool = False) -> None:
    cursor = anchor
    for component in parent.relative_to(anchor).parts:
        cursor /= component
        if linked(cursor):
            raise ValueError(f"parent is a symlink or junction: {cursor}")
        if cursor.exists() and not cursor.is_dir():
            raise ValueError(f"parent is not a directory: {cursor}")
        if create and not cursor.exists():
            cursor.mkdir()
        if linked(cursor):
            raise ValueError(f"parent changed during install: {cursor}")


def state(anchor: Path, source: Path, destination: Path) -> str:
    parents_safe(anchor, destination.parent)
    if linked(destination):
        if destination.resolve() == source:
            return "READY"
        raise ValueError(f"{destination} points elsewhere")
    if destination.exists():
        raise ValueError(f"{destination} already exists")
    return "PLANNED"


def create_link(source: Path, destination: Path) -> None:
    if os.name != "nt":
        destination.symlink_to(source, target_is_directory=True)
        return
    # Junctions need no administrator / Developer Mode on NTFS. Paths are
    # environment data, never interpolated into shell source text.
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise ValueError("PowerShell is required for Windows directory junctions")
    environment = dict(os.environ)
    environment["LEGENDS_LINK_SOURCE"] = str(source)
    environment["LEGENDS_LINK_DESTINATION"] = str(destination)
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
         "$ErrorActionPreference='Stop'; "
         "New-Item -ItemType Junction -Path $env:LEGENDS_LINK_DESTINATION "
         "-Target $env:LEGENDS_LINK_SOURCE | Out-Null"],
        env=environment, capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "junction creation failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="preview only (default)")
    modes.add_argument("--check", action="store_true", help="exit 1 if links are missing")
    modes.add_argument("--apply", action="store_true", help="create missing links")
    parser.add_argument("--host", action="append", choices=[*HOST_PATHS, "all"],
                        help="repeat to choose hosts; default: five user-level hosts")
    parser.add_argument("--home", type=Path, help="explicit existing home directory")
    parser.add_argument("--workspace", type=Path, help="existing workspace directory")
    parser.add_argument("--scope", choices=["user", "workspace"], default="user")
    options = parser.parse_args(argv)
    hosts = list(dict.fromkeys(
        item for host in (options.host or DEFAULT_HOSTS)
        for item in (HOST_PATHS if host == "all" else [host])
    ))
    try:
        home = (options.home or Path.home()).resolve(strict=True)
        workspace = options.workspace.resolve(strict=True) if options.workspace else None
        if not home.is_dir() or (workspace and not workspace.is_dir()):
            raise ValueError("home and workspace must be existing directories")
        if (options.scope == "workspace" or WORKSPACE_ONLY.intersection(hosts)) and not workspace:
            raise ValueError("selected host/scope requires --workspace PATH")
        skills = sorted(path for path in (ROOT / "skills").iterdir()
                        if path.is_dir() and (path / "SKILL.md").is_file())
        if not skills:
            raise ValueError("no skills found in product installation")
        for skill in skills:
            if linked(skill) or not skill.resolve().is_relative_to(ROOT):
                raise ValueError(f"source skill must be a real product directory: {skill}")
        plans = []
        for host in hosts:
            anchor = workspace if host in WORKSPACE_ONLY or options.scope == "workspace" else home
            assert anchor is not None
            relative = ".opencode/skills" if host == "opencode" and options.scope == "workspace" else HOST_PATHS[host]
            for source in skills:
                destination = anchor / relative / source.name
                status = state(anchor, source, destination)
                plans.append((host, anchor, source, destination, status))
        # Preflight the entire plan before creating anything. I/O failures may
        # leave new links; an ordinary retry verifies those links and resumes.
        for host, anchor, source, destination, status in plans:
            if options.apply and status == "PLANNED":
                parents_safe(anchor, destination.parent, create=True)
                if state(anchor, source, destination) != "PLANNED":
                    raise ValueError(f"destination changed during install: {destination}")
                create_link(source, destination)
                if state(anchor, source, destination) != "READY":
                    raise ValueError(f"link verification failed: {destination}")
                status = "CREATED"
            print(f"{status} {host} {destination}")
        if not options.apply and not options.check:
            print("Dry run only. Repeat with --apply to create the planned links.")
        return int(options.check and any(plan[-1] == "PLANNED" for plan in plans))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"CONFLICT {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11+ is required.")
    sys.exit(main())
