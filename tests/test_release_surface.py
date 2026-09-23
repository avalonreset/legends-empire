"""Public Legends distribution contract, executed by make test."""
import json
import os
import shutil
import subprocess
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

    def test_public_writing_style_and_six_host_contract(self):
        router = (ROOT / "skills/legends-obsidian/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not use em dashes", router)
        for relative in ("README.md", "docs/AGENTS-MATRIX.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for host in ("Grok", "Codex", "Gemini", "Claude", "Cursor", "MetaMuse"):
                self.assertIn(host, text, (relative, host))
        for directory in ("skills", "docs", "agents", "templates"):
            for path in (ROOT / directory).rglob("*.md"):
                self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"), str(path))

    @unittest.skipUnless(os.name == "nt" and shutil.which("pwsh"), "native Windows PowerShell smoke")
    def test_windows_wrapper_does_not_depend_on_os_environment(self):
        environment = dict(os.environ)
        environment.pop("OS", None)
        result = subprocess.run(["pwsh", "-NoProfile", "-File",
            str(ROOT / "scripts/setup-multi-agent.ps1"), "--apply"],
            env=environment, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("Native Windows: no host paths changed.", result.stdout)
        self.assertIn("MetaMuse", result.stdout)

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
