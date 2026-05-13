# Hermes Pattern Review

This document tracks the small Hermes Agent patterns that are worth comparing
against Forager's offdesk runtime. Hermes is a reference implementation, not a
replacement control plane. Forager keeps ownership of queue state, task state,
recovery artifacts, and action audit records.

The first pass compared against the Hermes checkout recorded in the companion
review note, `aoe_orch_control/docs/HERMES_AGENT_BENCHMARK_20260512.md`.

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
| 3 | Background process recovery | background run tickets and sidecars | Can stale local/tmux/background runners be reconciled with better tail and heartbeat evidence? | Planned |
| 4 | Provider profile and error classifier | provider routing and capacity memory | Can provider errors become structured retry/compress/fallback reasons before scheduler policy? | Planned |
| 5 | Checkpoint and rollback | pre-mutation evidence artifacts | Can canonical mutations require rollback evidence without copying Hermes shadow git wholesale? | Planned |
| 6 | Tool registry | Task Team capability registry | Can capabilities be declared with risk, scope, backend, approval, and offdesk eligibility? | Partially present |
| 7 | Redaction and context fencing | operator-facing summaries and debug bundles | Can runner-only context and secrets be stripped at every upload/share boundary? | Partially present |

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
