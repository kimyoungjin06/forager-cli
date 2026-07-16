# Refactor Baseline - 2026-06-26

This document freezes the current complexity baseline before the staged
Offdesk and Telegram decomposition work. It is a measurement and scope-control
artifact, not a new feature spec.

## Refactor Rule

Until the first decomposition cycle is complete, do not add new
mutation-capable remote actions. Bug fixes, routing safety fixes, tests,
documentation, and pure extraction refactors are allowed.

The immediate goal is to preserve current behavior while reducing the size and
coupling of the largest stateful surfaces.

## Current Size Baseline

Measured from the current checkout on 2026-06-26.

| Surface | Lines |
| --- | ---: |
| `src/cli/offdesk.rs` | 18,247 |
| `src/offdesk/adaptive_wiki.rs` | 9,326 |
| `src/cli/ondesk.rs` | 6,349 |
| `scripts/offdesk_remote_operator_telegram.py` | 8,588 |
| `src/cli/*.rs` total | 33,922 |
| `src/**/*.rs` total | 90,833 |
| `tests/**/*.rs` total | 27,240 |

The largest immediate maintenance risk is not a lack of tests or basic code
hygiene. It is the amount of hand-expanded workflow state and receipt logic in
large command or adapter files.

## Schema Baseline

The largest schema clusters are currently spread across CLI handlers and the
Telegram adapter.

### `src/cli/offdesk.rs`

11 schema string literals:

- `approval_brief.v1`
- `closeout_receipt.v1`
- `offdesk_marp_deck.v1`
- `offdesk_multiturn_plan.v1`
- `offdesk_plan_launch_prep.v1`
- `offdesk_plan_registration.v1`
- `offdesk_plan_review.v1`
- `offdesk_planner_council.v1`
- `remote_operator_readonly_projection.v1`
- `remote_operator_telegram_feedback.v1`
- `source_observation.v1`

### `src/cli/ondesk.rs`

12 schema string literals:

- `accepted_truth_recovery.v1`
- `accepted_truth_recovery_action_envelope.v1`
- `accepted_truth_recovery_action_receipt.v1`
- `action_envelope.v1`
- `action_envelope_receipt.v1`
- `action_execution_preflight.v1`
- `decision_action_closeout.v1`
- `decision_action_execution.v1`
- `decision_record.v1`
- `review_surface.v1`
- `runtime_dispatch_preflight.v1`
- `runtime_dispatch_receipt.v1`

### `scripts/offdesk_remote_operator_telegram.py`

32 schema string literals:

- `offdesk_llm_provider_resolution.v1`
- `offdesk_multiturn_plan.v1`
- `offdesk_plan_launch_prep.v1`
- `remote_operator_readonly_projection.v1`
- `remote_operator_telegram_adapter_result.v1`
- `remote_operator_telegram_feedback.v1`
- `remote_operator_telegram_health.v1`
- `remote_operator_telegram_state.v1`
- `telegram_action_readiness.v1`
- `telegram_agent_intent.v1`
- `telegram_choice_surface_contract.v1`
- `telegram_interaction_context.v1`
- `telegram_mobile_card_contract.v1`
- `telegram_remote_plan_closeout_packet.v1`
- `telegram_remote_plan_closeout_review_handoff.v1`
- `telegram_remote_plan_closeout_verdict.v1`
- `telegram_remote_plan_draft.v1`
- `telegram_remote_plan_enqueue_handoff.v1`
- `telegram_remote_plan_enqueue_run.v1`
- `telegram_remote_plan_execution_brief.v1`
- `telegram_remote_plan_gate_request.v1`
- `telegram_remote_plan_gate_resolution.v1`
- `telegram_remote_plan_launch_prep.v1`
- `telegram_remote_plan_registration.v1`
- `telegram_remote_plan_review.v1`
- `telegram_remote_plan_runtime_monitor.v1`
- `telegram_remote_plan_runtime_start.v1`
- `telegram_remote_plan_session.v1`
- `telegram_remote_plan_workload_binding.v1`
- `telegram_remote_project_candidate.v1`
- `telegram_remote_project_init_preview.v1`
- `telegram_remote_project_init_run.v1`

## Telegram Workflow Baseline

The Telegram remote plan session currently has 37 active stages:

- `project_selection`
- `project_selected`
- `project_manual_input`
- `project_path_required`
- `project_init_previewed`
- `project_init_created`
- `project_init_failed`
- `plan_draft_validated`
- `plan_draft_failed`
- `plan_registered`
- `plan_registration_failed`
- `plan_review_approved`
- `plan_review_failed`
- `plan_launch_prep_prepared`
- `plan_launch_prep_failed`
- `plan_gate_request_created`
- `plan_gate_request_failed`
- `plan_gate_approved`
- `plan_execution_brief_created`
- `plan_execution_brief_failed`
- `plan_enqueue_handoff_created`
- `plan_enqueue_handoff_failed`
- `plan_workload_path_required`
- `plan_workload_binding_failed`
- `plan_workload_bound`
- `plan_enqueue_run_failed`
- `plan_enqueued`
- `plan_runtime_started`
- `plan_runtime_start_failed`
- `plan_runtime_monitored`
- `plan_runtime_monitor_failed`
- `plan_closeout_packet_created`
- `plan_closeout_packet_failed`
- `plan_closeout_review_handoff_created`
- `plan_closeout_review_handoff_failed`
- `plan_closeout_verdict_recorded`
- `plan_closeout_verdict_failed`

The adapter also carries routing, rendering, persistence, health, Telegram
transport, local model calls, project scanning, receipt writing, and workflow
transition logic in one Python file.

## Current Verification Bar

Before and after each decomposition slice, run at minimum:

```bash
python3 -m py_compile scripts/offdesk_remote_operator_telegram.py
cargo fmt -- --check
git diff --check
cargo test --test remote_operator_telegram
```

For Rust Offdesk/Ondesk decomposition slices, add the relevant targeted tests:

```bash
cargo check
cargo test --test offdesk_cli
cargo test --test ondesk_cli
```

## First Reduction Targets

1. Extract Telegram input routing and session routing predicates into a
   transport-free module with unit-testable pure functions.
2. Extract Telegram message rendering and mobile-card contracts into a separate
   module.
3. Extract Telegram remote plan session transitions into a small workflow
   module before adding any new remote action stage.
4. After the Telegram adapter split proves the pattern, apply the same
   approach to `src/cli/offdesk.rs`: command parsing stays in CLI code, while
   stage transition and receipt construction move into `src/offdesk/workflow/`.

