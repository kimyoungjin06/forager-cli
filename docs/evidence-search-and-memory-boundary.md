# Evidence Search And Memory Boundary

This note records benchmarking passes over MemPalace, Headroom, and a Kurly
operator workflow case study, then translates the useful patterns into
Forager's evidence-governed model.

MemPalace is a useful reference because it starts from a strong simple premise:
store source material first, then make it searchable. Forager should adopt that
premise for evidence retrieval, but not adopt a hidden memory layer that can
turn recalled text into project truth without review.

## Source Scope

Official MemPalace sources inspected:

- <https://github.com/MemPalace/mempalace>
- <https://raw.githubusercontent.com/MemPalace/mempalace/main/README.md>
- <https://raw.githubusercontent.com/MemPalace/mempalace/main/hooks/README.md>
- <https://raw.githubusercontent.com/MemPalace/mempalace/main/docs/rfcs/002-source-adapter-plugin-spec.md>
- <https://raw.githubusercontent.com/MemPalace/mempalace/main/benchmarks/BENCHMARKS.md>

Search results show several similarly named domains and mirrors. Forager should
benchmark against the official GitHub repository, package, and official docs
only when revisiting this comparison.

Headroom sources inspected:

- <https://github.com/chopratejas/headroom>
- <https://github.com/chopratejas/headroom/blob/main/README.md>
- <https://github.com/chopratejas/headroom/blob/main/llms.txt>
- `docs/content/docs/architecture.mdx`
- `docs/content/docs/ccr.mdx`
- `docs/content/docs/failure-learning.mdx`
- `docs/content/docs/memory.mdx`
- `docs/content/docs/mcp.mdx`
- `docs/content/docs/benchmarks.mdx`
- `docs/content/docs/limitations.mdx`
- `docs/content/docs/cache-optimization.mdx`

Kurly source inspected:

- <https://helloworld.kurly.com/blog/claude-code-redesign-my-day>

## Why This Direction Fits Forager

The proposed Forager design is useful because it separates four things that
memory systems often blend together:

| Layer | Forager meaning | Authority |
| --- | --- | --- |
| Raw evidence | Logs, transcripts, closeouts, receipts, reports, artifacts | Inspectable source material |
| Evidence index | Searchable pointers, excerpts, hashes, source metadata | Retrieval aid only |
| Candidate knowledge | Possible lessons or facts derived from evidence | Review queue |
| Promoted knowledge | Reviewed adaptive wiki entries, facts, runbooks, or deliverables | Canonical after receipt |

This preserves Forager's core boundary: recall can help an agent find evidence,
but recall does not make a claim accepted. A completed search result still
needs review, evidence refs, and an explicit promotion path before it changes
project knowledge.

This design also addresses a current long-run problem. As projects accumulate
run folders, logs, reports, screenshots, and generated docs, humans should not
need to open every file to find the current evidence. A search layer gives
agents and operators a shallow discovery surface while keeping deep source
artifacts in place.

The three benchmarks point to complementary layers:

| Reference | Useful layer | Forager translation |
| --- | --- | --- |
| MemPalace | Evidence recall and source adapters | Raw evidence index and reviewed knowledge promotion |
| Headroom | Context budget and reversible compression | Compressed context packets with source retrieval |
| Kurly operator workflow | Daily operation and shared improvement loop | Operator briefings, workflow decomposition, and control-tower governance |

## Project-Wide Translation

These benchmarks should become reusable operating primitives, not isolated
feature ideas. The project-wide shape is:

```text
collect source material
  -> index and normalize evidence
  -> build a bounded context packet
  -> draft an implementation packet when work will be delegated
  -> route judgment to the best available evaluator
  -> ask for human decision only when needed
  -> promote reviewed knowledge with receipts
```

The judgment step is deliberately not tied to Claude Code. Forager can route it
to:

- Council, when the issue needs competing perspectives, tradeoff analysis, or
  user-facing recommendation;
