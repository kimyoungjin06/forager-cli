"""Shared receiver transport policy; acceptance and delivery holds are separate."""
from __future__ import annotations

import datetime as dt
from typing import Any

from .persistence import parse_utc_timestamp

TRANSPORT_FAILURE_COUNT = 3
TRANSPORT_FAILURE_SECONDS = 180


def failure_age(loop: dict[str, Any], now: dt.datetime) -> int | None:
    start = parse_utc_timestamp(loop.get("last_success_at")) or parse_utc_timestamp(loop.get("generated_at"))
    return max(0, int((now - start).total_seconds())) if start else None


def classify_transport_issues(issues: list[str], *, count: Any, age: Any) -> tuple[list[str], list[str]]:
    issues = list(issues)
    warnings: list[str] = []
    if issues == ["last_poll_transport_error"]:
        if type(count) is int and count > 0 and type(age) is int and age >= 0:
            if count < TRANSPORT_FAILURE_COUNT or age < TRANSPORT_FAILURE_SECONDS:
                warnings.append(issues.pop())
    return issues, warnings
