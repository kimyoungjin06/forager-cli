#!/usr/bin/env python3
"""External background runner handoff helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict

from aoe_tg_background_runs import (
    advance_background_run_ticket,
    claim_background_run_ticket,
)
from aoe_tg_request_contract import normalize_background_launch_spec_snapshot


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def external_background_handoff_path(team_dir: Path, ticket_id: str, runner_target: str) -> Path:
    ticket_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    ticket_token = "-".join(part for part in ticket_token.split("-") if part) or "run"
    runner_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(runner_target or "").strip())
    runner_token = "-".join(part for part in runner_token.split("-") if part) or "external"
    return Path(team_dir).expanduser().resolve() / "background_run_handoffs" / f"{runner_token}-{ticket_token}.json"


def _artifact_path_for_team(team_dir: Path, artifact_path: Path) -> str:
    team_root = Path(team_dir).expanduser().resolve()
    resolved = Path(artifact_path).expanduser().resolve()
    try:
        return str(resolved.relative_to(team_root)).strip()
    except Exception:
        return str(resolved).strip()


def emit_external_background_handoff(
    *,
    queue_path: Path,
    ticket_id: str,
    runner_target: str,
    now_iso: Callable[[], str],
    claimed_by: str = "",
    source_surface: str = "",
    launch_mode: str = "offdesk_manual",
) -> Dict[str, Any]:
    token = _trim(ticket_id, 96)
    target = _trim(runner_target, 64).lower()
    if not token or target not in {"github_runner", "remote_worker"}:
        return {}

    claimed = claim_background_run_ticket(
        queue_path,
        token,
        now_iso=now_iso,
        runner_target=target,
        launch_mode=launch_mode,
        claimed_by=claimed_by,
        source_surface=source_surface,
    )
    if not claimed or str(claimed.get("status", "")).strip().lower() != "dispatching":
        return claimed

    launch_spec = normalize_background_launch_spec_snapshot(claimed.get("launch_spec"))
    team_dir = queue_path.parent
    handoff_path = external_background_handoff_path(team_dir, token, target)
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_payload = {
        "version": "2026-04-07.v1",
        "emitted_at": now_iso(),
        "runner_target": target,
        "ticket_id": token,
        "request_id": _trim(claimed.get("request_id", ""), 96),
        "project_key": _trim(claimed.get("project_key", ""), 64),
        "launch_mode": _trim(launch_mode or claimed.get("launch_mode", ""), 64),
        "source_surface": _trim(source_surface or claimed.get("source_surface", ""), 64),
        "created_by": _trim(claimed_by or claimed.get("created_by", ""), 96),
        "launch_spec": launch_spec,
        "task_runtime": {
            "execution_brief_status": _trim(claimed.get("execution_brief_status", ""), 48),
        },
    }
    handoff_path.write_text(json.dumps(handoff_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    handoff_artifact = _artifact_path_for_team(team_dir, handoff_path)
    return advance_background_run_ticket(
        queue_path,
        token,
        now_iso=now_iso,
        status="running",
        runner_target=target,
        launch_mode=launch_mode,
        created_by=claimed_by,
        source_surface=source_surface,
        runtime_handle=handoff_artifact,
        runtime_summary=f"{target}_handoff={handoff_artifact}",
        evidence_bundle=f"status=running | outcome=external_handoff_emitted | handoff={handoff_artifact}",
        evidence_artifacts=[handoff_artifact],
    )
