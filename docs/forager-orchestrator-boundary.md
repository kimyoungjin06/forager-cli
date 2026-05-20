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

The recommended checkout names are:

```text
/home/kimyoungjin06/Desktop/Workspace/98.Harness/forager-cli
/home/kimyoungjin06/Desktop/Workspace/forager-control      # possible future rename
```

No checkout-level compatibility symlink is kept after the harness move:

```text
/home/kimyoungjin06/Desktop/Workspace/agent-of-empires     # removed local path
/home/kimyoungjin06/Desktop/Workspace/forager-cli          # removed local path
/home/kimyoungjin06/Desktop/Workspace/aoe_orch_control     # unchanged until separately renamed
```

Do not perform the local folder rename in the same patch as large Offdesk
runtime changes. Rename the checkout only after the stabilization tree is
checkpointed, because historical reports, scripts, shell history, and memory
records may still contain absolute paths under `agent-of-empires`.

## Product Direction

Forager should become the durable local autonomy substrate: safe execution,
auditable state, adaptive wiki projection, provider recovery, and result review.

The orchestrator should remain a higher-level operations layer: it coordinates
projects and people, but delegates local execution truth to Forager. If
`aoe_orch_control` is renamed, choose a name that makes this layering explicit,
such as `forager-control` or `forager-orch-lab`.

## Migration Checklist

Before renaming the local checkout:

- checkpoint or commit the current Offdesk stabilization patch set;
- verify no running tmux/offdesk task is using the old checkout as its workdir;
- check that no `98.Harness/forager-cli` directory already exists;
- remove the previous `agent-of-empires` symlink instead of preserving checkout
  path compatibility;
- run `cargo test --test offdesk_cli` and one `forager offdesk status` or
  equivalent smoke after the rename;
- update local notes/scripts only when they are active operational surfaces, not
  archival evidence paths.

The rename is a workspace hygiene step, not a product compatibility break.
