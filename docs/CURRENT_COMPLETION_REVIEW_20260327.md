# Current Completion Review (rebased 2026-04-28)

## 1. Purpose
- This document records the current maturity of `aoe_orch_control` by area.
- It also defines the rebased execution order for the next implementation block.
- It is intentionally blunt.
- The goal is to distinguish:
  - what is already operational,
  - what has been proven but still needs productization,
  - what is now the next real bottleneck.

## 2. Overall Assessment
- current overall completion:
  - `8.7 / 10`
- current product identity:
  - operationally usable owner-only orchestration package
- current limiting factor:
  - not operator shell maturity
  - not first-wave runtime contract verification
  - document/runtime convergence, external runner productionization, and governance/usage boundaries

## 3. Area Scores

### 3.1 Operating Model
- score:
  - `9.3 / 10`
- current strength:
  - `Control Plane -> Project Runtime -> Task Team`
  - `Prep / Run / Recovery`
  - offdesk and morning recovery semantics are explicit
  - rerun/manual-followup reentry rails now have live proof across presets
- residual gap:
  - some roadmap/project docs still lag behind the actual implemented surfaces

### 3.2 Operator Surface
- score:
  - `9.0 / 10`
- current strength:
  - Telegram
  - dashboard
  - nightly summary
  - action audit
  - history search
  - observatory hints
  - runtime detail exposes brief/background/document-registry/context-pack summaries
- residual gap:
  - document flow is not yet a first-class compiled dashboard card
  - doc/runtime drift is not yet surfaced as a durable operator signal

### 3.3 Plain-Text Control And Routing
- score:
  - `8.8 / 10`
- current strength:
  - offdesk/recovery prompts route conservatively
  - latest intent and latest action are visible across surfaces
  - approval-only issues no longer over-block `policy`/`none` modes
  - followup preview and followup execute are split by `FollowupBrief` status
- residual gap:
  - long-term drift still depends on keeping routing contracts explicit and tested

### 3.4 Recovery And Observability
- score:
  - `9.0 / 10`
- current strength:
  - nightly summary
  - dashboard recovery
  - session search
  - task team observatory
  - action audit and retention
  - reentry rails and background ticket summaries are inspectable
- residual gap:
  - learned runbook extraction has not started
  - exact per-tool telemetry is still best-effort

### 3.5 Documentation System
- score:
  - `7.8 / 10`
- current strength:
  - multi-project registry exists
  - `WorkspaceBrief`, `DocumentRegistry`, and `ContextPack` baselines are implemented
  - context-pack summaries are surfaced in `/task` and dashboard task/runtime detail
  - document-registry summaries are surfaced in `/orch status` and dashboard runtime detail
- residual gap:
  - no compiled per-project `Project Flow` artifact yet
  - no formal doc/runtime drift layer yet
  - dashboard still shows runtime progress better than document progress

### 3.6 Runtime Contract Verification
- score:
  - `8.8 / 10`
- current strength:
  - preset completion contract is documented and surfaced
  - phase1/phase2 planner drift corrections are in place
  - all first-wave phase2 scenario docs are `executed_done`
  - build/data/review/mixed each have happy-path, rerun-path, and manual-followup proof
  - external background rail support proof is recorded as `R4`
- residual gap:
  - production non-local runner pickup/ack worker is still not closed
  - broader governance/usage boundaries are not yet first-class product surfaces

### 3.7 Product Operations Discipline
- score:
  - `8.2 / 10`
- current strength:
  - centralized state root
  - migration helper
  - doctor
  - setup guidance
  - deprecated surface envelope
  - runtime verification documentation now has consistency tests
- residual gap:
  - broader upgrade/migration workflow remains open
  - disk hygiene is not yet fully tied to the retention strategy
  - roadmap/status docs need periodic consistency checks when large milestones close

### 3.8 Structural Health
- score:
  - `7.7 / 10`
- current strength:
  - dashboard monoliths were split
  - `run_handlers` and `scheduler_handlers` were reduced heavily
  - document/context/model/background seams now exist as separate modules
- residual gap:
  - the next real big modules are now:
    - `scripts/gateway/aoe_tg_orch_task_handlers.py`
    - `scripts/gateway/aoe_tg_task_state.py`
    - `scripts/gateway/aoe_tg_live_rehearsal_seed.py`
    - `scripts/gateway/aoe_tg_worker_task_contract.py`
    - `scripts/gateway/aoe_tg_offdesk_flow.py`
    - `scripts/gateway/aoe_tg_orch_contract.py`
    - `scripts/gateway/aoe_tg_request_contract.py`

## 4. What Is Actually Done
- dashboard read-only control surface:
  - done
- runtime detail / task detail / recovery / audit / history:
  - done
- session search:
  - done
- task team observatory:
  - done for practical Phase 2
