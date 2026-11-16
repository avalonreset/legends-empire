from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .entry_audit import entry_audit
from .models import SEVERITY_ORDER
from .observe import observe
from .paths import (
    MAX_JSON_INPUT_BYTES,
    ReportPathError,
    VaultRoot,
    _is_symlink_or_junction,
    check_no_symlink_chain,
    read_bounded_json,
    redact_home_text,
    resolve_vault_root,
    validate_report_dir,
)

DIGEST_SCHEMA = "legends.vault-steward.digest/v1"
MAX_DIGEST_MD_BYTES = 1024


def high_key(finding: dict[str, Any]) -> str:
    code = str(finding.get("code") or "")
    path = str(finding.get("path") or "")
    subkey = ""
    if finding.get("target"):
        subkey = str(finding["target"])
    elif finding.get("date"):
        subkey = str(finding["date"])
    elif finding.get("fresh_until"):
        subkey = str(finding["fresh_until"])
    elif finding.get("byte_offset") is not None:
        subkey = str(finding["byte_offset"])
    elif finding.get("surface") and finding.get("code", "").startswith("budget."):
        subkey = str(finding["surface"])
    elif finding.get("harness") and not path:
        subkey = str(finding["harness"])
    if subkey:
        return f"{code}|{path}|{subkey}"
    return f"{code}|{path}"


def merge_and_dedupe_findings(*finding_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for flist in finding_lists:
        for f in flist:
            key = (
                f.get("code"),
                f.get("path") or "",
                f.get("target") or "",
                f.get("date") or "",
                f.get("fresh_until") or "",
                f.get("line") if f.get("line") is not None else "",
                f.get("byte_offset") if f.get("byte_offset") is not None else "",
                f.get("surface") or "",
                f.get("key") or "",
                f.get("sha256") or "",
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    out.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(str(item.get("severity")), 9),
            str(item.get("code") or ""),
            str(item.get("path") or ""),
            str(item.get("message") or ""),
        )
    )
    return out


def _load_previous_digest(path: Path) -> dict[str, Any]:
    data = read_bounded_json(path, max_bytes=MAX_JSON_INPUT_BYTES)
    if not isinstance(data, dict) or data.get("schema") != DIGEST_SCHEMA:
        raise ValueError(f"previous file is not a {DIGEST_SCHEMA} document: {redact_home_text(str(path))}")
    if not isinstance(data.get("high_keys"), list) or not all(isinstance(k, str) for k in data["high_keys"]):
        raise ValueError(f"previous digest is missing valid high_keys: {redact_home_text(str(path))}")
    return data


def _utf8_truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    if not clipped.endswith("\n"):
        nl = clipped.rfind("\n")
        if nl > 0:
            clipped = clipped[: nl + 1]
    return clipped


def render_digest_markdown(digest: dict[str, Any], high_findings: list[dict[str, Any]]) -> str:
    status = digest["status"]
    sev = digest["severity_counts"]
    new_set = set(digest.get("new_high_keys", []))

    header_lines = [
        f"# Steward Digest ({status})",
        "",
        f"- Status: **{status}**" + (" (baseline initial run)" if digest.get("first_run") else ""),
        f"- Profile: `{digest['profile']}` | As of: {digest['as_of']}",
        f"- Counts: high={sev['high']}, medium={sev['medium']}, low={sev['low']}, info={sev['info']}",
        f"- New high keys: **{len(new_set)}** | Total high keys: **{len(digest['high_keys'])}**",
    ]
    if not high_findings:
        header_lines.extend(["", "No high findings."])
        text = "\n".join(header_lines).replace("\u2014", ":").replace("\u2013", "-") + "\n"
        return _utf8_truncate(text, MAX_DIGEST_MD_BYTES)

    header_lines.extend(["", "## High findings", ""])
    base = "\n".join(header_lines).replace("\u2014", ":").replace("\u2013", "-") + "\n"

    # Sort high findings so new highs come first
    ordered_highs = sorted(
        high_findings,
        key=lambda f: (0 if high_key(f) in new_set else 1, high_key(f)),
    )

    kept_lines: list[str] = []
    total = len(ordered_highs)
    for idx, f in enumerate(ordered_highs):
        hk = high_key(f)
        tag = "[NEW] " if hk in new_set else ""
        code = f.get("code", "")
        path = f.get("path") or "(vault)"
        msg = str(f.get("message") or "").replace("\r", " ").replace("\n", " ")
        if len(msg) > 80:
            msg = msg[:77] + "..."
        bullet = f"- {tag}`{code}` `{path}`: {msg}".replace("\u2014", ":").replace("\u2013", "-")
        remaining_after = total - (idx + 1)
        reserve_suffix = f"- +{remaining_after} more\n" if remaining_after > 0 else ""
        candidate = base + "".join(line + "\n" for line in kept_lines) + bullet + "\n" + reserve_suffix
        if len(candidate.encode("utf-8")) > MAX_DIGEST_MD_BYTES:
            remaining_now = total - idx
            more_line = f"- +{remaining_now} more"
            cand_with_more = base + "".join(line + "\n" for line in kept_lines) + more_line + "\n"
            if len(cand_with_more.encode("utf-8")) <= MAX_DIGEST_MD_BYTES:
                kept_lines.append(more_line)
            break
        kept_lines.append(bullet)

    out = base + "".join(line + "\n" for line in kept_lines)
    return _utf8_truncate(out, MAX_DIGEST_MD_BYTES)


