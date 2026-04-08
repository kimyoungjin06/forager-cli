# Runtime Verification Artifacts

## Purpose
- This directory stores execution-ready and execution-result artifacts for live runtime verification.
- The governing contracts are:
  - `docs/LIVE_RUNTIME_VERIFICATION_SPEC.md`
  - `docs/LIVE_RUNTIME_VERIFICATION_SCENARIOS.md`

## Layout
- `phase2/TEMPLATE.md`
  - common capture template
- `phase2/build/`
- `phase2/data/`
- `phase2/review/`
- `phase2/mixed/`
- review-specific non-happy-path split now includes:
  - `review/R2_rerun_path.md`
  - `review/R3_manual_followup_preview.md`
  - `review/R3_manual_followup_execute.md`
  - `review/R4_external_background_rail.md`

## Current First Wave
- happy-path scenario stubs are prepared for:
  - `build/B1_happy_path.md`
  - `data/D1_happy_path.md`
  - `review/R1_happy_path.md`
  - `mixed/M1_happy_path.md`
- current execution status:
  - `build/B1_happy_path.md`
    - `executed_done`
    - finding:
      - `build` preset live verification now completes planning, execution, review, retry, and close on the live path
      - final successful run (`T-012`) reached `planning_ready`, reran once under exec critic, and closed as `done`
      - visible project registration later exposed task lineage surface drift
  - `data/D1_happy_path.md`
    - `executed_done`
    - finding:
      - live progression moved from schema acceptance truncation to prompt binding, transform policy propagation, artifact-specific output contracts, request-contract plumbing, typed month/schema/null/sample policies, and artifact-intent routing
      - final successful run (`T-038`) reached `planning_ready` and `dispatch_completed` with all four expected artifacts and verifier success evidence
  - `data/D2_rerun_path.md`
    - `executed_blocked`
    - finding:
      - live rerun-path work surfaced reusable contracts for `quality_gate_policy`, `schema_column_expectations`, numeric `null-heavy` thresholds, and `schema_value_quality_policy`
      - after those generic promotions, the remaining blocker stayed scenario-specific: `null_summary.md` evidence formatting for rerun review remained too custom to justify more core growth
  - `review/R1_happy_path.md`
    - `executed_done`
    - finding:
      - live progression moved through reusable review seams: review-only routing, reviewer-only role defaults, readonly review contracts, canonical diff-range policy, auth/session scope tracing, and section-specific acceptance for severity findings vs test gaps vs uncertainties
      - final successful run (`T-022`) reached `planning_ready`, triggered one integration retry for dirty-path evidence precision, and then closed as `done`
  - `review/R2_rerun_path.md`
    - `executed_done`
    - finding:
      - isolated live rehearsal launched exactly one lane-scoped retry over `local_tmux` and closed the background ticket with `exit_code=0`
      - `/task`, `/offdesk review`, dashboard task/runtime detail, and the retry trigger response all kept the branch on `rerun`
      - the prelaunch `pref=local_tmux | effective=local_background` status nuance is now documented as a task-specific launch-spec limitation, not a rehearsal blocker
  - `review/R3_manual_followup_preview.md`
    - `executed_done`
    - finding:
      - preview proof is now a first-class manual-followup target
      - read-only live rehearsal proved `/followup` remains read-only and agrees with `FollowupBrief.status=preview_only`
  - `review/R3_manual_followup_execute.md`
    - `executed_done`
    - finding:
      - execute proof is separate from preview proof
      - isolated live rehearsal launched exactly one execution-only `followup-exec` over `local_tmux` and closed the background ticket with `exit_code=0`
      - `/task`, `/followup`, `/offdesk review`, and dashboard task/runtime detail all kept the branch on `manual_followup` without auto-launching review remainder
  - `review/R4_external_background_rail.md`
    - `executed_done`
    - finding:
      - isolated `test_only` live rehearsal proved the full `handoff -> pickup_acknowledged -> result_received` lifecycle on real operator surfaces
      - `/orch status`, `/orch bgx-status`, `/orch bgx-result`, `/task`, and dashboard task/runtime detail all stayed phase-consistent
      - `run_lock=test_only` remained active, so `/offdesk review` stayed conservative after completion instead of recommending further mutation
  - `mixed/M1_happy_path.md`
    - `executed_done`
    - finding:
      - live runs promoted reusable `mixed` abstractions:
        - reviewer_note lane ownership and reviewer-output contracts
        - request-contract parity for `scope_inventory`
        - execution-lane deliverables/acceptance metadata
        - writer-owned handoff labeling
        - typed auth/session scope inventory and boundary policy
      - final successful run (`T-038`) reached `planning_ready`, completed execution/review/integration, and closed as `done` with `/task`, `/monitor`, and dashboard task detail evidence

## Rule
- Do not replace these artifacts with summaries detached from runtime evidence.
- Each scenario file should record:
  - expected contract
  - actual runtime evidence
  - operator surface evidence
  - mismatch and next fix

## Proof Promotion Rule
- default progression is:
  - `planned`
  - `bounded_replay_pass`
  - `live_rehearsal_ready`
  - `executed_done` or `executed_blocked`
- bounded replay should be treated as the default proof mode while:
  - `run_lock_mode=test_only`
  - internal jobs are intentionally locked down
  - runner safety or launch serializability is still being hardened
- promote to `live_rehearsal_ready` only when:
  - contract ambiguity is already removed
  - operator surface parity is already proven
  - queue / runner behavior is already bounded and inspectable
- do not skip directly from `planned` to `executed_done` for new non-happy-path rails

## Current Promotion Decision
- first live rehearsal completed:
  - `review/R3_manual_followup_preview.md`
- launch-bearing live rehearsal completed:
  - `review/R2_rerun_path.md`
- reason:
  - isolated `local_tmux` retry remained lane-scoped
  - no external pickup dependency
  - background ticket, runtime handle, and reentry rail all stayed coherent
- runbook:
  - embedded in `review/R2_rerun_path.md`
- still bounded replay only:
  - `none in the review rail first wave`
- next live candidate:
  - `review rail first wave complete`
- candidate reason:
  - `R2`, `R3 preview`, `R3 execute`, and `R4` have all crossed into executed live rehearsal`
- remaining gate:
  - `decide whether any non-review rail deserves the next live promotion`
- runbook:
  - `R4` runbook is now embedded in `review/R4_external_background_rail.md`

## Manual Followup Rule
- `manual followup` proof is now split in two:
  - preview proof
    - `FollowupBrief.status=preview_only`
    - `/followup` and dashboard preview surfaces must agree
    - preview-open seed currently also requires aligned `exec_critic.manual_followup_*` lane ids and reason
  - execute proof
    - only valid after an explicit executable `FollowupBrief` exists
    - `/followup-exec` must not be treated as equivalent to preview

## Reentry Rail Rule
- rerun/manual-followup proof is no longer just a final branch check.
- every non-happy-path proof should record:
  - `ExecutionBrief.status`
  - `FollowupBrief.status`, when present
  - `reentry_rails_summary`
  - background ticket / runner target / launch spec / evidence bundle, when used
  - external phase / next-step parity, when a non-local runner is used
  - queue scheduling evidence, when bounded replay covers retry ordering
- cross-cutting external-rail proofs may be recorded as supporting bounded replay artifacts when they validate:
  - non-local runner lifecycle parity
  - artifact inspect surfaces
  - scheduler/claim-order behavior
