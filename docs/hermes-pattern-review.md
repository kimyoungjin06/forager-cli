# Hermes Pattern Review

This document tracks the small Hermes Agent patterns that are worth comparing
against Forager's offdesk runtime. Hermes is a reference implementation, not a
replacement control plane. Forager keeps ownership of queue state, task state,
recovery artifacts, and action audit records.

The first pass compared against the Hermes checkout recorded in the companion
review note, `aoe_orch_control/docs/HERMES_AGENT_BENCHMARK_20260512.md`.
The adaptive knowledge follow-up is tracked in
[`hermes-adaptive-knowledge-benchmark.md`](hermes-adaptive-knowledge-benchmark.md).

## Review Rules

- Adopt patterns only behind Forager-owned interfaces.
- Keep durable truth in Forager state and artifacts.
- Prefer structured action state over replaying raw prompts.
- Preserve operator visibility through CLI, dashboard-ready JSON, and audit
  files.
- Do not import Hermes gateway, memory, curator, or skill mutation behavior as
  canonical Forager behavior.

## Candidate Patterns

| Priority | Hermes Pattern | Forager Target | Review Question | Status |
|---|---|---|---|---|
| 1 | Approval rail | `offdesk` pending action approvals | Can runtime/canonical mutation pause on a bounded action object with id, TTL, result, and audit trail? | First pass active |
| 2 | Session and resume durability | task transcript and resume artifacts | Can restart recovery explain the next safe task step without treating chat history as truth? | First pass active |
| 3 | Background process recovery | background run tickets and sidecars | Can stale local/tmux/background runners be reconciled with better tail and heartbeat evidence? | First pass active |
| 4 | Provider profile and error classifier | provider routing and capacity memory | Can provider errors become structured retry/compress/fallback reasons before scheduler policy? | First pass active |
| 5 | Checkpoint and rollback | pre-mutation evidence artifacts | Can canonical mutations require rollback evidence without copying Hermes shadow git wholesale? | First pass active |
| 6 | Tool registry | Task Team capability registry | Can capabilities be declared with risk, scope, backend, approval, and offdesk eligibility? | First pass active |
| 7 | Redaction and context fencing | operator-facing summaries and debug bundles | Can runner-only context and secrets be stripped at every upload/share boundary? | First pass active |
| 8 | Adaptive wiki | canonical wiki store and AI/human/runtime projections | Can repeated corrections become promoted, scoped knowledge without exposing raw human wiki pages to runtime prompts? | First pass active |
| 9 | Memory lifecycle hooks | Offdesk event-driven learning signals | Can task/approval/resume/compression events feed learning candidates without hidden memory truth? | Benchmarked |
| 10 | Curator | recommendation-only wiki review reports | Can stale/conflicting knowledge be surfaced for review without autonomous mutation? | Benchmarked |
| 11 | Skills/procedural memory | governed procedure/runbook entries | Can procedural knowledge stay compact and scoped without becoming a self-mutating tool layer? | Benchmarked |

## First Pass: Approval Rail

Hermes' useful shape is a pending approval object that blocks the exact risky
action until an operator resolves it. Forager already had the core commands:

- `forager offdesk pending`
- `forager offdesk ok [APPROVAL_ID]`
- `forager offdesk cancel [APPROVAL_ID]`

The first Forager pass tightens the stored object and audit line:

- every new pending row gets both an `approval_id` and an `action_id`;
- legacy rows without `action_id` still load and use `approval_id` as the audit
  fallback;
- `action_audit.jsonl` records transition, action id, result, scope, source
  surface, preview, reason, expiry, and resolver metadata;
- CLI human output prints both approval id and action id so operators can
  correlate the pending row with audit history.

Keep the first version scoped to `once` and `session` approvals. Do not add
Hermes-style permanent `always` approvals until Forager has dashboard-visible
policy review and revocation.

## Acceptance Checks

- A runtime mutation outside a fresh execution brief creates exactly one
  pending approval row.
- Repeating the same pending action returns the same pending approval instead
  of creating duplicates.
- Approving a `once` approval allows one matching action and then supersedes the
  approval.
- Denying an approval makes matching retry attempts fail until an explicit
  `--new-approval` retry supersedes the denial.
