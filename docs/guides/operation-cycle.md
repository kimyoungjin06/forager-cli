# Operation Cycle Guide

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

Then prepare the Offdesk task. For TwinPaper this is the dedicated prepare
script, but the general rule is the same for other projects:

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
forager offdesk closeout --project-key <project> --dry-run
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

For the concrete TwinPaper validation flow, see
[`TwinPaper Offdesk Runtime Smoke`](twinpaper-offdesk-runtime-smoke.md). For
the realistic long-run validation sequence, see
[`TwinPaper Offdesk Long-Run Validation`](twinpaper-offdesk-long-run-validation.md).
For the current baseline and remaining work queue, see
[`Offdesk Operation Status`](../offdesk-operation-status.md).
