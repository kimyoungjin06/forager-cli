# Project Initialization

`forager project init` creates a read-only operation packet for a new project.
It is the bootstrap step before Ondesk/Offdesk/wiki work starts.

```bash
forager project init /path/to/project \
  --project-key my-project \
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
  EVIDENCE_COLLECTOR_PLAN.md
  WIKI_SEED_CANDIDATES.json
  ONDESK_START_PACKAGE.md
  OFFDESK_READY_CHECK.json
```

Use `--out <dir>` to place the packet somewhere else. Existing non-empty output
directories are refused unless `--force` is provided.

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
- root docs, entrypoints, artifact roots, and optional git status;
- default agent mode contracts for planning, development, analysis, writing,
  critique, and maintenance;
- Ondesk bridge policy;
- Offdesk runtime policy and required artifacts;
- safety policy.

`MODULE_CANDIDATES.json` is a shallow scan of likely operating units under
`modules/`, `apps/`, `packages/`, and `crates/`. Candidates are not approved
module operation profiles. They are review targets.

`EVIDENCE_COLLECTOR_PLAN.md` is the first draft of the deterministic evidence
collector contract. It says what the collector should read, not what the
project has proven.

`WIKI_SEED_CANDIDATES.json` contains candidate-only wiki seeds. They should be
reviewed before promotion.

`ONDESK_START_PACKAGE.md` is the first package to give a fresh external
harness. It is designed to avoid raw context resume as the only startup path.

`OFFDESK_READY_CHECK.json` marks Ondesk startup as ready but keeps Offdesk
runtime blocked until operator review selects a scoped operation.

## Recommended Flow

1. Run `forager project init`.
2. Read `PROJECT_ONBOARDING.md` and `OFFDESK_READY_CHECK.json`.
3. Review module candidates and decide which ones need a module operation
   profile.
4. Turn the evidence collector plan into a project-specific deterministic
   bundle builder.
5. Promote only reviewed wiki seeds.
6. Start Ondesk from `ONDESK_START_PACKAGE.md`.
7. Enqueue Offdesk only after runtime capability, evidence, and closeout
   requirements are explicit.
