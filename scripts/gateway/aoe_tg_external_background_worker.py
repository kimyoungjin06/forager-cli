#!/usr/bin/env python3
"""External background runner handoff helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import quote

from aoe_tg_action_audit import append_action_audit_row
from aoe_tg_background_runs import (
    advance_background_run_ticket,
    claim_background_run_ticket,
    list_background_run_tickets,
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


def external_background_result_path(team_dir: Path, ticket_id: str, runner_target: str) -> Path:
    ticket_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    ticket_token = "-".join(part for part in ticket_token.split("-") if part) or "run"
    runner_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(runner_target or "").strip())
    runner_token = "-".join(part for part in runner_token.split("-") if part) or "external"
    return Path(team_dir).expanduser().resolve() / "background_run_results" / f"{runner_token}-{ticket_token}.json"


def external_background_ack_path(team_dir: Path, ticket_id: str, runner_target: str) -> Path:
    ticket_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    ticket_token = "-".join(part for part in ticket_token.split("-") if part) or "run"
    runner_token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(runner_target or "").strip())
    runner_token = "-".join(part for part in runner_token.split("-") if part) or "external"
    return Path(team_dir).expanduser().resolve() / "background_run_acks" / f"{runner_token}-{ticket_token}.json"


def _artifact_path_for_team(team_dir: Path, artifact_path: Path) -> str:
    team_root = Path(team_dir).expanduser().resolve()
    resolved = Path(artifact_path).expanduser().resolve()
    try:
        return str(resolved.relative_to(team_root)).strip()
    except Exception:
        return str(resolved).strip()


def _append_external_background_audit(
    *,
    team_dir: Path,
    ticket: Dict[str, Any],
    runner_target: str,
    phase: str,
    note: str,
    source_command: str,
    outcome_reason_code: str,
    next_step: str,
    remediation: str,
    at: str,
) -> bool:
    request_id = _trim(ticket.get("request_id", ""), 96)
    link_href = f"/control/tasks/by-request/{quote(request_id, safe='')}" if request_id else "-"
    link_label = "task detail" if request_id else "runtime detail"
    label = request_id or _trim(ticket.get("ticket_id", ""), 96) or "-"
    return append_action_audit_row(
        str(team_dir),
        headline=f"External Background {phase} | {label}",
        status="updated",
        outcome_kind="background_external",
        outcome_status="updated",
        outcome_reason_code=outcome_reason_code,
        outcome_detail=note,
        next_step=next_step,
        remediation=remediation,
        source_command=source_command,
        link_label=link_label,
        link_href=link_href,
        at=at,
    )


def _read_external_background_result(result_path: Path) -> Dict[str, Any]:
    if not result_path.exists():
        return {}
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    status = _trim(raw.get("status", ""), 32).lower()
    if status not in {"completed", "failed"}:
        return {}
    evidence_artifacts = [
        _trim(item, 240)
        for item in list(raw.get("evidence_artifacts") or [])
        if _trim(item, 240)
    ]
    return {
        "ticket_id": _trim(raw.get("ticket_id", ""), 96),
        "status": status,
        "reason": _trim(raw.get("reason", ""), 160),
        "summary": _trim(raw.get("summary", ""), 240),
        "evidence_bundle": _trim(raw.get("evidence_bundle", ""), 320),
        "evidence_artifacts": evidence_artifacts,
    }


def _read_external_background_ack(ack_path: Path) -> Dict[str, Any]:
    if not ack_path.exists():
        return {}
    try:
        raw = json.loads(ack_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    status = _trim(raw.get("status", ""), 32).lower()
    if status not in {"claimed", "running", "acknowledged"}:
        status = "acknowledged"
    evidence_artifacts = [
        _trim(item, 240)
        for item in list(raw.get("evidence_artifacts") or [])
        if _trim(item, 240)
    ]
    return {
        "ticket_id": _trim(raw.get("ticket_id", ""), 96),
        "status": status,
        "worker_id": _trim(raw.get("worker_id", ""), 96),
        "summary": _trim(raw.get("summary", ""), 240),
        "evidence_artifacts": evidence_artifacts,
    }


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


def poll_external_background_tickets(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    team_dir = queue_path.parent
    acknowledged: List[str] = []
    completed: List[str] = []
    failed: List[str] = []
    changed = False
    for runner_target in ("github_runner", "remote_worker"):
        for row in list_background_run_tickets(queue_path, statuses=["running"], runner_target=runner_target):
            ticket_id = _trim(row.get("ticket_id", ""), 96)
            if not ticket_id:
                continue
            ack_path = external_background_ack_path(team_dir, ticket_id, runner_target)
            result_path = external_background_result_path(team_dir, ticket_id, runner_target)
            ack = _read_external_background_ack(ack_path)
            result = _read_external_background_result(result_path)
            runtime_handle = _trim(row.get("runtime_handle", ""), 240)
            if not result:
                if ack:
                    evidence_artifacts = list(row.get("evidence_artifacts") or [])
                    ack_rel = _artifact_path_for_team(team_dir, ack_path)
                    if ack_rel and ack_rel not in evidence_artifacts:
                        evidence_artifacts.append(ack_rel)
                    for rel_path in list(ack.get("evidence_artifacts") or []):
                        token = _trim(rel_path, 240)
                        if token and token not in evidence_artifacts:
                            evidence_artifacts.append(token)
                    current_bundle = _trim(row.get("evidence_bundle", ""), 320)
                    already_acked = "external_pickup_acknowledged" in current_bundle and (not ack_rel or ack_rel in evidence_artifacts)
                    if not already_acked:
                        summary = _trim(ack.get("summary", ""), 160)
                        worker_id = _trim(ack.get("worker_id", ""), 96)
                        runtime_summary = str(row.get("runtime_summary", "")).strip() or f"{runner_target}_handoff={runtime_handle}"
                        if ack_rel and f"ack={ack_rel}" not in runtime_summary:
                            runtime_summary = f"{runtime_summary} | ack={ack_rel}"
                        evidence_bundle = f"status=running | outcome=external_pickup_acknowledged | ack={ack_rel or '-'}"
                        if worker_id:
                            evidence_bundle += f" | worker={worker_id}"
                        if summary:
                            evidence_bundle += f" | summary={summary}"
                        advanced = advance_background_run_ticket(
                            queue_path,
                            ticket_id,
                            now_iso=now_iso,
                            status="running",
                            runner_target=runner_target,
                            runtime_handle=runtime_handle,
                            runtime_summary=runtime_summary,
                            evidence_bundle=evidence_bundle,
                            evidence_artifacts=evidence_artifacts,
                        )
                        if advanced:
                            changed = True
                            acknowledged.append(ticket_id)
                            _append_external_background_audit(
                                team_dir=team_dir,
                                ticket={**row, **advanced},
                                runner_target=runner_target,
                                phase="Pickup Ack",
                                note=ack_rel or summary or worker_id or ticket_id,
                                source_command=f"/external ack {runner_target} {ticket_id}",
                                outcome_reason_code="external_pickup_acknowledged",
                                next_step=f"/task {ticket_id if not _trim(row.get('request_id', ''), 96) else _trim(row.get('request_id', ''), 96)}",
                                remediation=(
                                    f"{runner_target} picked up the background run; wait for the result sidecar before taking the next operator action"
                                ),
                                at=now_iso(),
                            )
                continue
            evidence_artifacts = list(row.get("evidence_artifacts") or [])
            for artifact_path in (ack_path,):
                rel_path = _artifact_path_for_team(team_dir, artifact_path)
                if ack and rel_path and rel_path not in evidence_artifacts:
                    evidence_artifacts.append(rel_path)
            for artifact_path in (result_path,):
                rel_path = _artifact_path_for_team(team_dir, artifact_path)
                if rel_path and rel_path not in evidence_artifacts:
                    evidence_artifacts.append(rel_path)
            for rel_path in list(result.get("evidence_artifacts") or []):
                token = _trim(rel_path, 240)
                if token and token not in evidence_artifacts:
                    evidence_artifacts.append(token)
            status = str(result.get("status", "")).strip().lower()
            if status not in {"completed", "failed"}:
                continue
            evidence_bundle = _trim(result.get("evidence_bundle", ""), 320)
            if not evidence_bundle:
                summary = _trim(result.get("summary", ""), 200)
                reason = _trim(result.get("reason", ""), 160)
                if status == "completed":
                    evidence_bundle = (
                        f"status=completed | outcome=external_result | runner={runner_target}"
                        + (f" | summary={summary}" if summary else "")
                    )
                else:
                    evidence_bundle = (
                        f"status=failed | reason={reason or 'external_result_failed'} | runner={runner_target}"
                        + (f" | summary={summary}" if summary else "")
                    )
            advanced = advance_background_run_ticket(
                queue_path,
                ticket_id,
                now_iso=now_iso,
                status=status,
                runner_target=runner_target,
                runtime_handle=runtime_handle,
                runtime_summary=(str(row.get("runtime_summary", "")).strip() or f"{runner_target}_handoff={runtime_handle}"),
                evidence_bundle=evidence_bundle,
                evidence_artifacts=evidence_artifacts,
            )
            if advanced:
                changed = True
                if status == "completed":
                    completed.append(ticket_id)
                    _append_external_background_audit(
                        team_dir=team_dir,
                        ticket={**row, **advanced},
                        runner_target=runner_target,
                        phase="Result",
                        note=evidence_bundle,
                        source_command=f"/external result {runner_target} {ticket_id}",
                        outcome_reason_code="external_result_completed",
                        next_step=f"/task {ticket_id if not _trim(row.get('request_id', ''), 96) else _trim(row.get('request_id', ''), 96)}",
                        remediation="inspect the task detail and evidence bundle before issuing another rerun or follow-up action",
                        at=now_iso(),
                    )
                else:
                    failed.append(ticket_id)
                    _append_external_background_audit(
                        team_dir=team_dir,
                        ticket={**row, **advanced},
                        runner_target=runner_target,
                        phase="Result",
                        note=evidence_bundle,
                        source_command=f"/external result {runner_target} {ticket_id}",
                        outcome_reason_code="external_result_failed",
                        next_step=f"/task {ticket_id if not _trim(row.get('request_id', ''), 96) else _trim(row.get('request_id', ''), 96)}",
                        remediation="inspect the failed external result and evidence bundle before retrying or changing runner target",
                        at=now_iso(),
                    )
    return {
        "changed": changed,
        "acknowledged_count": len(acknowledged),
        "acknowledged_ticket_ids": acknowledged,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "completed_ticket_ids": completed,
        "failed_ticket_ids": failed,
    }
