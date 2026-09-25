# Install legends-empire

legends-empire has two independent parts:

1. the module package (pinned `cto-legends` router copy, portable core,
   and vault templates), installed through the router;
2. a user-owned Obsidian vault containing mutable knowledge.

Do not use a module checkout as the vault. A source clone is suitable
for development, but a normal user vault should be a separate directory.

## Requirements

- Python 3.11 or newer
- `cto-legends` (the only registered skill; this repo vendors a pinned
  copy at `skills/cto-legends/SKILL.md`)
- Obsidian when you want its visual editor
- Bash for the vault helper scripts
- Git only for source development, release builds, or explicit checkpoints
- On Windows: WSL for vault writes; native Windows supports read-only
  inspection and dry-runs: see the [Windows and WSL guide](windows-wsl.md)

## Router install

`cto-legends` is the only registered skill. Do not register this module
as its own skill and do not link per-module skills into host skill
directories.

```bash
cto-legends install legends-empire
```

Follow the module recipe the router loads. The recipe lives in the
module README plus `docs/`; there is no per-host skill installer.

## Host support

One canonical router copy and the Python core serve Grok, Codex, Gemini,
Claude, Cursor, Windsurf, OpenCode, ZCode, and MetaMuse. No separate
per-model implementation is necessary. Ask the host to read the absolute
path to `skills/cto-legends/SKILL.md`, then run
`python scripts/claude-empire.py package validate` from the product
directory. A chat-only service cannot execute this workflow.

Linux and macOS run the full POSIX suite. Native Windows supports
inspection, retrieval and dry-run planning. Recoverable canonical
mutation uses WSL or a supported POSIX host; never bypass this boundary
with generic direct writes. Read `windows-wsl.md` before promising full
Windows execution.

## Product versus vault

The extracted product is code. A user vault is separate mutable data. No
machine-specific vault is selected by default. Resolve an explicit path
or configured workspace before research ingestion, indexing or changes.

## Create a new vault

Review the initialization plan first:

```bash
python3 scripts/claude-empire.py init <new-vault> \
  --generated-at <ISO-UTC> --operation-id init-reviewed
```

Apply the same operation only after the destination and changed paths look
correct:

```bash
python3 scripts/claude-empire.py init <new-vault> \
  --generated-at <ISO-UTC> --operation-id init-reviewed \
  --approved-plan-sha256 <reviewed-sha256> --apply
```

The generated vault contains:

- `.gitignore`: privacy-safe defaults excluding `.vault-meta/` runtime state,
  Obsidian workspace state, and live `.mcp.json` launch configuration;
- `.claude-empire.json`: workspace identity and vault selection;
- `inbox/`: visible source intake;
- `.raw/`: immutable source payloads and legacy delta manifest;
- `wiki/`: index, log, hot cache, overview, and generated notes;
- `.obsidian/`: minimal non-destructive Obsidian defaults;
- `.vault-meta/`: ignored runtime state created when needed.

Initialization does not add an upstream Git remote or install community
plugins. Open the new directory through Obsidian's vault picker.

## Adopt an existing vault

Adoption scans an existing Obsidian directory and proposes only missing product
metadata and foundation files:

```bash
python3 scripts/claude-empire.py adopt <existing-vault> \
  --generated-at <ISO-UTC> --operation-id adopt-reviewed
python3 scripts/claude-empire.py adopt <existing-vault> \
  --generated-at <ISO-UTC> --operation-id adopt-reviewed \
  --approved-plan-sha256 <reviewed-sha256> --apply
```

It preserves existing notes and Obsidian JSON. `--force` is intentionally
separate and should be used only after inspecting replacement targets.

For older claude-empire layouts, add provenance ledgers and workspace config
with an additive migration:

```bash
python3 scripts/claude-empire.py migrate --vault <existing-vault> \
  --generated-at <ISO-UTC> --operation-id migrate-reviewed
python3 scripts/claude-empire.py migrate --vault <existing-vault> \
  --generated-at <ISO-UTC> --operation-id migrate-reviewed \
  --approved-plan-sha256 <reviewed-sha256> --apply
```

Migration is idempotent, does not infer claims from prose, and leaves the
legacy `.raw/.manifest.json` byte-for-byte unchanged.

## Vault selection

Mutable commands use this precedence:

1. `--vault <path>`
2. `CLAUDE_EMPIRE_VAULT`
3. nearest `.claude-empire.json`
4. nearest unambiguous initialized vault from the current directory

If selection fails, the command exits without mutation. The product root
is rejected as an implicit vault.

Verify a selected vault:

```bash
python3 scripts/claude-empire.py doctor --vault <vault>
python3 scripts/claude-empire.py contracts --verify --vault <vault>
```

## Optional configuration

The portable filesystem transport always remains available. Detect an active,
supported Obsidian CLI without persisting a snapshot:

```bash
bash scripts/detect-transport.sh --peek --vault <vault>
```

Optional extensions are explicit and vault-scoped:

```bash
bash scripts/setup-mode.sh --vault <vault>
bash scripts/setup-retrieve.sh --vault <vault>
bash scripts/setup-dragonscale.sh --vault <vault>
```

Read each script's preview before applying. Retrieval may use local BM25 alone;
model-based contextual prefixes or remote endpoints require explicit egress
consent. Optional tools such as Ollama and defuddle are capability-detected.

## First operation

Place a source in `inbox/`. Inspect the byte-capture plan:

```bash
python3 scripts/claude-empire.py capture plan --vault <vault>
```

Create immutable content-addressed copies only when the plan is correct:

```bash
python3 scripts/claude-empire.py capture apply --vault <vault> \
  --generated-at <ISO-UTC> --operation-id capture-reviewed
python3 scripts/claude-empire.py capture apply --vault <vault> \
  --generated-at <ISO-UTC> --operation-id capture-reviewed \
  --approved-plan-sha256 <reviewed-sha256> --apply
```

Then ask the host to route the ingest through `cto-legends`. Image, PDF,
and EPUB semantic extraction is not built into the core; those formats
currently receive bounded metadata unless a separately configured adapter
is explicitly approved.

## Upgrade and rollback

Upgrade product code independently from the user vault. Before a migration or
large ingest, make a normal backup or snapshot. Knowledge writes are journaled;
run recovery after an interrupted operation:

```bash
python3 scripts/claude-empire.py transaction recover --vault <vault>
```

Recovery conservatively preserves stale or foreign lock identities. Only after
confirming no writer is active may an operator add `--force-stale-lock`.

Git history is optional and never automatic. To checkpoint exactly one
completed transaction:

```bash
python3 scripts/claude-empire.py checkpoint <operation-id> --vault <vault> \
  --as-of YYYY-MM-DD
```

Checkpointing uses a temporary index, verifies exact Git blob bytes, refuses
pre-existing staged state, and resumes an interrupted ref/index finalization
from its vault-local pending record.

## Uninstall

Remove the module through the router, not the vault: ask `cto-legends`
to drop the `legends-empire` registration.

User notes, sources, ledgers, and Obsidian settings remain untouched.

## Troubleshooting

| Symptom | Check |
|---|---|
| Skill is not discovered | Confirm the host read `skills/cto-legends/SKILL.md` from the product checkout; run `python scripts/claude-empire.py package validate` from the product directory. |
| Command says no vault selected | Run from the vault, pass `--vault`, or set `CLAUDE_EMPIRE_VAULT`. |
| Product-root refusal | Select a separate user vault; product checkout writes are unsupported. |
| Transaction conflict / exit 75 | Another operation is active or a target changed; reread, rebuild, and inspect a new bundle. |
| Obsidian CLI is unavailable | Use filesystem reads; start/update Obsidian before retrying CLI transport. |
| Capture adapter is not implemented | Inspect `capture adapters`; configure a separate runner only with explicit consent. |
| Writes refused with `UNSUPPORTED_PLATFORM` on Windows | Vault mutation requires WSL; see the [Windows and WSL guide](windows-wsl.md). |
| WSL installed but `wsl --status` hangs | Follow Microsoft's diagnostic and reporting flow in the [Windows and WSL guide](windows-wsl.md#wsl-troubleshooting); do not assume a cause without evidence. |
| Native dry-run approval fails in WSL with `PLAN_CHANGED` | Approval hashes bind the reviewing environment; redo the dry-run inside WSL ([details](windows-wsl.md#wsl-troubleshooting)). |

Run `make test` in the product repository when developing or packaging changes.
