# Configuration Reference

Forager uses a layered configuration system. Settings are resolved in this order:

1. **Global config** -- `~/.forager/config.toml` (or `~/.config/forager/config.toml` on Linux)
2. **Profile config** -- `~/.forager/profiles/<name>/config.toml`
3. **Repo config** -- `.forager/config.toml` in the project root

Forager still reads and writes the existing legacy paths when they
already exist: `~/.agent-of-empires`, `~/.config/agent-of-empires`, and
`.aoe/config.toml`.

Run `forager doctor` to see which global data path, repo config path, and
profile environment source are active on the current machine.

Run `forager migrate aoe` to copy existing legacy global data and the
current repo's `.aoe/config.toml` into the new Forager paths. The migration keeps
legacy paths as backups and refuses to overwrite existing Forager targets.

Later layers override earlier ones. Only explicitly set fields override; unset fields inherit from the previous layer.

All settings below can also be edited from the TUI settings screen (press `s` or access via the menu).

## File Locations

| Platform | Global Config |
|----------|--------------|
| Linux | `$XDG_CONFIG_HOME/forager/config.toml` (defaults to `~/.config/forager/`) |
| macOS | `~/.forager/config.toml` |

```
~/.forager/
  config.toml              # Global configuration
  trusted_repos.toml       # Hook trust decisions (auto-managed)
  .schema_version          # Migration tracking (auto-managed)
  profiles/
    default/
      sessions.json        # Session data
      groups.json          # Group hierarchy
      config.toml          # Profile-specific overrides
  logs/                    # Session execution logs
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `FORAGER_PROFILE` | Default profile to use |
| `FORAGER_DEBUG` | Enable debug logging (`1` to enable) |
| `AGENT_OF_EMPIRES_PROFILE` | Legacy fallback for `FORAGER_PROFILE` |
| `AGENT_OF_EMPIRES_DEBUG` | Legacy fallback for `FORAGER_DEBUG` |

## Session

```toml
[session]
default_tool = "claude"   # claude, opencode, vibe, codex, gemini
yolo_mode_default = false
```

| Option | Default | Description |
|--------|---------|-------------|
| `default_tool` | (auto-detect) | Default agent for new sessions. Falls back to the first available tool if unset or unavailable. |
| `yolo_mode_default` | `false` | Enable YOLO mode by default for new sessions (skip permission prompts). |

## Worktree

```toml
[worktree]
enabled = false
path_template = "../{repo-name}-worktrees/{branch}"
bare_repo_path_template = "./{branch}"
auto_cleanup = true
show_branch_in_tui = true
delete_branch_on_cleanup = false
```

| Option | Default | Description |
|--------|---------|-------------|
| `enabled` | `false` | Enable worktree support for new sessions |
| `path_template` | `../{repo-name}-worktrees/{branch}` | Path template for worktrees in regular repos |
| `bare_repo_path_template` | `./{branch}` | Path template for worktrees in bare repos |
| `auto_cleanup` | `true` | Prompt to remove worktree when deleting a session |
| `show_branch_in_tui` | `true` | Display branch name in the TUI session list |
| `delete_branch_on_cleanup` | `false` | Also delete the git branch when removing a worktree |

**Template variables:**

| Variable | Description |
|----------|-------------|
| `{repo-name}` | Repository folder name |
| `{branch}` | Branch name (slashes converted to hyphens) |
| `{session-id}` | First 8 characters of session UUID |

## tmux

```toml
[tmux]
status_bar = "auto"
mouse = "auto"
```

| Option | Default | Description |
|--------|---------|-------------|
| `status_bar` | `"auto"` | `"auto"`: apply if no `~/.tmux.conf`; `"enabled"`: always apply; `"disabled"`: never apply |
| `mouse` | `"auto"` | Same modes as `status_bar`. Controls mouse support in Forager tmux sessions. |

## Diff

```toml
[diff]
default_branch = "main"
context_lines = 3
```

| Option | Default | Description |
|--------|---------|-------------|
| `default_branch` | (auto-detect) | Base branch for diffs |
| `context_lines` | `3` | Lines of context around changes |

## Updates

```toml
[updates]
check_enabled = true
auto_update = false
check_interval_hours = 24
notify_in_cli = true
```

| Option | Default | Description |
|--------|---------|-------------|
| `check_enabled` | `true` | Check for new versions |
| `auto_update` | `false` | Automatically install updates |
| `check_interval_hours` | `24` | Hours between update checks |
| `notify_in_cli` | `true` | Show update notifications in CLI output |

## Claude

```toml
[claude]
config_dir = "~/.claude"
```

| Option | Default | Description |
|--------|---------|-------------|
| `config_dir` | (none) | Custom Claude Code config directory. Supports `~/` prefix. |

## Profiles

Profiles provide separate workspaces with their own sessions and groups. Each profile can override any of the settings above.

```bash
forager                 # Uses "default" profile
forager -p work         # Uses "work" profile
forager profile create client-xyz
forager profile list
forager profile default work   # Set "work" as default
```

Profile overrides go in `~/.forager/profiles/<name>/config.toml` and use the same format as the global config.

## Repo Config

Per-repo settings go in `.forager/config.toml` at your project root. Run `forager init` to generate a template. Existing `.aoe/config.toml` files are still honored.

Repo config supports: `[hooks]`, `[session]`, and `[worktree]` sections. It does not support `[tmux]`, `[updates]`, `[claude]`, or `[diff]` -- those are personal settings.

See [Repo Config & Hooks](repo-config.md) for details.
