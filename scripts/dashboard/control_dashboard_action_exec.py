#!/usr/bin/env python3
"""Mutation action bridges for the Control Dashboard."""

from __future__ import annotations

import importlib.util
import shutil
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import aoe_tg_chat_state as chat_state
import aoe_tg_management_handlers as management_handlers
import aoe_tg_parse as parse_mod
import aoe_tg_request_state as request_state_mod
import aoe_tg_retry_handlers as retry_handlers
import aoe_tg_run_handlers as run_handlers
import aoe_tg_runtime_read as runtime_read
import aoe_tg_scheduler_control_handlers as scheduler_control_handlers
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view

from control_dashboard_common import ROOT, DashboardAppConfig, _dashboard_paths, _json, _not_found_json

_DASHBOARD_CHAT_ID = "dashboard-http"
_DASHBOARD_CHAT_ROLE = "owner"

_RETRY_BLOCKED_REMEDIATIONS = {
    "planning-gate": "inspect planning critic issues and approval blockers in /task and /offdesk review before retrying again",
    "dispatch-exception": "inspect dispatch exception output and backend notes in the task detail before attempting another retry",
    "exec-critic": "inspect exec critic verdict and lane rerun targets in /task before retrying again",
    "verifier-gate failed": "inspect verifier findings and required verifier roles in /task before retrying again",
    "run usage": "inspect the retry command payload and lane selection before retrying again",
    "unknown command": "inspect the retry action contract and command mapping before retrying again",
    "empty prompt": "inspect the source task prompt in the runtime lifecycle before retrying again",
}


def _latest_recorded_outcome(rows: List[Dict[str, Any]], *, kind: str) -> Dict[str, Any]:
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if str(row.get("kind", "")).strip() == kind:
            return row
    return {}



def _missing_outcome_response(
    *,
    path: str,
    source_command: str,
    payload: Dict[str, Any],
    kind: str,
    messages: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    remediation: str,
) -> Tuple[int, Dict[str, str], bytes]:
    return _json(
        {
            "ok": False,
            "implemented": True,
            "executed": False,
            "status": "contract_missing",
            "method": "POST",
            "path": path,
            "source_command": source_command,
            "payload": payload,
            "messages": messages,
            "events": events,
            "outcome": {
                "kind": kind,
                "status": "contract_missing",
                "reason_code": "outcome_missing",
                "detail": "handler returned without structured outcome",
            },
            "next_step": "/task" if kind == "retry_run" else "/auto status",
            "remediation": remediation,
        },
        status=500,
    )



def _retry_blocked_remediation_for_reason(reason_code: str, detail: str = "") -> str:
    token = str(reason_code or "").strip().lower().replace("-", "_")
    if token == "planning_gate":
        return _RETRY_BLOCKED_REMEDIATIONS["planning-gate"]
    if token == "dispatch_exception":
        return _RETRY_BLOCKED_REMEDIATIONS["dispatch-exception"]
    if token == "exec_critic":
        return _RETRY_BLOCKED_REMEDIATIONS["exec-critic"]
    if token == "verifier_gate_failed":
        return _RETRY_BLOCKED_REMEDIATIONS["verifier-gate failed"]
    if token == "verifier_gate_setup":
        return "assign or enable the required verifier role before retrying the runtime again"
    return str(detail or "").strip() or "inspect the task and runtime state before retrying again"


