# Adaptive Wiki Tag Graph

Forager's adaptive wiki tag graph is a read-only navigation and review surface
over the canonical adaptive wiki JSON. Tags help operators and agents discover
related knowledge, but they are not a separate source of truth and do not grant
runtime authority.

## Policy

Use a hybrid tag model:

- `core_tags` are controlled tags. They may be used by graph export, retrieval,
  review queues, and future routing experiments.
- `proposed_tags` are open suggestions. Agents and reviewers may add them while
  exploring new concepts, but they should not be treated as canonical routing
  signals until reviewed.
- Derived core tags are generated from canonical fields such as kind, scope,
  status, confidence, agent mode, capability id, and required artifact kind.

The graph builder keeps these classes separate. `core_tags` and derived core
tags become graph edges immediately. `proposed_tags` also appear in the graph,
but they are reviewable suggestions, not stable policy.

## Core Registry

Core tags use small typed prefixes:

| Prefix | Use |
| --- | --- |
| `project/` | Project or repository key. |
| `agent/` | Agent mode or role lens. |
| `mode/` | Execution mode, for example `offdesk`. |
| `artifact/` | Artifact class such as `report` or `evidence`. |
| `evidence/` | Evidence source, gate, or review class. |
| `risk/` | Operator or runtime risk class. |
| `status/` | Lifecycle or reportability status. |
| `capability/` | Offdesk capability id. |
| `kind/` | Derived adaptive wiki kind. |
| `scope/` | Derived adaptive wiki scope. |
| `confidence/` | Derived confidence level. |
| `signal/` | Derived candidate signal kind. |

Core tags should be lowercase and reusable. Prefer `#risk/operator-denial` over
one-off tags like `#denied-by-user-at-20260520`.

## Review Criteria

A proposed tag should be promoted or normalized only when it passes these
checks:

- It cannot be expressed by an existing core tag.
- It is likely to apply to more than one wiki entry, evidence artifact, or
  review episode.
- It improves retrieval, graph navigation, critique, or handoff quality.
- It is not too broad, such as `#important`, `#research`, or `#todo`.
- It is not too narrow, such as a single timestamped iteration id.
- It has a clear typed prefix or should be mapped to one.

Reviewer agents should prefer normalization over expansion. For example,
`#비평` and `#critic` should normalize to `#agent/critique`, while `#review`
should normalize to `#agent/review` when it refers to the separate Offdesk
checkpoint-review mode.

## Export

Generate a read-only graph report:

```bash
forager offdesk wiki graph --json
forager offdesk wiki graph --output /tmp/forager-wiki-graph
```

With `--output`, Forager writes:

- `graph.json`: machine-readable nodes, edges, registry, and review issues.
- `graph.md`: human-readable summary, registry, review issues, and tag edges.

This command does not mutate wiki entries, candidates, approvals, tasks,
runtime context, provider routing, model routing, or launch specs.

## Runtime Boundary

The graph is advisory. It must not override approval gates, command/workdir
safety, provider/model routing, or the promoted-entry projection rules. Future
retrieval or routing work should use graph data only after preserving the same
operator review and safety boundaries that apply to adaptive wiki projection.
