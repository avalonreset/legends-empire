#!/usr/bin/env python3
"""Acceptance tests for the read-only installed-product boundary."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_LAUNCHERS = tuple(sorted((ROOT / "scripts").glob("*.py")))
SETUP_SCRIPT_NAMES = frozenset(
    {
        "setup-dragonscale.sh",
        "setup-mode.sh",
        "setup-retrieve.sh",
        "setup-vault.sh",
    }
)
SHELL_LAUNCHERS = tuple(
    sorted(
        path
        for path in (ROOT / "scripts").glob("*.sh")
        if path.name in SETUP_SCRIPT_NAMES
        or "python3" in path.read_text(encoding="utf-8")
    )
)
PRODUCT_DIRS = (
    "claude_empire",
    "config",
    "hooks",
    "legends_vault_steward",
    "scripts",
    "skills",
    "templates",
)


def _git_bash() -> str:
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
            Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            lowered = directory.lower()
            if "system32" in lowered or "windowsapps" in lowered:
                continue
            candidate = Path(directory) / "bash.exe"
            if candidate.is_file():
                return str(candidate)
        raise unittest.SkipTest("Git Bash is required on Windows")
    return shutil.which("bash") or "/bin/bash"


def copy_product(destination: Path) -> None:
    for relative in PRODUCT_DIRS:
        source = ROOT / relative
        if source.is_dir():
            shutil.copytree(
                source,
                destination / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )


def fingerprint(root: Path) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_dir():
            result[relative] = ("directory", "")
        elif path.is_file():
            result[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        else:
            result[relative] = ("other", "")
    return result


class InstalledTreeBoundaryTests(unittest.TestCase):
    def test_make_test_disables_bytecode_for_imported_test_modules(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("export PYTHONDONTWRITEBYTECODE := 1", makefile)

    def test_every_python_launcher_disables_bytecode_before_local_imports(self) -> None:
        self.assertTrue(PYTHON_LAUNCHERS)
        for launcher in PYTHON_LAUNCHERS:
            with self.subTest(launcher=launcher.name):
                source = launcher.read_text(encoding="utf-8")
                guard = source.find("sys.dont_write_bytecode = True")
                self.assertGreaterEqual(guard, 0)
                local_imports = [
                    position
                    for marker in (
                        "from claude_empire",
                        "import claude_empire",
                        "spec.loader.exec_module",
                    )
                    if (position := source.find(marker)) >= 0
                ]
                # Standalone standard-library helpers have no product imports.
                # Every helper still sets the guard; importing helpers must set it first.
                if local_imports:
                    self.assertLess(guard, min(local_imports))

    def test_shell_launchers_export_the_bytecode_policy(self) -> None:
        self.assertTrue(SHELL_LAUNCHERS)
        for launcher in SHELL_LAUNCHERS:
            with self.subTest(launcher=launcher.relative_to(ROOT).as_posix()):
                source = launcher.read_text(encoding="utf-8")
                export = source.find("export PYTHONDONTWRITEBYTECODE=1")
                first_python = source.find("python3")
                self.assertGreaterEqual(export, 0)
                if first_python >= 0:
                    self.assertLess(export, first_python)

    def test_normal_installed_commands_leave_the_product_tree_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            installed = temporary / "installed product"
            installed.mkdir()
            copy_product(installed)

            vault = temporary / "user vault"
            (vault / "wiki").mkdir(parents=True)
            (vault / ".raw").mkdir()
            (vault / "wiki" / "Page.md").write_text(
                "---\ntitle: Page\naddress: c-000001\n---\n\nA page about durable memory.\n",
                encoding="utf-8",
            )

            home = temporary / "home"
            home.mkdir()
            bash = _git_bash()
            environment = {k: v for k, v in os.environ.items() if k.lower() != "path"}
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.pop("PYTHONPYCACHEPREFIX", None)
            environment.pop("PYTHONPATH", None)
            environment["PATH"] = (
                f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"
            )
            environment["HOME"] = str(home)
            environment["CLAUDE_PLUGIN_ROOT"] = str(installed)
            environment["CLAUDE_EMPIRE_VAULT"] = str(vault)

            before = fingerprint(installed)

            commands: list[tuple[list[str], set[int]]] = []
            for launcher in sorted((installed / "scripts").glob("*.py")):
                commands.append(([sys.executable, str(launcher), "--help"], {0}))
            commands.extend(
                (
                    (
                        [
                            bash,
                            (installed / "scripts/setup-dragonscale.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--check",
                        ],
                        {0, 1},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/setup-mode.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--check",
                        ],
                        {0},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/setup-retrieve.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--check",
                        ],
                        {0},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/setup-vault.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--check",
                        ],
                        {0, 1},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/allocate-address.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--peek",
                        ],
                        {0},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/detect-transport.sh").as_posix(),
                            "--vault",
                            str(vault),
                            "--peek",
                            "--quiet",
                        ],
                        {0},
                    ),
                    (
                        [
                            bash,
                            (installed / "scripts/wiki-lock.sh").as_posix(),
                            "peek",
                            "wiki/Page.md",
                        ],
                        {0},
                    ),
                    (
                        [
                            sys.executable,
                            str(installed / "scripts/claude-empire.py"),
                            "steward",
                            "doctor",
                        ],
                        {0},
                    ),
                    (
                        [
                            sys.executable,
                            str(installed / "scripts/claude-empire.py"),
                            "hook",
                            "stop",
                        ],
                        {0},
                    ),
                    (
                        [
                            sys.executable,
                            str(installed / "scripts/contextual-prefix.py"),
                            "--vault",
                            str(vault),
                            "--all",
                            "--no-llm",
                        ],
                        {0},
                    ),
                    (
                        [
                            sys.executable,
                            str(installed / "scripts/bm25-index.py"),
                            "--vault",
                            str(vault),
                            "build",
                        ],
                        {0},
                    ),
                    (
                        [
                            sys.executable,
                            str(installed / "scripts/retrieve.py"),
                            "--vault",
                            str(vault),
                            "durable memory",
                            "--top",
                            "1",
                            "--no-rerank",
                        ],
                        {0},
                    ),
                )
            )

            for command, accepted in commands:
                with self.subTest(command=command):
                    completed = subprocess.run(
                        command,
                        cwd=installed,
                        env=environment,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=20,
                    )
                    self.assertIn(
                        completed.returncode,
                        accepted,
                        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
                    )

            after = fingerprint(installed)
            self.assertEqual(before, after)
            self.assertFalse(
                any(
                    path.name == "__pycache__"
                    for path in installed.rglob("__pycache__")
                )
            )
            self.assertFalse(any(installed.rglob("*.py[co]")))


if __name__ == "__main__":
    unittest.main()
