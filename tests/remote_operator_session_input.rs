//! Integration tests for remote session-input delivery.
//!
//! `tmux send-keys` exits 0 as soon as the key is queued, so the Telegram
//! operator used to report every remote approval as delivered. On 2026-08-06 a
//! codex pane sat frozen for fourteen hours while six approvals were reported
//! successful and nothing moved. These tests pin the distinction: a pane that
//! never repaints is a failure, a pane that reacts is a success.

use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};
use tempfile::tempdir;

use forager::session::{Instance, RemoteSessionIdentityV1};

// The dispatcher resolves a session by the "_<8-char id>" suffix on the tmux
// name, so these must end with the ids used in the status fixtures below.
const FROZEN_SESSION: &str = "forager_test_deadbeef";
const LIVE_SESSION: &str = "forager_test_cafe1234";
const TEXT_SESSION: &str = "forager_test_feed5678";
const MULTILINE_SESSION: &str = "forager_test_face5678";
const QUIET_TEXT_SESSION: &str = "forager_test_f00d5678";
const HASH_SESSION: &str = "forager_test_bead9876";

fn tool_available(bin: &str, version_flag: &str) -> bool {
    Command::new(bin)
        .arg(version_flag)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn scripts_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts")
}

fn kill_session(name: &str) {
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", name])
        .output();
}

fn start_session(name: &str, command: &str) {
    kill_session(name);
    let created = Command::new("tmux")
        .args(["new-session", "-d", "-s", name, command])
        .output()
        .expect("failed to spawn tmux session");
    assert!(created.status.success(), "could not create {name}");
}

