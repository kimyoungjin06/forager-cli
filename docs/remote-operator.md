# Remote Operator

The Remote Operator layer lets an operator supervise Offdesk work from a
remote surface such as Telegram, Slack, a mobile app, or a small web console.
It is not a remote shell. It is a transport-agnostic control surface for
planning, approval, observation, interruption, and closeout.

The first likely transport is Telegram, but Telegram must remain an adapter.
The durable contract belongs to Forager's Offdesk registry, approval gate, and
runtime evidence model.

## Purpose

Remote operation should make long-running work easier to entrust without
weakening Forager's safety boundaries.

It should support:

- selecting which project should receive autonomous time;
- preparing read-only plans and council reviews;
- approving plan review separately from launch approval;
- observing progress, heartbeat, blockers, and final closeout;
- interrupting only for decisions that materially change risk or scope;
- preserving append-only receipts for every remote action.

The operator should be able to start a night or lunch-break planning cycle from
a phone, make a small number of high-leverage decisions, and return later to a
reviewable closeout packet instead of raw chat logs.

## Non-Goals

Remote Operator must not provide:

- arbitrary shell execution;
- direct `git push`, deletion, file movement, or package installation commands;
- direct task launch that bypasses `offdesk gate`;
- direct provider or model retargeting without the existing approval boundary;
- access to secrets, environment variables, raw token values, or sensitive
  absolute paths;
- a transport-specific state model that only Telegram can understand.

Any future transport must use the same remote command envelope, approval
receipt, staleness checks, and run ownership model.

## Architecture

```text
Remote transport
  -> Remote Operator adapter
  -> Remote command envelope
  -> Project inventory and Plan Mode
  -> Offdesk plan registry
  -> Operator review receipt
  -> Launch-preparation packet
  -> Execution brief candidate
  -> Offdesk gate approval
  -> Background runner
  -> Monitor, decision interrupts, and closeout
```

Transport adapters receive messages and callbacks. They do not decide whether a
mutation is allowed. The Remote Operator core normalizes input, validates
identity and staleness, and then calls existing Forager surfaces.

The durable path is:

```text
forager offdesk plan
  -> forager offdesk plan-review
  -> forager offdesk plan-launch-prep
  -> forager offdesk gate
  -> forager offdesk ok|cancel
  -> forager offdesk launch|enqueue|tick
```

Plan approval is not launch approval. Launch-preparation packets are not
execution permission. Runtime mutation remains behind `offdesk gate`.

## State Machine

Remote Operator tracks user-visible workflow state while Offdesk keeps durable
task and approval state.

```text
idle
  -> portfolio_scan_requested
  -> project_candidates_ready
  -> plan_draft_ready
  -> council_review_ready
  -> operator_review_pending
  -> plan_approved
  -> launch_prep_ready
  -> gate_pending
  -> running
  -> needs_decision
  -> running
  -> closeout_ready
  -> completed
```

Terminal or exceptional states:

```text
rejected
revision_required
stale_action_rejected
approval_expired
blocked
aborted
transport_unavailable
```

State transitions must be idempotent. Replaying a Telegram callback or mobile
button must not duplicate a review, consume the wrong approval, or launch work
twice.

## Remote Command Envelope

Every remote message or button callback is normalized before it can affect
Forager state.

```json
{
  "schema": "remote_command_envelope.v1",
  "source_transport": "telegram",
  "source_surface": "remote_operator.telegram",
  "operator_id": "telegram:<user-id>",
  "chat_id": "telegram:<chat-id>",
  "intent": "approve_plan",
  "target_type": "offdesk_plan",
  "target_id": "plan_...",
  "observed_hash": "sha256:...",
  "observed_version": "offdesk_plan_summary.v1",
  "message_id": "telegram:<message-id>",
  "callback_id": "telegram:<callback-query-id>",
  "nonce": "uuid",
  "created_at": "2026-06-06T00:00:00Z",
  "expires_at": "2026-06-06T00:30:00Z"
}
```

The envelope is operator-safe. It must not store bot tokens, environment
values, raw prompts containing secrets, or full command output.

The `observed_hash` is mandatory for approvals. It binds the approval to the
exact plan, launch-prep packet, execution brief, or pending approval summary
that the operator saw.

## Project Selection Contract

When multiple projects are available, Plan Mode starts with a comparable
project inventory. Each candidate must use the same fields so the recommendation
is inspectable.

```json
{
  "schema": "remote_project_candidate.v1",
  "project_key": "98-harness",
  "workspace_path": "/operator-safe/path",
  "current_branch": "offdesk-generic-planning-harness",
  "head_ref": "sha",
  "dirty_tree": false,
  "remote_sync": "up_to_date|ahead|behind|diverged|unknown",
  "recent_failures": [],
  "available_tests": ["cargo test", "cargo clippy"],
  "estimated_runtime_minutes": 180,
  "risk_level": "low|medium|high",
  "autonomy_fit": "low|medium|high",
  "requires_network": false,
  "requires_secrets": false,
  "requires_human_context": false,
  "recommended_next_action": "build execution brief bridge",
  "blocked_reason": null
}
```

