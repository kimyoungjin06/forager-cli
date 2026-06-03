# Functional Definition Set

This document defines Forager's required product capabilities without assuming
any current UI, screen layout, command naming, or implementation shape.

Use it as the product-level bridge between `project-direction.md` and concrete
feature specs. A feature may be implemented through CLI, TUI, Telegram, WebUI,
local files, or another surface, but the functional contract should stay stable.

## Reading Rule

For every capability, ask:

- What user problem does this solve?
- What durable local state proves it happened?
- What does it authorize?
- What does it explicitly not authorize?
- What must a fresh harness or human know to resume safely?
- Which evidence must remain machine-readable?
- Which summary must remain operator-readable?

If a UI element does not answer one of those questions, it is secondary.

## Functional Definition Template

New product capabilities should use this shape:

```text
FD-XXX Name

Purpose:
- The user-visible problem this solves.

Actors:
- Operator, hosted harness agent, Council/reviewer, external coordinator, or
  local automation.

Inputs:
- Required state, user intent, artifacts, approvals, and runtime facts.

Outputs:
- Durable records, review packets, notifications, JSON contracts, and handoff
  artifacts.

Authorization Boundary:
- What this capability permits.
- What it never permits by itself.

Acceptance Criteria:
- Observable conditions that prove the capability works.

Primary Surfaces:
- CLI/TUI/Telegram/WebUI/Ondesk packet/JSON, listed as projections rather than
  sources of truth.

Open Design Questions:
- Decisions that can change without violating the project direction.
```

## Capability Map

| ID | Capability | Product Role | Priority |
| --- | --- | --- | --- |
| FD-001 | Local Profile State | Canonical local truth | P0 |
| FD-002 | Harness Session Runtime | Interactive agent/terminal substrate | P0 |
| FD-003 | Hosted Harness Agent Workloads | Run external agents under Forager supervision | P0 |
| FD-004 | Approval And Decision Pipeline | Bound human/Council decisions | P0 |
| FD-005 | Offdesk Runtime Supervision | Durable unattended execution | P0 |
| FD-006 | Recovery And Resume | Restart from evidence, not scrollback | P0 |
| FD-007 | Closeout And Accepted Truth | Separate completion from acceptance | P0 |
| FD-008 | Review Surface Packet | Shared review contract for WebUI and other rich surfaces | P0 |
| FD-009 | Operator Notification Relay | Compact decision delivery | P1 |
| FD-010 | Ondesk Handoff | Return to a fresh harness | P1 |
| FD-011 | Adaptive Knowledge Governance | Promote reviewed lessons | P1 |
| FD-012 | Artifact Governance And Retention | Keep long projects findable and disposable | P1 |
| FD-013 | Provider And Model Routing | Select a viable worker without hiding risk | P2 |
| FD-014 | Harness Comparison And Evaluation | Compare agents by task evidence | P2 |
| FD-015 | External Orchestration Boundary | Coordinate without surrendering local truth | P2 |
| FD-016 | Implementation Packet And Alignment Review | Preserve original purpose before delegation | P1 |

## Priority And Readiness

Priority describes product importance. Readiness describes how safely a
capability can be used as a dependency.

Priority:
- P0: Required for Forager to be trusted as an autonomous harness supervisor.
- P1: Required for a good operator workflow once the P0 spine is stable.
- P2: Important expansion area, but not allowed to weaken P0/P1 contracts.

Readiness states:
- Defined: the user problem, actors, and authorization boundary are written.
- Contracted: durable state and JSON projection fields are named.
- Implemented: at least one CLI or runtime path produces the contract.
- Validated: tests or smoke runs prove the contract against realistic state.
- Operational: the capability is wired into the normal operator workflow.

Rule:
- P0 capabilities should reach `contracted` before any new WebUI-first design
  work depends on them.
- A capability should not be treated as operational until its summary surface
  and its machine-readable evidence agree on the same next safe action.
- A surface may be experimental, but the local contract behind it must state
  whether it is stable, transitional, or legacy.

## Capability Dependencies

| Capability | Depends On | Why It Matters |
| --- | --- | --- |
| FD-002 Harness Session Runtime | FD-001 | Runtime state must attach to a profile-local truth source. |
| FD-003 Hosted Harness Agent Workloads | FD-001, FD-002 | External agents need a supervised session and state boundary. |
| FD-004 Approval And Decision Pipeline | FD-001 | Decisions must become durable local records. |
| FD-005 Offdesk Runtime Supervision | FD-001, FD-003, FD-004 | Unattended work needs state, hosted workers, and approval gates. |
| FD-006 Recovery And Resume | FD-001, FD-005 | Recovery must replay evidence from supervised runtime state. |
| FD-007 Closeout And Accepted Truth | FD-001, FD-005, FD-006 | Closeout only matters if runtime and recovery evidence can be checked. |
| FD-008 Review Surface Packet | FD-001, FD-004, FD-007, FD-012 | Rich review needs state, decisions, acceptance, and artifact indexes. |
| FD-009 Operator Notification Relay | FD-004, FD-008 | Notifications should carry decisions and link to review details. |
| FD-010 Ondesk Handoff | FD-006, FD-007, FD-008 | A fresh harness needs accepted truth, recovery state, and review context. |
| FD-011 Adaptive Knowledge Governance | FD-004, FD-007 | Promotion should depend on reviewed decisions and accepted outcomes. |
| FD-012 Artifact Governance And Retention | FD-001, FD-005, FD-007 | Retention should preserve evidence while allowing old work to be disposed. |
| FD-013 Provider And Model Routing | FD-003, FD-004 | Routing changes are worker changes and may require approval. |
| FD-014 Harness Comparison And Evaluation | FD-003, FD-007, FD-012 | Comparisons need comparable runs, accepted closeouts, and artifacts. |
| FD-015 External Orchestration Boundary | FD-001, FD-004, FD-008 | External systems should call contracts, not become the truth source. |
| FD-016 Implementation Packet And Alignment Review | FD-003, FD-004, FD-007, FD-008 | Delegated work needs a design packet, routed review, closeout comparison, and resume surface. |

