#!/usr/bin/env python3
"""Detached no-wait dispatch flow helpers for run handlers."""

from typing import Any, Callable, Dict, Optional

from aoe_tg_background_runs import (
    background_runs_state_path,
    summarize_background_runner_slots,
    upsert_background_run_ticket,
)
from aoe_tg_executor_dispatch import (
    build_gateway_run_launch_spec_for_adapter,
    launch_background_ticket_via_adapter,
)
from aoe_tg_local_background_worker import (
    drain_local_background_queue,
    ensure_local_background_daemon,
    register_local_background_run,
)
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
from aoe_tg_request_contract import (
    apply_background_run_ticket_snapshot,
    build_background_launch_spec,
    background_run_evidence_artifacts_from_task,
    background_run_evidence_bundle_from_task,
    build_background_run_ticket,
    select_background_runner_target,
)
from aoe_tg_run_lock import project_run_lock_blocks_launch, project_run_lock_note


def maybe_handle_no_wait_dispatch_detach(
    *,
    dispatch_mode: bool,
    planning_requested: bool,
    effective_no_wait: bool,
    args: Any,
    entry: Dict[str, Any],
    key: str,
    orch_target: str,
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
    selected_roles: list[str],
    effective_priority: str,
    effective_timeout: int,
    send_planning_detached_notice: Callable[..., bool],
    finalize_provisional_task: Callable[..., None],
    start_background_dispatch_flow: Callable[..., Any],
    send_dispatch_exception: Callable[..., None],
) -> Optional[bool]:
    if not (dispatch_mode and planning_requested and effective_no_wait and (not args.dry_run)):
        return None
    team_dir = str(entry.get("team_dir", "")).strip()
    queue_path = background_runs_state_path(team_dir) if team_dir else None
    project_root = str(entry.get("project_root", "") or getattr(args, "project_root", "") or "").strip()
    manager_state_file = str(getattr(args, "manager_state_file", "") or "").strip()
    preferred_runner_target = str(entry.get("background_runner_target", "")).strip()
    source_prompt = (
        str((provisional_task or {}).get("prompt", "")).strip()
        or str((provisional_task or {}).get("source_prompt", "")).strip()
    )
    role_tokens = [str(item).strip() for item in (selected_roles or (provisional_task or {}).get("roles") or []) if str(item).strip()]
    orch_ref = str(orch_target or entry.get("project_alias", "") or key or "").strip()
    tmux_launch_spec = {}
    if preferred_runner_target == "local_tmux" and source_prompt and project_root and team_dir and manager_state_file:
        tmux_launch_spec = build_gateway_run_launch_spec_for_adapter(
            runner_target=preferred_runner_target,
            request_id=str(provisional_req_id or "").strip(),
            project_key=str(key or "").strip(),
            project_root=project_root,
            team_dir=team_dir,
            manager_state_file=manager_state_file,
            orch_target=orch_ref,
            prompt=source_prompt,
            roles=role_tokens,
            priority=str(effective_priority or "").strip(),
            timeout_sec=int(effective_timeout or 0),
            force_mode="dispatch",
            simulate_chat_id=str(chat_id or "local-background").strip() or "local-background",
            launch_mode="detached_no_wait",
            source_surface="run_no_wait",
            created_by=f"telegram:{chat_id}",
        )
    fallback_launch_spec = build_background_launch_spec(
        request_id=str(provisional_req_id or "").strip(),
        project_key=str(key or "").strip(),
        project_root=project_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
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
    launch_spec = tmux_launch_spec or fallback_launch_spec
    selected_runner_target = select_background_runner_target(
        preferred_runner_target=preferred_runner_target,
        launch_spec=launch_spec,
        allow_external_targets=False,
    )
    if selected_runner_target != "local_tmux":
        launch_spec = fallback_launch_spec
    model_plan = model_endpoint_adapter.resolve_task_model_plan(team_dir, entry=entry, task=provisional_task)
    judge_binding = model_endpoint_adapter.resolve_task_judge_binding(
        team_dir,
        entry=entry,
        task=provisional_task,
    )
    judge_probe = model_endpoint_adapter.summarize_deferred_model_binding_probe(
        judge_binding,
        default_label="offdesk_judge",
    )
    escalation_binding = model_endpoint_adapter.resolve_task_escalation_binding(
        team_dir,
        entry=entry,
        task=provisional_task,
    )
    escalation_probe = model_endpoint_adapter.summarize_deferred_model_binding_probe(
        escalation_binding,
        default_label="background_worker_escalation",
    )
    launch_spec.update(
        model_endpoint_adapter.launch_spec_model_plan_metadata(
            model_plan,
            judge_binding=judge_binding,
            judge_probe=judge_probe,
            escalation_binding=escalation_binding,
            escalation_probe=escalation_probe,
        )
    )

    if selected_runner_target in {"local_tmux", "github_runner", "remote_worker"} and queue_path:
        slot_snapshot = summarize_background_runner_slots(
            queue_path,
            entry,
            selected_runner=selected_runner_target,
            statuses=["dispatching", "running"],
            max_value=8,
        )
        slot_limit = int(slot_snapshot.get("selected_limit", 1) or 1)
        active_slots = int(slot_snapshot.get("selected_active", 0) or 0)
        if active_slots >= slot_limit:
            reason = f"background runner slots exhausted for {selected_runner_target} ({active_slots}/{slot_limit})"
            if isinstance(provisional_task, dict):
                ticket = build_background_run_ticket(
                    request_id=str(provisional_req_id or "").strip(),
                    project_key=str(key or "").strip(),
                    execution_brief_status=str(provisional_task.get("execution_brief_status", "")).strip(),
                    runner_target=selected_runner_target,
                    launch_mode="detached_no_wait",
                    created_at=now_iso(),
                    created_by=f"telegram:{chat_id}",
                    source_surface="run_no_wait",
                    status="failed",
                    evidence_bundle="status=failed | reason=background_runner_slots_exhausted",
                    launch_spec=launch_spec,
                )
                apply_background_run_ticket_snapshot(provisional_task, ticket)
                provisional_task.setdefault("result", {})
                if isinstance(provisional_task.get("result"), dict):
                    provisional_task["result"]["background_run_status"] = "failed"
                    provisional_task["result"]["background_run_runner_target"] = selected_runner_target
                    provisional_task["result"]["background_run_ticket_id"] = str(ticket.get("ticket_id", "")).strip()
                    provisional_task["result"]["background_run_evidence_bundle"] = "status=failed | reason=background_runner_slots_exhausted"
                provisional_task["updated_at"] = now_iso()
                entry["updated_at"] = now_iso()
                if not args.dry_run:
                    save_manager_state(args.manager_state_file, manager_state)
            send(
                f"detached dispatch blocked\n- runtime: {key}\n- reason: {reason}\n- next: /orch bg-slots {orch_ref} {selected_runner_target} {slot_limit + 1 if slot_limit < 8 else slot_limit}",
                context="dispatch-detach blocked",
            )
            log_event(
                event="dispatch_detach_blocked",
                project=key,
                request_id=str(provisional_req_id or "").strip(),
                task=provisional_task if isinstance(provisional_task, dict) else None,
                stage="planning",
                status="blocked",
                detail=reason[:240],
            )
            return True

    if project_run_lock_blocks_launch(
        entry,
        launch_mode="detached_no_wait",
        source_surface="run_no_wait",
        source_command=source_prompt,
        launch_spec=launch_spec,
    ):
        reason = project_run_lock_note(entry) or "test-only run lock blocked detached no-wait dispatch"
        if isinstance(provisional_task, dict):
            ticket = build_background_run_ticket(
                request_id=str(provisional_req_id or "").strip(),
                project_key=str(key or "").strip(),
                execution_brief_status=str(provisional_task.get("execution_brief_status", "")).strip(),
                runner_target=selected_runner_target,
                launch_mode="detached_no_wait",
                created_at=now_iso(),
                created_by=f"telegram:{chat_id}",
                source_surface="run_no_wait",
                status="failed",
                evidence_bundle="status=failed | reason=run_lock_test_only",
                launch_spec=launch_spec,
            )
            apply_background_run_ticket_snapshot(provisional_task, ticket)
            provisional_task.setdefault("result", {})
            if isinstance(provisional_task.get("result"), dict):
                provisional_task["result"]["background_run_status"] = "failed"
                provisional_task["result"]["background_run_runner_target"] = selected_runner_target
                provisional_task["result"]["background_run_ticket_id"] = str(ticket.get("ticket_id", "")).strip()
                provisional_task["result"]["background_run_evidence_bundle"] = "status=failed | reason=run_lock_test_only"
            provisional_task["updated_at"] = now_iso()
            entry["updated_at"] = now_iso()
            if not args.dry_run:
                save_manager_state(args.manager_state_file, manager_state)
        send(
            f"detached dispatch blocked\n- runtime: {key}\n- reason: {reason}\n- next: /orch run-lock {orch_ref} open",
            context="dispatch-detach blocked",
        )
        log_event(
            event="dispatch_detach_blocked",
            project=key,
            request_id=str(provisional_req_id or "").strip(),
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status="blocked",
            detail=reason[:240],
        )
        return True

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
            runtime_handle=str(ticket.get("runtime_handle", "")).strip(),
            runtime_summary=str(ticket.get("runtime_summary", "")).strip(),
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
            runner_target=selected_runner_target,
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

    def _mark_detached_failure(reason: str) -> None:
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
            event="dispatch_detach_failed",
            project=key,
            request_id=str(provisional_req_id or "").strip(),
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status="failed",
            error_code="E_DISPATCH",
            detail=reason,
        )

    _persist_background_ticket(status="queued")

    send_planning_detached_notice(
        entry=entry,
        project_key=key,
        task=provisional_task,
        request_id=provisional_req_id,
        send=send,
    )

    if queue_path and selected_runner_target == "local_tmux":
        daemon_started = {}
        try:
            daemon_started = ensure_local_background_daemon(
                queue_path=queue_path,
                now_iso=now_iso,
                runner_target="local_background",
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
        launched = launch_background_ticket_via_adapter(
            queue_path=queue_path,
            ticket_id=str((provisional_task or {}).get("background_run_ticket_id", "")).strip(),
            runner_target=selected_runner_target,
            now_iso=now_iso,
            claimed_by=f"tmux:{provisional_req_id or chat_id}",
            source_surface="run_no_wait",
            launch_mode="detached_no_wait",
        )
        if isinstance(launched, dict) and launched:
            _sync_background_ticket(launched)
        status = str((launched or {}).get("status", "")).strip().lower()
        if status == "failed":
            reason = str((launched or {}).get("evidence_bundle", "")).strip() or "tmux_background_launch_failed"
            _mark_detached_failure(reason)
            return True
        log_event(
            event="dispatch_detached",
            project=key,
            request_id=str(provisional_req_id or "").strip(),
            task=provisional_task if isinstance(provisional_task, dict) else None,
            stage="planning",
            status=status or "running",
            detail=(
                "background tmux launched"
                + (
                    f" | worker={str(daemon_started.get('thread_name', '')).strip()}"
                    if str(daemon_started.get("thread_name", "")).strip()
                    else ""
                )
                + (
                    f" | session={str((launched or {}).get('runtime_handle', '')).strip()}"
                    if str((launched or {}).get("runtime_handle", "")).strip()
                    else ""
                )
            ),
        )
        return True

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
        _mark_detached_failure(reason)
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