The recommendation should explain why a project is selected and why plausible
alternatives were not selected. A project with a dirty tree, missing tests,
unknown branch state, or human-context dependency can still be recommended, but
the risk must be explicit.

## Plan Mode Portfolio Flow

A request such as `/plan tonight --until 09:00` should perform:

1. Read-only workspace discovery.
2. Project candidate inventory.
3. Candidate ranking by value, readiness, risk, and autonomy fit.
4. Plan generation for the selected project or top candidates.
5. Council-style review of the plan.
6. Registration through `forager offdesk plan`.
7. Remote summary with a stable observed hash.

The remote summary should be compact:

```text
Recommended plan

Project: 98.Harness
Goal: Build execution brief bridge from launch-prep to offdesk gate
Risk: medium
Autonomy fit: high
Deadline: 09:00
Requires launch approval: yes
Plan hash: sha256:...

Actions:
[Approve Plan] [Request Revision] [Reject]
```

The full plan stays in the registry. The transport only displays a bounded
summary and links or IDs needed for inspection.

## Approval Model

Remote operation has at least two human approval boundaries.

### Plan Review Approval

Plan approval means:

- the operator accepts the direction;
- `forager offdesk plan-review --decision approved` may be recorded;
- a launch-preparation packet may be built;
- work still must not be enqueued, launched, or dispatched.

### Launch Approval

Launch approval means:

- an execution brief candidate has been prepared;
- `forager offdesk gate` has created or evaluated a scoped approval;
- the operator approves the exact pending approval ID or capability transition;
- the normal Offdesk runtime path may continue.

The two approvals must not be collapsed into one Telegram button. A remote
operator can approve a plan while still refusing to launch it after seeing the
execution brief.

## Staleness Guards

Before accepting any remote approval, Forager must validate:

- the approval or review target still exists;
- the target is still pending or reviewable;
- the observed hash matches the current target summary;
- the source artifact hash has not changed;
- the remote command envelope has not expired;
- the nonce has not already been consumed;
- the project branch and head ref still match the displayed summary, unless the
  execution brief explicitly allows drift;
- the dirty-tree state has not changed in a way that invalidates the plan;
- the capability remains allowed under current policy;
- provider capacity, model availability, and auth state still satisfy the
  approval metadata when relevant.

If any check fails, the remote action is rejected as stale and an append-only
receipt records the reason. The operator receives a fresh summary rather than a
best-effort mutation.

## Autonomy Budget

Every autonomous run needs an explicit budget. Time alone is not enough.

```json
{
  "schema": "remote_autonomy_budget.v1",
  "deadline": "2026-06-06T09:00:00+09:00",
  "max_turns": 12,
  "max_model_calls": 30,
  "max_cost_usd": null,
  "max_commits": 3,
  "max_pushes": 1,
  "max_project_switches": 1,
  "max_retries_per_failure": 2,
  "decision_interrupt_policy": "conservative"
}
```

Budget exhaustion should stop or pause the run. It should not silently expand
scope. A budget update is a new remote decision with its own envelope and
receipt.

## Decision Interrupts

Remote Operator should interrupt only for decisions that change risk, scope, or
durable state.

Mandatory interrupts:

- project switch;
- scope expansion;
- destructive file operation;
- commit or push;
- provider or model retargeting;
- network or secret requirement not declared in the approved plan;
- test failure bypass;
- approval retargeting;
- budget exceeded;
- repeated blocker;
- source hash, branch, or dirty-tree mismatch;
- closeout verdict requiring human judgement.

No interrupt is required for:

- read-only inspection;
- formatting;
- focused test reruns;
- bounded documentation updates;
- non-destructive artifact creation;
- implementation choices inside the approved plan and budget;
- status polling or heartbeat updates.

This keeps remote operation from becoming one-message-at-a-time chat while
still preserving important decisions.

## Run Ownership

When a run starts, ownership is recorded separately from transport state.

```json
{
  "schema": "remote_run_ownership.v1",
  "run_id": "run_...",
  "project_key": "98-harness",
  "plan_id": "plan_...",
  "plan_review_id": "review_...",
  "launch_prep_id": "launch_prep_...",
  "approval_id": "approval_...",
  "operator_id": "telegram:<user-id>",
  "transport": "telegram",
  "started_at": "2026-06-06T00:00:00Z",
  "deadline": "2026-06-06T09:00:00+09:00",
  "abort_policy": "operator_or_safety_gate",
  "closeout_required": true
}
```