- a single harness-backed agent, when the work is narrow and one capable agent
  is enough;
- a deterministic gate, when checks, schema validation, test results, or policy
  rules can decide the outcome;
- the user, when the remaining choice is preference, risk tolerance, or
  authority rather than analysis.

The reusable project primitives are:

| Primitive | Role | Reused by |
| --- | --- | --- |
| Source adapter | Collects raw artifacts with hashes and source metadata | Offdesk, harness transcripts, docs, external systems |
| Evidence index | Makes source material searchable without promoting it | Search, Telegram cards, ondesk handoff, retention review |
| Context packet | Compresses handoff context while preserving retrieval | Hosted agents, Council, fresh-session resume |
| Implementation packet | Preserves original intent, scope, brand fit, stop conditions, and validation before delegation | Offdesk launch, local-model episodes, hosted harness work, Council design review |
| Recursive alignment review | Checks whether a plan still serves the original goal and Forager's north star | Packet review, closeout, Ondesk resume |
| Judgment route | Selects Council, agent, deterministic gate, or user | Decisions, briefings, closeouts, review flows |
| Decision packet | Presents options, evidence, disagreement, and default | Telegram, TUI, Web UI, CLI JSON |
| Promotion receipt | Turns reviewed candidates into durable knowledge | Adaptive wiki, runbooks, shared rules |
| Briefing projection | Shows the right slice of evidence for the operator | Ondesk, offdesk closeout, daily status, Council prep |

This gives every major module the same boundary: collect and preserve first,
compress only with retrieval, judge through the appropriate route, and promote
only after review.

## Project-Wide Adoption Scope

The benchmark lessons can apply across the project, but not at the same depth
or priority. The immediate goal is to add shared contracts where Forager already
has matching surfaces, then expand into indexing and briefing once those
contracts prove stable.

| Area | Current Forager surface | Applicable primitives | Plan addition | Priority |
| --- | --- | --- | --- | --- |
| Decision pipeline | `DecisionRecord`, `DecisionRoute`, `CouncilReview`, `approval_brief.v1` | Judgment route, decision packet | `judgment_route.v1` is now the evaluator route; delivery/execution routing remains separate. | P1 |
| Telegram and approval cards | Telegram relay, approval brief renderer, natural-language replies | Decision packet, briefing projection | Show route reason, evaluator type, recommendation, evidence sufficiency, and consequence without raw ids as primary text. | P1 |
| Review surface and Ondesk handoff | `review_surface.v1`, `forager ondesk prompt-package`, Telegram detail cards | Context packet, judgment route, implementation packet, evidence refs | Surface recent judgment routes, accepted-truth status, design packet readiness, and retrievable evidence/context packet refs. | P1 |
| Design-first delegation | `implementation_packet.v1`, Offdesk task/launch metadata, review/Ondesk projection, future packet-aware closeout | Implementation packet, recursive alignment review, judgment route | Review original intent, north-star fit, brand fit, scope balance, evidence, and stop conditions before local or hosted worker execution. | P1 |
| Offdesk closeout | Closeout plan, return package, closeout receipt | Source adapter, evidence index, decision packet | Treat closeout artifacts as indexed evidence sources and route unresolved review through the same judgment contract. | P1/P2 |
| Artifact governance | `artifact_index.v1`, retention review, retention approvals | Evidence index, promotion receipt | Add source hashes and transformation labels consistently; connect retention choices to evidence refs. | P2 |
| Adaptive wiki | Candidates, promotion receipts, review-after checks | Failure-learning candidates, control-tower rules | Convert repeated failures into candidates with success correlation; promote shared rules only by reviewed receipt. | P2 |
| Hosted harness agents | Hosted workload contracts, provider/model routing, prompt packages | Context packet, cache alignment, shared context | Launch agents from compact packets with source retrieval rather than full transcript replay. | P2 |
| Evidence search | Future source registry and index | Source adapter, evidence index, read-time presentation | Add read-only indexing and search before semantic memory or writable MCP tools. | P2 |
| Daily/operator briefings | Ondesk handoff script, status/review summaries | Briefing projection, routed judgment | Build deterministic collection first, then route judgment to Council, one harness, deterministic gate, or user. | P2 |
| External integrations | CLI JSON, future MCP/local API | Read-only evidence and status tools | Expose read-only discovery first; gate writes through existing approvals and receipts. | P3 |

