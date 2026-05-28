# TwinPaper Offdesk Long-Run Validation

This runbook is the next step after the short
[`TwinPaper Offdesk Runtime Smoke`](twinpaper-offdesk-runtime-smoke.md). The
smoke proves the launch path. A longer run should prove whether the operating
cycle produces useful, reviewable work.

The validation target is not "the process exited." The target is:

- Council decisions are understandable and can stop or hand off work;
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

Prepare a dry-run package with Council enabled. Use `prompt-package`, `mock`, or
`command` depending on which Council path you are validating:

```bash
scripts/prepare_twinpaper_offdesk_task.py \
  --out-root target/offdesk-long-run-validation \
  --duration-minutes 30 \
  --max-iterations 12 \
  --model qwen3-coder-next:latest \
  --base-url http://172.16.0.37:11434 \
  --role-gate-result latest \
  --module-preflight-artifact latest \
  --review-artifact generate \
  --council-mode prompt-package
```

When the run should ask for a human continuation decision through the existing
Telegram bot, add the operator decision relay. Telegram messages are rendered
from `approval_brief.v1`: the primary card shows the recommendation, the main
reason, the approval question, and the approval scope; the detail card shows
evidence, choice impact, and natural-language reply examples. Owner quick
replies such as `좋아`, `진행`, `계속`, `수정`, `보류`, or `중단` are accepted
for the latest prompted decision. `수정` and `보류` may ask for a free-form
explanation after the button press.

```bash
scripts/prepare_twinpaper_offdesk_task.py \
  --out-root target/offdesk-long-run-validation \
  --run-until-kst 09:00 \
  --max-iterations 24 \
  --model qwen3-coder-next:latest \
  --base-url http://172.16.0.37:11434 \
  --role-gate-result latest \
  --module-preflight-artifact latest \
  --review-artifact generate \
  --council-mode prompt-package \
  --council-operator-decision-relay telegram \
  --telegram-decision-timeout-sec 28800
```

The Telegram decision controls only whether the workload continues to the next
episode. It does not approve file changes, cleanup, provider retargeting, wiki
promotion, or any system mutation.

For the shared approval-card contract used by the relay and producers, see
[`Approval Briefs`](approval-brief.md).

Every prepare run writes:

```text
prepared_task.json
preflight.json
LAUNCH_DRY_RUN.md
LONG_RUN_VALIDATION.md
offdesk_monitor_commands.md
offdesk_enqueue_command.sh
run_workload.sh
```

Read `LONG_RUN_VALIDATION.md` before enqueueing. It is the operator checklist
for approval, monitoring, closeout, Ondesk return, and wiki review.

## Pre-Launch Gate

Continue only when:

- `preflight.json` has `ready_for_enqueue=true`;
- `LAUNCH_DRY_RUN.md` has no blockers;
- the runner is `local-tmux`;
- the pending workload scope matches `project_key=twinpaper` and
  `module03_regspec_machine`;
- the role gate has `quality_gate.ready_for_long_workload=true`;
- the evidence review decision is `sufficient`;
- Council configuration is intentional and documented in the packet.
- if Telegram relay is enabled, `preflight.json` shows
  `operator_decision_relay.ready=true`.

Stop if the prepare packet depends on `--allow-preflight-blockers` for a real
long run.

## Enqueue And Approve

Use the generated script so the preflight guard stays attached:

```bash
bash target/offdesk-long-run-validation/<timestamp>/offdesk_enqueue_command.sh
target/debug/forager -p twinpaper-adaptive-debug offdesk tick --limit 1 --json
target/debug/forager -p twinpaper-adaptive-debug offdesk pending --json
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
target/debug/forager -p twinpaper-adaptive-debug offdesk poll --json
target/debug/forager -p twinpaper-adaptive-debug offdesk tasks --project-key twinpaper --task-id <task_id> --json
tail -80 target/offdesk-long-run-validation/<timestamp>/offdesk-runner.log
cat target/offdesk-long-run-validation/<timestamp>/heartbeat.json
tail -40 target/offdesk-long-run-validation/<timestamp>/progress.jsonl
tail -40 target/offdesk-long-run-validation/<timestamp>/council_progress.jsonl
```

Do not report completion from a single status field. Use task state, poll
evidence, tmux state, heartbeat/progress, log tail, `result.json`, `REPORT.md`,
and post-run review together.

Stop and inspect when:

- poll reports stale callback, stale heartbeat, or missing result;
- Council returns a non-`continue` decision without an accepted Telegram
  operator decision;
- the Telegram relay times out or records an unclear reply;
- task state and background-run state disagree;
- provider/model is retargeted without a separate provider fallback approval;
- the workload attempts file cleanup, deletion, package changes, service
  changes, process interruption, network changes, storage changes, or other
  system mutation.

## Completion Review

After `result.json` exists, inspect:

```bash
cat target/offdesk-long-run-validation/<timestamp>/result.json
cat target/offdesk-long-run-validation/<timestamp>/REPORT.md
cat target/offdesk-long-run-validation/<timestamp>/result_review/results.json
cat target/offdesk-long-run-validation/<timestamp>/result_review/RESULT_REVIEW.md
```

The review should separate:

- runtime passed or failed;
- output quality;
- Council usefulness;
- direction-control failures;
- missing evidence;
- operator judgement about whether the result should influence the project.

## Closeout

Run closeout before returning to live work:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk closeout \
  --project-key twinpaper \
  --dry-run
```

Closeout must remain a dry-run planner. It may propose keep/archive/delete
classes, but it must not move, delete, or archive files. Any real file
operation needs a separate reviewed approval path.

## Ondesk Return

Start the next harness from the return package:

```bash
target/debug/forager -p twinpaper-adaptive-debug ondesk prompt-package \
  --project-key twinpaper
```

The package should include the latest project initialization summary, latest
closeout return package, review verdict when present, and first-read
instructions for the fresh harness.

## Wiki Review

Review generated knowledge as candidates:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk wiki candidates \
  --project-key twinpaper \
  --json
target/debug/forager -p twinpaper-adaptive-debug offdesk wiki review \
  --active-only \
  --json
target/debug/forager -p twinpaper-adaptive-debug offdesk wiki review-after-report \
  --project-key twinpaper \
  --artifact-kind report \
  --agent-mode critique \
  --json
target/debug/forager -p twinpaper-adaptive-debug offdesk wiki runtime-policy-ack-report \
  --project-key twinpaper \
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
- Council records were present and understandable;
- `result.json`, `REPORT.md`, and result review artifacts exist;
- closeout generated a dry-run packet without applying file operations;
- Ondesk prompt package can tell a fresh harness what to read first;
- wiki candidates remain reviewable and no candidate is promoted without
  operator review.