fn wait_for_quiet_sleep_pane(name: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut previous_capture = None;

    loop {
        let command = Command::new("tmux")
            .args([
                "display-message",
                "-p",
                "-t",
                name,
                "#{pane_current_command}",
            ])
            .output()
            .expect("failed to inspect tmux pane command");
        let is_sleeping =
            command.status.success() && String::from_utf8_lossy(&command.stdout).trim() == "sleep";
        if is_sleeping {
            let capture = Command::new("tmux")
                .args(["capture-pane", "-p", "-t", name])
                .output()
                .expect("failed to capture quiet tmux pane");
            assert!(capture.status.success(), "could not capture {name}");
            if previous_capture.as_ref() == Some(&capture.stdout) {
                return;
            }
            previous_capture = Some(capture.stdout);
        } else {
            previous_capture = None;
        }

        assert!(Instant::now() < deadline, "{name} did not become quiet");
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// Drive the Python dispatcher the way the Telegram poll loop does.
fn apply_session_input(status_file: &Path, session_id: &str) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.dispatch import apply_session_input
print(json.dumps(apply_session_input(
    "forager", "default", sys.argv[3], "approve", status_file=sys.argv[2])))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(status_file)
        .arg(session_id)
        .output()
        .expect("failed to run dispatcher");
    assert!(
        output.status.success(),
        "dispatcher failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("dispatcher returned non-JSON")
}

fn apply_session_input_bound(
    status_file: &Path,
    session_id: &str,
    expected_identity: &Value,
) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.dispatch import apply_session_input
print(json.dumps(apply_session_input(
    "forager", "default", sys.argv[3], "approve", status_file=sys.argv[2],
    expected_identity=json.loads(sys.argv[4]), require_remote_identity=True)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(status_file)
        .arg(session_id)
        .arg(serde_json::to_string(expected_identity).expect("serialize identity"))
        .output()
        .expect("failed to run identity-bound input dispatcher");
    assert!(
        output.status.success(),
        "identity-bound input dispatcher failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout)
        .expect("identity-bound input dispatcher returned non-JSON")
}

fn apply_session_text_input(
    status_file: &Path,
    session_id: &str,
    text: &str,
    expected_hash: &str,
) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.dispatch import apply_session_text_input
print(json.dumps(apply_session_text_input(
    "forager", "default", sys.argv[3], sys.argv[4], status_file=sys.argv[2],
    expected_hash=sys.argv[5])))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(status_file)
        .arg(session_id)
        .arg(text)
        .arg(expected_hash)
        .output()
        .expect("failed to run text dispatcher");
    assert!(
        output.status.success(),
        "text dispatcher failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("text dispatcher returned non-JSON")
}

fn apply_session_text_input_exact(status_file: &Path, session_id: &str, text: &str) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.dispatch import apply_session_text_input
print(json.dumps(apply_session_text_input(
    "forager", "default", sys.argv[3], sys.argv[4], status_file=sys.argv[2],
    require_exact_id=True)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(status_file)
        .arg(session_id)
        .arg(text)
        .output()
        .expect("failed to run exact text dispatcher");
    assert!(
        output.status.success(),
        "exact text dispatcher failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("exact text dispatcher returned non-JSON")
}

fn apply_session_text_input_bound(
    status_file: &Path,
    session_id: &str,
    text: &str,
    expected_identity: &Value,
) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.dispatch import apply_session_text_input
print(json.dumps(apply_session_text_input(
    "forager", "default", sys.argv[3], sys.argv[4], status_file=sys.argv[2],
    require_exact_id=True, expected_identity=json.loads(sys.argv[5]),
    require_remote_identity=True)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(status_file)
        .arg(session_id)
        .arg(text)
        .arg(serde_json::to_string(expected_identity).expect("serialize identity"))
        .output()
        .expect("failed to run identity-bound text dispatcher");
    assert!(
        output.status.success(),
        "identity-bound text dispatcher failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout)
        .expect("identity-bound text dispatcher returned non-JSON")
}

struct TmuxInstanceCleanup(Vec<Instance>);

impl Drop for TmuxInstanceCleanup {
    fn drop(&mut self) {
        for instance in &self.0 {
            if let Ok(session) = instance.tmux_session() {
                let _ = session.kill();
            }
        }
    }
}

fn shell_quote(value: &Path) -> String {
    format!("'{}'", value.to_string_lossy().replace('\'', "'\\''"))
}

fn wait_for_pane_text(instance: &Instance, expected: &str) {
    let session = instance.tmux_session().expect("tmux session");
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let capture = session.capture_pane(30).unwrap_or_default();
        if capture.contains(expected) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "{} did not render {expected:?}",
            instance.title
        );
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn wait_for_receipt(path: &Path, expected: &str) {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if std::fs::read_to_string(path).is_ok_and(|value| value == format!("{expected}\n")) {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "receipt {} did not contain its exact message",
            path.display()
        );
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn render_session_input_error(error: &str) -> Value {
    let driver = r#"
import argparse, json, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
import offdesk_remote_operator_telegram as mod
from telegram_operator.remote_session_identity import signed_identity
error = sys.argv[2]
mod.apply_session_input = lambda *args, **kwargs: {
    "ok": False, "kind": "session_input", "error": error,
    "project": "demo", "session_title": "codex", "key": "Enter",
    "new_hash": "new12345", "prompt_line": "Allow the new command?",
}
args = argparse.Namespace(
    forager_bin="forager", profile="default", session_status_file=None,
)
parsed = {
    "command": "session_approve",
    "session_input_session_id": "deadbeef",
    "session_input_hash": "old12345",
    "session_input_option": "",
}
identity = signed_identity({
    "schema": "remote_session_identity.v1",
    "machine_identity_sha256": "1" * 64,
    "project_key": "demo",
    "project_root_identity_sha256": "2" * 64,
    "worktree_kind": "direct_project_root",
    "worktree_identity_sha256": "3" * 64,
    "harness_kind": "codex",
    "harness_launch_sha256": "4" * 64,
    "session_id": "deadbeef00000000",
    "session_record_identity_sha256": "5" * 64,
    "transport_kind": "tmux",
    "transport_session_identity_sha256": "6" * 64,
    "pane_identity_sha256": "7" * 64,
    "process_identity_sha256": "8" * 64,
})
print(json.dumps(mod.render_dispatch_command(args, {}, parsed, {
    "parsed_command": parsed, "generated_at": "2026-08-11T00:00:00Z",
}, session_context={
    "session_id": "deadbeef00000000",
    "source": "waiting_card",
    "remote_session_identity": identity,
})))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(error)
        .output()
        .expect("failed to run render probe");
    assert!(
        output.status.success(),
        "render probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("render probe returned non-JSON")
}

fn normalize_send_agent_intent() -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.agent import normalize_agent_chat
parsed = {
    "action": "send_agent",
    "session_id": "cafe123400000000",
    "message": "전체 테스트를 다시 실행하고 결과를 알려줘",
    "assistant_reply": "로컬 Codex에 전달합니다.",
    "confidence": 0.94,
    "reason": "operator directed the live agent",
}
runtime = {"provider": "ollama", "base_url": "local", "model": "test"}
print(json.dumps(normalize_agent_chat(parsed, runtime=runtime)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to normalize agent intent");
    assert!(
        output.status.success(),
        "normalizer failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("normalizer returned non-JSON")
}

fn validate_send_agent_intent(chat_text: &str, intent: Value, snapshot: Value) -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.agent import validate_session_message_intent
print(json.dumps(validate_session_message_intent(
    json.loads(sys.argv[3]), chat_text=sys.argv[2],
    operator_snapshot=json.loads(sys.argv[4]))))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .arg(chat_text)
        .arg(serde_json::to_string(&intent).expect("serialize intent"))
        .arg(serde_json::to_string(&snapshot).expect("serialize snapshot"))
        .output()
        .expect("failed to validate agent intent");
    assert!(
        output.status.success(),
        "validator failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("validator returned non-JSON")
}

fn render_session_message_dry_run() -> Value {
    let driver = r#"
import argparse, json, sys
sys.path.insert(0, sys.argv[1])
import offdesk_remote_operator_telegram as mod
def unexpected_apply(*args, **kwargs):
    raise RuntimeError("dry-run called apply_session_text_input")
mod.apply_session_text_input = unexpected_apply
args = argparse.Namespace(
    forager_bin="forager", profile="default", session_status_file=None,
)
result = {
    "mode": "dry_run", "generated_at": "2026-08-11T00:00:00Z",
    "read_only": True, "mutation_authorized": False,
}
print(json.dumps(mod.render_session_message_delivery(
    args, result, session_id="cafe123400000000", message="continue")))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to render dry-run message");
    assert!(
        output.status.success(),
        "dry-run render failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("dry-run render returned non-JSON")
}

fn resolve_replied_session_context() -> Value {
    let driver = r#"
import json, sys
sys.path.insert(0, sys.argv[1])
import offdesk_remote_operator_telegram as mod
state = {"waiting_notified_by_session": {
    "feed567800000000": {
        "message_id": 700,
        "resolved": False,
        "project": "forager",
        "title": "Codex",
        "tool": "codex",
        "prompt_hash": "abcd1234",
    }
}}
message = {"reply_to_message": {"message_id": 700}}
print(json.dumps(mod.replied_session_context(state, message)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to resolve replied session context");
    assert!(
        output.status.success(),
        "reply context probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("reply context probe returned non-JSON")
}

fn remote_pending_action_replacement_matrix() -> Value {
    let driver = r#"
import copy, json, sys
sys.path.insert(0, sys.argv[1])
from telegram_operator.remote_session_identity import (
    BOUND_IDENTITY_FIELDS,
    pending_action_identity_issue,
    signed_identity,
)
base = signed_identity({
    "schema": "remote_session_identity.v1",
    "machine_identity_sha256": "1" * 64,
    "project_key": "forager",
    "project_root_identity_sha256": "2" * 64,
    "worktree_kind": "managed_worktree",
    "worktree_identity_sha256": "3" * 64,
    "harness_kind": "codex",
    "harness_launch_sha256": "4" * 64,
    "session_id": "session-one",
    "session_record_identity_sha256": "5" * 64,
    "transport_kind": "tmux",
    "transport_session_identity_sha256": "6" * 64,
    "pane_identity_sha256": "7" * 64,
    "process_identity_sha256": "8" * 64,
})
def status(identity):
    return {"sessions": [{
        "id": "session-one",
        "project": identity.get("project_key"),
        "tool": identity.get("harness_kind"),
        "remote_session_identity": identity,
    }]}
results = {"exact": pending_action_identity_issue(base, status(base))}
for field in BOUND_IDENTITY_FIELDS:
    current = copy.deepcopy(base)
    if field.endswith("_sha256"):
        current[field] = "9" * 64
    elif field == "worktree_kind":
        current[field] = "direct_project_root"
    elif field == "project_key":
        current[field] = "replacement-project"
    elif field == "session_id":
        current[field] = "session-two"
    elif field == "transport_kind":
        current[field] = "replacement-transport"
    else:
        current[field] = str(current[field]) + "-replacement"
    if field == "transport_kind":
        # The strict envelope rejects an unsupported transport before equality.
        current["transport_kind"] = "tmux"
        current["transport_session_identity_sha256"] = "9" * 64
    current = signed_identity(current)
    results[field] = pending_action_identity_issue(base, status(current))
results["missing"] = pending_action_identity_issue(base, {"sessions": []})
tampered = copy.deepcopy(base)
tampered["pane_identity_sha256"] = "a" * 64
results["tampered"] = pending_action_identity_issue(base, status(tampered))
print(json.dumps(results))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to run remote session identity matrix");
    assert!(
        output.status.success(),
        "remote session identity matrix failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("identity matrix returned non-JSON")
}

fn render_waiting_action_with_replaced_identity() -> Value {
    let driver = r#"
import argparse, copy, json, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
import offdesk_remote_operator_telegram as mod
from telegram_operator.remote_session_identity import signed_identity
expected = signed_identity({
    "schema": "remote_session_identity.v1",
    "machine_identity_sha256": "1" * 64,
    "project_key": "forager",
    "project_root_identity_sha256": "2" * 64,
    "worktree_kind": "direct_project_root",
    "worktree_identity_sha256": "3" * 64,
    "harness_kind": "codex",
    "harness_launch_sha256": "4" * 64,
    "session_id": "feed567800000000",
    "session_record_identity_sha256": "5" * 64,
    "transport_kind": "tmux",
    "transport_session_identity_sha256": "6" * 64,
    "pane_identity_sha256": "7" * 64,
    "process_identity_sha256": "8" * 64,
})
current = copy.deepcopy(expected)
current["pane_identity_sha256"] = "9" * 64
current = signed_identity(current)
with tempfile.TemporaryDirectory() as directory:
    status_file = pathlib.Path(directory) / "status.json"
    status_file.write_text(json.dumps({"sessions": [{
        "id": "feed567800000000", "title": "Codex", "tool": "codex",
        "project": "forager", "status": "waiting",
        "remote_session_identity": current,
    }]}))
    args = argparse.Namespace(
        forager_bin="forager", profile="default", session_status_file=status_file,
    )
    parsed = {
        "command": "session_approve", "session_input_session_id": "feed5678",
        "session_input_hash": "", "session_input_option": "",
    }
    result = mod.render_dispatch_command(
        args, {}, parsed,
        {"generated_at": "2026-08-11T00:00:00Z"},
        session_context={
            "session_id": "feed567800000000", "source": "waiting_card",
            "remote_session_identity": expected,
        },
    )
    print(json.dumps(result))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to render replaced waiting action");
    assert!(
        output.status.success(),
        "replaced waiting action probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("waiting action probe returned non-JSON")
}

fn session_interaction_matrix() -> Value {
    let driver = r#"
import argparse, json, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
import offdesk_remote_operator_telegram as mod
from telegram_operator.dispatch import classify_session_interaction
from telegram_operator.remote_session_identity import signed_identity
from telegram_operator.routing import parse_remote_command

trust = """> You are in /workspace/project
Do you trust the contents of this directory?
› 1. Yes, continue
  2. No, quit
Press enter to continue"""
update = """✨ Update available! 0.149.0 -> 0.150.1
› 1. Update now (runs `npm install -g @openai/codex`)
  2. Skip
  3. Skip until next version
  Press enter to continue"""
ready = """Implemented the requested change and all tests pass.
› Implement {feature}
gpt-5.6-sol"""
question = """I need one detail before editing.
Which target should I use?"""
unknown = "terminal output only"

def identity_for(session_id, project):
    return signed_identity({
        "schema": "remote_session_identity.v1",
        "machine_identity_sha256": "1" * 64,
        "project_key": project,
        "project_root_identity_sha256": "2" * 64,
        "worktree_kind": "direct_project_root",
        "worktree_identity_sha256": "3" * 64,
        "harness_kind": "codex",
        "harness_launch_sha256": "4" * 64,
        "session_id": session_id,
        "session_record_identity_sha256": "5" * 64,
        "transport_kind": "tmux",
        "transport_session_identity_sha256": "6" * 64,
        "pane_identity_sha256": "7" * 64,
        "process_identity_sha256": "8" * 64,
    })
identity = identity_for("feed567800000000", "forager")
ready_interaction = classify_session_interaction(ready, "codex")
trust_interaction = classify_session_interaction(trust, "codex")
update_interaction = classify_session_interaction(update, "codex")
markup = mod.session_interaction_markup(
    ready_interaction,
    session_id="feed567800000000",
    prompt_hash="abcd1234",
)
trust_markup = mod.session_interaction_markup(
    trust_interaction,
    session_id="feed567800000000",
    prompt_hash="dcba4321",
)
update_markup = mod.session_interaction_markup(
    update_interaction,
    session_id="feed567800000000",
    prompt_hash="face1234",
)
callback = markup["inline_keyboard"][0][0]["callback_data"]
parsed = parse_remote_command(mod.command_from_callback_data(callback))
state = {"waiting_notified_by_session": {"feed567800000000": {
    "resolved": False,
    "prompt_hash": "abcd1234",
    "interaction_kind": "task_ready",
    "project": "forager",
    "title": "Codex",
    "tool": "codex",
    "remote_session_identity": identity,
}}}
context = mod.pending_session_action_context(state, parsed)
args = argparse.Namespace(
    forager_bin="forager", profile="default", session_status_file=None,
)
rendered = mod.render_dispatch_command(
    args,
    {},
    parsed,
    {"mode": "dry_run", "generated_at": "2026-09-01T00:00:00Z"},
    session_context=context,
)
permission_state = json.loads(json.dumps(state))
permission_state["waiting_notified_by_session"]["feed567800000000"][
    "interaction_kind"
] = "permission"
permission_context = mod.pending_session_action_context(permission_state, parsed)
captured = []
mod.send_message = lambda config, chat, message, args, reply_markup=None: (
    captured.append({"message": message, "reply_markup": reply_markup}) or 77
)
mod.summarize_waiting_prompt = lambda args, tail: "요청된 변경을 완료했습니다."
import telegram_operator.dispatch as dispatch
dispatch.find_tmux_session_name = lambda session_id: "fake_tmux"
dispatch.capture_session_tail = lambda name, lines=15: ready
dispatch.capture_notification_tail = lambda name, lines=15: ready
with tempfile.TemporaryDirectory() as directory:
    status_file = pathlib.Path(directory) / "status.json"
    status_file.write_text(json.dumps({"sessions": [{
        "id": "feed567800000000",
        "title": "Codex",
        "tool": "codex",
        "project": "forager",
        "status": "waiting",
        "remote_session_identity": identity,
        "notification_capture": {"schema": "notification_capture.v1",
            "binding_sha256": identity["binding_sha256"], "tail": ready},
    }]}))
    scan_args = argparse.Namespace(
        session_notify=True,
        session_status_file=status_file,
        state_file=pathlib.Path(directory) / "operator-state.json",
        forager_bin="forager",
        profile="default",
        session_notify_backoff_sec=1800,
        session_notify_max_backoff_sec=14400,
        session_notify_max_cards=4,
        session_notify_quiet_from_hour=0,
        session_notify_quiet_to_hour=0,
        dry_run=False,
    )
    # The keyboard migration is a separate one-time prerequisite. This probe
    # starts after it so the task-ready card is the only delivery under test.
    scan_state = {"telegram_session_keyboard_schema": "inline_v3"}
    scan_result = mod.scan_and_notify_waiting_sessions(
        scan_args,
        {"target_chat_id": "1", "target_chat_id_hash": "chat"},
        scan_state,
    )
bootstrap_captured = []
mod.send_message = lambda config, chat, message, args, reply_markup=None: (
    bootstrap_captured.append({"message": message, "reply_markup": reply_markup}) or 88
)
dispatch.find_tmux_session_name = lambda session_id: session_id
dispatch.capture_session_tail = lambda name, lines=15: (
    trust if name == "aaaabbbb00000001" else update
)
dispatch.capture_notification_tail = dispatch.capture_session_tail
with tempfile.TemporaryDirectory() as directory:
    status_file = pathlib.Path(directory) / "status.json"
    status_file.write_text(json.dumps({"sessions": [
        {
            "id": "aaaabbbb00000001", "title": "Orchestrator", "tool": "codex",
            "project": "k-write", "status": "waiting", "yolo_mode": True,
            "auto_orchestrator": True,
            "remote_session_identity": identity_for("aaaabbbb00000001", "k-write"),
            "notification_capture": {"schema": "notification_capture.v1",
                "binding_sha256": identity_for("aaaabbbb00000001", "k-write")["binding_sha256"], "tail": trust},
        },
        {
            "id": "aaaabbbb00000002", "title": "Custom Coordinator", "tool": "codex",
            "project": "environment", "status": "waiting", "yolo_mode": True,
            "auto_orchestrator": True,
            "remote_session_identity": identity_for("aaaabbbb00000002", "environment"),
            "notification_capture": {"schema": "notification_capture.v1",
                "binding_sha256": identity_for("aaaabbbb00000002", "environment")["binding_sha256"], "tail": update},
        },
    ]}))
    scan_args.session_status_file = status_file
    bootstrap_state = {"telegram_session_keyboard_schema": "inline_v3"}
    bootstrap_result = mod.scan_and_notify_waiting_sessions(
        scan_args,
        {"target_chat_id": "1", "target_chat_id_hash": "chat"},
        bootstrap_state,
    )
    bootstrap_repeat_result = mod.scan_and_notify_waiting_sessions(
        scan_args,
        {"target_chat_id": "1", "target_chat_id_hash": "chat"},
        bootstrap_state,
    )
suppressed_hash = bootstrap_state["waiting_notified_by_session"][
    "aaaabbbb00000001"
]["prompt_hash"]
suppressed_action = parse_remote_command(
    f"/session_approve aaaabbbb {suppressed_hash} 1"
)
suppressed_context = mod.pending_session_action_context(
    bootstrap_state, suppressed_action
)
mod.apply_session_input = lambda *args, **kwargs: (_ for _ in ()).throw(
    AssertionError("suppressed startup gate reached tmux input")
)
suppressed_rendered = mod.render_dispatch_command(
    scan_args,
    {"target_chat_id": "1", "target_chat_id_hash": "chat"},
    suppressed_action,
    {"mode": "live_once", "generated_at": "2026-09-01T00:00:00Z"},
    session_context=suppressed_context,
)
print(json.dumps({
    "trust": trust_interaction,
    "update": update_interaction,
    "ready": ready_interaction,
    "question": classify_session_interaction(question, "codex"),
    "unknown": classify_session_interaction(unknown, "codex"),
    "markup": markup,
    "trust_markup": trust_markup,
    "update_markup": update_markup,
    "callback_command": mod.command_from_callback_data(callback),
    "parsed": parsed,
    "context": context,
    "permission_context": permission_context,
    "rendered": rendered,
    "scan_result": scan_result,
    "scan_state": scan_state,
    "captured": captured,
    "bootstrap_result": bootstrap_result,
    "bootstrap_repeat_result": bootstrap_repeat_result,
    "bootstrap_state": bootstrap_state,
    "bootstrap_captured": bootstrap_captured,
    "suppressed_context": suppressed_context,
    "suppressed_rendered": suppressed_rendered,
}))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to classify session interactions");
    assert!(
        output.status.success(),
        "session interaction probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    serde_json::from_slice(&output.stdout).expect("session interaction probe returned non-JSON")
}

#[test]
fn remote_pending_action_rejects_every_bound_identity_replacement() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let result = remote_pending_action_replacement_matrix();
    assert!(result["exact"].is_null());
    for field in [
        "machine_identity_sha256",
        "project_key",
        "project_root_identity_sha256",
        "worktree_kind",
        "worktree_identity_sha256",
        "harness_kind",
        "harness_launch_sha256",
        "session_id",
        "session_record_identity_sha256",
        "transport_kind",
        "transport_session_identity_sha256",
        "pane_identity_sha256",
        "process_identity_sha256",
    ] {
        assert!(
            result[field].is_string(),
            "replacement was not rejected for {field}: {result}"
        );
    }
    assert_eq!(result["missing"], "session_missing");
    assert_eq!(result["tampered"], "current_binding_sha256_mismatch");
}

#[test]
fn telegram_waiting_action_refuses_a_replaced_bound_pane() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let result = render_waiting_action_with_replaced_identity();
    assert_eq!(result["dispatch_result"]["ok"], false);
    assert_eq!(
        result["dispatch_result"]["error"],
        "session_identity_changed:pane_identity_sha256_changed"
    );
}

#[test]
fn telegram_session_cards_separate_permission_from_next_task_continuation() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let result = session_interaction_matrix();
    assert_eq!(result["trust"]["kind"], "startup_trust");
    assert_eq!(result["trust"]["continuation_allowed"], false);
    assert_eq!(result["trust"]["options"].as_array().map(Vec::len), Some(2));
    assert_eq!(result["update"]["kind"], "maintenance_update");
    assert_eq!(result["update"]["continuation_allowed"], false);
    assert_eq!(
        result["update"]["options"].as_array().map(Vec::len),
        Some(3)
    );
    assert_eq!(result["ready"]["kind"], "task_ready");
    assert_eq!(result["ready"]["continuation_allowed"], true);
    assert_eq!(result["question"]["kind"], "question");
    assert_eq!(result["unknown"]["kind"], "unknown");
    assert_eq!(
        result["markup"]["inline_keyboard"][0][0]["text"],
        "다음 작업 바로 진행"
    );
    assert_eq!(
        result["trust_markup"]["inline_keyboard"][0][0]["text"],
        "1. Yes, continue"
    );
    assert!(!result["trust_markup"]
        .to_string()
        .contains("다음 작업 바로 진행"));
    assert_eq!(
        result["update_markup"]["inline_keyboard"]
            .as_array()
            .map(Vec::len),
        Some(1)
    );
    assert_eq!(
        result["update_markup"]["inline_keyboard"][0][0]["text"],
        "상태"
    );
    assert_eq!(
        result["callback_command"],
        "/session_continue feed5678 abcd1234"
    );
    assert_eq!(result["parsed"]["command"], "session_continue");
    assert_eq!(result["context"]["interaction_kind"], "task_ready");
    assert!(result["permission_context"].is_null());
    assert_eq!(
        result["rendered"]["session_message_preview"]["session_id"],
        "feed567800000000"
    );
    assert!(result["rendered"]["message_preview"]
        .as_str()
        .expect("message preview")
        .contains("다음 미완료 작업"));
    assert_eq!(
        result["scan_result"]["sent"][0]["interaction_kind"],
        "task_ready"
    );
    assert_eq!(
        result["scan_result"]["sent"][0]["prompt_identity"]
            .as_str()
            .map(str::len),
        Some(16)
    );
    assert_eq!(
        result["scan_result"]["sent"][0]["prompt_observation_hash"]
            .as_str()
            .map(str::len),
        Some(8)
    );
    assert_eq!(
        result["scan_state"]["waiting_notified_by_session"]["feed567800000000"]["prompt_hash"],
        result["scan_result"]["sent"][0]["prompt_identity"]
    );
    assert_eq!(
        result["scan_state"]["telegram_session_keyboard_schema"],
        "inline_v3"
    );
    assert_eq!(result["captured"].as_array().map(Vec::len), Some(1));
    assert!(result["captured"][0]["message"]
        .as_str()
        .expect("card message")
        .contains("작업 완료 · 다음 지시 대기"));
    assert!(result["captured"][0]["reply_markup"]
        .get("keyboard")
        .is_none());
    assert_eq!(
        result["captured"][0]["reply_markup"]["inline_keyboard"][0][0]["text"],
        "다음 작업 바로 진행"
    );
    assert_eq!(
        result["captured"][0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            .as_str()
            .and_then(|data| data.split(':').nth(3))
            .map(str::len),
        Some(16)
    );
    assert_eq!(result["bootstrap_result"]["status"], "notified");
    assert_eq!(
        result["bootstrap_result"]["bootstrap_digest"]["status"],
        "notified"
    );
    assert_eq!(result["bootstrap_result"]["bootstrap_digest"]["count"], 2);
    assert_eq!(
        result["bootstrap_captured"].as_array().map(Vec::len),
        Some(1)
    );
    assert_eq!(
        result["bootstrap_repeat_result"]["bootstrap_digest"]["status"],
        "unchanged"
    );
    assert!(result["suppressed_context"].is_null());
    assert_eq!(
        result["suppressed_rendered"]["dispatch_result"]["ok"],
        false
    );
    assert_eq!(
        result["suppressed_rendered"]["dispatch_result"]["error"],
        "waiting_card_context_missing_or_changed"
    );
    assert!(result["bootstrap_captured"][0]["message"]
        .as_str()
        .expect("bootstrap digest")
        .contains("백그라운드 Orchestrator 준비 필요 · 2개"));
    assert_eq!(
        result["bootstrap_captured"][0]["reply_markup"]["inline_keyboard"][0][0]["text"],
        "상태"
    );
    assert_eq!(
        result["bootstrap_state"]["waiting_notified_by_session"]["aaaabbbb00000001"]
            ["bootstrap_suppressed"],
        true
    );
    assert_eq!(
        result["bootstrap_state"]["waiting_notified_by_session"]["aaaabbbb00000002"]
            ["bootstrap_suppressed"],
        true
    );
}

#[test]
fn semantic_prompt_identity_ignores_tui_churn_but_rejects_changed_action() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let driver = r#"
import json, pathlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
import telegram_operator.dispatch as dispatch

base = """Codex wants to run the following command:
$ rm -rf target
Allow this command?
❯ 1. Yes, run this command
  2. No, tell Codex what to do differently
Press Enter to select · Esc to cancel
Working (12.4s)
gpt-5.6-sol"""
volatile = base.replace("Working (12.4s)", "Working (19.8s)").replace(
    "gpt-5.6-sol", "gpt-5.6-sol\nEsc to interrupt · 14:32 · 81% · $0.42"
)
changed_command = volatile.replace("$ rm -rf target", "$ rm -rf important")
changed_option = volatile.replace(
    "2. No, tell Codex what to do differently",
    "2. Yes, always allow this command",
)
changed_question = volatile.replace("Allow this command?", "Allow this command now?")
timed_command_30 = base.replace("$ rm -rf target", "$ sleep 30s")
timed_command_90 = base.replace("$ rm -rf target", "$ sleep 90s")

raw_base = dispatch.pane_prompt_hash(base)
raw_volatile = dispatch.pane_prompt_hash(volatile)
semantic_base = dispatch.session_prompt_identity(base, "codex")
semantic_volatile = dispatch.session_prompt_identity(volatile, "codex")
semantic_command = dispatch.session_prompt_identity(changed_command, "codex")
semantic_option = dispatch.session_prompt_identity(changed_option, "codex")
semantic_question = dispatch.session_prompt_identity(changed_question, "codex")
semantic_30s = dispatch.session_prompt_identity(timed_command_30, "codex")
semantic_90s = dispatch.session_prompt_identity(timed_command_90, "codex")
semantics = dispatch.session_prompt_semantics(base, "codex")

dispatch.find_tmux_session_name = lambda session_id: "fake_tmux"
dispatch.pane_settled_after_input = lambda name, before_hash: (True, "afterhash")
dispatch.subprocess.run = lambda *args, **kwargs: None
with tempfile.TemporaryDirectory() as directory:
    status_file = pathlib.Path(directory) / "status.json"
    status_file.write_text(json.dumps({"sessions": [{
        "id": "feed567800000000", "title": "Codex", "tool": "codex",
        "project": "forager", "status": "waiting"
    }]}))
    dispatch.capture_session_tail = lambda name, lines=15: volatile
    stable = dispatch.apply_session_input(
        "forager", "default", "feed5678", "approve",
        status_file=status_file,
        expected_prompt_identity=semantic_base,
        expected_interaction_kind="permission",
    )
    dispatch.capture_session_tail = lambda name, lines=15: changed_command
    changed = dispatch.apply_session_input(
        "forager", "default", "feed5678", "approve",
        status_file=status_file,
        expected_prompt_identity=semantic_base,
        expected_interaction_kind="permission",
    )
    dispatch.capture_session_tail = lambda name, lines=15: volatile
    stable_text = dispatch.apply_session_text_input(
        "forager", "default", "feed5678", "operator response",
        status_file=status_file,
        expected_prompt_identity=semantic_base,
        expected_interaction_kind="permission",
    )
    dispatch.capture_session_tail = lambda name, lines=15: changed_option
    changed_text = dispatch.apply_session_text_input(
        "forager", "default", "feed5678", "operator response",
        status_file=status_file,
        expected_prompt_identity=semantic_base,
        expected_interaction_kind="permission",
    )

print(json.dumps({
    "raw_base": raw_base,
    "raw_volatile": raw_volatile,
    "semantic_base": semantic_base,
    "semantic_volatile": semantic_volatile,
    "semantic_command": semantic_command,
    "semantic_option": semantic_option,
    "semantic_question": semantic_question,
    "semantic_30s": semantic_30s,
    "semantic_90s": semantic_90s,
    "semantics": semantics,
    "stable": stable,
    "changed": changed,
    "stable_text": stable_text,
    "changed_text": changed_text,
}))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(driver)
        .arg(scripts_dir())
        .output()
        .expect("failed to verify semantic prompt identity");
    assert!(
        output.status.success(),
        "semantic prompt probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value =
        serde_json::from_slice(&output.stdout).expect("semantic prompt probe returned non-JSON");
    assert_ne!(result["raw_base"], result["raw_volatile"]);
    assert_eq!(result["semantic_base"], result["semantic_volatile"]);
    assert_ne!(result["semantic_base"], result["semantic_command"]);
    assert_ne!(result["semantic_base"], result["semantic_option"]);
    assert_ne!(result["semantic_base"], result["semantic_question"]);
    assert_ne!(result["semantic_30s"], result["semantic_90s"]);
    assert_eq!(result["semantic_base"].as_str().map(str::len), Some(16));
    assert_eq!(result["semantics"]["interaction_kind"], "permission");
    assert!(result["semantics"]["prompt_text"]
        .as_str()
        .is_some_and(|text| text.contains("$ rm -rf target")));
    assert_eq!(
        result["semantics"]["options"].as_array().map(Vec::len),
        Some(2)
    );
    assert_eq!(result["stable"]["ok"], true);
    assert_eq!(result["stable"]["prompt_identity"], result["semantic_base"]);
    assert_eq!(
        result["stable"]["prompt_observation_hash"],
        result["raw_volatile"]
    );
    assert_eq!(result["changed"]["ok"], false);
    assert_eq!(result["changed"]["error"], "prompt_changed");
    assert_eq!(
        result["changed"]["new_prompt_identity"],
        result["semantic_command"]
    );
    assert_eq!(result["stable_text"]["ok"], true);
    assert_eq!(result["changed_text"]["ok"], false);
    assert_eq!(result["changed_text"]["error"], "prompt_changed");
}

#[test]
fn session_input_reports_no_effect_on_a_frozen_pane() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"deadbeef00000000","title":"frozen","project":"t","status":"waiting"}]}"#,
    )
    .expect("write status");

    // The pane prints the exact output shape that made the detector call this
    // session "waiting", then sleeps. Enter cannot change anything here.
    start_session(
        FROZEN_SESSION,
        "printf 'reviewing_not_approved\\nALLOWED_POSTS\\n'; sleep 300",
    );
    std::thread::sleep(std::time::Duration::from_millis(500));

    let result = apply_session_input(&status_file, "deadbeef");
    kill_session(FROZEN_SESSION);

    assert_eq!(
        result["ok"], false,
        "a pane that never repaints must not report success: {result}"
    );
    assert_eq!(result["error"], "no_effect", "unexpected error: {result}");
}

#[test]
fn session_input_succeeds_when_the_pane_reacts() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"cafe123400000000","title":"live","project":"t","status":"waiting"}]}"#,
    )
    .expect("write status");

    // An interactive shell repaints its prompt on Enter, standing in for an
    // agent that actually consumes the keystroke.
    start_session(LIVE_SESSION, "bash --norc -i");
    std::thread::sleep(std::time::Duration::from_millis(500));

    let result = apply_session_input(&status_file, "cafe1234");
    kill_session(LIVE_SESSION);

    assert_eq!(
        result["ok"], true,
        "a reacting pane must report success: {result}"
    );
}

#[test]
fn session_text_input_reaches_the_live_pane_and_submits() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"feed567800000000","title":"live","tool":"codex","project":"t","status":"running"}]}"#,
    )
    .expect("write status");
    start_session(TEXT_SESSION, "bash --norc -i");
    std::thread::sleep(std::time::Duration::from_millis(500));

    let result = apply_session_text_input(
        &status_file,
        "feed5678",
        "printf telegram-text-delivered",
        "",
    );
    std::thread::sleep(std::time::Duration::from_millis(250));
    let capture = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", TEXT_SESSION])
        .output()
        .expect("capture pane");
    kill_session(TEXT_SESSION);

    assert_eq!(result["ok"], true, "text delivery failed: {result}");
    assert_eq!(result["kind"], "session_message");
    assert_eq!(result["action"], "send_text");
    assert_eq!(result["delivery_status"], "pane_reacted");
    assert!(
        String::from_utf8_lossy(&capture.stdout).contains("telegram-text-delivered"),
        "pane did not receive message: {}",
        String::from_utf8_lossy(&capture.stdout)
    );
}

#[test]
fn session_text_input_preserves_multiline_payload() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"face567800000000","title":"live","tool":"codex","project":"t","status":"running"}]}"#,
    )
    .expect("write status");
    start_session(MULTILINE_SESSION, "bash --norc -i");
    std::thread::sleep(std::time::Duration::from_millis(500));
    let message = "printf 'first-preserved\\n'\nprintf 'second-preserved\\n'";

    let result = apply_session_text_input(&status_file, "face5678", message, "");
    std::thread::sleep(std::time::Duration::from_millis(250));
    let capture = Command::new("tmux")
        .args(["capture-pane", "-p", "-t", MULTILINE_SESSION])
        .output()
        .expect("capture pane");
    kill_session(MULTILINE_SESSION);

    assert_eq!(result["ok"], true, "multiline delivery failed: {result}");
    assert_eq!(result["message_chars"], message.chars().count());
    let pane = String::from_utf8_lossy(&capture.stdout);
    assert!(
        pane.contains("first-preserved"),
        "first line missing: {pane}"
    );
    assert!(
        pane.contains("second-preserved"),
        "second line missing: {pane}"
    );
}

#[test]
fn session_text_input_reports_queued_without_claiming_agent_acknowledgement() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"f00d567800000000","title":"quiet","tool":"codex","project":"t","status":"running"}]}"#,
    )
    .expect("write status");
    start_session(QUIET_TEXT_SESSION, "stty -echo; exec sleep 300");
    wait_for_quiet_sleep_pane(QUIET_TEXT_SESSION);

    let result = apply_session_text_input(&status_file, "f00d5678", "continue", "");
    kill_session(QUIET_TEXT_SESSION);

    assert_eq!(
        result["ok"], true,
        "tmux queue should be recorded: {result}"
    );
    assert_eq!(result["pane_reacted"], false);
    assert_eq!(result["delivery_status"], "input_queued");
}

#[test]
fn concurrent_claude_codex_and_generic_tmux_routes_only_exact_bound_messages() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let temp = tempdir().expect("tempdir");
    let worker_script = temp.path().join("harness_worker.py");
    std::fs::write(
        &worker_script,
        r#"import pathlib
import sys

receipt = pathlib.Path(sys.argv[1])
print("READY", flush=True)
for line in sys.stdin:
    message = line.rstrip("\r\n")
    with receipt.open("a", encoding="utf-8") as output:
        output.write(message + "\n")
    print("ACK:" + message, flush=True)
"#,
    )
    .expect("write harness worker");

    let cases = [
        ("Claude Benchmark", "claude", "s7-claude", "CLAUDE_ONLY"),
        ("Codex Benchmark", "codex", "s7-codex", "CODEX_ONLY"),
        (
            "Generic Tmux Benchmark",
            "generic_tmux",
            "s7-generic",
            "GENERIC_ONLY",
        ),
    ];
    let mut cleanup = TmuxInstanceCleanup(Vec::new());
    let mut rows = Vec::new();
    let mut deliveries = Vec::new();

    for (title, tool, project_key, message) in cases {
        let project = temp.path().join(project_key);
        std::fs::create_dir(&project).expect("create benchmark project");
        let receipt = temp.path().join(format!("{project_key}.receipt"));
        let command = format!(
            "python3 -u {} {}",
            shell_quote(&worker_script),
            shell_quote(&receipt)
        );
        let mut instance = Instance::new(title, &project.to_string_lossy());
        instance.tool = tool.to_string();
        instance.command = command.clone();
        let session = instance.tmux_session().expect("tmux session");
        session
            .create(&instance.project_path, Some(&command))
            .expect("create benchmark tmux session");
        cleanup.0.push(instance.clone());
        wait_for_pane_text(&instance, "READY");

        let identity = RemoteSessionIdentityV1::observe(&instance, Some(project_key))
            .expect("observe benchmark session identity");
        identity.validate().expect("validate benchmark identity");
        let identity_value = serde_json::to_value(&identity).expect("serialize identity");
        rows.push(json!({
            "id": instance.id,
            "title": title,
            "tool": tool,
            "project": project_key,
            "status": "idle",
            "remote_session_identity": identity_value,
        }));
        deliveries.push((
            instance.id.clone(),
            project_key.to_string(),
            tool.to_string(),
            message.to_string(),
            receipt,
            identity_value,
        ));
    }

    let status_file = temp.path().join("status.json");
    let status_bytes =
        serde_json::to_vec_pretty(&json!({"sessions": rows})).expect("serialize benchmark status");
    std::fs::write(&status_file, &status_bytes).expect("write benchmark status");
    let status_sha256 = format!("{:x}", Sha256::digest(&status_bytes));

    let results: Vec<Value> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for (session_id, _, _, message, _, identity) in &deliveries {
            let status_file = status_file.clone();
            let session_id = session_id.clone();
            let message = message.clone();
            let identity = identity.clone();
            handles.push(scope.spawn(move || {
                apply_session_text_input_bound(&status_file, &session_id, &message, &identity)
            }));
        }
        handles
            .into_iter()
            .map(|handle| handle.join().expect("join dispatcher"))
            .collect()
    });

    let mut binding_hashes = std::collections::HashSet::new();
    for (index, result) in results.iter().enumerate() {
        let (session_id, project_key, tool, message, receipt, identity) = &deliveries[index];
        assert_eq!(result["ok"], true, "delivery failed: {result}");
        assert_eq!(result["session_id"], *session_id);
        assert_eq!(result["project"], *project_key);
        assert_eq!(result["tool"], *tool);
        assert_eq!(
            result["remote_session_identity_sha256"],
            identity["binding_sha256"]
        );
        assert_eq!(identity["schema"], "remote_session_identity.v1");
        assert_eq!(identity["transport_kind"], "tmux");
        binding_hashes.insert(
            identity["binding_sha256"]
                .as_str()
                .expect("binding hash")
                .to_string(),
        );
        wait_for_receipt(receipt, message);
    }
    assert_eq!(binding_hashes.len(), 3);

    let claude_target = &deliveries[0];
    let codex_identity = &deliveries[1].5;
    let cross_route =
        apply_session_text_input_bound(&status_file, &claude_target.0, "MISROUTED", codex_identity);
    assert_eq!(cross_route["ok"], false);
    assert_eq!(
        cross_route["error"],
        "session_identity_changed:target_session_id_mismatch"
    );
    let cross_approval = apply_session_input_bound(&status_file, &claude_target.0, codex_identity);
    assert_eq!(cross_approval["ok"], false);
    assert_eq!(
        cross_approval["error"],
        "session_identity_changed:target_session_id_mismatch"
    );
    for (_, _, _, message, receipt, _) in &deliveries {
        assert_eq!(
            std::fs::read_to_string(receipt).expect("read exact receipt"),
            format!("{message}\n")
        );
    }
    assert_eq!(
        format!(
            "{:x}",
            Sha256::digest(std::fs::read(&status_file).expect("read benchmark status"))
        ),
        status_sha256,
        "session input must not mutate the shared routing projection"
    );
    assert!(cleanup
        .0
        .iter()
        .all(|instance| instance.tmux_session().expect("tmux session").exists()));
}

