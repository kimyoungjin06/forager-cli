use anyhow::Result;
use chrono::{Duration, Utc};
use fs2::FileExt;
use serde_json::json;
use serial_test::serial;
use sha2::{Digest, Sha256};
use std::fs;
use std::fs::OpenOptions;
use std::path::{Path, PathBuf};
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

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn count_files_with_prefix(directory: &Path, prefix: &str) -> Result<usize> {
    if !directory.exists() {
        return Ok(0);
    }
    let mut count = 0;
    for entry in fs::read_dir(directory)? {
        if entry?.file_name().to_string_lossy().starts_with(prefix) {
            count += 1;
        }
    }
    Ok(count)
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

fn write_implementation_packet_fixture(
    home: &std::path::Path,
    project_key: &str,
    packet_id: &str,
) -> Result<std::path::PathBuf> {
    let packet_dir = profile_dir(home)
        .join("implementation_packets")
        .join(format!("20260603T000000Z_{project_key}"));
    fs::create_dir_all(&packet_dir)?;
    let packet = json!({
        "schema": "implementation_packet.v1",
        "packet_id": packet_id,
        "created_at": Utc::now(),
        "project_key": project_key,
        "project_root": home.display().to_string(),
        "source_intent": {
            "user_goal": "Bind delegated Offdesk execution to the original design intent.",
            "why_now": "The queued task should carry its design packet into launch records.",
            "success_state": "Task JSON and background records expose packet readiness without raw path-first output."
        },
        "alignment": {
            "north_star_fit": "Keeps delegated work tied to the project direction.",
            "brand_fit": "Supports design-first harness execution.",
            "product_boundary": "Metadata only; no runtime authority is granted.",
            "anti_drift_notes": ["Do not treat packet presence as execution approval."]
        },
        "scope": {
            "included": ["task metadata", "background probe metadata"],
            "excluded": ["approval bypass"],
            "allowed_files": ["src/offdesk/task_queue.rs", "src/offdesk/background.rs"],
            "mutation_boundary": "Attach optional summary only.",
            "non_authorized_actions": ["Do not launch without existing gate behavior."]
        },
        "capability_mapping": [
            {
                "capability_id": "FD-016",
                "reason": "Substantial delegated work needs an implementation packet."
            }
        ],
        "design": {
            "approach": "Attach latest project packet to queued task records.",
            "work_slices": ["resolve latest packet", "attach artifact refs"],
            "interfaces": ["offdesk task JSON", "background probe JSON"],
            "data_contracts": ["implementation_packet.v1"],
            "compatibility_notes": ["Missing packet leaves the field absent."]
        },
        "execution": {
            "preferred_worker": "hosted_harness",
            "worker_requirements": ["packet is metadata only"],
            "commands": ["forager offdesk enqueue ... --json"],
            "stop_conditions": ["packet project key mismatch"],
            "rollback_or_recovery": ["omit implementation_packet field"]
        },
        "validation": {
            "tests": ["cargo test --test offdesk_cli offdesk_enqueue_tasks_json_redacts_command"],
            "smoke_checks": ["forager offdesk tasks --json"],
            "manual_review": ["confirm no raw JSON path is primary surface"],
            "evidence_required": ["task artifact refs include implementation packet"]
        },
        "closeout": {
            "expected_artifacts": ["IMPLEMENTATION_PACKET.json", "RECURSIVE_ALIGNMENT_REVIEW.json", "IMPLEMENTATION_PACKET.md"],
            "accepted_truth_rule": "Execution completion is not acceptance.",
            "handoff_summary_requirements": ["state packet outcome and safe_to_delegate"]
        },
        "recursive_alignment_review": {
            "schema": "recursive_alignment_review.v1",
            "reviewer": "deterministic_gate",
            "outcome": "pass",
            "checks": {
                "original_goal_coverage": "complete",
                "north_star_alignment": "complete",
                "brand_alignment": "complete",
                "scope_balance": "complete",
                "capability_coverage": "complete",
                "evidence_sufficiency": "complete",
                "completion_definition": "complete"
            },
            "drift_signals": [],
            "missing_decisions": [],
            "required_revisions": [],
            "safe_to_delegate": true
        }
    });
    fs::write(
        packet_dir.join("IMPLEMENTATION_PACKET.json"),
        serde_json::to_string_pretty(&packet)?,
    )?;
    fs::write(
        packet_dir.join("RECURSIVE_ALIGNMENT_REVIEW.json"),
        serde_json::to_string_pretty(&packet["recursive_alignment_review"])?,
    )?;
    fs::write(
        packet_dir.join("IMPLEMENTATION_PACKET.md"),
        "# Implementation Packet\n\nBind delegated Offdesk execution to design intent.\n",
    )?;
    Ok(packet_dir)
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

#[test]
fn queue_cli_mutations_refuse_a_tick_lock_held_by_another_process() -> Result<()> {
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let queue = profile.join("offdesk_tasks.json");
    fs::write(
        &queue,
        serde_json::to_vec(&json!([durable_task(
            "queued",
            Utc::now(),
            "true",
            temp.path()
        )]))?,
    )?;
    let before = fs::read(&queue)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .open(profile.join("offdesk_tick.lock"))?;
    lock.lock_exclusive()?;
    for command in ["cancel-task", "retry-task", "resume-task", "abandon-task"] {
        let output = forager_command(temp.path())
            .args(["offdesk", command, "task", "--json"])
            .output()?;
        assert!(!output.status.success(), "{command} bypassed tick lock");
        assert_eq!(fs::read(&queue)?, before);
    }
    FileExt::unlock(&lock)?;
    let output = forager_command(temp.path())
        .args(["offdesk", "cancel-task", "task", "--json"])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}

fn record_fixture_exit_evidence(profile: &Path) -> Result<()> {
    use forager::offdesk::{
        BackgroundProbe, BackgroundRunStore, BackgroundRunnerKind, OffdeskTask,
    };
    let tasks: Vec<OffdeskTask> =
        serde_json::from_slice(&fs::read(profile.join("offdesk_tasks.json"))?)?;
    let mut probes = Vec::new();
    for task in tasks {
        let Some(ticket) = task.background_ticket_id else {
            continue;
        };
        let (mut command, receipt) =
            forager::offdesk::local_exit::command(Path::new(&task.workdir), "exit 0")?;
        let mut child = command.spawn()?;
        let pid = child.id();
        child.wait()?;
        let mut probe = BackgroundProbe::new(ticket, BackgroundRunnerKind::LocalBackground);
        probe.task_id = Some(task.task_id);
        probe.project_key = Some(task.project_key);
        probe.request_id = Some(task.request_id);
        probe.working_dir = Some(task.workdir);
        probe.runtime_pid = Some(pid);
        probe.runtime_identity = Some("fixture-exited-process".into());
        probe.local_exit_receipt = Some(receipt);
        probes.push(probe);
    }
    BackgroundRunStore::new(profile).save(&probes)?;
    Ok(())
}

#[test]
fn offdesk_task_recovery_missing_profile_does_not_initialize_state() -> Result<()> {
    let temp = tempdir()?;
    let output = forager_command(temp.path())
        .args(["offdesk", "task-recovery", "missing", "--json"])
        .output()?;
    assert!(!output.status.success());
    assert!(!app_dir(temp.path()).exists());
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn offdesk_task_recovery_reports_evidence_without_mutating_state() -> Result<()> {
    use forager::offdesk::BackgroundRunStore;
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let queue = profile.join("offdesk_tasks.json");
    fs::write(
        &queue,
        serde_json::to_vec(&json!([durable_task(
            "failed",
            Utc::now(),
            "exit 0",
            temp.path()
        )]))?,
    )?;
    record_fixture_exit_evidence(&profile)?;
    let store = BackgroundRunStore::new(&profile);
    let original = store.load()?;
    for expected in [
        "descendant_wait_receipt_verified",
        "supervisor_lost_descendants_unverified",
        "supervisor_alive",
        "supervisor_pid_reused",
        "containment_binding_missing_or_invalid",
        "runtime_binding_unsupported_or_mismatched",
        "runtime_probe_missing_or_ambiguous",
    ] {
        let mut probes = original.clone();
        match expected {
            "containment_binding_missing_or_invalid" => {
                let receipt = probes[0].local_exit_receipt.as_mut().unwrap();
                receipt.wait_scope = Some("linux_subreaper_children_cgroup_v1".into());
                // Even a complete matching shell receipt cannot bypass missing
                // containment evidence after a prepared launch loses its PID.
                let receipt_path = temp.path().join(&receipt.relative_path);
                let mut value: serde_json::Value =
                    serde_json::from_slice(&fs::read(&receipt_path)?)?;
                value["wait_scope"] = json!("linux_subreaper_children_cgroup_v1");
                fs::write(receipt_path, serde_json::to_vec(&value)?)?;
                probes[0].runtime_pid = None;
            }
            "supervisor_lost_descendants_unverified" => probes[0].local_exit_receipt = None,
            "supervisor_alive" | "supervisor_pid_reused" => {
                probes[0].runtime_pid = Some(std::process::id());
                probes[0].runtime_identity = Some(if expected == "supervisor_alive" {
                    forager::process::process_identity(std::process::id()).unwrap()
                } else {
                    "not-the-current-process".into()
                });
            }
            "runtime_binding_unsupported_or_mismatched" => probes[0].task_id = Some("other".into()),
            "runtime_probe_missing_or_ambiguous" => probes.push(probes[0].clone()),
            _ => {}
        }
        store.save(&probes)?;
        let before: std::collections::BTreeMap<_, _> = fs::read_dir(&profile)?
            .map(|entry| {
                let path = entry?.path();
                Ok((path.clone(), fs::read(path)?))
            })
            .collect::<std::io::Result<_>>()?;
        for _ in 0..2 {
            let output = forager_command(temp.path())
                .args(["offdesk", "task-recovery", "task", "--json"])
                .output()?;
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            let value: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            assert_eq!(value["reason"], expected);
            assert_eq!(
                value["release_verified"],
                expected == "descendant_wait_receipt_verified"
            );
            assert_eq!(value["automatic_recovery"], false);
            assert_eq!(
                value["does_not_authorize"],
                json!(["signal", "terminate", "clear_ticket", "retry", "launch"])
            );
        }
        let after: std::collections::BTreeMap<_, _> = fs::read_dir(&profile)?
            .map(|entry| {
                let path = entry?.path();
                Ok((path.clone(), fs::read(path)?))
            })
            .collect::<std::io::Result<_>>()?;
        assert_eq!(before, after);
    }
    Ok(())
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
    assert_eq!(pending[0]["next_safe_action"]["kind"], "approval_pending");
    assert!(pending[0]["next_safe_action"]["commands"]
        .as_array()
        .expect("next action commands")
        .iter()
        .any(|command| command
            .as_str()
            .expect("next action command")
            .contains("forager offdesk ok approval_one")));

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
fn offdesk_pending_presentation_redacts_legacy_json_and_human_state() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let approvals_path = profile_dir.join("pending_action_approvals.json");
    fs::write(
        &approvals_path,
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": format!("approval-token={secret}"),
                "status": "pending",
                "scope": "once",
                "project_key": format!("project-token={secret}"),
                "request_id": format!("request-token={secret}"),
                "task_id": format!("task-token={secret}"),
                "action": format!("dispatch.runtime token={secret}"),
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": format!("legacy preview token={secret}"),
                "reason": format!("legacy reason token={secret}"),
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": format!("legacy token={secret}")
            }
        ]))?,
    )?;
    let before = fs::read(&approvals_path)?;

    let json_output = forager_command(temp.path())
        .args(["offdesk", "pending", "--json"])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let approvals: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert!(approvals[0]["preview"]
        .as_str()
        .expect("approval preview")
        .contains("[REDACTED]"));
    assert!(approvals[0]["next_safe_action"]["commands"]
        .as_array()
        .expect("next safe action commands")
        .iter()
        .all(|command| !command.as_str().unwrap_or_default().contains(secret)));
    assert_eq!(fs::read(&approvals_path)?, before);

    let human_output = forager_command(temp.path())
        .args(["offdesk", "pending"])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("APPROVAL ID"));
    assert!(human_stdout.contains("preview: legacy preview token=[REDACTED]"));
    assert!(human_stdout.contains("command: forager offdesk ok approval-token=[REDACTED]"));
    assert!(!human_stdout.contains(secret));
    assert_eq!(fs::read(&approvals_path)?, before);

    fs::write(&approvals_path, "[]\n")?;
    let empty_output = forager_command(temp.path())
        .args(["offdesk", "pending"])
        .output()?;
    assert!(empty_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_output.stdout).trim(),
        "No offdesk approvals found."
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_approval_resolution_presentation_redacts_legacy_state_and_preserves_authority(
) -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let approved_id = format!("approval-ok-token={secret}");
    let denied_id = format!("approval-deny-token={secret}");
    let approvals_path = profile_dir.join("pending_action_approvals.json");
    fs::write(
        &approvals_path,
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": approved_id,
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request-ok",
                "task_id": "task-ok",
                "action": format!("dispatch.runtime token={secret}"),
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": format!("legacy preview token={secret}"),
                "reason": "outside envelope",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            },
            {
                "approval_id": denied_id,
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request-deny",
                "task_id": "task-deny",
                "action": format!("canonical.sync token={secret}"),
                "risk_level": "canonical_mutation",
                "approval_mode": "operator_required",
                "preview": "safe preview",
                "reason": format!("legacy reason token={secret}"),
                "created_at": now + Duration::seconds(1),
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;

    let approve_output = forager_command(temp.path())
        .args(["offdesk", "ok", &approved_id, "--json"])
        .output()?;
    assert!(approve_output.status.success());
    let approve_stdout = String::from_utf8_lossy(&approve_output.stdout);
    assert!(!approve_stdout.contains(secret));
    let approved: serde_json::Value = serde_json::from_slice(&approve_output.stdout)?;
    assert_eq!(approved["status"], "approved");
    assert_eq!(approved["approval_id"], "approval-ok-token=[REDACTED]");
    assert_eq!(approved["action"], "dispatch.runtime token=[REDACTED]");
    assert_eq!(approved["preview"], "legacy preview token=[REDACTED]");

    let deny_output = forager_command(temp.path())
        .args(["offdesk", "deny", &denied_id])
        .output()?;
    assert!(deny_output.status.success());
    let deny_stdout = String::from_utf8_lossy(&deny_output.stdout);
    assert!(!deny_stdout.contains(secret));
    assert!(deny_stdout.contains(
        "Denied offdesk approval approval-deny-token=[REDACTED]: canonical.sync token=[REDACTED] (CanonicalMutation)"
    ));

    let approvals: serde_json::Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals[0]["status"], "approved");
    assert_eq!(approvals[0]["resolved_by"], "cli");
    assert_eq!(approvals[1]["status"], "denied");
    assert_eq!(approvals[1]["resolved_by"], "cli");

    let audit = fs::read_to_string(profile_dir.join("action_audit.jsonl"))?;
    assert_eq!(audit.matches("\"transition\":\"approve\"").count(), 1);
    assert_eq!(audit.matches("\"transition\":\"deny\"").count(), 1);
    let candidates_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "candidates",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(candidates_output.status.success());
    let candidates: serde_json::Value = serde_json::from_slice(&candidates_output.stdout)?;
    assert_eq!(candidates.as_array().expect("candidates").len(), 1);
    assert_eq!(candidates[0]["signal_kind"], "approval_denial");

    let empty_output = forager_command(temp.path())
        .args(["offdesk", "ok"])
        .output()?;
    assert!(empty_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_output.stdout).trim(),
        "No pending offdesk approvals."
    );

    let missing_id = format!("missing-token={secret}");
    let missing_output = forager_command(temp.path())
        .args(["offdesk", "ok", &missing_id])
        .output()?;
    assert!(!missing_output.status.success());
    let missing_stderr = String::from_utf8_lossy(&missing_output.stderr);
    assert!(!missing_stderr.contains(secret));
    assert!(missing_stderr.contains("Pending offdesk approval not found: missing-token=[REDACTED]"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_decisions_lists_and_shows_decision_records() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let pending = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-user",
        "project_key": "project",
        "request_id": "request",
        "task_id": "task",
        "raised_by": "agent",
        "source_surface": "offdesk.council",
        "materiality": "high",
        "status": "user_pending",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": "council_escalation",
            "summary": "Council recommends revising the next episode.",
            "decision_needed": "Choose whether to continue, revise, block, or stop.",
            "why_now": ["Council did not return continue."],
            "current_scope": "Next episode only.",
            "non_authorized_scope": [
                "provider retargeting",
                "cleanup",
                "wiki promotion"
            ]
        },
        "council_review": {
            "recommendation": "revise",
            "agreement": true,
            "reviewer_decisions": {
                "claude": "revise",
                "gpt": "revise"
            }
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
    let internal = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-auto",
        "project_key": "project",
        "request_id": "request",
        "task_id": "other-task",
        "raised_by": "council",
        "source_surface": "offdesk.council",
        "materiality": "low",
        "status": "auto_resolved",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": "verification_order",
            "summary": "Council selected the approved verification command.",
            "decision_needed": "Select verification order.",
            "current_scope": "Approved task contract.",
            "non_authorized_scope": []
        },
        "route": {
            "materiality": "low",
            "target": "agent",
            "reason": "Covered by existing task policy."
        }
    });
    fs::write(
        profile_dir.join("offdesk_decisions.jsonl"),
        format!(
            "{}\n{}\n",
            serde_json::to_string(&pending)?,
            serde_json::to_string(&internal)?
        ),
    )?;

    let list_output = forager_command(temp.path())
        .args(["offdesk", "decisions", "--status", "user_pending", "--json"])
        .output()?;
    assert!(
        list_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&list_output.stderr)
    );
    let list: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    assert_eq!(list.as_array().expect("array").len(), 1);
    assert_eq!(list[0]["record"]["decision_id"], "decision-user");
    assert_eq!(
        list[0]["record"]["approval_brief"]["schema"],
        "approval_brief.v1"
    );
    assert_eq!(list[0]["validation_issues"], json!([]));

    let show_output = forager_command(temp.path())
        .args(["offdesk", "decision", "show", "decision-user", "--json"])
        .output()?;
    assert!(
        show_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&show_output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(shown["record"]["status"], "user_pending");
    assert_eq!(shown["record"]["route"]["target"], "user");
    Ok(())
}

#[test]
#[serial]
fn offdesk_decision_resolve_and_receipt_append_handoff_records() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let pending = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-handoff",
        "project_key": "project",
        "request_id": "request",
        "task_id": "task",
        "raised_by": "council",
        "source_surface": "offdesk.council",
        "materiality": "high",
        "status": "user_pending",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": "episode_council_continuation",
            "summary": "Council recommends revising before continuing.",
            "decision_needed": "Choose whether to continue, revise, block, or stop.",
            "why_now": ["Council did not return continue."],
            "current_scope": "Next episode only.",
            "non_authorized_scope": [
                "provider retargeting",
                "cleanup",
                "wiki promotion"
            ]
        },
        "council_review": {
            "recommendation": "revise",
            "agreement": true,
            "reviewer_decisions": {
                "claude": "revise",
                "gpt": "revise"
            }
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
        profile_dir.join("offdesk_decisions.jsonl"),
        format!("{}\n", serde_json::to_string(&pending)?),
    )?;

    let resolve_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "resolve",
            "decision-handoff",
            "--decision",
            "revise",
            "--note",
            "Diagnose primary gate failure before continuing.",
            "--json",
        ])
        .output()?;
    assert!(
        resolve_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&resolve_output.stderr)
    );
    let resolved: serde_json::Value = serde_json::from_slice(&resolve_output.stdout)?;
    assert_eq!(resolved["record"]["status"], "handoff_ready");
    assert_eq!(
        resolved["record"]["execution_handoff"]["approved_direction"],
        "revise"
    );
    assert_eq!(resolved["record"]["execution_handoff"]["target"], "agent");
    assert!(resolved["record"]["execution_handoff"]["instructions"]
        .as_array()
        .expect("handoff instructions")
        .iter()
        .any(|line| line
            .as_str()
            .unwrap_or_default()
            .contains("Diagnose primary gate failure")));
    assert_eq!(resolved["validation_issues"], json!([]));

    let receipt_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "receipt",
            "decision-handoff",
            "--result-status",
            "applied",
            "--evidence",
            "Handoff was reviewed by the next harness.",
            "--remaining-review",
            "Closeout still needs final acceptance.",
            "--json",
        ])
        .output()?;
    assert!(
        receipt_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&receipt_output.stderr)
    );
    let receipted: serde_json::Value = serde_json::from_slice(&receipt_output.stdout)?;
    assert_eq!(receipted["record"]["status"], "receipted");
    assert_eq!(
        receipted["record"]["decision_receipt"]["final_decision"],
        "revise"
    );
    assert_eq!(
        receipted["record"]["decision_receipt"]["result_status"],
        "applied"
    );
    assert_eq!(receipted["validation_issues"], json!([]));

    let ledger = fs::read_to_string(profile_dir.join("offdesk_decisions.jsonl"))?;
    assert_eq!(ledger.lines().count(), 3);
    assert!(ledger.contains("\"status\":\"handoff_ready\""));
    assert!(ledger.contains("\"status\":\"receipted\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_task_and_decision_presentation_redacts_legacy_state_and_preserves_authority(
) -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";

    let task_id = format!("task-token={secret}");
    let capability_id = format!("dispatch-token={secret}");
    let mut task = durable_task_with(
        &task_id,
        &capability_id,
        "failed",
        now,
        &format!("printf token={secret}"),
        temp.path(),
    );
    task["project_key"] = json!(format!("project-token={secret}"));
    task["request_id"] = json!(format!("request-token={secret}"));
    task["background_ticket_id"] = json!(format!("ticket-token={secret}"));
    task["workdir"] = json!(temp
        .path()
        .join(format!("work-token={secret}"))
        .to_str()
        .expect("utf-8 workdir"));
    let tasks_path = profile_dir.join("offdesk_tasks.json");
    fs::write(&tasks_path, serde_json::to_string_pretty(&json!([task]))?)?;
    let tasks_before = fs::read(&tasks_path)?;

    let task_json_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--task-id", &task_id, "--json"])
        .output()?;
    assert!(task_json_output.status.success());
    let task_json_stdout = String::from_utf8_lossy(&task_json_output.stdout);
    assert!(!task_json_stdout.contains(secret));
    let tasks: serde_json::Value = serde_json::from_slice(&task_json_output.stdout)?;
    assert!(tasks[0]["task_id"]
        .as_str()
        .expect("task id")
        .contains("[REDACTED]"));
    assert!(tasks[0]["workdir"]
        .as_str()
        .expect("workdir")
        .contains("[REDACTED]"));

    let task_human_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--task-id", &task_id])
        .output()?;
    assert!(task_human_output.status.success());
    let task_human_stdout = String::from_utf8_lossy(&task_human_output.stdout);
    assert!(task_human_stdout.contains("Open tasks:"));
    assert!(task_human_stdout.contains("[REDACTED]"));
    assert!(!task_human_stdout.contains(secret));
    assert_eq!(fs::read(&tasks_path)?, tasks_before);

    let decision_id = format!("decision-token={secret}");
    let decision = json!({
        "schema": "decision_record.v1",
        "decision_id": decision_id,
        "project_key": format!("project-token={secret}"),
        "request_id": format!("request-token={secret}"),
        "task_id": format!("task-token={secret}"),
        "raised_by": "council",
        "source_surface": format!("offdesk.council token={secret}"),
        "materiality": "high",
        "status": "user_pending",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": format!("episode_continuation token={secret}"),
            "summary": format!("Revise before continuing token={secret}"),
            "decision_needed": format!("Choose the next direction token={secret}"),
            "why_now": [format!("Council returned revise token={secret}")],
            "current_scope": format!("Next episode only token={secret}"),
            "non_authorized_scope": [format!("cleanup token={secret}")]
        },
        "council_review": {
            "recommendation": format!("revise token={secret}"),
            "agreement": true,
            "reviewer_decisions": {
                format!("reviewer-token={secret}"): format!("revise token={secret}")
            }
        },
        "route": {
            "materiality": "high",
            "target": "user",
            "reason": format!("Direction changes token={secret}"),
            "default_if_no_reply": format!("defer token={secret}")
        },
        "approval_brief": {
            "schema": "approval_brief.v1",
            "recommendation": "revise",
            "subject": format!("continuation token={secret}"),
            "summary_lines": [format!("Revise before continuing token={secret}")],
            "scope": format!("Next episode only token={secret}"),
            "question": format!("How should the run proceed token={secret}")
        }
    });
    let decisions_path = profile_dir.join("offdesk_decisions.jsonl");
    fs::write(
        &decisions_path,
        format!("{}\n", serde_json::to_string(&decision)?),
    )?;
    let decisions_before = fs::read(&decisions_path)?;

    let list_json_output = forager_command(temp.path())
        .args(["offdesk", "decisions", "--status", "user_pending", "--json"])
        .output()?;
    assert!(list_json_output.status.success());
    let list_json_stdout = String::from_utf8_lossy(&list_json_output.stdout);
    assert!(!list_json_stdout.contains(secret));
    let listed: serde_json::Value = serde_json::from_slice(&list_json_output.stdout)?;
    assert!(listed[0]["record"]["decision_id"]
        .as_str()
        .expect("decision id")
        .contains("[REDACTED]"));

    let list_human_output = forager_command(temp.path())
        .args(["offdesk", "decisions", "--status", "user_pending"])
        .output()?;
    assert!(list_human_output.status.success());
    let list_human_stdout = String::from_utf8_lossy(&list_human_output.stdout);
    assert!(list_human_stdout.contains("DECISION"));
    assert!(list_human_stdout.contains("[REDACTED]"));
    assert!(!list_human_stdout.contains(secret));

    for json in [false, true] {
        let mut command = forager_command(temp.path());
        command.args(["offdesk", "decision", "show", &decision_id]);
        if json {
            command.arg("--json");
        }
        let show_output = command.output()?;
        assert!(show_output.status.success());
        let show_stdout = String::from_utf8_lossy(&show_output.stdout);
        assert!(show_stdout.contains("[REDACTED]"));
        assert!(!show_stdout.contains(secret));
    }
    assert_eq!(fs::read(&decisions_path)?, decisions_before);

    let missing_id = format!("missing-token={secret}");
    let missing_output = forager_command(temp.path())
        .args(["offdesk", "decision", "show", &missing_id])
        .output()?;
    assert!(!missing_output.status.success());
    let missing_stderr = String::from_utf8_lossy(&missing_output.stderr);
    assert!(missing_stderr.contains("missing-token=[REDACTED]"));
    assert!(!missing_stderr.contains(secret));

    let resolve_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "resolve",
            &decision_id,
            "--decision",
            "revise",
            "--note",
            &format!("Inspect the gate token={secret}"),
            "--by",
            &format!("operator-token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(resolve_output.status.success());
    let resolve_stdout = String::from_utf8_lossy(&resolve_output.stdout);
    assert!(!resolve_stdout.contains(secret));
    let resolved: serde_json::Value = serde_json::from_slice(&resolve_output.stdout)?;
    assert_eq!(resolved["record"]["status"], "handoff_ready");

    let receipt_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "receipt",
            &decision_id,
            "--by",
            &format!("operator-token={secret}"),
            "--result-status",
            "applied",
            "--evidence",
            &format!("Handoff checked token={secret}"),
            "--remaining-review",
            &format!("Closeout remains token={secret}"),
        ])
        .output()?;
    assert!(receipt_output.status.success());
    let receipt_stdout = String::from_utf8_lossy(&receipt_output.stdout);
    assert!(receipt_stdout.contains("Decision receipt:"));
    assert!(receipt_stdout.contains("status:   receipted"));
    assert!(!receipt_stdout.contains(secret));

    let ledger = fs::read_to_string(&decisions_path)?;
    let records = ledger
        .lines()
        .map(serde_json::from_str::<serde_json::Value>)
        .collect::<std::result::Result<Vec<_>, _>>()?;
    assert_eq!(records.len(), 3);
    assert_eq!(records[0]["status"], "user_pending");
    assert_eq!(records[1]["status"], "handoff_ready");
    assert_eq!(records[2]["status"], "receipted");
    assert!(records
        .iter()
        .all(|record| record["decision_id"] == decision_id));
    Ok(())
}

#[test]
#[serial]
fn offdesk_decision_ingest_telegram_appends_profile_handoff_and_receipt() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let artifact_dir = temp.path().join("relay");
    fs::create_dir_all(&artifact_dir)?;
    let now = Utc::now();
    let decision_record = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-telegram",
        "project_key": "project",
        "request_id": "request",
        "task_id": "task",
        "raised_by": "council",
        "source_surface": "offdesk.council",
        "materiality": "high",
        "status": "user_pending",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": "episode_council_continuation",
            "summary": "Council needs a revised direction.",
            "decision_needed": "Choose the next direction.",
            "why_now": ["Council returned revise."],
            "current_scope": "Next episode only.",
            "non_authorized_scope": ["cleanup", "provider retargeting"]
        },
        "route": {
            "materiality": "high",
            "target": "user",
            "reason": "The next direction changes.",
            "default_if_no_reply": "defer"
        },
        "approval_brief": {
            "schema": "approval_brief.v1",
            "recommendation": "revise",
            "subject": "council continuation decision",
            "summary_lines": ["Council needs a revised direction."],
            "scope": "Only approves the next episode direction.",
            "question": "How should the run proceed?"
        }
    });
    let request_path = artifact_dir.join("request.json");
    fs::write(
        &request_path,
        serde_json::to_string_pretty(&json!({
            "decision_request_id": "request:episode-001:council",
            "decision_record": decision_record,
            "approval_brief": {
                "schema": "approval_brief.v1",
                "recommendation": "revise",
                "subject": "council continuation decision",
                "summary_lines": ["Council needs a revised direction."],
                "scope": "Only approves the next episode direction.",
                "question": "How should the run proceed?"
            }
        }))?,
    )?;
    let result_path = artifact_dir.join("telegram_decision.json");
    fs::write(
        &result_path,
        serde_json::to_string_pretty(&json!({
            "status": "accepted",
            "decision": "revise",
            "reason": "Focus on the failed primary gate before continuing.",
            "input_mode": "callback_then_message"
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "ingest-telegram",
            "--request",
            request_path.to_str().expect("utf8 request path"),
            "--result",
            result_path.to_str().expect("utf8 result path"),
            "--receipt-result-status",
            "applied",
            "--receipt-evidence",
            "Telegram reply was consumed by the workload control loop.",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["decision_id"], "decision-telegram");
    assert_eq!(report["telegram_status"], "accepted");
    assert_eq!(report["telegram_decision"], "revise");
    assert_eq!(
        report["appended_records"],
        json!(["user_pending", "handoff_ready", "receipted"])
    );
    assert_eq!(report["record"]["status"], "receipted");
    assert_eq!(
        report["record"]["execution_handoff"]["approved_direction"],
        "revise"
    );
    assert_eq!(
        report["record"]["decision_receipt"]["result_status"],
        "applied"
    );
    assert_eq!(report["validation_issues"], json!([]));

    let ledger = fs::read_to_string(profile_dir.join("offdesk_decisions.jsonl"))?;
    assert_eq!(ledger.lines().count(), 3);
    assert!(ledger.contains("Focus on the failed primary gate"));
    assert!(ledger.contains("\"status\":\"receipted\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_decision_ingest_telegram_renders_fail_closed_status() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let artifact_dir = temp.path().join("relay");
    fs::create_dir_all(&artifact_dir)?;
    let now = Utc::now();
    let request_path = artifact_dir.join("request.json");
    fs::write(
        &request_path,
        serde_json::to_string_pretty(&json!({
            "decision_record": {
                "schema": "decision_record.v1",
                "decision_id": "decision-telegram-expired",
                "project_key": "project",
                "request_id": "request",
                "task_id": "task",
                "raised_by": "operator",
                "source_surface": "offdesk.remote_operator",
                "materiality": "medium",
                "status": "user_pending",
                "created_at": now,
                "updated_at": now,
                "decision_request": {
                    "kind": "operator_choice",
                    "summary": "Operator choice expired.",
                    "decision_needed": "Choose the next direction.",
                    "current_scope": "Review only.",
                    "non_authorized_scope": ["runtime mutation"]
                }
            }
        }))?,
    )?;
    let result_path = artifact_dir.join("telegram_decision.json");
    fs::write(
        &result_path,
        serde_json::to_string_pretty(&json!({
            "status": "expired",
            "decision": "revise"
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "ingest-telegram",
            "--request",
            request_path.to_str().expect("utf8 request path"),
            "--result",
            result_path.to_str().expect("utf8 result path"),
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout)?;
    assert!(stdout.contains("Decision: decision-telegram-expired"));
    assert!(stdout.contains("Telegram status: expired"));
    assert!(stdout.contains("Appended: user_pending"));
    assert!(stdout.contains("Skipped: telegram_result_status_expired"));

    let ledger = fs::read_to_string(profile_dir.join("offdesk_decisions.jsonl"))?;
    assert_eq!(ledger.lines().count(), 1);
    assert!(!ledger.contains("\"status\":\"handoff_ready\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_decision_ingest_telegram_feedback_creates_reviewable_inbox_item() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let artifact_dir = temp.path().join("relay");
    fs::create_dir_all(&artifact_dir)?;
    let old_feedback = json!({
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": Utc::now(),
        "profile": "default",
        "chat_id_hash": "sha256:old-chat",
        "user_id_hash": "sha256:old-user",
        "message_id": 1,
        "feedback_text": "old feedback",
        "target_chat_id_hash": "sha256:old-chat",
        "feedback_context": serde_json::Value::Null
    });
    let feedback = json!({
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": Utc::now(),
        "profile": "default",
        "chat_id_hash": "sha256:chat",
        "user_id_hash": "sha256:user",
        "message_id": 777,
        "feedback_text": "실패 조건 보강 필요",
        "target_chat_id_hash": "sha256:chat",
        "feedback_context": {
            "schema": "telegram_interaction_context.v1",
            "command": "plans",
            "profile": "default",
            "context_kind": "plan_attention",
            "focus_kind": "plan",
            "focus_ref": "plan_harness_mobile",
            "focus_label": "수정 필요",
            "next_command": "/show plan_harness_mobile",
            "project_key": "project",
            "request_id": "request",
            "task_id": "task"
        }
    });
    let feedback_path = artifact_dir.join("feedback.jsonl");
    fs::write(
        &feedback_path,
        format!(
            "{}\n{}\n",
            serde_json::to_string(&old_feedback)?,
            serde_json::to_string(&feedback)?
        ),
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            feedback_path.to_str().expect("utf8 feedback path"),
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let decision_id = report["decision_id"].as_str().expect("decision id");
    assert!(decision_id.starts_with("telegram-feedback-"));
    assert_eq!(report["appended"], true);
    assert_eq!(report["record"]["status"], "user_pending");
    assert_eq!(report["record"]["materiality"], "medium");
    assert_eq!(
        report["record"]["source_surface"],
        "telegram.remote_operator.feedback"
    );
    assert_eq!(
        report["record"]["decision_request"]["kind"],
        "telegram_operator_feedback"
    );
    assert_eq!(report["record"]["route"]["target"], "user");
    assert_eq!(
        report["record"]["approval_brief"]["source"],
        "telegram.remote_operator.feedback"
    );
    assert!(report["record"]["decision_request"]["non_authorized_scope"]
        .as_array()
        .expect("non-authorized scope")
        .iter()
        .any(|scope| scope.as_str() == Some("approval resolution")));
    assert_eq!(report["validation_issues"], json!([]));
    let ledger_path = profile_dir.join("offdesk_decisions.jsonl");
    let ledger = fs::read_to_string(&ledger_path)?;
    assert_eq!(ledger.lines().count(), 1);

    let mut replay_feedback = feedback.clone();
    replay_feedback["received_at"] = json!(Utc::now() + Duration::seconds(5));
    fs::write(
        &feedback_path,
        format!("{}\n", serde_json::to_string(&replay_feedback)?),
    )?;

    let duplicate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            feedback_path.to_str().expect("utf8 feedback path"),
            "--json",
        ])
        .output()?;
    assert!(
        duplicate_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&duplicate_output.stderr)
    );
    let duplicate_report: serde_json::Value = serde_json::from_slice(&duplicate_output.stdout)?;
    assert_eq!(duplicate_report["decision_id"], decision_id);
    assert_eq!(duplicate_report["appended"], false);
    let ledger = fs::read_to_string(&ledger_path)?;
    assert_eq!(ledger.lines().count(), 1);

    let resolve_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "resolve",
            decision_id,
            "--decision",
            "revise",
            "--note",
            "Tighten the mobile feedback summary before the next offdesk run.",
            "--json",
        ])
        .output()?;
    assert!(
        resolve_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&resolve_output.stderr)
    );
    let resolved: serde_json::Value = serde_json::from_slice(&resolve_output.stdout)?;
    assert_eq!(resolved["record"]["status"], "handoff_ready");
    assert_eq!(
        resolved["record"]["execution_handoff"]["approved_direction"],
        "revise"
    );

    let receipt_output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "receipt",
            decision_id,
            "--result-status",
            "reviewed",
            "--evidence",
            "Telegram feedback was reviewed by the planning harness.",
            "--json",
        ])
        .output()?;
    assert!(
        receipt_output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&receipt_output.stderr)
    );
    let receipted: serde_json::Value = serde_json::from_slice(&receipt_output.stdout)?;
    assert_eq!(receipted["record"]["status"], "receipted");
    assert_eq!(
        receipted["record"]["decision_receipt"]["result_status"],
        "reviewed"
    );
    let ledger = fs::read_to_string(&ledger_path)?;
    assert_eq!(ledger.lines().count(), 3);
    Ok(())
}

#[test]
fn offdesk_decision_ingest_telegram_planning_request_is_not_generic_feedback() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let artifact_dir = temp.path().join("relay");
    fs::create_dir_all(&artifact_dir)?;
    let feedback = json!({
        "schema": "remote_operator_telegram_feedback.v1",
        "received_at": Utc::now(),
        "profile": "default",
        "chat_id_hash": "sha256:chat",
        "user_id_hash": "sha256:user",
        "message_id": 706,
        "feedback_text": "nanoclustering Fractal tree 개발쪽을 자율주행으로 처리할 수 있을지 검토해볼까",
        "feedback_kind": "planning_request",
        "target_chat_id_hash": "sha256:chat",
        "feedback_context": {
            "schema": "telegram_interaction_context.v1",
            "command": "status",
            "profile": "default",
            "context_kind": "status_clear",
            "focus_kind": "none",
            "focus_label": "처리할 항목 없음"
        }
    });
    let feedback_path = artifact_dir.join("feedback.json");
    fs::write(&feedback_path, serde_json::to_string_pretty(&feedback)?)?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "decision",
            "ingest-telegram-feedback",
            "--feedback",
            feedback_path.to_str().expect("utf8 feedback path"),
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["appended"], true);
    assert_eq!(report["record"]["status"], "user_pending");
    assert_eq!(report["record"]["materiality"], "medium");
    assert_eq!(
        report["record"]["source_surface"],
        "telegram.remote_operator.plan_request"
    );
    assert_eq!(
        report["record"]["decision_request"]["kind"],
        "telegram_operator_plan_request"
    );
    assert_eq!(report["record"]["approval_brief"]["recommendation"], "plan");
    assert!(report["record"]["approval_brief"]["summary_lines"][1]
        .as_str()
        .expect("summary line")
        .contains("no work has started"));
    assert!(report["record"]["decision_request"]["non_authorized_scope"]
        .as_array()
        .expect("non-authorized scope")
        .iter()
        .any(|scope| scope.as_str() == Some("background dispatch")));
    assert_eq!(report["validation_issues"], json!([]));
    Ok(())
}

#[test]
#[serial]
fn offdesk_decisions_report_validation_issues() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let invalid = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-invalid",
        "project_key": "project",
        "request_id": "request",
        "task_id": "task",
        "raised_by": "agent",
        "source_surface": "offdesk.council",
        "materiality": "high",
        "status": "user_pending",
        "created_at": now,
        "updated_at": now,
        "decision_request": {
            "kind": "council_escalation",
            "summary": "Council needs operator input.",
            "decision_needed": "Choose next direction.",
            "current_scope": "Next episode only.",
            "non_authorized_scope": []
        }
    });
    fs::write(
        profile_dir.join("offdesk_decisions.jsonl"),
        format!("{}\n", serde_json::to_string(&invalid)?),
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "decision", "show", "decision-invalid", "--json"])
        .output()?;
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert!(shown["validation_issues"]
        .as_array()
        .expect("validation issues")
        .iter()
        .any(|issue| issue["code"] == "user_pending_without_approval_brief"));
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
    assert_eq!(states[0]["resume_id"], serde_json::Value::Null);
    assert_eq!(states[0]["next_safe_resume_step"], "inspect result sidecar");

    let human_output = forager_command(temp.path())
        .args(["offdesk", "resume"])
        .output()?;
    assert!(human_output.status.success());
    let stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(stdout.contains("resume_id: project:task"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_resume_presentation_redacts_legacy_json_and_human_state() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "resume_pending",
                "phase": "background",
                "runner_target": "local_background",
                "last_evidence_artifacts": [format!("/tmp/token={secret}")],
                "evidence": [
                    {
                        "kind": "log_tail",
                        "summary": format!("legacy summary token={secret}"),
                        "path": format!("/tmp/token={secret}"),
                        "present": true,
                        "observed_at": now
                    }
                ],
                "last_log_tail": format!("legacy tail token={secret}"),
                "next_safe_resume_step": format!("inspect token={secret}"),
                "interrupted_at": now,
                "interruption_reason": format!("restart token={secret}"),
                "fresh_until": now + Duration::minutes(10)
            }
        ]))?,
    )?;

    let json_output = forager_command(temp.path())
        .args(["offdesk", "resume", "--json"])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let states: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert!(states[0]["last_log_tail"]
        .as_str()
        .expect("last log tail")
        .contains("[REDACTED]"));

    let human_output = forager_command(temp.path())
        .args(["offdesk", "resume"])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("TASK"));
    assert!(human_stdout.contains("evidence: log_tail"));
    assert!(human_stdout.contains("[REDACTED]"));
    assert!(!human_stdout.contains(secret));

    fs::write(profile_dir.join("task_resume_state.json"), "[]\n")?;
    let empty_output = forager_command(temp.path())
        .args(["offdesk", "resume"])
        .output()?;
    assert!(empty_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_output.stdout).trim(),
        "No task resume artifacts found."
    );
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
fn offdesk_gate_blocks_provider_capacity_before_approval() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let retry_at = now + Duration::minutes(2);
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
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
            "--provider-id",
            "openai",
            "--model",
            "gpt-4.1",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["status"], "blocked");
    assert_eq!(outcome["retry_at"], serde_json::to_value(retry_at)?);
    assert_eq!(outcome["provider_capacity"]["provider_id"], "openai");
    assert_eq!(outcome["provider_capacity"]["model"], "gpt-4.1");
    assert_eq!(
        outcome["provider_capacity"]["matched_scope"],
        "provider_model"
    );
    assert_eq!(
        outcome["provider_fallback"]["current_provider_id"],
        "openai"
    );
    assert_eq!(outcome["provider_fallback"]["current_model"], "gpt-4.1");
    assert!(outcome["provider_fallback"]["candidates"]
        .as_array()
        .expect("fallback candidates")
        .iter()
        .all(
            |candidate| !(candidate["provider_id"] == "openai" && candidate["model"] == "gpt-4.1")
        ));
    assert!(!profile_dir.join("pending_action_approvals.json").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_provider_fallback_json_is_operator_safe() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now + Duration::minutes(1),
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .env_remove("OPENAI_API_KEY")
        .env_remove("ANTHROPIC_API_KEY")
        .args([
            "offdesk",
            "provider-fallback",
            "--provider-id",
            "openai",
            "--model",
            "gpt-4.1",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let recommendation: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(recommendation["current_provider_id"], "openai");
    let candidates = recommendation["candidates"]
        .as_array()
        .expect("fallback candidates");
    assert!(candidates.iter().all(
        |candidate| !(candidate["provider_id"] == "openai" && candidate["model"] == "gpt-4.1")
    ));
    let anthropic = candidates
        .iter()
        .find(|candidate| {
            candidate["provider_id"] == "anthropic"
                && candidate["model"] == "claude-3-5-sonnet-latest"
        })
        .expect("anthropic candidate");
    assert_eq!(anthropic["auth_status"], "missing_auth");
    assert_eq!(anthropic["capacity_status"], "cooling_down");
    assert_eq!(anthropic["recommended"], false);
    assert!(!String::from_utf8_lossy(&output.stdout).contains("ANTHROPIC_API_KEY"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_provider_capacity_json_is_operator_safe() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now + Duration::minutes(1),
                "last_error_summary": format!("rate limit token={secret}"),
                "updated_at": now
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "provider-capacity", "--json"])
        .output()?;

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains(secret));
    let states: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(states[0]["provider_id"], "openai");
    assert_eq!(states[0]["status"], "cooling_down");
    Ok(())
}

#[test]
#[serial]
fn offdesk_provider_presentation_redacts_human_outputs_and_stays_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let capacity_path = profile_dir.join("provider_capacity.json");
    fs::write(
        &capacity_path,
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": format!("provider-token={secret}"),
                "model": format!("model-token={secret}"),
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now + Duration::minutes(1),
                "last_error_summary": format!("rate limit token={secret}"),
                "updated_at": now
            }
        ]))?,
    )?;
    let source_before = fs::read(&capacity_path)?;

    let capacity_output = forager_command(temp.path())
        .args(["offdesk", "provider-capacity"])
        .output()?;
    assert!(capacity_output.status.success());
    let capacity_stdout = String::from_utf8_lossy(&capacity_output.stdout);
    assert!(capacity_stdout.contains("PROVIDER"));
    assert!(capacity_stdout.contains("provider-token=[REDACTED]"));
    assert!(capacity_stdout.contains("model-token=[REDACTED]"));
    assert!(capacity_stdout.contains("summary: rate limit token=[REDACTED]"));
    assert!(!capacity_stdout.contains(secret));
    assert_eq!(fs::read(&capacity_path)?, source_before);

    let fallback_output = forager_command(temp.path())
        .env_remove("OPENAI_API_KEY")
        .env_remove("ANTHROPIC_API_KEY")
        .args([
            "offdesk",
            "provider-fallback",
            "--provider-id",
            "openai",
            "--model",
            "gpt-4.1",
        ])
        .output()?;
    assert!(fallback_output.status.success());
    let fallback_stdout = String::from_utf8_lossy(&fallback_output.stdout);
    assert!(fallback_stdout.contains("Provider fallback for openai model gpt-4.1"));
    assert!(fallback_stdout.contains("RECOMMENDED"));
    assert!(fallback_stdout.contains("gpt-4.1-mini"));
    assert_eq!(fs::read(&capacity_path)?, source_before);

    let provider_id = format!("provider-token={secret}");
    let model = format!("model-token={secret}");
    let fallback_json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "provider-fallback",
            "--provider-id",
            &provider_id,
            "--model",
            &model,
            "--runner-role",
            "no-candidates",
            "--json",
        ])
        .output()?;
    assert!(fallback_json_output.status.success());
    let fallback_json_stdout = String::from_utf8_lossy(&fallback_json_output.stdout);
    assert!(!fallback_json_stdout.contains(secret));
    let fallback: serde_json::Value = serde_json::from_slice(&fallback_json_output.stdout)?;
    assert_eq!(fallback["current_provider_id"], "provider-token=[REDACTED]");
    assert_eq!(fallback["current_model"], "model-token=[REDACTED]");
    assert!(fallback.get("candidates").is_none());
    assert_eq!(fs::read(&capacity_path)?, source_before);

    let fallback_empty_output = forager_command(temp.path())
        .args([
            "offdesk",
            "provider-fallback",
            "--provider-id",
            &provider_id,
            "--model",
            &model,
            "--runner-role",
            "no-candidates",
        ])
        .output()?;
    assert!(fallback_empty_output.status.success());
    let fallback_empty_stdout = String::from_utf8_lossy(&fallback_empty_output.stdout);
    assert!(fallback_empty_stdout
        .contains("Provider fallback for provider-token=[REDACTED] model model-token=[REDACTED]"));
    assert!(fallback_empty_stdout.contains("no fallback candidates found"));
    assert!(!fallback_empty_stdout.contains(secret));
    assert_eq!(fs::read(&capacity_path)?, source_before);

    fs::write(&capacity_path, "[]\n")?;
    let empty_source_before = fs::read(&capacity_path)?;
    let empty_output = forager_command(temp.path())
        .args(["offdesk", "provider-capacity"])
        .output()?;
    assert!(empty_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_output.stdout).trim(),
        "No provider capacity state found."
    );
    assert_eq!(fs::read(&capacity_path)?, empty_source_before);
    Ok(())
}

#[test]
#[serial]
fn offdesk_capabilities_json_exposes_artifact_contracts() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args(["offdesk", "capabilities", "--json"])
        .output()?;

    assert!(output.status.success());
    let capabilities: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let syncback = capabilities
        .as_array()
        .expect("capability array")
        .iter()
        .find(|capability| capability["capability_id"] == "canonical.syncback")
        .expect("canonical syncback capability");
    assert_eq!(syncback["approval_scope"], "once");
    assert_eq!(syncback["retry_eligible"], false);
    assert_eq!(syncback["resume_eligible"], false);
    assert!(syncback["required_artifacts"]
        .as_array()
        .expect("required artifacts")
        .iter()
        .any(|artifact| artifact["artifact_id"] == "mutation_snapshot"));

    let launch = capabilities
        .as_array()
        .expect("capability array")
        .iter()
        .find(|capability| capability["capability_id"] == "background.launch")
        .expect("background launch capability");
    assert!(launch["produced_artifacts"]
        .as_array()
        .expect("produced artifacts")
        .iter()
        .any(|artifact| artifact["artifact_id"] == "background_run"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_capability_and_harness_human_presentation_is_read_only() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    assert!(!profile_dir(temp.path()).exists());

    let capabilities_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", secret)
        .args(["offdesk", "capabilities"])
        .output()?;
    assert!(capabilities_output.status.success());
    let capabilities_stdout = String::from_utf8_lossy(&capabilities_output.stdout);
    assert!(capabilities_stdout.contains("CAPABILITY"));
    assert!(capabilities_stdout.contains("inspect.status"));
    assert!(capabilities_stdout.contains("canonical.syncback"));
    assert!(capabilities_stdout.contains("Inspect Status"));
    assert!(!capabilities_stdout.contains(secret));
    assert!(!profile_dir(temp.path()).exists());

    let harnesses_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", secret)
        .args(["offdesk", "harnesses"])
        .output()?;
    assert!(harnesses_output.status.success());
    let harnesses_stdout = String::from_utf8_lossy(&harnesses_output.stdout);
    assert!(harnesses_stdout.contains("Hosted harness agent profiles"));
    assert!(harnesses_stdout.contains("Current support target: Codex CLI and Claude Code"));
    assert!(harnesses_stdout.contains("- Codex CLI (codex) [supported]"));
    assert!(harnesses_stdout.contains("- Claude Code (claude) [supported]"));
    assert!(harnesses_stdout.contains("- Gemini CLI (gemini) [planned]"));
    assert!(harnesses_stdout.contains("launch:  manual integration"));
    assert!(harnesses_stdout.contains(
        "prompt:  compact_prompt_with_first_read_artifacts (inline <= 4096 bytes, first-read <= 262144 bytes total)"
    ));
    assert!(!harnesses_stdout.contains(secret));
    assert!(!profile_dir(temp.path()).exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_json_includes_adaptive_wiki_projection() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_report",
                    "kind": "failure_pattern",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Keep report evidence separate",
                    "ai_instruction": "Confirm before merging evidence and recommendations. token=sk-secretsecretsecretsecret",
                    "human_summary": "Human-only summary should not be in AI projection",
                    "evidence_refs": ["task:one"],
                    "confidence": "repeated"
                },
                {
                    "id": "wiki_other_artifact",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "spreadsheet",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Spreadsheet rule",
                    "ai_instruction": "Do not include this.",
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_deprecated",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "deprecated",
                    "activation_mode": "confirm",
                    "claim": "Deprecated report rule",
                    "ai_instruction": "Do not include deprecated entries.",
                    "confidence": "explicit"
                }
            ]
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(value["status"], "proceed");
    let wiki = value["adaptive_wiki"].as_array().expect("wiki projection");
    assert_eq!(wiki.len(), 1);
    assert_eq!(wiki[0]["id"], "wiki_report");
    let runtime = value["adaptive_wiki_runtime"]
        .as_array()
        .expect("runtime wiki projection");
    assert_eq!(runtime.len(), 1);
    assert_eq!(runtime[0]["id"], "wiki_report");
    assert_eq!(
        value["adaptive_wiki_runtime_policy"]["review_expired"],
        "warn"
    );
    assert_eq!(wiki[0]["activation_mode"], "confirm");
    assert!(wiki[0]["instruction"]
        .as_str()
        .unwrap()
        .contains("REDACTED"));
    assert!(!wiki[0]["instruction"]
        .as_str()
        .unwrap()
        .contains("sk-secret"));
    assert!(
        !serde_json::to_string(&wiki[0])?.contains("Human-only summary"),
        "AI projection must not include human summary"
    );
    Ok(())
}

#[test]
fn offdesk_deck_writes_marp_markdown_from_closeout_json() -> Result<()> {
    let temp = tempdir()?;
    let source = temp.path().join("closeout_plan.json");
    let deck = temp.path().join("closeout.marp.md");
    fs::write(
        &source,
        serde_json::to_string_pretty(&json!({
            "generated_at": "2026-06-11T00:00:00Z",
            "closeout_id": "closeout_alpha",
            "profile": "default",
            "artifact_dir": temp.path().join("closeout_alpha"),
            "summary": {
                "completed_tasks": 3,
                "active_or_blocked_tasks": 1,
                "missing_artifacts": 0,
                "return_package_required": true
            },
            "open_decisions": [
                {
                    "kind": "archive_review",
                    "detail": "Review generated logs before archive."
                }
            ],
            "verification_commands": [
                "forager offdesk tasks --json"
            ],
            "required_first_reads": [
                {
                    "path": "REPORT.md",
                    "reason": "Review result summary first."
                }
            ],
            "artifacts": {
                "closeout_plan_json": "closeout_plan.json",
                "return_package_markdown": "RETURN_PACKAGE.md"
            }
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "deck", "--from"])
        .arg(&source)
        .args(["--out"])
        .arg(&deck)
        .arg("--json")
        .output()?;

    assert!(
        output.status.success(),
        "deck command failed\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["schema"], "offdesk_marp_deck.v1");
    assert_eq!(report["source_kind"], "closeout");
    assert_eq!(report["render_status"], "not_requested");
    assert_eq!(
        reported_path(&report["marp_markdown_path"]),
        expected_path(&deck)
    );

    let markdown = fs::read_to_string(&deck)?;
    assert!(markdown.contains("marp: true"));
    assert!(markdown.contains("source JSON remains authoritative"));
    assert!(markdown.contains("closeout_alpha"));
    assert!(markdown.contains("forager offdesk tasks --json"));
    assert!(markdown.contains("review surface only"));
    Ok(())
}

#[test]
fn offdesk_deck_without_render_does_not_require_marp_cli() -> Result<()> {
    let temp = tempdir()?;
    let source = temp.path().join("status.json");
    let deck = temp.path().join("status.marp.md");
    fs::write(
        &source,
        serde_json::to_string_pretty(&json!({
            "status": "healthy",
            "agent_runtime_status": "available",
            "listener_status": "polling",
            "model": "local-coder",
            "pending_approvals": [],
            "queued_offdesk_tasks": [],
            "active_offdesk_tasks": []
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "deck", "--from"])
        .arg(&source)
        .args(["--out"])
        .arg(&deck)
        .args(["--marp-bin", "definitely-missing-marp-bin"])
        .arg("--json")
        .output()?;

    assert!(
        output.status.success(),
        "deck command unexpectedly required Marp CLI\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["source_kind"], "status");
    assert_eq!(report["render_status"], "not_requested");
    assert!(deck.exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_json_filters_adaptive_wiki_projection_by_agent_mode() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_shared",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Shared report rule",
                    "ai_instruction": "Use shared report guidance.",
                    "evidence_refs": ["task:shared"],
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_code",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["code_development"],
                    "claim": "Code report rule",
                    "ai_instruction": "Use code-development report guidance.",
                    "evidence_refs": ["task:code"],
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_research",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["research_writing"],
                    "claim": "Research report rule",
                    "ai_instruction": "Do not include for code mode.",
                    "evidence_refs": ["task:research"],
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_critique",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["critique"],
                    "claim": "Critique report rule",
                    "ai_instruction": "Do not include for code mode.",
                    "evidence_refs": ["task:critique"],
                    "confidence": "explicit"
                }
            ]
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--agent-mode",
            "development",
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let wiki = value["adaptive_wiki"].as_array().expect("wiki projection");
    let ids = wiki
        .iter()
        .map(|entry| entry["id"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(ids.len(), 2);
    assert!(ids.contains(&"wiki_shared"));
    assert!(ids.contains(&"wiki_code"));
    assert!(!ids.contains(&"wiki_research"));
    assert!(!ids.contains(&"wiki_critique"));
    let code = wiki
        .iter()
        .find(|entry| entry["id"] == "wiki_code")
        .expect("code wiki");
    assert_eq!(code["agent_modes"], json!(["development"]));
    let runtime_ids = value["adaptive_wiki_runtime"]
        .as_array()
        .expect("runtime wiki projection")
        .iter()
        .map(|entry| entry["id"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(runtime_ids, ids);

    let shared_only_output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(shared_only_output.status.success());
    let shared_only: serde_json::Value = serde_json::from_slice(&shared_only_output.stdout)?;
    let shared_only_ids = shared_only["adaptive_wiki"]
        .as_array()
        .expect("shared-only wiki projection")
        .iter()
        .map(|entry| entry["id"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(shared_only_ids, vec!["wiki_shared"]);

    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--agent-mode",
            "review",
            "--json",
        ])
        .output()?;
    assert!(review_output.status.success());
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;
    let review_ids = review["adaptive_wiki"]
        .as_array()
        .expect("review wiki projection")
        .iter()
        .map(|entry| entry["id"].as_str().unwrap())
        .collect::<Vec<_>>();
    assert_eq!(review_ids, vec!["wiki_shared"]);
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_brief_defaults_to_shared_mode_and_json_is_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_shared",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Shared project rule",
                    "ai_instruction": "Use shared project guidance.",
                    "evidence_refs": ["task:shared"],
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_development",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["development"],
                    "claim": "Development project rule",
                    "ai_instruction": "Use development-only project guidance.",
                    "evidence_refs": ["task:development"],
                    "confidence": "explicit"
                },
                {
                    "id": "wiki_critique",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "agent_modes": ["critique"],
                    "claim": "Critique project rule",
                    "ai_instruction": "Use critique-only project guidance.",
                    "evidence_refs": ["task:critique"],
                    "confidence": "explicit"
                }
            ]
        }))?,
    )?;
    let project_root = temp.path().join("project");
    fs::create_dir_all(&project_root)?;
    let project_state = project_root.join("PROJECT_STATE.md");
    fs::write(&project_state, "project truth\n")?;
    let entries_before = fs::read(&entries_path)?;
    let project_state_before = fs::read(&project_state)?;

    let shared_output = forager_command(temp.path())
        .current_dir(&project_root)
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(
        shared_output.status.success(),
        "shared brief failed: {}",
        String::from_utf8_lossy(&shared_output.stderr)
    );
    let shared: serde_json::Value = serde_json::from_slice(&shared_output.stdout)?;
    assert_eq!(shared["agent_mode"], "shared");
    let shared_ids = shared["entries"]
        .as_array()
        .expect("shared entries")
        .iter()
        .map(|entry| entry["id"].as_str().expect("entry id"))
        .collect::<Vec<_>>();
    assert_eq!(shared_ids, vec!["wiki_shared"]);

    let development_output = forager_command(temp.path())
        .current_dir(&project_root)
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--agent-mode",
            "development",
            "--json",
        ])
        .output()?;
    assert!(development_output.status.success());
    let development: serde_json::Value = serde_json::from_slice(&development_output.stdout)?;
    assert_eq!(development["agent_mode"], "development");
    let development_ids = development["entries"]
        .as_array()
        .expect("development entries")
        .iter()
        .map(|entry| entry["id"].as_str().expect("entry id"))
        .collect::<Vec<_>>();
    assert_eq!(development_ids.len(), 2);
    assert!(development_ids.contains(&"wiki_shared"));
    assert!(development_ids.contains(&"wiki_development"));
    assert!(!development_ids.contains(&"wiki_critique"));

    assert_eq!(fs::read(&entries_path)?, entries_before);
    assert_eq!(fs::read(&project_state)?, project_state_before);
    assert!(!project_root.join(".wiki-brief.md").exists());
    Ok(())
}

#[cfg(unix)]
#[test]
#[serial]
fn offdesk_wiki_brief_rejects_symlink_with_failure_manifest() -> Result<()> {
    use std::os::unix::fs::symlink;

    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": "wiki_shared",
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Shared project rule",
                "ai_instruction": "Use shared project guidance.",
                "evidence_refs": ["task:shared"],
                "confidence": "explicit"
            }]
        }))?,
    )?;
    let project_root = temp.path().join("project");
    fs::create_dir(&project_root)?;
    let outside = temp.path().join("outside.md");
    fs::write(&outside, "outside truth\n")?;
    let target = project_root.join(".wiki-brief.md");
    symlink(&outside, &target)?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--out",
            target.to_str().expect("utf-8 target"),
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    let manifest: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(manifest["schema"], "wiki_context_manifest.v1");
    assert_eq!(
        manifest["outcome"]["projection_status"],
        "unsafe_output_path"
    );
    assert_eq!(fs::read_to_string(&outside)?, "outside truth\n");

    let over_budget = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "13",
            "--json",
        ])
        .output()?;
    assert!(!over_budget.status.success());
    assert!(String::from_utf8_lossy(&over_budget.stderr).contains("between 1 and 12"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_brief_does_not_replace_output_for_corrupt_source() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        b"{not valid json",
    )?;
    let project_root = temp.path().join("project");
    fs::create_dir(&project_root)?;
    let target = project_root.join(".wiki-brief.md");
    fs::write(&target, b"previous trusted brief\n")?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--out",
            target.to_str().expect("utf-8 target"),
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("parse bounded adaptive wiki context entries"));
    assert_eq!(fs::read(&target)?, b"previous trusted brief\n");
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_candidate_record_presentation_redacts_and_preserves_recurrence_authority(
) -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let scope_ref = format!("project-token={secret}");
    let claim = format!("Keep candidate review explicit token={secret}");
    let ai_instruction = format!("Ask before promotion token={secret}");
    let human_summary = format!("Operator review required token={secret}");
    let evidence_ref = format!("task:task-token={secret}");
    let core_tag = format!("harness/token={secret}");
    let proposed_tag = format!("risk/token={secret}");
    let review_reason = format!("Repeated operator correction token={secret}");

    let json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "record-candidate",
            "--kind",
            "fact",
            "--scope",
            "project",
            "--scope-ref",
            &scope_ref,
            "--claim",
            &claim,
            "--ai-instruction",
            &ai_instruction,
            "--human-summary",
            &human_summary,
            "--evidence-ref",
            &evidence_ref,
            "--agent-mode",
            "critique",
            "--core-tag",
            &core_tag,
            "--proposed-tag",
            &proposed_tag,
            "--confidence",
            "explicit",
            "--origin",
            "operator_explicit",
            "--signal-kind",
            "operator_correction",
            "--review-reason",
            &review_reason,
            "--json",
        ])
        .output()?;
    assert!(
        json_output.status.success(),
        "{}",
        String::from_utf8_lossy(&json_output.stderr)
    );
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let candidate: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    let candidate_id = candidate["id"].as_str().expect("candidate id").to_string();
    assert!(candidate_id.starts_with("wiki_candidate_"));
    for field in [
        "scope_ref",
        "claim",
        "suggested_ai_instruction",
        "human_summary",
        "review_reason",
    ] {
        assert!(candidate[field]
            .as_str()
            .expect("redacted candidate field")
            .contains("[REDACTED]"));
    }
    assert!(candidate["evidence_refs"][0]
        .as_str()
        .expect("evidence ref")
        .contains("[REDACTED]"));
    assert!(candidate["core_tags"][0]
        .as_str()
        .expect("core tag")
        .contains("[REDACTED]"));
    assert_eq!(candidate["occurrence_count"], 1);

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "record-candidate",
            "--kind",
            "fact",
            "--scope",
            "project",
            "--scope-ref",
            &scope_ref,
            "--claim",
            &claim,
            "--ai-instruction",
            &ai_instruction,
            "--human-summary",
            &human_summary,
            "--evidence-ref",
            &evidence_ref,
            "--agent-mode",
            "critique",
            "--core-tag",
            &core_tag,
            "--proposed-tag",
            &proposed_tag,
            "--confidence",
            "explicit",
            "--origin",
            "operator_explicit",
            "--signal-kind",
            "operator_correction",
            "--review-reason",
            &review_reason,
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains(&format!("Recorded candidate {candidate_id}")));
    assert!(human_stdout.contains("Project:project-token=[REDACTED]"));
    assert!(human_stdout.contains("claim: Keep candidate review explicit token=[REDACTED]"));
    assert!(human_stdout.contains("occurrences: 2"));
    assert!(human_stdout.contains(&format!(
        "forager -p default offdesk wiki promote {candidate_id}"
    )));
    assert!(!human_stdout.contains(secret));

    let profile_dir = profile_dir(temp.path());
    let stored: serde_json::Value = serde_json::from_slice(&fs::read(
        profile_dir.join("adaptive_wiki_candidates.json"),
    )?)?;
    let stored_candidates = stored["candidates"].as_array().expect("stored candidates");
    assert_eq!(stored_candidates.len(), 1);
    let stored_candidate = &stored_candidates[0];
    assert_eq!(stored_candidate["id"], candidate_id);
    assert_eq!(stored_candidate["scope_ref"], scope_ref);
    assert_eq!(stored_candidate["claim"], claim);
    assert_eq!(stored_candidate["suggested_ai_instruction"], ai_instruction);
    assert_eq!(stored_candidate["human_summary"], human_summary);
    assert_eq!(stored_candidate["evidence_refs"][0], evidence_ref);
    assert_eq!(stored_candidate["source_refs"][0], evidence_ref);
    assert_eq!(stored_candidate["core_tags"][0], core_tag);
    assert_eq!(stored_candidate["proposed_tags"][0], proposed_tag);
    assert_eq!(stored_candidate["review_reason"], review_reason);
    assert_eq!(stored_candidate["occurrence_count"], 2);

    let corrections = fs::read_to_string(profile_dir.join("adaptive_wiki_corrections.jsonl"))?;
    assert_eq!(corrections.lines().count(), 2);
    assert!(!corrections.contains(secret));
    let correction_rows = corrections
        .lines()
        .map(serde_json::from_str::<serde_json::Value>)
        .collect::<std::result::Result<Vec<_>, _>>()?;
    assert!(correction_rows
        .iter()
        .all(|row| row["candidate_id"] == candidate_id));
    assert!(!profile_dir.join("adaptive_wiki_entries.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_catalog_presentation_redacts_legacy_ids_tags_and_stays_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let entry_id = format!("wiki_entry_token={secret}");
    let candidate_id = format!("wiki_candidate_token={secret}");
    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    let candidates_path = profile_dir.join("adaptive_wiki_candidates.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": entry_id,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": format!("project-token={secret}"),
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": format!("Review catalog output token={secret}"),
                "ai_instruction": "Review catalog output.",
                "human_summary": format!("Legacy summary token={secret}"),
                "evidence_refs": [format!("task:task-token={secret}")],
                "support_refs": [format!("docs/runbook.md?token={secret}")],
                "capability_ids": [format!("capability-token={secret}")],
                "required_artifact_kinds": [format!("report-token={secret}")],
                "core_tags": [format!("facet/token={secret}")],
                "proposed_tags": [format!("risk/token={secret}")],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }]
        }))?,
    )?;
    fs::write(
        &candidates_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": candidate_id,
                "kind": "policy_rule",
                "scope": "project",
                "scope_ref": format!("project-token={secret}"),
                "claim": format!("Review candidate token={secret}"),
                "suggested_ai_instruction": "Ask before promotion.",
                "human_summary": format!("Legacy candidate token={secret}"),
                "evidence_refs": [format!("approval:approval-token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "operator_explicit",
                "source_refs": [format!("task:task-token={secret}")],
                "source_hashes": ["sha256:abc"],
                "core_tags": [format!("facet/token={secret}")],
                "proposed_tags": [format!("risk/token={secret}")],
                "review_reason": format!("Legacy review token={secret}"),
                "occurrence_count": 2,
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;
    let entries_before = fs::read(&entries_path)?;
    let candidates_before = fs::read(&candidates_path)?;

    let entries_json_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "entries", "--json"])
        .output()?;
    assert!(entries_json_output.status.success());
    let entries_json_stdout = String::from_utf8_lossy(&entries_json_output.stdout);
    assert!(!entries_json_stdout.contains(secret));
    let entries: serde_json::Value = serde_json::from_slice(&entries_json_output.stdout)?;
    assert!(entries[0]["id"]
        .as_str()
        .expect("entry id")
        .starts_with("redacted_id_sha256_"));
    assert!(entries[0]["core_tags"][0]
        .as_str()
        .expect("core tag")
        .contains("[REDACTED"));

    let candidates_json_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "candidates", "--json"])
        .output()?;
    assert!(candidates_json_output.status.success());
    let candidates_json_stdout = String::from_utf8_lossy(&candidates_json_output.stdout);
    assert!(!candidates_json_stdout.contains(secret));
    let candidates: serde_json::Value = serde_json::from_slice(&candidates_json_output.stdout)?;
    assert!(candidates[0]["id"]
        .as_str()
        .expect("candidate id")
        .starts_with("redacted_id_sha256_"));
    assert!(candidates[0]["proposed_tags"][0]
        .as_str()
        .expect("proposed tag")
        .contains("[REDACTED"));

    for args in [
        vec!["offdesk", "wiki", "entries"],
        vec!["offdesk", "wiki", "candidates"],
        vec!["offdesk", "wiki", "show", entry_id.as_str()],
    ] {
        let output = forager_command(temp.path()).args(args).output()?;
        assert!(output.status.success());
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.contains("[REDACTED"));
        assert!(!stdout.contains(secret));
    }

    let show_candidate_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", &candidate_id, "--json"])
        .output()?;
    assert!(show_candidate_output.status.success());
    assert!(!String::from_utf8_lossy(&show_candidate_output.stdout).contains(secret));

    let missing_id = format!("wiki_missing_token={secret}");
    let missing_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", &missing_id])
        .output()?;
    assert!(!missing_output.status.success());
    let missing_stderr = String::from_utf8_lossy(&missing_output.stderr);
    assert!(missing_stderr.contains("[REDACTED"));
    assert!(!missing_stderr.contains(secret));

    assert_eq!(fs::read(&entries_path)?, entries_before);
    assert_eq!(fs::read(&candidates_path)?, candidates_before);
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir.join("adaptive_wiki_corrections.jsonl").exists());
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_mutation_human_presentation_redacts_without_moving_authority() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let entry_id = format!("wiki_entry_token={secret}");
    let candidate_id = format!("wiki_candidate_token={secret}");
    let core_tag = format!("facet/token={secret}");
    let proposed_tag = format!("risk/token={secret}");
    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    let candidates_path = profile_dir.join("adaptive_wiki_candidates.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": entry_id,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Keep mutation authority in the store.",
                "ai_instruction": "Keep mutation authority in the store.",
                "human_summary": "Mutation fixture",
                "evidence_refs": [],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }]
        }))?,
    )?;
    fs::write(
        &candidates_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": candidate_id,
                "kind": "fact",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Reject this legacy candidate.",
                "suggested_ai_instruction": "Do not promote.",
                "human_summary": "Mutation fixture",
                "evidence_refs": [],
                "signal_kind": "operator_correction",
                "origin": "operator_explicit",
                "source_refs": [],
                "source_hashes": [],
                "review_reason": "Review required.",
                "occurrence_count": 1,
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;

    let retag_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "add-tag",
            &entry_id,
            "--core-tag",
            &core_tag,
            "--proposed-tag",
            &proposed_tag,
            "--reason",
            &format!("Classify token={secret}"),
        ])
        .output()?;
    assert!(retag_output.status.success());
    let retag_stdout = String::from_utf8_lossy(&retag_output.stdout);
    assert!(retag_stdout.contains("Retagged adaptive wiki entry"));
    assert!(retag_stdout.contains("[REDACTED"));
    assert!(!retag_stdout.contains(secret));

    let reject_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "reject",
            &candidate_id,
            "--reason",
            &format!("Reject token={secret}"),
        ])
        .output()?;
    assert!(reject_output.status.success());
    let reject_stdout = String::from_utf8_lossy(&reject_output.stdout);
    assert!(reject_stdout.contains("Rejected adaptive wiki candidate"));
    assert!(reject_stdout.contains("[REDACTED"));
    assert!(!reject_stdout.contains(secret));

    let entries: serde_json::Value = serde_json::from_slice(&fs::read(&entries_path)?)?;
    assert_eq!(entries["entries"][0]["id"], entry_id);
    assert!(entries["entries"][0]["core_tags"]
        .as_array()
        .expect("core tags")
        .iter()
        .any(|tag| tag == &core_tag));
    assert!(entries["entries"][0]["proposed_tags"]
        .as_array()
        .expect("proposed tags")
        .iter()
        .any(|tag| tag == &proposed_tag));
    let candidates: serde_json::Value = serde_json::from_slice(&fs::read(&candidates_path)?)?;
    assert!(candidates["candidates"]
        .as_array()
        .expect("candidates")
        .is_empty());

    let audit = fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?;
    let audit_rows: Vec<serde_json::Value> = audit
        .lines()
        .map(serde_json::from_str)
        .collect::<std::result::Result<_, _>>()?;
    assert_eq!(audit_rows.len(), 2);
    assert_eq!(audit_rows[0]["action"], "retag");
    assert_eq!(audit_rows[0]["subject_id"], entry_id);
    assert_eq!(audit_rows[0]["entry_id"], entry_id);
    assert!(!audit_rows[0]["reason"]
        .as_str()
        .expect("safe retag reason")
        .contains(secret));
    assert_eq!(audit_rows[1]["action"], "reject");
    assert_eq!(audit_rows[1]["subject_id"], candidate_id);
    assert_eq!(audit_rows[1]["candidate_id"], candidate_id);
    assert!(!audit_rows[1]["reason"]
        .as_str()
        .expect("safe reject reason")
        .contains(secret));
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_review_presentation_redacts_without_changing_report_or_source_bytes() -> Result<()>
{
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let home = temp.path().join(format!("home-token={secret}"));
    let profile_dir = profile_dir(&home);
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let candidates_path = profile_dir.join("adaptive_wiki_candidates.json");
    fs::write(
        &candidates_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": format!("wiki_candidate_token={secret}"),
                "kind": "policy_rule",
                "scope": "project",
                "scope_ref": format!("project-token={secret}"),
                "claim": format!("Review this candidate token={secret}"),
                "suggested_ai_instruction": "Ask before promotion.",
                "human_summary": "Review fixture",
                "evidence_refs": [format!("task:task-token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "operator_explicit",
                "source_refs": [],
                "source_hashes": [],
                "review_reason": format!("Review reason token={secret}"),
                "occurrence_count": 2,
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;
    let candidates_before = fs::read(&candidates_path)?;

    let json_output = forager_command(&home)
        .args(["offdesk", "wiki", "review", "--json"])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let report: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert_eq!(report["dry_run"], false);
    assert_eq!(report["summary"]["files_written"], 2);
    assert!(report["report_dir"]
        .as_str()
        .expect("operator report dir")
        .contains("[REDACTED"));
    assert!(report["proposals"][0]["subject_id"]
        .as_str()
        .expect("proposal subject")
        .starts_with("redacted_id_sha256_"));

    let review_root = profile_dir.join("adaptive_wiki_review_reports");
    let report_dirs = fs::read_dir(&review_root)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    assert_eq!(report_dirs.len(), 1);
    let report_dir = &report_dirs[0];
    let stored_report_bytes = fs::read(report_dir.join("report.json"))?;
    let stored_report: serde_json::Value = serde_json::from_slice(&stored_report_bytes)?;
    assert!(stored_report["report_dir"]
        .as_str()
        .expect("stored report dir")
        .contains(secret));
    assert!(report_dir.join("REPORT.md").is_file());

    let human_output = forager_command(&home)
        .args(["offdesk", "wiki", "review", "--dry-run"])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Adaptive wiki review report planned"));
    assert!(human_stdout.contains("[REDACTED"));
    assert!(!human_stdout.contains(secret));

    assert_eq!(fs::read(&candidates_path)?, candidates_before);
    assert_eq!(
        fs::read(report_dir.join("report.json"))?,
        stored_report_bytes
    );
    assert!(!profile_dir.join("adaptive_wiki_entries.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir.join("adaptive_wiki_corrections.jsonl").exists());
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_evidence_presentation_redacts_and_preserves_report_write_authority() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let home = temp.path().join(format!("evidence-home-token={secret}"));
    let profile_dir = profile_dir(&home);
    fs::create_dir_all(&profile_dir)?;
    let entry_id = format!("wiki_entry_token={secret}");
    let project_key = format!("project-token={secret}");
    let request_id = format!("request-token={secret}");
    let commands = vec![
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "evaluate-episode".to_string(),
            entry_id.clone(),
            "--project-key".to_string(),
            project_key.clone(),
            "--out-project-key".to_string(),
            format!("other-token={secret}"),
            "--dry-run".to_string(),
        ],
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "episode-trace".to_string(),
            "--request-id".to_string(),
            request_id,
            "--project-key".to_string(),
            project_key,
            "--dry-run".to_string(),
        ],
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "evaluate-recurrence".to_string(),
            entry_id.clone(),
            "--dry-run".to_string(),
        ],
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "promotion-chain".to_string(),
            entry_id.clone(),
            "--dry-run".to_string(),
        ],
    ];

    for base_args in &commands {
        for json in [false, true] {
            let mut args = base_args.clone();
            if json {
                args.push("--json".to_string());
            }
            let output = forager_command(&home).args(&args).output()?;
            assert!(
                output.status.success(),
                "{}",
                String::from_utf8_lossy(&output.stderr)
            );
            let stdout = String::from_utf8_lossy(&output.stdout);
            assert!(stdout.contains("[REDACTED") || stdout.contains("redacted_id_sha256_"));
            assert!(!stdout.contains(secret));
            if json {
                let _: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            }
        }
    }

    for report_dir in [
        "adaptive_wiki_episode_reports",
        "adaptive_wiki_episode_traces",
        "adaptive_wiki_recurrence_reports",
        "adaptive_wiki_promotion_chains",
    ] {
        assert!(!profile_dir.join(report_dir).exists());
    }

    let written_output = forager_command(&home)
        .args(["offdesk", "wiki", "promotion-chain", &entry_id, "--json"])
        .output()?;
    assert!(written_output.status.success());
    let written_stdout = String::from_utf8_lossy(&written_output.stdout);
    assert!(written_stdout.contains("[REDACTED"));
    assert!(!written_stdout.contains(secret));
    let written: serde_json::Value = serde_json::from_slice(&written_output.stdout)?;
    assert_eq!(written["dry_run"], false);
    assert_eq!(written["summary"]["files_written"], 3);

    let chain_root = profile_dir.join("adaptive_wiki_promotion_chains");
    let report_dirs = fs::read_dir(&chain_root)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::result::Result<Vec<_>, _>>()?;
    assert_eq!(report_dirs.len(), 1);
    let report_dir = &report_dirs[0];
    let stored_report = fs::read_to_string(report_dir.join("report.json"))?;
    assert!(stored_report.contains(secret));
    assert!(report_dir.join("chain.jsonl").is_file());
    assert!(report_dir.join("REPORT.md").is_file());
    assert!(!profile_dir.join("adaptive_wiki_entries.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_candidates.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir.join("adaptive_wiki_usage.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_audit_export_and_projection_outputs_are_operator_safe() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let home = temp.path().join(format!("wiki-home-{secret}"));
    let profile_dir = profile_dir(&home);
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let entry_id = format!("wiki-entry-{secret}");
    let project_key = format!("project-{secret}");
    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": entry_id,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": project_key,
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Audit and export presentation fixture",
                "ai_instruction": "Review the bounded projection before acting.",
                "human_summary": "Operator-safe export fixture",
                "evidence_refs": [],
                "core_tags": [format!("risk/{secret}")],
                "confidence": "inferred",
                "created_at": now,
                "updated_at": now,
                "review_after": now - Duration::minutes(1)
            }]
        }))?,
    )?;
    let entries_before = fs::read(&entries_path)?;

    let projection_commands = vec![
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "projection".to_string(),
            "--project-key".to_string(),
            project_key.clone(),
        ],
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "projection".to_string(),
            "--project-key".to_string(),
            project_key.clone(),
            "--report".to_string(),
        ],
        vec![
            "offdesk".to_string(),
            "wiki".to_string(),
            "projection".to_string(),
            "--project-key".to_string(),
            project_key.clone(),
            "--compare-review-expired-policy".to_string(),
        ],
    ];
    for base_args in &projection_commands {
        for json in [false, true] {
            let mut args = base_args.clone();
            if json {
                args.push("--json".to_string());
            }
            let output = forager_command(&home).args(&args).output()?;
            assert!(
                output.status.success(),
                "stdout:\n{}\nstderr:\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            let stdout = String::from_utf8_lossy(&output.stdout);
            assert!(stdout.contains("[REDACTED") || stdout.contains("redacted_id_sha256_"));
            assert!(!stdout.contains(secret));
            if json {
                let _: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            }
        }
    }

    for json in [false, true] {
        let mut args = vec!["offdesk", "wiki", "lint"];
        if json {
            args.push("--json");
        }
        let output = forager_command(&home).args(args).output()?;
        assert!(output.status.success());
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.contains("[REDACTED") || stdout.contains("redacted_id_sha256_"));
        assert!(!stdout.contains(secret));
        if json {
            let _: serde_json::Value = serde_json::from_slice(&output.stdout)?;
        }
    }

    let vault_dir = home.join(format!("vault-{secret}"));
    for json in [false, true] {
        let mut args = vec![
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            vault_dir.to_str().expect("vault path"),
            "--dry-run",
        ];
        if json {
            args.push("--json");
        }
        let output = forager_command(&home).args(args).output()?;
        assert!(output.status.success());
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.contains("[REDACTED"));
        assert!(!stdout.contains(secret));
        if json {
            let _: serde_json::Value = serde_json::from_slice(&output.stdout)?;
        }
    }
    assert!(!vault_dir.exists());

    let export_output = forager_command(&home)
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            vault_dir.to_str().expect("vault path"),
            "--json",
        ])
        .output()?;
    assert!(export_output.status.success());
    let export_stdout = String::from_utf8_lossy(&export_output.stdout);
    assert!(export_stdout.contains("[REDACTED"));
    assert!(!export_stdout.contains(secret));
    let export: serde_json::Value = serde_json::from_slice(&export_output.stdout)?;
    assert_eq!(export["dry_run"], false);
    assert_eq!(
        export["summary"]["files_written"],
        export["summary"]["files_planned"]
    );
    let vault_index = fs::read_to_string(vault_dir.join("index.md"))?;
    assert!(vault_index.contains("[REDACTED"));
    assert!(!vault_index.contains(secret));

    let graph_dir = home.join(format!("graph-{secret}"));
    let graph_output = forager_command(&home)
        .args([
            "offdesk",
            "wiki",
            "graph",
            "--output",
            graph_dir.to_str().expect("graph path"),
            "--json",
        ])
        .output()?;
    assert!(graph_output.status.success());
    let graph_stdout = String::from_utf8_lossy(&graph_output.stdout);
    assert!(graph_stdout.contains("redacted_id_sha256_"));
    assert!(!graph_stdout.contains(secret));
    let _: serde_json::Value = serde_json::from_slice(&graph_output.stdout)?;
    for file_name in ["graph.json", "graph.md"] {
        let content = fs::read_to_string(graph_dir.join(file_name))?;
        assert!(content.contains("redacted_id_sha256_"));
        assert!(!content.contains(secret));
    }

    let graph_human_dir = home.join(format!("graph-human-{secret}"));
    let graph_human_output = forager_command(&home)
        .args([
            "offdesk",
            "wiki",
            "graph",
            "--output",
            graph_human_dir.to_str().expect("graph human path"),
            "--dry-run",
        ])
        .output()?;
    assert!(graph_human_output.status.success());
    let graph_human_stdout = String::from_utf8_lossy(&graph_human_output.stdout);
    assert!(
        graph_human_stdout.contains("[REDACTED")
            || graph_human_stdout.contains("redacted_id_sha256_")
    );
    assert!(!graph_human_stdout.contains(secret));
    assert!(!graph_human_dir.exists());

    assert_eq!(fs::read(&entries_path)?, entries_before);
    assert!(!profile_dir.join("adaptive_wiki_candidates.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir.join("adaptive_wiki_usage.jsonl").exists());
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_remaining_presenters_redact_and_preserve_authority() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let profile = format!("profile-{secret}");
    let project_key = format!("project-{secret}");
    let entry_id = format!("wiki-entry-{secret}");
    let profile_dir = profile_dir_for(temp.path(), &profile);
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();

    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": entry_id,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": project_key,
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Remaining presentation boundary fixture",
                "ai_instruction": "Read the bounded brief before acting.",
                "human_summary": "Brief and attention fixture",
                "evidence_refs": ["task:fixture"],
                "confidence": "explicit",
                "created_at": now - Duration::days(2),
                "updated_at": now - Duration::days(2),
                "review_after": now - Duration::days(1)
            }]
        }))?,
    )?;

    let events_path = profile_dir.join("adaptive_wiki_review_events.jsonl");
    fs::write(
        &events_path,
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": format!("wiki-event-{secret}"),
                "proposal_id": format!("wiki-proposal-{secret}"),
                "decision": "accepted",
                "proposal_action": "promote",
                "subject_kind": format!("candidate-{secret}"),
                "subject_id": format!("wiki-candidate-{secret}"),
                "actor": format!("operator-{secret}"),
                "reason": format!("accepted token={secret}"),
                "evidence_refs": [format!("review:fixture?token={secret}")],
                "created_at": now
            }))?
        ),
    )?;
    let corrections_path = profile_dir.join("adaptive_wiki_corrections.jsonl");
    fs::write(
        &corrections_path,
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": format!("wiki-correction-{secret}"),
                "correction_kind": "operator_correction",
                "entry_id": format!("wiki-entry-{secret}"),
                "task_id": format!("task-{secret}"),
                "request_id": format!("request-{secret}"),
                "project_key": format!("project-{secret}"),
                "summary": format!("correction token={secret}"),
                "created_at": now
            }))?
        ),
    )?;
    let acknowledgements_path =
        profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl");
    fs::write(
        &acknowledgements_path,
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": format!("wiki-runtime-ack-{secret}"),
                "scope_mode": "exact_query",
                "query": {
                    "session_id": format!("session-{secret}"),
                    "project_key": format!("project-{secret}"),
                    "artifact_kind": format!("report-{secret}")
                },
                "budget": {
                    "max_entries": 8,
                    "max_context_chars": 4000,
                    "max_instruction_chars": 500
                },
                "policy": {"review_expired": "exclude"},
                "comparison_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "selected_only_in_warn": [format!("wiki-entry-{secret}")],
                "selected_only_in_strict": [],
                "review_expired_excluded": [format!("wiki-entry-{secret}")],
                "reason": format!("legacy acknowledgement token={secret}"),
                "created_at": now - Duration::days(2),
                "expires_at": now - Duration::days(1)
            }))?
        ),
    )?;

    let entries_before = fs::read(&entries_path)?;
    let events_before = fs::read(&events_path)?;
    let corrections_before = fs::read(&corrections_path)?;
    let acknowledgements_before = fs::read(&acknowledgements_path)?;

    let read_only_commands = vec![
        vec!["offdesk", "wiki", "proposal-events"],
        vec!["offdesk", "wiki", "corrections"],
        vec![
            "offdesk",
            "wiki",
            "review-after-report",
            "--project-key",
            &project_key,
        ],
        vec!["offdesk", "wiki", "runtime-policy-acks"],
        vec!["offdesk", "wiki", "runtime-policy-ack-report"],
        vec!["offdesk", "wiki", "brief", "--project-key", &project_key],
    ];
    for base_args in &read_only_commands {
        for json in [false, true] {
            let mut args = vec!["-p", profile.as_str()];
            args.extend(base_args.iter().copied());
            if json {
                args.push("--json");
            }
            let output = forager_command(temp.path()).args(args).output()?;
            assert!(
                output.status.success(),
                "stdout:\n{}\nstderr:\n{}",
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            );
            let stdout = String::from_utf8_lossy(&output.stdout);
            assert!(stdout.contains("[REDACTED") || stdout.contains("redacted_id_sha256_"));
            assert!(!stdout.contains(secret));
            if json {
                let _: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            }
        }
    }

    let recorded_event_output = forager_command(temp.path())
        .args([
            "-p",
            &profile,
            "offdesk",
            "wiki",
            "record-proposal-event",
            &format!("new-proposal-{secret}"),
            "--decision",
            "rejected",
            "--subject-kind",
            &format!("entry-{secret}"),
            "--subject-id",
            &format!("new-entry-{secret}"),
            "--reason",
            &format!("rejected token={secret}"),
        ])
        .output()?;
    assert!(recorded_event_output.status.success());
    let recorded_event_stdout = String::from_utf8_lossy(&recorded_event_output.stdout);
    assert!(recorded_event_stdout.contains("redacted_id_sha256_"));
    assert!(!recorded_event_stdout.contains(secret));
    let events_after_append = fs::read(&events_path)?;
    assert!(events_after_append.starts_with(&events_before));
    assert_eq!(
        String::from_utf8_lossy(&events_after_append)
            .lines()
            .count(),
        2
    );

    let project_root = temp.path().join(format!("brief-project-{secret}"));
    fs::create_dir_all(&project_root)?;
    let brief_path = project_root.join(format!("brief-{secret}.md"));
    let write_output = forager_command(temp.path())
        .args([
            "-p",
            &profile,
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            &project_key,
            "--out",
            brief_path.to_str().expect("brief path"),
            "--json",
        ])
        .output()?;
    assert!(write_output.status.success());
    let write_stdout = String::from_utf8_lossy(&write_output.stdout);
    assert!(write_stdout.contains("[REDACTED"));
    assert!(!write_stdout.contains(secret));
    let manifest: serde_json::Value = serde_json::from_slice(&write_output.stdout)?;
    assert_eq!(manifest["schema"], "wiki_context_manifest.v1");

    let brief = fs::read_to_string(&brief_path)?;
    assert!(brief.contains("[REDACTED"));
    assert!(brief.contains("**STALE**"));
    assert!(!brief.contains(secret));

    let confirmation_output = forager_command(temp.path())
        .args([
            "-p",
            &profile,
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            &project_key,
            "--out",
            brief_path.to_str().expect("brief path"),
        ])
        .output()?;
    assert!(confirmation_output.status.success());
    let confirmation_stdout = String::from_utf8_lossy(&confirmation_output.stdout);
    assert!(confirmation_stdout.contains("[REDACTED"));
    assert!(!confirmation_stdout.contains(secret));

    assert_eq!(fs::read(&entries_path)?, entries_before);
    assert_eq!(fs::read(&events_path)?, events_after_append);
    assert_eq!(fs::read(&corrections_path)?, corrections_before);
    assert_eq!(fs::read(&acknowledgements_path)?, acknowledgements_before);
    assert!(!profile_dir.join("adaptive_wiki_candidates.json").exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());
    assert!(!profile_dir.join("adaptive_wiki_usage.jsonl").exists());
    assert!(!profile_dir
        .join("adaptive_wiki_promotion_receipts")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_read_only_commands_expose_candidates_entries_projection_and_lint() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_project_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Project entries are visible to operators",
                    "ai_instruction": format!("Confirm project-specific wiki rules before acting token={secret}"),
                    "human_summary": "Human project note",
                    "evidence_refs": ["task:project"],
                    "core_tags": ["project/project"],
                    "proposed_tags": ["method/baseline-first"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                },
                {
                    "id": "wiki_needs_review",
                    "kind": "policy_rule",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Expired review entry",
                    "ai_instruction": "Review this entry.",
                    "human_summary": "Needs review",
                    "evidence_refs": [],
                    "confidence": "inferred",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::minutes(1)
                },
                {
                    "id": "wiki_other_project",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "other-project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Other project rule",
                    "ai_instruction": "Do not include this.",
                    "human_summary": "Other project",
                    "evidence_refs": ["task:other"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [
                {
                    "id": "wiki_candidate_denial",
                    "kind": "policy_rule",
                    "scope": "project",
                    "scope_ref": "project",
                    "claim": "Operator denied dispatch for project task",
                    "suggested_ai_instruction": "Ask for confirmation before retrying dispatch.",
                    "human_summary": "Captured denial",
                    "evidence_refs": ["approval:approval_one"],
                    "signal_kind": "approval_denial",
                    "origin": "operator_explicit",
                    "source_refs": [format!("approval:approval_one?token={secret}")],
                    "source_hashes": ["sha256:abc"],
                    "core_tags": ["risk/operator-denial"],
                    "proposed_tags": ["project/project"],
                    "suggested_scope": {
                        "scope": "project",
                        "scope_ref": "project"
                    },
                    "review_reason": format!("Review before promotion token={secret}"),
                    "occurrence_count": 2,
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now
                }
            ]
        }))?,
    )?;
    let catalog_entries_before = fs::read(profile_dir.join("adaptive_wiki_entries.json"))?;
    let catalog_candidates_before = fs::read(profile_dir.join("adaptive_wiki_candidates.json"))?;

    let candidates_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "candidates",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(candidates_output.status.success());
    let candidates: serde_json::Value = serde_json::from_slice(&candidates_output.stdout)?;
    assert_eq!(candidates.as_array().expect("candidates").len(), 1);
    assert_eq!(candidates[0]["id"], "wiki_candidate_denial");
    assert_eq!(candidates[0]["signal_kind"], "approval_denial");
    assert_eq!(candidates[0]["origin"], "operator_explicit");
    assert_eq!(candidates[0]["suggested_scope"]["scope"], "project");
    assert!(!String::from_utf8_lossy(&candidates_output.stdout).contains(secret));
    assert!(candidates[0]["source_refs"][0]
        .as_str()
        .expect("source ref")
        .contains("[REDACTED]"));
    assert!(candidates[0]["review_reason"]
        .as_str()
        .expect("review reason")
        .contains("[REDACTED]"));

    let candidates_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "candidates", "--project-key", "project"])
        .output()?;
    assert!(candidates_human_output.status.success());
    let candidates_stdout = String::from_utf8_lossy(&candidates_human_output.stdout);
    assert!(candidates_stdout.contains("wiki_candidate_denial"));
    assert!(candidates_stdout.contains("project:project"));
    assert!(candidates_stdout.contains("review:"));
    assert!(candidates_stdout.contains("sources:"));
    assert!(!candidates_stdout.contains(secret));

    let entries_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "entries",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(entries_output.status.success());
    let entries: serde_json::Value = serde_json::from_slice(&entries_output.stdout)?;
    let entry_ids: Vec<_> = entries
        .as_array()
        .expect("entries")
        .iter()
        .map(|entry| entry["id"].as_str().expect("entry id"))
        .collect();
    assert!(entry_ids.contains(&"wiki_project_entry"));
    assert!(entry_ids.contains(&"wiki_needs_review"));
    assert!(!entry_ids.contains(&"wiki_other_project"));

    let entries_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "entries", "--project-key", "project"])
        .output()?;
    assert!(entries_human_output.status.success());
    let entries_stdout = String::from_utf8_lossy(&entries_human_output.stdout);
    assert!(entries_stdout.contains("AGENT_MODES"));
    assert!(entries_stdout.contains("wiki_project_entry"));
    assert!(entries_stdout.contains("wiki_needs_review"));
    assert!(!entries_stdout.contains("wiki_other_project"));
    assert!(entries_stdout.contains("summary: Human project note"));
    assert!(entries_stdout.contains("evidence: task:project"));

    let projection_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(projection_output.status.success());
    let projection: serde_json::Value = serde_json::from_slice(&projection_output.stdout)?;
    assert_eq!(projection.as_array().expect("projection").len(), 2);
    assert!(projection
        .as_array()
        .expect("projection")
        .iter()
        .any(|entry| entry["instruction"]
            .as_str()
            .expect("instruction")
            .contains("[REDACTED]")));
    assert!(!String::from_utf8_lossy(&projection_output.stdout).contains(secret));

    let projection_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--report",
            "--max-entries",
            "1",
            "--max-instruction-chars",
            "24",
            "--json",
        ])
        .output()?;
    assert!(projection_report_output.status.success());
    assert!(!String::from_utf8_lossy(&projection_report_output.stdout).contains(secret));
    let projection_report: serde_json::Value =
        serde_json::from_slice(&projection_report_output.stdout)?;
    assert_eq!(projection_report["budget"]["max_entries"], 1);
    assert_eq!(projection_report["budget"]["max_instruction_chars"], 24);
    assert_eq!(projection_report["summary"]["selected"], 1);
    assert_eq!(projection_report["summary"]["rejected"], 1);
    assert_eq!(projection_report["summary"]["instructions_truncated"], 1);
    assert_eq!(projection_report["selected"][0]["id"], "wiki_project_entry");
    assert!(projection_report["selected"][0]["instruction"]
        .as_str()
        .expect("truncated instruction")
        .ends_with("..."));
    assert!(projection_report["rejected"]
        .as_array()
        .expect("projection rejected entries")
        .iter()
        .any(|entry| entry["entry_id"] == "wiki_needs_review"
            && entry["reason"] == "budget_max_entries"));

    let projection_review_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--report",
            "--max-entries",
            "2",
            "--json",
        ])
        .output()?;
    assert!(projection_review_report_output.status.success());
    assert!(!String::from_utf8_lossy(&projection_review_report_output.stdout).contains(secret));
    let projection_review_report: serde_json::Value =
        serde_json::from_slice(&projection_review_report_output.stdout)?;
    assert_eq!(
        projection_review_report["summary"]["review_expired_projected"],
        1
    );
    assert!(projection_review_report["selected"]
        .as_array()
        .expect("projection selected entries")
        .iter()
        .any(|entry| entry["id"] == "wiki_needs_review"));
    assert!(projection_review_report["review_expired"]
        .as_array()
        .expect("review expired projection warnings")
        .iter()
        .any(|entry| entry["entry_id"] == "wiki_needs_review"
            && entry["detail"]
                .as_str()
                .expect("review expired detail")
                .contains("default warn policy")));

    let strict_projection_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--report",
            "--max-entries",
            "2",
            "--exclude-review-expired",
            "--json",
        ])
        .output()?;
    assert!(strict_projection_report_output.status.success());
    assert!(!String::from_utf8_lossy(&strict_projection_report_output.stdout).contains(secret));
    let strict_projection_report: serde_json::Value =
        serde_json::from_slice(&strict_projection_report_output.stdout)?;
    assert_eq!(
        strict_projection_report["policy"]["review_expired"],
        "exclude"
    );
    assert_eq!(
        strict_projection_report["summary"]["review_expired_projected"],
        0
    );
    assert!(!strict_projection_report["selected"]
        .as_array()
        .expect("strict selected entries")
        .iter()
        .any(|entry| entry["id"] == "wiki_needs_review"));
    assert!(strict_projection_report["rejected"]
        .as_array()
        .expect("strict rejected entries")
        .iter()
        .any(|entry| entry["entry_id"] == "wiki_needs_review"
            && entry["reason"] == "review_expired_excluded"));

    let comparison_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--compare-review-expired-policy",
            "--max-entries",
            "2",
            "--json",
        ])
        .output()?;
    assert!(comparison_output.status.success());
    assert!(!String::from_utf8_lossy(&comparison_output.stdout).contains(secret));
    let comparison: serde_json::Value = serde_json::from_slice(&comparison_output.stdout)?;
    assert_eq!(comparison["warn"]["policy"]["review_expired"], "warn");
    assert_eq!(comparison["strict"]["policy"]["review_expired"], "exclude");
    assert!(comparison["summary"]["selected_only_in_warn"]
        .as_array()
        .expect("selected only in warn")
        .iter()
        .any(|entry| entry == "wiki_needs_review"));
    assert!(comparison["summary"]["review_expired_excluded"]
        .as_array()
        .expect("review expired excluded")
        .iter()
        .any(|entry| entry == "wiki_needs_review"));
    assert!(!comparison["strict"]["selected"]
        .as_array()
        .expect("strict comparison selected")
        .iter()
        .any(|entry| entry["id"] == "wiki_needs_review"));

    let projection_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "projection", "--project-key", "project"])
        .output()?;
    assert!(projection_human_output.status.success());
    let projection_human_stdout = String::from_utf8_lossy(&projection_human_output.stdout);
    assert!(projection_human_stdout.contains("INSTRUCTION"));
    assert!(projection_human_stdout.contains("wiki_project_entry"));
    assert!(projection_human_stdout.contains("wiki_needs_review"));
    assert!(projection_human_stdout.contains("[REDACTED]"));
    assert!(!projection_human_stdout.contains(secret));
    assert!(!projection_human_stdout.contains("wiki_other_project"));

    let projection_report_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--report",
            "--max-entries",
            "2",
        ])
        .output()?;
    assert!(projection_report_human_output.status.success());
    let projection_report_human_stdout =
        String::from_utf8_lossy(&projection_report_human_output.stdout);
    assert!(projection_report_human_stdout.contains("Adaptive wiki projection: 2 selected"));
    assert!(projection_report_human_stdout.contains("policy: review_expired=Warn"));
    assert!(projection_report_human_stdout.contains("selected:"));
    assert!(projection_report_human_stdout.contains("review_expired:"));
    assert!(projection_report_human_stdout.contains("wiki_needs_review"));
    assert!(!projection_report_human_stdout.contains(secret));

    let comparison_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project",
            "--compare-review-expired-policy",
            "--max-entries",
            "2",
        ])
        .output()?;
    assert!(comparison_human_output.status.success());
    let comparison_human_stdout = String::from_utf8_lossy(&comparison_human_output.stdout);
    assert!(comparison_human_stdout
        .contains("Adaptive wiki projection review-expired policy comparison"));
    assert!(comparison_human_stdout.contains("warn:"));
    assert!(comparison_human_stdout.contains("strict:"));
    assert!(comparison_human_stdout.contains("selected only in warn: wiki_needs_review"));
    assert!(comparison_human_stdout.contains("review_expired_excluded: wiki_needs_review"));
    assert!(!comparison_human_stdout.contains(secret));

    let brief_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "2",
            "--json",
        ])
        .output()?;
    assert!(brief_output.status.success());
    let brief_stdout = String::from_utf8_lossy(&brief_output.stdout);
    assert!(!brief_stdout.contains(secret));
    let brief: serde_json::Value = serde_json::from_slice(&brief_output.stdout)?;
    assert_eq!(brief["schema"], "wiki_brief.v1");
    assert_eq!(brief["profile"], "default");
    assert_eq!(brief["scope"], "project");
    assert_eq!(brief["entries"].as_array().expect("brief entries").len(), 2);
    let needs_review = brief["entries"]
        .as_array()
        .expect("brief entries")
        .iter()
        .find(|entry| entry["id"] == "wiki_needs_review")
        .expect("review-expired brief entry");
    assert_eq!(needs_review["kind"], "PolicyRule");
    assert_eq!(needs_review["text"], "Review this entry.");
    assert_eq!(needs_review["stale"], true);
    let project_entry = brief["entries"]
        .as_array()
        .expect("brief entries")
        .iter()
        .find(|entry| entry["id"] == "wiki_project_entry")
        .expect("project brief entry");
    assert_eq!(project_entry["kind"], "Procedure");
    assert!(project_entry["text"]
        .as_str()
        .expect("project brief text")
        .contains("[REDACTED]"));
    assert!(!brief["entries"]
        .as_array()
        .expect("brief entries")
        .iter()
        .any(|entry| entry["id"] == "wiki_other_project"));

    let brief_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "2",
        ])
        .output()?;
    assert!(brief_human_output.status.success());
    let brief_human_stdout = String::from_utf8_lossy(&brief_human_output.stdout);
    assert!(brief_human_stdout.contains("# Wiki Brief: default / project"));
    assert!(brief_human_stdout.contains("## Policy rules"));
    assert!(brief_human_stdout.contains("## Procedures"));
    assert!(brief_human_stdout.contains("Review this entry. **STALE**"));
    assert!(brief_human_stdout.contains("wiki_project_entry"));
    assert!(brief_human_stdout.contains("[REDACTED]"));
    assert!(!brief_human_stdout.contains("wiki_other_project"));
    assert!(!brief_human_stdout.contains(secret));

    let brief_path = temp.path().join("generated/wiki/.wiki-brief.md");
    fs::create_dir_all(brief_path.parent().expect("brief parent"))?;
    let brief_file_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "2",
            "--out",
            brief_path.to_str().expect("utf-8 brief path"),
        ])
        .output()?;
    assert!(brief_file_output.status.success());
    let brief_file_stdout = String::from_utf8_lossy(&brief_file_output.stdout);
    assert!(brief_file_stdout.contains("Wrote wiki brief (2 entries) to"));
    assert!(brief_file_stdout.contains(&brief_path.display().to_string()));
    let brief_file = fs::read_to_string(&brief_path)?;
    assert!(brief_file.contains("# Wiki Brief: default / project"));
    assert!(brief_file.contains("Schema: wiki_brief.v1"));
    assert!(brief_file.contains("Agent mode: shared"));
    assert!(brief_file.contains("Projection: sha256:"));
    assert!(brief_file.contains("Entries: 2 (1 stale)"));
    assert!(brief_file.contains("Review this entry. **STALE**"));
    assert!(brief_file.contains("[REDACTED]"));
    assert!(!brief_file.contains("wiki_other_project"));
    assert!(!brief_file.contains(secret));

    let brief_metadata = fs::metadata(&brief_path)?;
    let unchanged_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "2",
            "--out",
            brief_path.to_str().expect("utf-8 brief path"),
        ])
        .output()?;
    assert!(unchanged_output.status.success());
    assert!(String::from_utf8_lossy(&unchanged_output.stdout).contains("Wiki brief unchanged"));
    assert_eq!(fs::read_to_string(&brief_path)?, brief_file);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(fs::metadata(&brief_path)?.ino(), brief_metadata.ino());
    }

    let manifest_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "project",
            "--max-entries",
            "2",
            "--out",
            brief_path.to_str().expect("utf-8 brief path"),
            "--json",
        ])
        .output()?;
    assert!(manifest_output.status.success());
    let manifest: serde_json::Value = serde_json::from_slice(&manifest_output.stdout)?;
    assert_eq!(manifest["schema"], "wiki_context_manifest.v1");
    assert_eq!(manifest["outcome"]["projection_status"], "unchanged");
    assert_eq!(manifest["outcome"]["detail"]["project_key"], "project");
    assert_eq!(manifest["outcome"]["detail"]["agent_mode"], "shared");
    assert_eq!(manifest["outcome"]["detail"]["entry_count"], 2);

    let empty_brief_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "missing-project",
            "--json",
        ])
        .output()?;
    assert!(empty_brief_output.status.success());
    let empty_brief: serde_json::Value = serde_json::from_slice(&empty_brief_output.stdout)?;
    assert_eq!(empty_brief["schema"], "wiki_brief.v1");
    assert_eq!(empty_brief["scope"], "missing-project");
    assert!(empty_brief["entries"]
        .as_array()
        .expect("empty brief entries")
        .is_empty());

    let empty_brief_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "brief",
            "--project-key",
            "missing-project",
        ])
        .output()?;
    assert!(empty_brief_human_output.status.success());
    let empty_brief_human_stdout = String::from_utf8_lossy(&empty_brief_human_output.stdout);
    assert!(empty_brief_human_stdout.contains("# Wiki Brief: default / missing-project"));
    assert!(empty_brief_human_stdout.contains("(no promoted in-scope entries yet)"));
    assert!(!empty_brief_human_stdout.contains(secret));

    let show_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", "wiki_candidate_denial", "--json"])
        .output()?;
    assert!(show_output.status.success());
    let show: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(show["kind"], "candidate");
    assert_eq!(show["candidate"]["id"], "wiki_candidate_denial");

    let show_candidate_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", "wiki_candidate_denial"])
        .output()?;
    assert!(show_candidate_human_output.status.success());
    let show_candidate_stdout = String::from_utf8_lossy(&show_candidate_human_output.stdout);
    assert!(show_candidate_stdout.contains("Adaptive wiki candidate wiki_candidate_denial"));
    assert!(show_candidate_stdout.contains("origin:     OperatorExplicit"));
    assert!(show_candidate_stdout.contains("sources:"));
    assert!(!show_candidate_stdout.contains(secret));

    let show_entry_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", "wiki_project_entry"])
        .output()?;
    assert!(show_entry_human_output.status.success());
    let show_entry_stdout = String::from_utf8_lossy(&show_entry_human_output.stdout);
    assert!(show_entry_stdout.contains("Adaptive wiki entry wiki_project_entry"));
    assert!(show_entry_stdout.contains("scope:      project:project"));
    assert!(show_entry_stdout.contains("claim:      Project entries are visible to operators"));

    let missing_show_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", "wiki_missing"])
        .output()?;
    assert!(!missing_show_output.status.success());
    assert!(String::from_utf8_lossy(&missing_show_output.stderr)
        .contains("Adaptive wiki entry or candidate not found: wiki_missing"));
    assert_eq!(
        fs::read(profile_dir.join("adaptive_wiki_entries.json"))?,
        catalog_entries_before
    );
    assert_eq!(
        fs::read(profile_dir.join("adaptive_wiki_candidates.json"))?,
        catalog_candidates_before
    );

    let lint_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "lint", "--json"])
        .output()?;
    assert!(lint_output.status.success());
    let lint: serde_json::Value = serde_json::from_slice(&lint_output.stdout)?;
    assert_eq!(lint["summary"]["entries_checked"], 3);
    assert_eq!(lint["summary"]["candidates_checked"], 1);
    let lint_codes: Vec<_> = lint["issues"]
        .as_array()
        .expect("lint issues")
        .iter()
        .map(|issue| issue["code"].as_str().expect("lint code"))
        .collect();
    assert!(lint_codes.contains(&"promoted_without_evidence"));
    assert!(lint_codes.contains(&"review_expired"));

    let lint_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "lint"])
        .output()?;
    assert!(lint_human_output.status.success());
    let lint_human_stdout = String::from_utf8_lossy(&lint_human_output.stdout);
    assert!(lint_human_stdout.contains("Adaptive wiki lint:"));
    assert!(lint_human_stdout.contains("3 entries, 1 candidates"));
    assert!(lint_human_stdout.contains("wiki_needs_review"));
    assert!(!lint_human_stdout.contains(secret));

    let graph_dir = temp.path().join("adaptive-wiki-graph");
    let graph_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "graph",
            "--output",
            graph_dir.to_str().expect("graph dir"),
            "--json",
        ])
        .output()?;
    assert!(graph_output.status.success());
    let graph: serde_json::Value = serde_json::from_slice(&graph_output.stdout)?;
    assert_eq!(graph["summary"]["entries"], 3);
    assert_eq!(graph["summary"]["candidates"], 1);
    assert!(graph["nodes"]
        .as_array()
        .expect("graph nodes")
        .iter()
        .any(|node| node["id"] == "tag:project/project"));
    assert!(graph["edges"]
        .as_array()
        .expect("graph edges")
        .iter()
        .any(|edge| edge["relationship"] == "has_proposed_tag"
            && edge["target"] == "tag:project/project"));
    assert!(graph["review_issues"]
        .as_array()
        .expect("graph review issues")
        .iter()
        .any(|issue| issue["code"] == "proposed_tag_matches_core_prefix"));
    assert!(graph_dir.join("graph.json").is_file());
    assert!(graph_dir.join("graph.md").is_file());
    assert!(!String::from_utf8_lossy(&graph_output.stdout).contains(secret));
    assert!(!fs::read_to_string(graph_dir.join("graph.md"))?.contains(secret));

    let graph_human_dir = temp.path().join("adaptive-wiki-graph-human");
    let graph_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "graph",
            "--output",
            graph_human_dir.to_str().expect("human graph dir"),
            "--dry-run",
        ])
        .output()?;
    assert!(graph_human_output.status.success());
    let graph_human_stdout = String::from_utf8_lossy(&graph_human_output.stdout);
    assert!(graph_human_stdout.contains("Adaptive wiki tag graph:"));
    assert!(graph_human_stdout.contains("entries: 3  candidates: 1"));
    assert!(graph_human_stdout.contains("export: planned 2 files to"));
    assert!(graph_human_stdout.contains("proposed_tag_matches_core_prefix"));
    assert!(!graph_human_stdout.contains(secret));
    assert!(!graph_human_dir.exists());

    let entries_before_episode =
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let candidates_before_episode =
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?;
    let episode_dry_run_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-episode",
            "wiki_project_entry",
            "--project-key",
            "project",
            "--out-project-key",
            "other-project",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(episode_dry_run_output.status.success());
    let episode_dry_run: serde_json::Value =
        serde_json::from_slice(&episode_dry_run_output.stdout)?;
    assert_eq!(episode_dry_run["dry_run"], true);
    assert_eq!(episode_dry_run["passed"], false);
    assert_eq!(episode_dry_run["summary"]["target_entry_in_scope"], true);
    assert_eq!(
        episode_dry_run["summary"]["target_entry_out_of_scope"],
        false
    );
    assert_eq!(
        episode_dry_run["summary"]["review_expired_entry_projected"],
        true
    );
    assert_eq!(episode_dry_run["summary"]["files_written"], 0);
    assert!(episode_dry_run["review_expired_projected_entry_ids"]
        .as_array()
        .expect("review expired ids")
        .iter()
        .any(|id| id == "wiki_needs_review"));
    assert!(!String::from_utf8_lossy(&episode_dry_run_output.stdout).contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_episode_reports").exists());
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before_episode
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before_episode
    );

    let episode_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-episode",
            "wiki_project_entry",
            "--project-key",
            "project",
            "--out-project-key",
            "other-project",
            "--dry-run",
        ])
        .output()?;
    assert!(episode_human_output.status.success());
    let episode_human_stdout = String::from_utf8_lossy(&episode_human_output.stdout);
    assert!(episode_human_stdout.contains("Adaptive wiki episode evaluation planned"));
    assert!(episode_human_stdout.contains("target: wiki_project_entry"));
    assert!(episode_human_stdout.contains("review-expired entries were projected"));
    assert!(episode_human_stdout.contains("in-scope ids:"));
    assert!(!episode_human_stdout.contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_episode_reports").exists());

    let episode_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-episode",
            "wiki_project_entry",
            "--project-key",
            "project",
            "--out-project-key",
            "other-project",
            "--json",
        ])
        .output()?;
    assert!(episode_output.status.success());
    let episode: serde_json::Value = serde_json::from_slice(&episode_output.stdout)?;
    assert_eq!(episode["dry_run"], false);
    assert_eq!(episode["summary"]["files_written"], 2);
    assert_eq!(episode["summary"]["scope_leakage_count"], 0);
    let episode_dir = PathBuf::from(episode["report_dir"].as_str().expect("episode report dir"));
    assert!(episode_dir.join("episode.json").is_file());
    assert!(episode_dir.join("EPISODE.md").is_file());
    let episode_md = fs::read_to_string(episode_dir.join("EPISODE.md"))?;
    assert!(episode_md.contains("Adaptive Wiki Episode Evaluation"));
    assert!(episode_md.contains("wiki_project_entry"));
    assert!(episode_md.contains("review-expired entries were projected"));
    assert!(!episode_md.contains(secret));
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before_episode
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before_episode
    );

    let vault_dir = temp.path().join("adaptive-wiki-vault");
    let dry_run_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            vault_dir.to_str().expect("vault dir"),
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(dry_run_output.status.success());
    let dry_run: serde_json::Value = serde_json::from_slice(&dry_run_output.stdout)?;
    assert_eq!(dry_run["dry_run"], true);
    assert_eq!(dry_run["summary"]["entries_exported"], 3);
    assert_eq!(dry_run["summary"]["candidates_exported"], 1);
    assert_eq!(dry_run["summary"]["files_written"], 0);
    assert!(dry_run["files"]
        .as_array()
        .expect("export files")
        .iter()
        .any(|file| file["path"] == "entries/procedure/wiki-project-entry.md"));
    assert!(!vault_dir.exists());
    assert!(!String::from_utf8_lossy(&dry_run_output.stdout).contains(secret));

    let human_vault_dir = temp.path().join("adaptive-wiki-vault-human");
    let export_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            human_vault_dir.to_str().expect("human vault dir"),
            "--dry-run",
        ])
        .output()?;
    assert!(export_human_output.status.success());
    let export_human_stdout = String::from_utf8_lossy(&export_human_output.stdout);
    assert!(export_human_stdout.contains("Adaptive wiki markdown export planned"));
    assert!(export_human_stdout.contains("status: Missing  reexport_recommended=true"));
    assert!(export_human_stdout.contains("entries: 3  candidates: 1"));
    assert!(export_human_stdout.contains("entries/procedure/wiki-project-entry.md"));
    assert!(!export_human_stdout.contains(secret));
    assert!(!human_vault_dir.exists());

    let export_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            vault_dir.to_str().expect("vault dir"),
            "--json",
        ])
        .output()?;
    assert!(export_output.status.success());
    let export: serde_json::Value = serde_json::from_slice(&export_output.stdout)?;
    assert_eq!(export["dry_run"], false);
    assert_eq!(
        export["summary"]["files_written"],
        export["summary"]["files_planned"]
    );
    let index = fs::read_to_string(vault_dir.join("index.md"))?;
    assert!(index.contains("wiki_project_entry"));
    assert!(index.contains("wiki_candidate_denial"));
    assert!(!index.contains(secret));
    let entry_page = fs::read_to_string(vault_dir.join("entries/procedure/wiki-project-entry.md"))?;
    assert!(entry_page.contains("Human project note"));
    assert!(entry_page.contains("[REDACTED]"));
    assert!(!entry_page.contains(secret));
    assert!(vault_dir.join("raw/audits").is_dir());

    let entries_before_review = fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let candidates_before_review =
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?;
    let review_dry_run_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run", "--json"])
        .output()?;
    assert!(review_dry_run_output.status.success());
    let review_dry_run: serde_json::Value = serde_json::from_slice(&review_dry_run_output.stdout)?;
    assert_eq!(review_dry_run["dry_run"], true);
    assert_eq!(review_dry_run["summary"]["files_written"], 0);
    assert!(
        review_dry_run["summary"]["proposals"]
            .as_u64()
            .expect("proposal count")
            >= 2
    );
    assert!(review_dry_run["proposals"]
        .as_array()
        .expect("review proposals")
        .iter()
        .all(|proposal| proposal["subject_id"]
            .as_str()
            .is_some_and(|id| !id.is_empty())
            && proposal["evidence_refs"]
                .as_array()
                .is_some_and(|refs| !refs.is_empty())));
    assert!(review_dry_run["proposals"]
        .as_array()
        .expect("review proposals")
        .iter()
        .any(|proposal| proposal["action"] == "promote"
            && proposal["subject_id"] == "wiki_candidate_denial"));
    assert!(!String::from_utf8_lossy(&review_dry_run_output.stdout).contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_review_reports").exists());
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before_review
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before_review
    );

    let review_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run"])
        .output()?;
    assert!(review_human_output.status.success());
    let review_human_stdout = String::from_utf8_lossy(&review_human_output.stdout);
    assert!(review_human_stdout.contains("Adaptive wiki review report planned"));
    assert!(review_human_stdout.contains("checked: 3 entries, 1 candidates"));
    assert!(review_human_stdout.contains("lifecycle:"));
    assert!(review_human_stdout.contains("promotion receipts:"));
    assert!(review_human_stdout.contains("wiki_candidate_denial"));
    assert!(!review_human_stdout.contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_review_reports").exists());

    let review_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--json"])
        .output()?;
    assert!(review_output.status.success());
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["dry_run"], false);
    assert_eq!(review["summary"]["files_written"], 2);
    let review_dir = PathBuf::from(review["report_dir"].as_str().expect("review report dir"));
    assert!(review_dir.join("report.json").is_file());
    assert!(review_dir.join("REPORT.md").is_file());
    let review_md = fs::read_to_string(review_dir.join("REPORT.md"))?;
    assert!(review_md.contains("Adaptive Wiki Review Report"));
    assert!(review_md.contains("wiki_candidate_denial"));
    assert!(!review_md.contains(secret));
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before_review
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before_review
    );
    Ok(())
}

#[test]
fn offdesk_wiki_read_only_dispatch_is_migration_free() -> Result<()> {
    let temp = tempdir()?;
    let read_only_commands: &[&[&str]] = &[
        &["offdesk", "wiki", "corrections", "--json"],
        &["offdesk", "wiki", "proposal-events", "--json"],
        &["offdesk", "wiki", "candidates", "--json"],
        &["offdesk", "wiki", "entries", "--json"],
        &["offdesk", "wiki", "projection", "--json"],
        &["offdesk", "wiki", "brief", "--json"],
        &["offdesk", "wiki", "runtime-policy-acks", "--json"],
        &["offdesk", "wiki", "runtime-policy-ack-report", "--json"],
        &["offdesk", "wiki", "review-after-report", "--json"],
        &["offdesk", "wiki", "lint", "--json"],
        &["offdesk", "wiki", "export-markdown", "--dry-run", "--json"],
        &["offdesk", "wiki", "graph", "--json"],
        &["offdesk", "wiki", "review", "--dry-run", "--json"],
    ];

    for args in read_only_commands {
        let output = forager_command(temp.path()).args(*args).output()?;
        assert!(
            output.status.success(),
            "read-only command failed: {args:?}\nstderr: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            !app_dir(temp.path()).exists(),
            "read-only command created app state: {args:?}"
        );
    }

    let failed_lookup = forager_command(temp.path())
        .args(["offdesk", "wiki", "show", "missing", "--json"])
        .output()?;
    assert!(!failed_lookup.status.success());
    assert!(!app_dir(temp.path()).exists());

    let dry_graph_dir = temp.path().join("dry-graph");
    let dry_graph_dir_text = dry_graph_dir.to_str().expect("UTF-8 graph path");
    let read_only_probes = [
        vec!["offdesk", "wiki", "proposal-handoff", "missing", "--json"],
        vec![
            "offdesk",
            "wiki",
            "proposal-receipt",
            "missing",
            "--audit-id",
            "missing-audit",
            "--event-id",
            "missing-event",
            "--command",
            "preview",
            "--json",
        ],
        vec![
            "offdesk",
            "wiki",
            "graph",
            "--output",
            dry_graph_dir_text,
            "--dry-run",
            "--json",
        ],
        vec![
            "offdesk",
            "wiki",
            "evaluate-episode",
            "missing",
            "--dry-run",
            "--json",
        ],
        vec!["offdesk", "wiki", "episode-trace", "--dry-run", "--json"],
        vec![
            "offdesk",
            "wiki",
            "evaluate-recurrence",
            "missing",
            "--dry-run",
            "--json",
        ],
        vec![
            "offdesk",
            "wiki",
            "promotion-chain",
            "missing",
            "--dry-run",
            "--json",
        ],
    ];
    for args in read_only_probes {
        let _ = forager_command(temp.path()).args(&args).output()?;
        assert!(
            !app_dir(temp.path()).exists(),
            "read-only probe created app state: {args:?}"
        );
    }
    assert!(!dry_graph_dir.exists());

    let mutating_temp = tempdir()?;
    let mutation = forager_command(mutating_temp.path())
        .args([
            "offdesk",
            "wiki",
            "record-candidate",
            "--kind",
            "procedure",
            "--scope",
            "user_global",
            "--claim",
            "Migration-bearing wiki mutation",
            "--json",
        ])
        .output()?;
    assert!(
        mutation.status.success(),
        "wiki mutation failed: {}",
        String::from_utf8_lossy(&mutation.stderr)
    );
    assert!(app_dir(mutating_temp.path())
        .join(".schema_version")
        .exists());
    assert!(profile_dir(mutating_temp.path())
        .join("adaptive_wiki_candidates.json")
        .exists());
    Ok(())
}

#[test]
fn offdesk_wiki_terminal_error_boundary_redacts_both_binaries_and_error_chains() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let selector = format!("wiki_review_missing-{secret}");

    for (binary, mut command) in [
        ("forager", forager_command(temp.path())),
        ("aoe", legacy_aoe_command(temp.path())),
    ] {
        let output = command
            .args(["offdesk", "wiki", "proposal-handoff", &selector, "--json"])
            .output()?;
        assert!(!output.status.success(), "{binary} unexpectedly succeeded");
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(stderr.contains(
            "Error: Adaptive wiki review proposal not found: wiki_review_missing-[REDACTED_API_KEY]"
        ));
        assert!(!stderr.contains(secret), "{binary} leaked: {stderr}");
    }
    assert!(!app_dir(temp.path()).exists());

    let unknown = format!("--unknown-{secret}");
    let parse_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "entries", &unknown])
        .output()?;
    assert_eq!(parse_output.status.code(), Some(2));
    let parse_stderr = String::from_utf8_lossy(&parse_output.stderr);
    assert!(parse_stderr.contains("unexpected argument"));
    assert!(parse_stderr.contains("[REDACTED_API_KEY]"));
    assert!(!parse_stderr.contains(secret));
    assert!(!app_dir(temp.path()).exists());

    let help_output = forager_command(temp.path()).arg("--help").output()?;
    assert!(help_output.status.success());
    assert!(String::from_utf8_lossy(&help_output.stdout).contains("Usage: forager"));
    assert!(help_output.stderr.is_empty());
    assert!(!app_dir(temp.path()).exists());

    let path_temp = tempdir()?;
    let blocked_parent = path_temp.path().join(format!("blocked-{secret}"));
    fs::write(&blocked_parent, b"sentinel")?;
    let output_path = blocked_parent.join("receipt.json");
    let path_output = forager_command(path_temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            "missing",
            "--audit-id",
            "missing-audit",
            "--event-id",
            "missing-event",
            "--command",
            "preview",
            "--output",
            output_path.to_str().expect("UTF-8 output path"),
        ])
        .output()?;
    assert!(!path_output.status.success());
    let path_stderr = String::from_utf8_lossy(&path_output.stderr);
    assert!(path_stderr.contains("create adaptive wiki proposal receipt export directory"));
    assert!(path_stderr.contains("blocked-[REDACTED_API_KEY]"));
    assert!(!path_stderr.contains(secret));
    assert_eq!(fs::read(&blocked_parent)?, b"sentinel");
    assert!(!output_path.exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_export_markdown_defaults_to_profile_vault_and_reports_stale_state() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_export_default",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "context_only",
                    "claim": "Default vault export is inspectable",
                    "ai_instruction": "Use the default vault for human projection checks.",
                    "human_summary": "Default vault export note",
                    "evidence_refs": ["task:export"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
        }))?,
    )?;

    let dry_run_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "export-markdown", "--dry-run", "--json"])
        .output()?;
    assert!(
        dry_run_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&dry_run_output.stdout),
        String::from_utf8_lossy(&dry_run_output.stderr)
    );
    let dry_run: serde_json::Value = serde_json::from_slice(&dry_run_output.stdout)?;
    assert_eq!(
        reported_path(&dry_run["output_dir"]),
        expected_path(&profile_dir.join("wiki-vault"))
    );
    assert_eq!(dry_run["projection_status"]["state"], "missing");
    assert_eq!(dry_run["projection_status"]["reexport_recommended"], true);
    assert!(!profile_dir.join("wiki-vault/index.md").exists());

    let export_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "export-markdown", "--json"])
        .output()?;
    assert!(
        export_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&export_output.stdout),
        String::from_utf8_lossy(&export_output.stderr)
    );
    let export: serde_json::Value = serde_json::from_slice(&export_output.stdout)?;
    assert_eq!(export["projection_status"]["state"], "fresh");
    assert_eq!(export["projection_status"]["reexport_recommended"], false);
    assert!(profile_dir.join("wiki-vault/index.md").exists());

    let relative_output = forager_command(temp.path())
        .current_dir(temp.path())
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            "relative-vault",
            "--json",
        ])
        .output()?;
    assert!(
        relative_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&relative_output.stdout),
        String::from_utf8_lossy(&relative_output.stderr)
    );
    assert!(temp.path().join("relative-vault/index.md").is_file());

    let relative_work_dir = temp.path().join("relative-work");
    fs::create_dir(&relative_work_dir)?;
    let parent_relative_output = forager_command(temp.path())
        .current_dir(&relative_work_dir)
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            "../sibling-vault",
            "--json",
        ])
        .output()?;
    assert!(
        parent_relative_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&parent_relative_output.stdout),
        String::from_utf8_lossy(&parent_relative_output.stderr)
    );
    assert!(temp.path().join("sibling-vault/index.md").is_file());

    thread::sleep(StdDuration::from_millis(1100));
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": []
        }))?,
    )?;

    let stale_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "export-markdown", "--dry-run", "--json"])
        .output()?;
    assert!(
        stale_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&stale_output.stdout),
        String::from_utf8_lossy(&stale_output.stderr)
    );
    let stale: serde_json::Value = serde_json::from_slice(&stale_output.stdout)?;
    assert_eq!(stale["projection_status"]["state"], "stale");
    assert_eq!(stale["projection_status"]["reexport_recommended"], true);
    assert_eq!(stale["summary"]["files_written"], 0);
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_episode_trace_links_task_usage_candidate_and_audit_evidence() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_trace_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Trace entry",
                    "ai_instruction": "Use trace entry.",
                    "human_summary": "Trace summary",
                    "evidence_refs": ["task:task_episode"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [
                {
                    "id": "wiki_trace_candidate",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "claim": "Operator corrected trace behavior",
                    "suggested_ai_instruction": "Remember the correction.",
                    "human_summary": "Correction summary",
                    "evidence_refs": ["task:task_episode", "request:request_episode"],
                    "signal_kind": "operator_correction",
                    "origin": "operator_explicit",
                    "source_refs": [format!("approval:approval_episode?token={secret}")],
                    "source_hashes": ["sha256:abc"],
                    "review_reason": "Trace review",
                    "occurrence_count": 2,
                    "confidence": "repeated",
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_usage.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_usage_episode",
                "entry_id": "wiki_trace_entry",
                "task_id": "task_episode",
                "request_id": "request_episode",
                "project_key": "project",
                "artifact_kind": "report",
                "projection_kind": "runtime_probe",
                "activation_mode": "confirm",
                "created_at": now
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_corrections.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_correction_episode",
                "correction_kind": "operator_correction",
                "candidate_id": "wiki_trace_candidate",
                "entry_id": "wiki_trace_entry",
                "task_id": "task_episode",
                "request_id": "request_episode",
                "project_key": "project",
                "artifact_kind": "report",
                "summary": "First-class correction summary",
                "evidence_refs": [],
                "source_refs": [],
                "created_at": now
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_audit.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_audit_episode",
                "action": "promote",
                "subject_id": "wiki_trace_entry",
                "candidate_id": "wiki_trace_candidate",
                "entry_id": "wiki_trace_entry",
                "actor": "cli",
                "reason": "Trace promotion",
                "evidence_ref": "request:request_episode",
                "created_at": now
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task_episode",
                "request_id": "request_episode",
                "project_key": "project",
                "status": "completed",
                "capability_id": "capability.trace",
                "runner_kind": "local_background",
                "command": format!("echo token={secret}"),
                "workdir": "/tmp",
                "background_ticket_id": "ticket_episode",
                "attempt_count": 1,
                "created_at": now,
                "updated_at": now,
                "artifact_kind": "report",
                "last_adaptive_wiki_entry_ids": ["wiki_trace_entry"],
                "preview": "Trace preview",
                "reason": "Trace reason"
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket_episode",
                "capability_id": "capability.trace",
                "project_key": "project",
                "request_id": "request_episode",
                "task_id": "task_episode",
                "runner_kind": "local_background",
                "phase": "completed",
                "runtime_handle_alive": false,
                "last_observed_at": now,
                "last_recovery_evidence": "result artifact present",
                "adaptive_wiki_entry_ids": ["wiki_trace_entry"],
                "adaptive_wiki_context": "context token=sk-secretsecretsecretsecret"
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([
            {
                "resume_id": "resume_episode",
                "task_id": "task_episode",
                "request_id": "request_episode",
                "project_key": "project",
                "status": "resume_pending",
                "phase": "background_probe",
                "runner_target": "ticket_episode",
                "last_evidence_artifacts": ["log:episode"],
                "next_safe_resume_step": "inspect result",
                "interrupted_at": now,
                "interruption_reason": "test resume"
            }
        ]))?,
    )?;

    let entries_before = fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let candidates_before = fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?;
    let dry_run_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "episode-trace",
            "--request-id",
            "request_episode",
            "--project-key",
            "project",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(dry_run_output.status.success());
    let dry_run: serde_json::Value = serde_json::from_slice(&dry_run_output.stdout)?;
    assert_eq!(dry_run["dry_run"], true);
    assert_eq!(dry_run["summary"]["files_written"], 0);
    assert!(dry_run["summary"]["events"].as_u64().expect("event count") >= 8);
    assert_eq!(dry_run["summary"]["runtime_usage_events"], 1);
    assert_eq!(dry_run["summary"]["candidate_events"], 1);
    assert_eq!(dry_run["summary"]["correction_events"], 1);
    assert_eq!(dry_run["summary"]["promotion_events"], 1);
    assert!(dry_run["events"]
        .as_array()
        .expect("trace events")
        .iter()
        .any(|event| event["kind"] == "runtime_usage_recorded"
            && event["entry_ids"][0] == "wiki_trace_entry"));
    assert!(dry_run["events"]
        .as_array()
        .expect("trace events")
        .iter()
        .any(|event| event["kind"] == "operator_correction_observed"
            && event["candidate_id"] == "wiki_trace_candidate"
            && event["summary"]
                .as_str()
                .expect("correction summary")
                .contains("correction kind=operator_correction")));
    assert!(!String::from_utf8_lossy(&dry_run_output.stdout).contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_episode_traces").exists());
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "episode-trace",
            "--request-id",
            "request_episode",
            "--project-key",
            "project",
            "--dry-run",
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Adaptive wiki live episode trace planned"));
    assert!(human_stdout.contains("runtime usage: 1"));
    assert!(human_stdout.contains("wiki_trace_entry"));
    assert!(!human_stdout.contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_episode_traces").exists());

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "episode-trace",
            "--request-id",
            "request_episode",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["dry_run"], false);
    assert_eq!(report["summary"]["files_written"], 3);
    let report_dir = PathBuf::from(report["report_dir"].as_str().expect("trace report dir"));
    assert!(report_dir.join("report.json").is_file());
    assert!(report_dir.join("trace.jsonl").is_file());
    assert!(report_dir.join("REPORT.md").is_file());
    let trace_jsonl = fs::read_to_string(report_dir.join("trace.jsonl"))?;
    assert!(trace_jsonl.contains("runtime_usage_recorded"));
    assert!(trace_jsonl.contains("entry_promoted"));
    assert!(!trace_jsonl.contains(secret));
    let report_md = fs::read_to_string(report_dir.join("REPORT.md"))?;
    assert!(report_md.contains("Adaptive Wiki Live Episode Trace"));
    assert!(report_md.contains("wiki_trace_entry"));
    assert!(!report_md.contains(secret));
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_evaluate_recurrence_counts_pre_and_post_promotion_corrections() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let promotion_at = Utc::now();
    let before = promotion_at - Duration::hours(2);
    let after = promotion_at + Duration::hours(2);
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_recur_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Avoid repeated correction",
                    "ai_instruction": "Use the promoted correction.",
                    "human_summary": "Recurrence target",
                    "evidence_refs": ["task:task_before"],
                    "confidence": "repeated",
                    "created_at": promotion_at,
                    "updated_at": promotion_at
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [
                {
                    "id": "wiki_recur_before",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "claim": "Before promotion correction",
                    "suggested_ai_instruction": "Before correction.",
                    "human_summary": "Before correction",
                    "evidence_refs": ["task:task_before"],
                    "signal_kind": "operator_correction",
                    "origin": "operator_explicit",
                    "source_refs": ["request:request_before"],
                    "source_hashes": ["sha256:before"],
                    "review_reason": "Before recurrence baseline",
                    "occurrence_count": 1,
                    "confidence": "explicit",
                    "created_at": before,
                    "updated_at": before,
                    "last_seen_at": before
                },
                {
                    "id": "wiki_recur_after",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "claim": "After promotion correction",
                    "suggested_ai_instruction": "After correction.",
                    "human_summary": "After correction",
                    "evidence_refs": ["task:task_after"],
                    "signal_kind": "operator_correction",
                    "origin": "operator_explicit",
                    "source_refs": [format!("request:request_after?token={secret}")],
                    "source_hashes": ["sha256:after"],
                    "review_reason": "After recurrence observed",
                    "occurrence_count": 1,
                    "confidence": "explicit",
                    "created_at": after,
                    "updated_at": after,
                    "last_seen_at": after
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_usage.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_usage_after",
                "entry_id": "wiki_recur_entry",
                "task_id": "task_after",
                "request_id": "request_after",
                "project_key": "project",
                "artifact_kind": "report",
                "projection_kind": "runtime_probe",
                "activation_mode": "confirm",
                "created_at": after
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_corrections.jsonl"),
        format!(
            "{}\n{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_corr_before",
                "correction_kind": "operator_correction",
                "candidate_id": "wiki_recur_before",
                "entry_id": "wiki_recur_entry",
                "task_id": "task_before",
                "request_id": "request_before",
                "project_key": "project",
                "artifact_kind": "report",
                "summary": "Before correction record",
                "evidence_refs": ["task:task_before"],
                "source_refs": ["request:request_before"],
                "created_at": before
            }))?,
            serde_json::to_string(&json!({
                "id": "wiki_corr_after",
                "correction_kind": "operator_correction",
                "candidate_id": "wiki_recur_after",
                "entry_id": "wiki_recur_entry",
                "task_id": "task_after",
                "request_id": "request_after",
                "project_key": "project",
                "artifact_kind": "report",
                "summary": format!("After correction record token={secret}"),
                "evidence_refs": ["task:task_after"],
                "source_refs": [format!("request:request_after?token={secret}")],
                "created_at": after
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_audit.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_audit_promote_recur",
                "action": "promote",
                "subject_id": "wiki_recur_entry",
                "candidate_id": "wiki_recur_before",
                "entry_id": "wiki_recur_entry",
                "actor": "cli",
                "reason": "Promotion boundary",
                "evidence_ref": "task:task_before",
                "created_at": promotion_at
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task_after",
                "request_id": "request_after",
                "project_key": "project",
                "status": "completed",
                "capability_id": "capability.recur",
                "runner_kind": "local_background",
                "command": format!("echo token={secret}"),
                "workdir": "/tmp",
                "attempt_count": 1,
                "created_at": after,
                "updated_at": after,
                "artifact_kind": "report",
                "last_adaptive_wiki_entry_ids": ["wiki_recur_entry"],
                "preview": "Recurrence preview",
                "reason": "Recurrence reason"
            }
        ]))?,
    )?;

    let entries_before = fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let candidates_before = fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?;
    let dry_run_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-recurrence",
            "wiki_recur_entry",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(dry_run_output.status.success());
    let dry_run: serde_json::Value = serde_json::from_slice(&dry_run_output.stdout)?;
    assert_eq!(dry_run["dry_run"], true);
    assert_eq!(dry_run["assessment"], "recurrence_observed");
    assert_eq!(dry_run["summary"]["correction_records_checked"], 2);
    assert_eq!(dry_run["summary"]["pre_promotion_correction_events"], 1);
    assert_eq!(dry_run["summary"]["post_promotion_correction_events"], 1);
    assert_eq!(dry_run["summary"]["post_promotion_usage_events"], 1);
    assert_eq!(
        dry_run["summary"]["post_promotion_recurrence_per_1000"],
        1000
    );
    assert_eq!(dry_run["summary"]["files_written"], 0);
    assert!(!String::from_utf8_lossy(&dry_run_output.stdout).contains(secret));
    assert!(!profile_dir
        .join("adaptive_wiki_recurrence_reports")
        .exists());
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-recurrence",
            "wiki_recur_entry",
            "--dry-run",
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Adaptive wiki correction recurrence planned"));
    assert!(human_stdout.contains("entry: wiki_recur_entry"));
    assert!(human_stdout.contains("corrections: pre=1 post=1 delta=0"));
    assert!(human_stdout.contains("post usage=1"));
    assert!(!human_stdout.contains(secret));
    assert!(!profile_dir
        .join("adaptive_wiki_recurrence_reports")
        .exists());

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-recurrence",
            "wiki_recur_entry",
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["dry_run"], false);
    assert_eq!(report["summary"]["files_written"], 3);
    let report_dir = PathBuf::from(
        report["report_dir"]
            .as_str()
            .expect("recurrence report dir"),
    );
    assert!(report_dir.join("report.json").is_file());
    assert!(report_dir.join("recurrence.jsonl").is_file());
    assert!(report_dir.join("REPORT.md").is_file());
    let recurrence_jsonl = fs::read_to_string(report_dir.join("recurrence.jsonl"))?;
    assert!(recurrence_jsonl.contains("operator_correction_observed"));
    assert!(!recurrence_jsonl.contains(secret));
    let report_md = fs::read_to_string(report_dir.join("REPORT.md"))?;
    assert!(report_md.contains("Adaptive Wiki Correction Recurrence"));
    assert!(report_md.contains("wiki_recur_entry"));
    assert!(!report_md.contains(secret));
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_corrections_json_and_debug_bundle_redact_records() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_corrections.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_corr_cli",
                "correction_kind": "operator_correction",
                "candidate_id": "wiki_candidate_cli",
                "entry_id": "wiki_entry_cli",
                "task_id": "task_cli",
                "request_id": "request_cli",
                "project_key": "project",
                "artifact_kind": "report",
                "summary": format!("CLI correction token={secret}"),
                "evidence_refs": [format!("task:task_cli?token={secret}")],
                "source_refs": [format!("request:request_cli?token={secret}")],
                "created_at": now
            }))?
        ),
    )?;

    let corrections_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "corrections", "--json"])
        .output()?;
    assert!(corrections_output.status.success());
    let corrections_stdout = String::from_utf8_lossy(&corrections_output.stdout);
    assert!(!corrections_stdout.contains(secret));
    let corrections: serde_json::Value = serde_json::from_slice(&corrections_output.stdout)?;
    assert_eq!(corrections.as_array().expect("correction array").len(), 1);
    assert_eq!(corrections[0]["id"], "wiki_corr_cli");
    assert_eq!(corrections[0]["correction_kind"], "operator_correction");
    assert!(corrections[0]["summary"]
        .as_str()
        .expect("summary")
        .contains("[REDACTED]"));

    let corrections_human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "corrections"])
        .output()?;
    assert!(corrections_human_output.status.success());
    let corrections_human_stdout = String::from_utf8_lossy(&corrections_human_output.stdout);
    assert!(corrections_human_stdout.contains("wiki_corr_cli OperatorCorrection"));
    assert!(corrections_human_stdout.contains("task=task_cli"));
    assert!(corrections_human_stdout.contains("request=request_cli"));
    assert!(corrections_human_stdout.contains("entry=wiki_entry_cli"));
    assert!(corrections_human_stdout.contains("[REDACTED]"));
    assert!(!corrections_human_stdout.contains(secret));

    let bundle_output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--json"])
        .output()?;
    assert!(bundle_output.status.success());
    let bundle_stdout = String::from_utf8_lossy(&bundle_output.stdout);
    assert!(!bundle_stdout.contains(secret));
    let bundle: serde_json::Value = serde_json::from_slice(&bundle_output.stdout)?;
    assert_eq!(
        bundle["adaptive_wiki_corrections"]
            .as_array()
            .expect("bundle corrections")
            .len(),
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_corrections"][0]["id"],
        "wiki_corr_cli"
    );

    let stored = fs::read_to_string(profile_dir.join("adaptive_wiki_corrections.jsonl"))?;
    assert!(stored.contains(secret));
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_proposal_events_record_list_and_debug_bundle_are_redacted() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let proposal_id = "wiki_review_promote_candidate_wiki-candidate";
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": "wiki_candidate",
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Repeated operator correction needs a durable wiki entry",
                "suggested_ai_instruction": "Check the project wiki before applying repeated corrections.",
                "human_summary": "Repeated correction candidate.",
                "evidence_refs": ["task:wiki_candidate"],
                "signal_kind": "operator_correction",
                "origin": "runtime_observed",
                "occurrence_count": 2,
                "confidence": "repeated",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;

    let record_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "record-proposal-event",
            proposal_id,
            "--decision",
            "accepted",
            "--proposal-action",
            "promote",
            "--subject-kind",
            "candidate",
            "--subject-id",
            "wiki_candidate",
            "--reason",
            &format!("operator accepted token={secret}"),
            "--evidence-ref",
            &format!("review:report?token={secret}"),
            "--supersedes",
            "old_proposal",
            "--json",
        ])
        .output()?;
    assert!(record_output.status.success());
    assert!(!String::from_utf8_lossy(&record_output.stdout).contains(secret));
    let record: serde_json::Value = serde_json::from_slice(&record_output.stdout)?;
    assert_eq!(record["proposal_id"], proposal_id);
    assert_eq!(record["decision"], "accepted");
    assert_eq!(record["proposal_action"], "promote");
    assert_eq!(record["subject_kind"], "candidate");
    assert_eq!(record["subject_id"], "wiki_candidate");
    assert!(record["reason"]
        .as_str()
        .expect("event reason")
        .contains("[REDACTED]"));

    let list_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-events",
            "--proposal-id",
            proposal_id,
            "--json",
        ])
        .output()?;
    assert!(list_output.status.success());
    assert!(!String::from_utf8_lossy(&list_output.stdout).contains(secret));
    let events: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    assert_eq!(events.as_array().expect("proposal events").len(), 1);
    assert_eq!(events[0]["proposal_id"], proposal_id);

    let list_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-events",
            "--proposal-id",
            proposal_id,
        ])
        .output()?;
    assert!(list_human_output.status.success());
    let list_human_stdout = String::from_utf8_lossy(&list_human_output.stdout);
    assert!(list_human_stdout.contains("Accepted"));
    assert!(list_human_stdout.contains(&format!("proposal={proposal_id}")));
    assert!(list_human_stdout.contains("action=Promote"));
    assert!(list_human_stdout.contains("subject=candidate:wiki_candidate"));
    assert!(list_human_stdout.contains("by=cli"));
    assert!(list_human_stdout.contains("[REDACTED]"));
    assert!(!list_human_stdout.contains(secret));

    let review_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run", "--json"])
        .output()?;
    assert!(review_output.status.success());
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["summary"]["review_events_checked"], 1);
    assert_eq!(review["summary"]["proposals_with_events"], 1);
    assert_eq!(review["summary"]["open_proposals"], 0);
    assert_eq!(review["summary"]["accepted_proposals"], 1);
    assert_eq!(review["summary"]["filtered_out_proposals"], 0);
    assert_eq!(review["summary"]["files_written"], 0);
    let proposals = review["proposals"].as_array().expect("review proposals");
    let proposal = proposals
        .iter()
        .find(|proposal| proposal["id"] == proposal_id)
        .expect("proposal with lifecycle");
    assert_eq!(proposal["lifecycle"]["decision"], "accepted");
    assert_eq!(proposal["lifecycle"]["actor"], "cli");
    assert!(proposal["lifecycle"]["reason"]
        .as_str()
        .expect("lifecycle reason")
        .contains("[REDACTED]"));

    let active_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review",
            "--dry-run",
            "--active-only",
            "--json",
        ])
        .output()?;
    assert!(active_output.status.success());
    let active: serde_json::Value = serde_json::from_slice(&active_output.stdout)?;
    assert_eq!(
        active["proposals"]
            .as_array()
            .expect("active proposals")
            .len(),
        0
    );
    assert_eq!(active["summary"]["filtered_out_proposals"], 1);

    let decided_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review",
            "--dry-run",
            "--decided-only",
            "--json",
        ])
        .output()?;
    assert!(decided_output.status.success());
    let decided: serde_json::Value = serde_json::from_slice(&decided_output.stdout)?;
    assert_eq!(
        decided["proposals"]
            .as_array()
            .expect("decided proposals")
            .len(),
        1
    );
    assert_eq!(decided["summary"]["accepted_proposals"], 1);
    assert_eq!(decided["summary"]["filtered_out_proposals"], 0);

    let stale_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review",
            "--dry-run",
            "--stale-only",
            "--json",
        ])
        .output()?;
    assert!(stale_output.status.success());
    let stale: serde_json::Value = serde_json::from_slice(&stale_output.stdout)?;
    assert_eq!(
        stale["proposals"]
            .as_array()
            .expect("stale proposals")
            .len(),
        0
    );
    assert_eq!(stale["summary"]["filtered_out_proposals"], 1);

    let invalid_filter_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review",
            "--dry-run",
            "--active-only",
            "--decided-only",
        ])
        .output()?;
    assert!(!invalid_filter_output.status.success());
    assert!(String::from_utf8_lossy(&invalid_filter_output.stderr)
        .contains("choose only one of --active-only, --decided-only, or --stale-only"));

    let bundle_output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--json"])
        .output()?;
    assert!(bundle_output.status.success());
    assert!(!String::from_utf8_lossy(&bundle_output.stdout).contains(secret));
    let bundle: serde_json::Value = serde_json::from_slice(&bundle_output.stdout)?;
    assert_eq!(
        bundle["adaptive_wiki_review_events"]
            .as_array()
            .expect("bundle review events")
            .len(),
        1
    );

    let stored = fs::read_to_string(profile_dir.join("adaptive_wiki_review_events.jsonl"))?;
    assert!(!stored.contains(secret));
    assert!(stored.contains("[REDACTED]"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_proposal_closure_helpers_copy_metadata_and_block_duplicates() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let proposal_id = "wiki_review_promote_candidate_wiki-candidate";
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": "wiki_candidate",
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Repeated operator correction needs a durable wiki entry",
                "suggested_ai_instruction": "Check the project wiki before applying repeated corrections.",
                "human_summary": "Repeated correction candidate.",
                "evidence_refs": [format!("task:wiki_candidate?token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "runtime_observed",
                "occurrence_count": 2,
                "confidence": "repeated",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;

    let accept_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "accept-proposal",
            proposal_id,
            "--reason",
            &format!("accepted proposal token={secret}"),
            "--evidence-ref",
            &format!("review:manual?token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(accept_output.status.success());
    let accept_stdout = String::from_utf8_lossy(&accept_output.stdout);
    assert!(!accept_stdout.contains(secret));
    let accepted: serde_json::Value = serde_json::from_slice(&accept_output.stdout)?;
    assert_eq!(accepted["proposal_id"], proposal_id);
    assert_eq!(accepted["decision"], "accepted");
    assert_eq!(accepted["proposal_action"], "promote");
    assert_eq!(accepted["subject_kind"], "candidate");
    assert_eq!(accepted["subject_id"], "wiki_candidate");
    let evidence_refs = accepted["evidence_refs"]
        .as_array()
        .expect("accepted evidence refs");
    assert!(evidence_refs
        .iter()
        .any(|value| value.as_str().expect("evidence ref") == "lint:promotion_candidate"));
    assert!(evidence_refs
        .iter()
        .any(|value| value.as_str().expect("evidence ref").contains("[REDACTED]")));

    let duplicate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "reject-proposal",
            proposal_id,
            "--reason",
            "second decision should require explicit override",
        ])
        .output()?;
    assert!(!duplicate_output.status.success());
    assert!(String::from_utf8_lossy(&duplicate_output.stderr)
        .contains("already has a non-stale lifecycle decision"));

    let supersede_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "supersede-proposal",
            proposal_id,
            "--reason",
            "superseded by operator follow-up",
            "--supersedes",
            "wiki_review_old",
            "--allow-decided",
            "--json",
        ])
        .output()?;
    assert!(supersede_output.status.success());
    let superseded: serde_json::Value = serde_json::from_slice(&supersede_output.stdout)?;
    assert_eq!(superseded["decision"], "superseded");
    assert_eq!(superseded["proposal_action"], "promote");
    assert_eq!(superseded["supersedes"], "wiki_review_old");

    let reject_human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "reject-proposal",
            proposal_id,
            "--reason",
            &format!("rejected by follow-up token={secret}"),
            "--allow-decided",
        ])
        .output()?;
    assert!(reject_human_output.status.success());
    let reject_human_stdout = String::from_utf8_lossy(&reject_human_output.stdout);
    assert!(reject_human_stdout.contains("Recorded adaptive wiki proposal event"));
    assert!(reject_human_stdout.contains(&format!("for {proposal_id} (Rejected)")));
    assert!(!reject_human_stdout.contains(secret));

    let stored = fs::read_to_string(profile_dir.join("adaptive_wiki_review_events.jsonl"))?;
    assert!(!stored.contains(secret));
    assert!(stored.contains("[REDACTED]"));
    assert!(stored.contains("\"accepted\""));
    assert!(stored.contains("\"superseded\""));
    assert!(stored.contains("\"rejected\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_proposal_handoff_previews_ready_manual_and_blocked() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let promote_proposal_id = "wiki_review_promote_candidate_wiki-candidate";
    let renew_proposal_id = "wiki_review_renew_review_entry_wiki-entry";
    let conflict_proposal_id = "wiki_review_split_entry_allow-tables";
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": "wiki_candidate",
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Repeated operator correction needs a durable wiki entry",
                "suggested_ai_instruction": "Check the project wiki before applying repeated corrections.",
                "human_summary": "Repeated correction candidate.",
                "evidence_refs": [format!("task:wiki_candidate?token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "runtime_observed",
                "occurrence_count": 2,
                "confidence": "repeated",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": "wiki_entry",
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Review expired entries before projection.",
                "ai_instruction": "Check review windows before relying on this entry.",
                "human_summary": "Expired review entry.",
                "evidence_refs": ["audit:wiki_entry"],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now,
                "review_after": now - Duration::days(1)
            }, {
                "id": "allow_tables",
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Use markdown tables for report evidence",
                "ai_instruction": "Use markdown tables for report evidence",
                "human_summary": "Tables are preferred for report evidence.",
                "evidence_refs": ["audit:allow_tables"],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }, {
                "id": "block_tables",
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Do not use markdown tables for report evidence",
                "ai_instruction": "Do not use markdown tables for report evidence",
                "human_summary": "Tables are blocked for report evidence.",
                "evidence_refs": ["audit:block_tables"],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }]
        }))?,
    )?;

    let ready_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            promote_proposal_id,
            "--json",
        ])
        .output()?;
    assert!(ready_output.status.success());
    let ready_stdout = String::from_utf8_lossy(&ready_output.stdout);
    assert!(!ready_stdout.contains(secret));
    let ready: serde_json::Value = serde_json::from_slice(&ready_output.stdout)?;
    assert_eq!(ready["status"], "ready");
    assert_eq!(ready["action"], "promote");
    assert!(ready["command"]
        .as_str()
        .expect("ready command")
        .contains("forager offdesk wiki promote wiki_candidate"));
    assert!(ready["evidence_refs"]
        .as_array()
        .expect("ready evidence refs")
        .iter()
        .any(|value| {
            value
                .as_str()
                .expect("evidence ref")
                .contains("redacted_id_sha256_")
        }));
    assert!(ready["required_inputs"]
        .as_array()
        .expect("ready required inputs")
        .is_empty());
    assert!(ready["mutation_options"]
        .as_array()
        .expect("ready mutation options")
        .is_empty());
    assert!(!profile_dir
        .join("adaptive_wiki_review_events.jsonl")
        .exists());

    let manual_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            renew_proposal_id,
            "--json",
        ])
        .output()?;
    assert!(manual_output.status.success());
    let manual: serde_json::Value = serde_json::from_slice(&manual_output.stdout)?;
    assert_eq!(manual["status"], "manual_required");
    assert!(manual["command"].is_null());
    assert!(manual["reason"]
        .as_str()
        .expect("manual reason")
        .contains("Renew-review proposals require"));
    let manual_inputs = manual["required_inputs"]
        .as_array()
        .expect("manual required inputs");
    assert!(manual_inputs.iter().any(|input| {
        input["name"] == "mutation" && input["required"].as_bool().expect("required bool")
    }));
    assert!(manual_inputs
        .iter()
        .any(|input| { input["name"] == "scope" && input["cli_flag"] == "--scope" }));
    assert!(manual_inputs
        .iter()
        .any(|input| { input["name"] == "evidence_ref" && input["cli_flag"] == "--evidence-ref" }));
    let manual_options = manual["mutation_options"]
        .as_array()
        .expect("manual mutation options");
    assert!(manual_options.iter().any(|option| {
        option["mutation"] == "rescope"
            && option["command_template"]
                .as_str()
                .expect("rescope template")
                .contains("forager offdesk wiki rescope")
    }));
    assert!(manual_options
        .iter()
        .any(|option| option["mutation"] == "deprecate"));
    assert!(manual_options
        .iter()
        .any(|option| option["mutation"] == "add_counterexample"));

    let human_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "proposal-handoff", renew_proposal_id])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Adaptive wiki proposal handoff"));
    assert!(human_stdout.contains("manual_required"));
    assert!(human_stdout.contains("required inputs:"));
    assert!(human_stdout.contains("mutation options:"));
    assert!(human_stdout.contains("rescope: forager offdesk wiki rescope"));

    let conflict_manual_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            conflict_proposal_id,
            "--json",
        ])
        .output()?;
    assert!(conflict_manual_output.status.success());
    let conflict_manual: serde_json::Value =
        serde_json::from_slice(&conflict_manual_output.stdout)?;
    assert_eq!(conflict_manual["status"], "manual_required");
    assert!(conflict_manual["reason"]
        .as_str()
        .expect("conflict manual reason")
        .contains("Projection-conflict proposals require"));
    assert!(conflict_manual["evidence_refs"]
        .as_array()
        .expect("conflict evidence refs")
        .iter()
        .any(|value| value == "entry:block_tables"));
    assert!(conflict_manual["evidence_refs"]
        .as_array()
        .expect("conflict evidence refs")
        .iter()
        .any(|value| value == "projection:markdown tables for report evidence"));
    let conflict_options = conflict_manual["mutation_options"]
        .as_array()
        .expect("conflict mutation options");
    for mutation in ["rescope", "deprecate", "split", "add_counterexample"] {
        assert!(conflict_options
            .iter()
            .any(|option| option["mutation"] == mutation));
    }
    let conflict_inputs = conflict_manual["required_inputs"]
        .as_array()
        .expect("conflict required inputs");
    assert!(conflict_inputs.iter().any(|input| {
        input["name"] == "deprecated_entry_id" && input["cli_flag"] == "--deprecated-entry-id"
    }));

    let conflict_deprecate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            conflict_proposal_id,
            "--mutation",
            "deprecate",
            "--deprecated-entry-id",
            "block_tables",
            "--reason",
            "resolve conflicting table policy",
            "--json",
        ])
        .output()?;
    assert!(conflict_deprecate_output.status.success());
    let conflict_deprecate: serde_json::Value =
        serde_json::from_slice(&conflict_deprecate_output.stdout)?;
    assert_eq!(conflict_deprecate["status"], "ready");
    assert!(conflict_deprecate["command"]
        .as_str()
        .expect("conflict deprecate command")
        .contains("forager offdesk wiki deprecate 'block_tables'"));

    let conflict_split_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            conflict_proposal_id,
            "--mutation",
            "split",
            "--json",
        ])
        .output()?;
    assert!(conflict_split_output.status.success());
    let conflict_split: serde_json::Value = serde_json::from_slice(&conflict_split_output.stdout)?;
    assert_eq!(conflict_split["status"], "manual_required");
    assert!(conflict_split["reason"]
        .as_str()
        .expect("conflict split reason")
        .contains("governed mutations"));

    let rescope_ready_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            renew_proposal_id,
            "--mutation",
            "rescope",
            "--scope",
            "artifact_kind",
            "--scope-ref",
            "runbook",
            "--reason",
            "narrow expired review entry",
            "--json",
        ])
        .output()?;
    assert!(rescope_ready_output.status.success());
    let rescope_ready: serde_json::Value = serde_json::from_slice(&rescope_ready_output.stdout)?;
    assert_eq!(rescope_ready["status"], "ready");
    assert_eq!(
        rescope_ready["required_inputs"].as_array().unwrap().len(),
        0
    );
    let rescope_command = rescope_ready["command"]
        .as_str()
        .expect("parameterized rescope command");
    assert!(rescope_command.contains("forager offdesk wiki rescope"));
    assert!(rescope_command.contains("--scope artifact_kind"));
    assert!(rescope_command.contains("--scope-ref 'runbook'"));
    assert!(rescope_command.contains("--reason 'narrow expired review entry'"));
    assert!(!profile_dir
        .join("adaptive_wiki_review_events.jsonl")
        .exists());

    let counterexample_ready_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            renew_proposal_id,
            "--mutation",
            "add-counterexample",
            "--evidence-ref",
            &format!("task:review?token={secret}"),
            "--reason",
            "attach limiting review evidence",
            "--json",
        ])
        .output()?;
    assert!(counterexample_ready_output.status.success());
    let counterexample_stdout = String::from_utf8_lossy(&counterexample_ready_output.stdout);
    assert!(!counterexample_stdout.contains(secret));
    let counterexample_ready: serde_json::Value =
        serde_json::from_slice(&counterexample_ready_output.stdout)?;
    assert_eq!(counterexample_ready["status"], "ready");
    assert!(counterexample_ready["command"]
        .as_str()
        .expect("counterexample command")
        .contains("forager offdesk wiki add-counterexample"));
    assert!(counterexample_ready["command"]
        .as_str()
        .expect("counterexample command")
        .contains("[REDACTED]"));

    let missing_rescope_input_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            renew_proposal_id,
            "--mutation",
            "rescope",
            "--scope",
            "project",
            "--json",
        ])
        .output()?;
    assert!(missing_rescope_input_output.status.success());
    let missing_rescope_input: serde_json::Value =
        serde_json::from_slice(&missing_rescope_input_output.stdout)?;
    assert_eq!(missing_rescope_input["status"], "manual_required");
    assert!(missing_rescope_input["reason"]
        .as_str()
        .expect("missing rescope reason")
        .contains("--scope-ref"));

    let accept_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "accept-proposal",
            promote_proposal_id,
            "--reason",
            "accepted before handoff preview",
        ])
        .output()?;
    assert!(accept_output.status.success());
    let blocked_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            promote_proposal_id,
            "--json",
        ])
        .output()?;
    assert!(blocked_output.status.success());
    let blocked: serde_json::Value = serde_json::from_slice(&blocked_output.stdout)?;
    assert_eq!(blocked["status"], "blocked_by_decision");
    assert!(blocked["command"].is_null());
    assert!(blocked["required_inputs"]
        .as_array()
        .expect("blocked required inputs")
        .is_empty());
    assert_eq!(blocked["lifecycle_decision"], "accepted");
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_proposal_receipt_links_preview_audit_and_event_without_mutation() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let proposal_id = "wiki_review_promote_candidate_wiki-candidate";
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": "wiki_candidate",
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Repeated operator correction needs a durable wiki entry",
                "suggested_ai_instruction": "Check the project wiki before applying repeated corrections.",
                "human_summary": "Repeated correction candidate.",
                "evidence_refs": [format!("task:wiki_candidate?token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "runtime_observed",
                "occurrence_count": 2,
                "confidence": "repeated",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;

    let handoff_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "proposal-handoff", proposal_id, "--json"])
        .output()?;
    assert!(handoff_output.status.success());
    let handoff: serde_json::Value = serde_json::from_slice(&handoff_output.stdout)?;
    assert_eq!(handoff["status"], "ready");
    let preview_command = handoff["command"]
        .as_str()
        .expect("handoff preview command")
        .to_string();

    let accept_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "accept-proposal",
            proposal_id,
            "--reason",
            &format!("accepted proposal token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(accept_output.status.success());
    let event: serde_json::Value = serde_json::from_slice(&accept_output.stdout)?;
    let event_id = event["id"].as_str().expect("event id");

    let promote_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "promote",
            "wiki_candidate",
            "--reason",
            &format!("executed preview token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(promote_output.status.success());
    let mutation: serde_json::Value = serde_json::from_slice(&promote_output.stdout)?;
    let audit_id = mutation["audit"]["id"].as_str().expect("audit id");

    let audit_path = profile_dir.join("adaptive_wiki_audit.jsonl");
    let events_path = profile_dir.join("adaptive_wiki_review_events.jsonl");
    let audit_before = fs::read_to_string(&audit_path)?;
    let events_before = fs::read_to_string(&events_path)?;
    let executed_command = format!("{preview_command} --reason token={secret}");
    let receipt_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            proposal_id,
            "--audit-id",
            audit_id,
            "--event-id",
            event_id,
            "--command",
            &executed_command,
            "--json",
        ])
        .output()?;
    assert!(receipt_output.status.success());
    let receipt_stdout = String::from_utf8_lossy(&receipt_output.stdout);
    assert!(!receipt_stdout.contains(secret));
    let receipt: serde_json::Value = serde_json::from_slice(&receipt_output.stdout)?;
    assert_eq!(receipt["status"], "linked");
    assert_eq!(receipt["read_only"], true);
    assert_eq!(receipt["proposal"]["current"], false);
    assert_eq!(receipt["proposal"]["action"], "promote");
    assert_eq!(receipt["proposal"]["subject_kind"], "candidate");
    assert_eq!(receipt["proposal"]["subject_id"], "wiki_candidate");
    assert_eq!(receipt["audit"]["id"], audit_id);
    assert_eq!(receipt["event"]["id"], event_id);
    let receipt_command = receipt["preview_command"]
        .as_str()
        .expect("receipt command");
    assert!(receipt_command.contains("[REDACTED]"));
    assert_eq!(
        receipt["preview_command_sha256"],
        sha256_hex(receipt_command.as_bytes())
    );
    assert_eq!(
        receipt["preview_command_sha256"]
            .as_str()
            .expect("receipt command hash")
            .len(),
        64
    );
    assert!(receipt["checks"]
        .as_array()
        .expect("receipt checks")
        .iter()
        .all(|check| check["passed"].as_bool().expect("check passed bool")));
    assert!(receipt["blockers"]
        .as_array()
        .expect("receipt blockers")
        .is_empty());

    let output_path = temp.path().join("exports").join("proposal-receipt.json");
    let export_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            proposal_id,
            "--audit-id",
            audit_id,
            "--event-id",
            event_id,
            "--command",
            &executed_command,
            "--output",
            output_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(export_output.status.success());
    let export_stdout = String::from_utf8_lossy(&export_output.stdout);
    assert!(!export_stdout.contains(secret));
    let export_receipt: serde_json::Value = serde_json::from_slice(&export_output.stdout)?;
    assert_eq!(
        export_receipt["exported_to"],
        output_path.to_str().expect("utf-8 path")
    );
    assert_eq!(export_receipt["receipt"]["status"], "linked");
    assert_eq!(
        export_receipt["bytes_written"]
            .as_u64()
            .expect("bytes written"),
        fs::metadata(&output_path)?.len()
    );
    let exported_receipt: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&output_path)?)?;
    assert_eq!(exported_receipt, export_receipt["receipt"]);
    assert!(!fs::read_to_string(&output_path)?.contains(secret));

    let incomplete_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            proposal_id,
            "--audit-id",
            "missing_audit",
            "--event-id",
            event_id,
            "--command",
            &executed_command,
            "--json",
        ])
        .output()?;
    assert!(incomplete_output.status.success());
    let incomplete: serde_json::Value = serde_json::from_slice(&incomplete_output.stdout)?;
    assert_eq!(incomplete["status"], "incomplete");
    assert!(incomplete["blockers"]
        .as_array()
        .expect("incomplete blockers")
        .iter()
        .any(|blocker| blocker
            .as_str()
            .expect("blocker text")
            .contains("missing_audit was not found")));
    assert!(incomplete["checks"]
        .as_array()
        .expect("incomplete checks")
        .iter()
        .any(|check| check["name"] == "audit_matches_proposal"
            && !check["passed"].as_bool().expect("check passed")
            && check["detail"]
                .as_str()
                .expect("check detail")
                .contains("audit metadata unavailable")));
    assert_eq!(fs::read_to_string(&audit_path)?, audit_before);
    assert_eq!(fs::read_to_string(&events_path)?, events_before);
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_handoff_and_receipt_redact_legacy_sources_without_authority_drift() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let proposal_id = "wiki_review_promote_candidate_wiki-candidate";
    let candidates_path = profile_dir.join("adaptive_wiki_candidates.json");
    fs::write(
        &candidates_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [{
                "id": "wiki_candidate",
                "kind": "failure_pattern",
                "scope": "project",
                "scope_ref": "project",
                "claim": "Legacy candidate requires a safe handoff",
                "suggested_ai_instruction": "Review the legacy candidate before promotion.",
                "human_summary": "Legacy handoff candidate.",
                "evidence_refs": [format!("task:legacy?token={secret}")],
                "signal_kind": "operator_correction",
                "origin": "runtime_observed",
                "occurrence_count": 2,
                "confidence": "repeated",
                "created_at": now,
                "updated_at": now,
                "last_seen_at": now
            }]
        }))?,
    )?;
    let candidates_before = fs::read(&candidates_path)?;

    for json_output in [false, true] {
        let mut command = forager_command(temp.path());
        command.args(["offdesk", "wiki", "proposal-handoff", proposal_id]);
        if json_output {
            command.arg("--json");
        }
        let output = command.output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(!stdout.contains(secret));
        assert!(stdout.contains("redacted_id_sha256_"));
        if json_output {
            let preview: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            assert_eq!(preview["status"], "ready");
        } else {
            assert!(stdout.contains("Adaptive wiki proposal handoff"));
            assert!(stdout.contains("evidence:"));
        }
    }
    assert_eq!(fs::read(&candidates_path)?, candidates_before);
    assert!(!profile_dir
        .join("adaptive_wiki_review_events.jsonl")
        .exists());
    assert!(!profile_dir.join("adaptive_wiki_audit.jsonl").exists());

    let audit_id = format!("wiki_audit_{secret}");
    let event_id = format!("wiki_review_event_{secret}");
    let audit_path = profile_dir.join("adaptive_wiki_audit.jsonl");
    let events_path = profile_dir.join("adaptive_wiki_review_events.jsonl");
    fs::write(
        &audit_path,
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": audit_id,
                "action": "promote",
                "subject_id": "wiki_candidate",
                "candidate_id": "wiki_candidate",
                "entry_id": "wiki_entry",
                "actor": format!("legacy-actor-{secret}"),
                "reason": format!("legacy audit token={secret}"),
                "evidence_ref": format!("task:legacy?token={secret}"),
                "created_at": now
            }))?
        ),
    )?;
    fs::write(
        &events_path,
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": event_id,
                "proposal_id": proposal_id,
                "decision": "accepted",
                "proposal_action": "promote",
                "subject_kind": "candidate",
                "subject_id": "wiki_candidate",
                "actor": format!("legacy-actor-{secret}"),
                "reason": format!("legacy event token={secret}"),
                "evidence_refs": [format!("task:legacy?token={secret}")],
                "created_at": now
            }))?
        ),
    )?;
    let audit_before = fs::read(&audit_path)?;
    let events_before = fs::read(&events_path)?;
    let executed_command =
        format!("forager offdesk wiki promote wiki_candidate --reason token={secret}");

    for json_output in [false, true] {
        let mut command = forager_command(temp.path());
        command.args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            proposal_id,
            "--audit-id",
            &audit_id,
            "--event-id",
            &event_id,
            "--command",
            &executed_command,
        ]);
        if json_output {
            command.arg("--json");
        }
        let output = command.output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(!stdout.contains(secret), "secret leaked:\n{stdout}");
        assert!(stdout.contains("[REDACTED]"));
        if json_output {
            let receipt: serde_json::Value = serde_json::from_slice(&output.stdout)?;
            assert_eq!(receipt["status"], "linked");
            assert_eq!(receipt["read_only"], true);
        } else {
            assert!(stdout.contains("Adaptive wiki proposal receipt"));
            assert!(stdout.contains("linked"));
        }
    }

    let output_path = temp
        .path()
        .join("exports")
        .join(format!("proposal-receipt-{secret}.json"));
    let export_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-receipt",
            proposal_id,
            "--audit-id",
            &audit_id,
            "--event-id",
            &event_id,
            "--command",
            &executed_command,
            "--output",
            output_path.to_str().expect("utf-8 output path"),
        ])
        .output()?;
    assert!(
        export_output.status.success(),
        "{}",
        String::from_utf8_lossy(&export_output.stderr)
    );
    let export_stdout = String::from_utf8_lossy(&export_output.stdout);
    assert!(!export_stdout.contains(secret));
    assert!(export_stdout.contains("[REDACTED]"));
    let exported_bytes = fs::read(&output_path)?;
    assert!(!String::from_utf8_lossy(&exported_bytes).contains(secret));
    let exported: serde_json::Value = serde_json::from_slice(&exported_bytes)?;
    assert_eq!(exported["status"], "linked");
    assert!(exported["audit"]["id"]
        .as_str()
        .expect("safe audit id")
        .contains("[REDACTED_API_KEY]"));
    assert!(exported["event"]["id"]
        .as_str()
        .expect("safe event id")
        .contains("[REDACTED_API_KEY]"));

    assert_eq!(fs::read(&candidates_path)?, candidates_before);
    assert_eq!(fs::read(&audit_path)?, audit_before);
    assert_eq!(fs::read(&events_path)?, events_before);
    for path in [
        "adaptive_wiki_entries.json",
        "adaptive_wiki_corrections.jsonl",
        "adaptive_wiki_usage.jsonl",
        "adaptive_wiki_promotion_receipts",
        "adaptive_wiki_proposal_receipts",
    ] {
        assert!(!profile_dir.join(path).exists(), "unexpected write: {path}");
    }
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_public_identity_aliases_are_distinct_and_actionable() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let first_raw = "wiki-entry-sk-aaaaaaaaaaaaaaaa";
    let second_raw = "wiki-entry-sk-bbbbbbbbbbbbbbbb";
    let first_alias = format!(
        "redacted_id_sha256_{}",
        sha256_hex(format!("forager.operator-safe-identity.v1\0{first_raw}").as_bytes())
    );
    let reserved_raw = first_alias.clone();
    let reserved_alias = format!(
        "redacted_id_sha256_{}",
        sha256_hex(
            format!("forager.operator-safe-identity.reserved.v1\0{reserved_raw}").as_bytes()
        )
    );
    let entries_path = profile_dir.join("adaptive_wiki_entries.json");
    fs::write(
        &entries_path,
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": first_raw,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "First legacy entry needs review.",
                "ai_instruction": "Review the first entry.",
                "human_summary": "First legacy entry.",
                "evidence_refs": [],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }, {
                "id": second_raw,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Second legacy entry needs review.",
                "ai_instruction": "Review the second entry.",
                "human_summary": "Second legacy entry.",
                "evidence_refs": [],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }, {
                "id": reserved_raw,
                "kind": "procedure",
                "scope": "project",
                "scope_ref": "project",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Reserved-prefix legacy entry needs review.",
                "ai_instruction": "Review the reserved-prefix entry.",
                "human_summary": "Reserved-prefix legacy entry.",
                "evidence_refs": [],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now
            }]
        }))?,
    )?;

    let list_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "entries", "--json"])
        .output()?;
    assert!(
        list_output.status.success(),
        "{}",
        String::from_utf8_lossy(&list_output.stderr)
    );
    let list_stdout = String::from_utf8_lossy(&list_output.stdout);
    assert!(!list_stdout.contains(first_raw));
    assert!(!list_stdout.contains(second_raw));
    let entries: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    let public_ids: Vec<_> = entries
        .as_array()
        .expect("entry array")
        .iter()
        .map(|entry| entry["id"].as_str().expect("public entry id").to_string())
        .collect();
    assert_eq!(public_ids.len(), 3);
    assert_eq!(
        public_ids
            .iter()
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        3
    );
    assert!(public_ids
        .iter()
        .all(|id| id.starts_with("redacted_id_sha256_") && id.len() == 83));
    assert!(public_ids.contains(&first_alias));
    assert!(public_ids.contains(&reserved_alias));

    for public_id in [&first_alias, &reserved_alias] {
        let show_output = forager_command(temp.path())
            .args(["offdesk", "wiki", "show", public_id, "--json"])
            .output()?;
        assert!(
            show_output.status.success(),
            "{}",
            String::from_utf8_lossy(&show_output.stderr)
        );
        let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
        assert_eq!(shown["entry"]["id"], *public_id);
    }

    let vault = temp.path().join("wiki-vault");
    let export_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "export-markdown",
            "--output",
            vault.to_str().expect("UTF-8 vault path"),
            "--json",
        ])
        .output()?;
    assert!(
        export_output.status.success(),
        "{}",
        String::from_utf8_lossy(&export_output.stderr)
    );
    let export: serde_json::Value = serde_json::from_slice(&export_output.stdout)?;
    assert_eq!(export["summary"]["entries_exported"], 3);
    assert_eq!(export["summary"]["files_written"], 6);
    for public_id in [&first_alias, &reserved_alias] {
        let slug = public_id.replace('_', "-");
        assert!(vault
            .join("entries")
            .join("procedure")
            .join(format!("{slug}.md"))
            .is_file());
    }

    let episode_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "evaluate-episode",
            &first_alias,
            "--project-key",
            "project",
            "--out-project-key",
            "other-project",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(
        episode_output.status.success(),
        "{}",
        String::from_utf8_lossy(&episode_output.stderr)
    );
    let episode: serde_json::Value = serde_json::from_slice(&episode_output.stdout)?;
    assert_eq!(episode["target_entry_id"], first_alias);
    assert!(episode["trace"][0]["entry_ids"]
        .as_array()
        .expect("in-scope trace ids")
        .iter()
        .any(|entry_id| entry_id == &first_alias));
    for public_id in [&first_alias, &reserved_alias] {
        let trace_output = forager_command(temp.path())
            .args([
                "offdesk",
                "wiki",
                "episode-trace",
                "--entry-id",
                public_id,
                "--dry-run",
                "--json",
            ])
            .output()?;
        assert!(trace_output.status.success());
        let trace: serde_json::Value = serde_json::from_slice(&trace_output.stdout)?;
        assert_eq!(trace["filter"]["entry_id"], *public_id);
    }

    let review_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run", "--json"])
        .output()?;
    assert!(
        review_output.status.success(),
        "{}",
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;
    let deprecations: Vec<_> = review["proposals"]
        .as_array()
        .expect("proposal array")
        .iter()
        .filter(|proposal| proposal["action"] == "deprecate")
        .collect();
    assert_eq!(deprecations.len(), 3);
    assert_eq!(
        deprecations
            .iter()
            .map(|proposal| proposal["id"].as_str().expect("proposal id"))
            .collect::<std::collections::BTreeSet<_>>()
            .len(),
        3
    );
    let first_proposal_id = deprecations
        .iter()
        .find(|proposal| proposal["subject_id"] == first_alias)
        .expect("first proposal")["id"]
        .as_str()
        .expect("proposal id");

    let handoff_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            first_proposal_id,
            "--json",
        ])
        .output()?;
    assert!(
        handoff_output.status.success(),
        "{}",
        String::from_utf8_lossy(&handoff_output.stderr)
    );
    let handoff: serde_json::Value = serde_json::from_slice(&handoff_output.stdout)?;
    assert_eq!(handoff["status"], "ready");
    let command = handoff["command"].as_str().expect("handoff command");
    assert!(command.contains(&first_alias));
    assert!(!command.contains("[REDACTED_API_KEY]"));

    let parameterized_handoff_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "proposal-handoff",
            first_proposal_id,
            "--mutation",
            "deprecate",
            "--reason",
            "exercise manual selector",
            "--json",
        ])
        .output()?;
    assert!(parameterized_handoff_output.status.success());
    let parameterized_handoff: serde_json::Value =
        serde_json::from_slice(&parameterized_handoff_output.stdout)?;
    let parameterized_command = parameterized_handoff["command"]
        .as_str()
        .expect("parameterized handoff command");
    assert!(parameterized_command.contains(&first_alias));
    assert!(!parameterized_command.contains(&reserved_alias));

    let deprecate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "deprecate",
            &first_alias,
            "--reason",
            "exercise public identity selector",
            "--json",
        ])
        .output()?;
    assert!(
        deprecate_output.status.success(),
        "{}",
        String::from_utf8_lossy(&deprecate_output.stderr)
    );
    let deprecate_stdout = String::from_utf8_lossy(&deprecate_output.stdout);
    assert!(!deprecate_stdout.contains(first_raw));
    assert!(!deprecate_stdout.contains(second_raw));

    let canonical: serde_json::Value = serde_json::from_str(&fs::read_to_string(&entries_path)?)?;
    assert_eq!(canonical["entries"][0]["id"], first_raw);
    assert_eq!(canonical["entries"][0]["status"], "deprecated");
    assert_eq!(canonical["entries"][1]["id"], second_raw);
    assert_eq!(canonical["entries"][1]["status"], "promoted");
    assert_eq!(canonical["entries"][2]["id"], reserved_raw);
    assert_eq!(canonical["entries"][2]["status"], "promoted");

    let reserved_deprecate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "deprecate",
            &reserved_alias,
            "--reason",
            "exercise escaped reserved identity selector",
            "--json",
        ])
        .output()?;
    assert!(
        reserved_deprecate_output.status.success(),
        "{}",
        String::from_utf8_lossy(&reserved_deprecate_output.stderr)
    );
    let canonical: serde_json::Value = serde_json::from_str(&fs::read_to_string(&entries_path)?)?;
    assert_eq!(canonical["entries"][0]["status"], "deprecated");
    assert_eq!(canonical["entries"][1]["status"], "promoted");
    assert_eq!(canonical["entries"][2]["status"], "deprecated");
    let audit = fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?;
    assert!(audit.contains(first_raw));
    assert!(audit.contains(&reserved_raw));
    assert!(!audit.contains(second_raw));
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_promotion_chain_reports_snapshots_and_usage_without_mutation() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let promotion_at = Utc::now();
    let after = promotion_at + Duration::hours(1);
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_chain_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Keep promotion evidence replayable",
                    "ai_instruction": "Use the promotion evidence chain.",
                    "human_summary": "Promotion chain target",
                    "evidence_refs": ["task:task_chain"],
                    "confidence": "explicit",
                    "created_at": promotion_at,
                    "updated_at": after
                }
            ]
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": []
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_usage.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_usage_chain",
                "entry_id": "wiki_chain_entry",
                "task_id": "task_chain",
                "request_id": format!("request_chain?token={secret}"),
                "project_key": "project",
                "artifact_kind": "report",
                "projection_kind": "runtime_probe",
                "activation_mode": "confirm",
                "created_at": after
            }))?
        ),
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_audit.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "id": "wiki_audit_chain_promote",
                "action": "promote",
                "subject_id": "wiki_chain_entry",
                "candidate_id": "wiki_chain_candidate",
                "entry_id": "wiki_chain_entry",
                "actor": "cli",
                "reason": format!("promotion chain token={secret}"),
                "evidence_ref": "task:task_chain",
                "candidate_snapshot": {
                    "id": "wiki_chain_candidate",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "claim": "Keep promotion evidence replayable",
                    "human_summary": "Promotion candidate",
                    "evidence_refs": ["task:task_chain"],
                    "signal_kind": "operator_correction",
                    "origin": "operator_explicit",
                    "source_refs": [format!("approval:chain?token={secret}")],
                    "source_hashes": ["sha256:chain"],
                    "review_reason": "Promotion chain review",
                    "occurrence_count": 2,
                    "confidence": "explicit",
                    "updated_at": promotion_at,
                    "last_seen_at": promotion_at
                },
                "entry_snapshot": {
                    "id": "wiki_chain_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Keep promotion evidence replayable",
                    "human_summary": "Promotion entry",
                    "evidence_refs": ["task:task_chain"],
                    "counterexamples": [],
                    "confidence": "explicit",
                    "updated_at": promotion_at
                },
                "created_at": promotion_at
            }))?
        ),
    )?;

    let entries_before = fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let candidates_before = fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?;
    let audit_before = fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?;
    let dry_run_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "promotion-chain",
            "wiki_chain_entry",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(dry_run_output.status.success());
    let dry_run: serde_json::Value = serde_json::from_slice(&dry_run_output.stdout)?;
    assert_eq!(dry_run["dry_run"], true);
    assert_eq!(dry_run["summary"]["files_written"], 0);
    assert_eq!(dry_run["summary"]["promotion_audit_found"], true);
    assert_eq!(dry_run["summary"]["candidate_snapshot_present"], true);
    assert_eq!(dry_run["summary"]["entry_snapshot_present"], true);
    assert_eq!(dry_run["summary"]["usage_records"], 1);
    assert_eq!(dry_run["summary"]["related_audit_records"], 1);
    assert!(!String::from_utf8_lossy(&dry_run_output.stdout).contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_promotion_chains").exists());
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?,
        audit_before
    );

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "promotion-chain",
            "wiki_chain_entry",
            "--dry-run",
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Adaptive wiki promotion evidence chain planned"));
    assert!(human_stdout.contains("entry: wiki_chain_entry"));
    assert!(human_stdout.contains(
        "promotion audit=true candidate snapshot=true entry snapshot=true current entry=true"
    ));
    assert!(human_stdout.contains("usage records=1 related audits=1 failures=0"));
    assert!(!human_stdout.contains(secret));
    assert!(!profile_dir.join("adaptive_wiki_promotion_chains").exists());

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "promotion-chain",
            "wiki_chain_entry",
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["dry_run"], false);
    assert_eq!(report["summary"]["files_written"], 3);
    assert_eq!(report["candidate_snapshot"]["id"], "wiki_chain_candidate");
    assert_eq!(report["entry_snapshot"]["id"], "wiki_chain_entry");
    assert_eq!(report["current_entry"]["id"], "wiki_chain_entry");
    assert!(!String::from_utf8_lossy(&output.stdout).contains(secret));
    let report_dir = PathBuf::from(report["report_dir"].as_str().expect("chain report dir"));
    assert!(report_dir.join("report.json").is_file());
    assert!(report_dir.join("chain.jsonl").is_file());
    assert!(report_dir.join("REPORT.md").is_file());
    let chain_jsonl = fs::read_to_string(report_dir.join("chain.jsonl"))?;
    assert!(chain_jsonl.contains("promotion_audit"));
    assert!(chain_jsonl.contains("candidate_snapshot"));
    assert!(chain_jsonl.contains("usage"));
    assert!(!chain_jsonl.contains(secret));
    let report_md = fs::read_to_string(report_dir.join("REPORT.md"))?;
    assert!(report_md.contains("Adaptive Wiki Promotion Evidence Chain"));
    assert!(report_md.contains("wiki_chain_entry"));
    assert!(!report_md.contains(secret));
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        entries_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_candidates.json"))?,
        candidates_before
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?,
        audit_before
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_review_commands_mutate_entries_and_append_audit() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": []
        }))?,
    )?;
    fs::write(
        profile_dir.join("adaptive_wiki_candidates.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "candidates": [
                {
                    "id": "wiki_candidate_promote",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project-a",
                    "claim": "Ask before retrying dispatch",
                    "suggested_ai_instruction": "Ask the operator before retrying dispatch.",
                    "human_summary": "Captured denial",
                    "evidence_refs": ["approval:one"],
                    "signal_kind": "approval_denial",
                    "origin": "operator_explicit",
                    "source_refs": ["approval:one"],
                    "occurrence_count": 2,
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now
                },
                {
                    "id": "wiki_candidate_reject",
                    "kind": "fact",
                    "scope": "project",
                    "scope_ref": "project-a",
                    "claim": "Reject this",
                    "suggested_ai_instruction": "Do not promote this.",
                    "human_summary": "Low quality candidate",
                    "evidence_refs": ["task:reject"],
                    "signal_kind": "unknown",
                    "origin": "unknown",
                    "source_refs": ["task:reject"],
                    "occurrence_count": 1,
                    "confidence": "inferred",
                    "created_at": now,
                    "updated_at": now,
                    "last_seen_at": now
                }
            ]
        }))?,
    )?;

    let promote_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "promote",
            "wiki_candidate_promote",
            "--scope",
            "artifact_kind",
            "--scope-ref",
            "report",
            "--activation-mode",
            "context_only",
            "--reason",
            &format!("promote after review token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(promote_output.status.success());
    assert!(!String::from_utf8_lossy(&promote_output.stdout).contains(secret));
    let promoted: serde_json::Value = serde_json::from_slice(&promote_output.stdout)?;
    assert_eq!(promoted["action"], "promote");
    assert_eq!(promoted["entry"]["scope"], "artifact_kind");
    assert_eq!(promoted["entry"]["scope_ref"], "report");
    assert_eq!(promoted["entry"]["activation_mode"], "context_only");
    assert!(promoted["audit"]["reason"]
        .as_str()
        .expect("audit reason")
        .contains("[REDACTED]"));
    assert_eq!(
        promoted["audit"]["candidate_snapshot"]["id"],
        "wiki_candidate_promote"
    );
    assert_eq!(
        promoted["audit"]["candidate_snapshot"]["scope_ref"],
        "project-a"
    );
    let entry_id = promoted["entry"]["id"]
        .as_str()
        .expect("entry id")
        .to_string();
    assert_eq!(promoted["audit"]["entry_snapshot"]["id"], entry_id.as_str());
    assert_eq!(promoted["audit"]["entry_snapshot"]["scope_ref"], "report");
    assert_eq!(
        promoted["promotion_receipt"]["schema"],
        "adaptive_wiki_promotion_receipt.v1"
    );
    assert_eq!(promoted["promotion_receipt"]["status"], "promoted");
    assert_eq!(
        promoted["promotion_receipt"]["candidate_id"],
        "wiki_candidate_promote"
    );
    assert_eq!(promoted["promotion_receipt"]["entry_id"], entry_id.as_str());
    assert_eq!(
        promoted["promotion_receipt"]["audit_id"],
        promoted["audit"]["id"]
    );
    assert_eq!(
        promoted["promotion_receipt"]["authority"]["canonical_mutation_recorded"],
        true
    );
    let promotion_receipt_path = PathBuf::from(
        promoted["promotion_receipt_path"]
            .as_str()
            .expect("promotion receipt path"),
    );
    assert!(promotion_receipt_path.is_file());
    let promotion_receipt_file = fs::read_to_string(&promotion_receipt_path)?;
    assert!(!promotion_receipt_file.contains(secret));

    let review_after_promotion_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run", "--json"])
        .output()?;
    assert!(review_after_promotion_output.status.success());
    let review_after_promotion: serde_json::Value =
        serde_json::from_slice(&review_after_promotion_output.stdout)?;
    assert_eq!(
        review_after_promotion["summary"]["promotion_receipts_checked"],
        1
    );
    assert_eq!(
        review_after_promotion["summary"]["promotion_receipt_files_invalid"],
        0
    );
    assert_eq!(
        review_after_promotion["summary"]["promoted_entries_with_promotion_receipt"],
        1
    );
    assert_eq!(
        review_after_promotion["summary"]["promoted_entries_missing_promotion_receipt"],
        0
    );
    let invalid_receipt_path = promotion_receipt_path
        .parent()
        .expect("promotion receipt parent")
        .join("broken_promotion_receipt.json");
    fs::write(&invalid_receipt_path, "{not valid json")?;
    let review_with_invalid_receipt_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "review", "--dry-run", "--json"])
        .output()?;
    assert!(review_with_invalid_receipt_output.status.success());
    let review_with_invalid_receipt: serde_json::Value =
        serde_json::from_slice(&review_with_invalid_receipt_output.stdout)?;
    assert_eq!(
        review_with_invalid_receipt["summary"]["promotion_receipts_checked"],
        1
    );
    assert_eq!(
        review_with_invalid_receipt["summary"]["promotion_receipt_files_invalid"],
        1
    );
    assert_eq!(
        review_with_invalid_receipt["summary"]["promoted_entries_with_promotion_receipt"],
        1
    );

    let candidates_after_promote: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("adaptive_wiki_candidates.json"),
    )?)?;
    assert_eq!(
        candidates_after_promote["candidates"]
            .as_array()
            .expect("candidates")
            .len(),
        1
    );
    assert_eq!(
        candidates_after_promote["candidates"][0]["id"],
        "wiki_candidate_reject"
    );

    let reject_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "reject",
            "wiki_candidate_reject",
            "--reason",
            "not durable enough",
            "--json",
        ])
        .output()?;
    assert!(reject_output.status.success());
    let rejected: serde_json::Value = serde_json::from_slice(&reject_output.stdout)?;
    assert_eq!(rejected["action"], "reject");
    assert_eq!(rejected["candidate"]["id"], "wiki_candidate_reject");

    let rescope_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "rescope",
            &entry_id,
            "--scope",
            "project",
            "--scope-ref",
            "project-b",
            "--reason",
            "narrow to project",
            "--json",
        ])
        .output()?;
    assert!(rescope_output.status.success());
    let rescoped: serde_json::Value = serde_json::from_slice(&rescope_output.stdout)?;
    assert_eq!(rescoped["action"], "rescope");
    assert_eq!(rescoped["entry"]["scope"], "project");
    assert_eq!(rescoped["entry"]["scope_ref"], "project-b");
    assert_eq!(rescoped["audit"]["before_scope"]["scope"], "artifact_kind");
    assert_eq!(rescoped["audit"]["after_scope"]["scope"], "project");

    let runbook_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "update-runbook",
            &entry_id,
            "--support-ref",
            &format!("references/report.md?token={secret}"),
            "--capability-id",
            "capability.syncback",
            "--required-artifact-kind",
            "report",
            "--reason",
            "attach runbook refs",
            "--json",
        ])
        .output()?;
    assert!(runbook_output.status.success());
    assert!(!String::from_utf8_lossy(&runbook_output.stdout).contains(secret));
    let runbook: serde_json::Value = serde_json::from_slice(&runbook_output.stdout)?;
    assert_eq!(runbook["action"], "update_runbook");
    assert!(runbook["entry"]["support_refs"][0]
        .as_str()
        .expect("support ref")
        .contains("[REDACTED]"));
    assert_eq!(
        runbook["entry"]["capability_ids"],
        json!(["capability.syncback"])
    );
    assert_eq!(
        runbook["entry"]["required_artifact_kinds"],
        json!(["report"])
    );

    let runbook_projection_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project-b",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(runbook_projection_output.status.success());
    let runbook_projection: serde_json::Value =
        serde_json::from_slice(&runbook_projection_output.stdout)?;
    let runbook_projection_text = serde_json::to_string(&runbook_projection)?;
    assert_eq!(runbook_projection.as_array().expect("projection").len(), 1);
    assert!(!runbook_projection_text.contains("support_refs"));
    assert!(!runbook_projection_text.contains("capability.syncback"));
    assert!(!runbook_projection_text.contains("references/report.md"));

    let edit_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "edit",
            &entry_id,
            "--kind",
            "policy_rule",
            "--agent-mode",
            "planning",
            "--agent-mode",
            "review",
            "--reason",
            "apply curator classification",
            "--json",
        ])
        .output()?;
    assert!(edit_output.status.success());
    let edited: serde_json::Value = serde_json::from_slice(&edit_output.stdout)?;
    assert_eq!(edited["action"], "edit");
    assert_eq!(edited["entry"]["kind"], "policy_rule");
    assert_eq!(
        edited["entry"]["agent_modes"],
        json!(["planning", "review"])
    );
    assert_eq!(edited["audit"]["entry_snapshot"]["kind"], "policy_rule");
    assert_eq!(
        edited["audit"]["entry_snapshot"]["agent_modes"],
        json!(["planning", "review"])
    );

    let retag_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "add-tag",
            &entry_id,
            "--core-tag",
            "reviewed",
            "--proposed-tag",
            "operations",
            "--reason",
            "classify reviewed guidance",
        ])
        .output()?;
    assert!(retag_output.status.success());
    let retag_stdout = String::from_utf8_lossy(&retag_output.stdout);
    assert!(retag_stdout.contains(&format!("Retagged adaptive wiki entry {entry_id}")));
    assert!(retag_stdout.contains("core tags: reviewed"));
    assert!(retag_stdout.contains("proposed tags: operations"));
    assert!(retag_stdout.contains("audit: wiki_audit_"));

    let counterexample_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "add-counterexample",
            &entry_id,
            "--evidence-ref",
            "audit:counterexample",
            "--reason",
            "limited case",
            "--json",
        ])
        .output()?;
    assert!(counterexample_output.status.success());
    let counterexample: serde_json::Value = serde_json::from_slice(&counterexample_output.stdout)?;
    assert_eq!(counterexample["action"], "add_counterexample");
    assert!(counterexample["entry"]["counterexamples"]
        .as_array()
        .expect("counterexamples")
        .iter()
        .any(|value| value == "audit:counterexample"));

    let deprecate_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "deprecate",
            &entry_id,
            "--reason",
            &format!("superseded token={secret}"),
            "--json",
        ])
        .output()?;
    assert!(deprecate_output.status.success());
    assert!(!String::from_utf8_lossy(&deprecate_output.stdout).contains(secret));
    let deprecated: serde_json::Value = serde_json::from_slice(&deprecate_output.stdout)?;
    assert_eq!(deprecated["action"], "deprecate");
    assert_eq!(deprecated["entry"]["status"], "deprecated");
    assert!(deprecated["audit"]["reason"]
        .as_str()
        .expect("audit reason")
        .contains("[REDACTED]"));

    let projection_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--project-key",
            "project-b",
            "--json",
        ])
        .output()?;
    assert!(projection_output.status.success());
    let projection: serde_json::Value = serde_json::from_slice(&projection_output.stdout)?;
    assert!(projection.as_array().expect("projection").is_empty());

    let audit = fs::read_to_string(profile_dir.join("adaptive_wiki_audit.jsonl"))?;
    assert!(!audit.contains(secret));
    assert_eq!(audit.lines().count(), 8);
    assert!(audit.contains("\"action\":\"promote\""));
    assert!(audit.contains("\"candidate_snapshot\""));
    assert!(audit.contains("\"entry_snapshot\""));
    assert!(audit.contains("\"action\":\"reject\""));
    assert!(audit.contains("\"action\":\"rescope\""));
    assert!(audit.contains("\"action\":\"update_runbook\""));
    assert!(audit.contains("\"action\":\"edit\""));
    assert!(audit.contains("\"action\":\"retag\""));
    assert!(audit.contains("\"action\":\"add_counterexample\""));
    assert!(audit.contains("\"action\":\"deprecate\""));
    Ok(())
}

#[test]
#[serial]
fn offdesk_deny_records_adaptive_wiki_approval_denial_candidate() -> Result<()> {
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

    let deny_output = forager_command(temp.path())
        .args(["offdesk", "deny", "approval_one", "--json"])
        .output()?;
    assert!(deny_output.status.success());
    let denied: serde_json::Value = serde_json::from_slice(&deny_output.stdout)?;
    assert_eq!(denied["status"], "denied");
    assert_eq!(denied["approval_id"], "approval_one");

    let candidates_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "candidates",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(candidates_output.status.success());
    let candidates: serde_json::Value = serde_json::from_slice(&candidates_output.stdout)?;
    assert_eq!(candidates.as_array().expect("candidates").len(), 1);
    let candidate = &candidates[0];
    assert_eq!(candidate["kind"], "policy_rule");
    assert_eq!(candidate["scope"], "project");
    assert_eq!(candidate["scope_ref"], "project");
    assert_eq!(candidate["signal_kind"], "approval_denial");
    assert_eq!(candidate["origin"], "operator_explicit");
    assert_eq!(candidate["confidence"], "explicit");
    assert_eq!(candidate["suggested_scope"]["scope"], "project");
    assert_eq!(candidate["suggested_scope"]["scope_ref"], "project");
    assert!(candidate["claim"]
        .as_str()
        .expect("claim")
        .contains("Operator denied `dispatch.runtime`"));
    assert!(candidate["evidence_refs"]
        .as_array()
        .expect("evidence refs")
        .iter()
        .any(|value| value == "approval:approval_one"));
    assert!(candidate["source_refs"]
        .as_array()
        .expect("source refs")
        .iter()
        .any(|value| value == "approval:approval_one"));
    assert!(candidate["source_refs"]
        .as_array()
        .expect("source refs")
        .iter()
        .any(|value| value == "task:task"));
    assert!(candidate["source_refs"]
        .as_array()
        .expect("source refs")
        .iter()
        .any(|value| value == "request:request"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_blocks_missing_required_artifact_before_approval() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "canonical.syncback",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["status"], "blocked");
    assert!(outcome["reason"]
        .as_str()
        .expect("reason")
        .contains("mutation_snapshot"));
    assert!(!profile_dir(temp.path())
        .join("pending_action_approvals.json")
        .exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_accepts_supplied_required_artifact() -> Result<()> {
    let temp = tempdir()?;
    let artifact_path = temp.path().join("mutation.json");
    fs::write(&artifact_path, "{}")?;
    let artifact_arg = format!("mutation_snapshot={}", artifact_path.display());

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "gate",
            "canonical.syncback",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact",
            artifact_arg.as_str(),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["status"], "pending_approval");
    assert_eq!(outcome["artifact_check"]["satisfied"], true);
    assert_eq!(
        outcome["artifact_check"]["missing_artifact_ids"]
            .as_array()
            .map(Vec::len)
            .unwrap_or(0),
        0
    );
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
    write_implementation_packet_fixture(temp.path(), "project", "packet-launch-test")?;
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
    assert_eq!(
        outcome["probe"]["implementation_packet"]["packet_id"],
        "packet-launch-test"
    );
    assert_eq!(
        outcome["probe"]["implementation_packet"]["safe_to_delegate"],
        true
    );
    assert!(!outcome["probe"]["launch_spec_summary"]
        .as_str()
        .expect("summary")
        .contains("sk-secret"));

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["ticket_id"], "ticket");
    assert_eq!(
        runs[0]["implementation_packet"]["packet_id"],
        "packet-launch-test"
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_gate_and_launch_presentation_redact_json_and_human_outputs() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";

    let gate_json_output = forager_command(temp.path())
        .arg("offdesk")
        .arg("gate")
        .arg("dispatch.runtime")
        .arg("--project-key")
        .arg(format!("project token={secret}"))
        .arg("--request-id")
        .arg(format!("request-json token={secret}"))
        .arg("--task-id")
        .arg(format!("task-json token={secret}"))
        .arg("--preview")
        .arg(format!("preview token={secret}"))
        .arg("--reason")
        .arg(format!("reason token={secret}"))
        .arg("--json")
        .output()?;
    assert!(gate_json_output.status.success());
    let gate_json_stdout = String::from_utf8_lossy(&gate_json_output.stdout);
    assert!(!gate_json_stdout.contains(secret));
    assert!(gate_json_stdout.contains("[REDACTED]"));
    let gate_json: serde_json::Value = serde_json::from_slice(&gate_json_output.stdout)?;
    assert_eq!(gate_json["status"], "pending_approval");

    let gate_human_output = forager_command(temp.path())
        .arg("offdesk")
        .arg("gate")
        .arg("dispatch.runtime")
        .arg("--project-key")
        .arg(format!("project token={secret}"))
        .arg("--request-id")
        .arg(format!("request-human token={secret}"))
        .arg("--task-id")
        .arg(format!("task-human token={secret}"))
        .arg("--preview")
        .arg(format!("preview token={secret}"))
        .arg("--reason")
        .arg(format!("reason token={secret}"))
        .output()?;
    assert!(gate_human_output.status.success());
    let gate_human_stdout = String::from_utf8_lossy(&gate_human_output.stdout);
    assert!(gate_human_stdout.contains("Pending approval: dispatch.runtime"));
    assert!(gate_human_stdout.contains("preview:     preview token=[REDACTED]"));
    assert!(gate_human_stdout.contains("reason:      reason token=[REDACTED]"));
    assert!(!gate_human_stdout.contains(secret));

    let project_key = format!("launch-project token={secret}");
    let request_id = format!("launch-request token={secret}");
    let task_id = format!("launch-task token={secret}");
    let brief_path = temp.path().join("redaction-brief.json");
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": request_id,
            "task_id": task_id,
            "project_key": project_key,
            "approved": true,
            "allowed_runtime_mutations": ["background.launch"],
            "allowed_canonical_mutations": [],
            "fresh_until": Utc::now() + Duration::minutes(10)
        }))?,
    )?;

    let launch_json_output = forager_command(temp.path())
        .arg("offdesk")
        .arg("launch")
        .arg("background.launch")
        .arg("--runner")
        .arg("local-background")
        .arg("--project-key")
        .arg(&project_key)
        .arg("--request-id")
        .arg(&request_id)
        .arg("--task-id")
        .arg(&task_id)
        .arg("--ticket-id")
        .arg(format!("ticket-json token={secret}"))
        .arg("--launch-spec")
        .arg(format!("legacy launch token={secret}"))
        .arg("--brief")
        .arg(&brief_path)
        .arg("--json")
        .output()?;
    assert!(launch_json_output.status.success());
    let launch_json_stdout = String::from_utf8_lossy(&launch_json_output.stdout);
    assert!(!launch_json_stdout.contains(secret));
    let launch_json: serde_json::Value = serde_json::from_slice(&launch_json_output.stdout)?;
    assert_eq!(launch_json["gate"]["status"], "proceed");
    assert!(launch_json["probe"]["ticket_id"]
        .as_str()
        .expect("ticket id")
        .contains("[REDACTED]"));

    let launch_human_output = forager_command(temp.path())
        .arg("offdesk")
        .arg("launch")
        .arg("background.launch")
        .arg("--runner")
        .arg("local-background")
        .arg("--project-key")
        .arg(&project_key)
        .arg("--request-id")
        .arg(&request_id)
        .arg("--task-id")
        .arg(&task_id)
        .arg("--ticket-id")
        .arg(format!("ticket-human token={secret}"))
        .arg("--brief")
        .arg(&brief_path)
        .output()?;
    assert!(launch_human_output.status.success());
    let launch_human_stdout = String::from_utf8_lossy(&launch_human_output.stdout);
    assert!(launch_human_stdout.contains("Proceed: background.launch"));
    assert!(launch_human_stdout.contains("ticket_id: ticket-human token=[REDACTED]"));
    assert!(!launch_human_stdout.contains(secret));
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
    assert_eq!(outcomes[0]["next_safe_action"]["kind"], "closeout_check");
    assert_eq!(
        outcomes[0]["next_safe_action"]["requires_operator_review"],
        true
    );

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["phase"], "completed");
    assert_eq!(
        runs[0]["last_recovery_evidence"],
        "local background result artifact present"
    );
    assert_eq!(runs[0]["last_recovery_terminal"], true);
    assert!(runs[0]["last_observed_at"].as_str().is_some());
    Ok(())
}

#[test]
#[serial]
fn offdesk_poll_marks_stale_background_heartbeat() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "runner_kind": "local_background",
                "phase": "launched",
                "runtime_handle_alive": true,
                "worker_heartbeat_at": now - Duration::minutes(20),
                "heartbeat_timeout_sec": 300
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "poll", "ticket", "--json"])
        .output()?;

    assert!(output.status.success());
    let outcomes: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcomes[0]["decision"]["phase"], "stale_lost_callback");
    assert_eq!(outcomes[0]["probe"]["worker_heartbeat_stale"], true);
    assert_eq!(
        outcomes[0]["next_safe_action"]["kind"],
        "resume_review_required"
    );
    assert!(outcomes[0]["next_safe_action"]["commands"]
        .as_array()
        .expect("next action commands")
        .iter()
        .any(|command| command
            .as_str()
            .expect("next action command")
            .contains("forager offdesk poll ticket")));

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["phase"], "stale_lost_callback");
    assert_eq!(runs[0]["worker_heartbeat_stale"], true);
    assert_eq!(
        runs[0]["last_recovery_evidence"],
        "local background heartbeat is stale"
    );
    assert_eq!(runs[0]["last_recovery_terminal"], false);
    assert!(runs[0]["last_observed_at"].as_str().is_some());
    Ok(())
}

#[test]
#[serial]
fn offdesk_background_poll_presentation_redacts_legacy_json_and_human_state() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "project_key": format!("project-token={secret}"),
                "runner_kind": "local_background",
                "phase": "launched",
                "launch_spec_summary": format!("legacy launch token={secret}"),
                "runtime_handle_alive": false,
                "last_log_tail": format!("legacy tail token={secret}")
            }
        ]))?,
    )?;

    let poll_json_output = forager_command(temp.path())
        .args(["offdesk", "poll", "ticket", "--json"])
        .output()?;
    assert!(poll_json_output.status.success());
    let poll_json_stdout = String::from_utf8_lossy(&poll_json_output.stdout);
    assert!(!poll_json_stdout.contains(secret));
    let outcomes: serde_json::Value = serde_json::from_slice(&poll_json_output.stdout)?;
    assert_eq!(outcomes[0]["decision"]["phase"], "stale_lost_callback");
    assert!(outcomes[0]["probe"]["launch_spec_summary"]
        .as_str()
        .expect("launch spec summary")
        .contains("[REDACTED]"));

    let poll_human_output = forager_command(temp.path())
        .args(["offdesk", "poll", "ticket"])
        .output()?;
    assert!(poll_human_output.status.success());
    let poll_human_stdout = String::from_utf8_lossy(&poll_human_output.stdout);
    assert!(poll_human_stdout.contains("ticket LocalBackground -> StaleLostCallback"));
    assert!(poll_human_stdout.contains("tail: legacy tail token=[REDACTED]"));
    assert!(poll_human_stdout.contains("next:"));
    assert!(!poll_human_stdout.contains(secret));

    let background_json_output = forager_command(temp.path())
        .args(["offdesk", "background", "--json"])
        .output()?;
    assert!(background_json_output.status.success());
    let background_json_stdout = String::from_utf8_lossy(&background_json_output.stdout);
    assert!(!background_json_stdout.contains(secret));
    let statuses: serde_json::Value = serde_json::from_slice(&background_json_output.stdout)?;
    assert_eq!(statuses[0]["decision"]["phase"], "stale_lost_callback");

    let background_human_output = forager_command(temp.path())
        .args(["offdesk", "background"])
        .output()?;
    assert!(background_human_output.status.success());
    let background_human_stdout = String::from_utf8_lossy(&background_human_output.stdout);
    assert!(background_human_stdout.contains("tail: legacy tail token=[REDACTED]"));
    assert!(!background_human_stdout.contains(secret));

    fs::write(profile_dir.join("background_runs.json"), "[]\n")?;
    let empty_poll_output = forager_command(temp.path())
        .args(["offdesk", "poll"])
        .output()?;
    assert!(empty_poll_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_poll_output.stdout).trim(),
        "No matching background runner probes found."
    );
    let empty_background_output = forager_command(temp.path())
        .args(["offdesk", "background"])
        .output()?;
    assert!(empty_background_output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&empty_background_output.stdout).trim(),
        "No background runner probes found."
    );
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
    assert_eq!(
        runs[0]["last_recovery_evidence"],
        "local background result artifact present"
    );
    assert!(runs[0]["last_observed_at"].as_str().is_some());
    Ok(())
}

#[test]
#[serial]
fn offdesk_enqueue_resolves_relative_artifacts_from_task_workdir() -> Result<()> {
    let temp = tempdir()?;
    let workdir = temp.path().join("packet");
    fs::create_dir_all(&workdir)?;
    fs::write(workdir.join("plan.json"), b"{}")?;

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
            "true",
            "--workdir",
            workdir.to_str().expect("utf-8 workdir"),
            "--artifact",
            "plan=plan.json",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let task: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        task["artifact_refs"][0]["path"].as_str(),
        workdir.join("plan.json").to_str()
    );
    assert_eq!(task["artifact_refs"][0]["present"], true);
    Ok(())
}

#[test]
#[serial]
fn offdesk_enqueue_tasks_json_redacts_command() -> Result<()> {
    let temp = tempdir()?;
    write_implementation_packet_fixture(temp.path(), "project", "packet-offdesk-test")?;

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
    assert_eq!(
        task["implementation_packet"]["packet_id"],
        "packet-offdesk-test"
    );
    assert_eq!(task["implementation_packet"]["outcome"], "pass");
    assert_eq!(task["implementation_packet"]["safe_to_delegate"], true);
    assert!(task["artifact_refs"]
        .as_array()
        .expect("artifact refs")
        .iter()
        .any(
            |artifact| artifact["artifact_id"] == "implementation_packet"
                && artifact["path"]
                    .as_str()
                    .expect("implementation packet path")
                    .contains("IMPLEMENTATION_PACKET.json")
        ));
    assert!(!task["command"]
        .as_str()
        .expect("command")
        .contains("sk-secret"));

    let tasks_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--json"])
        .output()?;
    assert!(tasks_output.status.success());
    let tasks: serde_json::Value = serde_json::from_slice(&tasks_output.stdout)?;
    assert_eq!(
        tasks[0]["implementation_packet"]["packet_id"],
        "packet-offdesk-test"
    );
    assert!(!tasks[0]["command"]
        .as_str()
        .expect("command")
        .contains("sk-secret"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_enqueue_and_tick_presentation_redacts_without_moving_authority() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let project_key = "project".to_string();
    let request_id = format!("request-token={secret}");
    let json_task_id = format!("task-json-token={secret}");
    let human_task_id = format!("task-human-token={secret}");
    let packet_id = "packet-offdesk-test".to_string();
    let provider_id = format!("provider-token={secret}");
    let model = format!("model-token={secret}");
    let artifact_kind = format!("report-token={secret}");
    let preview = format!("Launch preview token={secret}");
    let reason = format!("Operator gate token={secret}");
    let command = format!("printf token={secret}");
    let workdir = temp.path().join(format!("work-token={secret}"));
    fs::create_dir_all(&workdir)?;
    let packet_dir = write_implementation_packet_fixture(temp.path(), &project_key, &packet_id)?;

    let enqueue_json = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            &project_key,
            "--request-id",
            &request_id,
            "--task-id",
            &json_task_id,
            "--cmd",
            &command,
            "--workdir",
            workdir.to_str().expect("utf-8 workdir"),
            "--provider-id",
            &provider_id,
            "--model",
            &model,
            "--artifact-kind",
            &artifact_kind,
            "--implementation-packet",
            packet_dir.to_str().expect("utf-8 packet directory"),
            "--preview",
            &preview,
            "--reason",
            &reason,
            "--json",
        ])
        .output()?;
    assert!(
        enqueue_json.status.success(),
        "{}",
        String::from_utf8_lossy(&enqueue_json.stderr)
    );
    let enqueue_json_stdout = String::from_utf8_lossy(&enqueue_json.stdout);
    assert!(!enqueue_json_stdout.contains(secret));
    let enqueued: serde_json::Value = serde_json::from_slice(&enqueue_json.stdout)?;
    for field in [
        "task_id",
        "request_id",
        "workdir",
        "provider_id",
        "model",
        "artifact_kind",
        "preview",
        "reason",
    ] {
        assert!(enqueued[field]
            .as_str()
            .expect("redacted task field")
            .contains("[REDACTED]"));
    }
    assert_eq!(
        enqueued["implementation_packet"]["packet_id"],
        "packet-offdesk-test"
    );
    assert!(enqueued["artifact_refs"]
        .as_array()
        .expect("artifact refs")
        .iter()
        .all(|artifact| !artifact.to_string().contains(secret)));

    let enqueue_human = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            &project_key,
            "--request-id",
            &request_id,
            "--task-id",
            &human_task_id,
            "--cmd",
            &command,
            "--workdir",
            workdir.to_str().expect("utf-8 workdir"),
            "--implementation-packet",
            packet_dir.to_str().expect("utf-8 packet directory"),
        ])
        .output()?;
    assert!(enqueue_human.status.success());
    let enqueue_human_stdout = String::from_utf8_lossy(&enqueue_human.stdout);
    assert!(enqueue_human_stdout.contains("Enqueued offdesk task task-human-token=[REDACTED]"));
    assert!(enqueue_human_stdout.contains("packet-offdesk-test (pass)"));
    assert!(!enqueue_human_stdout.contains(secret));

    let profile_dir = profile_dir(temp.path());
    let tasks_path = profile_dir.join("offdesk_tasks.json");
    let tasks_before_tick = fs::read(&tasks_path)?;
    let persisted: serde_json::Value = serde_json::from_slice(&tasks_before_tick)?;
    assert_eq!(persisted.as_array().expect("tasks").len(), 2);
    assert!(persisted
        .as_array()
        .expect("tasks")
        .iter()
        .any(|task| task["task_id"] == json_task_id
            && task["project_key"] == project_key
            && task["request_id"] == request_id
            && task["provider_id"] == provider_id
            && task["model"] == model
            && task["command"] == command));

    let pause_reason = format!("emergency token={secret}");
    let pause = forager_command(temp.path())
        .args([
            "offdesk",
            "pause",
            "--reason",
            &pause_reason,
            "--by",
            "test",
            "--json",
        ])
        .output()?;
    assert!(pause.status.success());

    let paused_tick = forager_command(temp.path())
        .args(["offdesk", "tick", "--limit", "5"])
        .output()?;
    assert!(paused_tick.status.success());
    let paused_tick_stdout = String::from_utf8_lossy(&paused_tick.stdout);
    assert!(paused_tick_stdout.contains("dispatch paused: 2 held"));
    assert!(paused_tick_stdout.contains("pause reason: emergency token=[REDACTED]"));
    assert!(!paused_tick_stdout.contains(secret));
    assert_eq!(fs::read(&tasks_path)?, tasks_before_tick);

    let unpause = forager_command(temp.path())
        .args(["offdesk", "unpause", "--by", "test", "--json"])
        .output()?;
    assert!(unpause.status.success());

    let tick_json = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--project-key",
            &project_key,
            "--task-id",
            &json_task_id,
            "--json",
        ])
        .output()?;
    assert!(tick_json.status.success());
    let tick_json_stdout = String::from_utf8_lossy(&tick_json.stdout);
    assert!(!tick_json_stdout.contains(secret));
    let tick: serde_json::Value = serde_json::from_slice(&tick_json.stdout)?;
    assert_eq!(tick["pending_approval"], 1);
    assert!(tick["updated_task_ids"][0]
        .as_str()
        .expect("updated task id")
        .contains("[REDACTED]"));

    let tick_human = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--project-key",
            &project_key,
            "--task-id",
            &human_task_id,
        ])
        .output()?;
    assert!(tick_human.status.success());
    let tick_human_stdout = String::from_utf8_lossy(&tick_human.stdout);
    assert!(tick_human_stdout.contains("1 pending approval"));
    assert!(tick_human_stdout.contains("updated tasks: task-human-token=[REDACTED]"));
    assert!(tick_human_stdout.contains("Next safe actions:"));
    assert!(!tick_human_stdout.contains(secret));

    let persisted: serde_json::Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert!(persisted
        .as_array()
        .expect("tasks")
        .iter()
        .all(|task| task["status"] == "pending_approval"));
    assert!(persisted
        .as_array()
        .expect("tasks")
        .iter()
        .any(|task| task["task_id"] == json_task_id));
    assert!(persisted
        .as_array()
        .expect("tasks")
        .iter()
        .any(|task| task["task_id"] == human_task_id));

    let approvals: serde_json::Value = serde_json::from_slice(&fs::read(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 2);
    assert!(approvals
        .as_array()
        .expect("approvals")
        .iter()
        .any(|approval| approval["task_id"] == json_task_id));
    assert!(approvals
        .as_array()
        .expect("approvals")
        .iter()
        .any(|approval| approval["task_id"] == human_task_id));
    let background_runs: serde_json::Value =
        serde_json::from_slice(&fs::read(profile_dir.join("background_runs.json"))?)?;
    assert!(background_runs
        .as_array()
        .expect("background runs")
        .is_empty());
    Ok(())
}

#[test]
#[serial]
fn offdesk_tasks_json_filters_by_project_status_task_and_latest() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let mut other_project = durable_task_with(
        "other-completed",
        "dispatch.runtime",
        "completed",
        now + Duration::seconds(3),
        "true",
        temp.path(),
    );
    other_project["project_key"] = json!("other");
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            durable_task_with(
                "queued-task",
                "dispatch.runtime",
                "queued",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "completed-old",
                "dispatch.runtime",
                "completed",
                now + Duration::seconds(1),
                "true",
                temp.path(),
            ),
            durable_task_with(
                "completed-new",
                "dispatch.runtime",
                "completed",
                now + Duration::seconds(2),
                "true",
                temp.path(),
            ),
            other_project
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "tasks",
            "--json",
            "--project-key",
            "project",
            "--status",
            "completed",
        ])
        .output()?;
    assert!(output.status.success());
    let tasks: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let task_ids: Vec<_> = tasks
        .as_array()
        .expect("tasks array")
        .iter()
        .map(|task| task["task_id"].as_str().expect("task id"))
        .collect();
    assert_eq!(task_ids, vec!["completed-old", "completed-new"]);

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "tasks",
            "--json",
            "--project-key",
            "project",
            "--status",
            "completed",
            "--latest",
        ])
        .output()?;
    assert!(output.status.success());
    let tasks: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let task_ids: Vec<_> = tasks
        .as_array()
        .expect("tasks array")
        .iter()
        .map(|task| task["task_id"].as_str().expect("task id"))
        .collect();
    assert_eq!(task_ids, vec!["completed-new"]);

    let output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--json", "--task-id", "queued-task"])
        .output()?;
    assert!(output.status.success());
    let tasks: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(tasks.as_array().expect("tasks array").len(), 1);
    assert_eq!(tasks[0]["task_id"], "queued-task");
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
    assert!(stdout.contains("forager offdesk tick"));
    assert!(stdout.contains("forager offdesk cancel-task queued-task"));
    assert!(stdout.contains("verify closeout before treating Offdesk output as accepted"));
    assert!(stdout.contains("forager offdesk closeout --project-key project --task-id done-task"));
    assert!(!stdout.contains("sk-secret"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_debug_bundle_json_redacts_legacy_state_and_is_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let runner_context =
        "<!-- FORAGER:RUNNER_CONTEXT_BEGIN -->runner-only hidden<!-- FORAGER:RUNNER_CONTEXT_END -->";

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
                "preview": format!("preview token={secret} {runner_context}"),
                "reason": "https://example.com?access_token=secret123",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test",
                "metadata": {
                    "kind": "provider_fallback",
                    "current_provider_id": "openai",
                    "current_model": "gpt-4.1",
                    "runner_role": "worker",
                    "generated_at": now,
                    "candidate_limit": 3,
                    "candidates": [
                        {
                            "provider_id": "openai",
                            "model": "gpt-4.1-mini",
                            "source": "same_provider_model",
                            "auth_status": "available",
                            "capacity_status": "available",
                            "recommended": true,
                            "reason": format!("same provider token={secret}")
                        }
                    ],
                    "apply_scope": "request_matching_provider_model"
                }
            }
        ]))?,
    )?;
    let mut task = durable_task(
        "failed",
        now,
        &format!("printf token={secret}"),
        temp.path(),
    );
    task["last_provider_fallback"] = json!({
        "current_provider_id": "openai",
        "current_model": "gpt-4.1",
        "trigger_reason": format!("cooldown token={secret}"),
        "generated_at": now,
        "candidates": [
            {
                "provider_id": "anthropic",
                "model": "claude-3-5-sonnet-latest",
                "source": "cross_provider_fallback_model",
                "auth_status": "missing_auth",
                "capacity_status": "available",
                "recommended": false,
                "reason": format!("auth token={secret}")
            }
        ]
    });
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([task]))?,
    )?;
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "resume_pending",
                "phase": "background",
                "runner_target": "local_background",
                "last_evidence_artifacts": [],
                "evidence": [
                    {
                        "kind": "log_tail",
                        "summary": format!("tail token={secret} {runner_context}"),
                        "observed_at": now
                    }
                ],
                "last_log_tail": format!("tail token={secret}"),
                "next_safe_resume_step": "inspect result sidecar",
                "interrupted_at": now,
                "interruption_reason": format!("restart token={secret}"),
                "fresh_until": now + Duration::minutes(10)
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
                "launch_spec_summary": format!("cmd token={secret}"),
                "runtime_handle_alive": false,
                "last_log_tail": format!("log token={secret} {runner_context}")
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-test",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now + Duration::minutes(1),
                "last_error_summary": format!("provider token={secret}"),
                "updated_at": now
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--json"])
        .output()?;

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains(secret));
    assert!(!stdout.contains("secret123"));
    assert!(!stdout.contains("runner-only hidden"));
    let bundle: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(bundle["read_only"], true);
    assert_eq!(bundle["redaction_applied"], true);
    assert!(
        bundle["redaction_summary"]["secrets_redacted"]
            .as_u64()
            .unwrap_or(0)
            > 0
    );
    assert!(
        bundle["redaction_summary"]["runner_context_removed"]
            .as_u64()
            .unwrap_or(0)
            > 0
    );
    assert_eq!(bundle["tasks"][0]["status"], "failed");
    assert_eq!(bundle["tasks"][0]["mode_verdict"], "unscoped");
    assert_eq!(bundle["tasks"][0]["mode_risk"], "missing_agent_mode");
    assert_eq!(
        bundle["tasks"][0]["last_provider_fallback"]["current_provider_id"],
        "openai"
    );
    assert!(
        !bundle["tasks"][0]["last_provider_fallback"]["trigger_reason"]
            .as_str()
            .expect("trigger reason")
            .contains(secret)
    );
    assert_eq!(
        bundle["background_runs"][0]["decision"]["phase"],
        "stale_lost_callback"
    );
    assert_eq!(bundle["background_runs"][0]["mode_verdict"], "unscoped");
    assert_eq!(
        bundle["background_runs"][0]["mode_risk"],
        "missing_agent_mode"
    );

    let stored_approvals = fs::read_to_string(profile_dir.join("pending_action_approvals.json"))?;
    assert!(stored_approvals.contains(secret));
    assert!(!profile_dir.join("action_audit.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_maintenance_report_json_is_read_only_and_summarizes_mode_risks() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let result_path = temp.path().join("writing-result.json");
    fs::write(&result_path, "{\"ok\":true}\n")?;

    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": "approval_pending",
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request",
                "task_id": "writing-task",
                "action": "dispatch.runtime",
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": "safe preview",
                "reason": "operator review",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;

    let mut completed_task = durable_task_with(
        "writing-task",
        "dispatch.runtime",
        "completed",
        now,
        "printf secret",
        temp.path(),
    );
    completed_task["agent_mode"] = json!("writing");
    completed_task["result_artifact_path"] = json!(result_path.to_str().expect("utf-8 path"));
    let failed_task = durable_task_with(
        "unscoped-task",
        "dispatch.runtime",
        "failed",
        now,
        "printf secret",
        temp.path(),
    );
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([completed_task, failed_task]))?,
    )?;

    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([resume_state(now)]))?,
    )?;
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-test",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now + Duration::minutes(5),
                "last_error_summary": "rate limited",
                "updated_at": now
            }
        ]))?,
    )?;
    let background_runs_json = serde_json::to_string_pretty(&json!([
        {
            "ticket_id": "ticket",
            "runner_kind": "local_background",
            "phase": "launched",
            "runtime_handle_alive": false
        }
    ]))?;
    fs::write(
        profile_dir.join("background_runs.json"),
        &background_runs_json,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "maintenance-report", "--json"])
        .output()?;

    assert!(output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["read_only"], true);
    assert_eq!(report["tasks"]["total"], 2);
    assert_eq!(report["tasks"]["by_status"]["completed"], 1);
    assert_eq!(
        report["tasks"]["mode"]["by_risk"]["operator_review_required"],
        1
    );
    assert_eq!(report["tasks"]["missing_agent_mode"], 1);
    assert_eq!(report["approvals"]["pending"], 1);
    assert_eq!(report["resume_states"]["total"], 1);
    assert_eq!(report["provider_capacity"]["attention"], 1);
    assert_eq!(report["background_runs"]["total"], 1);
    assert_eq!(report["background_runs"]["missing_agent_mode"], 1);
    let action_kinds = report["recommended_actions"]
        .as_array()
        .expect("actions")
        .iter()
        .filter_map(|action| action["kind"].as_str())
        .collect::<Vec<_>>();
    assert!(action_kinds.contains(&"pending_approval"));
    assert!(action_kinds.contains(&"operator_review"));
    assert!(action_kinds.contains(&"missing_agent_mode"));
    assert!(action_kinds.contains(&"provider_capacity"));
    let next_action_kinds = report["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .filter_map(|action| action["kind"].as_str())
        .collect::<Vec<_>>();
    assert!(next_action_kinds.contains(&"approval_pending"));
    assert!(next_action_kinds.contains(&"review_required"));
    assert!(next_action_kinds.contains(&"provider_attention"));
    assert!(report["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .all(|action| action["requires_operator_review"] == true));
    assert_eq!(
        fs::read_to_string(profile_dir.join("background_runs.json"))?,
        background_runs_json
    );
    assert!(!profile_dir.join("action_audit.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_maintenance_request_creates_deduped_approval_without_execution() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let secret = "sk-secretsecretsecretsecret";

    let first = forager_command(temp.path())
        .args([
            "offdesk",
            "maintenance-request",
            "--kind",
            "artifact-cleanup",
            "--project-key",
            "project",
            "--request-id",
            "nightly-review",
            "--target-id",
            "debug/bundles",
            "--preview",
            &format!("Remove old debug bundles token={secret}"),
            "--reason",
            "operator wants to clean reviewed diagnostics",
            "--json",
        ])
        .output()?;
    assert!(first.status.success());
    let first_report: serde_json::Value = serde_json::from_slice(&first.stdout)?;
    assert_eq!(first_report["status"], "pending_approval");
    assert_eq!(first_report["action"], "maintenance.artifact_cleanup");
    assert_eq!(first_report["risk_level"], "destructive");
    assert_eq!(
        first_report["task_id"],
        "maintenance-artifact-cleanup-debug-bundles"
    );
    assert_eq!(first_report["approval"]["status"], "pending");
    assert!(!first_report["approval"]["preview"]
        .as_str()
        .expect("preview")
        .contains(secret));
    let approval_id = first_report["approval"]["approval_id"]
        .as_str()
        .expect("approval id")
        .to_string();

    let second = forager_command(temp.path())
        .args([
            "offdesk",
            "maintenance-request",
            "--kind",
            "artifact-cleanup",
            "--project-key",
            "project",
            "--request-id",
            "nightly-review",
            "--target-id",
            "debug/bundles",
            "--preview",
            "Remove old debug bundles",
            "--reason",
            "operator wants to clean reviewed diagnostics",
            "--json",
        ])
        .output()?;
    assert!(second.status.success());
    let second_report: serde_json::Value = serde_json::from_slice(&second.stdout)?;
    assert_eq!(
        second_report["approval"]["approval_id"]
            .as_str()
            .expect("approval id"),
        approval_id
    );

    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert_eq!(approvals[0]["action"], "maintenance.artifact_cleanup");
    assert_eq!(approvals[0]["risk_level"], "destructive");
    assert_eq!(
        approvals[0]["task_id"],
        "maintenance-artifact-cleanup-debug-bundles"
    );
    assert!(!profile_dir.join("offdesk_tasks.json").exists());
    assert!(!profile_dir.join("background_runs.json").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_maintenance_presentation_redacts_outputs_and_preserves_approval_authority() -> Result<()>
{
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let secret = "sk-secretsecretsecretsecret";
    let project_key = format!("project-token={secret}");
    let request_id = format!("request-token={secret}");
    let task_id = format!("task-token={secret}");
    let target_id = format!("debug-token={secret}");
    let preview = format!("Remove old bundles token={secret}");
    let reason = format!("Operator review token={secret}");
    let source_surface = "legacy-test".to_string();

    let json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "maintenance-request",
            "--kind",
            "artifact-cleanup",
            "--project-key",
            &project_key,
            "--request-id",
            &request_id,
            "--task-id",
            &task_id,
            "--target-id",
            &target_id,
            "--preview",
            &preview,
            "--reason",
            &reason,
            "--source-surface",
            &source_surface,
            "--json",
        ])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let request_report: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert!(request_report["project_key"]
        .as_str()
        .expect("project key")
        .contains("[REDACTED]"));
    assert!(request_report["approval"]["preview"]
        .as_str()
        .expect("approval preview")
        .contains("[REDACTED]"));

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "maintenance-request",
            "--kind",
            "artifact-cleanup",
            "--project-key",
            &project_key,
            "--request-id",
            &request_id,
            "--task-id",
            &task_id,
            "--target-id",
            &target_id,
            "--preview",
            &preview,
            "--reason",
            &reason,
            "--source-surface",
            &source_surface,
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Offdesk maintenance approval request"));
    assert!(human_stdout.contains("project_key:  project-token=[REDACTED]"));
    assert!(human_stdout.contains("request_id:   request-token=[REDACTED]"));
    assert!(human_stdout.contains("task_id:      task-token=[REDACTED]"));
    assert!(human_stdout.contains("target_id:    debug-token=[REDACTED]"));
    assert!(!human_stdout.contains(secret));

    let approvals_path = profile_dir.join("pending_action_approvals.json");
    let approvals: serde_json::Value = serde_json::from_slice(&fs::read(&approvals_path)?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert_eq!(approvals[0]["project_key"], project_key);
    assert_eq!(approvals[0]["request_id"], request_id);
    assert_eq!(approvals[0]["task_id"], task_id);
    assert_eq!(
        approvals[0]["preview"],
        "Remove old bundles token=[REDACTED]"
    );
    assert_eq!(approvals[0]["reason"], "Operator review token=[REDACTED]");
    assert_eq!(approvals[0]["source_surface"], source_surface);
    let approvals_before_reports = fs::read(&approvals_path)?;

    let report_json_output = forager_command(temp.path())
        .args(["offdesk", "maintenance-report", "--json"])
        .output()?;
    assert!(report_json_output.status.success());
    let report_json_stdout = String::from_utf8_lossy(&report_json_output.stdout);
    assert!(!report_json_stdout.contains(secret));
    let report: serde_json::Value = serde_json::from_slice(&report_json_output.stdout)?;
    assert_eq!(report["read_only"], true);
    assert_eq!(report["approvals"]["pending"], 1);

    let report_human_output = forager_command(temp.path())
        .args(["offdesk", "maintenance-report"])
        .output()?;
    assert!(report_human_output.status.success());
    let report_human_stdout = String::from_utf8_lossy(&report_human_output.stdout);
    assert!(report_human_stdout.contains("Offdesk maintenance report"));
    assert!(report_human_stdout.contains("Recommended actions:"));
    assert!(report_human_stdout.contains("pending_approval"));
    assert!(!report_human_stdout.contains(secret));

    assert_eq!(fs::read(&approvals_path)?, approvals_before_reports);
    assert!(!profile_dir.join("offdesk_tasks.json").exists());
    assert!(!profile_dir.join("background_runs.json").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_debug_bundle_includes_wiki_attention_summaries_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_bundle_expired",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Expired bundle review",
                    "ai_instruction": "Expired bundle guidance.",
                    "human_summary": "Expired review entry.",
                    "evidence_refs": ["audit:expired"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::days(1)
                },
                {
                    "id": "wiki_bundle_near",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Near bundle review",
                    "ai_instruction": "Near bundle guidance.",
                    "human_summary": "Near review entry.",
                    "evidence_refs": ["audit:near"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now + Duration::hours(12)
                },
                {
                    "id": "wiki_bundle_missing",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Missing bundle review",
                    "ai_instruction": "Missing review_after guidance.",
                    "human_summary": "Missing review entry.",
                    "evidence_refs": ["audit:missing"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
        }))?,
    )?;

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--artifact-kind",
            "report",
            "--ttl-hours",
            "1",
            "--reason",
            "bundle attention summary seed",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let near_ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    let mut expired_ack = near_ack.clone();
    expired_ack["id"] = json!("wiki_runtime_policy_ack_expired_for_bundle");
    expired_ack["created_at"] = serde_json::to_value(now - Duration::hours(2))?;
    expired_ack["expires_at"] = serde_json::to_value(now - Duration::hours(1))?;
    fs::write(
        profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl"),
        format!(
            "{}\n{}\n",
            serde_json::to_string(&near_ack)?,
            serde_json::to_string(&expired_ack)?
        ),
    )?;

    let before_entries = fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?;
    let before_acks = fs::read_to_string(
        profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl"),
    )?;
    let bundle_output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--json"])
        .output()?;
    assert!(bundle_output.status.success());
    let bundle: serde_json::Value = serde_json::from_slice(&bundle_output.stdout)?;
    assert_eq!(
        bundle["adaptive_wiki_review_after_attention_summary"]["expired"],
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_review_after_attention_summary"]["near_expiry"],
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_review_after_attention_summary"]["missing_review_after"],
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_review_after_attention_summary"]["attention"],
        2
    );
    assert_eq!(
        bundle["adaptive_wiki_runtime_policy_ack_attention_summary"]["total"],
        2
    );
    assert_eq!(
        bundle["adaptive_wiki_runtime_policy_ack_attention_summary"]["expired"],
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_runtime_policy_ack_attention_summary"]["near_expiry"],
        1
    );
    assert_eq!(
        bundle["adaptive_wiki_runtime_policy_ack_attention_summary"]["suggested_actions"],
        2
    );
    assert_eq!(
        fs::read_to_string(profile_dir.join("adaptive_wiki_entries.json"))?,
        before_entries
    );
    assert_eq!(
        fs::read_to_string(
            profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl")
        )?,
        before_acks
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_debug_bundle_export_writes_sanitized_profile_artifact() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let runner_context =
        "<!-- FORAGER:RUNNER_CONTEXT_BEGIN -->runner-only hidden<!-- FORAGER:RUNNER_CONTEXT_END -->";
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": "approval_export",
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request",
                "task_id": "task",
                "action": "dispatch.runtime",
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": format!("preview token={secret} {runner_context}"),
                "reason": "export test",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--export"])
        .output()?;

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("exported_to:"));
    assert!(stdout.contains("bytes_written:"));
    assert!(!stdout.contains(secret));
    assert!(!stdout.contains("runner-only hidden"));

    let export_dir = profile_dir.join("debug_bundles");
    let mut exports = fs::read_dir(&export_dir)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<std::io::Result<Vec<_>>>()?;
    exports.sort();
    assert_eq!(exports.len(), 1);
    let export_name = exports[0]
        .file_name()
        .expect("export file name")
        .to_string_lossy();
    assert!(export_name.starts_with("offdesk_debug_bundle_"));
    assert!(export_name.ends_with(".json"));
    let exported = fs::read_to_string(&exports[0])?;
    assert!(!exported.contains(secret));
    assert!(!exported.contains("runner-only hidden"));
    let bundle: serde_json::Value = serde_json::from_str(&exported)?;
    assert_eq!(bundle["read_only"], true);
    assert_eq!(bundle["redaction_applied"], true);
    assert!(
        bundle["redaction_summary"]["secrets_redacted"]
            .as_u64()
            .unwrap_or(0)
            > 0
    );

    let second_output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--export"])
        .output()?;
    assert!(second_output.status.success());
    let exports_after_second = fs::read_dir(&export_dir)?.count();
    assert_eq!(exports_after_second, 2);

    let stored_approvals = fs::read_to_string(profile_dir.join("pending_action_approvals.json"))?;
    assert!(stored_approvals.contains(secret));
    Ok(())
}

#[test]
#[serial]
fn offdesk_debug_bundle_output_json_receipt_writes_custom_file() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let approvals_path = profile_dir.join("pending_action_approvals.json");
    fs::write(
        &approvals_path,
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": "approval_custom_export",
                "status": "pending",
                "scope": "once",
                "project_key": "project",
                "request_id": "request",
                "task_id": "task",
                "action": "dispatch.runtime",
                "risk_level": "runtime_mutation",
                "approval_mode": "operator_required",
                "preview": format!("preview token={secret}"),
                "reason": "custom export test",
                "created_at": now,
                "expires_at": now + Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;
    let approvals_before = fs::read(&approvals_path)?;
    let output_path = temp
        .path()
        .join("exports")
        .join(format!("custom-bundle-token={secret}.json"));

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "debug-bundle",
            "--output",
            output_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains(secret));
    let receipt: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert!(receipt["exported_to"]
        .as_str()
        .expect("export path")
        .contains("[REDACTED]"));
    assert_eq!(receipt["bundle"]["read_only"], true);
    assert_eq!(
        receipt["bytes_written"].as_u64().expect("bytes written"),
        fs::metadata(&output_path)?.len()
    );
    let exported_bundle: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&output_path)?)?;
    assert_eq!(exported_bundle, receipt["bundle"]);
    assert!(!fs::read_to_string(&output_path)?.contains(secret));
    assert_eq!(fs::read(&approvals_path)?, approvals_before);

    let human_output_path = temp
        .path()
        .join("exports")
        .join(format!("human-bundle-token={secret}.json"));
    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "debug-bundle",
            "--output",
            human_output_path.to_str().expect("utf-8 path"),
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Offdesk debug bundle"));
    assert!(human_stdout.contains("exported_to:"));
    assert!(human_stdout.contains("[REDACTED]"));
    assert!(!human_stdout.contains(secret));
    assert!(human_output_path.exists());
    assert!(!fs::read_to_string(&human_output_path)?.contains(secret));
    assert_eq!(fs::read(&approvals_path)?, approvals_before);

    let overwrite_output = forager_command(temp.path())
        .args([
            "offdesk",
            "debug-bundle",
            "--output",
            output_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(!overwrite_output.status.success());
    let stderr = String::from_utf8_lossy(&overwrite_output.stderr);
    assert!(stderr.contains("write debug bundle export"));
    assert!(stderr.contains("exists") || stderr.contains("AlreadyExists"));
    assert!(stderr.contains("[REDACTED]"));
    assert!(!stderr.contains(secret));
    assert_eq!(fs::read(&approvals_path)?, approvals_before);
    Ok(())
}

#[test]
#[serial]
fn offdesk_debug_bundle_empty_profile_does_not_create_storage() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args(["offdesk", "debug-bundle", "--json"])
        .output()?;

    assert!(output.status.success());
    let bundle: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(bundle["read_only"], true);
    assert_eq!(bundle["approvals"].as_array().expect("approvals").len(), 0);
    assert_eq!(bundle["tasks"].as_array().expect("tasks").len(), 0);
    assert!(!app_dir(temp.path()).exists());
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
fn configured_default_profile_is_used_when_no_profile_is_provided() -> Result<()> {
    let temp = tempdir()?;

    let create_output = forager_command(temp.path())
        .args(["profile", "create", "work"])
        .output()?;
    assert!(create_output.status.success());

    let default_output = forager_command(temp.path())
        .args(["profile", "default", "work"])
        .output()?;
    assert!(default_output.status.success());

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());

    assert!(profile_dir_for(temp.path(), "work").exists());
    assert!(!profile_dir(temp.path()).exists());
    Ok(())
}

#[test]
#[serial]
fn profile_argument_rejects_path_traversal() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args(["-p", "../../outside", "list"])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("Profile name cannot contain path separators"));
    assert!(!temp.path().join(".config").join("outside").exists());
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
    assert_eq!(report["profile"]["dir_source"], "new_primary");
    assert_eq!(
        reported_path(&report["profile"]["dir"]),
        expected_path(&app_dir(home.path()).join("profiles").join("default"))
    );
    assert_eq!(
        reported_path(&report["profile"]["primary_dir"]),
        expected_path(&app_dir(home.path()).join("profiles").join("default"))
    );
    assert_eq!(report["profile"]["primary_exists"], false);
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
    assert_eq!(report["profile"]["active"], "default");
    assert_eq!(report["profile"]["dir_source"], "legacy");
    assert_eq!(
        reported_path(&report["profile"]["dir"]),
        expected_path(&legacy_app_dir(home.path()).join("profiles").join("default"))
    );
    assert_eq!(
        reported_path(&report["profile"]["primary_dir"]),
        expected_path(&app_dir(home.path()).join("profiles").join("default"))
    );
    assert_eq!(report["profile"]["primary_exists"], false);
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
    assert_eq!(report["profile"]["dir_source"], "new_primary");
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
    assert!(complete["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "review_required"));
    assert!(!complete["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "runtime_monitoring"));

    let tasks: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("offdesk_tasks.json"),
    )?)?;
    assert_eq!(tasks[0]["status"], "completed");
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_project_task_filter_dispatches_only_matching_task() -> Result<()> {
    let temp = tempdir()?;
    let brief_path = temp.path().join("target-brief.json");
    let target_result_path = temp.path().join("target-result.txt");
    let other_result_path = temp.path().join("other-result.txt");
    let now = Utc::now();
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "target-request",
            "task_id": "target-task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(10)
        }))?,
    )?;
    let target_command = format!("printf target > {}", target_result_path.display());
    let other_command = format!("printf other > {}", other_result_path.display());

    let target_enqueue = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "target-request",
            "--task-id",
            "target-task",
            "--brief",
            brief_path.to_str().expect("utf-8 path"),
            "--cmd",
            target_command.as_str(),
            "--workdir",
            temp.path().to_str().expect("utf-8 path"),
            "--result-artifact",
            target_result_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(target_enqueue.status.success());

    let other_enqueue = forager_command(temp.path())
        .args([
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--runner",
            "local-background",
            "--project-key",
            "project",
            "--request-id",
            "other-request",
            "--task-id",
            "other-task",
            "--cmd",
            other_command.as_str(),
            "--workdir",
            temp.path().to_str().expect("utf-8 path"),
            "--result-artifact",
            other_result_path.to_str().expect("utf-8 path"),
            "--json",
        ])
        .output()?;
    assert!(other_enqueue.status.success());

    let tick_output = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--project-key",
            "project",
            "--task-id",
            "target-task",
            "--limit",
            "1",
            "--json",
        ])
        .output()?;
    assert!(
        tick_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&tick_output.stdout),
        String::from_utf8_lossy(&tick_output.stderr)
    );
    let tick: serde_json::Value = serde_json::from_slice(&tick_output.stdout)?;
    assert_eq!(tick["launched"], 1);
    assert_eq!(tick["pending_approval"], 0);
    assert_eq!(tick["updated_task_ids"], json!(["target-task"]));

    let tasks: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir(temp.path()).join("offdesk_tasks.json"),
    )?)?;
    let target = tasks
        .as_array()
        .expect("tasks")
        .iter()
        .find(|task| task["task_id"] == "target-task")
        .expect("target task");
    let other = tasks
        .as_array()
        .expect("tasks")
        .iter()
        .find(|task| task["task_id"] == "other-task")
        .expect("other task");
    assert_eq!(target["status"], "launched");
    assert_eq!(other["status"], "queued");
    assert!(target["background_ticket_id"].is_string());
    assert!(other["background_ticket_id"].is_null());
    assert!(
        !profile_dir(temp.path())
            .join("pending_action_approvals.json")
            .exists(),
        "scoped tick must not create approval for the non-target task"
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_emits_runner_work_slice_receipts_for_packet() -> Result<()> {
    let temp = tempdir()?;
    write_implementation_packet_fixture(temp.path(), "project", "packet-runner-receipts")?;
    fs::write(temp.path().join("README.md"), "# Project\n")?;
    fs::write(
        temp.path().join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(temp.path().join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        temp.path().join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: project overview.\n",
    )?;

    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("tick-packet-result.txt");
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

    let launch_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(launch_output.status.success());
    wait_for_path(&result_path);

    let complete_output = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(complete_output.status.success());
    let complete: serde_json::Value = serde_json::from_slice(&complete_output.stdout)?;
    assert_eq!(complete["completed"], 1);

    let receipt_path = temp.path().join("work_slice_receipts.jsonl");
    let receipt_jsonl = fs::read_to_string(&receipt_path)?;
    let receipts = receipt_jsonl
        .lines()
        .map(serde_json::from_str::<serde_json::Value>)
        .collect::<std::result::Result<Vec<_>, _>>()?;
    assert_eq!(receipts.len(), 2);
    assert!(receipts.iter().all(|receipt| {
        receipt["schema"] == "work_slice_execution_receipt.v1"
            && receipt["packet_id"] == "packet-runner-receipts"
            && receipt["producer"] == "runner_poll"
            && receipt["status"] == "deferred"
            && receipt["next_safe_action"]
                .as_str()
                .unwrap_or_default()
                .contains("before accepting truth")
    }));

    let repeat_tick = forager_command(temp.path())
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(repeat_tick.status.success());
    assert_eq!(fs::read_to_string(&receipt_path)?.lines().count(), 2);

    let closeout_output = forager_command(temp.path())
        .args([
            "offdesk",
            "closeout",
            "--project-key",
            "project",
            "--task-id",
            "task",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(
        closeout_output.status.success(),
        "{}",
        String::from_utf8_lossy(&closeout_output.stderr)
    );
    let closeout: serde_json::Value = serde_json::from_slice(&closeout_output.stdout)?;
    assert_eq!(closeout["summary"]["packet_goals_completed"], 1);
    assert_eq!(closeout["summary"]["packet_detail_items_deferred"], 2);
    assert_eq!(
        closeout["implementation_packet_coverage"]["items"][0]["detail_source"],
        "implementation_packet_and_work_slice_receipts"
    );
    assert!(
        closeout["implementation_packet_coverage"]["items"][0]["work_slices"]
            .as_array()
            .expect("work slices")
            .iter()
            .all(|item| item["status"] == "deferred"
                && item["receipt_source"]
                    .as_str()
                    .unwrap_or_default()
                    .ends_with("work_slice_receipts.jsonl"))
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_injects_adaptive_wiki_runtime_context_and_records_usage() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("wiki-runtime-result.txt");
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
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
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_runtime_entry",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Runtime should see report guidance",
                    "ai_instruction": format!("Mention report evidence boundaries token={secret}"),
                    "human_summary": "Human-only wiki note",
                    "evidence_refs": ["task:one"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
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
            "--artifact-kind",
            "report",
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

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(
        runs[0]["adaptive_wiki_entry_ids"],
        json!(["wiki_runtime_entry"])
    );
    assert_eq!(
        runs[0]["adaptive_wiki_runtime_policy"]["review_expired"],
        "warn"
    );
    let context = runs[0]["adaptive_wiki_context"]
        .as_str()
        .expect("runtime wiki context");
    assert!(context.contains("<adaptive-wiki-context>"));
    assert!(context.contains("wiki_runtime_entry"));
    assert!(context.contains("[REDACTED]"));
    assert!(!context.contains(secret));
    assert_eq!(runs[0]["launch_spec_summary"], command);
    assert_eq!(
        runs[0]["working_dir"],
        temp.path().to_str().expect("utf-8 path")
    );

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["command"], command);
    assert_eq!(
        tasks[0]["workdir"],
        temp.path().to_str().expect("utf-8 path")
    );
    assert_eq!(
        tasks[0]["last_adaptive_wiki_entry_ids"],
        json!(["wiki_runtime_entry"])
    );

    let usage = fs::read_to_string(profile_dir.join("adaptive_wiki_usage.jsonl"))?;
    assert_eq!(usage.lines().count(), 1);
    let usage_record: serde_json::Value = serde_json::from_str(usage.lines().next().unwrap())?;
    assert_eq!(usage_record["entry_id"], "wiki_runtime_entry");
    assert_eq!(usage_record["task_id"], "task");
    assert_eq!(usage_record["request_id"], "request");
    assert_eq!(usage_record["project_key"], "project");
    assert_eq!(usage_record["artifact_kind"], "report");
    assert_eq!(usage_record["projection_kind"], "runtime_probe");
    assert_eq!(usage_record["projection_policy"]["review_expired"], "warn");
    Ok(())
}

#[test]
#[serial]
fn offdesk_launch_runtime_wiki_kill_switch_keeps_preflight_projection() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
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
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_disabled_runtime_entry",
                    "kind": "procedure",
                    "scope": "project",
                    "scope_ref": "project",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Visible in preflight only",
                    "ai_instruction": "Use only as preflight metadata.",
                    "human_summary": "Human-only note",
                    "evidence_refs": ["task:one"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                }
            ]
        }))?,
    )?;

    let output = forager_command(temp.path())
        .env("FORAGER_ADAPTIVE_WIKI_RUNTIME", "0")
        .args([
            "offdesk",
            "launch",
            "background.launch",
            "--runner",
            "remote-worker",
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
            "--json",
        ])
        .output()?;
    assert!(output.status.success());
    let outcome: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(outcome["gate"]["status"], "proceed");
    assert_eq!(
        outcome["gate"]["adaptive_wiki"][0]["id"],
        "wiki_disabled_runtime_entry"
    );
    assert_eq!(
        outcome["gate"]["adaptive_wiki_runtime"][0]["id"],
        "wiki_disabled_runtime_entry"
    );
    assert_eq!(
        outcome["gate"]["adaptive_wiki_runtime_policy"]["review_expired"],
        "warn"
    );
    assert!(outcome["probe"].get("adaptive_wiki_context").is_none());
    assert!(outcome["probe"].get("adaptive_wiki_entry_ids").is_none());
    assert!(outcome["probe"]
        .get("adaptive_wiki_runtime_policy")
        .is_none());
    assert!(!profile_dir.join("adaptive_wiki_usage.jsonl").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_strict_runtime_wiki_requires_ack_and_excludes_review_expired() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_runtime_expired",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Expired runtime guidance",
                    "ai_instruction": "Use only after review renewal.",
                    "evidence_refs": ["task:expired"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::days(1)
                },
                {
                    "id": "wiki_runtime_fresh",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Fresh runtime guidance",
                    "ai_instruction": "Keep report evidence separate.",
                    "evidence_refs": ["task:fresh"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now + Duration::days(1)
                }
            ]
        }))?,
    )?;

    let missing_ack_output = forager_command(temp.path())
        .env("FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED", "exclude")
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(missing_ack_output.status.success());
    let missing_ack: serde_json::Value = serde_json::from_slice(&missing_ack_output.stdout)?;
    assert_eq!(missing_ack["status"], "proceed");
    let preflight = missing_ack["adaptive_wiki"]
        .as_array()
        .expect("preflight wiki");
    assert!(preflight
        .iter()
        .any(|entry| entry["id"] == "wiki_runtime_expired"));
    assert!(preflight
        .iter()
        .any(|entry| entry["id"] == "wiki_runtime_fresh"));
    assert!(missing_ack["adaptive_wiki_runtime"]
        .as_array()
        .is_none_or(Vec::is_empty));
    assert_eq!(
        missing_ack["adaptive_wiki_runtime_decision"]["status"],
        "strict_requested_missing_acknowledgement"
    );
    assert_eq!(
        missing_ack["adaptive_wiki_runtime_decision"]["requested_policy"]["review_expired"],
        "exclude"
    );

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--session-id",
            "request",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--reason",
            "operator reviewed warn-vs-strict comparison",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    assert_eq!(ack["policy"]["review_expired"], "exclude");
    assert_eq!(
        ack["review_expired_excluded"],
        json!(["wiki_runtime_expired"])
    );
    assert!(ack["comparison_hash"]
        .as_str()
        .is_some_and(|hash| hash.len() == 64));

    let ack_list_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "runtime-policy-acks", "--json"])
        .output()?;
    assert!(ack_list_output.status.success());
    let ack_list: serde_json::Value = serde_json::from_slice(&ack_list_output.stdout)?;
    assert_eq!(ack_list.as_array().expect("ack list").len(), 1);

    let acknowledged_output = forager_command(temp.path())
        .env("FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED", "exclude")
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(acknowledged_output.status.success());
    let acknowledged: serde_json::Value = serde_json::from_slice(&acknowledged_output.stdout)?;
    assert_eq!(
        acknowledged["adaptive_wiki_runtime_decision"]["status"],
        "applied_acknowledged"
    );
    assert_eq!(
        acknowledged["adaptive_wiki_runtime_decision"]["acknowledgement_id"],
        ack["id"]
    );
    let runtime = acknowledged["adaptive_wiki_runtime"]
        .as_array()
        .expect("runtime wiki");
    assert!(runtime
        .iter()
        .any(|entry| entry["id"] == "wiki_runtime_fresh"));
    assert!(!runtime
        .iter()
        .any(|entry| entry["id"] == "wiki_runtime_expired"));
    assert_eq!(
        acknowledged["adaptive_wiki_runtime_policy"]["review_expired"],
        "exclude"
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_renew_review_after_updates_only_review_metadata() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let expired_review_after = now - Duration::days(1);
    let renewed_review_after = now + Duration::days(7);
    let expired_review_after_json = serde_json::to_value(expired_review_after)?;
    let renewed_review_after_json = serde_json::to_value(renewed_review_after)?;
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [{
                "id": "wiki_review_renew",
                "kind": "procedure",
                "scope": "artifact_kind",
                "scope_ref": "report",
                "status": "promoted",
                "activation_mode": "confirm",
                "claim": "Renew review window",
                "ai_instruction": "Keep report review guidance unchanged.",
                "human_summary": "Review renewal target.",
                "evidence_refs": ["audit:renew"],
                "confidence": "explicit",
                "created_at": now,
                "updated_at": now,
                "review_after": expired_review_after
            }]
        }))?,
    )?;

    let renew_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "renew-review-after",
            "wiki_review_renew",
            "--review-after",
            &renewed_review_after.to_rfc3339(),
            "--reason",
            "operator revalidated entry",
            "--json",
        ])
        .output()?;
    assert!(renew_output.status.success());
    let renewed: serde_json::Value = serde_json::from_slice(&renew_output.stdout)?;
    assert_eq!(renewed["action"], "renew_review_after");
    assert_eq!(renewed["entry"]["id"], "wiki_review_renew");
    assert_eq!(
        renewed["entry"]["review_after"],
        renewed_review_after_json.clone()
    );
    assert_eq!(renewed["previous_review_after"], expired_review_after_json);
    assert_eq!(renewed["audit"]["action"], "renew_review_after");

    let stored: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("adaptive_wiki_entries.json"),
    )?)?;
    let entry = &stored["entries"][0];
    assert_eq!(entry["scope"], "artifact_kind");
    assert_eq!(entry["scope_ref"], "report");
    assert_eq!(
        entry["ai_instruction"],
        "Keep report review guidance unchanged."
    );
    assert_eq!(entry["review_after"], renewed_review_after_json);

    let projection_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "projection",
            "--artifact-kind",
            "report",
            "--report",
            "--exclude-review-expired",
            "--json",
        ])
        .output()?;
    assert!(projection_output.status.success());
    let projection: serde_json::Value = serde_json::from_slice(&projection_output.stdout)?;
    assert!(projection["review_expired"]
        .as_array()
        .expect("review_expired")
        .is_empty());
    assert_eq!(projection["selected"][0]["id"], "wiki_review_renew");
    Ok(())
}

#[test]
#[serial]
fn offdesk_wiki_review_after_report_flags_expired_and_near_expiry_entries() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": [
                {
                    "id": "wiki_review_expired",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Expired report review",
                    "ai_instruction": "Expired instruction should not be in attention report.",
                    "human_summary": "Expired review entry.",
                    "evidence_refs": ["audit:expired"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::days(1)
                },
                {
                    "id": "wiki_review_near",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Near expiry report review",
                    "ai_instruction": "Near expiry instruction should not be in attention report.",
                    "human_summary": "Near expiry review entry.",
                    "evidence_refs": ["audit:near"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now + Duration::hours(12)
                },
                {
                    "id": "wiki_review_fresh",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Fresh report review",
                    "ai_instruction": "Fresh review is outside attention window.",
                    "human_summary": "Fresh review entry.",
                    "evidence_refs": ["audit:fresh"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now + Duration::days(10)
                },
                {
                    "id": "wiki_review_missing",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Missing review_after",
                    "ai_instruction": "Missing review_after should be counted but not listed.",
                    "human_summary": "Missing review_after entry.",
                    "evidence_refs": ["audit:missing"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now
                },
                {
                    "id": "wiki_review_deprecated",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "report",
                    "status": "deprecated",
                    "activation_mode": "confirm",
                    "claim": "Deprecated expired review",
                    "ai_instruction": "Deprecated entries are ignored.",
                    "human_summary": "Deprecated review entry.",
                    "evidence_refs": ["audit:deprecated"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::days(1)
                },
                {
                    "id": "wiki_review_other_artifact",
                    "kind": "procedure",
                    "scope": "artifact_kind",
                    "scope_ref": "dataset",
                    "status": "promoted",
                    "activation_mode": "confirm",
                    "claim": "Other artifact review",
                    "ai_instruction": "Other artifact is out of scope.",
                    "human_summary": "Other artifact entry.",
                    "evidence_refs": ["audit:other"],
                    "confidence": "explicit",
                    "created_at": now,
                    "updated_at": now,
                    "review_after": now - Duration::days(1)
                }
            ]
        }))?,
    )?;

    let report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review-after-report",
            "--artifact-kind",
            "report",
            "--near-expiry-hours",
            "24",
            "--json",
        ])
        .output()?;
    assert!(report_output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&report_output.stdout)?;
    assert_eq!(report["summary"]["scoped_promoted"], 4);
    assert_eq!(report["summary"]["with_review_after"], 3);
    assert_eq!(report["summary"]["missing_review_after"], 1);
    assert_eq!(report["summary"]["expired"], 1);
    assert_eq!(report["summary"]["near_expiry"], 1);
    assert_eq!(report["summary"]["attention"], 2);
    assert_eq!(report["entries"][0]["id"], "wiki_review_expired");
    assert_eq!(report["entries"][0]["status"], "expired");
    assert_eq!(report["entries"][1]["id"], "wiki_review_near");
    assert_eq!(report["entries"][1]["status"], "near_expiry");
    assert!(report["entries"][0]["renew_command_template"]
        .as_str()
        .is_some_and(|command| command.contains("renew-review-after 'wiki_review_expired'")));
    let report_json = serde_json::to_string(&report)?;
    assert!(!report_json.contains("Expired instruction should not be in attention report"));
    assert!(!report_json.contains("wiki_review_fresh"));
    assert!(!report_json.contains("wiki_review_deprecated"));
    assert!(!report_json.contains("wiki_review_other_artifact"));

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "review-after-report",
            "--artifact-kind",
            "report",
            "--near-expiry-hours",
            "24",
        ])
        .output()?;
    assert!(human_output.status.success());
    let human = String::from_utf8(human_output.stdout)?;
    assert!(human.contains("Adaptive wiki review_after attention report"));
    assert!(human.contains("wiki_review_expired"));
    assert!(human.contains("wiki_review_near"));
    assert!(human.contains("artifactkind:report"));
    assert!(human.contains(
        "renew: forager offdesk wiki renew-review-after 'wiki_review_expired' --review-after <rfc3339> --reason <reason>"
    ));
    assert!(!human.contains("Expired instruction should not be in attention report"));
    assert!(!human.contains("wiki_review_fresh"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_project_artifact_runtime_ack_reuses_only_without_session_specific_projection(
) -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let base_entries = json!([
        {
            "id": "wiki_project_expired",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Expired project/artifact guidance",
            "ai_instruction": "Use only after project review renewal.",
            "evidence_refs": ["task:expired"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now - Duration::days(1)
        },
        {
            "id": "wiki_project_fresh",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Fresh project/artifact guidance",
            "ai_instruction": "Keep report evidence separate.",
            "evidence_refs": ["task:fresh"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now + Duration::days(1)
        }
    ]);
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": base_entries
        }))?,
    )?;

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--scope-mode",
            "project-artifact",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--reason",
            "operator reviewed project/artifact strict projection",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    assert_eq!(ack["scope_mode"], "project_artifact");
    assert_eq!(ack["query"]["session_id"], serde_json::Value::Null);
    assert_eq!(ack["query"]["project_key"], "project");
    assert_eq!(ack["query"]["artifact_kind"], "report");

    let applied_output = forager_command(temp.path())
        .env("FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED", "exclude")
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request-one",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(applied_output.status.success());
    let applied: serde_json::Value = serde_json::from_slice(&applied_output.stdout)?;
    assert_eq!(
        applied["adaptive_wiki_runtime_decision"]["status"],
        "applied_project_artifact_acknowledged"
    );
    assert_eq!(
        applied["adaptive_wiki_runtime_decision"]["acknowledgement_scope_mode"],
        "project_artifact"
    );
    let runtime = applied["adaptive_wiki_runtime"]
        .as_array()
        .expect("runtime wiki");
    assert!(runtime
        .iter()
        .any(|entry| entry["id"] == "wiki_project_fresh"));
    assert!(!runtime
        .iter()
        .any(|entry| entry["id"] == "wiki_project_expired"));

    let mut entries = base_entries.as_array().expect("entries").clone();
    entries.push(json!({
        "id": "wiki_session_specific",
        "kind": "procedure",
        "scope": "session",
        "scope_ref": "request-two",
        "status": "promoted",
        "activation_mode": "confirm",
        "claim": "Session-specific guidance",
        "ai_instruction": "Request-specific context must be separately reviewed.",
        "evidence_refs": ["task:session"],
        "confidence": "explicit",
        "created_at": now,
        "updated_at": now,
        "review_after": now + Duration::days(1)
    }));
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": entries
        }))?,
    )?;

    let blocked_output = forager_command(temp.path())
        .env("FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED", "exclude")
        .args([
            "offdesk",
            "gate",
            "inspect.status",
            "--project-key",
            "project",
            "--request-id",
            "request-two",
            "--task-id",
            "task",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(blocked_output.status.success());
    let blocked: serde_json::Value = serde_json::from_slice(&blocked_output.stdout)?;
    assert!(blocked["adaptive_wiki"]
        .as_array()
        .expect("preflight")
        .iter()
        .any(|entry| entry["id"] == "wiki_session_specific"));
    assert!(blocked["adaptive_wiki_runtime"]
        .as_array()
        .is_none_or(Vec::is_empty));
    assert_eq!(
        blocked["adaptive_wiki_runtime_decision"]["status"],
        "strict_requested_scope_mode_blocked"
    );
    assert_eq!(
        blocked["adaptive_wiki_runtime_decision"]["acknowledgement_scope_mode"],
        "project_artifact"
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_runtime_policy_ack_report_flags_near_expiry_and_session_block() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let mut entries = vec![
        json!({
            "id": "wiki_report_expired",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Expired report guidance",
            "ai_instruction": "Use after explicit review.",
            "evidence_refs": ["task:expired"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now - Duration::days(1)
        }),
        json!({
            "id": "wiki_report_fresh",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Fresh report guidance",
            "ai_instruction": "Keep reviewed report guidance.",
            "evidence_refs": ["task:fresh"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now + Duration::days(1)
        }),
    ];
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": entries
        }))?,
    )?;

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--scope-mode",
            "project-artifact",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--ttl-hours",
            "1",
            "--reason",
            "short lived strict projection review",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    let ack_id = ack["id"].as_str().expect("ack id");

    let ack_list_output = forager_command(temp.path())
        .args(["offdesk", "wiki", "runtime-policy-acks"])
        .output()?;
    assert!(ack_list_output.status.success());
    let ack_list_stdout = String::from_utf8(ack_list_output.stdout)?;
    assert!(ack_list_stdout.contains("SCOPE_MODE"));
    assert!(ack_list_stdout.contains(ack_id));
    assert!(ack_list_stdout.contains("project_artifact"));

    entries.push(json!({
        "id": "wiki_report_session",
        "kind": "procedure",
        "scope": "session",
        "scope_ref": "request-two",
        "status": "promoted",
        "activation_mode": "confirm",
        "claim": "Session-specific report guidance",
        "ai_instruction": "This request has separate reviewed guidance.",
        "evidence_refs": ["task:session"],
        "confidence": "explicit",
        "created_at": now,
        "updated_at": now,
        "review_after": now + Duration::days(1)
    }));
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": entries
        }))?,
    )?;

    let report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "runtime-policy-ack-report",
            "--session-id",
            "request-two",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--near-expiry-hours",
            "2",
            "--json",
        ])
        .output()?;
    assert!(report_output.status.success());
    let report: serde_json::Value = serde_json::from_slice(&report_output.stdout)?;
    assert_eq!(report["summary"]["total"], 1);
    assert_eq!(report["summary"]["active"], 1);
    assert_eq!(report["summary"]["near_expiry"], 1);
    assert_eq!(report["summary"]["suggested_actions"], 1);
    assert_eq!(report["summary"]["query_blocked"], 1);
    assert_eq!(
        report["decision"]["status"],
        "strict_requested_scope_mode_blocked"
    );
    assert_eq!(report["decision"]["acknowledgement_id"], ack["id"]);
    assert_eq!(report["acknowledgements"][0]["id"], ack["id"]);
    let statuses = report["acknowledgements"][0]["status"]
        .as_array()
        .expect("status");
    assert!(statuses.iter().any(|status| status == "near_expiry"));
    assert!(statuses
        .iter()
        .any(|status| status == "query_blocked_by_session_scope"));
    assert_eq!(
        report["acknowledgements"][0]["suggested_action"]["kind"],
        "record_exact_query_acknowledgement"
    );
    let suggested_ack = report["acknowledgements"][0]["suggested_action"]["ack_command_template"]
        .as_str()
        .expect("ack command");
    assert!(suggested_ack.contains("ack-runtime-policy"));
    assert!(suggested_ack.contains("--session-id 'request-two'"));
    assert!(!suggested_ack.contains("--scope-mode"));

    let human_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "runtime-policy-ack-report",
            "--session-id",
            "request-two",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--near-expiry-hours",
            "2",
        ])
        .output()?;
    assert!(human_report_output.status.success());
    let human_report_stdout = String::from_utf8(human_report_output.stdout)?;
    assert!(human_report_stdout.contains("Adaptive wiki runtime policy acknowledgement report"));
    assert!(human_report_stdout.contains(ack_id));
    assert!(human_report_stdout.contains("project_artifact"));
    assert!(human_report_stdout.contains("query_blocked_by_session_scope"));
    assert!(human_report_stdout.contains("suggested_action: record_exact_query_acknowledgement"));
    assert!(human_report_stdout.contains("--session-id 'request-two'"));

    let human_ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--scope-mode",
            "project-artifact",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--reason",
            "human presentation regression",
        ])
        .output()?;
    assert!(human_ack_output.status.success());
    let human_ack_stdout = String::from_utf8(human_ack_output.stdout)?;
    assert!(human_ack_stdout.contains("Recorded adaptive wiki runtime policy acknowledgement"));
    assert!(human_ack_stdout.contains("scope_mode: project_artifact"));
    assert!(human_ack_stdout.contains("reason: human presentation regression"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_runtime_policy_ack_report_suggests_recompare_for_expired_and_stale_ack() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let base_entries = json!([
        {
            "id": "wiki_ack_expired_review",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Expired review guidance",
            "ai_instruction": "Use only after strict review.",
            "evidence_refs": ["task:expired"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now - Duration::days(1)
        },
        {
            "id": "wiki_ack_fresh_review",
            "kind": "procedure",
            "scope": "artifact_kind",
            "scope_ref": "report",
            "status": "promoted",
            "activation_mode": "confirm",
            "claim": "Fresh review guidance",
            "ai_instruction": "Fresh strict runtime guidance.",
            "evidence_refs": ["task:fresh"],
            "confidence": "explicit",
            "created_at": now,
            "updated_at": now,
            "review_after": now + Duration::days(1)
        }
    ]);
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": base_entries
        }))?,
    )?;

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "ack-runtime-policy",
            "--session-id",
            "request",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--reason",
            "operator reviewed strict projection",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    let mut expired_ack = ack.clone();
    expired_ack["created_at"] = serde_json::to_value(now - Duration::hours(2))?;
    expired_ack["expires_at"] = serde_json::to_value(now - Duration::hours(1))?;
    fs::write(
        profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl"),
        format!("{}\n", serde_json::to_string(&expired_ack)?),
    )?;

    let expired_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "runtime-policy-ack-report",
            "--session-id",
            "request",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(expired_report_output.status.success());
    let expired_report: serde_json::Value = serde_json::from_slice(&expired_report_output.stdout)?;
    assert_eq!(
        expired_report["decision"]["status"],
        "strict_requested_expired_acknowledgement"
    );
    assert_eq!(expired_report["summary"]["expired"], 1);
    assert_eq!(expired_report["summary"]["query_expired"], 1);
    assert_eq!(expired_report["summary"]["suggested_actions"], 1);
    assert_eq!(
        expired_report["acknowledgements"][0]["suggested_action"]["kind"],
        "recompare_and_append_acknowledgement"
    );
    let expired_ack_command = expired_report["acknowledgements"][0]["suggested_action"]
        ["ack_command_template"]
        .as_str()
        .expect("expired ack command");
    assert!(expired_ack_command.contains("ack-runtime-policy"));
    assert!(expired_ack_command.contains("--session-id 'request'"));
    assert!(!expired_ack_command.contains("expires-at"));

    fs::write(
        profile_dir.join("adaptive_wiki_runtime_policy_acknowledgements.jsonl"),
        format!("{}\n", serde_json::to_string(&ack)?),
    )?;
    let mut stale_entries = base_entries.as_array().expect("entries").clone();
    stale_entries.push(json!({
        "id": "wiki_ack_new_review",
        "kind": "procedure",
        "scope": "artifact_kind",
        "scope_ref": "report",
        "status": "promoted",
        "activation_mode": "confirm",
        "claim": "New strict review guidance",
        "ai_instruction": "New projection entry changes the comparison hash.",
        "evidence_refs": ["task:new"],
        "confidence": "explicit",
        "created_at": now,
        "updated_at": now,
        "review_after": now + Duration::days(1)
    }));
    fs::write(
        profile_dir.join("adaptive_wiki_entries.json"),
        serde_json::to_string_pretty(&json!({
            "version": "2026-05-14.v0",
            "entries": stale_entries
        }))?,
    )?;

    let stale_report_output = forager_command(temp.path())
        .args([
            "offdesk",
            "wiki",
            "runtime-policy-ack-report",
            "--session-id",
            "request",
            "--project-key",
            "project",
            "--artifact-kind",
            "report",
            "--json",
        ])
        .output()?;
    assert!(stale_report_output.status.success());
    let stale_report: serde_json::Value = serde_json::from_slice(&stale_report_output.stdout)?;
    assert_eq!(
        stale_report["decision"]["status"],
        "strict_requested_stale_acknowledgement"
    );
    assert_eq!(stale_report["summary"]["query_stale"], 1);
    assert_eq!(stale_report["summary"]["suggested_actions"], 1);
    assert_eq!(
        stale_report["acknowledgements"][0]["suggested_action"]["kind"],
        "recompare_and_append_acknowledgement"
    );
    let compare_command = stale_report["acknowledgements"][0]["suggested_action"]
        ["compare_command_template"]
        .as_str()
        .expect("compare command");
    assert!(compare_command.contains("projection"));
    assert!(compare_command.contains("--compare-review-expired-policy"));
    assert!(compare_command.contains("--runtime-agent-mode-default"));
    assert!(compare_command.contains("--session-id 'request'"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_defers_provider_capacity_then_launches_after_cooldown() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("provider-result.txt");
    let now = Utc::now();
    let retry_at = now + Duration::minutes(2);
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
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
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
            "--provider-id",
            "openai",
            "--model",
            "gpt-4.1",
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

    let blocked_output = forager_command(temp.path())
        .env_remove("OPENAI_API_KEY")
        .env_remove("ANTHROPIC_API_KEY")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(blocked_output.status.success());
    let blocked: serde_json::Value = serde_json::from_slice(&blocked_output.stdout)?;
    assert_eq!(blocked["provider_deferred"], 1);
    assert_eq!(blocked["failed"], 0);
    assert!(!profile_dir.join("pending_action_approvals.json").exists());
    let background_runs_path = profile_dir.join("background_runs.json");
    if background_runs_path.exists() {
        let runs: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(&background_runs_path)?)?;
        assert_eq!(runs.as_array().map(Vec::len), Some(0));
    }

    let tasks_path = profile_dir.join("offdesk_tasks.json");
    let mut tasks: serde_json::Value = serde_json::from_str(&fs::read_to_string(&tasks_path)?)?;
    assert_eq!(tasks[0]["status"], "queued");
    assert_eq!(tasks[0]["not_before"], serde_json::to_value(retry_at)?);
    assert_eq!(tasks[0]["provider_id"], "openai");
    assert_eq!(tasks[0]["model"], "gpt-4.1");
    assert_eq!(
        tasks[0]["last_provider_fallback"]["current_provider_id"],
        "openai"
    );
    assert!(tasks[0]["last_provider_fallback"]["candidates"]
        .as_array()
        .expect("fallback candidates")
        .iter()
        .all(
            |candidate| !(candidate["provider_id"] == "openai" && candidate["model"] == "gpt-4.1")
        ));

    let tasks_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--json"])
        .output()?;
    assert!(tasks_output.status.success());
    let task_views: serde_json::Value = serde_json::from_slice(&tasks_output.stdout)?;
    assert_eq!(
        task_views[0]["last_provider_fallback"]["current_provider_id"],
        "openai"
    );

    tasks[0]["not_before"] = serde_json::to_value(now - Duration::seconds(1))?;
    fs::write(&tasks_path, serde_json::to_string_pretty(&tasks)?)?;
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": now - Duration::seconds(1),
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
    )?;

    let launch_output = forager_command(temp.path())
        .env_remove("OPENAI_API_KEY")
        .env_remove("ANTHROPIC_API_KEY")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(launch_output.status.success());
    let launched: serde_json::Value = serde_json::from_slice(&launch_output.stdout)?;
    assert_eq!(launched["launched"], 1);
    let tasks_after_launch: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&tasks_path)?)?;
    assert!(tasks_after_launch[0]
        .get("last_provider_fallback")
        .is_none());
    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["task_id"], "task");
    Ok(())
}

#[test]
#[serial]
fn offdesk_tick_creates_provider_fallback_approval_then_retargets_and_launches() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let brief_path = temp.path().join("brief.json");
    let result_path = temp.path().join("fallback-result.txt");
    let now = Utc::now();
    let retry_at = now + Duration::minutes(10);
    fs::write(
        &brief_path,
        serde_json::to_string_pretty(&json!({
            "request_id": "request",
            "task_id": "task",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(30)
        }))?,
    )?;
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
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
            "--provider-id",
            "openai",
            "--model",
            "gpt-4.1",
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

    let blocked_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", "sk-test-provider-fallback")
        .env_remove("ANTHROPIC_API_KEY")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(blocked_output.status.success());
    let blocked: serde_json::Value = serde_json::from_slice(&blocked_output.stdout)?;
    assert_eq!(blocked["provider_deferred"], 1);
    assert_eq!(blocked["pending_approval"], 0);
    assert_eq!(blocked["launched"], 0);

    let approvals_path = profile_dir.join("pending_action_approvals.json");
    let tasks_path = profile_dir.join("offdesk_tasks.json");
    let approvals: serde_json::Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(approvals.as_array().expect("approvals").len(), 1);
    assert_eq!(approvals[0]["action"], "dispatch.provider_fallback");
    assert_eq!(approvals[0]["risk_level"], "runtime_mutation");
    assert_eq!(approvals[0]["approval_mode"], "operator_required");
    assert_eq!(approvals[0]["metadata"]["kind"], "provider_fallback");
    assert_eq!(approvals[0]["metadata"]["current_provider_id"], "openai");
    assert_eq!(approvals[0]["metadata"]["current_model"], "gpt-4.1");
    assert_eq!(approvals[0]["metadata"]["candidate_limit"], 3);
    assert_eq!(
        approvals[0]["metadata"]["apply_scope"],
        "request_matching_provider_model"
    );
    let approval_brief = &approvals[0]["metadata"]["approval_brief"];
    assert_eq!(approval_brief["schema"], "approval_brief.v1");
    assert_eq!(approval_brief["source"], "offdesk.provider_fallback");
    assert_eq!(approval_brief["recommendation"], "approve");
    assert_eq!(approval_brief["subject"], "provider fallback");
    assert!(approval_brief["summary_lines"]
        .as_array()
        .expect("summary lines")
        .iter()
        .any(|line| line
            .as_str()
            .unwrap_or_default()
            .contains("Provider/model retargeting")));
    assert!(approval_brief["scope"]
        .as_str()
        .unwrap_or_default()
        .contains("does not approve runtime dispatch"));
    assert!(approval_brief["decision_impacts"]["approve"]
        .as_str()
        .unwrap_or_default()
        .contains("runtime dispatch still needs its own approval"));
    assert!(approval_brief["judgment_route_summary"]
        .as_str()
        .unwrap_or_default()
        .contains("deterministic gate"));
    assert!(approval_brief["evidence_sufficiency"]
        .as_str()
        .unwrap_or_default()
        .contains("ranked fallback candidates"));
    assert_eq!(approval_brief["default_if_no_reply"], "defer");
    assert!(approval_brief["decision_impacts"]["deny"]
        .as_str()
        .unwrap_or_default()
        .contains("Keep openai model gpt-4.1 queued"));
    assert_eq!(approval_brief["options"][0]["id"], "approve");
    assert_eq!(approval_brief["options"][0]["label"], "Approve fallback");
    assert_eq!(approval_brief["options"][1]["id"], "deny");
    assert!(approval_brief["options"][1]["natural_input_prompt"]
        .as_str()
        .unwrap_or_default()
        .contains("should not be applied"));
    assert_eq!(approval_brief["options"][2]["id"], "defer");
    assert!(!serde_json::to_string(approval_brief)?.contains("sk-test-provider-fallback"));
    assert!(approvals[0]["metadata"]["candidates"]
        .as_array()
        .expect("candidates")
        .iter()
        .all(|candidate| candidate["recommended"] == true));

    let pending_json_output = forager_command(temp.path())
        .args(["offdesk", "pending", "--json"])
        .output()?;
    assert!(pending_json_output.status.success());
    let pending_json: serde_json::Value = serde_json::from_slice(&pending_json_output.stdout)?;
    assert_eq!(pending_json.as_array().expect("pending approvals").len(), 1);
    assert_eq!(pending_json[0]["metadata"], approvals[0]["metadata"]);
    assert_eq!(
        pending_json[0]["next_safe_action"]["kind"],
        "approval_pending"
    );

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "pending"])
        .output()?;
    assert!(pending_output.status.success());
    let pending_stdout = String::from_utf8_lossy(&pending_output.stdout);
    assert!(pending_stdout.contains("prompt: approve recommendation for provider fallback"));
    assert!(pending_stdout.contains("Approve this provider fallback retargeting?"));
    assert!(pending_stdout.contains("does not approve runtime dispatch"));
    assert!(pending_stdout.contains("fallback target: openai model gpt-4.1"));
    assert!(pending_stdout.contains("gpt-4.1-mini"));
    assert!(pending_stdout.contains("forager offdesk ok"));
    assert!(pending_stdout.contains("forager offdesk cancel"));

    let mut tasks_before_ok: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&tasks_path)?)?;
    tasks_before_ok[0]["not_before"] = serde_json::to_value(now - Duration::seconds(1))?;
    fs::write(&tasks_path, serde_json::to_string_pretty(&tasks_before_ok)?)?;
    let repeated_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", "sk-test-provider-fallback")
        .env_remove("ANTHROPIC_API_KEY")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(repeated_output.status.success());
    let repeated: serde_json::Value = serde_json::from_slice(&repeated_output.stdout)?;
    assert_eq!(repeated["provider_deferred"], 1);
    let repeated_approvals: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(repeated_approvals.as_array().expect("approvals").len(), 1);

    let ok_output = forager_command(temp.path())
        .args(["offdesk", "ok", "--json"])
        .output()?;
    assert!(ok_output.status.success());

    let launch_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", "sk-test-provider-fallback")
        .env_remove("ANTHROPIC_API_KEY")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(launch_output.status.success());
    let launched: serde_json::Value = serde_json::from_slice(&launch_output.stdout)?;
    assert_eq!(launched["provider_retargeted"], 1);
    assert_eq!(launched["launched"], 1);

    let tasks: serde_json::Value = serde_json::from_str(&fs::read_to_string(&tasks_path)?)?;
    assert_eq!(tasks[0]["provider_id"], "openai");
    assert_eq!(tasks[0]["model"], "gpt-4.1-mini");
    assert_eq!(tasks[0]["command"], command.as_str());
    assert_eq!(
        tasks[0]["workdir"],
        temp.path().to_str().expect("utf-8 path")
    );
    assert_eq!(
        tasks[0]["result_artifact_path"],
        result_path.to_str().expect("utf-8 path")
    );
    assert!(tasks[0].get("not_before").is_none());
    assert!(tasks[0].get("last_provider_fallback").is_none());
    let approvals_after: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(approvals_after[0]["status"], "superseded");
    Ok(())
}

#[test]
#[serial]
fn offdesk_provider_fallback_approval_skips_invalid_candidate_and_keeps_scope() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let retry_at = now + Duration::minutes(10);
    let result_path = temp.path().join("fallback-scope-result.txt");
    let other_result_path = temp.path().join("fallback-scope-other.txt");
    let command = format!("printf done > {}", result_path.display());
    let other_command = format!("printf other > {}", other_result_path.display());
    let brief = |task_id: &str| {
        json!({
            "request_id": "request",
            "task_id": task_id,
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": now + Duration::minutes(30)
        })
    };
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "queued",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": command,
                "workdir": temp.path().to_str().expect("utf-8 path"),
                "execution_brief": brief("task"),
                "provider_id": "openai",
                "model": "gpt-4.1",
                "result_artifact_path": result_path.to_str().expect("utf-8 path"),
                "created_at": now,
                "updated_at": now
            },
            {
                "task_id": "other-model",
                "request_id": "request",
                "project_key": "project",
                "status": "queued",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": other_command,
                "workdir": temp.path().to_str().expect("utf-8 path"),
                "execution_brief": brief("other-model"),
                "provider_id": "openai",
                "model": "gpt-4o",
                "not_before": retry_at,
                "result_artifact_path": other_result_path.to_str().expect("utf-8 path"),
                "created_at": now,
                "updated_at": now
            }
        ]))?,
    )?;

    let blocked_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", "sk-test-provider-fallback")
        .env("ANTHROPIC_API_KEY", "sk-ant-test-provider-fallback")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(blocked_output.status.success());
    let blocked: serde_json::Value = serde_json::from_slice(&blocked_output.stdout)?;
    assert_eq!(blocked["provider_deferred"], 1);

    let ok_output = forager_command(temp.path())
        .args(["offdesk", "ok", "--json"])
        .output()?;
    assert!(ok_output.status.success());
    fs::write(
        profile_dir.join("provider_capacity.json"),
        serde_json::to_string_pretty(&json!([
            {
                "provider_id": "openai",
                "model": "gpt-4.1",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            },
            {
                "provider_id": "openai",
                "model": "gpt-4.1-mini",
                "status": "cooling_down",
                "reason": "rate_limit",
                "cooldown_until": retry_at,
                "last_error_summary": "rate limit",
                "updated_at": now
            }
        ]))?,
    )?;

    let launch_output = forager_command(temp.path())
        .env("OPENAI_API_KEY", "sk-test-provider-fallback")
        .env("ANTHROPIC_API_KEY", "sk-ant-test-provider-fallback")
        .args(["offdesk", "tick", "--json"])
        .output()?;
    assert!(launch_output.status.success());
    let launched: serde_json::Value = serde_json::from_slice(&launch_output.stdout)?;
    assert_eq!(launched["provider_retargeted"], 1);
    assert_eq!(launched["launched"], 1);

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    let task = tasks
        .as_array()
        .expect("tasks")
        .iter()
        .find(|task| task["task_id"] == "task")
        .expect("task");
    let other = tasks
        .as_array()
        .expect("tasks")
        .iter()
        .find(|task| task["task_id"] == "other-model")
        .expect("other task");
    assert_eq!(task["provider_id"], "anthropic");
    assert_eq!(task["model"], "claude-3-5-sonnet-latest");
    assert_eq!(other["provider_id"], "openai");
    assert_eq!(other["model"], "gpt-4o");
    assert_eq!(other["not_before"], serde_json::to_value(retry_at)?);
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
    assert_eq!(pending["next_safe_actions"][0]["kind"], "approval_pending");
    assert_eq!(
        pending["next_safe_actions"][0]["requires_operator_review"],
        true
    );

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
    assert!(launched["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "runtime_monitoring"));

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
    let log_path = temp.path().join("background.log");
    let result_path = temp.path().join("background-result.txt");
    fs::write(
        &log_path,
        "first line\nlast line token=sk-secretsecretsecretsecret\n",
    )?;
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
                "log_artifact_path": log_path.to_str().expect("utf-8 path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 path"),
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
                "log_artifact_path": log_path.to_str().expect("utf-8 path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 path"),
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
    assert!(report["next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "recovery_required"));

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "resume_pending");

    let resume: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("task_resume_state.json"),
    )?)?;
    assert_eq!(resume[0]["status"], "resume_pending");
    assert!(resume[0]["resume_id"]
        .as_str()
        .expect("resume id")
        .starts_with("resume_"));
    assert_eq!(resume[0]["background_ticket_id"], "ticket");
    assert_eq!(resume[0]["last_task_status"], "running");
    assert_eq!(resume[0]["attempt_count"], 1);
    assert!(!resume[0]["last_log_tail"]
        .as_str()
        .expect("last log tail")
        .contains("sk-secretsecretsecretsecret"));
    let evidence = resume[0]["evidence"].as_array().expect("evidence");
    assert!(evidence
        .iter()
        .any(|entry| entry["kind"].as_str() == Some("background_probe")));
    assert!(evidence.iter().any(|entry| {
        entry["kind"].as_str() == Some("log_artifact") && entry["present"].as_bool() == Some(true)
    }));
    assert!(evidence.iter().any(|entry| {
        entry["kind"].as_str() == Some("result_artifact")
            && entry["present"].as_bool() == Some(false)
    }));
    assert!(evidence
        .iter()
        .any(|entry| entry["kind"].as_str() == Some("log_tail")));

    let resume_output = forager_command(temp.path())
        .args(["offdesk", "resume"])
        .output()?;
    assert!(resume_output.status.success());
    let stdout = String::from_utf8_lossy(&resume_output.stdout);
    assert!(stdout.contains("resume_id: resume_"));
    assert!(stdout.contains("evidence: background_probe"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_background_ack_clears_stale_attention_for_cancelled_linked_task() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task",
            "dispatch.runtime",
            "cancelled",
            now,
            "true",
            temp.path()
        )]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "runner_kind": "local_background",
                "phase": "stale_lost_callback",
                "runtime_handle_alive": false
            }
        ]))?,
    )?;

    let status_before_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_before_output.status.success());
    let status_before: serde_json::Value = serde_json::from_slice(&status_before_output.stdout)?;
    assert_eq!(status_before["stale_background_runs"], 1);
    assert!(status_before["offdesk_next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "recovery_required"));

    let ack_output = forager_command(temp.path())
        .args([
            "offdesk",
            "background-ack",
            "ticket",
            "--reason",
            "cancelled stale local-background probe was superseded",
            "--by",
            "codex",
            "--json",
        ])
        .output()?;
    assert!(ack_output.status.success());
    let ack: serde_json::Value = serde_json::from_slice(&ack_output.stdout)?;
    assert_eq!(ack["ticket_id"], "ticket");
    assert_eq!(ack["linked_task_ids"], json!(["task"]));
    assert_eq!(
        ack["acknowledgement"]["previous_phase"],
        "stale_lost_callback"
    );
    assert_eq!(ack["acknowledgement"]["acknowledged_by"], "codex");
    assert_eq!(ack["status"]["decision"]["phase"], "recovery_acknowledged");

    let runs: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(runs[0]["phase"], "recovery_acknowledged");
    assert_eq!(
        runs[0]["operator_recovery_ack"]["reason"],
        "cancelled stale local-background probe was superseded"
    );
    assert!(runs[0]["operator_recovery_ack"]["does_not_authorize"]
        .as_array()
        .expect("does not authorize")
        .iter()
        .any(|value| value == "accepting any Offdesk output as truth"));

    let status_after_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_after_output.status.success());
    let status_after: serde_json::Value = serde_json::from_slice(&status_after_output.stdout)?;
    assert_eq!(status_after["stale_background_runs"], 0);
    assert!(!status_after["offdesk_next_safe_actions"]
        .as_array()
        .expect("next safe actions")
        .iter()
        .any(|action| action["kind"] == "recovery_required"));
    assert!(!profile_dir.join("task_resume_state.json").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_background_ack_presentation_redacts_legacy_acknowledgement() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "task_id": "task",
                "runner_kind": "local_background",
                "phase": "recovery_acknowledged",
                "runtime_handle_alive": false,
                "operator_recovery_ack": {
                    "acknowledged_at": now,
                    "acknowledged_by": format!("operator token={secret}"),
                    "reason": format!("legacy reason token={secret}"),
                    "previous_phase": "stale_lost_callback",
                    "linked_task_ids": [format!("task-token={secret}")],
                    "source_surface": format!("legacy token={secret}"),
                    "does_not_authorize": []
                }
            }
        ]))?,
    )?;

    let json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "background-ack",
            "ticket",
            "--reason",
            "already acknowledged",
            "--json",
        ])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let report: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert_eq!(
        report["status"]["decision"]["phase"],
        "recovery_acknowledged"
    );
    assert!(report["acknowledgement"]["reason"]
        .as_str()
        .expect("acknowledgement reason")
        .contains("[REDACTED]"));

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "background-ack",
            "ticket",
            "--reason",
            "already acknowledged",
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("Acknowledged background recovery ticket"));
    assert!(human_stdout.contains("[REDACTED]"));
    assert!(human_stdout.contains("no retry, resume, closeout, cleanup"));
    assert!(!human_stdout.contains(secret));
    Ok(())
}

#[test]
#[serial]
fn offdesk_background_ack_rejects_non_cancelled_linked_task() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task",
            "dispatch.runtime",
            "running",
            now,
            "true",
            temp.path()
        )]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket",
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "runner_kind": "local_background",
                "phase": "stale_lost_callback",
                "runtime_handle_alive": false
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "background-ack",
            "ticket",
            "--reason",
            "unsafe shortcut",
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("non-cancelled linked tasks"));
    assert!(stderr.contains("task:Running"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_poll_reconciles_completed_background_task() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let log_path = temp.path().join("background.log");
    let result_path = temp.path().join("background-result.json");
    fs::write(&log_path, "completed\n")?;
    fs::write(&result_path, "{\"ok\":true}\n")?;
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "launched",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_tmux",
                "command": "true",
                "workdir": temp.path().to_str().expect("utf-8 path"),
                "background_ticket_id": "ticket",
                "attempt_count": 1,
                "agent_mode": "writing",
                "log_artifact_path": log_path.to_str().expect("utf-8 path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 path"),
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
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "agent_mode": "writing",
                "runner_kind": "local_tmux",
                "phase": "launched",
                "log_artifact_path": log_path.to_str().expect("utf-8 path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 path"),
                "runtime_handle_alive": false
            }
        ]))?,
    )?;

    let poll_output = forager_command(temp.path())
        .args(["offdesk", "poll", "--json", "ticket"])
        .output()?;
    assert!(poll_output.status.success());
    let poll: serde_json::Value = serde_json::from_slice(&poll_output.stdout)?;
    assert_eq!(poll[0]["decision"]["phase"], "completed");
    assert_eq!(poll[0]["probe"]["result_artifact_present"], true);
    assert_eq!(poll[0]["mode_verdict"], "evidence_ready");
    assert_eq!(poll[0]["mode_risk"], "operator_review_required");
    assert_eq!(poll[0]["review_stage_required"], true);
    assert_eq!(poll[0]["next_safe_action"]["kind"], "review_required");
    assert!(poll[0]["next_safe_action"]["commands"]
        .as_array()
        .expect("poll next action commands")
        .iter()
        .any(|command| command
            .as_str()
            .expect("poll next action command")
            .contains("forager offdesk closeout --project-key project --task-id task")));

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "completed");
    assert!(tasks[0]["last_error"].is_null());

    let task_output = forager_command(temp.path())
        .args(["offdesk", "tasks", "--json"])
        .output()?;
    assert!(task_output.status.success());
    let task_views: serde_json::Value = serde_json::from_slice(&task_output.stdout)?;
    assert_eq!(task_views[0]["status"], "completed");
    assert_eq!(task_views[0]["agent_mode"], "writing");
    assert_eq!(task_views[0]["mode_verdict"], "evidence_ready");
    assert_eq!(task_views[0]["mode_risk"], "operator_review_required");
    assert_eq!(task_views[0]["review_stage_required"], true);
    assert_eq!(task_views[0]["next_safe_action"]["kind"], "review_required");
    assert_eq!(
        task_views[0]["next_safe_action"]["requires_operator_review"],
        true
    );
    assert!(task_views[0]["next_safe_action"]["commands"]
        .as_array()
        .expect("next action commands")
        .iter()
        .any(|command| command
            .as_str()
            .expect("next action command")
            .contains("forager offdesk closeout --project-key project --task-id task")));
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
fn offdesk_terminate_task_records_stopped_runtime_and_cancels_task() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task("running", now, "true", temp.path(),)]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([{
            "ticket_id": "ticket",
            "project_key": "project",
            "request_id": "request",
            "task_id": "task",
            "runner_kind": "local_background",
            "phase": "launched"
        }]))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "terminate-task",
            "task",
            "--reason",
            "operator stop",
            "--by",
            "test",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["schema"], "offdesk_task_termination_report.v1");
    assert_eq!(report["task"]["status"], "cancelled");
    assert_eq!(report["background"]["phase"], "terminated");
    assert_eq!(report["background"]["changed"], false);

    let tasks: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(profile_dir.join("offdesk_tasks.json"))?)?;
    assert_eq!(tasks[0]["status"], "cancelled");
    let probes: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("background_runs.json"),
    )?)?;
    assert_eq!(probes[0]["phase"], "terminated");
    assert_eq!(probes[0]["termination"]["terminated_by"], "test");
    assert_eq!(probes[0]["termination"]["reason"], "operator stop");
    Ok(())
}

#[test]
#[serial]
fn offdesk_task_lifecycle_presentation_redacts_legacy_json_and_human_reports() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let task_id = format!("task-token={secret}");
    let mut running_task = durable_task_with(
        &task_id,
        "inspect.status",
        "running",
        now,
        "true",
        temp.path(),
    );
    running_task["project_key"] = json!(format!("project-token={secret}"));
    running_task["background_ticket_id"] = json!(format!("ticket-token={secret}"));
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([running_task]))?,
    )?;

    let cancel_json_output = forager_command(temp.path())
        .args(["offdesk", "cancel-task", &task_id, "--json"])
        .output()?;
    assert!(cancel_json_output.status.success());
    let cancel_json_stdout = String::from_utf8_lossy(&cancel_json_output.stdout);
    assert!(!cancel_json_stdout.contains(secret));
    let cancel_report: serde_json::Value = serde_json::from_slice(&cancel_json_output.stdout)?;
    assert_eq!(cancel_report["status"], "cancelled");
    assert!(cancel_report["task"]["task_id"]
        .as_str()
        .expect("task id")
        .contains("[REDACTED]"));

    let cancel_human_output = forager_command(temp.path())
        .args(["offdesk", "cancel-task", &task_id])
        .output()?;
    assert!(cancel_human_output.status.success());
    let cancel_human_stdout = String::from_utf8_lossy(&cancel_human_output.stdout);
    assert!(cancel_human_stdout.contains("Unchanged offdesk task task-token=[REDACTED]"));
    assert!(cancel_human_stdout.contains("ticket: ticket-token=[REDACTED]"));
    assert!(!cancel_human_stdout.contains(secret));

    let mut failed_task = durable_task_with(
        &task_id,
        "dispatch.runtime",
        "failed",
        now,
        "true",
        temp.path(),
    );
    failed_task["project_key"] = json!(format!("project-token={secret}"));
    failed_task["background_ticket_id"] = json!(format!("ticket-token={secret}"));
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([failed_task]))?,
    )?;

    record_fixture_exit_evidence(&profile_dir)?;

    let before_retry = fs::read(profile_dir.join("offdesk_tasks.json"))?;
    let retry_json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "retry-task",
            &task_id,
            "--new-approval",
            "--json",
        ])
        .output()?;
    if !cfg!(target_os = "linux") {
        assert!(!retry_json_output.status.success());
        assert_eq!(
            fs::read(profile_dir.join("offdesk_tasks.json"))?,
            before_retry
        );
        assert!(!String::from_utf8_lossy(&retry_json_output.stderr).contains(secret));
        return Ok(());
    }
    assert!(retry_json_output.status.success());
    let retry_json_stdout = String::from_utf8_lossy(&retry_json_output.stdout);
    assert!(!retry_json_stdout.contains(secret));
    let retry_report: serde_json::Value = serde_json::from_slice(&retry_json_output.stdout)?;
    assert_eq!(retry_report["status"], "queued");
    assert_eq!(retry_report["superseded_denied_approvals"], 0);

    let retry_human_output = forager_command(temp.path())
        .args(["offdesk", "retry-task", &task_id, "--new-approval"])
        .output()?;
    assert!(retry_human_output.status.success());
    let retry_human_stdout = String::from_utf8_lossy(&retry_human_output.stdout);
    assert!(retry_human_stdout.contains("task-token=[REDACTED]"));
    assert!(retry_human_stdout.contains("superseded denied approvals: 0"));
    assert!(!retry_human_stdout.contains(secret));
    Ok(())
}

#[test]
#[serial]
#[cfg(target_os = "linux")]
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

    record_fixture_exit_evidence(&profile_dir)?;

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
#[cfg(target_os = "linux")]
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

    record_fixture_exit_evidence(&profile_dir)?;

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
#[cfg(target_os = "linux")]
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

    record_fixture_exit_evidence(&profile_dir)?;

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
#[cfg(target_os = "linux")]
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

    record_fixture_exit_evidence(&profile_dir)?;

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
fn offdesk_snapshot_commands_report_verify_and_restore_plan() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let snapshot_dir = profile_dir.join("mutation_snapshots");
    fs::create_dir_all(&snapshot_dir)?;
    let now = Utc::now();
    let target = temp.path().join("target.txt");
    let before_path = snapshot_dir.join("mutation_one.before");
    fs::write(&target, "after")?;
    fs::write(&before_path, "before")?;
    fs::write(
        snapshot_dir.join("mutation_one.json"),
        serde_json::to_string_pretty(&json!({
            "snapshot_schema_version": 1,
            "mutation_id": "mutation_one",
            "project_key": "project",
            "request_id": "request",
            "task_id": "task",
            "target_path": target.to_str().expect("utf-8 path"),
            "mutation_kind": "canonical_syncback",
            "target_exists_before": true,
            "before_size_bytes": 6,
            "before_hash": sha256_hex(b"before"),
            "before_excerpt_or_snapshot_path": before_path.to_str().expect("utf-8 path"),
            "snapshot_truncated": false,
            "rollback_available": true,
            "rollback_blockers": [],
            "diff_preview": "diff token=sk-secretsecretsecretsecret",
            "created_at": now,
            "created_by": "worker token=sk-secretsecretsecretsecret"
        }))?,
    )?;

    let list_output = forager_command(temp.path())
        .args(["offdesk", "snapshots", "--json"])
        .output()?;
    assert!(list_output.status.success());
    let list: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    assert_eq!(list[0]["mutation_id"], "mutation_one");
    assert_eq!(list[0]["rollback_available"], true);

    let snapshot_output = forager_command(temp.path())
        .args(["offdesk", "snapshot", "mutation_one", "--json"])
        .output()?;
    assert!(snapshot_output.status.success());
    let verification: serde_json::Value = serde_json::from_slice(&snapshot_output.stdout)?;
    assert_eq!(verification["snapshot_present"], true);
    assert_eq!(verification["rollback_available"], true);
    assert_eq!(verification["target_current_matches_before"], false);
    assert!(!verification["snapshot"]["diff_preview"]
        .as_str()
        .expect("diff preview")
        .contains("sk-secretsecretsecretsecret"));

    let plan_output = forager_command(temp.path())
        .args(["offdesk", "restore-plan", "mutation_one", "--json"])
        .output()?;
    assert!(plan_output.status.success());
    let plan: serde_json::Value = serde_json::from_slice(&plan_output.stdout)?;
    assert_eq!(plan["operation"], "restore_file");
    assert_eq!(plan["rollback_available"], true);
    assert_eq!(fs::read_to_string(&target)?, "after");
    Ok(())
}

#[test]
#[serial]
fn offdesk_mutation_snapshot_presentation_redacts_legacy_state_and_stays_read_only() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let snapshot_dir = profile_dir.join("mutation_snapshots");
    fs::create_dir_all(&snapshot_dir)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    let mutation_id = format!("mutation-token={secret}");
    let target = temp.path().join(format!("target-token={secret}.txt"));
    let before_path = snapshot_dir.join(format!("before-token={secret}.bin"));
    fs::write(&target, "after")?;
    fs::write(&before_path, "before")?;
    let snapshot_path = snapshot_dir.join(format!("{mutation_id}.json"));
    fs::write(
        &snapshot_path,
        serde_json::to_string_pretty(&json!({
            "snapshot_schema_version": 1,
            "mutation_id": mutation_id,
            "project_key": format!("project-token={secret}"),
            "request_id": format!("request-token={secret}"),
            "task_id": format!("task-token={secret}"),
            "target_path": target.to_str().expect("utf-8 path"),
            "mutation_kind": format!("canonical_syncback token={secret}"),
            "target_exists_before": true,
            "before_size_bytes": 6,
            "before_hash": sha256_hex(b"before"),
            "before_excerpt_or_snapshot_path": before_path.to_str().expect("utf-8 path"),
            "snapshot_truncated": false,
            "rollback_available": true,
            "rollback_blockers": [],
            "diff_preview": format!("legacy diff token={secret}"),
            "created_at": now,
            "created_by": format!("legacy worker token={secret}")
        }))?,
    )?;
    let snapshot_before = fs::read(&snapshot_path)?;
    let before_artifact_before = fs::read(&before_path)?;
    let target_before = fs::read(&target)?;

    let list_json_output = forager_command(temp.path())
        .args(["offdesk", "snapshots", "--json"])
        .output()?;
    assert!(list_json_output.status.success());
    let list_json_stdout = String::from_utf8_lossy(&list_json_output.stdout);
    assert!(!list_json_stdout.contains(secret));
    let list: serde_json::Value = serde_json::from_slice(&list_json_output.stdout)?;
    assert!(list[0]["mutation_id"]
        .as_str()
        .expect("mutation id")
        .contains("[REDACTED]"));

    let list_human_output = forager_command(temp.path())
        .args(["offdesk", "snapshots"])
        .output()?;
    assert!(list_human_output.status.success());
    let list_human_stdout = String::from_utf8_lossy(&list_human_output.stdout);
    assert!(list_human_stdout.contains("MUTATION ID"));
    assert!(list_human_stdout.contains("[REDACTED]"));
    assert!(!list_human_stdout.contains(secret));

    let snapshot_json_output = forager_command(temp.path())
        .args(["offdesk", "snapshot", &mutation_id, "--json"])
        .output()?;
    assert!(snapshot_json_output.status.success());
    let snapshot_json_stdout = String::from_utf8_lossy(&snapshot_json_output.stdout);
    assert!(!snapshot_json_stdout.contains(secret));
    let verification: serde_json::Value = serde_json::from_slice(&snapshot_json_output.stdout)?;
    assert_eq!(verification["snapshot_present"], true);
    assert!(verification["snapshot"]["target_path"]
        .as_str()
        .expect("target path")
        .contains("[REDACTED]"));

    let snapshot_human_output = forager_command(temp.path())
        .args(["offdesk", "snapshot", &mutation_id])
        .output()?;
    assert!(snapshot_human_output.status.success());
    let snapshot_human_stdout = String::from_utf8_lossy(&snapshot_human_output.stdout);
    assert!(snapshot_human_stdout.contains("Snapshot mutation-token=[REDACTED]"));
    assert!(!snapshot_human_stdout.contains(secret));

    let plan_json_output = forager_command(temp.path())
        .args(["offdesk", "restore-plan", &mutation_id, "--json"])
        .output()?;
    assert!(plan_json_output.status.success());
    let plan_json_stdout = String::from_utf8_lossy(&plan_json_output.stdout);
    assert!(!plan_json_stdout.contains(secret));
    let plan: serde_json::Value = serde_json::from_slice(&plan_json_output.stdout)?;
    assert_eq!(plan["operation"], "restore_file");
    assert!(plan["before_snapshot_path"]
        .as_str()
        .expect("before snapshot path")
        .contains("[REDACTED]"));

    let plan_human_output = forager_command(temp.path())
        .args(["offdesk", "restore-plan", &mutation_id])
        .output()?;
    assert!(plan_human_output.status.success());
    let plan_human_stdout = String::from_utf8_lossy(&plan_human_output.stdout);
    assert!(plan_human_stdout.contains("Restore plan mutation-token=[REDACTED]"));
    assert!(!plan_human_stdout.contains(secret));

    assert_eq!(fs::read(&snapshot_path)?, snapshot_before);
    assert_eq!(fs::read(&before_path)?, before_artifact_before);
    assert_eq!(fs::read(&target)?, target_before);
    Ok(())
}

#[test]
#[serial]
fn offdesk_snapshot_unknown_id_reports_operator_safe_error() -> Result<()> {
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let mutation_id = format!("mutation_missing-token={secret}");

    let output = forager_command(temp.path())
        .args(["offdesk", "snapshot", &mutation_id, "--json"])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains(secret));
    assert!(stderr.contains("Mutation snapshot not found: mutation_missing-token=[REDACTED]"));

    let restore_output = forager_command(temp.path())
        .args(["offdesk", "restore-plan", &mutation_id])
        .output()?;
    assert!(!restore_output.status.success());
    let restore_stderr = String::from_utf8_lossy(&restore_output.stderr);
    assert!(!restore_stderr.contains(secret));
    assert!(
        restore_stderr.contains("Mutation snapshot not found: mutation_missing-token=[REDACTED]")
    );
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
    assert_eq!(status["profile"], "default");
    assert_eq!(status["profile_dir_source"], "primary");
    assert_eq!(
        reported_path(&status["profile_dir"]),
        expected_path(&profile_dir(temp.path()))
    );
    assert_eq!(
        reported_path(&status["app_dir"]),
        expected_path(&app_dir(temp.path()))
    );
    assert_eq!(status["app_dir_source"], "primary");
    assert_eq!(
        reported_path(&status["primary_app_dir"]),
        expected_path(&app_dir(temp.path()))
    );
    assert_eq!(status["primary_app_dir_exists"], true);
    assert_eq!(status["queued_offdesk_tasks"], 1);
    assert_eq!(status["pending_approvals"], 0);
    assert_eq!(status["failed_offdesk_tasks"], 0);
    assert_eq!(status["resume_pending_offdesk_tasks"], 0);
    assert_eq!(status["cancelled_offdesk_tasks"], 0);
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    Ok(())
}

#[test]
#[serial]
fn offdesk_harnesses_lists_supported_and_planned_profiles() -> Result<()> {
    let temp = tempdir()?;

    let output = forager_command(temp.path())
        .args(["offdesk", "harnesses", "--json"])
        .output()?;

    assert!(output.status.success());
    let profiles: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    let profiles = profiles.as_array().expect("profiles array");
    assert!(profiles
        .iter()
        .any(|profile| { profile["id"] == "codex" && profile["support_status"] == "supported" }));
    assert!(profiles
        .iter()
        .any(|profile| { profile["id"] == "claude" && profile["support_status"] == "supported" }));
    let claude = profiles
        .iter()
        .find(|profile| profile["id"] == "claude")
        .expect("claude profile");
    assert_eq!(
        claude["prompt_contract"]["strategy"],
        "compact_prompt_with_first_read_artifacts"
    );
    assert_eq!(claude["prompt_contract"]["first_read_required"], true);
    assert_eq!(
        claude["prompt_contract"]["first_read_total_budget_bytes"],
        262_144
    );
    assert!(claude["prompt_contract"]["discouraged_inline_context"]
        .as_array()
        .expect("discouraged inline context")
        .iter()
        .any(|item| item == "full git diff"));
    assert!(profiles
        .iter()
        .any(|profile| { profile["id"] == "gemini" && profile["support_status"] == "planned" }));
    Ok(())
}

#[test]
#[serial]
fn offdesk_harness_prompt_builds_first_read_packet() -> Result<()> {
    let temp = tempdir()?;
    let first_read = temp.path().join("RETURN_PACKAGE.md");
    fs::write(&first_read, "return package")?;
    let output_path = temp.path().join("claude_start.md");
    let result_path = temp.path().join("result.json");

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            "Review the smoke result and report only missing evidence.",
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
            "--result-artifact",
            result_path.to_str().expect("utf-8 result path"),
            "--workdir",
            temp.path().to_str().expect("utf-8 workdir"),
            "--output",
            output_path.to_str().expect("utf-8 output path"),
            "--json",
        ])
        .output()?;

    assert!(output.status.success());
    let packet: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(packet["harness_id"], "claude");
    assert_eq!(
        packet["prompt_strategy"],
        "compact_prompt_with_first_read_artifacts"
    );
    assert_eq!(packet["first_reads"][0]["present"], true);
    assert_eq!(packet["first_reads"][0]["size_bytes"], 14);
    assert_eq!(packet["first_reads"][0]["over_file_budget"], false);
    assert_eq!(packet["first_read_budget_status"], "ok");
    assert_eq!(packet["read_scope_enforced"], false);
    assert_eq!(packet["first_read_total_bytes"], 14);
    assert_eq!(
        packet["result_artifact"],
        result_path.to_str().expect("utf-8 result path")
    );
    assert_eq!(
        packet["output_path"],
        output_path.to_str().expect("utf-8 output path")
    );
    assert!(packet["warnings"]
        .as_array()
        .expect("warnings array")
        .is_empty());
    let prompt = fs::read_to_string(output_path)?;
    assert!(prompt.contains("## First-Read Artifacts"));
    assert!(prompt.contains("Do not consult local memory or unrelated workspace files"));
    assert!(prompt.contains("Do not ask the operator to paste full git diffs"));
    assert!(prompt.contains(first_read.to_str().expect("utf-8 first read")));
    Ok(())
}

#[test]
#[serial]
fn offdesk_harness_prompt_warns_and_strict_fails_on_budget() -> Result<()> {
    let temp = tempdir()?;
    let first_read = temp.path().join("large.md");
    fs::write(&first_read, "0123456789")?;

    let warning_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            "Review first-read budget.",
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
            "--max-first-read-total-bytes",
            "5",
            "--json",
        ])
        .output()?;

    assert!(warning_output.status.success());
    let packet: serde_json::Value = serde_json::from_slice(&warning_output.stdout)?;
    assert_eq!(packet["first_read_total_bytes"], 10);
    assert_eq!(packet["first_read_total_budget_bytes"], 5);
    assert_eq!(packet["first_read_budget_status"], "warning");
    assert!(packet["warnings"]
        .as_array()
        .expect("warnings array")
        .iter()
        .any(|warning| warning
            .as_str()
            .expect("warning text")
            .contains("first-read artifacts total 10 bytes")));

    let strict_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            "Review first-read budget.",
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
            "--max-first-read-total-bytes",
            "5",
            "--strict-first-read-budget",
            "--json",
        ])
        .output()?;

    assert!(!strict_output.status.success());
    assert!(
        String::from_utf8_lossy(&strict_output.stderr).contains("first-read budget guard failed")
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_harness_prompt_presentation_redacts_outputs_and_preserves_written_packet() -> Result<()>
{
    let temp = tempdir()?;
    let secret = "sk-secretsecretsecretsecret";
    let first_read = temp.path().join(format!("first-read-token={secret}.md"));
    fs::write(&first_read, "review evidence")?;
    let source_before = fs::read(&first_read)?;
    let task = format!("Review legacy task token={secret}");

    let human_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            &task,
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
        ])
        .output()?;
    assert!(human_output.status.success());
    let human_stdout = String::from_utf8_lossy(&human_output.stdout);
    assert!(human_stdout.contains("# Hosted Harness Start Packet"));
    assert!(human_stdout.contains("Review legacy task token=[REDACTED]"));
    assert!(human_stdout.contains("first-read-token=[REDACTED]"));
    assert!(!human_stdout.contains(secret));
    assert_eq!(fs::read(&first_read)?, source_before);

    let json_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            &task,
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
            "--json",
        ])
        .output()?;
    assert!(json_output.status.success());
    let json_stdout = String::from_utf8_lossy(&json_output.stdout);
    assert!(!json_stdout.contains(secret));
    let packet: serde_json::Value = serde_json::from_slice(&json_output.stdout)?;
    assert_eq!(packet["task"], "Review legacy task token=[REDACTED]");
    assert!(packet["first_reads"][0]["path"]
        .as_str()
        .expect("first-read path")
        .contains("first-read-token=[REDACTED]"));
    assert!(!packet["prompt_markdown"]
        .as_str()
        .expect("prompt markdown")
        .contains(secret));
    assert_eq!(fs::read(&first_read)?, source_before);

    let output_path = temp.path().join(format!("prompt-token={secret}.md"));
    let file_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            &task,
            "--first-read",
            first_read.to_str().expect("utf-8 first read"),
            "--output",
            output_path.to_str().expect("utf-8 output path"),
        ])
        .output()?;
    assert!(file_output.status.success());
    let file_stdout = String::from_utf8_lossy(&file_output.stdout);
    assert!(file_stdout.contains("Wrote hosted harness prompt:"));
    assert!(file_stdout.contains("[REDACTED]"));
    assert!(!file_stdout.contains(secret));
    let written_prompt = fs::read_to_string(&output_path)?;
    assert!(written_prompt.contains(&task));
    assert!(written_prompt.contains(first_read.to_str().expect("utf-8 first read")));

    let oversized = temp.path().join(format!("oversized-token={secret}.md"));
    fs::write(&oversized, vec![b'x'; 65_537])?;
    let strict_output = forager_command(temp.path())
        .args([
            "offdesk",
            "harness-prompt",
            "claude",
            "--task",
            "Review strict budget.",
            "--first-read",
            oversized.to_str().expect("utf-8 oversized first read"),
            "--strict-first-read-budget",
        ])
        .output()?;
    assert!(!strict_output.status.success());
    let strict_stderr = String::from_utf8_lossy(&strict_output.stderr);
    assert!(strict_stderr.contains("first-read budget guard failed"));
    assert!(strict_stderr.contains("[REDACTED]"));
    assert!(!strict_stderr.contains(secret));
    Ok(())
}

#[test]
#[serial]
fn offdesk_plan_registers_multiturn_plan_without_runtime_authority() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    let plan = json!({
        "schema": "offdesk_multiturn_plan.v1",
        "profile_key": "generic",
        "profile_name": "Generic Offdesk Planning",
        "decision": {
            "ready_for_operator_review": true,
            "ready_for_launch_preparation": false,
            "ready_for_enqueue": false,
            "reason": "Operator review is required before any launch preparation."
        },
        "execution_sequence": [
            {
                "id": "phase_1",
                "objective": "Review evidence before any runtime work.",
                "stop_condition": "Stop at a human-readable plan artifact."
            }
        ],
        "authority": {
            "read_only_plan": true,
            "does_not_authorize": [
                "enqueue",
                "launch",
                "approval",
                "file movement",
                "archive",
                "delete",
                "wiki promotion",
                "accepted truth"
            ]
        }
    });
    let input_bytes = serde_json::to_vec_pretty(&plan)?;
    fs::write(&input_path, &input_bytes)?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let registration: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(registration["schema"], "offdesk_plan_registration.v1");
    assert_eq!(registration["artifact_kind"], "offdesk_multiturn_plan");
    assert_eq!(registration["plan_schema"], "offdesk_multiturn_plan.v1");
    assert_eq!(registration["profile_key"], "generic");
    assert_eq!(registration["project_key"], "project");
    assert_eq!(registration["request_id"], "request");
    assert_eq!(registration["task_id"], "task");
    assert_eq!(registration["ready_for_operator_review"], true);
    assert_eq!(registration["ready_for_launch_preparation"], false);
    assert_eq!(registration["ready_for_enqueue"], false);
    assert_eq!(registration["source_sha256"], sha256_hex(&input_bytes));
    assert!(registration["does_not_authorize"]
        .as_array()
        .expect("denials array")
        .iter()
        .any(|item| item == "enqueue"));

    let registration_path = registration["artifacts"]["registration_json"]
        .as_str()
        .expect("registration path");
    let copied_source_path = registration["artifacts"]["copied_source_json"]
        .as_str()
        .expect("source copy path");
    let saved_registration: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(registration_path)?)?;
    assert_eq!(
        saved_registration["source_sha256"],
        registration["source_sha256"]
    );
    let copied_source: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(copied_source_path)?)?;
    assert_eq!(copied_source["schema"], "offdesk_multiturn_plan.v1");
    assert!(profile_dir(temp.path()).join("offdesk_plans").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_plan_rejects_enqueue_ready_multiturn_plan() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    fs::write(
        &input_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "offdesk_multiturn_plan.v1",
            "profile_key": "generic",
            "decision": {
                "ready_for_operator_review": true,
                "ready_for_launch_preparation": false,
                "ready_for_enqueue": true
            },
            "execution_sequence": [
                {
                    "id": "phase_1",
                    "objective": "This should remain a plan only."
                }
            ],
            "authority": {
                "read_only_plan": true,
                "does_not_authorize": [
                    "enqueue",
                    "launch",
                    "approval",
                    "file movement",
                    "archive",
                    "delete",
                    "wiki promotion",
                    "accepted truth"
                ]
            }
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("decision.ready_for_enqueue_must_be_false"));
    assert!(!profile_dir(temp.path()).join("offdesk_plans").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_plan_dry_run_accepts_planner_council_without_writing() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("planner_council_result.json");
    fs::write(
        &input_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "offdesk_planner_council.v1",
            "profile_key": "generic",
            "profile_name": "Generic Offdesk Planning",
            "consensus": {
                "decision": "ready_for_operator_review",
                "agreement": true,
                "ready_for_operator_review": true,
                "ready_for_launch_preparation": false,
                "ready_for_enqueue": false,
                "selected_planner": "planner_a"
            },
            "validation_failures": [],
            "synthesized_plan_path": temp.path().join("SYNTHESIZED_PLAN.json").display().to_string()
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 council path"),
            "--dry-run",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let registration: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(registration["artifact_kind"], "offdesk_planner_council");
    assert_eq!(registration["plan_schema"], "offdesk_planner_council.v1");
    assert_eq!(registration["dry_run"], true);
    assert_eq!(registration["ready_for_operator_review"], true);
    assert_eq!(registration["ready_for_launch_preparation"], false);
    assert_eq!(registration["ready_for_enqueue"], false);
    assert_eq!(registration["consensus"]["selected_planner"], "planner_a");
    assert!(registration["selected_plan_path"]
        .as_str()
        .expect("selected plan path")
        .ends_with("SYNTHESIZED_PLAN.json"));
    assert!(registration["artifacts"]["registration_json"].is_null());
    assert!(!profile_dir(temp.path()).join("offdesk_plans").exists());
    Ok(())
}

#[test]
#[serial]
fn offdesk_plans_list_and_plan_show_registered_artifact() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    let plan = json!({
        "schema": "offdesk_multiturn_plan.v1",
        "profile_key": "generic",
        "decision": {
            "ready_for_operator_review": true,
            "ready_for_launch_preparation": false,
            "ready_for_enqueue": false
        },
        "execution_sequence": [
            {
                "id": "phase_1",
                "objective": "Register and inspect this plan."
            }
        ],
        "authority": {
            "read_only_plan": true,
            "does_not_authorize": [
                "enqueue",
                "launch",
                "approval",
                "file movement",
                "archive",
                "delete",
                "wiki promotion",
                "accepted truth"
            ]
        }
    });
    let input_bytes = serde_json::to_vec_pretty(&plan)?;
    fs::write(&input_path, &input_bytes)?;

    let register_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--project-key",
            "project",
            "--task-id",
            "task",
            "--json",
        ])
        .output()?;
    assert!(
        register_output.status.success(),
        "{}",
        String::from_utf8_lossy(&register_output.stderr)
    );

    let list_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plans",
            "--project-key",
            "project",
            "--latest",
            "--json",
        ])
        .output()?;
    assert!(
        list_output.status.success(),
        "{}",
        String::from_utf8_lossy(&list_output.stderr)
    );
    let plans: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    let plans = plans.as_array().expect("plans array");
    assert_eq!(plans.len(), 1);
    let plan_id = plans[0]["plan_id"].as_str().expect("plan id");
    assert_eq!(plans[0]["registration"]["project_key"], "project");
    assert_eq!(plans[0]["registration"]["task_id"], "task");
    assert_eq!(
        plans[0]["registration"]["source_sha256"],
        sha256_hex(&input_bytes)
    );
    assert_eq!(plans[0]["registration"]["ready_for_enqueue"], false);

    let show_output = forager_command(temp.path())
        .args(["offdesk", "plan-show", plan_id, "--json"])
        .output()?;
    assert!(
        show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&show_output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(shown["plan_id"], plan_id);
    assert_eq!(
        shown["registration"]["artifact_kind"],
        "offdesk_multiturn_plan"
    );
    assert_eq!(
        shown["registration"]["source_sha256"],
        sha256_hex(&input_bytes)
    );
    assert!(shown["registration"]["does_not_authorize"]
        .as_array()
        .expect("denials array")
        .iter()
        .any(|item| item == "launch"));

    let text_list_output = forager_command(temp.path())
        .args(["offdesk", "plans", "--project-key", "project", "--latest"])
        .output()?;
    assert!(
        text_list_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_list_output.stderr)
    );
    let text_list = String::from_utf8_lossy(&text_list_output.stdout);
    assert!(text_list.contains("Registered Offdesk plans"));
    assert!(text_list.contains(plan_id));
    assert!(text_list.contains("plan_review=unreviewed"));
    assert!(text_list.contains("enqueue=false"));

    let text_show_output = forager_command(temp.path())
        .args(["offdesk", "plan-show", plan_id])
        .output()?;
    assert!(
        text_show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_show_output.stderr)
    );
    let text_show = String::from_utf8_lossy(&text_show_output.stdout);
    assert!(text_show.contains(&format!("Registered Offdesk plan: {plan_id}")));
    assert!(text_show.contains("does_not_authorize:"));
    assert!(text_show.contains("launch"));

    let missing_output = forager_command(temp.path())
        .args(["offdesk", "plan-show", "plan_missing", "--json"])
        .output()?;
    assert!(!missing_output.status.success());
    assert!(String::from_utf8_lossy(&missing_output.stderr)
        .contains("Registered Offdesk plan not found: plan_missing"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_plan_review_records_decision_without_runtime_authority() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    let plan = json!({
        "schema": "offdesk_multiturn_plan.v1",
        "profile_key": "generic",
        "decision": {
            "ready_for_operator_review": true,
            "ready_for_launch_preparation": false,
            "ready_for_enqueue": false
        },
        "execution_sequence": [
            {
                "id": "phase_1",
                "objective": "Record an operator review."
            }
        ],
        "authority": {
            "read_only_plan": true,
            "does_not_authorize": [
                "enqueue",
                "launch",
                "approval",
                "file movement",
                "archive",
                "delete",
                "wiki promotion",
                "accepted truth"
            ]
        }
    });
    fs::write(&input_path, serde_json::to_vec_pretty(&plan)?)?;

    let register_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(
        register_output.status.success(),
        "{}",
        String::from_utf8_lossy(&register_output.stderr)
    );
    let registration: serde_json::Value = serde_json::from_slice(&register_output.stdout)?;
    let registry_dir = registration["artifacts"]["registry_dir"]
        .as_str()
        .expect("registry dir");
    let plan_id = Path::new(registry_dir)
        .file_name()
        .expect("registry dir name")
        .to_string_lossy()
        .to_string();

    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reviewer",
            "operator",
            "--reason",
            "The plan is ready for a separate launch-preparation packet.",
            "--follow-up",
            "Prepare launch packet in a separate command.",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "{}",
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["schema"], "offdesk_plan_review.v1");
    assert_eq!(review["plan_id"], plan_id);
    assert_eq!(review["decision"], "approved");
    assert_eq!(review["ready_for_launch_preparation_candidate"], true);
    assert_eq!(review["ready_for_enqueue"], false);
    assert_eq!(review["applies_file_operations"], false);
    assert!(review["does_not_authorize"]
        .as_array()
        .expect("review denials")
        .iter()
        .any(|item| item == "launch"));
    let review_record_path = review["artifacts"]["review_record_json"]
        .as_str()
        .expect("review record path");
    assert!(Path::new(review_record_path).exists());

    let list_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plans",
            "--project-key",
            "project",
            "--latest",
            "--json",
        ])
        .output()?;
    assert!(
        list_output.status.success(),
        "{}",
        String::from_utf8_lossy(&list_output.stderr)
    );
    let plans: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    assert_eq!(plans[0]["review_state"]["status"], "approved");
    assert_eq!(
        plans[0]["review_state"]["ready_for_launch_preparation_candidate"],
        true
    );
    assert_eq!(
        plans[0]["review_state"]["next_safe_action"],
        "prepare_launch_packet"
    );
    assert_eq!(plans[0]["review_count"], 1);
    assert_eq!(plans[0]["latest_review"]["review_id"], review["review_id"]);

    let show_output = forager_command(temp.path())
        .args(["offdesk", "plan-show", &plan_id, "--json"])
        .output()?;
    assert!(
        show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&show_output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(shown["reviews"].as_array().expect("reviews").len(), 1);
    assert_eq!(shown["reviews"][0]["review_id"], review["review_id"]);
    assert_eq!(shown["registration"]["ready_for_enqueue"], false);

    let review_count_before = count_files_with_prefix(Path::new(registry_dir), "plan_review_")?;
    let blocked_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reviewer",
            "operator",
            "--reason",
            "This should fail because approved reviews cannot carry blockers.",
            "--blocker",
            "missing launch packet",
            "--json",
        ])
        .output()?;
    assert!(!blocked_output.status.success());
    assert!(String::from_utf8_lossy(&blocked_output.stderr)
        .contains("approved Offdesk plan review cannot include blockers"));
    assert_eq!(
        count_files_with_prefix(Path::new(registry_dir), "plan_review_")?,
        review_count_before
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_plan_launch_prep_requires_approved_review_and_stays_read_only() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    let plan = json!({
        "schema": "offdesk_multiturn_plan.v1",
        "profile_key": "generic",
        "decision": {
            "ready_for_operator_review": true,
            "ready_for_launch_preparation": false,
            "ready_for_enqueue": false
        },
        "execution_sequence": [
            {
                "id": "phase_1",
                "objective": "Prepare a launch-prep packet only."
            }
        ],
        "authority": {
            "read_only_plan": true,
            "does_not_authorize": [
                "enqueue",
                "launch",
                "approval",
                "file movement",
                "archive",
                "delete",
                "wiki promotion",
                "accepted truth"
            ]
        }
    });
    let input_bytes = serde_json::to_vec_pretty(&plan)?;
    fs::write(&input_path, &input_bytes)?;

    let register_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--json",
        ])
        .output()?;
    assert!(
        register_output.status.success(),
        "{}",
        String::from_utf8_lossy(&register_output.stderr)
    );
    let registration: serde_json::Value = serde_json::from_slice(&register_output.stdout)?;
    let registry_dir = registration["artifacts"]["registry_dir"]
        .as_str()
        .expect("registry dir");
    let plan_id = Path::new(registry_dir)
        .file_name()
        .expect("registry dir name")
        .to_string_lossy()
        .to_string();

    assert_eq!(
        count_files_with_prefix(Path::new(registry_dir), "launch_prep_")?,
        0
    );
    let blocked_output = forager_command(temp.path())
        .args(["offdesk", "plan-launch-prep", &plan_id, "--json"])
        .output()?;
    assert!(!blocked_output.status.success());
    assert!(String::from_utf8_lossy(&blocked_output.stderr)
        .contains("launch-prep requires an approved review"));
    assert_eq!(
        count_files_with_prefix(Path::new(registry_dir), "launch_prep_")?,
        0
    );

    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reason",
            "Approved only for a separate launch-preparation packet.",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "{}",
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: serde_json::Value = serde_json::from_slice(&review_output.stdout)?;

    let revision_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "revision-required",
            "--reason",
            "The latest review requests a revision.",
            "--blocker",
            "Clarify the execution boundary.",
            "--json",
        ])
        .output()?;
    assert!(
        revision_output.status.success(),
        "{}",
        String::from_utf8_lossy(&revision_output.stderr)
    );

    let latest_blocked_output = forager_command(temp.path())
        .args(["offdesk", "plan-launch-prep", &plan_id, "--json"])
        .output()?;
    assert!(!latest_blocked_output.status.success());
    assert!(String::from_utf8_lossy(&latest_blocked_output.stderr)
        .contains("launch-prep requires an approved review"));
    assert_eq!(
        count_files_with_prefix(Path::new(registry_dir), "launch_prep_")?,
        0
    );

    let prep_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-launch-prep",
            &plan_id,
            "--review-id",
            review["review_id"].as_str().expect("approved review id"),
            "--prepared-by",
            "operator",
            "--notes",
            "Prepare execution brief next; do not dispatch here.",
            "--json",
        ])
        .output()?;
    assert!(
        prep_output.status.success(),
        "{}",
        String::from_utf8_lossy(&prep_output.stderr)
    );
    let prep: serde_json::Value = serde_json::from_slice(&prep_output.stdout)?;
    assert_eq!(prep["schema"], "offdesk_plan_launch_prep.v1");
    assert_eq!(prep["plan_id"], plan_id);
    assert_eq!(prep["source_sha256"], sha256_hex(&input_bytes));
    assert_eq!(prep["review_id"], review["review_id"]);
    assert_eq!(prep["launch_preparation_candidate"], true);
    assert_eq!(prep["ready_for_launch"], false);
    assert_eq!(prep["ready_for_enqueue"], false);
    assert_eq!(prep["applies_file_operations"], false);
    assert_eq!(prep["project_key"], "project");
    assert_eq!(prep["request_id"], "request");
    assert_eq!(prep["task_id"], "task");
    assert!(prep["required_first_reads"]
        .as_array()
        .expect("first reads")
        .iter()
        .any(|item| item.as_str() == review["artifacts"]["review_record_json"].as_str()));
    assert!(prep["does_not_authorize"]
        .as_array()
        .expect("prep denials")
        .iter()
        .any(|item| item == "dispatch"));
    let prep_path = prep["artifacts"]["launch_prep_json"]
        .as_str()
        .expect("prep path");
    assert!(Path::new(prep_path).exists());
    assert_eq!(
        count_files_with_prefix(Path::new(registry_dir), "launch_prep_")?,
        1
    );

    let list_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plans",
            "--project-key",
            "project",
            "--latest",
            "--json",
        ])
        .output()?;
    assert!(
        list_output.status.success(),
        "{}",
        String::from_utf8_lossy(&list_output.stderr)
    );
    let plans: serde_json::Value = serde_json::from_slice(&list_output.stdout)?;
    assert_eq!(plans[0]["launch_prep_count"], 1);
    assert_eq!(plans[0]["latest_launch_prep"]["prep_id"], prep["prep_id"]);

    let show_output = forager_command(temp.path())
        .args(["offdesk", "plan-show", &plan_id, "--json"])
        .output()?;
    assert!(
        show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&show_output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(
        shown["launch_preps"]
            .as_array()
            .expect("launch preps")
            .len(),
        1
    );
    assert_eq!(shown["latest_launch_prep"]["prep_id"], prep["prep_id"]);
    assert_eq!(shown["registration"]["ready_for_enqueue"], false);
    Ok(())
}

#[test]
#[serial]
fn offdesk_remote_operator_plans_and_show_are_read_only() -> Result<()> {
    let temp = tempdir()?;
    let input_path = temp.path().join("OVERNIGHT_PLAN.json");
    fs::write(
        &input_path,
        serde_json::to_vec_pretty(&json!({
            "schema": "offdesk_multiturn_plan.v1",
            "profile_key": "generic",
            "decision": {
                "ready_for_operator_review": true,
                "ready_for_launch_preparation": false,
                "ready_for_enqueue": false
            },
            "execution_sequence": [
                {
                    "id": "phase_1",
                    "objective": "Expose this plan to a read-only remote operator surface."
                }
            ],
            "authority": {
                "read_only_plan": true,
                "does_not_authorize": [
                    "enqueue",
                    "launch",
                    "approval",
                    "file movement",
                    "archive",
                    "delete",
                    "wiki promotion",
                    "accepted truth"
                ]
            }
        }))?,
    )?;

    let register_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            input_path.to_str().expect("utf-8 plan path"),
            "--project-key",
            "project",
            "--request-id",
            "request",
            "--task-id",
            "task",
            "--json",
        ])
        .output()?;
    assert!(
        register_output.status.success(),
        "{}",
        String::from_utf8_lossy(&register_output.stderr)
    );
    let registration: serde_json::Value = serde_json::from_slice(&register_output.stdout)?;
    let registry_dir = registration["artifacts"]["registry_dir"]
        .as_str()
        .expect("registry dir");
    let plan_id = Path::new(registry_dir)
        .file_name()
        .expect("registry dir name")
        .to_string_lossy()
        .to_string();

    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reason",
            "Ready for read-only remote inspection.",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "{}",
        String::from_utf8_lossy(&review_output.stderr)
    );

    let plans_output = forager_command(temp.path())
        .args([
            "offdesk",
            "remote-operator",
            "plans",
            "--project-key",
            "project",
            "--latest",
            "--json",
        ])
        .output()?;
    assert!(
        plans_output.status.success(),
        "{}",
        String::from_utf8_lossy(&plans_output.stderr)
    );
    let plans: serde_json::Value = serde_json::from_slice(&plans_output.stdout)?;
    assert_eq!(plans["schema"], "remote_operator_readonly_projection.v1");
    assert_eq!(plans["command"], "plans");
    assert_eq!(plans["transport"], "telegram");
    assert_eq!(plans["read_only"], true);
    assert_eq!(plans["mutation_authorized"], false);
    assert_eq!(plans["approval_authorized"], false);
    assert!(plans["forbidden_remote_intents"]
        .as_array()
        .expect("forbidden intents")
        .iter()
        .any(|item| item == "approve_plan"));
    assert_eq!(plans["payload"]["plan_count"], 1);
    assert_eq!(plans["payload"]["plans"][0]["plan_id"], plan_id);
    assert_eq!(plans["payload"]["plans"][0]["review_status"], "approved");
    assert_eq!(plans["payload"]["plans"][0]["ready_for_enqueue"], false);
    assert!(plans["payload"]["plans"][0]["observed_hash"]
        .as_str()
        .expect("plan observed hash")
        .starts_with("sha256:"));
    assert!(plans["payload"]["plans"][0]["remote_actions"]
        .as_array()
        .expect("remote actions")
        .iter()
        .any(|item| item == "inspect_plan"));

    let show_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "show", &plan_id, "--json"])
        .output()?;
    assert!(
        show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&show_output.stderr)
    );
    let shown: serde_json::Value = serde_json::from_slice(&show_output.stdout)?;
    assert_eq!(shown["schema"], "remote_operator_readonly_projection.v1");
    assert_eq!(shown["command"], "show");
    assert_eq!(shown["payload"]["plan"]["plan_id"], plan_id);
    assert_eq!(
        shown["payload"]["reviews"]
            .as_array()
            .expect("review summaries")
            .len(),
        1
    );
    assert_eq!(
        shown["payload"]["launch_preps"].as_array().unwrap().len(),
        0
    );
    assert!(shown["payload"]["does_not_authorize"]
        .as_array()
        .expect("denials")
        .iter()
        .any(|item| item == "launch"));
    assert!(shown["card"]["disabled_remote_actions"]
        .as_array()
        .expect("disabled actions")
        .iter()
        .any(|item| item == "dispatch"));

    let text_plans_output = forager_command(temp.path())
        .args([
            "offdesk",
            "remote-operator",
            "plans",
            "--project-key",
            "project",
            "--latest",
        ])
        .output()?;
    assert!(
        text_plans_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_plans_output.stderr)
    );
    let text_plans = String::from_utf8_lossy(&text_plans_output.stdout);
    assert!(text_plans.contains("Forager Remote Plans"));
    assert!(text_plans.contains("mode:      read-only"));
    assert!(
        text_plans.contains("remote launch, dispatch, shell execution, and mutation are disabled")
    );

    let text_show_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "show", &plan_id])
        .output()?;
    assert!(
        text_show_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_show_output.stderr)
    );
    let text_show = String::from_utf8_lossy(&text_show_output.stdout);
    assert!(text_show.contains("Forager Remote Plan Detail"));
    assert!(text_show.contains(&format!("plan: {plan_id}")));
    assert!(
        text_show.contains("remote launch, dispatch, shell execution, and mutation are disabled")
    );
    Ok(())
}

#[test]
#[serial]
fn offdesk_remote_operator_status_is_read_only() -> Result<()> {
    let temp = tempdir()?;

    let status_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "status", "--json"])
        .output()?;
    assert!(
        status_output.status.success(),
        "{}",
        String::from_utf8_lossy(&status_output.stderr)
    );
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["schema"], "remote_operator_readonly_projection.v1");
    assert_eq!(status["command"], "status");
    assert_eq!(status["transport"], "telegram");
    assert_eq!(status["read_only"], true);
    assert_eq!(status["mutation_authorized"], false);
    assert_eq!(status["approval_authorized"], false);
    assert_eq!(status["payload"]["profile"], "default");
    assert_eq!(status["card"]["title"], "Forager Remote Status");
    assert!(status["card"]["observed_hash"]
        .as_str()
        .expect("observed hash")
        .starts_with("sha256:"));
    assert!(status["card"]["remote_actions"]
        .as_array()
        .expect("remote actions")
        .iter()
        .any(|item| item == "inspect_status"));
    assert!(status["forbidden_remote_intents"]
        .as_array()
        .expect("forbidden intents")
        .iter()
        .any(|item| item == "dispatch"));

    let text_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "status"])
        .output()?;
    assert!(
        text_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_output.stderr)
    );
    let text = String::from_utf8_lossy(&text_output.stdout);
    assert!(text.contains("Forager Remote Status"));
    assert!(text.contains("mode:      read-only"));
    assert!(text.contains("sessions:"));
    assert!(text.contains("remote launch, dispatch, shell execution, and mutation are disabled"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_remote_operator_pending_is_read_only_and_does_not_expire() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("pending_action_approvals.json"),
        serde_json::to_string_pretty(&json!([
            {
                "approval_id": "approval_stale",
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
                "created_at": now - Duration::minutes(20),
                "expires_at": now - Duration::minutes(10),
                "source_surface": "test"
            }
        ]))?,
    )?;

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "pending", "--json"])
        .output()?;
    assert!(
        pending_output.status.success(),
        "{}",
        String::from_utf8_lossy(&pending_output.stderr)
    );
    let pending: serde_json::Value = serde_json::from_slice(&pending_output.stdout)?;
    assert_eq!(pending["schema"], "remote_operator_readonly_projection.v1");
    assert_eq!(pending["command"], "pending");
    assert_eq!(pending["read_only"], true);
    assert_eq!(pending["mutation_authorized"], false);
    assert_eq!(pending["approval_authorized"], false);
    assert_eq!(pending["payload"]["approval_count"], 1);
    assert_eq!(
        pending["payload"]["approvals"][0]["approval_id"],
        "approval_stale"
    );
    assert_eq!(pending["payload"]["approvals"][0]["status"], "pending");
    assert_eq!(pending["payload"]["approvals"][0]["expired"], true);
    assert_eq!(
        pending["payload"]["approvals"][0]["next_safe_action"]["kind"],
        "approval_expired"
    );
    assert!(pending["payload"]["approvals"][0]["remote_actions"]
        .as_array()
        .expect("remote actions")
        .iter()
        .any(|item| item == "inspect_approval"));
    assert!(pending["forbidden_remote_intents"]
        .as_array()
        .expect("forbidden intents")
        .iter()
        .any(|item| item == "approve_launch"));

    let text_output = forager_command(temp.path())
        .args(["offdesk", "remote-operator", "pending"])
        .output()?;
    assert!(
        text_output.status.success(),
        "{}",
        String::from_utf8_lossy(&text_output.stderr)
    );
    let text = String::from_utf8_lossy(&text_output.stdout);
    assert!(text.contains("Forager Remote Pending"));
    assert!(text.contains("mode:      read-only"));
    assert!(text.contains("approval_stale: dispatch.runtime pending"));
    assert!(text.contains("expired pending approvals: 1"));
    assert!(text.contains("remote launch, dispatch, shell execution, and mutation are disabled"));

    let stored: serde_json::Value = serde_json::from_str(&fs::read_to_string(
        profile_dir.join("pending_action_approvals.json"),
    )?)?;
    assert_eq!(stored[0]["status"], "pending");
    Ok(())
}

#[test]
#[serial]
fn status_json_reports_legacy_profile_dir_when_compat_storage_is_active() -> Result<()> {
    let temp = tempdir()?;
    fs::create_dir_all(legacy_app_dir(temp.path()).join("profiles").join("default"))?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["profile"], "default");
    assert_eq!(status["profile_dir_source"], "legacy");
    assert_eq!(
        reported_path(&status["profile_dir"]),
        expected_path(&legacy_app_dir(temp.path()).join("profiles").join("default"))
    );
    assert_eq!(
        reported_path(&status["app_dir"]),
        expected_path(&legacy_app_dir(temp.path()))
    );
    assert_eq!(status["app_dir_source"], "legacy");
    assert_eq!(
        reported_path(&status["primary_app_dir"]),
        expected_path(&app_dir(temp.path()))
    );
    assert_eq!(status["primary_app_dir_exists"], false);
    assert!(!app_dir(temp.path()).exists());
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
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    Ok(())
}

#[test]
#[serial]
fn status_json_includes_resume_store_next_safe_action() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([resume_state(now)]))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["resume_pending_fresh"], 1);
    assert_eq!(
        status["offdesk_next_safe_actions"][0]["kind"],
        "resume_review_required"
    );
    assert_eq!(
        status["offdesk_next_safe_actions"][0]["commands"][0],
        "forager offdesk resume"
    );
    assert_eq!(
        status["offdesk_next_safe_actions"][0]["requires_operator_review"],
        true
    );
    Ok(())
}

#[test]
#[serial]
fn status_json_orders_resume_store_next_safe_action_by_priority() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            durable_task_with(
                "approval-task",
                "dispatch.runtime",
                "pending_approval",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "completed-task",
                "dispatch.runtime",
                "completed",
                now,
                "true",
                temp.path(),
            ),
            durable_task_with(
                "running-task",
                "dispatch.runtime",
                "running",
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
            )
        ]))?,
    )?;
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([resume_state(now)]))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    let action_kinds: Vec<&str> = status["offdesk_next_safe_actions"]
        .as_array()
        .expect("offdesk next safe actions")
        .iter()
        .map(|action| action["kind"].as_str().expect("action kind"))
        .collect();

    assert_eq!(
        action_kinds,
        vec![
            "approval_pending",
            "resume_review_required",
            "review_required",
            "runtime_monitoring",
            "dispatch_pending",
        ]
    );
    Ok(())
}

#[test]
#[serial]
fn status_json_includes_closeout_required_count() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "completed-task",
            "dispatch.runtime",
            "completed",
            now,
            "true",
            temp.path(),
        )]))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 1);
    assert_eq!(status["closeout_state"]["missing_closeout"], 1);
    assert_eq!(status["closeout_state"]["pending_review"], 0);
    assert_eq!(status["closeout_state"]["revision_required"], 0);
    assert!(status["offdesk_next_safe_actions"]
        .as_array()
        .expect("offdesk next safe actions")
        .iter()
        .any(|action| action["kind"] == "review_required"));

    let status_output = forager_command(temp.path()).args(["status"]).output()?;
    assert!(status_output.status.success());
    let stdout = String::from_utf8_lossy(&status_output.stdout);
    assert!(stdout.contains("1 closeout required"));
    assert!(stdout.contains("Closeout state: 1 missing, 0 pending review, 0 revise/blocked"));
    assert!(stdout.contains("Closeout: run `forager offdesk closeout`"));
    assert!(stdout.contains("Next safe actions:"));
    assert!(stdout.contains("forager ondesk prompt-package"));

    let closeout_dir = profile_dir.join("offdesk_closeouts").join("latest");
    fs::create_dir_all(&closeout_dir)?;
    fs::write(
        closeout_dir.join("closeout_review_20260521T000000Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": now + Duration::minutes(1),
            "verdict": "approved",
            "applies_to_tasks": [
                {
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "completed-task"
                }
            ]
        }))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    assert_eq!(status["closeout_state"]["approved"], 1);

    fs::write(
        closeout_dir.join("closeout_review_20260521T000100Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": now + Duration::minutes(2),
            "verdict": "approved",
            "closeout_receipt": {
                "schema": "closeout_receipt.v1",
                "acceptance_status": "approved_with_followups"
            },
            "applies_to_tasks": [
                {
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "completed-task"
                }
            ]
        }))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 1);
    assert_eq!(status["closeout_state"]["approved_with_followups"], 1);
    assert_eq!(status["closeout_state"]["accepted"], 0);
    assert!(status["offdesk_next_safe_actions"]
        .as_array()
        .expect("offdesk next safe actions")
        .iter()
        .any(|action| action["detail"]
            .as_str()
            .unwrap_or_default()
            .contains("receipt follow-ups")));

    let status_output = forager_command(temp.path()).args(["status"]).output()?;
    assert!(status_output.status.success());
    let stdout = String::from_utf8_lossy(&status_output.stdout);
    assert!(stdout.contains("1 approved with follow-ups"));
    assert!(stdout.contains("Closeout receipt: approved review still has follow-ups"));

    fs::write(
        closeout_dir.join("closeout_review_20260521T000200Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": now + Duration::minutes(3),
            "verdict": "approved",
            "closeout_receipt": {
                "schema": "closeout_receipt.v1",
                "acceptance_status": "accepted"
            },
            "applies_to_tasks": [
                {
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "completed-task"
                }
            ]
        }))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(status_output.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status_output.stdout)?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    assert_eq!(status["closeout_state"]["accepted"], 1);
    assert_eq!(status["closeout_state"]["approved"], 1);
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

#[test]
fn workload_review_allows_wiki_candidate_queue_exception() -> Result<()> {
    let temp = tempdir()?;
    let repo_dir = temp.path().join("repo");
    let out_dir = temp.path().join("out");
    fs::create_dir_all(&repo_dir)?;
    fs::create_dir_all(&out_dir)?;
    fs::write(out_dir.join("run_workload.sh"), "#!/usr/bin/env bash\n")?;
    fs::write(out_dir.join("evidence_bundle.json"), "{}\n")?;
    fs::write(
        out_dir.join("evidence_review.json"),
        serde_json::to_string_pretty(&json!({
            "kind": "evidence_bundle_review",
            "passed": true,
            "decision": "sufficient"
        }))?,
    )?;

    let manifest_path = out_dir.join("prepared_task.json");
    let review_path = out_dir.join("workload_review").join("results.json");
    let manifest = json!({
        "repo": repo_dir,
        "out_dir": out_dir,
        "duration_minutes": 10,
        "max_iterations": 1,
        "model": "qwen3-coder-next:latest",
        "schedule": {
            "mode": "run_until_local",
            "duration_minutes": 10,
            "target_at": "2026-05-22T09:00:00+09:00",
            "timezone": "Asia/Seoul"
        },
        "safety": {
            "repo_read_only": true,
            "writes_only_under_out_dir": false,
            "writes_only_under_out_dir_except_adaptive_wiki_candidate_queue": true,
            "adaptive_wiki_candidate_queue_write": true,
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
            "runner": "local-tmux",
            "approval_required_before_dispatch": true,
            "clean_role_gate_required": true,
            "separate_review_artifact_required": true,
            "episode_council_between_episodes": true
        },
        "council": {
            "mode": "mock",
            "reviewers": ["gpt", "claude"],
            "every": 1
        },
        "preflight": {
            "role_gate": {
                "ready": true,
                "failed": 0,
                "failure_category_counts": {},
                "quality_gate": {
                    "ready_for_long_workload": true
                }
            }
        },
        "workload_command": [
            "python3",
            "scripts/offdesk_twinpaper_autonomy_workload.py",
            "--out-dir",
            out_dir,
            "--evidence-bundle",
            out_dir.join("evidence_bundle.json"),
            "--evidence-review",
            out_dir.join("evidence_review.json")
        ],
        "workload_wrapper": out_dir.join("run_workload.sh"),
        "artifacts": {
            "prepared_task": manifest_path,
            "preflight": out_dir.join("preflight.json"),
            "runner_log": out_dir.join("offdesk-runner.log"),
            "result": out_dir.join("result.json"),
            "report": out_dir.join("REPORT.md"),
            "evidence_bundle": out_dir.join("evidence_bundle.json"),
            "evidence_review": out_dir.join("evidence_review.json")
        },
        "enqueue_args": [
            "forager",
            "offdesk",
            "enqueue",
            "dispatch.runtime",
            "--agent-mode",
            "critique",
            "--provider-id",
            "ollama"
        ]
    });
    fs::write(&manifest_path, serde_json::to_string_pretty(&manifest)?)?;

    let script_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join("offdesk_workload_review_harness.py");
    let output = Command::new("python3")
        .arg(script_path)
        .arg("--manifest")
        .arg(&manifest_path)
        .arg("--out")
        .arg(&review_path)
        .output()?;

    assert!(
        output.status.success(),
        "review harness failed\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let review: serde_json::Value = serde_json::from_slice(&fs::read(&review_path)?)?;
    assert_eq!(review["passed"], true);
    assert_eq!(review["blocking_reasons"], json!([]));
    assert_eq!(
        review["results"][0]["review_stage_decision"],
        "needs_approval"
    );
    Ok(())
}

#[test]
fn generic_prepare_offdesk_workload_writes_reviewed_launch_packet() -> Result<()> {
    let temp = tempdir()?;
    let repo_dir = temp.path().join("repo");
    let out_root = temp.path().join("prepared");
    fs::create_dir_all(&repo_dir)?;
    fs::write(repo_dir.join("README.md"), "# Generic Project\n")?;

    let script_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join("prepare_offdesk_workload.py");
    let output = Command::new("python3")
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .arg(script_path)
        .arg("--profile")
        .arg("generic-profile")
        .arg("--project-key")
        .arg("generic-project")
        .arg("--repo")
        .arg(&repo_dir)
        .arg("--out-root")
        .arg(&out_root)
        .arg("--duration-minutes")
        .arg("0.1")
        .arg("--max-iterations")
        .arg("1")
        .arg("--workload-command")
        .arg("printf 'ok\\n'")
        .output()?;

    assert!(
        output.status.success(),
        "prepare generic workload failed\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let prepared: serde_json::Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(prepared["ready_for_enqueue"], true);
    assert_eq!(prepared["blocking_reasons"], json!([]));
    let manifest_path = PathBuf::from(prepared["manifest"].as_str().expect("manifest path"));
    let preflight_path = PathBuf::from(prepared["preflight"].as_str().expect("preflight path"));
    let launch_path = PathBuf::from(
        prepared["launch_dry_run_report"]
            .as_str()
            .expect("launch packet path"),
    );
    let validation_path = PathBuf::from(
        prepared["long_run_validation_packet"]
            .as_str()
            .expect("validation packet path"),
    );
    assert!(manifest_path.exists());
    assert!(preflight_path.exists());
    assert!(launch_path.exists());
    assert!(validation_path.exists());
    assert!(manifest_path.with_file_name("preflight_ready").exists());

    let manifest: serde_json::Value = serde_json::from_slice(&fs::read(&manifest_path)?)?;
    assert_eq!(manifest["kind"], "forager_offdesk_prepared_workload");
    assert_eq!(manifest["project_key"], "generic-project");
    assert_eq!(manifest["preflight"]["ready_for_enqueue"], true);
    assert_eq!(
        manifest["review_contract"]["schema"],
        "forager_workload_review_contract.v1"
    );
    assert!(manifest["artifacts"]["review_artifact"]
        .as_str()
        .map(PathBuf::from)
        .filter(|path| path.exists())
        .is_some());
    let launch = fs::read_to_string(&launch_path)?;
    let validation = fs::read_to_string(&validation_path)?;
    let manifest_text = fs::read_to_string(&manifest_path)?;
    assert!(launch.contains("Offdesk Launch Dry Run"));
    assert!(validation.contains("Offdesk Long-Run Validation Packet"));
    assert!(!launch.contains("TwinPaper"));
    assert!(!validation.contains("TwinPaper"));
    assert!(!manifest_text.contains("TwinPaper"));
    Ok(())
}

#[test]
#[serial]
fn offdesk_pause_unpause_round_trips_and_tick_holds_dispatch() -> Result<()> {
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;

    // Not paused by default.
    let status = forager_command(temp.path())
        .args(["offdesk", "pause-status", "--json"])
        .output()?;
    assert!(status.status.success());
    let status: serde_json::Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(status["paused"], false);

    // Pause records the reason and flips the state.
    let pause = forager_command(temp.path())
        .args([
            "offdesk",
            "pause",
            "--reason",
            "emergency stop",
            "--by",
            "test",
            "--json",
        ])
        .output()?;
    assert!(
        pause.status.success(),
        "{}",
        String::from_utf8_lossy(&pause.stderr)
    );
    let pause: serde_json::Value = serde_json::from_slice(&pause.stdout)?;
    assert_eq!(pause["paused"], true);
    assert_eq!(pause["reason"], "emergency stop");
    assert!(profile.join("offdesk_operator_pause.json").exists());

    // A tick while paused holds new dispatch instead of launching.
    let tick = forager_command(temp.path())
        .args(["offdesk", "tick", "--limit", "5", "--json"])
        .output()?;
    assert!(tick.status.success());
    let tick: serde_json::Value = serde_json::from_slice(&tick.stdout)?;
    assert_eq!(tick["dispatch_paused"], true);
    assert_eq!(tick["launched"], 0);

    // Unpause clears it.
    let unpause = forager_command(temp.path())
        .args(["offdesk", "unpause", "--by", "test", "--json"])
        .output()?;
    assert!(unpause.status.success());
    let unpause: serde_json::Value = serde_json::from_slice(&unpause.stdout)?;
    assert_eq!(unpause["paused"], false);
    Ok(())
}

#[test]
#[serial]
fn offdesk_pause_presentation_redacts_secrets_for_json_and_humans() -> Result<()> {
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let secret = "sk-secretsecretsecretsecret";

    let pause = forager_command(temp.path())
        .args([
            "offdesk",
            "pause",
            "--reason",
            &format!("emergency token={secret}"),
            "--by",
            "test",
            "--json",
        ])
        .output()?;
    assert!(pause.status.success());
    let json_stdout = String::from_utf8_lossy(&pause.stdout);
    assert!(!json_stdout.contains(secret));
    let pause: serde_json::Value = serde_json::from_slice(&pause.stdout)?;
    assert_eq!(pause["paused"], true);
    assert!(pause["reason"]
        .as_str()
        .expect("pause reason")
        .contains("[REDACTED]"));

    let status = forager_command(temp.path())
        .args(["offdesk", "pause-status"])
        .output()?;
    assert!(status.status.success());
    let human_stdout = String::from_utf8_lossy(&status.stdout);
    assert!(human_stdout.contains("Offdesk dispatch is PAUSED"));
    assert!(human_stdout.contains("reason: emergency token=[REDACTED]"));
    assert!(!human_stdout.contains(secret));

    let unpause = forager_command(temp.path())
        .args(["offdesk", "unpause", "--by", "test"])
        .output()?;
    assert!(unpause.status.success());
    assert!(String::from_utf8_lossy(&unpause.stdout).contains("Offdesk dispatch is active."));
    Ok(())
}

#[test]
#[serial]
fn offdesk_learning_scan_human_presentation_is_actionable_and_operator_safe() -> Result<()> {
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let now = Utc::now();
    let secret = "sk-secretsecretsecretsecret";
    fs::write(
        profile.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([durable_task_with(
            "task-learning-human",
            "dispatch.runtime",
            "failed",
            now,
            "cargo build",
            temp.path()
        )]))?,
    )?;

    let scan = forager_command(temp.path())
        .args(["offdesk", "learning-scan"])
        .output()?;
    assert!(scan.status.success());
    let stdout = String::from_utf8_lossy(&scan.stdout);
    assert!(stdout.contains("Emitted 1 learning candidate(s) (0 already recorded):"));
    assert!(stdout.contains("[task_failed]"));
    assert!(stdout.contains("Candidates are recommendation-only"));
    assert!(!stdout.contains(secret));

    let rescan = forager_command(temp.path())
        .args(["offdesk", "learning-scan"])
        .output()?;
    assert!(rescan.status.success());
    assert!(String::from_utf8_lossy(&rescan.stdout)
        .contains("No new learning signals (1 already recorded)."));
    Ok(())
}

#[test]
#[serial]
fn offdesk_learning_scan_emits_candidates_once_and_tick_auto_scans() -> Result<()> {
    use forager::offdesk::{
        AdaptiveWikiKind, AdaptiveWikiStore, BackgroundRunnerKind, OffdeskTask, OffdeskTaskInput,
        OffdeskTaskStatus, OffdeskTaskStore,
    };

    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let now = Utc::now();

    let failed_task = |task_id: &str, last_error: &str| -> OffdeskTask {
        let mut task = OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some(task_id.to_string()),
                request_id: "req-1".to_string(),
                project_key: "proj-a".to_string(),
                capability_id: "dispatch.runtime".to_string(),
                runner_kind: BackgroundRunnerKind::LocalBackground,
                command: "cargo build".to_string(),
                workdir: "/tmp".to_string(),
                execution_brief: None,
                not_before: None,
                mutation_class: None,
                artifact_refs: Vec::new(),
                implementation_packet: None,
                artifact_kind: None,
                agent_mode: None,
                provider_id: None,
                model: None,
                preview: String::new(),
                reason: String::new(),
                log_artifact_path: None,
                result_artifact_path: None,
            },
            now,
        );
        task.status = OffdeskTaskStatus::Failed;
        task.last_error = Some(last_error.to_string());
        task
    };

    // Seed one failed task, then run the CLI scan.
    let store = OffdeskTaskStore::new(&profile);
    store.save(&[failed_task("task-1", "compile error: token=sk-secret")])?;

    let scan = forager_command(temp.path())
        .args(["offdesk", "learning-scan", "--json"])
        .output()?;
    assert!(
        scan.status.success(),
        "{}",
        String::from_utf8_lossy(&scan.stderr)
    );
    let scan: serde_json::Value = serde_json::from_slice(&scan.stdout)?;
    assert_eq!(scan["emitted"].as_array().unwrap().len(), 1);
    assert_eq!(scan["emitted"][0]["source"], "task_failed");

    // Re-running is idempotent: the durable cursor skips the processed event.
    let rescan = forager_command(temp.path())
        .args(["offdesk", "learning-scan", "--json"])
        .output()?;
    assert!(rescan.status.success());
    let rescan: serde_json::Value = serde_json::from_slice(&rescan.stdout)?;
    assert_eq!(rescan["emitted"].as_array().unwrap().len(), 0);
    assert_eq!(rescan["skipped_already_processed"], 1);

    // A second failed task of the same shape is a new event; a tick auto-scans it.
    store.save(&[
        failed_task("task-1", "compile error: token=sk-secret"),
        failed_task("task-2", "another failure"),
    ])?;
    let tick = forager_command(temp.path())
        .args(["offdesk", "tick", "--limit", "5", "--json"])
        .output()?;
    assert!(
        tick.status.success(),
        "{}",
        String::from_utf8_lossy(&tick.stderr)
    );
    let tick: serde_json::Value = serde_json::from_slice(&tick.stdout)?;
    assert_eq!(tick["learning_signals_emitted"], 1);

    // Both failures share a runner/project, so they merge into one candidate whose
    // occurrence_count reflects the repeated pattern, and no secret leaks.
    let candidates = AdaptiveWikiStore::new(&profile)
        .load_candidates()?
        .candidates;
    assert_eq!(
        candidates.len(),
        1,
        "same-shape failures should merge into one candidate"
    );
    assert_eq!(candidates[0].kind, AdaptiveWikiKind::FailurePattern);
    assert_eq!(candidates[0].occurrence_count, 2);
    assert!(
        !candidates[0].human_summary.contains("sk-secret"),
        "secret leaked into candidate: {}",
        candidates[0].human_summary
    );
    Ok(())
}

#[test]
#[cfg(not(target_os = "linux"))]
fn parent_only_exit_evidence_keeps_retry_and_resume_held() -> Result<()> {
    for (status, action) in [("failed", "retry-task"), ("resume_pending", "resume-task")] {
        let temp = tempdir()?;
        let profile = profile_dir(temp.path());
        fs::create_dir_all(&profile)?;
        let queue = profile.join("offdesk_tasks.json");
        let now = Utc::now();
        fs::write(
            &queue,
            serde_json::to_vec(&json!([durable_task(status, now, "true", temp.path())]))?,
        )?;
        let resume = profile.join("task_resume_state.json");
        fs::write(&resume, serde_json::to_vec(&json!([resume_state(now)]))?)?;
        record_fixture_exit_evidence(&profile)?;
        let queue_before = fs::read(&queue)?;
        let resume_before = fs::read(&resume)?;
        let probes_before = fs::read(profile.join("background_runs.json"))?;
        let out = forager_command(temp.path())
            .args(["offdesk", action, "task", "--json"])
            .output()?;
        assert!(
            !out.status.success(),
            "parent-only receipt released {action}"
        );
        assert_eq!(fs::read(&queue)?, queue_before);
        assert_eq!(fs::read(&resume)?, resume_before);
        assert_eq!(
            fs::read(profile.join("background_runs.json"))?,
            probes_before
        );
    }
    Ok(())
}
