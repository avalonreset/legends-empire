#!/usr/bin/env python3
from __future__ import annotations

import ast
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from claude_empire.transaction import apply_bundle
from claude_empire.vault_ops import build_vault_bundle
from legends_vault_steward.cli import main
from legends_vault_steward.config import DEFAULT_CONFIG_PATH, load_config
from legends_vault_steward.observe import observe
from legends_vault_steward.paths import MAX_BASELINE_JSON_BYTES
from legends_vault_steward.review import build_snapshot, compare

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


class Steward041FixesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()

    def write_note(self, rel: str, content: str | bytes) -> Path:
        target = self.vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def test_f2_baseline_2mb_accepted_over_64mib_exit_11_and_snapshot_matches(self) -> None:
        self.assertEqual(MAX_BASELINE_JSON_BYTES, 67_108_864)
        self.write_note("Note.md", "# Note\n\n[[MissingTarget]]\n")
        out_dir = self.base / "out-1"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["scan", str(self.vault), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(code, 0, stderr.getvalue())

        obs_path = out_dir / "observe.json"
        snap_path = out_dir / "snapshot.json"
        self.assertTrue(obs_path.is_file())
        self.assertTrue(snap_path.is_file())

        obs_doc = json.loads(obs_path.read_text(encoding="utf-8"))
        snap_doc = json.loads(snap_path.read_text(encoding="utf-8"))
        self.assertEqual(snap_doc["schema"], "legends.vault-steward.snapshot/v1")

        # Mutate vault and verify snapshot.json and observe.json give identical comparison
        self.write_note("Added.md", "# Added\n")
        after_doc = observe(str(self.vault), now=NOW)
        cmp_from_obs = compare(obs_doc, after_doc)
        cmp_from_snap = compare(snap_doc, after_doc)
        self.assertEqual(cmp_from_obs, cmp_from_snap)

        # Write a > 2 MB baseline JSON (padded with extra snapshot entries) and verify CLI accepts it
        padded = dict(snap_doc)
        padded["snapshot"] = dict(snap_doc["snapshot"])
        blob_val = "a" * 64
        for idx in range(28_000):
            padded["snapshot"][f"synthetic/pad-{idx:05d}.md"] = blob_val
        baseline_2mb = self.base / "baseline-2mb.json"
        baseline_2mb.write_text(json.dumps(padded), encoding="utf-8")
        self.assertGreater(baseline_2mb.stat().st_size, 2 * 1024 * 1024)

        out_2mb = io.StringIO()
        err_2mb = io.StringIO()
        with redirect_stdout(out_2mb), redirect_stderr(err_2mb):
            rc_2mb = main(
                ["scan", str(self.vault), "--baseline", str(baseline_2mb), "--format", "json", "--as-of", "2026-11-16"]
            )
        self.assertEqual(rc_2mb, 0, err_2mb.getvalue())

        # Write a > 64 MiB sparse/large file and verify CLI returns exit 11
        baseline_over_64m = self.base / "baseline-65mb.json"
        with baseline_over_64m.open("wb") as handle:
            handle.seek(MAX_BASELINE_JSON_BYTES + 1024)
            handle.write(b"}")
        err_65m = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err_65m):
            rc_65m = main(["scan", str(self.vault), "--baseline", str(baseline_over_64m), "--format", "json"])
        self.assertEqual(rc_65m, 11)
        self.assertIn("exceeds", err_65m.getvalue())

    def test_f3_default_skip_dirs_and_tmp_preserved_in_generic(self) -> None:
        for skipped_dir in ("backups", "_runtime", "_third-party", ".backup", ".cache"):
            self.write_note(f"{skipped_dir}/Ignored.md", "# Ignored\n")
        self.write_note("tmp/Scratch.md", "# Scratch\n")
        self.write_note("Keep.md", "# Keep\n")

        generic_report = observe(str(self.vault), now=NOW, profile="generic")
        scanned_paths = set(generic_report["snapshot"].keys())
        self.assertEqual(scanned_paths, {"Keep.md", "tmp/Scratch.md"})
        notes_by_path = {n["path"]: n for n in generic_report["notes"]}
        self.assertEqual(notes_by_path["tmp/Scratch.md"]["class"], "G")

        empire_report = observe(str(self.vault), now=NOW, profile="empire")
        self.assertEqual(set(empire_report["snapshot"].keys()), {"Keep.md"})

    def test_f5_empire_entrypoints_and_index_max_age_days(self) -> None:
        cfg = load_config(profile="empire")
        self.assertIn("EMPIRE-ENTRY.md", cfg["entrypoints"])
        self.assertIn("EMPIRE-START.md", cfg["entrypoints"])
        index_surface = next(s for s in cfg["surfaces"] if s["id"] == "index")
        self.assertEqual(index_surface["max_age_days"], 30)

        self.write_note("EMPIRE-ENTRY.md", "# Empire Entry\n\n[[wiki/index]]\n")
        self.write_note(
            "wiki/index.md",
            "---\nupdated: 2026-08-01\n---\n# Wiki Index\n\n[[hot]]\n",
        )
        self.write_note(
            "wiki/hot.md",
            "---\nupdated: 2026-11-15\n---\n# Hot Cache\n\n[[index]]\n",
        )
        report = observe(str(self.vault), now=NOW, profile="empire")
        findings = report["findings"]
        # EMPIRE-ENTRY.md is a recognized Class A entrypoint, not an orphan defect
        self.assertFalse(
            any(f["path"] == "EMPIRE-ENTRY.md" and f["code"] == "graph.orphan_defect" for f in findings)
        )
        # Stale wiki/index.md (> 30 days) triggers freshness.stale_named_surface
        self.assertTrue(
            any(f["path"] == "wiki/index.md" and f["code"] == "freshness.stale_named_surface" for f in findings)
        )
        # Optional empire surfaces (HOT.md, fleet/Core.md) do not emit missing_named_surface when absent
        self.assertFalse(
            any(
                f["code"] == "freshness.missing_named_surface" and f["path"] in {"HOT.md", "fleet/Core.md"}
                for f in findings
            )
        )

    def test_empire_profile_skips_archive_and_recognizes_core_and_hot_surfaces(self) -> None:
        self.write_note(
            "archive/Old-Now.md",
            "---\nupdated: 2026-01-01\nfresh_until: 2026-02-01\n---\n# Archived\n",
        )
        self.write_note(
            "HOT.md",
            "---\nupdated: 2026-10-01\nfresh_until: 2026-11-01\n---\n# Hot\n",
        )
        self.write_note(
            "fleet/Core.md",
            "---\nupdated: 2026-11-16\n---\n# Core\n"
            "- 2026-01-01 a\n- 2026-01-02 b\n- 2026-01-03 c\n"
            "- 2026-01-04 d\n- 2026-01-05 e\n- 2026-01-06 f\n",
        )
        self.write_note("wiki/index.md", "---\nupdated: 2026-11-16\n---\n# Index\n")
        self.write_note("wiki/hot.md", "---\nupdated: 2026-11-16\n---\n# Wiki Hot\n")

        report = observe(str(self.vault), now=NOW, profile="empire")
        self.assertNotIn("archive/Old-Now.md", report["snapshot"])
        notes_by_path = {n["path"]: n for n in report["notes"]}
        self.assertEqual(notes_by_path["HOT.md"]["class"], "A")
        self.assertEqual(notes_by_path["fleet/Core.md"]["class"], "A")
        findings = report["findings"]
        self.assertFalse(
            any(
                f["code"] == "graph.orphan_defect" and f["path"] in {"HOT.md", "fleet/Core.md"}
                for f in findings
            )
        )
        self.assertTrue(
            any(
                f["code"] == "freshness.expired_fresh_until" and f["path"] == "HOT.md"
                for f in findings
            )
        )
        self.assertTrue(
            any(
                f["code"] == "freshness.state_leak_in_core" and f["path"] == "fleet/Core.md"
                for f in findings
            )
        )

        # Explicit overlay overriding HOT.md yields optional empire_hot without duplicate findings
        overlay = self.base / "explicit-hot.json"
        overlay.write_text(
            json.dumps({"surfaces": [{"id": "hot", "path": "HOT.md", "role": "hot", "max_age_days": 14}]}),
            encoding="utf-8",
        )
        report_ov = observe(str(self.vault), config_path=str(overlay), now=NOW, profile="empire")
        hot_expired = [
            f
            for f in report_ov["findings"]
            if f["code"] == "freshness.expired_fresh_until" and f["path"] == "HOT.md"
        ]
        self.assertEqual(len(hot_expired), 1)
        self.assertEqual(hot_expired[0].get("surface"), "hot")

    def test_f6_unreadable_severity_promotion_and_byte_offset(self) -> None:
        # Unreadable note with invalid UTF-8 at byte offset 7
        self.write_note("meta/Hot-Cache.md", b"# Good\n\xff\xfeBad UTF-8\n")
        self.write_note("Minor.md", b"abc\x80def")

        # Before 10 inbound links, both are low severity
        r1 = observe(str(self.vault), now=NOW)
        f_minor = next(f for f in r1["findings"] if f["code"] == "note.unreadable" and f["path"] == "Minor.md")
        self.assertEqual(f_minor["severity"], "low")
        self.assertEqual(f_minor["byte_offset"], 3)
        self.assertIn("byte offset 3", f_minor["message"])

        # Add 10 notes linking via extensionless path and stem
        for i in range(5):
            self.write_note(f"notes/A-{i}.md", "See [[meta/Hot-Cache]] for context.\n")
        for i in range(5):
            self.write_note(f"notes/B-{i}.md", "See [[Hot-Cache]] for context.\n")

        r2 = observe(str(self.vault), now=NOW)
        f_hot = next(f for f in r2["findings"] if f["code"] == "note.unreadable" and f["path"] == "meta/Hot-Cache.md")
        self.assertEqual(f_hot["severity"], "high")
        self.assertEqual(f_hot["byte_offset"], 7)
        self.assertEqual(f_hot["unresolved_links"], 10)

        # Named surface unreadable also promotes to high even with 0 links
        self.write_note("wiki/hot.md", b"bad\xffbytes")
        r3 = observe(str(self.vault), now=NOW, profile="empire")
        f_surf = next(f for f in r3["findings"] if f["code"] == "note.unreadable" and f["path"] == "wiki/hot.md")
        self.assertEqual(f_surf["severity"], "high")
        self.assertEqual(f_surf["byte_offset"], 3)

    def test_f7_utf8_stdio_and_unicode_encode_error_is_exit_10(self) -> None:
        self.write_note("Emoji.md", "# Launch \U0001f680\n\n[[Target-\U0001f680]]\n")
        raw_buf = io.BytesIO()
        cp1252_stream = io.TextIOWrapper(raw_buf, encoding="cp1252", errors="strict")
        err_stream = io.StringIO()
        with redirect_stdout(cp1252_stream), redirect_stderr(err_stream):
            rc = main(["scan", str(self.vault), "--stdout", "--format", "md", "--as-of", "2026-11-16"])
        self.assertEqual(rc, 0, err_stream.getvalue())

        # Stream without .reconfigure that raises UnicodeEncodeError returns exit 10 (never 11)
        class BrokenAsciiStream:
            def write(self, s: str) -> int:
                s.encode("ascii")
                return len(s)

            def flush(self) -> None:
                pass

        err2 = io.StringIO()
        with redirect_stdout(BrokenAsciiStream()), redirect_stderr(err2):  # type: ignore[arg-type]
            rc2 = main(["scan", str(self.vault), "--stdout", "--format", "md", "--as-of", "2026-11-16"])
        self.assertEqual(rc2, 10)

    def test_f19_fresh_init_vault_passes_steward_scan_empire_with_no_high_findings(self) -> None:
        fresh_vault = self.base / "fresh-init"
        fresh_vault.mkdir()
        bundle = build_vault_bundle(
            fresh_vault,
            operation_id="init-test",
            operation_type="setup",
            generated_at="2026-11-16T00:00:00Z",
            adopt=False,
        )
        apply_bundle(fresh_vault, bundle)
        report = observe(str(fresh_vault), now=NOW, profile="empire")
        high_findings = [f for f in report["findings"] if f["severity"] == "high"]
        self.assertEqual(high_findings, [])

    def test_f20_dead_config_keys_removed(self) -> None:
        raw_default = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        findings_cfg = raw_default["findings"]
        for dead_key in ("unresolved_core_classes", "max_queue_orphan_findings", "max_unresolved_findings"):
            self.assertNotIn(dead_key, findings_cfg)

    def test_surface_byte_budget_fresh_until_and_core_state_leak(self) -> None:
        overlay = self.base / "overlay.json"
        overlay.write_text(
            json.dumps(
                {
                    "surfaces": [
                        {"id": "hot", "path": "wiki/hot.md", "role": "hot", "max_bytes": 50},
                        {"id": "core", "path": "AGENTS.md", "role": "core", "max_dated_lines": 2},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.write_note(
            "wiki/hot.md",
            "---\nupdated: 2026-10-01\nfresh_until: 2026-11-01\n---\n# Hot\n\n" + ("word " * 20) + "\n",
        )
        self.write_note(
            "AGENTS.md",
            "---\nupdated: 2026-11-16\n---\n# Core\n- 2026-01-01 item 1\n- 2026-01-02 item 2\n- 2026-01-03 item 3\n",
        )
        report = observe(str(self.vault), config_path=str(overlay), now=NOW)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("budget.surface_bytes_exceeded", codes)
        self.assertIn("freshness.expired_fresh_until", codes)
        self.assertIn("freshness.stale_named_surface", codes)
        self.assertIn("freshness.state_leak_in_core", codes)

    def test_o1_ontology_index_findings_wired(self) -> None:
        idx_dir = self.vault / ".vault-meta" / "ontology"
        idx_dir.mkdir(parents=True)
        (idx_dir / "index.json").write_text(
            json.dumps(
                {
                    "facts": {},
                    "findings": [
                        {
                            "code": "ontology_duplicate_entity_id",
                            "path": "wiki/dup.md",
                            "detail": "duplicate entity_id 'acme'",
                        },
                        {
                            "code": "ontology_certified_without_value",
                            "path": "wiki/cert.md",
                            "detail": "missing value for certified key",
                        },
                        {
                            "code": "frontmatter_nested_unsupported",
                            "path": "wiki/nested.md",
                            "detail": "nested block unsupported",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        report = observe(str(self.vault), now=NOW)
        codes = {f["code"] for f in report["findings"]}
        self.assertIn("ontology_duplicate_entity_id", codes)
        self.assertIn("ontology_certified_without_value", codes)
        self.assertIn("frontmatter_nested_unsupported", codes)

    def test_stdlib_only_imports_in_legends_vault_steward(self) -> None:
        pkg_dir = ROOT / "legends_vault_steward"
        stdlib_names = set(sys.stdlib_module_names) | {"legends_vault_steward"}
        for py_file in sorted(pkg_dir.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        self.assertIn(top, stdlib_names, f"non-stdlib import {top} in {py_file.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        top = node.module.split(".", 1)[0]
                        self.assertIn(top, stdlib_names, f"non-stdlib import {top} in {py_file.name}")


if __name__ == "__main__":
    unittest.main()
