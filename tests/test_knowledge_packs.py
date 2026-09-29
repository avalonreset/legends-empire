#!/usr/bin/env python3
"""Knowledge attachment exercises real transactions in isolated synthetic vaults."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire.cli import main
from claude_empire.knowledge import attachment_bundle, load_pack
from claude_empire.transaction import TransactionError, apply_bundle, inspect_bundle, sha256_bytes

STAMP = "2026-09-28T12:00:00Z"
DEST = "wiki/library/legends-empire/stewardship"
POSIX = unittest.skipIf(os.name == "nt", "writes require POSIX or WSL")


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.vault = self.parent / "owner"
        (self.vault / "wiki").mkdir(parents=True)
        (self.vault / ".raw").mkdir()
        self.source = self.parent / "module"
        (self.source / "vault").mkdir(parents=True)
        self.manifest = self.source / "pack.json"
        self.files = {"index.md": "# Stewardship\n\n[Review](review.md)\n", "review.md": "# Review\nRead evidence.\n"}
        self.publish()

    def publish(self, version="0.2.0"):
        rows = []
        for path, text in self.files.items():
            target = self.source / "vault" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = text.encode("utf-8")
            target.write_bytes(payload)
            rows.append({"path": path, "sha256": sha256_bytes(payload)})
        self.data = {"schema": "legends.knowledge-pack/v1", "id": "stewardship", "module": "legends-empire",
                     "version": version, "entrypoint": "index.md", "files": rows,
                     "ontology": {"kind": "machinery", "suggested_home": DEST, "purpose": "Keep meaningful work discoverable."}}
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")

    def bundle(self, operation="attach-first", **kwargs):
        return attachment_bundle(self.vault, self.manifest, operation_id=operation, generated_at=STAMP, **kwargs)

    def apply(self, bundle=None):
        bundle = bundle or self.bundle()
        approval = inspect_bundle(self.vault, bundle)["approval_sha256"]
        return apply_bundle(self.vault, bundle, approved_plan_sha256=approval)

    def test_preview_has_no_side_effects_and_explicit_generic_bundle(self):
        bundle = self.bundle()
        result = inspect_bundle(self.vault, bundle)
        self.assertEqual("generic", bundle["operation_type"])
        self.assertEqual(4, len(result["changed_paths"]))
        self.assertFalse((self.vault / "wiki/library").exists())
        self.assertFalse((self.vault / ".vault-meta").exists())

    @POSIX
    def test_attach_idempotence_and_personalized_index_preserved(self):
        self.apply()
        index = self.vault / DEST / "index.md"
        index.write_text("# My own integration note\n", encoding="utf-8")
        self.assertEqual([], self.bundle("again")["writes"])
        self.assertEqual("# My own integration note\n", index.read_text())
        self.assertFalse((self.vault / DEST / "work").exists())

    @POSIX
    def test_update_preserves_private_records_and_obsolete_reference(self):
        self.apply()
        work = self.vault / DEST / "work/private.md"
        work.parent.mkdir()
        work.write_bytes(b"private evidence\r\n")
        old_reference = self.vault / DEST / "reference/review.md"
        before = old_reference.read_bytes()
        del self.files["review.md"]
        self.files["index.md"] = "# New reviewed guidance\n"
        self.publish("0.2.1")
        self.apply(self.bundle("update"))
        self.assertEqual(before, old_reference.read_bytes())
        self.assertEqual(b"private evidence\r\n", work.read_bytes())
        state = json.loads((self.vault / DEST / "pack-state.json").read_text())
        self.assertTrue(state["files"]["review.md"]["obsolete"])
        self.assertEqual("0.2.1", state["version"])
        self.assertEqual([], self.bundle("again")["writes"])

    @POSIX
    def test_rollback_updates_only_unmodified_references(self):
        self.apply()
        self.files["index.md"] = "# Version two\n"
        self.publish("0.2.1")
        self.apply(self.bundle("update"))
        self.files["index.md"] = "# Stewardship\n\n[Review](review.md)\n"
        self.publish("0.2.0")
        self.apply(self.bundle("rollback"))
        self.assertEqual(self.files["index.md"], (self.vault / DEST / "reference/index.md").read_text())

    @POSIX
    def test_modified_reference_refuses_whole_update(self):
        self.apply()
        reference = self.vault / DEST / "reference/review.md"
        reference.write_text("owner edit", encoding="utf-8")
        self.files["index.md"] = "# Update\n"
        self.publish("0.2.1")
        with self.assertRaisesRegex(TransactionError, "modified or removed"):
            self.bundle("update")
        self.assertEqual("owner edit", reference.read_text())

    @POSIX
    def test_deleted_reference_refuses_update(self):
        self.apply()
        (self.vault / DEST / "reference/review.md").unlink()
        with self.assertRaisesRegex(TransactionError, "modified or removed"):
            self.bundle("again")

    @POSIX
    def test_tampered_state_rejected(self):
        self.apply()
        state_path = self.vault / DEST / "pack-state.json"
        state = json.loads(state_path.read_text())
        state["files"]["review.md"]["sha256"] = "0" * 64
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(TransactionError, "integrity mismatch"):
            self.bundle("again")

    @POSIX
    def test_raced_unchanged_reference_rejects_reviewed_transaction(self):
        self.apply()
        self.files["index.md"] = "# Changed upstream\n"
        self.publish("0.2.1")
        bundle = self.bundle("update")
        approval = inspect_bundle(self.vault, bundle)["approval_sha256"]
        reference = self.vault / DEST / "reference/review.md"
        self.assertIn(f"{DEST}/reference/review.md", bundle["read_preconditions"])
        reference.write_text("changed after preview", encoding="utf-8")
        with self.assertRaises(TransactionError):
            apply_bundle(self.vault, bundle, approved_plan_sha256=approval)
        self.assertNotEqual(self.files["index.md"], (self.vault / DEST / "reference/index.md").read_text())

    @POSIX
    def test_raced_state_or_index_rejects_update(self):
        self.apply()
        self.files["index.md"] = "# Changed\n"
        self.publish("0.2.1")
        bundle = self.bundle("update")
        approval = inspect_bundle(self.vault, bundle)["approval_sha256"]
        (self.vault / DEST / "index.md").write_text("new user intent", encoding="utf-8")
        with self.assertRaises(TransactionError):
            apply_bundle(self.vault, bundle, approved_plan_sha256=approval)

    def test_hash_mismatch_and_duplicate_fields_rejected(self):
        (self.source / "vault/review.md").write_text("altered", encoding="utf-8")
        with self.assertRaisesRegex(TransactionError, "hash mismatch"):
            self.bundle()
        self.manifest.write_text('{"schema":1,"schema":2}', encoding="utf-8")
        with self.assertRaises(TransactionError):
            load_pack(self.manifest)

    def test_escape_absolute_reserved_and_non_markdown_paths_rejected(self):
        original = copy.deepcopy(self.data)
        for path in ("../outside.md", "/outside.md", "C:/outside.md", "a\\b.md", "a//b.md", "a/./b.md", "CON.md", "run.py"):
            with self.subTest(path=path):
                self.data = copy.deepcopy(original)
                self.data["files"][0]["path"] = path
                self.save_manifest()
                with self.assertRaises(TransactionError):
                    self.bundle()

    def test_case_collisions_rejected(self):
        self.data["files"].append({"path": "INDEX.md", "sha256": self.data["files"][0]["sha256"]})
        self.save_manifest()
        with self.assertRaisesRegex(TransactionError, "colliding"):
            self.bundle()

    def test_entrypoint_and_schema_required(self):
        self.data["entrypoint"] = "absent.md"
        self.save_manifest()
        with self.assertRaisesRegex(TransactionError, "entrypoint"):
            self.bundle()
        self.data["schema"] = "future-schema"
        self.save_manifest()
        with self.assertRaisesRegex(TransactionError, "schema"):
            self.bundle()

    def test_destination_collision_and_scope(self):
        (self.vault / DEST).mkdir(parents=True)
        (self.vault / DEST / "index.md").write_text("user note", encoding="utf-8")
        with self.assertRaisesRegex(TransactionError, "already exists"):
            self.bundle()
        for destination in ("wiki/library", "projects/steward", "wiki/library/../outside"):
            with self.subTest(destination=destination), self.assertRaises(TransactionError):
                self.bundle(destination=destination)

    def test_custom_destination_and_separate_source(self):
        bundle = self.bundle(destination="wiki/library/custom/shelf")
        self.assertTrue(all(row["path"].startswith("wiki/library/custom/shelf/") for row in bundle["writes"]))
        with self.assertRaisesRegex(TransactionError, "separate"):
            attachment_bundle(self.parent, self.manifest, operation_id="bad", generated_at=STAMP)

    @POSIX
    def test_symlink_source_or_destination_rejected(self):
        source_note = self.source / "vault/review.md"
        source_note.unlink()
        source_note.symlink_to(self.source / "vault/index.md")
        with self.assertRaises(TransactionError):
            self.bundle()
        source_note.unlink()
        self.publish()
        (self.vault / "wiki/library").symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(TransactionError):
            self.bundle()

    @POSIX
    def test_same_version_changed_bytes_requires_release_bump(self):
        self.apply()
        self.files["index.md"] = "# Different\n"
        self.publish()
        with self.assertRaisesRegex(TransactionError, "same pack version"):
            self.bundle("update")

    def test_cli_preview_and_required_explicit_selection(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(["knowledge", "attach", str(self.manifest), "--vault", str(self.vault),
                           "--operation-id", "cli-preview", "--generated-at", STAMP])
        self.assertEqual(0, result)
        report = json.loads(output.getvalue())
        self.assertEqual("dry-run", report["status"])
        self.assertEqual(64, len(report["approved_plan_sha256"]))
        self.assertFalse((self.vault / DEST).exists())

    @POSIX
    def test_cli_apply_requires_review_hash_and_applies_exact_plan(self):
        args = ["knowledge", "attach", str(self.manifest), "--vault", str(self.vault),
                "--operation-id", "cli-apply", "--generated-at", STAMP]
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, main(args))
        approval = json.loads(output.getvalue())["approved_plan_sha256"]
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, main(args + ["--apply"]))
        self.assertFalse((self.vault / DEST).exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, main(args + ["--apply", "--approved-plan-sha256", approval]))
        self.assertTrue((self.vault / DEST / "reference/index.md").is_file())


if __name__ == "__main__":
    unittest.main()
