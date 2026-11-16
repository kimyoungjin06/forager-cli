# Operation Cycle Guide

This guide describes interface contracts and versioned qualification examples.
Those examples preserve their recorded test state. Use current local `status`,
`doctor`, and operator deployment records to identify the selected runtime.

## Inspecting a stopped task's runtime

Use `forager -p <profile> offdesk task-recovery <exact-task-id> --json`
to inspect local-background release evidence. This source-only diagnostic does
not initialize a profile, migrate data, signal processes, clear tickets or retry
work. A live supervisor, reused PID, missing binding or missing descendant-wait
receipt keeps release unverified. If a supervisor dies without its receipt,
descendant identities cannot be reconstructed from the dead parent: keep the
workdir held and investigate externally. Do not clear its ticket to bypass the
hold. A verified receipt still requires the normal lifecycle and approval gates.
Other runners and legacy parent-only receipts do not establish release here.

New explicitly contained launches also report `containment_populated`,
`containment_empty_admission_unfenced`, `containment_observation_unverified`, or
`containment_binding_missing_or_invalid`. All four retain the hold. Empty means
only that the exactly identified cgroup was observed empty; it does not guarantee
that processes cannot enter later or previously migrated out. Deleting the
binding file or cgroup never converts uncertainty into release authorization.

The internal closure journal adds `containment_seal_incomplete` (intent without
confirmed completion), `containment_seal_journal_invalid` (unreadable or mismatched
records), and `containment_sealed_migration_unverified` (closure recorded but prior
migration unverified). These also retain the hold. Do not delete a journal to
force another attempt. The diagnostic remains read-only; it never starts closure.

## Operating modes

Forager has two different jobs:

- On desk, it is a bridge around the harness the operator is already using.
- Off desk, it owns queueing, approvals, runtime evidence, recovery, and
  closeout.

The important boundary is that Forager should not treat raw chat history as the
source of truth. Each transition should produce a small artifact that the next
stage can read without inheriting an unbounded session.

## Cycle

```text
project init
  -> ondesk work package
  -> offdesk prepare
  -> launch dry run
  -> dispatch.runtime approval
  -> local-tmux runtime
  -> offdesk closeout
  -> ondesk return package
  -> reviewed wiki promotion
```

The cycle is intentionally slower than a raw resume. It makes each handoff
auditable and gives the operator a place to stop before runtime, cleanup, or
wiki promotion.

