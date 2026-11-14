#!/usr/bin/env python3
"""Hermetic unit tests for claude_empire.ontology (Spec v1.1 compliant)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import ontology as ont


class OntologyTests(unittest.TestCase):
    def test_split_frontmatter_strict_standard(self) -> None:
        content = b"---\nentity_id: acme\naliases:\n  - Acme Corp\n---\n# Acme Overview\nBody text."
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, fm_text, body = res
        self.assertFalse(has_bom)
        self.assertEqual(nl, "\n")
        self.assertIn("entity_id: acme", fm_lines)
        self.assertEqual(body, b"# Acme Overview\nBody text.")

    def test_split_frontmatter_strict_crlf_and_bom(self) -> None:
        content = b"\xef\xbb\xbf---\r\nentity_id: acme\r\n---\r\nBody text\r\n"
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, fm_text, body = res
        self.assertTrue(has_bom)
        self.assertEqual(nl, "\r\n")
        self.assertEqual(body, b"Body text\r\n")

    def test_split_frontmatter_strict_empty_frontmatter(self) -> None:
        content = b"---\n---\nBody text"
        res = ont.split_frontmatter_strict(content)
        self.assertIsNotNone(res)
        has_bom, nl, fm_lines, fm_text, body = res
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

    def test_parse_frontmatter_strict_and_nested_detection(self) -> None:
        content = b"---\nentity_id: acme\naliases: [Acme, \"The Acme\"]\nfact.team: Engineering\n---\n# Acme\n"
        data, findings, body, nl, bom, unp = ont.parse_frontmatter_strict(content, "test.md")
        self.assertFalse(unp)
        self.assertEqual(data.get("entity_id"), "acme")
        self.assertEqual(data.get("aliases"), ["Acme", "The Acme"])
        self.assertEqual(data.get("fact.team"), "Engineering")
        self.assertEqual(findings, [])

        # Nested YAML construct
        nested_content = b"---\ndescription: |\n  ---\n  nested: true\n---\n# Nested\n"
        data_n, findings_n, _, _, _, unp_n = ont.parse_frontmatter_strict(nested_content, "nested.md")
        self.assertTrue(unp_n)
        self.assertTrue(any(f["code"] == "frontmatter_nested_unsupported" for f in findings_n))

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

            idx, exit_code = ont.build_index(vault, as_of="2026-11-14", write_file=True)
            self.assertEqual(exit_code, 0)
            self.assertIn("acme", idx["entities"])
            self.assertEqual(idx["entities"]["acme"]["path"], "wiki/acme.md")

            # Check index freshness
            saved, check_code = ont.check_index(vault, as_of="2026-11-14")
            self.assertEqual(check_code, 0)
            self.assertIsNotNone(saved)

            # Resolve term: exact entity ID
            rep, code = ont.resolve_term(vault, "acme", index_doc=idx)
            self.assertEqual(code, 0)
            self.assertEqual(rep["entity_id"], "acme")

            # Resolve term: alias
            rep_a, code_a = ont.resolve_term(vault, "Acme Corp", index_doc=idx)
            self.assertEqual(code_a, 0)
            self.assertEqual(rep_a["entity_id"], "acme")

            # Resolve term: not found
            rep_nf, code_nf = ont.resolve_term(vault, "nonexistent-co", index_doc=idx)
            self.assertEqual(code_nf, 4)

            # Modify file -> index is stale (exit 14)
            (wiki / "acme.md").write_text("# Modified\n", encoding="utf-8")
            saved_stale, code_stale = ont.check_index(vault, as_of="2026-11-14")
            self.assertEqual(code_stale, 14)

    def test_need_keys_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)

            (wiki / "entity.md").write_text(
                "---\n"
                "entity_id: test-ent\n"
                "fact.certified: val1\n"
                "fact.draft: val2\n"
                "certified_owner:\n"
                "  - fact.certified\n"
                "certified_on: 2026-01-01\n"
                "review_after: 2027-01-01\n"
                "---\n",
                encoding="utf-8",
            )
            (wiki / "stale.md").write_text(
                "---\n"
                "entity_id: stale-ent\n"
                "fact.expired: val3\n"
                "certified_owner:\n"
                "  - fact.expired\n"
                "certified_on: 2024-01-01\n"
                "review_after: 2025-01-01\n"
                "---\n",
                encoding="utf-8",
            )

            idx, _ = ont.build_index(vault, as_of="2026-11-14", write_file=False)

            # Certified key -> 0
            rep, code = ont.need_keys(vault, keys=["test-ent.certified"], as_of="2026-11-14", index_doc=idx)
            self.assertEqual(code, 0)

            # Draft key -> 10
            rep_d, code_d = ont.need_keys(vault, keys=["test-ent.draft"], as_of="2026-11-14", index_doc=idx)
            self.assertEqual(code_d, 10)

            # Missing key -> 11
            rep_m, code_m = ont.need_keys(vault, keys=["test-ent.missing"], as_of="2026-11-14", index_doc=idx)
            self.assertEqual(code_m, 11)

            # Stale key -> 13
            rep_s, code_s = ont.need_keys(vault, keys=["stale-ent.expired"], as_of="2026-11-14", index_doc=idx)
            self.assertEqual(code_s, 13)

            # Conflict key across notes -> 12
            (wiki / "secondary.md").write_text(
                "---\n"
                "about: test-ent\n"
                "fact.draft: conflicting_val\n"
                "---\n",
                encoding="utf-8",
            )
            idx2, _ = ont.build_index(vault, as_of="2026-11-14", write_file=False)
            rep_c, code_c = ont.need_keys(vault, keys=["test-ent.draft"], as_of="2026-11-14", index_doc=idx2)
            self.assertEqual(code_c, 12)

            # Precedence: 12 > 11 > 13 > 10 > 0
            rep_p, code_p = ont.need_keys(vault, keys=["test-ent.draft", "test-ent.missing"], as_of="2026-11-14", index_doc=idx2)
            self.assertEqual(code_p, 12)

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
            bundles, paths, code = ont.propose_bundles(vault, operation_id="test-prop", generated_at="2026-11-14T00:00:00Z")
            self.assertEqual(code, 0)
            self.assertEqual(len(bundles), 1)
            b = bundles[0]
            self.assertEqual(b["operation_type"], "ontology")
            self.assertIn("entity_id:", b["writes"][0]["content"])

            (wiki / "indexed.md").write_text(
                "---\nentity_id: ent1\nfact.domain: example.com\n---\n", encoding="utf-8"
            )
            digest = wiki / "digest.md"
            digest.write_text("# Digest\nApproved.\n", encoding="utf-8")

            cert_b, cert_path, c_code = ont.certify_bundle(
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

    def test_run_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "note.md").write_text("---\nentity_id: b-ent\naliases: [BEnt]\n---\n", encoding="utf-8")
            res = ont.run_benchmark(vault, as_of="2026-11-14")
            self.assertEqual(res["status"], "complete")
            self.assertEqual(res["entities_indexed"], 1)
            self.assertEqual(res["wrong_record_rate"], 0.0)
            self.assertEqual(res["gating_accuracy_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
