# Project State

Updated: 2026-06-19

This is the small current surface for Forager development work. It is separate
from the public product README and from the mdBook user guides.

## Current Focus

Forager is a local autonomy meta-harness around tmux-supervised,
harness-backed agent work. The current development focus is the generic
Offdesk operating loop, mobile Remote Operator surfaces, runtime recovery,
documentation/artifact hygiene, and keeping domain-specific validation history
out of product-facing docs. The product direction is defined in
`docs/project-direction.md`.

## First Reads For Development

- repository rules: `AGENTS.md`
- project direction: `docs/project-direction.md`
- hosted harness contract: `docs/hosted-harness-agents.md`
- product overview: `README.md`
- remote operator contract: `docs/remote-operator.md`
- operation cycle: `docs/guides/operation-cycle.md`
- current offdesk status: `docs/offdesk-operation-status.md`
- UI surface TODO: `docs/ui-surface-todo.md`
- Web dashboard control-plane TODO:
  `docs/web-dashboard-control-plane-todo.md`
- runtime smoke runbook: `docs/guides/offdesk-runtime-smoke.md`
- long-run validation runbook: `docs/guides/offdesk-long-run-validation.md`
- adaptive wiki contract: `docs/adaptive-wiki.md`

## Active Documentation Work

- Public README, mdBook introduction, brand system, and Astro landing page lead
  with the north star and local meta-harness positioning.
- The committed TUI preview must stay profile-neutral. It should not show a
  project-specific profile, domain module, or private workspace name.
- `forager project audit-docs` provides the v1 audit for governance surfaces,
  deliverables, decision sources, current freshness, latest aliases, logs,
  adaptive wiki projection freshness, and focused recommendations.
- `forager offdesk closeout` carries documentation governance recommendations
  into `RETURN_PACKAGE.md`, so `ondesk prompt-package` can surface them without
  embedding the full audit summary.
- `forager project apply-governance-hints` is the reviewed bridge from packet
  hints into target project files. It dry-runs by default, creates only missing
  governance surfaces with `--reviewed`, and never overwrites existing docs.
- `forager offdesk wiki export-markdown` defaults to the active profile's
  `wiki-vault/` projection and reports whether the markdown vault is missing,
  stale, fresh, or empty relative to canonical adaptive wiki JSON state.
- `forager doctor` and `forager status --json` expose active profile/app
  directory source, making legacy AoE storage fallback visible before
  migration.
- Telegram Remote Operator health is live for the default profile. It reports a
  polling listener, configured chat allowlist, local model availability, and a
  read-only action surface. Remote launch remains blocked by design.
- The external Telegram Remote Operator watchdog can run outside the listener
  process, read loop-status and systemd state, send rate-limited emergency
  alerts, and render user-service/timer units through the installer.
- Domain-specific validation notes have been moved out of mdBook product docs
  into `archive/domain-history/`. They remain available for historical
  inspection but should not shape public product language.

## Current Gaps

- The Remote Operator can guide project selection, initialization preview,
  plan draft creation, plan registration, plan-review approval, and read-only
  launch-preparation packet creation. It can also create a pending
  `dispatch.runtime` gate request, resolve that exact approval, and write a
  bounded `ExecutionBrief`, a local-review enqueue handoff receipt, and a
  prepared-workload binding receipt. It can enqueue only the bound
  `dispatch.runtime` task, start it only through task-scoped tick, and monitor
  only that same task through task-scoped tick with `--limit 0`. It can create
  a closeout packet only for that completed monitored task, then write a local
  `closeout-review` handoff with verdict command templates. It can record only
  handoff-bound `approved`, `revise`, or `blocked` closeout-review verdicts
  from Telegram. Accepted truth is recorded only when the resulting
  `closeout_receipt.v1` status is `accepted`.
- Closeout follow-up decision resolution and accepted-truth recovery surfaces
  still need a separate design and implementation pass.
- The Telegram listener now survives known transport failures, and the external
  watchdog covers stale listener, failed service, and emergency alert paths.
- Website dependencies were refreshed to close `npm audit` findings; Astro 6,
  Tailwind 4, and an esbuild security override are now part of the verified
  site build path.
- UI surfaces now have a first shared operator-state contract, compact Telegram
  decision relay cards, semantic TUI Offdesk summaries, a fixture-backed WebUI
  review route, live `review_surface.v1` export/hydration for the WebUI route,
  Playwright desktop/mobile screenshot coverage, and a TUI preview summary.
- The long-term Web dashboard/control-plane direction is captured in
  `docs/web-dashboard-control-plane-todo.md`: workstation overview, project
  portfolio, decision inbox, scoped provenance graph, action envelope, and
  dashboard-aware assistant.
- The first Web dashboard slice is live: `forager ondesk workstation-surface
  --json`, `npm run export:workstation-surface`, and the static `/dashboard/`
  route now share `workstation_surface.v1`.
- The workstation dashboard now has fixture coverage for healthy idle, agent
  outage, active run, accepted closeout, and accepted-truth recovery states.
- The `/dashboard/` project portfolio now behaves as a selectable work queue:
  selecting a project shows a focused detail panel with plan/runtime/closeout/
  truth chips, decision/runtime/truth-recovery counts, and a project-specific
  next action derived from the existing `workstation_surface.v1`. The
  dashboard's Scoped Graph now follows the selected project and shows that
  project's plan/runtime/closeout/accepted-truth path plus local blockers. The
  read-only Assistant panel now follows the same selected project and exposes
  scoped prompts plus state or receipt refs before any future action-card work.
