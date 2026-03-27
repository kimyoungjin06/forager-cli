# Task Team Observatory Spec

## 1. Goal
- Turn `Task Team` visibility into diagnostic observability that helps the operator answer:
  - which lane is stale?
  - which lane is currently blocking progress?
  - what artifact or backend likely caused the block?
  - what should be inspected first?

## 2. Scope

### 2.1 Phase 1
- Add lane observability based on existing task/runtime artifacts.
- Surface:
  - Telegram `/task`
  - Telegram `/monitor`
  - dashboard `Task Detail`
  - dashboard `Recovery`
- Focus:
  - lane age
  - last event freshness
  - stale warning
  - bottleneck lane summary

### 2.2 Phase 2
- Extend lane observability with artifact-derived execution evidence.
- Focus:
  - touched files index
  - tool count
  - conflict hints
  - richer lane intervention summary

### 2.3 Out Of Scope
- dashboard-only business logic
- parsing human-facing strings back into state
- per-token billing or exact cost accounting
- generalized agent observability outside `Task Team`

## 3. Operator Questions

### 3.1 `/task`
- Which lane is oldest?
- Which lane has not advanced recently?
- Which review lane is waiting on a failed execution lane?
- Which lane should the operator inspect first?

### 3.2 `/monitor`
- Which active task is stale or bottlenecked?
- Is there an obvious blocked lane worth drilling into?

### 3.3 Dashboard
- Which lane card needs attention?
- Which blocked task in recovery should be opened first?
- Which files or backend outcomes explain the block?

## 4. Canonical Sources

### 4.1 Current Phase 1 Sources
- task plan lane topology:
  - `scripts/gateway/aoe_tg_task_state.py:267`
  - `scripts/gateway/aoe_tg_task_state.py:439`
- runtime task monitor snapshot:
  - `scripts/gateway/aoe_tg_task_state.py:288`
- task detail lane rendering:
  - `scripts/gateway/aoe_tg_task_view.py:308`
- lane topology and phase2 execution metadata:
  - `scripts/gateway/aoe_tg_tf_exec.py:284`
  - `scripts/gateway/aoe_tg_tf_exec.py:1398`

### 4.2 Current Reliable Fields
- `lane_id`
- `role`
- `kind`
- `status`
- `reason`
- `depends_on`
- `subtask_ids`
- task-level `created_at`
- task-level `updated_at`
- task-level lane summary counts
- backend/result summary already present on task rows

### 4.3 Missing Fields That Need Explicit Artifact Support
- per-lane `started_at`
- per-lane `last_event_at`
- per-lane `last_event_kind`
- per-lane `tool_count`
- per-lane `touched_files`
- per-lane `backend`
- per-lane `outcome_reason_code`

These must be written as structured fields in runtime artifacts. They must not be derived from rendered Telegram/dashboard text.

## 5. Observability Model

### 5.1 Lane Record
- `lane_id`
- `phase`
  - `execution`
  - `review`
- `role`
- `kind`
- `status`
- `reason`
- `depends_on[]`
- `subtask_ids[]`
- `started_at`
- `last_event_at`
- `last_event_kind`
- `age_sec`
- `idle_sec`
- `stale_threshold_sec`
- `is_stale`
- `backend`
- `tool_count`
- `touched_files[]`
- `outcome_reason_code`

### 5.2 Task Observability Summary
- `lane_count`
- `stale_lane_count`
- `oldest_lane_id`
- `bottleneck_lane_id`
- `bottleneck_reason`
- `last_lane_event_at`
- `conflict_file_count`
- `first_focus`

### 5.3 Runtime Recovery Summary
- `stale_task_count`
- `blocked_lane_count`
- `top_bottleneck_task_id`
- `top_bottleneck_lane_id`

## 6. Derived Rules

### 6.1 Phase 1 Lane Age
- Until per-lane timestamps exist, `age_sec` and `idle_sec` use task-level timestamps as a conservative fallback.
- UI must distinguish:
  - `task-scoped freshness`
  - `lane-scoped freshness`
- Phase 1 must not pretend task timestamp equals true lane timestamp.

### 6.2 Stale Heuristic
- Phase 1:
  - execution lane stale when task-level idle time exceeds threshold and lane status is `running` or `pending`
  - review lane stale when task-level idle time exceeds threshold and status is `waiting_on_dependencies` or `running`
- Phase 2:
  - use true lane `last_event_at`
- Threshold defaults:
  - execution: `1800s`
  - review: `1200s`
- Thresholds should remain config-backed, not hard-coded in presentation code.

### 6.3 Bottleneck Selection
- Prefer lanes with:
  - `failed`
  - `waiting_on_dependencies`
  - `is_stale`
  - oldest active age
- If multiple candidates tie, prefer review lanes over execution lanes only when the review lane is the last blocking stage.

### 6.4 Conflict Hint
- Phase 1:
  - no synthetic file conflict detection
- Phase 2:
  - only from structured `touched_files[]`
  - conflict hint requires at least two lanes touching the same file path

## 7. Surface Contract

### 7.1 Telegram `/task`
- Add compact observatory section after current phase2 lane summary.
- Required lines:
  - `team_observatory: stale=<n> bottleneck=<lane>/<reason>`
  - up to `4` lane detail rows
- Lane row shape:
  - `- obs <lane_id> [<phase>/<role>] <status> age=<...> idle=<...> note=<...>`

### 7.2 Telegram `/monitor`
- Add one compact line per active task only when meaningful:
  - `observatory: stale <lane_id> idle=<...> | first=<...>`
- No full lane tables here.

### 7.3 Dashboard `Task Detail`
- Add `Task Team Observatory` card.
- Required blocks:
  - summary stats
  - lane table
  - first focus line
- Table columns for Phase 1:
  - lane
  - phase
  - role
  - status
  - age
  - idle
  - note

### 7.4 Dashboard `Recovery`
- For blocked or stale tasks only, show observatory excerpt:
  - bottleneck lane
  - stale lane count
  - first focus

### 7.5 Nightly Summary
- Not primary surface.
- Carry only compact observatory summary for blocked/stale tasks.

## 8. Implementation Plan

### 8.1 Phase 1
1. define pure-read observatory DTO
2. compute task-scoped fallback age/idle
3. add stale heuristic
4. surface compact observatory lines in `/task` and `/monitor`
5. add dashboard observatory card and recovery excerpt

### 8.2 Phase 2
1. extend lane runtime artifacts with:
   - `started_at`
   - `last_event_at`
   - `last_event_kind`
   - `tool_count`
   - `touched_files`
   - `backend`
   - `outcome_reason_code`
2. replace task-scoped fallback with true lane freshness
3. add conflict hints
4. add touched file summary

## 9. Guardrails
- observatory fields must be rebuildable from runtime artifacts
- no string parsing from Telegram/dashboard render output
- no duplicate dashboard-only decision logic
- `Task Team Observatory` must remain subordinate to canonical task/runtime truth
- ambiguous freshness must be labeled as task-scoped fallback, not lane-scoped fact

## 10. Acceptance Criteria
- `/task` exposes bottleneck and stale lane hints for phase2 tasks
- `/monitor` exposes compact observatory hint without inflating row noise
- dashboard `Task Detail` shows lane observatory card
- dashboard `Recovery` shows observatory excerpt for blocked or stale tasks
- Phase 1 clearly differentiates fallback freshness from true lane event timestamps
- Phase 2 adds explicit lane event fields without changing operator surface semantics
