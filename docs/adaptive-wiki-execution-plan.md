# Adaptive Wiki Execution Plan

This plan turns the Hermes adaptive-memory lessons into a Forager-native
Offdesk implementation. Hermes is a reference, not a dependency. The target is
an inspectable adaptive loop where durable behavior changes only after evidence
and operator review.

## Hermes Lessons To Keep

Hermes does not implement one monolithic wiki. The useful structure is a set of
separate knowledge surfaces with different lifecycles:

- `MEMORY.md` and `USER.md`: compact declarative facts and user preferences.
- `MemoryProvider`: pluggable long-term recall with lifecycle hooks such as
  prefetch, turn sync, session switch, compression, and memory-write mirroring.
- Skills: procedural memory stored as `SKILL.md` plus optional `references/`,
  `templates/`, and `scripts/`.
- Curator: background maintenance for agent-created procedural knowledge.
- `llm-wiki`: markdown wiki pattern with `raw/`, `SCHEMA.md`, `index.md`,
  `log.md`, source hashes, provenance markers, confidence, contested pages, and
  drift checks.

The Forager translation is not "copy Hermes memory." It is:

- declarative facts and preferences become scoped adaptive wiki entries;
- procedures and failure patterns become reviewable runbook-style entries;
- raw source material stays immutable and evidence-backed;
- runtime context receives a small AI projection, never the raw human wiki;
- every durable behavior change is auditable and operator-governed.

The follow-up Hermes adaptive knowledge benchmark is recorded in
[`hermes-adaptive-knowledge-benchmark.md`](hermes-adaptive-knowledge-benchmark.md).
It covers the remaining memory lifecycle, `llm-wiki`, curator, and skills
patterns.

## Non-Negotiable Invariants

- Candidate observation never changes runtime behavior by itself.
- Promoted entries can be projected as context, but v0/v1 do not auto-apply
  command, workdir, provider, model, launch spec, or approval mutations.
- AI projection excludes human summaries, raw transcripts, candidates,
  deprecated entries, and unredacted secrets.
- Human projection may include governance context, but it must be sanitized.
- Every promoted entry keeps enough evidence refs to explain why it exists.
- Background review may recommend changes, but it must not directly rewrite
  durable behavior without operator approval.

## Target Architecture

```text
Offdesk event
  -> learning signal classifier
  -> adaptive_wiki_candidates.json
  -> operator review commands / dashboard-ready JSON
  -> adaptive_wiki_entries.json
  -> scoped AI projection
  -> runtime context or preflight metadata
  -> usage audit record
  -> lint / stale / contradiction review
```

Canonical JSON remains the source of truth. Markdown wiki export is a human
view, not a second writable source of truth in the first pass.

## Data Model Additions

The current v0 store has entries, candidates, scope, status, activation mode,
agent modes, confidence, evidence refs, projections, candidate provenance,
review audit records, and runtime usage audit records. Entry-level aggregate
counters remain future additions.

Implemented candidate fields:

- `signal_kind`: `operator_correction | approval_denial | rollback |
  repeated_failure | manual_patch | explicit_preference | imported_doc`
- `origin`: `operator_explicit | runtime_observed | background_review |
  imported`
- `source_refs`: structured refs such as `task:<id>`, `approval:<id>`,
  `audit:<id>`, `diff:<id>`, `doc:<path>#<hash>`
- `source_hashes`: optional hashes for immutable source payloads or exported
  markdown snapshots
- `suggested_scope`: proposed scope before operator review
- `review_reason`: why the candidate is being surfaced
- `last_seen_at`: separate from `updated_at` so repeated observation can be
  counted without rewriting the conceptual claim

Future entry fields:

- `promoted_by`: operator or system actor that approved the entry
- `promoted_at`: timestamp for governance
- `last_used_at`: last time the AI projection included the entry
- `usage_count`: number of projected uses
- `review_after`: existing field, enforced by lint/projection policy
- `contested`: true when counterexamples exist or claims conflict
- `contradictions`: entry ids or page slugs that conflict with this entry

Usage audit records:

```json
{
  "id": "wiki_usage_...",
  "entry_id": "wiki_entry_...",
  "task_id": "task_...",
  "request_id": "request_...",
  "project_key": "project-a",
  "artifact_kind": "report",
  "agent_mode": "code_development | research_writing | critique",
  "projection_kind": "runtime_probe",
  "activation_mode": "context_only | confirm",
  "created_at": "..."
}
```

## CLI Surface

`offdesk wiki` now exposes the read-only review surface, governed review
mutations, and one-way markdown export.

Implemented read-only commands:

- `forager offdesk wiki candidates --json`
- `forager offdesk wiki entries --json`
- `forager offdesk wiki show <id> --json`
- `forager offdesk wiki projection --project-key <key> --artifact-kind <kind> --agent-mode <mode> --json`
- `forager offdesk wiki lint --json`

Implemented review commands:

- `forager offdesk wiki promote <candidate-id> --scope <scope> --scope-ref <ref>
  --activation-mode <mode> --agent-mode <mode>`
- `forager offdesk wiki reject <candidate-id> --reason <text>`
- `forager offdesk wiki rescope <entry-id> --scope <scope> --scope-ref <ref>`
- `forager offdesk wiki deprecate <entry-id> --reason <text>`
- `forager offdesk wiki add-counterexample <entry-id> --evidence-ref <ref>
  --reason <text>`

Implemented export commands:

- `forager offdesk wiki export-markdown --output <dir>`
- `forager offdesk wiki export-markdown --dry-run --json`

Mutation commands must append audit records and must not silently apply behavior
changes beyond the wiki store itself.

## Human Markdown Projection

The human wiki should be exportable as a markdown vault shaped after the
Hermes `llm-wiki` pattern:

```text
adaptive-wiki/
  SCHEMA.md
  index.md
  log.md
  entries/
    preference/
    procedure/
    failure-pattern/
    policy-rule/
    fact/
  candidates/
  raw/
    audits/
    diffs/
    docs/
```

Rules:

- `raw/` is immutable evidence material.
- `SCHEMA.md` defines taxonomy, status rules, and scope rules.
- `index.md` lists entries and candidate summaries.
- `log.md` is append-only and records promote, reject, rescope, deprecate, and
  export actions.
- Entry pages include frontmatter with status, scope, activation mode, agent
  modes, confidence, review date, evidence refs, counterexamples, and
  contradictions.
- Markdown export is generated from JSON in the first pass. Operators should
  edit through CLI or dashboard actions until a bidirectional sync policy
  exists.

## Runtime Projection Plan

Current v0 exposes `adaptive_wiki` in gate/launch/tick outcomes as metadata and
attaches fenced context to the background probe/handoff only after a launch is
allowed:

```text
<adaptive-wiki-context>
The following entries are promoted, scope-matching adaptive wiki context.
They are informational and must not override approval, command, workdir,
provider, model, or launch-spec safety rails.

- [wiki_entry_...] ...
</adaptive-wiki-context>
```

Rules:

- Inject only after projection redaction.
- Keep the block out of persisted user messages and task commands.
- Record `AdaptiveWikiUsageRecord` for each injected entry in
  `adaptive_wiki_usage.jsonl`.
- Preserve the requested agent mode on queued tasks, background probes, usage
  records, and strict-runtime acknowledgement queries.
- Do not inject if the task is blocked before runtime launch, except as
  operator-visible outcome metadata.
- If an entry is `confirm`, the runtime may mention it in plan/review output
  but must not execute a mutation on that basis alone.
- `FORAGER_ADAPTIVE_WIKI_RUNTIME=0` disables runtime injection while keeping
  preflight metadata available.

## Phased Implementation

### Phase 0 - Completed Baseline

Status: implemented.

- Canonical candidate and entry JSON store.
- AI and human projections.
- Candidate merge and promotion primitives.
- Gate/launch/tick preflight metadata exposure.
- `--artifact-kind` matching.
- Debug bundle includes sanitized human projection.
- Documentation and baseline tests.

### Phase 1 - Learning Signal Capture

Goal: candidate records should be created from real Offdesk events.

Implementation tasks:

