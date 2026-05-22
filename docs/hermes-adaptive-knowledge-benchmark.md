# Hermes Adaptive Knowledge Benchmark

This note is the second Hermes comparison pass for Forager. The first pass
covered safety rails: approvals, provider fallback, rollback evidence,
capability registry, redaction, and adaptive wiki projections. This pass focuses
on the remaining Hermes value: memory lifecycle, `llm-wiki`, curator, and
skills.

Hermes remains a reference implementation, not a dependency. Forager should
keep canonical truth in Offdesk state and artifacts.

## Source Scope

Local Hermes checkout:

- `aoe_orch_control/vendor/hermes-agent`
- companion note:
  `aoe_orch_control/docs/HERMES_AGENT_BENCHMARK_20260512.md`

Hermes files inspected for this pass:

- `agent/memory_provider.py`
- `agent/memory_manager.py`
- `agent/curator.py`
- `agent/curator_backup.py`
- `agent/skill_utils.py`
- `agent/skill_commands.py`
- `agent/prompt_builder.py`
- `tools/skill_usage.py`
- `tools/skill_manager_tool.py`
- `tools/skills_tool.py`
- `skills/research/llm-wiki/SKILL.md`
- `website/docs/user-guide/features/curator.md`

## Decision Summary

| Hermes Pattern | Forager Decision | Reason |
|---|---|---|
| Memory provider lifecycle hooks | Adapt behind Offdesk events | Useful lifecycle, but Forager must not delegate canonical memory truth to a plugin. |
| Memory context fencing and streaming scrubber | Keep and generalize | Directly matches the existing adaptive wiki fenced context boundary. |
| `llm-wiki` markdown vault | Adapt as generated human projection | Strong human knowledge-base shape; JSON remains canonical. |
| Curator deterministic transitions | Adapt as report/lint first | Useful maintenance loop, but no autonomous mutation in Forager. |
| Curator LLM consolidation pass | Adapt only as review recommendations | Forager needs operator approval before durable knowledge changes. |
| Skills progressive disclosure | Adapt for procedure/runbook entries | Good way to keep procedural memory compact until relevant. |
| Self-writing skill mutation loop | Reject for now | Too much durable behavior mutation risk without Forager approval/audit. |
| Mandatory full skill prompt index | Reject for Offdesk runtime | It is token-heavy and too broad; use scoped retrieval and projection. |

## Findings

### 1. Memory Lifecycle

Hermes' `MemoryProvider` shape is useful because it separates lifecycle from the
storage backend. The key hooks are:

- `initialize(session_id, hermes_home, platform, agent_context, ...)`
- `system_prompt_block()`
- `prefetch(query, session_id)`
- `queue_prefetch(query, session_id)`
- `sync_turn(user, assistant, session_id)`
- `on_turn_start(...)`
- `on_session_end(messages)`
- `on_session_switch(new_session_id, parent_session_id, reset)`
- `on_pre_compress(messages)`
- `on_memory_write(action, target, content, metadata)`
- `on_delegation(task, result, child_session_id)`

Forager should translate these into Offdesk event hooks, not expose a generic
memory provider API yet:

- `task_enqueued`
- `approval_created`
- `approval_resolved`
- `task_launched`
- `task_completed`
- `task_failed`
- `resume_created`
- `snapshot_created`
- `restore_plan_created`
- `wiki_entry_projected`
- `wiki_entry_reviewed`

The immediate implementation target is an append-only learning signal layer
that records candidates from more event types. Runtime recall should continue
to use the small AI projection already added to background probes.

### 2. Memory Fencing

Hermes' `MemoryManager` wraps recalled memory in `<memory-context>` and scrubs
that block from streamed user-visible output. It also strips provider-returned
pre-wrapped context and handles split tags across streaming chunks.

Forager already has the equivalent safety direction:

- `adaptive_wiki_context` is fenced.
- It is attached to the background probe/handoff, not to command strings.
- Debug bundles and operator output pass through redaction.

Next Forager improvement:

- add a reusable fenced-context scrubber for any future runner-visible context,
  not just adaptive wiki;
- keep a context kind field, e.g. `adaptive_wiki`, `resume_evidence`,
  `provider_hint`, so debug bundles can count and redact by source.

### 3. `llm-wiki`

Hermes' `llm-wiki` skill is the best human-facing knowledge-base pattern:

- `raw/` stores immutable sources;
- `SCHEMA.md` defines conventions, page thresholds, taxonomy, and update policy;
- `index.md` is the navigation spine;
- `log.md` is append-only operational history;
- wiki pages carry frontmatter: type, tags, sources, confidence, contested, and
  contradictions;
- raw source files carry `source_url`, `ingested`, and body `sha256`;
- lint checks broken links, orphan pages, index completeness, stale content,
  contradictions, low confidence, source drift, oversized pages, and tag drift;
- every session starts by reading schema, index, and recent log.

Forager should adapt this as a generated human projection:

- canonical source remains `adaptive_wiki_entries.json`,
  `adaptive_wiki_candidates.json`, `adaptive_wiki_audit.jsonl`, and
  `adaptive_wiki_usage.jsonl`;
- markdown export is one-way until conflict-resolution semantics are designed;
- `raw/` contains copied or summarized evidence artifacts with source hashes;
- `SCHEMA.md`, `index.md`, and `log.md` are generated from canonical state.

### 4. Curator

Hermes curator has two separable parts.

