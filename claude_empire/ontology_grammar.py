"""Validation rules for ``operation_type: "ontology"`` transaction bundles.

Spec: ontology spec v1.1, section 5.5. An ontology bundle may only replace
existing Markdown notes inside the resolved ontology roots, and may only change
the ontology frontmatter keys. Every other byte of the note (BOM, newline style,
non-ontology frontmatter blocks in their exact order, and the body) must be
identical. Certified facts are protected: changing them needs an owner
certification digest pinned in ``read_preconditions``, and every evidence
certified fact must verify against pinned evidence.

The parser here is deliberately stricter than the general frontmatter reader.
It works on raw bytes, accepts at most one UTF-8 BOM, one uniform newline
(``\\n`` or ``\\r\\n``), and rejects Unicode line separators and control
characters so that no other reader can split a "single-line" value into extra
keys. Error messages never echo a value (only key names and pattern classes).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

BOM = b"\xef\xbb\xbf"
MAX_FRONTMATTER_BYTES = 16 * 1024
MAX_FACTS = 40
MAX_FACT_CHARS = 500
MAX_ALIAS_CHARS = 80
MAX_TITLE_CHARS = 200
MAX_FACT_SOURCE_CHARS = 300
MAX_BUNDLE_TARGETS = 150
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_NOTE_SEARCH_ENTRIES = 200_000

SCALAR_KEYS = frozenset(
    {
        "entity_id",
        "entity_type",
        "about",
        "certified_on",
        "review_after",
        "last_verified",
        "fact_source",
    }
)
LIST_KEYS = frozenset({"aliases", "alias", "certified_owner", "certified_evidence"})
KEY_PREFIXES = ("fact.", "evidence.")
DATE_KEYS = frozenset({"certified_on", "review_after", "last_verified"})

STANDALONE_PROFILE_ROOTS = ("wiki/",)
SHARED_ROOT_PROFILE_ROOTS = (
    "wiki/",
    "Projects/Active/",
    "Workspaces/Clients/Companies/",
    "Workspaces/Clients/People/",
    "Workspaces/Brands & Sites/",
)
HOME_STATE_PATH = "wiki/integrations/aimh-home/shared-root/installation.json"
WORKSPACE_CONFIG = ".claude-empire.json"
EXCLUDED_TOP_LEVEL = frozenset(
    {".raw", ".vault-meta", ".obsidian", ".git", ".claude", ".agents", ".codex", ".gemini"}
)
EXCLUDED_ANY_LEVEL = frozenset({".git", "node_modules"})
ROOT_INSTRUCTION_FILES = frozenset(
    {"agents.md", "claude.md", "gemini.md", "codex.md", "empire-entry.md"}
)
EVIDENCE_PACKAGE_DIR = "inbox/research"
EVIDENCE_PACKAGE_MEMBERS = ("manifest.json", "README.md", "response.json")

_FIELD_RE = re.compile(r"[a-z][a-z0-9_]{0,47}")
_ENTITY_RE = re.compile(r"[a-z][a-z0-9-]{1,47}")
_ENTITY_TYPE_RE = re.compile(r"[a-z][a-z0-9-]{0,47}")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_KEY_LINE_RE = re.compile(r"([A-Za-z0-9_.-]+):(?:[ \t]+(.*))?")
_CONTINUATION_ITEM_RE = re.compile(r"[ \t]*-(?:[ \t].*)?")
_STRICT_ITEM_RE = re.compile(r"( {0,8})- (.+)")
_BLOCK_SCALAR_RE = re.compile(r"[|>][0-9+-]*(?:[ \t]+#.*)?")
_SECRET_REF_RE = re.compile(r"\Asecret-ref:[A-Za-z0-9._-]+\.env#[A-Z][A-Z0-9_]*\Z")
_RAW_REF_RE = re.compile(r"raw:sha256:([0-9a-f]{64})")
_BANK_REF_RE = re.compile(r"bank:([A-Za-z0-9][A-Za-z0-9._-]{0,63})/([0-9a-f]{64})")
_NOTE_REF_RE = re.compile(r"note:\[\[([^\[\]|#\r\n]+)\]\]#([^\r\n]+)")
_YAML_NULLS = frozenset({"~", "null", "Null", "NULL"})


class OntologyRuleError(ValueError):
    """A spec 5.5 violation; ``code`` is the stable machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Unparseable:
    """Marker for an old ontology value that the strict grammar cannot read."""

    _instance: "_Unparseable | None" = None

    def __new__(cls) -> "_Unparseable":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __eq__(self, other: object) -> bool:  # never equal, so any comparison is a change
        return False

    def __hash__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "<unparseable>"


UNPARSEABLE = _Unparseable()


def portable_key(value: str) -> str:
    """NFC(casefold) identity, identical to the transaction engine's key."""

    return unicodedata.normalize("NFC", value.casefold())


def _fold_key(key: str) -> str:
    return unicodedata.normalize("NFKC", key).casefold()


def is_ontology_key(key: str | None) -> bool:
    """True when ``key`` names an ontology key, compared NFKC + casefold."""

    if key is None:
        return False
    folded = _fold_key(key)
    return folded in SCALAR_KEYS or folded in LIST_KEYS or folded.startswith(KEY_PREFIXES)


def _label(key: str) -> str:
    if len(key) > 48 or secret_like_class(key) is not None:
        return "<redacted key>"
    return key


# ---------------------------------------------------------------------------
# Secret-like detector (spec v1.1 section 5.1, Amendment 1.3)
# ---------------------------------------------------------------------------

