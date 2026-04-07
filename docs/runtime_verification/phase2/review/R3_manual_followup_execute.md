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
  - `bounded_replay_pass`
- executed_at:
  - `2026-04-07 KST`
- operator:
  - `Codex`

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
  - `REQ-1` (dashboard fixture)
  - `REQ-123` (gateway followup fixture)
- task_short_id:
  - `T-001`
  - `T-123`
- planning:
  - `bounded replay via gateway/dashboard tests only`
- execution brief:
  - `partially_executable` / execution slice only
- followup brief:
  - `partially_executable | execution=L2 | review=R1`
  - `executable | execution=L3 | review=-` in direct snapshot helper
- reentry rails:
  - `followup rail executes only selected execution lanes`
- stage progression:
  - planning:
    - `fixture replay only`
  - execution:
    - `foreground followup transition`
    - `local_tmux background transition`
  - verification:
    - `review lanes explicitly excluded from auto-launch`
  - integration:
    - `-`
  - close:
    - `transition recorded as followup execute`
- critic/verifier verdict:
  - `execute surface launches only execution slice and keeps review/manual remainder visible`
- final branch:
  - `manual_followup execute_surface`

## 5. Surface Evidence
- `/task`:
  - `summary helper exposes executable/partially_executable FollowupBrief state`
- `/monitor`:
  - `not used in bounded replay`
- `/offdesk review`:
  - `operator-owned review remainder remains visible; not auto-launched`
- dashboard `Task Detail`:
  - `proof covered by task/runtime detail fixture tests`
- dashboard `Recovery`:
  - `not separately exercised in this bounded replay slice`
- background run ticket / runner:
  - `proved for local_tmux via dashboard followup execute background test`
- launch spec / evidence bundle:
  - `background ticket contains runner_target=local_tmux and runtime_handle when preferred`

## 6. Result
- result:
  - `pass`
- mismatch class:
  - `none`
- mismatch notes:
  - `followup-exec now reuses the rerun rail as a dedicated control mode`
  - `selected review lanes are rejected for execute surface`
  - `preview_only and executable surfaces are no longer conflated`
- next fix:
  - `promote to a later live replay with background ticket capture if a full runtime proof is needed`

## 7. Raw References
- runtime state refs:
  - `tests/gateway/test_gateway_state_helpers.py::test_build_followup_brief_snapshot_marks_execution_only_slice_executable`
  - `tests/gateway/test_gateway_operator_workflows.py::test_resolve_followup_execute_transition_uses_execution_slice_only`
  - `tests/gateway/test_gateway_operator_workflows.py::test_resolve_followup_execute_transition_rejects_review_lane_selection`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_followup_execute_route_runs_partially_executable_brief`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_followup_execute_route_uses_local_tmux_background_when_preferred`
  - `tests/gateway/test_operator_action_contract.py::test_partition_task_operator_commands_adds_followup_execute_when_followup_brief_is_executable`
- log refs:
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_gateway_state_helpers.py -k 'build_followup_brief_snapshot_marks_execution_only_slice_executable or task_lifecycle_summary'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_gateway_operator_workflows.py -k 'orch_followup_execute_blocks_preview_only_followup_brief or resolve_followup_execute_transition_uses_execution_slice_only or resolve_followup_execute_transition_rejects_review_lane_selection'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_control_dashboard.py -k 'post_followup_execute_route_blocks_preview_only_brief or post_followup_execute_route_uses_local_tmux_background_when_preferred or task_detail_route_redirects_alias_to_request_id or runtime_detail_route_renders_runtime_scope'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_operator_action_contract.py -k 'partition_task_operator_commands_adds_followup_execute_when_followup_brief_is_executable or http_action_spec_maps_followup_execute_to_post_contract'`
- artifact refs:
  - `no live artifacts; proof uses fixture state, response payloads, and background ticket assertions only`
