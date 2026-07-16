# Telegram Adapter Decomposition Plan - 2026-06-26

This plan decomposes `scripts/offdesk_remote_operator_telegram.py` without
changing the operator-facing behavior. It follows the freeze rule in
`docs/refactor-baseline-20260626.md`: no new mutation-capable remote actions
until the first decomposition cycle is complete.

## Current Problem

The Telegram adapter currently mixes these responsibilities in one file:

- CLI argument parsing and environment resolution
- Telegram Bot API transport
- listener health and loop status reporting
- local LLM routing and prompt construction
- plain chat, slash command, button, and remote-plan session routing
- mobile card rendering and choice-surface contracts
- feedback persistence and decision inbox ingest
- project discovery and candidate ranking
- remote plan session state transitions
- receipt generation for plan draft, registration, gate, enqueue, runtime,
  closeout, and verdict stages

The recent chat-first routing fix showed why this is risky: the user-facing
rule "plain text defaults to chat" had to be defended against an active plan
session state machine in the same file.

## Non-Goals

- Do not redesign the workflow stages in this pass.
- Do not add new remote action stages.
- Do not change Telegram message text except when necessary to preserve the
  existing mobile-card contract.
- Do not migrate persistent state schemas in this pass.
- Do not split into a package layout that requires deployment changes before
  tests prove parity.

## Proposed Module Layout

Keep the existing script as the executable entrypoint, then extract modules
under `scripts/telegram_operator/`.

```text
scripts/
  offdesk_remote_operator_telegram.py       # thin entrypoint and wiring
  telegram_operator/
    __init__.py
    config.py                              # args, env, static constants
    transport.py                           # Telegram API, getUpdates, sendMessage
    health.py                              # listener health and readiness
    routing.py                             # parse_remote_command and session routing predicates
    rendering.py                           # message rendering and card contracts
    persistence.py                         # state, feedback JSONL, decision ingest
    agent.py                               # local LLM prompt/call/normalization
    project_candidates.py                  # workspace discovery and candidate ranking
    plan_workflow.py                       # session stages and transition functions
    receipts.py                            # plan receipt writers and public redaction helpers
```

The first extraction should favor importable pure functions. Keep side-effect
heavy code in place until the surrounding data types are stable enough to move
without broad rewrites.

## Slice 1: Routing Extraction

Move first:

- `BUTTON_COMMAND_ALIASES`
- `normalize_command_name`
- `parse_remote_command`
- `unsupported_command`
- `is_core_or_slash_command_text`
- `remote_plan_session_command_payload`
- `remote_plan_action_text`
- `remote_plan_project_selection_text`
- `remote_plan_session_should_handle_text`
- small text predicate helpers such as `remote_plan_defer_text`,
  `remote_plan_rescan_text`, `remote_plan_reselect_text`, and action label
  matchers

Target file:

- `scripts/telegram_operator/routing.py`

Expected result:

- `offdesk_remote_operator_telegram.py` imports routing helpers.
- The chat-first rules remain unchanged:
  - plain text maps to `chat`
  - `/feedback` and `/note` map to explicit feedback
  - `/plan` maps to explicit plan request
  - active plan sessions only intercept button labels, direct candidate
    selections, explicit `/select ...`, valid paths, and known stage actions

Verification:

```bash
python3 -m py_compile scripts/offdesk_remote_operator_telegram.py
cargo test --test remote_operator_telegram remote_operator_telegram_plain_text_defaults_to_chat
cargo test --test remote_operator_telegram remote_operator_telegram_replay_plan_session_plain_text_stays_chat
cargo test --test remote_operator_telegram
```

Status:

- Started on 2026-06-26.
- Pure command routing has been extracted to
  `scripts/telegram_operator/routing.py`.
- The main script now imports button aliases, core button labels,
  `parse_remote_command`, `is_core_or_slash_command_text`, and
  `remote_plan_session_command_payload` from that module.
- Session workflow predicates that depend on workspace discovery and path
  resolution remain in `scripts/offdesk_remote_operator_telegram.py` for the
  next slice.

## Slice 2: Rendering Extraction

Move next:

- `title_with_profile`
- `render_status_message`
- `render_pending_message`
- `render_plans_message`
- `render_show_message`
- `render_chat_message`
- `render_feedback_message`
- remote-plan message renderers
- `mobile_card_contract`
- `choice_keyboard`
- `choice_surface_contract`

Target file:

- `scripts/telegram_operator/rendering.py`

Expected result:

- Rendering can be tested without Telegram API, filesystem state, or local LLM.
- Mobile-card constraints stay centralized.
- Workflow logic no longer has to know HTML card details beyond choosing a
  renderer.

Verification:

```bash
python3 -m py_compile scripts/offdesk_remote_operator_telegram.py
cargo test --test remote_operator_telegram remote_operator_telegram_pending_fixture_is_mobile_scannable
cargo test --test remote_operator_telegram remote_operator_telegram_replay_plan_request_creates_project_selection_session
cargo test --test remote_operator_telegram
```

