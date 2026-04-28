#!/usr/bin/env python3
"""Live rehearsal runtime seed helpers."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))

import aoe_tg_runtime_read as runtime_read  # noqa: E402
import aoe_tg_task_state as task_state  # noqa: E402
from aoe_tg_live_rehearsal_seed import (  # noqa: E402
    seed_b2_build_rerun_runtime,
    seed_b3_build_manual_followup_runtime,
    seed_d2_data_rerun_runtime,
    seed_m2_mixed_rerun_runtime,
    seed_r2_review_rerun_runtime,
    seed_r3_manual_followup_execute_runtime,
    seed_r4_external_background_runtime,
)


def test_seed_b2_build_rerun_runtime_creates_isolated_retry_candidate(tmp_path: Path) -> None:
    payload = seed_b2_build_rerun_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-B2-001"]

    assert project["project_alias"] == "O5"
    assert project["overview"] == "isolated build rerun live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert project["background_runner_slot_limits"]["local_tmux"] == 1
    assert task["phase1_role_preset"] == "build"
    assert task["phase2_team_preset"] == "build"
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "executable"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["lane_id"] == "L1"
    assert task["exec_critic"]["rerun_execution_lane_ids"] == ["L1"]
    assert task["exec_critic"]["rerun_review_lane_ids"] == ["R1"]
    assert task["reentry_rails_summary"] == "retry=ready exec=L1 review=R1 | followup=none"
    assert payload["reentry_rails_summary"] == task["reentry_rails_summary"]
    assert payload["trigger_command"] == "/retry T-501 lane L1"


def test_seed_b3_build_manual_followup_runtime_creates_canonical_followup_candidate(tmp_path: Path) -> None:
    payload = seed_b3_build_manual_followup_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-B3-001"]

    assert project["project_alias"] == "O6"
    assert project["overview"] == "isolated build manual followup live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert project["background_runner_slot_limits"]["local_tmux"] == 1
    assert task["phase1_role_preset"] == "build"
    assert task["phase2_team_preset"] == "build"
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task["phase_checkpoint_status"] == "active"
    assert task["phase_checkpoint_current_phase"] == "verify"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task_state.derive_task_manual_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "partially_executable"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["lane_id"] == "L2"
    assert task["followup_brief_status"] == "partially_executable"
    assert task["followup_brief_execution_lane_ids"] == ["L2"]
    assert task["followup_brief_review_lane_ids"] == ["R1"]
    assert task["exec_critic"]["manual_followup_execution_lane_ids"] == []
    assert task["exec_critic"]["manual_followup_review_lane_ids"] == []
    assert task["reentry_rails_summary"] == "retry=none | followup=partially_executable exec=L2 review=R1"
    assert payload["reentry_rails_summary"] == task["reentry_rails_summary"]
    assert payload["trigger_command"] == "/followup-exec T-601 lane L2"


def test_seed_d2_data_rerun_runtime_creates_isolated_null_heavy_candidate(tmp_path: Path) -> None:
    payload = seed_d2_data_rerun_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    project_root = Path(payload["project_root"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-D2-001"]

    assert project["project_alias"] == "O7"
    assert project["overview"] == "isolated data rerun live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert task["phase1_role_preset"] == "data"
    assert task["phase2_team_preset"] == "data"
    assert task["request_contract_type"] == "data"
    assert task["request_contract_status"] == "complete"
    assert task["request_contract_fields"]["quality_gate_policy"]["null_heavy_min_rows_per_column"] == "2"
    assert task["request_contract_artifact_contracts"]["null_summary"]["required_fields"][:5] == [
        "affected_columns",
        "null_or_invalid_count",
        "null_heavy",
        "rerun_required",
        "reason",
    ]
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "executable"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["lane_id"] == "L1"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["role"] == "DataEngineer"
    assert task["exec_critic"]["rerun_execution_lane_ids"] == ["L1"]
    assert task["exec_critic"]["rerun_review_lane_ids"] == ["R1"]
    assert task["reentry_rails_summary"] == "retry=ready exec=L1 review=R1 | followup=none"
    assert payload["trigger_command"] == "/retry T-701 lane L1"

    for artifact in payload["artifact_paths"]:
        assert (project_root / artifact).exists()
    null_summary = (project_root / "null_summary.md").read_text(encoding="utf-8")
    assert "affected_columns: orders, revenue" in null_summary
    assert "null_or_invalid_count:" in null_summary
    assert "- orders: 2" in null_summary
    assert "- revenue: 2" in null_summary
    assert "null_heavy: true" in null_summary
    assert "rerun_required: true" in null_summary
    assert "close as rerun, not done" in null_summary


def test_seed_m2_mixed_rerun_runtime_creates_handoff_retry_candidate(tmp_path: Path) -> None:
    payload = seed_m2_mixed_rerun_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    project_root = Path(payload["project_root"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-M2-001"]

    assert project["project_alias"] == "O8"
    assert project["overview"] == "isolated mixed rerun live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert task["phase1_role_preset"] == "mixed"
    assert task["phase2_team_preset"] == "mixed"
    assert task["request_contract_type"] == "mixed"
    assert task["request_contract_status"] == "complete"
    assert task["request_contract_required_outputs"] == [
        "work_result",
        "scope_inventory",
        "handoff_doc",
        "reviewer_note",
    ]
    assert task["request_contract_artifact_contracts"]["handoff_doc"]["path"] == "docs/handoff/operator_handoff.md"
    assert task["request_contract_artifact_contracts"]["reviewer_note"]["path"] == "docs/reviews/reviewer_note.md"
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "executable"
    execution_lanes = task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"]
    assert [lane["lane_id"] for lane in execution_lanes] == ["L1", "L2"]
    assert [lane["role"] for lane in execution_lanes] == ["Codex-Dev", "Codex-Writer"]
    assert task["exec_critic"]["rerun_execution_lane_ids"] == ["L2"]
    assert task["exec_critic"]["rerun_review_lane_ids"] == ["R1"]
    assert task["reentry_rails_summary"] == "retry=ready exec=L2 review=R1 | followup=none"
    assert payload["reentry_rails_summary"] == task["reentry_rails_summary"]
    assert payload["trigger_command"] == "/retry T-801 lane L2"

    for artifact in payload["artifact_paths"]:
        assert (project_root / artifact).exists()
    handoff = (project_root / "docs" / "handoff" / "operator_handoff.md").read_text(encoding="utf-8")
    reviewer_note = (project_root / "docs" / "reviews" / "reviewer_note.md").read_text(encoding="utf-8")
    assert "missing changed files: tests/session.test.js" in handoff
    assert "retry writer/handoff lane L2" in reviewer_note
    assert "do not rerun implementation lane L1" in reviewer_note


def test_seed_r2_review_rerun_runtime_creates_isolated_retry_candidate(tmp_path: Path) -> None:
    payload = seed_r2_review_rerun_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-R2-001"]

    assert (team_dir / "AOE_TODO.md").read_text(encoding="utf-8").strip() == "Alpha/TODO.md"
    assert project["project_alias"] == "O2"
    assert project["overview"] == "isolated review rerun live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert project["background_runner_slot_limits"]["local_tmux"] == 1
    assert task["phase1_role_preset"] == "review"
    assert task["phase2_team_preset"] == "review"
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "executable"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["lane_id"] == "L1"
    assert task["exec_critic"]["rerun_execution_lane_ids"] == ["L1"]
    assert task["exec_critic"]["rerun_review_lane_ids"] == ["R1"]
    assert task["reentry_rails_summary"] == "retry=ready exec=L1 review=R1 | followup=none"
    assert payload["reentry_rails_summary"] == task["reentry_rails_summary"]
    assert payload["trigger_command"] == "/retry T-201 lane L1"


def test_seed_r3_manual_followup_execute_runtime_creates_isolated_followup_candidate(tmp_path: Path) -> None:
    payload = seed_r3_manual_followup_execute_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="local_tmux",
        local_tmux_slot_limit=1,
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-R3-001"]

    assert project["project_alias"] == "O3"
    assert project["overview"] == "isolated review followup execute live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "local_tmux"
    assert project["background_runner_slot_limits"]["local_tmux"] == 1
    assert task["approved_plan_status"] == "approved"
    assert task["critic_review_status"] == "approved"
    assert task["phase_checkpoint_status"] == "active"
    assert task["phase_checkpoint_current_phase"] == "verify"
    assert task_state.derive_task_dispatch_gate(task)["status"] == "ready"
    assert task_state.derive_task_manual_gate(task)["status"] == "ready"
    assert task["execution_brief_status"] == "partially_executable"
    assert task["plan"]["meta"]["phase2_execution_plan"]["execution_lanes"][0]["lane_id"] == "L2"
    assert task["followup_brief_status"] == "partially_executable"
    assert task["followup_brief_execution_lane_ids"] == ["L2"]
    assert task["followup_brief_review_lane_ids"] == ["R1"]
    assert task["exec_critic"]["manual_followup_execution_lane_ids"] == ["L2"]
    assert task["exec_critic"]["manual_followup_review_lane_ids"] == ["R1"]
    assert task["reentry_rails_summary"] == "retry=none | followup=partially_executable exec=L2 review=R1"
    assert payload["reentry_rails_summary"] == task["reentry_rails_summary"]
    assert payload["trigger_command"] == "/followup-exec T-301 lane L2"


def test_seed_r4_external_background_runtime_creates_handoff_seed(tmp_path: Path) -> None:
    payload = seed_r4_external_background_runtime(
        tmp_path / "control",
        run_lock_mode="test_only",
        runner_target="github_runner",
    )

    control_root = Path(payload["control_root"])
    team_dir = Path(payload["team_dir"])
    project_root = Path(payload["project_root"])
    manager_state_file = Path(payload["manager_state_file"])
    state = runtime_read.load_manager_state(manager_state_file, control_root, team_dir)
    project = state["projects"]["alpha"]
    task = project["tasks"]["REQ-R4-001"]

    assert project["project_alias"] == "O4"
    assert project["overview"] == "isolated external background rail live rehearsal"
    assert project["run_lock_mode"] == "test_only"
    assert project["background_runner_target"] == "github_runner"
    assert task["background_run_status"] == "running"
    assert task["background_run_runner_target"] == "github_runner"
    assert task["background_run_external_phase"] == "handoff_emitted"
    assert task["reentry_rails_summary"] == "retry=ready exec=L1 review=R1 | followup=none | bg=running/github_runner"
    assert payload["trigger_commands"] == [
        "/orch bgx-emit-ack O4",
        "/orch bgx-emit-result O4 completed",
    ]
    assert (project_root / ".aoe-team" / "background_run_handoffs" / "github-runner-bgt-r4-001.json").exists()
