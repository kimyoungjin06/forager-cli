# Ondesk Handoff Guide

Ondesk work is usually driven by the harness you are already using, such as
Codex or Claude Code. In that mode Forager should not become the primary agent
loop. Its job is to keep a safe bridge between live harness work, project
notes, and later Offdesk or wiki review.

This guide covers the Ondesk side of the full
[Operation Cycle](operation-cycle.md). The main rule is that a fresh harness
should start from a compact package, not from a hidden raw resume.

## Core Loop

1. Record operator intent while the work is fresh.

```bash
forager ondesk note --project-key twinpaper --mode writing \
  --text "Draft focuses on evidence chain before novelty claims."
```

2. Capture the current harness context when you want cross-review or handoff.

```bash
forager ondesk capture codex-harness --project-key twinpaper --mode writing --lines 250
```

This writes an append-only capture under the active profile:

```text
ondesk_captures/<timestamp>_<capture-id>/
  capture.json
  PROMPT_CONTEXT.md
```

3. Give the generated prompt package to another harness.

```bash
forager ondesk prompt-package --capture-id ondesk-cap-12345678
```

The package is context, not proof of completion. The next harness should still
separate observation from inference, ask for missing evidence, and propose wiki
changes as candidates.

When a matching project initialization exists in the active profile,
`prompt-package` includes the latest `ONDESK_START_PACKAGE.md`, operation
targets, readiness state, and a concise `MODULE_OPERATION_PREFLIGHT.json`
summary for the same `project_key`. The preflight bridge exposes module
readiness, blockers, and command purposes, not raw command strings. This is the
preferred bridge from `forager project init` into a fresh Ondesk harness.

Every prompt package includes a `Documentation Governance Source` section. By
default it states whether governance guidance came from the latest matching
closeout return package, or whether no fresh audit was requested. Add
`--include-doc-audit` when the next harness should audit the active project
immediately:

```bash
forager ondesk prompt-package --project-key twinpaper --include-doc-audit
```

The fresh audit path reports `source: fresh_project_audit`, keeps the full
machine audit out of the prompt package, and embeds only the capped
recommendations a human can evaluate next. When a matching closeout exists, the
fresh audit uses the closeout plan's documentation-governance workdir before
falling back to the captured session path or current directory. If the audit
cannot run, the package uses `fresh_project_audit_unavailable` and keeps the
prompt render non-fatal.

When a matching Offdesk closeout exists, `prompt-package` also includes the
latest `RETURN_PACKAGE.md` and closeout-review verdict for the same
`project_key`. This is the preferred bridge from overnight Offdesk work back
into a fresh Ondesk session. If closeout found documentation governance
recommendations, the return package carries the focused action list rather than
the full audit summary.

`prompt-package` also renders the compact `artifact_index.v1` and
`artifact_retention_review.v1` projections from the shared review surface. The
prompt shows artifact counts, retention action counts, and meaning first; paths
remain in `review_surface` JSON for audit. Use the project artifact-index and
retention-review commands when the next reviewer must inspect project outputs
from `DELIVERABLES.md` and common output roots, not only profile-local handoff
artifacts:

```bash
forager project artifact-index /path/to/project --project-key <project> --json
forager project retention-review /path/to/project --project-key <project> --json
```

When one retention item needs an operator decision before follow-up work,
create a separate approval-only request:

```bash
forager project retention-request /path/to/project --project-key <project> --artifact-id <id> --action <keep|promote|archive|dispose>
```

The request appears in `forager offdesk pending`; it does not mutate project
files or consume the Ondesk review surface itself.

Once approved, use `retention-apply` to consume the decision into a
profile-local receipt before planning any project mutation:

```bash
forager project retention-apply /path/to/project --project-key <project> --approval-id <approval-id>
```

This records `artifact_retention_application.v1` with
`mutation_performed=false`. It is the handoff record that a later deliverables,
archive, or disposal workflow should reference.

For a promote decision, `retention-promote` is the reviewed deliverables bridge:

```bash
forager project retention-promote /path/to/project --project-key <project> --approval-id <approval-id>
forager project retention-promote /path/to/project --project-key <project> --approval-id <approval-id> --reviewed
```

