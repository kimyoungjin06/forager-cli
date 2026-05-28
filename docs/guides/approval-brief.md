# Approval Briefs

`approval_brief.v1` is the shared contract for operator approval prompts. It is
designed for Telegram and other compact operator surfaces where the operator
needs a decision prompt, not a log dump.

The approval card contract separates two outputs:

- user-facing prompt: recommendation, reason, question, scope, and choice impact;
- internal trace: request id, raw artifacts, file paths, model output, and logs.

Do not put raw paths, secret values, request ids, heartbeat paths, or unfiltered
JSON payloads into the user-facing message. Keep them in the relay state,
result JSON, invocation JSON, or producer artifacts.

## Shape

Minimum recommended request:

```json
{
  "message_type": "approval_request",
  "decision_request_id": "stable-internal-id",
  "approval_brief": {
    "schema": "approval_brief.v1",
    "recommendation": "revise",
    "subject": "reportability status check",
    "summary_lines": [
      "The current result cannot be promoted to a reportable claim.",
      "Reason: primary_objective_gate did not pass.",
      "Council: revise recommended, reviewers agree."
    ],
    "scope": "Only approves the next episode direction.",
    "question": "How should the run proceed?"
  }
}
```

## Fields

| Field | Required | User-facing purpose |
| --- | --- | --- |
| `schema` | yes | Version marker. Use `approval_brief.v1`. |
| `recommendation` | yes | Recommended decision, usually `continue`, `revise`, `block`, or `stop`. |
| `subject` | yes | Short object of the approval, such as `provider fallback` or `reportability status check`. |
| `summary_lines` | yes | Primary card body. Keep this to 1-3 short lines. |
| `scope` | yes | What this approval authorizes and, just as importantly, what it does not authorize. |
| `question` | yes | Direct operator question shown above the buttons. |
| `options` | no | Domain-specific button choices, such as approve/deny/defer. |
| `why_recommendation` | no | Detailed explanation for the recommended path. |
| `evidence` | no | Decision-relevant evidence for the detail card. |
| `failure` | no | Structured failure summary when the approval follows a gate failure. |
| `council` | no | Council recommendation, reviewer agreement, evidence gaps, and reviewer decisions. |
| `decision_impacts` | no | What happens if the operator selects each decision. |
| `reply_examples` | no | Natural-language examples for decisions that require explanation. |
| `context` | no | Safe small context such as iteration, case, claim status, or baseline status. |
| `source` | no | Producer name, used for debugging and contract checks. |

The relay preserves unknown fields internally, but unknown fields should not be
assumed to render.

Explicit producer-provided briefs are validated before the Telegram card is
sent. Validation failures stop the relay when required fields are missing, the
recommendation does not map to a visible action, the scope lacks a clear
non-authorized boundary, or user-facing fields include raw paths, request ids,
secret-like strings, trace keys, artifact filenames, or raw JSON dumps. Briefs
inferred from legacy `summary`, `operator_brief`, or artifacts keep running but
surface validation gaps as warnings in the relay result.

## Decisions

The default relay supports:

| Decision | Meaning |
| --- | --- |
| `continue` | Proceed despite the current warning. |
| `revise` | Continue with a natural-language correction or narrowed direction. |
| `block` | Stop and require a condition or extra review before resuming. |
| `stop` | End the run and move to closeout or a separate review path. |

Direction-choice prompts may use custom option ids. If a custom option needs
free-form input, include a natural input prompt in the option.
Mutation approvals may use domain actions such as `approve`, `deny`, or
`defer`, but they must still state the exact scope and non-authorized actions.
When `options` is present in the approval brief, Telegram renders those choices
instead of the default continue/revise/block/stop buttons.

Morning Ondesk handoff prompts use `message_type: "ondesk_handoff"` and may use
`schema: "ondesk_handoff_brief.v1"`. They are entry prompts, not mutation
approvals. The default choices are `start_ondesk_review`, `keep_pending`, and
`defer_ondesk`; `start_ondesk_review` should lead the operator into WebUI or an
equivalent review surface, while destructive cleanup, wiki promotion, provider
changes, and file movement remain separately approved.

## Decision Card Quality Rubric

Golden tests should not blindly preserve the current wording. They should
preserve the operator decision quality of the card. A good card answers these
questions without requiring the operator to open raw logs:

- What happened?
- Why does this need a human decision now?
- What does Council or the system recommend?
- What will each visible choice change?
- What does this choice not authorize?
- Where should deeper review continue?

The compact Telegram card uses an information budget, not a fixed line count.
The budget is type-specific:

| Message type | Compact-card budget |
| --- | --- |
| `approval_request` | Short recommendation, 1-3 summary lines, direct question, explicit scope. |
| `council_decision` | Recommendation, failure/reason, Council agreement, direct question, explicit scope. |
| `direction_choice` | Decision object, visible option list, direct question, explicit scope. |
| `ondesk_handoff` | Handoff time, closeout summary, remaining decisions, WebUI entry question, explicit scope. |

The detail card should hold the dense material: recommendation rationale,
failure summary, evidence, Council details, choice impacts, and reply examples.
If the detail card has insufficient structured data, it should say that
explicitly instead of dumping request JSON.

