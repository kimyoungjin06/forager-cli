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

from aoe_tg_external_sidecar_sync import import_external_background_sidecars  # noqa: E402


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
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
