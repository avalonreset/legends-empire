# Legends Obsidian: host compatibility

Legends Obsidian supplies the same portable Agent Skills and Python core to each
host. The host supplies file, shell, and optional web tools. Say **Legends
Obsidian** to load the neutral router, then select the appropriate wiki workflow.
The internal `claude_obsidian` package and vault schema remain compatible with
Daniel Agrici's [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian).

## Discovery and installation

Paths below are the paths used by our installer. Each contains one directory per
skill with `SKILL.md` immediately inside it. The installer does not nest the
entire pack under a single skill directory.

| Host | User skills | Workspace skills | Invocation / discovery check |
| --- | --- | --- | --- |
| Grok CLI / Grok Build | `~/.grok/skills/` | `.grok/skills/` | `/skills`, `grok inspect --json` where available |
| Codex CLI | `~/.agents/skills/` | `.agents/skills/` | `/skills`, `$legends-obsidian` |
| Gemini CLI | `~/.gemini/skills/` | `.gemini/skills/` | `gemini skills list`, `/skills reload` |
| Claude Code | `~/.claude/skills/` | `.claude/skills/` | `/legends-obsidian`, or plugin namespace when installed as a plugin |
| OpenCode | `~/.config/opencode/skills/` | `.opencode/skills/` | Host skill discovery |
| Cursor | Not installed globally by this tool | `.cursor/skills/` | Workspace skill discovery |
| Windsurf | Not installed globally by this tool | `.windsurf/skills/` | Workspace skill discovery |

The four major host paths are documented by their vendors: [Grok skills](https://docs.x.ai/build/features/skills-plugins-marketplaces),
[Codex skills](https://developers.openai.com/codex/skills/),
[Gemini skills](https://geminicli.com/docs/cli/skills/), and
[Claude Code skills](https://code.claude.com/docs/en/skills) (checked September 5,
2026). Grok and Gemini also discover `.agents/skills`; Gemini gives that alias
precedence within the same scope. Avoid leaving an older pack in another
higher-priority discovery location. The installer reports collisions in its own
destinations and never removes competing installations.

## Safe installer

Python 3.11+ is required. Use an extracted release or a stable product checkout,
not a temporary download directory you will later delete. The links need that
source directory to remain available. Unix uses directory symlinks; native
Windows uses directory junctions via PowerShell, without administrator rights or
Developer Mode on ordinary local NTFS volumes.

Bash (Linux, macOS, or WSL with the product installed in that environment):

```bash
bash bin/setup-multi-agent.sh --host grok --host codex --host gemini --host claude
bash bin/setup-multi-agent.sh --apply --host grok --host codex --host gemini --host claude
bash bin/setup-multi-agent.sh --check --host grok --host codex --host gemini --host claude
```

Windows PowerShell 5.1+ or PowerShell 7:

```powershell
./bin/setup-multi-agent.ps1 -Hosts grok,codex,gemini,claude
./bin/setup-multi-agent.ps1 -Mode apply -Hosts grok,codex,gemini,claude
./bin/setup-multi-agent.ps1 -Mode check -Hosts grok,codex,gemini,claude
```

No host argument selects the four major hosts plus OpenCode. `--host all`
(`-Hosts all`) also selects Cursor and Windsurf and requires `--workspace PATH`
(`-Workspace PATH`). `--scope workspace` (`-Scope workspace`) installs all selected
hosts in an existing workspace. Without that scope, only Cursor and Windsurf use
the workspace. Use `--home PATH` (`-HomeDirectory PATH`) to select an existing
isolated test home explicitly. Existing directories, foreign links, dangling
links, and redirected parent directories are conflicts. All destinations are
preflighted before writes; permission or I/O failures during creation can leave
new links, which a retry checks and reuses. Nothing is replaced or deleted.

Exit codes: **0** ready/successful preview; **1** missing links in check mode;
**2** invalid arguments, conflicts, or installation failure. Installation does
not modify host settings, write `AGENTS.md`/`GEMINI.md`/`CLAUDE.md`, enable hooks,
configure accounts, choose a vault, or call a model.

## Workflow compatibility and limits

All hosts use the same skills. These are CLI/agent integrations, not a promise
that a consumer web chat can operate a local vault. Claude plugin lifecycle hooks
are optional conveniences; flat skill installs do not enable them. No workflow
requires Claude hooks. Use one discovery method per host to avoid duplicate skill
names from a plugin plus flat links.

Select the user vault explicitly (`--vault PATH`) or with the upstream
`CLAUDE_OBSIDIAN_VAULT` environment variable. The product checkout is never the
vault. Resolve linked skill locations to the real product root before invoking
`python /absolute/product/scripts/legends-obsidian.py`. Gemini may ask to activate
a skill and access its directory; other hosts may enforce their own shell or
workspace permissions. Grant access to the selected product and vault through
that host's normal controls.

Native Windows can install and discover skills and run supported read-only /
dry-run commands. The upstream transaction engine refuses native Windows writes;
use the core in WSL/Linux/macOS for mutation. Do not bypass that boundary with
host Write/Edit tools. Optional Obsidian CLI, retrieval extras, and network
research retain their own prerequisites and consent rules.

## Release evidence standard

`python -B tests/test_setup_multi_agent.py` tests isolated home/workspace layouts,
all seven host destinations, idempotence, conflicts, parent-link redirection,
and shell-wrapper exit codes. The same suite runs on Linux and Windows; OS-only
cases are explicitly skipped elsewhere. Discovery by a real installed CLI is
stronger evidence than the filesystem checks. A successful model-driven vault
workflow is stronger still. Do not label all hosts live-tested from installation
or static skill validation alone. Paid provider runs require existing account
access and are recorded separately from these offline checks.
