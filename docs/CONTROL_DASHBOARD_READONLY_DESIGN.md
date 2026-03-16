# CONTROL_DASHBOARD_READONLY_DESIGN

## 1. Purpose
- This document specifies the first implementation phase of the `Control Dashboard`.
- Phase 1 is a read-only operations board.
- It must be directly implementable without inventing new control logic.

## 2. Phase 1 Goal
- Expose existing `Control Plane` and `Project Runtime` state as a web board.
- Reuse existing gateway helpers for summaries and contracts.
- Keep all actions outside the dashboard in Phase 1.

Success for Phase 1 means:
- the operator can open one board and understand current overnight state,
- the board reuses current runtime state,
- no dashboard-only policy engine is added.

## 3. Implementation Boundary

### 3.1 Allowed
- read runtime state files
- call existing state/view helpers
- render HTML pages
- refresh by polling or manual reload

### 3.2 Not Allowed
- dispatching new work
- changing queue state
- changing task state
- executing existing Telegram handler actions through HTTP
- duplicating business rules already implemented in gateway modules

## 4. Proposed File Layout

### 4.1 App Entry
- `scripts/dashboard/control_dashboard.py`
  - FastAPI app entrypoint
  - route registration
  - template setup
  - runtime path wiring

### 4.2 State Adapter Layer
- `scripts/dashboard/control_dashboard_state.py`
  - reads:
    - `.aoe-team/orch_manager_state.json`
    - `.aoe-team/auto_scheduler.json`
    - `.aoe-team/provider_capacity.json`
  - reuses gateway state helpers where possible
  - exposes dashboard view-model assembly helpers

### 4.3 View Layer
- `scripts/dashboard/control_dashboard_views.py`
  - maps runtime state into:
    - overview cards
    - offdesk prep cards
    - active task rows
    - task detail sections

### 4.4 Templates
- `templates/dashboard/base.html`
- `templates/dashboard/overview.html`
- `templates/dashboard/offdesk.html`
- `templates/dashboard/tasks.html`
- `templates/dashboard/task_detail.html`
- `templates/dashboard/_cards.html`
- `templates/dashboard/_task_rows.html`

### 4.5 Static
- `templates/dashboard/static/dashboard.css`
  - keep minimal
  - operator-first, dense, readable

## 5. Route Design

### 5.1 Overview
- `GET /control`
- Purpose:
  - top-level board for the `Prep`, `Run`, `Recovery` loops

### 5.2 Offdesk Prep
- `GET /control/offdesk`
- Purpose:
  - operator leaves this page open before leaving

### 5.3 Active Tasks
- `GET /control/tasks`
- Purpose:
  - web version of `/monitor`

### 5.4 Task Detail
- `GET /control/tasks/{task_short_id}`
- Purpose:
  - web version of `/task T-###`

### 5.5 Health
- `GET /control/health`
- Purpose:
  - basic readiness check for local dashboard process

## 6. Source Reuse Plan

### 6.1 Control Plane Summary
- Reuse:
  - provider capacity loading from `aoe_tg_offdesk_flow.py`
    - `provider_capacity_state_path(...)`
    - `load_provider_capacity_state(...)`
    - `load_auto_state(...)`
  - existing auto/offdesk summary logic where reusable from scheduler control handlers

### 6.2 Active Task Rows
- Reuse:
  - `summarize_task_monitor(...)`
    - `scripts/gateway/aoe_tg_task_state.py`
  - if structured extraction is needed, use task-state helpers first, not new parsing logic

### 6.3 Task Detail
- Reuse:
  - `summarize_task_lifecycle(...)`
    - `scripts/gateway/aoe_tg_task_view.py`
  - task phase / lane / backend snapshot fields already persisted in task state

### 6.4 Offdesk Cards
- Reuse:
  - `aoe_tg_offdesk_flow.py`
    - sync quality helpers
    - provider capacity loaders
    - preset hint/next focus helpers
  - avoid rebuilding offdesk priority semantics in dashboard code

### 6.5 Base Runtime Load
- Reuse:
  - `load_manager_state(...)`
    - `scripts/gateway/aoe-telegram-gateway.py`
  - only if import cost is acceptable
- fallback:
  - minimal local state loader in dashboard adapter if gateway bootstrap side effects are too heavy

## 7. Read-Only View Models

### 7.1 OverviewModel
- `auto_mode`
- `offdesk_mode`
- `provider_capacity_summary`
- `next_retry_at`
- `next_retry_target`
- `repeat_memory_summary`
- `active_runtime_count`
- `attention_runtime_cards`

### 7.2 RuntimeCardModel
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

### 7.3 ActiveTaskRowModel
- `task_short_id`
- `project_alias`
- `status`
- `tf_phase`
- `preset`
- `phase2_shape`
- `backend_summary`
- `lane_summary`
- `primary_link`

### 7.4 TaskDetailModel
- `task_short_id`
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

## 8. Rendering Rules

### 8.1 Density
- Prefer compact operator tables/cards.
- Avoid decorative UI.
- Emphasize:
  - `what is blocked`
  - `what is active`
  - `what should be looked at next`

### 8.2 Color Semantics
- neutral: informational runtime state
- yellow: warning / degraded / parked / waiting
- red: blocked / critical / repeated capacity issue
- green: ready / healthy / completed

### 8.3 Refresh
- Phase 1 default:
  - manual reload
  - optional HTMX poll every 15-30 seconds for:
    - overview summary
    - active task table
- no websocket requirement

## 9. Error Handling
- Missing state files should render a partial board, not 500 the whole page.
- If `provider_capacity.json` is absent:
  - show `provider capacity: unavailable`
- If a task id is missing:
  - task detail returns 404 with a link back to `/control/tasks`
- If gateway state schema drifts:
  - show raw fallback section only in development mode

## 10. Bootstrap and Run

### 10.1 Launch
- Local command candidate:
```bash
python3 scripts/dashboard/control_dashboard.py --project-root /path/to/project --host 127.0.0.1 --port 8765
```

### 10.2 Runtime Paths
- default source:
  - same `project_root/.aoe-team`
- no separate dashboard state dir

## 11. Acceptance Criteria

### 11.1 Overview
- Can show provider capacity, next retry, repeat summary, and top attention runtimes.

### 11.2 Offdesk
- Can show readiness cards with:
  - sync quality
  - active preset
  - phase2 shape
  - first action
  - next focus

### 11.3 Tasks
- Can show active tasks with:
  - phase
  - preset
  - backend contract summary
  - lane summary

### 11.4 Task Detail
- Can show the same core information currently visible in `/task` without dropping:
  - preset
  - quality contract
  - lanes
  - rerun/followup targets
  - backend contract

## 12. Build Order
1. app shell + route registration
2. state adapter for runtime files
3. overview page
4. offdesk page
5. active task page
6. task detail page
7. HTMX partial refresh polish if needed

## 13. Immediate Follow-up After Phase 1
- Add action buttons that call existing handlers:
  - `auto on/off`
  - `auto recover`
  - `retry`
  - `followup`
- Only after read-only parity is confirmed.