## Shared Data Contracts

These contracts are the preferred integration points between capabilities. A
new UI should project these contracts instead of inventing a separate model.

| Contract | Owner | Purpose | Projection Rule |
| --- | --- | --- | --- |
| `profile_state` | FD-001 | Canonical project/profile state root. | May expose paths in CLI JSON; human surfaces summarize ownership and freshness. |
| `session_record` | FD-002 | Interactive or hosted harness session identity, cwd, provider, and lifecycle. | TUI can show compact status; detailed paths stay in JSON/details. |
| `hosted_workload` | FD-003 | External agent command, model/provider, runtime handle, and safety envelope. | Operator summaries show who is working and why, not raw command dumps first. |
| `decision_record.v1` | FD-004 | Durable decision, options, rationale, response, and effect. | Telegram shows the choice problem; JSON preserves ids, files, and receipts. |
| `judgment_route.v1` | FD-004 | Records whether evaluation is routed to Council, one harness-backed agent, deterministic gates, or the user. | Human surfaces show route and reason; JSON preserves evaluator ids, evidence refs, and route policy. |
| `approval_brief.v1` | FD-004 | Human-readable decision card generated from routed judgment and evidence. | Must include context, options, evaluator route summary, recommendation, evidence sufficiency, tradeoff, and default timeout behavior. |
| `decision_receipt` | FD-004 | Proof that an approval, rejection, or natural-language response was handled. | Surfaces show final status and consequence before implementation continues. |
| `offdesk_task` | FD-005 | Unattended workload scope, guardrails, heartbeat, and stop conditions. | Summaries show progress, ETA, blockers, and interruption safety. |
| `runtime_evidence` | FD-005 | Heartbeat, progress events, logs, and process handle evidence. | Human surfaces summarize; machine surfaces retain artifact refs. |
| `next_safe_action` | FD-006 | The single safest continuation action after interruption or closeout. | Every operator-facing surface must agree on the first action. |
| `closeout_receipt.v1` | FD-007 | Completion status, accepted-truth state, evidence, and unresolved risk. | Never collapse `execution_complete` into `accepted`. |
| `review_surface.v1` | FD-008 | Rich review packet shared by WebUI, Ondesk handoff, and detail views. | Rich surfaces consume this packet as their read model. |
| `artifact_index` | FD-012 | Findable inventory of outputs, retention class, and disposal status. | User surfaces explain why an artifact matters before exposing its path. |
| `implementation_packet.v1` | FD-016 | Design-first packet for substantial delegated work. | Human surfaces show goal, alignment, scope, and readiness; JSON preserves commands, refs, stop conditions, and validation plan. |
| `recursive_alignment_review.v1` | FD-016 | Self-review that checks original goal coverage, north-star fit, brand fit, scope balance, and completion definition. | Surfaces show pass/revise/block and missing goals before worker launch or closeout acceptance. |
| `work_slice_execution_receipt.v1` | FD-016 | Slice-level execution receipt for work delegated from an implementation packet. | Human surfaces show attention slices and counts; JSON preserves packet ids, slice ids, status, evidence refs, drift signals, and next safe action. |

## FD-001 Local Profile State

Purpose:
- Keep Forager's canonical state in local, inspectable, profile-scoped files so
  no UI, Telegram message, terminal scrollback, or hosted harness becomes the
  source of truth.

Actors:
- Operator.
- Forager CLI/TUI.
- Hosted harness agents supervised by Forager.
- External coordinators that call Forager commands.

Inputs:
- Profile id.
- Task records.
- Approval records.
- Runtime evidence.
- Decision records and receipts.
- Adaptive wiki records.
- Closeout records and receipts.

Outputs:
- Durable profile files with stable schemas or documented compatibility rules.
- JSON projections for automation.
- Human-readable summaries derived from canonical state.

Authorization Boundary:
- Permits reading and appending governed local records.
- Does not authorize mutation outside the profile or project worktree.
- Does not let external systems rewrite Forager-owned truth directly.

Acceptance Criteria:
- A fresh process can reconstruct current Offdesk/Ondesk state from profile
  files.
