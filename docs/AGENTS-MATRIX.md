# legends-obsidian agent compatibility

One canonical skill suite and Python core serve Grok, Codex, Gemini, Claude,
Cursor and MetaMuse. Start with `skills/legends-obsidian/SKILL.md`; select a
user vault explicitly. No separate per-model implementation is necessary.

## Discovery adapters

| Host | Discovery route | Evidence and limits |
|---|---|---|
| Grok | `--host grok`, `~/.grok/skills/<name>/SKILL.md` | Matches installed Grok CLI user-guide skill discovery documentation; no live model certification. |
| Codex | `--host codex`, `~/.agents/skills` and `~/.codex/skills` | Per-skill links; fixture-tested configuration. |
| Gemini | `--host gemini`, `~/.gemini/skills` | Per-skill links; fixture-tested configuration. |
| Claude | `--host claude`, `~/.claude/skills`, or packaged Claude plugin | Shared canonical skills; fixture-tested configuration. |
| Cursor | `--host cursor --workspace PATH`, workspace `.cursor/skills` | Explicit workspace; fixture-tested confinement. |
| MetaMuse | `--host metamuse` (alias `muse`) prints the exact portable entry point | Native discovery path unverified; no files installed and no native integration claim. |

Run `bash scripts/setup-multi-agent.sh --help`, then `--dry-run`, review exact
locations, and use `--apply` for supported link adapters. Existing entries are
never replaced. `--host all` includes the six hosts above plus OpenCode and
Windsurf; an explicit workspace is required. ZCode remains an opt-in adapter.
The PowerShell entry prints portable instructions without mutating Windows host
configuration; on POSIX it forwards to the same Bash installer.

All six can use the same manual fallback when their application provides file
and terminal access: ask it to read the absolute path to
`skills/legends-obsidian/SKILL.md`, then run
`python scripts/claude-obsidian.py package validate` from the product directory.
A chat-only service cannot execute this workflow. An orchestrator may inject
the same skill into its supported hosts; that does not certify standalone
native discovery. No guessed MetaMuse directory is installed.

Adapter configuration is not proof that every version of every agent works.
Verify discovery and a harmless CLI command in the actual execution host.
The fixture tests cover installation and conflict preservation, not live model
behavior. The CLI and core are tested independently of the model.

## Runtime support

Linux and macOS run the full POSIX suite. Native Windows supports inspection,
retrieval and dry-run planning. Recoverable canonical mutation uses WSL or a
supported POSIX host; never bypass this boundary with generic direct writes.
Read `windows-wsl.md` before promising full Windows execution.

## Product versus vault

The extracted product is code. A user vault is separate mutable data. No
machine-specific vault is selected by default. Resolve an explicit path or
configured workspace before research ingestion, indexing or changes.
