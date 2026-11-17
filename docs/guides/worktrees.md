# Worktrees Reference

Reference documentation for git worktree commands and configuration in `forager`.

For workflow guidance, see the [Workflow Guide](workflow.md).

## CLI vs TUI Behavior

| Feature | CLI | TUI |
|---------|-----|-----|
| Create new branch | Use `-b` flag | Always creates new branch |
| Use existing branch | Omit `-b` flag | Not supported |
| Branch validation | Checks branch selection | Requests a new branch; Git rejects invalid or existing names |

## CLI Commands

```bash
# Create worktree session (new branch)
forager add . -w feat/my-feature -b

# Create worktree session (existing branch)
forager add . -w feat/my-feature

# List all worktrees
forager worktree list

# Show session info
forager worktree info <session>

# Find orphaned worktrees
forager worktree cleanup

# Remove session, preserving its worktree
forager remove <session>

# Remove session and delete worktree
forager remove <session> --delete-worktree
```

## TUI Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `n` | New session dialog |
| `Tab` | Next field |
| `Shift+Tab` | Previous field |
| `Enter` | Submit and create session |
| `Esc` | Cancel |

When creating a session with a worktree branch name in the TUI, it automatically creates a new branch and worktree.

## Configuration

```toml
[worktree]
path_template = "../{repo-name}-worktrees/{branch}"
bare_repo_path_template = "./{branch}"
auto_cleanup = true
show_branch_in_tui = true
delete_branch_on_cleanup = false
```

### Template Variables

| Variable | Description |
|----------|-------------|
| `{repo-name}` | Repository folder name |
| `{branch}` | Branch name (slashes converted to hyphens) |
| `{session-id}` | First 8 characters of session UUID |

### Path Template Examples

```toml
# Default (sibling directory) - used for non-bare repos
path_template = "../{repo-name}-worktrees/{branch}"

# Bare repo default (worktrees as siblings)
bare_repo_path_template = "./{branch}"

# Nested in repo
path_template = "./worktrees/{branch}"

# Absolute path
path_template = "/absolute/path/to/worktrees/{repo-name}/{branch}"

# With session ID for uniqueness
path_template = "../wt/{branch}-{session-id}"
```

The retired `worktree.enabled` key does not control creation. Request a worktree
explicitly with `-w` or the TUI branch field.

## Cleanup Behavior

| Scenario | Cleanup Prompt? |
|----------|-----------------|
| Managed worktree, CLI without `--delete-worktree` | No; worktree is preserved |
| Managed worktree, TUI deletion | Offered according to cleanup settings |
| Manual worktree | Not automatically selected for deletion |
| `--delete-worktree` flag | Yes (deletes worktree) |
| Non-worktree session | No |

## Auto-Detection

Forager automatically detects bare repos and uses `bare_repo_path_template` instead of `path_template`, creating worktrees as siblings within the project directory.

## File Locations

| Item | Path |
|------|------|
| Config | `<app-dir>/config.toml` |
| Sessions | `<app-dir>/profiles/<profile>/sessions.json` |

On Linux, `<app-dir>` defaults to `$XDG_CONFIG_HOME/forager` or `~/.config/forager`;
on macOS it is `~/.forager`. Use `forager doctor` to resolve active and legacy
paths. See [Configuration](configuration.md#file-locations).

## Error Messages

| Error | Solution |
|-------|----------|
| "Not in a git repository" | Navigate to a git repo first |
| "Worktree already exists" | Use different branch name or add `{session-id}` to template |
| "Failed to remove worktree" | May need manual cleanup with `git worktree remove` |
| "Branch already exists" (CLI) | Branch exists; remove `-b` flag to use existing branch |
