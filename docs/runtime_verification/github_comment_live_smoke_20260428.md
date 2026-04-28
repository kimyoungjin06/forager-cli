# GitHub Comment Flow Live Smoke - 2026-04-28

## Scope
- Verify the trusted issue-comment path dispatches `external-background-worker.yml`.
- Verify `github_runner` worker pickup writes ack/result/log sidecars.
- Verify local `auto-import-github-artifact --poll` can import the sidecars and advance the ticket.

## Temporary Seed
- Ticket: `BGT-GHA-LIVE-20260428-001`
- Runner: `github_runner`
- Launch mode: `comment_flow_live_smoke`
- Command: bounded Python no-op that writes `tmp/github_comment_live_smoke.txt` in the GitHub Actions checkout and exits 0.

## Cleanup
- Remove `.aoe-team/background_runs.json`.
- Remove `.aoe-team/background_run_handoffs/github-runner-bgt-gha-live-20260428-001.json`.
- Keep this report as the evidence pointer for the smoke run outcome.