- JSON output and human output are projections of the same local state.
- Secret-like values are redacted from operator-facing summaries.
- Legacy state remains readable through explicit compatibility paths.

Primary Surfaces:
- `forager status --json`.
- TUI home summary.
- Ondesk prompt package.
- Future review surface packet.

Open Design Questions:
- Which records need schema version fields before the next compatibility break?
- Which profile files should be append-only ledgers versus current-state files?

## FD-002 Harness Session Runtime

Purpose:
- Let the operator run and inspect multiple harness-backed agents or terminals
  without losing control of the local workspace.

Actors:
- Operator.
- Interactive harness-backed agents.
- Paired terminal user.

Inputs:
- Worktree path.
- Harness/tool selection.
- Optional branch or worktree configuration.
- Profile and repo settings.

Outputs:
- Tmux-backed sessions.
- Session metadata.
- Attach/detach state.
- Optional diff and status projections.

Authorization Boundary:
- Permits starting interactive local sessions.
- Does not imply Offdesk approval, cleanup approval, or accepted result status.

Acceptance Criteria:
- Sessions survive TUI exit through tmux.
- The operator can reattach and inspect the current process.
- Branch/worktree context is visible enough to avoid accidental edits.
- Session management remains useful without Offdesk features enabled.

Primary Surfaces:
- TUI dashboard.
- CLI session commands.
- Paired terminal.

Open Design Questions:
- Which harness-specific affordances belong in Forager versus remaining inside
  the hosted harness?

## FD-003 Hosted Harness Agent Workloads

Purpose:
- Run agents built by other harnesses as supervised workers while keeping
  Forager responsible for approvals, runtime evidence, recovery, closeout, and
  handoff.

Actors:
- Operator.
- Hosted harness agent.
- Local automation.
- Optional Council/reviewer.

Inputs:
- Harness capability contract.
- Launch command.
- Working directory.
- Mutation scope.
- Expected evidence artifacts.
- Runtime timeout and retention policy.

Outputs:
- Workload record.
- Runtime handle.
- Heartbeat/progress/log evidence.
- Result artifact.
- Failure signal.
- Closeout package reference.

Authorization Boundary:
- Permits launching the selected workload only after the required approval path.
- Does not give the hosted harness authority to mark its output accepted.
- Does not let one harness's internal memory become project truth.

Acceptance Criteria:
- Every hosted workload has a launchable, inspectable command contract.
- A completed process still requires review before accepted truth.
- Runtime evidence is enough to distinguish running, stale, failed, and
  complete states.
- Workloads are comparable across harnesses using common evidence fields.

Primary Surfaces:
- Offdesk queue and tick.
- Runtime poll.
- Debug bundles.
- Future harness comparison reports.

Open Design Questions:
- Which open-source harnesses should become first-class capability templates?
- How much harness-specific metadata should be normalized?

## FD-004 Approval And Decision Pipeline

Purpose:
- Make meaningful decisions explicit, reviewable, and receipted before they
  authorize handoff, runtime continuation, mutation, or user-facing claims.

Actors:
- Agent.
- Council/reviewer.
- Deterministic gate or local automation.
- Operator.
- Telegram relay or other notification bridge.

Inputs:
- Decision request.
- Approval brief.
- Judgment route and route rationale.
- Options and decision impacts.
- Scope and non-authorized boundary.
- Optional free-form operator instruction.

Outputs:
- `decision_record.v1`.
- `judgment_route.v1`.
- Approval brief projection.
- Execution handoff when approved.
- Decision receipt when consumed or closed.

Authorization Boundary:
- Permits only the bounded action named in the decision record.
- Does not authorize cleanup, wiki promotion, provider changes, or accepted
  truth unless those are explicitly separate decisions.

Acceptance Criteria:
- The operator can see what is being decided and what is not being decided.
- The route records whether the decision was evaluated by Council, a single
  harness-backed agent, deterministic gates, or the user.
- Telegram or any other relay is a projection, not canonical state.
- Free-form replies are scoped or treated as ambiguous.
- Applied handoffs can be closed by receipt.

Primary Surfaces:
- Decision ledger.
- Telegram card.
- Pending approval views.
- Future review surface packet.

Open Design Questions:
- Which decision types need stricter typed options rather than generic
  approval/deny/defer choices?
- Which policy chooses Council, one harness-backed agent, deterministic gates,
  or direct user decision for each workflow?

## FD-005 Offdesk Runtime Supervision

Purpose:
- Let the operator safely send bounded work away from the desk while Forager
  records what ran, what evidence appeared, and what next action is safe.

Actors:
- Operator.
- Offdesk scheduler.
- Hosted harness agent or local command workload.
- Runtime monitor.

Inputs:
- Task request.
- Approval state.
- Capability id.
- Runner kind.
- Command summary.
- Expected evidence.
- Runtime limits.

Outputs:
- Offdesk task state.
- Approval request or launch result.
- Runtime handle.
- Heartbeat/progress/log records.
- `next_safe_actions`.

Authorization Boundary:
- Permits only the queued command under its approved scope.
- Does not authorize automatic follow-up mutation, cleanup, wiki promotion, or
  accepted truth.