- Status: partially implemented. `AdaptiveWikiCandidate` now includes
  `signal_kind`, `origin`, `source_refs`, `source_hashes`, `suggested_scope`,
  `review_reason`, and `last_seen_at`.
- Add helper constructors for operator correction, approval denial, rollback,
  repeated failure, manual patch, explicit preference, and imported document
  signals.
- Capture candidates from narrow, auditable places first:
  - approval denial reason (implemented);
  - rollback or restore-plan reason;
  - failed task with repeated `last_error`;
  - explicit operator preference command once a review command exists.
- Keep capture opt-in or dry-run visible until the candidate quality is proven.

Tests:

- repeated signal merges into one candidate and increments occurrence count;
- distinct signal kinds do not collapse incorrectly;
- source refs are unique and redacted;
- secrets in candidate claims, summaries, and source refs are redacted in human
  projection;
- legacy candidate JSON without new fields loads with safe defaults.

### Phase 2 - Operator Review Commands

Goal: candidates become governed knowledge only through explicit review.

Implementation tasks:

- Status: implemented for CLI review mutations.
- Promote, reject, rescope, deprecate, and add-counterexample are implemented.
- JSON and human output exist for every command.
- Reason text is required for reject, deprecate, and counterexample actions.
- Review mutations append `adaptive_wiki_audit.jsonl` records.
- CLI docs are regenerated with `cargo run -p xtask -- gen-docs`.

Tests:

- promote removes candidate and creates promoted entry;
- reject removes or marks candidate without creating an entry;
- rescope changes only scope fields and updated timestamp;
- deprecate removes entry from AI projection;
- confirm-mode entries stay context-only at runtime;
- command output is redacted and stable under `--json`.

### Phase 3 - Runtime Context Injection

Goal: promoted scoped knowledge can influence planning without bypassing rails.

Implementation tasks:

- Status: implemented for background probe/handoff context.
- Runtime projection builder returns a fenced context block.
- Launch and tick dispatch attach context only to `BackgroundProbe`, not to
  command strings, workdirs, provider/model routing, launch specs, or approvals.
- Usage audit entries are appended for every injected entry.
- Projection ids are stored on task views and debug bundle state.
- `FORAGER_ADAPTIVE_WIKI_RUNTIME=0` disables runtime wiki injection while
  keeping preflight metadata.

Tests:

- context block contains only promoted, scope-matching, redacted entries;
- command, workdir, launch spec, provider, model, and approval decision are
  unchanged by projection;
- usage records are written for injected entries;
- no usage record is written for out-of-scope or deprecated entries;
- disabled runtime injection still leaves preflight metadata available.

### Phase 4 - Human Markdown Export

Goal: operators can inspect the adaptive wiki as a readable knowledge base.

Implementation tasks:

- Status: implemented as a one-way sanitized export.
- Generate `SCHEMA.md`, `index.md`, `log.md`, entry markdown pages, and
  candidate markdown pages from canonical JSON.
- Include evidence refs, confidence, contested state, review dates,
  counterexamples, source refs, source hashes, and review reasons.
- Future pass: add raw evidence snapshots and drift checks for their source
  hashes.
- Keep export one-way until conflict resolution and import semantics are
  designed.

Tests:

- export is deterministic;
- generated markdown is sanitized;
- index lists promoted and deprecated entries;
- candidates appear separately from entries;
- exported file hashes are deterministic in the export report.

### Phase 5 - Lint, Staleness, And Conflict Policy

Goal: prevent stale or contradictory adaptive knowledge from hardening.

Implementation tasks:

- Status: basic lint implemented. It currently checks missing ids, empty
  runtime instructions, promoted entries without evidence, expired
  `review_after`, inferred-confidence promoted entries, promoted entries with
  counterexamples, candidates without source refs, stale candidates, zero
  occurrence counts, empty candidate claims, and legacy unknown signal kinds.
- Add future lint checks for contradictory entries, orphaned candidates, and raw
  source hash drift.
- Add a projection policy for `review_after`:
  - first pass: include but flag in human output;
  - stricter future pass: exclude expired entries from AI projection unless
    explicitly renewed.
- Add conflict detection over same kind/scope/scope_ref with contradictory
  claims or counterexamples.

Tests:

- lint flags stale entries;
- lint flags promoted entries with no evidence refs;
- contested entries are visible in human projection;
- deprecated entries never appear in AI projection;
- review-expired policy is covered by tests.

### Phase 6 - Episode Evaluation

Goal: validate adaptive behavior, not raw recall.

Status: deterministic projection reports and live artifact trace reports
implemented.

Episode shape:

1. A task risks or repeats a known mistake.
2. An operator correction creates a candidate.
3. Repeated evidence recommends review.
4. Operator promotes with bounded scope.
5. Similar in-scope task receives projection and avoids the mistake.
6. Out-of-scope task does not receive the entry.
7. Deprecated entry no longer appears.

Outputs:

- Implemented now: `adaptive_wiki_episode_reports/<timestamp>/episode.json`.
- Implemented now: `adaptive_wiki_episode_reports/<timestamp>/EPISODE.md`.
- Implemented now: projection transcript with in-scope and out-of-scope entry
  ids.
- Implemented now: scope leakage, deprecated projection, review-expired
  projection, and evidence-trace summary.
- Implemented now: JSONL episode trace assembled from existing durable task,
  probe, resume, usage, candidate, and audit artifacts.
- Implemented now: candidate and entry snapshots are captured on new promotion
  audit records and can be replayed through promotion-chain reports.
- Implemented now: first-class correction records are captured in
  `adaptive_wiki_corrections.jsonl` and used by trace/recurrence reports before
  legacy candidate-derived fallback.

Acceptance criteria:

- Implemented now: target entry is projected for the in-scope query.
- Implemented now: target entry does not leak into the out-of-scope query.
- Implemented now: deprecated entries are absent from AI projection.
- Implemented now: review-expired projected entries are report failures rather
  than silent execution context.
- Implemented now: every projected entry must have an evidence trail.
- Implemented now: report generation does not mutate safety rails.
- Implemented now: live trace generation is report-only and redacted.
- Implemented now: correction recurrence is counted before and after promotion.
- Implemented now: promotion evidence chains expose promotion-time snapshots
  without mutating canonical wiki state.
- Implemented now: richer recurrence attribution uses first-class correction
  records and avoids double-counting the same candidate correction.

### Phase 7 - Curator Review Reports

Goal: surface stale, conflicting, or overly narrow knowledge for operator
review without autonomous mutation.

Implementation tasks:

- Status: implemented for deterministic first-pass reports.
- Add recommendation-only review reports under
  `adaptive_wiki_review_reports/<timestamp>/`.
- Use entries, candidates, audit records, usage records, and lint as inputs.
- Future pass: add task failure evidence, resume evidence, and
  rollback/provider evidence as inputs.
- Emit proposed operations such as promote, reject, rescope, deprecate,
  add-counterexample, renew-review, split, and merge.
- Keep the first version deterministic and recommendation-only; LLM-assisted
  review can be added later only if its output stays a proposal.

Tests:

- report generation is read-only;
- every proposal includes evidence refs and subject ids;
- reports are redacted;
- no proposed operation is applied without a separate command or approval.

### Phase 8 - Procedure Runbooks

Goal: let procedural knowledge become reusable without becoming an autonomous
tool mutation layer.

Implementation tasks:

- Status: implemented for first-pass runbook metadata.
- Keep procedures as promoted adaptive wiki entries first.
- Add optional support refs for generated `references/`, `templates/`, and
  `scripts/` files in markdown export.
- Link procedures to capability ids and required artifact kinds.
- Keep runtime projection compact; load support material only through an
  allowed runtime path after scheduler approval.

Tests:

- procedure entries do not authorize command, workdir, provider, model, launch
  spec, or approval changes;
- capability/artifact mismatch blocks before runtime launch;
- support refs are exported for humans but omitted from compact AI projection
  unless explicitly requested by an allowed flow.

## First Implementation Slice

Status: implemented.

This code slice covered Phase 1 plus the read-only part of Phase 2:

1. Extend candidate schema with signal provenance fields.
2. Add read-only `offdesk wiki candidates|entries|show|projection --json`.
3. Add `offdesk wiki lint --json` with basic checks.
4. Capture one conservative candidate source: approval denial reason.
5. Add tests and regenerate CLI docs.

