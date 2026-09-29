from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .observe import observe
from .paths import ReportPathError, resolve_vault_root, validate_report_dir
from .report import render_json, render_markdown, write_reports

MUTATING_COMMANDS: tuple[str, ...] = ()
READ_ONLY_COMMANDS = ("scan", "doctor", "version")


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
    scan.add_argument("--baseline", help="Prior observe.json from the same vault, version and configuration")
    scan.add_argument("--profile", choices=("generic", "empire"), default="generic")
    scan.add_argument("--expect-changed", action="append", default=[], metavar="PATH", help="With --baseline, allow this exact vault-relative path to change; unexpected changes exit 12")

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
        except Exception as exc:  # noqa: BLE001 — doctor must stay broad
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
    vault = resolve_vault_root(args.vault)
    include = args.notes
    report = observe(str(vault.given), config_path=args.config, include_notes=include, review_limit=args.review_limit, profile=args.profile)
    if args.baseline:
        from .review import compare
        before = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
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
            print(f"wrote {path}", file=sys.stderr)
    return 12 if report.get("comparison", {}).get("unexpected_changes") else 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        return _cmd_doctor()
    if args.command == "scan":
        try:
            return _cmd_scan(args)
        except FileNotFoundError as exc:
            print(f"lvs: {exc}", file=sys.stderr)
            return 10
        except NotADirectoryError as exc:
            print(f"lvs: {exc}", file=sys.stderr)
            return 10
        except (ValueError, ReportPathError) as exc:
            print(f"lvs: {exc}", file=sys.stderr)
            return 11
        except OSError as exc:
            print(f"lvs: {exc}", file=sys.stderr)
            return 10
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
