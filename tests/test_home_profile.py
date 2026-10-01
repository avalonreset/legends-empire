"""Original, synthetic shared-root and headless materialization acceptance cases."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import home_adapter as adapter, home_profile as profile, home_install as installer
from claude_empire.transaction import TransactionError, apply_bundle, inspect_bundle, sha256_bytes

POSIX = unittest.skipIf(os.name == "nt", "writes require POSIX or WSL")
STAMP = "2026-10-01T00:00:00Z"


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "Empire"
        (self.root / "wiki").mkdir(parents=True)
        (self.root / ".raw").mkdir()
        self.source = self.base / "AuthorizedHome"
        self.files = {"AGENTS.md": b"# Invented Home contract\n",
                      "AI Team/Sessions/handoff.md": b"---\ntype: handoff\n---\n# Navigation\n",
                      ".agents/skills/aimh/SKILL.md": b"# Synthetic agent entry\n",
                      ".claude/skills/aimh/SKILL.md": b"# Synthetic Claude entry\n",
                      "_third-party/example/LICENSE": b"Synthetic attribution\n",
                      "README.md": b"# Synthetic native help\n",
                      "AI Team/Knowledge/Scripts/resolve-context.py": b"print('synthetic')\n",
                      "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md": b"# Invented method\n",
                      "_templates/Example.md": b"# Invented template\n",
                      ".obsidian/plugins/example/main.js": b"do_not_install()\n"}
        inventory = {"format": "aimh-preview-integrity-v1", "files": [
            {"path": p, "sha256": sha256_bytes(b), "bytes": len(b)} for p, b in self.files.items()]}
        self.files["PACKAGE-INTEGRITY.json"] = profile.encoded(inventory)
        for p, b in self.files.items():
            path = self.source / p
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b)
        self.pin = self.base / "pin.json"
        pin = {"release": "v0.5.2", "commit": "test-only", "files": {
            p: sha256_bytes(b) for p, b in self.files.items() if p in ("AGENTS.md", "PACKAGE-INTEGRITY.json") or p.endswith(".py")}}
        self.pin.write_bytes(profile.encoded(pin))
        patcher = patch.object(adapter, "PIN", self.pin)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.spec = self.base / "records.json"

    def apply(self, operation):
        if not operation["writes"]:
            return
        return apply_bundle(self.root, operation, approved_plan_sha256=inspect_bundle(self.root, operation)["approval_sha256"])

    def prepare(self):
        self.apply(profile.prepare_bundle(self.root, "prepare", STAMP))

    def install(self, operation="install"):
        plan, batches, completion = installer.install_plan(self.root, self.source, operation, STAMP)
        return installer.apply_install(self.root, plan, batches, completion)

    def scaffold(self, **kwargs):
        data = {"schema": "legends.home-records/v1", "project_id": "first-project", **kwargs}
        self.spec.write_bytes(profile.encoded(data))
        return profile.scaffold_bundle(self.root, self.spec, data["project_id"], STAMP)

    def test_prepare_does_not_need_home_or_invent_business(self):
        with patch.object(adapter, "PIN", self.base / "absent"):
            operation = profile.prepare_bundle(self.root, "prepare", STAMP)
        self.assertEqual(operation["operation_type"], "home-profile")
        paths = [row["path"] for row in operation["writes"]]
        self.assertIn("Projects/Completed/.empire-slot.json", paths)
        self.assertFalse(any("context.md" in p for p in paths))
        self.assertFalse(any("Archive" in p for p in paths))
        self.assertFalse((self.root / "AGENTS.md").exists())

    @POSIX
    def test_prepare_preserves_owner_and_is_idempotent(self):
        (self.root / "AGENTS.md").write_bytes(b"Owner contract\r\n")
        self.prepare()
        self.assertEqual((self.root / "AGENTS.md").read_bytes(), b"Owner contract\r\n")
        self.assertFalse(profile.prepare_bundle(self.root, "again", STAMP)["writes"])

    @POSIX
    def test_compose_preserves_exact_contracts_and_drift_refuses(self):
        (self.root / "AGENTS.md").write_bytes(b"Owner instructions\r\n")
        self.prepare()
        self.install()
        self.apply(profile.compose_bundle(self.root, self.source, "compose", STAMP))
        self.assertEqual((self.root / profile.OWNER_CONTRACT).read_bytes(), b"Owner instructions\r\n")
        self.assertEqual((self.root / profile.HOME_CONTRACT).read_bytes(), self.files["AGENTS.md"])
        self.assertEqual(adapter.inspect_home(self.root)["instruction_mode"], "managed-composition")
        self.assertFalse(profile.compose_bundle(self.root, self.source, "again", STAMP)["writes"])
        (self.root / "AGENTS.md").write_text("arbitrary drift")
        self.assertIn("AGENTS.md", adapter.inspect_home(self.root)["mismatches"])
        with self.assertRaises(TransactionError):
            profile.compose_bundle(self.root, self.source, "no", STAMP)

    @POSIX
    def test_end_to_end_shared_binding_and_detach_preserve_capabilities(self):
        self.prepare()
        result = self.install()
        self.assertEqual(result["status"], "installed")
        self.apply(profile.compose_bundle(self.root, self.source, "compose", STAMP))
        operation = adapter.attachment_bundle(self.root, self.root, None, "shared", "attach", STAMP, shared_root=True)
        self.apply(operation)
        self.assertEqual(adapter.check_binding(self.root, "shared")["native_readiness"], "not_selected")
        self.apply(adapter.detach_bundle(self.root, "shared", "detach", STAMP))
        self.assertEqual(adapter.check_binding(self.root, "shared")["compatibility"], "detached")
        self.assertTrue((self.root / "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md").exists())
        self.assertFalse((self.root / ".obsidian").exists())

    @POSIX
    def test_install_inventory_only_idempotent_and_no_untracked_data(self):
        (self.source / "Workspaces/private.md").write_text("private user data not in release")
        self.install()
        self.assertFalse((self.root / "Workspaces/private.md").exists())
        self.assertFalse((self.root / ".obsidian").exists())
        plan, batches, completion = installer.install_plan(self.root, self.source, "again", STAMP)
        self.assertEqual(batches, [])
        self.assertEqual(plan["create_files"], 0)
        self.assertEqual(installer.apply_install(self.root, plan, batches, completion)["completed_batches"], 0)

    @POSIX
    def test_mutable_handoff_preserved_but_methods_and_schema_checked(self):
        self.install()
        handoff = self.root / "AI Team/Sessions/handoff.md"
        content = b"---\ntype: handoff\nstatus: active\n---\n# Owner update\n"
        handoff.write_bytes(content)
        self.assertEqual(installer.installation_status(self.root), "headless-verified")
        self.install("repeat")
        self.assertEqual(handoff.read_bytes(), content)
        for path in (".agents/skills/aimh/SKILL.md", ".claude/skills/aimh/SKILL.md", "_third-party/example/LICENSE"):
            self.assertEqual((self.root / path).read_bytes(), self.files[path])
        handoff.write_text("invalid handoff")
        self.assertEqual(installer.installation_status(self.root), "changed-installation")
        with self.assertRaises(TransactionError):
            installer.install_plan(self.root, self.source, "invalid", STAMP)

    @POSIX
    def test_install_partial_state_resume_and_final_hashes(self):
        with patch.object(installer, "BATCH_SIZE", 1):
            plan, batches, completion = installer.install_plan(self.root, self.source, "resume", STAMP)
            self.apply(batches[0])
            self.apply(batches[1])
            self.assertEqual(installer.installation_status(self.root), "incomplete-installation")
            plan, batches, completion = installer.install_plan(self.root, self.source, "resume", STAMP)
            installer.apply_install(self.root, plan, batches, completion)
        self.assertEqual(installer.installation_status(self.root), "headless-verified")

    def test_changed_source_and_destination_refuse_before_writes(self):
        path = "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md"
        (self.source / path).write_text("unqualified changed method")
        with self.assertRaises(TransactionError):
            installer.install_plan(self.root, self.source, "bad", STAMP)
        (self.source / path).write_bytes(self.files[path])
        target = self.root / path
        target.parent.mkdir(parents=True)
        target.write_text("owner method")
        with self.assertRaises(TransactionError):
            installer.install_plan(self.root, self.source, "bad", STAMP)
        self.assertEqual(target.read_text(), "owner method")

    def test_owner_root_support_document_is_not_overwritten(self):
        target = self.root / "README.md"
        target.write_text("Existing owner help")
        with self.assertRaises(TransactionError):
            installer.install_plan(self.root, self.source, "conflict", STAMP)
        self.assertEqual(target.read_text(), "Existing owner help")

    @POSIX
    def test_whole_life_scaffold_is_native_pm_but_draft_context(self):
        self.apply(self.scaffold(title="Learn piano"))
        overview = (self.root / "Projects/Active/first-project/first-project.md").read_text()
        context = (self.root / "Projects/Active/first-project/context.md").read_text()
        self.assertIn("pm-project: true", overview)
        self.assertIn('status: "draft"', context)
        self.assertIn("client: null", context)
        self.assertFalse((self.root / "Workspaces").exists())

    @POSIX
    def test_second_project_reuses_populated_company_and_brand(self):
        self.apply(self.scaffold(kind="marketing", company_id="example", brand_id="example"))
        company = self.root / "Workspaces/Clients/Companies/example.md"
        company.write_text('---\ntype: company\nstatus: active\n---\n# Real supplied facts\n')
        brand = self.root / "Workspaces/Brands & Sites/example/brand.md"
        brand.write_text('---\ntype: brand\n---\n# Customer approved brand facts\n')
        before = company.read_bytes(), brand.read_bytes()
        self.apply(self.scaffold(project_id="second-project", kind="marketing", company_id="example", brand_id="example"))
        self.assertEqual(before, (company.read_bytes(), brand.read_bytes()))

    def test_generic_scope_and_install_inventory_cannot_be_bypassed(self):
        operation = self.scaffold()
        operation["operation_type"] = "generic"
        with self.assertRaises(TransactionError):
            inspect_bundle(self.root, operation)
        plan, batches, _ = installer.install_plan(self.root, self.source, "install", STAMP)
        attack = copy.deepcopy(batches[1])
        attack["writes"][0]["content"] = "injected method"
        with self.assertRaises(TransactionError):
            inspect_bundle(self.root, attack)
        attack = copy.deepcopy(batches[1])
        attack["writes"][0]["path"] = ".obsidian/plugins/evil/main.js"
        attack["expected_hashes"] = {".obsidian/plugins/evil/main.js": None}
        with self.assertRaises(TransactionError):
            inspect_bundle(self.root, attack)

    def test_scaffold_rejects_escape_and_unknown_facts(self):
        with self.assertRaises(TransactionError):
            self.scaffold(project_id="../escape")
        with self.assertRaises(TransactionError):
            self.scaffold(status="ready")
        with self.assertRaises(TransactionError):
            self.scaffold(company_id="imagined-business")


if __name__ == "__main__":
    unittest.main()
