from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "data" / "default.json"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return data


def _merge_lists(base: list[Any], overlay: list[Any]) -> list[Any]:
    if all(isinstance(item, dict) and "id" in item for item in base + overlay):
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
    if path:
        overlay = _read_json(Path(path))
        config = _deep_merge(config, overlay)
    if not isinstance(config.get("findings"), dict):
        raise ValueError("findings must be an object")
    for section in ("surfaces", "classifier_rules"):
        if not isinstance(config.get(section), list):
            raise ValueError(f"{section} must be a list")
    for surface in config["surfaces"]:
        if not isinstance(surface, dict) or not all(isinstance(surface.get(k), str) and surface[k] for k in ("id", "path")):
            raise ValueError("each surface requires an id and vault-relative path")
        for key in ("max_words", "max_age_days", "max_living_bullets"):
            if key in surface and (not isinstance(surface[key], (int, float)) or surface[key] < 0):
                raise ValueError(f"surface {key} must be nonnegative")
    for rule in config["classifier_rules"]:
        if not isinstance(rule, dict) or "id" not in rule or rule.get("class") not in set("ABCDEFGHI"):
            raise ValueError("classifier rule requires id and class A-I")
    operations = config.get("operations", {})
    if not isinstance(operations, dict) or not isinstance(operations.get("review_after_days", 14), int) or operations.get("review_after_days", 14) < 0:
        raise ValueError("operations.review_after_days must be a nonnegative integer")
    return config


def config_hash(config: dict[str, Any]) -> str:
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
