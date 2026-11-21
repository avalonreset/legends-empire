from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import sys
from pathlib import Path
from . import __version__
from .digest import run_digest
from .entry_audit import entry_audit, render_entry_audit_markdown, write_entry_audit_reports
from .observe import observe
from .paths import (
    MAX_BASELINE_JSON_BYTES,
    ReportPathError,
    read_bounded_json,
    redact_home_text,
    resolve_vault_root,
    validate_report_dir,
)
from .report import render_json, render_markdown, write_reports

MUTATING_COMMANDS: tuple[str, ...] = ()
READ_ONLY_COMMANDS = ("scan", "entry-audit", "digest", "doctor", "version")


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _parse_as_of(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        raise ValueError(f"invalid --as-of date '{raw}'; expected YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"invalid --as-of date '{raw}'; expected YYYY-MM-DD") from exc
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)



def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lvs",
        description="legends-empire vault stewardship: evidence, review and verification.",
    )
    parser.add_argument("--version", action="version", version=f"legends-vault-steward {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Observe a vault. Never writes human notes.")
    scan.add_argument("vault", help="Vault root directory")
    scan.add_argument("--config", help="Optional JSON overlay merged onto the default config")
    scan.add_argument("--format", choices=("json", "md", "both"), default="both")
    scan.add_argument(
        "--out",
        help="Report directory: outside the vault, or <vault>/.steward/ only.",
    )
    scan.add_argument("--notes", choices=("all", "summary", "none"), default="all")
    scan.add_argument("--stdout", action="store_true", help="Print the selected format to stdout")
    scan.add_argument("--review-limit", type=int, default=5, help="Review at most 1-25 note groups")
    scan.add_argument("--baseline", help="Prior observe.json or snapshot.json from the same vault, version and configuration")
    scan.add_argument("--profile", choices=("generic", "empire"), default="generic")
    scan.add_argument("--expect-changed", action="append", default=[], metavar="PATH", help="With --baseline, allow this exact vault-relative path to change; unexpected changes exit 12")
    scan.add_argument("--as-of", help="Audit date in YYYY-MM-DD format")

    ea = sub.add_parser("entry-audit", help="Audit entry harnesses, @-import chains, core parity, budgets and law rules.")
    ea.add_argument("vault", help="Vault root directory")
    ea.add_argument("--profile", choices=("generic", "empire"), default="generic")
    ea.add_argument("--config", help="Optional JSON overlay merged onto the default config")
    ea.add_argument("--out", help="Report directory: outside the vault, or <vault>/.steward/ only.")
    ea.add_argument("--format", choices=("json", "md", "both"), default="both")
    ea.add_argument("--stdout", action="store_true", help="Print the selected format to stdout")
    ea.add_argument("--as-of", help="Audit date in YYYY-MM-DD format")
    ea.add_argument("--check", action="store_true", help="Exit 12 when any high finding is present")

    dig = sub.add_parser("digest", help="Write compact digest.json and <=1024-byte digest.md; exit 12 on new high findings.")
    dig.add_argument("vault", help="Vault root directory")
    dig.add_argument("--profile", choices=("generic", "empire"), default="generic")
    dig.add_argument("--config", help="Optional JSON overlay merged onto the default config")
    dig.add_argument("--previous", help="Prior digest.json for high-key regression comparison")
    dig.add_argument("--out", required=True, help="Report directory: outside the vault, or <vault>/.steward/ only.")
    dig.add_argument("--as-of", help="Audit date in YYYY-MM-DD format")
    dig.add_argument("--update-baseline", action="store_true", help="Overwrite digest.baseline.json even when exiting 12")
    dig.add_argument("--stdout", action="store_true", help="Also print the digest to stdout")
    dig.add_argument("--format", choices=("json", "md", "both"), default="both")

    sub.add_parser("doctor", help="Self-check: imports, read-only command surface")
    sub.add_parser("version", help="Print version")
    return parser


def _cmd_doctor() -> int:
    from .config import DEFAULT_CONFIG_PATH, load_config
    from .paths import STEWARD_STATE_DIR

    problems: list[str] = []
    if MUTATING_COMMANDS:
        problems.append(f"mutating commands registered: {MUTATING_COMMANDS}")
    if not DEFAULT_CONFIG_PATH.is_file():
        problems.append(f"missing default config: {DEFAULT_CONFIG_PATH}")
    else:
        try:
            load_config()
        except Exception as exc:  # noqa: BLE001: doctor must stay broad
            problems.append(f"default config failed to load: {exc}")
    if problems:
        print("lvs doctor FAIL", file=sys.stderr)
        for item in problems:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("lvs doctor OK")
    print(f"version: {__version__}")
    print(f"commands: {', '.join(READ_ONLY_COMMANDS)}")
    print("write commands: none")
    print(f"report --out: outside vault or {STEWARD_STATE_DIR}/ only")
    print("python: stdlib only")
    return 0


