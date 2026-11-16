"""Ontology engine for legends-empire.

Standard library only. Provides index, resolve, need, ask, propose, certify, and bench.
Conforms to Spec v1.1 (Sections 5.1-5.7 + Carbon Pre-Audit and Post-Diff amendments).
Unifies with claude_empire.ontology_grammar for shared grammar, parser, and secret detection.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
from typing import Any
import unicodedata

try:
    from .ontology_grammar import (
        BOM,
        EXCLUDED_TOP_LEVEL,
        EXCLUDED_ANY_LEVEL,
        OntologyRuleError,
        ROOT_INSTRUCTION_FILES,
        Scope,
        WORKSPACE_CONFIG,
        EVIDENCE_PACKAGE_DIR,
        EVIDENCE_PACKAGE_MEMBERS,
        _first_newline,
        _normalize_relative,
        check_note_invariants,
        fact_secret_like_class,
        is_ontology_key,
        owner_certification_reasons,
        portable_key,
        resolve_scope,
        secret_like_class,
        split_frontmatter_strict as og_split_frontmatter_strict,
        parse_note,
        title_value,
        validate_new_values,
        _label,
        _headings,
        UNPARSEABLE,
        Note,
        Block,
    )
except ImportError:
    from claude_empire.ontology_grammar import (
        BOM,
        EXCLUDED_TOP_LEVEL,
        EXCLUDED_ANY_LEVEL,
        OntologyRuleError,
        ROOT_INSTRUCTION_FILES,
        Scope,
        WORKSPACE_CONFIG,
        EVIDENCE_PACKAGE_DIR,
        EVIDENCE_PACKAGE_MEMBERS,
        _first_newline,
        _normalize_relative,
        check_note_invariants,
        fact_secret_like_class,
        is_ontology_key,
        owner_certification_reasons,
        portable_key,
        resolve_scope,
        secret_like_class,
        split_frontmatter_strict as og_split_frontmatter_strict,
        parse_note,
        title_value,
        validate_new_values,
        _label,
        _headings,
        UNPARSEABLE,
        Note,
        Block,
    )

# Schema identifiers
INDEX_SCHEMA = "claude-empire.ontology-index.v1"
TRANSACTION_SCHEMA = "claude-empire.transaction.v1"
OPERATION_TYPE = "ontology"

# Regex rules per Spec 5.1 & Spec v1.1
ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
FIELD_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SECRET_REF_RE = re.compile(r"^secret-ref:[A-Za-z0-9._-]+\.env#[A-Z][A-Z0-9_]*$")
RAW_SHA_RE = re.compile(r"^raw:sha256:([0-9a-f]{64})$")
BANK_REF_RE = re.compile(r"^bank:([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)$")
NOTE_REF_RE = re.compile(r"^note:\[\[([^\]]+)\]\]#(.*)$")

# Allowlisted ontology frontmatter keys
ONTOLOGY_ALLOWLIST = {
    "entity_id",
    "entity_type",
    "about",
    "aliases",
    "alias",
    "title",
    "fact_source",
    "certified_owner",
    "certified_evidence",
    "certified_on",
    "review_after",
}

_PROPOSE_RULES: tuple[tuple[tuple[str | None, ...], str, str], ...] = (
    (("Projects", "Active", None, "context.md"), "proj", "project"),
    (("Workspaces", "Brands & Sites", None, "brand.md"), "brand", "brand"),
    (("Workspaces", "Clients", "Companies", None), "biz", "company"),
    (("Workspaces", "Clients", "People", None), "person", "person"),
    (("Workspaces", "Businesses", None), "biz", "company"),
    (("wiki", "entities", None), "", "entity"),
)
_GENERIC_STEMS = frozenset(
    {
        "context",
        "index",
        "readme",
        "hot",
        "log",
        "overview",
        "study",
        "scorecards",
        "brand",
        "notes",
    }
)
_ASK_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "what",
        "who",
        "how",
        "where",
        "when",
        "why",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "not",
        "but",
        "can",
        "will",
        "status",
        "thing",
        "about",
        "does",
        "into",
        "your",
        "our",
        "their",
    }
)


def _plain_scalar(note: Note, key: str) -> str | None:
    folded = key.casefold()
    for block in note.blocks:
        if block.key and block.key.casefold() == folded and len(block.lines) == 1 and not block.block_scalar:
            val = block.lines[0].split(":", 1)[1].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1].strip()
            return val or None
    return None


def _propose_candidate(rel: str, profile: str) -> tuple[str, str | None, str] | None:
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    for pattern, prefix, etype in _PROPOSE_RULES:
        if len(parts) != len(pattern):
            continue
        name: str | None = None
        for got, want in zip(parts, pattern):
            if want is None:
                name = got[:-3] if got.lower().endswith(".md") else got
            elif portable_key(got) != portable_key(want):
                break
        else:
            if name:
                return prefix, etype, name
    if profile == "standalone" and len(parts) == 2 and portable_key(parts[0]) == "wiki":
        stem = parts[1][:-3] if parts[1].lower().endswith(".md") else parts[1]
        if portable_key(stem) not in _GENERIC_STEMS:
            return "", None, stem
    return None


def _slug_id(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).casefold()).strip("-")
    if not slug:
        slug = "entity"
    eid = f"{prefix}-{slug}" if prefix else slug
    if not eid[0].isalpha():
        eid = f"e-{eid}"
    if len(eid) > 48:
        cut = eid[:48]
        if "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        eid = cut.strip("-") or eid[:48].strip("-")
    return eid


def _is_generic_title(title: str) -> bool:
    t_norm = normalize_alias(title)
    if not t_norm or t_norm in _GENERIC_STEMS:
        return True
    if "context" in t_norm or "`" in title:
        return True
    return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _portable_name_key(path_str: str) -> str:
    """Canonical portable name key: NFC normalized + forward-slashed + casefolded."""
    return portable_key(str(path_str).replace("\\", "/"))


def is_reparse_or_symlink(path: Path) -> bool:
    """Check if path is a symlink or Windows junction / reparse point."""
    try:
        st = path.lstat()
    except (FileNotFoundError, OSError):
        return False
    if path.is_symlink():
        return True
    if sys.platform == "win32":
        if getattr(st, "st_file_attributes", 0) & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            return True
    return False


def detect_secret_like(val: str, *, is_fact_value: bool = False) -> str | None:
    """Detect secrets in values using unified ontology_grammar rules. Returns pattern class name or None."""
    if is_fact_value:
        return fact_secret_like_class(val)
    return secret_like_class(val)


def normalize_alias(alias: str) -> str:
    """Normalized alias form: NFKC + casefold + whitespace collapsed + trimmed."""
    nfkc = unicodedata.normalize("NFKC", alias)
    collapsed = re.sub(r"\s+", " ", nfkc).strip()
    return collapsed.casefold()


def split_frontmatter_strict(content: bytes) -> tuple[bool, bytes, list[str], bytes, bytes] | None:
    """Strict frontmatter splitter delegating to ontology_grammar."""
    has_bom = content.startswith(BOM)
    rest = content[len(BOM):] if has_bom else content
    if BOM in rest:
        return None
    try:
        split = og_split_frontmatter_strict(content, side="old", path="<note>")
    except OntologyRuleError:
        return None
    if split.nl is None:
        return None
    nl = split.nl
    lines = [line.decode("utf-8", errors="replace") for line in split.lines]
    return has_bom, nl, lines, b"", split.body


def is_path_excluded(rel_path: str, exclusions: set[str]) -> bool:
    """Check if a relative forward-slashed path matches exclusions or scope excluded prefixes."""
    folded = _portable_name_key(rel_path)
    for ex in exclusions:
        ex_clean = ex.rstrip("/")
        if folded == ex_clean or folded.startswith(ex_clean + "/"):
            return True
    parts = [p for p in folded.split("/") if p]
    # Exclude internal / non-note folders at any depth
    for part in parts:
        if part in ("node_modules", ".git", ".claude-empire", ".obsidian", ".vault-meta") or part in EXCLUDED_ANY_LEVEL:
            return True
    return False


def get_ontology_roots(vault_root: Path) -> tuple[str, list[str], set[str]]:
    """Determine profile, roots, and exclusions per Spec 5.2 and Carbon Spec v1.1.

    Fails closed (raising OntologyRuleError) on invalid config.
    Returns (profile, roots, exclusions).
    """
    vault_root = Path(vault_root).resolve()
    from .transaction import read_vault_regular

    def _read_file(rel: str) -> bytes | None:
        try:
            return read_vault_regular(vault_root, rel, missing_ok=True)
        except OSError:
            return None

    # Fail-closed per M8-HIGH-3: do not swallow OntologyRuleError
    scope = resolve_scope(_read_file)
    exclusions: set[str] = set(scope.home_managed)
    for rf in ROOT_INSTRUCTION_FILES:
        exclusions.add(portable_key(rf))
    for tl in EXCLUDED_TOP_LEVEL:
        exclusions.add(portable_key(tl))
    for ex in scope.excluded_prefixes:
        exclusions.add(portable_key(ex))

    return scope.profile, list(scope.roots), exclusions


def verify_evidence(
    vault_root: Path,
    current_note_rel: str,
    evidence_ref: str,
    roots: list[str],
    exclusions: set[str],
) -> tuple[bool, dict[str, str] | None, str | None]:
    """Verify evidence ref (raw, bank, note) per HIGH-1 & HIGH-2.

    Returns (verified, precondition_pins, error_code).
    """
    raw_m = RAW_SHA_RE.match(evidence_ref)
    if raw_m:
        target_hex = raw_m.group(1).lower()
        raw_dir = vault_root / ".raw"
        candidates: list[Path] = []
        captured_dir = raw_dir / "captured"
        if captured_dir.exists() and not is_reparse_or_symlink(captured_dir):
            for entry in captured_dir.iterdir():
                if entry.is_file() and not is_reparse_or_symlink(entry) and entry.name.lower().startswith(target_hex):
                    candidates.append(entry)
        if raw_dir.exists() and not is_reparse_or_symlink(raw_dir):
            for entry in raw_dir.iterdir():
                if entry.is_file() and not is_reparse_or_symlink(entry) and entry.name.lower().startswith(target_hex):
                    candidates.append(entry)

        for cand in candidates:
            try:
                content = cand.read_bytes()
                if _sha256_bytes(content) == target_hex:
                    rel = cand.relative_to(vault_root).as_posix()
                    return True, {rel: target_hex}, None
            except OSError:
                continue
        return False, None, "ontology_evidence_hash_mismatch"

    bank_m = BANK_REF_RE.match(evidence_ref)
    if bank_m:
        ws_id, evidence_id = bank_m.groups()
        package = f"{EVIDENCE_PACKAGE_DIR}/{evidence_id}"
        pkg_dir = vault_root / package
        if not pkg_dir.is_dir() or is_reparse_or_symlink(pkg_dir):
            return False, None, "ontology_evidence_missing"

        from .research_evidence import EvidenceError, verify_package
        try:
            report = verify_package(pkg_dir, workspace=ws_id)
        except (EvidenceError, OSError, ValueError):
            return False, None, "ontology_evidence_hash_mismatch"

        if report.get("evidence_id") != evidence_id:
            return False, None, "ontology_evidence_hash_mismatch"

        pins: dict[str, str] = {}
        for member_name in EVIDENCE_PACKAGE_MEMBERS:
            m_file = pkg_dir / member_name
            if not m_file.is_file() or is_reparse_or_symlink(m_file):
                return False, None, "ontology_evidence_missing"
            try:
                pins[f"{package}/{member_name}"] = _sha256_bytes(m_file.read_bytes())
            except OSError:
                return False, None, "ontology_evidence_missing"
        return True, pins, None

    note_m = NOTE_REF_RE.match(evidence_ref)
    if note_m:
        target, anchor = note_m.groups()
        target = target.strip()
        candidates: list[str] = []
        if "/" in target:
            candidate = target if target.lower().endswith(".md") else target + ".md"
            cand_p = vault_root / candidate
            if cand_p.is_file() and not is_reparse_or_symlink(cand_p):
                candidates.append(candidate.replace("\\", "/"))
        else:
            for r_name in roots:
                r_path = vault_root / r_name
                if not r_path.exists() or is_reparse_or_symlink(r_path):
                    continue
                for root_dp, _, fnames in os.walk(r_path):
                    if is_reparse_or_symlink(Path(root_dp)):
                        continue
                    for fn in fnames:
                        if fn.endswith(".md"):
                            cp = Path(root_dp) / fn
                            if not is_reparse_or_symlink(cp) and cp.stem.lower() == target.lower():
                                candidates.append(cp.relative_to(vault_root).as_posix())

        if len(candidates) != 1:
            return False, None, "ontology_evidence_missing"

        resolved = candidates[0]
        # HIGH-1: Reject self-reference
        if _portable_name_key(resolved) == _portable_name_key(current_note_rel):
            return False, None, "ontology_evidence_missing"
        # HIGH-1: Reject instruction or Home-managed file
        if is_path_excluded(resolved, exclusions):
            return False, None, "ontology_evidence_missing"

        target_file = vault_root / resolved
        try:
            target_content = target_file.read_bytes()
        except OSError:
            return False, None, "ontology_evidence_missing"

        from .lint_engine import _normalize_heading
        headings = _headings(target_content)
        normalized_anchor = _normalize_heading(anchor)
        if normalized_anchor not in headings:
            return False, None, "ontology_evidence_hash_mismatch"

        h = _sha256_bytes(target_content)
        return True, {resolved: h}, None

    return False, None, "ontology_evidence_missing"


def build_index(
    vault_root: Path,
    *,
    as_of: str | None = None,
    write_file: bool = True,
) -> tuple[dict[str, Any], int]:
    """Scan vault and generate index.json schema per Spec 5.4.

    Returns (index_doc, exit_code).
    """
    vault_root = Path(vault_root).resolve()
    as_of = as_of or datetime.date.today().isoformat()
    profile, roots, exclusions = get_ontology_roots(vault_root)

    entities: dict[str, dict[str, Any]] = {}
    aliases: dict[str, list[str]] = {}
    facts: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []

    # Temporary collector for fact candidates:
    # key -> list of {value, path, state, evidence, review_after, is_primary, last_verified}
    fact_candidates: dict[str, list[dict[str, Any]]] = {}
    about_records: list[tuple[str, str]] = []  # (about_id, rel_path)
    scanned_notes: list[tuple[str, str]] = []  # (rel_path, sha256)

    # Walk roots
    for root_rel in roots:
        root_dir = vault_root / root_rel
        if not root_dir.exists() or is_reparse_or_symlink(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirpath_p = Path(dirpath)
            if is_reparse_or_symlink(dirpath_p):
                dirnames[:] = []
                continue
            rel_dir = dirpath_p.relative_to(vault_root).as_posix()
            if is_path_excluded(rel_dir, exclusions):
                dirnames[:] = []
                continue

            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                file_path = dirpath_p / fname
                if is_reparse_or_symlink(file_path):
                    continue
                rel_path = file_path.relative_to(vault_root).as_posix()

                if is_path_excluded(rel_path, exclusions):
                    continue
                # NTFS 8.3 short-name reject
                if any("~" in part and re.search(r"~\d+$", part) for part in file_path.parts):
                    continue

                try:
                    raw_bytes = file_path.read_bytes()
                except OSError:
                    continue

                file_hash = _sha256_bytes(raw_bytes)
                scanned_notes.append((rel_path, file_hash))

                try:
                    note = parse_note(raw_bytes, side="old", path=rel_path)
                except OntologyRuleError as exc:
                    f_code = (
                        "frontmatter_mixed_newlines"
                        if exc.code == "ONTOLOGY_LINE_ENDING"
                        else "frontmatter_nested_unsupported"
                    )
                    detail = exc.message
                    if detail.startswith(f"{rel_path}: "):
                        detail = detail[len(rel_path) + 2 :]
                    detail = re.sub(r"^(?:old|new)\s+", "", detail)
                    findings.append({
                        "code": f_code,
                        "path": rel_path,
                        "detail": detail,
                    })
                    continue

                if not note.has_frontmatter:
                    continue

                # Check for unparseable blocks per Spec 5.1
                has_unparseable = any(v is UNPARSEABLE for v in note.values.values()) or any(
                    block.block_scalar for block in note.blocks
                )
                if has_unparseable:
                    findings.append({
                        "code": "frontmatter_nested_unsupported",
                        "path": rel_path,
                        "detail": "frontmatter contains unparseable or unsupported nested blocks",
                    })
                    continue

                data = dict(note.values)
                entity_id = data.get("entity_id")
                about_id = data.get("about")

                if entity_id and about_id:
                    findings.append({
                        "code": "ontology_entity_and_about",
                        "path": rel_path,
                        "detail": f"note declares both entity_id {entity_id!r} and about {about_id!r}",
                    })
                    continue

                # Title extraction & secret scan
                default_stem = (
                    file_path.parent.name
                    if file_path.stem.casefold() in _GENERIC_STEMS and file_path.parent != vault_root
                    else file_path.stem
                )
                raw_title = title_value(note)
                if raw_title:
                    sec_title = detect_secret_like(str(raw_title))
                    if sec_title:
                        findings.append({
                            "code": "ontology_secret_like_value",
                            "path": rel_path,
                            "detail": f"redacted secret-like value in title ({sec_title})",
                        })
                        title = default_stem
                    elif not data.get("title") and default_stem != file_path.stem and _is_generic_title(str(raw_title)):
                        title = default_stem
                    else:
                        title = raw_title
                else:
                    title = default_stem

                # Fact source secret scan
                fact_source = data.get("fact_source")
                if fact_source:
                    sec_src = detect_secret_like(str(fact_source))
                    if sec_src:
                        findings.append({
                            "code": "ontology_secret_like_value",
                            "path": rel_path,
                            "detail": f"redacted secret-like value in fact_source ({sec_src})",
                        })
                        fact_source = None

                # Process entity_id (primary note)
                if entity_id:
                    if not isinstance(entity_id, str) or not ENTITY_ID_RE.match(entity_id):
                        findings.append({
                            "code": "ontology_invalid_entity_id",
                            "path": rel_path,
                            "detail": f"invalid entity_id {entity_id!r}",
                        })
                        continue
                    if entity_id in entities:
                        findings.append({
                            "code": "ontology_duplicate_entity_id",
                            "path": rel_path,
                            "detail": f"duplicate entity_id {entity_id!r} already defined in {entities[entity_id]['path']}",
                        })
                        continue

                    # Collect aliases
                    raw_aliases = note.list_value("aliases")
                    if raw_aliases is UNPARSEABLE or not isinstance(raw_aliases, list):
                        raw_aliases = []

                    clean_aliases = []
                    for raw_al in raw_aliases:
                        al_str = str(raw_al).strip()
                        sec_al = detect_secret_like(al_str)
                        if sec_al:
                            findings.append({
                                "code": "ontology_secret_like_value",
                                "path": rel_path,
                                "detail": f"redacted secret-like value in aliases ({sec_al})",
                            })
                            continue
                        clean_aliases.append(al_str)
                        norm_al = normalize_alias(al_str)
                        if norm_al not in aliases:
                            aliases[norm_al] = []
                        if entity_id not in aliases[norm_al]:
                            aliases[norm_al].append(entity_id)
                        if len(aliases[norm_al]) > 1:
                            findings.append({
                                "code": "ontology_alias_collision",
                                "path": rel_path,
                                "detail": f"alias {norm_al!r} collides between {aliases[norm_al]}",
                            })

                    # Populate entity_type per M8-HIGH-1 & Opus #3
                    entity_type_val = (
                        data.get("entity_type")
                        or _plain_scalar(note, "entity_type")
                        or _plain_scalar(note, "type")
                    )
                    if isinstance(entity_type_val, str):
                        if detect_secret_like(entity_type_val):
                            entity_type_val = None
                        elif entity_type_val == "project-context" and not data.get("entity_type"):
                            entity_type_val = "project"
                    entities[entity_id] = {
                        "path": rel_path,
                        "title": title,
                        "type": entity_type_val,
                        "aliases": clean_aliases,
                    }

                elif about_id:
                    if not isinstance(about_id, str) or not ENTITY_ID_RE.match(about_id):
                        findings.append({
                            "code": "ontology_invalid_about",
                            "path": rel_path,
                            "detail": f"invalid about {about_id!r}",
                        })
                        continue
                    about_records.append((about_id, rel_path))

                # Collect facts
                active_entity = entity_id or about_id
                if not active_entity:
                    for k in data:
                        if k.startswith("fact."):
                            findings.append({
                                "code": "ontology_fact_without_entity",
                                "path": rel_path,
                                "detail": f"fact {_label(k)} declared in note without entity_id or about",
                            })
                    continue

                certified_owner_list = note.list_value("certified_owner")
                certified_owner = set(certified_owner_list) if isinstance(certified_owner_list, list) else set()
                certified_evidence_list = note.list_value("certified_evidence")
                certified_evidence = set(certified_evidence_list) if isinstance(certified_evidence_list, list) else set()
                review_after = data.get("review_after")
                certified_on = data.get("certified_on")

                fact_keys = [k for k in data if k.startswith("fact.")]
                if len(fact_keys) > 40:
                    findings.append({
                        "code": "ontology_too_many_facts",
                        "path": rel_path,
                        "detail": f"note declares {len(fact_keys)} facts (max 40)",
                    })

                # Check certified keys without value
                for k in certified_owner | certified_evidence:
                    if k not in data:
                        findings.append({
                            "code": "ontology_certified_without_value",
                            "path": rel_path,
                            "detail": f"key {_label(k)} listed in certification but missing from frontmatter",
                        })

                for f_key in fact_keys:
                    field = f_key[len("fact.") :]
                    if not FIELD_NAME_RE.match(field):
                        findings.append({
                            "code": "ontology_invalid_field_name",
                            "path": rel_path,
                            "detail": f"invalid field name {field!r}",
                        })
                        continue

                    val = str(data[f_key])
                    sec_f = detect_secret_like(val, is_fact_value=True)
                    if sec_f:
                        findings.append({
                            "code": "ontology_secret_like_value",
                            "path": rel_path,
                            "detail": f"redacted secret-like value in {_label(f_key)} ({sec_f})",
                        })
                        continue

                    # Determine initial state
                    evidence_ref = data.get(f"evidence.{field}")
                    fact_index_key = f"{active_entity}.{field}"
                    state = "draft"
                    ev_verified = False

                    if f_key in certified_owner:
                        if review_after and str(review_after) < as_of:
                            state = "stale"
                            findings.append({
                                "code": "ontology_review_overdue",
                                "path": rel_path,
                                "detail": f"fact {fact_index_key} review overdue ({review_after} < {as_of})",
                            })
                        else:
                            state = "certified-owner"
                    elif f_key in certified_evidence:
                        if not evidence_ref:
                            state = "stale"
                            findings.append({
                                "code": "ontology_evidence_missing",
                                "path": rel_path,
                                "detail": f"certified evidence key {_label(f_key)} missing evidence locator",
                            })
                        else:
                            verified, precond, err = verify_evidence(
                                vault_root, rel_path, evidence_ref, roots, exclusions
                            )
                            if not verified:
                                state = "stale"
                                findings.append({
                                    "code": err or "ontology_evidence_hash_mismatch",
                                    "path": rel_path,
                                    "detail": f"evidence verification failed for {fact_index_key}",
                                })
                            elif review_after and str(review_after) < as_of:
                                state = "stale"
                                findings.append({
                                    "code": "ontology_review_overdue",
                                    "path": rel_path,
                                    "detail": f"fact {fact_index_key} review overdue ({review_after} < {as_of})",
                                })
                            else:
                                state = "certified-evidence"
                                ev_verified = True

                    candidate = {
                        "value": val,
                        "path": rel_path,
                        "state": state,
                        "evidence": evidence_ref,
                        "certified_on": str(certified_on) if certified_on else None,
                        "review_after": str(review_after) if review_after else None,
                        "is_primary": bool(entity_id),
                    }
                    if fact_index_key not in fact_candidates:
                        fact_candidates[fact_index_key] = []
                    fact_candidates[fact_index_key].append(candidate)

    # Check unknown about entities
    for ab_id, ab_path in about_records:
        if ab_id not in entities:
            findings.append({
                "code": "ontology_about_unknown_entity",
                "path": ab_path,
                "detail": f"about targets unknown entity_id {ab_id!r}",
            })

    # Resolve facts & precedence
    rank_order = {
        "certified-owner": 5,
        "certified-evidence": 4,
        "draft": 3,
        "stale": 2,
        "conflict": 1,
    }

    for f_key, cand_list in fact_candidates.items():
        if len(cand_list) == 1:
            c = cand_list[0]
            facts[f_key] = {
                "value": c["value"],
                "state": c["state"],
                "path": c["path"],
                "evidence": c["evidence"],
                "certified_on": c["certified_on"],
                "review_after": c["review_after"],
                "others": [],
            }
            continue

        cand_list.sort(
            key=lambda x: (
                -1 if x["is_primary"] else 0,
                -rank_order.get(x["state"], 0),
                _portable_name_key(x["path"]),
            )
        )

        primary_cand = next((c for c in cand_list if c["is_primary"]), None)
        winner = cand_list[0]
        others: list[dict[str, Any]] = []
        is_conflict = False

        if primary_cand and primary_cand["state"] in ("certified-owner", "certified-evidence"):
            winner = primary_cand
            for c in cand_list:
                if c is winner:
                    continue
                if c["value"] != winner["value"]:
                    others.append({
                        "path": c["path"],
                        "value": c["value"],
                        "status": "superseded",
                    })
                    findings.append({
                        "code": "ontology_superseded_value",
                        "path": c["path"],
                        "detail": f"value in {c['path']} superseded by certified primary value in {winner['path']}",
                    })
        else:
            values = {c["value"] for c in cand_list}
            if len(values) > 1:
                is_conflict = True
                for c in cand_list[1:]:
                    others.append({
                        "path": c["path"],
                        "value": c["value"],
                        "status": "conflicting",
                    })

        facts[f_key] = {
            "value": winner["value"],
            "state": "conflict" if is_conflict else winner["state"],
            "path": winner["path"],
            "evidence": winner["evidence"],
            "certified_on": winner["certified_on"],
            "review_after": winner["review_after"],
            "others": others,
        }

    # Vault fingerprint calculation
    scanned_notes.sort(key=lambda item: _portable_name_key(item[0]))
    fp_stream = "".join(f"{p}:{h}\n" for p, h in scanned_notes).encode("utf-8")
    vault_fingerprint = _sha256_bytes(fp_stream)

    sorted_entities = {k: entities[k] for k in sorted(entities)}
    sorted_aliases = {k: sorted(aliases[k]) for k in sorted(aliases)}
    sorted_facts = {k: facts[k] for k in sorted(facts)}
    findings.sort(key=lambda f: (_portable_name_key(f["path"]), f["code"]))

    certified_count = sum(
        1 for f in facts.values() if f["state"] in ("certified-owner", "certified-evidence")
    )

    index_doc = {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "as_of": as_of,
        "profile": profile,
        "roots": roots,
        "config_sha256": _compute_config_sha256(roots, exclusions),
        "stat_fingerprint": _compute_stat_fingerprint(vault_root, roots, exclusions),
        "vault_fingerprint": vault_fingerprint,
        "entities": sorted_entities,
        "aliases": sorted_aliases,
        "facts": sorted_facts,
        "findings": findings,
        "counts": {
            "notes_scanned": len(scanned_notes),
            "entities": len(entities),
            "aliases": len(aliases),
            "facts": len(facts),
            "certified": certified_count,
        },
    }

    if write_file:
        meta_dir = vault_root / ".vault-meta" / "ontology"
        if is_reparse_or_symlink(vault_root / ".vault-meta") or is_reparse_or_symlink(meta_dir):
            raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "symlink or reparse point detected in .vault-meta")
        meta_dir.mkdir(parents=True, exist_ok=True)
        idx_path = meta_dir / "index.json"
        if is_reparse_or_symlink(idx_path):
            raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "index.json is a symlink or reparse point")
        idx_path.write_text(json.dumps(index_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return index_doc, 0


def _compute_config_sha256(roots: list[str], exclusions: set[str]) -> str:
    payload = {"roots": list(roots), "exclusions": sorted(exclusions)}
    return _sha256_bytes(json.dumps(payload, sort_keys=True).encode("utf-8"))


def _compute_stat_fingerprint(
    vault_root: Path, roots: list[str], exclusions: set[str]
) -> str:
    import stat as _stat

    entries: dict[str, tuple[int, int]] = {}

    def _entry_is_reparse(st: os.stat_result) -> bool:
        if _stat.S_ISLNK(st.st_mode):
            return True
        if sys.platform == "win32" and (getattr(st, "st_file_attributes", 0) & 0x400):
            return True
        return False

    def _scan_dir(cur_dir: Path, rel_prefix: str, *, is_evidence: bool) -> None:
        try:
            with os.scandir(cur_dir) as it:
                items = list(it)
        except OSError:
            return
        for entry in items:
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if _entry_is_reparse(st):
                continue
            rel = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
            if _stat.S_ISDIR(st.st_mode):
                if not is_evidence and is_path_excluded(rel, exclusions):
                    continue
                _scan_dir(Path(entry.path), rel, is_evidence=is_evidence)
            elif _stat.S_ISREG(st.st_mode):
                if not is_evidence:
                    if not entry.name.endswith(".md"):
                        continue
                    if is_path_excluded(rel, exclusions):
                        continue
                    if any("~" in part and re.search(r"~\d+$", part) for part in Path(entry.path).parts):
                        continue
                entries[rel] = (st.st_size, st.st_mtime_ns)

    for root_rel in roots:
        clean_rel = root_rel.replace("\\", "/").strip("/")
        root_dir = vault_root / clean_rel if clean_rel else vault_root
        if not root_dir.exists() or is_reparse_or_symlink(root_dir):
            continue
        if clean_rel and is_path_excluded(clean_rel, exclusions):
            continue
        _scan_dir(root_dir, clean_rel, is_evidence=False)

    for ev_rel in (".raw", EVIDENCE_PACKAGE_DIR):
        ev_dir = vault_root / ev_rel
        if not ev_dir.exists() or is_reparse_or_symlink(ev_dir):
            continue
        _scan_dir(ev_dir, ev_rel, is_evidence=True)

    ordered = sorted(entries.items(), key=lambda item: (_portable_name_key(item[0]), item[0]))
    stream = "".join(f"{p}:{sz}:{mt}\n" for p, (sz, mt) in ordered).encode("utf-8")
    return _sha256_bytes(stream)


def _load_fresh_index(vault_root: Path, *, as_of: str | None = None) -> dict[str, Any] | None:
    vault_root = Path(vault_root).resolve()
    idx_path = vault_root / ".vault-meta" / "ontology" / "index.json"
    if not idx_path.is_file() or is_reparse_or_symlink(idx_path):
        return None
    try:
        saved_doc = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(saved_doc, dict) or saved_doc.get("schema") != INDEX_SCHEMA:
        return None
    if as_of is not None and saved_doc.get("as_of") != as_of:
        return None
    profile, roots, exclusions = get_ontology_roots(vault_root)
    if saved_doc.get("config_sha256") != _compute_config_sha256(roots, exclusions):
        return None
    if saved_doc.get("stat_fingerprint") != _compute_stat_fingerprint(vault_root, roots, exclusions):
        return None
    return saved_doc


def check_index(vault_root: Path, *, as_of: str | None = None) -> tuple[dict[str, Any] | None, int]:
    """Check if index.json is fresh. Returns (index_doc, 0) or (None, 14)."""
    fresh = _load_fresh_index(vault_root, as_of=as_of)
    if fresh is None:
        return None, 14
    return fresh, 0


def load_or_build_index(vault_root: Path, *, as_of: str | None = None) -> tuple[dict[str, Any], int]:
    """Load index.json when present and current; otherwise build in memory."""
    fresh = _load_fresh_index(vault_root, as_of=as_of)
    if fresh is not None:
        return fresh, 0
    return build_index(vault_root, as_of=as_of, write_file=False)


def resolve_term(
    vault_root: Path,
    term: str,
    *,
    index_doc: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Resolve an entity from a term (entity_id, alias, or title).

    Exit codes: 0 (single match), 3 (ambiguous), 4 (not found).
    """
    if index_doc is None:
        index_doc, _ = load_or_build_index(vault_root, as_of=as_of)

    entities = index_doc.get("entities", {})
    aliases = index_doc.get("aliases", {})

    # 1. Exact or case-insensitive entity_id match
    if term in entities:
        return {"entity_id": term, "entity": entities[term]}, 0
    folded_term = term.strip().casefold()
    if folded_term in entities:
        return {"entity_id": folded_term, "entity": entities[folded_term]}, 0

    # 2. Normalized alias match
    norm_term = normalize_alias(term)
    if norm_term in aliases:
        matching_ids = aliases[norm_term]
        if len(matching_ids) == 1:
            eid = matching_ids[0]
            return {"entity_id": eid, "entity": entities.get(eid)}, 0
        elif len(matching_ids) > 1:
            return {"candidates": sorted(matching_ids), "term": term}, 3

    # 3. Title match
    title_matches = [
        eid for eid, edoc in entities.items()
        if normalize_alias(edoc.get("title") or "") == norm_term
    ]
    if len(title_matches) == 1:
        eid = title_matches[0]
        return {"entity_id": eid, "entity": entities.get(eid)}, 0
    elif len(title_matches) > 1:
        return {"candidates": sorted(title_matches), "term": term}, 3

    return {"error": "not found", "term": term}, 4


