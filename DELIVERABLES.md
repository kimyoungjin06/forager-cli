# Deliverables

Updated: 2026-05-30

This is the compact development deliverables surface for Forager. It is for
humans who need to inspect outputs without browsing build or documentation
folders.

## Product And Documentation

- `README.md`: public product overview.
- `docs/index.md`: mdBook introduction.
- `docs/SUMMARY.md`: mdBook navigation.
- `docs/guides/operation-cycle.md`: offdesk lifecycle guide.
- `docs/guides/documentation-artifact-governance.md`: documentation and
  artifact governance guide.
- `docs/cli/reference.md`: generated CLI reference.

## Operator And Offdesk References

- `docs/offdesk-operation-status.md`: current offdesk status and improvement
  queue.
- `docs/adaptive-wiki.md`: adaptive wiki operator boundary.
- `docs/adaptive-wiki-execution-plan.md`: adaptive wiki implementation plan.
- `docs/guides/approval-brief.md`: operator decision brief contract.
- `docs/guides/twinpaper-offdesk-long-run-validation.md`: realistic long-run
  validation procedure.

## Visual Assets

- `docs/assets/tui.png`: committed TUI preview used by documentation.
- `docs/assets/benchmarks/single_agent_ops_template_20260226.zip`: benchmark
  template asset.

## Local Build Outputs

- `target/debug/forager`: local debug binary when built.
- `target/release/forager`: local release binary when built.
- `book/`: local mdBook output when `mdbook build` is available.

Local build outputs are not source-controlled deliverables. They are listed
here as inspection targets for local validation.

## Promotion Rule

Add a path here when it is useful for human inspection, release review,
operator handoff, or documentation validation. Keep raw test and build outputs
in their source directories.