The most important distinction is between evaluator routing and delivery
routing. `judgment_route.v1` should answer who or what evaluated the issue:
Council, one harness-backed agent, deterministic gate, or user. Existing
delivery or execution routing can still answer where the result goes next:
agent, approval ledger, closeout, handoff, or receipt.

## Plan Additions

### P1: Make Routed Judgment Real In Existing Surfaces

Implemented first slice: `judgment_route.v1` is now part of the decision spine
before adding new UI:

- `DecisionRecord` carries evaluator kind, route reason, policy
  basis, evidence refs, and selected/default outcome;
- CLI `decision list/show` displays evaluator route separately from
  delivery target;
- route summaries are projected into `review_surface.v1`;
- Telegram and approval cards show why Council, one harness,
  deterministic gate, or user decision is being used;
- natural-language replies remain scoped to the selected decision and route.

Acceptance signal: a fresh operator can tell whether a pending decision was
evaluated by Council, by one harness, by deterministic checks, or by the user,
and can see why that route was chosen.

### P1/P2: Preserve Evidence References Before Compressing Context

Context packets should wait until evidence refs are stable enough to retrieve
the omitted material.

- define a common evidence/source ref shape for decisions, review surfaces,
  closeout receipts, retention reviews, and prompt packages;
- ensure compact surfaces omit detail only when a richer contract can retrieve
  it;
- add source hashes and transformation labels where artifacts are generated,
  redacted, summarized, or line-numbered at read time.

Acceptance signal: a compact decision or handoff can point back to the exact
source rows or artifacts that support it.

### P1/P2: Add Design-First Implementation Packets

Before local-model overnight work or substantial hosted harness delegation,
Forager should create a packet that keeps the original purpose visible:

- original user goal and success state;
- north-star and brand fit;
- included scope, excluded scope, and non-authorized actions;
- affected functional capabilities and data contracts;
- work slices, stop conditions, validation commands, and expected artifacts;
- recursive alignment review outcome: `pass`, `revise`, or `block`.

This is where the benchmarking lessons become operational. Kiro and Spec Kit
show why requirements/design/tasks should be explicit before implementation.
Claude Code plan mode and Aider architect/editor mode show why design and
execution roles should be separable. SWE-agent trajectories show why closeout
must compare actual execution against the intended path. LangGraph human-in-loop
patterns show why interruption and resume must keep the same state boundary.

Acceptance signal: a delegated worker can implement from the packet, and
morning review can tell whether the run served the original goal rather than
only finishing a narrow subtask.

### P2: Add Evidence Indexing And Context Packets

Once refs are stable, add the MemPalace and Headroom layers:

- source adapter registry;
- local read-only evidence index;
- transcript/offdesk backfill;
- `context_packet.v1` for hosted harness launch, Council review, and Ondesk
  resume;
- rebuild and drift checks for the index and packets.

Acceptance signal: a fresh harness can resume from a compact packet and retrieve
the full evidence without chat scrollback.

### P2: Turn Failure Learning Into Candidates

Headroom's failure-learning idea should become an adaptive wiki candidate path,
not an automatic rewrite of agent instruction files.

- mine failed sessions, corrected commands, wrong paths, missing search scopes,
  and repeated user corrections;
- attach success correlation and evidence refs;
- group repeated issues into a control-tower inbox;
- promote only through adaptive wiki or runbook receipts.

