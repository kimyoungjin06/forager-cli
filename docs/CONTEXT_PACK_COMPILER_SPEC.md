# Context Pack Compiler Spec

## 1. Purpose
- Define how the control plane compiles task-scoped context instead of dumping raw folders or entire document trees into agents.
- Make context selection deterministic, inspectable, and bounded.
- Keep "document management" aligned to actual execution needs.

## 2. Core Principle
- document storage is not the goal
- task-scoped context compilation is the goal

Operational meaning:
- `WorkspaceBrief` defines where project knowledge lives
- `DocumentRegistry` defines what each document means
- `ContextPack` defines what a specific task/run should actually carry

## 3. Why This Layer Is Needed
- The agent should not load whole repos or whole doc trees by default.
- Operators need to know:
  - why a specific document was included
  - why another document was excluded
  - whether the pack is stale or missing key knowledge
- Without a compiler, context handling becomes:
  - prompt stuffing
  - folder dumping
  - accidental omission of the governing runbook/spec

## 4. Canonical Object

### 4.1 ContextPack
- `pack_id`
- `workspace_key`
- `request_id`
- `task_id`
- `profile`
  - `on_desk_plan`
  - `offdesk_execute`
  - `review`
  - `followup_preview`
  - `followup_execute`
  - `incident_recovery`
- `compile_reason`
- `objective`
- `constraints[]`
- `relevant_docs[]`
  - `doc_id`
  - `path`
  - `why_included`
  - `freshness_class`
- `runtime_context`
  - task/runtime summaries
  - recent blockers
  - background rail status
- `known_failures[]`
- `unresolved_questions[]`
- `excluded_context[]`
- `budget`
  - target doc count
  - target token/size envelope
- `compiled_at`
- `compiler_version`

### 4.2 ContextPack Policy
- `ContextPack` is compiled context truth for a specific work profile.
- It is not a second document registry.
- It must explain inclusion and exclusion decisions.
- It must stay bounded.

## 5. Inputs
- `WorkspaceBrief`
- `DocumentRegistry`
- `RequestContract`
- `ExecutionBrief` or `FollowupBrief`
- recent runtime/task summaries
- optional touched-file or lane-scope signals

## 6. Selection Rules
1. Prefer canonical documents over uncategorized notes.
2. Prefer fresher governing docs over stale but broader references.
3. Include the smallest set that explains:
   - objective
   - scope
   - constraints
   - remediation expectations
4. Preserve excluded-context reasons when a document is relevant but intentionally omitted.
5. Never silently widen the pack because an executor/model has a larger window.

## 7. Hard Rules
- No whole-tree dump as a default compilation strategy.
- No full-document ingestion just because a file is under `docs/`.
- If a stale document is still included, the pack must say so.
- If a required governing doc is missing from the registry, compilation should produce a structured warning/failure.
- The compiler must be deterministic enough that operator surfaces can inspect and compare outputs.

## 8. Output Artifact
- canonical path:
  - `<team_dir>/context_packs/<task_or_request>/<profile>.json`
- optional later:
  - rendered markdown cards for operator review

## 9. Relation To Existing Runtime
- `ContextPack` sits between:
  - workspace/document knowledge layers
  - request/execution/followup layers
- It is especially relevant for:
  - on-desk planning
  - off-desk rerun/followup execution
  - incident recovery
  - future local/open-worker model routing

## 10. Near-Term Implementation Order
1. define pack profiles and artifact format
2. compile read-only packs from existing runtime/task truth
3. surface pack summary in `/task` and dashboard `Runtime Detail`
4. let background/adapters consume packs through a stable seam

## 11. References
- `docs/WORKSPACE_ONBOARDING_SPEC.md`
- `docs/DOCUMENT_REGISTRY_SPEC.md`
- `docs/REQUEST_CONTRACT_SPEC.md`
- `docs/FOLLOWUP_BRIEF_SPEC.md`
- `docs/ROADMAP.md`
