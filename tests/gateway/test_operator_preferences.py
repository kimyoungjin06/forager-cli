#!/usr/bin/env python3
"""Adaptive operator preference registry regressions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_operator_preferences as operator_preferences  # noqa: E402


def test_record_preference_candidate_increments_existing_candidate(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"

    operator_preferences.record_preference_candidate(
        team_dir,
        artifact_kind="chart",
        key="legend_position",
        suggested_value="bottom",
        issue="legend overlaps the plotted bars",
        source_ref="REQ-1",
        now_iso="2026-04-22T09:00:00+09:00",
    )
    candidate = operator_preferences.record_preference_candidate(
        team_dir,
        artifact_kind="chart",
        key="legend_position",
        suggested_value="bottom",
        issue="legend still overlaps the plotted bars",
        source_ref="REQ-2",
        now_iso="2026-04-22T09:05:00+09:00",
    )

    state = operator_preferences.load_operator_preference_candidates(team_dir)

    assert candidate["occurrence_count"] == 2
    assert len(state["candidates"]) == 1
    assert state["candidates"][0]["occurrence_count"] == 2
    assert state["candidates"][0]["source_refs"] == ["REQ-1", "REQ-2"]
    assert state["candidates"][0]["issue"] == "legend still overlaps the plotted bars"


def test_build_adaptive_preference_preflight_splits_prompt_modes(tmp_path: Path) -> None:
    _ = tmp_path
    state = {
        "rules": [
            {
                "artifact_kind": "chart",
                "key": "show_source_note",
                "value": True,
                "description": "Always include the source note below the chart.",
                "scope": "artifact_kind",
                "prompt_mode": "auto",
                "enabled": True,
            },
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "value": "bottom",
                "description": "Prefer the legend below wide bar charts.",
                "scope": "artifact_kind",
                "prompt_mode": "confirm",
                "enabled": True,
            },
            {
                "artifact_kind": "chart",
                "key": "color_palette",
                "value": "accessible",
                "description": "Use the accessible palette when explicitly requested.",
                "scope": "artifact_kind",
                "prompt_mode": "manual_only",
                "enabled": True,
            },
            {
                "artifact_kind": "chart",
                "key": "show_gridlines",
                "value": False,
                "description": "Keep gridlines disabled unless asked for them.",
                "scope": "artifact_kind",
                "prompt_mode": "confirm",
                "enabled": False,
            },
        ]
    }

    preflight = operator_preferences.build_adaptive_preference_preflight(
        state,
        artifact_kind="chart",
    )

    assert [row["key"] for row in preflight["auto_apply"]] == ["show_source_note"]
    assert [row["key"] for row in preflight["confirm"]] == ["legend_position"]
    assert [row["key"] for row in preflight["manual_only"]] == ["color_palette"]
    assert [row["key"] for row in preflight["disabled_defaults"]] == ["show_gridlines"]
    assert operator_preferences.summarize_preference_preflight(preflight) == (
        "preflight=chart | auto=1 | confirm=1 | manual=1 | disabled=1"
    )


def test_build_adaptive_preference_preflight_includes_seeded_defaults_when_registry_empty() -> None:
    preflight = operator_preferences.build_adaptive_preference_preflight(
        {"rules": []},
        artifact_kind="document",
    )

    assert [row["key"] for row in preflight["confirm"]] == ["preserve_heading_structure"]
    assert [row["key"] for row in preflight["manual_only"]] == ["explicit_open_questions_section"]
    assert operator_preferences.summarize_preference_preflight(preflight) == (
        "preflight=document | auto=0 | confirm=1 | manual=1 | disabled=0"
    )


def test_build_adaptive_preference_preflight_prefers_explicit_rules_over_seeded_defaults() -> None:
    preflight = operator_preferences.build_adaptive_preference_preflight(
        {
            "rules": [
                {
                    "artifact_kind": "chart",
                    "key": "legend_position",
                    "value": "right",
                    "description": "Keep the legend to the right for this chart family.",
                    "scope": "artifact_kind",
                    "prompt_mode": "auto",
                    "enabled": True,
                }
            ]
        },
        artifact_kind="chart",
    )

    assert [row["key"] for row in preflight["auto_apply"]] == ["legend_position"]
    assert [row["key"] for row in preflight["confirm"]] == []
    assert [row["key"] for row in preflight["manual_only"]] == ["show_source_note", "color_palette"]


def test_build_adaptive_preference_preflight_includes_profile_specific_seeded_defaults() -> None:
    preflight = operator_preferences.build_adaptive_preference_preflight(
        {"rules": []},
        artifact_kind="chart",
        artifact_profile="chart_bar",
    )

    assert [row["key"] for row in preflight["confirm"]] == ["legend_position", "category_order"]
    assert [row["key"] for row in preflight["manual_only"]] == ["show_source_note", "color_palette", "show_bar_value_labels"]
    assert operator_preferences.summarize_preference_preflight(preflight) == (
        "preflight=chart | profile=chart_bar | auto=0 | confirm=2 | manual=3 | disabled=0"
    )


def test_build_preference_candidate_recommendations_promotes_repeated_candidates() -> None:
    candidate_state = {
        "candidates": [
            {
                "artifact_kind": "chart",
                "key": "legend_position",
                "project_ref": "O2",
                "suggested_value": "bottom",
                "issue": "legend keeps overlapping the plotted bars",
                "occurrence_count": 2,
                "source_refs": ["REQ-1", "REQ-2"],
            },
            {
                "artifact_kind": "chart",
                "key": "show_gridlines",
                "project_ref": "O2",
                "suggested_value": False,
                "issue": "gridlines keep being removed in follow-up requests",
                "occurrence_count": 1,
                "source_refs": ["REQ-3"],
            },
        ]
    }

    recommendations = operator_preferences.build_preference_candidate_recommendations(
        candidate_state,
        preference_state={"rules": []},
        artifact_kind="chart",
        project_ref="O2",
    )

    assert [row["key"] for row in recommendations] == ["legend_position"]
    assert recommendations[0]["occurrence_count"] == 2
    assert recommendations[0]["value"] == "bottom"
    assert len(recommendations[0]["options"]) == 4
    assert operator_preferences.summarize_preference_candidates(recommendations).startswith(
        "preference_candidates=legend_position=bottom"
    )


def test_apply_preference_decision_persists_apply_always_rule(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"

    result = operator_preferences.apply_preference_decision(
        team_dir,
        decision={
            "artifact_kind": "chart",
            "key": "legend_position",
            "value": "bottom",
            "description": "Keep the legend below the chart.",
            "choice": "apply_always",
        },
        now_iso="2026-04-22T09:10:00+09:00",
    )

    registry = operator_preferences.load_operator_preferences(team_dir)

    assert result["ok"] is True
    assert result["persisted"] is True
    assert result["request_override"]["scope"] == "session"
    assert result["request_override"]["enabled"] is True
    assert registry["rules"][0]["key"] == "legend_position"
    assert registry["rules"][0]["enabled"] is True
    assert registry["rules"][0]["prompt_mode"] == "auto"
    assert registry["rules"][0]["scope"] == "artifact_kind"
    assert registry["rules"][0]["scope_ref"] == "chart"


def test_apply_preference_decision_persists_skip_always_rule(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"

    result = operator_preferences.apply_preference_decision(
        team_dir,
        decision={
            "artifact_kind": "chart",
            "key": "show_gridlines",
            "value": False,
            "description": "Do not show gridlines by default.",
            "choice": "skip_always",
        },
        now_iso="2026-04-22T09:12:00+09:00",
    )

    registry = operator_preferences.load_operator_preferences(team_dir)

    assert result["ok"] is True
    assert result["persisted"] is True
    assert result["request_override"]["enabled"] is False
    assert registry["rules"][0]["key"] == "show_gridlines"
    assert registry["rules"][0]["enabled"] is False
    assert registry["rules"][0]["scope"] == "artifact_kind"
    assert registry["rules"][0]["scope_ref"] == "chart"