@lru_cache(maxsize=1)
def _load_gateway_main_module():
    module_path = ROOT / "scripts" / "gateway" / "aoe-telegram-gateway.py"
    spec = importlib.util.spec_from_file_location("aoe_telegram_gateway_main", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load gateway main module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def _dashboard_action_args(config: DashboardAppConfig) -> Any:
    paths = _dashboard_paths(config)
    return SimpleNamespace(
        control_root=str(paths.control_root),
        project_root=str(paths.control_root),
        team_dir=str(paths.team_dir),
        manager_state_file=paths.manager_state_file,
        roles="",
        priority="P2",
        orch_timeout_sec=600,
        no_wait=False,
        orch_command_timeout_sec=900,
        orch_poll_sec=2.0,
        aoe_orch_bin=shutil.which("aoe-orch") or "aoe-orch",
        aoe_team_bin=shutil.which("aoe-team") or "aoe-team",
        dry_run=False,
        require_verifier=False,
        verifier_roles="",
    )



def _load_dashboard_manager_state(config: DashboardAppConfig) -> tuple[Any, Dict[str, Any]]:
    paths = _dashboard_paths(config)
    state = runtime_read.load_manager_state(paths.manager_state_file, paths.control_root, paths.team_dir)
    return paths, state



def _make_send_collector(messages: List[Dict[str, Any]]):
    def _send(text: Any, *, context: str = "", with_menu: bool = False, reply_markup: Any = None, **_kwargs: Any) -> bool:
        messages.append(
            {
                "text": str(text or "").strip(),
                "context": str(context or "").strip(),
                "with_menu": bool(with_menu),
                "reply_markup_present": bool(reply_markup),
            }
        )
        return True

    return _send



def _make_log_collector(events: List[Dict[str, Any]]):
    def _log(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    return _log



def _retry_blocked_remediation(contexts: List[str]) -> str:
    for context in contexts:
        token = str(context or "").strip()
        if token in _RETRY_BLOCKED_REMEDIATIONS:
            return _RETRY_BLOCKED_REMEDIATIONS[token]
    return "inspect planning or critic blockers in /offdesk review before re-running retry"



def _auto_recover_remediation(*, blocked: bool, provider_state: Dict[str, Any]) -> str:
    retry_at = str(provider_state.get("next_retry_at", "")).strip()
    repeat_count = int(provider_state.get("recovery_repeat_count", 0) or 0)
    if not blocked:
        return "verify recovery grace and next retry timing in /auto status before making the next control decision"
    if retry_at and repeat_count > 0:
        return f"provider capacity is still blocked; inspect /offdesk review and /auto status, then wait for retry_at={retry_at} with repeat memory in mind"
    if retry_at:
        return f"provider capacity is still blocked; inspect /auto status and wait for retry_at={retry_at} before forcing another recover"
    if repeat_count > 0:
        return "provider capacity is repeatedly blocked; inspect repeat memory and blocked runtimes in /offdesk review before forcing another recover"
    return "inspect provider capacity and blocked runtimes in /offdesk review before forcing another recover"



def _dashboard_get_context_factory(manager_state: Dict[str, Any], paths: Any):
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}

    def _get_context(raw_target: Optional[str]) -> tuple[str, Dict[str, Any], Any]:
        target = str(raw_target or "").strip()
        if target:
            upper = target.upper()
            for key, entry in projects.items():
                if not isinstance(entry, dict):
                    continue
                alias = str(entry.get("project_alias", "")).strip().upper()
                if str(key) == target or alias == upper:
                    return str(key), entry, SimpleNamespace(
                        project_root=Path(str(entry.get("project_root", paths.control_root))).expanduser().resolve(),
                        team_dir=Path(str(entry.get("team_dir", paths.team_dir))).expanduser().resolve(),
                        manager_state_file=paths.manager_state_file,
                        roles="",
                        priority="P2",
                        orch_timeout_sec=600,
                        no_wait=False,
                        orch_command_timeout_sec=900,
                        orch_poll_sec=2.0,
                        aoe_orch_bin=shutil.which("aoe-orch") or "aoe-orch",
                        aoe_team_bin=shutil.which("aoe-team") or "aoe-team",
                        require_verifier=False,
                        verifier_roles="",
                    )
        active = str(manager_state.get("active", "")).strip()
        entry = projects.get(active) if active and isinstance(projects.get(active), dict) else None
        if isinstance(entry, dict):
            return active, entry, SimpleNamespace(
                project_root=Path(str(entry.get("project_root", paths.control_root))).expanduser().resolve(),
                team_dir=Path(str(entry.get("team_dir", paths.team_dir))).expanduser().resolve(),
                manager_state_file=paths.manager_state_file,
                roles="",
                priority="P2",
                orch_timeout_sec=600,
                no_wait=False,
                orch_command_timeout_sec=900,
                orch_poll_sec=2.0,
                aoe_orch_bin=shutil.which("aoe-orch") or "aoe-orch",
                aoe_team_bin=shutil.which("aoe-team") or "aoe-team",
                require_verifier=False,
                verifier_roles="",
            )
        raise RuntimeError(f"runtime not found: {raw_target or '-'}")

    return _get_context



def _find_task_project_key(manager_state: Dict[str, Any], task_ref: str) -> str:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(task_ref or "").strip()
    if not target:
        return ""
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        if gateway_task_state.get_task_record(entry, target):
            return str(key)
    return ""



def _dashboard_run_args(config: DashboardAppConfig, *, source_task: Optional[Dict[str, Any]]) -> Any:
    args = _dashboard_action_args(config)
    verifier_roles = gateway_task_view.dedupe_roles((source_task or {}).get("verifier_roles") or [])
    phase1_rounds = int((source_task or {}).get("phase1_rounds", 0) or 0)
    phase1_providers = [str(item).strip() for item in ((source_task or {}).get("phase1_providers") or []) if str(item).strip()]
    args.auto_dispatch = False
    args.task_planning = True
    args.plan_phase1_ensemble = True
    args.plan_phase1_rounds = phase1_rounds if phase1_rounds > 0 else 3
    args.plan_phase1_providers = ",".join(phase1_providers) if phase1_providers else "codex,claude"
    args.plan_max_subtasks = 4
    args.plan_auto_replan = True
    args.plan_replan_attempts = 2
    args.plan_block_on_critic = True
    args.exec_critic = False
    args.exec_critic_retry_max = 3
    args.chat_max_running = 3
    args.chat_daily_cap = 20
    args.require_verifier = bool((source_task or {}).get("require_verifier")) or bool(verifier_roles)
    args.verifier_roles = ",".join(verifier_roles)
    return args



def _dashboard_render_run_response(state: Dict[str, Any], task: Optional[Dict[str, Any]] = None) -> str:
    return request_state_mod.render_run_response(
        state,
        task=task,
        report_level="normal",
        default_report_level="normal",
        task_display_label=gateway_task_view.task_display_label,
        summarize_state=request_state_mod.summarize_state,
    )



def _build_dashboard_retry_run_deps(
    *,
    config: DashboardAppConfig,
    manager_state: Dict[str, Any],
    paths: Any,
    messages: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    outcomes: List[Dict[str, Any]],
):
    gateway_main = _load_gateway_main_module()
    return run_handlers.build_run_deps(
        send=_make_send_collector(messages),
        log_event=_make_log_collector(events),
        help_text=lambda: "dashboard retry action",
        record_outcome=lambda row: outcomes.append(dict(row)) if isinstance(row, dict) else None,
        summarize_chat_usage=gateway_main.summarize_chat_usage,
        detect_high_risk_prompt=parse_mod.detect_high_risk_prompt,
        set_confirm_action=chat_state.set_confirm_action,
        save_manager_state=gateway_main.save_manager_state,
        get_context=_dashboard_get_context_factory(manager_state, paths),
        choose_auto_dispatch_roles=gateway_main.choose_auto_dispatch_roles,
        resolve_verifier_candidates=gateway_main.resolve_verifier_candidates,
        load_orchestrator_roles=gateway_main.load_orchestrator_roles,
        parse_roles_csv=gateway_main.parse_roles_csv,
        ensure_verifier_roles=gateway_main.ensure_verifier_roles,
        available_worker_roles=gateway_main.available_worker_roles,
        normalize_task_plan_payload=gateway_main.normalize_task_plan_payload,
        build_task_execution_plan=gateway_main.build_task_execution_plan,
        critique_task_execution_plan=gateway_main.critique_task_execution_plan,
        critic_has_blockers=gateway_main.critic_has_blockers,
        repair_task_execution_plan=gateway_main.repair_task_execution_plan,
        plan_roles_from_subtasks=gateway_main.plan_roles_from_subtasks,
        build_planned_dispatch_prompt=gateway_main.build_planned_dispatch_prompt,
        phase1_ensemble_planning=gateway_main.run_phase1_ensemble_planning,
        run_orchestrator_direct=lambda *_args, **_kwargs: "dashboard direct mode is not enabled for retry actions",
        run_aoe_orch=gateway_main.run_aoe_orch,
        create_request_id=gateway_main.create_request_id,
        ensure_task_record=gateway_main.ensure_task_record,
        finalize_request_reply_messages=lambda *_args, **_kwargs: {},
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
        now_iso=gateway_main.now_iso,
        sync_task_lifecycle=gateway_main.sync_task_lifecycle,
        lifecycle_set_stage=gateway_main.lifecycle_set_stage,
        summarize_task_lifecycle=gateway_main.summarize_task_lifecycle,
        synthesize_orchestrator_response=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("dashboard synth disabled")),
        critique_task_result=lambda **_kwargs: {"verdict": "success", "reason": ""},
        extract_todo_proposals=lambda *_args, **_kwargs: [],
        merge_todo_proposals=lambda **_kwargs: {"created_count": 0, "created_ids": [], "duplicate_count": 0, "skipped_count": 0},
        render_run_response=_dashboard_render_run_response,
    )



