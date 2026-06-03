# Implementation Packet And Recursive Alignment Review

This document defines the design-first packet that Forager should use before
delegating substantial work to an Offdesk worker, hosted harness agent, local
model episode, Council reviewer, or future automation path.

The goal is not to make planning heavier for its own sake. The goal is to make
delegated work executable without losing the original product intent.

## Premise

Long-running agent work fails in two different ways:

- the implementation is technically weak because the worker received an
  incomplete design;
- the implementation is technically competent but drifts away from Forager's
  north star, product boundary, brand promise, or original user goal.

Forager needs a packet that handles both risks. A good worker packet should be
specific enough for local models or narrow harness agents to execute overnight,
and broad enough to keep the work aligned with the reason Forager exists.

The packet should therefore ask two questions repeatedly:

1. Can this work be executed from bounded instructions and evidence?
2. If every local subtask succeeds, will the original purpose still be served?

## Contract Overview

The implementation packet is a design artifact, not a runtime approval by
itself.

```text
user or operator intent
  -> implementation_packet.v1
  -> recursive_alignment_review.v1
  -> judgment route for design quality
  -> approved or revised packet
  -> bounded worker execution
  -> closeout against packet and alignment review
```

The packet should become the normal bridge between high-level direction and
delegated execution. It can be used by:

- Offdesk queued work;
- hosted harness agents such as Claude Code, Codex, OpenCode, OpenHands, or
  Gemini CLI;
- local LLM implementation episodes;
- Council review;
- morning Ondesk resume packets;
- future WebUI design review surfaces.

## `implementation_packet.v1`

Recommended minimum shape:

```json
{
  "schema": "implementation_packet.v1",
  "packet_id": "packet-id",
  "created_at": "2026-06-03T00:00:00Z",
  "project_key": "forager-cli",
  "source_intent": {
    "user_goal": "What the operator originally wanted.",
    "why_now": "Why this matters now.",
    "success_state": "What must be true when the work is complete."
  },
  "alignment": {
    "north_star_fit": "How this serves evidence, choices, and continuity.",
    "brand_fit": "How this preserves Forager's meta-harness identity.",
    "product_boundary": "What Forager owns and what the hosted harness owns.",
    "anti_drift_notes": []
  },
  "scope": {
    "included": [],
    "excluded": [],
    "allowed_files": [],
    "mutation_boundary": "What the worker may change.",
    "non_authorized_actions": []
  },
  "capability_mapping": [
    {
      "capability_id": "FD-004",
      "reason": "Decision routing is affected."
    }
  ],
  "design": {
    "approach": "The intended implementation approach.",
    "work_slices": [],
    "interfaces": [],
    "data_contracts": [],
    "compatibility_notes": []
  },
  "execution": {
    "preferred_worker": "local_model | hosted_harness | deterministic_script",
    "worker_requirements": [],
    "commands": [],
    "stop_conditions": [],
    "rollback_or_recovery": []
  },
  "validation": {
    "tests": [],
    "smoke_checks": [],
    "manual_review": [],
    "evidence_required": []
  },
  "closeout": {
    "expected_artifacts": [],
    "accepted_truth_rule": "Execution completion is not acceptance.",
    "handoff_summary_requirements": []
  },
  "recursive_alignment_review": {}
}
```

The exact field names may evolve, but every implementation packet should
preserve the same responsibilities:

- explain the original intent in human terms;
- map the work back to Forager's product direction and brand boundary;
- state what is in scope and what remains explicitly out of scope;
- tell the worker what to build, where it may operate, and when it must stop;
- define how completion will be tested and reviewed;
- make deferred goals visible instead of silently dropping them.

## `recursive_alignment_review.v1`

The recursive alignment review is the self-check that prevents narrow subtask
success from replacing real project progress.

Recommended minimum shape:

```json
{
  "schema": "recursive_alignment_review.v1",
  "reviewer": "council | single_harness | deterministic_gate | user",
  "outcome": "pass | revise | block",
  "checks": {
    "original_goal_coverage": "complete | partial | missing",
    "north_star_alignment": "strong | acceptable | weak",
    "brand_alignment": "strong | acceptable | weak",
    "scope_balance": "right_sized | too_narrow | too_broad",
    "capability_coverage": "complete | partial | missing",
    "evidence_sufficiency": "sufficient | partial | insufficient",
    "completion_definition": "testable | vague | missing"
  },
  "drift_signals": [],
  "missing_decisions": [],
  "required_revisions": [],
  "safe_to_delegate": false
}
```

The review should be recursive in the practical sense: each proposed work slice
should be checked against the packet, and the packet should be checked against
the original project direction.

## Self-Review Questions

Before delegation, the packet should answer:

- Does this still serve Forager's north star: evidence, choices, and continuity?
- Does this preserve Forager as a local meta-harness rather than turning it into
  one hosted agent, one notification surface, or one memory system?
- Does the worker know the user-visible outcome, not just the implementation
  task?
