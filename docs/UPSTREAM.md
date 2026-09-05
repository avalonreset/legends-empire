# Upstream and attribution

Legends Obsidian is a lightly adapted, provider-neutral distribution of
**[claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian), created by
[Daniel Agrici](https://github.com/AgriciDaniel)**. Daniel's project supplies the
knowledge engine, the 15 canonical workflows, transaction safety, templates,
and most of this repository. Please visit and star his project.

The Legends name gives users one product name to speak across agent providers.
The MIT license and Daniel's copyright remain intact. This distribution is
maintained independently by Benjamin Samar and does not imply upstream
endorsement. Please report Legends installation and packaging issues here;
credit and link Daniel's project when sharing the underlying knowledge system.

## Exact baseline

- Latest upstream tagged release checked on 2026-09-05: **v2.1.1**.
- Release commit: `9f8c1199047eac2c3828496279fbb7ba9540b90b`.
- Public main commit incorporated: `ad67087cad22ad84cc3288f915588ae42c0c2b44`.
- Main adds recovery, checkpoint, and bounded response hardening after v2.1.1.
- `config/upstream-parity.json` records hashes of the preserved implementation.
- `python tests/test_upstream_parity.py` checks those hashes without network.

Of 78 protected implementation files, 77 remain byte-for-byte upstream. The
sole engine exception adds `O_BINARY` when opening release ZIPs on Windows;
without it, Windows text-mode I/O translates CRLF and truncates at Ctrl-Z.
The regression test fails before the fix and passes afterward. Original and
distribution digests plus the reason are recorded in the parity manifest. All
15 skill trees, runtime scripts, hooks, and vault templates are unchanged.
Internal identifiers,
JSON schemas, configuration names, environment variables, and the legacy CLI
entry point retain their original spelling so existing vaults remain compatible.

## Legends additions

- A `legends-obsidian` routing skill and neutral CLI entry point.
- Host installers covering Grok, Codex, Gemini CLI, and Claude Code, with
  additional compatible-host paths documented in the support matrix.
- Provider-neutral package metadata, public documentation, and attribution.
- Installer regression tests and an executable upstream parity manifest.

Private workstation paths, vault content, local retrieval-scope extensions,
and experimental Windows locking changes are excluded from this distribution.
The supported operating-system boundary remains upstream's: protected writes
on POSIX/WSL; portable read-only inspection on native Windows.

## Updating

Review Daniel's public releases and main-branch changes, refresh the pinned
baseline and parity hashes, then rerun the complete upstream suite, Legends
installer tests, and deterministic artifact build/audit. Version 2.1.1 identifies
this initial Legends release; the pinned upstream commit identifies precisely
which post-release fixes it includes.