The deterministic part is valuable:

- state is persisted in `.curator_state`;
- first run is deferred instead of mutating immediately after install;
- default cadence is interval plus idle time;
- usage telemetry is sidecar state, not mixed into skill content;
- pinned skills are excluded;
- stale and archive transitions are based on derived activity timestamps;
- backups are taken before real curator passes.

The LLM consolidation part is useful only as a recommendation source:

- it scans agent-created skills;
- it proposes umbrella skills, support files, archives, and patches;
- it records structured reports.

Forager should not let the curator mutate adaptive wiki entries directly. The
Forager version should write review reports with proposed operations:

- `promote_candidate`
- `reject_candidate`
- `rescope_entry`
- `deprecate_entry`
- `add_counterexample`
- `renew_review_after`
- `split_entry`
- `merge_entries`
- `export_markdown`

Operators or explicit Offdesk approvals should apply those operations.

Important risk: Hermes documentation and curator prompts emphasize recoverable
archival, while `skill_manage(action="delete")` removes a skill directory. The
automatic transition path uses `skill_usage.archive_skill(...)`, which moves to
`.archive/`, but Forager should avoid this ambiguity entirely: review first,
snapshot first, mutate only through explicit Forager commands.

### 5. Skills And Procedural Memory

Hermes skills are not just memory entries. They are progressive-disclosure
procedural bundles:

- `SKILL.md` frontmatter declares name, description, platform, tags, category,
  related skills, requirements, fallback conditions, and config variables;
- `references/`, `templates/`, `scripts/`, and `assets/` keep large support
  material out of the prompt until needed;
- `skill_view` loads full content and support-file hints on demand;
- skill config values can be injected without exposing secrets;
- skill commands can be hidden based on platform or available tools/toolsets;
- external skill directories are read-only; local skills take precedence.

Forager should not import this as a general self-mutating skill system in the
Offdesk runtime. The useful translation is narrower:

- adaptive wiki `procedure` entries can become governed runbook entries;
- generated markdown export can include support files under a procedure page;
- future capability registry links can say which capability/artifact a runbook
  requires;
- runtime projection stays compact and scoped, with support material loaded
  only after the scheduler has already allowed the action.

## Forager Execution Plan

### Slice 4: Markdown Human Vault

Implemented first-pass one-way export:

- `forager offdesk wiki export-markdown --output <dir>`
- `forager offdesk wiki export-markdown --dry-run --json`
- generated `SCHEMA.md`, `index.md`, `log.md`
- entry pages grouped by kind and scope
- candidate pages grouped separately
- empty `raw/audits`, `raw/diffs`, and `raw/docs` evidence directories
- deterministic file hash metadata in the export report
- future: raw evidence snapshots when source refs can be resolved
- future: source hash metadata and drift checks for exported raw evidence

Acceptance checks:

- export is deterministic;
- exported markdown is sanitized;
- deprecated entries remain visible to humans but absent from AI projection;
- raw evidence is never edited in place;
- repeated export does not reorder unrelated pages.

### Slice 5: Governance Lint And Staleness

Extend `offdesk wiki lint`. First pass now covers contested entries,
inferred-confidence promoted entries, stale candidates, review-expired entries,
missing evidence, missing instructions, empty claims, missing sources, zero hit
counts, and legacy unknown signal kinds. Remaining checks:

- contradictions over same kind/scope/scope_ref;
- orphaned candidates;
- source hash drift for exported raw evidence;
- markdown export drift when export is present.

Acceptance checks:

- lint reports actionable subject ids;
- lint remains read-only;
- review-expired entries are either flagged or excluded according to a documented
  projection policy;
- secrets remain redacted in every lint output.

### Slice 6: Curator Review Reports

Implemented deterministic first-pass curator output:

- `adaptive_wiki_review_reports/<timestamp>/report.json`
- `adaptive_wiki_review_reports/<timestamp>/REPORT.md`
- `forager offdesk wiki review --json`
- `forager offdesk wiki review --dry-run --json`

Inputs:

- entries, candidates, audit records, usage records;
- lint report;
- future: task failure/resume evidence;
- future: provider fallback and rollback evidence when relevant.

Outputs:

- proposed operation list;
- evidence refs for every proposal;
- confidence and risk classification;
- no mutation.

Acceptance checks:

- first run is report-only;
- report generation is deterministic when LLM review is disabled;
- LLM-generated proposals cannot be applied without an explicit command or
  approval;
- reports are included in debug bundle only after redaction.

### Slice 7: Procedure Runbooks

Implemented first-pass governed procedural layer:

- keep `AdaptiveWikiKind::Procedure`;
- add optional support refs for generated `references/`, `templates/`, and
  `scripts/` export files;
- link procedures to capability ids and required artifacts;
- never let a procedure override the capability registry or approval rails.

Acceptance checks:

- procedure projection is compact;
- support files are human/export material until explicitly loaded by an allowed
  runtime path;
- capability mismatch blocks before runtime launch;
- procedure entries do not authorize command/workdir/provider/model mutations.

### Slice 8: Episode Evaluation

Implemented deterministic first-pass episode evaluation:

- `forager offdesk wiki evaluate-episode <entry-id> --json`
- `forager offdesk wiki evaluate-episode <entry-id> --dry-run --json`
- `adaptive_wiki_episode_reports/<timestamp>/episode.json`
- `adaptive_wiki_episode_reports/<timestamp>/EPISODE.md`

