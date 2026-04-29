# Planning Convergence Spec

## 1. Goal
- Replace the implicit one-shot planning assumption with an explicit convergence loop.
- Make `planning_ready` a policy outcome, not a hopeful reading of a single planner draft.
- Require at least three critical review passes before a plan may be treated as ready for execution.

## 2. Why This Exists
- Recent live verification failures showed that planning defects surface in layers:
  - missing binding
  - missing transform policy
  - artifact-contract duplication
  - accepted-format drift
  - transform ownership drift
- Those defects do not appear all at once.
- A valid planning system must assume that the first draft is incomplete and that improvement is iterative.

## 3. Core Policy

### 3.1 Minimum Review Rule
- `planning_ready` requires at least `3` critical review passes.
- A pass is counted only when:
  - a plan candidate exists,
  - critic issues are recorded structurally,
  - a repair or carry-forward decision is made for the next round.

### 3.2 Immediate Block Exceptions
- The system must not force three rounds when execution would already be unsafe or undefined.
- The following may short-circuit directly to `blocked`:
  - `contract_incomplete`
  - `contract_ambiguous`
  - unsafe mutation policy
  - invalid dependency graph
  - missing required artifact contract

### 3.3 Stalled Planning
- The loop must stop as `stalled` when repeated reviews do not improve plan quality.
- Initial stall policy:
  - the same high-severity issue survives `2` consecutive critical reviews, or
  - by review pass `3`, the blocker set is unchanged or broader than the prior pass.

## 4. Lifecycle

### 4.1 Convergence Stages
1. `contract_gate`
2. `draft`
3. `critical_review_1`
4. `repair_1`
5. `critical_review_2`
6. `repair_2`
7. `critical_review_3`
8. `decision`

### 4.2 Allowed Outcomes
- `ready`
- `blocked`
- `stalled`

Meaning:
- `ready`
  - contract complete
  - at least `3` critical reviews completed
  - no blocking issue remains
- `blocked`
  - fatal issue exists and planning must not continue
- `stalled`
  - the loop ran, but quality did not converge enough to continue safely

## 5. Critical Review Focus By Pass

### 5.1 Review 1
- contract completeness
- preset drift
- source/target binding
- scope drift

Question:
- can this plan be executed at all?

### 5.2 Review 2
- lane ownership
- dependency validity
- artifact ownership
- execution/review separation

Question:
- is the work decomposition structurally sound?

### 5.3 Review 3
- completion verifiability
- rerun/manual-followup semantics
- operator-surface explainability
- evidence sufficiency

Question:
- can reviewer and operator actually decide completion from this plan?

## 6. Issue Schema

### 6.1 Required Fields
- `issue_code`
- `severity`
  - `fatal`
  - `major`
  - `minor`
- `scope`
  - `contract`
  - `preset`
  - `lane`
  - `artifact`
  - `verification`
  - `surface`
- `target_ids`
  - lane ids / artifact ids / task ids when applicable
- `summary`
- `repair_hint`
- `first_seen_round`
- `last_seen_round`
- `resolved`

### 6.2 Starter Issue Codes
- `contract_missing.binding`
- `contract_missing.transform_policy`
- `contract_missing.artifact_contract`
- `preset_drift`
- `lane_ownership_ambiguous`
- `dependency_invalid`
- `artifact_ownership_ambiguous`
- `acceptance_too_broad`
- `completion_not_verifiable`
- `surface_explainability_gap`

## 7. Persistence

### 7.1 Required Runtime Fields
- `plan_review_count`
- `plan_issue_codes`
- `plan_issue_history`
- `plan_convergence_status`
- `plan_stalled_reason`
- `plan_last_round`

### 7.2 Policy
- Task/runtime state must preserve the issue history, not only the final blocker message.
- Recovery and `/task` must explain whether a task is:
  - still converging,
  - blocked early by contract,
  - or stalled after repeated reviews.

## 8. Surface Expectations
- `/task`
  - show `plan_review_count`
  - show `plan_convergence_status`
  - show top unresolved issue codes
- `/monitor`
  - compact convergence line
- `/offdesk review`
  - distinguish `blocked` vs `stalled`
- dashboard `Task Detail`
  - round history
  - unresolved issue list
  - stalled reason when present

## 9. Interaction With Request Contract
- `RequestContract` still gates entry into planning.
- The order is:
  1. request contract extraction
  2. contract completeness gate
  3. planning convergence loop
  4. `planning_ready` decision
  5. Phase2 dispatch
- This means:
  - incomplete requests do not consume all three review passes
  - the three-pass rule applies to plans that are valid enough to critique and repair

## 10. Immediate Rollout
1. fix this policy in docs and roadmap
2. add issue schema and convergence fields to runtime state
3. make `planning_ready` require `plan_review_count >= 3`
4. add `stalled` detection
5. rerun `D1` under the new loop before widening further preset verification

Current implementation status:
- Phase1 ensemble planning enforces at least `3` critic review passes before `ready`.
- Runtime state preserves `plan_review_count`, `plan_issue_codes`, `plan_issue_history`, `plan_convergence_status`, `plan_stalled_reason`, and `plan_last_round`.
- `stalled` detection covers repeated primary issues and the round-3 case where the blocker code set stays unchanged or grows.

## 11. Bottom Line
- Planning is not a one-shot generation step.
- Planning is a bounded convergence process.
- The system should only execute when that convergence process has produced a plan that survived at least three critical reviews.
