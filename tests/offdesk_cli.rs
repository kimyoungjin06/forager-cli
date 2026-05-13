use anyhow::Result;
use chrono::{Duration, Utc};
use fs2::FileExt;
use serde_json::json;
use serial_test::serial;
use std::fs;
use std::fs::OpenOptions;
use std::path::Path;
use std::process::Command;
use std::thread;
use std::time::Duration as StdDuration;
use tempfile::tempdir;

fn legacy_aoe_command(home: &std::path::Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_aoe"));
    command.env("HOME", home);
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command.env_remove("FORAGER_DEBUG");
    command.env_remove("AGENT_OF_EMPIRES_DEBUG");
    #[cfg(target_os = "linux")]
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command
}

fn forager_command(home: &std::path::Path) -> Command {
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

fn app_dir(home: &std::path::Path) -> std::path::PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config").join("forager")
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".forager")
    }
}

fn legacy_app_dir(home: &std::path::Path) -> std::path::PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config").join("agent-of-empires")
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".agent-of-empires")
    }
}

fn normalize_test_path(path: &str) -> String {
    #[cfg(target_os = "macos")]
    {
        path.strip_prefix("/private").unwrap_or(path).to_owned()
    }
    #[cfg(not(target_os = "macos"))]
    {
        path.to_owned()
    }
}

fn expected_path(path: &Path) -> String {
    normalize_test_path(&path.display().to_string())
}

fn reported_path(value: &serde_json::Value) -> String {
    normalize_test_path(value.as_str().expect("path string"))
}

fn profile_dir(home: &std::path::Path) -> std::path::PathBuf {
    profile_dir_for(home, "default")
}

fn profile_dir_for(home: &std::path::Path, profile: &str) -> std::path::PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config")
            .join("forager")
            .join("profiles")
            .join(profile)
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".forager").join("profiles").join(profile)
    }
}

fn legacy_profile_dir_for(home: &std::path::Path, profile: &str) -> std::path::PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config")
            .join("agent-of-empires")
            .join("profiles")
            .join(profile)
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".agent-of-empires")
            .join("profiles")
            .join(profile)
    }
}

fn wait_for_path(path: &Path) {
    for _ in 0..40 {
        if path.exists() {
            return;
        }
        thread::sleep(StdDuration::from_millis(50));
    }
}

fn durable_task(
    status: &str,
    now: chrono::DateTime<Utc>,
    command: &str,
    workdir: &Path,
) -> serde_json::Value {
    durable_task_with("task", "inspect.status", status, now, command, workdir)
}

fn durable_task_with(
    task_id: &str,
    capability_id: &str,
    status: &str,
    now: chrono::DateTime<Utc>,
    command: &str,
    workdir: &Path,
) -> serde_json::Value {
    json!({
        "task_id": task_id,
        "request_id": "request",
        "project_key": "project",
        "status": status,
        "capability_id": capability_id,
        "runner_kind": "local_background",
        "command": command,
        "workdir": workdir.to_str().expect("utf-8 path"),
        "background_ticket_id": "ticket",
        "attempt_count": 1,
        "last_gate_status": "denied",
        "last_error": "token=sk-secretsecretsecretsecret",
        "created_at": now,
        "updated_at": now,
        "preview": "preview token=sk-secretsecretsecretsecret",
        "reason": "reason token=sk-secretsecretsecretsecret"
    })
}

fn denied_approval(action: &str, now: chrono::DateTime<Utc>) -> serde_json::Value {
    json!({
        "approval_id": "approval_denied",
        "status": "denied",
        "scope": "once",
        "project_key": "project",
        "request_id": "request",
        "task_id": "task",
        "action": action,
        "risk_level": "runtime_mutation",
        "approval_mode": "operator_required",
        "preview": "safe preview",
        "reason": "operator denied",
        "created_at": now,
        "expires_at": now + Duration::minutes(10),
        "resolved_at": now,
        "resolved_by": "operator",
        "source_surface": "test"
    })
}

