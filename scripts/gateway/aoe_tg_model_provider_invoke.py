#!/usr/bin/env python3
"""Invoke a bound model route through the provider adapter seam."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import aoe_tg_model_endpoint_adapter as endpoint_adapter
import aoe_tg_model_provider_adapter as provider_adapter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Invoke a bound model route or task-scoped judge/escalation stub.")
    parser.add_argument("--team-dir", required=True, help="team_dir containing compiled model routing artifacts")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--route-id", help="Explicit route id to invoke, for example background_worker_primary")
    mode.add_argument("--kind", choices=["judge", "escalation"], help="Task-scoped stub kind to invoke")
    parser.add_argument("--pack-profile", default="", help="Optional task pack profile override for --kind mode")
    parser.add_argument("--prompt", required=True, help="Prompt text")
    parser.add_argument("--system", default="", help="Optional system text")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    return parser


def _invoke(args: argparse.Namespace) -> Dict[str, Any]:
    team_dir = Path(args.team_dir).expanduser().resolve()
    if str(args.route_id or "").strip():
        binding = endpoint_adapter.resolve_model_binding_snapshot(team_dir, str(args.route_id).strip().lower())
        return provider_adapter.invoke_model_binding(
            binding,
            prompt=args.prompt,
            system=args.system,
            timeout_sec=float(args.timeout_sec or 30.0),
        )
    task_stub: Dict[str, Any] = {"request_id": "CLI-STUB-REQ"}
    profile = str(args.pack_profile or "").strip().lower()
    if args.kind == "judge":
        return provider_adapter.invoke_task_judge_stub(
            team_dir,
            task=task_stub,
            prompt=args.prompt,
            system=args.system,
            pack_profile_override=profile or None,
            timeout_sec=float(args.timeout_sec or 30.0),
        )
    return provider_adapter.invoke_task_escalation_stub(
        team_dir,
        task=task_stub,
        prompt=args.prompt,
        system=args.system,
        pack_profile_override=profile or None,
        timeout_sec=float(args.timeout_sec or 30.0),
    )


def main() -> int:
    args = _build_parser().parse_args()
    payload = _invoke(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
