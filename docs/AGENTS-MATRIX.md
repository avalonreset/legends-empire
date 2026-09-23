# Legends Obsidian — multi-agent matrix

**Philosophy:** One brand. One skill pack. Many hosts.  
**Capability:** `2.3.0` (upstream `AgriciDaniel/claude-obsidian` v2.2.0)

You say **Legends Obsidian** to any coding agent. The pack installs into that agent’s skill system. The **life vault** is still wherever you point it (Empire default: `E:\empire`). This repo is the **skill source**, not the life dump.

## Non-negotiables

1. **Agent Skills format** — every skill is `skills/<name>/SKILL.md` with YAML `name` + `description` only in frontmatter (portable standard).
2. **No host-owned logic in the skill bodies** — skills describe vault operations; hosts provide tools (Read/Write/Shell).
3. **Same triggers everywhere** — “Legends Obsidian”, `/wiki`, ingest, query, lint, save, canvas, autoresearch.
4. **Deprecated names** — “Codex Obsidian” / `codex-obsidian` and spoken “Claude Obsidian” are **decommissioned** as house speech. Do not install the `codex-obsidian` skill id. Brand is **Legends Obsidian** only.
5. **Product ≠ vault** — never treat `E:\legends-obsidian` as the life wiki.

## Support tiers

| Tier | Host | Skill discovery (typical) | Status |
|------|------|---------------------------|--------|
| **P0** | **Grok** | `~/.grok/skills/<skill>/SKILL.md` | **First-class** — per-skill junctions from this pack |
| **P0** | **Codex CLI** | `~/.codex/skills/<skill>/` and `~/.agents/skills/<skill>/` | **First-class** |
| **P0** | **Muse** | `~/.agents/skills/<skill>/` (shared user-skill root) | **First-class** — per-skill symlinks from this pack |
| **P0** | **Claude Code** | `~/.claude/skills/<skill>/SKILL.md` or project `.claude/skills/` | **First-class** |
| **P0** | **Gemini CLI** | `~/.gemini/skills/<skill>/` | **First-class** |
| **P1** | **Cursor** | `.cursor/skills` or `~/.cursor/skills` | **Supported** |
| **P1** | **Windsurf** | `.windsurf/skills` | **Supported** |
| **P1** | **OpenCode** | `~/.opencode/skills` / `~/.config/opencode/skills` | **Supported** |
| **P2** | **GitHub Copilot Chat** | `.github/copilot-instructions.md` | **Bootstrap only** |
| **P2** | Others | Point agent at `AGENTS.md` + `skills/` | **Docs-only** until proven |

## What each host gets

| Host | Entry files in repo | Install action |
|------|---------------------|----------------|
| Any / multi | `AGENTS.md`, `LEGENDS.md`, `docs/AGENTS-MATRIX.md` | Read first |
| Codex | `.codex-plugin/plugin.json` | `setup-multi-agent` → `~/.codex/skills/*` |
| Muse | `scripts/setup-multi-agent.sh --host muse` | `setup-multi-agent` → `~/.agents/skills/*` |
| Claude | `.claude-plugin/plugin.json` | → `~/.claude/skills/*` |
| Gemini | `GEMINI.md` | → `~/.gemini/skills/*` |
| Grok | `LEGENDS.md` + global skills | → `~/.grok/skills/*` |
| Cursor / Windsurf | `.cursor/rules`, `.windsurf/rules` | workspace skills |

## Install (Windows — preferred on this machine)

```powershell
pwsh -File E:\legends-obsidian\bin\setup-multi-agent.ps1
```

## Install (Unix / WSL)

```bash
bash E:/legends-obsidian/scripts/setup-multi-agent.sh --apply --host grok --host codex
```

## Verify

| Host | Smoke check |
|------|-------------|
| Grok | Say “Legends Obsidian” or “lint the wiki” — skill should load |
| Codex | `/wiki` or skill list contains `wiki` / `legends-obsidian` |
| Muse | Skill list contains `legends-obsidian` / `wiki-query`; `contracts --verify` clean |
| Claude | Skill list / “use Legends Obsidian” |
| Gemini | Session sees GEMINI.md + linked skills |

## Skill inventory (canonical)

| Skill | Role |
|-------|------|
| `legends-obsidian` | Brand entry / router |
| `wiki` | Orchestrator, init/adopt, hot cache |
| `wiki-ingest` | Sources → wiki pages |
| `wiki-query` | Cited answers |
| `wiki-lint` | Health check |
| `wiki-fold` | Log rollup |
| `wiki-mode` | LYT / PARA / Zettel / generic |
| `wiki-cli` | Obsidian CLI transport |
| `wiki-retrieve` | Optional hybrid retrieval (`--all-vault` for Empire spine) |
| `save` | File this session |
| `autoresearch` | Research loop |
| `canvas` | `.canvas` files |
| `defuddle` | Clean web pages |
| `obsidian-markdown` | OFM syntax |
| `obsidian-bases` | Bases |
| `think` | 10-principle loop |

## Windows mutation boundary (2.2.0)

`scripts/claude-obsidian.py` writes are WSL/Linux/macOS only. Native NERV: inspect, dry-run, retrieve. Daily Empire notes still use host Write tools.

## Life vault vs skill pack

| Path | Role |
|------|------|
| `E:\legends-obsidian` | This pack (develop + install from here) |
| `E:\empire` | Default **life monobrain** for org work |

When the user is organizing digital life, file into Empire. When the user is improving the skill pack, work here.
