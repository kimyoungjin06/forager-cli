# Offdesk Closeout Guide

Offdesk work is not complete when a background task exits. It is complete only
after its artifacts, document updates, cleanup candidates, and Ondesk return
context have been reviewed.

Forager treats closeout as a required lifecycle gate between:

```text
Ondesk closeout -> Offdesk execution -> Offdesk closeout -> Ondesk return
```

The surrounding handoff path is documented in the
[Operation Cycle Guide](operation-cycle.md). Closeout is the point where a
finished process becomes reviewable work.

## Ondesk To Offdesk

Before handing work to Offdesk, prepare the work while you are present:

- record the operator intent with `forager ondesk note`;
- capture current harness context with `forager ondesk capture`;
- write or refresh the task plan, wiki candidates, and expected evidence;
- define success criteria and forbidden actions;
- require approvals for deletion, file movement, reboot, service restart,
  provider/model retargeting, and wiki mutation.

This gives Offdesk a scoped target instead of raw chat history.

## Offdesk To Ondesk

After Offdesk work, run closeout before resuming Ondesk:

```bash
forager offdesk closeout --project-key twinpaper --dry-run
```

The command writes a closeout artifact directory under the active profile:

```text
offdesk_closeouts/<timestamp>_<closeout-id>/
  closeout_plan.json
  CLOSEOUT_PLAN.md
  cleanup_manifest.json
  COMMERCIAL_REVIEW_PACKET.md
  RETURN_PACKAGE.md
```

The closeout command is a dry-run planner. It does not move, delete, archive,
or mutate project files.

Generating the packet is not the same as approving the closeout. After a
commercial model or operator reviews `COMMERCIAL_REVIEW_PACKET.md`, record the
verdict:

```bash
forager offdesk closeout-review \
  --closeout-id <closeout-id> \
  --verdict approved \
  --reviewer gpt-5.5 \
  --review-provider gpt-5.5 \
  --review-file <path-to-review-output>
```

Only an `approved` review record clears the TUI's closeout-required signal.
`revise` and `blocked` verdicts are preserved as evidence but keep the return
path reviewable.

`forager offdesk closeout-review` also writes a `closeout_receipt.v1` artifact:

```text
closeout_receipt_<timestamp>.json
```

The receipt separates the review verdict from accepted truth:

- `accepted`: the closeout was approved with no tracked follow-ups.
- `approved_with_followups`: the closeout verdict is approved, but open
  decisions, missing evidence, required first reads, retention review, stale
  tasks, or wiki/documentation follow-ups remain.
- `revision_required`: the reviewer returned `revise`.
- `blocked`: the reviewer returned `blocked`.

The receipt is also inserted into `RETURN_PACKAGE.md` so the next Ondesk harness
can see whether it is resuming from accepted work or from an approved package
with follow-ups. It still does not approve file operations, cleanup, provider
retargeting, or wiki promotion.

## Cleanup Policy

Closeout classifies files but does not apply changes:

- `keep`: provenance anchors, result artifacts, reports, declared task
  artifacts, and files required for Ondesk return.
- `archive_candidate`: raw logs and bulky runtime artifacts that may be moved
  after review.
- `delete_candidate`: disabled by default in the first closeout surface.

Any future `move`, `archive`, or `delete` action must pass both:

- commercial model review of the closeout packet;
- explicit human approval recorded through the normal approval path.

Never auto-delete or auto-move:

- git-tracked source or documentation files;
- human-authored notes or reports;
- hidden config, env, symlink, mount, external drive, or system paths;
- artifacts referenced by reports, wiki candidates, approvals, or resume
  records.

## Commercial Review

`COMMERCIAL_REVIEW_PACKET.md` is the model-facing review packet. It asks a
strong commercial model to return only a verdict:

```json
{
  "verdict": "approved|revise|blocked",
  "unsafe_operations": [],
  "missing_evidence": [],
  "required_first_reads": [],
  "notes": ""
}
```

