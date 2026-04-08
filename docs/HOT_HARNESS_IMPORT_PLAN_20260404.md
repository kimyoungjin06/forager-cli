# Hot Harness Import Plan (2026-04-04)

## 1. Goal
- Re-rank `aoe_orch_control` around the strongest public coding-agent harness patterns instead of continuing to spend the main effort on scenario-level planner seams.
- Keep our existing `Control Plane -> Project Runtime -> Task Team` model.
- Import product and operating-model strengths, not prompt wording or provider-specific quirks.

## 2. Scope
- This document focuses on:
  - coding-agent shells
  - operator dashboards
  - background / remote execution
  - governance / permissions / audit
  - plan-first and handoff workflows
- It does not attempt to compare base model quality.

## 3. Current Strategic Reading
- Public market direction is converging on:
  - plan-first interaction
  - background execution
  - remote or cloud execution
  - operator-facing governance
  - durable logs, reports, and review artifacts
- Our current over-investment is:
  - scenario-level acceptance seam chasing
- Our current under-investment is:
  - `Execution Brief`
  - on-desk -> off-desk handoff
  - background execution
  - project progress surfaces
  - governance / usage / permissions

## 4. Packages To Watch

### 4.1 OpenHands
- Strengths:
  - local + cloud + enterprise surface
  - background agent fleet
  - integrations (`Slack`, `Jira`, `Linear`, SCM)
  - RBAC / multi-user / reporting
- Weaknesses:
  - heavy platform footprint
  - more infrastructure-oriented than single-operator shell
- Import:
  1. background / remote execution model
  2. governance and reporting surface
  3. operator console thinking

### 4.2 Claude Code + Claude Code Action
- Strengths:
  - strong terminal UX
  - hooks / plugins / SDK direction
  - GitHub issue/PR automation
- Weaknesses:
  - public surface is stronger on developer workflow than runtime observability
- Import:
  1. hook / plugin architecture
  2. GitHub-triggered off-desk execution
  3. PR/issue-based activation model

### 4.3 GitHub Copilot Coding Agent
- Strengths:
  - issue -> background work -> PR loop
  - durable review and action history inside the SCM workflow
  - very clear asynchronous operator handoff
- Weaknesses:
  - GitHub-centric
  - less suitable as a local runtime truth layer
- Import:
  1. issue-to-agent handoff
  2. background PR execution
  3. review artifact as operator-facing evidence

### 4.4 OpenCode
- Strengths:
  - explicit `Plan mode`
  - strong permission model
  - project-local `AGENTS.md`
- Weaknesses:
  - archived public repo is continuity risk
- Import:
  1. plan/build split
  2. permission policy UX
  3. project-local operator instructions

### 4.5 Aider
- Strengths:
  - repo map
  - git-native editing discipline
  - built-in lint/test loop
- Weaknesses:
  - not an operator dashboard / orchestration product
- Import:
  1. repo-map style context summary
  2. auto test/lint discipline
  3. git-native evidence expectations

### 4.6 Goose
- Strengths:
  - desktop + CLI dual surface
  - MCP and multi-provider breadth
  - diagnostics / reporting
- Weaknesses:
  - broad extension surface increases complexity
- Import:
  1. diagnostics and health reporting
  2. desktop + CLI parity thinking
  3. extensibility model

### 4.7 Roo Code / Cline
- Strengths:
  - editor ergonomics
  - mode-based autonomy
  - checkpointing / session flow
- Weaknesses:
  - editor-centered, weaker as an operator console
- Import:
  1. mode separation
  2. checkpoint and recovery affordances
  3. editor-side ergonomics only where they help local operator work

### 4.8 Oh My OpenCode / OMC
- Strengths:
  - batteries-included harness
  - async specialist agents
  - aggressive context hygiene and verification defaults
- Weaknesses:
  - opinionated substrate
  - easy to over-copy at the wrong layer
- Import:
  1. context hygiene hooks
  2. async specialist delegation
  3. strong default verification discipline

### 4.9 Amp
- Strengths:
  - policy-centric operator controls
  - secret handling and redaction posture
  - enterprise security framing
- Weaknesses:
  - less open at the runtime internals layer
- Import:
  1. policy engine mindset
  2. secret redaction
  3. explicit allow/reject/ask/delegate boundaries

### 4.10 AgentAPI
- Strengths:
  - vendor-neutral adapter layer
  - unified external control plane
- Weaknesses:
  - adapter value depends on upstream native APIs staying fragmented
- Import:
  1. agent adapter seam
  2. vendor-neutral off-desk execution bridge

## 5. What To Copy Directly
1. `Execution Brief` as the last on-desk artifact and first off-desk artifact
2. plan-first / build-later interaction
3. background and remote execution rails
4. governance:
   - permissions
   - budget / usage
   - audit
   - recovery
5. operator dashboard as the primary runtime shell
6. git-native evidence and review artifacts

## 5A. What We Keep Vs What We Rent
- Keep native:
  - operator control plane
  - request/brief/ticket schemas
  - run lock / slot / scheduler policy
  - audit / recovery / dashboard truth
- Rent or reuse:
  - local process/session executors
  - tmux-backed launchers
  - GitHub runner pickup
  - remote worker pickup
  - third-party coding shells
