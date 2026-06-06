use anyhow::Result;
use serde_json::Value;
use serial_test::serial;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::tempdir;

fn script_path(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join(name)
}

fn write_env_file(path: &Path) -> Result<()> {
    fs::write(
        path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n",
    )?;
    Ok(())
}

fn remote_operator_command(home: &Path) -> Command {
    let mut command = Command::new("python3");
    command.arg(script_path("offdesk_remote_operator_telegram.py"));
    command.env("HOME", home);
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command
}

#[test]
#[serial]
fn remote_operator_telegram_dry_run_status_renders_read_only_projection() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_status.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/status")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(
        result["schema"],
        "remote_operator_telegram_adapter_result.v1"
    );
    assert_eq!(result["mode"], "dry_run");
    assert_eq!(result["status"], "rendered");
    assert_eq!(result["read_only"], true);
    assert_eq!(result["mutation_authorized"], false);
    assert_eq!(result["approval_authorized"], false);
    assert_eq!(
        result["projection_schema"],
        "remote_operator_readonly_projection.v1"
    );
    assert_eq!(result["projection"]["command"], "status");
    assert_eq!(result["projection"]["read_only"], true);
    assert_eq!(result["projection"]["mutation_authorized"], false);
    assert_eq!(result["projection"]["approval_authorized"], false);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("Forager 점검"));
    assert!(preview.contains("다음:"));
    assert!(preview.contains("읽기 전용"));
    assert!(!preview.contains("Forager Remote Status"));
    assert!(!preview.contains("Read-only"));
    assert!(result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .starts_with("sha256:"));

    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains("fake-token-for-test"));
    assert!(!serialized.contains("999999:"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_rejects_approval_command_without_projection() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_approve.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/approve approval_one")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert_eq!(output.status.code(), Some(2));
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["status"], "unsupported");
    assert_eq!(result["reason"], "unsupported_remote_operator_command");
    assert_eq!(result["projection"], Value::Null);
    assert_eq!(result["read_only"], true);
    assert_eq!(result["mutation_authorized"], false);
    assert_eq!(result["approval_authorized"], false);
    assert!(result["forbidden_remote_intents"]
        .as_array()
        .expect("forbidden intents")
        .iter()
        .any(|item| item == "approve_launch"));
    assert!(!temp
        .path()
        .join(".config")
        .join("forager")
        .join("profiles")
        .join("default")
        .join("pending_action_approvals.json")
        .exists());
    Ok(())
}
