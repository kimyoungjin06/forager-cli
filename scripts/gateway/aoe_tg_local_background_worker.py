#!/usr/bin/env python3
"""Local background worker helpers."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict

from aoe_tg_background_runs import (
    advance_background_run_ticket,
    claim_background_run_ticket,
    claim_next_background_run_ticket,
)

_LOCAL_BACKGROUND_REGISTRY: Dict[str, Dict[str, Any]] = {}
_LOCAL_BACKGROUND_REGISTRY_LOCK = threading.Lock()


def register_local_background_run(
    *,
    ticket_id: str,
    run_target: Callable[[], Any],
    on_ticket_update: Callable[[Dict[str, Any]], None],
    on_queue_error: Callable[[str, Exception], None],
    completed_evidence_artifacts: Callable[[], list[str]] | None = None,
    completed_evidence_bundle: Callable[[], str] | None = None,
) -> bool:
    token = str(ticket_id or "").strip()
    if not token:
        return False
    with _LOCAL_BACKGROUND_REGISTRY_LOCK:
        _LOCAL_BACKGROUND_REGISTRY[token] = {
            "run_target": run_target,
            "on_ticket_update": on_ticket_update,
            "on_queue_error": on_queue_error,
            "completed_evidence_artifacts": completed_evidence_artifacts,
            "completed_evidence_bundle": completed_evidence_bundle,
        }
    return True


def _pop_local_background_run(ticket_id: str) -> Dict[str, Any]:
    token = str(ticket_id or "").strip()
    if not token:
        return {}
    with _LOCAL_BACKGROUND_REGISTRY_LOCK:
        row = _LOCAL_BACKGROUND_REGISTRY.pop(token, None)
    return row if isinstance(row, dict) else {}


def _run_claimed_local_background_ticket(
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
    token = str(claimed_ticket.get("ticket_id", "")).strip()
    if not token:
        return run_target()

    try:
        running = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="running",
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
            evidence_bundle=completed_bundle or "status=completed | outcome=dispatch_flow_returned",
            evidence_artifacts=completed_artifacts,
        )
        if completed:
            on_ticket_update(completed)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_state_write_failed", exc)
    return result


def run_local_background_ticket(
    *,
    queue_path: Path,
    ticket_id: str,
    now_iso: Callable[[], str],
    run_target: Callable[[], Any],
    on_ticket_update: Callable[[Dict[str, Any]], None],
    on_queue_error: Callable[[str, Exception], None],
    runner_target: str = "local_background",
    launch_mode: str = "detached_no_wait",
    claimed_by: str = "",
    source_surface: str = "",
    completed_evidence_artifacts: Callable[[], list[str]] | None = None,
    completed_evidence_bundle: Callable[[], str] | None = None,
) -> Any:
    token = str(ticket_id or "").strip()
    if not token:
        return run_target()

    try:
        claimed = claim_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            runner_target=runner_target,
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        if claimed:
            on_ticket_update(claimed)
    except Exception as exc:  # pragma: no cover - defensive path
        on_queue_error("background_run_claim_failed", exc)
    return _run_claimed_local_background_ticket(
        queue_path=queue_path,
        claimed_ticket=claimed,
        now_iso=now_iso,
        run_target=run_target,
        on_ticket_update=on_ticket_update,
        on_queue_error=on_queue_error,
        completed_evidence_artifacts=completed_evidence_artifacts,
        completed_evidence_bundle=completed_evidence_bundle,
    )


def drain_local_background_queue_once(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    runner_target: str = "local_background",
    launch_mode: str = "",
    claimed_by: str = "",
    source_surface: str = "",
) -> Dict[str, Any]:
    claimed = claim_next_background_run_ticket(
        queue_path,
        now_iso=now_iso,
        runner_target=runner_target,
        launch_mode=launch_mode,
        claimed_by=claimed_by,
        source_surface=source_surface,
    )
    if not claimed:
        return {}
    token = str(claimed.get("ticket_id", "")).strip()
    row = _pop_local_background_run(token)
    if not row:
        stale = advance_background_run_ticket(
            queue_path,
            token,
            now_iso=now_iso,
            status="stale",
            evidence_bundle="status=stale | reason=missing_local_handler",
        )
        return stale or claimed
    on_ticket_update = row.get("on_ticket_update")
    if callable(on_ticket_update):
        on_ticket_update(claimed)
    _run_claimed_local_background_ticket(
        queue_path=queue_path,
        claimed_ticket=claimed,
        now_iso=now_iso,
        run_target=row["run_target"],
        on_ticket_update=row["on_ticket_update"],
        on_queue_error=row["on_queue_error"],
        completed_evidence_artifacts=row.get("completed_evidence_artifacts"),
        completed_evidence_bundle=row.get("completed_evidence_bundle"),
    )
    return claimed


def drain_local_background_queue(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    runner_target: str = "local_background",
    launch_mode: str = "",
    claimed_by: str = "",
    source_surface: str = "",
    max_items: int = 16,
) -> Dict[str, Any]:
    consumed: list[str] = []
    last_claimed: Dict[str, Any] = {}
    limit = max(1, int(max_items or 1))
    for _ in range(limit):
        claimed = drain_local_background_queue_once(
            queue_path=queue_path,
            now_iso=now_iso,
            runner_target=runner_target,
            launch_mode=launch_mode,
            claimed_by=claimed_by,
            source_surface=source_surface,
        )
        if not claimed:
            break
        ticket_id = str(claimed.get("ticket_id", "")).strip()
        if ticket_id:
            consumed.append(ticket_id)
        last_claimed = claimed
    return {
        "claimed_count": len(consumed),
        "claimed_ticket_ids": consumed,
        "last_claimed": last_claimed,
    }
