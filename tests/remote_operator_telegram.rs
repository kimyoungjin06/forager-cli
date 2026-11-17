use anyhow::Result;
use chrono::{Local, Timelike};
use serde_json::{json, Map, Value};
use serial_test::serial;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use std::{env, ffi::OsString};
use tempfile::tempdir;

#[cfg(unix)]
use std::os::unix::fs::{symlink, PermissionsExt};

fn script_path(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join(name)
}

fn remote_session_identity(session_id: &str, project: &str, tool: &str) -> Value {
    let mut identity = json!({
        "schema": "remote_session_identity.v1",
        "machine_identity_sha256": "1".repeat(64),
        "project_key": project,
        "project_root_identity_sha256": "2".repeat(64),
        "worktree_kind": "direct_project_root",
        "worktree_identity_sha256": "3".repeat(64),
        "harness_kind": tool,
        "harness_launch_sha256": "4".repeat(64),
        "session_id": session_id,
        "session_record_identity_sha256": "5".repeat(64),
        "transport_kind": "tmux",
        "transport_session_identity_sha256": "6".repeat(64),
        "pane_identity_sha256": "7".repeat(64),
        "process_identity_sha256": "8".repeat(64),
    });
    let encoded = serde_json::to_vec(&identity).expect("serialize remote session identity");
    identity["binding_sha256"] = Value::String(format!("{:x}", Sha256::digest(encoded)));
    identity
}

fn notification_capture(session_id: &str, project: &str, tool: &str, tail: &str) -> Value {
    json!({"schema": "notification_capture.v1",
        "binding_sha256": remote_session_identity(session_id, project, tool)["binding_sha256"],
        "tail": tail})
}

fn write_env_file(path: &Path) -> Result<()> {
    fs::write(
        path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\n",
    )?;
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

fn default_test_control_generation() -> String {
    let material = json!({
        "operator_members": [["123456789", "987654321"]],
        "operator_mode": "single_owner",
        "owner_chat_id": "123456789",
        "owner_user_id": "987654321",
        "token": "999999:fake-token-for-test",
        "transport": "telegram"
    });
    format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&material).expect("control generation material"))
    )
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

fn scripts_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts")
}

fn run_notification_cycle_probe(source: &str) -> Result<Value> {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.common import RemoteOperatorTelegramError
from telegram_operator.notification_policy import (
    NotificationCandidate, NotificationCycle, NotificationPriority,
)

sent = []
committed = []

def commit(name):
    return lambda message_id: committed.append([name, message_id])

def candidate(event_key, source, priority, name):
    return NotificationCandidate(
        event_key=event_key,
        source=source,
        priority=priority,
        message=name,
        reply_markup=None,
        report={"name": name},
        on_delivered=commit(name),
    )

def send(item):
    sent.append(item.event_key)
    if sys.argv[2] == "fail":
        raise RemoteOperatorTelegramError("temporary Telegram outage")
    return 700 + len(sent)

cycle = NotificationCycle(send)
cycle.add(candidate("autonomy:night", "autonomy", NotificationPriority.AUTONOMY, "autonomy"))
cycle.add(candidate("task:ready", "task_ready", NotificationPriority.TASK_READY, "task"))
cycle.add(candidate("decision:one", "decision", NotificationPriority.DECISION, "decision"))
cycle.add(candidate("session:permission", "session", NotificationPriority.INTERACTION, "permission"))
cycle.add(candidate("session:permission", "session", NotificationPriority.INTERACTION, "permission-duplicate"))
first = cycle.deliver()
second = cycle.deliver()

retry_sent = []
retry_committed = []
retry = NotificationCycle(lambda item: (retry_sent.append(item.event_key) or 901))
retry.add(NotificationCandidate(
    event_key="session:permission",
    source="session",
    priority=NotificationPriority.INTERACTION,
    message="permission-retry",
    reply_markup=None,
    report={"name": "permission-retry"},
    on_delivered=lambda message_id: retry_committed.append(message_id),
))
retry_result = retry.deliver()

print(json.dumps({
    "first": first,
    "second": second,
    "sent": sent,
    "committed": committed,
    "retry": retry_result,
    "retry_sent": retry_sent,
    "retry_committed": retry_committed,
}))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(source)
        .output()?;
    assert!(
        output.status.success(),
        "notification cycle probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn find_schema<'a>(value: &'a Value, schema: &str) -> Option<&'a Value> {
    match value {
        Value::Object(map) => {
            if value.get("schema").and_then(Value::as_str) == Some(schema) {
                return Some(value);
            }
            map.values().find_map(|child| find_schema(child, schema))
        }
        Value::Array(items) => items.iter().find_map(|child| find_schema(child, schema)),
        _ => None,
    }
}

#[cfg(unix)]
fn install_fake_tmux(temp: &Path, prompts: &Map<String, Value>) -> Result<OsString> {
    let bin_dir = temp.join("fake-bin");
    fs::create_dir_all(&bin_dir)?;
    let tmux = bin_dir.join("tmux");
    let prompt_json = serde_json::to_string(prompts)?;
    let script = format!(
        r#"#!/usr/bin/env python3
import json, sys
prompts = json.loads({prompt_json:?})
args = sys.argv[1:]
if args == ["-V"]:
    print("tmux fake 1.0")
elif args[:2] == ["ls", "-F"]:
    print("\n".join(prompts))
elif args and args[0] == "capture-pane":
    target = args[args.index("-t") + 1]
    print(prompts.get(target, ""))
elif args and args[0] == "display-message":
    print("sleep")
else:
    raise SystemExit(0)
"#,
    );
    fs::write(&tmux, script)?;
    fs::set_permissions(&tmux, fs::Permissions::from_mode(0o700))?;
    let mut paths = vec![bin_dir];
    if let Some(existing) = env::var_os("PATH") {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|error| anyhow::anyhow!(error))
}

#[cfg(unix)]
fn install_fake_forager(temp: &Path) -> Result<PathBuf> {
    let forager = temp.join("fake-forager.py");
    fs::write(
        &forager,
        r#"#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
if "pause-status" in argv:
    print(json.dumps({"schema": "offdesk_pause_status.v1", "paused": False}))
elif "shift" in argv and "status" in argv:
    print(json.dumps({
        "schema": "offdesk_shift_status_report.v1",
        "active_shift_id": None,
        "shifts": [{
            "shift_id": "shift_notification_arbitration",
            "status": None,
            "starts_at": "2026-09-07T17:00:00+00:00",
            "stops_at": "2099-01-01T00:00:00+00:00",
            "manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "task_count": 2,
            "routine_count": 1
        }]
    }))
elif "workstation-surface" in argv:
    print(json.dumps({
        "schema": "workstation_surface.v1",
        "profile": "default",
        "redaction": {"operator_safe": True, "path_policy": "summary_first"},
        "decision_inbox": {"items": [{
            "decision_id": "decision-attention",
            "title": "Review attention decision",
            "status": "user_pending",
            "action_envelopes": [{"action_kind": "revise"}]
        }]},
        "accepted_truth_recovery": {"items": [{
            "closeout_id": "recovery-attention",
            "stage": "recovery_required",
            "acceptance_status": "blocked",
            "next_safe_action": "Review the accepted-truth recovery packet",
            "action_envelopes": [{"action_kind": "inspect_recovery"}]
        }]},
        "projects": []
    }))
else:
    print(json.dumps({"sessions": []}))
"#,
    )?;
    fs::set_permissions(&forager, fs::Permissions::from_mode(0o700))?;
    Ok(forager)
}

#[test]
#[serial]
fn notification_cycle_sends_one_deterministic_highest_priority_card_and_is_idempotent() -> Result<()>
{
    let result = run_notification_cycle_probe("success")?;
    let first = &result["first"];
    assert_eq!(first["schema"], "remote_operator_notification_cycle.v1");
    assert_eq!(first["status"], "delivered");
    assert_eq!(first["candidate_count"], 4);
    assert_eq!(first["duplicate_count"], 1);
    assert_eq!(first["attempted_count"], 1);
    assert_eq!(first["delivered_count"], 1);
    assert_eq!(first["selected_priority"], 10);
    assert_eq!(result["sent"].as_array().map(Vec::len), Some(1));
    assert_eq!(result["committed"].as_array().map(Vec::len), Some(1));
    assert_eq!(result["second"], result["first"]);
    // A new poll can retry a deferred semantic event, while the closed first
    // cycle never sends a second card.
    assert_eq!(result["retry"]["status"], "delivered");
    assert_eq!(result["retry"]["selected_priority"], 10);
    assert_eq!(result["retry_sent"].as_array().map(Vec::len), Some(1));
    Ok(())
}

#[test]
#[serial]
fn notification_cycle_failure_does_not_fallback_or_commit_and_retry_remains_eligible() -> Result<()>
{
    let result = run_notification_cycle_probe("fail")?;
    let first = &result["first"];
    assert_eq!(first["status"], "send_failed");
    assert_eq!(first["attempted_count"], 1);
    assert_eq!(first["delivered_count"], 0);
    assert_eq!(result["sent"].as_array().map(Vec::len), Some(1));
    assert!(result["committed"].as_array().is_some_and(Vec::is_empty));
    // Failure of the selected interaction must not fall through to a lower
    // priority decision in the same poll. A later poll can retry it.
    assert_eq!(result["retry"]["status"], "delivered");
    assert_eq!(result["retry_sent"].as_array().map(Vec::len), Some(1));
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn production_run_once_arbitrates_attention_autonomy_and_waiting_permission() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let fake_forager = install_fake_forager(temp.path())?;

    let session_id = "perm000000000001";
    let tmux_name = "forager_test_perm0000";
    let mut prompts = Map::new();
    prompts.insert(
        tmux_name.to_string(),
        json!("Allow this command?\n1. Allow\n2. Deny"),
    );
    let fake_tmux_path = install_fake_tmux(temp.path(), &prompts)?;
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{
                "id": session_id,
                "title": "Permission agent",
                "tool": "codex",
                "status": "waiting",
                "project": "project",
                "remote_session_identity": remote_session_identity(session_id, "project", "codex"),
                "notification_capture": notification_capture(session_id, "project", "codex", "Allow this command?\n1. Allow\n2. Deny")
            }]
        }))?,
    )?;
    let updates = temp.path().join("updates.json");
    fs::write(&updates, "[]\n")?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "telegram_session_keyboard_schema": "inline_v3",
            "control_generation_sha256": default_test_control_generation()
        }))?,
    )?;
    let out = temp.path().join("result.json");
    let watch_dir = temp.path().join("activity");
    fs::create_dir_all(&watch_dir)?;
    let marker = watch_dir.join("session.jsonl");
    fs::write(&marker, "{}\n")?;
    let old = SystemTime::now() - Duration::from_secs(2 * 3600);
    fs::File::options()
        .write(true)
        .open(&marker)?
        .set_modified(old)?;
    fs::File::open(&watch_dir)?.set_modified(old)?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&updates)
        .arg("--forager-bin")
        .arg(&fake_forager)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&out)
        .arg("--attention-notify")
        .arg("--autonomy-propose")
        .arg("--autonomy-propose-from-hour")
        .arg("0")
        .arg("--autonomy-idle-min")
        .arg("30")
        .arg("--autonomy-watch-path")
        .arg(&watch_dir)
        .arg("--session-notify")
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--session-notify-quiet-from-hour")
        .arg("0")
        .arg("--session-notify-quiet-to-hour")
        .arg("0")
        .env("PATH", &fake_tmux_path)
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let cycle = find_schema(&result, "remote_operator_notification_cycle.v1")
        .expect("run_once should expose the shared notification cycle report");
    assert_eq!(cycle["status"], "delivered");
    assert_eq!(cycle["attempted_count"], 1);
    assert_eq!(cycle["delivered_count"], 1);
    // Interaction cards outrank ordinary decisions and autonomy proposals.
    assert_eq!(cycle["selected_priority"], 10);
    assert!(cycle["deferred_count"].as_u64().unwrap_or(0) >= 2);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state["waiting_notified_by_session"][session_id].is_object());
    assert!(state["attention_notified_by_key"]
        .as_object()
        .is_none_or(Map::is_empty));
    assert!(state["pending_dispatch_confirmations_by_chat"]
        .as_object()
        .is_none_or(Map::is_empty));
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn production_waiting_collectors_defer_lower_priority_session_until_next_poll() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let permission_id = "perm000000000002";
    let ready_id = "ready000000000003";
    let permission_tmux = "forager_test_perm0000";
    let ready_tmux = "forager_test_ready000";
    let mut prompts = Map::new();
    prompts.insert(
        permission_tmux.to_string(),
        json!("Allow this command?\n1. Allow\n2. Deny"),
    );
    prompts.insert(ready_tmux.to_string(), json!(">"));
    let fake_tmux_path = install_fake_tmux(temp.path(), &prompts)?;

    let status_path = temp.path().join("status.json");
    // Reverse input order to prove ranking is independent of scanner order.
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [
                {"id": ready_id, "title": "Ready agent", "tool": "codex",
                 "status": "waiting", "project": "project",
                 "remote_session_identity": remote_session_identity(ready_id, "project", "codex"),
                 "notification_capture": notification_capture(ready_id, "project", "codex", ">")},
                {"id": permission_id, "title": "Permission agent", "tool": "codex",
                 "status": "waiting", "project": "project",
                 "remote_session_identity": remote_session_identity(permission_id, "project", "codex"),
                 "notification_capture": notification_capture(permission_id, "project", "codex", "Allow this command?\n1. Allow\n2. Deny")}
            ]
        }))?,
    )?;
    let updates = temp.path().join("updates.json");
    fs::write(&updates, "[]\n")?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "telegram_session_keyboard_schema": "inline_v3",
            "control_generation_sha256": default_test_control_generation()
        }))?,
    )?;

    let run = |out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&updates)
            .arg("--forager-bin")
            .arg(env!("CARGO_BIN_EXE_forager"))
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--out")
            .arg(out)
            .arg("--session-notify")
            .arg("--session-status-file")
            .arg(&status_path)
            .arg("--session-notify-quiet-from-hour")
            .arg("0")
            .arg("--session-notify-quiet-to-hour")
            .arg("0")
            .env("PATH", &fake_tmux_path)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let first = run(&temp.path().join("first.json"))?;
    let first_cycle = find_schema(&first, "remote_operator_notification_cycle.v1")
        .expect("first poll cycle report");
    assert_eq!(first_cycle["status"], "delivered");
    assert_eq!(first_cycle["selected_priority"], 10);
    let first_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(first_state["waiting_notified_by_session"][permission_id].is_object());
    assert!(first_state["waiting_notified_by_session"][ready_id].is_null());

    let second = run(&temp.path().join("second.json"))?;
    let second_cycle = find_schema(&second, "remote_operator_notification_cycle.v1")
        .expect("second poll cycle report");
    assert_eq!(second_cycle["status"], "delivered");
    assert_eq!(second_cycle["selected_priority"], 40);
    let second_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(second_state["waiting_notified_by_session"][permission_id].is_object());
    assert!(second_state["waiting_notified_by_session"][ready_id].is_object());
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn production_waiting_collector_respects_quiet_hours_without_consuming_candidate() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let session_id = "quiet000000000004";
    let tmux_name = "forager_test_quiet000";
    let mut prompts = Map::new();
    prompts.insert(
        tmux_name.to_string(),
        json!("Allow this command?\n1. Allow\n2. Deny"),
    );
    let fake_tmux_path = install_fake_tmux(temp.path(), &prompts)?;
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{"id": session_id, "title": "Quiet agent", "tool": "codex",
             "status": "waiting", "project": "project",
             "remote_session_identity": remote_session_identity(session_id, "project", "codex"),
             "notification_capture": notification_capture(session_id, "project", "codex", "Allow this command?\n1. Allow\n2. Deny")}]
        }))?,
    )?;
    let updates = temp.path().join("updates.json");
    fs::write(&updates, "[]\n")?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "telegram_session_keyboard_schema": "inline_v3",
            "control_generation_sha256": default_test_control_generation()
        }))?,
    )?;
    let now_hour = Local::now().hour();
    let quiet_to = (now_hour + 1) % 24;
    let out = temp.path().join("quiet.json");
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&updates)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&out)
        .arg("--session-notify")
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--session-notify-quiet-from-hour")
        .arg(now_hour.to_string())
        .arg("--session-notify-quiet-to-hour")
        .arg(quiet_to.to_string())
        .env("PATH", &fake_tmux_path)
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state["waiting_notified_by_session"]
        .as_object()
        .is_none_or(Map::is_empty));
    if let Some(cycle) = find_schema(&result, "remote_operator_notification_cycle.v1") {
        assert_ne!(cycle["status"], "delivered");
    }
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn production_keyboard_cleanup_is_one_card_then_waiting_card_on_next_poll() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let session_id = "ready000000000005";
    let tmux_name = "forager_test_ready000";
    let mut prompts = Map::new();
    prompts.insert(tmux_name.to_string(), json!(">"));
    let fake_tmux_path = install_fake_tmux(temp.path(), &prompts)?;
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{"id": session_id, "title": "Ready agent", "tool": "codex",
             "status": "waiting", "project": "project",
             "remote_session_identity": remote_session_identity(session_id, "project", "codex"),
             "notification_capture": notification_capture(session_id, "project", "codex", ">")}]
        }))?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0
        }))?,
    )?;

    let run = |out: &Path, api_base: &str| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--once")
            .arg("--forager-bin")
            .arg(env!("CARGO_BIN_EXE_forager"))
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--out")
            .arg(out)
            .arg("--session-notify")
            .arg("--session-status-file")
            .arg(&status_path)
            .arg("--poll-timeout-sec")
            .arg("0")
            .arg("--api-timeout-sec")
            .arg("2")
            .arg("--session-notify-quiet-from-hour")
            .arg("0")
            .arg("--session-notify-quiet-to-hour")
            .arg("0")
            .env("PATH", &fake_tmux_path)
            .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", api_base)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let (first_base, first_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": true, "result": {"message_id": 901}}),
    ])?;
    let first = run(&temp.path().join("cleanup.json"), &first_base)?;
    let first_cycle =
        find_schema(&first, "remote_operator_notification_cycle.v1").expect("cleanup cycle report");
    assert_eq!(first_cycle["status"], "delivered");
    assert_eq!(first_cycle["selected_priority"], 0);
    assert_eq!(first_cycle["attempted_count"], 1);
    assert_eq!(first["session_notification"]["keyboard_cleanup"], "removed");
    assert_eq!(
        first_server.join().expect("cleanup fake Telegram")?.len(),
        2
    );
    let first_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(first_state["telegram_session_keyboard_schema"], "inline_v3");
    assert!(first_state["waiting_notified_by_session"]
        .as_object()
        .is_none_or(Map::is_empty));

    let (second_base, second_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": true, "result": {"message_id": 902}}),
    ])?;
    let second = run(&temp.path().join("waiting.json"), &second_base)?;
    let second_cycle = find_schema(&second, "remote_operator_notification_cycle.v1")
        .expect("waiting cycle report");
    assert_eq!(second_cycle["status"], "delivered");
    assert_eq!(second_cycle["selected_priority"], 40);
    assert_eq!(second["session_notification"]["status"], "notified");
    assert_eq!(
        second_server.join().expect("waiting fake Telegram")?.len(),
        2
    );
    let second_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(second_state["waiting_notified_by_session"][session_id].is_object());
    Ok(())
}

#[test]
#[serial]
fn production_attention_failure_preserves_recovery_and_deferred_decision_for_retry() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let fake_forager = install_fake_forager(temp.path())?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "control_generation_sha256": default_test_control_generation(),
            "telegram_session_keyboard_schema": "inline_v3"
        }))?,
    )?;

    let run = |out: &Path, api_base: &str| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--once")
            .arg("--forager-bin")
            .arg(&fake_forager)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--out")
            .arg(out)
            .arg("--attention-notify")
            .arg("--poll-timeout-sec")
            .arg("0")
            .arg("--api-timeout-sec")
            .arg("2")
            .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", api_base)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let (failed_base, failed_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": false, "description": "temporary Telegram outage"}),
    ])?;
    let failed = run(&temp.path().join("failed.json"), &failed_base)?;
    let failed_cycle = find_schema(&failed, "remote_operator_notification_cycle.v1")
        .expect("failed attention cycle report");
    assert_eq!(failed_cycle["status"], "send_failed");
    assert_eq!(failed_cycle["selected_priority"], 20);
    assert_eq!(failed_cycle["attempted_count"], 1);
    assert_eq!(failed_cycle["delivered_count"], 0);
    assert_eq!(failed["attention_notification"]["status"], "send_failed");
    assert_eq!(
        failed_server.join().expect("failed fake Telegram")?.len(),
        2
    );
    let failed_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(failed_state["attention_notified_by_key"]
        .as_object()
        .is_none_or(Map::is_empty));

    let (retry_base, retry_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": true, "result": {"message_id": 903}}),
    ])?;
    let retry = run(&temp.path().join("retry.json"), &retry_base)?;
    let retry_cycle = find_schema(&retry, "remote_operator_notification_cycle.v1")
        .expect("retry attention cycle report");
    assert_eq!(retry_cycle["status"], "delivered");
    assert_eq!(retry_cycle["selected_priority"], 20);
    assert_eq!(retry["attention_notification"]["status"], "notified");
    assert_eq!(retry_server.join().expect("retry fake Telegram")?.len(), 2);
    let retry_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(retry_state["attention_notified_by_key"]["recovery:recovery-attention"].is_object());
    assert!(retry_state["attention_notified_by_key"]["decision:decision-attention"].is_null());
    Ok(())
}

fn watchdog_command(home: &Path) -> Command {
    let mut command = Command::new("python3");
    command.arg(script_path("offdesk_remote_operator_watchdog.py"));
    command.env("HOME", home);
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command.env_remove("FORAGER_PROFILE");
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

fn assert_effect(result: &Value, kind: &str, authority_domain: &str) {
    assert_eq!(result["read_only"], false);
    assert_eq!(result["mutation_authorized"], true);
    assert_eq!(result["authority_domain"], authority_domain);
    assert_eq!(
        result["effect"]["schema"],
        "remote_operator_telegram_effect.v1"
    );
    assert_eq!(result["effect"]["kind"], kind);
    assert_eq!(result["effect"]["status"], "applied");
    assert_eq!(result["effect"]["authorized"], true);
    assert_eq!(result["effect"]["authority_domain"], authority_domain);
}

fn last_conversation_row(home: &Path) -> Result<Value> {
    let path = home
        .join(".cache")
        .join("forager")
        .join("remote_operator_telegram_conversation.jsonl");
    let content = fs::read_to_string(path)?;
    Ok(serde_json::from_str(
        content.lines().last().expect("conversation ledger row"),
    )?)
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

fn spawn_fake_telegram(
    responses: Vec<Value>,
) -> Result<(String, thread::JoinHandle<Result<Vec<String>>>)> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    listener.set_nonblocking(true)?;
    let handle = thread::spawn(move || -> Result<Vec<String>> {
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut requests = Vec::new();
        for response in responses {
            let (mut stream, _) = loop {
                match listener.accept() {
                    Ok(value) => break value,
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        if Instant::now() > deadline {
                            anyhow::bail!("fake Telegram server timed out waiting for requests");
                        }
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(error) => return Err(error.into()),
                }
            };
            let (request_line, _body) = read_http_request(&mut stream)?;
            requests.push(request_line);
            write_http_json(&mut stream, response)?;
        }
        Ok(requests)
    });
    Ok((format!("http://127.0.0.1:{port}"), handle))
}

fn spawn_fake_ollama_with_classification(
    body_path: PathBuf,
    classified: Value,
) -> Result<(String, thread::JoinHandle<Result<()>>)> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    listener.set_nonblocking(true)?;
    let handle = thread::spawn(move || -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(30);
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

/// Serves /api/tags any number of times and answers successive
/// /api/generate calls from `responses` in order, writing each request body
/// to `<body_dir>/generate_<n>.json`. Returns after the last response.
fn spawn_fake_ollama_with_sequence(
    body_dir: PathBuf,
    responses: Vec<Value>,
) -> Result<(String, thread::JoinHandle<Result<()>>)> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    listener.set_nonblocking(true)?;
    let handle = thread::spawn(move || -> Result<()> {
        let deadline = Instant::now() + Duration::from_secs(20);
        let mut served = 0usize;
        while served < responses.len() {
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
                fs::write(body_dir.join(format!("generate_{served}.json")), &body)?;
                write_http_json(
                    &mut stream,
                    json!({"response": serde_json::to_string(&responses[served])?}),
                )?;
                served += 1;
            } else {
                write_http_json(&mut stream, json!({"error": request_line}))?;
            }
        }
        Ok(())
    });
    Ok((format!("http://127.0.0.1:{port}"), handle))
}

fn spawn_fake_ollama(body_path: PathBuf) -> Result<(String, thread::JoinHandle<Result<()>>)> {
    spawn_fake_ollama_with_classification(
        body_path,
        json!({
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
        }),
    )
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
    let surface_kind = choice_contract["surface_kind"].as_str().unwrap_or("");
    assert!(matches!(
        surface_kind,
        "none" | "reply" | "inline" | "remove"
    ));
    if surface_kind == "reply" {
        assert_eq!(choice_contract["has_freeform_placeholder"], true);
    }

    let preview = result["message_preview"].as_str().expect("message preview");
    let preview_without_command_names = preview.replace("/receipts", "");
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
        "shell",
        "launch-prep",
        "runtime_handle_alive",
    ] {
        assert!(
            !preview_without_command_names.contains(forbidden),
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
    write_text_update_from(
        path, update_id, message_id, text, 123456789, 987654321, "private",
    )
}

fn write_callback_update(
    path: &Path,
    update_id: i64,
    message_id: i64,
    callback_data: &str,
) -> Result<()> {
    fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "update_id": update_id,
            "callback_query": {
                "id": format!("callback-{update_id}"),
                "from": {
                    "id": 987654321,
                    "is_bot": false,
                    "first_name": "Operator"
                },
                "message": {
                    "message_id": message_id,
                    "date": current_unix_timestamp(),
                    "chat": {"id": 123456789, "type": "private"},
                    "from": {"id": 999999, "is_bot": true, "first_name": "Forager"},
                    "text": "Agent card"
                },
                "data": callback_data
            }
        }))?,
    )?;
    Ok(())
}

fn prompt_identity_from_tmux_capture(capture: &[u8]) -> Result<String> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(
            "import sys; from telegram_operator.dispatch import session_prompt_identity; \
             print(session_prompt_identity(sys.argv[1], 'codex'))",
        )
        .arg(String::from_utf8_lossy(capture).to_string())
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "prompt identity helper failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(String::from_utf8(output.stdout)?.trim().to_string())
}

fn current_unix_timestamp() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock after unix epoch")
        .as_secs() as i64
}

fn write_text_update_from(
    path: &Path,
    update_id: i64,
    message_id: i64,
    text: &str,
    chat_id: i64,
    user_id: i64,
    chat_type: &str,
) -> Result<()> {
    fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": current_unix_timestamp(),
                "chat": {"id": chat_id, "type": chat_type},
                "from": {"id": user_id, "is_bot": false, "first_name": "Operator"},
                "text": text
            }
        }))?,
    )?;
    Ok(())
}

fn write_text_update_at(
    path: &Path,
    update_id: i64,
    message_id: i64,
    text: &str,
    message_date: i64,
) -> Result<()> {
    fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "update_id": update_id,
            "message": {
                "message_id": message_id,
                "date": message_date,
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                "text": text
            }
        }))?,
    )?;
    Ok(())
}

#[test]
fn telegram_default_env_file_follows_platform_config_location() -> Result<()> {
    let temp = tempdir()?;
    let xdg = temp.path().join("xdg");
    let output = Command::new("python3")
        .arg("-c")
        .arg(
            "from telegram_operator.config import default_telegram_env_file; \
             print(default_telegram_env_file())",
        )
        .env("PYTHONPATH", script_path(""))
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", &xdg)
        .env_remove("OFFDESK_TELEGRAM_ENV")
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let expected = if cfg!(target_os = "linux") {
        xdg.join("forager/telegram.env")
    } else {
        temp.path().join(".forager/telegram.env")
    };
    assert_eq!(
        String::from_utf8(output.stdout)?.trim(),
        expected.to_string_lossy()
    );
    Ok(())
}

#[test]
fn telegram_single_owner_config_is_default_and_requires_both_ids() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let probe = Command::new("python3")
        .arg("-c")
        .arg(
            "import json, pathlib; from telegram_operator.config import resolve_telegram_config; \
             c = resolve_telegram_config(pathlib.Path(__import__('sys').argv[1]), required=True); \
             print(json.dumps({'mode': c['operator_mode'], 'ready': c['operator_identity_configured'], \
             'chats': sorted(c['allowed_chat_ids']), 'users': sorted(c['allowed_user_ids'])}))",
        )
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        probe.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&probe.stderr)
    );
    let config: Value = serde_json::from_slice(&probe.stdout)?;
    assert_eq!(config["mode"], "single_owner");
    assert_eq!(config["ready"], true);
    assert_eq!(config["chats"], json!(["123456789"]));
    assert_eq!(config["users"], json!(["987654321"]));

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n",
    )?;
    #[cfg(unix)]
    fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600))?;
    let missing_user = Command::new("python3")
        .arg("-c")
        .arg(
            "import pathlib, sys; from telegram_operator.config import resolve_telegram_config; \
             resolve_telegram_config(pathlib.Path(sys.argv[1]), required=True)",
        )
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(!missing_user.status.success());
    assert!(String::from_utf8_lossy(&missing_user.stderr).contains("single_owner_user_id_missing"));
    Ok(())
}

#[test]
fn telegram_single_owner_refuses_extra_identities_and_multi_requires_pairs() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    let python =
        "import pathlib, sys; from telegram_operator.config import resolve_telegram_config; \
         resolve_telegram_config(pathlib.Path(sys.argv[1]), required=True)";

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\nTELEGRAM_ALLOW_USER_IDS=987654321,111\n",
    )?;
    #[cfg(unix)]
    fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600))?;
    let extra_identity = Command::new("python3")
        .arg("-c")
        .arg(python)
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(!extra_identity.status.success());
    assert!(String::from_utf8_lossy(&extra_identity.stderr)
        .contains("single_owner_extra_user_ids_configured"));

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OPERATOR_MODE=multi_operator\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\n",
    )?;
    let multi = Command::new("python3")
        .arg("-c")
        .arg(python)
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(!multi.status.success());
    assert!(String::from_utf8_lossy(&multi.stderr).contains("multi_operator_members_missing"));

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OPERATOR_MODE=multi_operator\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\nTELEGRAM_OPERATOR_MEMBERS=not-a-chat:333\n",
    )?;
    let malformed_member = Command::new("python3")
        .arg("-c")
        .arg(python)
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(!malformed_member.status.success());
    assert!(
        String::from_utf8_lossy(&malformed_member.stderr).contains("multi_operator_member_invalid")
    );

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OPERATOR_MODE=multi_operator\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\nTELEGRAM_OPERATOR_MEMBERS=222:333\n",
    )?;
    let paired_multi = Command::new("python3")
        .arg("-c")
        .arg(python)
        .arg(&env_path)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        paired_multi.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&paired_multi.stderr)
    );
    Ok(())
}

#[test]
fn telegram_confirmation_is_bound_to_chat_and_user() -> Result<()> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(
            "from telegram_operator.dispatch import build_confirmation, clear_confirmation, pop_confirmation, store_confirmation; \
             state = {}; confirmation = build_confirmation(kind='runtime', target_id='closeout-1', \
             action_kind='dispatch', observed_hash='', note='run', chat_hash='chat-a', user_hash='user-a'); \
             store_confirmation(state, 'chat-a', 'user-a', confirmation); \
             assert clear_confirmation(state, 'chat-a', 'user-a', 'wrong-token') is False; \
             assert state['pending_dispatch_confirmations_by_chat']['chat-a']['token'] == confirmation['token']; \
             assert pop_confirmation(state, 'chat-a', 'user-b', '') is None; \
             assert state['pending_dispatch_confirmations_by_chat']['chat-a']['token'] == confirmation['token']; \
             assert pop_confirmation(state, 'chat-a', 'user-a', '')['token'] == confirmation['token']; \
             state = {'pending_dispatch_confirmations_by_chat': {'chat-a': {'token': 'legacy'}}}; \
             assert pop_confirmation(state, 'chat-a', 'user-a', 'legacy') is None; print('ok')",
        )
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout)?.trim(), "ok");
    Ok(())
}

#[test]
fn telegram_callback_contract_decodes_only_bound_actions_and_reports_outcome() -> Result<()> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(
            "from offdesk_remote_operator_telegram import callback_consumes_card, callback_outcome_text, command_from_callback_data; \
             plan = 'fg1:p:01234567:' + ('a' * 16) + ':0'; \
             assert command_from_callback_data(plan) == '/plan_input @01234567:' + ('a' * 16) + ':0'; \
             assert command_from_callback_data('fg1:p:telegram:aaaaaaaa:0') == ''; \
             assert command_from_callback_data('fg1:v:decision-user:continue') == '/decision decision-user continue'; \
             assert command_from_callback_data('fg1:v:decision-user:revise') == ''; \
             assert command_from_callback_data('fg1:v:decision/user:continue') == ''; \
             assert callback_consumes_card(plan) is True; \
             assert callback_consumes_card('fg1:v:decision-user:continue') is True; \
             assert callback_consumes_card('fg1:s') is False; \
             from telegram_operator.rendering import choice_keyboard, plan_choice_display_label; \
             assert plan_choice_display_label('계획 승인 ' + ('b' * 16)) == '계획 승인'; \
             assert plan_choice_display_label('1 Alpha') == '1 Alpha'; \
             safe_decision = choice_keyboard({'context_kind': 'decisions_actions', 'choice_commands': ['/decision decision-user continue']}); \
             assert safe_decision['inline_keyboard'][0][0]['callback_data'] == 'fg1:v:decision-user:continue'; \
             assert choice_keyboard({'context_kind': 'decisions_actions', 'choice_commands': ['/decision decision-user revise']}) is None; \
             assert callback_outcome_text({'dispatch_result': {'ok': False, 'error': 'plan_context_changed'}}) == '계획 카드가 변경되어 처리하지 않았습니다.'; \
             assert callback_outcome_text({'status': 'rendered', 'parsed_command': {'command': 'remote_plan_selection', 'selection_status': 'selected'}}) == '계획 선택을 처리했습니다.'; \
             print('ok')",
        )
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout)?.trim(), "ok");
    Ok(())
}

