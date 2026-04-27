# Data D2 Rerun Path

## 1. Scenario Metadata
- scenario_id:
  - `D2`
- preset:
  - `data`
- branch_target:
  - `rerun`
- status:
  - `live_rehearsal_ready`
- current_fix_branch:
  - `task/data-d2-isolated-rerun-1`
- executed_at:
  - `2026-04-28T01:03:16+09:00`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `입력 CSV는 data/monthly_raw.csv이고 정규화 대상 컬럼은 month다. 허용 입력 패턴은 YYYY/MM, YYYY-MM, YYYY.MM이고 모두 YYYY-MM으로 zero-pad 정규화한다. orders는 integer, revenue는 number 스키마를 유지해야 한다. orders 또는 revenue에서 null 또는 비수치 값이 2행 이상이면 null-heavy=true로 판정하고 done으로 닫지 말고 rerun으로 남겨라. parse 불가하거나 범위를 벗어난 month 값은 원본 행을 유지하고 month 원값을 그대로 두며 anomaly로 기록한다. schema_report.json에는 orders/revenue의 expected_type, observed_inferred_type, schema_drift, rerun_required, violations를 남기고, null_summary.md에는 affected_columns, null_or_invalid_count, null_heavy, rerun_required, reason을 남겨라. schema_report.json, null_summary.md, sample_5.csv도 함께 남겨라.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `O7 isolated data rerun seed runtime`

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
  - `REQ-D2-001`
- task_short_id:
  - `T-701`
- planning:
  - `phase1 ensemble rounds=3 providers=codex, claude`
- stage progression:
  - planning:
    - `done`
  - execution:
    - `done`
  - verification:
    - `failed`
  - integration:
    - `failed`
  - close:
    - `failed`
- critic/verifier verdict:
  - `retry`
- final branch:
  - `needs_retry`
- runtime proof:
  - `seeded isolated rehearsal reaches dispatch-ready state with reentry_rails=retry=ready exec=L1 review=R1 | followup=none`
  - `task contract remains data preset with DataEngineer execution lane and Codex/Claude verifier lanes`
  - `null_summary.md contains affected_columns, null_or_invalid_count, null_heavy, rerun_required, and reason`

## 5. Surface Evidence
- `/task`:
  - `T-701` shows `team_phase: needs_retry`, `phase2_execution: single lanes=1`, `phase2_review: parallel lanes=2`, and `execution_brief: executable`
- `/monitor`:
  - `covered by /orch status O7 runtime surface for this read-only seed`
- `/offdesk review`:
  - `flags runtime conservatively under test_only/no backlog, but exposes /task T-701 as the active needs_retry item`
- dashboard `Task Detail`:
  - `/control/tasks/by-request/REQ-D2-001`
- dashboard `Recovery`:
  - `/control/runtimes/O7`

## 6. Result
- result:
  - `live_rehearsal_ready`
- mismatch class:
  - `launch_pending`
- mismatch notes:
  - `T-039` blocked because rerun evidence was not lowered into contract-owned artifacts.
  - `T-041` blocked because `null-heavy` still lacked an explicit threshold and per-column rationale format.
  - `T-043` blocked because `null_or_invalid_count` arithmetic and non-numeric classification for `orders`/`revenue` were still not explicit enough for reviewer-owned rerun evidence.
  - `T-044` blocked even after `quality_gate_policy`, `schema_column_expectations`, numeric threshold extraction, and `schema_value_quality_policy` landed.
  - The remaining gap is not a reusable core abstraction; it is scenario-specific evidence formatting for `null_summary.md` and should be handled by stricter prompt discipline or operator-authored validator policy, not additional core expansion.
  - `2026-04-28 isolated seed proof now materializes concrete D2 artifacts and operator surfaces, but does not claim a launched provider/background execution.`
- follow-up fix attempt:
  - `2026-04-28 KST`: request-contract extraction now preserves explicit operator-authored artifact fields from `schema_report.json에는 ...` and `null_summary.md에는 ...`
  - `null_summary.md` required fields are ordered as `affected_columns`, `null_or_invalid_count`, `null_heavy`, `rerun_required`, `reason` when the prompt declares that shape
  - data acceptance floor now repeats the explicit null-heavy evidence fields together with `orders,revenue >= 2` and `null-or-invalid-row-count`
  - `2026-04-28 KST`: `aoe_tg_live_rehearsal_seed.py --scenario d2` creates a dispatch-ready D2 rerun candidate with concrete `data/monthly_raw.csv`, `normalized.csv`, `schema_report.json`, `null_summary.md`, and `sample_5.csv`
- next fix:
  - `launch /retry T-701 lane L1 from the isolated D2 runtime and promote to executed_done only if the background ticket closes cleanly while the source task remains rerun with the same concrete null_summary evidence`

## 7. Raw References
- runtime state refs:
  - `/tmp/aoe_d2_seed_check/.aoe-team/orch_manager_state.json`
  - prior blocked run: `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/.aoe-team/orch_manager_state.json`
- log refs:
  - `/tmp/aoe_d2_seed_check/.aoe-team/logs/gateway_events.jsonl`
  - prior blocked run: `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/.aoe-team/logs/gateway_events.jsonl`
  - `seed command: python3 scripts/gateway/aoe_tg_live_rehearsal_seed.py --scenario d2 --control-root /tmp/aoe_d2_seed_check --run-lock-mode test_only --runner-target local_tmux --local-tmux-slot-limit 1`
  - `surface command: python3 scripts/gateway/aoe-telegram-gateway.py --project-root /tmp/aoe_d2_seed_check/Alpha --workspace-root /tmp/aoe_d2_seed_check --team-dir /tmp/aoe_d2_seed_check/Alpha/.aoe-team --manager-state-file /tmp/aoe_d2_seed_check/.aoe-team/orch_manager_state.json --simulate-chat-id 939062873 --simulate-live --once --no-owner-only --no-deny-by-default --simulate-text '/task T-701'`
  - `surface command: python3 scripts/gateway/aoe-telegram-gateway.py --project-root /tmp/aoe_d2_seed_check/Alpha --workspace-root /tmp/aoe_d2_seed_check --team-dir /tmp/aoe_d2_seed_check/Alpha/.aoe-team --manager-state-file /tmp/aoe_d2_seed_check/.aoe-team/orch_manager_state.json --simulate-chat-id 939062873 --simulate-live --once --no-owner-only --no-deny-by-default --simulate-text '/orch status O7'`
  - `surface command: python3 scripts/gateway/aoe-telegram-gateway.py --project-root /tmp/aoe_d2_seed_check/Alpha --workspace-root /tmp/aoe_d2_seed_check --team-dir /tmp/aoe_d2_seed_check/Alpha/.aoe-team --manager-state-file /tmp/aoe_d2_seed_check/.aoe-team/orch_manager_state.json --simulate-chat-id 939062873 --simulate-live --once --no-owner-only --no-deny-by-default --simulate-text '/offdesk review O7'`
  - `bounded regression command: bash scripts/gateway_pytest.sh tests/gateway/test_phase1_planning.py -q`
  - `bounded regression command: bash scripts/gateway_pytest.sh tests/gateway/test_gateway_state_helpers.py -k 'request_contract or execution_brief' -q`
  - `bounded regression command: bash scripts/gateway_pytest.sh tests/gateway/test_live_rehearsal_seed.py -q`
- artifact refs:
  - `/tmp/aoe_d2_seed_check/Alpha/data/monthly_raw.csv`
  - `/tmp/aoe_d2_seed_check/Alpha/normalized.csv`
  - `/tmp/aoe_d2_seed_check/Alpha/schema_report.json`
  - `/tmp/aoe_d2_seed_check/Alpha/null_summary.md`
  - `/tmp/aoe_d2_seed_check/Alpha/sample_5.csv`
  - prior blocked run: `/tmp/aoe_lv_d2_BP7psW/demo-monthly-rerun/data/monthly_raw.csv`
