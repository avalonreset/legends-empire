# Stewardship demonstration

Run from the product root:

```text
python scripts/claude-empire.py steward scan examples/stewardship/vault --profile empire --review-limit 3 --format md
```

This fixture has a broken project route, a project missing ownership and blocker
information, an incomplete session handoff, and an old context review date.
The orphan receipt is intentionally preserved as evidence.

The agenda is bounded, while the report retains the complete findings ledger.
Dates are fixed demonstration data, so age measurements vary by scan date.
For a proven repair campaign, run `python3 tests/test_stewardship_campaign.py`
on POSIX or WSL. It creates a temporary copy, inspects and applies an exact
transaction, verifies the result and tests rejection of intervening edits.