When several reviewed tasks and fixed routines are grouped into one unattended
window, use the immutable manifest and receipt model described in
the [bounded-shift contract](https://github.com/kimyoungjin06/forager-cli/blob/main/src/offdesk/shift.rs). Review the exact selected
manifest before arming a window; timer execution consumes its bound routine claims.

## Stage Contract

| Stage | Owner | Main command or artifact | What it proves | What it does not prove |
| --- | --- | --- | --- | --- |
| Project initialization | Forager scanner | `forager project init` | Project scope, likely module targets, first-read packet | Runtime readiness or task success |
| Ondesk handoff | Operator plus live harness | `forager ondesk note`, `capture`, `prompt-package` | Current intent and selected context | That the work is complete |
| Offdesk prepare | Deterministic scripts | `prepared_task.json`, `preflight.json`, `LAUNCH_DRY_RUN.md` | The intended runtime command, evidence, blockers, and approval path | Permission to launch |
| Runtime approval | Operator | `forager offdesk pending`, `ok` | One explicit approval for one runtime mutation | Cleanup, file deletion, provider retargeting, or wiki promotion |
| Runtime execution | Forager | `local-tmux` background ticket | The process launched and wrote health/result artifacts | That the result is safe to use without review |
| Closeout | Forager planner plus reviewer | `forager offdesk closeout` | What to keep, inspect, archive, or return to Ondesk | Permission to move or delete files |
| Ondesk return | Operator plus live harness | `RETURN_PACKAGE.md`, `prompt-package` | What the next fresh harness should read first | That wiki candidates are promoted |
| Wiki promotion | Operator review | `forager offdesk wiki ...` | Reviewed durable knowledge | Hidden automatic memory truth |

## Morning Return and Agreement

### Local checkpoint byte verification

The versioned integration consumer is `scripts/offdesk_metadata_consumer.py`.
It consumes only a bounded local JSONL roster (at most 4096 rows and 2 MiB),
with exact `work_id`, ordered zero-based `local_idx` and explicit
`compute`/`reuse`/`exclude` fields. These are engineering counts, not the R91
Parquet or scientific classification contract. It uses the checkpoint module
below at admission, after a durable entry reservation and before completion.

Its `initialize ROOT` and `batch ROOT --contract ... --contract-sha256 ...
--receipt ... --receipt-sha256 ... --publication ... --publication-sha256 ...`
commands require a prepared root containing
`consumer.py`, `validator.py`, `configuration.json` and `input.jsonl`. The two
source copies must match the running consumer and validator. Configuration is
`forager.metadata_consumer.v1` with `batch_rows` (1 through 128) and the explicit
`work_seconds`, `shutdown_grace_seconds`, `total_seconds` limits object.
There is no automatic latest-checkpoint selection or arbitrary command execution.

`forager.metadata_consumer.v2` additionally requires a `packet` object with
`manifest_sha256`, exact `purpose`, `source_vintage`, `source_artifact_sha256`,
`source_contract_sha256`, `selected_rows_sha256` and `policy_snapshot_sha256`.
Its root also needs `adapter.py`, `manifest.json`, `row_provenance.jsonl` and
`policy.json`. All four join the exact input list in every checkpoint contract.
The adapter copy must match the running packet verifier. The selected purpose,
epoch and source hashes must agree with the pinned manifest; policy membership,
diagnostic/live flags, original row reconstruction and canonical conversion are
recomputed at admission, after entry reservation and before receipt publication.
Removing context inputs or rehashing inconsistent files does not bypass these
semantic checks. A v1 root containing packet artifacts is now refused instead
of silently ignoring them. Old saved source/approval bindings need fresh selection.

Each call rehashes and validates the full small input and rechecks committed
prefix counts before producing only the next batch's immutable artifacts.
A committed batch can be explicitly reopened in another process. An interrupted
entry without a completion receipt remains held and cannot silently replay;
reinitialization never overwrites its artifacts. Completion is a read-only no-op.
The original total-window anchor and Linux boot identity carry across batches.
Time checks happen at admission/row/publication boundaries; no hard process
termination is implemented. No old large map, remote lookup, model call,
scientific acceptance or production scheduler integration is implied.

New consumer batches require the separately retained publication-observation
path and SHA256 returned by initialization or the preceding batch. The API also
requires these two pins after the contract/receipt pins. It does not discover a
latest observation or upgrade an old receipt. The legacy completion receipt
retains declared timestamps; its accounting does not measure its own publication.
The separate observation measures through durable receipt publication and
revalidation. It explicitly excludes its own final publication, checks grace
and the original total window again after that write, and grants no resume
authority. The metadata controller independently records child time through
process exit, including publication and interpreter shutdown. Failed child
observations and already published artifacts remain available for review.

`scripts/offdesk_metadata_packet.py` prepares a separate compatibility packet
from copied, pinned source rows and an explicit frozen query/member denial
snapshot. Engineering `exclude` means that particular embedding admission gate;
it grants no Work deletion, BC exclusion or diagnostic-compute restriction.
The provenance sidecar preserves the source row/index, P0 action, vector-presence
flag and diagnostic permission. Consumer indexes are selection ordinals.
The v2 batch consumer enforces this composite binding, while legacy v1 does not.
Prepared manifests retain `ready_for_launch=false`; the separate runtime
observation `packet_semantics_verified` reports validation and grants no approval.
Real-input execution still requires an explicit window/limits and operator
selection. Format compatibility is not source-policy, vector/model/text or
scientific qualification. The 64 KiB bound below applies to contract/receipt
records; policy/provenance input artifacts have their separate bounded reads.

`scripts/offdesk_resume_checkpoint.py` supplies a source-only Python API for
the `metadata_policy_bridge` stage. It is not wired into production workers.
`freeze()` explicitly records implementation/configuration, input and checkpoint
bytes in a new contract; the caller retains its returned SHA256 separately.
`publish_completion()` rechecks that pinned contract and artifacts before
publishing a complete, immutable receipt. The caller must also retain the
receipt SHA256 separately. `inspect_resume()` requires both external hashes and
returns a read-only byte observation with `resume_authorized=false`.

Paths are relative to an explicit local root. Linked or nonregular files,
duplicate paths/JSON keys, unsupported stages, missing/partial records and byte
changes are refused. Current bounds are 8 MiB per artifact, 32 MiB in aggregate
and 64 KiB per JSON record; old large maps are outside this qualification.
Publication never replaces existing contract/receipt files. A crash between
temporary and final hard links may leave a two-link record, which readers refuse.
Final source revalidation can fail after publication, leaving the receipt for
inspection. An error does not authorize deletion, replay or execution.

Work deadline, shutdown grace, total elapsed window and every supplied failed
or interrupted attempt are checked separately. These are checks on declared
time records, not independent process metering or worker termination. The API
does not authenticate the producer, prove historical input/output provenance,
qualify scientific content, or guarantee a globally atomic snapshot. A caller
must recheck current bytes at actual entry and obtain the existing runtime and
owning-task approvals. Neither a valid receipt nor partial task acceptance
unlocks a dependent task that requires full acceptance.

### Explicit task-plan inspection

The initial read-only C1 path is available as:

```bash
forager ondesk plan-inspect /path/to/owning-plan.md --project-key demo
forager ondesk plan-inspect /path/to/owning-plan.md --project-key demo \
  --candidate /path/to/proposed-plan.md --expected-sha256 <source_sha256>
```

Both commands emit JSON and bypass profile creation and migrations. They do
not write plans, register tasks, launch work, or grant approval. The caller
must explicitly select the owning document; the command checks the declared
project key, not registry ownership or physical project-root membership.
The full UTF-8 document hash binds the comparison baseline, including narrative
changes outside the structured block. Preview is not a reservation or a lock.

An opted-in owning Markdown document contains exactly one column-zero
`forager-plan` fence. Its JSON is the task data source; surrounding prose is
context, not a second synchronized task store. This example is fenced as
documentation and is not itself an importable plan:

````markdown
```forager-plan
{
  "schema": "ondesk_task_plan.v1",
  "project_key": "demo",
  "tasks": [{
    "task_id": "T-1",
    "goal": "Reproduce and fix the failure, then verify the result",
    "completion_criteria": ["Regression test passes; remaining limits recorded"],
    "depends_on": [],
    "review_state": "unreviewed",
    "evidence_refs": []
  }]
}
```
````

Review states are `unreviewed`, `pending_review`, `accepted`,
`partially_accepted`, `rework`, and `deferred`. There is no execution-completed
alias. Accepted or partially accepted declarations require nonempty evidence
references, but inspection does not verify those references or prove user
approval. Unknown fields, duplicate task IDs, missing/cyclic dependencies,
ambiguous or unterminated blocks, and oversized documents are rejected.
Plans are limited to 1 MiB and 1024 tasks; an empty task list is valid.

Existing Markdown plans are not automatically converted. Do not create a
parallel plan merely to use this command. Automatic ownership discovery remains
work in the existing operator-owned continuity plan. See
[document roles and ownership](documentation-artifact-governance.md#document-roles).

### Explicit adoption of an existing TODO

For a plain owning TODO, prepare an `ondesk_task_plan.v1` JSON file containing
only explicitly selected task IDs, goals, completion criteria and dependencies.
Initial task states must be `unreviewed`. Checklist text, completed boxes and
past runtime results are not automatically interpreted as task acceptance.

```bash
forager ondesk plan-adopt-proposal /absolute/project/TODO.md \
  --tasks /path/to/selected-tasks.json --project-key demo \
  --expected-sha256 <current_full_todo_sha256> \
  --request-id adopt-001 --decision-ref <operator_mapping_reference>
```

This read-only command emits a JSON proposal with `candidate_markdown` and
`change_request`. It preserves every original byte and appends one explicit
task block in the same document. It does not write the TODO, create profile
state, or import other checklist items. Existing owning blocks, unfinished
Markdown fences, linked source files and invalid task selections are refused.
The caller selects the owning document and supplies the mapping; the proposal
does not establish ownership or prove that the selected goals faithfully
summarize the prose.

After reviewing the complete candidate and obtaining the applicable agreement,
save those two fields as a Markdown candidate and JSON request, then use
`ondesk plan-apply --candidate ... --confirm-plan-write`. The adoption request
uses `ondesk_plan_adoption_request.v1` and the existing adjacent plan journal.
Apply rechecks the full baseline and exact append-only candidate; stale source
or changed prose cannot be overwritten. Replay and interrupted-write recovery
use the same protocol as normal plan changes. Once adopted, use the normal
inspection, source-bound shift and review-proposal paths. Adoption does not
retroactively bind old unstructured shift sources. Older binaries that reject
this new request kind cannot read a journal containing it; use a build with
adoption support. Existing request records retain their format.

### Confirmed plan changes

`ondesk plan-apply` is an explicit local plan-write operation, not an Offdesk
launch or a wiki promotion. After reviewing the preview, prepare a request:

```json
{
  "schema": "ondesk_plan_change_request.v1",
  "request_id": "morning-agreement-001",
  "project_key": "demo",
  "source_path": "/absolute/project/NEXT_ACTIONS.md",
  "baseline_sha256": "<64 lowercase hex characters from source inspection>",
  "candidate_sha256": "<64 lowercase hex characters from candidate inspection>",
  "decision_ref": "<reference to the operator's exact agreement>"
}
```

The hash strings above are placeholders, not valid request values.

```bash
forager ondesk plan-apply /path/to/request.json \
  --candidate /path/to/proposed-plan.md --confirm-plan-write
```

The command requires confirmation and exact content hashes, validates both
plans, and writes only the bound plan and its adjacent protocol artifacts.
For `NEXT_ACTIONS.md`, those artifacts are `.NEXT_ACTIONS.md.forager-plan.lock`
and `.NEXT_ACTIONS.md.forager-plan-journal.json`. They are a coordination lock
and request receipts, not a second task database. Their creation is explicit;
the command does not edit Git ignore rules. Keep the journal with its source
and preserve it across retries; it is not disposable scratch.

The journal records a pending request before atomic source replacement and
marks it committed afterward. The same request ID with different contents is
rejected. An applied replay reports `already_applied` without reverting later
work. Retrying the exact confirmed pending request can finish a pre-replacement
write, or return `recovered_content_match` if the source already matches the
candidate. That recovery proves content equality, not who performed an
interrupted write. Unexpected source contents block recovery; another request
cannot bypass an unresolved pending entry. Preserve and inspect the records
rather than deleting them to force progress.

The lock serializes cooperating writers; bounded no-follow access rejects
symlink paths and single-link checks reject hardlink aliases. Hashes are checked
again before replacement. This is not an operating-system compare-and-swap
against arbitrary editors: an editor that ignores the protocol can still race
the final check. Stop such writers or use an exclusive integration workspace.
The protocol has bounded storage (512 requests, 1 MiB journal) and never silently
evicts replay records. Reaching a limit requires a reviewed retention design.

`decision_ref` is operator-supplied attribution, not an authenticated approval
credential. Invocation is a trusted local write boundary; it must not be
exposed as an automatically authorized remote/agent tool. User-agreement UI and
remote approval binding remain separate C4/C6 work. No existing stored schema
is migrated by this opt-in journal. macOS source support is not a live-tested
acceptance claim from the Linux fixture suite.

### Binding a task to its Ondesk source

Before materializing a shift, inspect an exact already-reviewed selection:

```sh
forager -p <profile> offdesk shift preview <plan-id> \
  --review-id <review-id> --task-id <queued-task-id> \
  --starts-at <RFC3339> --stops-at <RFC3339> --json
```

To inspect existing queued candidates before choosing exact IDs:

```bash
forager -p <profile> offdesk shift candidates <plan-id> \
  --review-id <review-id> --project-key <registered-project-key> \
  --starts-at <RFC3339> --stops-at <RFC3339> --json
```

This read-only command reports individual preparation checks and exclusions.
It does not import Markdown TODOs, select an executable batch, establish
cross-task ownership/resource safety, or approve work. Repeat `--project-key`
for explicit scope; candidates still have to match the one reviewed plan.
Use the resulting IDs with `shift preview` to validate the combined selection.

Both commands include `queue_conflicts`: overlapping selected workdirs need
serialization or isolation, and overlap with active/uncertain same-profile
queue entries needs occupancy resolution. Missing paths produce an incomplete
report. This read-only observation does not acquire a lock. Bounded-shift tick
rechecks the queue under its existing profile tick lock before launch reservation
and holds on overlapping occupancy or unresolved paths. Interactive sessions,
other profiles, unbounded launchers and external shared resources are not fenced
by this gate. `no_observed_queue_overlap` is not a general safety approval.
For source-bound tasks, preview reports dependency IDs whose source review
states are already accepted. Unbound task dependencies remain unverified.

Cancellation is not process termination. A retained runtime ticket continues
to reserve its workdir even on a cancelled/completed queue record until supported
release evidence is available. Retry/resume and same-ID enqueue refuse to discard
an unresolved binding. The initial release check supports local-background only:
exact task/probe binding, no currently visible PID, and a freshly checked receipt
tagged `linux_subreaper_children`. New Linux unattested local-background launches
use `/usr/bin/python3 -I -S` to wait for all descendants, including orphaned setsid
children, before emitting that receipt. Parent termination alone is insufficient.
The receipt reports the primary shell status, not every descendant's success.
Supervisor loss keeps occupancy unresolved. This is not a security sandbox;
non-Linux and host-policy-attested launches do not gain this evidence automatically.
Legacy and other-runner tickets without supported evidence remain blocked; do
not clear tickets manually to bypass this boundary.

Queue mutation commands now share the profile tick lock and fail without writes
when busy. Retry the operator command after the current tick finishes. Queue
saves use synced atomic replacement; an empty/truncated existing queue requires
recovery instead of being interpreted as no tasks. This is not a cross-profile
or cross-file transaction protocol.

Preview validates the selected tasks and source versions without creating a
shift, approval or launch. It reports the window, available goals/criteria,
routine/dispatch limits and stop-new-work policy. Missing time estimates and
task-level cost ceilings are explicit, not inferred. It does not run host
isolation probes or automatically import TODOs. Use `shift prepare` for actual
preparation and isolation checks, then review and explicitly arm the resulting
manifest. Prepare rechecks current inputs; preview output is not approval.

An existing Offdesk task can opt into source binding with its existing artifact
reference field: `ondesk-plan-sha256:<source_sha256>=/absolute/project/plan.md`.
The queued task ID and project key must match the source plan. The source must
be inside the task's workdir, with no symlink or parent traversal. Keep the
project root as workdir for this path, or explicitly design a different root
contract before supporting nested module workdirs.

The artifact ID/path are already included in the shift launch-spec hash.
Bounded shift preparation and pre-launch validation reread the source, require
its exact hash, reject missing or pending-review/accepted/deferred tasks, and
require accepted dependencies. Cached artifact `present` is not proof. A changed
plan needs renewed review and binding. Historical identity checks do not reread
the plan, so later legitimate plan edits do not erase old execution evidence.

This is opt-in: legacy unbound tasks remain unbound, and a plain enqueue does
not acquire approval. The new live check is on the bounded shift path, not a
claim that every generic dispatcher enforces this binding. A temporary-state
CLI fixture exercises one deterministic local background receiver and source
drift rejection; it is not container isolation or live-model acceptance.

Shift review JSON includes a per-task `closeout_report` projection. It names
the latest attempt status, declared evidence references, verification limits,
and next actions without changing the Ondesk source. Scope comes from the
hash-matched execution command, not task reason or a full historical
goal/criteria snapshot. Missing
or changed task records suppress that scope. Completion receipts do not verify
actual changes or authorize acceptance: reports remain `pending_user_review`
and `plan_update_authorized` is false. `terminal_phase` preserves the receipt's
observed phase separately from recovery disposition. Local result-file presence
does not validate a business status in its contents; a missing callback does not
prove an exit code. The temporary bound fixture covers early completion, a
failing receiver, lost acknowledgement, attempt-budget exhaustion, and source
drift. Newly prepared bound shifts preserve original source bytes inside the
approved binding. `original_goal` and `original_completion_criteria` come from
that hash-verified snapshot even when the current plan moves. These are requested
criteria, not proof they passed. Older records report `not_preserved`, without
backfilling current text. Full plan text is retained locally, so consider source
sensitivity before opting in. Old binaries cannot read new populated bindings.
Task-specific evidence verification and broader isolation and budget acceptance
remain unfinished.

`result_observation` reads up to 64 KiB from the declared result file inside the
task workdir. Relative result paths resolve from that workdir regardless of the
review command's current directory. It reports a content hash, recognized `status`, integer `exit_code`,
and comparisons of `task_id`, `request_id`, and `ticket_id` with the exact attempt.
Missing or mismatched IDs are not accepted as execution attribution. Even matching
IDs are self-reported, and `process_exit_verified` remains false. These are
current bytes, not preserved terminal-time evidence. Conflicts with completion
receipts require review and never authorize automatic TODO acceptance.

New unattested `local-background` runs also use a supervising shell to record
the command shell's wait status in a private `.forager-exit-*` directory inside
the workdir. Polling reloads that nonce-bound receipt after the dispatcher exits.
Missing or invalid receipts remain uncertain, and nonzero status overrides
result-file presence. `last_observed_supervisor_shell_status` is separate from
the result JSON's reported exit code. This is not goal acceptance, same-user
tamper resistance, exact signal classification, or a power-loss guarantee.
Existing runs, tmux, and host-policy-attested launches are not upgraded in place.
Keep receipt directories as evidence; cleanup is a separate explicit decision.

Result JSON may include `evidence` entries with `kind` (`change` or `check`),
`description`, `path`, and `sha256`. References are examined only when all three
reported execution IDs match. A maximum of 16 files, 64 KiB each, is read inside
the workdir. Hash matching establishes current byte correspondence, not task
authorship, check execution, or truth of the description.
An agent's `pending_user_review`, `blocked`, or `revision_required` status remains
a reported result, even when the supervisor exited successfully. Shift closeout
adds `result_content_requires_review` and a conflict-resolution action when a
terminal-completed run does not report `completed`. The runtime completion marker
does not become an accepted task verdict. Relative `--artifact id=path` refs on
`offdesk enqueue` resolve from the task `--workdir` and are stored as absolute
paths so launch checks examine the intended files.

### Morning context from a saved shift review

For a new session or a selected existing session, extend the existing prompt
package with explicit morning inputs:

```bash
forager -p <profile> ondesk prompt-package /absolute/project \
  --project-key <project-key> \
  --owning-plan /absolute/project/NEXT_ACTIONS.md \
  --morning-review /absolute/review-reference.json \
  --expected-branch <current-reviewed-branch> --json
```

`review-reference.json` contains the same four fields as `shift_review` below:
`path`, `sha256`, `shift_id`, and `manifest_sha256`. Keep the exact saved report
bytes. The command checks them against the persisted shift manifest and its
preserved Ondesk sources, without initializing a missing profile. The project
workdir must retain its approved directory identity, and the current owning
plan must be a bounded regular file inside that workdir. Wrong projects, changed
roots, missing historical sources or reports, invalid journals, and an explicitly
requested wrong branch are refused.

For an existing checklist such as `TODO.md` without a `forager-plan` block, add
`--review-only-unstructured` to the same command. This reads and hashes the
current checklist, validates the exact saved shift report and manifest, and
returns bound task closeouts with `canonical_plan_task_mapping: unverified`.
It does not import checklist items, recover an unpreserved historical plan,
bind a packet workdir to the canonical project, or permit `--resume-session`.
The package marks agent read-scope enforcement as unverified; a declared input
list or command log does not prove filesystem confinement.
The output has `authority: review_only` and cannot be used as a plan-change
proposal or session-input ticket. Compare the checklist and packet manually
before recording a disposition in the project's own plan.

The `morning_return` JSON context includes current and historical plan hashes,
original and current tasks, added/changed/removed task IDs, saved execution and
evidence observations, selected-project recent Council decisions, and journaled
review decisions with remaining work and unanswered questions. Journal entries
state whether they were committed and whether their candidate matches the
current plan. Recorded decisions reuse the proposal shape checks, and a journal
candidate matching the current plan must also match its task dispositions and
decision-specific evidence. Contradictions are refused rather than hiding
pending review. Pending journal requests require recovery before another review.
A recorded review matching the current plan is retained without requesting the
same agreement again. This does not independently authenticate the named user.

`exact_baseline`, `document_changed_tasks_equal`, and `tasks_changed` distinguish
byte equality, document edits with equal task records, and task changes. Task
ordering and JSON formatting are document changes. Neither kind of difference
is silently rebased: C4.2a proposal/application still requires the original exact
baseline. Missing current plans are refused; historical-only inspection remains
available through `offdesk shift review`.

Add `--resume-session <exact-session-id>` to bind an existing saved session in
the same workdir. Omit it for fresh-session recovery. Both modes produce a
package for explicit reading; neither sends input, checks a safe terminal input
point, nor proves acknowledgement. Historical Git branch/HEAD were not preserved
by older shift bindings. Current Git branch, HEAD and dirty status are observations;
`--expected-branch` checks a caller-supplied expectation, not a recovered historical
branch. Truncated Git status is marked. Plan, journal, report, shift, Git and
selected session are reread before returning; this is not a lock against later
changes.

Optional `--output /absolute/new-package.md` writes a private temporary file,
syncs its complete contents, and publishes it without replacing an existing
file or symlink. A failed payload write leaves no incomplete destination, so
export can be retried. Temporary files may survive abrupt process termination;
this is not a power-loss recovery guarantee. Oversized packages fail before
export rather than silently truncating recovery context. Operator-facing fields use existing secret
redaction. The saved report is frozen evidence, not a fresh runtime report; no
plan write, task launch, acceptance, or knowledge promotion is authorized by the
package. The morning mode cannot combine capture IDs, mode filters or a fresh
document audit; the ordinary prompt-package path remains available for those.

### Confirmed local return input and read claims

After resolving the package's pending review decisions, explicitly select a
ready Claude or Codex session in the same workdir. A fresh-session package can
target a newly created session; an existing-session package must retain its
selected session ID. The source CLI provides:

For an explicitly restricted return, register a stopped Codex or Claude session
whose command and credentials/configuration are ready inside a narrow workdir.
Launching runs that session's registered command; it does not create a new
registration, stage credentials, run repository hooks or send a return prompt.
The canonical Linux Landlock filesystem policy requires ABI 5 or newer:

```bash
forager -p <profile> ondesk return launch-restricted --session <exact_session_id>
```

Use the absolute `read_policy_receipt` path printed by that launch when preparing
the return preview:

```bash
forager -p <profile> ondesk return preview \
  --package /absolute/workdir/morning-package.md --session <exact_session_id> \
  --read-policy-receipt /absolute/workdir/.forager-read-policy-<id>/receipt.json \
  --output /absolute/private/return-ticket.json
```

Both the package and owning plan must be inside the approved workdir. The ticket
binds the exact receipt bytes, canonical policy, root identity, launcher and
restricted child process identities. Preview and send recheck that binding;
changed receipts, replaced processes and unconfined existing sessions fail
without input. An already protected session can receive a new bound preview;
launching protection again into any live session is refused. Existing unconfined
processes cannot be confined retroactively. Omit `--read-policy-receipt` only
when intentionally using the existing unrestricted return path.

The filesystem policy does not cover network, IPC, same-user tampering,
preopened descriptors, metadata access, hardlink aliases or nested mounts.
In particular, the shared tmux server remains outside the restricted child
boundary. Synthetic receiver qualification and R14's native Codex end-to-end
opt-in test passed for one protected existing-session context return: file/hash
ACK, denied outside access, permission-menu/busy refusal, replay without new
input and unrelated-session survival. A separate saved-byte audit verified
both document hashes, the ACK and retained launch/ledger bindings. R12/R13's
verifier failures are retained. Prior-conversation resume, fresh-session/Claude
qualification and installed-service rollout remain separate.
Optional ticket fields are omitted for legacy tickets. Older deny-unknown-fields
binaries reject protected tickets and ledgers, so keep the matching build.

```bash
forager -p <profile> ondesk return preview \
  --package /absolute/project/morning-package.md --session <exact-session-id> \
  --output /absolute/private/return-ticket.json
forager -p <profile> ondesk return send /absolute/private/return-ticket.json \
  --expected-preview-sha256 <preview_sha256> --confirm-input
forager -p <profile> ondesk return observe <request_id>
forager -p <profile> ondesk return status <request_id>
```

The commands emit JSON. Preview saves only an inert ticket and displays the
exact compact reference that would be entered. Keep the ticket outside the
project or in already ignored storage: send checks Git status against preview.
Review the JSON and use its exact `preview_sha256` within five minutes. Changed
package, plan, shift-review context, session identity, worktree or prompt blocks
new input. A running harness, permission question or selection menu is refused.
The reference stays within the 2,000-byte literal-input budget; the complete
package remains in its bound local file. This path does not deliver via Telegram
and does not create a session automatically.

Send requires `--confirm-input`, preserves a durable `send_intent` before input,
and queues at most one transport attempt for that request. The profile stores
`ondesk_return_deliveries.v1.json` and an adjacent lock as private protocol
receipts. Preserve these records across retries. A copied ticket or repeated
send returns its existing receipt without sending again. Interrupted or
uncertain attempts remain held; there is no automatic resend, reset or eviction.
`send_attempts=1` counts the reserved protocol attempt; a retained intent does
not prove that a paste occurred. Only `input_queued` reports transport success.
The ledger is bounded to 256 requests and 2 MiB. A full or invalid ledger requires
review rather than deletion to force progress. These are additional receipts;
existing plan, shift and review schemas are unchanged.

`input_queued` establishes transport progress only. The message asks the harness
to read the exact package and current plan, verify both hashes, report remaining
work and calculate an acknowledgement using their complete bytes and a nonce.
Observe rereads the artifacts and bound pane. A matching marker records
`agent_read_claim_observed`; it does not authorize task execution, plan writes,
user acceptance or knowledge promotion. Status is read-only and reports the
historical attempt and claim, not current session health or task progress.
Same-user tampering and semantic understanding are not proven by a screen marker.
Prompt checks and locks do not fence arbitrary editors, local keyboard input or
other terminal writers between the last check and paste.

This command is a source candidate. The currently selected private Telegram
release predates it. C4.1d native qualification passed in an owned disposable
Linux VM for both existing-session and fresh-session return input. Each mode
showed exact-file reads and a matching ACK in the Codex provider trace, native
permissions-menu and busy refusal, one reserved input, and replay without a
second send. The read-only sandbox probe denied writes. The model footer next
to Codex 0.158's exact empty-input placeholder is required as a readiness cue;
it is not account or provider health evidence. Earlier unsuccessful provider
attempts and pre-input guest startup failures remain preserved, and their root
causes are not established. The current selected release and global CLI remain
unchanged. The R5 optimized artifact and staged binary-only Telegram bundle
passed isolated qualification; explicit release selection remains a separate
gate in the owning
operator-owned continuity backlog. Preserve uncertain
attempts, do not resend automatically, and do not change host policy to force a
pass. A native read claim does not grant task execution, accepted truth or
product acceptance.

### Inert review decision proposals

`ondesk plan-review-proposal /absolute/plan.md --decisions decisions.json`
returns JSON containing `candidate_markdown`, `change_request`, and the full
decision input. Decisions require request/project/baseline identity, attribution
(`decided_by`, `decision_ref`), and `tasks` with `task_id`, `disposition`,
`evidence_refs`, `remaining_work`, and `unanswered_questions`. Dispositions use
`accepted`, `partially_accepted`, `rework`, or `deferred`. Partial acceptance and
rework require remaining work; full acceptance cannot conceal unanswered work.

Preserve the complete proposal for review, then submit its candidate/request
through `ondesk plan-apply` with explicit `--confirm-plan-write` when authorized.
Generating a proposal does not authenticate the named decision maker or grant
execution authority. C1 checks stale baselines and repeated request IDs and
preserves the proposal's optional `review_record` in its existing write journal.
That record includes attribution, evidence, remaining work and questions; apply
requires the candidate to implement those exact decisions. Requests without a
review record remain supported without backfilling. Populated review records
require the new binary. Remote authentication and automatic session context
refresh are still pending. Exact saved-report binding is available as the optional
`shift_review` object in `decisions.json`:

```json
{
  "path": "/absolute/night-review.json",
  "sha256": "<SHA256 of exact saved report bytes>",
  "shift_id": "<reviewed shift ID>",
  "manifest_sha256": "<reviewed shift manifest SHA256>"
}
```

Save the JSON from `forager -p <profile> offdesk shift review <shift-id> --json`
as the report, review it, and bind its exact bytes and execution identity.
Every decided task must occur once within that report's project. Without
explicit reconciliation, its preserved source hash must match the decision's
current plan baseline. Reports
without a verified preserved source cannot use this binding. If the plan has
changed since execution, reconcile it explicitly; this slice does not rebase
historical decisions onto changed goals or criteria.

When only narrative or JSON formatting changed, explicitly select the exact
preserved execution document and record why its unchanged tasks still apply:

```bash
forager ondesk plan-review-proposal /absolute/PLAN.md \
  --decisions decisions.json \
  --reconcile-baseline /absolute/execution-baseline.md \
  --reconciliation-reason "Reviewed narrative changes; all structured tasks unchanged"
```

The decision's `baseline_sha256` must be the full current document hash. The
execution document must hash to the report's verified preserved source. All
structured tasks, including goals, criteria, dependencies, review states and
evidence, must match between the two versions; changed tasks are refused.
The candidate preserves current narrative. The proposal records both hashes,
the exact execution document and the reason in `review_record.reconciliation`.
It remains inert until the existing confirmed `plan-apply`; changed current
bytes or a changed report still refuse the write. Journal/request byte budgets
remain unchanged, so large witnesses may be refused. No automatic merge occurs.

Morning return displays reconciliation hashes and reason with
`exact_record_source=owning_plan_journal`. It omits the embedded execution text
because return sanitization must not transform bytes presented as an exact
hash-bound witness. Use the saved write request/journal for the exact record,
not the display summary. Existing records omit the optional reconciliation
field. Populated records require the matching new binary; older readers reject
them, and existing journals are not rewritten.

Proposal generation and a new confirmed plan write both recheck the report.
Changed, missing, linked, oversized or misbound artifacts are refused. A report
change after the journal is prepared leaves the write pending; preserve the
journal and restore the reviewed artifact before retrying the same request.
A completed request replay returns the journaled result without rereading the
report or rewriting a later plan. Recovery when the exact candidate is already
present also completes only the journal, without rereading the report or writing
the plan again. Its full reference and decisions remain in
the journal, and changed references cannot reuse that request ID.

These checks bind an explicit local artifact, not its producer's authenticity,
the latest runtime state or the truth of reported changes/checks. Keep the saved
report as evidence: the journal stores its reference, not its full contents.
The report remains review-pending and grants no plan-write authority; explicit
operator confirmation is still required. Ordinary local review requests may
omit `shift_review`; they do not claim overnight-report binding. Existing absent
fields are not rewritten. Populated bindings require the new binary; old
binaries cannot read those new journal entries.

### Operating contract

This is the operating contract for both an existing harness session left open
overnight and a fresh session. It does not claim that automatic session
refresh, project-orchestrator messaging, TODO synchronization, or an approval
UI is implemented. Perform the checks explicitly where automation is absent.

### Roles and durable context

The overall Offdesk orchestrator coordinates project selection, shared budgets,
resources, and the run summary. A project orchestrator explains that project's
decisions, results, dependencies, and uncertainties from durable records.
These are logical roles, not a requirement to keep multiple model sessions
alive. Neither role replaces runtime approval checks or user acceptance.

If a project orchestrator is unavailable, inspect the preserved plan, execution
receipts, and task reports and distinguish recorded evidence from inference.
Leave unanswered questions explicit. Consulting an orchestrator does not grant
permission to spawn agents, rerun experiments, or make new changes.

### Resume sequence

1. Resolve the project, authoritative Ondesk plan, task IDs, current plan
   version, branch/worktree, local uncommitted changes, and active owners.
   Compare them with the session's last known state. Reuse the old session only
   when its execution context is suitable; preserving a conversation is less
   important than restoring the correct task context.
2. Read relevant Offdesk return reports and evidence. Distinguish reported
   execution outcomes from accepted task completion. Surface pending review
   without silently changing the Ondesk baseline to match overnight claims.
3. Discuss the results with the user, consulting the project orchestrator or
   durable records as needed. Ask why an approach was chosen, what changed,
   which checks actually ran, what remains unverified, and how results affect
   today's goal. Read direct evidence when a summary is insufficient.
4. Produce a bounded agreement record with task ID, baseline version,
   disposition, evidence references, unresolved questions, proposed TODO
   changes, and the user's decision or applicable prior authority. Distinguish
   accept, partial acceptance, rework, and defer. Do not describe agent-only
   agreement as user acceptance.
5. Recheck the baseline before applying authorized TODO changes. If another
   writer changed it, reconcile rather than overwriting it. Link accepted
   results to existing tasks; create new tasks only for genuinely remaining
   work. Code integration, canonical documentation changes, and wiki promotion
   retain their own review and authorization boundaries.
6. Reread the updated plan and confirm the next owned task before execution.
   Other sessions should learn of the new plan version at a safe interaction
   boundary and refresh before their next mutation. Do not force input into a
   running terminal or assume all sessions have acknowledged the change.

### Report and document discipline

Use an existing task record for the agreement where possible, rather than
creating a new Markdown file for each exchange. A task closeout report must be
understandable without reading all scratch notes: include goal and scope,
changes, verified findings, direct evidence, applicable versions/conditions,
limitations, unresolved work, and proposed updates to the owning documents.
Prefer source code, test receipts, data, and original references over citing a
temporary note's conclusion. Preserve provenance and irreplaceable evidence.

Task closure, report review, and canonical acceptance are different states.
A failed or incomplete effort can have a closeout report without its goal
being completed. Reviewed reports are starting points, not immunity from new
contradictory evidence. Working-note status never authorizes automatic deletion.

The shared-plan integration writer applies agreed changes; task agents submit
bounded results rather than independently rewriting shared TODOs or
`PROJECT_STATE.md`. Ownership claims must be checked against actual execution
state. Written guidance alone does not provide atomic claims or fencing.

## Starting A Project

Run initialization before trying to hand a project to Offdesk:

```bash
forager project init /path/to/project \
  --project-key <project> \
  --operation-target <module-or-path> \
  --include-git \
  --json
```

Read these first:

- `PROJECT_ONBOARDING.md`;
- `ONDESK_START_PACKAGE.md`;
- `MODULE_OPERATION_PREFLIGHT.json`;
- `OFFDESK_READY_CHECK.json`.

`OFFDESK_READY_CHECK.json` can say Ondesk is ready while Offdesk remains
blocked. That is expected. Offdesk needs a task-specific evidence bundle,
review artifact, module preflight, and runtime approval.

## Ondesk To Offdesk

Use Ondesk records to preserve operator intent before leaving the desk:

```bash
forager ondesk note --project-key <project> --mode planning \
  --text "Next Offdesk task should inspect X and avoid Y."
forager ondesk prompt-package --project-key <project>
```

Then prepare the Offdesk task. The exact producer can be a CLI flow or a
project-specific helper, but the contract is the same for every project:

- write a deterministic evidence bundle;
- review the exact prepared manifest;
- point to a module operation preflight artifact;
- generate a human launch review packet;
- enqueue only after the operator understands the packet.

The launch packet should answer:

- What project and module scope will be touched?
- What command will run, from which workdir, with which runner?
- Which evidence and review artifacts were used?
- What blockers exist?
- Which approval is still required?
- Which actions remain forbidden?

## Launch And Monitor

Hosted harness prompt packets can name first reads and a task-specific read
scope, but local tmux runners do not confine filesystem reads to those paths.
The prompt packet reports `read_scope_enforced=false`. If a task requires strict
source isolation, use a separately verified read-confining host policy before
launch. Treat an agent's `evidence_read` list as a claim to audit against its
tool trace, not proof that no other file was opened.

For bounded `local-background` tasks, source now supports an explicit
`shift prepare --read-policy linux-landlock-workdir-and-runtime-only` option.
First run `shift read-policy-probe --json`. It uses Linux Landlock to limit
ordinary filesystem reads to the exact task directory and listed runtime paths,
and denies ordinary writes outside that directory except explicit stdio and
`/dev/null`. Approval binds this policy and the root identity; failure to install
it prevents launch. It clears the child environment and uses workdir-local
`HOME` and `TMPDIR`. Stage necessary inputs explicitly. This does not protect
local tmux or return sessions and does not restrict network, IPC, metadata,
preopened descriptors, hardlink aliases or nested mounts. The complete task
directory is visible. See the [read-policy implementation](https://github.com/kimyoungjin06/forager-cli/blob/main/src/offdesk/read_policy.rs)
for the exact runtime exceptions and compatibility boundary. Installed services
have not been changed by source qualification.

The separate `bound_shift_landlock_real_codex` qualification now passed one
real Codex 0.160.1 / gpt-6.1-sol background task through that adapter. It
observed one inspection command, matched three input hashes and denied-access
checks, verified supervisor exit 0 and one outcome, and preserved review-pending
status without a second dispatch. The owned shift was disarmed and temporary
auth was removed. The test requires `FORAGER_LANDLOCK_CODEX_TRIAL=1` plus explicit
`FORAGER_LANDLOCK_CODEX_EVIDENCE`, `FORAGER_LANDLOCK_AUTH_FILE`,
`FORAGER_LANDLOCK_CODEX_BINARY`, and `FORAGER_LANDLOCK_CODEX_MODEL` inputs.
Its driver stages the locally preserved R9 summary/policy evidence; the normal
test suite ignores this external-model case. This is a bounded background
reading/reporting qualification. It does not qualify native return confinement,
arbitrary task correctness or scientific acceptance. Staged authentication and
runtime files are visible inside the approved workdir/runtime boundary.

The opt-in `ondesk_return_container_tui_reads_context_without_task_authority`
test exercises native Codex return delivery inside an owned disposable
container. Its wrapper uses a read-only root, dropped capabilities, no new
privileges, an unprivileged user, and exact bind mounts. A preflight checks
allowed reads, denied input writes, and denied reads of unmounted host research,
memory, and symlink targets. Codex uses full access inside this container; the
container supplies the host filesystem boundary. Runtime files, an explicit
authentication bridge, and evidence mounts remain visible. This is a test
wrapper, not an integrated Forager sandbox or proof that only packet files can
be read. The existing native VM test keeps its separate sandbox preflight.
Current Codex packaging needs both `codex` and the matching
`codex-code-mode-host`; keep its temporary account directory outside `/tmp` so
helper installation can proceed. Preserve failed delivery attempts and use a
new disposable fixture after correcting startup failures.

The normal runtime path is:

```bash
forager offdesk tick --limit 1 --json
forager offdesk pending --json
forager offdesk ok <approval-id> --json
forager offdesk tick --limit 1 --json
forager offdesk poll --json
```

For long Python workloads, prefer `local-tmux`. A healthy tmux-backed run should
be inspectable through:

- an Offdesk background ticket;
- a tmux session name while the process is alive;
- `heartbeat.json`;
- `progress.jsonl`;
- `offdesk-runner.log`;
- `result.json` after completion;
- `REPORT.md` and post-run review artifacts.

Do not report completion from a single status field. Use task state, poll
evidence, runner log, heartbeat/progress, and result-review evidence together.

## Offdesk To Ondesk

When runtime finishes, run closeout before resuming live work:

```bash
forager offdesk closeout --project-key <project> --task-id <task-id> --dry-run
```

Closeout is a review packet, not a cleanup executor. The expected morning path
is:

1. Inspect `result.json`, `REPORT.md`, and review artifacts.
2. Generate closeout.
3. Review `COMMERCIAL_REVIEW_PACKET.md`.
4. Record a `closeout-review` verdict.
5. Start the next harness from `RETURN_PACKAGE.md` or
   `forager ondesk prompt-package --project-key <project>`.

`RETURN_PACKAGE.md` includes focused documentation governance recommendations
from `project audit-docs` when the closeout workdir can be audited. This keeps
the Ondesk return surface action-oriented while leaving full audit inventories
in machine JSON.

Prefer task-scoped closeout for morning handoff when a specific Offdesk run is
being returned. Project-wide closeout is useful for backlog review, but it can
mix older debug, cancelled, and completed tasks into the same return package.
When runtime artifacts include `prepared_task.json` or `manifest.json`, closeout
uses their target repo field for documentation governance before falling back to
the runtime workdir.

For a current project-level governance view, add `--include-doc-audit` to
`forager ondesk prompt-package`. The prompt package will then identify the
governance source as `fresh_project_audit`, `latest_closeout_return_package`, or
`fresh_project_audit_unavailable` instead of leaving the next harness to infer
freshness from artifact paths. When a matching closeout is present, the fresh
audit uses the closeout workdir recorded in `closeout_plan.json` before falling
back to the current session or shell directory.

## Wiki Boundary

Offdesk can create wiki candidates or run-local trial entries, but it should
not silently promote canonical knowledge. Promotion belongs to Ondesk morning
review or an explicit operator-reviewed wiki command.

This keeps adaptive wiki useful without making it a hidden memory backend.

## Stop Conditions

Stop and inspect before continuing when:

- `LAUNCH_DRY_RUN.md` has blockers;
- the pending approval action is not `dispatch.runtime` for the expected task;
- the runner is `local-background` for a long Python workload;
- `offdesk poll` reports stale callback, stale heartbeat, or missing result;
- closeout proposes file movement or deletion;
- a wiki change would promote candidate knowledge without review.

For the concrete validation flow, see
[`Offdesk Runtime Smoke`](offdesk-runtime-smoke.md). For the realistic long-run
validation sequence, see
[`Offdesk Long-Run Validation`](offdesk-long-run-validation.md).
For the current baseline and remaining work queue, see
[`Offdesk Operation Status`](../offdesk-operation-status.md).

### Explicit historical journal boundaries

An existing journal can contain a historical discontinuity even when its current
user decision and following entries are intact. Ordinary `offdesk review-link`
continues to refuse every discontinuous chain. Never delete prior entries,
fabricate a missing transaction or call a changed task plan narrative-only drift.

A separately agreed `historical_boundary` path/SHA256 may be supplied in the
review-link request. The boundary uses `offdesk_historical_journal_boundary.v1`
and pins the complete request with that optional reference omitted, the actual
profile identity, exact journal length, one gap and its two surviving document
versions, and the continuous suffix. A separately hash-bound agreement repeats
the request/profile/range/gap and records the operator and permitted effect
`link_current_decision_with_unknown_historical_transition`. Request digests use
sorted JSON objects and compact UTF-8 serialization after typed deserialization.
These are explicit local attribution records, not authenticated signatures.

The full journal and current plan/result/decision remain hash-bound by the
request. Every entry must still have a valid scope, unique ID and committed
state; all other transitions must be continuous. Changed artifacts, another
gap, a decision before the boundary, a superseding decision or missing agreement
refuse. Endpoint documents may contain different task sets. The preview and
stored link explicitly describe their historical transition as unknown. The
boundary and its agreement/endpoints are rechecked on return and later review;
evidence drift reopens review without changing the historical result.

Only an explicitly confirmed boundary link selects `offdesk_task_queue.v3`.
Ordinary links retain v2, existing queues are not rewritten at startup, and v3
is retained on later saves. Migration 9 registers this opt-in capability without
modifying records. Older consumers refuse v3, so use the qualified matching
binary for this private profile. This does not deploy a binary, grant execution,
accept scientific work, or upgrade a partial disposition to full acceptance.

### Bounded evidence hashing

Current result and failure envelopes remain limited to 64 KiB. Referenced
check/change artifacts are hashed as streams, up to 16 MiB per file and 32 MiB
total per observation, matching the selected review producer and explicit
return-binding limits. At most 16 evidence entries are inspected. Bytes already
read consume the aggregate budget even when a later hash or identity check
fails. Reports larger than 64 KiB can therefore verify without loading their
whole contents into the envelope reader. This does not certify their claims.

Oversized or over-budget evidence remains unavailable; no prefix hash or silent
truncation is accepted. Root/path containment, no-follow traversal, regular-file
and single-link requirements, complete read and descriptor/path/root identity
rechecks still apply. Existing saved reports are immutable observations of their
original reader; use a separate current projection to observe corrected behavior.

### Explicitly acknowledged journal prefixes

Multiple historical discontinuities require an explicitly selected
`historical_prefix` path/SHA256 on the review-link request. It is exclusive with
the single `historical_boundary` selection. Default full-chain refusal and
existing single-boundary behavior remain unchanged.

`offdesk_historical_journal_prefix.v1` pins the full current request with this
optional reference omitted, actual profile identity, the exact original journal
snapshot and entry count, and the pre-decision document. It lists every gap
inside that prefix and at its boundary to the new decision. A separate
`offdesk_historical_journal_prefix_agreement.v1` repeats the exact request,
profile, prefix count, snapshot/document hashes and complete gap list with
explicit local attribution and effect
`link_current_decision_with_unknown_historical_prefix`. These records acknowledge
unknown history; they neither manufacture missing transactions nor authenticate
past authorization or scientific conclusions.

The current journal must preserve every prefix entry exactly as structured data;
the original snapshot bytes remain independently hash-bound. Every entry still
requires valid scope, a unique ID and committed state. The strict suffix must
start with the selected new user decision. Replaying that decision against the
pinned pre-decision document must reproduce the current plan and exact change
request. Missing/extra gaps, a changed snapshot, mixed acknowledgment types,
uncommitted entries, new suffix gaps or another selected decision refuse.
All external artifacts are rechecked before returning or confirming a link.

Confirmed prefix links opt only that profile into `offdesk_task_queue.v4`;
existing ordinary/v3 links remain readable and v4 never silently downgrades.
Migration 10 registers this capability without rewriting queues at startup.
Use the matching qualified consumer for this profile; older v3 consumers refuse
v4. Ordinary task readback may register the private app's new capability version.
Global deployment, new execution, scientific acceptance and permission expansion
are separate from this historical review classification.

### Explicit narrative-only review reconciliation

An unrelated edit outside `forager-plan` still changes a document's full hash.
The strict review-link path refuses that drift by default. For an existing
`partially_accepted`, `rework` or `deferred` decision, an explicitly selected
`narrative_reconciliation` path/SHA256 can instead bind an approved document
snapshot and the current document separately. It does not support full acceptance.

The `offdesk_review_narrative_reconciliation.v1` artifact contains
`review_request_sha256`, actual `profile_identity`, an `approved_document`
path/SHA256, `current_document_sha256`, and a separately pinned `agreement`.
The snapshot hash must equal the request's original `owning_plan_sha256`.
That original version still must match the committed journal tail and latest
selected task decision. The current document must match its separate selected
hash. Their complete structured plans, including task order, IDs, goals, every
criterion, dependencies, states and evidence, must be equal. Missing, ambiguous
or changed task blocks refuse; similarity or selected-task-only equality is
insufficient. Both exact documents are preserved without a new TODO transaction.

The `offdesk_review_narrative_reconciliation_agreement.v1` repeats the request
digest, profile identity and both document hashes, with nonempty `decided_by`,
`decision_ref` and effect
`link_existing_nonaccepted_decision_across_exact_narrative_change`.
This digest omits `narrative_reconciliation`, `historical_boundary` and
`historical_prefix` reference fields to avoid a hash cycle. All other request
fields, including runtime/result/owner/journal/decision and closeout versions,
remain bound. A historical boundary or prefix still uses its own full request
digest with only that historical reference omitted; it therefore also binds
the narrative artifact. Existing history guards and explicit unknown gaps
remain intact.

The preview records both source versions, all-task structural hash/count and
local attribution. It rechecks the documents, artifact and agreement before
confirming. Further document edits, changed evidence/journals, or new applicable
closeouts reopen review and require another exact selection; this is not an
automatic waiver for future narrative edits. The current narrative is not
relabelled as the historical committed or executed document, and its scientific
claims are not accepted. No stage, dependency, accepted truth or execution is
unlocked by this administrative reconciliation.

Confirmed reconciliations opt only the selected profile into queue v5, preserved
on later saves. Migration 11 registers that capability without rewriting queues.
Earlier array/v2/v3/v4 profiles retain their existing encoding unless explicitly
selected. Older v4 consumers refuse v5 without rewriting it. Use the qualified
matching runtime for that private profile; global deployment remains separate.

### Closeout output scope and accepted administrative closure

Closeout review, decision and retirement records write only inside their selected
artifact directory. A declared `artifacts.return_package_markdown` must name that
directory's `RETURN_PACKAGE.md`; a copied plan's unrelated absolute destination
is refused before creating records. An omitted destination uses that same slot.
Explicit external artifact directories and ordinary relative selections remain
supported. Parent traversal, symbolic links in the selected path, nonregular or
multiply linked return files, and return documents over 1 MiB refuse. The writer
uses the selected raw path, with output redaction applied only for presentation.

The directory identity and return document version are checked again during
descriptor-relative writes. Return-document replacement is atomic and preserves
text outside the receipt section. The JSON records and return document are not
one filesystem transaction: a late I/O failure may leave new record files in the
selected directory. Preserve those failed records and inspect the error before
continuing; do not infer successful publication from an allocated filename.

An administrative `approved_with_followups` receipt does not accept a result.
Readers derive completed acceptance only when the current owning decision is
independently `accepted`, its explicit result assessment is `evidence_reviewed`,
and its valid review link binds the exact administrative review being projected.
All referenced tasks must match their project, request and task identities.
The review must be newer than their execution updates. Open decisions, missing
evidence, required reads and unsafe operations must be empty, staleness zero,
retention resolved and wiki promotion free of pending decisions. A resolved
retention claim also requires its recorded preservation decision.

Status, shift/control-loop summaries, review and prompt surfaces, recovery
actions and retirement guards use the same derived rule. The raw administrative
receipt remains unchanged. Review-surface `closeout.recorded_receipt_status`
preserves its original status while `acceptance_basis` explains the combined
decision. An unbound or changed receipt, invalid result, later owner/evidence
drift, or unresolved gate keeps review pending. Existing partial/rework/deferred
closure remains nonaccepted. This derived classification adds no stored queue
schema, migration, deployment permission or scientific acceptance.
