#!/usr/bin/env python3
"""Subprocess acceptance of stewardship through the shared Empire entrypoint."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "claude-empire.py"


class StewardshipIntegrationTests(unittest.TestCase):
    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, str(CLI), *map(str, arguments)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=30, check=False,
        )

    def test_doctor_and_help_route_through_empire(self):
        doctor = self.invoke("steward", "doctor")
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn("doctor OK", doctor.stdout)
        help_result = self.invoke("steward", "scan", "--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--baseline", help_result.stdout)
        self.assertIn("--review-limit", help_result.stdout)
        root_help = self.invoke("--help")
        self.assertIn("steward", root_help.stdout)

    def test_scan_ordinary_vault_preserves_notes_and_supports_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            vault = base / "vault"
            vault.mkdir()
            note = vault / "Home.md"
            note.write_text("# Home\n\n[[Missing]]\n", encoding="utf-8")
            before_hash = hashlib.sha256(note.read_bytes()).hexdigest()
            first = self.invoke("steward", "scan", vault, "--review-limit", "5",
                                "--out", base / "before", "--format", "both")
            self.assertEqual(first.returncode, 0, first.stderr)
            report_path = base / "before" / "observe.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["inventory"]["markdown_notes"], 1)
            self.assertEqual(report["graph"]["unresolved_targets"], 1)
            self.assertTrue((base / "before" / "observe.md").is_file())
            second = self.invoke("steward", "scan", vault, "--baseline", report_path,
                                 "--review-limit", "5", "--format", "json")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["inventory"]["markdown_notes"], 1)
            self.assertEqual(hashlib.sha256(note.read_bytes()).hexdigest(), before_hash)
            self.assertEqual([p.name for p in vault.iterdir()], ["Home.md"])

    def test_refuses_mutation_and_in_vault_report_output(self):
        denied = self.invoke("steward", "repair")
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("invalid choice", denied.stderr)
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            denied = self.invoke("steward", "scan", vault, "--out", vault)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(list(vault.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
