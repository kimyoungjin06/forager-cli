# CONTROL_DASHBOARD_MVP

## 1. Goal
- Build a thin visual operations surface for the `Control Plane`.
- The first dashboard is for operations, not for replacing Telegram.
- The dashboard must support the three core loops:
  - `Prep`
  - `Run`
  - `Recovery`

## 2. Scope

### 2.1 In Scope
- Read-only overview of current `Control Plane` state
- Project-level `Project Runtime` cards
- Active `Task Team` drill-down
- Offdesk preparation and recovery visibility
- Provider capacity and retry visibility
- Read-only command/deep-link hints that mirror existing operator actions

### 2.2 Out of Scope
- Dashboard-only business logic
- A new state store
- Replacing Telegram commands
- Executing mutating operator actions through HTTP in Phase 1
- Real-time streaming infrastructure as a first milestone
- A generalized multi-user web product

## 3. Product Position
- Telegram remains the command/control surface.
- The dashboard is the visual operations surface.
- Logs/artifacts remain the evidence surface.

The dashboard must read existing runtime state first and only later trigger existing handlers.

Ultimate direction:
- Phase 1: read-only visual operations surface
- Phase 2: action-capable operations surface that calls existing handlers without introducing new policy logic

Current implementation note:
- Phase 1 currently uses `http.server.ThreadingHTTPServer + Jinja2`.
- This is an implementation baseline choice, not a product constraint.
- The route contract and DTO contract are the stable parts; the serving shell may later move to ASGI without changing operator-visible paths.

## 4. Primary Users
- Owner/operator preparing the system before leaving
- Owner/operator checking status during off-hours
- Owner/operator reviewing results and blocked work after returning

## 5. Core Views

### 5.1 Overview
- Purpose:
  - answer "is the system healthy and what needs attention now?"
- Sections:
  - global automation status
  - provider capacity summary
  - scheduled GitHub import backlog and latest failure reason
  - next retry target
  - active project count
  - blocked / parked / repeat-heavy project count
  - top attention projects
- Primary source reuse:
  - `/auto status`
  - `/offdesk review`
  - `/monitor`

### 5.2 Offdesk Prep
- Purpose:
  - answer "am I ready to leave this running tonight?"
- Sections:
  - tonight scope
  - project readiness cards
  - sync source quality
  - proposal pressure
  - active runtime task summary
  - first action / next focus
- Primary source reuse:
  - `/offdesk prepare`
  - `/offdesk review`

### 5.3 Active Tasks
- Purpose:
  - answer "what is currently running and in which phase?"
- Sections:
  - task rows matching `/monitor`
  - preset
  - phase
  - Phase2 shape
  - backend contract summary
  - lane rerun/followup targets
- Primary source reuse:
  - `/monitor`

### 5.4 Task Detail
- Purpose:
  - answer "what is happening inside this task team?"
- Sections:
  - task identity and aliases
  - phase1 planning progress
  - phase2 shape
  - phase2 quality contract
  - lane state
  - critic verdict
  - rerun/followup targets
  - backend contract note
  - provider capacity note if blocked
- Primary source reuse:
  - `/task`
- Canonical route rule:
  - `request_id` is the canonical read path
  - `project_alias + task_short_id` is a convenience path that resolves or redirects to the canonical route

### 5.5 Project Runtime Detail
- Purpose:
  - answer "what is happening in this runtime beyond the condensed board card?"
- Sections:
  - runtime readiness and sync health
  - proposals and proposal pressure
  - active task summary
  - recent completed/blocked/rate-limited tasks
  - provider pressure and repeat history
  - runtime-scoped first action / next focus
- Primary source reuse:
  - `/offdesk prepare`
  - `/offdesk review`
  - `/monitor`
- Phase:
  - not required for Phase 1
  - planned as the first structural expansion after read-only parity

## 6. Data Contract

### 6.1 Source of Truth
- `.aoe-team/orch_manager_state.json`
- `.aoe-team/auto_scheduler.json`
- `.aoe-team/provider_capacity.json`
- `.aoe-team/control/latest-intent.json`
- `.aoe-team/logs/gateway_events.jsonl` only for evidence links, not for primary page assembly

### 6.2 Reuse Rule
- If a field already exists in:
  - `aoe_tg_task_state.py`
  - `aoe_tg_task_view.py`
  - `aoe_tg_offdesk_flow.py`
  - `aoe_tg_scheduler_control_handlers.py`
then the dashboard should reuse that state/view contract rather than recomputing logic independently.
- Telegram-only summary strings are reference output, not the primary dashboard data contract.