#[test]
fn session_text_input_refuses_a_stopped_session() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"cafe123400000000","title":"old","tool":"codex","project":"t","status":"stopped"}]}"#,
    )
    .expect("write status");

    let result = apply_session_text_input(&status_file, "cafe1234", "continue", "");
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "session_not_live:stopped");
}

#[test]
fn send_agent_model_action_becomes_a_session_message_intent() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let result = normalize_send_agent_intent();
    assert_eq!(result["status"], "classified");
    assert_eq!(result["intent"], "session_message");
    assert_eq!(result["session_id"], "cafe123400000000");
    assert_eq!(
        result["session_message"],
        "전체 테스트를 다시 실행하고 결과를 알려줘"
    );
}

#[test]
fn ambiguous_codex_name_is_not_allowed_to_select_the_first_live_session() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let intent = json!({
        "status": "classified",
        "intent": "session_message",
        "session_id": "lrnm000000000001",
        "session_message": "전체 테스트를 다시 실행해",
        "confidence": 0.95,
        "requires_clarification": false
    });
    let snapshot = json!({
        "live_agent_sessions": [
            {"id": "lrnm000000000001", "title": "Local-Anal", "tool": "codex", "project": "lrnm"},
            {"id": "fora000000000002", "title": "Codex", "tool": "codex", "project": "forager"},
            {"id": "scor000000000003", "title": "codex", "tool": "codex", "project": "scoreboard"}
        ]
    });

    let result = validate_send_agent_intent(
        "Codex에게 전체 테스트를 다시 실행하라고 해",
        intent,
        snapshot,
    );

    assert_eq!(result["intent"], "chat");
    assert_eq!(result["requires_clarification"], true);
    assert_eq!(result["session_target_status"], "session_target_ambiguous");
}

