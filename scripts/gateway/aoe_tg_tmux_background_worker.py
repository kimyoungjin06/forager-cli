#!/usr/bin/env python3
"""tmux-backed background launch helpers."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict

from aoe_tg_background_runs import advance_background_run_ticket, claim_background_run_ticket
from aoe_tg_request_contract import normalize_background_launch_spec_snapshot


def _trim(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[: max(0, int(limit))]


def build_local_tmux_session_name(ticket_id: str) -> str:
    token = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(ticket_id or "").strip())
    token = "-".join(part for part in token.split("-") if part)
    return (f"aoe_bg_{token or 'run'}")[:64]


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

    launch_spec = normalize_background_launch_spec_snapshot(claimed.get("launch_spec"))
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
    shell_command = shlex.join([str(item) for item in command_argv if str(item).strip()])
    proc = subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name, "-c", command_cwd or ".", "bash", "-lc", shell_command],
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
    return advance_background_run_ticket(
        queue_path,
        ticket_id,
        now_iso=now_iso,
        status="running",
        runner_target="local_tmux",
        launch_mode=launch_mode,
        created_by=claimed_by,
        source_surface=source_surface,
        evidence_bundle=f"status=running | outcome=tmux_session_started | session={session_name}",
    )
