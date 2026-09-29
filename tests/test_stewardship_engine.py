from __future__ import annotations

import copy
import io
import json
from contextlib import redirect_stdout, redirect_stderr
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from legends_vault_steward.observe import observe
from legends_vault_steward.paths import ReportPathError, resolve_vault_root
from legends_vault_steward.report import render_markdown, write_reports
from legends_vault_steward.review import compare, finding_id
from legends_vault_steward.cli import main
from legends_vault_steward.config import load_config
def try_symlink(link, target):
    try:
        link.symlink_to(target)
        return link.is_symlink()
    except (OSError, NotImplementedError):
        return False

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class StewardshipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "vault"
        self.root.mkdir()

    def note(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    def scan(self, **kwargs):
        return observe(str(self.root), now=NOW, **kwargs)

    def test_project_session_and_history_have_distinct_obligations(self):
        self.note("Launch.md", "---\ntype: project\nstatus: blocked\nupdated: 2026-08-01\ndue: 2026-09-01\n---\nLaunch\n")
        self.note("Handoff.md", "---\ntype: session\nstatus: active\nowner: Pat\nupdated: 2026-09-28\nnext_action: Test export\n---\nSession\n")
        self.note("Archive.md", "---\ntype: project\nstatus: completed\nupdated: 2020-01-01\n---\nHistory\n")
        report = self.scan()
        codes = {f["code"] for f in report["findings"] if f["path"] == "Launch.md"}
        self.assertTrue({"operations.overdue", "operations.review_due", "operations.missing_fields", "operations.missing_blocker"} <= codes)
        self.assertFalse(any(f["code"].startswith("operations.") and f["path"] == "Archive.md" for f in report["findings"]))
        handoff = next(f for f in report["findings"] if f["path"] == "Handoff.md" and f["code"] == "operations.missing_fields")
        self.assertIn("evidence", handoff["message"])
        self.assertIn("objective", handoff["message"])

    def test_bounded_agenda_keeps_complete_evidence_and_hashes(self):
        for n in range(100):
            self.note(f"Note-{n:03}.md", f"[[Missing-{n:03}]]\n")
        report = self.scan(review_limit=3, include_notes="none")
        self.assertEqual(len(report["review"]["items"]), 3)
        self.assertEqual(report["review"]["deferred_groups"], 97)
        self.assertEqual(sum(f["code"] == "link.unresolved" for f in report["findings"]), 100)
        self.assertTrue(all(i["source_sha256"] for i in report["review"]["items"]))
        self.assertEqual(len(report["snapshot"]), 100)

    def test_comparison_distinguishes_repair_from_removed_evidence(self):
        self.note("Index.md", "[[Missing]]\n")
        before = self.scan()
        self.note("Missing.md", "A destination\n")
        after = self.scan()
        delta = compare(before, after)
        self.assertEqual(delta["added_notes"], ["Missing.md"])
        self.assertTrue(delta["no_longer_observed"])
        (self.root / "Index.md").unlink()
        removed = compare(after, self.scan())
        self.assertEqual(removed["removed_notes"], ["Index.md"])
        self.assertIn("not proof", removed["interpretation"])

    def test_reject_incompatible_baseline(self):
        report = self.scan()
        for key in ("version", "config_hash"):
            before = copy.deepcopy(report)
            before[key] = "different"
            with self.assertRaises(ValueError):
                compare(before, report)
        before = copy.deepcopy(report)
        before["vault"]["id"] = "elsewhere"
        with self.assertRaises(ValueError):
            compare(before, report)

    def test_finding_identity_survives_measurement_changes(self):
        first = {"code": "freshness.stale_named_surface", "path": "Hot.md", "surface": "hot", "age_days": 30}
        self.assertEqual(finding_id(first), finding_id({**first, "age_days": 31}))

    def test_immutable_report_and_format(self):
        report = self.scan()
        out = Path(self.tmp.name) / "reports"
        vault = resolve_vault_root(self.root)
        write_reports(report, out, vault=vault, format="md")
        self.assertFalse((out / "observe.json").exists())
        before = (out / "observe.md").read_bytes()
        with self.assertRaises(ReportPathError):
            write_reports(report, out, vault=vault)
        self.assertEqual((out / "observe.md").read_bytes(), before)
        self.assertFalse((out / "observe.json").exists())

    def test_report_symlink_cannot_overwrite_note(self):
        self.note("Keep.md", "keep me")
        out = self.root / ".steward" / "run"
        out.mkdir(parents=True)
        if not try_symlink(out / "observe.md", self.root / "Keep.md"):
            self.skipTest("symlink privileges unavailable")
        with self.assertRaises(ReportPathError):
            write_reports(self.scan(), out, vault=resolve_vault_root(self.root))
        self.assertEqual((self.root / "Keep.md").read_text(), "keep me")
        self.assertFalse((out / "observe.json").exists())

    def test_report_stem_cannot_escape(self):
        with self.assertRaises(ReportPathError):
            write_reports(self.scan(), Path(self.tmp.name), "../Keep", vault=resolve_vault_root(self.root))

    def test_report_does_not_render_note_html(self):
        self.note("Note.md", "[[<script>alert(1)</script>]]")
        md = render_markdown(self.scan())
        self.assertNotIn("<script>", md)
        self.assertLess(md.index("## Review agenda"), md.index("## Inventory"))

    def test_limit_is_validated(self):
        for n in (0, 26):
            with self.assertRaises(ValueError):
                self.scan(review_limit=n)

    def test_unexpected_change_exit_and_evidence(self):
        self.note("Project.md", "before")
        baseline = Path(self.tmp.name) / "before.json"
        baseline.write_text(json.dumps(self.scan()), encoding="utf-8")
        self.note("Project.md", "after")
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(io.StringIO()):
            code = main(["scan", str(self.root), "--baseline", str(baseline), "--expect-changed", "Other.md", "--format", "json"])
        self.assertEqual(code, 12)
        self.assertEqual(json.loads(output.getvalue())["comparison"]["unexpected_changes"], ["Project.md"])

    def test_malformed_baselines_are_errors(self):
        for value in ([], {}, {"schema": "legends.vault-steward.observe/v1", "snapshot": {}}):
            with self.assertRaises(ValueError):
                compare(value, self.scan())

    def test_profile_is_opt_in_and_surface_override_is_effective(self):
        self.assertFalse(load_config()["surfaces"])
        overlay = Path(self.tmp.name) / "policy.json"
        overlay.write_text(json.dumps({"surfaces": [{"id": "hot", "max_words": 900}]}))
        profile = load_config(overlay, "empire")
        hot = [s for s in profile["surfaces"] if s["id"] == "hot"]
        self.assertEqual(len(hot), 1)
        self.assertEqual(hot[0]["max_words"], 900)
        self.assertEqual(hot[0]["path"], "wiki/hot.md")

    def test_declared_entrypoint_does_not_need_an_inbound_link(self):
        self.note("wiki/index.md", "# Main entrance\n")
        report = self.scan(profile="empire")
        self.assertFalse(any(f["path"] == "wiki/index.md" and f["code"] == "graph.orphan_defect" for f in report["findings"]))

    def test_directory_read_error_is_reported_as_incomplete_coverage(self):
        def denied(root, followlinks, onerror):
            onerror(PermissionError(13, "denied", str(self.root / "Private")))
            return iter([])
        with patch("legends_vault_steward.inventory.os.walk", denied):
            report = self.scan()
        self.assertEqual(report["inventory"]["skipped_unreadable"], 1)
        self.assertIn("note.unreadable", [f["code"] for f in report["findings"]])
        self.assertTrue(compare(report, report)["coverage_incomplete"])


if __name__ == "__main__":
    unittest.main()
