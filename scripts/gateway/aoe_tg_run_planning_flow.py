#!/usr/bin/env python3
"""Planning task lifecycle helpers for run handlers."""

import re
import threading
from typing import Any, Callable, Dict, List, Optional

from aoe_tg_exec_pipeline import project_alias as exec_project_alias


def _provision_planning_task(

    *,
    entry: Dict[str, Any],
    manager_state: Dict[str, Any],
    chat_id: str,
    key: str,
    prompt: str,
    selected_roles: List[str],
    require_verifier: bool,
    create_request_id: Callable[[], str],
    ensure_task_record: Callable[..., Dict[str, Any]],
    lifecycle_set_stage: Callable[..., None],
    touch_chat_recent_task_ref: Callable[..., None],
    set_chat_selected_task_ref: Callable[..., None],
    now_iso: Callable[[], str],
    phase1_mode: str = "",
    phase1_rounds: int = 0,
    phase1_providers: Optional[List[str]] = None,
    phase1_role_preset: str = "",
    phase2_team_preset: str = "",
    run_intent_command: str = "",
    run_intent_action: str = "",
    run_intent_class: str = "",
    run_intent_trace: str = "",
) -> tuple[str, Dict[str, Any]]:
    request_id = str(create_request_id() or "").strip()
    task = ensure_task_record(
        entry=entry,
        request_id=request_id,
        prompt=prompt,
        mode="dispatch",
        roles=list(selected_roles or []),
        verifier_roles=[],
        require_verifier=bool(require_verifier),
        intent_command=run_intent_command,
        intent_action=run_intent_action,
        intent_class=run_intent_class,
        intent_trace=run_intent_trace,
    )
    task["initiator_chat_id"] = str(chat_id)
    task["status"] = "running"
    lifecycle_set_stage(task, "intake", "done", note="request accepted")
    lifecycle_set_stage(task, "planning", "running", note="phase1 planning queued")
    task["tf_phase"] = "planning"
    task["tf_phase_reason"] = "phase1 planning queued"
    if phase1_mode:
        task["phase1_mode"] = str(phase1_mode).strip()
    if int(phase1_rounds or 0) > 0:
        task["phase1_rounds"] = int(phase1_rounds)
        task["phase1_current_round"] = 1
        task["phase1_current_total_rounds"] = int(phase1_rounds)
    if phase1_providers:
        task["phase1_providers"] = [str(item).strip() for item in phase1_providers if str(item).strip()]
    task["phase1_current_phase"] = "planner"
    task["phase1_current_detail"] = "phase1 planning queued"
    task["phase1_candidate_roles"] = [str(item).strip() for item in (selected_roles or []) if str(item).strip()]
    if phase1_role_preset:
        task["phase1_role_preset"] = str(phase1_role_preset).strip()
    if phase2_team_preset:
        task["phase2_team_preset"] = str(phase2_team_preset).strip()
    task["updated_at"] = now_iso()
    entry["last_request_id"] = request_id
    entry["updated_at"] = now_iso()
    touch_chat_recent_task_ref(manager_state, chat_id, key, request_id)
    set_chat_selected_task_ref(manager_state, chat_id, key, request_id)
    return request_id, task


def _update_provisional_planning_task(
    *,
    task: Optional[Dict[str, Any]],
    phase: str,
    detail: str,
    attempt: int,
    total: int,
    lifecycle_set_stage: Callable[..., None],
    now_iso: Callable[[], str],
) -> None:
    if not isinstance(task, dict):
        return
    note_parts: List[str] = []
    token = str(phase or "").strip()
    if token:
        note_parts.append(token)
    if attempt > 0 and total > 0:
        note_parts.append(f"{attempt}/{total}")
    if str(detail or "").strip():
        note_parts.append(str(detail).strip())
    note = " | ".join(note_parts)[:240] or "phase1 planning in progress"
    lifecycle_set_stage(task, "planning", "running", note=note)
    task["status"] = "running"
    task["tf_phase"] = "planning"
    task["tf_phase_reason"] = note
    task["phase1_current_phase"] = token or "planning"
    task["phase1_current_detail"] = str(detail or "").strip()[:240]
    if attempt > 0:
        task["phase1_current_round"] = int(attempt)
    if total > 0:
        task["phase1_current_total_rounds"] = int(total)
    detail_text = str(detail or "").strip()
    provider_match = re.search(r"\bprovider=([a-zA-Z0-9._-]+)", detail_text)
    planner_match = re.search(r"\bplanner=([a-zA-Z0-9._-]+)", detail_text)
    critic_match = re.search(r"\bcritic=([a-zA-Z0-9._-]+)", detail_text)
    if provider_match:
        task["phase1_current_provider"] = provider_match.group(1)
    if planner_match:
        task["phase1_current_planner"] = planner_match.group(1)
    if critic_match:
        task["phase1_current_critic"] = critic_match.group(1)
    task["updated_at"] = now_iso()