#[test]
fn telegram_behavior_core_routes_and_validates_bound_inputs() -> Result<()> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(concat!(
            "from telegram_operator.behavior_core import InboundEvent, route_inbound_event, validate_action_candidate; ",
            "from telegram_operator.command_envelope import build_command_envelope; ",
            "from telegram_operator.plan_workflow import remote_plan_selection_context; ",
            "from telegram_operator.rendering import plan_context_identity, plan_session_callback_id; ",
            "event = InboundEvent(transport='telegram', source='message', event_id=1, message_id=2, occurred_at_epoch=1000, input_hash='input', actor_scope_hash='chat', actor_hash='user', text='/status'); ",
            "intent = route_inbound_event(event, active_plan_session=None, plan_callback=False, plan_text_owned=False); ",
            "assert intent.route == 'command' and intent.parsed_command['command'] == 'status'; ",
            "envelope = build_command_envelope(update_id=1, message_id=2, message_date=1000, input_hash='input', chat_hash='chat', user_hash='user', parsed_command=intent.parsed_command, plan_session_input=intent.plan_session_input, ttl_sec=60, received_epoch=1001); ",
            "candidate = validate_action_candidate(event, intent, envelope); ",
            "assert candidate.valid and candidate.public_record()['status'] == 'accepted'; ",
            "assert 'text' not in event.public_record() and 'plan_payload' not in intent.public_record(); ",
            "tampered = dict(envelope); tampered['input_hash'] = 'wrong'; ",
            "assert validate_action_candidate(event, intent, tampered).primary_issue == 'command_envelope_input_hash_mismatch'; ",
            "wrong_class = build_command_envelope(update_id=1, message_id=2, message_date=1000, input_hash='input', chat_hash='chat', user_hash='user', parsed_command=intent.parsed_command, plan_session_input=True, ttl_sec=60, received_epoch=1001); ",
            "assert validate_action_candidate(event, intent, wrong_class).primary_issue == 'action_candidate_command_mismatch'; ",
            "session = {'session_id': 'plan-session-1', 'stage': 'project_selection', 'profile': 'default', 'candidates': [{'rank': 1, 'display_name': 'Alpha', 'project_key': 'alpha'}]}; ",
            "context = remote_plan_selection_context(session); sid = plan_session_callback_id(session['session_id']); identity = plan_context_identity(context); ",
            "callback_event = InboundEvent(transport='telegram', source='callback', event_id=3, message_id=4, occurred_at_epoch=1000, input_hash='callback', actor_scope_hash='chat', actor_hash='user', text=f'/plan_input @{sid}:{identity}:0'); ",
            "plan_intent = route_inbound_event(callback_event, active_plan_session=session, plan_callback=True, plan_text_owned=False); ",
            "assert plan_intent.route == 'remote_plan_session' and plan_intent.plan_payload == '1 Alpha' and plan_intent.validation_issue is None; ",
            "stale_event = InboundEvent(transport='telegram', source='callback', event_id=5, message_id=6, occurred_at_epoch=1000, input_hash='stale', actor_scope_hash='chat', actor_hash='user', text=f'/plan_input @{sid}:' + ('0' * 16) + ':0'); ",
            "stale_intent = route_inbound_event(stale_event, active_plan_session=session, plan_callback=True, plan_text_owned=False); ",
            "assert stale_intent.route == 'remote_plan_callback_refusal' and stale_intent.validation_issue == 'plan_context_changed'; ",
            "print('ok')",
        ))
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout)?.trim(), "ok");
    Ok(())
}

#[test]
fn telegram_behavior_core_finalizes_one_redacted_response_card() -> Result<()> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(concat!(
            "from telegram_operator.behavior_core import InboundEvent, finalize_action_outcome, finalize_response_card, route_inbound_event, validate_action_candidate; ",
            "from telegram_operator.command_envelope import build_command_envelope; ",
            "from telegram_operator.rendering import choice_surface_contract, mobile_card_contract; ",
            "event = InboundEvent(transport='telegram', source='message', event_id=11, message_id=12, occurred_at_epoch=1000, input_hash='input', actor_scope_hash='chat', actor_hash='user', text='/status'); ",
            "intent = route_inbound_event(event, active_plan_session=None, plan_callback=False, plan_text_owned=False); ",
            "envelope = build_command_envelope(update_id=11, message_id=12, message_date=1000, input_hash='input', chat_hash='chat', user_hash='user', parsed_command=intent.parsed_command, plan_session_input=False, received_epoch=1001); ",
            "candidate = validate_action_candidate(event, intent, envelope); ",
            "result = {'status': 'rendered', 'mutation_authorized': False, 'effect': {'kind': 'none', 'status': 'none', 'authorized': False, 'authority_domain': 'none'}, 'message_preview': '<b>상태</b>\\n현재 대기 중입니다.\\n다음 조치: 없음', 'reply_markup_preview': None}; ",
            "outcome = finalize_action_outcome(candidate, result); ",
            "assert outcome.disposition == 'observed' and outcome.effect_kind == 'none'; ",
            "card = finalize_response_card(outcome, result, mobile_contract_builder=mobile_card_contract, choice_contract_builder=choice_surface_contract); ",
            "assert card.control_surface == 'none' and card.action_count == 0; ",
            "assert 'message' not in card.public_record() and 'reply_markup' not in card.public_record(); ",
            "result['reply_markup_preview'] = {'inline_keyboard': [[{'text': '상태', 'callback_data': 'fg1:s'}]]}; ",
            "inline = finalize_response_card(outcome, result, mobile_contract_builder=mobile_card_contract, choice_contract_builder=choice_surface_contract); ",
            "assert inline.control_surface == 'inline' and inline.action_count == 1 and inline.card_id != card.card_id; ",
            "print('ok')",
        ))
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout)?.trim(), "ok");
    Ok(())
}

#[test]
fn telegram_feedback_workflow_result_is_domain_only_and_redacted() -> Result<()> {
    let output = Command::new("python3")
        .arg("-c")
        .arg(concat!(
            "from types import SimpleNamespace; ",
            "from telegram_operator.feedback_workflow import FeedbackWorkflowOutcome, process_feedback_workflow; ",
            "record = {'workflow_command': 'feedback', 'chat_id_hash': 'chat', 'user_id_hash': 'user', 'message_id': 7, 'feedback_kind': 'freeform_feedback', 'feedback_text': 'private text'}; ",
            "outcome = FeedbackWorkflowOutcome(feedback_record=record, feedback_result={'feedback_recorded': True, 'feedback_text_chars': 12}, ingest_result={'decision_feedback_ingest_status': 'recorded', 'decision_feedback_decision_id': 'decision-1'}, plan_session=None); ",
            "fields = outcome.result_fields(); public = fields['feedback_workflow']; ",
            "assert public['schema'] == 'remote_operator_feedback_workflow.v1' and public['plan_session_started'] is False; ",
            "assert public['transition_id'].startswith('rfw-'); ",
            "assert 'feedback_text' not in public and 'message_preview' not in public and 'reply_markup_preview' not in public; ",
            "assert 'feedback_record' not in fields and 'message_preview' not in fields and 'reply_markup_preview' not in fields;\n",
            "try:\n",
            "    process_feedback_workflow(SimpleNamespace(), {}, {}, chat_hash='chat', user_hash='user', message_id=8, text='text', parsed_command={'command': 'status'}, feedback_context=None)\n",
            "    raise AssertionError('unsupported command accepted')\n",
            "except ValueError as error:\n",
            "    assert str(error) == 'unsupported_feedback_workflow_command'\n",
            "print('ok')",
        ))
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(
        output.status.success(),
        "stderr:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(String::from_utf8(output.stdout)?.trim(), "ok");
    Ok(())
}

#[test]
fn telegram_command_envelope_nonce_is_verified_before_journaling() -> Result<()> {
    let temp = tempdir()?;
    let journal = temp.path().join("journal.jsonl");
    let output = Command::new("python3")
        .arg("-c")
        .arg(
            "import pathlib, sys; \
             from telegram_operator.command_envelope import build_command_envelope; \
             from telegram_operator.update_journal import begin_update; \
             envelope = build_command_envelope(update_id=1, message_id=2, message_date=1000, \
             input_hash='input', chat_hash='chat', user_hash='user', \
             parsed_command={'supported': True, 'command': 'pause'}, \
             plan_session_input=False, ttl_sec=60, received_epoch=1001); \
             envelope['nonce'] = 'tg-tampered'; \
             begin_update(pathlib.Path(sys.argv[1]), update_id=1, input_hash='input', \
             chat_hash='chat', user_hash='user', command_envelope=envelope)",
        )
        .arg(&journal)
        .env("PYTHONPATH", script_path(""))
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("command_envelope_nonce_mismatch"));
    assert!(!journal.exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_single_owner_rejects_wrong_user_and_group_chat() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;

    for (name, user_id, chat_type, expected_reason) in [
        ("wrong_chat", 987654321_i64, "private", "chat_not_owner"),
        ("wrong_user", 111_i64, "private", "user_not_owner"),
        (
            "group_chat",
            987654321_i64,
            "group",
            "single_owner_private_chat_required",
        ),
    ] {
        let update_path = temp.path().join(format!("{name}.json"));
        write_text_update_from(
            &update_path,
            700,
            800,
            "/status",
            if name == "wrong_chat" { 222 } else { 123456789 },
            user_id,
            chat_type,
        )?;
        let out = temp.path().join(format!("{name}_out.json"));
        let state = temp.path().join(format!("{name}_state.json"));
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&update_path)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state)
            .arg("--out")
            .arg(&out)
            .output()?;
        assert!(
            output.status.success(),
            "stderr:\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let result: Value = serde_json::from_slice(&fs::read(out)?)?;
        assert_eq!(result["status"], "ignored");
        assert_eq!(result["reason"], expected_reason);
        assert_eq!(result["remote_command_envelope"], Value::Null);
    }
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_multi_operator_requires_exact_private_pair() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OPERATOR_MODE=multi_operator\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\nTELEGRAM_OPERATOR_MEMBERS=222:333\n",
    )?;
    #[cfg(unix)]
    fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600))?;

    for (index, (name, chat_id, user_id, chat_type, expected_status, expected_reason, role)) in [
        (
            "owner",
            123456789_i64,
            987654321_i64,
            "private",
            "rendered",
            "",
            "owner",
        ),
        (
            "member", 222_i64, 333_i64, "private", "rendered", "", "operator",
        ),
        (
            "cross_pair",
            222_i64,
            987654321_i64,
            "private",
            "ignored",
            "operator_pair_not_allowed",
            "",
        ),
        (
            "group",
            222_i64,
            333_i64,
            "group",
            "ignored",
            "multi_operator_private_chat_required",
            "",
        ),
    ]
    .into_iter()
    .enumerate()
    {
        let update_path = temp.path().join(format!("multi_{name}.json"));
        write_text_update_from(
            &update_path,
            710 + index as i64,
            810,
            "/status",
            chat_id,
            user_id,
            chat_type,
        )?;
        let out = temp.path().join(format!("multi_{name}_out.json"));
        let state = temp.path().join(format!("multi_{name}_state.json"));
        let output = remote_operator_command(temp.path())
            .arg("--forager-bin")
            .arg(env!("CARGO_BIN_EXE_forager"))
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&update_path)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state)
            .arg("--out")
            .arg(&out)
            .output()?;
        assert!(
            output.status.success(),
            "stderr:\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let result: Value = serde_json::from_slice(&fs::read(out)?)?;
        assert_eq!(result["status"], expected_status);
        if expected_reason.is_empty() {
            assert_eq!(result["operator_identity"]["role"], role);
            assert_eq!(
                result["operator_identity"]["operator_mode"],
                "multi_operator"
            );
        } else {
            assert_eq!(result["reason"], expected_reason);
        }
    }
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
    assert_eq!(result["operator_mode"], "single_owner");
    assert_eq!(result["operator_identity_configured"], true);
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