def _execute_retry_run_transition(
    transition: Dict[str, Any],
    *,
    config: DashboardAppConfig,
    manager_state: Dict[str, Any],
    paths: Any,
    source_command: str,
    payload: Dict[str, Any],
) -> Tuple[int, Dict[str, str], bytes]:
    messages: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []
    source_task = transition.get("run_source_task") if isinstance(transition.get("run_source_task"), dict) else None
    args = _dashboard_run_args(config, source_task=source_task)
    ctx = run_handlers.build_run_context(
        cmd=str(transition.get("cmd", "run")).strip() or "run",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        text=str(transition.get("run_prompt", "")).strip(),
        rest=str(transition.get("rest", "")).strip(),
        orch_target=str(transition.get("orch_target", "")).strip() or None,
        run_prompt=str(transition.get("run_prompt", "")).strip(),
        run_roles_override=transition.get("run_roles_override"),
        run_priority_override=None,
        run_timeout_override=None,
        run_no_wait_override=transition.get("run_no_wait_override"),
        run_force_mode=str(transition.get("run_force_mode", "")).strip() or None,
        run_auto_source="dashboard_retry",
        run_control_mode=str(transition.get("run_control_mode", "")).strip(),
        run_source_request_id=str(transition.get("run_source_request_id", "")).strip(),
        run_source_task=source_task,
        run_selected_execution_lane_ids=list(transition.get("run_selected_execution_lane_ids") or []),
        run_selected_review_lane_ids=list(transition.get("run_selected_review_lane_ids") or []),
    )
    deps = _build_dashboard_retry_run_deps(
        config=config,
        manager_state=manager_state,
        paths=paths,
        messages=messages,
        events=events,
        outcomes=outcomes,
    )
    handled = run_handlers.handle_run_or_unknown_command(ctx=ctx, deps=deps)
    if not handled:
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "unhandled",
                "path": "/control/actions/task/retry",
                "source_command": source_command,
                "payload": payload,
                "messages": messages,
                "events": events,
                "remediation": "inspect the runtime task detail and retry contract before attempting another retry bridge",
            },
            status=500,
        )

    project_key = str(transition.get("orch_target", "")).strip()
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    entry = projects.get(project_key) if project_key and isinstance(projects.get(project_key), dict) else {}
    executed_request_id = str(entry.get("last_request_id", "")).strip() if isinstance(entry, dict) else ""
    executed_task = gateway_task_state.get_task_record(entry, executed_request_id) if isinstance(entry, dict) and executed_request_id else None
    outcome = _latest_recorded_outcome(outcomes, kind="retry_run")
    if not outcome:
        return _missing_outcome_response(
            path="/control/actions/task/retry",
            source_command=source_command,
            payload=payload,
            kind="retry_run",
            messages=messages,
            events=events,
            remediation="inspect the retry handler contract; dashboard actions now require structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"
    task_payload = None
    if isinstance(executed_task, dict) and executed_request_id:
        task_payload = {
            "request_id": executed_request_id,
            "label": gateway_task_view.task_display_label(executed_task, fallback_request_id=executed_request_id),
            "status": str(executed_task.get("status", "")).strip() or "-",
            "tf_phase": str(executed_task.get("tf_phase", "")).strip() or "-",
            "detail_path": f"/control/tasks/by-request/{executed_request_id}",
        }
    next_step = str(outcome.get("next_step", "")).strip() or ("/offdesk review" if blocked else (f"/task {task_payload['label']}" if isinstance(task_payload, dict) else "/monitor"))
    remediation = (
        "review the updated task detail and lane state before repeating another retry"
        if not blocked
        else "inspect the structured retry outcome and planning contract before repeating another retry"
    )
    reason_code = str(outcome.get("reason_code", "")).strip() or "-"
    detail_note = str(outcome.get("detail", "")).strip()
    if blocked:
        remediation = _retry_blocked_remediation_for_reason(reason_code, detail_note)
    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": True,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": "/control/actions/task/retry",
            "mode": "phase2",
            "source_command": source_command,
            "payload": payload,
            "transition": {
                "cmd": transition.get("cmd", "run"),
                "orch_target": transition.get("orch_target", "-"),
                "run_control_mode": transition.get("run_control_mode", "-"),
                "run_source_request_id": transition.get("run_source_request_id", "-"),
                "run_force_mode": transition.get("run_force_mode", "-"),
                "execution_lane_ids": list(transition.get("run_selected_execution_lane_ids") or []),
                "review_lane_ids": list(transition.get("run_selected_review_lane_ids") or []),
            },
            "messages": messages,
            "events": events,
            "outcome": {
                "kind": "retry_run",
                "status": "blocked" if blocked else "executed",
                "reason_code": reason_code,
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "task": task_payload,
            "next_step": next_step,
            "remediation": remediation,
        },
        status=409 if blocked else 200,
    )



