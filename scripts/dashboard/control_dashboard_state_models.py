#!/usr/bin/env python3
"""Dashboard state DTO models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from control_dashboard_state_io import ActionAuditRowDTO, FileFreshnessDTO


@dataclass(frozen=True)
class ActionButtonDTO:
    label: str
    command: str
    method: str
    path: str
    mode: str
    note: str
    payload_json: str


@dataclass(frozen=True)
class LaneObservatoryDTO:
    lane_id: str
    phase: str
    role: str
    status: str
    age_text: str
    idle_text: str
    note: str
    freshness_scope: str
    last_event_kind: str = ""
    backend: str = ""
    tool_count: int = 0
    touched_file_count: int = 0
    touched_file_summary: str = ""
    conflict_file_count: int = 0
    conflict_summary: str = ""
    is_stale: bool = False


@dataclass(frozen=True)
class ControlSummaryDTO:
    auto_mode: str
    offdesk_mode: str
    state_root_mode: str
    state_root_path: str
    provider_capacity_summary: str
    next_retry_at: str
    next_retry_target: str
    repeat_memory_summary: str
    execution_brief_summary: str
    background_run_summary: str
    background_worker_summary: str
    latest_intent_command: str
    latest_intent_action: str
    latest_intent_trace: str
    latest_intent_focus: str
    active_runtime_count: int
    attention_runtime_count: int
    snapshot_taken_at: str


@dataclass(frozen=True)
class RuntimeCardDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    severity_score: int
    provider_pressure_score: int
    provider_repeat_count: int
    active_task_request_id: str
    active_task_label: str
    active_task_phase: str
    active_task_status: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_backend: str
    active_task_execution_brief_status: str
    active_task_execution_brief_summary: str
    active_task_execution_brief_executable_slice: str
    active_task_execution_brief_blocked_slice: str
    active_task_execution_brief_operator_decision: str
    active_task_followup_brief_status: str
    active_task_followup_brief_summary: str
    active_task_followup_brief_execution_lanes: str
    active_task_followup_brief_review_lanes: str
    active_task_followup_brief_reason: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_runtime_handle: str
    active_task_background_run_runtime_summary: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_launch_spec_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_queue_depth: int
    background_queue_stale_count: int
    runtime_safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    runtime_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActiveTaskRowDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    request_id: str
    label: str
    status: str
    stage: str
    tf_phase: str
    preset: str
    phase2_shape: str
    lane_summary: str
    backend_summary: str
    updated_at: str
    detail_path: str


@dataclass(frozen=True)
class TaskDetailDTO:
    project_key: str
    project_alias: str
    project_label: str
    request_id: str
    label: str
    status: str
    tf_phase: str
    mode: str
    prompt: str
    roles: List[str] = field(default_factory=list)
    verifier_roles: List[str] = field(default_factory=list)
    phase1_summary: str = ""
    phase1_progress: str = ""
    phase1_candidate_roles: List[str] = field(default_factory=list)
    phase1_role_preset: str = ""
    phase2_team_preset: str = ""
    phase2_shape: str = ""
    phase2_quality: str = ""
    lane_summary: str = ""
    rerun_summary: str = ""
    followup_summary: str = ""
    followup_brief_status: str = ""
    followup_brief_summary: str = ""
    followup_brief_execution_lanes: str = ""
    followup_brief_review_lanes: str = ""
    followup_brief_reason: str = ""
    reentry_rails_summary: str = ""
    run_lock_mode: str = ""
    run_lock_note: str = ""
    background_slot_limit: int = 1
    background_slot_active: int = 0
    background_slot_pressure: str = ""
    completion_focus: str = ""
    completion_done_when: str = ""
    completion_rerun_when: str = ""
    completion_followup_when: str = ""
    execution_brief_status: str = ""
    execution_brief_summary: str = ""
    execution_brief_executable_slice: str = ""
    execution_brief_blocked_slice: str = ""
    execution_brief_operator_decision: str = ""
    background_run_status: str = ""
    background_run_runner_target: str = ""
    background_run_ticket_id: str = ""
    background_run_launch_mode: str = ""
    background_run_runtime_handle: str = ""
    background_run_runtime_summary: str = ""
    background_run_evidence_bundle: str = ""
    background_run_evidence_artifacts: str = ""
    background_run_launch_spec_summary: str = ""
    backend_summary: str = ""
    backend_note: str = ""
    rate_limit_summary: str = ""
    observatory_headline: str = ""
    observatory_first_focus: str = ""
    observatory_freshness_scope: str = ""
    observatory_stale_lane_count: int = 0
    observatory_bottleneck_lane: str = ""
    observatory_bottleneck_reason: str = ""
    observatory_conflict_file_count: int = 0
    observatory_touched_file_count: int = 0
    observatory_lanes: List[LaneObservatoryDTO] = field(default_factory=list)
    updated_at: str = ""
    command_hints: List[str] = field(default_factory=list)
    phase2_action_hints: List[str] = field(default_factory=list)
    safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    reference_lines: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeDetailDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    completed_task_count: int
    blocked_task_count: int
    parked_task_count: int
    queue_summary: str
    proposal_summary: str
    sync_summary: str
    provider_pressure_summary: str
    repeat_summary: str
    active_task_request_id: str
    active_task_label: str
    active_task_path: str
    active_task_phase: str
    active_task_status: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_execution_brief_status: str
    active_task_execution_brief_summary: str
    active_task_execution_brief_executable_slice: str
    active_task_execution_brief_blocked_slice: str
    active_task_execution_brief_operator_decision: str
    active_task_followup_brief_status: str
    active_task_followup_brief_summary: str
    active_task_followup_brief_execution_lanes: str
    active_task_followup_brief_review_lanes: str
    active_task_followup_brief_reason: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_launch_mode: str
    active_task_background_run_runtime_handle: str
    active_task_background_run_runtime_summary: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_launch_spec_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_queue_depth: int
    background_queue_stale_count: int
    active_task_completion_focus: str
    active_task_completion_done: str
    active_task_completion_rerun: str
    active_task_completion_followup: str
    active_task_backend: str
    active_task_backend_note: str
    active_task_rate_limit: str
    runtime_command_hints: List[str] = field(default_factory=list)
    runtime_phase2_action_hints: List[str] = field(default_factory=list)
    active_task_command_hints: List[str] = field(default_factory=list)
    active_task_phase2_action_hints: List[str] = field(default_factory=list)
    runtime_safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    runtime_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    active_task_safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    active_task_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    lines: List[str] = field(default_factory=list)
    recent_tasks: List[ActiveTaskRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveryTaskDTO:
    request_id: str
    label: str
    detail_path: str
    status: str
    tf_phase: str
    preset: str
    phase2_shape: str
    phase2_quality: str
    lane_summary: str
    rerun_summary: str
    followup_summary: str
    completion_focus: str
    completion_done_when: str
    completion_rerun_when: str
    completion_followup_when: str
    backend_summary: str
    backend_note: str
    rate_limit_summary: str
    observatory_headline: str = ""
    observatory_first_focus: str = ""
    observatory_stale_lane_count: int = 0
    observatory_bottleneck_lane: str = ""
    observatory_bottleneck_reason: str = ""
    observatory_conflict_file_count: int = 0
    observatory_touched_file_count: int = 0
    command_hints: List[str] = field(default_factory=list)
    phase2_action_hints: List[str] = field(default_factory=list)
    safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)


@dataclass(frozen=True)
class RecoveryRuntimeDTO:
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    status: str
    readiness: str
    attention_summary: str
    priority_action: str
    priority_reason: str
    next_focus: str
    queue_summary: str
    proposal_summary: str
    sync_summary: str
    provider_pressure_summary: str
    repeat_summary: str
    completed_task_count: int
    blocked_task_count: int
    parked_task_count: int
    active_task_label: str
    active_task_path: str
    active_task_status: str
    active_task_phase: str
    active_task_preset: str
    active_task_phase2_shape: str
    active_task_phase2_quality: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_launch_spec_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_queue_depth: int
    background_queue_stale_count: int
    active_task_completion_focus: str
    active_task_completion_done: str
    active_task_completion_rerun: str
    active_task_completion_followup: str
    active_task_backend: str
    active_task_backend_note: str
    active_task_rate_limit: str
    runtime_command_hints: List[str] = field(default_factory=list)
    runtime_phase2_action_hints: List[str] = field(default_factory=list)
    active_task_command_hints: List[str] = field(default_factory=list)
    active_task_phase2_action_hints: List[str] = field(default_factory=list)
    runtime_safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    runtime_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    active_task_safe_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    active_task_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    task_teams: List[RecoveryTaskDTO] = field(default_factory=list)


@dataclass(frozen=True)
class RecoverySummaryDTO:
    exists: bool
    artifact_path: str
    updated_at: str
    stale: bool
    error: str
    generated_at: str
    snapshot_taken_at: str
    automation_posture: str
    auto_mode: str
    offdesk_mode: str
    provider_capacity_summary: str
    next_retry_at: str
    next_retry_target: str
    repeat_memory_summary: str
    execution_brief_summary: str
    background_run_summary: str
    background_worker_summary: str
    latest_intent_command: str
    latest_intent_action: str
    latest_intent_trace: str
    latest_intent_focus: str
    control_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    runtimes: List[RecoveryRuntimeDTO] = field(default_factory=list)


@dataclass(frozen=True)
class ActionAuditPageDTO:
    exists: bool
    audit_path: str
    updated_at: str
    stale: bool
    error: str
    total_rows: int
    status_summary: str
    rows: List[ActionAuditRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class HistorySearchRowDTO:
    at: str
    scope: str
    source: str
    project_alias: str
    project_key: str
    request_id: str
    task_short_id: str
    task_title: str
    action: str
    intent_action: str
    reason_code: str
    phase: str
    status: str
    summary: str
    detail: str
    followup_hint: str
    raw_ref: str


@dataclass(frozen=True)
class HistorySearchPageDTO:
    query: str
    project_filter: str
    since_label: str
    scope: str
    limit: int
    total_rows: int
    rows: List[HistorySearchRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class DashboardSnapshotDTO:
    control_root: str
    team_dir: str
    manager_state_file: str
    snapshot_taken_at: str
    source_files: List[FileFreshnessDTO]
    control_summary: ControlSummaryDTO
    runtime_cards: List[RuntimeCardDTO]
    attention_runtime_cards: List[RuntimeCardDTO]
    active_task_rows: List[ActiveTaskRowDTO]
    recent_action_audit_rows: List[ActionAuditRowDTO]


@dataclass(frozen=True)
class DashboardSnapshotLoadResult:
    snapshot: DashboardSnapshotDTO
    manager_state: Dict[str, Any]
    provider_state: Dict[str, Any]
