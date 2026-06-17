# Project State

Updated: 2026-06-17

This is the small current surface for Forager development work. It is separate
from the public product README and from the mdBook user guides.

## Current Focus

Forager is a local autonomy meta-harness around tmux-supervised,
harness-backed agent work. The current development focus is the generic
Offdesk operating loop, mobile Remote Operator surfaces, runtime recovery,
documentation/artifact hygiene, and keeping domain-specific validation history
out of product-facing docs. The product direction is defined in
`docs/project-direction.md`.

## First Reads For Development

- repository rules: `AGENTS.md`
- project direction: `docs/project-direction.md`
- hosted harness contract: `docs/hosted-harness-agents.md`
- product overview: `README.md`
- remote operator contract: `docs/remote-operator.md`
- operation cycle: `docs/guides/operation-cycle.md`
- current offdesk status: `docs/offdesk-operation-status.md`
- runtime smoke runbook: `docs/guides/offdesk-runtime-smoke.md`
- long-run validation runbook: `docs/guides/offdesk-long-run-validation.md`
- adaptive wiki contract: `docs/adaptive-wiki.md`

## Active Documentation Work

- Public README, mdBook introduction, brand system, and Astro landing page lead
  with the north star and local meta-harness positioning.
- The committed TUI preview must stay profile-neutral. It should not show a
  project-specific profile, domain module, or private workspace name.
- `forager project audit-docs` provides the v1 audit for governance surfaces,
  deliverables, decision sources, current freshness, latest aliases, logs,
  adaptive wiki projection freshness, and focused recommendations.
- `forager offdesk closeout` carries documentation governance recommendations
  into `RETURN_PACKAGE.md`, so `ondesk prompt-package` can surface them without
  embedding the full audit summary.
- `forager project apply-governance-hints` is the reviewed bridge from packet
  hints into target project files. It dry-runs by default, creates only missing
  governance surfaces with `--reviewed`, and never overwrites existing docs.
- `forager offdesk wiki export-markdown` defaults to the active profile's
  `wiki-vault/` projection and reports whether the markdown vault is missing,
  stale, fresh, or empty relative to canonical adaptive wiki JSON state.
- `forager doctor` and `forager status --json` expose active profile/app
  directory source, making legacy AoE storage fallback visible before
  migration.
- Telegram Remote Operator health is live for the default profile. It reports a
  polling listener, configured chat allowlist, local model availability, and a
  read-only action surface. Remote launch remains blocked by design.
- Domain-specific validation notes have been moved out of mdBook product docs
  into `archive/domain-history/`. They remain available for historical
  inspection but should not shape public product language.

## Current Gaps

- The Remote Operator can guide project selection, initialization preview,
  plan draft creation, plan registration, and plan-review approval. It still
  must not start runtime work from Telegram.
- Launch-preparation, gate approval, monitoring, and closeout bridge phases
  need a separate design and implementation pass.
- The Telegram listener now survives known transport failures, but an external
  watchdog is still needed for machine, process-manager, or service-wide
  failures.
- Website dependencies need a security refresh before treating the public site
  build as release-clean.
- Large Offdesk and Telegram adapter modules need staged decomposition before
  adding more mutation-capable remote actions.

## Next Work Candidates

1. Add an external Remote Operator watchdog that reports stale listener health.
2. Refresh website dependencies and verify `npm audit`.
3. Update `docs/remote-operator.md` phase status for the implemented Plan Mode
   bridge and the remaining launch/monitor/closeout bridge.
4. Split the large Offdesk CLI and Telegram adapter into smaller modules.

## Refresh Rule

Refresh this file when the active development focus changes, a new governance
surface is added, or the next contributor would otherwise need to infer current
state from multiple long-form status documents.
