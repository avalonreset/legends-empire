from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from .paths import is_disallowed_path_token, read_bounded_json

try:
    from re import _parser as _re_parser  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_parse as _re_parser  # type: ignore[no-redef]

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "data" / "default.json"


def _check_redos_ast(pattern: str) -> str | None:
    try:
        parsed = _re_parser.parse(pattern)
    except re.error as exc:
        return f"invalid regex: {exc}"

    def _walk(subpattern: Any, *, in_repeat: bool = False) -> str | None:
        for opcode, av in subpattern:
            op_name = str(opcode).upper()
            if op_name in {"GROUPREF", "GROUPREF_EXISTS"}:
                return "pattern contains a backreference"
            if op_name in {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}:
                _min_rep, max_rep, child = av
                if in_repeat:
                    return "pattern contains a nested quantifier"
                err = _walk(child, in_repeat=in_repeat or (max_rep != 1))
                if err is not None:
                    return err
            elif op_name == "BRANCH":
                if in_repeat:
                    return "pattern contains a nested quantifier or alternation in repeat"
                for branch_sub in av[1]:
                    err = _walk(branch_sub, in_repeat=in_repeat)
                    if err is not None:
                        return err
            elif op_name == "SUBPATTERN":
                err = _walk(av[3], in_repeat=in_repeat)
                if err is not None:
                    return err
            elif op_name in {"ASSERT", "ASSERT_NOT"}:
                err = _walk(av[1], in_repeat=in_repeat)
                if err is not None:
                    return err
            elif op_name == "ATOMIC_GROUP":
                err = _walk(av, in_repeat=in_repeat)
                if err is not None:
                    return err
        return None

    return _walk(parsed, in_repeat=False)


def validate_conflict_keys(conflict_keys: Any) -> list[tuple[str, re.Pattern[str]]]:
    if not isinstance(conflict_keys, list):
        raise ValueError("conflict_keys must be a list")
    compiled_list: list[tuple[str, re.Pattern[str]]] = []
    for ck in conflict_keys:
        if not isinstance(ck, dict) or not isinstance(ck.get("id"), str) or not ck["id"].strip():
            raise ValueError("each conflict_keys item requires a non-empty string id")
        key_id = ck["id"].strip()
        pattern = ck.get("pattern")
        if not isinstance(pattern, str) or not pattern or len(pattern) > 256:
            raise ValueError(f"conflict_keys '{key_id}' pattern must be a 1..256 char string")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"conflict_keys '{key_id}' invalid regex: {exc}") from exc
        ast_err = _check_redos_ast(pattern)
        if ast_err is not None:
            raise ValueError(f"conflict_keys '{key_id}' {ast_err}")
        if compiled.groups != 1:
            raise ValueError(
                f"conflict_keys '{key_id}' pattern must have exactly 1 capture group (got {compiled.groups})"
            )
        compiled_list.append((key_id, compiled))
    return compiled_list


def _read_json(path: Path) -> dict[str, Any]:
    data = read_bounded_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return data


def _merge_lists(base: list[Any], overlay: list[Any]) -> list[Any]:
    if all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in base + overlay):
        keyed = {item["id"]: deepcopy(item) for item in base}
        for item in overlay:
            keyed[item["id"]] = _deep_merge(keyed.get(item["id"], {}), item)
        return list(keyed.values())
    if all(isinstance(item, str) for item in base) and all(isinstance(item, str) for item in overlay):
        seen = set(base)
        merged = list(base)
        for item in overlay:
            if item not in seen:
                merged.append(item)
                seen.add(item)
        return merged
    return list(base) + deepcopy(overlay)


def _normalize_surface_path(raw: str) -> str:
    s = raw.strip()
    if not s or s.endswith(("/", "\\")):
        raise ValueError(f"surface path must be a safe vault-relative path: {raw}")
    if is_disallowed_path_token(s, allow_tilde=False, reject_env=True) is not None:
        raise ValueError(f"surface path must be a safe vault-relative path: {raw}")
    if Path(s).is_absolute() or re.match(r"^[A-Za-z]:[/\\]", s) or s.startswith(("/", "\\")):
        raise ValueError(f"surface path must be a safe vault-relative path: {raw}")
    clean = s.replace("\\", "/")
    parts = PurePosixPath(clean).parts
    if ".." in parts:
        raise ValueError(f"surface path must be a safe vault-relative path: {raw}")
    norm = PurePosixPath(clean).as_posix()
    if norm in {"", "."}:
        raise ValueError(f"surface path must be a safe vault-relative path: {raw}")
    return norm


