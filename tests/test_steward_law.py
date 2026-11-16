#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legends_vault_steward.cli import main
from legends_vault_steward.config import load_config
from legends_vault_steward.entry_audit import entry_audit

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class StewardLawTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()

    def write_vault_file(self, rel: str, content: str) -> Path:
        target = self.vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_config(self, data: dict) -> Path:
        cfg_path = self.base / "overlay.json"
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return cfg_path

    def test_law_expired_directive_severity_and_word_boundaries(self) -> None:
        # In entry chain: high severity; false-positive words like backends/extends/breakthrough ignored
        self.write_vault_file(
            "AGENTS.md",
            "# Operating Law\n"
            "Temporary routing through **2026-10-20** applies here.\n"
            "Our backends 2020-01-01 and breakthrough 2020-01-01 and extends 2020-01-01 are fine.\n"
            "Invalid date until 9999-99-99 is ignored.\n"
            "Future policy through 2027-01-01 is still active.\n"
            "```markdown\n"
            "Fenced example expires 2020-01-01 is ignored.\n"
            "```\n",
        )
        # In law_paths (not in entry chain): medium severity
        self.write_vault_file(
            "docs/policy/routing.md",
            "# Routing Policy\nThis override expires 2026-11-01.\n",
        )
        cfg = self.write_config(
            {
                "entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}],
                "law_paths": ["docs/policy/*.md"],
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        expired = [f for f in report["findings"] if f["code"] == "law.expired_directive"]
        self.assertEqual(len(expired), 2)
        by_path = {f["path"]: f for f in expired}
        self.assertEqual(by_path["AGENTS.md"]["severity"], "high")
        self.assertEqual(by_path["AGENTS.md"]["line"], 2)
        self.assertEqual(by_path["AGENTS.md"]["file_line"], "AGENTS.md:2")
        self.assertEqual(by_path["docs/policy/routing.md"]["severity"], "medium")
        self.assertEqual(by_path["docs/policy/routing.md"]["line"], 2)

    def test_law_duplicate_block_across_files(self) -> None:
        shared_para = (
            "Every module release or major code change must pass a read-only Carbon High "
            "pre-audit and post-diff verification gate before release. Never bypass the "
            "verification gate or push unreviewed changes to any public or internal branch."
        )
        self.assertGreaterEqual(len(shared_para), 200)
        self.write_vault_file("CLAUDE.md", f"# Claude\n\n{shared_para}\n\nShort unique line.\n")
        self.write_vault_file("GEMINI.md", f"# Gemini\n\n{shared_para}\n\nAnother short line.\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "claude", "entry": ["CLAUDE.md"]},
                    {"id": "gemini", "entry": ["GEMINI.md"]},
                ]
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        dups = [f for f in report["findings"] if f["code"] == "law.duplicate_block"]
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["severity"], "low")
        self.assertEqual(dups[0]["locations"], ["CLAUDE.md:3", "GEMINI.md:3"])

    def test_law_value_conflict_and_invalid_pattern_rejection(self) -> None:
        self.write_vault_file("AGENTS.md", "# Agents\nAuto-compaction threshold is pinned at 160K tokens.\n")
        self.write_vault_file("CLAUDE.md", "# Claude\nAuto-compaction threshold is pinned at 180K tokens.\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "agents", "entry": ["AGENTS.md"]},
                    {"id": "claude", "entry": ["CLAUDE.md"]},
                ],
                "conflict_keys": [
                    {
                        "id": "compaction_threshold",
                        "pattern": r"(?i)compaction[^\n]{0,40}?(\b\d{2,3}K\b)",
                    }
                ],
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        conflicts = [f for f in report["findings"] if f["code"] == "law.value_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["severity"], "medium")
        self.assertEqual(
            conflicts[0]["values"],
            {"160K": ["AGENTS.md:2"], "180K": ["CLAUDE.md:2"]},
        )

        # Reject nested quantifier (ReDoS pattern), alternation-in-repeat, and backrefs with ValueError / exit 11 (P2)
        for bad_pattern in (r"((a+)+)", r"(?:.+:)+(\d+)", r"(?:a|a)+(\d+)", r"(a)\1"):
            bad_redos = self.write_config(
                {"conflict_keys": [{"id": "bad", "pattern": bad_pattern}]}
            )
            with self.assertRaises(ValueError, msg=f"expected {bad_pattern!r} to raise ValueError"):
                load_config(bad_redos)
            rc = main(["entry-audit", str(self.vault), "--config", str(bad_redos)])
            self.assertEqual(rc, 11, f"expected {bad_pattern!r} to exit 11")

        bad_groups = self.write_config(
            {"conflict_keys": [{"id": "no_groups", "pattern": r"compaction \d+K"}]}
        )
        with self.assertRaises(ValueError):
            load_config(bad_groups)

        # Validate duplicate_block_entry_only must be bool (Recommended 2)
        bad_dup_bool = self.write_config({"duplicate_block_entry_only": "yes"})
        with self.assertRaises(ValueError):
            load_config(bad_dup_bool)
        self.assertEqual(main(["entry-audit", str(self.vault), "--config", str(bad_dup_bool)]), 11)

    def test_rec4_looks_like_secret_redacted_from_value_conflict(self) -> None:
        # Secret-like tokens must be skipped by _looks_like_secret in law.value_conflict
        tok_sk = "sk_" + "live_test1"
        tok_rk = "rk_" + "live_test2"
        tok_gh = "gh" + "p_test3"
        tok_jwt = "eyJ" + ("a" * 12)
        self.write_vault_file(
            "AGENTS.md",
            "# Agents\n"
            f"token: {tok_sk}\n"
            f"token: {tok_gh}\n"
            "token: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6\n"
            "mode: strict\n",
        )
        self.write_vault_file(
            "CLAUDE.md",
            "# Claude\n"
            f"token: {tok_rk}\n"
            f"token: {tok_jwt}\n"
            "token: " + ("x" * 65) + "\n"
            "mode: relaxed\n",
        )
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "agents", "entry": ["AGENTS.md"]},
                    {"id": "claude", "entry": ["CLAUDE.md"]},
                ],
                "conflict_keys": [
                    {"id": "api_token", "pattern": r"token:\s*(\S+)"},
                    {"id": "operating_mode", "pattern": r"mode:\s*(\S+)"},
                ],
            }
        )
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        conflicts = [f for f in report["findings"] if f["code"] == "law.value_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["key"], "operating_mode")
        serialized = json.dumps(report)
        self.assertNotIn(tok_sk, serialized)
        self.assertNotIn(tok_rk, serialized)
        self.assertNotIn(tok_gh, serialized)
        self.assertNotIn(tok_jwt, serialized)

    def test_redos_style_long_line_finishes_fast(self) -> None:
        # 120,000-char line with pathological repetitions
        long_line = ("through " * 10_000) + ("compaction " * 5_000) + "end\n"
        self.write_vault_file("AGENTS.md", "# Header\n" + long_line + "Expires 2026-01-01.\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}],
                "conflict_keys": [
                    {
                        "id": "compaction_threshold",
                        "pattern": r"(?i)compaction[^\n]{0,40}?(\b\d{2,3}K\b)",
                    }
                ],
            }
        )
        t0 = time.perf_counter()
        report = entry_audit(str(self.vault), config_path=str(cfg), now=NOW)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 0.5, f"ReDoS long-line check took {elapsed:.3f}s")
        expired = [f for f in report["findings"] if f["code"] == "law.expired_directive"]
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0]["line"], 3)


if __name__ == "__main__":
    unittest.main()
