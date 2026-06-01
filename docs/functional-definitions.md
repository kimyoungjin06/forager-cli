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
- Operator.
- Telegram relay or other notification bridge.

Inputs:
- Decision request.
- Approval brief.
- Options and decision impacts.
- Scope and non-authorized boundary.
- Optional free-form operator instruction.

Outputs:
- `decision_record.v1`.
- Approval brief projection.
- Execution handoff when approved.
- Decision receipt when consumed or closed.

Authorization Boundary:
- Permits only the bounded action named in the decision record.
- Does not authorize cleanup, wiki promotion, provider changes, or accepted
  truth unless those are explicitly separate decisions.

Acceptance Criteria:
- The operator can see what is being decided and what is not being decided.
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
- Proposed `forager ondesk review-surface --json`.
- Future WebUI.
- Telegram "details" link target.
- Prompt-package summary.

Open Design Questions:
- Should the first implementation be JSON-only, markdown, or both?
- Should packet generation be stored as an artifact or generated live?

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

Authorization Boundary:
- Candidate creation is provisional.
- Promotion requires review and provenance.
- Markdown exports are projections, not canonical truth.

Acceptance Criteria:
- Candidates remain findable without polluting canonical instructions.
- Promotions carry evidence and review receipts.
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
- Promotion or disposal decision.

Outputs:
- Artifact index.
- Retention recommendation.
- Review packet.
- Disposal-safe manifest.

Authorization Boundary:
- May recommend retention or disposal.
- Does not delete, move, or archive files without separate explicit approval.

Acceptance Criteria:
- Humans can find deliverables without opening every generated file.
- Runs do not create endless flat directories without review paths.
- Logs and raw evidence remain available but are not the primary review surface.
- Disposal candidates are inspectable before mutation.

Primary Surfaces:
- Closeout artifacts.
- Documentation governance reports.
- Future review surface packet.

Open Design Questions:
- What retention classes should be standard across projects?

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

## Cross-Capability Invariants

- Local profile state is canonical.
- UI surfaces are projections.
- Completed execution is not accepted truth.
- Acceptance requires evidence and review.
- Mutation and promotion are explicit approvals.
- Operator-facing summaries are compact and redacted.
- Machine-readable evidence stays available.
- Fresh harnesses start from artifacts, not from inherited chat.
- Legacy compatibility is explicit and should fade over time.

## Near-Term Functional Backlog

1. Define and implement `review_surface.v1` as the shared rich-review packet.
2. Make WebUI consume `review_surface.v1` rather than inventing its own state.
3. Add artifact governance indexes for long-running project outputs.
4. Strengthen adaptive wiki promotion receipts and review summaries.
5. Normalize hosted harness capability contracts for Claude Code, Codex,
   OpenCode, OpenHands, Gemini CLI, and local scripts.
6. Add harness comparison records after enough evidence exists.

## How To Use This Document

- Use this document to decide whether a proposed feature belongs in Forager.
- Use a capability's acceptance criteria to write a concrete `specs/<feature>`
  implementation spec.
- Use the authorization boundaries to reject UI shortcuts that would bypass
  approvals, receipts, or local truth.
- Update this document only when the product capability set changes, not for
  ordinary wording or screen-layout changes.
