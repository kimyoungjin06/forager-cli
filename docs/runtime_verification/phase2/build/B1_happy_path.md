# Build B1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `B1`
- preset:
  - `build`
- branch_target:
  - `done`
- status:
  - `executed_blocked`
- executed_at:
  - `2026-03-27T20:09:20+09:00`
  - `2026-03-30T10:55:45+09:00`
  - `2026-03-30T11:06:20+09:00`
  - `2026-03-30T11:17:05+09:00`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `O1 default` during live run
  - visible registry alias `O2 demo-login-build` was added later for surface inspection

## 3. Expected Contract
- expected preset:
  - `build`
- expected lane shape:
  - execution:
    - implementation lane
  - review:
    - verifier/reviewer lane
- expected completion branch:
  - `done`
- expected evidence:
  - diff summary
  - test or verification evidence
  - impacted-component note

## 4. Runtime Evidence
- request_id:
  - `r_20260327200920_c3e0106c`
- request_id progression:
  - `r_20260327200920_c3e0106c`
    - `T-002`
    - `plan_gate_reason=S1/S3/S4가 Phase2 배치에 없다. Codex-Analyst는 worker_roles/team_roles에 없고 execution_groups/execution_lanes에는 E2(S2)만 정의되어 있어 before/after 로그와 review.md를 생성할 실행 주체가 없다. 동시에 R1/R2는 존재하지 않는 E1/E3에 의존하므로 dispatch 그래프가 깨져 있다.`
  - `r_20260330105545_374c1c18`
    - `T-003`
    - `plan_gate_reason=phase2_execution_plan.readonly=true가 S2/S3의 테스트·구현·artifacts 쓰기 요구와 충돌한다. 현재 명세대로 dispatch하면 실행 자체가 막힌다.`
  - `r_20260330110620_8bc5d1d7`
    - `T-004`
    - `plan_gate_reason=S4는 독립 리뷰를 요구하지만 phase2_execution_plan에서 S1~S4 전체를 Codex-Dev 단일 lane(E1)에 배정했다. 구현자와 독립 판정자가 동일해 수용 기준을 충족할 실행 구성이 없다.`
  - `r_20260330111705_c0b46401`
    - `T-005`
    - `plan_gate_reason=완료 조건이 실제 버그 지점을 고정하지 못한다. acceptance는 handleLoginFailure(session, reason)의 반환값만 규정하고 있어, 런타임에서 유지되는 세션 저장소/호출부 상태까지 정리해야 하는지 불명확하다. 이 상태로는 테스트가 통과해도 실제 로그인 실패 흐름의 세션 만료 누락이 남을 수 있다.`
- planning:
  - all runs used `phase1 ensemble rounds=3 providers=codex`
  - all runs remained `plan_gate_passed=false`
- stage progression:
  - planning:
    - `all executed runs completed 3 planner/critic rounds before block`
  - execution:
    - `not entered`
  - verification:
    - `not entered`
  - integration:
    - `not entered`
  - close:
    - `failed`
- critic/verifier verdict:
  - `critic issues remain after auto-replan`
- final branch:
  - `blocked`

## 5. Surface Evidence
- `/task` before visible project registration:
  - correct blocked task was visible for `T-002` under hidden/default runtime lineage
- `/monitor` before visible project registration:
  - showed `T-002` as `failed/close/blocked`
- `/offdesk review` after visible project registration:
  - visible project `O2 demo-login-build` was present, but it was flagged only for bootstrap/backlog issues because the actual task lineage still lived under hidden/default runtime state
- dashboard `Task Detail` after visible project registration:
  - reachable at `/control/tasks/by-request/r_20260327200920_c3e0106c`, but rendered a duplicated `manual_intervention` task under `O2` instead of the original blocked `O1/T-002` lineage
- dashboard `Recovery` after visible project registration:
  - runtime `O2 demo-login-build` appeared, but reflected bootstrap-only state rather than the original blocked build task

## 6. Result
- result:
  - `blocked`
- mismatch class:
  - `planning_drift`
  - `surface_drift`
  - `runtime_adapter_bug_fixed`
  - `readonly_contract_fixed`
  - `phase1_prompt_guardrail_fixed`
- mismatch notes:
  - initial live run first exposed a real runtime seam bug: `handle_text_message(...).log_event()` rejected `task_short_id`; this was fixed in `scripts/gateway/aoe_tg_message_handler.py`
  - first blocker after the seam fix was an invalid `build` Phase2 lane graph; contract normalization now repairs partial planner metadata so execution/review graph coverage is no longer the first failure
  - second blocker was `phase2_execution_plan.readonly=true` on a mutating build task; live dispatch planning now defaults to mutable execution unless `readonly` is explicitly requested
  - third blocker was a standalone `independent review` execution subtask; planner/critic prompts now explicitly forbid review/approval subtasks inside non-review execution plans
  - current blocker is narrower and more legitimate: acceptance still does not pin the actual runtime/session-storage failure mode strongly enough for a safe build completion verdict
  - later visible-project registration via `aoe orch add ... --set-active` introduced a second mismatch: selected task refs and task detail surfaces for `O2` drifted away from the original hidden/default task lineage and showed `manual_intervention` instead of the original planning gate block
- next fix:
  - strengthen planner acceptance guidance so build preset plans must name the persisted session-store or caller-visible state transition, not only the helper return value
  - consider adding a deterministic acceptance-floor for `build` tasks that mention login/session/auth expiry flows
  - investigate `orch add --set-active` task lineage copy/drift into visible project registry before using dashboard/task detail as canonical evidence for migrated runtimes

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/logs/gateway_events.jsonl`
- artifact refs:
  - `scripts/gateway/aoe_tg_message_handler.py`
  - `scripts/gateway/aoe_tg_orch_contract.py`
  - `scripts/gateway/aoe_tg_schema.py`
  - `scripts/gateway/aoe_tg_plan_ensemble.py`
  - `scripts/gateway/aoe_tg_control_plane.py`
  - `tests/gateway/test_gateway_module_surfaces.py`
  - `tests/gateway/test_phase1_planning.py`
  - `http://127.0.0.1:18765/control/history?q=planning_gate`
  - `http://127.0.0.1:18765/control/tasks/by-request/r_20260327200920_c3e0106c`
