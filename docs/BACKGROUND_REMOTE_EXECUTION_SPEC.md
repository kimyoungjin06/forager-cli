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
  - must not reinterpret safe inspection commands as executable background work

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
- Conservative default:
  - if the detached run cannot be reconstructed as a stable gateway command:
    - `kind=gateway_dispatch`
    - `mode=in_process_callback`
    - `externalizable=false`
    - `blocked_reason=requires in-process callback registry`
  - if the detached run can be reconstructed safely and the project runner preference is `local_tmux`:
    - use a `local_tmux` launch spec with a gateway simulation payload

#### 5.3.1 Externalizable Runner Defaults
- `local_tmux`
  - `kind=background_dispatch`
  - `mode=tmux_session_json`
  - `entrypoint=aoe-background-worker`
  - `argv=["worker-run","--runner","local_tmux"]`
  - `env_keys=["AOE_TEAM_DIR","AOE_STATE_DIR","AOE_ORCH_ALIAS"]`
  - `externalizable=true`
  - minimal executable payload:
    - `command_argv[]`
    - `command_cwd`
  - supported serializable payload primitive:
    - gateway simulation command
    - shape:
      - `python <repo>/scripts/gateway/aoe-telegram-gateway.py --project-root ... --team-dir ... --manager-state-file ... --simulate-live --simulate-chat-id ... --simulate-text "<command>"`
    - current uses:
      - retry / replan style re-entry commands
      - executable followup slices
      - initial `detached no-wait` dispatch when the run can be reconstructed as `aoe orch run --dispatch ...`
  - runtime artifacts:
    - `.aoe-team/background_run_logs/<ticket>.log`
    - `.aoe-team/background_run_results/<ticket>.json`
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
- Current selection policy:
  - default to `local_background`
  - auto-promote to `local_tmux` only when the launch spec already declares `runner_target=local_tmux` and `externalizable=true`
  - `github_runner` and `remote_worker` stay operator-selected targets only
  - external runners emit a durable handoff manifest and mark the ticket `running`
  - automatic target selection stays conservative until explicit remote pickup/acknowledgement exists

### 5.6 Evidence Bundle
- Durable off-desk result package.
- Minimum contents:
  - request/task metadata
  - execution brief snapshot
  - launch ticket
  - final branch/outcome
  - task/runtime evidence links
  - produced artifacts

### 5.7 Followup Boundary
- Current `/followup` is an on-desk inspection surface.
- It previews:
  - manual follow-up lane targets
  - operator-facing reason/context
  - next safe drill-down commands
- It does not currently authorize background execution.
- Separate execute surface now exists:
  - `/followup-exec <task>`
  - `POST /control/actions/task/followup-execute`
- Current execute-surface rule:
  - if `FollowupBrief.status=preview_only`, block with `followup_execute_brief_required`
- When `FollowupBrief.status` is `executable` or `partially_executable`:
  - launch only `followup_brief_execution_lane_ids`
  - keep `followup_brief_review_lane_ids` visible as preview/manual scope
  - reuse the existing run transition rail with `run_control_mode=followup`
  - allow `local_tmux` background launch when runner preference permits
- If follow-up work should become executable off-desk, the system must first derive a distinct follow-up execution artifact:
  - `FollowupBrief` or equivalent `ExecutionBrief` subtype
  - explicit executable slice
  - explicit blocked/operator-owned slice
  - explicit launch spec
- detailed model:
  - `docs/FOLLOWUP_BRIEF_SPEC.md`
- `/followup` itself remains a safe preview.
- `/followup-exec` is the only mutation surface for executable follow-up slices.

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

### 6.3 local_tmux Result Polling
- tmux wrapper writes:
  - stdout/stderr log:
    - `.aoe-team/background_run_logs/<ticket>.log`
  - terminal result sidecar:
    - `.aoe-team/background_run_results/<ticket>.json`
- poll semantics:
  - `exit_code=0` -> `completed`
  - `exit_code!=0` -> `failed`
  - missing session + missing result sidecar -> `failed`
- ticket evidence should retain both:
  - `background_run_logs/<ticket>.log`
  - `background_run_results/<ticket>.json`

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

### Phase 1.5: local_tmux Launcher
- Add a minimal tmux launcher for externalizable tickets that already carry executable payload.
- Scope:
  - claim `local_tmux` queued ticket
  - validate `command_argv[]` / `command_cwd`
  - start detached tmux session
  - mark ticket `running` with session evidence
  - capture stdout/stderr log:
    - `.aoe-team/background_run_logs/<ticket>.log`
  - wrap the command so it writes a terminal result sidecar:
    - `.aoe-team/background_run_results/<ticket>.json`
  - poll running `local_tmux` tickets:
    - `exit_code=0` -> `completed`
    - non-zero exit -> `failed`
    - missing session + no result sidecar -> `failed`
- Non-goal:
  - reconstruct in-process callback runs as external tmux work

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
- Phase 1 launch specs are intentionally honest about the remaining limitation:
  - serializable detached runs can now use `local_tmux`
  - non-serializable detached runs still remain:
    - `mode=in_process_callback`
    - `externalizable=false`
  This fallback is not a bug; it remains the migration seam toward `github_runner` / `remote_worker` and any dispatch cases that still depend on in-process callback state.
- When an external runner attempts to claim a non-externalizable ticket, the ticket must fail with:
  - `reason=launch_spec_not_externalizable`
- When an externalizable external-runner ticket launches today:
  - a handoff manifest is written under `.aoe-team/background_run_handoffs/`
  - the ticket records:
    - `runtime_handle=<handoff artifact path>`
    - `runtime_summary=<runner>_handoff=<handoff artifact path>`
    - `evidence_bundle=status=running | outcome=external_handoff_emitted | handoff=<artifact>`
  - downstream pickup remains out of scope for the current gateway process
- How much of the current tmux/runtime process model should be reused as `local_background`?
- Should `github_runner` be phase2-only or allow full off-desk dispatch?
- What is the minimum evidence bundle for partial execution?