_PEM_MARKER = "-" * 5 + "BEGIN"
_B = r"(?<![A-Za-z0-9])"
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem-block", re.compile(re.escape(_PEM_MARKER))),
    ("anthropic-key", re.compile(_B + r"sk-ant-")),
    ("openai-style-key", re.compile(_B + r"sk-[A-Za-z0-9_-]{16,}")),
    ("stripe-key", re.compile(_B + r"(?:sk|rk)_live_")),
    ("webhook-secret", re.compile(_B + r"whsec_")),
    ("github-token", re.compile(_B + r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]")),
    ("gitlab-token", re.compile(_B + r"glpat-")),
    ("slack-token", re.compile(_B + r"xox[baprs]-")),
    ("nvidia-key", re.compile(_B + r"nvapi-")),
    ("aws-key-id", re.compile(_B + r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("google-api-key", re.compile(_B + r"AIza[0-9A-Za-z_-]{30,}")),
    ("private-integration-token", re.compile(_B + r"pit-[A-Za-z0-9-]{20,}")),
    ("huggingface-token", re.compile(_B + r"hf_[A-Za-z0-9]{20,}")),
    ("npm-token", re.compile(_B + r"npm_[A-Za-z0-9]{20,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("bearer-token", re.compile(r"(?i)\bBearer\s+\S{16,}")),
    ("url-userinfo", re.compile(r"://[^/\s:@]+:[^/\s@]+@")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|client[_-]?secret"
            r"|private[_-]?key)\s*[:=]\s*\S+"
        ),
    ),
)
_URL_RE = re.compile(r"(?i)\bhttps?://[^\s<>\"']+")
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?:api[_-]?key|key|token|access[_-]?token|secret|password|sig|signature|auth)"
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX_RUN_RE = re.compile(r"[0-9a-fA-F]{32,}")
_WORD_SEG_RE = re.compile(
    r"[A-Z]?[a-z]+|[A-Z]{2,8}[a-z]*|\d{1,8}|(?:[A-Z][a-z]{2,}|[A-Z]{2,6}){2,3}"
)


def _entropy_class(text: str) -> str | None:
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        if _HEX_RUN_RE.search(token):
            return "hex-run"
        segs = re.split(r"[-_/]", token)
        if len(segs) >= 2 and all(_WORD_SEG_RE.fullmatch(s) for s in segs if s):
            continue
        classes = sum(
            (
                any(ch.isupper() for ch in token),
                any(ch.islower() for ch in token),
                any(ch.isdigit() for ch in token),
            )
        )
        if classes >= 2:
            return "high-entropy-token"
    return None


def secret_like_class(value: str, *, allow_raw_hash: bool = False) -> str | None:
    """Return the matched secret pattern class, or ``None``. Never the value."""

    if not isinstance(value, str) or not value:
        return None
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(value):
            return name
    if "secret-ref:" in value:
        return "secret-ref-outside-fact"
    text = value
    if allow_raw_hash:
        text = re.sub(r"(?<![A-Za-z0-9])raw:sha256:[0-9a-f]{64}(?![A-Za-z0-9])", " ", text)
    remainder_parts: list[str] = []
    last = 0
    for match in _URL_RE.finditer(text):
        remainder_parts.append(text[last : match.start()])
        last = match.end()
        try:
            parts = urlsplit(match.group(0))
            query = parse_qsl(parts.query, keep_blank_values=False)
        except ValueError:
            return "malformed-url"
        for name, item in query:
            if item and _SENSITIVE_QUERY_RE.fullmatch(name):
                return "url-credential"
            found = _entropy_class(item)
            if found is not None:
                return found
    remainder_parts.append(text[last:])
    return _entropy_class(" ".join(remainder_parts))


def fact_secret_like_class(value: str) -> str | None:
    """Secret check for ``fact.*`` values: an exact ``secret-ref:`` pointer is allowed."""

    if value.startswith("secret-ref:"):
        return None if _SECRET_REF_RE.fullmatch(value) else "malformed-secret-ref"
    return secret_like_class(value)


# ---------------------------------------------------------------------------
# Strict frontmatter splitter (Amendment 3, Rule 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    bom: bool
    nl: bytes | None
    lines: tuple[bytes, ...]
    body: bytes
    frontmatter_bytes: int = 0


def split_frontmatter_strict(data: bytes, *, side: str, path: str) -> Split:
    """Split ``data`` into BOM, frontmatter lines and body without any guessing."""

    bom = data.startswith(BOM)
    rest = data[len(BOM) :] if bom else data
    if rest.startswith(BOM):
        raise OntologyRuleError(
            "ONTOLOGY_BOM_MISMATCH", f"{path}: {side} file has more than one byte order mark"
        )
    if rest.startswith(b"---\r\n"):
        nl = b"\r\n"
    elif rest.startswith(b"---\n"):
        nl = b"\n"
    else:
        return Split(bom, None, (), rest)
    position = 3 + len(nl)
    lines: list[bytes] = []
    while True:
        end = rest.find(b"\n", position)
        if end == -1:
            tail = rest[position:]
            if side == "old" and tail == b"---":
                return Split(bom, nl, tuple(lines), b"", len(rest))
            if side == "old":
                # An unterminated opening fence is body text (a thematic break).
                return Split(bom, None, (), rest)
            raise OntologyRuleError(
                "ONTOLOGY_FRONTMATTER_INVALID",
                f"{path}: new frontmatter has no closing --- line ending in a newline",
            )
        segment = rest[position : end + 1]
        if nl == b"\r\n":
            if not segment.endswith(b"\r\n"):
                raise OntologyRuleError(
                    "ONTOLOGY_LINE_ENDING", f"{path}: {side} frontmatter mixes LF and CRLF"
                )
            content = segment[:-2]
        else:
            content = segment[:-1]
            if content.endswith(b"\r"):
                raise OntologyRuleError(
                    "ONTOLOGY_LINE_ENDING", f"{path}: {side} frontmatter mixes LF and CRLF"
                )
        if content == b"---":
            return Split(bom, nl, tuple(lines), rest[end + 1 :], end + 1)
        lines.append(content)
        position = end + 1


def _first_newline(data: bytes) -> bytes:
    index = data.find(b"\n")
    if index > 0 and data[index - 1 : index] == b"\r":
        return b"\r\n"
    return b"\n"


def _check_line_characters(text: str, *, path: str) -> None:
    for character in text:
        code = ord(character)
        if (
            (code < 0x20 and character != "\t")
            or 0x7F <= code <= 0x9F
            or character in "  ﻿"
        ):
            raise OntologyRuleError(
                "ONTOLOGY_LINE_ENDING",
                f"{path}: new frontmatter contains a bare CR, Unicode line separator "
                "or control character",
            )


# ---------------------------------------------------------------------------
# Ordered top-level blocks (Amendment 3, Rules 4 and 5)
# ---------------------------------------------------------------------------


@dataclass
class Block:
    key: str | None
    lines: list[str]
    block_scalar: bool = False
    list_capable: bool = False


def parse_blocks(lines: Sequence[str], *, side: str, path: str) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None

    def unsupported(detail: str) -> OntologyRuleError:
        return OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {side} frontmatter {detail}"
        )

    def next_is_indented(start: int) -> bool:
        for later in lines[start + 1 :]:
            if later.strip():
                return later[:1] in (" ", "\t")
        return False

    for index, line in enumerate(lines):
        if not line.strip():
            if current is not None and current.block_scalar and next_is_indented(index):
                current.lines.append(line)
            else:
                blocks.append(Block(None, [line]))
                current = None
            continue
        if line[:1] in (" ", "\t"):
            if current is None:
                raise unsupported("has an indented line outside any key")
            if current.block_scalar:
                current.lines.append(line)
            elif current.list_capable and _CONTINUATION_ITEM_RE.fullmatch(line):
                current.lines.append(line)
            else:
                raise unsupported(
                    f"has nested or multi-line content under {_label(current.key or '')}"
                )
            continue
        if line.startswith("#"):
            blocks.append(Block(None, [line]))
            current = None
            continue
        if line == "-" or line.startswith("- "):
            if current is not None and current.list_capable and not current.block_scalar:
                current.lines.append(line)
                continue
            raise unsupported("has a list item outside a list key")
        match = _KEY_LINE_RE.fullmatch(line)
        if match is None:
            raise unsupported("has a column-0 construct that is not a plain ASCII key")
        value = (match.group(2) or "").strip()
        current = Block(
            match.group(1),
            [line],
            block_scalar=bool(_BLOCK_SCALAR_RE.fullmatch(value)),
            list_capable=value == "",
        )
        blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# Scalar and list value grammar (Amendment 1.2)
# ---------------------------------------------------------------------------

_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "r": "\r", "t": "\t"}
_HEX_ESCAPES = {"x": 2, "u": 4, "U": 8}