def _execute_retry_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    task_ref = str(payload.get("task_ref", "")).strip()
    paths, manager_state = _load_dashboard_manager_state(config)
    project_key = _find_task_project_key(manager_state, task_ref)
    if not project_key:
        return _not_found_json(path=str(spec.get("path", "")).strip() or "-", message=f"task not found: {task_ref}")

    messages: List[Dict[str, Any]] = []
    transition = retry_handlers.resolve_retry_replan_transition(
        cmd="orch-retry",
        args=_dashboard_action_args(config),
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        orch_target=project_key,
        orch_retry_request_id=task_ref,
        orch_replan_request_id=None,
        orch_retry_lane_ids=list(payload.get("lane_ids") or []),
        orch_replan_lane_ids=None,
        send=_make_send_collector(messages),
        get_context=_dashboard_get_context_factory(manager_state, paths),
        get_chat_selected_task_ref=chat_state.get_chat_selected_task_ref,
        resolve_chat_task_ref=chat_state.resolve_chat_task_ref,
        resolve_task_request_id=gateway_task_state.resolve_task_request_id,
        get_task_record=gateway_task_state.get_task_record,
        run_request_query=lambda *_args, **_kwargs: {},
        sync_task_lifecycle=lambda **_kwargs: None,
        resolve_verifier_candidates=lambda _raw: [],
        dedupe_roles=gateway_task_view.dedupe_roles,
        touch_chat_recent_task_ref=chat_state.touch_chat_recent_task_ref,
        set_chat_selected_task_ref=chat_state.set_chat_selected_task_ref,
    )
    if not isinstance(transition, dict):
        return _json(
            {
                "ok": False,
                "error": "retry_transition_unavailable",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect the task lifecycle first; retry transition could not be derived from the current runtime state",
            },
            status=500,
        )

    if bool(transition.get("terminal")):
        return _json(
            {
                "ok": False,
                "implemented": True,
                "executed": False,
                "status": "blocked",
                "method": "POST",
                "path": spec.get("path", "-"),
                "mode": spec.get("mode", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "messages": messages,
                "next_step": "/offdesk review",
                "remediation": _retry_blocked_remediation([str(row.get("context", "")).strip() for row in messages if str(row.get("context", "")).strip()]),
            },
            status=409,
        )
    import sys

    compatibility_module = sys.modules.get("control_dashboard")
    execute_retry = getattr(compatibility_module, "_execute_retry_run_transition", _execute_retry_run_transition)
    return execute_retry(
        transition,
        config=config,
        manager_state=manager_state,
        paths=paths,
        source_command=str(spec.get("command", "")).strip() or "/retry",
        payload=payload,
    )



def _execute_auto_recover_action(spec: Dict[str, object], *, config: DashboardAppConfig) -> Tuple[int, Dict[str, str], bytes]:
    payload = spec.get("payload") if isinstance(spec.get("payload"), dict) else {}
    args = _dashboard_action_args(config)
    paths, manager_state = _load_dashboard_manager_state(config)
    messages: List[Dict[str, Any]] = []
    outcomes: List[Dict[str, Any]] = []

    handled = scheduler_control_handlers.handle_scheduler_control_command(
        cmd="auto",
        args=args,
        manager_state=manager_state,
        chat_id=_DASHBOARD_CHAT_ID,
        chat_role=_DASHBOARD_CHAT_ROLE,
        rest="recover force" if bool(payload.get("force")) else "recover",
        send=_make_send_collector(messages),
        get_default_mode=lambda *_args, **_kwargs: "",
        get_pending_mode=lambda *_args, **_kwargs: "",
        get_chat_report_level=lambda *_args, **_kwargs: "normal",
        get_chat_room=lambda *_args, **_kwargs: management_handlers.DEFAULT_OFFDESK_ROOM,
        set_default_mode=lambda *_args, **_kwargs: None,
        set_chat_report_level=lambda *_args, **_kwargs: None,
        set_chat_room=lambda *_args, **_kwargs: None,
        clear_default_mode=lambda *_args, **_kwargs: False,
        clear_pending_mode=lambda *_args, **_kwargs: False,
        clear_confirm_action=lambda *_args, **_kwargs: False,
        clear_chat_report_level=lambda *_args, **_kwargs: False,
        save_manager_state=lambda *_args, **_kwargs: None,
        resolve_project_entry=management_handlers._resolve_project_entry,
        project_lock_row=management_handlers._project_lock_row,
        project_lock_label=management_handlers._project_lock_label,
        parse_replace_sync_flag=management_handlers._parse_replace_sync_flag,
        normalize_prefetch_token=management_handlers._normalize_prefetch_token,
        prefetch_display=management_handlers._prefetch_display,
        compact_reason=management_handlers._compact_reason,
        status_report_level=management_handlers._status_report_level,
        focused_project_snapshot_lines=management_handlers._focused_project_snapshot_lines,
        ops_scope_summary=management_handlers._ops_scope_summary,
        ops_scope_compact_lines=lambda state, limit, detail_level: management_handlers._ops_scope_compact_lines(
            state,
            limit=limit,
            detail_level=detail_level,
        ),
        offdesk_prepare_targets=management_handlers._offdesk_prepare_targets,
        offdesk_prepare_project_report=management_handlers._offdesk_prepare_project_report,
        sort_offdesk_reports=management_handlers._sort_offdesk_reports,
        offdesk_review_reply_markup=lambda *_args, **_kwargs: {},
        offdesk_prepare_reply_markup=lambda *_args, **_kwargs: {},
        auto_state_path=management_handlers._auto_state_path,
        offdesk_state_path=management_handlers._offdesk_state_path,
        provider_capacity_state_path=management_handlers._provider_capacity_state_path,
        load_auto_state=management_handlers._load_auto_state,
        save_auto_state=management_handlers._save_auto_state,
        load_offdesk_state=management_handlers._load_offdesk_state,
        save_offdesk_state=management_handlers._save_offdesk_state,
        load_provider_capacity_state=management_handlers._load_provider_capacity_state,
        save_provider_capacity_state=management_handlers._save_provider_capacity_state,
        scheduler_session_name=management_handlers._scheduler_session_name,
        tmux_has_session=management_handlers._tmux_has_session,
        tmux_auto_command=management_handlers._tmux_auto_command,
        now_iso=management_handlers._now_iso,
        default_auto_interval_sec=management_handlers.DEFAULT_AUTO_INTERVAL_SEC,
        default_auto_idle_sec=management_handlers.DEFAULT_AUTO_IDLE_SEC,
        default_auto_max_failures=management_handlers.DEFAULT_AUTO_MAX_FAILURES,
        default_offdesk_command=management_handlers.DEFAULT_OFFDESK_COMMAND,
        default_offdesk_prefetch=management_handlers.DEFAULT_OFFDESK_PREFETCH,
        default_offdesk_prefetch_since=management_handlers.DEFAULT_OFFDESK_PREFETCH_SINCE,
        default_offdesk_report_level=management_handlers.DEFAULT_OFFDESK_REPORT_LEVEL,
        default_offdesk_room=management_handlers.DEFAULT_OFFDESK_ROOM,
        record_outcome=lambda row: outcomes.append(dict(row)) if isinstance(row, dict) else None,
    )

    if not handled:
        return _json(
            {
                "ok": False,
                "error": "auto_recover_unhandled",
                "path": spec.get("path", "-"),
                "source_command": spec.get("command", "-"),
                "payload": payload,
                "remediation": "inspect /auto status and provider capacity state before retrying auto recover",
            },
            status=500,
        )

    auto_state = management_handlers._load_auto_state(management_handlers._auto_state_path(args))
    provider_state = management_handlers._load_provider_capacity_state(management_handlers._provider_capacity_state_path(args))
    outcome = _latest_recorded_outcome(outcomes, kind="auto_recover")
    if not outcome:
        return _missing_outcome_response(
            path=str(spec.get("path", "-")),
            source_command=str(spec.get("command", "-")),
            payload=payload,
            kind="auto_recover",
            messages=messages,
            events=[],
            remediation="inspect the auto recover handler contract; dashboard actions now require structured outcome rows",
        )
    blocked = str(outcome.get("status", "")).strip() == "blocked"

    return _json(
        {
            "ok": not blocked,
            "implemented": True,
            "executed": not blocked,
            "status": "blocked" if blocked else "executed",
            "method": "POST",
            "path": spec.get("path", "-"),
            "mode": spec.get("mode", "-"),
            "source_command": spec.get("command", "-"),
            "payload": payload,
            "messages": messages,
            "outcome": {
                "kind": "auto_recover",
                "status": "blocked" if blocked else "executed",
                "reason_code": str(outcome.get("reason_code", "")).strip() if outcome else "-",
                "detail": str(outcome.get("detail", "")).strip() if outcome else "-",
            },
            "auto_state": {
                "enabled": bool(auto_state.get("enabled", False)),
                "command": str(auto_state.get("command", "")).strip() or "-",
                "recovered_at": str(auto_state.get("recovered_at", "")).strip() or "-",
                "recovery_grace_until": str(auto_state.get("recovery_grace_until", "")).strip() or "-",
            },
            "provider_capacity": {
                "next_retry_at": str(provider_state.get("next_retry_at", "")).strip() or "-",
                "repeat_count": int(provider_state.get("recovery_repeat_count", 0) or 0),
            },
            "team_dir": str(paths.team_dir),
            "next_step": str(outcome.get("next_step", "")).strip() or ("/auto status" if not blocked else "/offdesk review"),
            "remediation": _auto_recover_remediation(blocked=blocked, provider_state=provider_state),
        },
        status=409 if blocked else 200,
    )
