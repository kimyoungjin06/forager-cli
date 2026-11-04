use anyhow::Result;
use serde_json::Value;
use serial_test::serial;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::tempdir;

fn forager_command(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_forager"));
    command.env("HOME", home);
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command.env_remove("FORAGER_DEBUG");
    command.env_remove("AGENT_OF_EMPIRES_DEBUG");
    #[cfg(target_os = "linux")]
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command
}

fn profile_dir(home: &Path) -> PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config")
            .join("forager")
            .join("profiles")
            .join("default")
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".forager").join("profiles").join("default")
    }
}

fn valid_brief(project_key: &str) -> String {
    format!(
        "<!-- generated -->\n# Wiki Brief\n\nSchema: wiki_brief.v1\nProject: {project_key}\nWiki profile: test\nAgent mode: shared\nProjection: sha256:{}\nGenerated: 2026-08-21 UTC\nEntries: 0 (0 stale)\n",
        "a".repeat(64)
    )
}

fn run_json(home: &Path, project: &Path, extra: &[&str]) -> Result<(bool, Value, String)> {
    let mut command = forager_command(home);
    command.args([
        "project",
        "wiki-context",
        project.to_str().expect("utf-8 project path"),
        "--project-key",
        "demo",
        "--json",
    ]);
    command.args(extra);
    let output = command.output()?;
    let value = if output.stdout.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(&output.stdout)?
    };
    Ok((
        output.status.success(),
        value,
        String::from_utf8_lossy(&output.stderr).into_owned(),
    ))
}

#[cfg(unix)]
#[test]
#[serial]
fn wiki_context_preview_apply_and_rerun_are_bounded_and_idempotent() -> Result<()> {
    use std::os::unix::fs::{symlink, MetadataExt};

    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir(&project)?;
    git2::Repository::init(&project)?;
    fs::write(project.join("AGENTS.md"), "Project instructions\n")?;
    symlink("AGENTS.md", project.join("CLAUDE.md"))?;
    fs::write(project.join(".gitignore"), "target/\n")?;
    fs::write(project.join(".wiki-brief.md"), valid_brief("demo"))?;
    let agents_before = fs::read(project.join("AGENTS.md"))?;
    let ignore_before = fs::read(project.join(".gitignore"))?;

    let (success, preview, stderr) = run_json(temp.path(), &project, &["--manage-gitignore"])?;
    assert!(success, "{stderr}");
    assert_eq!(preview["schema"], "wiki_context_onboarding.v1");
    assert_eq!(preview["read_only"], true);
    assert_eq!(preview["writes_project_state"], false);
    assert_eq!(preview["planned_count"], 2);
    assert_eq!(preview["git_ignore_status"], "missing_policy");
    assert_eq!(preview["routing"]["direct_launch_coverage"], "partial");
    assert_eq!(
        preview["routing"]["projected_coverage_after_apply"],
        "supported"
    );
    assert!(preview["operations"][0]["diff"]
        .as_str()
        .expect("AGENTS diff")
        .contains("+<!-- forager:wiki-context:start -->"));
    assert_eq!(fs::read(project.join("AGENTS.md"))?, agents_before);
    assert_eq!(fs::read(project.join(".gitignore"))?, ignore_before);
    assert_eq!(
        fs::read_link(project.join("CLAUDE.md"))?,
        Path::new("AGENTS.md")
    );
    assert!(!profile_dir(temp.path()).exists());

    let (success, applied, stderr) =
        run_json(temp.path(), &project, &["--manage-gitignore", "--reviewed"])?;
    assert!(success, "{stderr}");
    assert_eq!(applied["reviewed"], true);
    assert_eq!(applied["applied_count"], 2);
    assert_eq!(applied["writes_project_state"], true);
    assert_eq!(applied["grants_session_input_authority"], false);
    assert_eq!(applied["grants_mcp_authority"], false);
    assert_eq!(applied["routing"]["direct_launch_coverage"], "supported");
    assert!(!profile_dir(temp.path()).exists());
    let agents_after = fs::read_to_string(project.join("AGENTS.md"))?;
    let ignore_after = fs::read_to_string(project.join(".gitignore"))?;
    assert!(agents_after.starts_with("Project instructions\n\n"));
    assert_eq!(
        agents_after.matches("forager:wiki-context:start").count(),
        1
    );
    assert!(ignore_after.starts_with("target/\n\n"));
    assert_eq!(
        ignore_after.matches("forager:wiki-context:start").count(),
        1
    );
    assert_eq!(
        fs::read_link(project.join("CLAUDE.md"))?,
        Path::new("AGENTS.md")
    );

    let agents_inode = fs::metadata(project.join("AGENTS.md"))?.ino();
    let ignore_inode = fs::metadata(project.join(".gitignore"))?.ino();
    let (success, rerun, stderr) =
        run_json(temp.path(), &project, &["--manage-gitignore", "--reviewed"])?;
    assert!(success, "{stderr}");
    assert_eq!(rerun["applied_count"], 0);
    assert_eq!(rerun["planned_count"], 0);
    assert_eq!(fs::metadata(project.join("AGENTS.md"))?.ino(), agents_inode);
    assert_eq!(
        fs::metadata(project.join(".gitignore"))?.ino(),
        ignore_inode
    );

    let (success, lint, stderr) = run_json(temp.path(), &project, &[])?;
    assert!(success, "{stderr}");
    assert_eq!(lint["git_ignore_status"], "ignored");
    assert_eq!(lint["routing"]["direct_launch_coverage"], "supported");
    Ok(())
}

