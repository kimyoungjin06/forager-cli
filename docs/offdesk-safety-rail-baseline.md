# Offdesk Safety Rail Baseline

This page records the current stabilization baseline before the next Offdesk
adaptive-agent phase. It is the short operator and review entry point for the
larger provider fallback, checkpoint, capability, redaction, and adaptive wiki
work.

Detailed design notes remain in:

- [`hermes-pattern-review.md`](hermes-pattern-review.md)
- [`adaptive-wiki.md`](adaptive-wiki.md)
- [`adaptive-wiki-execution-plan.md`](adaptive-wiki-execution-plan.md)
- [`hermes-adaptive-knowledge-benchmark.md`](hermes-adaptive-knowledge-benchmark.md)

## Baseline Scope

The current baseline includes these shipped surfaces:

- provider capacity classification and cooldown persistence;
- recommendation-only provider fallback inspection;
- approval-gated provider/model retargeting for queued or pending Offdesk
  dispatch tasks;
- pre-mutation checkpoint snapshots and read-only restore plans;
- capability registry contracts for approval, artifact, retry, and resume
  policy;
- operator-safe redaction reports and debug bundle export;
- adaptive wiki candidates, promoted entries, human and AI projections, runtime
  usage audit records, governance lint, review proposals, procedure runbook
  refs, and one-way markdown export;
- strict runtime wiki policy acknowledgements for review-expired entries;
- agent-mode scoped wiki projection for code development, research/writing, and
  critique.

The baseline does not include autonomous wiki mutation, restore execution,
provider fallback without approval, upload/export integrations for debug
bundles, or automatic agent-mode inference.

## Safety Invariants

- Candidate observation never changes runtime behavior by itself.
- Provider fallback approval may retarget only `provider_id`, `model`,
  `updated_at`, `last_provider_fallback`, and `not_before`.
- Fallback approval does not authorize generic `dispatch.runtime` execution.
- Checkpoint restore commands are read-only plans. They do not write restored
  files.
- Runtime projection receives compact AI instructions, never raw human wiki
  pages, candidates, deprecated entries, runner-only context, or unredacted
  secrets.
- If gate, launch, or tick reaches adaptive wiki without an agent mode, only
  shared entries are projected. Human inspection commands remain broad by
  default.
- Mode tags filter context only. They do not authorize command execution,
  provider/model retargeting, file mutation, or approval resolution.
- Debug bundles and operator JSON must remain sanitized and read-only unless an
  explicit export path is requested.

## Operator Runbook

Inspect durable work:

```bash
forager offdesk tasks --json
forager offdesk pending --json
forager offdesk background --json
forager offdesk resume --json
```

Inspect provider capacity and fallback:

```bash
forager offdesk provider-capacity --json
forager offdesk provider-fallback --provider-id <provider> --model <model> --json
```

Review or approve provider fallback:

```bash
forager offdesk pending
forager offdesk ok <approval-id>
forager offdesk tick --json
```

Inspect checkpoint and rollback evidence:

```bash
forager offdesk snapshots --json
forager offdesk snapshot <mutation-id> --json
forager offdesk restore-plan <mutation-id> --json
```

Inspect sanitized diagnostics:

```bash
forager offdesk debug-bundle --json
forager offdesk debug-bundle --export
forager offdesk debug-bundle --output <path>
```

Inspect adaptive wiki state:

```bash
forager offdesk wiki candidates --json
forager offdesk wiki entries --json
forager offdesk wiki projection --project-key <project> --artifact-kind <kind> --json
forager offdesk wiki projection --project-key <project> --artifact-kind <kind> --agent-mode code-development --json
forager offdesk wiki lint --json
```

Review strict runtime wiki policy:

```bash
forager offdesk wiki projection \
  --project-key <project> \
  --artifact-kind <kind> \
  --agent-mode <mode> \
  --compare-review-expired-policy \
  --json

forager offdesk wiki ack-runtime-policy \
  --project-key <project> \
  --artifact-kind <kind> \
  --agent-mode <mode> \
  --reason "operator reviewed strict runtime projection" \
  --json

forager offdesk wiki runtime-policy-ack-report \
  --project-key <project> \
  --artifact-kind <kind> \
  --agent-mode <mode> \
  --json
```

Export human-facing wiki pages:

```bash
forager offdesk wiki export-markdown --output <dir> --json
forager offdesk wiki export-markdown --output <dir> --dry-run --json
```

## Done And Deferred

Completed in this baseline:

- provider error classification with structured retry, compression, fallback,
  cooldown, and recovery-action hints;
- provider fallback metadata on blocked tasks, pending approvals, and sanitized
  debug bundles;
- approval deduplication for repeated capacity blocks;
- invalid fallback candidate revalidation without unsafe retargeting;
- legacy approval, task, snapshot, and wiki JSON compatibility defaults;
- required capability artifact checks before approval creation;
- runtime wiki injection, usage audit records, and kill switch behavior;
- adaptive wiki review, proposal, recurrence, promotion-chain, markdown export,
  and agent-mode projection surfaces;
- safe execution default for missing agent mode.

Deferred intentionally:

- automatic agent-mode inference from task text or command metadata;
- raw evidence snapshot export for source refs;
- source hash drift checks for exported raw evidence;
- markdown export drift checks against canonical JSON;
- autonomous restore execution;
- automatic provider fallback without operator approval;
- automatic wiki mutation or `auto_apply` behavior;
- external debug-bundle upload, archive packaging, and retention policy;
- live episode quality metrics proving correction recurrence reduction after
  promotion.

## Verification Baseline

Run this full bundle after touching Offdesk safety rails, adaptive wiki, or CLI
reference surfaces:

```bash
cargo fmt --all -- --check
git diff --check
cargo check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
cargo run -p xtask -- gen-docs
```

The current baseline was verified with the full bundle on 2026-05-19.

## Suggested Commit Boundary

If this work is committed as one checkpoint, use a message with this scope:

```text
Stabilize Offdesk safety rails and adaptive wiki baseline
```

Useful body bullets:

- Add provider capacity fallback recommendation and approval-gated retargeting.
- Add mutation snapshots, restore plans, capability artifact contracts, and
  sanitized debug bundles.
- Add adaptive wiki storage, projections, governance, markdown export, runtime
  context injection, and agent-mode scoping.
- Keep runtime defaults conservative when no agent mode is supplied.
- Regenerate CLI reference and document done/deferred baseline.

If splitting into multiple commits, the safest logical sequence is:

1. provider fallback and approval stabilization;
2. checkpoint, capability, redaction, and debug-bundle rails;
3. adaptive wiki store, projection, and runtime integration;
4. adaptive wiki governance, markdown export, and agent-mode scoping;
5. docs, CLI reference, and baseline runbook.
