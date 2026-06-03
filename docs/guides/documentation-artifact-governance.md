# Documentation And Artifact Governance

Long-running agent projects need a documentation system that can absorb steady
growth without making humans browse every file. This guide defines the shared
premises, operating direction, and default behaviors for project docs, logs,
run artifacts, deliverables, and agent-created knowledge.

The goal is not to make every project use the same folder tree. The goal is to
keep raw evidence reproducible while keeping the human starting surface small,
current, and reviewable.

## Premises

These premises describe durable conditions of long-running projects. They are
not file rules or tool choices.

1. Long-running projects continuously accumulate knowledge, records, and
   artifacts.
2. People make better decisions from a small current surface than from the full
   historical record.
3. Files are understood through role, status, source, and authority, not only
   through path and name.
4. Records preserve past evidence, while current surfaces support present
   judgment and next actions.
5. Reproducible source structures and human-facing discovery structures serve
   different purposes.
6. Agent-created documents and knowledge become project knowledge through
   review, linking, summarization, and promotion.
7. Project knowledge is repeatedly strengthened, replaced, contradicted,
   summarized, preserved, or marked for retirement.
8. Long-term reference stability comes from identifiers, indexes, provenance,
   and transition records as much as from file location.

## Direction

The premises imply these operating preferences:

1. Keep the current state surface small and explicit.
2. Separate originals, canonical docs, logs, evidence, generated artifacts, and
   deliverables by role.
3. Allow deep storage structures while providing shallow discovery surfaces.
4. Prefer updating, linking, summarizing, or promoting existing documents before
   creating another document.
5. Keep important knowledge changes traceable to evidence.
6. Let automation propose organization and promotion while preserving reviewable
   human decisions for important state changes.

## Knowledge Layers

A mature project should distinguish these layers even when they are stored in
the same repository.

| Layer | Purpose | Human starting point |
| --- | --- | --- |
| Current state | What is true enough to act on now | `CURRENT_STATE.md` or an equivalent section |
| Next actions | Bounded work queue and immediate blockers | `NEXT_ACTIONS.md` or issue queue |
| Decisions | Durable choices with rationale | `DECISIONS.md` or decision ledger |
| Deliverables | Outputs meant for inspection or sharing | `DELIVERABLES.md`, gallery, or release packet |
| Logs | Append-only event history | Rotated logs and indexed search |
| Runs | Reproducible execution packets | Run index and manifests |
| Evidence | Inputs, validation results, and audit material | Evidence index or report |
| Adaptive knowledge | Reviewed agent-useful lessons | Canonical store plus projections |

These names are defaults, not mandatory file names. A small repository can
combine several layers in one document. A research-longrun repository should
keep them separate.

## Default Behaviors

Agents and humans should follow these positive defaults.

- Start from the current state, next actions, decisions, and deliverables
  surfaces before reading raw logs.
- Use logs for evidence recovery, audit, and chronological reconstruction.
- Record role, status, source, authority, and follow-up action when creating a
  meaningful document or artifact.
- Preserve run outputs in their source location, then promote human-facing
  outputs to the current discovery surface.
- Classify older material as summarized, replaced, preserved, or retirement
  candidate before removing it from active surfaces.
- Check entrypoints and indexes before creating a new document.
- Prefer stable identifiers and manifests for artifacts that may be moved,
  archived, or shared externally.

## Guardrails

Guardrails are the few rules that should remain explicit because they protect
reference stability and operator trust.

- Important deletion, retirement, canonical promotion, and external delivery
  changes should have a reviewable record.
- Generated or agent-authored material should carry enough authority metadata
  to distinguish it from reviewed project knowledge.
- A log file should not become the only current project entrypoint.
- A deep run folder should not be the only way to find a deliverable intended
  for human inspection.
- A moved file should keep enough transition evidence for old references to be
  interpreted.

## Document Roles

Use role labels consistently. They can be stored in frontmatter, a manifest, an
index row, or a project-specific registry.

| Role | Description |
| --- | --- |
| `entrypoint` | First-read navigation for a project, module, or domain |
| `current_state` | Compact statement of what is currently actionable |
| `next_actions` | Open work, blockers, and handoff-ready steps |
| `decision` | Reviewed choice with rationale and consequences |
| `runbook` | Repeatable procedure |
| `reference` | Stable API, command, data, or architecture facts |
| `evidence` | Material used to support or refute a claim |
| `review` | Point-in-time evaluation of a document, artifact, or run |
| `log` | Append-only chronological record |
| `deliverable` | Output selected for inspection, handoff, or external sharing |
| `failure_record` | Failed direction with evidence and revisit conditions |
| `archive_record` | Material preserved outside the active surface |

## Lifecycle States

Use lifecycle states to avoid treating every file as equally active.

| State | Meaning |
| --- | --- |
| `draft` | Working material that may be incomplete |
| `candidate` | Reviewable material that may become active |
| `active` | Current enough to support work |
| `review_due` | Still visible, but due for review |
| `superseded` | Replaced by a newer record |
| `preserved` | Kept for provenance or reproducibility |
| `summary_kept` | Original may be cold, but summary remains active |
| `retirement_candidate` | Proposed for archive or deletion review |
| `archived` | Removed from active surfaces but retained |

