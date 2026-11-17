# Forager And Orchestrator Boundary

This note defines the product boundary between Forager and the older
`aoe_orch_control` workspace. The goal is to keep names and responsibilities
clear as Offdesk grows into a stronger autonomous workflow.

## Names

| Name | Meaning | Current policy |
|---|---|---|
| Forager | Product and user-facing CLI identity. | Primary name. |
| `forager-cli` | GitHub repository and recommended local checkout name for this Rust project. | Primary repository name. |
| `agent-of-empires` | Legacy product/check-out/storage name from the pre-Forager era. | Compatibility only. Do not use for new product surfaces. |
| `aoe` | Legacy binary alias and migration vocabulary. | Compatibility only. |
| `aoe_orch_control` | Separate Python/Telegram control-plane workspace. | Keep separate unless it is deliberately renamed later. |
| Orchestrator / control plane | Higher-level coordinator that can observe and request work. | Optional layer above Forager, not a replacement for Forager state. |

## Responsibility Split

Forager owns local execution truth:

- tmux session lifecycle;
- local agent session storage;
- Offdesk task queue state;
- pending approvals and approval audit records;
- background runner probes and recovery sidecars;
- capability registry and mutation safety rails;
- provider capacity/fallback state;
- adaptive wiki candidates, promoted entries, projections, usage records, and
  review artifacts;
- operator-facing CLI and generated CLI reference.

The orchestrator/control-plane layer may own coordination surfaces:

- Telegram or dashboard operator UI;
- cross-project queues and daily planning;
- external worker or remote runner dispatch requests;
- high-level status aggregation across projects;
- human notification policy;
- long-horizon planning and scheduling.

The orchestrator may call Forager commands or consume Forager JSON output. It
must not rewrite Forager's task queue, approval ledger, adaptive wiki files, or
runner sidecars directly. Those are Forager-owned canonical state.

## Interaction Contract

Allowed integration paths:

- run `forager offdesk ...` commands as the control-plane execution API;
- read `forager offdesk ... --json` output for dashboards;
- link to Forager result artifacts in control-plane reports;
- enqueue work through documented Forager commands when the operator has chosen
  that policy;
- preserve Forager approval IDs, task IDs, request IDs, and artifact paths as
  evidence references.

Disallowed integration paths:

- editing Forager profile JSON files directly from the orchestrator;
- treating Telegram/chat history as canonical Offdesk state;
- bypassing `dispatch.runtime` or `dispatch.provider_fallback` approval rails;
- replacing Forager adaptive wiki promotion/review rules with orchestrator-side
  memory mutation;
- inferring completion without Forager task/result/poll evidence.

## Local Folder Policy

Checkout paths are operator choices. Portable examples are:

```text
~/src/forager-cli
~/src/forager-control   # optional external coordinator, not a bundled service
```

The earlier workstation rename is preserved in the
[historical migration record](rename-forager.md). Existing reports can retain
old paths as evidence; do not move a live checkout just to match these examples.

## Product Direction

Forager should become the durable local autonomy substrate: safe execution,
auditable state, adaptive wiki projection, provider recovery, and result review.

The orchestrator should remain a higher-level operations layer: it coordinates
projects and people, but delegates local execution truth to Forager. If
`aoe_orch_control` is renamed, choose a name that makes this layering explicit,
such as `forager-control` or `forager-orch-lab`.

## Migration Checklist

If a checkout move is separately selected, first checkpoint its work and
inspect active sessions and Offdesk working directories. Update active local
scripts and references after the move while preserving historical evidence.
Use `forager status --json` to inspect the selected profile and sessions.
The historical rename sequence is not a general setup prerequisite.
