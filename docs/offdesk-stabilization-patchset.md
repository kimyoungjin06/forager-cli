# Offdesk Stabilization Patch Set

This page is the review index for the current integrated Offdesk stabilization
tree. It is intentionally shorter than the design notes. Use it to decide what
belongs together, what each slice proves, and which verification gates must stay
green before moving to production-like Offdesk work.

For a PR-ready review summary, see
[`offdesk-stabilization-review.md`](offdesk-stabilization-review.md).

## Review Principle

The tree should be reviewed as one safety-rail stabilization pass, but it can be
read in five slices:

1. Provider fallback and approval-gated retargeting.
2. Mutation snapshots, restore plans, capability contracts, and redaction.
3. Adaptive wiki canonical store, projections, runtime usage, and governance
   review.
4. Offdesk agent-mode, evidence-bundle, and TwinPaper autonomy harnesses.
5. Operator-facing CLI, docs, generated reference, and regression tests.

Do not split by file type. The important boundaries are safety behavior and
operator-visible contracts.

## Patch Slices

| Slice | Primary files | Purpose | Main verification |
|---|---|---|---|
| Provider fallback | `src/offdesk/provider.rs`, `src/offdesk/scheduler.rs`, `src/offdesk/control_loop.rs`, `src/offdesk/approval.rs`, `tests/offdesk_cli.rs` | Convert provider capacity failures into operator-safe fallback recommendations and approval-gated provider/model retargeting. | `cargo test provider_fallback`, `cargo test --test offdesk_cli offdesk_provider_fallback_json_is_operator_safe` |
| Mutation and recovery rails | `src/offdesk/mutation.rs`, `src/offdesk/capability.rs`, `src/offdesk/background.rs`, `src/offdesk/runner.rs`, `src/offdesk/task_queue.rs`, `src/offdesk/redaction.rs` | Keep canonical mutation paths tied to snapshot evidence, restore-plan previews, sidecar recovery evidence, and redacted debug/operator output. | `cargo test --test offdesk_cli offdesk_snapshot_commands_report_verify_and_restore_plan`, `cargo test --test offdesk_cli offdesk_debug_bundle_json_redacts_legacy_state_and_is_read_only` |
| Adaptive wiki | `src/offdesk/adaptive_wiki.rs`, `src/cli/offdesk.rs`, `src/offdesk/runner.rs`, `src/offdesk/control_loop.rs`, `tests/offdesk_cli.rs` | Add governed adaptive memory: candidates, promoted entries, AI/human/runtime projections, usage traces, policy acknowledgements, correction recurrence, review proposals, and promotion evidence chains. | `cargo test adaptive_wiki`, `cargo test --test offdesk_cli offdesk_wiki`, `cargo test --test offdesk_cli offdesk_tick_injects_adaptive_wiki_runtime_context_and_records_usage` |
| TwinPaper Offdesk harness | `scripts/build_twinpaper_module03_operation_profile.py`, `scripts/build_twinpaper_evidence_bundle.py`, `scripts/review_evidence_bundle.py`, `scripts/offdesk_twinpaper_autonomy_workload.py`, `scripts/review_twinpaper_offdesk_result.py`, `scripts/prepare_twinpaper_offdesk_task.py`, `scripts/offdesk_*_harness.py` | Exercise the wiki, evidence, module-operation, review, and model-prompt contracts against a real TwinPaper-shaped workload without mutating the target repo. | Python compile check, deterministic result review, 30-minute Offdesk run with post-run review `clean` |
| Operator docs and generated CLI | `docs/offdesk-safety-rail-baseline.md`, `docs/adaptive-wiki.md`, `docs/adaptive-wiki-execution-plan.md`, `docs/offdesk-agent-modes.md`, `docs/offdesk-evidence-bundles.md`, `docs/hermes-pattern-review.md`, `docs/cli/reference.md` | Give operators a short baseline, detailed design trail, and regenerated CLI reference matching the changed surfaces. | `cargo run -p xtask -- gen-docs`, `git diff --check` |

## Commit Order

If this tree is split into review commits, use this order:

1. Provider fallback and approval metadata.
2. Mutation snapshot/restore, capability artifact contracts, and redaction.
3. Adaptive wiki core store and projections.
4. Adaptive wiki CLI/review/runtime policy surfaces.
5. TwinPaper evidence and autonomy harnesses.
6. Documentation and generated CLI reference.

This order keeps every commit explainable, but the final acceptance gate should
still be run on the combined tree because the safety rails interact through
`offdesk tick`, `debug-bundle`, runtime projection, and approval state.

## Split Feasibility Decision

Recommended default: review and merge this as one integrated stabilization
patch set.

Reason: the implementation boundaries are behavioral, but two files carry
several behavioral surfaces at once:

- `src/cli/offdesk.rs` includes provider capacity/fallback commands, snapshot
  commands, debug-bundle redaction surfaces, and the full adaptive wiki command
  tree.
- `tests/offdesk_cli.rs` similarly mixes provider fallback, capability artifact
  contracts, snapshot/restore, debug-bundle redaction, runtime wiki projection,
  and wiki governance regression coverage.

