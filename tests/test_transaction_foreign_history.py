#!/usr/bin/env python3
"""Foreign terminal history stays inspectable; recovery/replay remains native.

The foreign metadata is synthesized from an actual local transaction. These
tests exercise admission policy, not claim an actual cross-OS vault transfer.
"""
from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_empire import transaction as tx


class ForeignTerminalHistoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "vault"
        (self.root / "wiki").mkdir(parents=True)
        (self.root / ".raw").mkdir()
        self.original = self.bundle("original", "Original")
        tx.apply_bundle(self.root, self.original)
        self.operation = self.root / ".vault-meta/transactions/original"
        self.journal_path = self.operation / "journal.json"
        self.result_path = self.operation / "changed-paths.json"
        journal = json.loads(self.journal_path.read_text())
        result = json.loads(self.result_path.read_text())
        if os.name == "nt":
            journal.pop("native_security")
            result.pop("native_security")
            result.pop("security_hashes")
            for write in journal["writes"]:
                write.pop("original_security")
                write.pop("new_security")
        else:
            descriptor = "O:SYG:SYD:P(A;;FA;;;SY)"
            journal["native_security"] = result["native_security"] = "windows-sddl-v1"
            result["security_hashes"] = {}
            for write in journal["writes"]:
                write["original_security"] = descriptor if write["original_sha256"] else None
                write["new_security"] = descriptor
                result["security_hashes"][write["path"]] = tx.sha256_bytes(descriptor.encode())
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")
        self.result_path.write_text(json.dumps(result), encoding="utf-8")

    @staticmethod
    def bundle(identity, name):
        path = f"wiki/{name}.md"
        return {
            "schema": tx.BUNDLE_SCHEMA, "operation_type": "generic",
            "operation_id": identity, "expected_hashes": {path: None},
            "writes": [{"path": path, "mode": "create", "content": f"# {name}\n"}],
        }

    def test_valid_complete_foreign_history_does_not_block_new_work_or_change_history(self):
        before = (self.journal_path.read_bytes(), self.result_path.read_bytes())
        self.assertEqual([], tx.recover_incomplete(self.root))
        self.assertEqual("complete", tx.apply_bundle(self.root, self.bundle("next", "Next"))["status"])
        self.assertEqual(before, (self.journal_path.read_bytes(), self.result_path.read_bytes()))

    def test_foreign_replay_refused(self):
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.root, self.original)
        self.assertEqual("# Original\n", (self.root / "wiki/Original.md").read_text())

    def test_incomplete_foreign_history_refused_without_mutation(self):
        journal = json.loads(self.journal_path.read_text())
        journal["state"] = "applying"
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")
        before = self.journal_path.read_bytes()
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.root, self.bundle("next", "Next"))
        self.assertEqual(before, self.journal_path.read_bytes())
        self.assertFalse((self.root / "wiki/Next.md").exists())

    def test_missing_foreign_result_requires_original_backend(self):
        self.result_path.unlink()
        with self.assertRaises(tx.TransactionError):
            tx.recover_incomplete(self.root)
        self.assertFalse(self.result_path.exists())

    def test_foreign_result_hash_mismatch_refused(self):
        result = json.loads(self.result_path.read_text())
        result["hashes"]["wiki/Original.md"] = "0" * 64
        self.result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.root, self.bundle("next", "Next"))
        self.assertFalse((self.root / "wiki/Next.md").exists())

    def test_complete_windows_history_from_previous_owner_remains_opaque(self):
        journal = json.loads(self.journal_path.read_text())
        result = json.loads(self.result_path.read_text())
        descriptor = "O:SYG:SYD:P(A;;FA;;;SY)"
        journal["native_security"] = result["native_security"] = "windows-sddl-v1"
        result["security_hashes"] = {}
        for write in journal["writes"]:
            write["original_security"] = descriptor if write["original_sha256"] else None
            write["new_security"] = descriptor
            result["security_hashes"][write["path"]] = tx.sha256_bytes(descriptor.encode())
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")
        self.result_path.write_text(json.dumps(result), encoding="utf-8")
        before = (self.journal_path.read_bytes(), self.result_path.read_bytes())
        self.assertEqual("complete", tx.apply_bundle(self.root, self.bundle("next", "Next"))["status"])
        self.assertEqual(before, (self.journal_path.read_bytes(), self.result_path.read_bytes()))
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.root, self.original)

    def test_malformed_terminal_security_is_refused_without_interpretation(self):
        journal = json.loads(self.journal_path.read_text())
        result = json.loads(self.result_path.read_text())
        journal["native_security"] = result["native_security"] = "windows-sddl-v1"
        result["security_hashes"] = {}
        for write in journal["writes"]:
            write["original_security"] = None
            write["new_security"] = ""
            result["security_hashes"][write["path"]] = tx.sha256_bytes(b"")
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")
        self.result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.root, self.bundle("next", "Next"))
        self.assertFalse((self.root / "wiki/Next.md").exists())