This created the read-only review surface before adding governed mutation
commands or runtime context injection.

## Second Implementation Slice

Status: implemented.

This code slice completed the governed review mutation surface:

1. Add store primitives for promote, reject, rescope, deprecate, and
   add-counterexample.
2. Add append-only `adaptive_wiki_audit.jsonl` records for review mutations.
3. Expose mutation commands through `forager offdesk wiki`.
4. Keep command JSON/human output sanitized through human projection structs.
5. Add unit and CLI regression tests and regenerate CLI docs.

## Third Implementation Slice

Status: implemented.

This code slice completed Phase 3 for the current Offdesk background runtime
surface:

1. Add `AdaptiveWikiRuntimeProjection` and `AdaptiveWikiUsageRecord`.
2. Attach fenced, redacted wiki context to launched background probes and
   remote-worker handoffs only after scheduler approval.
3. Persist projected entry ids on background probes and task views.
4. Append `adaptive_wiki_usage.jsonl` records for launched runtime projections.
5. Add `adaptive_wiki_usage` to sanitized debug bundles.
6. Add an environment kill switch:
   `FORAGER_ADAPTIVE_WIKI_RUNTIME=0`.
7. Add unit and CLI regression tests proving commands, workdirs, launch specs,
   provider/model choices, and approval decisions are unchanged by projection.

## Fourth Implementation Slice

Status: implemented.

This code slice completed the first human vault and governance lint expansion:

1. Add `forager offdesk wiki export-markdown --output <dir>` with
   `--dry-run --json`.
2. Generate `SCHEMA.md`, `index.md`, `log.md`, entry pages, candidate pages,
   and empty `raw/audits`, `raw/diffs`, and `raw/docs` directories.
3. Keep export one-way from canonical JSON and sanitize all markdown content.
4. Add deterministic file hashes to the export report.
5. Surface contested entries in human projection and markdown pages.
6. Extend lint for contested promoted entries, inferred promoted entries, and
   stale candidates.

## Fifth Implementation Slice

Status: implemented.

This code slice completed the deterministic curator review report surface:

1. Add `forager offdesk wiki review --json` and `--dry-run --json`.
2. Read entries, candidates, lint, usage records, and audit records.
3. Generate recommendation-only proposals for promote, reject, deprecate,
   renew-review, split, merge, and add-counterexample cases.
4. Write `adaptive_wiki_review_reports/<timestamp>/report.json` and
   `REPORT.md` only when not in dry-run mode.
5. Keep every proposal evidence-backed, redacted, and non-mutating.

## Sixth Implementation Slice

Status: implemented.

This code slice completed the first procedure runbook surface:

1. Add procedure entry metadata: `support_refs`, `capability_ids`, and
   `required_artifact_kinds`.
2. Add `forager offdesk wiki update-runbook <entry-id>` with governed audit.
3. Restrict runbook updates to `kind=procedure` entries.
4. Show runbook metadata in human projection and markdown export.
5. Keep runbook support refs out of compact AI projection and runtime wiki
   context.
6. Add lint checks for procedure entries without runbook links, artifact kinds
   without capability ids, and runbook links attached to non-procedure entries.

## Seventh Implementation Slice

Status: implemented.

This code slice completed the first deterministic episode evaluation surface:

1. Add `forager offdesk wiki evaluate-episode <entry-id>` with in-scope and
   out-of-scope query inputs.
2. Generate `adaptive_wiki_episode_reports/<timestamp>/episode.json` and
   `EPISODE.md` when not in dry-run mode.
3. Compare in-scope and out-of-scope AI projections for a target entry.
4. Flag scope leakage, deprecated projection, review-expired projection, and
   projected entries without evidence refs.
5. Keep episode evaluation report-only and non-mutating.
6. Add unit and CLI regression tests for target scope matching, stale
   activation, evidence completeness, redaction, and dry-run behavior.

## Eighth Implementation Slice

Status: implemented.

This code slice completed the first live episode trace surface:

1. Add `forager offdesk wiki episode-trace` with request/task/project/artifact
   and entry filters.
2. Read existing durable artifacts: `offdesk_tasks.json`,
   `background_runs.json`, `task_resume_state.json`,
   `adaptive_wiki_usage.jsonl`, `adaptive_wiki_candidates.json`, and
   `adaptive_wiki_audit.jsonl`.
3. Generate `adaptive_wiki_episode_traces/<timestamp>/report.json`,
   `trace.jsonl`, and `REPORT.md` when not in dry-run mode.
4. Emit events for task lifecycle, projection attachment, runtime usage,
   operator-correction candidates, promotion audits, counterexamples,
   resume-pending states, and rollback-derived candidates.
5. Keep trace generation report-only, redacted, and non-mutating.
6. Add CLI regression tests proving task, usage, candidate, audit, probe, and
   resume evidence can be linked without leaking secrets.

## Ninth Implementation Slice

Status: implemented.

This code slice completed the first correction recurrence evaluator:

1. Add `forager offdesk wiki evaluate-recurrence <entry-id>`.
2. Determine the promotion boundary from promotion audit records, falling back
   to entry `created_at`.
3. Use the target entry scope to gather live episode events from existing
   durable artifacts.
4. Count pre/post-promotion operator corrections, post-promotion usage,
   task failures, resume-pending states, and counterexamples.
5. Generate `adaptive_wiki_recurrence_reports/<timestamp>/report.json`,
   `recurrence.jsonl`, and `REPORT.md` when not in dry-run mode.
6. Keep recurrence evaluation report-only, redacted, and non-mutating.

## Tenth Implementation Slice

Status: implemented.

This code slice hardened promotion evidence chains:

1. Add optional `candidate_snapshot` and `entry_snapshot` fields to
   `AdaptiveWikiAuditRecord` with serde defaults for older audit JSONL rows.
2. Capture redacted human candidate and entry snapshots when
   `forager offdesk wiki promote` appends a promotion audit record.
3. Add `forager offdesk wiki promotion-chain <entry-id>`.
4. Generate `adaptive_wiki_promotion_chains/<timestamp>/report.json`,
   `chain.jsonl`, and `REPORT.md` when not in dry-run mode.
5. Report missing promotion audits or missing snapshots explicitly for legacy
   entries instead of reconstructing promotion-time state from current state.
6. Keep promotion-chain evaluation report-only, redacted, and non-mutating.

## Eleventh Implementation Slice

Status: implemented.

This code slice promoted correction evidence to a first-class durable contract:

1. Add `AdaptiveWikiCorrectionRecord` and
   `adaptive_wiki_corrections.jsonl` with serde defaults for older or sparse
   rows.
2. Append a sanitized correction record whenever an operator-correction
   candidate is recorded.
3. Add `forager offdesk wiki corrections --json` and include correction rows in
   read-only debug bundles.
4. Teach live episode traces to emit correction-record events before legacy
   candidate-derived correction fallback.
5. Teach recurrence reports to count loaded correction records and avoid
   double-counting candidates that already have correction rows.
6. Keep all correction list, trace, recurrence, and debug-bundle output
   redacted, report-only, and non-mutating.

## Twelfth Implementation Slice

Status: implemented.

This code slice connected the evidence graph back into curator proposals:

1. Load `adaptive_wiki_corrections.jsonl` during
   `forager offdesk wiki review`.
2. Add `correction_records_checked` to review summaries and markdown reports.
3. Propose `rescope` review when post-promotion correction records target a
   promoted entry.
4. Propose `renew_review` when a promoted entry has no promotion audit or has a
   promotion audit without candidate/entry snapshots.
5. Preserve the older lint, candidate, usage, and audit-derived proposals for
   legacy profiles.
6. Keep the curator evidence graph recommendation-only, redacted, and
   non-mutating.

## Thirteenth Implementation Slice

Status: implemented.

This code slice added a durable proposal lifecycle event log:

1. Add `AdaptiveWikiReviewProposalEventRecord` and
   `adaptive_wiki_review_events.jsonl`.
2. Add `forager offdesk wiki proposal-events --json` for read-only inspection.
3. Add `forager offdesk wiki record-proposal-event <proposal-id>` with
   `--decision accepted|rejected|superseded`.
