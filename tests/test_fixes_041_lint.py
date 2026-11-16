#!/usr/bin/env python3
"""Hermetic regression tests for v0.4.1 lint, reparse/junction, and knowledge pack fixes (F1/R4, F8/F24/R8, F9/R9)."""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_empire import cli
from claude_empire import lint_engine
from claude_empire import ontology as ont


def _reference_without_link_suffix(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix
    if suffix.casefold() not in lint_engine._EXTENSIONLESS_WIKILINK_SUFFIXES:
        return None
    return path[: -len(suffix)]


def _reference_resolve(
    resolver: lint_engine._Resolver, link: lint_engine._Link
) -> list[lint_engine._Target]:
    """Test-local reference copy of the pre-B5 _Resolver.resolve algorithm."""
    raw = urllib.parse.unquote(link.file_part.strip()).replace("\\", "/")
    if not raw:
        return resolver._exact(link.source)

    raw = raw.lstrip("/")
    source_dir = posixpath.dirname(link.source)
    query_options: list[str] = []
    if link.markdown_relative or raw.startswith(("./", "../")):
        query_options.append(posixpath.normpath(posixpath.join(source_dir, raw)))
    query_options.append(posixpath.normpath(raw))
    if resolver.wiki_prefix != "." and not raw.casefold().startswith("wiki/"):
        query_options.append(
            posixpath.normpath(posixpath.join(resolver.wiki_prefix, raw))
        )

    for query in query_options:
        exact = resolver._exact(query)
        if exact:
            return exact

    if "/" not in raw:
        candidates: list[lint_engine._Target] = []
        candidates.extend(resolver.by_basename.get(raw.casefold(), ()))
        candidates.extend(resolver.by_alias.get(raw.casefold(), ()))
        return resolver._deduplicate(candidates)

    suffix = posixpath.normpath(raw).casefold()
    candidates = [
        target
        for target in resolver.targets
        if target.path.casefold().endswith(f"/{suffix}")
        or (
            _reference_without_link_suffix(target.path) is not None
            and _reference_without_link_suffix(target.path).casefold().endswith(f"/{suffix}")  # type: ignore[union-attr]
        )
    ]
    return resolver._deduplicate(candidates)


def _write_valid_note(path: Path, title: str = "Valid Note") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\n"
        f"title: {title}\n"
        f"type: concept\n"
        f"status: evergreen\n"
        f"created: 2026-11-16\n"
        f"updated: 2026-11-16\n"
        f"tags:\n"
        f"  - test\n"
        f"---\n\n"
        f"# {title}\n\n"
        f"Body content.\n",
        encoding="utf-8",
    )


