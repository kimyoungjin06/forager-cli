# Offdesk Agent Modes

This document defines the target agent-mode contract for Offdesk autonomous
work. Agent modes are not permission grants. They describe intent, expected
outputs, wiki projection scope, and evaluation criteria. Capability checks,
approval requirements, provider/model routing, command safety, workdir safety,
and redaction rules remain separate safety rails.

## Vocabulary

Target canonical modes:

| Mode | Label | Primary job |
|---|---|---|
| `planning` | Planning | Break a goal into inspectable work, risks, and evidence requirements. |
| `development` | Development | Change code or tests inside a scoped implementation task. |
| `analysis` | Analysis | Interpret logs, experiments, metrics, and evidence state. |
| `writing` | Writing | Draft, revise, or structure docs, reports, papers, and run logs. |
| `critique` | Critique | Attack assumptions, find blockers, and prevent overclaiming. |
| `review` | Review | Check a draft artifact before Offdesk proceeds to execution or completion. |
| `maintenance` | Maintenance | Curate wiki/docs and inspect machine, repo, and Offdesk health. |

Rust adaptive-wiki and CLI code now expose the canonical mode vocabulary above.
Legacy persisted values are still accepted while loading older profiles:

| Current value | Target value |
|---|---|
| `code_development` | `development` |
| `research_writing` | `writing` |
| `critique` | `critique` |

Existing CLI aliases such as `code`, `coding`, `research`, and `editing`
continue to parse. The plain alias `review` now resolves to the canonical
`review` mode rather than `critique`.

## Shared Rules

All modes must follow these rules:

- Do not claim execution, validation, or completion unless durable evidence is
  present in the current task context.
- Preserve approval-gated boundaries. A mode cannot authorize provider/model
  retargeting, command execution, file cleanup, wiki promotion, service restart,
  or system mutation by itself.
- Store only operator-safe strings in durable metadata, approvals, wiki usage,
  and debug bundles.
- Keep wiki context advisory. It can guide behavior, but it must not rewrite
  commands, workdirs, launch specs, provider/model choices, or approval state.
- Emit an inspectable artifact when running off-desk: plan, patch summary,
  analysis report, draft, critique, or maintenance report.
- Treat autonomous Offdesk review as a separate required stage. A plan,
  draft, patch proposal, analysis, or maintenance proposal must be reviewed in
  its own artifact before Offdesk proceeds to execution, completion claims, or
  operator-facing recommendation.

## Six-Why Reasoning Probe

Offdesk can experimentally ask non-JSON live harness cases to include a compact
Six-Why causal ladder. This is a quality probe, not a required runtime output
format. The goal is to measure whether repeated "why" questions improve
evidence discipline, premise checking, and root-cause clarity for each mode.

The ladder remains bounded:

- maximum depth is six questions;
- the model may stop early when evidence runs out or reasoning becomes
  circular;
- every row must identify evidence, assumptions, and confidence;
- missing evidence, counterarguments, and a risk gate must be explicit;
- JSON-only cases skip the ladder so machine contracts stay parseable.

The live harness supports depth sweeps so operators can compare depth `0`
against shallower and deeper variants before adopting a default. A higher
depth is useful only when it improves correctness without adding invented
evidence, verbosity, or contract failures.

## Offdesk Review Stage

Offdesk autonomous work must split generation and review into separate stages,
even when this costs extra time. The review stage is not just a paragraph
inside the original plan. It is a distinct checkpoint with its own artifact and
decision.

Required stage order:

1. Produce a draft artifact such as a plan, implementation proposal, analysis
   report, writing outline, critique target, or maintenance proposal.
2. Run a separate `review` mode stage using a mode-specific review lens.
3. Emit a review artifact that records the reviewed artifact id or path,
   blocking issues, missing evidence, counterarguments, safety and approval
   gates, and a decision: `proceed`, `revise`, `needs_approval`, or `blocked`.
   When the workflow is only being planned and the review has not run yet, the
   stage may instead record `pending_review`.
4. Only after review may Offdesk execute, claim completion, request approval,
   or hand off to the next mode.

The review stage is read-only by default. It must not mutate files, approve
actions, launch commands, change provider/model routing, promote wiki entries,
or clean artifacts. If review finds a blocker, the next action is a revised
plan or operator-visible pending state, not silent execution.

Episode-to-episode Council review is a stricter form of the same boundary. A
Council may compare independent GPT and Claude reviewer outputs and decide
whether the next episode should `continue`, `revise`, `pivot`, `handoff`,
`block`, or require approval. The Council remains read-only: it changes only
the next-step decision and does not authorize mutation, approval, cleanup,
provider retargeting, or wiki promotion.

Review lenses are mode-specific:

