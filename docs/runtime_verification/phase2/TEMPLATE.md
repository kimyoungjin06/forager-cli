# <Preset> <Scenario>

## 1. Scenario Metadata
- scenario_id:
  - `<B1|D1|R1|M1|...>`
- preset:
  - `<build|data|review|mixed>`
- branch_target:
  - `<done|rerun|manual_followup>`
- status:
  - `planned`
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
- expected lane shape:
  - execution:
    - `...`
  - review:
    - `...`
- expected completion branch:
  - `...`
- expected evidence:
  - `...`

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
  - `<pass|fail|blocked>`
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
