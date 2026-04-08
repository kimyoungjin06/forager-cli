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
  - `live_rehearsal_ready`
- proof_mode:
  - `bounded_replay`
- promotion_gate:
  - `preview/execute split and dashboard parity are already proven`
- live_gate:
  - `safe under test-only posture because preview is read-only and launches no internal work`
- executed_at:
  - `2026-04-07 KST`
- operator:
  - `Codex`

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
  - `REQ-1` (dashboard fixture)
  - `REQ-123` (gateway followup fixture)
- task_short_id:
  - `T-001`
  - `T-123`
- planning:
  - `not a live job; bounded replay via existing gateway/dashboard tests only`
- execution brief:
  - `underspecified` in dashboard fixture (`retry=blocked:underspecified ...`)
- followup brief:
  - `preview_only | execution=L2 | review=R1` in dashboard fixture
  - `preview_only | execution=L2 | review=R2` in gateway fixture
- reentry rails:
  - `retry=blocked:underspecified exec=L1 review=R1 | followup=preview_only exec=L2 review=R1 | bg=running/local_background`
- stage progression:
  - planning:
    - `fixture replay only`
  - execution:
    - `not launched`
  - verification:
    - `safe preview parity only`
  - integration:
    - `-`
  - close:
    - `blocked from execute surface`
- critic/verifier verdict:
  - `followup execute remains blocked while preview_only`
- final branch:
  - `manual_followup preview_only`

## 5. Surface Evidence
- `/task`:
  - `task detail fixture shows followup_brief + reentry_rails + operator reason`
- `/monitor`:
  - `not used in bounded replay`
- `/offdesk review`:
  - `safe next step remains /offdesk review or /followup, not execute`
- dashboard `Task Detail`:
  - `proved by tests/gateway/test_control_dashboard.py::test_control_dashboard_task_detail_route_redirects_alias_to_request_id`
  - `shows preview_only, lane split, reason, and reentry_rails`
- dashboard `Recovery`:
  - `preview interpretation covered by structured recovery/dashboard surfaces in bounded replay fixtures`
- background run ticket / runner:
  - `none expected`
- launch spec / evidence bundle:
  - `none expected`

## 6. Result
- result:
  - `pass`
- mismatch class:
  - `none`
- mismatch notes:
  - `preview surface is now explicitly distinct from execute surface`
  - `/followup-exec` rejects preview_only and routes operator back to safe preview`
- next fix:
  - `capture one read-only live rehearsal over /followup, /task, /offdesk review, and dashboard parity without launching any internal job`

## 7. Raw References
- runtime state refs:
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_task_detail_route_redirects_alias_to_request_id`
  - `tests/gateway/test_control_dashboard.py::test_control_dashboard_post_followup_execute_route_blocks_preview_only_brief`
  - `tests/gateway/test_gateway_operator_workflows.py::test_orch_followup_execute_blocks_preview_only_followup_brief`
- log refs:
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_gateway_operator_workflows.py -k 'orch_followup_execute_blocks_preview_only_followup_brief or resolve_followup_execute_transition_uses_execution_slice_only or resolve_followup_execute_transition_rejects_review_lane_selection'`
  - `bounded replay command: uv run --with pytest pytest -q tests/gateway/test_control_dashboard.py -k 'post_followup_execute_route_blocks_preview_only_brief or post_followup_execute_route_uses_local_tmux_background_when_preferred or task_detail_route_redirects_alias_to_request_id or runtime_detail_route_renders_runtime_scope'`
- artifact refs:
  - `no runtime artifacts; proof uses test fixtures and response payload assertions only`
