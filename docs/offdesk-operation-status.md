# Offdesk Operation Status

This page records the current Forager Offdesk operating baseline and the work
that remains before treating the system as a dependable overnight operator.

Snapshot date: 2026-05-27.

## Current Baseline

The current merged baseline is PR #139, which documented the operation cycle
and the verified TwinPaper runtime smoke. At this point:

- `main` and `origin/main` are synchronized in the local checkout;
- there are no open PRs from the latest stabilization pass;
- the `twinpaper-adaptive-debug` profile has no pending approvals;
- the recent TwinPaper Offdesk tasks are completed, including the short runtime
  smoke `twinpaper-autonomy-20260522T071200Z`;
- the launch path has been validated through `dispatch.runtime` approval,
  `local-tmux` execution, polling, result artifacts, and deterministic
  post-run review.

This baseline proves that the approval-gated runtime path works. It does not
prove that a longer autonomous run will produce useful research output.

## Verified Flow

The validated flow is:

```text
project init
  -> module operation preflight
  -> prepare TwinPaper workload
  -> LAUNCH_DRY_RUN.md
  -> enqueue dispatch.runtime task
  -> first tick creates operator approval
  -> offdesk ok <approval-id>
  -> second tick launches local-tmux
  -> poll observes result sidecar
  -> result review marks the run clean
```

The short TwinPaper smoke observed:

- task: `twinpaper-autonomy-20260522T071200Z`;
- background ticket: `bg_81e0dd2e-1164-4473-8815-e4df04bea179`;
- runner: `local-tmux`;
- model: `qwen3-coder-next:latest`;
- runtime: `11.81s`;
- result: `1 total / 1 passed / 0 failed`;
- final mode state: `mode_verdict=evidence_ready`;
- remaining risk: `mode_risk=operator_review_required`;
- pending approvals after completion: `[]`.

## Completed Work

### Runtime Safety

- Provider fallback is approval-gated and does not consume
  `dispatch.runtime` approvals.
- Runtime tasks require a scoped `dispatch.runtime` approval before launch.
- Long Python workloads default to `local-tmux`.
- Prepared TwinPaper workloads declare system-critical forbidden actions:
  deletion, cleanup, reboot, service restart, storage or mount changes,
  package or permission changes, provider retargeting, and wiki promotion.

### Project And Module Scope

- `forager project init` creates read-only project operation packets.
- TwinPaper initialization records project scope as `twinpaper` and module
  operation scope as `module03_regspec_machine`.
- `MODULE_OPERATION_PREFLIGHT.json` is used as a reviewed preflight reference,
  not copied wholesale into prompts or operator output.
- Ondesk prompt packages include concise module preflight summaries when a
  matching initialization exists.

### Prepare And Launch Review

- `scripts/prepare_twinpaper_offdesk_task.py` checks role-gate, workload
  review, module operation preflight, evidence review, and Council readiness.
- Every prepare run writes `LAUNCH_DRY_RUN.md`.
- The launch dry run shows blockers, readiness, module scope, evidence paths,
  approval commands, and safety boundaries.
- No-enqueue prepare smoke verified that dry-run artifacts are readable and do
  not insert the task into the queue.

### Adaptive Wiki And Return Boundary

- Offdesk may create wiki candidates or run-local trial entries.
- Canonical wiki promotion remains review-gated.
- Closeout and Ondesk return are documented as required lifecycle stages.
- The operation cycle now has a dedicated guide:
  [`Operation Cycle`](guides/operation-cycle.md).
- The smoke procedure now has a dedicated runbook:
  [`TwinPaper Offdesk Runtime Smoke`](guides/twinpaper-offdesk-runtime-smoke.md).
- The longer validation path now has a dedicated runbook and prepare artifact:
  [`TwinPaper Offdesk Long-Run Validation`](guides/twinpaper-offdesk-long-run-validation.md)
  and `LONG_RUN_VALIDATION.md`.

## Remaining Work

### 1. Longer TwinPaper Run With Council Enabled

Goal: validate quality and direction control, not just launch mechanics.

Expected slice:

- prepare a realistic TwinPaper workload with Council enabled;
- use a wall-clock stop time or a bounded duration;
- keep `local-tmux`;
- inspect Council decisions between episodes;
- confirm that the run can stop, continue, or hand off for review without
  hidden mutation.

