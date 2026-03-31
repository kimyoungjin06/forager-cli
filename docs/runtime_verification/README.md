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
