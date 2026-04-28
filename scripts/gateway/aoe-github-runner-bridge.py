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
    build_github_runner_completion_comment,
    build_github_runner_comment_dispatch,
    build_github_runner_transport_policy,
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

    policy = subparsers.add_parser("policy-check", help="validate github_runner workflow transport policy")
    policy.add_argument("--runner", default="github_runner")
    policy.add_argument("--team-dir", required=True)
    policy.add_argument("--event-name", default="")
    policy.add_argument("--commit-results", default="false")
    policy.add_argument("--bundle-present", default="false")
    policy.add_argument("--timeout-sec", default="900")
    policy.add_argument("--max-items", default="1")

    comment = subparsers.add_parser("comment-dispatch", help="parse an issue/PR comment into safe workflow inputs")
    comment.add_argument("--event-path", required=True, help="GitHub issue_comment event JSON path")
    comment.add_argument("--github-output", default="", help="Optional GITHUB_OUTPUT file path")
    comment.add_argument("--response-file", default="", help="Optional markdown response file path")

    completion = subparsers.add_parser("completion-comment", help="build a worker completion comment body")
    completion.add_argument("--ticket-id", required=True)
    completion.add_argument("--runner", default="github_runner")
    completion.add_argument("--team-dir", default=".aoe-team")
    completion.add_argument("--run-id", required=True)
    completion.add_argument("--run-url", default="")
    completion.add_argument("--artifact-name", default="")
    completion.add_argument("--worker-result", default="")
    completion.add_argument("--comment-issue-number", default="")
    completion.add_argument("--response-file", default="", help="Optional markdown response file path")
    return parser


def _read_bundle_b64(args: argparse.Namespace) -> str:
    if str(args.bundle_b64 or "").strip():
        return str(args.bundle_b64).strip()
    if str(args.bundle_file or "").strip():
        return Path(args.bundle_file).expanduser().read_text(encoding="utf-8").strip()
    raise ValueError("--bundle-b64 or --bundle-file is required")


def _write_github_output(path: str, result: dict) -> None:
    if not str(path or "").strip():
        return
    workflow_inputs = result.get("workflow_inputs") if isinstance(result.get("workflow_inputs"), dict) else {}
    rows = {
        "ok": "true" if result.get("ok") else "false",
        "command_seen": "true" if result.get("command_seen") else "false",
        "should_comment": "true" if result.get("should_comment") else "false",
        "reason": str(result.get("reason", "")).strip(),
        "workflow": str(result.get("workflow", "")).strip(),
        "runner_target": str(workflow_inputs.get("runner_target", "")).strip(),
        "team_dir": str(workflow_inputs.get("team_dir", "")).strip(),
        "ticket_id": str(workflow_inputs.get("ticket_id", "")).strip(),
        "timeout_sec": str(workflow_inputs.get("timeout_sec", "")).strip(),
        "max_items": str(workflow_inputs.get("max_items", "")).strip(),
        "commit_results": str(workflow_inputs.get("commit_results", "false")).strip() or "false",
        "comment_issue_number": str(workflow_inputs.get("comment_issue_number", "")).strip(),
    }
    with Path(path).expanduser().open("a", encoding="utf-8") as handle:
        for key, value in rows.items():
            handle.write(f"{key}={value}\n")


def _write_response_file(path: str, result: dict) -> None:
    if not str(path or "").strip():
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(result.get("response_markdown", "")).strip() + "\n", encoding="utf-8")


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
        if args.command == "policy-check":
            result = build_github_runner_transport_policy(
                runner_target=args.runner,
                team_dir=args.team_dir,
                event_name=args.event_name,
                commit_results=args.commit_results,
                bundle_present=args.bundle_present,
                timeout_sec=args.timeout_sec,
                max_items=args.max_items,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
        if args.command == "comment-dispatch":
            event = json.loads(Path(args.event_path).expanduser().read_text(encoding="utf-8"))
            if not isinstance(event, dict):
                raise ValueError("event JSON must be an object")
            result = build_github_runner_comment_dispatch(event)
            _write_response_file(args.response_file, result)
            _write_github_output(args.github_output, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "completion-comment":
            result = build_github_runner_completion_comment(
                ticket_id=args.ticket_id,
                runner_target=args.runner,
                team_dir=args.team_dir,
                run_id=args.run_id,
                run_url=args.run_url,
                artifact_name=args.artifact_name,
                worker_result=args.worker_result,
                comment_issue_number=args.comment_issue_number,
            )
            _write_response_file(args.response_file, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 1
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