This review is advisory until the operator approves the concrete action.
`forager offdesk closeout-review` records the verdict and the review artifact
path; it still does not execute file operations.

## Ondesk Return

Use `RETURN_PACKAGE.md` to start a fresh or resumed Ondesk harness. The harness
should read the listed result artifacts first, then inspect open decisions and
run the verification commands before continuing work.

Closeout now also reads `offdesk_decisions.jsonl` from the active profile and
from matched run artifact directories, such as the directory containing
`result.json`. Any unresolved `decision_record.v1` entry is surfaced in
`open_decisions`, and the raw records are preserved under `decision_records` in
`closeout_plan.json`. Receipted records remain in `decision_records` as history
and are not treated as open unless their schema validation fails.

The return package is intentionally shorter than `closeout_plan.json` and
`cleanup_manifest.json`. It starts with status, decisions needed, capped first
reads, a short change summary, grouped evidence, documentation governance
recommendations, and the next safe action. Large evidence inventories stay in
the machine-readable closeout artifacts.

`forager ondesk prompt-package --project-key <project>` now includes the latest
matching closeout return package and the latest recorded closeout-review
verdict, when those artifacts exist. This makes the normal morning return path
start from a fresh package instead of requiring the operator to manually paste
the closeout artifact path.

When a closeout receipt exists, the prompt package also surfaces
`closeout_receipt_status` and the receipt artifact path. The next harness should
treat `approved_with_followups`, `revision_required`, and `blocked` as review
states, not as accepted final truth.

When no closeout exists, or when the next harness needs a current governance
view, use:

```bash
forager ondesk prompt-package --project-key <project> --include-doc-audit
```

That adds a fresh `project audit-docs` recommendation summary to the prompt
package while preserving the same review boundary. If a matching closeout is
present, the fresh audit uses the closeout plan's documentation-governance
workdir instead of the shell's current directory. Audit failures are reported as
unavailable context, not treated as proof that the project is clean.

Closeout also runs the documentation governance audit against the closeout
workdir when one is available. `RETURN_PACKAGE.md` includes only the focused
recommendations, such as deliverables to promote or outputs to record in
`RETENTION_REVIEW.md`; the full machine summary stays in
`forager project audit-docs --json`.

For workloads launched from a harness checkout against a separate target repo,
closeout first looks beside result and log artifacts for `prepared_task.json` or
`manifest.json` and uses their `repo` field as the documentation-governance
workdir. An explicit `--workdir` still wins. This keeps a TwinPaper runtime
command launched from Forager from accidentally auditing the Forager checkout as
the research project.

## Runtime Smoke Interpretation

A clean runtime smoke does not skip closeout. For example, the TwinPaper
runtime smoke can prove that `dispatch.runtime` approval, `local-tmux` launch,
polling, result artifacts, and deterministic post-run review all work. It still
leaves `mode_risk=operator_review_required`, because the result must be read
before it becomes trusted work.

Use the smoke result as launch-path evidence, then close out any real Offdesk
run before returning to Ondesk. See
[`TwinPaper Offdesk Runtime Smoke`](twinpaper-offdesk-runtime-smoke.md) for the
validated short-run procedure.

`forager offdesk tasks`, `forager offdesk poll`, `forager offdesk tick`,
`forager offdesk pending`, `forager offdesk maintenance-report`, and
`forager status` also follow this boundary. They expose `next_safe_action` /
`next_safe_actions` surfaces and the human output points to closeout, approval,
or recovery review, rather than claiming that terminal dispatch status means no
operator action is needed.

`forager status --json` keeps the legacy `closeout_required_offdesk_tasks`
count, and also includes `closeout_state` so operators can distinguish:

- `missing_closeout`: completed tasks with no current closeout package;
- `pending_review`: a closeout package exists but no fresh review verdict was
  recorded;
- `revision_required`: the latest fresh verdict was `revise` or `blocked`;
- `stale_closeout` / `stale_review`: the task changed after the package or
  verdict;
- `approved`: a fresh `approved` verdict exists and the task is no longer
  counted as closeout-required.
