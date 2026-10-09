#!/usr/bin/env python3
"""Foreign terminal history stays inspectable; recovery/replay remains native.

The foreign metadata is synthesized from an actual local transaction. These
tests exercise admission policy, not claim an actual cross-OS vault transfer.
"""
from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