### 6.3 Initial View Model
- `control_summary`
  - auto mode
  - offdesk mode
  - provider capacity summary
  - next retry
  - repeat memory summary
- `runtime_card`
  - alias
  - readiness
  - sync quality
  - proposals
  - active task preset
  - active task phase
  - first action
  - provider pressure
- `task_row`
  - task short id
  - project alias
  - status / phase
  - preset
  - phase2 shape
  - backend summary
  - lane summary
- `task_detail`
  - task row fields
  - phase1 progress
  - phase2 quality contract
  - lane details
  - backend contract note
  - rerun/followup targets

## 7. Interaction Model

### 7.1 MVP
- Read-only pages first
- Refresh by polling or manual reload
- Links, command strings, or deep-link hints out to Telegram/operator command equivalents are enough
- Phase 1 should prefer structured DTOs and local reload over rich interactivity.
- Read-only command hints must be assembled through a shared operator action contract.
  - `safe`:
    - read-only drill-down/status/preview commands
  - `phase2`:
    - mutation-capable commands that may later become buttons
  - Phase 1 may display both, but must only execute neither through HTTP.

### 7.2 Phase 2
- Existing actions exposed as buttons:
  - `auto on`
  - `auto off`
  - `auto recover`
  - `retry`
  - `followup`
  - `sync preview`
  - `sync bootstrap`
- These must call existing handlers, not new logic
- `Project Runtime Detail` is added here as the bridge between board cards and task detail
- Initial HTTP shortlist:
  - `POST /control/actions/task/retry`
    - source command: `/retry <task_ref> [lane <...>]`
    - class: `phase2`
  - `POST /control/actions/task/followup`
    - source command: `/followup <task_ref> [lane <...>]`
    - class: `safe`
  - `POST /control/actions/runtime/sync-preview`
    - source command: `/sync preview <project_ref> [window]`
    - class: `safe`
  - `POST /control/actions/control/auto-recover`
    - source command: `/auto recover [force]`
    - class: `phase2`
- Deferred from the first HTTP shortlist:
  - `/offdesk on`
  - `/sync bootstrap`
  - `/syncback apply`
  - `/replan`

## 8. UX Rules
- The dashboard is an operator board, not a marketing UI.
- Every card should answer one operational question quickly.
- "What should I do next?" must be visible near the top of every main view.
- Use compact summaries first, drill-down second.
- Critical capacity or blocked states must be visible without scrolling into task detail.

## 9. Technical Direction
- Server:
  - Phase 1 current shell: `http.server.ThreadingHTTPServer`
  - Future option: ASGI shell (`FastAPI`/`Starlette`) without route or DTO contract change
- Rendering:
  - `Jinja2`
  - `HTMX` for small partial refreshes if needed in later phases
- State access:
  - reuse gateway state/view helpers directly where feasible
- Authentication:
  - local/private environment only for MVP
  - bind to `127.0.0.1` only by default
- Deployment:
  - same repo, same runtime boundary, no new external service dependency

## 10. Build Sequence

### 10.1 Step 1: Read-only skeleton
- `Overview`
- `Offdesk Prep`
- `Active Tasks`
- `Task Detail`
- Hard requirement:
  - no new business logic
  - side-effect-free state adapter first
  - structured DTO reuse first
  - only state/view reuse

### 10.2 Step 2: Control actions
- `Project Runtime Detail`
- wire existing actions through thin HTTP handlers
- confirm action parity with Telegram wording
- hard requirement:
  - only commands already classified as `phase2` in the shared operator action contract are eligible for button wiring
  - `safe` commands remain read-only links/hints

### 10.3 Step 3: Recovery polish
- morning summary
- repeat-heavy project emphasis
- provider capacity escalation visibility

## 11. Acceptance Criteria
- The operator can prepare offdesk mode without switching to raw JSON or log files.
- The operator can see global capacity pressure and next retry timing from one screen.
- The operator can inspect any active task and understand:
  - preset
  - phase
  - lane shape
  - critic status
  - rerun/followup targets
- The dashboard does not introduce a second policy engine.

## 12. Risks
- Rebuilding view logic independently would fork the control model.
- Reusing only Telegram summary strings would force parsing and create drift.
- A frontend-heavy approach would slow delivery and increase maintenance cost.
- Mixing dashboard and Telegram action semantics would confuse recovery behavior.

## 13. Immediate Next Step
- Implement the read-only dashboard shell on top of existing state/view helpers.
