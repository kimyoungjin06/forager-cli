# Workflow Guide

Use an existing Git clone for your first session. Optional worktrees give
parallel tasks separate working directories and branches. They still share Git
objects and the host filesystem; task ownership remains an operator decision.

```bash
cd /path/to/project
forager add . -c codex
forager
```

Choose an installed agent with `-c`. See [Quick Start](../quick-start.md) for
attachment and keyboard controls, and [Operation Cycle](operation-cycle.md)
when preparing unattended work.

## Optional Project Setup: Bare Git Repos

For a new checkout, one optional layout uses a "bare repo" structure. This keeps your main repository and all worktrees organized under a single directory:

```
my-project/
  .bare/               # Bare git repository
  .git                 # File pointing to .bare
  main/                # Worktree for main branch
  feat-api/            # Worktree for feature branch
  fix-bug/             # Another worktree
```

### Initial Setup

```bash
# Clone as bare repo
git clone --bare git@github.com:user/repo.git my-project/.bare

cd my-project

# Create .git file pointing to bare repo
echo "gitdir: ./.bare" > .git

# Configure fetch to get all branches
git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"
git fetch origin

# Create your main worktree
git worktree add main main
```

Now when you run `forager` from `my-project/`, new worktrees are created as siblings (e.g., `my-project/feat-api/`) rather than in a separate directory.

### Why Bare Repos?

- **Clean organization**: Everything lives under one project directory
- **Predictable paths**: All worktrees stay under the project root
- **Easy navigation**: Switch between branches by switching directories

## Single-Window Workflow

Run `forager` in a single terminal and toggle between views:

| Key | View | Purpose |
|-----|------|---------|
| (default) | Agent View | Manage and interact with AI coding agents |
| `t` | Terminal View | Access paired terminals for git, builds, tests |

### Daily Workflow

**1. Start your day**

```bash
cd ~/scm/my-project
forager
```

You'll see your sessions in Agent View. Keep one session on `main` for general questions and pulling updates.

**2. Update main** (Terminal View)

- Press `t` to switch to Terminal View
- Select your main session, press `Enter` to attach to its terminal
- Run `git pull origin main`
- Detach with `Ctrl+b d`
- Press `t` to return to Agent View

**3. Create a new session**

- Press `n` to open the new session dialog
- Fill in the worktree field with your branch name (e.g., `feat/auth-refactor`)
- Press `Enter`

This creates:
- A new branch from your current HEAD
- A new worktree at the configured path (`./feat-auth-refactor/` in the bare layout above)
- A new session with an agent working in that worktree

**4. Work on your feature** (Agent View)

- Select your session and press `Enter` to attach
- Interact with the agent
- Detach with `Ctrl+b d` when done

**5. Run builds/tests** (Terminal View)

- Press `t` to switch to Terminal View
- Select the same session, press `Enter`
- Run your build commands, tests, git operations
- Detach with `Ctrl+b d`

**6. Clean up when done**

- In Agent View, select the session and press `d` to delete
- Answer `Y` to also remove the worktree

## Tips

- **Keep one session on main**: Use it for codebase questions and its terminal for `git pull`
- **One task, one session**: Assign each task a separate worktree and session by convention. Forager does not enforce exclusive task ownership.
- **Pull before creating**: Always update main before creating new sessions so branches start fresh
- **Let agents stay focused**: Git operations happen in the paired terminal, not in agent sessions

## Keyboard Reference

| Key | Action |
|-----|--------|
| `t` | Toggle between Agent View and Terminal View |
| `D` | Open [Diff View](diff-view.md) to review git changes |
| `a` | Open the read-only operator attention queue |
| `Enter` | Attach to agent (Agent View) or terminal (Terminal View) |
| `n` | Create new session |
| `d` | Delete session (Agent View only) |
| `?` | Show help |
| `Ctrl+b d` | Detach from tmux (return to Forager) |

## Non-Bare Repos

If you're not using a bare repo setup, Forager defaults to creating worktrees in a sibling directory:

```
~/scm/
  my-project/              # Your repo (stays on main)
  my-project-worktrees/    # Worktrees created here
    feat-auth-refactor/
    fix-bug/
```

You can customize this with `path_template` in your config. See the [Worktrees Reference](worktrees.md) for details.
