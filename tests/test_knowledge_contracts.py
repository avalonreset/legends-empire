#!/usr/bin/env python3
"""Static contracts for skill routing, trust, and methodology templates."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_empire.lint_engine import lint_vault


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise AssertionError(f"missing frontmatter: {path}")
    return parts[1]


def _description(path: Path) -> str:
    match = re.search(r"(?m)^description:\s*(.+)$", _frontmatter(path))
    if not match:
        raise AssertionError(f"missing one-line description: {path}")
    return match.group(1).strip().strip('"').casefold()


class KnowledgeContractTests(unittest.TestCase):
    def test_persistent_content_has_an_explicit_trust_boundary(self) -> None:
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8").casefold()
        self.assertIn("content trust hierarchy", security)
        self.assertIn("untrusted content", security)

        removed_consumers = (
            "skills/save/SKILL.md",
            "skills/wiki-ingest/SKILL.md",
            "skills/autoresearch/SKILL.md",
            "skills/wiki-query/SKILL.md",
            "agents/wiki-ingest.md",
        )
        for relative in removed_consumers:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_ambiguous_skill_routes_are_narrowed(self) -> None:
        skills = ROOT / "skills"
        self.assertEqual(
            ["cto-legends"], sorted(path.name for path in skills.iterdir())
        )
        description = _description(skills / "cto-legends" / "SKILL.md")
        self.assertIn("without per-module skills", description)

    def test_methodology_templates_ship_no_per_module_copies(self) -> None:
        self.assertFalse((ROOT / "skills/wiki-mode/templates").exists())

    def test_reference_and_navigation_policies_are_routed(self) -> None:
        schema = (ROOT / "WIKI.md").read_text(encoding="utf-8")
        self.assertIn("active catalog or MOC", schema)

    def test_capability_scopes_match_skill_boundaries(self) -> None:
        document = json.loads(
            (ROOT / "config/capabilities.json").read_text(encoding="utf-8")
        )
        capabilities = {item["id"]: item for item in document["capabilities"]}
        self.assertEqual({"cto-legends"}, set(capabilities))
        router = capabilities["cto-legends"]
        self.assertEqual(["plugin:skills/cto-legends/**"], router["read_scope"])
        self.assertEqual([], router["write_scope"])
        reason = router["verification_reason"].casefold()
        self.assertIn("no automated", reason)

    def test_release_excludes_host_only_root_claude_context(self) -> None:
        allowlist = json.loads(
            (ROOT / "config/release-allowlist.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("CLAUDE.md", allowlist["include_files"])
        self.assertFalse((ROOT / "CLAUDE.md").exists())
        self.assertFalse((ROOT / ".claude-plugin").exists())

    def test_per_module_agent_definitions_are_removed(self) -> None:
        self.assertFalse((ROOT / "agents").exists())

    def test_forward_evaluation_corpus_is_complete_and_lint_clean(self) -> None:
        corpus = json.loads(
            (ROOT / "tests/fixtures/forward/scenarios.json").read_text(encoding="utf-8")
        )
        self.assertEqual("claude-empire.forward-evaluations.v1", corpus["schema"])
        scenarios = {item["skill"]: item for item in corpus["scenarios"]}
        self.assertEqual({"save", "wiki-ingest", "wiki-query"}, set(scenarios))
        for scenario in scenarios.values():
            self.assertIn("hostile_quote_not_executed", scenario["invariants"])
            self.assertTrue(scenario["prompt"].strip())

        contract = json.loads(
            (ROOT / "config/product-contract.json").read_text(encoding="utf-8")
        )
        gates = {item["id"]: item for item in contract["release_gates"]}
        self.assertTrue(gates["core-workflow-forward-tests"]["required"])

        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory) / "vault"
            shutil.copytree(ROOT / "examples/sample-vault", vault)
            shutil.copy2(
                ROOT / "tests/fixtures/forward/query-evidence.md",
                vault / "wiki/sources/Aster batch policy.md",
            )
            shutil.copy2(
                ROOT / "tests/fixtures/forward/query-index.md",
                vault / "wiki/index.md",
            )
            report = lint_vault(vault, as_of=date(2026, 7, 12))
            self.assertEqual(0, report["summary"]["issues_found"], report)


if __name__ == "__main__":
    unittest.main()
