# Web Dashboard Control Plane TODO

Updated: 2026-06-19

This is the long-term implementation backlog for turning the current WebUI
review route into a workstation-level Forager operations dashboard. The goal is
not to embed a terminal in the browser. The goal is to make project state,
decisions, provenance, action boundaries, and accepted truth visible and
operable without forcing the operator to decode terminal scrollback.

## Product Position

The Web UI should be a control room for one Forager workstation and, later, a
team-scale manager surface across multiple workstations. It competes with a
terminal only where the terminal is weak:

- seeing all active projects at once;
- finding the next decision that needs a human;
- understanding why a task is blocked;
- tracing plan -> task -> artifact -> closeout -> receipt -> accepted truth;
- executing only structured, receipt-backed Forager actions;
- asking a context-aware assistant about evidence and next safe actions.

The terminal and VSCode remain the raw-power fallback for debugging, unusual
commands, git work, and feature development.

## Design Principles

- Keep the first viewport calm: show health, attention, and the top next safe
  action, not every possible button.
- Treat actions as contextual: each card or drawer should expose only the
  actions that make sense for that state.
- Preserve progressive disclosure: summary first, evidence second, raw JSON and
  CLI fallback last.
- Never make the chatbot an action bypass. It may explain, search, summarize,
  and propose an action card; execution still goes through the same action
  envelope and receipt checks.
- Distinguish facts from inferences:
  - live state;
  - last observed state;
  - stale state;
  - receipt-backed fact;
  - inferred summary.
- Prefer scoped provenance graphs over full hairball graphs. The default graph
  should show the selected project, decision, task, artifact, or closeout and
  its immediate support chain.
- Keep project-file mutation, runtime mutation, closeout verdicts, and accepted
  truth visibly separate.

## Information Architecture

Use a small top-level navigation set:

- `Overview`: workstation health and top attention.
- `Work`: project portfolio, active runs, blocked runs, and closeout state.
- `Decisions`: the human decision inbox and action center.
- `Graph`: scoped work/knowledge provenance graph.
- `Settings`: profiles, workspace roots, providers, Telegram, watchdog, local
  model, and capacity.

Do not create a separate top-level page for every entity type. Runs, projects,
artifacts, receipts, and closeouts should open in detail drawers or stable
detail routes from these top-level areas.

## Core Read Models

### P0 - `workstation_surface.v1`

Goal: one read model that answers "is this workstation healthy, and what needs
attention now?"

Fields:

- `schema`
- `generated_at`
- `workstation_id`
- `profile`
- `workspace_roots`
- `health`
  - Telegram listener
  - watchdog
  - local LLM
  - configured provider
  - GPU endpoint if present
  - queue/runtime store freshness
- `capacity`
  - active background runners
  - queued tasks
  - provider-deferred tasks
  - token or budget signals when available
- `attention_counts`
  - pending approvals
  - decision inbox items
  - blocked tasks
  - failed tasks
  - closeout required
  - accepted truth missing
  - follow-up decisions required
- `top_attention`
- `next_safe_actions`
- `stale_state`
- `source_refs`

Done when:

- `/dashboard` can render from this model without reading unrelated local files.
- The top attention item matches `forager status --json` or a documented
  stronger source.
- The model explicitly says when data is stale or unavailable.

### P0 - `decision_inbox_surface.v1`

Goal: make human decisions the primary action center.

Decision categories:

- plan review;
- gate request;
- workload binding;
- runtime recovery;
- closeout packet review;
- closeout verdict;
- closeout follow-up decision;
- accepted truth recovery;
- adaptive wiki or knowledge promotion;
- provider/model outage triage.

Each decision item should include:

- `decision_id`
- `kind`
- `severity`
- `project_key`
- `what_changed`
- `why_now`
- `risk`
- `evidence_refs`
- `allowed_actions`
- `authorization_boundary`
- `stale_guard`
- `receipt_ref`
- `cli_fallback`

Done when:

- Every dashboard action starts from a decision item or a clearly scoped entity
  detail.
- The default view can be sorted by urgency without hiding blocked work.

### P1 - `project_portfolio_surface.v1`

Goal: show all projects as a manager-level portfolio rather than as a single
phase label.

Each project should expose parallel state chips:

