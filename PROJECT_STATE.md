# Project State

Updated: 2026-11-05

## Current focus

Private helper qualifications now recommend an exact selected-source
`inspect` command. Oversized ranges receive a compact refusal before a source
read; successful responses fit the retained 60-line and 8192-byte limits,
including JSON escaping and UTF-8 encoding. Oversized individual lines receive
an empty refusal instead of partial source output. Existing source-path and
frozen-checker restrictions remain in effect.

Validated size refusals consume command/inspection budgets but do not refresh
the tool-progress clock. Raw completion counts stay factual, with separate
nonprogress/progress command counts. The observed-output guard still stops a
direct 122-line checker dump after an edit. This is a preventive bound for the
selected helper route, not universal admission control over native commands.
The instructions prioritize the selected checker after an edit and forbid
rereading its already-inline source. Model-free fixtures cover recovery from a
refusal, encoded byte bounds, replay, scope, inactivity and hard deadlines.
R195 remains a failed native trial with incomplete code; no model retry,
97-vector change, installed activation or C5.1a/C6.3-local acceptance follows
from this source increment.

Private helper qualifications can now bind an explicit model and reasoning
effort in the selected phase. The same effort reaches both native contexts,
without adding an output-token cap. Unselected runs retain the backend default;
unsupported or mismatched policies fail before model metadata or native launch.
R191 verifies explicit `low` and absent output-token caps in the frozen-client
fixture and both backend-bound requests of one fresh real trial. The trial
stops before any model answer: Ollama's runner reaches model warmup but fails
to become ready, returning HTTP 500 on the first request. The native client's
HTTP retry remains in loading when the first context's 180-second deadline
ends. No tool, edit, recovery or helper check occurs. Effort performance and
C5.1a remain unqualified; the next diagnosis must separate model readiness from
the task clock before selecting another real attempt. Local regression checks
pass all 276 cases, formatting and strict Clippy. Owned resources are closed
and existing GPU jobs survive. This does not activate a production setting.

Native supervision now prioritizes completing a response. Nonempty reasoning,
answer text and tool-argument generation can outlast the tool-idle interval
without being counted as completed work. Silence still has a 180-second default
inactivity bound; selected phase/window deadlines, command timeouts and tool
budgets remain in force. Heartbeats, metadata, empty deltas and native message
replays do not extend the response clock. The inactivity reason changes from
`no_tool_progress` to `response_inactive`, with separate response/tool timing in
status and result evidence. The response-clock change has local fixture
verification. R191 observes waiting for a response, with no generated answer
to qualify sustained-response behavior. The installed runtime is unchanged.

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