#[test]
#[serial]
fn wiki_context_claude_regular_file_requires_explicit_opt_in() -> Result<()> {
    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir(&project)?;
    fs::write(
        project.join("AGENTS.md"),
        "Read `.wiki-brief.md` before starting.\n",
    )?;
    fs::write(project.join("CLAUDE.md"), "Claude-specific instructions\n")?;
    fs::write(project.join(".wiki-brief.md"), valid_brief("demo"))?;
    let claude_before = fs::read(project.join("CLAUDE.md"))?;

    let (success, lint, stderr) = run_json(temp.path(), &project, &[])?;
    assert!(success, "{stderr}");
    assert_eq!(lint["planned_count"], 0);
    assert_eq!(lint["routing"]["claude_md"], "not_routed");
    assert_eq!(fs::read(project.join("CLAUDE.md"))?, claude_before);

    let (success, preview, stderr) = run_json(temp.path(), &project, &["--include-claude"])?;
    assert!(success, "{stderr}");
    assert_eq!(preview["planned_count"], 1);
    assert_eq!(fs::read(project.join("CLAUDE.md"))?, claude_before);

    let (success, applied, stderr) =
        run_json(temp.path(), &project, &["--include-claude", "--reviewed"])?;
    assert!(success, "{stderr}");
    assert_eq!(applied["applied_count"], 1);
    assert_eq!(
        fs::read(project.join("AGENTS.md"))?,
        b"Read `.wiki-brief.md` before starting.\n"
    );
    let claude_after = fs::read_to_string(project.join("CLAUDE.md"))?;
    assert!(claude_after.starts_with("Claude-specific instructions\n\n"));
    assert!(claude_after.contains("<!-- forager:wiki-context:start -->"));
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn wiki_context_reviewed_apply_fails_before_writes_for_malformed_or_symlinked_targets() -> Result<()>
{
    use std::os::unix::fs::symlink;

    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir(&project)?;
    let malformed = "Project instructions\n<!-- forager:wiki-context:start -->\nbroken\n";
    fs::write(project.join("AGENTS.md"), malformed)?;
    let outside = temp.path().join("outside.md");
    fs::write(&outside, "outside truth\n")?;
    symlink(&outside, project.join("CLAUDE.md"))?;

    let (success, preview, stderr) = run_json(temp.path(), &project, &["--include-claude"])?;
    assert!(success, "{stderr}");
    assert_eq!(preview["blocked_count"], 2);

    let (success, value, stderr) =
        run_json(temp.path(), &project, &["--include-claude", "--reviewed"])?;
    assert!(!success);
    assert!(value.is_null());
    assert!(stderr.contains("apply is blocked"));
    assert_eq!(fs::read_to_string(project.join("AGENTS.md"))?, malformed);
    assert_eq!(fs::read_to_string(&outside)?, "outside truth\n");
    Ok(())
}

#[test]
#[serial]
fn wiki_context_reports_tracked_brief_without_changing_git_state() -> Result<()> {
    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir(&project)?;
    let repository = git2::Repository::init(&project)?;
    fs::write(
        project.join("AGENTS.md"),
        "Read `.wiki-brief.md` before starting.\n",
    )?;
    fs::write(project.join(".wiki-brief.md"), valid_brief("demo"))?;
    let mut index = repository.index()?;
    index.add_path(Path::new(".wiki-brief.md"))?;
    index.write()?;
    let index_before = fs::read(repository.path().join("index"))?;

    let (success, report, stderr) = run_json(temp.path(), &project, &[])?;
    assert!(success, "{stderr}");
    assert_eq!(report["git_ignore_status"], "tracked");
    assert!(report["warnings"]
        .as_array()
        .expect("warnings")
        .iter()
        .any(|warning| warning
            .as_str()
            .is_some_and(|text| text.contains("Git index"))));
    assert_eq!(fs::read(repository.path().join("index"))?, index_before);
    Ok(())
}
