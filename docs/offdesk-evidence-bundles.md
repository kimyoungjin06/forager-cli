# Offdesk Evidence Bundles

Offdesk agents should not infer current project state from scattered snippets.
Before analysis, writing, critique, or long autonomous work, a deterministic
collector should create an evidence bundle. The bundle is read-only evidence,
not execution authority.

## Contract

An evidence bundle is a pair of files:

- `evidence_bundle.json`: machine-readable source inventory, excerpts,
  artifact summaries, and derived evidence-state labels.
- `EVIDENCE.md`: operator-readable summary of the same material.

A separate reviewer may add:

- `evidence_review.json`: sufficient/insufficient/conflicting decision with
  missing evidence and warnings.
- `EVIDENCE_REVIEW.md`: operator-readable review summary.
- `*.marp.md`: optional Marp review deck generated from a JSON artifact for
  morning review, closeout handoff, or incident triage.

The collector must be deterministic and read-only for the target project. It
may write only to the selected Offdesk output directory. It must not call an
LLM, approve actions, mutate wiki state, change provider/model routing, or run
project experiments.

## Evidence Roles

| Role | Responsibility | Mutation |
|---|---|---|
| Collector | Find and package current evidence. | Writes bundle files only. |
| Reviewer | Decide whether the bundle is sufficient for a target claim/task. | Writes review files only. |
| Mode agent | Plan, analyze, write, critique, develop, or maintain from the bundle. | Depends on mode/capability. |

The collector does not decide the final answer. The reviewer does not execute
the plan. Mode agents must treat facts outside the bundle as unverified unless
they gather additional evidence and record it.

## Marp Review Decks

Marp decks are a presentation surface for already-written Offdesk artifacts.
They are not a control plane, approval channel, or source of truth. The source
JSON remains authoritative.

Generate a deck without requiring Marp CLI:

```bash
forager offdesk deck --from closeout_plan.json --out closeout.marp.md
```

Render only when Marp CLI is available in the runtime:

```bash
forager offdesk deck --from closeout_plan.json --out closeout.marp.md --render pdf --marp-bin marp --force
```

Use decks for compact human review: closeout summaries, plan readiness, runtime
status, and incident packets. Do not use decks to authorize work, mutate project
state, or bypass the original JSON artifact and linked evidence.

## TwinPaper V1

The first concrete bundle targets TwinPaper Module03 direction review quality.
It collects:

- `AGENTS.md` direction-review rules;
- `docs/operations/RunLog.md` recent tail entries;
- targeted RunLog excerpts for `no-option`, `singlex`, `openexplore`,
  `direction-review`, `validated_candidate`, `p/q`, `restart_stability`, and
  `primary_objective_gate`;
- latest metadata artifacts matching direction review, paired summaries, and
  no-option/singlex/openexplore run summaries;
- Module03 command and code/test entrypoint existence;
- a compact current-state label such as `executed_primary_gate_failed`.

The current-state label is not a research conclusion. It is a routing aid for
the next agent. For example, `executed_primary_gate_failed` means the bundle
found baseline execution evidence and a failed promotion/primary objective gate,
so a writing or critique agent should not claim that no baseline execution
evidence exists.

## Review Decisions

`evidence_review.json` uses one decision:

- `sufficient`: enough current evidence exists for the requested Offdesk task.
- `insufficient`: required evidence is missing or stale.
- `conflicting`: bundle sources disagree in a way that must be resolved first.
- `needs_operator`: evidence is present but the next step depends on an
  operator decision.

Offdesk workload preparation may block enqueue when an evidence review is not
`sufficient`, unless the operator explicitly allows preflight blockers.

## Safety Boundary

Evidence bundles can improve context quality, but they cannot grant authority.
They must not rewrite commands, workdirs, launch specs, approvals, provider
selection, or wiki projection. Long-running runtime still follows the normal
`dispatch.runtime` approval and runner safety rails.
