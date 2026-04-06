# Review R2 Rerun Path

## 1. Scenario Metadata
- scenario_id:
  - `R2`
- preset:
  - `review`
- branch_target:
  - `rerun`
- status:
  - `executed_blocked`
- executed_at:
  - `2026-03-31 23:00 KST`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행해줘. canonical diff range, 변경 파일, severity findings, test gaps, uncertainties를 review_report.md에 남겨라. 범위 근거나 필수 섹션이 부족하면 done으로 닫지 말고 rerun으로 남겨라.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build`

## 3. Expected Contract
- expected preset:
  - `review`
- expected lane shape:
  - execution:
    - reviewer-led readonly lane
  - review:
    - reviewer/verifier lane
- expected completion branch:
  - `rerun`
- expected evidence:
  - review_report diff scope evidence
  - changed files
  - severity findings
  - test gaps
  - uncertainties
  - explicit rerun reason when scope or required sections are incomplete

## 4. Runtime Evidence
- request_id:
  - `r_20260331224951_36a2290c`
- task_short_id:
  - `T-030`
- planning:
  - `blocked`
- stage progression:
  - planning:
    - `phase1 round 1/3`
    - `phase1 round 2/3`
    - `phase1 round 3/3`
    - `plan gate blocked`
  - execution:
    - `-`
  - verification:
    - `-`
  - integration:
    - `-`
  - close:
    - `-`
- critic/verifier verdict:
  - `critic issues remain after auto-replan`
- final branch:
  - `blocked before rerun`

## 5. Surface Evidence
- `/task`:
  - `pending`
- `/monitor`:
  - `pending`
- `/offdesk review`:
  - `pending`
- dashboard `Task Detail`:
  - `pending`
- dashboard `Recovery`:
  - `pending`

## 6. Result
- result:
  - `blocked`
- mismatch class:
  - `review_final_artifact_contract_gap`
- mismatch notes:
  - `review-only routing, reviewer-only role defaults, review_report single-output ownership, and evidence step-shape normalization are now present`
  - `the remaining blocker is the final review_report acceptance contract: canonical diff range, excluded candidates, and dirty-worktree exclusions are still not guaranteed as required report sections`
- next fix:
  - `push canonical diff scope and exclusion evidence into review_report required fields / review-lane acceptance so incomplete scope evidence drives rerun instead of plan_gate blocked`

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_lv_b1_61BoBN/demo-login-build/.aoe-team/logs/gateway_events.jsonl`
- artifact refs:
  - `review_report.md not produced because the task blocked before execution`
