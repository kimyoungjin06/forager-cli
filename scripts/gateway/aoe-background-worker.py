#!/usr/bin/env python3
"""CLI wrapper for background worker runtimes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from aoe_tg_external_worker_runtime import run_external_background_worker_batch  # noqa: E402


def _team_dir(raw: str) -> Path:
    token = str(raw or "").strip() or os.environ.get("AOE_TEAM_DIR", "") or ".aoe-team"
    return Path(token).expanduser().resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aoe-background-worker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker_run = subparsers.add_parser("worker-run", help="process external background handoffs")
    worker_run.add_argument("--runner", required=True, choices=("github_runner", "remote_worker"))
    worker_run.add_argument("--team-dir", default=os.environ.get("AOE_TEAM_DIR", ".aoe-team"))
    worker_run.add_argument("--ticket-id", default="")
    worker_run.add_argument("--worker-id", default="")
    worker_run.add_argument("--timeout-sec", type=int, default=900)
    worker_run.add_argument("--max-items", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "worker-run":
        result = run_external_background_worker_batch(
            team_dir=_team_dir(args.team_dir),
            runner_target=args.runner,
            ticket_id=args.ticket_id,
            worker_id=args.worker_id,
            timeout_sec=args.timeout_sec,
            max_items=args.max_items,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if int(result.get("failed_count", 0) or 0) > 0 else 0
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