Acceptance signal: repeated operational mistakes produce reviewable candidates,
not silent changes to `AGENTS.md`, `CLAUDE.md`, or shared rules.

### P2/P3: Build Operator Briefings From The Same Contracts

Daily or ondesk briefings should reuse the same evidence, context packet, and
judgment route contracts.

- deterministic collectors gather source artifacts first;
- Forager builds a briefing projection from indexed evidence and review state;
- Council, a single harness, deterministic gates, or the user handles judgment
  according to route policy;
- publish/upload remains a separate reviewed stage.

Acceptance signal: daily status, ondesk handoff, Council prep, and retention
review all use the same source refs and accepted-truth boundaries.

### Explicit Non-Goals For This Adoption

- Do not make Claude Code, Codex, Headroom, or any one harness the product
  boundary.
- Do not introduce transparent proxy compression as the default runtime path
  before retrieval, observability, and privacy semantics are proven.
- Do not let memory extraction or failure learning mutate project truth without
  candidate review and promotion receipts.
- Do not make Telegram, WebUI, or any external integration canonical state.

## MemPalace Patterns To Adapt

### Verbatim-First Retrieval

MemPalace's strongest lesson is that raw source text plus retrieval is a better
baseline than premature extraction. Forager should apply the same idea to:

- Offdesk logs and progress records;
- closeout packages;
- approval briefs and decision receipts;
- adaptive wiki candidate evidence;
- project artifact indexes and retention receipts;
- Claude Code, Codex, OpenCode, Gemini CLI, and tmux transcripts;
- human-facing deliverables and generated reports.

Forager should store or index enough raw material to recover context, tradeoffs,
and failed alternatives. It should not rely only on LLM summaries.

### Source Adapter Contract

MemPalace's source adapter RFC is directly relevant. Forager should introduce a
source adapter contract before adding many one-off ingesters.

Useful fields for a Forager adapter:

- `adapter_name`
- `adapter_version`
- `source_kind`
- `source_uri`
- `project_key`
- `run_id`
- `task_id`
- `artifact_id`
- `source_hash`
- `source_version`
- `privacy_class`
- `declared_transformations`
- `chunk_strategy`
- `ingested_at`

The key adoption is not the exact Python interface. The key adoption is the
contract: every ingester declares where data came from, whether it changed the
content, which metadata is stable, and how to determine whether an item is
current.

### Declared Transformations

MemPalace's strongest safety improvement is turning "verbatim" from a broad
claim into a checked capability. Forager should copy this principle.

Examples:

- `byte_preserving`: exact source bytes or decoded text are indexed.
- `line_numbered_at_read_time`: line numbers are display-only and not stored in
  the source text.
- `json_field_extraction`: structured fields were extracted from JSON.
- `tool_output_truncated`: long tool output was shortened for indexing.
- `redacted_secret`: secret-like spans were removed or replaced.
- `summary_generated`: a synthetic summary was indexed beside the source.

Every evidence search result should tell downstream consumers whether it is
raw, transformed, redacted, or synthetic.

### Read-Time Presentation

MemPalace has a useful line-numbering principle: add line numbers at read time
instead of rewriting stored drawers. Forager should use the same approach for
evidence excerpts.

This matters because source artifacts are evidence. Display conveniences should
not mutate them. Forager can show line numbers, excerpt bounds, syntax labels,
and path badges in human output while keeping the underlying source hash stable.

### Hooks And Backfill

MemPalace's hooks capture Claude Code and Codex sessions around stop and
pre-compact events, then backfill old transcripts. Forager can adapt the shape
without adopting hidden automatic memory writes.

Forager equivalents:

- `forager evidence backfill --source claude-code`
- `forager evidence backfill --source codex`
- `forager evidence backfill --source offdesk-profile`
- `forager evidence ingest --source tmux-session`
- future host hooks that write evidence-index records only

