# HARNESS_ADOPTION_PLAN

## 1. Purpose
- This document turns the `docs/OH_MY_CLAUDE_BENCHMARK_20260327.md` findings into an actionable adoption plan for `aoe_orch_control`.
- Strategic alignment and adoption filters are defined separately in:
  - `docs/HARNESS_ALIGNMENT_PRINCIPLES.md`
- It is intentionally selective.
- The goal is not to copy `oh-my-claudecode`.
- The goal is to import the parts of its harness quality that strengthen the existing `Control Plane -> Project Runtime -> Task Team` model.

## 2. Adoption Principles
1. Preserve the existing operating model.
- `Control Plane`, `Project Runtime`, and `Task Team` remain canonical.
- We are improving the harness around the system, not replacing the core model.

2. Prefer observability and recoverability over more modes.
- New modes, aliases, and keyword-heavy UX are out of scope.
- Search, replay, visibility, and state continuity are in scope.

3. Reuse runtime truth.
- New harness features must read existing runtime artifacts first.
- They must not create a second business-logic stack.

4. Favor artifact-first contracts.
- Durable summaries, snapshots, and audits are preferred over ad hoc live inference.

5. Design for owner-only operations.
- The current package is for a single operator.
- Multi-tenant or public-web assumptions are out of scope.

## 3. Scope Summary

### 3.1 Immediate Adoption
1. `Session Search`
2. `Task Team Observatory`
3. `Centralized State Root`

### 3.2 Medium-Term Adoption
1. `Doctor / Setup / Migration Discipline`
2. `Compatibility / Deprecation Envelopes`
3. `Delegation Contract Enforcement`

### 3.3 Long-Term Adoption
1. `Learned Runbook Extraction`
2. `Artifact-First Runtime Monitors`

## 4. Package A: Session Search

### 4.1 Goal
- Let the operator answer:
  - what happened,
  - when it happened,
  - why it stalled,
  - which task/runtime/request was involved
without grepping raw logs manually.

### 4.2 User Surface
- Telegram:
  - `/history search <query>`
  - `/history search --project O3 <query>`
  - `/history search --since 12h <query>`
- Dashboard:
  - `/control/history`

### 4.3 Sources
- `.aoe-team/logs/gateway_events.jsonl`
- `.aoe-team/recovery/nightly-session-summary/*.json`
- `.aoe-team/dashboard/action-history.jsonl`
- `latest-intent.json`
- task/runtime state snapshots from `.aoe-team/orch_manager_state.json`

### 4.4 Search Dimensions
- `request_id`
- `task_short_id`
- `project_alias`
- `reason_code`
- `action`
- `intent_action`
- `phase`
- `backend_verdict`
- `provider_capacity`
- free-text message snippet

### 4.5 Output Contract
- compact result rows:
  - timestamp
  - scope (`control|runtime|task|dashboard`)
  - key id (`request_id`, `T-xxx`, `O#`)
  - summary
  - follow-up command hint
- detail drill-down:
  - existing `/task`
  - `/monitor`
  - `/offdesk review`
  - nightly summary file reference

### 4.6 Design Constraints
- search is read-only
- no new canonical state store
- index/cache may exist, but must be rebuildable from existing artifacts

### 4.7 Implementation Plan
1. add pure-read history aggregation helper
2. define normalized history row schema
3. add Telegram `/history search`
4. add read-only dashboard `/control/history`
5. detailed Phase 1 contract is defined in:
   - `docs/SESSION_SEARCH_SPEC.md`

## 5. Package B: Task Team Observatory

### 5.1 Goal
- Turn `Task Team` visibility from “status summary” into “diagnostic observability”.

### 5.2 Operator Questions To Answer
- which lane is stale?
- which lane is the bottleneck?
- which files were touched?
- which runtime/backend caused the block?
- what should the operator inspect first?

### 5.3 User Surface
- `/task`
  - lane age
  - last event
  - touched file summary
  - block reason
  - stale/conflict warnings
- `/monitor`
  - compact observatory hints only
- dashboard `Task Detail`
  - richer lane observatory card
- dashboard `Recovery`
  - stale lane / bottleneck summary for blocked tasks

### 5.4 Observability Fields
- per lane:
  - `started_at`
  - `last_event_at`
  - `last_event_kind`
  - `elapsed_sec`
  - `stale_threshold_sec`
  - `touched_files[]`
  - `tool_count`
  - `backend`
  - `outcome_reason_code`
- per task:
  - `slowest_lane`
  - `stale_lane_count`
  - `file_conflict_count`
  - `last_blocker`

### 5.5 Source Strategy
- Prefer existing runtime/event artifacts.
- Add explicit per-lane event fields where needed rather than deriving them from display strings.

### 5.6 Phase Plan
1. lane age + last event
2. touched files index
3. stale warning heuristics
4. file conflict detection
5. bottleneck summary and intervention hints
6. detailed Phase 1 contract is defined in:
   - `docs/TASK_TEAM_OBSERVATORY_SPEC.md`

## 6. Package C: Centralized State Root

### 6.1 Goal
- Preserve orchestration state across worktree deletion, clone churn, and temporary runtime directories.

### 6.2 Current Limitation
- Current default state remains rooted at `<project_root>/.aoe-team`.
- This is simple and local, but fragile across worktree lifecycle.

### 6.3 Proposed Contract
- new optional env/config:
  - `AOE_STATE_DIR`
- stable project identifier:
  - git remote URL hash first
  - local path hash fallback
- resulting structure:
  - `$AOE_STATE_DIR/<project-id>/`

### 6.4 State Split
- canonical persistent state:
  - manager state
  - provider capacity
  - latest intent
  - action audit
  - recovery artifacts
