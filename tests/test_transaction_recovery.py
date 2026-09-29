#!/usr/bin/env python3
"""Historical v1 transaction compatibility, using synthetic vaults only."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire.transaction import (
    BUNDLE_SCHEMA, JOURNAL_SCHEMA, RESULT_SCHEMA, TransactionError,
    apply_bundle, recover_incomplete, sha256_bytes,
)

LEGACY_JOURNAL = "claude-obsidian.transaction-journal.v1"
LEGACY_RESULT = "claude-obsidian.transaction-result.v1"


@unittest.skipIf(os.name == "nt", "transaction recovery requires POSIX/WSL")
class HistoricalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.vault = Path(self.temp.name).resolve() / "vault"
        (self.vault / "wiki").mkdir(parents=True)
        (self.vault / ".raw").mkdir()
        self.note = self.vault / "wiki/Example.md"
        self.note.write_bytes(b"original\n")
        self.operation = {
            "schema": BUNDLE_SCHEMA, "operation_id": "historical-operation",
            "operation_type": "generic",
            "expected_hashes": {"wiki/Example.md": sha256_bytes(b"original\n")},
            "writes": [{"path": "wiki/Example.md", "mode": "replace", "content": "updated\n"}],
        }
        self.transaction = self.vault / ".vault-meta/transactions/historical-operation"
        self.journal_path = self.transaction / "journal.json"
        self.result_path = self.transaction / "changed-paths.json"

    def legacy(self, *, rolled_back=False):
        if rolled_back:
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                apply_bundle(self.vault, self.operation, fail_after=1)
        else:
            apply_bundle(self.vault, self.operation)
        journal = json.loads(self.journal_path.read_text())
        journal["schema"] = LEGACY_JOURNAL
        self.write(self.journal_path, journal)
        if self.result_path.exists():
            result = json.loads(self.result_path.read_text())
            result["schema"] = LEGACY_RESULT
            self.write(self.result_path, result)
        return journal

    def write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def snapshot(self):
        return {str(p.relative_to(self.vault)): p.read_bytes() for p in self.vault.rglob("*")
                if p.is_file() and p.name != "mutation.lock"}

    def test_completed_legacy_history_unchanged_and_new_operation_current(self):
        self.legacy()
        original_journal = self.journal_path.read_bytes()
        original_result = self.result_path.read_bytes()
        # Completed history remains valid after later legitimate owner edits.
        self.note.write_bytes(b"newer owner work\n")
        self.assertEqual([], recover_incomplete(self.vault))
        self.assertEqual(original_journal, self.journal_path.read_bytes())
        self.assertEqual(original_result, self.result_path.read_bytes())
        self.assertEqual(b"newer owner work\n", self.note.read_bytes())
        newer = {"schema": BUNDLE_SCHEMA, "operation_id": "new-attachment", "operation_type": "generic",
                 "expected_hashes": {"wiki/Shelf.md": None},
                 "writes": [{"path": "wiki/Shelf.md", "mode": "create", "content": "# Shelf\n"}]}
        result = apply_bundle(self.vault, newer)
        self.assertEqual(RESULT_SCHEMA, result["schema"])
        latest = json.loads((self.vault / ".vault-meta/transactions/new-attachment/journal.json").read_text())
        self.assertEqual(JOURNAL_SCHEMA, latest["schema"])
        self.assertEqual(original_journal, self.journal_path.read_bytes())

    def test_rolled_back_legacy_history_preserved(self):
        self.legacy(rolled_back=True)
        self.note.write_bytes(b"subsequent owner edit\n")
        before = self.snapshot()
        self.assertEqual([], recover_incomplete(self.vault))
        self.assertEqual(before, self.snapshot())

    def test_valid_incomplete_legacy_rollback_uses_existing_checks(self):
        journal = self.legacy()
        self.result_path.unlink()
        journal["state"] = "applying"
        self.write(self.journal_path, journal)
        self.assertEqual(["historical-operation"], recover_incomplete(self.vault))
        self.assertEqual(b"original\n", self.note.read_bytes())
        restored = json.loads(self.journal_path.read_text())
        self.assertEqual(LEGACY_JOURNAL, restored["schema"])
        self.assertEqual("rolled-back", restored["state"])

    def test_legacy_result_finalizes_interrupted_journal(self):
        journal = self.legacy()
        journal["state"] = "applying"
        self.write(self.journal_path, journal)
        result_before = self.result_path.read_bytes()
        self.assertEqual(["historical-operation"], recover_incomplete(self.vault))
        self.assertEqual("complete", json.loads(self.journal_path.read_text())["state"])
        self.assertEqual(result_before, self.result_path.read_bytes())
        self.assertEqual(b"updated\n", self.note.read_bytes())

    def test_missing_legacy_result_reconstructed_in_legacy_family(self):
        self.legacy()
        self.result_path.unlink()
        journal_before = self.journal_path.read_bytes()
        self.assertEqual(["historical-operation"], recover_incomplete(self.vault))
        self.assertEqual(LEGACY_RESULT, json.loads(self.result_path.read_text())["schema"])
        self.assertEqual(journal_before, self.journal_path.read_bytes())
        self.assertEqual([], recover_incomplete(self.vault))

    def test_existing_legacy_result_idempotent_lookup(self):
        self.legacy()
        before = self.snapshot()
        result = apply_bundle(self.vault, self.operation)
        self.assertEqual(LEGACY_RESULT, result["schema"])
        self.assertEqual(before, self.snapshot())

    def test_unknown_schema_operation_identity_and_state_rejected(self):
        original = self.legacy()
        for field, value in (("schema", "claude-obsidian.transaction-journal.v2"),
                             ("schema", "other.transaction-journal.v1"),
                             ("schema", []), ("state", {}),
                             ("operation_id", "unrelated-operation"), ("state", "unknown")):
            with self.subTest(field=field, value=value):
                journal = copy.deepcopy(original)
                journal[field] = value
                self.write(self.journal_path, journal)
                before = self.snapshot()
                with self.assertRaises(TransactionError) as caught:
                    recover_incomplete(self.vault)
                self.assertEqual("CORRUPT_JOURNAL", caught.exception.code)
                self.assertEqual(before, self.snapshot())

    def test_malformed_legacy_results_rejected_without_mutation(self):
        self.legacy()
        original = json.loads(self.result_path.read_text())
        for field, value in (("schema", RESULT_SCHEMA), ("schema", "unknown"),
                             ("operation_id", "other"), ("status", "pending"),
                             ("hashes", {"wiki/Example.md": "0" * 64}),
                             ("modes", {"wiki/Example.md": 0o777}),
                             ("changed_paths", ["wiki/Elsewhere.md"]),
                             ("bundle_sha256", "0" * 64)):
            with self.subTest(field=field):
                result = copy.deepcopy(original)
                result[field] = value
                self.write(self.result_path, result)
                before = self.snapshot()
                with self.assertRaises(TransactionError) as caught:
                    recover_incomplete(self.vault)
                self.assertEqual("CORRUPT_RESULT", caught.exception.code)
                self.assertEqual(before, self.snapshot())

    def test_rolled_back_legacy_malformed_writes_are_not_skipped(self):
        original = self.legacy(rolled_back=True)
        for field, value in (("path", "../escape.md"), ("path", ".vault-meta/transactions/other/journal.json"),
                             ("backup", "../outside"), ("new_sha256", "not-a-hash"),
                             ("new_mode", True), ("mode", "delete")):
            with self.subTest(field=field):
                journal = copy.deepcopy(original)
                journal["writes"][0][field] = value
                self.write(self.journal_path, journal)
                before = self.snapshot()
                with self.assertRaises(TransactionError) as caught:
                    recover_incomplete(self.vault)
                self.assertEqual("CORRUPT_JOURNAL", caught.exception.code)
                self.assertEqual(before, self.snapshot())

    def test_incomplete_legacy_bad_backup_rejected_before_restore(self):
        journal = self.legacy()
        self.result_path.unlink()
        journal["state"] = "applying"
        self.write(self.journal_path, journal)
        (self.transaction / "backups/0000.original").write_bytes(b"corrupt backup\n")
        before = self.snapshot()
        with self.assertRaises(TransactionError) as caught:
            recover_incomplete(self.vault)
        self.assertEqual("CORRUPT_JOURNAL", caught.exception.code)
        self.assertEqual(before, self.snapshot())

    def test_incomplete_legacy_external_edit_preserved(self):
        journal = self.legacy()
        self.result_path.unlink()
        journal["state"] = "applying"
        self.write(self.journal_path, journal)
        self.note.write_bytes(b"intervening owner edit\n")
        with self.assertRaises(TransactionError) as caught:
            recover_incomplete(self.vault)
        self.assertEqual("ROLLBACK_FAILED", caught.exception.code)
        self.assertEqual(b"intervening owner edit\n", self.note.read_bytes())


if __name__ == "__main__":
    unittest.main()