The first command is a dry run. The reviewed command creates a mutation
snapshot and restore plan for `DELIVERABLES.md` before appending the selected
artifact entry.

## Knowledge Policy

- `ondesk note` stores redacted, operator-safe JSONL in `ondesk_notes.jsonl`.
- `ondesk capture` records tmux scrollback only when the session is running.
- `--include-git` is read-only and captures `git status --short` plus
  `git diff --stat`; it does not run tests, clean files, or mutate worktrees.
- Ondesk commands do not promote adaptive wiki entries. They prepare candidate
  material for a later review stage.
- Secrets and runner-only context are redacted before durable note or capture
  artifacts are written.

## When To Use It

Use Ondesk handoff when:

- two live harnesses should cross-review the same project state;
- a long discussion needs to become a compact prompt for the next harness;
- a non-code writing, analysis, critique, or planning task should produce
  durable review material;
- a future Offdesk episode needs current human intent without inheriting raw
  chat logs.

Use Offdesk tasks instead when Forager should own the execution, approvals,
recovery records, and morning-review evidence.

## Handoff Checklist

Before switching from Ondesk to Offdesk:

- record the current objective and known non-goals with `ondesk note`;
- capture only the harness context that the next reviewer needs;
- make the target project and module explicit through `project_key` and, when
  available, a project initialization packet;
- state forbidden operations such as deletion, cleanup, service restart,
  package install, provider retargeting, and wiki promotion;
- describe the expected evidence artifacts, not only the desired conclusion.

Before returning from Offdesk to Ondesk:

- read `result.json`, `REPORT.md`, and post-run review artifacts;
- run or inspect Offdesk closeout;
- start the next harness from `RETURN_PACKAGE.md` or
  `forager ondesk prompt-package --project-key <project>`;
- promote wiki changes only after review, not just because an Offdesk run
  generated candidate knowledge.

## Morning Telegram Handoff

For long overnight work, send a compact Telegram handoff around 08:30 KST and
use WebUI as the review surface. Telegram should answer only: should the
operator start Ondesk review now, keep it pending, or defer with a natural
language condition. It should not approve cleanup, wiki promotion, provider
retargeting, file movement, or deletion.

Build the shared review packet, then build the request from closeout,
prompt-package, and review-surface artifacts:

```bash
forager ondesk review-surface --project-key twinpaper --json > "$REVIEW_SURFACE_JSON"

scripts/build_ondesk_handoff_request.py \
  --project-key twinpaper \
  --closeout-artifact-dir "$CLOSEOUT_DIR" \
  --prompt-package "$ONDESK_PROMPT_PACKAGE" \
  --review-surface "$REVIEW_SURFACE_JSON" \
  --webui-url "$FORAGER_WEBUI_URL" \
  --out "$HANDOFF_REQUEST_JSON"
```

Then pass that request through the existing Telegram relay:

```bash
scripts/offdesk_telegram_decision_relay.py \
  --request "$HANDOFF_REQUEST_JSON" \
  --out "$HANDOFF_RESULT_JSON"
```

The request includes `next_safe_actions` for the WebUI entry and pending path,
using the same operator-next-step contract as `forager status`, `offdesk pending`,
`tasks`, `poll`, `tick`, and `maintenance-report`. It also includes a
`decision_record.v1` parent record, built through the shared producer helper, so
the handoff can be ingested into the profile decision ledger with
`forager offdesk decision ingest-telegram` when the operator replies. The
handoff summary reads `closeout_receipt.acceptance_status` when a closeout
review receipt exists. `accepted` means the output can move into Ondesk review;
`approved_with_followups`, `revision_required`, and `blocked` remain visible as
review-required states rather than accepted truth. When a `review_surface.v1`
packet is provided, Telegram detail replies render the same review summary used
by `forager ondesk prompt-package`, including accepted-truth state, closeout
risks, review queue counts, artifact-index counts, and artifact meanings before
any paths. The rendered message hides raw paths and ids. Those remain in the
request, state, and result JSON for audit/debugging. The relay writes the state
beside the result as
`<result-stem>.telegram_decision_state.json`, so simultaneous handoff and
council prompts in the same directory do not overwrite each other's state
artifacts.