def _decode_double(raw: str, *, path: str, key: str) -> tuple[str, int]:
    """Decode a double-quoted scalar starting at raw[0]; return (value, end)."""

    out: list[str] = []
    index = 1
    while index < len(raw):
        character = raw[index]
        if character == '"':
            return "".join(out), index + 1
        if character == "\\":
            if index + 1 >= len(raw):
                break
            escape = raw[index + 1]
            if escape in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[escape])
                index += 2
                continue
            width = _HEX_ESCAPES.get(escape)
            digits = raw[index + 2 : index + 2 + width] if width else ""
            if not width or len(digits) != width or not re.fullmatch(r"[0-9A-Fa-f]+", digits):
                raise OntologyRuleError(
                    "ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} has an unsupported escape"
                )
            code = int(digits, 16)
            if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                raise OntologyRuleError(
                    "ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} has an invalid code point"
                )
            out.append(chr(code))
            index += 2 + width
            continue
        out.append(character)
        index += 1
    raise OntologyRuleError(
        "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has an unterminated quoted value"
    )


def _decode_single(raw: str, *, path: str, key: str) -> tuple[str, int]:
    out: list[str] = []
    index = 1
    while index < len(raw):
        character = raw[index]
        if character == "'":
            if raw[index + 1 : index + 2] == "'":
                out.append("'")
                index += 2
                continue
            return "".join(out), index + 1
        out.append(character)
        index += 1
    raise OntologyRuleError(
        "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has an unterminated quoted value"
    )


def _check_decoded(value: str, *, path: str, key: str) -> str:
    for character in value:
        if unicodedata.category(character) in ("Cc", "Cs", "Zl", "Zp") or character == "﻿":
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE",
                f"{path}: {_label(key)} decodes to a control or line-separator character",
            )
    return value


def parse_scalar(raw: str, *, path: str, key: str, in_flow: bool = False) -> str:
    """Parse one YAML scalar (plain, single- or double-quoted) on a single line."""

    if not raw:
        raise OntologyRuleError("ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} is empty")
    if raw[0] in "\"'":
        decoder = _decode_double if raw[0] == '"' else _decode_single
        value, end = decoder(raw, path=path, key=key)
        if raw[end:].strip():
            raise OntologyRuleError(
                "ONTOLOGY_UNSUPPORTED_YAML",
                f"{path}: {_label(key)} has text or a comment after its quoted value",
            )
        return _check_decoded(value, path=path, key=key)
    if raw[0] in "[]{}&*!|>%@`#," or raw[:2] in ("- ", "? ", ": ") or raw in ("-", "?"):
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML",
            f"{path}: {_label(key)} starts with a YAML indicator; quote the value",
        )
    if " #" in raw or "\t#" in raw or ": " in raw or raw.endswith(":"):
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML",
            f"{path}: {_label(key)} has a comment or mapping marker; quote the value",
        )
    if in_flow and any(character in raw for character in "[]{}"):
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has a nested flow collection"
        )
    if raw in _YAML_NULLS:
        raise OntologyRuleError(
            "ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} is a YAML null; quote or remove it"
        )
    return _check_decoded(raw, path=path, key=key)


def _split_flow(inner: str, *, path: str, key: str) -> list[str]:
    items: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    while index < len(inner):
        character = inner[index]
        if quote == '"':
            if character == "\\":
                index += 2
                continue
            if character == '"':
                quote = None
        elif quote == "'":
            if character == "'":
                if inner[index + 1 : index + 2] == "'":
                    index += 2
                    continue
                quote = None
        elif character in "\"'":
            quote = character
        elif character == ",":
            items.append(inner[start:index])
            start = index + 1
        index += 1
    if quote is not None:
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has an unterminated quoted item"
        )
    items.append(inner[start:])
    return items