Acceptance Criteria:
- `tick` can explain why work launched, waited, or refused to run.
- Stale and failed work is visible before any retry.
- Long-running local workloads are inspectable after the launch command exits.
- `next_safe_actions` are shared across CLI/TUI/notification projections.

Primary Surfaces:
- `forager offdesk tick`.
- `forager offdesk tasks`.
- `forager offdesk poll`.
- `forager status`.
- TUI morning review.

Open Design Questions:
- Which runner kinds should be first-class beyond tmux/local process runners?

## FD-006 Recovery And Resume

Purpose:
- Recover interrupted or uncertain Offdesk work from durable state instead of
  guessing from terminal scrollback.

Actors:
- Operator.
- Recovery reviewer.
- Offdesk runtime.
- Fresh harness.

Inputs:
- Resume records.
- Runtime heartbeat/progress.
- Result sidecars.
- Failure/stale signals.
- Next safe resume step.

Outputs:
- Recovery-required next action.
- Resume decision.
- Retry, closeout, or block record.
- Debug bundle when needed.

Authorization Boundary:
- Permits inspection and bounded resume decisions.
- Does not authorize blind retry or result acceptance.

Acceptance Criteria:
- A stale task has a concrete reason and first inspection command.
- Recovery ordering outranks normal monitoring.
- Resume state appears consistently in status JSON and TUI.
- Debug bundles are redacted and durable.

Primary Surfaces:
- `forager offdesk resume`.
- `forager status`.
- TUI morning review.
- Debug bundle export.

Open Design Questions:
- Which failure classes should trigger automatic diagnostic bundle generation?

## FD-007 Closeout And Accepted Truth

Purpose:
- Separate "the agent finished" from "the result is accepted, safe, and ready to
  use."

Actors:
- Operator.
- Council/reviewer.
- Fresh Ondesk harness.
- Closeout generator.

Inputs:
- Completed tasks.
- Result artifacts.
- Runtime evidence.
- Open decisions.
- File operation candidates.
- Wiki candidates.
- Git state.

Outputs:
- Closeout plan.
- Return package.
- Commercial/review packet.
- Closeout review record.
- `closeout_receipt.v1`.

Authorization Boundary:
- Closeout generation is read-only.
- Review records do not move, delete, archive, promote, or mutate files.
- Only an accepted closeout receipt clears accepted-truth review requirements.

Acceptance Criteria:
- `accepted` receipt clears closeout-required state.
- `approved_with_followups`, `revision_required`, and `blocked` remain
  review-required.
- Legacy receipt-less approved reviews are compatible but visibly legacy.
- Return packages tell the next harness whether the output is accepted truth.

Primary Surfaces:
- `forager offdesk closeout`.
- `forager offdesk closeout-review`.
- `forager status`.
- TUI morning review.
- Ondesk prompt package.
- Telegram handoff.

Open Design Questions:
- Which receipt fields should become mandatory before any WebUI consumes them?

## FD-008 Review Surface Packet

Purpose:
- Provide the stable, UI-agnostic contract that rich review surfaces consume,
  including future WebUI, HTML reports, notebooks, or external dashboards.

Actors:
- Operator.
- Fresh Ondesk harness.
- WebUI renderer.
- External coordinator in read-only mode.

Inputs:
- Local profile state.
- Current `next_safe_actions`.
- Closeout state and latest receipt.
- Return package summary.
- Open decisions.
- Runtime evidence summary.
- Wiki review summary.
- Artifact index summary.

Outputs:
- `review_surface.v1` JSON.
- Optional redacted markdown or HTML projection.
- Stable links or artifact references for deeper inspection.

Authorization Boundary:
- Read-only review projection.
- Does not approve mutation, cleanup, provider changes, wiki promotion, or
  accepted truth by itself.

Acceptance Criteria:
- A user can understand morning review state from the packet without opening
  raw logs first.
- The packet exposes the same first next-safe-action as `forager status`.
- `accepted_truth` is explicit and derived from closeout receipt status.
- Artifact refs are available for automation but user summaries avoid raw path
  dumps.

Primary Surfaces:
- `forager ondesk review-surface --json`.
- `forager project artifact-index`.
- Future WebUI.
- Telegram "details" link target.
- Prompt-package summary.

Minimum `review_surface.v1` Shape:

```json
{
  "schema": "review_surface.v1",
  "generated_at": "2026-06-01T00:00:00Z",
  "profile": "default",
  "project_key": "forager-cli",
  "status": {
    "label": "needs_review",
    "summary": "One offdesk task completed execution and awaits acceptance.",
    "severity": "attention"
  },
  "next_safe_actions": [
    {
      "id": "review-closeout",
      "label": "Review closeout evidence",
      "reason": "Execution completed, but accepted truth is not set.",
      "command": "forager offdesk closeout-review --latest"
    }
  ],
  "accepted_truth": {
    "status": "pending",
    "source": "closeout_receipt.v1",
    "reason": "No reviewed receipt has accepted the result yet."
  },
  "closeout": {
    "latest_receipt_id": "receipt-id",
    "execution_status": "completed",
    "review_status": "pending",
    "unresolved_risks": []
  },
  "runtime": {
    "active": false,
    "last_heartbeat_at": "2026-06-01T00:00:00Z",
    "progress_summary": "Completed 4 of 4 planned steps."
  },
  "decisions": {
    "open_count": 0,
    "recent": []
  },
  "adaptive_wiki": {
    "candidate_count": 0,
    "promotion_required": false
  },
  "artifacts": {
    "summary": [
      {
        "label": "Closeout summary",
        "why_it_matters": "Contains the evidence needed for acceptance.",
        "retention_class": "review"
      }
    ],
    "refs": []
  },
  "redaction": {
    "operator_safe": true,
    "path_policy": "summary_first"
  }
}
```

