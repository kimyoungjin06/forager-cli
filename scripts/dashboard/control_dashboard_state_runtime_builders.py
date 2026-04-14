#!/usr/bin/env python3
"""Runtime-scoped dashboard state builders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_offdesk_flow as offdesk_flow
import aoe_tg_ops_policy as ops_policy
import aoe_tg_action_audit as action_audit
import aoe_tg_background_runs as background_runs
import aoe_tg_context_pack as context_pack
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_document_registry as document_registry
import aoe_tg_run_lock as run_lock
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as task_state
import aoe_tg_worker_task_contract as worker_task_contract
import aoe_tg_workspace_brief as workspace_brief

from control_dashboard_state_common import (
    _append_unique_action_button,
    _background_scheduler_note,
    _compose_backend_summary,
    _compose_phase2_quality,
    _compose_phase2_shape,
    _completion_contract_for_preset,
    _detail_path,
    _provider_repeat_counts,
    _recovery_control_action_buttons,
    _replan_manual_route_action_button,
    _replan_auto_route_action_button,
    _runtime_action_buttons,
    _runtime_command_contract,
    _runtime_path,
    _runtime_reports,
    _task_action_buttons,
    _task_command_contract,
    _task_followup_summary,
    _task_rerun_summary,
    _worker_apply_proposal_button,
    _worker_apply_preview_button,
    _worker_apply_proposal_accept_button,
    _worker_apply_syncback_apply_button,
    _worker_apply_syncback_preview_button,
    _worker_update_proposal_accept_button,
    _worker_update_preview_button,
)
from control_dashboard_state_models import RuntimeCardDTO, RuntimeDetailDTO


def _worker_update_operator_summary(task: Dict[str, Any]) -> str:
    return worker_task_contract.summarize_worker_update_operator_summary(
        {
            "status": task.get("background_run_worker_update_stub_status"),
            "summary_line": task.get("background_run_worker_update_stub_summary"),
            "target_artifacts": task.get("background_run_worker_update_stub_targets"),
        },
        task.get("background_run_worker_update_proposal_ids"),
    )


def _worker_module_summary(task: Dict[str, Any]) -> str:
    module_summary = str(task.get("background_run_task_contract_module_summary", "")).strip()
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_summary:
        return module_summary
    if module_kind in {"", "-", "general"}:
        return "-"
    return module_kind


def _worker_policy_summary(task: Dict[str, Any]) -> str:
    summary = str(task.get("background_run_task_contract_policy_summary", "")).strip()
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind in {"", "-", "general"}:
        return "-"
    if summary:
        return summary
    return (
        str(worker_task_contract.resolve_worker_module_policy({"module_kind": module_kind}).get("summary", "")).strip()
        or "-"
    )


def _worker_gate_summary(task: Dict[str, Any]) -> str:
    summary = str(task.get("background_run_worker_gate_summary", "")).strip()
    if summary:
        return summary
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind in {"", "-", "general"}:
        return "-"
    derived = worker_task_contract.derive_worker_task_module_gate(
        {
            "module_kind": module_kind,
            "module_policy": task.get("background_run_task_contract_policy"),
            "artifact_targets": task.get("background_run_worker_update_stub_targets"),
        },
        {
            "status": task.get("background_run_worker_result_status"),
            "summary": task.get("background_run_worker_result_summary"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        },
    )
    return str(derived.get("summary_line", "")).strip() or "-"


def _worker_profile_summary(task: Dict[str, Any]) -> str:
    summary = str(task.get("background_run_worker_profile_summary", "")).strip()
    if summary:
        return summary
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind in {"", "-", "general"}:
        return "-"
    derived = worker_task_contract.derive_worker_task_module_profile(
        {
            "module_kind": module_kind,
            "module_policy": task.get("background_run_task_contract_policy"),
            "artifact_targets": task.get("background_run_worker_update_stub_targets"),
        },
        {
            "status": task.get("background_run_worker_result_status"),
            "summary": task.get("background_run_worker_result_summary"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        },
        gate={
            "state": task.get("background_run_worker_gate_status"),
            "summary_line": _worker_gate_summary(task),
        },
    )
    return str(derived.get("summary_line", "")).strip() or "-"


def _worker_checklist_summary(task: Dict[str, Any]) -> str:
    summary = str(task.get("background_run_worker_checklist_summary", "")).strip()
    if summary:
        return summary
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind in {"", "-", "general"}:
        return "-"
    derived = worker_task_contract.derive_worker_task_module_checklist(
        {
            "module_kind": module_kind,
            "module_policy": task.get("background_run_task_contract_policy"),
            "artifact_targets": task.get("background_run_worker_update_stub_targets"),
        },
        {
            "status": task.get("background_run_worker_result_status"),
            "summary": task.get("background_run_worker_result_summary"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        },
        gate={
            "state": task.get("background_run_worker_gate_status"),
            "summary_line": _worker_gate_summary(task),
        },
        profile={
            "state": task.get("background_run_worker_profile_status"),
            "summary_line": _worker_profile_summary(task),
        },
    )
    return str(derived.get("summary_line", "")).strip() or "-"


def _worker_items_summary(task: Dict[str, Any]) -> str:
    summary = str(task.get("background_run_worker_items_summary", "")).strip()
    if summary:
        return summary
    module_kind = str(task.get("background_run_task_contract_module", "")).strip().lower()
    if module_kind in {"", "-", "general"}:
        return "-"
    derived = worker_task_contract.derive_worker_task_module_items(
        {
            "module_kind": module_kind,
            "module_policy": task.get("background_run_task_contract_policy"),
            "artifact_targets": task.get("background_run_worker_update_stub_targets"),
        },
        {
            "status": task.get("background_run_worker_result_status"),
            "summary": task.get("background_run_worker_result_summary"),
            "actions": task.get("background_run_worker_result_actions"),
            "cautions": task.get("background_run_worker_result_cautions"),
            "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
        },
        gate={
            "state": task.get("background_run_worker_gate_status"),
            "summary_line": _worker_gate_summary(task),
        },
        profile={
            "state": task.get("background_run_worker_profile_status"),
            "summary_line": _worker_profile_summary(task),
        },
        checklist={
            "state": task.get("background_run_worker_checklist_status"),
            "summary_line": _worker_checklist_summary(task),
        },
    )
    return str(derived.get("summary_line", "")).strip() or "-"


def _worker_items(task: Dict[str, Any]) -> str:
    rows = [
        str(item).strip()
        for item in (
            (task.get("background_run_worker_items") if isinstance(task.get("background_run_worker_items"), list) else [])
            or []
        )
        if str(item).strip()
    ]
    return ", ".join(rows[:6])[:320] if rows else "-"


def _worker_apply_accept_summary(task: Dict[str, Any]) -> str:
    return str(task.get("background_run_worker_apply_accept_summary", "")).strip() or "-"


def _worker_syncback_summary(task: Dict[str, Any]) -> str:
    return str(task.get("background_run_worker_syncback_summary", "")).strip() or "-"


def _manual_step_execution_summary(task: Dict[str, Any]) -> str:
    return str(task.get("background_run_manual_step_execution_summary", "")).strip() or "-"


def _canonical_writeback_summary(task: Dict[str, Any]) -> str:
    return str(task.get("background_run_canonical_writeback_summary", "")).strip() or "-"


def _canonical_mutation_summary(task: Dict[str, Any]) -> str:
    return str(task.get("background_run_canonical_mutation_summary", "")).strip() or "-"


def _worker_syncback_applied(task: Dict[str, Any]) -> bool:
    if str(task.get("background_run_worker_syncback_status", "")).strip() != "applied":
        return False
    sync_at = str(task.get("background_run_worker_syncback_at", "")).strip()
    accept_at = str(task.get("background_run_worker_apply_accept_at", "")).strip()
    if sync_at and accept_at:
        return sync_at >= accept_at
    return True

from control_dashboard_state_task_builders import _build_runtime_recent_task_rows


def _preferred_slot_runner(entry: Dict[str, Any]) -> str:
    token = str(entry.get("background_runner_target", "")).strip().lower()
    return token if token in background_runs.SLOT_RUNNER_TARGETS else ""


def _background_slot_snapshot(entry: Dict[str, Any], team_dir: Path) -> Dict[str, Any]:
    return background_runs.summarize_background_runner_slots(
        background_runs.background_runs_state_path(team_dir),
        entry,
        selected_runner=_preferred_slot_runner(entry),
        statuses=["queued", "dispatching", "running"],
    )


def _latest_judge_summary(team_dir: Path, *, project_alias: str) -> str:
    row = action_audit.load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=project_alias,
        outcome_kind="offdesk_judge",
    )
    if not row:
        return "-"
    headline = str(row.get("headline", "")).strip() or "Offdesk Judge"
    next_step = str(row.get("next_step", "")).strip() or "-"
    detail = str(row.get("outcome_detail", "")).strip() or "-"
    return f"{headline} | next={next_step} | {detail}"


def _latest_judge_decision_summary(team_dir: Path, *, project_alias: str) -> str:
    return action_audit.load_latest_offdesk_judge_decision_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_judge_decision_bridge_summary(team_dir: Path, *, project_alias: str) -> str:
    return action_audit.load_latest_judge_decision_bridge_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_decision_summary(team_dir: Path, *, project_alias: str) -> str:
    return action_audit.load_latest_replan_auto_decision_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_routing_policy_summary(team_dir: Path, *, project_alias: str) -> str:
    return action_audit.load_latest_replan_auto_routing_policy_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_route_summary(team_dir: Path, *, project_alias: str) -> str:
    row = action_audit.load_latest_action_audit_for_runtime_kind(
        team_dir,
        project_alias=project_alias,
        outcome_kind="replan_auto_route",
    )
    if not row:
        return "-"
    return "{headline} | next={next_step} | {detail}".format(
        headline=str(row.get("headline", "")).strip() or "Replan Auto Route",
        next_step=str(row.get("next_step", "")).strip() or "-",
        detail=str(row.get("outcome_detail", "")).strip() or "-",
    )


def _latest_manual_step_summary(team_dir: Path, *, project_alias: str, active_task: Dict[str, Any] | None = None) -> str:
    policy_summary = action_audit.load_latest_manual_step_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )
    if policy_summary not in {"", "-"}:
        return policy_summary
    task_summary = _manual_step_execution_summary(active_task or {})
    if task_summary not in {"", "-"}:
        return task_summary
    return policy_summary


def _latest_canonical_writeback_summary(team_dir: Path, *, project_alias: str, active_task: Dict[str, Any] | None = None) -> str:
    task_summary = _canonical_writeback_summary(active_task or {})
    if task_summary not in {"", "-"}:
        return task_summary
    return action_audit.load_latest_canonical_writeback_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_canonical_mutation_summary(team_dir: Path, *, project_alias: str, active_task: Dict[str, Any] | None = None) -> str:
    task_summary = _canonical_mutation_summary(active_task or {})
    if task_summary not in {"", "-"}:
        return task_summary
    return action_audit.load_latest_canonical_mutation_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _build_runtime_cards(manager_state: Dict[str, Any], provider_state: Dict[str, Any], *, root_team_dir: Path) -> List[RuntimeCardDTO]:
    reports = _runtime_reports(manager_state, provider_state)

    cards: List[RuntimeCardDTO] = []
    for row in reports:
        phase1_preset = str(row.get("active_task_phase1_role_preset", "")).strip()
        phase2_preset = str(row.get("active_task_phase2_team_preset", "")).strip()
        alias = str(row.get("alias", "")).strip()
        active_request_id = str(row.get("active_task_request_id", "")).strip()
        active_task: Optional[Dict[str, Any]] = None
        resolved = _resolve_runtime_entry(manager_state, alias)
        if resolved is not None and active_request_id:
            _key, entry = resolved
            candidate = task_state.get_task_record(entry, active_request_id)
            if isinstance(candidate, dict):
                active_task = candidate
        queue_summary = "-"
        scheduler_summary = "-"
        queue_depth = 0
        queue_stale_count = 0
        worker_status = "-"
        worker_summary = "-"
        model_routing_summary = "-"
        model_registry_summary = "-"
        latest_judge_summary = "-"
        latest_judge_decision_summary = "-"
        latest_judge_decision_bridge_summary = "-"
        latest_replan_auto_decision_summary = "-"
        latest_replan_auto_routing_policy_summary = "-"
        latest_replan_auto_route_summary = "-"
        latest_replan_auto_route_status_summary = "-"
        latest_replan_auto_operator_summary = "-"
        latest_manual_step_summary = "-"
        latest_canonical_writeback_summary = "-"
        latest_canonical_mutation_summary = "-"
        latest_replan_auto_routing_policy: Dict[str, Any] = {}
        workspace_summary = "-"
        document_registry_summary = "-"
        active_task_context_pack_summary = "-"
        active_task_model_plan_summary = "-"
        run_lock_mode = "open"
        run_lock_note = "-"
        background_slot_limit = 1
        background_slot_active = 0
        background_slot_pressure = "not_applicable"
        if resolved is not None:
            _key, entry = resolved
            team_dir = Path(str(entry.get("team_dir", "")).strip() or ".")
            run_lock_mode = run_lock.project_run_lock_mode(entry)
            run_lock_note = run_lock.project_run_lock_note(entry) or "-"
            if str(entry.get("team_dir", "")).strip():
                snapshot = background_runs.summarize_background_runs_state(
                    background_runs.background_runs_state_path(team_dir)
                )
                scheduler_snapshot = background_runs.summarize_background_runner_scheduling(
                    background_runs.background_runs_state_path(team_dir),
                    now_iso=lambda: runtime_read.now_iso(),
                )
                queue_summary = str(snapshot.get("summary", "")).strip() or "-"
                scheduler_summary = str(scheduler_snapshot.get("summary", "")).strip() or "-"
                queue_depth = int(snapshot.get("depth", 0) or 0)
                queue_stale_count = int(snapshot.get("stale_count", 0) or 0)
                slot_snapshot = _background_slot_snapshot(entry, team_dir)
                background_slot_limit = int(slot_snapshot.get("selected_limit", 1) or 1)
                background_slot_active = int(slot_snapshot.get("selected_active", 0) or 0)
                background_slot_pressure = (
                    (str(slot_snapshot.get("selected_pressure", "")).strip() or "not_applicable")
                    + " | "
                    + (str(slot_snapshot.get("summary", "")).strip() or "-")
                )
                worker_snapshot = background_runs.summarize_background_worker_state(
                    background_runs.background_worker_state_path(team_dir)
                )
                worker_status = str(worker_snapshot.get("status", "")).strip() or "-"
                worker_summary = str(worker_snapshot.get("summary", "")).strip() or "-"
                workspace_summary = workspace_brief.summarize_workspace_brief(
                    team_dir,
                    entry=entry,
                    project_root=entry.get("project_root"),
                )
                document_registry_summary = document_registry.summarize_document_registry(
                    team_dir,
                    entry=entry,
                    project_root=entry.get("project_root"),
                )
                model_routing_summary = model_endpoint_adapter.summarize_model_routing(team_dir, entry=entry)
                model_registry_summary = model_endpoint_adapter.summarize_model_endpoint_registry(team_dir, entry=entry)
                latest_judge_summary = _latest_judge_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_judge_decision_summary = _latest_judge_decision_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_judge_decision_bridge_summary = _latest_judge_decision_bridge_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_replan_auto_decision_summary = _latest_replan_auto_decision_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_replan_auto_routing_policy_summary = _latest_replan_auto_routing_policy_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_replan_auto_route_summary = _latest_replan_auto_route_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                latest_replan_auto_route_status_summary = (
                    action_audit.load_latest_replan_auto_route_status_summary_for_runtime(
                        root_team_dir,
                        project_alias=str(entry.get("project_alias", "")).strip(),
                    )
                )
                latest_replan_auto_operator_summary = (
                    action_audit.load_latest_replan_auto_operator_summary_for_runtime(
                        root_team_dir,
                        project_alias=str(entry.get("project_alias", "")).strip(),
                    )
                )
                latest_manual_step_summary = _latest_manual_step_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                    active_task=active_task or {},
                )
                latest_canonical_writeback_summary = _latest_canonical_writeback_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                    active_task=active_task or {},
                )
                latest_canonical_mutation_summary = _latest_canonical_mutation_summary(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                    active_task=active_task or {},
                )
                latest_replan_auto_routing_policy = action_audit.load_latest_replan_auto_routing_policy_for_runtime(
                    root_team_dir,
                    project_alias=str(entry.get("project_alias", "")).strip(),
                )
                if isinstance(active_task, dict):
                    pack = context_pack.load_context_pack(
                        team_dir,
                        entry=entry,
                        task=active_task,
                        project_root=entry.get("project_root"),
                    )
                    model_plan = model_endpoint_adapter.resolve_task_model_plan(team_dir, entry=entry, task=active_task)
                    active_task_context_pack_summary = str(pack.get("summary", "")).strip() or "-"
                    active_task_model_plan_summary = str(model_plan.get("summary", "")).strip() or "-"
        active_rate_limit_summary = _runtime_active_task_rate_limit_summary(row)
        runtime_action_contract = _runtime_command_contract(
            project_alias=alias,
            priority_action=str(row.get("priority_action", "")).strip(),
            has_active_task=bool(active_request_id),
            has_rate_limit=active_rate_limit_summary != "-",
            background_queue_stale_count=queue_stale_count,
        )
        runtime_safe_action_buttons, runtime_phase2_action_buttons = _runtime_action_buttons(
            project_alias=alias,
            phase2_commands=list(runtime_action_contract.get("phase2") or []),
        )
        runtime_phase2_action_buttons = _append_unique_action_button(
            runtime_phase2_action_buttons,
            _replan_auto_route_action_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                policy=latest_replan_auto_routing_policy,
            ),
        )
        runtime_manual_route_button = _replan_manual_route_action_button(
            project_alias=alias,
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=latest_replan_auto_routing_policy,
        )
        if runtime_manual_route_button is not None and str(runtime_manual_route_button.mode).strip() == "phase2":
            runtime_phase2_action_buttons = _append_unique_action_button(runtime_phase2_action_buttons, runtime_manual_route_button)
        else:
            runtime_safe_action_buttons = _append_unique_action_button(runtime_safe_action_buttons, runtime_manual_route_button)
        worker_apply_applied = str((active_task or {}).get("background_run_worker_apply_accept_status", "")).strip() == "applied"
        worker_syncback_applied = _worker_syncback_applied(active_task or {})
        if not worker_apply_applied:
            runtime_safe_action_buttons = _append_unique_action_button(
                runtime_safe_action_buttons,
                _worker_apply_preview_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                        "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                        "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                        "actions": (active_task or {}).get("background_run_worker_result_actions"),
                        "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                        "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
                    },
                    proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
                ),
            )
        elif not worker_syncback_applied:
            runtime_safe_action_buttons = _append_unique_action_button(
                runtime_safe_action_buttons,
                _worker_apply_syncback_preview_button(project_alias=alias),
            )
        runtime_safe_action_buttons = _append_unique_action_button(
            runtime_safe_action_buttons,
            _worker_update_preview_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                update_stub={
                    "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                    "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                    "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                    "actions": (active_task or {}).get("background_run_worker_result_actions"),
                    "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                    "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not worker_apply_applied:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_proposal_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                        "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                        "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                        "actions": (active_task or {}).get("background_run_worker_result_actions"),
                        "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                        "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
                    },
                    proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
                ),
            )
        runtime_phase2_action_buttons = _append_unique_action_button(
            runtime_phase2_action_buttons,
            _worker_update_proposal_accept_button(
                project_alias=alias,
                proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not worker_apply_applied:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_proposal_accept_button(
                    label=str((active_task or {}).get("short_id", "")).strip() or str((active_task or {}).get("alias", "")).strip(),
                    request_id=str((active_task or {}).get("request_id", "")).strip(),
                    project_alias=alias,
                    proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
                    proposal_summary=(active_task or {}).get("background_run_worker_update_proposal_summary"),
                ),
            )
        elif not worker_syncback_applied:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_syncback_apply_button(project_alias=alias),
            )
        cards.append(
            RuntimeCardDTO(
                project_key=str(row.get("key", "")).strip() or str(row.get("alias", "")).strip(),
                project_alias=alias,
                project_label=str(row.get("display", "")).strip() or alias,
                runtime_path=_runtime_path(alias),
                status=str(row.get("status", "ready")).strip(),
                readiness=str(row.get("runtime_label", row.get("runtime", "ready"))).strip() or "ready",
                attention_summary=str(row.get("attention_summary", "-")).strip() or "-",
                priority_action=str(row.get("priority_action", "")).strip() or "-",
                priority_reason=str(row.get("priority_reason", "")).strip() or "-",
                next_focus=offdesk_flow.preset_next_focus(phase1_preset, phase2_preset),
                severity_score=int(row.get("severity_score", 0) or 0),
                provider_pressure_score=int(row.get("capacity_pressure_score", 0) or 0),
                provider_repeat_count=int(row.get("capacity_repeat_count", 0) or 0),
                active_task_request_id=active_request_id,
                active_task_label=str(row.get("active_task_label", "")).strip(),
                active_task_phase=str(row.get("active_task_tf_phase", "")).strip(),
                active_task_status=str(row.get("active_task_status", "")).strip(),
                active_task_preset=f"phase1={phase1_preset or '-'} phase2={phase2_preset or phase1_preset or '-'}" if (phase1_preset or phase2_preset) else "-",
                active_task_phase2_shape=_compose_phase2_shape(
                    row.get("active_task_phase2_execution_roles") or [],
                    row.get("active_task_phase2_review_roles") or [],
                ),
                active_task_phase2_quality=_compose_phase2_quality(row),
                active_task_backend=_compose_backend_summary(
                    str(row.get("active_task_backend", "")).strip(),
                    str(row.get("active_task_backend_profile", "")).strip(),
                    str(row.get("active_task_backend_verdict", "")).strip(),
                    str(row.get("active_task_backend_contract", "")).strip(),
                ),
                active_task_execution_brief_status=(
                    str((active_task or {}).get("execution_brief_status", "")).strip() or "-"
                ),
                active_task_execution_brief_summary=(
                    str((active_task or {}).get("execution_brief_summary", "")).strip() or "-"
                ),
                active_task_execution_brief_executable_slice=", ".join(
                    str(item).strip()
                    for item in ((active_task or {}).get("execution_brief_executable_slice") or [])
                    if str(item).strip()
                )
                or "-",
                active_task_execution_brief_blocked_slice=", ".join(
                    str(item).strip()
                    for item in ((active_task or {}).get("execution_brief_blocked_slice") or [])
                    if str(item).strip()
                )
                or "-",
                active_task_execution_brief_operator_decision=(
                    str((active_task or {}).get("execution_brief_operator_decision", "")).strip() or "-"
                ),
                active_task_followup_brief_status=(
                    str((active_task or {}).get("followup_brief_status", "")).strip() or "-"
                ),
                active_task_followup_brief_summary=(
                    str((active_task or {}).get("followup_brief_summary", "")).strip() or "-"
                ),
                active_task_followup_brief_execution_lanes=", ".join(
                    str(item).strip()
                    for item in ((active_task or {}).get("followup_brief_execution_lane_ids") or [])
                    if str(item).strip()
                )
                or "-",
                active_task_followup_brief_review_lanes=", ".join(
                    str(item).strip()
                    for item in ((active_task or {}).get("followup_brief_review_lane_ids") or [])
                    if str(item).strip()
                )
                or "-",
                active_task_followup_brief_reason=(
                    str((active_task or {}).get("followup_brief_reason", "")).strip() or "-"
                ),
                active_task_context_pack_summary=active_task_context_pack_summary,
                active_task_model_plan_summary=active_task_model_plan_summary,
                active_task_reentry_rails_summary=(
                    str((active_task or {}).get("reentry_rails_summary", "")).strip() or "-"
                ),
                active_task_background_run_status=(
                    str((active_task or {}).get("background_run_status", "")).strip() or "-"
                ),
                active_task_background_run_runner_target=(
                    str((active_task or {}).get("background_run_runner_target", "")).strip() or "-"
                ),
                active_task_background_run_ticket_id=(
                    str((active_task or {}).get("background_run_ticket_id", "")).strip() or "-"
                ),
                active_task_background_run_runtime_handle=(
                    str((active_task or {}).get("background_run_runtime_handle", "")).strip() or "-"
                ),
                active_task_background_run_runtime_summary=(
                    str((active_task or {}).get("background_run_runtime_summary", "")).strip() or "-"
                ),
                active_task_background_run_external_phase=(
                    str((active_task or {}).get("background_run_external_phase", "")).strip() or "-"
                ),
                active_task_background_run_external_note=(
                    str((active_task or {}).get("background_run_external_note", "")).strip() or "-"
                ),
                active_task_background_run_evidence_bundle=(
                    str((active_task or {}).get("background_run_evidence_bundle", "")).strip() or "-"
                ),
                active_task_background_run_evidence_artifacts=", ".join(
                    str(item).strip()
                    for item in ((active_task or {}).get("background_run_evidence_artifacts") or [])
                    if str(item).strip()
                )
                or "-",
                active_task_background_run_launch_spec_summary=(
                    str((active_task or {}).get("background_run_launch_spec_summary", "")).strip() or "-"
                ),
                active_task_background_run_worker_update_operator_summary=_worker_update_operator_summary(active_task or {}),
                active_task_background_run_worker_update_proposal_summary=(
                    str((active_task or {}).get("background_run_worker_update_proposal_summary", "")).strip() or "-"
                ),
                active_task_background_run_worker_apply_accept_summary=_worker_apply_accept_summary(active_task or {}),
                active_task_background_run_worker_syncback_summary=_worker_syncback_summary(active_task or {}),
                active_task_background_run_model_plan_summary=(
                    str((active_task or {}).get("background_run_model_plan_summary", "")).strip() or "-"
                ),
                workspace_summary=workspace_summary,
                document_registry_summary=document_registry_summary,
                model_routing_summary=model_routing_summary,
                model_registry_summary=model_registry_summary,
                latest_judge_summary=latest_judge_summary,
                latest_judge_decision_summary=latest_judge_decision_summary,
                latest_judge_decision_bridge_summary=latest_judge_decision_bridge_summary,
                latest_replan_auto_decision_summary=latest_replan_auto_decision_summary,
                latest_replan_auto_routing_policy_summary=latest_replan_auto_routing_policy_summary,
                latest_replan_auto_route_summary=latest_replan_auto_route_summary,
                latest_replan_auto_route_status_summary=latest_replan_auto_route_status_summary,
                latest_replan_auto_operator_summary=latest_replan_auto_operator_summary,
                latest_manual_step_summary=latest_manual_step_summary,
                latest_canonical_writeback_summary=latest_canonical_writeback_summary,
                latest_canonical_mutation_summary=latest_canonical_mutation_summary,
                run_lock_mode=run_lock_mode,
                run_lock_note=run_lock_note,
                background_slot_limit=background_slot_limit,
                background_slot_active=background_slot_active,
                background_slot_pressure=background_slot_pressure,
                background_worker_status=worker_status,
                background_worker_summary=worker_summary,
                background_queue_summary=queue_summary,
                background_scheduler_summary=scheduler_summary,
                background_scheduler_note=_background_scheduler_note(scheduler_summary),
                background_queue_depth=queue_depth,
                background_queue_stale_count=queue_stale_count,
                runtime_safe_action_buttons=runtime_safe_action_buttons,
                runtime_phase2_action_buttons=runtime_phase2_action_buttons,
                notes=list(row.get("notes") or []),
                lines=list(row.get("lines") or []),
            )
        )
    cards.sort(key=_runtime_card_sort_key)
    return cards


def _runtime_card_sort_key(card: RuntimeCardDTO) -> tuple[int, int, int, int, int, int, int, str]:
    brief_status = str(card.active_task_execution_brief_status or "").strip().lower()
    brief_blocked = 1 if brief_status in {"underspecified", "operator_decision_required", "infeasible"} else 0
    slot_saturated = 1 if int(card.background_slot_active or 0) >= max(1, int(card.background_slot_limit or 1)) else 0
    return (
        -int(card.background_queue_stale_count or 0),
        -slot_saturated,
        -int(card.background_queue_depth or 0),
        -brief_blocked,
        -int(card.severity_score or 0),
        -int(card.provider_pressure_score or 0),
        -int(card.provider_repeat_count or 0),
        str(card.project_alias or "").strip(),
    )



def _runtime_detail_sync_summary(row: Dict[str, Any]) -> str:
    syncback = row.get("syncback_counts") if isinstance(row.get("syncback_counts"), dict) else {}
    return (
        "quality={quality} | syncback done={done} reopen={reopen} append={append} blocked={blocked}".format(
            quality=str(row.get("sync_quality", "")).strip() or "-",
            done=int(syncback.get("done", 0) or 0),
            reopen=int(syncback.get("reopen", 0) or 0),
            append=int(syncback.get("append", 0) or 0),
            blocked=int(syncback.get("blocked", 0) or 0),
        )
    )


def _runtime_detail_queue_summary(row: Dict[str, Any]) -> str:
    return "open={open} running={running} blocked={blocked} followup={followup} pending={pending}".format(
        open=int(row.get("open", 0) or 0),
        running=int(row.get("running", 0) or 0),
        blocked=int(row.get("blocked_count", 0) or 0),
        followup=int(row.get("followup_count", 0) or 0),
        pending="yes" if bool(row.get("pending_flag", False)) else "no",
    )


def _runtime_detail_proposal_summary(row: Dict[str, Any]) -> str:
    triage = row.get("proposal_triage") if isinstance(row.get("proposal_triage"), dict) else {}
    return "open={open} | priorities={priorities} | kinds={kinds}".format(
        open=int(row.get("proposals", 0) or 0),
        priorities=str(triage.get("priority_summary", "-")).strip() or "-",
        kinds=str(triage.get("kind_summary", "-")).strip() or "-",
    )


def _runtime_detail_provider_pressure_summary(row: Dict[str, Any]) -> str:
    retry_wait_sec = int(row.get("capacity_retry_wait_sec", 0) or 0)
    retry_wait = f"{retry_wait_sec}s" if retry_wait_sec > 0 else "-"
    return "score={score} | providers={providers} | retry_wait={wait}".format(
        score=int(row.get("capacity_pressure_score", 0) or 0),
        providers=int(row.get("capacity_provider_count", 0) or 0),
        wait=retry_wait,
    )


def _runtime_detail_repeat_summary(row: Dict[str, Any]) -> str:
    count = int(row.get("capacity_repeat_count", 0) or 0)
    if count <= 0:
        return "-"
    return f"repeat_count={count}"


def _runtime_active_task_rate_limit_summary(row: Dict[str, Any]) -> str:
    rate_limit = row.get("active_task_rate_limit") if isinstance(row.get("active_task_rate_limit"), dict) else {}
    if not rate_limit:
        return "-"
    providers = [str(x).strip() for x in (rate_limit.get("limited_providers") or []) if str(x).strip()]
    retry_at = str(rate_limit.get("retry_at", "")).strip() or "-"
    retry_after = int(rate_limit.get("retry_after_sec", 0) or 0)
    degraded = [str(x).strip() for x in (row.get("active_task_degraded_by") or []) if str(x).strip()]
    return "mode={mode} providers={providers} retry_after={retry_after} retry_at={retry_at} degraded={degraded}".format(
        mode=str(rate_limit.get("mode", "")).strip() or "-",
        providers=",".join(providers) if providers else "-",
        retry_after=(f"{retry_after}s" if retry_after > 0 else "-"),
        retry_at=retry_at,
        degraded=",".join(degraded) if degraded else "-",
    )


def _resolve_runtime_entry(manager_state: Dict[str, Any], project_alias: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    token = str(project_alias or "").strip().upper()
    if not token:
        return None
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        if ops_policy.project_alias(entry, str(key)).upper() == token:
            return str(key), entry
    return None


def _build_runtime_detail(
    manager_state: Dict[str, Any],
    provider_state: Dict[str, Any],
    project_alias: str,
    *,
    root_team_dir: Path | str | None = None,
) -> Optional[RuntimeDetailDTO]:
    resolved = _resolve_runtime_entry(manager_state, project_alias)
    if resolved is None:
        return None
    key, entry = resolved
    reports = _runtime_reports(manager_state, provider_state)
    target_alias = ops_policy.project_alias(entry, key).upper()
    row = next((report for report in reports if str(report.get("alias", "")).strip().upper() == target_alias), None)
    if row is None:
        return None
    queue_snapshot = ops_policy.project_queue_snapshot(entry)
    completed_task_count = 0
    tasks = task_state.ensure_project_tasks(entry)
    for task in tasks.values():
        if not isinstance(task, dict):
            continue
        if runtime_read.normalize_task_status(task.get("status", "pending")) == "completed":
            completed_task_count += 1
    display = str(row.get("display", "")).strip() or target_alias
    phase1_preset = str(row.get("active_task_phase1_role_preset", "")).strip()
    phase2_preset = str(row.get("active_task_phase2_team_preset", "")).strip()
    active_contract = _completion_contract_for_preset(phase2_preset or phase1_preset)
    runtime_path = _runtime_path(target_alias)
    active_request_id = str(row.get("active_task_request_id", "")).strip()
    active_task = task_state.get_task_record(entry, active_request_id) if active_request_id else None
    if str(entry.get("team_dir", "")).strip():
        team_dir = Path(str(entry.get("team_dir", "")).strip() or ".")
        queue_snapshot = background_runs.summarize_background_runs_state(
            background_runs.background_runs_state_path(team_dir)
        )
        scheduler_snapshot = background_runs.summarize_background_runner_scheduling(
            background_runs.background_runs_state_path(team_dir),
            now_iso=lambda: runtime_read.now_iso(),
        )
        slot_snapshot = _background_slot_snapshot(entry, team_dir)
        background_slot_limit = int(slot_snapshot.get("selected_limit", 1) or 1)
        background_slot_active = int(slot_snapshot.get("selected_active", 0) or 0)
        background_slot_pressure = (
            (str(slot_snapshot.get("selected_pressure", "")).strip() or "not_applicable")
            + " | "
            + (str(slot_snapshot.get("summary", "")).strip() or "-")
        )
    else:
        queue_snapshot = {}
        scheduler_snapshot = {}
        background_slot_limit = 1
        background_slot_active = 0
        background_slot_pressure = "not_applicable | -"
        team_dir = Path(".")
    worker_snapshot = background_runs.summarize_background_worker_state(
        background_runs.background_worker_state_path(team_dir)
    ) if str(entry.get("team_dir", "")).strip() else {}
    workspace_summary = workspace_brief.summarize_workspace_brief(
        team_dir,
        entry=entry,
        project_root=entry.get("project_root"),
    ) if str(entry.get("team_dir", "")).strip() else "-"
    document_registry_summary = document_registry.summarize_document_registry(
        team_dir,
        entry=entry,
        project_root=entry.get("project_root"),
    ) if str(entry.get("team_dir", "")).strip() else "-"
    model_routing_summary = model_endpoint_adapter.summarize_model_routing(team_dir, entry=entry) if str(entry.get("team_dir", "")).strip() else "-"
    model_registry_summary = model_endpoint_adapter.summarize_model_endpoint_registry(team_dir, entry=entry) if str(entry.get("team_dir", "")).strip() else "-"
    latest_judge_summary = (
        _latest_judge_summary(Path(str(root_team_dir or "")).expanduser(), project_alias=str(entry.get("project_alias", "")).strip())
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_judge_decision_summary = (
        _latest_judge_decision_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_judge_decision_bridge_summary = (
        _latest_judge_decision_bridge_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_decision_summary = (
        _latest_replan_auto_decision_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_routing_policy_summary = (
        _latest_replan_auto_routing_policy_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_route_summary = (
        _latest_replan_auto_route_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_route_status_summary = (
        action_audit.load_latest_replan_auto_route_status_summary_for_runtime(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_operator_summary = (
        action_audit.load_latest_replan_auto_operator_summary_for_runtime(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_manual_step_summary = (
        _latest_manual_step_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
            active_task=active_task or {},
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_canonical_writeback_summary = (
        _latest_canonical_writeback_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
            active_task=active_task or {},
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_canonical_mutation_summary = (
        _latest_canonical_mutation_summary(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
            active_task=active_task or {},
        )
        if str(root_team_dir or "").strip()
        else "-"
    )
    latest_replan_auto_routing_policy = (
        action_audit.load_latest_replan_auto_routing_policy_for_runtime(
            Path(str(root_team_dir or "")).expanduser(),
            project_alias=str(entry.get("project_alias", "")).strip(),
        )
        if str(root_team_dir or "").strip()
        else {}
    )
    active_task_context_pack_profile = "-"
    active_task_context_pack_summary = "-"
    active_task_context_pack_docs = "-"
    active_task_context_pack_excluded = "-"
    active_task_model_plan_summary = "-"
    active_task_judge_binding_summary = "-"
    active_task_judge_probe_summary = "-"
    if isinstance(active_task, dict) and str(entry.get("team_dir", "")).strip():
        pack = context_pack.load_context_pack(
            team_dir,
            entry=entry,
            task=active_task,
            project_root=entry.get("project_root"),
        )
        model_plan = model_endpoint_adapter.resolve_task_model_plan(team_dir, entry=entry, task=active_task)
        judge_binding = model_endpoint_adapter.resolve_task_judge_binding(
            team_dir,
            entry=entry,
            task=active_task,
        )
        active_task_context_pack_profile = str(pack.get("profile", "")).strip() or "-"
        active_task_context_pack_summary = str(pack.get("summary", "")).strip() or "-"
        active_task_context_pack_docs = str(pack.get("docs_summary", "")).strip() or "-"
        active_task_context_pack_excluded = str(pack.get("excluded_summary", "")).strip() or "-"
        active_task_model_plan_summary = str(model_plan.get("summary", "")).strip() or "-"
        active_task_judge_binding_summary = str(judge_binding.get("summary", "")).strip() or "-"
        endpoint = judge_binding.get("endpoint") if isinstance(judge_binding.get("endpoint"), dict) else {}
        provider_kind = str(endpoint.get("provider_kind", "")).strip().lower()
        if not judge_binding.get("bound"):
            active_task_judge_probe_summary = "status=unbound"
        elif provider_kind != "ollama":
            active_task_judge_probe_summary = (
                f"endpoint={str(endpoint.get('endpoint_id', '')).strip() or '-'} "
                f"provider={provider_kind or '-'} status=unsupported_probe"
            )
        else:
            active_task_judge_probe_summary = (
                f"endpoint={str(endpoint.get('endpoint_id', '')).strip() or '-'} "
                f"provider=ollama status=deferred_live_probe"
            )
        active_task_judge_probe_summary = action_audit.prefer_recent_model_ping_probe_summary(
            team_dir,
            project_alias=str(entry.get("project_alias", "")).strip(),
            kind="judge",
            endpoint_id=str(endpoint.get("endpoint_id", "")).strip(),
            probe_status="unsupported_probe" if judge_binding.get("bound") and provider_kind != "ollama" else ("deferred_live_probe" if judge_binding.get("bound") else "unbound"),
            probe_summary=active_task_judge_probe_summary,
        )
    run_lock_mode = run_lock.project_run_lock_mode(entry)
    run_lock_note = run_lock.project_run_lock_note(entry) or "-"
    active_rerun_summary = _task_rerun_summary(active_task) if isinstance(active_task, dict) else "-"
    active_followup_summary = _task_followup_summary(active_task) if isinstance(active_task, dict) else "-"
    active_rate_limit_summary = _runtime_active_task_rate_limit_summary(row)
    runtime_action_contract = _runtime_command_contract(
        project_alias=target_alias,
        priority_action=str(row.get("priority_action", "")).strip(),
        has_active_task=bool(active_request_id),
        has_rate_limit=active_rate_limit_summary != "-",
        background_queue_stale_count=int(queue_snapshot.get("stale_count", 0) or 0),
    )
    active_task_action_contract = (
        _task_command_contract(
            project_alias=target_alias,
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            tf_phase=str(row.get("active_task_tf_phase", "")).strip(),
            rerun_summary=active_rerun_summary,
            followup_summary=active_followup_summary,
            followup_brief_status=str((active_task or {}).get("followup_brief_status", "")).strip(),
            rate_limit_summary=active_rate_limit_summary,
            execution_brief_status=str((active_task or {}).get("execution_brief_status", "")).strip(),
        )
        if active_request_id
        else {"safe": [], "phase2": []}
    )
    runtime_safe_action_buttons, runtime_phase2_action_buttons = _runtime_action_buttons(
        project_alias=target_alias,
        phase2_commands=list(runtime_action_contract.get("phase2") or []),
    )
    runtime_phase2_action_buttons = _append_unique_action_button(
        runtime_phase2_action_buttons,
        _replan_auto_route_action_button(
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=latest_replan_auto_routing_policy,
        ),
    )
    runtime_safe_action_buttons = _append_unique_action_button(
        runtime_safe_action_buttons,
        _replan_manual_route_action_button(
            project_alias=target_alias,
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=latest_replan_auto_routing_policy,
        ),
    )
    runtime_phase2_action_buttons = _append_unique_action_button(
        runtime_phase2_action_buttons,
        _worker_update_proposal_accept_button(
            project_alias=target_alias,
            proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
        ),
    )
    active_task_safe_action_buttons, active_task_phase2_action_buttons = _task_action_buttons(
        label=str(row.get("active_task_label", "")).strip(),
        request_id=active_request_id,
        phase2_commands=list(active_task_action_contract.get("phase2") or []),
        include_followup_preview=bool(active_request_id),
    )
    active_task_phase2_action_buttons = _append_unique_action_button(
        active_task_phase2_action_buttons,
        _replan_auto_route_action_button(
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=latest_replan_auto_routing_policy,
        ),
    )
    active_task_manual_route_button = _replan_manual_route_action_button(
        project_alias=target_alias,
        label=str(row.get("active_task_label", "")).strip(),
        request_id=active_request_id,
        policy=latest_replan_auto_routing_policy,
    )
    if active_task_manual_route_button is not None and str(active_task_manual_route_button.mode).strip() == "phase2":
        active_task_phase2_action_buttons = _append_unique_action_button(active_task_phase2_action_buttons, active_task_manual_route_button)
    else:
        active_task_safe_action_buttons = _append_unique_action_button(active_task_safe_action_buttons, active_task_manual_route_button)
    worker_apply_applied = str((active_task or {}).get("background_run_worker_apply_accept_status", "")).strip() == "applied"
    worker_syncback_applied = _worker_syncback_applied(active_task or {})
    if not worker_apply_applied:
        active_task_safe_action_buttons = _append_unique_action_button(
            active_task_safe_action_buttons,
            _worker_apply_preview_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                update_stub={
                    "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                    "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                    "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                    "actions": (active_task or {}).get("background_run_worker_result_actions"),
                    "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                    "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
            ),
        )
    elif not worker_syncback_applied:
        active_task_safe_action_buttons = _append_unique_action_button(
            active_task_safe_action_buttons,
            _worker_apply_syncback_preview_button(project_alias=target_alias),
        )
    active_task_safe_action_buttons = _append_unique_action_button(
        active_task_safe_action_buttons,
        _worker_update_preview_button(
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            update_stub={
                "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                "actions": (active_task or {}).get("background_run_worker_result_actions"),
                "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
            },
            proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
        ),
    )
    if not worker_apply_applied:
        active_task_phase2_action_buttons = _append_unique_action_button(
            active_task_phase2_action_buttons,
            _worker_apply_proposal_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                update_stub={
                    "status": (active_task or {}).get("background_run_worker_update_stub_status"),
                    "summary_line": (active_task or {}).get("background_run_worker_update_stub_summary"),
                    "target_artifacts": (active_task or {}).get("background_run_worker_update_stub_targets"),
                    "actions": (active_task or {}).get("background_run_worker_result_actions"),
                    "cautions": (active_task or {}).get("background_run_worker_result_cautions"),
                    "evidence_refs": (active_task or {}).get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
            ),
        )
    active_task_phase2_action_buttons = _append_unique_action_button(
        active_task_phase2_action_buttons,
        _worker_update_proposal_accept_button(
            project_alias=target_alias,
            proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
        ),
    )
    if not worker_apply_applied:
        active_task_phase2_action_buttons = _append_unique_action_button(
            active_task_phase2_action_buttons,
            _worker_apply_proposal_accept_button(
                label=str((active_task or {}).get("short_id", "")).strip() or str((active_task or {}).get("alias", "")).strip(),
                request_id=str((active_task or {}).get("request_id", "")).strip(),
                project_alias=target_alias,
                proposal_ids=(active_task or {}).get("background_run_worker_update_proposal_ids") or [],
                proposal_summary=(active_task or {}).get("background_run_worker_update_proposal_summary"),
            ),
        )
    elif not worker_syncback_applied:
        active_task_phase2_action_buttons = _append_unique_action_button(
            active_task_phase2_action_buttons,
            _worker_apply_syncback_apply_button(project_alias=target_alias),
        )
    return RuntimeDetailDTO(
        project_key=key,
        project_alias=target_alias,
        project_label=display,
        runtime_path=runtime_path,
        status=str(row.get("status", "ready")).strip(),
        readiness=str(row.get("runtime_label", "ready")).strip() or "ready",
        attention_summary=str(row.get("attention_summary", "-")).strip() or "-",
        priority_action=str(row.get("priority_action", "-")).strip() or "-",
        priority_reason=str(row.get("priority_reason", "-")).strip() or "-",
        next_focus=offdesk_flow.preset_next_focus(phase1_preset, phase2_preset),
        completed_task_count=completed_task_count,
        blocked_task_count=int(queue_snapshot.get("blocked_count", 0) or 0),
        parked_task_count=int(queue_snapshot.get("parked_count", 0) or 0),
        queue_summary=_runtime_detail_queue_summary(row),
        proposal_summary=_runtime_detail_proposal_summary(row),
        sync_summary=_runtime_detail_sync_summary(row),
        provider_pressure_summary=_runtime_detail_provider_pressure_summary(row),
        repeat_summary=_runtime_detail_repeat_summary(row),
        active_task_request_id=active_request_id,
        active_task_label=str(row.get("active_task_label", "")).strip(),
        active_task_path=_detail_path(active_request_id) if active_request_id else "",
        active_task_phase=str(row.get("active_task_tf_phase", "")).strip(),
        active_task_status=str(row.get("active_task_status", "")).strip(),
        active_task_preset="phase1={phase1} phase2={phase2}".format(
            phase1=phase1_preset or "-",
            phase2=phase2_preset or phase1_preset or "-",
        ) if (phase1_preset or phase2_preset) else "-",
        active_task_phase2_shape=_compose_phase2_shape(
            row.get("active_task_phase2_execution_roles") or [],
            row.get("active_task_phase2_review_roles") or [],
        ),
        active_task_phase2_quality=_compose_phase2_quality(row),
        active_task_execution_brief_status=str((active_task or {}).get("execution_brief_status", "")).strip() or "-",
        active_task_execution_brief_summary=str((active_task or {}).get("execution_brief_summary", "")).strip() or "-",
        active_task_execution_brief_executable_slice=", ".join(
            str(item).strip()
            for item in ((active_task or {}).get("execution_brief_executable_slice") or [])
            if str(item).strip()
        )
        or "-",
        active_task_execution_brief_blocked_slice=", ".join(
            str(item).strip()
            for item in ((active_task or {}).get("execution_brief_blocked_slice") or [])
            if str(item).strip()
        )
        or "-",
        active_task_execution_brief_operator_decision=(
            str((active_task or {}).get("execution_brief_operator_decision", "")).strip() or "-"
        ),
        active_task_followup_brief_status=str((active_task or {}).get("followup_brief_status", "")).strip() or "-",
        active_task_followup_brief_summary=(
            str((active_task or {}).get("followup_brief_summary", "")).strip() or "-"
        ),
        active_task_followup_brief_execution_lanes=", ".join(
            str(item).strip()
            for item in ((active_task or {}).get("followup_brief_execution_lane_ids") or [])
            if str(item).strip()
        )
        or "-",
        active_task_followup_brief_review_lanes=", ".join(
            str(item).strip()
            for item in ((active_task or {}).get("followup_brief_review_lane_ids") or [])
            if str(item).strip()
        )
        or "-",
        active_task_followup_brief_reason=(
            str((active_task or {}).get("followup_brief_reason", "")).strip() or "-"
        ),
        active_task_context_pack_profile=active_task_context_pack_profile,
        active_task_context_pack_summary=active_task_context_pack_summary,
        active_task_context_pack_docs=active_task_context_pack_docs,
        active_task_context_pack_excluded=active_task_context_pack_excluded,
        active_task_model_plan_summary=active_task_model_plan_summary,
        active_task_judge_binding_summary=active_task_judge_binding_summary,
        active_task_judge_probe_summary=active_task_judge_probe_summary,
        active_task_reentry_rails_summary=(
            str((active_task or {}).get("reentry_rails_summary", "")).strip() or "-"
        ),
        active_task_background_run_status=str((active_task or {}).get("background_run_status", "")).strip() or "-",
        active_task_background_run_runner_target=(
            str((active_task or {}).get("background_run_runner_target", "")).strip() or "-"
        ),
        active_task_background_run_ticket_id=(
            str((active_task or {}).get("background_run_ticket_id", "")).strip() or "-"
        ),
        active_task_background_run_launch_mode=(
            str((active_task or {}).get("background_run_launch_mode", "")).strip() or "-"
        ),
        active_task_background_run_runtime_handle=(
            str((active_task or {}).get("background_run_runtime_handle", "")).strip() or "-"
        ),
        active_task_background_run_runtime_summary=(
            str((active_task or {}).get("background_run_runtime_summary", "")).strip() or "-"
        ),
        active_task_background_run_external_phase=(
            str((active_task or {}).get("background_run_external_phase", "")).strip() or "-"
        ),
        active_task_background_run_external_note=(
            str((active_task or {}).get("background_run_external_note", "")).strip() or "-"
        ),
        active_task_background_run_evidence_bundle=(
            str((active_task or {}).get("background_run_evidence_bundle", "")).strip() or "-"
        ),
        active_task_background_run_evidence_artifacts=", ".join(
            str(item).strip()
            for item in ((active_task or {}).get("background_run_evidence_artifacts") or [])
            if str(item).strip()
        )
        or "-",
        active_task_background_run_launch_spec_summary=(
            str((active_task or {}).get("background_run_launch_spec_summary", "")).strip() or "-"
        ),
        active_task_background_run_task_contract_summary=(
            str((active_task or {}).get("background_run_task_contract_summary", "")).strip() or "-"
        ),
        active_task_background_run_task_contract_module_summary=_worker_module_summary(active_task or {}),
        active_task_background_run_task_contract_policy_summary=_worker_policy_summary(active_task or {}),
        active_task_background_run_worker_gate_summary=_worker_gate_summary(active_task or {}),
        active_task_background_run_worker_profile_summary=_worker_profile_summary(active_task or {}),
        active_task_background_run_worker_checklist_summary=_worker_checklist_summary(active_task or {}),
        active_task_background_run_worker_items_summary=_worker_items_summary(active_task or {}),
        active_task_background_run_worker_items=_worker_items(active_task or {}),
        active_task_background_run_worker_result_summary=(
            str((active_task or {}).get("background_run_worker_result_summary", "")).strip() or "-"
        ),
        active_task_background_run_worker_result_actions=", ".join(
            str(item).strip()
            for item in (
                (((active_task or {}).get("background_run_worker_result_actions")) if isinstance((active_task or {}).get("background_run_worker_result_actions"), list) else [])
                or []
            )
            if str(item).strip()
        )
        or "-",
        active_task_background_run_worker_result_cautions=", ".join(
            str(item).strip()
            for item in (
                (((active_task or {}).get("background_run_worker_result_cautions")) if isinstance((active_task or {}).get("background_run_worker_result_cautions"), list) else [])
                or []
            )
            if str(item).strip()
        )
        or "-",
        active_task_background_run_worker_result_evidence_refs=", ".join(
            str(item).strip()
            for item in (
                (((active_task or {}).get("background_run_worker_result_evidence_refs")) if isinstance((active_task or {}).get("background_run_worker_result_evidence_refs"), list) else [])
                or []
            )
            if str(item).strip()
        )
        or "-",
        active_task_background_run_worker_update_stub_status=(
            str((active_task or {}).get("background_run_worker_update_stub_status", "")).strip() or "-"
        ),
        active_task_background_run_worker_update_stub_summary=(
            str((active_task or {}).get("background_run_worker_update_stub_summary", "")).strip() or "-"
        ),
        active_task_background_run_worker_update_stub_targets=", ".join(
            str(item).strip()
            for item in (
                (((active_task or {}).get("background_run_worker_update_stub_targets")) if isinstance((active_task or {}).get("background_run_worker_update_stub_targets"), list) else [])
                or []
            )
            if str(item).strip()
        )
        or "-",
        active_task_background_run_worker_update_proposal_summary=(
            str((active_task or {}).get("background_run_worker_update_proposal_summary", "")).strip() or "-"
        ),
        active_task_background_run_worker_update_proposal_ids=[
            str(item).strip()
            for item in ((active_task or {}).get("background_run_worker_update_proposal_ids") or [])
            if str(item).strip()
        ],
        active_task_background_run_worker_update_operator_summary=_worker_update_operator_summary(active_task or {}),
        active_task_background_run_worker_apply_accept_summary=_worker_apply_accept_summary(active_task or {}),
        active_task_background_run_worker_syncback_summary=_worker_syncback_summary(active_task or {}),
        active_task_background_run_model_plan_summary=(
            str((active_task or {}).get("background_run_model_plan_summary", "")).strip() or "-"
        ),
        active_task_background_run_model_judge_binding_summary=(
            str((active_task or {}).get("background_run_model_judge_binding_summary", "")).strip() or "-"
        ),
        active_task_background_run_model_judge_probe_summary=(
            str((active_task or {}).get("background_run_model_judge_probe_summary", "")).strip() or "-"
        ),
        active_task_background_run_model_escalation_binding_summary=(
            str((active_task or {}).get("background_run_model_escalation_binding_summary", "")).strip() or "-"
        ),
        active_task_background_run_model_escalation_probe_summary=(
            str((active_task or {}).get("background_run_model_escalation_probe_summary", "")).strip() or "-"
        ),
        workspace_summary=workspace_summary,
        document_registry_summary=document_registry_summary,
        model_routing_summary=model_routing_summary,
        model_registry_summary=model_registry_summary,
        latest_judge_summary=latest_judge_summary,
        latest_judge_decision_summary=latest_judge_decision_summary,
        latest_judge_decision_bridge_summary=latest_judge_decision_bridge_summary,
        latest_replan_auto_decision_summary=latest_replan_auto_decision_summary,
        latest_replan_auto_routing_policy_summary=latest_replan_auto_routing_policy_summary,
        latest_replan_auto_route_summary=latest_replan_auto_route_summary,
        latest_replan_auto_route_status_summary=latest_replan_auto_route_status_summary,
        latest_replan_auto_operator_summary=latest_replan_auto_operator_summary,
        latest_manual_step_summary=latest_manual_step_summary,
        latest_canonical_writeback_summary=latest_canonical_writeback_summary,
        latest_canonical_mutation_summary=latest_canonical_mutation_summary,
        run_lock_mode=run_lock_mode,
        run_lock_note=run_lock_note,
        background_slot_limit=background_slot_limit,
        background_slot_active=background_slot_active,
        background_slot_pressure=background_slot_pressure,
        background_worker_status=str(worker_snapshot.get("status", "")).strip() or "-",
        background_worker_summary=str(worker_snapshot.get("summary", "")).strip() or "-",
        background_queue_summary=str(queue_snapshot.get("summary", "")).strip() or "-",
        background_scheduler_summary=str(scheduler_snapshot.get("summary", "")).strip() or "-",
        background_scheduler_note=_background_scheduler_note(
            str(scheduler_snapshot.get("summary", "")).strip() or "-"
        ),
        background_queue_depth=int(queue_snapshot.get("depth", 0) or 0),
        background_queue_stale_count=int(queue_snapshot.get("stale_count", 0) or 0),
        active_task_completion_focus=str(active_contract.get("focus", "")).strip() or "-",
        active_task_completion_done=str(active_contract.get("done_when", "")).strip() or "-",
        active_task_completion_rerun=str(active_contract.get("rerun_when", "")).strip() or "-",
        active_task_completion_followup=str(active_contract.get("manual_followup_when", "")).strip() or "-",
        active_task_backend=_compose_backend_summary(
            str(row.get("active_task_backend", "")).strip(),
            str(row.get("active_task_backend_profile", "")).strip(),
            str(row.get("active_task_backend_verdict", "")).strip(),
            str(row.get("active_task_backend_contract", "")).strip(),
        ),
        active_task_backend_note=str(row.get("active_task_backend_note", "")).strip(),
        active_task_rate_limit=active_rate_limit_summary,
        runtime_command_hints=list(runtime_action_contract.get("safe") or []),
        runtime_phase2_action_hints=list(runtime_action_contract.get("phase2") or []),
        active_task_command_hints=list(active_task_action_contract.get("safe") or []),
        active_task_phase2_action_hints=list(active_task_action_contract.get("phase2") or []),
        runtime_safe_action_buttons=runtime_safe_action_buttons,
        runtime_phase2_action_buttons=runtime_phase2_action_buttons,
        active_task_safe_action_buttons=active_task_safe_action_buttons,
        active_task_phase2_action_buttons=active_task_phase2_action_buttons,
        notes=list(row.get("notes") or []),
        lines=list(row.get("lines") or []),
        recent_tasks=_build_runtime_recent_task_rows(
            entry,
            project_key=key,
            project_alias=target_alias,
            project_label=display,
        ),
    )
