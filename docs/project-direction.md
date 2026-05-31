# Project Direction

Forager is a local autonomy harness for AI agent work.

Its purpose is to turn fragile live chat and terminal sessions into governed
operations: explicit intent, bounded approvals, durable runtime evidence,
reviewable handoffs, and promoted project knowledge.

## Direction Statement

Forager should become the durable local substrate for long-running agent work.
It should let an operator send work away from the desk, inspect what happened,
and return to a fresh harness without relying on memory, chat scrollback, or
implicit trust in a completed process.

The product direction is:

1. Keep local execution state durable and inspectable.
2. Make every autonomy boundary approval-gated.
3. Convert long-running work into small handoff artifacts.
4. Preserve raw evidence while keeping human decision surfaces compact.
5. Promote agent-created knowledge only through reviewable paths.
6. Host different agent harnesses and model backends without making any one of
   them the product boundary.
7. Integrate with higher-level orchestration without surrendering local truth.

## What Forager Is

Forager is:

- a tmux-backed manager for agent and terminal sessions;
- an approval-gated Offdesk runtime for queued or long-running work;
- a harness substrate that can run Claude Code, Codex, OpenCode, local LLM
  scripts, or future agent loops as supervised workloads;
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

## Harness-Agnostic Runtime

Forager should be able to supervise other harnesses as workloads. A hosted
workload may be a commercial CLI agent, a local LLM script, a deterministic
review harness, or a future custom agent loop. The operator's token budget,
latency needs, data boundary, and review standard should determine which harness
is launched.

This means local LLM use is an important operating mode, not the product's
identity. When tokens and budget are abundant, Forager can run richer external
harnesses. When tokens are constrained, it can run smaller local-model episodes,
deterministic scripts, or staged review loops. In both cases, Forager should
preserve the same approvals, runtime evidence, recovery state, closeout, and
handoff contract.

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

## Success Criteria

Forager is moving in the right direction when:

- an operator can leave a long-running task and later understand exactly what
  happened;
- a fresh harness can resume from artifacts rather than a long conversation;
- status surfaces distinguish running, failed, stale, pending-review, and
  accepted states;
- generated documents and run artifacts are findable without making humans open
  every file;
- external orchestration can notify and coordinate without bypassing Forager's
  approval and evidence model;
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
