"""Reviewed local materialization of qualified, privately supplied Home methods.

No downloads, account authentication, plugin activation or desktop settings.
Each bounded batch uses the existing recoverable transaction engine.
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from . import home_adapter as adapter
from . import home_profile as profile
from .transaction import apply_bundle, inspect_bundle, read_vault_regular, sha256_bytes

STATE = f"{profile.BASE}/installation.json"
SCHEMA = "legends.home-installation/v1"
ROOTS = ("AI Team/", "Workspaces/", "Projects/", "_system/", "_templates/", "Home/", "_assets/", "_examples/")
EXACT = {".agents/skills/aimh/SKILL.md", ".claude/skills/aimh/SKILL.md", "LICENSE.md", "THIRD_PARTY_NOTICES.md", "QUICKSTART.md", "requirements-dev.txt"}
SUFFIXES = {".md", ".json", ".py", ".txt", ".base", ".canvas", ".css", ".svg", ".yaml", ".yml", ".sh"}
BATCH_SIZE = 400


def selected(path: str) -> bool:
    if path in EXACT:
        return True
    if path.startswith("_third-party/"):
        name = PurePosixPath(path).name.upper()
        return PurePosixPath(path).suffix in {".md", ".txt", ".json"} or name in {"LICENSE", "NOTICE"} or name.endswith("-LICENSE")
    return path.startswith(ROOTS) and (PurePosixPath(path).suffix in SUFFIXES or PurePosixPath(path).name in (".keep", ".gitkeep"))


def mutable_seed(path: str) -> bool:
    # These are explicitly owner-operated session/approval records, not methods
    # or executable code. Preserve initial hashes, but never freeze their updates.
    return path == "AI Team/Sessions/handoff.md"


def valid_seed(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8")
    except UnicodeError:
        return False
    front = text.split("---", 2)
    return (len(payload) <= 1024 * 1024 and len(front) == 3 and not front[0].strip()
            and re.search(r"(?m)^type:\s*['\"]?handoff['\"]?\s*$", front[1]) is not None)


def inventory_rows(payload: bytes) -> dict[str, dict]:
    pin = json.loads(adapter.PIN.read_text(encoding="utf-8"))
    if sha256_bytes(payload.replace(b"\r\n", b"\n")) != pin["files"]["PACKAGE-INTEGRITY.json"]:
        adapter.fail("Home inventory is not the qualified release inventory")
    try:
        inventory = json.loads(payload)
        rows = inventory["files"]
    except (ValueError, TypeError, KeyError):
        adapter.fail("invalid qualified inventory")
    if not isinstance(rows, list) or len(rows) > 8192:
        adapter.fail("inventory exceeds supported scope")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            adapter.fail("invalid inventory row")
        path = row["path"]
        if path in result or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts or "\\" in path:
            adapter.fail("noncanonical inventory path")
        result[path] = row
    return result


def write_allowed(path: str, mode: str) -> bool:
    if path == STATE:
        return mode in ("create", "replace")
    return mode == "create" and (selected(path) or path == "PACKAGE-INTEGRITY.json")


def validate_prepared(prepared, operation: dict) -> None:
    payload = operation.get("home_inventory")
    if not isinstance(payload, str):
        adapter.fail("Home install transaction requires its pinned inventory")
    raw = payload.encode()
    rows = inventory_rows(raw)
    pin = json.loads(adapter.PIN.read_text(encoding="utf-8"))
    expected_files = {path: row["sha256"] for path, row in rows.items() if selected(path)}
    expected_files["PACKAGE-INTEGRITY.json"] = pin["files"]["PACKAGE-INTEGRITY.json"]
    for write in prepared:
        path = write.relative_path
        if path == STATE:
            try:
                state = json.loads(write.content)
            except ValueError:
                adapter.fail("invalid installation state")
            if (state.get("schema") != SCHEMA or state.get("commit") != pin["commit"]
                    or state.get("status") not in ("installing", "installed")
                    or state.get("files") != expected_files or state.get("scope") != "headless-capabilities"):
                adapter.fail("invalid installation state")
            continue
        expected = pin["files"]["PACKAGE-INTEGRITY.json"] if path == "PACKAGE-INTEGRITY.json" else rows.get(path, {}).get("sha256")
        if write.content_sha256 != expected:
            adapter.fail("Home install content does not match qualified inventory")


def installation_status(root: Path) -> str:
    payload = read_vault_regular(root, STATE)
    if payload is None:
        return "external-installation"
    try:
        state = json.loads(payload)
    except (ValueError, TypeError):
        return "invalid-installation"
    if not isinstance(state, dict) or state.get("schema") != SCHEMA or state.get("status") != "installed":
        return "incomplete-installation"
    pin = json.loads(adapter.PIN.read_text(encoding="utf-8"))
    inventory = read_vault_regular(root, "PACKAGE-INTEGRITY.json")
    if inventory is None:
        return "invalid-installation"
    rows = inventory_rows(inventory)
    expected = {path: row["sha256"] for path, row in rows.items() if selected(path)}
    expected["PACKAGE-INTEGRITY.json"] = pin["files"]["PACKAGE-INTEGRITY.json"]
    if state.get("commit") != pin["commit"] or state.get("files") != expected:
        return "invalid-installation"
    for path, digest in expected.items():
        actual = read_vault_regular(root, path)
        if actual is None or (mutable_seed(path) and not valid_seed(actual)) or (not mutable_seed(path) and sha256_bytes(actual) != digest):
            return "changed-installation"
    return "headless-verified"


def install_plan(root: Path, source: str | Path, operation_id: str, generated_at: str) -> tuple[dict, list[dict], dict]:
    source_root = adapter.safe_root(source)
    if source_root == root.resolve():
        adapter.fail("materialization source must be separate from its target")
    report = adapter.inspect_home(source_root)
    if report["mismatches"]:
        adapter.fail("Home source core is not qualified")
    inventory = adapter.read(source_root, "PACKAGE-INTEGRITY.json").replace(b"\r\n", b"\n")
    rows = inventory_rows(inventory)
    contents = {"PACKAGE-INTEGRITY.json": inventory}
    excluded = []
    for path, row in rows.items():
        if not selected(path):
            excluded.append(path)
            continue
        payload = adapter.read(source_root, path)
        if sha256_bytes(payload) != row["sha256"]:
            normalized = payload.replace(b"\r\n", b"\n")
            if sha256_bytes(normalized) != row["sha256"]:
                adapter.fail(f"qualified source file was modified: {path}")
            payload = normalized
        try:
            payload.decode("utf-8")
        except UnicodeError:
            adapter.fail(f"selected Home content is not UTF-8: {path}")
        contents[path] = payload
    pending, observed = {}, {}
    verify_hashes = {}
    for path, payload in sorted(contents.items()):
        old = read_vault_regular(root, path)
        if old is None:
            pending[path] = payload
            verify_hashes[path] = sha256_bytes(payload)
        elif old == payload or (mutable_seed(path) and valid_seed(old)):
            observed[path] = sha256_bytes(old)
            verify_hashes[path] = sha256_bytes(old)
        else:
            adapter.fail(f"destination differs; no overwrite or migration is implicit: {path}")
    files = {path: sha256_bytes(payload) for path, payload in sorted(contents.items())}
    state = {"schema": SCHEMA, "status": "installing", "commit": report["commit"],
             "release": report["release"], "scope": "headless-capabilities", "files": files,
             "mutable_seed_files": sorted(p for p in files if mutable_seed(p)),
             "excluded_files": len(excluded), "desktop_plugins_activated": False}
    old_state = read_vault_regular(root, STATE)
    if old_state is not None:
        try:
            prior = json.loads(old_state)
        except (ValueError, TypeError):
            adapter.fail("invalid existing installation state")
        if {k: v for k, v in prior.items() if k != "status"} != {k: v for k, v in state.items() if k != "status"}:
            adapter.fail("existing installation belongs to another release/scope; explicit upgrade required")
    batches = []
    if pending or old_state is None or json.loads(old_state).get("status") != "installed":
        initial = profile.bundle(root, {STATE: profile.encoded(state)}, "home-install", operation_id + "-start", generated_at,
                                 {STATE: old_state} if old_state else {})
        if initial["writes"]:
            initial["home_inventory"] = inventory.decode()
            batches.append(initial)
        items = list(pending.items())
        for index in range(0, len(items), BATCH_SIZE):
            content = dict(items[index:index + BATCH_SIZE])
            # Content-derived suffix makes a resumed remaining batch distinct
            # from a previous terminal transaction with the same user operation ID.
            identity = sha256_bytes(profile.encoded({p: sha256_bytes(b) for p, b in content.items()}))[:16]
            part = profile.bundle(root, content, "home-install", operation_id + "-" + identity, generated_at)
            part["home_inventory"] = inventory.decode()
            batches.append(part)
    completion = {**state, "status": "installed"}
    plan = {"schema": "legends.home-install-plan/v1", "scope": "headless-capabilities",
            "source": str(source_root), "target": str(root.resolve()), "release": report["release"],
            "commit": report["commit"], "operation_id": operation_id, "generated_at": generated_at,
            "selected_files": len(files), "create_files": len(pending), "reuse_files": len(observed),
            "excluded_files": len(excluded), "batches": len(batches),
            "file_hashes": files, "source_files_copied_from_inventory_only": True,
            "verification_hashes": verify_hashes,
            "prior_state_sha256": sha256_bytes(old_state) if old_state is not None else None,
            "completion_pending": bool(batches), "writes_per_batch_limit": BATCH_SIZE}
    plan["approved_plan_sha256"] = sha256_bytes(profile.encoded(plan))
    return plan, batches, {"state": completion, "inventory": inventory.decode()}


def apply_install(root: Path, plan: dict, batches: list[dict], completion: dict) -> dict:
    completed = []
    for operation in batches:
        approval = inspect_bundle(root, operation)["approval_sha256"]
        result = apply_bundle(root, operation, approved_plan_sha256=approval)
        completed.append(result.get("operation_id", operation["operation_id"]))
    for path, expected in plan["verification_hashes"].items():
        if sha256_bytes(adapter.read(root, path)) != expected:
            adapter.fail("installed bytes changed before completion; installation remains incomplete")
    old = read_vault_regular(root, STATE)
    finish = profile.bundle(root, {STATE: profile.encoded(completion["state"])}, "home-install",
                            plan["operation_id"] + "-finish", plan["generated_at"], {STATE: old} if old else {})
    finish["home_inventory"] = completion["inventory"]
    finish["read_preconditions"].update(plan["verification_hashes"])
    if finish["writes"]:
        approval = inspect_bundle(root, finish)["approval_sha256"]
        apply_bundle(root, finish, approved_plan_sha256=approval)
    return {"schema": SCHEMA, "status": "installed", "scope": "headless-capabilities",
            "verified_files": len(plan["file_hashes"]), "completed_batches": len(completed),
            "desktop_integration": "not_installed", "instruction_composition": "separate_required_step",
            "native_project_readiness": "not_checked"}
