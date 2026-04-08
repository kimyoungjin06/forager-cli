#!/usr/bin/env python3
"""Local background worker helpers."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict

from aoe_tg_background_runs import (
    background_worker_state_path,
    claim_background_run_ticket,
    claim_next_background_run_ticket,
    load_background_worker_state,
    mark_stale_background_run_tickets,
    summarize_background_runs_state,
    update_background_worker_state,
)
from aoe_tg_executor_runtime import (
    dispatch_claimed_background_ticket_via_adapter,
    poll_background_tickets_via_adapters,
)

_LOCAL_BACKGROUND_REGISTRY: Dict[str, Dict[str, Any]] = {}
_LOCAL_BACKGROUND_REGISTRY_LOCK = threading.Lock()
_LOCAL_BACKGROUND_DAEMONS: Dict[str, Dict[str, Any]] = {}
_LOCAL_BACKGROUND_DAEMONS_LOCK = threading.Lock()


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
    claimed: Dict[str, Any] = {}

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
    return dispatch_claimed_background_ticket_via_adapter(
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
    dispatch_claimed_background_ticket_via_adapter(
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


def _run_local_background_daemon(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    runner_target: str,
    launch_mode: str,
    claimed_by: str,
    source_surface: str,
    interval_sec: float,
    idle_sec: float,
    stale_after_sec: int,
    max_items: int,
    stop_event: threading.Event,
    thread_name: str,
) -> None:
    state_path = background_worker_state_path(queue_path.parent)
    update_background_worker_state(
        state_path,
        now_iso=now_iso,
        status="running",
        runner_target=runner_target,
        mode="thread_daemon",
        thread_name=thread_name,
        pid=os.getpid(),
        started_at=now_iso(),
        heartbeat_at=now_iso(),
        last_reason="startup",
    )
    try:
        while not stop_event.is_set():
            stale_result = mark_stale_background_run_tickets(
                queue_path,
                now_iso=now_iso,
                stale_after_sec=stale_after_sec,
            )
            adapter_poll = poll_background_tickets_via_adapters(
                queue_path=queue_path,
                now_iso=now_iso,
            )
            tmux_poll_result = adapter_poll.get("local_tmux") if isinstance(adapter_poll.get("local_tmux"), dict) else {}
            external_poll_result = adapter_poll.get("external") if isinstance(adapter_poll.get("external"), dict) else {}
            drained = drain_local_background_queue(
                queue_path=queue_path,
                now_iso=now_iso,
                runner_target=runner_target,
                launch_mode=launch_mode,
                claimed_by=claimed_by,
                source_surface=source_surface,
                max_items=max_items,
            )
            queue_snapshot = summarize_background_runs_state(queue_path)
            worker_snapshot = load_background_worker_state(state_path)
            claimed_count = int(drained.get("claimed_count", 0) or 0)
            last_claimed = drained.get("last_claimed") if isinstance(drained.get("last_claimed"), dict) else {}
            last_ticket_id = str(last_claimed.get("ticket_id", "")).strip()
            stale_count = int(stale_result.get("stale_count", 0) or 0)
            if claimed_count > 0:
                reason = f"drained:{claimed_count}"
            elif int(external_poll_result.get("acknowledged_count", 0) or 0) > 0:
                reason = f"external_ack:{int(external_poll_result.get('acknowledged_count', 0) or 0)}"
            elif int(tmux_poll_result.get("completed_count", 0) or 0) > 0 or int(tmux_poll_result.get("failed_count", 0) or 0) > 0:
                reason = (
                    f"tmux_polled:{int(tmux_poll_result.get('completed_count', 0) or 0)}"
                    f"/{int(tmux_poll_result.get('failed_count', 0) or 0)}"
                )
            elif int(external_poll_result.get("completed_count", 0) or 0) > 0 or int(external_poll_result.get("failed_count", 0) or 0) > 0:
                reason = (
                    f"external_polled:{int(external_poll_result.get('completed_count', 0) or 0)}"
                    f"/{int(external_poll_result.get('failed_count', 0) or 0)}"
                )
            elif stale_count > 0:
                reason = f"stale_marked:{stale_count}"
            elif int(queue_snapshot.get("depth", 0) or 0) > 0:
                reason = "queued_waiting"
            else:
                reason = "idle"
            update_background_worker_state(
                state_path,
                now_iso=now_iso,
                status="running" if int(queue_snapshot.get("depth", 0) or 0) > 0 or claimed_count > 0 else "idle",
                runner_target=runner_target,
                mode="thread_daemon",
                thread_name=thread_name,
                pid=os.getpid(),
                heartbeat_at=now_iso(),
                last_reason=reason,
                last_ticket_id=last_ticket_id,
                last_claimed_at=now_iso() if last_ticket_id else "",
                queue_summary=str(queue_snapshot.get("summary", "")).strip() or "-",
                claimed_count=int(worker_snapshot.get("claimed_count", 0) or 0) + claimed_count,
                drain_cycles=int(worker_snapshot.get("drain_cycles", 0) or 0) + 1,
                queue_depth=int(queue_snapshot.get("depth", 0) or 0),
                queue_stale_count=int(queue_snapshot.get("stale_count", 0) or 0),
            )
            sleep_sec = float(interval_sec) if int(queue_snapshot.get("depth", 0) or 0) > 0 or claimed_count > 0 else float(idle_sec)
            stop_event.wait(max(0.1, sleep_sec))
    finally:
        queue_snapshot = summarize_background_runs_state(queue_path)
        update_background_worker_state(
            state_path,
            now_iso=now_iso,
            status="stopped",
            runner_target=runner_target,
            mode="thread_daemon",
            thread_name=thread_name,
            pid=os.getpid(),
            heartbeat_at=now_iso(),
            stopped_at=now_iso(),
            last_reason="stopped",
            queue_summary=str(queue_snapshot.get("summary", "")).strip() or "-",
            queue_depth=int(queue_snapshot.get("depth", 0) or 0),
            queue_stale_count=int(queue_snapshot.get("stale_count", 0) or 0),
        )


def ensure_local_background_daemon(
    *,
    queue_path: Path,
    now_iso: Callable[[], str],
    runner_target: str = "local_background",
    launch_mode: str = "daemon_thread",
    claimed_by: str = "",
    source_surface: str = "",
    interval_sec: float = 1.0,
    idle_sec: float = 4.0,
    stale_after_sec: int = 900,
    max_items: int = 8,
) -> Dict[str, Any]:
    key = str(Path(queue_path).expanduser().resolve())
    state_path = background_worker_state_path(Path(key).parent)
    with _LOCAL_BACKGROUND_DAEMONS_LOCK:
        row = _LOCAL_BACKGROUND_DAEMONS.get(key)
        if isinstance(row, dict):
            thread = row.get("thread")
            if isinstance(thread, threading.Thread) and thread.is_alive():
                return {
                    "started": False,
                    "queue_path": key,
                    "thread_name": thread.name,
                    "runner_target": runner_target,
                }
        stop_event = threading.Event()
        thread_name = f"aoe-local-bg-{abs(hash(key)) % 100000}"
        thread = threading.Thread(
            target=_run_local_background_daemon,
            name=thread_name,
            daemon=True,
            kwargs={
                "queue_path": Path(key),
                "now_iso": now_iso,
                "runner_target": runner_target,
                "launch_mode": launch_mode,
                "claimed_by": claimed_by,
                "source_surface": source_surface,
                "interval_sec": interval_sec,
                "idle_sec": idle_sec,
                "stale_after_sec": stale_after_sec,
                "max_items": max_items,
                "stop_event": stop_event,
                "thread_name": thread_name,
            },
        )
        _LOCAL_BACKGROUND_DAEMONS[key] = {
            "thread": thread,
            "stop_event": stop_event,
        }
        update_background_worker_state(
            state_path,
            now_iso=now_iso,
            status="running",
            runner_target=runner_target,
            mode="thread_daemon",
            thread_name=thread_name,
            pid=os.getpid(),
            started_at=now_iso(),
            heartbeat_at=now_iso(),
            last_reason="startup",
        )
        thread.start()
    return {
        "started": True,
        "queue_path": key,
        "thread_name": thread_name,
        "runner_target": runner_target,
    }


def stop_local_background_daemon(
    *,
    queue_path: Path,
    wait_sec: float = 2.0,
) -> Dict[str, Any]:
    key = str(Path(queue_path).expanduser().resolve())
    with _LOCAL_BACKGROUND_DAEMONS_LOCK:
        row = _LOCAL_BACKGROUND_DAEMONS.get(key)
    if not isinstance(row, dict):
        return {"stopped": False, "queue_path": key}
    stop_event = row.get("stop_event")
    thread = row.get("thread")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(thread, threading.Thread):
        thread.join(timeout=max(0.1, float(wait_sec)))
    with _LOCAL_BACKGROUND_DAEMONS_LOCK:
        _LOCAL_BACKGROUND_DAEMONS.pop(key, None)
    return {
        "stopped": True,
        "queue_path": key,
        "thread_name": thread.name if isinstance(thread, threading.Thread) else "",
        "alive": bool(isinstance(thread, threading.Thread) and thread.is_alive()),
    }