- Audit JSON lines are sufficient to reconstruct who resolved what action and
  when.

## Second Pass: Session and Resume Durability

Hermes' useful shape is not its conversation database. The useful pattern is
that restart recovery is driven by durable, inspectable state and recent
evidence. Forager keeps this task-centric:

- `task_resume_state.json` remains the canonical resume artifact;
- every new resume-pending row gets a `resume_id`;
- legacy rows without `resume_id` still load and use `project_key:task_id` as
  the operator-facing fallback;
- resume rows record the previous task status, attempt count, background probe
  evidence, artifact presence, and redacted log tail when available;
- `forager offdesk resume` prints the resume id and compact evidence summaries
  below each task row.

Do not replay chat transcripts as task truth. Transcript or message history can
be attached later as evidence only after it has an explicit Forager-owned source,
redaction policy, and retention boundary.

## Session/Resume Acceptance Checks

- Stale or failed background probes create exactly one resume-pending row for
  the affected task.
- Resume rows are sufficient to explain the next safe operator action without
  inspecting raw chat history.
- Log tails and evidence summaries are operator-safe redacted.
- Legacy `task_resume_state.json` rows still load and render through both JSON
  and human CLI output.

## Third Pass: Background Process Recovery

Hermes' useful shape is fast, low-cost observation of long-running work. Forager
keeps the backend-specific state in `background_runs.json` and records the last
poll evidence directly on each probe:

- every poll updates `last_observed_at`, `last_recovery_evidence`, and
  `last_recovery_terminal`;
- background probes can carry `worker_heartbeat_at` plus `heartbeat_timeout_sec`;
- local background probes with an alive process but stale heartbeat become
  `stale_lost_callback` instead of staying silently running;
- `forager offdesk poll` and `forager offdesk background` show observed time and
  redacted log tail in human output when available.

This does not introduce a new worker runtime. Heartbeat fields are durable
evidence that current and future runners can write into the existing Forager
probe record.

## Background Recovery Acceptance Checks

- Polling a completed run persists the recovery evidence and terminal flag.
- A stale heartbeat timestamp turns an otherwise alive local background probe
  into `stale_lost_callback`.
- Legacy background probe rows without heartbeat or observation fields still
  load and poll normally.
- Human and JSON poll output expose enough evidence to explain the phase change.

## Fourth Pass: Provider Profile and Error Classifier

Hermes' useful shape is the split between declarative provider facts, transport
adapters, and centralized error classification. Forager keeps the runtime and
scheduler policy Forager-owned:

- `ProviderProfile` records provider identity, auth env names, fallback model
  hints, and supported runner roles for the built-in backends;
- the legacy `ProviderDescriptor` and `classify_provider_error(...)` API remain
  available;
- `classify_provider_error_with_context(...)` preserves provider/model/status
  context and returns structured retry, compress, fallback, cooldown, and
  recommended-action hints;
- `ProviderCapacityStore` writes `provider_capacity.json` entries for cooldown
  class failures only, and the offdesk scheduler gate reads those entries before
  creating approvals;
- provider-capacity blocks now attach a `ProviderFallbackRecommendation` that
  orders same-provider model fallbacks before compatible cross-provider
  fallback/default models, marks auth and capacity availability, and creates
  only an approval candidate. It does not auto-route before an operator
  approves `dispatch.provider_fallback`.

Provider fallback remains approval-gated. Capacity state is durable scheduler
policy input for transient deferral and inspectable fallback recommendations;
after approval, the tick loop may retarget only `provider_id` and `model` for
queued or pending tasks in the same request and current provider/model scope. It
does not rewrite commands, workdirs, launch specs, or credentials.

## Provider Classifier Acceptance Checks

- Built-in provider profiles cover Anthropic, OpenAI, OpenAI-compatible, Claude
  Code CLI, and Codex CLI backends.
- Provider/model context survives classification and can be persisted in
  capacity state.
- Rate limit, overload, and retryable server errors produce cooldown records.
- Active provider/model cooldowns block offdesk dispatch before approval
  creation and return `retry_at` plus provider capacity metadata.
- `offdesk tick` keeps provider-capacity-blocked tasks queued and updates
  `not_before` instead of marking them failed, while persisting the latest
  fallback recommendation on the task.
