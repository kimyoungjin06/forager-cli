# Sandbox Audit

Forager does not currently ship or recommend a Docker sandbox feature. The
inherited AoE sandbox path is treated as an audit surface until there is a
product decision to benchmark it, rebuild it as a Forager-native feature, or
remove it completely.

## Current Policy

- New sandbox sessions are blocked by `forager add --sandbox` and
  `forager add --sandbox-image`.
- TUI new-session data no longer carries sandbox creation parameters.
- The Home TUI no longer exposes sandbox creation controls.
- Settings no longer exposes or edits the sandbox category.
- Legacy sandbox sessions are readable but cannot be started as new Docker
  runtime sessions.
- Global, profile, and repo sandbox config schemas keep only the legacy
  `auto_cleanup` policy. Retired inherited keys are ignored on read.
- Container code is Docker-only legacy cleanup plumbing. Apple Container,
  image creation, runtime selection, and sandbox launch helpers have been
  removed.
- The old container-terminal tmux wrapper has been removed; Forager keeps only
  normal paired terminal session support.
- Official sandbox image Dockerfiles, publishing workflows, and public guides
  have been removed.
- JSON for existing sessions may still contain sandbox metadata and should
  remain readable during the transition.

## Keep For Compatibility

| Surface | Reason |
| --- | --- |
| `Instance::sandbox_info` / `SandboxInfo` | Existing stored session JSON may contain this field. |
| Legacy tmux status metadata such as `@forager_sandbox` and `@aoe_sandbox` | Existing tmux sessions may still expose these options. |
| `sandbox.auto_cleanup` config field | Existing delete flows may still need a stored container cleanup default. |
| Docker cleanup code that removes a stored sandbox container | Existing sessions may have an associated container name. |
| Legacy sandbox tmux/status metadata rendering | Existing tmux sessions may still need enough metadata to inspect and delete them. |

These surfaces should not create new sandbox sessions. They only preserve the
ability to inspect or clean up inherited state.

## Rebuild Candidates

If sandboxing returns, it should be treated as a new Forager-native design or a
fresh benchmark project rather than an extension of the removed AoE runtime
abstraction.

## Delete Candidates

| Surface | Deletion Condition |
| --- | --- |
| Docker cleanup module | Remove if no stored legacy sandbox containers need cleanup support. |

## Legacy Tests

`tests/legacy_sandbox_compatibility.rs` verifies that inherited sandbox metadata
can still be deserialized, persisted, and rendered with stable container names.
It does not start Docker or validate new sandbox session creation.

## Verification Targets

- `forager add --sandbox` fails with the deferred-sandbox message.
- `forager add --sandbox-image ...` fails with the same message.
- Normal non-sandbox session creation still works.
- Existing session JSON containing `sandbox_info` can still be loaded.
- Starting a loaded legacy sandbox session fails before creating or starting a
  Docker container.
- Legacy sandbox metadata can still be rendered without advertising new sandbox
  creation.
- Legacy sandbox config files that contain retired inherited keys still parse,
  but only `sandbox.auto_cleanup` affects behavior.
- Container runtime abstraction and image-creation code are absent from the
  active tree.
- Container-terminal tmux session wrappers are absent from the active tree.
