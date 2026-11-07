# Project State

Updated: 2026-11-08

## Current focus

Private qualifications now stop the native worker at its first failed selected
check. A separate host failure checkpoint retains the exact source bytes, full
check report, command identity and unmodified model-progress observation.
Captured files are hash-verified; later source changes leave latest-version
counts unknown while retaining evidence for the checked version. Missing,
colliding or changed capture files remain unverified and cannot trigger a
host check, new context or retry. The supervised stop is post-execution
observation, not universal pre-execution fencing.

Raw native interruption and command failures remain distinct. A matching
selected-check stop is reported as verification/execution failure even when
the stopped native turn is incomplete. Other primary native stops retain
secondary command evidence. The known compaction advisory is labelled as an
advisory with its enforced policy stop; it proves neither hard exhaustion nor
successful context recovery. The one-warning policy and all limits remain.
Both CLI harnesses publish the exact executed selection bytes and hash,
including Unicode escaping, rather than reserializing a readable substitute.

R201 passes 301 Python cases, 8 Rust native contracts, formatting and strict
all-target Clippy; 2 explicit model/VM opt-ins remain ignored. A local child
process stops before a delayed write. The model-free R200 control replay stops
at its first failed checker and preserves 80/17 for the checked source while
later-source counts stay unknown. Three first-attempt snapshot-reader errors
are retained and fixed by a separate bounded source reader without widening
the invocation-file namespace. No decoder/checker or real model replay occurs.
R200 remains failed with its partial helper and all raw receipts unchanged;
C5.1a/C6.3-local remain unqualified and pending review. This source increment
does not activate an installed runtime or select another real trial.

Private qualifications now preserve a failed selected checker as
`verification_failed` and other failed or unknown-exit commands as
`execution_failed`, with command identities and a durable failure artifact in
the phase result and handoff. Failed helper counts are reported only when the
complete 97-case inventory, current source hash and native checker summary
agree. Missing or mismatched evidence keeps counts unknown. Native/model
receipts remain distinct from independent host verification; a failed command
does not run another check, complete an audit or advance the next phase.

Progress instructions require a separate command-execution call after an edit;
the existing audit still rejects progress helpers combined with heredoc edits.
R198's read-only projection classifies retained R197 as a verification failure
with 37 passed and 60 failed checks. Its historical `orchestration_failed`
receipt and model-created helper remain unchanged. Model-free regressions pass
282 Python cases and 8 Rust native-contract cases; 2 explicit model/VM opt-ins
remain ignored. Formatting and strict all-target Clippy pass. No real model
retry, installed activation or C5.1a/C6.3-local acceptance follows.

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
