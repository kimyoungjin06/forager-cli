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

#[test]
#[serial]
fn ondesk_note_json_redacts_and_persists_note() -> Result<()> {
    let temp = tempdir()?;
    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "note",
            "--project-key",
            "twinpaper",
            "--mode",
            "writing",
            "--text",
            "draft note token=sk-secretsecretsecretsecret",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["project_key"], "twinpaper");
    assert_eq!(json["mode"], "writing");

    let notes = fs::read_to_string(profile_dir(temp.path()).join("ondesk_notes.jsonl"))?;
    assert!(notes.contains("twinpaper"));
    assert!(notes.contains("writing"));
    assert!(notes.contains("[REDACTED]"));
    assert!(!notes.contains("sk-secretsecretsecretsecret"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_uses_recent_redacted_notes() -> Result<()> {
    let temp = tempdir()?;
    let note_output = forager_command(temp.path())
        .args([
            "ondesk",
            "note",
            "--project-key",
            "research",
            "--mode",
            "analysis",
            "--text",
            "compare evidence before claim token=sk-secretsecretsecretsecret",
        ])
        .output()?;
    assert!(
        note_output.status.success(),
        "{}",
        String::from_utf8_lossy(&note_output.stderr)
    );

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "research",
            "--mode",
            "analysis",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["project_key"], "research");
    assert_eq!(json["note_count"], 1);
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("compare evidence before claim"));
    assert!(content.contains("Instructions For The Next Harness"));
    assert!(content.contains("[REDACTED]"));
    assert!(!content.contains("sk-secretsecretsecretsecret"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_capture_writes_artifacts_without_requiring_running_tmux() -> Result<()> {
    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir_all(&project)?;

    let add_output = forager_command(temp.path())
        .args([
            "add",
            project.to_str().expect("utf-8 project path"),
            "--title",
            "codex-harness",
            "--cmd",
            "codex",
        ])
        .output()?;
    assert!(
        add_output.status.success(),
        "{}",
        String::from_utf8_lossy(&add_output.stderr)
    );

    let capture_output = forager_command(temp.path())
        .args([
            "ondesk",
            "capture",
            "codex-harness",
            "--project-key",
            "project",
            "--lines",
            "50",
            "--json",
        ])
        .output()?;
    assert!(
        capture_output.status.success(),
        "{}",
        String::from_utf8_lossy(&capture_output.stderr)
    );
    let json: Value = serde_json::from_slice(&capture_output.stdout)?;
    assert_eq!(json["project_key"], "project");
    assert_eq!(json["session_running"], false);

    let capture_path = PathBuf::from(json["capture_path"].as_str().expect("capture path"));
    let prompt_path = PathBuf::from(
        json["prompt_package_path"]
            .as_str()
            .expect("prompt package path"),
    );
    assert!(capture_path.exists());
    assert!(prompt_path.exists());

    let capture: Value = serde_json::from_str(&fs::read_to_string(capture_path)?)?;
    assert_eq!(capture["session"]["title"], "codex-harness");
    assert_eq!(capture["scrollback"], "");

    let prompt = fs::read_to_string(prompt_path)?;
    assert!(prompt.contains("Captured Harness Scrollback"));
    assert!(prompt.contains("No live tmux scrollback was available"));
    Ok(())
}
