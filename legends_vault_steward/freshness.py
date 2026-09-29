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
    for surface in config.get("surfaces", []):
        path = surface["path"]
        note = notes.get(path)
        if note is None:
            result = FreshnessResult(
                id=surface["id"],
                path=path,
                present=False,
                ok=False,
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
        bullets = note.dated_bullets
        max_words = surface.get("max_words")
        max_bullets = surface.get("max_living_bullets")
        max_age = surface.get("max_age_days")
        age = _age_days(note, now) if max_age is not None else None
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
        results.append(
            FreshnessResult(
                id=surface["id"],
                path=path,
                present=True,
                ok=not breaches,
                words=words,
                max_words=max_words,
                dated_bullets=bullets,
                max_living_bullets=max_bullets,
                age_days=None if age is None else round(age, 2),
                max_age_days=max_age,
                updated=note.updated,
                breaches=breaches,
            )
        )
    return results, findings
