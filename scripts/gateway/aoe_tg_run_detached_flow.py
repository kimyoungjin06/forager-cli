#!/usr/bin/env python3
"""Detached no-wait dispatch flow helpers for run handlers."""

from typing import Any, Callable, Dict, Optional

from aoe_tg_background_runs import (
    background_runs_state_path,
    upsert_background_run_ticket,
)
from aoe_tg_local_background_worker import (
    drain_local_background_queue,
    ensure_local_background_daemon,
    register_local_background_run,
)
from aoe_tg_request_contract import (
    apply_background_run_ticket_snapshot,
    build_background_launch_spec,
    background_run_evidence_artifacts_from_task,
    background_run_evidence_bundle_from_task,
    build_background_run_ticket,
    select_background_runner_target,
)


def maybe_handle_no_wait_dispatch_detach(
    *,
    dispatch_mode: bool,
    planning_requested: bool,
    effective_no_wait: bool,
    args: Any,
    entry: Dict[str, Any],
    key: str,
    provisional_task: Optional[Dict[str, Any]],
    provisional_req_id: str,
    chat_id: str,
    todo_id: str,
    manager_state: Dict[str, Any],
    send: Callable[..., bool],
    now_iso: Callable[[], str],
    save_manager_state: Callable[..., None],
    lifecycle_set_stage: Callable[..., None],
    log_event: Callable[..., None],
    record_outcome: Optional[Callable[[Dict[str, Any]], None]],
    execute_dispatch_flow: Callable[[], bool],
    send_planning_detached_notice: Callable[..., bool],
    finalize_provisional_task: Callable[..., None],
    start_background_dispatch_flow: Callable[..., Any],
    send_dispatch_exception: Callable[..., None],
) -> Optional[bool]:
    if not (dispatch_mode and planning_requested and effective_no_wait and (not args.dry_run)):
        return None
    team_dir = str(entry.get("team_dir", "")).strip()
    queue_path = background_runs_state_path(team_dir) if team_dir else None
    launch_spec = build_background_launch_spec(
        request_id=str(provisional_req_id or "").strip(),
        project_key=str(key or "").strip(),
        project_root=str(entry.get("project_root", "") or getattr(args, "project_root", "") or "").strip(),
        team_dir=team_dir,
        manager_state_file=str(getattr(args, "manager_state_file", "") or "").strip(),
        runner_target="local_background",
        launch_mode="detached_no_wait",
        source_surface="run_no_wait",
        created_by=f"telegram:{chat_id}",
        kind="gateway_dispatch",
        mode="in_process_callback",
        entrypoint="aoe-telegram-gateway",
        argv=["run", "--no-wait"],
        env_keys=["AOE_TEAM_DIR", "AOE_STATE_DIR"],
        externalizable=False,
        blocked_reason="requires in-process callback registry",
    )
    selected_runner_target = select_background_runner_target(
        preferred_runner_target=str(entry.get("background_runner_target", "")).strip(),
        launch_spec=launch_spec,
        allow_external_targets=False,
    )

    def _sync_background_ticket(ticket: Dict[str, Any]) -> None:
        if not isinstance(provisional_task, dict):
            return
        snapshot = build_background_run_ticket(
            ticket_id=str(ticket.get("ticket_id", "")).strip(),
            request_id=str(ticket.get("request_id", provisional_req_id or "")).strip(),
            project_key=str(ticket.get("project_key", key or "")).strip(),
            execution_brief_status=str(
                ticket.get(
                    "execution_brief_status",
                    provisional_task.get("execution_brief_status", ""),
                )
            ).strip(),
            runner_target=str(ticket.get("runner_target", selected_runner_target)).strip() or selected_runner_target,
            launch_mode=str(ticket.get("launch_mode", "detached_no_wait")).strip() or "detached_no_wait",
            created_at=str(ticket.get("created_at", provisional_task.get("background_run_created_at", "") or now_iso())).strip(),
            created_by=str(ticket.get("created_by", f"telegram:{chat_id}")).strip() or f"telegram:{chat_id}",
            source_surface=str(ticket.get("source_surface", "run_no_wait")).strip() or "run_no_wait",
            status=str(ticket.get("status", "")).strip(),
            evidence_bundle=str(ticket.get("evidence_bundle", "")).strip(),
            evidence_artifacts=list(ticket.get("evidence_artifacts") or []),
            launch_spec=ticket.get("launch_spec") if isinstance(ticket.get("launch_spec"), dict) else launch_spec,
        )
        apply_background_run_ticket_snapshot(provisional_task, snapshot)
        provisional_task.setdefault("result", {})
        if isinstance(provisional_task.get("result"), dict):
            provisional_task["result"]["background_run_status"] = str(snapshot.get("status", "")).strip()
            provisional_task["result"]["background_run_runner_target"] = str(snapshot.get("runner_target", "")).strip()
            provisional_task["result"]["background_run_ticket_id"] = str(snapshot.get("ticket_id", "")).strip()
            if str(snapshot.get("evidence_bundle", "")).strip():
                provisional_task["result"]["background_run_evidence_bundle"] = str(snapshot.get("evidence_bundle", "")).strip()
        provisional_task["updated_at"] = now_iso()
        entry["updated_at"] = now_iso()
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

    def _persist_background_ticket(*, status: str, evidence_bundle: str = "") -> None:
        if not isinstance(provisional_task, dict):
            return
        ticket = build_background_run_ticket(
            ticket_id=str(provisional_task.get("background_run_ticket_id", "")).strip(),
            request_id=str(provisional_req_id or "").strip(),
            project_key=str(key or "").strip(),
            execution_brief_status=str(provisional_task.get("execution_brief_status", "")).strip(),
            runner_target="local_background",
            launch_mode="detached_no_wait",
            created_at=str(provisional_task.get("background_run_created_at", "")).strip() or now_iso(),
            created_by=f"telegram:{chat_id}",
            source_surface="run_no_wait",
            status=status,
            evidence_bundle=evidence_bundle,
            launch_spec=launch_spec,
        )
        apply_background_run_ticket_snapshot(provisional_task, ticket)
        provisional_task.setdefault("result", {})
        if isinstance(provisional_task.get("result"), dict):
            provisional_task["result"]["background_run_status"] = str(ticket.get("status", "")).strip()
            provisional_task["result"]["background_run_runner_target"] = str(ticket.get("runner_target", "")).strip()
            provisional_task["result"]["background_run_ticket_id"] = str(ticket.get("ticket_id", "")).strip()
            if str(ticket.get("evidence_bundle", "")).strip():
                provisional_task["result"]["background_run_evidence_bundle"] = str(ticket.get("evidence_bundle", "")).strip()
        provisional_task["updated_at"] = now_iso()
        entry["updated_at"] = now_iso()
        if queue_path:
            try:
                upsert_background_run_ticket(
                    queue_path,
                    ticket,
                    now_iso=now_iso,
                )
            except Exception as exc:  # pragma: no cover - defensive path
                log_event(
                    event="background_run_state_write_failed",
                    project=key,
                    request_id=str(provisional_req_id or "").strip(),
                    task=provisional_task if isinstance(provisional_task, dict) else None,
                    stage="planning",
                    status="failed",
                    detail=str(exc).strip()[:240] or "background run state write failed",
                )
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)

    _persist_background_ticket(status="queued")

    send_planning_detached_notice(
        entry=entry,
        project_key=key,
        task=provisional_task,
        request_id=provisional_req_id,
        send=send,
    )

    if queue_path and isinstance(provisional_task, dict):
        register_local_background_run(
            ticket_id=str(provisional_task.get("background_run_ticket_id", "")).strip(),
            run_target=execute_dispatch_flow,
            on_ticket_update=_sync_background_ticket,
            on_queue_error=lambda event_name, exc: log_event(
                event=event_name,
                project=key,
                request_id=str(provisional_req_id or "").strip(),
                task=provisional_task if isinstance(provisional_task, dict) else None,
                stage="planning",
                status="failed",
                detail=str(exc).strip()[:240] or "background run state write failed",
            ),
            completed_evidence_artifacts=lambda: background_run_evidence_artifacts_from_task(provisional_task),
            completed_evidence_bundle=lambda: background_run_evidence_bundle_from_task(provisional_task),
        )

    if queue_path:
        try:
            daemon_started = ensure_local_background_daemon(
                queue_path=queue_path,
                now_iso=now_iso,
                runner_target=selected_runner_target,
                launch_mode="detached_no_wait",
                claimed_by=f"daemon:{provisional_req_id or chat_id}",
                source_surface="run_no_wait",
                interval_sec=1.0,
                idle_sec=4.0,
                stale_after_sec=900,
                max_items=8,
            )
        except Exception as exc:
            log_event(
                event="background_daemon_start_failed",
                project=key,
                request_id=str(provisional_req_id or "").strip(),
                task=provisional_task if isinstance(provisional_task, dict) else None,
                stage="planning",
                status="failed",
                detail=str(exc).strip()[:240] or "background daemon start failed",
            )
        else:
            log_event(
                event="dispatch_detached",
                project=key,
                request_id=str(provisional_req_id or "").strip(),
                task=provisional_task if isinstance(provisional_task, dict) else None,
                stage="planning",
                status="running",
                detail=(
                    "background queue enqueued"
                    + (
                        f" | worker={str(daemon_started.get('thread_name', '')).strip()}"
                        if str(daemon_started.get("thread_name", "")).strip()
                        else ""
                    )
                ),
            )
            return True

    def _run_detached_dispatch() -> None:
        try:
            if queue_path:
                drain_local_background_queue(
                    queue_path=queue_path,
                    now_iso=now_iso,
                    runner_target=selected_runner_target,
                    launch_mode="detached_no_wait",
                    claimed_by=f"thread:{provisional_req_id or chat_id}",
                    source_surface="run_no_wait",
                    max_items=8,
                )
            else:
                _persist_background_ticket(status="running", evidence_bundle="status=running | outcome=dispatch_flow_started")
                execute_dispatch_flow()
        except Exception as exc:  # pragma: no cover - defensive path
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
            if not queue_path:
                _persist_background_ticket(status="failed", evidence_bundle=f"status=failed | reason={reason[:160]}")
            finalize_provisional_task(
                task=provisional_task,
                outcome="dispatch_failed",
                reason=reason,
                lifecycle_set_stage=lifecycle_set_stage,
                now_iso=now_iso,
            )
            entry["updated_at"] = now_iso()
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)
            send_dispatch_exception(
                entry=entry,
                key=key,
                todo_id=todo_id,
                reason=reason,
                send=send,
                record_outcome=record_outcome,
            )
            log_event(
                event="dispatch_detached_failed",
                project=key,
                request_id=str(provisional_req_id or "").strip(),
                task=provisional_task if isinstance(provisional_task, dict) else None,
                stage="planning",
                status="failed",
                error_code="E_DISPATCH",
                detail=reason,
            )
        else:
            if not queue_path:
                _persist_background_ticket(status="completed", evidence_bundle="status=completed | outcome=dispatch_flow_returned")

    try:
        start_background_dispatch_flow(
            name=f"aoe-run-{provisional_req_id or chat_id}",
            target=_run_detached_dispatch,
        )
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
        _persist_background_ticket(status="failed", evidence_bundle=f"status=failed | reason={reason[:160]}")
        finalize_provisional_task(
            task=provisional_task,
            outcome="dispatch_failed",
            reason=reason,
            lifecycle_set_stage=lifecycle_set_stage,
            now_iso=now_iso,
        )
        entry["updated_at"] = now_iso()
        if not args.dry_run:
            save_manager_state(args.manager_state_file, manager_state)
        send_dispatch_exception(
            entry=entry,
            key=key,
            todo_id=todo_id,
            reason=reason,
            send=send,
        )
        log_event(
            event="dispatch_detach_failed",
            project=key,
            request_id=str(provisional_req_id or "").strip(),
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status="failed",
            error_code="E_DISPATCH",
            detail=reason,
        )
    else:
        log_event(
            event="dispatch_detached",
            project=key,
            request_id=str(provisional_req_id or "").strip(),
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status="running",
            detail="background planning and dispatch started",
        )
    return True
