# Project Initialization

`forager project init` creates a read-only operation packet for a new project.
It is the bootstrap step before Ondesk/Offdesk/wiki work starts.

In the full operation cycle, initialization is the first bounded artifact. It
replaces "look around the repo again from scratch" with a durable first-read
packet that later Ondesk prompt packages and Offdesk preflight checks can
reference. See the [Operation Cycle Guide](operation-cycle.md) for the complete
handoff path and
[`Documentation And Artifact Governance`](documentation-artifact-governance.md)
for the long-running documentation and artifact model.

```bash
forager project init /path/to/project \
  --project-key my-project \
  --operation-target modules/03_regspec_machine \
  --include-git \
  --json
```

The command reads the target project and writes artifacts under the active
Forager profile:

```text
project_initializations/<timestamp>_<project-key>/
  PROJECT_OPERATION_PROFILE.json
  PROJECT_ONBOARDING.md
  MODULE_CANDIDATES.json
  MODULE_OPERATION_PREFLIGHT.json
  EVIDENCE_COLLECTOR_PLAN.md
  GOVERNANCE_SURFACE_HINTS.md
  WIKI_SEED_CANDIDATES.json
  ONDESK_START_PACKAGE.md
  OFFDESK_READY_CHECK.json
```

Use `--out <dir>` to place the packet somewhere else. Existing non-empty output
directories are refused unless `--force` is provided.

Use `--operation-target <MODULE_PATH_OR_ID>` when a project-level initialization
should prioritize a known module, such as TwinPaper's
`modules/03_regspec_machine`. The project remains the top-level target, while
the selected module is recorded as a module operation target.

## Boundary

Initialization does not grant authority. It does not:

- run project commands;
- enqueue Offdesk runtime;
- promote adaptive wiki entries;
- clean, archive, move, or delete files;
- change provider/model routing;
- install packages or change system state.

It only scans project shape, records candidate contracts, and prepares material
for operator review.

## Packet Contents

`PROJECT_OPERATION_PROFILE.json` records:

- `project_key` and project root;
- scope model: project target, module operation targets, module candidates,
  and artifact scopes;
- root docs, entrypoints, artifact roots, and optional git status;
- default agent mode contracts for planning, development, analysis, writing,
  critique, and maintenance;
- Ondesk bridge policy;
- Offdesk runtime policy and required artifacts;
- safety policy.

`MODULE_CANDIDATES.json` is a shallow scan of likely operating units under
`modules/`, `apps/`, `packages/`, and `crates/`. Candidates are not approved
module operation profiles. They are review targets.

`MODULE_OPERATION_PREFLIGHT.json` turns selected operation targets into an
explicit preflight checklist. It records known module-profile builders,
evidence-bundle/review commands, runtime blockers, and operator decisions. It
is advisory and read-only; it does not run the commands or authorize Offdesk
runtime.

For example, a TwinPaper initialization should use:

```bash
forager project init /home/.../1.2.8.TwinPaper \
  --project-key twinpaper \
  --operation-target modules/03_regspec_machine
```

This records:

```text
project scope: twinpaper
module operation scope: module03_regspec_machine
```

Do not initialize the module as the project unless it is being split into a
separate repository or standalone product/research unit.

`EVIDENCE_COLLECTOR_PLAN.md` is the first draft of the deterministic evidence
collector contract. It says what the collector should read, not what the
project has proven.

`GOVERNANCE_SURFACE_HINTS.md` checks whether the target project already has
compact current-state, next-action, decision, and deliverables surfaces. It
includes copy-ready template sketches, but it remains a packet artifact; it
does not write files into the target project.

After reviewing the hints, an operator may apply the missing template surfaces
with an explicit reviewed workflow:

```bash
forager project apply-governance-hints /path/to/project \
  --project-key my-project \
  --reviewed
```

Without `--reviewed`, the command is a dry run and writes nothing. With
`--reviewed`, it creates only missing governance surface files and never
overwrites existing files. Use `--surface current-state`, `--surface
next-actions`, `--surface decisions`, or `--surface deliverables` to limit the
scope.

`WIKI_SEED_CANDIDATES.json` contains candidate-only wiki seeds. They should be
reviewed before promotion.

`ONDESK_START_PACKAGE.md` is the first package to give a fresh external
harness. It is designed to avoid raw context resume as the only startup path.
`forager ondesk prompt-package --project-key <project>` automatically includes
the latest matching project initialization start package from the active
profile, plus a concise `MODULE_OPERATION_PREFLIGHT.json` summary when present.
That lets a fresh Ondesk harness see the reviewed project/module scope,
readiness blockers, and module-preflight command purposes without searching for
the artifact directory manually or inheriting raw command strings.

`OFFDESK_READY_CHECK.json` marks Ondesk startup as ready but keeps Offdesk
runtime blocked until operator review selects a scoped operation.

After initialization or after a long run creates visible outputs, build the
artifact index to see which deliverables are linked, missing, or still only
present in output roots:

```bash
forager project artifact-index /path/to/project \
  --project-key <project> \
  --json
```

This emits `artifact_index.v1`. It is a read-only discovery and retention
surface, not approval to clean, archive, publish, or accept output as truth.

## Recommended Flow

1. Run `forager project init`.
2. Read `PROJECT_ONBOARDING.md`, `GOVERNANCE_SURFACE_HINTS.md`, and
   `OFFDESK_READY_CHECK.json`.
3. Apply missing governance surfaces only after operator review, for example
   `forager project apply-governance-hints /path/to/project --project-key <project> --reviewed`.
4. Manually refresh existing or stale governance surfaces when needed.
5. Run `forager project audit-docs /path/to/project --audit-profile standard`
   or `--audit-profile research-longrun` for long-running research projects.
6. Run `forager project artifact-index /path/to/project --project-key <project>`
   when the project has generated outputs or handoff artifacts to review.
7. Review module candidates and decide which ones need a module operation
   profile.
8. Read `MODULE_OPERATION_PREFLIGHT.json` and run/review the listed
   module-profile and evidence preflight commands where available.
9. Turn the evidence collector plan into a project-specific deterministic
   bundle builder.
10. Promote only reviewed wiki seeds.
11. Start Ondesk from `ONDESK_START_PACKAGE.md`.
12. Prepare Offdesk with a matching module operation preflight artifact, for
   example `scripts/prepare_twinpaper_offdesk_task.py --module-preflight-artifact latest`.
13. Enqueue Offdesk only after runtime capability, evidence, and closeout
   requirements are explicit.

## Operator Interpretation

Treat the packet as a scope map, not as a green light:

- `ready_for_ondesk_start=true` means a fresh harness has enough first reads to
  begin with context.
- `ready_for_offdesk_runtime=false` is normal until a specific task has a clean
  evidence bundle, module preflight, workload review, and runtime approval.
- `MODULE_OPERATION_PREFLIGHT.json` should be referenced by later prepare
  scripts, but it should not be copied wholesale into model prompts or operator
  output.
- `GOVERNANCE_SURFACE_HINTS.md` can seed missing docs, but the initialization
  command itself remains read-only with respect to the target project.
- `forager project apply-governance-hints` is the reviewed bridge from packet
  hints to target project files. It creates missing surfaces only and leaves
  existing files for manual refresh.
- A selected module target narrows operating context; it does not change the
  canonical project key or grant permission to mutate files.
