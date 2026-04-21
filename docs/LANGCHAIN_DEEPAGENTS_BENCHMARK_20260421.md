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

### 3.5 Synchronous + Async Subagents
- `deepagents` clearly separates:
  - synchronous subagents for context quarantine
  - async subagents for long-running, non-blocking parallel work
- The synchronous model is close to what we want for bounded support lanes:
  - the supervisor delegates
  - the supervisor gets back a reduced result
  - the supervisor stays canonical
- The async model adds a stronger control pattern:
  - launch
  - check
  - update
  - cancel
  - list tracked tasks
- The notable implementation detail is correct:
  - task metadata is kept in a dedicated state channel rather than only in message history, so IDs survive compaction.
- Benchmark conclusion:
  - this is a real lesson for us.
  - if we add async support lanes later, task IDs and state must live in explicit structured state, not chat-only history.

### 3.6 Sandboxes As Tool Pattern
- `deepagents` documents the sandbox boundary more clearly than most agent stacks.
- The most useful pattern is not “run the whole agent in the sandbox”.
- The useful pattern is:
  - keep the agent loop, memory, and control logic outside
  - expose sandboxed file/shell tools into the agent
  - let execution happen remotely or inside an isolated environment
- This maps well to our existing boundary discipline.
- Benchmark conclusion:
  - copy this pattern into executor policy.
  - do not move control-plane truth into the sandbox.

### 3.7 Production Coupling To LangSmith / LangGraph
- `deepagents` production guidance is tightly coupled to:
  - LangSmith Deployments
  - assistant/thread/run/store/checkpointer primitives
  - tracing and observability
- This is operationally strong.
- Important nuance:
  - `LangSmith Deployment` itself is documented as framework-agnostic.
  - but the default `deepagents deploy` path still assumes their deployment/runtime substrate and primitives.
- Benchmark conclusion:
  - good source of operational ideas
  - not a drop-in ownership model for our runtime truth

### 3.8 CLI Operator Affordances
- The Deep Agents CLI surfaces several concrete affordances worth copying:
  - approval controls for destructive operations
  - shell allow-list in non-interactive mode
  - explicit todo writing
  - explicit conversation compaction
  - skill discovery from local/project directories
- Benchmark conclusion:
  - these are directly useful for `on-desk` tooling and bounded worker rails.
  - they are not enough to replace our dashboard/recovery/audit surfaces.

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

### 4.4 Explicit Recovery Contract
- `LangChain` documents durable execution, persistence, tracing, and deployment well.
- But it does not give us our actual operator contract:
  - nightly recovery artifact
  - replayable action audit
  - runtime-safe mutation rails
  - queue / repeat-memory / remediation surfaces
- This distinction matters.
- Durable execution is not the same thing as an operator recovery product.

## 5. Recommended Adoption Boundary

### 5.1 Good Candidates To Import
1. A stronger typed planning-tool contract for coding workers.
2. More formal subagent profiles beyond `general_research`.
3. Backend policy seams for local vs sandboxed execution.
4. Reusable skill bundles for bounded authoring/research tasks.
5. Explicit async support-lane lifecycle design:
   - launch
   - status
   - cancel
   - update
   - persisted task IDs
6. Approval controls and shell allow-lists for higher-risk execution rails.

### 5.2 Things We Should Not Import As-Is
1. Hidden long-lived agent memory as canonical truth.
2. Agent-owned routing or recovery policy.
3. Framework-owned task lifecycle state.
4. File backend state that is not mirrored into our audit/runtime contracts.
5. Production coupling that assumes LangSmith Deployments become the primary runtime owner.

## 6. Product Stack Reading

### 6.1 Official Layering
- Current official layering is roughly:
  - `LangChain`: higher-level agent abstraction
  - `Deep Agents`: batteries-included agent harness
  - `LangGraph`: low-level orchestration runtime
  - `LangSmith`: tracing / deployment / evaluation / observability product surface
- Their own docs recommend:
  - start with Deep Agents for batteries-included agent work
  - use LangChain for more custom agent construction
  - drop to LangGraph for low-level orchestration needs

### 6.2 What That Means For Us
- We should read this stack as:
  - useful execution/runtime patterns
  - useful deployment/observability ideas
  - not a reason to replace `aoe_orch_control` as canonical truth
