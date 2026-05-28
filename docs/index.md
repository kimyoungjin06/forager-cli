# Forager

A terminal session manager for AI coding agents on Linux and macOS, built on tmux and written in Rust.

Forager lets you run multiple AI agents in parallel -- each in its own tmux session, optionally on its own git branch. A TUI dashboard shows you what every agent is doing at a glance.

For Offdesk work, Forager also acts as an approval-gated runtime harness. It can
prepare work while you are present, stop at an explicit `dispatch.runtime`
approval, run long workloads under tmux, preserve evidence, and build a return
package for the next Ondesk session. The recommended lifecycle is described in
the [Operation Cycle Guide](guides/operation-cycle.md).

![Forager TUI preview](assets/tui.png)

## Why Forager?

**The problem:** You're working with AI coding agents (Claude Code, OpenCode, Codex, etc.) and want to run several in parallel across different tasks or branches. Managing multiple terminal windows and git branches by hand gets tedious fast.

**Forager handles it for you:**

- **One dashboard for all agents.** See status (running, waiting, idle, error) at a glance. Toggle to paired shell terminals with `t`.
- **Git worktrees built in.** Create a session and Forager creates a branch + worktree automatically. Delete the session and Forager cleans up.
- **Per-repo configuration.** Drop a `.forager/config.toml` in your repo for project-specific settings and hooks that run on session creation or launch. Existing `.aoe/config.toml` files remain supported as a compatibility fallback.
- **Sessions survive everything.** Forager wraps tmux, so agents keep running when you close the TUI, disconnect SSH, or your terminal crashes.
- **Offdesk lifecycle gates.** Queue runtime work, require operator approval,
  preserve heartbeat/progress/result artifacts, and close out before returning
  to live Ondesk work.

## Supported Agents

Claude Code, OpenCode, Mistral Vibe, Codex CLI, and Gemini CLI. Forager auto-detects which are installed.

<div class="cta-box">
<p><strong>Ready to get started?</strong></p>
<p><a href="installation.html">Install Forager</a></p>
</div>
