# Module Operation Profiles

Module operation profiles make Offdesk operate a project module through an
explicit contract instead of a loose repository scan.

For new projects, start with
[`Project Initialization`](project-initialization.md). Its
`MODULE_CANDIDATES.json` output identifies review targets that can later become
module operation profiles. Its `MODULE_OPERATION_PREFLIGHT.json` output records
the selected operation targets, known builder commands, evidence review
commands, and runtime blockers before Offdesk enqueue.

Keep the hierarchy explicit: the project target owns overall objectives,
adaptive wiki project scope, closeout, and Ondesk return context. A module
operation target owns canonical commands, module evidence gates, and
module-specific reportability vocabulary.

## Profile Shape

A module operation profile should be generated or reviewed from project-local
facts. It should not depend on chat history. A typical profile records:

- module identity and layout;
- canonical entrypoint or wrapper command;
- allowed operations and their mutation level;
- approval requirements for each operation;
- forbidden actions, including bypassing wrappers when the wrapper expresses
  the safe operation;
- evidence contract for the module's reports, logs, summaries, and validation
  artifacts;
- reportability vocabulary for success, exploratory evidence, blocked states,
  and missing evidence;
- next actions by agent mode.

## Example Command Pattern

Concrete projects can provide their own profile builders. A builder should be
read-only and write both machine and human artifacts:

```bash
scripts/build_module_operation_profile.py \
  --repo /path/to/project \
  --operation-target path/to/module \
  --evidence-bundle <evidence_bundle.json> \
  --include-git \
  --out <workload>/module_operation_profile.json
```

Expected outputs:

- `module_operation_profile.json`;
- `MODULE_OPERATION_PROFILE.md`.

## Evidence Bundle Integration

Evidence bundles can embed a compact `module_operation_profiles` projection so
Offdesk prompts consume the same module contract as deterministic review tools.

A typical state separates the presence of evidence from the quality of the
claim:

```text
baseline_evidence_status = evidence_available
claim_status = pending_review
```

That state does not mean the module is reportable as a successful result. It
means evidence exists and still needs review before promotion or reuse.

## Operating Policy

Treat planning and inspection operations as the only default non-mutating
module operations. Any operation that writes module artifacts must go through
Offdesk `dispatch.runtime` approval.

For long runs, use `local-tmux` when live inspection is needed. Do not let the
module profile authorize file cleanup, deletion, archive, package installs,
permission changes, service restarts, storage or mount changes, reboot,
kernel/driver/firmware changes, or provider retargeting. Those remain separate
governed actions.

## Ondesk Return

Ondesk return packages should summarize the module state, not only the latest
Offdesk task. Useful first reads include:

- `result.json` from the Offdesk run;
- `module_operation_profile.json`;
- project-local operation logs;
- latest review and summary metadata.

The return summary should answer:

- which module operation ran or was planned;
- which evidence gates passed, failed, or are missing;
- whether the state is reportable, exploratory, or blocked;
- which adaptive wiki entries affected the judgement.