Hooks should be explicit, trusted, and profile-aware. They should record
evidence or candidates, not promote durable knowledge.

### MCP Read Surface

MemPalace's MCP tool set is large, but the useful Forager subset is smaller and
more governed.

First useful read-only MCP tools:

- `forager_status`
- `forager_list_profiles`
- `forager_search_evidence`
- `forager_get_evidence`
- `forager_get_closeout`
- `forager_list_pending_decisions`
- `forager_list_wiki_candidates`
- `forager_get_promotion_chain`

Writable MCP tools should come later and should call existing approval-gated
commands. Forager should avoid direct MCP writes to adaptive wiki entries,
artifact retention state, approval ledgers, or runner sidecars.

### Temporal Fact Graph

MemPalace's local temporal knowledge graph is useful as a concept, but Forager
should not make it an automatic memory sink.

Forager-compatible translation:

```text
raw evidence
  -> fact candidate
  -> reviewed fact proposal
  -> accepted temporal fact with evidence refs and receipt
```

Potential uses:

- project decision timelines;
- provider capacity and failure-mode histories;
- harness comparison history;
- artifact lifecycle transitions;
- wiki entry validity windows;
- recurring operator preference changes.

### Integrity And Recovery

MemPalace has practical operational lessons around repair, reconnect, sync
dry-runs, index metadata recovery, and stale backend state. Forager should
expect the evidence index to need similar care.

Forager should design from the start for:

- read-only health checks;
- index rebuild from source artifacts;
- source hash drift detection;
- dry-run sync reports;
- cache reconnect or invalidation;
- corruption recovery that preserves source evidence;
- explicit distinction between missing source and stale index.

## Headroom Patterns To Adapt

Headroom is not primarily an evidence system. It is a context budget layer that
sits between tools, agents, and the model. The Forager value is not to wrap
every model call immediately; the value is to make evidence packets, handoffs,
and agent-to-agent context compact without losing the original.

### Compress-Cache-Retrieve For Handoffs

Headroom's CCR pattern is useful because compression is reversible. Forager can
use the same idea for operator cards, handoff packets, and fresh-harness resume
context:

```text
large evidence packet
  -> compact context packet shown to agent or operator
  -> source refs and hashes retained
  -> retrieve full evidence on demand
```

Forager should call this a context packet, not accepted knowledge. A compressed
packet should always carry:

- source refs;
- source hashes;
- transform labels;
- omitted-content counts;
- retrieve commands or artifact ids;
- expiry or rebuild policy.

### Content Routing

Headroom routes JSON, logs, code, text, and images differently. Forager should
use the same principle for evidence indexing and summaries:

- JSON receipts and progress events: preserve keys, anomalies, errors, and
  state transitions.
- Logs: cluster repetitive lines, preserve failures and warnings.
- Code and diffs: keep exact source refs and avoid compression when review,
  debug, or edit intent is active.
- Reports and docs: summarize for navigation, keep original artifact refs.
- Screenshots and media: index metadata and captions first, full visual review
  remains separate.

### Cache Alignment

Headroom moves dynamic prompt content away from stable prefixes to improve
provider cache hits. Forager can adapt this in generated handoffs:

- stable instructions first;
- project and mode rules second;
- dynamic date, task id, and runtime refs near the tail;
- large evidence packets attached by refs rather than repeated in full;
- explicit context kind labels for redaction and scrubbers.

This should improve repeat launches of similar hosted harness agents without
changing Forager's approval or evidence model.

### Failure Learning As Candidate Generation

Headroom's `learn` flow mines failed sessions and writes corrections to agent
context files. The useful Forager version is more governed:

```text
tool/session failure
  -> failure pattern candidate
  -> evidence refs and success correlation
  -> review report
  -> adaptive wiki candidate or runbook proposal
  -> explicit promotion receipt
```

Useful analysis categories:

- wrong path followed by successful path;
- command that fails outside the right runtime;
- repeated permission or sandbox failure;
- known large files that need paging;
- rejected operator behavior;
- search scope that repeatedly misses.

Forager should copy dry-run first, marker-bounded output, and agent-specific
writers only after adding review receipts. It should not auto-write `AGENTS.md`
or `CLAUDE.md` from analysis without approval.

### Shared Context Across Agents

Headroom's SharedContext is relevant to Forager's hosted harness agents. A
research agent, code agent, reviewer, and Council agent should not each receive
the full prior transcript. They should receive a bounded context packet and
retrieve full evidence only when needed.

Forager-compatible fields:

- `context_packet_id`
- `producer_agent`
- `consumer_agent`
- `source_task_id`
- `source_evidence_refs`
- `compressed_tokens`
- `original_tokens`
- `transforms`
- `retrieval_count`

### Safety Gates

Headroom's limitation docs are as important as its claims. Forager should adopt
the same posture:

- short content passes through;
- user intent is never compressed;
- recent code and active debug/review context are protected;
- malformed content passes through;
- compression that expands content is rejected;
- every dropped or compressed item remains retrievable or explicitly marked.

## Kurly Operator Workflow Patterns To Adapt

The Kurly article is valuable because it is not a library design. It describes
how an operator's day changes when AI work is turned into repeatable workflows,
shared context, and improvement loops.

### Scripts For Collection, Routed Judgment

The strongest operating lesson is to separate deterministic collection from
judgment. The Kurly workflow moved Slack, Jira, Confluence, and PR collection
out of repeated model tool calls and into scripts that first write source files.
Forager should generalize the next step: the source packet can be judged by
Council, by a single available harness-backed agent, or by deterministic gates
depending on risk, scope, and capability.

Forager should apply the same rule:

- deterministic collectors gather source artifacts;
- Forager records source hashes and timestamps;
- the judgment route selects Council, a single harness-backed agent,
  deterministic gates, or user decision;
- the selected evaluator summarizes and reasons over bounded evidence packets;
- collection failures are runtime evidence, not model confusion;
- repeated collection becomes a reusable module operation profile.

This directly supports the evidence source registry and reduces token spend.

### Decompose Big Commands Into Retryable Stages

Kurly split briefing creation into collection, per-person/project summary,
briefing page generation, and upload. Forager should use this as a default
shape for long-running tasks:

```text
collect
  -> normalize/index
  -> summarize/reason
  -> publish/promote
```

Each stage should have its own receipt, retry boundary, and failure evidence.
This is better than one large agent prompt whose failure is hard to localize.

### Reusable Intermediate Data

Kurly's intermediate summary database becomes input for weekly drafts, current
status queries, and town-hall preparation. Forager's equivalent is the evidence
index plus context packets:

- one collection pass can feed daily briefings, ondesk handoffs, retention
  reviews, Council packets, and wiki candidates;
- evidence indexing should prefer reusable normalized records over one-off
  summaries;
- generated briefings should cite the reusable source rows they used.

### Roundtable As Council Pattern

Kurly uses a roundtable-style discussion with multiple organizational
perspectives, then keeps final judgment with the human. This matches Forager's
Agent -> Council -> User decision model.

Forager should make judgment packets stronger by recording:

- which perspectives were requested;
- what evidence each perspective used;
- where perspectives disagree;
- what the recommended default is;
- which assumption the user must approve or reject;
- why the selected route was Council, one agent, deterministic gate, or user.

### Control Tower For Shared Rules

Kurly's "team lead" project acts as a shared rule source that workflows import.
Forager has the same need across hosted harness agents, adaptive wiki
projections, and workflow-specific runbooks.

Forager translation:

- shared rules live in reviewed adaptive wiki entries or governed runbooks;
- workflow-specific rules import shared rules through generated projection
  packets;
- a workflow can propose shared rule changes but cannot mutate shared rules
  directly;