Implementation Rule:
- The first implementation may be JSON-only.
- WebUI, TUI detail panels, Telegram detail replies, and Ondesk packets should
  project from this packet rather than each querying unrelated state.
- Raw paths are allowed in `artifacts.refs`, but user-facing summaries should
  explain the artifact's meaning before exposing its location.

Open Design Questions:
- Should packet generation be stored as an artifact or generated live?
- How much artifact detail belongs in the packet versus a linked artifact index?

## FD-009 Operator Notification Relay

Purpose:
- Deliver compact, decision-ready prompts to the operator when they are away
  from the terminal.

Actors:
- Operator.
- Telegram relay or future notification bridge.
- Decision pipeline.

Inputs:
- Approval brief.
- Message type.
- Options.
- Scoped natural-language input policy.
- Safe links.

Outputs:
- User-facing notification card.
- Relay state.
- Decision result JSON.
- Optional decision ledger ingestion.

Authorization Boundary:
- Records operator choice or ambiguity.
- Does not become canonical decision state until ingested into Forager records.
- Does not expose secrets, raw request ids, or raw path dumps.

Acceptance Criteria:
- The primary card explains decision, recommendation, question, and scope.
- The detail card explains evidence, impacts, and next actions.
- Button and free-form replies produce auditable result JSON.
- Multiple active requests cannot be accidentally resolved by ambiguous text.

Primary Surfaces:
- Telegram relay.
- Future email/mobile relays.

Open Design Questions:
- Which notification backends justify first-class support beyond Telegram?

## FD-010 Ondesk Handoff

Purpose:
- Let a fresh human or harness resume hands-on work from bounded artifacts
  rather than inherited chat history.

Actors:
- Operator.
- Fresh Codex/Claude/OpenHands/local harness.
- Offdesk closeout reviewer.

Inputs:
- Notes.
- Captures.
- Prompt package context.
- Latest closeout package.
- Project initialization packet.
- Documentation governance summary.

Outputs:
- Ondesk prompt package.
- Morning handoff request.
- Review entry decision record.
- Safe first-read list.

Authorization Boundary:
- Handoff authorizes review entry only.
- It does not authorize cleanup, file movement, wiki promotion, or accepting
  output without receipt-backed review.

Acceptance Criteria:
- The prompt package states receipt status and accepted-truth status.
- A fresh harness knows what to read first.
- Secret-like content is redacted.
- Prompt package generation remains read-only.

Primary Surfaces:
- `forager ondesk prompt-package`.
- `build_ondesk_handoff_request.py`.
- Telegram handoff.
- Future review surface packet.

Open Design Questions:
- Which prompt sections are mandatory for different harness types?

## FD-011 Adaptive Knowledge Governance

Purpose:
- Convert agent-created lessons into reviewed project knowledge without
  allowing unreviewed observations to become future instructions.

Actors:
- Agent.
- Operator.
- Wiki reviewer.
- Project maintainer.

Inputs:
- Candidate lessons.
- Evidence refs.
- Counterexamples.
- Projection conflicts.
- Freshness/review status.

Outputs:
- Candidate records.
- Review decisions.
- Promotion/deprecation events.
- Markdown projections.
- Proposal receipts.
- Promotion receipts.

Authorization Boundary:
- Candidate creation is provisional.
- Promotion requires review and provenance.
- Markdown exports are projections, not canonical truth.

Acceptance Criteria:
- Candidates remain findable without polluting canonical instructions.
- Promotions carry evidence and review receipts.
- Promotion mutations write a durable receipt that links candidate, entry, and
  audit state.
- Review reports summarize promotion receipt coverage and invalid receipt files
  for promoted entries.
- Conflicts produce review actions rather than silent overwrites.
- Long-running projects can prune or archive stale knowledge safely.

Primary Surfaces:
- Adaptive wiki CLI.
- Review/lint/export commands.
- Future review surface packet.

Open Design Questions:
- Which knowledge classes require expiry by default?

## FD-012 Artifact Governance And Retention

Purpose:
- Keep generated documents, run artifacts, scripts, logs, and review packets
  findable, resumable, and disposable over long projects.

Actors:
- Operator.
- Project maintainer.
- Offdesk runtime.
- Documentation reviewer.

Inputs:
- Artifact metadata.
- Run identity.
- Project/module association.
- Retention class.
- Retention review item selector.
- Promotion or disposal decision.

