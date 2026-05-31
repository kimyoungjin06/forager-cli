# Command-Line Help for `forager`

This document contains the help content for the `forager` command-line program.

**Command Overview:**

* [`forager`↴](#forager)
* [`forager add`↴](#forager-add)
* [`forager init`↴](#forager-init)
* [`forager list`↴](#forager-list)
* [`forager remove`↴](#forager-remove)
* [`forager status`↴](#forager-status)
* [`forager doctor`↴](#forager-doctor)
* [`forager migrate`↴](#forager-migrate)
* [`forager migrate aoe`↴](#forager-migrate-aoe)
* [`forager session`↴](#forager-session)
* [`forager session start`↴](#forager-session-start)
* [`forager session stop`↴](#forager-session-stop)
* [`forager session restart`↴](#forager-session-restart)
* [`forager session attach`↴](#forager-session-attach)
* [`forager session show`↴](#forager-session-show)
* [`forager session rename`↴](#forager-session-rename)
* [`forager session current`↴](#forager-session-current)
* [`forager group`↴](#forager-group)
* [`forager group list`↴](#forager-group-list)
* [`forager group create`↴](#forager-group-create)
* [`forager group delete`↴](#forager-group-delete)
* [`forager group move`↴](#forager-group-move)
* [`forager profile`↴](#forager-profile)
* [`forager profile list`↴](#forager-profile-list)
* [`forager profile create`↴](#forager-profile-create)
* [`forager profile delete`↴](#forager-profile-delete)
* [`forager profile default`↴](#forager-profile-default)
* [`forager project`↴](#forager-project)
* [`forager project init`↴](#forager-project-init)
* [`forager project apply-governance-hints`↴](#forager-project-apply-governance-hints)
* [`forager project audit-docs`↴](#forager-project-audit-docs)
* [`forager worktree`↴](#forager-worktree)
* [`forager worktree list`↴](#forager-worktree-list)
* [`forager worktree info`↴](#forager-worktree-info)
* [`forager worktree cleanup`↴](#forager-worktree-cleanup)
* [`forager offdesk`↴](#forager-offdesk)
* [`forager offdesk pending`↴](#forager-offdesk-pending)
* [`forager offdesk gate`↴](#forager-offdesk-gate)
* [`forager offdesk launch`↴](#forager-offdesk-launch)
* [`forager offdesk enqueue`↴](#forager-offdesk-enqueue)
* [`forager offdesk tick`↴](#forager-offdesk-tick)
* [`forager offdesk tasks`↴](#forager-offdesk-tasks)
* [`forager offdesk provider-capacity`↴](#forager-offdesk-provider-capacity)
* [`forager offdesk provider-fallback`↴](#forager-offdesk-provider-fallback)
* [`forager offdesk cancel-task`↴](#forager-offdesk-cancel-task)
* [`forager offdesk retry-task`↴](#forager-offdesk-retry-task)
* [`forager offdesk resume-task`↴](#forager-offdesk-resume-task)
* [`forager offdesk abandon-task`↴](#forager-offdesk-abandon-task)
* [`forager offdesk poll`↴](#forager-offdesk-poll)
* [`forager offdesk ok`↴](#forager-offdesk-ok)
* [`forager offdesk cancel`↴](#forager-offdesk-cancel)
* [`forager offdesk resume`↴](#forager-offdesk-resume)
* [`forager offdesk background`↴](#forager-offdesk-background)
* [`forager offdesk capabilities`↴](#forager-offdesk-capabilities)
* [`forager offdesk snapshots`↴](#forager-offdesk-snapshots)
* [`forager offdesk snapshot`↴](#forager-offdesk-snapshot)
* [`forager offdesk restore-plan`↴](#forager-offdesk-restore-plan)
* [`forager offdesk debug-bundle`↴](#forager-offdesk-debug-bundle)
* [`forager offdesk maintenance-report`↴](#forager-offdesk-maintenance-report)
* [`forager offdesk maintenance-request`↴](#forager-offdesk-maintenance-request)
* [`forager offdesk closeout`↴](#forager-offdesk-closeout)
* [`forager offdesk closeout-review`↴](#forager-offdesk-closeout-review)
* [`forager offdesk wiki`↴](#forager-offdesk-wiki)
* [`forager offdesk wiki corrections`↴](#forager-offdesk-wiki-corrections)
* [`forager offdesk wiki proposal-events`↴](#forager-offdesk-wiki-proposal-events)
* [`forager offdesk wiki record-proposal-event`↴](#forager-offdesk-wiki-record-proposal-event)
* [`forager offdesk wiki accept-proposal`↴](#forager-offdesk-wiki-accept-proposal)
* [`forager offdesk wiki reject-proposal`↴](#forager-offdesk-wiki-reject-proposal)
* [`forager offdesk wiki supersede-proposal`↴](#forager-offdesk-wiki-supersede-proposal)
* [`forager offdesk wiki proposal-handoff`↴](#forager-offdesk-wiki-proposal-handoff)
* [`forager offdesk wiki proposal-receipt`↴](#forager-offdesk-wiki-proposal-receipt)
* [`forager offdesk wiki candidates`↴](#forager-offdesk-wiki-candidates)
* [`forager offdesk wiki entries`↴](#forager-offdesk-wiki-entries)
* [`forager offdesk wiki show`↴](#forager-offdesk-wiki-show)
* [`forager offdesk wiki projection`↴](#forager-offdesk-wiki-projection)
* [`forager offdesk wiki runtime-policy-acks`↴](#forager-offdesk-wiki-runtime-policy-acks)
* [`forager offdesk wiki runtime-policy-ack-report`↴](#forager-offdesk-wiki-runtime-policy-ack-report)
* [`forager offdesk wiki review-after-report`↴](#forager-offdesk-wiki-review-after-report)
* [`forager offdesk wiki ack-runtime-policy`↴](#forager-offdesk-wiki-ack-runtime-policy)
* [`forager offdesk wiki lint`↴](#forager-offdesk-wiki-lint)
* [`forager offdesk wiki export-markdown`↴](#forager-offdesk-wiki-export-markdown)
* [`forager offdesk wiki graph`↴](#forager-offdesk-wiki-graph)
* [`forager offdesk wiki review`↴](#forager-offdesk-wiki-review)
* [`forager offdesk wiki evaluate-episode`↴](#forager-offdesk-wiki-evaluate-episode)
* [`forager offdesk wiki episode-trace`↴](#forager-offdesk-wiki-episode-trace)
* [`forager offdesk wiki evaluate-recurrence`↴](#forager-offdesk-wiki-evaluate-recurrence)
* [`forager offdesk wiki promotion-chain`↴](#forager-offdesk-wiki-promotion-chain)
* [`forager offdesk wiki promote`↴](#forager-offdesk-wiki-promote)
* [`forager offdesk wiki reject`↴](#forager-offdesk-wiki-reject)
* [`forager offdesk wiki rescope`↴](#forager-offdesk-wiki-rescope)
* [`forager offdesk wiki deprecate`↴](#forager-offdesk-wiki-deprecate)
* [`forager offdesk wiki renew-review-after`↴](#forager-offdesk-wiki-renew-review-after)
* [`forager offdesk wiki add-counterexample`↴](#forager-offdesk-wiki-add-counterexample)
* [`forager offdesk wiki update-runbook`↴](#forager-offdesk-wiki-update-runbook)
* [`forager ondesk`↴](#forager-ondesk)
* [`forager ondesk note`↴](#forager-ondesk-note)
* [`forager ondesk capture`↴](#forager-ondesk-capture)
* [`forager ondesk prompt-package`↴](#forager-ondesk-prompt-package)
* [`forager tmux`↴](#forager-tmux)
* [`forager tmux status`↴](#forager-tmux-status)
* [`forager sounds`↴](#forager-sounds)
* [`forager sounds install`↴](#forager-sounds-install)
* [`forager sounds list`↴](#forager-sounds-list)
* [`forager sounds test`↴](#forager-sounds-test)
* [`forager uninstall`↴](#forager-uninstall)
* [`forager completion`↴](#forager-completion)

## `forager`

Forager is an offdesk agent orchestration tool that uses tmux to help you manage, monitor, approve, and recover AI coding agent work.

Run without arguments to launch the TUI dashboard. The legacy `aoe` binary remains available as a compatibility alias and warns on human-facing commands.

**Usage:** `forager [OPTIONS] [COMMAND]`

###### **Subcommands:**

* `add` — Add a new session
* `init` — Initialize .forager/config.toml in a repository
* `list` — List all sessions
* `remove` — Remove a session
* `status` — Show session status summary
* `doctor` — Diagnose Forager paths, profile env, and legacy AoE compatibility state
* `migrate` — Migrate legacy AoE compatibility paths
* `session` — Manage session lifecycle (start, stop, attach, etc.)
* `group` — Manage groups for organizing sessions
* `profile` — Manage profiles (separate workspaces)
* `project` — Initialize and inspect project operation packets
* `worktree` — Manage git worktrees for parallel development
* `offdesk` — Manage offdesk approvals and recovery artifacts
* `ondesk` — Capture ondesk notes and prompt context from external harness work
* `tmux` — tmux integration utilities
* `sounds` — Manage sound effects for agent state transitions
* `uninstall` — Uninstall Forager
* `completion` — Generate shell completions

###### **Options:**

* `-p`, `--profile <PROFILE>` — Profile to use (separate workspace with its own sessions)



## `forager add`

Add a new session

**Usage:** `forager add [OPTIONS] [PATH]`

###### **Arguments:**

* `<PATH>` — Project directory (defaults to current directory)

  Default value: `.`

###### **Options:**

* `-t`, `--title <TITLE>` — Session title (defaults to folder name)
* `-g`, `--group <GROUP>` — Group path (defaults to parent folder)
* `-c`, `--cmd <COMMAND>` — Command to run (e.g., 'claude', 'opencode', 'vibe', 'codex', 'gemini')
* `-P`, `--parent <PARENT>` — Parent session (creates sub-session, inherits group)
* `-l`, `--launch` — Launch the session immediately after creating
* `-w`, `--worktree <WORKTREE_BRANCH>` — Create session in a git worktree for the specified branch
* `-b`, `--new-branch` — Create a new branch (use with --worktree)
* `-y`, `--yolo` — Enable YOLO mode (skip permission prompts)
* `--trust-hooks` — Automatically trust repository hooks without prompting



## `forager init`

Initialize .forager/config.toml in a repository

**Usage:** `forager init [PATH]`

###### **Arguments:**

* `<PATH>` — Directory to initialize (defaults to current directory)

  Default value: `.`



## `forager list`

List all sessions

**Usage:** `forager list [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON
* `--all` — List sessions from all profiles



## `forager remove`

Remove a session

**Usage:** `forager remove [OPTIONS] <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title to remove

###### **Options:**

* `--delete-worktree` — Delete worktree directory (default: keep worktree)
* `--force` — Force worktree removal even with untracked/modified files
* `--keep-container` — Keep legacy sandbox container instead of deleting it (default: delete per config)



## `forager status`

Show session status summary

**Usage:** `forager status [OPTIONS]`

###### **Options:**

* `-v`, `--verbose` — Show detailed session list
* `-q`, `--quiet` — Only output waiting count (for scripts)
* `--json` — Output as JSON



## `forager doctor`

Diagnose Forager paths, profile env, and legacy AoE compatibility state

**Usage:** `forager doctor [OPTIONS]`

###### **Options:**

* `--project <PATH>` — Repository path to inspect for .forager/.aoe config

  Default value: `.`
* `--json` — Output as JSON



## `forager migrate`

Migrate legacy AoE compatibility paths

**Usage:** `forager migrate <COMMAND>`

###### **Subcommands:**

* `aoe` — Copy legacy AoE paths into Forager primary paths



## `forager migrate aoe`

Copy legacy AoE paths into Forager primary paths

**Usage:** `forager migrate aoe [OPTIONS]`

###### **Options:**

* `--project <PATH>` — Repository path to inspect for .aoe/.forager config

  Default value: `.`
* `--dry-run` — Show the migration plan without copying files
* `--json` — Output as JSON



## `forager session`

Manage session lifecycle (start, stop, attach, etc.)

**Usage:** `forager session <COMMAND>`

###### **Subcommands:**

* `start` — Start a session's tmux process
* `stop` — Stop session process
* `restart` — Restart session
* `attach` — Attach to session interactively
* `show` — Show session details
* `rename` — Rename a session
* `current` — Auto-detect current session



## `forager session start`

Start a session's tmux process

**Usage:** `forager session start <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title



## `forager session stop`

Stop session process

**Usage:** `forager session stop <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title



## `forager session restart`

Restart session

**Usage:** `forager session restart <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title



## `forager session attach`

Attach to session interactively

**Usage:** `forager session attach <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title



## `forager session show`

Show session details

**Usage:** `forager session show [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title (optional, auto-detects in tmux)

###### **Options:**

* `--json` — Output as JSON



## `forager session rename`

Rename a session

**Usage:** `forager session rename [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title (optional, auto-detects in tmux)

###### **Options:**

* `-t`, `--title <TITLE>` — New title for the session
* `-g`, `--group <GROUP>` — New group for the session (empty string to ungroup)



## `forager session current`

Auto-detect current session

**Usage:** `forager session current [OPTIONS]`

###### **Options:**

* `-q`, `--quiet` — Just session name (for scripting)
* `--json` — Output as JSON



## `forager group`

Manage groups for organizing sessions

**Usage:** `forager group <COMMAND>`

###### **Subcommands:**

* `list` — List all groups
* `create` — Create a new group
* `delete` — Delete a group
* `move` — Move session to group



## `forager group list`

List all groups

**Usage:** `forager group list [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager group create`

Create a new group

**Usage:** `forager group create [OPTIONS] <NAME>`

###### **Arguments:**

* `<NAME>` — Group name

###### **Options:**

* `--parent <PARENT>` — Parent group for creating subgroups



## `forager group delete`

Delete a group

**Usage:** `forager group delete [OPTIONS] <NAME>`

###### **Arguments:**

* `<NAME>` — Group name

###### **Options:**

* `--force` — Force delete by moving sessions to default group



## `forager group move`

Move session to group

**Usage:** `forager group move <IDENTIFIER> <GROUP>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title
* `<GROUP>` — Target group



## `forager profile`

Manage profiles (separate workspaces)

**Usage:** `forager profile [COMMAND]`

###### **Subcommands:**

* `list` — List all profiles
* `create` — Create a new profile
* `delete` — Delete a profile
* `default` — Show or set default profile



## `forager profile list`

List all profiles

**Usage:** `forager profile list`



## `forager profile create`

Create a new profile

**Usage:** `forager profile create <NAME>`

###### **Arguments:**

* `<NAME>` — Profile name



## `forager profile delete`

Delete a profile

**Usage:** `forager profile delete <NAME>`

###### **Arguments:**

* `<NAME>` — Profile name



## `forager profile default`

Show or set default profile

**Usage:** `forager profile default [NAME]`

###### **Arguments:**

* `<NAME>` — Profile name (optional, shows current if not provided)



## `forager project`

Initialize and inspect project operation packets

**Usage:** `forager project <COMMAND>`

###### **Subcommands:**

* `init` — Create a read-only project operation initialization packet
* `apply-governance-hints` — Apply reviewed governance surface templates to a project
* `audit-docs` — Audit documentation and human-facing artifact governance surfaces



## `forager project init`

Create a read-only project operation initialization packet

**Usage:** `forager project init [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` — Project repository/root directory to initialize for Forager operation

###### **Options:**

* `--project-key <PROJECT_KEY>` — Stable project key used by Ondesk, Offdesk, and adaptive wiki records
* `--operation-target <MODULE_PATH_OR_ID>` — Module path/id to mark as a prioritized operation target
* `--out <OUT>` — Write the initialization packet to this directory
* `--include-git` — Include read-only git branch/status/diff-stat evidence
* `--force` — Overwrite known initialization files when --out already contains files
* `--json` — Output machine-readable JSON



## `forager project apply-governance-hints`

Apply reviewed governance surface templates to a project

**Usage:** `forager project apply-governance-hints [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` — Project repository/root directory to update

###### **Options:**

* `--project-key <PROJECT_KEY>` — Stable project key to render into newly created surfaces
* `--surface <SURFACE>` — Surface role to create. Repeat to limit scope; defaults to all missing surfaces

  Possible values: `current-state`, `next-actions`, `decisions`, `deliverables`

* `--reviewed` — Confirm that the operator reviewed the hints and approves creating missing files
* `--json` — Output machine-readable JSON



## `forager project audit-docs`

Audit documentation and human-facing artifact governance surfaces

**Usage:** `forager project audit-docs [OPTIONS] <PATH>`

###### **Arguments:**

* `<PATH>` — Project repository/root directory to audit

###### **Options:**

* `--audit-profile <AUDIT_PROFILE>` — Governance profile to apply

  Default value: `standard`

  Possible values: `light`, `standard`, `research-longrun`

* `--adaptive-profile-dir <ADAPTIVE_PROFILE_DIR>` — Optional profile directory containing adaptive wiki state
* `--current-stale-days <CURRENT_STALE_DAYS>` — Allowed day gap before the current-state surface is considered stale

  Default value: `0`
* `--large-log-lines <LARGE_LOG_LINES>` — Line threshold for large-log warnings

  Default value: `1000`
* `--json` — Output machine-readable JSON to stdout
* `--json-out <JSON_OUT>` — Write JSON report to this path
* `--md-out <MD_OUT>` — Write Markdown report to this path



## `forager worktree`

Manage git worktrees for parallel development

**Usage:** `forager worktree <COMMAND>`

###### **Subcommands:**

* `list` — List all worktrees in current repository
* `info` — Show worktree information for a session
* `cleanup` — Cleanup orphaned worktrees



## `forager worktree list`

List all worktrees in current repository

**Usage:** `forager worktree list`



## `forager worktree info`

Show worktree information for a session

**Usage:** `forager worktree info <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID or title



## `forager worktree cleanup`

Cleanup orphaned worktrees

**Usage:** `forager worktree cleanup [OPTIONS]`

###### **Options:**

* `-f`, `--force` — Actually remove worktrees (default is dry-run)



## `forager offdesk`

Manage offdesk approvals and recovery artifacts

**Usage:** `forager offdesk <COMMAND>`

###### **Subcommands:**

* `pending` — List pending action approvals
* `gate` — Evaluate whether an offdesk capability may execute now
* `launch` — Gate and record a background runner launch
* `enqueue` — Enqueue a durable offdesk task
* `tick` — Run one offdesk control-loop pass
* `tasks` — Show durable offdesk tasks
* `provider-capacity` — Show provider capacity cooldown state
* `provider-fallback` — Recommend provider/model fallbacks without retargeting tasks
* `cancel-task` — Mark a durable task cancelled without stopping its background runner
* `retry-task` — Requeue a failed, resume-pending, or cancelled durable task
* `resume-task` — Accept recovery for a resume-pending task and requeue it
* `abandon-task` — Discard a failed or resume-pending task
* `poll` — Poll background runner probes, persist phase transitions, and reconcile task status
* `ok` — Approve the oldest or targeted pending action
* `cancel` — Deny the oldest or targeted pending action
* `resume` — Show task resume artifacts
* `background` — Show background runner recovery probes
* `capabilities` — Show Task Team capability metadata
* `snapshots` — List pre-mutation checkpoint snapshots
* `snapshot` — Show and verify a pre-mutation checkpoint snapshot
* `restore-plan` — Show a dry-run rollback plan without modifying files
* `debug-bundle` — Emit a sanitized read-only debug bundle
* `maintenance-report` — Summarize read-only Offdesk maintenance risks
* `maintenance-request` — Create or reuse an approval request for a maintenance action
* `closeout` — Generate a mandatory closeout plan and commercial review packet
* `closeout-review` — Record a reviewed closeout verdict without applying file operations
* `wiki` — Inspect adaptive wiki candidates, entries, projections, and lint



## `forager offdesk pending`

List pending action approvals

**Usage:** `forager offdesk pending [OPTIONS]`

###### **Options:**

* `--all` — Include resolved and expired approvals
* `--json` — Output as JSON



## `forager offdesk gate`

Evaluate whether an offdesk capability may execute now

**Usage:** `forager offdesk gate [OPTIONS] --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --task-id <TASK_ID> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` — Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--project-key <PROJECT_KEY>` — Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` — Request ID for approval and audit correlation
* `--task-id <TASK_ID>` — Task ID for approval and audit correlation
* `--mutation-class <MUTATION_CLASS>` — Mutation class to match against an ExecutionBrief envelope
* `--brief <BRIEF>` — JSON file containing an ExecutionBrief
* `--provider-id <PROVIDER_ID>` — Provider ID to check against provider capacity cooldown state
* `--model <MODEL>` — Provider model to check against provider capacity cooldown state
* `--artifact <ARTIFACT_REFS>` — Artifact reference in ARTIFACT_ID=PATH form
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` — Agent work mode used to match adaptive wiki entries
* `--preview <PREVIEW>` — Operator-safe action preview

  Default value: ``
* `--reason <REASON>` — Reason shown when approval is required

  Default value: ``
* `--source-surface <SOURCE_SURFACE>` — Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` — Pending approval TTL in minutes

  Default value: `30`
* `--json` — Output as JSON



## `forager offdesk launch`

Gate and record a background runner launch

**Usage:** `forager offdesk launch [OPTIONS] --runner <RUNNER> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --task-id <TASK_ID> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` — Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--runner <RUNNER>` — Runner backend to record: local-tmux, local-background, github-runner, remote-worker
* `--project-key <PROJECT_KEY>` — Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` — Request ID for approval and audit correlation
* `--task-id <TASK_ID>` — Task ID for approval and audit correlation
* `--mutation-class <MUTATION_CLASS>` — Mutation class to match against an ExecutionBrief envelope
* `--brief <BRIEF>` — JSON file containing an ExecutionBrief
* `--provider-id <PROVIDER_ID>` — Provider ID to check against provider capacity cooldown state
* `--model <MODEL>` — Provider model to check against provider capacity cooldown state
* `--artifact <ARTIFACT_REFS>` — Artifact reference in ARTIFACT_ID=PATH form
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` — Agent work mode used to match adaptive wiki entries
* `--ticket-id <TICKET_ID>` — Stable ticket ID. Generated if omitted
* `--launch-spec <LAUNCH_SPEC>` — Redacted launch spec summary to store with the ticket
* `--cmd <COMMAND>` — Shell command to execute for local-background or local-tmux runners
* `--workdir <WORKDIR>` — Working directory for --cmd. Defaults to the current directory
* `--log-artifact <LOG_ARTIFACT>` — Log artifact path for --cmd stdout and stderr
* `--result-artifact <RESULT_ARTIFACT>` — Result sidecar path used by poll to mark the ticket completed
* `--runtime-alive` — Whether a local runtime handle is alive immediately after launch

  Default value: `true`
* `--provider-launch-spec-reconstructable` — Whether a local_background launch spec can be reconstructed after restart
* `--ack-timeout-sec <ACK_TIMEOUT_SEC>` — External ack timeout in seconds

  Default value: `300`
* `--preview <PREVIEW>` — Operator-safe action preview

  Default value: ``
* `--reason <REASON>` — Reason shown when approval is required

  Default value: ``
* `--source-surface <SOURCE_SURFACE>` — Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` — Pending approval TTL in minutes

  Default value: `30`
* `--json` — Output as JSON



## `forager offdesk enqueue`

Enqueue a durable offdesk task

**Usage:** `forager offdesk enqueue [OPTIONS] --runner <RUNNER> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --cmd <COMMAND> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` — Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--runner <RUNNER>` — Runner backend to use: local-tmux or local-background
* `--project-key <PROJECT_KEY>` — Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` — Request ID for approval and audit correlation
* `--task-id <TASK_ID>` — Task ID. Generated if omitted
* `--cmd <COMMAND>` — Shell command to execute when the task is dispatched
* `--workdir <WORKDIR>` — Working directory for --cmd. Defaults to the current directory
* `--brief <BRIEF>` — JSON file containing an ExecutionBrief to store with the task
* `--mutation-class <MUTATION_CLASS>` — Mutation class to match against an ExecutionBrief envelope
* `--provider-id <PROVIDER_ID>` — Provider ID to check against provider capacity cooldown state when dispatched
* `--model <MODEL>` — Provider model to check against provider capacity cooldown state when dispatched
* `--artifact <ARTIFACT_REFS>` — Artifact reference in ARTIFACT_ID=PATH form
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` — Agent work mode used to match adaptive wiki entries
* `--preview <PREVIEW>` — Operator-safe action preview

  Default value: ``
* `--reason <REASON>` — Reason shown when approval is required

  Default value: ``
* `--not-before <NOT_BEFORE>` — Do not dispatch before this RFC3339 timestamp
* `--log-artifact <LOG_ARTIFACT>` — Log artifact path for command stdout and stderr
* `--result-artifact <RESULT_ARTIFACT>` — Result sidecar path used by tick to mark the task completed
* `--json` — Output as JSON



## `forager offdesk tick`

Run one offdesk control-loop pass

**Usage:** `forager offdesk tick [OPTIONS]`

###### **Options:**

* `--limit <LIMIT>` — Maximum queued tasks to dispatch in this tick

  Default value: `10`
* `--lock-stale-minutes <LOCK_STALE_MINUTES>` — Treat previous free lock metadata as stale after this many minutes

  Default value: `30`
* `--notify-cooldown-minutes <NOTIFY_COOLDOWN_MINUTES>` — Record notification cooldown state in minutes while polling background runs
* `--json` — Output as JSON



## `forager offdesk tasks`

Show durable offdesk tasks

**Usage:** `forager offdesk tasks [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` — Filter tasks by project key
* `--task-id <TASK_ID>` — Filter tasks by exact task ID
* `--status <STATUS>` — Filter tasks by status. Repeat for multiple statuses
* `--latest` — Return only the newest matching task by updated_at
* `--json` — Output as JSON



## `forager offdesk provider-capacity`

Show provider capacity cooldown state

**Usage:** `forager offdesk provider-capacity [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk provider-fallback`

Recommend provider/model fallbacks without retargeting tasks

**Usage:** `forager offdesk provider-fallback [OPTIONS] --provider-id <PROVIDER_ID>`

###### **Options:**

* `--provider-id <PROVIDER_ID>` — Current provider ID that is blocked or under review
* `--model <MODEL>` — Current provider model to exclude from fallback candidates
* `--runner-role <RUNNER_ROLE>` — Runner role used to filter compatible cross-provider candidates

  Default value: `worker`
* `--json` — Output as JSON



## `forager offdesk cancel-task`

Mark a durable task cancelled without stopping its background runner

**Usage:** `forager offdesk cancel-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` — Offdesk task ID to cancel

###### **Options:**

* `--reason <REASON>` — Operator reason to store on the task
* `--json` — Output as JSON



## `forager offdesk retry-task`

Requeue a failed, resume-pending, or cancelled durable task

**Usage:** `forager offdesk retry-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` — Offdesk task ID to retry

###### **Options:**

* `--new-approval` — Supersede matching denied approval rows so the next tick creates a new approval
* `--json` — Output as JSON



## `forager offdesk resume-task`

Accept recovery for a resume-pending task and requeue it

**Usage:** `forager offdesk resume-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` — Offdesk task ID to update

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk abandon-task`

Discard a failed or resume-pending task

**Usage:** `forager offdesk abandon-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` — Offdesk task ID to update

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk poll`

Poll background runner probes, persist phase transitions, and reconcile task status

**Usage:** `forager offdesk poll [OPTIONS] [TICKET_ID]`

###### **Arguments:**

* `<TICKET_ID>` — Ticket ID to poll. Defaults to all tickets

###### **Options:**

* `--notify-cooldown-minutes <NOTIFY_COOLDOWN_MINUTES>` — Record notification cooldown state in minutes
* `--json` — Output as JSON



## `forager offdesk ok`

Approve the oldest or targeted pending action

**Usage:** `forager offdesk ok [OPTIONS] [APPROVAL_ID]`

###### **Arguments:**

* `<APPROVAL_ID>` — Approval ID to resolve. Defaults to the oldest pending approval

###### **Options:**

* `--by <BY>` — Operator or surface resolving this approval

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk cancel`

Deny the oldest or targeted pending action

**Usage:** `forager offdesk cancel [OPTIONS] [APPROVAL_ID]`

###### **Arguments:**

* `<APPROVAL_ID>` — Approval ID to resolve. Defaults to the oldest pending approval

###### **Options:**

* `--by <BY>` — Operator or surface resolving this approval

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk resume`

Show task resume artifacts

**Usage:** `forager offdesk resume [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk background`

Show background runner recovery probes

**Usage:** `forager offdesk background [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk capabilities`

Show Task Team capability metadata

**Usage:** `forager offdesk capabilities [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk snapshots`

List pre-mutation checkpoint snapshots

**Usage:** `forager offdesk snapshots [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk snapshot`

Show and verify a pre-mutation checkpoint snapshot

**Usage:** `forager offdesk snapshot [OPTIONS] <MUTATION_ID>`

###### **Arguments:**

* `<MUTATION_ID>` — Mutation snapshot ID

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk restore-plan`

Show a dry-run rollback plan without modifying files

**Usage:** `forager offdesk restore-plan [OPTIONS] <MUTATION_ID>`

###### **Arguments:**

* `<MUTATION_ID>` — Mutation snapshot ID

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk debug-bundle`

Emit a sanitized read-only debug bundle

**Usage:** `forager offdesk debug-bundle [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON
* `--export` — Write the sanitized bundle JSON to a diagnostics file
* `--output <OUTPUT>` — Write the sanitized bundle JSON to this path



## `forager offdesk maintenance-report`

Summarize read-only Offdesk maintenance risks

**Usage:** `forager offdesk maintenance-report [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON
* `--wiki-review-near-expiry-hours <WIKI_REVIEW_NEAR_EXPIRY_HOURS>` — Hours before review_after expiry to flag adaptive wiki entries

  Default value: `168`
* `--wiki-runtime-ack-near-expiry-hours <WIKI_RUNTIME_ACK_NEAR_EXPIRY_HOURS>` — Hours before runtime policy acknowledgement expiry to flag attention

  Default value: `6`



## `forager offdesk maintenance-request`

Create or reuse an approval request for a maintenance action

**Usage:** `forager offdesk maintenance-request [OPTIONS] --kind <KIND> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --preview <PREVIEW> --reason <REASON>`

###### **Options:**

* `--kind <KIND>` — Bounded maintenance action kind to request approval for
* `--project-key <PROJECT_KEY>` — Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` — Request ID for approval and audit correlation
* `--task-id <TASK_ID>` — Task ID for approval identity. Defaults to maintenance-{kind}-{target-id}
* `--target-id <TARGET_ID>` — Optional target identifier used for approval deduplication and review
* `--risk <RISK>` — Override the default risk for this maintenance kind
* `--preview <PREVIEW>` — Operator-safe action preview
* `--reason <REASON>` — Reason shown when approval is required
* `--source-surface <SOURCE_SURFACE>` — Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` — Pending approval TTL in minutes

  Default value: `30`
* `--json` — Output as JSON



## `forager offdesk closeout`

Generate a mandatory closeout plan and commercial review packet

**Usage:** `forager offdesk closeout [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` — Project key to close out. Defaults to all projects in the profile
* `--request-id <REQUEST_ID>` — Request ID to close out
* `--task-id <TASK_ID>` — Task ID to close out
* `--workdir <WORKDIR>` — Optional project workdir for read-only git status evidence
* `--include-git` — Include read-only git status and diff-stat from --workdir or matched task workdir
* `--review-provider <REVIEW_PROVIDER>` — Commercial model/provider label expected to review move/delete/archive decisions

  Default value: `commercial`
* `--output <OUTPUT>` — Write closeout artifacts to this directory
* `--dry-run` — Accepted for explicit operator intent; closeout never applies file operations
* `--json` — Output as JSON



## `forager offdesk closeout-review`

Record a reviewed closeout verdict without applying file operations

**Usage:** `forager offdesk closeout-review [OPTIONS] --verdict <VERDICT>`

###### **Options:**

* `--closeout-id <CLOSEOUT_ID>` — Closeout ID from `forager offdesk closeout`
* `--artifact-dir <ARTIFACT_DIR>` — Closeout artifact directory containing closeout_plan.json
* `--verdict <VERDICT>` — Commercial review verdict

  Possible values: `approved`, `revise`, `blocked`

* `--reviewer <REVIEWER>` — Reviewer or reviewing model label

  Default value: `operator`
* `--review-provider <REVIEW_PROVIDER>` — Commercial model/provider label used for review
* `--review-file <REVIEW_FILE>` — Optional path to the raw commercial review output
* `--unsafe-operation <UNSAFE_OPERATION>` — Unsafe operation reported by review; may be passed multiple times
* `--missing-evidence <MISSING_EVIDENCE>` — Missing evidence reported by review; may be passed multiple times
* `--required-first-read <REQUIRED_FIRST_READ>` — Required first-read path reported by review; may be passed multiple times
* `--notes <NOTES>` — Short review note. Secrets are redacted before persistence
* `--json` — Output as JSON



## `forager offdesk wiki`

Inspect adaptive wiki candidates, entries, projections, and lint

**Usage:** `forager offdesk wiki <COMMAND>`

###### **Subcommands:**

* `corrections` — List first-class adaptive wiki correction records
* `proposal-events` — List adaptive wiki review proposal lifecycle events
* `record-proposal-event` — Record an operator decision for a curator review proposal
* `accept-proposal` — Accept a current curator review proposal and copy its metadata into the event
* `reject-proposal` — Reject a current curator review proposal and copy its metadata into the event
* `supersede-proposal` — Mark a current curator review proposal superseded and copy its metadata into the event
* `proposal-handoff` — Preview the governed mutation handoff command for a current proposal
* `proposal-receipt` — Link a handoff preview, mutation audit, and lifecycle event without mutating state
* `candidates` — List adaptive wiki candidates
* `entries` — List adaptive wiki entries
* `show` — Show one adaptive wiki entry or candidate
* `projection` — Show the AI projection for a scope
* `runtime-policy-acks` — List strict runtime projection policy acknowledgements
* `runtime-policy-ack-report` — Report strict runtime projection acknowledgements that need attention
* `review-after-report` — Report promoted entries whose review_after needs attention
* `ack-runtime-policy` — Acknowledge strict review_after exclusion for runtime projection
* `lint` — Lint adaptive wiki state
* `export-markdown` — Export adaptive wiki state as a one-way markdown vault
* `graph` — Export a read-only adaptive wiki tag graph
* `review` — Generate a recommendation-only adaptive wiki review report
* `evaluate-episode` — Evaluate one adaptive wiki entry across in-scope and out-of-scope projections
* `episode-trace` — Trace live task/probe/wiki evidence for adaptive behavior review
* `evaluate-recurrence` — Evaluate whether corrections recur after an entry is promoted
* `promotion-chain` — Reconstruct the evidence chain captured at promotion time
* `promote` — Promote a candidate into a scoped wiki entry
* `reject` — Reject a candidate without creating an entry
* `rescope` — Change an entry scope
* `deprecate` — Deprecate an entry so it no longer appears in AI projection
* `renew-review-after` — Renew an entry review_after timestamp without changing scope or instruction
* `add-counterexample` — Add a counterexample evidence ref to an entry
* `update-runbook` — Attach governed runbook support refs to a procedure entry



## `forager offdesk wiki corrections`

List first-class adaptive wiki correction records

**Usage:** `forager offdesk wiki corrections [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk wiki proposal-events`

List adaptive wiki review proposal lifecycle events

**Usage:** `forager offdesk wiki proposal-events [OPTIONS]`

###### **Options:**

* `--proposal-id <PROPOSAL_ID>` — Filter lifecycle events by proposal id
* `--json` — Output as JSON



## `forager offdesk wiki record-proposal-event`

Record an operator decision for a curator review proposal

**Usage:** `forager offdesk wiki record-proposal-event [OPTIONS] --decision <DECISION> --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Curator review proposal id

###### **Options:**

* `--decision <DECISION>` — Operator decision for the proposal
* `--proposal-action <PROPOSAL_ACTION>` — Proposal action that was reviewed
* `--subject-kind <SUBJECT_KIND>` — Proposal subject kind, such as entry or candidate

  Default value: ``
* `--subject-id <SUBJECT_ID>` — Proposal subject id

  Default value: ``
* `--by <BY>` — Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` — Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` — Evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` — Previous proposal id superseded by this decision
* `--json` — Output as JSON



## `forager offdesk wiki accept-proposal`

Accept a current curator review proposal and copy its metadata into the event

**Usage:** `forager offdesk wiki accept-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Current curator review proposal id

###### **Options:**

* `--by <BY>` — Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` — Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` — Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` — Previous proposal id superseded by this decision
* `--allow-decided` — Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` — Output as JSON



## `forager offdesk wiki reject-proposal`

Reject a current curator review proposal and copy its metadata into the event

**Usage:** `forager offdesk wiki reject-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Current curator review proposal id

###### **Options:**

* `--by <BY>` — Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` — Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` — Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` — Previous proposal id superseded by this decision
* `--allow-decided` — Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` — Output as JSON



## `forager offdesk wiki supersede-proposal`

Mark a current curator review proposal superseded and copy its metadata into the event

**Usage:** `forager offdesk wiki supersede-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Current curator review proposal id

###### **Options:**

* `--by <BY>` — Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` — Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` — Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` — Previous proposal id superseded by this decision
* `--allow-decided` — Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` — Output as JSON



## `forager offdesk wiki proposal-handoff`

Preview the governed mutation handoff command for a current proposal

**Usage:** `forager offdesk wiki proposal-handoff [OPTIONS] <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Current curator review proposal id

###### **Options:**

* `--mutation <MUTATION>` — Operator-selected mutation path to preview when the proposal is manual
* `--scope <SCOPE>` — Scope for a parameterized rescope handoff
* `--scope-ref <SCOPE_REF>` — Scope reference for a parameterized rescope handoff
* `--evidence-ref <EVIDENCE_REF>` — Evidence ref for a parameterized counterexample handoff
* `--deprecated-entry-id <DEPRECATED_ENTRY_ID>` — Entry to deprecate for a parameterized merge cleanup or conflict handoff
* `--reason <REASON>` — Operator rationale to include in the previewed mutation command
* `--json` — Output as JSON



## `forager offdesk wiki proposal-receipt`

Link a handoff preview, mutation audit, and lifecycle event without mutating state

**Usage:** `forager offdesk wiki proposal-receipt [OPTIONS] --audit-id <AUDIT_ID> --event-id <EVENT_ID> --command <COMMAND> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` — Curator review proposal id that the receipt should link

###### **Options:**

* `--audit-id <AUDIT_ID>` — Adaptive wiki mutation audit id produced by the executed mutation command
* `--event-id <EVENT_ID>` — Proposal lifecycle event id recorded for the operator decision
* `--command <COMMAND>` — Previewed handoff command that the operator executed or reviewed
* `--export` — Write the sanitized receipt JSON to an audit artifact file
* `--output <OUTPUT>` — Write the sanitized receipt JSON to this path
* `--json` — Output as JSON



## `forager offdesk wiki candidates`

List adaptive wiki candidates

**Usage:** `forager offdesk wiki candidates [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to match
* `--project-key <PROJECT_KEY>` — Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to match
* `--json` — Output as JSON



## `forager offdesk wiki entries`

List adaptive wiki entries

**Usage:** `forager offdesk wiki entries [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to match
* `--project-key <PROJECT_KEY>` — Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to match
* `--json` — Output as JSON



## `forager offdesk wiki show`

Show one adaptive wiki entry or candidate

**Usage:** `forager offdesk wiki show [OPTIONS] <ID>`

###### **Arguments:**

* `<ID>` — Adaptive wiki entry or candidate id

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk wiki projection`

Show the AI projection for a scope

**Usage:** `forager offdesk wiki projection [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to match
* `--project-key <PROJECT_KEY>` — Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to match
* `--report` — Return the projection policy report instead of only selected entries
* `--compare-review-expired-policy` — Compare default warn policy with strict review_after exclusion
* `--max-entries <MAX_ENTRIES>` — Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` — Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` — Maximum characters kept per projected instruction; 0 disables truncation
* `--exclude-review-expired` — Exclude entries that are past review_after from the projection report
* `--json` — Output as JSON



## `forager offdesk wiki runtime-policy-acks`

List strict runtime projection policy acknowledgements

**Usage:** `forager offdesk wiki runtime-policy-acks [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk wiki runtime-policy-ack-report`

Report strict runtime projection acknowledgements that need attention

**Usage:** `forager offdesk wiki runtime-policy-ack-report [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to evaluate for query-specific ack applicability
* `--project-key <PROJECT_KEY>` — Project key scope to evaluate for query-specific ack applicability
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to evaluate for query-specific ack applicability
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to evaluate for query-specific ack applicability
* `--max-entries <MAX_ENTRIES>` — Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` — Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` — Maximum characters kept per projected instruction; 0 disables truncation
* `--near-expiry-hours <NEAR_EXPIRY_HOURS>` — Mark active acknowledgements expiring within this many hours

  Default value: `6`
* `--json` — Output as JSON



## `forager offdesk wiki review-after-report`

Report promoted entries whose review_after needs attention

**Usage:** `forager offdesk wiki review-after-report [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to match
* `--project-key <PROJECT_KEY>` — Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to match
* `--near-expiry-hours <NEAR_EXPIRY_HOURS>` — Mark entries needing review within this many hours

  Default value: `168`
* `--json` — Output as JSON



## `forager offdesk wiki ack-runtime-policy`

Acknowledge strict review_after exclusion for runtime projection

**Usage:** `forager offdesk wiki ack-runtime-policy [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` — Session/request scope to match exactly
* `--project-key <PROJECT_KEY>` — Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` — Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` — Agent work mode scope to match
* `--scope-mode <SCOPE_MODE>` — Acknowledgement scope: exact-query or project-artifact

  Default value: `exact-query`
* `--max-entries <MAX_ENTRIES>` — Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` — Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` — Maximum characters kept per projected instruction; 0 disables truncation
* `--ttl-hours <TTL_HOURS>` — Acknowledgement TTL in hours

  Default value: `24`
* `--reason <REASON>` — Operator reason for enabling strict runtime projection in this scope

  Default value: ``
* `--json` — Output as JSON



## `forager offdesk wiki lint`

Lint adaptive wiki state

**Usage:** `forager offdesk wiki lint [OPTIONS]`

###### **Options:**

* `--json` — Output as JSON



## `forager offdesk wiki export-markdown`

Export adaptive wiki state as a one-way markdown vault

**Usage:** `forager offdesk wiki export-markdown [OPTIONS] --output <OUTPUT>`

###### **Options:**

* `--output <OUTPUT>` — Directory to write the markdown vault into
* `--dry-run` — Preview export files without writing them
* `--json` — Output as JSON



## `forager offdesk wiki graph`

Export a read-only adaptive wiki tag graph

**Usage:** `forager offdesk wiki graph [OPTIONS]`

###### **Options:**

* `--output <OUTPUT>` — Optional directory to write graph.json and graph.md into
* `--dry-run` — Preview graph export files without writing them
* `--json` — Output as JSON



## `forager offdesk wiki review`

Generate a recommendation-only adaptive wiki review report

**Usage:** `forager offdesk wiki review [OPTIONS]`

###### **Options:**

* `--dry-run` — Preview recommendations without writing report files
* `--active-only` — Show proposals that are open or have stale lifecycle decisions
* `--decided-only` — Show proposals with non-stale accepted, rejected, or superseded decisions
* `--stale-only` — Show proposals whose latest lifecycle decision is stale
* `--json` — Output as JSON



## `forager offdesk wiki evaluate-episode`

Evaluate one adaptive wiki entry across in-scope and out-of-scope projections

**Usage:** `forager offdesk wiki evaluate-episode [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Promoted adaptive wiki entry id expected to appear only in the in-scope projection

###### **Options:**

* `--session-id <SESSION_ID>` — In-scope session/request id to match
* `--project-key <PROJECT_KEY>` — In-scope project key to match
* `--artifact-kind <ARTIFACT_KIND>` — In-scope artifact kind to match
* `--agent-mode <AGENT_MODE>` — In-scope agent work mode to match
* `--out-session-id <OUT_SESSION_ID>` — Out-of-scope session/request id. Defaults to a generated non-matching value
* `--out-project-key <OUT_PROJECT_KEY>` — Out-of-scope project key. Defaults to a generated non-matching value
* `--out-artifact-kind <OUT_ARTIFACT_KIND>` — Out-of-scope artifact kind. Defaults to a generated non-matching value
* `--out-agent-mode <OUT_AGENT_MODE>` — Out-of-scope agent work mode. Defaults to a generated non-matching mode when possible
* `--dry-run` — Preview the report without writing report files
* `--json` — Output as JSON



## `forager offdesk wiki episode-trace`

Trace live task/probe/wiki evidence for adaptive behavior review

**Usage:** `forager offdesk wiki episode-trace [OPTIONS]`

###### **Options:**

* `--request-id <REQUEST_ID>` — Filter trace events by request id
* `--task-id <TASK_ID>` — Filter trace events by task id
* `--project-key <PROJECT_KEY>` — Filter trace events by project key
* `--artifact-kind <ARTIFACT_KIND>` — Filter trace events by artifact kind
* `--entry-id <ENTRY_ID>` — Filter trace events by adaptive wiki entry id
* `--dry-run` — Preview the trace without writing report files
* `--json` — Output as JSON



## `forager offdesk wiki evaluate-recurrence`

Evaluate whether corrections recur after an entry is promoted

**Usage:** `forager offdesk wiki evaluate-recurrence [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Promoted adaptive wiki entry id to evaluate

###### **Options:**

* `--dry-run` — Preview the report without writing report files
* `--json` — Output as JSON



## `forager offdesk wiki promotion-chain`

Reconstruct the evidence chain captured at promotion time

**Usage:** `forager offdesk wiki promotion-chain [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Promoted adaptive wiki entry id to reconstruct

###### **Options:**

* `--dry-run` — Preview the report without writing report files
* `--json` — Output as JSON



## `forager offdesk wiki promote`

Promote a candidate into a scoped wiki entry

**Usage:** `forager offdesk wiki promote [OPTIONS] <CANDIDATE_ID>`

###### **Arguments:**

* `<CANDIDATE_ID>` — Adaptive wiki candidate id

###### **Options:**

* `--scope <SCOPE>` — Scope for the promoted entry. Defaults to the candidate scope
* `--scope-ref <SCOPE_REF>` — Scope reference for the promoted entry. Required when --scope is used
* `--activation-mode <ACTIVATION_MODE>` — Activation mode for the promoted entry

  Default value: `confirm`
* `--agent-mode <AGENT_MODES>` — Agent work mode this promoted entry should apply to. Repeat for multiple modes; omit to keep candidate modes
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--reason <REASON>` — Optional promotion reason for audit

  Default value: ``
* `--json` — Output as JSON



## `forager offdesk wiki reject`

Reject a candidate without creating an entry

**Usage:** `forager offdesk wiki reject [OPTIONS] --reason <REASON> <CANDIDATE_ID>`

###### **Arguments:**

* `<CANDIDATE_ID>` — Adaptive wiki candidate id

###### **Options:**

* `--reason <REASON>` — Reason for rejecting the candidate
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk wiki rescope`

Change an entry scope

**Usage:** `forager offdesk wiki rescope [OPTIONS] --scope <SCOPE> --scope-ref <SCOPE_REF> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Adaptive wiki entry id

###### **Options:**

* `--scope <SCOPE>` — New entry scope
* `--scope-ref <SCOPE_REF>` — New entry scope reference
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--reason <REASON>` — Optional rescope reason for audit

  Default value: ``
* `--json` — Output as JSON



## `forager offdesk wiki deprecate`

Deprecate an entry so it no longer appears in AI projection

**Usage:** `forager offdesk wiki deprecate [OPTIONS] --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Adaptive wiki entry id

###### **Options:**

* `--reason <REASON>` — Reason for deprecating the entry
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk wiki renew-review-after`

Renew an entry review_after timestamp without changing scope or instruction

**Usage:** `forager offdesk wiki renew-review-after [OPTIONS] --review-after <REVIEW_AFTER> --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Adaptive wiki entry id

###### **Options:**

* `--review-after <REVIEW_AFTER>` — New review_after timestamp in RFC3339 format
* `--reason <REASON>` — Reason for renewing the review timestamp
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk wiki add-counterexample`

Add a counterexample evidence ref to an entry

**Usage:** `forager offdesk wiki add-counterexample [OPTIONS] --evidence-ref <EVIDENCE_REF> --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Adaptive wiki entry id

###### **Options:**

* `--evidence-ref <EVIDENCE_REF>` — Evidence ref that contradicts or limits the entry
* `--reason <REASON>` — Reason for recording the counterexample
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--json` — Output as JSON



## `forager offdesk wiki update-runbook`

Attach governed runbook support refs to a procedure entry

**Usage:** `forager offdesk wiki update-runbook [OPTIONS] --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` — Adaptive wiki procedure entry id

###### **Options:**

* `--support-ref <SUPPORT_REF>` — Human/export support ref such as references/foo.md, templates/foo.md, or scripts/foo.sh
* `--capability-id <CAPABILITY_ID>` — Capability id this procedure is relevant to
* `--required-artifact-kind <REQUIRED_ARTIFACT_KIND>` — Required artifact kind this procedure depends on
* `--reason <REASON>` — Reason for updating the runbook metadata
* `--by <BY>` — Operator or surface performing the review

  Default value: `cli`
* `--json` — Output as JSON



## `forager ondesk`

Capture ondesk notes and prompt context from external harness work

**Usage:** `forager ondesk <COMMAND>`

###### **Subcommands:**

* `note` — Append a safe operator note for an ondesk session or project
* `capture` — Capture live harness scrollback into an inspectable prompt package
* `prompt-package` — Build a markdown prompt package from recent notes and optional capture



## `forager ondesk note`

Append a safe operator note for an ondesk session or project

**Usage:** `forager ondesk note [OPTIONS] --text <TEXT> [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--text <TEXT>` — Operator note text to persist
* `--mode <MODE>` — Work mode label, e.g. planning, analysis, writing, critique
* `--project-key <PROJECT_KEY>` — Stable project key for grouping ondesk knowledge
* `--json` — Output as JSON



## `forager ondesk capture`

Capture live harness scrollback into an inspectable prompt package

**Usage:** `forager ondesk capture [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--lines <LINES>` — Number of tmux scrollback lines to capture

  Default value: `200`
* `--mode <MODE>` — Work mode label, e.g. planning, analysis, writing, critique
* `--project-key <PROJECT_KEY>` — Stable project key for grouping ondesk knowledge
* `--include-git` — Include read-only git status and diff-stat from the session/project path
* `--json` — Output as JSON



## `forager ondesk prompt-package`

Build a markdown prompt package from recent notes and optional capture

**Usage:** `forager ondesk prompt-package [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` — Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--capture-id <CAPTURE_ID>` — Existing capture ID to render
* `--mode <MODE>` — Work mode label used to filter notes
* `--project-key <PROJECT_KEY>` — Stable project key used to filter notes
* `--output <OUTPUT>` — Write markdown package to a file instead of stdout
* `--json` — Output metadata as JSON



## `forager tmux`

tmux integration utilities

**Usage:** `forager tmux <COMMAND>`

###### **Subcommands:**

* `status` — Output session info for use in custom tmux status bar



## `forager tmux status`

Output session info for use in custom tmux status bar

Add this to your ~/.tmux.conf: set -g status-right "#(forager tmux status)"

**Usage:** `forager tmux status [OPTIONS]`

###### **Options:**

* `-f`, `--format <FORMAT>` — Output format (text or json)

  Default value: `text`



## `forager sounds`

Manage sound effects for agent state transitions

**Usage:** `forager sounds <COMMAND>`

###### **Subcommands:**

* `install` — Install bundled sound effects
* `list` — List currently installed sounds
* `test` — Test a sound by playing it



## `forager sounds install`

Install bundled sound effects

**Usage:** `forager sounds install`



## `forager sounds list`

List currently installed sounds

**Usage:** `forager sounds list`



## `forager sounds test`

Test a sound by playing it

**Usage:** `forager sounds test <NAME>`

###### **Arguments:**

* `<NAME>` — Sound file name (without extension)



## `forager uninstall`

Uninstall Forager

**Usage:** `forager uninstall [OPTIONS]`

###### **Options:**

* `--keep-data` — Keep data directory (sessions, config, logs)
* `--keep-tmux-config` — Keep tmux configuration
* `--dry-run` — Show what would be removed without removing
* `-y` — Skip confirmation prompts



## `forager completion`

Generate shell completions

**Usage:** `forager completion <SHELL>`

###### **Arguments:**

* `<SHELL>` — Shell to generate completions for

  Possible values: `bash`, `elvish`, `fish`, `powershell`, `zsh`




<hr/>

<small><i>
    This document was generated automatically by
    <a href="https://crates.io/crates/clap-markdown"><code>clap-markdown</code></a>.
</i></small>
