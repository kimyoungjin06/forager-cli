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
    - `bounded_replay_pass`
    - finding:
      - bounded replay now proves the current rerun rail:
        - review-only routing remains in `review`
        - retry lane selection stays bounded
        - background retry rails (`local_tmux`, `github_runner`) preserve runner-specific ticket state
        - run lock / slot saturation block retry coherently
      - the old `T-030` blocker remains captured only as legacy reference inside the scenario file
  - `review/R3_manual_followup_preview.md`
    - `bounded_replay_pass`
    - finding:
      - preview proof is now a first-class manual-followup target
      - bounded replay now proves `/followup` remains read-only and agrees with `FollowupBrief.status=preview_only`
  - `review/R3_manual_followup_execute.md`
    - `bounded_replay_pass`
    - finding:
      - execute proof is separate from preview proof
      - bounded replay now proves `/followup-exec` is only valid after an explicit executable or partially executable `FollowupBrief` exists
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

## Manual Followup Rule
- `manual followup` proof is now split in two:
  - preview proof
    - `FollowupBrief.status=preview_only`
    - `/followup` and dashboard preview surfaces must agree
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
