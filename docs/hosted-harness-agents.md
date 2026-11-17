# Hosted Harness Agents

A hosted harness agent runs another coding CLI through a configured Forager
runner. Forager records the task and execution around that CLI; the CLI owns
its agent loop and tool use. This page describes the built-in prompt profiles
and the intended integration contract. It does not qualify every workload or
harness version for unattended execution or recovery.

Interactive detection of Claude Code, Codex, OpenCode, Gemini CLI and Mistral
Vibe is separate from hosted prompt-profile support. OpenHands, Aider and other
future harnesses are integration candidates.

## Boundary

The hosted harness owns:

- the agent loop;
- prompts and system instructions;
- model calls and provider routing;
- tool policy inside the harness;
- intermediate reasoning and interaction style.

For configured Offdesk paths, Forager stores or manages:

- task intent and scope;
- approval gates;
- launch command and working directory;
- the requested mutation scope;
- runtime handle and liveness evidence;
- configured progress signals, logs, and result artifact references;
- recovery decision and closeout state;
- review package and next-harness handoff;
- adaptive wiki candidates and recorded promotion decisions.

The profile's mutation scope describes the requested operating boundary. A
repository path or Git worktree does not enforce a general file, process or
network sandbox. Enforcement of the agent's tool calls depends on that harness
and any separately configured restrictions.

Completed execution and recorded acceptance are separate states. A review
receipt records a decision about the submitted artifacts, not proof that the
result is correct or that every permitted operation was observed.

## Minimum Hosted Harness Contract

An integration should define the following fields. Their presence describes
the intended contract, not universal enforcement by every runner:

| Field | Purpose |
| --- | --- |
| Launch command | Exact command Forager may run after approval |
| Working directory | Repo, worktree, or scratch path used by the agent |
| Mutation scope | Requested file, branch and side-effect scope; enforcement must be established separately |
| Evidence sources | Paths or probes for heartbeat, progress, logs, and result artifacts |
| Result artifact | The artifact used to summarize what the run produced |
| Failure signal | How Forager detects error, stale, cancelled, or incomplete states |
| Closeout package | Human and machine-readable return package for fresh Ondesk work |
| Retention policy | What should be preserved, promoted, archived, or discarded |

The contract should be explicit before the agent runs. If a harness cannot
provide structured progress or result artifacts, Forager can still host it, but
the run should be labeled with the missing evidence and reviewed more
conservatively.

Hosted harnesses should not receive large raw context as a single prompt. The
default profile contract is:

- use a compact task prompt;
- keep inline context below the profile budget;
- pass `RETURN_PACKAGE.md`, `closeout_plan.json`, `result.json`, focused briefs,
  and selected source files as first-read artifacts;
- keep first-read artifacts under the profile budget, currently 64 KiB per file
  and 256 KiB total for supported Codex and Claude profiles;
- avoid pasting full git diffs, large logs, raw scrollback, or repository-wide
  inventories inline;
- report missing evidence explicitly instead of guessing.

Forager exposes the current built-in profile contract with:

```bash
forager offdesk harnesses
forager offdesk harnesses --json
```

Forager can also build a compact start packet for a hosted harness:

```bash
forager offdesk harness-prompt claude \
  --task "Review the closeout result and report missing evidence." \
  --first-read target/offdesk/RETURN_PACKAGE.md \
  --first-read target/offdesk/result.json \
  --result-artifact target/offdesk/result.json \
  --output target/offdesk/CLAUDE_START.md \
  --strict-first-read-budget
```

That prompt is intentionally a pointer surface. The hosted harness should read
the listed artifacts instead of asking the operator to paste the full diff or
raw logs into the prompt.

By default, `harness-prompt` reports missing or oversized first-read artifacts as
warnings in JSON and human output. Add `--strict-first-read-budget` when a
runtime smoke should fail before launch if the first-read packet is missing or
too large. Use `--max-first-read-total-bytes` to lower the total budget for a
specific smoke.

The built-in profile registry currently reports:

| Harness | Registry status | Available scope |
| --- | --- | --- |
| Codex CLI | `supported` | Built-in launch metadata and compact prompt/first-read profile for `local-tmux`. |
| Claude Code | `supported` | Built-in launch metadata and compact prompt/first-read profile for `local-tmux`. |
| Gemini CLI | `planned` | Registry entry exists; hosted artifact integration still needs a disposable smoke task. |
| OpenHands | `planned` | Future integration candidate. |
| Aider | `planned` | Future integration candidate. |

`supported` is the registry label for a built-in profile. Qualify the selected
command, harness version, task artifacts and recovery behavior separately; the
label does not establish a successful overnight workflow. See
[operation status](offdesk-operation-status.md) for validation boundaries.

## Preferred Integration Path

1. Start with a read-only or disposable-worktree smoke task.
2. Launch the hosted harness through `local-tmux` when live inspection matters.
3. Capture logs, command summary, runtime handle, and result artifacts.
4. Build a closeout package that a fresh harness can read first.
5. Compare evidence quality, latency, cost, and failure mode against another
   harness on the same task.
6. Promote a stable profile only after the evidence contract is repeatable.

## Selection Criteria

Choose a hosted harness agent based on the task and operating boundary:

- **Capability**: can it solve this task type well enough to justify running it?
- **Cost and tokens**: is the provider or local model budget appropriate?
- **Latency**: does the task need immediate interaction or overnight autonomy?
- **Data boundary**: can the target files and prompts leave the local machine?
- **Recoverability**: can Forager tell if the run is alive, stale, failed, or
  complete?
- **Evidence completeness**: can another harness resume without raw scrollback?
- **Reviewability**: can the result be inspected before knowledge or file
  changes become trusted?

The long-term goal is not to crown a single best agent. It is to let Forager
learn which harness-backed agent is appropriate for a given task while keeping
the same local approval, evidence, recovery, and review contract.