- If the recommendation has available candidates, `offdesk tick` creates a
  `dispatch.provider_fallback` approval with operator-safe metadata for the top
  recommended candidates. A later approved tick revalidates the candidates,
  skips newly blocked options, retargets matching queued/pending tasks, clears
  `not_before`, and immediately re-evaluates dispatch.
- `forager offdesk provider-fallback --provider-id <ID> [--model <MODEL>]`
  returns the same operator-safe candidate ordering without retargeting a task.
- Context overflow and payload/image size failures request compression but do
  not create provider capacity cooldowns.
- Existing classifier callers keep working through the compatibility function.

## Fifth Pass: Checkpoint and Rollback

Hermes' useful shape is rollback evidence before risky mutation, not its shadow
git implementation. Forager keeps this as explicit mutation artifacts:

- `MutationSnapshot` records rollback metadata, before size/hash, truncation
  status, and blockers;
- snapshots over the inline evidence limit remain audit evidence only and are
  not marked rollback-capable;
- `forager offdesk snapshots`, `forager offdesk snapshot`, and
  `forager offdesk restore-plan` expose read-only verification and dry-run
  restore plans;
- `canonical.apply` remains blocked in offdesk mode.

This pass does not write restored files. Restore execution can be designed later
behind explicit canonical mutation approval and rollback audit records.

## Checkpoint/Rollback Acceptance Checks

- Legacy snapshot JSON loads with safe defaults for newly added fields.
- Small-file snapshots verify their before artifact hash and can produce a
  restore-file plan without modifying the target.
- Truncated snapshots and missing before artifacts report blockers and remain
  unavailable for rollback.
- Unknown mutation ids fail with a clear operator error.

## Sixth Pass: Tool Registry

Hermes' useful shape is a declared tool contract that the scheduler and
operator surfaces can inspect before execution. Forager keeps this in the Task
Team capability registry:

- `CapabilityDescriptor` now records approval scope, operator label,
  retry/resume eligibility, required artifacts, and produced artifacts;
- `forager offdesk capabilities --json` exposes this contract for dashboard and
  operator tooling;
- `SchedulerGateRequest` can carry artifact references, and required artifacts
  are checked before approvals are created;
- `canonical.syncback` requires a `mutation_snapshot` artifact, while
  background runtime capabilities declare the `background_run` artifacts they
  produce.

This pass does not introduce a new tool runner or skill system. The registry is
the contract layer that later policy can use for routing, dashboards, and
preflight validation.

## Tool Registry Acceptance Checks

- Every unsafe capability has approval metadata, approval scope, and operator
  labels.
- `canonical.syncback` declares a mutation snapshot requirement and is blocked
  before approval when it is missing.
- Runtime launch/retry capabilities declare background-run outputs and remain
  retry/resume eligible.
- Capability JSON output is stable enough for dashboard consumers to inspect
  risk, artifacts, and eligibility in one response.

## Seventh Pass: Redaction and Context Fencing

Hermes' useful shape is a hard boundary between runner-only context and
operator/shareable diagnostics. Forager keeps this as an explicit redaction
boundary plus local diagnostics export rather than a new upload system:

- `operator_safe_report(...)` returns sanitized text plus counts for runner
  context removal and secret redactions;
- existing `operator_safe_text(...)` callers keep working through the new
  reporting path;
- `forager offdesk debug-bundle --json` emits a sanitized bundle for approvals,
  task views, resume states, background probes, capabilities, provider capacity
  state, and persisted provider fallback metadata;
- `forager offdesk debug-bundle --export` writes the same sanitized JSON to
  `debug_bundles/` under the active profile, while `--output <PATH>` writes an
  explicit diagnostics path without silently overwriting existing files;
- debug bundle generation is read-only and bypasses migrations so inspecting an
  empty profile does not create profile storage unless export was explicitly
  requested.

This pass does not add archive packaging, upload integration, automatic
pruning, or TTL cleanup. Exported JSON bundles are preserved by default as local
operator diagnostics and can be deleted manually from the profile
`debug_bundles/` directory.

## Redaction/Context Acceptance Checks

