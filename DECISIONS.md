# Decisions

Updated: 2026-05-30

This is the compact development decision index for Forager. It points to the
decisions that currently shape implementation, documentation, and operator
surfaces.

| Decision | Status | Source | Operational effect |
| --- | --- | --- | --- |
| Forager owns offdesk queue state, approvals, recovery, and audit artifacts. | active | `docs/forager-orchestrator-boundary.md`, `docs/hermes-pattern-review.md` | External harnesses can run work, but durable control state stays in Forager-owned stores. |
| Raw chat history is not the source of truth for offdesk transitions. | active | `docs/guides/operation-cycle.md` | Each handoff stage should produce a bounded artifact. |
| Adaptive wiki canonical state remains JSON, with markdown as a human projection. | active | `docs/adaptive-wiki.md`, `docs/adaptive-wiki-execution-plan.md` | Runtime receives compact scoped projections, not raw human wiki pages. |
| Candidate wiki observations do not change runtime behavior by themselves. | active | `docs/adaptive-wiki.md` | Promotion and durable behavior changes require reviewable commands or operator decisions. |
| Long Python offdesk workloads should use `local-tmux` when they need live inspection. | active | `docs/guides/twinpaper-offdesk-long-run-validation.md`, `docs/offdesk-operation-status.md` | Health is checked through tmux, heartbeat, progress, logs, and result artifacts. |
| Documentation and artifact governance uses small current surfaces over large logs. | active | `docs/guides/documentation-artifact-governance.md`, `PROJECT_STATE.md` | New long-running projects should expose current state, decisions, next actions, and deliverables before raw logs. |
| Documentation governance checks should be available through `forager project audit-docs`. | active | `docs/guides/documentation-artifact-governance.md`, `src/cli/project_audit.rs` | Operators can run the same audit contract from Forager instead of depending on a repo-local Python script. |
| Project initialization may generate governance surface hints as packet artifacts only. | active | `docs/guides/project-initialization.md`, `src/cli/project.rs` | Template sketches help bootstrap docs without mutating the target project or granting cleanup/runtime authority. |
| Governance surface hints may be applied only through a reviewed create-only workflow. | active | `docs/guides/project-initialization.md`, `src/cli/project.rs` | `project apply-governance-hints --reviewed` creates missing current-state, next-action, decision, and deliverables surfaces while skipping existing files. |
| Documentation audit recommendations should be focused operator actions. | active | `docs/guides/documentation-artifact-governance.md`, `src/cli/project_audit.rs` | Markdown and prompts should carry short promote/retain/review actions, while full path inventories remain in machine JSON. |
| Offdesk closeout may carry documentation governance recommendations into Ondesk return. | active | `docs/guides/offdesk-closeout.md`, `src/cli/offdesk.rs` | Fresh Ondesk harnesses see focused documentation actions through the return package without rerunning or embedding the full audit. |
| Adaptive wiki markdown vaults are disposable projections of canonical profile JSON. | active | `docs/adaptive-wiki.md`, `src/offdesk/adaptive_wiki.rs` | Operators should re-export the profile wiki vault when canonical entries or candidates change, rather than treating markdown pages as writable source of truth. |
| Legacy AoE storage fallback remains compatibility behavior, but active storage must be visible. | active | `docs/rename-forager.md`, `src/cli/doctor.rs`, `src/cli/status.rs` | `doctor` and `status --json` report whether the active profile/app directory is primary, legacy, or new-primary so migration decisions are explicit. |

## Refresh Rule

Update this file when a decision changes authority, runtime safety, adaptive
wiki behavior, documentation governance, or operator-facing handoff semantics.