def parse_inline_list(raw: str, *, path: str, key: str) -> list[str]:
    if not (raw.startswith("[") and raw.endswith("]")):
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has a malformed inline list"
        )
    inner = raw[1:-1]
    if not inner.strip():
        return []
    values = []
    for item in _split_flow(inner, path=path, key=key):
        stripped = item.strip()
        if not stripped:
            raise OntologyRuleError(
                "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has an empty list item"
            )
        values.append(parse_scalar(stripped, path=path, key=key, in_flow=True))
    return values


def parse_ontology_block(block: Block, *, path: str) -> str | list[str]:
    """Strictly parse one ontology block (single-line scalar or flat list)."""

    key = block.key or ""
    folded = _fold_key(key)
    for line in block.lines:
        if line != line.rstrip(" \t"):
            raise OntologyRuleError(
                "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} has trailing whitespace"
            )
    match = _KEY_LINE_RE.fullmatch(block.lines[0])
    assert match is not None
    raw = (match.group(2) or "").strip()
    items = block.lines[1:]
    if block.block_scalar:
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML",
            f"{path}: {_label(key)} uses a block scalar; ontology values are single-line",
        )
    if folded in LIST_KEYS:
        if raw == "":
            values = []
            for line in items:
                item = _STRICT_ITEM_RE.fullmatch(line)
                if item is None:
                    raise OntologyRuleError(
                        "ONTOLOGY_UNSUPPORTED_YAML",
                        f"{path}: {_label(key)} has a list item that is not '- value'",
                    )
                values.append(parse_scalar(item.group(2), path=path, key=key))
            return values
        if items:
            raise OntologyRuleError(
                "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} mixes inline and block items"
            )
        if raw.startswith("["):
            return parse_inline_list(raw, path=path, key=key)
        if folded == "alias":
            return [parse_scalar(raw, path=path, key=key)]
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} must be a list"
        )
    if items:
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML", f"{path}: {_label(key)} must be a single-line scalar"
        )
    if raw.startswith("["):
        raise OntologyRuleError(
            "ONTOLOGY_UNSUPPORTED_YAML",
            f"{path}: {_label(key)} is an unquoted inline list; quote a literal value",
        )
    return parse_scalar(raw, path=path, key=key)


# ---------------------------------------------------------------------------
# Note model
# ---------------------------------------------------------------------------


@dataclass
class Note:
    path: str
    split: Split
    blocks: list[Block] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def has_frontmatter(self) -> bool:
        return self.split.nl is not None

    def protected_blocks(self) -> list[tuple[str, ...]]:
        return [tuple(block.lines) for block in self.blocks if not is_ontology_key(block.key)]

    def get(self, key: str) -> Any:
        return self.values.get(key)

    def list_value(self, key: str) -> list[str] | _Unparseable:
        value = self.values.get(key)
        if value is None:
            return []
        if value is UNPARSEABLE or not isinstance(value, list):
            return UNPARSEABLE
        return value


def parse_note(data: bytes, *, side: str, path: str) -> Note:
    split = split_frontmatter_strict(data, side=side, path=path)
    note = Note(path=path, split=split)
    if split.nl is None:
        return note
    if side == "new" and split.frontmatter_bytes > MAX_FRONTMATTER_BYTES:
        raise OntologyRuleError(
            "ONTOLOGY_SHAPE_LIMIT", f"{path}: frontmatter exceeds {MAX_FRONTMATTER_BYTES} bytes"
        )
    texts: list[str] = []
    for line in split.lines:
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OntologyRuleError(
                "ONTOLOGY_ENCODING", f"{path}: {side} frontmatter is not UTF-8"
            ) from exc
        if side == "new":
            _check_line_characters(text, path=path)
        texts.append(text)
    note.blocks = parse_blocks(texts, side=side, path=path)
    seen: set[str] = set()
    duplicated: set[str] = set()
    for block in note.blocks:
        if block.key is None:
            continue
        folded = _fold_key(block.key)
        if side == "new":
            if is_ontology_key(block.key) and block.key != folded:
                raise OntologyRuleError(
                    "ONTOLOGY_KEY_NOT_CANONICAL",
                    f"{path}: ontology key {_label(block.key)} must be lowercase ASCII",
                )
            if folded == "alias":
                raise OntologyRuleError(
                    "ONTOLOGY_KEY_NOT_CANONICAL",
                    f"{path}: legacy alias must be written as aliases",
                )
            if folded in seen or (folded == "aliases" and "alias" in seen):
                raise OntologyRuleError(
                    "ONTOLOGY_DUPLICATE_KEY", f"{path}: duplicate key {_label(block.key)}"
                )
        elif folded in seen:
            duplicated.add(folded)
        seen.add(folded)
        if not is_ontology_key(block.key):
            continue
        if side == "new":
            note.values[folded] = parse_ontology_block(block, path=path)
        else:
            try:
                note.values[folded] = parse_ontology_block(block, path=path)
            except OntologyRuleError:
                note.values[folded] = UNPARSEABLE
    for folded in duplicated:
        if folded in note.values:
            note.values[folded] = UNPARSEABLE
    if side == "old" and "alias" in note.values:
        legacy = note.values.pop("alias")
        if "aliases" not in note.values:
            note.values["aliases"] = legacy
    return note


def title_value(note: Note) -> str | None:
    for block in note.blocks:
        if block.key is not None and _fold_key(block.key) == "title":
            match = _KEY_LINE_RE.fullmatch(block.lines[0])
            raw = ((match.group(2) if match else "") or "").strip()
            extra = " ".join(line.strip() for line in block.lines[1:])
            try:
                return parse_scalar(raw, path=note.path, key="title") + (
                    " " + extra if extra else ""
                )
            except OntologyRuleError:
                return (raw + " " + extra).strip()
    return None


# ---------------------------------------------------------------------------
# Per-note invariants
# ---------------------------------------------------------------------------