#[cfg(unix)]
#[test]
#[serial]
fn remote_operator_telegram_rejects_world_readable_token_file() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\n",
    )?;
    fs::set_permissions(&env_path, fs::Permissions::from_mode(0o644))?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/help")
        .arg("--env-file")
        .arg(&env_path)
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("permissions_too_open:0644"),
        "stderr: {stderr}"
    );
    assert!(stderr.contains("chmod 600"), "stderr: {stderr}");
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn remote_operator_telegram_rejects_linked_token_files() -> Result<()> {
    let temp = tempdir()?;
    let real_env = temp.path().join("telegram-real.env");
    write_env_file(&real_env)?;
    let symlink_env = temp.path().join("telegram-symlink.env");
    symlink(&real_env, &symlink_env)?;

    let symlink_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/help")
        .arg("--env-file")
        .arg(&symlink_env)
        .output()?;
    assert!(!symlink_output.status.success());
    assert!(String::from_utf8_lossy(&symlink_output.stderr)
        .contains("not a safe readable regular file"));

    let hardlink_env = temp.path().join("telegram-hardlink.env");
    fs::hard_link(&real_env, &hardlink_env)?;
    let hardlink_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/help")
        .arg("--env-file")
        .arg(&hardlink_env)
        .output()?;
    assert!(!hardlink_output.status.success());
    assert!(String::from_utf8_lossy(&hardlink_output.stderr)
        .contains("must have exactly one filesystem link"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_korean_status_question_routes_to_chat() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("korean_status_question.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("지금은 정상상태!?")
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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["parsed_command"]["reason"], "plain_text_chat");
    assert_eq!(result["projection"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(preview.contains("/status"));
    assert!(!preview.contains("<b>의견 접수</b>"));
    assert_eq!(result["reply_markup_preview"], Value::Null);
    assert_eq!(result["choice_surface_contract"]["surface_kind"], "none");
    assert!(button_texts(&result).is_empty());
    assert_mobile_contract(&result);
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
    assert!(!profile_dir(temp.path())
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
fn remote_operator_telegram_plain_text_defaults_to_chat() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_chat.json");

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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["projection"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(preview.contains("/feedback"));
    assert!(!preview.contains("<b>의견 접수</b>"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_feedback_command_gets_mobile_receipt() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_feedback.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/feedback 승인 전 실패 조건을 더 명확히 적어줘")
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
        "freeform_feedback"
    );
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
fn remote_operator_telegram_remember_command_gets_wiki_candidate_preview() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_remember.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/remember 평문 텔레그램 메시지는 기본 채팅으로 답한다")
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
    assert_eq!(result["parsed_command"]["command"], "remember");
    assert_eq!(result["projection"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>위키 후보</b>"));
    assert!(preview.contains("위키 후보 저장 미리보기입니다."));
    assert!(preview.contains("아직 런타임 지식은 아닙니다."));
    assert!(!profile_dir(temp.path())
        .join("adaptive_wiki_candidates.json")
        .exists());
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_plan_command_makes_non_execution_receipt_explicit() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_plan_request.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/plan nanoclustering Fractal tree 개발쪽을 자율주행으로 처리할 수 있을지 검토해볼까")
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
    assert_eq!(result["parsed_command"]["command"], "plan_request");
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
fn remote_operator_telegram_plan_command_classifies_korean_night_run_as_planning_request(
) -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("night_run_request.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/plan TwinPaper쪽에서 야간주행을 하고 싶어")
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
    assert_eq!(result["parsed_command"]["command"], "plan_request");
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
fn remote_operator_telegram_agent_classifies_plan_command_request() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_agent_plan_request.json");
    let agent_request_path = temp.path().join("ollama_generate_request.json");
    let (base_url, server) = spawn_fake_ollama(agent_request_path.clone())?;
    let telegram_text = "Please assess NanoClustering Fractal tree work for tomorrow night";
    let command_text = format!("/plan {telegram_text}");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg(&command_text)
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
    assert_eq!(result["parsed_command"]["command"], "plan_request");
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
fn remote_operator_telegram_agent_freeform_reply_is_conversational() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_agent_conversation.json");
    let agent_request_path = temp.path().join("ollama_conversation_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "feedback",
            "feedback_kind": "freeform_feedback",
            "confidence": 0.93,
            "project_hint": null,
            "goal": null,
            "timebox": null,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "지금 listener는 살아 있고, 최근 poll도 정상입니다. 다만 실행 권한은 로컬 검토 뒤에만 열립니다.",
            "reason": "The operator is asking for a conversational status explanation.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("방금 봇 상태를 사람이 읽기 좋게 설명해줘")
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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["parsed_command"]["feedback_kind"], Value::Null);
    assert_eq!(
        result["parsed_command"]["agent_intent"]["feedback_kind"],
        "chat"
    );
    assert_eq!(
        result["parsed_command"]["agent_intent"]["assistant_reply"],
        "지금 listener는 살아 있고, 최근 poll도 정상입니다. 다만 실행 권한은 로컬 검토 뒤에만 열립니다."
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(preview.contains("최근 poll도 정상"));
    assert!(!preview.contains("<b>의견 접수</b>"));
    assert_mobile_contract(&result);

    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    assert!(agent_request["prompt"]
        .as_str()
        .expect("agent prompt")
        .contains("assistant_reply"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_agent_clarification_is_visible_in_mobile_receipt() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("remote_agent_clarification.json");
    let agent_request_path = temp.path().join("ollama_clarification_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "clarification",
            "feedback_kind": "freeform_feedback",
            "confidence": 0.95,
            "project_hint": "nims",
            "goal": null,
            "timebox": null,
            "requires_clarification": true,
            "clarifying_question": "NIMS 안의 EPIMS 파일을 말하는지, 별도 EPIMS 프로젝트를 말하는지 확인해 주세요.",
            "reason": "The project scope is ambiguous.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("nims 프로젝트 내에 epims 프로젝트에서 지역별 클러스터링 파일 확인해줘")
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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["parsed_command"]["agent_intent"]["intent"], "chat");
    assert_eq!(
        result["parsed_command"]["agent_intent"]["requires_clarification"],
        true
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>확인 필요</b>"));
    assert!(preview.contains("NIMS 안의 EPIMS 파일"));
    assert!(preview.contains("위 질문에 평문으로 답해주세요"));
    assert!(!preview.contains("<b>의견 접수</b>"));
    assert_mobile_contract(&result);

    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    assert!(agent_request["prompt"]
        .as_str()
        .expect("agent prompt")
        .contains("same language as telegram_text"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_clarification_persists_the_sent_question() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let request_path = temp.path().join("ollama_thread_request.json");
    let question = "Scoreboard 경로 확인과 서비스 재시작 중 어느 작업을 원하시나요?";
    let (base_url, server) = spawn_fake_ollama_with_classification(
        request_path,
        json!({
            "action": "answer",
            "assistant_reply": "이 문장은 실제로 전송되면 안 됩니다.",
            "confidence": 0.9,
            "requires_clarification": true,
            "clarifying_question": question,
            "reason": "Need the requested action."
        }),
    )?;
    let update_path = temp.path().join("clarification_update.json");
    write_text_update(&update_path, 850, 970, "스코어보드가 꺼졌는데 검토해줘")?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("clarification_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(temp.path().join("missing-forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = result["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["last_interaction_context_by_chat"][chat_hash]["context_kind"],
        "chat_clarification"
    );
    assert_eq!(
        state["last_interaction_context_by_chat"][chat_hash]["question"],
        question
    );
    let history = state["chat_history_by_chat"][chat_hash]
        .as_array()
        .expect("chat history");
    let reply = history.last().expect("assistant history");
    assert!(reply["text"]
        .as_str()
        .is_some_and(|text| text.contains(question)));
    assert!(!reply["text"]
        .as_str()
        .is_some_and(|text| text.contains("실제로 전송되면 안")));
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
        .arg("/plan Please assess generic product telemetry cleanup for tonight")
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
    assert_eq!(result["parsed_command"]["command"], "plan_request");
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
    let mut remembered_context = first["interaction_context"].clone();
    remembered_context["remembered_at"] = json!("2999-01-01T00:00:00+00:00");
    let mut contexts = Map::new();
    contexts.insert(chat_hash.to_string(), remembered_context);
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
        .arg("/feedback 실패 조건 보강 필요")
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
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        false
    );
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_ignores_stale_last_card_context() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("feedback_result.json");
    let chat_hash = "sha256:9dd7fefaf214ceca";
    let mut contexts = Map::new();
    contexts.insert(
        chat_hash.to_string(),
        json!({
            "schema": "telegram_interaction_context.v1",
            "context_kind": "plan_attention",
            "focus_ref": "stale_plan",
            "focus_label": "수정 필요",
            "next_command": "/show stale_plan",
            "remembered_at": "2000-01-01T00:00:00+00:00"
        }),
    );
    let state = json!({
        "schema": "remote_operator_telegram_state.v1",
        "offset": 0,
        "last_interaction_context_by_chat": Value::Object(contexts)
    });
    fs::write(&state_path, serde_json::to_string_pretty(&state)?)?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/feedback 오래된 카드 맥락을 붙이지 마")
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
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(!preview.contains("stale_plan"));
    assert_eq!(result["feedback_context"], Value::Null);
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        false
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
                "date": current_unix_timestamp(),
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                "text": "/feedback 모바일 메시지에서 핵심만 남겨줘"
            }
        }))?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
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
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
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
    assert_eq!(
        result["inbound_event"]["schema"],
        "remote_operator_inbound_event.v1"
    );
    assert_eq!(result["inbound_event"]["source"], "message");
    assert!(result["inbound_event"].get("text").is_none());
    assert_eq!(
        result["routed_intent"]["schema"],
        "remote_operator_routed_intent.v1"
    );
    assert_eq!(result["routed_intent"]["route"], "command");
    assert_eq!(result["routed_intent"]["command"], "feedback");
    assert!(result["routed_intent"].get("plan_payload").is_none());
    assert_eq!(
        result["action_candidate"]["schema"],
        "remote_operator_action_candidate.v1"
    );
    assert_eq!(result["action_candidate"]["status"], "accepted");
    assert_eq!(
        result["action_outcome"]["schema"],
        "remote_operator_action_outcome.v1"
    );
    assert_eq!(result["action_outcome"]["disposition"], "succeeded");
    assert_eq!(result["action_outcome"]["effect_kind"], "feedback_record");
    assert_eq!(
        result["response_card"]["schema"],
        "remote_operator_response_card.v1"
    );
    assert_eq!(
        result["response_card"]["outcome_id"],
        result["action_outcome"]["outcome_id"]
    );
    assert_eq!(result["response_card"]["control_surface"], "none");
    assert!(result["response_card"].get("message").is_none());
    assert_eq!(result["feedback_recorded"], true);
    assert_eq!(result["decision_feedback_ingest_status"], "recorded");
    assert_eq!(result["decision_feedback_appended"], true);
    assert_eq!(
        result["feedback_workflow"]["schema"],
        "remote_operator_feedback_workflow.v1"
    );
    assert_eq!(result["feedback_workflow"]["command"], "feedback");
    assert_eq!(result["feedback_workflow"]["plan_session_started"], false);
    assert!(result["feedback_workflow"].get("feedback_text").is_none());
    assert!(result["feedback_workflow"].get("message_preview").is_none());
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

    let ledger_path = profile_dir(temp.path()).join("offdesk_decisions.jsonl");
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
    let outbox: Vec<Value> = fs::read_to_string(&outbox_path)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert_eq!(outbox[0]["action_outcome"], result["action_outcome"]);
    assert_eq!(outbox[0]["response_card"], result["response_card"]);
    assert_eq!(outbox[0]["feedback_workflow"], result["feedback_workflow"]);
    let journal: Vec<Value> = fs::read_to_string(&journal_path)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert_eq!(
        journal.last().expect("completed journal row")["action_outcome"],
        result["action_outcome"]
    );
    assert_eq!(
        journal.last().expect("completed journal row")["response_card"],
        result["response_card"]
    );
    assert_eq!(
        journal.last().expect("completed journal row")["feedback_workflow"],
        result["feedback_workflow"]
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plain_text_chat_does_not_record_feedback() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("chat_update.json");
    write_text_update(&update_path, 600, 778, "챗봇이랑 대화하고 싶어")?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let out = temp.path().join("chat_replay_result.json");

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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["feedback_recorded"], Value::Null);
    assert_eq!(result["decision_feedback_ingest_status"], Value::Null);
    assert!(!feedback_file.exists());
    assert!(!ingest_dir.exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(!preview.contains("<b>의견 접수</b>"));
    assert_mobile_contract(&result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 601);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_chat_records_history_without_context_refresh() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let status_update = temp.path().join("status_update.json");
    write_text_update(&status_update, 630, 790, "/status")?;
    let status_out = temp.path().join("status_result.json");
    let status_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&status_update)
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
        .arg(&status_out)
        .output()?;
    assert!(
        status_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&status_output.stdout),
        String::from_utf8_lossy(&status_output.stderr)
    );
    let status_result: Value = serde_json::from_slice(&fs::read(&status_out)?)?;
    let chat_hash = status_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let state_before: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let remembered_before = state_before["last_interaction_context_by_chat"][&chat_hash]
        ["remembered_at"]
        .as_str()
        .expect("remembered_at after status")
        .to_string();

    let chat_update = temp.path().join("chat_update.json");
    write_text_update(&chat_update, 631, 791, "지금 상태 요약해줘")?;
    let chat_out = temp.path().join("chat_result.json");
    let chat_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&chat_update)
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
        .arg(&chat_out)
        .output()?;
    assert!(
        chat_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&chat_output.stdout),
        String::from_utf8_lossy(&chat_output.stderr)
    );
    let chat_result: Value = serde_json::from_slice(&fs::read(&chat_out)?)?;
    assert_eq!(chat_result["parsed_command"]["command"], "chat");

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 632);
    let history = state["chat_history_by_chat"][&chat_hash]
        .as_array()
        .expect("chat history entries");
    assert!(history.iter().any(|entry| {
        entry["role"] == "operator"
            && entry["text"]
                .as_str()
                .is_some_and(|text| text.contains("지금 상태 요약해줘"))
    }));
    let assistant = history
        .iter()
        .rev()
        .find(|entry| entry["role"] == "assistant")
        .expect("assistant history entry");
    assert!(assistant["text"]
        .as_str()
        .is_some_and(|text| !text.contains("다음 조치:")));
    let conversation_path = temp
        .path()
        .join(".cache/forager/remote_operator_telegram_conversation.jsonl");
    let conversation = fs::read_to_string(&conversation_path)?;
    let last_row: Value = serde_json::from_str(
        conversation
            .lines()
            .last()
            .expect("conversation ledger row"),
    )?;
    assert_eq!(
        last_row["schema"],
        "remote_operator_telegram_conversation.v1"
    );
    assert_eq!(last_row["command"], "chat");
    assert!(last_row["sent_text"]
        .as_str()
        .is_some_and(|text| !text.is_empty()));
    #[cfg(unix)]
    assert_eq!(
        fs::metadata(&conversation_path)?.permissions().mode() & 0o777,
        0o600
    );
    // Chat must not refresh the last card context timestamp; otherwise the
    // context-max-age expiry never fires for chatty operators.
    assert_eq!(
        state["last_interaction_context_by_chat"][&chat_hash]["remembered_at"]
            .as_str()
            .expect("remembered_at after chat"),
        remembered_before
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_remember_records_adaptive_wiki_candidate() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("remember_update.json");
    write_text_update(
        &update_path,
        610,
        779,
        "/remember 평문 텔레그램 메시지는 기본 채팅으로 답한다",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let out = temp.path().join("remember_replay_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
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
    assert_eq!(result["parsed_command"]["command"], "remember");
    assert_eq!(result["wiki_candidate_recorded"], true);
    assert_eq!(result["wiki_candidate_status"], "recorded");
    assert_eq!(result["feedback_recorded"], Value::Null);
    assert_eq!(result["decision_feedback_ingest_status"], Value::Null);
    assert!(!feedback_file.exists());
    assert!(!ingest_dir.exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("위키 후보로 저장했습니다."));
    assert!(preview.contains("아직 런타임 지식은 아닙니다."));
    assert_mobile_contract(&result);

    let candidates_path = profile_dir(temp.path()).join("adaptive_wiki_candidates.json");
    let candidates_state: Value = serde_json::from_slice(&fs::read(&candidates_path)?)?;
    let candidates = candidates_state["candidates"]
        .as_array()
        .expect("candidate list");
    assert_eq!(candidates.len(), 1);
    let candidate = &candidates[0];
    assert_eq!(candidate["kind"], "preference");
    assert_eq!(candidate["scope"], "user_global");
    assert_eq!(
        candidate["claim"],
        "평문 텔레그램 메시지는 기본 채팅으로 답한다"
    );
    assert_eq!(candidate["signal_kind"], "explicit_preference");
    assert_eq!(candidate["origin"], "operator_explicit");
    assert_eq!(candidate["occurrence_count"], 1);
    assert!(candidate["evidence_refs"]
        .as_array()
        .expect("evidence refs")
        .iter()
        .any(|item| item == "telegram:message:779"));

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 611);
    assert!(state["last_interaction_context_by_chat"].is_null());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_remember_preserves_corrupt_wiki_state() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let candidates_path = profile_dir(temp.path()).join("adaptive_wiki_candidates.json");
    fs::create_dir_all(candidates_path.parent().expect("profile directory"))?;
    let corrupt_state = b"{\"candidates\":";
    fs::write(&candidates_path, corrupt_state)?;
    let update_path = temp.path().join("remember_update.json");
    write_text_update(
        &update_path,
        611,
        780,
        "/remember 기존 위키를 덮어쓰지 않는다",
    )?;
    let out = temp.path().join("remember_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(temp.path().join("state.json"))
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["wiki_candidate_recorded"], false);
    assert_eq!(result["wiki_candidate_status"], "failed");
    assert!(result["wiki_candidate_error"]
        .as_str()
        .expect("wiki error")
        .contains("was not reset"));
    assert_eq!(fs::read(&candidates_path)?, corrupt_state);
    assert!(!candidates_path.with_extension("json.corrupt").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_concurrent_remember_writers_preserve_candidates() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let writers = 8;
    let mut children = Vec::new();
    for index in 0..writers {
        let update_path = temp.path().join(format!("remember_update_{index}.json"));
        write_text_update(
            &update_path,
            10_000 + index,
            20_000 + index,
            &format!("/remember concurrent wiki candidate {index}"),
        )?;
        let mut command = remote_operator_command(temp.path());
        command
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&update_path)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(temp.path().join(format!("state_{index}.json")))
            .arg("--update-journal-file")
            .arg(temp.path().join(format!("journal_{index}.jsonl")))
            .arg("--reply-outbox-file")
            .arg(temp.path().join(format!("reply_outbox_{index}.jsonl")))
            .arg("--conversation-file")
            .arg(temp.path().join(format!("conversation_{index}.jsonl")))
            .arg("--out")
            .arg(temp.path().join(format!("result_{index}.json")))
            .stdout(Stdio::null())
            .stderr(Stdio::piped());
        children.push(command.spawn()?);
    }
    for child in children {
        let output = child.wait_with_output()?;
        assert!(
            output.status.success(),
            "stderr:\n{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }

    let candidates_path = profile_dir(temp.path()).join("adaptive_wiki_candidates.json");
    let state: Value = serde_json::from_slice(&fs::read(candidates_path)?)?;
    let candidates = state["candidates"].as_array().expect("candidate list");
    assert_eq!(candidates.len(), writers as usize);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_attention_summarizes_waiting_work() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    // A task the surface flags for review counts toward the summary too.
    let tasks = json!([{
        "task_id": "task-stuck",
        "request_id": "req-1",
        "project_key": "project",
        "status": "resume_pending",
        "capability_id": "web.visual_review",
        "runner_kind": "local_background",
        "command": "echo hi",
        "workdir": "/tmp",
        "attempt_count": 1,
        "created_at": "2026-07-17T00:00:00Z",
        "updated_at": "2026-07-17T00:00:00Z"
    }]);
    fs::write(
        profile.join("offdesk_tasks.json"),
        serde_json::to_string(&tasks)?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let update = temp.path().join("attention_update.json");
    write_text_update(&update, 900, 1, "/attention")?;
    let out = temp.path().join("attention_out.json");
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update)
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
    assert_eq!(result["parsed_command"]["command"], "attention");
    let preview = result["message_preview"].as_str().expect("preview");
    assert!(
        preview.contains("프로젝트 검토·작업 요약"),
        "unexpected attention preview: {preview}"
    );
    // One decision and one review-flagged task are aggregated.
    assert!(preview.contains("결정 1"));
    assert!(preview.contains("실행 복구 1"));
    // The most urgent action remains visible as a typed command. Passive
    // summary cards must not install a persistent reply keyboard.
    assert!(preview.contains("/decision decision-user"));
    assert_eq!(result["reply_markup_preview"], Value::Null);
    assert_eq!(result["choice_surface_contract"]["surface_kind"], "none");
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_completed_review_lookup_is_scoped_and_read_only() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    fs::write(
        profile
            .parent()
            .expect("profiles directory")
            .parent()
            .expect("app directory")
            .join(".schema_version"),
        "11",
    )?;
    let path = profile.join("offdesk_tasks.json");
    fs::write(
        &path,
        serde_json::to_vec(&json!([{
            "task_id": "review-completed", "request_id": "run-review",
            "project_key": "project-a", "status": "completed",
            "capability_id": "web.visual_review", "runner_kind": "local_background",
            "command": "echo never-dispatch-this-fixture", "workdir": temp.path(),
            "attempt_count": 1, "created_at": "2026-11-01T00:00:00Z",
            "updated_at": "2026-11-01T00:00:00Z"
        }]))?,
    )?;
    let before = fs::read(&path)?;
    for (index, project, expected) in [
        (0, "project-a", "실행 완료"),
        (1, "project-b", "선택한 작업을 찾지 못했습니다"),
    ] {
        let update = temp.path().join(format!("lookup-{index}.json"));
        let out = temp.path().join(format!("result-{index}.json"));
        write_text_update(
            &update,
            920 + index,
            1 + index,
            &format!("/tasks {project} review-completed run-review"),
        )?;
        let result = remote_operator_command(temp.path())
            .args(["--dry-run", "--once", "--replay-update-file"])
            .arg(&update)
            .arg("--forager-bin")
            .arg(env!("CARGO_BIN_EXE_forager"))
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(temp.path().join(format!("state-{index}.json")))
            .arg("--out")
            .arg(&out)
            .output()?;
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let result: Value = serde_json::from_slice(&fs::read(out)?)?;
        let message = result["message_preview"].as_str().expect("lookup preview");
        assert!(message.contains(expected), "{message}");
        assert!(!message.contains("/cancel-task"));
        assert_eq!(result["mutation_authorized"], false);
        assert_eq!(result["approval_authorized"], false);
        assert!(result["dispatch_result"].is_null());
        assert_mobile_contract(&result);
        assert_eq!(fs::read(&path)?, before);
    }
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_inline_confirm_applies_only_its_pending_token() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // The confirm card offers 확인/취소 as one-tap buttons.
    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        930,
        1,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let keyboard = decision_result["reply_markup_preview"]["inline_keyboard"]
        .as_array()
        .expect("inline keyboard");
    let confirm_button = &keyboard[0][0];
    assert_eq!(confirm_button["text"], "확인");
    let callback_data = confirm_button["callback_data"]
        .as_str()
        .expect("confirmation callback data");
    assert!(callback_data.starts_with("fg1:x:c:"));

    // The callback carries the exact single-use token. The adapter resolves it
    // through the same /confirm path as a typed command.
    let confirm_update = temp.path().join("confirm_update.json");
    write_callback_update(&confirm_update, 931, 2, callback_data)?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["parsed_command"]["command"], "confirm");
    assert_eq!(
        confirm_result["parsed_command"]["confirm_token"],
        callback_data.trim_start_matches("fg1:x:c:")
    );
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["stage"], "applied");
    assert_eq!(confirm_result["read_only"], false);
    assert_eq!(confirm_result["mutation_authorized"], true);
    assert_eq!(confirm_result["approval_resolution_authorized"], true);
    assert_eq!(confirm_result["approval_authorized"], false);
    assert_eq!(confirm_result["effect"]["action"], "revise");
    assert_eq!(confirm_result["effect"]["status"], "applied");
    Ok(())
}

fn seed_pending_decision(profile: &Path) -> Result<()> {
    fs::create_dir_all(profile)?;
    let decision = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-user",
        "project_key": "project",
        "request_id": "request",
        "task_id": "approval-task",
        "raised_by": "agent",
        "source_surface": "offdesk.council",
        "materiality": "high",
        "status": "user_pending",
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
        "decision_request": {
            "kind": "council_escalation",
            "summary": "Council recommends revising the next episode.",
            "decision_needed": "Choose whether to continue, revise, block, or stop.",
            "why_now": ["Council did not return continue."],
            "current_scope": "Next episode only.",
            "non_authorized_scope": ["provider retargeting"],
            "options": [
                {"id": "revise", "label": "Revise", "description": "Ask the agent to revise the plan."},
                {"id": "block", "label": "Block", "description": "Keep the run blocked."}
            ]
        },
        "route": {
            "materiality": "high",
            "target": "user",
            "reason": "The next episode direction changes.",
            "default_if_no_reply": "defer"
        },
        "approval_brief": {
            "schema": "approval_brief.v1",
            "recommendation": "revise",
            "subject": "council continuation decision",
            "summary_lines": ["Council recommends revising before continuing."],
            "scope": "Only approves the next episode direction.",
            "question": "How should the run proceed?"
        }
    });
    fs::write(
        profile.join("offdesk_decisions.jsonl"),
        format!("{}\n", serde_json::to_string(&decision)?),
    )?;
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_pause_is_immediate_and_resume_is_confirmed() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // /pause is immediate: one message halts all new dispatch, no confirm step.
    let pause_update = temp.path().join("pause_update.json");
    write_text_update(&pause_update, 900, 1, "/pause 에이전트 오작동")?;
    let pause_out = temp.path().join("pause_out.json");
    let pause_result = replay(&pause_update, &pause_out)?;
    assert_eq!(pause_result["parsed_command"]["command"], "pause");
    assert_eq!(pause_result["dispatch_result"]["ok"], true);
    assert_eq!(pause_result["dispatch_result"]["paused"], true);
    assert_eq!(
        pause_result["remote_command_envelope"]["schema"],
        "remote_command_envelope.v1"
    );
    assert_eq!(pause_result["remote_command_envelope"]["valid"], true);
    assert_eq!(
        pause_result["remote_command_envelope"]["command_class"],
        "guarded_control"
    );
    assert!(pause_result["remote_command_envelope"]["nonce"]
        .as_str()
        .expect("command nonce")
        .starts_with("tg-"));
    assert_eq!(
        pause_result["effect"]["command_envelope_id"],
        pause_result["remote_command_envelope"]["envelope_id"]
    );
    assert!(pause_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("신규 실행 일시정지"));
    assert_mobile_contract(&pause_result);
    // The pause state is persisted so the tick will hold new dispatch.
    let pause_state: Value =
        serde_json::from_slice(&fs::read(profile.join("offdesk_operator_pause.json"))?)?;
    assert_eq!(pause_state["paused"], true);
    let conversation_row = last_conversation_row(temp.path())?;
    assert_eq!(
        conversation_row["remote_command_envelope"]["envelope_id"],
        pause_result["remote_command_envelope"]["envelope_id"]
    );

    // /resume is confirm-gated: it returns a confirmation card first.
    let resume_update = temp.path().join("resume_update.json");
    write_text_update(&resume_update, 901, 2, "/resume")?;
    let resume_out = temp.path().join("resume_out.json");
    let resume_result = replay(&resume_update, &resume_out)?;
    assert_eq!(resume_result["parsed_command"]["command"], "resume");
    assert!(resume_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("신규 실행 재개 확인"));
    assert_mobile_contract(&resume_result);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = resume_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let token = state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();

    // /confirm clears the pause.
    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 902, 3, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["kind"], "resume");
    assert_eq!(confirm_result["dispatch_result"]["paused"], false);
    assert_mobile_contract(&confirm_result);
    let resumed_state: Value =
        serde_json::from_slice(&fs::read(profile.join("offdesk_operator_pause.json"))?)?;
    assert_eq!(resumed_state["paused"], false);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_owner_rotation_invalidates_pending_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let pause_update = temp.path().join("rotation_pause.json");
    write_text_update(&pause_update, 920, 20, "/pause rotate owner")?;
    let pause_out = temp.path().join("rotation_pause_out.json");
    let pause_result = replay(&pause_update, &pause_out)?;
    assert_eq!(pause_result["dispatch_result"]["paused"], true);

    let resume_update = temp.path().join("rotation_resume.json");
    write_text_update(&resume_update, 921, 21, "/resume")?;
    let resume_out = temp.path().join("rotation_resume_out.json");
    let resume_result = replay(&resume_update, &resume_out)?;
    let old_chat_hash = resume_result["target_chat_id_hash"]
        .as_str()
        .expect("old owner chat hash")
        .to_string();
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][&old_chat_hash]["token"]
        .as_str()
        .expect("pending resume token")
        .to_string();

    fs::write(
        &env_path,
        "TELEGRAM_BOT_TOKEN=999999:rotated-token-for-test\nTELEGRAM_OWNER_CHAT_ID=222\nTELEGRAM_OWNER_USER_ID=333\n",
    )?;
    #[cfg(unix)]
    fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600))?;

    let old_owner_confirm = temp.path().join("rotation_old_owner_confirm.json");
    write_text_update(&old_owner_confirm, 922, 22, &format!("/confirm {token}"))?;
    let old_owner_out = temp.path().join("rotation_old_owner_out.json");
    let old_owner_result = replay(&old_owner_confirm, &old_owner_out)?;
    assert_eq!(old_owner_result["status"], "ignored");
    assert_eq!(old_owner_result["reason"], "chat_not_owner");
    assert_eq!(old_owner_result["remote_command_envelope"], Value::Null);

    let new_owner_confirm = temp.path().join("rotation_new_owner_confirm.json");
    write_text_update_from(
        &new_owner_confirm,
        923,
        23,
        &format!("/confirm {token}"),
        222,
        333,
        "private",
    )?;
    let new_owner_out = temp.path().join("rotation_new_owner_out.json");
    let new_owner_result = replay(&new_owner_confirm, &new_owner_out)?;
    assert_eq!(new_owner_result["parsed_command"]["command"], "confirm");
    assert_eq!(new_owner_result["dispatch_result"], Value::Null);
    assert!(new_owner_result["message_preview"]
        .as_str()
        .expect("new owner result")
        .contains("찾을 수 없습니다"));

    let pause_state: Value =
        serde_json::from_slice(&fs::read(profile.join("offdesk_operator_pause.json"))?)?;
    assert_eq!(pause_state["paused"], true);
    let final_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(final_state["pending_dispatch_confirmations_by_chat"].is_null());
    assert_eq!(final_state["control_generation_sequence"], 2);
    assert!(final_state["control_generation_sha256"]
        .as_str()
        .is_some_and(|value| value.starts_with("sha256:") && value.len() == 71));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_bot_token_rotation_stops_listener_and_revokes_residue() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    let conversation_path = temp.path().join("conversation.jsonl");
    let lock_path = temp.path().join("listener.lock");
    let loop_status_path = temp.path().join("loop_status.json");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
            .arg("--forager-bin")
            .arg(env!("CARGO_BIN_EXE_forager"))
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--update-journal-file")
            .arg(&journal_path)
            .arg("--reply-outbox-file")
            .arg(&outbox_path)
            .arg("--conversation-file")
            .arg(&conversation_path)
            .arg("--out")
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let pause_update = temp.path().join("s8_pause.json");
    write_text_update(&pause_update, 9_260, 660, "/pause s8 token rotation")?;
    replay(&pause_update, &temp.path().join("s8_pause_out.json"))?;
    let resume_update = temp.path().join("s8_resume.json");
    write_text_update(&resume_update, 9_261, 661, "/resume")?;
    let resume = replay(&resume_update, &temp.path().join("s8_resume_out.json"))?;
    let chat_hash = resume["target_chat_id_hash"]
        .as_str()
        .expect("owner chat hash")
        .to_string();
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let old_generation = state["control_generation_sha256"]
        .as_str()
        .expect("initial control generation")
        .to_string();
    let pending_token = state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("pending resume token")
        .to_string();

    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    listener.set_nonblocking(true)?;
    let rotated_env =
        "TELEGRAM_BOT_TOKEN=999999:rotated-s8-token\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\n"
            .to_string();
    let server_env_path = env_path.clone();
    let server_journal_path = journal_path.clone();
    let server_outbox_path = outbox_path.clone();
    let server_generation = old_generation.clone();
    let server_token = pending_token.clone();
    let server = thread::spawn(move || -> Result<Vec<String>> {
        let deadline = Instant::now() + Duration::from_secs(10);
        let (mut stream, _) = loop {
            match listener.accept() {
                Ok(value) => break value,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    if Instant::now() > deadline {
                        anyhow::bail!("S8 fake Telegram server timed out");
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(error.into()),
            }
        };
        let (request_line, _body) = read_http_request(&mut stream)?;
        let residue_update_id = 9_262;
        let mut journal = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&server_journal_path)?;
        for row in [
            json!({
                "schema": "remote_operator_telegram_update_journal.v3",
                "recorded_at": "2026-09-01T00:00:00+00:00",
                "status": "started",
                "update_id": residue_update_id,
                "input_hash": "sha256:s8-residue",
                "chat_id_hash": "sha256:s8-chat",
                "user_id_hash": "sha256:s8-user"
            }),
            json!({
                "schema": "remote_operator_telegram_update_journal.v3",
                "recorded_at": "2026-09-01T00:00:01+00:00",
                "status": "effect_committed",
                "update_id": residue_update_id,
                "input_hash": "sha256:s8-residue",
                "result_status": "rendered",
                "effect": {"kind": "no_effect"},
                "delivery_id": "telegram_update:9262"
            }),
        ] {
            writeln!(journal, "{}", serde_json::to_string(&row)?)?;
        }
        journal.sync_all()?;
        let mut outbox = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&server_outbox_path)?;
        writeln!(
            outbox,
            "{}",
            serde_json::to_string(&json!({
                "schema": "remote_operator_telegram_reply_outbox.v1",
                "recorded_at": "2026-09-01T00:00:02+00:00",
                "queued_at": "2026-09-01T00:00:02+00:00",
                "status": "failed",
                "delivery_id": "telegram_update:9262",
                "update_id": residue_update_id,
                "input_hash": "sha256:s8-residue",
                "payload_hash": "sha256:s8-payload",
                "chat_id": "123456789",
                "message": "old generation reply",
                "reply_markup": null,
                "result_status": "rendered",
                "effect": {"kind": "no_effect"},
                "attempt_count": 1,
                "last_error": "simulated disconnect",
                "control_generation_sha256": server_generation
            }))?
        )?;
        outbox.sync_all()?;

        fs::write(&server_env_path, rotated_env)?;
        #[cfg(unix)]
        fs::set_permissions(&server_env_path, fs::Permissions::from_mode(0o600))?;
        write_http_json(
            &mut stream,
            json!({
                "ok": true,
                "result": [{
                    "update_id": 9_263,
                    "message": {
                        "message_id": 663,
                        "date": current_unix_timestamp(),
                        "chat": {"id": 123456789, "type": "private"},
                        "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
                        "text": format!("/confirm {server_token}")
                    }
                }]
            }),
        )?;

        let mut requests = vec![request_line];
        let extra_deadline = Instant::now() + Duration::from_millis(750);
        while Instant::now() < extra_deadline {
            match listener.accept() {
                Ok((mut extra, _)) => {
                    let (line, _body) = read_http_request(&mut extra)?;
                    requests.push(line);
                    write_http_json(&mut extra, json!({"ok": true, "result": []}))?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => return Err(error.into()),
            }
        }
        Ok(requests)
    });

    let base_url = format!("http://127.0.0.1:{port}");
    let listener_output = remote_operator_command(temp.path())
        .arg("--max-polls")
        .arg("3")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--conversation-file")
        .arg(&conversation_path)
        .arg("--listener-lock-file")
        .arg(&lock_path)
        .arg("--loop-status-file")
        .arg(&loop_status_path)
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&loop_status_path)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", base_url)
        .output()?;
    assert!(
        listener_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&listener_output.stdout),
        String::from_utf8_lossy(&listener_output.stderr)
    );
    let requests = server.join().expect("S8 fake Telegram server")?;
    assert_eq!(
        requests.len(),
        1,
        "old listener made a post-rotation request"
    );
    assert!(requests[0].contains("/bot999999:fake-token-for-test/getUpdates"));

    let loop_status: Value = serde_json::from_slice(&fs::read(&loop_status_path)?)?;
    assert_eq!(loop_status["status"], "control_revoked");
    assert_eq!(
        loop_status["control_revocation"]["reason"],
        "telegram_control_generation_changed"
    );
    assert_eq!(
        loop_status["control_revocation"]["cleanup"]["revoked_reply_count"],
        1
    );
    assert_eq!(
        loop_status["control_revocation"]["cleanup"]["cleared_state"]
            ["pending_dispatch_confirmations_by_chat"],
        1
    );

    let rotated_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(rotated_state["control_generation_sequence"], 2);
    assert_eq!(rotated_state["offset"], 0);
    assert!(rotated_state["pending_dispatch_confirmations_by_chat"].is_null());
    assert_ne!(
        rotated_state["control_generation_sha256"],
        Value::String(old_generation)
    );
    let outbox_text = fs::read_to_string(&outbox_path)?;
    assert!(outbox_text.lines().last().is_some_and(|line| {
        line.contains("\"status\": \"revoked\"") && line.contains("control_generation_changed")
    }));
    let journal_text = fs::read_to_string(&journal_path)?;
    assert!(journal_text.lines().last().is_some_and(|line| {
        line.contains("\"status\": \"completed\"") && line.contains("\"send_status\": \"revoked\"")
    }));
    let pause_state: Value =
        serde_json::from_slice(&fs::read(profile.join("offdesk_operator_pause.json"))?)?;
    assert_eq!(pause_state["paused"], true);

    let telegram_script = script_path("offdesk_remote_operator_telegram.py");
    let script_dir = telegram_script.parent().expect("script parent");
    let reacquire = Command::new("python3")
        .arg("-c")
        .arg(
            "import os,pathlib,sys; sys.path.insert(0,sys.argv[1]); \
             from offdesk_remote_operator_telegram import acquire_listener_lock; \
             descriptor=acquire_listener_lock(pathlib.Path(sys.argv[2])); os.close(descriptor)",
        )
        .arg(script_dir)
        .arg(&lock_path)
        .output()?;
    assert!(reacquire.status.success(), "listener lock was not released");
    #[cfg(unix)]
    assert_eq!(
        fs::metadata(&lock_path)?.permissions().mode() & 0o777,
        0o600
    );

    let stale_confirm = temp.path().join("s8_stale_confirm.json");
    write_text_update(
        &stale_confirm,
        9_264,
        664,
        &format!("/confirm {pending_token}"),
    )?;
    let stale_result = replay(&stale_confirm, &temp.path().join("s8_stale_out.json"))?;
    assert_eq!(stale_result["parsed_command"]["command"], "confirm");
    assert_eq!(stale_result["dispatch_result"], Value::Null);
    let final_pause_state: Value =
        serde_json::from_slice(&fs::read(profile.join("offdesk_operator_pause.json"))?)?;
    assert_eq!(final_pause_state["paused"], true);

    let residue = format!(
        "{}\n{}\n{}\n{}",
        fs::read_to_string(&state_path)?,
        fs::read_to_string(&loop_status_path)?,
        fs::read_to_string(&journal_path)?,
        fs::read_to_string(&outbox_path)?,
    );
    assert!(!residue.contains("fake-token-for-test"));
    assert!(!residue.contains("rotated-s8-token"));
    assert!(fs::read_to_string(&env_path)?.contains("rotated-s8-token"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_stale_mutation_is_refused_before_handler() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let conversation_path = temp.path().join("conversation.jsonl");
    let update_path = temp.path().join("stale_pause.json");
    write_text_update_at(
        &update_path,
        903,
        4,
        "/pause stale command",
        current_unix_timestamp() - 120,
    )?;
    let out = temp.path().join("stale_pause_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--remote-command-ttl-sec")
        .arg("60")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--conversation-file")
        .arg(&conversation_path)
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
    assert_eq!(result["status"], "command_refused");
    assert_eq!(result["reason"], "command_expired");
    assert_eq!(result["remote_command_envelope"]["valid"], false);
    assert_eq!(
        result["remote_command_envelope"]["validation_status"],
        "refused"
    );
    assert_eq!(result["effect"]["status"], "refused");
    assert_eq!(result["effect"]["authorized"], false);
    assert_eq!(result["authority_domain"], "telegram_command_ingress");
    assert!(!profile_dir(temp.path())
        .join("offdesk_operator_pause.json")
        .exists());
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 904);

    let completed: Value = serde_json::from_str(
        fs::read_to_string(&journal_path)?
            .lines()
            .last()
            .expect("completed journal row"),
    )?;
    assert_eq!(completed["status"], "completed");
    assert_eq!(
        completed["remote_command_envelope"]["envelope_id"],
        result["remote_command_envelope"]["envelope_id"]
    );
    let inspect_out = temp.path().join("journal_inspection.json");
    let inspect = remote_operator_command(temp.path())
        .arg("--journal-inspect")
        .arg("--journal-update-id")
        .arg("903")
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(temp.path().join("reply_outbox.jsonl"))
        .arg("--out")
        .arg(&inspect_out)
        .output()?;
    assert!(inspect.status.success());
    let inspection: Value = serde_json::from_slice(&fs::read(&inspect_out)?)?;
    assert_eq!(
        inspection["latest_records"][0]["remote_command_envelope"]["envelope_id"],
        result["remote_command_envelope"]["envelope_id"]
    );
    let conversation: Value = serde_json::from_str(
        fs::read_to_string(&conversation_path)?
            .lines()
            .last()
            .expect("conversation row"),
    )?;
    assert_eq!(
        conversation["remote_command_envelope"]["validation_status"],
        "refused"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_stale_read_only_command_remains_available() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("stale_status.json");
    write_text_update_at(
        &update_path,
        904,
        5,
        "/status",
        current_unix_timestamp() - 120,
    )?;
    let out = temp.path().join("stale_status_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--remote-command-ttl-sec")
        .arg("60")
        .arg("--replay-update-file")
        .arg(&update_path)
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
    assert_eq!(result["parsed_command"]["command"], "status");
    assert_eq!(result["remote_command_envelope"]["valid"], true);
    assert_eq!(result["remote_command_envelope"]["fresh"], false);
    assert_eq!(
        result["remote_command_envelope"]["requires_freshness"],
        false
    );
    assert_eq!(
        result["remote_command_envelope"]["freshness_issues"][0],
        "command_expired"
    );
    assert_eq!(result["effect"]["command_envelope_status"], "accepted");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_receipt_inspector_is_read_only_redacted_and_fail_closed() -> Result<()> {
    let temp = tempdir()?;
    let artifact_root = temp.path().join("plan_sessions");
    let session_id = "telegram-plan-0123456789ab";
    let session_dir = artifact_root.join(session_id);
    fs::create_dir_all(&session_dir)?;
    let secret_path = "/private/worktree/token=do-not-expose";
    fs::write(
        session_dir.join("PLAN_DRAFT_VALIDATION.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "telegram_remote_plan_draft.v1",
            "session_id": session_id,
            "created_at": "2026-08-18T00:00:00+00:00",
            "status": "validated",
            "project_key": "Harness",
            "plan_id": "plan-safe",
            "plan_artifact_path": secret_path,
            "validation_command": ["forager", "offdesk", "plan", secret_path],
            "execution_authorized": false,
            "runtime_authorized": false
        }))?,
    )?;
    fs::write(
        session_dir.join("PLAN_GATE_REQUEST.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "telegram_remote_plan_gate_request.v1",
            "session_id": session_id,
            "created_at": "2026-08-18T00:01:00+00:00",
            "status": "pending",
            "project_key": "Harness",
            "request_id": "request-safe",
            "task_id": "task-safe",
            "observed_hash": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            "observed_hash_matched": true,
            "approval_authorized": false,
            "execution_authorized": false,
            "secret_note": "sk-do-not-expose-abcdefghijkl",
            "secret_authorized": true,
            "secret_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        }))?,
    )?;
    fs::write(
        session_dir.join("PLAN_DRAFT_SELECTION.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "telegram_remote_plan_selection.v1",
            "session_id": session_id,
            "created_at": "2026-08-18T00:00:30+00:00",
            "status": "selected",
            "project_key": "Harness",
            "strategy_key": "balanced",
            "plan_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "comparison_sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            "registration_authorized": false,
            "plan_review_authorized": false,
            "execution_authorized": false,
            "runtime_authorized": false
        }))?,
    )?;
    fs::write(
        session_dir.join("PLAN_REGISTRATION.json"),
        "{not valid json\n",
    )?;
    fs::write(
        session_dir.join("PLAN_REVIEW.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "telegram_remote_plan_review.v1",
            "session_id": session_id,
            "created_at": "2026-08-18T00:02:00+00:00",
            "status": "revision_required",
            "decision": "revision_required",
            "observed_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "observed_hash_matched": true,
            "plan_review_authorized": true,
            "launch_preparation_authorized": false
        }))?,
    )?;
    fs::write(
        session_dir.join("PROJECT_INIT_RUN.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "telegram_remote_project_init_run.v1",
            "session_id": "telegram-plan-ffffffffffff",
            "created_at": "2026-08-18T00:03:00+00:00",
            "status": "created"
        }))?,
    )?;
    let original_draft = fs::read(session_dir.join("PLAN_DRAFT_VALIDATION.json"))?;
    let original_gate = fs::read(session_dir.join("PLAN_GATE_REQUEST.json"))?;
    let original_invalid = fs::read(session_dir.join("PLAN_REGISTRATION.json"))?;
    let out = temp.path().join("receipt_inspection.json");

    let output = remote_operator_command(temp.path())
        .arg("--receipt-inspect")
        .arg("--receipt-session-id")
        .arg(session_id)
        .arg("--remote-plan-artifact-dir")
        .arg(&artifact_root)
        .arg("--update-journal-file")
        .arg(temp.path().join("updates.jsonl"))
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
    assert_eq!(result["schema"], "remote_operator_receipt_inspection.v1");
    assert_eq!(result["status"], "invalid");
    assert_eq!(result["session_id"], session_id);
    assert_eq!(result["available_receipt_count"], 4);
    assert_eq!(result["invalid_receipt_count"], 2);
    assert_eq!(result["read_only"], true);
    assert_eq!(result["mutation_authorized"], false);
    assert_eq!(result["approval_authorized"], false);
    assert_eq!(result["execution_authorized"], false);
    assert_eq!(result["receipt_chain"][2]["artifact_status"], "available");
    assert_eq!(
        result["receipt_chain"][4]["issues"][0],
        "artifact_invalid_json"
    );
    assert_eq!(
        result["receipt_chain"][1]["issues"][0],
        "artifact_session_mismatch"
    );
    assert_eq!(
        result["receipt_chain"][5]["receipt"]["hashes"]["observed_hash"],
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    );
    assert_eq!(
        result["receipt_chain"][5]["receipt"]["integrity"]["observed_hash_matched"],
        true
    );
    assert_eq!(
        result["receipt_chain"][5]["receipt"]["authority"]["plan_review_authorized"],
        true
    );
    assert_eq!(
        result["receipt_chain"][3]["receipt"]["state"]["strategy_key"],
        "balanced"
    );
    assert_eq!(
        result["receipt_chain"][3]["receipt"]["authority"]["registration_authorized"],
        false
    );
    assert_eq!(
        result["receipt_chain"][3]["receipt"]["hashes"]["comparison_sha256"],
        "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    );
    assert_eq!(
        result["receipt_chain"][7]["receipt"]["identifiers"]["task_id"],
        "task-safe"
    );
    assert_eq!(
        result["receipt_chain"][7]["receipt"]["hashes"]["observed_hash"],
        "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
    );
    assert_eq!(
        result["receipt_chain"][7]["receipt"]["integrity"]["observed_hash_matched"],
        true
    );
    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains(secret_path));
    assert!(!serialized.contains("sk-do-not-expose"));
    assert!(!serialized.contains("secret_note"));
    assert!(!serialized.contains("secret_authorized"));
    assert!(!serialized.contains("secret_sha256"));
    assert_eq!(
        fs::read(session_dir.join("PLAN_DRAFT_VALIDATION.json"))?,
        original_draft
    );
    assert_eq!(
        fs::read(session_dir.join("PLAN_GATE_REQUEST.json"))?,
        original_gate
    );
    assert_eq!(
        fs::read(session_dir.join("PLAN_REGISTRATION.json"))?,
        original_invalid
    );
    let closed_session_id = "telegram-plan-cccccccccccc";
    let closed_session_dir = artifact_root.join(closed_session_id);
    fs::create_dir_all(&closed_session_dir)?;
    for (filename, schema, status) in [
        (
            "PLAN_DRAFT_VALIDATION.json",
            "telegram_remote_plan_draft.v1",
            "validated",
        ),
        (
            "PLAN_REGISTRATION.json",
            "telegram_remote_plan_registration.v1",
            "registered",
        ),
    ] {
        fs::write(
            closed_session_dir.join(filename),
            serde_json::to_vec_pretty(&json!({
                "schema": schema,
                "session_id": closed_session_id,
                "created_at": "2026-08-18T00:00:00+00:00",
                "status": status
            }))?,
        )?;
    }
    fs::write(
        closed_session_dir.join("PLAN_REVIEW.json"),
        serde_json::to_vec_pretty(&json!({
            "schema": "telegram_remote_plan_review.v1",
            "session_id": closed_session_id,
            "created_at": "2026-08-18T00:02:00+00:00",
            "status": "rejected",
            "decision": "rejected",
            "observed_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            "observed_hash_matched": true,
            "plan_review_authorized": true,
            "launch_preparation_authorized": false
        }))?,
    )?;
    let closed_out = temp.path().join("closed_receipt_inspection.json");
    let closed_output = remote_operator_command(temp.path())
        .arg("--receipt-inspect")
        .arg("--receipt-session-id")
        .arg(closed_session_id)
        .arg("--remote-plan-artifact-dir")
        .arg(&artifact_root)
        .arg("--update-journal-file")
        .arg(temp.path().join("updates.jsonl"))
        .arg("--out")
        .arg(&closed_out)
        .output()?;
    assert!(
        closed_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&closed_output.stdout),
        String::from_utf8_lossy(&closed_output.stderr)
    );
    let closed: Value = serde_json::from_slice(&fs::read(&closed_out)?)?;
    assert_eq!(closed["status"], "review_closed");
    assert_eq!(closed["latest_available_stage"], "plan_review");
    assert_eq!(closed["available_receipt_count"], 3);
    assert_eq!(closed["invalid_receipt_count"], 0);
    assert_eq!(
        closed["receipt_chain"][5]["receipt"]["state"]["decision"],
        "rejected"
    );
    #[cfg(unix)]
    {
        let linked_session_id = "telegram-plan-bbbbbbbbbbbb";
        std::os::unix::fs::symlink(&session_dir, artifact_root.join(linked_session_id))?;
        let linked_out = temp.path().join("linked_receipt_inspection.json");
        let linked_output = remote_operator_command(temp.path())
            .arg("--receipt-inspect")
            .arg("--receipt-session-id")
            .arg(linked_session_id)
            .arg("--remote-plan-artifact-dir")
            .arg(&artifact_root)
            .arg("--update-journal-file")
            .arg(temp.path().join("updates.jsonl"))
            .arg("--out")
            .arg(&linked_out)
            .output()?;
        assert!(
            linked_output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&linked_output.stdout),
            String::from_utf8_lossy(&linked_output.stderr)
        );
        let linked: Value = serde_json::from_slice(&fs::read(&linked_out)?)?;
        assert_eq!(linked["status"], "invalid");
        assert_eq!(linked["session_issues"][0], "receipt_session_symlink");
        assert_eq!(linked["receipt_chain"], json!([]));
    }
    Ok(())
}

#[test]
#[serial]
fn remote_plan_comparison_and_selection_hashes_fail_closed() -> Result<()> {
    let temp = tempdir()?;
    let driver = r#"
import hashlib
import json
import pathlib
import sys
from types import SimpleNamespace

from telegram_operator.plan_workflow import register_plan_draft
from telegram_operator.receipts import select_plan_draft

root = pathlib.Path(sys.argv[1])
artifact_root = root / "plan_sessions"
session = {
    "session_id": "telegram-plan-abcdef123456",
    "profile": "default",
}
session_dir = artifact_root / session["session_id"]
session_dir.mkdir(parents=True)
plan_path = session_dir / "OFFDESK_PLAN_DRAFT_BALANCED.json"
plan_path.write_text("{}\n", encoding="utf-8")
plan_sha256 = hashlib.sha256(plan_path.read_bytes()).hexdigest()
candidate = {
    "schema": "telegram_remote_plan_draft.v1",
    "session_id": session["session_id"],
    "project_key": "project",
    "strategy_key": "balanced",
    "strategy_label": "균형",
    "comparison_rank": 1,
    "recommended": True,
    "status": "validated",
    "plan_artifact_path": str(plan_path),
    "plan_sha256": plan_sha256,
    "validation_command": [],
    "validation_output": {},
}
comparison_path = session_dir / "PLAN_DRAFT_VALIDATION.json"
comparison_receipt = {
    "schema": "telegram_remote_plan_draft.v1",
    "comparison_schema": "telegram_remote_plan_comparison.v1",
    "session_id": session["session_id"],
    "status": "compared",
    "candidates": [candidate],
}
comparison_raw = json.dumps(
    comparison_receipt, ensure_ascii=False, indent=2
) + "\n"
comparison_path.write_text(comparison_raw, encoding="utf-8")
comparison = {
    **comparison_receipt,
    "artifact_path": str(comparison_path),
    "receipt_sha256": hashlib.sha256(comparison_raw.encode()).hexdigest(),
}
args = SimpleNamespace(
    profile="default",
    remote_plan_artifact_dir=artifact_root,
    forager_bin=sys.argv[2],
    plan_registration_timeout_sec=10,
)

comparison_path.write_text(
    json.dumps({**comparison_receipt, "tampered": True}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
stale_selection = select_plan_draft(
    args,
    session=session,
    comparison=comparison,
    candidate=candidate,
)

comparison_path.write_text(comparison_raw, encoding="utf-8")
selection = select_plan_draft(
    args,
    session=session,
    comparison=comparison,
    candidate=candidate,
)
selection_path = pathlib.Path(selection["artifact_path"])
selection_receipt = json.loads(selection_path.read_text(encoding="utf-8"))
selection_path.write_text(
    json.dumps({**selection_receipt, "tampered": True}, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
stale_registration = register_plan_draft(
    args,
    session=session,
    draft=selection,
)
print(json.dumps({
    "stale_selection": stale_selection,
    "stale_registration": stale_registration,
}))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(temp.path())
        .arg(env!("CARGO_BIN_EXE_forager"))
        .env(
            "PYTHONPATH",
            script_path("telegram_operator").parent().unwrap(),
        )
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result["stale_selection"]["status"], "stale");
    assert_eq!(result["stale_selection"]["comparison_hash_matched"], false);
    assert_eq!(result["stale_selection"]["execution_authorized"], false);
    assert_eq!(result["stale_selection"]["runtime_authorized"], false);
    assert_eq!(result["stale_registration"]["status"], "stale");
    assert_eq!(
        result["stale_registration"]["selection_hash_matched"],
        false
    );
    assert_eq!(result["stale_registration"]["execution_authorized"], false);
    assert_eq!(result["stale_registration"]["runtime_authorized"], false);
    assert!(!profile_dir(temp.path()).join("offdesk_plans").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_plan_review_revision_and_rejection_are_hash_bound_and_terminal() -> Result<()> {
    let temp = tempdir()?;
    let plan_path = temp.path().join("PLAN.json");
    fs::write(
        &plan_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "offdesk_multiturn_plan.v1",
            "profile_key": "generic",
            "decision": {
                "ready_for_operator_review": true,
                "ready_for_launch_preparation": false,
                "ready_for_enqueue": false
            },
            "execution_sequence": [{
                "id": "phase_1",
                "objective": "Review the plan without granting runtime authority."
            }],
            "authority": {
                "read_only_plan": true,
                "does_not_authorize": [
                    "enqueue", "launch", "approval", "file movement",
                    "archive", "delete", "wiki promotion", "accepted truth"
                ]
            }
        }))?,
    )?;
    let registration_output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .args([
            "offdesk",
            "plan",
            plan_path.to_str().expect("plan path"),
            "--project-key",
            "project",
            "--json",
        ])
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .output()?;
    assert!(
        registration_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&registration_output.stdout),
        String::from_utf8_lossy(&registration_output.stderr)
    );
    let registration: Value = serde_json::from_slice(&registration_output.stdout)?;
    let driver_input = temp.path().join("review_driver_input.json");
    fs::write(
        &driver_input,
        serde_json::to_vec(&json!({
            "registration": {
                "schema": "telegram_remote_plan_registration.v1",
                "status": "registered",
                "project_key": "project",
                "registration_output": registration
            }
        }))?,
    )?;
    let artifact_dir = temp.path().join("plan_sessions");
    let driver = r#"
import json
import pathlib
import sys
from types import SimpleNamespace

from telegram_operator.effects import annotate_result_effect
from telegram_operator.plan_workflow import (
    handle_remote_plan_session_input,
    plan_review_choice_labels,
)

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
args = SimpleNamespace(
    profile="default",
    forager_bin=sys.argv[2],
    remote_plan_artifact_dir=pathlib.Path(sys.argv[3]),
    plan_review_timeout_sec=20,
)
session = {
    "schema": "telegram_remote_plan_session.v1",
    "session_id": "telegram-plan-123456789abc",
    "profile": "default",
    "stage": "plan_registered",
    "selected_candidate": {
        "project_key": "project",
        "display_name": "Project",
    },
    "plan_registration": payload["registration"],
}
state = {}
labels = plan_review_choice_labels(session)
revision_label = next(label for label in labels if label.startswith("수정 요청 "))
rejected_label = next(label for label in labels if label.startswith("계획 거절 "))
unbound = handle_remote_plan_session_input(
    args, {}, state, chat_hash="chat", session=session, text="계획 승인", mode="test"
)
annotate_result_effect(unbound)
bad_token = ("0" if revision_label[-1] != "0" else "1")
stale_label = revision_label[:-1] + bad_token
stale = handle_remote_plan_session_input(
    args, {}, state, chat_hash="chat", session=session, text=stale_label, mode="test"
)
annotate_result_effect(stale)
registry_dir = pathlib.Path(
    payload["registration"]["registration_output"]["artifacts"]["registry_dir"]
)
stale_review_count = len(list(registry_dir.glob("plan_review_*.json")))

session["stage"] = "plan_registered"
session.pop("plan_review", None)
revision = handle_remote_plan_session_input(
    args, {}, state, chat_hash="chat", session=session, text=revision_label, mode="test"
)
annotate_result_effect(revision)

session["stage"] = "plan_registered"
session.pop("plan_review", None)
rejected = handle_remote_plan_session_input(
    args, {}, state, chat_hash="chat", session=session, text=rejected_label, mode="test"
)
annotate_result_effect(rejected)
print(json.dumps({
    "labels": labels,
    "unbound": unbound,
    "stale": stale,
    "stale_review_count": stale_review_count,
    "revision": revision,
    "rejected": rejected,
    "review_count": len(list(registry_dir.glob("plan_review_*.json"))),
    "launch_prep_exists": (pathlib.Path(sys.argv[3]) / session["session_id"] / "PLAN_LAUNCH_PREP.json").exists(),
}))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(&driver_input)
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg(&artifact_dir)
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .env(
            "PYTHONPATH",
            script_path("telegram_operator").parent().unwrap(),
        )
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        result["unbound"]["parsed_command"]["selection_status"],
        "plan_review_observed_hash_required"
    );
    assert_eq!(result["unbound"]["effect"]["authorized"], false);
    assert!(result["unbound"]["message_preview"]
        .as_str()
        .expect("unbound review message")
        .contains("계획 검토 갱신 필요"));
    assert_eq!(result["stale_review_count"], 0);
    assert_eq!(
        result["stale"]["parsed_command"]["selection_status"],
        "plan_review_stale"
    );
    assert_eq!(
        result["stale"]["remote_plan_session"]["plan_review"]["status"],
        "stale"
    );
    assert_eq!(
        result["stale"]["remote_plan_session"]["plan_review"]["observed_hash_matched"],
        false
    );

    for (key, decision, stage, title) in [
        (
            "revision",
            "revision_required",
            "plan_review_revision_required",
            "계획 수정 필요",
        ),
        (
            "rejected",
            "rejected",
            "plan_review_rejected",
            "계획 거절됨",
        ),
    ] {
        let current = &result[key];
        assert_eq!(current["parsed_command"]["selection_status"], stage);
        assert_eq!(current["remote_plan_session"]["stage"], stage);
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["status"],
            decision
        );
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["decision"],
            decision
        );
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["review_output"]["decision"],
            decision
        );
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["observed_hash_matched"],
            true
        );
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["launch_preparation_authorized"],
            false
        );
        assert_eq!(
            current["remote_plan_session"]["plan_review"]["execution_authorized"],
            false
        );
        assert_eq!(current["effect"]["kind"], "plan_review_resolution");
        assert_eq!(current["effect"]["action"], decision);
        assert_eq!(current["effect"]["authorized"], true);
        assert!(current["message_preview"]
            .as_str()
            .expect("message preview")
            .contains(title));
        let buttons = button_texts(current);
        assert!(!buttons.iter().any(|button| button == "실행 준비 검토"));
        assert!(buttons.iter().any(|button| button == "다시 선택"));
        assert_mobile_contract(current);
    }
    assert_eq!(result["review_count"], 2);
    assert_eq!(result["launch_prep_exists"], false);
    Ok(())
}

