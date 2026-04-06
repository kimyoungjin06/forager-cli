# Background / Remote Execution Spec

## 1. Purpose
- Move off-desk work off foreground live sessions and into durable execution rails.
- Keep `ExecutionBrief` as the operator-approved handoff object.
- Ensure every off-desk run leaves an auditable trail and an evidence bundle.

## 2. Benchmark References
- `REF-OH-1`: OpenHands local/cloud/operator console direction
- `REF-CC-2`: Claude Code Action GitHub-triggered execution model
- `REF-GHCA-1`: GitHub Copilot coding agent issue-to-background-work loop
- `REF-GHCA-2`: GitHub Copilot coding agent browser/background surface
- `REF-API-1`: Agent adapter seam for vendor-neutral control

## 3. Design Goal
- `on-desk`:
  - normalize request
  - build `ExecutionBrief`
  - decide `executable / underspecified / partially_executable / operator_decision_required / infeasible`
- `off-desk`:
  - only runs `ExecutionBrief` objects that are allowed for off-desk execution
  - never invents scope beyond the brief

## 4. Non-Goals
- This spec does not define planner prompt wording.
- This spec does not replace `Task Team` or `Phase2` planning.
- This spec does not require cloud execution first; local background execution is the first milestone.

## 5. Core Objects

### 5.1 Execution Brief
- Source of truth for off-desk eligibility.
- Required minimum:
  - `execution_brief_status`
  - `execution_brief_summary`
  - `execution_brief_executable_slice`
  - `execution_brief_blocked_slice`
  - `execution_brief_operator_decision`

### 5.2 Background Run Ticket
- Immutable handoff object created when an off-desk run is launched.
- Fields:
  - `ticket_id`
  - `request_id`
  - `project_key`
  - `execution_brief_status`
  - `runner_target`
  - `launch_mode`
  - `created_at`
  - `created_by`
  - `source_surface`
- Phase 1 persisted task/runtime fields:
  - `background_run_ticket_version`
  - `background_run_ticket_id`
  - `background_run_status`
  - `background_run_runner_target`
  - `background_run_launch_mode`
  - `background_run_created_at`
  - `background_run_created_by`
  - `background_run_source_surface`
  - `background_run_request_id`
  - `background_run_project_key`
  - `background_run_execution_brief_status`
  - `background_run_evidence_bundle`
  - `background_run_evidence_artifacts[]`
  - `background_run_launch_spec_id`
  - `background_run_launch_spec_kind`
  - `background_run_launch_spec_mode`
  - `background_run_launch_spec_summary`
  - `background_run_launch_spec_externalizable`
  - sidecar queue file:
    - `.aoe-team/background_runs.json`

### 5.3 Launch Spec
- Serializable execution envelope attached to the ticket.
- Purpose:
  - make runner assumptions explicit
  - separate launch intent from in-memory callback wiring
  - define what must exist before an external worker can consume the run
- Phase 1 shape:
  - `spec_id`
  - `kind`
  - `mode`
  - `entrypoint`
  - `project_root`
  - `team_dir`
  - `manager_state_file`
  - `request_id`
  - `project_key`
  - `runner_target`
  - `launch_mode`
  - `source_surface`
  - `created_by`
  - `argv[]`
  - `env_keys[]`
  - `externalizable`
  - `blocked_reason`
  - `summary`
- Current default:
  - `kind=gateway_dispatch`
  - `mode=in_process_callback`
  - `externalizable=false`
  - `blocked_reason=requires in-process callback registry`

#### 5.3.1 Externalizable Runner Defaults
- `local_tmux`
  - `kind=background_dispatch`
  - `mode=tmux_session_json`
  - `entrypoint=aoe-background-worker`
  - `argv=["worker-run","--runner","local_tmux"]`
  - `env_keys=["AOE_TEAM_DIR","AOE_STATE_DIR","AOE_ORCH_ALIAS"]`
  - `externalizable=true`
- `github_runner`
  - `kind=background_dispatch`
  - `mode=github_action_json`
  - `entrypoint=aoe-background-worker`
  - `argv=["worker-run","--runner","github_runner"]`
  - `env_keys=["AOE_TEAM_DIR","AOE_STATE_DIR","GITHUB_TOKEN","GITHUB_REPOSITORY"]`
  - `externalizable=true`