- plan;
- runtime;
- decisions;
- closeout;
- accepted truth;
- wiki or knowledge state;
- last artifact;
- last activity.

Done when:

- A project can simultaneously show `plan=approved`, `runtime=running`,
  `closeout=not_ready`, `truth=missing`, and `decisions=1` without collapsing
  into a misleading single phase.

### P1 - `action_envelope.v1`

Goal: define the Web UI execution boundary before adding mutation buttons.

Fields:

- `action_id`
- `action_kind`
- `profile`
- `project_key`
- `target_ref`
- `observed_hash`
- `nonce`
- `ttl`
- `issued_at`
- `expires_at`
- `idempotency_key`
- `preview`
- `allowed_command`
- `forbidden_effects`
- `expected_receipt_schema`
- `requires_confirmation`
- `confirmation_phrase`
- `stale_rejection_reason`

Rules:

- No arbitrary shell input.
- No action without an observed target and receipt expectation.
- No accepted-truth action unless the closeout receipt or verdict path is
  explicit.
- Repeated clicks must be idempotent or produce a clear duplicate receipt.

Done when:

- Web UI, Telegram, and TUI can describe the same action boundary.
- A failed or stale action produces a durable receipt instead of silently doing
  nothing.

### P1 - `work_graph_surface.v1`

Goal: provide provenance and knowledge graph visibility without overwhelming
the operator.

Default graph scope:

`Plan -> Task -> Artifact -> Closeout -> Receipt -> Accepted truth`

Node types:

- workstation;
- project;
- plan;
- approval;
- gate;
- task;
- run;
- artifact;
- closeout packet;
- closeout receipt;
- decision record;
- accepted truth;
- wiki or knowledge entry;
- agent;
- provider;
- model.

Edge types:

- created;
- approved;
- queued;
- executed;
- produced;
- supports;
- blocks;
- supersedes;
- accepted;
- needs decision;
- generated by.

Done when:

- The graph can answer "what evidence supports this accepted truth?"
- The default view is scoped to the selected item, not the whole repository.

### P2 - `chat_context_surface.v1`

Goal: make the assistant useful inside the dashboard without bypassing the
control plane.

The chatbot can:

- summarize current state;
- explain a decision;
- trace provenance;
- compare projects by risk or attention;
- find artifacts;
- propose a next action card;
- draft a plan or closeout review note.

The chatbot cannot:

- run shell commands directly;
- mutate files directly;
- approve, enqueue, launch, close out, or accept truth outside
  `action_envelope.v1`;
- hide whether an answer is receipt-backed or inferred.

Done when:

- Every assistant answer cites state refs, receipt refs, or explicitly marks
  itself as an inference.
- Suggested actions appear as reviewable action cards, not as direct execution.

## Screen-Level TODO

### P0 - `/dashboard`

Purpose: calm workstation overview.

First viewport:

- workstation health;
- active work count;
- blocked and failed count;
- decision needed count;
- closeout and accepted-truth count;
- top attention item;
- two primary actions at most:
  - review top item;
  - open decisions;
- assistant entry with current dashboard context.

Avoid:

- raw command lists;
- more than three primary buttons;
- unscoped logs;
- full graph visualization.

### P0 - `/decisions`

Purpose: the primary action center.

Layout:

- decision queue;
- selected decision detail;
- evidence and receipt refs;
- allowed actions;
- authorization boundary;
- CLI fallback in a collapsed detail section.

Action rule:

- show one to three actions per selected decision;
- if there are more actions, group them under a secondary menu;
- destructive or accepted-truth actions require a stronger confirmation
  phrase.

### P1 - `/work`

Purpose: project portfolio and active work monitor.

Layout:

- project table or lane list;
- state chips per project rather than a single project phase;
- active task drawer;
- closeout and accepted truth status;
- filters for blocked, running, attention, accepted, and stale.

### P1 - `/graph`

Purpose: scoped provenance and knowledge graph.

Default:

- show selected project or decision support graph;
- one to two hops by default;
- highlight blocker and accepted-truth paths;
- offer "expand" only as an advanced action.

### P2 - Assistant Panel

Purpose: context-aware explanation and action proposal.

Entry points:

- overview context;
- selected project;
- selected decision;
- selected graph node;
- selected receipt or artifact.

Suggested prompts:

