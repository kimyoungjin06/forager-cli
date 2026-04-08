#!/usr/bin/env python3
"""Executor adapter runtime helpers for background ticket lifecycle handling."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from aoe_tg_background_runs import advance_background_run_ticket
from aoe_tg_executor_adapter import normalize_executor_runner_target
from aoe_tg_external_background_worker import poll_external_background_tickets
from aoe_tg_tmux_background_worker import poll_local_tmux_background_tickets


def dispatch_claimed_background_ticket_via_adapter(
    *,
    queue_path: Path,
    claimed_ticket: Dict[str, Any],
    now_iso: Callable[[], str],
    run_target: Callable[[], Any],
    on_ticket_update: Callable[[Dict[str, Any]], None],
    on_queue_error: Callable[[str, Exception], None],
    completed_evidence_artifacts: Callable[[], list[str]] | None = None,
    completed_evidence_bundle: Callable[[], str] | None = None,
) -> Any:
    ticket = claimed_ticket if isinstance(claimed_ticket, dict) else {}
    token = str(ticket.get("ticket_id", "")).strip()
    runner_target = normalize_executor_runner_target(ticket.get("runner_target", ""), "local_background") or "local_background"
    if runner_target != "local_background" or not token:
        return run_target()

    try:
        running = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="running",
            runner_target=runner_target,
            evidence_bundle="status=running | outcome=dispatch_flow_started",
        )
        if running:
            on_ticket_update(running)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_state_write_failed", exc)

    try:
        result = run_target()
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
        try:
            failed = advance_background_run_ticket(
                queue_path,
                token,
                now_iso=now_iso,
                status="failed",
                runner_target=runner_target,
                evidence_bundle=f"status=failed | reason={reason[:160]}",
            )
            if failed:
                on_ticket_update(failed)
        except Exception as queue_exc:  # pragma: no cover - defensive path
            on_queue_error("background_run_state_write_failed", queue_exc)
        raise

    try:
        completed_artifacts = list(completed_evidence_artifacts() or []) if callable(completed_evidence_artifacts) else []
        completed_bundle = (
            str(completed_evidence_bundle() or "").strip()
            if callable(completed_evidence_bundle)
            else "status=completed | outcome=dispatch_flow_returned"
        )
        completed = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="completed",
            runner_target=runner_target,
            evidence_bundle=completed_bundle or "status=completed | outcome=dispatch_flow_returned",
            evidence_artifacts=completed_artifacts,
        )
        if completed:
            on_ticket_update(completed)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_state_write_failed", exc)
    return result


def poll_background_tickets_via_adapters(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    ack_source_command: str = "",
    result_source_command: str = "",
) -> Dict[str, Any]:
    tmux_poll = poll_local_tmux_background_tickets(
        queue_path=queue_path,
        now_iso=now_iso,
    )
    external_poll = poll_external_background_tickets(
        queue_path=queue_path,
        now_iso=now_iso,
        ack_source_command=ack_source_command,
        result_source_command=result_source_command,
    )
    local_background_poll = {
        "changed": False,
        "completed_count": 0,
        "failed_count": 0,
        "completed_ticket_ids": [],
        "failed_ticket_ids": [],
    }
    return {
        "changed": bool(tmux_poll.get("changed")) or bool(external_poll.get("changed")),
        "local_background": local_background_poll,
        "local_tmux": tmux_poll,
        "external": external_poll,
        "completed_count": int(tmux_poll.get("completed_count", 0) or 0) + int(external_poll.get("completed_count", 0) or 0),
        "failed_count": int(tmux_poll.get("failed_count", 0) or 0) + int(external_poll.get("failed_count", 0) or 0),
        "acknowledged_count": int(external_poll.get("acknowledged_count", 0) or 0),
    }
