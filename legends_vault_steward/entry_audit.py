from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import config_hash, load_config
from .law import LawFile, collect_law_files, evaluate_laws
from .models import Finding, SEVERITY_ORDER
from .parse import iter_unfenced_lines, parse_frontmatter, strip_fences
from .paths import (
    MAX_ENTRY_FILE_BYTES,
    ReportPathError,
    VaultRoot,
    _WIN_DEVICE_NAMES,
    _is_contained,
    _is_symlink_or_junction,
    get_home_dir,
    is_disallowed_path_token,
    redact_home_text,
    resolve_vault_root,
    validate_report_dir,
)
from .report import _md_table

MAX_IMPORT_DEPTH = 8
MAX_CHAIN_FILES = 64
IMPORT_LINE_RE = re.compile(r"^@(\S+)")


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def _has_symlink_between(target: Path, root: Path) -> bool:
    cur = Path(os.path.abspath(target))
    stop = Path(os.path.abspath(root))
    while True:
        if _is_symlink_or_junction(cur):
            return True
        if cur == stop or cur.parent == cur:
            break
        cur = cur.parent
    return _is_symlink_or_junction(stop)


def _resolve_configured_path(
    raw: str,
    vault: VaultRoot,
    *,
    reject_env: bool = True,
) -> tuple[Path, str, str, Path | None]:
    """Return (abs_path, display_path, origin, home_scope_dir).

    Raises ValueError if the configured path violates containment or syntax rules.
    """
    s = raw.strip()
    reason = is_disallowed_path_token(s, allow_tilde=True, reject_env=reject_env)
    if reason is not None:
        raise ValueError(f"disallowed path '{redact_home_text(s)}': {reason}")

    if s == "~" or s.startswith("~/") or s.startswith("~\\"):
        home = get_home_dir()
        rel_part = s[2:].replace("\\", "/").strip("/") if len(s) > 1 else ""
        if not rel_part:
            raise ValueError("home root '~' is not a valid file target")
        if ".." in Path(rel_part).parts:
            raise ValueError(f"path traversal is not permitted in home path: {redact_home_text(s)}")
        target = Path(os.path.abspath(home / rel_part))
        if not _is_contained(target, home):
            raise ValueError(f"configured path escapes home directory: {redact_home_text(s)}")
        parts = Path(rel_part).parts
        scope_dir = (home / parts[0]).resolve(strict=False) if len(parts) > 1 else home
        display = f"~/{Path(rel_part).as_posix()}"
        return target, display, "home", scope_dir

    cand = Path(s)
    if cand.is_absolute() or re.match(r"^[A-Za-z]:[/\\]", s):
        target = Path(os.path.abspath(cand))
        if not _is_contained(target, vault.real):
            raise ValueError(f"configured path escapes vault root: {redact_home_text(s)}")
        try:
            rel = target.relative_to(vault.real).as_posix()
        except ValueError:
            rel = target.resolve(strict=False).relative_to(vault.real).as_posix()
        return target, rel, "vault", None

    clean_rel = s.replace("\\", "/")
    if ".." in Path(clean_rel).parts:
        raise ValueError(f"configured path must not contain '..': {redact_home_text(s)}")
    target = Path(os.path.abspath(vault.real / clean_rel))
    if not _is_contained(target, vault.real):
        raise ValueError(f"configured path escapes vault root: {redact_home_text(s)}")
    rel = target.relative_to(vault.real).as_posix()
    return target, rel, "vault", None


