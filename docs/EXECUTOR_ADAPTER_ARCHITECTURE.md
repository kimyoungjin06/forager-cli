# Executor Adapter Architecture

## 1. Purpose
- Reframe `aoe_orch_control` as an operator-grade control plane with pluggable execution adapters.
- Keep our differentiated core in:
  - `RequestContract`
  - `ExecutionBrief`
  - `FollowupBrief`
  - `Background Run Ticket`
  - run locks / slot policy / scheduler policy
  - audit / recovery / dashboard / off-desk surfaces
- Treat runner-specific execution as an adapter seam rather than a native harness monolith.

## 2. Why This Reframe
- Native end-to-end harness development is too slow for the value we need.
- The reusable market layer already exists:
  - terminal/CLI execution shells
  - tmux-backed local workers
  - GitHub runner surfaces
  - remote worker substrates
- Our comparative advantage is not prompt-level planner cleverness.
- It is the operator control plane:
  - execution eligibility
  - task/runtime truth
  - remediation hints
  - recovery and audit

## 3. Layer Model

### 3.1 Control Plane Core
- Owns:
  - request normalization
  - execution/followup feasibility
  - background ticket lifecycle
  - run lock / slot / scheduler policy
  - operator actions and dashboard surfaces
- Must stay canonical.
- Must not depend on any single executor implementation.

### 3.2 Executor Adapter Layer
- Owns translation from control-plane objects to runner-specific launch and result shapes.
- Required responsibilities:
  - validate runner capability
  - derive or reject launch specs
  - emit launch / handoff artifacts
  - normalize runtime handle and result evidence back into control-plane truth
- Current adapter inventory:
  - `local_background`
  - `local_tmux`
  - `github_runner`
  - `remote_worker`

### 3.3 Commodity Executors
- Not part of the canonical control model.
- Examples:
  - tmux session launcher
  - gateway CLI replay command
  - GitHub Actions job
  - external worker process
  - third-party coding CLI / agent shell
- These can change without invalidating `ExecutionBrief`, `FollowupBrief`, or `Background Run Ticket`.

## 4. Control Plane Ownership
- We keep these native:
  1. `RequestContract -> ExecutionBrief -> OrchTaskSpec`
  2. `FollowupBrief`
  3. `Background Run Ticket`
  4. `run_lock_mode`
  5. runner-target slot and scheduler policy
  6. `/task`, `/orch status`, `/offdesk review`, dashboard, recovery, audit
- These are not executor-specific and should not leak runner implementation details into their core schema.

## 5. Adapter Contract

### 5.1 Inputs
- `ExecutionBrief` or `FollowupBrief`
- `Background Run Ticket`
- `Launch Spec`
- operator-selected policy:
  - runner target
  - slot limits
  - run lock
  - source surface / launch mode

### 5.2 Outputs
- accepted / rejected launch decision
- normalized runtime handle
- normalized runtime summary
- evidence bundle
- evidence artifacts
- optional external phase progression:
  - `handoff_emitted`
  - `pickup_acknowledged`
  - `result_received`

### 5.3 Hard Rules
- adapters must not widen scope beyond the incoming brief
- adapters must not create a second task truth model
- adapters must write back results using the canonical ticket/evidence shape
- adapter failure must surface as a structured blocked/failed reason, not hidden log text

## 6. Current Adapter Inventory

| Runner | Adapter Kind | Externalizable Spec Required | Ack Phase | Test-Only Harness | Slot-Limited |
|---|---|---:|---:|---:|---:|
| `local_background` | `in_process_callback` | no | no | no | no |
| `local_tmux` | `local_tmux_session` | yes | no | no | yes |
| `github_runner` | `external_handoff` | yes | yes | yes | yes |
| `remote_worker` | `external_handoff` | yes | yes | yes | yes |

## 7. What We Reuse Instead Of Rebuilding
- local process/session execution
- tmux
- GitHub runner or Actions pickup path
- external worker pickup path
- third-party coding shells and repo-native helpers

## 8. Near-Term Implementation Direction
1. Keep runner capability truth in one adapter module.
2. Make launch selection and slot policy depend on adapter capability instead of scattered string checks.
3. Keep background execution spec and roadmap aligned to the adapter seam.
4. Only build native execution features when they strengthen the control plane, not when they duplicate commodity executor behavior.

## 9. References
- `docs/HOT_HARNESS_IMPORT_PLAN_20260404.md`
  - `REF-OH-1`
  - `REF-CC-2`
  - `REF-GHCA-1`
  - `REF-API-1`
