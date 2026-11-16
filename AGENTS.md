# Agent Instructions for legends-empire

Start with [docs/AGENT-RECIPE.md](docs/AGENT-RECIPE.md), [README.md](README.md), and [docs/install-guide.md](docs/install-guide.md).

## Core Operating Rules

1. **Single Router Skill**: Discovery belongs to `skills/cto-legends/SKILL.md`. Do not add secondary skills under `skills/` or register per-module skills.
2. **Product vs. Vault Separation**: Never point test runs or implicit writes at the module source checkout or a real user vault without an explicit `--vault <path>`.
3. **Preview-First Reviewed Transactions**: Mutating workflows with built-in preview/apply modes (`init`, `adopt`, `migrate`, `capture apply`, `mode set`, `extension dragonscale`, `knowledge attach`, and `home-adapter prepare|compose|scaffold|install|upgrade|detach`) run in preview/dry-run mode first, emit `approved_plan_sha256`, and require `--apply --approved-plan-sha256 <sha256>`. Separate apply subcommands (`transaction apply` after `transaction inspect`, and `home-adapter apply` after `home-adapter plan`) take `--approved-plan-sha256 <sha256>` directly without `--apply`. `ontology propose` and `ontology certify` require `--generated-at <ISO-UTC>`, have no `--apply` flag, and write transaction bundles into the derived `.vault-meta/ontology/bundles/` cache to be applied via `transaction apply`.
4. **PATH- and CWD-Independent Invocation**: Invoke module commands via `python -m cto_legends --home <cto-legends-home> run legends-empire -- ...` (or `legends-vault-stewardship`). Source-tree invocations (`python scripts/claude-empire.py ...`, `python -m legends_vault_steward ...`) require the module checkout root as current working directory.
5. **Windows Defender, EDR, and Anti-Ransomware Guardrails**:
   - Never spawn hidden consoles, execution-policy bypass shell wrappers, or background localhost daemons.
   - Never read local browser profile databases, DPAPI stores, or third-party credential stores outside the explicit workspace.
   - Never print raw secrets or tokens to terminal output, logs, or receipts.
   - All vault mutations use bounded batches (`BATCH_SIZE <= 150`), refuse symlinks and reparse points (`CWE-59`), and fail closed on precondition drift.
6. **Release & Test Hygiene**:
   - From the module checkout root (CWD), run `python scripts/check_release.py` and the hermetic test suite (`make test` or `python -m unittest discover -s tests`) before packaging.
   - `make clean-test-state` removes runtime locks, caches, and generated state.
