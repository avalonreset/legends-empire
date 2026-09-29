# Reusable operating records

Adapt these templates when a real record is needed. Do not create empty
ceremonial records or migrate a working vault solely to match the examples.
The scanner recognizes simple scalar frontmatter for project, task and session
records. Existing prose remains important evidence.

## Project or task

```markdown
---
type: project
status: active
owner: The actual owner
updated: YYYY-MM-DD
next_action: A specific action supported by the current evidence
---
# Project name

## Outcome
What success means and how it will be verified.

## Evidence
Links to source material and verified results.

## Decisions
Current choices and their reasons.

## Open work
Unresolved work, blockers and the next useful action.
```

For a blocked or waiting record, add `blocked_by`. Add `due` only when a real
deadline exists. Use `type: task` for a bounded action with the same fields.
Do not substitute suggested placeholder text for an actual owner or action.

## Session handoff

```markdown
---
type: session
status: paused
owner: The actual owner
updated: YYYY-MM-DD
objective: The user's unfinished objective
next_action: The first concrete resume action
evidence: Path or link to the current evidence
---
# Session handoff

## Established
Verified facts and completed work.

## Resume here
Exact relevant paths, artifacts and checks.

## Constraints
Authorization, preserved work and consequential boundaries.

## Unresolved
Unknowns and decisions still needed.
```

## Maintenance campaign

```markdown
# Maintenance campaign

Goal: The concrete improvement.
Baseline: Path to the saved scan evidence.
Scope: Exact files and observed SHA-256 hashes.
Changes: Proposed content and reasons, supported by source evidence.
Authorization: The applicable user request and unresolved material decisions.
Acceptance: Observable conditions for success.
Transaction: Inspected bundle and reviewed approval hash, when applicable.
Verification: Rescan, allowed changed paths and semantic review results.
Outcome: What improved, what remains uncertain and the next useful action.
```

Markdown is sufficient for a personal project system with owners, outcomes,
tasks, blockers, decisions and handoffs. Shared boards can offer useful views,
but this release has no board synchronization or multiuser notification engine.