fn seed_running_task(profile: &Path) -> Result<()> {
    fs::create_dir_all(profile)?;
    let tasks = json!([{
        "task_id": "task-run-1",
        "request_id": "req-1",
        "project_key": "Harness",
        "status": "running",
        "capability_id": "web.visual_review",
        "runner_kind": "local_background",
        "command": "forager offdesk tick",
        "workdir": "/tmp",
        "background_ticket_id": "ticket-run-1",
        "attempt_count": 1,
        "created_at": "2026-07-17T00:00:00Z",
        "updated_at": "2026-07-17T00:00:00Z"
    }]);
    fs::write(
        profile.join("offdesk_tasks.json"),
        serde_json::to_string(&tasks)?,
    )?;
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_cancel_task_after_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_running_task(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // /tasks lists the running, cancellable task.
    let tasks_update = temp.path().join("tasks_update.json");
    write_text_update(&tasks_update, 800, 1, "/tasks")?;
    let tasks_out = temp.path().join("tasks_out.json");
    let tasks_result = replay(&tasks_update, &tasks_out)?;
    assert_eq!(tasks_result["parsed_command"]["command"], "tasks");
    assert!(tasks_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("task-run-1"));
    assert_mobile_contract(&tasks_result);

    // /cancel-task returns a confirmation card.
    let cancel_update = temp.path().join("cancel_update.json");
    write_text_update(
        &cancel_update,
        801,
        2,
        "/cancel-task task-run-1 잘못된 실행",
    )?;
    let cancel_out = temp.path().join("cancel_out.json");
    let cancel_result = replay(&cancel_update, &cancel_out)?;
    assert_eq!(cancel_result["parsed_command"]["command"], "cancel_task");
    assert!(cancel_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("작업 상태 취소 확인"));
    assert_mobile_contract(&cancel_result);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = cancel_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let token = state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();

    // /confirm cancels the task.
    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 802, 3, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["kind"], "cancel_task");
    assert_eq!(confirm_result["dispatch_result"]["changed"], true);
    assert_eq!(confirm_result["dispatch_result"]["status"], "cancelled");
    assert_mobile_contract(&confirm_result);

    // The task store reflects the cancellation.
    let stored: Value = serde_json::from_slice(&fs::read(profile.join("offdesk_tasks.json"))?)?;
    assert_eq!(stored[0]["status"], "cancelled");
    Ok(())
}

#[test]
#[serial]
#[cfg(any(target_os = "linux", target_os = "macos"))]
fn remote_operator_telegram_terminates_verified_local_runtime_after_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_running_task(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let mut child = Command::new("sh").args(["-c", "sleep 60"]).spawn()?;
    let pid = child.id();
    let identity = forager::process::process_identity(pid).expect("spawned process identity");
    let probes = json!([{
        "ticket_id": "ticket-run-1",
        "project_key": "Harness",
        "request_id": "req-1",
        "task_id": "task-run-1",
        "runner_kind": "local_background",
        "phase": "launched",
        "runtime_pid": pid,
        "runtime_identity": identity,
        "runtime_handle_alive": true
    }]);
    fs::write(
        profile.join("background_runs.json"),
        serde_json::to_string_pretty(&probes)?,
    )?;

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        if !output.status.success() {
            anyhow::bail!(
                "stdout:\n{}\nstderr:\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
        }
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let terminate_update = temp.path().join("terminate_update.json");
    write_text_update(
        &terminate_update,
        803,
        1,
        "/terminate-task task-run-1 즉시 중단",
    )?;
    let terminate_out = temp.path().join("terminate_out.json");
    let terminate_result = replay(&terminate_update, &terminate_out);
    if terminate_result.is_err() {
        let _ = child.kill();
        let _ = child.wait();
    }
    let terminate_result = terminate_result?;
    assert_eq!(
        terminate_result["parsed_command"]["command"],
        "terminate_task"
    );
    assert!(terminate_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("로컬 러너 종료 확인"));
    let chat_hash = terminate_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();

    let confirm_update = temp.path().join("terminate_confirm_update.json");
    write_text_update(&confirm_update, 804, 2, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("terminate_confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out);
    if confirm_result.is_err() {
        let _ = child.kill();
    }
    if child.try_wait()?.is_none() {
        let _ = child.kill();
    }
    let _ = child.wait();
    let confirm_result = confirm_result?;

    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["kind"], "terminate_task");
    assert_eq!(
        confirm_result["dispatch_result"]["runtime_terminated"],
        true
    );
    assert_eq!(confirm_result["dispatch_result"]["runtime_alive"], false);
    assert_eq!(confirm_result["dispatch_result"]["status"], "cancelled");
    assert_eq!(
        confirm_result["effect"]["authority_domain"],
        "offdesk_runtime_control"
    );
    assert_eq!(confirm_result["effect"]["task_id"], "task-run-1");
    assert_mobile_contract(&confirm_result);

    let tasks: Value = serde_json::from_slice(&fs::read(profile.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "cancelled");
    let probes: Value = serde_json::from_slice(&fs::read(profile.join("background_runs.json"))?)?;
    assert_eq!(probes[0]["phase"], "terminated");
    assert_eq!(probes[0]["runtime_handle_alive"], false);
    assert!(probes[0]["termination"]["terminated_by"]
        .as_str()
        .expect("termination actor")
        .starts_with("telegram:"));
    assert_eq!(probes[0]["termination"]["reason"], "즉시 중단");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_cancel_task_reports_unknown_task() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(output.status.success());
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let cancel_update = temp.path().join("cancel_update.json");
    write_text_update(&cancel_update, 810, 1, "/cancel-task task-missing")?;
    let cancel_out = temp.path().join("cancel_out.json");
    let cancel_result = replay(&cancel_update, &cancel_out)?;
    let chat_hash = cancel_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 811, 2, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    // An unknown task fails cleanly (no crash, error surfaced), not a wedge.
    assert_eq!(confirm_result["dispatch_result"]["ok"], false);
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("취소 실패"));
    assert_mobile_contract(&confirm_result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_applies_decision_after_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let list_update = temp.path().join("decisions_update.json");
    write_text_update(&list_update, 720, 900, "/decisions")?;
    let list_out = temp.path().join("decisions_result.json");
    let list_result = replay(&list_update, &list_out)?;
    assert_eq!(list_result["parsed_command"]["command"], "decisions");
    let list_preview = list_result["message_preview"].as_str().expect("preview");
    assert!(list_preview.contains("revise"));
    assert_mobile_contract(&list_result);

    // revise/block require a bounded note, so the list must not create an
    // incomplete one-tap action or install a persistent reply keyboard.
    assert_eq!(list_result["reply_markup_preview"], Value::Null);
    assert_eq!(
        list_result["choice_surface_contract"]["surface_kind"],
        "none"
    );

    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        721,
        901,
        "/decision decision-user revise 다음 에피소드를 수정",
    )?;
    let decision_out = temp.path().join("decision_result.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    assert_eq!(decision_result["parsed_command"]["command"], "decision");
    assert!(decision_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("실행 확인 필요"));
    assert_mobile_contract(&decision_result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let token = state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
        .as_str()
        .expect("confirmation token")
        .to_string();

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 722, 902, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_result.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["parsed_command"]["command"], "confirm");
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["stage"], "applied");
    assert_eq!(
        confirm_result["dispatch_result"]["execution_status"],
        "applied"
    );
    assert_eq!(confirm_result["dispatch_result"]["decision"], "revise");
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("적용됨"));
    assert_mobile_contract(&confirm_result);

    // The canonical ledgers must reflect the applied decision action.
    assert_eq!(
        fs::read_to_string(profile.join("offdesk_decisions.jsonl"))?
            .lines()
            .count(),
        3
    );
    assert_eq!(
        fs::read_to_string(profile.join("decision_action_executions.jsonl"))?
            .lines()
            .count(),
        1
    );
    assert_eq!(
        fs::read_to_string(profile.join("decision_action_closeouts.jsonl"))?
            .lines()
            .count(),
        1
    );

    // The single-use token must be gone after a successful confirm.
    let state_after: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state_after["pending_dispatch_confirmations_by_chat"][chat_hash].is_null());
    Ok(())
}