def _resolve_import_target(
    token: str,
    importer_abs: Path,
    importer_origin: str,
    importer_home_scope: Path | None,
    vault: VaultRoot,
) -> tuple[Path, str, str, Path | None] | str:
    """Resolve @token relative to importer or vault.

    Returns (abs_path, display_path, origin, home_scope_dir) or an error reason string.
    """
    s = token.strip()
    reason = is_disallowed_path_token(s, allow_tilde=True, reject_env=True)
    if reason is not None:
        return reason

    home = get_home_dir()
    if s == "~" or s.startswith("~/") or s.startswith("~\\"):
        rel_part = s[2:].replace("\\", "/").strip("/")
        if not rel_part or ".." in Path(rel_part).parts:
            return "invalid home-relative import path"
        cand = Path(os.path.abspath(home / rel_part))
    elif Path(s).is_absolute() or re.match(r"^[A-Za-z]:[/\\]", s):
        cand = Path(os.path.abspath(Path(s)))
        if not _is_contained(cand, vault.real):
            return "absolute import resolves outside vault"
    else:
        cand = Path(os.path.abspath(importer_abs.parent / s.replace("\\", "/")))

    # Check containment based on importer origin
    if _is_contained(cand, vault.real):
        if _has_symlink_between(cand, vault.real):
            return "import traverses a symlink or junction"
        rel = cand.resolve(strict=False).relative_to(vault.real).as_posix()
        return cand, rel, "vault", None

    if importer_origin == "home" and importer_home_scope is not None:
        if _is_contained(cand, importer_home_scope):
            if _has_symlink_between(cand, home):
                return "import traverses a symlink or junction"
            rel_home = cand.resolve(strict=False).relative_to(home).as_posix()
            return cand, f"~/{rel_home}", "home", importer_home_scope

    return "import escapes allowed containment boundary"


def _first_c0_control_offset(data: bytes) -> int | None:
    for idx, b in enumerate(data):
        if b < 0x20 and b not in (0x09, 0x0A, 0x0D):
            return idx
    return None


def _read_chain_file(
    abs_path: Path,
    containment_root: Path,
) -> tuple[str, bytes | None, int, int | None]:
    """Read a chain file safely.

    Returns (status, data_bytes, size_bytes, error_offset).
    status: 'ok' | 'missing' | 'oversize' | 'os_error'
    """
    if _has_symlink_between(abs_path, containment_root):
        return "missing", None, 0, None
    try:
        lstat_res = abs_path.lstat()
    except FileNotFoundError:
        return "missing", None, 0, None
    except OSError:
        return "os_error", None, 0, None
    if not stat.S_ISREG(lstat_res.st_mode):
        return "missing", None, 0, None
    if lstat_res.st_size > MAX_ENTRY_FILE_BYTES:
        return "oversize", None, int(lstat_res.st_size), None

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(abs_path, flags)
    except OSError:
        return "missing", None, 0, None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return "missing", None, 0, None
        if st.st_size > MAX_ENTRY_FILE_BYTES:
            return "oversize", None, int(st.st_size), None
        chunks: list[bytes] = []
        rem = MAX_ENTRY_FILE_BYTES + 1
        while rem > 0:
            chunk = os.read(fd, rem)
            if not chunk:
                break
            chunks.append(chunk)
            rem -= len(chunk)
        data = b"".join(chunks)
    except OSError:
        return "os_error", None, 0, None
    finally:
        os.close(fd)
    if len(data) > MAX_ENTRY_FILE_BYTES:
        return "oversize", None, len(data), None
    return "ok", data, len(data), None


def _extract_import_tokens(text: str) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for line_no, line in iter_unfenced_lines(text):
        match = IMPORT_LINE_RE.match(line)
        if not match:
            continue
        token = match.group(1).strip()
        if "@" in token:
            continue
        stem = token.rstrip(" .").split(".", 1)[0].split(":", 1)[0].upper()
        if not any(ch in token for ch in ("/", "\\", ".", ":")) and stem not in _WIN_DEVICE_NAMES:
            continue
        imports.append((line_no, token))
    return imports


def _strip_html_comments_preserving_code(text: str) -> str:
    return re.sub(
        r"(?<!`)(`+)(?!`)[\s\S]*?\1(?!`)|<!--[\s\S]*?(?:-->|\Z)",
        lambda m: m.group(0) if m.group(1) else "\n",
        text,
    )


