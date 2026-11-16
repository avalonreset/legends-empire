#!/usr/bin/env python3
"""Hermetic tests for ontology integration in lint_engine and legends_vault_steward."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire.lint_engine import lint_vault, render_markdown
from legends_vault_steward.parse import parse_frontmatter, has_nested_frontmatter


class LintOntologyTests(unittest.TestCase):
    def test_has_nested_frontmatter_detection(self) -> None:
        # Regular frontmatter
        text_normal = "---\nentity_id: proj-foo\naliases: [Foo]\n---\n# Body\n"
        self.assertFalse(has_nested_frontmatter(text_normal))

        # Nested frontmatter inside scalar
        text_nested = "---\ndescription: |\n  ---\n  nested: true\n  ---\n---\n# Body\n"
        self.assertTrue(has_nested_frontmatter(text_nested))

        parsed_fm, _ = parse_frontmatter(text_nested)
        self.assertIsInstance(parsed_fm, dict)

    def test_lint_ontology_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / ".obsidian").mkdir(parents=True)
            (vault / "wiki").mkdir(parents=True)

            # File 1: duplicate entity and missing certified value
            f1 = vault / "wiki" / "dup1.md"
            f1.write_text(
                "---\n"
                "title: Dup 1\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-07-12\n"
                "updated: 2026-07-12\n"
                "tags: [test]\n"
                "entity_id: biz-acme\n"
                "certified_owner:\n"
                "  - missing_fact\n"
                "---\n"
                "# Acme 1\n",
                encoding="utf-8",
            )

            f2 = vault / "wiki" / "dup2.md"
            f2.write_text(
                "---\n"
                "title: Dup 2\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-07-12\n"
                "updated: 2026-07-12\n"
                "tags: [test]\n"
                "entity_id: biz-acme\n"
                "---\n"
                "# Acme 2\n",
                encoding="utf-8",
            )

            # File 3: nested frontmatter
            f3 = vault / "wiki" / "nested.md"
            f3.write_text(
                "---\n"
                "title: Nested\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-07-12\n"
                "updated: 2026-07-12\n"
                "tags: [test]\n"
                "entity_id: biz-nested\n"
                "notes: |\n"
                "  ---\n"
                "  nested: true\n"
                "  ---\n"
                "---\n"
                "# Nested\n",
                encoding="utf-8",
            )

            report = lint_vault(vault)
            self.assertIn("ontology_findings", report)
            finding_codes = [f["code"] for f in report["ontology_findings"]]

            self.assertIn("ontology_duplicate_entity_id", finding_codes)
            self.assertIn("ontology_certified_without_value", finding_codes)
            self.assertIn("frontmatter_nested_unsupported", finding_codes)

            # Verify markdown render includes ontology findings
            md = render_markdown(report)
            self.assertIn("ontology_duplicate_entity_id", md)
            self.assertIn("frontmatter_nested_unsupported", md)

            # Verify exclude scopes out ontology findings matching excluded paths
            report_excluded = lint_vault(vault, exclude=["wiki/nested.md"])
            excluded_codes = [f["code"] for f in report_excluded["ontology_findings"]]
            self.assertIn("ontology_duplicate_entity_id", excluded_codes)
            self.assertNotIn("frontmatter_nested_unsupported", excluded_codes)

            # Verify ImportError fallback keeps lint running cleanly without configuration_errors
            import claude_empire

            with mock.patch.dict(
                sys.modules, {"claude_empire.ontology": None}
            ), mock.patch.object(claude_empire, "ontology", None, create=True):
                import builtins

                real_import = builtins.__import__

                def _block_ontology(
                    name: str,
                    globals: object = None,
                    locals: object = None,
                    fromlist: tuple[str, ...] = (),
                    level: int = 0,
                ) -> object:
                    if "ontology" in name or (fromlist and "ontology" in fromlist):
                        raise ImportError("ontology unavailable")
                    return real_import(name, globals, locals, fromlist, level)  # type: ignore[arg-type]

                with mock.patch("builtins.__import__", side_effect=_block_ontology):
                    report_no_ont = lint_vault(vault)
            self.assertEqual(report_no_ont["ontology_findings"], [])
            self.assertEqual(report_no_ont["configuration_errors"], [])


if __name__ == "__main__":
    unittest.main()
