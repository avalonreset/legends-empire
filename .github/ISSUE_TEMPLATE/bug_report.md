---
name: Bug report
about: Something is broken or behaves unexpectedly
title: "[bug] "
labels: bug
assignees: ''
---

## What happened
A clear description of the unexpected behavior.

## Expected behavior
What you expected to happen instead.

## Reproduction steps
1. ...
2. ...
3. ...

## Environment
- Product version: (run `python3 scripts/claude-empire.py --version`)
- Claude Code version: (run `claude --version`)
- OS: (e.g. macOS 14.5, Pop!_OS 24.04, Windows 11)
- Obsidian version: (if relevant)
- Vault selection source: (`doctor --vault <vault>` output, with private paths redacted)
- Transport state: (`bash scripts/detect-transport.sh --peek --vault <vault>` output)

## Module recipe / script involved
Which surface is affected? (e.g. router recipe step, `scripts/wiki-mode.py`, `scripts/claude-empire.py contracts`)

## Logs / output
```
Paste relevant terminal output or error messages here.
```

## What you tried
- [ ] Ran the relevant regression test or `make test`: Yes / No
- [ ] Confirmed the product repository and user vault are separate: Yes / No
- [ ] Checked CHANGELOG for known issues at your version
- [ ] Searched existing issues for similar reports

## Additional context
Anything else that might help (screenshots, related issues, recent changes).