4. Include review proposal events in `forager offdesk debug-bundle`.
5. Count review proposal events in curator review summaries.
6. Sanitize event reasons and evidence refs before storage, and keep proposal
   lifecycle events audit-only rather than applying proposed mutations.

## Fourteenth Implementation Slice

Status: implemented.

This code slice made curator review output proposal-aware:

1. Annotate each current review proposal with its latest lifecycle event when a
   matching event exists.
2. Count open proposals and accepted/rejected/superseded proposal decisions in
   review summaries.
3. Include lifecycle state in JSON, markdown, and human review output.
4. Redact lifecycle reasons and evidence refs in review reports, including
   legacy event rows that may not have been sanitized before storage.
5. Keep lifecycle annotation report-only; it does not apply, suppress, or
   mutate curator proposals.

## Fifteenth Implementation Slice

Status: implemented.

This code slice made proposal lifecycle decisions queue-aware:

1. Mark a lifecycle decision stale when the proposal subject changed after the
   latest decision.
2. Mark a lifecycle decision stale when timestamped proposal evidence
   (`usage:`, `correction:`, `audit:`, `entry:`, or `candidate:` refs) is newer
   than the latest decision.
3. Expose `stale` and `stale_evidence_refs` on proposal lifecycle annotations.
4. Count stale lifecycle decisions in review summaries and count those
   proposals as open for operator review.
5. Preserve recommendation-only behavior: stale detection does not accept,
   reject, suppress, or mutate wiki rows.

## Sixteenth Implementation Slice

Status: implemented.

This code slice added lifecycle-aware review filtering:

1. Add `AdaptiveWikiReviewQueueFilter` with `all`, `active`, `decided`, and
   `stale` views.
2. Add `forager offdesk wiki review --active-only` for open proposals and stale
   decisions that need renewed operator review.
3. Add `forager offdesk wiki review --decided-only` for non-stale accepted,
   rejected, and superseded proposals.
4. Add `forager offdesk wiki review --stale-only` for stale lifecycle
   decisions.
5. Include `filtered_out_proposals` in review summaries so filtered JSON,
   markdown, and human output remain auditable.
6. Keep the filter flags mutually exclusive and report-only; filtering does not
   mutate wiki rows or lifecycle event logs.

## Seventeenth Implementation Slice

Status: implemented.

This code slice added proposal event closure helpers:

1. Add `forager offdesk wiki accept-proposal <proposal-id> --reason <text>`.
2. Add `forager offdesk wiki reject-proposal <proposal-id> --reason <text>`.
3. Add `forager offdesk wiki supersede-proposal <proposal-id> --reason <text>`.
4. Resolve the current review proposal before recording the event, and copy its
   action, subject, and evidence refs into the lifecycle event.
5. Sanitize copied proposal evidence and extra `--evidence-ref` values before
   storage.
6. Block duplicate closure for non-stale decided proposals unless
   `--allow-decided` is passed.
7. Keep the helpers audit-only; they append lifecycle events but do not apply
   curator mutations.

## Eighteenth Implementation Slice

Status: implemented.

This code slice added proposal-to-mutation handoff previews:

1. Add `forager offdesk wiki proposal-handoff <proposal-id> --json`.
2. Resolve the current review proposal without writing report files or
   lifecycle events.
3. Return `ready` with an exact governed mutation command when a proposal has a
   safe command.
4. Return `manual_required` when exact execution still needs operator-selected
   scope, evidence, split, merge, or renewal policy.
5. Return `blocked_by_decision` when a proposal already has a non-stale
   lifecycle decision.
6. Keep the handoff read-only; it previews but never executes mutation
   commands.

## Nineteenth Implementation Slice

Status: implemented.

This code slice added proposal handoff input contracts:

1. Extend `forager offdesk wiki proposal-handoff <proposal-id> --json` with
   `required_inputs` for manual proposals.
2. Return `mutation_options` with command templates for safe operator-selected
   paths such as rescope, deprecate, and add-counterexample.
3. Keep `ready` and `blocked_by_decision` previews free of manual input
   contracts so automation can distinguish exact commands from advisory
   templates.
4. Document unsupported candidate evidence attachment as a manual contract
   rather than inventing a mutation command that the current CLI cannot apply.
5. Keep the contract read-only and sanitized; no lifecycle event or wiki row is
   written by previewing it.

## Twentieth Implementation Slice

Status: implemented.

This code slice added parameterized handoff previews:

1. Extend `forager offdesk wiki proposal-handoff <proposal-id>` with
   `--mutation`, `--scope`, `--scope-ref`, `--evidence-ref`,
   `--deprecated-entry-id`, and `--reason`.
2. Produce a `ready` governed command when an entry-scoped manual proposal has
   all required inputs for rescope, deprecate, add-counterexample, or duplicate
   deprecation.
3. Keep incomplete parameterized requests as `manual_required` and preserve the
   input contract in JSON.
4. Redact operator-supplied evidence refs and reasons before echoing them in a
   previewed command.
5. Keep preview-only semantics: no lifecycle events, wiki rows, reports, or
   approvals are written by parameterized handoff preview.

## Twenty-First Implementation Slice

Status: implemented.

This code slice added proposal handoff receipts:

1. Add `forager offdesk wiki proposal-receipt <proposal-id> --audit-id <id>
   --event-id <id> --command <cmd> --json`.
2. Link a read-only handoff preview command to the later adaptive wiki mutation
   audit and lifecycle proposal event.
3. Hash the sanitized preview command so receipts can be compared without
   storing raw operator text or secrets.
4. Validate that the audit action/target and lifecycle event metadata match
   the proposal subject.
5. Fall back to lifecycle-event proposal metadata when the current proposal is
   gone after a successful mutation.
6. Keep receipts read-only; no wiki rows, audit records, lifecycle events,
   reports, approvals, or tasks are written.

## Twenty-Second Implementation Slice

Status: implemented.

This code slice hardened proposal receipt operator workflow:

1. Add `--export` and `--output <path>` to `proposal-receipt`.
2. Write sanitized receipt artifacts without overwriting existing files.
3. Keep export as an explicit audit artifact only; receipt generation still
   does not mutate wiki rows, audit logs, lifecycle events, approvals, reports,
   or tasks.
4. Expand failed receipt checks with actual-vs-expected details so operators
   can see whether the audit id, lifecycle event, proposal subject, or mutation
   target is mismatched.
5. Document the full review -> handoff -> lifecycle decision -> mutation ->
   receipt workflow.

## Twenty-Third Implementation Slice

Status: implemented.

This code slice added projection quality policy reports:

1. Add `AdaptiveWikiProjectionBudget` with default caps for selected entries,
   estimated context characters, and per-instruction characters.
2. Add `AdaptiveWikiProjectionReport` with `selected`, `rejected`, `summary`,
   and budget metadata.
3. Keep `ai_projection()` backward-compatible by returning only selected
   entries from the default report.
4. Order projection candidates by scope specificity, confidence, evidence
   count, recency, activation mode, and stable id.
5. Reject scope-matching promoted entries with empty projected instruction or
   budget overflow, and preserve the reason in report JSON.
6. Add `forager offdesk wiki projection --report --json` plus budget override
   flags for operator inspection.

## Twenty-Fourth Implementation Slice

Status: implemented.

This code slice added projection conflict policy:

1. Add structured `AdaptiveWikiProjectionConflict` records to projection
   reports.
2. Detect same-kind, same-scope promoted entries whose normalized projected
   instructions have opposite polarity for the same target.
3. Count conflicts in projection summaries while keeping selected projection
   behavior unchanged.
4. Surface conflicts in human CLI output and JSON report output.
5. Add high-risk curator split proposals for conflicting promoted entries so an
   operator can rescope, split, deprecate, or add counterexample evidence.
6. Keep the policy recommendation-only: it does not mutate entries and does
   not change runtime commands, provider/model choices, launch specs, or
   approval decisions.

## Twenty-Fifth Implementation Slice

Status: implemented.

This code slice added projection-conflict handoff guidance:

1. Detect projection-conflict split proposals in `proposal-handoff`.
2. Show conflict-specific mutation options: `rescope`, `deprecate`, `split`,
   and `add_counterexample`.
