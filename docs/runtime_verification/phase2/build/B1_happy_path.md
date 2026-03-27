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
- task_short_id:
  - `T-002`
- planning:
  - `phase1 ensemble rounds=3 providers=codex`
  - `plan_gate_passed=false`
  - `plan_gate_reason=S1/S3/S4가 Phase2 배치에 없다. Codex-Analyst는 worker_roles/team_roles에 없고 execution_groups/execution_lanes에는 E2(S2)만 정의되어 있어 before/after 로그와 review.md를 생성할 실행 주체가 없다. 동시에 R1/R2는 존재하지 않는 E1/E3에 의존하므로 dispatch 그래프가 깨져 있다.`
- stage progression:
  - planning:
    - `done after 3 planner/critic rounds`
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
- mismatch notes:
  - initial live run first exposed a real runtime seam bug: `handle_text_message(...).log_event()` rejected `task_short_id`; this was fixed in `scripts/gateway/aoe_tg_message_handler.py`
  - after the seam fix, the actual build preset happy-path still failed because phase1 planner/critic produced an invalid Phase2 lane graph for a `build` task
  - later visible-project registration via `aoe orch add ... --set-active` introduced a second mismatch: selected task refs and task detail surfaces for `O2` drifted away from the original hidden/default task lineage and showed `manual_intervention` instead of the original planning gate block
- next fix:
  - enforce valid Phase2 lane graph generation for `build` preset before marking B1 happy-path as passing
  - investigate `orch add --set-active` task lineage copy/drift into visible project registry before using dashboard/task detail as canonical evidence for migrated runtimes

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/logs/gateway_events.jsonl`
- artifact refs:
  - `scripts/gateway/aoe_tg_message_handler.py`
  - `tests/gateway/test_gateway_module_surfaces.py`
  - `http://127.0.0.1:18765/control/history?q=planning_gate`
  - `http://127.0.0.1:18765/control/tasks/by-request/r_20260327200920_c3e0106c`
