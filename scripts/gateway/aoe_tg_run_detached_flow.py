#!/usr/bin/env python3
"""Detached no-wait dispatch flow helpers for run handlers."""

from typing import Any, Callable, Dict, Optional


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

    send_planning_detached_notice(
        entry=entry,
        project_key=key,
        task=provisional_task,
        request_id=provisional_req_id,
        send=send,
    )

    def _run_detached_dispatch() -> None:
        try:
            execute_dispatch_flow()
        except Exception as exc:  # pragma: no cover - defensive path
            reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
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

    try:
        start_background_dispatch_flow(
            name=f"aoe-run-{provisional_req_id or chat_id}",
            target=_run_detached_dispatch,
        )
    except Exception as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "background_dispatch_failed"
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
