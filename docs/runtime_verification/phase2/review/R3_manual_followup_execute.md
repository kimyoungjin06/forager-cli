# Review R3 Manual Followup Execute

## 1. Scenario Metadata
- scenario_id:
  - `R3-execute`
- preset:
  - `review`
- branch_target:
  - `manual_followup`
  - `execute_surface`
- status:
  - `planned`
- executed_at:
  - `-`
- operator:
  - `-`

## 2. Input
- request text:
  - `로그인 패치의 회귀 리스크 후보를 정리하고, 내가 지정한 lane만 후속 증거 수집으로 다시 실행해줘.`
- normalized action:
  - `followup_execute`
- target runtime:
  - `-`

## 3. Expected Contract
- expected preset:
  - `review`
- expected execution brief:
  - status:
    - `partially_executable`
  - executable slice:
    - `declared follow-up evidence lanes only`
  - blocked slice or operator decision:
    - `operator-owned severity acceptance remains outside execute scope`
- expected followup brief:
  - status:
    - `executable` or `partially_executable`
  - execution lanes:
    - `explicitly declared lane ids only`
  - review lanes:
    - `manual remainder stays visible and is not auto-launched`
- expected lane shape:
  - execution:
    - rerun rail re-entry over selected evidence lane(s)
  - review:
    - manual remainder preserved, not auto-executed
- expected completion branch:
  - `manual_followup`
- expected reentry rail:
  - `retry=... | followup=executable|partially_executable ... | bg=<runner or ->`
- expected evidence:
  - `/followup-exec` blocks when `preview_only`
  - when executable, it launches only declared execution lanes
  - background ticket / launch spec / evidence bundle align with followup scope if a background rail is used

## 4. Runtime Evidence
- request_id:
  - `-`
- task_short_id:
  - `-`
- planning:
  - `pending replay under current model`
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
  - `must show executable or partially executable FollowupBrief state`
- `/monitor`:
  - `capture if a rerun rail is launched`
- `/offdesk review`:
  - `must keep operator-owned remainder visible`
- dashboard `Task Detail`:
  - `must show followup brief status, lane split, and reentry rail`
- dashboard `Recovery`:
  - `must preserve the same interpretation if the task stalls`
- background run ticket / runner:
  - `capture when local_tmux or external rail is used`
- launch spec / evidence bundle:
  - `capture when a background rail is used`

## 6. Result
- result:
  - `planned`
- mismatch class:
  - `-`
- mismatch notes:
  - `-`
- next fix:
  - `run a bounded replay under the new followup execute rail and capture lane-scoped evidence only`

## 7. Raw References
- runtime state refs:
  - `-`
- log refs:
  - `-`
- artifact refs:
  - `-`