fn resume_state(now: chrono::DateTime<Utc>) -> serde_json::Value {
    json!({
        "task_id": "task",
        "request_id": "request",
        "project_key": "project",
        "status": "resume_pending",
        "phase": "background",
        "runner_target": "local_background",
        "background_ticket_id": "ticket",
        "last_evidence_artifacts": [],
        "next_safe_resume_step": "inspect result sidecar",
        "interrupted_at": now,
        "interruption_reason": "restart token=sk-secretsecretsecretsecret",
        "fresh_until": now + Duration::minutes(10)
    })
}

#[test]
#[serial]
fn offdesk_pending_and_ok_resolve_approval() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": "approval_one",
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request",
                "task_id": "task",
                "action": "dispatch.runtime",
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": "safe preview",
                "reason": "outside envelope",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "pending", "--json"])
        .output()?;
    assert!(pending_output.status.success());
    let pending: serde_json::Value = serde_json::from_slice(&pending_output.stdout)?;
    assert_eq!(pending.as_array().expect("array").len(), 1);
    assert_eq!(pending[0]["action_id"], serde_json::Value::Null);

    let ok_output = forager_command(temp.path())
        .args(["offdesk", "ok", "approval_one", "--json"])
        .output()?;
    assert!(ok_output.status.success());
    let approved: serde_json::Value = serde_json::from_slice(&ok_output.stdout)?;
    assert_eq!(approved["status"], "approved");
    assert_eq!(approved["approval_id"], "approval_one");

    let audit = fs::read_to_string(profile_dir.join("action_audit.jsonl"))?;
    assert!(audit.contains("\"transition\":\"approve\""));
    assert!(audit.contains("\"action_id\":\"approval_one\""));
    assert!(audit.contains("\"result\":\"approved\""));
    assert!(audit.contains("\"resolved_by\":\"cli\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_resume_json_reports_artifacts() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "resume_pending",
                "phase": "background",
                "runner_target": "local_tmux",
                "last_evidence_artifacts": [],
                "next_safe_resume_step": "inspect result sidecar",
                "interrupted_at": now,
                "interruption_reason": "restart",
                "fresh_until": now + Duration::minutes(10)
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "resume", "--json"])
        .output()?;
    assert!(output.status.success());
    let states: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(states[0]["status"], "resume_pending");
    assert_eq!(states[0]["next_safe_resume_step"], "inspect result sidecar");
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_creates_pending_approval_for_runtime_mutation_without_brief() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "dispatch.runtime",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--preview",
            "token=sk-secretsecretsecretsecret",
            "--reason",
            "outside envelope",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["status"], "pending_approval");
    assert_eq!(outcome["approval"]["status"], "pending");
    assert!(outcome["approval"]["action_id"]
        .as_str()
        .expect("action id")
        .starts_with("action_"));
    assert!(!outcome["approval"]["preview"]
        .as_str()
        .expect("preview")
        .contains("sk-secret"));

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert!(approvals[0]["action_id"]
        .as_str()
        .expect("action id")
        .starts_with("action_"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_proceeds_for_runtime_mutation_inside_execution_brief() -> Result<()> {
    let temp = tempdir()?;
    let brief_path = temp.path().join("brief.json");
    let now = Utc::now();
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "request",
            "task_id": "task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(10)
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "dispatch.runtime",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--brief",
            brief_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["status"], "proceed");
    assert_eq!(outcome["approval_mode"], "envelope_auto");

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 0);
    Ok(())
}

#[test]
#[serial]
fn offdesk_launch_without_brief_creates_pending_and_no_background_run() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "launch",
            "background.launch",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--ticket-id",
            "ticket",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["gate"]["status"], "pending_approval");
    assert!(outcome.get("probe").is_none());

    let profile_dir = profile_dir(temp.path());
    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert!(!profile_dir.join("background_runs.json").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_launch_with_brief_records_background_run() -> Result<()> {
    let temp = tempdir()?;
    let brief_path = temp.path().join("brief.json");
    let now = Utc::now();
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "request",
            "task_id": "task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["background.launch"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(10)
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "launch",
            "background.launch",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--ticket-id",
            "ticket",
            "--launch-spec",
            "token=sk-secretsecretsecretsecret",
            "--brief",
            brief_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["gate"]["status"], "proceed");
    assert_eq!(outcome["probe"]["ticket_id"], "ticket");
    assert_eq!(outcome["probe"]["phase"], "launched");
    assert!(!outcome["probe"]["launch_spec_summary"]
        .as_str()
        .expect("summary")
        .contains("sk-secret"));

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["ticket_id"], "ticket");
    Ok(())
}

