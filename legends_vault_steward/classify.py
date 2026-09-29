from __future__ import annotations

import fnmatch
from collections import defaultdict

from .models import NoteRecord, RECORD_CLASSES


def _frontmatter_type(note: NoteRecord) -> str | None:
    value = note.frontmatter.get("type")
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    return str(value).strip().lower()


def _rule_matches(rule: dict, note: NoteRecord) -> bool:
    path = note.path
    name = path.rsplit("/", 1)[-1]
    if "paths" in rule and path not in rule["paths"]:
        return False
    if "path_prefix" in rule and not path.startswith(rule["path_prefix"]):
        return False
    if "name_prefix" in rule and not name.startswith(rule["name_prefix"]):
        return False
    if "name_contains" in rule and rule["name_contains"].lower() not in name.lower():
        return False
    if "name_glob" in rule and not fnmatch.fnmatch(name, rule["name_glob"]):
        return False
    if "frontmatter_type" in rule:
        fm_type = _frontmatter_type(note)
        allowed = {item.lower() for item in rule["frontmatter_type"]}
        if fm_type not in allowed:
            return False
    required = {"paths", "path_prefix", "name_prefix", "name_contains", "name_glob", "frontmatter_type"}
    return any(key in rule for key in required)


def classify_note(note: NoteRecord, config: dict) -> None:
    scores: dict[str, float] = defaultdict(float)
    reasons: dict[str, list[str]] = defaultdict(list)
    for rule in config.get("classifier_rules", []):
        if not _rule_matches(rule, note):
            continue
        record_class = rule["class"]
        weight = float(rule.get("weight", 0.5))
        if weight > scores[record_class]:
            scores[record_class] = weight
        reasons[record_class].append(rule["id"])

    if not scores:
        note.record_class = str(config.get("default_class", "E"))
        note.confidence = float(config.get("default_confidence", 0.35))
        note.reasons = [str(config.get("default_reason", "DEFAULT_UNCLASSIFIED"))]
        return

    ranked = sorted(
        scores.items(),
        key=lambda item: (item[1], -RECORD_CLASSES.index(item[0]) if item[0] in RECORD_CLASSES else -99),
        reverse=True,
    )
    winner, best = ranked[0]
    runner = ranked[1][1] if len(ranked) > 1 else 0.0
    if runner <= 0:
        confidence = best
    else:
        confidence = max(0.35, min(0.99, best / (best + runner * 0.65)))
        if best - runner >= 0.25:
            confidence = max(confidence, best)
    note.record_class = winner
    note.confidence = round(min(0.99, confidence), 3)
    note.reasons = reasons[winner]


def classify_notes(notes: dict[str, NoteRecord], config: dict) -> None:
    for note in notes.values():
        classify_note(note, config)
