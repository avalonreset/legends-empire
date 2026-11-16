from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .classify import classify_notes
from .config import config_hash, load_config
from .freshness import evaluate_surfaces
from .inventory import walk_notes
from .law import collect_law_files, evaluate_laws
from .models import Finding, NoteRecord, SEVERITY_ORDER
from .paths import (
    MAX_BASELINE_JSON_BYTES,
    VaultRoot,
    _is_symlink_or_junction,
    is_contained,
    read_bounded_json,
    resolve_vault_root,
)
from .wikilinks import count_unresolved_to_unreadable, resolve_notes
from .operations import operating_records

_ONTOLOGY_FINDING_SEVERITY = {
    "ontology_duplicate_entity_id": "high",
    "ontology_certified_without_value": "high",
    "frontmatter_nested_unsupported": "medium",
    "frontmatter_mixed_newlines": "medium",
}


def _note_summary(note: NoteRecord) -> dict[str, Any]:
    return {
        "path": note.path,
        "class": note.record_class,
        "confidence": note.confidence,
        "reasons": list(note.reasons),
        "sha256": note.sha256,
        "bytes": note.bytes,
        "words": note.words,
        "in_degree": note.in_degree,
        "out_degree": note.out_degree,
        "resolved_out_degree": note.resolved_out_degree,
        "orphan": note.orphan,
        "dead_end": note.dead_end,
        "isolated": note.isolated,
        "updated": note.updated,
        "injection_like": note.injection_like,
        "symlink": note.symlink,
    }


def _graph_stats(notes: dict[str, NoteRecord]) -> dict[str, Any]:
    unresolved_occ = 0
    unresolved_targets: Counter[str] = Counter()
    ambiguous_occ = 0
    ambiguous_targets: Counter[str] = Counter()
    for note in notes.values():
        for link in note.outbound:
            if link.status == "unresolved":
                unresolved_occ += 1
                unresolved_targets[link.target] += 1
            elif link.status == "ambiguous":
                ambiguous_occ += 1
                ambiguous_targets[link.target] += 1
    orphans = sum(1 for note in notes.values() if note.orphan)
    dead_ends = sum(1 for note in notes.values() if note.dead_end)
    isolated = sum(1 for note in notes.values() if note.isolated)
    no_inbound = orphans
    return {
        "orphans": orphans,
        "dead_ends": dead_ends,
        "isolated": isolated,
        "no_inbound": no_inbound,
        "unresolved_occurrences": unresolved_occ,
        "unresolved_targets": len(unresolved_targets),
        "ambiguous_occurrences": ambiguous_occ,
        "ambiguous_targets": len(ambiguous_targets),
        "top_unresolved": unresolved_targets.most_common(20),
        "top_ambiguous": ambiguous_targets.most_common(20),
    }


def _class_stats(notes: dict[str, NoteRecord]) -> dict[str, Any]:
    buckets: dict[str, dict[str, int]] = {}
    for letter in "ABCDEFGHI":
        members = [note for note in notes.values() if note.record_class == letter]
        buckets[letter] = {
            "count": len(members),
            "orphans": sum(1 for note in members if note.orphan),
            "dead_ends": sum(1 for note in members if note.dead_end),
            "isolated": sum(1 for note in members if note.isolated),
        }
    return buckets


