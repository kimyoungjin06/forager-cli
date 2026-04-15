#!/usr/bin/env python3
"""Compatibility facade for dashboard mutation execution helpers."""

from __future__ import annotations

from control_dashboard_action_exec_auto import _execute_auto_recover_action
from control_dashboard_action_exec_background import _execute_background_queue_clean_action
from control_dashboard_action_exec_chat import _execute_chat_send_action
from control_dashboard_action_exec_runtime import (
    _execute_analysis_review_action,
    _execute_runtime_judge_action,
    _execute_runtime_syncback_apply_action,
    _execute_runtime_syncback_preview_action,
    _execute_todo_proposal_action,
    _execute_worker_apply_accept_action,
    _execute_worker_apply_preview_action,
    _execute_worker_apply_propose_action,
    _execute_worker_update_preview_action,
)
from control_dashboard_action_exec_retry import (
    _execute_followup_action,
    _execute_followup_run_transition,
    _execute_retry_action,
    _execute_retry_run_transition,
)
from control_dashboard_action_exec_shared import _load_dashboard_manager_state

__all__ = [
    "_execute_analysis_review_action",
    "_execute_background_queue_clean_action",
    "_execute_auto_recover_action",
    "_execute_chat_send_action",
    "_execute_runtime_judge_action",
    "_execute_runtime_syncback_apply_action",
    "_execute_runtime_syncback_preview_action",
    "_execute_todo_proposal_action",
    "_execute_worker_apply_accept_action",
    "_execute_worker_apply_preview_action",
    "_execute_worker_apply_propose_action",
    "_execute_worker_update_preview_action",
    "_execute_followup_action",
    "_execute_followup_run_transition",
    "_execute_retry_action",
    "_execute_retry_run_transition",
    "_load_dashboard_manager_state",
]
