# SESSION_SEARCH_SPEC

## 1. Purpose
- This document defines the first implementation contract for `Session Search`.
- The goal is to let the operator search recovery-relevant history without reading raw logs directly.
- This feature is the first immediate harness adoption package from `docs/HARNESS_ADOPTION_PLAN.md`.

## 2. Scope

### 2.1 Phase 1
- Telegram-only command surface
- read-only search over existing artifacts
- no search index required
- no dashboard search page yet

### 2.2 Phase 2
- optional dashboard `/control/history`
- optional cached index
- optional saved searches

## 3. Canonical User Questions
- what stalled last night?
- which task was blocked by `planning_gate`?
- what happened to `REQ-...`?
- what did the last retry do?
- what happened in `O3` in the last 12 hours?
- where did `auto recover` intervene?

## 4. Command Surface

### 4.1 Primary Command
- `/history search <query>`

### 4.2 Phase 1 Supported Options
- `--project <O#|alias>`
- `--since <duration>`
- `--limit <N>`
- `--scope <control|runtime|task|dashboard|recovery|all>`

### 4.3 Examples
- `/history search planning_gate`
- `/history search --project O3 verifier_gate_failed`
- `/history search --since 12h --scope dashboard retry`
- `/history search --limit 20 REQ-20260327-...`

## 5. Search Sources

### 5.1 Primary Sources
- `.aoe-team/logs/gateway_events.jsonl`
- `.aoe-team/dashboard/action-history.jsonl`
- `.aoe-team/recovery/nightly-session-summary/*.json`
- `.aoe-team/control/latest-intent.json`
- `.aoe-team/orch_manager_state.json`

### 5.2 Why These Sources
- `gateway_events.jsonl`
  - primary event history
- `action-history.jsonl`
  - operator mutation/searchable dashboard actions
- nightly summaries
  - recovery artifacts and condensed overnight outcomes
- latest intent
  - current control routing context
- manager state
  - task/runtime enrichment and alias resolution

## 6. Normalized History Row

### 6.1 Schema
- `at`
- `scope`
- `source`
- `project_alias`
- `project_key`
- `request_id`
- `task_short_id`
- `task_title`
- `action`
- `intent_action`
- `reason_code`
- `phase`
- `status`
- `summary`
- `detail`
- `followup_hint`
- `raw_ref`

### 6.2 Field Semantics
- `at`
  - ISO timestamp used for sorting
- `scope`
  - one of:
    - `control`
    - `runtime`
    - `task`
    - `dashboard`
    - `recovery`
- `source`
  - artifact origin:
    - `gateway_events`
    - `action_audit`
    - `nightly_summary`
    - `latest_intent`
    - `manager_state`
- `project_alias`
  - display alias like `O3`
- `request_id`
  - canonical task/request identity when available
- `task_short_id`
  - display identity like `T-004`
- `action`
  - normalized operator or runtime action
- `intent_action`
  - control-plane inferred action when present
- `reason_code`
  - structured blocker/outcome reason when available
- `phase`
  - `planning`, `phase2`, `rate_limited`, etc.
- `summary`
  - compact one-line result
- `detail`
  - searchable detail payload excerpt
- `followup_hint`
  - suggested operator command
- `raw_ref`
  - evidence pointer:
    - file path
    - request/task id
    - nightly summary filename

## 7. Query Grammar

### 7.1 Phase 1 Query Mode
- query text is simple substring matching over normalized fields
- no boolean query language in Phase 1
- case-insensitive by default

### 7.2 Search Fields
- `request_id`
- `task_short_id`
- `task_title`
- `project_alias`
- `action`
- `intent_action`
- `reason_code`
- `phase`
- `status`
- `summary`
- `detail`

### 7.3 Filters
- `--project`
  - restrict to a single runtime alias
- `--since`
  - restrict by time window
- `--scope`
  - restrict by normalized scope
- `--limit`
  - output cap after filtering and sorting

## 8. Sorting And Selection
- default sort: newest first
- primary sort key: `at`
- secondary tie-breaker:
  - source priority:
    - `dashboard`
    - `task`
    - `runtime`
    - `control`
    - `recovery`

## 9. Phase 1 Output Contract

### 9.1 Compact Result Rows
- format:
  - `<idx>. <at> | <scope> | <project/task/request> | <summary>`

### 9.2 Optional Detail Block
- if a row has stronger drill-down:
  - append one or two lines:
    - `reason: ...`
    - `next: /task ...` or `/offdesk review`

### 9.3 Empty Result
- return:
  - query
  - applied filters
  - `0 matches`
  - suggested fallback commands

## 10. Pure-Read Aggregation Design

### 10.1 New Helper Module
- proposed:
  - `scripts/gateway/aoe_tg_history_search.py`

### 10.2 Responsibilities
- load source artifacts
- normalize rows
- filter rows
- sort and cap results
- render compact Telegram text

### 10.3 Non-Responsibilities
- no mutation
- no event rewriting
- no canonical state persistence
- no dashboard HTML rendering in Phase 1

## 11. Enrichment Rules

### 11.1 Manager State Enrichment
- use `orch_manager_state.json` to map:
  - `request_id -> task_short_id`
  - `request_id -> project_alias`
  - `request_id -> task title`

### 11.2 Action Audit Enrichment
- map structured dashboard outcome:
  - `outcome_reason_code`
  - `headline`
  - `next_step`
  - `remediation`

### 11.3 Nightly Summary Enrichment
- treat nightly summary as recovery-scope artifact
- expose runtime/task highlights, not raw full JSON

## 12. Guardrails
- search remains read-only
- no dashboard-only logic in Phase 1
- no new canonical search DB
- any cache/index must be rebuildable from artifacts
- must degrade cleanly if one artifact source is missing

## 13. Follow-Up Surface Mapping
- result rows should prefer existing operator drill-downs:
  - `/task <T-xxx>`
  - `/monitor <O#>`
  - `/offdesk review`
  - `/auto status`
  - nightly summary file path reference

## 14. Acceptance Criteria
1. operator can find a recent blocker by `reason_code`
2. operator can search by `request_id`
3. operator can filter by `project_alias`
4. operator can search dashboard action history and runtime history together
5. missing one source file does not break the search surface
6. results point back to existing runtime/operator surfaces instead of inventing a new workflow
