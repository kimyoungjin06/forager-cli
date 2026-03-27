# Data D1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `D1`
- preset:
  - `data`
- branch_target:
  - `done`
- status:
  - `planned`
- executed_at:
  - `-`
- operator:
  - `-`

## 2. Input
- request text:
  - `월별 집계 CSV를 정규화하고 스키마 체크, null 요약, 샘플 5행을 함께 남겨줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `TBD`

## 3. Expected Contract
- expected preset:
  - `data`
- expected lane shape:
  - execution:
    - transform/data lane
  - review:
    - verifier/reviewer lane
- expected completion branch:
  - `done`
- expected evidence:
  - schema check
  - null/outlier summary
  - sample output
  - transform note

## 4. Runtime Evidence
- request_id:
  - `-`
- task_short_id:
  - `-`
- planning:
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

## 6. Result
- result:
  - `planned`
- mismatch class:
  - `-`
- mismatch notes:
  - `-`
- next fix:
  - `-`

## 7. Raw References
- runtime state refs:
  - `-`
- log refs:
  - `-`
- artifact refs:
  - `-`