def _chain_references_core(
    chain_texts: list[str],
    core_display: str,
    core_origin: str,
    vault: VaultRoot,
) -> bool:
    candidates: list[tuple[str, bool]] = [(core_display, core_origin == "vault")]
    if core_origin == "vault":
        abs_roots = {vault.as_posix.rstrip("/")}
        given_str = str(vault.given)
        if vault.given.is_absolute() or re.match(r"^[A-Za-z]:[/\\]", given_str):
            abs_roots.add(Path(os.path.abspath(vault.given)).as_posix().rstrip("/"))
        for root_str in sorted(abs_roots):
            if root_str:
                candidates.append((f"{root_str}/{core_display}", False))
    flags = re.IGNORECASE if os.name == "nt" else 0
    patterns = [
        re.compile(
            r"(?<![A-Za-z0-9_./:~@-])"
            + (r"(?:\./)?" if allow_dot_slash else r"")
            + re.escape(cand)
            + r"(?!\.[A-Za-z0-9_./-]|[A-Za-z0-9_/-])",
            flags,
        )
        for cand, allow_dot_slash in candidates
    ]
    for text in chain_texts:
        _, body = parse_frontmatter(text)
        unfenced = strip_fences(body)
        uncommented = _strip_html_comments_preserving_code(unfenced)
        for line in uncommented.splitlines():
            norm_line = line.replace("\\", "/")
            if any(pat.search(norm_line) for pat in patterns):
                return True
    return False


