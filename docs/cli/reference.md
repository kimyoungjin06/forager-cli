# Command-Line Help for `forager`

This document contains the help content for the `forager` command-line program.

**Command Overview:**

* [`forager`↴](#forager)
* [`forager add`↴](#forager-add)
* [`forager go`↴](#forager-go)
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
* [`forager session context-status`↴](#forager-session-context-status)
* [`forager session context-sync`↴](#forager-session-context-sync)
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
* [`forager project plan-routing`↴](#forager-project-plan-routing)
* [`forager project apply-governance-hints`↴](#forager-project-apply-governance-hints)
* [`forager project wiki-context`↴](#forager-project-wiki-context)
* [`forager project audit-docs`↴](#forager-project-audit-docs)
* [`forager project artifact-index`↴](#forager-project-artifact-index)
* [`forager project document-catalog`↴](#forager-project-document-catalog)
* [`forager project document-search`↴](#forager-project-document-search)
* [`forager project document-lifecycle-review`↴](#forager-project-document-lifecycle-review)
* [`forager project document-lifecycle-request`↴](#forager-project-document-lifecycle-request)
* [`forager project document-lifecycle-apply`↴](#forager-project-document-lifecycle-apply)
* [`forager project document-lifecycle-history`↴](#forager-project-document-lifecycle-history)
* [`forager project retention-review`↴](#forager-project-retention-review)
* [`forager project retention-request`↴](#forager-project-retention-request)
* [`forager project retention-apply`↴](#forager-project-retention-apply)
* [`forager project retention-promote`↴](#forager-project-retention-promote)
* [`forager project implementation-packet`↴](#forager-project-implementation-packet)
* [`forager project sync`↴](#forager-project-sync)
* [`forager project inventory`↴](#forager-project-inventory)
* [`forager worktree`↴](#forager-worktree)
* [`forager worktree list`↴](#forager-worktree-list)
* [`forager worktree info`↴](#forager-worktree-info)
* [`forager worktree cleanup`↴](#forager-worktree-cleanup)
* [`forager offdesk`↴](#forager-offdesk)
* [`forager offdesk harnesses`↴](#forager-offdesk-harnesses)
* [`forager offdesk harness-prompt`↴](#forager-offdesk-harness-prompt)
* [`forager offdesk plan`↴](#forager-offdesk-plan)
* [`forager offdesk plans`↴](#forager-offdesk-plans)
* [`forager offdesk plan-show`↴](#forager-offdesk-plan-show)
* [`forager offdesk plan-review`↴](#forager-offdesk-plan-review)
* [`forager offdesk plan-launch-prep`↴](#forager-offdesk-plan-launch-prep)
* [`forager offdesk remote-operator`↴](#forager-offdesk-remote-operator)
* [`forager offdesk remote-operator status`↴](#forager-offdesk-remote-operator-status)
* [`forager offdesk remote-operator pending`↴](#forager-offdesk-remote-operator-pending)
* [`forager offdesk remote-operator plans`↴](#forager-offdesk-remote-operator-plans)
* [`forager offdesk remote-operator show`↴](#forager-offdesk-remote-operator-show)
* [`forager offdesk remote-session`↴](#forager-offdesk-remote-session)
* [`forager offdesk remote-session policy-inspect`↴](#forager-offdesk-remote-session-policy-inspect)
* [`forager offdesk remote-session policy-resolve`↴](#forager-offdesk-remote-session-policy-resolve)
* [`forager offdesk pending`↴](#forager-offdesk-pending)
* [`forager offdesk gate`↴](#forager-offdesk-gate)
* [`forager offdesk launch`↴](#forager-offdesk-launch)
* [`forager offdesk enqueue`↴](#forager-offdesk-enqueue)
* [`forager offdesk tick`↴](#forager-offdesk-tick)
* [`forager offdesk shift`↴](#forager-offdesk-shift)
* [`forager offdesk shift start-review`↴](#forager-offdesk-shift-start-review)
* [`forager offdesk shift request-arm-review`↴](#forager-offdesk-shift-request-arm-review)
* [`forager offdesk shift confirm-arm-review`↴](#forager-offdesk-shift-confirm-arm-review)
* [`forager offdesk shift candidates`↴](#forager-offdesk-shift-candidates)
* [`forager offdesk shift propose`↴](#forager-offdesk-shift-propose)
* [`forager offdesk shift preview`↴](#forager-offdesk-shift-preview)
* [`forager offdesk shift enterprise-policies`↴](#forager-offdesk-shift-enterprise-policies)
* [`forager offdesk shift host-policy-probe`↴](#forager-offdesk-shift-host-policy-probe)
* [`forager offdesk shift read-policy-probe`↴](#forager-offdesk-shift-read-policy-probe)
* [`forager offdesk shift prepare`↴](#forager-offdesk-shift-prepare)
* [`forager offdesk shift instantiate-template`↴](#forager-offdesk-shift-instantiate-template)
* [`forager offdesk shift show`↴](#forager-offdesk-shift-show)
* [`forager offdesk shift arm`↴](#forager-offdesk-shift-arm)
* [`forager offdesk shift status`↴](#forager-offdesk-shift-status)
* [`forager offdesk shift review`↴](#forager-offdesk-shift-review)
* [`forager offdesk shift retention-report`↴](#forager-offdesk-shift-retention-report)
* [`forager offdesk shift council-packet`↴](#forager-offdesk-shift-council-packet)
* [`forager offdesk shift disarm`↴](#forager-offdesk-shift-disarm)
* [`forager offdesk shift routine-claim`↴](#forager-offdesk-shift-routine-claim)
* [`forager offdesk shift routine-start`↴](#forager-offdesk-shift-routine-start)
* [`forager offdesk shift routine-complete`↴](#forager-offdesk-shift-routine-complete)
* [`forager offdesk shift routine-recover`↴](#forager-offdesk-shift-routine-recover)
* [`forager offdesk tasks`↴](#forager-offdesk-tasks)
* [`forager offdesk task-recovery`↴](#forager-offdesk-task-recovery)
* [`forager offdesk decisions`↴](#forager-offdesk-decisions)
* [`forager offdesk decision`↴](#forager-offdesk-decision)
* [`forager offdesk decision show`↴](#forager-offdesk-decision-show)
* [`forager offdesk decision resolve`↴](#forager-offdesk-decision-resolve)
* [`forager offdesk decision receipt`↴](#forager-offdesk-decision-receipt)
* [`forager offdesk decision ingest-telegram`↴](#forager-offdesk-decision-ingest-telegram)
* [`forager offdesk decision ingest-telegram-feedback`↴](#forager-offdesk-decision-ingest-telegram-feedback)
* [`forager offdesk provider-capacity`↴](#forager-offdesk-provider-capacity)
* [`forager offdesk provider-fallback`↴](#forager-offdesk-provider-fallback)
* [`forager offdesk cancel-task`↴](#forager-offdesk-cancel-task)
* [`forager offdesk terminate-task`↴](#forager-offdesk-terminate-task)
* [`forager offdesk pause`↴](#forager-offdesk-pause)
* [`forager offdesk unpause`↴](#forager-offdesk-unpause)
* [`forager offdesk pause-status`↴](#forager-offdesk-pause-status)
* [`forager offdesk learning-scan`↴](#forager-offdesk-learning-scan)
* [`forager offdesk retry-task`↴](#forager-offdesk-retry-task)
* [`forager offdesk resume-task`↴](#forager-offdesk-resume-task)
* [`forager offdesk abandon-task`↴](#forager-offdesk-abandon-task)
* [`forager offdesk poll`↴](#forager-offdesk-poll)
* [`forager offdesk ok`↴](#forager-offdesk-ok)
* [`forager offdesk cancel`↴](#forager-offdesk-cancel)
* [`forager offdesk resume`↴](#forager-offdesk-resume)
* [`forager offdesk background`↴](#forager-offdesk-background)
* [`forager offdesk background-ack`↴](#forager-offdesk-background-ack)
* [`forager offdesk capabilities`↴](#forager-offdesk-capabilities)
* [`forager offdesk snapshots`↴](#forager-offdesk-snapshots)
* [`forager offdesk snapshot`↴](#forager-offdesk-snapshot)
* [`forager offdesk restore-plan`↴](#forager-offdesk-restore-plan)
* [`forager offdesk debug-bundle`↴](#forager-offdesk-debug-bundle)
* [`forager offdesk maintenance-report`↴](#forager-offdesk-maintenance-report)
* [`forager offdesk maintenance-request`↴](#forager-offdesk-maintenance-request)
* [`forager offdesk deck`↴](#forager-offdesk-deck)
* [`forager offdesk closeout`↴](#forager-offdesk-closeout)
* [`forager offdesk closeout-review`↴](#forager-offdesk-closeout-review)
* [`forager offdesk closeout-decision`↴](#forager-offdesk-closeout-decision)
* [`forager offdesk closeout-retire`↴](#forager-offdesk-closeout-retire)
* [`forager offdesk review-link`↴](#forager-offdesk-review-link)
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
* [`forager offdesk wiki brief`↴](#forager-offdesk-wiki-brief)
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
* [`forager offdesk wiki record-candidate`↴](#forager-offdesk-wiki-record-candidate)
* [`forager offdesk wiki promote`↴](#forager-offdesk-wiki-promote)
* [`forager offdesk wiki reject`↴](#forager-offdesk-wiki-reject)
* [`forager offdesk wiki rescope`↴](#forager-offdesk-wiki-rescope)
* [`forager offdesk wiki edit`↴](#forager-offdesk-wiki-edit)
* [`forager offdesk wiki add-tag`↴](#forager-offdesk-wiki-add-tag)
* [`forager offdesk wiki deprecate`↴](#forager-offdesk-wiki-deprecate)
* [`forager offdesk wiki renew-review-after`↴](#forager-offdesk-wiki-renew-review-after)
* [`forager offdesk wiki add-counterexample`↴](#forager-offdesk-wiki-add-counterexample)
* [`forager offdesk wiki update-runbook`↴](#forager-offdesk-wiki-update-runbook)
* [`forager ondesk`↴](#forager-ondesk)
* [`forager ondesk plan-inspect`↴](#forager-ondesk-plan-inspect)
* [`forager ondesk prepare-offdesk`↴](#forager-ondesk-prepare-offdesk)
* [`forager ondesk plan-adopt-proposal`↴](#forager-ondesk-plan-adopt-proposal)
* [`forager ondesk plan-apply`↴](#forager-ondesk-plan-apply)
* [`forager ondesk plan-review-proposal`↴](#forager-ondesk-plan-review-proposal)
* [`forager ondesk return-binding`↴](#forager-ondesk-return-binding)
* [`forager ondesk note`↴](#forager-ondesk-note)
* [`forager ondesk capture`↴](#forager-ondesk-capture)
* [`forager ondesk prompt-package`↴](#forager-ondesk-prompt-package)
* [`forager ondesk return`↴](#forager-ondesk-return)
* [`forager ondesk return launch-restricted`↴](#forager-ondesk-return-launch-restricted)
* [`forager ondesk return preview`↴](#forager-ondesk-return-preview)
* [`forager ondesk return send`↴](#forager-ondesk-return-send)
* [`forager ondesk return observe`↴](#forager-ondesk-return-observe)
* [`forager ondesk return status`↴](#forager-ondesk-return-status)
* [`forager ondesk review-surface`↴](#forager-ondesk-review-surface)
* [`forager ondesk workstation-surface`↴](#forager-ondesk-workstation-surface)
* [`forager ondesk action-envelope`↴](#forager-ondesk-action-envelope)
* [`forager ondesk accepted-truth-recovery-envelope`↴](#forager-ondesk-accepted-truth-recovery-envelope)
* [`forager ondesk action-preflight`↴](#forager-ondesk-action-preflight)
* [`forager ondesk action-decision`↴](#forager-ondesk-action-decision)
* [`forager ondesk action-closeout`↴](#forager-ondesk-action-closeout)
* [`forager ondesk runtime-preflight`↴](#forager-ondesk-runtime-preflight)
* [`forager ondesk runtime-dispatch`↴](#forager-ondesk-runtime-dispatch)
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

* `add` - Add a new session
* `go` - Find-or-create a session for this directory, refresh its wiki brief, and attach
* `init` - Initialize .forager/config.toml in a repository
* `list` - List all sessions
* `remove` - Remove a session
* `status` - Show session status summary
* `doctor` - Diagnose Forager paths, profile env, and legacy AoE compatibility state
* `migrate` - Migrate legacy AoE compatibility paths
* `session` - Manage session lifecycle (start, stop, attach, etc.)
* `group` - Manage groups for organizing sessions
* `profile` - Manage profiles (separate workspaces)
* `project` - Initialize and inspect project operation packets
* `worktree` - Manage git worktrees for parallel development
* `offdesk` - Manage offdesk approvals and recovery artifacts
* `ondesk` - Capture ondesk notes and prompt context from external harness work
* `tmux` - tmux integration utilities
* `sounds` - Manage sound effects for agent state transitions
* `uninstall` - Uninstall Forager
* `completion` - Generate shell completions

###### **Options:**

* `-p`, `--profile <PROFILE>` - Profile to use (separate workspace with its own sessions)



## `forager add`

Add a new session

**Usage:** `forager add [OPTIONS] [PATH]`

###### **Arguments:**

* `<PATH>` - Project directory (defaults to current directory)

  Default value: `.`

###### **Options:**

* `-t`, `--title <TITLE>` - Session title (defaults to folder name)
* `-g`, `--group <GROUP>` - Group path (defaults to parent folder)
* `-c`, `--cmd <COMMAND>` - Command to run (e.g., 'claude', 'opencode', 'vibe', 'codex', 'gemini')
* `-P`, `--parent <PARENT>` - Parent session (creates sub-session, inherits group)
* `-l`, `--launch` - Launch the session immediately after creating
* `-w`, `--worktree <WORKTREE_BRANCH>` - Create session in a git worktree for the specified branch
* `-b`, `--new-branch` - Create a new branch (use with --worktree)
* `-y`, `--yolo` - Enable YOLO mode (skip permission prompts)
* `--trust-hooks` - Automatically trust repository hooks without prompting



## `forager go`

Find-or-create a session for this directory, refresh its wiki brief, and attach

**Usage:** `forager go [OPTIONS] [TOOL] [TOOL_ARGS]...`

###### **Arguments:**

* `<TOOL>` - Agent tool to run (e.g. 'claude', 'codex', 'gemini', 'opencode'). Defaults to effective `session.default_tool`, else 'claude'
* `<TOOL_ARGS>` - Extra arguments appended to the tool command (after `--`), e.g. `forager go claude -- --continue`

###### **Options:**

* `--path <PATH>` - Project directory (defaults to current directory)

  Default value: `.`
* `-y`, `--yolo` - Enable YOLO mode when creating a new session
* `--trust-hooks` - Automatically trust repository hooks without prompting
* `--register` - Register this directory in the project registry before launching. The project key (see --key) doubles as the wiki profile, so a fresh knowledge plane comes up with the project
* `--key <KEY>` - Project key for --register (default: slugified folder name)
* `--no-brief` - Skip the wiki brief refresh
* `--no-attach` - Create/start the session but do not attach (for scripts and tests)



## `forager init`

Initialize .forager/config.toml in a repository

**Usage:** `forager init [PATH]`

###### **Arguments:**

* `<PATH>` - Directory to initialize (defaults to current directory)

  Default value: `.`



## `forager list`

List all sessions

**Usage:** `forager list [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON
* `--all` - List sessions from all profiles



## `forager remove`

Remove a session

**Usage:** `forager remove [OPTIONS] <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title to remove

###### **Options:**

* `--delete-worktree` - Delete worktree directory (default: keep worktree)
* `--force` - Force worktree removal even with untracked/modified files
* `--keep-container` - Keep legacy sandbox container instead of deleting it (default: delete per config)



## `forager status`

Show session status summary

**Usage:** `forager status [OPTIONS]`

###### **Options:**

* `-v`, `--verbose` - Show detailed session list
* `-q`, `--quiet` - Only output waiting count (for scripts)
* `--json` - Output as JSON
* `--notification-capture` - Include identity-checked terminal text for waiting notifications (sensitive)



## `forager doctor`

Diagnose Forager paths, profile env, and legacy AoE compatibility state

**Usage:** `forager doctor [OPTIONS]`

###### **Options:**

* `--project <PATH>` - Repository path to inspect for .forager/.aoe config

  Default value: `.`
* `--json` - Output as JSON



## `forager migrate`

Migrate legacy AoE compatibility paths

**Usage:** `forager migrate <COMMAND>`

###### **Subcommands:**

* `aoe` - Copy legacy AoE paths into Forager primary paths



## `forager migrate aoe`

Copy legacy AoE paths into Forager primary paths

**Usage:** `forager migrate aoe [OPTIONS]`

###### **Options:**

* `--project <PATH>` - Repository path to inspect for .aoe/.forager config

  Default value: `.`
* `--dry-run` - Show the migration plan without copying files
* `--json` - Output as JSON



## `forager session`

Manage session lifecycle (start, stop, attach, etc.)

**Usage:** `forager session <COMMAND>`

###### **Subcommands:**

* `start` - Start a session's tmux process
* `stop` - Stop session process
* `restart` - Restart session
* `attach` - Attach to session interactively
* `show` - Show session details
* `rename` - Rename a session
* `current` - Auto-detect current session
* `context-status` - Inspect a session's wiki brief and presentation freshness without writes
* `context-sync` - Explicitly refresh and present wiki context to one prompt-ready session



## `forager session start`

Start a session's tmux process

**Usage:** `forager session start <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title



## `forager session stop`

Stop session process

**Usage:** `forager session stop <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title



## `forager session restart`

Restart session

**Usage:** `forager session restart <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title



## `forager session attach`

Attach to session interactively

**Usage:** `forager session attach <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title



## `forager session show`

Show session details

**Usage:** `forager session show [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title (optional, auto-detects in tmux)

###### **Options:**

* `--json` - Output as JSON



## `forager session rename`

Rename a session

**Usage:** `forager session rename [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title (optional, auto-detects in tmux)

###### **Options:**

* `-t`, `--title <TITLE>` - New title for the session
* `-g`, `--group <GROUP>` - New group for the session (empty string to ungroup)



## `forager session current`

Auto-detect current session

**Usage:** `forager session current [OPTIONS]`

###### **Options:**

* `-q`, `--quiet` - Just session name (for scripting)
* `--json` - Output as JSON



## `forager session context-status`

Inspect a session's wiki brief and presentation freshness without writes

**Usage:** `forager session context-status [OPTIONS] <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Exact session ID, unique prefix, title, or project path

###### **Options:**

* `--json` - Output the read-only status envelope as JSON



## `forager session context-sync`

Explicitly refresh and present wiki context to one prompt-ready session

**Usage:** `forager session context-sync [OPTIONS] <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Exact session ID, unique prefix, title, or project path

###### **Options:**

* `--agent-mode <AGENT_MODE>` - Work mode used to select reviewed wiki entries

  Default value: `shared`

  Possible values: `shared`, `planning`, `development`, `analysis`, `writing`, `critique`, `review`, `maintenance`

* `--require-fresh` - Refuse to use the last exact brief if refresh fails
* `--json` - Output the sync receipt and resulting status as JSON



## `forager group`

Manage groups for organizing sessions

**Usage:** `forager group <COMMAND>`

###### **Subcommands:**

* `list` - List all groups
* `create` - Create a new group
* `delete` - Delete a group
* `move` - Move session to group



## `forager group list`

List all groups

**Usage:** `forager group list [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager group create`

Create a new group

**Usage:** `forager group create [OPTIONS] <NAME>`

###### **Arguments:**

* `<NAME>` - Group name

###### **Options:**

* `--parent <PARENT>` - Parent group for creating subgroups



## `forager group delete`

Delete a group

**Usage:** `forager group delete [OPTIONS] <NAME>`

###### **Arguments:**

* `<NAME>` - Group name

###### **Options:**

* `--force` - Force delete by moving sessions to default group



## `forager group move`

Move session to group

**Usage:** `forager group move <IDENTIFIER> <GROUP>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title
* `<GROUP>` - Target group



## `forager profile`

Manage profiles (separate workspaces)

**Usage:** `forager profile [COMMAND]`

###### **Subcommands:**

* `list` - List all profiles
* `create` - Create a new profile
* `delete` - Delete a profile
* `default` - Show or set default profile



## `forager profile list`

List all profiles

**Usage:** `forager profile list`



## `forager profile create`

Create a new profile

**Usage:** `forager profile create <NAME>`

###### **Arguments:**

* `<NAME>` - Profile name



## `forager profile delete`

Delete a profile

**Usage:** `forager profile delete <NAME>`

###### **Arguments:**

* `<NAME>` - Profile name



## `forager profile default`

Show or set default profile

**Usage:** `forager profile default [NAME]`

###### **Arguments:**

* `<NAME>` - Profile name (optional, shows current if not provided)



## `forager project`

Initialize and inspect project operation packets

**Usage:** `forager project <COMMAND>`

###### **Subcommands:**

* `init` - Create a read-only project operation initialization packet
* `plan-routing` - Preview or apply exact-hash-reviewed owning TODO and AGENTS routing
* `apply-governance-hints` - Apply reviewed governance surface templates to a project
* `wiki-context` - Lint or apply reviewed local-agent wiki context discovery markers
* `audit-docs` - Audit documentation and human-facing artifact governance surfaces
* `artifact-index` - Build a read-only project/profile artifact index
* `document-catalog` - Build a bounded read-only document catalog without storing state
* `document-search` - Search a fresh visibility-neutral document catalog
* `document-lifecycle-review` - Review one exact document lifecycle proposal without writing state
* `document-lifecycle-request` - Open one proposal-hash-bound lifecycle approval request
* `document-lifecycle-apply` - Apply one approved lifecycle proposal to the append-only profile ledger
* `document-lifecycle-history` - Inspect append-only lifecycle history without opening document bodies
* `retention-review` - Build a read-only artifact retention review packet
* `retention-request` - Create an approval-only artifact retention follow-up request
* `retention-apply` - Consume an approved artifact retention decision into a profile receipt
* `retention-promote` - Promote a retained artifact into DELIVERABLES.md with snapshot evidence
* `implementation-packet` - Draft a design-first implementation packet before delegated execution
* `sync` - Scan workspace roots and bulk-register unmatched projects into the registry
* `inventory` - Account for workspace paths and registry entries without writing state



## `forager project init`

Create a read-only project operation initialization packet

**Usage:** `forager project init [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to initialize for Forager operation

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used by Ondesk, Offdesk, and adaptive wiki records
* `--owning-plan <OWNING_PLAN>` - Existing authoritative TODO, relative to this project root (explicit selection)
* `--operation-target <MODULE_PATH_OR_ID>` - Module path/id to mark as a prioritized operation target
* `--subproject-spec <PATH>` - Explicit logical subproject scope spec. Repeat for several subprojects
* `--module-profile-spec <PATH>` - Project-specific module operation profile spec. Repeat to register multiple specs
* `--out <OUT>` - Write the initialization packet to this directory
* `--include-git` - Include read-only git branch/status/diff-stat evidence
* `--force` - Overwrite known initialization files when --out already contains files
* `--json` - Output machine-readable JSON



## `forager project plan-routing`

Preview or apply exact-hash-reviewed owning TODO and AGENTS routing

**Usage:** `forager project plan-routing [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` - Explicit project root; only bounded documentation discovery is performed

###### **Options:**

* `--project-key <PROJECT_KEY>`
* `--owning-plan <OWNING_PLAN>` - Explicit existing owning TODO, relative to the project root
* `--subproject-spec <SUBPROJECT_SPEC>` - Select one previously declared logical scope instead of the whole project
* `--document-route <ROLE=PATH>` - Existing document location: working_notes, evidence, reports, or canonical=PATH
* `--create-plan` - Preview establishment of an explicitly named missing plan (one file only)
* `--reviewed` - Apply the exact reviewed preview; planning never authorizes Offdesk work
* `--expected-preview-sha256 <EXPECTED_PREVIEW_SHA256>`
* `--json`



## `forager project apply-governance-hints`

Apply reviewed governance surface templates to a project

**Usage:** `forager project apply-governance-hints [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to update

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key to render into newly created surfaces
* `--surface <SURFACE>` - Surface role to create. Repeat to limit scope; defaults to all missing surfaces

  Possible values: `current-state`, `next-actions`, `decisions`, `deliverables`

* `--reviewed` - Confirm that the operator reviewed the hints and approves creating missing files
* `--json` - Output machine-readable JSON



## `forager project wiki-context`

Lint or apply reviewed local-agent wiki context discovery markers

**Usage:** `forager project wiki-context [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to inspect

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key expected in the generated wiki brief
* `--include-claude` - Also preview or update an existing regular CLAUDE.md; create it if missing
* `--manage-gitignore` - Preview or add the managed .wiki-brief.md rule to .gitignore
* `--reviewed` - Confirm review of the exact diff and apply selected marker changes
* `--json` - Output machine-readable JSON



## `forager project audit-docs`

Audit documentation and human-facing artifact governance surfaces

**Usage:** `forager project audit-docs [OPTIONS] <PATH>`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to audit

###### **Options:**

* `--audit-profile <AUDIT_PROFILE>` - Governance profile to apply

  Default value: `standard`

  Possible values: `light`, `standard`, `research-longrun`

* `--adaptive-profile-dir <ADAPTIVE_PROFILE_DIR>` - Optional profile directory containing adaptive wiki state
* `--current-stale-days <CURRENT_STALE_DAYS>` - Allowed day gap before the current-state surface is considered stale

  Default value: `0`
* `--large-log-lines <LARGE_LOG_LINES>` - Line threshold for large-log warnings

  Default value: `1000`
* `--json` - Output machine-readable JSON to stdout
* `--json-out <JSON_OUT>` - Write JSON report to this path
* `--md-out <MD_OUT>` - Write Markdown report to this path



## `forager project artifact-index`

Build a read-only project/profile artifact index

**Usage:** `forager project artifact-index [OPTIONS] [PATH]`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to scan. Defaults to the current directory

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used to filter profile-local Forager artifacts
* `--json` - Output machine-readable JSON



## `forager project document-catalog`

Build a bounded read-only document catalog without storing state

**Usage:** `forager project document-catalog [OPTIONS] <PATH>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root to scan

###### **Options:**

* `--project-key <PROJECT_KEY>` - Project key for this ephemeral projection
* `--max-files <MAX_FILES>` - Maximum supported document files to observe

  Default value: `10000`
* `--max-directory-entries <MAX_DIRECTORY_ENTRIES>` - Maximum filesystem entries to enumerate across all directories

  Default value: `100000`
* `--max-total-bytes <MAX_TOTAL_BYTES>` - Maximum total document bytes to hash and extract

  Default value: `67108864`
* `--max-file-bytes <MAX_FILE_BYTES>` - Maximum bytes to read from any one document

  Default value: `8388608`
* `--max-elapsed-ms <MAX_ELAPSED_MS>` - Maximum elapsed scan time in milliseconds

  Default value: `5000`
* `--lifecycle` - Join the active profile lifecycle ledger and emit document_catalog.v2
* `--json` - Output machine-readable JSON



## `forager project document-search`

Search a fresh visibility-neutral document catalog

**Usage:** `forager project document-search [OPTIONS] <PATH>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root to scan live

###### **Options:**

* `--query <QUERY>` - Path and supported-text query
* `--observation-id <OBSERVATION_ID>` - Inspect one observation from the current live catalog
* `--document-id <DOCUMENT_ID>` - Inspect one durable lifecycle document from the current live catalog
* `--relative-path <RELATIVE_PATH>` - Inspect one exact bounded project-relative path
* `--lifecycle` - Join the active profile lifecycle ledger and emit document_search.v2
* `--include-cold` - Return reviewed cold query matches instead of omitting them
* `--project-key <PROJECT_KEY>` - Project key for this ephemeral projection
* `--max-files <MAX_FILES>` - Maximum supported document files to observe

  Default value: `10000`
* `--max-directory-entries <MAX_DIRECTORY_ENTRIES>` - Maximum filesystem entries to enumerate across all directories

  Default value: `100000`
* `--max-total-bytes <MAX_TOTAL_BYTES>` - Maximum total document bytes to hash and extract

  Default value: `67108864`
* `--max-file-bytes <MAX_FILE_BYTES>` - Maximum bytes to read from any one document

  Default value: `8388608`
* `--max-elapsed-ms <MAX_ELAPSED_MS>` - Maximum elapsed scan time in milliseconds

  Default value: `5000`
* `--json` - Output machine-readable JSON



## `forager project document-lifecycle-review`

Review one exact document lifecycle proposal without writing state

**Usage:** `forager project document-lifecycle-review [OPTIONS] --project-key <PROJECT_KEY> --reason <REASON> <PATH> <TRANSITION>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root
* `<TRANSITION>` - Lifecycle event class to review

  Possible values: `mint-document`, `activity-transition`, `revalidate-observation`, `rebind-observation`


###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key bound into the proposal and ledger
* `--observation-id <OBSERVATION_ID>` - Exact current observation selector, required for mint
* `--document-id <DOCUMENT_ID>` - Durable document selector, required for an existing document
* `--activity-state <ACTIVITY_STATE>` - Reviewed activity for mint and activity transitions

  Possible values: `draft`, `candidate`, `active`, `review-due`, `superseded`, `retired`

* `--successor-document-id <SUCCESSOR_DOCUMENT_ID>` - Exact active successor for a superseded document
* `--active-owner-document-id <ACTIVE_OWNER_DOCUMENT_ID>` - Exact active owner for this document
* `--review-after <REVIEW_AFTER>` - Canonical UTC review deadline with a Z suffix
* `--reason <REASON>` - Operator-safe reason bound into the proposal hash
* `--json` - Output strict machine-readable JSON



## `forager project document-lifecycle-request`

Open one proposal-hash-bound lifecycle approval request

**Usage:** `forager project document-lifecycle-request [OPTIONS] --project-key <PROJECT_KEY> --reason <REASON> --expected-proposal-sha256 <EXPECTED_PROPOSAL_SHA256> <PATH> <TRANSITION>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root
* `<TRANSITION>` - Lifecycle event class to review

  Possible values: `mint-document`, `activity-transition`, `revalidate-observation`, `rebind-observation`


###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key bound into the proposal and ledger
* `--observation-id <OBSERVATION_ID>` - Exact current observation selector, required for mint
* `--document-id <DOCUMENT_ID>` - Durable document selector, required for an existing document
* `--activity-state <ACTIVITY_STATE>` - Reviewed activity for mint and activity transitions

  Possible values: `draft`, `candidate`, `active`, `review-due`, `superseded`, `retired`

* `--successor-document-id <SUCCESSOR_DOCUMENT_ID>` - Exact active successor for a superseded document
* `--active-owner-document-id <ACTIVE_OWNER_DOCUMENT_ID>` - Exact active owner for this document
* `--review-after <REVIEW_AFTER>` - Canonical UTC review deadline with a Z suffix
* `--reason <REASON>` - Operator-safe reason bound into the proposal hash
* `--expected-proposal-sha256 <EXPECTED_PROPOSAL_SHA256>` - Exact proposal hash returned by a fresh review
* `--request-id <REQUEST_ID>` - Optional bounded request identity
* `--task-id <TASK_ID>` - Optional bounded task identity
* `--json` - Output strict machine-readable JSON



## `forager project document-lifecycle-apply`

Apply one approved lifecycle proposal to the append-only profile ledger

**Usage:** `forager project document-lifecycle-apply [OPTIONS] --approval-id <APPROVAL_ID> <PATH>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root

###### **Options:**

* `--approval-id <APPROVAL_ID>` - Approval ID from the active profile's canonical approval ledger
* `--json` - Output strict machine-readable JSON



## `forager project document-lifecycle-history`

Inspect append-only lifecycle history without opening document bodies

**Usage:** `forager project document-lifecycle-history [OPTIONS] --project-key <PROJECT_KEY> --document-id <DOCUMENT_ID> <PATH>`

###### **Arguments:**

* `<PATH>` - One explicit bounded project root

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key bound into the lifecycle ledger
* `--document-id <DOCUMENT_ID>` - Exact durable document ID
* `--json` - Output strict machine-readable JSON



## `forager project retention-review`

Build a read-only artifact retention review packet

**Usage:** `forager project retention-review [OPTIONS] [PATH]`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to scan. Defaults to the current directory

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used to filter profile-local Forager artifacts
* `--document-id <DOCUMENT_ID>` - Resolve one exact durable document through the retention crosswalk
* `--json` - Output machine-readable JSON



## `forager project retention-request`

Create an approval-only artifact retention follow-up request

**Usage:** `forager project retention-request [OPTIONS] --project-key <PROJECT_KEY> --action <ACTION> [PATH]`

###### **Arguments:**

* `<PATH>` - Project repository/root directory to scan. Defaults to the current directory

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used for approval and audit correlation
* `--artifact-id <ARTIFACT_ID>` - Existing artifact_index/artifact_retention_review artifact id to request approval for
* `--path <PATH_FILTER>` - Artifact path or relative path to request approval for
* `--document-id <DOCUMENT_ID>` - Durable lifecycle document to request retention for
* `--summary-document-id <SUMMARY_DOCUMENT_ID>` - Exact active summary document required by summary-keep
* `--action <ACTION>` - Retention action to request approval for

  Possible values: `keep`, `preserve`, `summary-keep`, `promote`, `archive`, `quarantine`, `dispose`

* `--request-id <REQUEST_ID>` - Request ID for approval correlation

  Default value: `retention-review`
* `--task-id <TASK_ID>` - Override task ID used for approval deduplication
* `--reason <REASON>` - Extra operator-safe reason to include in the approval brief
* `--ttl-minutes <TTL_MINUTES>` - Pending approval TTL in minutes

  Default value: `30`
* `--json` - Output machine-readable JSON



## `forager project retention-apply`

Consume an approved artifact retention decision into a profile receipt

**Usage:** `forager project retention-apply [OPTIONS] --project-key <PROJECT_KEY> --approval-id <APPROVAL_ID> [PATH]`

###### **Arguments:**

* `<PATH>` - Project repository/root directory related to the approved retention decision. Defaults to the current directory

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used to validate the approved retention decision
* `--approval-id <APPROVAL_ID>` - Approved artifact retention approval ID to consume
* `--receipt-out <RECEIPT_OUT>` - Write the receipt to this path instead of the profile receipt directory
* `--json` - Output machine-readable JSON



## `forager project retention-promote`

Promote a retained artifact into DELIVERABLES.md with snapshot evidence

**Usage:** `forager project retention-promote [OPTIONS] --project-key <PROJECT_KEY> [PATH]`

###### **Arguments:**

* `<PATH>` - Project repository/root directory whose DELIVERABLES.md should be updated. Defaults to the current directory

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used to validate the retention application receipt
* `--receipt <RECEIPT>` - artifact_retention_application.v1 receipt path to promote from
* `--approval-id <APPROVAL_ID>` - Find the latest application receipt for this approval ID
* `--title <TITLE>` - Override the deliverables entry label
* `--reviewed` - Confirm that the artifact preview/provenance and deliverables mutation were reviewed
* `--json` - Output machine-readable JSON



## `forager project implementation-packet`

Draft a design-first implementation packet before delegated execution

**Usage:** `forager project implementation-packet [OPTIONS] --project-key <PROJECT_KEY> --goal <GOAL> --success-state <SUCCESS_STATE> <PATH>`

###### **Arguments:**

* `<PATH>` - Project repository/root directory the delegated work belongs to

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key used by Ondesk, Offdesk, and adaptive wiki records
* `--goal <GOAL>` - Original operator goal this packet must preserve
* `--success-state <SUCCESS_STATE>` - Concrete state that should be true when the work is complete
* `--why-now <WHY_NOW>` - Why this work matters now. Repeat to add multiple reasons
* `--north-star-fit <NORTH_STAR_FIT>` - How this serves Forager's north star: evidence, choices, and continuity
* `--brand-fit <BRAND_FIT>` - How this preserves the current product/brand boundary
* `--product-boundary <PRODUCT_BOUNDARY>` - What Forager owns and what the hosted worker owns
* `--scope <SCOPE>` - In-scope work item. Repeat for multiple slices of included scope
* `--exclude <EXCLUDE>` - Explicit non-goal or out-of-scope item. Repeat for multiple exclusions
* `--allowed-file <ALLOWED_FILE>` - File, directory, or contract the worker may inspect or edit
* `--mutation-boundary <MUTATION_BOUNDARY>` - Boundary for permitted mutation. Defaults to read-only packet drafting
* `--non-authorized-action <NON_AUTHORIZED_ACTION>` - Action this packet never authorizes by itself. Repeat for multiple actions
* `--capability <CAPABILITY>` - Affected functional capability, optionally as FD-016:reason
* `--approach <APPROACH>` - Intended implementation approach
* `--work-slice <WORK_SLICE>` - Worker execution slice. Repeat for multiple slices
* `--interface <INTERFACE>` - Interface affected or expected by the work. Repeat for multiple interfaces
* `--data-contract <DATA_CONTRACT>` - Data contract affected or expected by the work. Repeat for multiple contracts
* `--compatibility-note <COMPATIBILITY_NOTE>` - Compatibility note the worker must preserve
* `--preferred-worker <PREFERRED_WORKER>` - Preferred worker type, such as local_model, hosted_harness, or deterministic_script

  Default value: `hosted_harness`
* `--worker-requirement <WORKER_REQUIREMENT>` - Worker requirement that must be true before execution
* `--command <COMMAND>` - Command the worker or reviewer should run
* `--stop-condition <STOP_CONDITION>` - Condition where the worker must stop instead of continuing
* `--recovery-step <RECOVERY_STEP>` - Recovery or rollback instruction if execution fails or stalls
* `--validation-command <VALIDATION_COMMAND>` - Test or verification command required for closeout
* `--smoke-check <SMOKE_CHECK>` - Smoke check required for closeout
* `--manual-review <MANUAL_REVIEW>` - Manual review item required before acceptance
* `--evidence-ref <EVIDENCE_REF>` - Evidence ref or artifact required to support the result
* `--expected-artifact <EXPECTED_ARTIFACT>` - Artifact expected from execution
* `--handoff-requirement <HANDOFF_REQUIREMENT>` - Handoff summary requirement for morning Ondesk review
* `--reviewer <REVIEWER>` - Reviewer route used for the recursive alignment review

  Default value: `deterministic_gate`
* `--out <OUT>` - Write the packet artifacts to this directory
* `--force` - Overwrite known packet files when --out already contains files
* `--json` - Output machine-readable JSON



## `forager project sync`

Scan workspace roots and bulk-register unmatched projects into the registry

**Usage:** `forager project sync [OPTIONS] <ROOTS>...`

###### **Arguments:**

* `<ROOTS>` - Workspace root directories to scan (immediate children only)

###### **Options:**

* `--apply` - Write the new entries to the registry (default: dry-run report only)
* `--no-wiki` - Do not assign a wiki plane to auto-registered projects



## `forager project inventory`

Account for workspace paths and registry entries without writing state

**Usage:** `forager project inventory [OPTIONS] <ROOTS>...`

###### **Arguments:**

* `<ROOTS>` - Explicit workspace roots whose children should be accounted for

###### **Options:**

* `--registry <PATH>` - Registry to inspect; defaults to the existing project registry
* `--max-depth <MAX_DEPTH>` - Directory levels inspected through marker-free containers only

  Default value: `2`
* `--max-entries <MAX_ENTRIES>` - Maximum number of filesystem entries across all roots

  Default value: `4096`
* `--project-key <PROJECT_KEYS>` - Project keys to highlight; absence means inventory only, no selection
* `--no-selection` - Explicitly select no projects while still reporting inventory
* `--exclude <PATH>` - Explicitly excluded path; descendants are not inspected
* `--subproject-spec <PATH>` - Existing initialization subproject spec; parent must resolve uniquely
* `--json` - Output as JSON



## `forager worktree`

Manage git worktrees for parallel development

**Usage:** `forager worktree <COMMAND>`

###### **Subcommands:**

* `list` - List all worktrees in current repository
* `info` - Show worktree information for a session
* `cleanup` - Cleanup orphaned worktrees



## `forager worktree list`

List all worktrees in current repository

**Usage:** `forager worktree list`



## `forager worktree info`

Show worktree information for a session

**Usage:** `forager worktree info <IDENTIFIER>`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID or title



## `forager worktree cleanup`

Cleanup orphaned worktrees

**Usage:** `forager worktree cleanup [OPTIONS]`

###### **Options:**

* `-f`, `--force` - Actually remove worktrees (default is dry-run)



## `forager offdesk`

Manage offdesk approvals and recovery artifacts

**Usage:** `forager offdesk <COMMAND>`

###### **Subcommands:**

* `harnesses` - List hosted harness agent profile contracts
* `harness-prompt` - Build a compact hosted harness start prompt from first-read artifacts
* `plan` - Validate and register a read-only Offdesk planning artifact
* `plans` - List registered read-only Offdesk planning artifacts
* `plan-show` - Show one registered read-only Offdesk planning artifact
* `plan-review` - Record an operator review for a registered Offdesk planning artifact
* `plan-launch-prep` - Build a read-only launch-preparation packet from an approved plan review
* `remote-operator` - Render read-only Remote Operator projections for mobile/chat transports
* `remote-session` - Inspect remote interactive-session policy without opening launch authority
* `pending` - List pending action approvals
* `gate` - Evaluate whether an offdesk capability may execute now
* `launch` - Gate and record a background runner launch
* `enqueue` - Enqueue a durable offdesk task
* `tick` - Run one offdesk control-loop pass
* `shift` - Inspect bounded Offdesk shift authority and lifecycle
* `tasks` - Show durable offdesk tasks
* `task-recovery` - Inspect runtime release evidence without changing tasks or processes
* `decisions` - List canonical Offdesk decision records
* `decision` - Inspect one canonical Offdesk decision record
* `provider-capacity` - Show provider capacity cooldown state
* `provider-fallback` - Recommend provider/model fallbacks without retargeting tasks
* `cancel-task` - Mark a durable task cancelled without stopping its background runner
* `terminate-task` - Stop the verified local runtime and mark its durable task cancelled
* `pause` - Halt all new offdesk dispatch until resumed (existing runs keep polling)
* `unpause` - Clear the global operator pause so new dispatch can proceed again
* `pause-status` - Show the current global operator pause state
* `learning-scan` - Emit adaptive-wiki learning candidates from observed denials, failures, and resume-recovery rows (recommendation-only; runs each event once)
* `retry-task` - Requeue a failed, resume-pending, or cancelled durable task
* `resume-task` - Accept recovery for a resume-pending task and requeue it
* `abandon-task` - Discard a failed or resume-pending task
* `poll` - Poll background runner probes, persist phase transitions, and reconcile task status
* `ok` - Approve the oldest or targeted pending action
* `cancel` - Deny the oldest or targeted pending action
* `resume` - Show task resume artifacts
* `background` - Show background runner recovery probes
* `background-ack` - Acknowledge a stale or failed background probe after linked tasks are cancelled
* `capabilities` - Show Task Team capability metadata
* `snapshots` - List pre-mutation checkpoint snapshots
* `snapshot` - Show and verify a pre-mutation checkpoint snapshot
* `restore-plan` - Show a dry-run rollback plan without modifying files
* `debug-bundle` - Emit a sanitized read-only debug bundle
* `maintenance-report` - Summarize read-only Offdesk maintenance risks
* `maintenance-request` - Create or reuse an approval request for a maintenance action
* `deck` - Generate a Marp-compatible review deck from a read-only Offdesk artifact
* `closeout` - Generate a mandatory closeout plan and commercial review packet
* `closeout-review` - Record a reviewed closeout verdict without applying file operations
* `closeout-decision` - Resolve a closeout receipt open decision without applying file operations
* `closeout-retire` - Retire an evidence-incomplete historical closeout without accepting truth
* `review-link` - Inspect or explicitly record an exact existing-decision link to a completed runtime
* `wiki` - Inspect adaptive wiki candidates, entries, projections, and lint



## `forager offdesk harnesses`

List hosted harness agent profile contracts

**Usage:** `forager offdesk harnesses [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk harness-prompt`

Build a compact hosted harness start prompt from first-read artifacts

**Usage:** `forager offdesk harness-prompt [OPTIONS] --task <TASK> <HARNESS_ID>`

###### **Arguments:**

* `<HARNESS_ID>` - Hosted harness ID from `forager offdesk harnesses`

###### **Options:**

* `--task <TASK>` - Short task instruction for the hosted harness
* `--first-read <FIRST_READS>` - Artifact or source file the hosted harness must read first
* `--result-artifact <RESULT_ARTIFACT>` - Result sidecar path the hosted harness should write or inspect
* `--workdir <WORKDIR>` - Working directory the hosted harness should treat as the task root
* `--output <OUTPUT>` - Write the generated prompt markdown to this path
* `--max-first-read-total-bytes <MAX_FIRST_READ_TOTAL_BYTES>` - Override the total first-read artifact budget in bytes
* `--strict-first-read-budget` - Fail when first-read artifacts are missing or exceed the budget
* `--json` - Output packet metadata as JSON



## `forager offdesk plan`

Validate and register a read-only Offdesk planning artifact

**Usage:** `forager offdesk plan [OPTIONS] <INPUT>`

###### **Arguments:**

* `<INPUT>` - `offdesk_multiturn_plan.v1` or `offdesk_planner_council.v1` JSON to register

###### **Options:**

* `--project-key <PROJECT_KEY>` - Optional project key for correlation
* `--request-id <REQUEST_ID>` - Optional request ID for correlation
* `--task-id <TASK_ID>` - Optional task ID for correlation
* `--dry-run` - Validate without writing profile-local registry artifacts
* `--json` - Output as JSON



## `forager offdesk plans`

List registered read-only Offdesk planning artifacts

**Usage:** `forager offdesk plans [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Filter by project key
* `--task-id <TASK_ID>` - Filter by task ID
* `--profile-key <PROFILE_KEY>` - Filter by planning profile key
* `--artifact-kind <ARTIFACT_KIND>` - Filter by artifact kind, such as offdesk_multiturn_plan or offdesk_planner_council
* `--latest` - Return only the newest matching registration
* `--json` - Output as JSON



## `forager offdesk plan-show`

Show one registered read-only Offdesk planning artifact

**Usage:** `forager offdesk plan-show [OPTIONS] <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Plan ID from `forager offdesk plans`, or a registration/source path

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk plan-review`

Record an operator review for a registered Offdesk planning artifact

**Usage:** `forager offdesk plan-review [OPTIONS] --decision <DECISION> --reason <REASON> <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Plan ID from `forager offdesk plans`, or a registration/source path

###### **Options:**

* `--decision <DECISION>` - Operator review decision. This command never enqueues or launches work

  Possible values: `approved`, `revision-required`, `rejected`

* `--reviewer <REVIEWER>` - Reviewer or reviewing model label

  Default value: `operator`
* `--review-provider <REVIEW_PROVIDER>` - Model/provider label used for review
* `--review-file <REVIEW_FILE>` - Optional path to the raw review output
* `--reason <REASON>` - Required review rationale. Secrets are redacted before persistence
* `--blocker <BLOCKERS>` - Blocking issue reported by review; may be passed multiple times
* `--follow-up <FOLLOWUPS>` - Follow-up requested by review; may be passed multiple times
* `--json` - Output as JSON



## `forager offdesk plan-launch-prep`

Build a read-only launch-preparation packet from an approved plan review

**Usage:** `forager offdesk plan-launch-prep [OPTIONS] <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Plan ID from `forager offdesk plans`, or a registration/source path

###### **Options:**

* `--review-id <REVIEW_ID>` - Use a specific approved review ID instead of the latest review
* `--prepared-by <PREPARED_BY>` - Operator or surface preparing the packet

  Default value: `operator`
* `--notes <NOTES>` - Optional preparation note. Secrets are redacted before persistence
* `--json` - Output as JSON



## `forager offdesk remote-operator`

Render read-only Remote Operator projections for mobile/chat transports

**Usage:** `forager offdesk remote-operator <COMMAND>`

###### **Subcommands:**

* `status` - Render a read-only status projection for a remote operator surface
* `pending` - Render read-only pending approval summaries without resolving or expiring them
* `plans` - Render read-only Offdesk plan summaries for a remote operator surface
* `show` - Render one read-only Offdesk plan detail projection



## `forager offdesk remote-operator status`

Render a read-only status projection for a remote operator surface

**Usage:** `forager offdesk remote-operator status [OPTIONS]`

###### **Options:**

* `--transport <TRANSPORT>` - Remote transport label used for projection metadata

  Default value: `telegram`
* `--json` - Output as JSON



## `forager offdesk remote-operator pending`

Render read-only pending approval summaries without resolving or expiring them

**Usage:** `forager offdesk remote-operator pending [OPTIONS]`

###### **Options:**

* `--transport <TRANSPORT>` - Remote transport label used for projection metadata

  Default value: `telegram`
* `--all` - Include resolved approvals in addition to pending approval rows
* `--json` - Output as JSON



## `forager offdesk remote-operator plans`

Render read-only Offdesk plan summaries for a remote operator surface

**Usage:** `forager offdesk remote-operator plans [OPTIONS]`

###### **Options:**

* `--transport <TRANSPORT>` - Remote transport label used for projection metadata

  Default value: `telegram`
* `--project-key <PROJECT_KEY>` - Filter by project key
* `--task-id <TASK_ID>` - Filter by task ID
* `--profile-key <PROFILE_KEY>` - Filter by planning profile key
* `--artifact-kind <ARTIFACT_KIND>` - Filter by artifact kind, such as offdesk_multiturn_plan or offdesk_planner_council
* `--latest` - Return only the newest matching registration
* `--json` - Output as JSON



## `forager offdesk remote-operator show`

Render one read-only Offdesk plan detail projection

**Usage:** `forager offdesk remote-operator show [OPTIONS] <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Plan ID from `forager offdesk plans`, or a registration/source path

###### **Options:**

* `--transport <TRANSPORT>` - Remote transport label used for projection metadata

  Default value: `telegram`
* `--json` - Output as JSON



## `forager offdesk remote-session`

Inspect remote interactive-session policy without opening launch authority

**Usage:** `forager offdesk remote-session <COMMAND>`

###### **Subcommands:**

* `policy-inspect` - Validate and summarize one owner-only policy without reading profile state
* `policy-resolve` - Resolve one exact root and executable without creating a request



## `forager offdesk remote-session policy-inspect`

Validate and summarize one owner-only policy without reading profile state

**Usage:** `forager offdesk remote-session policy-inspect [OPTIONS] --policy <POLICY>`

###### **Options:**

* `--policy <POLICY>` - Owner-only remote session policy JSON file
* `--json` - Output as JSON



## `forager offdesk remote-session policy-resolve`

Resolve one exact root and executable without creating a request

**Usage:** `forager offdesk remote-session policy-resolve [OPTIONS] --policy <POLICY> --installation-boundary <INSTALLATION_BOUNDARY> --root-id <ROOT_ID> --launch-profile-id <LAUNCH_PROFILE_ID>`

###### **Options:**

* `--policy <POLICY>` - Owner-only remote session policy JSON file
* `--installation-boundary <INSTALLATION_BOUNDARY>` - Explicit absolute parent boundary containing every allowed project root
* `--root-id <ROOT_ID>` - Exact root ID from the policy
* `--launch-profile-id <LAUNCH_PROFILE_ID>` - Exact fixed launch profile ID authorized for the root
* `--json` - Output as JSON



## `forager offdesk pending`

List pending action approvals

**Usage:** `forager offdesk pending [OPTIONS]`

###### **Options:**

* `--all` - Include resolved and expired approvals
* `--json` - Output as JSON



## `forager offdesk gate`

Evaluate whether an offdesk capability may execute now

**Usage:** `forager offdesk gate [OPTIONS] --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --task-id <TASK_ID> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` - Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` - Request ID for approval and audit correlation
* `--task-id <TASK_ID>` - Task ID for approval and audit correlation
* `--mutation-class <MUTATION_CLASS>` - Mutation class to match against an ExecutionBrief envelope
* `--brief <BRIEF>` - JSON file containing an ExecutionBrief
* `--provider-id <PROVIDER_ID>` - Provider ID to check against provider capacity cooldown state
* `--model <MODEL>` - Provider model to check against provider capacity cooldown state
* `--artifact <ARTIFACT_REFS>` - Artifact reference in ARTIFACT_ID=PATH form
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` - Agent work mode used to match adaptive wiki entries
* `--preview <PREVIEW>` - Operator-safe action preview

  Default value: ``
* `--reason <REASON>` - Reason shown when approval is required

  Default value: ``
* `--source-surface <SOURCE_SURFACE>` - Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` - Pending approval TTL in minutes

  Default value: `30`
* `--json` - Output as JSON



## `forager offdesk launch`

Gate and record a background runner launch

**Usage:** `forager offdesk launch [OPTIONS] --runner <RUNNER> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --task-id <TASK_ID> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` - Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--runner <RUNNER>` - Runner backend to record: local-tmux, local-background, github-runner, remote-worker
* `--project-key <PROJECT_KEY>` - Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` - Request ID for approval and audit correlation
* `--task-id <TASK_ID>` - Task ID for approval and audit correlation
* `--mutation-class <MUTATION_CLASS>` - Mutation class to match against an ExecutionBrief envelope
* `--brief <BRIEF>` - JSON file containing an ExecutionBrief
* `--provider-id <PROVIDER_ID>` - Provider ID to check against provider capacity cooldown state
* `--model <MODEL>` - Provider model to check against provider capacity cooldown state
* `--artifact <ARTIFACT_REFS>` - Artifact reference in ARTIFACT_ID=PATH form
* `--implementation-packet <IMPLEMENTATION_PACKET>` - Implementation packet JSON or artifact directory to bind to this launch
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` - Agent work mode used to match adaptive wiki entries
* `--ticket-id <TICKET_ID>` - Stable ticket ID. Generated if omitted
* `--launch-spec <LAUNCH_SPEC>` - Redacted launch spec summary to store with the ticket
* `--cmd <COMMAND>` - Shell command to execute for local-background or local-tmux runners
* `--workdir <WORKDIR>` - Working directory for --cmd. Defaults to the current directory
* `--log-artifact <LOG_ARTIFACT>` - Log artifact path for --cmd stdout and stderr
* `--result-artifact <RESULT_ARTIFACT>` - Result sidecar path used by poll to mark the ticket completed
* `--runtime-alive` - Whether a local runtime handle is alive immediately after launch

  Default value: `true`
* `--provider-launch-spec-reconstructable` - Whether a local_background launch spec can be reconstructed after restart
* `--ack-timeout-sec <ACK_TIMEOUT_SEC>` - External ack timeout in seconds

  Default value: `300`
* `--preview <PREVIEW>` - Operator-safe action preview

  Default value: ``
* `--reason <REASON>` - Reason shown when approval is required

  Default value: ``
* `--source-surface <SOURCE_SURFACE>` - Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` - Pending approval TTL in minutes

  Default value: `30`
* `--json` - Output as JSON



## `forager offdesk enqueue`

Enqueue a durable offdesk task

**Usage:** `forager offdesk enqueue [OPTIONS] --runner <RUNNER> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --cmd <COMMAND> <CAPABILITY_ID>`

###### **Arguments:**

* `<CAPABILITY_ID>` - Capability ID from `forager offdesk capabilities`

###### **Options:**

* `--runner <RUNNER>` - Runner backend to use: local-tmux or local-background
* `--project-key <PROJECT_KEY>` - Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` - Request ID for approval and audit correlation
* `--task-id <TASK_ID>` - Task ID. Generated if omitted
* `--cmd <COMMAND>` - Shell command to execute when the task is dispatched
* `--workdir <WORKDIR>` - Working directory for --cmd. Defaults to the current directory
* `--brief <BRIEF>` - JSON file containing an ExecutionBrief to store with the task
* `--mutation-class <MUTATION_CLASS>` - Mutation class to match against an ExecutionBrief envelope
* `--provider-id <PROVIDER_ID>` - Provider ID to check against provider capacity cooldown state when dispatched
* `--model <MODEL>` - Provider model to check against provider capacity cooldown state when dispatched
* `--artifact <ARTIFACT_REFS>` - Artifact reference in ARTIFACT_ID=PATH form
* `--implementation-packet <IMPLEMENTATION_PACKET>` - Implementation packet JSON or artifact directory to bind to this task
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind used to match adaptive wiki entries
* `--agent-mode <AGENT_MODE>` - Agent work mode used to match adaptive wiki entries
* `--preview <PREVIEW>` - Operator-safe action preview

  Default value: ``
* `--reason <REASON>` - Reason shown when approval is required

  Default value: ``
* `--not-before <NOT_BEFORE>` - Do not dispatch before this RFC3339 timestamp
* `--log-artifact <LOG_ARTIFACT>` - Log artifact path for command stdout and stderr
* `--result-artifact <RESULT_ARTIFACT>` - Result sidecar path used by tick to mark the task completed
* `--json` - Output as JSON



## `forager offdesk tick`

Run one offdesk control-loop pass

**Usage:** `forager offdesk tick [OPTIONS]`

###### **Options:**

* `--limit <LIMIT>` - Maximum queued tasks to dispatch in this tick

  Default value: `10`
* `--shift-id <SHIFT_ID>` - Consume one exact bounded shift. Defaults to the active shift when present
* `--project-key <PROJECT_KEY>` - Restrict this tick to one project key
* `--task-id <TASK_ID>` - Restrict this tick to one task ID
* `--lock-stale-minutes <LOCK_STALE_MINUTES>` - Treat previous free lock metadata as stale after this many minutes

  Default value: `30`
* `--notify-cooldown-minutes <NOTIFY_COOLDOWN_MINUTES>` - Record notification cooldown state in minutes while polling background runs
* `--json` - Output as JSON



## `forager offdesk shift`

Inspect bounded Offdesk shift authority and lifecycle

**Usage:** `forager offdesk shift <COMMAND>`

###### **Subcommands:**

* `start-review` - Review an already prepared shift and inert night preferences without writes
* `request-arm-review` - Request only a pending arm approval for an exact local review; never arm or dispatch
* `confirm-arm-review` - Confirm an exact reviewed arm approval and activate its shift; never tick or dispatch
* `candidates` - Inspect existing queued candidates in explicitly registered projects without writes
* `propose` - Propose an explicit serial selection with pinned owning TODOs, without writes
* `preview` - Read-only start preview for an exact reviewed plan and queued selection
* `enterprise-policies` - List canonical enterprise policy profiles without selecting one
* `host-policy-probe` - Probe one supported host isolation policy without granting authority
* `read-policy-probe` - Verify filesystem read restrictions without creating profile state or authority
* `prepare` - Bind one reviewed plan, exact tasks, roots, routines, window, and budget
* `instantiate-template` - Materialize one daily or weekly slot as a fresh inert shift manifest
* `show` - Show one shift, or the latest shift when no ID is supplied
* `arm` - Create or consume a manifest-bound operator approval and arm the shift
* `status` - Summarize all bounded shifts without creating profile state
* `review` - Project one bounded shift into the shared read-only morning review
* `retention-report` - Classify preserved shift records and derived evidence without mutating either
* `council-packet` - Build a review-only Council comparison of goal, evidence, drift, and budget
* `disarm` - Terminate active shift authority without stopping already-running processes
* `routine-claim` - Select and reserve the one manifest-bound handler for a fixed timer task
* `routine-start` - Reserve one due typed routine occurrence before executing it
* `routine-complete` - Record the terminal outcome of one exact routine reservation
* `routine-recover` - Fail-safe close an unresolved routine after explicit operator acknowledgement



## `forager offdesk shift start-review`

Review an already prepared shift and inert night preferences without writes

**Usage:** `forager offdesk shift start-review [OPTIONS] <SHIFT_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact already prepared shift ID

###### **Options:**

* `--project-key <PROJECT_KEYS>` - One-run project scope; omitted uses saved global/profile night preferences
* `--approval-ttl-minutes <APPROVAL_TTL_MINUTES>` - One-run validity for a newly requested shift-arm approval
* `--json` - Output as JSON



## `forager offdesk shift request-arm-review`

Request only a pending arm approval for an exact local review; never arm or dispatch

**Usage:** `forager offdesk shift request-arm-review [OPTIONS] --expected-review-sha256 <EXPECTED_REVIEW_SHA256> <SHIFT_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact already prepared shift ID

###### **Options:**

* `--project-key <PROJECT_KEYS>` - One-run project scope; omitted uses saved global/profile night preferences
* `--approval-ttl-minutes <APPROVAL_TTL_MINUTES>` - One-run validity for a newly requested shift-arm approval
* `--json` - Output as JSON
* `--expected-review-sha256 <EXPECTED_REVIEW_SHA256>`



## `forager offdesk shift confirm-arm-review`

Confirm an exact reviewed arm approval and activate its shift; never tick or dispatch

**Usage:** `forager offdesk shift confirm-arm-review [OPTIONS] --expected-review-sha256 <EXPECTED_REVIEW_SHA256> --approval-id <APPROVAL_ID> --by <BY> <SHIFT_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact already prepared shift ID

###### **Options:**

* `--project-key <PROJECT_KEYS>` - One-run project scope; omitted uses saved global/profile night preferences
* `--approval-ttl-minutes <APPROVAL_TTL_MINUTES>` - One-run validity for a newly requested shift-arm approval
* `--json` - Output as JSON
* `--expected-review-sha256 <EXPECTED_REVIEW_SHA256>`
* `--approval-id <APPROVAL_ID>`
* `--by <BY>`
* `--acknowledge-unknown-task-usage` - Explicitly accept that child-task usage is unknown and no new spend cap is enforced



## `forager offdesk shift candidates`

Inspect existing queued candidates in explicitly registered projects without writes

**Usage:** `forager offdesk shift candidates [OPTIONS] --starts-at <STARTS_AT> --stops-at <STOPS_AT> --project-key <PROJECT_KEYS> <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Registered Offdesk plan ID or registration/source path

###### **Options:**

* `--review-id <REVIEW_ID>` - Use this approved plan review instead of the latest review
* `--shift-id <SHIFT_ID>` - Stable shift ID. Generated when omitted; instantiate-template rejects this override
* `--starts-at <STARTS_AT>` - Inclusive shift start as RFC3339
* `--stops-at <STOPS_AT>` - Inclusive shift stop as RFC3339, no more than 24 hours after start
* `--timezone <TIMEZONE>` - Display timezone recorded in the immutable manifest

  Default value: `Asia/Seoul`
* `--enterprise-policy <ENTERPRISE_POLICY>` - Canonical enterprise policy bound into the immutable manifest

  Default value: `single-owner-local`

  Possible values: `single-owner-local`, `multi-operator-local`, `airgapped-local`

* `--host-policy <HOST_POLICY>` - Require and bind one live-verified child-task host isolation policy

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`

* `--read-policy <READ_POLICY>` - Require filesystem read restrictions for every local-background or local-tmux task

  Possible values: `linux-landlock-workdir-and-runtime-only`, `linux-landlock-metadata-boot-identity`

* `--task-id <TASK_IDS>` - Exact queued task to bind. May be repeated
* `--serial-proposal-sha256 <SERIAL_PROPOSAL_SHA256>` - Require the exact freshly revalidated serial-selection proposal
* `--require-preparation-control <REQUIRED_PREPARATION_CONTROLS>` - Require an existing preparation control by ID; unsupported or unconfigured controls hold arm/launch. Repeat as needed
* `--weekly-usage-state <WEEKLY_USAGE_STATE>` - Existing guard state directly inside this profile; binds weekly baselines to this run
* `--weekly-gpt-sample <WEEKLY_GPT_SAMPLE>` - Fresh normalized GPT weekly sample inside this profile, without automatic polling
* `--weekly-claude-sample <WEEKLY_CLAUDE_SAMPLE>` - Fresh normalized Claude weekly sample inside this profile
* `--weekly-max-age-seconds <WEEKLY_MAX_AGE_SECONDS>` - Maximum age of manually supplied weekly samples, at most 300 seconds

  Default value: `300`
* `--serial-project-key <SERIAL_PROJECT_KEYS>` - Registered project scope used to produce the serial proposal; repeat in the same order
* `--dispatch-interval-minutes <DISPATCH_INTERVAL_MINUTES>` - Add a dispatch-tick routine over the full shift window
* `--wiki-project-key <WIKI_PROJECT_KEY>` - Project key for one candidate-only Wiki distillation routine
* `--wiki-profile <WIKI_PROFILE>` - Wiki profile for the distillation routine
* `--wiki-root <WIKI_ROOT>` - Exact project root for the Wiki distillation routine
* `--wiki-at <WIKI_AT>` - One-time Wiki distillation time as RFC3339
* `--morning-review-at <MORNING_REVIEW_AT>` - Add a one-time morning review brief routine at this RFC3339 time
* `--max-concurrent-tasks <MAX_CONCURRENT_TASKS>` - Maximum simultaneously running bound tasks. Defaults to one when tasks exist
* `--max-attempts-per-task <MAX_ATTEMPTS_PER_TASK>` - Maximum attempts per bound task

  Default value: `1`
* `--max-routine-process-starts <MAX_ROUTINE_PROCESS_STARTS>` - Maximum child processes started by fixed routine handlers
* `--max-routine-external-calls <MAX_ROUTINE_EXTERNAL_CALLS>` - Maximum network calls attempted by fixed routine handlers
* `--max-routine-disk-bytes <MAX_ROUTINE_DISK_BYTES>` - Maximum durable evidence bytes written by fixed routine handlers

  Default value: `33554432`
* `--max-routine-llm-tokens <MAX_ROUTINE_LLM_TOKENS>` - Maximum total local-LLM prompt plus output tokens
* `--max-routine-cost-micro-usd <MAX_ROUTINE_COST_MICRO_USD>` - Maximum estimated routine cost in micro-US dollars

  Default value: `0`
* `--routine-llm-cost-rate-micro-usd-per-million-tokens <ROUTINE_LLM_COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Manifest-bound LLM rate in micro-US dollars per million tokens

  Default value: `0`
* `--json` - Output as JSON
* `--project-key <PROJECT_KEYS>` - Exact registered project keys to inspect. May be repeated



## `forager offdesk shift propose`

Propose an explicit serial selection with pinned owning TODOs, without writes

**Usage:** `forager offdesk shift propose [OPTIONS] --starts-at <STARTS_AT> --stops-at <STOPS_AT> --project-key <PROJECT_KEYS> <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Registered Offdesk plan ID or registration/source path

###### **Options:**

* `--review-id <REVIEW_ID>` - Use this approved plan review instead of the latest review
* `--shift-id <SHIFT_ID>` - Stable shift ID. Generated when omitted; instantiate-template rejects this override
* `--starts-at <STARTS_AT>` - Inclusive shift start as RFC3339
* `--stops-at <STOPS_AT>` - Inclusive shift stop as RFC3339, no more than 24 hours after start
* `--timezone <TIMEZONE>` - Display timezone recorded in the immutable manifest

  Default value: `Asia/Seoul`
* `--enterprise-policy <ENTERPRISE_POLICY>` - Canonical enterprise policy bound into the immutable manifest

  Default value: `single-owner-local`

  Possible values: `single-owner-local`, `multi-operator-local`, `airgapped-local`

* `--host-policy <HOST_POLICY>` - Require and bind one live-verified child-task host isolation policy

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`

* `--read-policy <READ_POLICY>` - Require filesystem read restrictions for every local-background or local-tmux task

  Possible values: `linux-landlock-workdir-and-runtime-only`, `linux-landlock-metadata-boot-identity`

* `--task-id <TASK_IDS>` - Exact queued task to bind. May be repeated
* `--serial-proposal-sha256 <SERIAL_PROPOSAL_SHA256>` - Require the exact freshly revalidated serial-selection proposal
* `--require-preparation-control <REQUIRED_PREPARATION_CONTROLS>` - Require an existing preparation control by ID; unsupported or unconfigured controls hold arm/launch. Repeat as needed
* `--weekly-usage-state <WEEKLY_USAGE_STATE>` - Existing guard state directly inside this profile; binds weekly baselines to this run
* `--weekly-gpt-sample <WEEKLY_GPT_SAMPLE>` - Fresh normalized GPT weekly sample inside this profile, without automatic polling
* `--weekly-claude-sample <WEEKLY_CLAUDE_SAMPLE>` - Fresh normalized Claude weekly sample inside this profile
* `--weekly-max-age-seconds <WEEKLY_MAX_AGE_SECONDS>` - Maximum age of manually supplied weekly samples, at most 300 seconds

  Default value: `300`
* `--serial-project-key <SERIAL_PROJECT_KEYS>` - Registered project scope used to produce the serial proposal; repeat in the same order
* `--dispatch-interval-minutes <DISPATCH_INTERVAL_MINUTES>` - Add a dispatch-tick routine over the full shift window
* `--wiki-project-key <WIKI_PROJECT_KEY>` - Project key for one candidate-only Wiki distillation routine
* `--wiki-profile <WIKI_PROFILE>` - Wiki profile for the distillation routine
* `--wiki-root <WIKI_ROOT>` - Exact project root for the Wiki distillation routine
* `--wiki-at <WIKI_AT>` - One-time Wiki distillation time as RFC3339
* `--morning-review-at <MORNING_REVIEW_AT>` - Add a one-time morning review brief routine at this RFC3339 time
* `--max-concurrent-tasks <MAX_CONCURRENT_TASKS>` - Maximum simultaneously running bound tasks. Defaults to one when tasks exist
* `--max-attempts-per-task <MAX_ATTEMPTS_PER_TASK>` - Maximum attempts per bound task

  Default value: `1`
* `--max-routine-process-starts <MAX_ROUTINE_PROCESS_STARTS>` - Maximum child processes started by fixed routine handlers
* `--max-routine-external-calls <MAX_ROUTINE_EXTERNAL_CALLS>` - Maximum network calls attempted by fixed routine handlers
* `--max-routine-disk-bytes <MAX_ROUTINE_DISK_BYTES>` - Maximum durable evidence bytes written by fixed routine handlers

  Default value: `33554432`
* `--max-routine-llm-tokens <MAX_ROUTINE_LLM_TOKENS>` - Maximum total local-LLM prompt plus output tokens
* `--max-routine-cost-micro-usd <MAX_ROUTINE_COST_MICRO_USD>` - Maximum estimated routine cost in micro-US dollars

  Default value: `0`
* `--routine-llm-cost-rate-micro-usd-per-million-tokens <ROUTINE_LLM_COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Manifest-bound LLM rate in micro-US dollars per million tokens

  Default value: `0`
* `--json` - Output as JSON
* `--project-key <PROJECT_KEYS>` - Exact registered projects in scope; repeated task IDs define the proposed serial order
* `--expected-proposal-sha256 <EXPECTED_PROPOSAL_SHA256>` - Refuse if the freshly inspected proposal differs from this previous version



## `forager offdesk shift preview`

Read-only start preview for an exact reviewed plan and queued selection

**Usage:** `forager offdesk shift preview [OPTIONS] --starts-at <STARTS_AT> --stops-at <STOPS_AT> <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Registered Offdesk plan ID or registration/source path

###### **Options:**

* `--review-id <REVIEW_ID>` - Use this approved plan review instead of the latest review
* `--shift-id <SHIFT_ID>` - Stable shift ID. Generated when omitted; instantiate-template rejects this override
* `--starts-at <STARTS_AT>` - Inclusive shift start as RFC3339
* `--stops-at <STOPS_AT>` - Inclusive shift stop as RFC3339, no more than 24 hours after start
* `--timezone <TIMEZONE>` - Display timezone recorded in the immutable manifest

  Default value: `Asia/Seoul`
* `--enterprise-policy <ENTERPRISE_POLICY>` - Canonical enterprise policy bound into the immutable manifest

  Default value: `single-owner-local`

  Possible values: `single-owner-local`, `multi-operator-local`, `airgapped-local`

* `--host-policy <HOST_POLICY>` - Require and bind one live-verified child-task host isolation policy

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`

* `--read-policy <READ_POLICY>` - Require filesystem read restrictions for every local-background or local-tmux task

  Possible values: `linux-landlock-workdir-and-runtime-only`, `linux-landlock-metadata-boot-identity`

* `--task-id <TASK_IDS>` - Exact queued task to bind. May be repeated
* `--serial-proposal-sha256 <SERIAL_PROPOSAL_SHA256>` - Require the exact freshly revalidated serial-selection proposal
* `--require-preparation-control <REQUIRED_PREPARATION_CONTROLS>` - Require an existing preparation control by ID; unsupported or unconfigured controls hold arm/launch. Repeat as needed
* `--weekly-usage-state <WEEKLY_USAGE_STATE>` - Existing guard state directly inside this profile; binds weekly baselines to this run
* `--weekly-gpt-sample <WEEKLY_GPT_SAMPLE>` - Fresh normalized GPT weekly sample inside this profile, without automatic polling
* `--weekly-claude-sample <WEEKLY_CLAUDE_SAMPLE>` - Fresh normalized Claude weekly sample inside this profile
* `--weekly-max-age-seconds <WEEKLY_MAX_AGE_SECONDS>` - Maximum age of manually supplied weekly samples, at most 300 seconds

  Default value: `300`
* `--serial-project-key <SERIAL_PROJECT_KEYS>` - Registered project scope used to produce the serial proposal; repeat in the same order
* `--dispatch-interval-minutes <DISPATCH_INTERVAL_MINUTES>` - Add a dispatch-tick routine over the full shift window
* `--wiki-project-key <WIKI_PROJECT_KEY>` - Project key for one candidate-only Wiki distillation routine
* `--wiki-profile <WIKI_PROFILE>` - Wiki profile for the distillation routine
* `--wiki-root <WIKI_ROOT>` - Exact project root for the Wiki distillation routine
* `--wiki-at <WIKI_AT>` - One-time Wiki distillation time as RFC3339
* `--morning-review-at <MORNING_REVIEW_AT>` - Add a one-time morning review brief routine at this RFC3339 time
* `--max-concurrent-tasks <MAX_CONCURRENT_TASKS>` - Maximum simultaneously running bound tasks. Defaults to one when tasks exist
* `--max-attempts-per-task <MAX_ATTEMPTS_PER_TASK>` - Maximum attempts per bound task

  Default value: `1`
* `--max-routine-process-starts <MAX_ROUTINE_PROCESS_STARTS>` - Maximum child processes started by fixed routine handlers
* `--max-routine-external-calls <MAX_ROUTINE_EXTERNAL_CALLS>` - Maximum network calls attempted by fixed routine handlers
* `--max-routine-disk-bytes <MAX_ROUTINE_DISK_BYTES>` - Maximum durable evidence bytes written by fixed routine handlers

  Default value: `33554432`
* `--max-routine-llm-tokens <MAX_ROUTINE_LLM_TOKENS>` - Maximum total local-LLM prompt plus output tokens
* `--max-routine-cost-micro-usd <MAX_ROUTINE_COST_MICRO_USD>` - Maximum estimated routine cost in micro-US dollars

  Default value: `0`
* `--routine-llm-cost-rate-micro-usd-per-million-tokens <ROUTINE_LLM_COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Manifest-bound LLM rate in micro-US dollars per million tokens

  Default value: `0`
* `--json` - Output as JSON



## `forager offdesk shift enterprise-policies`

List canonical enterprise policy profiles without selecting one

**Usage:** `forager offdesk shift enterprise-policies [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift host-policy-probe`

Probe one supported host isolation policy without granting authority

**Usage:** `forager offdesk shift host-policy-probe [OPTIONS] [PROVIDER]`

###### **Arguments:**

* `<PROVIDER>` - Supported host policy provider to probe

  Default value: `linux-bubblewrap-external-ip-deny`

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`


###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift read-policy-probe`

Verify filesystem read restrictions without creating profile state or authority

**Usage:** `forager offdesk shift read-policy-probe [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift prepare`

Bind one reviewed plan, exact tasks, roots, routines, window, and budget

**Usage:** `forager offdesk shift prepare [OPTIONS] --starts-at <STARTS_AT> --stops-at <STOPS_AT> <PLAN_REF>`

###### **Arguments:**

* `<PLAN_REF>` - Registered Offdesk plan ID or registration/source path

###### **Options:**

* `--review-id <REVIEW_ID>` - Use this approved plan review instead of the latest review
* `--shift-id <SHIFT_ID>` - Stable shift ID. Generated when omitted; instantiate-template rejects this override
* `--starts-at <STARTS_AT>` - Inclusive shift start as RFC3339
* `--stops-at <STOPS_AT>` - Inclusive shift stop as RFC3339, no more than 24 hours after start
* `--timezone <TIMEZONE>` - Display timezone recorded in the immutable manifest

  Default value: `Asia/Seoul`
* `--enterprise-policy <ENTERPRISE_POLICY>` - Canonical enterprise policy bound into the immutable manifest

  Default value: `single-owner-local`

  Possible values: `single-owner-local`, `multi-operator-local`, `airgapped-local`

* `--host-policy <HOST_POLICY>` - Require and bind one live-verified child-task host isolation policy

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`

* `--read-policy <READ_POLICY>` - Require filesystem read restrictions for every local-background or local-tmux task

  Possible values: `linux-landlock-workdir-and-runtime-only`, `linux-landlock-metadata-boot-identity`

* `--task-id <TASK_IDS>` - Exact queued task to bind. May be repeated
* `--serial-proposal-sha256 <SERIAL_PROPOSAL_SHA256>` - Require the exact freshly revalidated serial-selection proposal
* `--require-preparation-control <REQUIRED_PREPARATION_CONTROLS>` - Require an existing preparation control by ID; unsupported or unconfigured controls hold arm/launch. Repeat as needed
* `--weekly-usage-state <WEEKLY_USAGE_STATE>` - Existing guard state directly inside this profile; binds weekly baselines to this run
* `--weekly-gpt-sample <WEEKLY_GPT_SAMPLE>` - Fresh normalized GPT weekly sample inside this profile, without automatic polling
* `--weekly-claude-sample <WEEKLY_CLAUDE_SAMPLE>` - Fresh normalized Claude weekly sample inside this profile
* `--weekly-max-age-seconds <WEEKLY_MAX_AGE_SECONDS>` - Maximum age of manually supplied weekly samples, at most 300 seconds

  Default value: `300`
* `--serial-project-key <SERIAL_PROJECT_KEYS>` - Registered project scope used to produce the serial proposal; repeat in the same order
* `--dispatch-interval-minutes <DISPATCH_INTERVAL_MINUTES>` - Add a dispatch-tick routine over the full shift window
* `--wiki-project-key <WIKI_PROJECT_KEY>` - Project key for one candidate-only Wiki distillation routine
* `--wiki-profile <WIKI_PROFILE>` - Wiki profile for the distillation routine
* `--wiki-root <WIKI_ROOT>` - Exact project root for the Wiki distillation routine
* `--wiki-at <WIKI_AT>` - One-time Wiki distillation time as RFC3339
* `--morning-review-at <MORNING_REVIEW_AT>` - Add a one-time morning review brief routine at this RFC3339 time
* `--max-concurrent-tasks <MAX_CONCURRENT_TASKS>` - Maximum simultaneously running bound tasks. Defaults to one when tasks exist
* `--max-attempts-per-task <MAX_ATTEMPTS_PER_TASK>` - Maximum attempts per bound task

  Default value: `1`
* `--max-routine-process-starts <MAX_ROUTINE_PROCESS_STARTS>` - Maximum child processes started by fixed routine handlers
* `--max-routine-external-calls <MAX_ROUTINE_EXTERNAL_CALLS>` - Maximum network calls attempted by fixed routine handlers
* `--max-routine-disk-bytes <MAX_ROUTINE_DISK_BYTES>` - Maximum durable evidence bytes written by fixed routine handlers

  Default value: `33554432`
* `--max-routine-llm-tokens <MAX_ROUTINE_LLM_TOKENS>` - Maximum total local-LLM prompt plus output tokens
* `--max-routine-cost-micro-usd <MAX_ROUTINE_COST_MICRO_USD>` - Maximum estimated routine cost in micro-US dollars

  Default value: `0`
* `--routine-llm-cost-rate-micro-usd-per-million-tokens <ROUTINE_LLM_COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Manifest-bound LLM rate in micro-US dollars per million tokens

  Default value: `0`
* `--json` - Output as JSON



## `forager offdesk shift instantiate-template`

Materialize one daily or weekly slot as a fresh inert shift manifest

**Usage:** `forager offdesk shift instantiate-template [OPTIONS] --template-key <TEMPLATE_KEY> --starts-at <STARTS_AT> --stops-at <STOPS_AT> <CADENCE> <PLAN_REF>`

###### **Arguments:**

* `<CADENCE>` - Cadence label used only to derive this occurrence's UTC slot

  Possible values: `daily`, `weekly`

* `<PLAN_REF>` - Registered Offdesk plan ID or registration/source path

###### **Options:**

* `--template-key <TEMPLATE_KEY>` - Stable non-authorizing identity for this recurring template
* `--review-id <REVIEW_ID>` - Use this approved plan review instead of the latest review
* `--shift-id <SHIFT_ID>` - Stable shift ID. Generated when omitted; instantiate-template rejects this override
* `--starts-at <STARTS_AT>` - Inclusive shift start as RFC3339
* `--stops-at <STOPS_AT>` - Inclusive shift stop as RFC3339, no more than 24 hours after start
* `--timezone <TIMEZONE>` - Display timezone recorded in the immutable manifest

  Default value: `Asia/Seoul`
* `--enterprise-policy <ENTERPRISE_POLICY>` - Canonical enterprise policy bound into the immutable manifest

  Default value: `single-owner-local`

  Possible values: `single-owner-local`, `multi-operator-local`, `airgapped-local`

* `--host-policy <HOST_POLICY>` - Require and bind one live-verified child-task host isolation policy

  Possible values: `linux-bubblewrap-external-ip-deny`, `linux-bubblewrap-external-ip-and-outside-workdir-write-deny`

* `--read-policy <READ_POLICY>` - Require filesystem read restrictions for every local-background or local-tmux task

  Possible values: `linux-landlock-workdir-and-runtime-only`, `linux-landlock-metadata-boot-identity`

* `--task-id <TASK_IDS>` - Exact queued task to bind. May be repeated
* `--serial-proposal-sha256 <SERIAL_PROPOSAL_SHA256>` - Require the exact freshly revalidated serial-selection proposal
* `--require-preparation-control <REQUIRED_PREPARATION_CONTROLS>` - Require an existing preparation control by ID; unsupported or unconfigured controls hold arm/launch. Repeat as needed
* `--weekly-usage-state <WEEKLY_USAGE_STATE>` - Existing guard state directly inside this profile; binds weekly baselines to this run
* `--weekly-gpt-sample <WEEKLY_GPT_SAMPLE>` - Fresh normalized GPT weekly sample inside this profile, without automatic polling
* `--weekly-claude-sample <WEEKLY_CLAUDE_SAMPLE>` - Fresh normalized Claude weekly sample inside this profile
* `--weekly-max-age-seconds <WEEKLY_MAX_AGE_SECONDS>` - Maximum age of manually supplied weekly samples, at most 300 seconds

  Default value: `300`
* `--serial-project-key <SERIAL_PROJECT_KEYS>` - Registered project scope used to produce the serial proposal; repeat in the same order
* `--dispatch-interval-minutes <DISPATCH_INTERVAL_MINUTES>` - Add a dispatch-tick routine over the full shift window
* `--wiki-project-key <WIKI_PROJECT_KEY>` - Project key for one candidate-only Wiki distillation routine
* `--wiki-profile <WIKI_PROFILE>` - Wiki profile for the distillation routine
* `--wiki-root <WIKI_ROOT>` - Exact project root for the Wiki distillation routine
* `--wiki-at <WIKI_AT>` - One-time Wiki distillation time as RFC3339
* `--morning-review-at <MORNING_REVIEW_AT>` - Add a one-time morning review brief routine at this RFC3339 time
* `--max-concurrent-tasks <MAX_CONCURRENT_TASKS>` - Maximum simultaneously running bound tasks. Defaults to one when tasks exist
* `--max-attempts-per-task <MAX_ATTEMPTS_PER_TASK>` - Maximum attempts per bound task

  Default value: `1`
* `--max-routine-process-starts <MAX_ROUTINE_PROCESS_STARTS>` - Maximum child processes started by fixed routine handlers
* `--max-routine-external-calls <MAX_ROUTINE_EXTERNAL_CALLS>` - Maximum network calls attempted by fixed routine handlers
* `--max-routine-disk-bytes <MAX_ROUTINE_DISK_BYTES>` - Maximum durable evidence bytes written by fixed routine handlers

  Default value: `33554432`
* `--max-routine-llm-tokens <MAX_ROUTINE_LLM_TOKENS>` - Maximum total local-LLM prompt plus output tokens
* `--max-routine-cost-micro-usd <MAX_ROUTINE_COST_MICRO_USD>` - Maximum estimated routine cost in micro-US dollars

  Default value: `0`
* `--routine-llm-cost-rate-micro-usd-per-million-tokens <ROUTINE_LLM_COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Manifest-bound LLM rate in micro-US dollars per million tokens

  Default value: `0`
* `--json` - Output as JSON



## `forager offdesk shift show`

Show one shift, or the latest shift when no ID is supplied

**Usage:** `forager offdesk shift show [OPTIONS] [SHIFT_ID]`

###### **Arguments:**

* `<SHIFT_ID>` - Exact shift ID. Defaults to the latest prepared or armed shift

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift arm`

Create or consume a manifest-bound operator approval and arm the shift

**Usage:** `forager offdesk shift arm [OPTIONS] [SHIFT_ID]`

###### **Arguments:**

* `<SHIFT_ID>` - Prepared shift ID. Defaults to the latest prepared shift

###### **Options:**

* `--approval-id <APPROVAL_ID>` - Exact approved approval ID. Without it, an exact pending approval is reused or created
* `--approval-ttl-minutes <APPROVAL_TTL_MINUTES>` - Time allowed to answer a newly created arm approval

  Default value: `30`
* `--json` - Output as JSON



## `forager offdesk shift status`

Summarize all bounded shifts without creating profile state

**Usage:** `forager offdesk shift status [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift review`

Project one bounded shift into the shared read-only morning review

**Usage:** `forager offdesk shift review [OPTIONS] [SHIFT_ID]`

###### **Arguments:**

* `<SHIFT_ID>` - Exact shift ID. Defaults to the active shift, then the latest shift

###### **Options:**

* `--current-run-id <CURRENT_RUN_ID>` - Routine run currently generating this projection; it is not treated as abandoned
* `--json` - Output as JSON



## `forager offdesk shift retention-report`

Classify preserved shift records and derived evidence without mutating either

**Usage:** `forager offdesk shift retention-report [OPTIONS]`

###### **Options:**

* `--shift-id <SHIFT_ID>` - Limit the report to one exact shift ID
* `--compact-after-days <COMPACT_AFTER_DAYS>` - Age after which terminal morning-review views become compaction candidates

  Default value: `30`
* `--json` - Output as JSON



## `forager offdesk shift council-packet`

Build a review-only Council comparison of goal, evidence, drift, and budget

**Usage:** `forager offdesk shift council-packet [OPTIONS] [SHIFT_ID]`

###### **Arguments:**

* `<SHIFT_ID>` - Exact shift ID. Defaults to the active shift, then the latest shift

###### **Options:**

* `--current-run-id <CURRENT_RUN_ID>` - Morning routine currently producing evidence; it is not treated as abandoned
* `--json` - Output as JSON



## `forager offdesk shift disarm`

Terminate active shift authority without stopping already-running processes

**Usage:** `forager offdesk shift disarm [OPTIONS] [SHIFT_ID]`

###### **Arguments:**

* `<SHIFT_ID>` - Active shift ID. Defaults to the one active shift

###### **Options:**

* `--reason <REASON>` - Operator-safe reason recorded in the terminal transition

  Default value: `operator requested disarm`
* `--json` - Output as JSON



## `forager offdesk shift routine-claim`

Select and reserve the one manifest-bound handler for a fixed timer task

**Usage:** `forager offdesk shift routine-claim [OPTIONS] --task <TASK> <SHIFT_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact armed shift ID

###### **Options:**

* `--task <TASK>` - Fixed timer task mapped to one manifest-bound routine handler by Rust policy

  Possible values: `tick`, `distill`, `brief`

* `--json` - Output as JSON



## `forager offdesk shift routine-start`

Reserve one due typed routine occurrence before executing it

**Usage:** `forager offdesk shift routine-start [OPTIONS] <SHIFT_ID> <ROUTINE_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact armed shift ID
* `<ROUTINE_ID>` - Exact routine ID from the reviewed manifest

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk shift routine-complete`

Record the terminal outcome of one exact routine reservation

**Usage:** `forager offdesk shift routine-complete [OPTIONS] --outcome <OUTCOME> --summary <SUMMARY> <SHIFT_ID> <RUN_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact armed shift ID
* `<RUN_ID>` - Deterministic run ID returned by routine-start

###### **Options:**

* `--outcome <OUTCOME>` - Terminal typed outcome

  Possible values: `succeeded`, `failed`, `skipped`, `budget-exhausted`, `identity-drift`

* `--summary <SUMMARY>` - Short operator-safe outcome summary
* `--evidence <EVIDENCE_PATHS>` - Local regular evidence file to hash-bind. May be repeated
* `--usage-complete` - Assert that all routine-controlled resource usage was measured
* `--process-starts <PROCESS_STARTS>` - Child processes started by the fixed handler

  Default value: `0`
* `--external-calls <EXTERNAL_CALLS>` - Network calls attempted by the fixed handler

  Default value: `0`
* `--llm-tokens <LLM_TOKENS>` - Prompt plus output tokens reported by the local LLM provider

  Default value: `0`
* `--cost-micro-usd <COST_MICRO_USD>` - Estimated cost in micro-US dollars

  Default value: `0`
* `--cost-rate-micro-usd-per-million-tokens <COST_RATE_MICRO_USD_PER_MILLION_TOKENS>` - Cost rate used for the estimate, in micro-US dollars per million tokens
* `--json` - Output as JSON



## `forager offdesk shift routine-recover`

Fail-safe close an unresolved routine after explicit operator acknowledgement

**Usage:** `forager offdesk shift routine-recover [OPTIONS] --reason <REASON> <SHIFT_ID> <RUN_ID>`

###### **Arguments:**

* `<SHIFT_ID>` - Exact shift ID that owns the unresolved reservation
* `<RUN_ID>` - Deterministic unresolved run ID returned by routine-start

###### **Options:**

* `--reason <REASON>` - Why the operator is closing the uncertain execution as failed
* `--acknowledge-execution-unknown` - Confirm that the handler may or may not have run and no success is being asserted
* `--evidence <EVIDENCE_PATHS>` - Local regular evidence file to hash-bind. May be repeated
* `--json` - Output as JSON



## `forager offdesk tasks`

Show durable offdesk tasks

**Usage:** `forager offdesk tasks [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Filter tasks by project key
* `--task-id <TASK_ID>` - Filter tasks by exact task ID
* `--status <STATUS>` - Filter tasks by status. Repeat for multiple statuses
* `--latest` - Return only the newest matching task by updated_at
* `--json` - Output as JSON



## `forager offdesk task-recovery`

Inspect runtime release evidence without changing tasks or processes

**Usage:** `forager offdesk task-recovery [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Exact Offdesk task ID

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk decisions`

List canonical Offdesk decision records

**Usage:** `forager offdesk decisions [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Filter by project key
* `--task-id <TASK_ID>` - Filter by task ID
* `--status <STATUS>` - Filter by decision status, such as user_pending or auto_resolved
* `--json` - Output as JSON



## `forager offdesk decision`

Inspect one canonical Offdesk decision record

**Usage:** `forager offdesk decision <COMMAND>`

###### **Subcommands:**

* `show` - Show one canonical Offdesk decision record
* `resolve` - Resolve a decision into an append-only execution handoff
* `receipt` - Close a handoff-ready decision with an append-only receipt
* `ingest-telegram` - Ingest a Telegram relay result into the canonical decision ledger
* `ingest-telegram-feedback` - Promote Telegram freeform feedback into the canonical decision inbox



## `forager offdesk decision show`

Show one canonical Offdesk decision record

**Usage:** `forager offdesk decision show [OPTIONS] <DECISION_ID>`

###### **Arguments:**

* `<DECISION_ID>` - Decision ID to inspect

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk decision resolve`

Resolve a decision into an append-only execution handoff

**Usage:** `forager offdesk decision resolve [OPTIONS] --decision <DECISION> <DECISION_ID>`

###### **Arguments:**

* `<DECISION_ID>` - Decision ID to resolve

###### **Options:**

* `--decision <DECISION>` - Operator or policy choice, such as continue, revise, block, stop, deny, or defer
* `--note <NOTE>` - Required rationale or natural-language direction for revise/block/custom choices

  Default value: ``
* `--by <BY>` - Actor recording the resolution

  Default value: `operator`
* `--target <TARGET>` - Override execution handoff target
* `--json` - Output as JSON



## `forager offdesk decision receipt`

Close a handoff-ready decision with an append-only receipt

**Usage:** `forager offdesk decision receipt [OPTIONS] <DECISION_ID>`

###### **Arguments:**

* `<DECISION_ID>` - Decision ID to close

###### **Options:**

* `--by <BY>` - Actor recording the receipt

  Default value: `operator`
* `--result-status <RESULT_STATUS>` - Result status for the consumed handoff

  Default value: `closed`
* `--evidence <EVIDENCE_SUMMARY>` - Evidence summary line. Repeat for multiple lines
* `--remaining-review <REMAINING_REVIEW>` - Remaining review item. Repeat for multiple lines
* `--json` - Output as JSON



## `forager offdesk decision ingest-telegram`

Ingest a Telegram relay result into the canonical decision ledger

**Usage:** `forager offdesk decision ingest-telegram [OPTIONS] --request <REQUEST> --result <RESULT>`

###### **Options:**

* `--request <REQUEST>` - Operator-safe decision request JSON containing decision_record
* `--result <RESULT>` - Telegram relay result JSON
* `--profile-dir <PROFILE_DIR>` - Override canonical profile directory for producer integrations
* `--by <BY>` - Actor recording the relay ingestion

  Default value: `telegram`
* `--target <TARGET>` - Override execution handoff target
* `--receipt-result-status <RECEIPT_RESULT_STATUS>` - Also append a receipt with this result status after resolving
* `--receipt-evidence <RECEIPT_EVIDENCE_SUMMARY>` - Receipt evidence summary line. Repeat for multiple lines
* `--remaining-review <REMAINING_REVIEW>` - Remaining review item. Repeat for multiple lines
* `--json` - Output as JSON



## `forager offdesk decision ingest-telegram-feedback`

Promote Telegram freeform feedback into the canonical decision inbox

**Usage:** `forager offdesk decision ingest-telegram-feedback [OPTIONS] --feedback <FEEDBACK>`

###### **Options:**

* `--feedback <FEEDBACK>` - Telegram feedback JSON or JSONL file
* `--profile-dir <PROFILE_DIR>` - Override canonical profile directory for producer integrations
* `--by <BY>` - Actor recording the inbox item

  Default value: `telegram`
* `--json` - Output as JSON



## `forager offdesk provider-capacity`

Show provider capacity cooldown state

**Usage:** `forager offdesk provider-capacity [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk provider-fallback`

Recommend provider/model fallbacks without retargeting tasks

**Usage:** `forager offdesk provider-fallback [OPTIONS] --provider-id <PROVIDER_ID>`

###### **Options:**

* `--provider-id <PROVIDER_ID>` - Current provider ID that is blocked or under review
* `--model <MODEL>` - Current provider model to exclude from fallback candidates
* `--runner-role <RUNNER_ROLE>` - Runner role used to filter compatible cross-provider candidates

  Default value: `worker`
* `--json` - Output as JSON



## `forager offdesk cancel-task`

Mark a durable task cancelled without stopping its background runner

**Usage:** `forager offdesk cancel-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Offdesk task ID to cancel

###### **Options:**

* `--reason <REASON>` - Operator reason to store on the task
* `--json` - Output as JSON



## `forager offdesk terminate-task`

Stop the verified local runtime and mark its durable task cancelled

**Usage:** `forager offdesk terminate-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Offdesk task ID whose verified local runtime should be stopped

###### **Options:**

* `--reason <REASON>` - Operator reason stored with the termination and task cancellation
* `--by <BY>` - Actor requesting the termination

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk pause`

Halt all new offdesk dispatch until resumed (existing runs keep polling)

**Usage:** `forager offdesk pause [OPTIONS]`

###### **Options:**

* `--reason <REASON>` - Reason to record for the pause
* `--by <BY>` - Actor engaging the pause

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk unpause`

Clear the global operator pause so new dispatch can proceed again

**Usage:** `forager offdesk unpause [OPTIONS]`

###### **Options:**

* `--by <BY>` - Actor clearing the pause

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk pause-status`

Show the current global operator pause state

**Usage:** `forager offdesk pause-status [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk learning-scan`

Emit adaptive-wiki learning candidates from observed denials, failures, and resume-recovery rows (recommendation-only; runs each event once)

**Usage:** `forager offdesk learning-scan [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk retry-task`

Requeue a failed, resume-pending, or cancelled durable task

**Usage:** `forager offdesk retry-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Offdesk task ID to retry

###### **Options:**

* `--new-approval` - Supersede matching denied approval rows so the next tick creates a new approval
* `--json` - Output as JSON



## `forager offdesk resume-task`

Accept recovery for a resume-pending task and requeue it

**Usage:** `forager offdesk resume-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Exact Offdesk task ID

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk abandon-task`

Discard a failed or resume-pending task

**Usage:** `forager offdesk abandon-task [OPTIONS] <TASK_ID>`

###### **Arguments:**

* `<TASK_ID>` - Exact Offdesk task ID

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk poll`

Poll background runner probes, persist phase transitions, and reconcile task status

**Usage:** `forager offdesk poll [OPTIONS] [TICKET_ID]`

###### **Arguments:**

* `<TICKET_ID>` - Ticket ID to poll. Defaults to all tickets

###### **Options:**

* `--notify-cooldown-minutes <NOTIFY_COOLDOWN_MINUTES>` - Record notification cooldown state in minutes
* `--json` - Output as JSON



## `forager offdesk ok`

Approve the oldest or targeted pending action

**Usage:** `forager offdesk ok [OPTIONS] [APPROVAL_ID]`

###### **Arguments:**

* `<APPROVAL_ID>` - Approval ID to resolve. Defaults to the oldest pending approval

###### **Options:**

* `--by <BY>` - Operator or surface resolving this approval

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk cancel`

Deny the oldest or targeted pending action

**Usage:** `forager offdesk cancel [OPTIONS] [APPROVAL_ID]`

###### **Arguments:**

* `<APPROVAL_ID>` - Approval ID to resolve. Defaults to the oldest pending approval

###### **Options:**

* `--by <BY>` - Operator or surface resolving this approval

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk resume`

Show task resume artifacts

**Usage:** `forager offdesk resume [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk background`

Show background runner recovery probes

**Usage:** `forager offdesk background [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk background-ack`

Acknowledge a stale or failed background probe after linked tasks are cancelled

**Usage:** `forager offdesk background-ack [OPTIONS] --reason <REASON> <TICKET_ID>`

###### **Arguments:**

* `<TICKET_ID>` - Background ticket ID to acknowledge

###### **Options:**

* `--reason <REASON>` - Operator reason for suppressing further recovery attention
* `--by <BY>` - Operator or surface recording this acknowledgement

  Default value: `cli`
* `--source-surface <SOURCE_SURFACE>` - Source surface recorded on the acknowledgement

  Default value: `cli`
* `--allow-unlinked` - Permit acknowledgement when no durable task is linked to the background ticket
* `--json` - Output as JSON



## `forager offdesk capabilities`

Show Task Team capability metadata

**Usage:** `forager offdesk capabilities [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk snapshots`

List pre-mutation checkpoint snapshots

**Usage:** `forager offdesk snapshots [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk snapshot`

Show and verify a pre-mutation checkpoint snapshot

**Usage:** `forager offdesk snapshot [OPTIONS] <MUTATION_ID>`

###### **Arguments:**

* `<MUTATION_ID>` - Mutation snapshot ID

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk restore-plan`

Show a dry-run rollback plan without modifying files

**Usage:** `forager offdesk restore-plan [OPTIONS] <MUTATION_ID>`

###### **Arguments:**

* `<MUTATION_ID>` - Mutation snapshot ID

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk debug-bundle`

Emit a sanitized read-only debug bundle

**Usage:** `forager offdesk debug-bundle [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON
* `--export` - Write the sanitized bundle JSON to a diagnostics file
* `--output <OUTPUT>` - Write the sanitized bundle JSON to this path



## `forager offdesk maintenance-report`

Summarize read-only Offdesk maintenance risks

**Usage:** `forager offdesk maintenance-report [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON
* `--wiki-review-near-expiry-hours <WIKI_REVIEW_NEAR_EXPIRY_HOURS>` - Hours before review_after expiry to flag adaptive wiki entries

  Default value: `168`
* `--wiki-runtime-ack-near-expiry-hours <WIKI_RUNTIME_ACK_NEAR_EXPIRY_HOURS>` - Hours before runtime policy acknowledgement expiry to flag attention

  Default value: `6`



## `forager offdesk maintenance-request`

Create or reuse an approval request for a maintenance action

**Usage:** `forager offdesk maintenance-request [OPTIONS] --kind <KIND> --project-key <PROJECT_KEY> --request-id <REQUEST_ID> --preview <PREVIEW> --reason <REASON>`

###### **Options:**

* `--kind <KIND>` - Bounded maintenance action kind to request approval for
* `--project-key <PROJECT_KEY>` - Project key for approval and audit correlation
* `--request-id <REQUEST_ID>` - Request ID for approval and audit correlation
* `--task-id <TASK_ID>` - Task ID for approval identity. Defaults to maintenance-{kind}-{target-id}
* `--target-id <TARGET_ID>` - Optional target identifier used for approval deduplication and review
* `--risk <RISK>` - Override the default risk for this maintenance kind
* `--preview <PREVIEW>` - Operator-safe action preview
* `--reason <REASON>` - Reason shown when approval is required
* `--source-surface <SOURCE_SURFACE>` - Source surface recorded on generated approval rows

  Default value: `cli`
* `--ttl-minutes <TTL_MINUTES>` - Pending approval TTL in minutes

  Default value: `30`
* `--json` - Output as JSON



## `forager offdesk deck`

Generate a Marp-compatible review deck from a read-only Offdesk artifact

**Usage:** `forager offdesk deck [OPTIONS] --from <SOURCE>`

###### **Options:**

* `--from <SOURCE>` - Source Offdesk JSON artifact to summarize into a Marp deck
* `--kind <KIND>` - Artifact shape. Use auto unless the source is ambiguous

  Default value: `auto`

  Possible values: `auto`, `closeout`, `plan`, `status`

* `--out <OUT>` - Markdown deck output path. Defaults to `<source-stem>.marp.md`
* `--force` - Overwrite the Markdown deck or rendered artifact if it already exists
* `--title <TITLE>` - Optional deck title
* `--render <RENDER>` - Render the deck with Marp CLI after writing Markdown

  Possible values: `html`, `pdf`, `pptx`

* `--marp-bin <MARP_BIN>` - Marp CLI binary to use with --render

  Default value: `marp`
* `--json` - Output as JSON



## `forager offdesk closeout`

Generate a mandatory closeout plan and commercial review packet

**Usage:** `forager offdesk closeout [OPTIONS]`

###### **Options:**

* `--owning-plan <OWNING_PLAN>` - Existing owning TODO relative to the declared project root
* `--subproject-spec <SUBPROJECT_SPEC>` - Existing logical subproject declaration, validated by project owner routing
* `--project-key <PROJECT_KEY>` - Project key to close out. Defaults to all projects in the profile
* `--request-id <REQUEST_ID>` - Request ID to close out
* `--task-id <TASK_ID>` - Task ID to close out
* `--workdir <WORKDIR>` - Optional project workdir for read-only git status evidence
* `--include-git` - Include read-only git status and diff-stat from --workdir or matched task workdir
* `--review-provider <REVIEW_PROVIDER>` - Commercial model/provider label expected to review move/delete/archive decisions

  Default value: `commercial`
* `--output <OUTPUT>` - Write closeout artifacts to this directory
* `--dry-run` - Accepted for explicit operator intent; closeout never applies file operations
* `--json` - Output as JSON



## `forager offdesk closeout-review`

Record a reviewed closeout verdict without applying file operations

**Usage:** `forager offdesk closeout-review [OPTIONS] --verdict <VERDICT>`

###### **Options:**

* `--closeout-id <CLOSEOUT_ID>` - Closeout ID from `forager offdesk closeout`
* `--artifact-dir <ARTIFACT_DIR>` - Closeout artifact directory containing closeout_plan.json
* `--verdict <VERDICT>` - Commercial review verdict

  Possible values: `approved`, `revise`, `blocked`

* `--reviewer <REVIEWER>` - Reviewer or reviewing model label

  Default value: `operator`
* `--review-provider <REVIEW_PROVIDER>` - Commercial model/provider label used for review
* `--review-file <REVIEW_FILE>` - Optional path to the raw commercial review output
* `--unsafe-operation <UNSAFE_OPERATION>` - Unsafe operation reported by review; may be passed multiple times
* `--missing-evidence <MISSING_EVIDENCE>` - Missing evidence reported by review; may be passed multiple times
* `--required-first-read <REQUIRED_FIRST_READ>` - Required first-read path reported by review; may be passed multiple times
* `--notes <NOTES>` - Short review note. Secrets are redacted before persistence
* `--json` - Output as JSON



## `forager offdesk closeout-decision`

Resolve a closeout receipt open decision without applying file operations

**Usage:** `forager offdesk closeout-decision [OPTIONS] --kind <KIND> --decision <DECISION> --reason <REASON>`

###### **Options:**

* `--closeout-id <CLOSEOUT_ID>` - Closeout ID from `forager offdesk closeout`
* `--artifact-dir <ARTIFACT_DIR>` - Closeout artifact directory containing closeout_plan.json
* `--kind <KIND>` - Open decision kind to resolve, for example archive_review
* `--decision <DECISION>` - Resolution to record. This command never moves, archives, or deletes files

  Possible values: `preserve-in-place`, `preserve-in-place-administrative`, `not-applicable-administrative`

* `--expected-review-sha256 <EXPECTED_REVIEW_SHA256>` - Exact latest review-file hash, required for administrative-only decisions
* `--acknowledge-read <ACKNOWLEDGE_READ>` - Acknowledge an exact artifact read as ABSOLUTE_PATH=SHA256; never accepts its claims
* `--reviewer <REVIEWER>` - Reviewer or operator label

  Default value: `operator`
* `--reason <REASON>` - Required rationale for the decision. Secrets are redacted before persistence
* `--json` - Output as JSON



## `forager offdesk closeout-retire`

Retire an evidence-incomplete historical closeout without accepting truth

**Usage:** `forager offdesk closeout-retire [OPTIONS] --reason <REASON>`

###### **Options:**

* `--closeout-id <CLOSEOUT_ID>` - Closeout ID from `forager offdesk closeout`
* `--artifact-dir <ARTIFACT_DIR>` - Closeout artifact directory containing closeout_plan.json
* `--reviewer <REVIEWER>` - Reviewer or operator label

  Default value: `operator`
* `--reason <REASON>` - Required rationale for retiring the closeout as evidence-incomplete
* `--json` - Output as JSON



## `forager offdesk review-link`

Inspect or explicitly record an exact existing-decision link to a completed runtime

**Usage:** `forager offdesk review-link [OPTIONS] <REQUEST>`

###### **Arguments:**

* `<REQUEST>` - Exact request binding the runtime, result and existing committed decision

###### **Options:**

* `--confirm` - Confirm only this review link, without accepting output or starting work
* `--expected-preview-sha256 <EXPECTED_PREVIEW_SHA256>`
* `--by <BY>`



## `forager offdesk wiki`

Inspect adaptive wiki candidates, entries, projections, and lint

**Usage:** `forager offdesk wiki <COMMAND>`

###### **Subcommands:**

* `corrections` - List first-class adaptive wiki correction records
* `proposal-events` - List adaptive wiki review proposal lifecycle events
* `record-proposal-event` - Record an operator decision for a curator review proposal
* `accept-proposal` - Accept a current curator review proposal and copy its metadata into the event
* `reject-proposal` - Reject a current curator review proposal and copy its metadata into the event
* `supersede-proposal` - Mark a current curator review proposal superseded and copy its metadata into the event
* `proposal-handoff` - Preview the governed mutation handoff command for a current proposal
* `proposal-receipt` - Link a handoff preview, mutation audit, and lifecycle event without mutating state
* `candidates` - List adaptive wiki candidates
* `entries` - List adaptive wiki entries
* `show` - Show one adaptive wiki entry or candidate
* `projection` - Show the AI projection for a scope
* `brief` - Render a compact, skepticism-aware knowledge brief for session start
* `runtime-policy-acks` - List strict runtime projection policy acknowledgements
* `runtime-policy-ack-report` - Report strict runtime projection acknowledgements that need attention
* `review-after-report` - Report promoted entries whose review_after needs attention
* `ack-runtime-policy` - Acknowledge strict review_after exclusion for runtime projection
* `lint` - Lint adaptive wiki state
* `export-markdown` - Export adaptive wiki state as a one-way markdown vault
* `graph` - Export a read-only adaptive wiki tag graph
* `review` - Generate a recommendation-only adaptive wiki review report
* `evaluate-episode` - Evaluate one adaptive wiki entry across in-scope and out-of-scope projections
* `episode-trace` - Trace live task/probe/wiki evidence for adaptive behavior review
* `evaluate-recurrence` - Evaluate whether corrections recur after an entry is promoted
* `promotion-chain` - Reconstruct the evidence chain captured at promotion time
* `record-candidate` - Record an operator-authored learning candidate (e.g. from a doc review)
* `promote` - Promote a candidate into a scoped wiki entry
* `reject` - Reject a candidate without creating an entry
* `rescope` - Change an entry scope
* `edit` - Edit an entry's classification, modes, text, or evidence refs in place
* `add-tag` - Add controlled/proposed tags to an entry (e.g. facet/* or domain/*)
* `deprecate` - Deprecate an entry so it no longer appears in AI projection
* `renew-review-after` - Renew an entry review_after timestamp without changing scope or instruction
* `add-counterexample` - Add a counterexample evidence ref to an entry
* `update-runbook` - Attach governed runbook support refs to a procedure entry



## `forager offdesk wiki corrections`

List first-class adaptive wiki correction records

**Usage:** `forager offdesk wiki corrections [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk wiki proposal-events`

List adaptive wiki review proposal lifecycle events

**Usage:** `forager offdesk wiki proposal-events [OPTIONS]`

###### **Options:**

* `--proposal-id <PROPOSAL_ID>` - Filter lifecycle events by proposal id
* `--json` - Output as JSON



## `forager offdesk wiki record-proposal-event`

Record an operator decision for a curator review proposal

**Usage:** `forager offdesk wiki record-proposal-event [OPTIONS] --decision <DECISION> --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Curator review proposal id

###### **Options:**

* `--decision <DECISION>` - Operator decision for the proposal
* `--proposal-action <PROPOSAL_ACTION>` - Proposal action that was reviewed
* `--subject-kind <SUBJECT_KIND>` - Proposal subject kind, such as entry or candidate

  Default value: ``
* `--subject-id <SUBJECT_ID>` - Proposal subject id

  Default value: ``
* `--by <BY>` - Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` - Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` - Evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` - Previous proposal id superseded by this decision
* `--json` - Output as JSON



## `forager offdesk wiki accept-proposal`

Accept a current curator review proposal and copy its metadata into the event

**Usage:** `forager offdesk wiki accept-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Current curator review proposal id

###### **Options:**

* `--by <BY>` - Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` - Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` - Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` - Previous proposal id superseded by this decision
* `--allow-decided` - Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` - Output as JSON



## `forager offdesk wiki reject-proposal`

Reject a current curator review proposal and copy its metadata into the event

**Usage:** `forager offdesk wiki reject-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Current curator review proposal id

###### **Options:**

* `--by <BY>` - Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` - Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` - Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` - Previous proposal id superseded by this decision
* `--allow-decided` - Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` - Output as JSON



## `forager offdesk wiki supersede-proposal`

Mark a current curator review proposal superseded and copy its metadata into the event

**Usage:** `forager offdesk wiki supersede-proposal [OPTIONS] --reason <REASON> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Current curator review proposal id

###### **Options:**

* `--by <BY>` - Operator or surface recording the decision

  Default value: `cli`
* `--reason <REASON>` - Required reason for accepting, rejecting, or superseding the proposal
* `--evidence-ref <EVIDENCE_REFS>` - Extra evidence ref that supports this proposal decision
* `--supersedes <SUPERSEDES>` - Previous proposal id superseded by this decision
* `--allow-decided` - Allow recording a new lifecycle event for a non-stale decided proposal
* `--json` - Output as JSON



## `forager offdesk wiki proposal-handoff`

Preview the governed mutation handoff command for a current proposal

**Usage:** `forager offdesk wiki proposal-handoff [OPTIONS] <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Current curator review proposal id

###### **Options:**

* `--mutation <MUTATION>` - Operator-selected mutation path to preview when the proposal is manual
* `--scope <SCOPE>` - Scope for a parameterized rescope handoff
* `--scope-ref <SCOPE_REF>` - Scope reference for a parameterized rescope handoff
* `--evidence-ref <EVIDENCE_REF>` - Evidence ref for a parameterized counterexample handoff
* `--deprecated-entry-id <DEPRECATED_ENTRY_ID>` - Entry to deprecate for a parameterized merge cleanup or conflict handoff
* `--reason <REASON>` - Operator rationale to include in the previewed mutation command
* `--json` - Output as JSON



## `forager offdesk wiki proposal-receipt`

Link a handoff preview, mutation audit, and lifecycle event without mutating state

**Usage:** `forager offdesk wiki proposal-receipt [OPTIONS] --audit-id <AUDIT_ID> --event-id <EVENT_ID> --command <COMMAND> <PROPOSAL_ID>`

###### **Arguments:**

* `<PROPOSAL_ID>` - Curator review proposal id that the receipt should link

###### **Options:**

* `--audit-id <AUDIT_ID>` - Adaptive wiki mutation audit id produced by the executed mutation command
* `--event-id <EVENT_ID>` - Proposal lifecycle event id recorded for the operator decision
* `--command <COMMAND>` - Previewed handoff command that the operator executed or reviewed
* `--export` - Write the sanitized receipt JSON to an audit artifact file
* `--output <OUTPUT>` - Write the sanitized receipt JSON to this path
* `--json` - Output as JSON



## `forager offdesk wiki candidates`

List adaptive wiki candidates

**Usage:** `forager offdesk wiki candidates [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to match
* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to match
* `--json` - Output as JSON



## `forager offdesk wiki entries`

List adaptive wiki entries

**Usage:** `forager offdesk wiki entries [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to match
* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to match
* `--json` - Output as JSON



## `forager offdesk wiki show`

Show one adaptive wiki entry or candidate

**Usage:** `forager offdesk wiki show [OPTIONS] <ID>`

###### **Arguments:**

* `<ID>` - Adaptive wiki entry or candidate id

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk wiki projection`

Show the AI projection for a scope

**Usage:** `forager offdesk wiki projection [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to match
* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to match
* `--report` - Return the projection policy report instead of only selected entries
* `--compare-review-expired-policy` - Compare default warn policy with strict review_after exclusion
* `--max-entries <MAX_ENTRIES>` - Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` - Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` - Maximum characters kept per projected instruction; 0 disables truncation
* `--exclude-review-expired` - Exclude entries that are past review_after from the projection report
* `--json` - Output as JSON



## `forager offdesk wiki brief`

Render a compact, skepticism-aware knowledge brief for session start

**Usage:** `forager offdesk wiki brief [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode to project for (omit for shared/universal entries)
* `--max-entries <MAX_ENTRIES>` - Maximum entries in the brief

  Default value: `12`
* `--out <OUT>` - Write the brief to this path instead of stdout
* `--json` - Output as JSON



## `forager offdesk wiki runtime-policy-acks`

List strict runtime projection policy acknowledgements

**Usage:** `forager offdesk wiki runtime-policy-acks [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk wiki runtime-policy-ack-report`

Report strict runtime projection acknowledgements that need attention

**Usage:** `forager offdesk wiki runtime-policy-ack-report [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to evaluate for query-specific ack applicability
* `--project-key <PROJECT_KEY>` - Project key scope to evaluate for query-specific ack applicability
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to evaluate for query-specific ack applicability
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to evaluate for query-specific ack applicability
* `--max-entries <MAX_ENTRIES>` - Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` - Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` - Maximum characters kept per projected instruction; 0 disables truncation
* `--near-expiry-hours <NEAR_EXPIRY_HOURS>` - Mark active acknowledgements expiring within this many hours

  Default value: `6`
* `--json` - Output as JSON



## `forager offdesk wiki review-after-report`

Report promoted entries whose review_after needs attention

**Usage:** `forager offdesk wiki review-after-report [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to match
* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to match
* `--near-expiry-hours <NEAR_EXPIRY_HOURS>` - Mark entries needing review within this many hours

  Default value: `168`
* `--json` - Output as JSON



## `forager offdesk wiki ack-runtime-policy`

Acknowledge strict review_after exclusion for runtime projection

**Usage:** `forager offdesk wiki ack-runtime-policy [OPTIONS]`

###### **Options:**

* `--session-id <SESSION_ID>` - Session/request scope to match exactly
* `--project-key <PROJECT_KEY>` - Project key scope to match
* `--artifact-kind <ARTIFACT_KIND>` - Artifact kind scope to match
* `--agent-mode <AGENT_MODE>` - Agent work mode scope to match
* `--scope-mode <SCOPE_MODE>` - Acknowledgement scope: exact-query or project-artifact

  Default value: `exact-query`
* `--max-entries <MAX_ENTRIES>` - Maximum selected projection entries
* `--max-context-chars <MAX_CONTEXT_CHARS>` - Maximum estimated runtime context characters
* `--max-instruction-chars <MAX_INSTRUCTION_CHARS>` - Maximum characters kept per projected instruction; 0 disables truncation
* `--ttl-hours <TTL_HOURS>` - Acknowledgement TTL in hours

  Default value: `24`
* `--reason <REASON>` - Operator reason for enabling strict runtime projection in this scope

  Default value: ``
* `--json` - Output as JSON



## `forager offdesk wiki lint`

Lint adaptive wiki state

**Usage:** `forager offdesk wiki lint [OPTIONS]`

###### **Options:**

* `--json` - Output as JSON



## `forager offdesk wiki export-markdown`

Export adaptive wiki state as a one-way markdown vault

**Usage:** `forager offdesk wiki export-markdown [OPTIONS]`

###### **Options:**

* `--output <OUTPUT>` - Directory to write the markdown vault into; defaults to the active profile's wiki-vault
* `--dry-run` - Preview export files without writing them
* `--json` - Output as JSON



## `forager offdesk wiki graph`

Export a read-only adaptive wiki tag graph

**Usage:** `forager offdesk wiki graph [OPTIONS]`

###### **Options:**

* `--output <OUTPUT>` - Optional directory to write graph.json and graph.md into
* `--dry-run` - Preview graph export files without writing them
* `--json` - Output as JSON



## `forager offdesk wiki review`

Generate a recommendation-only adaptive wiki review report

**Usage:** `forager offdesk wiki review [OPTIONS]`

###### **Options:**

* `--dry-run` - Preview recommendations without writing report files
* `--active-only` - Show proposals that are open or have stale lifecycle decisions
* `--decided-only` - Show proposals with non-stale accepted, rejected, or superseded decisions
* `--stale-only` - Show proposals whose latest lifecycle decision is stale
* `--json` - Output as JSON



## `forager offdesk wiki evaluate-episode`

Evaluate one adaptive wiki entry across in-scope and out-of-scope projections

**Usage:** `forager offdesk wiki evaluate-episode [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Promoted adaptive wiki entry id expected to appear only in the in-scope projection

###### **Options:**

* `--session-id <SESSION_ID>` - In-scope session/request id to match
* `--project-key <PROJECT_KEY>` - In-scope project key to match
* `--artifact-kind <ARTIFACT_KIND>` - In-scope artifact kind to match
* `--agent-mode <AGENT_MODE>` - In-scope agent work mode to match
* `--out-session-id <OUT_SESSION_ID>` - Out-of-scope session/request id. Defaults to a generated non-matching value
* `--out-project-key <OUT_PROJECT_KEY>` - Out-of-scope project key. Defaults to a generated non-matching value
* `--out-artifact-kind <OUT_ARTIFACT_KIND>` - Out-of-scope artifact kind. Defaults to a generated non-matching value
* `--out-agent-mode <OUT_AGENT_MODE>` - Out-of-scope agent work mode. Defaults to a generated non-matching mode when possible
* `--dry-run` - Preview the report without writing report files
* `--json` - Output as JSON



## `forager offdesk wiki episode-trace`

Trace live task/probe/wiki evidence for adaptive behavior review

**Usage:** `forager offdesk wiki episode-trace [OPTIONS]`

###### **Options:**

* `--request-id <REQUEST_ID>` - Filter trace events by request id
* `--task-id <TASK_ID>` - Filter trace events by task id
* `--project-key <PROJECT_KEY>` - Filter trace events by project key
* `--artifact-kind <ARTIFACT_KIND>` - Filter trace events by artifact kind
* `--entry-id <ENTRY_ID>` - Filter trace events by adaptive wiki entry id
* `--dry-run` - Preview the trace without writing report files
* `--json` - Output as JSON



## `forager offdesk wiki evaluate-recurrence`

Evaluate whether corrections recur after an entry is promoted

**Usage:** `forager offdesk wiki evaluate-recurrence [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Promoted adaptive wiki entry id to evaluate

###### **Options:**

* `--dry-run` - Preview the report without writing report files
* `--json` - Output as JSON



## `forager offdesk wiki promotion-chain`

Reconstruct the evidence chain captured at promotion time

**Usage:** `forager offdesk wiki promotion-chain [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Promoted adaptive wiki entry id to reconstruct

###### **Options:**

* `--dry-run` - Preview the report without writing report files
* `--json` - Output as JSON



## `forager offdesk wiki record-candidate`

Record an operator-authored learning candidate (e.g. from a doc review)

**Usage:** `forager offdesk wiki record-candidate [OPTIONS] --kind <KIND> --scope <SCOPE> --claim <CLAIM>`

###### **Options:**

* `--kind <KIND>` - Knowledge kind
* `--scope <SCOPE>` - Applicability scope
* `--scope-ref <SCOPE_REF>` - Scope reference (e.g. project key). Required unless scope is user_global
* `--claim <CLAIM>` - One-line durable claim
* `--ai-instruction <AI_INSTRUCTION>` - Compact instruction for the AI projection

  Default value: ``
* `--human-summary <HUMAN_SUMMARY>` - Operator-facing governance summary

  Default value: ``
* `--evidence-ref <EVIDENCE_REFS>` - Evidence reference (repeatable), e.g. doc:/path/AGENTS.md#section
* `--agent-mode <AGENT_MODES>` - Agent work mode this candidate applies to (repeatable; omit for universal)
* `--core-tag <CORE_TAGS>` - Controlled core tag (repeatable), e.g. domain/twinpaper or harness/dispatch
* `--proposed-tag <PROPOSED_TAGS>` - Proposed (reviewable) tag (repeatable)
* `--confidence <CONFIDENCE>` - Confidence level

  Default value: `explicit`
* `--origin <ORIGIN>` - Provenance of this candidate: who observed it

  Default value: `operator_explicit`
* `--signal-kind <SIGNAL_KIND>` - What kind of signal produced this candidate. operator_correction also appends a first-class correction record for recurrence evaluation

  Default value: `imported_doc`
* `--review-reason <REVIEW_REASON>` - Why this is worth reviewing/promoting

  Default value: ``
* `--json` - Output as JSON



## `forager offdesk wiki promote`

Promote a candidate into a scoped wiki entry

**Usage:** `forager offdesk wiki promote [OPTIONS] <CANDIDATE_ID>`

###### **Arguments:**

* `<CANDIDATE_ID>` - Adaptive wiki candidate id

###### **Options:**

* `--scope <SCOPE>` - Scope for the promoted entry. Defaults to the candidate scope
* `--scope-ref <SCOPE_REF>` - Scope reference for the promoted entry. Required when --scope is used
* `--activation-mode <ACTIVATION_MODE>` - Activation mode for the promoted entry

  Default value: `confirm`
* `--agent-mode <AGENT_MODES>` - Agent work mode this promoted entry should apply to. Repeat for multiple modes; omit to keep candidate modes
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--reason <REASON>` - Optional promotion reason for audit

  Default value: ``
* `--review-after-days <REVIEW_AFTER_DAYS>` - Review window in days: entries must be re-reviewed after this horizon (skepticism-by-default; 0 disables)

  Default value: `90`
* `--json` - Output as JSON



## `forager offdesk wiki reject`

Reject a candidate without creating an entry

**Usage:** `forager offdesk wiki reject [OPTIONS] --reason <REASON> <CANDIDATE_ID>`

###### **Arguments:**

* `<CANDIDATE_ID>` - Adaptive wiki candidate id

###### **Options:**

* `--reason <REASON>` - Reason for rejecting the candidate
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk wiki rescope`

Change an entry scope

**Usage:** `forager offdesk wiki rescope [OPTIONS] --scope <SCOPE> --scope-ref <SCOPE_REF> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--scope <SCOPE>` - New entry scope
* `--scope-ref <SCOPE_REF>` - New entry scope reference
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--reason <REASON>` - Optional rescope reason for audit

  Default value: ``
* `--json` - Output as JSON



## `forager offdesk wiki edit`

Edit an entry's classification, modes, text, or evidence refs in place

**Usage:** `forager offdesk wiki edit [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--kind <KIND>` - Replace the knowledge kind
* `--agent-mode <AGENT_MODES>` - Replace the agent-mode scope (repeatable)
* `--clear-agent-modes` - Make the entry apply to every agent mode
* `--claim <CLAIM>` - Replace the durable claim
* `--ai-instruction <AI_INSTRUCTION>` - Replace the compact AI instruction
* `--human-summary <HUMAN_SUMMARY>` - Replace the operator-facing summary
* `--evidence-ref <EVIDENCE_REFS>` - Add an evidence reference (repeatable)
* `--by <BY>` - Operator or surface performing the edit

  Default value: `cli`
* `--reason <REASON>` - Optional edit reason for audit

  Default value: ``
* `--json` - Output as JSON



## `forager offdesk wiki add-tag`

Add controlled/proposed tags to an entry (e.g. facet/* or domain/*)

**Usage:** `forager offdesk wiki add-tag [OPTIONS] <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--core-tag <CORE_TAGS>` - Controlled core tag (repeatable), e.g. facet/research or domain/twinpaper
* `--proposed-tag <PROPOSED_TAGS>` - Proposed (reviewable) tag (repeatable)
* `--by <BY>` - Operator or surface performing the retag

  Default value: `cli`
* `--reason <REASON>` - Optional retag reason for audit

  Default value: ``
* `--json` - Output as JSON



## `forager offdesk wiki deprecate`

Deprecate an entry so it no longer appears in AI projection

**Usage:** `forager offdesk wiki deprecate [OPTIONS] --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--reason <REASON>` - Reason for deprecating the entry
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk wiki renew-review-after`

Renew an entry review_after timestamp without changing scope or instruction

**Usage:** `forager offdesk wiki renew-review-after [OPTIONS] --review-after <REVIEW_AFTER> --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--review-after <REVIEW_AFTER>` - New review_after timestamp in RFC3339 format
* `--reason <REASON>` - Reason for renewing the review timestamp
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk wiki add-counterexample`

Add a counterexample evidence ref to an entry

**Usage:** `forager offdesk wiki add-counterexample [OPTIONS] --evidence-ref <EVIDENCE_REF> --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki entry id

###### **Options:**

* `--evidence-ref <EVIDENCE_REF>` - Evidence ref that contradicts or limits the entry
* `--reason <REASON>` - Reason for recording the counterexample
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--json` - Output as JSON



## `forager offdesk wiki update-runbook`

Attach governed runbook support refs to a procedure entry

**Usage:** `forager offdesk wiki update-runbook [OPTIONS] --reason <REASON> <ENTRY_ID>`

###### **Arguments:**

* `<ENTRY_ID>` - Adaptive wiki procedure entry id

###### **Options:**

* `--support-ref <SUPPORT_REF>` - Human/export support ref such as references/foo.md, templates/foo.md, or scripts/foo.sh
* `--capability-id <CAPABILITY_ID>` - Capability id this procedure is relevant to
* `--required-artifact-kind <REQUIRED_ARTIFACT_KIND>` - Required artifact kind this procedure depends on
* `--reason <REASON>` - Reason for updating the runbook metadata
* `--by <BY>` - Operator or surface performing the review

  Default value: `cli`
* `--json` - Output as JSON



## `forager ondesk`

Capture ondesk notes and prompt context from external harness work

**Usage:** `forager ondesk <COMMAND>`

###### **Subcommands:**

* `plan-inspect` - Inspect an explicitly selected Markdown task plan without importing or changing it
* `prepare-offdesk` - Draft shared night readiness from exact owning tasks without writes or model calls
* `plan-adopt-proposal` - Propose explicit task mapping in a plain owning TODO without changing it
* `plan-apply` - Apply one explicitly confirmed, hash-bound change to an existing plan
* `plan-review-proposal` - Preview attributed review decisions without writing a plan or starting work
* `return-binding` - Prepare a read-only binding from preserved execution copies to an original owning plan
* `note` - Append a safe operator note for an ondesk session or project
* `capture` - Capture live harness scrollback into an inspectable prompt package
* `prompt-package` - Build a markdown prompt package from recent notes and optional capture
* `return` - Preview and explicitly deliver a morning package to one ready local session
* `review-surface` - Emit the shared review surface for Ondesk and future rich UIs
* `workstation-surface` - Emit the workstation dashboard surface for the Web UI control plane
* `action-envelope` - Validate a Web UI action envelope and record a receipt
* `accepted-truth-recovery-envelope` - Validate an accepted-truth recovery envelope and record a receipt
* `action-preflight` - Preflight a validated action receipt before any mutation-capable executor
* `action-decision` - Execute a supported decision action from a ready action preflight
* `action-closeout` - Close an applied decision action execution with a canonical decision receipt
* `runtime-preflight` - Preflight a receipted decision action closeout before runtime dispatch
* `runtime-dispatch` - Queue runtime work from a ready runtime dispatch preflight



## `forager ondesk plan-inspect`

Inspect an explicitly selected Markdown task plan without importing or changing it

**Usage:** `forager ondesk plan-inspect [OPTIONS] --project-key <PROJECT_KEY> <PATH>`

###### **Arguments:**

* `<PATH>` - Existing owning Markdown document with one forager-plan JSON fence

###### **Options:**

* `--project-key <PROJECT_KEY>`
* `--candidate <CANDIDATE>` - Candidate document to compare, without applying any changes
* `--expected-sha256 <EXPECTED_SHA256>`



## `forager ondesk prepare-offdesk`

Draft shared night readiness from exact owning tasks without writes or model calls

**Usage:** `forager ondesk prepare-offdesk [OPTIONS] --source <SOURCES> --task <TASKS>`

###### **Options:**

* `--source <SOURCES>` - Explicit owning plans in PROJECT_KEY=PATH form; one source per project
* `--task <TASKS>` - Explicit selections in PROJECT_KEY/TASK_ID form
* `--expected-source-sha256 <EXPECTED>` - Optional exact source versions in PROJECT_KEY=SHA256 form



## `forager ondesk plan-adopt-proposal`

Propose explicit task mapping in a plain owning TODO without changing it

**Usage:** `forager ondesk plan-adopt-proposal --tasks <TASKS> --project-key <PROJECT_KEY> --expected-sha256 <EXPECTED_SHA256> --request-id <REQUEST_ID> --decision-ref <DECISION_REF> <SOURCE>`

###### **Arguments:**

* `<SOURCE>` - Absolute existing plain Markdown owning TODO

###### **Options:**

* `--tasks <TASKS>` - Explicit ondesk_task_plan.v1 JSON selection; checklist text is not imported
* `--project-key <PROJECT_KEY>`
* `--expected-sha256 <EXPECTED_SHA256>`
* `--request-id <REQUEST_ID>`
* `--decision-ref <DECISION_REF>`



## `forager ondesk plan-apply`

Apply one explicitly confirmed, hash-bound change to an existing plan

**Usage:** `forager ondesk plan-apply [OPTIONS] --candidate <CANDIDATE> <REQUEST>`

###### **Arguments:**

* `<REQUEST>` - JSON change request binding the exact source path and both content hashes

###### **Options:**

* `--candidate <CANDIDATE>`
* `--confirm-plan-write` - Confirm only the exact plan write, not execution or knowledge promotion



## `forager ondesk plan-review-proposal`

Preview attributed review decisions without writing a plan or starting work

**Usage:** `forager ondesk plan-review-proposal [OPTIONS] --decisions <DECISIONS> <SOURCE>`

###### **Arguments:**

* `<SOURCE>`

###### **Options:**

* `--decisions <DECISIONS>`
* `--reconcile-baseline <RECONCILE_BASELINE>` - Exact preserved execution-era plan, for narrative/format-only drift
* `--reconciliation-reason <RECONCILIATION_REASON>` - Operator's reason for connecting this execution baseline to the current plan



## `forager ondesk return-binding`

Prepare a read-only binding from preserved execution copies to an original owning plan

**Usage:** `forager ondesk return-binding --owning-plan <OWNING_PLAN> --project-key <PROJECT_KEY> --morning-review <MORNING_REVIEW> --by <BY> --decision-ref <DECISION_REF> <WORKDIR>`

###### **Arguments:**

* `<WORKDIR>` - Absolute original project root

###### **Options:**

* `--owning-plan <OWNING_PLAN>`
* `--project-key <PROJECT_KEY>`
* `--morning-review <MORNING_REVIEW>`
* `--by <BY>` - Operator attribution; preparation grants no task disposition
* `--decision-ref <DECISION_REF>`



## `forager ondesk note`

Append a safe operator note for an ondesk session or project

**Usage:** `forager ondesk note [OPTIONS] --text <TEXT> [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--text <TEXT>` - Operator note text to persist
* `--mode <MODE>` - Work mode label, e.g. planning, analysis, writing, critique
* `--project-key <PROJECT_KEY>` - Stable project key for grouping ondesk knowledge
* `--json` - Output as JSON



## `forager ondesk capture`

Capture live harness scrollback into an inspectable prompt package

**Usage:** `forager ondesk capture [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--lines <LINES>` - Number of tmux scrollback lines to capture

  Default value: `200`
* `--mode <MODE>` - Work mode label, e.g. planning, analysis, writing, critique
* `--project-key <PROJECT_KEY>` - Stable project key for grouping ondesk knowledge
* `--include-git` - Include read-only git status and diff-stat from the session/project path
* `--json` - Output as JSON



## `forager ondesk prompt-package`

Build a markdown prompt package from recent notes and optional capture

**Usage:** `forager ondesk prompt-package [OPTIONS] [IDENTIFIER]`

###### **Arguments:**

* `<IDENTIFIER>` - Session ID, title, or project path. Defaults to current tmux Forager session or cwd

###### **Options:**

* `--morning-review <MORNING_REVIEW>` - Exact saved shift-review reference JSON for morning recovery
* `--owning-plan <OWNING_PLAN>` - Absolute current owning plan for morning recovery
* `--return-binding <RETURN_BINDING>` - Exact operator-attributed binding for an execution-copy return
* `--return-binding-sha256 <RETURN_BINDING_SHA256>` - SHA256 of the selected return-binding artifact
* `--review-only-unstructured` - Build a review-only return for an existing plan without a forager-plan block
* `--resume-session <RESUME_SESSION>` - Existing session ID to bind without sending input
* `--expected-branch <EXPECTED_BRANCH>` - Require the currently selected Git branch
* `--capture-id <CAPTURE_ID>` - Existing capture ID to render
* `--mode <MODE>` - Work mode label used to filter notes
* `--project-key <PROJECT_KEY>` - Stable project key used to filter notes
* `--include-doc-audit` - Include a fresh documentation governance audit from the latest closeout workdir or resolved project path
* `--output <OUTPUT>` - Write markdown package to a file instead of stdout
* `--json` - Output metadata as JSON



## `forager ondesk return`

Preview and explicitly deliver a morning package to one ready local session

**Usage:** `forager ondesk return <COMMAND>`

###### **Subcommands:**

* `launch-restricted` - Launch a stopped registered session under the canonical Landlock read policy
* `preview` - Build a short-lived preview without sending input or writing runtime state
* `send` - Attempt input once after confirming the exact preview hash
* `observe` - Observe a bound agent read-claim marker without sending more input
* `status` - Read the durable delivery record without contacting the session



## `forager ondesk return launch-restricted`

Launch a stopped registered session under the canonical Landlock read policy

**Usage:** `forager ondesk return launch-restricted --session <SESSION>`

###### **Options:**

* `--session <SESSION>` - Exact explicitly selected stopped session ID; runs its registered command



## `forager ondesk return preview`

Build a short-lived preview without sending input or writing runtime state

**Usage:** `forager ondesk return preview [OPTIONS] --package <PACKAGE> --session <SESSION>`

###### **Options:**

* `--read-policy-receipt <READ_POLICY_RECEIPT>` - Require the matching live restricted-launch receipt; absence keeps legacy behavior
* `--package <PACKAGE>` - Absolute saved morning prompt-package path
* `--session <SESSION>` - Exact explicitly selected session ID
* `--output <OUTPUT>` - Save a new ticket file; keep it outside the worktree or in ignored storage



## `forager ondesk return send`

Attempt input once after confirming the exact preview hash

**Usage:** `forager ondesk return send [OPTIONS] --expected-preview-sha256 <EXPECTED_PREVIEW_SHA256> <TICKET>`

###### **Arguments:**

* `<TICKET>` - Absolute prepared ticket JSON path

###### **Options:**

* `--expected-preview-sha256 <EXPECTED_PREVIEW_SHA256>` - Exact preview hash printed by return preview
* `--confirm-input` - Explicit consent to context-reading input, not task execution



## `forager ondesk return observe`

Observe a bound agent read-claim marker without sending more input

**Usage:** `forager ondesk return observe <REQUEST_ID>`

###### **Arguments:**

* `<REQUEST_ID>`



## `forager ondesk return status`

Read the durable delivery record without contacting the session

**Usage:** `forager ondesk return status <REQUEST_ID>`

###### **Arguments:**

* `<REQUEST_ID>`



## `forager ondesk review-surface`

Emit the shared review surface for Ondesk and future rich UIs

**Usage:** `forager ondesk review-surface [OPTIONS]`

###### **Options:**

* `--project-key <PROJECT_KEY>` - Stable project key to focus the review packet. Defaults to all projects
* `--shift-id <SHIFT_ID>` - Exact bounded shift to project. Defaults to the active shift, then latest
* `--current-run-id <CURRENT_RUN_ID>` - Routine run currently generating this projection
* `--json` - Emit compact JSON. Without this flag, a human summary is printed



## `forager ondesk workstation-surface`

Emit the workstation dashboard surface for the Web UI control plane

**Usage:** `forager ondesk workstation-surface [OPTIONS]`

###### **Options:**

* `--shift-id <SHIFT_ID>` - Exact bounded shift to project. Defaults to the active shift, then latest
* `--current-run-id <CURRENT_RUN_ID>` - Routine run currently generating this projection
* `--json` - Emit compact JSON. Without this flag, a human summary is printed



## `forager ondesk action-envelope`

Validate a Web UI action envelope and record a receipt

**Usage:** `forager ondesk action-envelope [OPTIONS] --envelope <ENVELOPE>`

###### **Options:**

* `--envelope <ENVELOPE>` - JSON file containing an action_envelope.v1 preview
* `--dry-run` - Validate without writing action_envelope_receipts.jsonl
* `--json` - Output as JSON



## `forager ondesk accepted-truth-recovery-envelope`

Validate an accepted-truth recovery envelope and record a receipt

**Usage:** `forager ondesk accepted-truth-recovery-envelope [OPTIONS] --envelope <ENVELOPE>`

###### **Options:**

* `--envelope <ENVELOPE>` - JSON file containing an accepted_truth_recovery_action_envelope.v1 preview
* `--dry-run` - Validate without writing accepted_truth_recovery_action_receipts.jsonl
* `--json` - Output as JSON



## `forager ondesk action-preflight`

Preflight a validated action receipt before any mutation-capable executor

**Usage:** `forager ondesk action-preflight [OPTIONS] --receipt-id <RECEIPT_ID>`

###### **Options:**

* `--receipt-id <RECEIPT_ID>` - Receipt ID from action_envelope_receipts.jsonl
* `--dry-run` - Validate without writing action_execution_preflights.jsonl
* `--json` - Output as JSON



## `forager ondesk action-decision`

Execute a supported decision action from a ready action preflight

**Usage:** `forager ondesk action-decision [OPTIONS] --preflight-id <PREFLIGHT_ID>`

###### **Options:**

* `--preflight-id <PREFLIGHT_ID>` - Ready action_execution_preflight.v1 ID
* `--note <NOTE>` - Required bounded direction for revise/block/custom decisions

  Default value: ``
* `--by <BY>` - Actor recording the decision action

  Default value: `operator`
* `--target <TARGET>` - Override execution handoff target
* `--dry-run` - Validate without appending the decision record or execution receipt
* `--json` - Output as JSON



## `forager ondesk action-closeout`

Close an applied decision action execution with a canonical decision receipt

**Usage:** `forager ondesk action-closeout [OPTIONS] --execution-id <EXECUTION_ID>`

###### **Options:**

* `--execution-id <EXECUTION_ID>` - Applied decision_action_execution.v1 ID
* `--by <BY>` - Actor recording the closeout receipt

  Default value: `operator`
* `--result-status <RESULT_STATUS>` - Result status for the consumed decision action handoff

  Default value: `closed`
* `--evidence <EVIDENCE_SUMMARY>` - Evidence summary line. Repeat for multiple lines
* `--remaining-review <REMAINING_REVIEW>` - Remaining review item. Repeat for multiple lines
* `--dry-run` - Validate without appending the decision receipt or closeout record
* `--json` - Output as JSON



## `forager ondesk runtime-preflight`

Preflight a receipted decision action closeout before runtime dispatch

**Usage:** `forager ondesk runtime-preflight [OPTIONS] --closeout-id <CLOSEOUT_ID>`

###### **Options:**

* `--closeout-id <CLOSEOUT_ID>` - Receipted decision_action_closeout.v1 ID
* `--dry-run` - Validate without writing runtime_dispatch_preflights.jsonl
* `--json` - Output as JSON



## `forager ondesk runtime-dispatch`

Queue runtime work from a ready runtime dispatch preflight

**Usage:** `forager ondesk runtime-dispatch [OPTIONS] --preflight-id <PREFLIGHT_ID> --runner <RUNNER> --cmd <COMMAND>`

###### **Options:**

* `--preflight-id <PREFLIGHT_ID>` - Ready runtime_dispatch_preflight.v1 ID
* `--runner <RUNNER>` - Runner backend to queue for later offdesk tick dispatch
* `--cmd <COMMAND>` - Shell command to execute when the queued task is dispatched
* `--workdir <WORKDIR>` - Working directory for --cmd. Defaults to the current directory
* `--task-id <TASK_ID>` - Task ID. Generated deterministically if omitted
* `--capability-id <CAPABILITY_ID>` - Capability ID. Currently restricted to dispatch.runtime

  Default value: `dispatch.runtime`
* `--provider-id <PROVIDER_ID>` - Provider ID to check against provider capacity cooldown state when dispatched
* `--model <MODEL>` - Provider model to check against provider capacity cooldown state when dispatched
* `--log-artifact <LOG_ARTIFACT>` - Log artifact path for command stdout and stderr
* `--result-artifact <RESULT_ARTIFACT>` - Result sidecar path used by tick to mark the task completed
* `--dry-run` - Validate without writing offdesk_tasks.json or runtime_dispatch_receipts.jsonl
* `--json` - Output as JSON



## `forager tmux`

tmux integration utilities

**Usage:** `forager tmux <COMMAND>`

###### **Subcommands:**

* `status` - Output session info for use in custom tmux status bar



## `forager tmux status`

Output session info for use in custom tmux status bar

Add this to your ~/.tmux.conf: set -g status-right "#(forager tmux status)"

**Usage:** `forager tmux status [OPTIONS]`

###### **Options:**

* `-f`, `--format <FORMAT>` - Output format (text or json)

  Default value: `text`



## `forager sounds`

Manage sound effects for agent state transitions

**Usage:** `forager sounds <COMMAND>`

###### **Subcommands:**

* `install` - Install bundled sound effects
* `list` - List currently installed sounds
* `test` - Test a sound by playing it



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

* `<NAME>` - Sound file name (without extension)



## `forager uninstall`

Uninstall Forager

**Usage:** `forager uninstall [OPTIONS]`

###### **Options:**

* `--keep-data` - Keep data directory (sessions, config, logs)
* `--keep-tmux-config` - Keep tmux configuration
* `--dry-run` - Show what would be removed without removing
* `-y` - Skip confirmation prompts



## `forager completion`

Generate shell completions

**Usage:** `forager completion <SHELL>`

###### **Arguments:**

* `<SHELL>` - Shell to generate completions for

  Possible values: `bash`, `elvish`, `fish`, `powershell`, `zsh`




<hr/>

<small><i>
    This document was generated automatically by
    <a href="https://crates.io/crates/clap-markdown"><code>clap-markdown</code></a>.
</i></small>
