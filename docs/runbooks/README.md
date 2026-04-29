# Runbooks

This directory is the durable destination for operator runbooks promoted from repeated recovery patterns.

Generated learned recovery candidates come from:

- `.aoe-team/dashboard/action-history.jsonl`
- `.aoe-team/recovery/nightly-session-summary/*.json`

Use:

```bash
python3 scripts/gateway/aoe_tg_learned_runbook.py --project-root .
python3 scripts/gateway/aoe_tg_learned_runbook.py --project-root . --json
python3 scripts/gateway/aoe_tg_learned_runbook.py --project-root . --write-doc
```

The extractor only promotes repeated non-benign `reason_code + remediation + next_step` patterns that meet the `--min-count` threshold.
