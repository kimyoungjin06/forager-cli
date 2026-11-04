# Project State

Updated: 2026-11-04

## Current focus

Native supervision now prioritizes completing a response. Nonempty reasoning,
answer text and tool-argument generation can outlast the tool-idle interval
without being counted as completed work. Silence still has a 180-second default
inactivity bound; selected phase/window deadlines, command timeouts and tool
budgets remain in force. Heartbeats, metadata, empty deltas and native message
replays do not extend the response clock. The inactivity reason changes from
`no_tool_progress` to `response_inactive`, with separate response/tool timing in
status and result evidence. This source change has local fixture verification;
it does not repeat a model trial, qualify C5.1a or replace an installed runtime.

The requested maintenance sequence now has implementation candidates for
QA.1, Release.2, Release.3 and Ops.1, with local verification completed. Results
remain pending operator review in the existing continuity plan.

The subsequent source review corrects two edge cases: pending reply delivery
still reports a failure when an unrelated delivery is uncertain, and the Linux
maintenance helper matches the frontend's HOME fallback for relative XDG data
paths. The corrections pass nine attention-domain cases, sixteen maintenance
cases, six health CLI regressions, formatting and strict all-target Clippy.
They do not select a new installed runtime or accept the maintenance tasks.

The branch incorporates remote main `2c8a01f` through reviewed reconciliation,
retaining the qualified core's output, acceptance, runtime and containment
boundaries. The original exact source checkpoint remains at `3e01fbc`.

- Return integration fixtures use a supervised private HOME/XDG/tmux worker
  with failure and parent-interruption cleanup.
- `doctor` identifies the selected executable independently of its version tag.
- Cooperative frontend admission, reader leases and guarded cutover checks
  preserve agent sessions and restore terminals. The reviewed candidate SHA256
  and retained reader images stay pinned across alias changes and finalization.
- Health separates listener faults, delivery uncertainty and project review.
  New holds retain content-free attempt evidence; legacy evidence stays unknown.

Implementation and validation details are in the
[maintenance sequence](docs/project-direction.md#maintenance-sequence-after-the-2026-11-04-core-checkpoint),
[coordinated replacement contract](docs/installation.md#coordinated-frontend-replacement),
and [operator health domains](docs/remote-operator.md#health-and-review-domains).
Production selection, main integration and local-model qualification have their
own review and authorization boundaries.

## Plan ownership and execution boundaries

Task IDs refer to the existing operator-owned continuity plan. Its full history,
plan transaction journal, live queues, credentials, operational backups, and
local qualification evidence are private workspace artifacts. Resolve that
existing plan before applying task dispositions. The public roadmap is a
product summary rather than a second authoritative queue.

C5.1a, independent local-model coding and context recovery, and C6.3-local, the
complete local-adapter two-project cycle, remain unqualified. Core acceptance
does not accept other projects' scientific findings. Planning does not launch
a new Offdesk run or authorize another service cutover.

## First reads

- [Repository instructions](AGENTS.md)
- [Project direction and maintenance sequence](docs/project-direction.md)
- [Operation cycle and morning agreement](docs/guides/operation-cycle.md)
- [Core maintenance notes](docs/releases/v0.14.0.md)
- [Hosted harness contract](docs/hosted-harness-agents.md)
- [Product overview](README.md)

## Validation

Local checks cover strict all-target Clippy, formatting, 146 Python syntax
checks, remote policy contracts, both executable aliases, core closeout and
review-link regressions, supervised return fixtures, frontend cutover/PTY
regressions, and notification/health arbitration. Checks use private state and
owned local fixtures. Cross-platform CI and a final release build remain separate
qualification evidence. No broader local-model or scientific acceptance is
inferred from these checks.
