#!/usr/bin/env python3
"""tmux-backed background launch helpers."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List

import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
from aoe_tg_background_runs import (
    advance_background_run_ticket,
    claim_background_run_ticket,
    list_background_run_tickets,
    upsert_background_run_ticket,
)
from aoe_tg_request_contract import normalize_background_launch_spec_snapshot


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def build_local_tmux_session_name(ticket_id: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return (f"aoe_bg_{token or 'run'}")[:64]


def local_tmux_result_path(team_dir: Path, ticket_id: str) -> Path:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    token = "-".join(part for part in token.split("-") if part) or "run"
    return Path(team_dir).expanduser().resolve() / "background_run_results" / f"{token}.json"


def local_tmux_log_path(team_dir: Path, ticket_id: str) -> Path:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    token = "-".join(part for part in token.split("-") if part) or "run"
    return Path(team_dir).expanduser().resolve() / "background_run_logs" / f"{token}.log"


def _artifact_path_for_team(team_dir: Path, artifact_path: Path) -> str:
    team_root = Path(team_dir).expanduser().resolve()
    resolved = Path(artifact_path).expanduser().resolve()
    try:
        return str(resolved.relative_to(team_root)).strip()
    except Exception:
        return str(resolved).strip()


def _read_local_tmux_result(result_path: Path) -> Dict[str, Any]:
    if not result_path.exists():
        return {}
    try:
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    ticket_id = _trim(raw.get("ticket_id", ""), 96)
    if ticket_id:
        out["ticket_id"] = ticket_id
    try:
        out["exit_code"] = int(raw.get("exit_code", 1))
    except Exception:
        out["exit_code"] = 1
    return out


def _tmux_session_exists(session_name: str) -> bool:
    token = _trim(session_name, 120)
    if not token or not shutil.which("tmux"):
        return False
    proc = subprocess.run(
        ["tmux", "has-session", "-t", token],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def poll_local_tmux_background_tickets(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
) -> Dict[str, Any]:
    team_dir = queue_path.parent
    completed: List[str] = []
    failed: List[str] = []
    changed = False
    for row in list_background_run_tickets(queue_path, statuses=["running"], runner_target="local_tmux"):
        ticket_id = _trim(row.get("ticket_id", ""), 96)
        if not ticket_id:
            continue
        result_path = local_tmux_result_path(team_dir, ticket_id)
        log_path = local_tmux_log_path(team_dir, ticket_id)
        result = _read_local_tmux_result(result_path)
        runtime_handle = _trim(row.get("runtime_handle", ""), 120)
        evidence_artifacts = list(row.get("evidence_artifacts") or [])
        for artifact_path in (result_path, log_path):
            rel_path = _artifact_path_for_team(team_dir, artifact_path)
            if rel_path and rel_path not in evidence_artifacts:
                evidence_artifacts.append(rel_path)
        if result:
            try:
                exit_code = int(result.get("exit_code", 1))
            except Exception:
                exit_code = 1
            status = "completed" if exit_code == 0 else "failed"
            evidence_bundle = (
                f"status={status} | outcome=tmux_exit_code | exit_code={exit_code}"
                f" | log={_artifact_path_for_team(team_dir, log_path)}"
            )
            advanced = advance_background_run_ticket(
                queue_path,
                ticket_id,
                now_iso=now_iso,
                status=status,
                runner_target="local_tmux",
                runtime_handle=runtime_handle,
                runtime_summary=(f"tmux_session={runtime_handle}" if runtime_handle else ""),
                evidence_bundle=evidence_bundle,
                evidence_artifacts=evidence_artifacts,
            )
            if advanced:
                changed = True
                if status == "completed":
                    completed.append(ticket_id)
                else:
                    failed.append(ticket_id)
            continue
        if runtime_handle and (not _tmux_session_exists(runtime_handle)):
            advanced = advance_background_run_ticket(
                queue_path,
                ticket_id,
                now_iso=now_iso,
                status="failed",
                runner_target="local_tmux",
                runtime_handle=runtime_handle,
                runtime_summary=f"tmux_session={runtime_handle}",
                evidence_bundle="status=failed | reason=tmux_session_missing_result",
                evidence_artifacts=evidence_artifacts,
            )
            if advanced:
                changed = True
                failed.append(ticket_id)
    return {
        "changed": changed,
        "completed_count": len(completed),
        "failed_count": len(failed),
        "completed_ticket_ids": completed,
        "failed_ticket_ids": failed,
    }


def launch_local_tmux_background_ticket(
    *,
    queue_path: Path,
    ticket_id: str,
    now_iso: Callable[[], str],
    claimed_by: str = "",
    source_surface: str = "",
    launch_mode: str = "offdesk_manual",
) -> Dict[str, Any]:
    claimed = claim_background_run_ticket(
        queue_path,
        ticket_id,
        now_iso=now_iso,
        runner_target="local_tmux",
        launch_mode=launch_mode,
        claimed_by=claimed_by,
        source_surface=source_surface,
    )
    if not claimed or str(claimed.get("status", "")).strip().lower() != "dispatching":
        return claimed

    worker_probe = model_endpoint_adapter.probe_background_ticket_worker_binding(queue_path.parent, claimed)
    launch_spec = normalize_background_launch_spec_snapshot(claimed.get("launch_spec"))
    binding = worker_probe.get("binding") if isinstance(worker_probe.get("binding"), dict) else {}
    binding_summary = _trim(binding.get("summary", ""), 240) if binding.get("bound") else ""
    probe_status = _trim(worker_probe.get("probe_status", ""), 64)
    probe_summary = _trim(worker_probe.get("summary", ""), 240)
    if binding_summary:
        launch_spec["model_worker_binding_summary"] = binding_summary
    if probe_status:
        launch_spec["model_worker_probe_status"] = probe_status
    if probe_summary:
        launch_spec["model_worker_probe_summary"] = probe_summary
    claimed["launch_spec"] = launch_spec
    claimed = upsert_background_run_ticket(queue_path, claimed, now_iso=now_iso) or claimed
    if binding.get("bound") and (not bool(worker_probe.get("ok"))):
        return advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="failed",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            evidence_bundle=f"status=failed | reason=model_route_probe_failed | probe={probe_status or 'failed'}",
        )

    command_argv = list(launch_spec.get("command_argv") or [])
    command_cwd = _trim(launch_spec.get("command_cwd", ""), 240) or _trim(launch_spec.get("project_root", ""), 240)
    if not command_argv:
        return advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="failed",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            evidence_bundle="status=failed | reason=launch_spec_missing_command",
        )
    if not shutil.which("tmux"):
        return advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="failed",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            evidence_bundle="status=failed | reason=tmux_not_found",
        )

    session_name = build_local_tmux_session_name(ticket_id)
    result_path = local_tmux_result_path(queue_path.parent, ticket_id)
    log_path = local_tmux_log_path(queue_path.parent, ticket_id)
    result_artifact = _artifact_path_for_team(queue_path.parent, result_path)
    log_artifact = _artifact_path_for_team(queue_path.parent, log_path)
    shell_command = shlex.join([str(item) for item in command_argv if str(item).strip()])
    ticket_json = json.dumps(str(ticket_id or "").strip())
    wrapped_command = "\n".join(
        [
            f"mkdir -p {shlex.quote(str(result_path.parent))}",
            f"mkdir -p {shlex.quote(str(log_path.parent))}",
            "__aoe_exit=0",
            f"( {shell_command} ) > {shlex.quote(str(log_path))} 2>&1",
            "__aoe_exit=$?",
            (
                f"printf '{{\"ticket_id\":{ticket_json},\"exit_code\":%s}}\\n' "
                f"\"$__aoe_exit\" > {shlex.quote(str(result_path))}"
            ),
            'exit "$__aoe_exit"',
        ]
    )
    proc = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", command_cwd or ".", "bash", "-lc", wrapped_command],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        reason = _trim(proc.stderr or proc.stdout or "tmux_launch_failed", 160) or "tmux_launch_failed"
        return advance_background_run_ticket(
            queue_path,
            ticket_id,
            now_iso=now_iso,
            status="failed",
            runner_target="local_tmux",
            launch_mode=launch_mode,
            created_by=claimed_by,
            source_surface=source_surface,
            evidence_bundle=f"status=failed | reason={reason}",
        )
    runtime_summary = f"tmux_session={session_name}"
    if binding_summary:
        runtime_summary += f" | worker={binding_summary}"
    if probe_status and probe_status != "unbound":
        runtime_summary += f" | probe={probe_status}"
    evidence_bundle = f"status=running | outcome=tmux_session_started | session={session_name} | log={log_artifact}"
    if probe_status and probe_status != "unbound":
        evidence_bundle += f" | worker_probe={probe_status}"
    return advance_background_run_ticket(
        queue_path,
        ticket_id,
        now_iso=now_iso,
        status="running",
        runner_target="local_tmux",
        launch_mode=launch_mode,
        created_by=claimed_by,
        source_surface=source_surface,
        runtime_handle=session_name,
        runtime_summary=runtime_summary,
        evidence_bundle=evidence_bundle,
        evidence_artifacts=[item for item in (log_artifact, result_artifact) if item],
    )
