# Project State

Updated: 2026-05-31

This is the small current surface for Forager development work. It is separate
from the public product README and from the mdBook user guides.

## Current Focus

Forager is an approval-gated offdesk runtime harness around tmux-backed agent
work. The current development focus is long-running offdesk safety, operator
decision surfaces, adaptive wiki governance, and documentation/artifact hygiene.

## First Reads For Development

- repository rules: `AGENTS.md`
- product overview: `README.md`
- documentation governance guide:
  `docs/guides/documentation-artifact-governance.md`
- operation cycle: `docs/guides/operation-cycle.md`
- current offdesk status: `docs/offdesk-operation-status.md`
- adaptive wiki contract: `docs/adaptive-wiki.md`
- adaptive wiki execution plan: `docs/adaptive-wiki-execution-plan.md`

## Active Documentation Work

- Documentation and artifact governance is now captured as a guide under
  `docs/guides/documentation-artifact-governance.md`.
- `forager project audit-docs` now provides the v1 audit for governance
  surfaces, deliverables, decision sources, current freshness, latest aliases,
  logs, adaptive wiki projection freshness, and focused recommendations. The
  repository script remains a reference/fallback implementation.
- `forager offdesk closeout` now carries documentation governance
  recommendations into `RETURN_PACKAGE.md`, so `ondesk prompt-package` can
  surface them without embedding the full audit summary.
- Project initialization links to the governance model so new project packets
  have a place in the larger documentation lifecycle, and it now writes
  `GOVERNANCE_SURFACE_HINTS.md` as a read-only packet artifact.
- `forager project apply-governance-hints` is the reviewed bridge from packet
  hints into target project files. It dry-runs by default, creates only missing
  governance surfaces with `--reviewed`, and never overwrites existing docs.
- `forager offdesk wiki export-markdown` now defaults to the active profile's
  `wiki-vault/` projection and reports whether the markdown vault is missing,
  stale, fresh, or empty relative to canonical adaptive wiki JSON state.
- `forager doctor` and `forager status --json` now expose active profile/app
  directory source, making legacy AoE storage fallback visible before
  migration.
- `RETURN_PACKAGE.md` now uses a compact Ondesk-facing template with status,
  decisions needed, capped first reads, grouped evidence, documentation
  governance recommendations, and a next safe action. Full inventories stay in
  `closeout_plan.json` and `cleanup_manifest.json`.
- Offdesk operation status remains the running status and candidate work queue
  for operator surfaces and safety rails.

## Current Gaps

- Closeout consumes audit recommendations, but prompt packages still rely on
  the closeout return package rather than running a fresh audit themselves.
- The compact return package template is covered by fixture tests, but still
  needs validation on a real completed TwinPaper run.

## Next Work Candidates

1. Consider whether `ondesk prompt-package` should optionally run a fresh
   `project audit-docs` pass when no closeout package exists.
2. Run closeout on a real completed TwinPaper workload and inspect whether the
   compact return package is readable enough for morning handoff.

## Refresh Rule

Refresh this file when the active development focus changes, a new governance
surface is added, or the next contributor would otherwise need to infer current
state from multiple long-form status documents.