State transitions should prefer explicit records over silent path changes.

## Artifact Manifests

Run folders can be deep and date-based because they serve reproducibility. The
search and delivery path should come from manifests and generated indexes.

A run manifest should answer:

- What produced this run?
- Which project, module, track, and topic does it belong to?
- Which command, commit, config, and input versions were used?
- Which outputs are important?
- Which outputs were promoted for human viewing or delivery?
- What validation was performed?
- What retention state applies?

Minimal fields:

```json
{
  "id": "run_20260530_090000_topic_shortid",
  "project": "project-key",
  "topic": "short-topic",
  "role": "run",
  "status": "active",
  "created_at": "2026-05-30T09:00:00+09:00",
  "source_command": "...",
  "git_commit": "...",
  "inputs": [],
  "outputs": [],
  "promoted_outputs": [],
  "validation": [],
  "retention": "summary_kept"
}
```

The manifest should point to large data instead of copying it when the source
data already has stable provenance.

## Artifact Index

`forager project artifact-index` is the current read-only index surface for
human-facing outputs and Forager profile handoff artifacts:

```bash
forager project artifact-index /path/to/project \
  --project-key my-project \
  --json
```

The command scans `DELIVERABLES.md` references, common output roots such as
`outputs/`, `web/`, `deliverables/`, `previews/`, and `gallery/`, plus matching
profile-local closeout, project-initialization, and Ondesk-capture artifacts.
It emits `artifact_index.v1` with:

- counts for present, missing, review-required, disposal/archive candidate, and
  human-facing entries;
- per-entry role fields: source, kind, retention class, review status, and why
  the artifact matters;
- audit paths in JSON, while human text stays summary-first;
- an explicit read-only authority boundary.

The index can recommend review, archive, or disposal candidates. It does not
delete, move, archive, publish, or accept output as truth. Those actions still
need their own reviewed workflow.

`forager project retention-review` derives a shorter read-only action queue
from the same index:

```bash
forager project retention-review /path/to/project \
  --project-key my-project \
  --json
```

It emits `artifact_retention_review.v1` with summary counts, recommendation
rows, action-required artifacts, and a keep sample. Missing artifacts,
unreferenced human-facing outputs, archive candidates, and disposal candidates
are surfaced as review work, not executed cleanup.

When one review item needs an operator decision, create an approval-only bridge
record:

```bash
forager project retention-request /path/to/project \
  --project-key my-project \
  --path outputs/plot.png \
  --action promote \
  --json
```

Use `--artifact-id <id>` instead of `--path` when the selector could be
ambiguous. The command emits `artifact_retention_approval_request.v1` and
records a pending `maintenance.artifact_cleanup` approval with an
`artifact_retention` approval brief. The approval card states the requested
action, the review reason, why the artifact matters, options for approve/deny/
defer, and the non-authorized boundary.

This bridge still does not mutate files. It does not delete, move, archive,
edit `DELIVERABLES.md`, publish, or accept output as truth. Approval creates a
reviewable decision record for a later explicit follow-up workflow.

After the operator approves the card with `forager offdesk ok <approval-id>`,
consume that decision into a profile-local receipt:

```bash
forager project retention-apply /path/to/project \
  --project-key my-project \
  --approval-id approval_... \
  --json
```

The command emits and writes `artifact_retention_application.v1` under the
profile's `artifact_retention_applications/` directory. It consumes the
approved approval so the same decision cannot be applied twice, records the
requested keep/promote/archive/dispose plan, and keeps
`mutation_performed=false`. Promotion still needs a separate reviewed
`DELIVERABLES.md` update; archive and disposal still need a separate mutation
workflow with snapshot and restore planning.

For a promote receipt, run the deliverables mutation as its own reviewed step:

```bash
forager project retention-promote /path/to/project \
  --project-key my-project \
  --approval-id approval_... \
  --json
```

Without `--reviewed`, the command is a dry run and reports the exact
`DELIVERABLES.md` line it would append. With `--reviewed`, it creates a
mutation snapshot for `DELIVERABLES.md`, verifies that rollback evidence and a
restore plan are available, appends one backtick-linked entry, and writes
`artifact_retention_promotion.v1` under the profile's
`artifact_retention_promotions/` directory. Repeating the command for an
already listed artifact is a no-op.

`forager ondesk review-surface --project-key <project> --json` embeds a compact
projection of the matching profile-local artifact index and retention review.
`forager ondesk prompt-package --project-key <project>` renders their counts in
the morning review section. Use the project-level command when you need the
target repository's deliverables and output roots; use the review surface when
you need the current handoff state for a fresh harness or WebUI.

## Logs

Append-only logs remain useful, but they should be optimized for audit rather
than daily reading.

- Keep logs chronological and concise.
- Rotate or shard logs when they become too large for regular review.
- Generate a current-state summary from reviewed information, not by asking the
  operator to reread the whole log.
