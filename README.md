# aoe_orch_control

Telegram-controlled orchestration workspace for multi-project AOE operations.

## Source Attribution
- Base project (fork/upstream): `njbrake/agent-of-empires`
- Upstream repository: `https://github.com/njbrake/agent-of-empires`
- This repository adds a Telegram control plane, runtime queue/proposal handling, offdesk automation, and tmux-oriented operator workflow.

## What This Repository Is
- A `Python + shell/tmux` control plane for orchestrating project runtimes.
- A runtime queue/task system with `todo`, `proposal`, `sync`, `salvage`, and `syncback` flows.
- An operator-first workflow for:
  - `on-desk`: tmux/session switching and local orchestration
  - `off-desk`: Telegram-based review, scheduling, and monitoring

## What This Repository Is Not
- It is not a Rust codebase.
- It is not a replacement for upstream `aoe` core behavior.
- It is not a generic SaaS multi-agent platform.

## Core Intent
- Run project work reliably during off-hours.
- Keep orchestration separate from execution and verification.
- Preserve backlog state explicitly instead of burying it in chat history.

Project charter:
- `docs/PROJECT_CHARTER.md`
- `docs/CONSTITUTION.md`
- `docs/OPERATING_MODEL.md`

## Architecture At A Glance
- Control plane:
  - Telegram gateway
  - `aoe-team-stack`
  - tmux operator surface
- Execution plane:
  - request-scoped Task Team workdirs
  - role worker sessions
  - runtime queue/task state

Primary docs:
- Architecture: `docs/ARCHITECTURE.md`
- Operating model: `docs/OPERATING_MODEL.md`
- Control dashboard MVP: `docs/CONTROL_DASHBOARD_MVP.md`
- Control dashboard read-only design: `docs/CONTROL_DASHBOARD_READONLY_DESIGN.md`
- Preset completion matrix: `docs/PRESET_COMPLETION_MATRIX.md`
- Nightly session summary: `docs/NIGHTLY_SESSION_SUMMARY.md`
- Storage retention policy: `docs/STORAGE_RETENTION_POLICY.md`
- Control Action API: `docs/MOTHER_ORCH_ACTION_API.md`
- Command reference: `docs/COMMANDS.md`
- Deployment: `docs/DEPLOYMENT.md`
- Runbook: `docs/RUNBOOK.md`
- Daily checklist: `docs/DAILY_CHECKLIST.md`
- Roadmap: `docs/ROADMAP.md`
- Core decomposition plan: `docs/CORE_DECOMPOSITION_PLAN.md`
- AutoGen Core adoption note: `docs/AUTOGEN_CORE_ADOPTION.md`
- AutoGen Core sandbox pilot criteria: `docs/AUTOGEN_CORE_PILOT.md`
- LangChain deepagents benchmark note: `docs/LANGCHAIN_DEEPAGENTS_BENCHMARK_20260421.md`

## Repository Layout
- `scripts/gateway/`
  - Telegram gateway, scheduling, run pipeline, state/policy/view modules
- `scripts/team/`
  - stack launcher, runtime bootstrap, global CLI wrappers
- `templates/aoe-team/`
  - versioned runtime defaults
- `systemd/`
  - user service templates
- `docs/`
  - architecture, operations, deployment, governance
- `tests/gateway/`
  - gateway regression tests

## Main Entrypoints
- Stack launcher:
  - `scripts/team/aoe-team-stack.sh`
- Gateway process:
  - `scripts/gateway/aoe-telegram-gateway.py`
- Runtime bootstrap:
  - `scripts/team/bootstrap_runtime_templates.sh`

## Quick Start
1. Initialize runtime for a project
```bash
aoe-team-stack --project-root /path/to/project init
```

2. Start the stack
```bash
aoe-team-stack --project-root /path/to/project start
```

3. Apply tmux UI helpers
```bash
aoe-team-stack --project-root /path/to/project ui
```

4. Telegram-side offdesk routine
```text
/offdesk prepare
/offdesk review
/offdesk on
```

## Key Operator Workflows

### On-desk
- Inspect projects: `/map`
- Focus one project: `/use O#` or `/focus O#`
- Review backlog: `/queue`, `/todo O#`
- Run next item: `/next` or `/todo O# next`

### Off-desk
- Preflight: `/offdesk prepare`
- Resolve warnings: `/offdesk review`
- Enable automation: `/offdesk on`
- Check automation state: `/auto status short`

## Runtime Boundary
Package-managed, versioned assets:
- `scripts/`
- `templates/`
- `docs/`
- `tests/`
- `systemd/`

Generated runtime state:
- `.aoe-team/orch_manager_state.json`
- `.aoe-team/auto_scheduler.json`
- `.aoe-team/tf_exec_map.json`
- `.aoe-team/telegram_gateway_state.json`
- `.aoe-team/logs/`
- `.aoe-team/messages/`
- `.aoe-team/tf_runs/`

Rule:
- keep code and templates in the repository
- treat `.aoe-team/` as mutable environment-local state

## Testing
- Gateway pytest wrapper (defaults to CLI regressions; accepts explicit test paths):
```bash
scripts/gateway_pytest.sh
```

- Smoke subset:
```bash
bash scripts/gateway_smoke_test.sh
```

- Error subset:
```bash
bash scripts/gateway_error_test.sh
```

- Dashboard/operator subset:
```bash
bash scripts/gateway_dashboard_test.sh
```

- Full gateway suite:
```bash
bash scripts/gateway_full_test.sh
```

- CI workflow:
  - `.github/workflows/gateway-tests.yml`
  - `.github/workflows/external-background-worker.yml` for manual/repository-dispatch `github_runner` handoff pickup
- External sidecar import:
  - `scripts/gateway/aoe-external-sidecar-sync.py import-artifact --team-dir <team_dir> --artifact-root <artifact-dir-or-zip> --ticket-id <ticket> --runner github_runner --poll`

## Experimental Task Team Backends
Current production path:
- local Task Team backend based on `aoe-orch` + tmux/request-scoped workers

Planned experimental seam:
- `scripts/gateway/aoe_tg_tf_backend.py`
- `scripts/gateway/aoe_tg_tf_backend_local.py`
- `scripts/gateway/aoe_tg_tf_backend_autogen.py`
- `scripts/experiments/autogen_core_tf_spike.py`

Important rule:
- external frameworks may be used inside one Task Team execution backend
- backlog ownership, syncback, Telegram control, and offdesk scheduling remain in this repository

## Current Status
- Scheduler domain has been split into:
  - `aoe_tg_scheduler_handlers.py`
  - `aoe_tg_sync_sources.py`
  - `aoe_tg_sync_merge.py`
  - `aoe_tg_queue_engine.py`
- Management flows have been split into:
  - `aoe_tg_management_handlers.py`
  - `aoe_tg_scheduler_control_handlers.py`
  - `aoe_tg_offdesk_flow.py`
  - `aoe_tg_management_chat.py`
  - `aoe_tg_management_acl.py`

The project is in an `operational owner-only control plane` stage:
- suitable for personal multi-project operation
- first-wave phase2 runtime verification is complete for build/data/review/mixed happy, rerun, and manual-followup paths
- next maturity block is document/runtime convergence through `Project Flow Compiler`, dashboard `Document Flow`, and recovery drift excerpts
- still evolving in external runner productionization, governance/usage boundaries, retention cleanup, and long-term backend abstraction
