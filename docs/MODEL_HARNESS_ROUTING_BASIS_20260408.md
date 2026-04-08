# Model + Harness Routing Basis (2026-04-08)

## 1. Purpose
- Provide a planning basis for deciding:
  - which tool belongs on `on-desk`
  - which tool belongs on `off-desk`
  - which model should be used for interactive coding, synthesis, judgment, and low-cost execution
- Keep `aoe_orch_control` aligned to the already-selected product boundary:
  - `operator control plane + execution adapters`
- Avoid a false choice between:
  - "build everything ourselves"
  - "replace our control plane with an external harness"

## 2. Core Decision
1. `on-desk` should use a terminal-native coding shell.
- Default recommendation: `Claude Code`.
- Optional overlay: an `OMC`/`oh-my-openagent` style power harness on top of the chosen shell.
- Constraint: pick one primary interactive shell. Do not make multiple shells canonical at the same time.

2. `off-desk` should remain our control plane.
- Canonical truth stays in:
  - `RequestContract`
  - `ExecutionBrief`
  - `FollowupBrief`
  - `Background Run Ticket`
  - run lock / slot / scheduler / audit / dashboard state
- External tools may execute work, but they must be adapters, not a second runtime truth.

3. `LangGraph` is not the default `off-desk` answer.
- It is a low-level orchestration framework for long-running, stateful agents.
- It is useful only if we intentionally replace or rebuild our orchestration runtime.
- It is not the right default answer for operator shell, dashboard, audit, or remediation surfaces.

4. `OpenClaw` is not the default `off-desk` answer either.
- It is closer to an always-on gateway/daemon and channel integration substrate.
- It is useful if we want a persistent assistant, messaging bridge, or remote gateway.
- It is not the right canonical owner for planning truth, task/runtime truth, or execution eligibility.

5. Model routing should be split by role, not by ideology.
- Premium models should handle judgment, replanning, difficult synthesis, and final operator-facing quality gates.
- Open/local models should handle low-risk execution and repetitive batch work.
- The control plane must decide when to escalate.

## 3. Layer Placement Matrix

| Layer | Default | Optional | Not Default | Why |
|---|---|---|---|---|
| On-desk interactive coding shell | `Claude Code` | `Claude Code + OMC-style overlay` | `LangGraph`, `OpenClaw` | Terminal-native coding loop belongs here; orchestration/runtime frameworks do not |
| Off-desk canonical runtime truth | `aoe_orch_control` control plane | none | `LangGraph`, `OpenClaw`, shell overlays | briefs, tickets, audit, recovery, dashboard must stay canonical |
| Off-desk orchestration engine | current native control plane | `LangGraph` only if we explicitly rebuild orchestration internals | `OpenClaw` as orchestration core | `LangGraph` is a framework, not a drop-in shell or operator console |
| Always-on gateway / messaging bridge | current Telegram/control surfaces | `OpenClaw` | `LangGraph` | daemon/channel/gateway concerns fit OpenClaw better than orchestration frameworks |
| Local execution substrate | `local_tmux` / `local_background` adapters | third-party coding shell adapters | direct native reimplementation of every worker | executor behavior is commodity; translate it through adapters |
| Remote execution substrate | `github_runner` / `remote_worker` adapters | OpenHands-like or custom pickup worker | direct monolithic native remote harness | remote execution should remain replaceable |

## 4. Tool Recommendations

### 4.1 On-Desk
- Baseline recommendation: `Claude Code`.
- Reason:
  - official positioning is terminal-native coding work
  - plugins and GitHub integration already exist
  - it maps cleanly to synchronous operator work
- Optional augmentation:
  - `OMC`/`oh-my-openagent` style overlay for hooks, context hygiene, async specialist behavior, or batteries-included defaults
- Constraint:
  - the overlay must not become the canonical owner of task/runtime truth

### 4.2 Off-Desk
- Baseline recommendation: keep `aoe_orch_control` as the off-desk control plane.
- Use adapters for:
  - `local_tmux`
  - `github_runner`
  - `remote_worker`
  - future third-party coding shells
- Reason:
  - this is where our differentiated value already exists
  - replacing it would throw away the hard parts we already solved:
    - execution eligibility
    - remediation hints
    - recovery
    - audit
    - operator surfaces

### 4.3 LangGraph
- Use only if all of the following become true:
  1. we want to rebuild the orchestration runtime as an explicit state machine framework
  2. we need durable long-running graph semantics beyond our current runtime helpers
  3. we accept a framework integration project rather than a plug-and-play shell adoption
- Do not use it as shorthand for:
  - off-desk shell
  - dashboard
  - operator workflow product

### 4.4 OpenClaw
- Use only if we explicitly want:
  - a persistent gateway daemon
  - messaging/channel integrations
  - device-node or remote gateway behavior
- Do not use it as shorthand for:
  - execution brief truth
  - task/runtime truth
  - rerun/followup control policy

## 5. Model Routing Recommendation