#[test]
fn named_project_with_multiple_agents_requires_the_agent_name() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let intent = json!({
        "status": "classified",
        "intent": "session_message",
        "session_id": "clau000000000001",
        "session_message": "그 작업 계속 진행해",
        "confidence": 0.95,
        "requires_clarification": false
    });
    let snapshot = json!({
        "project_focus": {"key": "scoreboard", "focus_source": "mention"},
        "live_agent_sessions": [
            {"id": "clau000000000001", "title": "claude", "tool": "claude", "project": "scoreboard"},
            {"id": "code000000000002", "title": "codex", "tool": "codex", "project": "scoreboard"}
        ]
    });

    let result = validate_send_agent_intent("scoreboard에서 그 작업 계속 진행해", intent, snapshot);

    assert_eq!(result["intent"], "chat");
    assert_eq!(result["session_target_status"], "session_target_ambiguous");
}

#[test]
fn explicit_project_and_agent_resolve_one_exact_session() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let intent = json!({
        "status": "classified",
        "intent": "session_message",
        "session_id": "code000000000002",
        "session_message": "그 작업 계속 진행해",
        "confidence": 0.95,
        "requires_clarification": false
    });
    let snapshot = json!({
        "project_focus": {"key": "scoreboard", "focus_source": "mention"},
        "live_agent_sessions": [
            {"id": "clau000000000001", "title": "claude", "tool": "claude", "project": "scoreboard"},
            {"id": "code000000000002", "title": "codex", "tool": "codex", "project": "scoreboard"}
        ]
    });

    let result =
        validate_send_agent_intent("scoreboard Codex에서 그 작업 계속 진행해", intent, snapshot);

    assert_eq!(result["intent"], "session_message");
    assert_eq!(result["session_id"], "code000000000002");
    assert_eq!(result["session_target_status"], "resolved");
}

