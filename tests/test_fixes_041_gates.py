#!/usr/bin/env python3
"""Hermetic regression tests for v0.4.1 release gates CLI exit code (F16/R13)."""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import cli


class Gates041FixesTests(unittest.TestCase):
    def test_f16_r13_execute_selected_gate_exits_zero_when_passed(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([
                "release",
                "gates",
                "--execute",
                "--gate",
                "deterministic-sample",
            ])
        self.assertEqual(rc, 0, buf.getvalue())
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        # R13 constraint: ready remains False and manual gates remain in pending
        self.assertFalse(payload["ready"])
        self.assertIn("owner-promotion-approval", payload["pending"])
        by_id = {g["id"]: g for g in payload["gates"]}
        self.assertEqual(by_id["deterministic-sample"]["status"], "passed")

    def test_f16_r13_execute_manual_or_failed_selected_gate_exits_nonzero(self) -> None:
        # Selecting a manual-review gate with --execute must still exit 1
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([
                "release",
                "gates",
                "--execute",
                "--gate",
                "owner-promotion-approval",
            ])
        self.assertEqual(rc, 1)

        # Selecting a failing command gate exits 1
        fake_report = {
            "schema": "claude-empire.release-gates.v1",
            "ok": False,
            "ready": False,
            "executed": True,
            "scope": "selected",
            "selected": ["package-validation"],
            "summary": {"passed": 0, "failed": 1, "pending": 5},
            "failures": ["package-validation"],
            "pending": ["owner-promotion-approval"],
            "errors": [],
            "gates": [
                {
                    "id": "package-validation",
                    "required": True,
                    "type": "command",
                    "status": "failed",
                    "exit_code": 1,
                }
            ],
        }
        with mock.patch("claude_empire.cli.evaluate_release_gates", return_value=fake_report):
            with redirect_stdout(io.StringIO()):
                rc_fail = cli.main([
                    "release",
                    "gates",
                    "--execute",
                    "--gate",
                    "package-validation",
                ])
        self.assertEqual(rc_fail, 1)


if __name__ == "__main__":
    unittest.main()
