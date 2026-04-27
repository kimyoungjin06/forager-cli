# Data D2 Rerun Path

## 1. Scenario Metadata
- scenario_id:
  - `D2`
- preset:
  - `data`
- branch_target:
  - `rerun`
- status:
  - `executed_blocked`
- executed_at:
  - `2026-03-31T07:46:09+09:00`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. 허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. orders는 integer, revenue는 number 스키마를 유지해야 한다. orders 또는 revenue에서 null 또는 비수치 값이 2행 이상이면 null-heavy=true로 판정하고 done으로 닫지 말고 rerun으로 남겨라. parse 불가하거나 범위를 벗어난 month 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. schema_report.json에는 orders/revenue의 expected_type, observed_inferred_type, schema_drift, rerun_required, violations를 남기고, null_summary.md에는 affected_columns, null_or_invalid_count, null_heavy, rerun_required, reason을 남겨라. schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `O1 default (temp D2 runtime)`

## 3. Expected Contract
- expected preset:
  - `data`
- expected lane shape:
  - execution:
    - `DataEngineer`
  - review:
    - `Codex-Reviewer`
    - `Claude-Reviewer`
- expected completion branch:
  - `rerun`
- expected evidence:
  - `normalized.csv`
  - `schema_report.json`
  - `null_summary.md`
  - `sample_5.csv`
  - explicit rerun rationale tied to schema drift or null-heavy output

## 4. Runtime Evidence
- request_id:
  - `r_20260331074609_bff08ec1`
- task_short_id:
  - `T-044`
- planning:
  - `phase1 ensemble rounds=3 providers=codex, claude`
- stage progression:
  - planning:
    - `blocked after 3 review passes`
  - execution:
    - `-`
  - verification:
    - `-`
  - integration:
    - `-`
  - close:
    - `-`
- critic/verifier verdict:
  - `planning_blocked`
- final branch:
  - `not reached`

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
  - `executed_blocked`
- mismatch class:
  - `scenario_policy_gap`
- mismatch notes:
  - `T-039` blocked because rerun evidence was not lowered into contract-owned artifacts.
  - `T-041` blocked because `null-heavy` still lacked an explicit threshold and per-column rationale format.
  - `T-043` blocked because `null_or_invalid_count` arithmetic and non-numeric classification for `orders`/`revenue` were still not explicit enough for reviewer-owned rerun evidence.
  - `T-044` blocked even after `quality_gate_policy`, `schema_column_expectations`, numeric threshold extraction, and `schema_value_quality_policy` landed.
  - The remaining gap is not a reusable core abstraction; it is scenario-specific evidence formatting for `null_summary.md` and should be handled by stricter prompt discipline or operator-authored validator policy, not additional core expansion.
- next fix:
  - `freeze core changes for D2 and revisit only with scenario-specific prompt/validator guidance`

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/.aoe-team/logs/gateway_events.jsonl`
- artifact refs:
  - `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/data/monthly_raw.csv`