3. Allow `--mutation deprecate --reason <text>` for conflict proposals,
   defaulting to the proposal subject.
4. Allow `--deprecated-entry-id <entry>` to deprecate the conflicting entry
   referenced by proposal evidence.
5. Add `--mutation split` as an explicit manual handoff path when one conflict
   proposal needs multiple governed wiki mutations.
6. Keep the handoff read-only; it previews or explains commands but never
   writes lifecycle events, audit records, approvals, or wiki state.

## Twenty-Sixth Implementation Slice

Status: implemented.

This code slice added review-expired projection warnings:

1. Add `AdaptiveWikiProjectionReviewExpired` records to projection reports.
2. Add `summary.review_expired_projected` to count selected entries that are
   past `review_after`.
3. Keep default projection behavior unchanged: expired entries can still be
   selected.
4. Surface review-expired warnings in `projection --report --json` and human
   report output.
5. Keep strict exclusion out of this slice so operators first get an
   inspectable stale-trust report.

## Twenty-Seventh Implementation Slice

Status: implemented.

This code slice added opt-in strict review-expired projection policy:

1. Add `AdaptiveWikiProjectionPolicy` and
   `AdaptiveWikiProjectionReviewExpiredPolicy`.
2. Keep `build_ai_projection_report` backward-compatible by defaulting to
   `review_expired = warn`.
3. Add `build_ai_projection_report_with_policy` and
   `AdaptiveWikiStore::ai_projection_report_with_policy`.
4. Add `--exclude-review-expired` to `offdesk wiki projection`.
5. Reject expired entries with `review_expired_excluded` only when strict
   policy is selected.
6. Keep normal projection and runtime injection on the warn policy.

## Twenty-Eighth Implementation Slice

Status: implemented.

This code slice added warn-vs-strict projection comparison:

1. Add `AdaptiveWikiProjectionComparisonReport` and
   `AdaptiveWikiProjectionComparisonSummary`.
2. Add `build_ai_projection_review_expired_policy_comparison` and
   `AdaptiveWikiStore::ai_projection_review_expired_policy_comparison`.
3. Add `--compare-review-expired-policy` to `offdesk wiki projection`.
4. Return both the default warn projection report and the strict exclusion
   report in one JSON artifact.
5. Summarize `selected_only_in_warn`, `selected_only_in_strict`, and
   `review_expired_excluded` for operator review.
6. Reject combining comparison mode with `--exclude-review-expired` because the
   comparison already includes both policies.

## Twenty-Ninth Implementation Slice

Status: implemented.

This code slice separated preflight projection from runtime injection source:

1. Keep `SchedulerGateOutcome.adaptive_wiki` as the operator-facing default
   warn projection.
2. Add `adaptive_wiki_runtime` and `adaptive_wiki_runtime_policy` as the
   runtime context source.
3. Make the runner build fenced context from `adaptive_wiki_runtime`, not from
   preflight `adaptive_wiki`.
4. Persist runtime policy on background probes that receive wiki context.
5. Record runtime usage with `projection_policy` so debug bundles can explain
   which projection policy fed a live episode.
6. Preserve current behavior by making the runtime source identical to the
   default warn projection until a later strict-mode acknowledgement gate is
   implemented.

## Thirtieth Implementation Slice

Status: implemented.

This code slice added acknowledgement-gated strict runtime projection:

1. Add profile-scoped runtime policy acknowledgement records in
   `adaptive_wiki_runtime_policy_acknowledgements.jsonl`.
2. Add `forager offdesk wiki ack-runtime-policy` to record an acknowledgement
   for strict review-expired exclusion after comparison review.
3. Add `forager offdesk wiki runtime-policy-acks` for read-only inspection.
4. Gate `FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED=exclude` behind an exact
   query, budget, policy, comparison hash, and expiry match.
5. If strict runtime is requested without a valid acknowledgement, leave
   `adaptive_wiki_runtime` empty and record `adaptive_wiki_runtime_decision`
   instead of silently falling back to warn context.
6. Include runtime policy acknowledgements in debug bundles and keep runtime
   usage records tied to the policy that fed the live context.

## Thirty-First Implementation Slice

Status: implemented.

This code slice added explicit acknowledgement scope modes:

1. Add `AdaptiveWikiRuntimePolicyAckScopeMode` with `exact_query` as the
   backward-compatible default.
2. Add `--scope-mode project-artifact` to
   `forager offdesk wiki ack-runtime-policy`.
3. Store the scope mode in acknowledgement records and expose it in human/JSON
   output.
4. Allow project/artifact acknowledgements to apply across request ids only
   when the current runtime comparison has no session-scoped selected,
   rejected, or review-expired entries.
5. Record `applied_project_artifact_acknowledged` when broad reuse is applied
   and `strict_requested_scope_mode_blocked` when session-scoped projection
   requires exact review.
6. Preserve legacy acknowledgement compatibility by defaulting missing
   `scope_mode` fields to `exact_query`.

## Thirty-Second Implementation Slice

Status: implemented.

This code slice added the operator maintenance surface around strict runtime
projection:

1. Add `forager offdesk wiki renew-review-after` to update only an entry's
   `review_after` timestamp while preserving scope, instruction, status, and
   evidence fields.
2. Record `renew_review_after` audit entries so renew-review proposals can be
   closed with a direct governed mutation rather than an indirect rescope or
   counterexample.
3. Add `forager offdesk wiki runtime-policy-ack-report` as a read-only report
   for expired and near-expiry strict runtime acknowledgements.
4. When the report is given a runtime query, include the same strict runtime
   decision used by gate/launch/tick so `strict_requested_scope_mode_blocked`
   is visible before dispatch.
5. Keep acknowledgement reports operator-safe by exposing ids, scope mode,
   query, policy, expiry, status labels, and excluded entry ids only.

## Thirty-Third Implementation Slice

Status: implemented.

This code slice added a read-only attention report for promoted entry review
windows:

1. Add `forager offdesk wiki review-after-report` with the same
   session/project/artifact filters used by human wiki projection.
2. Report promoted entries whose `review_after` is expired or will expire
   within `--near-expiry-hours`.
3. Include summary counts for scoped promoted entries, entries with and without
   `review_after`, expired entries, near-expiry entries, and total attention
   items.
4. Keep report rows operator-safe by exposing id, kind, scope, `review_after`,
   hours until review, status, and a `renew-review-after` command template.
5. Do not include adaptive instructions or raw evidence payloads in the report.

## Thirty-Fourth Implementation Slice

Status: implemented.

This code slice added suggested next actions to strict runtime acknowledgement
reports:

1. Add optional `suggested_action` objects to
   `forager offdesk wiki runtime-policy-ack-report` rows.
2. Suggest comparison and `ack-runtime-policy` command templates for expired,
   near-expiry, stale-comparison, and session-scope-blocked acknowledgements.
3. Keep acknowledgement artifacts append-only: suggested actions never extend
   an old `expires_at`; they tell operators to recompare and append a new
   acknowledgement.
4. For `strict_requested_scope_mode_blocked`, suggest an exact-query
   acknowledgement for the supplied runtime query instead of reusing the broad
   project/artifact scope.
5. Keep command templates operator-safe by using only query scope, projection
   budget, scope mode, and placeholder reason text.

## Thirty-Fifth Implementation Slice

Status: implemented.

This code slice added summary-only adaptive wiki maintenance counters to
`forager offdesk debug-bundle`:

1. Add `adaptive_wiki_runtime_policy_ack_attention_summary` to the sanitized
   debug bundle using the same count logic as `runtime-policy-ack-report`
   without a query-specific decision.
2. Add `adaptive_wiki_review_after_attention_summary` using the same default
   all-scope, 168-hour attention window as `review-after-report`.
3. Keep the added debug-bundle surface summary-only; it contains counts, not
   adaptive instructions, raw evidence payloads, or new mutable state.
4. Keep `debug-bundle` read-only by calculating summaries from existing store
   reads and verifying source wiki/ack files are unchanged.

## Thirty-Sixth Implementation Slice

Status: implemented.

This code slice made adaptive wiki projection aware of the operator-selected
agent mode:

1. Add `code_development`, `research_writing`, and `critique` mode tags to
   entries and candidates, with empty tags meaning shared guidance.
