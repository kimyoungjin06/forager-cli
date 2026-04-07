# Review R2 Rerun Path

## 1. Scenario Metadata
- scenario_id:
  - `R2`
- preset:
  - `review`
- branch_target:
  - `rerun`
- status:
  - `bounded_replay_pass`
- executed_at:
  - `2026-04-07 KST`
- operator:
  - `Codex`

## 2. Input
- request text:
  - `최근 로그인 패치에 대한 회귀 리스크 리뷰를 수행해줘. canonical diff range, 변경 파일, severity findings, test gaps, uncertainties를 review_report.md에 남겨라. 범위 근거나 필수 섹션이 부족하면 done으로 닫지 말고 rerun으로 남겨라.`
- normalized action:
  - `retry`
- target runtime:
  - `bounded replay fixtures only`

## 3. Expected Contract
- expected preset:
  - `review`
- expected execution brief:
  - status:
    - `executable`
  - executable slice:
    - `review evidence collection and rerun over declared retry lanes`
  - blocked slice or operator decision:
    - `-`
- expected followup brief:
  - status:
    - `none`
  - execution lanes:
    - `-`
  - review lanes:
    - `-`
- expected lane shape:
  - execution:
    - reviewer-led readonly evidence lane
  - review:
    - reviewer/verifier lane
- expected completion branch:
  - `rerun`
- expected reentry rail:
  - `retry=<lane-scoped rerun> | followup=none | bg=<runner or ->`
- expected evidence:
  - review-only routing remains `review`
  - retry transition preserves selected execution/review lanes
  - invalid lane selection is rejected
  - background retry rail can use:
    - foreground bridge
    - `local_tmux`
    - `github_runner`
  - run lock and slot saturation can block retry cleanly

## 4. Runtime Evidence
- request_id:
  - `REQ-1` (dashboard retry fixture)
  - `REQ-123` (gateway retry transition fixture)
- task_short_id:
  - `T-001`
  - `T-123`
- planning:
  - `bounded replay via gateway/dashboard tests only`
- execution brief:
  - `retry rail active; task fixture exposes rerun targets and reentry rail summary`
- followup brief:
  - `none`
- reentry rails:
  - `retry=blocked:underspecified exec=L1 review=R1 | followup=none | bg=running/local_background` in dashboard fixture
- stage progression:
  - planning:
    - `review-only prompt remains routed to review preset`
  - execution:
    - `foreground retry transition`
    - `local_tmux background retry`
    - `github_runner handoff retry`
  - verification:
    - `selected retry lanes preserved`
  - integration:
    - `not separately exercised in bounded replay`
  - close:
    - `retry branch remains open / rerun-oriented rather than done`
- critic/verifier verdict:
  - `retry remains lane-scoped and bounded`
- final branch:
  - `rerun`

## 5. Surface Evidence
- `/task`:
  - `reentry_rails summary exposes retry scope distinctly from followup`
- `/monitor`:
  - `not used in bounded replay`
- `/offdesk review`:
  - `offdesk/runtime hints keep retry as the next actionable branch`
- dashboard `Task Detail`:
  - `reentry_rails shows retry=blocked:underspecified exec=L1 review=R1`
- dashboard `Recovery`:
  - `same retry rail is preserved in recovery/nightly surfaces`
- background run ticket / runner:
  - `proved for local_tmux and github_runner via dashboard retry tests`
- launch spec / evidence bundle:
  - `background retry ticket includes runner-target-specific launch payload and runtime handle/handoff`

## 6. Result
- result:
  - `pass`
- mismatch class:
  - `none`
- mismatch notes:
  - `review-only routing stays in review preset for rerun-oriented review_report prompts`
  - `retry rail now preserves selected execution/review lane scope`
  - `retry can be blocked coherently by run lock and slot saturation without drifting into followup`
  - `legacy live blocker about review_report acceptance remains historical context, not the current canonical proof`
- next fix:
  - `promote this bounded replay proof to a later live replay using the current ExecutionBrief + reentry rail surfaces`

## 7. Raw References
- runtime state refs:
  - `tests/gateway/test_gateway_state_helpers.py::test_choose_auto_dispatch_roles_keeps_review_report_rerun_prompt_in_review_only`
  - `tests/gateway/test_gateway_operator_workflows.py::test_resolve_retry_replan_transition_preserves_selected_lane_targets`
  - `tests/gateway/test_gateway_operator_workflows.py::test_resolve_retry_replan_transition_rejects_invalid_lane_selector`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_retry_route_executes_retry_bridge`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_retry_route_uses_local_tmux_background_when_preferred`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_retry_route_emits_github_runner_handoff_when_preferred`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_retry_route_blocks_when_run_lock_is_test_only`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_retry_route_blocks_when_background_slots_are_exhausted`
- log refs:
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_gateway_state_helpers.py -k 'choose_auto_dispatch_roles_keeps_review_report_rerun_prompt_in_review_only or apply_exec_critic_lifecycle_uses_phase2_quality_roles_for_retry_targets'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_gateway_operator_workflows.py -k 'resolve_retry_replan_transition_preserves_selected_lane_targets or resolve_retry_replan_transition_rejects_invalid_lane_selector'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_control_dashboard.py -k 'post_retry_route_executes_retry_bridge or post_retry_route_uses_local_tmux_background_when_preferred or post_retry_route_emits_github_runner_handoff_when_preferred or post_retry_route_blocks_when_run_lock_is_test_only or post_retry_route_blocks_when_background_slots_are_exhausted'`
- artifact refs:
  - `no live runtime artifacts; proof uses fixture state, response payloads, and background ticket assertions only`

## 8. Legacy Reference
- previous live artifact:
  - `2026-03-31`
  - `T-030`
  - blocked before execution because the then-current review_report acceptance contract was still incomplete
- interpretation:
  - this remains a useful history note for the old planning stack
  - it is no longer the current canonical R2 proof under the new `ExecutionBrief + FollowupBrief + reentry rail` model
