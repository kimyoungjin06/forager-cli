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

## First Live Run
- Trigger comment: PR `#112`, issue comment `4335458073`.
- Comment bridge run: `25054207593`.
- Worker run: `25054213583`.
- Worker job result: success.
- Imported sidecars:
  - `background_run_acks/github-runner-bgt-gha-live-20260428-001.json`
  - `background_run_results/github-runner-bgt-gha-live-20260428-001.json`
  - `background_run_logs/github-runner-bgt-gha-live-20260428-001.log`
- Local import command:
  - `scripts/gateway/aoe-external-sidecar-sync.py auto-import-github-artifact --team-dir .aoe-team --ticket-id BGT-GHA-LIVE-20260428-001 --runner github_runner --repo kimyoungjin06/aoe_orch_control --workflow external-background-worker.yml --list-limit 20 --timeout-sec 120 --interval-sec 5 --poll`
- Import result:
  - `copied_count=3`
  - `ack_imported=true`
  - `result_status=completed`
  - `poll_result.completed_ticket_ids=["BGT-GHA-LIVE-20260428-001"]`

## Finding
- `gh issue comment` failed from both the comment bridge response step and the worker completion step with `GraphQL: Resource not accessible by integration (addComment)`.
- The worker pickup/import/poll path still succeeded, but callback comment failure marked the Actions runs as failed.
- Follow-up hardening:
  - grant `pull-requests: write` to callback jobs
  - write callback bodies to `$GITHUB_STEP_SUMMARY`
  - make callback comment steps `continue-on-error`

## Hardened Rerun
- Trigger comment: PR `#112`, issue comment `4335511135`.
- Comment bridge run: `25054585964`.
- Worker run: `25054592355`.
- Comment bridge result: success.
- Worker workflow result: success.
- GitHub callback comments:
  - dispatch accepted comment: `4335512441`
  - worker completion comment: `4335514800`
- Fresh local import result from `/tmp/aoe-comment-smoke-second`:
  - `run_id=25054592355`
  - `run.conclusion=success`
  - `copied_count=3`
  - `ack_imported=true`
  - `result_status=completed`
  - `poll_result.completed_ticket_ids=["BGT-GHA-LIVE-20260428-001"]`

## Final Outcome
- Trusted PR comment dispatch worked.
- GitHub Actions worker pickup worked.
- Ack/result/log artifact upload worked.
- Ticket-named run discovery worked.
- Local scheduled import + drain + poll worked.
- Completion callback now posts successfully for this PR-thread path.

## Cleanup
- Remove `.aoe-team/background_runs.json`.
- Remove `.aoe-team/background_run_handoffs/github-runner-bgt-gha-live-20260428-001.json`.
- Keep this report as the evidence pointer for the smoke run outcome.