#[test]
#[serial]
fn offdesk_poll_persists_background_phase_transition() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "runner_kind": "local_background",
                "phase": "launched",
                "runtime_handle_alive": false,
                "result_artifact_present": true
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "poll", "ticket", "--json"])
        .output()?;

    assert!(output.status.success());
    let outcomes: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcomes[0]["decision"]["phase"], "completed");

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["phase"], "completed");
    Ok(())
}

#[test]
#[serial]
fn offdesk_launch_executes_local_background_command_and_poll_completes() -> Result<()> {
    let temp = tempdir()?;
    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("result.txt");
    let log_path = temp.path().join("background.log");
    let now = Utc::now();
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "request",
            "task_id": "task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["background.launch"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(10)
        }))?,
    )?;
    let command = format!(
        "printf 'token=sk-secretsecretsecretsecret\\n'; printf done > {}",
        result_path.display()
    );

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "launch",
            "background.launch",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--ticket-id",
            "ticket",
            "--brief",
            brief_path.to_str().expect("utf-8 path"),
            "--cmd",
            command.as_str(),
            "--workdir",
            temp.path().to_str().expect("utf-8 path"),
            "--log-artifact",
            log_path.to_str().expect("utf-8 path"),
            "--result-artifact",
            result_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["gate"]["status"], "proceed");
    assert_eq!(outcome["probe"]["ticket_id"], "ticket");
    assert_eq!(
        outcome["probe"]["log_artifact_path"].as_str(),
        Some(log_path.to_string_lossy().as_ref())
    );
    assert_eq!(
        outcome["probe"]["result_artifact_path"].as_str(),
        Some(result_path.to_string_lossy().as_ref())
    );

    wait_for_path(&result_path);
    assert!(result_path.exists());

    let poll_output = forager_command(temp.path())
        .args(["offdesk", "poll", "ticket", "--json"])
        .output()?;
    assert!(poll_output.status.success());
    let outcomes: serde_json::Value = serde_json::from_slice(&poll_output.stdout)?;
    assert_eq!(outcomes[0]["decision"]["phase"], "completed");
    assert!(!outcomes[0]["probe"]["last_log_tail"]
        .as_str()
        .expect("log tail")
        .contains("sk-secret"));

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["phase"], "completed");
    assert!(runs[0]["log_artifact_present"].as_bool().unwrap_or(false));
    assert!(runs[0]["result_artifact_present"]
        .as_bool()
        .unwrap_or(false));
    Ok(())
}

#[test]
#[serial]
fn offdesk_enqueue_tasks_json_redacts_command() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--cmd",
            "printf token=sk-secretsecretsecretsecret",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let task: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(task["task_id"], "task");
    assert!(!task["command"]
        .as_str()
        .expect("command")
        .contains("sk-secret"));

    let tasks_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--json"])
        .output()?;
    assert!(tasks_output.status.success());
    let tasks: serde_json::Value = serde_json::from_slice(&tasks_output.stdout)?;
    assert!(!tasks[0]["command"]
        .as_str()
        .expect("command")
        .contains("sk-secret"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_tasks_human_includes_recovery_commands_and_redacts_secrets() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            durable_task_with(
                "failed-task",
                "dispatch.runtime",
                "failed",
                now,
                "printf token=sk-secretsecretsecretsecret",
                temp.path(),
            ),
            durable_task_with(
                "resume-task",
                "dispatch.runtime",
                "resume_pending",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "queued-task",
                "dispatch.runtime",
                "queued",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "done-task",
                "dispatch.runtime",
                "completed",
                now,
                "true",
                temp.path(),
            )
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "tasks"])
        .output()?;

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("forager offdesk retry-task failed-task"));
    assert!(stdout.contains("forager offdesk retry-task failed-task --new-approval"));
    assert!(stdout.contains("forager offdesk resume-task resume-task"));
    assert!(stdout.contains("forager offdesk abandon-task resume-task"));
    assert!(stdout.contains("forager offdesk cancel-task queued-task"));
    assert!(stdout.contains("terminal: no action needed"));
    assert!(!stdout.contains("sk-secret"));
    Ok(())
}