fn seed_recovery_closeout(profile: &Path) -> Result<()> {
    let closeout_dir = profile.join("offdesk_closeouts").join("completed-task");
    fs::create_dir_all(&closeout_dir)?;
    fs::write(
        closeout_dir.join("closeout_plan.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "closeout_plan.v1",
            "closeout_id": "closeout-completed-task",
            "generated_at": "2026-07-16T00:00:00Z",
            "tasks": [{
                "project_key": "project",
                "request_id": "request-completed-task",
                "task_id": "completed-task"
            }]
        }))?,
    )?;
    fs::write(
        closeout_dir.join("closeout_review_20260618T000000Z.json"),
        serde_json::to_string_pretty(&json!({
            "schema": "closeout_review.v1",
            "reviewed_at": "2026-07-16T00:01:00Z",
            "review_id": "review-followup",
            "closeout_id": "closeout-completed-task",
            "verdict": "approved",
            "applies_to_tasks": [{
                "project_key": "project",
                "request_id": "request-completed-task",
                "task_id": "completed-task"
            }],
            "closeout_receipt": {
                "schema": "closeout_receipt.v1",
                "receipt_id": "receipt-followup",
                "closeout_id": "closeout-completed-task",
                "acceptance_status": "approved_with_followups",
                "verification_status": "pending",
                "evidence_status": "present",
                "retention_review": "required",
                "wiki_promotion_state": "not_required",
                "stale_task_count": 0,
                "next_safe_action": "Resolve archive review before accepting truth.",
                "open_decisions": [{
                    "kind": "archive_review",
                    "summary": "Archive decision is still open."
                }]
            }
        }))?,
    )?;
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_validates_recovery_after_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    seed_recovery_closeout(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let list_update = temp.path().join("recovery_update.json");
    write_text_update(&list_update, 740, 940, "/recovery")?;
    let list_out = temp.path().join("recovery_result.json");
    let list_result = replay(&list_update, &list_out)?;
    assert_eq!(list_result["parsed_command"]["command"], "recovery");
    assert!(list_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("resolve_followup"));
    assert_mobile_contract(&list_result);

    let recover_update = temp.path().join("recover_update.json");
    write_text_update(
        &recover_update,
        741,
        941,
        "/recover closeout-completed-task resolve_followup 아카이브 검토",
    )?;
    let recover_out = temp.path().join("recover_out.json");
    let recover_result = replay(&recover_update, &recover_out)?;
    assert_eq!(recover_result["parsed_command"]["command"], "recover");
    assert_mobile_contract(&recover_result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = recover_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash");
    let confirmation = &state["pending_dispatch_confirmations_by_chat"][chat_hash];
    assert_eq!(confirmation["kind"], "recovery");
    let token = confirmation["token"].as_str().expect("token").to_string();

    let confirm_update = temp.path().join("recover_confirm_update.json");
    write_text_update(&confirm_update, 742, 942, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("recover_confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(
        confirm_result["dispatch_result"]["stage"],
        "recovery_validated"
    );
    assert_eq!(confirm_result["dispatch_result"]["kind"], "recovery");
    assert_eq!(
        confirm_result["dispatch_result"]["next_local_command"],
        "forager offdesk closeout-decision --closeout-id closeout-completed-task --kind archive_review --decision preserve-in-place --reason <reason> --json"
    );
    assert_eq!(
        confirm_result["dispatch_result"]["local_execution_required"],
        true
    );
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("검증됨"));
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("closeout-decision"));
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("&lt;reason&gt;"));
    assert_mobile_contract(&confirm_result);

    // Recovery validation records a receipt but must not record accepted truth.
    assert_eq!(
        fs::read_to_string(profile.join("accepted_truth_recovery_action_receipts.jsonl"))?
            .lines()
            .count(),
        1
    );
    assert!(!profile.join("accepted_truth.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_queues_runtime_task_after_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--enable-runtime-dispatch")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };
    let confirm_token = |chat_hash: &str| -> Result<String> {
        let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
        Ok(
            state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
                .as_str()
                .expect("confirmation token")
                .to_string(),
        )
    };

    // Apply the decision so it reaches "receipted" and becomes a runtime handoff.
    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        750,
        950,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let apply_update = temp.path().join("apply_update.json");
    write_text_update(
        &apply_update,
        751,
        951,
        &format!("/confirm {}", confirm_token(&chat_hash)?),
    )?;
    let apply_out = temp.path().join("apply_out.json");
    let apply_result = replay(&apply_update, &apply_out)?;
    assert_eq!(apply_result["dispatch_result"]["stage"], "applied");

    // The runtime dispatch surface should now expose the receipted closeout.
    let surface_output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .arg("--profile")
        .arg("default")
        .args(["ondesk", "workstation-surface", "--json"])
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .env_remove("FORAGER_PROFILE")
        .output()?;
    assert!(surface_output.status.success());
    let surface: Value = serde_json::from_slice(&surface_output.stdout)?;
    let closeout_id = surface["runtime_dispatch"]["items"][0]["closeout_id"]
        .as_str()
        .expect("runtime dispatch closeout id")
        .to_string();

    let dispatch_update = temp.path().join("dispatch_update.json");
    write_text_update(
        &dispatch_update,
        752,
        952,
        &format!("/dispatch {closeout_id} local-background -- echo hello-from-telegram"),
    )?;
    let dispatch_out = temp.path().join("dispatch_out.json");
    let dispatch_result = replay(&dispatch_update, &dispatch_out)?;
    assert_eq!(dispatch_result["parsed_command"]["command"], "dispatch");
    assert!(dispatch_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("런타임 디스패치 확인"));
    assert_mobile_contract(&dispatch_result);
    let pending_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(
        pending_state["pending_dispatch_confirmations_by_chat"][&chat_hash]["user_id_hash"],
        dispatch_result["operator_identity"]["user_id_hash"]
    );

    let confirm_update = temp.path().join("dispatch_confirm_update.json");
    write_text_update(
        &confirm_update,
        753,
        953,
        &format!("/confirm {}", confirm_token(&chat_hash)?),
    )?;
    let confirm_out = temp.path().join("dispatch_confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["stage"], "queued");
    assert_eq!(confirm_result["dispatch_result"]["kind"], "runtime");
    assert_eq!(confirm_result["dispatch_result"]["task_enqueued"], true);
    assert!(confirm_result["dispatch_result"]["task_id"]
        .as_str()
        .is_some());
    assert_eq!(confirm_result["operator_identity"]["role"], "owner");
    assert_eq!(
        confirm_result["effect"]["operator_identity"],
        confirm_result["operator_identity"]
    );
    assert_eq!(
        confirm_result["effect"]["task_id"],
        confirm_result["dispatch_result"]["task_id"]
    );
    assert_mobile_contract(&confirm_result);

    assert_eq!(
        fs::read_to_string(profile.join("runtime_dispatch_receipts.jsonl"))?
            .lines()
            .count(),
        1
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_run_dispatches_curated_template_without_runtime_flag() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    // Curated allowlist on the trusted local machine; the operator only names it.
    let allowlist_path = temp.path().join("dispatch_allowlist.json");
    fs::write(
        &allowlist_path,
        serde_json::to_vec(&serde_json::json!({
            "templates": [
                {
                    "name": "smoke",
                    "runner": "local-background",
                    "command": "echo hello-from-run",
                    "description": "smoke check"
                }
            ]
        }))?,
    )?;

    // Deliberately no --enable-runtime-dispatch: curated /run is the safe path.
    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--dispatch-allowlist-file")
            .arg(&allowlist_path)
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };
    let confirm_token = |chat_hash: &str| -> Result<String> {
        let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
        Ok(
            state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"]
                .as_str()
                .expect("confirmation token")
                .to_string(),
        )
    };

    // Apply the decision so it reaches "receipted" and becomes a runtime handoff.
    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        770,
        970,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let apply_update = temp.path().join("apply_update.json");
    write_text_update(
        &apply_update,
        771,
        971,
        &format!("/confirm {}", confirm_token(&chat_hash)?),
    )?;
    let apply_out = temp.path().join("apply_out.json");
    let apply_result = replay(&apply_update, &apply_out)?;
    assert_eq!(apply_result["dispatch_result"]["stage"], "applied");

    let surface_output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .arg("--profile")
        .arg("default")
        .args(["ondesk", "workstation-surface", "--json"])
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .env_remove("FORAGER_PROFILE")
        .output()?;
    assert!(surface_output.status.success());
    let surface: Value = serde_json::from_slice(&surface_output.stdout)?;
    let closeout_id = surface["runtime_dispatch"]["items"][0]["closeout_id"]
        .as_str()
        .expect("runtime dispatch closeout id")
        .to_string();

    // /run (list) surfaces the curated template by name.
    let list_update = temp.path().join("run_list_update.json");
    write_text_update(&list_update, 772, 972, "/run")?;
    let list_out = temp.path().join("run_list_out.json");
    let list_result = replay(&list_update, &list_out)?;
    assert_eq!(list_result["parsed_command"]["command"], "run_list");
    assert!(list_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("smoke"));
    assert_mobile_contract(&list_result);

    // /run <closeout> <name> builds a confirm card and stashes a confirmation.
    let run_update = temp.path().join("run_update.json");
    write_text_update(&run_update, 773, 973, &format!("/run {closeout_id} smoke"))?;
    let run_out = temp.path().join("run_out.json");
    let run_result = replay(&run_update, &run_out)?;
    assert_eq!(run_result["parsed_command"]["command"], "run");
    assert!(run_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("실행 확인"));
    assert_mobile_contract(&run_result);

    // Confirm queues the runtime task even though --enable-runtime-dispatch is off.
    let confirm_update = temp.path().join("run_confirm_update.json");
    write_text_update(
        &confirm_update,
        774,
        974,
        &format!("/confirm {}", confirm_token(&chat_hash)?),
    )?;
    let confirm_out = temp.path().join("run_confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert_eq!(confirm_result["dispatch_result"]["ok"], true);
    assert_eq!(confirm_result["dispatch_result"]["stage"], "queued");
    assert_eq!(confirm_result["dispatch_result"]["kind"], "runtime");
    assert_eq!(confirm_result["dispatch_result"]["task_enqueued"], true);
    assert_mobile_contract(&confirm_result);

    assert_eq!(
        fs::read_to_string(profile.join("runtime_dispatch_receipts.jsonl"))?
            .lines()
            .count(),
        1
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_run_refuses_unknown_and_unconfigured() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let allowlist_path = temp.path().join("dispatch_allowlist.json");
    fs::write(
        &allowlist_path,
        serde_json::to_vec(&serde_json::json!({
            "templates": [
                {"name": "smoke", "runner": "local-background", "command": "echo hi"}
            ]
        }))?,
    )?;

    let replay = |update: &Path, out: &Path, allowlist: Option<&Path>| -> Result<Value> {
        let mut command = remote_operator_command(temp.path());
        command
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out);
        if let Some(path) = allowlist {
            command.arg("--dispatch-allowlist-file").arg(path);
        }
        let output = command.output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // Unknown template name: no confirmation is stored.
    let unknown_update = temp.path().join("run_unknown_update.json");
    write_text_update(&unknown_update, 780, 980, "/run closeout-x nope")?;
    let unknown_out = temp.path().join("run_unknown_out.json");
    let unknown_result = replay(&unknown_update, &unknown_out, Some(&allowlist_path))?;
    assert_eq!(unknown_result["parsed_command"]["command"], "run");
    assert!(unknown_result["dispatch_result"].is_null());
    assert!(unknown_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("템플릿이 없습니다"));
    assert_mobile_contract(&unknown_result);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(
        state["pending_dispatch_confirmations_by_chat"].is_null()
            || state["pending_dispatch_confirmations_by_chat"]
                .as_object()
                .expect("map")
                .is_empty()
    );

    // No allowlist configured: /run is refused as not configured.
    let disabled_update = temp.path().join("run_disabled_update.json");
    write_text_update(&disabled_update, 781, 981, "/run closeout-x smoke")?;
    let disabled_out = temp.path().join("run_disabled_out.json");
    let disabled_result = replay(&disabled_update, &disabled_out, None)?;
    assert_eq!(disabled_result["parsed_command"]["command"], "run");
    assert!(disabled_result["dispatch_result"].is_null());
    assert!(disabled_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("비활성"));
    assert_mobile_contract(&disabled_result);

    assert!(!profile.join("runtime_dispatch_receipts.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_refuses_runtime_when_disabled() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    // No --enable-runtime-dispatch flag: /dispatch must be refused outright.
    let dispatch_update = temp.path().join("dispatch_update.json");
    write_text_update(
        &dispatch_update,
        760,
        960,
        "/dispatch closeout-x local-background -- rm -rf /",
    )?;
    let out = temp.path().join("dispatch_out.json");
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&dispatch_update)
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
    assert_eq!(result["parsed_command"]["command"], "dispatch");
    assert!(result["dispatch_result"].is_null());
    assert!(result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("비활성"));
    assert_mobile_contract(&result);
    // No confirmation may be stored for a refused dispatch.
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(
        state["pending_dispatch_confirmations_by_chat"].is_null()
            || state["pending_dispatch_confirmations_by_chat"]
                .as_object()
                .expect("map")
                .is_empty()
    );
    assert!(!profile.join("runtime_dispatch_receipts.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_rejects_stale_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        730,
        910,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_result.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("confirmation token")
        .to_string();

    // Mutate the decision so its observed hash no longer matches the token.
    let mut decision: Value = serde_json::from_str(
        fs::read_to_string(profile.join("offdesk_decisions.jsonl"))?
            .lines()
            .next()
            .expect("decision line"),
    )?;
    decision["updated_at"] = json!("2026-07-16T09:00:00Z");
    decision["decision_request"]["summary"] = json!("Changed direction.");
    fs::write(
        profile.join("offdesk_decisions.jsonl"),
        format!("{}\n", serde_json::to_string(&decision)?),
    )?;

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 731, 911, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_result.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert!(confirm_result["dispatch_result"].is_null());
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("변경"));
    assert_mobile_contract(&confirm_result);
    // A stale confirmation must never write an execution.
    assert!(!profile.join("decision_action_executions.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_attention_notify_pushes_then_dedupes() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--attention-notify")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // First poll: the waiting decision is proactively pushed to the owner chat.
    let first_update = temp.path().join("poll1.json");
    write_text_update(&first_update, 900, 1, "/status")?;
    let first_out = temp.path().join("poll1_out.json");
    let first = replay(&first_update, &first_out)?;
    let notification = &first["attention_notification"];
    assert_eq!(notification["status"], "notified");
    assert_eq!(notification["notified_count"], 1);
    let preview = notification["message_preview"].as_str().expect("preview");
    assert!(preview.contains("결정 대기"));
    assert!(preview.contains("/decision decision-user"));
    // The attention card is a separate send to the owner chat, mobile-scannable.
    let contract = &notification["mobile_card_contract"];
    assert!(contract["warnings"]
        .as_array()
        .expect("warnings")
        .is_empty());

    // The notified item is recorded so it is not pushed again.
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state["attention_notified_by_key"]["decision:decision-user"].is_object());

    // Second poll: the same decision is still open but must not re-notify.
    let second_update = temp.path().join("poll2.json");
    write_text_update(&second_update, 901, 2, "/status")?;
    let second_out = temp.path().join("poll2_out.json");
    let second = replay(&second_update, &second_out)?;
    assert_eq!(
        second["attention_notification"]["status"],
        "no_new_attention"
    );
    assert_eq!(second["attention_notification"]["pending_count"], 1);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_attention_failed_send_does_not_consume_dedup() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let old_notification_time = "2026-01-01T00:00:00+00:00";
    fs::write(
        &state_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "control_generation_sha256": default_test_control_generation(),
            "attention_notified_by_key": {
                "decision:decision-user": {
                    "first_notified_at": old_notification_time,
                    "last_notified_at": old_notification_time
                }
            }
        }))?,
    )?;

    // With the default 21600-second reminder, this old item is due. The first
    // delivery fails, so the staged reminder timestamp must not become durable.
    let (first_base, first_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": false, "description": "temporary attention send failure"}),
    ])?;
    let first_out = temp.path().join("attention_failed.json");
    let first = remote_operator_command(temp.path())
        .arg("--once")
        .arg("--attention-notify")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&first_out)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", first_base)
        .output()?;
    assert!(
        first.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&first.stdout),
        String::from_utf8_lossy(&first.stderr)
    );
    let first_result: Value = serde_json::from_slice(&fs::read(&first_out)?)?;
    assert_eq!(
        first_result["attention_notification"]["status"],
        "send_failed"
    );
    assert_eq!(first_result["attention_notification"]["notified_count"], 0);
    assert_eq!(first_result["attention_notification"]["attempted_count"], 1);
    let first_requests = first_server.join().expect("first fake Telegram server")?;
    assert!(first_requests[0].contains("/getUpdates"));
    assert!(first_requests[1].contains("/sendMessage"));

    let failed_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(
        failed_state["attention_notified_by_key"]["decision:decision-user"]["last_notified_at"],
        old_notification_time
    );

    // A restarted poll retries the same due item despite the default reminder
    // interval, then commits its new timestamp only after delivery succeeds.
    let (second_base, second_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": []}),
        json!({"ok": true, "result": {"message_id": 812}}),
    ])?;
    let second_out = temp.path().join("attention_retry.json");
    let second = remote_operator_command(temp.path())
        .arg("--once")
        .arg("--attention-notify")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&second_out)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", second_base)
        .output()?;
    assert!(
        second.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second.stdout),
        String::from_utf8_lossy(&second.stderr)
    );
    let second_result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(
        second_result["attention_notification"]["status"],
        "notified"
    );
    assert_eq!(second_result["attention_notification"]["notified_count"], 1);
    assert_eq!(
        second_result["attention_notification"]["send_status"],
        "sent"
    );
    let second_requests = second_server.join().expect("second fake Telegram server")?;
    assert!(second_requests[0].contains("/getUpdates"));
    assert!(second_requests[1].contains("/sendMessage"));
    let delivered_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let committed_time = delivered_state["attention_notified_by_key"]["decision:decision-user"]
        ["last_notified_at"]
        .as_str()
        .expect("committed attention timestamp");
    assert_ne!(committed_time, old_notification_time);

    // The successful send is now deduplicated by the default 21600-second
    // reminder rather than causing another immediate Telegram card.
    let (third_base, third_server) = spawn_fake_telegram(vec![json!({
        "ok": true,
        "result": []
    })])?;
    let third_out = temp.path().join("attention_deduped.json");
    let third = remote_operator_command(temp.path())
        .arg("--once")
        .arg("--attention-notify")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&third_out)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", third_base)
        .output()?;
    assert!(third.status.success());
    let third_result: Value = serde_json::from_slice(&fs::read(&third_out)?)?;
    assert_eq!(
        third_result["attention_notification"]["status"],
        "no_new_attention"
    );
    let third_requests = third_server.join().expect("third fake Telegram server")?;
    assert_eq!(third_requests.len(), 1);
    assert!(third_requests[0].contains("/getUpdates"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_attention_notify_off_by_default() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");
    let update = temp.path().join("poll.json");
    write_text_update(&update, 910, 1, "/status")?;
    let out = temp.path().join("poll_out.json");

    // Without --attention-notify no proactive scan runs.
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update)
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
    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert!(result["attention_notification"].is_null());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_confirm_with_control_char_note_does_not_wedge() -> Result<()> {
    // An operator note containing a NUL byte makes the CLI subprocess raise
    // ValueError; the confirm path must convert that into an error card and
    // still advance the poll offset instead of re-delivering the update.
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "adapter must exit cleanly (no crash)\nstdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let decision_update = temp.path().join("decision_update.json");
    let note_with_nul = format!("revise{}tail", '\u{0}');
    write_text_update(
        &decision_update,
        740,
        940,
        &format!("/decision decision-user revise {note_with_nul}"),
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 741, 941, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    // No execution recorded, an error card rendered, and the offset advanced.
    assert!(confirm_result["dispatch_result"].is_null());
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("실패"));
    let state_after: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state_after["offset"], 742);
    assert!(!profile.join("decision_action_executions.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_rejects_expired_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        770,
        970,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();

    // Age the confirmation past its TTL by rewriting created_at into the past.
    let mut state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();
    state["pending_dispatch_confirmations_by_chat"][&chat_hash]["created_at"] =
        json!("2020-01-01T00:00:00+00:00");
    fs::write(&state_path, serde_json::to_string_pretty(&state)?)?;

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 771, 971, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert!(confirm_result["dispatch_result"].is_null());
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("만료"));
    assert_mobile_contract(&confirm_result);
    assert!(!profile.join("decision_action_executions.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_cancel_clears_pending_confirmation() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let decision_update = temp.path().join("decision_update.json");
    write_text_update(
        &decision_update,
        780,
        980,
        "/decision decision-user revise 수정",
    )?;
    let decision_out = temp.path().join("decision_out.json");
    let decision_result = replay(&decision_update, &decision_out)?;
    let chat_hash = decision_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let token = state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("token")
        .to_string();
    let cancel_callback = decision_result["reply_markup_preview"]["inline_keyboard"][0][1]
        ["callback_data"]
        .as_str()
        .expect("cancel callback")
        .to_string();
    assert_eq!(cancel_callback, format!("fg1:x:d:{token}"));

    let cancel_update = temp.path().join("cancel_update.json");
    write_callback_update(&cancel_update, 781, 981, &cancel_callback)?;
    let cancel_out = temp.path().join("cancel_out.json");
    let cancel_result = replay(&cancel_update, &cancel_out)?;
    assert_eq!(cancel_result["parsed_command"]["command"], "cancel");
    assert_eq!(cancel_result["parsed_command"]["cancel_token"], token);
    assert!(cancel_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("취소"));
    assert_mobile_contract(&cancel_result);
    let cleared_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(cleared_state["pending_dispatch_confirmations_by_chat"][&chat_hash].is_null());

    // The cancelled token must no longer confirm anything.
    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 782, 982, &format!("/confirm {token}"))?;
    let confirm_out = temp.path().join("confirm_out.json");
    let confirm_result = replay(&confirm_update, &confirm_out)?;
    assert!(confirm_result["dispatch_result"].is_null());
    assert!(confirm_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("찾을 수 없습니다"));
    assert!(!profile.join("decision_action_executions.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_dispatch_new_confirmation_supersedes_old() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let profile = profile_dir(temp.path());
    seed_pending_decision(&profile)?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let first_update = temp.path().join("first_decision.json");
    write_text_update(
        &first_update,
        790,
        990,
        "/decision decision-user revise 수정",
    )?;
    let first_out = temp.path().join("first_out.json");
    let first_result = replay(&first_update, &first_out)?;
    let chat_hash = first_result["target_chat_id_hash"]
        .as_str()
        .expect("chat hash")
        .to_string();
    assert!(first_result["superseded_pending_confirmation"].is_null());
    let first_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let first_token = first_state["pending_dispatch_confirmations_by_chat"][&chat_hash]["token"]
        .as_str()
        .expect("first token")
        .to_string();

    // A second /decision replaces the pending confirmation.
    let second_update = temp.path().join("second_decision.json");
    write_text_update(
        &second_update,
        791,
        991,
        "/decision decision-user block 보류",
    )?;
    let second_out = temp.path().join("second_out.json");
    let second_result = replay(&second_update, &second_out)?;
    assert_eq!(second_result["superseded_pending_confirmation"], true);

    // The first token is now dead.
    let confirm_first = temp.path().join("confirm_first.json");
    write_text_update(&confirm_first, 792, 992, &format!("/confirm {first_token}"))?;
    let confirm_first_out = temp.path().join("confirm_first_out.json");
    let confirm_first_result = replay(&confirm_first, &confirm_first_out)?;
    assert!(confirm_first_result["dispatch_result"].is_null());
    assert!(confirm_first_result["message_preview"]
        .as_str()
        .expect("preview")
        .contains("찾을 수 없습니다"));
    assert!(!profile.join("decision_action_executions.jsonl").exists());
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
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
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
    assert_eq!(result["parsed_command"]["command"], "plan_request");
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
        result["feedback_workflow"]["schema"],
        "remote_operator_feedback_workflow.v1"
    );
    assert_eq!(result["feedback_workflow"]["command"], "plan_request");
    assert_eq!(result["feedback_workflow"]["plan_session_started"], true);
    assert_eq!(
        result["feedback_workflow"]["plan_session_id"],
        result["remote_plan_session"]["session_id"]
    );
    assert!(result["feedback_workflow"].get("feedback_text").is_none());
    assert!(result["feedback_workflow"].get("message_preview").is_none());
    assert_eq!(
        result["remote_plan_session"]["candidates"][0]["display_name"],
        "Alpha"
    );
    for candidate in result["remote_plan_session"]["candidates"]
        .as_array()
        .expect("project candidates")
    {
        for key in [
            "current_branch",
            "head_ref",
            "dirty_tree",
            "remote_sync",
            "recent_failures",
            "recent_failures_observed",
            "available_tests",
            "estimated_runtime_minutes",
            "risk_level",
            "autonomy_fit",
            "requires_network",
            "requires_secrets",
            "requires_human_context",
            "recommended_next_action",
            "blocked_reason",
            "comparison_evidence",
        ] {
            assert!(
                candidate.get(key).is_some(),
                "missing candidate field {key}"
            );
        }
    }
    assert_eq!(
        result["interaction_context"]["context_kind"],
        "remote_plan_project_selection"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 대상 선택</b>"));
    assert!(preview.contains("/select"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"1 Alpha".to_string()));
    assert!(button_texts(&result).contains(&"다시 스캔".to_string()));
    assert!(button_texts(&result).contains(&"보류".to_string()));
    assert_eq!(result["choice_surface_contract"]["surface_kind"], "inline");
    let first_callback = result["reply_markup_preview"]["inline_keyboard"][0][0]["callback_data"]
        .as_str()
        .expect("Plan callback data");
    let callback_parts = first_callback.split(':').collect::<Vec<_>>();
    assert_eq!(callback_parts.len(), 5);
    assert_eq!(&callback_parts[..2], &["fg1", "p"]);
    assert_eq!(callback_parts[2].len(), 8);
    assert_eq!(callback_parts[3].len(), 16);
    assert_eq!(callback_parts[4], "0");
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
fn remote_operator_telegram_delegation_capture_confirm_starts_plan_session() -> Result<()> {
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

    let replay = |update_path: &Path, out: &Path| -> Result<std::process::Output> {
        Ok(remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update_path)
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
            .arg(out)
            .output()?)
    };

    let delegation_update = temp.path().join("delegation_update.json");
    write_text_update(
        &delegation_update,
        710,
        890,
        "그럼 Alpha에서 마지막 기록을 확인하고 새로 방향을 검토해볼까",
    )?;
    let capture_out = temp.path().join("capture_result.json");
    let output = replay(&delegation_update, &capture_out)?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let capture: Value = serde_json::from_slice(&fs::read(&capture_out)?)?;
    assert_eq!(capture["status"], "rendered");
    assert_eq!(capture["parsed_command"]["command"], "chat");
    assert_eq!(capture["plan_capture_offered"], true);
    let capture_preview = capture["message_preview"]
        .as_str()
        .expect("capture preview");
    assert!(capture_preview.contains("<b>계획 후보로 등록할까요?</b>"));
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = capture["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["pending_dispatch_confirmations_by_chat"][chat_hash]["kind"],
        "plan_capture"
    );

    let confirm_update = temp.path().join("confirm_update.json");
    write_text_update(&confirm_update, 711, 891, "/confirm")?;
    let confirm_out = temp.path().join("confirm_result.json");
    let output = replay(&confirm_update, &confirm_out)?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let confirmed: Value = serde_json::from_slice(&fs::read(&confirm_out)?)?;
    assert_eq!(confirmed["status"], "rendered");
    assert_eq!(confirmed["feedback_recorded"], true);
    assert_eq!(confirmed["decision_feedback_ingest_status"], "recorded");
    assert_eq!(
        confirmed["feedback_workflow"]["schema"],
        "remote_operator_feedback_workflow.v1"
    );
    assert_eq!(confirmed["feedback_workflow"]["command"], "plan_request");
    assert_eq!(confirmed["feedback_workflow"]["plan_session_started"], true);
    assert_eq!(
        confirmed["remote_plan_session"]["schema"],
        "telegram_remote_plan_session.v1"
    );
    assert_eq!(
        confirmed["remote_plan_session"]["stage"],
        "project_selection"
    );
    assert_eq!(
        confirmed["remote_plan_session"]["execution_authorized"],
        false
    );
    assert_eq!(
        confirmed["feedback_workflow"]["plan_session_id"],
        confirmed["remote_plan_session"]["session_id"]
    );
    assert!(confirmed["feedback_workflow"]
        .get("feedback_text")
        .is_none());
    let confirm_preview = confirmed["message_preview"]
        .as_str()
        .expect("confirm preview");
    assert!(confirm_preview.contains("<b>계획 대상 선택</b>"));
    assert_mobile_contract(&confirmed);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    assert!(feedback_rows.contains("planning_request"));

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(
        state["pending_dispatch_confirmations_by_chat"][chat_hash],
        Value::Null
    );
    assert_eq!(
        state["remote_plan_sessions_by_chat"][chat_hash]["stage"],
        "project_selection"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_plain_text_stays_chat() -> Result<()> {
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
        705,
        885,
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_result.json");
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

    let second_update = temp.path().join("plain_chat_update.json");
    write_text_update(&second_update, 706, 886, "이 계획 상태를 설명해줘")?;
    let second_out = temp.path().join("plain_chat_result.json");
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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["feedback_recorded"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(!preview.contains("<b>계획 대상 선택됨</b>"));
    assert_mobile_contract(&result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 707);
    assert_eq!(
        state["remote_plan_sessions_by_chat"][result["target_chat_id_hash"].as_str().unwrap()]
            ["stage"],
        "project_selection"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_replay_plan_session_unmatched_short_text_stays_chat() -> Result<()> {
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
        721,
        901,
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
    )?;
    let first_out = temp.path().join("plan_result.json");
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

    // Short text with no question marker that matches no candidate, no existing
    // directory, and no workspace project name. This previously crashed session
    // routing with a NameError and poisoned the update offset.
    let second_update = temp.path().join("unmatched_short_text_update.json");
    write_text_update(&second_update, 722, 902, "Zeta")?;
    let second_out = temp.path().join("unmatched_short_text_result.json");
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
    assert_eq!(result["parsed_command"]["command"], "chat");
    assert_eq!(result["feedback_recorded"], Value::Null);
    assert_mobile_contract(&result);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 723);
    assert_eq!(
        state["remote_plan_sessions_by_chat"][result["target_chat_id_hash"].as_str().unwrap()]
            ["stage"],
        "project_selection"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_stale_plan_session_input_does_not_advance_stage() -> Result<()> {
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
    let journal_path = temp.path().join("update_journal.jsonl");

    let run_replay = |update: &Path, out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--remote-command-ttl-sec")
            .arg("60")
            .arg("--replay-update-file")
            .arg(update)
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
            .arg("--update-journal-file")
            .arg(&journal_path)
            .arg("--workspace-root")
            .arg(&workspace_root)
            .arg("--out")
            .arg(out)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    let first_update = temp.path().join("plan_update.json");
    write_text_update(
        &first_update,
        723,
        903,
        "/plan Alpha 프로젝트 계획을 준비해줘",
    )?;
    let first_out = temp.path().join("plan_result.json");
    let first = run_replay(&first_update, &first_out)?;
    assert_eq!(first["remote_plan_session"]["stage"], "project_selection");

    let selection_update = temp.path().join("stale_selection.json");
    write_text_update_at(
        &selection_update,
        724,
        904,
        "1번",
        current_unix_timestamp() - 120,
    )?;
    let selection_out = temp.path().join("stale_selection_result.json");
    let refused = run_replay(&selection_update, &selection_out)?;
    assert_eq!(refused["status"], "command_refused");
    assert_eq!(refused["reason"], "command_expired");
    assert_eq!(
        refused["remote_command_envelope"]["command"],
        "remote_plan_session_input"
    );
    assert_eq!(
        refused["remote_command_envelope"]["command_class"],
        "plan_session"
    );
    assert_eq!(
        refused["effect"]["remote_plan_session_id"],
        first["remote_plan_session"]["session_id"]
    );

    let receipts_update = temp.path().join("receipts_update.json");
    write_text_update(&receipts_update, 725, 905, "/receipts")?;
    let receipts_out = temp.path().join("receipts_result.json");
    let receipts = run_replay(&receipts_update, &receipts_out)?;
    assert_eq!(receipts["parsed_command"]["command"], "receipts");
    assert_eq!(receipts["read_only"], true);
    assert_eq!(receipts["mutation_authorized"], false);
    assert_eq!(
        receipts["remote_receipt_inspection"]["session_id"],
        first["remote_plan_session"]["session_id"]
    );
    assert_eq!(
        receipts["remote_receipt_inspection"]["command_update_count"],
        2
    );
    assert_eq!(
        receipts["remote_receipt_inspection"]["refused_command_count"],
        1
    );
    assert!(receipts["message_preview"]
        .as_str()
        .expect("receipt card")
        .contains("영수증 체인"));
    assert_mobile_contract(&receipts);

    let linked_out = temp.path().join("receipt_by_update.json");
    let linked_output = remote_operator_command(temp.path())
        .arg("--receipt-inspect")
        .arg("--journal-update-id")
        .arg("723")
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--out")
        .arg(&linked_out)
        .output()?;
    assert!(
        linked_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&linked_output.stdout),
        String::from_utf8_lossy(&linked_output.stderr)
    );
    let linked: Value = serde_json::from_slice(&fs::read(&linked_out)?)?;
    assert_eq!(linked["selection_source"], "update_link");
    assert_eq!(
        linked["session_id"],
        first["remote_plan_session"]["session_id"]
    );
    assert_eq!(linked["status"], "empty");
    assert_eq!(linked["command_update_count"], 3);
    assert_eq!(linked["read_only"], true);
    assert_eq!(linked["mutation_authorized"], false);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = first["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["remote_plan_sessions_by_chat"][chat_hash]["stage"],
        "project_selection"
    );
    assert_eq!(state["offset"], 726);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_plan_inline_selection_is_bound_to_current_card() -> Result<()> {
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
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
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
    let first: Value = serde_json::from_slice(&fs::read(&first_out)?)?;
    let callback_data = first["reply_markup_preview"]["inline_keyboard"][0][0]["callback_data"]
        .as_str()
        .expect("project selection callback")
        .to_string();
    assert!(callback_data.starts_with("fg1:p:"));

    let second_update = temp.path().join("selection_update.json");
    write_callback_update(&second_update, 711, 891, &callback_data)?;
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
    assert_eq!(result["action_outcome"]["disposition"], "observed");
    assert_eq!(result["response_card"]["control_surface"], "inline");
    assert!(result["response_card"]["action_count"]
        .as_u64()
        .is_some_and(|count| count > 0));
    assert_mobile_contract(&result);

    // Replaying the old project-selection card after the workflow advanced
    // must fail closed. It may not reinterpret button 0 in the new stage.
    let stale_update = temp.path().join("stale_selection_update.json");
    write_callback_update(&stale_update, 712, 892, &callback_data)?;
    let stale_out = temp.path().join("stale_selection_result.json");
    let stale_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&stale_update)
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
        .arg(&stale_out)
        .output()?;
    assert!(
        stale_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&stale_output.stdout),
        String::from_utf8_lossy(&stale_output.stderr)
    );
    let stale: Value = serde_json::from_slice(&fs::read(&stale_out)?)?;
    assert_eq!(stale["dispatch_result"]["ok"], false);
    assert_eq!(stale["dispatch_result"]["error"], "plan_context_changed");
    assert_eq!(stale["effect"]["status"], "refused");
    assert_eq!(stale["action_outcome"]["disposition"], "refused");
    assert_eq!(
        stale["action_outcome"]["refusal_reason"],
        "plan_context_changed"
    );
    assert_eq!(stale["response_card"]["control_surface"], "inline");
    assert_eq!(stale["remote_plan_session"]["stage"], "project_selected");
    assert_eq!(
        stale["remote_plan_session"]["selected_candidate"]["display_name"],
        "Alpha"
    );
    assert_eq!(stale["choice_surface_contract"]["surface_kind"], "inline");
    assert!(button_texts(&stale).contains(&"초기화 검토".to_string()));
    assert_mobile_contract(&stale);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 713);
    let chat_hash = first["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["remote_plan_sessions_by_chat"][chat_hash]["stage"],
        "project_selected"
    );
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
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
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
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
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
    assert_effect(&result, "artifact_write", "plan_artifact");
    assert_eq!(result["effect"]["stage"], "init_created");
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
        "plan_drafts_compared"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_drafts_compared"
    );
    let comparison = &result["remote_plan_session"]["plan_draft_comparison"];
    assert_eq!(comparison["schema"], "telegram_remote_plan_draft.v1");
    assert_eq!(
        comparison["comparison_schema"],
        "telegram_remote_plan_comparison.v1"
    );
    assert_eq!(comparison["status"], "compared");
    assert_eq!(comparison["candidate_count"], 3);
    assert_eq!(comparison["validated_candidate_count"], 3);
    assert_eq!(comparison["recommended_strategy_key"], "balanced");
    assert_eq!(comparison["execution_authorized"], false);
    assert_eq!(comparison["approval_authorized"], false);
    assert_eq!(comparison["plan_review_authorized"], false);
    assert_eq!(comparison["registration_authorized"], false);
    assert_eq!(comparison["runtime_authorized"], false);
    assert_eq!(comparison["accepted_truth_authorized"], false);
    let candidates = comparison["candidates"]
        .as_array()
        .expect("plan candidates");
    assert_eq!(candidates.len(), 3);
    for (index, candidate) in candidates.iter().enumerate() {
        assert_eq!(candidate["comparison_rank"], (index + 1) as u64);
        assert_eq!(candidate["status"], "validated");
        assert_eq!(candidate["dry_run"], true);
        assert_eq!(candidate["execution_authorized"], false);
        assert_eq!(candidate["approval_authorized"], false);
        assert_eq!(candidate["plan_review_authorized"], false);
        assert_eq!(candidate["registration_authorized"], false);
        assert_eq!(candidate["runtime_authorized"], false);
        assert_eq!(candidate["accepted_truth_authorized"], false);
        assert_eq!(
            candidate["quality_score"],
            candidate["comparison_components"]
                .as_object()
                .expect("comparison components")
                .values()
                .map(|value| value.as_u64().expect("component score"))
                .sum::<u64>()
        );
        assert!(candidate["plan_artifact_path"].is_null());
        assert!(candidate["plan_artifact_path_hash"].is_string());
        assert_eq!(
            candidate["validation_output"]["ready_for_operator_review"],
            true
        );
        assert_eq!(
            candidate["validation_output"]["ready_for_launch_preparation"],
            false
        );
        assert_eq!(candidate["validation_output"]["ready_for_enqueue"], false);
    }
    assert_eq!(candidates[0]["recommended"], true);
    assert_eq!(candidates[0]["strategy_key"], "balanced");
    assert!(candidates[1]["not_recommended_because"]
        .as_array()
        .expect("alternative reasons")
        .iter()
        .any(|value| value == "different_planning_strategy"));
    let artifact_path = comparison["artifact_path"]
        .as_str()
        .expect("plan comparison receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_draft.v1");
    assert_eq!(
        artifact["comparison_schema"],
        "telegram_remote_plan_comparison.v1"
    );
    assert_eq!(artifact["status"], "compared");
    assert_eq!(artifact["dry_run"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    let plan_path = artifact["candidates"][0]["plan_artifact_path"]
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
    assert_eq!(plan["planning_strategy"]["key"], "balanced");
    assert!(plan["authority"]["does_not_authorize"]
        .as_array()
        .expect("denials")
        .iter()
        .any(|item| item == "launch"));
    assert!(!profile_dir(temp.path()).join("offdesk_plans").exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 초안 비교됨</b>"));
    assert!(preview.contains("1. 균형"));
    assert!(preview.contains("다음 조치: 후보 선택 · 등록/승인/실행 권한 없음"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    let selection_label = candidates[0]["selection_label"]
        .as_str()
        .expect("selection label")
        .to_string();
    assert!(button_texts(&result).contains(&selection_label));
    assert_mobile_contract(&result);

    let selection_update = temp.path().join("plan_selection_update.json");
    write_text_update(&selection_update, 735, 915, &selection_label)?;
    let selection_out = temp.path().join("plan_selection_result.json");
    let selection_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&selection_update)
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
        .arg(&selection_out)
        .output()?;
    assert!(
        selection_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&selection_output.stdout),
        String::from_utf8_lossy(&selection_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&selection_out)?)?;
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_draft_selected"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_draft_validated"
    );
    let draft = &result["remote_plan_session"]["plan_draft"];
    assert_eq!(draft["schema"], "telegram_remote_plan_selection.v1");
    assert_eq!(draft["status"], "selected");
    assert_eq!(draft["strategy_key"], "balanced");
    assert_eq!(draft["selection_rank"], 1);
    assert_eq!(draft["registration_authorized"], false);
    assert_eq!(draft["plan_review_authorized"], false);
    assert_eq!(draft["execution_authorized"], false);
    assert_eq!(draft["runtime_authorized"], false);
    assert!(draft["plan_artifact_path"].is_null());
    assert!(draft["plan_artifact_path_hash"].is_string());
    assert_effect(&result, "artifact_write", "plan_artifact");
    let selection_artifact_path = draft["artifact_path"]
        .as_str()
        .expect("plan selection receipt path");
    let selection_artifact: Value = serde_json::from_slice(&fs::read(selection_artifact_path)?)?;
    assert_eq!(
        selection_artifact["schema"],
        "telegram_remote_plan_selection.v1"
    );
    assert_eq!(selection_artifact["status"], "selected");
    assert!(selection_artifact["comparison_sha256"].is_string());
    assert_eq!(selection_artifact["comparison_hash_matched"], true);
    assert_eq!(
        selection_artifact["current_plan_sha256"],
        selection_artifact["expected_plan_sha256"]
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 초안 선택됨</b>"));
    assert!(preview.contains("검토/승인/실행 권한 없음"));
    assert!(button_texts(&result).contains(&"계획 등록".to_string()));
    assert_mobile_contract(&result);

    let sixth_update = temp.path().join("plan_register_update.json");
    write_text_update(&sixth_update, 736, 915, "계획 등록")?;
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
    assert_effect(&result, "plan_registry_mutation", "plan_registry");
    assert_eq!(result["effect"]["stage"], "plan_registered");
    assert_eq!(result["approval_authorized"], false);
    assert_eq!(result["approval_resolution_authorized"], false);
    let conversation_row = last_conversation_row(temp.path())?;
    assert_eq!(conversation_row["effect"], result["effect"]);
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
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["selection_hash_matched"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_registration"]["current_selection_sha256"],
        result["remote_plan_session"]["plan_registration"]["expected_selection_sha256"]
    );
    assert!(result["remote_plan_session"]["plan_registration"]["selection_receipt_path"].is_null());
    assert!(
        result["remote_plan_session"]["plan_registration"]["selection_receipt_path_hash"]
            .is_string()
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
    assert!(preview.contains("계획 지문"));
    assert!(preview.contains("아직 실행은 시작하지 않았습니다."));
    assert!(preview.contains("아래 버튼으로 승인·수정·거절 선택"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    let review_buttons = button_texts(&result);
    assert!(review_buttons.contains(&"계획 승인".to_string()));
    assert!(review_buttons.contains(&"수정 요청".to_string()));
    assert!(review_buttons.contains(&"계획 거절".to_string()));
    assert!(!review_buttons.iter().any(|button| button
        .split_whitespace()
        .last()
        .is_some_and(|part| part.len() == 16 && part.chars().all(|ch| ch.is_ascii_hexdigit()))));
    let approve_callback = result["reply_markup_preview"]["inline_keyboard"][0][0]["callback_data"]
        .as_str()
        .expect("hash-bound approve callback")
        .to_string();
    let review_action = result["interaction_context"]["choice_labels"][0]
        .as_str()
        .expect("review action");
    let review_token = review_action
        .split_whitespace()
        .last()
        .expect("review token")
        .to_string();
    assert_eq!(review_token.len(), 16);
    assert_mobile_contract(&result);

    let seventh_update = temp.path().join("plan_review_update.json");
    write_callback_update(&seventh_update, 737, 916, &approve_callback)?;
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
    let observed_hash = result["remote_plan_session"]["plan_review"]["observed_hash"]
        .as_str()
        .expect("observed hash");
    assert!(observed_hash.starts_with("sha256:"));
    assert_eq!(observed_hash.len(), 71);
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["action_token"],
        review_token
    );
    assert_eq!(
        result["remote_plan_session"]["plan_review"]["observed_hash_matched"],
        true
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
    assert_eq!(artifact["decision"], "approved");
    assert_eq!(artifact["observed_hash"], observed_hash);
    assert_eq!(artifact["action_token"], review_token);
    assert_eq!(artifact["received_action_token"], review_token);
    assert_eq!(artifact["observed_hash_matched"], true);
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
    assert!(preview.contains("아래 버튼으로 실행 준비 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"실행 준비 검토".to_string()));
    assert_mobile_contract(&result);

    let eighth_update = temp.path().join("launch_prep_update.json");
    write_text_update(&eighth_update, 738, 916, "실행 준비 검토")?;
    let eighth_out = temp.path().join("launch_prep_result.json");
    let eighth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&eighth_update)
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
        .arg(&eighth_out)
        .output()?;
    assert!(
        eighth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&eighth_output.stdout),
        String::from_utf8_lossy(&eighth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&eighth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_launch_prep_prepared"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_launch_prep_prepared"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["schema"],
        "telegram_remote_plan_launch_prep.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["status"],
        "prepared"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_preparation_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["gate_approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["runtime_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]["schema"],
        "offdesk_plan_launch_prep.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]["ready_for_launch"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]
            ["ready_for_enqueue"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]
            ["applies_file_operations"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]
            ["registration_path"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]
            ["registration_path_hash"]
            .is_string()
    );
    assert!(
        result["remote_plan_session"]["plan_launch_prep"]["launch_prep_output"]["artifacts"]
            ["launch_prep_json"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let artifact_path = result["remote_plan_session"]["plan_launch_prep"]["artifact_path"]
        .as_str()
        .expect("launch prep receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_launch_prep.v1");
    assert_eq!(artifact["status"], "prepared");
    assert_eq!(artifact["launch_preparation_authorized"], true);
    assert_eq!(artifact["approval_authorized"], false);
    assert_eq!(artifact["gate_approval_authorized"], false);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(
        artifact["launch_prep_output"]["schema"],
        "offdesk_plan_launch_prep.v1"
    );
    assert_eq!(artifact["launch_prep_output"]["ready_for_launch"], false);
    assert_eq!(artifact["launch_prep_output"]["ready_for_enqueue"], false);
    assert_eq!(
        artifact["launch_prep_output"]["applies_file_operations"],
        false
    );
    assert!(artifact["launch_prep_output"]["does_not_authorize"]
        .as_array()
        .expect("does_not_authorize array")
        .iter()
        .any(|item| item.as_str() == Some("dispatch")));
    let launch_prep_path = artifact["launch_prep_output"]["artifacts"]["launch_prep_json"]
        .as_str()
        .expect("launch prep json path");
    assert!(Path::new(launch_prep_path).exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>실행 준비 패킷 생성됨</b>"));
    assert!(preview.contains("패킷만 저장했습니다."));
    assert!(preview.contains("실행/승인은 아직 하지 않았습니다."));
    assert!(preview.contains("아래 버튼으로 게이트 요청"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"게이트 요청".to_string()));
    assert_mobile_contract(&result);

    let ninth_update = temp.path().join("gate_request_update.json");
    write_text_update(&ninth_update, 739, 916, "게이트 요청")?;
    let ninth_out = temp.path().join("gate_request_result.json");
    let ninth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&ninth_update)
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
        .arg(&ninth_out)
        .output()?;
    assert!(
        ninth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&ninth_output.stdout),
        String::from_utf8_lossy(&ninth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&ninth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_gate_request_created"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_gate_request_created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["schema"],
        "telegram_remote_plan_gate_request.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["status"],
        "pending_approval"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["gate_request_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["gate_approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["runtime_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_gate_request"]["launch_prep_json"].is_null());
    assert!(
        result["remote_plan_session"]["plan_gate_request"]["launch_prep_json_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["gate_output"]["status"],
        "pending_approval"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["gate_output"]["approval"]["action"],
        "dispatch.runtime"
    );
    assert!(
        result["remote_plan_session"]["plan_gate_request"]["gate_output"]["approval"]
            ["approval_id"]
            .is_string()
    );
    let artifact_path = result["remote_plan_session"]["plan_gate_request"]["artifact_path"]
        .as_str()
        .expect("gate request receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_gate_request.v1");
    assert_eq!(artifact["status"], "pending_approval");
    assert_eq!(artifact["gate_request_authorized"], true);
    assert_eq!(artifact["approval_authorized"], false);
    assert_eq!(artifact["gate_approval_authorized"], false);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(artifact["gate_output"]["status"], "pending_approval");
    assert_eq!(
        artifact["gate_output"]["approval"]["action"],
        "dispatch.runtime"
    );
    assert!(artifact["launch_prep_json"]
        .as_str()
        .is_some_and(|path| { Path::new(path).exists() }));
    let approvals_path = profile_dir(temp.path()).join("pending_action_approvals.json");
    let approvals: Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert_eq!(approvals[0]["action"], "dispatch.runtime");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>게이트 요청 생성됨</b>"));
    assert!(preview.contains("준비 지문"));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(preview.contains("아래 버튼으로 최신 승인/거절 선택"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    let gate_buttons = button_texts(&result);
    let gate_approve_button = "게이트 승인".to_string();
    assert!(gate_buttons.contains(&gate_approve_button));
    assert!(gate_buttons.contains(&"게이트 거절".to_string()));
    let gate_approve_callback = result["reply_markup_preview"]["inline_keyboard"][0][0]
        ["callback_data"]
        .as_str()
        .expect("hash-bound gate approve callback")
        .to_string();
    let gate_action = result["interaction_context"]["choice_labels"][0]
        .as_str()
        .expect("gate action");
    let gate_action_token = gate_action
        .split_whitespace()
        .last()
        .expect("gate action token")
        .to_string();
    assert_eq!(gate_action_token.len(), 16);
    assert!(gate_action_token.chars().all(|ch| ch.is_ascii_hexdigit()));
    let gate_observed_hash = result["remote_plan_session"]["plan_gate_request"]["observed_hash"]
        .as_str()
        .expect("gate observed hash")
        .to_string();
    assert!(gate_observed_hash.starts_with("sha256:"));
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["observed_hash_matched"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_request"]["action_token"],
        gate_action_token
    );
    assert_mobile_contract(&result);

    let legacy_gate_update = temp.path().join("legacy_gate_approval_update.json");
    write_text_update(&legacy_gate_update, 740, 916, "게이트 승인")?;
    let legacy_gate_out = temp.path().join("legacy_gate_approval_result.json");
    let legacy_gate_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&legacy_gate_update)
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
        .arg(&legacy_gate_out)
        .output()?;
    assert!(
        legacy_gate_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&legacy_gate_output.stdout),
        String::from_utf8_lossy(&legacy_gate_output.stderr)
    );
    let legacy_result: Value = serde_json::from_slice(&fs::read(&legacy_gate_out)?)?;
    assert_eq!(
        legacy_result["parsed_command"]["selection_status"],
        "plan_gate_observed_hash_required"
    );
    assert_eq!(
        legacy_result["remote_plan_session"]["stage"],
        "plan_gate_request_created"
    );
    assert!(legacy_result["remote_plan_session"]["plan_gate_resolution"].is_null());
    assert!(legacy_result["message_preview"]
        .as_str()
        .expect("legacy gate preview")
        .contains("<b>게이트 결정 갱신 필요</b>"));
    assert!(button_texts(&legacy_result).contains(&gate_approve_button));
    let approvals: Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals[0]["status"], "pending");

    let stale_gate_update = temp.path().join("stale_gate_approval_update.json");
    write_text_update(&stale_gate_update, 741, 916, "게이트 승인 0000000000000000")?;
    let stale_gate_out = temp.path().join("stale_gate_approval_result.json");
    let stale_gate_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&stale_gate_update)
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
        .arg(&stale_gate_out)
        .output()?;
    assert!(
        stale_gate_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&stale_gate_output.stdout),
        String::from_utf8_lossy(&stale_gate_output.stderr)
    );
    let stale_result: Value = serde_json::from_slice(&fs::read(&stale_gate_out)?)?;
    assert_eq!(
        stale_result["parsed_command"]["selection_status"],
        "plan_gate_observed_hash_required"
    );
    assert_eq!(
        stale_result["remote_plan_session"]["stage"],
        "plan_gate_request_created"
    );
    assert!(stale_result["remote_plan_session"]["plan_gate_resolution"].is_null());
    assert!(button_texts(&stale_result).contains(&gate_approve_button));
    let approvals: Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals[0]["status"], "pending");

    let tenth_update = temp.path().join("gate_approval_update.json");
    write_callback_update(&tenth_update, 742, 916, &gate_approve_callback)?;
    let tenth_out = temp.path().join("gate_approval_result.json");
    let tenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&tenth_update)
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
        .arg(&tenth_out)
        .output()?;
    assert!(
        tenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&tenth_output.stdout),
        String::from_utf8_lossy(&tenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&tenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_gate_approved"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "plan_gate_approved");
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["schema"],
        "telegram_remote_plan_gate_resolution.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["status"],
        "approved"
    );
    assert_effect(&result, "approval_resolution", "offdesk_gate");
    assert_eq!(result["effect"]["action"], "approved");
    assert_eq!(result["approval_resolution_authorized"], true);
    assert_eq!(result["approval_authorized"], true);
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["approval_resolution_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["approval_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["gate_approval_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["runtime_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["observed_hash"],
        gate_observed_hash
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["action_token"],
        gate_action_token
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["received_action_token"],
        gate_action_token
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["observed_hash_matched"],
        true
    );
    assert!(result["remote_plan_session"]["plan_gate_resolution"]["launch_prep_json"].is_null());
    assert!(
        result["remote_plan_session"]["plan_gate_resolution"]["launch_prep_json_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["pending_approval"]["status"],
        "pending"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["resolution_output"]["status"],
        "approved"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_gate_resolution"]["resolution_output"]["action"],
        "dispatch.runtime"
    );
    let artifact_path = result["remote_plan_session"]["plan_gate_resolution"]["artifact_path"]
        .as_str()
        .expect("gate resolution receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_gate_resolution.v1"
    );
    assert_eq!(artifact["status"], "approved");
    assert_eq!(artifact["approval_resolution_authorized"], true);
    assert_eq!(artifact["approval_authorized"], true);
    assert_eq!(artifact["gate_approval_authorized"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(artifact["observed_hash"], gate_observed_hash);
    assert_eq!(artifact["action_token"], gate_action_token);
    assert_eq!(artifact["received_action_token"], gate_action_token);
    assert_eq!(artifact["observed_hash_matched"], true);
    assert_eq!(artifact["pending_approval"]["status"], "pending");
    assert_eq!(artifact["resolution_output"]["status"], "approved");
    assert_eq!(artifact["resolution_output"]["action"], "dispatch.runtime");
    let approvals: Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert_eq!(approvals[0]["action"], "dispatch.runtime");
    assert_eq!(approvals[0]["status"], "approved");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>게이트 승인됨</b>"));
    assert!(preview.contains("approval만 해결했습니다."));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(preview.contains("로컬에서 다음 단계 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"실행 브리프 생성".to_string()));
    assert_mobile_contract(&result);

    let eleventh_update = temp.path().join("execution_brief_update.json");
    write_text_update(&eleventh_update, 743, 916, "실행 브리프 생성")?;
    let eleventh_out = temp.path().join("execution_brief_result.json");
    let eleventh_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&eleventh_update)
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
        .arg(&eleventh_out)
        .output()?;
    assert!(
        eleventh_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&eleventh_output.stdout),
        String::from_utf8_lossy(&eleventh_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&eleventh_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_execution_brief_created"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_execution_brief_created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["schema"],
        "telegram_remote_plan_execution_brief.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["status"],
        "created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_brief_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["gate_approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["runtime_authorized"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_brief_json"].is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_brief_json_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_brief"]["approved"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_execution_brief"]["execution_brief"]
            ["allowed_runtime_mutations"][0],
        "dispatch.runtime"
    );
    let artifact_path = result["remote_plan_session"]["plan_execution_brief"]["artifact_path"]
        .as_str()
        .expect("execution brief receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_execution_brief.v1"
    );
    assert_eq!(artifact["status"], "created");
    assert_eq!(artifact["execution_brief_authorized"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(artifact["execution_brief"]["approved"], true);
    assert_eq!(
        artifact["execution_brief"]["allowed_runtime_mutations"][0],
        "dispatch.runtime"
    );
    let execution_brief_path = artifact["execution_brief_json"]
        .as_str()
        .expect("execution brief json path");
    assert!(Path::new(execution_brief_path).exists());
    let execution_brief: Value = serde_json::from_slice(&fs::read(execution_brief_path)?)?;
    assert_eq!(execution_brief["approved"], true);
    assert_eq!(execution_brief["project_key"], artifact["project_key"]);
    assert_eq!(execution_brief["request_id"], artifact["request_id"]);
    assert_eq!(execution_brief["task_id"], artifact["task_id"]);
    assert_eq!(
        execution_brief["allowed_runtime_mutations"][0],
        "dispatch.runtime"
    );
    assert!(
        !profile_dir(temp.path()).join("offdesk_tasks.json").exists(),
        "execution brief creation must not enqueue a task"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>실행 브리프 생성됨</b>"));
    assert!(preview.contains("브리프 파일만 저장했습니다."));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(preview.contains("로컬에서 enqueue 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"큐 등록 검토".to_string()));
    assert_mobile_contract(&result);

    let twelfth_update = temp.path().join("enqueue_handoff_update.json");
    write_text_update(&twelfth_update, 744, 916, "큐 등록 검토")?;
    let twelfth_out = temp.path().join("enqueue_handoff_result.json");
    let twelfth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&twelfth_update)
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
        .arg(&twelfth_out)
        .output()?;
    assert!(
        twelfth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&twelfth_output.stdout),
        String::from_utf8_lossy(&twelfth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&twelfth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_enqueue_handoff_created"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_enqueue_handoff_created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["schema"],
        "telegram_remote_plan_enqueue_handoff.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["status"],
        "created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["prepared_workload_required"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["reviewed_workload_command_required"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["gate_approval_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["runtime_authorized"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["execution_brief_json"].is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_enqueue_handoff"]["execution_brief_json_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let public_command = result["remote_plan_session"]["plan_enqueue_handoff"]["command_template"]
        .as_array()
        .expect("public command template")
        .iter()
        .map(|item| item.as_str().unwrap_or_default())
        .collect::<Vec<_>>();
    assert!(public_command.contains(&"enqueue"));
    assert!(public_command.contains(&"dispatch.runtime"));
    assert!(public_command.contains(&"<execution_brief_json>"));
    assert!(public_command.contains(&"<reviewed-workload-command-required>"));
    let artifact_path = result["remote_plan_session"]["plan_enqueue_handoff"]["artifact_path"]
        .as_str()
        .expect("enqueue handoff receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_enqueue_handoff.v1"
    );
    assert_eq!(artifact["status"], "created");
    assert_eq!(artifact["prepared_workload_required"], true);
    assert_eq!(artifact["reviewed_workload_command_required"], true);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(
        artifact["execution_brief_json"],
        Value::String(execution_brief_path.to_string())
    );
    let command = artifact["command_template"]
        .as_array()
        .expect("command template")
        .iter()
        .map(|item| item.as_str().unwrap_or_default())
        .collect::<Vec<_>>();
    assert!(command.contains(&"enqueue"));
    assert!(command.contains(&"dispatch.runtime"));
    assert!(command.contains(&execution_brief_path));
    assert!(command.contains(&"<reviewed-workload-command-required>"));
    assert!(
        !profile_dir(temp.path()).join("offdesk_tasks.json").exists(),
        "enqueue handoff must not enqueue a task"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>큐 등록 검토 준비됨</b>"));
    assert!(preview.contains("명령 템플릿만 저장했습니다."));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"워크로드 패킷 연결".to_string()));
    assert_mobile_contract(&result);

    let project_key = artifact["project_key"]
        .as_str()
        .expect("project key")
        .to_string();
    let request_id = artifact["request_id"]
        .as_str()
        .expect("request id")
        .to_string();
    let task_id = artifact["task_id"].as_str().expect("task id").to_string();
    let prepared_dir = temp.path().join("prepared_workload");
    fs::create_dir_all(&prepared_dir)?;
    let wrapper_path = prepared_dir.join("run_workload.sh");
    let workload_result_path = prepared_dir.join("result.json");
    fs::write(
        &wrapper_path,
        format!(
            "#!/usr/bin/env bash\nprintf 'ok\\n'\nprintf '{{\"status\":\"ok\"}}\\n' > {}\n",
            workload_result_path.display()
        ),
    )?;
    let prepared_task_path = prepared_dir.join("prepared_task.json");
    let prepared_manifest = json!({
        "created_at": "2026-06-17T00:00:00Z",
        "kind": "forager_offdesk_prepared_workload",
        "title": "Telegram prepared workload fixture",
        "profile": "default",
        "project_key": project_key,
        "request_id": request_id,
        "task_id": task_id,
        "repo": workspace_root,
        "out_dir": prepared_dir,
        "duration_minutes": 0.1,
        "max_iterations": 1,
        "provider": "ollama",
        "model": "qwen3-coder-next:latest",
        "workload_command": ["bash", "-lc", "printf 'ok\\n'"],
        "workload_command_text": "printf 'ok\\n'",
        "workload_wrapper": wrapper_path,
        "enqueue_args": [
            env!("CARGO_BIN_EXE_forager"),
            "--profile",
            "default",
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            artifact["project_key"].as_str().expect("project key"),
            "--request-id",
            artifact["request_id"].as_str().expect("request id"),
            "--task-id",
            artifact["task_id"].as_str().expect("task id"),
            "--cmd",
            format!("bash {}", wrapper_path.display()),
            "--workdir",
            workspace_root,
            "--artifact-kind",
            "report",
            "--agent-mode",
            "critique",
            "--provider-id",
            "ollama",
            "--model",
            "qwen3-coder-next:latest",
            "--log-artifact",
            prepared_dir.join("offdesk-runner.log"),
            "--result-artifact",
            workload_result_path,
            "--json"
        ],
        "safety": {
            "repo_read_only": true,
            "writes_only_under_out_dir": true,
            "model_responses_not_executed": true,
            "no_file_deletion_or_cleanup": true,
            "no_reboot_shutdown_or_power_state_change": true,
            "no_service_restart_or_system_config_change": true,
            "no_storage_raid_nvme_or_mount_change": true,
            "no_package_install_or_permission_change": true,
            "no_process_termination_or_runner_interference": true,
            "no_network_firewall_or_remote_access_change": true,
            "no_kernel_driver_firmware_or_bios_change": true,
            "operator_approval_required_for_system_mutation": true,
            "capability": "dispatch.runtime",
            "runner": "local-background",
            "approval_required_before_dispatch": true,
            "separate_review_artifact_required": true
        },
        "preflight": {
            "ready_for_enqueue": true,
            "blocking_reasons": [],
            "warnings": [],
            "role_gate": {
                "ready": false,
                "reason": "not_required_for_fixture"
            },
            "review_artifact": {
                "ready": true,
                "path": prepared_dir.join("workload_review").join("results.json"),
                "decision": "needs_approval"
            }
        },
        "artifacts": {
            "prepared_task": prepared_task_path,
            "preflight": prepared_dir.join("preflight.json"),
            "runner_log": prepared_dir.join("offdesk-runner.log"),
            "result": workload_result_path,
            "report": prepared_dir.join("REPORT.md"),
            "workload_wrapper": wrapper_path
        }
    });
    fs::write(
        &prepared_task_path,
        serde_json::to_string_pretty(&prepared_manifest)?,
    )?;

    let thirteenth_update = temp.path().join("workload_binding_update.json");
    write_text_update(
        &thirteenth_update,
        745,
        916,
        prepared_task_path.to_str().expect("prepared task path"),
    )?;
    let thirteenth_out = temp.path().join("workload_binding_result.json");
    let thirteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&thirteenth_update)
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
        .arg(&thirteenth_out)
        .output()?;
    assert!(
        thirteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&thirteenth_output.stdout),
        String::from_utf8_lossy(&thirteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&thirteenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_workload_bound"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_workload_bound"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["schema"],
        "telegram_remote_plan_workload_binding.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["status"],
        "bound"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["ready_for_local_enqueue_review"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["enqueue_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_workload_binding"]["runtime_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_workload_binding"]["prepared_task_json"].is_null());
    assert!(
        result["remote_plan_session"]["plan_workload_binding"]["prepared_task_json_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let public_bound_args = result["remote_plan_session"]["plan_workload_binding"]
        ["bound_enqueue_args"]
        .as_array()
        .expect("public bound enqueue args")
        .iter()
        .map(|item| item.as_str().unwrap_or_default())
        .collect::<Vec<_>>();
    assert!(public_bound_args.contains(&"enqueue"));
    assert!(public_bound_args.contains(&"dispatch.runtime"));
    assert!(public_bound_args.contains(&"<execution_brief_json>"));
    assert!(public_bound_args
        .iter()
        .any(|item| item.contains("<workload_wrapper>")));
    let artifact_path = result["remote_plan_session"]["plan_workload_binding"]["artifact_path"]
        .as_str()
        .expect("workload binding receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_workload_binding.v1"
    );
    assert_eq!(artifact["status"], "bound");
    assert_eq!(artifact["ready_for_local_enqueue_review"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["enqueue_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    // The adapter canonicalizes the path; on macOS /var is a symlink to
    // /private/var, so the expectation must be canonicalized too.
    assert_eq!(
        artifact["prepared_task_json"],
        Value::String(
            prepared_task_path
                .canonicalize()?
                .to_string_lossy()
                .into_owned()
        )
    );
    let bound_args = artifact["bound_enqueue_args"]
        .as_array()
        .expect("bound enqueue args")
        .iter()
        .map(|item| item.as_str().unwrap_or_default())
        .collect::<Vec<_>>();
    assert!(bound_args.contains(&"--brief"));
    assert!(bound_args.contains(&execution_brief_path));
    assert!(bound_args.contains(&"--mutation-class"));
    assert!(bound_args.contains(&"dispatch.runtime"));
    assert!(
        !profile_dir(temp.path()).join("offdesk_tasks.json").exists(),
        "workload binding must not enqueue a task"
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>워크로드 패킷 연결됨</b>"));
    assert!(preview.contains("검토된 패킷만 연결했습니다."));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"큐 등록 실행".to_string()));
    assert_mobile_contract(&result);

    let fourteenth_update = temp.path().join("enqueue_run_update.json");
    write_text_update(&fourteenth_update, 746, 916, "큐 등록 실행")?;
    let fourteenth_out = temp.path().join("enqueue_run_result.json");
    let fourteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&fourteenth_update)
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
        .arg(&fourteenth_out)
        .output()?;
    assert!(
        fourteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&fourteenth_output.stdout),
        String::from_utf8_lossy(&fourteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&fourteenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_enqueued"
    );
    assert_eq!(result["remote_plan_session"]["stage"], "plan_enqueued");
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["schema"],
        "telegram_remote_plan_enqueue_run.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["status"],
        "queued"
    );
    assert_effect(&result, "queue_mutation", "offdesk_task_queue");
    assert_eq!(result["effect"]["action"], "enqueue");
    assert_eq!(result["approval_authorized"], false);
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["queue_mutation_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["enqueue_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["execution_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["launch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["runtime_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_enqueue_run"]["enqueue_command"].is_null());
    assert!(
        result["remote_plan_session"]["plan_enqueue_run"]["enqueue_command_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_enqueue_run"]["enqueue_output"]["status"],
        "queued"
    );
    let artifact_path = result["remote_plan_session"]["plan_enqueue_run"]["artifact_path"]
        .as_str()
        .expect("enqueue run receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_enqueue_run.v1");
    assert_eq!(artifact["status"], "queued");
    assert_eq!(artifact["queue_mutation_authorized"], true);
    assert_eq!(artifact["enqueue_authorized"], true);
    assert_eq!(artifact["execution_authorized"], false);
    assert_eq!(artifact["launch_authorized"], false);
    assert_eq!(artifact["runtime_authorized"], false);
    assert_eq!(artifact["enqueue_output"]["status"], "queued");
    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    assert!(tasks_path.exists(), "enqueue run must create a queued task");
    let tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["task_id"], task_id);
    assert_eq!(tasks[0]["status"], "queued");
    assert!(tasks[0]["background_ticket_id"].is_null());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>큐 등록됨</b>"));
    assert!(preview.contains("Offdesk 큐에만 등록했습니다."));
    assert!(preview.contains("실행은 아직 시작하지 않았습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"실행 시작".to_string()));
    assert_mobile_contract(&result);

    let fifteenth_update = temp.path().join("runtime_start_update.json");
    write_text_update(&fifteenth_update, 747, 916, "실행 시작")?;
    let fifteenth_out = temp.path().join("runtime_start_result.json");
    let fifteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&fifteenth_update)
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
        .arg(&fifteenth_out)
        .output()?;
    assert!(
        fifteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&fifteenth_output.stdout),
        String::from_utf8_lossy(&fifteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&fifteenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_runtime_started"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_runtime_started"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["schema"],
        "telegram_remote_plan_runtime_start.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["status"],
        "launched"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["runtime_start_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["tick_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["execution_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["closeout_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["accepted_truth_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_runtime_start"]["tick_command"].is_null());
    assert!(
        result["remote_plan_session"]["plan_runtime_start"]["tick_command_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["tick_output"]["launched"],
        1
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_start"]["tick_output"]["updated_task_ids"][0],
        task_id
    );
    let artifact_path = result["remote_plan_session"]["plan_runtime_start"]["artifact_path"]
        .as_str()
        .expect("runtime start receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(artifact["schema"], "telegram_remote_plan_runtime_start.v1");
    assert_eq!(artifact["status"], "launched");
    assert_eq!(artifact["tick_output"]["launched"], 1);
    assert_eq!(artifact["tick_output"]["updated_task_ids"][0], task_id);
    assert_eq!(artifact["closeout_authorized"], false);
    assert_eq!(artifact["accepted_truth_authorized"], false);
    let tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["task_id"], task_id);
    assert_eq!(tasks[0]["status"], "launched");
    assert!(tasks[0]["background_ticket_id"].is_string());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>실행 시작됨</b>"));
    assert!(preview.contains("대상 task만 시작했습니다."));
    assert!(preview.contains("완료 판정은 아직 없습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"실행 상태 확인".to_string()));
    assert_mobile_contract(&result);

    thread::sleep(Duration::from_millis(250));
    let sixteenth_update = temp.path().join("runtime_monitor_update.json");
    write_text_update(&sixteenth_update, 748, 916, "실행 상태 확인")?;
    let sixteenth_out = temp.path().join("runtime_monitor_result.json");
    let sixteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&sixteenth_update)
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
        .arg(&sixteenth_out)
        .output()?;
    assert!(
        sixteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&sixteenth_output.stdout),
        String::from_utf8_lossy(&sixteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&sixteenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_runtime_monitored"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_runtime_monitored"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["schema"],
        "telegram_remote_plan_runtime_monitor.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["status"],
        "completed"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["task_status"],
        "completed"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["poll_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["dispatch_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["closeout_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["accepted_truth_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_runtime_monitor"]["tick_command"].is_null());
    assert!(result["remote_plan_session"]["plan_runtime_monitor"]["tasks_command"].is_null());
    assert!(
        result["remote_plan_session"]["plan_runtime_monitor"]["tick_command_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["tick_output"]["completed"],
        1
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["tick_output"]["updated_task_ids"][0],
        task_id
    );
    assert_eq!(
        result["remote_plan_session"]["plan_runtime_monitor"]["target_task"]["status"],
        "completed"
    );
    let artifact_path = result["remote_plan_session"]["plan_runtime_monitor"]["artifact_path"]
        .as_str()
        .expect("runtime monitor receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_runtime_monitor.v1"
    );
    assert_eq!(artifact["status"], "completed");
    assert_eq!(artifact["task_status"], "completed");
    assert_eq!(artifact["tick_output"]["completed"], 1);
    assert_eq!(artifact["tick_output"]["updated_task_ids"][0], task_id);
    assert_eq!(artifact["dispatch_authorized"], false);
    assert_eq!(artifact["closeout_authorized"], false);
    assert_eq!(artifact["accepted_truth_authorized"], false);
    let tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["task_id"], task_id);
    assert_eq!(tasks[0]["status"], "completed");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>실행 완료 확인</b>"));
    assert!(preview.contains("로컬에서 closeout 검토"));
    assert!(preview.contains("결과 승인은 아직 없습니다."));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"실행 상태 확인".to_string()));
    assert!(button_texts(&result).contains(&"마무리 패킷 생성".to_string()));
    assert_mobile_contract(&result);

    let seventeenth_update = temp.path().join("closeout_packet_update.json");
    write_text_update(&seventeenth_update, 749, 916, "마무리 패킷 생성")?;
    let seventeenth_out = temp.path().join("closeout_packet_result.json");
    let seventeenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&seventeenth_update)
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
        .arg(&seventeenth_out)
        .output()?;
    assert!(
        seventeenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&seventeenth_output.stdout),
        String::from_utf8_lossy(&seventeenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&seventeenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_closeout_packet_created"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_closeout_packet_created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["schema"],
        "telegram_remote_plan_closeout_packet.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["status"],
        "created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_packet_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_review_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["accepted_truth_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["file_mutation_authorized"],
        false
    );
    assert!(result["remote_plan_session"]["plan_closeout_packet"]["closeout_command"].is_null());
    assert!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_command_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_output"]["dry_run"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_output"]
            ["read_only_project_state"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_packet"]["closeout_output"]["review_contract"]
            ["required"],
        true
    );
    let artifact_path = result["remote_plan_session"]["plan_closeout_packet"]["artifact_path"]
        .as_str()
        .expect("closeout packet receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_closeout_packet.v1"
    );
    assert_eq!(artifact["status"], "created");
    assert_eq!(artifact["closeout_packet_authorized"], true);
    assert_eq!(artifact["closeout_review_authorized"], false);
    assert_eq!(artifact["accepted_truth_authorized"], false);
    assert_eq!(artifact["file_mutation_authorized"], false);
    assert_eq!(artifact["closeout_output"]["dry_run"], true);
    assert_eq!(artifact["closeout_output"]["read_only_project_state"], true);
    assert_eq!(
        artifact["closeout_output"]["operator_requested_dry_run"],
        true
    );
    assert_eq!(artifact["closeout_output"]["tasks"][0]["task_id"], task_id);
    assert_eq!(
        artifact["closeout_output"]["tasks"][0]["status"],
        "completed"
    );
    assert!(PathBuf::from(
        artifact["closeout_output"]["artifacts"]["closeout_plan_json"]
            .as_str()
            .expect("closeout plan path")
    )
    .exists());
    assert!(PathBuf::from(
        artifact["closeout_output"]["artifacts"]["return_package_markdown"]
            .as_str()
            .expect("return package path")
    )
    .exists());
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>마무리 패킷 생성됨</b>"));
    assert!(preview.contains("closeout 자료만 만들었습니다."));
    assert!(preview.contains("결과 승인은 아직 없습니다."));
    assert!(preview.contains("로컬에서 closeout-review 검토"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"마무리 검토 준비".to_string()));
    assert_mobile_contract(&result);

    let eighteenth_update = temp.path().join("closeout_review_handoff_update.json");
    write_text_update(&eighteenth_update, 750, 917, "마무리 검토 준비")?;
    let eighteenth_out = temp.path().join("closeout_review_handoff_result.json");
    let eighteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&eighteenth_update)
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
        .arg(&eighteenth_out)
        .output()?;
    assert!(
        eighteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&eighteenth_output.stdout),
        String::from_utf8_lossy(&eighteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&eighteenth_out)?)?;
    assert_eq!(result["parsed_command"]["command"], "remote_plan_selection");
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_closeout_review_handoff_created"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_closeout_review_handoff_created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]["schema"],
        "telegram_remote_plan_closeout_review_handoff.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]["status"],
        "created"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]
            ["remote_closeout_review_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]["accepted_truth_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]["file_mutation_authorized"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]["local_review_commands"]
            .is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_closeout_review_handoff"]
            ["local_review_command_hashes"]["approved"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    let artifact_path = result["remote_plan_session"]["plan_closeout_review_handoff"]
        ["artifact_path"]
        .as_str()
        .expect("closeout review handoff receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_closeout_review_handoff.v1"
    );
    assert_eq!(artifact["status"], "created");
    assert_eq!(artifact["remote_closeout_review_authorized"], false);
    assert_eq!(artifact["closeout_review_authorized"], false);
    assert_eq!(artifact["accepted_truth_authorized"], false);
    assert_eq!(artifact["file_mutation_authorized"], false);
    assert_eq!(artifact["local_review_required"], true);
    assert!(artifact["approved_verdict_may_accept_truth"].is_boolean());
    for (verdict, expected) in [
        ("approved", "approved"),
        ("revise", "revise"),
        ("blocked", "blocked"),
    ] {
        let command = artifact["local_review_commands"][verdict]
            .as_array()
            .expect("local review command");
        assert!(command.iter().any(|item| item == "closeout-review"));
        assert!(command.iter().any(|item| item == expected));
    }
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>마무리 검토 준비됨</b>"));
    assert!(preview.contains("Telegram에서 verdict를 기록할 수 있습니다."));
    assert!(preview.contains("아래 버튼에서 verdict 선택"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert!(button_texts(&result).contains(&"승인 기록".to_string()));
    assert!(button_texts(&result).contains(&"수정 요청 기록".to_string()));
    assert!(button_texts(&result).contains(&"차단 기록".to_string()));
    assert_mobile_contract(&result);

    let nineteenth_update = temp.path().join("closeout_approved_update.json");
    write_text_update(&nineteenth_update, 751, 918, "승인 기록")?;
    let nineteenth_out = temp.path().join("closeout_approved_result.json");
    let nineteenth_output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&nineteenth_update)
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
        .arg(&nineteenth_out)
        .output()?;
    assert!(
        nineteenth_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&nineteenth_output.stdout),
        String::from_utf8_lossy(&nineteenth_output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&nineteenth_out)?)?;
    assert_eq!(
        result["parsed_command"]["selection_status"],
        "plan_closeout_verdict_recorded"
    );
    assert_eq!(
        result["remote_plan_session"]["stage"],
        "plan_closeout_verdict_recorded"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["schema"],
        "telegram_remote_plan_closeout_verdict.v1"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["status"],
        "recorded"
    );
    assert_effect(
        &result,
        "closeout_review_resolution",
        "accepted_truth_review",
    );
    assert_eq!(result["effect"]["action"], "approved");
    assert_eq!(result["approval_authorized"], false);
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["verdict"],
        "approved"
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["accepted_truth_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["remote_closeout_review_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["closeout_review_authorized"],
        true
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["project_file_mutation_authorized"],
        false
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["file_mutation_authorized"],
        false
    );
    assert!(
        result["remote_plan_session"]["plan_closeout_verdict"]["closeout_review_command"].is_null()
    );
    assert!(
        result["remote_plan_session"]["plan_closeout_verdict"]["closeout_review_command_hash"]
            .as_str()
            .unwrap_or_default()
            .starts_with("sha256:")
    );
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["closeout_review_output"]["verdict"],
        "approved"
    );
    let acceptance_status = result["remote_plan_session"]["plan_closeout_verdict"]
        ["closeout_review_output"]["closeout_receipt"]["acceptance_status"]
        .as_str()
        .expect("acceptance status");
    assert!(matches!(
        acceptance_status,
        "accepted" | "approved_with_followups"
    ));
    assert_eq!(
        result["remote_plan_session"]["plan_closeout_verdict"]["accepted_truth_recorded"],
        acceptance_status == "accepted"
    );
    let artifact_path = result["remote_plan_session"]["plan_closeout_verdict"]["artifact_path"]
        .as_str()
        .expect("closeout verdict receipt path");
    let artifact: Value = serde_json::from_slice(&fs::read(artifact_path)?)?;
    assert_eq!(
        artifact["schema"],
        "telegram_remote_plan_closeout_verdict.v1"
    );
    assert_eq!(artifact["status"], "recorded");
    assert_eq!(artifact["verdict"], "approved");
    assert_eq!(artifact["accepted_truth_authorized"], true);
    assert_eq!(
        artifact["accepted_truth_recorded"],
        artifact["acceptance_status"] == "accepted"
    );
    assert_eq!(artifact["project_file_mutation_authorized"], false);
    assert_eq!(artifact["file_mutation_authorized"], false);
    let artifact_acceptance = artifact["closeout_review_output"]["closeout_receipt"]
        ["acceptance_status"]
        .as_str()
        .expect("artifact acceptance status");
    assert!(matches!(
        artifact_acceptance,
        "accepted" | "approved_with_followups"
    ));
    assert_eq!(
        artifact["closeout_review_output"]["closeout_receipt"]["acceptance_status"],
        artifact["acceptance_status"]
    );
    assert_eq!(
        artifact["closeout_review_output"]["applies_file_operations"],
        false
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>마무리 verdict 기록됨</b>"));
    assert!(preview.contains("approved"));
    assert!(preview.contains(acceptance_status));
    if acceptance_status == "accepted" {
        assert!(preview.contains("accepted truth가 기록됐습니다."));
    } else {
        assert!(preview.contains("follow-up이 남아 아직 accepted는 아닙니다."));
    }
    assert!(!preview.contains(workspace_root.to_str().expect("workspace path")));
    assert_mobile_contract(&result);

    let feedback_rows = fs::read_to_string(&feedback_file)?;
    assert_eq!(feedback_rows.lines().count(), 1);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 752);
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
        "/plan Alpha 프로젝트를 오늘 밤 자율주행 계획으로 잡아줘",
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
    write_text_update(&second_update, 721, 901, "/select Gamma 프로젝트")?;
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
    assert!(preview.contains("<b>계획 대상 확인 필요</b>"));
    assert!(preview.contains("Gamma 프로젝트"));
    assert!(preview.contains("경로 미확인"));
    assert!(preview.contains("실제 폴더명/경로 입력"));
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
                "date": current_unix_timestamp(),
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
    assert_eq!(result["status_counts"]["rendered"], 1);
    assert_eq!(result["status_counts"]["no_update"], 1);
    assert_eq!(result["consecutive_error_count"], 0);
    assert!(result["last_success_at"].is_string());
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
fn remote_operator_telegram_listener_lock_rejects_a_second_owner() -> Result<()> {
    let temp = tempdir()?;
    let lock_path = temp.path().join("listener.lock");
    let script_dir = script_path("offdesk_remote_operator_telegram.py")
        .parent()
        .expect("script parent")
        .to_path_buf();
    let python = r#"
import os
import pathlib
import sys
sys.path.insert(0, sys.argv[1])
from offdesk_remote_operator_telegram import acquire_listener_lock
from telegram_operator.common import RemoteOperatorTelegramError
first = acquire_listener_lock(pathlib.Path(sys.argv[2]))
try:
    try:
        acquire_listener_lock(pathlib.Path(sys.argv[2]))
    except RemoteOperatorTelegramError:
        pass
    else:
        raise SystemExit("second listener unexpectedly acquired the lock")
finally:
    os.close(first)
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(python)
        .arg(script_dir)
        .arg(&lock_path)
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    #[cfg(unix)]
    assert_eq!(fs::metadata(lock_path)?.permissions().mode() & 0o777, 0o600);
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
    assert_eq!(result["action_readiness"][4]["action"], "session_message");
    assert_eq!(result["action_readiness"][4]["status"], "blocked");
    assert_eq!(
        result["action_readiness"][4]["reason"],
        "agent_runtime_disabled"
    );
    assert!(result["tmux_available"].is_boolean());
    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains("fake-token-for-test"));
    assert!(!serialized.contains("999999:"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_corrupt_state_fails_closed_without_reset() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let corrupt_state = b"{\"schema\":";
    fs::write(&state_path, corrupt_state)?;
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_100, 601, "/help")?;
    let out = temp.path().join("result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert_eq!(output.status.code(), Some(1));
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["status"], "error");
    assert!(result["error"]
        .as_str()
        .expect("state error")
        .contains("was not reset"));
    assert_eq!(fs::read(&state_path)?, corrupt_state);
    assert!(!temp
        .path()
        .join(".cache/forager/remote_operator_telegram_updates.jsonl")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_reports_corrupt_listener_state() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(&state_path, "not-json")?;
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    let out = temp.path().join("health.json");

    let output = remote_operator_command(temp.path())
        .arg("--health")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--health-max-age-sec")
        .arg("999999999")
        .arg("--out")
        .arg(&out)
        .output()?;

    assert_eq!(output.status.code(), Some(1));
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["health_status"], "unhealthy");
    assert!(result["transport_issues"]
        .as_array()
        .expect("transport issues")
        .contains(&json!("listener_state_unreadable")));
    assert_eq!(result["action_readiness"][0]["status"], "blocked");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_reports_pending_reply_delivery() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &journal_path,
        [
            json!({
                "schema": "remote_operator_telegram_update_journal.v2",
                "recorded_at": "2026-08-11T00:00:00+00:00",
                "status": "started",
                "update_id": 9_190,
                "input_hash": "sha256:input",
                "chat_id_hash": "sha256:chat",
                "user_id_hash": "sha256:user"
            }),
            json!({
                "schema": "remote_operator_telegram_update_journal.v2",
                "recorded_at": "2026-08-11T00:00:01+00:00",
                "status": "effect_committed",
                "update_id": 9_190,
                "input_hash": "sha256:input",
                "result_status": "rendered",
                "effect": {"kind": "no_effect"},
                "delivery_id": "telegram_update:9190"
            }),
        ]
        .iter()
        .map(serde_json::to_string)
        .collect::<serde_json::Result<Vec<_>>>()?
        .join("\n")
            + "\n",
    )?;
    fs::write(
        &outbox_path,
        serde_json::to_string(&json!({
            "schema": "remote_operator_telegram_reply_outbox.v1",
            "recorded_at": "2026-08-11T00:00:02+00:00",
            "queued_at": "2026-08-11T00:00:01+00:00",
            "status": "failed",
            "delivery_id": "telegram_update:9190",
            "update_id": 9_190,
            "input_hash": "sha256:input",
            "payload_hash": "sha256:payload",
            "chat_id": "123456789",
            "message": "receipt",
            "reply_markup": null,
            "result_status": "rendered",
            "effect": {"kind": "no_effect"},
            "attempt_count": 1,
            "last_error": "temporary failure"
        }))? + "\n",
    )?;
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "send_failed"
            }
        }))?,
    )?;
    let out = temp.path().join("health.json");

    let output = remote_operator_command(temp.path())
        .arg("--health")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--health-max-age-sec")
        .arg("999999999")
        .arg("--out")
        .arg(&out)
        .output()?;

    assert_eq!(output.status.code(), Some(1));
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["health_status"], "unhealthy");
    assert!(result["transport_issues"]
        .as_array()
        .expect("transport issues")
        .contains(&json!("update_journal_delivery_incomplete")));
    assert!(result["transport_issues"]
        .as_array()
        .expect("transport issues")
        .contains(&json!("reply_delivery_pending")));
    assert_eq!(result["reply_outbox"]["pending_count"], 1);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_completed_journal_prevents_update_replay() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let conversation_path = temp.path().join("conversation.jsonl");
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_200, 602, "/help")?;

    for (index, expected_status) in [(1, "rendered"), (2, "already_processed")] {
        if index == 2 {
            fs::write(
                &state_path,
                serde_json::to_string_pretty(&json!({
                    "schema": "remote_operator_telegram_state.v1",
                    "offset": 0
                }))?,
            )?;
        }
        let out = temp.path().join(format!("result_{index}.json"));
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&update_path)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--update-journal-file")
            .arg(&journal_path)
            .arg("--conversation-file")
            .arg(&conversation_path)
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
        assert_eq!(result["status"], expected_status);
    }

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 9_201);
    assert_eq!(fs::read_to_string(&conversation_path)?.lines().count(), 1);
    let rows = fs::read_to_string(&journal_path)?;
    assert_eq!(rows.lines().count(), 3);
    assert!(rows
        .lines()
        .any(|line| line.contains("\"status\": \"started\"")));
    assert!(rows
        .lines()
        .any(|line| line.contains("\"status\": \"effect_committed\"")));
    assert!(rows
        .lines()
        .any(|line| line.contains("\"status\": \"completed\"")));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_journal_rejects_changed_input_for_completed_update() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_210, 603, "/help")?;

    let first = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .output()?;
    assert!(first.status.success());

    fs::write(
        &state_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0
        }))?,
    )?;
    write_text_update(&update_path, 9_210, 603, "/status")?;
    let second = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .output()?;

    assert_eq!(second.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&second.stderr).contains("input hash does not match"));
    assert_eq!(fs::read_to_string(&journal_path)?.lines().count(), 3);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_journal_inspect_and_reconcile_started_update() -> Result<()> {
    let temp = tempdir()?;
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    fs::write(
        &journal_path,
        serde_json::to_string(&json!({
            "schema": "remote_operator_telegram_update_journal.v1",
            "recorded_at": "2026-08-11T00:00:00+00:00",
            "status": "started",
            "update_id": 9_220,
            "input_hash": "sha256:0123456789abcdef",
            "chat_id_hash": "sha256:chat",
            "user_id_hash": "sha256:user"
        }))? + "\n",
    )?;
    let inspect_out = temp.path().join("inspect.json");
    let inspect = remote_operator_command(temp.path())
        .arg("--journal-inspect")
        .arg("--journal-update-id")
        .arg("9220")
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--out")
        .arg(&inspect_out)
        .output()?;
    assert!(inspect.status.success());
    let inspection: Value = serde_json::from_slice(&fs::read(&inspect_out)?)?;
    assert_eq!(inspection["status_counts"]["started"], 1);
    assert_eq!(inspection["unresolved_update_ids"], json!([9_220]));
    assert_eq!(inspection["reply_outbox"]["pending_count"], 0);

    let reconcile_out = temp.path().join("reconcile.json");
    let reconcile = remote_operator_command(temp.path())
        .arg("--journal-reconcile")
        .arg("retry")
        .arg("--journal-update-id")
        .arg("9220")
        .arg("--journal-reason")
        .arg("downstream receipts confirm no effect was applied")
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--listener-lock-file")
        .arg(temp.path().join("listener.lock"))
        .arg("--out")
        .arg(&reconcile_out)
        .output()?;
    assert!(
        reconcile.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&reconcile.stdout),
        String::from_utf8_lossy(&reconcile.stderr)
    );
    let reconciliation: Value = serde_json::from_slice(&fs::read(&reconcile_out)?)?;
    assert_eq!(reconciliation["status"], "retry_authorized");
    assert_eq!(
        reconciliation["authority_domain"],
        "telegram_update_journal"
    );

    let journal = fs::read_to_string(&journal_path)?;
    assert_eq!(journal.lines().count(), 2);
    assert!(journal
        .lines()
        .last()
        .expect("reconciliation row")
        .contains("retry_authorized"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_journal_compaction_preserves_unresolved_work() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_240, 605, "/help")?;
    let first = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .output()?;
    assert!(first.status.success());
    fs::OpenOptions::new()
        .append(true)
        .open(&journal_path)?
        .write_all(
            (serde_json::to_string(&json!({
                "schema": "remote_operator_telegram_update_journal.v2",
                "recorded_at": "2026-08-11T00:00:00+00:00",
                "status": "started",
                "update_id": 9_241,
                "input_hash": "sha256:pending",
                "chat_id_hash": "sha256:chat",
                "user_id_hash": "sha256:user"
            }))? + "\n")
                .as_bytes(),
        )?;

    let out = temp.path().join("compact.json");
    let compact = remote_operator_command(temp.path())
        .arg("--journal-compact")
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--listener-lock-file")
        .arg(temp.path().join("listener.lock"))
        .arg("--out")
        .arg(&out)
        .output()?;
    assert!(
        compact.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&compact.stdout),
        String::from_utf8_lossy(&compact.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["trusted_offset"], 9_241);
    assert_eq!(result["update_journal"]["removed_update_count"], 1);
    assert_eq!(result["reply_outbox"]["removed_delivery_count"], 1);
    let journal = fs::read_to_string(&journal_path)?;
    assert_eq!(journal.lines().count(), 1);
    assert!(journal.contains("\"update_id\": 9241"));
    assert!(journal.contains("\"status\": \"started\""));
    assert!(fs::read_to_string(&outbox_path)?.is_empty());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_journal_reads_legacy_completed_rows() -> Result<()> {
    let temp = tempdir()?;
    let journal_path = temp.path().join("legacy_update_journal.jsonl");
    fs::write(
        &journal_path,
        [
            json!({
                "schema": "remote_operator_telegram_update_journal.v1",
                "recorded_at": "2026-08-10T00:00:00+00:00",
                "status": "started",
                "update_id": 9_250,
                "input_hash": "sha256:legacy",
                "chat_id_hash": "sha256:chat",
                "user_id_hash": "sha256:user"
            }),
            json!({
                "schema": "remote_operator_telegram_update_journal.v1",
                "recorded_at": "2026-08-10T00:00:01+00:00",
                "status": "completed",
                "update_id": 9_250,
                "result_status": "rendered",
                "send_status": "sent",
                "conversation_record_status": "recorded",
                "effect": {"kind": "no_effect"}
            }),
        ]
        .iter()
        .map(serde_json::to_string)
        .collect::<serde_json::Result<Vec<_>>>()?
        .join("\n")
            + "\n",
    )?;
    let out = temp.path().join("inspect_legacy.json");
    let inspect = remote_operator_command(temp.path())
        .arg("--journal-inspect")
        .arg("--journal-update-id")
        .arg("9250")
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(temp.path().join("reply_outbox.jsonl"))
        .arg("--out")
        .arg(&out)
        .output()?;
    assert!(inspect.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["status_counts"]["completed"], 1);
    assert_eq!(result["latest_records"][0]["input_hash"], "sha256:legacy");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_reply_outbox_retries_without_reapplying_effect() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let journal_path = temp.path().join("update_journal.jsonl");
    let outbox_path = temp.path().join("reply_outbox.jsonl");
    let conversation_path = temp.path().join("conversation.jsonl");
    let update = json!({
        "update_id": 9_230,
        "message": {
            "message_id": 604,
            "date": current_unix_timestamp(),
            "chat": {"id": 123456789, "type": "private"},
            "from": {"id": 987654321, "is_bot": false, "first_name": "Operator"},
            "text": "/remember retry delivery without duplicate effect"
        }
    });
    let (first_base, first_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": [update]}),
        json!({"ok": false, "description": "temporary send failure"}),
    ])?;
    let first_out = temp.path().join("first.json");
    let first = remote_operator_command(temp.path())
        .arg("--once")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--conversation-file")
        .arg(&conversation_path)
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&first_out)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", first_base)
        .output()?;
    assert!(first.status.success());
    let first_result: Value = serde_json::from_slice(&fs::read(&first_out)?)?;
    assert_eq!(first_result["status"], "send_failed");
    assert_eq!(first_result["update_journal_status"], "effect_committed");
    assert_eq!(first_result["reply_delivery_pending"], true);
    assert_eq!(first_result["action_outcome"]["disposition"], "succeeded");
    assert_eq!(first_result["action_outcome"]["result_status"], "rendered");
    assert_eq!(
        first_result["response_card"]["outcome_id"],
        first_result["action_outcome"]["outcome_id"]
    );
    let first_requests = first_server.join().expect("first fake Telegram server")?;
    assert!(first_requests[0].contains("/getUpdates"));
    assert!(first_requests[1].contains("/sendMessage"));

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(state["offset"], 9_231);
    let candidates: Value = serde_json::from_slice(&fs::read(
        profile_dir(temp.path()).join("adaptive_wiki_candidates.json"),
    )?)?;
    assert_eq!(
        candidates["candidates"]
            .as_array()
            .expect("candidates")
            .len(),
        1
    );
    assert_eq!(candidates["candidates"][0]["occurrence_count"], 1);

    let (second_base, second_server) = spawn_fake_telegram(vec![
        json!({"ok": true, "result": {"message_id": 777}}),
        json!({"ok": true, "result": []}),
    ])?;
    let second_out = temp.path().join("second.json");
    let second = remote_operator_command(temp.path())
        .arg("--once")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--update-journal-file")
        .arg(&journal_path)
        .arg("--reply-outbox-file")
        .arg(&outbox_path)
        .arg("--conversation-file")
        .arg(&conversation_path)
        .arg("--poll-timeout-sec")
        .arg("0")
        .arg("--api-timeout-sec")
        .arg("2")
        .arg("--out")
        .arg(&second_out)
        .env("OFFDESK_REMOTE_OPERATOR_TELEGRAM_API_BASE_URL", second_base)
        .output()?;
    assert!(
        second.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second.stdout),
        String::from_utf8_lossy(&second.stderr)
    );
    let second_result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(second_result["status"], "reply_delivered");
    assert_eq!(second_result["reply_outbox_delivery"]["delivered_count"], 1);
    let second_requests = second_server.join().expect("second fake Telegram server")?;
    assert!(second_requests[0].contains("/sendMessage"));
    assert!(second_requests[1].contains("/getUpdates"));

    let candidates_after: Value = serde_json::from_slice(&fs::read(
        profile_dir(temp.path()).join("adaptive_wiki_candidates.json"),
    )?)?;
    assert_eq!(candidates_after["candidates"][0]["occurrence_count"], 1);
    let completed: Value = serde_json::from_str(
        fs::read_to_string(&journal_path)?
            .lines()
            .last()
            .expect("completed journal row"),
    )?;
    assert_eq!(completed["status"], "completed");
    assert_eq!(completed["action_outcome"], first_result["action_outcome"]);
    assert_eq!(completed["response_card"], first_result["response_card"]);
    let delivered: Value = serde_json::from_str(
        fs::read_to_string(&outbox_path)?
            .lines()
            .last()
            .expect("delivered outbox row"),
    )?;
    assert_eq!(delivered["status"], "delivered");
    assert_eq!(delivered["action_outcome"], first_result["action_outcome"]);
    assert_eq!(delivered["response_card"], first_result["response_card"]);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_blocks_unresolvable_registered_projects() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let empty_workspace = temp.path().join("empty-workspace");
    fs::create_dir_all(&empty_workspace)?;
    let status_path = temp.path().join("loop_status.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "poll_count": 1,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    let out = temp.path().join("health_projects.json");

    let output = remote_operator_command(temp.path())
        .arg("--health")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--workspace-root")
        .arg(&empty_workspace)
        .arg("--health-max-age-sec")
        .arg("999999999")
        .arg("--out")
        .arg(&out)
        .output()?;

    assert_eq!(output.status.code(), Some(0));
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["health_status"], "degraded");
    assert_eq!(result["registered_project_count"], 1);
    assert_eq!(result["resolvable_project_count"], 0);
    let project_scan = result["action_readiness"]
        .as_array()
        .expect("action readiness")
        .iter()
        .find(|item| item["action"] == "project_scan")
        .expect("project scan readiness");
    assert_eq!(project_scan["status"], "blocked");
    assert_eq!(project_scan["reason"], "registered_projects_unresolvable");
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
        output.status.success(),
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
    assert_eq!(result["action_readiness"][4]["action"], "session_message");
    assert_eq!(result["action_readiness"][4]["status"], "blocked");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_health_reports_recent_poll_transport_error() -> Result<()> {
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
            "poll_count": 8,
            "updates_seen": 2,
            "handled_result_count": 1,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "poll_error",
                "reason": "telegram_transport_error",
                "error": "Telegram API transport error (getUpdates): TimeoutError"
            },
            "last_handled_result": {
                "status": "rendered"
            }
        }))?,
    )?;
    let out = temp.path().join("health_poll_error.json");

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
        !output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["health_status"], "unhealthy");
    assert_eq!(result["last_result_status"], "poll_error");
    assert!(result["transport_issues"]
        .as_array()
        .expect("transport issues")
        .contains(&json!("last_poll_transport_error")));
    assert_eq!(result["action_readiness"][0]["status"], "blocked");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_watchdog_reports_stale_listener_without_listener_process() -> Result<()> {
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
            "poll_count": 11,
            "updates_seen": 3,
            "handled_result_count": 2,
            "last_result": {
                "generated_at": "2000-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    let out = temp.path().join("watchdog.json");

    let output = watchdog_command(temp.path())
        .arg("--dry-run")
        .arg("--systemd-mode")
        .arg("off")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--health-max-age-sec")
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
    assert_eq!(result["schema"], "remote_operator_telegram_watchdog.v1");
    assert_eq!(result["health_status"], "unhealthy");
    assert!(result["issues"]
        .as_array()
        .expect("issues")
        .contains(&json!("last_poll_stale")));
    assert_eq!(result["listener"]["listener_status"], "polling");
    assert_eq!(result["systemd"]["active_state"], "not_checked");
    assert_eq!(result["alert"]["needed"], true);
    assert_eq!(result["alert"]["sent"], false);
    assert_eq!(result["alert"]["reason"], "dry_run");
    assert!(result["alert"]["line_count"].as_u64().unwrap() <= 5);
    assert!(result["alert"]["char_count"].as_u64().unwrap() <= 360);
    let message = result["alert"]["message_preview"]
        .as_str()
        .expect("message preview");
    assert!(message.contains("Remote Operator 고장"));
    assert!(message.contains("원격 제어: 확인 필요"));
    assert!(message.contains("listener health와 통신 상태 확인"));
    assert!(!message.contains("restart"));
    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains("fake-token-for-test"));
    assert!(!serialized.contains("999999:"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_watchdog_rate_limits_repeated_alerts() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let status_path = temp.path().join("loop_status.json");
    let state_path = temp.path().join("watchdog_state.json");
    fs::write(
        &status_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_adapter_result.v1",
            "mode": "live_loop",
            "status": "polling",
            "last_result": {
                "generated_at": "2000-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    fs::write(
        &state_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_watchdog_state.v1",
            "last_alert_key": "last_poll_stale",
            "last_alert_at": "2099-01-01T00:00:00+00:00"
        }))?,
    )?;
    let out = temp.path().join("watchdog_rate_limited.json");

    let output = watchdog_command(temp.path())
        .arg("--dry-run")
        .arg("--systemd-mode")
        .arg("off")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--health-max-age-sec")
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
    assert_eq!(result["health_status"], "unhealthy");
    assert_eq!(result["alert"]["needed"], true);
    assert_eq!(result["alert"]["suppressed"], true);
    assert_eq!(result["alert"]["reason"], "rate_limited");
    assert_eq!(result["alert"]["sent"], false);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_watchdog_accepts_fresh_listener() -> Result<()> {
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
            "poll_count": 11,
            "last_result": {
                "generated_at": "2099-01-01T00:00:00+00:00",
                "status": "no_update"
            }
        }))?,
    )?;
    let out = temp.path().join("watchdog_healthy.json");

    let output = watchdog_command(temp.path())
        .arg("--dry-run")
        .arg("--systemd-mode")
        .arg("off")
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
    assert_eq!(result["health_status"], "healthy");
    assert_eq!(result["issues"], json!([]));
    assert_eq!(result["alert"]["needed"], false);
    assert_eq!(result["alert"]["reason"], "healthy");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_watchdog_transient_transport_failure_is_warning_without_state_write(
) -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let status_path = temp.path().join("loop_status.json");
    let state_path = temp.path().join("watchdog_state.json");
    let now = chrono::Utc::now();
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "status": "polling",
            "consecutive_error_count": 1,
            "last_success_at": (now - chrono::Duration::seconds(20)).to_rfc3339(),
            "last_result": {"generated_at": now.to_rfc3339(), "status": "poll_error"}
        }))?,
    )?;
    let output = watchdog_command(temp.path())
        .args(["--dry-run", "--systemd-mode", "off", "--env-file"])
        .arg(&env_path)
        .arg("--loop-status-file")
        .arg(&status_path)
        .arg("--state-file")
        .arg(&state_path)
        .output()?;
    assert!(output.status.success(), "{:?}", output);
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result["health_status"], "degraded");
    assert_eq!(result["issues"], json!([]));
    assert_eq!(result["warnings"], json!(["last_poll_transport_error"]));
    assert_eq!(result["alert"]["needed"], false);
    assert!(!state_path.exists());
    Ok(())
}