The first pass validates projection behavior from current wiki state:

- target entry appears for the in-scope query;
- target entry does not leak into the out-of-scope query;
- deprecated projected entries are failures;
- review-expired projected entries are failures;
- projected entries without evidence refs are failures.

Future live episodes should validate behavior change, not recall:

- create a mistake-prone task;
- record candidate evidence;
- promote with bounded scope;
- run in-scope and out-of-scope follow-up tasks;
- measure correction recurrence, scope leakage, stale activation, and evidence
  trace completeness.

Acceptance checks:

- implemented now: out-of-scope projection leakage is zero for the target
  entry;
- implemented now: deprecated/review-expired entries do not silently guide
  execution; review-expired projection fails the report;
- implemented now: projected entries must have evidence refs;
- future: in-scope correction recurrence drops after promotion;
- future: every runtime projection in a live episode has a usage audit line.

### Slice 9: Live Episode Trace

Implemented first-pass live trace assembly over existing durable artifacts:

- `forager offdesk wiki episode-trace --json`
- `forager offdesk wiki episode-trace --dry-run --json`
- `adaptive_wiki_episode_traces/<timestamp>/report.json`
- `adaptive_wiki_episode_traces/<timestamp>/trace.jsonl`
- `adaptive_wiki_episode_traces/<timestamp>/REPORT.md`

Inputs:

- Offdesk tasks;
- background probes;
- task resume state;
- adaptive wiki usage records;
- adaptive wiki correction records;
- adaptive wiki candidates;
- adaptive wiki audit records.

Trace events:

- task enqueue/completion/failure;
- projection attachment;
- runtime wiki usage;
- first-class correction records;
- legacy operator-correction candidate fallback;
- promotion audits;
- counterexample records;
- resume-pending evidence;
- rollback-derived candidates.

Acceptance checks:

- implemented now: trace generation is report-only and non-mutating;
- implemented now: task, usage, candidate, audit, probe, and resume evidence can
  be linked by request/project/task filters;
- implemented now: trace output is redacted;
- implemented now: correction recurrence is measured before and after promotion;
- implemented now: promotion snapshots are attached to promotion audit records
  and replayed by promotion-chain reports.

### Slice 10: Correction Recurrence Evaluation

Implemented first-pass recurrence evaluation:

- `forager offdesk wiki evaluate-recurrence <entry-id> --json`
- `forager offdesk wiki evaluate-recurrence <entry-id> --dry-run --json`
- `adaptive_wiki_recurrence_reports/<timestamp>/report.json`
- `adaptive_wiki_recurrence_reports/<timestamp>/recurrence.jsonl`
- `adaptive_wiki_recurrence_reports/<timestamp>/REPORT.md`

Inputs:

- target promoted entry and scope;
- promotion audit record or entry creation time;
- live episode events assembled from task, probe, resume, usage, candidate, and
  audit artifacts.

Metrics:

- pre-promotion correction events;
- post-promotion correction events;
- post-promotion runtime usages;
- post-promotion task failure or resume-pending recurrence;
- post-promotion recurrence per 1000 usages;
- recurrence delta.

Acceptance checks:

- implemented now: recurrence evaluation is report-only and non-mutating;
- implemented now: recurrence output is redacted;
- implemented now: post-promotion correction recurrence is visible when it
  happens after runtime usage;
- implemented now: recurrence uses first-class correction records before
  candidate/audit/task-derived fallback and does not double-count the same
  candidate correction.

### Slice 11: Promotion Evidence Chain Reports

Implemented promotion-time snapshot replay:

- `forager offdesk wiki promotion-chain <entry-id> --json`
- `forager offdesk wiki promotion-chain <entry-id> --dry-run --json`
- `adaptive_wiki_promotion_chains/<timestamp>/report.json`
- `adaptive_wiki_promotion_chains/<timestamp>/chain.jsonl`
- `adaptive_wiki_promotion_chains/<timestamp>/REPORT.md`

Inputs:

- promotion audit records;
- redacted candidate snapshots captured at promotion time;
- redacted entry snapshots captured at promotion time;
- current human entry projection;
- runtime usage records and later audit records tied to the entry.

Acceptance checks:

- implemented now: new promotion audits carry candidate and entry snapshots;
- implemented now: legacy promotion audits without snapshots are reported as
  incomplete evidence chains;
- implemented now: chain reports are redacted, report-only, and non-mutating.

### Slice 12: First-Class Correction Evidence

Implemented a durable correction evidence contract:

- `adaptive_wiki_corrections.jsonl`
- `forager offdesk wiki corrections --json`
- debug-bundle field `adaptive_wiki_corrections`

Inputs:

- operator-correction candidates recorded by the wiki store;
- explicit correction rows with task/request/project/artifact refs;
- recurrence and trace filters over the existing live episode graph.

Acceptance checks:

- implemented now: operator-correction candidate recording appends sanitized
  correction rows;
- implemented now: live episode traces prefer correction rows and keep legacy
  candidate fallback for older profiles;
- implemented now: recurrence reports expose `correction_records_checked`;
- implemented now: correction list and debug-bundle output are redacted,
  read-only, and non-mutating.

### Slice 13: Curator Evidence Graph Proposals

Implemented recommendation-only curator use of the evidence graph:

- `forager offdesk wiki review --json`
- `adaptive_wiki_review_reports/<timestamp>/report.json`
- `adaptive_wiki_review_reports/<timestamp>/REPORT.md`

