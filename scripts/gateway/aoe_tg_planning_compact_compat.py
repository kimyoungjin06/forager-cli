#!/usr/bin/env python3
"""Legacy compatibility helpers for planning compact summary migration."""

from __future__ import annotations

from typing import Any


def legacy_planning_review_summary(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("planning_review_summary", "")).strip() or str(raw.get("planning_review", "")).strip()


def legacy_planning_review_summary_alias(summary: str) -> str:
    # Read-only compatibility shim for older callers that still ask for
    # planning_review_summary while current-generation code uses
    # planning_compact_summary.
    return str(summary or "").strip()
