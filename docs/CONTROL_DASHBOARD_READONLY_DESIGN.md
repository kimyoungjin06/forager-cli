# CONTROL_DASHBOARD_READONLY_DESIGN

## 1. Purpose
- This document specifies the first implementation phase of the `Control Dashboard`.
- Phase 1 is a read-only operations board.
- It must be directly implementable without inventing new control logic.

## 2. Phase 1 Goal
- Expose existing `Control Plane` and `Project Runtime` state as a web board.
- Reuse existing gateway helpers for summaries and contracts.
- Reuse structured runtime fields, not Telegram-only display strings, as the primary source.
- Keep all actions outside the dashboard in Phase 1.

Success for Phase 1 means:
- the operator can open one board and understand current overnight state,
- the board reuses current runtime state,
- no dashboard-only policy engine is added.

## 3. Implementation Boundary

### 3.1 Allowed
- read runtime state files
- call existing side-effect-free state/view helpers
- build structured dashboard DTOs
- render HTML pages
- refresh by polling or manual reload

### 3.2 Not Allowed
- dispatching new work
- changing queue state
- changing task state
- executing existing Telegram handler actions through HTTP
- duplicating business rules already implemented in gateway modules
- parsing human-oriented Telegram summary strings back into structure

## 4. Required Pre-Implementation Decisions

### 4.1 Shared State Adapter Boundary
- The dashboard must not import ad-hoc loader logic directly from multiple gateway modules.
- A single side-effect-free adapter boundary is required first.
- Preferred direction:
  - `scripts/dashboard/control_dashboard_state.py` owns runtime file reads
  - it may call side-effect-free helper functions from gateway modules
  - it must not depend on Telegram polling, tmux bootstrap, or message send paths

### 4.2 Structured DTO Rule
- Telegram summary helpers are not the primary data contract for the dashboard.
- The dashboard must consume structured DTOs first and render them separately.
- Required DTO groups:
  - `ControlSummaryDTO`
  - `RuntimeCardDTO`
  - `ActiveTaskRowDTO`
  - `TaskDetailDTO`
- Telegram surfaces may later be refactored to consume the same DTOs, but Phase 1 may keep Telegram rendering unchanged.

### 4.3 Stable Route Identity
- `task_short_id` is treated as project-scoped, not globally unique.
- Phase 1 canonical task detail route:
  - `GET /control/tasks/by-request/{request_id}`
- Convenience route:
  - `GET /control/{project_alias}/tasks/{task_short_id}`
  - this route may redirect to the canonical `request_id` route after lookup
- `project_alias + task_short_id` is operator-friendly, but not the immutable canonical key.

### 4.4 Security Boundary
- Phase 1 is loopback-only.
- Required defaults:
  - host binds to `127.0.0.1`
  - no public listen by default
  - no reverse-proxy/public deployment guidance in Phase 1
- non-loopback bind should fail fast in Phase 1
- Read-only does not mean low-sensitivity; runtime state remains operator-sensitive.

### 4.5 Snapshot Consistency
- A page render may combine multiple runtime files, so exact transactional consistency is not assumed.
- Phase 1 must explicitly expose:
  - `snapshot_taken_at`
  - per-file freshness metadata where possible
- If a file is temporarily unreadable during write:
  - use last-known-good snapshot when available
  - render a stale/fallback note instead of failing the page

### 4.6 Control Root Assumption
- The dashboard is a board for one `Control Plane`, not one project.
- Phase 1 runtime source must be rooted at the control repo/runtime root.
- Canonical launch inputs:
  - `--control-root`
  - or `--team-dir`
- `--project-root` must not be the canonical top-level input for the dashboard.

## 5. Proposed File Layout

### 5.1 App Entry
- `scripts/dashboard/control_dashboard.py`
  - FastAPI app entrypoint
  - route registration
  - template setup
  - runtime path wiring
  - validates loopback-only bind in Phase 1
  - resolves `control_root` / `team_dir`

