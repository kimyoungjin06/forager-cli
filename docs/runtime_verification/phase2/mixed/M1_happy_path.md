# Mixed M1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `M1`
- preset:
  - `mixed`
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
  - `배포 체크리스트 자동화 스크립트를 보강하고 operator handoff 문서와 reviewer note를 함께 남겨줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `TBD`

## 3. Expected Contract
- expected preset:
  - `mixed`
- expected lane shape:
  - execution:
    - work lane
    - writer/handoff lane
  - review:
    - reviewer lane
- expected completion branch:
  - `done`
- expected evidence:
  - primary work artifact
  - handoff or documentation artifact
  - review evidence

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
