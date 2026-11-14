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
        fact_secret_like_class,
        is_ontology_key,
        portable_key,
        resolve_scope,
        secret_like_class,
        split_frontmatter_strict as og_split_frontmatter_strict,
        parse_note,
        title_value,
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
        fact_secret_like_class,
        is_ontology_key,
        portable_key,
        resolve_scope,
        secret_like_class,
        split_frontmatter_strict as og_split_frontmatter_strict,
        parse_note,
        title_value,
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
    parts = folded.split("/")
    # Exclude internal / non-note folders at any depth
    for part in parts[:-1]:
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
            data = read_vault_regular(vault_root, rel, missing_ok=True)
            if data is not None and rel == WORKSPACE_CONFIG:
                try:
                    cfg = json.loads(data.decode("utf-8"))
                    if "ontology.roots" in cfg and "ontology" not in cfg:
                        cfg["ontology"] = {"roots": cfg["ontology.roots"]}
                        return json.dumps(cfg).encode("utf-8")
                except Exception:
                    pass
            return data
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
                    findings.append({
                        "code": "frontmatter_nested_unsupported",
                        "path": rel_path,
                        "detail": f"{rel_path}: {exc.message}",
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
                        "detail": f"{rel_path}: frontmatter contains unparseable or unsupported nested blocks",
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
                raw_title = title_value(note)
                if raw_title:
                    sec_title = detect_secret_like(str(raw_title))
                    if sec_title:
                        findings.append({
                            "code": "ontology_secret_like_value",
                            "path": rel_path,
                            "detail": f"redacted secret-like value in title ({sec_title})",
                        })
                        title = file_path.stem
                    else:
                        title = raw_title
                else:
                    title = file_path.stem

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

                    # Populate entity_type per M8-HIGH-1
                    entity_type_val = data.get("entity_type") or data.get("type")
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
        "config_sha256": _sha256_bytes(json.dumps(roots, sort_keys=True).encode()),
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


def check_index(vault_root: Path, *, as_of: str | None = None) -> tuple[dict[str, Any] | None, int]:
    """Check if index.json is fresh. Returns (index_doc, 0) or (None, 14)."""
    idx_path = Path(vault_root) / ".vault-meta" / "ontology" / "index.json"
    if not idx_path.is_file() or is_reparse_or_symlink(idx_path):
        return None, 14

    try:
        saved_doc = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return None, 14

    mem_doc, _ = build_index(vault_root, as_of=as_of, write_file=False)
    if mem_doc["vault_fingerprint"] != saved_doc.get("vault_fingerprint"):
        return None, 14

    return saved_doc, 0


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
        index_doc, _ = build_index(vault_root, as_of=as_of, write_file=False)

    entities = index_doc.get("entities", {})
    aliases = index_doc.get("aliases", {})

    # 1. Exact entity_id match
    if term in entities:
        return {"entity_id": term, "entity": entities[term]}, 0

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
        index_doc, _ = build_index(vault_root, as_of=as_of, write_file=False)

    target_keys: list[str] = []
    if keys:
        target_keys.extend(keys)
    if entity and fields:
        for f in fields:
            target_keys.append(f"{entity}.{f}")

    if not target_keys:
        return {"error": "no keys requested", "ok": False}, 20

    as_of = index_doc.get("as_of") or datetime.date.today().isoformat()
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
        "as_of": as_of,
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
        index_doc, _ = build_index(vault_root, as_of=as_of, write_file=False)

    entities = index_doc.get("entities", {})
    facts = index_doc.get("facts", {})

    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9_-]+", question) if len(t) >= 2]
    matched_entities: set[str] = set()

    for eid, edata in entities.items():
        norm_eid = eid.lower()
        if any(t in norm_eid for t in tokens):
            matched_entities.add(eid)
            continue
        title = (edata.get("title") or "").lower()
        if any(t in title for t in tokens):
            matched_entities.add(eid)
            continue
        for al in edata.get("aliases") or []:
            if any(t in al.lower() for t in tokens):
                matched_entities.add(eid)
                break

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
) -> tuple[list[dict[str, Any]], list[Path], list[str], int]:
    """Generate proposal transaction bundles (<=150 writes each).

    Pre-validates every bundle via transaction.inspect_bundle and returns approval hashes.
    """
    vault_root = Path(vault_root).resolve()
    from .transaction import inspect_bundle

    profile, detected_roots, exclusions = get_ontology_roots(vault_root)
    target_roots = roots or detected_roots

    notes_to_propose: list[tuple[str, Path, str]] = []  # (rel_path, abs_path, entity_id)
    seen_ids: set[str] = set()

    # Pre-scan existing entity_id values across all roots
    for r_str in target_roots:
        r_dir = vault_root / r_str
        if not r_dir.exists() or is_reparse_or_symlink(r_dir):
            continue
        for dirpath, _, filenames in os.walk(r_dir):
            if is_reparse_or_symlink(Path(dirpath)):
                continue
            for fname in filenames:
                if fname.endswith(".md"):
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
                    except Exception:
                        pass

    for r_str in target_roots:
        r_dir = vault_root / r_str
        if not r_dir.exists() or is_reparse_or_symlink(r_dir):
            continue
        for dirpath, _, filenames in os.walk(r_dir):
            if is_reparse_or_symlink(Path(dirpath)):
                continue
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
                except Exception:
                    continue

                if note.has_frontmatter:
                    if note.values.get("entity_id") or note.values.get("about"):
                        continue
                stem_id = re.sub(r"[^a-z0-9-]+", "-", p.stem.casefold()).strip("-")
                if not stem_id or not stem_id[0].isalpha():
                    stem_id = f"e-{stem_id}"
                stem_id = stem_id[:48]
                base_id = stem_id
                ctr = 1
                while stem_id in seen_ids:
                    stem_id = f"{base_id[:40]}-{ctr}"
                    ctr += 1
                seen_ids.add(stem_id)
                notes_to_propose.append((rel, p, stem_id))

    bundles: list[dict[str, Any]] = []
    bundle_paths: list[Path] = []
    approval_hashes: list[str] = []
    out_dir = out_dir or (vault_root / ".vault-meta" / "ontology" / "bundles")
    if is_reparse_or_symlink(out_dir):
        raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir is a symlink or reparse point")
    out_dir.mkdir(parents=True, exist_ok=True)

    batch_size = max(1, min(150, batch_size))
    for i in range(0, len(notes_to_propose), batch_size):
        chunk = notes_to_propose[i : i + batch_size]
        writes = []
        expected_hashes: dict[str, str | None] = {}

        for rel, p, eid in chunk:
            raw = p.read_bytes()
            h = _sha256_bytes(raw)
            expected_hashes[rel] = h

            has_bom = raw.startswith(BOM)
            bom_bytes = BOM if has_bom else b""
            note = parse_note(raw, side="old", path=rel)
            quoted_stem = json.dumps(p.stem, ensure_ascii=False)

            if note.has_frontmatter:
                nl_bytes = note.split.nl if note.split.nl is not None else b"\n"
                nl_str = "\r\n" if nl_bytes == b"\r\n" else "\n"
                # Reconstruct frontmatter blocks cleanly
                # Check if aliases already present
                has_aliases = any(block.key in ("aliases", "alias") for block in note.blocks if block.key)
                new_blocks: list[str] = [f"entity_id: {eid}"]
                if not has_aliases:
                    new_blocks.append(f"aliases:{nl_str}  - {quoted_stem}")

                for block in note.blocks:
                    if block.key in ("aliases", "alias"):
                        # Keep existing aliases, append quoted_stem if not present
                        curr_aliases = note.list_value(block.key)
                        if isinstance(curr_aliases, list) and p.stem not in curr_aliases:
                            lines = list(block.lines)
                            lines.append(f"  - {quoted_stem}")
                            new_blocks.append(nl_str.join(lines))
                        else:
                            new_blocks.append(nl_str.join(block.lines))
                    elif block.key is not None:
                        new_blocks.append(nl_str.join(block.lines))
                    elif block.lines:
                        new_blocks.append(nl_str.join(block.lines))

                fm_text = nl_str.join(new_blocks)
                new_raw = bom_bytes + f"---{nl_str}{fm_text}{nl_str}---{nl_str}".encode("utf-8") + note.split.body
                new_content = new_raw.decode("utf-8")
            else:
                # No frontmatter on note
                nl_bytes = b"\r\n" if b"\r\n" in raw else b"\n"
                nl_str = "\r\n" if nl_bytes == b"\r\n" else "\n"
                body_raw = raw[len(BOM):] if has_bom else raw
                new_fm = f"---{nl_str}entity_id: {eid}{nl_str}aliases:{nl_str}  - {quoted_stem}{nl_str}---{nl_str}"
                new_raw = bom_bytes + new_fm.encode("utf-8") + body_raw
                new_content = new_raw.decode("utf-8")

            writes.append({
                "path": rel,
                "mode": "replace",
                "content": new_content,
            })

        bundle_op_id = f"{operation_id}-{i // batch_size}" if len(notes_to_propose) > batch_size else operation_id
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

    return bundles, bundle_paths, approval_hashes, 0


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
) -> tuple[dict[str, Any], Path, str, int]:
    """Generate a certification transaction bundle for keys.

    cert_class must be 'owner' or 'evidence'.
    """
    vault_root = Path(vault_root).resolve()
    from .transaction import inspect_bundle

    if cert_class not in ("owner", "evidence"):
        raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", f"invalid certification class {cert_class!r}")

    index_doc, _ = build_index(vault_root, write_file=False)
    facts = index_doc.get("facts", {})

    target_notes: dict[str, list[str]] = {}  # rel_path -> list of fact keys
    for k in keys:
        if k not in facts:
            raise OntologyRuleError("ONTOLOGY_FACT_UNKNOWN", f"unknown fact key {k!r}")
        p = facts[k]["path"]
        if p not in target_notes:
            target_notes[p] = []
        target_notes[p].append(k)

    read_preconditions: dict[str, str] = {}
    if cert_class == "owner":
        if not digest:
            raise OntologyRuleError("ONTOLOGY_CERTIFICATION_INVALID", "digest path required for owner certification")
        digest_rel = digest.replace("\\", "/")
        digest_path = vault_root / digest_rel
        if not digest_path.is_file() or is_reparse_or_symlink(digest_path):
            raise OntologyRuleError("ONTOLOGY_DIGEST_MISSING", f"digest file missing: {digest_rel}")
        read_preconditions[digest_rel] = _sha256_bytes(digest_path.read_bytes())
    elif cert_class == "evidence":
        profile, roots, exclusions = get_ontology_roots(vault_root)
        for p, k_list in target_notes.items():
            abs_p = vault_root / p
            raw = abs_p.read_bytes()
            curr_note = parse_note(raw, side="old", path=p)
            existing_ev_facts = curr_note.list_value("certified_evidence")
            all_ev_field_names: set[str] = set()
            if isinstance(existing_ev_facts, list):
                for item in existing_ev_facts:
                    if item.startswith("fact."):
                        all_ev_field_names.add(item[len("fact."):])
            for k in k_list:
                all_ev_field_names.add(k.split(".", 1)[1])

            for field_name in all_ev_field_names:
                ev_ref = curr_note.values.get(f"evidence.{field_name}")
                if not ev_ref and any(k.endswith(f".{field_name}") for k in k_list):
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
        abs_p = vault_root / rel
        raw = abs_p.read_bytes()
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
    if cert_class == "owner" and digest:
        bundle["certification"] = {"class": "owner", "digest": digest.replace("\\", "/")}

    # Pre-validate bundle via inspect_bundle
    plan = inspect_bundle(vault_root, bundle)
    approval_sha256 = plan["approval_sha256"]

    out_dir = out_dir or (vault_root / ".vault-meta" / "ontology" / "bundles")
    if is_reparse_or_symlink(out_dir):
        raise OntologyRuleError("ONTOLOGY_PATH_INSECURE", "out_dir is a symlink or reparse point")
    out_dir.mkdir(parents=True, exist_ok=True)
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
    index_doc, check_code = check_index(vault_root, as_of=as_of)
    if index_doc is None:
        index_doc, _ = build_index(vault_root, as_of=as_of, write_file=False)

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
                    expected_eid = t.get("expected_entity_id")
                    if expected_eid:
                        resolved_eid = res.get("entity_id")
                        if resolved_eid and resolved_eid != expected_eid:
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
                    if t.get("expect_halt"):
                        gate_expected += 1
                        if n_code != 0:
                            gate_observed += 1

        wrong_rate = round(wrong_resolutions / total_evaluations, 4) if total_evaluations else 0.0
        gating_acc = round((gate_observed / gate_expected) * 100, 2) if gate_expected else 100.0
        report["tasks_evaluated"] = len(tasks_list)
        report["attempts"] = attempts
        report["wrong_record_rate"] = wrong_rate
        report["gating_accuracy_percent"] = gating_acc

    return report
