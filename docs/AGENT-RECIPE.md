# Operating legends-empire

This is an on-demand module recipe, not a registered skill. The central `cto-legends` router owns discovery (`python -m cto_legends --home <cto-legends-home> handoff legends-empire` or alias `python -m cto_legends --home <cto-legends-home> handoff legends-vault-stewardship`; bare `cto-legends ...` is an optional shorthand when on `PATH`).

## Select knowledge and workflow first

Read [the installation guide](install-guide.md), [vault stewardship workflow](stewardship.md), [knowledge packs contract](knowledge-packs.md), [research evidence handoff](RESEARCH-EVIDENCE-HANDOFF.md), [ontology engine guide](ontology.md), and [optional Home adapter](home-adapter.md). Always separate the installed `legends-empire` module directory from the user-selected Obsidian vault.

All commands can be invoked from any working directory and without `Scripts` on `PATH` via `python -m cto_legends --home <cto-legends-home> run legends-empire -- <subcommand> ...` (or `legends-vault-stewardship`). Source-tree forms (`python scripts/claude-empire.py ...` and `python -m legends_vault_steward ...`) require the module checkout root as current working directory (CWD).

## Readiness

1. Verify Python 3.11+ and package coherence (via the PATH- and CWD-independent router, or from the module root as CWD):
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-empire -- --version
   python -m cto_legends --home <cto-legends-home> run legends-empire -- package validate
   python -m cto_legends --home <cto-legends-home> run legends-empire -- contracts --check-only

   # Or from the module checkout root (requires the repo root as CWD):
   python scripts/claude-empire.py --version
   python scripts/claude-empire.py package validate
   python scripts/claude-empire.py contracts --check-only
   ```
2. Verify vault stewardship readiness (read-only):
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-vault-stewardship -- steward doctor

   # Or from the module checkout root (requires the repo root as CWD):
   python scripts/claude-empire.py steward doctor
   python -m legends_vault_steward doctor
   ```
3. Inspect a selected user vault before any write:
   ```bash
   python -m cto_legends --home <cto-legends-home> run legends-empire -- doctor --vault /path/to/vault
   python -m cto_legends --home <cto-legends-home> run legends-empire -- lint --vault /path/to/vault --format json
   ```
4. Mutating workflows with built-in preview/apply modes (`init`, `adopt`, `migrate`, `capture apply`, `mode set`, `extension dragonscale`, `knowledge attach`, `home-adapter prepare|compose|scaffold|install|upgrade|detach`) run in preview/dry-run mode first and emit a canonical `approved_plan_sha256`; applying requires `--apply --approved-plan-sha256 <sha256>` with the exact pinned `--operation-id` and `--generated-at`. Separate apply subcommands (`transaction apply` after `transaction inspect` which emits `approval_sha256`, and `home-adapter apply` after `home-adapter plan`) take `--approved-plan-sha256 <sha256>` directly without `--apply`. `ontology propose` and `ontology certify` require `--generated-at <ISO-UTC>`, have no `--apply` or `--approved-plan-sha256` flags, and write transaction bundles into the derived `.vault-meta/ontology/bundles/` cache to be applied via `transaction apply`.

## Core CLI and workflow reference (`python -m cto_legends --home <cto-legends-home> run legends-empire -- ...` or `python scripts/claude-empire.py ...` from repo CWD)

