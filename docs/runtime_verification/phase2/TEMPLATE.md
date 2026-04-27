# <Preset> <Scenario>

## 1. Scenario Metadata
- scenario_id:
  - `<B1|D1|R1|M1|...>`
- preset:
  - `<build|data|review|mixed>`
- branch_target:
  - `<done|rerun|manual_followup>`
  - if `manual_followup`, specify:
    - `<preview_surface|execute_surface>`
- status:
  - `<planned|bounded_replay_pass|live_rehearsal_ready|executed_done|executed_blocked>`
- executed_at:
  - `-`
- operator:
  - `-`

## 2. Input
- request text:
  - `...`
- normalized action:
  - `...`
- target runtime:
  - `...`

## 3. Expected Contract
- expected preset:
  - `...`
- expected execution brief:
  - status:
    - `<executable|partially_executable|underspecified|operator_decision_required|infeasible>`
  - executable slice:
    - `...`
  - blocked slice or operator decision:
    - `...`
- expected followup brief:
  - status:
    - `<none|preview_only|executable|partially_executable>`
  - execution lanes:
    - `...`
  - review lanes:
    - `...`
- expected lane shape:
  - execution:
    - `...`
  - review:
    - `...`
- expected completion branch:
  - `...`
- expected reentry rail:
  - `retry=... | followup=... | bg=...`
- expected evidence:
  - `...`

## 4. Runtime Evidence
- request_id:
  - `-`
- task_short_id:
  - `-`
- planning:
  - `-`
- execution brief:
  - `-`
- followup brief:
  - `-`
- reentry rails:
  - `-`
- stage progression:
  - planning:
    - `-`
  - execution:
    - `-`
  - verification:
    - `-`
  - integration:
    - `-`
  - close:
    - `-`
- critic/verifier verdict:
  - `-`
- final branch:
  - `-`

## 5. Surface Evidence
- `/task`:
  - `-`
- `/monitor`:
  - `-`
- `/offdesk review`:
  - `-`
- dashboard `Task Detail`:
  - `-`
- dashboard `Recovery`:
  - `-`
- background run ticket / runner:
  - `-`
- launch spec / evidence bundle:
  - `-`

## 6. Result
- result:
  - `<pass|fail|blocked>`
- mismatch class:
  - `-`
- mismatch notes:
  - `-`
- next fix:
  - `-`

## 6.1 Proof Mode
- proof_mode:
  - `<bounded_replay|live_rehearsal>`
- promotion_gate:
  - `-`
- live_gate:
  - `-`

## 7. Raw References
- runtime state refs:
  - `-`
- log refs:
  - `-`
- artifact refs:
  - `-`