#[test]
fn model_clarification_flag_always_blocks_session_delivery() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }
    let intent = json!({
        "status": "classified",
        "intent": "session_message",
        "session_id": "code000000000002",
        "session_message": "continue",
        "confidence": 0.99,
        "requires_clarification": true,
        "clarifying_question": "어느 세션인가요?"
    });
    let snapshot = json!({
        "live_agent_sessions": [
            {"id": "code000000000002", "title": "codex", "tool": "codex", "project": "scoreboard"}
        ]
    });

    let result = validate_send_agent_intent("계속해", intent, snapshot);

    assert_eq!(result["intent"], "chat");
    assert_eq!(
        result["session_target_status"],
        "model_requires_clarification"
    );
}

#[test]
fn telegram_reply_to_waiting_card_binds_the_agent_session() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let result = resolve_replied_session_context();
    assert_eq!(result["session_id"], "feed567800000000");
    assert_eq!(result["project"], "forager");
    assert_eq!(result["prompt_hash"], "abcd1234");
    assert_eq!(result["source"], "reply_card");
}

#[test]
fn session_text_input_refuses_a_changed_card_bound_prompt() {
    if !tool_available("tmux", "-V") || !tool_available("python3", "--version") {
        eprintln!("Skipping test: tmux or python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"bead987600000000","title":"changed","tool":"codex","project":"t","status":"waiting"}]}"#,
    )
    .expect("write status");
    start_session(HASH_SESSION, "bash --norc -i");
    std::thread::sleep(std::time::Duration::from_millis(500));

    let result = apply_session_text_input(
        &status_file,
        "bead9876",
        "this must not be delivered",
        "stale123",
    );
    kill_session(HASH_SESSION);

    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "prompt_changed");
}

