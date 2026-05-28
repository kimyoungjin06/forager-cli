# tmux Status Bar

Forager can display session information in your tmux status bar, showing:
- **Session title**: The name of your Forager session
- **Git branch**: For worktree sessions

## How It Works

When you start a session, Forager sets tmux user options (`@forager_title`, `@forager_branch`) and configures the status bar to display this information with Forager's KISTI-aligned blue theme. Stored legacy sandbox metadata may also appear through `@forager_sandbox`. During the rename transition, Forager also writes and reads legacy `@aoe_*` options for existing integrations.

**Example status bars:**
```
forager: My Session | 14:30                           # Basic session
forager: My Session | feature-branch | 14:30          # Worktree session
```

## Auto Mode (Default)

By default, Forager uses "auto" mode for the status bar:

- **If you don't have a `~/.tmux.conf`**: Forager automatically styles the status bar for Forager sessions
- **If you have a `~/.tmux.conf`**: Forager assumes you prefer your own configuration and does not modify the status bar

This ensures beginners get a helpful status bar out of the box, while experienced tmux users retain full control.

## Configuration

Configure the status bar behavior in `~/.forager/config.toml` (`~/.config/forager/config.toml` on Linux):

```toml
[tmux]
# "auto" (default) - Apply only if no ~/.tmux.conf exists
# "enabled"        - Always apply Forager status bar styling
# "disabled"       - Never apply, use your own tmux config
status_bar = "auto"
mouse = "auto"    # Same modes: auto, enabled, disabled
```

### Values

| Value | Description |
|-------|-------------|
| `auto` | Apply status bar if user has no tmux config (default) |
| `enabled` | Always apply Forager status bar to Forager sessions |
| `disabled` | Never modify tmux status bar |

## Custom Integration

If you have your own tmux configuration but want to display Forager session info, use the `forager tmux status` command.

### Basic Integration

Add this to your `~/.tmux.conf`:

```tmux
set -g status-right "#(forager tmux status) | %H:%M"
```

This will show the Forager session title and branch when attached to a Forager session, and nothing when in other tmux sessions.

### JSON Output

For more advanced scripting:

```bash
forager tmux status --format json
```

Output:
```json
{"title": "My Session", "branch": "feature-branch", "sandbox": null}
```

Returns `null` if not in a Forager session.

### Example: Conditional Display

```tmux
# Only show Forager info if in a Forager session
set -g status-right "#{?#{==:#(forager tmux status),},,%#(forager tmux status) | }%H:%M"
```

## tmux User Options

When Forager starts a session with status bar enabled, it sets these tmux options:

| Option | Description |
|--------|-------------|
| `@forager_title` | Session title |
| `@forager_branch` | Git branch (worktree sessions only) |
| `@forager_sandbox` | Legacy sandbox metadata from stored sessions, if present |

You can reference these in your own tmux config:

```tmux
set -g status-right "#{@forager_title} #{@forager_branch} #{@forager_sandbox} | %H:%M"
```

Legacy sessions and custom configs that still use `@aoe_title`, `@aoe_branch`,
or `@aoe_sandbox` continue to work. New sessions use the `forager_` tmux
session prefix; existing `aoe_` sessions are still recognized by `forager tmux
status`.

## Troubleshooting

### Status bar not showing

1. Check if you have a `~/.tmux.conf` or `~/.config/tmux/tmux.conf`
2. If so, either:
   - Set `status_bar = "enabled"` in your Forager config
   - Or add `forager tmux status` to your tmux.conf manually

### Status bar shows old info

The tmux user options are set when the session starts. If you rename a session in Forager, the status bar will show the old name until you restart the session.

### Branch not showing

Branch is only displayed for worktree sessions (sessions created with `forager add --worktree`). Regular sessions don't have a fixed branch.
