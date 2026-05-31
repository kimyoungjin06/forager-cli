# Hosted Harness Agents

Forager's product boundary is not the agent loop itself. A hosted harness agent
is an agent built by another harness and launched under Forager's local
approval, evidence, recovery, and review contract.

Examples include Claude Code, Codex, OpenCode, OpenHands, Aider, SWE-agent,
Gemini CLI, Mistral Vibe, deterministic review scripts, and local LLM harnesses.
Forager should be able to supervise these workers without pretending that they
share the same prompts, tool policies, model routing, or internal memory.

## Boundary

The hosted harness owns:

- the agent loop;
- prompts and system instructions;
- model calls and provider routing;
- tool policy inside the harness;
- intermediate reasoning and interaction style.

Forager owns:

- task intent and scope;
- approval gates;
- launch command and working directory;
- mutation boundary;
- runtime handle and liveness evidence;
- heartbeat, progress, logs, and result artifacts;
- recovery decision and closeout state;
- review package and next-harness handoff;
- adaptive wiki candidate capture and reviewed promotion.

Completed execution does not mean accepted truth. A hosted agent may report that
it is done, but Forager should treat the work as accepted only after evidence,
risk, and next-action surfaces are reviewable.

## Minimum Hosted Harness Contract

Each hosted harness agent profile should define:

| Field | Purpose |
| --- | --- |
| Launch command | Exact command Forager may run after approval |
| Working directory | Repo, worktree, or scratch path used by the agent |
| Mutation scope | What files, branches, network calls, or side effects are allowed |
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

The v1 support target is intentionally narrow:

| Harness | Status | Reason |
| --- | --- | --- |
| Codex CLI | supported | Primary current golden-loop harness. |
| Claude Code | supported | Primary current golden-loop harness alongside Codex. |
| Gemini CLI | planned | Registry exists, but the hosted harness evidence contract still needs a disposable smoke task. |
| OpenHands | planned | Future integration candidate. |
| Aider | planned | Future integration candidate. |

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