- Is the scope narrow enough to execute but broad enough to solve the actual
  problem?
- Are important non-goals explicit, including cleanup, wiki promotion, accepted
  truth, provider changes, and external writes?
- Are the required tests, smoke checks, artifacts, or visual reviews named?
- If this succeeds, what previously painful operator workflow becomes better?
- If this fails or stalls, what evidence will let a fresh harness resume?

After execution, closeout should answer:

- Did the implementation complete every intended outcome in the packet?
- Which goals were deferred, and were they explicitly receipted?
- Did the work introduce product drift, brand drift, scope creep, or hidden
  state?
- Do CLI JSON, human summaries, docs, and runtime evidence still agree?
- Is the next safe action clear to a fresh Ondesk harness?

## Judgment Routing

The packet's design quality should be routed through the same evaluator model
as other Forager decisions:

| Route | Use When |
| --- | --- |
| `deterministic_gate` | The packet can be checked by schema, tests, dependency rules, or policy. |
| `single_harness` | One strong reviewer can verify a narrow technical plan. |
| `council` | The work affects product direction, user experience, safety boundaries, or tradeoffs. |
| `user` | The remaining decision is authority, preference, risk tolerance, or brand judgment. |

For long overnight runs, Council should usually review the packet before local
model execution. A local model can then implement from the packet without
having to rediscover the product strategy.

## Surface Rules

Different surfaces can project the packet differently:

- CLI JSON should preserve ids, file paths, schemas, commands, and evidence
  refs.
- CLI text should show the goal, next action, delegation readiness, and failing
  alignment checks.
- Telegram should show only the decision question, recommendation, key
  alignment concern, options, and default if no reply.
- WebUI should show a packet review surface with goal coverage, scope, evidence,
  and drift signals.
- Ondesk prompt packages should carry the accepted packet summary and unresolved
  alignment risks to the next harness.

No surface should treat an implementation packet as accepted truth. It is an
execution design and review aid.

## Non-Goals

This design does not require every small edit to produce a large packet. Trivial
fixes can still use ordinary implementation judgment.

This design does not turn planning into approval. Runtime mutation, cleanup,
retention, wiki promotion, provider changes, and accepted truth still need their
own existing authorization paths.

This design does not make Council mandatory for every task. The point is to
route design judgment to the evaluator that fits the risk and scope.

## Acceptance Criteria

The first operational version is good enough when:

- a substantial Offdesk task can be launched from an implementation packet
  without relying on chat scrollback;
- the packet records original intent, north-star fit, scope, non-goals, work
  slices, stop conditions, and validation commands;
- recursive alignment review can return `pass`, `revise`, or `block` with clear
  missing goals or drift signals;
- closeout compares actual results against the packet rather than only saying
  the command finished;
- morning Ondesk review can tell whether the work solved the original problem
  or only completed a narrow subtask.

## Near-Term Implementation Slices

Implemented first slice:

- Rust state types define `implementation_packet.v1` and
  `recursive_alignment_review.v1`;
- `forager project implementation-packet` drafts read-only packet artifacts
  from operator-supplied goal, scope, validation, stop condition, and closeout
  fields;
- the command writes profile-local or `--out` JSON/Markdown artifacts and does
  not grant runtime authority.
- `review_surface.v1` reads the latest project-matching packet and summarizes
  source intent, delegation readiness, missing decisions, validation shape, and
  artifact refs;
- `forager ondesk prompt-package` renders that summary so a fresh harness sees
  the design context before continuing.
- `forager offdesk enqueue` and `forager offdesk launch` bind the latest
  project-matching packet, or an explicit `--implementation-packet`, into task
  and background launch records as metadata only;
- Telegram/Ondesk detail cards preserve the packet's goal and readiness from
  `review_surface.v1` without exposing raw packet paths as the primary surface.
- `forager offdesk closeout` now emits `implementation_packet_coverage` in
  `closeout_plan.json`, `CLOSEOUT_PLAN.md`, `RETURN_PACKAGE.md`, and the
  commercial review packet. The first coverage pass classifies linked packet
  goals as `completed`, `deferred`, `missing`, or `drifted` while preserving
  the rule that execution evidence is not accepted truth. When the packet JSON
  is still available, closeout also itemizes work slices, validation items, and
  expected artifacts; validation and artifact items are matched against
  task/background evidence refs before they are marked completed.

Remaining slices:

1. Define a JSON schema and Rust data type for `implementation_packet.v1` and
   `recursive_alignment_review.v1`. Done for the first Rust contract.
2. Add a CLI command that drafts a packet from current task, decision, closeout,
   and project-direction context. Initial command exists; richer state
   collectors still need to be connected.
3. Add Council or single-harness review prompts that critique goal coverage,
   scope balance, evidence sufficiency, and brand fit.
4. Add explicit per-work-slice execution receipts and richer drift explanations
   so work slices do not have to inherit packet-level status.
