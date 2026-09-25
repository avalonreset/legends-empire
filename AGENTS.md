# legends-empire: contributor build and test notes

Product source for `legends-empire` (local-first Markdown knowledge-base
tooling). For product usage see `README.md` and `docs/`. Agent skill routing
goes through `cto-legends`; this repo registers no skill of its own.

## Test

Run `make test` after behavioral changes. Targets:

- `make test-python`: each `tests/test_*.py` in isolation.
- `make test-shell`: each `tests/test_*.sh` in isolation.
- `make test-contracts`: `scripts/claude-empire.py contracts --check-only`
  and `--verify`.
- `make test-package`: `scripts/claude-empire.py package validate`.
- `make validate`: contracts plus package validation without the suite.

## Working-tree safety

Tests are hermetic. Do not point test runs at a real user vault (a directory
containing `.claude-empire.json`). `make clean-test-state` removes runtime
locks, caches, and generated state.
