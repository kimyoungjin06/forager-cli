#!/usr/bin/env python3
"""Dashboard state DTO models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from control_dashboard_state_io import ActionAuditRowDTO, FileFreshnessDTO


class _PlanningCompactSummaryCompatMixin:
    @property
    def planning_review_summary(self) -> str:
        # Legacy compatibility alias for older state consumers.
        return self.planning_compact_summary


class _LatestPlanningCompactSummaryCompatMixin:
    @property
    def latest_planning_review_summary(self) -> str:
        # Legacy compatibility alias for older state consumers.
        return self.latest_planning_compact_summary


class _ActiveTaskPlanningCompactSummaryCompatMixin:
    @property
    def active_task_planning_review_summary(self) -> str:
        # Legacy compatibility alias for older state consumers.
        return self.active_task_planning_compact_summary


class _SelectedTaskPlanningCompactSummaryCompatMixin:
    @property
    def selected_task_planning_review_summary(self) -> str:
        # Legacy compatibility alias for older state consumers.
        return self.selected_task_planning_compact_summary


@dataclass(frozen=True)
class ServerGuardDTO:
    status: str
    summary: str
    reason_summary: str
    note: str
    next_step: str
    disk_summary: str
    memory_summary: str
    load_summary: str
    process_summary: str
    queue_summary: str
    focus_label: str = ""
    action_copy: str = ""
    priority_link_label: str = ""
    priority_link_note: str = ""
    snapshot_path: str = ""
    snapshot_updated_at: str = ""
    recommended_actions: List["ServerGuardActionDTO"] = field(default_factory=list)


@dataclass(frozen=True)
class ServerGuardActionDTO:
    label: str
    href: str = ""
    note: str = ""
    method: str = "GET"
    path: str = ""
    mode: str = "safe"
    payload_json: str = "{}"
    command: str = ""


@dataclass(frozen=True)
class ServerGuardActionGroupDTO:
    key: str
    label: str
    note: str = ""
    operator_sentence: str = ""
    action_sentence: str = ""
    focus_preset_label: str = ""
    priority_link_label: str = ""
    priority_link_note: str = ""
    actions: List["ServerGuardActionDTO"] = field(default_factory=list)


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
    server_guard: ServerGuardDTO
    server_guard_latest_action_summary: str
    server_guard_latest_action_path: str
    server_guard_latest_result_summary: str
    server_guard_latest_result_path: str
    server_guard_preview_actions: List["ServerGuardActionDTO"]
    server_guard_preview_groups: List["ServerGuardActionGroupDTO"]
    server_guard_threads: List["ServerGuardThreadDTO"]
    active_runtime_count: int
    attention_runtime_count: int
    snapshot_taken_at: str


@dataclass(frozen=True)
class RuntimeCardDTO(_LatestPlanningCompactSummaryCompatMixin):
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
    chat_console_path: str
    chat_console_label: str
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
    active_task_context_pack_summary: str
    active_task_model_plan_summary: str
    active_task_planning_lanes_summary: str
    active_task_approved_plan_gate_summary: str
    active_task_approved_plan_summary: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_runtime_handle: str
    active_task_background_run_runtime_summary: str
    active_task_background_run_external_phase: str
    active_task_background_run_external_note: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_launch_spec_summary: str
    active_task_background_run_worker_update_operator_summary: str
    active_task_background_run_operator_preference_artifact_kind: str
    active_task_background_run_operator_preference_preflight_summary: str
    active_task_background_run_operator_preference_applied_summary: str
    active_task_background_run_operator_preference_candidate_summary: str
    active_task_background_run_operator_preference_confirm_summary: str
    active_task_background_run_operator_preference_manual_summary: str
    active_task_background_run_operator_preference_disabled_summary: str
    active_task_background_run_operator_preference_decision_summary: str
    active_task_background_run_worker_update_proposal_summary: str
    active_task_background_run_worker_apply_accept_summary: str
    active_task_background_run_worker_syncback_summary: str
    active_task_background_run_model_plan_summary: str
    workspace_summary: str
    document_registry_summary: str
    model_routing_summary: str
    model_registry_summary: str
    latest_judge_summary: str
    latest_judge_decision_summary: str
    latest_judge_decision_bridge_summary: str
    latest_replan_auto_decision_summary: str
    latest_replan_auto_routing_policy_summary: str
    latest_replan_auto_route_summary: str
    latest_replan_auto_route_status_summary: str
    latest_replan_auto_operator_summary: str
    latest_planning_handoff_summary: str
    latest_planning_compact_summary: str
    latest_manual_step_summary: str
    latest_canonical_writeback_summary: str
    latest_canonical_mutation_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_scheduler_summary: str
    background_scheduler_note: str
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
class TaskDetailDTO(_PlanningCompactSummaryCompatMixin):
    project_key: str
    project_alias: str
    project_label: str
    chat_console_path: str
    chat_console_label: str
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
    planning_compact_summary: str = ""
    planning_lanes_summary: str = ""
    approved_plan_gate_summary: str = ""
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
    context_pack_profile: str = ""
    context_pack_summary: str = ""
    context_pack_docs: str = ""
    context_pack_excluded: str = ""
    general_subagent_summary: str = ""
    general_subagent_artifact_summary: str = ""
    general_subagent_artifact_path: str = ""
    judge_binding_summary: str = ""
    judge_probe_summary: str = ""
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
    job_contract_summary: str = ""
    job_contract_goal: str = ""
    job_contract_scope: str = ""
    job_contract_non_goals: str = ""
    job_contract_acceptance_checks: str = ""
    job_contract_artifacts_to_touch: str = ""
    job_contract_rollback_hint: str = ""
    planner_lane_summary: str = ""
    critic_lane_summary: str = ""
    critic_review_summary: str = ""
    critic_review_blocking_issues: str = ""
    critic_review_required_fixes: str = ""
    approved_plan_summary: str = ""
    approved_plan_artifact_rows: str = ""
    debug_packet_summary: str = ""
    debug_packet_symptom: str = ""
    debug_packet_root_cause: str = ""
    debug_packet_evidence: str = ""
    debug_packet_failed_attempt: str = ""
    debug_packet_next_step: str = ""
    phase_checkpoint_summary: str = ""
    phase_checkpoint_current_phase: str = ""
    phase_checkpoint_rows: str = ""
    background_run_status: str = ""
    background_run_runner_target: str = ""
    background_run_ticket_id: str = ""
    background_run_launch_mode: str = ""
    background_run_runtime_handle: str = ""
    background_run_runtime_summary: str = ""
    background_run_external_phase: str = ""
    background_run_external_note: str = ""
    background_run_evidence_bundle: str = ""
    background_run_evidence_artifacts: str = ""
    background_run_launch_spec_summary: str = ""
    background_run_task_contract_summary: str = ""
    background_run_task_contract_module_summary: str = ""
    background_run_task_contract_policy_summary: str = ""
    background_run_worker_gate_summary: str = ""
    background_run_worker_profile_summary: str = ""
    background_run_worker_checklist_summary: str = ""
    background_run_worker_items_summary: str = ""
    background_run_worker_items: str = ""
    background_run_worker_item_classes_summary: str = ""
    background_run_worker_item_classes: str = ""
    background_run_worker_records_summary: str = ""
    background_run_worker_records: str = ""
    background_run_worker_record_rows_summary: str = ""
    background_run_worker_record_rows: str = ""
    background_run_worker_record_set_summary: str = ""
    background_run_worker_record_set: str = ""
    background_run_worker_preflight_summary: str = ""
    background_run_worker_preflight_rows_summary: str = ""
    background_run_worker_preflight_rows: str = ""
    background_run_worker_result_summary: str = ""
    background_run_worker_result_actions: str = ""
    background_run_worker_result_cautions: str = ""
    background_run_worker_result_evidence_refs: str = ""
    background_run_worker_update_stub_status: str = ""
    background_run_worker_update_stub_summary: str = ""
    background_run_worker_update_stub_targets: str = ""
    background_run_worker_update_proposal_summary: str = ""
    background_run_worker_update_proposal_ids: List[str] = field(default_factory=list)
    background_run_worker_update_operator_summary: str = ""
    background_run_operator_preference_artifact_kind: str = ""
    background_run_operator_preference_preflight_summary: str = ""
    background_run_operator_preference_applied_summary: str = ""
    background_run_operator_preference_candidate_summary: str = ""
    background_run_operator_preference_confirm_summary: str = ""
    background_run_operator_preference_manual_summary: str = ""
    background_run_operator_preference_disabled_summary: str = ""
    background_run_operator_preference_decision_summary: str = ""
    background_run_worker_apply_accept_summary: str = ""
    background_run_worker_syncback_summary: str = ""
    background_run_manual_step_execution_summary: str = ""
    background_run_canonical_writeback_summary: str = ""
    background_run_canonical_mutation_summary: str = ""
    background_run_model_plan_summary: str = ""
    background_run_model_judge_binding_summary: str = ""
    background_run_model_judge_probe_summary: str = ""
    background_run_model_escalation_binding_summary: str = ""
    background_run_model_escalation_probe_summary: str = ""
    latest_replan_auto_route_summary: str = ""
    latest_replan_auto_route_status_summary: str = ""
    latest_replan_auto_operator_summary: str = ""
    latest_planning_handoff_summary: str = ""
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
class DocumentFlowDTO:
    summary: str = "-"
    artifact_path: str = ""
    compiled_at: str = ""
    drift_level: str = "none"
    drift_reasons: List[str] = field(default_factory=list)
    runtime_status: str = "-"
    active_requests: List[str] = field(default_factory=list)
    latest_runtime_phase: str = "-"
    objective: str = "-"
    next_steps: List[str] = field(default_factory=list)
    open_decisions: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    ongoing_doc_path: str = ""
    note_doc_path: str = ""
    latest_tf_report_path: str = ""
    open_tf_ids: List[str] = field(default_factory=list)
    recent_closed_tf_ids: List[str] = field(default_factory=list)
    stale_doc_refs: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)

@dataclass(frozen=True)
class RuntimeDetailDTO(
    _PlanningCompactSummaryCompatMixin,
    _ActiveTaskPlanningCompactSummaryCompatMixin,
    _LatestPlanningCompactSummaryCompatMixin,
):
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    chat_console_path: str
    chat_console_label: str
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
    active_task_job_contract_summary: str
    active_task_job_contract_goal: str
    active_task_job_contract_scope: str
    active_task_job_contract_non_goals: str
    active_task_job_contract_acceptance_checks: str
    active_task_job_contract_artifacts_to_touch: str
    active_task_job_contract_rollback_hint: str
    active_task_planning_compact_summary: str
    active_task_planning_lanes_summary: str
    active_task_approved_plan_gate_summary: str
    active_task_planner_lane_summary: str
    active_task_critic_lane_summary: str
    active_task_critic_review_summary: str
    active_task_critic_review_blocking_issues: str
    active_task_critic_review_required_fixes: str
    active_task_approved_plan_summary: str
    active_task_approved_plan_artifact_rows: str
    active_task_debug_packet_summary: str
    active_task_debug_packet_symptom: str
    active_task_debug_packet_root_cause: str
    active_task_debug_packet_evidence: str
    active_task_debug_packet_failed_attempt: str
    active_task_debug_packet_next_step: str
    active_task_phase_checkpoint_summary: str
    active_task_phase_checkpoint_current_phase: str
    active_task_phase_checkpoint_rows: str
    active_task_followup_brief_status: str
    active_task_followup_brief_summary: str
    active_task_followup_brief_execution_lanes: str
    active_task_followup_brief_review_lanes: str
    active_task_followup_brief_reason: str
    active_task_context_pack_profile: str
    active_task_context_pack_summary: str
    active_task_context_pack_docs: str
    active_task_context_pack_excluded: str
    active_task_general_subagent_summary: str
    active_task_general_subagent_artifact_summary: str
    active_task_general_subagent_artifact_path: str
    active_task_general_subagent_gate_summary: str
    active_task_model_plan_summary: str
    active_task_judge_binding_summary: str
    active_task_judge_probe_summary: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_launch_mode: str
    active_task_background_run_runtime_handle: str
    active_task_background_run_runtime_summary: str
    active_task_background_run_external_phase: str
    active_task_background_run_external_note: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_launch_spec_summary: str
    active_task_background_run_task_contract_summary: str
    active_task_background_run_task_contract_module_summary: str
    active_task_background_run_task_contract_policy_summary: str
    active_task_background_run_worker_gate_summary: str
    active_task_background_run_worker_profile_summary: str
    active_task_background_run_worker_checklist_summary: str
    active_task_background_run_worker_items_summary: str
    active_task_background_run_worker_items: str
    active_task_background_run_worker_item_classes_summary: str
    active_task_background_run_worker_item_classes: str
    active_task_background_run_worker_records_summary: str
    active_task_background_run_worker_records: str
    active_task_background_run_worker_record_rows_summary: str
    active_task_background_run_worker_record_rows: str
    active_task_background_run_worker_record_set_summary: str
    active_task_background_run_worker_record_set: str
    active_task_background_run_worker_preflight_summary: str
    active_task_background_run_worker_preflight_rows_summary: str
    active_task_background_run_worker_preflight_rows: str
    active_task_background_run_worker_result_summary: str
    active_task_background_run_worker_result_actions: str
    active_task_background_run_worker_result_cautions: str
    active_task_background_run_worker_result_evidence_refs: str
    active_task_background_run_worker_update_stub_status: str
    active_task_background_run_worker_update_stub_summary: str
    active_task_background_run_worker_update_stub_targets: str
    active_task_background_run_worker_update_proposal_summary: str
    active_task_background_run_worker_update_proposal_ids: List[str]
    active_task_background_run_worker_update_operator_summary: str
    active_task_background_run_operator_preference_artifact_kind: str
    active_task_background_run_operator_preference_preflight_summary: str
    active_task_background_run_operator_preference_applied_summary: str
    active_task_background_run_operator_preference_candidate_summary: str
    active_task_background_run_operator_preference_confirm_summary: str
    active_task_background_run_operator_preference_manual_summary: str
    active_task_background_run_operator_preference_disabled_summary: str
    active_task_background_run_operator_preference_decision_summary: str
    active_task_background_run_worker_apply_accept_summary: str
    active_task_background_run_worker_syncback_summary: str
    active_task_background_run_model_plan_summary: str
    active_task_background_run_model_judge_binding_summary: str
    active_task_background_run_model_judge_probe_summary: str
    active_task_background_run_model_escalation_binding_summary: str
    active_task_background_run_model_escalation_probe_summary: str
    workspace_summary: str
    document_registry_summary: str
    model_routing_summary: str
    model_registry_summary: str
    latest_judge_summary: str
    latest_judge_decision_summary: str
    latest_judge_decision_bridge_summary: str
    latest_replan_auto_decision_summary: str
    latest_replan_auto_routing_policy_summary: str
    latest_replan_auto_route_summary: str
    latest_replan_auto_route_status_summary: str
    latest_replan_auto_operator_summary: str
    latest_planning_handoff_summary: str
    latest_planning_compact_summary: str
    latest_manual_step_summary: str
    latest_canonical_writeback_summary: str
    latest_canonical_mutation_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_scheduler_summary: str
    background_scheduler_note: str
    background_queue_depth: int
    background_queue_stale_count: int
    active_task_completion_focus: str
    active_task_completion_done: str
    active_task_completion_rerun: str
    active_task_completion_followup: str
    active_task_backend: str
    active_task_backend_note: str
    active_task_rate_limit: str
    document_flow: DocumentFlowDTO = field(default_factory=DocumentFlowDTO)
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
    chat_console_path: str
    chat_console_label: str
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
class RecoveryRuntimeDTO(_LatestPlanningCompactSummaryCompatMixin):
    project_key: str
    project_alias: str
    project_label: str
    runtime_path: str
    chat_console_path: str
    chat_console_label: str
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
    active_task_context_pack_summary: str
    active_task_general_subagent_summary: str
    active_task_general_subagent_artifact_summary: str
    active_task_general_subagent_artifact_path: str
    active_task_general_subagent_gate_summary: str
    active_task_model_plan_summary: str
    active_task_reentry_rails_summary: str
    active_task_background_run_status: str
    active_task_background_run_runner_target: str
    active_task_background_run_ticket_id: str
    active_task_background_run_evidence_bundle: str
    active_task_background_run_evidence_artifacts: str
    active_task_background_run_external_phase: str
    active_task_background_run_external_note: str
    active_task_background_run_launch_spec_summary: str
    active_task_background_run_worker_update_operator_summary: str
    active_task_background_run_operator_preference_artifact_kind: str
    active_task_background_run_operator_preference_preflight_summary: str
    active_task_background_run_operator_preference_applied_summary: str
    active_task_background_run_operator_preference_candidate_summary: str
    active_task_background_run_operator_preference_confirm_summary: str
    active_task_background_run_operator_preference_manual_summary: str
    active_task_background_run_operator_preference_disabled_summary: str
    active_task_background_run_operator_preference_decision_summary: str
    active_task_background_run_worker_update_proposal_summary: str
    active_task_background_run_worker_apply_accept_summary: str
    active_task_background_run_worker_syncback_summary: str
    active_task_background_run_model_plan_summary: str
    workspace_summary: str
    document_registry_summary: str
    model_routing_summary: str
    model_registry_summary: str
    latest_judge_summary: str
    latest_judge_decision_summary: str
    latest_judge_decision_bridge_summary: str
    latest_replan_auto_decision_summary: str
    latest_replan_auto_routing_policy_summary: str
    latest_replan_auto_route_summary: str
    latest_replan_auto_route_status_summary: str
    latest_replan_auto_operator_summary: str
    latest_planning_handoff_summary: str
    latest_planning_compact_summary: str
    latest_subagent_evidence_summary: str
    latest_subagent_gate_summary: str
    latest_manual_step_summary: str
    latest_canonical_writeback_summary: str
    latest_canonical_mutation_summary: str
    run_lock_mode: str
    run_lock_note: str
    background_slot_limit: int
    background_slot_active: int
    background_slot_pressure: str
    background_worker_status: str
    background_worker_summary: str
    background_queue_summary: str
    background_scheduler_summary: str
    background_scheduler_note: str
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
    server_guard: ServerGuardDTO
    control_phase2_action_buttons: List[ActionButtonDTO] = field(default_factory=list)
    runtimes: List[RecoveryRuntimeDTO] = field(default_factory=list)


@dataclass(frozen=True)
class ActionAuditPageDTO:
    exists: bool
    audit_path: str
    updated_at: str
    stale: bool
    error: str
    limit: int
    total_rows: int
    status_summary: str
    focus_summary: str
    focus_filter: str
    project_filter: str
    artifact_filter: str
    memory_scope_filter: str
    refresh_diff_filter: str
    chat_event_filter: str
    chat_mode_filter: str
    chat_filter: str
    query_filter: str
    artifact_query_base: str
    memory_scope_query_base: str
    refresh_diff_query_base: str
    chat_event_query_base: str
    chat_mode_query_base: str
    focus_counts: Dict[str, int]
    project_counts: Dict[str, int]
    artifact_counts: Dict[str, int]
    memory_scope_counts: Dict[str, int]
    refresh_diff_counts: Dict[str, int]
    chat_event_counts: Dict[str, int]
    chat_mode_counts: Dict[str, int]
    rows: List[ActionAuditRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class HistorySearchRowDTO(_PlanningCompactSummaryCompatMixin):
    at: str
    scope: str
    source: str
    chat_id: str
    chat_mode: str
    room: str
    actor: str
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
    summary_highlight_html: str = ""
    detail_highlight_html: str = ""
    planning_compact_summary: str = ""
    subagent_contract_summary: str = ""
    subagent_evidence_summary: str = ""
    subagent_artifact_path: str = ""
    subagent_gate_summary: str = ""
    approved_plan_summary: str = ""
    pressure_kind_label: str = ""
    pressure_kind_note: str = ""

@dataclass(frozen=True)
class HistorySearchPageDTO:
    query: str
    compact_mode: bool
    chat_event_filter: str
    chat_event_query_base: str
    chat_mode_filter: str
    chat_mode_query_base: str
    room_kind_filter: str
    room_kind_query_base: str
    room_actor_filter: str
    room_actor_query_base: str
    room_text_filters: List[str]
    room_text_query_base: str
    chat_filter: str
    project_filter: str
    since_label: str
    scope: str
    limit: int
    total_rows: int
    chat_event_counts: Dict[str, int] = field(default_factory=dict)
    chat_mode_counts: Dict[str, int] = field(default_factory=dict)
    room_kind_counts: Dict[str, int] = field(default_factory=dict)
    room_kind_values: List[str] = field(default_factory=list)
    room_actor_counts: Dict[str, int] = field(default_factory=dict)
    room_actor_values: List[str] = field(default_factory=list)
    room_text_counts: Dict[str, int] = field(default_factory=dict)
    room_text_values: List[str] = field(default_factory=list)
    room_text_hints: Dict[str, str] = field(default_factory=dict)
    rows: List[HistorySearchRowDTO] = field(default_factory=list)


@dataclass(frozen=True)
class OperatorPreferenceRuleDTO:
    project_key: str
    project_alias: str
    project_label: str
    project_team_dir: str
    runtime_ref: str
    runtime_path: str
    id: str
    key: str
    artifact_kind: str
    scope: str
    scope_ref: str
    value_summary: str
    value_json: str
    description: str
    enabled: bool
    prompt_mode: str
    source: str
    confidence: str
    summary: str
    updated_at: str


@dataclass(frozen=True)
class OperatorPreferenceCandidateDTO:
    project_key: str
    project_alias: str
    project_label: str
    project_team_dir: str
    runtime_ref: str
    runtime_path: str
    id: str
    key: str
    artifact_kind: str
    project_ref: str
    expected_scope: str
    expected_scope_label: str
    expected_scope_ref: str
    suggested_value_summary: str
    suggested_value_json: str
    issue: str
    hits: int
    source_refs_summary: str
    summary: str
    updated_at: str


@dataclass(frozen=True)
class OperatorPreferenceProjectSummaryDTO:
    project_key: str
    project_alias: str
    project_label: str
    project_team_dir: str
    runtime_ref: str
    runtime_path: str
    filter_value: str
    filter_href: str
    is_active: bool
    is_selected: bool
    registry_file: FileFreshnessDTO
    candidate_file: FileFreshnessDTO
    rule_count: int
    candidate_count: int
    rule_summary: str
    candidate_summary: str
    scope_summary: str
    artifact_summary: str


@dataclass(frozen=True)
class OperatorPreferenceArtifactSummaryDTO:
    artifact_kind: str
    filter_value: str
    filter_href: str
    audit_href: str
    history_href: str
    is_selected: bool
    rule_count: int
    candidate_count: int
    ready_candidate_count: int
    prompt_mode_summary: str
    project_summary: str


@dataclass(frozen=True)
class OperatorPreferenceMemoryScopeSummaryDTO:
    scope: str
    scope_label: str
    filter_value: str
    filter_href: str
    audit_href: str
    history_href: str
    is_selected: bool
    rule_count: int
    candidate_count: int
    ready_candidate_count: int
    enabled_count: int
    disabled_count: int
    prompt_mode_summary: str
    artifact_summary: str
    project_summary: str


@dataclass(frozen=True)
class OperatorPreferencesPageDTO:
    project_alias: str
    project_label: str
    project_team_dir: str
    return_path: str
    registry_file: FileFreshnessDTO
    candidate_file: FileFreshnessDTO
    rule_summary: str
    candidate_summary: str
    scope_summary: str
    artifact_summary: str
    project_filter: str = ""
    artifact_filter: str = ""
    memory_scope_filter: str = ""
    selected_scope_summary: str = ""
    selected_artifact_summary: str = ""
    selected_memory_scope_summary: str = ""
    visible_project_count: int = 0
    total_project_count: int = 0
    registry_file_summary: str = ""
    candidate_file_summary: str = ""
    clear_project_href: str = "/control/preferences"
    clear_artifact_href: str = "/control/preferences"
    clear_memory_scope_href: str = "/control/preferences"
    rule_action_path: str = "/control/actions/control/operator-preference-rule"
    candidate_action_path: str = "/control/actions/control/operator-preference-candidate"
    projects: List[OperatorPreferenceProjectSummaryDTO] = field(default_factory=list)
    artifact_rows: List[OperatorPreferenceArtifactSummaryDTO] = field(default_factory=list)
    memory_scope_rows: List[OperatorPreferenceMemoryScopeSummaryDTO] = field(default_factory=list)
    rules: List[OperatorPreferenceRuleDTO] = field(default_factory=list)
    candidates: List[OperatorPreferenceCandidateDTO] = field(default_factory=list)


@dataclass(frozen=True)
class ChatSessionDTO:
    chat_id: str
    chat_alias: str
    updated_at: str
    default_mode: str
    pending_mode: str
    lang: str
    report_level: str
    room: str
    selected_task_summary: str
    recent_task_summary: str
    is_selected: bool = False


@dataclass(frozen=True)
class ChatRoomLineDTO:
    at: str
    actor: str
    kind: str
    text: str


@dataclass(frozen=True)
class ChatTimelineEntryDTO:
    at: str
    source: str
    headline: str
    badge: str
    body: str
    command: str = ""
    next_step: str = ""
    room: str = ""
    detail_href: str = ""
    detail_label: str = ""


@dataclass(frozen=True)
class ServerGuardThreadDTO:
    exists: bool = False
    preview_headline: str = ""
    apply_headline: str = ""
    subagent_evidence_summary: str = ""
    subagent_artifact_path: str = ""
    subagent_gate_summary: str = ""
    pressure_kind_key: str = ""
    pressure_kind_label: str = ""
    action_sentence: str = ""
    priority_link_label: str = ""
    priority_link_note: str = ""
    preset_diff_summary: str = ""
    chat_id: str = ""
    at: str = ""
    command: str = ""
    next_step: str = ""
    detail_href: str = ""
    detail_label: str = ""
    chat_href: str = ""
    audit_href: str = ""
    health_href: str = "/control/health/view"


@dataclass(frozen=True)
class ChatSessionPresetDTO:
    label: str
    room: str
    default_mode: str
    pending_mode: str
    lang: str
    report_level: str
    note: str = ""


@dataclass(frozen=True)
class ChatRoomOptionDTO:
    room: str
    lane_label: str
    difference_summary: str
    is_selected: bool = False


@dataclass(frozen=True)
class ChatConsolePageDTO(_SelectedTaskPlanningCompactSummaryCompatMixin):
    selected_chat_id: str
    selected_chat_alias: str
    selected_room: str
    selected_project_key: str
    selected_project_alias: str
    selected_task_ref: str
    selected_default_mode: str
    selected_pending_mode: str
    selected_send_mode: str
    selected_lang: str
    selected_report_level: str
    selected_task_planning_lanes_summary: str = "-"
    selected_task_approved_plan_gate_summary: str = "-"
    selected_task_planning_compact_summary: str = "-"
    selected_task_planner_lane_summary: str = "-"
    selected_task_critic_lane_summary: str = "-"
    selected_task_approved_plan_summary: str = "-"
    rooms: List[str] = field(default_factory=list)
    room_options: List[ChatRoomOptionDTO] = field(default_factory=list)
    room_options_summary: str = "-"
    room_presets: List[str] = field(default_factory=list)
    room_preset_options: List[ChatRoomOptionDTO] = field(default_factory=list)
    session_presets: List[ChatSessionPresetDTO] = field(default_factory=list)
    recommended_session_presets: List[ChatSessionPresetDTO] = field(default_factory=list)
    deep_link_preset_label: str = ""
    deep_link_preset_note: str = ""
    deep_link_preset_room: str = ""
    deep_link_preset_default_mode: str = ""
    deep_link_preset_pending_mode: str = ""
    deep_link_preset_lang: str = ""
    deep_link_preset_report_level: str = ""
    live_preview_preset_label: str = ""
    live_preview_preset_note: str = ""
    live_preview_preset_room: str = ""
    live_preview_preset_default_mode: str = ""
    live_preview_preset_pending_mode: str = ""
    live_preview_preset_lang: str = ""
    live_preview_preset_report_level: str = ""
    selected_recent_task_refs: List[str] = field(default_factory=list)
    sessions: List[ChatSessionDTO] = field(default_factory=list)
    room_tail: List[ChatRoomLineDTO] = field(default_factory=list)
    server_guard_thread: ServerGuardThreadDTO = field(default_factory=ServerGuardThreadDTO)
    server_guard_threads: List[ServerGuardThreadDTO] = field(default_factory=list)
    timeline_entries: List[ChatTimelineEntryDTO] = field(default_factory=list)
    send_action_path: str = "/control/actions/chat/send"
    session_action_path: str = "/control/actions/chat/session-update"
    select_task_action_path: str = "/control/actions/chat/session-select-task"
    send_mode_options: Dict[str, str] = field(default_factory=dict)
    recent_chat_actions: List[ActionAuditRowDTO] = field(default_factory=list)

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
