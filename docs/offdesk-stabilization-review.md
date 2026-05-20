# Offdesk Stabilization Review Note

This note is the final review and PR-description draft for the integrated
Offdesk stabilization patch set. The detailed patch grouping lives in
[`offdesk-stabilization-patchset.md`](offdesk-stabilization-patchset.md).

## Suggested PR Title

Stabilize Offdesk fallback, adaptive wiki, and evidence-backed autonomy rails

## Suggested Commit Message

```text
Stabilize Offdesk fallback and adaptive wiki rails

- add approval-gated provider fallback retargeting and capacity-state surfaces
- add mutation snapshot, restore-plan, artifact-contract, and redaction rails
- add adaptive wiki candidates, promoted entries, projections, usage, review,
  recurrence, proposal, and promotion-chain surfaces
- add TwinPaper evidence bundles, role/runtime harnesses, autonomous workload,
  and deterministic post-run result review
- regenerate CLI docs and add operator-facing safety baseline docs
```

## PR Summary

This patch turns the current Offdesk work into an integrated safety baseline.
It keeps autonomous behavior behind explicit approval and review gates while
adding the adaptive wiki and evidence-review loops needed for longer Offdesk
tasks.

The main behavior changes are:

- provider capacity failures now produce operator-safe fallback recommendations;
- provider/model retargeting is approval-gated through
  `dispatch.provider_fallback`, separate from `dispatch.runtime`;
- canonical mutation capabilities can require snapshot/restore evidence;
- debug bundles and operator JSON paths use stronger redaction/reporting;
- adaptive wiki state now has governed candidates, promoted entries, compact AI
  projections, human projections, runtime usage records, correction recurrence,
  review proposals, and promotion evidence traces;
- long TwinPaper-style Offdesk runs now have deterministic evidence bundles,
  preflight review, live heartbeat/progress artifacts, preserved model
  responses, and deterministic post-run review.

## Safety Claims

- Fallback approval does not authorize runtime dispatch.
- Fallback retargeting changes provider/model scheduling fields only; it does
  not mutate command, workdir, launch spec, or execution brief.
- Adaptive wiki candidates do not affect runtime behavior until promoted.
- Runtime wiki projection is fenced, compact, and redacted.
- Review-expired wiki entries can be warned or strictly excluded by policy.
- Debug bundles and operator output do not expose runner-only context or known
  secret patterns.
- TwinPaper autonomy harnesses are read-only for the target repo and write only
  under the selected Offdesk output directory.

## Review Focus

Reviewers should focus on these questions:

1. Does provider fallback preserve the approval boundary between
   `dispatch.provider_fallback` and `dispatch.runtime`?
2. Does retargeting preserve task command/workdir/launch-spec immutability?
3. Are adaptive wiki projections scoped tightly enough by project, artifact
   kind, and agent mode?
4. Are runtime wiki policy acknowledgements auditable and bounded?
5. Are redaction paths applied before operator-visible JSON, debug bundles, and
   reports leave the runner-only context boundary?
6. Do the TwinPaper harnesses preserve evidence and failures without converting
   model success into unreviewed durable behavior changes?

## Verification

The integrated tree passed:

```bash
python3 -B -m py_compile scripts/build_twinpaper_evidence_bundle.py scripts/review_evidence_bundle.py scripts/offdesk_twinpaper_autonomy_workload.py scripts/prepare_twinpaper_offdesk_task.py scripts/review_twinpaper_offdesk_result.py scripts/offdesk_workload_review_harness.py scripts/offdesk_role_llm_episode_harness.py scripts/offdesk_role_episode_harness.py scripts/offdesk_wiki_llm_harness.py scripts/offdesk_runtime_episode_harness.py
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

The latest real Offdesk run also passed:

- request/task: `twinpaper-autonomy-20260520T122750Z`
- runner: `local-tmux`
- approval path: `dispatch.runtime` pending approval, explicit `offdesk ok`,
  then `offdesk tick`
- workload result: `12/12` passed
- workload verdict: `usable`
- operator risk: `low`
- evidence review: `sufficient`
- deterministic result review: `clean`
- result-review findings: `0`
- result-review learning candidates: `0`

## Known Non-Goals

This patch does not add autonomous wiki mutation, restore execution, unapproved
provider fallback, debug-bundle upload/export integrations, or production
scheduling policy for arbitrary real-work Offdesk tasks.

## Merge Recommendation

Merge as one integrated stabilization patch set unless the reviewer requires a
multi-commit history. If a split is required, follow the hunk-level staging
plan in `offdesk-stabilization-patchset.md`; do not split mechanically by file.
