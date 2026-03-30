# Request Contract Spec

## 1. Goal
- Replace text-only execution inference with a canonical `Request Contract` layer.
- Keep plain text as the operator-facing input surface, but stop treating raw phrasing as the execution truth.
- Make planning, acceptance generation, runtime state, and operator surfaces depend on structured request fields instead of brittle keyword matches.

## 2. Why This Layer Is Needed
- Text-only inference breaks once phrasing drifts.
- The current marker-based hardening was useful for:
  - preset selection,
  - acceptance floor patches,
  - early live verification,
  but it is not strong enough to be the long-term execution contract.
- Recent `data` live verification (`D1`) showed the limit clearly:
  - first blocker: schema acceptance truncation
  - second blocker: missing input binding
  - third blocker: missing transform policy
  - current blocker: per-artifact output contract duplication
- Those are not keyword problems.
- They are missing structured fields.

## 3. Core Principle
- `plain text = UI`
- `request contract = planning truth`

Operational meaning:
- Telegram / CLI / quick plain text remains the intake surface.
- After intake routing, the system should derive a normalized request contract.
- Planning, acceptance generation, and runtime reporting should prefer that contract over raw prompt phrasing.

## 4. Position In The System

### 4.1 Before
- `text`
- `intent routing`
- `prompt -> preset inference`
- `planner/critic + acceptance floor patches`

### 4.2 After
- `text`
- `intent routing`
- `request contract extraction`
- `contract completeness gate`
- `planner/critic + acceptance generation from contract`
- `task/runtime/surface summaries from contract`

## 5. Canonical Object

### 5.1 Base Shape
- `version`
- `contract_type`
  - `build`
  - `data`
  - `review`
  - `mixed`
- `status`
  - `complete`
  - `incomplete`
  - `ambiguous`
- `objective`
- `project_key`
- `intent_action`
- `source_prompt`
- `preset`
- `readonly`
- `approval_mode`
- `fields`
  - preset-specific structured content
- `required_outputs[]`
- `required_evidence[]`
- `missing_fields[]`
- `ambiguity_notes[]`
- `summary`

### 5.2 Policy
- Once extracted, the request contract is the canonical normalized input for planning.
- Planner output may enrich the contract, but must not erase required fields already present in the contract.
- If the contract is incomplete for a preset-critical field, planning should fail closed with an explicit contract reason instead of silently guessing.

### 5.3 Relation To OrchTaskSpec
- `RequestContract` is not a second planner input beside `OrchTaskSpec`.
- It is the canonical normalization layer that must be applied before `OrchTaskSpec` is assembled.
- The enforced order is:
  1. plain text intake
  2. request contract extraction
  3. contract completeness gate
  4. `RequestContract -> OrchTaskSpec` assembly
  5. TF planning

Policy:
- TF planner should receive `OrchTaskSpec` plus contract-derived metadata, not two competing truths.
- `OrchTaskSpec` remains the planner-facing object.
- `RequestContract` remains the intake-normalization truth that constrains how `OrchTaskSpec` is built.
- When the two disagree, the implementation is wrong; planner code must not silently pick one.

## 6. Preset-Specific Shapes

### 6.1 DataRequestContract
- `source_path`
- `target_column`
- `accepted_input_formats[]`
- `normalize_to`
- `zero_pad`
- `invalid_value_policy`
  - `preserve_row`
  - `preserve_original_value`
  - `record_anomaly`
  - `drop_row`
- `output_artifacts[]`
  - `normalized_csv`
  - `schema_report`
  - `null_summary`
  - `sample_output`
- `artifact_contracts`
  - per artifact:
    - `path`
    - `format`
    - `required_fields[]`
    - `acceptance_notes[]`

### 6.2 BuildRequestContract
- `target_surface`
  - file / module / endpoint / feature area
- `public_boundary`
  - caller-visible behavior that must change
- `persisted_state_scope`
  - token/session/cache/db/file state expectations
- `verification_scope`
  - tests / checks / evidence
- `risk_scope`
  - auth / integration / migration / regression
- `required_artifacts[]`

### 6.3 ReviewRequestContract
- `review_scope`
  - code / runtime / document / artifact
