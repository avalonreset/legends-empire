from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}

RECORD_CLASSES = ("A", "B", "C", "D", "E", "F", "G", "H", "I")

CLASS_LABELS = {
    "A": "Living route",
    "B": "Law / procedure",
    "C": "Canonical knowledge",
    "D": "Evidence",
    "E": "Capture",
    "F": "Imported shelf",
    "G": "Scratch",
    "H": "Overlay",
    "I": "Packet",
}


@dataclass(frozen=True)
class LinkRef:
    raw: str
    target: str
    kind: str  # wikilink | embed | markdown
    heading: str | None = None
    alias: str | None = None
    source_path: str = ""
    status: str = "pending"  # resolved | unresolved | ambiguous | self | attachment | skipped
    resolved_path: str | None = None
    candidates: tuple[str, ...] = ()


@dataclass
class NoteRecord:
    path: str
    abs_path: str
    sha256: str
    bytes: int
    mtime_ns: int
    words: int
    title: str | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)
    aliases: tuple[str, ...] = ()
    outbound: list[LinkRef] = field(default_factory=list)
    inbound_from: list[str] = field(default_factory=list)
    record_class: str = "E"
    confidence: float = 0.4
    reasons: list[str] = field(default_factory=list)
    injection_like: bool = False
    dated_bullets: int = 0
    updated: str | None = None
    symlink: bool = False

    @property
    def in_degree(self) -> int:
        return len(self.inbound_from)

    @property
    def out_degree(self) -> int:
        return sum(1 for link in self.outbound if link.status in {"resolved", "unresolved", "ambiguous"})

    @property
    def resolved_out_degree(self) -> int:
        return sum(1 for link in self.outbound if link.status == "resolved")

    @property
    def orphan(self) -> bool:
        return self.in_degree == 0

    @property
    def dead_end(self) -> bool:
        return self.out_degree == 0

    @property
    def isolated(self) -> bool:
        return self.in_degree == 0 and self.resolved_out_degree == 0


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    path: str | None
    record_class: str | None
    message: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        extra = payload.pop("extra") or {}
        payload.update(extra)
        return payload


@dataclass
class FreshnessResult:
    id: str
    path: str
    present: bool
    ok: bool
    words: int | None = None
    max_words: int | None = None
    dated_bullets: int | None = None
    max_living_bullets: int | None = None
    age_days: float | None = None
    max_age_days: float | None = None
    updated: str | None = None
    breaches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
