#!/usr/bin/env python3
"""Centralized state root migration helper regressions."""

from __future__ import annotations

from _gateway_test_support import *  # noqa: F401,F403
import aoe_tg_state_root_migration as state_root_migration


def test_state_root_migration_plan_targets_centralized_artifacts(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "demo"
    legacy_team_dir = project_root / ".aoe-team"
    state_root = tmp_path / "state-root"
    legacy_team_dir.mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "orch_manager_state.json").write_text("{}", encoding="utf-8")
    (legacy_team_dir / "provider_capacity.json").write_text("{}", encoding="utf-8")
    (legacy_team_dir / "control").mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "control" / "latest-intent.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    plan = state_root_migration.build_state_root_migration_plan(project_root=project_root)

    assert plan.source_team_dir == legacy_team_dir.resolve()
    assert plan.target_team_dir == runtime_core.resolve_centralized_team_dir(project_root.resolve(), state_root.resolve())
    assert any(row.label == "manager_state" and row.action == "copy" for row in plan.rows)
    assert any(row.label == "provider_capacity" and row.action == "copy" for row in plan.rows)
    assert any(row.label == "latest_intent" and row.action == "copy" for row in plan.rows)


def test_state_root_migration_apply_copies_missing_artifacts(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "demo"
    legacy_team_dir = project_root / ".aoe-team"
    state_root = tmp_path / "state-root"
    legacy_team_dir.mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "telegram_gateway_state.json").write_text('{"chat_sessions":{}}', encoding="utf-8")
    (legacy_team_dir / "orch_manager_state.json").write_text('{"projects":{}}', encoding="utf-8")
    (legacy_team_dir / "dashboard").mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "dashboard" / "action-history.jsonl").write_text('{"headline":"Retry"}\n', encoding="utf-8")
    (legacy_team_dir / "recovery" / "nightly-session-summary").mkdir(parents=True, exist_ok=True)
    (legacy_team_dir / "recovery" / "nightly-session-summary" / "latest.json").write_text('{"generated_at":"2026-03-27T00:00:00+0900"}', encoding="utf-8")
    monkeypatch.delenv("AOE_TEAM_DIR", raising=False)
    monkeypatch.setenv("AOE_STATE_DIR", str(state_root))

    plan = state_root_migration.build_state_root_migration_plan(project_root=project_root)
    applied = state_root_migration.apply_state_root_migration(plan)

    target_team_dir = plan.target_team_dir
    assert (target_team_dir / "telegram_gateway_state.json").exists()
    assert (target_team_dir / "orch_manager_state.json").exists()
    assert (target_team_dir / "dashboard" / "action-history.jsonl").exists()
    assert (target_team_dir / "recovery" / "nightly-session-summary" / "latest.json").exists()
    assert any(row.label == "gateway_state" and row.action == "copied" for row in applied)
    assert any(row.label == "action_audit" and row.action == "copied" for row in applied)
    assert any(row.label == "recovery_summary:latest.json" and row.action == "copied" for row in applied)