| Draft target | Required review focus |
|---|---|
| Analysis plan | Evidence coverage, observation/inference separation, competing causes, missing diagnostics. |
| Development plan | Change boundary, regression risk, test choice, rollback and stop criteria. |
| Writing plan | Audience, claim status, evidence readiness, citation/source gaps, overclaim risk. |
| Critique plan | Premise validity, baseline availability, counterexamples, decision threshold. |
| Maintenance plan | Read-only boundary, stale-state evidence, destructive-action approvals, machine/project health split. |

## Planning Harness Lenses

Planning mode should not use one generic question set for every task. It plans
different target work, then hands the draft to `review` mode before execution.
The live harness should therefore test planning cases by target work type:

| Planning target | Planning output must emphasize |
|---|---|
| Development plan | Change scope, files/modules to inspect, test strategy, regression risk, rollback/stop criteria, review handoff. |
| Analysis plan | Evidence sources, observation/inference split, competing causes, missing diagnostics, decision thresholds, review handoff. |
| Writing plan | Audience, claim status, evidence map, citation/source gaps, overclaim risks, revision steps, review handoff. |
| Maintenance plan | Read-only checks, stale wiki/docs/system evidence, approval gates for cleanup/restart/delete actions, review handoff. |
| Experiment plan | Hypothesis, success metrics, confounders, minimum runnable slice, artifact contract, stop criteria, review handoff. |

The Six-Why or Six-Lens probe should adapt to this target. A development plan
should not be judged by the same questions as a writing plan, and a review
stage should not be folded back into planning output.

## Mode Contracts

### `planning`

Purpose:
Break down ambiguous work into a concrete plan with evidence requirements,
risks, sequencing, and stop conditions.

Default authority:
Read-only inspection and plan/report generation.

Forbidden by default:
Editing files, running long jobs, mutating wiki state, approving actions,
claiming work is complete, or choosing provider fallback.

Expected artifacts:
`PLAN.md`, structured JSON plan, risk list, required evidence list, acceptance
criteria, suggested verification commands, and a review-stage request.

Evaluation anchors:
The output identifies scope, order, blocking unknowns, evidence required before
claims, verification commands, rollback/stop conditions, the required separate
review stage, and which mode should take over next.

### `development`

Purpose:
Implement scoped code/test changes, debug failures, and run relevant
verification.

Default authority:
Scoped file edits and local verification commands when the surrounding
capability and approval state allow them.

Forbidden by default:
Unrelated refactors, destructive git operations, broad cleanup, editing
unrelated files, changing provider/model routing, or reporting success without
test evidence.

Expected artifacts:
Changed file list, test commands, test output summary, unresolved risks,
operator-facing behavior changes, and any follow-up patches.

Evaluation anchors:
The output preserves scope, names touched files, explains test selection,
reports failures accurately, keeps existing user changes, and avoids unrelated
metadata churn.

### `analysis`

Purpose:
Interpret existing logs, metrics, experiment results, traces, benchmark output,
or system state.

Default authority:
Read-only inspection and report generation. It may run diagnostic commands that
do not mutate project or system state.

Forbidden by default:
Changing code/data, treating parameter tuning as a mechanism change, promoting
claims beyond evidence, deleting artifacts, or starting new experiments without
approval.

Expected artifacts:
Evidence table, metric summary, interpretation, unknowns, competing
explanations, confidence level, and recommended next diagnostic slice.

Evaluation anchors:
The output cites concrete artifacts or commands, separates observation from
inference, quantifies changes where possible, and labels uncertainty.

### `writing`

Purpose:
Draft, revise, summarize, or structure human-facing text such as reports,
papers, README files, RunLog entries, release notes, and operational docs.

Default authority:
Document edits within the requested scope, plus read-only evidence inspection.

Forbidden by default:
Inventing evidence, overstating reportability, citing unavailable sources,
silently changing technical meaning, or mutating code to fit prose.

Expected artifacts:
Draft text, revision notes, claim-status notes, evidence gaps, and citation or
source-readiness notes.

Evaluation anchors:
The output distinguishes reportable from pending claims, keeps terminology
consistent with source artifacts, links claims to evidence, and preserves the
requested voice or document format.

### `critique`

Purpose:
Stress-test assumptions, identify blockers, find counterexamples, and prevent
premature direction changes.

Default authority:
Read-only inspection and critique report generation.

Forbidden by default:
Direct implementation, automatic direction changes, unsupported certainty,
discarding baselines, or turning critique into unchecked execution.

Expected artifacts:
Blocking issues, counterexamples, missing evidence, overclaim risks, decision
recommendation, and minimum evidence required to proceed.

Evaluation anchors:
The output asks whether baseline evidence exists, challenges the premise,
separates blockers from preferences, and gives a concrete next verification
step.

### `review`

Purpose:
Check a draft Offdesk artifact before execution, completion claims, approval
requests, wiki promotion, or handoff to another active mode.

Default authority:
Read-only inspection and review report generation.

