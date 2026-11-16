#!/usr/bin/env python3
"""Hermetic regression tests for v0.4.1 lint, reparse/junction, and knowledge pack fixes (F1/R4, F8/F24/R8, F9/R9)."""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import cli
from claude_empire import lint_engine


def _write_valid_note(path: Path, title: str = "Valid Note") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n"
        f"title: {title}\n"
        f"type: concept\n"
        f"status: evergreen\n"
        f"created: 2026-11-16\n"
        f"updated: 2026-11-16\n"
        f"tags:\n"
        f"  - test\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"Body content.\n",
        encoding="utf-8",
    )


class Lint041FixesTests(unittest.TestCase):
    def test_oserror_per_entry_recorded_as_unreadable_path_and_exclude_prunes_before_stat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            _write_valid_note(vault / "wiki" / "index.md", "Index")
            bad_dir = vault / "${APPDATA}" / "\uf05cnpm" / "bin"
            bad_dir.mkdir(parents=True)
            bad_file = bad_dir / "grok"
            bad_file.write_text("stub", encoding="utf-8")

            real_lstat = os.lstat
            real_stat = os.stat
            stat_calls_on_bad: list[str] = []

            def FaultyLstat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if p_str.endswith("grok"):
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_lstat(target, *args, **kwargs)

            def FaultyStat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if p_str.endswith("grok") or "\uf05cnpm" in p_str:
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_stat(target, *args, **kwargs)

            with mock.patch("claude_empire.lint_engine.os.lstat", side_effect=FaultyLstat), mock.patch(
                "claude_empire.lint_engine.os.stat", side_effect=FaultyStat
            ):
                report = lint_engine.lint_vault(vault, as_of="2026-11-16")

            self.assertEqual(report["summary"]["pages_scanned"], 1)
            self.assertEqual(len(report["read_errors"]), 1)
            err = report["read_errors"][0]
            self.assertEqual(err["path"], "${APPDATA}/\uf05cnpm/bin/grok")
            self.assertEqual(err["code"], "lint.unreadable_path")
            self.assertIn("The file cannot be accessed by the system", err["message"])
            if os.name == "nt":
                self.assertIn("WinError 1920", err["message"])

            # Now verify --exclude prunes BEFORE lstat/stat is called
            stat_calls_on_bad.clear()

            def StrictFaultyLstat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if "\uf05cnpm" in p_str or p_str.endswith("grok"):
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_lstat(target, *args, **kwargs)

            with mock.patch("claude_empire.lint_engine.os.lstat", side_effect=StrictFaultyLstat), mock.patch(
                "claude_empire.lint_engine.os.stat", side_effect=FaultyStat
            ):
                report_ex = lint_engine.lint_vault(
                    vault, as_of="2026-11-16", exclude=["${APPDATA}/*"]
                )

            self.assertEqual(stat_calls_on_bad, [])
            self.assertEqual(report_ex["read_errors"], [])
            self.assertGreaterEqual(report_ex["summary"]["excluded_paths"], 1)
            self.assertEqual(report_ex["summary"]["issues_found"], 0)

    def test_knowledge_pack_attach_passes_lint_strict_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            pack_manifest = ROOT / "knowledge" / "stewardship" / "pack.json"

            # 1. Preview and apply init
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "init",
                    str(vault),
                    "--operation-id",
                    "init-pack-test",
                    "--generated-at",
                    "2026-11-16T12:00:00Z",
                ])
            self.assertEqual(rc, 0)
            init_plan = json.loads(buf.getvalue())
            with redirect_stdout(io.StringIO()):
                rc = cli.main([
                    "init",
                    str(vault),
                    "--operation-id",
                    "init-pack-test",
                    "--generated-at",
                    "2026-11-16T12:00:00Z",
                    "--apply",
                    "--approved-plan-sha256",
                    init_plan["approved_plan_sha256"],
                ])
            self.assertEqual(rc, 0)

            # Initialize git repo with baseline commit after init
            subprocess.run(["git", "init"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial vault"], cwd=vault, check=True, capture_output=True)

            # 2. Preview and apply knowledge attach
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "knowledge",
                    "attach",
                    str(pack_manifest),
                    "--vault",
                    str(vault),
                    "--operation-id",
                    "attach-stewardship",
                    "--generated-at",
                    "2026-11-16T12:05:00Z",
                ])
            self.assertEqual(rc, 0)
            attach_plan = json.loads(buf.getvalue())
            with redirect_stdout(io.StringIO()):
                rc = cli.main([
                    "knowledge",
                    "attach",
                    str(pack_manifest),
                    "--vault",
                    str(vault),
                    "--operation-id",
                    "attach-stewardship",
                    "--generated-at",
                    "2026-11-16T12:05:00Z",
                    "--apply",
                    "--approved-plan-sha256",
                    attach_plan["approved_plan_sha256"],
                ])
            self.assertEqual(rc, 0)

            # Check version 0.2.2 and F24 content
            state_doc = json.loads(
                (vault / "wiki/library/legends-empire/stewardship/pack-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state_doc["version"], "0.2.2")
            gen_index = (vault / "wiki/library/legends-empire/stewardship/index.md").read_text(encoding="utf-8")
            self.assertIn("python -m cto_legends --home <home> handoff legends-empire", gen_index)
            ev_text = (vault / "wiki/library/legends-empire/stewardship/reference/evidence.md").read_text(encoding="utf-8")
            self.assertNotIn("Native Windows remains read-only", ev_text)
            self.assertIn("native Windows NTFS", ev_text)

            # 3. lint --strict must exit 0 with 0 issues
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "lint",
                    "--vault",
                    str(vault),
                    "--as-of",
                    "2026-11-16",
                    "--strict",
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            lint_rep = json.loads(buf.getvalue())
            self.assertEqual(lint_rep["summary"]["issues_found"], 0, lint_rep)
            self.assertEqual(lint_rep["duplicate_basenames"], [])
            self.assertEqual(lint_rep["missing_frontmatter"], [])

            # 4. Run checkpoint on the attached knowledge pack operation
            buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err_buf):
                rc = cli.main([
                    "checkpoint",
                    "attach-stewardship",
                    "--vault",
                    str(vault),
                    "--as-of",
                    "2026-11-16",
                ])
            self.assertEqual(rc, 0, f"stdout={buf.getvalue()} stderr={err_buf.getvalue()}")

    @unittest.skipUnless(os.name == "nt", "NTFS junctions require Windows (_winapi.CreateJunction)")
    def test_ntfs_junction_skipped_in_lint_walk_and_wiki_location(self) -> None:
        import _winapi

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            _write_valid_note(vault / "wiki" / "index.md", "Index")

            outside = base / "outside-target"
            outside.mkdir(parents=True)
            (outside / "outside-secret.md").write_text("# Outside Secret\nNo frontmatter.\n", encoding="utf-8")

            junc_dir = vault / "wiki" / "junc-out"
            _winapi.CreateJunction(str(outside), str(junc_dir))

            report = lint_engine.lint_vault(vault, as_of="2026-11-16")
            self.assertEqual(report["summary"]["pages_scanned"], 1)
            self.assertEqual(report["missing_frontmatter"], [])
            self.assertEqual(report["orphans"], [])


if __name__ == "__main__":
    unittest.main()
