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

## Review-Level Model

Different phases need different levels of review. A plan can be reviewed from
text because it is not yet an execution claim. A result cannot be reviewed from
worker prose alone because the worker may be wrong, incomplete, or optimistic.

Forager should therefore separate these levels:

| Level | Primary Question | Minimum Evidence |
| --- | --- | --- |
| `plan_review` | Is the packet aligned, bounded, and executable? | Packet text, scope, non-goals, validation plan, and routed judgment. |
| `launch_readiness` | Is it safe to start this worker now? | Workdir, command, approval envelope, stop conditions, and required evidence refs. |
| `worker_claim_review` | What does the worker claim happened? | Worker-authored claim, claimed status, cited refs, uncertainties, and next read. |
| `source_verification` | Do source artifacts agree with the claim? | Git diff, changed files, logs, generated artifacts, source data, or transform outputs. |
| `validation_review` | Did the declared checks actually run and pass? | Test logs, smoke output, command transcripts, or deterministic gate output. |
| `acceptance_review` | Should this become accepted truth? | Closeout receipt, reviewer judgment, first-read verification, and unresolved risk state. |

The practical rule is:

```text
plans can be text-reviewed;
results must be evidence-reconciled;
worker reports are claims, not truth.
```

For code work, the reconciliation path should include at least:

```text
worker report
  -> git diff and changed files
  -> test or smoke output
  -> generated artifacts
  -> independent reviewer or deterministic judgment
  -> closeout projection
```

## Work-Slice Execution Receipt

`work_slice_execution_receipt.v1` lets closeout judge each packet work slice
from slice-level evidence instead of inheriting the packet-level status.

It can be generated by a worker, runner, or closeout collector. Runner-generated
receipts are conservative handoff records; worker-authored receipts should carry
stronger slice evidence when available. It is a profile-local or run-local
execution receipt, not an approval record and not accepted truth.

A worker-authored receipt is still a claim. Closeout may preserve that claim,
but it should not project a completed slice as independently completed until
source verification, validation review, routed review judgment, or closeout
collector evidence reconciles it.

`source_observation.v1` is the first read-only source-state input for that
reconciliation path. When closeout runs with `--include-git`, it records the
observed workdir, base ref, changed files, linked artifact refs, and warnings.
This is not accepted truth and it is not enough to promote a worker claim by
itself. It lets Ondesk and review surfaces see whether a worker claim is at least
being compared against current source state before tests, artifacts, and review
judgment are checked.

Recommended minimum shape:

```json
{
  "schema": "work_slice_execution_receipt.v1",
  "packet_id": "packet-id",
  "project_key": "forager-cli",
  "task_id": "task-id",
  "background_ticket_id": "background-ticket-id",
  "generated_at": "2026-06-03T00:00:00Z",
  "producer": "worker | runner | closeout_collector",
  "producer_role": "worker_claim | runner_observation | deterministic_verification | review_judgment | closeout_collector | legacy_receipt",
  "slice_id": "slice-0",
  "slice_index": 0,
  "slice_label": "Human-readable slice title",
  "status": "completed | deferred | missing | drifted",
  "claim_status": "completed | deferred | missing | drifted",
  "verification_status": "unverified | evidence_observed | validation_passed | review_accepted",
  "verification_summary": "What was independently checked, if anything.",
  "verification_refs": [],
  "summary": "What actually happened for this slice.",
  "evidence_refs": [],
  "validation_refs": [],
  "artifact_refs": [],
  "open_questions": [],
  "drift_signals": [],
  "next_safe_action": "What a fresh harness should do next for this slice."
}
```

Closeout should apply these rules:

- if a receipt exists for a packet work slice, preserve its reported status,
  producer role, claim status, verification status, and refs;
- if a worker claim reports `completed` but independent verification is still
  `unverified`, project the slice as `deferred` with `claim_status=completed`;
- if source observation is available, show its status and changed-file refs
  beside the slice claim so the next reviewer can reconcile the claim against
  source state;
- if a runner observation reports execution evidence, project the slice as
  runtime evidence, not semantic completion;
- only deterministic verification, review judgment, or closeout collector
  evidence with an independent verification status should promote a completed
  report into a completed closeout detail;
- if no receipt exists, keep the current inherited packet-level status but mark
  the slice as requiring manual review;
- `completed` means slice execution evidence exists, not that the result is
  accepted truth;
- accepted truth remains gated by `closeout_receipt.v1` and first-read
  verification;
- `deferred`, `missing`, and `drifted` slices should be shown before completed
  slices in Ondesk and review surfaces.

Storage stays close to runtime evidence. Closeout looks for
`work_slice_receipts.jsonl` beside result artifacts, runtime logs, workdirs,
background artifacts, and implementation packet artifact dirs. Local runners
also write conservative `runner_poll` receipts beside the result artifact when
a terminal result is observed for packet-bound work. Those runner-generated
receipts use `deferred`, because the runner can prove that execution produced a
result artifact but cannot prove semantic completion for each slice. A worker
or reviewer may later write stronger slice receipts with completed, missing, or
drifted status.

Projection rules:

- CLI JSON preserves packet ids, slice ids, status, refs, and raw drift
  signals.
- Closeout JSON also preserves `source_observation.v1`; Markdown surfaces show
  only the source status and a capped changed-file summary.
- CLI text and Ondesk prompt packages show counts first, then attention slices.
- `review_surface.v1` exposes a compact, grouped projection for WebUI and
  morning review.
- Telegram should only surface missing, deferred, or drifted slices when they
  require a human decision.

Open implementation decisions:

- which worker backends should emit stronger worker-authored receipts instead
  of relying on conservative runner-generated deferred receipts;
- whether `slice_id` should be deterministic from packet id plus slice index or
  from a content hash;
- what minimum evidence is enough to call a slice `completed`;
- how much drift explanation should be generated by deterministic gates versus
  routed Council or single-harness review.

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

Implemented operational slices:

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
- `work_slice_execution_receipt.v1` is typed and closeout now reads
  `work_slice_receipts.jsonl` from nearby runtime evidence directories. When a
  receipt matches a packet work slice by label, index, or slice id, that slice
  uses the receipt's completed, deferred, missing, or drifted status instead of
  inheriting packet-level status. Receipt summaries, drift signals, refs, and
  next safe action are preserved in JSON and attention summaries.
- local background/tmux polling now emits runner-generated deferred
  `work_slice_execution_receipt.v1` records beside the result artifact for
  packet-bound runs when no worker-authored receipt already covers the slice.
  This prevents closeout from treating packet-level execution completion as
  slice-level completion.

Remaining slices:

1. Connect richer state collectors so `forager project implementation-packet`
   can draft from current task, decision, closeout, and project-direction
   context instead of operator-supplied fields only.
2. Add Council or single-harness review prompts that critique goal coverage,
   scope balance, evidence sufficiency, and brand fit.
3. Make hosted harness and local worker prompts emit worker-authored
   `work_slice_execution_receipt.v1` records with stronger evidence than the
   conservative runner-generated deferred receipts.
4. Add richer drift explanations from deterministic gates, Council, or a
   single-harness reviewer when a receipt reports `drifted`.
