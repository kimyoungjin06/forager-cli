use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::Duration;

use anyhow::{Context, Result};
use forager::session::Instance;
use tempfile::TempDir;

fn app_dir(home: &Path) -> PathBuf {
    if cfg!(target_os = "linux") {
        home.join(".config/forager")
    } else {
        home.join(".forager")
    }
}

fn command(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_forager"));
    command.env("HOME", home);
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command
}

fn run(home: &Path, args: &[&str]) -> Result<Output> {
    command(home)
        .args(args)
        .output()
        .with_context(|| format!("run forager {}", args.join(" ")))
}

fn unique_session_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

fn setup_registered_session(
    temp: &TempDir,
    id: &str,
    title: &str,
    tool: &str,
) -> Result<(PathBuf, Instance)> {
    let project = temp.path().join("workspace/demo-project");
    fs::create_dir_all(&project)?;
    let config_root = temp.path().join(".config/forager");
    let profile_root = app_dir(temp.path()).join("profiles/default");
    fs::create_dir_all(&config_root)?;
    fs::create_dir_all(&profile_root)?;
    fs::write(
        config_root.join("projects.toml"),
        "schema = \"forager_project_registry.v1\"\n\n\
         [projects.demo]\n\
         display_name = \"Demo\"\n\
         workspace_patterns = [\"demo-project\"]\n\
         wiki_profile = \"demo-wiki\"\n",
    )?;
    let mut instance = Instance::new(title, project.to_string_lossy().as_ref());
    instance.id = id.to_string();
    instance.tool = tool.to_string();
    instance.command = tool.to_string();
    fs::write(
        profile_root.join("sessions.json"),
        serde_json::to_vec_pretty(&vec![instance.clone()])?,
    )?;
    fs::write(profile_root.join("groups.json"), b"[]")?;
    Ok((project, instance))
}

fn setup_registered_project(temp: &TempDir, folder: &str) -> Result<PathBuf> {
    let project = temp.path().join("workspace").join(folder);
    fs::create_dir_all(&project)?;
    let config_root = temp.path().join(".config/forager");
    fs::create_dir_all(&config_root)?;
    fs::write(
        config_root.join("projects.toml"),
        format!(
            "schema = \"forager_project_registry.v1\"\n\n\
             [projects.demo]\n\
             display_name = \"Demo\"\n\
             workspace_patterns = [\"{folder}\"]\n\
             wiki_profile = \"demo-wiki\"\n"
        ),
    )?;
    Ok(project)
}

struct TmuxSessionGuard {
    name: String,
}

impl TmuxSessionGuard {
    fn start(project: &Path, instance: &Instance, body: &str) -> Result<Self> {
        let name = forager::tmux::Session::generate_name(&instance.id, &instance.title);
        let script = project.join(format!(".{name}.sh"));
        fs::write(&script, body)?;
        let status = Command::new("tmux")
            .args([
                "new-session",
                "-d",
                "-s",
                &name,
                "-c",
                project.to_string_lossy().as_ref(),
                &format!("bash {}", script.to_string_lossy()),
            ])
            .status()?;
        anyhow::ensure!(status.success(), "start fixture tmux session");
        std::thread::sleep(Duration::from_millis(250));
        Ok(Self { name })
    }

    fn capture(&self) -> Result<String> {
        let output = Command::new("tmux")
            .args(["capture-pane", "-p", "-t", &self.name, "-S", "-80"])
            .output()?;
        anyhow::ensure!(output.status.success(), "capture fixture tmux session");
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }
}

impl Drop for TmuxSessionGuard {
    fn drop(&mut self) {
        let _ = Command::new("tmux")
            .args(["kill-session", "-t", &self.name])
            .status();
    }
}

fn tmux_available() -> bool {
    Command::new("tmux")
        .arg("-V")
        .output()
        .is_ok_and(|output| output.status.success())
}

#[test]
fn context_status_on_missing_profile_is_read_only() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let output = run(
        temp.path(),
        &["session", "context-status", "missing", "--json"],
    )?;
    assert!(!output.status.success());
    assert!(!app_dir(temp.path()).exists());

    let temp = tempfile::tempdir()?;
    let id = unique_session_id();
    let (_, instance) =
        setup_registered_session(&temp, &id, "forager_test_ReadOnlyStatus", "codex")?;
    let output = run(
        temp.path(),
        &["session", "context-status", &instance.id, "--json"],
    )?;
    assert!(output.status.success());
    assert!(!app_dir(temp.path()).join(".schema_version").exists());
    Ok(())
}

#[test]
fn ambiguous_session_target_fails_before_refresh_or_input() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let first_id = unique_session_id();
    let (project, first) = setup_registered_session(&temp, &first_id, "AmbiguousWiki", "codex")?;
    let mut second = Instance::new("AmbiguousWiki", project.to_string_lossy().as_ref());
    second.id = unique_session_id();
    second.tool = "codex".to_string();
    second.command = "codex".to_string();
    fs::write(
        app_dir(temp.path()).join("profiles/default/sessions.json"),
        serde_json::to_vec_pretty(&vec![first, second])?,
    )?;

    let output = run(
        temp.path(),
        &["session", "context-sync", "AmbiguousWiki", "--json"],
    )?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("Ambiguous session identifier"));
    assert!(!project.join(".wiki-brief.md").exists());
    assert!(!app_dir(temp.path())
        .join("profiles/default/wiki_context_presentations.v1.json")
        .exists());
    Ok(())
}

