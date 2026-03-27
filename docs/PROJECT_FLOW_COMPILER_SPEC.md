# Project Flow Compiler Spec

## 1. Goal
- Turn scattered project documents into a structured `Project Flow` artifact that can be inspected from the `Control Dashboard` without replacing runtime truth.
- The compiler must answer:
  - what is this project trying to do?
  - which document milestones and decisions are currently active?
  - how does the current runtime state relate to those documents?
  - where is document/runtime drift visible?

## 2. Position In The System
- This is a convergence layer between:
  - runtime truth in `.aoe-team/orch_manager_state.json`
  - document truth in `docs/investigations_mo/registry/*` and per-project docs
- It is not a second scheduler.
- It is not a document writer in Phase 1.
- It is not allowed to invent project semantics that are not present in runtime artifacts or canonical docs.

## 3. Scope

### 3.1 Phase 1
- read-only compiler
- one compiled flow artifact per project
- dashboard/runtime consumption only
- drift detection based on existing runtime and document artifacts

### 3.2 Phase 2
- optional rendered human summary refresh
- history search integration
- stronger decision/milestone extraction
- doc/runtime drift hints in nightly recovery

### 3.3 Out Of Scope
- automatic document rewriting
- LLM-only summarization over arbitrary markdown
- replacing `ongoing.md` or `report.md` as canonical documents
- dashboard-only business logic

## 4. Canonical Inputs

### 4.1 Registry Inputs
- `docs/investigations_mo/registry/project_lock.yaml`
- `docs/investigations_mo/registry/project_registry.md`
- `docs/investigations_mo/registry/tf_registry.md`
- `docs/investigations_mo/registry/handoff_index.csv`
- `docs/investigations_mo/registry/tf_close_index.csv`

### 4.2 Per-Project Inputs
- `docs/investigations_mo/projects/<project_alias>/ongoing.md`
- `docs/investigations_mo/projects/<project_alias>/note.md`
- `docs/investigations_mo/projects/<project_alias>/tfs/<tf_id>/report.md`

### 4.3 Runtime Inputs
- `.aoe-team/orch_manager_state.json`
- `.aoe-team/control/latest-intent.json`
- `.aoe-team/recovery/nightly-session-summary/latest.json`
- `.aoe-team/dashboard/action-history.jsonl`

## 5. Output Artifacts

### 5.1 Machine Artifact
- path:
  - `.aoe-team/project-flow/<project_alias>/latest.json`
- purpose:
  - canonical compiler output for dashboard/history/recovery consumption

### 5.2 Optional Human Artifact
- path:
  - `docs/investigations_mo/projects/<project_alias>/flow.md`
- Phase 1 policy:
  - optional
  - generated only if explicitly requested later
  - not required for dashboard integration

## 6. Flow Model

### 6.1 Identity
- `project_alias`
- `project_purpose`
- `project_status`
- `active_in_lock`
- `compiled_at`

### 6.2 Runtime Convergence
- `runtime_status`
- `active_request_ids[]`
- `active_task_short_ids[]`
- `latest_runtime_phase`
- `provider_pressure_summary`
- `runtime_first_focus`

### 6.3 Document Convergence
- `ongoing_doc_path`
- `note_doc_path`
- `latest_tf_report_path`
- `open_tf_ids[]`
- `recent_closed_tf_ids[]`
- `document_objective`
- `document_next_steps[]`
- `document_open_decisions[]`
- `document_blockers[]`

### 6.4 Drift Summary
- `drift_level`
  - `none`
  - `notice`
  - `warning`
- `drift_reasons[]`
- `runtime_without_doc_signal`
- `doc_without_runtime_signal`
- `stale_doc_refs[]`

### 6.5 Evidence References
- `evidence_refs[]`
  - registry files
  - project docs
  - TF reports
  - runtime request/task ids
  - nightly summary refs

## 7. Phase 1 Extraction Rules

### 7.1 Registry Extraction
- `project_registry.md` provides project purpose/status and document paths.
- `project_lock.yaml` decides whether the project is active in the current operator context.
- `tf_registry.md` and `tf_close_index.csv` provide current TF lineage and recent closed TF lineage.

### 7.2 Project Doc Extraction
- `ongoing.md` is the primary project-level document source.
- Extract only structured signals that can be recognized conservatively:
  - top-level objective lines
  - TODO/open item bullets
  - explicit blocker sections
  - explicit decision sections
- `note.md` is secondary context only.
- `report.md` is TF-scoped evidence and recent milestone source.

### 7.3 Runtime Enrichment
- active requests/tasks come from manager state only.
- runtime phase and first-focus come from existing runtime/task helpers only.
- provider/recovery context is enrichment, not the primary project objective source.

## 8. Drift Detection Rules

### 8.1 Runtime Without Doc Signal
- active runtime exists but:
  - project missing from registry, or
  - missing `ongoing.md`, or
  - no recent TF report / no extracted objective

### 8.2 Doc Without Runtime Signal
- project marked active in docs/registry but no active runtime or no recent task lineage exists

### 8.3 Stale Doc Refs
- document refs point to missing files
- latest TF in registry has no report file
- lock file active project differs from runtime attention leader in a way that needs operator review

## 9. Dashboard Surface Contract

### 9.1 Project Runtime Detail
- add a `Document Flow` card with:
  - document objective
  - document next steps
  - latest TF lineage
  - drift level
  - evidence refs

### 9.2 Recovery
- blocked runtime rows may show:
  - `doc drift: ...`
  - `latest TF: ...`

### 9.3 History Search
- Phase 2:
  - compiled flow artifact becomes another read-only search source

## 10. Design Constraints
- compiler is read-only in Phase 1
- compiler must be rebuildable from canonical docs and runtime artifacts
- compiler must prefer deterministic parsing over speculative summarization
- compiler may normalize paths and headings, but must not invent new decisions or milestones
- compiler output must degrade cleanly when some docs are missing

## 11. Proposed Implementation Plan
1. add pure-read compiler helper
   - proposed module: `scripts/gateway/aoe_tg_project_flow.py`
2. define compiled flow JSON schema
3. load registry + per-project docs + runtime state
4. emit `.aoe-team/project-flow/<project_alias>/latest.json`
5. add dashboard `Document Flow` card to runtime detail
6. add drift warning excerpts to recovery
7. later consider optional rendered `flow.md`

## 12. Guardrails
- no mutation of project docs in the compiler path
- no dashboard-only interpretation layer
- no parsing rendered Telegram strings back into state
- if extraction is uncertain, emit `notice` drift instead of pretending certainty
