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
    discover_schedule_and_import_github_external_sidecars,
    download_and_import_github_external_sidecars,
    drain_scheduled_github_external_sidecar_imports,
    import_external_background_sidecars,
    schedule_github_external_sidecar_import,
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

    schedule_github = subparsers.add_parser(
        "schedule-github-import",
        help="record a GitHub Actions run for later sidecar import",
    )
    schedule_github.add_argument("--team-dir", required=True)
    schedule_github.add_argument("--run-id", required=True)
    schedule_github.add_argument("--ticket-id", required=True)
    schedule_github.add_argument("--runner", default="github_runner", choices=("github_runner", "remote_worker"))
    schedule_github.add_argument("--artifact-name", default="")
    schedule_github.add_argument("--repo", default="", help="Optional gh --repo owner/name")
    schedule_github.add_argument("--gh-bin", default="gh")
    schedule_github.add_argument("--run-url", default="")

    drain_github = subparsers.add_parser(
        "drain-github-imports",
        help="process scheduled GitHub sidecar imports",
    )
    drain_github.add_argument("--team-dir", required=True)
    drain_github.add_argument("--max-items", type=int, default=1)
    drain_github.add_argument("--overwrite", action="store_true")
    drain_github.add_argument("--poll", action="store_true", help="Run external background poll after import")
    drain_github.add_argument("--timeout-sec", type=int, default=0)
    drain_github.add_argument("--interval-sec", type=float, default=0.0)

    auto_github = subparsers.add_parser(
        "auto-import-github-artifact",
        help="discover a GitHub worker run by ticket, schedule it, then import sidecars",
    )
    auto_github.add_argument("--team-dir", required=True)
    auto_github.add_argument("--ticket-id", required=True)
    auto_github.add_argument("--runner", default="github_runner", choices=("github_runner", "remote_worker"))
    auto_github.add_argument("--artifact-name", default="")
    auto_github.add_argument("--repo", default="", help="Optional gh --repo owner/name")
    auto_github.add_argument("--gh-bin", default="gh")
    auto_github.add_argument("--workflow", default="external-background-worker.yml")
    auto_github.add_argument("--list-limit", type=int, default=20)
    auto_github.add_argument("--overwrite", action="store_true")
    auto_github.add_argument("--poll", action="store_true", help="Run external background poll after import")
    auto_github.add_argument("--timeout-sec", type=int, default=900)
    auto_github.add_argument("--interval-sec", type=float, default=10.0)
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
    if args.command == "schedule-github-import":
        result = schedule_github_external_sidecar_import(
            team_dir=args.team_dir,
            run_id=args.run_id,
            ticket_id=args.ticket_id,
            runner_target=args.runner,
            artifact_name=args.artifact_name,
            repo=args.repo,
            gh_bin=args.gh_bin,
            run_url=args.run_url,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "drain-github-imports":
        result = drain_scheduled_github_external_sidecar_imports(
            team_dir=args.team_dir,
            max_items=int(args.max_items),
            overwrite=bool(args.overwrite),
            poll_after_import=bool(args.poll),
            timeout_sec=int(args.timeout_sec),
            interval_sec=float(args.interval_sec),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "auto-import-github-artifact":
        result = discover_schedule_and_import_github_external_sidecars(
            team_dir=args.team_dir,
            ticket_id=args.ticket_id,
            runner_target=args.runner,
            artifact_name=args.artifact_name,
            repo=args.repo,
            gh_bin=args.gh_bin,
            workflow=args.workflow,
            list_limit=int(args.list_limit),
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