- "What needs my attention?"
- "Can this be approved?"
- "What evidence supports this?"
- "What blocks this project?"
- "Show the provenance path."
- "Draft a safe action note."

## Risk Register

- Button overload: mitigated by contextual actions and detail drawers.
- Stale dashboard state: mitigated by generated timestamps, stale banners, and
  observed-hash rejection.
- Chatbot action bypass: mitigated by action cards and envelopes.
- Misleading single project phase: mitigated by parallel state chips.
- Graph hairball: mitigated by scoped default graph.
- Duplicate clicks: mitigated by idempotency keys and receipt identity.
- Hidden terminal dependency: mitigated by collapsed CLI fallback on every
  action.
- Over-broad backend authority: mitigated by a local-only Forager control API
  or action packet processor with explicit schemas.

## Suggested Implementation Order

1. Define `workstation_surface.v1` and export it from existing local status,
   tasks, plans, closeout, Telegram loop, and review-surface sources.
2. Create `/dashboard` as a read-only static/hydrated route backed by
   `workstation_surface.v1`.
3. Define `decision_inbox_surface.v1` and render `/decisions` as read-only.
4. Define `action_envelope.v1` and implement preview-only action cards.
5. Wire low-risk actions through the envelope with nonce, TTL, observed hash,
   idempotency key, and receipt inspection.
6. Add project portfolio state chips through `project_portfolio_surface.v1`.
7. Add scoped provenance graph through `work_graph_surface.v1`.
8. Add dashboard-aware assistant in read-only mode.
9. Let the assistant propose action cards, still executed only through
   `action_envelope.v1`.

## First Slice

Implement `workstation_surface.v1` without adding any new mutation button.

Current status:

- Done: documented the long-term dashboard/control-plane backlog.
- Done: added an `attention` fixture for `workstation_surface.v1`.
- Done: added a typed dashboard projection helper for
  `workstation_surface.v1`.
- Done: added a read-only `/dashboard/` route with fixture fallback and
  client-side hydration from `/workstation-surface.json`.
- Done: added desktop/mobile Playwright coverage for dashboard rendering, no
  horizontal page overflow, wrapped command text, and live surface hydration.
- Done: added nav and landing-page entry points to `/dashboard/`.
- Done: added `forager ondesk workstation-surface --json`.
- Done: added `npm run export:workstation-surface`, which writes
  `website/public/workstation-surface.json`.
- Done: connected top attention to `forager status --json`, Offdesk status
  summary, task queue, decision ledger, and Telegram loop-status projection.
- Done: added fixture variants for healthy idle, agent outage, active run,
  accepted closeout, and accepted-truth recovery states.
- Done: added Playwright hydration coverage for all workstation fixture
  variants, including empty-state rendering for idle/accepted decision and
  project gaps.
- Done: upgraded the `/dashboard/` project portfolio from a passive table into
  a selectable project work queue with a focused detail panel showing each
  project's plan/runtime/closeout/truth chips, decision count, runtime handoff
  count, accepted-truth recovery count, and project-specific next action.
- Done: made the `/dashboard/` Scoped Graph follow the selected project,
  rendering that project's plan -> runtime -> closeout -> accepted-truth path
  plus local decision, runtime-handoff, and truth-recovery blockers.
- Done: made the `/dashboard/` Assistant panel follow the selected project in
  read-only mode, with scoped context, up to three suggested prompts, and
  explicit state or receipt refs so it cannot act as an execution bypass.
- Done: defined `decision_inbox_surface.v1` inside
  `workstation_surface.v1`, including open/visible counts, urgency sort order,
  read-only action model, stale guard, authorization boundary, receipt policy,
  and CLI fallback.
- Done: added a read-only `/decisions/` action-center route backed by
  `/workstation-surface.json`, with desktop/mobile Playwright coverage.
- Done: defined `action_envelope.v1` preview-only cards for decision actions,
  including target ref, observed hash, nonce, TTL, issued/expiry timestamps,
  idempotency key, allowed inspection command, forbidden effects, expected
  receipt schema, confirmation phrase, and stale rejection reason.
- Done: added `forager ondesk action-envelope --envelope <PATH> --json`, which
  validates an `action_envelope.v1` against the current decision ledger and
  writes an idempotent `action_envelope_receipt.v1` acceptance or stale
  rejection receipt.