#[test]
fn session_text_input_refuses_an_ambiguous_id_prefix() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[
            {"id":"feed567800000001","status":"running"},
            {"id":"feed567800000002","status":"idle"}
        ]}"#,
    )
    .expect("write status");

    let result = apply_session_text_input(&status_file, "feed5678", "continue", "");
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "session_id_ambiguous");
}

#[test]
fn natural_session_text_input_requires_the_full_exact_id() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"feed567800000001","status":"running"}]}"#,
    )
    .expect("write status");

    let result = apply_session_text_input_exact(&status_file, "feed5678", "continue");
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "session_id_not_exact");
}

#[test]
fn session_text_input_never_defaults_an_empty_id_to_the_only_session() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let dir = tempdir().expect("tempdir");
    let status_file = dir.path().join("status.json");
    std::fs::write(
        &status_file,
        r#"{"sessions":[{"id":"feed567800000001","status":"running"}]}"#,
    )
    .expect("write status");

    let result = apply_session_text_input(&status_file, "", "continue", "");
    assert_eq!(result["ok"], false);
    assert_eq!(result["error"], "session_id_missing");
}

#[test]
fn direct_session_message_dry_run_never_calls_the_tmux_dispatcher() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let result = render_session_message_dry_run();
    assert_eq!(result["read_only"], true);
    assert!(result.get("dispatch_result").is_none());
    assert_eq!(
        result["session_message_preview"]["session_id"],
        "cafe123400000000"
    );
    assert!(result["message_preview"]
        .as_str()
        .expect("message preview")
        .contains("tmux 입력을 보내지 않았습니다"));
}

#[test]
fn no_effect_renders_the_dead_input_message() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let result = render_session_input_error("no_effect");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(
        preview.contains("입력이 먹히지 않았습니다"),
        "unexpected preview: {preview}"
    );
    assert!(
        !preview.contains("프롬프트가 바뀌어"),
        "wrong error branch: {preview}"
    );
}

#[test]
fn prompt_changed_keeps_its_refresh_message() {
    if !tool_available("python3", "--version") {
        eprintln!("Skipping test: python3 not available");
        return;
    }

    let result = render_session_input_error("prompt_changed");
    let preview = result["message_preview"].as_str().expect("message preview");
    assert!(
        preview.contains("프롬프트가 바뀌어"),
        "unexpected preview: {preview}"
    );
    assert!(
        preview.contains("Allow the new command?"),
        "missing current prompt: {preview}"
    );
}
