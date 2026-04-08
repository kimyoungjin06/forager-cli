# Workspace Onboarding Spec

## 1. Purpose
- Define the canonical contract for attaching a real project folder to the control plane.
- Prevent ad hoc "just point at a repo" behavior that hides:
  - document roots
  - todo/source-of-truth paths
  - model routing defaults
  - background execution policy
- Make workspace registration deterministic before:
  - `RequestContract`
  - `ExecutionBrief`
  - background execution
  - document/context compilation

## 2. Why This Layer Is Needed
- A raw `project_root` path is not enough to operate a project safely.
- The control plane needs stable runtime metadata before it can answer:
  - where canonical docs live
  - where generated/runtime artifacts live
  - which runner/model policy applies
  - which paths should be ignored
- Without an explicit workspace layer, document registry and context-pack compilation will drift across:
  - CLI-only runs
  - dashboard/off-desk views
  - future external worker adapters

## 3. Canonical Object

### 3.1 WorkspaceBrief
- `version`
- `workspace_key`
- `project_alias`
- `project_root`
- `state_root`
- `team_dir`
- `project_overview`
- `code_roots[]`
- `doc_roots[]`
- `doc_ignore_globs[]`
- `canonical_todo_path`
- `canonical_runbook_paths[]`
- `model_routing_profile`
- `background_runner_target`
- `run_lock_mode_default`
- `background_runner_slot_limits`
- `endpoint_registry_path`
- `routing_policy_path`
- `onboarding_status`
  - `draft`
  - `validated`
  - `active`
  - `stale`
- `validation_notes[]`
- `summary`

### 3.2 Policy
- `WorkspaceBrief` is the workspace-normalization truth.
- `RequestContract` and `ExecutionBrief` are task/request truths.
- The control plane must not infer doc roots, model policy, or todo ownership from random filesystem scanning once a `WorkspaceBrief` exists.
- Secrets or raw API keys must not be stored in `WorkspaceBrief`.
- Hostnames and ports may be referenced indirectly via:
  - `endpoint_registry_path`
  - `routing_policy_path`
  but canonical credentials remain outside runtime truth.

## 4. Onboarding Lifecycle

### 4.1 Discover
- choose `project_root`
- resolve `state_root`
- resolve `team_dir`
- identify initial:
  - code roots
  - document roots
  - canonical todo path

### 4.2 Validate
- verify `project_root` exists
- verify `team_dir`/`state_root` are writable
- reject doc roots that point into obvious generated/vendor areas
- confirm runtime policy defaults:
  - model routing profile
  - background runner target
  - run lock default
  - slot limits

### 4.3 Register
- write canonical workspace metadata
- make operator surfaces depend on `WorkspaceBrief` instead of local ad hoc inference
- allow downstream builders to read:
  - document registry inputs
  - context-pack inputs
  - model endpoint routing inputs

### 4.4 Refresh
- onboarding is not write-once
- the workspace layer must support:
  - doc-root changes
  - routing-policy changes
  - runner-policy changes
  - stale workspace detection when the filesystem no longer matches the registered brief

## 5. Hard Rules
- No project runtime should be treated as fully onboarded without a `WorkspaceBrief`.
- `doc_roots` must be explicit; "scan the whole repo" is not a canonical default.
- Generated, vendor, cache, and runtime artifact paths must be excluded by default unless explicitly re-added.
- `canonical_todo_path` must be singular or explicitly absent; multiple silent todo owners are not allowed.
- Onboarding must fail closed if:
  - `project_root` is missing
  - `team_dir`/`state_root` cannot be resolved
  - document roots collide with ignored/generated paths in a way that makes registry output ambiguous

## 6. Relation To Existing Layers
- `WorkspaceBrief` feeds:
  - `DocumentRegistry`
  - `ContextPack`
  - model endpoint routing
  - background execution defaults
- `WorkspaceBrief` does not replace:
  - `RequestContract`
  - `ExecutionBrief`
  - `FollowupBrief`
- It is an upstream project/runtime layer, not a task-planning layer.

## 7. Suggested Artifact Shape
- canonical path:
  - `<team_dir>/workspace_brief.json`
- read-only summary surfaces:
  - `/orch status`
  - dashboard `Runtime Detail`
  - dashboard `Offdesk`
- later mutable surfaces:
  - CLI onboarding command
  - dashboard workspace settings page

## 8. Near-Term Implementation Order
1. read-only `WorkspaceBrief` artifact and loader
2. CLI onboarding/bootstrap helper
3. dashboard runtime card parity
4. doc-registry builder wired to `doc_roots`
5. context-pack compiler wired to workspace + runtime truth

## 9. References
- `docs/EXECUTOR_ADAPTER_ARCHITECTURE.md`
- `docs/REQUEST_CONTRACT_SPEC.md`
- `docs/MODEL_ENDPOINT_ADAPTER_SPEC.md`
- `docs/ROADMAP.md`