2. Add `--agent-mode` filters to gate, launch, enqueue, wiki list/projection,
   strict runtime acknowledgement, review-after, and episode-evaluation paths.
3. Preserve mode context on queued tasks, background probes, runtime usage
   records, markdown frontmatter, and operator-visible human output.
4. Keep mode matching advisory and contextual only; it does not grant approval,
   alter provider/model routing, rewrite commands, or mutate wiki state.

## Thirty-Seventh Implementation Slice

Status: implemented.

This code slice made the agent-mode default safe for execution:

1. Add an internal projection policy that distinguishes human inspection from
   runtime selection when `--agent-mode` is omitted.
2. Keep human wiki commands broad by default so operators can audit all
   mode-tagged entries and candidates.
3. Use shared-only matching for scheduler gate, launch, and tick runtime queries
   when no agent mode is present.
4. Preserve explicit `--agent-mode` behavior: mode-specific projection includes
   universal entries plus entries tagged for that mode.

## Thirty-Eighth Implementation Slice

Status: implemented.

This code slice added a live LLM prompt harness for adaptive wiki contract
testing:

1. Add `scripts/offdesk_wiki_llm_harness.py` as an opt-in, non-CI harness for
   Ollama-compatible model endpoints.
2. Load real `offdesk wiki projection` output per profile, project,
   artifact-kind, and agent-mode scope instead of hardcoding wiki context.
3. Test code-development, research-writing, critique, JSON verdict, and
   mode-classification contracts with anchor, forbidden-claim, strict JSON, and
   schema checks.
4. Preserve result artifacts under
   `wiki_llm_harness_runs/<timestamp>/results.json`.
5. Add `contract_v3`, which makes evidence state, required anchors, and
   JSON-only schema requirements explicit enough to avoid synonym drift such as
   `not reportable` replacing required `pending`.
6. Track empty model responses and retry them separately from prompt-contract
   failures.
7. Send Ollama-compatible `think:false` by default so contract tests evaluate
   final-answer behavior instead of exhausting the prediction budget on hidden
   reasoning.
8. Send Ollama `format=json` by default for cases with a JSON contract. Live
   qwen3-coder-next TwinPaper probes showed that prompt-only JSON requests can
   still produce malformed JSON, while API-level JSON mode keeps the same
   workload parseable.
9. Add a Module03 root-entrypoint case so the harness tests that supplied
   repository facts are converted into repo-root-relative commands rather than
   basename-only or module-cwd-relative examples.

## Thirty-Ninth Implementation Slice

Status: implemented.

This code slice added a deterministic runtime episode harness for adaptive wiki
execution wiring:

1. Add `scripts/offdesk_runtime_episode_harness.py` as a non-CI CLI harness that
   creates an isolated profile under `target/offdesk-runtime-episode-harness/`.
2. Drive real `forager offdesk gate`, `enqueue`, `tick`, `debug-bundle`, and
   `wiki episode-trace` commands instead of directly calling Rust internals.
3. Verify safe runtime selection defaults: omitted agent mode projects shared
   entries only, while explicit `code-development` projects shared plus matching
   mode-specific entries.
4. Verify launched probes receive fenced runtime context and usage records, with
   out-of-scope and deprecated entries excluded.
5. Verify runtime context is redacted and does not rewrite command, workdir,
   launch spec, provider/model routing, or approval state.
6. Add an optional kill-switch check showing
   `FORAGER_ADAPTIVE_WIKI_RUNTIME=0` preserves preflight projection while
   omitting probe context and usage records.

## Fortieth Implementation Slice

Status: implemented.

This code slice made the live model harnesses easier to use as operator
evidence after the first 30-minute TwinPaper autonomy run:

1. Canonicalize domain-anchor aliases in both live harnesses, including
   `no-option`/`no-op`/`no option`, `singlex`/`single-singlex`,
   `validated_candidate`/`validated candidate`, and
   `restart_stability`/`validated_rate`.
2. Record per-anchor match details and `canonicalization_warnings` so accepted
   aliases do not silently inflate strict pass rates.
3. Classify workload outcomes as `pass`, `pass_with_canonicalization`,
   `contract_anchor_failure`, `json_contract_failure`, `format_failure`,
   `safety_failure`, or `request_failure`.
4. Add a workload-level `assessment` block with `overall_verdict`,
   `operator_risk`, `next_action`, failure-category counts, baseline policy
   coverage, and `false_negative_prevented_count`.
5. Render the same assessment at the top of the TwinPaper workload `REPORT.md`
   so reboot recovery or later `offdesk poll` inspection can distinguish model
   failures from checker false negatives.

## Forty-First Implementation Slice

Status: implemented.

This code slice extended the live LLM harness toward the initial target Offdesk
agent modes without changing Rust persisted mode values:

1. Split each harness case's target `agent_mode` from the currently supported
   Rust `projection_agent_mode`.
2. Keep `development` mapped to `code-development`, `writing` mapped to
   `research-writing`, and `critique` mapped directly to `critique`.
3. Evaluate `planning`, `analysis`, and `maintenance` as target modes while
   loading shared projection context until the Rust adaptive-wiki registry is
   migrated. The later `review` target mode reuses `critique` projection during
   the same migration window.
4. Add planning, analysis, and maintenance cases with explicit authority
   boundaries: no false launched/completed claims, observation-vs-inference
   separation, and approval-gated cleanup/system changes.
5. Add `mode_coverage` and `mode_failures` to the live harness summary so
   operators can see which target modes were actually exercised.

## Forty-Second Implementation Slice

Status: implemented.

This code slice added an experimental Six-Why depth probe to the live LLM
harness so objective, critical reasoning can be measured before becoming a
default Offdesk behavior:

1. Add `--why-depth <0..6>` and `--why-depth-sweep <list>` to
   `scripts/offdesk_wiki_llm_harness.py`.
2. Add a `planning_toy_task_design` case so planner quality can be tested by
   asking the model to design a tiny, inspectable Offdesk task before any
   longer autonomous workload runs. The toy task is deliberately not the
   depth sweep itself; the harness supplies depth `0`, `3`, and `6`
   externally so current-answer reasoning can be compared.
3. Inject a compact `WHY_LADDER` requirement only for non-JSON cases, keeping
   JSON-only verdict and classification contracts parseable.
4. Require each ladder row to expose the why-question, answer, evidence,
   assumption flag, and confidence while allowing early stop when evidence runs
   out.
5. Require `ROOT_CAUSE_HYPOTHESIS`, `MISSING_EVIDENCE`,
   `COUNTERARGUMENTS`, and `RISK_GATE` sections so the ladder tests both
   causal depth and adversarial restraint.
6. Grade ladder shape separately from domain anchors with
   `why_ladder_score`, `why_ladder_observed_depth`, and
   `why_ladder_failures`, while recording `why_ladder_row_pattern` so strict
   row-format failures are not confused with poor causal reasoning.
7. Add `why_depth_summary` to the preserved result artifact so operators can
   compare depth `0`, shallow probes such as `3`, and the full depth `6`
   before choosing a mode-specific default.
8. Add opt-in `--store-response-text` for debugging Six-Why prompt behavior
   when the default 800-character preview is not enough to explain a failure.
9. Add a deterministic semantic quality rubric for `planning_toy_task_design`
   so depth `3` and `6` can be compared on task quality, not just pass rate,
   row count, latency, or answer length. The rubric treats the toy task as
   read-only and planner-only; future file writes or command runs are quality
   failures even if the model does not claim they already happened.

## Forty-Third Implementation Slice

Status: implemented.

This code slice made separate review an explicit Offdesk pipeline contract and
returned planning-mode harness design to target-specific planning quality:

1. Document that autonomous Offdesk work must split draft generation and review
   into separate stages even when this adds latency.
2. Split `review` into its own target mode, distinct from `critique`. Until
   Rust adaptive-wiki mode values are migrated, review-mode harness cases
   project through the existing `critique` wiki context.
3. Define the review stage as read-only by default with its own artifact and
   decision: `proceed`, `revise`, `needs_approval`, or `blocked`.
4. Require review artifacts to name the reviewed artifact, blockers, missing
   evidence, counterarguments, safety gates, approval gates, and the next mode.
