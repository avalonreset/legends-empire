"""Optional reference-only adapter; Home remains a separately obtained native vault.

No Home code or customer records are copied. A compatibility fingerprint is not
an entitlement, tenancy boundary, or proof that a particular project is ready.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

from .paths import is_name_surrogate
from .transaction import TransactionValidationError, read_vault_regular, sha256_bytes

PIN = Path(__file__).resolve().parents[1] / "config/home-compatibility.json"
SCHEMA = "legends.home-binding/v1"


def fail(message: str):
    raise TransactionValidationError("HOME_ADAPTER", message)


def safe_root(value: str | Path) -> Path:
    if any(ord(c) < 32 for c in str(value)):
        fail("Home root contains control characters")
    path = Path(value).expanduser().absolute()
    for part in (path, *path.parents):
        if part.exists() and is_name_surrogate(part.lstat()):
            fail("Home paths must not traverse a symlink or junction")
    if not path.is_dir():
        fail("Home root must be an existing directory")
    return path.resolve()


def relative(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or "\\" in value or ":" in value or path.is_absolute()
            or str(path) != value or any(p in (".", "..") for p in path.parts)
            or any(ord(c) < 32 for c in value)):
        fail("project must be a canonical relative POSIX path")
    if not value.startswith("Projects/") or path.name != "context.md":
        fail("project must be Home's native Projects/.../context.md")
    return value


def read(root: Path, path: str) -> bytes:
    payload = read_vault_regular(root, path)
    if payload is None:
        fail(f"required Home file missing: {path}")
    return payload


def inspect_home(home: str | Path, project: str | None = None) -> dict:
    root = safe_root(home)
    pin = json.loads(PIN.read_text(encoding="utf-8"))
    mismatches = []
    composition = False
    for path, expected in pin["files"].items():
        payload = read_vault_regular(root, path)
        actual = None if payload is None else sha256_bytes(payload.replace(b"\r\n", b"\n"))
        if actual != expected:
            if path == "AGENTS.md":
                from .home_profile import composition_valid
                if composition_valid(root, pin):
                    composition = True
                    continue
            mismatches.append(path)
    context_hash = None
    if project is not None:
        context_hash = sha256_bytes(read(root, relative(project)))
    from .home_install import installation_status
    installation = installation_status(root)
    if installation not in ("external-installation", "headless-verified"):
        mismatches.append("managed-installation-state")
    return {"schema": "legends.home-inspection/v1", "home_root": str(root),
            "release": pin["release"], "commit": pin["commit"],
            "compatibility": "pinned-core" if not mismatches else "unverified",
            "mismatches": mismatches, "project": project,
            "instruction_mode": "managed-composition" if composition else "native",
            "installation": installation,
            "context_sha256": context_hash, "native_readiness": "not_checked",
            "writes": 0, "source_copied": False,
            "scope": "Pinned contract, script and inventory bytes only; not whole-vault integrity or project readiness."}


def binding_paths(binding_id: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", binding_id):
        fail("binding id must be lowercase letters/digits/hyphens, starting with a letter")
    base = f"wiki/integrations/aimh-home/{binding_id}"
    return f"{base}/binding.json", f"{base}/README.md"


def attachment_bundle(empire: Path, home: str | Path, project: str | None, binding_id: str,
                      operation_id: str, generated_at: str, shared_root: bool = False) -> dict:
    report = inspect_home(home, project)
    source = Path(report["home_root"])
    if source == empire.resolve():
        if not shared_root or report["instruction_mode"] != "managed-composition":
            fail("same-root binding requires explicit --shared-root and verified managed instruction composition")
    elif empire.resolve().is_relative_to(source):
        fail("Home must be a distinct root, nested beneath Empire or external, never Empire itself or its ancestor")
    elif shared_root:
        fail("--shared-root requires Home and Empire to be the same canonical root")
    if report["mismatches"]:
        fail("Home release/core differs from the qualified pin; inspect before qualifying an update")
    state_path, route_path = binding_paths(binding_id)
    state = {"schema": SCHEMA, "binding_id": binding_id, "enabled": True,
             "root_mode": "shared" if shared_root else "separate",
             "empire_root": str(empire.resolve()), "home_root": str(source),
             "project": project, "release": report["release"], "commit": report["commit"],
             "ownership": "Native Home-compatible business records are canonical; Empire owns entry and routing."}
    state_bytes = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
    route = (f"# Optional Home workspace: {binding_id}\n\n"
             "Start every session from the Empire root. This binding points to a separately installed Home vault; "
             "it does not copy marketing methods, business facts or tasks.\n\n"
             f"Canonical Home root: `{source}`\n\nCanonical project: `{project or 'not selected'}`\n\n"
             "Run `home-adapter check --vault <Empire> --binding-id " + binding_id + "` before routing. "
             "Use `--native` for Home's pinned, read-only context resolver. A binding is not project readiness.\n\n"
             "Read Home's AGENTS.md and selected context for Home work. Home's Secretary entry, "
             "recording requirements and disabled native role dispatch remain scoped to Home procedures. "
             "Empire remains the launch and orchestration location. Do not claim Home dispatched agents. "
             "Surface conflicting instructions; this mapping grants no additional authority.\n\n"
             "Keep Home's Projects/, AI Team/ and wiki-link layout intact. Do not duplicate facts or tasks "
             "in Empire, overwrite root instructions, or treat paths as access control. "
             "Keep secrets outside both vaults. This reference does not grant a Home license.\n")
    writes, expected, observed = [], {}, {}
    for path, payload in ((state_path, state_bytes), (route_path, route.encode())):
        old = read_vault_regular(empire, path)
        if old is None:
            writes.append({"path": path, "mode": "create", "content": payload.decode()})
            expected[path] = None
        elif path == state_path and old != payload:
            fail("binding already exists with different content; preserve it and use a new binding id")
        else:
            # User-owned route note may be annotated; never overwrite it.
            observed[path] = sha256_bytes(old)
    return {"schema": "claude-empire.transaction.v1", "operation_id": operation_id,
            "operation_type": "generic", "generated_at": generated_at,
            "expected_hashes": expected, "read_preconditions": observed, "writes": writes}


def detach_bundle(empire: Path, binding_id: str, operation_id: str, generated_at: str) -> dict:
    """Disable routing without reading, modifying or requiring the Home installation."""
    state_path, _ = binding_paths(binding_id)
    old = read(empire, state_path)
    try:
        state = json.loads(old)
    except (ValueError, TypeError):
        fail("invalid binding")
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or state.get("binding_id") != binding_id or state.get("empire_root") != str(empire.resolve())
            or not isinstance(state.get("enabled"), bool)):
        fail("binding identity/root mismatch")
    changed = state["enabled"]
    state["enabled"] = False
    return {"schema": "claude-empire.transaction.v1", "operation_id": operation_id,
            "operation_type": "generic", "generated_at": generated_at,
            "expected_hashes": {state_path: sha256_bytes(old)} if changed else {},
            "read_preconditions": {} if changed else {state_path: sha256_bytes(old)},
            "writes": [{"path": state_path, "mode": "replace", "content": json.dumps(state, sort_keys=True, indent=2) + "\n"}] if changed else []}


def list_bindings(empire: Path) -> dict:
    """Bounded discovery of adapter-owned metadata, never scans customer records."""
    folder = empire / "wiki/integrations/aimh-home"
    rows = []
    if folder.exists():
        safe_root(folder)
        for child in folder.iterdir():
            if len(rows) >= 128:
                fail("binding discovery exceeds 128 entries; narrow the workspace")
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", child.name):
                continue
            path, _ = binding_paths(child.name)
            payload = read_vault_regular(empire, path)
            if payload is None:
                continue
            try:
                state = json.loads(payload)
            except (ValueError, TypeError):
                fail("invalid binding during discovery")
            if (not isinstance(state, dict) or state.get("schema") != SCHEMA
                    or state.get("binding_id") != child.name or not isinstance(state.get("enabled"), bool)):
                fail("invalid binding during discovery")
            rows.append({"binding_id": child.name, "enabled": state["enabled"],
                         "check_required": True})
    return {"schema": "legends.home-bindings/v1", "bindings": sorted(rows, key=lambda r: r["binding_id"]),
            "writes": 0, "home_required": False}


def check_binding(empire: Path, binding_id: str, native: bool = False, project: str | None = None,
                  native_python: str | None = None) -> dict:
    state_path, route_path = binding_paths(binding_id)
    try:
        state = json.loads(read(empire, state_path))
    except (ValueError, TypeError) as exc:
        fail(f"invalid binding: {exc}")
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or state.get("binding_id") != binding_id or state.get("empire_root") != str(empire.resolve())
            or not isinstance(state.get("home_root"), str)
            or "project" not in state or (state["project"] is not None and not isinstance(state["project"], str))
            or not isinstance(state.get("enabled"), bool)):
        fail("binding identity/root mismatch; explicit rebinding is required after relocation")
    if not state["enabled"]:
        return {"schema": SCHEMA, "binding_id": binding_id, "compatibility": "detached",
                "native_readiness": "not_checked", "writes": 0, "session_root": str(empire.resolve())}
    read(empire, route_path)
    selected_project = project if project is not None else state["project"]
    report = inspect_home(state["home_root"], selected_project)
    if Path(report["home_root"]) == empire.resolve() and (
            state.get("root_mode") != "shared" or report["instruction_mode"] != "managed-composition"):
        fail("shared-root binding requires intact managed instruction composition")
    if report["commit"] != state.get("commit") or report["release"] != state.get("release"):
        fail("binding pin differs from installed adapter; requalify explicitly")
    report["binding_id"] = binding_id
    report["session_root"] = str(empire.resolve())
    if selected_project is None:
        report["native_readiness"] = "not_selected"
        return report
    if native:
        if report["mismatches"]:
            fail("refusing to execute unqualified Home scripts")
        root = Path(report["home_root"])
        interpreter = Path(native_python) if native_python else Path(sys.executable)
        if not interpreter.is_absolute() or not interpreter.is_file():
            fail("--native-python must name an existing absolute trusted Python interpreter")
        interpreter = interpreter.resolve()
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        # Only explicit native check executes the separately installed, pinned resolver.
        # Do not import Home in the Empire process or include private note contents.
        try:
            # Fresh private staging avoids unpinned imports/bytecode in the Home
            # Scripts directory. Recheck the exact bytes being executed.
            pin = json.loads(PIN.read_text(encoding="utf-8"))
            with tempfile.TemporaryDirectory(prefix="empire-home-check-") as temp:
                stage = Path(temp)
                dependency = subprocess.run([str(interpreter), "-B", "-E", "-s", "-c", "import yaml"],
                                            cwd=stage, env=env, capture_output=True, timeout=15)
                if dependency.returncode:
                    report["native_readiness"] = "dependency_missing"
                    report["native_requirement"] = "Use --native-python with a trusted Home Python environment containing PyYAML. Standalone Empire does not require it."
                    return report
                for path, digest in pin["files"].items():
                    if path.endswith(".py"):
                        payload = read(root, path).replace(b"\r\n", b"\n")
                        if sha256_bytes(payload) != digest:
                            fail("Home script changed during native check")
                        (stage / Path(path).name).write_bytes(payload)
                command = [str(interpreter), "-B", "-E", "-s", str(stage / "resolve-context.py"),
                           "--vault", str(root), "--project", selected_project]
                result = subprocess.run(command, cwd=stage, env=env, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            fail("native resolver unavailable or timed out; no readiness claim")
        report["native_readiness"] = "ready" if result.returncode == 0 else "refused"
        report["native_exit_code"] = result.returncode
        report["native_execution"] = "temporary verified scripts; zero vault writes"
        # Native stdout can contain customer facts. Return status and a receipt hash only.
        report["native_result_sha256"] = sha256_bytes(result.stdout + b"\0" + result.stderr)
    return report
