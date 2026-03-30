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
  - `review/R1_happy_path.md`
    - `planned`
  - `mixed/M1_happy_path.md`
    - `planned`

## Rule
- Do not replace these artifacts with summaries detached from runtime evidence.
- Each scenario file should record:
  - expected contract
  - actual runtime evidence
  - operator surface evidence
  - mismatch and next fix
