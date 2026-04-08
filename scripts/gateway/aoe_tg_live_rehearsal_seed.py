#!/usr/bin/env python3
"""Seed isolated live-rehearsal runtimes without launching internal jobs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import aoe_tg_runtime_read as runtime_read


R2_REQUEST_TEXT = (
    "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행해줘. canonical diff range, 변경 파일, "
    "severity findings, test gaps, uncertainties를 review_report.md에 남겨라. "
    "범위 근거나 필수 섹션이 부족하면 done으로 닫지 말고 rerun으로 남겨라."
)

R3_EXECUTE_REQUEST_TEXT = (
    "로그인 패치의 회귀 리스크 후보를 정리하고, 내가 지정한 lane만 후속 증거 수집으로 다시 실행해줘."
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare_project_layout(control_root: Path) -> tuple[Path, Path, Path]:
    team_dir = control_root / ".aoe-team"
    project_root = control_root / "Alpha"
    project_team_dir = project_root / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    project_team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "AOE_TODO.md").write_text("Alpha/TODO.md\n", encoding="utf-8")
    (project_root / "TODO.md").write_text("# TODO\n", encoding="utf-8")
    (project_team_dir / "AOE_TODO.md").write_text("TODO.md\n", encoding="utf-8")
    _write_json(
        project_team_dir / "orchestrator.json",
        {
            "version": 1,
            "project_root": str(project_root),
            "team_dir": str(project_team_dir),
            "overview": "isolated R2 review rerun rehearsal",
        },
    )
    return team_dir, project_root, project_team_dir


def _r2_task(now: str) -> Dict[str, Any]:
    return {
        "request_id": "REQ-R2-001",
        "short_id": "T-201",
        "alias": "review-rerun",
        "prompt": R2_REQUEST_TEXT,
        "mode": "dispatch",
        "status": "failed",
        "stage": "verification",
        "stages": {
            "intake": "done",
            "planning": "done",
            "execution": "done",
            "verification": "failed",
            "integration": "failed",
            "close": "failed",
        },
        "roles": ["Codex-Reviewer", "Claude-Reviewer"],
        "verifier_roles": ["Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "review",
        "phase2_team_preset": "review",
        "execution_brief_status": "executable",
        "execution_brief_summary": "executable | do=review_evidence/*,review_report.md | blocked=-",
        "execution_brief_executable_slice": [
            "review_evidence/git_diff_scope.md",
            "review_evidence/severity_rationale.md",
            "review_report.md",
        ],
        "execution_brief_blocked_slice": [],
        "execution_brief_operator_decision": "",
        "reentry_rails_summary": "retry=executable exec=L1 review=R1 | followup=none | bg=-",
        "plan": {
            "summary": "review | auth/session scope -> canonical diff+severity -> test gaps+uncertainties | review lane validates review_report",
            "meta": {
                "phase1_role_preset": "review",
                "phase2_team_preset": "review",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L1", "role": "Codex-Reviewer", "kind": "review_execution"},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Reviewer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {"lane_id": "L1", "role": "Codex-Reviewer", "kind": "review_execution", "outputs": ["review_report"]},
                    ],
                    "review_lanes": [
                        {"lane_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"], "outputs": ["review_report"]},
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L1",
                    "role": "Codex-Reviewer",
                    "status": "done",
                    "subtask_ids": ["S1"],
                    "touched_files": ["review_report.md", "review_evidence/git_diff_scope.md"],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "failed",
                    "depends_on": ["L1"],
                    "reason": "review scope was incomplete; rerun reviewer lane over canonical diff + severity path",
                    "verdict": "retry",
                    "action": "rerun",
                    "touched_files": ["review_report.md"],
                }
            ],
            "summary": {
                "execution": {"done": 1},
                "review": {"failed": 1},
                "review_verdicts": {"retry": 1},
            },
        },
        "exec_critic": {
            "verdict": "retry",
            "action": "retry",
            "reason": "review scope was incomplete; rerun canonical diff + severity path",
            "rerun_execution_lane_ids": ["L1"],
            "rerun_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "retry",
            "backend_contract": "review_rerun",
            "backend_contract_note": "rerun lane targets are explicit and remain lane-scoped",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O2",
            "task_short_id": "T-201",
        },
        "created_at": now,
        "updated_at": now,
    }


def _r3_execute_task(now: str) -> Dict[str, Any]:
    return {
        "request_id": "REQ-R3-001",
        "short_id": "T-301",
        "alias": "review-followup-execute",
        "prompt": R3_EXECUTE_REQUEST_TEXT,
        "mode": "dispatch",
        "status": "failed",
        "stage": "verification",
        "stages": {
            "intake": "done",
            "planning": "done",
            "execution": "done",
            "verification": "failed",
            "integration": "failed",
            "close": "failed",
        },
        "roles": ["Codex-Reviewer", "Claude-Reviewer"],
        "verifier_roles": ["Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "review",
        "phase2_team_preset": "review",
        "execution_brief_status": "partially_executable",
        "execution_brief_summary": "partially_executable | do=review_evidence/followup_scope.md | blocked=operator-owned review wording",
        "execution_brief_executable_slice": [
            "review_evidence/followup_scope.md",
        ],
        "execution_brief_blocked_slice": [
            "operator-owned review wording",
        ],
        "execution_brief_operator_decision": "operator keeps the review wording and acceptance slice",
        "followup_brief_status": "partially_executable",
        "followup_brief_summary": "partially_executable | execution=L2 | review=R1",
        "followup_brief_execution_lane_ids": ["L2"],
        "followup_brief_review_lane_ids": ["R1"],
        "followup_brief_reason": "operator keeps the review slice while execution-only followup may proceed",
        "reentry_rails_summary": "retry=none | followup=partially_executable exec=L2 review=R1",
        "plan": {
            "summary": "review | followup execute evidence lane L2 remains runnable while review wording stays manual in R1",
            "meta": {
                "phase1_role_preset": "review",
                "phase2_team_preset": "review",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L2", "role": "Codex-Reviewer", "kind": "review_execution"},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L2"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Reviewer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {"lane_id": "L2", "role": "Codex-Reviewer", "kind": "review_execution", "outputs": ["review_report"]},
                    ],
                    "review_lanes": [
                        {"lane_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L2"], "outputs": ["review_report"]},
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L2",
                    "role": "Codex-Reviewer",
                    "status": "blocked",
                    "subtask_ids": ["S2"],
                    "touched_files": ["review_evidence/followup_scope.md"],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "blocked",
                    "depends_on": ["L2"],
                    "reason": "operator keeps the review slice; execute only the declared evidence lane",
                    "verdict": "manual_followup",
                    "action": "manual_followup",
                    "touched_files": ["review_report.md"],
                }
            ],
            "summary": {
                "execution": {"blocked": 1},
                "review": {"blocked": 1},
                "review_verdicts": {"manual_followup": 1},
            },
        },
        "exec_critic": {
            "verdict": "manual_followup",
            "action": "manual_followup",
            "reason": "operator keeps the review slice while execution lane L2 can be rerun for followup evidence",
            "manual_followup_execution_lane_ids": ["L2"],
            "manual_followup_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "manual_followup",
            "backend_contract": "review_followup_execute",
            "backend_contract_note": "followup execute is limited to execution lane L2 while review lane R1 stays manual",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O3",
            "task_short_id": "T-301",
        },
        "created_at": now,
        "updated_at": now,
    }


def seed_r2_review_rerun_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(control_root)
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_r2_task(now), "REQ-R2-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O2",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated review rerun live rehearsal",
        "last_request_id": "REQ-R2-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-R2-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "R2",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O2",
        "request_id": "REQ-R2-001",
        "task_ref": "T-201",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "preflight_commands": [
            "/orch status O2",
            "/task T-201",
            "/offdesk review O2",
        ],
        "trigger_command": "/retry T-201 lane L1",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-R2-001",
            "runtime_detail": "/control/runtimes/O2",
        },
    }


def seed_r3_manual_followup_execute_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(control_root)
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_r3_execute_task(now), "REQ-R3-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O3",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated review followup execute live rehearsal",
        "last_request_id": "REQ-R3-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-R3-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "R3-execute",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O3",
        "request_id": "REQ-R3-001",
        "task_ref": "T-301",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "preflight_commands": [
            "/orch status O3",
            "/task T-301",
            "/followup T-301",
            "/offdesk review O3",
        ],
        "trigger_command": "/followup-exec T-301 lane L2",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-R3-001",
            "runtime_detail": "/control/runtimes/O3",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed an isolated live-rehearsal runtime without launching work.")
    parser.add_argument("--scenario", choices=["r2", "r3-execute"], default="r2")
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--run-lock-mode", choices=["open", "test_only"], default="test_only")
    parser.add_argument("--runner-target", choices=["local_tmux"], default="local_tmux")
    parser.add_argument("--local-tmux-slot-limit", type=int, default=1)
    args = parser.parse_args()

    if args.scenario == "r2":
        payload = seed_r2_review_rerun_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "r3-execute":
        payload = seed_r3_manual_followup_execute_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    else:
        raise SystemExit(f"unsupported scenario: {args.scenario}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