#[test]
#[serial]
fn telegram_operator_systemd_installer_dry_run_renders_unit() -> Result<()> {
    let temp = tempdir()?;
    let workspace_root = temp.path().join("Desktop/Workspace");
    let repo_root = workspace_root.join("98.Harness/forager-cli");
    fs::create_dir_all(&repo_root)?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("service.json");

    let output = Command::new("python3")
        .arg(script_path("install_offdesk_telegram_operator_service.py"))
        .arg("--dry-run")
        .arg("--service-name")
        .arg("forager-test-telegram")
        .arg("--repo-root")
        .arg(&repo_root)
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
    assert_eq!(result["service_name"], "forager-test-telegram.service");
    assert!(result["service_path"]
        .as_str()
        .expect("service path")
        .ends_with("forager-test-telegram.service"));
    let unit = result["unit_preview"].as_str().expect("unit preview");
    assert!(unit.contains("ExecStart="));
    assert!(unit.contains("offdesk_remote_operator_telegram.py"));
    assert!(unit.contains("--poll-timeout-sec 30"));
    assert!(unit.contains("--poll-error-backoff-sec 5"));
    assert!(unit.contains("--listener-lock-file"));
    assert!(unit.contains("--update-journal-file"));
    assert!(unit.contains("--reply-outbox-file"));
    assert!(unit.contains("--workspace-root"));
    assert!(unit.contains(workspace_root.to_str().expect("workspace root")));
    assert!(unit.contains("UMask=0077"));
    // Proactive attention notification is enabled by default for deployments.
    assert!(unit.contains("--attention-notify"));
    assert!(unit.contains("StartLimitIntervalSec=60"));
    assert!(unit.contains("StartLimitBurst=5"));
    assert!(unit.contains("Restart=on-failure"));
    assert!(!unit.contains("fake-token-for-test"));
    assert!(unit.contains(".local/share/forager/scripts"));
    assert!(
        !temp.path().join(".local/share/forager/scripts").exists(),
        "dry-run must not deploy scripts"
    );
    Ok(())
}

