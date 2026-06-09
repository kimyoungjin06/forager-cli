# TwinPaper Offdesk Runtime Smoke

This runbook validates the current TwinPaper Offdesk launch path without
starting a long overnight campaign. It checks the full operator path:

```text
prepare -> enqueue -> tick creates dispatch.runtime approval
  -> approve -> tick launches local-tmux
  -> poll observes terminal result artifacts
```

Use it after changes to the TwinPaper prepare script, module preflight wiring,
runtime approvals, background polling, or documentation that describes the
launch path.

## Preconditions

- The repo is on a clean branch.
- `target/debug/forager` has been built.
- The `twinpaper-adaptive-debug` profile is the intended test profile.
- The target TwinPaper repo exists at
  `/home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper`.
- The Ollama-compatible endpoint has `qwen3-coder-next:latest` available.
- A clean role-gate artifact exists under
  `target/offdesk-role-llm-episode-harness/`.
- A matching project initialization exists for `project_key=twinpaper`, or can
  be regenerated.

Check the model endpoint:

```bash
curl -sS http://<gpu-server>:11434/api/tags
```

Check pending approvals before starting:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk pending --json
```

## Refresh Project Initialization

Run this if `--module-preflight-artifact latest` cannot find a matching
`MODULE_OPERATION_PREFLIGHT.json`:

```bash
target/debug/forager -p twinpaper-adaptive-debug project init \
  /home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper \
  --project-key twinpaper \
  --operation-target modules/03_regspec_machine \
  --json
```

Expected properties:

- `read_only_project_state=true`;
- `operation_target_count=1`;
- `ready_for_ondesk_start=true`;
- `ready_for_offdesk_runtime=false` is acceptable, because runtime still needs
  task-specific preflight and approval.

## No-Enqueue Dry Run

First prepare without `--enqueue` when validating the operator-facing packet:

```bash
scripts/prepare_twinpaper_offdesk_task.py \
  --out-root target/offdesk-launch-dry-run-smoke \
  --duration-minutes 0.1 \
  --max-iterations 1 \
  --model qwen3-coder-next:latest \
  --base-url http://<gpu-server>:11434 \
  --role-gate-result latest \
  --module-preflight-artifact latest \
  --review-artifact generate
```

Inspect:

```bash
cat target/offdesk-launch-dry-run-smoke/<timestamp>/LAUNCH_DRY_RUN.md
cat target/offdesk-launch-dry-run-smoke/<timestamp>/preflight.json
cat target/offdesk-launch-dry-run-smoke/<timestamp>/workload_review/results.json
```

Expected dry-run state:

- `enqueued=false`;
- `ready_for_enqueue=true`;
- `enqueue_allowed=true`;
- `blocking_reasons=[]`;
- `LAUNCH_DRY_RUN.md` says runtime dispatch still requires the normal
  `dispatch.runtime` approval path;
- `schedule_target_at` is rendered as JSON-style `null`, not Python `None`;
- no task with that smoke `task_id` appears in `offdesk tasks --json`.

## Enqueue Runtime Smoke

Only after the dry-run packet is readable, run the short runtime smoke:

```bash
scripts/prepare_twinpaper_offdesk_task.py \
  --out-root target/offdesk-runtime-smoke \
  --duration-minutes 0.1 \
  --max-iterations 1 \
  --model qwen3-coder-next:latest \
  --base-url http://<gpu-server>:11434 \
  --role-gate-result latest \
  --module-preflight-artifact latest \
  --review-artifact generate \
  --enqueue
```

Expected enqueue state:

- `enqueued=true`;
- task status is `queued`;
- runner is `local_tmux`;
- `review_artifact.decisions=["needs_approval"]`;
- `preflight.ready_for_enqueue=true`;
- `preflight.enqueue_allowed=true`.

## Approval Gate

Run the first tick:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk tick --limit 1 --json
```

Expected:

- `launched=0`;
- `pending_approval=1`;
- updated task id is the runtime smoke task.

Confirm the pending approval:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk pending --json
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
target/debug/forager -p twinpaper-adaptive-debug offdesk ok <approval-id> --json
```

Then launch:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk tick --limit 1 --json
```

Expected:

- `launched=1`;
- `pending_approval=0`;
- task receives a background ticket.

## Poll And Inspect

Poll until the short smoke completes:

```bash
target/debug/forager -p twinpaper-adaptive-debug offdesk poll --json
```

Inspect the workload directory:

```bash
find target/offdesk-runtime-smoke/<timestamp> -maxdepth 3 -type f | sort
tail -80 target/offdesk-runtime-smoke/<timestamp>/offdesk-runner.log
```

Required artifacts:

- `LAUNCH_DRY_RUN.md`;
- `prepared_task.json`;
- `preflight.json`;
- `manifest.json`;
- `heartbeat.json`;
- `progress.jsonl`;
- `result.json`;
- `REPORT.md`;
- `result_review/results.json`;
- `result_review/RESULT_REVIEW.md`;
- `offdesk-runner.log`;
- `responses/*.txt`.

Expected terminal state:

- task status is `completed`;
- `last_gate_status=proceed`;
- `mode_verdict=evidence_ready`;
- `mode_risk=operator_review_required`;
- `pending` returns `[]`;
- tmux session for the background ticket is gone after completion;
- `result.json.summary.total=1`;
- `result.json.summary.passed=1`;
- `result.json.summary.failed=0`;
- `result_review.results.json.passed=true`.

## Verified Local Smoke

The 2026-05-22 local smoke validated this path:

- task: `twinpaper-autonomy-20260522T071200Z`;
- approval: `approval_23b5ae16-fa7a-4579-adec-ffa4d949ded0`;
- background ticket: `bg_81e0dd2e-1164-4473-8815-e4df04bea179`;
- runtime: `11.81s`;
- result: `1 total / 1 passed / 0 failed`;
- post-run review: clean;
- remaining pending approvals: `[]`;
- final repo state: clean.

The smoke proves that the launch path works. It does not prove that a longer
overnight run will produce useful research output. Longer campaigns still need
Council review, closeout, and morning Ondesk inspection.

## Troubleshooting

| Symptom | Likely cause | First response |
| --- | --- | --- |
| `latest` module preflight is missing | No profile-local project initialization | Re-run `forager project init` with `--operation-target modules/03_regspec_machine`. |
| `ready_for_enqueue=false` | Role gate, workload review, evidence review, or module preflight failed | Inspect `preflight.json` and do not use `FORAGER_ALLOW_PREFLIGHT_BLOCKERS` unless deliberately testing a blocked path. |
| First tick launches immediately | Approval gate was bypassed or task capability is wrong | Stop and inspect task capability; runtime tasks should use `dispatch.runtime`. |
| Pending approval action is not `dispatch.runtime` | Wrong task or stale approval | Do not approve; filter by `task_id` and inspect `pending --json`. |
| `local-background` is selected for a long run | Wrong runner | Reprepare with `local-tmux`; do not rely on background callbacks for long Python work. |
| Poll shows stale callback | Runtime restarted or callback was lost | Inspect runner log, heartbeat, result artifact, and background ticket before retrying. |
| Result exists but review is missing | Closeout/review stage did not run | Run or inspect deterministic result review before reporting success. |

## Safety Boundary

This smoke is allowed to write only under the selected output directory and the
profile-local Offdesk state. It must not mutate the TwinPaper repository,
delete files, move artifacts, restart services, change mounts, modify provider
routing, or promote wiki entries.
