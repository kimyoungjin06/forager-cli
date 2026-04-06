# Live Runtime Verification Spec

## 1. Purpose
- This document defines how `aoe_orch_control` proves that the documented `Task Team` preset contracts are implementable in live runtime behavior.
- It exists because:
  - `docs/PRESET_COMPLETION_MATRIX.md` defines the contract,
  - but the contract must also be verified against real `Phase2` execution and review flows.

## 2. Goal
- Verify that each target preset can move through:
  - planning,
  - Phase2 lane construction,
  - execution/review progression,
  - critic/verifier handling,
  - rerun/manual followup branching,
  - operator surface rendering
without drifting from the preset completion matrix.

## 3. Scope

### 3.1 Phase 1 Verification Scope
- target presets:
  - `build`
  - `data`
  - `review`
  - `mixed`
- required surfaces:
  - `/task`
  - `/monitor`
  - `/offdesk review`
  - dashboard `Task Detail`
  - dashboard `Recovery`

### 3.2 Out Of Scope
- proving every backend-specific implementation detail
- benchmarking cost/token usage
- replacing the preset matrix with ad hoc live judgment
- broad UI polish unrelated to preset verification

## 4. Canonical Inputs
- `docs/PRESET_COMPLETION_MATRIX.md`
- `docs/ORCH_CORE_CONTRACT.md`
- `docs/REQUEST_CONTRACT_SPEC.md`
- `docs/OPERATING_MODEL.md`
- runtime state in `.aoe-team/orch_manager_state.json`
- task history and lane topology from runtime artifacts
- task team execution artifacts
- dashboard/runtime operator surfaces

## 5. Verification Questions

### 5.1 Planning
- was the correct preset selected?
- did on-desk classify the request correctly as:
  - `executable`
  - `underspecified`
  - `infeasible`
  - `partially_executable`
  - `operator_decision_required`?
- was the `phase2_team_preset` stable and visible?
- was the expected execution/review shape materialized explicitly?

### 5.2 Execution / Review
- were execution lanes appropriate for the preset?
- did review/critic lanes preserve the expected separation?
- did the task reach the expected lifecycle stages?

### 5.3 Completion / Recovery
- was `done` only reached with the required evidence?
- did off-desk stay inside the executable slice declared by on-desk?
- when the flow was incomplete, did it choose:
  - `rerun`
  - `manual followup`
  - `blocked`
  coherently?

### 5.4 Surface Parity
- did `/task`, `/monitor`, `/offdesk review`, dashboard `Task Detail`, and dashboard `Recovery` all expose the same essential interpretation?

## 6. Verification Matrix

| Preset | Scenario Focus | Must Prove | Common Failure To Catch |
|---|---|---|---|
| `build` | implementation + verification evidence | test/verification evidence gates completion, integration risk is visible | code diff looks complete but verification evidence is weak or absent |
| `data` | transform integrity + schema evidence | schema/null/sample evidence is surfaced and required | output exists but schema/null integrity is not proven |
| `review` | review-only completeness | review artifact can complete without fake execution work, but still carries evidence and severity | shallow review marked complete without supported findings/scope |
| `mixed` | work + documentation/review split | work lanes and handoff/review lanes stay distinct, evidence from both is visible | writer/reviewer drift into execution or mismatched handoff/completion states |

## 7. Scenario Design

### 7.1 Per-Preset Scenario Set
- each target preset should have at least:
  1. happy-path completion
  2. rerun-eligible incomplete path
  3. manual-followup-required path

### 7.2 Required Evidence For Each Scenario
- request text / normalized action
- request contract summary
- execution brief status and executable slice
- selected preset and lane topology
- stage progression:
  - planning
  - execution
  - verification
  - integration
  - close
- final verdict:
  - done
  - rerun
  - manual followup
  - blocked
- surface snapshots:
  - `/task`
  - `/monitor`
  - `/offdesk review`
  - dashboard task/recovery view when relevant

## 8. Pass Criteria

### 8.1 Preset Pass
- preset classification is correct
- on-desk feasibility classification is coherent
- expected lane shape exists
- lifecycle stages progress coherently
- completion verdict matches `docs/PRESET_COMPLETION_MATRIX.md`
- off-desk execution does not exceed the briefed scope
- operator surfaces agree on the essential status and next action

### 8.2 Verification Batch Pass
- all target presets have at least one passing happy-path scenario
- all target presets have at least one verified non-happy-path scenario
- no surface contradiction remains unresolved

## 9. Failure Classes

### 9.1 Planning Drift
- wrong preset
- wrong execution-brief status
- missing or implicit Phase2 lane staffing
- review shape drift

### 9.2 Lifecycle Drift
- task stages do not match actual lane outcomes
- verification/integration state is over-optimistic

### 9.3 Completion Drift
- `done` without required evidence
- `done` outside the executable slice
- rerun/manual followup branch chosen inconsistently
- preset-specific risk not surfaced

### 9.4 Surface Drift
- runtime state says one thing but `/task` / dashboard say another
- recovery surface loses the critical blocker or next action

## 10. Execution Plan

### 10.1 Step 0: On-desk Brief Classification
- record:
  - request contract summary
  - execution brief status
  - executable slice
  - blocked slice or operator decision, when relevant

### 10.2 Step 1: Scenario Fixture Definition
- define one scenario file or note set per target preset
- each scenario must state:
  - prompt
  - expected preset
  - expected brief status
  - expected lane shape
  - expected completion branch

### 10.3 Step 2: Live/Replay Execution
- run scenarios through the current runtime path
- prefer replayable or isolated testable inputs where possible

### 10.4 Step 3: Surface Capture
- capture operator-visible outputs from:
  - `/task`
  - `/monitor`
  - `/offdesk review`
  - dashboard pages if applicable

### 10.5 Step 4: Contract Comparison
- compare actual results to:
  - preset matrix
  - ORCH core contract
  - execution brief classification
  - expected scenario branch

### 10.6 Step 5: Result Recording
- record:
  - pass/fail
  - drift class
  - blocking mismatch
  - required fix owner

## 11. Recording Format

### 11.1 Verification Artifact
- proposed path:
  - `docs/runtime_verification/phase2/<preset>/<scenario>.md`

### 11.2 Minimum Contents
- scenario summary
- expected contract
- actual runtime evidence
- surface evidence
- result:
  - `pass`
  - `fail`
  - `blocked`
- mismatch notes

## 12. Immediate Implementation Order
1. define scenario inventory for:
   - `build`
   - `data`
   - `review`
   - `mixed`
2. run the first happy-path verification for each preset
3. run rerun/manual-followup scenarios
4. document mismatches before fixing them
5. use the resulting evidence to decide the next runtime fixes

## 13. Bottom Line
- This work is not optional polish.
- It is the step that proves our current preset/runtime model is operationally valid.
- `Project Flow Compiler` and later learned runbooks should build on these verified flows, not replace them.