def _cmd_scan(args: argparse.Namespace) -> int:
    if args.expect_changed and not args.baseline:
        raise ValueError("--expect-changed requires --baseline")
    as_of = _parse_as_of(getattr(args, "as_of", None))
    vault = resolve_vault_root(args.vault)
    include = args.notes
    report = observe(
        str(vault.given),
        config_path=args.config,
        now=as_of,
        include_notes=include,
        review_limit=args.review_limit,
        profile=args.profile,
    )
    if args.baseline:
        from .review import compare
        before = read_bounded_json(args.baseline, max_bytes=MAX_BASELINE_JSON_BYTES)
        report["comparison"] = compare(before, report)
        if args.expect_changed:
            delta = report["comparison"]
            changes = set(delta["added_notes"] + delta["removed_notes"] + delta["changed_notes"])
            expected = set(args.expect_changed)
            delta["expected_changes"] = sorted(expected)
            delta["unexpected_changes"] = sorted(changes - expected)
    wrote = {}
    if args.out:
        out_dir = validate_report_dir(args.out, vault)
        wrote = write_reports(report, out_dir, vault=vault, format=args.format)
    if args.stdout or not args.out:
        if args.format == "md":
            sys.stdout.write(render_markdown(report))
        elif args.format == "json":
            sys.stdout.write(render_json(report))
        else:
            sys.stdout.write(render_json(report))
    if wrote:
        for path in wrote.values():
            print(f"wrote {redact_home_text(str(path))}", file=sys.stderr)
    return 12 if report.get("comparison", {}).get("unexpected_changes") else 0


def _cmd_entry_audit(args: argparse.Namespace) -> int:
    as_of = _parse_as_of(getattr(args, "as_of", None))
    vault = resolve_vault_root(args.vault)
    report = entry_audit(
        str(vault.given),
        config_path=args.config,
        now=as_of,
        profile=args.profile,
    )
    wrote = {}
    if args.out:
        out_dir = validate_report_dir(args.out, vault)
        wrote = write_entry_audit_reports(report, out_dir, vault=vault, format=args.format)
    if args.stdout or not args.out:
        if args.format == "md":
            sys.stdout.write(render_entry_audit_markdown(report))
        else:
            sys.stdout.write(render_json(report))
    if wrote:
        for path in wrote.values():
            print(f"wrote {redact_home_text(str(path))}", file=sys.stderr)
    if getattr(args, "check", False) and any(f.get("severity") == "high" for f in report.get("findings", [])):
        return 12
    return 0


def _cmd_digest(args: argparse.Namespace) -> int:
    as_of = _parse_as_of(getattr(args, "as_of", None))
    digest_doc, md_text, wrote, exit_code = run_digest(
        args.vault,
        out_raw=args.out,
        config_path=args.config,
        previous=args.previous,
        now=as_of,
        profile=args.profile,
        update_baseline=bool(getattr(args, "update_baseline", False)),
    )
    if getattr(args, "stdout", False):
        if args.format == "md":
            sys.stdout.write(md_text)
        else:
            sys.stdout.write(render_json(digest_doc))
    for path in wrote.values():
        print(f"wrote {redact_home_text(str(path))}", file=sys.stderr)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command in {"scan", "entry-audit", "digest"}:
        try:
            if args.command == "scan":
                return _cmd_scan(args)
            if args.command == "entry-audit":
                return _cmd_entry_audit(args)
            return _cmd_digest(args)
        except FileNotFoundError as exc:
            print(f"lvs: {redact_home_text(str(exc))}", file=sys.stderr)
            return 10
        except NotADirectoryError as exc:
            print(f"lvs: {redact_home_text(str(exc))}", file=sys.stderr)
            return 10
        except UnicodeEncodeError as exc:
            print(f"lvs: {redact_home_text(str(exc))}", file=sys.stderr)
            return 10
        except (ValueError, ReportPathError) as exc:
            print(f"lvs: {redact_home_text(str(exc))}", file=sys.stderr)
            return 11
        except OSError as exc:
            print(f"lvs: {redact_home_text(str(exc))}", file=sys.stderr)
            return 10
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
