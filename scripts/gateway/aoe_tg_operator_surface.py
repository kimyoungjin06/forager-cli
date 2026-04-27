#!/usr/bin/env python3
"""Shared operator summary surface helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from aoe_tg_action_audit import append_latest_action_lines, append_latest_action_summary_line
from aoe_tg_operator_summary import append_latest_intent_lines, append_latest_intent_summary_line


def append_operator_status_lines(
    lines: List[str],
    *,
    latest_intent: Dict[str, str],
    latest_action: Dict[str, str],
    compact_reason: Optional[Callable[[Any, int], str]] = None,
    line_prefix: str = "",
) -> None:
    append_latest_intent_lines(
        lines,
        latest_intent,
        compact_reason=compact_reason,
        line_prefix=line_prefix,
    )
    append_latest_action_lines(
        lines,
        latest_action,
        compact_reason=compact_reason,
        line_prefix=line_prefix,
    )


def append_operator_status_summary_lines(
    lines: List[str],
    *,
    latest_intent: Dict[str, str],
    latest_action: Dict[str, str],
    compact_reason: Optional[Callable[[Any, int], str]] = None,
    line_prefix: str = "",
) -> None:
    append_latest_intent_summary_line(
        lines,
        latest_intent,
        compact_reason=compact_reason,
        line_prefix=line_prefix,
    )
    append_latest_action_summary_line(
        lines,
        latest_action,
        compact_reason=compact_reason,
        line_prefix=line_prefix,
    )
