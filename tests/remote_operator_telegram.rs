use anyhow::Result;
use serde_json::{json, Map, Value};
use serial_test::serial;
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};
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
    command.env("OFFDESK_REMOTE_OPERATOR_AGENT_INTENT_MODE", "off");
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
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

fn find_header_end(data: &[u8]) -> Option<usize> {
    data.windows(4).position(|window| window == b"\r\n\r\n")
}

fn parse_content_length(headers: &str) -> usize {
    headers
        .lines()
        .find_map(|line| {
            let (key, value) = line.split_once(':')?;
            if key.trim().eq_ignore_ascii_case("content-length") {
                value.trim().parse::<usize>().ok()
            } else {
                None
            }
        })
        .unwrap_or(0)
}

fn read_http_request(stream: &mut TcpStream) -> Result<(String, Vec<u8>)> {
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    let mut data = Vec::new();
    let mut buffer = [0_u8; 4096];
    loop {
        let read = stream.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        data.extend_from_slice(&buffer[..read]);
        if let Some(header_end) = find_header_end(&data) {
            let headers = String::from_utf8_lossy(&data[..header_end]).to_string();
            let body_start = header_end + 4;
            let content_length = parse_content_length(&headers);
            if data.len() >= body_start + content_length {
                let line = headers.lines().next().unwrap_or_default().to_string();
                let body = data[body_start..body_start + content_length].to_vec();
                return Ok((line, body));
            }
        }
    }
    anyhow::bail!("incomplete HTTP request")
}

fn write_http_json(stream: &mut TcpStream, value: Value) -> Result<()> {
    let body = serde_json::to_vec(&value)?;
    write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        body.len()
    )?;
    stream.write_all(&body)?;
    Ok(())
}

fn spawn_fake_ollama(body_path: PathBuf) -> Result<(String, thread::JoinHandle<Result<()>>)> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    listener.set_nonblocking(true)?;
    let handle = thread::spawn(move || -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut handled = 0;
        while handled < 2 {
            let (mut stream, _) = match listener.accept() {
                Ok(value) => value,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() > deadline {
                        anyhow::bail!("fake Ollama timed out waiting for requests");
                    }
                    thread::sleep(Duration::from_millis(10));
                    continue;
                }
                Err(error) => return Err(error.into()),
            };
            let (request_line, body) = read_http_request(&mut stream)?;
            if request_line.starts_with("GET /api/tags ") {
                write_http_json(
                    &mut stream,
                    json!({"models": [{"name": "qwen3-coder-next:latest"}]}),
                )?;
            } else if request_line.starts_with("POST /api/generate ") {
                fs::write(&body_path, &body)?;
                let classified = json!({
                    "intent": "plan_request",
                    "feedback_kind": "planning_request",
                    "confidence": 0.91,
                    "project_hint": "NanoClustering",
                    "goal": "Assess Fractal tree work",
                    "timebox": "tomorrow night",
                    "requires_clarification": false,
                    "clarifying_question": null,
                    "reason": "The operator is asking whether this work should become a plan candidate.",
                    "non_authorized": ["execution", "approval", "shell"]
                });
                write_http_json(
                    &mut stream,
                    json!({"response": serde_json::to_string(&classified)?}),
                )?;
            } else {
                write_http_json(&mut stream, json!({"error": request_line}))?;
            }
            handled += 1;
        }
        Ok(())
    });
    Ok((format!("http://127.0.0.1:{port}"), handle))
}

