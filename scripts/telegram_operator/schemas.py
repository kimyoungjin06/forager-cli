"""Telegram remote plan-session and stage schema identifiers.

Shared string constants for plan-session artifacts. Kept in one module so
the receipt writers and the plan-session state machine reference identical
schema names without a circular import.
"""

from __future__ import annotations


REMOTE_PLAN_SESSION_SCHEMA = "telegram_remote_plan_session.v1"
PROJECT_INIT_PREVIEW_SCHEMA = "telegram_remote_project_init_preview.v1"
PROJECT_INIT_RUN_SCHEMA = "telegram_remote_project_init_run.v1"
PLAN_DRAFT_SCHEMA = "telegram_remote_plan_draft.v1"
PLAN_REGISTRATION_SCHEMA = "telegram_remote_plan_registration.v1"
PLAN_REVIEW_SCHEMA = "telegram_remote_plan_review.v1"
PLAN_LAUNCH_PREP_SCHEMA = "telegram_remote_plan_launch_prep.v1"
PLAN_GATE_REQUEST_SCHEMA = "telegram_remote_plan_gate_request.v1"
PLAN_GATE_RESOLUTION_SCHEMA = "telegram_remote_plan_gate_resolution.v1"
PLAN_EXECUTION_BRIEF_SCHEMA = "telegram_remote_plan_execution_brief.v1"
PLAN_ENQUEUE_HANDOFF_SCHEMA = "telegram_remote_plan_enqueue_handoff.v1"
PLAN_WORKLOAD_BINDING_SCHEMA = "telegram_remote_plan_workload_binding.v1"
PLAN_ENQUEUE_RUN_SCHEMA = "telegram_remote_plan_enqueue_run.v1"
PLAN_RUNTIME_START_SCHEMA = "telegram_remote_plan_runtime_start.v1"
PLAN_RUNTIME_MONITOR_SCHEMA = "telegram_remote_plan_runtime_monitor.v1"
PLAN_CLOSEOUT_PACKET_SCHEMA = "telegram_remote_plan_closeout_packet.v1"
PLAN_CLOSEOUT_REVIEW_HANDOFF_SCHEMA = "telegram_remote_plan_closeout_review_handoff.v1"
PLAN_CLOSEOUT_VERDICT_SCHEMA = "telegram_remote_plan_closeout_verdict.v1"
PLAN_DRAFT_AUTHORITY_DENIALS = [
    "enqueue",
    "launch",
    "approval",
    "file movement",
    "archive",
    "delete",
    "wiki promotion",
    "accepted truth",
]


RESULT_SCHEMA = "remote_operator_telegram_adapter_result.v1"
INTERACTION_CONTEXT_SCHEMA = "telegram_interaction_context.v1"
FORBIDDEN_REMOTE_INTENTS = (
    "approve_plan",
    "approve_launch",
    "deny_launch",
    "enqueue",
    "launch",
    "dispatch",
    "shell",
    "git_push",
    "delete",
    "provider_retarget",
)