Acceptance checks:

- Council records are present and understandable;
- non-`continue` decisions stop or hand off according to policy;
- output quality is better than the one-iteration smoke;
- no system-critical mutation occurs;
- the final report separates pass/fail from operator judgement.

### 2. Offdesk Closeout On A Real Completed Run

Goal: make the "finished process -> reviewable work" transition practical.

Expected slice:

```bash
forager offdesk closeout --project-key twinpaper --dry-run
```

Then inspect:

- `closeout_plan.json`;
- `CLOSEOUT_PLAN.md`;
- `cleanup_manifest.json`;
- `COMMERCIAL_REVIEW_PACKET.md`;
- `RETURN_PACKAGE.md`.

Acceptance checks:

- closeout does not move, delete, or archive files;
- keep/archive/delete candidates are explainable;
- required first reads for Ondesk return are complete;
- any proposed cleanup requires separate review and approval.

### 3. Ondesk Return Package Validation

Goal: confirm a fresh live harness can resume from artifacts rather than raw
chat history.

Expected slice:

```bash
forager ondesk prompt-package --project-key twinpaper
```

Acceptance checks:

- the prompt package includes the latest project initialization summary;
- the package includes the latest closeout return package when present;
- the package tells the harness what to read first;
- the package does not imply that Offdesk output is trusted without review.

### 4. Wiki Candidate Review And Promotion Loop

Goal: prevent adaptive wiki from becoming hidden or noisy memory.

Expected slice:

- inspect candidate entries generated from recent TwinPaper runs;
- remove duplicates or weakly grounded claims;
- promote only reviewed, scoped entries;
- keep run-local trial entries separate from canonical entries.

Acceptance checks:

- every promoted entry has explicit evidence refs;
- candidate volume remains reviewable;
- project/module/agent-mode scope is correct;
- no entry silently changes runtime approval, provider, command, or workdir
  behavior.

### 5. Generalize Beyond TwinPaper

Goal: verify that the pattern is not overfit to one project.

Candidate targets:

- another TwinPaper module;
- regspec;
- another research or writing-heavy repository.

Acceptance checks:

- `project init` produces useful module candidates;
- project-specific evidence builders can be defined without hard-coding
  TwinPaper assumptions;
- prepare-time preflight catches missing evidence before enqueue;
- Ondesk return remains understandable to a fresh harness.

### 6. Improve Operator Surfaces

Goal: make the CLI/TUI reflect the operation cycle without requiring the
operator to remember the docs.

Candidate surfaces:

- `offdesk tasks`;
- `offdesk poll`;
- `offdesk pending`;
- closeout-required TUI attention;
- prompt-package summaries.

Acceptance checks:

- each surface shows the next safe action;
- review-required states are obvious;
- stale callback, missing result, and closeout-required states are not hidden;
- launch dry-run and closeout artifact paths are easy to find.

Current implementation note: `offdesk tasks`, `offdesk poll`, and `offdesk
tick` now expose a shared `next_safe_action` / `next_safe_actions` contract for
operator-facing next steps. Remaining surfaces should reuse the same contract
rather than inventing separate wording.

## Near-Term Recommendation

The next practical step is:

1. prepare a longer TwinPaper run with Council enabled;
2. launch it through the existing `dispatch.runtime` approval path;
3. monitor with `offdesk poll`, tmux, heartbeat, progress, and logs;
4. run closeout after completion;
5. validate the Ondesk return prompt package;
6. review wiki candidates before promotion.

Use the generated `LONG_RUN_VALIDATION.md` packet from
`scripts/prepare_twinpaper_offdesk_task.py` as the checklist for this sequence.

The launch path is no longer the main unknown. The remaining unknowns are
output quality, Council usefulness, closeout ergonomics, and wiki review load.

## Stop Conditions

Stop before continuing when:

- `LAUNCH_DRY_RUN.md` reports blockers;
- pending approval is not the expected `dispatch.runtime` approval;
- the task uses `local-background` for a long Python workload;
- `offdesk poll` reports stale callback, stale heartbeat, or missing result;
- closeout proposes file movement or deletion;
- a wiki promotion lacks evidence refs or correct scope;
- a fresh Ondesk prompt package cannot identify the next first reads.