#[test]
#[serial]
fn forager_binary_uses_forager_storage_layout() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;

    assert!(output.status.success());
    assert!(profile_dir(temp.path()).exists());
    Ok(())
}

#[test]
#[serial]
fn forager_binary_falls_back_to_existing_legacy_storage_layout() -> Result<()> {
    let temp = tempdir()?;
    fs::create_dir_all(legacy_profile_dir_for(temp.path(), "default"))?;

    let output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;

    assert!(output.status.success());
    assert!(legacy_profile_dir_for(temp.path(), "default").exists());
    assert!(!profile_dir(temp.path()).exists());
    Ok(())
}

#[test]
#[serial]
fn forager_profile_env_takes_precedence_over_legacy_env() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .env("FORAGER_PROFILE", "new-name")
        .env("AGENT_OF_EMPIRES_PROFILE", "old-name")
        .args(["status", "--json"])
        .output()?;

    assert!(output.status.success());
    assert!(profile_dir_for(temp.path(), "new-name").exists());
    assert!(!profile_dir_for(temp.path(), "old-name").exists());
    Ok(())
}

#[test]
#[serial]
fn legacy_profile_env_still_works() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .env("AGENT_OF_EMPIRES_PROFILE", "legacy-name")
        .args(["status", "--json"])
        .output()?;

    assert!(output.status.success());
    assert!(profile_dir_for(temp.path(), "legacy-name").exists());
    Ok(())
}

#[test]
#[serial]
fn forager_init_creates_forager_repo_config() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;

    let output = forager_command(home.path())
        .args(["init", repo.path().to_str().expect("utf-8 path")])
        .output()?;

    assert!(output.status.success());
    assert!(repo.path().join(".forager").join("config.toml").exists());
    assert!(!repo.path().join(".aoe").join("config.toml").exists());
    Ok(())
}

#[test]
#[serial]
fn forager_init_refuses_existing_legacy_repo_config() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    let legacy_dir = repo.path().join(".aoe");
    fs::create_dir_all(&legacy_dir)?;
    fs::write(legacy_dir.join("config.toml"), "# legacy\n")?;

    let output = forager_command(home.path())
        .args(["init", repo.path().to_str().expect("utf-8 path")])
        .output()?;

    assert!(!output.status.success());
    assert!(!repo.path().join(".forager").join("config.toml").exists());
    Ok(())
}

#[test]
#[serial]
fn forager_add_rejects_deferred_sandbox_flags() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    let deferred = "Docker sandbox support is deferred while Forager decides whether to benchmark";

    let sandbox_output = forager_command(home.path())
        .current_dir(repo.path())
        .args(["add", "--sandbox", "."])
        .output()?;
    assert!(!sandbox_output.status.success());
    assert!(
        String::from_utf8_lossy(&sandbox_output.stderr).contains(deferred),
        "stderr: {}",
        String::from_utf8_lossy(&sandbox_output.stderr)
    );

    let image_output = forager_command(home.path())
        .current_dir(repo.path())
        .args(["add", "--sandbox-image", "custom/image:latest", "."])
        .output()?;
    assert!(!image_output.status.success());
    assert!(
        String::from_utf8_lossy(&image_output.stderr).contains(deferred),
        "stderr: {}",
        String::from_utf8_lossy(&image_output.stderr)
    );

    Ok(())
}

#[test]
#[serial]
fn forager_doctor_reports_new_primary_paths_without_creating_storage() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;

    let output = forager_command(home.path())
        .args([
            "doctor",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["profile"]["active"], "default");
    assert_eq!(report["global_data"]["active_source"], "new_primary");
    assert_eq!(
        reported_path(&report["global_data"]["primary_path"]),
        expected_path(&app_dir(home.path()))
    );
    assert!(!app_dir(home.path()).exists());
    assert_eq!(report["repo_config"]["active_source"], "none");
    assert_eq!(
        reported_path(&report["repo_config"]["primary_path"]),
        expected_path(&repo.path().join(".forager").join("config.toml"))
    );
    Ok(())
}