- live runtime verification:
  - first-wave phase2 scenario set is `executed_done`
  - build/data/review/mixed each have happy-path, rerun-path, and manual-followup proof
  - R4 external background rail support proof is recorded
  - doc/inventory/status consistency is guarded by `tests/gateway/test_runtime_verification_docs.py`
- workspace/document/context baseline:
  - `WorkspaceBrief` implemented
  - `DocumentRegistry` implemented
  - `ContextPack` compiler implemented
  - `ProjectFlow` compiler baseline implemented
  - summaries are exposed on task/runtime/operator surfaces
- centralized state root:
  - done for resolver, artifact helpers, migration helper
- doctor:
  - implemented
- setup guide:
  - implemented
- deprecated surface compatibility envelope:
  - implemented for known retired forms

## 5. What Is Not Done
- project flow dashboard/recovery integration:
  - minimal `.aoe-team/project-flow/<project_alias>/latest.json` artifact exists
  - dashboard `Project Runtime Detail` consumption is wired
  - recovery/nightly consumption is wired through compact doc/runtime drift excerpts
- document registry + dashboard convergence:
  - baseline summaries exist
  - first-class `Document Flow` dashboard card is done
  - doc/runtime drift excerpts are present in nightly summary and recovery
- external runner productionization:
  - `github_runner` / `remote_worker` lifecycle is now backed by worker-run pickup, ack/result/log sidecars, and artifact import
  - baseline issue/PR comment trigger and completion callback ergonomics are implemented for trusted artifact-only GitHub runner dispatch
  - scheduled GitHub import drain is wired into the long-running local auto scheduler by default
- governance / permissions / usage:
  - owner-only safety baseline exists
  - usage reporting, budget boundaries, and secret-redaction surfaces remain open
- disk hygiene tied to retention strategy:
  - not done
- learned runbook extraction:
  - not done

## 6. Rebased Execution Order

### 6.1 First Block: Runtime Truth
1. `Live Runtime Verification`
- status:
  - complete for the first-wave phase2 scenario set
- locked behavior:
  - planning
  - execution/review lanes
  - critic verdict
  - rerun/manual followup
  - `/task`
  - `/monitor`
  - `/offdesk review`
  - dashboard task/runtime/recovery surfaces

### 6.2 Second Block: Document Convergence
2. `Project Flow Compiler`
- compile per-project flow artifacts from registry + project docs + runtime state
  - baseline implemented
- add `Document Flow` card to dashboard runtime detail
  - implemented
- add doc/runtime drift excerpts to recovery
  - implemented

### 6.3 Third Block: External Execution Productization
3. `External Runner Pickup / Ack`
- baseline non-local worker loop implemented through `aoe-background-worker.py worker-run`
- preserve the same operator-visible lifecycle:
  - `handoff`
  - `pickup_acknowledged`
  - `result_received`
- remaining productization:
  - baseline SCM/GitHub workflow trigger bridge implemented
  - sidecar artifact import + local poll bridge implemented
  - `gh run download` artifact retrieval + import bridge implemented
  - baseline credentials / transport policy implemented
  - baseline issue/PR comment ergonomics implemented
  - baseline workflow completion callback implemented
  - baseline local run wait + artifact import orchestration implemented
  - baseline ticket-named GitHub run discovery + scheduled local import drain implemented
  - baseline long-running stack/timer hook for scheduled GitHub import drain implemented
  - remaining: live comment-triggered GitHub runner verification and dashboard-level scheduled import backlog/failure visibility

### 6.4 Fourth Block: Operational Hygiene
4. `Retention And Disk Hygiene`
- tie actual cleanup policy to:
  - TTLs
  - audit retention
  - runtime artifact growth
  - centralized state root usage

### 6.5 Fifth Block: Product Operations Completion
5. `Doctor / Setup / Migration Completion`
- keep existing scripts
- close the remaining upgrade/migration workflow gaps

6. `Compatibility / Deprecation Completion`
- expand deterministic migration notes only where real legacy surface still exists

### 6.6 Sixth Block: Learned System
7. `Learned Runbook Extraction`
- only after verification and document convergence
- repeated blocker/remediation patterns become durable runbooks

### 6.7 Ongoing Structural Work
8. `Structural Debt Follow-Up`
- do this opportunistically when it supports one of the blocks above
- do not let code splitting outrun convergence work

## 7. Immediate Next Sprint
1. implement the minimal `Project Flow Compiler` artifact:
   - `.aoe-team/project-flow/<project_alias>/latest.json`
   - status: done
2. wire dashboard runtime detail to a read-only `Document Flow` card
   - status: done
3. surface conservative doc/runtime drift in recovery/nightly summary
   - status: done
4. then move to production external runner pickup/ack

## 8. Bottom Line
- The project is no longer blocked on shell maturity.
- The first-wave runtime verification block is closed.
- The next gain will come from:
  - compiling project document flow,
  - aligning doc truth and runtime truth on the dashboard,
  - productizing the external runner lifecycle beyond test-only proof.