#[test]
#[serial]
fn telegram_operator_systemd_installer_dry_run_renders_watchdog_timer() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;

    let output = Command::new("python3")
        .arg(script_path("install_offdesk_telegram_operator_service.py"))
        .arg("--dry-run")
        .arg("--include-watchdog")
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
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result["watchdog_included"], true);
    assert_eq!(result["watchdog_installed"], false);
    let service_unit = result["watchdog_service_unit_preview"]
        .as_str()
        .expect("watchdog service unit");
    let timer_unit = result["watchdog_timer_unit_preview"]
        .as_str()
        .expect("watchdog timer unit");
    assert!(service_unit.contains("Type=oneshot"));
    assert!(service_unit.contains("offdesk_remote_operator_watchdog.py"));
    assert!(service_unit.contains(".local/share/forager/scripts"));
    assert!(service_unit.contains("--systemd-mode required"));
    assert!(service_unit.contains("--alert-min-interval-sec 1800"));
    assert!(timer_unit.contains("OnBootSec=2min"));
    assert!(timer_unit.contains("OnUnitActiveSec=120s"));
    assert!(timer_unit.contains("WantedBy=timers.target"));
    let serialized = serde_json::to_string(&result)?;
    assert!(!serialized.contains("fake-token-for-test"));
    assert!(!serialized.contains("999999:"));
    assert!(
        !temp.path().join(".local/share/forager/scripts").exists(),
        "dry-run must not deploy watchdog scripts"
    );
    Ok(())
}

