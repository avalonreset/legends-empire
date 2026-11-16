#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legends_vault_steward.cli import main

VALID_TEMPORAL_KEYS = {
    "job_id",
    "title",
    "queue",
    "interpreter",
    "script",
    "args",
    "cwd",
    "timeout_s",
    "wait_for_worker_s",
    "notify_channel",
    "cron",
    "crons",
    "interval",
    "interval_offset",
    "timezone",
    "jitter",
    "overlap",
    "catchup_window",
    "pause_on_failure",
    "remaining_actions",
    "paused",
    "note",
    "kind",
    "session_id",
    "spawn",
    "agent",
    "effort",
    "task_type",
    "project",
    "workspace",
    "message",
    "model",
    "code",
    "omnigent_ctl",
    "description",
}


class StewardDigestTests(unittest.TestCase):
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

    def test_digest_first_run_no_change_and_new_high_without_ratchet(self) -> None:
        self.write_vault_file("AGENTS.md", "# Agents\n@missing-1.md\n")
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        out_dir = self.vault / ".steward" / "digest"

        # 1. First run: baseline bootstrap -> exit 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc1, 0)
        d1 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        md1 = (out_dir / "digest.md").read_text(encoding="utf-8")
        self.assertEqual(d1["schema"], "legends.vault-steward.digest/v1")
        self.assertEqual(d1["status"], "baseline")
        self.assertTrue(d1["first_run"])
        self.assertIn("baseline", md1.lower())
        self.assertLessEqual(len(md1.encode("utf-8")), 1024)
        self.assertEqual(d1["high_keys"], ["entry.broken_import|AGENTS.md|missing-1.md"])

        # 2. Second run with no change -> exit 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc2 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc2, 0)
        d2 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "ok")
        self.assertEqual(d2["new_high_keys"], [])

        # 3. Third run with a new high in the same file -> exit 12
        self.write_vault_file("AGENTS.md", "# Agents\n@missing-1.md\n@missing-2.md\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc3 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc3, 12)
        d3 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d3["status"], "regression")
        self.assertEqual(d3["new_high_keys"], ["entry.broken_import|AGENTS.md|missing-2.md"])

        # 4. Fourth run (Day N+1) with the unfixed regression still present -> still exits 12 (no upward ratchet!)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc4 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc4, 12)

    def test_digest_md_hard_cap_1024_bytes_with_plus_n_more(self) -> None:
        imports = "\n".join(f"@missing/very-long-target-path-number-{i:03d}.md" for i in range(40))
        self.write_vault_file("AGENTS.md", f"# Agents\n{imports}\n")
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        out_dir = self.base / "digest-cap"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc, 0)
        md_bytes = (out_dir / "digest.md").read_bytes()
        self.assertLessEqual(len(md_bytes), 1024)
        md_text = md_bytes.decode("utf-8")
        self.assertRegex(md_text, r"\+\d+ more")

    def test_jobs_template_validates_as_json_with_expected_keys(self) -> None:
        tpl_path = ROOT / "templates" / "legends-temporal" / "steward-digest.jobs.json"
        self.assertTrue(tpl_path.is_file())
        raw_text = tpl_path.read_text(encoding="utf-8")
        self.assertNotIn("C:\\Users\\", raw_text)
        data = json.loads(raw_text)
        self.assertIn("jobs", data)
        self.assertIsInstance(data["jobs"], list)
        self.assertEqual(len(data["jobs"]), 1)
        job = data["jobs"][0]
        self.assertTrue(set(job.keys()).issubset(VALID_TEMPORAL_KEYS))
        self.assertEqual(job["job_id"], "empire-steward-digest")
        self.assertEqual(job["kind"], "script")
        self.assertNotEqual(job["kind"], "agent")
        self.assertEqual(job["cron"], "0 7 * * *")
        self.assertEqual(job["timezone"], "America/New_York")
        self.assertEqual(job["overlap"], "SKIP")
        self.assertEqual(job["script"], "-m")
        self.assertIn("legends-vault-stewardship", job["args"])
        self.assertIn("digest", job["args"])


if __name__ == "__main__":
    unittest.main()