#[test]
#[serial]
fn forager_doctor_reports_legacy_storage_and_repo_config() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    fs::create_dir_all(legacy_app_dir(home.path()).join("profiles").join("default"))?;
    let legacy_repo_dir = repo.path().join(".aoe");
    fs::create_dir_all(&legacy_repo_dir)?;
    fs::write(legacy_repo_dir.join("config.toml"), "# legacy\n")?;

    let output = forager_command(home.path())
        .args([
            "doctor",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["global_data"]["active_source"], "legacy");
    assert_eq!(
        reported_path(&report["global_data"]["active_path"]),
        expected_path(&legacy_app_dir(home.path()))
    );
    assert_eq!(report["repo_config"]["active_source"], "legacy");
    assert_eq!(
        reported_path(&report["repo_config"]["active_path"]),
        expected_path(&repo.path().join(".aoe").join("config.toml"))
    );
    assert!(!app_dir(home.path()).exists());
    Ok(())
}

#[test]
#[serial]
fn forager_doctor_reports_profile_env_precedence() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;

    let output = forager_command(home.path())
        .env("FORAGER_PROFILE", "new-name")
        .env("AGENT_OF_EMPIRES_PROFILE", "old-name")
        .args([
            "doctor",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["profile"]["active"], "new-name");
    assert_eq!(report["profile"]["source"], "--profile/FORAGER_PROFILE");
    assert_eq!(report["env"]["forager_profile_set"], true);
    assert_eq!(report["env"]["legacy_profile_set"], true);
    Ok(())
}

#[test]
#[serial]
fn forager_migrate_aoe_copies_legacy_paths_and_preserves_sources() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    let legacy_dir = legacy_app_dir(home.path());
    fs::create_dir_all(legacy_dir.join("profiles").join("default"))?;
    fs::write(legacy_dir.join("config.toml"), "legacy global")?;
    fs::write(
        legacy_dir
            .join("profiles")
            .join("default")
            .join("sessions.json"),
        "[]",
    )?;
    let legacy_repo_dir = repo.path().join(".aoe");
    fs::create_dir_all(&legacy_repo_dir)?;
    fs::write(legacy_repo_dir.join("config.toml"), "# legacy repo\n")?;

    let output = forager_command(home.path())
        .args([
            "migrate",
            "aoe",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["migration"], "aoe");
    assert_eq!(report["mode"], "copy");
    assert_eq!(report["has_conflicts"], false);
    assert!(report["operations"]
        .as_array()
        .expect("operations")
        .iter()
        .all(|operation| operation["status"] == "copied"));
    assert_eq!(
        fs::read_to_string(app_dir(home.path()).join("config.toml"))?,
        "legacy global"
    );
    assert_eq!(
        fs::read_to_string(
            app_dir(home.path())
                .join("profiles")
                .join("default")
                .join("sessions.json")
        )?,
        "[]"
    );
    assert_eq!(
        fs::read_to_string(repo.path().join(".forager").join("config.toml"))?,
        "# legacy repo\n"
    );
    assert!(legacy_dir.join("config.toml").exists());
    assert!(legacy_repo_dir.join("config.toml").exists());
    Ok(())
}

#[test]
#[serial]
fn forager_migrate_aoe_dry_run_does_not_copy() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    fs::create_dir_all(legacy_app_dir(home.path()).join("profiles").join("default"))?;
    let legacy_repo_dir = repo.path().join(".aoe");
    fs::create_dir_all(&legacy_repo_dir)?;
    fs::write(legacy_repo_dir.join("config.toml"), "# legacy repo\n")?;

    let output = forager_command(home.path())
        .args([
            "migrate",
            "aoe",
            "--dry-run",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["dry_run"], true);
    assert_eq!(report["has_conflicts"], false);
    assert!(report["operations"]
        .as_array()
        .expect("operations")
        .iter()
        .all(|operation| operation["status"] == "would_copy"));
    assert!(!app_dir(home.path()).exists());
    assert!(!repo.path().join(".forager").join("config.toml").exists());
    Ok(())
}

