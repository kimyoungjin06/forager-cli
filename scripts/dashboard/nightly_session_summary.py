#!/usr/bin/env python3
"""Generate file-based nightly session summaries for the Recovery Loop."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts" / "dashboard") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "dashboard"))
if str(ROOT / "scripts" / "gateway") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts" / "gateway"))

from aoe_tg_runtime_core import recovery_summary_dir as runtime_recovery_summary_dir
from control_dashboard_state import (
    DashboardSnapshotDTO,
    RuntimeDetailDTO,
    TaskDetailDTO,
    load_dashboard_runtime_details,
    now_iso,
    task_detail_from_state,
)
from control_dashboard_state_common import _background_scheduler_note
import aoe_tg_action_audit as action_audit
import aoe_tg_task_view as task_view


def _safe_stamp(iso_text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(iso_text or "").strip()) or "summary"


def _default_output_dir(snapshot: DashboardSnapshotDTO) -> Path:
    return runtime_recovery_summary_dir(snapshot.team_dir)


def _automation_posture(snapshot: DashboardSnapshotDTO) -> str:
    auto_mode = str(snapshot.control_summary.auto_mode).strip().lower()
    offdesk_mode = str(snapshot.control_summary.offdesk_mode).strip().lower()
    if auto_mode not in {"", "off"}:
        return f"auto_active ({auto_mode})"
    if offdesk_mode == "on":
        return "offdesk_only"
    return "inactive"


def _runtime_has_activity(detail: RuntimeDetailDTO) -> bool:
    if detail.active_task_request_id:
        return True
    if detail.completed_task_count > 0 or detail.blocked_task_count > 0 or detail.parked_task_count > 0:
        return True
    if detail.repeat_summary != "-":
        return True
    if detail.status != "ready":
        return True
    return False


def _task_rows_for_runtime(manager_state: Dict[str, Any], detail: RuntimeDetailDTO, *, cap: int = 5) -> List[TaskDetailDTO]:
    rows: List[TaskDetailDTO] = []
    seen: set[str] = set()
    for row in detail.recent_tasks:
        request_id = str(row.request_id or "").strip()
        if not request_id or request_id in seen:
            continue
        task = task_detail_from_state(manager_state, request_id)
        if task is None:
            continue
        rows.append(task)
        seen.add(request_id)
        if len(rows) >= max(1, int(cap)):
            break
    return rows


def _runtime_planning_compact_summary(detail: RuntimeDetailDTO, *, approved_plan: str) -> str:
    return (
        str(
            task_view.planning_operator_bundle(
                planning_lanes=detail.active_task_planning_lanes_summary,
                approved_plan_gate=detail.active_task_approved_plan_gate_summary,
                approved_plan=approved_plan,
                planner_lane=detail.active_task_planner_lane_summary,
                critic_lane=detail.active_task_critic_lane_summary,
            ).get("planning_compact", "-")
        ).strip()
        or "-"
    )


def _recent_action_planning_compact_summary(row: Dict[str, Any]) -> str:
    return str(row.get("planning_compact_summary", "")).strip() or "-"


def _task_summary_dict(task: TaskDetailDTO) -> Dict[str, Any]:
    return {
        "request_id": task.request_id,
        "label": task.label,
        "status": task.status,
        "tf_phase": task.tf_phase,
        "preset": {
            "phase1": task.phase1_role_preset or "-",
            "phase2": task.phase2_team_preset or "-",
        },
        "phase2_shape": task.phase2_shape,
        "phase2_quality": task.phase2_quality,
        "lane_summary": task.lane_summary,
        "rerun_summary": task.rerun_summary,
        "followup_summary": task.followup_summary,
        "followup_brief_status": getattr(task, "followup_brief_status", ""),
        "followup_brief_summary": getattr(task, "followup_brief_summary", ""),
        "followup_brief_execution_lanes": getattr(task, "followup_brief_execution_lanes", ""),
        "followup_brief_review_lanes": getattr(task, "followup_brief_review_lanes", ""),
        "completion_contract": {
            "focus": task.completion_focus,
            "done_when": task.completion_done_when,
            "rerun_when": task.completion_rerun_when,
            "manual_followup_when": task.completion_followup_when,
        },
        "backend_summary": task.backend_summary,
        "backend_note": task.backend_note,
        "rate_limit_summary": task.rate_limit_summary,
        "background_run_worker_update_stub_status": getattr(task, "background_run_worker_update_stub_status", ""),
        "background_run_worker_update_stub_summary": task.background_run_worker_update_stub_summary,
        "background_run_worker_update_stub_targets": task.background_run_worker_update_stub_targets,
        "background_run_worker_update_proposal_summary": task.background_run_worker_update_proposal_summary,
        "background_run_worker_update_proposal_ids": list(
            getattr(task, "background_run_worker_update_proposal_ids", []) or []
        ),
        "background_run_task_contract_module_summary": getattr(task, "background_run_task_contract_module_summary", ""),
        "background_run_worker_records_summary": getattr(task, "background_run_worker_records_summary", ""),
        "background_run_worker_records": getattr(task, "background_run_worker_records", ""),
        "background_run_worker_record_rows_summary": getattr(task, "background_run_worker_record_rows_summary", ""),
        "background_run_worker_record_rows": getattr(task, "background_run_worker_record_rows", ""),
        "background_run_worker_preflight_summary": getattr(task, "background_run_worker_preflight_summary", ""),
        "background_run_worker_preflight_rows_summary": getattr(task, "background_run_worker_preflight_rows_summary", ""),
        "background_run_worker_preflight_rows": getattr(task, "background_run_worker_preflight_rows", ""),
        "background_run_worker_apply_accept_summary": getattr(task, "background_run_worker_apply_accept_summary", ""),
        "background_run_worker_apply_accept_at": getattr(task, "background_run_worker_apply_accept_at", ""),
        "background_run_worker_syncback_status": getattr(task, "background_run_worker_syncback_status", ""),
        "background_run_worker_syncback_summary": getattr(task, "background_run_worker_syncback_summary", ""),
        "background_run_worker_syncback_at": getattr(task, "background_run_worker_syncback_at", ""),
        "observatory": {
            "headline": task.observatory_headline,
            "first_focus": task.observatory_first_focus,
            "stale_lane_count": task.observatory_stale_lane_count,
            "bottleneck_lane": task.observatory_bottleneck_lane,
            "bottleneck_reason": task.observatory_bottleneck_reason,
            "conflict_file_count": task.observatory_conflict_file_count,
            "touched_file_count": task.observatory_touched_file_count,
        },
        "operator_hints": list(task.command_hints),
        "phase2_actions": list(task.phase2_action_hints),
        "updated_at": task.updated_at,
    }


def _latest_judge_summary(team_dir: str, project_alias: str) -> str:
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


def _latest_judge_decision_summary(team_dir: str, project_alias: str) -> str:
    return action_audit.load_latest_offdesk_judge_decision_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_judge_decision_bridge_summary(team_dir: str, project_alias: str) -> str:
    return action_audit.load_latest_judge_decision_bridge_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_decision_summary(team_dir: str, project_alias: str) -> str:
    return action_audit.load_latest_replan_auto_decision_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_routing_policy_summary(team_dir: str, project_alias: str) -> str:
    return action_audit.load_latest_replan_auto_routing_policy_summary_for_runtime(
        team_dir,
        project_alias=project_alias,
    )


def _latest_replan_auto_route_summary(team_dir: str, project_alias: str) -> str:
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


def build_nightly_session_summary(
    *,
    control_root: Path | str,
    team_dir: Path | str | None = None,
    manager_state_file: Path | str | None = None,
) -> Dict[str, Any]:
    generated_at = now_iso()
    snapshot, runtime_details, manager_state = load_dashboard_runtime_details(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    active_details = [detail for detail in runtime_details if _runtime_has_activity(detail)]
    if not active_details:
        active_details = runtime_details

    runtimes: List[Dict[str, Any]] = []
    for detail in active_details:
        latest_replan_policy = action_audit.load_latest_replan_auto_routing_policy_for_runtime(
            snapshot.team_dir,
            project_alias=detail.project_alias,
        )
        latest_planning_handoff = (latest_replan_policy or {}).get("planning_handoff")
        latest_planning_handoff_summary = action_audit.summarize_planning_handoff_snapshot(latest_planning_handoff)
        latest_planning_review_approved_plan = (
            action_audit.summarize_retry_replan_approved_plan_handoff(latest_planning_handoff)
            or detail.active_task_approved_plan_summary
        )
        latest_planning_compact_summary = _runtime_planning_compact_summary(
            detail,
            approved_plan=latest_planning_review_approved_plan,
        )
        task_rows = _task_rows_for_runtime(manager_state, detail)
        runtimes.append(
            {
                "project_key": detail.project_key,
                "project_alias": detail.project_alias,
                "project_label": detail.project_label,
                "runtime_path": detail.runtime_path,
                "status": detail.status,
                "readiness": detail.readiness,
                "attention_summary": detail.attention_summary,
                "priority_action": detail.priority_action,
                "priority_reason": detail.priority_reason,
                "next_focus": detail.next_focus,
                "completed_task_count": detail.completed_task_count,
                "blocked_task_count": detail.blocked_task_count,
                "parked_task_count": detail.parked_task_count,
                "queue_summary": detail.queue_summary,
                "proposal_summary": detail.proposal_summary,
                "sync_summary": detail.sync_summary,
                "provider_pressure_summary": detail.provider_pressure_summary,
                "repeat_summary": detail.repeat_summary,
                "active_task_request_id": detail.active_task_request_id,
                "active_task_label": detail.active_task_label,
                "active_task_phase": detail.active_task_phase,
                "active_task_status": detail.active_task_status,
                "active_task_preset": detail.active_task_preset,
                "active_task_phase2_shape": detail.active_task_phase2_shape,
                "active_task_phase2_quality": detail.active_task_phase2_quality,
                "active_task_followup_brief_status": detail.active_task_followup_brief_status,
                "active_task_followup_brief_summary": detail.active_task_followup_brief_summary,
                "active_task_followup_brief_execution_lanes": detail.active_task_followup_brief_execution_lanes,
                "active_task_followup_brief_review_lanes": detail.active_task_followup_brief_review_lanes,
                "active_task_context_pack_summary": detail.active_task_context_pack_summary,
                "active_task_model_plan_summary": detail.active_task_model_plan_summary,
                "active_task_reentry_rails_summary": detail.active_task_reentry_rails_summary,
                "active_task_background_run_status": detail.active_task_background_run_status,
                "active_task_background_run_runner_target": detail.active_task_background_run_runner_target,
                "active_task_background_run_ticket_id": detail.active_task_background_run_ticket_id,
                "active_task_background_run_external_phase": detail.active_task_background_run_external_phase,
                "active_task_background_run_external_note": detail.active_task_background_run_external_note,
                "active_task_background_run_evidence_bundle": detail.active_task_background_run_evidence_bundle,
                "active_task_background_run_evidence_artifacts": detail.active_task_background_run_evidence_artifacts,
                "active_task_background_run_launch_spec_summary": detail.active_task_background_run_launch_spec_summary,
                "active_task_background_run_task_contract_module_summary": detail.active_task_background_run_task_contract_module_summary,
                "active_task_background_run_worker_update_stub_status": detail.active_task_background_run_worker_update_stub_status,
                "active_task_background_run_worker_update_stub_summary": detail.active_task_background_run_worker_update_stub_summary,
                "active_task_background_run_worker_update_stub_targets": detail.active_task_background_run_worker_update_stub_targets,
                "active_task_background_run_worker_update_operator_summary": detail.active_task_background_run_worker_update_operator_summary,
                "active_task_background_run_worker_update_proposal_summary": detail.active_task_background_run_worker_update_proposal_summary,
                "active_task_background_run_worker_update_proposal_ids": list(
                    getattr(detail, "active_task_background_run_worker_update_proposal_ids", []) or []
                ),
                "active_task_background_run_worker_records_summary": getattr(
                    detail, "active_task_background_run_worker_records_summary", ""
                ),
                "active_task_background_run_worker_records": getattr(
                    detail, "active_task_background_run_worker_records", ""
                ),
                "active_task_background_run_worker_record_rows_summary": getattr(
                    detail, "active_task_background_run_worker_record_rows_summary", ""
                ),
                "active_task_background_run_worker_record_rows": getattr(
                    detail, "active_task_background_run_worker_record_rows", ""
                ),
                "active_task_background_run_worker_preflight_summary": getattr(
                    detail, "active_task_background_run_worker_preflight_summary", ""
                ),
                "active_task_background_run_worker_preflight_rows_summary": getattr(
                    detail, "active_task_background_run_worker_preflight_rows_summary", ""
                ),
                "active_task_background_run_worker_preflight_rows": getattr(
                    detail, "active_task_background_run_worker_preflight_rows", ""
                ),
                "active_task_background_run_worker_apply_accept_summary": getattr(
                    detail, "active_task_background_run_worker_apply_accept_summary", ""
                ),
                "active_task_background_run_worker_apply_accept_at": getattr(
                    detail, "active_task_background_run_worker_apply_accept_at", ""
                ),
                "active_task_background_run_worker_syncback_status": getattr(
                    detail, "active_task_background_run_worker_syncback_status", ""
                ),
                "active_task_background_run_worker_syncback_summary": getattr(
                    detail, "active_task_background_run_worker_syncback_summary", ""
                ),
                "active_task_background_run_worker_syncback_at": getattr(
                    detail, "active_task_background_run_worker_syncback_at", ""
                ),
                "active_task_background_run_model_plan_summary": detail.active_task_background_run_model_plan_summary,
                "workspace_summary": detail.workspace_summary,
                "document_registry_summary": detail.document_registry_summary,
                "latest_judge_summary": _latest_judge_summary(snapshot.team_dir, detail.project_alias),
                "latest_judge_decision_summary": _latest_judge_decision_summary(snapshot.team_dir, detail.project_alias),
                "latest_judge_decision_bridge_summary": _latest_judge_decision_bridge_summary(snapshot.team_dir, detail.project_alias),
                "latest_replan_auto_decision_summary": _latest_replan_auto_decision_summary(snapshot.team_dir, detail.project_alias),
                "latest_replan_auto_routing_policy_summary": _latest_replan_auto_routing_policy_summary(snapshot.team_dir, detail.project_alias),
                "latest_replan_auto_routing_policy": latest_replan_policy,
                "latest_planning_handoff_summary": latest_planning_handoff_summary,
                "latest_planning_compact_summary": latest_planning_compact_summary,
                "latest_replan_auto_route_summary": _latest_replan_auto_route_summary(snapshot.team_dir, detail.project_alias),
                "latest_replan_auto_route_status_summary": action_audit.load_latest_replan_auto_route_status_summary_for_runtime(
                    snapshot.team_dir,
                    project_alias=detail.project_alias,
                ),
                "latest_manual_step_summary": action_audit.load_latest_manual_step_summary_for_runtime(
                    snapshot.team_dir,
                    project_alias=detail.project_alias,
                ),
                "latest_canonical_mutation_summary": action_audit.load_latest_canonical_mutation_summary_for_runtime(
                    snapshot.team_dir,
                    project_alias=detail.project_alias,
                ),
                "latest_canonical_writeback_summary": action_audit.load_latest_canonical_writeback_summary_for_runtime(
                    snapshot.team_dir,
                    project_alias=detail.project_alias,
                ),
                "run_lock_mode": detail.run_lock_mode,
                "run_lock_note": detail.run_lock_note,
                "background_slot_limit": detail.background_slot_limit,
                "background_slot_active": detail.background_slot_active,
                "background_slot_pressure": detail.background_slot_pressure,
                "background_worker_status": detail.background_worker_status,
                "background_worker_summary": detail.background_worker_summary,
                "background_queue_summary": detail.background_queue_summary,
                "background_scheduler_summary": detail.background_scheduler_summary,
                "background_scheduler_note": _background_scheduler_note(detail.background_scheduler_summary),
                "background_queue_depth": detail.background_queue_depth,
                "background_queue_stale_count": detail.background_queue_stale_count,
                "active_task_completion_contract": {
                    "focus": detail.active_task_completion_focus,
                    "done_when": detail.active_task_completion_done,
                    "rerun_when": detail.active_task_completion_rerun,
                    "manual_followup_when": detail.active_task_completion_followup,
                },
                "active_task_backend": detail.active_task_backend,
                "active_task_backend_note": detail.active_task_backend_note,
                "active_task_rate_limit": detail.active_task_rate_limit,
                "operator_hints": list(detail.runtime_command_hints),
                "phase2_actions": list(detail.runtime_phase2_action_hints),
                "active_task_hints": list(detail.active_task_command_hints),
                "active_task_phase2_actions": list(detail.active_task_phase2_action_hints),
                "notes": list(detail.notes),
                "task_teams": [_task_summary_dict(task) for task in task_rows],
            }
        )

    return {
        "generated_at": generated_at,
        "snapshot_taken_at": snapshot.snapshot_taken_at,
        "control_root": snapshot.control_root,
        "team_dir": snapshot.team_dir,
        "manager_state_file": snapshot.manager_state_file,
        "control_summary": {
            "auto_mode": snapshot.control_summary.auto_mode,
            "offdesk_mode": snapshot.control_summary.offdesk_mode,
            "automation_posture": _automation_posture(snapshot),
            "provider_capacity_summary": snapshot.control_summary.provider_capacity_summary,
            "next_retry_at": snapshot.control_summary.next_retry_at,
            "next_retry_target": snapshot.control_summary.next_retry_target,
            "repeat_memory_summary": snapshot.control_summary.repeat_memory_summary,
            "execution_brief_summary": snapshot.control_summary.execution_brief_summary,
            "background_run_summary": snapshot.control_summary.background_run_summary,
            "background_worker_summary": snapshot.control_summary.background_worker_summary,
            "latest_intent_command": snapshot.control_summary.latest_intent_command,
            "latest_intent_action": snapshot.control_summary.latest_intent_action,
            "latest_intent_trace": snapshot.control_summary.latest_intent_trace,
            "latest_intent_focus": snapshot.control_summary.latest_intent_focus,
            "active_runtime_count": snapshot.control_summary.active_runtime_count,
            "attention_runtime_count": snapshot.control_summary.attention_runtime_count,
            "server_guard": {
                "status": snapshot.control_summary.server_guard.status,
                "summary": snapshot.control_summary.server_guard.summary,
                "reason_summary": snapshot.control_summary.server_guard.reason_summary,
                "note": snapshot.control_summary.server_guard.note,
                "next_step": snapshot.control_summary.server_guard.next_step,
                "disk_summary": snapshot.control_summary.server_guard.disk_summary,
                "memory_summary": snapshot.control_summary.server_guard.memory_summary,
                "load_summary": snapshot.control_summary.server_guard.load_summary,
                "process_summary": snapshot.control_summary.server_guard.process_summary,
                "queue_summary": snapshot.control_summary.server_guard.queue_summary,
                "focus_label": snapshot.control_summary.server_guard.focus_label,
                "action_copy": snapshot.control_summary.server_guard.action_copy,
                "priority_link_label": snapshot.control_summary.server_guard.priority_link_label,
                "priority_link_note": snapshot.control_summary.server_guard.priority_link_note,
                "snapshot_path": snapshot.control_summary.server_guard.snapshot_path,
                "snapshot_updated_at": snapshot.control_summary.server_guard.snapshot_updated_at,
                "recommended_actions": [asdict(row) for row in snapshot.control_summary.server_guard.recommended_actions],
            },
        },
        "recent_action_audit": [asdict(row) for row in snapshot.recent_action_audit_rows],
        "source_files": [asdict(row) for row in snapshot.source_files],
        "runtimes": runtimes,
    }


def render_nightly_session_summary(summary: Dict[str, Any]) -> str:
    control = summary.get("control_summary") if isinstance(summary.get("control_summary"), dict) else {}
    server_guard = control.get("server_guard") if isinstance(control.get("server_guard"), dict) else {}
    priority_link = "-"
    if server_guard:
        priority_link = str(server_guard.get("priority_link_label", "")).strip() or "-"
        priority_note = str(server_guard.get("priority_link_note", "")).strip()
        if priority_note:
            priority_link = f"{priority_link} | {priority_note}"
    runtimes = summary.get("runtimes") if isinstance(summary.get("runtimes"), list) else []
    lines: List[str] = [
        "# Nightly Session Summary",
        "",
        "## Control Plane Summary",
        f"- generated_at: {summary.get('generated_at', '-')}",
        f"- snapshot_taken_at: {summary.get('snapshot_taken_at', '-')}",
        f"- automation_posture: {control.get('automation_posture', '-')}",
        f"- auto_mode: {control.get('auto_mode', '-')}",
        f"- offdesk_mode: {control.get('offdesk_mode', '-')}",
        f"- provider_capacity: {control.get('provider_capacity_summary', '-')}",
        f"- next_retry_at: {control.get('next_retry_at', '-')}",
        f"- next_retry_target: {control.get('next_retry_target', '-')}",
        f"- repeat_memory: {control.get('repeat_memory_summary', '-')}",
        f"- execution_brief_summary: {control.get('execution_brief_summary', '-')}",
        f"- background_run_summary: {control.get('background_run_summary', '-')}",
        f"- background_worker_summary: {control.get('background_worker_summary', '-')}",
        f"- latest_intent_command: {control.get('latest_intent_command', '-')}",
        f"- latest_intent_action: {control.get('latest_intent_action', '-')}",
        f"- latest_intent_trace: {control.get('latest_intent_trace', '-')}",
        f"- first_focus: {control.get('latest_intent_focus', '-')}",
        f"- server_guard: {server_guard.get('summary', '-') if server_guard else '-'}",
        f"- server_guard_reasons: {server_guard.get('reason_summary', '-') if server_guard else '-'}",
        f"- server_guard_note: {server_guard.get('note', '-') if server_guard else '-'}",
        f"- server_guard_next: {server_guard.get('next_step', '-') if server_guard else '-'}",
        f"- server_guard_focus: {server_guard.get('focus_label', '-') if server_guard else '-'}",
        f"- server_guard_action_copy: {server_guard.get('action_copy', '-') if server_guard else '-'}",
        f"- server_guard_priority_link: {priority_link}",
        f"- server_guard_snapshot: {server_guard.get('snapshot_path', '-') if server_guard else '-'}",
        "",
    ]
    recent_action_audit = summary.get("recent_action_audit") if isinstance(summary.get("recent_action_audit"), list) else []
    if recent_action_audit:
        lines.extend(["## Recent Dashboard Actions"])
        for row in recent_action_audit:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- {at} | {headline} | next={next_step}".format(
                    at=row.get("at", "-"),
                    headline=row.get("headline", "-"),
                    next_step=row.get("next_step", "-"),
                )
            )
            planning_compact = _recent_action_planning_compact_summary(row)
            if planning_compact and planning_compact != "-":
                lines.append(f"  - planning_compact: {planning_compact}")
            if str(row.get("link_href", "")).strip():
                lines.append(
                    "  - link: {label} -> {href}".format(
                        label=row.get("link_label", "detail"),
                        href=row.get("link_href", "-"),
                    )
                )
        lines.append("")
    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue
        runtime_heading = f"{runtime.get('project_alias', '-')} {runtime.get('project_label', '-')}"
        runtime_planning_compact = str(runtime.get("latest_planning_compact_summary", "")).strip()
        if runtime_planning_compact and runtime_planning_compact != "-":
            runtime_heading = f"{runtime_heading} | {runtime_planning_compact}"
        lines.extend(
            [
                f"## {runtime_heading}",
                f"- runtime: {runtime.get('readiness', '-')}",
                f"- status: {runtime.get('status', '-')}",
                f"- attention: {runtime.get('attention_summary', '-')}",
                f"- completed_tasks: {runtime.get('completed_task_count', 0)}",
                f"- blocked_tasks: {runtime.get('blocked_task_count', 0)}",
                f"- parked_tasks: {runtime.get('parked_task_count', 0)}",
                f"- first_focus: {control.get('latest_intent_focus', '-')} | next={runtime.get('priority_action', '-')}",
                f"- next_focus: {runtime.get('next_focus', '-') or '-'}",
                f"- queue: {runtime.get('queue_summary', '-')}",
                f"- background_queue: {runtime.get('background_queue_summary', '-')}",
                f"- latest_judge: {runtime.get('latest_judge_summary', '-')}",
                f"- latest_judge_decision: {runtime.get('latest_judge_decision_summary', '-')}",
                f"- latest_judge_decision_bridge: {runtime.get('latest_judge_decision_bridge_summary', '-')}",
                f"- replan_auto_decision: {runtime.get('latest_replan_auto_decision_summary', '-')}",
                f"- replan_auto_routing_policy: {runtime.get('latest_replan_auto_routing_policy_summary', '-')}",
                f"- planning_compact: {runtime.get('latest_planning_compact_summary', '-')}",
                f"- planning_handoff: {runtime.get('latest_planning_handoff_summary', '-')}",
                f"- latest_replan_auto_route: {runtime.get('latest_replan_auto_route_summary', '-')}",
                f"- auto_route_status: {runtime.get('latest_replan_auto_route_status_summary', '-')}",
                f"- manual_step: {runtime.get('latest_manual_step_summary', '-')}",
                f"- canonical_mutation: {runtime.get('latest_canonical_mutation_summary', '-')}",
                f"- canonical_writeback: {runtime.get('latest_canonical_writeback_summary', '-')}",
                f"- worker_apply_accept: {runtime.get('active_task_background_run_worker_apply_accept_summary', '-')}",
                f"- worker_syncback: {runtime.get('active_task_background_run_worker_syncback_summary', '-')}",
                f"- run_lock: {runtime.get('run_lock_mode', '-')}",
                f"- run_lock_note: {runtime.get('run_lock_note', '-')}",
                f"- background_slots: active={runtime.get('background_slot_active', 0)} limit={runtime.get('background_slot_limit', 1)}",
                f"- background_slot_pressure: {runtime.get('background_slot_pressure', '-')}",
                f"- background_worker: {runtime.get('background_worker_summary', '-')}",
                f"- background_queue_depth: {runtime.get('background_queue_depth', 0)}",
                f"- background_queue_stale_count: {runtime.get('background_queue_stale_count', 0)}",
                f"- background_scheduler: {runtime.get('background_scheduler_summary', '-')}",
                f"- background_scheduler_note: {runtime.get('background_scheduler_note', '-')}",
                f"- proposals: {runtime.get('proposal_summary', '-')}",
                f"- sync: {runtime.get('sync_summary', '-')}",
                f"- provider_pressure: {runtime.get('provider_pressure_summary', '-')}",
                f"- repeat_memory: {runtime.get('repeat_summary', '-')}",
            ]
        )
        operator_hints = runtime.get("operator_hints") if isinstance(runtime.get("operator_hints"), list) else []
        phase2_actions = runtime.get("phase2_actions") if isinstance(runtime.get("phase2_actions"), list) else []
        if operator_hints:
            lines.append(f"- operator_hints: {', '.join(str(item).strip() for item in operator_hints if str(item).strip())}")
        if phase2_actions:
            lines.append(f"- phase2_actions: {', '.join(str(item).strip() for item in phase2_actions if str(item).strip())}")
        active_task_label = str(runtime.get("active_task_label", "")).strip()
        if active_task_label:
            lines.extend(
                [
                    "- active_task:",
                    f"  - label: {active_task_label}",
                    f"  - status: {runtime.get('active_task_status', '-')}/{runtime.get('active_task_phase', '-')}",
                    f"  - preset: {runtime.get('active_task_preset', '-')}",
                    f"  - phase2_shape: {runtime.get('active_task_phase2_shape', '-')}",
                    f"  - phase2_quality: {runtime.get('active_task_phase2_quality', '-')}",
                    f"  - reentry_rails: {runtime.get('active_task_reentry_rails_summary', '-')}",
                    f"  - completion_focus: {((runtime.get('active_task_completion_contract') or {}).get('focus', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - done_when: {((runtime.get('active_task_completion_contract') or {}).get('done_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - rerun_when: {((runtime.get('active_task_completion_contract') or {}).get('rerun_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - manual_followup_when: {((runtime.get('active_task_completion_contract') or {}).get('manual_followup_when', '-') if isinstance(runtime.get('active_task_completion_contract'), dict) else '-')}",
                    f"  - backend: {runtime.get('active_task_backend', '-')}",
                    f"  - backend_note: {runtime.get('active_task_backend_note', '-') or '-'}",
                    f"  - rate_limit: {runtime.get('active_task_rate_limit', '-')}",
                    f"  - worker_update: {runtime.get('active_task_background_run_worker_update_operator_summary', '-')}",
                ]
            )
            active_task_hints = runtime.get("active_task_hints") if isinstance(runtime.get("active_task_hints"), list) else []
            active_task_phase2 = runtime.get("active_task_phase2_actions") if isinstance(runtime.get("active_task_phase2_actions"), list) else []
            if active_task_hints:
                lines.append(
                    "  - operator_hints: "
                    + ", ".join(str(item).strip() for item in active_task_hints if str(item).strip())
                )
            if active_task_phase2:
                lines.append(
                    "  - phase2_actions: "
                    + ", ".join(str(item).strip() for item in active_task_phase2 if str(item).strip())
                )
        task_teams = runtime.get("task_teams") if isinstance(runtime.get("task_teams"), list) else []
        if task_teams:
            lines.append("- task_teams:")
            for task in task_teams:
                if not isinstance(task, dict):
                    continue
                preset = task.get("preset") if isinstance(task.get("preset"), dict) else {}
                lines.extend(
                    [
                        f"  - {task.get('label', '-')} ({task.get('request_id', '-')})",
                        f"    - status: {task.get('status', '-')}/{task.get('tf_phase', '-')}",
                        "    - preset: phase1={phase1} phase2={phase2}".format(
                            phase1=preset.get("phase1", "-"),
                            phase2=preset.get("phase2", "-"),
                        ),
                        f"    - phase2_shape: {task.get('phase2_shape', '-')}",
                        f"    - phase2_quality: {task.get('phase2_quality', '-')}",
                        f"    - completion_focus: {((task.get('completion_contract') or {}).get('focus', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - done_when: {((task.get('completion_contract') or {}).get('done_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - rerun_when: {((task.get('completion_contract') or {}).get('rerun_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - manual_followup_when: {((task.get('completion_contract') or {}).get('manual_followup_when', '-') if isinstance(task.get('completion_contract'), dict) else '-')}",
                        f"    - lanes: {task.get('lane_summary', '-')}",
                        f"    - rerun: {task.get('rerun_summary', '-')}",
                        f"    - followup: {task.get('followup_summary', '-')}",
                        f"    - backend: {task.get('backend_summary', '-')}",
                        f"    - backend_note: {task.get('backend_note', '-') or '-'}",
                        f"    - rate_limit: {task.get('rate_limit_summary', '-')}",
                    ]
                )
                observatory = task.get("observatory") if isinstance(task.get("observatory"), dict) else {}
                if observatory:
                    lines.append(
                        "    - observatory: {headline}".format(
                            headline=str(observatory.get("headline", "")).strip() or "-",
                        )
                    )
                    lines.append(
                        "    - first_focus: {focus}".format(
                            focus=str(observatory.get("first_focus", "")).strip() or "-",
                        )
                    )
                    lines.append(
                        "    - observatory_files: touched={touched} conflicts={conflicts}".format(
                            touched=int(observatory.get("touched_file_count", 0) or 0),
                            conflicts=int(observatory.get("conflict_file_count", 0) or 0),
                        )
                    )
                task_hints = task.get("operator_hints") if isinstance(task.get("operator_hints"), list) else []
                task_phase2 = task.get("phase2_actions") if isinstance(task.get("phase2_actions"), list) else []
                if task_hints:
                    lines.append("    - operator_hints: " + ", ".join(str(item).strip() for item in task_hints if str(item).strip()))
                if task_phase2:
                    lines.append("    - phase2_actions: " + ", ".join(str(item).strip() for item in task_phase2 if str(item).strip()))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_nightly_session_summary(
    *,
    summary: Dict[str, Any],
    output_dir: Path,
    write_timestamped_copy: bool = True,
) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_nightly_session_summary(summary)
    payload = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    latest_md = output_dir / "latest.md"
    latest_json = output_dir / "latest.json"
    latest_md.write_text(markdown, encoding="utf-8")
    latest_json.write_text(payload, encoding="utf-8")
    if write_timestamped_copy:
        stamp = _safe_stamp(str(summary.get("generated_at", "")))
        (output_dir / f"{stamp}.md").write_text(markdown, encoding="utf-8")
        (output_dir / f"{stamp}.json").write_text(payload, encoding="utf-8")
    return latest_md, latest_json


def parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a nightly session summary artifact")
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--team-dir")
    parser.add_argument("--manager-state-file")
    parser.add_argument("--output-dir")
    parser.add_argument("--latest-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv)
    control_root = Path(args.control_root).expanduser().resolve()
    team_dir = Path(args.team_dir).expanduser().resolve() if args.team_dir else None
    manager_state_file = Path(args.manager_state_file).expanduser().resolve() if args.manager_state_file else None
    summary = build_nightly_session_summary(
        control_root=control_root,
        team_dir=team_dir,
        manager_state_file=manager_state_file,
    )
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else runtime_recovery_summary_dir(str(summary.get("team_dir", ".")))
    )
    latest_md, latest_json = write_nightly_session_summary(
        summary=summary,
        output_dir=output_dir,
        write_timestamped_copy=not bool(args.latest_only),
    )
    print(f"nightly summary written: {latest_md}")
    print(f"nightly summary json: {latest_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
