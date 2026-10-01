"""Hermetic adapter checks; invented Home fixture contains no licensed source."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from claude_empire import home_adapter as adapter
from claude_empire.cli import main
from claude_empire.transaction import TransactionError, apply_bundle, inspect_bundle, sha256_bytes

POSIX = unittest.skipIf(os.name == "nt", "writes require POSIX or WSL")
PROJECT = "Projects/Active/Synthetic/context.md"


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.empire = self.base / "Empire"
        (self.empire / "wiki").mkdir(parents=True)
        (self.empire / ".raw").mkdir()
        self.home = self.empire / "HomeWorkspace"
        self.files = {"AGENTS.md": b"Synthetic Home contract\n",
            "PACKAGE-INTEGRITY.json": b'{"synthetic": true}\n',
            "AI Team/Knowledge/Scripts/resolve-context.py":
                b'import sys\nfrom pathlib import Path\np=Path(sys.argv[sys.argv.index("--vault")+1])/sys.argv[sys.argv.index("--project")+1]\nprint("private-result")\nsys.exit(0 if "ready" in p.read_text() else 1)\n'}
        for path, payload in {**self.files, PROJECT: b"ready\n"}.items():
            out = self.home / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(payload)
        self.pin = self.base / "pin.json"
        self.pin.write_text(json.dumps({"release": "v0.5.2", "commit": "test-only",
            "files": {p: sha256_bytes(b) for p, b in self.files.items()}}))
        patcher = patch.object(adapter, "PIN", self.pin)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The invented resolver needs no third-party modules. Mock only its
        # dependency probe so standalone CI remains dependency-free; execute the
        # actual staged fixture subprocess for native readiness behavior.
        real_run = subprocess.run
        def synthetic_dependency(command, **kwargs):
            if command[-2:] == ["-c", "import yaml"]:
                return subprocess.CompletedProcess(command, 0, b"", b"")
            return real_run(command, **kwargs)
        runner = patch.object(adapter.subprocess, "run", side_effect=synthetic_dependency)
        runner.start()
        self.addCleanup(runner.stop)

    def bundle(self, **changes):
        args = dict(empire=self.empire, home=self.home, project=PROJECT,
                    binding_id="synthetic", operation_id="home-attach", generated_at="2026-10-01T00:00:00Z")
        args.update(changes)
        return adapter.attachment_bundle(**args)

    def apply(self, bundle=None):
        bundle = bundle or self.bundle()
        return apply_bundle(self.empire, bundle, approved_plan_sha256=inspect_bundle(self.empire, bundle)["approval_sha256"])

    def test_inspection_is_read_only_not_readiness(self):
        before = sorted(str(p) for p in self.empire.rglob("*"))
        result = adapter.inspect_home(self.home, PROJECT)
        self.assertEqual(result["compatibility"], "pinned-core")
        self.assertEqual(result["native_readiness"], "not_checked")
        self.assertEqual(before, sorted(str(p) for p in self.empire.rglob("*")))

    def test_plan_references_only_no_root_instruction_edits(self):
        result = self.bundle()
        self.assertEqual(len(result["writes"]), 2)
        self.assertTrue(all(p["path"].startswith("wiki/integrations/aimh-home/") for p in result["writes"]))
        inspect_bundle(self.empire, result)
        self.assertFalse((self.empire / "integrations").exists())

    def test_unknown_or_modified_core_rejected(self):
        (self.home / "AGENTS.md").write_text("new upstream rules")
        self.assertEqual(adapter.inspect_home(self.home)["compatibility"], "unverified")
        with self.assertRaises(TransactionError):
            self.bundle()

    def test_context_is_mutable_customer_content_not_core(self):
        (self.home / PROJECT).write_text("new business facts")
        self.assertEqual(adapter.inspect_home(self.home, PROJECT)["compatibility"], "pinned-core")

    def test_root_collision_and_ancestor_rejected(self):
        with self.assertRaises(TransactionError):
            self.bundle(empire=self.home)
        nested = self.home / "Empire"
        nested.mkdir()
        with self.assertRaises(TransactionError):
            self.bundle(empire=nested)

    def test_project_and_identifier_escape_rejected(self):
        for path in ("../context.md", "/Projects/context.md", "Projects/../context.md", "projects/A/context.md", "Projects\\A\\context.md", "Projects/A/other.md"):
            with self.subTest(path=path), self.assertRaises(TransactionError):
                self.bundle(project=path)
        with self.assertRaises(TransactionError):
            self.bundle(binding_id="../other")

    def test_crlf_core_is_compatible(self):
        for path, payload in self.files.items():
            (self.home / path).write_bytes(payload.replace(b"\n", b"\r\n"))
        self.assertEqual(adapter.inspect_home(self.home)["compatibility"], "pinned-core")

    @POSIX
    def test_symlink_project_or_home_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(TransactionError):
            adapter.inspect_home(alias)
        context = self.home / PROJECT
        context.unlink()
        context.symlink_to(self.home / "AGENTS.md")
        with self.assertRaises(TransactionError):
            self.bundle()

    @POSIX
    def test_apply_preserves_notes_idempotent_and_native_check(self):
        note = self.empire / "AGENTS.md"
        note.write_bytes(b"Owner instructions\r\n")
        source = (self.home / PROJECT).read_bytes()
        self.apply()
        route = self.empire / adapter.binding_paths("synthetic")[1]
        route.write_text("My annotated routing note")
        self.assertEqual(self.bundle(operation_id="repeat")["writes"], [])
        self.assertEqual(note.read_bytes(), b"Owner instructions\r\n")
        self.assertEqual((self.home / PROJECT).read_bytes(), source)
        result = adapter.check_binding(self.empire, "synthetic", native=True)
        self.assertEqual(result["native_readiness"], "ready")
        self.assertNotIn("private-result", json.dumps(result))
        (self.home / PROJECT).write_text("draft")
        self.assertEqual(adapter.check_binding(self.empire, "synthetic", native=True)["native_readiness"], "refused")

    @POSIX
    def test_binding_retarget_and_native_drift_refused(self):
        self.apply()
        with self.assertRaises(TransactionError):
            self.bundle(project="Projects/Other/context.md")
        (self.home / "AGENTS.md").write_text("changed")
        with self.assertRaises(TransactionError):
            adapter.check_binding(self.empire, "synthetic", native=True)

    @POSIX
    def test_reviewed_plan_required_and_concurrent_note_not_overwritten(self):
        bundle = self.bundle()
        with self.assertRaises(TransactionError):
            apply_bundle(self.empire, bundle, approved_plan_sha256="0" * 64)
        route = self.empire / adapter.binding_paths("synthetic")[1]
        route.parent.mkdir(parents=True)
        route.write_text("concurrent owner work")
        with self.assertRaises(TransactionError):
            self.apply(bundle)
        self.assertEqual(route.read_text(), "concurrent owner work")

    def test_cli_inspect_does_not_need_initialized_empire(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = main(["home-adapter", "inspect", "--home", str(self.home)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["writes"], 0)

    def test_standalone_parser_and_version_do_not_require_home(self):
        with patch.object(adapter, "PIN", self.base / "absent"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                main(["--version"])
            self.assertEqual(stopped.exception.code, 0)

    @POSIX
    def test_workspace_only_discovery_selection_and_detach(self):
        self.assertEqual(adapter.list_bindings(self.empire)["bindings"], [])
        self.apply(self.bundle(project=None))
        self.assertEqual(adapter.check_binding(self.empire, "synthetic", native=True)["native_readiness"], "not_selected")
        self.assertEqual(adapter.check_binding(self.empire, "synthetic", native=True, project=PROJECT)["native_readiness"], "ready")
        self.assertEqual(adapter.list_bindings(self.empire)["bindings"][0]["enabled"], True)
        source = {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()}
        bundle = adapter.detach_bundle(self.empire, "synthetic", "unplug", "2026-10-01T00:00:00Z")
        self.apply(bundle)
        self.assertEqual(adapter.check_binding(self.empire, "synthetic", native=True)["compatibility"], "detached")
        self.assertFalse(adapter.list_bindings(self.empire)["bindings"][0]["enabled"])
        self.assertEqual(adapter.detach_bundle(self.empire, "synthetic", "again", "2026-10-01T00:00:00Z")["writes"], [])
        self.assertEqual(source, {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob("*") if p.is_file()})

    def test_cli_apply_rechecks_source_pin_after_review(self):
        bundle = self.bundle()
        approval = inspect_bundle(self.empire, bundle)["approval_sha256"]
        (self.home / "AGENTS.md").write_text("changed after preview")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            code = main(["home-adapter", "apply", "--vault", str(self.empire), "--home", str(self.home),
                         "--project", PROJECT, "--binding-id", "synthetic", "--operation-id", "home-attach",
                         "--generated-at", "2026-10-01T00:00:00Z", "--approved-plan-sha256", approval])
        self.assertNotEqual(code, 0)
        self.assertFalse((self.empire / "wiki/integrations").exists())

    @POSIX
    def test_missing_native_dependency_is_actionable_without_vault_writes(self):
        self.apply()
        with patch.object(adapter.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"missing")):
            report = adapter.check_binding(self.empire, "synthetic", native=True)
        self.assertEqual(report["native_readiness"], "dependency_missing")
        self.assertIn("--native-python", report["native_requirement"])
        self.assertEqual(report["writes"], 0)

    @POSIX
    def test_malformed_binding_refuses_without_traceback(self):
        self.apply()
        state_file = self.empire / adapter.binding_paths("synthetic")[0]
        state = json.loads(state_file.read_text())
        del state["project"]
        state_file.write_text(json.dumps(state))
        with self.assertRaises(TransactionError):
            adapter.check_binding(self.empire, "synthetic")


if __name__ == "__main__":
    unittest.main()
