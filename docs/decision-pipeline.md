# Decision Pipeline

The Decision Pipeline is Forager's canonical state model for Offdesk choices
that are too important to leave inside an agent transcript, but not all
important enough to interrupt the operator immediately.

It sits above existing approval and Telegram surfaces:

```text
Decision Pipeline = what must be decided, by whom, and why
Approval Ledger   = which runtime or canonical mutation is authorized
Approval Brief    = compact user-facing projection for Telegram/WebUI/TUI
Execution Handoff = bounded instruction passed back to an agent or harness
Decision Receipt  = audit record closing the decision loop
```

The pipeline does not replace `approval_brief.v1` or
`PendingActionApproval`. It gives them a shared parent record.

## Premise

Offdesk autonomy should keep moving through safe, policy-resolved choices while
escalating material decisions to the operator with enough context to choose.

The pipeline exists to preserve Forager's invariants:

- Council can advise, but it is not an executor.
- Telegram can display a decision, but it is not canonical state.
- Approval briefs are projections, not the full decision record.
- Execution handoffs are created only after approval or policy resolution.
- Completion receipts are separate from task completion status.

## Product Fit

This design follows the Gajae-Code benchmarking conclusion: unclear delegation
requires a better decision structure, not stronger unchecked autonomy.

Forager translates that into:

```text
Agent raises issue
  -> evidence and context packet are assembled
  -> implementation packet is drafted when delegated execution needs design
  -> Judgment Router selects Council, one harness, deterministic gate, or user
  -> selected evaluator reviews evidence and options
  -> Decision Router classifies delivery and authorization path
  -> policy-resolvable: guidance or receipt is produced without interruption
  -> material: User receives a compact decision brief
  -> approved choice becomes execution handoff
  -> result closes with decision receipt
```

## Canonical Record

The canonical artifact is `DecisionRecord`.

Suggested first storage target:

```text
<profile-dir>/offdesk_decisions.jsonl
```

A task-scoped run may also mirror records into its run directory for closeout
and handoff packaging, but profile JSONL remains the durable decision ledger.

### `DecisionRecord`

```json
{
  "schema": "decision_record.v1",
  "decision_id": "decision-uuid-or-stable-id",
  "project_key": "<project-key>",
  "request_id": "request-id",
  "task_id": "task-id",
  "raised_by": "agent",
  "source_surface": "offdesk.council",
  "materiality": "high",
  "status": "user_pending",
  "created_at": "2026-06-01T00:00:00Z",
  "updated_at": "2026-06-01T00:00:00Z",
  "decision_request": {},
  "council_review": {},
  "route": {},
  "approval_brief": {},
  "execution_handoff": null,
  "decision_receipt": null,
  "trace_refs": []
}
```

## Status Model

| Status | Meaning | May create execution handoff? |
| --- | --- | --- |
| `draft` | Producer is assembling the decision request. | no |
| `council_review` | Council is reviewing options, evidence, or reviewer outputs. | no |
| `auto_resolved` | Router or Council resolved it under existing policy. | yes, if route permits |
| `user_pending` | Operator decision is required. | no |
| `approved` | Operator or policy approved the selected direction. | yes |
| `revised` | Operator supplied a revised direction. | yes, after normalized handoff |
| `denied` | Operator denied the action or direction. | no |
| `deferred` | Operator or router deferred pending more evidence. | no |
| `handoff_ready` | Approved direction has been converted into a bounded handoff. | yes |
| `applied` | The handoff was consumed by an agent/runtime path. | no new handoff without new decision |
| `receipted` | A decision receipt closed the loop. | no |

State changes should append to the decision ledger. A compact latest-state view
may be derived for CLI/TUI, but append-only history is the audit surface.

## Core Objects

### `DecisionRequest`

Producer-facing object. It explains what stalled or branched the work.

Recommended fields:

```json
{
  "kind": "council_escalation",
  "summary": "Council recommends revision before continuing the episode.",
  "decision_needed": "Choose whether to continue, revise, block, or stop.",
  "why_now": [
    "The current council decision is not continue.",
    "The next episode direction changes the run outcome."
  ],
  "current_scope": "Next episode direction only.",
  "non_authorized_scope": [
    "runtime dispatch",
    "provider retargeting",
    "cleanup",
    "wiki promotion"
  ],
  "options": [],
  "evidence_refs": [],
  "trace_refs": []
}
```

`DecisionRequest` may contain paths and trace references because it is internal.
Those fields must not be copied directly into user-facing cards.

### `CouncilReview`

Internal advisory output. It normalizes role/reviewer outputs into a form the
router can use.

Recommended fields:

```json
{
  "recommendation": "revise",
  "agreement": true,
  "reviewer_decisions": {
    "gpt": "revise",
    "claude": "revise"
  },
  "evidence_gaps": [],
  "risk_notes": [],
  "option_assessment": []
}
```

Raw Council outputs remain evidence. The operator should receive a synthesized
brief, not raw role transcripts.

### `JudgmentRoute`

Evaluator-routing object. It decides who or what should evaluate the issue
before any delivery or execution route is selected.

Recommended fields:

```json
{
  "schema": "judgment_route.v1",
  "evaluator": "council",
  "reason": "The issue changes the next episode direction and has tradeoff risk.",
  "policy_basis": [
    "materiality=high",
    "competing options need comparison"
  ],
  "evidence_refs": [],
  "selected_by": "decision_router",
  "selected_at": "2026-06-01T00:00:00Z"
}
```

Evaluator targets:

| Evaluator | Meaning |
| --- | --- |
| `council` | Use multiple perspectives for tradeoffs, disagreement, or recommendation. |
| `single_harness` | Use one capable harness-backed agent for narrow analysis or summarization. |
| `deterministic_gate` | Use tests, schema validation, policy checks, or state rules that can decide directly. |
| `user` | Ask the operator because the remaining choice is authority, preference, or risk tolerance. |

`JudgmentRoute` should not authorize mutation. It records the evaluation path
and rationale. Its output may then feed `DecisionRoute`, `approval_brief.v1`,
`ExecutionHandoff`, closeout review, or a receipt.

### `DecisionRoute`

Delivery-routing object. It decides where the evaluated decision should go next:
back to an agent, to the user, to the approval ledger, or to closeout. It should
not be used as the only record of who evaluated the issue.

Recommended fields:

```json
{
  "materiality": "high",
  "target": "user",
  "reason": "Changes the next episode direction and claim status.",
  "policy_basis": [],
  "default_if_no_reply": "defer",
  "expires_at": "2026-06-01T00:30:00Z"
}
```

Targets:

| Target | Meaning |
| --- | --- |
| `agent` | Return guidance to the agent without user escalation. |
| `user` | Create an operator-facing decision brief. |
| `approval_ledger` | Create or link a mutation approval. |
| `closeout` | Defer until closeout review rather than interrupting runtime. |

Implementation note: the current schema keeps `JudgmentRoute` and
`DecisionRoute` as separate objects. `JudgmentRoute` records evaluator selection;
`DecisionRoute` records delivery or execution routing.

### `DecisionBrief`

User-facing projection. For v1 this should be represented by the existing
`approval_brief.v1` schema.

Rules:

- include recommendation, short summary, direct question, scope, and choice
  impact;
- omit raw paths, request ids, secrets, artifact filenames, and raw JSON dumps;
- keep dense material in the detail card;
- make non-authorized actions explicit;
- preserve unknown fields internally but do not depend on rendering them.

### `ExecutionHandoff`

Agent-facing object created only after `approved`, `revised`, or
policy-resolved `auto_resolved` states.

Recommended fields:

```json
{
  "handoff_id": "handoff-uuid",
  "decision_id": "decision-id",
  "target": "agent",
  "approved_direction": "revise",
  "approved_scope": "Next episode direction only.",
  "instructions": [
    "Revise the next episode according to the operator note.",
    "Do not change provider, cleanup files, or promote wiki entries."
  ],
  "constraints": [],
  "verification_required": [],
  "non_authorized_actions": [
    "provider retargeting",
    "cleanup",
    "wiki promotion"
  ]
}
```