### 5.2 State Adapter Layer
- `scripts/dashboard/control_dashboard_state.py`
  - reads:
    - `.aoe-team/orch_manager_state.json`
    - `.aoe-team/auto_scheduler.json`
    - `.aoe-team/provider_capacity.json`
  - reuses gateway state helpers where possible
  - exposes dashboard DTO assembly helpers
  - returns:
    - `snapshot_taken_at`
    - file freshness metadata
    - dashboard DTOs only
  - must be pure read only
  - must not initialize, migrate, or mutate runtime files

### 5.3 View Layer
- `scripts/dashboard/control_dashboard_views.py`
  - renders DTOs into:
    - overview cards
    - offdesk prep cards
    - active task rows
    - task detail sections

### 5.4 Templates
- `templates/dashboard/base.html`
- `templates/dashboard/overview.html`
- `templates/dashboard/offdesk.html`
- `templates/dashboard/tasks.html`
- `templates/dashboard/task_detail.html`
- `templates/dashboard/_cards.html`
- `templates/dashboard/_task_rows.html`

### 5.5 Static
- `static/dashboard/dashboard.css`
  - keep minimal
  - operator-first, dense, readable

## 6. Route Design

### 6.1 Overview
- `GET /control`
- Purpose:
  - top-level board for the `Prep`, `Run`, `Recovery` loops

### 6.2 Offdesk Prep
- `GET /control/offdesk`
- Purpose:
  - operator leaves this page open before leaving

### 6.3 Active Tasks
- `GET /control/tasks`
- Purpose:
  - web version of `/monitor`

### 6.4 Task Detail
- `GET /control/tasks/by-request/{request_id}`
- Purpose:
  - stable web version of `/task`

### 6.5 Convenience Task Route
- `GET /control/{project_alias}/tasks/{task_short_id}`
- Purpose:
  - operator-friendly shortcut that resolves to the canonical `request_id` route

### 6.6 Health
- `GET /control/health`
- Purpose:
  - basic readiness check for local dashboard process

## 7. Source Reuse Plan

### 7.1 Control Plane Summary
- Reuse:
  - provider capacity loading from `aoe_tg_offdesk_flow.py`
    - `provider_capacity_state_path(...)`
    - `load_provider_capacity_state(...)`
    - `load_auto_state(...)`
  - existing auto/offdesk summary logic where reusable from scheduler control handlers

### 7.2 Active Task Rows
- Reuse:
  - structured task-state helpers in `scripts/gateway/aoe_tg_task_state.py`
  - `summarize_task_monitor(...)` may be used only as parity/reference output, not as the primary parse source

### 7.3 Task Detail
- Reuse:
  - structured task fields and task-state helpers first
  - `summarize_task_lifecycle(...)` may be used only as parity/reference output
  - task phase / lane / backend snapshot fields already persisted in task state

### 7.4 Offdesk Cards
- Reuse:
  - `aoe_tg_offdesk_flow.py`
    - sync quality helpers
    - provider capacity loaders
    - preset hint/next focus helpers
  - avoid rebuilding offdesk priority semantics in dashboard code
  - extract a shared `RuntimeCardDTO` assembler if current helper reuse still leaves dashboard-specific policy joins

### 7.5 Base Runtime Load
- Reuse:
  - `load_manager_state(...)`
    - `scripts/gateway/aoe-telegram-gateway.py`
  - only if import cost is acceptable and the import path is proven side-effect-free
- preferred fallback:
  - extract a side-effect-free runtime loader helper and share it between gateway and dashboard
- forbidden fallback:
  - using a loader that mutates runtime state during page read
  - duplicating manager state interpretation in a second hand-written loader

### 7.6 DTO Ownership Rule
- `scripts/dashboard/control_dashboard_state.py` may assemble dashboard DTOs, but it must not become a second policy layer.
- If a DTO field requires non-trivial joins across runtime/offdesk/priority logic, the join should move into a shared side-effect-free gateway helper first.
- The dashboard adapter may map and normalize; it must not invent new runtime semantics.

## 8. Source Mapping Contract

### 8.1 ControlSummaryDTO
- `auto_mode`
  - source: `auto_scheduler.json`
- `offdesk_mode`
  - source: `auto_scheduler.json` and/or offdesk state helpers