Only the owner, an explicitly configured emergency operator, or a local CLI
operator should be able to abort or revise a run. Observer accounts can read
status but cannot approve mutations.

## Telegram Transport Policy

Telegram should expose a small command surface:

```text
/status
/projects
/plan tonight
/plans
/show <plan_id>
/pending
/approve <approval_id>
/deny <approval_id>
/pause <run_id>
/abort <run_id>
/summary
```

Allowed button callbacks:

```text
approve_plan
request_revision
reject_plan
approve_launch
deny_launch
acknowledge_status
abort_run
request_closeout
```

Forbidden command classes:

```text
/run <shell>
/exec <shell>
/git push
/delete
/move
/install
/set-env
/retarget-provider
```

If an operator needs one of these actions, Remote Operator should create a
bounded approval or decision request rather than execute the text directly.

## Security And Audit

Remote Operator requires:

- configured allowlist of transport user IDs and chat IDs;
- one owner for mutation approvals during the initial release;
- read-only mode for observers;
- nonce-based idempotency for every callback;
- short TTL for approval callbacks;
- append-only receipts for accepted and rejected remote actions;
- secret redaction before persistence and before transport replies;
- rate limits for plan requests, status polling, and callback retries;
- local CLI recovery for every remote-visible state.

Remote transport outages must never leave work in an unknowable state. The CLI
must remain the source of truth for plans, approvals, tasks, and closeout.

## Monitoring And Closeout

Running work should emit:

- heartbeat status;
- current phase;
- latest meaningful progress;
- current blocker, if any;
- budget remaining;
- pending decisions;
- last verification result;
- final closeout artifact refs.

The morning summary should not be a raw transcript. It should include:

- what changed;
- files changed;
- commits and pushes;
- tests run and failures;
- open blockers;
- decisions needed;
- next safe action;
- whether the tree is dirty;
- whether closeout was accepted, needs revision, or is blocked.

The closeout packet remains the durable handoff surface. Telegram only displays
a compact summary and artifact references.

## Failure Recovery

Remote Operator must handle:

- duplicate callback delivery;
- stale button presses;
- revoked or expired approvals;
- transport outage during a run;
- background runner failure;
- local profile lock contention;
- project branch drift;
- dirty tree drift;
- plan source file deletion;
- model/provider cooldown;
- operator ownership handoff.

Recovery should prefer read-only status and fresh approval requests over
implicit mutation.

## Implementation Phases

### Phase 1: Read-Only Remote Surface

- Implement transport allowlist.
- Implement `/status`, `/pending`, `/plans`, and `/show`.
- Return operator-safe summaries only.
- No remote approval, launch, enqueue, or mutation.

Current CLI projection surface:

```bash
forager offdesk remote-operator status --json
forager offdesk remote-operator pending --json
forager offdesk remote-operator plans --json
forager offdesk remote-operator show <plan-id> --json
```

These commands emit `remote_operator_readonly_projection.v1` with
`read_only=true`, `mutation_authorized=false`, and
`approval_authorized=false`. They are intended for Telegram or another
transport adapter to render. The `pending` projection reads approval rows
without resolving or expiring them.

### Phase 2: Remote Envelope And Receipts

- Add `remote_command_envelope.v1`.
- Add nonce and TTL validation.
- Persist accepted and rejected remote action receipts.
- Verify local CLI can inspect all receipts.

### Phase 3: Plan Mode Bridge

- Add portfolio project inventory.
- Generate candidate ranking and plan artifacts.
- Register plans through `forager offdesk plan`.
- Display summaries with observed hashes.

### Phase 4: Plan Review Bridge

- Allow `approve_plan`, `request_revision`, and `reject_plan`.
- Map approvals to `forager offdesk plan-review`.
- Reject stale approvals by observed hash.
- Never enqueue or launch work in this phase.

### Phase 5: Launch-Prep And Gate Bridge

- Build launch-preparation packets after approved plan review.
- Generate execution brief candidates.
- Use `forager offdesk gate` for launch approval.
- Consume only matching pending approvals.

### Phase 6: Monitor And Closeout Bridge

- Stream bounded heartbeat and blocker summaries.
- Raise mandatory decision interrupts.
- Surface closeout artifacts and verdict state.
- Keep CLI as recovery path for all state.

## Acceptance Criteria

Remote Operator is ready for first practical use when:

- no remote message can execute arbitrary shell text;
- plan approval and launch approval are separate receipts;
- stale callbacks are rejected and recorded;
- project candidates are comparable across workspaces;
- every mutation-capable callback binds to an observed hash;
- every remote-visible state can be inspected through CLI;
- Telegram outage does not prevent local recovery;
- closeout produces a reviewable morning package;
- tests cover duplicate callbacks, expired approvals, hash mismatch, observer
  denial, and owner-only abort.