Outputs:
- `artifact_index.v1`.
- `artifact_retention_review.v1`.
- `artifact_retention_approval_request.v1`.
- `artifact_retention_application.v1`.
- `artifact_retention_promotion.v1`.
- Retention recommendation.
- Approval-only retention decision card.
- Profile-local retention application receipt.
- Snapshot-backed deliverables promotion receipt.
- Review packet.
- Disposal-safe manifest.

Authorization Boundary:
- May recommend retention or disposal.
- May record an approval request for a single retention follow-up.
- May consume an approved retention decision into a profile-local plan receipt.
- May append one reviewed promotion entry to `DELIVERABLES.md` when a
  rollback-ready mutation snapshot and restore plan exist.
- Does not delete, move, archive, publish, or accept output as truth without a
  later explicit mutation workflow.

Acceptance Criteria:
- Humans can find deliverables without opening every generated file.
- Retention review separates keep, review, missing, archive, and disposal
  candidates without mutating files.
- A specific review item can become a pending approval card with summary,
  evidence, options, impact, and scope.
- Repeating the same retention request reuses the pending approval instead of
  creating duplicate decisions.
- An approved retention decision can be consumed exactly once into
  `artifact_retention_application.v1`, which records the plan and keeps
  `mutation_performed=false`.
- Repeating `retention-apply` for the same consumed approval is rejected.
- A promote application receipt can produce `artifact_retention_promotion.v1`
  only after `--reviewed` and a rollback-ready snapshot; dry-run output does
  not write project files.
- Repeating `retention-promote` for an already listed artifact is a no-op.
- Runs do not create endless flat directories without review paths.
- Logs and raw evidence remain available but are not the primary review surface.
- Disposal candidates are inspectable before mutation.

Primary Surfaces:
- `forager project artifact-index`.
- `forager project retention-review`.
- `forager project retention-request`.
- `forager project retention-apply`.
- `forager project retention-promote`.
- `forager ondesk review-surface`.
- Closeout artifacts.
- Documentation governance reports.
- Prompt-package summary.

Open Design Questions:
- What later reviewed mutation workflow should execute archive/dispose moves
  after the plan receipt, snapshot, and restore plan exist?

## FD-013 Provider And Model Routing

Purpose:
- Select viable harness/model/provider paths while making capacity, cooldown,
  cost, and risk visible.

Actors:
- Operator.
- Scheduler.
- Provider fallback policy.
- Hosted harness agent.

Inputs:
- Capability requirements.
- Provider health/cooldown.
- Token/cost constraints.
- User approval policy.

Outputs:
- Provider recommendation.
- Approval request for retargeting when needed.
- Provider fallback metadata.
- Audit trail of actual worker chosen.

Authorization Boundary:
- Recommendation does not retarget work by itself.
- Provider changes require explicit approval when they alter cost, model,
  boundary, or quality expectations.

Acceptance Criteria:
- Work does not silently switch providers.
- The user can see why a provider was selected or deferred.
- Fallback metadata is available in status/debug surfaces.

Primary Surfaces:
- Offdesk scheduling.
- Pending approvals.
- Provider attention next actions.

Open Design Questions:
- Which provider changes are safe policy defaults versus user decisions?

## FD-014 Harness Comparison And Evaluation

Purpose:
- Compare harness-backed agents by task type using observable evidence rather
  than impressions from chat transcripts.

Actors:
- Operator.
- Evaluation reviewer.
- Hosted harness agents.

Inputs:
- Task type.
- Harness identity.
- Runtime metrics.
- Cost/token metadata when available.
- Failure modes.
- Evidence completeness.
- Review outcome.

Outputs:
- Harness comparison record.
- Capability recommendation.
- Failure taxonomy.
- Quality/cost/latency summary.

Authorization Boundary:
- Evaluation recommends future routing.
- It does not automatically promote a harness or retarget live work without
  policy approval.

Acceptance Criteria:
- Comparisons use the same task/evidence schema across harnesses.
- Accepted truth and completed execution are separated in evaluation results.
- Failures and recoveries count as first-class evidence.

Primary Surfaces:
- Future evaluation reports.
- Capability docs.
- Provider/model routing recommendations.

Open Design Questions:
- What minimum sample size is needed before a harness recommendation is shown?

## FD-015 External Orchestration Boundary

Purpose:
- Let higher-level tools coordinate schedules, notifications, and portfolios
  without bypassing Forager's local approval, evidence, recovery, and knowledge
  promotion model.

Actors:
- External orchestrator.
- Operator.
- Forager CLI/API layer.
- Project workspace tools.

Inputs:
- Read-only status request.
- Bounded command invocation.
- Notification schedule.
- Handoff target.

Outputs:
- Forager JSON.
- Queued command result.
- Notification artifact.
- Audit record.

Authorization Boundary:
- External tools may call Forager commands or consume Forager JSON.
- They must not rewrite profile state, approvals, receipts, or adaptive wiki
  records directly.

Acceptance Criteria:
- External coordination can trigger review flows without becoming local truth.
- Mutating operations still pass through Forager approvals.
- The operator can audit who asked Forager to do what.

Primary Surfaces:
- CLI/JSON.
- Future local API.
- Telegram/calendar/notion style integrations.