Inputs:

- correction records;
- promotion audit records and promotion snapshots;
- usage records;
- existing lint, candidate, and entry state.

Acceptance checks:

- implemented now: review summaries include `correction_records_checked`;
- implemented now: post-promotion corrections tied to an entry produce a
  rescope/review proposal;
- implemented now: missing promotion audit or promotion snapshots produce a
  renew-review proposal;
- implemented now: all curator evidence-graph proposals are redacted,
  report-only, and non-mutating.

### Slice 14: Proposal Lifecycle Events

Implemented operator action traceability for curator proposals:

- `adaptive_wiki_review_events.jsonl`
- `forager offdesk wiki proposal-events --json`
- `forager offdesk wiki record-proposal-event <proposal-id> --decision <accepted|rejected|superseded>`
- debug-bundle field `adaptive_wiki_review_events`

Inputs:

- proposal id from a curator review report;
- operator decision and reason;
- optional proposal action, subject metadata, superseded proposal id, and
  evidence refs.

Acceptance checks:

- implemented now: proposal lifecycle events are append-only and durable;
- implemented now: review reports count lifecycle events as
  `review_events_checked`;
- implemented now: lifecycle event reasons and evidence refs are sanitized
  before storage;
- implemented now: recording a proposal event does not apply the proposed
  mutation.

### Slice 15: Proposal-Aware Review Output

Implemented decision-aware curator review output:

- `forager offdesk wiki review --json`
- `adaptive_wiki_review_reports/<timestamp>/report.json`
- `adaptive_wiki_review_reports/<timestamp>/REPORT.md`
- human `forager offdesk wiki review` output

Inputs:

- current curator proposals;
- append-only proposal lifecycle events;
- latest event per proposal id.

Acceptance checks:

- implemented now: each current proposal is annotated with its latest lifecycle
  event when one exists;
- implemented now: review summaries count open proposals and proposals with
  accepted/rejected/superseded decisions;
- implemented now: lifecycle reasons and evidence refs are redacted in review
  output even if legacy event files contain unsanitized text;
- implemented now: lifecycle annotation remains report-only and does not apply
  curator mutations.

### Slice 16: Decision-Aware Curator Queue Hygiene

Implemented stale-decision detection for curator proposals:

- proposal lifecycle annotation field `stale`
- proposal lifecycle annotation field `stale_evidence_refs`
- review summary field `stale_decision_proposals`
- human and markdown review output stale decision markers

Inputs:

- latest proposal lifecycle event;
- current candidate and entry timestamps;
- timestamped evidence refs from usage, correction, audit, entry, and candidate
  records.

Acceptance checks:

- implemented now: accepted/rejected/superseded decisions are marked stale when
  the proposal subject changed after the decision;
- implemented now: decisions are marked stale when timestamped evidence refs are
  newer than the decision;
- implemented now: stale decisions count as open proposals for review queue
  accounting;
- implemented now: stale detection remains report-only and does not suppress or
  apply proposals.

### Slice 17: Lifecycle-Aware Review Filtering

Implemented operator queue views for curator proposals:

- `forager offdesk wiki review --active-only`
- `forager offdesk wiki review --decided-only`
- `forager offdesk wiki review --stale-only`
- review summary field `filtered_out_proposals`

Inputs:

- current proposal lifecycle annotations;
- stale-decision state;
- selected operator queue filter.

Acceptance checks:

- implemented now: active-only shows open proposals and stale decisions;
- implemented now: decided-only shows non-stale accepted/rejected/superseded
  proposals;
- implemented now: stale-only shows proposals whose latest decision needs
  renewed review;
- implemented now: filter flags are mutually exclusive and remain
  recommendation-only.

### Slice 18: Proposal Event Closure Helpers

Implemented shortcut commands for proposal lifecycle decisions:

- `forager offdesk wiki accept-proposal <proposal-id> --reason <text>`
- `forager offdesk wiki reject-proposal <proposal-id> --reason <text>`
- `forager offdesk wiki supersede-proposal <proposal-id> --reason <text>`
- optional `--allow-decided` override for deliberate re-closure

Inputs:

- current review proposal id;
- operator reason;
- optional extra evidence refs and superseded proposal id.

Acceptance checks:

- implemented now: helper commands resolve the current review proposal and copy
  proposal action, subject, and evidence refs into the lifecycle event;
- implemented now: copied and extra evidence refs are sanitized before storage;
- implemented now: duplicate closure of non-stale decided proposals is blocked
  unless `--allow-decided` is explicit;
- implemented now: helpers append audit events only and do not apply curator
  mutations.

### Slice 19: Proposal-to-Mutation Handoff Previews

Implemented read-only mutation handoff previews:

- `forager offdesk wiki proposal-handoff <proposal-id> --json`
- preview statuses `ready`, `manual_required`, and `blocked_by_decision`
- manual proposal contracts through `required_inputs` and `mutation_options`
- parameterized ready previews for safe entry-scoped mutation paths

Inputs:

- current review proposal id;
- current lifecycle decision state;
- proposal-suggested governed mutation command when available.
- operator input contract needed for manual proposal classes.
- optional operator-supplied mutation parameters such as scope, scope ref,
  evidence ref, duplicate entry id, and reason.

Acceptance checks:

- implemented now: ready proposals return an exact governed mutation command;
- implemented now: proposals that need operator choices return
  `manual_required` with a reason instead of inventing a command;
