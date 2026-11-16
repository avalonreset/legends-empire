#!/usr/bin/env python3
"""Hermetic regression tests for v0.4.1 scripts fixes (F7/R7, F9/R9, F18/R14)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _find_git_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if os.path.isfile(candidate):
                return candidate
        found = shutil.which("bash")
        if found and "system32" not in found.lower() and "windowsapps" not in found.lower():
            return found
        return None
    return shutil.which("bash")


def _bash_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    py_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = py_dir + os.pathsep + env.get("PATH", "")
    if extra:
        env.update(extra)
    return env


class Scripts041FixesTests(unittest.TestCase):
    def test_f7_r7_utf8_stdio_under_cp1252_pipe_with_emoji_note(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            wiki = vault / "wiki" / "concepts"
            wiki.mkdir(parents=True)
            (vault / ".vault-meta").mkdir(parents=True)
            (vault / ".claude-empire.json").write_text("{}\n", encoding="utf-8")

            emoji_title = "Launch Rocket \U0001f680"
            (wiki / "Launch.md").write_text(
                f"---\n"
                f"title: \"{emoji_title}\"\n"
                f"address: c-000001\n"
                f"type: concept\n"
                f"status: evergreen\n"
                f"created: 2026-11-16\n"
                f"updated: 2026-11-16\n"
                f"tags:\n"
                f"  - rocket\n"
                f"---\n\n"
                f"# {emoji_title}\n\n"
                f"See [[Unresolved Target \U0001f680]] for details.\n",
                encoding="utf-8",
            )

            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "cp1252"

            # 1. contextual-prefix.py + bm25-index.py + retrieve.py
            r_cp = subprocess.run(
                [sys.executable, str(ROOT / "scripts/contextual-prefix.py"), "--vault", str(vault), "--no-llm"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_cp.returncode, 0, r_cp.stderr.decode("utf-8", errors="replace"))

            r_bm25 = subprocess.run(
                [sys.executable, str(ROOT / "scripts/bm25-index.py"), "--vault", str(vault), "build"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_bm25.returncode, 0, r_bm25.stderr.decode("utf-8", errors="replace"))

            r_ret = subprocess.run(
                [sys.executable, str(ROOT / "scripts/retrieve.py"), "--vault", str(vault), "Launch \U0001f680", "--no-rerank"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_ret.returncode, 0, r_ret.stderr.decode("utf-8", errors="replace"))
            ret_json = json.loads(r_ret.stdout.decode("utf-8"))
            self.assertEqual(ret_json["query"], "Launch \U0001f680")

            # 2. wiki-mode.py route with emoji
            r_wm = subprocess.run(
                [sys.executable, str(ROOT / "scripts/wiki-mode.py"), "--vault", str(vault), "route", "concept", "Cafe \U0001f680"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_wm.returncode, 0, r_wm.stderr.decode("utf-8", errors="replace"))
            self.assertIn("\U0001f680", r_wm.stdout.decode("utf-8"))

            # 3. boundary-score.py (both markdown and json)
            r_bs = subprocess.run(
                [sys.executable, str(ROOT / "scripts/boundary-score.py"), "--vault", str(vault), "--include-score-zero"],
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_bs.returncode, 0, r_bs.stderr.decode("utf-8", errors="replace"))
            self.assertIn("\U0001f680", r_bs.stdout.decode("utf-8"))

            # 4. python -m claude_empire.lint_engine
            r_le = subprocess.run(
                [sys.executable, "-m", "claude_empire.lint_engine", str(vault), "--format", "markdown", "--as-of", "2026-11-16"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(r_le.returncode, 0, r_le.stderr.decode("utf-8", errors="replace"))
            self.assertIn("\U0001f680", r_le.stdout.decode("utf-8"))

    def test_f18_r14_wiki_mode_hides_templates_in_help_but_keeps_handler(self) -> None:
        r_help = subprocess.run(
            [sys.executable, str(ROOT / "scripts/wiki-mode.py"), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        self.assertEqual(r_help.returncode, 0)
        self.assertNotIn("templates", r_help.stdout)
        self.assertIn("{get,set,config,route,id}", r_help.stdout)

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "wiki").mkdir()
            r_tmpl = subprocess.run(
                [sys.executable, str(ROOT / "scripts/wiki-mode.py"), "--vault", str(vault), "templates"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            self.assertEqual(r_tmpl.returncode, 2)
            self.assertIn("templates dir missing", r_tmpl.stderr)

    def test_f18_r14_read_hot_cache_supports_claude_empire_json_and_env(self) -> None:
        bash = _find_git_bash()
        if not bash:
            self.skipTest("Git Bash not available")

        script = (ROOT / "scripts" / "read-hot-cache.sh").as_posix()
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "wiki").mkdir()
            (vault / "wiki" / "hot.md").write_text("HOT_CACHE_BODY_12345", encoding="utf-8")

            # Unmarked directory prints nothing
            r_unmarked = subprocess.run(
                [bash, script],
                cwd=vault,
                env=_bash_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(r_unmarked.returncode, 0)
            self.assertEqual(r_unmarked.stdout, "")

            # Marked with .claude-empire.json prints hot.md and honors CLAUDE_EMPIRE_HOT_CACHE_MAX_BYTES
            (vault / ".claude-empire.json").write_text("{}\n", encoding="utf-8")
            r_marked = subprocess.run(
                [bash, script],
                cwd=vault,
                env=_bash_env({"CLAUDE_EMPIRE_HOT_CACHE_MAX_BYTES": "9"}),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(r_marked.returncode, 0)
            self.assertEqual(r_marked.stdout, "HOT_CACHE")

            # Legacy .vault-meta/codex-empire.vault fallback still works
            (vault / ".claude-empire.json").unlink()
            (vault / ".vault-meta").mkdir()
            (vault / ".vault-meta" / "codex-empire.vault").write_text("1\n", encoding="utf-8")
            r_legacy = subprocess.run(
                [bash, script],
                cwd=vault,
                env=_bash_env({"CODEX_EMPIRE_HOT_CACHE_MAX_BYTES": "14"}),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(r_legacy.returncode, 0)
            self.assertEqual(r_legacy.stdout, "HOT_CACHE_BODY")

    @unittest.skipUnless(os.name == "nt", "NTFS junctions require Windows (_winapi.CreateJunction)")
    def test_f9_r9_allocate_address_skips_ntfs_junction(self) -> None:
        import _winapi

        bash = _find_git_bash()
        if not bash:
            self.skipTest("Git Bash not available")

        script = (ROOT / "scripts" / "allocate-address.sh").as_posix()
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            (vault / "wiki").mkdir(parents=True)
            (vault / ".claude-empire.json").write_text("{}\n", encoding="utf-8")
            (vault / "wiki" / "local.md").write_text(
                "---\naddress: c-000005\n---\n# Local\n",
                encoding="utf-8",
            )

            outside = base / "outside"
            outside.mkdir()
            (outside / "escaped.md").write_text(
                "---\naddress: c-000999\n---\n# Outside\n",
                encoding="utf-8",
            )
            _winapi.CreateJunction(str(outside), str(vault / "wiki" / "junc"))

            res = subprocess.run(
                [bash, script, "--vault", vault.as_posix(), "--peek"],
                cwd=ROOT,
                env=_bash_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "6")


if __name__ == "__main__":
    unittest.main()