- Link log entries to decisions, runs, deliverables, or evidence records when
  they matter beyond chronology.

## Folder Depth

Deep folder structures are acceptable when they represent provenance, date,
run identity, data stage, or retention state. They are a poor primary interface
for human browsing.

Use deep structures for:

- run packets;
- raw and processed data stores;
- retained evidence;
- archived history;
- reproducible experiment outputs.

Use shallow surfaces for:

- current state;
- next actions;
- selected deliverables;
- active decisions;
- galleries and previews;
- human handoff packets.

## Project Profiles

Apply the system by project size.

| Profile | Suitable for | Minimum surfaces |
| --- | --- | --- |
| `light` | Small utility or short task | `README.md`, concise `AGENTS.md` |
| `standard` | Active software project | Entry point, current state, decisions, deliverables |
| `research-longrun` | Long research or agent-heavy project | Current state, decisions, run index, evidence index, deliverables, retention queue, rotated logs |

Use the smallest profile that preserves discovery and review. Upgrade to the
research-longrun profile when a project has persistent runs, evidence, external
deliverables, and agent-created documentation.

## Reviewed Application

`forager project init` produces `GOVERNANCE_SURFACE_HINTS.md` as a read-only
packet artifact. That packet can tell a fresh harness which governance surfaces
are missing without changing the target repository.

`forager project apply-governance-hints` is the approval-gated bridge from
those hints to real files:

```bash
forager project apply-governance-hints /path/to/project \
  --project-key my-project \
  --reviewed
```

The command is intentionally narrow:

- without `--reviewed`, it is a dry run and writes nothing;
- with `--reviewed`, it creates only missing surfaces;
- existing governance files are skipped, not overwritten;
- `--surface current-state`, `--surface next-actions`, `--surface decisions`,
  and `--surface deliverables` can limit the scope;
- after applying surfaces, run `forager project audit-docs` to verify that the
  target project has a coherent governance entrypoint.

For adaptive wiki state, the canonical files remain profile-local JSON. The
human markdown vault is a projection and should be refreshed from the canonical
store:

```bash
forager -p <profile> offdesk wiki export-markdown
```

`forager project audit-docs --adaptive-profile-dir <profile-dir>` checks
whether `wiki-vault/index.md` is missing or older than canonical adaptive wiki
state and emits a focused `reexport_adaptive_wiki_projection` recommendation.

## Audit Checklist

A basic audit should report:

- entrypoint documents that point to oversized logs as the main starting point;
- active documents without role, status, source, or review metadata;
- deliverables that exist only inside deep run folders;
- run folders without manifests;
- active decisions that are superseded by newer decisions;
- archived or moved files without transition records;
- failure records that are not discoverable from an active index;
- generated artifacts that are treated as reviewed deliverables without
  validation evidence.

The audit should classify findings and suggest actions before moving, deleting,
or rewriting files.

## Audit Command

Forager provides a project-level audit for documentation and human-facing
artifact governance:

```bash
forager project audit-docs /path/to/project \
  --audit-profile research-longrun \
  --json-out target/documentation-governance-audit/project.json \
  --md-out target/documentation-governance-audit/project.md
```

Use `--audit-profile standard` for ordinary software repositories and
`--audit-profile research-longrun` for research projects with persistent runs,
deliverables, logs, and agent-created documents. Add
`--adaptive-profile-dir <profile-dir>` when the project has a Forager adaptive
wiki store that should be checked against its markdown projection.

The audit checks the shallow governance surfaces, entrypoint references,
deliverable links, retention-managed output records, latest-alias integrity,
decision source links, current-state freshness, large logs, and optional
adaptive wiki projection freshness. It also emits a focused `recommendations`
array that converts raw findings into operator actions.

Interpret the severities conservatively:

- `error`: a referenced path or required artifact is missing.
- `warn`: the project can continue, but the current surface may mislead an
  operator or future agent.
- `info`: a review queue signal, such as human-facing output candidates that
  were not promoted to `DELIVERABLES.md`.

An `info` finding does not mean every candidate should be linked. It means the
largest or most visible candidates should be reviewed, and only the selected
inspection or handoff artifacts should be promoted.

Recommendations are intentionally smaller than the raw audit summary. For
deliverables, the audit reports counts plus a short review sample, then asks
the operator to either promote selected outputs to `DELIVERABLES.md` or record
non-active outputs in `RETENTION_REVIEW.md`. Markdown reports keep this focused
view and point operators to `--json` or `--json-out` for the complete machine
summary.

The repository script `scripts/audit_documentation_governance.py` remains as a
local reference implementation and migration fallback. New automation should
prefer `forager project audit-docs`.

## Relation To Adaptive Wiki

Adaptive wiki follows the same governance boundary with stricter execution
safety. Candidate observations are not durable behavior changes. Promoted
entries can be projected to agents as compact, scoped context, while the human
projection can carry summaries, evidence, counterexamples, and lifecycle
status.

Project documentation and adaptive wiki can share vocabulary for role, state,
scope, source, and authority. They should not share one writable source of
truth until conflict handling and review semantics are explicit.
