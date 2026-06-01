# Gajae-Code Benchmarking

This note records what Forager should learn from
[Gajae-Code](https://github.com/Yeachan-Heo/gajae-code) without changing
Forager into a single agent harness. It is a translation document, not an
implementation import plan.

Reviewed reference snapshot:

- repository: `Yeachan-Heo/gajae-code`
- snapshot: `60bab38357de737d86881f5835abd00dd03b8e7c`
- date reviewed: 2026-06-01 KST

## Verdict

Gajae-Code does not require a change to Forager's north star or product
identity. It is valuable because it sharpens patterns Forager already values:
small public workflow surfaces, explicit planning and execution boundaries,
tmux-backed runtime evidence, and completion receipts that separate "finished"
from "accepted".

Forager should benchmark Gajae-Code as an operating-pattern source. It should
not copy Gajae-Code's agent loop, provider runtime, skill system, or product
boundary.

## Product Boundary Difference

| Project | Primary identity | Owns | Should not own |
| --- | --- | --- | --- |
| Gajae-Code | Agent harness | Agent loop, workflow skills, role agents, model routing, TUI, tools | Other harnesses' local execution truth |
| Forager | Local meta-harness | Approval, evidence, runtime supervision, recovery, closeout, reviewed knowledge promotion | Hosted harness prompts, internal reasoning, tool policy, model behavior |

The benchmark rule is:

```text
Gajae feature
  -> Forager invariant fit
  -> Forager translation
  -> module contract
```

Anything that bypasses the invariant fit step is out of scope.

## Forager Invariants

The following Forager invariants govern every benchmark decision:

1. Forager supervises harness-backed agents; it does not become the primary
   agent loop.
2. Local canonical state lives in Forager-owned artifacts, not Telegram, chat
   history, or terminal scrollback.
3. Meaningful runtime mutation, provider retargeting, cleanup, and knowledge
   promotion pass through explicit approval or review paths.
4. Completed execution is not accepted truth.
5. Human surfaces stay compact, while full machine evidence remains available.
6. Adaptive wiki knowledge starts as a candidate and becomes durable only after
   reviewed promotion.
7. External orchestration may notify and coordinate, but it must not rewrite
   Forager-owned state directly.

## Principle To Add

Gajae-Code suggests one useful formulation that fits Forager:

> Unclear delegation requires a better decision structure, not stronger
> unchecked autonomy.

In Forager terms, more autonomy should mean better scope, approval, evidence,
and receipt mechanics. It should not mean fewer boundaries.

## Invariant Fit Matrix

| Gajae pattern | Forager translation | Supports invariant | What not to copy | Immutable rule | Living policy | First implementation candidate |
| --- | --- | --- | --- | --- | --- | --- |
| `deep-interview` | Scope Gate | Bounded work and compact decision surfaces | Socratic skill as a product surface | Ambiguous Offdesk work should resolve top-level components, success criteria, non-goals, and material decisions before launch | Question style, thresholds, when to skip | Pre-Offdesk scope topology artifact |
| `ralplan` | Council Decision Pipeline | Approval before mutation; human choices before execution | Planning mode that writes directly for executors | Council output for material choices is a user decision brief before it becomes execution handoff | Role roster, option format, escalation scoring | `Agent -> Council -> Decision Router -> User/Agent` artifact contract |
| `team` | Hosted Harness Execution Lanes | Durable tmux evidence and recovery | Gajae worker runtime, mailbox schema, CLI surface | A hosted lane needs runtime handle, liveness, progress, result, failure, and shutdown evidence | Worker roles, verification lane shape, polling cadence | Evidence lane requirements for hosted harness profiles |
| `ultragoal` | Closeout Receipt / Quality Gate | Completed execution is not accepted truth | Goal engine or `.gjc` ledger format | Closeout cannot mark work accepted without evidence sufficiency, verification, risk, next action, and review state | Receipt fields, review thresholds, retry policy | Offdesk closeout receipt schema |
| Role agents | Council internal roles | Compact human surface with full machine evidence | Public agent zoo | Raw role output is advisory evidence; the user sees a synthesized brief | Planner/architect/critic names, number of reviewers | Council role output normalization |

## Module Contract Layers

Each Forager module that adopts a benchmarked pattern should declare four
layers.

### 1. Module Premise

The module premise explains why the module exists and which Forager invariant it
serves. It should change rarely.

Example:

```text
The Decision Pipeline exists to keep Offdesk autonomy moving while escalating
only material choices to the operator.
```

### 2. Module Invariants

Module invariants are hard boundaries. They should be difficult to change.

Examples:

- Council may advise before approval, but it is not an executor.
- User-visible decisions are persisted as Forager artifacts before Telegram or
  WebUI display.
- Telegram can carry a decision card, but it is not canonical state.
- Execution handoff is created only from an approved or policy-resolved
  decision.
- Closeout receipt is separate from task completion status.

### 3. Living Policy

Living policy is expected to improve with use.

Examples:

- materiality scoring thresholds;
- option count and copy style;
- default action when the operator does not reply;
- Telegram card templates;
- WebUI detail expansion;
- harness-specific prompt and first-read budgets;
- reviewer role mix.

### 4. Implementation

Implementation details should remain easy to revise.

Examples:

- JSON field names;
- CLI flags;
- file paths;
- test fixtures;
- provider adapters;
- exact card layout;
- generated markdown sections.

## P0: Council Decision Pipeline

The first Forager-native implementation candidate is the Council Decision
Pipeline because it directly supports Offdesk autonomy and the operator
decision surface.

### Flow

```text
Agent raises issue
  -> Council reviews evidence and options
  -> Decision Router classifies materiality
  -> low or policy-resolvable: Council returns guidance to Agent
  -> material: User receives a compact decision brief
  -> user choice becomes approved execution handoff
  -> result closes with decision receipt
```

### Core Artifacts

| Artifact | Audience | Purpose |
| --- | --- | --- |
| Decision Request | Council and router | Explain what blocked progress and what decision is needed |
| Decision Brief | User/operator | Present why the decision matters, options, recommendation, risk, and default |
| Execution Handoff | Agent, team, or hosted harness | Convert approved choice into bounded next action and verification contract |
| Decision Receipt | Operator and audit | Record what was decided, by whom or by policy, and what execution was allowed |

### Materiality Examples

Escalate to the user when the choice affects:

- scope expansion or reduction;
- destructive or hard-to-reverse mutation;
- external data movement;
- provider/model retargeting with cost, privacy, or reliability implications;
- research interpretation or claim strength;
- wiki canon promotion;
- cleanup, deletion, archive, or retention decisions;
- runtime policy changes that alter future autonomy.

Return to the agent without user escalation when:

- the decision is covered by an existing approved policy;
- the choice only affects implementation order;
- the verification command can be selected from the approved task contract;
- the Council recommendation does not widen mutation scope or trust boundary.

## P1: Closeout Receipt

The next benchmarked pattern is the closeout receipt. Forager already separates
runtime completion from accepted truth. The receipt should make that separation
machine-checkable.

Minimum receipt dimensions:

- requested scope;
- executed scope;
- evidence completeness;
- verification performed;
- known risks and unresolved questions;
- next safe action;
- wiki promotion eligibility;
- retention or disposal recommendation;
- reviewer verdict.

## P2: Gajae-Code As A Hosted Harness Candidate

Gajae-Code may later become one hosted harness profile among Codex, Claude Code,
OpenCode, Gemini CLI, local scripts, and deterministic review harnesses.

The correct integration path is a disposable smoke under Forager's hosted
harness contract:

```text
forager owns approval, launch, runtime evidence, closeout, and receipt
gajae-code owns its own agent loop, prompts, tools, and model behavior
```

This is not P0. It should wait until the decision pipeline and closeout receipt
contracts are clearer.

## Explicit Non-Goals

Forager should not:

- copy Gajae-Code's `.gjc` state layout;
- import Gajae-Code's bundled skills as Forager product surfaces;
- replace Forager Offdesk with Gajae team mode;
- adopt Gajae-Code's provider/model runtime as Forager's boundary;
- expose raw planner/architect/critic outputs directly to the operator as the
  primary decision surface;
- treat Gajae-Code completion as accepted truth without Forager-visible
  evidence and receipt.

## Immediate Next Step

Use this benchmark note as the premise for the narrow
[Decision Pipeline](decision-pipeline.md) design:

1. define `DecisionRequest`, `DecisionBrief`, `ExecutionHandoff`, and
   `DecisionReceipt`;
2. wire the artifacts into existing Offdesk/Council/Telegram surfaces without
   making Telegram canonical;
3. test low-materiality auto-resolution and high-materiality user escalation;
4. require the approved handoff before any material runtime mutation.