#[test]
#[serial]
fn forager_migrate_aoe_refuses_conflicts_before_copying_anything() -> Result<()> {
    let home = tempdir()?;
    let repo = tempdir()?;
    fs::create_dir_all(legacy_app_dir(home.path()).join("profiles").join("default"))?;
    fs::create_dir_all(app_dir(home.path()))?;
    fs::write(app_dir(home.path()).join("config.toml"), "primary global")?;
    let legacy_repo_dir = repo.path().join(".aoe");
    fs::create_dir_all(&legacy_repo_dir)?;
    fs::write(legacy_repo_dir.join("config.toml"), "# legacy repo\n")?;

    let output = forager_command(home.path())
        .args([
            "migrate",
            "aoe",
            "--json",
            "--project",
            repo.path().to_str().expect("utf-8 path"),
        ])
        .output()?;

    assert!(!output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["has_conflicts"], true);
    let operations = report["operations"].as_array().expect("operations");
    assert!(operations.iter().any(|operation| {
        operation["scope"] == "global_data"
            && operation["status"] == "conflict"
            && operation["reason"] == "target_exists"
    }));
    assert!(operations.iter().any(|operation| {
        operation["scope"] == "repo_config"
            && operation["status"] == "blocked"
            && operation["reason"] == "conflict_in_plan"
    }));
    assert_eq!(
        fs::read_to_string(app_dir(home.path()).join("config.toml"))?,
        "primary global"
    );
    assert!(!repo.path().join(".forager").join("config.toml").exists());
    Ok(())
}

#[test]
#[serial]
fn aoe_human_commands_warn_that_alias_is_legacy() -> Result<()> {
    let temp = tempdir()?;

    let output = legacy_aoe_command(temp.path()).args(["status"]).output()?;

    assert!(output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("`aoe` is a legacy alias; use `forager` instead."));
    Ok(())
}

#[test]
#[serial]
fn aoe_script_safe_commands_do_not_emit_deprecation_warning() -> Result<()> {
    let temp = tempdir()?;

    for args in [
        vec!["status", "--json"],
        vec!["status", "-q"],
        vec!["tmux", "status"],
        vec!["completion", "bash"],
    ] {
        let output = legacy_aoe_command(temp.path()).args(args).output()?;
        assert!(output.status.success());
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            !stderr.contains("legacy alias"),
            "unexpected warning for stderr: {stderr}"
        );
    }

    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_launches_briefed_task_and_completes_from_sidecar() -> Result<()> {
    let temp = tempdir()?;
    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("tick-result.txt");
    let now = Utc::now();
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "request",
            "task_id": "task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(10)
        }))?,
    )?;
    let command = format!("printf done > {}", result_path.display());

    let enqueue_output = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--brief",
            brief_path.to_str().expect("utf-8 path"),
            "--cmd",
            command.as_str(),
            "--workdir",
            temp.path().to_str().expect("utf-8 path"),
            "--result-artifact",
            result_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(enqueue_output.status.success());

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["launched"], 1);

    wait_for_path(&result_path);
    let complete_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(complete_output.status.success());
    let complete: serde_json::Value = serde_json::from_slice(&complete_output.stdout)?;
    assert_eq!(complete["completed"], 1);

    let tasks: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("offdesk_tasks.json"),
    )?)?;
    assert_eq!(tasks[0]["status"], "completed");
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_pending_approval_then_ok_launches_next_tick() -> Result<()> {
    let temp = tempdir()?;
    let result_path = temp.path().join("approved-result.txt");
    let command = format!("printf done > {}", result_path.display());

    let enqueue_output = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--cmd",
            command.as_str(),
            "--workdir",
            temp.path().to_str().expect("utf-8 path"),
            "--result-artifact",
            result_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(enqueue_output.status.success());

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(pending_output.status.success());
    let pending: serde_json::Value = serde_json::from_slice(&pending_output.stdout)?;
    assert_eq!(pending["pending_approval"], 1);

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);

    let ok_output = forager_command(temp.path())
        .args(["offdesk", "ok", "--json"])
        .output()?;
    assert!(ok_output.status.success());

    let launch_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(launch_output.status.success());
    let launched: serde_json::Value = serde_json::from_slice(&launch_output.stdout)?;
    assert_eq!(launched["launched"], 1);

    let approvals_after: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals_after[0]["status"], "superseded");
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_stale_background_creates_resume_state() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "running",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": "true",
                "workdir": temp.path().to_str().expect("utf-8 path"),
                "background_ticket_id": "ticket",
                "attempt_count": 1,
                "created_at": now,
                "updated_at": now
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "runner_kind": "local_background",
                "phase": "launched",
                "runtime_handle_alive": false
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["resume_pending"], 1);

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "resume_pending");

    let resume: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("task_resume_state.json"),
    )?)?;
    assert_eq!(resume[0]["status"], "resume_pending");
    assert_eq!(resume[0]["background_ticket_id"], "ticket");
    Ok(())
}

