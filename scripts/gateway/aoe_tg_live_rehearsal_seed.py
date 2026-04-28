#!/usr/bin/env python3
"""Seed isolated live-rehearsal runtimes without launching internal jobs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from aoe_tg_external_background_worker import emit_external_background_handoff
from aoe_tg_request_contract import (
    build_execution_brief,
    build_background_run_ticket,
    build_external_runner_gateway_command_launch_spec,
    build_request_contract,
    request_contract_metadata,
)
from aoe_tg_background_runs import upsert_background_run_ticket
import aoe_tg_runtime_read as runtime_read


R2_REQUEST_TEXT = (
    "최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행해줘. canonical diff range, 변경 파일, "
    "severity findings, test gaps, uncertainties를 review_report.md에 남겨라. "
    "범위 근거나 필수 섹션이 부족하면 done으로 닫지 말고 rerun으로 남겨라."
)

B2_REQUEST_TEXT = (
    "로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘. "
    "세션 정리나 테스트 증적이 부족하면 done으로 닫지 말고 rerun으로 남겨라."
)

B3_REQUEST_TEXT = (
    "로그인 실패 세션 정리 패치의 테스트 증거는 보강하되, 배포 허용 여부와 릴리즈 문구는 "
    "내가 판단할 수 있게 manual follow-up으로 남겨라."
)

R3_EXECUTE_REQUEST_TEXT = (
    "로그인 패치의 회귀 리스크 후보를 정리하고, 내가 지정한 lane만 후속 증거 수집으로 다시 실행해줘."
)

R4_REQUEST_TEXT = (
    "review rerun work is handed to a non-local background runner and must remain operator-visible through handoff, pickup acknowledgement, and result."
)

D2_REQUEST_TEXT = (
    "입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. "
    "허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. "
    "orders는 integer, revenue는 number 스키마를 유지해야 한다. "
    "orders 또는 revenue에서 null 또는 비수치 값이 2행 이상이면 null-heavy=true로 판정하고 done으로 닫지 말고 rerun으로 남겨라. "
    "parse 불가하거나 범위를 벗어난 month 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. "
    "schema_report.json에는 orders/revenue의 expected_type, observed_inferred_type, schema_drift, rerun_required, violations를 남기고, "
    "null_summary.md에는 affected_columns, null_or_invalid_count, null_heavy, rerun_required, reason을 남겨라. "
    "schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라."
)

D3_REQUEST_TEXT = (
    "입력 CSV는 data/customer_events.csv이고 region_code 매핑에는 KR=Korea, US=United States만 확정되어 있다. "
    "EU와 APAC은 운영자가 비즈니스 기준으로 어느 reporting_region에 넣을지 결정해야 한다. "
    "확정 매핑만 적용해 normalized_customers.csv와 data_profile.md를 만들고, "
    "미확정 매핑은 business_rule_questions.md와 sample_ambiguous_rows.csv에 남겨라. "
    "EU/APAC 매핑 결정은 내가 판단해야 하므로 done으로 닫지 말고 manual follow-up으로 남겨라."
)

M2_REQUEST_TEXT = (
    "session_expired 로그인 실패 시 토큰을 비우도록 수정하고 operator handoff 문서와 reviewer note를 함께 남겨줘. "
    "구현 결과는 src/session.js와 tests/session.test.js에 남기고, handoff 문서는 변경 파일 목록과 테스트 증거를 포함해야 한다. "
    "handoff 문서나 reviewer note가 구현 증거와 불일치하면 done으로 닫지 말고 writer/handoff lane만 rerun으로 남겨라."
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare_project_layout(control_root: Path, *, overview: str) -> tuple[Path, Path, Path]:
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
            "overview": overview,
            "provider_commands": {
                "codex": "codex",
                "claude": "claude",
            },
            "coordinator": {
                "role": "Orchestrator",
                "provider": "codex",
                "launch": "codex",
                "session": "",
            },
            "agents": [
                {"role": "Codex-Dev", "provider": "codex", "launch": "codex", "session": ""},
                {"role": "Codex-Writer", "provider": "codex", "launch": "codex", "session": ""},
                {"role": "DataEngineer", "provider": "codex", "launch": "codex", "session": ""},
                {"role": "Codex-Reviewer", "provider": "codex", "launch": "codex", "session": ""},
                {"role": "Claude-Reviewer", "provider": "claude", "launch": "claude", "session": ""},
            ],
        },
    )
    return team_dir, project_root, project_team_dir


def _d2_request_contract() -> Dict[str, Any]:
    return build_request_contract(
        source_prompt=D2_REQUEST_TEXT,
        selected_roles=["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
        explicit_preset="data",
        project_key="alpha",
    )


def _m2_request_contract() -> Dict[str, Any]:
    return build_request_contract(
        source_prompt=M2_REQUEST_TEXT,
        selected_roles=["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        explicit_preset="mixed",
        project_key="alpha",
    )


def _write_m2_artifacts(project_root: Path) -> None:
    (project_root / "src").mkdir(parents=True, exist_ok=True)
    (project_root / "tests").mkdir(parents=True, exist_ok=True)
    (project_root / "docs" / "analysis").mkdir(parents=True, exist_ok=True)
    (project_root / "docs" / "handoff").mkdir(parents=True, exist_ok=True)
    (project_root / "docs" / "reviews").mkdir(parents=True, exist_ok=True)
    (project_root / "src" / "session.js").write_text(
        "\n".join(
            [
                "export function handleLoginFailure(error, tokenStore) {",
                "  if (error && error.code === 'session_expired') {",
                "    tokenStore.clear();",
                "    return { status: 'logged_out', reason: 'session_expired' };",
                "  }",
                "  return { status: 'failed', reason: error && error.code };",
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "tests" / "session.test.js").write_text(
        "\n".join(
            [
                "import { handleLoginFailure } from '../src/session.js';",
                "",
                "test('clears persisted token on session_expired', () => {",
                "  const calls = [];",
                "  const tokenStore = { clear: () => calls.push('clear') };",
                "  expect(handleLoginFailure({ code: 'session_expired' }, tokenStore)).toEqual({",
                "    status: 'logged_out',",
                "    reason: 'session_expired',",
                "  });",
                "  expect(calls).toEqual(['clear']);",
                "});",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "docs" / "analysis" / "auth_scope_inventory.md").write_text(
        "\n".join(
            [
                "# Auth Scope Inventory",
                "",
                "- public_failure_entrypoints: login submit, token refresh",
                "- caller_visible_auth_state_surfaces: logged_out banner, retry button",
                "- persisted_token_or_session_store_paths: tokenStore.clear",
                "- excluded_paths_with_reasons: password reset is not a session_expired path",
                "- target_failure_codes: session_expired",
                "- non_target_failures_preserve_existing_auth_state: true",
                "- single_helper_boundary_proof_when_used: handleLoginFailure owns token clearing",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "docs" / "handoff" / "operator_handoff.md").write_text(
        "\n".join(
            [
                "# Operator Handoff",
                "",
                "- change summary: session_expired now clears persisted token state.",
                "- validation status: implementation reviewed, but test evidence is missing from this handoff.",
                "- changed files: src/session.js",
                "- missing changed files: tests/session.test.js",
                "- operator follow-ups: rerun writer handoff lane to reconcile changed files and validation evidence.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "docs" / "reviews" / "reviewer_note.md").write_text(
        "\n".join(
            [
                "# Reviewer Note",
                "",
                "- severity findings: medium; handoff evidence omits the regression test file.",
                "- regression risks: operator may ship without seeing session_expired regression proof.",
                "- test gaps: handoff references no test command or result for tests/session.test.js.",
                "- uncertainties: implementation lane appears complete, but handoff/review evidence is stale.",
                "- verdict: retry writer/handoff lane L2 with reviewer lane R1; do not rerun implementation lane L1.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_d2_artifacts(project_root: Path) -> None:
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "data" / "monthly_raw.csv").write_text(
        "\n".join(
            [
                "month,region,orders,revenue,notes",
                "2026/01,NA,10,1250.50,ok",
                "2026-02,EU,,980.00,missing orders",
                "2026.03,APAC,7,NaN,null-like revenue",
                "2026/13,NA,abc,1500.00,bad month and bad orders",
                "bad-month,EU,14,bad,bad month and bad revenue",
                "2026-04,APAC,12,1100.00,ok",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "normalized.csv").write_text(
        "\n".join(
            [
                "month,region,orders,revenue,notes",
                "2026-01,NA,10,1250.50,ok",
                "2026-02,EU,,980.00,missing orders",
                "2026-03,APAC,7,NaN,null-like revenue",
                "2026/13,NA,abc,1500.00,bad month and bad orders",
                "bad-month,EU,14,bad,bad month and bad revenue",
                "2026-04,APAC,12,1100.00,ok",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        project_root / "schema_report.json",
        {
            "source_path": "data/monthly_raw.csv",
            "normalized_output": "normalized.csv",
            "schema_drift": {
                "status": True,
                "rerun_required": True,
                "violations": [
                    {"column": "orders", "expected_type": "integer", "null_or_invalid_count": 2},
                    {"column": "revenue", "expected_type": "number", "null_or_invalid_count": 2},
                ],
            },
            "columns": [
                {
                    "name": "month",
                    "expected_type": "string",
                    "observed_inferred_type": "string",
                    "inferred_type": "string",
                    "type_rule": "valid month values normalized to YYYY-MM; invalid values preserved",
                    "null_count": 0,
                    "observed_non_null_count": 6,
                    "schema_drift": False,
                    "rerun_required": False,
                    "violations": [],
                },
                {
                    "name": "region",
                    "expected_type": "string",
                    "observed_inferred_type": "string",
                    "inferred_type": "string",
                    "type_rule": "string",
                    "null_count": 0,
                    "observed_non_null_count": 6,
                    "schema_drift": False,
                    "rerun_required": False,
                    "violations": [],
                },
                {
                    "name": "orders",
                    "expected_type": "integer",
                    "observed_inferred_type": "string",
                    "inferred_type": "string",
                    "type_rule": "integer expected; empty and non-numeric values count as invalid",
                    "null_count": 1,
                    "observed_non_null_count": 5,
                    "schema_drift": True,
                    "rerun_required": True,
                    "violations": [
                        {"row": 2, "value": "", "kind": "empty-string"},
                        {"row": 4, "value": "abc", "kind": "non-numeric"},
                    ],
                },
                {
                    "name": "revenue",
                    "expected_type": "number",
                    "observed_inferred_type": "string",
                    "inferred_type": "string",
                    "type_rule": "number expected; null-like and non-numeric values count as invalid",
                    "null_count": 1,
                    "observed_non_null_count": 5,
                    "schema_drift": True,
                    "rerun_required": True,
                    "violations": [
                        {"row": 3, "value": "NaN", "kind": "literal-nan"},
                        {"row": 5, "value": "bad", "kind": "non-numeric"},
                    ],
                },
                {
                    "name": "notes",
                    "expected_type": "string",
                    "observed_inferred_type": "string",
                    "inferred_type": "string",
                    "type_rule": "string",
                    "null_count": 0,
                    "observed_non_null_count": 6,
                    "schema_drift": False,
                    "rerun_required": False,
                    "violations": [],
                },
            ],
            "month_anomalies": [
                {"bucket": "out-of-range-month", "count": 1, "examples": ["2026/13"]},
                {"bucket": "malformed-value", "count": 1, "examples": ["bad-month"]},
            ],
        },
    )
    (project_root / "null_summary.md").write_text(
        "\n".join(
            [
                "# Null Summary",
                "",
                "affected_columns: orders, revenue",
                "",
                "null_or_invalid_count:",
                "- orders: 2",
                "- revenue: 2",
                "",
                "null_heavy: true",
                "rerun_required: true",
                "reason: orders and revenue each meet the D2 null-heavy threshold (>= 2 rows by null-or-invalid-row-count); close as rerun, not done.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "sample_5.csv").write_text(
        "\n".join(
            [
                "month,region,orders,revenue,notes",
                "2026-01,NA,10,1250.50,ok",
                "2026-02,EU,,980.00,missing orders",
                "2026-03,APAC,7,NaN,null-like revenue",
                "2026/13,NA,abc,1500.00,bad month and bad orders",
                "bad-month,EU,14,bad,bad month and bad revenue",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_d3_artifacts(project_root: Path) -> None:
    (project_root / "data").mkdir(parents=True, exist_ok=True)
    (project_root / "data" / "customer_events.csv").write_text(
        "\n".join(
            [
                "customer_id,region_code,event_type,amount",
                "C-001,KR,signup,0",
                "C-002,US,purchase,125.00",
                "C-003,EU,purchase,89.50",
                "C-004,APAC,refund,21.00",
                "C-005,EU,signup,0",
                "C-006,APAC,purchase,210.25",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "normalized_customers.csv").write_text(
        "\n".join(
            [
                "customer_id,region_code,reporting_region,event_type,amount,mapping_status",
                "C-001,KR,Korea,signup,0,resolved",
                "C-002,US,United States,purchase,125.00,resolved",
                "C-003,EU,,purchase,89.50,operator_decision_required",
                "C-004,APAC,,refund,21.00,operator_decision_required",
                "C-005,EU,,signup,0,operator_decision_required",
                "C-006,APAC,,purchase,210.25,operator_decision_required",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "data_profile.md").write_text(
        "\n".join(
            [
                "# Data Profile",
                "",
                "- source_path: data/customer_events.csv",
                "- normalized_output: normalized_customers.csv",
                "- resolved_region_codes: KR, US",
                "- ambiguous_region_codes: EU, APAC",
                "- ambiguous_row_count: 4",
                "- executable_slice: apply confirmed mappings and isolate ambiguous rows",
                "- blocked_slice: operator-owned reporting_region mapping for EU/APAC",
                "- branch: manual_followup",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "business_rule_questions.md").write_text(
        "\n".join(
            [
                "# Business Rule Questions",
                "",
                "- Should `EU` map to a single `Europe` reporting region or be split by country before reporting?",
                "- Should `APAC` map to `Asia Pacific`, or should refunds be excluded until a regional owner confirms treatment?",
                "- Should unresolved region rows be withheld from the final dashboard or carried with blank `reporting_region`?",
                "- operator_decision: required before done; keep as manual follow-up.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (project_root / "sample_ambiguous_rows.csv").write_text(
        "\n".join(
            [
                "customer_id,region_code,event_type,amount,reason",
                "C-003,EU,purchase,89.50,reporting_region mapping not confirmed",
                "C-004,APAC,refund,21.00,reporting_region mapping not confirmed",
                "C-005,EU,signup,0,reporting_region mapping not confirmed",
                "C-006,APAC,purchase,210.25,reporting_region mapping not confirmed",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _approved_planning_fields() -> Dict[str, Any]:
    return {
        "phase1_current_provider": "codex",
        "phase1_current_planner": "codex",
        "phase1_current_critic": "claude",
        "plan_critic": {"approved": True, "issues": [], "recommendations": []},
        "plan_review_count": 3,
        "plan_convergence_status": "ready",
        "plan_gate_passed": True,
    }


def _manual_followup_ready_checkpoint(reason: str) -> Dict[str, Any]:
    return {
        "phase_checkpoint_status": "active",
        "phase_checkpoint_current_phase": "verify",
        "phase_checkpoint_summary": "status=active | current=verify | plan=done | implement=done | verify=active | handoff=ready",
        "phase_checkpoint_rows": [
            "plan=done|note=approved followup plan",
            "implement=done|note=execution slice is available for followup",
            f"verify=active|note={reason[:120]}",
            "handoff=ready|note=manual remainder remains operator-owned",
        ],
    }


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
        **_approved_planning_fields(),
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
            "subtasks": [
                {
                    "id": "S1",
                    "owner_role": "Codex-Reviewer",
                    "title": "Review evidence rerun",
                    "goal": "refresh canonical diff, severity rationale, test gaps, and uncertainties",
                },
            ],
            "meta": {
                "phase1_role_preset": "review",
                "phase2_team_preset": "review",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L1", "role": "Codex-Reviewer", "kind": "review_execution", "subtask_ids": ["S1"]},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Reviewer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {"lane_id": "L1", "role": "Codex-Reviewer", "kind": "review_execution", "subtask_ids": ["S1"], "outputs": ["review_report"]},
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


def _b2_task(now: str) -> Dict[str, Any]:
    return {
        "request_id": "REQ-B2-001",
        "short_id": "T-501",
        "alias": "build-rerun",
        "prompt": B2_REQUEST_TEXT,
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
        "roles": ["Codex-Dev", "Claude-Reviewer"],
        "verifier_roles": ["Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "build",
        "phase2_team_preset": "build",
        **_approved_planning_fields(),
        "execution_brief_status": "executable",
        "execution_brief_summary": "executable | do=src/session.js,tests/session.test.js,report.md | blocked=-",
        "execution_brief_executable_slice": [
            "src/session.js",
            "tests/session.test.js",
            "report.md",
        ],
        "execution_brief_blocked_slice": [],
        "execution_brief_operator_decision": "",
        "followup_brief_status": "none",
        "reentry_rails_summary": "retry=ready exec=L1 review=R1 | followup=none | bg=-",
        "plan": {
            "summary": "build | session expiry fix -> regression test -> report evidence | review lane validates session cleanup and test proof",
            "subtasks": [
                {
                    "id": "S1",
                    "owner_role": "Codex-Dev",
                    "title": "Build evidence rerun",
                    "goal": "refresh session expiry fix, regression test, and report evidence",
                },
            ],
            "meta": {
                "phase1_role_preset": "build",
                "phase2_team_preset": "build",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L1", "role": "Codex-Dev", "kind": "implementation", "subtask_ids": ["S1"]},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Dev",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {"lane_id": "L1", "role": "Codex-Dev", "kind": "implementation", "subtask_ids": ["S1"], "outputs": ["work_result", "handoff_doc"]},
                    ],
                    "review_lanes": [
                        {"lane_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"], "outputs": ["reviewer_note"]},
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L1",
                    "role": "Codex-Dev",
                    "status": "done",
                    "subtask_ids": ["S1"],
                    "touched_files": ["src/session.js", "tests/session.test.js", "report.md"],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "failed",
                    "depends_on": ["L1"],
                    "reason": "session cleanup evidence stayed incomplete; rerun the primary build lane before closing",
                    "verdict": "retry",
                    "action": "retry",
                    "touched_files": ["report.md"],
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
            "reason": "recheck build lane first",
            "rerun_execution_lane_ids": ["L1"],
            "rerun_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "retry",
            "backend_contract": "build_rerun",
            "backend_contract_note": "rerun should stay on the primary build lane with verifier follow-up",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O5",
            "task_short_id": "T-501",
        },
        "created_at": now,
        "updated_at": now,
    }


def _b3_task(now: str) -> Dict[str, Any]:
    return {
        "request_id": "REQ-B3-001",
        "short_id": "T-601",
        "alias": "build-manual-followup",
        "prompt": B3_REQUEST_TEXT,
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
        "roles": ["Codex-Dev", "Claude-Reviewer"],
        "verifier_roles": ["Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "build",
        "phase2_team_preset": "build",
        **_approved_planning_fields(),
        "execution_brief_status": "partially_executable",
        "execution_brief_summary": (
            "partially_executable | do=tests/session.test.js,report.md | "
            "blocked=operator-owned release acceptance"
        ),
        "execution_brief_executable_slice": [
            "tests/session.test.js",
            "report.md",
        ],
        "execution_brief_blocked_slice": [
            "release acceptance decision",
            "release note wording",
        ],
        "execution_brief_operator_decision": "operator owns final release acceptance and wording",
        "followup_brief_status": "partially_executable",
        "followup_brief_summary": "partially_executable | execution=L2 | review=R1",
        "followup_brief_execution_lane_ids": ["L2"],
        "followup_brief_review_lane_ids": ["R1"],
        "followup_brief_reason": (
            "operator owns release acceptance; build follow-up may rerun the evidence lane only"
        ),
        **_manual_followup_ready_checkpoint(
            "operator owns release acceptance; build follow-up may rerun the evidence lane only"
        ),
        "reentry_rails_summary": "retry=none | followup=partially_executable exec=L2 review=R1",
        "plan": {
            "summary": (
                "build | session regression evidence lane L2 may run while release acceptance "
                "and wording remain manual in R1"
            ),
            "subtasks": [
                {
                    "id": "S2",
                    "owner_role": "Codex-Dev",
                    "title": "Build manual followup evidence",
                    "goal": "refresh session regression evidence while release acceptance remains manual",
                },
            ],
            "meta": {
                "phase1_role_preset": "build",
                "phase2_team_preset": "build",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L2", "role": "Codex-Dev", "kind": "implementation_followup", "subtask_ids": ["S2"]},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L2"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Dev",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {
                            "lane_id": "L2",
                            "role": "Codex-Dev",
                            "kind": "implementation_followup",
                            "subtask_ids": ["S2"],
                            "outputs": ["session_regression_evidence", "handoff_report"],
                        },
                    ],
                    "review_lanes": [
                        {
                            "lane_id": "R1",
                            "role": "Claude-Reviewer",
                            "kind": "verifier",
                            "depends_on": ["L2"],
                            "outputs": ["release_acceptance_note"],
                        },
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L2",
                    "role": "Codex-Dev",
                    "status": "blocked",
                    "subtask_ids": ["S2"],
                    "touched_files": ["tests/session.test.js", "report.md"],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "blocked",
                    "depends_on": ["L2"],
                    "reason": "operator keeps release acceptance and wording; execute only the evidence lane",
                    "verdict": "manual_followup",
                    "action": "manual_followup",
                    "touched_files": ["report.md"],
                }
            ],
            "summary": {
                "execution": {"blocked": 1},
                "review": {"blocked": 1},
                "review_verdicts": {"manual_followup": 1},
            },
        },
        "exec_critic": {
            "verdict": "intervention",
            "action": "manual_followup",
            "reason": "legacy critic reason only; FollowupBrief owns the follow-up lane ids",
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "manual_followup",
            "backend_contract": "build_manual_followup",
            "backend_contract_note": (
                "followup execute is limited to build evidence lane L2 while release review lane R1 stays manual"
            ),
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O6",
            "task_short_id": "T-601",
        },
        "created_at": now,
        "updated_at": now,
    }


def _d2_task(now: str) -> Dict[str, Any]:
    contract = _d2_request_contract()
    brief = build_execution_brief(contract)
    return {
        "request_id": "REQ-D2-001",
        "short_id": "T-701",
        "alias": "data-rerun",
        "prompt": D2_REQUEST_TEXT,
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
        "roles": ["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
        "verifier_roles": ["Codex-Reviewer", "Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "data",
        "phase2_team_preset": "data",
        **_approved_planning_fields(),
        **request_contract_metadata(contract),
        "execution_brief_status": brief.get("status", "executable"),
        "execution_brief_summary": brief.get(
            "summary",
            "executable | do=normalized.csv,schema_report.json,null_summary.md,sample_5.csv",
        ),
        "execution_brief_executable_slice": list(
            brief.get("executable_slice")
            or ["normalized.csv", "schema_report.json", "null_summary.md", "sample_5.csv"]
        ),
        "execution_brief_blocked_slice": list(brief.get("blocked_slice") or []),
        "execution_brief_operator_decision": "",
        "followup_brief_status": "none",
        "reentry_rails_summary": "retry=ready exec=L1 review=R1 | followup=none | bg=-",
        "plan": {
            "summary": "data | normalize monthly csv -> schema/null evidence -> rerun branch on null-heavy threshold",
            "subtasks": [
                {
                    "id": "S1",
                    "owner_role": "DataEngineer",
                    "title": "Normalize monthly CSV",
                    "goal": "write normalized.csv from data/monthly_raw.csv while preserving invalid month rows",
                    "acceptance": ["normalized.csv exists"],
                },
                {
                    "id": "S2",
                    "owner_role": "DataEngineer",
                    "title": "Write schema_report.json",
                    "goal": "capture orders/revenue schema expectations, drift, rerun flags, and violations",
                    "acceptance": ["schema_report.json exists"],
                },
                {
                    "id": "S3",
                    "owner_role": "DataEngineer",
                    "title": "Write null_summary.md",
                    "goal": "summarize null_summary.md for orders/revenue null-heavy rerun evidence",
                    "acceptance": ["null_summary.md exists"],
                },
                {
                    "id": "S4",
                    "owner_role": "DataEngineer",
                    "title": "Export sample_5.csv",
                    "goal": "write the first five transformed data rows for review",
                    "acceptance": ["sample_5.csv exists"],
                },
            ],
            "meta": {
                "worker_roles": ["DataEngineer", "Codex-Reviewer", "Claude-Reviewer"],
                "phase1_role_preset": "data",
                "phase2_team_preset": "data",
                "request_contract": contract,
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L1", "role": "DataEngineer", "kind": "data_transform", "subtask_ids": ["S1", "S2", "S3", "S4"]},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L1"]},
                        {"group_id": "R2", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L1"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "DataEngineer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {
                            "lane_id": "L1",
                            "role": "DataEngineer",
                            "kind": "data_transform",
                            "subtask_ids": ["S1", "S2", "S3", "S4"],
                            "outputs": ["normalized.csv", "schema_report.json", "null_summary.md", "sample_5.csv"],
                        },
                    ],
                    "review_lanes": [
                        {
                            "lane_id": "R1",
                            "role": "Codex-Reviewer",
                            "kind": "verifier",
                            "depends_on": ["L1"],
                            "outputs": ["null_summary.md", "schema_report.json"],
                        },
                        {
                            "lane_id": "R2",
                            "role": "Claude-Reviewer",
                            "kind": "verifier",
                            "depends_on": ["L1"],
                            "outputs": ["rerun_branch_decision"],
                        },
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L1",
                    "role": "DataEngineer",
                    "status": "done",
                    "subtask_ids": ["S1", "S2", "S3", "S4"],
                    "touched_files": [
                        "normalized.csv",
                        "schema_report.json",
                        "null_summary.md",
                        "sample_5.csv",
                    ],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Codex-Reviewer",
                    "kind": "verifier",
                    "status": "failed",
                    "depends_on": ["L1"],
                    "reason": "null_summary.md declares orders/revenue null-heavy threshold >=2; branch must remain rerun",
                    "verdict": "retry",
                    "action": "retry",
                    "touched_files": ["null_summary.md", "schema_report.json"],
                },
                {
                    "lane_id": "R2",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "failed",
                    "depends_on": ["L1"],
                    "reason": "quality gate forbids done when null-heavy evidence is true",
                    "verdict": "retry",
                    "action": "retry",
                    "touched_files": ["null_summary.md"],
                },
            ],
            "summary": {
                "execution": {"done": 1},
                "review": {"failed": 2},
                "review_verdicts": {"retry": 2},
            },
        },
        "exec_critic": {
            "verdict": "retry",
            "action": "retry",
            "reason": "D2 null-heavy evidence is concrete for orders/revenue; rerun the data lane instead of closing done",
            "rerun_execution_lane_ids": ["L1"],
            "rerun_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "retry",
            "backend_contract": "data_rerun",
            "backend_contract_note": "null_summary.md carries affected_columns/null_or_invalid_count/null_heavy/rerun_required/reason for the rerun branch",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O7",
            "task_short_id": "T-701",
        },
        "created_at": now,
        "updated_at": now,
    }


def _d3_task(now: str) -> Dict[str, Any]:
    contract = {
        "type": "data",
        "status": "complete",
        "summary": (
            "data | manual_followup | outputs=normalized_customers.csv,"
            "data_profile.md,business_rule_questions.md,sample_ambiguous_rows.csv"
        ),
        "required_outputs": [
            "normalized_customers.csv",
            "data_profile.md",
            "business_rule_questions.md",
            "sample_ambiguous_rows.csv",
        ],
        "fields": {
            "source_path": "data/customer_events.csv",
            "confirmed_region_mappings": {"KR": "Korea", "US": "United States"},
            "operator_owned_region_codes": ["EU", "APAC"],
            "manual_followup_reason": "reporting_region mapping requires business-rule judgment",
        },
        "artifact_contracts": {
            "normalized_customers": {"path": "normalized_customers.csv", "format": "csv"},
            "data_profile": {"path": "data_profile.md", "format": "markdown"},
            "business_rule_questions": {
                "path": "business_rule_questions.md",
                "format": "markdown",
            },
            "sample_ambiguous_rows": {"path": "sample_ambiguous_rows.csv", "format": "csv"},
        },
    }
    return {
        "request_id": "REQ-D3-001",
        "short_id": "T-901",
        "alias": "data-manual-followup",
        "prompt": D3_REQUEST_TEXT,
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
        "roles": ["DataEngineer", "Codex-Reviewer"],
        "verifier_roles": ["Codex-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "data",
        "phase2_team_preset": "data",
        **_approved_planning_fields(),
        "execution_brief_status": "partially_executable",
        "execution_brief_summary": (
            "partially_executable | do=normalized_customers.csv,data_profile.md,"
            "business_rule_questions.md,sample_ambiguous_rows.csv | "
            "blocked=operator-owned reporting_region mapping for EU/APAC"
        ),
        "execution_brief_executable_slice": [
            "normalized_customers.csv",
            "data_profile.md",
            "business_rule_questions.md",
            "sample_ambiguous_rows.csv",
        ],
        "execution_brief_blocked_slice": [
            "operator-owned reporting_region mapping for EU",
            "operator-owned reporting_region mapping for APAC",
        ],
        "execution_brief_operator_decision": (
            "operator must decide EU/APAC reporting_region mapping before final done"
        ),
        "followup_brief_status": "partially_executable",
        "followup_brief_summary": "partially_executable | execution=L2 | review=R1",
        "followup_brief_execution_lane_ids": ["L2"],
        "followup_brief_review_lane_ids": ["R1"],
        "followup_brief_reason": (
            "data profiling can rerun, but EU/APAC reporting_region mapping remains operator-owned"
        ),
        **_manual_followup_ready_checkpoint(
            "data profiling can rerun, but EU/APAC reporting_region mapping remains operator-owned"
        ),
        "reentry_rails_summary": "retry=none | followup=partially_executable exec=L2 review=R1",
        "plan": {
            "summary": (
                "data | confirmed region mapping can be applied while EU/APAC reporting rules "
                "remain manual in R1"
            ),
            "subtasks": [
                {
                    "id": "S2",
                    "owner_role": "DataEngineer",
                    "title": "Data manual followup evidence",
                    "goal": (
                        "refresh confirmed region mapping outputs and isolate ambiguous EU/APAC rows "
                        "without choosing the business rule"
                    ),
                    "acceptance": [
                        "normalized_customers.csv exists",
                        "business_rule_questions.md exists",
                        "sample_ambiguous_rows.csv exists",
                    ],
                },
            ],
            "meta": {
                "worker_roles": ["DataEngineer", "Codex-Reviewer"],
                "phase1_role_preset": "data",
                "phase2_team_preset": "data",
                "request_contract": contract,
                "phase2_team_spec": {
                    "execution_groups": [
                        {
                            "group_id": "L2",
                            "role": "DataEngineer",
                            "kind": "data_followup",
                            "subtask_ids": ["S2"],
                        },
                    ],
                    "review_groups": [
                        {
                            "group_id": "R1",
                            "role": "Codex-Reviewer",
                            "kind": "business_rule_remainder",
                            "depends_on": ["L2"],
                        },
                    ],
                    "critic_role": "Codex-Reviewer",
                    "integration_role": "DataEngineer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {
                            "lane_id": "L2",
                            "role": "DataEngineer",
                            "kind": "data_followup",
                            "subtask_ids": ["S2"],
                            "outputs": [
                                "normalized_customers.csv",
                                "data_profile.md",
                                "business_rule_questions.md",
                                "sample_ambiguous_rows.csv",
                            ],
                        },
                    ],
                    "review_lanes": [
                        {
                            "lane_id": "R1",
                            "role": "Codex-Reviewer",
                            "kind": "business_rule_remainder",
                            "depends_on": ["L2"],
                            "outputs": ["operator_region_mapping_decision"],
                        },
                    ],
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L2",
                    "role": "DataEngineer",
                    "status": "blocked",
                    "subtask_ids": ["S2"],
                    "reason": "confirmed mappings can run, but EU/APAC rows must remain isolated",
                    "touched_files": [
                        "normalized_customers.csv",
                        "data_profile.md",
                        "business_rule_questions.md",
                        "sample_ambiguous_rows.csv",
                    ],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Codex-Reviewer",
                    "kind": "business_rule_remainder",
                    "status": "blocked",
                    "depends_on": ["L2"],
                    "reason": "operator must decide EU/APAC reporting_region mapping before done",
                    "verdict": "manual_followup",
                    "action": "manual_followup",
                    "touched_files": ["business_rule_questions.md"],
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
            "reason": "EU/APAC reporting_region mapping is a business-rule decision; keep done blocked",
            "manual_followup_execution_lane_ids": ["L2"],
            "manual_followup_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "manual_followup",
            "backend_contract": "data_manual_followup",
            "backend_contract_note": (
                "followup execute is limited to confirmed data profiling while EU/APAC mapping stays manual"
            ),
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O9",
            "task_short_id": "T-901",
        },
        "created_at": now,
        "updated_at": now,
    }


def _m2_task(now: str) -> Dict[str, Any]:
    contract = _m2_request_contract()
    brief = build_execution_brief(contract)
    return {
        "request_id": "REQ-M2-001",
        "short_id": "T-801",
        "alias": "mixed-rerun",
        "prompt": M2_REQUEST_TEXT,
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
        "roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
        "verifier_roles": ["Codex-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "verification",
        "phase1_current_round": 3,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "mixed",
        "phase2_team_preset": "mixed",
        **_approved_planning_fields(),
        **request_contract_metadata(contract),
        "execution_brief_status": brief.get("status", "executable"),
        "execution_brief_summary": brief.get(
            "summary",
            "executable | do=work_result,scope_inventory,handoff_doc,reviewer_note",
        ),
        "execution_brief_executable_slice": list(
            brief.get("executable_slice")
            or ["work_result", "scope_inventory", "handoff_doc", "reviewer_note"]
        ),
        "execution_brief_blocked_slice": list(brief.get("blocked_slice") or []),
        "execution_brief_operator_decision": "",
        "followup_brief_status": "none",
        "reentry_rails_summary": "retry=ready exec=L2 review=R1 | followup=none | bg=-",
        "plan": {
            "summary": (
                "mixed | implementation evidence is complete while handoff/reviewer evidence drift "
                "requires a writer-lane rerun"
            ),
            "subtasks": [
                {
                    "id": "S1",
                    "owner_role": "Codex-Dev",
                    "title": "Patch session_expired token cleanup",
                    "goal": "update src/session.js and tests/session.test.js with regression evidence",
                    "acceptance": ["src/session.js exists", "tests/session.test.js exists"],
                },
                {
                    "id": "S2",
                    "owner_role": "Codex-Writer",
                    "title": "Repair operator handoff evidence",
                    "goal": (
                        "rewrite docs/handoff/operator_handoff.md so changed files, validation status, "
                        "and test evidence match the implementation lane"
                    ),
                    "acceptance": ["docs/handoff/operator_handoff.md includes tests/session.test.js and test evidence"],
                },
            ],
            "meta": {
                "worker_roles": ["Codex-Dev", "Codex-Writer", "Codex-Reviewer"],
                "phase1_role_preset": "mixed",
                "phase2_team_preset": "mixed",
                "request_contract": contract,
                "phase2_team_spec": {
                    "execution_mode": "parallel",
                    "execution_groups": [
                        {"group_id": "L1", "role": "Codex-Dev", "kind": "implementation", "subtask_ids": ["S1"], "parallel": True},
                        {"group_id": "L2", "role": "Codex-Writer", "kind": "handoff", "subtask_ids": ["S2"], "parallel": True},
                    ],
                    "review_mode": "single",
                    "review_groups": [
                        {"group_id": "R1", "role": "Codex-Reviewer", "kind": "verifier", "depends_on": ["L2"]},
                    ],
                    "critic_role": "Codex-Reviewer",
                    "integration_role": "Codex-Dev",
                },
                "phase2_execution_plan": {
                    "execution_mode": "parallel",
                    "execution_lanes": [
                        {
                            "lane_id": "L1",
                            "role": "Codex-Dev",
                            "kind": "implementation",
                            "subtask_ids": ["S1"],
                            "outputs": ["work_result", "scope_inventory"],
                            "parallel": True,
                        },
                        {
                            "lane_id": "L2",
                            "role": "Codex-Writer",
                            "kind": "handoff",
                            "subtask_ids": ["S2"],
                            "outputs": ["handoff_doc"],
                            "parallel": True,
                        },
                    ],
                    "review_mode": "single",
                    "review_lanes": [
                        {
                            "lane_id": "R1",
                            "role": "Codex-Reviewer",
                            "kind": "verifier",
                            "depends_on": ["L2"],
                            "outputs": ["reviewer_note", "handoff_parity_decision"],
                        },
                    ],
                    "parallel_workers": True,
                    "parallel_reviews": False,
                },
            },
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L1",
                    "role": "Codex-Dev",
                    "status": "done",
                    "subtask_ids": ["S1"],
                    "touched_files": [
                        "src/session.js",
                        "tests/session.test.js",
                        "docs/analysis/auth_scope_inventory.md",
                    ],
                },
                {
                    "lane_id": "L2",
                    "role": "Codex-Writer",
                    "status": "failed",
                    "subtask_ids": ["S2"],
                    "reason": "operator_handoff.md omits tests/session.test.js and validation evidence from the implementation lane",
                    "touched_files": ["docs/handoff/operator_handoff.md"],
                },
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Codex-Reviewer",
                    "kind": "verifier",
                    "status": "failed",
                    "depends_on": ["L2"],
                    "reason": "handoff/reviewer evidence drift is isolated to writer lane L2; implementation lane L1 should not rerun",
                    "verdict": "retry",
                    "action": "retry",
                    "touched_files": ["docs/handoff/operator_handoff.md", "docs/reviews/reviewer_note.md"],
                }
            ],
            "summary": {
                "execution": {"done": 1, "failed": 1},
                "review": {"failed": 1},
                "review_verdicts": {"retry": 1},
            },
        },
        "exec_critic": {
            "verdict": "retry",
            "action": "retry",
            "reason": "mixed handoff evidence drift is writer-owned; rerun L2 with reviewer R1 and keep implementation lane L1 closed",
            "rerun_execution_lane_ids": ["L2"],
            "rerun_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "retry",
            "backend_contract": "mixed_rerun",
            "backend_contract_note": "rerun is scoped to the writer/handoff lane and verifier, not the completed implementation lane",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O8",
            "task_short_id": "T-801",
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
        **_approved_planning_fields(),
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
        **_manual_followup_ready_checkpoint(
            "operator keeps the review slice while execution-only followup may proceed"
        ),
        "reentry_rails_summary": "retry=none | followup=partially_executable exec=L2 review=R1",
        "plan": {
            "summary": "review | followup execute evidence lane L2 remains runnable while review wording stays manual in R1",
            "subtasks": [
                {
                    "id": "S2",
                    "owner_role": "Codex-Reviewer",
                    "title": "Review followup evidence",
                    "goal": "refresh followup scope evidence while review wording remains manual",
                },
            ],
            "meta": {
                "phase1_role_preset": "review",
                "phase2_team_preset": "review",
                "phase2_team_spec": {
                    "execution_groups": [
                        {"group_id": "L2", "role": "Codex-Reviewer", "kind": "review_execution", "subtask_ids": ["S2"]},
                    ],
                    "review_groups": [
                        {"group_id": "R1", "role": "Claude-Reviewer", "kind": "verifier", "depends_on": ["L2"]},
                    ],
                    "critic_role": "Claude-Reviewer",
                    "integration_role": "Codex-Reviewer",
                },
                "phase2_execution_plan": {
                    "execution_lanes": [
                        {"lane_id": "L2", "role": "Codex-Reviewer", "kind": "review_execution", "subtask_ids": ["S2"], "outputs": ["review_report"]},
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


def _r4_external_task(now: str, runner_target: str) -> Dict[str, Any]:
    return {
        "request_id": "REQ-R4-001",
        "short_id": "T-401",
        "alias": "review-external-rail",
        "prompt": R4_REQUEST_TEXT,
        "mode": "dispatch",
        "status": "running",
        "stage": "execution",
        "stages": {
            "intake": "done",
            "planning": "done",
            "execution": "running",
            "verification": "pending",
            "integration": "pending",
            "close": "pending",
        },
        "roles": ["Codex-Reviewer", "Claude-Reviewer"],
        "verifier_roles": ["Claude-Reviewer"],
        "require_verifier": True,
        "phase1_mode": "ensemble",
        "phase1_rounds": 3,
        "phase1_providers": ["codex", "claude"],
        "phase1_current_phase": "execution",
        "phase1_current_round": 1,
        "phase1_current_total_rounds": 3,
        "phase1_role_preset": "review",
        "phase2_team_preset": "review",
        "execution_brief_status": "executable",
        "execution_brief_summary": f"executable | do=lane-scoped external reentry via {runner_target} | blocked=-",
        "execution_brief_executable_slice": [
            "review_report.md",
        ],
        "execution_brief_blocked_slice": [],
        "execution_brief_operator_decision": "",
        "followup_brief_status": "none",
        "reentry_rails_summary": f"retry=ready exec=L1 review=R1 | followup=none | bg=running/{runner_target}",
        "plan": {
            "summary": f"review | external background rail over {runner_target} keeps rerun visible through handoff, ack, and result",
        },
        "lane_states": {
            "execution": [
                {
                    "lane_id": "L1",
                    "role": "Codex-Reviewer",
                    "status": "running",
                    "subtask_ids": ["S1"],
                    "touched_files": ["review_report.md"],
                }
            ],
            "review": [
                {
                    "lane_id": "R1",
                    "role": "Claude-Reviewer",
                    "kind": "verifier",
                    "status": "pending",
                    "depends_on": ["L1"],
                    "verdict": "retry",
                    "action": "retry",
                    "reason": "external rail still needs pickup/result visibility before closure",
                    "touched_files": ["review_report.md"],
                }
            ],
            "summary": {
                "execution": {"running": 1},
                "review": {"pending": 1},
                "review_verdicts": {"retry": 1},
            },
        },
        "exec_critic": {
            "verdict": "retry",
            "action": "retry",
            "reason": f"{runner_target} handoff is active; await pickup ack and result before deciding the next rerun action",
            "rerun_execution_lane_ids": ["L1"],
            "rerun_review_lane_ids": ["R1"],
        },
        "result": {
            "backend": "autogen_core",
            "backend_profile": "sandbox",
            "backend_verdict": "retry",
            "backend_contract": "review_external_background",
            "backend_contract_note": f"external runner {runner_target} should remain operator-visible through handoff, ack, and result",
        },
        "context": {
            "project_key": "alpha",
            "project_alias": "O4",
            "task_short_id": "T-401",
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
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated review rerun live rehearsal",
    )
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


def seed_b2_build_rerun_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated build rerun live rehearsal",
    )
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_b2_task(now), "REQ-B2-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O5",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated build rerun live rehearsal",
        "last_request_id": "REQ-B2-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-B2-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "B2",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O5",
        "request_id": "REQ-B2-001",
        "task_ref": "T-501",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "preflight_commands": [
            "/orch status O5",
            "/task T-501",
            "/offdesk review O5",
        ],
        "trigger_command": "/retry T-501 lane L1",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-B2-001",
            "runtime_detail": "/control/runtimes/O5",
        },
    }


def seed_b3_build_manual_followup_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated build manual followup live rehearsal",
    )
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_b3_task(now), "REQ-B3-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O6",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated build manual followup live rehearsal",
        "last_request_id": "REQ-B3-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-B3-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "B3",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O6",
        "request_id": "REQ-B3-001",
        "task_ref": "T-601",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "preflight_commands": [
            "/orch status O6",
            "/task T-601",
            "/followup T-601",
            "/offdesk review O6",
        ],
        "trigger_command": "/followup-exec T-601 lane L2",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-B3-001",
            "runtime_detail": "/control/runtimes/O6",
        },
    }


def seed_d2_data_rerun_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated data rerun live rehearsal",
    )
    _write_d2_artifacts(project_root)
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_d2_task(now), "REQ-D2-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O7",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated data rerun live rehearsal",
        "last_request_id": "REQ-D2-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-D2-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "D2",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O7",
        "request_id": "REQ-D2-001",
        "task_ref": "T-701",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "artifact_paths": [
            "data/monthly_raw.csv",
            "normalized.csv",
            "schema_report.json",
            "null_summary.md",
            "sample_5.csv",
        ],
        "preflight_commands": [
            "/orch status O7",
            "/task T-701",
            "/offdesk review O7",
        ],
        "trigger_command": "/retry T-701 lane L1",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-D2-001",
            "runtime_detail": "/control/runtimes/O7",
        },
    }


def seed_d3_data_manual_followup_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated data manual followup live rehearsal",
    )
    _write_d3_artifacts(project_root)
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_d3_task(now), "REQ-D3-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O9",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated data manual followup live rehearsal",
        "last_request_id": "REQ-D3-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-D3-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "D3",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O9",
        "request_id": "REQ-D3-001",
        "task_ref": "T-901",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "artifact_paths": [
            "data/customer_events.csv",
            "normalized_customers.csv",
            "data_profile.md",
            "business_rule_questions.md",
            "sample_ambiguous_rows.csv",
        ],
        "preflight_commands": [
            "/orch status O9",
            "/task T-901",
            "/followup T-901",
            "/offdesk review O9",
        ],
        "trigger_command": "/followup-exec T-901 lane L2",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-D3-001",
            "runtime_detail": "/control/runtimes/O9",
        },
    }


def seed_m2_mixed_rerun_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "local_tmux",
    local_tmux_slot_limit: int = 1,
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated mixed rerun live rehearsal",
    )
    _write_m2_artifacts(project_root)
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_m2_task(now), "REQ-M2-001")
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O8",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated mixed rerun live rehearsal",
        "last_request_id": "REQ-M2-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": local_tmux_slot_limit,
        "background_runner_slot_limits": {
            "local_tmux": local_tmux_slot_limit,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-M2-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "M2",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O8",
        "request_id": "REQ-M2-001",
        "task_ref": "T-801",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "artifact_paths": [
            "src/session.js",
            "tests/session.test.js",
            "docs/analysis/auth_scope_inventory.md",
            "docs/handoff/operator_handoff.md",
            "docs/reviews/reviewer_note.md",
        ],
        "preflight_commands": [
            "/orch status O8",
            "/task T-801",
            "/offdesk review O8",
        ],
        "trigger_command": "/retry T-801 lane L2",
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-M2-001",
            "runtime_detail": "/control/runtimes/O8",
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
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated review followup execute live rehearsal",
    )
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


def seed_r4_external_background_runtime(
    control_root: Path,
    *,
    run_lock_mode: str = "test_only",
    runner_target: str = "github_runner",
) -> Dict[str, Any]:
    control_root = Path(control_root).expanduser().resolve()
    team_dir, project_root, project_team_dir = _prepare_project_layout(
        control_root,
        overview="isolated external background rail live rehearsal",
    )
    manager_state_file = team_dir / "orch_manager_state.json"
    now = _now_iso()

    queue_path = project_team_dir / "background_runs.json"
    launch_spec = build_external_runner_gateway_command_launch_spec(
        runner_target=runner_target,
        request_id="REQ-R4-001",
        project_key="alpha",
        project_root=str(project_root),
        team_dir=str(project_team_dir),
        manager_state_file=str(manager_state_file),
        command_text="/retry T-401 lane L1",
        simulate_chat_id="939062873",
        launch_mode="dashboard_retry",
        source_surface="dashboard_retry",
        created_by="dashboard:control",
    )
    ticket = build_background_run_ticket(
        ticket_id="BGT-R4-001",
        request_id="REQ-R4-001",
        project_key="alpha",
        execution_brief_status="executable",
        runner_target=runner_target,
        launch_mode="dashboard_retry",
        created_at=now,
        created_by="dashboard:control",
        source_surface="dashboard_retry",
        status="queued",
        launch_spec=launch_spec,
    )
    upsert_background_run_ticket(queue_path, ticket, now_iso=lambda: now)
    handoff = emit_external_background_handoff(
        queue_path=queue_path,
        ticket_id="BGT-R4-001",
        runner_target=runner_target,
        now_iso=lambda: now,
        claimed_by="dashboard:control",
        source_surface="dashboard_retry",
        launch_mode="dashboard_retry",
    )

    state = runtime_read.default_manager_state(control_root, team_dir)
    state["active"] = "alpha"
    state.pop("project_lock", None)
    task = runtime_read.sanitize_task_record(_r4_external_task(now, runner_target), "REQ-R4-001")
    task["background_run_ticket_id"] = str(handoff.get("ticket_id", "")).strip()
    task["background_run_status"] = str(handoff.get("status", "")).strip()
    task["background_run_runner_target"] = str(handoff.get("runner_target", "")).strip()
    task["background_run_launch_mode"] = str(handoff.get("launch_mode", "")).strip()
    task["background_run_runtime_handle"] = str(handoff.get("runtime_handle", "")).strip()
    task["background_run_runtime_summary"] = str(handoff.get("runtime_summary", "")).strip()
    task["background_run_evidence_bundle"] = str(handoff.get("evidence_bundle", "")).strip()
    task["background_run_evidence_artifacts"] = list(handoff.get("evidence_artifacts") or [])
    task["background_run_external_phase"] = "handoff_emitted"
    task["background_run_external_note"] = str(handoff.get("runtime_handle", "")).strip()
    task["result"]["background_run_status"] = str(handoff.get("status", "")).strip()
    task["result"]["background_run_runner_target"] = str(handoff.get("runner_target", "")).strip()
    task["result"]["background_run_ticket_id"] = str(handoff.get("ticket_id", "")).strip()
    task["result"]["background_run_evidence_bundle"] = str(handoff.get("evidence_bundle", "")).strip()
    state["projects"]["alpha"] = {
        "name": "alpha",
        "display_name": "Alpha",
        "project_alias": "O4",
        "project_root": str(project_root),
        "team_dir": str(project_team_dir),
        "overview": "isolated external background rail live rehearsal",
        "last_request_id": "REQ-R4-001",
        "background_runner_target": runner_target,
        "run_lock_mode": run_lock_mode,
        "background_runner_slot_limit": 1,
        "background_runner_slot_limits": {
            "local_tmux": 1,
            "github_runner": 1,
            "remote_worker": 1,
        },
        "tasks": {"REQ-R4-001": task},
    }
    _write_json(manager_state_file, state)

    return {
        "scenario": "R4",
        "control_root": str(control_root),
        "team_dir": str(team_dir),
        "manager_state_file": str(manager_state_file),
        "project_root": str(project_root),
        "project_alias": "O4",
        "request_id": "REQ-R4-001",
        "task_ref": "T-401",
        "run_lock_mode": run_lock_mode,
        "background_runner_target": runner_target,
        "background_runner_slot_limits": state["projects"]["alpha"]["background_runner_slot_limits"],
        "reentry_rails_summary": task.get("reentry_rails_summary", ""),
        "preflight_commands": [
            "/orch status O4",
            "/orch bgx-status O4",
            "/offdesk review O4",
        ],
        "trigger_commands": [
            "/orch bgx-emit-ack O4",
            "/orch bgx-emit-result O4 completed",
        ],
        "dashboard_paths": {
            "task_detail": "/control/tasks/by-request/REQ-R4-001",
            "runtime_detail": "/control/runtimes/O4",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed an isolated live-rehearsal runtime without launching work.")
    parser.add_argument("--scenario", choices=["b2", "b3", "d2", "d3", "m2", "r2", "r3-execute", "r4"], default="r2")
    parser.add_argument("--control-root", required=True)
    parser.add_argument("--run-lock-mode", choices=["open", "test_only"], default="test_only")
    parser.add_argument("--runner-target", choices=["local_tmux", "github_runner", "remote_worker"], default="local_tmux")
    parser.add_argument("--local-tmux-slot-limit", type=int, default=1)
    args = parser.parse_args()

    if args.scenario == "b2":
        payload = seed_b2_build_rerun_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "b3":
        payload = seed_b3_build_manual_followup_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "r2":
        payload = seed_r2_review_rerun_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "d2":
        payload = seed_d2_data_rerun_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "d3":
        payload = seed_d3_data_manual_followup_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target,
            local_tmux_slot_limit=max(1, int(args.local_tmux_slot_limit)),
        )
    elif args.scenario == "m2":
        payload = seed_m2_mixed_rerun_runtime(
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
    elif args.scenario == "r4":
        payload = seed_r4_external_background_runtime(
            Path(args.control_root),
            run_lock_mode=args.run_lock_mode,
            runner_target=args.runner_target if args.runner_target in {"github_runner", "remote_worker"} else "github_runner",
        )
    else:
        raise SystemExit(f"unsupported scenario: {args.scenario}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