Open Design Questions:
- Whether a local HTTP API is needed before WebUI exists, or whether CLI JSON is
  enough for the next phase.

## FD-016 Implementation Packet And Alignment Review

Purpose:
- Preserve original operator intent, Forager's north star, brand boundary,
  scope, validation plan, and stop conditions before substantial work is
  delegated to Offdesk, Council, a hosted harness agent, or a local model.

Actors:
- Operator.
- Council/reviewer.
- Hosted harness agent.
- Local LLM worker.
- Fresh Ondesk harness.

Inputs:
- User goal or task request.
- Project direction and brand boundary.
- Relevant functional capabilities.
- Current decisions, closeout state, review surface, and evidence refs.
- Proposed implementation approach and validation plan.

Outputs:
- `implementation_packet.v1`.
- `recursive_alignment_review.v1`.
- `work_slice_execution_receipt.v1` when slice-level execution evidence exists.
- Design review outcome: `pass`, `revise`, or `block`.
- Worker-ready execution summary when approved.
- Closeout comparison between intended outcomes and actual results.

Authorization Boundary:
- Permits design review and worker instruction generation.
- Does not authorize runtime mutation, cleanup, wiki promotion, retention
  action, provider retargeting, or accepted truth by itself.

Acceptance Criteria:
- A substantial delegated task can be understood from the packet without chat
  scrollback.
- The packet states original goal, success state, north-star fit, brand fit,
  included scope, excluded scope, stop conditions, and validation requirements.
- Recursive alignment review flags when the plan is too narrow, too broad, weak
  on evidence, or disconnected from the product direction.
- Worker closeout reports which packet goals were completed, deferred, missing,
  or drifted.
- Work slices with explicit receipts use slice-level status and evidence;
  unreceipted slices remain visible as manual-review items instead of silently
  disappearing into packet-level status.
- Morning Ondesk review can tell whether execution served the original purpose
  rather than only completing a narrow implementation slice.

Primary Surfaces:
- `forager project implementation-packet` CLI JSON.
- Offdesk launch records.
- Council design review prompts.
- Review surface packet.
- Ondesk prompt package.
- WebUI packet review.

Open Design Questions:
- Which task size or risk threshold requires a full implementation packet?
- Should packets be generated from templates, from Council review, or from a
  deterministic collector plus routed judgment?
- Which packet fields become mandatory before local-model overnight execution?
- Should every worker emit one work-slice receipt per planned slice, or should
  closeout synthesize missing receipts from runtime evidence where possible?

## Cross-Capability Invariants

- Local profile state is canonical.
- UI surfaces are projections.
- Completed execution is not accepted truth.
- Acceptance requires evidence and review.
- Mutation and promotion are explicit approvals.
- Operator-facing summaries are compact and redacted.
- Machine-readable evidence stays available.
- Fresh harnesses start from artifacts, not from inherited chat.
- Delegated work starts from original intent and returns to that intent at
  closeout.
- Legacy compatibility is explicit and should fade over time.

## Surface Projection Rules

Each surface should answer a different operator problem from the same contracts.
Surfaces should differ in density and interaction model, not in truth.

| Surface | Primary Job | Should Show | Should Avoid |
| --- | --- | --- | --- |
| CLI text | Fast local operation and scripting hints. | Current status, first next safe action, concise reason. | Long raw logs by default. |
| CLI JSON | Durable automation and audit integration. | Full ids, paths, schema versions, receipts, and refs. | Human-only prose as the only evidence. |
| TUI | Live operator focus while at desk. | Active task, progress, open decisions, accepted-truth state. | Deep file inventories in the main view. |
| Telegram | Offdesk decision and interruption prompts. | Decision context, recommendation, options, consequence, detail trigger. | Raw ids, raw paths, or log-style payloads in the primary card. |
| Ondesk Prompt Package | Fresh harness continuation. | Accepted truth, unresolved risks, next commands, artifact references. | Claims that depend only on old chat history. |
| WebUI or HTML Review | Dense visual review and browsing. | `review_surface.v1`, artifact summaries, drill-down panels. | Direct mutation before approval contracts exist. |
| Project Docs and GH Pages | Public explanation and onboarding. | Direction, concepts, examples, supported surfaces. | Runtime state, secrets, or operational decisions. |

Projection rules:
- A compact surface may omit detail only if it links to or names a richer
  contract that contains the omitted evidence.
- A rich surface may add layout, grouping, and filtering, but should not invent
  a different accepted-truth or next-safe-action calculation.
- Raw artifact locations are machine-friendly data. User-facing cards should
  first explain what the artifact proves and why the user might open it.

## Functional Definition Done

A capability definition is ready to become an implementation spec when it has:

- A named owner source of truth.
- A durable state or JSON contract, even if the first version is small.
- An authorization boundary that says what the capability permits.
- A projection plan for CLI JSON and at least one human surface.
- A `next_safe_action` behavior when the capability can affect operator flow.
- An accepted-truth rule when the capability can complete or close work.
- A redaction rule for operator-facing summaries.
- A validation plan with tests, smoke commands, or inspectable artifacts.
- A compatibility note for transitional or legacy state.
- A recursive alignment check when the capability can delegate substantial
  work or affect the product direction.