- `provider_capacity_summary`
  - source: `provider_capacity.json` via offdesk/provider helpers
- `next_retry_at`
  - source: `provider_capacity.json`
- `next_retry_target`
  - source: `provider_capacity.json`
- `repeat_memory_summary`
  - source: `provider_capacity.json`
- `active_runtime_count`
  - source: `orch_manager_state.json`
- `attention_runtime_cards`
  - source: manager state + offdesk helper assembly

### 8.2 RuntimeCardDTO
- `project_alias`, `project_label`
  - source: manager state registry
- `readiness`, `sync_quality`, `proposal_pressure`
  - source: offdesk helper assembly
- `active_task_label`, `active_task_phase`, `active_task_preset`, `active_task_phase2_shape`
  - source: project active task entry + task state helpers
- `first_action_text`, `next_focus_text`
  - source: offdesk priority/preset helpers
- `provider_pressure`, `provider_repeat_count`
  - source: provider capacity state + queue/offdesk helpers

### 8.3 ActiveTaskRowDTO
- `task_short_id`, `request_id`, `project_alias`, `status`, `tf_phase`
  - source: task state / manager state
- `preset`, `phase2_shape`, `backend_summary`, `lane_summary`
  - source: task state helpers
- `primary_link`
  - source: canonical request-id route assembly

### 8.4 TaskDetailDTO
- core identity/state fields
  - source: task record
- `phase1_progress`, `phase1_providers`, `phase1_candidate_roles`
  - source: task record
- `phase2_shape`, `phase2_quality`, `phase2_evidence`
  - source: task record + task view/task state helpers
- `lane_states`, `rerun_targets`, `manual_followup_targets`
  - source: task record + task state helpers
- `backend_summary`, `backend_contract_note`
  - source: task backend snapshot
- `provider_capacity_note`
  - source: provider capacity state joined by request/task/project linkage

## 9. Read-Only View Models

### 9.1 ControlSummaryDTO
- `snapshot_taken_at`
- `state_freshness`
- `auto_mode`
- `offdesk_mode`
- `provider_capacity_summary`
- `next_retry_at`
- `next_retry_target`
- `repeat_memory_summary`
- `active_runtime_count`
- `attention_runtime_cards`

### 9.2 RuntimeCardDTO
- `project_alias`
- `project_label`
- `readiness`
- `sync_quality`
- `proposal_pressure`
- `active_task_label`
- `active_task_phase`
- `active_task_preset`
- `active_task_phase2_shape`
- `first_action_text`
- `next_focus_text`
- `provider_pressure`
- `provider_repeat_count`

### 9.3 ActiveTaskRowDTO
- `task_short_id`
- `request_id`
- `project_alias`
- `status`
- `tf_phase`
- `preset`
- `phase2_shape`
- `backend_summary`
- `lane_summary`
- `primary_link`

### 9.4 TaskDetailDTO
- `snapshot_taken_at`
- `task_short_id`
- `request_id`
- `project_alias`
- `status`
- `phase1_progress`
- `phase1_providers`
- `phase1_candidate_roles`
- `phase1_role_preset`
- `phase2_team_preset`
- `phase2_shape`
- `phase2_quality`
- `phase2_evidence`
- `lane_states`
- `rerun_targets`
- `manual_followup_targets`
- `backend_summary`
- `backend_contract_note`
- `provider_capacity_note`

## 10. Rendering Rules

### 10.1 Density
- Prefer compact operator tables/cards.
- Avoid decorative UI.
- Emphasize:
  - `what is blocked`
  - `what is active`
  - `what should be looked at next`

### 10.2 Color Semantics
- neutral: informational runtime state
- yellow: warning / degraded / parked / waiting
- red: blocked / critical / repeated capacity issue
- green: ready / healthy / completed

### 10.3 Refresh
- Phase 1 default:
  - manual reload
  - optional HTMX poll every 15-30 seconds for:
    - overview summary
    - active task table
- no websocket requirement
- initial implementation may ship with manual reload only; polling is optional

### 10.4 List Caps and Sorting
- `/control/tasks`
  - default sort:
    - active/running first
    - then warning/degraded
    - then newest updated
  - default cap: `50`