| Workflow / Capability | Command | Behavior and safety boundary |
|---|---|---|
| Core readiness | `python scripts/claude-empire.py doctor --vault <vault>` | Checks vault existence, `.obsidian`, `wiki/`, `.raw/`, writable `.vault-meta/`, and lock state |
| Deterministic vault lint | `python scripts/claude-empire.py lint --vault <vault> [--format json\|markdown] [--strict] [--as-of YYYY-MM-DD] [--exclude GLOB]` | Read-only link resolution, frontmatter, orphan, and provenance freshness inspection |
| Initialize new vault | `python scripts/claude-empire.py init <path> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Preview-first creation of a separate Obsidian vault with `.claude-empire.json`, `wiki/`, and `.raw/` |
| Adopt existing vault | `python scripts/claude-empire.py adopt <path> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Preview-first additive adoption preserving existing user notes and Obsidian config |
| Migrate legacy layout | `python scripts/claude-empire.py migrate --vault <vault> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Additive provenance ledger migration; preserves `.raw/.manifest.json` byte-for-byte |
| Source capture | `python scripts/claude-empire.py capture plan\|apply --vault <vault> [--inbox DIR] [--max-items N] [--max-total-bytes N] [--max-file-bytes N] [--operation-id ID] [--generated-at ISO] [--approved-plan-sha256 HASH] [--apply] [sources ...]` | Plans and copies local `inbox/` sources into immutable content-addressed `.raw/` storage |
| Capture adapter manifest | `python scripts/claude-empire.py capture adapters` | Emits declared capture adapter maturity and offline/consent boundaries |
| External capture plan | `python scripts/claude-empire.py capture external-plan <url\|image\|pdf\|youtube\|epub\|ocr> <source> [--approved-host HOST]` | Creates an inert, consent-gated external capture adapter plan without network or file writes |
| Durable capture queue | `python scripts/claude-empire.py capture queue list\|enqueue\|claim\|complete\|fail\|resume\|recover --vault <vault> [--timeout SEC] [--lock-stale-after SEC] ...` | Operates the durable `.vault-meta/capture/` state machine (`list [--state STATE]`, `enqueue <adapter> <source>`, `claim [item_id] [--worker ID]`, `complete <item_id> <token> <json>`, `fail <item_id> <token> <error> [--permanent]`, `resume [item_id] [--stale-after SEC] [--claimed-only] [--force]`, `recover [--force-stale-lock]`) |
| Methodology routing | `python scripts/claude-empire.py mode get\|set --vault <vault> [generic\|lyt\|para\|zettelkasten] [--operation-id ID] [--generated-at ISO] [--approved-plan-sha256 HASH] [--apply]` | Reads or transactionally configures `generic`, `lyt`, `para`, or `zettelkasten` mode |
| DragonScale extension | `python scripts/claude-empire.py extension dragonscale [--vault <vault>] [--check] [--operation-id ID] [--generated-at ISO] [--approved-plan-sha256 HASH] [--apply]` | Checks (`--check`) or transactionally scaffolds missing DragonScale compatibility state |
| Host lifecycle hooks | `python scripts/claude-empire.py hook session-start` / `python scripts/claude-empire.py hook stop` | Bounded read-only `SessionStart` context adapter (`CLAUDE_EMPIRE_SESSION_CONTEXT=1`) and `Stop` recovery status reporter |
| Inspect transaction | `python scripts/claude-empire.py transaction inspect --vault <vault> [--bundle] <bundle.json>` | Validates `claude-empire.transaction.v1` bundle preconditions and computes `approval_sha256` |
| Apply transaction | `python scripts/claude-empire.py transaction apply --vault <vault> [--bundle] <bundle.json> --approved-plan-sha256 HASH [--timeout SEC] [--stale-after SEC]` | Applies a reviewed bundle using atomic handle/POSIX confinement and crash journals (no `--apply` flag) |
| Recover transaction | `python scripts/claude-empire.py transaction recover --vault <vault> [--timeout SEC] [--stale-after SEC] [--force-stale-lock]` | Rolls back interrupted operations from `.vault-meta/transactions/` journals |
| Explicit Git checkpoint | `python scripts/claude-empire.py checkpoint <operation-id> --vault <vault> [--message MSG] [--include-raw] [--skip-lint] [--as-of YYYY-MM-DD]` | Commits one completed transaction through a temporary Git index after blob verification |
| Research evidence intake | `python scripts/claude-empire.py research-evidence verify\|plan <package> --workspace <id> [--vault <vault>]` | Offline verification and intake planning for DataForSEO and Firecrawl v1 evidence banks |
| Package & contract gates | `python scripts/claude-empire.py package validate` / `python scripts/claude-empire.py contracts [--vault <vault>] [--verify] [--check-only] [--capability ID]` | Verifies router-native skill allowlist, hooks schema, and capability contracts |
| Reproducible release ZIP | `python scripts/claude-empire.py release build --output <zip> [--source-date-epoch INT] [--denylist PATH] [--require-denylist]` / `python scripts/claude-empire.py release audit <zip> [--denylist PATH] [--require-denylist]` | Builds and self-audits a deterministic stored ZIP from a clean Git worktree |
| Release disclosure scan | `python scripts/claude-empire.py release disclosure [--range RANGE] [--no-commits] [--text-file PATH] [--denylist PATH] [--require-denylist]` | Scans tracked files, release notes, and commit ranges for personal data or private denylist terms |
| Release gate evaluation | `python scripts/claude-empire.py release gates [--execute] [--gate ID] [--timeout SEC]` | Plans or executes declared offline release verification gates without publishing |

## Ontology engine (`ontology`)

| Goal | Command | Behavior and safety boundary |
|---|---|---|
| Derived ontology index | `python scripts/claude-empire.py ontology index [--vault <vault>] [--as-of ISO] [--check] [--dry-run]` | Builds or checks `.vault-meta/ontology/index.json` entity, fact, and alias index |
| Entity & alias resolution | `python scripts/claude-empire.py ontology resolve <term> [--vault <vault>]` | Fast deterministic resolution of entity IDs, aliases, or titles to primary vault notes (exits: 0=found, 3=ambiguous, 4=not found) |
| Fact prerequisite gating | `python scripts/claude-empire.py ontology need [--keys <k1,k2>] [--entity <id> --fields <f1,f2>] [--vault <vault>] [--as-of ISO] [--no-values]` | Evaluates fact certification and freshness gates before automated actions (exits: 0=certified, 10=draft, 11=missing, 12=conflict, 13=stale) |
| Natural language query | `python scripts/claude-empire.py ontology ask "<question>" [--vault <vault>]` | Suggests matching entities, certified facts, and provenance states for conversational queries |
| Propose entity & alias tags | `python scripts/claude-empire.py ontology propose --generated-at ISO [--vault <vault>] [--operation-id ID] [--scope PREFIX] [--batch-size N]` | Emits reversible `operation_type: "ontology"` bundle(s) under `.vault-meta/ontology/bundles/` (derived cache; no `--apply` flag) proposing `entity_id` and normalized `aliases` for untagged notes |
| Prepare certified bundle | `python scripts/claude-empire.py ontology certify --keys <k1> --class owner\|evidence --generated-at ISO [--vault <vault>] [--digest PATH] [--evidence LOCATOR] [--note-paths p1,p2] [--secrets-root DIR] [--operation-id ID]` | Prepares an owner- or evidence-certified transaction bundle under `.vault-meta/ontology/bundles/` (derived cache; no `--apply` flag) with pinned digest / evidence preconditions |
| Deterministic benchmark | `python scripts/claude-empire.py ontology bench [--vault <vault>] [--as-of ISO] [--tasks PATH] [--attempts N] [--out PATH]` | Runs the deterministic benchmark evaluating query latency, wrong-record elimination, and gating accuracy |

### Exact Differential Replacement (EDR) for Ontology Operations
Ontology transaction operations (`operation_type: "ontology"`) enforce strict Exact Differential Replacement (EDR): notes may only modify declared ontology frontmatter keys (`entity_id`, `entity_type`, `about`, `aliases`, `fact.*`, `evidence.*`, `certified_*`, `certified_on`, `review_after`, `last_verified`, `fact_source`). All non-ontology frontmatter blocks in their exact original order, comments, byte-for-byte UTF-8 BOM, uniform newline (`\n` or `\r\n`), and markdown body bytes must be identical. Header-only mutations prevent agents from altering user body text during fact cataloging.

## Vault stewardship (`legends-vault-stewardship`)

| Goal | Command | Behavior |
|---|---|---|
| Stewardship self-check | `python -m cto_legends --home <cto-legends-home> run legends-vault-stewardship -- steward doctor` (or `python scripts/claude-empire.py steward doctor` / `python -m legends_vault_steward doctor` from repo CWD) | Confirms read-only command surface and default stewardship configuration |
| Observe vault & queue | `python -m cto_legends --home <cto-legends-home> run legends-vault-stewardship -- steward scan <vault> --review-limit 5 --out <report-dir> --format both` | Read-only inventory, link/freshness evidence, and bounded review queue (`observe.json` + `observe.md`) |
| Empire profile scan | `python -m cto_legends --home <cto-legends-home> run legends-vault-stewardship -- steward scan <vault> --profile empire --out <report-dir>` | Checks portable `wiki/index.md`, `EMPIRE-ENTRY.md` / `EMPIRE-START.md`, and `wiki/hot.md` contracts in addition to standard link checks |
| Baseline delta verification | `python -m cto_legends --home <cto-legends-home> run legends-vault-stewardship -- steward scan <vault> --baseline <before/observe.json> --expect-changed <path>` | Compares post-campaign state against a prior baseline and exits `12` on unexpected note changes |

## Knowledge packs (`knowledge-packs`)

| Goal | Command | Behavior |
|---|---|---|
| Preview pack attachment | `python scripts/claude-empire.py knowledge attach knowledge/stewardship/pack.json --vault <vault> [--destination PATH] --operation-id ID --generated-at ISO` | Previews attaching or updating a `legends.knowledge-pack/v1` shelf under `wiki/library/` |
| Apply reviewed pack | `python scripts/claude-empire.py knowledge attach knowledge/stewardship/pack.json --vault <vault> [--destination PATH] --operation-id ID --generated-at ISO --approved-plan-sha256 HASH --apply` | Writes `reference/`, `pack-state.json`, and initial user-owned `index.md`; refuses on locally edited references |

## Optional AI Marketing Hub Home adapter (`home-adapter`)

| Goal | Command | Behavior |
|---|---|---|
| Prepare shared-root slots | `python scripts/claude-empire.py home-adapter prepare --vault <vault> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Previews or applies `EMPIRE-ENTRY.md` and Home-compatible directory slot markers |
| Compose root instructions | `python scripts/claude-empire.py home-adapter compose --vault <vault> --home-source <home-root> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Previews or applies exact-provenance shared-root `AGENTS.md` / `CLAUDE.md` composition |
| Scaffold project/company/brand | `python scripts/claude-empire.py home-adapter scaffold --vault <vault> --spec <json> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Previews or applies canonical `Projects/Active/<id>/` and `Workspaces/` records from an explicit JSON spec |
| Install Home into shared root | `python scripts/claude-empire.py home-adapter install --vault <vault> --home-source <home-root> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Previews or applies inventory-bound multi-batch (`<= 150` files/batch) Home installation into a shared root |
| Upgrade Home in shared root | `python scripts/claude-empire.py home-adapter upgrade --vault <vault> --home-source <home-root> --backup-root <backup-dir> --operation-id ID --generated-at ISO [--approved-plan-sha256 HASH --apply]` | Previews or applies an in-place qualified Home upgrade with verified rollback backups and session guards |
| Inspect separate Home | `python scripts/claude-empire.py home-adapter inspect --home <home-root> [--project <context.md>]` | Verifies pinned Home v0.7.2 core digests (supporting qualified pins v0.5.2 through v0.7.2) without modifying files |
| Plan / apply / detach / check binding | `python scripts/claude-empire.py home-adapter plan\|apply\|detach\|list\|check --vault <vault> [--binding-id ID] [--home <home-root>] [--project <context.md>] [--shared-root] [--native] [--native-python <exe>] [--operation-id ID] [--generated-at ISO] [--approved-plan-sha256 HASH] [--apply]` | Manages and verifies reference-only bindings under `wiki/integrations/aimh-home/<binding-id>/` (`home-adapter apply` takes `--approved-plan-sha256` without `--apply`) |

## EDR and Security Guardrails

- **Windows Defender, EDR, and Anti-Ransomware Guardrails**:
  1. Never spawn background shells, hidden console windows, or execution-policy bypass shell wrappers.
  2. Never read local browser profile databases, DPAPI stores, or third-party credential stores outside the explicit project workspace.
  3. Never print raw secret values or personal tokens to stdout, logs, or Markdown receipts.
  4. Never bypass the transaction engine with uncontrolled bulk file-overwrite loops; all multi-file writes are bounded (`BATCH_SIZE <= 150`), symlink/reparse-refusing, and require `--approved-plan-sha256` review.
  5. Keep stewardship `--out` report directories either outside the vault or inside `<vault>/.steward/` only.
