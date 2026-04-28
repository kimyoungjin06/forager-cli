#!/usr/bin/env python3
"""CLI for synchronizing external background worker sidecars."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aoe_tg_external_sidecar_sync import (  # noqa: E402
    download_and_import_github_external_sidecars,
    import_external_background_sidecars,
    watch_and_import_github_external_sidecars,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aoe-external-sidecar-sync")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_artifact = subparsers.add_parser("import-artifact", help="import downloaded external worker sidecars")
    import_artifact.add_argument("--team-dir", required=True)
    import_artifact.add_argument("--artifact-root", required=True, help="Downloaded Actions artifact dir or zip")
    import_artifact.add_argument("--ticket-id", required=True)
    import_artifact.add_argument("--runner", default="github_runner", choices=("github_runner", "remote_worker"))
    import_artifact.add_argument("--overwrite", action="store_true")
    import_artifact.add_argument("--poll", action="store_true", help="Run external background poll after import")

    download_github = subparsers.add_parser(
        "download-github-artifact",
        help="download a GitHub Actions artifact with gh, then import worker sidecars",
    )
    download_github.add_argument("--team-dir", required=True)
    download_github.add_argument("--run-id", required=True)
    download_github.add_argument("--ticket-id", required=True)
    download_github.add_argument("--runner", default="github_runner", choices=("github_runner", "remote_worker"))
    download_github.add_argument("--artifact-name", default="")
    download_github.add_argument("--repo", default="", help="Optional gh --repo owner/name")
    download_github.add_argument("--gh-bin", default="gh")
    download_github.add_argument("--overwrite", action="store_true")
    download_github.add_argument("--poll", action="store_true", help="Run external background poll after import")

    watch_github = subparsers.add_parser(
        "watch-github-artifact",
        help="wait for a GitHub Actions run, then download and import worker sidecars",
    )
    watch_github.add_argument("--team-dir", required=True)
    watch_github.add_argument("--run-id", required=True)
    watch_github.add_argument("--ticket-id", required=True)
    watch_github.add_argument("--runner", default="github_runner", choices=("github_runner", "remote_worker"))
    watch_github.add_argument("--artifact-name", default="")
    watch_github.add_argument("--repo", default="", help="Optional gh --repo owner/name")
    watch_github.add_argument("--gh-bin", default="gh")
    watch_github.add_argument("--overwrite", action="store_true")
    watch_github.add_argument("--poll", action="store_true", help="Run external background poll after import")
    watch_github.add_argument("--timeout-sec", type=int, default=900)
    watch_github.add_argument("--interval-sec", type=float, default=10.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "import-artifact":
        result = import_external_background_sidecars(
            team_dir=args.team_dir,
            artifact_root=args.artifact_root,
            ticket_id=args.ticket_id,
            runner_target=args.runner,
            overwrite=bool(args.overwrite),
            poll_after_import=bool(args.poll),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "download-github-artifact":
        result = download_and_import_github_external_sidecars(
            team_dir=args.team_dir,
            run_id=args.run_id,
            ticket_id=args.ticket_id,
            runner_target=args.runner,
            artifact_name=args.artifact_name,
            repo=args.repo,
            gh_bin=args.gh_bin,
            overwrite=bool(args.overwrite),
            poll_after_import=bool(args.poll),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "watch-github-artifact":
        result = watch_and_import_github_external_sidecars(
            team_dir=args.team_dir,
            run_id=args.run_id,
            ticket_id=args.ticket_id,
            runner_target=args.runner,
            artifact_name=args.artifact_name,
            repo=args.repo,
            gh_bin=args.gh_bin,
            overwrite=bool(args.overwrite),
            poll_after_import=bool(args.poll),
            timeout_sec=int(args.timeout_sec),
            interval_sec=float(args.interval_sec),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
