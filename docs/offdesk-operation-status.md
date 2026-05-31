# Offdesk Operation Status

This page records the current Forager Offdesk operating baseline and the work
that remains before treating the system as a dependable overnight operator.

Snapshot date: 2026-05-31.

## Current Baseline

The last merged runtime baseline is PR #139, which documented the operation
cycle and the verified TwinPaper runtime smoke. The current local checkout adds
documentation governance, adaptive wiki projection freshness, closeout return
guidance, and Forager storage-path visibility. These changes are implemented,
tested locally, and split into reviewable commits. They still need remote review
or push.

At this point:

- the default Forager profile is loaded from primary Forager storage, not legacy
  AoE fallback storage;
- the default profile has no pending approvals, queued tasks, active tasks,
  failed tasks, resume-pending tasks, stale background runs, or closeout-required
  tasks;
- the `twinpaper-adaptive-debug` wiki markdown vault is fresh relative to
  canonical adaptive wiki state;
- `forager project audit-docs` reports no findings or recommendations for the
  Forager checkout under the standard profile;
- `forager project audit-docs` reports no findings or recommendations for the
  TwinPaper checkout under the research-longrun profile;
- `cargo test -q`, `cargo fmt --check`, `git diff --check`, and the
  documentation-governance script syntax check pass locally.

This baseline proves that the approval-gated runtime path and the governance
surfaces can be inspected consistently. It still does not prove that a longer
autonomous run will produce useful research output.

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

### Documentation And Artifact Governance

- `PROJECT_STATE.md`, `DECISIONS.md`, and `DELIVERABLES.md` define the shallow
  current surfaces for the Forager checkout.
- `forager project audit-docs` audits current-state freshness, decision and
  deliverable surfaces, human-facing output candidates, large logs, latest
  aliases, and adaptive wiki markdown projection freshness.
- `forager project init` writes `GOVERNANCE_SURFACE_HINTS.md` as a read-only
  packet artifact.
- `forager project apply-governance-hints` is dry-run by default, requires
  `--reviewed` to write, creates only missing governance surfaces, and never
  overwrites existing project documents.
- `forager offdesk closeout` carries focused documentation-governance
  recommendations into the Ondesk return package.
- Task-scoped closeout has been validated on the real
  `twinpaper-autonomy-20260527T125541Z` long-run artifact. It produced a compact
  one-task return package with no missing artifacts and no delete candidates.
- Closeout documentation governance now prefers the target repo recorded beside
  runtime artifacts in `prepared_task.json` or `manifest.json` before falling
  back to the runtime workdir. This matters when the runtime command runs from
  the harness checkout while the research target is a separate repository.
- `forager ondesk prompt-package` now states the documentation governance source
  and can run a fresh `project audit-docs` pass with `--include-doc-audit`,
  using the latest closeout workdir when one is available.

### Adaptive Wiki Human Projection

- `forager offdesk wiki export-markdown` now defaults to the active profile's
  `wiki-vault/` directory.
- The export report includes `projection_status` with `missing`, `stale`,
  `fresh`, and `empty_canonical` states.
- Project audits can recommend a wiki vault re-export when canonical adaptive
  wiki state is newer than the markdown projection.

### Rename And Storage Visibility

- `forager doctor` reports active primary/legacy storage and repo-config
  sources.
- `forager status --json` exposes `profile_dir_source` and `app_dir_source` so
  automation can detect legacy fallback.
- The local machine has migrated global legacy AoE state into primary Forager
  storage; legacy data remains as backup.

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
- output quality is materially better than the one-iteration smoke;
- no system-critical mutation occurs;
- the final report separates pass/fail from operator judgement.

### 2. Closeout Review Verdict For Real TwinPaper Return

Goal: record the human/commercial review result without applying file
operations.

Expected slice:

```bash
forager offdesk closeout-review \
  --closeout-id closeout_f2573215 \
  --verdict approved|revise|blocked \
  --reviewer <reviewer>
```

Acceptance checks:

- the verdict applies to the task-scoped closeout artifact;
- unsafe operations and missing evidence are recorded explicitly;
- the record does not move, delete, archive, or promote files;
- `ondesk prompt-package` includes the closeout-review verdict.

### 3. Ondesk Return Package Validation

Goal: confirm a fresh live harness can resume from artifacts rather than raw
chat history.

Expected slice:

```bash
forager ondesk prompt-package --project-key twinpaper --include-doc-audit
```

Acceptance checks:

- the prompt package includes the latest project initialization summary;
- the package includes the latest closeout return package when present;
- the package states whether documentation governance came from closeout,
  a fresh project audit, or an unavailable audit;
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
- Telegram and WebUI operator cards;
- prompt-package summaries.

Acceptance checks:

- each surface shows the next safe action;
- review-required states are obvious;
- Telegram and WebUI preserve the shared next-safe-action priority order;
- stale callback, missing result, and closeout-required states are not hidden;
- launch dry-run and closeout artifact paths are easy to find.

Current implementation note: `offdesk tasks`, `offdesk poll`, `offdesk tick`,
`offdesk pending`, `offdesk maintenance-report`, and `forager status` now
expose a shared `next_safe_action` / `next_safe_actions` contract for
operator-facing next steps. Remaining surfaces should reuse the same contract
rather than inventing separate wording.

## Near-Term Recommendation

The next practical step is:

1. review the latest task-scoped TwinPaper `COMMERCIAL_REVIEW_PACKET.md`;
2. record a `closeout-review` verdict for `closeout_f2573215`;
3. validate the Ondesk return prompt package includes that verdict;
4. review wiki candidates before promotion;
5. prepare the next longer TwinPaper run only after the return/review path is
   closed.

Use the generated `LONG_RUN_VALIDATION.md` packet from
`scripts/prepare_twinpaper_offdesk_task.py` as the checklist for this sequence.

The launch path and local governance checks are no longer the main unknowns.
The remaining unknowns are output quality, Council usefulness, return-package
ergonomics, and wiki review load.

## Stop Conditions

Stop before continuing when:

- `LAUNCH_DRY_RUN.md` reports blockers;
- pending approval is not the expected `dispatch.runtime` approval;
- the task uses `local-background` for a long Python workload;
- `offdesk poll` reports stale callback, stale heartbeat, or missing result;
- closeout proposes file movement or deletion;
- a wiki promotion lacks evidence refs or correct scope;
- a fresh Ondesk prompt package cannot identify the next first reads.