def entry_audit(
    vault_path: str,
    config_path: str | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
    profile: str = "generic",
) -> dict[str, Any]:
    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    config = load_config(config_path, profile)
    vault = resolve_vault_root(vault_path)
    home_dir = get_home_dir()

    findings: list[Finding] = []
    harnesses_out: list[dict[str, Any]] = []
    law_chain_files: list[LawFile] = []
    seen_law_paths: set[Path] = set()

    harnesses_cfg = config.get("entry_harnesses", [])
    for h_cfg in harnesses_cfg:
        h_id = h_cfg["id"]
        entry_list = h_cfg["entry"]
        core_raw = h_cfg.get("core")
        core_by_ref = bool(h_cfg.get("core_by_reference", False))
        max_chain_bytes = h_cfg.get("max_chain_bytes")
        shim_cfg = h_cfg.get("shim")

        core_abs: Path | None = None
        core_display: str | None = None
        core_origin: str | None = None
        if core_raw:
            core_abs, core_display, core_origin, _ = _resolve_configured_path(core_raw, vault, reject_env=True)

        chain_nodes: list[dict[str, Any]] = []
        harness_chain_texts: list[str] = []
        visited_real: dict[Path, dict[str, Any]] = {}
        active_stack: set[Path] = set()
        cycle_detected = False
        depth_cap_reached = False
        file_cap_reached = False
        max_depth_seen = 0
        total_bytes = 0
        oversize_emitted = False

        def visit_file(
            abs_p: Path,
            disp_p: str,
            origin: str,
            home_scope: Path | None,
            depth: int,
            is_root_entry: bool,
            importer_disp: str | None = None,
            import_line: int | None = None,
            raw_token: str | None = None,
        ) -> None:
            nonlocal cycle_detected, depth_cap_reached, file_cap_reached, max_depth_seen, total_bytes, oversize_emitted
            resolved_p = abs_p.resolve(strict=False)
            if resolved_p in active_stack:
                cycle_detected = True
                return
            if resolved_p in visited_real:
                return
            if len(chain_nodes) >= MAX_CHAIN_FILES:
                file_cap_reached = True
                return

            containment_root = vault.real if origin == "vault" else home_dir
            status, data, size_bytes, _ = _read_chain_file(abs_p, containment_root)
            if status in {"missing", "os_error"}:
                if is_root_entry:
                    findings.append(
                        Finding(
                            code="entry.missing_entry",
                            severity="high",
                            path=disp_p,
                            record_class=None,
                            message=f"Harness '{h_id}' entry file is missing or unreadable: {disp_p}",
                            extra={"harness": h_id},
                        )
                    )
                else:
                    loc = f"{importer_disp}:{import_line}"
                    findings.append(
                        Finding(
                            code="entry.broken_import",
                            severity="high",
                            path=importer_disp,
                            record_class=None,
                            message=f"Broken @-import '{redact_home_text(raw_token or disp_p)}' at {loc}",
                            extra={
                                "harness": h_id,
                                "line": import_line,
                                "target": redact_home_text(raw_token or disp_p),
                                "file_line": loc,
                            },
                        )
                    )
                return

            max_depth_seen = max(max_depth_seen, depth)
            total_bytes += size_bytes

            if status == "oversize" or data is None:
                node = {
                    "path": disp_p,
                    "bytes": size_bytes,
                    "sha256": None,
                    "depth": depth,
                }
                chain_nodes.append(node)
                visited_real[resolved_p] = node
                oversize_emitted = True
                findings.append(
                    Finding(
                        code="budget.entry_chain_exceeded",
                        severity="high",
                        path=disp_p,
                        record_class=None,
                        message=(
                            f"Entry file {disp_p} is {size_bytes} bytes, exceeding the "
                            f"{MAX_ENTRY_FILE_BYTES}-byte per-file limit"
                        ),
                        extra={"harness": h_id, "bytes": size_bytes, "max_bytes": MAX_ENTRY_FILE_BYTES},
                    )
                )
                return

            sha = hashlib.sha256(data).hexdigest()
            node = {
                "path": disp_p,
                "bytes": size_bytes,
                "sha256": sha,
                "depth": depth,
            }
            chain_nodes.append(node)
            visited_real[resolved_p] = node

            # Check encoding (invalid UTF-8 or C0 control bytes other than tab/LF/CR)
            c0_offset = _first_c0_control_offset(data)
            decode_err_offset: int | None = None
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                decode_err_offset = exc.start
                text = data.decode("utf-8", errors="replace")

            if decode_err_offset is not None:
                findings.append(
                    Finding(
                        code="entry.encoding",
                        severity="high",
                        path=disp_p,
                        record_class=None,
                        message=f"Invalid UTF-8 byte sequence in {disp_p} at byte offset {decode_err_offset}",
                        extra={"harness": h_id, "byte_offset": decode_err_offset},
                    )
                )
            elif c0_offset is not None:
                findings.append(
                    Finding(
                        code="entry.encoding",
                        severity="high",
                        path=disp_p,
                        record_class=None,
                        message=(
                            f"Disallowed C0 control byte 0x{data[c0_offset]:02x} in {disp_p} "
                            f"at byte offset {c0_offset}"
                        ),
                        extra={"harness": h_id, "byte_offset": c0_offset},
                    )
                )

            text_clean = text.lstrip("\ufeff")
            if decode_err_offset is None:
                harness_chain_texts.append(text_clean)
            fm, _ = parse_frontmatter(text_clean)
            raw_fu = fm.get("fresh_until")
            if isinstance(raw_fu, list):
                raw_fu = raw_fu[0] if raw_fu else None
            fu_date = _parse_date_val(raw_fu)
            if fu_date is not None and fu_date < started.date():
                findings.append(
                    Finding(
                        code="freshness.expired_fresh_until",
                        severity="high",
                        path=disp_p,
                        record_class=None,
                        message=f"Entry file {disp_p} declared fresh_until {fu_date.isoformat()} has passed",
                        extra={"harness": h_id, "fresh_until": fu_date.isoformat()},
                    )
                )

            if resolved_p not in seen_law_paths and decode_err_offset is None:
                seen_law_paths.add(resolved_p)
                law_chain_files.append(
                    LawFile(
                        display_path=disp_p,
                        abs_path=resolved_p,
                        text=text_clean,
                        in_entry_chain=True,
                    )
                )

            imports = _extract_import_tokens(text_clean)
            if not imports:
                return
            if depth >= MAX_IMPORT_DEPTH:
                depth_cap_reached = True
                return

            active_stack.add(resolved_p)
            try:
                for line_no, token in imports:
                    resolved_imp = _resolve_import_target(token, abs_p, origin, home_scope, vault)
                    if isinstance(resolved_imp, str):
                        loc = f"{disp_p}:{line_no}"
                        findings.append(
                            Finding(
                                code="entry.broken_import",
                                severity="high",
                                path=disp_p,
                                record_class=None,
                                message=(
                                    f"Broken or rejected @-import '{redact_home_text(token)}' "
                                    f"at {loc}: {redact_home_text(resolved_imp)}"
                                ),
                                extra={
                                    "harness": h_id,
                                    "line": line_no,
                                    "target": redact_home_text(token),
                                    "file_line": loc,
                                },
                            )
                        )
                        continue
                    child_abs, child_disp, child_origin, child_scope = resolved_imp
                    visit_file(
                        child_abs,
                        child_disp,
                        child_origin,
                        child_scope,
                        depth + 1,
                        False,
                        importer_disp=disp_p,
                        import_line=line_no,
                        raw_token=token,
                    )
            finally:
                active_stack.discard(resolved_p)

        entry_displays: list[str] = []
        for raw_ep in entry_list:
            ep_abs, ep_disp, ep_origin, ep_scope = _resolve_configured_path(raw_ep, vault, reject_env=True)
            entry_displays.append(ep_disp)
            visit_file(ep_abs, ep_disp, ep_origin, ep_scope, 0, True)

        core_reached = False
        core_sha256: str | None = None
        if core_abs is not None and core_display is not None:
            c_root = vault.real if core_origin == "vault" else home_dir
            core_resolved = core_abs.resolve(strict=False)
            if (
                not _has_symlink_between(core_abs, c_root)
                and core_resolved in visited_real
                and visited_real[core_resolved].get("sha256")
            ):
                core_reached = True
                core_sha256 = visited_real[core_resolved]["sha256"]
            elif (
                core_by_ref
                and core_origin is not None
                and _chain_references_core(harness_chain_texts, core_display, core_origin, vault)
            ):
                c_st, c_data, c_size, _ = _read_chain_file(core_abs, c_root)
                if c_st == "oversize":
                    oversize_emitted = True
                    findings.append(
                        Finding(
                            code="budget.entry_chain_exceeded",
                            severity="high",
                            path=core_display,
                            record_class=None,
                            message=(
                                f"Entry file {core_display} is {c_size} bytes, exceeding the "
                                f"{MAX_ENTRY_FILE_BYTES}-byte per-file limit"
                            ),
                            extra={"harness": h_id, "bytes": c_size, "max_bytes": MAX_ENTRY_FILE_BYTES},
                        )
                    )
                elif c_st == "ok" and c_data is not None:
                    core_reached = True
                    core_sha256 = hashlib.sha256(c_data).hexdigest()
                    c0_off = _first_c0_control_offset(c_data)
                    dec_err_off: int | None = None
                    try:
                        raw_core_text = c_data.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        dec_err_off = exc.start
                        raw_core_text = c_data.decode("utf-8", errors="replace")
                    if dec_err_off is not None:
                        findings.append(
                            Finding(
                                code="entry.encoding",
                                severity="high",
                                path=core_display,
                                record_class=None,
                                message=f"Invalid UTF-8 byte sequence in {core_display} at byte offset {dec_err_off}",
                                extra={"harness": h_id, "byte_offset": dec_err_off},
                            )
                        )
                    elif c0_off is not None:
                        findings.append(
                            Finding(
                                code="entry.encoding",
                                severity="high",
                                path=core_display,
                                record_class=None,
                                message=(
                                    f"Disallowed C0 control byte 0x{c_data[c0_off]:02x} in {core_display} "
                                    f"at byte offset {c0_off}"
                                ),
                                extra={"harness": h_id, "byte_offset": c0_off},
                            )
                        )
                    core_text = raw_core_text.lstrip("\ufeff")
                    c_fm, _ = parse_frontmatter(core_text)
                    c_raw_fu = c_fm.get("fresh_until")
                    if isinstance(c_raw_fu, list):
                        c_raw_fu = c_raw_fu[0] if c_raw_fu else None
                    c_fu_date = _parse_date_val(c_raw_fu)
                    if c_fu_date is not None and c_fu_date < started.date():
                        findings.append(
                            Finding(
                                code="freshness.expired_fresh_until",
                                severity="high",
                                path=core_display,
                                record_class=None,
                                message=f"Entry file {core_display} declared fresh_until {c_fu_date.isoformat()} has passed",
                                extra={"harness": h_id, "fresh_until": c_fu_date.isoformat()},
                            )
                        )
                    if dec_err_off is None and core_resolved not in seen_law_paths:
                        seen_law_paths.add(core_resolved)
                        law_chain_files.append(
                            LawFile(
                                display_path=core_display,
                                abs_path=core_resolved,
                                text=core_text,
                                in_entry_chain=True,
                            )
                        )
            if not core_reached:
                findings.append(
                    Finding(
                        code="entry.core_unreached",
                        severity="high",
                        path=core_display,
                        record_class=None,
                        message=f"Harness '{h_id}' chain did not reach declared core {core_display}",
                        extra={"harness": h_id, "core": core_display},
                    )
                )

        if max_chain_bytes is not None and total_bytes > int(max_chain_bytes) and not oversize_emitted:
            primary_path = entry_displays[0] if entry_displays else h_id
            findings.append(
                Finding(
                    code="budget.entry_chain_exceeded",
                    severity="high",
                    path=primary_path,
                    record_class=None,
                    message=(
                        f"Harness '{h_id}' entry chain is {total_bytes} bytes; "
                        f"contract max is {int(max_chain_bytes)} bytes"
                    ),
                    extra={
                        "harness": h_id,
                        "total_bytes": total_bytes,
                        "max_chain_bytes": int(max_chain_bytes),
                    },
                )
            )

        shim_out: dict[str, Any] | None = None
        if shim_cfg is not None:
            shim_abs, shim_disp, shim_origin, _ = _resolve_configured_path(shim_cfg["path"], vault, reject_env=True)
            shim_resolved = shim_abs.resolve(strict=False)
            shim_bytes: int | None = None
            if shim_resolved in visited_real:
                shim_bytes = int(visited_real[shim_resolved]["bytes"])
            else:
                c_root = vault.real if shim_origin == "vault" else home_dir
                if not _has_symlink_between(shim_abs, c_root):
                    try:
                        st_shim = shim_abs.lstat()
                        if stat.S_ISREG(st_shim.st_mode):
                            shim_bytes = int(st_shim.st_size)
                    except OSError:
                        shim_bytes = None
            shim_max = shim_cfg.get("max_bytes")
            shim_ok = True
            if shim_max is not None and shim_bytes is not None and shim_bytes > int(shim_max):
                shim_ok = False
                findings.append(
                    Finding(
                        code="entry.shim_oversize",
                        severity="medium",
                        path=shim_disp,
                        record_class=None,
                        message=(
                            f"Shim {shim_disp} in harness '{h_id}' is {shim_bytes} bytes; "
                            f"budget max is {int(shim_max)} bytes"
                        ),
                        extra={"harness": h_id, "bytes": shim_bytes, "max_bytes": int(shim_max)},
                    )
                )
            shim_out = {
                "path": shim_disp,
                "bytes": shim_bytes,
                "max_bytes": shim_max,
                "ok": shim_ok,
            }

        harnesses_out.append(
            {
                "id": h_id,
                "entry": entry_displays,
                "core": core_display,
                "core_by_reference": core_by_ref,
                "core_reached": core_reached if core_display else None,
                "core_sha256": core_sha256,
                "total_bytes": total_bytes,
                "max_chain_bytes": max_chain_bytes,
                "cycle_detected": cycle_detected,
                "depth_cap_reached": depth_cap_reached,
                "file_cap_reached": file_cap_reached,
                "max_depth": max_depth_seen,
                "shim": shim_out,
                "chain": chain_nodes,
            }
        )

    # Check core parity across harnesses
    reached_cores: dict[str, tuple[str, str]] = {}
    declared_core_harnesses = [h for h in harnesses_out if h.get("core")]
    for h in declared_core_harnesses:
        if h.get("core_sha256"):
            reached_cores[h["id"]] = (str(h["core"]), str(h["core_sha256"]))
        elif h.get("core"):
            # Even if unreached via chain, check if the declared core file on disk has a different sha256
            try:
                c_abs, c_disp, c_orig, _ = _resolve_configured_path(str(h["core"]), vault, reject_env=True)
                c_root = vault.real if c_orig == "vault" else home_dir
                c_st, c_data, _, _ = _read_chain_file(c_abs, c_root)
                if c_st == "ok" and c_data is not None:
                    reached_cores[h["id"]] = (c_disp, hashlib.sha256(c_data).hexdigest())
            except ValueError:
                pass

    distinct_core_hashes = sorted({sha for _, sha in reached_cores.values()})
    if len(distinct_core_hashes) > 1:
        first_core_path = next(iter(reached_cores.values()))[0]
        summary = ", ".join(f"{hid}={cpath}:{sha[:12]}" for hid, (cpath, sha) in sorted(reached_cores.items()))
        findings.append(
            Finding(
                code="entry.core_mismatch",
                severity="high",
                path=first_core_path,
                record_class=None,
                message=f"Harnesses declare cores with mismatched SHA-256 hashes ({summary})",
                extra={
                    "cores": {
                        hid: {"core": cpath, "sha256": sha}
                        for hid, (cpath, sha) in sorted(reached_cores.items())
                    }
                },
            )
        )

    # Check forbidden_paths via lstat / bounded scandir only (never read bytes)
    forbidden_out: list[dict[str, Any]] = []
    for raw_forb in config.get("forbidden_paths", []):
        f_abs, f_disp, _, _ = _resolve_configured_path(raw_forb, vault, reject_env=False)
        present = False
        entry_count: int | None = None
        try:
            st_forb = f_abs.lstat()
            present = True
            if stat.S_ISDIR(st_forb.st_mode) and not _is_symlink_or_junction(f_abs):
                count = 0
                try:
                    with os.scandir(f_abs) as it:
                        for _ in it:
                            count += 1
                            if count >= 32:
                                break
                    entry_count = count
                except OSError:
                    entry_count = None
        except FileNotFoundError:
            present = False
        except OSError:
            present = False

        forbidden_out.append({"path": f_disp, "present": present, "entries_sampled": entry_count})
        if present:
            findings.append(
                Finding(
                    code="entry.forbidden_present",
                    severity="high",
                    path=f_disp,
                    record_class=None,
                    message=f"Forbidden path exists on disk: {f_disp}",
                    extra={"path": f_disp},
                )
            )

    # Run law checks over entry-chain files + law_paths
    all_law_files = collect_law_files(vault, config, entry_chain_files=law_chain_files)
    law_findings = evaluate_laws(all_law_files, config, started.date())
    findings.extend(law_findings)

    # Deduplicate and sort findings
    deduped = _dedupe_findings(findings)
    parity_ok = len(distinct_core_hashes) <= 1 and all(
        bool(h.get("core_reached")) for h in declared_core_harnesses
    )
    ended = datetime.now(timezone.utc)
    vault_id = hashlib.sha256(vault.as_posix.encode("utf-8")).hexdigest()

    return {
        "schema": "legends.vault-steward.entry-audit/v1",
        "product": "Legends Vault Steward",
        "mode": "read-only",
        "version": __import__("legends_vault_steward").__version__,
        "run_id": run_id or str(uuid.uuid4()),
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "as_of": started.date().isoformat(),
        "profile": profile,
        "config_hash": config_hash(config),
        "vault": {
            "root": redact_home_text(str(vault.given)),
            "root_real": redact_home_text(vault.as_posix),
            "id": vault_id,
        },
        "harnesses": harnesses_out,
        "parity": {
            "ok": parity_ok,
            "distinct_core_hashes": distinct_core_hashes,
            "harnesses": [
                {
                    "id": h["id"],
                    "core": h["core"],
                    "core_reached": h["core_reached"],
                    "core_sha256": h["core_sha256"],
                    "total_bytes": h["total_bytes"],
                }
                for h in harnesses_out
            ],
        },
        "forbidden_paths": forbidden_out,
        "law_files_scanned": [lf.display_path for lf in all_law_files],
        "findings": [f.to_dict() for f in deduped],
    }


