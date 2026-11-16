"""Bounded review agendas and conservative before/after evidence."""
from __future__ import annotations

import hashlib
import json

SNAPSHOT_SCHEMA = "legends.vault-steward.snapshot/v1"


def finding_id(item: dict) -> str:
    identity = [item.get(key) for key in ("code", "path", "target", "surface")]
    for extra_key in ("date", "fresh_until", "key", "line", "byte_offset"):
        if item.get(extra_key) is not None:
            identity.append(f"{extra_key}:{item[extra_key]}")
    return hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()[:20]


def build_snapshot(report: dict) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": report["version"],
        "profile": report["profile"],
        "config_hash": report["config_hash"],
        "vault": {"id": report["vault"]["id"]},
        "inventory": {
            key: int(report["inventory"].get(key, 0))
            for key in ("skipped_escapes", "skipped_unreadable", "skipped_dir_symlinks")
        },
        "snapshot": dict(report["snapshot"]),
        "finding_ids": sorted({finding_id(f) for f in report.get("findings", [])}),
    }


def agenda(report: dict, limit: int = 5) -> dict:
    if not 1 <= limit <= 25:
        raise ValueError("review limit must be between 1 and 25")
    notes = {n["path"]: n for n in report.get("notes", [])}
    groups: dict[str, list] = {}
    for item in report["findings"]:
        item["id"] = finding_id(item)
        groups.setdefault(item.get("path") or "(scan coverage)", []).append(item)
    selected = []
    for path, findings in list(groups.items())[:limit]:
        note = notes.get(path, {})
        codes = {f["code"] for f in findings}
        if any(c.startswith("path.") or c == "note.unreadable" for c in codes):
            action = "Resolve scan coverage before drawing conclusions about these files."
        elif any(c.startswith("operations.") for c in codes):
            action = "Read the project or session and linked evidence. Establish actual status, owner, next action and blockers. Refresh the review date only after verification; preserve prior decisions."
        elif any(c.startswith("freshness.") for c in codes):
            action = "Check current facts against their sources; shorten the route by linking to preserved detail. Age alone does not prove a claim is false."
        elif any(c.startswith("link.") for c in codes):
            action = "Read the source and intended destination; propose an exact link correction. Do not invent a missing destination."
        elif "graph.orphan_defect" in codes:
            action = "Check whether this procedure or route is active, then propose a link from its canonical index. Preserve history."
        else:
            action = "Read and classify this item before proposing its canonical home. Being unlinked does not make a note disposable."
        selected.append({"path": path, "source_sha256": note.get("sha256"),
                         "classification_reasons": note.get("reasons", []),
                         "finding_ids": [f["id"] for f in findings], "next_action": action})
    return {"limit": limit, "total_groups": len(groups), "deferred_groups": max(0, len(groups)-limit),
            "items": selected, "authority": "Review proposals only; source hashes are evidence, not permission to edit."}


def _extract_finding_ids(report: dict) -> set[str]:
    schema = report.get("schema")
    if schema == "legends.vault-steward.observe/v1":
        findings = report.get("findings")
        if not isinstance(findings, list) or not all(isinstance(f, dict) and "code" in f for f in findings):
            raise ValueError("baseline findings must be objects with codes")
        return {finding_id(f) for f in findings}
    if schema == SNAPSHOT_SCHEMA:
        if "finding_ids" in report:
            fids = report.get("finding_ids")
            if not isinstance(fids, list) or not all(isinstance(fid, str) and fid for fid in fids):
                raise ValueError("snapshot baseline finding_ids must be non-empty strings")
            return set(fids)
        findings = report.get("findings")
        if isinstance(findings, list) and all(isinstance(f, dict) and "code" in f for f in findings):
            return {finding_id(f) for f in findings}
        raise ValueError("snapshot baseline is missing finding_ids")
    raise ValueError("baseline requires a current full snapshot report")


def compare(before: dict, after: dict) -> dict:
    for report in (before, after):
        if not isinstance(report, dict) or report.get("schema") not in {"legends.vault-steward.observe/v1", SNAPSHOT_SCHEMA} or not isinstance(report.get("snapshot"), dict):
            raise ValueError("baseline requires a current full snapshot report")
        if not isinstance(report.get("vault"), dict) or not report["vault"].get("id") or not isinstance(report.get("inventory"), dict):
            raise ValueError("baseline is missing vault, inventory or findings")
    old_findings = _extract_finding_ids(before)
    new_findings = _extract_finding_ids(after)
    for key in ("config_hash", "version"):
        if before.get(key) != after.get(key):
            raise ValueError(f"baseline {key} differs; start a new baseline")
    if before["vault"]["id"] != after["vault"]["id"]:
        raise ValueError("baseline belongs to a different vault")
    old, new = before["snapshot"], after["snapshot"]
    incomplete = any(after["inventory"].get(k, 0) or before["inventory"].get(k, 0)
                     for k in ("skipped_escapes", "skipped_unreadable", "skipped_dir_symlinks"))
    return {"added_notes": sorted(new.keys()-old.keys()), "removed_notes": sorted(old.keys()-new.keys()),
            "changed_notes": sorted(k for k in new.keys() & old.keys() if new[k] != old[k]),
            "new_findings": sorted(new_findings-old_findings),
            "persisting_findings": sorted(new_findings & old_findings),
            "no_longer_observed": sorted(old_findings-new_findings),
            "coverage_incomplete": incomplete,
            "interpretation": "No longer observed is not proof of repair. Check removed notes, scan coverage, and the intended outcome."}
