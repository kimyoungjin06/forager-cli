#!/usr/bin/env python3
"""Probe model routes/endpoints through the modular endpoint adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe bound model routes/endpoints.")
    parser.add_argument("--team-dir", required=True, help="team_dir containing model_endpoints.json and model_routing.json")
    parser.add_argument(
        "--route-id",
        action="append",
        default=[],
        help="Specific route id to probe. Repeatable. Defaults to all canonical routes.",
    )
    parser.add_argument("--timeout-sec", type=float, default=3.0)
    return parser


def _probe_routes(team_dir: Path, route_ids: List[str], timeout_sec: float) -> Dict[str, Any]:
    target_routes = route_ids or list(model_endpoint_adapter.MODEL_ROUTE_IDS)
    results = []
    for route_id in target_routes:
        token = str(route_id or "").strip().lower()
        if token not in model_endpoint_adapter.MODEL_ROUTE_IDS:
            continue
        results.append(
            model_endpoint_adapter.probe_model_route(
                team_dir,
                token,
                timeout_sec=timeout_sec,
            )
        )
    ok_count = sum(1 for row in results if bool(row.get("ok")))
    return {
        "team_dir": str(team_dir),
        "count": len(results),
        "ok_count": ok_count,
        "results": results,
        "summary": f"probed={len(results)} ok={ok_count}",
    }


def main() -> int:
    args = _build_parser().parse_args()
    payload = _probe_routes(
        Path(args.team_dir).expanduser().resolve(),
        [str(item).strip().lower() for item in (args.route_id or []) if str(item).strip()],
        float(args.timeout_sec),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