An execution handoff is not an approval for runtime mutation unless it links to
a valid `PendingActionApproval` or approved `ExecutionBrief`.

### `DecisionReceipt`

Audit object that closes the decision loop.

Recommended fields:

```json
{
  "receipt_id": "receipt-uuid",
  "decision_id": "decision-id",
  "resolved_by": "operator",
  "resolved_at": "2026-06-01T00:10:00Z",
  "final_decision": "revise",
  "applied_handoff_id": "handoff-id",
  "authorization_summary": "Approved next episode direction only.",
  "evidence_summary": [],
  "result_status": "applied",
  "remaining_review": []
}
```

Receipts help closeout distinguish:

- a process that completed;
- a decision that was approved;
- a result that is accepted;
- knowledge that is eligible for promotion.

These are separate states.

## Materiality Policy

Escalate to the user when a decision affects:

- task scope expansion or reduction;
- destructive or hard-to-reverse mutation;
- provider or model retargeting;
- external data movement;
- cost, latency, or token-budget increase beyond the approved envelope;
- research interpretation, reportability, or claim strength;
- wiki canon promotion;
- cleanup, deletion, archive, or retention;
- runtime policy changes that alter future autonomy.

Resolve internally when:

- an existing policy already covers the choice;
- the choice only affects implementation order;
- the verification command is inside the approved task contract;
- retry or continue stays inside approved scope;
- Council guidance does not widen mutation scope or trust boundary.

When in doubt, choose `user_pending` for material trust-boundary changes and
`closeout` for non-urgent review decisions.

## Relationship To Existing Surfaces

### `approval_brief.v1`

`approval_brief.v1` remains the compact operator projection. The Decision
Pipeline should generate or reference it as `DecisionRecord.approval_brief`.

Do not make `approval_brief.v1` canonical. It is optimized for Telegram and
other compact surfaces.

### `PendingActionApproval`

`PendingActionApproval` remains the authority for runtime or canonical mutation
approval. A `DecisionRecord` may link to an approval, but a decision alone does
not authorize mutation.

Examples:

- provider fallback still uses `dispatch.provider_fallback`;
- runtime dispatch still uses `dispatch.runtime`;
- cleanup, file movement, and wiki promotion need their own approval paths.

### Telegram Relay

Telegram receives only the decision brief projection and records the operator
response. It must not rewrite the decision ledger directly unless invoked
through a Forager command or future narrow write API.

Accepted relay results are ingested through:

```bash
forager offdesk decision ingest-telegram \
  --request <operator-decision-request.json> \
  --result <telegram-decision-result.json> \
  --json
```

The command reads `decision_record.v1` from the request, appends the seed record
to the active profile ledger when missing, and appends a resolved
`ExecutionHandoff` when the Telegram result is `accepted`. If the caller has
already consumed the handoff, it may also pass `--receipt-result-status
<status>` and `--receipt-evidence <line>` to append a `DecisionReceipt`.
Producer scripts that already know the concrete profile root may pass
`--profile-dir <profile-dir>` instead of relying on the active CLI profile.

### Council Scripts

Current workload-specific Council scripts can become producers:

```text
council record
  -> DecisionRequest + CouncilReview
  -> JudgmentRoute
  -> DecisionRoute
  -> approval_brief.v1 projection
```

A workload-specific `build_operator_decision_request` shape can be a useful
prototype, but it should be normalized into `DecisionRecord` rather than
becoming the generic contract.

Current producer behavior:

- `build_operator_decision_request` includes a `decision_record.v1` parent
  record and an `approval_brief.v1` projection.
- `build_ondesk_handoff_request` also includes a `decision_record.v1` parent
  record for the morning handoff entry decision.
- Python producers share `scripts/offdesk_decision_records.py` for stable ids,
  judgment routes, approval-brief projection, trace refs, and run-local ledger
  mirroring.
- The Telegram relay continues to render from `approval_brief.v1`.
- Telegram detail views also read `DecisionRecord.judgment_route` so the compact
  brief can explain why Council, a deterministic gate, a single harness, or the
  user is evaluating the decision.