- runtime view/shim:
  - `<project_root>/.aoe-team`
  - compatibility files and local pointers

### 6.5 Migration Strategy
- if centralized state exists, prefer it
- if only legacy `.aoe-team` exists, continue using it
- if both exist:
  - emit a clear notice
  - define precedence
  - provide migration command later

### 6.6 Risks
- path indirection drift
- multiple clones of same repo sharing state unintentionally
- operator confusion if state root is not visible enough

### 6.7 Guardrails
- include resolved state root in `/status`-like surfaces
- keep fallback to local `.aoe-team`
- document migration clearly

### 6.8 Current Implementation Status
- Phase 1 core resolver is implemented.
- current precedence:
  - explicit `team_dir`
  - `AOE_TEAM_DIR`
  - centralized `AOE_STATE_DIR/<project-id>/` when it already exists
  - legacy `<project_root>/.aoe-team` when legacy state already exists
  - centralized `AOE_STATE_DIR/<project-id>/` for new state with no legacy directory
- `telegram_gateway_state.json` now follows the resolved team dir instead of assuming local `.aoe-team`.
- foreign project rows loaded from manager state repair their `team_dir` using the same centralized-state-aware default.
- resolved state root is now surfaced in `/auto`, `/offdesk`, and dashboard control summary/recovery views.
- provider-capacity, latest-intent, action-audit, dashboard history search, and nightly summary paths now share runtime-core artifact resolvers.
- copy-first migration helper is now available:
  - `python3 scripts/gateway/aoe_tg_state_root_migration.py --project-root <repo> --state-dir <AOE_STATE_DIR>`
  - add `--apply` to copy missing artifacts into the centralized state root
  - add `--force` to overwrite already-present target files

## 7. Package D: Doctor / Setup / Migration Discipline

### 7.1 Goal
- Turn operational bootstrap and upgrade into a supported workflow, not tribal knowledge.

### 7.2 Needed Surfaces
- `doctor`
  - state path health
  - dashboard health
  - scheduler/stack health
  - missing dependencies
  - retention/audit issues
- `setup`
  - initial `.aoe-team` / dashboard / systemd guidance
- `migration`
  - renamed commands
  - deprecated surfaces
  - state-root migration

### 7.3 Priority
- medium-term
- should follow `AOE_STATE_DIR` because migration logic depends on it
- current status:
  - standalone `doctor` script added first
  - standalone `setup guide` script added next
  - initial checks:
    - resolved state root / drift warnings
    - artifact readability
    - runtime config presence
    - binary presence
  - setup guidance now covers:
    - runtime bootstrap
    - env file creation
    - state-root migration
    - systemd install
    - local dashboard launch
    - doctor rerun
  - broader upgrade envelopes remain open

## 8. Package E: Compatibility / Deprecation Envelopes

### 8.1 Goal
- Avoid silent drift when a command or surface is retired.

### 8.2 Design
- a deprecated surface must return:
  - machine-readable code
  - human-readable replacement
  - optional migration note

### 8.3 Candidate Uses
- old terminology aliases
- old command forms
- legacy dashboard routes if replaced later

### 8.4 Current Status
- shared deterministic deprecation envelope added
- code-centric retired-surface inventory added
- initial retired surfaces:
  - `mother-orch`
  - `swarm`
  - `orch map`
  - `tasks` / `board`
  - `lifecycle`
  - `follow-up`
  - `off-desk`
  - `cleanup`
- current response contract:
  - machine-readable `code`
  - canonical `replacement`
  - human `note`
  - optional `next` operator hint

## 9. Package F: Delegation Contract Enforcement

### 9.1 Goal
- Centralize provider/model/role defaults that are currently repeated across call sites.

### 9.2 Immediate Relevance
- we already made progress on `Control Plane` provider decoupling
- the next step is to reduce caller-by-caller drift in agent/provider selection

### 9.3 Scope
- not a direct copy of OMC's model injection
- adapted to:
  - provider order
  - preset role defaults
  - critic/integration defaults
  - backend selection defaults

## 10. Package G: Learned Runbook Extraction

### 10.1 Goal
- Repeated recovery judgments should become durable operator knowledge.

### 10.2 Source Candidates
- nightly summary repeated blockers
- repeated `reason_code`
- repeated dashboard remediation actions
- repeated offdesk/recovery notes

### 10.3 Outputs
- `docs/runbooks/*.md`
- or `.aoe-team/learned/*.json` + rendered docs

### 10.4 Timing
- long-term
- depends on session search and observability first

## 11. Sequence

### 11.1 Phase 1
1. `Session Search`
2. `Task Team Observatory`
3. `Centralized State Root` design and partial implementation

### 11.2 Phase 2
1. `Doctor / Setup / Migration`
2. `Compatibility / Deprecation Envelopes`
3. `Delegation Contract Enforcement`

### 11.3 Phase 3
1. `Learned Runbook Extraction`
2. stronger artifact-first runtime convergence

## 12. Recommended Immediate Execution Order
1. `Session Search`
- highest recovery ROI
- low conceptual risk
- directly uses artifacts already present

2. `Task Team Observatory`
- biggest operator visibility improvement
- builds on existing lane/task/runtime model

3. `AOE_STATE_DIR`
- foundational, but touches path contracts and migration
- better after search/observability requirements are concrete

## 13. Non-Goals
- copying OMC’s mode vocabulary
- replacing Telegram with dashboard
- turning this package into a Claude Code plugin clone
- adding more natural-language ambiguity through extra keywords

## 14. Success Criteria
- operator can search past failures and recoveries without raw grep
- task/runtime views show actionable lane diagnostics, not just status labels
- state continuity survives worktree churn when centralized state is enabled
- setup/update/deprecation become explicit, documented workflows
