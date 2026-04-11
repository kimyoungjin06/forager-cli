#!/usr/bin/env python3
"""Task-scoped dashboard state builders."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_ops_policy as ops_policy
import aoe_tg_action_audit as action_audit
import aoe_tg_background_runs as background_runs
import aoe_tg_context_pack as context_pack
import aoe_tg_model_endpoint_adapter as model_endpoint_adapter
import aoe_tg_run_lock as run_lock
import aoe_tg_runtime_read as runtime_read
import aoe_tg_task_state as task_state
import aoe_tg_task_view as task_view
import aoe_tg_team_observatory as team_observatory
import aoe_tg_worker_task_contract as worker_task_contract

from control_dashboard_state_common import (
    _append_unique_action_button,
    _compose_backend_summary,
    _compose_lane_summary,
    _compose_phase2_shape,
    _compose_task_quality,
    _completion_contract_for_preset,
    _detail_path,
    _replan_manual_route_action_button,
    _replan_auto_route_action_button,
    _task_action_buttons,
    _task_command_contract,
    _task_followup_summary,
    _task_phase1_progress,
    _task_phase1_summary,
    _task_rate_limit_summary,
    _task_rerun_summary,
    _runtime_path,
    _worker_apply_proposal_button,
    _worker_apply_preview_button,
    _worker_apply_proposal_accept_button,
    _worker_update_proposal_accept_button,
    _worker_update_preview_button,
)
from control_dashboard_state_models import ActiveTaskRowDTO, LaneObservatoryDTO, TaskDetailDTO


def _worker_update_operator_summary(task: Dict[str, Any]) -> str:
    return worker_task_contract.summarize_worker_update_operator_summary(
        {
            "status": task.get("background_run_worker_update_stub_status"),
            "summary_line": task.get("background_run_worker_update_stub_summary"),
            "target_artifacts": task.get("background_run_worker_update_stub_targets"),
        },
        task.get("background_run_worker_update_proposal_ids"),
    )


def _selected_slot_runner(entry: Dict[str, Any], task: Dict[str, Any]) -> str:
    task_runner = str(task.get("background_run_runner_target", "")).strip().lower()
    if task_runner in background_runs.SLOT_RUNNER_TARGETS:
        return task_runner
    preferred_runner = str(entry.get("background_runner_target", "")).strip().lower()
    return preferred_runner if preferred_runner in background_runs.SLOT_RUNNER_TARGETS else ""


def _background_slot_snapshot(entry: Dict[str, Any], task: Dict[str, Any], team_dir: Path) -> Dict[str, Any]:
    return background_runs.summarize_background_runner_slots(
        background_runs.background_runs_state_path(team_dir),
        entry,
        selected_runner=_selected_slot_runner(entry, task),
        statuses=["queued", "dispatching", "running"],
    )


def _observatory_lane_rows(snapshot: Dict[str, Any]) -> List[LaneObservatoryDTO]:
    rows: List[LaneObservatoryDTO] = []
    for row in snapshot.get("lanes") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            LaneObservatoryDTO(
                lane_id=str(row.get("lane_id", "")).strip() or "-",
                phase=str(row.get("phase", "")).strip() or "-",
                role=str(row.get("role", "")).strip() or "-",
                status=str(row.get("status", "")).strip() or "-",
                age_text=str(row.get("age_text", "")).strip() or "-",
                idle_text=str(row.get("idle_text", "")).strip() or "-",
                note=str(row.get("note", "")).strip() or "-",
                freshness_scope=str(row.get("freshness_scope", "")).strip() or "-",
                last_event_kind=str(row.get("last_event_kind", "")).strip() or "-",
                backend=str(row.get("backend", "")).strip() or "-",
                tool_count=int(row.get("tool_count", 0) or 0),
                touched_file_count=int(row.get("touched_file_count", 0) or 0),
                touched_file_summary=str(row.get("touched_file_summary", "")).strip() or "-",
                conflict_file_count=int(row.get("conflict_file_count", 0) or 0),
                conflict_summary=str(row.get("conflict_summary", "")).strip() or "-",
                is_stale=bool(row.get("is_stale")),
            )
        )
    return rows


def _build_active_task_rows(manager_state: Dict[str, Any], *, cap: int = 60) -> List[ActiveTaskRowDTO]:
    rows: List[ActiveTaskRowDTO] = []
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    for key, entry in ops_policy.list_ops_projects(projects, skip_paused=False, require_ready=False):
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key)
        display = str(entry.get("display_name", "")).strip() or str(key)
        tasks = task_state.ensure_project_tasks(entry)
        for request_id, task in tasks.items():
            if not isinstance(task, dict):
                continue
            status = runtime_read.normalize_task_status(task.get("status", "pending"))
            if status == "completed":
                continue
            snap = task_state.task_monitor_row_snapshot(
                task,
                str(request_id or "").strip(),
                normalize_task_status=runtime_read.normalize_task_status,
                task_display_label=task_view.task_display_label,
            )
            preset = str(snap.get("phase2_team_preset", "")).strip() or str(snap.get("phase1_role_preset", "")).strip() or "-"
            rows.append(
                ActiveTaskRowDTO(
                    project_key=str(key),
                    project_alias=alias,
                    project_label=display,
                    runtime_path=_runtime_path(alias),
                    request_id=str(snap.get("request_id", "")).strip(),
                    label=str(snap.get("label", "")).strip(),
                    status=str(snap.get("status", "")).strip(),
                    stage=str(snap.get("stage", "")).strip(),
                    tf_phase=str(snap.get("tf_phase", "")).strip(),
                    preset=preset,
                    phase2_shape=_compose_phase2_shape(snap.get("phase2_execution_roles") or [], snap.get("phase2_review_roles") or []),
                    lane_summary=_compose_lane_summary(snap),
                    backend_summary=_compose_backend_summary(
                        str(snap.get("backend", "")).strip(),
                        str(snap.get("backend_profile", "")).strip(),
                        str(snap.get("backend_verdict", "")).strip(),
                        str(snap.get("backend_contract", "")).strip(),
                    ),
                    updated_at=str(snap.get("updated_at", "")).strip(),
                    detail_path=_detail_path(str(request_id or "").strip()),
                )
            )
    rows.sort(key=lambda row: (row.updated_at, row.project_alias, row.request_id), reverse=True)
    return rows[: max(1, int(cap))]


def _build_runtime_recent_task_rows(
    entry: Dict[str, Any],
    *,
    project_key: str,
    project_alias: str,
    project_label: str,
    cap: int = 8,
) -> List[ActiveTaskRowDTO]:
    rows: List[ActiveTaskRowDTO] = []
    tasks = task_state.ensure_project_tasks(entry)
    for request_id, task in tasks.items():
        if not isinstance(task, dict):
            continue
        status = runtime_read.normalize_task_status(task.get("status", "pending"))
        if status == "canceled":
            continue
        snap = task_state.task_monitor_row_snapshot(
            task,
            str(request_id or "").strip(),
            normalize_task_status=runtime_read.normalize_task_status,
            task_display_label=task_view.task_display_label,
        )
        preset = str(snap.get("phase2_team_preset", "")).strip() or str(snap.get("phase1_role_preset", "")).strip() or "-"
        rows.append(
            ActiveTaskRowDTO(
                project_key=project_key,
                project_alias=project_alias,
                project_label=project_label,
                runtime_path=_runtime_path(project_alias),
                request_id=str(snap.get("request_id", "")).strip(),
                label=str(snap.get("label", "")).strip(),
                status=str(snap.get("status", "")).strip(),
                stage=str(snap.get("stage", "")).strip(),
                tf_phase=str(snap.get("tf_phase", "")).strip(),
                preset=preset,
                phase2_shape=_compose_phase2_shape(snap.get("phase2_execution_roles") or [], snap.get("phase2_review_roles") or []),
                lane_summary=_compose_lane_summary(snap),
                backend_summary=_compose_backend_summary(
                    str(snap.get("backend", "")).strip(),
                    str(snap.get("backend_profile", "")).strip(),
                    str(snap.get("backend_verdict", "")).strip(),
                    str(snap.get("backend_contract", "")).strip(),
                ),
                updated_at=str(snap.get("updated_at", "")).strip(),
                detail_path=_detail_path(str(request_id or "").strip()),
            )
        )
    rows.sort(key=lambda row: (row.updated_at, row.request_id), reverse=True)
    return rows[: max(1, int(cap))]


def _build_task_detail(manager_state: Dict[str, Any], request_id: str, *, root_team_dir: Optional[Path] = None) -> Optional[TaskDetailDTO]:
    projects = manager_state.get("projects") if isinstance(manager_state.get("projects"), dict) else {}
    target = str(request_id or "").strip()
    if not target:
        return None
    for key, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        task = task_state.get_task_record(entry, target)
        if not isinstance(task, dict):
            continue
        rid = task_state.resolve_task_request_id(entry, target)
        alias = str(entry.get("project_alias", "")).strip().upper() or str(key)
        display = str(entry.get("display_name", "")).strip() or str(key)
        shape = task_state.task_phase2_shape_snapshot(task)
        lane = task_state.task_lane_summary_snapshot(task)
        observatory = team_observatory.task_team_observatory_snapshot(task)
        result = task.get("result") if isinstance(task.get("result"), dict) else {}
        contract = _completion_contract_for_preset(str(task.get("phase2_team_preset", "")).strip() or str(task.get("phase1_role_preset", "")).strip())
        rerun_summary = _task_rerun_summary(task)
        followup_summary = _task_followup_summary(task)
        followup_brief_execution_lanes = ", ".join(
            str(item).strip() for item in (task.get("followup_brief_execution_lane_ids") or []) if str(item).strip()
        ) or "-"
        followup_brief_review_lanes = ", ".join(
            str(item).strip() for item in (task.get("followup_brief_review_lane_ids") or []) if str(item).strip()
        ) or "-"
        rate_limit_summary = _task_rate_limit_summary(task)
        run_lock_mode = run_lock.project_run_lock_mode(entry)
        run_lock_note = run_lock.project_run_lock_note(entry) or "-"
        background_slot_limit = 1
        background_slot_active = 0
        background_slot_pressure = "not_applicable | -"
        pack_profile = "-"
        pack_summary = "-"
        pack_docs = "-"
        pack_excluded = "-"
        judge_binding_summary = "-"
        judge_probe_summary = "-"
        latest_replan_auto_routing_policy: Dict[str, Any] = {}
        latest_replan_auto_route_summary = "-"
        latest_replan_auto_route_status_summary = "-"
        latest_replan_auto_operator_summary = "-"
        team_dir_raw = str(entry.get("team_dir", "")).strip()
        if team_dir_raw:
            team_dir = Path(team_dir_raw)
            slot_snapshot = _background_slot_snapshot(entry, task, Path(team_dir_raw))
            background_slot_limit = int(slot_snapshot.get("selected_limit", 1) or 1)
            background_slot_active = int(slot_snapshot.get("selected_active", 0) or 0)
            background_slot_pressure = (
                (str(slot_snapshot.get("selected_pressure", "")).strip() or "not_applicable")
                + " | "
                + (str(slot_snapshot.get("summary", "")).strip() or "-")
            )
            pack = context_pack.load_context_pack(
                team_dir,
                entry=entry,
                task=task,
                project_root=entry.get("project_root"),
            )
            pack_profile = str(pack.get("profile", "")).strip() or "-"
            pack_summary = str(pack.get("summary", "")).strip() or "-"
            pack_docs = str(pack.get("docs_summary", "")).strip() or "-"
            pack_excluded = str(pack.get("excluded_summary", "")).strip() or "-"
            judge_binding = model_endpoint_adapter.resolve_task_judge_binding(
                team_dir,
                entry=entry,
                task=task,
            )
            judge_binding_summary = str(judge_binding.get("summary", "")).strip() or "-"
            endpoint = judge_binding.get("endpoint") if isinstance(judge_binding.get("endpoint"), dict) else {}
            provider_kind = str(endpoint.get("provider_kind", "")).strip().lower()
            if not judge_binding.get("bound"):
                judge_probe_summary = "status=unbound"
            elif provider_kind != "ollama":
                judge_probe_summary = (
                    f"endpoint={str(endpoint.get('endpoint_id', '')).strip() or '-'} "
                    f"provider={provider_kind or '-'} status=unsupported_probe"
                )
            else:
                judge_probe_summary = (
                    f"endpoint={str(endpoint.get('endpoint_id', '')).strip() or '-'} "
                    f"provider=ollama status=deferred_live_probe"
                )
            judge_probe_summary = action_audit.prefer_recent_model_ping_probe_summary(
                team_dir,
                project_alias=str(entry.get("project_alias", "")).strip(),
                kind="judge",
                endpoint_id=str(endpoint.get("endpoint_id", "")).strip(),
                probe_status="unsupported_probe" if judge_binding.get("bound") and provider_kind != "ollama" else ("deferred_live_probe" if judge_binding.get("bound") else "unbound"),
                probe_summary=judge_probe_summary,
            )
        if isinstance(root_team_dir, Path):
            latest_replan_auto_routing_policy = action_audit.load_latest_replan_auto_routing_policy_for_runtime(
                root_team_dir,
                project_alias=alias,
            )
            latest_replan_auto_route_status_summary = action_audit.load_latest_replan_auto_route_status_summary_for_runtime(
                root_team_dir,
                project_alias=alias,
            )
            latest_replan_auto_operator_summary = action_audit.load_latest_replan_auto_operator_summary_for_runtime(
                root_team_dir,
                project_alias=alias,
            )
            latest_replan_auto_route = action_audit.load_latest_action_audit_for_runtime_kind(
                root_team_dir,
                project_alias=alias,
                outcome_kind="replan_auto_route",
            )
            if latest_replan_auto_route:
                latest_replan_auto_route_summary = "{headline} | next={next_step} | {detail}".format(
                    headline=str(latest_replan_auto_route.get("headline", "")).strip() or "Replan Auto Route",
                    next_step=str(latest_replan_auto_route.get("next_step", "")).strip() or "-",
                    detail=str(latest_replan_auto_route.get("outcome_detail", "")).strip() or "-",
                )
        action_contract = _task_command_contract(
            project_alias=alias,
            label=task_view.task_display_label(task, fallback_request_id=rid),
            request_id=rid,
            tf_phase=str(task.get("tf_phase", "")).strip() or task_view.normalize_tf_phase(task_view.derive_tf_phase(task), "queued"),
            rerun_summary=rerun_summary,
            followup_summary=followup_summary,
            followup_brief_status=str(task.get("followup_brief_status", "")).strip(),
            rate_limit_summary=rate_limit_summary,
            execution_brief_status=str(task.get("execution_brief_status", "")).strip(),
        )
        safe_action_buttons, phase2_action_buttons = _task_action_buttons(
            label=task_view.task_display_label(task, fallback_request_id=rid),
            request_id=rid,
            phase2_commands=list(action_contract.get("phase2") or []),
        )
        phase2_action_buttons = _append_unique_action_button(
            phase2_action_buttons,
            _replan_auto_route_action_button(
                label=task_view.task_display_label(task, fallback_request_id=rid),
                request_id=rid,
                policy=latest_replan_auto_routing_policy,
            ),
        )
        manual_route_button = _replan_manual_route_action_button(
            project_alias=alias,
            label=task_view.task_display_label(task, fallback_request_id=rid),
            request_id=rid,
            policy=latest_replan_auto_routing_policy,
        )
        if manual_route_button is not None and str(manual_route_button.mode).strip() == "phase2":
            phase2_action_buttons = _append_unique_action_button(phase2_action_buttons, manual_route_button)
        else:
            safe_action_buttons = _append_unique_action_button(safe_action_buttons, manual_route_button)
        safe_action_buttons = _append_unique_action_button(
            safe_action_buttons,
            _worker_apply_preview_button(
                label=task_view.task_display_label(task, fallback_request_id=rid),
                request_id=rid,
                update_stub={
                    "status": task.get("background_run_worker_update_stub_status"),
                    "summary_line": task.get("background_run_worker_update_stub_summary"),
                    "target_artifacts": task.get("background_run_worker_update_stub_targets"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=task.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        safe_action_buttons = _append_unique_action_button(
            safe_action_buttons,
            _worker_update_preview_button(
                label=task_view.task_display_label(task, fallback_request_id=rid),
                request_id=rid,
                update_stub={
                    "status": task.get("background_run_worker_update_stub_status"),
                    "summary_line": task.get("background_run_worker_update_stub_summary"),
                    "target_artifacts": task.get("background_run_worker_update_stub_targets"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=task.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        phase2_action_buttons = _append_unique_action_button(
            phase2_action_buttons,
            _worker_apply_proposal_button(
                label=task_view.task_display_label(task, fallback_request_id=rid),
                request_id=rid,
                update_stub={
                    "status": task.get("background_run_worker_update_stub_status"),
                    "summary_line": task.get("background_run_worker_update_stub_summary"),
                    "target_artifacts": task.get("background_run_worker_update_stub_targets"),
                    "actions": task.get("background_run_worker_result_actions"),
                    "cautions": task.get("background_run_worker_result_cautions"),
                    "evidence_refs": task.get("background_run_worker_result_evidence_refs"),
                },
                proposal_ids=task.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        phase2_action_buttons = _append_unique_action_button(
            phase2_action_buttons,
            _worker_update_proposal_accept_button(
                project_alias=alias,
                proposal_ids=task.get("background_run_worker_update_proposal_ids") or [],
            ),
        )
        phase2_action_buttons = _append_unique_action_button(
            phase2_action_buttons,
            _worker_apply_proposal_accept_button(
                project_alias=alias,
                proposal_ids=task.get("background_run_worker_update_proposal_ids") or [],
                proposal_summary=task.get("background_run_worker_update_proposal_summary"),
            ),
        )
        backend_summary = _compose_backend_summary(
            str(task.get("backend", "") or result.get("backend", "")).strip(),
            str(task.get("backend_profile", "") or result.get("backend_profile", "")).strip(),
            str(task.get("backend_verdict", "") or result.get("backend_verdict", "")).strip(),
            str(task.get("backend_contract", "") or result.get("backend_contract", "")).strip(),
        )
        return TaskDetailDTO(
            project_key=str(key),
            project_alias=alias,
            project_label=display,
            request_id=rid,
            label=task_view.task_display_label(task, fallback_request_id=rid),
            status=runtime_read.normalize_task_status(task.get("status", "pending")),
            tf_phase=str(task.get("tf_phase", "")).strip() or task_view.normalize_tf_phase(task_view.derive_tf_phase(task), "queued"),
            mode=str(task.get("mode", "dispatch")).strip(),
            prompt=str(task.get("prompt", "")).strip(),
            roles=task_view.dedupe_roles(task.get("roles") or []),
            verifier_roles=task_view.dedupe_roles(task.get("verifier_roles") or []),
            phase1_summary=_task_phase1_summary(task),
            phase1_progress=_task_phase1_progress(task),
            phase1_candidate_roles=task_view.dedupe_roles(task.get("phase1_candidate_roles") or []),
            phase1_role_preset=str(task.get("phase1_role_preset", "")).strip(),
            phase2_team_preset=str(task.get("phase2_team_preset", "")).strip(),
            phase2_shape=_compose_phase2_shape(shape["execution_roles"], shape["review_roles"]),
            phase2_quality=_compose_task_quality(task),
            lane_summary=_compose_lane_summary(lane),
            rerun_summary=rerun_summary,
            followup_summary=followup_summary,
            followup_brief_status=str(task.get("followup_brief_status", "")).strip() or "-",
            followup_brief_summary=str(task.get("followup_brief_summary", "")).strip() or "-",
            followup_brief_execution_lanes=followup_brief_execution_lanes,
            followup_brief_review_lanes=followup_brief_review_lanes,
            followup_brief_reason=str(task.get("followup_brief_reason", "")).strip() or "-",
            context_pack_profile=pack_profile,
            context_pack_summary=pack_summary,
            context_pack_docs=pack_docs,
            context_pack_excluded=pack_excluded,
            judge_binding_summary=judge_binding_summary,
            judge_probe_summary=judge_probe_summary,
            reentry_rails_summary=str(task.get("reentry_rails_summary", "")).strip() or "-",
            run_lock_mode=run_lock_mode,
            run_lock_note=run_lock_note,
            background_slot_limit=background_slot_limit,
            background_slot_active=background_slot_active,
            background_slot_pressure=background_slot_pressure,
            completion_focus=str(contract.get("focus", "")).strip() or "-",
            completion_done_when=str(contract.get("done_when", "")).strip() or "-",
            completion_rerun_when=str(contract.get("rerun_when", "")).strip() or "-",
            completion_followup_when=str(contract.get("manual_followup_when", "")).strip() or "-",
            execution_brief_status=str(task.get("execution_brief_status", "")).strip() or "-",
            execution_brief_summary=str(task.get("execution_brief_summary", "")).strip() or "-",
            execution_brief_executable_slice=", ".join(
                str(item).strip() for item in (task.get("execution_brief_executable_slice") or []) if str(item).strip()
            )
            or "-",
            execution_brief_blocked_slice=", ".join(
                str(item).strip() for item in (task.get("execution_brief_blocked_slice") or []) if str(item).strip()
            )
            or "-",
            execution_brief_operator_decision=str(task.get("execution_brief_operator_decision", "")).strip() or "-",
            background_run_status=str(task.get("background_run_status", "")).strip() or "-",
            background_run_runner_target=str(task.get("background_run_runner_target", "")).strip() or "-",
            background_run_ticket_id=str(task.get("background_run_ticket_id", "")).strip() or "-",
            background_run_launch_mode=str(task.get("background_run_launch_mode", "")).strip() or "-",
            background_run_runtime_handle=str(task.get("background_run_runtime_handle", "")).strip() or "-",
            background_run_runtime_summary=str(task.get("background_run_runtime_summary", "")).strip() or "-",
            background_run_external_phase=str(task.get("background_run_external_phase", "")).strip() or "-",
            background_run_external_note=str(task.get("background_run_external_note", "")).strip() or "-",
            background_run_evidence_bundle=str(task.get("background_run_evidence_bundle", "")).strip() or "-",
            background_run_evidence_artifacts=", ".join(
                str(item).strip() for item in (task.get("background_run_evidence_artifacts") or []) if str(item).strip()
            )
            or "-",
            background_run_launch_spec_summary=(
                str(task.get("background_run_launch_spec_summary", "")).strip() or "-"
            ),
            background_run_task_contract_summary=(
                str(task.get("background_run_task_contract_summary", "")).strip() or "-"
            ),
            background_run_worker_result_summary=(
                str(task.get("background_run_worker_result_summary", "")).strip() or "-"
            ),
            background_run_worker_result_actions=", ".join(
                str(item).strip()
                for item in (
                    (task.get("background_run_worker_result_actions") if isinstance(task.get("background_run_worker_result_actions"), list) else [])
                    or []
                )
                if str(item).strip()
            )
            or "-",
            background_run_worker_result_cautions=", ".join(
                str(item).strip()
                for item in (
                    (task.get("background_run_worker_result_cautions") if isinstance(task.get("background_run_worker_result_cautions"), list) else [])
                    or []
                )
                if str(item).strip()
            )
            or "-",
            background_run_worker_result_evidence_refs=", ".join(
                str(item).strip()
                for item in (
                    (task.get("background_run_worker_result_evidence_refs") if isinstance(task.get("background_run_worker_result_evidence_refs"), list) else [])
                    or []
                )
                if str(item).strip()
            )
            or "-",
            background_run_worker_update_stub_status=(
                str(task.get("background_run_worker_update_stub_status", "")).strip() or "-"
            ),
            background_run_worker_update_stub_summary=(
                str(task.get("background_run_worker_update_stub_summary", "")).strip() or "-"
            ),
            background_run_worker_update_stub_targets=", ".join(
                str(item).strip()
                for item in (
                    (task.get("background_run_worker_update_stub_targets") if isinstance(task.get("background_run_worker_update_stub_targets"), list) else [])
                    or []
                )
                if str(item).strip()
            )
            or "-",
            background_run_worker_update_proposal_summary=(
                str(task.get("background_run_worker_update_proposal_summary", "")).strip() or "-"
            ),
            background_run_worker_update_proposal_ids=[
                str(item).strip()
                for item in (task.get("background_run_worker_update_proposal_ids") or [])
                if str(item).strip()
            ],
            background_run_worker_update_operator_summary=_worker_update_operator_summary(task),
            background_run_model_plan_summary=(
                str(task.get("background_run_model_plan_summary", "")).strip() or "-"
            ),
            background_run_model_judge_binding_summary=(
                str(task.get("background_run_model_judge_binding_summary", "")).strip() or "-"
            ),
            background_run_model_judge_probe_summary=(
                str(task.get("background_run_model_judge_probe_summary", "")).strip() or "-"
            ),
            background_run_model_escalation_binding_summary=(
                str(task.get("background_run_model_escalation_binding_summary", "")).strip() or "-"
            ),
            background_run_model_escalation_probe_summary=(
                str(task.get("background_run_model_escalation_probe_summary", "")).strip() or "-"
            ),
            latest_replan_auto_route_summary=latest_replan_auto_route_summary,
            latest_replan_auto_route_status_summary=latest_replan_auto_route_status_summary,
            latest_replan_auto_operator_summary=latest_replan_auto_operator_summary,
            backend_summary=backend_summary,
            backend_note=str(task.get("backend_contract_note", "") or result.get("backend_contract_note", "")).strip(),
            rate_limit_summary=rate_limit_summary,
            observatory_headline=str(observatory.get("headline", "")).strip() or "-",
            observatory_first_focus=str(observatory.get("first_focus", "")).strip() or "-",
            observatory_freshness_scope=str(observatory.get("freshness_scope", "")).strip() or "-",
            observatory_stale_lane_count=int(observatory.get("stale_lane_count", 0) or 0),
            observatory_bottleneck_lane=str(observatory.get("bottleneck_lane_id", "")).strip() or "-",
            observatory_bottleneck_reason=str(observatory.get("bottleneck_reason", "")).strip() or "-",
            observatory_conflict_file_count=int(observatory.get("conflict_file_count", 0) or 0),
            observatory_touched_file_count=int(observatory.get("touched_file_count", 0) or 0),
            observatory_lanes=_observatory_lane_rows(observatory),
            updated_at=str(task.get("updated_at", "")).strip() or str(task.get("created_at", "")).strip(),
            command_hints=list(action_contract.get("safe") or []),
            phase2_action_hints=list(action_contract.get("phase2") or []),
            safe_action_buttons=safe_action_buttons,
            phase2_action_buttons=phase2_action_buttons,
            reference_lines=task_view.summarize_task_lifecycle(display, task).splitlines(),
        )
    return None