class ForeignRolledBackHistoryTests(unittest.TestCase):
    """Real rollback receipts remain immutable after their platform is changed."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "vault"
        (self.root / "wiki").mkdir(parents=True)
        (self.root / ".raw").mkdir()
        self.note = self.root / "wiki/Original.md"
        self.note.write_bytes(b"original\n")
        self.original = {
            "schema": tx.BUNDLE_SCHEMA, "operation_type": "generic",
            "operation_id": "rolled-history",
            "expected_hashes": {
                "wiki/Original.md": tx.sha256_bytes(b"original\n"),
                "wiki/Created.md": None,
            },
            "writes": [
                {"path": "wiki/Original.md", "mode": "replace", "content": "replacement\n"},
                {"path": "wiki/Created.md", "mode": "create", "content": "created\n"},
            ],
        }
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            tx.apply_bundle(self.root, self.original, fail_after=2)
        self.operation = self.root / ".vault-meta/transactions/rolled-history"
        self.journal_path = self.operation / "journal.json"
        self.backup = self.operation / "backups/0000.original"
        journal = json.loads(self.journal_path.read_text())
        self.assertEqual("rolled-back", journal["state"])
        self.assertFalse((self.operation / "changed-paths.json").exists())
        if os.name == "nt":
            journal.pop("native_security")
            for write in journal["writes"]:
                write.pop("original_security")
                write.pop("new_security")
        else:
            descriptor = "O:SYG:SYD:P(A;;FA;;;SY)"
            journal["native_security"] = "windows-sddl-v1"
            for write in journal["writes"]:
                write["original_security"] = descriptor if write["original_sha256"] else None
                write["new_security"] = descriptor
        self.journal = journal
        self.write_journal(journal)

    def write_journal(self, journal):
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")

    def snapshot(self):
        # The lock is transient; include all persistent content and directories.
        return {
            path.relative_to(self.root).as_posix():
                path.read_bytes() if path.is_file() else None
            for path in self.root.rglob("*") if path.name != "mutation.lock"
        }

    def assert_refused_unchanged(self, code="CORRUPT_JOURNAL"):
        before = self.snapshot()
        with self.assertRaises(tx.TransactionError) as caught:
            tx.recover_incomplete(self.root)
        self.assertEqual(code, caught.exception.code)
        self.assertEqual(before, self.snapshot())

    def test_both_schema_families_preserve_terminal_history_and_owner_edits(self):
        self.note.write_bytes(b"later owner work\n")
        for schema in (tx.JOURNAL_SCHEMA, "claude-obsidian.transaction-journal.v1"):
            with self.subTest(schema=schema):
                journal = copy.deepcopy(self.journal)
                journal["schema"] = schema
                self.write_journal(journal)
                before = self.snapshot()
                self.assertEqual([], tx.recover_incomplete(self.root))
                self.assertEqual(before, self.snapshot())
        history = {path: data for path, data in self.snapshot().items()
                   if path.startswith(".vault-meta/transactions/rolled-history")}
        result = tx.apply_bundle(self.root, ForeignTerminalHistoryTests.bundle("next", "Next"))
        self.assertEqual("complete", result["status"])
        after = self.snapshot()
        self.assertEqual(history, {path: data for path, data in after.items()
                                  if path.startswith(".vault-meta/transactions/rolled-history")})
        self.assertEqual(b"later owner work\n", self.note.read_bytes())
        self.assertFalse((self.root / "wiki/Created.md").exists())

    def test_foreign_retry_cannot_delete_history_or_replay_writes(self):
        before = self.snapshot()
        with self.assertRaises(tx.TransactionError) as caught:
            tx.apply_bundle(self.root, self.original)
        self.assertEqual("UNSUPPORTED_JOURNAL_PLATFORM", caught.exception.code)
        self.assertEqual(before, self.snapshot())

    def test_every_incomplete_foreign_state_requires_original_backend(self):
        for state in ("prepared", "applying", "rollback-failed"):
            with self.subTest(state=state):
                journal = copy.deepcopy(self.journal)
                journal["state"] = state
                self.write_journal(journal)
                self.assert_refused_unchanged("UNSUPPORTED_JOURNAL_PLATFORM")

    def test_malformed_paths_hashes_modes_and_backup_bindings_fail_closed(self):
        for field, value in (
            ("path", "../escape.md"),
            ("path", ".vault-meta/transactions/other/journal.json"),
            ("path", "wiki\\Original.md"),
            ("path", "wiki/\x00.md"),
            ("backup", "../outside"),
            ("backup", "0001.original"),
            ("new_sha256", "not-a-hash"),
            ("original_sha256", "0" * 64),
            ("new_mode", True),
            ("original_mode", -1),
            ("mode", "delete"),
        ):
            with self.subTest(field=field, value=value):
                journal = copy.deepcopy(self.journal)
                journal["writes"][0][field] = value
                self.write_journal(journal)
                self.assert_refused_unchanged()

    def test_duplicate_write_and_invalid_envelope_fail_closed(self):
        for field, value in (
            ("writes", []),
            ("writes", self.journal["writes"] * 2),
            ("writes", [None]),
            ("operation_type", "delete"),
            ("operation_id", "different"),
            ("schema", "unknown"),
            ("state", {}),
        ):
            with self.subTest(field=field):
                journal = copy.deepcopy(self.journal)
                journal[field] = value
                self.write_journal(journal)
                self.assert_refused_unchanged()

    def test_missing_and_corrupt_backup_fail_closed(self):
        self.backup.write_bytes(b"corrupted backup\n")
        self.assert_refused_unchanged()
        self.backup.unlink()
        self.assert_refused_unchanged()

    def test_create_entry_with_unexpected_backup_fails_closed(self):
        (self.operation / "backups/0001.original").write_bytes(b"unexpected\n")
        self.assert_refused_unchanged()

    def test_opaque_security_still_requires_bounded_consistent_metadata(self):
        descriptor = "O:SYG:SYD:P(A;;FA;;;SY)"
        original = copy.deepcopy(self.journal)
        original["native_security"] = "windows-sddl-v1"
        for write in original["writes"]:
            write["original_security"] = descriptor if write["original_sha256"] else None
            write["new_security"] = descriptor
        for value in ("", "\x00", "x" * 65537, None, 42):
            with self.subTest(value_type=type(value).__name__, length=len(str(value))):
                journal = copy.deepcopy(original)
                journal["writes"][0]["original_security"] = value
                journal["writes"][0]["new_security"] = value
                self.write_journal(journal)
                self.assert_refused_unchanged()
        journal = copy.deepcopy(original)
        journal["writes"][0]["new_security"] = "different bounded descriptor"
        self.write_journal(journal)
        self.assert_refused_unchanged()
        journal = copy.deepcopy(original)
        journal.pop("native_security")
        self.write_journal(journal)
        self.assert_refused_unchanged()
        journal["native_security"] = "unknown-platform"
        self.write_journal(journal)
        self.assert_refused_unchanged("UNSUPPORTED_JOURNAL_PLATFORM")


if __name__ == "__main__":
    unittest.main()
