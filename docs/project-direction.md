# Project Direction

Forager is a local autonomy meta-harness for harness-backed AI agent work.

Its purpose is to turn fragile live chat, terminal sessions, and external agent
harnesses into governed operations: explicit intent, bounded approvals, durable
runtime evidence, reviewable handoffs, and promoted project knowledge.

## North Star

Forager lets people entrust meaningful work to agents and return to evidence,
choices, and continuity instead of mystery.

This is the human-facing product promise. It should stay short enough to guide
taste, positioning, and product judgment. The internal criteria below define how
the project knows whether that promise is actually being met.

## Direction Statement

Forager should become the durable local substrate for long-running agent work
across many underlying harnesses. It should let an operator choose a
harness-backed agent, send work away from the desk, inspect what happened, and
return to a fresh harness without relying on memory, chat scrollback, or
implicit trust in a completed process.

The product direction is:

1. Keep local execution state durable and inspectable.
2. Make every autonomy boundary approval-gated.
3. Convert long-running work into small handoff artifacts.
4. Preserve raw evidence while keeping human decision surfaces compact.
5. Promote agent-created knowledge only through reviewable paths.
6. Host harness-backed agents and model backends without making any one of them
   the product boundary.
7. Integrate with higher-level orchestration without surrendering local truth.
8. Keep Forager's own boundary at supervision, evidence, recovery, review, and
   knowledge promotion rather than agent reasoning itself.

## What Forager Is

Forager is:

- a tmux-backed manager for agent and terminal sessions;
- an approval-gated Offdesk runtime for queued or long-running work;
- a meta-harness that can supervise harness-backed agents from Claude Code,
  Codex, OpenCode, OpenHands, local LLM scripts, or future agent loops as
  workloads;
- a local source of truth for task queues, approvals, runtime probes, recovery,
  result artifacts, closeout packages, and adaptive wiki state;
- a handoff system that moves work from Ondesk to Offdesk and back through
  bounded artifacts;
- an operator surface for deciding what is safe to run, trust, preserve,
  promote, or discard.

## What Forager Is Not

Forager is not:

- a replacement for Claude Code, Codex, OpenCode, Gemini CLI, or other agents;
- a product tied to one commercial model, local model, provider, or token
  budget assumption;
- a claim that Forager's own local LLM scripts are the primary or best agent
  loop for every workload;
- the owner of a hosted agent's internal planning, memory, tool-use policy, or
  model behavior;
- a cloud orchestrator or cross-organization control plane;
- an autonomous system that treats completed execution as accepted truth;
- a hidden memory layer that mutates project knowledge without review;
- a cleanup tool that moves or deletes files just because an agent finished;
- a product whose canonical state lives in Telegram, chat history, or terminal
  scrollback.

## Core Product Bet

The core bet is that useful agent autonomy depends less on giving agents more
freedom and more on making boundaries explicit.

Agents can work longer and more independently when the harness records:

- what was requested;
- what was approved;
- what actually ran;
- what evidence was produced;
- what needs human or model review;
- what knowledge is safe to reuse later.

Forager should therefore improve autonomy by strengthening state, evidence,
review, and handoff mechanics, not by bypassing them.

## Harness-Backed Agent Runtime

Forager should be able to supervise agents that were built by other harnesses.
A hosted workload may be a commercial CLI agent, an open-source autonomous
coding harness, a local LLM script, a deterministic review harness, or a future
custom agent loop. The operator's token budget, latency needs, data boundary,
tooling preference, and review standard should determine which harness-backed
agent is launched.

This means local LLM use is an important operating mode, not the product's
identity. When tokens and budget are abundant, Forager can run richer external
harnesses. When tokens are constrained, it can run smaller local-model episodes,
deterministic scripts, or staged review loops. In both cases, Forager should
preserve the same approvals, runtime evidence, recovery state, closeout, and
handoff contract.

The concrete runtime distinction is:

- the hosted harness owns the agent loop, prompts, tools, model calls, and
  interaction style;
- Forager owns the local task state, approval boundary, runtime supervision,
  evidence capture, recovery decision, closeout package, and reviewed knowledge
  promotion;