- `Overview` attention cards
  - default cap: `12`
  - sort by existing offdesk/priority semantics
- `Offdesk Prep` runtime cards
  - default cap: `20` before explicit expansion
  - sort by readiness severity, then provider pressure, then first-action urgency
- list caps should be explicit in the adapter/view layer, not hidden in templates

## 11. Snapshot and Freshness Policy
- Every page render should record `snapshot_taken_at`.
- When possible, expose freshness for:
  - manager state
  - auto scheduler state
  - provider capacity state
- If one file is stale or temporarily unreadable:
  - keep rendering the page
  - mark stale sections explicitly
  - avoid mixing healthy styling with stale data

## 12. Error Handling
- Missing state files should render a partial board, not 500 the whole page.
- If `provider_capacity.json` is absent:
  - show `provider capacity: unavailable`
- If a task id is missing:
  - task detail returns 404 with a link back to `/control/tasks`
- If gateway state schema drifts:
  - show raw fallback section only in development mode

## 13. Security and Binding

### 12.1 Binding
- Phase 1 must bind to `127.0.0.1` only by default.
- Any non-loopback bind must be treated as out of scope for Phase 1.
- Passing `0.0.0.0` or a non-loopback host should hard-fail at startup.

### 12.2 Exposure
- No auth scheme is added in Phase 1 because the board is local-only.
- If public exposure is needed later, it must be treated as a new phase with explicit auth and deployment design.

## 14. Bootstrap and Run

### 14.1 Launch
- Local command candidate:
```bash
python3 scripts/dashboard/control_dashboard.py --control-root /path/to/control/repo --host 127.0.0.1 --port 8765
```

### 14.2 Runtime Paths
- default source:
  - same `control_root/.aoe-team`
- no separate dashboard state dir

## 15. Acceptance Criteria

### 15.1 Overview
- Can show provider capacity, next retry, repeat summary, and top attention runtimes.

### 15.2 Offdesk
- Can show readiness cards with:
  - sync quality
  - active preset
  - phase2 shape
  - first action
  - next focus

### 15.3 Tasks
- Can show active tasks with:
  - phase
  - preset
  - backend contract summary
  - lane summary

### 15.4 Task Detail
- Can show the same core information currently visible in `/task` without dropping:
  - preset
  - quality contract
  - lanes
  - rerun/followup targets
  - backend contract

### 15.5 Explicit Non-Goals for Phase 1
- no mutating HTTP actions
- no runtime detail page yet
- no project/runtime policy logic that does not already exist in gateway helpers

## 16. Test Contract

### 15.1 Route Smoke
- HTML smoke tests for:
  - `/control`
  - `/control/offdesk`
  - `/control/tasks`
  - `/control/tasks/by-request/{request_id}`
  - `/control/{project_alias}/tasks/{task_short_id}`

### 15.2 Parity Checks
- `Overview` retains the same critical fields visible in `/auto status`
- `Active Tasks` retains the same critical fields visible in `/monitor`
- `Task Detail` retains the same critical fields visible in `/task`
- `Offdesk Prep` retains the same critical fields visible in `/offdesk prepare`
- convenience route resolves to the same canonical task detail as the request-id route

### 15.3 Failure Cases
- missing `provider_capacity.json`
- stale `auto_scheduler.json`
- missing task detail target
- malformed but recoverable runtime file snapshot

### 15.4 Snapshot Semantics
- tests should assert `snapshot_taken_at` is rendered
- tests should assert stale sections are marked when one runtime file is unavailable

## 17. Build Order
1. side-effect-free state adapter for runtime files
2. DTO assembly layer
3. app shell + route registration
4. loopback-only launch guard + control-root wiring
5. overview page
6. offdesk page
7. active task page
8. task detail page
9. HTMX partial refresh polish if needed

## 18. Immediate Follow-up After Phase 1
- Add `Project Runtime Detail` page
- Add action buttons that call existing handlers:
  - `auto on/off`
  - `auto recover`
  - `retry`
  - `followup`
- Only after read-only parity is confirmed.