| Role | Default Recommendation | Why | Promotion / Escalation |
|---|---|---|---|
| Interactive coding on-desk | `Claude Sonnet 4` or equivalent premium coding model inside the chosen on-desk shell | high-quality synchronous coding loop and fast operator iteration | escalate only when reasoning depth or final judgment quality is insufficient |
| Large-context research and synthesis | `Gemini 2.5 Pro` | strong long-context reasoning over codebases, PDFs, and mixed materials | use for multi-source synthesis, repo-wide review, or document-heavy planning |
| Off-desk replan / final judgment | `Claude Opus 4.1` or equivalent top-tier judge model | strongest reasoning tier should be reserved for hard decisions, not routine execution | call only on explicit escalation conditions |
| Low-cost local execution worker | `Qwen3-Coder` first | coding-first open model with explicit agentic coding positioning and shell compatibility | if hardware allows and tool use matters, test `gpt-oss`; if local reasoning/JSON/function-calling fit matters, test `Gemma 4` |
| Heavy local open worker | `gpt-oss-20b` or `gpt-oss-120b` depending hardware | official strong tool-use and open-weight deployment story | use only when hardware budget is real; do not assume it is “free” |
| Local reasoning-first open alternative | `Gemma 4` | official support for agentic workflows, structured JSON, and offline code | benchmark before making it the primary coding executor |

## 6. Why The Open-Model Ranking Is Not “Gemma 4 First” Yet
1. `Qwen3-Coder` is more directly positioned for agentic coding.
- Its official repo explicitly frames it as an agentic code model and highlights compatibility with coding shells.

2. `gpt-oss` has a clearer tool-use + deployment claim for heavier local workers.
- Official OpenAI positioning emphasizes tool use and quantized deployment on practical hardware sizes.

3. `Gemma 4` is still highly relevant.
- It now explicitly supports agentic workflows, structured JSON output, system instructions, and offline code assistance.
- But that does not automatically make it the first local coding executor for our stack.
- The right move is to benchmark it as an adapter target, not assume it wins by announcement alone.

## 7. Off-Desk Escalation Policy
- Use premium judge calls only when at least one of these is true:
  1. a local/open worker hits the same blocker twice
  2. tests partially pass but failure classification is unclear
  3. three or more sources must be synthesized into one decision
  4. a morning report or operator-facing conclusion needs a value gate
- Keep premium models out of the default background worker path.
- Working rule:
  - premium models decide
  - open/local models execute

## 8. Planning Implications For This Repo
1. Do not replace the current off-desk core with `LangGraph` or `OpenClaw`.
2. Continue moving execution behavior behind `ExecutorAdapter` seams.
3. Standardize one primary on-desk shell before adding overlays.
4. Pilot one local open worker through adapters before adding more orchestration products.
5. Treat `OpenClaw` and `LangGraph` as optional adjacent layers, not as the new product boundary.

## 9. Recommended Adoption Sequence
1. Standardize `on-desk` on `Claude Code` as the primary shell.
2. Keep `aoe_orch_control` as `off-desk` truth.
3. Add one open/local worker pilot through the adapter seam.
- first candidate: `Qwen3-Coder`
- second candidate: `gpt-oss`
- third candidate: `Gemma 4`
4. Revisit `OpenClaw` only if we need a stronger always-on gateway or channel hub.
5. Revisit `LangGraph` only if we intentionally decide to re-platform orchestration internals.
6. Keep endpoint binding modular through:
   - `docs/MODEL_ENDPOINT_ADAPTER_SPEC.md`

## 10. Anti-Patterns To Avoid
- `on-desk = Claude Code + OMC + another shell` as simultaneous primaries
- `off-desk = LangGraph` as a reflex without a re-platform decision
- `off-desk = OpenClaw` as a replacement for task/runtime truth
- “free model” language that ignores compute, memory, and operator overhead
- putting premium models in the always-on worker lane by default

## 11. References
- `SRC-CC-1` Claude Code official repo
  - https://github.com/anthropics/claude-code
  - reviewed 2026-04-08
- `SRC-CCA-1` Claude Code Action official repo
  - https://github.com/anthropics/claude-code-action
  - reviewed 2026-04-08
- `SRC-LG-1` LangGraph official overview
  - https://docs.langchain.com/oss/python/langgraph/overview
  - reviewed 2026-04-08
- `SRC-OCL-1` OpenClaw official repo
  - https://github.com/openclaw/openclaw
  - reviewed 2026-04-08
- `SRC-GHCA-1` GitHub Copilot cloud agent docs
  - https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent
  - reviewed 2026-04-08
- `SRC-OH-1` OpenHands introduction
  - https://docs.openhands.dev/overview/introduction
  - reviewed 2026-04-08
- `SRC-OMC-1` oh-my-openagent official repo
  - https://github.com/code-yeongyu/oh-my-openagent
  - reviewed 2026-04-08
- `SRC-OC-1` OpenCode modes docs
  - https://open-code.ai/docs/en/modes
  - reviewed 2026-04-08
- `SRC-GEM-1` Gemini model docs
  - https://ai.google.dev/gemini-api/docs/models
  - reviewed 2026-04-08
- `SRC-GEM-2` Gemini 2.5 Pro official blog post
  - https://blog.google/technology/google-deepmind/gemini-model-thinking-updates-march-2025/
  - reviewed 2026-04-08
- `SRC-ANT-1` Anthropic models overview
  - https://docs.anthropic.com/en/docs/about-claude/models/all-models
  - reviewed 2026-04-08
- `SRC-GPTOSS-1` OpenAI gpt-oss announcement
  - https://openai.com/index/introducing-gpt-oss/
  - reviewed 2026-04-08
- `SRC-QW-1` Qwen3-Coder official repo
  - https://github.com/QwenLM/Qwen3-Coder
  - reviewed 2026-04-08
- `SRC-GO-1` Gemma 4 official announcement
  - https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/
  - reviewed 2026-04-08
