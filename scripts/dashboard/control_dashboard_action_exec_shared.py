#!/usr/bin/env python3
"""Shared execution glue for dashboard mutation actions."""

from __future__ import annotations

import importlib.util
import shutil
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import aoe_tg_chat_state as chat_state
import aoe_tg_parse as parse_mod
import aoe_tg_request_state as request_state_mod
import aoe_tg_run_handlers as run_handlers
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as gateway_task_state
import aoe_tg_task_view as gateway_task_view

from control_dashboard_common import ROOT, DashboardAppConfig, _dashboard_paths, _json

_DASHBOARD_CHAT_ID = "dashboard-http"
_DASHBOARD_CHAT_ROLE = "owner"


@lru_cache(maxsize=1)
def _load_gateway_main_module():
    module_path = ROOT / "scripts" / "gateway" / "aoe-telegram-gateway.py"
    spec = importlib.util.spec_from_file_location("aoe_telegram_gateway_main", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load gateway main module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



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