def _parse_date_val(value: Any) -> date | None:
    return _parse_iso_date(value)


def _dedupe_findings(items: list[Finding]) -> list[Finding]:
    seen: dict[tuple[Any, ...], int] = {}
    out: list[Finding] = []
    for item in items:
        d = item.to_dict()
        key = (
            d.get("code"),
            d.get("path") or "",
            d.get("target") or "",
            d.get("date") or "",
            d.get("fresh_until") or "",
            d.get("line") if d.get("line") is not None else "",
            d.get("byte_offset") if d.get("byte_offset") is not None else "",
            d.get("harness") or "",
            d.get("key") or "",
            d.get("sha256") or "",
        )
        if key in seen:
            idx = seen[key]
            if SEVERITY_ORDER.get(item.severity, 9) < SEVERITY_ORDER.get(out[idx].severity, 9):
                out[idx] = item
            continue
        seen[key] = len(out)
        out.append(item)
    out.sort(
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 9),
            f.code,
            f.path or "",
            f.message,
        )
    )
    return out


def render_entry_audit_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Legends Vault Steward entry-audit report",
        "",
        f"- Schema: `{report['schema']}`",
        f"- Mode: **{report['mode']}** - version `{report['version']}`",
        f"- Vault: `{report['vault']['root_real']}` (`{report['profile']}`)",
        f"- As of: {report['as_of']}",
        f"- Parity OK: **{'yes' if report['parity']['ok'] else 'NO'}**",
        "",
        "## Harnesses",
        "",
    ]
    if not report["harnesses"]:
        lines.append("_No entry harnesses configured._")
    else:
        rows = []
        for h in report["harnesses"]:
            rows.append(
                [
                    h["id"],
                    ", ".join(h["entry"]),
                    len(h["chain"]),
                    h["total_bytes"],
                    h.get("max_chain_bytes") if h.get("max_chain_bytes") is not None else "n/a",
                    h.get("core") or "n/a",
                    "yes" if h.get("core_reached") else ("NO" if h.get("core") else "n/a"),
                    (h.get("core_sha256") or "n/a")[:12],
                ]
            )
        lines.append(
            _md_table(
                ["Harness", "Entry", "Files", "Total bytes", "Max bytes", "Core", "Core reached", "Core SHA-256"],
                rows,
            )
        )
        for h in report["harnesses"]:
            lines.extend(["", f"### Chain: `{h['id']}`", ""])
            if not h["chain"]:
                lines.append("_Empty chain._")
            else:
                lines.append(
                    _md_table(
                        ["Depth", "Path", "Bytes", "SHA-256"],
                        [
                            [
                                c["depth"],
                                c["path"],
                                c["bytes"],
                                (c.get("sha256") or "oversize")[:16],
                            ]
                            for c in h["chain"]
                        ],
                    )
                )

    lines.extend(["", "## Findings", ""])
    if not report["findings"]:
        lines.append("_No findings._")
    else:
        lines.append(
            _md_table(
                ["Severity", "Code", "Path", "Message"],
                [
                    [
                        f.get("severity", ""),
                        f.get("code", ""),
                        f.get("path") or "n/a",
                        f.get("message", ""),
                    ]
                    for f in report["findings"]
                ],
            )
        )
    lines.append("")
    return "\n".join(lines).replace("\u2014", ":").replace("\u2013", "-")


def write_entry_audit_reports(
    report: dict[str, Any],
    out_dir: Path,
    *,
    vault: VaultRoot,
    format: str = "both",
) -> dict[str, Path]:
    if format not in {"both", "json", "md"}:
        raise ValueError("unknown report format")
    out_dir = validate_report_dir(out_dir, vault)
    targets = {
        kind: out_dir / f"entry-audit.{kind}"
        for kind in ("json", "md")
        if format in ("both", kind)
    }
    for path in targets.values():
        if path.exists() or _is_symlink_or_junction(path):
            raise ReportPathError(f"report already exists or is a link: {path}; choose a new run directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind, path in targets.items():
        with path.open("x", encoding="utf-8") as handle:
            if kind == "json":
                handle.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
            else:
                handle.write(render_entry_audit_markdown(report))
    return targets
