#!/usr/bin/env python3
"""Real native Windows transaction acceptance in synthetic local NTFS vaults."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_empire import transaction as tx


@unittest.skipUnless(os.name == "nt", "native Windows backend acceptance")
class WindowsTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="empire-native-")
        self.addCleanup(self.temporary.cleanup)
        self.vault = Path(self.temporary.name) / "Vault with spaces"
        (self.vault / "wiki").mkdir(parents=True)
        (self.vault / ".raw").mkdir()
        self.note = self.vault / "wiki" / "Notes.md"
        self.note.write_bytes(b"owner work\r\n")

    def bundle(self, identity="native-001"):
        return {
            "schema": tx.BUNDLE_SCHEMA, "operation_id": identity,
            "operation_type": "generic",
            "expected_hashes": {"wiki/Notes.md": tx.sha256_bytes(self.note.read_bytes()),
                                "wiki/新 note.md": None},
            "writes": [
                {"path": "wiki/Notes.md", "mode": "replace", "content": "# Updated\r\n"},
                {"path": "wiki/新 note.md", "mode": "create", "content": "# New\n"},
            ],
        }

    def test_apply_replay_preserves_existing_security_and_private_new_file(self):
        before_security = tx._safe_native_security(self.vault, "wiki/Notes.md")
        bundle = self.bundle()
        preview = tx.inspect_bundle(self.vault, bundle)
        self.assertFalse((self.vault / ".vault-meta").exists())
        result = tx.apply_bundle(
            self.vault, bundle, approved_plan_sha256=preview["approval_sha256"],
        )
        self.assertEqual("complete", result["status"])
        self.assertEqual(b"# Updated\r\n", self.note.read_bytes())
        self.assertEqual(before_security, tx._safe_native_security(self.vault, "wiki/Notes.md"))
        self.assertEqual(tx.os.private_security(), tx._safe_native_security(self.vault, "wiki/新 note.md"))
        self.assertEqual(result["hashes"], tx.apply_bundle(self.vault, bundle)["hashes"])

    def test_injected_failure_restores_content_and_security(self):
        original = self.note.read_bytes()
        security = tx._safe_native_security(self.vault, "wiki/Notes.md")
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            tx.apply_bundle(self.vault, self.bundle(), fail_after=1)
        self.assertEqual(original, self.note.read_bytes())
        self.assertEqual(security, tx._safe_native_security(self.vault, "wiki/Notes.md"))
        self.assertFalse((self.vault / "wiki/新 note.md").exists())
        self.assertEqual([], tx.recover_incomplete(self.vault))

    def test_review_hash_binds_native_security_changes(self):
        bundle = self.bundle()
        approval = tx.inspect_bundle(self.vault, bundle)["approval_sha256"]
        descriptor = tx.os.open(self.note, tx.os.O_RDWR)
        try:
            tx.os.set_security(descriptor, tx.os.private_security())
        finally:
            tx.os.close(descriptor)
        with self.assertRaises(tx.TransactionValidationError):
            tx.apply_bundle(self.vault, bundle, approved_plan_sha256=approval)
        self.assertEqual(b"owner work\r\n", self.note.read_bytes())

    def test_completed_replay_detects_security_drift(self):
        bundle = self.bundle()
        tx.apply_bundle(self.vault, bundle)
        descriptor = tx.os.open(self.note, tx.os.O_RDWR)
        try:
            tx.os.set_security(descriptor, tx.os.private_security())
        finally:
            tx.os.close(descriptor)
        with self.assertRaises(tx.TransactionError):
            tx.apply_bundle(self.vault, bundle)

    def test_process_liveness_probe_does_not_kill_process(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            self.assertTrue(tx._process_alive(child.pid))
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=10)

    def test_external_reader_conflict_rolls_back_prior_write_and_allows_retry(self):
        other = self.vault / "wiki" / "Other.md"
        other.write_bytes(b"other original\r\n")
        operation = self.bundle("sharing-conflict")
        operation["expected_hashes"].pop("wiki/新 note.md")
        operation["expected_hashes"]["wiki/Other.md"] = tx.sha256_bytes(other.read_bytes())
        operation["writes"][1] = {
            "path": "wiki/Other.md", "mode": "replace", "content": "# Changed\n",
        }
        # A normal CRT reader denies Windows DELETE sharing. It must not cause
        # an earlier file to remain changed or make the blocked file disappear.
        with other.open("rb") as reader:
            with self.assertRaises((OSError, tx.TransactionError)):
                tx.apply_bundle(self.vault, operation)
            self.assertEqual(b"other original\r\n", reader.read())
            self.assertEqual(b"owner work\r\n", self.note.read_bytes())
            self.assertEqual(b"other original\r\n", other.read_bytes())
        tx.recover_incomplete(self.vault)
        self.assertEqual(b"owner work\r\n", self.note.read_bytes())
        self.assertEqual("complete", tx.apply_bundle(self.vault, operation)["status"])

    def test_process_death_releases_lock_and_recovers_incomplete_apply(self):
        bundle = self.bundle("killed-apply")
        bundle_file = Path(self.temporary.name) / "bundle.json"
        bundle_file.write_text(json.dumps(bundle), encoding="utf-8")
        signal = Path(self.temporary.name) / "ready"
        program = (
            "import json,os,sys,time; from pathlib import Path; "
            "from claude_empire.transaction import apply_bundle; "
            "vault=Path(sys.argv[1]); bundle=json.loads(Path(sys.argv[2]).read_text()); "
            "signal=Path(sys.argv[3]); "
            "exec('def progress(path,index):\\n signal.write_text(\"ready\")\\n time.sleep(60)'); "
            "apply_bundle(vault,bundle,progress=progress)"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", program, str(self.vault), str(bundle_file), str(signal)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            import time
            end = time.monotonic() + 20
            while not signal.exists() and child.poll() is None and time.monotonic() < end:
                time.sleep(0.05)
            if not signal.exists():
                child.terminate()
                _, stderr = child.communicate(timeout=10)
                self.fail("child did not reach first write: " + stderr.decode(errors="replace"))
            child.kill()
            child.wait(timeout=10)
            with tx.MutationLock(self.vault, force_stale_lock=True) as held:
                self.assertEqual(
                    ["killed-apply"], tx.recover_incomplete(self.vault, mutation_lock=held),
                )
            self.assertEqual(b"owner work\r\n", self.note.read_bytes())
            self.assertFalse((self.vault / "wiki/新 note.md").exists())
            self.assertEqual([], tx.recover_incomplete(self.vault))
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            child.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
