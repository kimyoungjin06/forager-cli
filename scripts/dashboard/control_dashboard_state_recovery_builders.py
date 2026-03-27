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
    _detail_path,
    _recovery_control_action_buttons,
    _runtime_action_buttons,
    _runtime_command_contract,
    _runtime_path,
    _task_action_buttons,
    _task_command_contract,
)
from control_dashboard_state_io import FileFreshnessDTO
from control_dashboard_state_models import RecoveryRuntimeDTO, RecoverySummaryDTO, RecoveryTaskDTO


def _build_recovery_task_rows(rows: Iterable[Dict[str, Any]], *, project_alias: str) -> List[RecoveryTaskDTO]:
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
        action_contract = _task_command_contract(
            project_alias=project_alias,
            label=label,
            request_id=request_id,
            tf_phase=str(row.get("tf_phase", "")).strip(),
            rerun_summary=rerun_summary,
            followup_summary=followup_summary,
            rate_limit_summary=rate_limit_summary,
        )
        safe_action_buttons, phase2_action_buttons = _task_action_buttons(
            label=label,
            request_id=request_id,
            phase2_commands=list(action_contract.get("phase2") or []),
        )
        built.append(
            RecoveryTaskDTO(
                request_id=request_id,
                label=label,
                detail_path=_detail_path(request_id),
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
                command_hints=list(action_contract.get("safe") or []),
                phase2_action_hints=list(action_contract.get("phase2") or []),
                safe_action_buttons=safe_action_buttons,
                phase2_action_buttons=phase2_action_buttons,
            )
        )
    return built


def _build_recovery_runtime_rows(rows: Iterable[Dict[str, Any]]) -> List[RecoveryRuntimeDTO]:
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
        runtime_action_contract = _runtime_command_contract(
            project_alias=alias,
            priority_action=str(row.get("priority_action", "")).strip(),
            has_active_task=bool(active_request_id),
            has_rate_limit=active_rate_limit != "-",
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
            )
            if active_request_id
            else {"safe": [], "phase2": []}
        )
        runtime_safe_action_buttons, runtime_phase2_action_buttons = _runtime_action_buttons(
            project_alias=alias,
            phase2_commands=list(runtime_action_contract.get("phase2") or []),
        )
        active_task_safe_action_buttons, active_task_phase2_action_buttons = _task_action_buttons(
            label=str(row.get("active_task_label", "")).strip(),
            request_id=active_request_id,
            phase2_commands=list(active_task_action_contract.get("phase2") or []),
            include_followup_preview=bool(active_request_id),
        )
        built.append(
            RecoveryRuntimeDTO(
                project_key=str(row.get("project_key", "")).strip() or alias,
                project_alias=alias or "-",
                project_label=label,
                runtime_path=_runtime_path(alias),
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
                task_teams=_build_recovery_task_rows(row.get("task_teams") or [], project_alias=alias),
            )
        )
    return built


def _build_recovery_summary(summary_state: Dict[str, Any], freshness: FileFreshnessDTO) -> RecoverySummaryDTO:
    control = summary_state.get("control_summary") if isinstance(summary_state.get("control_summary"), dict) else {}
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
        latest_intent_command=str(control.get("latest_intent_command", "")).strip() or "-",
        latest_intent_action=str(control.get("latest_intent_action", "")).strip() or "-",
        latest_intent_trace=str(control.get("latest_intent_trace", "")).strip() or "-",
        latest_intent_focus=str(control.get("latest_intent_focus", "")).strip() or operator_summary.latest_intent_focus(
            str(control.get("latest_intent_action", "")).strip(),
            str(control.get("latest_intent_trace", "")).strip(),
        ),
        control_phase2_action_buttons=_recovery_control_action_buttons(),
        runtimes=_build_recovery_runtime_rows(summary_state.get("runtimes") or []),
    )