def _build_findings(
    notes: dict[str, NoteRecord],
    inventory_findings: list[Finding],
    freshness_findings: list[Finding],
    config: dict,
) -> list[Finding]:
    policy = config.get("findings", {})
    findings = list(inventory_findings) + list(freshness_findings)
    defect = set(policy.get("orphan_defect_classes", ["A", "B"]))
    queue = set(policy.get("orphan_queue_classes", ["E"]))
    normal = set(policy.get("orphan_normal_classes", ["D", "F", "G", "I"]))
    core = set("ABCDEFGHI")
    emit_normal = bool(policy.get("emit_orphan_normal", False))
    ambiguous_seen: set[str] = set()

    for path in sorted(notes):
        note = notes[path]
        if note.injection_like:
            findings.append(
                Finding(
                    code="note.injection_like",
                    severity="medium",
                    path=path,
                    record_class=note.record_class,
                    message="Note text looks like an instruction injection; treated as inert data",
                )
            )
        if note.orphan:
            if note.record_class in defect and path not in config.get("entrypoints", []):
                findings.append(
                    Finding(
                        code="graph.orphan_defect",
                        severity="high",
                        path=path,
                        record_class=note.record_class,
                        message="Class A/B orphan: this class requires a route",
                    )
                )
            elif note.record_class in queue:
                findings.append(
                    Finding(
                        code="graph.orphan_queue",
                        severity="low",
                        path=path,
                        record_class=note.record_class,
                        message="Class E orphan: candidate for the human review queue",
                    )
                )
            elif note.record_class in normal and emit_normal:
                findings.append(
                    Finding(
                        code="graph.orphan_normal",
                        severity="info",
                        path=path,
                        record_class=note.record_class,
                        message="Expected orphan for this record class",
                    )
                )
        for link in note.outbound:
            if link.status == "unresolved" and note.record_class in core:
                findings.append(
                    Finding(
                        code="link.unresolved",
                        severity="medium",
                        path=path,
                        record_class=note.record_class,
                        message=f"Unresolved wikilink [[{link.target}]]",
                        extra={"target": link.target, "kind": link.kind},
                    )
                )
            elif link.status == "ambiguous":
                ambiguous_seen.add(link.target)
                findings.append(
                    Finding(
                        code="link.ambiguous",
                        severity="medium",
                        path=path,
                        record_class=note.record_class,
                        message=f"Ambiguous wikilink [[{link.target}]] matches {len(link.candidates)} notes",
                        extra={"target": link.target, "candidates": list(link.candidates)},
                    )
                )

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item.severity, 9),
            item.code,
            item.path or "",
            item.message,
        )
    )
    return findings