class Lint041FixesTests(unittest.TestCase):
    def test_oserror_per_entry_recorded_as_unreadable_path_and_exclude_prunes_before_stat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            _write_valid_note(vault / "wiki" / "index.md", "Index")
            bad_dir = vault / "${APPDATA}" / "\uf05cnpm" / "bin"
            bad_dir.mkdir(parents=True)
            bad_file = bad_dir / "grok"
            bad_file.write_text("stub", encoding="utf-8")

            real_lstat = os.lstat
            real_stat = os.stat
            stat_calls_on_bad: list[str] = []

            def FaultyLstat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if p_str.endswith("grok"):
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_lstat(target, *args, **kwargs)

            def FaultyStat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if p_str.endswith("grok") or "\uf05cnpm" in p_str:
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_stat(target, *args, **kwargs)

            with mock.patch("claude_empire.lint_engine.os.lstat", side_effect=FaultyLstat), mock.patch(
                "claude_empire.lint_engine.os.stat", side_effect=FaultyStat
            ):
                report = lint_engine.lint_vault(vault, as_of="2026-11-16")

            self.assertEqual(report["summary"]["pages_scanned"], 1)
            self.assertEqual(len(report["read_errors"]), 1)
            err = report["read_errors"][0]
            self.assertEqual(err["path"], "${APPDATA}/\uf05cnpm/bin/grok")
            self.assertEqual(err["code"], "lint.unreadable_path")
            self.assertIn("The file cannot be accessed by the system", err["message"])
            if os.name == "nt":
                self.assertIn("WinError 1920", err["message"])

            # Now verify --exclude prunes BEFORE lstat/stat is called
            stat_calls_on_bad.clear()

            def StrictFaultyLstat(target: os.PathLike[str] | str, *args: object, **kwargs: object) -> os.stat_result:
                p_str = os.fspath(target)
                if "\uf05cnpm" in p_str or p_str.endswith("grok"):
                    stat_calls_on_bad.append(p_str)
                    raise OSError(errno.EINVAL, "The file cannot be accessed by the system", p_str, 1920)
                return real_lstat(target, *args, **kwargs)

            with mock.patch("claude_empire.lint_engine.os.lstat", side_effect=StrictFaultyLstat), mock.patch(
                "claude_empire.lint_engine.os.stat", side_effect=FaultyStat
            ):
                report_ex = lint_engine.lint_vault(
                    vault, as_of="2026-11-16", exclude=["${APPDATA}/*"]
                )

            self.assertEqual(stat_calls_on_bad, [])
            self.assertEqual(report_ex["read_errors"], [])
            self.assertGreaterEqual(report_ex["summary"]["excluded_paths"], 1)
            self.assertEqual(report_ex["summary"]["issues_found"], 0)

    def test_knowledge_pack_attach_passes_lint_strict_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            pack_manifest = ROOT / "knowledge" / "stewardship" / "pack.json"

            # 1. Preview and apply init
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "init",
                    str(vault),
                    "--operation-id",
                    "init-pack-test",
                    "--generated-at",
                    "2026-11-16T12:00:00Z",
                ])
            self.assertEqual(rc, 0)
            init_plan = json.loads(buf.getvalue())
            with redirect_stdout(io.StringIO()):
                rc = cli.main([
                    "init",
                    str(vault),
                    "--operation-id",
                    "init-pack-test",
                    "--generated-at",
                    "2026-11-16T12:00:00Z",
                    "--apply",
                    "--approved-plan-sha256",
                    init_plan["approved_plan_sha256"],
                ])
            self.assertEqual(rc, 0)

            # Initialize git repo with baseline commit after init
            subprocess.run(["git", "init"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=vault, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial vault"], cwd=vault, check=True, capture_output=True)

            # 2. Preview and apply knowledge attach
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "knowledge",
                    "attach",
                    str(pack_manifest),
                    "--vault",
                    str(vault),
                    "--operation-id",
                    "attach-stewardship",
                    "--generated-at",
                    "2026-11-16T12:05:00Z",
                ])
            self.assertEqual(rc, 0)
            attach_plan = json.loads(buf.getvalue())
            with redirect_stdout(io.StringIO()):
                rc = cli.main([
                    "knowledge",
                    "attach",
                    str(pack_manifest),
                    "--vault",
                    str(vault),
                    "--operation-id",
                    "attach-stewardship",
                    "--generated-at",
                    "2026-11-16T12:05:00Z",
                    "--apply",
                    "--approved-plan-sha256",
                    attach_plan["approved_plan_sha256"],
                ])
            self.assertEqual(rc, 0)

            # Check version 0.2.2 and F24 content
            state_doc = json.loads(
                (vault / "wiki/library/legends-empire/stewardship/pack-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state_doc["version"], "0.2.2")
            gen_index = (vault / "wiki/library/legends-empire/stewardship/index.md").read_text(encoding="utf-8")
            self.assertIn("python -m cto_legends --home <home> handoff legends-empire", gen_index)
            ev_text = (vault / "wiki/library/legends-empire/stewardship/reference/evidence.md").read_text(encoding="utf-8")
            self.assertNotIn("Native Windows remains read-only", ev_text)
            self.assertIn("native Windows NTFS", ev_text)

            # 3. lint --strict must exit 0 with 0 issues
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "lint",
                    "--vault",
                    str(vault),
                    "--as-of",
                    "2026-11-16",
                    "--strict",
                ])
            self.assertEqual(rc, 0, buf.getvalue())
            lint_rep = json.loads(buf.getvalue())
            self.assertEqual(lint_rep["summary"]["issues_found"], 0, lint_rep)
            self.assertEqual(lint_rep["duplicate_basenames"], [])
            self.assertEqual(lint_rep["missing_frontmatter"], [])

            # 4. Run checkpoint on the attached knowledge pack operation
            buf = io.StringIO()
            err_buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(err_buf):
                rc = cli.main([
                    "checkpoint",
                    "attach-stewardship",
                    "--vault",
                    str(vault),
                    "--as-of",
                    "2026-11-16",
                ])
            self.assertEqual(rc, 0, f"stdout={buf.getvalue()} stderr={err_buf.getvalue()}")

    @unittest.skipUnless(os.name == "nt", "NTFS junctions require Windows (_winapi.CreateJunction)")
    def test_ntfs_junction_skipped_in_lint_walk_and_wiki_location(self) -> None:
        import _winapi

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            vault = base / "vault"
            _write_valid_note(vault / "wiki" / "index.md", "Index")

            outside = base / "outside-target"
            outside.mkdir(parents=True)
            (outside / "outside-secret.md").write_text("# Outside Secret\nNo frontmatter.\n", encoding="utf-8")

            junc_dir = vault / "wiki" / "junc-out"
            _winapi.CreateJunction(str(outside), str(junc_dir))

            report = lint_engine.lint_vault(vault, as_of="2026-11-16")
            self.assertEqual(report["summary"]["pages_scanned"], 1)
            self.assertEqual(report["missing_frontmatter"], [])
            self.assertEqual(report["orphans"], [])

    def test_resolver_suffix_index_equivalence_with_reference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            note_specs = {
                "wiki/index.md": ("Index", ["Home Index"]),
                "wiki/concepts/Alpha.md": ("Alpha", ["First Alpha"]),
                "wiki/concepts/deep/nested/Topic.md": ("Nested Topic", ["Deep/Topic"]),
                "wiki/entities/deep/nested/Topic.md": ("Entity Topic", ["Entity/Topic"]),
                "projects/team-a/sub/Shared.md": ("Shared A", ["Team A Shared"]),
                "projects/team-b/sub/Shared.md": ("Shared B", ["Team B Shared"]),
                "Archive/Team-C/Sub/shared.md": ("Shared C", []),
                "wiki/reports/v1.2/Summary.v1.md": ("Summary V1", ["Summary Alias"]),
            }
            for rel, (title, aliases) in note_specs.items():
                p = vault / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                alias_yaml = ""
                if aliases:
                    alias_yaml = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
                p.write_text(
                    f"---\n"
                    f"title: {title}\n"
                    f"type: concept\n"
                    f"status: evergreen\n"
                    f"created: 2026-11-16\n"
                    f"updated: 2026-11-16\n"
                    f"tags:\n"
                    f"  - test\n"
                    f"{alias_yaml}"
                    f"---\n\n"
                    f"# {title}\n",
                    encoding="utf-8",
                )

            non_md_specs = (
                "wiki/views/Board.canvas",
                "wiki/views/Board.base",
                "assets/diagrams/arch/Board.canvas",
                "assets/images/logo.png",
                "docs/reference/Makefile",
            )
            for rel in non_md_specs:
                p = vault / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("{}\n", encoding="utf-8")

            all_paths = lint_engine._walk_files(vault)
            targets: list[lint_engine._Target] = []
            for path in all_paths:
                rel = path.relative_to(vault).as_posix()
                page = (
                    lint_engine._page_metadata(path, rel, "wiki")
                    if path.suffix.casefold() == ".md"
                    else None
                )
                target = lint_engine._Target(path=rel, absolute=path, page=page)
                self.assertEqual(
                    target.without_link_suffix,
                    _reference_without_link_suffix(rel),
                )
                targets.append(target)

            resolver = lint_engine._Resolver(targets, "wiki")

            query_specs = [
                ("wiki/index.md", "sub/Shared", "wikilink", False),
                ("wiki/index.md", "sub/Shared.md", "wikilink", False),
                ("wiki/index.md", "team-a/sub/Shared", "wikilink", False),
                ("wiki/index.md", "team-b/sub/Shared.md", "wikilink", False),
                ("wiki/index.md", "Team-C/Sub/shared", "wikilink", False),
                ("wiki/index.md", "deep/nested/Topic", "wikilink", False),
                ("wiki/index.md", "nested/Topic", "wikilink", False),
                ("wiki/index.md", "concepts/deep/nested/Topic", "wikilink", False),
                ("wiki/index.md", "entities/deep/nested/Topic.md", "wikilink", False),
                ("wiki/index.md", "views/Board", "wikilink", False),
                ("wiki/index.md", "views/Board.canvas", "wikilink", False),
                ("wiki/index.md", "arch/Board", "wikilink", False),
                ("wiki/index.md", "arch/Board.canvas", "wikilink", False),
                ("wiki/index.md", "images/logo.png", "embed", False),
                ("wiki/index.md", "images/logo", "wikilink", False),
                ("wiki/index.md", "reference/Makefile", "wikilink", False),
                ("wiki/index.md", "v1.2/Summary.v1", "wikilink", False),
                ("wiki/index.md", "v1.2/Summary.v1.md", "wikilink", False),
                ("wiki/index.md", "First Alpha", "wikilink", False),
                ("wiki/index.md", "Deep/Topic", "wikilink", False),
                ("wiki/index.md", "/sub/Shared", "wikilink", False),
                ("wiki/concepts/Alpha.md", "./deep/nested/Topic.md", "markdown-link", True),
                ("wiki/concepts/Alpha.md", "../views/Board.base", "markdown-link", True),
                ("wiki/index.md", "", "wikilink", False),
                ("wiki/index.md", "nonexistent/slash/Target", "wikilink", False),
            ]
            for source, file_part, syntax, md_rel in query_specs:
                link = lint_engine._Link(
                    source=source,
                    line=1,
                    target=file_part,
                    file_part=file_part,
                    fragment=None,
                    fragment_kind=None,
                    syntax=syntax,
                    markdown_relative=md_rel,
                )
                expected = [t.path for t in _reference_resolve(resolver, link)]
                actual = [t.path for t in resolver.resolve(link)]
                self.assertEqual(
                    expected,
                    actual,
                    f"Mismatch for link {file_part!r} (md_rel={md_rel})",
                )

    def test_resolver_scale_3000_notes_2000_slash_links(self) -> None:
        base = Path("/virtual/vault")
        targets: list[lint_engine._Target] = []
        for idx in range(3000):
            bucket = idx % 30
            sub = idx % 10
            ext = ".md" if idx % 5 != 0 else (".canvas" if idx % 2 == 0 else ".png")
            rel = f"area_{bucket:02d}/group_{sub:02d}/item_{idx:04d}{ext}"
            targets.append(
                lint_engine._Target(path=rel, absolute=base / rel, page=None)
            )

        links: list[lint_engine._Link] = []
        for idx in range(2000):
            target_idx = (idx * 7) % 3000
            sub = target_idx % 10
            if idx % 4 == 0:
                fp = f"group_{sub:02d}/item_{target_idx:04d}"
            elif idx % 4 == 1:
                fp = f"group_{sub:02d}/item_{target_idx:04d}.md"
            elif idx % 4 == 2:
                bucket = target_idx % 30
                fp = f"area_{bucket:02d}/group_{sub:02d}/item_{target_idx:04d}"
            else:
                fp = f"missing_group/item_{idx:04d}"
            links.append(
                lint_engine._Link(
                    source="wiki/index.md",
                    line=idx + 1,
                    target=fp,
                    file_part=fp,
                    fragment=None,
                    fragment_kind=None,
                    syntax="wikilink",
                    markdown_relative=False,
                )
            )

        t0 = time.perf_counter()
        resolver = lint_engine._Resolver(targets, "wiki")
        resolved_counts = [len(resolver.resolve(link)) for link in links]
        elapsed = time.perf_counter() - t0

        self.assertLess(
            elapsed,
            5.0,
            f"Resolver scale test (3000 targets, 2000 slash links) took {elapsed:.3f}s",
        )
        self.assertEqual(len(resolved_counts), 2000)
        self.assertGreater(sum(resolved_counts), 1000)

        # Spot-check equivalence on a sample of 50 links against reference resolver
        for link in links[:50]:
            self.assertEqual(
                [t.path for t in _reference_resolve(resolver, link)],
                [t.path for t in resolver.resolve(link)],
            )

    def test_lint_reuses_fresh_ontology_index_and_never_modifies_vault_meta(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            _write_valid_note(vault / "wiki" / "index.md", "Index")
            dup1 = vault / "wiki" / "dup1.md"
            dup1.write_text(
                "---\n"
                "title: Dup 1\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-11-16\n"
                "updated: 2026-11-16\n"
                "tags:\n"
                "  - test\n"
                "entity_id: biz-acme\n"
                "---\n\n"
                "# Dup 1\n",
                encoding="utf-8",
            )
            dup2 = vault / "wiki" / "dup2.md"
            dup2.write_text(
                "---\n"
                "title: Dup 2\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-11-16\n"
                "updated: 2026-11-16\n"
                "tags:\n"
                "  - test\n"
                "entity_id: biz-acme\n"
                "---\n\n"
                "# Dup 2\n",
                encoding="utf-8",
            )

            idx_path = vault / ".vault-meta" / "ontology" / "index.json"
            self.assertFalse(idx_path.exists())

            # 1. Lint when no index.json exists: computes findings in memory, never writes .vault-meta
            rep1 = lint_engine.lint_vault(vault, as_of="2026-11-16")
            self.assertFalse(idx_path.exists())
            self.assertFalse((vault / ".vault-meta").exists())
            self.assertIn(
                "ontology_duplicate_entity_id",
                [f["code"] for f in rep1["ontology_findings"]],
            )

            # 2. Build index.json explicitly, then verify lint reuses it without calling build_index
            ont.build_index(vault, as_of="2026-11-16", write_file=True)
            self.assertTrue(idx_path.is_file())
            before_stat = idx_path.stat()
            before_sha = hashlib.sha256(idx_path.read_bytes()).hexdigest()

            with mock.patch(
                "claude_empire.ontology.build_index", wraps=ont.build_index
            ) as spy_build:
                rep2 = lint_engine.lint_vault(vault, as_of="2026-11-16")
                rep3 = lint_engine.lint_vault(vault)
                self.assertEqual(spy_build.call_count, 0)

            self.assertEqual(rep2["ontology_findings"], rep1["ontology_findings"])
            self.assertEqual(rep3["ontology_findings"], rep1["ontology_findings"])
            after_stat = idx_path.stat()
            after_sha = hashlib.sha256(idx_path.read_bytes()).hexdigest()
            self.assertEqual(before_stat.st_size, after_stat.st_size)
            self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)
            self.assertEqual(before_sha, after_sha)

            # 3. Make cached index stale by adding a new ontology error; lint rebuilds in memory without touching disk
            bad_note = vault / "wiki" / "bad-cert.md"
            bad_note.write_text(
                "---\n"
                "title: Bad Cert\n"
                "type: entity\n"
                "status: active\n"
                "created: 2026-11-16\n"
                "updated: 2026-11-16\n"
                "tags:\n"
                "  - test\n"
                "entity_id: biz-other\n"
                "certified_owner:\n"
                "  - fact.missing_field\n"
                "---\n\n"
                "# Bad Cert\n",
                encoding="utf-8",
            )
            with mock.patch(
                "claude_empire.ontology.build_index", wraps=ont.build_index
            ) as spy_build_stale:
                rep4 = lint_engine.lint_vault(vault, as_of="2026-11-16")
                self.assertEqual(spy_build_stale.call_count, 1)
                self.assertEqual(
                    spy_build_stale.call_args.kwargs.get("write_file"), False
                )

            self.assertIn(
                "ontology_certified_without_value",
                [f["code"] for f in rep4["ontology_findings"]],
            )
            stale_stat = idx_path.stat()
            stale_sha = hashlib.sha256(idx_path.read_bytes()).hexdigest()
            self.assertEqual(before_stat.st_size, stale_stat.st_size)
            self.assertEqual(before_stat.st_mtime_ns, stale_stat.st_mtime_ns)
            self.assertEqual(before_sha, stale_sha)

    def test_lint_surfaces_ontology_error_instead_of_swallowing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td) / "vault"
            (vault / ".obsidian").mkdir(parents=True)
            _write_valid_note(vault / "wiki" / "index.md", "Index")
            baseline = lint_engine.lint_vault(vault, as_of="2026-11-16")
            expected_keys = set(baseline.keys())

            # 1. Invalid .claude-empire.json ontology config surfaces in configuration_errors
            (vault / ".claude-empire.json").write_text(
                json.dumps({"ontology.roots": "not-a-list"}),
                encoding="utf-8",
            )
            rep_cfg = lint_engine.lint_vault(vault, as_of="2026-11-16")
            self.assertEqual(set(rep_cfg.keys()), expected_keys)
            self.assertEqual(len(rep_cfg["configuration_errors"]), 1)
            cfg_err = rep_cfg["configuration_errors"][0]
            self.assertEqual(cfg_err["path"], ".claude-empire.json")
            self.assertEqual(cfg_err["code"], "ONTOLOGY_CONFIG_INVALID")
            self.assertIn("OntologyRuleError", cfg_err["message"])
            self.assertGreaterEqual(rep_cfg["summary"]["issues_found"], 1)

            # Remove invalid config and test OSError surfacing in read_errors
            (vault / ".claude-empire.json").unlink()
            with mock.patch(
                "claude_empire.ontology.load_or_build_index",
                side_effect=OSError(errno.EIO, "simulated ontology I/O failure"),
            ):
                rep_io = lint_engine.lint_vault(vault, as_of="2026-11-16")
            self.assertEqual(set(rep_io.keys()), expected_keys)
            self.assertEqual(len(rep_io["read_errors"]), 1)
            io_err = rep_io["read_errors"][0]
            self.assertEqual(io_err["path"], ".vault-meta/ontology/index.json")
            self.assertIn("simulated ontology I/O failure", io_err["message"])
            self.assertGreaterEqual(rep_io["summary"]["issues_found"], 1)


if __name__ == "__main__":
    unittest.main()