#[test]
fn telegram_has_no_direct_context_sync_route() {
    let dispatch = include_str!("../scripts/telegram_operator/dispatch.py");
    let routing = include_str!("../scripts/telegram_operator/routing.py");
    assert!(!dispatch.contains("context-sync"));
    assert!(!routing.contains("context-sync"));
}

#[test]
fn busy_session_defers_without_changing_pane_bytes() -> Result<()> {
    if !tmux_available() {
        return Ok(());
    }
    let temp = tempfile::tempdir()?;
    let id = unique_session_id();
    let (project, instance) =
        setup_registered_session(&temp, &id, "forager_test_WikiBusy", "codex")?;
    let session = TmuxSessionGuard::start(
        &project,
        &instance,
        "#!/usr/bin/env bash\nprintf '• Working (2s • esc to interrupt)\\n\\n› Implement {feature}\\n'\nsleep 60\n",
    )?;
    let before = session.capture()?;
    let output = run(
        temp.path(),
        &[
            "session",
            "context-sync",
            &instance.id,
            "--require-fresh",
            "--json",
        ],
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result["receipt"]["presentation_status"], "deferred_busy");
    assert_eq!(result["input_authority"], "explicit_local_cli_context_sync");
    assert_eq!(session.capture()?, before, "busy pane received input bytes");
    Ok(())
}

