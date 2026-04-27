# PRESET_COMPLETION_MATRIX

## 1. Purpose
- This document defines the completion contract for each `Task Team` preset.
- It exists so that `done`, `rerun`, and `manual followup` are driven by an explicit matrix rather than ad hoc live judgment.
- Live validation proves the matrix; it does not replace it.

## 2. Shared Rules
- Every preset must satisfy:
  - required evidence exists and is inspectable
  - the expected execution/review role shape is preserved
  - critic/reviewer expectations are either satisfied or escalated explicitly
- `done` means:
  - the primary output exists
  - required evidence exists
  - unresolved risk is cleared or surfaced
- `rerun` means:
  - the result is incomplete, internally inconsistent, or lane-specific retry is likely to help
- `manual followup` means:
  - the result is blocked on operator judgment, missing external context, or non-automatable cleanup

## 3. Matrix
| Preset | Primary Output | Required Evidence | Expected Execution Shape | Expected Review Shape | Default Critic Focus | Common Rerun Trigger | Common Manual Followup Trigger |
|---|---|---|---|---|---|---|---|
| `writer` | draft/report/handoff/spec text | changed files, output excerpt, source references, open issue list | writer roles | reviewer pair | clarity, completeness, source grounding, unresolved claims | missing sections, weak grounding, malformed deliverable | ambiguity in target audience, policy-sensitive wording, missing source of truth |
| `analysis` | analytical conclusion or ranked findings | evidence trail, metrics/table, assumptions, uncertainty note | analyst roles | reviewer pair | evidence quality, reasoning coherence, missing caveats | unsupported conclusion, weak evidence join, missing caveats | decision depends on operator preference or external interpretation |
| `build` | code/config/integration change | diff summary, test results, failure notes, impacted components | build/dev roles | reviewer pair | implementation delta, tests, integration risk | failing tests, incomplete patch, broken integration edge | env-specific issue, secret/deploy dependency, risky mutation approval |
| `data` | transformed dataset/query/report output | schema check, null/outlier summary, sample output, transform note | data roles | reviewer pair | schema correctness, null handling, transform integrity | schema drift, invalid/null-heavy output, broken pipeline step | source data quality decision, business rule ambiguity |
| `review` | review verdict, critique, or regression assessment | findings list, affected scope, severity rationale, unresolved questions | minimal execution or reviewer-led | reviewer pair | risk detection, regression coverage, missing evidence | shallow review, unsupported verdict, missed required scope | operator must decide acceptance threshold or risk tradeoff |
| `mixed` | work result plus accompanying documentation/review output | primary work evidence plus handoff/review evidence | work roles plus writer where needed | reviewer pair | execution/review split integrity, handoff quality, unresolved coupling | work lane incomplete, handoff drift, review lane mismatch | operator must arbitrate between competing outputs or decide packaging/scope |
| `general` | fallback mixed work result | task-specific evidence minimum plus summary | general work roles | reviewer pair | basic completion and risk surfacing | unclear or partial result | unclear scope or missing task framing |

## 4. Preset Notes
### 4.1 Writer
- Prefer `Codex-Writer` / `Claude-Writer` execution and reviewer pair review.
- Completion requires a deliverable that is readable by the intended human consumer without reconstructing intent from logs.

### 4.2 Analysis
- Completion requires explicit caveats, not just a conclusion.
- Evidence quality is part of the output, not a hidden implementation detail.

### 4.3 Build
- A build preset is not complete without test or verification evidence, even when the change itself looks correct.
- Integration risk must be visible to the operator.

### 4.4 Data
- Null/schema evidence is mandatory.
- Sample outputs are required unless the data itself is too sensitive to surface.

### 4.5 Review
- Review presets may end in "no change required", but only if the review artifact itself is complete.

### 4.6 Mixed
- Mixed presets must preserve the work/review split.
- Reviewer drift into execution lanes is a contract violation, not just a naming issue.

## 5. Immediate Follow-up
- Wire this matrix into:
  - dashboard task detail rendering
  - recovery summary wording
  - preset-specific live Phase2 validation plans
