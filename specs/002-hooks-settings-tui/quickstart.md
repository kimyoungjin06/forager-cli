# Quickstart: Hooks at Global/Profile Level & Repo Settings in TUI

## Verify Global Hooks

1. Open `~/.config/forager/config.toml` (Linux) or
   `~/.forager/config.toml` (macOS).
2. Add:
   ```toml
   [hooks]
   on_create = ["echo 'global create hook ran'"]
   on_launch = ["echo 'global launch hook ran'"]
   ```
3. Launch `forager` and create a new session for a repo without
   `.forager/config.toml`.
4. Verify "global create hook ran" appears during creation.
5. Verify "global launch hook ran" appears on launch.

## Verify Profile Override

1. Open `forager`, go to Settings (press 's').
2. Switch to Profile scope (Tab key).
3. Navigate to the "Hooks" tab.
4. Edit `on_create` to `["echo 'profile hook ran'"]`.
5. Save (Ctrl-s).
6. Create a new session - verify "profile hook ran" appears instead of
   "global create hook ran".
7. Verify `on_launch` still shows the global value ("global launch hook
   ran") since it was not overridden.

## Verify Repo Tab

1. Select a session on the home screen.
2. Open Settings (press 's').
3. Navigate to the "Repo" tab.
4. Add an `on_create` hook: `["echo 'repo hook ran'"]`.
5. Save (Ctrl-s).
6. Verify `.forager/config.toml` was created/updated in the project
   directory.
7. Create a new session for the same repo - verify "repo hook ran"
   appears and global/profile hooks do NOT run.

## Verify Override Hierarchy

1. Set global `on_create = ["echo global"]`.
2. Set profile `on_create = ["echo profile"]`.
3. Set repo `on_create = ["echo repo"]`.
4. Create session: expect "repo".
5. Remove repo hooks (clear `.forager/config.toml` hooks section).
6. Create session: expect "profile".
7. Clear profile override (press 'r' on the field in Profile scope).
8. Create session: expect "global".

## Verify Host Execution and Deferred Sandbox

1. Set global `on_create = ["pwd"]`.
2. Create a normal session.
3. Verify the hook output shows the host project directory.
4. Run `forager add --sandbox .`.
5. Verify the command is rejected with the deferred-sandbox message.
6. Run `forager add --sandbox-image custom/image .`.
7. Verify the command is rejected with the same deferred-sandbox message.

## Verify Failure Semantics

1. Set global `on_create = ["exit 1"]` (a command that always fails).
2. Create a session - verify session creation is aborted with an error.
3. Change to `on_launch = ["exit 1"]` (and remove the on_create hook).
4. Create a session - verify session creation succeeds.
5. Attach to the session - verify a warning is logged but the session
   starts normally.

## Verify Duplicate on_launch Prevention

1. Set global `on_launch = ["echo 'launch hook ran'"]`.
2. Create a new session - observe "launch hook ran" in creation output.
3. Immediately attach to the session - verify "launch hook ran" does NOT
   appear again (hooks skipped because they already ran during creation).

## Verify Repo Tab Disabled State

1. Deselect all sessions on the home screen (or have no sessions).
2. Open Settings.
3. Navigate to "Repo" tab.
4. Verify the tab shows a disabled/placeholder message.