#[test]
fn idle_session_presents_once_and_unchanged_sync_does_not_duplicate() -> Result<()> {
    if !tmux_available() {
        return Ok(());
    }
    let temp = tempfile::tempdir()?;
    let id = unique_session_id();
    let (project, instance) =
        setup_registered_session(&temp, &id, "forager_test_WikiIdle", "codex")?;
    let session = TmuxSessionGuard::start(
        &project,
        &instance,
        "#!/usr/bin/env bash\nprintf 'Ready\\n\\n› Implement {feature}\\n'\nIFS= read -r line\nprintf '\\nMESSAGE_ACCEPTED\\n'\nsleep 60\n",
    )?;
    let first = run(
        temp.path(),
        &[
            "session",
            "context-sync",
            &instance.id,
            "--require-fresh",
            "--json",
        ],
    )?;
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let first_result: serde_json::Value = serde_json::from_slice(&first.stdout)?;
    assert_eq!(first_result["receipt"]["presentation_status"], "presented");
    let projection = first_result["receipt"]["projection_sha256"]
        .as_str()
        .context("projection hash")?;
    let pane_after_first = session.capture()?;
    assert!(pane_after_first.contains("Forager context prepared"));
    let compact_pane = pane_after_first
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>();
    assert!(compact_pane.contains(projection));

    let second = run(
        temp.path(),
        &[
            "session",
            "context-sync",
            &instance.id,
            "--require-fresh",
            "--json",
        ],
    )?;
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    let second_result: serde_json::Value = serde_json::from_slice(&second.stdout)?;
    assert_eq!(
        second_result["receipt"]["presentation_status"],
        "unchanged_no_message"
    );
    let pane_after_second = session.capture()?;
    assert_eq!(
        pane_after_second
            .matches("Forager context prepared")
            .count(),
        1
    );

    let wiki_profile_root = app_dir(temp.path()).join("profiles/demo-wiki");
    fs::create_dir_all(&wiki_profile_root)?;
    let corrupt_source = wiki_profile_root.join("adaptive_wiki_entries.json");
    fs::write(&corrupt_source, b"{not-json")?;
    let strict_refresh = run(
        temp.path(),
        &[
            "session",
            "context-sync",
            &instance.id,
            "--require-fresh",
            "--json",
        ],
    )?;
    assert!(!strict_refresh.status.success());
    let fallback = run(
        temp.path(),
        &["session", "context-sync", &instance.id, "--json"],
    )?;
    assert!(fallback.status.success());
    let fallback: serde_json::Value = serde_json::from_slice(&fallback.stdout)?;
    assert_eq!(fallback["refresh_fallback_used"], true);
    assert_eq!(
        fallback["receipt"]["presentation_status"],
        "unchanged_no_message"
    );
    assert_eq!(session.capture()?, pane_after_second);
    fs::remove_file(corrupt_source)?;

    let status = run(
        temp.path(),
        &["session", "context-status", &instance.id, "--json"],
    )?;
    assert!(status.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(status["brief_status"], "ready");
    assert_eq!(status["presentation_status"], "presented");
    assert_eq!(status["fresh_for_session"], true);
    assert_eq!(status["sync_required"], false);
    assert_eq!(status["read_only"], true);
    assert_eq!(status["writes_performed"], false);

    let sessions_path = app_dir(temp.path()).join("profiles/default/sessions.json");
    let mut changed_tool = instance.clone();
    changed_tool.tool = "claude".to_string();
    fs::write(
        &sessions_path,
        serde_json::to_vec_pretty(&vec![changed_tool])?,
    )?;
    let changed_tool_status = run(
        temp.path(),
        &["session", "context-status", &instance.id, "--json"],
    )?;
    assert!(changed_tool_status.status.success());
    let changed_tool_status: serde_json::Value =
        serde_json::from_slice(&changed_tool_status.stdout)?;
    assert_eq!(changed_tool_status["fresh_for_session"], false);
    assert_eq!(changed_tool_status["sync_required"], true);
    fs::write(
        &sessions_path,
        serde_json::to_vec_pretty(&vec![instance.clone()])?,
    )?;

    let registry_path = temp.path().join(".config/forager/projects.toml");
    fs::write(
        &registry_path,
        "schema = \"forager_project_registry.v1\"\n\n\
         [projects.renamed]\n\
         display_name = \"Renamed\"\n\
         workspace_patterns = [\"demo-project\"]\n\
         wiki_profile = \"renamed-wiki\"\n",
    )?;
    let changed_project_status = run(
        temp.path(),
        &["session", "context-status", &instance.id, "--json"],
    )?;
    assert!(changed_project_status.status.success());
    let changed_project_status: serde_json::Value =
        serde_json::from_slice(&changed_project_status.stdout)?;
    assert_eq!(changed_project_status["fresh_for_session"], false);
    assert_eq!(changed_project_status["sync_required"], true);
    assert_eq!(changed_project_status["project_key"], "renamed");
    fs::write(
        registry_path,
        "schema = \"forager_project_registry.v1\"\n\n\
         [projects.demo]\n\
         display_name = \"Demo\"\n\
         workspace_patterns = [\"demo-project\"]\n\
         wiki_profile = \"demo-wiki\"\n",
    )?;

    drop(session);
    let restarted = TmuxSessionGuard::start(
        &project,
        &instance,
        "#!/usr/bin/env bash\nprintf 'Restarted\\n\\n› Implement {feature}\\n'\nIFS= read -r line\nprintf '\\nMESSAGE_ACCEPTED\\n'\nsleep 60\n",
    )?;
    let after_restart = run(
        temp.path(),
        &["session", "context-status", &instance.id, "--json"],
    )?;
    let after_restart: serde_json::Value = serde_json::from_slice(&after_restart.stdout)?;
    assert_eq!(after_restart["fresh_for_session"], false);
    assert_eq!(after_restart["sync_required"], true);

    let resync = run(
        temp.path(),
        &[
            "session",
            "context-sync",
            &instance.id,
            "--require-fresh",
            "--json",
        ],
    )?;
    assert!(resync.status.success());
    let resync: serde_json::Value = serde_json::from_slice(&resync.stdout)?;
    assert_eq!(resync["receipt"]["presentation_status"], "presented");
    assert!(restarted.capture()?.contains("Forager context prepared"));
    Ok(())
}

#[cfg(unix)]
#[test]
fn go_presents_one_fresh_brief_to_new_codex_and_claude_sessions() -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    if !tmux_available() {
        return Ok(());
    }
    for (tool, prompt) in [("codex", "› Implement {feature}"), ("claude", "❯")] {
        let temp = tempfile::tempdir()?;
        let project = setup_registered_project(&temp, &format!("go-{tool}-project"))?;
        let fake = temp.path().join(format!("fake-{tool}"));
        fs::write(
            &fake,
            format!(
                "#!/usr/bin/env bash\nprintf '%s\\n' '{}'\nIFS= read -r line\nprintf 'MESSAGE_ACCEPTED\\n'\nsleep 60\n",
                prompt
            ),
        )?;
        fs::set_permissions(&fake, fs::Permissions::from_mode(0o755))?;

        let output = run(
            temp.path(),
            &[
                "go",
                fake.to_string_lossy().as_ref(),
                "--path",
                project.to_string_lossy().as_ref(),
                "--no-attach",
            ],
        )?;
        assert!(
            output.status.success(),
            "{tool}: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(
            stdout.contains("Wiki context: presented"),
            "{tool}: {stdout}"
        );
        let sessions: Vec<Instance> = serde_json::from_slice(&fs::read(
            app_dir(temp.path()).join("profiles/default/sessions.json"),
        )?)?;
        let instance = sessions.first().context("go session")?;
        assert_eq!(instance.tool, tool);
        let name = forager::tmux::Session::generate_name(&instance.id, &instance.title);
        let pane = Command::new("tmux")
            .args(["capture-pane", "-p", "-t", &name, "-S", "-80"])
            .output()?;
        let pane = String::from_utf8_lossy(&pane.stdout);
        assert!(pane.contains("Forager context prepared"), "{tool}: {pane}");
        let _ = Command::new("tmux")
            .args(["kill-session", "-t", &name])
            .status();
    }
    Ok(())
}
