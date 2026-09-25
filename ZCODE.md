# claude-empire: ZCode instructions

Read `AGENTS.md` as the canonical host-neutral contract. The only
registered skill is the vendored `cto-legends` router copy at
`skills/cto-legends/SKILL.md`; the portable core lives in `claude_empire/`.
Do not register this module as its own skill and do not link per-module
skills into host skill directories.

ZCode reads `AGENTS.md` at the workspace and user scope natively (per the
ZCode Agent documentation, https://zcode.z.ai/en/docs/agents), so no mirrored
rules file is needed. Route module work through `cto-legends`
(`cto-legends install legends-empire`) and point ZCode at the vendored
router copy by path when a skill reference is needed.

This repository is product source, not the default user vault. Create a
separate vault with the dry-run-first `init` command or adopt an existing vault.
Resolve that vault before reading `wiki/hot.md` or running a skill.

All shared mutations use one inspected `claude-empire.transaction.v1` bundle.
Parallel workers draft only. Do not use direct shared writes, automatic commits,
or the deprecated per-file lock helper. Remote egress and destructive actions
need explicit user consent.

Public canonical: https://github.com/AgriciDaniel/claude-obsidian