5. Add mode-specific review lenses so analysis, development, writing,
   critique, and maintenance plans are not all judged by the same generic
   safety questions.
6. Keep planning-mode cases responsible for review handoff only; the review
   mode case is responsible for blockers, missing evidence, counterarguments,
   gates, and decision.
7. Add planning harness cases for development, analysis, and writing targets
   so planning quality is tested with target-specific lenses rather than a
   single generic safety checklist.

## Forty-Fourth Implementation Slice

Status: implemented.

This code slice added deterministic role-specific benchmark episodes for the
adaptive wiki projection layer:

1. Add `scripts/offdesk_role_episode_harness.py` as a non-CI CLI harness that
   creates an isolated profile under `target/offdesk-role-episode-harness/`.
2. Write a fixture containing shared, code-development, research-writing,
   critique, and deprecated adaptive wiki entries.
3. Drive real `forager offdesk gate inspect.status` commands instead of calling
   Rust internals directly.
4. Verify that no `--agent-mode` receives only shared guidance.
5. Verify that code-development, research-writing, and critique runs receive
   only shared plus matching role-specific guidance.
6. Verify that deprecated entries do not appear in any projection.
7. Preserve `results.json` with the isolated profile path and per-role selected
   ids so failed scope-leakage checks are inspectable.

## Forty-Fifth Implementation Slice

Status: implemented.

This code slice added live model role-specific behavior episodes:

1. Add `scripts/offdesk_role_llm_episode_harness.py` as an opt-in LLM harness
   that creates an isolated profile under
   `target/offdesk-role-llm-episode-harness/`.
2. Use role markers in shared, code-development, research-writing, critique,
   and deprecated adaptive wiki fixture entries.
3. Load execution-facing context through real `forager offdesk gate
   inspect.status` calls so no-mode behavior matches runtime shared-only
   projection.
4. Send the projected context to an Ollama-compatible model with JSON mode on by
   default.
5. Check projection leakage before the model call and response leakage after the
   model call.
6. Require role-appropriate behavior: code stays plan-only, research-writing
   remains pending without RunLog and validation evidence, and critique asks for
   no-option plus singlex before strategy changes.
7. Check that the model keeps adaptive wiki guidance as context only, not
   execution authority, and does not claim completed work without evidence.

## Forty-Sixth Implementation Slice

Status: implemented.

This code slice turned live role episodes into a repeatable quality gate:

1. Add per-result `failure_categories` and `primary_failure_category` to
   `scripts/offdesk_role_llm_episode_harness.py`.
2. Classify failures as projection leakage, missing projection markers, response
   role leakage, missing response markers, authority-boundary failures, false
   completion claims, research overclaims, critique baseline skips, JSON format
   failures, empty responses, and role contract failures.
3. Add summary-level `failure_category_counts`.
4. Add per-case `case_summary` with pass counts, fail counts, pass rate, and
   category counts.
5. Add `quality_gate` with a clean-run verdict and
   `ready_for_long_workload`.
6. Print failure categories on failed stdout lines so repeated runs can be
   watched without opening the JSON artifact first.
7. Document `--iterations 5` as the recommended role-specific preflight before
   relying on adaptive-wiki guidance in a longer Offdesk workload.

## Forty-Seventh Implementation Slice

Status: implemented.

This code slice wired the role quality gate into the TwinPaper Offdesk workload
preparation flow:

1. Add `--role-gate-result <path|latest>` to
   `scripts/prepare_twinpaper_offdesk_task.py`.
2. Add `--review-artifact <path|latest>` for an
   `offdesk_wiki_llm_harness.py` artifact containing
   `review_offdesk_stage_contract`.
3. Record both artifacts under `preflight` in `prepared_task.json`.
4. Require the role-gate `quality_gate.ready_for_long_workload` to be true.
5. Require the review contract to pass and the review decision to be `proceed`
   or `needs_approval`; `blocked` and `revise` stop enqueue.
6. Write `preflight.json`, `preflight_ready`, or `preflight_blocked` in the
   workload directory.
7. Guard both `--enqueue` and the generated `offdesk_enqueue_command.sh` so
   blocked preflight does not silently add a 30-minute workload to the queue.

## Forty-Eighth Implementation Slice

Status: implemented.

This code slice added workload-specific review artifacts:

1. Add `scripts/offdesk_workload_review_harness.py` as a deterministic read-only
   reviewer for the exact `prepared_task.json`.
2. Check the prepared manifest for read-only workload safety, out-dir scoped
   artifacts, `dispatch.runtime`, role-gate readiness, local-tmux runner use for
   longer workloads, and separate approval gates.
3. Emit `workload_review/results.json` plus `workload_review/REVIEW.md`.
4. Return `needs_approval` for clean manifests instead of `proceed` because
   `dispatch.runtime` still requires operator approval.
5. Add `--review-artifact generate` to
   `scripts/prepare_twinpaper_offdesk_task.py` so the prepared manifest is
   reviewed in the same out-dir and then rewritten with final preflight state.
6. Accept both `review_offdesk_stage_contract` and `workload_manifest_review`
   artifacts in the preflight summarizer.
7. Narrow secret-like detection to real token-shaped strings so paths such as
   `offdesk-prepare-smoke` do not cause false review blockers.

## Forty-Ninth Implementation Slice

Status: implemented.

This code slice added deterministic evidence bundles before TwinPaper Offdesk
model work:

1. Add `docs/offdesk-evidence-bundles.md` as the evidence contract.
2. Add `scripts/build_twinpaper_evidence_bundle.py` to collect RunLog tail,
   targeted RunLog excerpts, Module03 artifacts, and source metadata without an
   LLM.
3. Add `scripts/review_evidence_bundle.py` to classify the bundle as
   `sufficient`, `insufficient`, `conflicting`, or `needs_operator`.
4. Generate evidence bundle/review artifacts during
   `scripts/prepare_twinpaper_offdesk_task.py` and record them in
   `prepared_task.json`.
5. Pass the generated bundle and review into
   `scripts/offdesk_twinpaper_autonomy_workload.py`.
6. Add a workload evidence-state case before research/writing/critique cases so
   the model must acknowledge current baseline evidence instead of relying on a
   stale `RunLog.md` prefix.
7. Store full model responses under `responses/` while keeping `progress.jsonl`
   compact.

## Fiftieth Implementation Slice

Status: implemented.

This code slice hardened the TwinPaper critique workload after the first
evidence-backed 30-minute run:

1. Use the 2026-05-20 run result as the regression source:
   `10/12` passed, with both failures isolated to
   `critique_open_explore_direction_change`.
2. Keep the critique task as prose because the operator value is a readable
   skeptical review, not a JSON object.
3. Require the critique response to start with an exact `Evidence anchors:`
   line containing `open-explore`, `no-option`, `singlex`,
   `validated_candidate`, `p/q`, `restart_stability`,
   `primary_objective_gate`, and the current baseline evidence status.
4. Keep the existing semantic anchors in `must_have` so the workload still
   fails when the model omits the evidence vocabulary after the prompt
   hardening.
5. Preserve full response capture under `responses/` so future failures can be
   reviewed as either model misses or harness-contract misses.

## Fifty-First Implementation Slice

Status: implemented.

This code slice added deterministic post-run review so reasoning failures can
be converted into follow-up work instead of being hidden behind a pass rate:

1. Add `scripts/review_twinpaper_offdesk_result.py` to inspect a completed
   `result.json`, response files, and the evidence bundle without calling an
   LLM.
2. Classify blockers and warnings separately so a run can be usable while still
   producing follow-up candidates.
3. Check that response files and raw response files exist for every record.
4. Add stricter review for operator-command cases: runnable commands can still
   be flagged when they drift from canonical repo-relative command formatting.
5. Add research/critique review warnings for missing inline evidence refs,
   possible open-explore evidence conflicts, and scope-overreach around
   regression coverage.
6. Emit `result_review/results.json` plus `result_review/RESULT_REVIEW.md`.
7. Have `scripts/offdesk_twinpaper_autonomy_workload.py` run this deterministic
   reviewer after writing `result.json`, and have
   `scripts/prepare_twinpaper_offdesk_task.py` expose the expected review
   artifact paths in `prepared_task.json`.

