# Project Initialization

`forager project init` creates a read-only operation packet for a new project.
It is the bootstrap step before Ondesk/Offdesk/wiki work starts.

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

## Recommended Flow

1. Run `forager project init`.
2. Read `PROJECT_ONBOARDING.md` and `OFFDESK_READY_CHECK.json`.
3. Review module candidates and decide which ones need a module operation
   profile.
4. Read `MODULE_OPERATION_PREFLIGHT.json` and run/review the listed
   module-profile and evidence preflight commands where available.
5. Turn the evidence collector plan into a project-specific deterministic
   bundle builder.
6. Promote only reviewed wiki seeds.
7. Start Ondesk from `ONDESK_START_PACKAGE.md`.
8. Enqueue Offdesk only after runtime capability, evidence, and closeout
   requirements are explicit.
