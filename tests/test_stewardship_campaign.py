#!/usr/bin/env python3
"""A synthetic stewardship campaign through the existing transaction engine."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire.transaction import TransactionError, apply_bundle, inspect_bundle


@unittest.skipUnless(os.name == "posix", "Empire transaction writes require POSIX")
class StewardshipCampaignTests(unittest.TestCase):
    def scan(self, vault):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/claude-empire.py"), "steward",
             "scan", str(vault), "--review-limit", "5", "--format", "json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def fixture(self, root):
        vault = root / "synthetic-vault"
        for folder in (".obsidian", ".raw", "wiki", "wiki/projects", "wiki/sessions", "inbox"):
            (vault / folder).mkdir(parents=True, exist_ok=True)
        files = {
            "wiki/index.md": "# Map\n[[wiki/projects/Launch-Old]]\n[[wiki/sessions/Launch]]\n",
            "wiki/projects/Launch.md": "---\ntype: project\nstatus: active\nupdated: 2000-01-01\n---\n# Launch\n[[wiki/Evidence]]\n",
            "wiki/sessions/Launch.md": "---\ntype: session\nstatus: active\nupdated: 2000-01-01\n---\n# Handoff\n[[wiki/Evidence]]\n",
            "wiki/Evidence.md": "# Synthetic evidence\nOwner: Alex. Objective: publish the fixture guide.\nNext action: review the fixture guide draft. Review conducted today.\n",
            "inbox/Receipt-Keep.md": "# Receipt\nHistorical evidence remains unchanged.\n",
        }
        for path, content in files.items():
            (vault / path).write_text(content, encoding="utf-8")
        return vault

    def campaign(self, vault, before):
        today = datetime.now(timezone.utc).date().isoformat()
        writes = {
            "wiki/index.md": "# Map\n[[wiki/projects/Launch]]\n[[wiki/sessions/Launch]]\n",
            "wiki/projects/Launch.md": f"---\ntype: project\nstatus: active\nowner: Alex\nnext_action: review the fixture guide draft\nupdated: {today}\n---\n# Launch\n[[wiki/Evidence]]\n",
            "wiki/sessions/Launch.md": f"---\ntype: session\nstatus: active\nowner: Alex\nobjective: publish the fixture guide\nnext_action: review the fixture guide draft\nevidence: wiki/Evidence.md\nupdated: {today}\n---\n# Handoff\n[[wiki/Evidence]]\n",
        }
        observed = {item["path"]: item["sha256"] for item in before["notes"]}
        return {
            "schema": "claude-empire.transaction.v1",
            "operation_id": "synthetic-stewardship-campaign",
            "operation_type": "generic",
            "expected_hashes": {path: observed[path] for path in writes},
            "writes": [{"path": path, "mode": "replace", "content": content}
                       for path, content in writes.items()],
        }

    def test_exact_campaign_repairs_findings_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = self.fixture(Path(directory))
            receipt = (vault / "inbox/Receipt-Keep.md").read_bytes()
            before = self.scan(vault)
            codes = {item["code"] for item in before["findings"]}
            self.assertTrue({"link.unresolved", "operations.review_due", "operations.missing_fields"} <= codes)
            operation = self.campaign(vault, before)
            reviewed = inspect_bundle(vault, operation)
            result = apply_bundle(vault, operation, approved_plan_sha256=reviewed["approval_sha256"])
            self.assertEqual(set(result["changed_paths"]), set(operation["expected_hashes"]))
            after = self.scan(vault)
            relevant = {"link.unresolved", "operations.review_due", "operations.missing_fields"}
            self.assertFalse(relevant & {item["code"] for item in after["findings"]})
            self.assertEqual((vault / "inbox/Receipt-Keep.md").read_bytes(), receipt)
            for path, old_hash in before["snapshot"].items():
                if path not in operation["expected_hashes"]:
                    self.assertEqual(after["snapshot"][path], old_hash)

    def test_reviewed_campaign_rejects_changed_before_state(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = self.fixture(Path(directory))
            before = self.scan(vault)
            operation = self.campaign(vault, before)
            reviewed = inspect_bundle(vault, operation)
            target = vault / "wiki/index.md"
            target.write_text("# Human edit after review\n", encoding="utf-8")
            retained = {path: hashlib.sha256((vault / path).read_bytes()).hexdigest()
                        for path in before["snapshot"]}
            with self.assertRaises(TransactionError):
                apply_bundle(vault, operation, approved_plan_sha256=reviewed["approval_sha256"])
            for path, digest in retained.items():
                self.assertEqual(hashlib.sha256((vault / path).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