- accepted shared rules carry promotion receipts and scope.

### Self-Improvement Loop With Approval

The Kurly flow checks session issues, repeated mistakes, user complaints, and
environment limits, then proposes rule changes. The critical Forager adaptation
is approval and evidence:

```text
session closeout
  -> issue classification
  -> candidate improvement
  -> control-tower inbox
  -> review and promotion
  -> projection to relevant workflows
```

This is close to Forager's existing adaptive wiki candidate lifecycle. The
improvement is to make cross-workflow propagation more explicit.

### Personal To Organizational Scaling

The article's final question is organizational: where is the bottleneck, what
is AI's role, where must humans stay in the loop, and how should shared context
accumulate. This is a product-level benchmark for Forager. The system should
help an operator answer those questions per project and per workflow, not just
run agents faster.

## Patterns To Reject Or Defer

| Pattern | Decision | Reason |
| --- | --- | --- |
| Free-form memory writes as canonical truth | Reject | Conflicts with reviewed promotion. |
| Palace metaphor as product vocabulary | Reject for primary UX | Forager's domain is operations, evidence, decisions, and artifacts. |
| Automatic KG mutation from transcripts | Defer | Needs candidate and review receipts first. |
| ChromaDB as required core dependency | Defer | Rust CLI core should stay lightweight; semantic backend can be optional. |
| Compression dialect as default operator surface | Defer | Forager needs auditability and readable handoff surfaces first. |
| Delete/prune through memory sync | Reject in first pass | Cleanup must go through artifact retention approvals and receipts. |
| Benchmark headline based on recall only | Reject | Forager needs task/evidence/resume metrics, not memory recall alone. |
| Inline memory extraction into project truth | Reject | Conflicts with candidate and promotion receipts. |
| Transparent proxy compression as default runtime | Defer | Needs observability, retrieval, privacy, and failure semantics first. |
| Auto-writing shared rules from failure analysis | Reject in first pass | Rule changes need review and scope. |

## Forager Evidence Search MVP

The first implementation should be read-only and should not require a vector
database.

### Slice 1: Evidence Source Registry

Define source kinds and discovery rules:

- `offdesk_closeout`
- `approval_receipt`
- `runtime_log`
- `progress_event`
- `artifact_index`
- `retention_receipt`
- `adaptive_wiki_candidate`
- `adaptive_wiki_promotion`
- `project_deliverable`
- `harness_transcript`

Acceptance criteria:

- every indexed row has a stable source ref and source hash;
- every row records whether it is raw, transformed, redacted, or synthetic;
- no source artifact is moved or edited.

### Slice 2: Local Evidence Index

Add a profile-local or project-local index that can be rebuilt from source
artifacts.

Recommended first backend:

- SQLite FTS5 or a simple SQLite table plus deterministic excerpt generation.

Optional later backend:

- semantic embeddings behind a feature flag or extension package.

Acceptance criteria:

- `forager evidence index --dry-run --json` previews ingest counts;
- `forager evidence index --apply --json` writes only index records;
- `forager evidence search <query> --json` returns source refs, snippets,
  transformation labels, and confidence fields;
- rebuilding the index from source produces stable row identities.

### Slice 3: Transcript Backfill

Add explicit transcript backfill for local harnesses:

- Claude Code JSONL;
- Codex sessions;
- Forager tmux/offdesk logs.

Acceptance criteria:

- transcript backfill is opt-in;
- source paths are scoped by project/profile;
- tool output is preserved or explicitly marked as truncated/redacted;
- repeated backfill is idempotent.

### Slice 4: Candidate Bridge

Let evidence search create adaptive wiki candidates, not promoted entries.

Acceptance criteria:

- candidate creation requires explicit command or approval;
- candidate records cite evidence search result ids and source hashes;
- promotion still uses the existing adaptive wiki review path.

### Slice 5: Context Packets