def check_note_invariants(old: Note, new: Note) -> None:
    """Rule 3 (BOM, newline, body bytes) and Rules 4/5 (ordered block identity)."""

    path = new.path
    if not new.has_frontmatter:
        raise OntologyRuleError(
            "ONTOLOGY_FRONTMATTER_INVALID",
            f"{path}: ontology writes must leave a frontmatter block at byte 0",
        )
    if old.split.bom != new.split.bom:
        raise OntologyRuleError(
            "ONTOLOGY_BOM_MISMATCH", f"{path}: byte order mark presence must not change"
        )
    if old.has_frontmatter:
        expected_nl = old.split.nl
    else:
        expected_nl = _first_newline(old.split.body)
    if new.split.nl != expected_nl:
        raise OntologyRuleError(
            "ONTOLOGY_LINE_ENDING",
            f"{path}: frontmatter newline style must match the existing note",
        )
    if new.split.body != old.split.body:
        raise OntologyRuleError(
            "ONTOLOGY_BODY_CHANGED", f"{path}: bytes after the frontmatter must not change"
        )
    if old.protected_blocks() != new.protected_blocks():
        raise OntologyRuleError(
            "ONTOLOGY_PROTECTED_KEYS_CHANGED",
            f"{path}: only ontology keys may change; other frontmatter must stay "
            "byte-identical and in order",
        )


def _check_date(value: str, *, path: str, key: str) -> None:
    if not _DATE_RE.fullmatch(value):
        raise OntologyRuleError("ONTOLOGY_INVALID_VALUE", f"{path}: {key} must be YYYY-MM-DD")
    try:
        _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise OntologyRuleError(
            "ONTOLOGY_INVALID_VALUE", f"{path}: {key} is not a calendar date"
        ) from exc


def _secret_error(path: str, key: str, kind: str) -> OntologyRuleError:
    return OntologyRuleError(
        "ONTOLOGY_SECRET_LIKE_VALUE",
        f"{path}: {_label(key)} holds a secret-like value ({kind}); store a "
        "secret-ref:<file>.env#<VAR> pointer in a fact.* key instead",
    )


def validate_new_values(note: Note) -> None:
    """Rule 5 shape and the 5.1 value grammar on the proposed note."""

    path = note.path
    values = note.values
    if "entity_id" in values and "about" in values:
        raise OntologyRuleError(
            "ONTOLOGY_ENTITY_AND_ABOUT", f"{path}: entity_id and about are mutually exclusive"
        )
    for key in ("entity_id", "about"):
        if key in values and not _ENTITY_RE.fullmatch(values[key]):
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: {key} must match [a-z][a-z0-9-]{{1,47}}"
            )
    if "entity_type" in values and not _ENTITY_TYPE_RE.fullmatch(values["entity_type"]):
        raise OntologyRuleError(
            "ONTOLOGY_INVALID_VALUE", f"{path}: entity_type must match [a-z][a-z0-9-]{{0,47}}"
        )
    for key in DATE_KEYS:
        if key in values:
            _check_date(values[key], path=path, key=key)
    for alias in values.get("aliases", []):
        normalized = " ".join(unicodedata.normalize("NFKC", alias).casefold().split())
        if not 1 <= len(alias) <= MAX_ALIAS_CHARS or not normalized:
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: each alias must be 1-{MAX_ALIAS_CHARS} characters"
            )
        kind = secret_like_class(alias)
        if kind is not None:
            raise _secret_error(path, "aliases", kind)
    facts = {key: value for key, value in values.items() if key.startswith("fact.")}
    if len(facts) > MAX_FACTS:
        raise OntologyRuleError(
            "ONTOLOGY_SHAPE_LIMIT", f"{path}: at most {MAX_FACTS} fact.* keys per note"
        )
    if facts and "entity_id" not in values and "about" not in values:
        raise OntologyRuleError(
            "ONTOLOGY_FACT_WITHOUT_ENTITY", f"{path}: fact.* keys need entity_id or about"
        )
    for key in values:
        kind = secret_like_class(key)
        if kind is not None:
            raise _secret_error(path, key, kind)
    for key, value in facts.items():
        if not _FIELD_RE.fullmatch(key[len("fact.") :]):
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} has an invalid field name"
            )
        if len(value) > MAX_FACT_CHARS:
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: {key} exceeds {MAX_FACT_CHARS} characters"
            )
        kind = fact_secret_like_class(value)
        if kind is not None:
            raise _secret_error(path, key, kind)
    for key, value in values.items():
        if not key.startswith("evidence."):
            continue
        if not _FIELD_RE.fullmatch(key[len("evidence.") :]):
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: {_label(key)} has an invalid field name"
            )
        if not (
            _RAW_REF_RE.fullmatch(value)
            or _BANK_REF_RE.fullmatch(value)
            or _NOTE_REF_RE.fullmatch(value)
        ):
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE",
                f"{path}: {key} must be raw:sha256:<hex>, bank:<workspace>/<id> "
                "or note:[[Target]]#<heading>",
            )
    if "fact_source" in values:
        source = values["fact_source"]
        if len(source) > MAX_FACT_SOURCE_CHARS:
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE",
                f"{path}: fact_source exceeds {MAX_FACT_SOURCE_CHARS} characters",
            )
        kind = secret_like_class(source, allow_raw_hash=True)
        if kind is not None:
            raise _secret_error(path, "fact_source", kind)
    title = title_value(note)
    if title is not None:
        if len(title) > MAX_TITLE_CHARS:
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: title exceeds {MAX_TITLE_CHARS} characters"
            )
        kind = secret_like_class(title)
        if kind is not None:
            raise _secret_error(path, "title", kind)
    for list_key in ("certified_owner", "certified_evidence"):
        members = values.get(list_key, [])
        if len(set(members)) != len(members):
            raise OntologyRuleError(
                "ONTOLOGY_INVALID_VALUE", f"{path}: {list_key} lists a key twice"
            )
        for member in members:
            if not member.startswith("fact.") or not _FIELD_RE.fullmatch(member[5:]):
                raise OntologyRuleError(
                    "ONTOLOGY_INVALID_VALUE", f"{path}: {list_key} may list only fact.<field> keys"
                )
            if not facts.get(member):
                raise OntologyRuleError(
                    "ONTOLOGY_CERTIFIED_WITHOUT_VALUE",
                    f"{path}: {list_key} names {member}, which has no value",
                )
            if list_key == "certified_evidence" and f"evidence.{member[5:]}" not in values:
                raise OntologyRuleError(
                    "ONTOLOGY_EVIDENCE_MISSING",
                    f"{path}: {member} is evidence-certified without evidence.{member[5:]}",
                )


