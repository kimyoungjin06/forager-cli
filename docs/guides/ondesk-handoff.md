# Ondesk Handoff Guide

Ondesk work is usually driven by the harness you are already using, such as
Codex or Claude Code. In that mode Forager should not become the primary agent
loop. Its job is to keep a safe bridge between live harness work, project
notes, and later Offdesk or wiki review.

This guide covers the Ondesk side of the full
[Operation Cycle](operation-cycle.md). The main rule is that a fresh harness
should start from a compact package, not from a hidden raw resume.

## Core Loop

1. Record operator intent while the work is fresh.

```bash
forager ondesk note --project-key twinpaper --mode writing \
  --text "Draft focuses on evidence chain before novelty claims."
```

2. Capture the current harness context when you want cross-review or handoff.

```bash
forager ondesk capture codex-harness --project-key twinpaper --mode writing --lines 250
```

This writes an append-only capture under the active profile:

```text
ondesk_captures/<timestamp>_<capture-id>/
  capture.json
  PROMPT_CONTEXT.md
```

3. Give the generated prompt package to another harness.

```bash
forager ondesk prompt-package --capture-id ondesk-cap-12345678
```

The package is context, not proof of completion. The next harness should still
separate observation from inference, ask for missing evidence, and propose wiki
changes as candidates.

When a matching project initialization exists in the active profile,
`prompt-package` includes the latest `ONDESK_START_PACKAGE.md`, operation
targets, readiness state, and a concise `MODULE_OPERATION_PREFLIGHT.json`
summary for the same `project_key`. The preflight bridge exposes module
readiness, blockers, and command purposes, not raw command strings. This is the
preferred bridge from `forager project init` into a fresh Ondesk harness.

When a matching Offdesk closeout exists, `prompt-package` also includes the
latest `RETURN_PACKAGE.md` and closeout-review verdict for the same
`project_key`. This is the preferred bridge from overnight Offdesk work back
into a fresh Ondesk session.

## Knowledge Policy

- `ondesk note` stores redacted, operator-safe JSONL in `ondesk_notes.jsonl`.
- `ondesk capture` records tmux scrollback only when the session is running.
- `--include-git` is read-only and captures `git status --short` plus
  `git diff --stat`; it does not run tests, clean files, or mutate worktrees.
- Ondesk commands do not promote adaptive wiki entries. They prepare candidate
  material for a later review stage.
- Secrets and runner-only context are redacted before durable note or capture
  artifacts are written.

## When To Use It

Use Ondesk handoff when:

- two live harnesses should cross-review the same project state;
- a long discussion needs to become a compact prompt for the next harness;
- a non-code writing, analysis, critique, or planning task should produce
  durable review material;
- a future Offdesk episode needs current human intent without inheriting raw
  chat logs.

Use Offdesk tasks instead when Forager should own the execution, approvals,
recovery records, and morning-review evidence.

## Handoff Checklist

Before switching from Ondesk to Offdesk:

- record the current objective and known non-goals with `ondesk note`;
- capture only the harness context that the next reviewer needs;
- make the target project and module explicit through `project_key` and, when
  available, a project initialization packet;
- state forbidden operations such as deletion, cleanup, service restart,
  package install, provider retargeting, and wiki promotion;
- describe the expected evidence artifacts, not only the desired conclusion.

Before returning from Offdesk to Ondesk:

- read `result.json`, `REPORT.md`, and post-run review artifacts;
- run or inspect Offdesk closeout;
- start the next harness from `RETURN_PACKAGE.md` or
  `forager ondesk prompt-package --project-key <project>`;
- promote wiki changes only after review, not just because an Offdesk run
  generated candidate knowledge.

## Morning Telegram Handoff

For long overnight work, send a compact Telegram handoff around 08:30 KST and
use WebUI as the review surface. Telegram should answer only: should the
operator start Ondesk review now, keep it pending, or defer with a natural
language condition. It should not approve cleanup, wiki promotion, provider
retargeting, file movement, or deletion.

Build the request from closeout and prompt-package artifacts:

```bash
scripts/build_ondesk_handoff_request.py \
  --project-key twinpaper \
  --closeout-artifact-dir "$CLOSEOUT_DIR" \
  --prompt-package "$ONDESK_PROMPT_PACKAGE" \
  --webui-url "$FORAGER_WEBUI_URL" \
  --out "$HANDOFF_REQUEST_JSON"
```

Then pass that request through the existing Telegram relay:

```bash
scripts/offdesk_telegram_decision_relay.py \
  --request "$HANDOFF_REQUEST_JSON" \
  --out "$HANDOFF_RESULT_JSON"
```

The rendered message hides raw paths and ids. Those remain in the request,
state, and result JSON for audit/debugging. The relay writes the state beside
the result as `<result-stem>.telegram_decision_state.json`, so simultaneous
handoff and council prompts in the same directory do not overwrite each other's
state artifacts.
