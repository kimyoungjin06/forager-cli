# Current Completion Review (2026-03-27)

## 1. Purpose
- This document records the current maturity of `aoe_orch_control` by area.
- It also defines the rebased execution order for the next implementation block.
- It is intentionally blunt.
- The goal is to distinguish:
  - what is already operational,
  - what is partially mature,
  - what is the next real bottleneck.

## 2. Overall Assessment
- current overall completion:
  - `8.4 / 10`
- current product identity:
  - operationally usable owner-only orchestration package
- current limiting factor:
  - not operator shell maturity
  - runtime contract verification and document/runtime convergence

## 3. Area Scores

### 3.1 Operating Model
- score:
  - `9.2 / 10`
- current strength:
  - `Control Plane -> Project Runtime -> Task Team`
  - `Prep / Run / Recovery`
  - offdesk and morning recovery semantics are explicit
- residual gap:
  - some roadmap/project docs still lag behind the actual implemented surfaces

### 3.2 Operator Surface
- score:
  - `8.8 / 10`
- current strength:
  - Telegram
  - dashboard
  - nightly summary
  - action audit
  - history search
  - observatory hints
- residual gap:
  - document flow is not yet first-class in dashboard runtime detail

### 3.3 Plain-Text Control And Routing
- score:
  - `8.6 / 10`
- current strength:
  - offdesk/recovery prompts now route conservatively
  - latest intent and latest action are visible across surfaces
  - approval-only issues no longer over-block `policy`/`none` modes
- residual gap:
  - long-term drift still depends on keeping routing contracts explicit and tested

### 3.4 Recovery And Observability
- score:
  - `8.9 / 10`
- current strength:
  - nightly summary
  - dashboard recovery
  - session search
  - task team observatory
  - action audit and retention
- residual gap:
  - learned runbook extraction has not started
  - exact per-tool telemetry is still best-effort

### 3.5 Documentation System
- score:
  - `7.2 / 10`
- current strength:
  - multi-project registry exists
  - `ongoing.md` / `report.md` / registry / archive structure exists
  - document hierarchy is explicit
- residual gap:
  - dashboard still shows runtime progress better than document progress
  - no compiled per-project document flow artifact yet
  - no formal doc/runtime drift layer yet

### 3.6 Runtime Contract Verification
- score:
  - `6.4 / 10`
- current strength:
  - preset completion contract is documented and surfaced
  - phase1/phase2 planner drift corrections are in place
- residual gap:
  - live `Phase2` verification for `build`, `data`, `review`, `mixed` is still open
  - this is currently the highest-risk unfinished axis

### 3.7 Product Operations Discipline
- score:
  - `8.0 / 10`
- current strength:
  - centralized state root
  - migration helper
  - doctor
  - setup guidance
  - deprecated surface envelope
- residual gap:
  - broader migration workflow and compatibility notes are not fully closed
  - roadmap status still understates what is already implemented

### 3.8 Structural Health
- score:
  - `7.7 / 10`
- current strength:
  - dashboard monoliths were split
  - `run_handlers` and `scheduler_handlers` were reduced heavily
- residual gap:
  - the next real big modules are now:
    - `scripts/gateway/aoe-telegram-gateway.py`
    - `scripts/gateway/aoe_tg_task_state.py`
    - `scripts/gateway/aoe_tg_tf_exec.py`
    - `scripts/gateway/aoe_tg_offdesk_flow.py`
    - `scripts/gateway/aoe_tg_parse.py`
    - `scripts/gateway/aoe_tg_orch_contract.py`
    - `scripts/gateway/aoe_tg_scheduler_sync.py`

## 4. What Is Actually Done
- dashboard read-only control surface:
  - done
- runtime detail / task detail / recovery / audit / history:
  - done
- session search:
  - done
- task team observatory:
  - done for practical Phase 2
- centralized state root:
  - done for resolver, artifact helpers, migration helper
- doctor:
  - implemented
- setup guide:
  - implemented
- deprecated surface compatibility envelope:
  - implemented for known retired forms

## 5. What Is Not Done
- live runtime verification:
  - not done
- project flow compiler:
  - spec only
- document registry + dashboard convergence:
  - not done
- disk hygiene tied to retention strategy:
  - not done
- learned runbook extraction:
  - not done

## 6. Rebased Execution Order

### 6.1 First Block: Runtime Truth
1. `Live Runtime Verification`
- verify real `Phase2` flows for:
  - `build`
  - `data`
  - `review`
  - `mixed`
- lock actual behavior for:
  - planning
  - execution/review lanes
  - critic verdict
  - rerun/manual followup
  - `/task`
  - `/monitor`
  - `/offdesk review`

### 6.2 Second Block: Document Convergence
2. `Project Flow Compiler`
- compile per-project flow artifacts from registry + project docs + runtime state
- add `Document Flow` card to dashboard runtime detail
- add doc/runtime drift excerpts to recovery

### 6.3 Third Block: Operational Hygiene
3. `Retention And Disk Hygiene`
- tie actual cleanup policy to:
  - TTLs
  - audit retention
  - runtime artifact growth
  - centralized state root usage

### 6.4 Fourth Block: Product Operations Completion
4. `Doctor / Setup / Migration Completion`
- keep existing scripts
- close the remaining upgrade/migration workflow gaps

5. `Compatibility / Deprecation Completion`
- expand deterministic migration notes only where real legacy surface still exists

### 6.5 Fifth Block: Learned System
6. `Learned Runbook Extraction`
- only after verification and document convergence
- repeated blocker/remediation patterns become durable runbooks

### 6.6 Ongoing Structural Work
7. `Structural Debt Follow-Up`
- do this opportunistically when it supports one of the blocks above
- do not let code splitting outrun verification and convergence work

## 7. Immediate Next Sprint
1. write `Live Runtime Verification` execution spec
2. run verification scenarios for `build`, `data`, `review`, `mixed`
3. record mismatches between:
   - preset contract
   - runtime behavior
   - operator surfaces
4. only then start `Project Flow Compiler` implementation

## 8. Bottom Line
- The project is no longer blocked on shell maturity.
- The next gain will come from:
  - verifying runtime behavior,
  - compiling project document flow,
  - aligning doc truth and runtime truth on the dashboard.
