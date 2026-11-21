from __future__ import annotations

from datetime import date, datetime, timezone

from .models import Finding, FreshnessResult, NoteRecord


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _age_days(note: NoteRecord, now: datetime) -> float | None:
    updated = _parse_date(note.updated)
    if updated is not None:
        return max(0.0, float((now.date() - updated).days))
    # fallback to filesystem mtime when frontmatter is silent
    try:
        mtime = datetime.fromtimestamp(note.mtime_ns / 1_000_000_000, tz=timezone.utc)
        return max(0.0, (now - mtime).total_seconds() / 86400.0)
    except (OverflowError, OSError, ValueError):
        return None


def evaluate_surfaces(notes: dict[str, NoteRecord], config: dict, now: datetime) -> tuple[list[FreshnessResult], list[Finding]]:
    results: list[FreshnessResult] = []
    findings: list[Finding] = []
    surface_paths: set[str] = set()
    explicit_paths = {
        s["path"]
        for s in config.get("surfaces", [])
        if isinstance(s, dict) and isinstance(s.get("path"), str) and not s.get("optional")
    }
    for surface in config.get("surfaces", []):
        path = surface["path"]
        if surface.get("optional") and path in explicit_paths:
            continue
        surface_paths.add(path)
        role = surface.get("role")
        note = notes.get(path)
        if note is None:
            if surface.get("optional"):
                continue
            result = FreshnessResult(
                id=surface["id"],
                path=path,
                present=False,
                ok=False,
                role=role,
                breaches=["missing"],
            )
            results.append(result)
            findings.append(
                Finding(
                    code="freshness.missing_named_surface",
                    severity="medium",
                    path=path,
                    record_class=None,
                    message=f"Named surface {surface['id']} is missing",
                    extra={"surface": surface["id"]},
                )
            )
            continue

        breaches: list[str] = []
        words = note.words
        n_bytes = note.bytes
        bullets = note.dated_bullets
        d_lines = max(note.dated_lines, bullets)
        max_words = surface.get("max_words")
        max_bytes = surface.get("max_bytes")
        max_bullets = surface.get("max_living_bullets")
        max_dated_lines = surface.get("max_dated_lines")
        if role == "core" and max_dated_lines is None:
            max_dated_lines = 5
        max_age = surface.get("max_age_days")
        if role == "hot" and max_age is None:
            max_age = 14
        age = _age_days(note, now) if max_age is not None else None
        if max_bytes is not None and n_bytes > int(max_bytes):
            breaches.append("byte_budget")
            findings.append(
                Finding(
                    code="budget.surface_bytes_exceeded",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} is {n_bytes} bytes; contract max is {int(max_bytes)} bytes",
                    extra={"surface": surface["id"], "bytes": n_bytes, "max_bytes": int(max_bytes)},
                )
            )
        if max_words is not None and words > int(max_words):
            breaches.append("word_count")
            findings.append(
                Finding(
                    code="freshness.word_count_exceeded",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} is {words} words; contract max is {max_words}",
                    extra={"surface": surface["id"], "words": words, "max_words": max_words},
                )
            )
        if max_bullets is not None and bullets > int(max_bullets):
            breaches.append("living_bullets")
            findings.append(
                Finding(
                    code="freshness.bullet_count_exceeded",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} has {bullets} dated bullets; contract max is {max_bullets}",
                    extra={"surface": surface["id"], "dated_bullets": bullets, "max_living_bullets": max_bullets},
                )
            )
        if max_dated_lines is not None and d_lines > int(max_dated_lines):
            breaches.append("state_leak")
            findings.append(
                Finding(
                    code="freshness.state_leak_in_core",
                    severity="medium",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} has {d_lines} dated lines; max_dated_lines is {int(max_dated_lines)}",
                    extra={"surface": surface["id"], "dated_lines": d_lines, "max_dated_lines": int(max_dated_lines)},
                )
            )
        if max_age is not None and age is not None and age > float(max_age):
            breaches.append("stale")
            findings.append(
                Finding(
                    code="freshness.stale_named_surface",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} is {age:.1f} days old; contract max is {max_age} days",
                    extra={"surface": surface["id"], "age_days": round(age, 2), "max_age_days": max_age},
                )
            )
        raw_fu = note.frontmatter.get("fresh_until")
        if isinstance(raw_fu, list):
            raw_fu = raw_fu[0] if raw_fu else None
        fu_date = _parse_date(str(raw_fu) if raw_fu is not None else None)
        if fu_date is not None and fu_date < now.date():
            breaches.append("expired_fresh_until")
            findings.append(
                Finding(
                    code="freshness.expired_fresh_until",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"{surface['id']} declared fresh_until {fu_date.isoformat()} has passed",
                    extra={"surface": surface["id"], "fresh_until": fu_date.isoformat()},
                )
            )
        results.append(
            FreshnessResult(
                id=surface["id"],
                path=path,
                present=True,
                ok=not breaches,
                words=words,
                max_words=max_words,
                bytes=n_bytes,
                max_bytes=max_bytes,
                dated_bullets=bullets,
                max_living_bullets=max_bullets,
                dated_lines=d_lines if max_dated_lines is not None else None,
                max_dated_lines=max_dated_lines,
                age_days=None if age is None else round(age, 2),
                max_age_days=max_age,
                updated=note.updated,
                fresh_until=fu_date.isoformat() if fu_date is not None else None,
                role=role,
                breaches=breaches,
            )
        )

    for path in sorted(notes):
        if path in surface_paths:
            continue
        note = notes[path]
        raw_fu = note.frontmatter.get("fresh_until")
        if isinstance(raw_fu, list):
            raw_fu = raw_fu[0] if raw_fu else None
        fu_date = _parse_date(str(raw_fu) if raw_fu is not None else None)
        if fu_date is not None and fu_date < now.date():
            findings.append(
                Finding(
                    code="freshness.expired_fresh_until",
                    severity="high",
                    path=path,
                    record_class=note.record_class,
                    message=f"Declared fresh_until {fu_date.isoformat()} has passed",
                    extra={"fresh_until": fu_date.isoformat()},
                )
            )
    return results, findings