- a workload is accepted only through Forager-visible artifacts, not because the
  hosted agent reports that it is done.

## Terminology

- **Agent**: the worker behavior that plans, edits, reviews, or answers inside a
  harness.
- **Harness**: the runtime that builds and steers an agent, such as Claude Code,
  Codex, OpenHands, OpenCode, Aider, SWE-agent, or a local script.
- **Harness-backed agent**: an agent together with the harness that supplies its
  prompts, model routing, tool policy, and execution loop.
- **Hosted harness agent**: a harness-backed agent launched under Forager's local
  approval, evidence, recovery, and review contract.
- **Forager meta-harness**: the supervising layer that decides what may run,
  records what did run, preserves evidence, prepares handoffs, and promotes
  knowledge after review.

## Operating Principles

### State Over Scrollback

Raw chat history is not the source of truth. Important transitions should create
small, durable artifacts that another harness can read.

### Approval Before Runtime Mutation

Runtime execution, provider retargeting, cleanup, canonical promotion, and other
meaningful state changes should pass through explicit approval or review
surfaces.

### Evidence Before Acceptance

A completed process proves that something ran. It does not prove that the result
is correct, safe, useful, or ready to promote.

### Compact Human Surfaces, Full Machine Evidence

Humans should start from current state, next actions, decisions, and return
packages. Full inventories, logs, and machine plans should remain available
without becoming the primary reading surface.

### Local Truth, External Coordination

Higher-level tools may coordinate projects, notifications, and schedules. They
should call Forager commands or consume Forager JSON instead of rewriting
Forager-owned state.

### Reviewed Knowledge, Disposable Projections

Adaptive wiki JSON is the canonical profile knowledge store. Markdown and other
views are projections. Candidate observations become durable knowledge through
review, promotion, and provenance.

## Product Shape

Forager's long-term shape is a staged operating loop:

```text
observe current state
  -> prepare bounded work
  -> request approval
  -> run under durable local supervision
  -> collect evidence
  -> close out and review
  -> return to a fresh Ondesk harness
  -> promote durable knowledge deliberately
```

The loop should stay usable from CLI, TUI, and external operator surfaces, but
the same underlying state and safety contract should apply everywhere.

## Practical Completion Criteria

The north star is being met when these internal criteria are true:

- each supported hosted harness agent has a shared local contract: launch
  command, working directory, mutation scope, runtime evidence sources, result
  artifact, failure signal, closeout package, and retention policy;
- every Offdesk workload records intent, approval, command summary, runtime
  handle, heartbeat, progress, logs, result artifacts, and stale/failed/complete
  state without relying on terminal scrollback;
- a fresh Claude Code, Codex, OpenHands, OpenCode, or local-model harness can
  resume from bounded artifacts rather than a long conversation;
- an operator can return to a long-running task and understand the current
  state, evidence, risks, and next decision within a few minutes;
- Telegram, WebUI, TUI, and CLI expose the same decision model while fitting
  their own surface: compact decisions for alerts, richer review in UI, durable
  JSON for automation and audit;
- completed execution is separated from accepted truth: results remain pending
  review until evidence, risks, and next actions are inspectable;
- agent-created lessons become adaptive wiki candidates first and become
  reusable project knowledge only through reviewed promotion;
- generated documents, run artifacts, and deliverables are findable, resumable,
  and eligible for retention or disposal without forcing humans to open every
  file;
- Forager can compare harness-backed agents by task type using quality, cost,
  latency, failure modes, recovery reliability, and evidence completeness;
- external orchestration can notify and coordinate without bypassing Forager's
  approval, evidence, recovery, and knowledge-promotion model;
- old compatibility surfaces fade without breaking existing local state.

## Near-Term Focus

The current implementation should prioritize:

- Offdesk closeout and review closure;
- operator decision surfaces that show enough context to choose safely;
- documentation and artifact governance for long-running projects;
- adaptive wiki review, projection, and promotion;
- clean boundaries between Forager and any higher-level control plane;
- gradual removal of legacy AoE naming from new product surfaces while
  preserving compatibility where needed.