def need_keys(
    vault_root: Path,
    *,
    keys: list[str] | None = None,
    entity: str | None = None,
    fields: list[str] | None = None,
    no_values: bool = False,
    as_of: str | None = None,
    index_doc: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    """Query fact states for keys with exit code precedence: 12 > 11 > 13 > 10 > 0."""
    if index_doc is None:
        index_doc, _ = load_or_build_index(vault_root, as_of=as_of)

    target_keys: list[str] = []
    if keys:
        target_keys.extend(keys)
    if entity and fields:
        for f in fields:
            target_keys.append(f"{entity}.{f}")

    if not target_keys:
        return {"error": "no keys requested", "ok": False}, 20

    effective_as_of = as_of or index_doc.get("as_of") or datetime.date.today().isoformat()
    facts = index_doc.get("facts", {})
    key_results: dict[str, Any] = {}
    exit_codes_seen: set[int] = set()

    for k in sorted(target_keys):
        if k not in facts:
            key_results[k] = {
                "state": "missing",
                "value": None,
                "path": None,
                "evidence": None,
            }
            exit_codes_seen.add(11)
            continue

        f = facts[k]
        st = f["state"]
        review_after = f.get("review_after")
        if (
            as_of is not None
            and st in ("certified-owner", "certified-evidence")
            and isinstance(review_after, str)
            and review_after < effective_as_of
        ):
            st = "stale"
        val = None if no_values else f["value"]
        key_results[k] = {
            "state": st,
            "value": val,
            "path": f["path"],
            "evidence": f["evidence"],
        }
        if st in ("certified-owner", "certified-evidence"):
            exit_codes_seen.add(0)
        elif st == "draft":
            exit_codes_seen.add(10)
        elif st == "stale":
            exit_codes_seen.add(13)
        elif st == "conflict":
            exit_codes_seen.add(12)

    if 12 in exit_codes_seen:
        code = 12
    elif 11 in exit_codes_seen:
        code = 11
    elif 13 in exit_codes_seen:
        code = 13
    elif 10 in exit_codes_seen:
        code = 10
    else:
        code = 0

    return {
        "ok": (code == 0),
        "exit": code,
        "as_of": effective_as_of,
        "keys": key_results,
    }, code


def ask_question(
    vault_root: Path,
    question: str,
    *,
    index_doc: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Conversational question helper: matches entities and suggested fact keys."""
    if index_doc is None:
        index_doc, _ = load_or_build_index(vault_root, as_of=as_of)

    entities = index_doc.get("entities", {})
    facts = index_doc.get("facts", {})

    tokens = [
        t
        for t in re.findall(r"[a-z0-9]+", question.casefold())
        if len(t) >= 3 and t not in _ASK_STOPWORDS
    ]
    norm_q = normalize_alias(question)
    matched_entities: set[str] = set()

    for eid, edata in entities.items():
        text_parts = [eid, edata.get("title") or "", *(edata.get("aliases") or [])]
        entity_words = set(re.findall(r"[a-z0-9]+", " ".join(text_parts).casefold()))
        if any(t in entity_words for t in tokens):
            matched_entities.add(eid)
            continue
        for al in edata.get("aliases") or []:
            norm_al = normalize_alias(al)
            if len(norm_al) >= 3 and norm_al in norm_q:
                matched_entities.add(eid)
                break

    if tokens:
        for fk, fv in facts.items():
            if "." not in fk:
                continue
            eid, field_name = fk.split(".", 1)
            if eid not in entities or eid in matched_entities:
                continue
            val_str = str(fv.get("value") or "")
            fact_words = set(re.findall(r"[a-z0-9]+", f"{field_name} {val_str}".casefold()))
            if any(t in fact_words for t in tokens):
                matched_entities.add(eid)

    suggested_facts: dict[str, Any] = {}
    for eid in sorted(matched_entities):
        prefix = f"{eid}."
        for fk, fv in facts.items():
            if fk.startswith(prefix):
                suggested_facts[fk] = {
                    "state": fv["state"],
                    "value": fv["value"],
                    "path": fv["path"],
                }

    return {
        "question": question,
        "matched_entities": sorted(matched_entities),
        "suggested_facts": suggested_facts,
    }, 0


def propose_bundles(
    vault_root: Path,
    *,
    operation_id: str = "ontology-proposals",
    generated_at: str,
    roots: list[str] | None = None,
    out_dir: Path | None = None,
    as_of: str | None = None,
    batch_size: int = 150,
    details: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[Path], list[str], int]:
    """Generate proposal transaction bundles (<=150 writes each).

    Pre-validates every bundle via transaction.inspect_bundle and returns approval hashes.
    """
    vault_root = Path(vault_root).resolve()
    from .transaction import _safe_native_security, inspect_bundle, read_vault_regular

    profile, detected_roots, exclusions = get_ontology_roots(vault_root)
    if roots:
        target_roots: list[str] = []
        for r in roots:
            r_clean = r.replace("\\", "/").strip("/")
            if not r_clean or ".." in r_clean.split("/"):
                raise OntologyRuleError("ONTOLOGY_CONFIG_INVALID", f"invalid scope path: {r!r}")
            r_fold = _portable_name_key(r_clean)
            if not any(
                r_fold == _portable_name_key(dr.rstrip("/"))
                or r_fold.startswith(_portable_name_key(dr.rstrip("/")) + "/")
                for dr in detected_roots
            ):
                raise OntologyRuleError(
                    "ONTOLOGY_SCOPE_OUTSIDE_ROOTS",
                    f"scope {r_clean!r} is outside configured ontology roots {detected_roots}",
                )
            target_roots.append(r_clean)
    else:
        target_roots = list(detected_roots)

    def _propose_alias_key(alias: str) -> str:
        norm = normalize_alias(alias)
        return re.sub(r"[-_\s]+", " ", norm).strip()

    def _ensure_safe_out_dir(base_dir: Path | None) -> Path:
        if base_dir is None:
            meta_dir = vault_root / ".vault-meta"
            ont_dir = meta_dir / "ontology"
            target = ont_dir / "bundles"
            for anc in (meta_dir, ont_dir, target):
                if is_reparse_or_symlink(anc):
                    raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir or parent is a symlink or reparse point")
            target.mkdir(parents=True, exist_ok=True)
            if is_reparse_or_symlink(target):
                raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir is a symlink or reparse point")
            return target
        curr = Path(base_dir)
        for anc in (curr, *curr.parents):
            if is_reparse_or_symlink(anc):
                raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir or parent is a symlink or reparse point")
            if anc == vault_root:
                break
        curr.mkdir(parents=True, exist_ok=True)
        if is_reparse_or_symlink(curr):
            raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir is a symlink or reparse point")
        return curr

    seen_ids: set[str] = set()
    seen_aliases: dict[str, str] = {}
    seen_alias_keys: dict[str, str] = {}
    skipped: list[dict[str, Any]] = []
    skipped_aliases: list[dict[str, Any]] = []

    # Pre-scan existing entity_id and aliases across all detected roots
    for r_str in detected_roots:
        r_dir = vault_root / r_str
        if not r_dir.exists() or is_reparse_or_symlink(r_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(r_dir):
            dirpath_p = Path(dirpath)
            if is_reparse_or_symlink(dirpath_p):
                dirnames[:] = []
                continue
            rel_dir = dirpath_p.relative_to(vault_root).as_posix()
            if rel_dir != "." and is_path_excluded(rel_dir, exclusions):
                dirnames[:] = []
                continue
            dirnames[:] = [
                d
                for d in sorted(dirnames)
                if d not in ("node_modules", ".git", ".claude-empire", ".obsidian", ".vault-meta")
                and d.casefold() not in EXCLUDED_ANY_LEVEL
            ]
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                p = Path(dirpath) / fname
                if is_reparse_or_symlink(p):
                    continue
                rel = p.relative_to(vault_root).as_posix()
                if is_path_excluded(rel, exclusions):
                    continue
                try:
                    raw = p.read_bytes()
                    note = parse_note(raw, side="old", path=rel)
                    if note.has_frontmatter:
                        eid = note.values.get("entity_id")
                        if eid and isinstance(eid, str):
                            seen_ids.add(eid)
                            raw_als = note.list_value("aliases")
                            if isinstance(raw_als, list):
                                for al in raw_als:
                                    norm_al = normalize_alias(str(al))
                                    if norm_al and norm_al not in seen_aliases:
                                        seen_aliases[norm_al] = eid
                                    key_al = _propose_alias_key(str(al))
                                    if key_al and key_al not in seen_alias_keys:
                                        seen_alias_keys[key_al] = eid
                except Exception:
                    pass

    proposed_writes_all: list[tuple[str, str, dict[str, Any]]] = []  # (rel, expected_sha256, write_entry)
    visited_rels: set[str] = set()

    for r_str in target_roots:
        r_dir = vault_root / r_str
        if not r_dir.exists() or is_reparse_or_symlink(r_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(r_dir):
            dirpath_p = Path(dirpath)
            if is_reparse_or_symlink(dirpath_p):
                dirnames[:] = []
                continue
            rel_dir = dirpath_p.relative_to(vault_root).as_posix()
            if rel_dir != "." and is_path_excluded(rel_dir, exclusions):
                dirnames[:] = []
                continue
            dirnames.sort()
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue
                p = dirpath_p / fname
                if is_reparse_or_symlink(p):
                    continue
                rel = p.relative_to(vault_root).as_posix()
                if rel in visited_rels or is_path_excluded(rel, exclusions):
                    continue
                visited_rels.add(rel)

                cand = _propose_candidate(rel, profile)
                if cand is None:
                    continue
                prefix, etype, cand_name = cand

                try:
                    _safe_native_security(vault_root, rel)
                    raw = read_vault_regular(vault_root, rel, missing_ok=False)
                    if raw is None:
                        continue
                except Exception as exc:
                    skipped.append({
                        "path": rel,
                        "reason": "native_owner_mismatch",
                        "detail": str(exc),
                    })
                    continue

                try:
                    raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped.append({
                        "path": rel,
                        "reason": "body_not_utf8",
                    })
                    continue

                try:
                    note = parse_note(raw, side="old", path=rel)
                except OntologyRuleError as exc:
                    skipped.append({
                        "path": rel,
                        "reason": "frontmatter_unparseable",
                        "detail": exc.code,
                    })
                    continue

                has_bom = raw.startswith(BOM)
                bom_bytes = BOM if has_bom else b""
                body_raw = raw[len(BOM) :] if has_bom else raw

                if note.has_frontmatter:
                    if note.values.get("entity_id") or note.values.get("about"):
                        continue
                    if any(v is UNPARSEABLE for v in note.values.values()) or any(
                        b.block_scalar for b in note.blocks
                    ):
                        skipped.append({
                            "path": rel,
                            "reason": "frontmatter_unparseable",
                        })
                        continue
                    alias_blocks = [
                        b for b in note.blocks if b.key and b.key.casefold() in ("aliases", "alias")
                    ]
                    if any(b.key.casefold() == "alias" or len(b.lines) == 1 for b in alias_blocks):
                        skipped.append({
                            "path": rel,
                            "reason": "aliases_not_block_list",
                        })
                        continue
                else:
                    if body_raw in (b"---", b"---\n", b"---\r\n") or body_raw.startswith((b"---\n", b"---\r\n")):
                        skipped.append({
                            "path": rel,
                            "reason": "frontmatter_unterminated",
                        })
                        continue
                    alias_blocks = []

                eid = _slug_id(prefix, cand_name)
                if not ENTITY_ID_RE.match(eid) or eid in seen_ids:
                    skipped.append({
                        "path": rel,
                        "reason": "id_collision",
                        "entity_id": eid,
                    })
                    continue

                existing_aliases: list[str] = []
                if note.has_frontmatter and alias_blocks:
                    raw_als = note.list_value("aliases")
                    if isinstance(raw_als, list):
                        existing_aliases = [str(x).strip() for x in raw_als if str(x).strip()]

                existing_bad = False
                for ex_al in existing_aliases:
                    if (
                        detect_secret_like(ex_al)
                        or normalize_alias(ex_al) in seen_aliases
                        or _propose_alias_key(ex_al) in seen_alias_keys
                    ):
                        existing_bad = True
                        break
                if existing_bad:
                    skipped.append({
                        "path": rel,
                        "reason": "alias_collision",
                    })
                    continue

                cand_aliases: list[str] = [cand_name]
                if prefix:
                    if "-" in cand_name:
                        cand_aliases.append(cand_name.replace("-", " "))
                    t_val = title_value(note)
                    if t_val and not _is_generic_title(str(t_val)) and len(str(t_val)) <= 60:
                        cand_aliases.append(str(t_val))

                aliases_to_add: list[str] = []
                local_norms = {normalize_alias(x) for x in existing_aliases}
                local_keys = {_propose_alias_key(x) for x in existing_aliases if _propose_alias_key(x)}
                had_alias_collision = False
                for cand_al in cand_aliases:
                    cand_al_clean = cand_al.strip()
                    if not cand_al_clean:
                        continue
                    norm_al = normalize_alias(cand_al_clean)
                    if not norm_al or norm_al in local_norms:
                        continue
                    sec_cls = detect_secret_like(cand_al_clean)
                    if sec_cls:
                        skipped_aliases.append({
                            "path": rel,
                            "alias": "<redacted>",
                            "reason": f"secret_like:{sec_cls}",
                        })
                        continue
                    key_al = _propose_alias_key(cand_al_clean)
                    if norm_al in seen_aliases or (key_al and key_al in seen_alias_keys):
                        had_alias_collision = True
                        skipped_aliases.append({
                            "path": rel,
                            "alias": cand_al_clean,
                            "reason": "alias_collision",
                        })
                        continue
                    aliases_to_add.append(cand_al_clean)
                    local_norms.add(norm_al)
                    if key_al:
                        local_keys.add(key_al)

                if not existing_aliases and not aliases_to_add:
                    if prefix and had_alias_collision:
                        skipped_aliases.append({
                            "path": rel,
                            "entity_id": eid,
                            "reason": "id_only",
                        })
                    else:
                        skipped.append({
                            "path": rel,
                            "reason": "alias_collision",
                        })
                        continue

                quoted_to_add = [json.dumps(al, ensure_ascii=False) for al in aliases_to_add]

                if note.has_frontmatter:
                    nl_bytes = note.split.nl if note.split.nl is not None else b"\n"
                    nl_str = "\r\n" if nl_bytes == b"\r\n" else "\n"
                    has_etype = _plain_scalar(note, "entity_type") is not None
                    new_blocks: list[str] = [f"entity_id: {eid}"]
                    if etype and not has_etype:
                        new_blocks.append(f"entity_type: {etype}")
                    if not alias_blocks and quoted_to_add:
                        alias_lines = ["aliases:"] + [f"  - {q}" for q in quoted_to_add]
                        new_blocks.append(nl_str.join(alias_lines))

                    for block in note.blocks:
                        if block.key and block.key.casefold() in ("aliases", "alias"):
                            lines = list(block.lines)
                            for q in quoted_to_add:
                                lines.append(f"  - {q}")
                            new_blocks.append(nl_str.join(lines))
                        elif block.key is not None or block.lines:
                            new_blocks.append(nl_str.join(block.lines))

                    fm_text = nl_str.join(new_blocks)
                    new_raw = (
                        bom_bytes
                        + f"---{nl_str}{fm_text}{nl_str}---{nl_str}".encode("utf-8")
                        + note.split.body
                    )
                else:
                    nl_bytes = _first_newline(body_raw)
                    nl_str = "\r\n" if nl_bytes == b"\r\n" else "\n"
                    fm_lines = [f"entity_id: {eid}"]
                    if etype:
                        fm_lines.append(f"entity_type: {etype}")
                    if quoted_to_add:
                        fm_lines.append("aliases:")
                        for q in quoted_to_add:
                            fm_lines.append(f"  - {q}")
                    fm_text = nl_str.join(fm_lines)
                    new_raw = (
                        bom_bytes
                        + f"---{nl_str}{fm_text}{nl_str}---{nl_str}".encode("utf-8")
                        + body_raw
                    )

                try:
                    new_parsed = parse_note(new_raw, side="new", path=rel)
                    check_note_invariants(note, new_parsed)
                    validate_new_values(new_parsed)
                    if owner_certification_reasons(note, new_parsed):
                        raise OntologyRuleError("ONTOLOGY_OWNER_CERTIFICATION", "owner certification touched")
                    new_content = new_raw.decode("utf-8")
                except (OntologyRuleError, UnicodeDecodeError) as exc:
                    skipped.append({
                        "path": rel,
                        "reason": "invariant_violation",
                        "detail": getattr(exc, "code", "ONTOLOGY_UTF8_INVALID"),
                    })
                    continue

                seen_ids.add(eid)
                for norm_al in local_norms:
                    seen_aliases[norm_al] = eid
                for key_al in local_keys:
                    seen_alias_keys[key_al] = eid
                proposed_writes_all.append(
                    (
                        rel,
                        _sha256_bytes(raw),
                        {
                            "path": rel,
                            "mode": "replace",
                            "content": new_content,
                        },
                    )
                )

    bundles: list[dict[str, Any]] = []
    bundle_paths: list[Path] = []
    approval_hashes: list[str] = []
    if proposed_writes_all:
        out_dir = _ensure_safe_out_dir(out_dir)
        for old_b in out_dir.glob("bundle-propose-*.json"):
            if not is_reparse_or_symlink(old_b):
                try:
                    old_b.unlink()
                except OSError:
                    pass

        batch_size = max(1, min(150, batch_size))
        for i in range(0, len(proposed_writes_all), batch_size):
            chunk = proposed_writes_all[i : i + batch_size]
            writes = [w for _, _, w in chunk]
            expected_hashes: dict[str, str | None] = {rel: h for rel, h, _ in chunk}

            bundle_op_id = (
                f"{operation_id}-{i // batch_size}"
                if len(proposed_writes_all) > batch_size
                else operation_id
            )
            bundle = {
                "schema": TRANSACTION_SCHEMA,
                "operation_type": OPERATION_TYPE,
                "operation_id": bundle_op_id,
                "generated_at": generated_at,
                "expected_hashes": expected_hashes,
                "read_preconditions": {},
                "writes": writes,
            }

            # Pre-validate bundle and obtain approved_plan_sha256
            plan = inspect_bundle(vault_root, bundle)
            approval_sha256 = plan["approval_sha256"]
            approval_hashes.append(approval_sha256)

            b_file = out_dir / f"bundle-propose-{i // batch_size}.json"
            b_file.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            bundles.append(bundle)
            bundle_paths.append(b_file)

    if details is not None:
        details["skipped"] = skipped
        details["skipped_aliases"] = skipped_aliases

    return bundles, bundle_paths, approval_hashes, 0


def _verify_secret_ref_exists(secrets_root: Path, ref_val: str, fact_key: str) -> None:
    """Verify that secret-ref:<file>.env#<VAR> exists in secrets_root without reading secret values."""
    if not SECRET_REF_RE.fullmatch(ref_val):
        raise OntologyRuleError(
            "ONTOLOGY_SECRET_REF_INVALID",
            f"invalid secret-ref syntax for {fact_key}",
        )
    if not secrets_root.is_dir() or is_reparse_or_symlink(secrets_root):
        raise OntologyRuleError(
            "ONTOLOGY_SECRET_REF_UNRESOLVED",
            f"secrets root unavailable for {fact_key}",
        )
    rest = ref_val[len("secret-ref:") :]
    env_name, var_name = rest.split("#", 1)
    env_path = secrets_root / env_name
    if not env_path.is_file() or is_reparse_or_symlink(env_path):
        raise OntologyRuleError(
            "ONTOLOGY_SECRET_REF_UNRESOLVED",
            f"secret-ref file {env_name!r} for {fact_key} not found in secrets root",
        )
    var_re = re.compile(rf"^\s*(?:export\s+)?{re.escape(var_name)}\s*=")
    try:
        raw_lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise OntologyRuleError(
            "ONTOLOGY_SECRET_REF_UNRESOLVED",
            f"cannot read secret-ref file {env_name!r} for {fact_key}: {exc}",
        ) from exc
    if not any(var_re.match(line) for line in raw_lines):
        raise OntologyRuleError(
            "ONTOLOGY_SECRET_REF_UNRESOLVED",
            f"secret-ref variable {var_name!r} in {env_name!r} for {fact_key} not defined",
        )


def certify_bundle(
    vault_root: Path,
    *,
    keys: list[str],
    cert_class: str,
    operation_id: str = "ontology-certify",
    generated_at: str,
    digest: str | None = None,
    evidence: str | None = None,
    note_paths: list[str] | None = None,
    out_dir: Path | None = None,
    secrets_root: Path | None = None,
) -> tuple[dict[str, Any], Path, str, int]:
    """Generate a certification transaction bundle for keys.

    cert_class must be 'owner' or 'evidence'.
    """
    vault_root = Path(vault_root).resolve()
    from .transaction import TransactionError, inspect_bundle, read_vault_regular

    if cert_class not in ("owner", "evidence"):
        raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", f"invalid certification class {cert_class!r}")

    allowed_notes: set[str] | None = None
    if note_paths is not None:
        if not note_paths:
            raise OntologyRuleError("ONTOLOGY_TARGET_SCOPE", "note_paths must be non-empty when specified")
        allowed_notes = {
            portable_key(_normalize_relative(np.replace("\\", "/"), label="note_paths entry"))
            for np in note_paths
        }

    index_doc, _ = build_index(vault_root, write_file=False)
    facts = index_doc.get("facts", {})

    target_notes: dict[str, list[str]] = {}  # rel_path -> list of fact keys
    for k in keys:
        if k not in facts:
            raise OntologyRuleError("ONTOLOGY_FACT_UNKNOWN", f"unknown fact key {k!r}")
        f_val = facts[k].get("value")
        if isinstance(f_val, str) and f_val.startswith("secret-ref:"):
            if secrets_root is None:
                raise OntologyRuleError(
                    "ONTOLOGY_SECRET_REF_UNVERIFIED",
                    f"cannot certify secret-ref for {k!r} without a verified secrets_root",
                )
            _verify_secret_ref_exists(Path(secrets_root), f_val, k)
        p = facts[k]["path"]
        if allowed_notes is not None and portable_key(p) not in allowed_notes:
            raise OntologyRuleError(
                "ONTOLOGY_TARGET_SCOPE",
                f"fact {k!r} note {p!r} is outside requested --note-paths",
            )
        if p not in target_notes:
            target_notes[p] = []
        target_notes[p].append(k)

    read_preconditions: dict[str, str] = {}
    digest_rel: str | None = None
    if cert_class == "owner":
        if not digest:
            raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", "digest path required for owner certification")
        try:
            digest_rel = _normalize_relative(digest.replace("\\", "/"), label="certification digest")
        except OntologyRuleError as exc:
            raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", exc.message) from exc
        try:
            digest_bytes = read_vault_regular(vault_root, digest_rel, missing_ok=True)
        except (TransactionError, OSError, ValueError) as exc:
            raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", f"invalid digest path: {digest_rel}") from exc
        if digest_bytes is None:
            raise OntologyRuleError("ONTOLOGY_DIGEST_MISSING", f"digest file missing: {digest_rel}")
        read_preconditions[digest_rel] = _sha256_bytes(digest_bytes)

    profile, roots, exclusions = get_ontology_roots(vault_root)
    for p, k_list in target_notes.items():
        raw = read_vault_regular(vault_root, p, missing_ok=False)
        assert raw is not None
        curr_note = parse_note(raw, side="old", path=p)
        existing_ev_facts = curr_note.list_value("certified_evidence")
        all_ev_field_names: set[str] = set()
        if isinstance(existing_ev_facts, list):
            for item in existing_ev_facts:
                if isinstance(item, str) and item.startswith("fact."):
                    all_ev_field_names.add(item[len("fact.") :])
        if cert_class == "evidence":
            for k in k_list:
                all_ev_field_names.add(k.split(".", 1)[1])

        for field_name in sorted(all_ev_field_names):
            ev_ref = curr_note.values.get(f"evidence.{field_name}")
            if not ev_ref and cert_class == "evidence" and any(k.endswith(f".{field_name}") for k in k_list):
                ev_ref = evidence
            if not ev_ref:
                for fk, fv in facts.items():
                    if fk.endswith(f".{field_name}") and fv.get("path") == p:
                        ev_ref = fv.get("evidence")
                        break
            if not ev_ref:
                raise OntologyRuleError("ONTOLOGY_EVIDENCE_MISSING", f"evidence locator missing for field {field_name}")
            ver, preconds, err = verify_evidence(vault_root, p, ev_ref, roots, exclusions)
            if not ver or not preconds:
                raise OntologyRuleError(err or "ONTOLOGY_EVIDENCE_UNVERIFIED", f"evidence verification failed for {field_name}")
            read_preconditions.update(preconds)

    writes = []
    expected_hashes = {}
    today_str = generated_at[:10] if len(generated_at) >= 10 else datetime.date.today().isoformat()

    for rel, k_list in target_notes.items():
        raw = read_vault_regular(vault_root, rel, missing_ok=False)
        assert raw is not None
        expected_hashes[rel] = _sha256_bytes(raw)

        note = parse_note(raw, side="old", path=rel)
        if not note.has_frontmatter:
            raise OntologyRuleError("ONTOLOGY_FRONTMATTER_MISSING", f"note {rel} lacks valid frontmatter")

        has_bom = raw.startswith(BOM)
        bom_bytes = BOM if has_bom else b""
        nl_bytes = note.split.nl if note.split.nl is not None else b"\n"
        nl_str = "\r\n" if nl_bytes == b"\r\n" else "\n"

        target_field_names = [f"fact.{k.split('.', 1)[1]}" for k in k_list]

        curr_owner = list(note.list_value("certified_owner") if isinstance(note.list_value("certified_owner"), list) else [])
        curr_evidence = list(note.list_value("certified_evidence") if isinstance(note.list_value("certified_evidence"), list) else [])

        if cert_class == "owner":
            for tf in target_field_names:
                if tf not in curr_owner:
                    curr_owner.append(tf)
        else:
            for tf in target_field_names:
                if tf not in curr_evidence:
                    curr_evidence.append(tf)

        # Reconstruct frontmatter blocks preserving non-ontology blocks cleanly
        new_blocks: list[str] = []
        for block in note.blocks:
            if block.key in ("certified_owner", "certified_evidence", "certified_on"):
                continue
            new_blocks.append(nl_str.join(block.lines))

        if curr_owner:
            owner_lines = ["certified_owner:"] + [f"  - {item}" for item in curr_owner]
            new_blocks.append(nl_str.join(owner_lines))
        if curr_evidence:
            ev_lines = ["certified_evidence:"] + [f"  - {item}" for item in curr_evidence]
            new_blocks.append(nl_str.join(ev_lines))
        new_blocks.append(f"certified_on: {today_str}")

        fm_text = nl_str.join(new_blocks)
        new_raw = bom_bytes + f"---{nl_str}{fm_text}{nl_str}---{nl_str}".encode("utf-8") + note.split.body
        new_content = new_raw.decode("utf-8")

        writes.append({
            "path": rel,
            "mode": "replace",
            "content": new_content,
        })

    target_keys_folded = {portable_key(k) for k in expected_hashes}
    read_preconditions = {
        k: v
        for k, v in read_preconditions.items()
        if portable_key(k) not in target_keys_folded
    }

    bundle: dict[str, Any] = {
        "schema": TRANSACTION_SCHEMA,
        "operation_type": OPERATION_TYPE,
        "operation_id": operation_id,
        "generated_at": generated_at,
        "expected_hashes": expected_hashes,
        "read_preconditions": read_preconditions,
        "writes": writes,
    }
    # Only include certification block when owner class per Spec 5.5 and M8-BLOCKER-2
    if cert_class == "owner" and digest_rel:
        bundle["certification"] = {"class": "owner", "digest": digest_rel}

    # Pre-validate bundle via inspect_bundle
    plan = inspect_bundle(vault_root, bundle)
    approval_sha256 = plan["approval_sha256"]

    if out_dir is None:
        meta_dir = vault_root / ".vault-meta"
        ont_dir = meta_dir / "ontology"
        target_out = ont_dir / "bundles"
        for anc in (meta_dir, ont_dir, target_out):
            if is_reparse_or_symlink(anc):
                raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir or parent is a symlink or reparse point")
        out_dir = target_out
    else:
        for anc in (Path(out_dir), *Path(out_dir).parents):
            if is_reparse_or_symlink(anc):
                raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir or parent is a symlink or reparse point")
            if anc == vault_root:
                break
    out_dir.mkdir(parents=True, exist_ok=True)
    if is_reparse_or_symlink(out_dir):
        raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir is a symlink or reparse point")
    b_file = out_dir / f"bundle-certify-{operation_id}.json"
    b_file.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return bundle, b_file, approval_sha256, 0


def run_benchmark(
    vault_root: Path,
    *,
    tasks_path: Path | None = None,
    attempts: int = 3,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Run deterministic benchmark evaluation against active vault index.

    Evaluates index telemetry and optionally executes an external task suite.
    """
    vault_root = Path(vault_root).resolve()
    t0 = time.perf_counter()
    index_doc, _ = load_or_build_index(vault_root, as_of=as_of)

    entities = index_doc.get("entities", {})
    aliases = index_doc.get("aliases", {})
    facts = index_doc.get("facts", {})

    total_entities = len(entities)
    total_aliases = len(aliases)
    total_facts = len(facts)

    certified_facts = sum(1 for f in facts.values() if f.get("state") in ("certified-owner", "certified-evidence"))
    draft_facts = sum(1 for f in facts.values() if f.get("state") == "draft")
    conflict_facts = sum(1 for f in facts.values() if f.get("state") == "conflict")
    stale_facts = sum(1 for f in facts.values() if f.get("state") == "stale")

    # Sample query latency
    latencies = []
    test_queries = list(aliases.keys())[:20] if aliases else ["test"]
    for q in test_queries:
        t_start = time.perf_counter()
        resolve_term(vault_root, q, index_doc=index_doc)
        latencies.append((time.perf_counter() - t_start) * 1000)

    median_lat = sorted(latencies)[len(latencies) // 2] if latencies else 0.0

    report: dict[str, Any] = {
        "status": "complete",
        "entities_indexed": total_entities,
        "aliases_indexed": total_aliases,
        "facts_total": total_facts,
        "facts_certified": certified_facts,
        "facts_draft": draft_facts,
        "facts_conflict": conflict_facts,
        "facts_stale": stale_facts,
        "median_query_latency_ms": round(median_lat, 3),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
    }

    # Evaluate task suite if provided
    if tasks_path:
        tasks_p = Path(tasks_path).resolve()
        if not tasks_p.is_file():
            raise OntologyRuleError("ONTOLOGY_TASK_FILE_MISSING", f"task file not found: {tasks_path}")
        task_data = json.loads(tasks_p.read_text(encoding="utf-8"))
        tasks_list = task_data if isinstance(task_data, list) else task_data.get("tasks", [])

        attempts = max(1, attempts)
        total_evaluations = len(tasks_list) * attempts
        wrong_resolutions = 0
        gate_expected = 0
        gate_observed = 0

        for _ in range(attempts):
            for t in tasks_list:
                # Query resolution check
                if "query" in t:
                    res, r_code = resolve_term(vault_root, t["query"], index_doc=index_doc)
                    if "expected_entity_id" in t and t["expected_entity_id"] is not None:
                        expected_eid = t["expected_entity_id"]
                        resolved_eid = res.get("entity_id")
                        if resolved_eid != expected_eid:
                            wrong_resolutions += 1
                # Need gate check
                if "need_keys" in t:
                    need_res, n_code = need_keys(
                        vault_root,
                        keys=t["need_keys"],
                        as_of=as_of,
                        index_doc=index_doc,
                    )
                    expected_exit = t.get("expected_exit")
                    expect_halt = t.get("expect_halt")
                    has_exit = expected_exit is not None
                    has_halt = isinstance(expect_halt, bool)
                    if has_exit or has_halt:
                        gate_expected += 1
                        exit_ok = (n_code == expected_exit) if has_exit else True
                        halt_ok = ((n_code != 0) == expect_halt) if has_halt else True
                        if exit_ok and halt_ok:
                            gate_observed += 1

        wrong_rate = round(wrong_resolutions / total_evaluations, 4) if total_evaluations else 0.0
        gating_acc = round((gate_observed / gate_expected) * 100, 2) if gate_expected else 100.0
        report["tasks_evaluated"] = len(tasks_list)
        report["attempts"] = attempts
        report["wrong_record_rate"] = wrong_rate
        report["gating_accuracy_percent"] = gating_acc

    return report
