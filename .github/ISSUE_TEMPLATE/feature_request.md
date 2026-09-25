---
name: Feature request
about: Suggest an idea, recipe improvement, or enhancement
title: "[feature] "
labels: enhancement
assignees: ''
---

## Problem
What user need or workflow gap motivates this request? Be specific about the situation where today's behavior falls short.

## Proposed solution
Describe what you'd like to see. New module recipe? New vault tool? Change to an existing one? Sketch the interface (router wording, command, expected output). This repo registers no per-module skills; skill requests belong to the `cto-legends` router.

## Alternatives considered
What other approaches did you think about, and why is this one preferred?

## Scope
Which existing surface(s) does this touch?
- [ ] Router pin refresh (`skills/cto-legends/SKILL.md`)
- [ ] A new script (`scripts/<name>`)
- [ ] Change to the portable core (`claude_empire/`)
- [ ] Change to hooks / contracts / release config
- [ ] Module recipe change (README / `docs/`)
- [ ] Documentation only

## Compatibility
- Does this change behavior for existing v1.x vaults? Yes / No
- Does it require a new opt-in (`scripts/setup-*.sh`)? Yes / No
- Does it introduce a new dependency? Yes / No

## Testing
How would this be tested hermetically? (No network, no external services.)

## Additional context
Links, examples, or references to similar features in other tools.