All user-facing card surfaces must reject raw paths, request ids, secret-like
values, raw JSON key dumps, and trace-only state. Those values belong in the
relay state, result JSON, or producer artifacts.

Free-form Telegram replies are a convenience layer, not the authority model.
The relay should accept unscoped natural language only when there is exactly one
active decision request for the operator. If multiple requests are active, a
free-form reply must be scoped by replying to the decision card, using a button,
or including the request id. Otherwise the result is `ambiguous_input` and no
decision is applied.

## Telegram Rendering

The compact card renders:

```text
<recommendation> 권고: <subject>

<summary_lines as a short quote>

질문: <question>
범위: <scope>
```

The detail card renders:

- why this recommendation;
- failure summary when present;
- evidence, using expandable blockquote when useful;
- Council judgement when present;
- decision impacts;
- reply examples.

If Telegram rejects expandable blockquotes, the relay falls back to a normal
blockquote. If Telegram rejects HTML parsing again, it sends a plain-text
version.

## Producer Checklist

A producer should create `approval_brief.v1` directly when it is asking for a
human decision. The relay still supports older requests by deriving an approval
brief from `operator_brief`, artifacts, or summary fields, but new producers
should not depend on that inference.

Before emitting an approval brief, check:

- The primary card tells the operator what decision is requested.
- The recommendation is explicit and maps to a button.
- The reason is short enough to read on a phone.
- The scope states what is not authorized.
- The detail card explains what each choice changes.
- Raw paths, request ids, secrets, and raw JSON keys are absent from rendered
  user messages.
- Internal trace fields remain available in state/result artifacts.

## Examples

### Council Revise

```json
{
  "schema": "approval_brief.v1",
  "source": "offdesk_twinpaper_autonomy_workload",
  "recommendation": "revise",
  "subject": "reportability status check",
  "summary_lines": [
    "The current result cannot be promoted to a reportable claim.",
    "Reason: primary_objective_gate did not pass.",
    "Council: revise recommended, reviewers agree."
  ],
  "why_recommendation": [
    "The run executed, but the promotion gate failed.",
    "Continuing unchanged may repeat the non-reportable state."
  ],
  "evidence": [
    "no-option primary validated gate failed",
    "no-option restart validated rate gate failed"
  ],
  "decision_impacts": {
    "continue": "Proceed despite the warning.",
    "revise": "Use the operator's correction as the next episode direction.",
    "block": "Pause until a restart condition is supplied.",
    "stop": "End this run and move to closeout."
  },
  "scope": "Only approves the next episode direction.",
  "question": "How should the run proceed?"
}
```

### Provider Fallback

```json
{
  "schema": "approval_brief.v1",
  "source": "offdesk.provider_fallback",
  "recommendation": "approve",
  "subject": "provider fallback",
  "summary_lines": [
    "Provider/model retargeting is waiting for operator approval.",
    "Reason: provider capacity cooldown active.",
    "Candidate: openai model gpt-4.1-mini."
  ],
  "why_recommendation": [
    "openai model gpt-4.1 is currently blocked by provider capacity state.",
    "openai model gpt-4.1-mini is the first currently recommended fallback candidate.",
    "The approval is scoped to provider/model retargeting only."
  ],
  "decision_impacts": {
    "approve": "Retarget only this request; runtime dispatch still needs its own approval.",
    "deny": "Keep the current provider/model queued until capacity recovers or manual retargeting is chosen.",
    "defer": "Leave the approval pending while reviewing cost, quality, or capacity evidence."
  },
  "options": [
    {
      "id": "approve",
      "label": "Approve fallback",
      "description": "Retarget only this request; runtime dispatch still needs its own approval."
    },
    {
      "id": "deny",
      "label": "Deny fallback",
      "description": "Keep the current provider/model queued until capacity recovers or manual retargeting is chosen.",
      "natural_input_prompt": "Explain why this fallback should not be applied."
    },
    {
      "id": "defer",
      "label": "Need more detail",
      "description": "Leave the approval pending while reviewing cost, quality, or capacity evidence.",
      "natural_input_prompt": "State what provider, cost, or quality evidence you need first."
    }
  ],
  "scope": "Approves provider/model retargeting for this request only; does not approve runtime dispatch, command/workdir changes, cleanup, or wiki promotion.",
  "question": "Approve this provider fallback retargeting?"
}
```

### Wiki Promotion

```json
{
  "schema": "approval_brief.v1",
  "recommendation": "block",
  "subject": "wiki promotion",
  "summary_lines": [
    "A candidate entry exists but has not been reviewed.",
    "Reason: evidence refs and scope need operator confirmation."
  ],
  "decision_impacts": {
    "block": "Keep the candidate out of canonical wiki.",
    "continue": "Continue the run without promoting the candidate.",
    "stop": "Move to manual wiki review."
  },
  "scope": "Does not promote canonical wiki knowledge.",
  "question": "How should this wiki candidate be handled?"
}
```

### Cleanup Or Mutation

Cleanup, deletion, file movement, service restart, package installation,
provider retargeting, and wiki promotion should use a separate approval action
from runtime continuation. An approval brief may ask the operator what to do
next, but it must not make a continuation reply authorize a mutation.
