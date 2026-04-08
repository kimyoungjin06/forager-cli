# Harness Authoring Adapter Spec

## 1. Purpose
- Define how upstream harness generators such as `revfactory/harness` plug into our stack.
- Keep them on the authoring side:
  - agent team design
  - skill generation
  - orchestrator scaffolding
- Do not let them become the canonical runtime truth.

## 2. Boundary
- upstream harnesses are not:
  - `ExecutionBrief`
  - `FollowupBrief`
  - `Background Run Ticket`
  - dashboard/off-desk/recovery truth
- they are:
  - authoring modules
  - topology/skill generators
  - on-desk generation helpers

## 3. Canonical Adapter
- module:
  - `scripts/gateway/aoe_tg_harness_authoring_adapter.py`
- export command:
  - `scripts/gateway/aoe_tg_harness_authoring_export.py`
- current role:
  - inspect vendored upstream harness layout
  - build a read-only authoring plan from:
    - `WorkspaceBrief`
    - `DocumentRegistry`
    - `ContextPack`
  - expose canonical output targets:
    - `.claude/agents`
    - `.claude/skills`

## 4. Current Upstream Target
- upstream repo:
  - `https://github.com/revfactory/harness`
- current interpretation:
  - benchmark for `agent topology / skill compiler`
  - not a replacement for our off-desk runtime

## 5. Hard Rules
1. runtime truth remains ours
- upstream harness output must not replace:
  - request normalization
  - execution feasibility
  - task/runtime state

2. adapter input must be compiled context
- `WorkspaceBrief`
- `DocumentRegistry`
- `ContextPack`

3. adapter output is generated authoring material
- agent definitions
- skill definitions
- orchestrator scaffolds

4. vendor availability must be explicit
- missing vendor tree is a valid state
- adapter must fail readably, not implicitly

## 6. Current Read-Only Plan Shape
- `repo_url`
- `vendor.available`
- `vendor.vendor_root`
- `vendor.patterns[]`
- `workspace_key`
- `project_alias`
- `context_pack_profile`
- `context_pack_summary`
- `document_registry_summary`
- `selected_doc_ids[]`
- `authoring_targets`
  - `.claude/agents`
  - `.claude/skills`
- `summary`

## 7. Vendoring Strategy
- preferred near-term strategy:
  - `git subtree`
- rationale:
  - upstream can keep moving
  - local runtime does not depend on vendoring details
  - authoring adapter reads a stable folder boundary
- current import path:
  - `vendor/revfactory-harness`

## 8. Near-Term Roadmap
1. pin upstream under `vendor/revfactory-harness`
2. keep adapter read-only first
3. add explicit compile/export command later
4. only then connect generated agent/skill output into on-desk workflows

## 9. References
- `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
- `docs/HARNESS_ADOPTION_PLAN.md`
- `docs/MODEL_HARNESS_ROUTING_BASIS_20260408.md`