def owner_certification_reasons(old: Note, new: Note) -> list[str]:
    """Amendment 3 Rule 6: every way a bundle can touch owner certification."""

    reasons: list[str] = []
    old_members = old.list_value("certified_owner")
    new_members = new.list_value("certified_owner")
    old_set = None if old_members is UNPARSEABLE else set(old_members)
    new_set = set(new_members) if isinstance(new_members, list) else None
    if old_set is None or new_set != old_set:
        reasons.append("certified_owner membership changes")
    for key in sorted(new_set or ()):
        if new.get(key) != old.get(key):
            reasons.append(f"{key} changes while owner-certified")
    if new_set:
        for key in ("entity_id", "about"):
            if new.get(key) != old.get(key):
                reasons.append(f"{key} changes while facts are owner-certified")
        old_review = old.get("review_after")
        new_review = new.get("review_after")
        if new_review != old_review and (
            old_review is UNPARSEABLE
            or (new_review is None and old_review is not None)
            or (old_review is not None and new_review is not None and new_review > old_review)
        ):
            reasons.append("review_after is removed or extended while facts are owner-certified")
    return reasons


# ---------------------------------------------------------------------------
# Scope (spec 5.2 roots, Amendment 2 casefolded exclusions, Rule 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    profile: str
    roots: tuple[str, ...]
    home_managed: frozenset[str]
    excluded_prefixes: tuple[str, ...] = ()


def check_target_lexical(relative: str, write_mode: Any) -> None:
    """Rule 1 checks that need no vault access (run per write and in recovery)."""

    if write_mode != "replace":
        raise OntologyRuleError(
            "ONTOLOGY_WRITE_MODE",
            f"ontology bundles may only replace existing notes: {relative}",
        )
    folded = portable_key(relative)
    parts = PurePosixPath(folded).parts
    if not folded.endswith(".md") or not parts:
        raise OntologyRuleError(
            "ONTOLOGY_TARGET_SCOPE", f"ontology bundles may only write Markdown notes: {relative}"
        )
    if (
        parts[0] in EXCLUDED_TOP_LEVEL
        or any(part in EXCLUDED_ANY_LEVEL for part in parts)
        or (len(parts) == 1 and parts[0] in ROOT_INSTRUCTION_FILES)
    ):
        raise OntologyRuleError(
            "ONTOLOGY_TARGET_SCOPE",
            f"ontology bundles may not write instruction files or reserved directories: {relative}",
        )


def _normalize_relative(value: Any, *, label: str) -> str:
    from .transaction import TransactionValidationError, _normalize_vault_path

    try:
        return _normalize_vault_path(value)
    except TransactionValidationError as exc:
        raise OntologyRuleError(
            "ONTOLOGY_CONFIG_INVALID", f"{label} is not a safe vault-relative path"
        ) from exc


def _qualified_pins() -> list[dict[str, Any]]:
    from .home_adapter import PIN

    pins = []
    for candidate in [PIN, *sorted(PIN.parent.glob("home-compatibility-v*.json"))]:
        try:
            pin = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(pin, dict):
            pins.append(pin)
    return pins