def _atomic_write_text(target: Path, content: str) -> None:
    if _is_symlink_or_junction(target):
        raise ReportPathError(f"refusing to write symlink or junction: {redact_home_text(str(target))}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if _is_symlink_or_junction(target):
            raise ReportPathError(f"refusing to overwrite symlink or junction: {redact_home_text(str(target))}")
        os.replace(tmp_path, target)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def run_digest(
    vault_path: str,
    *,
    out_raw: str,
    config_path: str | None = None,
    previous: str | None = None,
    now: datetime | None = None,
    profile: str = "generic",
    update_baseline: bool = False,
) -> tuple[dict[str, Any], str, dict[str, Path], int]:
    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    vault: VaultRoot = resolve_vault_root(vault_path)
    out_dir = validate_report_dir(out_raw, vault)

    digest_json_path = out_dir / "digest.json"
    digest_md_path = out_dir / "digest.md"
    baseline_path = out_dir / "digest.baseline.json"
    for target in (digest_json_path, digest_md_path, baseline_path):
        if _is_symlink_or_junction(target):
            raise ReportPathError(f"digest target is a symlink or junction: {redact_home_text(str(target))}")

    prev_doc: dict[str, Any] | None = None
    if previous is None:
        if baseline_path.exists():
            prev_doc = _load_previous_digest(baseline_path)
    else:
        prev_path = check_no_symlink_chain(previous, error_cls=ValueError)
        same_as_out_digest = prev_path.resolve(strict=False) == digest_json_path.resolve(strict=False)
        if same_as_out_digest:
            if baseline_path.exists():
                prev_doc = _load_previous_digest(baseline_path)
            elif prev_path.exists():
                prev_doc = _load_previous_digest(prev_path)
            else:
                prev_doc = None
        else:
            if not prev_path.exists():
                raise FileNotFoundError(f"previous digest does not exist: {redact_home_text(str(previous))}")
            prev_doc = _load_previous_digest(prev_path)

    obs_report = observe(
        str(vault.given),
        config_path=config_path,
        now=started,
        include_notes="none",
        profile=profile,
    )
    ea_report = entry_audit(
        str(vault.given),
        config_path=config_path,
        now=started,
        profile=profile,
    )

    merged_findings = merge_and_dedupe_findings(
        obs_report.get("findings", []),
        ea_report.get("findings", []),
    )

    sev_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
    code_counter: Counter[str] = Counter()
    high_findings: list[dict[str, Any]] = []
    high_keys_set: set[str] = set()

    for f in merged_findings:
        sev = str(f.get("severity") or "info")
        if sev in sev_counts:
            sev_counts[sev] += 1
        code = str(f.get("code") or "unknown")
        code_counter[code] += 1
        if sev == "high":
            high_findings.append(f)
            high_keys_set.add(high_key(f))

    sorted_high_keys = sorted(high_keys_set)
    first_run = prev_doc is None
    if first_run:
        new_high_keys: list[str] = []
        resolved_high_keys: list[str] = []
        status = "baseline"
        exit_code = 0
    else:
        old_highs = set(prev_doc.get("high_keys", []))
        new_high_keys = sorted(high_keys_set - old_highs)
        resolved_high_keys = sorted(old_highs - high_keys_set)
        status = "regression" if new_high_keys else "ok"
        exit_code = 12 if new_high_keys else 0

    digest_doc: dict[str, Any] = {
        "schema": DIGEST_SCHEMA,
        "product": "Legends Vault Steward",
        "mode": "read-only",
        "version": obs_report["version"],
        "profile": profile,
        "config_hash": obs_report["config_hash"],
        "generated_at": started.isoformat(),
        "as_of": started.date().isoformat(),
        "vault": {
            "root": redact_home_text(str(vault.given)),
            "root_real": redact_home_text(vault.as_posix),
            "id": obs_report["vault"]["id"],
        },
        "first_run": first_run,
        "status": status,
        "severity_counts": sev_counts,
        "code_counts": dict(sorted(code_counter.items())),
        "findings_total": len(merged_findings),
        "high_keys": sorted_high_keys,
        "new_high_keys": new_high_keys,
        "resolved_high_keys": resolved_high_keys,
        "entry_parity_ok": ea_report["parity"]["ok"],
    }

    md_text = render_digest_markdown(digest_doc, high_findings)
    json_text = json.dumps(digest_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    _atomic_write_text(digest_json_path, json_text)
    _atomic_write_text(digest_md_path, md_text)
    wrote = {"json": digest_json_path, "md": digest_md_path}

    # Ratchet protection (R3): do NOT overwrite baseline when exiting 12 unless --update-baseline
    if exit_code == 0 or update_baseline:
        _atomic_write_text(baseline_path, json_text)
        wrote["baseline"] = baseline_path

    return digest_doc, md_text, wrote, exit_code
