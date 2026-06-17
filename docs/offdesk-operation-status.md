# Offdesk Operation Status

This page records the current Forager Offdesk operating baseline and the work
that remains before treating the system as a dependable overnight operator.

Snapshot date: 2026-06-17.

## Current Baseline

Forager now has a generic Offdesk operating loop, a read-only Remote Operator
projection surface, a Telegram transport adapter, project initialization
packets, plan registration/review records, runtime approval gates, background
runner probes, closeout packets, Ondesk return packages, and adaptive wiki
review surfaces.

At this point:

- the default Forager profile is loaded from primary Forager storage;
- the default profile has no pending approvals, queued tasks, active tasks,
  failed tasks, resume-pending tasks, stale background runs, or
  closeout-required tasks;
- the Telegram listener is installed as a user service and reports healthy
  polling state through `scripts/offdesk_remote_operator_telegram.py --health`;
- the Telegram listener can survive known Telegram transport failures and
  record them as loop state instead of exiting;
- the local model endpoint configured for the Remote Operator is available;
- remote start/launch/dispatch/shell actions remain blocked from Telegram by
  design;
- `cargo test`, `cargo fmt -- --check`, `cargo clippy -- -D warnings`,
  `mdbook build`, and the website build pass locally.

This baseline proves that the approval-gated runtime path and the remote
operator status path can be inspected consistently. It still does not prove
that every long autonomous run will produce useful project output.

## Verified Generic Flow

The intended flow is:

```text
project init
  -> implementation or plan packet
  -> plan dry run
  -> plan registration
  -> plan review
  -> launch-preparation packet
  -> enqueue dispatch.runtime task
  -> tick creates operator approval
  -> offdesk ok <approval-id>
  -> tick launches supervised runner
  -> poll reconciles heartbeat, log, and result artifacts
  -> closeout packet
  -> closeout review
  -> Ondesk return package
```

The Telegram Remote Operator currently covers the planning side of this flow:

- read-only `/status`, `/pending`, `/plans`, and `/show`;
- freeform planning request capture;
- project candidate selection;
- project initialization preview;
- project initialization packet creation;
- bounded plan draft creation;
- plan registration;
- explicit plan-review approval.

It intentionally does not cover runtime launch. Runtime dispatch still belongs
to local Offdesk gate approval.

## Completed Work

### Runtime Safety

- Provider fallback is approval-gated and does not consume
  `dispatch.runtime` approvals.
- Runtime tasks require scoped `dispatch.runtime` approval before launch.
- Long Python workloads should use `local-tmux` when they need live inspection.
- System-critical operations remain forbidden unless a separate reviewed
  capability and approval path exists.
- Pre-mutation snapshots and dry-run restore plans keep rollback reasoning
  separate from execution.

### Project And Module Scope

- `forager project init` creates read-only project operation packets.
- Project initialization can identify module candidates and selected operation
  targets without mutating the target project.
- `MODULE_OPERATION_PREFLIGHT.json` is a reviewed preflight reference, not a
  hidden runtime permission grant.
- Ondesk prompt packages include concise project/module summaries when matching
  initialization artifacts exist.

### Remote Operator

- Remote projections are read-only and operator-safe.
- Telegram messages are compact enough for mobile scanning and keep detailed
  diagnostics in local health output.
- Telegram freeform messages can become reviewable decision-inbox items or
  planning sessions.
- Telegram transport errors, send failures, and unexpected loop exceptions are
  recorded as health state with backoff.
- The user service uses restart-friendly systemd settings.

### Adaptive Wiki And Return Boundary

- Offdesk may create wiki candidates or run-local trial entries.
- Canonical wiki promotion remains review-gated.
- Closeout and Ondesk return are required lifecycle stages for completed
  runtime work.
- `forager offdesk wiki export-markdown` defaults to the active profile's
  `wiki-vault/` directory and reports projection freshness.

### Documentation And Artifact Governance

- `PROJECT_STATE.md`, `DECISIONS.md`, and `DELIVERABLES.md` define the shallow
  current surfaces for the Forager checkout.
- `forager project audit-docs` audits current-state freshness, decision and
  deliverable surfaces, human-facing output candidates, large logs, latest
  aliases, and adaptive wiki markdown projection freshness.
- `forager project apply-governance-hints` is dry-run by default, requires
  `--reviewed` to write, creates only missing governance surfaces, and never
  overwrites existing project documents.
- `forager offdesk closeout` carries focused documentation-governance
  recommendations into the Ondesk return package.
- Domain-specific validation history is retained under `archive/domain-history/`
  instead of being presented as product documentation.

## Remaining Work

### 1. External Remote Operator Watchdog

Goal: report when the listener, user service, or machine-level dependency is
stale even if the Telegram listener itself cannot send a message.

Acceptance checks:

- watchdog reads the loop health file and systemd status;
- stale listener state is reported with concrete recovery commands;
- watchdog does not depend on the listener process it is checking;
- repeated alerts are rate-limited.

### 2. Launch-Prep And Gate Bridge

Goal: connect approved plan review to launch-preparation packets without
collapsing plan approval and runtime launch approval.

Acceptance checks:

- launch prep binds to a reviewed plan hash;
- `forager offdesk gate` remains the runtime authority;
- Telegram cannot convert a plan-review approval into execution;
- stale plan, stale review, or changed launch packet blocks progression.

### 3. Monitor And Closeout Bridge

Goal: expose live task state and closeout readiness through compact operator
surfaces.

Acceptance checks:

- mobile surfaces show heartbeat, blocker, and next-safe-action summaries;
- closeout artifacts are linked through CLI-inspectable receipts;
- completed execution remains separate from accepted truth;
- review-required states do not disappear from status.

### 4. Documentation And Dependency Hygiene

Goal: keep product docs current and release-clean.

Acceptance checks:

- `docs/**` contains no project-specific validation names in product surfaces;
- committed screenshots are profile-neutral;
- CLI reference is regenerated when command surfaces change;
- website dependencies are refreshed and `npm audit` is reviewed.

### 5. Module Decomposition

Goal: reduce regression risk before adding more remote actions.

Acceptance checks:

- Offdesk CLI plan/closeout/remote-operator code is split into smaller modules;
- Telegram adapter session, rendering, provider, and loop code are separated;
- existing integration tests continue to cover the public contracts.
