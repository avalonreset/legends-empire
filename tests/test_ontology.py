#!/usr/bin/env python3
"""Hermetic unit tests for claude_empire.ontology (Spec v1.1 compliant)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import cli
from claude_empire import ontology as ont
from claude_empire import transaction as tx


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class OntologyTests(unittest.TestCase):
    def test_split_frontmatter_strict_standard(self) -> None:
        content = b"---\nentity_id: acme\naliases:\n  - Acme Corp\n---\n# Acme Overview\nBody text."
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, _, body = res
        self.assertFalse(has_bom)
        self.assertEqual(nl, b"\n")
        self.assertIn("entity_id: acme", fm_lines)
        self.assertEqual(body, b"# Acme Overview\nBody text.")

    def test_split_frontmatter_strict_crlf_and_bom(self) -> None:
        content = b"\xef\xbb\xbf---\r\nentity_id: acme\r\n---\r\nBody text\r\n"
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, _, body = res
        self.assertTrue(has_bom)
        self.assertEqual(nl, b"\r\n")
        self.assertEqual(body, b"Body text\r\n")

    def test_split_frontmatter_strict_empty_frontmatter(self) -> None:
        content = b"---\n---\nBody text"
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, _, body = res
        self.assertEqual(fm_lines, [])
        self.assertEqual(body, b"Body text")

    def test_split_frontmatter_strict_rejects_multiple_boms(self) -> None:
        content = b"\xef\xbb\xbf---\n\xef\xbb\xbfkey: v\n---\nBody"
        res = ont.split_frontmatter_strict(content)
        self.assertIsNone(res)

    def test_secret_detection(self) -> None:
        self.assertIsNotNone(ont.detect_secret_like("sk-" + "1234567890abcdef123456"))
        self.assertIsNotNone(ont.detect_secret_like("ghp_" + "1234567890abcdef1234567890"))
        self.assertIsNotNone(ont.detect_secret_like("AKIA" + "1234567890ABCDEF"))
        self.assertIsNotNone(ont.detect_secret_like("pit-" + "1234567890abcdef123456"))
        self.assertIsNotNone(ont.detect_secret_like("whsec_" + "1234567890abcdef123456"))
        self.assertIsNotNone(ont.detect_secret_like("-----BEGIN " + "RSA PRIVATE KEY-----"))
        # Authorized secret-ref pointer in fact value
        self.assertIsNone(ont.detect_secret_like("secret-ref:vault.env#FOO", is_fact_value=True))
        # Malformed secret-ref
        self.assertIsNotNone(ont.detect_secret_like("secret-ref:bad", is_fact_value=True))
        # Secret-ref outside fact value is rejected
        self.assertIsNotNone(ont.detect_secret_like("secret-ref:vault.env#FOO", is_fact_value=False))

    def test_parse_note_and_nested_detection(self) -> None:
        content = b"---\nentity_id: acme\naliases:\n  - Acme\n  - The Acme\nfact.team: Engineering\n---\n# Acme\n"
        note = ont.parse_note(content, side="old", path="test.md")
        self.assertTrue(note.has_frontmatter)
        self.assertEqual(note.values.get("entity_id"), "acme")
        self.assertEqual(note.list_value("aliases"), ["Acme", "The Acme"])
        self.assertEqual(note.values.get("fact.team"), "Engineering")

        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            nested_content = b"---\ndescription:\n  nested: true\n---\n# Nested\n"
            (wiki / "nested.md").write_bytes(nested_content)
            idx, _ = ont.build_index(vault, as_of="2026-11-14", write_file=False)
            findings = idx.get("findings", [])
            self.assertTrue(any(f["code"] == "frontmatter_nested_unsupported" for f in findings))

    def test_index_build_and_check_and_resolve(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            (wiki / "acme.md").write_text(
                "---\n"
                "entity_id: acme\n"
                "title: Acme Corporation\n"
                "aliases:\n"
                "  - Acme\n"
                "  - Acme Corp\n"
                "fact.domain: acme.com\n"
                "certified_owner:\n"
                "  - fact.domain\n"
                "certified_on: 2026-11-14\n"
                "---\n"
                "# Acme Corporation\n",
                encoding="utf-8",
            )

            idx, code = ont.build_index(vault, as_of="2026-11-14")
            self.assertEqual(code, 0)
            self.assertEqual(idx["counts"]["entities"], 1)
            self.assertIn("acme", idx["entities"])
            self.assertIn("acme", idx["aliases"])
            self.assertIn("acme corp", idx["aliases"])
            self.assertEqual(idx["counts"]["certified"], 1)

            saved, c_code = ont.check_index(vault, as_of="2026-11-14")
            self.assertEqual(c_code, 0)
            self.assertIsNotNone(saved)

            # Resolve
            res, r_code = ont.resolve_term(vault, "Acme")
            self.assertEqual(r_code, 0)
            self.assertEqual(res["entity_id"], "acme")

            # Unknown resolve
            res_u, u_code = ont.resolve_term(vault, "NonExistent")
            self.assertEqual(u_code, 4)

    def test_alias_collision_exit_3(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            (wiki / "e1.md").write_text(
                "---\nentity_id: e1\naliases:\n  - SharedAlias\n---\n", encoding="utf-8"
            )
            (wiki / "e2.md").write_text(
                "---\nentity_id: e2\naliases:\n  - SharedAlias\n---\n", encoding="utf-8"
            )

            res, code = ont.resolve_term(vault, "SharedAlias")
            self.assertEqual(code, 3)
            self.assertEqual(res["candidates"], ["e1", "e2"])

    def test_need_keys_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            (wiki / "entity.md").write_text(
                "---\n"
                "entity_id: bakery\n"
                "fact.name: Sweet Treats\n"
                "fact.tax_id: 12-3456789\n"
                "certified_owner:\n"
                "  - fact.name\n"
                "certified_on: 2026-11-14\n"
                "review_after: 2026-10-01\n"
                "---\n",
                encoding="utf-8",
            )

            idx, _ = ont.build_index(vault, as_of="2026-11-14", write_file=False)

            # Missing key -> 11
            rep1, code1 = ont.need_keys(vault, keys=["bakery.missing"], index_doc=idx)
            self.assertEqual(code1, 11)

            # Stale key (review_after in past) -> 13
            rep2, code2 = ont.need_keys(vault, keys=["bakery.name"], as_of="2026-11-14", index_doc=idx)
            self.assertEqual(code2, 13)

            # Draft key -> 10
            rep3, code3 = ont.need_keys(vault, keys=["bakery.tax_id"], index_doc=idx)
            self.assertEqual(code3, 10)

            # Conflict > Missing > Stale > Draft > 0
            rep4, code4 = ont.need_keys(
                vault, keys=["bakery.missing", "bakery.name", "bakery.tax_id"], as_of="2026-11-14", index_doc=idx
            )
            self.assertEqual(code4, 11)

    def test_secret_redaction_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            secret_token = "sk-" + "live_0123456789abcdef0123456789"
            (wiki / "secret.md").write_text(
                f"---\n"
                f"entity_id: secret-co\n"
                f"title: {secret_token}\n"
                f"aliases:\n"
                f"  - {secret_token}\n"
                f"fact.api_token: {secret_token}\n"
                f"---\n",
                encoding="utf-8",
            )

            idx, _ = ont.build_index(vault, as_of="2026-11-14", write_file=False)
            idx_str = json.dumps(idx)
            self.assertNotIn(secret_token, idx_str)
            findings = idx.get("findings", [])
            self.assertTrue(any(f["code"] == "ontology_secret_like_value" for f in findings))
            for f in findings:
                if f["code"] == "ontology_secret_like_value":
                    self.assertNotIn(secret_token, f.get("detail", ""))

    def test_propose_and_certify_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            (wiki / "unindexed.md").write_text("# Unindexed Note\nContent.\n", encoding="utf-8")
            bundles, paths, approvals, code = ont.propose_bundles(
                vault, operation_id="test-prop", generated_at="2026-11-14T00:00:00Z"
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(bundles), 1)
            b = bundles[0]
            self.assertEqual(b["operation_type"], "ontology")
            self.assertIn("entity_id:", b["writes"][0]["content"])
            self.assertTrue(len(approvals) == 1 and len(approvals[0]) == 64)

            (wiki / "indexed.md").write_text(
                "---\nentity_id: ent1\nfact.domain: example.com\n---\n", encoding="utf-8"
            )
            digest = wiki / "digest.md"
            digest.write_text("# Digest\nApproved.\n", encoding="utf-8")

            cert_b, cert_path, app_hash, c_code = ont.certify_bundle(
                vault,
                keys=["ent1.domain"],
                cert_class="owner",
                operation_id="test-cert",
                generated_at="2026-11-14T00:00:00Z",
                digest="wiki/digest.md",
            )
            self.assertEqual(c_code, 0)
            self.assertEqual(cert_b["operation_type"], "ontology")
            self.assertEqual(cert_b["certification"]["class"], "owner")
            self.assertIn("wiki/digest.md", cert_b["read_preconditions"])
            self.assertTrue(len(app_hash) == 64)

    def test_run_benchmark_without_and_with_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "note.md").write_text(
                "---\nentity_id: b-ent\naliases:\n  - BEnt\nfact.city: Atlanta\n---\n",
                encoding="utf-8",
            )
            # Without tasks: index & latency telemetry only
            res = ont.run_benchmark(vault, as_of="2026-11-14")
            self.assertEqual(res["status"], "complete")
            self.assertEqual(res["entities_indexed"], 1)
            self.assertEqual(res["aliases_indexed"], 1)
            self.assertEqual(res["facts_total"], 1)
            self.assertNotIn("wrong_record_rate", res)

            # With tasks
            task_file = vault / "tasks.json"
            task_file.write_text(
                json.dumps([
                    {"query": "BEnt", "expected_entity_id": "b-ent"},
                    {"need_keys": ["b-ent.city"], "expect_halt": True},
                ]),
                encoding="utf-8",
            )
            res_tasks = ont.run_benchmark(vault, tasks_path=task_file, attempts=2, as_of="2026-11-14")
            self.assertEqual(res_tasks["tasks_evaluated"], 2)
            self.assertEqual(res_tasks["wrong_record_rate"], 0.0)
            self.assertEqual(res_tasks["gating_accuracy_percent"], 100.0)

    def test_cli_propose_inspect_apply_roundtrip_lf_and_crlf_bom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            # Note 1: LF without frontmatter
            lf_note = wiki / "lf_note.md"
            lf_note.write_bytes(b"# Standard Note\nLine one.\nLine two.\n")

            # Note 2: CRLF with BOM and existing non-ontology frontmatter
            crlf_note = wiki / "crlf_note.md"
            crlf_raw = (
                b"\xef\xbb\xbf---\r\n"
                b"title: Complex Note\r\n"
                b"custom_key: preserve_me\r\n"
                b"---\r\n"
                b"# Complex Note\r\n"
                b"Windows body.\r\n"
            )
            crlf_note.write_bytes(crlf_raw)

            # Run propose via CLI
            code = cli.main([
                "ontology",
                "propose",
                "--vault",
                str(vault),
                "--generated-at",
                "2026-11-14T12:00:00Z",
                "--operation-id",
                "test-prop-e2e",
            ])
            self.assertEqual(code, 0)

            bundle_file = vault / ".vault-meta" / "ontology" / "bundles" / "bundle-propose-0.json"
            self.assertTrue(bundle_file.is_file())
            bundle = json.loads(bundle_file.read_text(encoding="utf-8"))

            # Inspect bundle through transaction engine
            plan = tx.inspect_bundle(vault, bundle)
            self.assertTrue(plan["valid"])
            self.assertEqual(plan["operation_type"], "ontology")

            # Apply bundle with approval hash
            res = tx.apply_bundle(vault, bundle, approved_plan_sha256=plan["approval_sha256"])
            self.assertEqual(res["status"], "complete")

            # Verify LF note updated cleanly with \n
            lf_updated = lf_note.read_bytes()
            self.assertFalse(lf_updated.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"entity_id: lf-note\n", lf_updated)
            self.assertIn(b"# Standard Note\nLine one.\nLine two.\n", lf_updated)
            self.assertNotIn(b"\r\n", lf_updated)

            # Verify CRLF+BOM note preserved BOM, preserved \r\n, and preserved custom_key
            crlf_updated = crlf_note.read_bytes()
            self.assertTrue(crlf_updated.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", crlf_updated)
            self.assertIn(b"custom_key: preserve_me\r\n", crlf_updated)
            self.assertIn(b"entity_id: crlf-note\r\n", crlf_updated)
            self.assertIn(b"# Complex Note\r\nWindows body.\r\n", crlf_updated)

    def test_cli_certify_owner_inspect_apply_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            store_note = wiki / "store.md"
            store_note.write_text(
                "---\nentity_id: store\nfact.phone: 555-1234\n---\n# Store\n", encoding="utf-8"
            )
            digest = wiki / "digest.md"
            digest.write_text("# Digest of Store Records\nVerified phone number.\n", encoding="utf-8")

            # Run certify owner via CLI
            code = cli.main([
                "ontology",
                "certify",
                "--vault",
                str(vault),
                "--class",
                "owner",
                "--keys",
                "store.phone",
                "--digest",
                "wiki/digest.md",
                "--generated-at",
                "2026-11-14T12:00:00Z",
                "--operation-id",
                "cert-owner-e2e",
            ])
            self.assertEqual(code, 0)

            bundle_file = vault / ".vault-meta" / "ontology" / "bundles" / "bundle-certify-cert-owner-e2e.json"
            self.assertTrue(bundle_file.is_file())
            bundle = json.loads(bundle_file.read_text(encoding="utf-8"))

            plan = tx.inspect_bundle(vault, bundle)
            self.assertTrue(plan["valid"])

            res = tx.apply_bundle(vault, bundle, approved_plan_sha256=plan["approval_sha256"])
            self.assertEqual(res["status"], "complete")

            # Verify note updated with certified_owner
            content = store_note.read_text(encoding="utf-8")
            self.assertIn("certified_owner:", content)
            self.assertIn("  - fact.phone", content)

            # Query need_keys -> exit code 0
            rep, n_code = ont.need_keys(vault, keys=["store.phone"])
            self.assertEqual(n_code, 0)
            self.assertEqual(rep["keys"]["store.phone"]["state"], "certified-owner")

    def test_cli_certify_evidence_inspect_apply_roundtrip_raw_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            # Setup raw evidence
            raw_dir = vault / ".raw" / "captured"
            raw_dir.mkdir(parents=True)
            raw_data = b"INVOICE #9981 TOTAL $120.00"
            raw_hex = _sha256(raw_data)
            (raw_dir / f"{raw_hex}.txt").write_bytes(raw_data)

            # Setup note evidence
            inspection = wiki / "inspection.md"
            inspection.write_text("# Inspection Record\nGrade A passed on Monday.\n", encoding="utf-8")

            # Entity with draft facts
            target = wiki / "bakery.md"
            target.write_text(
                "---\n"
                "entity_id: bakery\n"
                "fact.total: 120.00\n"
                f"evidence.total: raw:sha256:{raw_hex}\n"
                "fact.grade: A\n"
                "evidence.grade: note:[[wiki/inspection]]#Inspection Record\n"
                "---\n"
                "# Bakery\n",
                encoding="utf-8",
            )

            # Certify raw evidence via CLI
            code1 = cli.main([
                "ontology",
                "certify",
                "--vault",
                str(vault),
                "--class",
                "evidence",
                "--keys",
                "bakery.total",
                "--generated-at",
                "2026-11-14T12:00:00Z",
                "--operation-id",
                "cert-ev-raw",
            ])
            self.assertEqual(code1, 0)

            bundle_file1 = vault / ".vault-meta" / "ontology" / "bundles" / "bundle-certify-cert-ev-raw.json"
            bundle1 = json.loads(bundle_file1.read_text(encoding="utf-8"))
            self.assertNotIn("certification", bundle1)
            plan1 = tx.inspect_bundle(vault, bundle1)
            self.assertTrue(plan1["valid"])
            tx.apply_bundle(vault, bundle1, approved_plan_sha256=plan1["approval_sha256"])

            # Certify note evidence via CLI
            code2 = cli.main([
                "ontology",
                "certify",
                "--vault",
                str(vault),
                "--class",
                "evidence",
                "--keys",
                "bakery.grade",
                "--generated-at",
                "2026-11-14T12:00:00Z",
                "--operation-id",
                "cert-ev-note",
            ])
            self.assertEqual(code2, 0)

            bundle_file2 = vault / ".vault-meta" / "ontology" / "bundles" / "bundle-certify-cert-ev-note.json"
            bundle2 = json.loads(bundle_file2.read_text(encoding="utf-8"))
            plan2 = tx.inspect_bundle(vault, bundle2)
            self.assertTrue(plan2["valid"])
            tx.apply_bundle(vault, bundle2, approved_plan_sha256=plan2["approval_sha256"])

            # Verify need_keys reports certified-evidence
            rep, n_code = ont.need_keys(vault, keys=["bakery.total", "bakery.grade"])
            self.assertEqual(n_code, 0)
            self.assertEqual(rep["keys"]["bakery.total"]["state"], "certified-evidence")
            self.assertEqual(rep["keys"]["bakery.grade"]["state"], "certified-evidence")

    def test_cli_error_codes_exit_20(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            # Owner certification missing digest -> exit 20
            code = cli.main([
                "ontology",
                "certify",
                "--vault",
                str(vault),
                "--class",
                "owner",
                "--keys",
                "missing.key",
                "--generated-at",
                "2026-11-14T12:00:00Z",
            ])
            self.assertEqual(code, 20)

    def test_v040_council_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            # Configured shared-root style roots
            (vault / ".claude-empire.json").write_text(
                json.dumps({
                    "ontology": {
                        "roots": [
                            "wiki/",
                            "Projects/Active/",
                            "Workspaces/Clients/Companies/",
                            "Workspaces/Clients/People/",
                            "Workspaces/Brands & Sites/",
                        ]
                    }
                }),
                encoding="utf-8",
            )
            (vault / "Projects" / "Active").mkdir(parents=True)
            (vault / "Workspaces" / "Clients" / "Companies").mkdir(parents=True)
            (vault / "Workspaces" / "Clients" / "People").mkdir(parents=True)
            (vault / "Workspaces" / "Brands & Sites").mkdir(parents=True)
            (vault / "wiki").mkdir(parents=True)

            # R1: hyphenated 32+ char project name & path must not trigger high-entropy-token
            self.assertIsNone(ont.detect_secret_like("Northwind-Business-Intelligence-Service"))
            self.assertIsNone(
                ont.detect_secret_like(
                    "path:Projects/Active/Northwind Kitchen/deliverables/Northwind-Kitchen-Unabridged-Operating-Plan-Printable.pdf",
                    is_fact_value=True,
                )
            )
            self.assertEqual(
                ont.detect_secret_like("ghp_" + "1234567890abcdef1234567890abcdef1234"),
                "github-token",
            )
            self.assertEqual(
                ont.detect_secret_like("aB3dEf9GhIjKlMnOpQrStUvWxYz012345678"),
                "high-entropy-token",
            )

            # #1: propose_bundles rule table, hyphen-boundary truncation, skip non-block-list aliases
            p1 = vault / "Projects" / "Active" / "Northwind-Business-Intelligence-Service"
            p1.mkdir(parents=True)
            (p1 / "context.md").write_text(
                "---\ntype: project-context\n---\n# Context — Projects/Active/Northwind-Business-Intelligence-Service\n",
                encoding="utf-8",
            )
            # pm-project card in same folder must NOT be proposed as second entity
            (p1 / "Northwind-Business-Intelligence-Service.md").write_text(
                "---\ntype: pm-project\n---\n# Card\n", encoding="utf-8"
            )
            # Note with flow-list aliases -> recorded in skipped as aliases_not_block_list
            p2 = vault / "Projects" / "Active" / "Flow-List-Project"
            p2.mkdir(parents=True)
            (p2 / "context.md").write_text(
                "---\naliases: [Flowy, \"Flow Two\"]\n---\n# Flow\n", encoding="utf-8"
            )
            # Non-UTF-8 note -> skipped as body_not_utf8
            p3 = vault / "Projects" / "Active" / "Binary-Project"
            p3.mkdir(parents=True)
            (p3 / "context.md").write_bytes(b"# Invalid UTF-8 \xff\xfe\n")
            # Unterminated frontmatter note -> skipped as frontmatter_unterminated
            p4 = vault / "Projects" / "Active" / "Unterminated-Project"
            p4.mkdir(parents=True)
            (p4 / "context.md").write_text("---\ntitle: Unterminated\n# Body\n", encoding="utf-8")
            # Note with secret-like title -> skipped as invariant_violation, alias redacted
            p5 = vault / "Projects" / "Active" / "Secret-Title-Project"
            p5.mkdir(parents=True)
            secret_tok = "ghp_" + "1234567890abcdef1234567890abcdef1234"
            (p5 / "context.md").write_text(
                f"---\ntitle: {secret_tok}\n---\n# Secret\n",
                encoding="utf-8",
            )
            # Note without frontmatter whose first newline is LF followed later by CRLF
            p6 = vault / "Projects" / "Active" / "Mixed-Body-Project"
            p6.mkdir(parents=True)
            (p6 / "context.md").write_bytes(b"# Mixed Body\nFirst line LF.\r\nSecond line CRLF.\n")

            # Company note with non-allowlisted type: company and scalar alias:
            comp = vault / "Workspaces" / "Clients" / "Companies" / "Acme.md"
            comp.write_text(
                "---\nentity_id: biz-acme\ntype: \"company\"\nalias: Acme Corp\nfact.tax_state: TX\ncertified_owner:\n  - fact.tax_state\ncertified_on: 2026-11-14\nreview_after: 2027-02-14\n---\n# Acme\n",
                encoding="utf-8",
            )

            details: dict = {}
            bundles, paths, approvals, code = ont.propose_bundles(
                vault,
                generated_at="2026-11-15T12:00:00Z",
                roots=["Projects/Active"],
                details=details,
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(bundles), 1)
            writes = bundles[0]["writes"]
            self.assertEqual(len(writes), 2)
            write_paths = [w["path"] for w in writes]
            self.assertIn(
                "Projects/Active/Northwind-Business-Intelligence-Service/context.md",
                write_paths,
            )
            self.assertIn("Projects/Active/Mixed-Body-Project/context.md", write_paths)
            w0 = [w for w in writes if "Northwind" in w["path"]][0]
            self.assertIn("entity_id: proj-northwind-business-intelligence-service", w0["content"])
            self.assertIn("entity_type: project", w0["content"])
            self.assertIn('"Northwind-Business-Intelligence-Service"', w0["content"])
            skip_reasons = {s["path"]: s["reason"] for s in details["skipped"]}
            self.assertEqual(
                skip_reasons.get("Projects/Active/Flow-List-Project/context.md"),
                "aliases_not_block_list",
            )
            self.assertEqual(
                skip_reasons.get("Projects/Active/Binary-Project/context.md"),
                "body_not_utf8",
            )
            self.assertEqual(
                skip_reasons.get("Projects/Active/Unterminated-Project/context.md"),
                "frontmatter_unterminated",
            )
            self.assertEqual(
                skip_reasons.get("Projects/Active/Secret-Title-Project/context.md"),
                "invariant_violation",
            )
            for sa in details["skipped_aliases"]:
                self.assertNotIn(secret_tok, json.dumps(sa))
                if sa["path"] == "Projects/Active/Secret-Title-Project/context.md":
                    self.assertEqual(sa["alias"], "<redacted>")

            # #2: CLI transaction inspect/apply with --bundle flag
            self.assertEqual(
                cli.main(["transaction", "inspect", "--vault", str(vault), "--bundle", str(paths[0])]),
                0,
            )
            self.assertEqual(
                cli.main([
                    "transaction",
                    "apply",
                    "--vault",
                    str(vault),
                    "--bundle",
                    str(paths[0]),
                    "--approved-plan-sha256",
                    approvals[0],
                ]),
                0,
            )

            # Client triplet company note & look-alike brand alias -> id_only proposal
            triplet_co = vault / "Workspaces" / "Clients" / "Companies" / "Mixed-Body-Project.md"
            triplet_co.write_text("# Mixed-Body-Project\n", encoding="utf-8")
            brand_dir = vault / "Workspaces" / "Brands & Sites" / "acme-corp"
            brand_dir.mkdir(parents=True)
            (brand_dir / "brand.md").write_text("# acme-corp\n", encoding="utf-8")
            triplet_details: dict = {}
            t_bundles, _, _, t_code = ont.propose_bundles(
                vault,
                generated_at="2026-11-15T12:00:00Z",
                roots=["Workspaces/Clients/Companies", "Workspaces/Brands & Sites"],
                details=triplet_details,
            )
            self.assertEqual(t_code, 0)
            self.assertEqual(len(t_bundles), 1)
            t_writes = {w["path"]: w["content"] for w in t_bundles[0]["writes"]}
            self.assertIn("Workspaces/Clients/Companies/Mixed-Body-Project.md", t_writes)
            self.assertIn("entity_id: biz-mixed-body-project", t_writes["Workspaces/Clients/Companies/Mixed-Body-Project.md"])
            self.assertNotIn("aliases:", t_writes["Workspaces/Clients/Companies/Mixed-Body-Project.md"])
            self.assertIn("Workspaces/Brands & Sites/acme-corp/brand.md", t_writes)
            self.assertIn("entity_id: brand-acme-corp", t_writes["Workspaces/Brands & Sites/acme-corp/brand.md"])
            self.assertNotIn("aliases:", t_writes["Workspaces/Brands & Sites/acme-corp/brand.md"])
            id_only_entries = [
                sa for sa in triplet_details["skipped_aliases"] if sa.get("reason") == "id_only"
            ]
            self.assertEqual(len(id_only_entries), 2)

            # #3, R3, R4, R6, R7: index, resolve (case-insensitive + --term), check --as-of, need_keys --as-of
            idx, idx_code = ont.build_index(vault, as_of="2026-11-15", write_file=True)
            self.assertEqual(idx_code, 0)
            self.assertEqual(idx["entities"]["biz-acme"]["type"], "company")
            self.assertIn("Acme Corp", idx["entities"]["biz-acme"]["aliases"])
            self.assertEqual(
                idx["entities"]["proj-northwind-business-intelligence-service"]["type"],
                "project",
            )

            # R4: check_index with different as_of returns 14
            _, chk_same = ont.check_index(vault, as_of="2026-11-15")
            self.assertEqual(chk_same, 0)
            _, chk_diff = ont.check_index(vault, as_of="2027-06-01")
            self.assertEqual(chk_diff, 14)

            # R3 & #2: resolve case-insensitive entity_id and --term flag
            res_doc, res_code = ont.resolve_term(vault, "BIZ-ACME", index_doc=idx)
            self.assertEqual(res_code, 0)
            self.assertEqual(res_doc["entity_id"], "biz-acme")
            self.assertEqual(
                cli.main([
                    "ontology",
                    "resolve",
                    "--vault",
                    str(vault),
                    "--term",
                    "Northwind-Business-Intelligence-Service",
                    "--as-of",
                    "2026-11-15",
                ]),
                0,
            )

            # R7: need_keys honors explicit as_of even when index_doc is supplied
            need_fresh, nf_code = ont.need_keys(
                vault, keys=["biz-acme.tax_state"], as_of="2026-12-01", index_doc=idx
            )
            self.assertEqual(nf_code, 0)
            need_stale, ns_code = ont.need_keys(
                vault, keys=["biz-acme.tax_state"], as_of="2027-06-01", index_doc=idx
            )
            self.assertEqual(ns_code, 13)
            self.assertEqual(need_stale["keys"]["biz-acme.tax_state"]["state"], "stale")

            # R2: ask_question stopwords and whole-token matching
            ask_doc, ask_code = ont.ask_question(
                vault, "What is the status of the thing in TX?", index_doc=idx
            )
            self.assertEqual(ask_code, 0)
            self.assertNotIn("proj-northwind-business-intelligence-service", ask_doc["matched_entities"])

            # D2-3 & Opus #3: certify --secrets-root checks file and variable existence and fails closed when secrets_root=None
            sec_dir = vault / "secrets_test"
            sec_dir.mkdir()
            (sec_dir / "valid.env").write_text("API_KEY=dummy\n", encoding="utf-8")
            comp.write_text(
                "---\nentity_id: biz-acme\nfact.good_ref: secret-ref:valid.env#API_KEY\nfact.bad_ref: secret-ref:missing.env#API_KEY\n---\n# Acme\n",
                encoding="utf-8",
            )
            digest = vault / "wiki" / "d.md"
            digest.write_text("# Digest\n", encoding="utf-8")
            with self.assertRaises(ont.OntologyRuleError) as ctx_unverified:
                ont.certify_bundle(
                    vault,
                    keys=["biz-acme.good_ref"],
                    cert_class="owner",
                    generated_at="2026-11-15T12:00:00Z",
                    digest="wiki/d.md",
                    secrets_root=None,
                )
            self.assertEqual(ctx_unverified.exception.code, "ONTOLOGY_SECRET_REF_UNVERIFIED")
            with self.assertRaises(ont.OntologyRuleError):
                ont.certify_bundle(
                    vault,
                    keys=["biz-acme.bad_ref"],
                    cert_class="owner",
                    generated_at="2026-11-15T12:00:00Z",
                    digest="wiki/d.md",
                    secrets_root=sec_dir,
                )
            _, _, _, cert_ok = ont.certify_bundle(
                vault,
                keys=["biz-acme.good_ref"],
                cert_class="owner",
                generated_at="2026-11-15T12:00:00Z",
                digest="wiki/d.md",
                secrets_root=sec_dir,
            )
            self.assertEqual(cert_ok, 0)

            # #4: native_owner_mismatch skip in propose_bundles does not abort batch
            p_owner_ok = vault / "Projects" / "Active" / "Owner-Ok-Project"
            p_owner_ok.mkdir(parents=True)
            (p_owner_ok / "context.md").write_text("# Owner Ok\n", encoding="utf-8")
            p_owner_bad = vault / "Projects" / "Active" / "Owner-Mismatch-Project"
            p_owner_bad.mkdir(parents=True)
            (p_owner_bad / "context.md").write_text("# Owner Bad\n", encoding="utf-8")
            orig_sec = tx._safe_native_security

            def _mock_sec(v_root: Path, rel_p: str, *args: object, **kwargs: object) -> None:
                if "Owner-Mismatch-Project" in rel_p:
                    raise tx.TransactionValidationError("UNSUPPORTED_SECURITY_STATE: owner mismatch")
                return orig_sec(v_root, rel_p, *args, **kwargs)

            owner_details: dict = {}
            with unittest.mock.patch.object(tx, "_safe_native_security", side_effect=_mock_sec):
                o_bundles, _, _, o_code = ont.propose_bundles(
                    vault,
                    generated_at="2026-11-15T12:00:00Z",
                    roots=["Projects/Active"],
                    details=owner_details,
                )
            self.assertEqual(o_code, 0)
            self.assertEqual(len(o_bundles), 1)
            o_paths = [w["path"] for w in o_bundles[0]["writes"]]
            self.assertEqual(o_paths, ["Projects/Active/Owner-Ok-Project/context.md"])
            o_skip = {s["path"]: s["reason"] for s in owner_details["skipped"]}
            self.assertEqual(
                o_skip.get("Projects/Active/Owner-Mismatch-Project/context.md"),
                "native_owner_mismatch",
            )


if __name__ == "__main__":
    unittest.main()