def _finalize_provisional_task(
    *,
    task: Optional[Dict[str, Any]],
    outcome: str,
    reason: str,
    lifecycle_set_stage: Callable[..., None],
    now_iso: Callable[[], str],
) -> None:
    if not isinstance(task, dict):
        return
    note = str(reason or "").strip()[:240]
    rate_limit = task.get("rate_limit") if isinstance(task.get("rate_limit"), dict) else {}
    if outcome == "blocked" and str(rate_limit.get("mode", "")).strip().lower() == "blocked":
        lifecycle_set_stage(task, "planning", "running", note=note or "waiting for provider capacity")
        lifecycle_set_stage(task, "close", "pending", note="rate-limited")
        task["status"] = "running"
        task["tf_phase"] = "rate_limited"
        task["tf_phase_reason"] = note or "waiting for provider capacity"
        task["updated_at"] = now_iso()
        return
    if outcome == "blocked":
        task["plan_gate_passed"] = False
        if note:
            task["plan_gate_reason"] = note
        lifecycle_set_stage(task, "planning", "failed", note=note or "planning blocked")
        lifecycle_set_stage(task, "close", "failed", note=note or "planning blocked")
        task["status"] = "failed"
        task["tf_phase"] = "blocked"
        task["tf_phase_reason"] = note or "planning blocked"
    elif outcome == "dispatch_failed":
        lifecycle_set_stage(task, "planning", "done", note="planning completed")
        lifecycle_set_stage(task, "staffing", "running", note="dispatch started")
        lifecycle_set_stage(task, "execution", "failed", note=note or "dispatch failed")
        lifecycle_set_stage(task, "close", "failed", note=note or "dispatch failed")
        task["status"] = "failed"
        task["tf_phase"] = "manual_intervention"
        task["tf_phase_reason"] = note or "dispatch failed"
    task["updated_at"] = now_iso()


def _project_alias(entry: Dict[str, Any], fallback: str) -> str:
    return exec_project_alias(entry, fallback)


def _planning_detached_reply_markup(*, entry: Dict[str, Any], project_key: str, task_label: str) -> Dict[str, Any]:
    alias = _project_alias(entry, project_key)
    keyboard: List[List[Dict[str, str]]] = []
    if str(task_label or "").strip():
        keyboard.append([{"text": f"/task {str(task_label).strip()}"}])
    nav_row: List[Dict[str, str]] = [{"text": "/monitor"}]
    if alias:
        nav_row.append({"text": f"/offdesk review {alias}"})
    keyboard.append(nav_row)
    return {
        "keyboard": keyboard,
        "resize_keyboard": True,
        "one_time_keyboard": False,
    }


def _send_planning_detached_notice(
    *,
    entry: Dict[str, Any],
    project_key: str,
    task: Optional[Dict[str, Any]],
    request_id: str,
    send: Callable[..., bool],
) -> bool:
    label = (
        str((task or {}).get("label", "")).strip()
        or str((task or {}).get("short_id", "")).strip()
        or str(request_id or "").strip()
    )
    alias = _project_alias(entry, project_key)
    next_actions = [f"/task {label}"] if label else []
    next_actions.append("/monitor")
    if alias:
        next_actions.append(f"/offdesk review {alias}")
    body = (
        f"accepted: {label or '-'}\n"
        "status: planning\n"
        f"next: {' | '.join(next_actions)}"
    )
    return send(
        body,
        context="planning-accepted",
        reply_markup=_planning_detached_reply_markup(
            entry=entry,
            project_key=project_key,
            task_label=label,
        ),
    )


def _start_background_dispatch_flow(*, name: str, target: Callable[[], Any]) -> threading.Thread:
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread
