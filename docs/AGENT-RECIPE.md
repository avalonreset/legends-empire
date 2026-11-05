# Operating legends-empire

This is an on-demand module recipe, not a registered skill. The central `cto-legends` router owns discovery (`cto-legends handoff legends-empire` or alias `cto-legends handoff legends-vault-stewardship`).

## Select knowledge and workflow first

Read [the installation guide](install-guide.md), [vault stewardship workflow](stewardship.md), [knowledge packs contract](knowledge-packs.md), [research evidence handoff](RESEARCH-EVIDENCE-HANDOFF.md), and [optional Home adapter](home-adapter.md). Always separate the installed `legends-empire` module directory from the user-selected Obsidian vault.

## Readiness

1. Verify Python 3.11+ and package coherence from the module root:
   ```bash
   python scripts/claude-empire.py --version
   python scripts/claude-empire.py package validate
   python scripts/claude-empire.py contracts --check-only
   ```
2. Verify vault stewardship readiness (read-only):
   ```bash
   python scripts/claude-empire.py steward doctor
   python -m legends_vault_steward doctor
   ```
3. Inspect a selected user vault before any write:
   ```bash
   python scripts/claude-empire.py doctor --vault /path/to/vault
   python scripts/claude-empire.py lint --vault /path/to/vault --format json
   ```
4. Every mutating workflow (`init`, `adopt`, `migrate`, `capture apply`, `mode set`, `extension dragonscale`, `knowledge attach`, `home-adapter`, `transaction apply`) runs in preview/dry-run mode first and emits a canonical `approved_plan_sha256`. Applying requires `--apply --approved-plan-sha256 <sha256>` with the exact pinned `--operation-id` and `--generated-at`.

## Core CLI and workflow reference (`scripts/claude-empire.py`)

| Workflow / Capability | Command | Behavior and safety boundary |
|---|---|---|
| Core readiness | `python scripts/claude-empire.py doctor --vault <vault>` | Checks vault existence, `.obsidian`, `wiki/`, `.raw/`, writable `.vault-meta/`, and lock state |
| Deterministic vault lint | `python scripts/claude-empire.py lint --vault <vault> [--format json\|markdown] [--strict]` | Read-only link resolution, frontmatter, orphan, and provenance freshness inspection |
| Initialize new vault | `python scripts/claude-empire.py init <path> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Preview-first creation of a separate Obsidian vault with `.claude-empire.json`, `wiki/`, and `.raw/` |
| Adopt existing vault | `python scripts/claude-empire.py adopt <path> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Preview-first additive adoption preserving existing user notes and Obsidian config |
| Migrate legacy layout | `python scripts/claude-empire.py migrate --vault <vault> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Additive provenance ledger migration; preserves `.raw/.manifest.json` byte-for-byte |
| Source capture | `python scripts/claude-empire.py capture plan\|apply --vault <vault> ...` | Plans and copies local `inbox/` sources into immutable content-addressed `.raw/` storage |
| Methodology routing | `python scripts/claude-empire.py mode get\|set --vault <vault> ...` | Reads or transactionally configures `generic`, `lyt`, `para`, or `zettelkasten` mode |
| Inspect transaction | `python scripts/claude-empire.py transaction inspect --vault <vault> <bundle.json>` | Validates `claude-empire.transaction.v1` bundle preconditions and computes `approval_sha256` |
| Apply transaction | `python scripts/claude-empire.py transaction apply --vault <vault> <bundle.json> --approved-plan-sha256 HASH` | Applies a reviewed bundle using atomic handle/POSIX confinement and crash journals |
| Recover transaction | `python scripts/claude-empire.py transaction recover --vault <vault>` | Rolls back interrupted operations from `.vault-meta/transactions/` journals |
| Explicit Git checkpoint | `python scripts/claude-empire.py checkpoint <operation-id> --vault <vault> [--as-of YYYY-MM-DD]` | Commits one completed transaction through a temporary Git index after blob verification |
| Research evidence intake | `python scripts/claude-empire.py research-evidence verify\|plan <package> --workspace <id> [--vault <vault>]` | Offline verification and intake planning for DataForSEO and Firecrawl v1 evidence banks |
| Package & contract gates | `python scripts/claude-empire.py package validate` / `contracts --verify` | Verifies router-native skill allowlist, hooks schema, and capability contracts |
| Reproducible release ZIP | `python scripts/claude-empire.py release build --output <zip>` / `release audit <zip>` | Builds and self-audits a deterministic stored ZIP from a clean Git worktree |

## Vault stewardship (`legends-vault-stewardship`)

| Goal | Command | Behavior |
|---|---|---|
| Stewardship self-check | `python scripts/claude-empire.py steward doctor` (or `python -m legends_vault_steward doctor`) | Confirms read-only command surface and default stewardship configuration |
| Observe vault & queue | `python scripts/claude-empire.py steward scan <vault> --review-limit 5 --out <report-dir> --format both` | Read-only inventory, link/freshness evidence, and bounded review queue (`observe.json` + `observe.md`) |
| Empire profile scan | `python scripts/claude-empire.py steward scan <vault> --profile empire --out <report-dir>` | Checks portable `wiki/index.md` and `wiki/hot.md` contracts in addition to standard link checks |
| Baseline delta verification | `python scripts/claude-empire.py steward scan <vault> --baseline <before/observe.json> --expect-changed <path>` | Compares post-campaign state against a prior baseline and exits `12` on unexpected note changes |

## Knowledge packs (`knowledge-packs`)

| Goal | Command | Behavior |
|---|---|---|
| Preview pack attachment | `python scripts/claude-empire.py knowledge attach knowledge/stewardship/pack.json --vault <vault> --operation-id ID --generated-at ISO` | Previews attaching or updating a `legends.knowledge-pack/v1` shelf under `wiki/library/` |
| Apply reviewed pack | `python scripts/claude-empire.py knowledge attach knowledge/stewardship/pack.json --vault <vault> --operation-id ID --generated-at ISO --approved-plan-sha256 HASH --apply` | Writes `reference/`, `pack-state.json`, and initial user-owned `index.md`; refuses on locally edited references |

## Optional AI Marketing Hub Home adapter (`home-adapter`)

| Goal | Command | Behavior |
|---|---|---|
| Prepare shared-root slots | `python scripts/claude-empire.py home-adapter prepare --vault <vault> --operation-id ID --generated-at ISO` | Previews `EMPIRE-ENTRY.md` and Home-compatible directory slot markers |
| Scaffold project/company/brand | `python scripts/claude-empire.py home-adapter scaffold --vault <vault> --spec <json> --operation-id ID --generated-at ISO` | Previews canonical `Projects/Active/<id>/` and `Workspaces/` records from an explicit JSON spec |
| Inspect separate Home | `python scripts/claude-empire.py home-adapter inspect --home <home-root>` | Verifies pinned Home v0.5.2 core digests without modifying files |
| Plan / apply / detach binding | `python scripts/claude-empire.py home-adapter plan\|apply\|detach\|list\|check ...` | Manages reference-only bindings under `wiki/integrations/aimh-home/<binding-id>/` |

## EDR and Security Guardrails

- **Windows Defender, EDR, and Anti-Ransomware Guardrails**:
  1. Never spawn background shells, hidden console windows, or execution-policy bypass shell wrappers.
  2. Never read local browser profile databases, DPAPI stores, or third-party credential stores outside the explicit project workspace.
  3. Never print raw secret values or personal tokens to stdout, logs, or Markdown receipts.
  4. Never bypass the transaction engine with uncontrolled bulk file-overwrite loops; all multi-file writes are bounded (`BATCH_SIZE <= 150`), symlink/reparse-refusing, and require `--approved-plan-sha256` review.
  5. Keep stewardship `--out` report directories either outside the vault or inside `<vault>/.steward/` only.
