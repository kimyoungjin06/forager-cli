# Adaptive Wiki Distillation

Distillation proposes reusable lessons from selected documents or session
transcripts. It does not establish that a lesson is correct, generally useful
or approved for future work. Keep source identity and adverse evidence visible
until review.

## Collect candidates

Choose the exact source, profile, project scope and model endpoint. The document
distiller calls that endpoint even without `--record`; dry run means no candidate
write, not no model call:

```bash
python3 scripts/offdesk_wiki_distiller.py \
  --doc /path/to/project/AGENTS.md \
  --profile <profile> --scope project --scope-ref <project-key> \
  --base-url http://<selected-endpoint>:11434 --model <installed-model> \
  --out /path/to/review/distillation.json
```

Use a selected transcript path rather than choosing the newest file by time:

```bash
python3 scripts/offdesk_wiki_session_distiller.py \
  --transcript /path/to/selected-session.jsonl \
  --profile <profile> --scope project --scope-ref <project-key> \
  --base-url http://<selected-endpoint>:11434 --model <installed-model>
```

Both tools leave candidate storage unchanged unless `--record` is supplied.
Recording writes proposals, not promotions. A new model call can produce
different proposals, so inspect the actual recorded IDs and evidence after a
recording run. Repeated collection can add occurrence evidence; do not assume
that rerunning leaves every counter and audit record unchanged.

The document distiller also offers `--mock` for an offline deterministic smoke.
That checks plumbing with synthetic output, not extraction quality.

## Understand the evidence check

The document distiller requires a source quote after whitespace normalization.
If the supplied quote is absent, it can substitute a similar source line through
fuzzy matching. Reports mark repaired quotes. This verifies a correspondence to
source text; it does not verify that the text entails the claim. A similar line
can be the wrong evidence, and a genuine quote can still support an inverted,
overgeneralized or project-inappropriate instruction.

Review the claim, instruction, quote, surrounding paragraph, source version,
scope, tags and counterexamples together. Keep rejected and uncertain cases in
the review record rather than reporting only accepted examples.

## Review before promotion

Inspect stored proposals and the deterministic reports first:

```bash
forager -p <profile> offdesk wiki candidates --json
forager -p <profile> offdesk wiki lint --json
forager -p <profile> offdesk wiki review --json
```

The prereview helper calls a configured model. `--packet` selects an output
file; it is not an offline mode. `--council` requests multiple configured model
passes and aggregates their verdicts:

```bash
python3 scripts/offdesk_wiki_prereview.py \
  --profile <profile> \
  --base-url http://<selected-endpoint>:11434 --model <installed-model> \
  --packet /path/to/review/WIKI_REVIEW.md \
  --out /path/to/review/wiki_review.json
```

Model agreement is advisory. Labels such as `supported` do not prove correct
entailment or independent review. If the project requires independent review,
select that reviewer and context explicitly, preserve its judgment, and record
the operator's decision separately.

Check at least these failure cases:

- polarity reversal or a dropped exception;
- one session producing many near-duplicate candidates;
- a paraphrase matched to the wrong source line;
- a local project rule generalized to other projects;
- unsupported claims, stale rules and unresolved counterexamples.

Promote only the exact reviewed candidate with a suitable scope and activation
mode. Use [Adaptive Wiki](adaptive-wiki.md) for the promotion, correction and
projection contracts. Scope labels and prompt fences do not sandbox a harness.

## Endpoint, cost and scheduling

The selected endpoint receives source excerpts and, for session distillation,
selected transcript content. Ollama may run on another on-premise GPU host;
"local" does not mean that data stays on the current machine. Inspect the
endpoint, supplied content and any provider policy before use. Hardware time,
energy and provider or account quotas still apply; no zero-cost promise or
model-family performance ranking is established here.

Nightly collection is an optional operator-configured recipe. This page does
not install timers, enable collection or authorize provider calls. Keep
overnight output as candidates for later review.

## Historical observations

<a id="adaptive-wiki-distillation-rules"></a>


<a id="why-compression-matters-measured"></a>
<a id="keep-high-projection-value"></a>
<a id="prune-low-value-or-harmful"></a>
<a id="compress"></a>
<a id="environment-and-compute-gotchas-highest-value-class"></a>
<a id="constraints-measured-failure-modes"></a>
<a id="classify"></a>
<a id="evidence"></a>
<a id="review-gate"></a>
<a id="continuous-distillation-with-a-local-llm"></a>
<a id="continuous-operation-nightly-recipe"></a>
<a id="review-rubric-for-the-reviewing-agent"></a>


The [original distillation notes, preserved verbatim](assets/history/20261117-adaptive-wiki-distillation.txt)
contain reported compression, latency, token and model-comparison observations.
They did not link a reproducible benchmark pack for those comparisons. Preserve
them as historical development notes; they are not current quality, cost,
privacy or lossless-compression guarantees.
