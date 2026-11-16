# Forager

Forager manages coding agent sessions and Git worktrees from a terminal UI.
It uses tmux and is written in Rust for Linux and macOS.

Start with a coding agent in a tmux session and an optional Git worktree. Keep
its terminal and diff close at hand. Optional Offdesk workflows add task queues,
execution approvals, logs and result review for explicitly configured commands.

![Forager TUI preview](assets/tui.png)

## Choose a starting point

| Your goal | Start here |
| --- | --- |
| Install and open your first session | [Installation](installation.md), then [Quick start](quick-start.md) |
| Work on separate branches | [Session workflow](guides/workflow.md) and [Git worktrees](guides/worktrees.md) |
| Prepare work and review it on return | [Operation cycle](guides/operation-cycle.md) |
| Inspect and control work from Telegram | [Remote operator](remote-operator.md) |
| Understand the supported scope | [Operation status](offdesk-operation-status.md) |
| Look up a command or setting | [CLI reference](cli/reference.md) and [Configuration](guides/configuration.md) |

The public website's workspace pages are static previews. Local exports and
an explicitly configured local action bridge supply your own workstation data
and controls. See the [remote operator guide](remote-operator.md) for setup.

Release downloads contain tagged versions. This documentation tree may describe
newer source changes; use the [release notes](releases/v0.14.0.md) for the
published version and [operation status](offdesk-operation-status.md) for scope.

## Why Forager?

When several coding sessions are open, Forager provides one place to list them,
attach to their terminals, inspect diffs and manage their working directories.
For configured Offdesk tasks, you can also inspect execution records and result
artifacts before recording a review decision. The available artifacts depend on
the runner and task configuration.

## Interactive Coding CLIs

Claude Code, OpenCode, Mistral Vibe, Codex CLI, and Gemini CLI are auto-detected
for interactive sessions. Other command workloads need explicit configuration
and checks for their selected task. Local-model coding and context recovery
remain task-specific qualification work; running a command alone does not
establish those outcomes.

## Core Surfaces

- **TUI dashboard**: create, list and attach to sessions; inspect status estimates
  and diffs.
- **Paired terminals**: run git, builds, tests, and inspection commands without
  interrupting the agent.
- **Offdesk runtime**: queue bounded work, require approval, preserve runtime
  evidence, and close out before returning Ondesk.
- **Adaptive wiki**: store proposed lessons, evidence references and review
  decisions. A promotion record does not prove a lesson is correct.
- **CLI/JSON**: keep automation and audit grounded in durable local state.

## Product and implementation references

[Project direction](project-direction.md) describes the product's goals.
[Harness contracts](hosted-harness-agents.md),
[functional definitions](functional-definitions.md),
[decision pipeline](decision-pipeline.md) and
[evidence and memory boundaries](evidence-search-and-memory-boundary.md)
explain the design. Benchmarking and implementation proposals are grouped under
Design and development in the navigation.

<div class="cta-box">
<p><strong>Ready to get started?</strong></p>
<p><a href="installation.html">Install Forager</a></p>
</div>