- The clean mapping for our system is:
  - `on-desk agent harness` may borrow from Deep Agents / CLI ideas
  - `off-desk execution adapters` may borrow sandbox + backend patterns
  - `control plane` remains ours
  - `audit/recovery truth` remains ours
  - `LangSmith-style tracing/deployment` may inform observability and hosting, but must remain adapter-owned rather than truth-owning

## 7. Adopt / Defer / Reject Matrix

| Topic | Decision | Why |
|---|---|---|
| Typed planning tool | `adopt` | already aligned with `planning_compact` and plan-gate surfacing |
| Bounded sync subagents | `adopt` | maps directly to support artifacts and context quarantine |
| Async subagent lifecycle model | `adopt later` | valuable, but needs explicit state/audit/recovery integration first |
| Sandbox-as-tool pattern | `adopt` | cleanly fits our executor boundary discipline |
| Shell approval + allow-list controls | `adopt` | directly improves on-desk and non-interactive safety |
| Skill discovery model | `adopt later` | useful, but needs our own skill registry boundary |
| Memory as hidden long-term truth | `reject` | conflicts with inspectable runtime/audit state |
| LangSmith Deployments as runtime owner | `reject` | would move canonical ownership outside our control plane |
| LangGraph as full orchestration replacement now | `defer` | interesting for internals, not justified as a current re-platform |
| LangSmith tracing ideas | `adopt selectively` | tracing model is useful, but must not replace our action audit |

## 8. Concrete Implications For `aoe_orch_control`
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
6. If we add async support lanes, require:
   - stable task IDs
   - explicit state channel storage
   - cancellation/update hooks
   - audit rows for every lifecycle transition
   - recovery visibility in dashboard and nightly artifacts

## 9. Decision
- `deepagents` is worth benchmarking as an execution-pattern library.
- It is not the right replacement for our control plane.
- The correct move is:
  - import the good patterns
  - keep runtime/recovery/audit truth local
  - expose every imported behavior through our existing operator surfaces
- `LangGraph` and `LangSmith` are also worth studying, but as lower-level runtime and observability references, not as automatic product-boundary choices.

## 10. Near-Term Follow-up
1. Define a second bounded subagent contract beside `general_research`.
2. Add backend-policy visibility to runtime and recovery surfaces.
3. Prototype a small skill registry that maps cleanly onto context packs and bounded support artifacts.
4. Design an `async support lane` contract only after:
   - task ID persistence
   - cancel/update semantics
   - action-audit hooks
   - recovery rendering
5. Evaluate whether selective LangSmith-style trace metadata should be mirrored into our own action audit rows.

## 11. References
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
- `SRC-DA-6`
  - LangChain deepagents subagents
  - https://docs.langchain.com/oss/python/deepagents/subagents
  - reviewed 2026-04-21
- `SRC-DA-7`
  - LangChain deepagents async subagents
  - https://docs.langchain.com/oss/python/deepagents/async-subagents
  - reviewed 2026-04-21
- `SRC-DA-8`
  - LangChain deepagents sandboxes
  - https://docs.langchain.com/oss/python/deepagents/sandboxes
  - reviewed 2026-04-21
- `SRC-DA-9`
  - LangChain deepagents going to production
  - https://docs.langchain.com/oss/python/deepagents/going-to-production
  - reviewed 2026-04-21
- `SRC-DA-10`
  - LangChain deepagents CLI overview
  - https://docs.langchain.com/oss/python/deepagents/cli/overview
  - reviewed 2026-04-21
- `SRC-LC-1`
  - LangChain overview
  - https://docs.langchain.com/oss/python/langchain/overview
  - reviewed 2026-04-21
- `SRC-LG-2`
  - LangGraph overview
  - https://docs.langchain.com/oss/python/langgraph
  - reviewed 2026-04-21
- `SRC-LS-1`
  - LangSmith trace deep agents
  - https://docs.langchain.com/langsmith/trace-deep-agents
  - reviewed 2026-04-21
- `SRC-LS-2`
  - LangSmith Deployment quickstart
  - https://docs.langchain.com/oss/python/langchain/deploy
  - reviewed 2026-04-21
