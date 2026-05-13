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
* `worktree` — Manage git worktrees for parallel development
* `offdesk` — Manage offdesk approvals and recovery artifacts
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
* `cancel-task` — Mark a durable task cancelled without stopping its background runner
* `retry-task` — Requeue a failed, resume-pending, or cancelled durable task
* `resume-task` — Accept recovery for a resume-pending task and requeue it
* `abandon-task` — Discard a failed or resume-pending task
* `poll` — Poll background runner probes and persist phase transitions
* `ok` — Approve the oldest or targeted pending action
* `cancel` — Deny the oldest or targeted pending action
* `resume` — Show task resume artifacts
* `background` — Show background runner recovery probes
* `capabilities` — Show Task Team capability metadata



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

Poll background runner probes and persist phase transitions

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
