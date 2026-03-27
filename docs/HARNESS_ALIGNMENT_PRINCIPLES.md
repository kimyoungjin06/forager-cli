# HARNESS_ALIGNMENT_PRINCIPLES

## 1. Purpose
- This document records the strategic alignment between:
  - the current direction of `aoe_orch_control`
  - the strengths observed in `oh-my-claudecode`
- It exists to keep adoption work disciplined.
- We are not trying to turn `aoe_orch_control` into `oh-my-claudecode`.
- We are deciding what to import, what to reject, and what to preserve.

## 2. Our Direction

### 2.1 Product Identity
- `aoe_orch_control` is an owner-only operations system.
- It is optimized for:
  - preparing work before leaving,
  - running project work overnight,
  - returning to a clear recovery surface.

### 2.2 Canonical Model
- `Control Plane`
- `Project Runtime`
- `Task Team`

### 2.3 Canonical Loops
- `Prep`
- `Run`
- `Recovery`

### 2.4 What This Means
- The system is not primarily a generic coding-agent toolkit.
- It is not primarily a plugin wrapper for a host application.
- It is an operations-oriented orchestration system with explicit runtime and recovery contracts.

## 3. OMC's Direction

### 3.1 Product Identity
- `oh-my-claudecode` is a productized orchestration harness for Claude Code.

### 3.2 Core Strength
- OMC's strongest layer is not its orchestration algorithm.
- Its strongest layer is the product shell around orchestration:
  - onboarding
  - hooks
  - session tooling
  - replay/search
  - migration/compatibility
  - observability

### 3.3 What This Means
- OMC is best understood as a high-quality harness and product shell.
- It compresses internal complexity into a small number of user-facing surfaces.

## 4. What We Should Preserve
1. `Control Plane -> Project Runtime -> Task Team`
2. `Prep / Run / Recovery` as the main operator loops
3. offdesk/nightly/morning recovery as the center of the product
4. preset/phase/critic/rerun/followup contract depth
5. parity across Telegram, dashboard, nightly summary, and action audit

## 5. What We Should Learn From OMC

### 5.1 Product Discipline
- canonical surfaces
- explicit deprecation
- migration guidance
- compatibility contracts

### 5.2 Harness Quality
- lifecycle interception
- centralized policy enforcement
- better recovery hooks
- stronger operator visibility

### 5.3 Session Tooling
- session replay
- session search
- session-end summaries
- intervention hints

### 5.4 State Portability
- stable project identity
- persistent state that survives worktree churn

### 5.5 Observability
- per-agent or per-lane observability
- stale detection
- bottleneck detection
- file conflict visibility

### 5.6 Knowledge Capture
- repeated blockers and remediations should become durable runbooks

## 6. What We Should Not Learn From OMC

### 6.1 Mode Proliferation
- We should not add many new named modes just because OMC has them.
- More surfaces would blur the operating model we have already clarified.

### 6.2 Keyword-Heavy UX
- We should not rely on more keyword-driven routing.
- Plain-text routing has only recently stabilized and should be protected.

### 6.3 Plugin-Clone Architecture
- We should not rebuild this package as a Claude Code plugin clone.
- Hook thinking is useful.
- Claude-plugin architecture is not the target.

### 6.4 Generic Toolkit Drift
- We should not dilute the overnight operations focus in order to become a general multi-agent toolkit.

### 6.5 Surface Sprawl
- We should not add user-facing commands/pages faster than we improve observability and recovery quality.

## 7. Adoption Filters
- Every candidate harness feature must pass these checks:

1. Does it strengthen `Prep`, `Run`, or `Recovery`?
2. Does it preserve the `Control Plane -> Project Runtime -> Task Team` model?
3. Does it reuse runtime truth instead of creating a new business-logic stack?
4. Does it improve operator visibility, recovery, or continuity?
5. Does it avoid introducing new ambiguity into plain-text routing?
6. Can it be expressed as a contract rather than a pile of heuristics?

If the answer is "no" to most of these, it should not be imported.

## 8. Immediate Strategic Direction
- Immediate focus should be:
  1. `Session Search`
  2. `Task Team Observatory`
  3. runbook-ready recovery schema
- Then:
  4. centralized state root
  5. doctor/migration discipline
  6. selective delegation contract enforcement

## 9. Checklist For Future Adoption Work
- Before adopting a harness feature:
  - identify the operator question it answers
  - identify the existing runtime truth it reads
  - define the artifact contract
  - define the fallback behavior
  - define what surface exposes it first
  - define what legacy behavior must remain stable

- Before adding a new surface:
  - explain why an existing surface is insufficient
  - define the canonical entrypoint
  - define the deprecation story for any overlapping surface

- Before adding new routing behavior:
  - define how ambiguity is reduced, not increased
  - define safe fallback behavior
  - add recovery-visible evidence for why routing chose that path

## 10. Bottom Line
- `aoe_orch_control` should keep its operating-system character.
- `oh-my-claudecode` should be treated as a harness benchmark, not a product template.
- The correct move is:
  - preserve our operating model,
  - import OMC's harness maturity,
  - reject OMC's tendency toward mode and surface expansion where it conflicts with operator clarity.
