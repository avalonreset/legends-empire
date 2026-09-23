"""Public Legends distribution contract, executed by make test."""
import json
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

class PublicSurfaceTests(unittest.TestCase):
    def test_marketplace_routes_to_public_distribution(self):
        manifest = json.loads((ROOT / "config/public-marketplace.json").read_text())
        plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        self.assertEqual("legends-obsidian", manifest["name"])
        self.assertEqual(plugin["name"], manifest["plugins"][0]["name"])
        self.assertEqual("https://github.com/avalonreset/legends-obsidian", plugin["repository"])

    def test_required_knowledge_contracts_exist(self):
        for relative in ("WIKI.md", "agents/wiki-ingest.md", "agents/wiki-lint.md",
                         "docs/RESEARCH-EVIDENCE-HANDOFF.md"):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_router_selects_vault_instead_of_house_paths(self):
        text = (ROOT / "skills/legends-obsidian/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("selected", text)
        self.assertNotIn("E:" + chr(92), text)
        self.assertIn("WSL", text)

if __name__ == "__main__":
    unittest.main()