#[test]
#[serial]
fn offdesk_cancel_task_updates_task_json_and_redacts_output() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task(
            "running",
            now,
            "printf token=sk-secretsecretsecretsecret",
            temp.path(),
        )]))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "cancel-task",
            "task",
            "--reason",
            "operator stop token=sk-secretsecretsecretsecret",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["changed"], true);
    assert_eq!(report["status"], "cancelled");
    assert!(!String::from_utf8_lossy(&output.stdout).contains("sk-secret"));

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "cancelled");
    assert_eq!(tasks[0]["background_ticket_id"], "ticket");
    Ok(())
}

#[test]
#[serial]
fn offdesk_retry_task_requeues_failed_task_and_tick_launches() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let result_path = temp.path().join("retry-result.txt");
    let command = format!("printf done > {}", result_path.display());
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task(
            "failed",
            now,
            command.as_str(),
            temp.path(),
        )]))?,
    )?;

    let retry_output = forager_command(temp.path())
        .args(["offdesk", "retry-task", "task", "--json"])
        .output()?;
    assert!(retry_output.status.success());
    let retry: serde_json::Value = serde_json::from_slice(&retry_output.stdout)?;
    assert_eq!(retry["changed"], true);
    assert_eq!(retry["status"], "queued");
    assert!(retry["task"].get("background_ticket_id").is_none());

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["launched"], 1);

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "launched");
    assert_ne!(tasks[0]["background_ticket_id"], "ticket");
    Ok(())
}

#[test]
#[serial]
fn offdesk_plain_retry_preserves_denied_approval_and_tick_denies() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task",
            "dispatch.runtime",
            "failed",
            now,
            "true",
            temp.path(),
        )]))?,
    )?;
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([denied_approval("dispatch.runtime", now)]))?,
    )?;

    let retry_output = forager_command(temp.path())
        .args(["offdesk", "retry-task", "task", "--json"])
        .output()?;
    assert!(retry_output.status.success());
    let retry: serde_json::Value = serde_json::from_slice(&retry_output.stdout)?;
    assert_eq!(retry["status"], "queued");
    assert_eq!(retry["superseded_denied_approvals"], 0);

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["failed"], 1);

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals[0]["status"], "denied");
    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "failed");
    Ok(())
}

