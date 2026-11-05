# Agent Instructions for legends-empire

Start with [docs/AGENT-RECIPE.md](docs/AGENT-RECIPE.md), [README.md](README.md), and [docs/install-guide.md](docs/install-guide.md).

## Core Operating Rules

1. **Single Router Skill**: Discovery belongs to `skills/cto-legends/SKILL.md`. Do not add secondary skills under `skills/` or register per-module skills.
2. **Product vs. Vault Separation**: Never point test runs or implicit writes at the module source checkout or a real user vault without an explicit `--vault <path>`.
3. **Preview-First Reviewed Transactions**: All mutating workflows (`init`, `adopt`, `migrate`, `capture apply`, `mode set`, `extension dragonscale`, `knowledge attach`, `home-adapter`, `transaction apply`) require a preview pass followed by `--apply --approved-plan-sha256 <sha256>`.
4. **Windows Defender, EDR, and Anti-Ransomware Guardrails**:
   - Never spawn hidden consoles, execution-policy bypass shell wrappers, or background localhost daemons.
   - Never read local browser profile databases, DPAPI stores, or third-party credential stores outside the explicit workspace.
   - Never print raw secrets or tokens to terminal output, logs, or receipts.
   - All vault mutations use bounded batches (`BATCH_SIZE <= 150`), refuse symlinks and reparse points (`CWE-59`), and fail closed on precondition drift.
5. **Release & Test Hygiene**:
   - Run `python scripts/check_release.py` and the hermetic test suite (`make test` or `python -m unittest discover -s tests`) before packaging.
   - `make clean-test-state` removes runtime locks, caches, and generated state.
