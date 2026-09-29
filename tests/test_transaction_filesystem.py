#!/usr/bin/env python3
"""Permission persistence preflight, using only synthetic temporary vaults."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_empire import transaction as tx


@unittest.skipIf(os.name == "nt", "transaction writes require POSIX/WSL")
class FilesystemPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name) / "vault"
        (self.vault / "wiki").mkdir(parents=True)
        (self.vault / ".raw").mkdir()
        self.note = self.vault / "wiki/Example.md"
        self.note.write_bytes(b"owner work\n")
        self.bundle = {
            "schema": tx.BUNDLE_SCHEMA, "operation_id": "permission-check",
            "operation_type": "generic",
            "expected_hashes": {"wiki/Example.md": tx.sha256_bytes(b"owner work\n")},
            "writes": [{"path": "wiki/Example.md", "mode": "replace", "content": "changed\n"}],
        }
        self.meta = self.vault / ".vault-meta"

    def assert_no_probe(self):
        self.assertEqual([], list(self.meta.glob(".permissions-probe-*")))

    def reject_chmod(self, descriptor, mode):
        # DrvFS without metadata may accept chmod but keep reporting 0777.
        self.real_chmod(descriptor, 0o777)

    def test_nonpersistent_chmod_rejected_before_notes_journals_or_recovery(self):
        self.real_chmod = os.fchmod
        with patch.object(tx.os, "fchmod", side_effect=self.reject_chmod), \
             patch.object(tx, "_atomic_vault_write") as write, \
             patch.object(tx, "_recover_incomplete_locked") as recovery:
            with self.assertRaises(tx.TransactionValidationError) as caught:
                tx.apply_bundle(self.vault, self.bundle)
        self.assertEqual("UNSUPPORTED_FILESYSTEM_PERMISSIONS", caught.exception.code)
        self.assertIn("observed 0777", str(caught.exception))
        self.assertIn("DrvFS metadata", str(caught.exception))
        write.assert_not_called()
        recovery.assert_not_called()
        self.assertEqual(b"owner work\n", self.note.read_bytes())
        self.assertEqual([], list(self.meta.rglob("journal.json")))
        self.assertEqual([], list((self.meta / "transactions").iterdir()))
        self.assert_no_probe()

    def test_posix_apply_keeps_exact_file_mode_and_removes_probe(self):
        self.note.chmod(0o640)
        result = tx.apply_bundle(self.vault, self.bundle)
        self.assertEqual(b"changed\n", self.note.read_bytes())
        self.assertEqual(0o640, stat.S_IMODE(self.note.stat().st_mode))
        self.assertEqual(0o640, result["modes"]["wiki/Example.md"])
        self.assert_no_probe()

    def test_chmod_failure_cleans_probe_and_does_not_write_notes(self):
        with patch.object(tx.os, "fchmod", side_effect=OSError("chmod unavailable")):
            with self.assertRaises(tx.TransactionValidationError) as caught:
                tx.apply_bundle(self.vault, self.bundle)
        self.assertEqual("FILESYSTEM_PREFLIGHT_FAILED", caught.exception.code)
        self.assertEqual(b"owner work\n", self.note.read_bytes())
        self.assertEqual([], list(self.meta.rglob("journal.json")))
        self.assert_no_probe()

    def test_recovery_rejects_before_modifying_pending_transaction(self):
        tx.apply_bundle(self.vault, self.bundle)
        operation = self.meta / "transactions/permission-check"
        (operation / "changed-paths.json").unlink()
        journal_path = operation / "journal.json"
        journal = json.loads(journal_path.read_text())
        journal["state"] = "applying"
        journal_path.write_text(json.dumps(journal))
        original = journal_path.read_bytes()
        self.real_chmod = os.fchmod
        with patch.object(tx.os, "fchmod", side_effect=self.reject_chmod):
            with self.assertRaises(tx.TransactionValidationError) as caught:
                tx.recover_incomplete(self.vault)
        self.assertEqual("UNSUPPORTED_FILESYSTEM_PERMISSIONS", caught.exception.code)
        self.assertEqual(b"changed\n", self.note.read_bytes())
        self.assertEqual(original, journal_path.read_bytes())
        self.assert_no_probe()

    def test_inspect_remains_read_only_on_unsupported_filesystem(self):
        with patch.object(tx.os, "fchmod", side_effect=AssertionError("unexpected mutation")):
            tx.inspect_bundle(self.vault, self.bundle)
        self.assertFalse(self.meta.exists())
        self.assertEqual(b"owner work\n", self.note.read_bytes())

    def test_probe_reopen_failure_cleans_up_and_closes_descriptor(self):
        self.meta.mkdir()
        parent_fd = os.open(self.meta, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent_fd)
        real_open = os.open
        probe_fds = []

        def reject_reopen(path, flags, *args, **kwargs):
            if str(path).startswith(".permissions-probe-"):
                if not flags & os.O_CREAT:
                    raise OSError("reopen unavailable")
                fd = real_open(path, flags, *args, **kwargs)
                probe_fds.append(fd)
                return fd
            return real_open(path, flags, *args, **kwargs)

        with patch.object(tx.os, "open", side_effect=reject_reopen):
            with self.assertRaises(tx.TransactionValidationError) as caught:
                tx._require_runtime_file_permissions(parent_fd)
        self.assertEqual("FILESYSTEM_PREFLIGHT_FAILED", caught.exception.code)
        self.assert_no_probe()
        self.assertEqual(1, len(probe_fds))
        with self.assertRaises(OSError):
            os.fstat(probe_fds[0])

    def test_probe_cleanup_failure_reports_exact_empty_residual(self):
        self.meta.mkdir()
        parent_fd = os.open(self.meta, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, parent_fd)
        with patch.object(tx.os, "unlink", side_effect=OSError("unlink unavailable")):
            with self.assertRaises(tx.TransactionValidationError) as caught:
                tx._require_runtime_file_permissions(parent_fd)
        self.assertEqual("FILESYSTEM_PREFLIGHT_FAILED", caught.exception.code)
        residuals = list(self.meta.iterdir())
        self.assertEqual(1, len(residuals))
        self.assertIn(residuals[0].name, str(caught.exception))
        self.assertEqual(b"", residuals[0].read_bytes())


if __name__ == "__main__":
    unittest.main()
