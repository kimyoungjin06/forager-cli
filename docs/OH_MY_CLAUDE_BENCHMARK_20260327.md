# OH_MY_CLAUDE_BENCHMARK_20260327

## 1. Snapshot
- Date: `2026-03-27`
- Target project: `oh-my-claudecode`
- Repository: `https://github.com/Yeachan-Heo/oh-my-claudecode`
- Scope of review:
  - product surface
  - orchestration harness
  - observability and recovery
  - compatibility/migration discipline
  - recent harness patterns worth importing into `aoe_orch_control`

## 2. Executive Summary
- `oh-my-claudecode` is strong not because its orchestration core is categorically better than `aoe_orch_control`, but because it packages orchestration as a polished product.
- Its biggest advantages are:
  - ruthless canonical-surface discipline,
  - strong onboarding and migration ergonomics,
  - hook-based lifecycle interception,
  - session-level observability and replay/search,
  - state portability across worktrees and sessions,
  - explicit compatibility and deprecation contracts.
- In contrast, `aoe_orch_control` is stronger in:
  - explicit operating model (`Control Plane -> Project Runtime -> Task Team`),
  - offdesk/nightly/morning recovery framing,
  - runtime/preset/critic/rerun contract depth,
  - operator parity across Telegram, dashboard, nightly summary, and action audit.

## 3. What OMC Is Actually Better At

### 3.1 Productization Discipline
- OMC has a much stronger sense of canonical entrypoints and user-facing deprecation.
- It does not just add new modes; it actively removes or deprecates old ones and publishes migration paths.
- Examples:
  - `Team` becomes canonical while legacy surfaces are deprecated or compatibility-wrapped.
  - legacy MCP team runtime returns a deterministic deprecation envelope instead of silently drifting.
  - migration guidance is explicit and versioned.
- Why this matters:
  - users learn fewer surfaces,
  - compatibility debt is managed intentionally,
  - upgrade friction is lower.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/README.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/MIGRATION.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/COMPATIBILITY.md`

### 3.2 Low-Friction Onboarding
- OMC’s onboarding path is short and productized:
  - install plugin,
  - run setup,
  - start with natural language.
- It also provides repair/doctor surfaces and HUD setup as part of the product, not as an afterthought.
- Why this matters:
  - the system feels operable immediately,
  - setup becomes part of the product contract,
  - support burden is lower.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/README.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`

### 3.3 Better User-Facing Packaging Of Complexity
- OMC compresses a lot of internal complexity into a small number of recognizable surfaces:
  - `team`
  - `autopilot`
  - `deep-interview`
  - `ccg`
- This is not just “more modes”.
- It is controlled packaging:
  - a user sees a few strong entrypoints,
  - internal routing and composition stay hidden.
- Why this matters:
  - lower cognitive load,
  - easier memorability,
  - better first-run experience.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/README.md`

### 3.4 Hook-Based Harness Architecture
- OMC treats lifecycle hooks as a first-class harness layer.
- The docs explicitly present hooks for:
  - prompt interception,
  - stop handling,
  - permission handling,
  - recovery,
  - compaction prevention,
  - mode enforcement,
  - learner behavior,
  - delegation enforcement.
- This is a major strength because orchestration becomes ambient system behavior rather than isolated commands.
- Why this matters:
  - better interception points,
  - less manual ceremony,
  - better recovery and continuity.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/ARCHITECTURE.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`
  - Claude Code official docs surfaces:
    - `https://code.claude.com/docs/en/hooks`
    - `https://code.claude.com/docs/en/sub-agents`
    - `https://code.claude.com/docs/en/settings`

### 3.5 Session-Level Observability
- OMC is significantly stronger at session telemetry:
  - Agent Observatory
  - session-end summaries
  - session replay
  - session search
  - intervention system
  - HUD statusline
- This is more than logging.
- It is an operator loop around the orchestration runtime.
- Why this matters:
  - easier post-mortem,
  - easier bottleneck diagnosis,
  - better visibility into agent behavior and cost.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/PERFORMANCE-MONITORING.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`

### 3.6 State Portability Across Worktrees
- OMC’s `OMC_STATE_DIR` is a strong idea.
- It explicitly addresses the problem that worktree-local state disappears when the worktree is deleted.
- The docs tie state identity to project identity instead of ephemeral working directories.
- Why this matters:
  - persistent recovery history,
  - better multi-worktree continuity,
  - more robust session tooling.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`

### 3.7 Delegation Policy Enforcement
- OMC has a documented `Delegation Enforcer` layer that injects missing model parameters into agent/tool delegation calls.
- The important point is not the exact feature itself.
- The strength is the pattern:
  - delegation policy is enforced centrally,
  - not left to every caller.
- Why this matters:
  - lower drift,
  - fewer silent defaults,
  - more predictable orchestration quality.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/DELEGATION-ENFORCER.md`

### 3.8 Compatibility And External Tool Bridge
- OMC documents a compatibility layer that covers:
  - plugin discovery,
  - MCP discovery,
  - tool registry,
  - permission adapter,
  - MCP bridge.
- This is a real product advantage because it makes external capability growth systematic rather than ad hoc.
- Why this matters:
  - external tool growth becomes manageable,
  - conflicts are mediated,
  - permissions are part of the design.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/COMPATIBILITY.md`

### 3.9 Session/Workspace Management
- OMC exposes a project session manager and tmux-based team topology as first-class surfaces.
- This means isolation, topology, and worker lifecycle are explicit product concepts.
- Why this matters:
  - parallel work becomes repeatable,
  - resource waste is lower,
  - session cleanup has a contract.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/README.md`
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`