def observe(
    vault_path: str,
    config_path: str | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
    include_notes: str = "all",
    review_limit: int = 5,
    profile: str = "generic",
) -> dict[str, Any]:
    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    config = load_config(config_path, profile)
    vault = resolve_vault_root(vault_path)
    inventory = walk_notes(vault, config)
    resolve_notes(inventory.notes)
    classify_notes(inventory.notes, config)
    freshness, freshness_findings = evaluate_surfaces(inventory.notes, config, started)
    operations, operation_findings = operating_records(inventory.notes, started, config)
    freshness_findings.extend(operation_findings)

    inventory_findings: list[Finding] = []
    # Check ontology signals if index exists (bounded and contained per R2 / O1)
    idx_path = vault.real / ".vault-meta" / "ontology" / "index.json"
    if idx_path.exists() and not _is_symlink_or_junction(idx_path) and is_contained(idx_path, vault):
        try:
            raw_idx = read_bounded_json(idx_path, max_bytes=MAX_BASELINE_JSON_BYTES)
            if isinstance(raw_idx, dict):
                for f_key, f_data in raw_idx.get("facts", {}).items():
                    if not isinstance(f_data, dict):
                        continue
                    st = f_data.get("state")
                    f_path = f_data.get("path")
                    if st == "conflict":
                        inventory_findings.append(
                            Finding(
                                code="ontology.conflict",
                                severity="high",
                                path=f_path,
                                record_class=None,
                                message=f"Ontology fact {f_key} has conflicting uncertified values across notes",
                            )
                        )
                    elif st == "stale":
                        inventory_findings.append(
                            Finding(
                                code="ontology.stale_fact",
                                severity="medium",
                                path=f_path,
                                record_class=None,
                                message=f"Ontology fact {f_key} is stale (review overdue or evidence mismatch)",
                            )
                        )
                for ont_f in raw_idx.get("findings", []):
                    if not isinstance(ont_f, dict):
                        continue
                    ont_code = str(ont_f.get("code") or "")
                    if ont_code in _ONTOLOGY_FINDING_SEVERITY:
                        ont_path = ont_f.get("path")
                        ont_detail = str(ont_f.get("detail") or f"Ontology index reported {ont_code}")
                        inventory_findings.append(
                            Finding(
                                code=ont_code,
                                severity=_ONTOLOGY_FINDING_SEVERITY[ont_code],
                                path=str(ont_path) if ont_path else None,
                                record_class=inventory.notes[ont_path].record_class if ont_path in inventory.notes else None,
                                message=ont_detail,
                            )
                        )
        except Exception:
            pass
    for escaped in inventory.skipped_escapes:
        inventory_findings.append(
            Finding(
                code="path.escape",
                severity="high",
                path=escaped,
                record_class=None,
                message="Path resolved outside the vault root and was skipped",
            )
        )
    for skipped in inventory.skipped_dir_symlinks:
        inventory_findings.append(
            Finding(
                code="path.symlink_dir_skipped",
                severity="info",
                path=skipped,
                record_class=None,
                message="Directory symlink was not followed",
            )
        )

    unreadable_link_counts = count_unresolved_to_unreadable(inventory.notes, inventory.skipped_unreadable)
    surface_paths_set = {
        s["path"] for s in config.get("surfaces", []) if isinstance(s, dict) and isinstance(s.get("path"), str)
    }
    entrypoints_set = set(config.get("entrypoints", []))
    for h in config.get("entry_harnesses", []):
        if isinstance(h, dict):
            for ep in h.get("entry", []):
                if isinstance(ep, str):
                    entrypoints_set.add(ep)
            if isinstance(h.get("core"), str):
                entrypoints_set.add(h["core"])

    for unread in inventory.skipped_unreadable:
        detail = inventory.unreadable_details.get(unread, {})
        link_hits = unreadable_link_counts.get(unread, 0)
        is_critical = (
            unread in surface_paths_set
            or unread in entrypoints_set
            or link_hits >= 10
        )
        sev = "high" if is_critical else "low"
        extra: dict[str, Any] = {}
        if "byte_offset" in detail and detail["byte_offset"] is not None:
            offset = int(detail["byte_offset"])  # type: ignore[arg-type]
            extra["byte_offset"] = offset
            msg = f"File could not be decoded as UTF-8 (byte offset {offset}); inventory coverage is incomplete"
        else:
            msg = "File or directory could not be read; inventory coverage is incomplete"
        if link_hits > 0:
            extra["unresolved_links"] = link_hits
        inventory_findings.append(
            Finding(
                code="note.unreadable",
                severity=sev,
                path=unread,
                record_class=None,
                message=msg,
                extra=extra,
            )
        )

    if config.get("law_paths") or config.get("conflict_keys"):
        law_files = collect_law_files(vault, config)
        freshness_findings.extend(evaluate_laws(law_files, config, started.date()))

    findings = _build_findings(inventory.notes, inventory_findings, freshness_findings, config)
    ended = datetime.now(timezone.utc)
    vault_id = hashlib.sha256(vault.as_posix.encode("utf-8")).hexdigest()
    payload: dict[str, Any] = {
        "schema": "legends.vault-steward.observe/v1",
        "product": "Legends Vault Steward",
        "phase": "stewardship-candidate",
        "mode": "read-only",
        "version": __import__("legends_vault_steward").__version__,
        "run_id": run_id or str(uuid.uuid4()),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "vault": {
            "root": str(vault.given),
            "root_real": vault.as_posix,
            "id": vault_id,
        },
        "config_hash": config_hash(load_config(config_path, profile)),
        "profile": profile,
        "snapshot": {path: note.sha256 for path, note in sorted(inventory.notes.items())},
        "inventory": {
            "markdown_notes": len(inventory.notes),
            "markdown_bytes": inventory.markdown_bytes,
            "skipped_escapes": len(inventory.skipped_escapes),
            "skipped_unreadable": len(inventory.skipped_unreadable),
            "skipped_dir_symlinks": len(inventory.skipped_dir_symlinks),
        },
        "graph": _graph_stats(inventory.notes),
        "classes": _class_stats(inventory.notes),
        "freshness": [item.to_dict() for item in freshness],
        "operations": operations,
        "findings": [item.to_dict() for item in findings],
        "mutation": {
            "human_notes_written": 0,
            "commands": ["scan", "entry-audit", "digest", "doctor", "version"],
            "write_commands": [],
        },
    }
    from .review import agenda
    payload["notes"] = [_note_summary(inventory.notes[path]) for path in sorted(inventory.notes)]
    payload["review"] = agenda(payload, review_limit)
    if include_notes == "all":
        payload["notes"] = [_note_summary(inventory.notes[path]) for path in sorted(inventory.notes)]
    elif include_notes == "summary":
        payload["notes"] = [
            _note_summary(note)
            for note in inventory.notes.values()
            if note.record_class in {"A", "B"} or note.injection_like
        ]
    elif include_notes == "none":
        payload.pop("notes", None)
    return payload