## Fifty-Second Implementation Slice

Status: implemented.

This code slice tightened the failure-learning loop around reasoning-mode
errors:

1. Treat post-run review warnings as learning signals, not as a replacement for
   the original workload result.
2. Distinguish real evidence conflicts from correct skeptical language that
   says exploratory open-explore evidence exists but is not promotion-gate
   comparable.
3. Keep warnings active when the response says open-explore has no
   `validated_candidate` or no `p/q` evidence without also mentioning
   promotion-gate comparability, the `primary_objective_gate`, or same-gate
   comparison limits.
4. Let clean corrected behavior remove stale learning candidates so the agent
   does not keep reinforcing a fixed failure.
5. Verify the full prompt-response-review path with a short qwen3-coder-next
   smoke run: `5/5` workload cases passed and automatic result review returned
   `decision=clean`.

## Verification Commands

Targeted:

```bash
python3 -m py_compile scripts/build_twinpaper_evidence_bundle.py
python3 -m py_compile scripts/review_evidence_bundle.py
python3 -m py_compile scripts/offdesk_workload_review_harness.py
python3 -m py_compile scripts/offdesk_role_llm_episode_harness.py
python3 -m py_compile scripts/offdesk_role_episode_harness.py
python3 -m py_compile scripts/offdesk_wiki_llm_harness.py
python3 -m py_compile scripts/offdesk_twinpaper_autonomy_workload.py
python3 -m py_compile scripts/offdesk_runtime_episode_harness.py
scripts/offdesk_role_episode_harness.py
scripts/offdesk_role_llm_episode_harness.py --model qwen3-coder-next:latest --base-url http://172.16.0.37:11434 --temperature 0.0 --max-budget 2048 --num-ctx 8192
scripts/offdesk_role_llm_episode_harness.py --model qwen3-coder-next:latest --base-url http://172.16.0.37:11434 --temperature 0.0 --iterations 5 --max-budget 2048 --num-ctx 8192
scripts/build_twinpaper_evidence_bundle.py --out target/twinpaper-evidence-smoke/evidence_bundle.json
scripts/review_evidence_bundle.py --bundle target/twinpaper-evidence-smoke/evidence_bundle.json --out target/twinpaper-evidence-smoke/evidence_review.json
scripts/review_twinpaper_offdesk_result.py --result /home/kimyoungjin06/.config/agent-of-empires/profiles/twinpaper-adaptive-debug/offdesk_workloads/twinpaper_autonomy/20260520T111823Z/result.json --out target/offdesk-result-review-smoke/results.json
scripts/review_twinpaper_offdesk_result.py --result target/offdesk-workload-smoke/post-review-v1/result.json --out target/offdesk-result-review-smoke/post-review-v1-retuned/results.json
scripts/offdesk_twinpaper_autonomy_workload.py --out-dir target/offdesk-workload-smoke/critique-anchor-v1 --duration-minutes 0.01 --max-iterations 5 --evidence-bundle target/twinpaper-evidence-smoke/evidence_bundle.json --evidence-review target/twinpaper-evidence-smoke/evidence_review.json --base-url http://172.16.0.37:11434 --model qwen3-coder-next:latest --temperature 0.0 --num-ctx 16384 --num-predict 4096
scripts/offdesk_twinpaper_autonomy_workload.py --out-dir target/offdesk-workload-smoke/post-review-v2 --duration-minutes 0.01 --max-iterations 5 --evidence-bundle target/twinpaper-evidence-smoke/evidence_bundle.json --evidence-review target/twinpaper-evidence-smoke/evidence_review.json --base-url http://172.16.0.37:11434 --model qwen3-coder-next:latest --temperature 0.0 --num-ctx 16384 --num-predict 4096
scripts/prepare_twinpaper_offdesk_task.py --out-root target/offdesk-prepare-smoke --duration-minutes 0.1 --max-iterations 1 --role-gate-result latest --review-artifact latest
scripts/prepare_twinpaper_offdesk_task.py --out-root target/offdesk-prepare-smoke --duration-minutes 0.1 --max-iterations 1 --role-gate-result latest --review-artifact generate
scripts/offdesk_wiki_llm_harness.py --prompt-profile contract_v3 --iterations 1
scripts/offdesk_wiki_llm_harness.py --case planning_offdesk_review_stage --prompt-profile contract_v3 --iterations 1
scripts/offdesk_wiki_llm_harness.py --case review_offdesk_stage_contract --prompt-profile contract_v3 --iterations 1
scripts/offdesk_wiki_llm_harness.py --prompt-profile contract_v3 --why-depth-sweep 0,3,6 --iterations 1
scripts/offdesk_runtime_episode_harness.py --runtime-disabled-check
cargo test adaptive_wiki
cargo test adaptive_wiki::tests::legacy_runtime_policy_ack_json_defaults_to_exact_query_scope
cargo test offdesk::runner::tests::runtime_context_uses_runtime_projection_not_preflight_projection
cargo test adaptive_wiki::tests::ai_projection_report_flags_conflicts_and_review_proposes_resolution
cargo test adaptive_wiki::tests::ai_projection_report_warns_review_expired_without_excluding
cargo test adaptive_wiki::tests::ai_projection_report_can_exclude_review_expired_by_policy
cargo test adaptive_wiki::tests::ai_projection_policy_comparison_reports_warn_and_strict_delta
cargo test adaptive_wiki::tests::ai_projection_shared_when_unspecified_policy_keeps_mode_specific_entries_out
cargo test --test offdesk_cli offdesk_gate_json_includes_adaptive_wiki_projection
cargo test --test offdesk_cli offdesk_gate_json_filters_adaptive_wiki_projection_by_agent_mode
cargo test --test offdesk_cli offdesk_tick_injects_adaptive_wiki_runtime_context_and_records_usage
cargo test --test offdesk_cli offdesk_launch_runtime_wiki_kill_switch_keeps_preflight_projection
cargo test --test offdesk_cli offdesk_strict_runtime_wiki_requires_ack_and_excludes_review_expired
cargo test --test offdesk_cli offdesk_project_artifact_runtime_ack_reuses_only_without_session_specific_projection
cargo test --test offdesk_cli offdesk_wiki_renew_review_after_updates_only_review_metadata
cargo test --test offdesk_cli offdesk_runtime_policy_ack_report_flags_near_expiry_and_session_block
cargo test --test offdesk_cli offdesk_runtime_policy_ack_report_suggests_recompare_for_expired_and_stale_ack
cargo test --test offdesk_cli offdesk_wiki_review_after_report_flags_expired_and_near_expiry_entries
cargo test --test offdesk_cli offdesk_debug_bundle_includes_wiki_attention_summaries_read_only
cargo test --test offdesk_cli offdesk_wiki
cargo test --test offdesk_cli offdesk_wiki_read_only_commands_expose_candidates_entries_projection_and_lint
cargo test --test offdesk_cli offdesk_wiki_episode_trace_links_task_usage_candidate_and_audit_evidence
cargo test --test offdesk_cli offdesk_wiki_evaluate_recurrence_counts_pre_and_post_promotion_corrections
cargo test --test offdesk_cli offdesk_wiki_corrections_json_and_debug_bundle_redact_records
cargo test --test offdesk_cli offdesk_wiki_proposal_events_record_list_and_debug_bundle_are_redacted
cargo test --test offdesk_cli offdesk_wiki_proposal_closure_helpers_copy_metadata_and_block_duplicates
cargo test --test offdesk_cli offdesk_wiki_proposal_handoff_previews_ready_manual_and_blocked
cargo test --test offdesk_cli offdesk_wiki_proposal_receipt_links_preview_audit_and_event_without_mutation
cargo test adaptive_wiki::tests::review_report_annotates_proposals_with_latest_lifecycle_event
cargo test adaptive_wiki::tests::review_report_marks_lifecycle_decision_stale_after_new_subject_evidence
cargo test --test offdesk_cli offdesk_wiki_promotion_chain_reports_snapshots_and_usage_without_mutation
cargo run -p xtask -- gen-docs
```

Full:

```bash
cargo fmt --all -- --check
git diff --check
cargo check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```
