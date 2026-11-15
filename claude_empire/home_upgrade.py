"""Reviewed shared-root release updates; never migrate customer records.

Release qualification and recoverable transactions are existing Empire
boundaries. This module adds ownership-aware replacement and open-work checks.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from . import home_adapter as adapter, home_install as installer, home_profile as profile
from .transaction import read_vault_regular, sha256_bytes, inspect_bundle, apply_bundle


def write_allowed(path: str, mode: str) -> bool:
    return mode in ("create", "replace") and (
        installer.selected(path) or path == "PACKAGE-INTEGRITY.json"
        or path in {installer.STATE, profile.HOME_CONTRACT, profile.COMPOSITION}
        or re.fullmatch(r"wiki/integrations/aimh-home/[a-z][a-z0-9-]{0,63}/binding\.json", path) is not None)


def _files(rows: dict, pin: dict, selector=installer.selected) -> dict:
    return {**{p: row["sha256"] for p, row in rows.items() if selector(p)},
            "PACKAGE-INTEGRITY.json": pin["files"]["PACKAGE-INTEGRITY.json"]}


def _states(old: dict, pin: dict, rows: dict) -> tuple[dict, dict]:
    marker = {**old, "status": "upgrading", "upgrade": {"commit": pin["commit"]}}
    new = {"schema": installer.SCHEMA, "status": "installed", "selection_profile": 2,
           "commit": pin["commit"], "release": pin["release"], "scope": "headless-capabilities",
           "files": _files(rows, pin), "mutable_seed_files": ["AI Team/Sessions/handoff.md"],
           "excluded_files": sum(not installer.selected(p) for p in rows),
           "desktop_plugins_activated": False}
    return marker, new


def _evidence(operation: dict):
    pin = adapter.qualified_pin(operation.get("home_pin_commit"))
    previous = adapter.qualified_pin(operation.get("home_previous_commit"))
    new_rows = installer.inventory_rows(operation["home_inventory"].encode(), pin)
    old_rows = installer.inventory_rows(operation["home_previous_inventory"].encode(), previous)
    old = operation["home_previous_state"]
    selector = installer.selected if old.get("selection_profile") == 2 else installer.legacy_selected
    if (old.get("schema") != installer.SCHEMA or old.get("status") != "installed"
            or old.get("commit") != previous["commit"] or old.get("release") != previous["release"]
            or old.get("files") != _files(old_rows, previous, selector)):
        adapter.fail("previous managed ownership does not match its qualified release")
    return pin, previous, new_rows, old_rows, old


def finish_slices(metadata: dict, size: int | None = None) -> list[dict]:
    """Split finish metadata into bounded transactions; the state marker goes last."""
    size = size or installer.BATCH_SIZE
    others = [p for p in metadata if p != installer.STATE]
    chunks = [{p: metadata[p] for p in others[i:i + size]} for i in range(0, len(others), size)] or [{}]
    if installer.STATE in metadata:
        if len(chunks[-1]) >= size:
            chunks.append({})
        chunks[-1][installer.STATE] = metadata[installer.STATE]
    return [c for c in chunks if c]


def validate_prepared(prepared, operation: dict) -> None:
    try:
        pin, previous, rows, old_rows, old = _evidence(operation)
        marker, final = _states(old, pin, rows)
        expected = _files(rows, pin)
        for write in prepared:
            path = write.relative_path
            if path == installer.STATE:
                if json.loads(write.content) not in (marker, final):
                    adapter.fail("invalid upgrade state")
            elif path == profile.HOME_CONTRACT:
                if write.content_sha256 != pin["files"]["AGENTS.md"]:
                    adapter.fail("upgraded Home contract is not qualified")
            elif path == profile.COMPOSITION:
                composition = json.loads(write.content)
                prior = json.loads(operation["home_previous_composition"])
                wanted = {**prior, "commit": pin["commit"], "release": pin["release"],
                          "home_agents_sha256": pin["files"]["AGENTS.md"]}
                if composition != wanted:
                    adapter.fail("upgrade must preserve the owner and generated Empire contracts")
            elif path.endswith("/binding.json"):
                before = operation["home_previous_bindings"][path]
                wanted = {**json.loads(before), "commit": pin["commit"], "release": pin["release"]}
                if json.loads(write.content) != wanted:
                    adapter.fail("upgrade may change only a binding's release pin")
                if operation["expected_hashes"].get(path) != sha256_bytes(before.encode()):
                    adapter.fail("binding replacement must guard its original bytes")
            else:
                if installer.mutable_seed(path) or write.content_sha256 != expected.get(path):
                    adapter.fail("upgrade content is outside the immutable qualified release")
                prior_hash = operation["expected_hashes"].get(path)
                if prior_hash is not None and prior_hash != old["files"].get(path):
                    adapter.fail("upgrade may replace only unchanged previously owned release files")
    except (KeyError, TypeError, ValueError) as exc:
        adapter.fail(f"invalid upgrade evidence: {exc}")


def _open_sessions(root: Path, owned: dict) -> list[dict]:
    """Scan nested records, not only the immediate records directory.

    This is a conservative upgrade guard, not a replacement YAML validator.
    Unknown/damaged member records refuse rather than assuming they are closed.
    """
    folder = root / "AI Team/Sessions/records"
    conflicts = []
    paths = sorted(folder.rglob("*.md")) if folder.exists() else []
    if len(paths) > 10000:
        adapter.fail("session inventory exceeds the upgrade guard limit")
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if relative in owned:
            continue
        try:
            text = adapter.read(root, relative).decode("utf-8")
        except UnicodeDecodeError:
            conflicts.append({"path": relative, "reason": "open-or-unrecognized-session",
                              "next": "Finish or preserve a governed handoff and close with native Home tools before upgrading."})
            continue
        front = text.split("---", 2)
        def field(name):
            match = re.search(rf"(?im)^{name}:\s*['\"]?([a-z-]+)['\"]?\s*$", front[1]) if len(front) == 3 else None
            return match.group(1).lower() if match else None
        if field("type") != "session" or field("status") != "closed":
            conflicts.append({"path": relative, "reason": "open-or-unrecognized-session",
                              "next": "Finish or preserve a governed handoff and close with native Home tools before upgrading."})
    return conflicts


def _session_hashes(root: Path) -> dict:
    folder = root / "AI Team/Sessions/records"
    if not folder.exists():
        return {}
    paths = sorted(folder.rglob("*.md"))
    if len(paths) > 10000:
        adapter.fail("session inventory exceeds the upgrade guard limit")
    return {p.relative_to(root).as_posix(): sha256_bytes(adapter.read(root, p.relative_to(root).as_posix()))
            for p in paths}


def upgrade_plan(root: Path, source: str | Path, operation_id: str, generated_at: str,
                 backup_root: str | Path | None = None) -> tuple[dict, list[dict], dict]:
    root = adapter.safe_root(root)
    source = adapter.safe_root(source)
    if source == root:
        adapter.fail("upgrade source must be separate from the selected Empire root")
    report = adapter.inspect_home(source)
    if report["mismatches"]:
        adapter.fail("upgrade source is not a qualified pristine Home release")
    pin = adapter.qualified_pin(report["commit"])
    old_raw = adapter.read(root, installer.STATE)
    old = json.loads(old_raw)
    resuming = old.get("status") == "upgrading"
    if resuming:
        if old.get("upgrade") != {"commit": pin["commit"]}:
            adapter.fail("incomplete upgrade belongs to another release; recover it first")
        old = {k: v for k, v in old.items() if k != "upgrade"}
        old["status"] = "installed"
    elif old.get("status") != "installed":
        adapter.fail("upgrade requires a completed managed installation or its matching interrupted upgrade")
    previous = adapter.qualified_pin(old.get("commit"))
    # The old inventory can already have been replaced in a completed batch.
    backup = adapter.safe_root(backup_root) if backup_root is not None else None
    if backup is not None and (backup.is_relative_to(root) or root.is_relative_to(backup)):
        adapter.fail("upgrade backup must be outside the Empire vault, not its ancestor")
    prior_inventory = adapter.read(backup if resuming and backup else root, "PACKAGE-INTEGRITY.json")
    inventory = adapter.read(source, "PACKAGE-INTEGRITY.json").replace(b"\r\n", b"\n")
    evidence = {"home_pin_commit": pin["commit"], "home_previous_commit": previous["commit"],
                "home_inventory": inventory.decode(), "home_previous_inventory": prior_inventory.decode(),
                "home_previous_state": old}
    _, _, rows, old_rows, _ = _evidence(evidence)
    if old["commit"] == pin["commit"] and old.get("selection_profile") == 2 and not resuming:
        if adapter.inspect_home(root)["mismatches"]:
            adapter.fail("already-installed release has changed; preserve edits and inspect")
        return {"status": "noop", "release": pin["release"], "changed_paths": []}, [], {}
    if not profile.composition_valid(root, previous):
        adapter.fail("upgrade requires intact shared-root composition; preserve owner instructions and resolve drift")
    prior_composition = adapter.read(root, profile.COMPOSITION)
    evidence["home_previous_composition"] = prior_composition.decode()
    evidence["home_previous_bindings"] = {}
    guard = {p: sha256_bytes(adapter.read(root, p)) for p in
             ("AGENTS.md", profile.OWNER_CONTRACT, profile.HOME_CONTRACT, profile.COMPOSITION)}
    conflicts = _open_sessions(root, old["files"])
    contents, observed, replacements = {}, {}, {}
    for path, row in sorted(rows.items()):
        if not installer.selected(path):
            continue
        payload = adapter.read(source, path)
        if sha256_bytes(payload) != row["sha256"]:
            payload = payload.replace(b"\r\n", b"\n")
        if sha256_bytes(payload) != row["sha256"]:
            adapter.fail(f"modified selected release source: {path}")
        contents[path] = payload
    contents["PACKAGE-INTEGRITY.json"] = inventory
    pending = {}
    for path, payload in contents.items():
        actual = read_vault_regular(root, path)
        if installer.mutable_seed(path):
            if actual is None or not installer.valid_seed(actual):
                conflicts.append({"path": path, "reason": "invalid-member-handoff"})
            else:
                observed[path] = sha256_bytes(actual)
        elif actual == payload:
            observed[path] = sha256_bytes(actual)
        elif actual is None:
            pending[path] = payload
        elif sha256_bytes(actual) == old["files"].get(path):
            pending[path] = payload
            replacements[path] = actual
        else:
            conflicts.append({"path": path, "reason": "member-edit-or-unowned-collision"})
    # Removed upstream files remain owner-visible, never silently deleted.
    retired = sorted(set(old["files"]) - set(contents))
    for path in retired:
        payload = read_vault_regular(root, path)
        if payload is not None:
            observed[path] = sha256_bytes(payload)
    marker, final = _states(old, pin, rows)
    contract = adapter.read(source, "AGENTS.md").replace(b"\r\n", b"\n")
    composition = {**json.loads(prior_composition), "commit": pin["commit"], "release": pin["release"],
                   "home_agents_sha256": sha256_bytes(contract)}
    metadata = {profile.HOME_CONTRACT: contract, profile.COMPOSITION: profile.encoded(composition),
                installer.STATE: profile.encoded(final)}
    for binding in adapter.list_bindings(root)["bindings"]:
        path, _ = adapter.binding_paths(binding["binding_id"])
        raw = adapter.read(root, path)
        state = json.loads(raw)
        if state.get("root_mode") != "shared":
            continue
        if state.get("empire_root") != str(root) or state.get("home_root") != str(root):
            conflicts.append({"path": path, "reason": "relocated-binding-needs-explicit-rebind"})
            continue
        evidence["home_previous_bindings"][path] = raw.decode()
        metadata[path] = profile.encoded({**state, "commit": pin["commit"], "release": pin["release"]})
        guard[path] = sha256_bytes(raw)
    # Require real rollback copies for every replacement, not merely a checkbox.
    backup_errors, backup_hashes = [], {}
    for path in {*replacements, *metadata}:
        actual = read_vault_regular(root, path)
        if actual is not None:
            saved = read_vault_regular(backup, path) if backup else None
            expected = old_raw if path == installer.STATE else actual
            if resuming and path == installer.STATE:
                expected = profile.encoded(old)
            if saved is None or (saved != expected and not (
                    resuming and path == installer.STATE and json.loads(saved) == old)):
                backup_errors.append(path)
            elif saved is not None:
                backup_hashes[path] = sha256_bytes(saved)
    plan = {"schema": "legends.home-upgrade-plan/v1", "status": "blocked" if conflicts else "ready",
            "target": str(root), "source": str(source), "backup": str(backup) if backup else None,
            "from_release": previous["release"], "release": pin["release"], "commit": pin["commit"],
            "operation_id": operation_id, "generated_at": generated_at, "resuming": resuming,
            "conflicts": conflicts, "backup_missing_or_different": sorted(backup_errors),
            "changed_paths": sorted(set(pending) | set(metadata)), "retired_files_preserved": retired,
            "preserved_handoff": True, "desktop_plugins_activated": False,
            "file_hashes": {p: sha256_bytes(b) for p, b in contents.items()},
            "observed_hashes": observed, "guard_hashes": guard,
            "session_hashes": _session_hashes(root), "backup_hashes": backup_hashes,
            "prior_state_sha256": sha256_bytes(old_raw)}
    plan["approved_plan_sha256"] = sha256_bytes(profile.encoded(plan))
    batches = []
    def operation(content, identity):
        op = profile.bundle(root, {}, "home-upgrade", identity, generated_at)
        for path, payload in content.items():
            old_content = read_vault_regular(root, path)
            if old_content == payload:
                op["read_preconditions"][path] = sha256_bytes(payload)
                continue
            try:
                value = {"content": payload.decode()}
            except UnicodeError:
                value = {"content_file": str(source / path), "sha256": sha256_bytes(payload)}
            op["writes"].append({"path": path, "mode": "replace" if old_content is not None else "create", **value})
            op["expected_hashes"][path] = sha256_bytes(old_content) if old_content is not None else None
        op.update(evidence)
        return op
    start = operation({installer.STATE: profile.encoded(marker)}, operation_id + "-start")
    if start["writes"]:
        batches.append(start)
    items = list(pending.items())
    for offset in range(0, len(items), installer.BATCH_SIZE):
        part = dict(items[offset:offset + installer.BATCH_SIZE])
        identity = sha256_bytes(profile.encoded({p: sha256_bytes(b) for p, b in part.items()}))[:16]
        batches.append(operation(part, operation_id + "-" + identity))
    completion = {"metadata": metadata, "evidence": evidence, "marker": profile.encoded(marker),
                  "verification": {**plan["file_hashes"], **observed}}
    return plan, batches, completion


def apply_upgrade(root: Path, plan: dict, batches: list[dict], completion: dict) -> dict:
    if plan["status"] == "noop":
        return plan
    if plan["conflicts"] or plan["backup_missing_or_different"]:
        adapter.fail("resolve upgrade conflicts and verify rollback copies before applying")
    backup = adapter.safe_root(plan["backup"])
    for path, expected in plan["backup_hashes"].items():
        if sha256_bytes(adapter.read(backup, path)) != expected:
            adapter.fail(f"rollback copy changed after review: {path}")
    if _session_hashes(root) != plan["session_hashes"]:
        adapter.fail("native sessions changed after upgrade review; review a fresh plan")
    for path, expected in {**plan["observed_hashes"], **plan["guard_hashes"]}.items():
        if sha256_bytes(adapter.read(root, path)) != expected:
            adapter.fail(f"upgrade input changed after review: {path}")
    # Preflight every content batch before setting the incomplete marker.
    # The transaction engine repeats these guards during each actual apply.
    for operation in batches:
        inspect_bundle(root, operation)
    for operation in batches:
        apply_bundle(root, operation, approved_plan_sha256=inspect_bundle(root, operation)["approval_sha256"])
    for path, expected in completion["verification"].items():
        if sha256_bytes(adapter.read(root, path)) != expected:
            adapter.fail(f"upgrade verification failed; installation remains incomplete: {path}")
    # Guard original metadata, never adopt a concurrent edit as the new baseline.
    prior = {}
    for path in completion["metadata"]:
        payload = adapter.read(root, path)
        expected = (sha256_bytes(completion["marker"]) if path == installer.STATE
                    else plan["guard_hashes"][path])
        if sha256_bytes(payload) != expected:
            adapter.fail(f"upgrade metadata changed; installation remains incomplete: {path}")
        prior[path] = payload
    # Shipped record documentation may have changed; compare member records only.
    owned = completion["evidence"]["home_previous_state"]["files"]
    before = {p: h for p, h in plan["session_hashes"].items() if p not in owned}
    after = {p: h for p, h in _session_hashes(root).items()
             if p not in owned and p not in plan["file_hashes"]}
    if before != after:
        adapter.fail("member sessions changed during upgrade; installation remains incomplete")
    for index, chunk in enumerate(finish_slices(completion["metadata"])):
        suffix = "-finish" if index == 0 else f"-finish-{index + 1}"
        finish = profile.bundle(root, chunk, "home-upgrade", plan["operation_id"] + suffix,
                                plan["generated_at"], {p: prior[p] for p in chunk})
        finish.update(completion["evidence"])
        finish["read_preconditions"].update(completion["verification"])
        finish["read_preconditions"].update({p: h for p, h in plan["guard_hashes"].items()
                                            if p not in completion["metadata"]})
        apply_bundle(root, finish, approved_plan_sha256=inspect_bundle(root, finish)["approval_sha256"])
    report = adapter.inspect_home(root)
    if report["mismatches"]:
        adapter.fail("upgrade finished but final compatibility check failed")
    return {"status": "upgraded", "release": plan["release"], "verified_files": len(plan["file_hashes"]),
            "retired_files_preserved": len(plan["retired_files_preserved"]),
            "desktop_plugins_activated": False, "native_project_readiness": "not_checked"}
