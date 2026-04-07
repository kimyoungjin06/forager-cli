# Review R3 Manual Followup Preview

## 1. Scenario Metadata
- scenario_id:
  - `R3-preview`
- preset:
  - `review`
- branch_target:
  - `manual_followup`
  - `preview_surface`
- status:
  - `planned`
- executed_at:
  - `-`
- operator:
  - `-`

## 2. Input
- request text:
  - `로그인 패치의 보안 리스크는 정리하되, 최종 허용 여부는 내가 판단할 수 있게 후보 근거만 남겨줘.`
- normalized action:
  - `dispatch_task`
- target runtime:
  - `-`

## 3. Expected Contract
- expected preset:
  - `review`
- expected execution brief:
  - status:
    - `partially_executable`
  - executable slice:
    - `collect bounded evidence and draft a review summary`
  - blocked slice or operator decision:
    - `final acceptance threshold remains operator-owned`
- expected followup brief:
  - status:
    - `preview_only`
  - execution lanes:
    - `bounded evidence lanes only`
  - review lanes:
    - `operator-owned decision review lanes remain visible`
- expected lane shape:
  - execution:
    - reviewer-led readonly evidence lane
  - review:
    - reviewer/verifier lane
- expected completion branch:
  - `manual_followup`
- expected reentry rail:
  - `retry=none | followup=preview_only ... | bg=-`
- expected evidence:
  - `FollowupBrief.status=preview_only`
  - `/followup` preview agrees with dashboard task/runtime detail
  - operator-owned reason is explicit

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
  - `must show followup_brief + reentry_rails`
- `/monitor`:
  - `capture if task remains active`
- `/offdesk review`:
  - `must remain preview-oriented and not offer mutation as the default next step`
- dashboard `Task Detail`:
  - `must show FollowupBrief preview state and operator-owned reason`
- dashboard `Recovery`:
  - `must preserve preview-only manual-followup interpretation when the task is blocked or stale`
- background run ticket / runner:
  - `none expected`
- launch spec / evidence bundle:
  - `none expected`

## 6. Result
- result:
  - `planned`
- mismatch class:
  - `-`
- mismatch notes:
  - `-`
- next fix:
  - `run a bounded replay or isolated fixture under the current model and capture preview parity`

## 7. Raw References
- runtime state refs:
  - `-`
- log refs:
  - `-`
- artifact refs:
  - `-`