- The workload mirrors the parent record to the episode operator-decision
  directory and appends it to run-local `offdesk_decisions.jsonl`.
- The run-local ledger is evidence for closeout and later ingestion; it does
  not make Telegram canonical and does not authorize mutation.

### Closeout

Closeout should read decision records and receipts to explain:

- which choices were made during runtime;
- which choices remain pending;
- which approvals authorized mutation;
- which results still need review before acceptance.

The first closeout integration reads `offdesk_decisions.jsonl` from the active
profile and matched run artifact directories, adds the records to
`closeout_plan.json`, and projects unresolved or invalid records into
`open_decisions`.

## CLI Surface

Read-only inspection:

```bash
forager offdesk decisions --json
forager offdesk decision show <decision-id> --json
```

Append-only resolution and closeout:

```bash
forager offdesk decision resolve <decision-id> --decision <choice> --note <text> --json
forager offdesk decision receipt <decision-id> --result-status <status> --json
forager offdesk decision ingest-telegram --request <request.json> --result <result.json> --json
```

The mutation surface is intentionally narrow. It appends a new ledger row for
the resolved state, then appends a receipt row when the handoff is applied or
explicitly closed. It does not edit earlier rows in place and does not grant
provider fallback, runtime mutation, cleanup, or wiki promotion authority.
Producers should write through a small library API so schema and validation are
shared.

## Validation Rules

Decision records should reject:

- missing `decision_id`, `project_key`, `request_id`, or `task_id`;
- `user_pending` without an `approval_brief`;
- `handoff_ready` without an `execution_handoff`;
- `applied` without a handoff reference;
- `receipted` without a `decision_receipt`;
- user-facing brief fields that contain raw paths, request ids, artifact
  filenames, secret-like values, or raw JSON key dumps.

Validation should warn, not fail, when:

- internal trace references are missing for low-materiality decisions;
- Council reviewer roles differ from the recommended default;
- a workload-specific producer omits optional context that is not needed for the
  operator decision.

## Implementation Slices

### Slice 1: Documentation And Schema Tests

- Add Rust `DecisionRecord` data structures behind a new module.
- Add serialization fixtures for low, high, and provider-fallback decisions.
- Keep all commands read-only.

### Slice 2: Read-Only CLI

- Load profile decision records.
- Show current and historical decisions.
- Surface pending decisions in `status --json` and home TUI summaries without
  changing approval behavior.

### Slice 3: Council Producer Adapter

- Wrap the existing Council operator decision request in `DecisionRecord`.
- Keep Telegram relay input as `approval_brief.v1`.
- Preserve current tests that verify Telegram cards do not leak raw ids or
  paths.
- Council and Ondesk handoff producers now use the shared Python producer
  helper; future producers should reuse that helper.

### Slice 4: Handoff And Receipt

- Generate `ExecutionHandoff` only after user approval or policy resolution.
- Record `DecisionReceipt` when the handoff is applied or explicitly closed.
- Include open decisions and receipts in `offdesk closeout`.
- The first CLI path is implemented through append-only `decision resolve` and
  `decision receipt`.
- Telegram relay artifacts can be ingested into the profile ledger through
  append-only `decision ingest-telegram`.
- Council and Ondesk handoff producers now share the same Python producer
  helper; remaining work is wiring future producer scripts to that helper
  rather than copying ad hoc record dictionaries.

## Non-Goals

The Decision Pipeline does not:

- execute agent work;
- replace the approval ledger;
- make Telegram canonical;
- approve provider fallback, runtime dispatch, cleanup, or wiki promotion by
  itself;
- expose raw Council transcripts as the primary operator surface;
- infer accepted truth from a completed process.

## Open Design Questions

- Should profile-level decisions live in one JSONL file or be sharded by
  project key once volume grows?
- Should `DecisionReceipt` be appended to the same ledger or mirrored into
  `action_audit.jsonl` for approval-adjacent decisions?
- Which decisions should be deferred to closeout instead of interrupting a
  running Offdesk workload?
- How much of the initial implementation should be generic before adapting
  workload-specific Council paths?