#[test]
#[serial]
fn offdesk_retry_new_approval_supersedes_denied_and_tick_creates_pending() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task",
            "dispatch.runtime",
            "failed",
            now,
            "true",
            temp.path(),
        )]))?,
    )?;
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([denied_approval("dispatch.runtime", now)]))?,
    )?;

    let retry_output = forager_command(temp.path())
        .args(["offdesk", "retry-task", "task", "--new-approval", "--json"])
        .output()?;
    assert!(retry_output.status.success());
    let retry: serde_json::Value = serde_json::from_slice(&retry_output.stdout)?;
    assert_eq!(retry["status"], "queued");
    assert_eq!(retry["superseded_denied_approvals"], 1);

    let approvals_after_retry: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals_after_retry[0]["status"], "superseded");

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["pending_approval"], 1);

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 2);
    assert_eq!(approvals[0]["status"], "superseded");
    assert_eq!(approvals[1]["status"], "pending");
    let audit = fs::read_to_string(profile_dir.join("action_audit.jsonl"))?;
    assert!(audit.contains("\"transition\":\"supersede_denied\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_retry_new_approval_reports_supersede_even_when_retry_noops() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task",
            "dispatch.runtime",
            "queued",
            now,
            "true",
            temp.path(),
        )]))?,
    )?;
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([denied_approval("dispatch.runtime", now)]))?,
    )?;

    let retry_output = forager_command(temp.path())
        .args(["offdesk", "retry-task", "task", "--new-approval", "--json"])
        .output()?;
    assert!(retry_output.status.success());
    let retry: serde_json::Value = serde_json::from_slice(&retry_output.stdout)?;
    assert_eq!(retry["changed"], false);
    assert_eq!(retry["status"], "queued");
    assert_eq!(retry["superseded_denied_approvals"], 1);

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals[0]["status"], "superseded");
    Ok(())
}

#[test]
#[serial]
fn offdesk_resume_task_marks_resume_artifact_resumed_and_tick_launches() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let result_path = temp.path().join("resume-result.txt");
    let command = format!("printf done > {}", result_path.display());
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task(
            "resume_pending",
            now,
            command.as_str(),
            temp.path(),
        )]))?,
    )?;
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([resume_state(now)]))?,
    )?;

    let resume_output = forager_command(temp.path())
        .args(["offdesk", "resume-task", "task", "--json"])
        .output()?;
    assert!(resume_output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&resume_output.stdout)?;
    assert_eq!(report["changed"], true);
    assert_eq!(report["status"], "queued");

    let resume: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("task_resume_state.json"),
    )?)?;
    assert_eq!(resume[0]["status"], "resumed");

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["launched"], 1);
    Ok(())
}

#[test]
#[serial]
fn offdesk_abandon_task_cancels_resume_pending_and_prevents_dispatch() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task(
            "resume_pending",
            now,
            "true",
            temp.path(),
        )]))?,
    )?;
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([resume_state(now)]))?,
    )?;

    let abandon_output = forager_command(temp.path())
        .args(["offdesk", "abandon-task", "task", "--json"])
        .output()?;
    assert!(abandon_output.status.success());
    let abandon: serde_json::Value = serde_json::from_slice(&abandon_output.stdout)?;
    assert_eq!(abandon["status"], "cancelled");

    let tick_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(tick_output.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["launched"], 0);

    let resume: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("task_resume_state.json"),
    )?)?;
    assert_eq!(resume[0]["status"], "abandoned");
    Ok(())
}

#[test]
#[serial]
fn status_json_includes_offdesk_counts() -> Result<()> {
    let temp = tempdir()?;

    let enqueue_output = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--cmd",
            "true",
            "--json",
        ])
        .output()?;
    assert!(enqueue_output.status.success());

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["queued_offdesk_tasks"], 1);
    assert_eq!(status["pending_approvals"], 0);
    assert_eq!(status["failed_offdesk_tasks"], 0);
    assert_eq!(status["resume_pending_offdesk_tasks"], 0);
    assert_eq!(status["cancelled_offdesk_tasks"], 0);
    Ok(())
}

#[test]
#[serial]
fn status_json_includes_offdesk_recovery_counts() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            durable_task_with(
                "failed-task",
                "dispatch.runtime",
                "failed",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "resume-task",
                "dispatch.runtime",
                "resume_pending",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "cancelled-task",
                "dispatch.runtime",
                "cancelled",
                now,
                "true",
                temp.path(),
            )
        ]))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["failed_offdesk_tasks"], 1);
    assert_eq!(status["resume_pending_offdesk_tasks"], 1);
    assert_eq!(status["cancelled_offdesk_tasks"], 1);
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_rejects_concurrent_lock() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let lock_path = profile_dir.join("offdesk_tick.lock");
    let lock_file = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(&lock_path)?;
    FileExt::lock_exclusive(&lock_file)?;

    let output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("offdesk tick already running"));
    FileExt::unlock(&lock_file)?;
    Ok(())
}
