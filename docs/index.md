# Forager

A local meta-harness for harness-backed AI coding agents on Linux and macOS,
built on tmux and written in Rust.

> Forager lets people entrust meaningful work to agents and return to evidence,
> choices, and continuity instead of mystery.

Forager lets you run multiple harness-backed agents in parallel, each in its own
tmux session and optionally on its own git branch. It is useful as a session
manager on day one, but its product direction is larger: make long-running agent
work governable, inspectable, recoverable, and transferable.

Forager does not replace Claude Code, Codex, OpenCode, OpenHands, Gemini CLI,
Mistral Vibe, or local LLM scripts. It supervises harness-backed agents with
local approvals, durable evidence, recovery state, reviewable handoffs, and
deliberate knowledge promotion.

![Forager TUI preview](assets/tui.png)

## What To Read First

- [Project Direction](project-direction.md) defines the north star, product
  boundary, and practical completion criteria.
- [Operation Cycle](guides/operation-cycle.md) explains the Ondesk to Offdesk to
  Ondesk loop.
- [Hosted Harness Agents](hosted-harness-agents.md) defines how external
  harness-backed agents fit into Forager.
- [Quick Start](quick-start.md) gets the TUI running for ordinary agent sessions.

## Why Forager?

AI coding agents are strong enough to do meaningful work, but long-running work
often becomes hard to inspect after the fact. Forager keeps the useful autonomy
and makes the operating boundary explicit:

- **Entrust** a bounded task to a chosen harness-backed agent.
- **Observe** what actually ran through local state, tmux, heartbeat, progress,
  logs, and result artifacts.
- **Return** to evidence, risks, next choices, and handoff packages.
- **Promote** only reviewed lessons into durable project knowledge.

## Supported Harness-Backed Agents

Claude Code, OpenCode, Mistral Vibe, Codex CLI, and Gemini CLI are currently
auto-detected for interactive sessions. Offdesk can also supervise local command
workloads, deterministic review harnesses, local LLM scripts, and future hosted
harness agents through the same approval and evidence model.

## Core Surfaces

- **TUI dashboard**: create, monitor, attach, recover, and review sessions.
- **Paired terminals**: run git, builds, tests, and inspection commands without
  interrupting the agent.
- **Offdesk runtime**: queue bounded work, require approval, preserve runtime
  evidence, and close out before returning Ondesk.
- **Adaptive wiki**: capture agent-created lessons as candidates and promote
  only reviewed knowledge.
- **CLI/JSON**: keep automation and audit grounded in durable local state.

<div class="cta-box">
<p><strong>Ready to get started?</strong></p>
<p><a href="installation.html">Install Forager</a></p>
</div>