- `expected_findings_shape`
  - severity / evidence / reproduction / scope
- `evidence_sources[]`
- `output_artifacts[]`
  - review memo
  - finding list
  - risk summary

### 6.4 MixedRequestContract
- `work_contract`
- `handoff_contract`
- `review_contract`
- `coordination_policy`
  - what must remain separated across Phase2 execution vs review lanes

## 7. Extraction Lifecycle

### 7.1 Intake Routing
Files:
- `scripts/gateway/aoe_tg_orch_actions.py`
- `scripts/gateway/aoe_tg_command_resolver.py`
- `scripts/gateway/aoe_tg_message_handler.py`

Role after this change:
- keep only:
  - intent class selection
  - project selection
  - safe control/offdesk/status routing
- do not make deep execution assumptions from surface wording once a work request is selected

### 7.2 Contract Extraction
New modules:
- `scripts/gateway/aoe_tg_request_contract.py`
- `scripts/gateway/aoe_tg_request_contract_data.py`
- later:
  - `scripts/gateway/aoe_tg_request_contract_build.py`
  - `scripts/gateway/aoe_tg_request_contract_review.py`
  - `scripts/gateway/aoe_tg_request_contract_mixed.py`

Responsibilities:
- infer preset-specific fields from prompt + routing context
- emit `missing_fields[]` and `ambiguity_notes[]`
- build deterministic `summary`

### 7.2A Preset Decision Precedence
The extractor must not leave preset selection as an implicit side effect of wording.

Final precedence:
1. explicit operator override
   - slash/CLI request that already names a preset or preset-bound command family
2. existing runtime lineage when the request is a retry/replan/followup of an existing task
3. contract-extractable artifact/work shape
   - explicit file transforms, schema outputs, review-only deliverables, build verification scope
4. role-preset inference from selected worker roles
5. fallback text heuristics

Policy:
- lower-precedence layers may suggest a preset but must not overrule a higher-precedence source
- if competing high-confidence signals disagree, emit `contract_conflict`
- if only weak fallback text heuristics are available, the result should prefer `ambiguous` over a confident but fragile preset lock

### 7.3 Contract Completeness Gate
Files:
- `scripts/gateway/aoe_tg_run_command_flow.py`
- `scripts/gateway/aoe_tg_plan_pipeline.py`

Responsibilities:
- reject planning when preset-critical fields are absent
- log structured reason codes such as:
  - `contract_missing.source_path`
  - `contract_missing.target_column`
  - `contract_missing.artifact_contract`
- decide whether the run can proceed to planner or must stop early

### 7.4 Planner / Critic / Acceptance Generation
Files:
- `scripts/gateway/aoe_tg_schema.py`
- `scripts/gateway/aoe_tg_plan_ensemble.py`
- `scripts/gateway/aoe_tg_control_plane.py`

Responsibilities:
- stop using marker-only acceptance floors as the primary mechanism
- generate acceptance and artifact contracts from structured request fields
- preserve explicit file-specific output contracts

### 7.5 Runtime Persistence
Files:
- `scripts/gateway/aoe_tg_run_planning_flow.py`
- `scripts/gateway/aoe_tg_task_state.py`
- `scripts/gateway/aoe_tg_tf_exec.py`

Required stored fields:
- `request_contract_type`
- `request_contract_status`
- `request_contract_summary`
- `request_contract_missing_fields[]`
- `request_contract_version`
- `request_contract_preset`
- `request_contract_fields`
- `request_contract_required_outputs[]`

Optional later fields:
- `request_contract_artifact_contracts`
- `request_contract_ambiguity_notes[]`

Persistence policy:
- `request_contract_fields` may be trimmed to the preset-minimum canonical subset
- for `data`, the persisted minimum subset must include:
  - `source_path`
  - `target_column`
  - `accepted_input_formats`
  - `normalize_to`
  - `invalid_value_policy`
- for `data`, file-producing tasks must also persist `request_contract_artifact_contracts`
- rerun/recovery/history surfaces must never reconstruct these core fields from free-text summaries if a stored contract is available

