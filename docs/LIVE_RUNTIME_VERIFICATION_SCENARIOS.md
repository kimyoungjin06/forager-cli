# Live Runtime Verification Scenario Inventory

## 1. Purpose
- This document defines the first scenario inventory for `docs/LIVE_RUNTIME_VERIFICATION_SPEC.md`.
- It exists to make `Live Runtime Verification` executable instead of aspirational.

## 2. Coverage Rule
- each target preset must have at least:
  1. one happy-path completion scenario
  2. one rerun-path scenario
  3. one manual-followup-path scenario

## 3. Shared Capture Contract
- every scenario run must capture:
  - input prompt
  - expected preset
  - expected lane shape
  - expected final branch
  - stage progression
  - `/task`
  - `/monitor`
  - `/offdesk review`
  - dashboard `Task Detail`
  - dashboard `Recovery` when the task is blocked or stale

## 4. Scenario Inventory

### 4.1 Build

#### B1. Happy Path
- intent:
  - implement a bounded code/config change and leave verification evidence
- current status:
  - `executed_planning_passed`
- current finding:
  - initial `build` live runs exposed sequential planning-gate blockers: lane graph, readonly drift, standalone review subtask, owner-role drift, auth/session acceptance weakness, and single-serial policy conflict
  - latest run reaches `planning_ready`; execution evidence is the next capture target
  - visible project registration later exposed task/detail surface drift
- prompt shape:
  - patch a focused defect or integration issue
  - require a test or verification note
- expected preset:
  - `build`
- expected lane shape:
  - execution:
    - `Codex-Dev` or equivalent build role
  - review:
    - reviewer/verifier lane
- expected branch:
  - `done`
- must prove:
  - verification evidence gates completion
  - integration risk is surfaced

#### B2. Rerun Path
- intent:
  - code change lands but test/verification evidence is incomplete or failing
- expected branch:
  - `rerun`
- must prove:
  - rerun targets the relevant build lane or verifier lane
  - `/task` and dashboard show the rerun reason coherently

#### B3. Manual Followup Path
- intent:
  - code change is plausible but blocked on environment, secret, deploy, or risky mutation approval
- expected branch:
  - `manual followup`
- must prove:
  - branch is not misreported as simple retry
  - offdesk/recovery surfaces point the operator to the external dependency

### 4.2 Data

#### D1. Happy Path
- intent:
  - produce a transformed dataset, query result, or schema-bound report
- prompt shape:
  - require schema/null/sample evidence
- expected preset:
  - `data`
- expected lane shape:
  - execution:
    - data role(s)
  - review:
    - reviewer/verifier lane
- expected branch:
  - `done`
- must prove:
  - schema/null/sample evidence is visible and required

#### D2. Rerun Path
- intent:
  - transform runs but schema drift, null explosion, or broken pipeline step appears
- expected branch:
  - `rerun`
- must prove:
  - invalid output is not marked complete
  - rerun rationale surfaces concrete transform/schema failure

#### D3. Manual Followup Path
- intent:
  - transform requires operator business-rule judgment or source data interpretation
- expected branch:
  - `manual followup`
- must prove:
  - branch captures business ambiguity rather than pretending technical completion

### 4.3 Review

#### R1. Happy Path
- intent:
  - perform a review, critique, or regression scan with a supported verdict
- prompt shape:
  - no primary code change required
  - review artifact is the output
- expected preset:
  - `review`
- expected lane shape:
  - minimal execution or reviewer-led flow
  - reviewer pair remains primary
- expected branch:
  - `done`
- must prove:
  - review-only completion is possible without fake execution work
  - findings/severity/evidence are present

#### R2. Rerun Path
- intent:
  - review result is shallow or required scope was missed
- expected branch:
  - `rerun`
- must prove:
  - unsupported or incomplete verdict is not accepted as complete

#### R3. Manual Followup Path
- intent:
  - review surfaces a real tradeoff but acceptance threshold belongs to the operator
- expected branch:
  - `manual followup`
- must prove:
  - system escalates the risk decision rather than choosing for the operator

### 4.4 Mixed

#### M1. Happy Path
- intent:
  - complete work plus handoff/documentation/review output
- prompt shape:
  - implementation plus handoff/review expectation
- expected preset:
  - `mixed`
- expected lane shape:
  - execution:
    - work role(s)
    - writer/handoff role where needed
  - review:
    - reviewer lane(s)
- expected branch:
  - `done`
- must prove:
  - work/review split remains intact
  - evidence from both sides is present

#### M2. Rerun Path
- intent:
  - primary work is incomplete or handoff/review evidence drifts
- expected branch:
  - `rerun`
- must prove:
  - rerun can target the specific failing lane group
  - review lane is not treated as execution recovery by mistake

#### M3. Manual Followup Path
- intent:
  - work artifact and review/handoff artifact conflict or require packaging/scope arbitration
- expected branch:
  - `manual followup`
- must prove:
  - operator arbitration is surfaced explicitly
  - mixed preset does not collapse into generic retry when the real issue is packaging or judgment

## 5. Recommended Execution Order
1. `B1`, `D1`, `R1`, `M1`
2. `B2`, `D2`, `R2`, `M2`
3. `B3`, `D3`, `R3`, `M3`

## 6. Minimum Pass Threshold
- the first milestone is not all twelve scenarios.
- the first milestone is:
  - one happy-path scenario per preset completed and documented
- the second milestone is:
  - one rerun-path scenario per preset completed and documented
- the third milestone is:
  - one manual-followup scenario per preset completed and documented

## 7. Bottom Line
- This inventory is the bridge from the preset matrix to executable runtime proof.
- Without it, `Live Runtime Verification` stays too vague to drive fixes.
