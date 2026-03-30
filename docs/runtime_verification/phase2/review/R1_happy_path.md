# Review R1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `R1`
- preset:
  - `review`
- branch_target:
  - `done`
- status:
  - `executed_blocked`
- executed_at:
  - `2026-03-31 08:30 KST`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build`

## 3. Expected Contract
- expected preset:
  - `review`
- expected lane shape:
  - execution:
    - minimal execution or reviewer-led lane
  - review:
    - reviewer/verifier lane remains primary
- expected completion branch:
  - `done`
- expected evidence:
  - findings list
  - severity rationale
  - affected scope
  - unresolved questions if any

## 4. Runtime Evidence
- request_id:
  - `r_20260331082223_f60a9627`
- task_short_id:
  - `T-015`
- planning:
  - `phase1 ensemble 3 rounds / 3 critical reviews / convergence=blocked`
- stage progression:
  - planning:
    - `intake -> phase1 round1 -> round2 -> round3 -> planning_blocked`
  - execution:
    - `not started`
  - verification:
    - `not started`
  - integration:
    - `not started`
  - close:
    - `failed`
- critic/verifier verdict:
  - `critic blocker after auto-replan`
- final branch:
  - `planning_blocked`

## 5. Surface Evidence
- `/task`:
  - `phase1=review phase2=review`
- `/monitor`:
  - `not captured; terminal blocker confirmed in gateway events`
- `/offdesk review`:
  - `not captured`
- dashboard `Task Detail`:
  - `not captured`
- dashboard `Recovery`:
  - `not captured`

## 6. Result
- result:
  - `executed_blocked`
- mismatch class:
  - `generic_contract_gap`
- mismatch notes:
  - `T-013` first run misrouted to `build` preset and blocked on reviewer-result ownership after review lanes.
  - generic fix 1: review-only requests mentioning patch/code context now prefer `review` preset and reviewer roles.
  - `T-014` second run entered `review` preset but blocked because multi-subtask reviewer lanes were emitted with `parallel: true`.
  - generic fix 2: execution lanes that own multiple subtasks are now forced serial even when the overall preset still uses parallel workers.
  - `T-015` latest clean rerun stayed in `review` preset and moved the blocker down to canonical diff-range policy:
    - `최근 로그인 패치`의 git 기준 range, dirty worktree 포함/제외, multi-candidate commit selection rule이 contract에 없어 severity scope가 흔들릴 수 있음.
- next fix:
  - `review request contract`에 canonical diff-range selection policy를 typed field로 추가

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/logs/gateway_events.jsonl`
- artifact refs:
  - `none; planning gate blocked before execution`