- implemented now: non-stale decided proposals return `blocked_by_decision`;
- implemented now: manual proposals expose command templates and required
  operator inputs without executing or mutating anything;
- implemented now: complete parameterized rescope and add-counterexample
  previews return exact commands, while incomplete parameter sets stay
  `manual_required`;
- implemented now: operator-supplied evidence refs and reasons are redacted
  before being echoed in previewed commands;
- implemented now: handoff previews are read-only and do not append lifecycle
  events or mutate wiki rows.

### Slice 20: Proposal Handoff Receipts

Implemented read-only audit receipts for separated proposal governance:

- `forager offdesk wiki proposal-receipt <proposal-id> --audit-id <id> --event-id <id> --command <cmd> --json`
- receipt statuses `linked` and `incomplete`
- sanitized preview command hash
- proposal snapshot fallback through lifecycle event metadata

Inputs:

- proposal id from the curator review queue;
- previewed handoff command;
- mutation audit id produced by the executed wiki mutation;
- lifecycle event id produced by accepting, rejecting, or superseding the
  proposal.

Acceptance checks:

- implemented now: receipts are read-only and do not append lifecycle events,
  audit records, wiki rows, reports, approvals, or tasks;
- implemented now: the sanitized preview command is hashed for later
  comparison without storing raw operator text;
- implemented now: the audit target, lifecycle event subject, and proposal
  subject are validated together;
- implemented now: receipts still work after a mutation removes the current
  proposal by using metadata copied into the lifecycle event.

### Slice 21: Receipt Workflow Hardening

Implemented operator workflow hardening around proposal receipts:

- `proposal-receipt --export`
- `proposal-receipt --output <path> --json`
- actual-vs-expected check details for incomplete receipts
- documented review -> handoff -> lifecycle event -> mutation -> receipt flow

Inputs:

- a linked or incomplete proposal receipt request;
- optional receipt export path;
- operator need for later audit evidence.

Acceptance checks:

- implemented now: receipt exports are sanitized JSON artifacts and do not
  overwrite existing files;
- implemented now: exports remain explicit and do not change wiki rows, audit
  records, lifecycle events, approvals, reports, or tasks;
- implemented now: incomplete receipts report whether the missing or mismatched
  link is the audit record, lifecycle event, proposal subject, or target
  alignment;
- implemented now: operator docs show the complete separated governance
  workflow.

### Slice 22: Projection Quality Policy Reports

Implemented inspectable AI projection selection:

- `forager offdesk wiki projection --report --json`
- `--max-entries <n>`
- `--max-context-chars <n>`
- `--max-instruction-chars <n>`
- report fields for selected entries, rejected entries, budget, and summary

Inputs:

- promoted wiki entries matching the session/project/artifact query;
- default projection budget;
- optional operator budget overrides.

Acceptance checks:

- implemented now: default `ai_projection()` remains backward-compatible and
  returns only selected entries;
- implemented now: projection candidates are ordered by scope specificity,
  confidence, evidence count, recency, activation mode, and stable id;
- implemented now: empty projected instructions and budget overflows are
  rejected with structured reasons;
- implemented now: long instructions can be truncated under the instruction
  budget and counted in the report summary;
- implemented now: CLI reports expose why entries entered or were left out of
  AI runtime context.

### Slice 23: Projection Conflict Policy

Implemented recommendation-only conflict detection for promoted entries:

- projection reports include `conflicts`;
- summaries count promoted-entry conflicts;
- human CLI projection reports list conflicting entry ids and the normalized
  instruction target;
- curator review reports add high-risk split proposals for conflicting
  promoted entries.

Inputs:

- promoted wiki entries matching the projection query;
- normalized instruction polarity such as `use ...` versus `do not use ...`;
- same kind, scope, and scope reference.

Acceptance checks:

- implemented now: conflicts are reported without changing selected projection
  entries;
- implemented now: conflict proposals are review-only and carry evidence refs
  for the conflicting entry and normalized projection target;
- implemented now: runtime behavior remains unchanged until an operator applies
  a governed wiki mutation.

Immediate next work:

- decide whether review-expired entries should remain projected with warnings
  or be excluded by a stricter runtime policy.

### Slice 24: Projection Conflict Handoff Guidance

Implemented conflict-specific proposal handoffs:

- `proposal-handoff` recognizes projection-conflict split proposals;
- manual previews list `rescope`, `deprecate`, `split`, and
  `add_counterexample` options;
- `--mutation deprecate --reason <text>` can retire the proposal subject;
- `--deprecated-entry-id <entry>` can target the conflicting entry referenced
  in proposal evidence;
- `--mutation split` stays manual when the conflict needs multiple governed
  mutations.

Acceptance checks:

- implemented now: conflict handoffs remain read-only;
- implemented now: exact deprecate previews are available for either side of a
  conflict pair;
- implemented now: multi-mutation split remains explicit instead of pretending
  there is a single safe command.

Immediate next work:

### Slice 25: Review-Expired Projection Warnings

Implemented stale-trust visibility without runtime exclusion:

- projection reports include `review_expired` records;
- summaries count `review_expired_projected`;
- selected entries remain unchanged under the default warn policy;
- human CLI projection reports show selected entries that are past
  `review_after`.

Acceptance checks:

- implemented now: expired entries can still project by default;
- implemented now: report JSON and human output make stale review status
  visible;