Add a reversible compact context format for handoffs and hosted harness agent
launches.

Acceptance criteria:

- each packet records original token count, compact token count, source refs,
  and transform labels;
- omitted sections remain retrievable through Forager evidence commands;
- user intent and current debug/review code stay uncompressed;
- packets expire or rebuild when source hashes drift.

### Slice 6: Failure-Learning Candidates

Mine failed sessions and closeouts into adaptive wiki or runbook candidates.

Acceptance criteria:

- dry-run first;
- candidates include failure evidence and success correlation;
- no direct writes to `AGENTS.md`, `CLAUDE.md`, or shared rules;
- repeated issues can be grouped into a control-tower inbox for review.

### Slice 7: Daily Operator Briefing

Add a deterministic collection plus routed judgment path for daily or ondesk
briefings.

Acceptance criteria:

- collection is script-driven and records source artifacts before judgment;
- briefing sections cite source rows;
- evaluator route is recorded as Council, single harness, deterministic gate,
  or user;
- upload/publish is a separate reviewed stage;
- intermediate data can feed handoffs, retention review, and Council packets.

## Benchmark Metrics For Forager

Forager should not copy memory recall metrics as its product score. The better
benchmark set is:

| Metric | Question |
| --- | --- |
| Evidence findability | Can a fresh harness find the right source artifact quickly? |
| Resume sufficiency | Can a fresh harness resume without chat scrollback? |
| Decision sufficiency | Does the operator card include enough evidence to choose? |
| Promotion precision | Do promoted wiki entries reduce repeated corrections? |
| Review load | How many candidates are produced per useful promotion? |
| Drift detection | Does the index detect moved, changed, or missing sources? |
| Recovery reliability | Can the index be rebuilt after corruption or deletion? |
| Privacy correctness | Are sensitive sources labeled, redacted, or rejected as configured? |
| Context compression safety | Can compact packets preserve answer quality with retrieval available? |
| Judgment routing fitness | Was Council, a single harness, deterministic gate, or user decision chosen appropriately? |
| Stage retryability | Can failed collection, summary, publish, or promotion stages be retried alone? |
| Rule propagation precision | Do shared-rule promotions help multiple workflows without over-scoping? |

These metrics align with Forager's north star better than top-k memory recall.

## Open Questions

1. Should evidence indexing be profile-local, project-local, or both?
2. Should transcript backfill be limited to active projects by default?
3. Which source kinds need secret scanning before indexing?
4. What is the first acceptable semantic backend: optional ChromaDB, Tantivy,
   SQLite vector extension, or external MCP memory system?
5. Should Forager expose an MCP server before evidence search has promotion
   receipts and privacy labels?
6. How should evidence search results be cited inside approval briefs and
   Telegram cards without leaking local paths as the main user surface?
7. Which artifacts deserve full-text indexing versus metadata-only indexing?
8. Should context packet compression be built in Rust first or delegated to an
   optional Headroom-compatible sidecar?
9. What review threshold should promote a workflow-local lesson into a shared
   control-tower rule?
10. Which operator briefings should exist first: morning ondesk, offdesk
   closeout, daily project status, or Council decision prep?
11. What policy chooses Council versus one harness-backed agent versus a
    deterministic gate for each workflow?

## Recommendation

Adopt MemPalace's retrieval discipline, not its product boundary.

Forager should add an evidence search layer that is:

- local-first;
- source-ref and hash backed;
- adapter-based;
- transformation-aware;
- rebuildable;
- initially read-only;
- connected to adaptive wiki candidates and artifact retention workflows only
  through explicit receipts.

After that, Forager should add reversible context packets and failure-learning
candidates. The daily briefing and control-tower rule propagation should build
on those foundations, because they need the same source refs, hashes,
transform labels, and review receipts.

This strengthens Forager's ability to return users to evidence and continuity
without turning memory recall, compression, or automatic workflow learning into
accepted truth.
