"""Synthetic shared-root upgrades; no private Home material or live vaults."""
import copy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import home_adapter as adapter, home_install as installer, home_profile as profile, home_upgrade as upgrade
from claude_empire.transaction import TransactionError, apply_bundle, inspect_bundle, sha256_bytes
import test_home_profile as fixture
from test_home_profile import STAMP


class UpgradeTests(unittest.TestCase):
    setUpBase = fixture.ProfileTests.setUp
    apply = fixture.ProfileTests.apply
    prepare = fixture.ProfileTests.prepare
    install = fixture.ProfileTests.install

    def setUp(self):
        self.setUpBase()
        self.root = self.base / "My life and work"
        (self.root / "wiki").mkdir(parents=True)
        (self.root / ".raw").mkdir()
        (self.root / "AGENTS.md").write_bytes(b"Owner's wider life rules\r\n")
        self.prepare()
        self.install()
        self.apply(profile.compose_bundle(self.root, self.source, "compose", STAMP))
        self.apply(adapter.attachment_bundle(self.root, self.root, None, "home", "bind", STAMP, shared_root=True))
        customer = self.root / "Projects/Active/customer/notes.md"
        customer.parent.mkdir(parents=True)
        customer.write_bytes(b"Owner's work, not release content\r\n")
        (self.root / "wiki/life.md").write_text("Beyond marketing")
        self.handoff = b"---\ntype: handoff\nstatus: active\n---\n# Owner continuation\n"
        (self.root / "AI Team/Sessions/handoff.md").write_bytes(self.handoff)
        self.backup = self.base / "Backup"
        # Copy only product content, never clone private transaction locks/journals.
        self.backup_copy()
        (self.pin.parent / "home-compatibility-v0.5.2.json").write_bytes(self.pin.read_bytes())
        self.new_source = self.base / "Next authorized release"
        self.next_files = {p: b for p, b in self.files.items() if p != "PACKAGE-INTEGRITY.json"}
        self.next_files.update({
            "AGENTS.md": b"# New synthetic Home contract\n",
            "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md": b"# New synthetic method\n",
            "Assets/README.md": b"# New asset guidance\n",
            "AI Team/Knowledge/Avatars/team-cards/test.png": b"\x89PNG\r\n\xff\x00",
            ".obsidian/plugins/aimh-projects/data.json": b'{"customFields":[]}\n',
        })
        inventory = {"format": "aimh-preview-integrity-v1", "files": [
            {"path": p, "sha256": sha256_bytes(b), "bytes": len(b)} for p, b in self.next_files.items()]}
        self.next_files["PACKAGE-INTEGRITY.json"] = profile.encoded(inventory)
        for rel, data in self.next_files.items():
            dest = self.new_source / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        pin = {"release": "v0.6.1", "commit": "next-synthetic", "files": {
            p: sha256_bytes(b) for p, b in self.next_files.items()
            if p in ("AGENTS.md", "PACKAGE-INTEGRITY.json") or p.endswith(".py")}}
        self.pin.write_bytes(profile.encoded(pin))

    def backup_copy(self):
        shutil.copytree(self.root, self.backup, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".vault-meta"))

    def plan(self, backup=True):
        return upgrade.upgrade_plan(self.root, self.new_source, "upgrade", STAMP,
                                    self.backup if backup else None)

    def perform(self):
        plan, batches, completion = self.plan()
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(plan["backup_missing_or_different"], [])
        return upgrade.apply_upgrade(self.root, plan, batches, completion)

    def test_old_binding_stays_qualified_after_adapter_update(self):
        self.assertEqual(adapter.check_binding(self.root, "home")["release"], "v0.5.2")
        self.assertEqual(adapter.check_binding(self.root, "home")["compatibility"], "pinned-core")

    def test_upgrade_preserves_owner_and_business_in_arbitrarily_named_root(self):
        owners = ("AGENTS.md", profile.OWNER_CONTRACT, "wiki/life.md",
                  "Projects/Active/customer/notes.md", "AI Team/Sessions/handoff.md")
        before = {p: (self.root / p).read_bytes() for p in owners}
        result = self.perform()
        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in owners})
        self.assertEqual(adapter.check_binding(self.root, "home")["release"], "v0.6.1")
        self.assertEqual(adapter.check_binding(self.root, "home")["compatibility"], "pinned-core")
        self.assertFalse((self.root / ".obsidian/plugins/example/main.js").exists())
        self.assertFalse((self.root / ".obsidian/community-plugins.json").exists())
        self.assertEqual((self.root / "AI Team/Knowledge/Avatars/team-cards/test.png").read_bytes(),
                         self.next_files["AI Team/Knowledge/Avatars/team-cards/test.png"])
        self.assertEqual(self.plan()[0]["status"], "noop")

    def test_legacy_manifest_without_profile_upgrades(self):
        path = self.root / installer.STATE
        state = json.loads(path.read_bytes())
        del state["selection_profile"]
        path.write_bytes(profile.encoded(state))
        self.backup_copy()
        self.assertEqual(self.perform()["status"], "upgraded")

    def test_member_edit_is_a_conflict_and_never_replaced(self):
        path = self.root / "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md"
        path.write_text("Owner customization")
        plan, batches, completion = self.plan()
        self.assertEqual(plan["conflicts"][0]["reason"], "member-edit-or-unowned-collision")
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual(path.read_text(), "Owner customization")
        self.assertEqual(json.loads((self.root / installer.STATE).read_bytes())["status"], "installed")

    def test_new_unowned_collision_refuses(self):
        path = self.root / "Assets/README.md"
        path.parent.mkdir(parents=True)
        path.write_text("Owner assets guide")
        self.assertIn({"path": "Assets/README.md", "reason": "member-edit-or-unowned-collision"},
                      self.plan()[0]["conflicts"])

    def test_open_nested_session_blocks_before_any_write(self):
        path = self.root / "AI Team/Sessions/records/2026/10/unfinished.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ntype: session\nstatus: open\n---\n# Work\n")
        plan, batches, completion = self.plan()
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["conflicts"][0]["path"], path.relative_to(self.root).as_posix())
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        path.write_text("---\ntype: session\nstatus: closed\n---\n# History\n")
        self.assertEqual(self.plan()[0]["conflicts"], [])

    def test_backup_is_verified_not_an_acknowledgment(self):
        plan, batches, completion = self.plan(backup=False)
        self.assertTrue(plan["backup_missing_or_different"])
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        (self.backup / profile.HOME_CONTRACT).write_text("Wrong backup")
        self.assertIn(profile.HOME_CONTRACT, self.plan()[0]["backup_missing_or_different"])

    def test_changed_pending_file_refuses_before_incomplete_marker(self):
        plan, batches, completion = self.plan()
        (self.root / "Workspaces/AI Marketing Hub/Skills/Example/SKILL.md").write_text("Concurrent edit")
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual(json.loads((self.root / installer.STATE).read_bytes())["status"], "installed")

    def test_interrupted_batches_resume_without_overwriting_completed_content(self):
        with patch.object(installer, "BATCH_SIZE", 1):
            plan, batches, completion = self.plan()
            for batch in batches[:2]:
                self.apply(batch)
            self.assertEqual(installer.installation_status(self.root), "incomplete-installation")
            plan, batches, completion = self.plan()
            self.assertTrue(plan["resuming"])
            result = upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual(result["status"], "upgraded")

    def test_forged_upgrade_payload_cannot_write_unowned_projects(self):
        plan, batches, completion = self.plan()
        operation = copy.deepcopy(batches[1])
        operation["writes"] = [{"path": "Projects/Active/customer/notes.md", "mode": "replace",
                                 "content": "Injected private changes"}]
        operation["expected_hashes"] = {"Projects/Active/customer/notes.md":
                                      sha256_bytes((self.root / "Projects/Active/customer/notes.md").read_bytes())}
        with self.assertRaises(TransactionError):
            inspect_bundle(self.root, operation)

    def test_changed_source_bytes_refuse(self):
        (self.new_source / "Assets/README.md").write_text("Unqualified")
        with self.assertRaises(TransactionError):
            self.plan()

    def test_relocated_binding_requires_explicit_repair(self):
        path = self.root / "wiki/integrations/aimh-home/home/binding.json"
        binding = json.loads(path.read_bytes())
        binding["home_root"] = "/old/host"
        path.write_bytes(profile.encoded(binding))
        self.assertTrue(any(c["reason"] == "relocated-binding-needs-explicit-rebind"
                            for c in self.plan()[0]["conflicts"]))

    def test_new_session_after_review_refuses_before_marker(self):
        plan, batches, completion = self.plan()
        path = self.root / "AI Team/Sessions/records/new-session.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\ntype: session\nstatus: open\n---\n")
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual(json.loads((self.root / installer.STATE).read_bytes())["status"], "installed")

    def test_backup_changed_after_review_refuses(self):
        plan, batches, completion = self.plan()
        (self.backup / profile.HOME_CONTRACT).write_text("Changed after preview")
        with self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual(json.loads((self.root / installer.STATE).read_bytes())["status"], "installed")

    def test_metadata_changed_during_batches_is_preserved(self):
        plan, batches, completion = self.plan()
        original_apply = upgrade.apply_bundle
        count = 0
        def apply_and_edit(*args, **kwargs):
            nonlocal count
            result = original_apply(*args, **kwargs)
            count += 1
            if count == len(batches):
                (self.root / profile.COMPOSITION).write_text('{"owner":"concurrent edit"}')
            return result
        with patch.object(upgrade, "apply_bundle", apply_and_edit), self.assertRaises(TransactionError):
            upgrade.apply_upgrade(self.root, plan, batches, completion)
        self.assertEqual((self.root / profile.COMPOSITION).read_text(), '{"owner":"concurrent edit"}')
        self.assertEqual(json.loads((self.root / installer.STATE).read_bytes())["status"], "upgrading")

    def test_backup_inside_vault_refuses(self):
        with self.assertRaises(TransactionError):
            upgrade.upgrade_plan(self.root, self.new_source, "upgrade", STAMP, self.root / "wiki")

    def test_fresh_install_includes_binary_dependencies(self):
        self.root = self.base / "Fresh customer workspace"
        (self.root / "wiki").mkdir(parents=True)
        (self.root / ".raw").mkdir()
        self.prepare()
        plan, batches, completion = installer.install_plan(self.root, self.new_source, "fresh", STAMP)
        installer.apply_install(self.root, plan, batches, completion)
        self.assertEqual(installer.installation_status(self.root), "headless-verified")
        self.assertEqual((self.root / "AI Team/Knowledge/Avatars/team-cards/test.png").read_bytes(),
                         self.next_files["AI Team/Knowledge/Avatars/team-cards/test.png"])


if __name__ == "__main__":
    unittest.main()