- implemented now: strict exclusion is not introduced until operators have an
  inspectable warning baseline.

Immediate next work:

### Slice 26: Opt-In Strict Review-Expired Projection

Implemented strict projection as an explicit preview policy:

- projection reports carry a `policy.review_expired` value;
- default policy remains `warn`;
- `offdesk wiki projection --exclude-review-expired --report` selects the
  strict `exclude` policy;
- expired entries rejected by strict mode use `review_expired_excluded`;
- runtime injection still uses the default warn policy.

Acceptance checks:

- implemented now: default projection is backward-compatible;
- implemented now: strict mode is operator-selected and visible in JSON;
- implemented now: excluded expired entries remain auditable as structured
  projection rejections.

Immediate next work:

### Slice 27: Warn vs Strict Projection Comparison

Implemented side-by-side policy comparison:

- `offdesk wiki projection --compare-review-expired-policy` returns both warn
  and strict reports;
- comparison summaries include `selected_only_in_warn`,
  `selected_only_in_strict`, and `review_expired_excluded`;
- comparison mode rejects `--exclude-review-expired` because it already
  includes both policies;
- human output summarizes the policy delta without changing runtime defaults.

Acceptance checks:

- implemented now: operators can inspect what strict mode would remove or
  replace before enabling it anywhere else;
- implemented now: strict-only replacements caused by budget changes are
  visible;
- implemented now: runtime projection remains on the default warn policy.

Immediate next work:

### Slice 28: Runtime Projection Source Separation

Implemented runtime projection separation without enabling strict runtime
behavior:

- gate outcomes keep `adaptive_wiki` as the default warn preflight projection;
- gate outcomes now carry `adaptive_wiki_runtime` and
  `adaptive_wiki_runtime_policy` as the source used for runtime context;
- the runner builds fenced context from `adaptive_wiki_runtime`, not from the
  preflight projection;
- background probes and runtime usage records include the runtime policy;
- current behavior remains default warn because runtime and preflight sources
  are identical until a later acknowledgement gate exists.

Acceptance checks:

- implemented now: runtime context source can diverge later without changing
  what operators saw in preflight;
- implemented now: usage/debug-bundle evidence can explain which policy fed a
  live runtime context;
- implemented now: `FORAGER_ADAPTIVE_WIKI_RUNTIME=0` still disables injection
  while leaving both gate metadata surfaces visible.

Immediate next work:

### Slice 29: Acknowledgement-Gated Strict Runtime Projection

Implemented strict runtime policy as an explicitly acknowledged operator
choice:

- `FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED=exclude` requests strict
  runtime projection;
- `offdesk wiki ack-runtime-policy` records a profile-scoped acknowledgement
  keyed by query, budget, policy, comparison hash, and expiry;
- `offdesk wiki runtime-policy-acks` exposes the acknowledgement artifact;
- if strict is requested without a current matching acknowledgement, runtime
  wiki context is omitted rather than falling back to warn;
- gate outcomes record `adaptive_wiki_runtime_decision` so operators can see
  whether strict was applied, missing, expired, or stale;
- debug bundles include runtime policy acknowledgements.

Acceptance checks:

- implemented now: strict runtime exclusion cannot apply from env alone;
- implemented now: expired review entries remain visible in preflight warn
  projection even when strict runtime context is omitted or applied;
- implemented now: acknowledgement artifacts store hashes and ids, not runtime
  instructions or raw evidence payloads.

Immediate next work:

### Slice 30: Scoped Runtime Policy Acknowledgements

Implemented explicit acknowledgement scope modes:

- `exact_query` remains the default and preserves old acknowledgement files;
- `project_artifact` lets operators reuse strict runtime acknowledgement for a
  reviewed project/artifact pair across request ids;
- project/artifact acknowledgement is blocked when the current runtime
  projection includes session-scoped selected, rejected, or review-expired
  entries;
- gate decisions distinguish `applied_project_artifact_acknowledged` from
  `strict_requested_scope_mode_blocked`.

Acceptance checks:

- implemented now: broad reuse is opt-in and labeled by scope mode;
- implemented now: request-specific wiki knowledge still requires exact review;
- implemented now: missing `scope_mode` in existing acknowledgement JSON loads
  as `exact_query`.

Immediate next work:

### Slice 31: Review Renewal And Ack Attention Report

Implemented the maintenance surface that operators need after strict runtime
projection is available:

- `offdesk wiki renew-review-after` updates only an entry's `review_after`
  timestamp, keeping scope, status, instruction, and evidence unchanged;
- the mutation appends a `renew_review_after` audit record, so renew-review
  proposals can now close through a direct governed action;
- `offdesk wiki runtime-policy-ack-report` lists active, near-expiry, and
  expired strict runtime acknowledgements;
- when supplied a runtime query, the report includes the strict runtime
  decision and flags acknowledgements blocked by session-scoped projection
  entries.

Acceptance checks:

- implemented now: renewal does not rewrite the adaptive instruction or scope;
- implemented now: near-expiry acknowledgement state is visible before launch;
- implemented now: project/artifact ack blockage is reportable without mutating
  dispatch state.

Immediate next work:

### Slice 32: Entry Review-After Attention Report

Implemented the read-only maintenance report for promoted wiki entries:

- `offdesk wiki review-after-report` uses the human projection scope filters;
- it lists promoted entries whose `review_after` is expired or near expiry;
- summaries count scoped promoted entries, entries missing `review_after`,
  expired entries, near-expiry entries, and total attention rows;
