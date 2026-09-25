"""Router-native module surface contract, executed by make test.

Mirrors .github/workflows/contract.yml: skills/ holds only the pinned
cto-legends vendor copy, the module identity files and README agent block
are present, and the forbidden per-module skill registration set is absent.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_ID = "legends-empire"


class RouterNativeSurfaceTests(unittest.TestCase):
    def test_skills_holds_only_the_pinned_router_copy(self):
        skills = ROOT / "skills"
        self.assertTrue(skills.is_dir(), "skills/")
        entries = sorted(path.name for path in skills.iterdir())
        self.assertEqual(["cto-legends"], entries)
        vendored = skills / "cto-legends" / "SKILL.md"
        self.assertTrue(vendored.is_file(), str(vendored))
        frontmatter = vendored.read_text(encoding="utf-8")
        self.assertTrue(frontmatter.startswith("---\n"), "router skill frontmatter")
        match = re.search(r"(?m)^name:\s*(\S+)", frontmatter)
        self.assertIsNotNone(match, "router skill name")
        assert match is not None
        self.assertEqual("cto-legends", match.group(1))

    def test_module_identity_files_are_present(self):
        module = (ROOT / ".legends-module").read_text(encoding="utf-8").strip()
        self.assertEqual(MODULE_ID, module)
        pin = (ROOT / ".legends-router-pin").read_text(encoding="utf-8").strip()
        self.assertRegex(pin, r"^[0-9a-f]{40}$")

    def test_readme_carries_the_router_agent_block(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Agent setup (via `cto-legends`)", readme)
        self.assertIn("skills/cto-legends/SKILL.md", readme)
        self.assertIn(f"cto-legends install {MODULE_ID}", readme)

    def test_forbidden_skill_registrations_are_absent(self):
        forbidden = [
            f"skills/{MODULE_ID}",
            "CLAUDE.md",
            "GEMINI.md",
            "CODEX.md",
            "GROK.md",
            "LEGENDS.md",
            "gemini-extension.json",
            "skill-package.json",
            "SKILL.md",
            "github/SKILL.md",
            ".claude-plugin",
            "agents",
            "bin/setup-multi-agent.ps1",
            "bin/setup-multi-agent.sh",
            "bin/setup-multi-agent",
            "bin/install-spine.ps1",
            "bin/install-spine.sh",
            "scripts/setup-multi-agent.sh",
            "scripts/setup-multi-agent.ps1",
            "scripts/mirror-agent-skills.ps1",
            "install-codex.ps1",
            "install-codex.sh",
        ]
        hits = [f for f in forbidden if (ROOT / f).exists()]
        for mirror in (".agents/skills", ".claude/skills"):
            mirror_path = ROOT / mirror
            if mirror_path.is_dir() and any(mirror_path.iterdir()):
                hits.append(mirror + "/")
        self.assertEqual([], hits)

    def test_no_ide_rule_dispatchers_remain(self):
        markers = re.compile(
            r"SKILL\.md|setup-multi-agent|install-spine|skills/[A-Za-z0-9_.-]+/",
            re.IGNORECASE,
        )
        dispatchers = []
        for rules_dir in (".cursor/rules", ".windsurf/rules", ".codex", ".gemini"):
            root = ROOT / rules_dir
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        text = path.read_text(encoding="utf-8", errors="replace")
                        text = re.sub(r"cto-legends", "", text, flags=re.IGNORECASE)
                        if markers.search(text):
                            dispatchers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], dispatchers)

    def test_public_docs_use_portable_style(self):
        for relative in ("README.md", "docs/install-guide.md"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn(chr(0x2014), text, relative)


if __name__ == "__main__":
    unittest.main()
