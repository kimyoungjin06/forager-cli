# NIGHTLY_SESSION_SUMMARY

## 1. Purpose
- This document defines the summary artifact for the `Recovery Loop`.
- The summary exists so the operator can understand overnight activity without reconstructing state from multiple live views.

## 2. Role In The Operating Model
- The nightly session summary is a Recovery Loop artifact, not a replacement for live runtime views.
- It complements:
  - `/auto status`
  - `/offdesk review`
  - `/monitor`
  - `/task`

## 3. Required Questions
- What completed overnight?
- What is still blocked or parked?
- Which retries or recoveries happened automatically?
- Which projects or tasks repeated the same provider-capacity issue?
- What should the operator look at first this morning?

## 4. Minimum Contents
### 4.1 Control Plane Summary
- summary timestamp
- automation mode and whether it stayed on
- provider capacity summary
- next retry target if capacity-blocked work remains
- recent dashboard actions with next-step links when available

### 4.2 Project Runtime Summary
- runtime alias
- readiness/recovery posture
- completed task count
- blocked/parked task count
- latest planning compact
- latest bounded support evidence summary when present
- latest bounded support gate summary when present
- repeat-capacity signal if present
- first action / next focus

### 4.3 Task Team Summary
- task id
- preset
- final phase reached
- critic/verdict summary
- support research contract/evidence/gate snapshot when present
- rerun/manual followup targets if unresolved
- backend contract note when relevant

## 5. Source Rules
- Use the same runtime state and helper contracts as the dashboard and Telegram surfaces.
- Do not introduce a separate summary-only policy engine.
- Event logs may provide supporting evidence links, but the summary must be assembled from structured runtime state first.

## 6. Output Form
- Phase 1 target:
  - file-based summary artifact
  - generated after or alongside overnight automation windows
- Preferred shape:
  - one global summary section
  - one section per active runtime that saw overnight activity
- Phase 1 implementation path:
  - generator: `scripts/dashboard/nightly_session_summary.py`
  - default output dir: `.aoe-team/recovery/nightly-session-summary/`
  - files:
    - `latest.md`
    - `latest.json`
    - timestamped `*.md` / `*.json` copies unless `--latest-only` is used

## 7. Manual Generation
```bash
python3 scripts/dashboard/nightly_session_summary.py \
  --control-root /path/to/aoe_orch_control
```

## 8. Immediate Follow-up
- Define the generation trigger after dashboard read-only parity is stable.
- Reuse this artifact in the future `Control Dashboard` recovery view.
