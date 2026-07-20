# Adaptive Wiki Distillation Rules

How to turn project documents into durable wiki knowledge. These rules govern
both authoring (`forager offdesk wiki record-candidate`) and review (an operator
or a separate reviewing agent deciding promote, compress, rescope, or reject).

The wiki is not a mirror of the docs. The source docs stay the source of truth.
The wiki carries the compact, non-obvious, projectable knowledge an agent should
hold in context so it does not repeat a known mistake. If an item is already a
stable line in an authored doc and adds nothing when injected as an instruction,
it does not belong in the wiki.

## Why compression matters (measured)

The AI projection is budgeted: roughly 8 entries, ~4000 estimated context
characters, and ~500 characters per instruction. Distillation is not cosmetic.
An A/B test on the TwinPaper wiki (12 verbose entries vs 10 distilled) cut
injected context by 18 percent in analysis mode and 40 percent in development
mode, with the same scope discipline and no loss of the non-obvious knowledge.
The verbose version was already at the 8-entry budget cap; the distilled version
left headroom. Compression is what keeps the most important knowledge in budget
as the wiki grows.

## Keep (high projection value)

- Non-obvious gotchas that prevent a real mistake ("cohort denominator is 822,
  840 is legacy only"; "figure outputs_svg must be []").
- Authority and boundary rules an agent could plausibly violate ("the frontend
  must not recompute ranking in the browser"; "approval before runtime mutation").
- Durable domain facts an agent needs in context (outcome variables, canonical
  data sources and IDs, analysis window).
- Methodology rules that shape correct work (pair fixed effects with within-pair
  variables only; start a direction review from the latest baseline).

## Prune (low value or harmful)

- Doc mirrors: near-verbatim restatements of a stable source-of-truth doc. The
  doc already carries it, and the wiki copy goes stale when the doc changes.
- Generic best practice that is already universal ("run tests before finishing",
  "state over scrollback") unless it is project-specific and non-obvious.
- Operational trivia unlikely to change the agent's task (specific legacy-path
  blocklists), unless the risk of getting it wrong is high.
- Anything better served by a link to the doc than by a projected instruction.

## Compress

- `claim`: one durable statement, roughly <= 120 characters. Drop throat-clearing
  ("TwinPaper work must ..."), the project name (scope already carries it),
  hedges, and narrative.
- `ai_instruction`: imperative and actionable, roughly <= 200 characters. Say
  what to do or not do; do not restate the claim.
- Prefer one precise sentence over a paragraph. If it needs a paragraph, it is
  probably two entries or belongs in the doc.

## Classify

- `kind`: preference, procedure, failure_pattern, policy_rule, or fact. Pick the
  one that matches how the knowledge is used, not its tone.
- `scope`: project (project-specific), user_global (cross-project), or
  artifact_kind. Scope drives runtime projection matching.
- `facet` (tag `facet/<x>`): research or product (the substance) vs ops (how to
  run and operate). A research project skews research; a software app skews
  product; the tool's own profile is all ops.
- tags: `domain/<project>` plus a topical tag when useful (`risk/...`,
  `method/...`, `harness/<area>`).

## Evidence

- Every entry cites a verifiable source: `doc:<path> (Section Name)`. Do not
  invent anchors that do not resolve.
- Prefer one or two precise refs over many vague ones.

## Review gate

Authoring never self-promotes. Candidates are authored with these rules, then a
separate reviewer (the operator, or a reviewing agent) decides per candidate:
promote (with activation mode and scope), compress, rescope, merge, or reject.
This keeps observation separate from the decision to trust knowledge, and it is
where promotion precision is actually validated.

Apply the verdicts in place, without reject and re-record:

- `compress` -> `forager offdesk wiki edit <id> --claim <shorter> [--ai-instruction <text>]`
- evidence fix -> `forager offdesk wiki edit <id> --evidence-ref <ref>`
- retag / classify -> `forager offdesk wiki add-tag <id> --core-tag facet/<x>`
- `rescope` -> `forager offdesk wiki rescope <id> --scope <scope> --scope-ref <ref>`
- `reject` -> `forager offdesk wiki reject <id> --reason <text>`
- `promote` -> `forager offdesk wiki promote <id> --activation-mode <mode>`

Each mutation appends an audit record, so the review decision stays traceable.

## Review rubric (for the reviewing agent)

For each candidate, decide a verdict and give a one-line reason:

1. Durable and non-obvious? (If it is a doc mirror or generic, lean reject.)
2. Accurate to the cited source?
3. Right kind, scope, and facet?
4. Is the claim tight and the instruction actionable? (If long, verdict
   `compress` with a proposed shorter claim.)
5. Does the evidence ref resolve?

Verdicts: `promote:<activation>` | `compress` | `rescope:<scope>` | `merge:<id>`
| `reject`, each with a short reason.