def resolve_scope(read: Callable[[str], bytes | None]) -> Scope:
    """Resolve roots per spec 5.2: configured, then shared-root, then standalone."""

    from .transaction import _strict_json_loads

    roots: tuple[str, ...] | None = None
    excluded: list[str] = []
    profile = "standalone"
    raw_config = read(WORKSPACE_CONFIG)
    if raw_config is not None:
        try:
            config = _strict_json_loads(raw_config.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OntologyRuleError(
                "ONTOLOGY_CONFIG_INVALID", f"{WORKSPACE_CONFIG} is not valid JSON"
            ) from exc
        if not isinstance(config, dict):
            raise OntologyRuleError("ONTOLOGY_CONFIG_INVALID", f"{WORKSPACE_CONFIG} must be an object")
        ontology = config.get("ontology")
        if ontology is not None:
            if not isinstance(ontology, dict):
                raise OntologyRuleError(
                    "ONTOLOGY_CONFIG_INVALID", f"{WORKSPACE_CONFIG} ontology must be an object"
                )
            configured = ontology.get("roots")
            if configured is not None:
                if (
                    not isinstance(configured, list)
                    or not configured
                    or not all(isinstance(item, str) and item.strip("/") for item in configured)
                ):
                    raise OntologyRuleError(
                        "ONTOLOGY_CONFIG_INVALID",
                        f"{WORKSPACE_CONFIG} ontology.roots must be a non-empty list of paths",
                    )
                roots = tuple(
                    _normalize_relative(item.rstrip("/"), label="ontology.roots entry") + "/"
                    for item in configured
                )
                profile = "configured"
            skip = ontology.get("exclude", [])
            if not isinstance(skip, list) or not all(isinstance(item, str) for item in skip):
                raise OntologyRuleError(
                    "ONTOLOGY_CONFIG_INVALID",
                    f"{WORKSPACE_CONFIG} ontology.exclude must be a list of paths",
                )
            excluded = [
                _normalize_relative(item.rstrip("/"), label="ontology.exclude entry")
                for item in skip
            ]
    home_managed: set[str] = set()
    pinned_install = False
    raw_state = read(HOME_STATE_PATH)
    if raw_state is not None:
        try:
            state = _strict_json_loads(raw_state.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OntologyRuleError(
                "ONTOLOGY_CONFIG_INVALID", "the Home installation record is not valid JSON"
            ) from exc
        files = state.get("files") if isinstance(state, dict) else None
        if not isinstance(files, dict):
            raise OntologyRuleError(
                "ONTOLOGY_CONFIG_INVALID", "the Home installation record has no file list"
            )
        home_managed.update(portable_key(str(path)) for path in files)
        commit = state.get("commit")
        for pin in _qualified_pins():
            if isinstance(commit, str) and pin.get("commit") == commit:
                pinned_install = True
                if isinstance(pin.get("files"), dict):
                    home_managed.update(portable_key(str(path)) for path in pin["files"])
        if roots is None and pinned_install and state.get("status") == "installed":
            roots = SHARED_ROOT_PROFILE_ROOTS
            profile = "shared-root"
    if roots is None:
        roots = STANDALONE_PROFILE_ROOTS
    return Scope(profile, roots, frozenset(home_managed), tuple(excluded))


def check_target_in_scope(relative: str, scope: Scope) -> None:
    folded = portable_key(relative)
    if not any(folded.startswith(portable_key(root)) for root in scope.roots):
        raise OntologyRuleError(
            "ONTOLOGY_TARGET_SCOPE",
            f"target is outside the {scope.profile} ontology roots: {relative}",
        )
    if folded in scope.home_managed:
        raise OntologyRuleError(
            "ONTOLOGY_TARGET_SCOPE", f"target is a Home-managed file: {relative}"
        )
    for prefix in scope.excluded_prefixes:
        key = portable_key(prefix)
        if folded == key or folded.startswith(key + "/"):
            raise OntologyRuleError(
                "ONTOLOGY_TARGET_SCOPE", f"target is under an excluded ontology path: {relative}"
            )


# ---------------------------------------------------------------------------
# Certification and evidence (Amendment 3, Rules 6 and 7)
# ---------------------------------------------------------------------------


def _pinned(preconditions: Mapping[str, str | None], relative: str) -> str | None:
    folded = portable_key(relative)
    for path, digest in preconditions.items():
        if portable_key(path) == folded:
            return digest
    return None


def check_owner_certification(
    bundle: Mapping[str, Any],
    pairs: Sequence[tuple[Note, Note]],
    *,
    preconditions: Mapping[str, str | None],
    read: Callable[[str], bytes | None],
) -> dict[str, Any] | None:
    from .transaction import sha256_bytes

    required = {
        new.path: reasons
        for old, new in pairs
        if (reasons := owner_certification_reasons(old, new))
    }
    certification = bundle.get("certification")
    if certification is None:
        if required:
            first = sorted(required)[0]
            raise OntologyRuleError(
                "ONTOLOGY_OWNER_CERTIFICATION_REQUIRED",
                f"{first}: {required[first][0]}; the bundle needs "
                '"certification": {"class": "owner", "digest": "<reviewed digest .md>"}',
            )
        return None
    if (
        not isinstance(certification, dict)
        or set(certification) != {"class", "digest"}
        or certification.get("class") != "owner"
        or not isinstance(certification.get("digest"), str)
    ):
        raise OntologyRuleError(
            "ONTOLOGY_CERTIFICATION_INVALID",
            'certification must be exactly {"class": "owner", "digest": "<path>.md"}',
        )
    digest = _normalize_relative(certification["digest"], label="certification digest")
    folded = portable_key(digest)
    parts = PurePosixPath(folded).parts
    targets = {portable_key(new.path) for _, new in pairs}
    if (
        not folded.endswith(".md")
        or folded in targets
        or parts[0] in EXCLUDED_TOP_LEVEL
        or (len(parts) == 1 and parts[0] in ROOT_INSTRUCTION_FILES)
    ):
        raise OntologyRuleError(
            "ONTOLOGY_CERTIFICATION_INVALID",
            "the certification digest must be a separate in-vault Markdown note, "
            "not a bundle target or an instruction file",
        )
    content = read(digest)
    if content is None:
        raise OntologyRuleError(
            "ONTOLOGY_CERTIFICATION_INVALID", f"certification digest does not exist: {digest}"
        )
    pinned = _pinned(preconditions, digest)
    if pinned is None or pinned != sha256_bytes(content):
        raise OntologyRuleError(
            "ONTOLOGY_CERTIFICATION_NOT_PINNED",
            f"certification digest must be pinned with its SHA-256 in read_preconditions: {digest}",
        )
    return {"class": "owner", "digest": digest}


def _find_note_candidates(vault_root: Path, target: str) -> list[str]:
    wanted = portable_key(target)
    found: list[str] = []
    count = 0
    for directory, dirnames, filenames in os.walk(vault_root, followlinks=False):
        base = Path(directory)
        keep = []
        for name in dirnames:
            child = base / name
            is_junction = getattr(child, "is_junction", lambda: False)()
            if name.startswith(".") or name == "node_modules" or child.is_symlink() or is_junction:
                continue
            keep.append(name)
        dirnames[:] = sorted(keep)
        for name in filenames:
            count += 1
            if count > MAX_NOTE_SEARCH_ENTRIES:
                raise OntologyRuleError(
                    "ONTOLOGY_EVIDENCE_UNVERIFIED", "vault too large to resolve a note: reference"
                )
            if name.lower().endswith(".md") and portable_key(name[:-3]) == wanted:
                found.append((base / name).relative_to(vault_root).as_posix())
    return found


def _headings(data: bytes) -> set[str]:
    from .lint_engine import (
        _ATX_HEADING_RE,
        _mask_markdown_code,
        _normalize_heading,
        _parse_frontmatter,
    )

    text = data.decode("utf-8-sig")
    masked = _mask_markdown_code(text, _parse_frontmatter(text).end_offset)
    return {
        normalized
        for match in _ATX_HEADING_RE.finditer(masked)
        if (normalized := _normalize_heading(match.group(2)))
    }


def verify_certified_evidence(
    note: Note,
    *,
    vault_root: Path,
    scope: Scope,
    targets: frozenset[str],
    preconditions: Mapping[str, str | None],
    read: Callable[[str], bytes | None],
) -> None:
    """Rule 7: verify every evidence-certified key, not only added ones."""

    from .transaction import sha256_bytes

    path = note.path
    for member in note.values.get("certified_evidence", []):
        field_name = member[len("fact.") :]
        ref = note.values[f"evidence.{field_name}"]

        def fail(detail: str) -> OntologyRuleError:
            return OntologyRuleError(
                "ONTOLOGY_EVIDENCE_UNVERIFIED", f"{path}: evidence.{field_name} {detail}"
            )

        raw = _RAW_REF_RE.fullmatch(ref)
        if raw is not None:
            if not any(
                portable_key(pinned).startswith(".raw/") and digest == raw.group(1)
                for pinned, digest in preconditions.items()
            ):
                raise fail("needs the .raw/ object with that SHA-256 pinned in read_preconditions")
            continue
        bank = _BANK_REF_RE.fullmatch(ref)
        if bank is not None:
            workspace, evidence_id = bank.groups()
            package = f"{EVIDENCE_PACKAGE_DIR}/{evidence_id}"
            for member_name in EVIDENCE_PACKAGE_MEMBERS:
                if _pinned(preconditions, f"{package}/{member_name}") is None:
                    raise fail(f"needs {package}/{member_name} pinned in read_preconditions")
            from .research_evidence import EvidenceError, verify_package

            try:
                report = verify_package(vault_root / package, workspace=workspace)
            except (EvidenceError, OSError, ValueError) as exc:
                raise fail("does not verify as a research-evidence package") from exc
            if report.get("evidence_id") != evidence_id:
                raise fail("names a package whose identity differs")
            continue
        note_ref = _NOTE_REF_RE.fullmatch(ref)
        if note_ref is None:  # pragma: no cover - grammar already enforced
            raise fail("is not a supported evidence reference")
        target, anchor = note_ref.groups()
        target = target.strip()
        if "/" in target:
            candidate = target if target.lower().endswith(".md") else target + ".md"
            candidate = _normalize_relative(candidate, label="note: evidence target")
            candidates = [candidate] if read(candidate) is not None else []
        else:
            candidates = _find_note_candidates(vault_root, target)
        if len(candidates) != 1:
            raise fail("must resolve to exactly one note")
        resolved = candidates[0]
        folded = portable_key(resolved)
        if folded in targets:
            raise fail("may not cite a note changed by this bundle (self-certification)")
        try:
            check_target_lexical(resolved, "replace")
            check_target_in_scope(resolved, scope)
        except OntologyRuleError as exc:
            raise fail("must cite a note inside the ontology roots") from exc
        content = read(resolved)
        pinned = _pinned(preconditions, resolved)
        if content is None or pinned is None or pinned != sha256_bytes(content):
            raise fail(f"needs {resolved} pinned with its SHA-256 in read_preconditions")
        from .lint_engine import _normalize_heading

        if _normalize_heading(anchor) not in _headings(content):
            raise fail("cites a heading that does not exist")


# ---------------------------------------------------------------------------
# Bundle entry points
# ---------------------------------------------------------------------------


def validate_bundle(
    bundle: Mapping[str, Any],
    writes: Sequence[tuple[str, bytes | None, bytes]],
    *,
    vault_root: Path,
    read: Callable[[str], bytes | None],
    preconditions: Mapping[str, str | None],
) -> None:
    """Validate a complete ontology bundle; raises :class:`OntologyRuleError`."""

    if len(writes) > MAX_BUNDLE_TARGETS:
        raise OntologyRuleError(
            "ONTOLOGY_BUNDLE_LIMIT", f"ontology bundles may have at most {MAX_BUNDLE_TARGETS} targets"
        )
    if sum(len(new) for _, _, new in writes) > MAX_BUNDLE_BYTES:
        raise OntologyRuleError(
            "ONTOLOGY_BUNDLE_LIMIT", f"ontology bundles may hold at most {MAX_BUNDLE_BYTES} bytes"
        )
    scope = resolve_scope(read)
    pairs: list[tuple[Note, Note]] = []
    for relative, old_bytes, new_bytes in writes:
        check_target_lexical(relative, "replace")
        check_target_in_scope(relative, scope)
        if old_bytes is None:
            raise OntologyRuleError(
                "ONTOLOGY_WRITE_MODE", f"ontology bundles may only replace existing notes: {relative}"
            )
        old = parse_note(old_bytes, side="old", path=relative)
        new = parse_note(new_bytes, side="new", path=relative)
        check_note_invariants(old, new)
        validate_new_values(new)
        pairs.append((old, new))
    check_owner_certification(bundle, pairs, preconditions=preconditions, read=read)
    targets = frozenset(portable_key(new.path) for _, new in pairs)
    for _, new in pairs:
        verify_certified_evidence(
            new,
            vault_root=vault_root,
            scope=scope,
            targets=targets,
            preconditions=preconditions,
            read=read,
        )


def plan_summary(bundle: Mapping[str, Any], writes: Iterable[tuple[str, bytes]]) -> dict[str, Any]:
    """Inspect output: the approval label plus every certified key and value."""

    certification = bundle.get("certification")
    owner = isinstance(certification, dict) and certification.get("class") == "owner"
    owner_keys: list[dict[str, str]] = []
    evidence_keys: list[dict[str, str]] = []
    for relative, content in writes:
        note = parse_note(content, side="new", path=relative)
        for member in note.values.get("certified_owner", []):
            owner_keys.append({"path": relative, "key": member, "value": note.values[member]})
        for member in note.values.get("certified_evidence", []):
            evidence_keys.append(
                {
                    "path": relative,
                    "key": member,
                    "value": note.values[member],
                    "evidence": note.values[f"evidence.{member[5:]}"],
                }
            )
    return {
        "approval": "OWNER APPROVAL REQUIRED" if owner else "standard",
        "certification": dict(certification) if owner else None,
        "certified_owner": owner_keys,
        "certified_evidence": evidence_keys,
    }
