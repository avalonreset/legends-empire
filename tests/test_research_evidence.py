"""Hermetic admission tests against actual producer-generated synthetic fixtures."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire.cli import main
from claude_empire.research_evidence import EvidenceError, verify_package, plan_intake
from claude_empire.transaction import TransactionError

FIXTURES = ROOT / "tests/fixtures/research-evidence"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        shutil.copytree(FIXTURES / "dataforseo-completed", self.package)
        self.vault = self.root / "vault"
        (self.vault / "wiki").mkdir(parents=True)
        (self.vault / ".raw").mkdir()

    def verify(self, path=None):
        return verify_package(path or self.package, workspace="synthetic-client")

    def rewrite(self, **changes):
        file = self.package / "manifest.json"
        m = json.loads(file.read_text())
        m.update(changes)
        m.pop("evidence_id")
        m["evidence_id"] = hashlib.sha256(json.dumps(m, sort_keys=True,
            ensure_ascii=False, indent=2, allow_nan=False).encode()).hexdigest()
        file.write_text(json.dumps(m), encoding="utf-8")

    def test_actual_producer_fixtures_admit_with_correct_state(self):
        for fixture in sorted(FIXTURES.iterdir()):
            if not fixture.is_dir(): continue
            with self.subTest(fixture=fixture.name):
                result = self.verify(fixture)
                self.assertTrue(result["verified"])
                self.assertEqual(result["provider_calls"], 0)
                self.assertEqual(result["writes"], 0)
                self.assertFalse(result["canonical_promotion"])
                expected = {"firecrawl-null": "unknown", "firecrawl-nested-error": "error", "firecrawl-in-progress": "pending"}.get(fixture.name, "completed" if fixture.name.endswith("legacy") else fixture.name.split("-")[-1])
                self.assertEqual(result["response_state"], expected)
                self.assertEqual(result["note_integrity"], "not_recorded" if fixture.name.endswith("legacy") else "verified")

    def test_wrong_workspace_refused(self):
        with self.assertRaises(EvidenceError):
            verify_package(self.package, workspace="other-client")

    def test_mutated_raw_and_readme_refused(self):
        for name in ("response.json", "README.md", "manifest.json"):
            file = self.package / name
            original = file.read_bytes()
            file.write_bytes(original + b" ")
            if name == "manifest.json":
                # Whitespace does not alter canonical identity; alter metadata.
                file.write_bytes(original.replace(b"synthetic-client", b"different-client"))
            with self.subTest(name=name), self.assertRaises(EvidenceError): self.verify()
            file.write_bytes(original)

    def test_unknown_schema_refused_even_with_valid_identity(self):
        self.rewrite(schema="unknown/v1")
        with self.assertRaises(EvidenceError): self.verify()

    def test_metadata_crosscheck_after_resigning(self):
        self.rewrite(status_code=20100)
        with self.assertRaises(EvidenceError): self.verify()

    def test_firecrawl_state_crosscheck_after_resigning(self):
        for file in self.package.iterdir(): file.unlink()
        for file in (FIXTURES / "firecrawl-pending").iterdir(): shutil.copyfile(file, self.package / file.name)
        self.rewrite(status="completed")
        with self.assertRaises(EvidenceError): self.verify()

    def test_strict_json_rejects_duplicates_despite_matching_hash(self):
        file = self.package / "response.json"
        raw = file.read_bytes().replace(b'"status_code": 20000', b'"status_code": 10000, "status_code": 20000', 1)
        file.write_bytes(raw)
        self.rewrite(response_sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(EvidenceError): self.verify()

    def test_nonfinite_json_refused_despite_matching_response_hash(self):
        file = self.package / "response.json"
        raw = file.read_bytes().replace(b'"cost": 0', b'"cost": NaN', 1)
        file.write_bytes(raw)
        self.rewrite(response_sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaises(EvidenceError): self.verify()

    def test_missing_member_and_bounded_reads(self):
        file = self.package / "README.md"
        original = file.read_bytes()
        file.unlink()
        with self.assertRaises((OSError, EvidenceError, TransactionError)): self.verify()
        file.write_bytes(b"x" * 2_000_001)
        with self.assertRaises(TransactionError): self.verify()
        file.write_bytes(original)

    @unittest.skipIf(os.name == "nt", "symlink creation needs Windows privilege")
    def test_linked_root_and_member_refused(self):
        linked = self.root / "linked"
        linked.symlink_to(self.package, target_is_directory=True)
        with self.assertRaises(OSError): self.verify(linked)
        member = self.package / "README.md"
        saved = member.read_bytes()
        member.unlink()
        other = self.root / "note.md"
        other.write_bytes(saved)
        member.symlink_to(other)
        with self.assertRaises(EvidenceError): self.verify()

    def test_plan_is_read_only_and_existing_target_refused(self):
        before = sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob("*"))
        result = plan_intake(self.package, workspace="synthetic-client", vault=self.vault)
        self.assertEqual(result["status"], "review_required")
        self.assertIsNone(result["transaction"])
        self.assertEqual(before, sorted(str(p.relative_to(self.vault)) for p in self.vault.rglob("*")))
        (self.vault / result["destination"]).mkdir(parents=True)
        with self.assertRaises(EvidenceError): plan_intake(self.package, workspace="synthetic-client", vault=self.vault)

    def test_cli_emits_no_raw_response_or_request(self):
        for command in ("verify", "plan"):
            args = ["research-evidence", command, str(self.package), "--workspace", "synthetic-client"]
            if command == "plan": args += ["--vault", str(self.vault)]
            out = io.StringIO()
            with contextlib.redirect_stdout(out): code = main(args)
            self.assertEqual(code, 0, out.getvalue())
            result = json.loads(out.getvalue())
            self.assertNotIn("request", result)
            self.assertNotIn("response", result)
            self.assertNotIn("Synthetic", out.getvalue())


if __name__ == "__main__": unittest.main()
