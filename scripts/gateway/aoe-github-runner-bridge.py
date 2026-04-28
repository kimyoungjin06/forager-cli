#!/usr/bin/env python3
"""CLI for GitHub Actions external background worker bundles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aoe_tg_github_runner_bridge import (  # noqa: E402
    build_github_runner_worker_bundle,
    decode_github_runner_worker_bundle,
    encode_github_runner_worker_bundle,
    materialize_github_runner_worker_bundle,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aoe-github-runner-bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_bundle = subparsers.add_parser("export-bundle", help="export a github_runner handoff bundle")
    export_bundle.add_argument("--team-dir", required=True)
    export_bundle.add_argument("--ticket-id", required=True)
    export_bundle.add_argument("--runner", default="github_runner", choices=("github_runner",))
    export_bundle.add_argument("--format", choices=("base64", "json"), default="base64")

    materialize = subparsers.add_parser("materialize-bundle", help="materialize a github_runner handoff bundle")
    materialize.add_argument("--bundle-b64", default="")
    materialize.add_argument("--bundle-file", default="")
    materialize.add_argument("--output-root", default=".")
    materialize.add_argument("--team-dir", default="")
    return parser


def _read_bundle_b64(args: argparse.Namespace) -> str:
    if str(args.bundle_b64 or "").strip():
        return str(args.bundle_b64).strip()
    if str(args.bundle_file or "").strip():
        return Path(args.bundle_file).expanduser().read_text(encoding="utf-8").strip()
    raise ValueError("--bundle-b64 or --bundle-file is required")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "export-bundle":
            bundle = build_github_runner_worker_bundle(
                team_dir=args.team_dir,
                ticket_id=args.ticket_id,
                runner_target=args.runner,
            )
            if args.format == "json":
                print(json.dumps(bundle, ensure_ascii=False, indent=2))
            else:
                print(encode_github_runner_worker_bundle(bundle))
            return 0
        if args.command == "materialize-bundle":
            bundle = decode_github_runner_worker_bundle(_read_bundle_b64(args))
            result = materialize_github_runner_worker_bundle(
                bundle=bundle,
                output_root=args.output_root,
                team_dir=args.team_dir,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