- Runner-only context markers are stripped before operator or bundle output.
- Secret-like assignments, bearer/API keys, database URLs, JWTs, URL
  credentials, and query tokens are redacted with an auditable count.
- Redaction is idempotent for already sanitized text.
- `offdesk debug-bundle --json` sanitizes legacy/raw stored state without
  mutating the underlying approval, task, resume, background, or provider files.
- `offdesk debug-bundle --export` writes only the sanitized bundle payload and
  never copies raw stored state into diagnostics artifacts.

## Eighth Pass: Adaptive Wiki

Hermes' useful shape is not just memory recall. The stronger pattern is an
agent that can grow from a durable, operator-governed wiki while keeping raw
human-facing knowledge out of the model prompt. Forager keeps this as a
canonical Offdesk wiki record plus separate projections:

- `adaptive_wiki_candidates.json` stores observed but unpromoted learning
  candidates with evidence refs and occurrence counts;
- `adaptive_wiki_entries.json` stores canonical entries with kind, scope,
  status, activation mode, optional agent-mode tags, claim, AI instruction,
  human summary, evidence refs, confidence, and review metadata;
- the AI projection includes only promoted entries matching scope and requested
  agent mode, while entries with no mode tags remain shared guidance; it redacts
  the compact instruction before gate/launch/tick outcomes expose it;
- if execution reaches gate/launch/tick without an agent mode, only shared
  entries are projected, while human inspection can still audit all tagged
  entries;
- approved launches can attach a fenced runtime context block to the background
  probe or handoff and write `adaptive_wiki_usage.jsonl` records for the
  projected entries;
- the human projection keeps sanitized governance context, including summaries,
  evidence refs, counterexamples, status, activation mode, agent modes, and
  candidate hits.

This pass does not make wiki entries an authority for runtime mutation and does
not enable `auto_apply`. Candidate observation, promotion, dashboard actions,
and benchmark episodes remain separate integration steps. The design and
benchmark contract live in [`adaptive-wiki.md`](adaptive-wiki.md), and the
implementation sequence lives in
[`adaptive-wiki-execution-plan.md`](adaptive-wiki-execution-plan.md).

## Adaptive Wiki Acceptance Checks

- Candidate records merge repeated claims in the same kind/scope and retain
  unique evidence refs.
- AI projection excludes candidates, deprecated entries, human summaries, and
  out-of-scope or out-of-agent-mode entries while preserving untagged shared
  entries.
- Gate/launch/tick outcomes may expose matching promoted wiki entries as
  metadata, and approved launches may attach fenced runtime context plus usage
  records, but they do not rewrite commands, provider/model selection,
  workdirs, launch specs, or approval decisions.
- AI and human projections redact secrets and runner-only context before
  operator/runtime surfaces consume them.
- Legacy or partial adaptive wiki JSON loads with safe defaults for new fields.
- Promotion creates a promoted canonical entry and removes the candidate without
  auto-applying behavior.

## Remaining Adaptive Knowledge Benchmark

The second Hermes pass found three additional patterns worth adapting behind
Forager-owned interfaces:

- `MemoryProvider` lifecycle hooks are useful as an event vocabulary, but
  Forager should translate them into Offdesk events such as task completion,
  approval resolution, resume creation, pre-compression extraction, and wiki
  projection usage. It should not add a generic external memory provider as
  canonical truth.
- Hermes `llm-wiki` is the strongest human wiki model: immutable `raw/`
  evidence, generated `SCHEMA.md`, `index.md`, append-only `log.md`, source
  hashes, provenance markers, confidence, contested pages, and drift lint.
  Forager should adapt this as one-way markdown export from canonical JSON.
- Hermes curator is useful as deterministic usage/staleness accounting plus
  review reports. Forager should keep curator behavior recommendation-only
  until every proposed wiki mutation can pass through explicit commands,
  approvals, and audit records.
- Hermes skills are useful as procedural-memory bundles with progressive
  disclosure, support files, platform/tool conditions, and config metadata.
  Forager should map this to governed `procedure` entries and runbook export,
  not to autonomous skill writes.

The detailed adoption matrix and next implementation slices are in
[`hermes-adaptive-knowledge-benchmark.md`](hermes-adaptive-knowledge-benchmark.md).
