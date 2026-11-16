#!/usr/bin/env python3
"""Hermetic regression tests for v0.4.1 ontology fixes (F10/R10, F11/F12/R11, F13/F14/F15/R12, F17)."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import cli
from claude_empire import ontology as ont
from claude_empire import ontology_grammar as og
from claude_empire import transaction as tx


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Ontology041FixesTests(unittest.TestCase):
    def test_f10_r10_certify_owner_with_existing_certified_evidence_and_confined_digest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki" / "entities"
            wiki.mkdir(parents=True)
            raw_dir = vault / ".raw" / "captured"
            raw_dir.mkdir(parents=True)

            raw_bytes = b"CONTRACT TIER: ENTERPRISE"
            raw_hex = _sha256(raw_bytes)
            raw_rel = f".raw/captured/{raw_hex}.txt"
            (vault / raw_rel).write_bytes(raw_bytes)

            note_ev = vault / "wiki" / "audit-log.md"
            note_ev.write_text("# Audit Verification\nConfirmed tier.\n", encoding="utf-8")

            entity_note = wiki / "acme-corp.md"
            entity_note.write_text(
                f"---\n"
                f"entity_id: acme-corp\n"
                f"fact.tier: enterprise\n"
                f"evidence.tier: raw:sha256:{raw_hex}\n"
                f"fact.audit: passed\n"
                f"evidence.audit: note:[[wiki/audit-log]]#Audit Verification\n"
                f"fact.owner: Alice\n"
                f"certified_evidence:\n"
                f"  - fact.tier\n"
                f"  - fact.audit\n"
                f"certified_on: 2026-11-15\n"
                f"---\n"
                f"# Acme Corp\n",
                encoding="utf-8",
            )

            digest_note = vault / "wiki" / "owner-digest.md"
            digest_note.write_text("# Owner Digest\nApproved owner fact.\n", encoding="utf-8")

            # Certify owner on a note that already has certified_evidence (raw: and note:)
            bundle, b_path, approval, code = ont.certify_bundle(
                vault,
                keys=["acme-corp.owner"],
                cert_class="owner",
                operation_id="cert-owner-on-ev-note",
                generated_at="2026-11-16T12:00:00Z",
                digest="wiki/owner-digest.md",
            )
            self.assertEqual(code, 0)
            self.assertIn("wiki/owner-digest.md", bundle["read_preconditions"])
            self.assertIn(raw_rel, bundle["read_preconditions"])
            self.assertIn("wiki/audit-log.md", bundle["read_preconditions"])
            self.assertNotIn("wiki/entities/acme-corp.md", bundle["read_preconditions"])

            # Apply transaction bundle and verify both certified_owner and certified_evidence survive
            plan = tx.inspect_bundle(vault, bundle)
            self.assertTrue(plan["valid"])
            res = tx.apply_bundle(vault, bundle, approved_plan_sha256=approval)
            self.assertEqual(res["status"], "complete")

            rep, n_code = ont.need_keys(vault, keys=["acme-corp.tier", "acme-corp.audit", "acme-corp.owner"])
            self.assertEqual(n_code, 0)
            self.assertEqual(rep["keys"]["acme-corp.tier"]["state"], "certified-evidence")
            self.assertEqual(rep["keys"]["acme-corp.audit"]["state"], "certified-evidence")
            self.assertEqual(rep["keys"]["acme-corp.owner"]["state"], "certified-owner")

            # Path traversal in --digest must fail closed
            with self.assertRaises(ont.OntologyRuleError) as ctx:
                ont.certify_bundle(
                    vault,
                    keys=["acme-corp.owner"],
                    cert_class="owner",
                    operation_id="cert-bad-digest",
                    generated_at="2026-11-16T12:00:00Z",
                    digest="../outside.md",
                )
            self.assertEqual(ctx.exception.code, "ONTOLOGY_CERTIFICATION_INVALID")

    def test_f11_f12_r11_profile_json_and_flat_ontology_roots_normalizer(self) -> None:
        # 1. Standalone home-adapter prepare + scaffold works with ontology index & propose
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "wiki").mkdir(parents=True)

            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(
                    cli.main([
                        "home-adapter",
                        "prepare",
                        "--vault",
                        str(vault),
                        "--operation-id",
                        "prep-1",
                        "--generated-at",
                        "2026-11-16T12:00:00Z",
                    ]),
                    0,
                )
            prep_sha = json.loads(buf.getvalue())["approved_plan_sha256"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    cli.main([
                        "home-adapter",
                        "prepare",
                        "--vault",
                        str(vault),
                        "--operation-id",
                        "prep-1",
                        "--generated-at",
                        "2026-11-16T12:00:00Z",
                        "--apply",
                        "--approved-plan-sha256",
                        prep_sha,
                    ]),
                    0,
                )

            spec_file = Path(td) / "scaffold-spec.json"
            spec_file.write_text(
                json.dumps({
                    "schema": "legends.home-records/v1",
                    "project_id": "proj-alpha",
                    "title": "Project Alpha",
                    "description": "Launch alpha",
                    "kind": "marketing",
                    "company_id": "acme-biz",
                    "brand_id": "acme-brand",
                }),
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(
                    cli.main([
                        "home-adapter",
                        "scaffold",
                        "--vault",
                        str(vault),
                        "--spec",
                        str(spec_file),
                        "--operation-id",
                        "scaf-1",
                        "--generated-at",
                        "2026-11-16T12:01:00Z",
                    ]),
                    0,
                )
            scaf_sha = json.loads(buf.getvalue())["approved_plan_sha256"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    cli.main([
                        "home-adapter",
                        "scaffold",
                        "--vault",
                        str(vault),
                        "--spec",
                        str(spec_file),
                        "--operation-id",
                        "scaf-1",
                        "--generated-at",
                        "2026-11-16T12:01:00Z",
                        "--apply",
                        "--approved-plan-sha256",
                        scaf_sha,
                    ]),
                    0,
                )

            profile, roots, _ = ont.get_ontology_roots(vault)
            self.assertEqual(profile, "shared-root")
            self.assertIn("Projects/Active/", roots)

            bundles, paths, approvals, code = ont.propose_bundles(
                vault,
                operation_id="prop-scaffold",
                generated_at="2026-11-16T12:02:00Z",
                roots=["Projects/Active"],
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(bundles), 1)
            plan = tx.inspect_bundle(vault, bundles[0])
            self.assertTrue(plan["valid"])

        # 2. Flat "ontology.roots" works in both ontology and transaction, and fails closed on conflict/bad types
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "wiki").mkdir(parents=True)
            (vault / "custom_notes").mkdir(parents=True)
            (vault / "custom_notes" / "item.md").write_text("# Custom Item\n", encoding="utf-8")
            (vault / ".claude-empire.json").write_text(
                json.dumps({"ontology.roots": ["wiki", "custom_notes"]}),
                encoding="utf-8",
            )
            profile, roots, _ = ont.get_ontology_roots(vault)
            self.assertEqual(profile, "configured")
            self.assertEqual(roots, ["wiki/", "custom_notes/"])

            # Conflict between flat ontology.roots and nested ontology.roots fails closed
            (vault / ".claude-empire.json").write_text(
                json.dumps({"ontology.roots": ["wiki"], "ontology": {"roots": ["wiki"]}}),
                encoding="utf-8",
            )
            with self.assertRaises(og.OntologyRuleError) as ctx:
                ont.get_ontology_roots(vault)
            self.assertEqual(ctx.exception.code, "ONTOLOGY_CONFIG_INVALID")

            # Bad type for flat ontology.roots fails closed
            (vault / ".claude-empire.json").write_text(
                json.dumps({"ontology.roots": "wiki"}),
                encoding="utf-8",
            )
            with self.assertRaises(og.OntologyRuleError) as ctx2:
                ont.get_ontology_roots(vault)
            self.assertEqual(ctx2.exception.code, "ONTOLOGY_CONFIG_INVALID")

    def test_f13_r12_resolve_need_ask_use_current_index_and_rebuild_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            note = wiki / "acme.md"
            note.write_text(
                "---\n"
                "entity_id: acme\n"
                "title: Acme Corp\n"
                "aliases:\n"
                "  - Acme\n"
                "fact.tier: gold\n"
                "certified_owner:\n"
                "  - fact.tier\n"
                "certified_on: 2026-11-16\n"
                "---\n"
                "# Acme Corp\n",
                encoding="utf-8",
            )

            idx, code = ont.build_index(vault, as_of="2026-11-16", write_file=True)
            self.assertEqual(code, 0)
            self.assertIn("stat_fingerprint", idx)

            # When index.json is current, resolve/need/ask must NOT call build_index
            with mock.patch("claude_empire.ontology.build_index", wraps=ont.build_index) as spy_build:
                res, r_code = ont.resolve_term(vault, "Acme")
                self.assertEqual(r_code, 0)
                self.assertEqual(res["entity_id"], "acme")

                need_rep, n_code = ont.need_keys(vault, keys=["acme.tier"], as_of="2026-11-16")
                self.assertEqual(n_code, 0)
                self.assertEqual(need_rep["keys"]["acme.tier"]["value"], "gold")

                ask_rep, a_code = ont.ask_question(vault, "What is Acme tier?")
                self.assertEqual(a_code, 0)
                self.assertIn("acme", ask_rep["matched_entities"])

                self.assertEqual(spy_build.call_count, 0)

            # Modify note so stat_fingerprint changes -> stale index must be rebuilt once
            note.write_text(
                "---\n"
                "entity_id: acme\n"
                "title: Acme Corp\n"
                "aliases:\n"
                "  - Acme\n"
                "fact.tier: platinum-updated\n"
                "certified_owner:\n"
                "  - fact.tier\n"
                "certified_on: 2026-11-16\n"
                "---\n"
                "# Acme Corp\n",
                encoding="utf-8",
            )
            _, chk_code = ont.check_index(vault, as_of="2026-11-16")
            self.assertEqual(chk_code, 14)

            with mock.patch("claude_empire.ontology.build_index", wraps=ont.build_index) as spy_build2:
                need_rep2, n_code2 = ont.need_keys(vault, keys=["acme.tier"], as_of="2026-11-16")
                self.assertEqual(n_code2, 0)
                self.assertEqual(need_rep2["keys"]["acme.tier"]["value"], "platinum-updated")
                self.assertEqual(spy_build2.call_count, 1)

    def test_f14_r12_certify_note_paths_restricts_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "existing.md").write_text(
                "---\nentity_id: existing\nfact.owner: Alice\n---\n# Existing\n",
                encoding="utf-8",
            )
            (wiki / "other.md").write_text(
                "---\nentity_id: other\nfact.owner: Someone\n---\n# Other\n",
                encoding="utf-8",
            )
            (wiki / "digest.md").write_text("# Digest\n", encoding="utf-8")

            with self.assertRaises(ont.OntologyRuleError) as ctx:
                ont.certify_bundle(
                    vault,
                    keys=["existing.owner"],
                    cert_class="owner",
                    operation_id="cert-wrong-scope",
                    generated_at="2026-11-16T12:00:00Z",
                    digest="wiki/digest.md",
                    note_paths=["wiki/other.md"],
                )
            self.assertEqual(ctx.exception.code, "ONTOLOGY_TARGET_SCOPE")

            # Matching --note-paths succeeds
            bundle, _, _, code = ont.certify_bundle(
                vault,
                keys=["existing.owner"],
                cert_class="owner",
                operation_id="cert-right-scope",
                generated_at="2026-11-16T12:00:00Z",
                digest="wiki/digest.md",
                note_paths=["wiki/existing.md"],
            )
            self.assertEqual(code, 0)
            self.assertEqual(len(bundle["writes"]), 1)

    def test_f15_r12_benchmark_evaluates_expected_exit_expect_halt_and_none_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "entity.md").write_text(
                "---\n"
                "entity_id: alpha\n"
                "fact.draft_field: val1\n"
                "fact.cert_field: val2\n"
                "certified_owner:\n"
                "  - fact.cert_field\n"
                "certified_on: 2026-11-16\n"
                "---\n",
                encoding="utf-8",
            )
            tasks_file = vault / "tasks.json"
            tasks_file.write_text(
                json.dumps([
                    # Unresolved query (resolved_eid is None != "alpha") -> wrong resolution
                    {"query": "nonexistent-term", "expected_entity_id": "alpha"},
                    # Wrong expected_exit (actual is 10 for draft, expected 999) -> gate failure
                    {"need_keys": ["alpha.draft_field"], "expected_exit": 999},
                    # Correct expected_exit (actual is 10) -> gate pass
                    {"need_keys": ["alpha.draft_field"], "expected_exit": 10},
                    # expect_halt False on certified key (exit 0) -> gate pass
                    {"need_keys": ["alpha.cert_field"], "expect_halt": False},
                    # expect_halt False on draft key (exit 10 != 0) -> gate failure
                    {"need_keys": ["alpha.draft_field"], "expect_halt": False},
                ]),
                encoding="utf-8",
            )
            report = ont.run_benchmark(vault, tasks_path=tasks_file, attempts=1, as_of="2026-11-16")
            self.assertEqual(report["tasks_evaluated"], 5)
            # 1 wrong resolution out of 5 tasks = 0.2
            self.assertEqual(report["wrong_record_rate"], 0.2)
            # 2 passing gates out of 4 gate tasks = 50.0%
            self.assertEqual(report["gating_accuracy_percent"], 50.0)

    def test_f17_propose_noop_preserves_prior_bundles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            (wiki / "note.md").write_text("# Note\n", encoding="utf-8")

            # First propose creates bundle-propose-0.json
            bundles1, paths1, _, code1 = ont.propose_bundles(
                vault, operation_id="prop-1", generated_at="2026-11-16T12:00:00Z"
            )
            self.assertEqual(code1, 0)
            self.assertEqual(len(paths1), 1)
            self.assertTrue(paths1[0].is_file())

            # Apply the bundle so a second propose has nothing left to propose (noop)
            plan = tx.inspect_bundle(vault, bundles1[0])
            tx.apply_bundle(vault, bundles1[0], approved_plan_sha256=plan["approval_sha256"])

            # Second propose is noop and must NOT unlink paths1[0]
            bundles2, paths2, _, code2 = ont.propose_bundles(
                vault, operation_id="prop-2", generated_at="2026-11-16T12:05:00Z"
            )
            self.assertEqual(code2, 0)
            self.assertEqual(bundles2, [])
            self.assertEqual(paths2, [])
            self.assertTrue(paths1[0].is_file())

    def test_p4_need_without_as_of_checks_review_after_against_today_on_cached_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            wiki = vault / "wiki"
            wiki.mkdir(parents=True)
            note = wiki / "acme.md"
            note.write_text(
                "---\n"
                "entity_id: acme\n"
                "title: Acme Corp\n"
                "fact.tier: gold\n"
                "certified_owner:\n"
                "  - fact.tier\n"
                "certified_on: 2026-10-15\n"
                "review_after: 2026-11-10\n"
                "---\n"
                "# Acme Corp\n",
                encoding="utf-8",
            )

            # Build and cache index.json as of 2026-11-01 (when review_after 2026-11-10 was still fresh)
            idx, code = ont.build_index(vault, as_of="2026-11-01", write_file=True)
            self.assertEqual(code, 0)
            self.assertEqual(idx["facts"]["acme.tier"]["state"], "certified-owner")

            # Calling need_keys without --as-of must compare review_after against today's date and exit 13 (stale)
            rep, n_code = ont.need_keys(vault, keys=["acme.tier"])
            self.assertEqual(n_code, 13)
            self.assertEqual(rep["keys"]["acme.tier"]["state"], "stale")

            buf = io.StringIO()
            with redirect_stdout(buf):
                cli_code = cli.main(["ontology", "need", "--vault", str(vault), "--keys", "acme.tier"])
            self.assertEqual(cli_code, 13)
            cli_out = json.loads(buf.getvalue())
            self.assertEqual(cli_out["keys"]["acme.tier"]["state"], "stale")

    def test_rec3_in_vault_junction_guards_in_ontology_and_hook_adapter(self) -> None:
        import os as std_os
        from claude_empire.hook_adapter import _safe_directory

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            vault.mkdir()
            outside = base / "outside-dir"
            outside.mkdir()
            (outside / "index.json").write_text("{}", encoding="utf-8")

            meta_dir = vault / ".vault-meta"
            meta_dir.mkdir()
            ont_link = meta_dir / "ontology"

            if std_os.name == "nt":
                import _winapi
                _winapi.CreateJunction(str(outside), str(ont_link))
            else:
                std_os.symlink(outside, ont_link)

            self.assertFalse(_safe_directory(vault, ont_link))
            self.assertIsNone(ont._load_fresh_index(vault))


if __name__ == "__main__":
    unittest.main()
