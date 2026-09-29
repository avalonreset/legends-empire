"""Explicit Markdown project and session contracts, never inferred business truth."""
from __future__ import annotations

from datetime import date, datetime

from .models import Finding, NoteRecord

ACTIVE = {"active", "in-progress", "in_progress", "blocked", "waiting", "paused", "open"}
TERMINAL = {"done", "complete", "completed", "cancelled", "canceled", "archived", "closed"}


def operating_records(notes: dict[str, NoteRecord], now: datetime, config: dict) -> tuple[list[dict], list[Finding]]:
    records, findings = [], []
    max_days = int(config.get("operations", {}).get("review_after_days", 14))
    for path, note in sorted(notes.items()):
        fm = note.frontmatter
        kind = str(fm.get("type", "")).lower()
        if kind not in {"project", "session", "task"}:
            continue
        status = str(fm.get("status", "")).lower()
        record = {"path": path, "type": kind, "status": status or "undeclared",
                  "owner": fm.get("owner"), "next_action": fm.get("next_action"),
                  "updated": fm.get("updated"), "due": fm.get("due"),
                  "blocked_by": fm.get("blocked_by"), "sha256": note.sha256}
        records.append(record)

        def add(code: str, message: str, severity: str = "medium") -> None:
            findings.append(Finding(code, severity, path, note.record_class, message))

        if status in TERMINAL:
            continue
        if status not in ACTIVE:
            add("operations.status_unrecognized", "Declare an active or terminal status before judging this record's upkeep.", "low")
            continue
        required = ["owner", "next_action", "updated"]
        if kind == "session":
            required += ["objective", "evidence"]
        missing = [key for key in required if not fm.get(key)]
        if missing:
            add("operations.missing_fields", "Active " + kind + " lacks explicit fields: " + ", ".join(missing))
        if status in {"blocked", "waiting"} and not fm.get("blocked_by"):
            add("operations.missing_blocker", "Blocked or waiting work needs a named dependency or blocker.")
        if fm.get("updated"):
            try:
                updated = date.fromisoformat(str(fm["updated"])[:10])
                age = (now.date() - updated).days
                if age > max_days:
                    add("operations.review_due", f"Active {kind} last reviewed {age} days ago; policy is {max_days}. Verify progress, do not merely change the date.")
                elif age < 0:
                    add("operations.future_review", "Review date is in the future; check the record.")
            except ValueError:
                add("operations.invalid_updated", "Use an ISO YYYY-MM-DD review date.")
        if fm.get("due"):
            try:
                due = date.fromisoformat(str(fm["due"])[:10])
                if due < now.date():
                    add("operations.overdue", "Declared due date has passed while this record remains open.", "high")
            except ValueError:
                add("operations.invalid_due", "Use an ISO YYYY-MM-DD due date.")
    return records, findings