### 7.6 Operator Surface Integration
Files:
- `scripts/gateway/aoe_tg_task_view.py`
- `scripts/gateway/aoe_tg_task_state.py`
- `scripts/dashboard/control_dashboard_state_task_builders.py`
- `scripts/dashboard/control_dashboard_state_runtime_builders.py`
- `scripts/dashboard/control_dashboard_state_recovery_builders.py`
- `scripts/dashboard/nightly_session_summary.py`
- `scripts/gateway/aoe_tg_history_search.py`

Expected output additions:
- contract summary
- missing field summary
- artifact contract completeness summary
- contract-derived first focus

## 8. Failure Model

### 8.1 Allowed Fail-Fast States
- `contract_incomplete`
- `contract_ambiguous`
- `contract_conflict`

### 8.2 Not Allowed
- planner silently inventing missing input bindings
- planner silently collapsing multiple artifacts into one generic acceptance copy
- operator surfaces hiding missing contract fields behind vague blocker wording

## 9. Impact Surface

| Layer | Main Files | Required Change |
|---|---|---|
| Intake routing | `aoe_tg_orch_actions.py`, `aoe_tg_command_resolver.py`, `aoe_tg_message_handler.py` | keep shallow text routing only |
| Planning entry | `aoe_tg_run_command_flow.py`, `aoe_tg_plan_pipeline.py`, `aoe_tg_run_dispatch_flow.py` | pass request contract through the run/planning seam |
| Planner contract | `aoe_tg_schema.py`, `aoe_tg_plan_ensemble.py`, `aoe_tg_control_plane.py` | generate acceptance from contract, not markers |
| Runtime state | `aoe_tg_run_planning_flow.py`, `aoe_tg_task_state.py`, `aoe_tg_tf_exec.py` | persist contract summary and missing fields |
| Operator surfaces | `/task`, `/monitor`, dashboard builders, nightly summary | show contract completeness and missing fields |
| Search/docs/tests | `aoe_tg_history_search.py`, runtime verification docs, tests | search by contract fields and verify contract extraction |

## 10. Incremental Plan

### 10.1 Phase 1: Data Contract
Goal:
- solve the concrete `D1` blockers with a typed `DataRequestContract`

Files:
- add `aoe_tg_request_contract.py`
- add `aoe_tg_request_contract_data.py`
- wire extraction into `aoe_tg_run_command_flow.py`
- use contract-derived acceptance in `aoe_tg_schema.py`
- persist summary in `aoe_tg_task_state.py`
- expose summary in `/task` and dashboard task detail

Exit criteria:
- `D1` no longer blocks on:
  - missing input binding
  - missing transform policy
  - duplicated artifact acceptance
- `OrchTaskSpec` is assembled from `DataRequestContract` instead of raw prompt markers
- `/task` and dashboard `Task Detail` show persisted data contract fields directly

### 10.2 Phase 2: Build Contract
Goal:
- move build/auth/session acceptance from keyword floors to explicit contract fields

Exit criteria:
- build happy path and rerun path explain:
  - persisted state boundary
  - caller-visible behavior
  - verification scope

### 10.3 Phase 3: Review + Mixed
Goal:
- make review-only and mixed work/handoff/review flows contract-driven

Exit criteria:
- no fake execution tasks are needed to express review scope
- mixed handoff/review separation is contract-visible

## 11. Testing Plan

### 11.1 Unit
- extractor output for each preset
- missing field detection
- contract summary normalization

### 11.2 Planning
- acceptance generation from contract
- fail-closed behavior on incomplete contract

### 11.3 Surface
- `/task`
- `/monitor`
- dashboard `Task Detail`
- dashboard `Recovery`
- `history search`

### 11.4 Live Verification
- `D1` is the first mandatory live target
- after `data`, expand to:
  - `B*`
  - `R*`
  - `M*`

## 12. Non-Goals
- replacing plain text input with a rigid slash-only API
- requiring full structured JSON from the operator
- introducing dashboard-only logic
- adding a second task/business state stack outside runtime truth

## 13. Bottom Line
- Text-only inference remains acceptable at the intake boundary.
- It is not acceptable as the execution truth.
- The `Request Contract` layer is the canonical bridge that must sit between:
  - plain-language input
  - planner/runtime execution
  - operator-facing evidence and recovery surfaces
