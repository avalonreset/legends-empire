# Legends Obsidian agent compatibility

One canonical skill suite and Python core serve multiple agent hosts. Start
with `skills/legends-obsidian/SKILL.md`; select a user vault explicitly.

## Discovery adapters

`scripts/setup-multi-agent.sh` supports Codex, Gemini, Grok, Muse, Cursor,
Windsurf, OpenCode and ZCode. Use `--help`, then `--dry-run`, review the exact
locations, and use `--apply`. Existing unrelated entries are never replaced.
Claude Code can install the `.claude-plugin` distribution or read the same
portable skills. Any agent with file and terminal access can read `AGENTS.md`
and load the selected skill directly without native plugin support.

Adapter configuration is not proof that every version of every agent works.
Validate discovery and a harmless CLI command in the actual execution host.
The repository's fixture tests cover installation rules; they do not certify
live model behavior.

## Runtime support

Linux and macOS run the full POSIX suite. Native Windows supports inspection,
retrieval and dry-run planning. Recoverable canonical mutation uses WSL or a
supported POSIX host; never bypass this boundary with generic direct writes.
Read `windows-wsl.md` before promising full Windows execution.

## Product versus vault

The extracted product is code. A user vault is separate mutable data. No
machine-specific vault is selected by default. Resolve an explicit path or
configured workspace before research ingestion, indexing or changes.
