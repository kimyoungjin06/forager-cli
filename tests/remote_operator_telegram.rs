use anyhow::Result;
use serde_json::{json, Value};
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

fn write_projection_file(path: &Path, command: &str, payload: Value) -> Result<()> {
    let projection = json!({
        "schema": "remote_operator_readonly_projection.v1",
        "generated_at": "2026-06-06T00:00:00Z",
        "forager_profile": "default",
        "transport": "telegram",
        "source_surface": "remote_operator.telegram",
        "command": command,
        "phase": "read_only_surface",
        "read_only": true,
        "mutation_authorized": false,
        "approval_authorized": false,
        "allowed_remote_intents": [
            "inspect_status",
            "inspect_pending",
            "inspect_plans",
            "inspect_plan"
        ],
        "forbidden_remote_intents": [
            "approve_plan",
            "approve_launch",
            "deny_launch",
            "enqueue",
            "launch",
            "dispatch",
            "shell"
        ],
        "card": {
            "title": "Forager Remote Status",
            "summary_lines": [],
            "detail_lines": [],
            "observed_hash": "sha256:0123456789abcdef0123456789abcdef",
            "remote_actions": ["inspect_status"],
            "disabled_remote_actions": ["approve_launch", "dispatch", "shell"]
        },
        "payload": payload
    });
    fs::write(path, serde_json::to_string_pretty(&projection)?)?;
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

fn assert_mobile_contract(result: &Value) {
    let contract = &result["mobile_card_contract"];
    assert_eq!(contract["schema"], "telegram_mobile_card_contract.v1");
    assert!(contract["warnings"]
        .as_array()
        .expect("mobile card warnings")
        .is_empty());
    assert!(contract["line_count"].as_u64().expect("line count") <= 8);
    let choice_contract = &result["choice_surface_contract"];
    assert_eq!(
        choice_contract["schema"],
        "telegram_choice_surface_contract.v1"
    );
    assert!(choice_contract["warnings"]
        .as_array()
        .expect("choice surface warnings")
        .is_empty());
    for label in ["상태", "승인 대기", "계획", "도움말"] {
        assert!(choice_contract["button_texts"]
            .as_array()
            .expect("button texts")
            .iter()
            .any(|item| item == label));
    }
    assert_eq!(choice_contract["has_freeform_placeholder"], true);

    let preview = result["message_preview"].as_str().expect("message preview");
    for forbidden in [
        "Forager Remote Status",
        "Read-only",
        "읽기 전용",
        "검증:",
        "sha256:",
        "dispatch",
        "shell",
        "launch-prep",
        "runtime_handle_alive",
    ] {
        assert!(
            !preview.contains(forbidden),
            "mobile preview leaked forbidden term {forbidden}:\n{preview}"
        );
    }
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
    assert!(preview.contains("Forager 점검 / default"));
    assert!(preview.contains("상태: 정상, 처리할 항목 없음"));
    assert!(preview.contains("다음:"));
    assert!(!preview.contains("Forager Remote Status"));
    assert!(!preview.contains("Read-only"));
    assert_mobile_contract(&result);
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
    assert_mobile_contract(&result);
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

#[test]
#[serial]
fn remote_operator_telegram_korean_buttons_map_to_safe_commands() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_button_alias.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("계획")
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
    assert_eq!(result["status"], "rendered");
    assert_eq!(result["parsed_command"]["command"], "plans");
    assert_eq!(
        result["parsed_command"]["argv"],
        json!(["plans", "--latest"])
    );
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_freeform_feedback_gets_mobile_receipt() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_feedback.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("승인 전 실패 조건을 더 명확히 적어줘")
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
    assert_eq!(result["status"], "rendered");
    assert_eq!(result["parsed_command"]["command"], "feedback");
    assert_eq!(result["projection"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>의견 접수 / default</b>"));
    assert!(preview.contains("상태: 입력 내용을 저장했습니다."));
    assert!(preview.contains("승인 전 실패 조건을 더 명확히 적어줘"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_status_fixture_prioritizes_attention() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let projection_path = temp.path().join("status_attention.json");
    write_projection_file(
        &projection_path,
        "status",
        json!({
            "profile": "default",
            "waiting": 1,
            "running": 1,
            "total": 9,
            "pending_approvals": 2,
            "queued_offdesk_tasks": 3,
            "active_offdesk_tasks": 1,
            "failed_offdesk_tasks": 1,
            "closeout_required_offdesk_tasks": 1
        }),
    )?;
    let out = temp.path().join("status_attention_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/status")
        .arg("--projection-file")
        .arg(&projection_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("상태: 승인 요청 2개 확인 필요"));
    assert!(preview.contains("다음: <code>/pending</code> 으로 승인 요청 확인"));
    assert!(preview.contains("승인 2 · 실패 1 · 마무리 1 · 자율주행 진행 1 / 대기 3"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_pending_fixture_is_mobile_scannable() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let projection_path = temp.path().join("pending_attention.json");
    write_projection_file(
        &projection_path,
        "pending",
        json!({
            "approval_count": 4,
            "approvals": [
                {"approval_id": "approval_one", "action": "approve_plan", "expired": false},
                {"approval_id": "approval_two", "action": "approve_launch", "expired": false},
                {"approval_id": "approval_three", "action": "provider_retarget", "expired": true},
                {"approval_id": "approval_four", "action": "approve_launch", "expired": false}
            ]
        }),
    )?;
    let out = temp.path().join("pending_attention_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/pending --all")
        .arg("--projection-file")
        .arg(&projection_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>승인 대기 / default</b>"));
    assert!(preview.contains("상태: 승인 요청 4개 확인 필요 · 만료 1"));
    assert!(preview.contains("계획 승인"));
    assert!(preview.contains("실행 승인"));
    assert!(preview.contains("- 외 2개"));
    assert!(preview.contains("다음: 로컬에서 승인 판단"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_plans_fixture_has_empty_and_nonempty_next_actions() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let projection_path = temp.path().join("plans_attention.json");
    write_projection_file(
        &projection_path,
        "plans",
        json!({
            "plan_count": 1,
            "plans": [
                {"plan_id": "plan_harness_mobile", "review_status": "revision_required"}
            ]
        }),
    )?;
    let out = temp.path().join("plans_attention_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/plans --latest")
        .arg("--projection-file")
        .arg(&projection_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>자율주행 계획 / default</b>"));
    assert!(preview.contains("plan_harness_mobile: 수정 필요"));
    assert!(preview.contains("다음: <code>/show PLAN_ID</code> 로 세부 확인"));
    assert_mobile_contract(&result);
    Ok(())
}
