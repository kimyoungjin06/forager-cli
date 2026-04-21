# LangChain deepagents Benchmark (2026-04-21)

## 1. Purpose
- Benchmark the current `LangChain deepagents` surface against `aoe_orch_control`.
- Decide what should be imported as an implementation pattern versus what should stay outside our control plane boundary.
- Keep the answer concrete:
  - what deepagents already does well
  - what we already do better or differently
  - what is worth copying into our stack

## 2. Current Read
- `deepagents` is a coding-oriented agent framework, not an operator control plane.
- Its center of gravity is:
  - deep planning
  - subagent delegation
  - file-system / sandbox backends
  - reusable skills and memory
- Our center of gravity is different:
  - runtime truth
  - recovery truth
  - audit truth
  - operator surfaces across dashboard + Telegram + tmux

## 3. Strong Upstream Ideas

### 3.1 Built-in Planning Tool
- `deepagents` ships with an explicit planning tool instead of treating planning as invisible prompt behavior.
- This is directionally aligned with our recent `planning_compact`, `approved_plan_gate`, and `critic` surfaces.
- Benchmark conclusion:
  - copy the explicitness, not the framework.
  - keep our canonical plan truth in task/runtime state instead of moving it into an external agent runtime.

### 3.2 First-Class Subagents
- `deepagents` treats subagents as a native composition unit rather than an ad hoc pattern.
- This matches what we are starting to do with bounded `general_research` support artifacts.
- Benchmark conclusion:
  - we should keep adding typed subagent contracts.
  - we should not allow subagents to own dispatch/apply state directly.

### 3.3 Filesystem / Sandbox Backends
- `deepagents` explicitly exposes different execution backends for local or constrained file work.
- This is useful as a design reference for our executor adapters.
- Benchmark conclusion:
  - copy backend abstraction ideas into `ExecutorAdapter` and worker launch policy.
  - do not let backend choice become a second source of runtime truth.

### 3.4 Skills + Reusable Memory
- `deepagents` has a cleaner story for reusable skills and memory than our current support-lane artifacts.
- Our nearest equivalent is:
  - context packs
  - workspace brief
  - document registry
  - support artifact summaries
- Benchmark conclusion:
  - skills are worth copying as authoring/runtime helpers.
  - long-term memory should remain bounded and inspectable, not hidden behind agent-private state.

## 4. Where Our Stack Is Already Stronger

### 4.1 Recovery Surfaces
- `deepagents` is not built around:
  - nightly recovery artifacts
  - recovery dashboard views
  - action audit trails
  - repeat-memory for operator remediation
- This is one of our differentiated areas.

### 4.2 Canonical Runtime Truth
- We already have explicit state contracts for:
  - request/task truth
  - execution brief truth
  - followup truth
  - background run truth
  - action audit truth
- `deepagents` can inform execution patterns, but it should not become the owner of these records.

### 4.3 Operator Routing
- Our system is explicitly split across:
  - `on-desk`
  - `off-desk`
  - dashboard
  - Telegram
  - tmux rails
- `deepagents` is useful inside execution lanes, not as the top-level operator product.

## 5. Recommended Adoption Boundary

### 5.1 Good Candidates To Import
1. A stronger typed planning-tool contract for coding workers.
2. More formal subagent profiles beyond `general_research`.
3. Backend policy seams for local vs sandboxed execution.
4. Reusable skill bundles for bounded authoring/research tasks.

### 5.2 Things We Should Not Import As-Is
1. Hidden long-lived agent memory as canonical truth.
2. Agent-owned routing or recovery policy.
3. Framework-owned task lifecycle state.
4. File backend state that is not mirrored into our audit/runtime contracts.

## 6. Concrete Implications For `aoe_orch_control`
1. Keep `aoe_orch_control` as the canonical control plane.
2. Continue growing support-lane artifacts:
   - contract
   - evidence summary
   - gate summary
   - artifact path
3. Add at least two more bounded subagent kinds after `general_research`.
- candidates:
  - `codebase_diff_scan`
  - `vendor_pattern_alignment`
4. Add backend policy metadata to executor surfaces so the operator can see:
   - local
   - tmux
   - background
   - sandboxed
5. Consider a lightweight skill registry for:
   - recovery helpers
   - authoring helpers
   - review helpers

## 7. Decision
- `deepagents` is worth benchmarking as an execution-pattern library.
- It is not the right replacement for our control plane.
- The correct move is:
  - import the good patterns
  - keep runtime/recovery/audit truth local
  - expose every imported behavior through our existing operator surfaces

## 8. Near-Term Follow-up
1. Define a second bounded subagent contract beside `general_research`.
2. Add backend-policy visibility to runtime and recovery surfaces.
3. Prototype a small skill registry that maps cleanly onto context packs and bounded support artifacts.

## 9. References
- `SRC-DA-1`
  - LangChain deepagents overview
  - https://docs.langchain.com/oss/python/deepagents/overview
  - reviewed 2026-04-21
- `SRC-DA-2`
  - LangChain deepagents memory
  - https://docs.langchain.com/oss/python/deepagents/memory
  - reviewed 2026-04-21
- `SRC-DA-3`
  - LangChain deepagents backends
  - https://docs.langchain.com/oss/python/deepagents/backends
  - reviewed 2026-04-21
- `SRC-DA-4`
  - LangChain deepagents skills
  - https://docs.langchain.com/oss/python/deepagents/skills
  - reviewed 2026-04-21
- `SRC-DA-5`
  - LangChain deepagents GitHub repo
  - https://github.com/langchain-ai/deepagents
  - reviewed 2026-04-21