#[test]
#[serial]
fn telegram_operator_systemd_installer_refuses_dirty_source_deploy() -> Result<()> {
    let temp = tempdir()?;
    let repo_root = temp.path().join("repo");
    fs::create_dir_all(&repo_root)?;
    let git_init = Command::new("git")
        .args(["init", "-q"])
        .current_dir(&repo_root)
        .output()?;
    assert!(git_init.status.success());
    fs::write(repo_root.join("dirty.txt"), "uncommitted\n")?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;

    let output = Command::new("python3")
        .arg(script_path("install_offdesk_telegram_operator_service.py"))
        .arg("--install")
        .arg("--repo-root")
        .arg(&repo_root)
        .arg("--forager-bin")
        .arg(temp.path().join("forager"))
        .arg("--env-file")
        .arg(&env_path)
        .env("HOME", temp.path())
        .output()?;

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("dirty Git checkout"));
    assert!(!temp.path().join(".local/share/forager/scripts").exists());
    assert!(!temp.path().join(".config/systemd/user").exists());
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
        false
    );
    assert_eq!(result["reply_markup_preview"], Value::Null);
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
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        false
    );
    assert_eq!(result["reply_markup_preview"], Value::Null);
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
    assert_eq!(
        result["choice_surface_contract"]["has_contextual_choice"],
        false
    );
    assert_eq!(result["reply_markup_preview"], Value::Null);
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_prompt_includes_command_surface_and_snapshot() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("grounded_chat.json");
    let agent_request_path = temp.path().join("ollama_grounded_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "chat",
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "대기 중인 결정은 없습니다.",
            "reason": "State question answered from the operator snapshot.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("마지막 상태를 먼저 읽어볼까")
        .arg("--forager-bin")
        .arg(temp.path().join("missing-forager"))
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
    assert_eq!(result["parsed_command"]["command"], "chat");

    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    let prompt = agent_request["prompt"].as_str().expect("agent prompt");
    // The chat agent must receive the live snapshot and the complete command
    // surface so it answers from real state and never invents slash commands.
    assert!(prompt.contains("operator_snapshot"));
    assert!(prompt.contains("supported_commands"));
    assert!(prompt.contains("autonomy_armed"));
    assert!(prompt.contains("workspace_projects"));
    assert!(prompt.contains("/remember"));
    assert!(prompt.contains("Never mention, suggest, or invent a slash command"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_snapshot_includes_shared_shift_review() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let fake_forager = temp.path().join("fake-forager.py");
    fs::write(
        &fake_forager,
        r#"#!/usr/bin/env python3
import json
import os
import pathlib
import sys

pathlib.Path(os.environ["FAKE_FORAGER_ARGS_LOG"]).open("a").write(json.dumps(sys.argv[1:]) + "\n")

if "workstation-surface" in sys.argv:
    print(json.dumps({
        "schema": "workstation_surface.v1",
        "generated_at": "2026-08-29T08:30:00Z",
        "attention_counts": {"shift_review_reasons": 1},
        "capacity": {},
        "health": [],
        "redaction": {"operator_safe": True},
        "decision_inbox": {"items": []},
        "offdesk_shift_review": {
            "shift_id": "shift_chat_review_1",
            "review_status": "needs_attention",
            "lifecycle_status": "review_ready",
            "attention_reasons": ["closeout_review_required"],
            "summary": {
                "task_attempts_reserved": 1,
                "task_attempts_terminal": 1,
                "routine_runs_reserved": 2,
                "routine_runs_terminal": 2,
                "closeout_required": 1,
                "wiki_candidates": 2
            },
            "authority": {
                "review_only": True,
                "accepted_truth_authorized": False,
                "plan_revision_authorized": False,
                "wiki_promotion_authorized": False
            }
        }
    }))
elif "shift" in sys.argv and "status" in sys.argv:
    print(json.dumps({
        "schema": "offdesk_shift_status_report.v1",
        "active_shift_id": None,
        "shifts": []
    }))
else:
    print(json.dumps({"sessions": []}))
"#,
    )?;
    #[cfg(unix)]
    fs::set_permissions(&fake_forager, fs::Permissions::from_mode(0o700))?;
    let out = temp.path().join("shared_shift_review_chat.json");
    let forager_args_log = temp.path().join("fake_forager_args.jsonl");
    let agent_request_path = temp.path().join("ollama_shared_shift_review_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "chat",
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "교대 작업은 종료됐지만 closeout 검토가 남아 있습니다.",
            "reason": "Shared shift review requires attention.",
            "non_authorized": ["execution", "approval", "accepted_truth"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("오늘 아침 교대 작업 상태를 알려줘")
        .arg("--forager-bin")
        .arg(&fake_forager)
        .arg("--shift-review-id")
        .arg("shift_chat_review_1")
        .arg("--shift-review-current-run-id")
        .arg("run_chat_review_1")
        .arg("--workspace-root")
        .arg(temp.path())
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
        .env("FAKE_FORAGER_ARGS_LOG", &forager_args_log)
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    let prompt = agent_request["prompt"].as_str().expect("agent prompt");
    assert!(prompt.contains("offdesk_shift_review"));
    assert!(prompt.contains("shift_chat_review_1"));
    assert!(prompt.contains("closeout_review_required"));
    assert!(prompt.contains("accepted_truth_authorized"));
    let argv_rows = fs::read_to_string(forager_args_log)?;
    assert!(argv_rows.lines().any(|line| {
        line.contains("workstation-surface")
            && line.contains("--shift-id")
            && line.contains("shift_chat_review_1")
            && line.contains("--current-run-id")
            && line.contains("run_chat_review_1")
    }));
    Ok(())
}

fn write_project_registry(home: &Path) -> Result<()> {
    let dir = home.join(".config/forager");
    fs::create_dir_all(&dir)?;
    fs::write(
        dir.join("projects.toml"),
        r#"schema = "forager_project_registry.v1"

[projects.twinpaper]
display_name = "TwinPaper"
aliases = ["트윈페이퍼"]
workspace_patterns = ["1.2.8.TwinPaper"]
wiki_profile = "twinpaper-review"
"#,
    )?;
    Ok(())
}

fn write_ambiguous_project_registry(home: &Path) -> Result<()> {
    let dir = home.join(".config/forager");
    fs::create_dir_all(&dir)?;
    fs::write(
        dir.join("projects.toml"),
        r#"schema = "forager_project_registry.v1"

[projects.alpha]
display_name = "Alpha"
aliases = ["공통프로젝트"]
workspace_patterns = ["Alpha"]
wiki_profile = "alpha-wiki"

[projects.beta]
display_name = "Beta"
aliases = ["공통프로젝트"]
workspace_patterns = ["Beta"]
wiki_profile = "beta-wiki"
"#,
    )?;
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_ambiguous_project_alias_never_selects_a_writer() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_ambiguous_project_registry(temp.path())?;
    let state_path = temp.path().join("telegram_state.json");

    let chat_out = temp.path().join("chat.json");
    let chat = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("공통프로젝트 상태를 확인해줘")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&chat_out)
        .output()?;
    assert!(chat.status.success());
    let chat_result: Value = serde_json::from_slice(&fs::read(&chat_out)?)?;
    assert!(chat_result["project_focus"].is_null());
    assert_eq!(chat_result["project_resolution"]["status"], "ambiguous");
    assert_eq!(
        chat_result["project_resolution"]["candidates"],
        json!(["alpha", "beta"])
    );
    assert_eq!(
        chat_result["parsed_command"]["agent_intent"]["reason"],
        "ambiguous_project_reference"
    );

    let wiki_out = temp.path().join("wiki.json");
    let wiki = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/wiki 공통프로젝트")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&wiki_out)
        .output()?;
    assert!(wiki.status.success());
    let wiki_result: Value = serde_json::from_slice(&fs::read(&wiki_out)?)?;
    assert_eq!(wiki_result["wiki_summary"]["status"], "ambiguous_project");
    assert!(wiki_result["message_preview"]
        .as_str()
        .expect("wiki preview")
        .contains("프로젝트 키: alpha, beta"));

    let update_path = temp.path().join("remember.json");
    write_text_update(
        &update_path,
        10_100,
        20_100,
        "/remember 공통프로젝트 규칙을 저장한다",
    )?;
    let remember_out = temp.path().join("remember_result.json");
    let remember = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&remember_out)
        .output()?;
    assert!(remember.status.success());
    let remember_result: Value = serde_json::from_slice(&fs::read(&remember_out)?)?;
    assert_eq!(remember_result["wiki_candidate_recorded"], false);
    assert_eq!(remember_result["wiki_candidate_status"], "blocked");
    assert_eq!(
        remember_result["wiki_candidate_error"],
        "ambiguous_project_reference"
    );
    assert!(remember_result["message_preview"]
        .as_str()
        .expect("remember preview")
        .contains("아직 위키 후보를 저장하지 않았습니다"));
    assert!(!profile_dir(temp.path())
        .join("adaptive_wiki_candidates.json")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_ascii_project_mentions_require_token_boundaries() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let out = temp.path().join("boundary.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("Twinpapership이라는 일반 단어를 설명해줘")
        .arg("--env-file")
        .arg(&env_path)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(output.status.success());
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert!(result["project_focus"].is_null());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_korean_project_alias_overrides_focus() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let out = temp.path().join("alias_focus.json");
    let request_path = temp.path().join("ollama_alias_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        request_path,
        json!({
            "action": "answer",
            "assistant_reply": "트윈페이퍼 상태를 확인했습니다.",
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "reason": "Project alias resolved."
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("트윈페이퍼 마지막 상태를 확인해줘")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["project_focus"]["key"], "twinpaper");
    assert_eq!(result["project_focus"]["source"], "mention");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_projects_lists_only_resolved_access_as_available() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let workspace_root = temp.path().join("workspace");
    fs::create_dir_all(workspace_root.join("1.2.8.TwinPaper"))?;
    let out = temp.path().join("projects.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/projects 트윈페이퍼")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["parsed_command"]["command"], "projects");
    assert_eq!(result["project_focus"]["key"], "twinpaper");
    assert_eq!(result["project_summary"]["registered_count"], 1);
    assert_eq!(result["project_summary"]["available_count"], 1);
    assert_eq!(
        result["project_summary"]["comparison_schema"],
        "telegram_project_portfolio_comparison.v1"
    );
    assert_eq!(result["project_summary"]["read_only"], true);
    assert_eq!(result["project_summary"]["mutation_authorized"], false);
    assert_eq!(result["project_summary"]["approval_authorized"], false);
    assert_eq!(result["project_summary"]["execution_authorized"], false);
    assert_eq!(result["project_summary"]["enqueue_authorized"], false);
    assert_eq!(result["project_summary"]["launch_authorized"], false);
    assert_eq!(result["project_summary"]["runtime_authorized"], false);
    assert_eq!(
        result["project_summary"]["accepted_truth_authorized"],
        false
    );
    assert!(result["message_preview"]
        .as_str()
        .is_some_and(|text| text.contains("작업공간: 접근 가능")));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_projects_compare_registered_workspaces_read_only() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_ambiguous_project_registry(temp.path())?;
    let workspace_root = temp.path().join("workspace");
    let alpha = workspace_root.join("Alpha");
    let beta = workspace_root.join("Beta");
    fs::create_dir_all(&alpha)?;
    fs::create_dir_all(&beta)?;
    fs::write(alpha.join("README.md"), "Alpha\n")?;
    fs::write(
        alpha.join("Cargo.toml"),
        "[package]\nname = \"alpha\"\nversion = \"0.1.0\"\n",
    )?;
    let git_init = Command::new("git")
        .args(["init", "-q"])
        .current_dir(&alpha)
        .output()?;
    assert!(git_init.status.success());
    let git_add = Command::new("git")
        .args(["add", "."])
        .current_dir(&alpha)
        .output()?;
    assert!(git_add.status.success());
    let git_commit = Command::new("git")
        .args([
            "-c",
            "user.name=Forager Test",
            "-c",
            "user.email=forager@example.invalid",
            "commit",
            "-qm",
            "initial",
        ])
        .current_dir(&alpha)
        .output()?;
    assert!(git_commit.status.success());
    fs::write(beta.join("README.md"), "Beta\n")?;
    fs::write(
        beta.join("pyproject.toml"),
        "[project]\nname = \"beta\"\nversion = \"0.1.0\"\n",
    )?;
    let out = temp.path().join("projects_comparison.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/projects")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
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
    let summary = &result["project_summary"];
    assert_eq!(summary["schema"], "telegram_project_portfolio.v1");
    assert_eq!(
        summary["comparison_schema"],
        "telegram_project_portfolio_comparison.v1"
    );
    assert_eq!(summary["registered_count"], 2);
    assert_eq!(summary["available_count"], 2);
    assert_eq!(summary["recommended_project_key"], "alpha");
    assert_eq!(summary["comparison_leader_project_key"], "alpha");
    assert_eq!(summary["comparison_purpose"], "read_only_attention_ranking");
    assert_eq!(summary["read_only"], true);
    assert_eq!(summary["mutation_authorized"], false);
    assert_eq!(summary["approval_authorized"], false);
    assert_eq!(summary["execution_authorized"], false);
    assert_eq!(summary["enqueue_authorized"], false);
    assert_eq!(summary["launch_authorized"], false);
    assert_eq!(summary["runtime_authorized"], false);
    assert_eq!(summary["accepted_truth_authorized"], false);
    assert_eq!(summary["projects"][0]["key"], "alpha");
    assert_eq!(summary["projects"][0]["comparison_rank"], 1);
    assert_eq!(summary["projects"][0]["readiness"], "ready");
    assert_eq!(summary["projects"][0]["risk_level"], "low");
    assert_eq!(summary["projects"][0]["dirty_tree"], false);
    assert_eq!(
        summary["projects"][0]["available_tests"],
        json!(["cargo test", "cargo clippy"])
    );
    assert_eq!(summary["projects"][0]["remote_sync"], "unknown");
    assert_eq!(summary["projects"][0]["recommended"], true);
    assert_eq!(
        summary["projects"][0]["comparison_score"],
        summary["projects"][0]["comparison_components"]
            .as_object()
            .expect("comparison components")
            .values()
            .map(|value| value.as_i64().expect("component score"))
            .sum::<i64>()
    );
    assert_eq!(summary["projects"][1]["key"], "beta");
    assert_eq!(summary["projects"][1]["comparison_rank"], 2);
    assert_eq!(summary["projects"][1]["readiness"], "needs_review");
    assert_eq!(summary["projects"][1]["recent_failures_observed"], false);
    assert!(summary["projects"][1]["not_recommended_because"]
        .as_array()
        .expect("comparison differences")
        .iter()
        .any(|value| value == "different_readiness"));
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("1. alpha · 준비됨 · 위험 낮음"));
    assert!(preview.contains("2. beta · 검토 필요 · 위험 중간"));
    assert!(!preview.contains(workspace_root.to_str().expect("workspace root")));
    assert_mobile_contract(&result);
    Ok(())
}

fn record_wiki_candidate(home: &Path, profile: &str, claim: &str) -> Result<()> {
    let output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join(".config"))
        .args([
            "--profile",
            profile,
            "offdesk",
            "wiki",
            "record-candidate",
            "--kind",
            "fact",
            "--scope",
            "project",
            "--scope-ref",
            "twinpaper",
            "--claim",
            claim,
            "--evidence-ref",
            "telegram:test",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}

fn read_wiki_candidates(home: &Path, profile: &str) -> Result<Value> {
    let output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join(".config"))
        .args([
            "--profile",
            profile,
            "offdesk",
            "wiki",
            "candidates",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
#[serial]
fn remote_operator_telegram_wiki_lists_project_candidate_pressure() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    record_wiki_candidate(
        temp.path(),
        "twinpaper-review",
        "검토되지 않은 실행 결과를 정설로 승격하지 않는다",
    )?;
    let out = temp.path().join("wiki_portfolio.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/wiki")
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
    assert_eq!(result["parsed_command"]["command"], "wiki");
    assert_eq!(result["wiki_summary"]["candidate_count"], 1);
    assert_eq!(result["wiki_summary"]["projects_with_candidates"], 1);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("후보 1개"));
    assert!(preview.contains("twinpaper 1"));
    assert!(preview.contains("/wiki &lt;project&gt;"));
    assert_eq!(result["reply_markup_preview"], Value::Null);
    assert_eq!(result["choice_surface_contract"]["surface_kind"], "none");
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_remember_uses_sticky_projects_wiki_profile() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let state_path = temp.path().join("telegram_state.json");

    let focus_update = temp.path().join("focus_update.json");
    write_text_update(&focus_update, 800, 1000, "/wiki twinpaper")?;
    let focus_out = temp.path().join("focus_result.json");
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&focus_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&focus_out)
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let focused: Value = serde_json::from_slice(&fs::read(&focus_out)?)?;
    assert_eq!(focused["project_focus"]["key"], "twinpaper");
    assert_eq!(focused["project_focus"]["source"], "explicit");

    let remember_update = temp.path().join("remember_update.json");
    write_text_update(
        &remember_update,
        801,
        1001,
        "/remember 근거와 결론을 분리해서 검토한다",
    )?;
    let remember_out = temp.path().join("remember_result.json");
    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&remember_update)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--state-file")
        .arg(&state_path)
        .arg("--out")
        .arg(&remember_out)
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let remembered: Value = serde_json::from_slice(&fs::read(&remember_out)?)?;
    assert_eq!(
        remembered["parsed_command"]["wiki_profile"],
        "twinpaper-review"
    );
    assert_eq!(remembered["project_focus"]["key"], "twinpaper");
    assert_eq!(remembered["project_focus"]["source"], "sticky");
    assert_eq!(remembered["wiki_candidate_status"], "recorded");
    assert!(remembered["message_preview"]
        .as_str()
        .expect("message preview")
        .contains("twinpaper-review"));

    let project_candidates = read_wiki_candidates(temp.path(), "twinpaper-review")?;
    assert_eq!(
        project_candidates.as_array().expect("candidate list").len(),
        1
    );
    assert_eq!(
        project_candidates[0]["claim"],
        "근거와 결론을 분리해서 검토한다"
    );
    assert_eq!(project_candidates[0]["scope"], "project");
    assert_eq!(project_candidates[0]["scope_ref"], "twinpaper");
    let default_candidates = read_wiki_candidates(temp.path(), "default")?;
    assert!(default_candidates
        .as_array()
        .expect("default candidate list")
        .is_empty());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_grounds_mentioned_project_focus() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let out = temp.path().join("focused_chat.json");
    let agent_request_path = temp.path().join("ollama_focus_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "chat",
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "TwinPaper 세션은 모두 중지 상태입니다.",
            "reason": "Project question answered from project_focus.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("좋아. Twinpaper 프로젝트 마지막 상태 리뷰해줘")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
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
    assert_eq!(result["project_focus"]["key"], "twinpaper");
    assert_eq!(result["project_focus"]["source"], "mention");

    let agent_request: Value = serde_json::from_slice(&fs::read(&agent_request_path)?)?;
    let prompt = agent_request["prompt"].as_str().expect("agent prompt");
    // The agent must see the mentioned project's live state, not just the
    // registry row, so it answers with concrete session/wiki facts.
    assert!(prompt.contains("project_focus"));
    assert!(prompt.contains("\"focus_source\": \"mention\""));
    assert!(prompt.contains("TwinPaper"));
    assert!(prompt.contains("session_counts"));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_focus_sticks_across_messages() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let state_path = temp.path().join("telegram_state.json");
    let feedback_file = temp.path().join("feedback.jsonl");
    let ingest_dir = temp.path().join("feedback_ingest");

    let replay = |update_path: &Path, out: &Path| -> Result<std::process::Output> {
        Ok(remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(update_path)
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
            .arg(out)
            .output()?)
    };

    let mention_update = temp.path().join("mention_update.json");
    write_text_update(&mention_update, 720, 900, "Twinpaper 마지막 기록 리뷰해줘")?;
    let mention_out = temp.path().join("mention_result.json");
    let output = replay(&mention_update, &mention_out)?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let mention: Value = serde_json::from_slice(&fs::read(&mention_out)?)?;
    assert_eq!(mention["parsed_command"]["command"], "chat");
    assert_eq!(mention["project_focus"]["key"], "twinpaper");
    assert_eq!(mention["project_focus"]["source"], "mention");
    let chat_hash = mention["target_chat_id_hash"].as_str().expect("chat hash");
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(
        state["chat_focus_by_chat"][chat_hash]["project_key"],
        "twinpaper"
    );

    let followup_update = temp.path().join("followup_update.json");
    write_text_update(&followup_update, 721, 901, "지금 상태 어때")?;
    let followup_out = temp.path().join("followup_result.json");
    let output = replay(&followup_update, &followup_out)?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let followup: Value = serde_json::from_slice(&fs::read(&followup_out)?)?;
    assert_eq!(followup["parsed_command"]["command"], "chat");
    // The follow-up drops the project name; the chat's sticky focus keeps
    // the conversation about the same project.
    assert_eq!(followup["project_focus"]["key"], "twinpaper");
    assert_eq!(followup["project_focus"]["source"], "sticky");
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_stale_project_focus_expires() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let state_path = temp.path().join("telegram_state.json");
    let chat_hash = "sha256:9dd7fefaf214ceca";
    fs::write(
        &state_path,
        serde_json::to_string_pretty(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "chat_focus_by_chat": {
                chat_hash: {
                    "project_key": "twinpaper",
                    "updated_at": "2026-01-01T00:00:00+00:00"
                }
            }
        }))?,
    )?;
    let update_path = temp.path().join("stale_focus_update.json");
    write_text_update(&update_path, 880, 980, "지금 상태 어때")?;
    let out = temp.path().join("stale_focus_result.json");

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
        .arg("--project-focus-max-age-sec")
        .arg("60")
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert!(result["project_focus"].is_null());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_agent_delegate_intent_offers_capture_without_keywords() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let agent_request_path = temp.path().join("ollama_delegate_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "delegate_work",
            "delegation_goal": "TwinPaper 논문 그림을 재정리한다",
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "계획 후보로 잡아둘게요.",
            "reason": "Operator handed over work in natural language.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;
    let update_path = temp.path().join("delegate_update.json");
    // No deterministic planning keyword anywhere in this message: only the
    // agent's structured decision can route it to the capture card.
    write_text_update(
        &update_path,
        730,
        910,
        "트윈페이퍼 논문 그림 다시 정리해줘야겠다",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("delegate_result.json");

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
        .arg(temp.path().join("feedback.jsonl"))
        .arg("--feedback-ingest-dir")
        .arg(temp.path().join("feedback_ingest"))
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
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
    assert_eq!(result["plan_capture_offered"], true);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 후보로 등록할까요?</b>"));
    // The card carries the agent's restated goal, not the raw message.
    assert!(preview.contains("TwinPaper 논문 그림을 재정리한다"));
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = result["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["pending_dispatch_confirmations_by_chat"][chat_hash]["note"],
        "TwinPaper 논문 그림을 재정리한다"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_plain_text_reaches_selected_local_agent() -> Result<()> {
    if Command::new("tmux").arg("-V").output().is_err() {
        eprintln!("Skipping test: tmux not available");
        return Ok(());
    }
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let prefix = format!("{:08x}", std::process::id());
    let session_id = format!("{prefix}00000000");
    let tmux_name = format!("forager_test_{prefix}");
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();
    let started = Command::new("tmux")
        .args(["new-session", "-d", "-s", &tmux_name, "bash --norc -i"])
        .output()?;
    assert!(started.status.success(), "failed to create tmux session");
    thread::sleep(Duration::from_millis(500));

    let status_path = temp.path().join("session_status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{
                "id": session_id,
                "title": "Codex",
                "tool": "codex",
                "project": "forager",
                "status": "running"
            }]
        }))?,
    )?;
    let agent_request_path = temp.path().join("ollama_session_message_request.json");
    let delivered_text = "please rerun all tests and report the result";
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "action": "send_agent",
            "session_id": session_id,
            "message": delivered_text,
            "assistant_reply": "forager Codex에 전달합니다.",
            "confidence": 0.96,
            "requires_clarification": false,
            "clarifying_question": null,
            "reason": "operator explicitly addressed the live Codex session"
        }),
    )?;
    let update_path = temp.path().join("session_message_update.json");
    write_text_update(
        &update_path,
        9_400,
        9_401,
        "forager Codex에게 전체 테스트를 다시 돌리고 결과를 알려달라고 전해줘",
    )?;
    let out = temp.path().join("session_message_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
        .output()?;
    server.join().expect("fake ollama server panicked")?;
    thread::sleep(Duration::from_millis(250));
    let capture = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", &tmux_name])
        .output()?;
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(
        result["parsed_command"]["agent_intent"]["intent"],
        "session_message"
    );
    assert_eq!(result["dispatch_result"]["ok"], true);
    assert_eq!(result["dispatch_result"]["kind"], "session_message");
    assert_effect(&result, "session_message", "agent_session_input");
    assert!(result["message_preview"]
        .as_str()
        .expect("message preview")
        .contains("로컬 에이전트에 입력 전송됨"));
    assert!(String::from_utf8_lossy(&capture.stdout).contains(delivered_text));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_literal_relay_reaches_selected_local_agent_without_model() -> Result<()>
{
    if Command::new("tmux").arg("-V").output().is_err() {
        eprintln!("Skipping test: tmux not available");
        return Ok(());
    }
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let prefix = format!("{:08x}", std::process::id());
    let session_id = format!("{prefix}00000000");
    let tmux_name = format!("forager_test_{prefix}");
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();
    let started = Command::new("tmux")
        .args(["new-session", "-d", "-s", &tmux_name, "bash --norc -i"])
        .output()?;
    assert!(started.status.success(), "failed to create tmux session");
    thread::sleep(Duration::from_millis(500));

    let status_path = temp.path().join("session_status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{
                "id": session_id,
                "title": "Codex",
                "tool": "codex",
                "project": "forager",
                "status": "running"
            }]
        }))?,
    )?;
    let delivered_text = "그럼 다음 작업은 뭘 하면 좋을까?";
    let update_path = temp.path().join("session_message_update.json");
    write_text_update(
        &update_path,
        9_400,
        9_401,
        "forager:Codex 에게 아래처럼 입력해줘.\n\n그럼 다음 작업은 뭘 하면 좋을까?",
    )?;
    let out = temp.path().join("session_message_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--once")
        .arg("--replay-update-file")
        .arg(&update_path)
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--agent-intent-mode")
        .arg("off")
        .arg("--out")
        .arg(&out)
        .output()?;
    thread::sleep(Duration::from_millis(250));
    let capture = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", &tmux_name])
        .output()?;
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(
        result["parsed_command"]["agent_intent"]["intent"],
        "session_message"
    );
    assert_eq!(result["dispatch_result"]["ok"], true);
    assert_eq!(result["dispatch_result"]["kind"], "session_message");
    assert_effect(&result, "session_message", "agent_session_input");
    assert!(result["message_preview"]
        .as_str()
        .expect("message preview")
        .contains("로컬 에이전트에 입력 전송됨"));
    assert!(String::from_utf8_lossy(&capture.stdout).contains(delivered_text));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_inline_continue_is_prompt_and_identity_bound() -> Result<()> {
    if Command::new("tmux").arg("-V").output().is_err() {
        eprintln!("Skipping test: tmux not available");
        return Ok(());
    }
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let prefix = format!("{:08x}", std::process::id());
    let session_id = format!("{prefix}11111111");
    let tmux_name = format!("forager_continue_{prefix}");
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();
    let started = Command::new("tmux")
        .args(["new-session", "-d", "-s", &tmux_name, "bash --norc -i"])
        .output()?;
    assert!(started.status.success(), "failed to create tmux session");
    thread::sleep(Duration::from_millis(500));
    let primed = Command::new("tmux")
        .args([
            "send-keys",
            "-t",
            &tmux_name,
            "printf 'Implemented the current task and all tests pass.\\n›\\n'",
            "Enter",
        ])
        .output()?;
    assert!(primed.status.success(), "failed to prime task-ready prompt");
    thread::sleep(Duration::from_millis(250));
    let before = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", &tmux_name])
        .output()?;
    assert!(before.status.success(), "failed to capture tmux session");
    let prompt_hash = prompt_identity_from_tmux_capture(&before.stdout)?;
    let identity = remote_session_identity(&session_id, "forager", "codex");

    let status_path = temp.path().join("session_status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{
                "id": session_id,
                "title": "Codex",
                "tool": "codex",
                "project": "forager",
                "status": "waiting",
                "remote_session_identity": identity
            }]
        }))?,
    )?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "control_generation_sha256": default_test_control_generation(),
            "waiting_notified_by_session": {
                (session_id.clone()): {
                    "at": current_unix_timestamp(),
                    "cards": 1,
                    "message_id": 9_501,
                    "prompt_hash": prompt_hash,
                    "interaction_kind": "task_ready",
                    "project": "forager",
                    "title": "Codex",
                    "tool": "codex",
                    "remote_session_identity": identity
                }
            }
        }))?,
    )?;
    let update_path = temp.path().join("callback_update.json");
    write_callback_update(
        &update_path,
        9_500,
        9_501,
        &format!("fg1:c:{prefix}:{prompt_hash}"),
    )?;
    let out = temp.path().join("callback_result.json");

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
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--out")
        .arg(&out)
        .output()?;
    thread::sleep(Duration::from_millis(250));
    let after = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", &tmux_name])
        .output()?;
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", &tmux_name])
        .output();

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["parsed_command"]["command"], "session_continue");
    assert_eq!(
        result["remote_command_envelope"]["command_class"],
        "agent_session_control"
    );
    assert_eq!(result["dispatch_result"]["ok"], true);
    assert_eq!(result["dispatch_result"]["kind"], "session_message");
    assert_eq!(
        result["dispatch_result"]["remote_session_identity_sha256"],
        identity["binding_sha256"]
    );
    let pane = String::from_utf8_lossy(&after.stdout);
    assert!(
        pane.contains("다음 미완료 작업"),
        "pane did not receive continuation: {pane}"
    );
    // A new update from the same card must not enter the input path again,
    // even after the original pane has gone away.
    write_callback_update(
        &update_path,
        9_502,
        9_501,
        &format!("fg1:c:{prefix}:{prompt_hash}"),
    )?;
    let repeated = remote_operator_command(temp.path())
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
        .arg("--session-status-file")
        .arg(&status_path)
        .arg("--out")
        .arg(&out)
        .output()?;
    assert!(
        repeated.status.success(),
        "{}",
        String::from_utf8_lossy(&repeated.stderr)
    );
    let repeated: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(repeated["dispatch_result"]["error"], "already_processed");
    assert_eq!(repeated["effect"]["authorized"], false);
    assert!(repeated["message_preview"]
        .as_str()
        .unwrap()
        .contains("이미 처리된 버튼"));
    assert_eq!(
        repeated["reply_markup_preview"]["inline_keyboard"][0][0]["callback_data"],
        "fg1:s"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_agent_chat_intent_overrides_planning_keywords() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let agent_request_path = temp.path().join("ollama_question_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "chat",
            "delegation_goal": null,
            "confidence": 0.9,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "지금 처리 중인 작업은 없습니다.",
            "reason": "Question about work in progress, not a delegation.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;
    let update_path = temp.path().join("question_update.json");
    // Contains the deterministic marker '처리', but it is a question; the
    // agent's chat decision must win over the keyword fallback.
    write_text_update(&update_path, 731, 911, "지금 뭐 처리하고 있어?")?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("question_result.json");

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
        .arg(temp.path().join("feedback.jsonl"))
        .arg("--feedback-ingest-dir")
        .arg(temp.path().join("feedback_ingest"))
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
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
    assert_eq!(result["plan_capture_offered"], Value::Null);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>Forager 응답</b>"));
    assert!(preview.contains("지금 처리 중인 작업은 없습니다."));
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = result["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["pending_dispatch_confirmations_by_chat"][chat_hash],
        Value::Null
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_tools_probe_workspace_and_answer() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let workspace_root = temp.path().join("workspace");
    let project_dir = workspace_root.join("1.2.8.TwinPaper");
    fs::create_dir_all(project_dir.join("modules"))?;
    fs::write(
        project_dir.join("PROJECT_STATE.md"),
        "focus is module two\n",
    )?;
    fs::write(project_dir.join("README.md"), "TwinPaper\n")?;
    fs::write(
        project_dir.join("modules").join("next.md"),
        "next actions\n",
    )?;

    let body_dir = temp.path().join("agent_requests");
    fs::create_dir_all(&body_dir)?;
    let (base_url, server) = spawn_fake_ollama_with_sequence(
        body_dir.clone(),
        vec![
            // Legacy intent shape must still map to a workspace_overview call.
            json!({
                "intent": "inspect_project",
                "assistant_reply": null,
                "reason": "Operator asked for the project's local file state."
            }),
            json!({
                "action": "tool",
                "tool": "read_file",
                "project": "twinpaper",
                "path": "PROJECT_STATE.md",
                "reason": "Read the state document surfaced by the overview."
            }),
            json!({
                "action": "answer",
                "assistant_reply": "PROJECT_STATE.md 기준 현재 초점은 module two입니다.",
                "confidence": 0.9,
                "requires_clarification": false,
                "clarifying_question": null,
                "reason": "Answered from tool_results."
            }),
        ],
    )?;
    let out = temp.path().join("tools_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("Twinpaper 로컬 파일 상태 확인해봐")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--workspace-root")
        .arg(&workspace_root)
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
    assert_eq!(result["project_focus"]["key"], "twinpaper");
    assert_eq!(result["chat_tools_used"][0]["tool"], "workspace_overview");
    assert_eq!(result["chat_tools_used"][0]["status"], "ok");
    assert_eq!(result["chat_tools_used"][1]["tool"], "read_file");
    assert_eq!(result["chat_tools_used"][1]["status"], "ok");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("현재 초점은 module two입니다."));

    // Round 2 sees the overview results; round 3 sees the file content.
    let second_request: Value =
        serde_json::from_slice(&fs::read(body_dir.join("generate_1.json"))?)?;
    let second_prompt = second_request["prompt"].as_str().expect("second prompt");
    assert!(second_prompt.contains("tool_results"));
    assert!(second_prompt.contains("PROJECT_STATE.md"));
    assert!(second_prompt.contains("modules/next.md"));
    let third_request: Value =
        serde_json::from_slice(&fs::read(body_dir.join("generate_2.json"))?)?;
    let third_prompt = third_request["prompt"].as_str().expect("third prompt");
    assert!(third_prompt.contains("focus is module two"));
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn remote_operator_telegram_chat_tools_refuse_project_escape_and_external_symlink() -> Result<()> {
    use std::os::unix::fs::symlink;

    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let workspace_root = temp.path().join("workspace");
    let project_dir = workspace_root.join("1.2.8.TwinPaper");
    fs::create_dir_all(&project_dir)?;
    fs::write(project_dir.join("README.md"), "TwinPaper\n")?;
    let outside = workspace_root.join("outside-secret.md");
    fs::write(&outside, "CROSS_PROJECT_SECRET_MUST_NOT_LEAK\n")?;
    symlink(&outside, project_dir.join("linked-secret.md"))?;

    let body_dir = temp.path().join("agent_escape_requests");
    fs::create_dir_all(&body_dir)?;
    let (base_url, server) = spawn_fake_ollama_with_sequence(
        body_dir.clone(),
        vec![
            json!({
                "action": "tool",
                "tool": "read_file",
                "project": "twinpaper",
                "path": "../outside-secret.md",
                "reason": "Try a parent-directory escape."
            }),
            json!({
                "action": "tool",
                "tool": "read_file",
                "project": "twinpaper",
                "path": "linked-secret.md",
                "reason": "Try a symlink escape."
            }),
            json!({
                "action": "answer",
                "assistant_reply": "프로젝트 경계 밖 파일은 읽을 수 없습니다.",
                "confidence": 0.9,
                "requires_clarification": false,
                "clarifying_question": null,
                "reason": "Both reads were refused."
            }),
        ],
    )?;
    let out = temp.path().join("escape_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("Twinpaper 밖의 문서도 확인해줘")
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--workspace-root")
        .arg(&workspace_root)
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
    assert_eq!(result["chat_tools_used"][0]["status"], "not_a_file");
    assert_eq!(result["chat_tools_used"][1]["status"], "not_a_file");
    let result_text = serde_json::to_string(&result)?;
    assert!(!result_text.contains("CROSS_PROJECT_SECRET_MUST_NOT_LEAK"));
    for index in 1..=2 {
        let request: Value =
            serde_json::from_slice(&fs::read(body_dir.join(format!("generate_{index}.json")))?)?;
        assert!(!request["prompt"]
            .as_str()
            .expect("agent prompt")
            .contains("CROSS_PROJECT_SECRET_MUST_NOT_LEAK"));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
#[serial]
fn remote_operator_telegram_port_question_includes_live_listener_evidence() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    let request_path = temp.path().join("ollama_port_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        request_path.clone(),
        json!({
            "action": "answer",
            "assistant_reply": format!("{port} 포트는 현재 리슨 중입니다."),
            "confidence": 0.99,
            "requires_clarification": false,
            "clarifying_question": null,
            "reason": "Answered from service_probe."
        }),
    )?;
    let out = temp.path().join("port_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg(format!("{port} 포트가 지금 실제로 리슨 중인지 확인해줘"))
        .arg("--forager-bin")
        .arg(env!("CARGO_BIN_EXE_forager"))
        .arg("--env-file")
        .arg(&env_path)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
        .output()?;

    server.join().expect("fake ollama server panicked")?;
    drop(listener);
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["chat_tools_used"][0]["tool"], "service_probe");
    assert_eq!(result["chat_tools_used"][0]["port"], port);
    assert_eq!(result["chat_tools_used"][0]["status"], "listening");
    let request: Value = serde_json::from_slice(&fs::read(&request_path)?)?;
    let prompt = request["prompt"].as_str().expect("agent prompt");
    assert!(prompt.contains("service_probe"));
    assert!(prompt.contains("\"status\": \"listening\""));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_tools_then_propose_plan_offers_capture() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    write_project_registry(temp.path())?;
    let workspace_root = temp.path().join("workspace");
    let project_dir = workspace_root.join("1.2.8.TwinPaper");
    fs::create_dir_all(&project_dir)?;
    fs::write(project_dir.join("NEXT_ACTIONS.md"), "P1: rerun baseline\n")?;

    let body_dir = temp.path().join("agent_requests");
    fs::create_dir_all(&body_dir)?;
    let (base_url, server) = spawn_fake_ollama_with_sequence(
        body_dir.clone(),
        vec![
            json!({
                "action": "tool",
                "tool": "read_file",
                "project": "twinpaper",
                "path": "NEXT_ACTIONS.md",
                "reason": "Diagnose before planning."
            }),
            json!({
                "action": "propose_plan",
                "delegation_goal": "P1 기준 재실행을 반영해 TwinPaper 실행 계획을 새로 수립한다",
                "assistant_reply": "진단 결과를 반영한 계획을 제안합니다.",
                "confidence": 0.9,
                "reason": "Operator asked to diagnose and replan."
            }),
        ],
    )?;

    let update_path = temp.path().join("diagnose_update.json");
    write_text_update(
        &update_path,
        740,
        920,
        "twinpaper의 현재 프로젝트 상태를 진단하고 새로 계획을 세워봐",
    )?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("diagnose_result.json");

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
        .arg(temp.path().join("feedback.jsonl"))
        .arg("--feedback-ingest-dir")
        .arg(temp.path().join("feedback_ingest"))
        .arg("--workspace-root")
        .arg(&workspace_root)
        .arg("--agent-intent-mode")
        .arg("required")
        .arg("--agent-base-url")
        .arg(&base_url)
        .arg("--agent-model")
        .arg("qwen3-coder-next:latest")
        .arg("--out")
        .arg(&out)
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
    assert_eq!(result["chat_tools_used"][0]["tool"], "read_file");
    assert_eq!(result["plan_capture_offered"], true);
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("<b>계획 후보로 등록할까요?</b>"));
    // The card carries the diagnosis-informed goal.
    assert!(preview.contains("P1 기준 재실행을 반영해"));
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let chat_hash = result["target_chat_id_hash"].as_str().expect("chat hash");
    assert_eq!(
        state["pending_dispatch_confirmations_by_chat"][chat_hash]["kind"],
        "plan_capture"
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_chat_scrubs_hallucinated_commands() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("scrubbed_chat.json");
    let agent_request_path = temp.path().join("ollama_scrub_request.json");
    let (base_url, server) = spawn_fake_ollama_with_classification(
        agent_request_path.clone(),
        json!({
            "intent": "chat",
            "confidence": 0.95,
            "requires_clarification": false,
            "clarifying_question": null,
            "assistant_reply": "목록은 /list 명령어로 확인할 수 있습니다.",
            "reason": "Model hallucinated a nonexistent command.",
            "non_authorized": ["execution", "approval", "shell"]
        }),
    )?;

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("목록이 안보이잖아")
        .arg("--forager-bin")
        .arg(temp.path().join("missing-forager"))
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
    // /list does not exist; the reply must not teach the operator a command
    // that will bounce with unsupported_remote_operator_command.
    assert_eq!(
        result["parsed_command"]["agent_intent"]["assistant_reply"],
        "목록은 /help 명령어로 확인할 수 있습니다."
    );
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(!preview.contains("/list"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_autonomy_stale_confirmation_does_not_block_proposal() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let update_path = temp.path().join("idle_update.json");
    write_text_update(&update_path, 700, 900, "/status")?;
    let state_path = temp.path().join("telegram_state.json");
    let out = temp.path().join("autonomy_result.json");

    let shift_profile = profile_dir(temp.path());
    fs::create_dir_all(&shift_profile)?;
    let now = chrono::Utc::now();
    let starts_at = now + chrono::Duration::minutes(5);
    let stops_at = now + chrono::Duration::hours(10);
    let shift_ledger = json!({
        "schema": "offdesk_shift_ledger.v1",
        "manifests": [{
            "schema": "offdesk_shift_manifest.v1",
            "shift_id": "shift_telegram_reviewed_1",
            "profile": "default",
            "created_at": now.to_rfc3339(),
            "window": {
                "starts_at": starts_at.to_rfc3339(),
                "stops_at": stops_at.to_rfc3339(),
                "timezone": "Asia/Seoul"
            },
            "plan_id": "plan_telegram_reviewed_1",
            "plan_review_id": "review_telegram_reviewed_1",
            "plan_source_sha256": "a".repeat(64),
            "plan_review_sha256": "b".repeat(64),
            "task_bindings": [],
            "routine_bindings": [{
                "routine_id": "morning_review",
                "routine_kind": "morning_review_brief",
                "schedule": {"schedule_kind": "once", "at": (stops_at - chrono::Duration::minutes(10)).to_rfc3339()}
            }],
            "budget": {
                "max_concurrent_tasks": 0,
                "max_task_launches": 0,
                "max_attempts_per_task": 0,
                "max_routine_runs": 1
            },
            "authority": {
                "task_dispatch_requires_fresh_approval": true,
                "task_launch_spec_is_immutable": true,
                "wiki_writes_are_candidate_only": true,
                "plan_revision_requires_review": true,
                "acceptance_requires_ondesk_review": true,
                "does_not_authorize": [
                    "task_dispatch_without_fresh_approval",
                    "command_or_workdir_change",
                    "provider_retargeting",
                    "canonical_mutation",
                    "destructive_action",
                    "unlisted_external_side_effect",
                    "wiki_promotion",
                    "accepted_truth",
                    "plan_revision_without_review"
                ]
            }
        }],
        "arm_receipts": [],
        "states": [],
        "transitions": []
    });
    let shift_path = shift_profile.join("offdesk_shifts.v1.json");
    fs::write(&shift_path, serde_json::to_vec_pretty(&shift_ledger)?)?;
    #[cfg(unix)]
    fs::set_permissions(&shift_path, fs::Permissions::from_mode(0o600))?;

    // An expired proposal confirmation (TTL long past) left in state must be
    // cleared, not treated as pending forever.
    let chat_hash = "sha256:15e2b0d3c33891eb";
    let state = json!({
        "schema": "remote_operator_telegram_state.v1",
        "offset": 0,
        "pending_dispatch_confirmations_by_chat": {
            chat_hash: {
                "schema": "telegram_dispatch_confirmation.v1",
                "token": "deadbeef0000",
                "kind": "autonomy_start",
                "target_id": "overnight",
                "action_kind": "arm",
                "observed_hash": "",
                "note": "idle 45m",
                "chat_id_hash": chat_hash,
                "created_at": "2026-07-01T00:00:00+00:00",
                "ttl_sec": 1800
            }
        }
    });
    fs::write(&state_path, serde_json::to_string_pretty(&state)?)?;

    // Watch tree whose newest mtime is hours old -> idle.
    let watch_dir = temp.path().join("sessions");
    fs::create_dir_all(&watch_dir)?;
    let marker = watch_dir.join("session.jsonl");
    fs::write(&marker, "{}\n")?;
    let old = std::time::SystemTime::now() - Duration::from_secs(2 * 3600);
    fs::File::options()
        .write(true)
        .open(&marker)?
        .set_modified(old)?;
    fs::File::open(&watch_dir)?.set_modified(old)?;

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
        .arg("--out")
        .arg(&out)
        .arg("--autonomy-propose")
        .arg("--autonomy-idle-min")
        .arg("30")
        .arg("--autonomy-propose-from-hour")
        .arg("0")
        .arg("--autonomy-watch-path")
        .arg(&watch_dir)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["autonomy_proposal"]["status"], "proposed");
    assert!(
        result["autonomy_proposal"]["idle_min"]
            .as_i64()
            .expect("idle minutes")
            >= 30
    );

    // The stale token was replaced by a fresh confirmation for the new card.
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let pending = &state["pending_dispatch_confirmations_by_chat"][chat_hash];
    assert_eq!(pending["kind"], "autonomy_start");
    assert_eq!(pending["target_id"], "shift_telegram_reviewed_1");
    assert!(pending["observed_hash"]
        .as_str()
        .is_some_and(|value| value.len() == 64));
    assert_ne!(pending["token"], "deadbeef0000");
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn remote_operator_telegram_no_update_autonomy_proposal_persists_confirmation_and_backoff(
) -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let empty_updates = temp.path().join("empty_updates.json");
    fs::write(&empty_updates, "[]\n")?;

    // Keep the proposal path real while making the shift API side-effect-free.
    // The adapter only needs pause-status and a prepared shift for this scan.
    let fake_forager = temp.path().join("fake-forager.py");
    fs::write(
        &fake_forager,
        r#"#!/usr/bin/env python3
import json
import sys

if "pause-status" in sys.argv:
    print(json.dumps({"schema": "offdesk_pause_status.v1", "paused": False}))
elif "shift" in sys.argv and "status" in sys.argv:
    print(json.dumps({
        "schema": "offdesk_shift_status_report.v1",
        "active_shift_id": None,
        "shifts": [{
            "shift_id": "shift_no_update_regression",
            "status": None,
            "starts_at": "2026-09-07T17:00:00+00:00",
            "stops_at": "2099-01-01T00:00:00+00:00",
            "manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "task_count": 2,
            "routine_count": 1
        }]
    }))
else:
    print(json.dumps({"sessions": []}))
"#,
    )?;
    fs::set_permissions(&fake_forager, fs::Permissions::from_mode(0o700))?;

    let watch_dir = temp.path().join("activity");
    fs::create_dir_all(&watch_dir)?;
    let marker = watch_dir.join("session.jsonl");
    fs::write(&marker, "{}\n")?;
    let old = SystemTime::now() - Duration::from_secs(2 * 3600);
    fs::File::options()
        .write(true)
        .open(&marker)?
        .set_modified(old)?;
    fs::File::open(&watch_dir)?.set_modified(old)?;

    let run_poll = |out: &Path| -> Result<Value> {
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--once")
            .arg("--replay-update-file")
            .arg(&empty_updates)
            .arg("--forager-bin")
            .arg(&fake_forager)
            .arg("--env-file")
            .arg(&env_path)
            .arg("--state-file")
            .arg(&state_path)
            .arg("--out")
            .arg(out)
            .arg("--autonomy-propose")
            .arg("--autonomy-idle-min")
            .arg("30")
            .arg("--autonomy-propose-from-hour")
            .arg("0")
            .arg("--autonomy-watch-path")
            .arg(&watch_dir)
            .env("OFFDESK_REMOTE_OPERATOR_ATTENTION_NOTIFY", "0")
            .env("OFFDESK_REMOTE_OPERATOR_SESSION_NOTIFY", "0")
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&fs::read(out)?)?)
    };

    // There is no update to advance the offset, so the proposal and its
    // confirmation must be the only reason run_once persists state.
    let first = run_poll(&temp.path().join("autonomy_first.json"))?;
    assert_eq!(first["status"], "no_update");
    assert_eq!(first["autonomy_proposal"]["status"], "proposed");
    let first_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    let pending_map = first_state["pending_dispatch_confirmations_by_chat"]
        .as_object()
        .expect("persisted autonomy confirmation map");
    assert_eq!(pending_map.len(), 1);
    let (chat_hash, pending) = pending_map.iter().next().expect("pending confirmation");
    let first_token = pending["token"].as_str().expect("confirmation token");
    assert!(!first_token.is_empty());
    assert!(first_state["autonomy_proposal_at"].is_number());

    // A fresh process/state reload sees the durable proposal timestamp and
    // leaves the same confirmation token available instead of replacing it.
    let second = run_poll(&temp.path().join("autonomy_second.json"))?;
    assert_eq!(second["status"], "no_update");
    // A still-fresh confirmation is checked before the time backoff. Either
    // guard prevents a second proposal, with the pending-token guard being the
    // stronger result because it preserves the operator's actionable token.
    assert_eq!(
        second["autonomy_proposal"]["status"],
        "confirmation_already_pending"
    );
    let second_state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert_eq!(
        second_state["pending_dispatch_confirmations_by_chat"][chat_hash]["token"],
        first_token
    );
    assert_eq!(
        second_state["pending_dispatch_confirmations_by_chat"][chat_hash]["kind"],
        "autonomy_start"
    );
    assert!(second_state["autonomy_proposal_at"].is_number());
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_guide_renders_full_command_surface() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;

    for command in ["/guide", "/qna"] {
        let out = temp.path().join("guide_result.json");
        let output = remote_operator_command(temp.path())
            .arg("--dry-run")
            .arg("--command-text")
            .arg(command)
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
        assert_eq!(result["parsed_command"]["command"], "guide");
        let preview = result["message_preview"].as_str().expect("message preview");
        // The reference sheet must teach the whole surface, grouped.
        assert!(preview.contains("<b>Forager 사용 안내</b>"));
        for group in ["[조회]", "[실행]", "[기록]", "[제어]"] {
            assert!(preview.contains(group), "missing group {group}");
        }
        for usage in [
            "/attention",
            "/decision",
            "/plan",
            "/remember",
            "/wiki",
            "/pause",
            "/guide",
        ] {
            assert!(preview.contains(usage), "missing command {usage}");
        }
        // Telegram HTML: every non-tag "<" must be escaped or sendMessage 400s.
        assert!(preview.contains("&lt;목표&gt;"));
        for line in preview.lines() {
            let stripped = line.replace("<b>", "").replace("</b>", "");
            assert!(
                !stripped.contains('<') && !stripped.contains('>'),
                "unescaped angle bracket in guide line: {line}"
            );
        }
        // Guide uses the relaxed reference-sheet budget, still warning-free.
        assert_mobile_contract(&result);
        assert!(
            result["mobile_card_contract"]["max_lines"]
                .as_u64()
                .expect("guide max lines")
                > 5
        );
    }
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_help_points_to_guide() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let out = temp.path().join("help_result.json");

    let output = remote_operator_command(temp.path())
        .arg("--dry-run")
        .arg("--command-text")
        .arg("/help")
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
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(preview.contains("/guide"));
    assert_mobile_contract(&result);
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_waiting_session_notifies_once_per_episode() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_string(&json!({
            "sessions": [
                {"id": "abc123", "title": "ClaudePilot", "tool": "claude",
                 "path": "/ws/1.2.8.TwinPaper", "status": "waiting", "project": "twinpaper",
                 "remote_session_identity": remote_session_identity("abc123", "twinpaper", "claude"),
                 "notification_capture": notification_capture("abc123", "twinpaper", "claude", ">")},
                {"id": "def456", "title": "Tuner", "tool": "codex",
                 "path": "/ws/nano", "status": "running", "project": "nanoclustering"}
            ]
        }))?,
    )?;

    let mut results = Vec::new();
    for (index, update_id) in [(1, 810), (2, 811)] {
        let update_path = temp.path().join(format!("update_{index}.json"));
        write_text_update(&update_path, update_id, 960 + index, "/status")?;
        let out = temp.path().join(format!("waiting_result_{index}.json"));
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
            .arg("--out")
            .arg(&out)
            .arg("--session-notify")
            .args([
                "--session-notify-quiet-from-hour",
                "0",
                "--session-notify-quiet-to-hour",
                "0",
            ])
            .arg("--session-status-file")
            .arg(&status_path)
            .output()?;
        assert!(
            output.status.success(),
            "stdout:\n{}\nstderr:\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        results.push(serde_json::from_slice::<Value>(&fs::read(&out)?)?);
    }

    // First poll notifies the waiting session (project-tagged card), second
    // poll is suppressed by the per-session backoff.
    assert_eq!(results[0]["session_notification"]["status"], "notified");
    let sent = results[0]["session_notification"]["sent"]
        .as_array()
        .expect("sent sessions");
    assert_eq!(sent.len(), 1);
    assert_eq!(sent[0]["session_id"], "abc123");
    assert_eq!(sent[0]["project"], "twinpaper");
    assert_eq!(
        results[1]["session_notification"]["status"],
        "no_new_waiting"
    );
    assert_eq!(results[1]["session_notification"]["waiting_count"], 1);

    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    // Entries are objects so the pushed card can be expired when the episode
    // ends: "at" timestamp plus the sent message id / prompt hash / label.
    let entry = &state["waiting_notified_by_session"]["abc123"];
    assert!(entry.is_object());
    assert!(entry["at"].is_number());
    assert_eq!(
        entry["remote_session_identity"]["schema"],
        "remote_session_identity.v1"
    );
    assert_eq!(
        sent[0]["remote_session_identity_sha256"],
        entry["remote_session_identity"]["binding_sha256"]
    );
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_never_offers_an_unbound_waiting_action() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [{
                "id": "unbound000000000", "title": "Codex", "tool": "codex",
                "status": "waiting", "project": "forager"
            }]
        }))?,
    )?;
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_610, 9_611, "/status")?;
    let out = temp.path().join("result.json");

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
        .arg("--out")
        .arg(&out)
        .arg("--session-notify")
        .args([
            "--session-notify-quiet-from-hour",
            "0",
            "--session-notify-quiet-to-hour",
            "0",
        ])
        .arg("--session-status-file")
        .arg(&status_path)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["session_notification"]["status"], "no_new_waiting");
    assert_eq!(
        result["session_notification"]["muted"][0]["reason"],
        "remote_session_identity_unavailable"
    );
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state["waiting_notified_by_session"]
        .as_object()
        .is_none_or(Map::is_empty));
    Ok(())
}

#[test]
#[serial]
fn remote_operator_telegram_multiple_waiting_cards_do_not_choose_a_sticky_session() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_env_file(&env_path)?;
    let state_path = temp.path().join("telegram_state.json");
    fs::write(
        &state_path,
        serde_json::to_vec(&json!({
            "schema": "remote_operator_telegram_state.v1",
            "offset": 0,
            "session_focus_by_chat": {
                "sha256:15e2b0d3c33891eb": {
                    "session_id": "abc1230000000000",
                    "project": "first",
                    "title": "Codex",
                    "tool": "codex",
                    "prompt_hash": "oldhash1",
                    "source": "waiting_card",
                    "updated_at": "2026-08-11T00:00:00+00:00"
                }
            }
        }))?,
    )?;
    let status_path = temp.path().join("status.json");
    fs::write(
        &status_path,
        serde_json::to_vec(&json!({
            "sessions": [
                {"id": "abc1230000000000", "title": "Codex", "tool": "codex",
                 "status": "waiting", "project": "first",
                 "remote_session_identity": remote_session_identity("abc1230000000000", "first", "codex"),
                 "notification_capture": notification_capture("abc1230000000000", "first", "codex", ">")},
                {"id": "def4560000000000", "title": "Claude", "tool": "claude",
                 "status": "waiting", "project": "second",
                 "remote_session_identity": remote_session_identity("def4560000000000", "second", "claude"),
                 "notification_capture": notification_capture("def4560000000000", "second", "claude", ">")}
            ]
        }))?,
    )?;
    let update_path = temp.path().join("update.json");
    write_text_update(&update_path, 9_500, 9_501, "/status")?;
    let out = temp.path().join("result.json");

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
        .arg("--out")
        .arg(&out)
        .arg("--session-notify")
        .args([
            "--session-notify-quiet-from-hour",
            "0",
            "--session-notify-quiet-to-hour",
            "0",
        ])
        .arg("--session-status-file")
        .arg(&status_path)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(result["session_notification"]["waiting_count"], 2);
    assert_eq!(
        result["session_notification"]["sent"]
            .as_array()
            .expect("sent sessions")
            .len(),
        1
    );
    let first_id = result["session_notification"]["sent"][0]["session_id"]
        .as_str()
        .expect("first waiting session")
        .to_string();

    // The lower-ranked waiting episode remains eligible for the next poll;
    // arbitration must not consume it merely because another card won.
    let second_out = temp.path().join("second_result.json");
    let second = remote_operator_command(temp.path())
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
        .arg("--out")
        .arg(&second_out)
        .arg("--session-notify")
        .args([
            "--session-notify-quiet-from-hour",
            "0",
            "--session-notify-quiet-to-hour",
            "0",
        ])
        .arg("--session-status-file")
        .arg(&status_path)
        .output()?;
    assert!(
        second.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&second.stdout),
        String::from_utf8_lossy(&second.stderr)
    );
    let second_result: Value = serde_json::from_slice(&fs::read(&second_out)?)?;
    assert_eq!(second_result["session_notification"]["waiting_count"], 2);
    assert_eq!(
        second_result["session_notification"]["sent"]
            .as_array()
            .expect("second sent sessions")
            .len(),
        1
    );
    let second_id = second_result["session_notification"]["sent"][0]["session_id"]
        .as_str()
        .expect("second waiting session");
    assert_ne!(first_id, second_id);
    let state: Value = serde_json::from_slice(&fs::read(&state_path)?)?;
    assert!(state["session_focus_by_chat"]
        .as_object()
        .is_none_or(Map::is_empty));
    Ok(())
}
