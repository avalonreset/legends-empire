---
name: legends-empire
description: "Portable Legends Empire entry point for source-cited vault setup, ingest, query, save, lint, research and research-evidence handoff. Load the matching canonical skill and select a user vault explicitly."
---

# Legends Empire

**Brand:** Legends Empire  
**Vault:** explicitly selected user directory, never the product checkout.  
**Capability:** `3.0.0`  
**Matrix:** `docs/AGENTS-MATRIX.md`

Retired names: **Codex Obsidian** / `codex-obsidian`, spoken **Claude Obsidian**, suite `legends-obsidian`. Same suite. One name for every coding agent.

Internal Python package remains `claude_empire`. Do not ask the user to say that.

## Philosophy

One skill pack. Many hosts. Portable Agent Skills (`skills/*/SKILL.md`). Host provides tools; this pack provides vault procedure.

This repository is **product code**. Resolve the user vault explicitly. Never use the plugin/product root as the life wiki.

## Route

For DataForSEO or GeoGrid research evidence packages, read
`../../docs/RESEARCH-EVIDENCE-HANDOFF.md`, then route through `wiki-ingest`.
Keep collection tools independent; use the existing capture and transaction
protocol for canonical knowledge. An exported package is unreviewed evidence.

Load sibling skill `wiki` as orchestrator, then:

| Intent | Skill |
|--------|--------|
| setup / scaffold / adopt | `wiki` |
| ingest | `wiki-ingest` |
| query | `wiki-query` |
| lint | `wiki-lint` |
| fold | `wiki-fold` |
| mode (PARA/LYT/…) | `wiki-mode` |
| Obsidian CLI transport | `wiki-cli` |
| hybrid retrieval | `wiki-retrieve` |
| save session | `save` |
| research | `autoresearch` |
| canvas | `canvas` |
| clean URL | `defuddle` |
| markdown | `obsidian-markdown` |
| bases | `obsidian-bases` |
| deep think | `think` |

## Installation and execution

Read `../../docs/install-guide.md`. On POSIX hosts, run
`bash "$PRODUCT_ROOT/scripts/setup-multi-agent.sh" --dry-run --host codex` from the extracted
product directory; review before `--apply`. The CLI is
`python "$PRODUCT_ROOT/scripts/claude-empire.py" --help`, with PRODUCT_ROOT resolved from this installed skill.

Native Windows supports retrieval, inspection and dry runs. Recoverable vault
mutation requires WSL or another supported POSIX host; never bypass the
transaction protocol with direct writes. Do not infer full host support from
successful skill discovery alone.

## Writing style

Do not use em dashes in generated notes, summaries, reports, headings or public
documentation. Use periods, commas, colons or parentheses as the sentence requires.
Preserve exact quotations, legal notices and immutable source evidence unchanged.