- `remote_worker`
  - `kind=background_dispatch`
  - `mode=remote_worker_json`
  - `entrypoint=aoe-background-worker`
  - `argv=["worker-run","--runner","remote_worker"]`
  - `env_keys=["AOE_TEAM_DIR","AOE_STATE_DIR","AOE_REMOTE_ENDPOINT"]`
  - `externalizable=true`

### 5.4 Background Worker State
- Durable heartbeat/state object for the active local worker.
- Phase 1 shape:
  - `status`
  - `runner_target`
  - `mode`
  - `thread_name`
  - `pid`
  - `started_at`
  - `heartbeat_at`
  - `stopped_at`
  - `last_reason`
  - `last_ticket_id`
  - `last_claimed_at`
  - `claimed_count`
  - `drain_cycles`
  - `queue_depth`
  - `queue_stale_count`
  - `queue_summary`
- Sidecar path:
  - `.aoe-team/background_worker.json`

### 5.5 Runner Target
- Where the work executes.
- Initial enum:
  - `local_background`
  - `local_tmux`
  - `github_runner`
  - `remote_worker`

### 5.6 Evidence Bundle
- Durable off-desk result package.
- Minimum contents:
  - request/task metadata
  - execution brief snapshot
  - launch ticket
  - final branch/outcome
  - task/runtime evidence links
  - produced artifacts

## 6. State Model

### 6.1 Off-desk Eligibility
- `executable`
  - may launch directly
- `partially_executable`
  - may launch only the declared executable slice
- `underspecified`
  - must not launch
- `operator_decision_required`
  - must not launch
- `infeasible`
  - must not launch

### 6.2 Background Run Lifecycle
- `queued`
- `dispatching`
- `running`
- `completed`
- `failed`
- `canceled`
- `stale`

## 7. Required Guarantees
1. A run cannot start without a stored `ExecutionBrief`.
2. A run cannot start if `execution_brief_status` is not in:
   - `executable`
   - `partially_executable`
3. Every run must write a launch ticket before work starts.
4. Every run must write a final evidence bundle before closing.
5. Operator surfaces must show:
   - current runner target
   - launch mode
   - latest ticket
   - launch spec summary / externalizable state
   - current background lifecycle state
6. External runner targets:
   - `local_tmux`
   - `github_runner`
   - `remote_worker`
   must not enter `dispatching` unless `launch_spec.externalizable=true`.

## 8. Rollout Plan

### Phase 1: Local Background Queue
- Launch off-desk work without tying it to the foreground gateway session.
- Add:
  - queue file/state
  - run ticket persistence
  - same-process singleton `local_background` daemon thread
  - worker heartbeat/state file
  - background lifecycle state in dashboard

### Phase 2: Remote Runner Abstraction
- Introduce runner target selection:
  - `local_background`
  - `github_runner`
  - `remote_worker`
- Preserve the same run ticket and evidence bundle format.

### Phase 3: SCM / Issue Trigger Bridge
- Allow issue/PR-driven off-desk execution.
- Constraints:
  - brief must still exist first
  - remote launch still writes the same ticket/evidence objects

## 9. Operator Surfaces
- Dashboard must expose:
  - `ExecutionBrief` status
  - background lifecycle state
  - runner target
  - latest launch ticket
  - evidence bundle availability
- Recovery must expose:
  - stale background runs
  - failed launches
  - missing evidence bundles

## 10. Implementation Order
1. `ExecutionBrief` gate in off-desk action path
2. background run ticket persistence
3. local background queue
4. dashboard runtime/recovery visibility
5. remote runner abstraction
6. SCM trigger bridge

## 11. Open Questions
- Current `local_background` daemon is an in-process thread because execution targets still live in the gateway process registry. A future external worker requires serializable launch specs instead of in-memory callbacks.
- Phase 1 launch specs are intentionally honest about the current limitation:
  - `mode=in_process_callback`
  - `externalizable=false`
  This is not a bug; it is the explicit migration seam toward `local_tmux` / `github_runner` / `remote_worker`.
- When an external runner attempts to claim a non-externalizable ticket, the ticket must fail with:
  - `reason=launch_spec_not_externalizable`
- How much of the current tmux/runtime process model should be reused as `local_background`?
- Should `github_runner` be phase2-only or allow full off-desk dispatch?
- What is the minimum evidence bundle for partial execution?
