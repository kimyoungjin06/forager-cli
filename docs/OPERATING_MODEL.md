# OPERATING_MODEL

## 1. Purpose
- This document defines the operating model for `aoe_orch_control`.
- It exists to make the system legible as one coherent product, not just a collection of gateway features.
- The primary product goal is:
  - prepare work before leaving,
  - run project work during off-hours,
  - return to a clear recovery surface the next day.

## 2. Canonical Terms
- `Control Plane`
  - The top-level control and scheduling layer.
  - Owns cross-project routing, automation mode, remote control, and escalation policy.
- `Project Runtime`
  - The per-project execution and state layer.
  - Owns queue state, sync state, task lifecycle, and Phase1/Phase2 progression.
- `Task Team`
  - The task-scoped temporary execution unit.
  - Owns planning participants, execution lanes, review lanes, and execution artifacts.

### 2.1 Legacy Term Mapping
- `Mother-Orch` -> `Control Plane`
- `Project Orch` / `Orch` -> `Project Runtime`
- `TF` -> `Task Team`

Legacy names may remain in implementation details and historical logs until the terminology sweep is complete.

## 3. Product Definition
- The system is an owner-only control plane for operating multiple project runtimes.
- It is not a generic multi-tenant SaaS platform.
- It is not a chat-first assistant product.
- It is an operations system for:
  - nightly preparation,
  - autonomous overnight execution,
  - morning recovery and prioritization.

## 4. Core Operating Loops

### 4.1 Prep Loop
- Goal: leave the system in a safe, ready state before off-hours.
- Main actions:
  - select tonight's project scope
  - sync/update backlog state
  - run `/offdesk prepare`
  - resolve warnings through `/offdesk review`
  - decide whether to enable `/auto on`
- Main outputs:
  - a clean or explicitly acknowledged offdesk state
  - active project scope for overnight work
  - known operator overrides and escalation boundaries

### 4.2 Run Loop
- Goal: continue work autonomously while preserving safety and observability.
- Main actions:
  - select the next runnable project/runtime task
  - start or resume a task team
  - execute Phase1 planning and Phase2 execution/review
  - apply retry, followup, rerun, and provider fallback policy
  - respect provider capacity, `retry_at`, and cooldown memory
- Main outputs:
  - completed tasks
  - parked/rate-limited tasks with retry timing
  - evidence and state updates for operator review

### 4.3 Recovery Loop
- Goal: let the operator rapidly understand what happened and what to do next.
- Main actions:
  - inspect `/auto status`
  - inspect `/offdesk review`
  - inspect `/monitor` and `/task`
  - inspect the nightly session summary artifact
  - decide whether to recover, retry, follow up, or pause automation
- Main outputs:
  - nightly session summary
  - next operator action
  - resumed or paused automation state
  - clarified blocked/repeat/rate-limited situations

## 5. State Hierarchy

### 5.1 Control Plane State
- Cross-project registry
- active/focused project selection
- automation state (`auto`, `offdesk`)
- provider capacity memory
- project ordering and escalation policy

### 5.2 Project Runtime State
- backlog and sync state
- todo proposals and syncback state
- task lifecycle state
- preset classification
- phase and lane summaries
- project-local runtime metadata

### 5.3 Task Team State
- Phase1 planning progress
- Phase2 execution/review lanes
- critic verdict
- rerun/followup targets
- backend verdicts
- evidence and artifacts

## 6. Surface Model

### 6.1 Telegram
- Role: command and control surface
- Best for:
  - remote operations
  - fast commands
  - approval/recovery actions
  - quick summaries

### 6.2 Dashboard
- Role: visual operations surface
- Best for:
  - offdesk preparation
  - night monitoring
  - morning recovery review
- Constraint:
  - must reuse existing runtime state and handler logic
  - must not introduce a parallel business-logic stack

### 6.3 Logs and Artifacts
- Role: evidence surface
- Best for:
  - audit trails
  - debugging
  - replay/recovery support
  - post-run review

## 7. Execution Contract

### 7.1 Request Flow
- plain text or slash command enters the `Control Plane`
- `Control Plane` resolves a normalized action
- action is routed to a `Project Runtime`
- `Project Runtime` provisions or resumes a `Task Team`
- `Task Team` enters:
  - `Phase1` planning
  - `Phase2` execution/review

### 7.2 Completion Contract
- A task is not complete because it "ran".
- A task is complete only when:
  - its preset contract is satisfied,
  - the required evidence exists,
  - critic/reviewer expectations are satisfied,
  - unresolved risk is either cleared or surfaced to operator review.
- The preset-specific completion matrix must be defined explicitly and versioned separately from implementation.
- Live validation is not a substitute for the matrix; validation exists to prove the matrix is implementable.

### 7.3 Recovery Contract
- Provider capacity failures are treated as operational state, not generic task failure.
- `one-provider-limited` should degrade or fallback where safe.
- `both-providers-limited` should park work and wait for recovery.
- The operator must always see:
  - why a task is blocked,
  - when it can retry,
  - what the next action is.

## 8. Dashboard Direction
- The first dashboard is not a general product UI.
- It is a `Control Dashboard` for the three core loops:
  - `Prep`
  - `Run`
  - `Recovery`

### 8.1 MVP Pages
- `Overview`
- `Offdesk Prep`
- `Active Tasks`
- `Task Detail`

### 8.2 MVP Constraints
- Read-only first
- Existing state/view logic reused first
- New action buttons only after the read-only board is stable
- The first implementation gate is the shared read-only state adapter and DTO contract, not page templates.

## 9. Current Strategic Priorities
1. terminology sweep for operator-facing documents and surfaces
2. dashboard shared read-only adapter and DTO contract
3. control dashboard MVP implementation
4. preset completion matrix and live Phase2 completion validation
5. nightly session summary for Recovery Loop
6. retention/storage policy for evidence and runtime artifacts
7. structural decomposition of remaining gateway hot spots

## 10. Non-Goals For The Next Phase
- building a separate dashboard-only backend
- promoting AutoGen sandbox into the primary execution engine
- large-scale identifier rename before document and surface terminology stabilizes
- adding more fallback complexity before operator visibility is complete

## 11. Immediate Planning Artifacts
- `docs/PRESET_COMPLETION_MATRIX.md`
  - explicit preset-specific done criteria, evidence, and reviewer expectations
- `docs/NIGHTLY_SESSION_SUMMARY.md`
  - Recovery Loop summary artifact contract
- `docs/STORAGE_RETENTION_POLICY.md`
  - runtime artifact retention and storage boundaries
