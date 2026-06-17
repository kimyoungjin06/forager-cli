# Offdesk Long-Run Validation

This runbook is the next step after the short
[`Offdesk Runtime Smoke`](offdesk-runtime-smoke.md). The smoke proves the
launch path. A longer run should prove whether the operating cycle produces
useful, reviewable work.

The validation target is not "the process exited." The target is:

- review decisions are understandable and can stop or hand off work;
- runtime evidence is live and inspectable while the run is active;
- result quality is separable from runtime mechanics;
- closeout produces a review packet without moving or deleting files;
- a fresh Ondesk harness can resume from artifacts;
- adaptive wiki candidates remain reviewable and are not promoted silently.

## Prepare

Build the local binary first:

```bash
cargo build
```

Prepare a dry-run package with the chosen long-run workload. Use
`scripts/prepare_offdesk_workload.py` for generic bounded commands, or a
project-specific producer when the workload needs custom evidence bundles,
module profiles, or post-run reviewers. The prepare step should write:

```text
prepared_task.json
preflight.json
LAUNCH_DRY_RUN.md
LONG_RUN_VALIDATION.md
offdesk_monitor_commands.md
offdesk_enqueue_command.sh
run_workload.sh
```

If the run should ask for a human continuation decision through a remote
surface, use `approval_brief.v1` as the shared card contract. The primary card
should show the recommendation, main reason, approval question, and approval
scope. The detail card should show evidence, choice impact, and examples of
valid natural-language replies.

Remote continuation decisions control only whether the workload continues to
the next episode. They do not approve file changes, cleanup, provider
retargeting, wiki promotion, or any system mutation.

For the shared approval-card contract used by relays and producers, see
[`Approval Briefs`](approval-brief.md).

Read `LONG_RUN_VALIDATION.md` before enqueueing. It is the operator checklist
for approval, monitoring, closeout, Ondesk return, and wiki review.

## Pre-Launch Gate

Continue only when:

- `preflight.json` has `ready_for_enqueue=true`;
- `LAUNCH_DRY_RUN.md` has no blockers;
- the runner is appropriate for live inspection, usually `local-tmux`;
- the pending workload scope matches the selected project and task;
- role/evidence gates are intentional and documented in the packet;
- any remote decision relay is explicitly marked ready.

Stop if the prepare packet depends on a bypass flag for a real long run.

## Enqueue And Approve

Use the generated script so the preflight guard stays attached:

```bash
bash <workload-output>/offdesk_enqueue_command.sh
target/debug/forager -p <profile> offdesk tick --limit 1 --json
target/debug/forager -p <profile> offdesk pending --json
```

Approve only the row that matches the packet:

- `action=dispatch.runtime`;
- `risk_level=runtime_mutation`;
- `scope=once`;
- task id matches `prepared_task.json`;
- preview matches the launch dry run.

This approval does not authorize provider fallback, cleanup, closeout file
operations, or wiki promotion.

## Monitor

Poll and inspect live evidence together:

```bash
target/debug/forager -p <profile> offdesk poll --json
target/debug/forager -p <profile> offdesk tasks --project-key <project-key> --task-id <task-id> --json
tail -80 <workload-output>/offdesk-runner.log
cat <workload-output>/heartbeat.json
tail -40 <workload-output>/progress.jsonl
```

Do not report completion from a single status field. Use task state, poll
evidence, tmux state, heartbeat/progress, log tail, result sidecars, reports,
and post-run review together.

Stop and inspect when:

- poll reports stale callback, stale heartbeat, or missing result;
- a review decision says stop, revise, or hand off;
- the remote decision relay times out or records an unclear reply;
- task state and background-run state disagree;
- provider/model is retargeted without a separate provider fallback approval;
- the workload attempts file cleanup, deletion, package changes, service
  changes, process interruption, network changes, storage changes, or other
  system mutation.

## Completion Review

After result artifacts exist, inspect:

```bash
cat <workload-output>/result.json
cat <workload-output>/REPORT.md
cat <workload-output>/result_review/results.json
cat <workload-output>/result_review/RESULT_REVIEW.md
```

The review should separate:

- runtime passed or failed;
- output quality;
- decision usefulness;
- direction-control failures;
- missing evidence;
- operator judgement about whether the result should influence the project.

## Closeout

Run closeout before returning to live work:

```bash
target/debug/forager -p <profile> offdesk closeout \
  --project-key <project-key> \
  --task-id <task-id> \
  --dry-run
```

Closeout must remain a dry-run planner. It may propose keep/archive/dispose
classes, but it must not move, delete, or archive files. Any real file
operation needs a separate reviewed approval path.

## Ondesk Return

Start the next harness from the return package:

```bash
target/debug/forager -p <profile> ondesk prompt-package \
  --project-key <project-key> \
  --include-doc-audit
```

The package should include the latest project initialization summary, latest
closeout return package, review verdict when present, documentation governance
source, and first-read instructions for the fresh harness.

## Wiki Review

Review generated knowledge as candidates:

```bash
target/debug/forager -p <profile> offdesk wiki candidates \
  --project-key <project-key> \
  --json
target/debug/forager -p <profile> offdesk wiki review \
  --active-only \
  --json
target/debug/forager -p <profile> offdesk wiki review-after-report \
  --project-key <project-key> \
  --artifact-kind report \
  --agent-mode critique \
  --json
target/debug/forager -p <profile> offdesk wiki runtime-policy-ack-report \
  --project-key <project-key> \
  --artifact-kind report \
  --agent-mode critique \
  --json
```

Promote only reviewed entries with explicit evidence refs, correct scope, and
no hidden changes to approval, provider, command, or workdir behavior.

## Acceptance Criteria

The long run is validated only when:

- `LONG_RUN_VALIDATION.md` was read before enqueue;
- dispatch approval matched the prepared packet exactly;
- live monitoring had heartbeat, progress, log, and poll evidence;
- decision records were present and understandable;
- result and review artifacts exist;
- closeout generated a dry-run packet without applying file operations;
- Ondesk prompt package can tell a fresh harness what to read first;
- wiki candidates remain reviewable and no candidate is promoted without
  operator review.