- rows include a `renew-review-after` command template but do not expose
  adaptive instructions or raw evidence payloads.

Acceptance checks:

- implemented now: review renewal work is discoverable before strict runtime
  projection blocks stale entries;
- implemented now: the attention report is read-only and operator-safe;
- implemented now: fresh, deprecated, and out-of-scope entries are not listed
  as renewal attention items.

Immediate next work:

### Slice 33: Ack Suggested Next Actions

Implemented suggested next actions for strict runtime acknowledgement reports:

- `runtime-policy-ack-report` rows now include `suggested_action` when an ack
  is expired, near expiry, stale against the current comparison, or blocked by
  session-scoped projection;
- suggested actions include a comparison command and an `ack-runtime-policy`
  command template;
- expired/stale acknowledgements are not renewed in place; operators append a
  new acknowledgement after rechecking the comparison hash;
- project/artifact acknowledgements blocked by session-specific entries suggest
  an exact-query acknowledgement for the supplied request scope.

Acceptance checks:

- implemented now: report guidance preserves append-only acknowledgement
  auditability;
- implemented now: blocked broad acknowledgements point to exact-query review;
- implemented now: stale and expired hash-bound approvals require recompare
  before a new ack.

Immediate next work:

### Slice 34: Debug-Bundle Attention Summaries

Implemented summary-only adaptive wiki maintenance counters in
`offdesk debug-bundle`:

- `adaptive_wiki_runtime_policy_ack_attention_summary` exposes runtime ack
  attention counts without adding query-specific raw report rows;
- `adaptive_wiki_review_after_attention_summary` exposes promoted entry review
  window attention counts;
- the bundle remains read-only and the new fields are counts only, not adaptive
  instructions or raw evidence payloads.

Acceptance checks:

- implemented now: debug-bundle can show whether wiki maintenance needs
  attention without forcing operators to run every report first;
- implemented now: source wiki entry and ack files are unchanged by bundle
  generation;
- implemented now: the higher-level surface is still less noisy than status.

Immediate next work:

- leave `status` unchanged unless operators repeatedly need these counters on
  the primary health surface.

### Slice 35: Agent Mode-Aware Wiki Projection

Implemented the first bridge between the user's intentionally separated agent
modes and the adaptive wiki:

- wiki candidates and promoted entries can carry `agent_modes`;
- empty `agent_modes` means shared guidance across planning, development,
  analysis, writing, critique, review, and maintenance;
- `offdesk gate`, `launch`, `enqueue`, wiki list/projection, strict runtime
  acknowledgement reports, review-after reports, and episode evaluation accept
  `--agent-mode`;
- runtime usage records, queued tasks, background probes, and markdown export
  preserve the mode context;
- mode tags filter projection only; they do not authorize command execution,
  provider/model retargeting, or wiki mutation.
- execution projection is shared-only when no agent mode is present, while human
  inspection can still list all mode-tagged knowledge.

Acceptance checks:

- implemented now: a development projection receives shared and
  development entries, but not writing or critique-only entries;
- implemented now: planning, analysis, review, and maintenance are first-class
  canonical projection modes;
- implemented now: task, poll, background, and debug-bundle outputs expose
  derived `mode_verdict`, `mode_risk`, `mode_risk_detail`, and
  `review_stage_required` fields without changing persisted task state;
- implemented now: `offdesk maintenance-report` aggregates those mode risks
  with approval, resume, provider-capacity, and adaptive-wiki attention
  summaries as a read-only operator checkpoint;
- implemented now: `offdesk maintenance-request` creates or reuses scoped
  `maintenance.<kind>` approvals without executing the requested maintenance
  action or consuming an approved one-time grant;
- implemented now: a gate without `--agent-mode` receives shared entries only;
- implemented now: candidate promotion preserves or explicitly overrides
  candidate mode tags;
- implemented now: operator human output and generated markdown expose mode
  tags so the wiki remains inspectable by both humans and agents.

### Slice 36: Role-Specific Benchmark Episodes

Implemented deterministic role-specific benchmark episodes:

- `scripts/offdesk_role_episode_harness.py`;
- isolated profile fixture under `target/offdesk-role-episode-harness/`;
- shared, planning, development, analysis, writing, critique, review,
  maintenance, legacy alias, and deprecated entries;
- real `forager offdesk gate inspect.status` calls for each role scope.

Acceptance checks:

- implemented now: a gate without `--agent-mode` receives shared guidance only;
- implemented now: development receives shared plus development
  guidance, with no writing or critique leakage;
- implemented now: planning, analysis, review, and maintenance receive shared
  plus matching guidance only;
- implemented now: writing receives shared plus writing
  guidance, with no development or critique leakage;
- implemented now: critique receives shared plus critique guidance, with no
  development or writing leakage;
- implemented now: legacy `code_development` and `research_writing` entries
  project into canonical `development` and `writing` mode queries;
- implemented now: deprecated entries are absent from every AI projection;
- implemented now: the harness preserves `results.json` with selected ids and
  the isolated profile path for inspection.

### Slice 37: Live Role Behavior Episodes

Implemented live model role-specific benchmark episodes:

- `scripts/offdesk_role_llm_episode_harness.py`;
- isolated profile fixture under `target/offdesk-role-llm-episode-harness/`;
- role markers in shared, development, writing, critique, and
  deprecated entries;
