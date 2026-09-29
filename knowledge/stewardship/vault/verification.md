# What counts as stewardship evidence

| Claim | Evidence needed |
| --- | --- |
| A route is repaired | The intended target exists, the route resolves, and the context confirms that target |
| A project is current | Its status and next action agree with reviewed source evidence |
| A session can resume | The handoff names the objective, verified artifacts, constraints and next action |
| A campaign stayed in scope | Before/after snapshots show only expected changes, with unexplained drift investigated |
| A pack upgrade preserves personal work | The preview binds observed files; modified references conflict; user-owned files remain untouched |
| The vault is healthier | Specific acceptance criteria passed without lost evidence or weakened coverage |

Stable finding IDs support comparisons. File SHA-256 hashes detect changed
inputs. Neither proves meaning. A finding that disappears may be repaired,
excluded, renamed or deleted. Inspect the actual change before claiming success.

The scanner does not establish semantic truth, find every contradiction,
fully parse all Markdown or YAML, validate heading/block destinations, or
control live sessions. Reports can contain private paths and derived content.
Keep them within the vault's privacy boundary. Do not upload a private report
to demonstrate a public release; use a synthetic fixture.

When a run is interrupted, retain the campaign and last verified state.
Transaction recovery concerns file operations. Session recovery concerns
meaning and continuity. Record which one was verified.