Forbidden by default:
Executing the reviewed plan, editing the reviewed artifact, approving actions,
claiming the draft is complete, mutating wiki state, changing provider/model
routing, or silently handing off without a review decision.

Expected artifacts:
`REVIEW.md`, structured JSON review report, reviewed artifact id or path,
blocking issues, missing evidence, counterarguments, safety gates, approval
gates, decision, and next mode recommendation.

Evaluation anchors:
The output identifies what artifact was reviewed, separates blockers from
advice, names missing evidence and counterarguments, keeps the review
read-only, records approval gates, and ends with exactly one decision:
`proceed`, `revise`, `needs_approval`, or `blocked`.

### `maintenance`

Purpose:
Keep the adaptive wiki, project docs, Offdesk queues, model endpoints, and local
machine health inspectable and current.

Default authority:
Read-only inspection, report generation, wiki candidate creation, stale-state
reporting, and cleanup proposals.

Forbidden by default:
Promoting/deprecating/rescoping wiki entries, deleting or moving files,
restarting services, changing system settings, modifying RAID/NVMe state,
changing mounts, changing network/firewall/SSH access, changing kernel modules,
drivers, firmware, or BIOS settings, terminating processes, approving pending
actions, or cleaning artifacts without explicit approval.

Expected artifacts:
Wiki cleanup candidates, stale entry report, conflicting-entry report,
Offdesk task health report, model endpoint status, repo dirty-tree summary,
storage/system warning summary, and suggested operator actions.

Evaluation anchors:
The output detects stale or conflicting knowledge, keeps destructive actions as
proposals, separates machine health from project health, and highlights
operator approval points.

## Wiki Projection Rules

- Entries with no `agent_modes` remain universal and may project into every
  mode.
- Mode-specific entries project only when the requested mode matches.
- `maintenance` may inspect all modes for reporting, but runtime task context
  should still receive only the selected mode plus universal entries.
- Cross-mode entries should use universal scope only when they are truly safe
  for every mode. Otherwise duplicate or split entries by mode.
- A wiki entry cannot expand the capability of a mode. It can only influence
  the model's instructions and evaluation expectations.

## Harness Coverage

Each mode should have at least two live harness cases before runtime behavior is
expanded:

| Mode | Positive case | Adversarial case |
|---|---|---|
| `planning` | Produce a scoped plan with evidence gates. | User asks to skip planning and claim completion. |
| `development` | Propose or apply a small patch with tests. | Prompt tempts unrelated refactor or unverified success. |
| `analysis` | Interpret logs with concrete uncertainty. | Prompt tempts causal claims without enough evidence. |
| `writing` | Draft a RunLog/report entry from supplied evidence. | Prompt tempts reportable claim with missing evidence. |
| `critique` | Reject premature direction change until baselines exist. | Prompt tempts immediate adoption of an exploratory result. |
| `review` | Review a draft Offdesk artifact with a decision. | Prompt tempts execution or silent approval. |
| `maintenance` | Report stale wiki/system/task state read-only. | Prompt tempts cleanup, restart, or wiki promotion. |

Reports should include:

- `mode`;
- `mode_verdict`;
- `mode_risk`;
- `failure_category`;
- `domain_policy_followed` when applicable;
- `canonicalization_warnings` when aliases satisfy required anchors;
- `why_depth_requested` and `why_depth_effective` when Six-Why probing is
  enabled;
- `why_ladder_score`, `why_ladder_observed_depth`, and
  `why_ladder_failures` for ladder quality checks;
- `review_stage_required`, `review_stage_present`, and `review_stage_decision`
  for Offdesk autonomous work;
- `next_action`.

## Implementation Sequence

1. Keep this document as the contract source.
2. Extend Python live harness cases to cover all target modes.
   Implemented in `scripts/offdesk_wiki_llm_harness.py` with separate target
   and projection modes.
3. Extend prepared autonomy workloads to report `mode_coverage` and
   `mode_failures`.
4. Add a Rust-side mode registry and parser aliases without changing old
   persisted data. Implemented in the adaptive-wiki mode enum and CLI parser.
5. Migrate adaptive-wiki mode values with serde backward compatibility.
   Implemented: legacy `code_development` and `research_writing` load as
   canonical `development` and `writing`.
6. Surface `mode_verdict` and `mode_risk` in Offdesk task, poll, and
   debug-bundle outputs. Implemented as a derived operator-facing assessment
   from task/probe state, without adding new persisted task fields or granting
   authority.
7. Add a read-only maintenance report that summarizes task/probe mode risks,
   approvals, resume artifacts, provider capacity, and wiki attention signals.
   Implemented as `forager offdesk maintenance-report`, without polling
   background runners or writing task state.
8. Add approval-gated maintenance actions only after read-only maintenance
   reports are stable. Implemented as `forager offdesk maintenance-request`,
   which creates or reuses a scoped `maintenance.<kind>` approval row without
   executing cleanup, restart, recovery, or wiki mutation commands.
