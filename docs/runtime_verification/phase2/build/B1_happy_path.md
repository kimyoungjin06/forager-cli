# Build B1 Happy Path

## 1. Scenario Metadata
- scenario_id:
  - `B1`
- preset:
  - `build`
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
  - `로그인 실패 시 세션 만료 처리 누락을 수정하고 회귀 테스트 결과까지 남겨줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `TBD`

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