- Done: `workstation_surface.v1` reads `action_envelope_receipts.jsonl` and
  attaches the latest receipt verdict, stale reason, failed checks, and receipt
  history count to matching action cards.
- Done: `/decisions/` renders the receipt verdict block on desktop/mobile
  without exposing full logs or arbitrary command input.
- Done: added `forager ondesk action-preflight --receipt-id <ID> --json`,
  which starts from an `action_envelope_receipt.v1`, rejects stale/non-latest
  receipts, rechecks the current decision hash, and writes idempotent
  `action_execution_preflight.v1` records without mutating project/runtime
  state.
- Done: added `forager ondesk action-decision --preflight-id <ID> --note
  <TEXT> --json` as the first action-specific executor. It requires a ready
  `action_execution_preflight.v1`, supports only bounded decision choices,
  records blocked attempts idempotently, applies supported choices as
  canonical decision handoffs, and never dispatches runtime work.
- Done: `workstation_surface.v1` reads `decision_action_executions.jsonl` and
  attaches the latest `decision_action_execution.v1` result plus execution
  history count to matching action cards by project, decision, and action kind.
- Done: `/decisions/` renders applied/blocked decision action execution
  results without exposing full execution logs.
- Done: added `forager ondesk action-closeout --execution-id <ID> --json`.
  It starts from an applied `decision_action_execution.v1`, verifies the
  matching handoff-ready decision record, appends a canonical `DecisionReceipt`,
  writes `decision_action_closeout.v1`, and remains explicitly non-runtime.
- Done: `workstation_surface.v1` now projects append-only decisions through the
  latest record per decision id, so a newly `receipted` decision no longer
  remains open because of older ledger entries.
- Done: Web/mobile decision action cards expose a short closeout fallback
  command for applied handoff executions instead of full execution logs.
- Done: added `forager ondesk runtime-preflight --closeout-id <ID> --json`.
  It starts from a `decision_action_closeout.v1`, verifies the latest canonical
  decision record is `receipted` with the matching `DecisionReceipt`, and
  writes `runtime_dispatch_preflight.v1` without queueing or launching runtime
  work.
- Done: added `forager ondesk runtime-dispatch --preflight-id <ID> --runner
  <RUNNER> --cmd <CMD> --json`. It requires a ready runtime preflight, writes
  an idempotent `runtime_dispatch_receipt.v1`, and queues an `OffdeskTask`; it
  does not launch a process. Launch remains under `forager offdesk tick` and
  the existing scheduler gate.
- Done: `workstation_surface.v1` now includes a separate
  `runtime_dispatch_surface.v1` built from receipted
  `decision_action_closeout.v1` records plus latest runtime preflight/dispatch
  receipts. `/decisions/` renders this as a post-closeout handoff area with
  preflight, queue-dispatch, and scheduler-gated tick CLI fallbacks, without
  re-opening receipted decisions or turning the decision inbox into a terminal.
- Done: `workstation_surface.v1` now includes
  `accepted_truth_recovery_surface.v1` for latest closeout receipts that are
  not accepted truth. `/dashboard/` renders follow-up, blocked/revision,
  receipt-missing, and retired-incomplete states separately from the decision
  inbox, with local CLI fallbacks for `closeout-decision` and
  `closeout-retire`.
- Done: accepted-truth recovery fallbacks now carry
  `accepted_truth_recovery_action_envelope.v1` previews, and `forager ondesk
  accepted-truth-recovery-envelope --envelope <PATH> --json` validates them
  against the current recovery surface into idempotent
  `accepted_truth_recovery_action_receipt.v1` records. The dashboard still
  does not resolve follow-ups, retire closeouts, move files, promote wiki
  state, or record accepted truth.

Scope:

- Add a CLI export command or script that writes
  `website/public/workstation-surface.json`.
- Include workstation health, attention counts, top attention, and
  `next_safe_actions`.
- Add fixtures for:
  - healthy idle;
  - pending decision;
  - agent outage;
  - active run;
  - closeout accepted;
  - accepted truth missing.
- Add `/dashboard` read-only route with desktop/mobile Playwright smoke tests.
- Keep `/review/` as the selected-item detail surface.

Non-goals:

- no backend server;
- no arbitrary command execution;
- no chatbot execution;
- no full graph;
- no mutation actions.
