# Project State

Updated: 2026-11-17

This is the public source-development entrypoint. Start with the
[README](README.md) for installation and ordinary use. The workspace's live
operator records and accepted task decisions are maintained separately.

## Current focus

The current source update prepares the repository and website for publication:

- README, documentation and the website follow the same install, prepare,
  run, review, agree and resume path.
- Published release scope, source capabilities, sample workspace views and
  task-specific local-model qualification are distinguished.
- Public descriptions lead with session/worktree management. Product goals,
  status estimates, recorded reviews and task-specific validation are distinct.
- The website groups its workspace routes, supports keyboard/mobile navigation
  and provides a shared installation command with copy-failure feedback.
- Document bodies distinguish design requirements, source interfaces, tagged
  releases and dated observations. Historical originals and adverse evidence
  remain available; user instructions follow the selected source.
- CLI reference text is generated from the current command definitions.
- Static-site link validation and dependency auditing gate Pages publication.

These are source changes and local checks. Publishing a branch, deploying Pages,
selecting a runtime release and accepting a project result are separate actions.

## Repository map

| Path | Purpose |
| --- | --- |
| [README](README.md) | Product overview, installation and first session |
| [Documentation index](docs/index.md) | User guides and reference entrypoints |
| [Operation status](docs/offdesk-operation-status.md) | Source capabilities, release and workload boundaries |
| [Development](docs/development.md) | Source, website and verification commands |
| `src/`, `scripts/`, `tests/` | Implementation, tools and regression fixtures |
| `website/` | Astro website and sample operator views |
| [Retained source qualification notes](archive/development-state/20261111-qualified-source.md) | Historical validation outcomes and limits, preserved verbatim |

## Plan ownership and execution boundaries

Task IDs refer to the existing operator-owned continuity plan. Its full history,
plan transaction journal, live queues, credentials, operational backups, and
local qualification evidence are private workspace artifacts. Resolve that
existing plan before applying task dispositions. The public roadmap is a
product summary rather than a second authoritative queue.

C5.1a, independent local-model coding and context recovery, and C6.3-local, the
complete local-adapter two-project cycle, remain unqualified. Core acceptance
does not accept other projects' scientific findings. Planning does not launch
a new Offdesk run or authorize another service cutover.

## Verification and publication

Use the [development guide](docs/development.md#building-the-public-website) to
generate CLI help, build the assembled site, check its routes/assets/anchors,
audit locked website dependencies and run browser regressions.

Keep sample fixtures separate from real project results. Local checks establish
only their measured scope; cross-platform CI, a tagged release and actual user
acceptance retain their own evidence and decisions.
