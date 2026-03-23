# STORAGE_RETENTION_POLICY

## 1. Purpose
- This document defines storage and retention boundaries for runtime artifacts.
- It exists because evidence is necessary, but uncontrolled retention becomes an operational risk.

## 2. Storage Classes
### 2.1 Canonical Runtime State
- examples:
  - `.aoe-team/orch_manager_state.json`
  - `.aoe-team/auto_scheduler.json`
  - `.aoe-team/provider_capacity.json`
- policy:
  - keep current live state
  - rotate backups conservatively
  - never treat these as disposable caches

### 2.2 Evidence And Artifacts
- examples:
  - task handoff files
  - archived close summaries
  - explicit review artifacts
- policy:
  - retain long enough for morning recovery and audit
  - prune only with clear archival rules

### 2.3 Ephemeral Runtime Artifacts
- examples:
  - worktree caches
  - temporary run dirs
  - transient execution outputs
- policy:
  - TTL-based cleanup is expected
  - these should not silently become permanent evidence

### 2.4 Logs And Rooms
- examples:
  - gateway event logs
  - room logs
  - replay-supporting traces
  - dashboard action audit (`.aoe-team/dashboard/action-history.jsonl`)
- policy:
  - retain for operational debugging and replay
  - rotate/prune on explicit retention windows

## 3. Planning Direction
- Retention policy must cover:
  - task-team run artifacts
  - provider capacity memory
  - nightly summaries
  - room/event logs
  - dashboard-related snapshots and action audit trails

## 4. Immediate Follow-up
- Connect this policy to:
  - TTL settings already in use
  - disk hygiene checks
  - future storage location split between fast local state and larger evidence/archive storage
