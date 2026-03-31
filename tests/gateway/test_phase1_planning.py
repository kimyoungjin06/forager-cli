#!/usr/bin/env python3
"""Regression tests for Phase1 ensemble planning."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
GW_DIR = ROOT / "scripts" / "gateway"
PLAN_FILE = GW_DIR / "aoe_tg_plan_ensemble.py"
PIPELINE_FILE = GW_DIR / "aoe_tg_plan_pipeline.py"
GW_FILE = GW_DIR / "aoe-telegram-gateway.py"

if str(GW_DIR) not in sys.path:
    sys.path.insert(0, str(GW_DIR))


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ensemble_mod = _load_module(PLAN_FILE, "aoe_tg_plan_ensemble_mod")
pipeline_mod = _load_module(PIPELINE_FILE, "aoe_tg_plan_pipeline_mod")
gw = _load_module(GW_FILE, "aoe_telegram_gateway_mod_phase1")
import aoe_tg_orch_contract as orch_contract_mod
import aoe_tg_request_contract as request_contract_mod
import aoe_tg_request_contract_data as request_contract_data_mod
import aoe_tg_request_contract_review as request_contract_review_mod
import aoe_tg_tf_exec as tf_exec_mod


def test_phase1_planner_prompt_forbids_standalone_review_subtasks() -> None:
    prompt = ensemble_mod._planner_prompt(
        user_prompt="로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.",
        provider="codex",
        workers=["Codex-Dev", "Codex-Reviewer"],
        max_subtasks=4,
        round_no=1,
        total_rounds=3,
        shared_feedback="",
    )

    assert "reviewer/verifier/QA/independent review 자체를 별도 execution subtask로 만들지 마라" in prompt
    assert "Phase2 review lane이 담당하게 하라" in prompt
    assert "실제 실패 경계(entrypoint, caller-visible state, persisted session/token store)" in prompt
    assert "helper 함수 하나만으로 충분하다고 단정하지 말고" in prompt
    assert "single serial lane도 허용된다" in prompt


def test_phase1_critic_prompt_blocks_review_subtasks_inside_execution_plan() -> None:
    prompt = ensemble_mod._critic_prompt(
        user_prompt="로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.",
        provider="codex",
        planner_provider="codex",
        plan={
            "summary": "login fix",
            "subtasks": [
                {"id": "S1", "title": "Implement fix", "goal": "patch handler", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Independent review", "goal": "independent review", "owner_role": "Codex-Reviewer"},
            ],
            "meta": {"worker_roles": ["Codex-Dev", "Codex-Reviewer"]},
        },
        round_no=1,
        total_rounds=3,
    )

    assert "review/approval/QA를 별도 execution subtask로 넣은 계획은 blocker로 지적한다" in prompt
    assert "Phase2 review lane의 acceptance/evidence로 표현되어야 한다" in prompt
    assert "helper 함수 하나만 실제 실패 경계라고 가정하면 blocker로 지적한다" in prompt
    assert "single serial lane 자체만으로 blocker를 만들지 마라" in prompt


def test_phase1_planner_prompt_requires_concrete_review_output_contracts() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    rendered = ensemble_mod._planner_prompt(
        user_prompt=prompt,
        provider="codex",
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=5,
        round_no=1,
        total_rounds=3,
        shared_feedback="",
        request_contract=contract,
    )

    assert "review_output마다 최소 하나의 reviewer-owned subtask" in rendered
    assert "docs/reviews/reviewer_note.md" in rendered
    assert "severity findings, regression risks, test gaps, uncertainties" in rendered


def test_phase1_critic_prompt_blocks_generic_review_verifier_without_contract() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    rendered = ensemble_mod._critic_prompt(
        user_prompt=prompt,
        provider="codex",
        planner_provider="claude",
        plan={
            "summary": "mixed plan",
            "subtasks": [
                {"id": "S1", "title": "Patch login flow", "goal": "clear stale token", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write handoff", "owner_role": "Codex-Writer"},
            ],
            "meta": {
                "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
                "request_contract": contract,
            },
        },
        round_no=1,
        total_rounds=3,
    )

    assert "generic verifier만 있으면 blocker로 지적한다" in rendered
    assert "docs/reviews/reviewer_note.md" in rendered


def test_phase1_ensemble_runs_three_rounds_and_uses_both_providers() -> None:
    prompts: list[str] = []

    def _runner(name: str):
        def _run(prompt: str, timeout_sec: int) -> str:
            prompts.append(f"{name}:{prompt.splitlines()[0]}")
            return '{"summary":"plan from %s","subtasks":[{"id":"S1","title":"Draft","goal":"Write the plan","owner_role":"Codex-Writer","acceptance":["has a concise plan"]}]}' % name
        return _run

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner("codex"), "claude": _runner("claude")},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["phase1_mode"] == "ensemble"
    assert result["phase1_rounds"] == 3
    assert result["plan_review_count"] == 3
    assert result["plan_convergence_status"] == "ready"
    assert len(result["plan_issue_history"]) == 3
    assert [row["review_pass"] for row in result["plan_issue_history"]] == ["contract", "execution", "verification"]
    assert result["phase1_providers"] == ["codex", "claude"]
    assert len(result["plan_replans"]) == 3
    assert result["plan_data"]["subtasks"][0]["owner_role"] == "Codex-Writer"
    assert result["plan_gate_blocked"] is False
    assert any(item.startswith("codex:") for item in prompts)
    assert any(item.startswith("claude:") for item in prompts)


def test_phase1_ensemble_marks_repeated_blocker_as_stalled() -> None:
    def _runner(prompt: str, timeout_sec: int) -> str:
        if "critic이다" in prompt:
            return json.dumps(
                {
                    "approved": False,
                    "issues": ["missing artifact-specific acceptance"],
                    "recommendations": [],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "summary": "plan from codex",
                "subtasks": [
                    {
                        "id": "S1",
                        "title": "Draft",
                        "goal": "Write the plan",
                        "owner_role": "Codex-Writer",
                        "acceptance": ["has a concise plan"],
                    }
                ],
            },
            ensure_ascii=False,
        )

    args = SimpleNamespace(
        plan_phase1_providers="codex",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is True
    assert result["plan_convergence_status"] == "stalled"
    assert result["plan_stalled_reason"] == "missing artifact-specific acceptance"
    assert result["plan_review_count"] == 3
    assert len(result["plan_issue_history"]) == 3
    assert [row["review_pass"] for row in result["plan_issue_history"]] == ["contract", "execution", "verification"]
    assert result["plan_issue_codes"] == ["acceptance_gap"]


def test_phase1_ensemble_launches_round1_planners_in_parallel() -> None:
    start_times: dict[str, float] = {}
    lock = threading.Lock()

    def _runner(name: str):
        def _run(prompt: str, timeout_sec: int) -> str:
            line1 = prompt.splitlines()[0]
            if "TF Phase1 planner" in line1:
                with lock:
                    start_times.setdefault(name, time.monotonic())
                time.sleep(0.25)
                return '{"summary":"plan from %s","subtasks":[{"id":"S1","title":"Draft","goal":"Write the plan","owner_role":"Codex-Writer","acceptance":["has a concise plan"]}]}' % name
            time.sleep(0.25)
            return '{"approved": true, "issues": [], "recommendations": []}'
        return _run

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner("codex"), "claude": _runner("claude")},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert set(start_times) == {"codex", "claude"}
    assert abs(start_times["codex"] - start_times["claude"]) < 0.15


def test_stage_review_prompt_includes_execution_evidence_paths() -> None:
    prompt = tf_exec_mod.stage_review_prompt(
        "원사용자 요청:\nsession_expired를 수정해줘.",
        {
            "request_id": "r_exec_123",
            "reply_messages": [
                {
                    "from": "Codex-Dev",
                    "request_id": "r_exec_123-execution-E1",
                    "body": (
                        "구현 결과는 "
                        "[src/session.js](/tmp/demo/e1/src/session.js#L1), "
                        "[tests/session.test.js](/tmp/demo/e1/tests/session.test.js#L1), "
                        "[work_result](/tmp/demo/e1/work_result#L1)에 남겼다."
                    ),
                },
                {
                    "from": "Codex-Writer",
                    "request_id": "r_exec_123-execution-E2",
                    "body": (
                        "handoff는 "
                        "[operator_handoff.md](/tmp/demo/e2/docs/handoff/operator_handoff.md#L1)에 작성했다."
                    ),
                },
            ],
        },
        {
            "review_lanes": [
                {
                    "lane_id": "R1",
                    "role": "Codex-Reviewer",
                    "kind": "verifier",
                    "depends_on": ["E1", "E2"],
                }
            ]
        },
    )

    assert "Execution request: r_exec_123" in prompt
    assert "The current review workspace may not contain execution changes." in prompt
    assert "/tmp/demo/e1/src/session.js#L1" in prompt
    assert "/tmp/demo/e2/docs/handoff/operator_handoff.md#L1" in prompt


def test_phase1_ensemble_falls_back_to_codex_when_claude_is_rate_limited() -> None:
    calls: list[str] = []

    def _codex(prompt: str, timeout_sec: int) -> str:
        calls.append("codex")
        if "critic이다" in prompt:
            return '{"approved": true, "issues": [], "recommendations": []}'
        return '{"summary":"plan from codex","subtasks":[{"id":"S1","title":"Draft","goal":"Write the plan","owner_role":"Codex-Writer","acceptance":["has a concise plan"]}]}'

    def _claude(prompt: str, timeout_sec: int) -> str:
        calls.append("claude")
        raise RuntimeError("429 rate limit exceeded; retry after 60s")

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _codex, "claude": _claude},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is False
    assert result["plan_data"]["summary"] == "plan from codex"
    assert calls.count("claude") >= 1
    assert calls.count("codex") >= 2
    assert result["rate_limit"]["mode"] == "degraded"
    assert result["rate_limit"]["degraded_by"] == ["claude_rate_limit->codex"]


def test_phase1_ensemble_falls_back_to_claude_when_codex_is_rate_limited() -> None:
    calls: list[str] = []

    def _codex(prompt: str, timeout_sec: int) -> str:
        calls.append("codex")
        raise RuntimeError("429 rate limit exceeded; retry after 120s")

    def _claude(prompt: str, timeout_sec: int) -> str:
        calls.append("claude")
        if "critic이다" in prompt:
            return '{"approved": true, "issues": [], "recommendations": []}'
        return '{"summary":"plan from claude","subtasks":[{"id":"S1","title":"Draft","goal":"Write the plan","owner_role":"Codex-Writer","acceptance":["has a concise plan"]}]}'

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _codex, "claude": _claude},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is False
    assert result["plan_data"]["summary"] == "plan from claude"
    assert calls.count("codex") >= 1
    assert calls.count("claude") >= 2
    assert result["rate_limit"]["mode"] == "degraded"
    assert result["rate_limit"]["degraded_by"] == ["codex_rate_limit->claude"]


def test_phase1_ensemble_blocks_when_all_providers_are_rate_limited() -> None:
    def _limited(prompt: str, timeout_sec: int) -> str:
        raise RuntimeError("429 rate limit exceeded; retry after 180s")

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _limited, "claude": _limited},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is True
    assert result["plan_data"] is None
    assert result["rate_limit"]["mode"] == "blocked"
    assert sorted(result["rate_limit"]["limited_providers"]) == ["claude", "codex"]
    assert result["rate_limit"]["retry_after_sec"] == 180
    assert "retry_at" in result["rate_limit"]
    assert str(result["rate_limit"]["retry_at"]).strip()


def test_phase1_ensemble_uses_proactive_cooldown_fallback_from_provider_memory(tmp_path: Path) -> None:
    team_dir = tmp_path / ".aoe-team"
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "provider_capacity.json").write_text(
        json.dumps(
            {
                "providers": {
                    "claude": {
                        "blocked_count": 1,
                        "project_count": 1,
                        "cooldown_level": "cooldown",
                        "next_retry_at": "2099-03-14T03:10:00+09:00",
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[str] = []

    def _codex(prompt: str, timeout_sec: int) -> str:
        calls.append("codex")
        if "critic이다" in prompt:
            return '{"approved": true, "issues": [], "recommendations": []}'
        return '{"summary":"plan from codex","subtasks":[{"id":"S1","title":"Draft","goal":"Write the plan","owner_role":"Codex-Writer","acceptance":["has a concise plan"]}]}'

    def _claude(prompt: str, timeout_sec: int) -> str:
        calls.append("claude")
        raise AssertionError("claude should not be called while cooldown memory is active")

    args = SimpleNamespace(
        team_dir=team_dir,
        plan_phase1_providers="claude,codex",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="Prepare a stable execution plan",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _codex, "claude": _claude},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is False
    assert result["plan_data"]["summary"] == "plan from codex"
    assert calls.count("claude") == 0
    assert calls.count("codex") >= 2
    assert result["rate_limit"]["degraded_by"] == ["claude_rate_limit->codex"]


def test_phase1_ensemble_policy_approval_issue_does_not_block() -> None:
    def _runner(name: str):
        def _run(prompt: str, timeout_sec: int) -> str:
            if "TF Phase1 critic" in prompt:
                return json.dumps(
                    {
                        "approved": False,
                        "issues": ["단일 DRI/최종 승인자가 Task Team 내부 역할로 고정되어 있습니다."],
                        "recommendations": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "summary": f"plan from {name}",
                    "subtasks": [
                        {
                            "id": "S1",
                            "title": "Draft",
                            "goal": "Write the plan",
                            "owner_role": "Codex-Writer",
                            "acceptance": ["has a concise plan"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        return _run

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="오프데스크 검토용 계획을 세워라",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner("codex"), "claude": _runner("claude")},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is False
    assert result["plan_critic"]["issues"] == []
    assert any("approval_policy_note:" in row for row in result["plan_critic"]["recommendations"])


def test_phase1_ensemble_none_approval_issue_does_not_block() -> None:
    def _runner(name: str):
        def _run(prompt: str, timeout_sec: int) -> str:
            if "TF Phase1 critic" in prompt:
                return json.dumps(
                    {
                        "approved": False,
                        "issues": ["최종 승인자/approver가 지정되지 않았습니다."],
                        "recommendations": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "summary": f"plan from {name}",
                    "subtasks": [
                        {
                            "id": "S1",
                            "title": "Draft",
                            "goal": "Write the plan",
                            "owner_role": "Codex-Writer",
                            "acceptance": ["has a concise plan"],
                        }
                    ],
                    "meta": {"approval_mode": "none"},
                },
                ensure_ascii=False,
            )
        return _run

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="검토 메모를 정리하되 승인 절차는 생략해도 된다",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner("codex"), "claude": _runner("claude")},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is False
    assert result["plan_critic"]["issues"] == []
    assert any("approval_not_required_note:" in row for row in result["plan_critic"]["recommendations"])


def test_phase1_ensemble_confirm_approval_issue_still_blocks() -> None:
    def _runner(name: str):
        def _run(prompt: str, timeout_sec: int) -> str:
            if "TF Phase1 critic" in prompt:
                return json.dumps(
                    {
                        "approved": False,
                        "issues": ["최종 승인자/DRI가 Task Team 내부에 명시되지 않았습니다."],
                        "recommendations": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "summary": f"plan from {name}",
                    "subtasks": [
                        {
                            "id": "S1",
                            "title": "Draft",
                            "goal": "Write the plan",
                            "owner_role": "Codex-Writer",
                            "acceptance": ["has a concise plan"],
                        }
                    ],
                    "meta": {"approval_mode": "confirm"},
                },
                ensure_ascii=False,
            )
        return _run

    args = SimpleNamespace(
        plan_phase1_providers="codex,claude",
        plan_phase1_rounds=3,
        plan_max_subtasks=3,
        orch_command_timeout_sec=120,
        plan_block_on_critic=True,
    )

    result = ensemble_mod.run_phase1_ensemble_planning(
        args=args,
        user_prompt="최종 승인 확인이 필요한 실행 계획을 세워라",
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        parse_json_object_from_text=gw.parse_json_object_from_text,
        run_provider_execs={"codex": _runner("codex"), "claude": _runner("claude")},
        plan_roles_from_subtasks=gw.plan_roles_from_subtasks,
        report_progress=None,
    )

    assert result["plan_gate_blocked"] is True
    assert result["plan_critic"]["issues"] == ["최종 승인자/DRI가 Task Team 내부에 명시되지 않았습니다."]


def test_resolve_dispatch_mode_defaults_to_tf_dispatch_when_not_forced_direct() -> None:
    result = pipeline_mod.resolve_dispatch_mode_and_roles(
        run_force_mode=None,
        run_roles_override=None,
        project_roles_csv="",
        auto_dispatch_enabled=False,
        prompt="로그인 버그를 수정하고 회귀 리스크를 검토해줘",
        choose_auto_dispatch_roles=lambda *args, **kwargs: [],
        available_roles=["Codex-Dev", "Codex-Reviewer"],
        team_dir=None,
    )

    assert result.dispatch_mode is True
    assert result.dispatch_roles == "Codex-Reviewer"


def test_resolve_dispatch_mode_uses_auto_roles_for_forced_dispatch_even_when_auto_dispatch_disabled() -> None:
    result = pipeline_mod.resolve_dispatch_mode_and_roles(
        run_force_mode="dispatch",
        run_roles_override=None,
        project_roles_csv="",
        auto_dispatch_enabled=False,
        prompt="로그인 버그를 수정하고 회귀 리스크를 검토해줘",
        choose_auto_dispatch_roles=lambda *args, **kwargs: ["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
        available_roles=["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
        team_dir=None,
    )

    assert result.dispatch_mode is True
    assert result.dispatch_roles == "Codex-Dev,Codex-Reviewer,Claude-Reviewer"


def test_compute_dispatch_plan_uses_phase1_plan_roles_for_phase2_execution() -> None:
    args = SimpleNamespace(
        task_planning=True,
        dry_run=False,
        plan_phase1_ensemble=True,
        plan_phase1_rounds=3,
        plan_phase1_providers="codex,claude",
        plan_phase1_min_providers=2,
        plan_max_subtasks=4,
        plan_auto_replan=True,
        plan_replan_attempts=1,
        plan_block_on_critic=True,
    )
    phases: list[dict] = []

    meta = pipeline_mod.compute_dispatch_plan(
        args=args,
        p_args=args,
        prompt="계획 수립 후 병렬 실행팀을 꾸려라",
        dispatch_mode=True,
        run_control_mode="normal",
        run_source_task=None,
        selected_roles=["Codex-Reviewer"],
        available_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        available_worker_roles=lambda roles: roles,
        normalize_task_plan_payload=lambda parsed, **_kwargs: parsed,
        build_task_execution_plan=lambda *_args, **_kwargs: {},
        critique_task_execution_plan=lambda *_args, **_kwargs: {"approved": True, "issues": [], "recommendations": []},
        critic_has_blockers=lambda critic: (not bool(critic.get("approved", True))) or bool(critic.get("issues") or []),
        repair_task_execution_plan=lambda *_args, **_kwargs: {},
        plan_roles_from_subtasks=lambda plan: [row["owner_role"] for row in (plan.get("subtasks") or [])] if isinstance(plan, dict) else [],
        phase1_ensemble_planning=lambda *_args, **kwargs: {
            "plan_data": {
                "summary": "phase1 plan",
                "subtasks": [
                    {"id": "S1", "title": "implement", "goal": "do implementation", "owner_role": "Codex-Dev", "acceptance": ["code complete"]},
                    {"id": "S2", "title": "write", "goal": "document plan", "owner_role": "Codex-Writer", "acceptance": ["report complete"]},
                ],
            },
            "plan_critic": {"approved": True, "issues": [], "recommendations": []},
            "plan_roles": ["Codex-Dev", "Codex-Writer"],
            "plan_replans": [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}],
            "plan_error": "",
            "plan_gate_blocked": False,
            "plan_gate_reason": "",
            "phase1_mode": "ensemble",
            "phase1_rounds": 3,
            "phase1_providers": ["codex", "claude"],
        },
        report_progress=lambda **kwargs: phases.append(kwargs),
    )

    assert meta.selected_roles == ["Codex-Dev", "Codex-Writer"]
    assert meta.phase1_mode == "ensemble"
    assert meta.phase1_rounds == 3
    assert meta.phase1_providers == ["codex", "claude"]
    assert phases[-1]["phase"] == "ready"


def test_compute_dispatch_plan_policy_approval_issue_does_not_trigger_plan_gate() -> None:
    args = SimpleNamespace(
        task_planning=True,
        dry_run=False,
        plan_phase1_ensemble=False,
        plan_max_subtasks=3,
        plan_auto_replan=True,
        plan_replan_attempts=1,
        plan_block_on_critic=True,
    )

    base_plan = gw.normalize_task_plan_payload(
        {
            "summary": "policy review plan",
            "subtasks": [
                {"id": "S1", "title": "Review", "goal": "review work", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt="오프데스크 검토용 계획을 세워라",
        workers=["Codex-Writer", "Codex-Reviewer"],
        max_subtasks=3,
    )

    meta = pipeline_mod.compute_dispatch_plan(
        args=args,
        p_args=args,
        prompt="오프데스크 검토용 계획을 세워라",
        dispatch_mode=True,
        run_control_mode="normal",
        run_source_task=None,
        selected_roles=["Codex-Reviewer"],
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        available_worker_roles=lambda roles: roles,
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        build_task_execution_plan=lambda *_args, **_kwargs: dict(base_plan),
        critique_task_execution_plan=lambda *_args, **_kwargs: {
            "approved": False,
            "issues": ["최종 승인자/DRI가 Task Team 내부에 명시되지 않았습니다."],
            "recommendations": [],
        },
        critic_has_blockers=lambda critic: (not bool(critic.get("approved", True))) or bool(critic.get("issues") or []),
        repair_task_execution_plan=lambda *_args, **_kwargs: dict(base_plan),
        plan_roles_from_subtasks=lambda plan: [row["owner_role"] for row in (plan.get("subtasks") or [])] if isinstance(plan, dict) else [],
        phase1_ensemble_planning=None,
        report_progress=None,
    )

    assert meta.plan_gate_blocked is False
    assert meta.plan_critic["issues"] == []
    assert any("approval_policy_note:" in row for row in meta.plan_critic["recommendations"])


def test_compute_dispatch_plan_confirm_approval_issue_still_triggers_plan_gate() -> None:
    args = SimpleNamespace(
        task_planning=True,
        dry_run=False,
        plan_phase1_ensemble=False,
        plan_max_subtasks=3,
        plan_auto_replan=True,
        plan_replan_attempts=1,
        plan_block_on_critic=True,
    )

    base_plan = gw.normalize_task_plan_payload(
        {
            "summary": "confirm review plan",
            "subtasks": [
                {"id": "S1", "title": "Review", "goal": "review work", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
            "meta": {"approval_mode": "confirm"},
        },
        user_prompt="최종 승인 확인이 필요한 계획을 세워라",
        workers=["Codex-Writer", "Codex-Reviewer"],
        max_subtasks=3,
    )

    meta = pipeline_mod.compute_dispatch_plan(
        args=args,
        p_args=args,
        prompt="최종 승인 확인이 필요한 계획을 세워라",
        dispatch_mode=True,
        run_control_mode="normal",
        run_source_task=None,
        selected_roles=["Codex-Reviewer"],
        available_roles=["Codex-Writer", "Codex-Reviewer"],
        available_worker_roles=lambda roles: roles,
        normalize_task_plan_payload=gw.normalize_task_plan_payload,
        build_task_execution_plan=lambda *_args, **_kwargs: dict(base_plan),
        critique_task_execution_plan=lambda *_args, **_kwargs: {
            "approved": False,
            "issues": ["최종 승인자/DRI가 Task Team 내부에 명시되지 않았습니다."],
            "recommendations": [],
        },
        critic_has_blockers=lambda critic: (not bool(critic.get("approved", True))) or bool(critic.get("issues") or []),
        repair_task_execution_plan=lambda *_args, **_kwargs: dict(base_plan),
        plan_roles_from_subtasks=lambda plan: [row["owner_role"] for row in (plan.get("subtasks") or [])] if isinstance(plan, dict) else [],
        phase1_ensemble_planning=None,
        report_progress=None,
    )

    assert meta.plan_gate_blocked is True
    assert meta.plan_gate_reason == "최종 승인자/DRI가 Task Team 내부에 명시되지 않았습니다."


def test_normalize_task_plan_payload_derives_phase2_team_spec() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "parallel execution",
            "subtasks": [
                {"id": "S1", "title": "Implement", "goal": "build feature", "owner_role": "Codex-Dev", "acceptance": ["done"]},
                {"id": "S2", "title": "Document", "goal": "write handoff", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt="계획 수립 후 병렬 실행팀을 꾸려라",
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=4,
    )

    spec = plan["meta"]["phase2_team_spec"]
    execution_plan = plan["meta"]["phase2_execution_plan"]
    assert spec["execution_mode"] == "parallel"
    assert [row["role"] for row in spec["execution_groups"]] == ["Codex-Dev", "Codex-Writer"]
    assert [row["role"] for row in spec["review_groups"]] == ["Codex-Reviewer"]
    assert execution_plan["execution_mode"] == "parallel"
    assert [row["role"] for row in execution_plan["execution_lanes"]] == ["Codex-Dev", "Codex-Writer"]
    assert [row["role"] for row in execution_plan["review_lanes"]] == ["Codex-Reviewer"]
    assert execution_plan["parallel_workers"] is True
    assert execution_plan["readonly"] is False
    assert plan["meta"]["phase1_role_preset"] == "mixed"
    assert plan["meta"]["phase2_team_preset"] == "mixed"


def test_normalize_task_plan_payload_preserves_explicit_role_preset_overrides() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "reporting",
            "subtasks": [
                {"id": "S1", "title": "Draft", "goal": "write report", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt="결과를 요약하고 보고서를 작성해라",
        workers=["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
        meta_overrides={
            "worker_roles": ["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "writer",
            "phase2_team_preset": "writer",
        },
    )

    assert plan["meta"]["worker_roles"] == ["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"]
    assert plan["meta"]["phase1_role_preset"] == "writer"
    assert plan["meta"]["phase2_team_preset"] == "writer"
    spec = plan["meta"]["phase2_team_spec"]
    execution_plan = plan["meta"]["phase2_execution_plan"]
    assert [row["role"] for row in spec["execution_groups"]] == ["Codex-Writer", "Claude-Writer"]
    assert [row["role"] for row in spec["review_groups"]] == ["Codex-Reviewer", "Claude-Reviewer"]
    assert [row["role"] for row in execution_plan["execution_lanes"]] == ["Codex-Writer", "Claude-Writer"]


def test_normalize_task_plan_payload_preserves_explicit_readonly_override() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "reporting",
            "subtasks": [
                {"id": "S1", "title": "Draft", "goal": "write report", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt="결과를 요약하고 보고서를 작성해라",
        workers=["Codex-Writer", "Codex-Reviewer"],
        max_subtasks=3,
        meta_overrides={
            "worker_roles": ["Codex-Writer", "Codex-Reviewer"],
            "phase1_role_preset": "writer",
            "phase2_team_preset": "writer",
            "readonly": True,
        },
    )

    assert plan["meta"]["readonly"] is True
    assert plan["meta"]["phase2_execution_plan"]["readonly"] is True


def test_build_auth_session_prompt_adds_acceptance_floor() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "login fix",
            "subtasks": [
                {
                    "id": "S1",
                    "title": "Implement fix",
                    "goal": "patch login failure session handling",
                    "owner_role": "Codex-Dev",
                    "acceptance": ["회귀 테스트 결과를 남긴다."],
                },
            ],
        },
        user_prompt="로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.",
        workers=["Codex-Dev", "Codex-Reviewer"],
        max_subtasks=3,
    )

    acceptance = plan["subtasks"][0]["acceptance"]

    assert plan["meta"]["phase2_team_preset"] == "build"
    assert any("Caller-visible or persisted auth/session state changes" in item for item in acceptance)
    assert any("stored token/session invalidation" in item for item in acceptance)
    assert any("existing auth/session state" in item for item in acceptance)


def test_data_schema_prompt_adds_acceptance_floor_and_prioritizes_floor() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "normalize csv",
            "subtasks": [
                {
                    "id": "S2",
                    "title": "Write schema report",
                    "goal": "capture schema_report.json for normalized monthly csv",
                    "owner_role": "DataEngineer",
                    "acceptance": [
                        "schema_report.json columns month, region, orders, revenue, notes with inferred_type/type_rule/null_count/observed_non_null_count and sample linkage are recorded in one long sentence that would otherwise dominate acceptance",
                        "null summary exists",
                        "sample rows exist",
                    ],
                },
            ],
        },
        user_prompt="월별 집계 CSV를 정규화하고 스키마 점검, null 요약, 샘플 5행을 만들어줘.",
        workers=["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
    )

    acceptance = plan["subtasks"][0]["acceptance"]

    assert plan["meta"]["phase2_team_preset"] == "data"
    assert len(acceptance) == 3
    assert any("every output column with inferred_type and type_rule" in item for item in acceptance)
    assert any("null_count and observed_non_null_count for every output column" in item for item in acceptance)
    assert any("Sample evidence is taken from the transformed output" in item for item in acceptance)


def test_data_transform_prompt_adds_transform_policy_floor() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "normalize monthly csv",
            "subtasks": [
                {
                    "id": "S1",
                    "title": "Normalize month column",
                    "goal": "normalize month values in the transformed csv",
                    "owner_role": "DataEngineer",
                    "acceptance": ["normalized CSV is produced"],
                },
            ],
        },
        user_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다."
        ),
        workers=["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
    )

    acceptance = plan["subtasks"][0]["acceptance"]

    assert plan["meta"]["phase2_team_preset"] == "data"
    assert len(acceptance) == 3
    assert any("source CSV path and target month column explicitly" in item for item in acceptance)
    assert any("accepted month input formats" in item for item in acceptance)
    assert any("must stay anomalies" in item for item in acceptance)
    assert any("invalid, unparseable, or out-of-range month values" in item for item in acceptance)


def test_data_request_contract_extracts_structured_fields() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
            "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    assert contract["contract_type"] == "data"
    assert contract["preset"] == "data"
    assert contract["status"] == "complete"
    assert contract["fields"]["source_path"] == "data/monthly_raw.csv"
    assert contract["fields"]["target_column"] == "month"
    assert contract["fields"]["accepted_input_formats"] == ["YYYY/MM", "YYYY-MM", "YYYY.MM"]
    assert contract["fields"]["normalize_to"] == "YYYY-MM"
    assert contract["fields"]["invalid_value_policy"]["preserve_row"] is True
    assert contract["fields"]["invalid_value_policy"]["preserve_original_value"] is True
    assert contract["fields"]["invalid_value_policy"]["record_anomaly"] is True
    assert contract["fields"]["normalized_output_policy"]["row_order_policy"] == "preserve-source-data-row-order"
    assert contract["fields"]["normalized_output_policy"]["header_policy"] == "preserve-source-header-order"
    assert contract["fields"]["month_bucket_policy"]["valid_patterns"] == ["YYYY/MM", "YYYY-MM", "YYYY.MM"]
    assert contract["fields"]["month_bucket_policy"]["valid_year_rule"] == "4-digit-year"
    assert contract["fields"]["month_bucket_policy"]["valid_month_rule"] == "01-12"
    assert contract["fields"]["month_bucket_policy"]["trim_before_match"] is True
    assert contract["fields"]["month_bucket_policy"]["null_like_match"] == "trim+casefold-exact"
    assert contract["fields"]["month_bucket_policy"]["null_like_tokens"] == ["null", "nan"]
    assert contract["fields"]["month_bucket_policy"]["allowed_separators"] == ["/", "-", "."]
    assert contract["fields"]["month_bucket_policy"]["year_width_mismatch_bucket"] == "bad-year"
    assert contract["fields"]["month_bucket_policy"]["separator_mismatch_bucket"] == "malformed-value"
    assert contract["fields"]["month_bucket_policy"]["token_count_mismatch_bucket"] == "malformed-value"
    assert contract["fields"]["month_bucket_policy"]["one_digit_month_bucket"] == "one-digit-month"
    assert contract["fields"]["month_bucket_policy"]["match_order"][0] == "empty-string"
    assert "literal-null" in contract["fields"]["month_bucket_policy"]["anomaly_buckets"]
    assert contract["fields"]["schema_inference_policy"]["allowed_inferred_types"] == ["string", "integer", "number", "boolean"]
    assert contract["fields"]["schema_inference_policy"]["precedence_order"] == ["integer", "number", "boolean", "string"]
    assert contract["fields"]["schema_inference_policy"]["mixed_type_resolution"] == "string"
    assert contract["fields"]["schema_null_count_policy"]["null_like_buckets"] == ["empty-string", "whitespace-only", "literal-null", "literal-nan"]
    assert "malformed-value" in contract["fields"]["schema_null_count_policy"]["target_invalid_buckets_excluded"]
    assert contract["fields"]["schema_anomaly_evidence_policy"]["field_path"] == "month_anomalies[]"
    assert contract["fields"]["schema_anomaly_evidence_policy"]["required_fields"] == ["bucket", "count", "examples[]"]
    assert contract["fields"]["sample_output_policy"]["selection_policy"] == "head"
    assert contract["fields"]["sample_output_policy"]["sample_size"] == "5"
    assert contract["fields"]["sample_output_policy"]["row_unit"] == "data-row"
    assert contract["fields"]["sample_output_policy"]["order_basis"] == "transformed-output-order"
    assert contract["fields"]["sample_output_policy"]["shortfall_policy"] == "emit-all-available-and-note-shortfall"
    assert contract["fields"]["sample_output_policy"]["shortfall_encoding"] == "append-note-row"
    assert contract["fields"]["sample_output_policy"]["shortfall_note_position"] == "after-emitted-rows"
    assert contract["fields"]["sample_output_policy"]["shortfall_note_marker_column"] == "__aoe_sample_note__"
    assert contract["fields"]["sample_output_policy"]["shortfall_note_marker_value"] == "sample_shortfall"
    assert contract["fields"]["sample_output_policy"]["shortfall_note_fields"] == [
        "requested_rows",
        "emitted_rows",
        "missing_rows",
    ]
    assert contract["artifact_contracts"]["schema_report"]["path"] == "schema_report.json"
    assert contract["artifact_contracts"]["schema_report"]["inference_policy"]["type_rule_source"] == "observable-transformed-values"
    assert "month_anomalies[].bucket" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert "month_anomalies[].examples[]" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert contract["artifact_contracts"]["null_summary"]["path"] == "null_summary.md"
    assert contract["artifact_contracts"]["sample_output"]["path"] == "sample_5.csv"
    assert "first_5_data_rows" in contract["artifact_contracts"]["sample_output"]["required_fields"]
    assert "shortfall_note_when_needed" in contract["artifact_contracts"]["sample_output"]["required_fields"]


def test_data_request_contract_extracts_quality_gate_policy_for_rerun_requests() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "orders는 integer, revenue는 number 스키마를 유지해야 한다. "
            "schema drift가 남거나 null-heavy output이면 done으로 닫지 말고 rerun으로 남겨라. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
            "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    assert contract["fields"]["quality_gate_policy"]["branch_on_failure"] == "rerun"
    assert contract["fields"]["quality_gate_policy"]["schema_drift_gate"] is True
    assert contract["fields"]["quality_gate_policy"]["null_heavy_gate"] is True
    assert contract["fields"]["quality_gate_policy"]["done_forbidden_on_failure"] is True
    assert contract["fields"]["quality_gate_policy"]["evidence_artifacts"] == ["schema_report.json", "null_summary.md"]
    assert contract["fields"]["quality_gate_policy"]["null_heavy_scope_columns"] == ["orders", "revenue"]
    assert contract["fields"]["quality_gate_policy"]["null_heavy_count_basis"] == "null-or-invalid-row-count"
    assert "schema_drift_status" in contract["fields"]["quality_gate_policy"]["required_decisions"]
    assert "null_heavy_status" in contract["fields"]["quality_gate_policy"]["required_decisions"]
    assert "done_forbidden_when_quality_gate_fails" in contract["fields"]["quality_gate_policy"]["required_decisions"]
    assert contract["fields"]["schema_column_expectations"]["orders"] == "integer"
    assert contract["fields"]["schema_column_expectations"]["revenue"] == "number"
    assert contract["fields"]["schema_value_quality_policy"]["scope_columns"] == ["orders", "revenue"]
    assert contract["fields"]["schema_value_quality_policy"]["null_or_invalid_count_field"] == "null_or_invalid_count"
    assert contract["fields"]["schema_value_quality_policy"]["numeric_parse_failure_counts_as_invalid"] is True
    assert "schema_drift.status" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert "schema_drift.rerun_required" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert "columns[].expected_type" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert "schema_drift.violations[]" in contract["artifact_contracts"]["schema_report"]["required_fields"]
    assert "null_heavy.status" in contract["artifact_contracts"]["null_summary"]["required_fields"]
    assert "null_heavy.rerun_required" in contract["artifact_contracts"]["null_summary"]["required_fields"]


def test_data_request_contract_treats_original_preserve_phrase_as_row_and_value_policy() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 csv는 data/monthly_raw.csv이고 대상 컬럼은 month야. "
            "허용 포맷 YYYY/MM, YYYY-MM, YYYY.MM만 YYYY-MM으로 정규화하고 "
            "invalid/null/empty/out-of-range 값은 원본 유지하면서 normalized.csv, "
            "schema_report.json, null_summary.md, sample_5.csv를 만들어줘."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    assert contract["contract_type"] == "data"
    assert contract["fields"]["invalid_value_policy"]["preserve_row"] is True
    assert contract["fields"]["invalid_value_policy"]["preserve_original_value"] is True
    assert contract["fields"]["invalid_value_policy"]["record_anomaly"] is True


def test_data_request_contract_adds_artifact_specific_acceptance_floor() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
            "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "normalize monthly csv",
            "subtasks": [
                {
                    "id": "S1",
                    "title": "Normalize month column",
                    "goal": "transform month values in the csv and write anomaly summary",
                    "owner_role": "DataEngineer",
                    "acceptance": ["normalized CSV is produced"],
                },
                {
                    "id": "S2",
                    "title": "Write schema report",
                    "goal": "capture schema evidence for the transformed output",
                    "owner_role": "DataEngineer",
                    "acceptance": ["schema evidence exists"],
                },
                {
                    "id": "S3",
                    "title": "Write null summary",
                    "goal": "summarize null and anomaly handling",
                    "owner_role": "DataEngineer",
                    "acceptance": ["null summary exists"],
                },
                {
                    "id": "S4",
                    "title": "Export sample rows",
                    "goal": "leave a sample 5 rows csv for review",
                    "owner_role": "DataEngineer",
                    "acceptance": ["sample exists"],
                },
            ],
        },
        user_prompt="월별 집계 CSV를 정규화하고 스키마 체크, null 요약, 샘플 5행을 함께 남겨줘.",
        workers=["DataEngineer", "Codex-Reviewer"],
        max_subtasks=4,
        meta_overrides={
            "request_contract": contract,
            "phase1_role_preset": "data",
            "phase2_team_preset": "data",
        },
    )

    acceptance_by_id = {row["id"]: row["acceptance"] for row in plan["subtasks"]}

    assert any("source `data/monthly_raw.csv`" in item for item in acceptance_by_id["S1"])
    assert any("normalized.csv" in item for item in acceptance_by_id["S1"])
    assert any("row/header order stay unchanged" in item for item in acceptance_by_id["S1"])
    assert any("request-contract valid month formats YYYY/MM, YYYY-MM, YYYY.MM" in item for item in acceptance_by_id["S1"])
    assert any("all other month buckets stay anomalies" in item for item in acceptance_by_id["S1"])
    assert any("Month bucket policy:" in item for item in acceptance_by_id["S1"])
    assert any("trim-before-match only for classification" in item for item in acceptance_by_id["S1"])
    assert any("null-like=null/nan via trim+casefold-exact" in item for item in acceptance_by_id["S1"])
    assert any("bad-year=bad-year" in item for item in acceptance_by_id["S1"])
    assert any("one-digit-month=one-digit-month" in item for item in acceptance_by_id["S1"])
    assert any("keep original row + month bytes" in item for item in acceptance_by_id["S1"])
    assert any("row count + row/header order stay unchanged" in item for item in acceptance_by_id["S1"])
    assert any("non-target columns stay exact" in item for item in acceptance_by_id["S1"])
    assert any("schema_report.json" in item for item in acceptance_by_id["S2"])
    assert any("every transformed output column" in item for item in acceptance_by_id["S2"])
    assert any("canonical anomaly evidence" in item for item in acceptance_by_id["S2"])
    assert any("Policies:" in item for item in acceptance_by_id["S2"])
    assert not any("null_summary.md" in item for item in acceptance_by_id["S2"])
    assert any("`schema_anomaly_evidence_policy`" in item for item in acceptance_by_id["S2"])
    assert any("month_anomalies[]" in item for item in acceptance_by_id["S2"])
    assert any("`schema_null_count_policy`" in item for item in acceptance_by_id["S2"])
    assert any("exclude month invalids" in item for item in acceptance_by_id["S2"])
    assert any("`schema_inference_policy`" in item for item in acceptance_by_id["S2"])
    assert any("integer>number>boolean>string" in item for item in acceptance_by_id["S2"])
    assert any("null_summary.md" in item for item in acceptance_by_id["S3"])
    assert any("canonical anomaly buckets/count/examples" in item for item in acceptance_by_id["S3"])
    assert any("month_anomalies[]" in item for item in acceptance_by_id["S3"])
    assert not any("Schema evidence covers every transformed output column" in item for item in acceptance_by_id["S3"])
    assert any("sample_5.csv" in item for item in acceptance_by_id["S4"])
    assert any("`sample_output_policy`" in item for item in acceptance_by_id["S4"])
    assert any("head 5 data-rows by transformed-output-order" in item for item in acceptance_by_id["S4"])
    assert any("shortfall=append-note-row(after-emitted-rows" in item for item in acceptance_by_id["S4"])
    assert any("__aoe_sample_note__=sample_shortfall" in item for item in acceptance_by_id["S4"])
    assert any("req/emitted/missing" in item for item in acceptance_by_id["S4"])


def test_data_request_contract_combined_evidence_task_gets_file_specific_acceptance_floor() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
            "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    floor = request_contract_data_mod.data_request_contract_acceptance_floor(
        request_contract=contract,
        title="Evidence outputs",
        goal="Produce null_summary.md, schema_report.json, and sample_5.csv from the transformed output",
    )

    assert len(floor) == 3
    assert any("null_summary.md" in item for item in floor)
    assert any("schema_report.json" in item for item in floor)
    assert any("request-contract `month_bucket_policy`" in item for item in floor)
    assert any("request-contract `schema_inference_policy`" in item for item in floor)
    assert any("sample_5.csv" in item for item in floor)


def test_data_request_contract_quality_gate_floor_marks_rerun_evidence() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "orders는 integer, revenue는 number 스키마를 유지해야 한다. "
            "schema drift가 남거나 null-heavy output이면 done으로 닫지 말고 rerun으로 남겨라. "
            "parse 불가하거나 범위를 벗어난 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
            "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    schema_floor = request_contract_data_mod.data_request_contract_acceptance_floor(
        request_contract=contract,
        title="Write schema report",
        goal="capture schema_report.json for the transformed output",
    )
    null_floor = request_contract_data_mod.data_request_contract_acceptance_floor(
        request_contract=contract,
        title="Write null summary",
        goal="summarize null_summary.md for the transformed output",
    )

    assert any("schema drift requires rerun instead of `done`" in item for item in schema_floor)
    assert any("`quality_gate_policy`" in item for item in schema_floor)
    assert any("`schema_column_expectations` orders=integer, revenue=number" in item for item in schema_floor)
    assert any("marks whether output is null-heavy" in item for item in null_floor)
    assert any("rerun-required decision instead of claiming `done`" in item for item in null_floor)
    assert any("`quality_gate_policy`" in item for item in null_floor)
    assert any("`schema_value_quality_policy` orders,revenue -> null_or_invalid_count via trim-empty/null-like/non-numeric" in item for item in null_floor)


def test_data_request_contract_quality_gate_extracts_explicit_null_heavy_threshold() -> None:
    contract = request_contract_mod.build_request_contract(
        source_prompt=(
            "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
            "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
            "orders는 integer, revenue는 number 스키마를 유지해야 한다. "
            "orders 또는 revenue에서 null 또는 비수치 값이 2행 이상이면 null-heavy=true로 판정하고 done으로 닫지 말고 rerun으로 남겨라. "
            "schema_report.json에는 orders/revenue의 expected_type, observed_inferred_type, schema_drift, rerun_required, violations를 남기고, "
            "null_summary.md에는 affected_columns, null_or_invalid_count, null_heavy, rerun_required, reason을 남겨라."
        ),
        selected_roles=["DataEngineer", "Codex-Reviewer"],
        project_key="demo-data",
    )

    assert contract["fields"]["quality_gate_policy"]["null_heavy_min_rows_per_column"] == "2"
    assert contract["fields"]["quality_gate_policy"]["null_heavy_comparison"] == ">="
    assert contract["fields"]["quality_gate_policy"]["null_heavy_scope_columns"] == ["orders", "revenue"]
    assert contract["fields"]["schema_value_quality_policy"]["scope_columns"] == ["orders", "revenue"]

    null_floor = request_contract_data_mod.data_request_contract_acceptance_floor(
        request_contract=contract,
        title="Write null summary",
        goal="summarize null_summary.md for the transformed output",
    )
    assert any("threshold orders,revenue >= 2 by null-or-invalid-row-count" in item for item in null_floor)
    assert any("`schema_value_quality_policy` orders,revenue -> null_or_invalid_count via trim-empty/null-like/non-numeric" in item for item in null_floor)


def test_review_risk_prompt_prefers_review_preset_over_build_context_words() -> None:
    prompt = (
        "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘. "
        "변경 파일과 테스트 공백, 확인이 필요한 불확실성을 명시하고 review 결과물만 남겨라."
    )

    preset = request_contract_mod.resolve_request_contract_preset(
        source_prompt=prompt,
        selected_roles=[],
    )

    assert preset == "review"


def test_review_request_contract_extracts_diff_range_policy() -> None:
    prompt = (
        "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘. "
        "변경 파일과 테스트 공백, 확인이 필요한 불확실성을 명시하고 review 결과물만 남겨라."
    )

    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Reviewer", "Claude-Reviewer"],
        project_key="demo-login-build",
    )

    assert contract["contract_type"] == "review"
    assert contract["preset"] == "review"
    assert contract["readonly"] is True
    assert contract["required_outputs"] == ["review_report", "changed_files", "severity_findings", "test_gaps", "uncertainties"]
    assert contract["required_evidence"] == ["git_diff_scope", "severity_rationale", "test_coverage_gap", "open_uncertainties"]
    assert contract["fields"]["diff_range_policy"]["scope_source"] == "git-history"
    assert contract["fields"]["diff_range_policy"]["dirty_worktree_policy"] == "exclude-uncommitted-from-canonical-range-and-record-separately"
    assert contract["fields"]["diff_range_policy"]["record_excluded_candidates"] is True
    assert contract["fields"]["auth_scope_policy"]["entrypoint_required"] is True
    assert contract["fields"]["auth_scope_policy"]["caller_visible_state_required"] is True
    assert contract["fields"]["auth_scope_policy"]["persisted_store_required"] is True
    assert contract["fields"]["auth_scope_policy"]["record_excluded_paths"] is True
    assert contract["fields"]["auth_scope_policy"]["helper_only_boundary_requires_proof"] is True
    assert contract["fields"]["scope_anchor_terms"] == ["login"]
    assert contract["artifact_contracts"]["review_report"]["path"] == "review_report.md"


def test_review_request_contract_defaults_task_plan_to_readonly() -> None:
    prompt = (
        "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘. "
        "변경 파일과 테스트 공백, 확인이 필요한 불확실성을 명시하고 review 결과물만 남겨라."
    )
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Reviewer"],
        project_key="demo-login-build",
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "review only",
            "subtasks": [
                {"id": "S1", "title": "Scope range", "goal": "pick canonical diff range", "owner_role": "Codex-Reviewer"},
                {"id": "S2", "title": "Write report", "goal": "finalize review report", "owner_role": "Codex-Reviewer"},
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Reviewer"],
        max_subtasks=4,
        meta_overrides={"request_contract": contract, "phase1_role_preset": "review", "phase2_team_preset": "review"},
    )

    assert plan["meta"]["readonly"] is True
    assert plan["meta"]["phase2_execution_plan"]["readonly"] is True
    assert any("Review-only flow stays readonly" in item for item in plan["subtasks"][0]["acceptance"])
    assert any("Canonical diff scope records recent matching candidates" in item for item in plan["subtasks"][0]["acceptance"])
    assert any("Auth/session scope evidence enumerates login entrypoints" in item for item in plan["subtasks"][0]["acceptance"])


def test_review_request_contract_adds_artifact_specific_acceptance_floor() -> None:
    prompt = (
        "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘. "
        "변경 파일과 테스트 공백, 확인이 필요한 불확실성을 명시하고 review 결과물만 남겨라."
    )
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Reviewer"],
        project_key="demo-login-build",
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "review only",
            "subtasks": [
                {"id": "S1", "title": "Canonical Diff 범위와 인증 경계 고정", "goal": "pick canonical diff range and auth scope", "owner_role": "Codex-Reviewer"},
                {"id": "S2", "title": "변경 파일별 회귀 리스크 판정", "goal": "write severity findings with impact and evidence", "owner_role": "Codex-Reviewer"},
                {"id": "S3", "title": "테스트 공백과 잔여 불확실성 정리", "goal": "separate test gaps and uncertainties", "owner_role": "Codex-Reviewer"},
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Reviewer"],
        max_subtasks=4,
        meta_overrides={"request_contract": contract, "phase1_role_preset": "review", "phase2_team_preset": "review"},
    )

    s1 = plan["subtasks"][0]["acceptance"]
    s2 = plan["subtasks"][1]["acceptance"]
    s3 = plan["subtasks"][2]["acceptance"]

    assert any("Canonical diff scope records recent matching candidates" in item for item in s1)
    assert any("Auth/session scope evidence enumerates login entrypoints" in item for item in s1)
    assert any("Each severity finding records severity" in item for item in s2)
    assert any("Test gaps and uncertainties are separated explicitly" in item for item in s3)


def test_mixed_request_contract_extracts_handoff_and_reviewer_note_outputs() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
    )

    assert contract["preset"] == "mixed"
    assert contract["required_outputs"] == ["work_result", "scope_inventory", "handoff_doc", "reviewer_note"]
    deliverable_policy = contract["fields"]["deliverable_policy"]
    assert deliverable_policy["execution_outputs"] == ["scope_inventory", "work_result"]
    assert deliverable_policy["writer_outputs"] == ["handoff_doc"]
    assert deliverable_policy["review_outputs"] == ["reviewer_note"]
    assert contract["fields"]["auth_failure_policy"]["target_failure_codes"] == ["session_expired"]
    assert contract["fields"]["auth_failure_policy"]["require_negative_case_evidence"] is True
    assert "auth_scope_inventory" in contract["required_evidence"]
    assert contract["artifact_contracts"]["scope_inventory"]["path"] == "docs/analysis/auth_scope_inventory.md"
    assert contract["artifact_contracts"]["work_result"]["format"] == "implementation_delta"
    assert contract["artifact_contracts"]["handoff_doc"]["path"] == "docs/handoff/operator_handoff.md"
    assert contract["artifact_contracts"]["reviewer_note"]["path"] == "docs/reviews/reviewer_note.md"


def test_mixed_request_contract_adds_handoff_and_reviewer_note_acceptance_floor() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed deliverables",
            "subtasks": [
                {"id": "S0", "title": "Trace login session scope", "goal": "trace session_expired entrypoints and persisted token paths", "owner_role": "Codex-Dev", "acceptance": ["done"]},
                {"id": "S1", "title": "Implement token reset", "goal": "patch login flow and add regression tests", "owner_role": "Codex-Dev", "acceptance": ["done"]},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "acceptance": ["done"]},
                {"id": "S3", "title": "Summarize reviewer note", "goal": "package reviewer note for review lane", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=6,
        meta_overrides={"request_contract": contract, "phase1_role_preset": "mixed", "phase2_team_preset": "mixed"},
    )

    scope_acceptance = plan["subtasks"][0]["acceptance"]
    impl_acceptance = plan["subtasks"][1]["acceptance"]
    handoff_acceptance = plan["subtasks"][2]["acceptance"]
    review_acceptance = plan["subtasks"][3]["acceptance"]
    assert any("docs/analysis/auth_scope_inventory.md" in item for item in scope_acceptance)
    assert any("before implementation starts" in item for item in scope_acceptance)
    assert any("excluded paths with reasons" in item for item in scope_acceptance)
    assert any("proves whether one helper is the only boundary" in item for item in scope_acceptance)
    assert any("session_expired is in-scope" in item for item in scope_acceptance)
    assert not any("Implementation subtasks explicitly show" in item for item in scope_acceptance)
    assert any("work_result" in item for item in impl_acceptance)
    assert any("docs/analysis/auth_scope_inventory.md" in item for item in impl_acceptance)
    assert any("limited to session_expired" in item for item in impl_acceptance)
    assert any("For every inventory entry" in item for item in impl_acceptance)
    assert not any("Scope-tracing subtasks hand downstream lanes a concrete scope inventory" in item for item in impl_acceptance)
    assert not any("review lane" in item.lower() and "reviewer_note.md" in item for item in impl_acceptance)
    assert any("docs/handoff/operator_handoff.md" in item for item in handoff_acceptance)
    assert not any("Auth/session scope evidence enumerates login entrypoints" in item for item in handoff_acceptance)
    assert plan["subtasks"][3]["owner_role"] == "Codex-Reviewer"
    assert plan["subtasks"][3]["title"] == "Draft reviewer note"
    assert any("docs/reviews/reviewer_note.md" in item for item in review_acceptance)


def test_mixed_scope_task_with_fix_boundary_language_keeps_scope_acceptance() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed scope-first repair",
            "subtasks": [
                {
                    "id": "S1",
                    "title": "로그인 실패 경계 및 저장소 범위 확정",
                    "goal": "session_expired가 실제로 관찰되는 로그인/auth 진입점과 persisted token store를 전수 확인해 수정 범위를 고정한다.",
                    "owner_role": "Codex-Dev",
                    "acceptance": ["done"],
                }
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=6,
        meta_overrides={"request_contract": contract, "phase1_role_preset": "mixed", "phase2_team_preset": "mixed"},
    )

    acceptance = plan["subtasks"][0]["acceptance"]
    assert any("docs/analysis/auth_scope_inventory.md" in item for item in acceptance)
    assert any("before implementation starts" in item for item in acceptance)
    assert any("session_expired is in-scope" in item for item in acceptance)
    assert not any("Execution-owned work_result records" in item for item in acceptance)


def test_mixed_handoff_task_with_implementation_evidence_language_keeps_handoff_acceptance() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed handoff-first repair",
            "subtasks": [
                {
                    "id": "S3",
                    "title": "운영자 handoff 문서 작성",
                    "goal": "운영자가 바로 판단할 수 있도록 canonical handoff 문서를 작성하고, 구현 근거와 검증 상태를 함께 정리한다.",
                    "owner_role": "Codex-Writer",
                    "acceptance": ["done"],
                }
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=6,
        meta_overrides={"request_contract": contract, "phase1_role_preset": "mixed", "phase2_team_preset": "mixed"},
    )

    acceptance = plan["subtasks"][0]["acceptance"]
    assert any("docs/handoff/operator_handoff.md" in item for item in acceptance)
    assert not any("Execution-owned work_result records" in item for item in acceptance)


def test_review_request_contract_module_matches_review_prompt() -> None:
    prompt = "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘."
    assert request_contract_review_mod.review_request_contract_matches(prompt) is True


def test_review_request_contract_does_not_override_build_fix_request() -> None:
    prompt = "로그인 버그를 수정하고 회귀 리스크도 같이 검토해줘."

    preset = request_contract_mod.resolve_request_contract_preset(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Reviewer"],
    )

    assert preset == "build"


def test_phase2_team_preset_overrides_planner_owner_role_drift() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "reporting",
            "subtasks": [
                {"id": "S1", "title": "Draft", "goal": "write report", "owner_role": "Codex-Reviewer", "acceptance": ["done"]},
            ],
        },
        user_prompt="결과를 요약하고 보고서를 작성해라",
        workers=["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
        meta_overrides={
            "worker_roles": ["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "writer",
            "phase2_team_preset": "writer",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    execution_plan = plan["meta"]["phase2_execution_plan"]
    assert [row["role"] for row in spec["execution_groups"]] == ["Codex-Writer", "Claude-Writer"]
    assert [row["role"] for row in spec["review_groups"]] == ["Codex-Reviewer", "Claude-Reviewer"]
    assert [row["role"] for row in execution_plan["execution_lanes"]] == ["Codex-Writer", "Claude-Writer"]
    assert execution_plan["review_mode"] == "parallel"


def test_multi_subtask_execution_lanes_are_serialized_even_with_parallel_workers() -> None:
    spec = {
        "execution_mode": "parallel",
        "execution_groups": [
            {"group_id": "E1", "role": "Codex-Reviewer", "subtask_ids": ["S1", "S2", "S3"]},
            {"group_id": "E1C", "role": "Claude-Reviewer", "subtask_ids": ["S1", "S2", "S3"]},
        ],
        "review_mode": "parallel",
        "review_groups": [
            {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E1C"]},
            {"group_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E1", "E1C"]},
        ],
    }

    plan = orch_contract_mod.normalize_phase2_execution_plan(
        {
            "execution_mode": "parallel",
            "execution_lanes": [
                {"lane_id": "E1", "role": "Codex-Reviewer", "subtask_ids": ["S1", "S2", "S3"], "parallel": True},
                {"lane_id": "E1C", "role": "Claude-Reviewer", "subtask_ids": ["S1", "S2", "S3"], "parallel": True},
            ],
            "review_mode": "parallel",
            "review_lanes": [
                {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E1C"], "parallel": True},
                {"lane_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E1", "E1C"], "parallel": True},
            ],
        },
        team_spec=spec,
        readonly=False,
    )

    assert [row["parallel"] for row in plan["execution_lanes"]] == [False, False]
    assert plan["parallel_workers"] is True


def test_phase2_mixed_preset_keeps_work_roles_out_of_reviewer_drift() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed execution",
            "subtasks": [
                {"id": "S1", "title": "Do work", "goal": "fix and document", "owner_role": "Codex-Reviewer", "acceptance": ["done"]},
            ],
        },
        user_prompt="로그인 수정안과 handoff 문서를 함께 준비하고 회귀 리스크도 검토해줘.",
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=4,
        meta_overrides={
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    execution_roles = [row["role"] for row in spec["execution_groups"]]
    review_roles = [row["role"] for row in spec["review_groups"]]

    assert execution_roles == ["Codex-Dev", "Codex-Writer", "Claude-Writer"]
    assert review_roles == ["Codex-Reviewer", "Claude-Reviewer"]


def test_phase2_mixed_preset_preserves_execution_lane_dependencies() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 handoff와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed execution with dependency graph",
            "subtasks": [
                {"id": "S1", "title": "Confirm failure boundary", "goal": "trace entrypoint and persisted store", "owner_role": "Codex-Dev"},
                {
                    "id": "S2",
                    "title": "Patch login flow",
                    "goal": "clear stale token on session_expired",
                    "owner_role": "Codex-Writer",
                    "depends_on": ["S1"],
                },
                {
                    "id": "S3",
                    "title": "Add regression note",
                    "goal": "record before/after operator handoff note",
                    "owner_role": "Claude-Writer",
                    "depends_on": ["S1"],
                },
                {
                    "id": "S4",
                    "title": "Summarize reviewer note",
                    "goal": "package implementation and handoff evidence for review",
                    "owner_role": "Codex-Writer",
                    "depends_on": ["S2", "S3"],
                },
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    subtasks = {row["id"]: row for row in plan["subtasks"]}
    assert subtasks["S2"]["depends_on"] == ["S1"]
    assert subtasks["S3"]["depends_on"] == ["S1"]
    assert subtasks["S4"]["depends_on"] == ["S2", "S3"]

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]

    assert subtasks["S4"]["owner_role"] == "Codex-Reviewer"
    assert subtasks["S4"]["title"] == "Draft reviewer note"

    assert [row["role"] for row in spec["execution_groups"]] == ["Codex-Dev", "Codex-Writer", "Claude-Writer"]
    assert spec["execution_groups"][0]["subtask_ids"] == ["S1"]
    assert spec["execution_groups"][1]["subtask_ids"] == ["S2"]
    assert spec["execution_groups"][2]["subtask_ids"] == ["S3"]
    assert spec["execution_groups"][1]["depends_on"] == ["E1"]
    assert spec["execution_groups"][2]["depends_on"] == ["E1"]
    assert spec["review_groups"][0]["role"] == "Codex-Reviewer"
    assert spec["review_groups"][0]["subtask_ids"] == ["S4"]
    assert spec["review_groups"][0]["outputs"] == ["reviewer_note"]

    assert [row["lane_id"] for row in exec_plan["execution_lanes"]] == ["E1", "E2", "E3"]
    assert exec_plan["execution_lanes"][1]["depends_on"] == ["E1"]
    assert exec_plan["execution_lanes"][2]["depends_on"] == ["E1"]


def test_phase2_mixed_preset_assigns_review_outputs_to_first_review_lane() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed execution with review deliverable",
            "subtasks": [
                {"id": "S1", "title": "Patch login flow", "goal": "clear stale token on session_expired", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "depends_on": ["S1"]},
                {"id": "S3", "title": "Prepare reviewer evidence", "goal": "package implementation and handoff evidence for review lane", "owner_role": "Claude-Writer", "depends_on": ["S1", "S2"]},
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]

    assert plan["subtasks"][2]["owner_role"] == "Codex-Reviewer"
    assert plan["subtasks"][2]["title"] == "Draft reviewer note"
    assert spec["review_groups"][0]["outputs"] == ["reviewer_note"]
    assert spec["review_groups"][0]["deliverables"][0]["path"] == "docs/reviews/reviewer_note.md"
    assert "severity findings" in spec["review_groups"][0]["deliverables"][0]["required_fields"]
    assert any("docs/reviews/reviewer_note.md" in item for item in spec["review_groups"][0]["acceptance"])
    assert all(not row.get("outputs") for row in spec["review_groups"][1:])
    assert spec["critic_role"] == "Codex-Reviewer"
    assert exec_plan["review_lanes"][0]["outputs"] == ["reviewer_note"]
    assert exec_plan["review_lanes"][0]["deliverables"][0]["path"] == "docs/reviews/reviewer_note.md"
    assert any("docs/reviews/reviewer_note.md" in item for item in exec_plan["review_lanes"][0]["acceptance"])
    assert all(not row.get("outputs") for row in exec_plan["review_lanes"][1:])


def test_phase2_mixed_preset_repairs_review_owned_subtask_into_evidence_prep() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed review-owned artifact repair",
            "subtasks": [
                {"id": "S1", "title": "Patch login flow", "goal": "clear stale token on session_expired", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "depends_on": ["S1"]},
                {
                    "id": "S3",
                    "title": "review-lane reviewer_note 작성",
                    "goal": "Phase2 review lane에서 docs/reviews/reviewer_note.md를 작성해 구현 위험과 테스트 공백을 남긴다.",
                    "owner_role": "Codex-Reviewer",
                    "depends_on": ["S1"],
                },
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    s3 = plan["subtasks"][2]
    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]

    assert s3["owner_role"] == "Codex-Reviewer"
    assert s3["title"] == "Draft reviewer note"
    assert "docs/reviews/reviewer_note.md" in s3["goal"]
    assert not any(row["role"] == "Codex-Reviewer" for row in spec["execution_groups"])
    assert spec["review_groups"][0]["subtask_ids"] == ["S3"]
    assert spec["review_groups"][0]["outputs"] == ["reviewer_note"]
    assert spec["review_groups"][0]["deliverables"][0]["path"] == "docs/reviews/reviewer_note.md"
    assert exec_plan["review_lanes"][0]["outputs"] == ["reviewer_note"]
    assert exec_plan["review_lanes"][0]["deliverables"][0]["path"] == "docs/reviews/reviewer_note.md"


def test_phase2_review_lane_preserves_explicit_outputs() -> None:
    plan = orch_contract_mod.normalize_phase2_execution_plan(
        {
            "execution_mode": "parallel",
            "execution_lanes": [
                {"lane_id": "E1", "role": "Codex-Dev", "subtask_ids": ["S1"], "parallel": False},
                {"lane_id": "E2", "role": "Codex-Writer", "subtask_ids": ["S2"], "parallel": False},
            ],
            "review_mode": "parallel",
            "review_lanes": [
                {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E2"], "outputs": ["reviewer_note"], "parallel": True},
                {"lane_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E1", "E2"], "parallel": True},
            ],
        },
        team_spec={
            "execution_mode": "parallel",
            "execution_groups": [
                {"group_id": "E1", "role": "Codex-Dev", "subtask_ids": ["S1"]},
                {"group_id": "E2", "role": "Codex-Writer", "subtask_ids": ["S2"]},
            ],
            "review_mode": "parallel",
            "review_groups": [
                {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E2"], "outputs": ["reviewer_note"]},
                {"group_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E1", "E2"], "outputs": []},
            ],
        },
        readonly=False,
    )

    assert plan["review_lanes"][0]["outputs"] == ["reviewer_note"]
    assert plan["review_lanes"][1]["outputs"] == []


def test_phase2_mixed_preset_keeps_single_writer_owner_for_canonical_handoff_output() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed canonical handoff owner",
            "subtasks": [
                {"id": "S1", "title": "Implement fix", "goal": "clear stale token", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "depends_on": ["S1"]},
            ],
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]
    dev_groups = [row for row in spec["execution_groups"] if row["role"] == "Codex-Dev"]
    dev_lanes = [row for row in exec_plan["execution_lanes"] if row["role"] == "Codex-Dev"]
    writer_groups = [row for row in spec["execution_groups"] if "writer" in row["role"].lower()]
    writer_lanes = [row for row in exec_plan["execution_lanes"] if "writer" in row["role"].lower()]

    assert len(dev_groups) == 1
    assert dev_groups[0]["outputs"] == ["scope_inventory", "work_result"]
    assert dev_groups[0]["deliverables"][0]["path"] == "docs/analysis/auth_scope_inventory.md"
    assert "single_helper_boundary_proof_when_used" in dev_groups[0]["deliverables"][0]["required_fields"]
    assert any("docs/analysis/auth_scope_inventory.md" in item for item in dev_groups[0]["acceptance"])
    assert len(dev_lanes) == 1
    assert dev_lanes[0]["outputs"] == ["scope_inventory", "work_result"]
    assert dev_lanes[0]["deliverables"][0]["path"] == "docs/analysis/auth_scope_inventory.md"
    assert any("docs/analysis/auth_scope_inventory.md" in item for item in dev_lanes[0]["acceptance"])
    assert len(writer_groups) == 1
    assert writer_groups[0]["role"] == "Codex-Writer"
    assert writer_groups[0]["outputs"] == ["handoff_doc"]
    assert any("Writer lane directly writes docs/handoff/operator_handoff.md" in item for item in writer_groups[0]["acceptance"])
    assert len(writer_lanes) == 1
    assert writer_lanes[0]["role"] == "Codex-Writer"
    assert writer_lanes[0]["outputs"] == ["handoff_doc"]
    assert any("Writer lane directly writes docs/handoff/operator_handoff.md" in item for item in writer_lanes[0]["acceptance"])


def test_phase2_mixed_preset_repairs_cyclic_execution_dependencies_from_planner_metadata() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Claude-Analyst", "Codex-Dev", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed execution with cyclic planner graph",
            "subtasks": [
                {"id": "S1", "title": "Trace scope", "goal": "identify session_expired entrypoints", "owner_role": "Claude-Analyst"},
                {"id": "S2", "title": "Implement fix", "goal": "clear stale token", "owner_role": "Codex-Dev", "depends_on": ["S1"]},
                {"id": "S3", "title": "Write handoff", "goal": "write operator handoff", "owner_role": "Claude-Writer", "depends_on": ["S1", "S2"]},
            ],
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "E2", "role": "Codex-Dev", "subtask_ids": ["S2"], "depends_on": ["E1"]},
                    {"group_id": "E3", "role": "Claude-Writer", "subtask_ids": ["S3"], "depends_on": ["E1", "E2"]},
                    {"group_id": "E1", "role": "Claude-Analyst", "subtask_ids": ["S1"], "depends_on": ["E2"]},
                ],
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "E2", "role": "Codex-Dev", "subtask_ids": ["S2"], "depends_on": ["E1"], "parallel": True},
                    {"lane_id": "E3", "role": "Claude-Writer", "subtask_ids": ["S3"], "depends_on": ["E1", "E2"], "parallel": True},
                    {"lane_id": "E1", "role": "Claude-Analyst", "subtask_ids": ["S1"], "depends_on": ["E2"], "parallel": True},
                ],
            },
        },
        user_prompt=prompt,
        workers=["Claude-Analyst", "Codex-Dev", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Claude-Analyst", "Codex-Dev", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]
    spec_groups = {row["group_id"]: row for row in spec["execution_groups"]}
    exec_lanes = {row["lane_id"]: row for row in exec_plan["execution_lanes"]}

    assert spec_groups["E2"]["depends_on"] == ["E1"]
    assert spec_groups["E3"]["depends_on"] == ["E1", "E2"]
    assert "depends_on" not in spec_groups["E1"]

    assert exec_lanes["E2"]["depends_on"] == ["E1"]
    assert exec_lanes["E3"]["depends_on"] == ["E1", "E2"]
    assert "depends_on" not in exec_lanes["E1"]


def test_phase2_mixed_preset_repairs_repeated_execution_subtask_assignments() -> None:
    prompt = "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘."
    contract = request_contract_mod.build_request_contract(
        source_prompt=prompt,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
    )

    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed repeated execution assignments",
            "subtasks": [
                {"id": "S1", "title": "Implement fix", "goal": "clear stale token on session_expired", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Draft operator handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "depends_on": ["S1"]},
                {"id": "S3", "title": "Prepare reviewer evidence", "goal": "prepare reviewer note evidence", "owner_role": "Codex-Writer", "depends_on": ["S1", "S2"]},
            ],
            "phase2_team_spec": {
                "execution_mode": "parallel",
                "execution_groups": [
                    {"group_id": "E1", "role": "Codex-Dev", "subtask_ids": ["S1", "S2", "S3"]},
                    {"group_id": "E2", "role": "Codex-Writer", "subtask_ids": ["S1", "S2", "S3"]},
                ],
            },
            "phase2_execution_plan": {
                "execution_mode": "parallel",
                "execution_lanes": [
                    {"lane_id": "E1", "role": "Codex-Dev", "subtask_ids": ["S1", "S2", "S3"], "parallel": True},
                    {"lane_id": "E2", "role": "Codex-Writer", "subtask_ids": ["S1", "S2", "S3"], "parallel": True},
                ],
            },
        },
        user_prompt=prompt,
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "request_contract": contract,
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]
    spec_groups = {row["group_id"]: row for row in spec["execution_groups"]}
    exec_lanes = {row["lane_id"]: row for row in exec_plan["execution_lanes"]}

    assert spec_groups["E1"]["subtask_ids"] == ["S1"]
    assert spec_groups["E1"]["outputs"] == ["scope_inventory", "work_result"]
    assert spec_groups["E1"]["deliverables"][0]["path"] == "docs/analysis/auth_scope_inventory.md"
    assert spec_groups["E2"]["subtask_ids"] == ["S2"]
    assert spec_groups["E2"]["outputs"] == ["handoff_doc"]
    assert any("Writer lane directly writes docs/handoff/operator_handoff.md" in item for item in spec_groups["E2"]["acceptance"])
    assert spec["review_groups"][0]["subtask_ids"] == ["S3"]
    assert spec["review_groups"][0]["outputs"] == ["reviewer_note"]

    assert exec_lanes["E1"]["subtask_ids"] == ["S1"]
    assert exec_lanes["E1"]["outputs"] == ["scope_inventory", "work_result"]
    assert exec_lanes["E1"]["deliverables"][0]["path"] == "docs/analysis/auth_scope_inventory.md"
    assert exec_lanes["E2"]["subtask_ids"] == ["S2"]
    assert exec_lanes["E2"]["outputs"] == ["handoff_doc"]
    assert any("Writer lane directly writes docs/handoff/operator_handoff.md" in item for item in exec_lanes["E2"]["acceptance"])
    assert exec_plan["review_lanes"][0]["outputs"] == ["reviewer_note"]


def test_phase2_mixed_preset_repairs_owner_role_outside_worker_roles() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "mixed owner role repair",
            "subtasks": [
                {"id": "S1", "title": "Trace scope", "goal": "identify session_expired entrypoints", "owner_role": "Claude-Analyst"},
                {"id": "S2", "title": "Implement fix", "goal": "clear stale token", "owner_role": "Codex-Dev"},
                {"id": "S3", "title": "Write handoff", "goal": "write operator handoff", "owner_role": "Claude-Writer"},
            ],
        },
        user_prompt="session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘.",
        workers=["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "worker_roles": ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "mixed",
            "phase2_team_preset": "mixed",
        },
    )

    assert [row["owner_role"] for row in plan["subtasks"]] == ["Codex-Dev", "Codex-Dev", "Claude-Writer"]
    assert plan["meta"]["worker_roles"] == ["Codex-Dev", "Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"]


def test_build_planned_dispatch_prompt_includes_phase2_team_lanes() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "parallel execution",
            "subtasks": [
                {"id": "S1", "title": "Implement", "goal": "build feature", "owner_role": "Codex-Dev", "acceptance": ["done"]},
                {"id": "S2", "title": "Document", "goal": "write handoff", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
        },
        user_prompt="계획 수립 후 병렬 실행팀을 꾸려라",
        workers=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        max_subtasks=4,
    )
    plan = gw.attach_phase2_team_spec(
        plan,
        roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        verifier_roles=["Codex-Reviewer"],
        require_verifier=True,
    )

    prompt = gw.build_planned_dispatch_prompt(
        "원 요청",
        plan,
        {"approved": True, "issues": [], "recommendations": []},
    )

    assert "Phase2 execution lanes: parallel" in prompt
    assert "lane E1 [Codex-Dev] -> S1" in prompt
    assert "Phase2 critic lanes: single" in prompt
    assert "review R1 [Codex-Reviewer/verifier]" in prompt


def test_build_preset_repairs_partial_phase2_graph_from_planner_metadata() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "login fix",
            "subtasks": [
                {"id": "S1", "title": "Trace bug", "goal": "inspect session expiry path", "owner_role": "Codex-Analyst"},
                {"id": "S2", "title": "Implement fix", "goal": "patch session handler", "owner_role": "Codex-Dev"},
                {"id": "S3", "title": "Before after log", "goal": "record behavior delta", "owner_role": "Codex-Analyst"},
                {"id": "S4", "title": "Review note", "goal": "write review evidence", "owner_role": "Codex-Analyst"},
            ],
            "phase2_team_spec": {
                "execution_mode": "single",
                "execution_groups": [
                    {"group_id": "E2", "role": "Codex-Dev", "subtask_ids": ["S2"]},
                ],
                "review_mode": "parallel",
                "review_groups": [
                    {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E3"]},
                    {"group_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E3"]},
                ],
            },
            "phase2_execution_plan": {
                "execution_mode": "single",
                "execution_lanes": [
                    {"lane_id": "E2", "role": "Codex-Dev", "subtask_ids": ["S2"], "parallel": False},
                ],
                "review_mode": "parallel",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1", "E3"], "parallel": True},
                    {"lane_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["E3"], "parallel": True},
                ],
            },
        },
        user_prompt="로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.",
        workers=["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=6,
        meta_overrides={
            "worker_roles": ["Codex-Dev", "Codex-Reviewer", "Claude-Reviewer"],
            "phase1_role_preset": "build",
            "phase2_team_preset": "build",
        },
    )

    spec = plan["meta"]["phase2_team_spec"]
    exec_plan = plan["meta"]["phase2_execution_plan"]

    assert [row["owner_role"] for row in plan["subtasks"]] == ["Codex-Dev", "Codex-Dev", "Codex-Dev", "Codex-Dev"]
    assert [row["group_id"] for row in spec["execution_groups"]] == ["E1"]
    assert [row["role"] for row in spec["execution_groups"]] == ["Codex-Dev"]
    assert spec["execution_groups"][0]["subtask_ids"] == ["S1", "S2", "S3", "S4"]
    assert [row["depends_on"] for row in spec["review_groups"]] == [["E1"], ["E1"]]

    assert [row["lane_id"] for row in exec_plan["execution_lanes"]] == ["E1"]
    assert [row["role"] for row in exec_plan["execution_lanes"]] == ["Codex-Dev"]
    assert exec_plan["execution_lanes"][0]["subtask_ids"] == ["S1", "S2", "S3", "S4"]
    assert [row["depends_on"] for row in exec_plan["review_lanes"]] == [["E1"], ["E1"]]


def test_single_execution_lane_coerces_parallel_false() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "login fix",
            "subtasks": [
                {"id": "S1", "title": "Trace bug", "goal": "inspect failure", "owner_role": "Codex-Dev"},
                {"id": "S2", "title": "Patch handler", "goal": "fix session expiry", "owner_role": "Codex-Dev"},
                {"id": "S3", "title": "Run tests", "goal": "capture regression evidence", "owner_role": "Codex-Dev"},
            ],
            "phase2_execution_plan": {
                "execution_mode": "single",
                "execution_lanes": [
                    {"lane_id": "E1", "role": "Codex-Dev", "subtask_ids": ["S1", "S2", "S3"], "parallel": True},
                ],
                "review_mode": "single",
                "review_lanes": [
                    {"lane_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["E1"], "parallel": True},
                ],
            },
        },
        user_prompt="로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.",
        workers=["Codex-Dev", "Codex-Reviewer"],
        max_subtasks=4,
        meta_overrides={
            "worker_roles": ["Codex-Dev", "Codex-Reviewer"],
            "phase1_role_preset": "build",
            "phase2_team_preset": "build",
        },
    )

    exec_plan = plan["meta"]["phase2_execution_plan"]

    assert exec_plan["execution_lanes"][0]["parallel"] is False
    assert exec_plan["review_lanes"][0]["parallel"] is False


def test_normalize_task_plan_payload_with_companion_workers_derives_parallel_claude_lanes() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "parallel reporting",
            "subtasks": [
                {"id": "S1", "title": "Document", "goal": "write handoff", "owner_role": "Codex-Writer", "acceptance": ["done"]},
                {"id": "S2", "title": "Analyze", "goal": "compare options", "owner_role": "Codex-Analyst", "acceptance": ["done"]},
            ],
        },
        user_prompt="계획 수립 후 병렬 실행팀을 꾸려라",
        workers=["Codex-Writer", "Claude-Writer", "Codex-Analyst", "Claude-Analyst", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=4,
    )
    plan = gw.attach_phase2_team_spec(
        plan,
        roles=["Codex-Writer", "Claude-Writer", "Codex-Analyst", "Claude-Analyst", "Codex-Reviewer", "Claude-Reviewer"],
        verifier_roles=["Codex-Reviewer"],
        require_verifier=True,
    )

    spec = plan["meta"]["phase2_team_spec"]
    execution_plan = plan["meta"]["phase2_execution_plan"]
    assert [row["role"] for row in spec["execution_groups"]] == [
        "Codex-Writer",
        "Claude-Writer",
        "Codex-Analyst",
        "Claude-Analyst",
    ]
    assert [row["role"] for row in spec["review_groups"]] == ["Codex-Reviewer", "Claude-Reviewer"]
    assert [row["role"] for row in execution_plan["execution_lanes"]] == [
        "Codex-Writer",
        "Claude-Writer",
        "Codex-Analyst",
        "Claude-Analyst",
    ]
    assert [row["role"] for row in execution_plan["review_lanes"]] == ["Codex-Reviewer", "Claude-Reviewer"]


def test_build_planned_dispatch_prompt_includes_phase2_quality_contract() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "writer execution plan",
            "subtasks": [
                {"id": "S1", "title": "Draft handoff", "goal": "write operator handoff", "owner_role": "Codex-Writer", "acceptance": ["done"]},
            ],
            "evidence_required": [
                "Draft or handoff artifact is produced.",
                "Output is readable from the operator perspective.",
            ],
            "meta": {
                "phase1_role_preset": "writer",
                "phase2_team_preset": "writer",
            },
        },
        user_prompt="문서형 실행 계획을 준비해라",
        workers=["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
    )
    plan = gw.attach_phase2_team_spec(
        plan,
        roles=["Codex-Writer", "Claude-Writer", "Codex-Reviewer", "Claude-Reviewer"],
        verifier_roles=["Codex-Reviewer", "Claude-Reviewer"],
        require_verifier=True,
    )
    plan["evidence_required"] = [
        "Draft or handoff artifact is produced.",
        "Output is readable from the operator perspective.",
    ]

    prompt = gw.build_planned_dispatch_prompt(
        "원 요청",
        plan,
        {"approved": True, "issues": [], "recommendations": []},
    )

    assert "Phase2 quality contract:" in prompt
    assert "- preset: phase1=writer phase2=writer" in prompt
    assert "- approval mode: policy" in prompt
    assert "- operator approval/recovery remains outside Task Team" in prompt
    assert "- critic role: Codex-Reviewer" in prompt
    assert "- integration role: Codex-Writer" in prompt
    assert "- evidence: Draft or handoff artifact is produced." in prompt
    assert "- evidence: Output is readable from the operator perspective." in prompt
    assert "- quality contract의 preset/critic/integration/evidence를 기본 완료 기준으로 따른다." in prompt


def test_normalize_task_plan_payload_defaults_approval_mode_to_policy() -> None:
    plan = gw.normalize_task_plan_payload(
        {
            "summary": "analysis plan",
            "subtasks": [
                {"id": "S1", "title": "Analyze", "goal": "review runtime issues", "owner_role": "Codex-Analyst", "acceptance": ["done"]},
            ],
        },
        user_prompt="오프데스크 운영 문제를 검토해라",
        workers=["Codex-Analyst", "Codex-Reviewer", "Claude-Reviewer"],
        max_subtasks=3,
    )

    assert plan["meta"]["approval_mode"] == "policy"