### 3.10 Documentation And Metadata Hygiene
- OMC even documents documentation sync itself.
- The metadata sync system keeps badges, counts, and references aligned.
- This sounds minor, but it is a sign of project maturity.
- Why this matters:
  - public surface stays trustworthy,
  - drift is caught structurally,
  - the project feels maintained rather than accumulated.
- Sources:
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/SYNC-SYSTEM.md`

## 4. The Real OMC Strength: A Better Harness
- The biggest advantage is not “more agents”.
- It is the harness around the agents.
- That harness has these qualities:
  - orchestration is ambient,
  - session state is durable,
  - lifecycle events are intercepted,
  - replay/search are available,
  - migration and compatibility are deliberate,
  - operator visibility is continuous.

This is the right way to interpret OMC:
- less as an orchestration algorithm,
- more as an orchestration product shell.

## 5. Hot Harness Features Right Now
- Based on OMC docs, Claude Code’s official docs surfaces, and current coding-agent ecosystem direction, these are the high-signal harness patterns:

### 5.1 Lifecycle Hooks
- Prompt/stop/pre-tool/post-tool hooks are now core harness primitives.
- They are being used for:
  - mode activation,
  - permission mediation,
  - recovery,
  - compaction control,
  - delegation enforcement.

### 5.2 Session Replay And Search
- Replay timelines and searchable session artifacts are increasingly table stakes.
- A modern harness is expected to answer:
  - what happened,
  - when it happened,
  - why it stalled,
  - what files/tools were involved.

### 5.3 Centralized Portable State
- Worktree-local state is too fragile for serious long-running orchestration.
- Modern harnesses are moving toward centralized state roots keyed by stable project identity.

### 5.4 Agent Observatory / Intervention
- Real-time visibility into:
  - stale agents,
  - slow tools,
  - excessive cost,
  - file conflicts,
  - intervention recommendations
is becoming a standard high-end feature.

### 5.5 Artifact-First Convergence
- Runtime monitors should trust finalized artifacts and deterministic envelopes over ad hoc live heuristics.
- This is visible in OMC’s team runtime semantics and in its explicit deprecation envelopes.

### 5.6 Compatibility And Migration As First-Class Design
- Mature agent systems now treat deprecation, migration, and compatibility as product surfaces.
- This is especially important as MCP/plugin ecosystems change quickly.

### 5.7 Delegation Contract Enforcement
- Model routing, agent defaults, and permission policy are being centralized.
- The trend is toward policy injection and away from caller-by-caller consistency.

### 5.8 Session/Workspace Topology Management
- Harnesses increasingly expose:
  - session manager,
  - tmux/team topology,
  - shutdown/cleanup contract,
  - isolated worker lifecycles
as user-facing capabilities.

### 5.9 Learned Memory / Skill Extraction
- Reusable skill extraction from prior sessions is turning from a novelty into a serious leverage point.
- The key insight is:
  - repeated reasoning should become durable operator or agent knowledge.

## 6. What `aoe_orch_control` Should Actually Import

### 6.1 Immediate Imports
1. `session search`
- Search over:
  - `.aoe-team/logs/gateway_events.jsonl`
  - nightly summary artifacts
  - dashboard action audit
  - request/task traces

2. `Task Team observatory`
- lane-level:
  - age
  - last event
  - touched files
  - bottleneck phase
  - stale/conflict warning

3. `centralized state root`
- add a stable state-root option above `<project_root>/.aoe-team`
- preserve recovery continuity across clones/worktrees

### 6.2 Medium-Term Imports
1. `doctor/setup/update discipline`
- stronger install/repair/update contract

2. `compatibility/deprecation envelopes`
- if a surface is being retired, return deterministic envelopes rather than silent drift

3. `delegation policy enforcement`
- enforce provider/model/role defaults centrally where possible

4. `session/workspace topology tools`
- make runtime/session lifecycle easier to inspect and recover

### 6.3 Long-Term Imports
1. `learned runbook extraction`
- repeated blockers and recovery decisions should become durable runbooks

2. `artifact-first runtime monitors`
- prefer durable result artifacts over heuristic live summaries where possible

## 7. What Not To Copy
- Do not copy OMC’s mode proliferation directly.
- Do not try to become a Claude Code plugin clone.
- Do not replace the current `Control Plane -> Project Runtime -> Task Team` model with OMC’s surface vocabulary.
- Do not adopt keyword-heavy UX before preserving plain-text routing stability.

## 8. Direct Implication For Current Strategy
- The earlier narrow interpretation was wrong.
- OMC is not just slightly better at observability.
- It is substantially better at:
  - productization,
  - harness design,
  - migration discipline,
  - session tooling,
  - external compatibility framing.

For `aoe_orch_control`, the right benchmark takeaway is:
- keep the current operating model,
- import OMC’s harness quality.

## 9. Source List
- OMC README
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/README.md`
- OMC Architecture
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/ARCHITECTURE.md`
- OMC Reference
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/REFERENCE.md`
- OMC Performance Monitoring
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/PERFORMANCE-MONITORING.md`
- OMC Delegation Enforcer
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/DELEGATION-ENFORCER.md`
- OMC Compatibility
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/COMPATIBILITY.md`
- OMC Migration
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/MIGRATION.md`
- OMC Sync System
  - `https://raw.githubusercontent.com/Yeachan-Heo/oh-my-claudecode/main/docs/SYNC-SYSTEM.md`
- Claude Code hooks
  - `https://code.claude.com/docs/en/hooks`
- Claude Code sub-agents
  - `https://code.claude.com/docs/en/sub-agents`
- Claude Code settings
  - `https://code.claude.com/docs/en/settings`