- Required seam:
  - an `Executor Adapter` layer that translates control-plane truth into runner-specific execution and back
- reference:
  - `REF-API-1`

## 6. What Not To Copy
1. prompt wording
2. provider-specific role names
3. scenario-specific acceptance strings
4. UX aliases that add mode count without improving operator clarity
5. benchmark-specific marketing surfaces that do not strengthen runtime truth

## 7. Direct Import Lanes

### 7.1 Lane A: On-Desk -> Off-Desk Contract
- Primary references:
  - OpenCode
  - GitHub Copilot coding agent
  - Claude Code
- Deliverables:
  - `Execution Brief`
  - executable-state model
  - operator decision boundary
  - explicit non-goals and blocked slices

### 7.2 Lane B: Background / Remote Execution
- Primary references:
  - OpenHands
  - Claude Code Action
  - GitHub Copilot coding agent
- Deliverables:
  - background queue
  - remote worker / runner support
  - request-to-run audit trail
  - durable off-desk evidence bundle
  - executor adapter seam

### 7.3 Lane C: Operator Dashboard
- Primary references:
  - OpenHands
  - Goose
  - our own existing dashboard lead
- Deliverables:
  - project progress board
  - brief status board
  - recovery / history / runtime detail convergence

### 7.4 Lane D: Governance
- Primary references:
  - Amp
  - Goose
  - Claude Code permissions / action surfaces
- Deliverables:
  - permissions policy
  - budget and usage reporting
  - audit and redaction
  - operator escalation boundaries

### 7.5 Lane E: Repo-Native Correctness
- Primary references:
  - Aider
  - Cline / Roo Code
  - OMC
- Deliverables:
  - repo map
  - auto lint/test gates
  - checkpoint and recovery hints
  - async specialist verification defaults

## 8. Recommended Re-Prioritization
1. `Execution Brief`
2. `Ondesk / Offdesk state model`
3. `Background / Remote Execution`
4. `Dashboard progress board`
5. `Governance / permissions / usage / audit`
6. `Project Flow Compiler`
7. only then continue deep rerun/manual-followup scenario proof

## 9. Our Real Comparative Strength
- Our strongest differentiated asset is not raw planner cleverness.
- It is:
  1. operator-facing runtime truth
  2. `Task Team Observatory`
  3. `Session Search`
  4. planning convergence metadata
  5. the emerging on-desk / off-desk split
- The correct strategy is to lean harder into that operating shell, not to out-compete upstream projects on prompt-level planner refinement.
- That implies a product boundary:
  - `aoe_orch_control` should become the control plane plus adapter layer
  - not a monolithic native harness that re-implements every executor surface

## 10. Sources
### 10.1 Reference Discipline
- Every benchmark-driven import item in:
  - `docs/HARNESS_ADOPTION_PLAN.md`
  - `docs/ROADMAP.md`
  - `docs/REQUEST_CONTRACT_SPEC.md`
  should cite at least one reference ID from this section.
- The reference IDs below are intended to be stable shorthands for roadmap and spec discussions.

### 10.2 Reference IDs
- `REF-OH-1`
  - OpenHands repo
  - `https://github.com/OpenHands/OpenHands`
- `REF-CC-1`
  - Claude Code repo
  - `https://github.com/anthropics/claude-code`
- `REF-CC-2`
  - Claude Code Action repo
  - `https://github.com/anthropics/claude-code-action`
- `REF-GHCA-1`
  - GitHub Copilot coding agent overview
  - `https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent`
- `REF-GHCA-2`
  - GitHub Copilot coding agent GA changelog
  - `https://github.blog/changelog/2025-09-25-copilot-coding-agent-is-now-generally-available/`
- `REF-GHCA-3`
  - GitHub Copilot coding agent browser changelog
  - `https://github.blog/changelog/2025-07-02-copilot-coding-agent-now-has-its-own-web-browser/`
- `REF-OC-1`
  - OpenCode docs root
  - `https://open-code.ai/docs/en`
- `REF-OC-2`
  - OpenCode modes
  - `https://open-code.ai/docs/en/modes`
- `REF-OC-3`
  - OpenCode permissions
  - `https://open-code.ai/docs/en/permissions`
- `REF-OC-4`
  - OpenCode archived repo
  - `https://github.com/opencode-ai/opencode`
- `REF-AI-1`
  - Aider site
  - `https://aider.chat/`
- `REF-AI-2`
  - Aider repo
  - `https://github.com/Aider-AI/aider`
- `REF-GS-1`
  - Goose repo
  - `https://github.com/block/goose`
- `REF-GS-2`
  - Goose quickstart/docs
  - `https://block.github.io/goose/docs/quickstart/`
- `REF-RC-1`
  - Roo Code repo
  - `https://github.com/RooCodeInc/Roo-Code`
- `REF-CL-1`
  - Cline repo
  - `https://github.com/cline/cline`
- `REF-OMC-1`
  - Oh My OpenCode repo
  - `https://github.com/code-yeongyu/oh-my-opencode`
- `REF-AMP-1`
  - Amp manual
  - `https://ampcode.com/manual`
- `REF-AMP-2`
  - Amp security
  - `https://ampcode.com/security`
- `REF-API-1`
  - AgentAPI repo
  - `https://github.com/coder/agentapi`