Splitting by file would create incoherent commits. Splitting by behavior is
possible, but it requires careful hunk-level staging in those two files and
running targeted tests after each staged commit.

If a multi-commit review is required, use this exact staging model:

1. `provider-fallback-approval`
   - include provider model/capacity code, provider fallback approval metadata,
     scheduler/control-loop retargeting, provider fallback CLI output, and only
     the provider fallback tests;
   - verify with `cargo test provider_fallback`.
2. `mutation-capability-redaction`
   - include capability artifact contracts, mutation snapshot/restore-plan
     changes, debug-bundle/read-only redaction changes, and their tests;
   - verify with snapshot/debug-bundle targeted tests plus `cargo clippy`.
3. `adaptive-wiki-core`
   - include `src/offdesk/adaptive_wiki.rs`, module exports, store types,
     projection logic, review proposals, correction recurrence, markdown export,
     and unit tests;
   - verify with `cargo test adaptive_wiki`.
4. `adaptive-wiki-cli-runtime`
   - include adaptive wiki CLI commands, gate/launch/tick runtime projection,
     runtime policy acknowledgement, background probe usage records, and
     related `offdesk_cli` tests;
   - verify with `cargo test --test offdesk_cli offdesk_wiki` and runtime wiki
     targeted tests.
5. `twinpaper-offdesk-harness`
   - include the Python evidence, workload, role, runtime, and post-run review
     harnesses plus evidence-bundle docs;
   - verify with Python compile checks, deterministic result review, and the
     recorded 30-minute run artifacts.
6. `operator-docs-generated-reference`
   - include safety baseline docs, Hermes/adaptive wiki notes, agent modes,
     patch-set index, `docs/SUMMARY.md`, and regenerated CLI reference;
   - verify with `cargo run -p xtask -- gen-docs` and `git diff --check`.

Do not create those commits mechanically with path-only `git add` commands.
Use `git add -p` or an equivalent hunk-aware staging tool for
`src/cli/offdesk.rs` and `tests/offdesk_cli.rs`, then run the verification gate
listed for that slice before moving to the next slice.

## Current Verified Baseline

The following verification commands passed on the integrated tree:

```bash
python3 -B -m py_compile scripts/build_twinpaper_module03_operation_profile.py scripts/build_twinpaper_evidence_bundle.py scripts/review_evidence_bundle.py scripts/offdesk_twinpaper_autonomy_workload.py scripts/prepare_twinpaper_offdesk_task.py scripts/review_twinpaper_offdesk_result.py scripts/offdesk_workload_review_harness.py scripts/offdesk_role_llm_episode_harness.py scripts/offdesk_role_episode_harness.py scripts/offdesk_wiki_llm_harness.py scripts/offdesk_runtime_episode_harness.py
scripts/review_twinpaper_offdesk_result.py --result /home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T122750Z/result.json --out target/offdesk-result-review-smoke/latest-30min/results.json
cargo fmt --all -- --check
git diff --check
cargo check
cargo clippy --all-targets --all-features -- -D warnings
cargo test adaptive_wiki
cargo test provider_fallback
cargo test --test offdesk_cli
cargo test
cargo run -p xtask -- gen-docs
```

The latest 30-minute TwinPaper Offdesk run used qwen3-coder-next through the
real `dispatch.runtime` approval flow and `local-tmux` runner:

- request/task: `twinpaper-autonomy-20260520T122750Z`
- workload result: `12/12` passed
- workload verdict: `usable`
- operator risk: `low`
- evidence review: `sufficient`
- post-run deterministic review: `clean`
- result findings: `0`
- learning candidates: `0`

Artifacts:

- `/home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T122750Z/result.json`
- `/home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T122750Z/REPORT.md`
- `/home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T122750Z/result_review/results.json`
- `/home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T122750Z/result_review/RESULT_REVIEW.md`

## Acceptance Criteria

Before treating this stabilization patch set as ready:

- `cargo test` and `cargo clippy --all-targets --all-features -- -D warnings`
  must pass on the combined tree.
- `cargo run -p xtask -- gen-docs` must be rerun after CLI changes.
- `git diff --check` must pass after generated docs are updated.
- Provider fallback approvals must remain separate from runtime dispatch
  approvals.
- Provider fallback retargeting must only mutate provider/model scheduling
  fields, not command, workdir, or launch spec.
- Adaptive wiki candidates must not change runtime behavior until promoted.
- Runtime wiki projection must remain fenced, compact, and redacted.
- Debug bundles and operator JSON must not expose runner-only context or
  secrets.
- Long Offdesk workload health must be proven through tmux, heartbeat,
  progress, logs, result, and post-run review artifacts.

## Known Non-Goals

This patch set does not add:

- autonomous wiki mutation;
- restore execution;
- provider fallback without explicit approval;
- upload/export integrations for debug bundles;
- automatic mode inference for every future agent mode;
- production scheduling policy for arbitrary real-work Offdesk tasks.

Those belong in later work after the current safety baseline is merged.