- execution-facing `forager offdesk gate inspect.status` projection for each
  role scope;
- Ollama-compatible JSON-mode model calls.

Acceptance checks:

- implemented now: no-mode gate projection is shared-only in the live harness;
- implemented now: development, writing, and critique projections
  include matching role guidance without leaking other role markers;
- implemented now: the model response does not emit out-of-scope or deprecated
  role markers;
- implemented now: development stays plan-only without claiming edits or
  test results;
- implemented now: writing remains pending without RunLog and
  validation evidence;
- implemented now: critique asks for no-option and singlex evidence before
  accepting strategy changes;
- implemented now: the response must state that adaptive wiki context is not
  execution authority.

### Slice 38: Multi-Iteration Role LLM Quality Gate

Implemented a repeatable preflight gate for role-specific adaptive-wiki
behavior:

- `scripts/offdesk_role_llm_episode_harness.py --iterations <n>`;
- per-response `failure_categories`;
- summary-level `failure_category_counts`;
- per-case pass/fail/pass-rate `case_summary`;
- `quality_gate.ready_for_long_workload`.

Acceptance checks:

- implemented now: repeated runs report whether every selected role episode
  passed;
- implemented now: failed runs identify the dominant failure bucket without
  requiring manual inspection of response text;
- implemented now: projection failures and model-response failures are counted
  separately;
- implemented now: the quality gate remains blocked until the selected repeated
  episode set is clean.

### Slice 39: TwinPaper Workload Preflight Wiring

Implemented workload preparation wiring for the role gate and review pass:

- `scripts/prepare_twinpaper_offdesk_task.py --role-gate-result <path|latest>`;
- `scripts/prepare_twinpaper_offdesk_task.py --review-artifact <path|latest>`;
- `prepared_task.json.preflight`;
- `preflight.json`, `preflight_ready`, and `preflight_blocked`;
- generated `offdesk_enqueue_command.sh` preflight guard.

Acceptance checks:

- implemented now: the prepared workload records the clean role-gate artifact
  path and summary;
- implemented now: the prepared workload records the separate review-mode
  artifact path and review decision;
- implemented now: enqueue is blocked when the review decision is `blocked` or
  `revise`, even if the review artifact contract itself passed;
- implemented now: `--enqueue` stops before queue mutation when preflight is
  blocked;
- implemented now: direct execution of the generated enqueue script also checks
  for `preflight_ready`.

### Slice 40: Workload-Specific Review Artifact

Implemented a deterministic review artifact for the exact prepared workload
manifest:

- `scripts/offdesk_workload_review_harness.py --manifest <prepared_task.json>`;
- `scripts/prepare_twinpaper_offdesk_task.py --review-artifact generate`;
- generated `workload_review/results.json`;
- generated `workload_review/REVIEW.md`;
- `prepared_task.json.preflight.review_artifact.decisions`.

Acceptance checks:

- implemented now: the review harness reads the exact `prepared_task.json`
  before enqueue, rather than reviewing a generic prompt-stage fixture;
- implemented now: a clean manifest returns `needs_approval`, because
  `dispatch.runtime` still requires an operator approval boundary;
- implemented now: blockers are reported when the manifest is missing safety
  rail evidence, a clean role gate, `local-tmux`, scoped artifacts, or matching
  workload command/output paths;
- implemented now: generated review artifacts are recorded back into
  `prepared_task.json` and `preflight.json`;
- implemented now: `ready_for_enqueue` becomes true only when the role gate is
  clean and the workload-specific review decision is acceptable;
- implemented now: existing generic review artifacts that return `blocked` or
  `revise` still block enqueue when supplied explicitly.

### Slice 41: Module Operation Preflight Gate

Implemented a module-operation preflight gate in the TwinPaper workload
preparer:

- `scripts/prepare_twinpaper_offdesk_task.py --module-preflight-artifact <path|latest|none>`;
- `prepared_task.json.preflight.module_operation_preflight`;
- `prepared_task.json.module_operation_preflight`;
- `prepared_task.json.safety.module_operation_preflight_required`;
- `offdesk_monitor_commands.md` module-preflight readiness lines.

Acceptance checks:

- implemented now: `latest` resolves the newest matching project initialization
  `MODULE_OPERATION_PREFLIGHT.json`;
- implemented now: the gate requires the Module03 scope, the recognized
  TwinPaper Module03 profile kind, profile/evidence/review builder availability,
  and all expected preflight command purposes;
- implemented now: raw command strings from `MODULE_OPERATION_PREFLIGHT.json`
  are not copied into `prepared_task.json`;
- implemented now: missing or unrecognized module preflight blocks enqueue
  unless the operator explicitly allows preflight blockers.

## Immediate Next Work

The next useful slice is an operator-facing launch dry run: prepare the
TwinPaper workload with `--review-artifact generate`, inspect the generated
review report and preflight summary, then enqueue only after the explicit
`dispatch.runtime` approval path is selected. This keeps the final boundary on
human approval rather than letting a clean review artifact launch runtime work
by itself.

## Rejected For Now

- Hidden model-private memory as canonical truth.
- Multiple external memory providers competing in one runtime.
- Direct self-writing skill loops in Offdesk.
- Automatic curator mutation of canonical wiki entries.
- Broad mandatory skill prompt index for every Offdesk task.
- Bidirectional markdown import before conflict semantics exist.
