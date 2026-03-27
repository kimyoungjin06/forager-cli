# Review R1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `R1`
- preset:
  - `review`
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
  - `최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행하고 severity와 근거를 정리해줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `TBD`

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
