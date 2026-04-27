#!/usr/bin/env python3
"""Facade for dashboard state assembly helpers."""

from __future__ import annotations

from control_dashboard_state_common import (
    _next_retry_target_text,
    _provider_summary_text,
    _recovery_control_action_buttons,
    _recovery_summary_path,
    _repeat_summary_text,
)
from control_dashboard_state_recovery_builders import _build_recovery_summary
from control_dashboard_state_runtime_builders import _build_runtime_cards, _build_runtime_detail
from control_dashboard_state_task_builders import _build_active_task_rows, _build_task_detail

__all__ = [
    "_build_active_task_rows",
    "_build_recovery_summary",
    "_build_runtime_cards",
    "_build_runtime_detail",
    "_build_task_detail",
    "_next_retry_target_text",
    "_provider_summary_text",
    "_recovery_control_action_buttons",
    "_recovery_summary_path",
    "_repeat_summary_text",
]
