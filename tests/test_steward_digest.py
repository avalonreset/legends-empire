#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legends_vault_steward.cli import main

VALID_TEMPORAL_KEYS = {
    "job_id",
    "title",
    "queue",
    "interpreter",
    "script",
    "args",
    "cwd",
    "timeout_s",
    "wait_for_worker_s",
    "notify_channel",
    "cron",
    "crons",
    "interval",
    "interval_offset",
    "timezone",
    "jitter",
    "overlap",
    "catchup_window",
    "pause_on_failure",
    "remaining_actions",
    "paused",
    "note",
    "kind",
    "session_id",
    "spawn",
    "agent",
    "effort",
    "task_type",
    "project",
    "workspace",
    "message",
    "model",
    "code",
    "omnigent_ctl",
    "description",
}


class StewardDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        self.vault.mkdir()

    def write_vault_file(self, rel: str, content: str) -> Path:
        target = self.vault / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_config(self, data: dict) -> Path:
        cfg_path = self.base / "overlay.json"
        cfg_path.write_text(json.dumps(data), encoding="utf-8")
        return cfg_path

    def test_digest_first_run_no_change_and_new_high_without_ratchet(self) -> None:
        self.write_vault_file("AGENTS.md", "# Agents\n" "@missing-1.md\n")
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        out_dir = self.vault / ".steward" / "digest"

        # 1. First run: baseline bootstrap -> exit 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc1, 0)
        d1 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        md1 = (out_dir / "digest.md").read_text(encoding="utf-8")
        self.assertEqual(d1["schema"], "legends.vault-steward.digest/v1")
        self.assertEqual(d1["status"], "baseline")
        self.assertTrue(d1["first_run"])
        self.assertIn("baseline", md1.lower())
        self.assertLessEqual(len(md1.encode("utf-8")), 1024)
        self.assertEqual(d1["high_keys"], ["entry.broken_import|AGENTS.md|missing-1.md|agents"])

        # 2. Second run with no change -> exit 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc2 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc2, 0)
        d2 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "ok")
        self.assertEqual(d2["new_high_keys"], [])

        # 3. Third run with a new high in the same file -> exit 12
        self.write_vault_file("AGENTS.md", "# Agents\n" "@missing-1.md\n" "@missing-2.md\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc3 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc3, 12)
        d3 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d3["status"], "regression")
        self.assertEqual(d3["new_high_keys"], ["entry.broken_import|AGENTS.md|missing-2.md|agents"])

        # 4. Fourth run (Day N+1) with the unfixed regression still present -> still exits 12 (no upward ratchet!)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc4 = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc4, 12)

    def test_digest_md_hard_cap_1024_bytes_with_plus_n_more(self) -> None:
        imports = "\n".join(f"@missing/very-long-target-path-number-{i:03d}.md" for i in range(40))
        self.write_vault_file("AGENTS.md", f"# Agents\n{imports}\n")
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        out_dir = self.base / "digest-cap"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc, 0)
        md_bytes = (out_dir / "digest.md").read_bytes()
        self.assertLessEqual(len(md_bytes), 1024)
        md_text = md_bytes.decode("utf-8")
        self.assertRegex(md_text, r"\+\d+ more")

    def test_jobs_template_validates_as_json_with_expected_keys(self) -> None:
        tpl_path = ROOT / "templates" / "legends-temporal" / "steward-digest.jobs.json"
        self.assertTrue(tpl_path.is_file())
        raw_text = tpl_path.read_text(encoding="utf-8")
        self.assertNotIn("C:\\Users\\", raw_text)
        data = json.loads(raw_text)
        self.assertIn("jobs", data)
        self.assertIsInstance(data["jobs"], list)
        self.assertEqual(len(data["jobs"]), 1)
        job = data["jobs"][0]
        self.assertTrue(set(job.keys()).issubset(VALID_TEMPORAL_KEYS))
        self.assertEqual(job["job_id"], "empire-steward-digest")
        self.assertEqual(job["kind"], "script")
        self.assertNotEqual(job["kind"], "agent")
        self.assertEqual(job["cron"], "0 7 * * *")
        self.assertEqual(job["timezone"], "America/New_York")
        self.assertEqual(job["overlap"], "SKIP")
        self.assertEqual(job["script"], "-m")
        self.assertIn("legends-vault-stewardship", job["args"])
        self.assertIn("digest", job["args"])

    def test_p3_expired_directive_in_entry_chain_and_law_paths_stays_high_and_exits_12(self) -> None:
        from legends_vault_steward.digest import merge_and_dedupe_findings

        # Verify merge_and_dedupe_findings keeps the higher severity on key collision
        deduped = merge_and_dedupe_findings(
            [{"code": "law.expired_directive", "severity": "medium", "path": "AGENTS.md", "date": "2026-10-20", "line": 2, "message": "m"}],
            [{"code": "law.expired_directive", "severity": "high", "path": "AGENTS.md", "date": "2026-10-20", "line": 2, "message": "m"}],
        )
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["severity"], "high")

        # End-to-end digest where law_paths overlaps entry_harnesses (like multivendor overlay)
        self.write_vault_file("AGENTS.md", "# Core Agents\nAll policies active.\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"], "core": "AGENTS.md"}],
                "law_paths": ["AGENTS.md"],
            }
        )
        out_dir = self.vault / ".steward" / "digest-p3"

        # 1. Baseline run with clean AGENTS.md -> exit 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc1, 0)

        # 2. Add expired directive to AGENTS.md -> must be high severity in digest and exit 12
        self.write_vault_file(
            "AGENTS.md",
            "# Core Agents\nTemporary routing through 2026-10-20 applies here.\n",
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc2 = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc2, 12)
        d2 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "regression")
        self.assertEqual(d2["code_counts"].get("law.expired_directive"), 1)
        self.assertIn("law.expired_directive|AGENTS.md|2026-10-20", d2["high_keys"])
        self.assertIn("law.expired_directive|AGENTS.md|2026-10-20", d2["new_high_keys"])

    def test_rec4_digest_missing_previous_exit_10_and_update_baseline(self) -> None:
        self.write_vault_file("AGENTS.md", "# Core\n")
        cfg = self.write_config(
            {"entry_harnesses": [{"id": "agents", "entry": ["AGENTS.md"]}]}
        )
        out_dir = self.vault / ".steward" / "digest-rec4"

        # Missing --previous file must exit 10
        missing_prev = self.base / "does-not-exist.json"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc_missing = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--previous",
                    str(missing_prev),
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc_missing, 10)

        # Bootstrap baseline (exit 0)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc_init = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc_init, 0)

        # Introduce new high finding and run with --update-baseline: exits 12 AND updates baseline
        self.write_vault_file("AGENTS.md", "# Core\n" "@missing-after.md\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc_upd = main(
                [
                    "digest",
                    str(self.vault),
                    "--config",
                    str(cfg),
                    "--out",
                    str(out_dir),
                    "--update-baseline",
                    "--as-of",
                    "2026-11-16",
                ]
            )
        self.assertEqual(rc_upd, 12)
        baseline_doc = json.loads((out_dir / "digest.baseline.json").read_text(encoding="utf-8"))
        self.assertIn("entry.broken_import|AGENTS.md|missing-after.md|agents", baseline_doc["high_keys"])

        # Subsequent run without --update-baseline now sees no new highs against updated baseline -> exits 0
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc_after = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-16"])
        self.assertEqual(rc_after, 0)

    def test_multiharness_core_unreached_and_encoding_stay_distinct_and_regress(self) -> None:
        from legends_vault_steward.digest import high_key, merge_and_dedupe_findings

        # Unit check: distinct harnesses on the same path must not collapse
        f_agy = {
            "code": "entry.core_unreached",
            "severity": "high",
            "path": "fleet/Core.md",
            "harness": "agy",
            "message": "Harness 'agy' chain did not reach declared core fleet/Core.md",
        }
        f_codex = {
            "code": "entry.core_unreached",
            "severity": "high",
            "path": "fleet/Core.md",
            "harness": "codex-muse",
            "message": "Harness 'codex-muse' chain did not reach declared core fleet/Core.md",
        }
        f_enc_claude = {
            "code": "entry.encoding",
            "severity": "high",
            "path": "Shared.md",
            "byte_offset": 12,
            "harness": "claude",
            "message": "Invalid UTF-8 in Shared.md at byte offset 12",
        }
        f_enc_gemini = {
            "code": "entry.encoding",
            "severity": "high",
            "path": "Shared.md",
            "byte_offset": 12,
            "harness": "gemini",
            "message": "Invalid UTF-8 in Shared.md at byte offset 12",
        }
        merged = merge_and_dedupe_findings([f_agy, f_codex, f_enc_claude, f_enc_gemini])
        self.assertEqual(len(merged), 4)
        self.assertEqual(
            {high_key(item) for item in merged},
            {
                "entry.core_unreached|fleet/Core.md|agy",
                "entry.core_unreached|fleet/Core.md|codex-muse",
                "entry.encoding|Shared.md|12|claude",
                "entry.encoding|Shared.md|12|gemini",
            },
        )

        # End-to-end digest: second harness losing core reachability must exit 12 (regression)
        self.write_vault_file("fleet/Core.md", "# Core\n")
        self.write_vault_file("AGY.md", "# AGY without core import\n")
        self.write_vault_file("CODEX.md", "@fleet/Core.md\n")
        cfg = self.write_config(
            {
                "entry_harnesses": [
                    {"id": "agy", "entry": ["AGY.md"], "core": "fleet/Core.md"},
                    {"id": "codex-muse", "entry": ["CODEX.md"], "core": "fleet/Core.md"},
                ]
            }
        )
        out_dir = self.vault / ".steward" / "digest-harnesses"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-21"])
        self.assertEqual(rc1, 0)
        d1 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d1["high_keys"], ["entry.core_unreached|fleet/Core.md|agy"])

        # Now codex-muse also drops @fleet/Core.md -> must detect regression
        self.write_vault_file("CODEX.md", "# Codex without core import\n")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc2 = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-21"])
        self.assertEqual(rc2, 12)
        d2 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "regression")
        self.assertEqual(d2["code_counts"].get("entry.core_unreached"), 2)
        self.assertEqual(d2["new_high_keys"], ["entry.core_unreached|fleet/Core.md|codex-muse"])
        self.assertEqual(
            d2["high_keys"],
            [
                "entry.core_unreached|fleet/Core.md|agy",
                "entry.core_unreached|fleet/Core.md|codex-muse",
            ],
        )

    def test_expired_fresh_until_collapses_observe_and_entry_audit_duplicates(self) -> None:
        from legends_vault_steward.digest import merge_and_dedupe_findings

        obs_item = {
            "code": "freshness.expired_fresh_until",
            "severity": "high",
            "path": "HOT.md",
            "surface": "hot",
            "fresh_until": "2026-11-24",
            "message": "hot declared fresh_until 2026-11-24 has passed",
        }
        ea_claude = {
            "code": "freshness.expired_fresh_until",
            "severity": "high",
            "path": "HOT.md",
            "harness": "claude",
            "fresh_until": "2026-11-24",
            "message": "Entry file HOT.md declared fresh_until 2026-11-24 has passed",
        }
        ea_gemini = {
            "code": "freshness.expired_fresh_until",
            "severity": "high",
            "path": "HOT.md",
            "harness": "gemini",
            "fresh_until": "2026-11-24",
            "message": "Entry file HOT.md declared fresh_until 2026-11-24 has passed",
        }
        # Both input orders must collapse to 1 finding preferring the named surface
        for merged in (
            merge_and_dedupe_findings([obs_item], [ea_claude, ea_gemini]),
            merge_and_dedupe_findings([ea_claude, ea_gemini], [obs_item]),
        ):
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0].get("surface"), "hot")
            self.assertNotIn("harness", merged[0])

        # End-to-end digest: HOT.md imported by two harnesses and declared as surface="hot"
        self.write_vault_file("HOT.md", "---\nupdated: 2026-11-21\nfresh_until: 2026-11-24\n---\n# Hot\n")
        self.write_vault_file("CLAUDE.md", "@HOT.md\n")
        self.write_vault_file("GEMINI.md", "@HOT.md\n")
        cfg = self.write_config(
            {
                "surfaces": [{"id": "hot", "path": "HOT.md", "role": "hot", "max_age_days": 14}],
                "entry_harnesses": [
                    {"id": "claude", "entry": ["CLAUDE.md"]},
                    {"id": "gemini", "entry": ["GEMINI.md"]},
                ],
            }
        )
        out_dir = self.vault / ".steward" / "digest-hot-dedupe"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = main(["digest", str(self.vault), "--config", str(cfg), "--out", str(out_dir), "--as-of", "2026-11-25"])
        self.assertEqual(rc, 0)
        doc = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        md = (out_dir / "digest.md").read_text(encoding="utf-8")
        self.assertEqual(doc["code_counts"].get("freshness.expired_fresh_until"), 1)
        self.assertEqual(doc["high_keys"], ["freshness.expired_fresh_until|HOT.md|2026-11-24"])
        self.assertEqual(md.count("freshness.expired_fresh_until"), 1)

    def test_expired_fresh_until_harness_only_order_independence_and_ontology_key_regressions(self) -> None:
        from legends_vault_steward.digest import high_key, merge_and_dedupe_findings

        # 1. Harness-only expired_fresh_until (e.g., ~/... or skipped dir) is order-independent and omits harness in high_key
        ea_claude = {
            "code": "freshness.expired_fresh_until",
            "severity": "high",
            "path": "~/.claude/HOT.md",
            "harness": "claude",
            "fresh_until": "2026-11-20",
            "message": "Entry file ~/.claude/HOT.md declared fresh_until 2026-11-20 has passed",
        }
        ea_gemini = {
            "code": "freshness.expired_fresh_until",
            "severity": "high",
            "path": "~/.claude/HOT.md",
            "harness": "gemini",
            "fresh_until": "2026-11-20",
            "message": "Entry file ~/.claude/HOT.md declared fresh_until 2026-11-20 has passed",
        }
        m_ab = merge_and_dedupe_findings([ea_claude, ea_gemini])
        m_ba = merge_and_dedupe_findings([ea_gemini, ea_claude])
        self.assertEqual(m_ab, m_ba)
        self.assertEqual(len(m_ab), 1)
        self.assertNotIn("harness", m_ab[0])
        self.assertEqual(high_key(ea_claude), "freshness.expired_fresh_until|~/.claude/HOT.md|2026-11-20")
        self.assertEqual(high_key(m_ab[0]), "freshness.expired_fresh_until|~/.claude/HOT.md|2026-11-20")

        # 2. high_key preserves harness even when harness == subkey
        same_sub = {
            "code": "entry.broken_import",
            "severity": "high",
            "path": "CLAUDE.md",
            "target": "core.md",
            "harness": "core.md",
        }
        self.assertEqual(high_key(same_sub), "entry.broken_import|CLAUDE.md|core.md|core.md")

        # 3. End-to-end ontology.conflict & ontology_certified_without_value on same note stay distinct and regress
        self.write_vault_file("wiki/acme.md", "# Acme\n")
        idx_dir = self.vault / ".vault-meta" / "ontology"
        idx_dir.mkdir(parents=True)
        idx_file = idx_dir / "index.json"
        idx_file.write_text(
            json.dumps(
                {
                    "facts": {
                        "acme.domain": {"state": "conflict", "path": "wiki/acme.md"},
                    },
                    "findings": [
                        {
                            "code": "ontology_certified_without_value",
                            "path": "wiki/acme.md",
                            "detail": "key 'fact.domain' listed in certification but missing from frontmatter",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        out_dir = self.vault / ".steward" / "digest-ont"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc1 = main(["digest", str(self.vault), "--out", str(out_dir), "--as-of", "2026-11-21"])
        self.assertEqual(rc1, 0)
        d1 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertIn("ontology.conflict|wiki/acme.md|acme.domain", d1["high_keys"])

        # Second conflicting fact and second missing certified value on the SAME note must regress (exit 12)
        idx_file.write_text(
            json.dumps(
                {
                    "facts": {
                        "acme.domain": {"state": "conflict", "path": "wiki/acme.md"},
                        "acme.phone": {"state": "conflict", "path": "wiki/acme.md"},
                    },
                    "findings": [
                        {
                            "code": "ontology_certified_without_value",
                            "path": "wiki/acme.md",
                            "detail": "key 'fact.domain' listed in certification but missing from frontmatter",
                        },
                        {
                            "code": "ontology_certified_without_value",
                            "path": "wiki/acme.md",
                            "detail": "key 'fact.phone' listed in certification but missing from frontmatter",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc2 = main(["digest", str(self.vault), "--out", str(out_dir), "--as-of", "2026-11-21"])
        self.assertEqual(rc2, 12)
        d2 = json.loads((out_dir / "digest.json").read_text(encoding="utf-8"))
        self.assertEqual(d2["status"], "regression")
        self.assertEqual(d2["code_counts"].get("ontology.conflict"), 2)
        self.assertEqual(d2["code_counts"].get("ontology_certified_without_value"), 2)
        self.assertIn("ontology.conflict|wiki/acme.md|acme.phone", d2["new_high_keys"])
        self.assertEqual(len(d2["new_high_keys"]), 2)


if __name__ == "__main__":
    unittest.main()