def _surface_path_key(path: str) -> str:
    return path.lower() if os.name == "nt" else path


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in overlay.items():
        if key == "comment":
            continue
        current = out.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            out[key] = _deep_merge(current, value)
        elif isinstance(value, list) and isinstance(current, list):
            out[key] = _merge_lists(current, value)
        else:
            out[key] = deepcopy(value)
    return out


def load_config(path: str | Path | None = None, profile: str = "generic") -> dict[str, Any]:
    config = _read_json(DEFAULT_CONFIG_PATH)
    if profile not in {"generic", "empire"}:
        raise ValueError("profile must be generic or empire")
    if profile == "empire":
        config = _deep_merge(config, _read_json(PACKAGE_DIR / "data" / "empire.json"))
    base_optional_ids = {
        s["id"]
        for s in config.get("surfaces", [])
        if isinstance(s, dict) and isinstance(s.get("id"), str) and s.get("optional") is True
    }
    overlay_surface_ids: set[str] = set()
    if path:
        overlay = _read_json(Path(path))
        if isinstance(overlay.get("surfaces"), list):
            overlay_surface_ids = {
                s["id"]
                for s in overlay["surfaces"]
                if isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"]
            }
        config = _deep_merge(config, overlay)
    if not isinstance(config.get("findings"), dict):
        raise ValueError("findings must be an object")
    for section in ("surfaces", "classifier_rules"):
        if not isinstance(config.get(section), list):
            raise ValueError(f"{section} must be a list")
    for str_list_key in ("entrypoints", "skip_dir_names", "skip_dir_prefixes"):
        if str_list_key in config:
            str_list_val = config[str_list_key]
            if not isinstance(str_list_val, list) or not all(isinstance(item, str) for item in str_list_val):
                raise ValueError(f"{str_list_key} must be a list of strings")
    for surface in config["surfaces"]:
        if not isinstance(surface, dict) or not all(isinstance(surface.get(k), str) and surface[k].strip() for k in ("id", "path")):
            raise ValueError("each surface requires an id and vault-relative path")
        surface["path"] = _normalize_surface_path(surface["path"])
        if "role" in surface:
            if surface["role"] is None:
                surface.pop("role", None)
            elif not isinstance(surface["role"], str) or surface["role"] not in {"hot", "core"}:
                raise ValueError("surface role must be 'hot' or 'core'")
        if "optional" in surface and not isinstance(surface["optional"], bool):
            raise ValueError("surface optional must be a boolean")
        for key in ("max_words", "max_age_days", "max_living_bullets", "max_bytes", "max_dated_lines"):
            if key in surface and (isinstance(surface[key], bool) or not isinstance(surface[key], (int, float)) or surface[key] < 0):
                raise ValueError(f"surface {key} must be nonnegative")
    if overlay_surface_ids:
        overlay_paths = {
            _surface_path_key(s["path"])
            for s in config["surfaces"]
            if s["id"] in overlay_surface_ids
        }
        config["surfaces"] = [
            s
            for s in config["surfaces"]
            if not (
                s["id"] in base_optional_ids
                and s["id"] not in overlay_surface_ids
                and _surface_path_key(s["path"]) in overlay_paths
            )
        ]
    for rule in config["classifier_rules"]:
        if (
            not isinstance(rule, dict)
            or not isinstance(rule.get("id"), str)
            or not rule["id"].strip()
            or not isinstance(rule.get("class"), str)
            or rule["class"] not in set("ABCDEFGHI")
        ):
            raise ValueError("classifier rule requires id and class A-I")
        if "weight" in rule and (isinstance(rule["weight"], bool) or not isinstance(rule["weight"], (int, float)) or rule["weight"] < 0):
            raise ValueError("classifier rule weight must be nonnegative")
    operations = config.get("operations", {})
    if not isinstance(operations, dict) or isinstance(operations.get("review_after_days", 14), bool) or not isinstance(operations.get("review_after_days", 14), int) or operations.get("review_after_days", 14) < 0:
        raise ValueError("operations.review_after_days must be a nonnegative integer")

    harnesses = config.get("entry_harnesses", [])
    if not isinstance(harnesses, list):
        raise ValueError("entry_harnesses must be a list")
    for harness in harnesses:
        if not isinstance(harness, dict) or not isinstance(harness.get("id"), str) or not harness["id"].strip():
            raise ValueError("each entry_harnesses item requires a non-empty string id")
        raw_entry = harness.get("entry")
        if isinstance(raw_entry, str) and raw_entry.strip():
            entries = [raw_entry.strip()]
            harness["entry"] = entries
        elif isinstance(raw_entry, list) and raw_entry and all(isinstance(e, str) and e.strip() for e in raw_entry):
            entries = [e.strip() for e in raw_entry]
        else:
            raise ValueError(f"harness {harness['id']} requires a non-empty entry list")
        for entry_path in entries:
            reason = is_disallowed_path_token(entry_path, allow_tilde=True, reject_env=True)
            if reason is not None:
                raise ValueError(f"harness {harness['id']} entry '{entry_path}' rejected: {reason}")
        core = harness.get("core")
        if core is not None:
            if not isinstance(core, str) or not core.strip():
                raise ValueError(f"harness {harness['id']} core must be a non-empty string")
            reason = is_disallowed_path_token(core, allow_tilde=True, reject_env=True)
            if reason is not None:
                raise ValueError(f"harness {harness['id']} core '{core}' rejected: {reason}")
        core_by_ref = harness.get("core_by_reference")
        if core_by_ref is not None and not isinstance(core_by_ref, bool):
            raise ValueError(f"harness {harness['id']} core_by_reference must be a boolean")
        max_chain = harness.get("max_chain_bytes")
        if max_chain is not None:
            if isinstance(max_chain, bool) or not isinstance(max_chain, int) or max_chain < 0:
                raise ValueError(f"harness {harness['id']} max_chain_bytes must be a nonnegative integer")
        shim = harness.get("shim")
        if shim is not None:
            if not isinstance(shim, dict) or not isinstance(shim.get("path"), str) or not shim["path"].strip():
                raise ValueError(f"harness {harness['id']} shim requires a path string")
            reason = is_disallowed_path_token(shim["path"], allow_tilde=True, reject_env=True)
            if reason is not None:
                raise ValueError(f"harness {harness['id']} shim path '{shim['path']}' rejected: {reason}")
            shim_max = shim.get("max_bytes")
            if shim_max is not None and (isinstance(shim_max, bool) or not isinstance(shim_max, int) or shim_max < 0):
                raise ValueError(f"harness {harness['id']} shim max_bytes must be a nonnegative integer")

    forbidden = config.get("forbidden_paths", [])
    if not isinstance(forbidden, list) or not all(isinstance(p, str) and p.strip() for p in forbidden):
        raise ValueError("forbidden_paths must be a list of non-empty strings")
    for f_path in forbidden:
        reason = is_disallowed_path_token(f_path, allow_tilde=True, reject_env=False)
        if reason is not None:
            raise ValueError(f"forbidden_paths item '{f_path}' rejected: {reason}")

    law_paths = config.get("law_paths", [])
    if not isinstance(law_paths, list) or not all(isinstance(p, str) and p.strip() for p in law_paths):
        raise ValueError("law_paths must be a list of non-empty strings")
    for l_path in law_paths:
        clean_lp = l_path.strip().replace("\\", "/")
        reason = is_disallowed_path_token(l_path, allow_tilde=False, reject_env=True)
        if (
            reason is not None
            or Path(l_path).is_absolute()
            or re.match(r"^[A-Za-z]:[/\\]", l_path.strip())
            or ".." in PurePosixPath(clean_lp).parts
            or PurePosixPath(clean_lp).as_posix() in {"", "."}
        ):
            raise ValueError(f"law_paths item '{l_path}' must be an in-vault relative glob")

    if "duplicate_block_entry_only" in config and not isinstance(config["duplicate_block_entry_only"], bool):
        raise ValueError("duplicate_block_entry_only must be a boolean")

    validate_conflict_keys(config.get("conflict_keys", []))

    return config


def config_hash(config: dict[str, Any]) -> str:
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