fn assert_mobile_contract(result: &Value) {
    let contract = &result["mobile_card_contract"];
    assert_eq!(contract["schema"], "telegram_mobile_card_contract.v1");
    assert!(contract["warnings"]
        .as_array()
        .expect("mobile card warnings")
        .is_empty());
    assert!(
        contract["line_count"].as_u64().expect("line count")
            <= contract["max_lines"].as_u64().expect("max lines")
    );
    assert!(
        contract["char_count"].as_u64().expect("char count")
            <= contract["max_chars"].as_u64().expect("max chars")
    );
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
        "상태:",
        "다음:",
        "맥락:",
        "기준 ",
        "검증:",
        "sha256:",
        "receipt",
        "preview",
        "project init",
        "plan evidence",
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

fn button_texts(result: &Value) -> Vec<String> {
    result["choice_surface_contract"]["button_texts"]
        .as_array()
        .expect("button texts")
        .iter()
        .map(|item| item.as_str().unwrap_or_default().to_string())
        .collect()
}

fn write_text_update(path: &Path, update_id: i64, message_id: i64, text: &str) -> Result<()> {
    fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": 1780000000,
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                "text": text
            }
        }))?,
    )?;
    Ok(())
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
    assert!(preview.contains("<b>Forager 점검</b>"));
    assert!(preview.contains("처리할 항목이 없습니다."));
    assert!(preview.contains("새 알림이 오면 다시 확인하세요."));
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
fn remote_operator_telegram_status_includes_adapter_readiness_when_loop_status_exists() -> Result<()>
{
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "poll_count": 3,
            "updates_seen": 0,
            "handled_result_count": 0,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    let out = temp.path().join("remote_status_with_adapter.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/status")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
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
    assert_eq!(result["adapter_health"]["health_status"], "healthy");
    assert_eq!(
        result["adapter_health"]["action_readiness"][2]["action"],
        "build_plan"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 점검</b>"));
    assert!(preview.contains("원격 정상 · 계획 준비 가능"));
    assert!(preview.contains("새 알림이 오면 다시 확인하세요."));
    assert_mobile_contract(&result);
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
    assert!(preview.contains("<b>의견 접수</b>"));
    assert!(preview.contains("의견을 저장했습니다."));
    assert!(!preview.contains("승인 전 실패 조건을 더 명확히 적어줘"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_planning_request_makes_non_execution_receipt_explicit() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_plan_request.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("nanoclustering Fractal tree 개발쪽을 자율주행으로 처리할 수 있을지 검토해볼까")
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
    assert_eq!(
        result["parsed_command"]["feedback_kind"],
        "planning_request"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 요청 접수</b>"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(preview.contains("로컬에서 계획으로 바꾸세요."));
    assert!(!preview.contains("Fractal tree"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_classifies_korean_night_run_as_planning_request() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("night_run_request.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("TwinPaper쪽에서 야간주행을 하고 싶어")
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
    assert_eq!(result["parsed_command"]["command"], "feedback");
    assert_eq!(
        result["parsed_command"]["feedback_kind"],
        "planning_request"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 요청 접수</b>"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_agent_classifies_freeform_plan_request() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_agent_plan_request.json");
    let agent_request_path = temp.path().join("ollama_generate_request.json");
    let (base_url, server) = spawn_fake_ollama(agent_request_path.clone())?;
    let telegram_text = "Please assess NanoClustering Fractal tree work for tomorrow night";

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg(telegram_text)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["status"], "rendered");
    assert_eq!(result["parsed_command"]["command"], "feedback");
    assert_eq!(
        result["parsed_command"]["feedback_kind"],
        "planning_request"
    );
    assert_eq!(result["parsed_command"]["agent_intent"]["source"], "ollama");
    assert_eq!(
        result["parsed_command"]["agent_intent"]["model"],
        "qwen3-coder-next:latest"
    );
    assert_eq!(
        result["parsed_command"]["agent_intent"]["intent"],
        "plan_request"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 요청 접수</b>"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(!preview.contains("NanoClustering"));
    assert_mobile_contract(&result);

    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    assert_eq!(agent_request["model"], "qwen3-coder-next:latest");
    assert!(agent_request["prompt"]
        .as_str()
        .expect("agent prompt")
        .contains(telegram_text));
    assert_eq!(agent_request["stream"], false);
    assert_eq!(agent_request["think"], false);
    assert_eq!(agent_request["format"], "json");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_agent_uses_generic_provider_config() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp
        .path()
        .join("remote_generic_provider_plan_request.json");
    let agent_request_path = temp.path().join("ollama_generic_provider_request.json");
    let (base_url, server) = spawn_fake_ollama(agent_request_path.clone())?;
    let config_path = temp.path().join("forager_config.toml");
    fs::write(
        &config_path,
        format!(
            r#"
[llm.provider]
provider = "ollama"
base_urls = ["{base_url}"]
models = ["qwen3-coder-next:latest"]
timeout_sec = 20
num_ctx = 4096
num_predict = 512
"#
        ),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("Please assess generic product telemetry cleanup for tonight")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-config-file")
        .arg(&config_path)
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(
        result["parsed_command"]["agent_intent"]["base_url"],
        base_url
    );
    assert_eq!(
        result["parsed_command"]["agent_intent"]["config_sources"],
        json!(["llm.provider"])
    );
    assert_eq!(
        result["parsed_command"]["feedback_kind"],
        "planning_request"
    );
    assert_mobile_contract(&result);
    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    assert_eq!(agent_request["options"]["num_ctx"], 4096);
    assert_eq!(agent_request["options"]["num_predict"], 512);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_feedback_uses_last_card_context() -> Result<()> {
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
    let first_out = temp.path().join("plans_result.json");
    let state_path = temp.path().join("telegram_state.json");

    let first_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--send-command-text")
        .arg("/plans --latest")
        .arg("--projection-file")
        .arg(&projection_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&first_out)
        .output()?;

    assert!(
        first_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first_output.stdout),
        String::from_utf8_lossy(&first_output.stderr)
    );
    let first: Value = serde_json::from_slice(&fs::read(&first_out)?)?;
    let chat_hash = first["target_chat_id_hash"]
        .as_str()
        .expect("target chat hash");
    let mut contexts = Map::new();
    contexts.insert(chat_hash.to_string(), first["interaction_context"].clone());
    let state = json!({
        "schema": "remote_operator_telegram_state.v1",
        "offset": 0,
        "last_interaction_context_by_chat": Value::Object(contexts)
    });
    fs::write(&state_path, serde_json::to_string_pretty(&state)?)?;

    let feedback_out = temp.path().join("feedback_result.json");
    let feedback_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("실패 조건 보강 필요")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&feedback_out)
        .output()?;

    assert!(
        feedback_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&feedback_output.stdout),
        String::from_utf8_lossy(&feedback_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&feedback_out)?)?;
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("관련: 계획 plan_harness_mobile · 수정 필요"));
    assert!(preview.contains("로컬에서 검토합니다."));
    assert!(!preview.contains("남긴 말: 실패 조건 보강 필요"));
    assert_eq!(result["feedback_context"]["context_kind"], "plan_attention");
    assert_eq!(
        result["feedback_context"]["focus_ref"],
        "plan_harness_mobile"
    );
    assert!(button_texts(&result).contains(&"/show plan_harness_mobile".to_string()));
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        true
    );
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_feedback_records_decision_inbox_item() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("feedback_update.json");
    fs::write(
        &update_path,
        serde_json::to_string_pretty(&json!({
            "update_id": 500,
            "message": {
                "message_id": 777,
                "date": 1780000000,
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                "text": "모바일 메시지에서 핵심만 남겨줘"
            }
        }))?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let out = temp.path().join("replay_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
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
    assert_eq!(result["mode"], "live_once");
    assert_eq!(result["feedback_recorded"], true);
    assert_eq!(result["decision_feedback_ingest_status"], "recorded");
    assert_eq!(result["decision_feedback_appended"], true);
    assert!(result["decision_feedback_decision_id"]
        .as_str()
        .expect("decision id")
        .starts_with("telegram-feedback-"));
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("의견을 검토 목록에 넣었습니다."));
    assert!(!preview.contains("모바일 메시지에서 핵심만 남겨줘"));
    assert_mobile_contract(&result);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    assert!(feedback_rows.contains("remote_operator_telegram_feedback.v1"));

    let ledger_path = temp
        .path()
        .join(".config")
        .join("forager")
        .join("profiles")
        .join("default")
        .join("offdesk_decisions.jsonl");
    let ledger = fs::read_to_string(&ledger_path)?;
    assert_eq!(ledger.lines().count(), 1);
    let decision: Value = serde_json::from_str(ledger.lines().next().expect("ledger row"))?;
    assert_eq!(
        decision["decision_id"],
        result["decision_feedback_decision_id"]
    );
    assert_eq!(decision["status"], "user_pending");
    assert_eq!(
        decision["decision_request"]["kind"],
        "telegram_operator_feedback"
    );
    assert_eq!(
        decision["source_surface"],
        "telegram.remote_operator.feedback"
    );

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 501);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_request_creates_project_selection_session() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let workspace_root = temp.path().join("workspace");
    fs::create_dir_all(workspace_root.join("Alpha"))?;
    fs::create_dir_all(workspace_root.join("Beta"))?;
    fs::write(
        workspace_root.join("Alpha").join("README.md"),
        "Alpha project\n",
    )?;
    fs::write(
        workspace_root.join("Alpha").join("Cargo.toml"),
        "[package]\nname = \"alpha\"\n",
    )?;
    fs::write(
        workspace_root.join("Beta").join("README.md"),
        "Beta project\n",
    )?;
    fs::write(
        workspace_root.join("Beta").join("pyproject.toml"),
        "[project]\nname = \"beta\"\n",
    )?;
    let update_path = temp.path().join("plan_update.json");
    write_text_update(
        &update_path,
        700,
        880,
        "Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let out = temp.path().join("plan_replay_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
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
    assert_eq!(
        result["parsed_command"]["feedback_kind"],
        "planning_request"
    );
    assert_eq!(
        result["remote_plan_session"]["schema"],
        "telegram_remote_plan_session.v1"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "project_selection");
    assert_eq!(result["remote_plan_session"]["execution_authorized"], false);
    assert_eq!(
        result["remote_plan_session"]["candidates"][0]["display_name"],
        "Alpha"
    );
    assert_eq!(
        result["interaction_context"]["context_kind"],
        "remote_plan_project_selection"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택</b>"));
    assert!(preview.contains("직접 입력"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"1 Alpha".to_string()));
    assert!(button_texts(&result).contains(&"다시 스캔".to_string()));
    assert!(button_texts(&result).contains(&"보류".to_string()));
    assert_mobile_contract(&result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 701);
    assert_eq!(
        state["remote_plan_sessions_by_chat"][result["target_chat_id_hash"].as_str().unwrap()]
            ["stage"],
        "project_selection"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_accepts_direct_project_input() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let workspace_root = temp.path().join("workspace");
    fs::create_dir_all(workspace_root.join("Alpha"))?;
    fs::create_dir_all(workspace_root.join("Beta"))?;
    fs::write(
        workspace_root.join("Alpha").join("README.md"),
        "Alpha project\n",
    )?;
    fs::write(
        workspace_root.join("Alpha").join("Cargo.toml"),
        "[package]\nname = \"alpha\"\n",
    )?;
    fs::write(
        workspace_root.join("Beta").join("README.md"),
        "Beta project\n",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let first_update = temp.path().join("plan_update.json");
    write_text_update(
        &first_update,
        710,
        890,
        "Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_replay_result.json");

    let first_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&first_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&first_out)
        .output()?;
    assert!(
        first_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first_output.stdout),
        String::from_utf8_lossy(&first_output.stderr)
    );

    let second_update = temp.path().join("selection_update.json");
    write_text_update(&second_update, 711, 891, "1번")?;
    let second_out = temp.path().join("selection_result.json");
    let second_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&second_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&second_out)
        .output()?;

    assert!(
        second_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second_output.stdout),
        String::from_utf8_lossy(&second_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(result["status"], "rendered");
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(result["parsed_command"]["selection_status"], "selected");
    assert_eq!(result["remote_plan_session"]["stage"], "project_selected");
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["display_name"],
        "Alpha"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택됨</b>"));
    assert!(preview.contains("초기화 검토"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"초기화 검토".to_string()));
    assert!(button_texts(&result).contains(&"다시 선택".to_string()));
    assert_mobile_contract(&result);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 712);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_searches_workspace_before_manual_fallback(
) -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let workspace_root = temp.path().join("workspace");
    for name in ["Alpha", "Beta", "Gamma", "TwinPaper"] {
        fs::create_dir_all(workspace_root.join(name))?;
        fs::write(
            workspace_root.join(name).join("README.md"),
            format!("{name} project\n"),
        )?;
    }
    fs::write(
        workspace_root.join("Alpha").join("Cargo.toml"),
        "[package]\nname = \"alpha\"\n",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let first_update = temp.path().join("plan_update.json");
    write_text_update(
        &first_update,
        713,
        892,
        "Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_replay_result.json");
    let first_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&first_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&first_out)
        .output()?;
    assert!(
        first_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first_output.stdout),
        String::from_utf8_lossy(&first_output.stderr)
    );
    let first_result: Value = serde_json::from_slice(&fs::read(&first_out)?)?;
    let first_buttons = button_texts(&first_result);
    assert!(!first_buttons
        .iter()
        .any(|button| button.contains("TwinPaper")));

    let second_update = temp.path().join("workspace_search_selection_update.json");
    write_text_update(&second_update, 714, 893, "TwinPaper")?;
    let second_out = temp.path().join("workspace_search_selection_result.json");
    let second_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&second_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&second_out)
        .output()?;

    assert!(
        second_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second_output.stdout),
        String::from_utf8_lossy(&second_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "selected_by_search"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "project_selected");
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["display_name"],
        "TwinPaper"
    );
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["resolved_by"],
        "workspace_search"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택됨</b>"));
    assert!(preview.contains("TwinPaper"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_builds_init_preview_receipt() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let workspace_root = temp.path().join("workspace");
    fs::create_dir_all(workspace_root.join("Alpha"))?;
    fs::write(
        workspace_root.join("Alpha").join("README.md"),
        "Alpha project\n",
    )?;
    fs::write(
        workspace_root.join("Alpha").join("Cargo.toml"),
        "[package]\nname = \"alpha\"\n",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let plan_artifact_dir = temp.path().join("plan_artifacts");
    let first_update = temp.path().join("plan_update.json");
    write_text_update(
        &first_update,
        730,
        910,
        "Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_replay_result.json");
    let first_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&first_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&first_out)
        .output()?;
    assert!(
        first_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first_output.stdout),
        String::from_utf8_lossy(&first_output.stderr)
    );

    let second_update = temp.path().join("selection_update.json");
    write_text_update(&second_update, 731, 911, "1번")?;
    let second_out = temp.path().join("selection_result.json");
    let second_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&second_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&second_out)
        .output()?;
    assert!(
        second_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second_output.stdout),
        String::from_utf8_lossy(&second_output.stderr)
    );

    let third_update = temp.path().join("init_preview_update.json");
    write_text_update(&third_update, 732, 912, "초기화 검토")?;
    let third_out = temp.path().join("init_preview_result.json");
    let third_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&third_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&third_out)
        .output()?;
    assert!(
        third_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&third_output.stdout),
        String::from_utf8_lossy(&third_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&third_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "init_previewed"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "project_init_previewed"
    );
    assert_eq!(
        result["remote_plan_session"]["project_init_preview"]["schema"],
        "telegram_remote_project_init_preview.v1"
    );
    assert!(
        result["remote_plan_session"]["project_init_preview"]["recommended_next_command"]
            .as_array()
            .expect("command preview")
            .iter()
            .any(|item| item == "<workspace_path>")
    );
    let artifact_path = result["remote_plan_session"]["project_init_preview"]["artifact_path"]
        .as_str()
        .expect("artifact path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_project_init_preview.v1"
    );
    assert_eq!(artifact["display_name"], "Alpha");
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert!(artifact["root_markers"]
        .as_array()
        .expect("markers")
        .iter()
        .any(|item| item == "README.md"));
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>초기화 검토 준비</b>"));
    assert!(preview.contains("초기화 검토 기록을 저장했습니다."));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"초기화 생성".to_string()));
    assert_mobile_contract(&result);

    let fourth_update = temp.path().join("init_create_update.json");
    write_text_update(&fourth_update, 733, 913, "초기화 생성")?;
    let fourth_out = temp.path().join("init_create_result.json");
    let fourth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&fourth_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&fourth_out)
        .output()?;
    assert!(
        fourth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&fourth_output.stdout),
        String::from_utf8_lossy(&fourth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&fourth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(result["parsed_command"]["selection_status"], "init_created");
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "project_init_created"
    );
    assert_eq!(
        result["remote_plan_session"]["project_init_run"]["schema"],
        "telegram_remote_project_init_run.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["project_init_run"]["status"],
        "created"
    );
    let public_command = result["remote_plan_session"]["project_init_run"]["command"]
        .as_array()
        .expect("public command");
    assert!(public_command.iter().any(|item| item == "<workspace_path>"));
    assert!(!public_command.iter().any(|item| {
        item.as_str()
            .unwrap_or_default()
            .contains(workspace_root.to_str().expect("workspace path"))
    }));
    assert!(
        result["remote_plan_session"]["project_init_run"]["project_init_output"]["project_root"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["project_init_run"]["project_init_output"]["artifact_dir"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["project_init_run"]["project_init_output"]
            ["project_root_hash"]
            .is_string()
    );
    assert!(
        result["remote_plan_session"]["project_init_run"]["project_init_output"]
            ["artifact_dir_hash"]
            .is_string()
    );
    let artifact_path = result["remote_plan_session"]["project_init_run"]["artifact_path"]
        .as_str()
        .expect("run artifact path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_project_init_run.v1");
    assert_eq!(artifact["status"], "created");
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(
        artifact["project_init_output"]["kind"],
        "forager_project_initialization"
    );
    assert_eq!(
        artifact["project_init_output"]["read_only_project_state"],
        true
    );
    assert_eq!(
        artifact["project_init_output"]["requires_operator_review"],
        true
    );
    let package_path = artifact["project_init_output"]["artifacts"]
        ["ondesk_start_package_markdown"]
        .as_str()
        .expect("ondesk package path");
    assert!(Path::new(package_path).exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>초기화 패킷 생성됨</b>"));
    assert!(preview.contains("초기화 패킷을 저장했습니다."));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"계획 초안 생성".to_string()));
    assert_mobile_contract(&result);

    let fifth_update = temp.path().join("plan_draft_update.json");
    write_text_update(&fifth_update, 734, 914, "계획 초안 생성")?;
    let fifth_out = temp.path().join("plan_draft_result.json");
    let fifth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&fifth_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&fifth_out)
        .output()?;
    assert!(
        fifth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&fifth_output.stdout),
        String::from_utf8_lossy(&fifth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&fifth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_draft_validated"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_draft_validated"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["schema"],
        "telegram_remote_plan_draft.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["status"],
        "validated"
    );
    assert_eq!(result["remote_plan_session"]["plan_draft"]["dry_run"], true);
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]["schema"],
        "offdesk_plan_registration.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]["dry_run"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]
            ["ready_for_operator_review"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]
            ["ready_for_launch_preparation"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]["ready_for_enqueue"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]["source_path"].is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_draft"]["validation_output"]["source_path_hash"]
            .is_string()
    );
    assert!(result["remote_plan_session"]["plan_draft"]["plan_artifact_path"].is_null());
    assert!(result["remote_plan_session"]["plan_draft"]["plan_artifact_path_hash"].is_string());
    let public_command = result["remote_plan_session"]["plan_draft"]["validation_command"]
        .as_array()
        .expect("plan draft command");
    assert!(public_command
        .iter()
        .any(|item| item == "<plan_draft_path>"));
    assert!(!public_command.iter().any(|item| {
        item.as_str()
            .unwrap_or_default()
            .contains(workspace_root.to_str().expect("workspace path"))
    }));
    let artifact_path = result["remote_plan_session"]["plan_draft"]["artifact_path"]
        .as_str()
        .expect("plan draft receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_draft.v1");
    assert_eq!(artifact["status"], "validated");
    assert_eq!(artifact["dry_run"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(
        artifact["validation_output"]["schema"],
        "offdesk_plan_registration.v1"
    );
    assert_eq!(artifact["validation_output"]["dry_run"], true);
    assert!(artifact["validation_output"]["artifacts"]["registry_dir"].is_null());
    let plan_path = artifact["plan_artifact_path"]
        .as_str()
        .expect("plan draft path");
    let plan: Value = serde_json::from_slice(&fs::read(plan_path)?)?;
    assert_eq!(plan["schema"], "offdesk_multiturn_plan.v1");
    assert_eq!(plan["decision"]["ready_for_operator_review"], true);
    assert_eq!(plan["decision"]["ready_for_launch_preparation"], false);
    assert_eq!(plan["decision"]["ready_for_enqueue"], false);
    assert!(
        plan["execution_sequence"]
            .as_array()
            .expect("execution sequence")
            .len()
            >= 2
    );
    assert!(plan["authority"]["does_not_authorize"]
        .as_array()
        .expect("denials")
        .iter()
        .any(|item| item == "launch"));
    assert!(!profile_dir(temp.path()).join("offdesk_plans").exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 초안 검증됨</b>"));
    assert!(preview.contains("계획 초안을 저장했습니다."));
    assert!(preview.contains("계획 등록/실행은 아직 하지 않았습니다."));
    assert!(preview.contains("아래 버튼으로 계획 등록"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"계획 등록".to_string()));
    assert_mobile_contract(&result);

    let sixth_update = temp.path().join("plan_register_update.json");
    write_text_update(&sixth_update, 735, 915, "계획 등록")?;
    let sixth_out = temp.path().join("plan_register_result.json");
    let sixth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&sixth_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&sixth_out)
        .output()?;
    assert!(
        sixth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&sixth_output.stdout),
        String::from_utf8_lossy(&sixth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&sixth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_registered"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "plan_registered");
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["schema"],
        "telegram_remote_plan_registration.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["status"],
        "registered"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["runtime_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["approval_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_registration"]["plan_artifact_path"].is_null());
    assert!(
        result["remote_plan_session"]["plan_registration"]["plan_artifact_path_hash"].is_string()
    );
    let public_command = result["remote_plan_session"]["plan_registration"]["registration_command"]
        .as_array()
        .expect("registration command");
    assert!(public_command
        .iter()
        .any(|item| item == "<plan_draft_path>"));
    assert!(!public_command.iter().any(|item| {
        item.as_str()
            .unwrap_or_default()
            .contains(workspace_root.to_str().expect("workspace path"))
    }));
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]["schema"],
        "offdesk_plan_registration.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]["dry_run"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]["source_path"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]
            ["source_path_hash"]
            .is_string()
    );
    assert!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]["artifacts"]
            ["registration_json"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert!(
        result["remote_plan_session"]["plan_registration"]["registration_output"]["artifacts"]
            ["copied_source_json"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let artifact_path = result["remote_plan_session"]["plan_registration"]["artifact_path"]
        .as_str()
        .expect("registration receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_registration.v1");
    assert_eq!(artifact["status"], "registered");
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(artifact["approval_authorized"], false);
    assert_eq!(
        artifact["registration_output"]["schema"],
        "offdesk_plan_registration.v1"
    );
    assert_eq!(artifact["registration_output"]["dry_run"], false);
    assert_eq!(
        artifact["registration_output"]["ready_for_operator_review"],
        true
    );
    assert_eq!(
        artifact["registration_output"]["ready_for_launch_preparation"],
        false
    );
    assert_eq!(artifact["registration_output"]["ready_for_enqueue"], false);
    let registration_path = artifact["registration_output"]["artifacts"]["registration_json"]
        .as_str()
        .expect("registration json path");
    let copied_source_path = artifact["registration_output"]["artifacts"]["copied_source_json"]
        .as_str()
        .expect("copied source path");
    assert!(Path::new(registration_path).exists());
    assert!(Path::new(copied_source_path).exists());
    assert!(profile_dir(temp.path()).join("offdesk_plans").exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 등록됨</b>"));
    assert!(preview.contains("계획을 등록했습니다."));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(preview.contains("로컬에서 계획 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"계획 승인".to_string()));
    assert_mobile_contract(&result);

    let seventh_update = temp.path().join("plan_review_update.json");
    write_text_update(&seventh_update, 736, 916, "계획 승인")?;
    let seventh_out = temp.path().join("plan_review_result.json");
    let seventh_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&seventh_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--remote-plan-artifact-dir")
        .arg(&plan_artifact_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&seventh_out)
        .output()?;
    assert!(
        seventh_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&seventh_output.stdout),
        String::from_utf8_lossy(&seventh_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&seventh_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_review_approved"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_review_approved"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["schema"],
        "telegram_remote_plan_review.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["status"],
        "approved"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["plan_review_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["launch_preparation_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["runtime_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["review_output"]["schema"],
        "offdesk_plan_review.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["review_output"]["decision"],
        "approved"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["review_output"]
            ["ready_for_launch_preparation_candidate"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["review_output"]["ready_for_enqueue"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["review_output"]["applies_file_operations"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_review"]["review_output"]["registration_path"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_review"]["review_output"]["registration_path_hash"]
            .is_string()
    );
    assert!(
        result["remote_plan_session"]["plan_review"]["review_output"]["artifacts"]
            ["review_record_json"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let artifact_path = result["remote_plan_session"]["plan_review"]["artifact_path"]
        .as_str()
        .expect("plan review receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_review.v1");
    assert_eq!(artifact["status"], "approved");
    assert_eq!(artifact["plan_review_authorized"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_preparation_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(
        artifact["review_output"]["schema"],
        "offdesk_plan_review.v1"
    );
    assert_eq!(artifact["review_output"]["decision"], "approved");
    assert_eq!(
        artifact["review_output"]["ready_for_launch_preparation_candidate"],
        true
    );
    assert_eq!(artifact["review_output"]["ready_for_enqueue"], false);
    assert_eq!(artifact["review_output"]["applies_file_operations"], false);
    let review_record_path = artifact["review_output"]["artifacts"]["review_record_json"]
        .as_str()
        .expect("review record path");
    assert!(Path::new(review_record_path).exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 승인됨</b>"));
    assert!(preview.contains("계획 검토를 기록했습니다."));
    assert!(preview.contains("실행 준비는 아직 하지 않았습니다."));
    assert!(preview.contains("로컬에서 실행 준비 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert_mobile_contract(&result);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 737);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_accepts_manual_project_input() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let workspace_root = temp.path().join("workspace");
    fs::create_dir_all(workspace_root.join("Alpha"))?;
    fs::write(
        workspace_root.join("Alpha").join("README.md"),
        "Alpha project\n",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let first_update = temp.path().join("plan_update.json");
    write_text_update(
        &first_update,
        720,
        900,
        "Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_replay_result.json");
    let first_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&first_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&first_out)
        .output()?;
    assert!(
        first_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first_output.stdout),
        String::from_utf8_lossy(&first_output.stderr)
    );

    let second_update = temp.path().join("manual_selection_update.json");
    write_text_update(&second_update, 721, 901, "Gamma 프로젝트")?;
    let second_out = temp.path().join("manual_selection_result.json");
    let second_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&second_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&second_out)
        .output()?;
    assert!(
        second_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second_output.stdout),
        String::from_utf8_lossy(&second_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(result["parsed_command"]["selection_status"], "manual_input");
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "project_manual_input"
    );
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["manual_input"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["display_name"],
        "Gamma 프로젝트"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택됨</b>"));
    assert!(preview.contains("Gamma 프로젝트"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert_mobile_contract(&result);

    let third_update = temp.path().join("manual_init_update.json");
    write_text_update(&third_update, 722, 902, "초기화 검토")?;
    let third_out = temp.path().join("manual_init_result.json");
    let third_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&third_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&third_out)
        .output()?;
    assert!(
        third_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&third_output.stdout),
        String::from_utf8_lossy(&third_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&third_out)?)?;
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "path_required"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "project_path_required"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>경로 확인 필요</b>"));
    assert!(preview.contains("프로젝트 경로를 직접 입력하세요."));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert_mobile_contract(&result);

    let fourth_update = temp.path().join("manual_path_update.json");
    write_text_update(
        &fourth_update,
        723,
        903,
        workspace_root
            .join("Alpha")
            .to_str()
            .expect("workspace path"),
    )?;
    let fourth_out = temp.path().join("manual_path_result.json");
    let fourth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&fourth_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--feedback-file")
        .arg(&feedback_file)
        .arg("--feedback-ingest-dir")
        .arg(&ingest_dir)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&fourth_out)
        .output()?;
    assert!(
        fourth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&fourth_output.stdout),
        String::from_utf8_lossy(&fourth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&fourth_out)?)?;
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "path_confirmed"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "project_selected");
    assert_eq!(
        result["remote_plan_session"]["selected_candidate"]["display_name"],
        "Alpha"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택됨</b>"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_poll_loop_handles_updates_without_once() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("status_update.json");
    fs::write(
        &update_path,
        serde_json::to_string_pretty(&json!({
            "update_id": 600,
            "message": {
                "message_id": 778,
                "date": 1780000001,
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                "text": "/status"
            }
        }))?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("poll_loop_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--max-polls")
        .arg("2")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
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
    assert_eq!(result["mode"], "live_loop");
    assert_eq!(result["status"], "max_polls_reached");
    assert_eq!(result["poll_count"], 2);
    assert_eq!(result["updates_seen"], 1);
    assert_eq!(result["handled_result_count"], 1);
    assert_eq!(result["last_handled_result"]["status"], "rendered");
    assert_eq!(
        result["last_handled_result"]["parsed_command"]["command"],
        "status"
    );
    assert_mobile_contract(&result["last_handled_result"]);
    assert_eq!(result["last_result"]["status"], "no_update");

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 601);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_reports_fresh_listener_status() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "poll_count": 7,
            "updates_seen": 2,
            "handled_result_count": 1,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            },
            "last_handled_result": {
                "status": "rendered"
            }
        }))?,
    )?;
    let out = temp.path().join("health.json");

    let output = remote_operator_command(temp.path())
        .arg("--health")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--health-max-age-sec")
        .arg("999999999")
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
    assert_eq!(result["schema"], "remote_operator_telegram_health.v1");
    assert_eq!(result["health_status"], "healthy");
    assert_eq!(result["listener_status"], "polling");
    assert_eq!(result["poll_count"], 7);
    assert_eq!(result["handled_result_count"], 1);
    assert_eq!(result["agent_runtime_status"]["status"], "disabled");
    assert_eq!(result["transport_issues"], json!([]));
    assert_eq!(result["action_readiness"][0]["action"], "status");
    assert_eq!(result["action_readiness"][0]["status"], "healthy");
    assert_eq!(result["action_readiness"][2]["action"], "build_plan");
    assert_eq!(result["action_readiness"][2]["status"], "healthy");
    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains("fake-token-for-test"));
    assert!(!serialized.contains("999999:"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_degrades_when_agent_runtime_unavailable() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "poll_count": 7,
            "updates_seen": 2,
            "handled_result_count": 1,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            },
            "last_handled_result": {
                "status": "rendered"
            }
        }))?,
    )?;
    let out = temp.path().join("health_degraded.json");

    let output = remote_operator_command(temp.path())
        .arg("--health")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--health-max-age-sec")
        .arg("999999999")
        .arg("--agent-intent-mode")
        .arg("auto")
        .arg("--agent-base-url")
        .arg("http://127.0.0.1:9")
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--agent-timeout-sec")
        .arg("1")
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        !output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["health_status"], "degraded");
    assert_eq!(result["transport_issues"], json!([]));
    assert_eq!(result["agent_runtime_status"]["status"], "unavailable");
    assert!(result["issues"]
        .as_array()
        .expect("issues")
        .contains(&json!("agent_runtime_unavailable")));
    assert_eq!(result["action_readiness"][0]["action"], "status");
    assert_eq!(result["action_readiness"][0]["status"], "healthy");
    assert_eq!(result["action_readiness"][2]["action"], "build_plan");
    assert_eq!(result["action_readiness"][2]["status"], "blocked");
    assert_eq!(
        result["action_readiness"][2]["blocked_actions"],
        json!(["new_plan", "start_offdesk"])
    );
    Ok(())
}

#[test]
#[serial]
fn telegram_operator_systemd_installer_dry_run_renders_unit() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("service.json");

    let output = Command::new("python3")
        .arg(script_path("install_offdesk_telegram_operator_service.py"))
        .arg("--dry-run")
        .arg("--repo-root")
        .arg(temp.path())
        .arg("--forager-bin")
        .arg(temp.path().join("target/debug/forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(temp.path().join("loop.json"))
        .env("HOME", temp.path())
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    fs::write(&out, &output.stdout)?;
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(
        result["schema"],
        "forager_telegram_operator_systemd_install.v1"
    );
    assert_eq!(result["installed"], false);
    let unit = result["unit_preview"].as_str().expect("unit preview");
    assert!(unit.contains("ExecStart="));
    assert!(unit.contains("offdesk_remote_operator_telegram.py"));
    assert!(unit.contains("--poll-timeout-sec 30"));
    assert!(unit.contains("Restart=on-failure"));
    assert!(!unit.contains("fake-token-for-test"));
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
    assert!(preview.contains("승인 요청 2개가 먼저입니다."));
    assert!(preview.contains("아래 버튼으로 승인 내용 보기"));
    assert!(preview.contains("그 밖에 실패 1 · 마무리 1 · 진행 1 / 대기 3"));
    assert_eq!(
        result["interaction_context"]["context_kind"],
        "status_attention"
    );
    assert_eq!(
        result["interaction_context"]["focus_kind"],
        "approval_queue"
    );
    assert_eq!(result["interaction_context"]["next_command"], "/pending");
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        true
    );
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
    assert!(preview.contains("<b>승인 대기</b>"));
    assert!(preview.contains("승인 요청 4개가 기다립니다. 만료 1개 포함."));
    assert!(preview.contains("계획 승인"));
    assert!(preview.contains("실행 승인"));
    assert!(preview.contains("외 2개 더 있음"));
    assert!(preview.contains("승인은 로컬에서 판단하세요."));
    assert_eq!(
        result["interaction_context"]["context_kind"],
        "approval_attention"
    );
    assert_eq!(result["interaction_context"]["focus_ref"], "approval_one");
    assert_eq!(
        result["interaction_context"]["next_command"],
        "/pending --all"
    );
    assert!(button_texts(&result).contains(&"전체 승인".to_string()));
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        true
    );
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
    assert!(preview.contains("<b>자율주행 계획</b>"));
    assert!(preview.contains("plan_harness_mobile · 수정 필요"));
    assert!(preview.contains("아래 버튼으로 계획 상세 보기"));
    assert_eq!(
        result["interaction_context"]["context_kind"],
        "plan_attention"
    );
    assert_eq!(
        result["interaction_context"]["focus_ref"],
        "plan_harness_mobile"
    );
    assert_eq!(
        result["interaction_context"]["next_command"],
        "/show plan_harness_mobile"
    );
    assert!(button_texts(&result).contains(&"/show plan_harness_mobile".to_string()));
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        true
    );
    assert_mobile_contract(&result);
    Ok(())
}
