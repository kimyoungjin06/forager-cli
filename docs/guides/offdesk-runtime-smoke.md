# Offdesk Runtime Smoke

This runbook validates a short Offdesk launch path without starting a long
overnight campaign. It checks the operator path:

```text
prepare -> enqueue -> tick creates dispatch.runtime approval
  -> approve -> tick launches supervised runner
  -> poll observes terminal result artifacts
```

Use it after changes to runtime approvals, background polling, task queueing,
project initialization, closeout, or documentation that describes the launch
path.

## Preconditions

- The repo is on a clean branch.
- `target/debug/forager` has been built.
- A test profile is selected.
- The target project exists locally and has a project initialization packet.
- Any model endpoint required by the workload is reachable.
- The workload command is bounded and writes result artifacts under a known
  output directory.

Check pending approvals before starting:

```bash
target/debug/forager -p <profile> offdesk pending --json
```

## Refresh Project Initialization

Run this if the workload needs a fresh project packet:

```bash
target/debug/forager -p <profile> project init \
  /path/to/project \
  --project-key <project-key> \
  --include-git \
  --json
```

Expected properties:

- `read_only_project_state=true`;
- project markers and first-read sources are visible;
- runtime readiness is not implied by project initialization alone.

## Prepare A No-Enqueue Dry Run

Create a launch packet without enqueueing the task first. The concrete prepare
command depends on the workload. The output should include:

```text
prepared_task.json
preflight.json
LAUNCH_DRY_RUN.md
offdesk_enqueue_command.sh
run_workload.sh
```

Expected dry-run state:

- `enqueued=false`;
- blockers are explicit;
- the launch packet says runtime dispatch still requires normal
  `dispatch.runtime` approval;
- no task with the smoke `task_id` appears in `offdesk tasks --json`.

## Enqueue Runtime Smoke

Only after the dry-run packet is readable, enqueue the short smoke through the
prepared command or generated enqueue script.

Expected enqueue state:

- task status is `queued`;
- runner is the expected supervised runner;
- preflight says the task is ready for enqueue;
- review artifacts identify any operator approval still needed.

## Approval Gate

Run the first tick:

```bash
target/debug/forager -p <profile> offdesk tick --limit 1 --json
```

Expected:

- `launched=0`;
- a pending approval exists for the smoke task.

Confirm the pending approval:

```bash
target/debug/forager -p <profile> offdesk pending --json
```

The approval must be scoped to the smoke task and must have:

- `action=dispatch.runtime`;
- `risk_level=runtime_mutation`;
- `approval_mode=operator_required`;
- `scope=once`.

Do not approve if the action, task id, provider/model, or preview does not
match the launch packet.

## Launch

Approve the exact approval id:

```bash
target/debug/forager -p <profile> offdesk ok <approval-id> --json
```

Then launch:

```bash
target/debug/forager -p <profile> offdesk tick --limit 1 --json
```

Expected:

- `launched=1`;
- `pending_approval=0`;
- task receives a background ticket.

## Poll And Inspect

Poll until the short smoke completes:

```bash
target/debug/forager -p <profile> offdesk poll --json
```

Inspect the workload directory:

```bash
find <workload-output> -maxdepth 3 -type f | sort
tail -80 <workload-output>/offdesk-runner.log
```

Required artifact classes:

- launch dry-run packet;
- prepared task JSON;
- preflight JSON;
- manifest or command packet;
- heartbeat;
- progress log when applicable;
- result JSON or equivalent sidecar;
- human-readable report when applicable;
- runner log.

Expected terminal state:

- task status is `completed` or a clear failure state;
- `last_gate_status=proceed`;
- pending approvals do not remain for the completed smoke;
- the supervised runner can be reconciled by `offdesk poll`;
- output quality remains subject to closeout/review.

## Troubleshooting

| Symptom | Likely cause | First response |
| --- | --- | --- |
| Preflight artifact is missing | No matching project initialization or prepare output | Re-run `forager project init` and the prepare dry run. |
| `ready_for_enqueue=false` | Role gate, workload review, evidence review, or preflight failed | Inspect `preflight.json` and do not bypass blockers unless testing a blocked path. |
| First tick launches immediately | Approval gate was bypassed or task capability is wrong | Stop and inspect task capability; runtime tasks should use `dispatch.runtime`. |
| Pending approval action is not `dispatch.runtime` | Wrong task or stale approval | Do not approve; filter by `task_id` and inspect `pending --json`. |
| `local-background` is selected for a long run | Wrong runner for live inspection | Reprepare with `local-tmux` when live inspection is needed. |
| Poll shows stale callback | Runtime restarted or callback was lost | Inspect runner log, heartbeat, result artifact, and background ticket before retrying. |
| Result exists but review is missing | Closeout/review stage did not run | Run closeout and deterministic review before reporting accepted success. |

## Safety Boundary

This smoke is allowed to write only under the selected output directory and the
profile-local Offdesk state. It must not mutate unrelated project files,
delete artifacts, restart services, change mounts, modify provider routing, or
promote wiki entries.