Status:

- Started on 2026-06-26.
- Query, chat, feedback, mobile-card, and choice-surface rendering helpers have
  been extracted to `scripts/telegram_operator/rendering.py`.
- Remote plan stage-specific renderers remain in
  `scripts/offdesk_remote_operator_telegram.py` until the plan workflow slice.

## Slice 3: Persistence and Transport Extraction

Move after routing and rendering are stable:

- env parsing and Telegram config resolution
- `telegram_api`
- `get_updates`
- `send_message`
- state load/save helpers
- feedback JSONL append and decision ingest
- listener health and loop summary helpers

Target files:

- `scripts/telegram_operator/config.py`
- `scripts/telegram_operator/transport.py`
- `scripts/telegram_operator/persistence.py`
- `scripts/telegram_operator/health.py`

Expected result:

- `run_once` becomes mostly orchestration:
  1. get update
  2. authorize
  3. route
  4. render
  5. persist if needed
  6. send response
- Transport failures remain handled as current `poll_error` or `send_failed`
  result objects.

Verification:

```bash
python3 -m py_compile scripts/offdesk_remote_operator_telegram.py
cargo test --test remote_operator_telegram remote_operator_telegram_health_reports_fresh_listener_status
cargo test --test remote_operator_telegram remote_operator_telegram_replay_feedback_records_decision_inbox_item
cargo test --test remote_operator_telegram
```

Status:

- Started on 2026-06-26.
- Shared adapter errors, JSON helpers, env parsing, and `sha256_short` have been
  extracted to `scripts/telegram_operator/common.py`.
- Telegram env/config resolution has been extracted to
  `scripts/telegram_operator/config.py`.
- Bot API transport, update replay loading, live `getUpdates`, and `sendMessage`
  have been extracted to `scripts/telegram_operator/transport.py`.
- State load/save and last interaction context persistence have been extracted
  to `scripts/telegram_operator/persistence.py`.
- Feedback JSONL append still uses the shared common helper from the main
  script. Moving the whole feedback ingest path can be a later small slice if
  needed.

## Slice 4: Plan Workflow Extraction

Move last:

- remote plan session public redaction
- active stage detection
- project selection and path resolution
- stage action predicates that remain after Slice 1
- `handle_remote_plan_session_input`
- plan receipt creation and stage mutation helpers, in smaller groups

Target files:

- `scripts/telegram_operator/plan_workflow.py`
- `scripts/telegram_operator/project_candidates.py`
- `scripts/telegram_operator/receipts.py`

Expected result:

- The plan session engine can be tested as a state transition module.
- Telegram transport and message rendering become adapters around the workflow.
- Adding a future stage requires a transition entry, a receipt contract, and a
  renderer instead of editing unrelated routing and transport code.

Verification:

```bash
python3 -m py_compile scripts/offdesk_remote_operator_telegram.py
cargo test --test remote_operator_telegram
```

Status:

- Started on 2026-06-26.
- Workspace project discovery, project marker detection, Git readiness probing,
  project candidate ranking, candidate label helpers, and public candidate
  redaction have been extracted to
  `scripts/telegram_operator/project_candidates.py`.
- Remote plan session state transitions, stage-specific receipt creation, and
  public redaction for non-candidate plan artifacts remain in
  `scripts/offdesk_remote_operator_telegram.py`.

## Completion Criteria

The first decomposition cycle is complete when:

- `scripts/offdesk_remote_operator_telegram.py` is primarily entrypoint and
  orchestration code.
- Routing, rendering, transport, persistence, agent calls, project discovery,
  and plan workflow logic are in separate modules.
- `cargo test --test remote_operator_telegram` passes.
- `docs/refactor-baseline-20260626.md` is updated or superseded with reduced
  size metrics.

## Additional Extracted Modules

### Guarded Remote Execution

Status:

- Guarded remote decision, accepted-truth recovery, and opt-in runtime
  dispatch surfaces have been added in
  `scripts/telegram_operator/dispatch.py`. They reuse the existing
  receipt-gated ondesk CLI executors and share one confirmation-token model.

### Listener Health

Status:

- Listener health, action readiness, and agent-runtime issue reporting have
  been extracted to `scripts/telegram_operator/health.py`. The main script now
  imports `listener_health`, `action_readiness`, and
  `readiness_from_agent_intent`. Run-loop result plumbing (`result_base`,
  `loop_summary_base`, backoff) stays in the main script because it is coupled
  to the poller.

### Agent Calls

Status:

- Started on 2026-06-26.
- Local LLM provider resolution, Telegram chat prompt construction, feedback
  intent classification, deterministic feedback kind fallback, and agent runtime
  health resolution have been extracted to `scripts/telegram_operator/agent.py`.
- The main Telegram script now imports `chat_with_agent`,
  `classify_feedback_with_agent`, `classify_feedback_kind`, and
  `DEFAULT_AGENT_CONFIG_FILE` from the agent module.