If one of these is unknown, the implementation spec should name the unknown
explicitly instead of hiding it inside UI behavior.

## Implementation Slicing Rules

Forager features should move from contracts to surfaces in this order:

1. Define the profile-local state or JSON contract.
2. Add the smallest CLI JSON projection that proves the contract.
3. Add focused tests or smoke checks against realistic state.
4. Add a compact human projection for CLI text, TUI, Telegram, or Ondesk.
5. Add rich review surfaces after the compact and machine surfaces agree.
6. Promote learnings to adaptive wiki only after review and receipt handling.

The preferred first slice for WebUI work is not a screen. It is
`review_surface.v1` plus a static or CLI-generated projection that proves the
data is complete enough for review.

## Near-Term Functional Backlog

Completed P0 slice:
1. Implement `review_surface.v1` JSON generation from local profile state.
2. Add a closeout and accepted-truth section to that packet.
3. Add an artifact summary section that explains meaning before paths.
4. Add tests that compare `forager status`, closeout review, and
   `review_surface.v1` first next-safe-action.

Completed P1 operator workflow slice:
1. Project `review_surface.v1` into Ondesk prompt packages.
2. Make Telegram detail replies use the same review packet summaries.
3. Add `artifact_index.v1` for project outputs and profile-local handoff
   artifacts.
4. Project artifact-index summaries into `review_surface.v1` and Ondesk prompt
   packages.
5. Strengthen adaptive wiki promotion receipts and review summaries.
6. Add `artifact_retention_review.v1` and project it into review/Ondesk
   surfaces.
7. Add `project retention-request` as an approval-only bridge from retention
   review items into the pending approval ledger.
8. Define `judgment_route.v1` beside the existing decision route so evaluator
   routing and delivery/execution routing are not conflated.
9. Project `judgment_route.v1` across Telegram, CLI JSON, CLI human views,
   `review_surface.v1`, and Ondesk prompt packages.
10. Update `approval_brief.v1` summaries to explain evaluator route, route
    reason, recommendation, consequence, and evidence sufficiency without
    putting raw ids or paths in the primary card.
11. Add focused regression coverage for approval brief quality warnings, detail
    card evidence sufficiency, and prompt-package judgment route summaries.
12. Implement `implementation_packet.v1` and `recursive_alignment_review.v1`
    as typed state plus `forager project implementation-packet` CLI JSON and
    profile-local artifacts.
13. Project the latest `implementation_packet.v1` summary into
    `review_surface.v1` and Ondesk prompt packages so a returning harness can
    see source intent, delegation readiness, missing decisions, and validation
    shape before continuing.
14. Bind implementation packet summaries to Offdesk task/background launch
    records and Telegram/Ondesk detail projections without granting runtime
    authority.
15. Add packet-aware closeout comparison so linked implementation packets are
    reported as completed, deferred, missing, or drifted before any accepted
    truth decision.
16. Expand packet-aware closeout into itemized work-slice, validation, and
    expected-artifact coverage. Validation and expected-artifact items are
    matched against task/background evidence refs where possible.

Remaining P1 operator workflow slice:
1. Standardize retention-class names across project templates.
2. Design the reviewed apply/archive/dispose workflows that can consume
   approved retention decisions without bypassing closeout and deliverable
   authority.

P2 expansion slice:
1. Normalize hosted harness capability contracts for Claude Code, Codex,
   OpenCode, OpenHands, Gemini CLI, and local scripts.
2. Add harness comparison records after enough accepted evidence exists.
3. Define common source/evidence refs with source hashes and transformation
   labels for decisions, closeouts, retention reviews, and prompt packages.
4. Add a read-only evidence source registry and local evidence index before any
   writable memory or MCP surface.
5. Broaden `work_slice_execution_receipt.v1` from closeout sidecar collection
   into normal worker-runner emission and richer drift explanations.
6. Add `context_packet.v1` for hosted harness launch, Council review, and
   Ondesk resume once evidence refs are retrievable.
7. Add failure-learning candidates from failed sessions, corrected commands,
   and repeated user corrections, but route promotion through adaptive wiki or
   runbook receipts.
8. Add daily/operator briefing projections from deterministic collection plus
   routed judgment.
9. Decide whether a local HTTP API is needed after CLI JSON proves stable.

P3 integration slice:
1. Expose read-only MCP/local API tools for status, evidence search, closeout,
   pending decisions, and wiki candidates.
2. Add optional semantic search or compression sidecars only after rebuild,
   retrieval, redaction, and privacy checks are proven.
3. Keep writable external integrations approval-gated through existing decision
   and receipt contracts.

## How To Use This Document

- Use this document to decide whether a proposed feature belongs in Forager.
- Use a capability's acceptance criteria to write a concrete `specs/<feature>`
  implementation spec.
- Use the authorization boundaries to reject UI shortcuts that would bypass
  approvals, receipts, or local truth.
- Update this document only when the product capability set changes, not for
  ordinary wording or screen-layout changes.