- The read-only Web decision action center is live: `workstation_surface.v1`
  now embeds `decision_inbox_surface.v1`, and `/decisions/` renders open
  decision records with allowed action previews, stale guards, authorization
  boundary, and CLI fallback.
- Decision action previews now carry `action_envelope.v1` cards with observed
  hashes, nonce/TTL, issued/expiry timestamps, idempotency keys, forbidden
  effects, confirmation phrases, expected receipt schema, and safe CLI
  inspection fallback. `forager ondesk action-envelope --envelope <PATH>
  --json` now validates those envelopes against the current decision ledger and
  writes idempotent `action_envelope_receipt.v1` acceptance or stale-rejection
  receipts. `workstation_surface.v1` now attaches matching latest receipt
  verdicts back onto action cards, and `/decisions/` renders those verdicts
  without exposing full logs. `forager ondesk action-preflight --receipt-id
  <ID> --json` now starts only from a validated action-envelope receipt,
  rejects stale or non-latest receipts, rechecks the current decision hash, and
  writes an idempotent `action_execution_preflight.v1` without mutating runtime
  or project state. `forager ondesk action-decision --preflight-id <ID>
  --note <TEXT> --json` is the first action-specific executor: it requires a
  ready preflight, supports only bounded decision choices, appends canonical
  decision handoff records, records idempotent `decision_action_execution.v1`
  receipts, and still does not dispatch runtime work or update accepted truth.
  `workstation_surface.v1` now attaches the latest matching
  `decision_action_execution.v1` result back onto decision action cards, and
  `/decisions/` renders applied/blocked execution summaries without exposing
  full execution logs. `forager ondesk action-closeout --execution-id <ID>
  --json` now closes an applied decision action handoff into a canonical
  `DecisionReceipt`, writes `decision_action_closeout.v1`, and keeps runtime
  dispatch/project-file/accepted-truth mutation out of this stage. The
  workstation surface projects the append-only decision ledger through the
  latest record per decision id, so receipted decisions no longer remain open
  through stale ledger entries. `forager ondesk runtime-preflight
  --closeout-id <ID> --json` now verifies a receipted closeout against the
  latest canonical decision receipt and writes `runtime_dispatch_preflight.v1`.
  `forager ondesk runtime-dispatch --preflight-id <ID> --runner <RUNNER>
  --cmd <CMD> --json` queues a durable `OffdeskTask` and records
  `runtime_dispatch_receipt.v1`; it does not launch a process, so actual
  runtime execution still passes through `forager offdesk tick` and the
  scheduler gate. `workstation_surface.v1` now exposes a separate
  `runtime_dispatch_surface.v1` for receipted post-closeout handoffs, and
  `/decisions/` renders preflight, queue-dispatch, and tick CLI fallbacks
  without re-opening the decision inbox. The workstation surface also exposes
  `accepted_truth_recovery_surface.v1` for latest closeout receipts that are
  not accepted truth, and `/dashboard/` renders those follow-up, blocked, or
  retired-incomplete states with short local CLI fallback commands plus
  receipt-backed `accepted_truth_recovery_action_envelope.v1` previews. The
  matching `forager ondesk accepted-truth-recovery-envelope --envelope <PATH>
  --json` command writes only
  `accepted_truth_recovery_action_receipt.v1` validation/stale receipts and
  never executes the fallback or records accepted truth.
- Large Offdesk and Telegram adapter modules need staged decomposition before
  adding more mutation-capable remote actions.
- The 2026-06-26 refactor baseline is captured in
  `docs/refactor-baseline-20260626.md`. The mutation-freeze has been lifted by
  product decision to widen the Telegram operator surface, but new remote
  execution must reuse the existing receipt-gated CLI executors rather than
  add new mutation logic, and must land in the new `scripts/telegram_operator/`
  modules instead of the monolith.
- Telegram now has a guarded remote execution surface in
  `scripts/telegram_operator/dispatch.py`: `/decisions` lists open decisions,
  `/decision <id> <action> [note]` returns a single-use confirmation token
  bound to the target's observed hash with a TTL, and `/confirm <token>`
  runs the existing `action-envelope` -> `action-preflight` ->
  `action-decision` -> `action-closeout` chain after re-checking the hash.
  `/recovery` and `/recover <closeout-id> <action> [note]` mirror this for
  accepted-truth recovery follow-ups; `/confirm` on a recovery token runs
  `accepted-truth-recovery-envelope`, which validates and records a receipt but
  stops short of recording accepted truth. `/cancel` clears a pending
  confirmation. It never records accepted truth. The plan-session engine,
  `health`, and `receipts` modules still need decomposition.
- Runtime dispatch is available but opt-in: `/runtime` lists post-closeout
  handoffs and `/dispatch <closeout-id> <runner> -- <command>` queues an
  operator-supplied command, gated behind `--enable-runtime-dispatch`
  (default off; this is remote command execution). On `/confirm`,
  `runtime-preflight` re-verifies the closeout and `runtime-dispatch` queues a
  durable task that runs only through `forager offdesk tick`.

## Next Work Candidates

1. Split the Telegram Remote Operator adapter's remaining plan-session engine,
   health, and receipt logic into modules while preserving the current
   44-test behavioral contract.
2. Split the large Offdesk CLI into command handling and typed workflow
   transition modules.
3. Optionally add a curated allowlist mode for `/dispatch` (named command
   templates) as a safer alternative to free-form `--enable-runtime-dispatch`.

## Refresh Rule

Refresh this file when the active development focus changes, a new governance
surface is added, or the next contributor would otherwise need to infer current
state from multiple long-form status documents.
