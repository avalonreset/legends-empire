"""Release disclosure scanning for personal data and private terms.

Two layers run over every scanned text:

* A generic layer that ships with the repository: North American phone
  numbers written with separators (the fictional 555-01xx exchange
  range is allowed) and Tailscale CGNAT addresses (the 100.64/10 range).
* An optional private layer loaded from a denylist kept outside the
  repository, selected with ``--denylist``, ``LEGENDS_RELEASE_DENYLIST`` or
  the default ``~/.empire/secrets/legends-release-denylist.txt`` when it
  exists. Public CI has no denylist and runs the generic layer only; the
  owner release step passes ``--require-denylist`` so a missing list fails
  closed.

Denylist format: UTF-8, one entry per line, ``#`` comments and blank lines
ignored, symlinks refused. Text and entries are normalized the same way:
NFKC, removal of format characters (zero-width and similar), folding of
common Cyrillic/Greek look-alike letters, then case folding. Text is split
into atomic tokens on separators and on camel-case and letter/digit
transitions. Matching is tiered by the entry's normalized length:

* ``word:term`` or 3 to 5 characters (or a number under 8 digits): the
  entry's tokens equal consecutive text tokens.
* 6 or 7 characters: the entry equals a run of whole consecutive tokens
  joined together (``MyClient``, ``my-client``, ``my\\nclient``).
* 8 or more characters, or ``compound:term`` (6 or more): substring of the
  joined token stream of the whole document. Pure-hex entries shorter than
  12 characters ignore 40- and 64-character hex digests.

The private layer also scans a decoded view (``\\uXXXX`` and ``\\xXX``
escapes, URL percent-encoding, HTML entities, and base64 runs that decode
to printable UTF-8) and printable strings inside binary files. Findings
name the entry number and a short SHA-256 prefix of the normalized entry,
never the term or the matching text. Known limit: compressed streams (for
example PDF Flate data) are not inflated.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import unquote

DENYLIST_ENV = "LEGENDS_RELEASE_DENYLIST"
REQUIRE_ENV = "LEGENDS_RELEASE_REQUIRE_DENYLIST"
DEFAULT_DENYLIST = Path.home() / ".empire" / "secrets" / "legends-release-denylist.txt"
MIN_TERM = 3
MIN_COMPOUND = 6
MAX_DENYLIST_BYTES = 256 * 1024
MAX_ENTRIES = 2000

Error = dict[str, str]

_LOOKALIKES = {
    # Cyrillic lower / upper
    "а": "a", "е": "e", "ё": "e", "о": "o", "р": "p",
    "с": "c", "у": "y", "х": "x", "і": "i", "ї": "i",
    "ѕ": "s", "ј": "j", "к": "k", "м": "m", "т": "t",
    "н": "h", "в": "b", "ґ": "r", "ԁ": "d", "ԛ": "q",
    "ԝ": "w",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C",
    "У": "Y", "Х": "X", "І": "I", "Ѕ": "S", "Ј": "J",
    "К": "K", "М": "M", "Т": "T", "Н": "H", "В": "B",
    # Greek lower / upper
    "α": "a", "ε": "e", "ο": "o", "ρ": "p", "ι": "i",
    "κ": "k", "ν": "v", "τ": "t", "υ": "u", "χ": "x",
    "β": "b", "η": "n", "μ": "u", "ω": "w",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
}
_CONFUSABLES = str.maketrans(_LOOKALIKES)

_PHONE_RE = re.compile(
    r"(?<![\w.+])(?:\+?1[\s.\-]?)?(?:\(([2-9]\d{2})\)\s?|([2-9]\d{2})[\s.\-])"
    r"([2-9]\d{2})[\s.\-](\d{4})(?![\w.])"
)
_TAILNET_RE = re.compile(
    r"(?<![\w.])100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.(\d{1,3})\.(\d{1,3})(?![\w.])"
)
_DIGEST_RE = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{64}|[0-9A-Fa-f]{40})(?![0-9A-Fa-f])")
_HEX_TERM_RE = re.compile(r"[0-9a-f]+")
_RUN_RE = re.compile(r"[^\W_]+")
_ESCAPE_RE = re.compile(r"\\u([0-9A-Fa-f]{4})|\\x([0-9A-Fa-f]{2})|\\U([0-9A-Fa-f]{8})")
_BASE64_RE = re.compile(r"[A-Za-z0-9+/_-]{24,}={0,2}")
_ASCII_STRINGS_RE = re.compile(rb"[\x20-\x7e\t\r\n]{4,}")
_UTF16_STRINGS_RE = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


class DisclosureError(ValueError):
    """The denylist is unreadable or malformed. Messages never echo entries."""


@dataclass(frozen=True)
class Entry:
    index: int
    tier: str  # "word", "joined" or "stream"
    normalized: str
    tokens: tuple[str, ...]
    digest: str
    hex_only: bool


@dataclass(frozen=True)
class Denylist:
    source: str
    entries: tuple[Entry, ...]


def _prepare(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return text.translate(_CONFUSABLES)


def _split_run(run: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for i in range(1, len(run)):
        prev, cur = run[i - 1], run[i]
        nxt = run[i + 1] if i + 1 < len(run) else ""
        boundary = (
            (prev.isdigit() != cur.isdigit())
            or (prev.islower() and cur.isupper())
            or (prev.isupper() and cur.isupper() and nxt.islower())
        )
        if boundary:
            parts.append(run[start:i])
            start = i
    parts.append(run[start:])
    return parts


def tokenize(text: str) -> list[str]:
    """Atomic, case-folded tokens in document order."""

    out: list[str] = []
    for run in _RUN_RE.findall(_prepare(text)):
        out.extend(part.casefold() for part in _split_run(run) if part)
    return out


def normalize_stream(text: str) -> str:
    return "".join(tokenize(text))


def _entry(index: int, line_number: int, line: str) -> Entry:
    lowered = line.casefold()
    mode = None
    for prefix in ("word:", "compound:"):
        if lowered.startswith(prefix):
            mode = prefix[:-1]
            line = line[len(prefix):].strip()
            break
    term_tokens = tuple(tokenize(line))
    normalized = "".join(term_tokens)
    if len(normalized) < MIN_TERM:
        raise DisclosureError(f"denylist line {line_number} is shorter than {MIN_TERM} letters or digits")
    if mode == "compound" and len(normalized) < MIN_COMPOUND:
        raise DisclosureError(f"denylist line {line_number} compound: entry is shorter than {MIN_COMPOUND}")
    if mode == "word" or (mode is None and (len(normalized) <= 5 or (normalized.isdigit() and len(normalized) < 8))):
        tier = "word"
    elif mode is None and len(normalized) <= 7:
        tier = "joined"
    else:
        tier = "stream"
    digest = hashlib.sha256(f"{tier}:{normalized}".encode()).hexdigest()[:12]
    hex_only = bool(_HEX_TERM_RE.fullmatch(normalized)) and len(normalized) < 12
    return Entry(index, tier, normalized, term_tokens, digest, hex_only)


def parse_denylist(data: bytes, *, source: str = "denylist") -> Denylist:
    if len(data) > MAX_DENYLIST_BYTES:
        raise DisclosureError("denylist exceeds size limit")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DisclosureError("denylist must be UTF-8") from exc
    entries: list[Entry] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(_entry(len(entries) + 1, line_number, line))
        if len(entries) > MAX_ENTRIES:
            raise DisclosureError("denylist has too many entries")
    if not entries:
        raise DisclosureError("denylist has no entries")
    return Denylist(source=source, entries=tuple(entries))


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def load_denylist(path: str | os.PathLike[str]) -> Denylist:
    candidate = Path(path)
    try:
        info = os.lstat(candidate)
    except OSError as exc:
        raise DisclosureError("denylist file is unreadable") from exc
    if _is_link(info) or not stat.S_ISREG(info.st_mode):
        raise DisclosureError("denylist must be a regular file, not a link")
    try:
        data = candidate.read_bytes()
    except OSError as exc:
        raise DisclosureError("denylist file is unreadable") from exc
    return parse_denylist(data, source=candidate.name or "denylist")


_override_path: str | None = None
_override_require: bool | None = None
_cache: dict[tuple[str, int, int], Denylist] = {}


def configure(path: str | None = None, *, require: bool | None = None) -> None:
    """Set the process-wide denylist selection (CLI flags win over env)."""

    global _override_path, _override_require
    _override_path = path
    _override_require = require


def _selected_path() -> str | None:
    value = _override_path if _override_path is not None else os.environ.get(DENYLIST_ENV)
    if value:
        return value
    try:
        if DEFAULT_DENYLIST.exists() or DEFAULT_DENYLIST.is_symlink():
            return str(DEFAULT_DENYLIST)
    except OSError:
        return None
    return None


def _required() -> bool:
    if _override_require is not None:
        return _override_require
    return os.environ.get(REQUIRE_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _err(code: str, path: str, message: str) -> Error:
    return {"code": code, "path": path, "message": message}


def resolve() -> tuple[Denylist | None, list[Error]]:
    """Return the active denylist and configuration errors (fail closed)."""

    path = _selected_path()
    if path is None:
        if _required():
            return None, [
                _err("denylist_required", "denylist", "a private release denylist is required but none was selected")
            ]
        return None, []
    try:
        info = os.lstat(path)
        key = (str(Path(path)), info.st_mtime_ns, info.st_size)
    except OSError:
        return None, [_err("denylist_unreadable", "denylist", "selected denylist file is unreadable")]
    cached = _cache.get(key)
    if cached is not None:
        return cached, []
    try:
        loaded = load_denylist(path)
    except DisclosureError as exc:
        return None, [_err("denylist_invalid", "denylist", str(exc))]
    _cache.clear()
    _cache[key] = loaded
    return loaded, []


def active_denylist() -> Denylist | None:
    loaded, _ = resolve()
    return loaded


def _printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    good = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return good / len(text)


def _decode_base64_runs(text: str) -> list[str]:
    decoded: list[str] = []
    for match in _BASE64_RE.finditer(text):
        token = match.group(0)
        if _HEX_TERM_RE.fullmatch(token.lower()):
            continue  # hex digests are not base64 payloads
        padded = token + "=" * (-len(token) % 4)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = decoder(padded)
                value = raw.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                continue
            if _printable_ratio(value) >= 0.8:
                decoded.append(value)
                break
    return decoded


def decoded_view(text: str) -> str:
    """Alternative renderings of ``text`` that may hide a term."""

    parts: list[str] = []

    def _unescape(match: re.Match[str]) -> str:
        value = match.group(1) or match.group(2) or match.group(3)
        try:
            return chr(int(value, 16))
        except (ValueError, OverflowError):
            return match.group(0)

    if "\\" in text:
        escaped = _ESCAPE_RE.sub(_unescape, text)
        if escaped != text:
            parts.append(escaped)
    if "%" in text:
        unquoted = unquote(text)
        if unquoted != text:
            parts.append(unquoted)
    if "&" in text:
        unescaped = html.unescape(text)
        if unescaped != text:
            parts.append(unescaped)
    parts.extend(_decode_base64_runs(text))
    return "\n".join(parts)


def binary_strings(data: bytes) -> str:
    """Printable ASCII and UTF-16LE runs inside binary content."""

    runs = [m.group(0).decode("ascii") for m in _ASCII_STRINGS_RE.finditer(data)]
    runs.extend(m.group(0).decode("utf-16-le") for m in _UTF16_STRINGS_RE.finditer(data))
    return "\n".join(runs)


class _View:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.token_set = set(self.tokens)
        self._stream: str | None = None
        self._masked: str | None = None
        self._source = text

    def stream(self, masked: bool) -> str:
        if masked:
            if self._masked is None:
                self._masked = normalize_stream(_DIGEST_RE.sub(" ", self._source))
            return self._masked
        if self._stream is None:
            self._stream = "".join(self.tokens)
        return self._stream

    def matches(self, entry: Entry) -> bool:
        if entry.tier == "word":
            first = entry.tokens[0]
            if first not in self.token_set:
                return False
            if len(entry.tokens) == 1:
                return True
            width = len(entry.tokens)
            return any(
                tuple(self.tokens[i : i + width]) == entry.tokens
                for i, token in enumerate(self.tokens)
                if token == first
            )
        if entry.tier == "joined":
            target = entry.normalized
            tokens = self.tokens
            for i, token in enumerate(tokens):
                if not target.startswith(token):
                    continue
                joined = token
                j = i + 1
                while len(joined) < len(target) and j < len(tokens):
                    joined += tokens[j]
                    j += 1
                if joined == target:
                    return True
            return False
        return entry.normalized in self.stream(entry.hex_only)


def private_findings(path: str, text: str, denylist: Denylist | None, *, extra: str = "") -> list[Error]:
    if denylist is None:
        return []
    views = [_View(text)]
    alternate = decoded_view(text)
    if extra:
        alternate = alternate + "\n" + extra if alternate else extra
    if alternate:
        views.append(_View(alternate))
    errors: list[Error] = []
    for entry in denylist.entries:
        if any(view.matches(entry) for view in views):
            errors.append(
                _err(
                    "private_term",
                    path,
                    f"content matches private denylist entry #{entry.index} (sha256:{entry.digest})",
                )
            )
    return errors


def scan_text(path: str, text: str, denylist: Denylist | None = None) -> list[Error]:
    errors: list[Error] = []
    for match in _PHONE_RE.finditer(text):
        exchange, line = match.group(3), match.group(4)
        if exchange == "555" and 100 <= int(line) <= 199:
            continue
        errors.append(_err("phone_number", path, "content contains a phone number outside the fictional 555-01xx range"))
        break
    for match in _TAILNET_RE.finditer(text):
        if all(0 <= int(part) <= 255 for part in match.groups()[1:]):
            errors.append(_err("tailnet_address", path, "content contains a Tailscale CGNAT (100.64/10) address"))
            break
    errors.extend(private_findings(path, text, denylist))
    return errors


def scan_binary(path: str, data: bytes, denylist: Denylist | None = None) -> list[Error]:
    if denylist is None:
        return []
    strings = binary_strings(data)
    return private_findings(path, strings, denylist) if strings else []


def scan_texts(items: Iterable[tuple[str, str]], denylist: Denylist | None) -> list[Error]:
    errors: list[Error] = []
    for label, text in items:
        errors.extend(scan_text(label, text, denylist))
    return errors


def commit_messages(repo_root: Path, revision_range: str | None, run_git) -> tuple[list[tuple[str, str]], list[Error]]:
    """Return ``(label, message)`` for commits in ``revision_range``.

    ``None`` selects ``<last-tag>..HEAD`` and fails closed when no tag is
    reachable; pass ``HEAD`` explicitly to scan all history. Shallow clones
    are refused because their history is incomplete. ``run_git`` is the
    release module's sanitized Git runner.
    """

    shallow = run_git(repo_root, ["rev-parse", "--is-shallow-repository"])
    if shallow.returncode != 0:
        return [], [_err("git_unavailable", "git", "cannot inspect the repository history")]
    if shallow.stdout.strip() == b"true":
        return [], [_err("shallow_history", "git", "commit messages cannot be audited in a shallow clone")]
    if revision_range is None:
        described = run_git(repo_root, ["describe", "--tags", "--abbrev=0", "HEAD"])
        if described.returncode != 0 or not described.stdout.strip():
            return [], [_err("no_release_tag", "git", "no tag is reachable from HEAD; pass --range HEAD to scan all history")]
        revision_range = described.stdout.decode("utf-8", "replace").strip() + "..HEAD"
    if revision_range.startswith("-"):
        return [], [_err("invalid_range", "git", "revision range cannot start with '-'")]
    listed = run_git(repo_root, ["log", "--format=%H%x00%B%x1e", revision_range, "--"])
    if listed.returncode != 0:
        return [], [_err("git_log_failed", "git", "cannot read commit messages for the selected range")]
    items: list[tuple[str, str]] = []
    for record in listed.stdout.decode("utf-8", "replace").split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        sha, _, message = record.partition("\x00")
        items.append((f"commit:{sha.strip()[:12]}", message))
    return items, []


def summarize(errors: Sequence[Error], denylist: Denylist | None, scanned: int) -> dict:
    return {
        "ok": not errors,
        "kind": "disclosure_audit",
        "errors": list(errors),
        "summary": {
            "error_count": len(errors),
            "scanned": scanned,
            "private_layer": "active" if denylist is not None else "absent",
            "private_entries": len(denylist.entries) if denylist is not None else 0,
        },
    }
