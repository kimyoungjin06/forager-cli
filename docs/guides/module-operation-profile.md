# Module Operation Profiles

Module operation profiles make Offdesk operate a project module through an
explicit contract instead of a loose repository scan.

The first concrete profile is TwinPaper Module03:

```bash
scripts/build_twinpaper_module03_operation_profile.py \
  --repo /home/kimyoungjin06/Desktop/Workspace/1.2.8.TwinPaper \
  --evidence-bundle <evidence_bundle.json> \
  --include-git \
  --out <workload>/module03_operation_profile.json
```

The command is read-only. It writes:

- `module03_operation_profile.json`
- `MODULE03_OPERATION_PROFILE.md`

## Contract Surface

The profile records:

- module identity and layout: monorepo vs standalone module;
- canonical entrypoint: `modules/03_regspec_machine/scripts/run_module_03.sh`;
- allowed operations: `plan`, `single-nooption`, `single-singlex`, `paired`,
  `overnight`, and `contract-ci`;
- approval requirements and mutation policy for each operation;
- forbidden actions, including direct internal Python entrypoints when the
  wrapper can express the operation;
- evidence contract: `RunLog.md`, direction-review artifacts, paired summaries,
  run summaries, `validated_candidate`, `p/q`, `restart_stability`, and
  `primary_objective_gate`;
- reportability vocabulary for `executed_primary_gate_failed`,
  `pending_not_reportable`, `exploratory_evidence_available`, and
  `promotion_ready_evidence_absent`;
- next actions by agent mode.

## Evidence Bundle Integration

`scripts/build_twinpaper_evidence_bundle.py` embeds a compact
`module_operation_profiles.module03_regspec_machine` projection. This lets
Offdesk prompts consume the same module contract as deterministic review tools.

For the current TwinPaper shape, a typical state is:

```text
baseline_evidence_status = executed_primary_gate_failed
claim_status = pending_not_reportable
```

That state does not mean evidence is missing. It means the no-option/singlex
baseline evidence exists but primary objective gates are failing, so the module
is not reportable as a successful research claim.

## Operating Policy

Treat `plan` as the only default non-mutating module operation. Any operation
with `--exec` writes module artifacts and must go through Offdesk
`dispatch.runtime` approval.

For long runs, use `local-tmux`. Do not let the module profile authorize file
cleanup, deletion, archive, package installs, permission changes, service
restarts, storage or mount changes, reboot, kernel/driver/firmware changes, or
provider retargeting. Those remain separate governed actions.

## Ondesk Return

Ondesk return packages should summarize the module state, not only the latest
Offdesk task. The first reads are:

- `result.json` from the Offdesk run;
- `module03_operation_profile.json`;
- `docs/operations/RunLog.md`;
- latest direction-review and paired-summary metadata.

The return summary should answer:

- which Module03 operation ran or was planned;
- which evidence gates passed, failed, or are missing;
- whether the state is reportable, exploratory, or blocked;
- which adaptive wiki entries affected the judgement.
