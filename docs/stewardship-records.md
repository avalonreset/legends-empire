# Markdown operating records

Use these contracts for new records, or during a real review of existing work.
There is no mandatory migration. File names and folders remain user choices.
The scanner recognizes `type: project`, `type: task`, and `type: session`.
Only simple scalar frontmatter and simple lists are supported.

## Project or task

```markdown
---
type: project
status: active
owner: Alex
updated: 2026-09-28
next_action: Compare the trial export against the acceptance checklist
due: 2026-10-05
---
# Export workflow

## Outcome
Produce a reproducible export with intact source references.

## Evidence
[[Trial export results]]

## Decisions
Keep the original source format until the trial passes.

## Open work
- Verify reference preservation.

## Review history
- 2026-09-28: Trial prepared; acceptance remains open.
```

Active statuses: `active`, `in-progress`, `in_progress`, `open`, `blocked`,
`waiting`, `paused`. Terminal statuses: `done`, `complete`, `completed`,
`cancelled`, `canceled`, `archived`, `closed`. Other values request status
interpretation. Blocked/waiting records also need `blocked_by`, such as
`blocked_by: Awaiting the sample export from the owner`.

The review interval defaults to 14 days and can be changed using
`{"operations": {"review_after_days": 30}}` in the JSON configuration overlay.
Missing fields are requests for explicit records, not proof that the owner
or next action does not exist elsewhere in the note.

## Session handoff

```markdown
---
type: session
status: paused
owner: Alex
updated: 2026-09-28
objective: Verify the export preserves source references
next_action: Run the acceptance fixture against the saved trial output
evidence: Trial export results.md
---
# Export acceptance handoff

## Established
The trial output exists; source-reference verification is unfinished.

## Resume here
[[Trial export results]] links to the input and output paths.

## Constraints
Preserve the original source files. No publication authorized.

## Unresolved
Whether the output retains all source references.
```

This is portable continuity, not a live session scheduler. An agent must
inspect the saved evidence before claiming the underlying work is ready.

## Maintenance campaign

```markdown
# Maintenance campaign

Goal: Restore reliable navigation to the export project.
Baseline: reports/before/observe.json
Scope: Exact note paths and observed SHA-256 hashes below.

| Note | Before SHA-256 | Proposed change | Acceptance |
| --- | --- | --- | --- |
| Projects.md | fill from baseline | Correct the destination link | Link resolves to the existing project |

Evidence: Why this is the intended destination.
Authorization: The user's current request and any unresolved material decision.
Transaction: Link to the inspected bundle and its approval hash.
Verification: Rescan with the same profile/configuration; check changed paths.
Outcome: What changed, what remains, and the next useful action.
```

Use `--baseline before/observe.json --expect-changed Projects.md` during the
verification scan to detect changes outside that path. Repeat `--expect-changed`
for each allowed path. Unexpected note changes produce exit code 12 and remain
in the report. This is not a filesystem snapshot lock; another agent or person
may edit concurrently. Investigate drift instead of claiming sole authorship.

Markdown reports are the reading surface. JSON is companion evidence for
stable comparison. Neither requires a database, external board, or background
service.
