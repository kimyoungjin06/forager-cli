#!/usr/bin/env python3
"""Legacy planning compact compatibility helpers for dashboard regressions."""

from __future__ import annotations

import json
from pathlib import Path


LEGACY_PLANNING_REVIEW_SUMMARY = (
    "draft via codex | review via claude | dispatch waits for critic-approved plan"
)


def legacy_planning_review_payload() -> dict[str, str]:
    return {"planning_review_summary": LEGACY_PLANNING_REVIEW_SUMMARY}


def rewrite_latest_nightly_runtime_with_legacy_planning_review_key(latest_json: Path) -> None:
    payload = json.loads(latest_json.read_text(encoding="utf-8"))
    payload["runtimes"][0]["latest_planning_review_summary"] = payload["runtimes"][0].pop(
        "latest_planning_compact_summary"
    )
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
