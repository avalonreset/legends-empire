#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legends_vault_steward.cli import main
from legends_vault_steward.config import load_config
from legends_vault_steward.entry_audit import entry_audit

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class StewardEntryAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()
        self.fake_home = self.base / "fake-home"
        self.fake_home.mkdir()

    def write_vault_file(self, rel: str, content: str | bytes) -> Path:
        target = self.vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def write_config(self, data: dict) -> Path:
        cfg_path = self.base / "overlay.json"
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return cfg_path

    def test_import_cycle_and_depth_cap(self) -> None:
        # Cycle: A.md -> B.md -> A.md
        self.write_vault_file("A.md", "# A\n" "@B.md\n")
        self.write_vault_file("B.md", "# B\n" "@A.md\n" "@C0.md\n")
        # Depth chain: C0 -> C1 -> ... -> C9 (exceeds depth cap 8)
        for idx in range(10):
            nxt = f"@C{idx + 1}.md\n" if idx < 9 else ""
            self.write_vault_file(f"C{idx}.md", f"# C{idx}\n{nxt}")

        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "cycle-depth", "entry": ["A.md"], "core": "C6.md"}
                ]
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        h = report["harnesses"][0]
        self.assertTrue(h["cycle_detected"])
        self.assertTrue(h["depth_cap_reached"])
        self.assertEqual(h["max_depth"], 8)
        self.assertTrue(h["core_reached"])
        chain_paths = [c["path"] for c in h["chain"]]
        self.assertIn("C6.md", chain_paths)
        # C6 is at depth 8 (A=0, B=1, C0=2..C6=8); C7 at depth 9 is not loaded
        self.assertNotIn("C7.md", chain_paths)

    def test_broken_import_with_file_line_and_fence_handle_ignoring(self) -> None:
        self.write_vault_file(
            "CLAUDE.md",
            "---\ntitle: Claude Entry\n---\n"
            "Contact @avalonreset in prose.\n"
            "```python\n"
            "@decorator.route('/ignore')\n"
            "@missing/in-fence.md\n"
            "```\n"
            "@missing/real-broken.md\n",
        )
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "claude", "entry": ["CLAUDE.md"]}]}
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        broken = [f for f in report["findings"] if f["code"] == "entry.broken_import"]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["severity"], "high")
        self.assertEqual(broken[0]["path"], "CLAUDE.md")
        self.assertEqual(broken[0]["line"], 9)
        self.assertEqual(broken[0]["file_line"], "CLAUDE.md:9")
        self.assertIn("CLAUDE.md:9", broken[0]["message"])

    def test_parity_mismatch_and_core_unreached(self) -> None:
        self.write_vault_file("core-a.md", "# Core Version A\n")
        self.write_vault_file("core-b.md", "# Core Version B (diverged)\n")
        self.write_vault_file("CLAUDE.md", "@core-a.md\n")
        self.write_vault_file("GEMINI.md", "@core-b.md\n")
        self.write_vault_file("AGENTS.md", "# Agents without import\n")

        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "claude", "entry": ["CLAUDE.md"], "core": "core-a.md"},
                    {"id": "gemini", "entry": ["GEMINI.md"], "core": "core-b.md"},
                    {"id": "agents", "entry": ["AGENTS.md"], "core": "core-a.md"},
                ]
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        self.assertFalse(report["parity"]["ok"])
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("entry.core_mismatch", codes)
        self.assertIn("entry.core_unreached", codes)

    def test_forbidden_path_and_home_redaction_with_isolated_home(self) -> None:
        # O5: isolate both USERPROFILE and HOME
        env_patch = {"USERPROFILE": str(self.fake_home), "HOME": str(self.fake_home)}
        with patch.dict(os.environ, env_patch):
            claude_dir = self.fake_home / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            (claude_dir / "CLAUDE.md").write_text(f"@{self.vault.as_posix()}/AGENTS.md\n", encoding="utf-8")
            forb_dir = claude_dir / "projects" / "memory"
            forb_dir.mkdir(parents=True, exist_ok=True)
            (forb_dir / "secret.md").write_text("do not read me", encoding="utf-8")

            self.write_vault_file("AGENTS.md", "# Core Agents\n")
            cfg = self.write_config(
                {
                    "entry_harnesses": [
                        {
                            "id": "claude",
                            "entry": ["~/.claude/CLAUDE.md"],
                            "core": "AGENTS.md",
                        }
                    ],
                    "forbidden_paths": ["~/.claude/projects/memory"],
                }
            )
            report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
            forb_findings = [f for f in report["findings"] if f["code"] == "entry.forbidden_present"]
            self.assertEqual(len(forb_findings), 1)
            self.assertEqual(forb_findings[0]["severity"], "high")
            self.assertEqual(forb_findings[0]["path"], "~/.claude/projects/memory")

            # Ensure expanded fake_home path never appears anywhere in serialized JSON
            serialized = json.dumps(report)
            self.assertNotIn(str(self.fake_home), serialized)
            self.assertNotIn(self.fake_home.as_posix(), serialized)
            self.assertEqual(report["harnesses"][0]["chain"][0]["path"], "~/.claude/CLAUDE.md")

            # Also verify via subprocess with USERPROFILE and HOME set (O5)
            env = {**os.environ, **env_patch}
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "claude-empire.py"),
                    "steward",
                    "entry-audit",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--format",
                    "json",
                    "--check",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            self.assertEqual(proc.returncode, 12, proc.stderr)
            self.assertNotIn(str(self.fake_home), proc.stdout)
            self.assertNotIn( self.fake_home.as_posix(), proc.stdout)

    def test_encoding_with_byte_offset_utf8_and_c0_control(self) -> None:
        self.write_vault_file("BadUtf8.md", b"# Header\nValid\n\xff\xfeInvalid")
        self.write_vault_file("BadControl.md", b"# Header\nNull\x00Byte\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "utf8", "entry": ["BadUtf8.md"]},
                    {"id": "ctrl", "entry": ["BadControl.md"]},
                ]
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        enc = {f["path"]: f for f in report["findings"] if f["code"] == "entry.encoding"}
        self.assertIn("BadUtf8.md", enc)
        self.assertEqual(enc["BadUtf8.md"]["severity"], "high")
        self.assertEqual(enc["BadUtf8.md"]["byte_offset"], 15)
        self.assertIn("15", enc["BadUtf8.md"]["message"])

        self.assertIn("BadControl.md", enc)
        self.assertEqual(enc["BadControl.md"]["severity"], "high")
        self.assertEqual(enc["BadControl.md"]["byte_offset"], 13)
        self.assertIn("13", enc["BadControl.md"]["message"])

    def test_over_budget_chain_and_over_budget_shim_and_expired_fresh_until(self) -> None:
        self.write_vault_file(
            "CLAUDE.md",
            "---\nfresh_until: 2026-10-01\n---\n# Shim\n" "@AGENTS.md\n" + ("shim padding " * 20) + "\n",
        )
        self.write_vault_file("AGENTS.md", "# Core\n" + ("core content " * 40) + "\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {
                        "id": "claude",
                        "entry": ["CLAUDE.md"],
                        "core": "AGENTS.md",
                        "max_chain_bytes": 200,
                        "shim": {"path": "CLAUDE.md", "max_bytes": 100},
                    }
                ]
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        by_code = {f["code"]: f for f in report["findings"]}
        self.assertIn("budget.entry_chain_exceeded", by_code)
        self.assertEqual(by_code["budget.entry_chain_exceeded"]["severity"], "high")
        self.assertIn("entry.shim_oversize", by_code)
        self.assertEqual(by_code["entry.shim_oversize"]["severity"], "medium")
        self.assertIn("freshness.expired_fresh_until", by_code)
        self.assertEqual(by_code["freshness.expired_fresh_until"]["severity"], "high")

    def test_containment_rejects_escape_unc_and_env_imports(self) -> None:
        outside_file = self.base / "outside.md"
        outside_file.write_text("outside secret", encoding="utf-8")
        self.write_vault_file("secrets.env", "SECRET=123\n")
        self.write_vault_file(
            "AGENTS.md",
            "@../outside.md\n" "@\\\\server\\share\\file.md\n" "@secrets.env\n",
        )
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        broken = [f for f in report["findings"] if f["code"] == "entry.broken_import"]
        self.assertEqual(len(broken), 3)
        lines = sorted(f["line"] for f in broken)
        self.assertEqual(lines, [1, 2, 3])

    def test_multivendor_overlay_example_is_valid(self) -> None:
        example_path = ROOT / "examples" / "steward-overlay.multivendor.json"
        self.assertTrue(example_path.is_file())
        cfg = load_config(example_path, profile="empire")
        harness_ids = [h["id"] for h in cfg["entry_harnesses"]]
        self.assertEqual(harness_ids, ["claude", "gemini", "codex-antigravity"])
        self.assertEqual(cfg["conflict_keys"][0]["id"], "context_compaction_threshold")


if __name__ == "__main__":
    unittest.main()
