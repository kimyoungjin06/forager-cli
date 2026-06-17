# Deliverables

Updated: 2026-06-17

This is the compact development deliverables surface for Forager. It is for
humans who need to inspect outputs without browsing build or documentation
folders.

## Product And Documentation

- `README.md`: public product overview.
- `docs/index.md`: mdBook introduction.
- `docs/SUMMARY.md`: mdBook navigation.
- `docs/project-direction.md`: product direction and operating principles.
- `docs/remote-operator.md`: mobile/chat operator contract.
- `docs/decision-pipeline.md`: canonical decision state design for
  Agent/Council/User escalation and execution handoff.
- `docs/guides/operation-cycle.md`: Offdesk lifecycle guide.
- `docs/guides/offdesk-runtime-smoke.md`: generic short runtime validation
  runbook.
- `docs/guides/offdesk-long-run-validation.md`: generic long-run validation
  runbook.
- `docs/guides/documentation-artifact-governance.md`: documentation and
  artifact governance guide.
- `docs/cli/reference.md`: generated CLI reference.

## Operator And Offdesk References

- `docs/offdesk-operation-status.md`: current Offdesk status and improvement
  queue.
- `docs/adaptive-wiki.md`: adaptive wiki operator boundary.
- `docs/guides/approval-brief.md`: operator decision brief contract.
- `docs/guides/offdesk-closeout.md`: closeout and review packet contract.
- `docs/guides/module-operation-profile.md`: generic module-operation profile
  contract.
- `scripts/prepare_offdesk_workload.py`: generic workload producer for a
  bounded command, reviewed manifest, launch dry-run packet, validation packet,
  and approval-gated enqueue script.
- `scripts/offdesk_workload_review_harness.py`: generic prepared-workload
  manifest reviewer. Domain-specific producers can tighten it with
  `review_contract` requirements.

## Visual Assets

- `docs/assets/tui.png`: committed neutral TUI preview used by documentation
  and the website hero.
- `assets/logo.svg`, `assets/logo.png`, `assets/logo-lockup.svg`: product logo
  assets.
- `assets/social-preview.svg`, `assets/social-preview.png`: social preview
  assets.

## Historical Validation Notes

- `archive/domain-history/`: domain-specific validation history retained
  outside mdBook product docs. These files are not current product surfaces.

## Local Build Outputs

- `target/debug/forager`: local debug binary when built.
- `target/release/forager`: local release binary when built.
- `book/`: local mdBook output when `mdbook build` is available.
- `dist/`: local website output from `scripts/build-site.sh`.

Local build outputs are not source-controlled deliverables. They are listed
here as inspection targets for local validation.

## Promotion Rule

Add a path here when it is useful for human inspection, release review,
operator handoff, or documentation validation. Keep raw test and build outputs
in their source directories.
