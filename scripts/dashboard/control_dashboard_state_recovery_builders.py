#!/usr/bin/env python3
"""Recovery-scoped dashboard state builders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_operator_summary as operator_summary

from control_dashboard_state_common import (
    _append_unique_action_button,
    _background_scheduler_note,
    _chat_console_target,
    _detail_path,
    _filter_dispatch_phase2_action_buttons,
    _filter_manual_route_action_buttons,
    _recovery_control_action_buttons,
    _replan_manual_route_action_button,
    _runtime_action_buttons,
    _runtime_command_contract,
    _runtime_path,
    _task_action_buttons,
    _task_command_contract,
    _worker_apply_proposal_button,
    _worker_apply_preview_button,
    _worker_apply_ready,
    _worker_apply_proposal_accept_button,
    _worker_blocker_action_button,
    _worker_apply_syncback_apply_button,
    _worker_apply_syncback_preview_button,
    _worker_syncback_ready,
    _worker_update_preview_button,
    _worker_update_proposal_accept_button,
)
from control_dashboard_state_io import FileFreshnessDTO
from control_dashboard_state_models import RecoveryRuntimeDTO, RecoverySummaryDTO, RecoveryTaskDTO, ServerGuardActionDTO, ServerGuardDTO


def _server_guard_from_recovery_control(control: Dict[str, Any]) -> ServerGuardDTO:
    raw = control.get("server_guard") if isinstance(control.get("server_guard"), dict) else {}
    actions_raw = raw.get("recommended_actions") if isinstance(raw.get("recommended_actions"), list) else []
    actions: List[ServerGuardActionDTO] = []
    for item in actions_raw:
        if not isinstance(item, dict):
            continue
        actions.append(
            ServerGuardActionDTO(
                label=str(item.get("label", "")).strip() or "-",
                href=str(item.get("href", "")).strip(),
                note=str(item.get("note", "")).strip(),
                method=str(item.get("method", "GET")).strip() or "GET",
                path=str(item.get("path", "")).strip(),
                mode=str(item.get("mode", "safe")).strip() or "safe",
                payload_json=str(item.get("payload_json", "{}")).strip() or "{}",
                command=str(item.get("command", "")).strip(),
            )
        )
    return ServerGuardDTO(
        status=str(raw.get("status", "")).strip() or "-",
        summary=str(raw.get("summary", "")).strip() or "-",
        reason_summary=str(raw.get("reason_summary", "")).strip() or "-",
        note=str(raw.get("note", "")).strip() or "-",
        next_step=str(raw.get("next_step", "")).strip() or "-",
        disk_summary=str(raw.get("disk_summary", "")).strip() or "-",
        memory_summary=str(raw.get("memory_summary", "")).strip() or "-",
        load_summary=str(raw.get("load_summary", "")).strip() or "-",
        process_summary=str(raw.get("process_summary", "")).strip() or "-",
        queue_summary=str(raw.get("queue_summary", "")).strip() or "-",
        focus_label=str(raw.get("focus_label", "")).strip() or "-",
        action_copy=str(raw.get("action_copy", "")).strip() or "-",
        priority_link_label=str(raw.get("priority_link_label", "")).strip() or "-",
        priority_link_note=str(raw.get("priority_link_note", "")).strip() or "-",
        snapshot_path=str(raw.get("snapshot_path", "")).strip() or "-",
        snapshot_updated_at=str(raw.get("snapshot_updated_at", "")).strip() or "-",
        recommended_actions=actions,
    )


def _worker_apply_applied(row: Dict[str, Any], key: str) -> bool:
    return str(row.get(key, "")).strip() == "applied"


def _worker_syncback_applied(
    row: Dict[str, Any],
    *,
    status_key: str,
    summary_key: str,
    sync_at_key: str,
    accept_at_key: str,
) -> bool:
    status = str(row.get(status_key, "")).strip()
    summary = str(row.get(summary_key, "")).strip()
    if status != "applied" and summary in {"", "-"}:
        return False
    sync_at = str(row.get(sync_at_key, "")).strip()
    accept_at = str(row.get(accept_at_key, "")).strip()
    if sync_at and accept_at:
        return sync_at >= accept_at
    return True


def _recovery_latest_planning_compact_summary(row: Dict[str, Any]) -> str:
    legacy_summary = str(row.get("latest_planning_review_summary", "")).strip()
    return (
        str(row.get("latest_planning_compact_summary", "")).strip()
        or legacy_summary
        or "-"
    )


def _build_recovery_task_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    project_alias: str,
    manager_state: Dict[str, Any],
    root_team_dir: Path,
) -> List[RecoveryTaskDTO]:
    built: List[RecoveryTaskDTO] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        preset = row.get("preset") if isinstance(row.get("preset"), dict) else {}
        request_id = str(row.get("request_id", "")).strip()
        label = str(row.get("label", "")).strip() or "-"
        rerun_summary = str(row.get("rerun_summary", "")).strip() or "-"
        followup_summary = str(row.get("followup_summary", "")).strip() or "-"
        rate_limit_summary = str(row.get("rate_limit_summary", "")).strip() or "-"
        contract = row.get("completion_contract") if isinstance(row.get("completion_contract"), dict) else {}
        observatory = row.get("observatory") if isinstance(row.get("observatory"), dict) else {}
        chat_console_path, chat_console_label = _chat_console_target(
            manager_state,
            root_team_dir=root_team_dir,
            project_alias=project_alias,
            request_id=request_id,
        )
        action_contract = _task_command_contract(
            project_alias=project_alias,
            label=label,
            request_id=request_id,
            tf_phase=str(row.get("tf_phase", "")).strip(),
            rerun_summary=rerun_summary,
            followup_summary=followup_summary,
            rate_limit_summary=rate_limit_summary,
            execution_brief_status=str(row.get("execution_brief_status", "")).strip(),
        )
        safe_action_buttons, phase2_action_buttons = _task_action_buttons(
            label=label,
            request_id=request_id,
            phase2_commands=list(action_contract.get("phase2") or []),
        )
        phase2_action_buttons = _filter_dispatch_phase2_action_buttons(
            phase2_action_buttons,
            task=row,
        )
        worker_apply_applied = _worker_apply_applied(row, "background_run_worker_apply_accept_status")
        worker_syncback_applied = _worker_syncback_applied(
            row,
            status_key="background_run_worker_syncback_status",
            summary_key="background_run_worker_syncback_summary",
            sync_at_key="background_run_worker_syncback_at",
            accept_at_key="background_run_worker_apply_accept_at",
        )
        worker_syncback_ready = _worker_syncback_ready(
            row,
            records_summary_key="background_run_worker_records_summary",
            records_key="background_run_worker_records",
        )
        worker_apply_ready = _worker_apply_ready(row)
        if not worker_apply_applied and worker_apply_ready:
            safe_action_buttons = _append_unique_action_button(
                safe_action_buttons,
                _worker_apply_preview_button(
                    label=label,
                    request_id=request_id,
                    update_stub={
                        "status": row.get("background_run_worker_update_stub_status"),
                        "summary_line": row.get("background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("background_run_worker_update_proposal_ids") or [],
                ),
            )
        elif worker_apply_applied and worker_syncback_ready and not worker_syncback_applied:
            safe_action_buttons = _append_unique_action_button(
                safe_action_buttons,
                _worker_apply_syncback_preview_button(project_alias=project_alias),
            )
        elif not worker_apply_applied:
            worker_blocker_button = _worker_blocker_action_button(
                project_alias=project_alias,
                label=label,
                request_id=request_id,
                task=row,
                followup_brief_execution_lane_ids_key="followup_brief_execution_lanes",
                followup_brief_review_lane_ids_key="followup_brief_review_lanes",
            )
            if worker_blocker_button is not None and str(worker_blocker_button.mode).strip() == "phase2":
                phase2_action_buttons = _append_unique_action_button(phase2_action_buttons, worker_blocker_button)
            else:
                safe_action_buttons = _append_unique_action_button(safe_action_buttons, worker_blocker_button)
        safe_action_buttons = _append_unique_action_button(
            safe_action_buttons,
            _worker_update_preview_button(
                label=label,
                request_id=request_id,
                update_stub={
                    "status": row.get("background_run_worker_update_stub_status"),
                    "summary_line": row.get("background_run_worker_update_stub_summary"),
                    "target_artifacts": str(row.get("background_run_worker_update_stub_targets", "")).split(","),
                },
                proposal_ids=row.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not worker_apply_applied and worker_apply_ready:
            phase2_action_buttons = _append_unique_action_button(
                phase2_action_buttons,
                _worker_apply_proposal_button(
                    label=label,
                    request_id=request_id,
                    update_stub={
                        "status": row.get("background_run_worker_update_stub_status"),
                        "summary_line": row.get("background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("background_run_worker_update_proposal_ids") or [],
                ),
            )
        phase2_action_buttons = _append_unique_action_button(
            phase2_action_buttons,
            _worker_update_proposal_accept_button(
                project_alias=project_alias,
                proposal_ids=row.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not worker_apply_applied and worker_apply_ready:
            phase2_action_buttons = _append_unique_action_button(
                phase2_action_buttons,
                _worker_apply_proposal_accept_button(
                    label=label,
                    request_id=str(request_id or ""),
                    project_alias=project_alias,
                    proposal_ids=row.get("background_run_worker_update_proposal_ids") or [],
                    proposal_summary=row.get("background_run_worker_update_proposal_summary"),
                ),
            )
        elif worker_apply_applied and worker_syncback_ready and not worker_syncback_applied:
            phase2_action_buttons = _append_unique_action_button(
                phase2_action_buttons,
                _worker_apply_syncback_apply_button(project_alias=project_alias),
            )
        built.append(
            RecoveryTaskDTO(
                request_id=request_id,
                label=label,
                detail_path=_detail_path(request_id),
                chat_console_path=chat_console_path,
                chat_console_label=chat_console_label,
                status=str(row.get("status", "")).strip() or "-",
                tf_phase=str(row.get("tf_phase", "")).strip() or "-",
                preset="phase1={phase1} phase2={phase2}".format(
                    phase1=str(preset.get("phase1", "")).strip() or "-",
                    phase2=str(preset.get("phase2", "")).strip() or "-",
                ),
                phase2_shape=str(row.get("phase2_shape", "")).strip() or "-",
                phase2_quality=str(row.get("phase2_quality", "")).strip() or "-",
                lane_summary=str(row.get("lane_summary", "")).strip() or "-",
                rerun_summary=rerun_summary,
                followup_summary=followup_summary,
                completion_focus=str(contract.get("focus", "")).strip() or "-",
                completion_done_when=str(contract.get("done_when", "")).strip() or "-",
                completion_rerun_when=str(contract.get("rerun_when", "")).strip() or "-",
                completion_followup_when=str(contract.get("manual_followup_when", "")).strip() or "-",
                backend_summary=str(row.get("backend_summary", "")).strip() or "-",
                backend_note=str(row.get("backend_note", "")).strip(),
                rate_limit_summary=rate_limit_summary,
                observatory_headline=str(observatory.get("headline", "")).strip() or "-",
                observatory_first_focus=str(observatory.get("first_focus", "")).strip() or "-",
                observatory_stale_lane_count=int(observatory.get("stale_lane_count", 0) or 0),
                observatory_bottleneck_lane=str(observatory.get("bottleneck_lane", "")).strip() or "-",
                observatory_bottleneck_reason=str(observatory.get("bottleneck_reason", "")).strip() or "-",
                observatory_conflict_file_count=int(observatory.get("conflict_file_count", 0) or 0),
                observatory_touched_file_count=int(observatory.get("touched_file_count", 0) or 0),
                command_hints=list(action_contract.get("safe") or []),
                phase2_action_hints=list(action_contract.get("phase2") or []),
                safe_action_buttons=safe_action_buttons,
                phase2_action_buttons=phase2_action_buttons,
            )
        )
    return built


def _build_recovery_runtime_rows(
    rows: Iterable[Dict[str, Any]],
    *,
    manager_state: Dict[str, Any],
    root_team_dir: Path,
) -> List[RecoveryRuntimeDTO]:
    built: List[RecoveryRuntimeDTO] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        alias = str(row.get("project_alias", "")).strip().upper()
        label = str(row.get("project_label", "")).strip() or alias or "-"
        active_request_id = str(row.get("active_task_request_id", "")).strip()
        active_contract = row.get("active_task_completion_contract") if isinstance(row.get("active_task_completion_contract"), dict) else {}
        active_rate_limit = str(row.get("active_task_rate_limit", "")).strip() or "-"
        task_rows = row.get("task_teams") or []
        active_task_row = next(
            (
                item
                for item in task_rows
                if isinstance(item, dict) and str(item.get("request_id", "")).strip() == active_request_id
            ),
            {},
        )
        chat_console_path, chat_console_label = _chat_console_target(
            manager_state,
            root_team_dir=root_team_dir,
            project_alias=alias,
            request_id=active_request_id,
        )
        runtime_action_contract = _runtime_command_contract(
            project_alias=alias,
            priority_action=str(row.get("priority_action", "")).strip(),
            has_active_task=bool(active_request_id),
            has_rate_limit=active_rate_limit != "-",
            background_queue_stale_count=int(row.get("background_queue_stale_count", 0) or 0),
        )
        active_task_action_contract = (
            _task_command_contract(
                project_alias=alias,
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                tf_phase=str(row.get("active_task_phase", "")).strip(),
                rerun_summary=str(active_task_row.get("rerun_summary", "")).strip() or "-",
                followup_summary=str(active_task_row.get("followup_summary", "")).strip() or "-",
                rate_limit_summary=active_rate_limit,
                execution_brief_status=str(active_task_row.get("execution_brief_status", "")).strip(),
            )
            if active_request_id
            else {"safe": [], "phase2": []}
        )
        runtime_safe_action_buttons, runtime_phase2_action_buttons = _runtime_action_buttons(
            project_alias=alias,
            phase2_commands=list(runtime_action_contract.get("phase2") or []),
        )
        runtime_phase2_action_buttons = _filter_dispatch_phase2_action_buttons(
            runtime_phase2_action_buttons,
            task=active_task_row or {},
        )
        runtime_manual_route_button = _replan_manual_route_action_button(
            project_alias=alias,
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=row.get("latest_replan_auto_routing_policy") if isinstance(row.get("latest_replan_auto_routing_policy"), dict) else {},
            task=active_task_row or {},
        )
        if runtime_manual_route_button is not None and str(runtime_manual_route_button.mode).strip() == "phase2":
            runtime_phase2_action_buttons = _append_unique_action_button(runtime_phase2_action_buttons, runtime_manual_route_button)
        else:
            runtime_safe_action_buttons = _append_unique_action_button(runtime_safe_action_buttons, runtime_manual_route_button)
        runtime_worker_apply_applied = _worker_apply_applied(row, "active_task_background_run_worker_apply_accept_status")
        runtime_worker_syncback_applied = _worker_syncback_applied(
            row,
            status_key="active_task_background_run_worker_syncback_status",
            summary_key="active_task_background_run_worker_syncback_summary",
            sync_at_key="active_task_background_run_worker_syncback_at",
            accept_at_key="active_task_background_run_worker_apply_accept_at",
        )
        runtime_worker_syncback_ready = _worker_syncback_ready(
            row,
            records_summary_key="active_task_background_run_worker_records_summary",
            records_key="active_task_background_run_worker_records",
            record_rows_summary_key="active_task_background_run_worker_record_rows_summary",
            record_rows_key="active_task_background_run_worker_record_rows",
        )
        runtime_worker_apply_ready = _worker_apply_ready(
            row,
            module_key="active_task_background_run_task_contract_module",
            record_rows_summary_key="active_task_background_run_worker_record_rows_summary",
            record_rows_key="active_task_background_run_worker_record_rows",
        )
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            runtime_safe_action_buttons = _append_unique_action_button(
                runtime_safe_action_buttons,
                _worker_apply_preview_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": row.get("active_task_background_run_worker_update_stub_status"),
                        "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                ),
            )
        elif runtime_worker_apply_applied and runtime_worker_syncback_ready and not runtime_worker_syncback_applied:
            runtime_safe_action_buttons = _append_unique_action_button(
                runtime_safe_action_buttons,
                _worker_apply_syncback_preview_button(project_alias=alias),
            )
        elif not runtime_worker_apply_applied:
            runtime_worker_blocker_button = _worker_blocker_action_button(
                project_alias=alias,
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                task=row,
                followup_brief_status_key="active_task_followup_brief_status",
                followup_brief_execution_lane_ids_key="active_task_followup_brief_execution_lanes",
                followup_brief_review_lane_ids_key="active_task_followup_brief_review_lanes",
                module_key="active_task_background_run_task_contract_module",
                preflight_rows_summary_key="active_task_background_run_worker_preflight_rows_summary",
                preflight_rows_key="active_task_background_run_worker_preflight_rows",
                preflight_summary_key="active_task_background_run_worker_preflight_summary",
                preflight_status_key="active_task_background_run_worker_preflight_status",
                record_rows_summary_key="active_task_background_run_worker_record_rows_summary",
                record_rows_key="active_task_background_run_worker_record_rows",
                result_status_key="active_task_background_run_worker_result_status",
                result_summary_key="active_task_background_run_worker_result_summary",
                result_actions_key="active_task_background_run_worker_result_actions",
                result_cautions_key="active_task_background_run_worker_result_cautions",
                result_evidence_refs_key="active_task_background_run_worker_result_evidence_refs",
            )
            if runtime_worker_blocker_button is not None and str(runtime_worker_blocker_button.mode).strip() == "phase2":
                runtime_phase2_action_buttons = _append_unique_action_button(
                    runtime_phase2_action_buttons,
                    runtime_worker_blocker_button,
                )
            else:
                runtime_safe_action_buttons = _append_unique_action_button(
                    runtime_safe_action_buttons,
                    runtime_worker_blocker_button,
                )
        runtime_safe_action_buttons = _append_unique_action_button(
            runtime_safe_action_buttons,
            _worker_update_preview_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                update_stub={
                    "status": row.get("active_task_background_run_worker_update_stub_status"),
                    "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                    "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                },
                proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_proposal_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": row.get("active_task_background_run_worker_update_stub_status"),
                        "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                ),
            )
        runtime_phase2_action_buttons = _append_unique_action_button(
            runtime_phase2_action_buttons,
            _worker_update_proposal_accept_button(
                project_alias=alias,
                proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_proposal_accept_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    project_alias=alias,
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                    proposal_summary=row.get("active_task_background_run_worker_update_proposal_summary"),
                ),
            )
        elif runtime_worker_apply_applied and runtime_worker_syncback_ready and not runtime_worker_syncback_applied:
            runtime_phase2_action_buttons = _append_unique_action_button(
                runtime_phase2_action_buttons,
                _worker_apply_syncback_apply_button(project_alias=alias),
            )
        active_task_safe_action_buttons, active_task_phase2_action_buttons = _task_action_buttons(
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            phase2_commands=list(active_task_action_contract.get("phase2") or []),
            include_followup_preview=bool(active_request_id),
        )
        active_task_safe_action_buttons = _filter_manual_route_action_buttons(
            active_task_safe_action_buttons,
            task=active_task_row or {},
        )
        active_task_phase2_action_buttons = _filter_manual_route_action_buttons(
            active_task_phase2_action_buttons,
            task=active_task_row or {},
        )
        active_task_manual_route_button = _replan_manual_route_action_button(
            project_alias=alias,
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            policy=row.get("latest_replan_auto_routing_policy") if isinstance(row.get("latest_replan_auto_routing_policy"), dict) else {},
            task=active_task_row or {},
        )
        if active_task_manual_route_button is not None and str(active_task_manual_route_button.mode).strip() == "phase2":
            active_task_phase2_action_buttons = _append_unique_action_button(active_task_phase2_action_buttons, active_task_manual_route_button)
        else:
            active_task_safe_action_buttons = _append_unique_action_button(active_task_safe_action_buttons, active_task_manual_route_button)
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            active_task_safe_action_buttons = _append_unique_action_button(
                active_task_safe_action_buttons,
                _worker_apply_preview_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": row.get("active_task_background_run_worker_update_stub_status"),
                        "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                ),
            )
        elif runtime_worker_apply_applied and runtime_worker_syncback_ready and not runtime_worker_syncback_applied:
            active_task_safe_action_buttons = _append_unique_action_button(
                active_task_safe_action_buttons,
                _worker_apply_syncback_preview_button(project_alias=alias),
            )
        elif not runtime_worker_apply_applied:
            active_task_worker_blocker_button = _worker_blocker_action_button(
                project_alias=alias,
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                task=row,
                followup_brief_status_key="active_task_followup_brief_status",
                followup_brief_execution_lane_ids_key="active_task_followup_brief_execution_lanes",
                followup_brief_review_lane_ids_key="active_task_followup_brief_review_lanes",
                module_key="active_task_background_run_task_contract_module",
                preflight_rows_summary_key="active_task_background_run_worker_preflight_rows_summary",
                preflight_rows_key="active_task_background_run_worker_preflight_rows",
                preflight_summary_key="active_task_background_run_worker_preflight_summary",
                preflight_status_key="active_task_background_run_worker_preflight_status",
                record_rows_summary_key="active_task_background_run_worker_record_rows_summary",
                record_rows_key="active_task_background_run_worker_record_rows",
                result_status_key="active_task_background_run_worker_result_status",
                result_summary_key="active_task_background_run_worker_result_summary",
                result_actions_key="active_task_background_run_worker_result_actions",
                result_cautions_key="active_task_background_run_worker_result_cautions",
                result_evidence_refs_key="active_task_background_run_worker_result_evidence_refs",
            )
            if active_task_worker_blocker_button is not None and str(active_task_worker_blocker_button.mode).strip() == "phase2":
                active_task_phase2_action_buttons = _append_unique_action_button(
                    active_task_phase2_action_buttons,
                    active_task_worker_blocker_button,
                )
            else:
                active_task_safe_action_buttons = _append_unique_action_button(
                    active_task_safe_action_buttons,
                    active_task_worker_blocker_button,
                )
        active_task_safe_action_buttons = _append_unique_action_button(
            active_task_safe_action_buttons,
            _worker_update_preview_button(
                label=str(row.get("active_task_label", "")).strip(),
                request_id=active_request_id,
                update_stub={
                    "status": row.get("active_task_background_run_worker_update_stub_status"),
                    "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                    "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                },
                proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            active_task_phase2_action_buttons = _append_unique_action_button(
                active_task_phase2_action_buttons,
                _worker_apply_proposal_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    update_stub={
                        "status": row.get("active_task_background_run_worker_update_stub_status"),
                        "summary_line": row.get("active_task_background_run_worker_update_stub_summary"),
                        "target_artifacts": str(row.get("active_task_background_run_worker_update_stub_targets", "")).split(","),
                    },
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                ),
            )
        active_task_phase2_action_buttons = _append_unique_action_button(
            active_task_phase2_action_buttons,
            _worker_update_proposal_accept_button(
                project_alias=alias,
                proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
            ),
        )
        if not runtime_worker_apply_applied and runtime_worker_apply_ready:
            active_task_phase2_action_buttons = _append_unique_action_button(
                active_task_phase2_action_buttons,
                _worker_apply_proposal_accept_button(
                    label=str(row.get("active_task_label", "")).strip(),
                    request_id=active_request_id,
                    project_alias=alias,
                    proposal_ids=row.get("active_task_background_run_worker_update_proposal_ids") or [],
                    proposal_summary=row.get("active_task_background_run_worker_update_proposal_summary"),
                ),
            )
        elif runtime_worker_apply_applied and runtime_worker_syncback_ready and not runtime_worker_syncback_applied:
            active_task_phase2_action_buttons = _append_unique_action_button(
                active_task_phase2_action_buttons,
                _worker_apply_syncback_apply_button(project_alias=alias),
            )
        active_task_phase2_action_buttons = _filter_dispatch_phase2_action_buttons(
            active_task_phase2_action_buttons,
            task=active_task_row or {},
        )
        runtime_phase2_action_buttons = _filter_dispatch_phase2_action_buttons(
            runtime_phase2_action_buttons,
            task=active_task_row or {},
        )
        built.append(
            RecoveryRuntimeDTO(
                project_key=str(row.get("project_key", "")).strip() or alias,
                project_alias=alias or "-",
                project_label=label,
                runtime_path=_runtime_path(alias),
                chat_console_path=chat_console_path,
                chat_console_label=chat_console_label,
                status=str(row.get("status", "")).strip() or "-",
                readiness=str(row.get("readiness", "")).strip() or "-",
                attention_summary=str(row.get("attention_summary", "")).strip() or "-",
                priority_action=str(row.get("priority_action", "")).strip() or "-",
                priority_reason=str(row.get("priority_reason", "")).strip() or "-",
                next_focus=str(row.get("next_focus", "")).strip() or "-",
                queue_summary=str(row.get("queue_summary", "")).strip() or "-",
                proposal_summary=str(row.get("proposal_summary", "")).strip() or "-",
                sync_summary=str(row.get("sync_summary", "")).strip() or "-",
                provider_pressure_summary=str(row.get("provider_pressure_summary", "")).strip() or "-",
                repeat_summary=str(row.get("repeat_summary", "")).strip() or "-",
                completed_task_count=int(row.get("completed_task_count", 0) or 0),
                blocked_task_count=int(row.get("blocked_task_count", 0) or 0),
                parked_task_count=int(row.get("parked_task_count", 0) or 0),
                active_task_label=str(row.get("active_task_label", "")).strip(),
                active_task_path=_detail_path(active_request_id) if active_request_id else "",
                active_task_status=str(row.get("active_task_status", "")).strip() or "-",
                active_task_phase=str(row.get("active_task_phase", "")).strip() or "-",
                active_task_preset=str(row.get("active_task_preset", "")).strip() or "-",
                active_task_phase2_shape=str(row.get("active_task_phase2_shape", "")).strip() or "-",
                active_task_phase2_quality=str(row.get("active_task_phase2_quality", "")).strip() or "-",
                active_task_context_pack_summary=str(row.get("active_task_context_pack_summary", "")).strip() or "-",
                active_task_model_plan_summary=str(row.get("active_task_model_plan_summary", "")).strip() or "-",
                active_task_reentry_rails_summary=str(row.get("active_task_reentry_rails_summary", "")).strip() or "-",
                active_task_background_run_status=str(row.get("active_task_background_run_status", "")).strip() or "-",
                active_task_background_run_runner_target=str(row.get("active_task_background_run_runner_target", "")).strip() or "-",
                active_task_background_run_ticket_id=str(row.get("active_task_background_run_ticket_id", "")).strip() or "-",
                active_task_background_run_evidence_bundle=str(row.get("active_task_background_run_evidence_bundle", "")).strip() or "-",
                active_task_background_run_evidence_artifacts=str(row.get("active_task_background_run_evidence_artifacts", "")).strip() or "-",
                active_task_background_run_external_phase=str(row.get("active_task_background_run_external_phase", "")).strip() or "-",
                active_task_background_run_external_note=str(row.get("active_task_background_run_external_note", "")).strip() or "-",
                active_task_background_run_launch_spec_summary=str(row.get("active_task_background_run_launch_spec_summary", "")).strip() or "-",
                active_task_background_run_worker_update_operator_summary=(
                    str(row.get("active_task_background_run_worker_update_operator_summary", "")).strip() or "-"
                ),
                active_task_background_run_worker_update_proposal_summary=(
                    str(row.get("active_task_background_run_worker_update_proposal_summary", "")).strip() or "-"
                ),
                active_task_background_run_worker_apply_accept_summary=(
                    str(row.get("active_task_background_run_worker_apply_accept_summary", "")).strip() or "-"
                ),
                active_task_background_run_worker_syncback_summary=(
                    str(row.get("active_task_background_run_worker_syncback_summary", "")).strip() or "-"
                ),
                active_task_background_run_model_plan_summary=str(row.get("active_task_background_run_model_plan_summary", "")).strip() or "-",
                workspace_summary=str(row.get("workspace_summary", "")).strip() or "-",
                document_registry_summary=str(row.get("document_registry_summary", "")).strip() or "-",
                model_routing_summary=str(row.get("model_routing_summary", "")).strip() or "-",
                model_registry_summary=str(row.get("model_registry_summary", "")).strip() or "-",
                latest_judge_summary=str(row.get("latest_judge_summary", "")).strip() or "-",
                latest_judge_decision_summary=str(row.get("latest_judge_decision_summary", "")).strip() or "-",
                latest_judge_decision_bridge_summary=str(row.get("latest_judge_decision_bridge_summary", "")).strip() or "-",
                latest_replan_auto_decision_summary=str(row.get("latest_replan_auto_decision_summary", "")).strip() or "-",
                latest_replan_auto_routing_policy_summary=str(row.get("latest_replan_auto_routing_policy_summary", "")).strip() or "-",
                latest_replan_auto_route_summary=str(row.get("latest_replan_auto_route_summary", "")).strip() or "-",
                latest_replan_auto_route_status_summary=str(row.get("latest_replan_auto_route_status_summary", "")).strip() or "-",
                latest_replan_auto_operator_summary=(
                    str(row.get("replan_auto_route_operator_summary", "")).strip()
                    or str(row.get("latest_replan_auto_route_status_summary", "")).strip()
                    or "-"
                ),
                latest_planning_handoff_summary=str(row.get("latest_planning_handoff_summary", "")).strip() or "-",
                latest_planning_compact_summary=_recovery_latest_planning_compact_summary(row),
                latest_manual_step_summary=str(row.get("latest_manual_step_summary", "")).strip() or "-",
                latest_canonical_writeback_summary=(
                    str(row.get("latest_canonical_writeback_summary", "")).strip() or "-"
                ),
                latest_canonical_mutation_summary=(
                    str(row.get("latest_canonical_mutation_summary", "")).strip() or "-"
                ),
                run_lock_mode=str(row.get("run_lock_mode", "")).strip() or "-",
                run_lock_note=str(row.get("run_lock_note", "")).strip() or "-",
                background_slot_limit=int(row.get("background_slot_limit", 1) or 1),
                background_slot_active=int(row.get("background_slot_active", 0) or 0),
                background_slot_pressure=str(row.get("background_slot_pressure", "")).strip() or "-",
                background_worker_status=str(row.get("background_worker_status", "")).strip() or "-",
                background_worker_summary=str(row.get("background_worker_summary", "")).strip() or "-",
                background_queue_summary=str(row.get("background_queue_summary", "")).strip() or "-",
                background_scheduler_summary=str(row.get("background_scheduler_summary", "")).strip() or "-",
                background_scheduler_note=_background_scheduler_note(
                    str(row.get("background_scheduler_summary", "")).strip() or "-"
                ),
                background_queue_depth=int(row.get("background_queue_depth", 0) or 0),
                background_queue_stale_count=int(row.get("background_queue_stale_count", 0) or 0),
                active_task_completion_focus=str(active_contract.get("focus", "")).strip() or "-",
                active_task_completion_done=str(active_contract.get("done_when", "")).strip() or "-",
                active_task_completion_rerun=str(active_contract.get("rerun_when", "")).strip() or "-",
                active_task_completion_followup=str(active_contract.get("manual_followup_when", "")).strip() or "-",
                active_task_backend=str(row.get("active_task_backend", "")).strip() or "-",
                active_task_backend_note=str(row.get("active_task_backend_note", "")).strip(),
                active_task_rate_limit=active_rate_limit,
                runtime_command_hints=list(runtime_action_contract.get("safe") or []),
                runtime_phase2_action_hints=list(runtime_action_contract.get("phase2") or []),
                active_task_command_hints=list(active_task_action_contract.get("safe") or []),
                active_task_phase2_action_hints=list(active_task_action_contract.get("phase2") or []),
                runtime_safe_action_buttons=runtime_safe_action_buttons,
                runtime_phase2_action_buttons=runtime_phase2_action_buttons,
                active_task_safe_action_buttons=active_task_safe_action_buttons,
                active_task_phase2_action_buttons=active_task_phase2_action_buttons,
                task_teams=_build_recovery_task_rows(
                    row.get("task_teams") or [],
                    project_alias=alias,
                    manager_state=manager_state,
                    root_team_dir=root_team_dir,
                ),
            )
        )
    return built


def _build_recovery_summary(
    summary_state: Dict[str, Any],
    freshness: FileFreshnessDTO,
    *,
    manager_state: Dict[str, Any],
    root_team_dir: Path,
) -> RecoverySummaryDTO:
    control = summary_state.get("control_summary") if isinstance(summary_state.get("control_summary"), dict) else {}
    server_guard = _server_guard_from_recovery_control(control)
    return RecoverySummaryDTO(
        exists=bool(freshness.exists and summary_state),
        artifact_path=freshness.path,
        updated_at=freshness.updated_at,
        stale=bool(freshness.stale),
        error=str(freshness.error or "").strip(),
        generated_at=str(summary_state.get("generated_at", "")).strip() or "-",
        snapshot_taken_at=str(summary_state.get("snapshot_taken_at", "")).strip() or "-",
        automation_posture=str(control.get("automation_posture", "")).strip() or "-",
        auto_mode=str(control.get("auto_mode", "")).strip() or "-",
        offdesk_mode=str(control.get("offdesk_mode", "")).strip() or "-",
        provider_capacity_summary=str(control.get("provider_capacity_summary", "")).strip() or "-",
        next_retry_at=str(control.get("next_retry_at", "")).strip() or "-",
        next_retry_target=str(control.get("next_retry_target", "")).strip() or "-",
        repeat_memory_summary=str(control.get("repeat_memory_summary", "")).strip() or "-",
        execution_brief_summary=str(control.get("execution_brief_summary", "")).strip() or "-",
        background_run_summary=str(control.get("background_run_summary", "")).strip() or "-",
        background_worker_summary=str(control.get("background_worker_summary", "")).strip() or "-",
        latest_intent_command=str(control.get("latest_intent_command", "")).strip() or "-",
        latest_intent_action=str(control.get("latest_intent_action", "")).strip() or "-",
        latest_intent_trace=str(control.get("latest_intent_trace", "")).strip() or "-",
        latest_intent_focus=str(control.get("latest_intent_focus", "")).strip() or operator_summary.latest_intent_focus(
            str(control.get("latest_intent_action", "")).strip(),
            str(control.get("latest_intent_trace", "")).strip(),
        ),
        server_guard=server_guard,
        control_phase2_action_buttons=_recovery_control_action_buttons(),
        runtimes=_build_recovery_runtime_rows(
            summary_state.get("runtimes") or [],
            manager_state=manager_state,
            root_team_dir=root_team_dir,
        ),
    )
