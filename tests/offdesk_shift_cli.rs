use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::Result;
use chrono::{Duration, SecondsFormat, Utc};
use serde_json::{json, Value};
use tempfile::tempdir;

#[path = "support/isolated_process.rs"]
mod isolated_process;

#[path = "offdesk_shift_cli/local_review.rs"]
mod local_review;
#[path = "offdesk_shift_cli/proposal.rs"]
mod proposal;
#[path = "offdesk_shift_cli/serial.rs"]
mod serial;
#[path = "offdesk_shift_cli/weekly.rs"]
mod weekly;

fn forager_command(home: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_forager"));
    command.env("HOME", home);
    command.env("XDG_STATE_HOME", home.join(".local/state"));
    command.env("XDG_CACHE_HOME", home.join(".cache"));
    command.env("XDG_DATA_HOME", home.join(".local/share"));
    // Exercise the production default without inheriting a host override.
    command.env_remove("TOKIO_WORKER_THREADS");
    command.env_remove("FORAGER_PROFILE");
    command.env_remove("AGENT_OF_EMPIRES_PROFILE");
    command.env_remove("FORAGER_DEBUG");
    command.env_remove("AGENT_OF_EMPIRES_DEBUG");
    #[cfg(target_os = "linux")]
    command.env("XDG_CONFIG_HOME", home.join(".config"));
    command
}

fn app_dir(home: &Path) -> PathBuf {
    #[cfg(target_os = "linux")]
    {
        home.join(".config/forager")
    }
    #[cfg(not(target_os = "linux"))]
    {
        home.join(".forager")
    }
}

fn profile_dir(home: &Path) -> PathBuf {
    app_dir(home).join("profiles/default")
}

fn prepared_shift_ledger() -> Value {
    json!({
        "schema": "offdesk_shift_ledger.v1",
        "manifests": [{
            "schema": "offdesk_shift_manifest.v1",
            "shift_id": "shift_cli_1",
            "profile": "default",
            "created_at": "2026-08-28T00:00:00Z",
            "window": {
                "starts_at": "2026-08-28T00:00:00Z",
                "stops_at": "2026-08-28T09:00:00Z",
                "timezone": "Asia/Seoul"
            },
            "plan_id": "plan_cli_1",
            "plan_review_id": "review_cli_1",
            "plan_source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "plan_review_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            "task_bindings": [],
            "routine_bindings": [{
                "routine_id": "morning_review",
                "routine_kind": "morning_review_brief",
                "schedule": {
                    "schedule_kind": "once",
                    "at": "2026-08-28T08:30:00Z"
                }
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
    })
}

fn write_shift_ledger(home: &Path, mode: u32) -> Result<PathBuf> {
    let profile = profile_dir(home);
    fs::create_dir_all(&profile)?;
    let ledger = profile.join("offdesk_shifts.v1.json");
    fs::write(
        &ledger,
        serde_json::to_vec_pretty(&prepared_shift_ledger())?,
    )?;
    #[cfg(unix)]
    fs::set_permissions(&ledger, fs::Permissions::from_mode(mode))?;
    let _ = mode;
    Ok(ledger)
}

fn rfc3339(value: chrono::DateTime<Utc>) -> String {
    value.to_rfc3339_opts(SecondsFormat::Secs, true)
}

fn register_review_and_enqueue(
    home: &Path,
    project_root: &Path,
    starts_at: chrono::DateTime<Utc>,
    stops_at: chrono::DateTime<Utc>,
) -> Result<(String, String)> {
    register_review_and_enqueue_with_source(home, project_root, starts_at, stops_at, None)
}

fn register_review_and_enqueue_with_source(
    home: &Path,
    project_root: &Path,
    starts_at: chrono::DateTime<Utc>,
    stops_at: chrono::DateTime<Utc>,
    artifact: Option<&str>,
) -> Result<(String, String)> {
    fs::create_dir_all(project_root)?;
    let plan_path = home.join("OVERNIGHT_PLAN.json");
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
                "objective": "Run one exact reviewed task during the bounded shift."
            }],
            "authority": {
                "read_only_plan": true,
                "does_not_authorize": [
                    "enqueue", "launch", "approval", "file movement", "archive",
                    "delete", "wiki promotion", "accepted truth"
                ]
            }
        }))?,
    )?;
    let registration_output = forager_command(home)
        .args([
            "offdesk",
            "plan",
            plan_path.to_str().expect("UTF-8 plan path"),
            "--project-key",
            "project",
            "--request-id",
            "request_shift_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(
        registration_output.status.success(),
        "plan registration failed: {}",
        String::from_utf8_lossy(&registration_output.stderr)
    );
    let registration: Value = serde_json::from_slice(&registration_output.stdout)?;
    let plan_id = Path::new(
        registration["artifacts"]["registry_dir"]
            .as_str()
            .expect("registry directory"),
    )
    .file_name()
    .expect("plan ID")
    .to_string_lossy()
    .into_owned();

    let review_output = forager_command(home)
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reason",
            "Reviewed for one bounded shift.",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "plan review failed: {}",
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: Value = serde_json::from_slice(&review_output.stdout)?;
    let review_id = review["review_id"].as_str().expect("review ID").to_string();

    let brief_path = home.join("execution_brief.json");
    fs::write(
        &brief_path,
        serde_json::to_vec_pretty(&json!({
            "request_id": "request_shift_1",
            "task_id": "task_shift_1",
            "project_key": "project",
            "approved": true,
            "allowed_runtime_mutations": ["dispatch.runtime"],
            "allowed_canonical_mutations": [],
            "fresh_until": rfc3339(stops_at)
        }))?,
    )?;
    let mut enqueue = forager_command(home);
    enqueue.args([
        "offdesk",
        "enqueue",
        "dispatch.runtime",
        "--runner",
        "local-background",
        "--project-key",
        "project",
        "--request-id",
        "request_shift_1",
        "--task-id",
        "task_shift_1",
        "--cmd",
        "printf shift-safe",
        "--workdir",
        project_root.to_str().expect("UTF-8 project root"),
        "--brief",
        brief_path.to_str().expect("UTF-8 brief path"),
        "--mutation-class",
        "dispatch.runtime",
        "--not-before",
        &rfc3339(starts_at),
        "--json",
    ]);
    if let Some(artifact) = artifact {
        enqueue.args(["--artifact", artifact]);
    }
    let enqueue_output = enqueue.output()?;
    assert!(
        enqueue_output.status.success(),
        "task enqueue failed: {}",
        String::from_utf8_lossy(&enqueue_output.stderr)
    );
    Ok((plan_id, review_id))
}

fn prepare_shift_and_approve(
    home: &Path,
    plan_id: &str,
    review_id: &str,
    shift_id: &str,
    starts_at: chrono::DateTime<Utc>,
    stops_at: chrono::DateTime<Utc>,
) -> Result<String> {
    prepare_shift_and_approve_with_read_policy(
        home, plan_id, review_id, shift_id, starts_at, stops_at, false,
    )
}

fn prepare_shift_and_approve_with_read_policy(
    home: &Path,
    plan_id: &str,
    review_id: &str,
    shift_id: &str,
    starts_at: chrono::DateTime<Utc>,
    stops_at: chrono::DateTime<Utc>,
    read_policy: bool,
) -> Result<String> {
    let prepare = forager_command(home)
        .args([
            "offdesk",
            "shift",
            "prepare",
            plan_id,
            "--review-id",
            review_id,
            "--shift-id",
            shift_id,
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .args(if read_policy {
            vec!["--read-policy", "linux-landlock-workdir-and-runtime-only"]
        } else {
            vec![]
        })
        .output()?;
    assert!(
        prepare.status.success(),
        "shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let pending = forager_command(home)
        .args(["offdesk", "shift", "arm", shift_id, "--json"])
        .output()?;
    assert!(pending.status.success());
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"]
        .as_str()
        .expect("approval ID")
        .to_string();
    let approval = forager_command(home)
        .args(["offdesk", "ok", &approval_id, "--by", "operator", "--json"])
        .output()?;
    assert!(approval.status.success());
    Ok(approval_id)
}

fn approve_pending_dispatch(home: &Path) -> Result<()> {
    let pending = forager_command(home)
        .args(["offdesk", "pending", "--json"])
        .output()?;
    assert!(pending.status.success());
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    assert_eq!(pending.as_array().unwrap().len(), 1);
    assert_eq!(pending[0]["action"], "dispatch.runtime");
    assert_eq!(pending[0]["task_id"], "task_shift_1");
    let approval_id = pending[0]["approval_id"].as_str().unwrap();
    let approved = forager_command(home)
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(
        approved.status.success(),
        "{}",
        String::from_utf8_lossy(&approved.stderr)
    );
    Ok(())
}

#[test]
fn shift_dispatch_requires_fresh_approval_even_with_approved_execution_brief() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let arm_approval = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_fresh_dispatch",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_fresh_dispatch",
            "--approval-id",
            &arm_approval,
            "--json",
        ])
        .output()?;
    assert!(
        armed.status.success(),
        "{}",
        String::from_utf8_lossy(&armed.stderr)
    );

    let tick_args = [
        "offdesk",
        "tick",
        "--shift-id",
        "shift_fresh_dispatch",
        "--task-id",
        "task_shift_1",
        "--limit",
        "1",
        "--json",
    ];
    let first = forager_command(temp.path()).args(tick_args).output()?;
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let first: Value = serde_json::from_slice(&first.stdout)?;
    assert_eq!(first["launched"], 0);
    assert_eq!(first["pending_approval"], 1);
    let shift = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_fresh_dispatch", "--json"])
        .output()?;
    let shift: Value = serde_json::from_slice(&shift.stdout)?;
    assert_eq!(shift["task_launch_reservations"], json!([]));

    let pending = forager_command(temp.path())
        .args(["offdesk", "pending", "--json"])
        .output()?;
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    assert_eq!(pending.as_array().unwrap().len(), 1);
    assert_eq!(pending[0]["action"], "dispatch.runtime");
    assert_eq!(pending[0]["task_id"], "task_shift_1");
    let approval_id = pending[0]["approval_id"].as_str().unwrap();
    let approved = forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(
        approved.status.success(),
        "{}",
        String::from_utf8_lossy(&approved.stderr)
    );

    let second = forager_command(temp.path()).args(tick_args).output()?;
    assert!(
        second.status.success(),
        "{}",
        String::from_utf8_lossy(&second.stderr)
    );
    let second: Value = serde_json::from_slice(&second.stdout)?;
    assert_eq!(second["launched"], 1);
    Ok(())
}

#[test]
fn shift_start_preview_is_repeatable_and_writes_no_runtime_state() -> Result<()> {
    fn snapshot(root: &Path) -> Result<std::collections::BTreeMap<std::path::PathBuf, Vec<u8>>> {
        let mut result = std::collections::BTreeMap::new();
        if !root.exists() {
            return Ok(result);
        }
        for entry in fs::read_dir(root)? {
            let path = entry?.path();
            if path.is_dir() {
                result.extend(snapshot(&path)?);
            } else {
                result.insert(path.clone(), fs::read(path)?);
            }
        }
        Ok(result)
    }
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let start = Utc::now() - Duration::minutes(1);
    let stop = Utc::now() + Duration::hours(6);
    let (plan, review) = register_review_and_enqueue(temp.path(), &root, start, stop)?;
    let before = snapshot(&profile_dir(temp.path()))?;
    let mut previous = None;
    for _ in 0..2 {
        let output = forager_command(temp.path())
            .args([
                "offdesk",
                "shift",
                "preview",
                &plan,
                "--review-id",
                &review,
                "--starts-at",
                &rfc3339(start),
                "--stops-at",
                &rfc3339(stop),
                "--task-id",
                "task_shift_1",
                "--json",
            ])
            .output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let value: Value = serde_json::from_slice(&output.stdout)?;
        assert_eq!(value["schema"], "offdesk_shift_start_preview.v1");
        assert_eq!(value["task_count"], 1);
        assert_eq!(value["estimated_wall_time"], "unavailable");
        assert_eq!(
            value["task_cost_ceiling"],
            "not_established_by_routine_budget"
        );
        assert_eq!(
            value["stop_policy"],
            "stop_new_dispatch_not_process_shutdown"
        );
        if let Some(previous) = previous {
            assert_eq!(value, previous);
        }
        previous = Some(value);
        assert_eq!(snapshot(&profile_dir(temp.path()))?, before);
    }
    let bad = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "preview",
            &plan,
            "--review-id",
            &review,
            "--starts-at",
            &rfc3339(start),
            "--stops-at",
            &rfc3339(stop),
            "--task-id",
            "unknown",
            "--json",
        ])
        .output()?;
    assert!(!bad.status.success());
    assert_eq!(snapshot(&profile_dir(temp.path()))?, before);
    assert!(!profile_dir(temp.path())
        .join("offdesk_shifts.v1.json")
        .exists());
    Ok(())
}

#[test]
fn shift_candidates_reports_eligibility_exclusions_and_empty_scope_without_writes() -> Result<()> {
    fn snapshot(root: &Path) -> Result<std::collections::BTreeMap<PathBuf, Vec<u8>>> {
        let mut files = std::collections::BTreeMap::new();
        for entry in fs::read_dir(root)? {
            let path = entry?.path();
            if path.is_dir() {
                files.extend(snapshot(&path)?);
            } else {
                files.insert(path.clone(), fs::read(path)?);
            }
        }
        Ok(files)
    }
    let temp = tempdir()?;
    let root = temp.path().join("project");
    let start = Utc::now() - Duration::minutes(1);
    let stop = Utc::now() + Duration::hours(6);
    let (plan, review) = register_review_and_enqueue(temp.path(), &root, start, stop)?;
    let registry = temp.path().join("projects.toml");
    fs::write(&registry, "schema = \"forager_project_registry.v1\"\n[projects.project]\nworkspace_patterns = [\"project\"]\n[projects.empty]\nworkspace_patterns = [\"empty\"]\n")?;
    let queue = profile_dir(temp.path()).join("offdesk_tasks.json");
    let initial = fs::read(&queue)?;
    let before = snapshot(&profile_dir(temp.path()))?;
    let run = |project: &str| -> Result<std::process::Output> {
        Ok(forager_command(temp.path())
            .env("FORAGER_PROJECT_REGISTRY", &registry)
            .args([
                "offdesk",
                "shift",
                "candidates",
                &plan,
                "--review-id",
                &review,
                "--project-key",
                project,
                "--starts-at",
                &rfc3339(start),
                "--stops-at",
                &rfc3339(stop),
                "--json",
            ])
            .output()?)
    };
    let first = run("project")?;
    assert!(
        first.status.success(),
        "{}",
        String::from_utf8_lossy(&first.stderr)
    );
    let value: Value = serde_json::from_slice(&first.stdout)?;
    assert_eq!(value["eligible_task_ids"], json!(["task_shift_1"]));
    assert_eq!(value["ownership_and_resource_conflicts"], "not_verified");
    assert_eq!(
        value,
        serde_json::from_slice::<Value>(&run("project")?.stdout)?
    );
    assert_eq!(fs::read(&queue)?, initial);
    let empty: Value = serde_json::from_slice(&run("empty")?.stdout)?;
    assert_eq!(empty["selection_status"], "no_eligible_work");
    assert_eq!(empty["outside_scope_count"], 1);
    assert!(!run("unregistered")?.status.success());
    assert_eq!(snapshot(&profile_dir(temp.path()))?, before);
    let mut occupied: Value = serde_json::from_slice(&initial)?;
    let mut running = occupied[0].clone();
    running["task_id"] = json!("outside_scope_running");
    running["project_key"] = json!("another_project");
    running["status"] = json!("running");
    occupied.as_array_mut().unwrap().push(running);
    fs::write(&queue, serde_json::to_vec(&occupied)?)?;
    let occupied_before = snapshot(&profile_dir(temp.path()))?;
    let conflicts: Value = serde_json::from_slice(&run("project")?.stdout)?;
    assert_eq!(conflicts["queue_conflicts"]["status"], "conflicts_detected");
    assert_eq!(
        conflicts["queue_conflicts"]["conflicts"][0]["reason"],
        "active_or_uncertain_queue_occupancy"
    );
    let preview = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "preview",
            &plan,
            "--review-id",
            &review,
            "--task-id",
            "task_shift_1",
            "--starts-at",
            &rfc3339(start),
            "--stops-at",
            &rfc3339(stop),
            "--json",
        ])
        .output()?;
    assert!(
        preview.status.success(),
        "{}",
        String::from_utf8_lossy(&preview.stderr)
    );
    let preview: Value = serde_json::from_slice(&preview.stdout)?;
    assert_eq!(preview["queue_conflicts"]["status"], "conflicts_detected");
    assert_eq!(
        preview["tasks"][0]["dependency_status"],
        "unbound_not_verified"
    );
    assert_eq!(snapshot(&profile_dir(temp.path()))?, occupied_before);
    let mut tasks: Value = serde_json::from_slice(&initial)?;
    tasks[0]["status"] = json!("cancelled");
    fs::write(&queue, serde_json::to_vec(&tasks)?)?;
    let cancelled_bytes = fs::read(&queue)?;
    let before_cancelled = snapshot(&profile_dir(temp.path()))?;
    let excluded_output = run("project")?;
    assert!(
        excluded_output.status.success(),
        "{}",
        String::from_utf8_lossy(&excluded_output.stderr)
    );
    let excluded: Value = serde_json::from_slice(&excluded_output.stdout)?;
    assert_eq!(excluded["selection_status"], "no_eligible_work");
    assert_eq!(excluded["candidates"][0]["status"], "excluded");
    assert!(excluded["candidates"][0]["reason"].is_string());
    assert_ne!(value["queue_sha256"], excluded["queue_sha256"]);
    assert_eq!(fs::read(&queue)?, cancelled_bytes);
    assert_eq!(snapshot(&profile_dir(temp.path()))?, before_cancelled);
    let duplicate = tasks[0].clone();
    tasks.as_array_mut().unwrap().push(duplicate);
    fs::write(&queue, serde_json::to_vec(&tasks)?)?;
    let duplicated = run("project")?;
    assert!(!duplicated.status.success());
    assert!(String::from_utf8_lossy(&duplicated.stderr).contains("duplicate task IDs"));
    fs::write(&queue, b"[]")?;
    let empty_queue: Value = serde_json::from_slice(&run("project")?.stdout)?;
    assert_eq!(empty_queue["selection_status"], "no_eligible_work");
    assert_eq!(empty_queue["candidate_count"], 0);
    assert!(!profile_dir(temp.path())
        .join("offdesk_shifts.v1.json")
        .exists());
    Ok(())
}

fn prepare_and_arm_morning_shift(
    home: &Path,
    plan_id: &str,
    review_id: &str,
    shift_id: &str,
    starts_at: chrono::DateTime<Utc>,
    stops_at: chrono::DateTime<Utc>,
    morning_at: chrono::DateTime<Utc>,
) -> Result<()> {
    let prepare = forager_command(home)
        .args([
            "offdesk",
            "shift",
            "prepare",
            plan_id,
            "--review-id",
            review_id,
            "--shift-id",
            shift_id,
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "morning shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let pending = forager_command(home)
        .args(["offdesk", "shift", "arm", shift_id, "--json"])
        .output()?;
    assert!(pending.status.success());
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    assert!(forager_command(home)
        .args(["offdesk", "ok", approval_id, "--by", "operator", "--json"])
        .output()?
        .status
        .success());
    let arm = forager_command(home)
        .args([
            "offdesk",
            "shift",
            "arm",
            shift_id,
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        arm.status.success(),
        "morning shift arm failed: {}",
        String::from_utf8_lossy(&arm.stderr)
    );
    Ok(())
}

#[test]
fn shift_read_only_commands_create_no_state_when_profile_is_absent() -> Result<()> {
    let temp = tempdir()?;

    let policies = forager_command(temp.path())
        .args(["offdesk", "shift", "enterprise-policies", "--json"])
        .output()?;
    assert!(
        policies.status.success(),
        "enterprise policy listing failed: {}",
        String::from_utf8_lossy(&policies.stderr)
    );
    let policy_report: Value = serde_json::from_slice(&policies.stdout)?;
    assert_eq!(
        policy_report["schema"],
        "offdesk_shift_enterprise_policy_report.v1"
    );
    assert_eq!(policy_report["default_profile_key"], "single_owner_local");
    assert_eq!(policy_report["profiles"].as_array().unwrap().len(), 3);
    assert_eq!(policy_report["authority"]["read_only"], true);
    assert_eq!(
        policy_report["authority"]["authorizes_child_task_network"],
        false
    );
    assert!(!app_dir(temp.path()).exists());

    let host_policy = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "host-policy-probe",
            "linux-bubblewrap-external-ip-deny",
            "--json",
        ])
        .output()?;
    assert!(
        host_policy.status.success(),
        "host policy probe failed: {}",
        String::from_utf8_lossy(&host_policy.stderr)
    );
    let host_policy: Value = serde_json::from_slice(&host_policy.stdout)?;
    assert_eq!(host_policy["schema"], "offdesk_shift_host_policy_report.v1");
    assert!(matches!(
        host_policy["status"].as_str(),
        Some("verified" | "unavailable")
    ));
    assert_eq!(host_policy["authority"]["read_only"], true);
    assert_eq!(
        host_policy["authority"]["creates_profile_or_migration_state"],
        false
    );
    assert_eq!(host_policy["authority"]["authorizes_task_dispatch"], false);
    assert!(!app_dir(temp.path()).exists());

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(
        status.status.success(),
        "status failed: {}",
        String::from_utf8_lossy(&status.stderr)
    );
    let report: Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(report["schema"], "offdesk_shift_status_report.v1");
    assert_eq!(report["record_count"], 0);
    assert_eq!(report["shifts"], json!([]));
    assert!(!app_dir(temp.path()).exists());

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "--json"])
        .output()?;
    assert!(!show.status.success());
    assert!(String::from_utf8_lossy(&show.stderr).contains("no bounded Offdesk shift exists"));
    assert!(!app_dir(temp.path()).exists());

    let council = forager_command(temp.path())
        .args(["offdesk", "shift", "council-packet", "--json"])
        .output()?;
    assert!(!council.status.success());
    assert!(String::from_utf8_lossy(&council.stderr).contains("no bounded Offdesk shift exists"));
    assert!(!app_dir(temp.path()).exists());

    let retention = forager_command(temp.path())
        .args(["offdesk", "shift", "retention-report", "--json"])
        .output()?;
    assert!(retention.status.success());
    let report: Value = serde_json::from_slice(&retention.stdout)?;
    assert_eq!(report["schema"], "offdesk_shift_retention_report.v1");
    assert_eq!(report["summary"]["shift_count"], 0);
    assert_eq!(report["authority"]["read_only"], true);
    assert!(!app_dir(temp.path()).exists());
    Ok(())
}

#[test]
fn shift_retention_report_preserves_ledger_and_only_recommends_derived_compaction() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(2);
    let stops_at = Utc::now() + Duration::hours(2);
    let morning_at = Utc::now() - Duration::minutes(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    prepare_and_arm_morning_shift(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_retention_1",
        starts_at,
        stops_at,
        morning_at,
    )?;

    let started = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_retention_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(
        started.status.success(),
        "routine start failed: {}",
        String::from_utf8_lossy(&started.stderr)
    );
    let started: Value = serde_json::from_slice(&started.stdout)?;
    let run_id = started["reservation"]["run_id"].as_str().expect("run ID");
    let evidence_dir = temp.path().join("shift-evidence");
    fs::create_dir(&evidence_dir)?;
    let review_json = evidence_dir.join("morning-review.json");
    let review_markdown = evidence_dir.join("morning-review.md");
    let raw_log = evidence_dir.join("raw-review.log");
    fs::write(&review_json, b"{}\n")?;
    fs::write(&review_markdown, b"# Review\n")?;
    fs::write(&raw_log, b"raw evidence\n")?;
    let completed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_retention_1",
            run_id,
            "--outcome",
            "succeeded",
            "--summary",
            "morning review evidence recorded",
            "--evidence",
            review_json.to_str().expect("UTF-8 path"),
            "--evidence",
            review_markdown.to_str().expect("UTF-8 path"),
            "--evidence",
            raw_log.to_str().expect("UTF-8 path"),
            "--usage-complete",
            "--process-starts",
            "1",
            "--external-calls",
            "1",
            "--json",
        ])
        .output()?;
    assert!(
        completed.status.success(),
        "routine completion failed: {}",
        String::from_utf8_lossy(&completed.stderr)
    );
    let disarmed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "disarm",
            "shift_retention_1",
            "--reason",
            "morning handoff complete",
            "--json",
        ])
        .output()?;
    assert!(disarmed.status.success());

    let ledger = profile_dir(temp.path()).join("offdesk_shifts.v1.json");
    let ledger_before = fs::read(&ledger)?;
    let json_before = fs::read(&review_json)?;
    let markdown_before = fs::read(&review_markdown)?;
    let raw_before = fs::read(&raw_log)?;
    let retention = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "retention-report",
            "--shift-id",
            "shift_retention_1",
            "--compact-after-days",
            "0",
            "--json",
        ])
        .output()?;
    assert!(
        retention.status.success(),
        "retention report failed: {}",
        String::from_utf8_lossy(&retention.stderr)
    );
    let report: Value = serde_json::from_slice(&retention.stdout)?;
    assert_eq!(report["schema"], "offdesk_shift_retention_report.v1");
    assert_eq!(
        report["source_snapshot_schema"],
        "offdesk_shift_store_snapshot.v1"
    );
    assert_eq!(report["selected_shift_id"], "shift_retention_1");
    assert_eq!(report["policy"]["canonical_ledger_disposition"], "preserve");
    assert_eq!(
        report["policy"]["canonical_receipt_disposition"],
        "preserve"
    );
    assert_eq!(
        report["policy"]["wiki_candidate_disposition"],
        "preserve_candidate_queue"
    );
    assert_eq!(report["summary"]["evidence_reference_count"], 3);
    assert_eq!(report["summary"]["compaction_candidate_count"], 2);
    assert_eq!(report["summary"]["preserved_evidence_reference_count"], 1);
    assert_eq!(report["summary"]["items_complete"], true);
    assert_eq!(report["authority"]["source_ledger_mutated"], false);
    assert_eq!(report["authority"]["evidence_mutated"], false);
    assert_eq!(report["authority"]["compaction_authorized"], false);
    assert_eq!(report["authority"]["deletion_authorized"], false);
    assert_eq!(
        report["authority"]["requires_separate_retention_approval"],
        true
    );
    let items = report["items"].as_array().expect("retention items");
    assert_eq!(
        items
            .iter()
            .filter(|item| item["disposition"] == "compact_derived_view_candidate")
            .count(),
        2
    );
    assert!(items
        .iter()
        .all(|item| item["live_artifact_verified"] == false));
    assert!(items
        .iter()
        .all(|item| item["ledger_receipt_preserved"] == true));
    let raw_item = items
        .iter()
        .find(|item| {
            item["reference"]
                .as_str()
                .is_some_and(|value| value.ends_with("raw-review.log"))
        })
        .expect("raw evidence item");
    assert_eq!(raw_item["disposition"], "preserve");
    assert_eq!(raw_item["reason"], "non_derived_evidence");

    assert_eq!(fs::read(&ledger)?, ledger_before);
    assert_eq!(fs::read(&review_json)?, json_before);
    assert_eq!(fs::read(&review_markdown)?, markdown_before);
    assert_eq!(fs::read(&raw_log)?, raw_before);

    let human = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "retention-report",
            "--shift-id",
            "shift_retention_1",
            "--compact-after-days",
            "0",
        ])
        .output()?;
    assert!(human.status.success());
    let stdout = String::from_utf8_lossy(&human.stdout);
    assert!(stdout.contains("canonical ledger records preserved"));
    assert!(stdout.contains("2 compaction candidate(s)"));
    assert!(stdout.contains("compaction authorized: false"));
    assert_eq!(fs::read(&ledger)?, ledger_before);

    let missing = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "retention-report",
            "--shift-id",
            "missing_shift",
            "--json",
        ])
        .output()?;
    assert!(!missing.status.success());
    assert!(String::from_utf8_lossy(&missing.stderr)
        .contains("bounded Offdesk shift not found: missing_shift"));
    Ok(())
}

#[test]
fn shift_council_packet_compares_exact_goal_evidence_drift_and_remaining_budget() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::minutes(59);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_council_packet_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--max-routine-process-starts",
            "12",
            "--max-routine-disk-bytes",
            "4096",
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );

    let packet = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "council-packet",
            "shift_council_packet_1",
            "--json",
        ])
        .output()?;
    assert!(
        packet.status.success(),
        "Council packet failed: {}",
        String::from_utf8_lossy(&packet.stderr)
    );
    let packet: Value = serde_json::from_slice(&packet.stdout)?;
    assert_eq!(
        packet["schema"],
        "offdesk_shift_council_escalation_packet.v1"
    );
    assert_eq!(
        packet["original_goal"]["summary"],
        "Run one exact reviewed task during the bounded shift."
    );
    assert_eq!(packet["original_goal"]["objective_count"], 1);
    assert_eq!(
        packet["original_goal"]["provenance"],
        json!(["plan.execution_sequence[0].objective"])
    );
    assert_eq!(packet["current_evidence"]["review_status"], "prepared");
    assert_eq!(
        packet["drift"]["assessment"],
        "no_deterministic_drift_detected"
    );
    assert_eq!(
        packet["remaining_budget"]["task"]["remaining_unique_task_launches"],
        1
    );
    assert_eq!(
        packet["remaining_budget"]["task"]["per_task"][0]["remaining_attempts"],
        1
    );
    assert_eq!(
        packet["remaining_budget"]["routine_resources"]["remaining"]["process_starts"],
        12
    );
    assert_eq!(
        packet["decision"]["options"]
            .as_array()
            .expect("Council options")
            .iter()
            .map(|option| option["id"].as_str().expect("option ID"))
            .collect::<Vec<_>>(),
        vec!["continue", "revise", "handoff", "block"]
    );
    assert_eq!(packet["decision"]["default_if_no_decision"], "block");
    assert_eq!(packet["authority"]["review_only"], true);
    assert_eq!(
        packet["authority"]["recommendation_authorizes_execution"],
        false
    );
    assert_eq!(packet["authority"]["plan_revision_authorized"], false);

    let human = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "council-packet",
            "shift_council_packet_1",
        ])
        .output()?;
    assert!(human.status.success());
    let human = String::from_utf8_lossy(&human.stdout);
    assert!(human.contains("Bounded Offdesk Shift Council Escalation Packet"));
    assert!(human.contains("default without a decision: block"));
    assert!(human.contains("does not authorize execution or revision"));
    Ok(())
}

#[test]
fn shift_council_packet_rejects_changed_bound_plan_source() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::minutes(59);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_council_source_drift_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(prepare.status.success());
    fs::write(
        profile_dir(temp.path())
            .join("offdesk_plans")
            .join(&plan_id)
            .join("source.json"),
        b"{}",
    )?;

    let packet = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "council-packet",
            "shift_council_source_drift_1",
            "--json",
        ])
        .output()?;
    assert!(!packet.status.success());
    assert!(String::from_utf8_lossy(&packet.stderr)
        .contains("shift plan source bytes do not match registration"));
    Ok(())
}

#[test]
fn shift_show_and_status_read_exact_prepared_ledger_without_migration() -> Result<()> {
    let temp = tempdir()?;
    write_shift_ledger(temp.path(), 0o600)?;

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(
        status.status.success(),
        "status failed: {}",
        String::from_utf8_lossy(&status.stderr)
    );
    let report: Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(report["record_count"], 1);
    assert_eq!(report["active_shift_id"], Value::Null);
    assert_eq!(report["latest_shift_id"], "shift_cli_1");
    assert_eq!(report["shifts"][0]["shift_id"], "shift_cli_1");
    assert_eq!(report["shifts"][0]["status"], Value::Null);
    assert_eq!(report["shifts"][0]["routine_count"], 1);

    for args in [
        vec!["offdesk", "shift", "show", "--json"],
        vec!["offdesk", "shift", "show", "shift_cli_1", "--json"],
    ] {
        let show = forager_command(temp.path()).args(&args).output()?;
        assert!(
            show.status.success(),
            "show failed: {args:?}: {}",
            String::from_utf8_lossy(&show.stderr)
        );
        let record: Value = serde_json::from_slice(&show.stdout)?;
        assert_eq!(record["manifest"]["shift_id"], "shift_cli_1");
        assert_eq!(
            record["manifest"]["routine_bindings"][0]["routine_id"],
            "morning_review"
        );
        assert_eq!(record["transitions"], json!([]));
    }

    let review = forager_command(temp.path())
        .args(["offdesk", "shift", "review", "shift_cli_1", "--json"])
        .output()?;
    assert!(
        review.status.success(),
        "review failed: {}",
        String::from_utf8_lossy(&review.stderr)
    );
    let review: Value = serde_json::from_slice(&review.stdout)?;
    assert_eq!(review["schema"], "offdesk_shift_review_projection.v1");
    assert_eq!(review["selection"], "explicit");
    assert_eq!(review["shift_id"], "shift_cli_1");
    assert_eq!(review["review_status"], "prepared");
    assert_eq!(review["authority"]["review_only"], true);

    let invalid_run = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "review",
            "shift_cli_1",
            "--current-run-id",
            "unbound_run",
            "--json",
        ])
        .output()?;
    assert!(!invalid_run.status.success());
    assert!(String::from_utf8_lossy(&invalid_run.stderr)
        .contains("current morning projection run is not reserved"));

    let human = forager_command(temp.path())
        .args(["offdesk", "shift", "status"])
        .output()?;
    assert!(human.status.success());
    let stdout = String::from_utf8_lossy(&human.stdout);
    assert!(stdout.contains("shift_cli_1"));
    assert!(stdout.contains("prepared"));
    assert!(!app_dir(temp.path()).join(".schema_version").exists());
    Ok(())
}

#[cfg(unix)]
#[test]
fn shift_read_only_commands_reject_non_owner_only_ledger_without_repair() -> Result<()> {
    let temp = tempdir()?;
    let ledger = write_shift_ledger(temp.path(), 0o644)?;

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(!status.status.success());
    assert!(String::from_utf8_lossy(&status.stderr).contains("not owner-only"));
    assert_eq!(fs::metadata(&ledger)?.permissions().mode() & 0o777, 0o644);
    assert!(!app_dir(temp.path()).join(".schema_version").exists());
    Ok(())
}

#[test]
fn shift_read_only_commands_reject_cross_profile_manifest() -> Result<()> {
    let temp = tempdir()?;
    let profile = profile_dir(temp.path());
    fs::create_dir_all(&profile)?;
    let ledger = profile.join("offdesk_shifts.v1.json");
    let mut value = prepared_shift_ledger();
    value["manifests"][0]["profile"] = json!("other-profile");
    fs::write(&ledger, serde_json::to_vec_pretty(&value)?)?;
    #[cfg(unix)]
    fs::set_permissions(&ledger, fs::Permissions::from_mode(0o600))?;

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(!status.status.success());
    assert!(String::from_utf8_lossy(&status.stderr).contains("different profile"));
    assert!(!app_dir(temp.path()).join(".schema_version").exists());
    Ok(())
}

#[test]
fn shift_review_rejects_an_invalid_required_source_without_repair() -> Result<()> {
    let temp = tempdir()?;
    write_shift_ledger(temp.path(), 0o600)?;
    let task_store = profile_dir(temp.path()).join("offdesk_tasks.json");
    fs::write(&task_store, b"{\"unexpected\":true}\n")?;
    let before = fs::read(&task_store)?;

    let review = forager_command(temp.path())
        .args(["offdesk", "shift", "review", "shift_cli_1", "--json"])
        .output()?;

    assert!(!review.status.success());
    assert_eq!(fs::read(&task_store)?, before);
    assert!(!app_dir(temp.path()).join(".schema_version").exists());
    Ok(())
}

#[test]
fn shift_template_instantiation_materializes_fresh_daily_and_weekly_manifests() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let morning_at = stops_at - Duration::minutes(10);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let daily_args = vec![
        "offdesk".to_string(),
        "shift".to_string(),
        "instantiate-template".to_string(),
        "daily".to_string(),
        plan_id.clone(),
        "--template-key".to_string(),
        "Nightly-Review".to_string(),
        "--review-id".to_string(),
        review_id.clone(),
        "--starts-at".to_string(),
        rfc3339(starts_at),
        "--stops-at".to_string(),
        rfc3339(stops_at),
        "--task-id".to_string(),
        "task_shift_1".to_string(),
        "--morning-review-at".to_string(),
        rfc3339(morning_at),
        "--json".to_string(),
    ];

    let created = forager_command(temp.path()).args(&daily_args).output()?;
    assert!(
        created.status.success(),
        "daily template instantiation failed: {}",
        String::from_utf8_lossy(&created.stderr)
    );
    let created: Value = serde_json::from_slice(&created.stdout)?;
    let expected_daily_id = format!(
        "shift_template_nightly-review_daily_{}",
        starts_at.format("%Y%m%d")
    );
    assert_eq!(
        created["schema"],
        "offdesk_shift_template_instantiation_report.v1"
    );
    assert_eq!(created["outcome"], "prepared");
    assert_eq!(created["created"], true);
    assert_eq!(created["shift_id"], expected_daily_id);
    assert_eq!(created["template"]["template_key"], "nightly-review");
    assert_eq!(created["template"]["cadence"], "daily");
    assert_eq!(created["template"]["slot_basis"], "window_starts_at_utc");
    assert_eq!(created["template"]["recurrence_authorized"], false);
    assert_eq!(
        created["template"]["next_occurrence_requires_new_manifest"],
        true
    );
    assert_eq!(created["template"]["arm_approval_required"], true);
    assert_eq!(created["arm_receipt_present"], false);
    assert_eq!(created["lifecycle_state_present"], false);
    assert_eq!(created["authority"]["manifest_only"], true);
    assert_eq!(
        created["authority"]["arm_approval_granted_by_instantiation"],
        false
    );
    assert_eq!(created["authority"]["recurrence_authorized"], false);
    assert_eq!(created["authority"]["next_occurrence_authorized"], false);

    let retried = forager_command(temp.path()).args(&daily_args).output()?;
    assert!(retried.status.success());
    let retried: Value = serde_json::from_slice(&retried.stdout)?;
    assert_eq!(retried["outcome"], "unchanged");
    assert_eq!(retried["created"], false);
    assert_eq!(retried["record"]["manifest"], created["record"]["manifest"]);
    let human = forager_command(temp.path())
        .args(&daily_args[..daily_args.len() - 1])
        .output()?;
    assert!(human.status.success());
    let human = String::from_utf8_lossy(&human.stdout);
    assert!(human.contains("Offdesk Shift Template Instantiation"));
    assert!(human.contains("authority: manifest only; separate arm approval required"));
    assert!(human.contains("next occurrence: requires a new manifest"));

    let mut changed_args = daily_args.clone();
    let stops_index = changed_args
        .iter()
        .position(|value| value == "--stops-at")
        .expect("stops-at argument")
        + 1;
    changed_args[stops_index] = rfc3339(stops_at + Duration::minutes(1));
    let changed = forager_command(temp.path()).args(&changed_args).output()?;
    assert!(!changed.status.success());
    assert!(String::from_utf8_lossy(&changed.stderr)
        .contains("shift manifest ID already exists with different content"));

    let forbidden_id = forager_command(temp.path())
        .args(&daily_args[..daily_args.len() - 1])
        .args(["--shift-id", "operator-selected", "--json"])
        .output()?;
    assert!(!forbidden_id.status.success());
    assert!(String::from_utf8_lossy(&forbidden_id.stderr)
        .contains("instantiate-template derives the shift ID"));

    let weekly = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "instantiate-template",
            "weekly",
            &plan_id,
            "--template-key",
            "knowledge-review",
            "--review-id",
            &review_id,
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--json",
        ])
        .output()?;
    assert!(
        weekly.status.success(),
        "weekly template instantiation failed: {}",
        String::from_utf8_lossy(&weekly.stderr)
    );
    let weekly: Value = serde_json::from_slice(&weekly.stdout)?;
    assert_eq!(weekly["template"]["cadence"], "weekly");
    assert!(weekly["shift_id"]
        .as_str()
        .expect("weekly shift ID")
        .starts_with("shift_template_knowledge-review_weekly_"));
    assert_ne!(weekly["shift_id"], created["shift_id"]);
    assert_eq!(
        weekly["record"]["manifest"]["task_bindings"]
            .as_array()
            .expect("weekly task bindings")
            .len(),
        1
    );

    let pending_arm = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", &expected_daily_id, "--json"])
        .output()?;
    assert!(pending_arm.status.success());
    let pending_arm: Value = serde_json::from_slice(&pending_arm.stdout)?;
    assert_eq!(pending_arm[0]["action"], "offdesk.shift.arm");
    assert_eq!(
        pending_arm[0]["metadata"]["approval_brief"]["context"]["template_key"],
        "nightly-review"
    );
    assert_eq!(
        pending_arm[0]["metadata"]["approval_brief"]["context"]["template_cadence"],
        "daily"
    );
    assert!(
        pending_arm[0]["metadata"]["approval_brief"]["summary_lines"]
            .as_array()
            .expect("approval summary lines")
            .iter()
            .any(|line| line
                .as_str()
                .is_some_and(|line| line.contains("Template: nightly-review daily slot")))
    );
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", &expected_daily_id, "--json"])
        .output()?;
    assert!(show.status.success());
    let show: Value = serde_json::from_slice(&show.stdout)?;
    assert!(show.get("arm_receipt").is_none());
    assert!(show.get("state").is_none());

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(status.status.success());
    let status: Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(status["record_count"], 2);
    assert_eq!(status["active_shift_id"], Value::Null);
    Ok(())
}

#[test]
fn shift_enterprise_profile_is_explicit_and_budget_caps_fail_closed() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let morning_at = stops_at - Duration::minutes(10);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;

    let prepared = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_enterprise_multi_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--enterprise-policy",
            "multi-operator-local",
            "--json",
        ])
        .output()?;
    assert!(
        prepared.status.success(),
        "multi-operator shift prepare failed: {}",
        String::from_utf8_lossy(&prepared.stderr)
    );
    let prepared: Value = serde_json::from_slice(&prepared.stdout)?;
    let policy = &prepared["record"]["manifest"]["enterprise_policy"];
    assert_eq!(policy["profile_key"], "multi_operator_local");
    assert_eq!(policy["operator_mode"], "multi_operator");
    assert_eq!(policy["notification_route"], "telegram_owner_private");

    let pending = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_enterprise_multi_1",
            "--json",
        ])
        .output()?;
    assert!(pending.status.success());
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    assert_eq!(
        pending[0]["metadata"]["approval_brief"]["context"]["operator_mode"],
        "multi_operator"
    );

    let airgapped = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_enterprise_airgap_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--enterprise-policy",
            "airgapped-local",
            "--json",
        ])
        .output()?;
    assert!(
        airgapped.status.success(),
        "airgapped shift prepare failed: {}",
        String::from_utf8_lossy(&airgapped.stderr)
    );
    let airgapped: Value = serde_json::from_slice(&airgapped.stdout)?;
    assert_eq!(
        airgapped["record"]["manifest"]["enterprise_policy"]["notification_route"],
        "local_evidence_only"
    );
    assert_eq!(
        airgapped["record"]["manifest"]["routine_resource_budget"]["max_external_calls"],
        0
    );

    let rejected = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_enterprise_over_budget_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--max-attempts-per-task",
            "4",
            "--json",
        ])
        .output()?;
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr)
        .contains("execution budget exceeds the enterprise policy ceiling"));
    Ok(())
}

#[test]
fn shift_host_policy_is_live_probed_before_manifest_binding() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let probe = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "host-policy-probe",
            "linux-bubblewrap-external-ip-deny",
            "--json",
        ])
        .output()?;
    assert!(probe.status.success());
    let probe: Value = serde_json::from_slice(&probe.stdout)?;

    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_host_policy_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--host-policy",
            "linux-bubblewrap-external-ip-deny",
            "--json",
        ])
        .output()?;
    match probe["status"].as_str() {
        Some("verified") => {
            assert!(
                prepare.status.success(),
                "verified host policy was not bindable: {}",
                String::from_utf8_lossy(&prepare.stderr)
            );
            let prepared: Value = serde_json::from_slice(&prepare.stdout)?;
            let attestation = &prepared["record"]["manifest"]["host_policy_attestation"];
            assert_eq!(
                attestation["schema"],
                "offdesk_shift_host_policy_attestation.v1"
            );
            assert_eq!(attestation["provider"], "linux_bubblewrap_external_ip_deny");
            assert_eq!(attestation["external_ip_network_allowed"], false);
            assert_eq!(attestation["unix_domain_sockets_covered"], false);
            assert!(attestation.get("filesystem_policy").is_none());
            assert_eq!(attestation["forager_enforces_attested_launcher"], true);
            let retried = forager_command(temp.path())
                .args([
                    "offdesk",
                    "shift",
                    "prepare",
                    &plan_id,
                    "--review-id",
                    &review_id,
                    "--shift-id",
                    "shift_host_policy_1",
                    "--starts-at",
                    &rfc3339(starts_at),
                    "--stops-at",
                    &rfc3339(stops_at),
                    "--task-id",
                    "task_shift_1",
                    "--host-policy",
                    "linux-bubblewrap-external-ip-deny",
                    "--json",
                ])
                .output()?;
            assert!(retried.status.success());
            let retried: Value = serde_json::from_slice(&retried.stdout)?;
            assert_eq!(retried["outcome"], "unchanged");
            assert_eq!(
                retried["record"]["manifest"]["host_policy_attestation"],
                *attestation
            );
        }
        Some("unavailable") => {
            assert!(!prepare.status.success());
            assert!(String::from_utf8_lossy(&prepare.stderr)
                .contains("host policy linux_bubblewrap_external_ip_deny is unavailable"));
            let status = forager_command(temp.path())
                .args(["offdesk", "shift", "status", "--json"])
                .output()?;
            assert!(status.status.success());
            let status: Value = serde_json::from_slice(&status.stdout)?;
            assert_eq!(status["record_count"], 0);
        }
        other => panic!("unexpected host policy status: {other:?}"),
    }
    Ok(())
}

#[test]
fn shift_host_write_policy_is_explicit_or_unavailable_without_manifest_state() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let provider = "linux-bubblewrap-external-ip-and-outside-workdir-write-deny";
    let probe = forager_command(temp.path())
        .args(["offdesk", "shift", "host-policy-probe", provider, "--json"])
        .output()?;
    assert!(probe.status.success());
    let probe: Value = serde_json::from_slice(&probe.stdout)?;

    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_host_write_policy_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--host-policy",
            provider,
            "--json",
        ])
        .output()?;
    match probe["status"].as_str() {
        Some("verified") => {
            assert!(
                prepare.status.success(),
                "verified host write policy was not bindable: {}",
                String::from_utf8_lossy(&prepare.stderr)
            );
            let prepared: Value = serde_json::from_slice(&prepare.stdout)?;
            let attestation = &prepared["record"]["manifest"]["host_policy_attestation"];
            assert_eq!(
                attestation["provider"],
                "linux_bubblewrap_external_ip_and_outside_workdir_write_deny"
            );
            assert_eq!(
                attestation["filesystem_policy"]["boundary"],
                "host_read_only_except_working_directory_and_private_tmp"
            );
            assert_eq!(
                attestation["filesystem_policy"]["host_filesystem_read_allowed"],
                true
            );
            assert_eq!(
                attestation["filesystem_policy"]
                    ["host_filesystem_write_outside_working_directory_allowed"],
                false
            );
            assert_eq!(attestation["filesystem_policy"]["private_tmp"], true);
            assert_eq!(
                attestation["filesystem_policy"]
                    ["hardlink_aliases_outside_working_directory_covered"],
                false
            );
            assert_eq!(
                attestation["filesystem_policy"]["nested_mounts_inside_working_directory_covered"],
                false
            );
            assert_eq!(
                attestation["filesystem_policy"]["device_and_kernel_interfaces_covered"],
                false
            );
            assert_eq!(attestation["unix_domain_sockets_covered"], false);
        }
        Some("unavailable") => {
            assert!(!prepare.status.success());
            assert!(String::from_utf8_lossy(&prepare.stderr).contains(
                "host policy linux_bubblewrap_external_ip_and_outside_workdir_write_deny is unavailable"
            ));
            let status = forager_command(temp.path())
                .args(["offdesk", "shift", "status", "--json"])
                .output()?;
            assert!(status.status.success());
            let status: Value = serde_json::from_slice(&status.stdout)?;
            assert_eq!(status["record_count"], 0);
        }
        other => panic!("unexpected host write policy status: {other:?}"),
    }
    Ok(())
}

#[test]
fn policyless_legacy_manifest_remains_readable_but_cannot_be_armed() -> Result<()> {
    let temp = tempdir()?;
    write_shift_ledger(temp.path(), 0o600)?;
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_cli_1", "--json"])
        .output()?;
    assert!(show.status.success());
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert!(record["manifest"].get("enterprise_policy").is_none());

    let arm = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", "shift_cli_1", "--json"])
        .output()?;
    assert!(!arm.status.success());
    assert!(String::from_utf8_lossy(&arm.stderr)
        .contains("has no enterprise policy and cannot gain new authority"));
    Ok(())
}

#[test]
fn shift_manifest_only_boundary_is_inert_and_exact_prepare_retry_is_idempotent() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare_args = vec![
        "offdesk".to_string(),
        "shift".to_string(),
        "prepare".to_string(),
        plan_id,
        "--review-id".to_string(),
        review_id,
        "--shift-id".to_string(),
        "shift_manifest_crash_1".to_string(),
        "--starts-at".to_string(),
        rfc3339(starts_at),
        "--stops-at".to_string(),
        rfc3339(stops_at),
        "--task-id".to_string(),
        "task_shift_1".to_string(),
        "--json".to_string(),
    ];

    let prepared = forager_command(temp.path()).args(&prepare_args).output()?;
    assert!(
        prepared.status.success(),
        "shift prepare failed: {}",
        String::from_utf8_lossy(&prepared.stderr)
    );
    let prepared: Value = serde_json::from_slice(&prepared.stdout)?;
    assert_eq!(prepared["outcome"], "prepared");
    assert_eq!(prepared["changed"], true);

    let retried = forager_command(temp.path()).args(&prepare_args).output()?;
    assert!(
        retried.status.success(),
        "shift prepare retry failed: {}",
        String::from_utf8_lossy(&retried.stderr)
    );
    let retried: Value = serde_json::from_slice(&retried.stdout)?;
    assert_eq!(retried["outcome"], "unchanged");
    assert_eq!(retried["changed"], false);
    assert_eq!(
        retried["record"]["manifest"],
        prepared["record"]["manifest"]
    );
    assert_eq!(retried["record"]["arm_receipt"], Value::Null);
    assert_eq!(retried["record"]["state"], Value::Null);
    assert_eq!(retried["record"]["transitions"], json!([]));

    let tick = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_manifest_crash_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(!tick.status.success());
    assert!(!profile_dir(temp.path())
        .join("background_runs.json")
        .exists());
    Ok(())
}

#[test]
fn shift_arm_commit_reconciles_when_approval_consumption_was_lost() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_arm_crash_1",
        starts_at,
        stops_at,
    )?;
    let approval_path = profile_dir(temp.path()).join("pending_action_approvals.json");
    let approved_before_arm = fs::read(&approval_path)?;

    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_arm_crash_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());

    fs::write(&approval_path, approved_before_arm)?;
    let recovered = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_arm_crash_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        recovered.status.success(),
        "shift arm reconciliation failed: {}",
        String::from_utf8_lossy(&recovered.stderr)
    );
    let recovered: Value = serde_json::from_slice(&recovered.stdout)?;
    assert_eq!(recovered["outcome"], "reconciled");
    assert_eq!(recovered["record"]["state"]["sequence"], 1);
    assert_eq!(
        recovered["record"]["transitions"]
            .as_array()
            .expect("transitions")
            .len(),
        1
    );

    let approvals: Value = serde_json::from_slice(&fs::read(&approval_path)?)?;
    let approval = approvals
        .as_array()
        .expect("approvals")
        .iter()
        .find(|approval| approval["approval_id"] == approval_id)
        .expect("shift arm approval");
    assert_eq!(approval["status"], "superseded");
    Ok(())
}

#[test]
fn shift_prepare_arm_reconcile_and_disarm_follow_exact_approval_flow() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let morning_at = stops_at - Duration::minutes(10);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;

    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_e2e_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let prepared: Value = serde_json::from_slice(&prepare.stdout)?;
    assert_eq!(prepared["schema"], "offdesk_shift_prepare_report.v1");
    assert_eq!(prepared["outcome"], "prepared");
    assert_eq!(prepared["record"]["manifest"]["shift_id"], "shift_e2e_1");
    assert_eq!(
        prepared["record"]["manifest"]["enterprise_policy"]["profile_key"],
        "single_owner_local"
    );
    assert_eq!(
        prepared["record"]["manifest"]["enterprise_policy"]["forager_controls_child_task_network"],
        false
    );
    assert!(prepared["record"]["manifest"]
        .get("template_instantiation")
        .is_none());
    assert_eq!(
        prepared["record"]["manifest"]["task_bindings"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        prepared["record"]["manifest"]["routine_bindings"]
            .as_array()
            .unwrap()
            .len(),
        2
    );

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", "shift_e2e_1", "--json"])
        .output()?;
    assert!(
        pending_output.status.success(),
        "shift arm request failed: {}",
        String::from_utf8_lossy(&pending_output.stderr)
    );
    let pending: Value = serde_json::from_slice(&pending_output.stdout)?;
    let approval_id = pending[0]["approval_id"]
        .as_str()
        .expect("approval ID")
        .to_string();
    assert_eq!(pending[0]["action"], "offdesk.shift.arm");
    assert_eq!(pending[0]["metadata"]["kind"], "offdesk_shift_arm");
    assert_eq!(
        pending[0]["metadata"]["approval_brief"]["context"]["enterprise_policy_key"],
        "single_owner_local"
    );
    assert!(pending[0]["metadata"]["approval_brief"]["evidence"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "forager_controls_child_task_network=false"));

    let approval = forager_command(temp.path())
        .args(["offdesk", "ok", &approval_id, "--by", "operator", "--json"])
        .output()?;
    assert!(approval.status.success());

    let armed_output = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_e2e_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        armed_output.status.success(),
        "shift arm failed: {}",
        String::from_utf8_lossy(&armed_output.stderr)
    );
    let armed: Value = serde_json::from_slice(&armed_output.stdout)?;
    assert_eq!(armed["schema"], "offdesk_shift_arm_report.v1");
    assert_eq!(armed["outcome"], "armed");
    assert_eq!(armed["record"]["state"]["status"], "armed");
    assert_eq!(armed["record"]["state"]["sequence"], 1);
    assert_eq!(armed["record"]["arm_receipt"]["approval_id"], approval_id);

    let retry_output = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_e2e_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(retry_output.status.success());
    let retry: Value = serde_json::from_slice(&retry_output.stdout)?;
    assert_eq!(retry["outcome"], "reconciled");
    assert_eq!(retry["record"]["transitions"].as_array().unwrap().len(), 1);

    let disarm_output = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "disarm",
            "shift_e2e_1",
            "--reason",
            "operator returned on desk",
            "--json",
        ])
        .output()?;
    assert!(disarm_output.status.success());
    let disarmed: Value = serde_json::from_slice(&disarm_output.stdout)?;
    assert_eq!(disarmed["outcome"], "disarmed");
    assert_eq!(disarmed["record"]["state"]["status"], "disarmed");
    assert_eq!(disarmed["record"]["state"]["sequence"], 2);

    let repeated_disarm = forager_command(temp.path())
        .args(["offdesk", "shift", "disarm", "shift_e2e_1", "--json"])
        .output()?;
    assert!(repeated_disarm.status.success());
    let repeated: Value = serde_json::from_slice(&repeated_disarm.stdout)?;
    assert_eq!(repeated["outcome"], "already_terminal");
    assert_eq!(
        repeated["record"]["transitions"].as_array().unwrap().len(),
        2
    );
    Ok(())
}

#[test]
fn shift_tick_launches_only_after_revalidating_the_active_manifest() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_tick_1",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_tick_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        armed.status.success(),
        "shift arm failed: {}",
        String::from_utf8_lossy(&armed.stderr)
    );

    let queue_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let original_queue = fs::read(&queue_path)?;
    let mut queue: Value = serde_json::from_slice(&original_queue)?;
    let mut occupying = queue[0].clone();
    occupying["task_id"] = json!("other_project_running");
    occupying["project_key"] = json!("other_project");
    occupying["status"] = json!("running");
    queue.as_array_mut().unwrap().push(occupying);
    for missing in [false, true] {
        if missing {
            queue[1]["workdir"] = json!(temp.path().join("missing-root"));
        }
        fs::write(&queue_path, serde_json::to_vec(&queue)?)?;
        for _ in 0..2 {
            let held = forager_command(temp.path())
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "shift_tick_1",
                    "--task-id",
                    "task_shift_1",
                    "--json",
                ])
                .output()?;
            assert!(
                held.status.success(),
                "{}",
                String::from_utf8_lossy(&held.stderr)
            );
            let report: Value = serde_json::from_slice(&held.stdout)?;
            assert_eq!(report["launched"], 0);
            assert_eq!(report["bounded_shift"]["held"], 1);
            let reason = if missing {
                "shift_workdir_occupancy_unresolved"
            } else {
                "shift_workdir_occupied"
            };
            assert!(report["bounded_shift"]["hold_reasons"]
                .as_array()
                .unwrap()
                .contains(&json!(reason)));
            let stored: Value = serde_json::from_slice(&fs::read(&queue_path)?)?;
            assert_eq!(stored[0]["attempt_count"], 0);
        }
    }
    let held_record = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_tick_1", "--json"])
        .output()?;
    let held_record: Value = serde_json::from_slice(&held_record.stdout)?;
    assert_eq!(held_record["task_launch_reservations"], json!([]));
    // Fixture occupancy is removed explicitly; the production gate never clears it.
    fs::write(&queue_path, original_queue)?;

    let first_tick = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(first_tick.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&first_tick.stdout)?["pending_approval"],
        1
    );
    approve_pending_dispatch(temp.path())?;
    let tick = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(
        tick.status.success(),
        "shift tick failed: {}",
        String::from_utf8_lossy(&tick.stderr)
    );
    let report: Value = serde_json::from_slice(&tick.stdout)?;
    assert_eq!(report["launched"], 1);
    assert_eq!(report["bounded_shift"]["shift_id"], "shift_tick_1");
    assert_eq!(report["bounded_shift"]["status"], "running");
    assert_eq!(report["bounded_shift"]["held"], 0);

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_tick_1", "--json"])
        .output()?;
    assert!(show.status.success());
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(record["state"]["status"], "running");
    assert_eq!(record["state"]["sequence"], 2);
    assert_eq!(record["transitions"].as_array().unwrap().len(), 2);
    assert_eq!(
        record["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(record["task_launch_reservations"][0]["attempt_number"], 1);
    assert_eq!(
        record["task_launch_ticket_receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(
        record["task_launch_reservations"][0]["background_ticket_id"],
        record["task_launch_reservations"][0]["launch_id"]
    );
    assert_eq!(
        record["task_launch_ticket_receipts"][0]["background_ticket_id"],
        record["task_launch_reservations"][0]["launch_id"]
    );

    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["status"], "launched");
    assert_eq!(tasks[0]["attempt_count"], 1);

    tasks[0]["status"] = json!("queued");
    tasks[0]["attempt_count"] = json!(0);
    tasks[0]
        .as_object_mut()
        .unwrap()
        .remove("background_ticket_id");
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
    let replay = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(replay.status.success());
    let replay: Value = serde_json::from_slice(&replay.stdout)?;
    assert_eq!(replay["launched"], 0);
    assert_eq!(
        replay["bounded_shift"]["hold_reasons"],
        json!(["shift_state_attention_required"])
    );
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_tick_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["task_launch_ticket_receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(record["task_outcome_receipts"].as_array().unwrap().len(), 1);
    assert_eq!(
        record["task_outcome_receipts"][0]["outcome"],
        "recovery_required"
    );
    let tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["status"], "resume_pending");
    assert_eq!(
        tasks[0]["background_ticket_id"],
        record["task_launch_reservations"][0]["launch_id"]
    );
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn ondesk_bound_shift_rechecks_source_before_real_background_launch() -> Result<()> {
    qualify_bound_shift_cycle(false, false)
}

#[test]
#[ignore = "requires an isolated disposable container with tmux and no host state"]
fn ondesk_cycle_disposable_receiver_reads_agreed_plan_without_relaunch() -> Result<()> {
    if isolated_process::run("ondesk_cycle_disposable_receiver_reads_agreed_plan_without_relaunch")?
    {
        return Ok(());
    }
    assert!(Path::new("/.dockerenv").is_file());
    assert!(!Path::new("/var/run/docker.sock").exists());
    qualify_bound_shift_cycle(true, false)
}

#[test]
#[ignore = "requires an isolated disposable container with tmux and no host state"]
fn ondesk_return_disposable_cli_preserves_one_attempt_and_observes_read_claim() -> Result<()> {
    if isolated_process::run(
        "ondesk_return_disposable_cli_preserves_one_attempt_and_observes_read_claim",
    )? {
        return Ok(());
    }
    assert!(Path::new("/.dockerenv").is_file());
    assert!(!Path::new("/var/run/docker.sock").exists());
    qualify_bound_shift_cycle(true, true)
}

#[test]
#[ignore = "requires an owned disposable VM with native Codex and sandbox preflight"]
fn ondesk_return_native_tui_reads_context_without_task_authority() -> Result<()> {
    if isolated_process::run("ondesk_return_native_tui_reads_context_without_task_authority")? {
        return Ok(());
    }
    assert_eq!(
        std::env::var("FORAGER_RETURN_NATIVE_TUI_TRIAL").as_deref(),
        Ok("1")
    );
    assert_eq!(
        fs::read_to_string("/sys/class/dmi/id/sys_vendor")?.trim(),
        "QEMU"
    );
    assert!(!Path::new("/.dockerenv").exists());
    assert!(!Path::new("/var/run/docker.sock").exists());
    assert!(Path::new("/tmp/forager-native-sandbox-pass.json").is_file());
    qualify_bound_shift_cycle(true, true)
}

#[test]
#[ignore = "requires an owned disposable container with Codex, exact mounts and read-scope preflight"]
fn ondesk_return_container_tui_reads_context_without_task_authority() -> Result<()> {
    if isolated_process::run("ondesk_return_container_tui_reads_context_without_task_authority")? {
        return Ok(());
    }
    assert_eq!(
        std::env::var("FORAGER_RETURN_CONTAINER_TUI_TRIAL").as_deref(),
        Ok("1")
    );
    assert!(Path::new("/.dockerenv").is_file());
    assert!(!Path::new("/var/run/docker.sock").exists());
    assert!(Path::new("/tmp/forager-container-scope-pass.json").is_file());
    qualify_bound_shift_cycle(true, true)
}

fn native_return_trial_enabled() -> bool {
    [
        "FORAGER_RETURN_NATIVE_TUI_TRIAL",
        "FORAGER_RETURN_CONTAINER_TUI_TRIAL",
        "FORAGER_RETURN_LANDLOCK_NATIVE_TRIAL",
    ]
    .iter()
    .any(|name| std::env::var(name).as_deref() == Ok("1"))
}

fn qualify_bound_shift_cycle(deliver_return: bool, production_return: bool) -> Result<()> {
    qualify_bound_shift_cycle_with_read_policy(deliver_return, production_return, false)
}

#[test]
fn bound_shift_read_policy_constrains_real_children_and_preserves_outcome_gates() -> Result<()> {
    let report = forager::offdesk::read_policy::report();
    if report["status"] != "verified" {
        eprintln!("read policy unavailable: {report}");
        return Ok(());
    }
    qualify_bound_shift_cycle_with_read_policy(false, false, true)
}

fn qualify_bound_shift_cycle_with_read_policy(
    deliver_return: bool,
    production_return: bool,
    read_policy: bool,
) -> Result<()> {
    qualify_bound_shift_cycle_options(deliver_return, production_return, read_policy, false, false)
}

#[test]
fn bound_shift_tmux_read_policy_and_restricted_morning_return() -> Result<()> {
    if isolated_process::run("bound_shift_tmux_read_policy_and_restricted_morning_return")? {
        return Ok(());
    }
    if forager::offdesk::read_policy::report()["status"] != "verified"
        || !forager::tmux::is_tmux_available()
    {
        return Ok(());
    }
    qualify_bound_shift_cycle_options(true, true, true, true, true)
}

#[test]
fn bound_shift_tmux_read_policy_preserves_failure_and_uncertainty_gates() -> Result<()> {
    if isolated_process::run(
        "bound_shift_tmux_read_policy_preserves_failure_and_uncertainty_gates",
    )? {
        return Ok(());
    }
    if forager::offdesk::read_policy::report()["status"] != "verified"
        || !forager::tmux::is_tmux_available()
    {
        return Ok(());
    }
    qualify_bound_shift_cycle_options(false, false, true, true, false)
}

fn qualify_bound_shift_cycle_options(
    deliver_return: bool,
    production_return: bool,
    read_policy: bool,
    restricted_tmux: bool,
    restricted_return: bool,
) -> Result<()> {
    qualify_bound_shift_cycle_review_options(
        deliver_return,
        production_return,
        read_policy,
        restricted_tmux,
        restricted_return,
        None,
    )
}

#[test]
fn bound_shift_reconciles_narrative_drift_and_returns_all_review_dispositions() -> Result<()> {
    if isolated_process::run(
        "bound_shift_reconciles_narrative_drift_and_returns_all_review_dispositions",
    )? {
        return Ok(());
    }
    if !forager::tmux::is_tmux_available()
        || forager::offdesk::read_policy::report()["status"] != "verified"
    {
        eprintln!("composed review qualification needs tmux and verified read policy");
        return Ok(());
    }
    for disposition in ["accepted", "partially_accepted", "rework", "deferred"] {
        println!("C4_RECONCILED_DISPOSITION={disposition}");
        qualify_bound_shift_cycle_review_options(true, true, true, true, true, Some(disposition))?;
    }
    Ok(())
}

fn qualify_bound_shift_cycle_review_options(
    deliver_return: bool,
    production_return: bool,
    read_policy: bool,
    restricted_tmux: bool,
    restricted_return: bool,
    reconciled_disposition: Option<&str>,
) -> Result<()> {
    use forager::offdesk::workflow::ondesk_plan::inspect;
    use sha2::{Digest, Sha256};
    for scenario in [
        "completed",
        "reported_failed",
        "reported_pending_review",
        "supervisor_killed",
        "failed",
        "unknown_launch",
        "budget_exhausted",
        "source_drift",
    ] {
        if deliver_return && scenario != "completed" {
            continue;
        }
        let drift = scenario == "source_drift";
        let exhausted = scenario == "budget_exhausted";
        let evidence_directory = std::env::var_os("FORAGER_R11_EVIDENCE_DIR")
            .filter(|_| restricted_tmux)
            .map(PathBuf::from);
        let temp = if let Some(directory) = &evidence_directory {
            tempfile::Builder::new()
                .prefix(&format!("{scenario}-"))
                .tempdir_in(directory)?
        } else {
            tempdir()?
        };
        let scenario_started = std::time::Instant::now();
        let stage = |name: &str| {
            eprintln!(
                "BOUND_RECEIVER scenario={scenario} stage={name} elapsed_ms={} home={}",
                scenario_started.elapsed().as_millis(),
                temp.path().display()
            );
        };
        stage("prepare");
        let root = temp.path().join("project");
        fs::create_dir(&root)?;
        let source_path = root.join("NEXT_ACTIONS.md");
        let selected_tasks = json!({
        "schema":"ondesk_task_plan.v1", "project_key":"project", "tasks":[{
            "task_id":"task_shift_1", "goal":"bounded deterministic execution", "completion_criteria":["one terminal receipt"],
            "depends_on":[], "review_state":"unreviewed", "evidence_refs":[]
        }]});
        let plain_source =
            "# Existing TODO\n- [ ] bounded deterministic execution\n- [ ] unrelated task\n";
        fs::write(&source_path, plain_source)?;
        let selection_path = root.join("selected-tasks.json");
        fs::write(&selection_path, serde_json::to_vec(&selected_tasks)?)?;
        let baseline = format!("{:x}", Sha256::digest(plain_source.as_bytes()));
        let proposal = forager_command(temp.path())
            .args([
                "ondesk",
                "plan-adopt-proposal",
                source_path.to_str().unwrap(),
                "--tasks",
                selection_path.to_str().unwrap(),
                "--project-key",
                "project",
                "--expected-sha256",
                &baseline,
                "--request-id",
                "fixture-adoption",
                "--decision-ref",
                "operator:fixture-mapping",
            ])
            .output()?;
        assert!(
            proposal.status.success(),
            "{}",
            String::from_utf8_lossy(&proposal.stderr)
        );
        assert_eq!(fs::read_to_string(&source_path)?, plain_source);
        let proposal: Value = serde_json::from_slice(&proposal.stdout)?;
        let source = proposal["candidate_markdown"].as_str().unwrap().to_owned();
        let adoption_request = root.join("adoption-request.json");
        let adoption_candidate = root.join("adoption-candidate.md");
        fs::write(
            &adoption_request,
            serde_json::to_vec(&proposal["change_request"])?,
        )?;
        fs::write(&adoption_candidate, &source)?;
        let adopted = forager_command(temp.path())
            .args([
                "ondesk",
                "plan-apply",
                adoption_request.to_str().unwrap(),
                "--candidate",
                adoption_candidate.to_str().unwrap(),
                "--confirm-plan-write",
            ])
            .output()?;
        assert!(
            adopted.status.success(),
            "{}",
            String::from_utf8_lossy(&adopted.stderr)
        );
        assert!(source.starts_with(plain_source));
        assert_eq!(fs::read_to_string(&source_path)?, source);
        let reference = format!(
            "ondesk-plan-sha256:{}={}",
            inspect(&source, "project")?.source_sha256,
            source_path.display()
        );
        let start = Utc::now() - Duration::minutes(1);
        let stop = Utc::now() + Duration::minutes(10);
        let (plan, review) = register_review_and_enqueue_with_source(
            temp.path(),
            &root,
            start,
            stop,
            Some(&reference),
        )?;
        // Configure the deterministic receiver's result contract before any
        // shift approval, just as the existing terminal-outcome fixture does.
        let result_path = root.join("shift-result.json");
        let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
        let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
        tasks[0]["command"] = json!(format!(
            "printf '{{\"status\":\"{}\"}}' > '{}'",
            if scenario == "reported_failed" {
                "failed"
            } else if scenario == "reported_pending_review" {
                "pending_user_review"
            } else {
                "completed"
            },
            result_path.display()
        ));
        tasks[0]["result_artifact_path"] = json!(result_path.display().to_string());
        if scenario == "reported_pending_review" {
            tasks[0]["result_artifact_path"] = json!("shift-result.json");
        }
        if scenario == "supervisor_killed" {
            tasks[0]["command"] = json!(format!(
                "printf done > '{}'; kill -KILL \"$PPID\"; exit 0",
                result_path.display()
            ));
        }
        if scenario == "failed" {
            // A result file's presence is a runtime completion signal, not a
            // parsed business verdict. Fail without the declared result file.
            tasks[0]["command"] =
                json!(format!("printf done > '{}'; exit 1", result_path.display()));
            tasks[0]["result_artifact_path"] =
                json!(root.join("missing-result.json").display().to_string());
        }
        let outside = temp.path().join("private-canary");
        if read_policy {
            fs::write(&outside, b"private-outside")?;
            fs::write(root.join("allowed-input"), b"allowed")?;
            #[cfg(unix)]
            std::os::unix::fs::symlink(&outside, root.join("escape"))?;
            fs::write(
                root.join("boundary.py"),
                r#"
import errno, os, pathlib, subprocess, sys
assert pathlib.Path('allowed-input').read_bytes() == b'allowed'
assert pathlib.Path('/bin/sh').read_bytes()
for path in [sys.argv[1], 'escape', '/etc/passwd', '/proc/%s/root%s' % (os.getppid(), sys.argv[1])]:
    try:
        pathlib.Path(path).read_bytes()
    except OSError as error:
        assert error.errno == errno.EACCES
    else:
        raise AssertionError('outside read escaped policy')
try:
    pathlib.Path(sys.argv[1]).write_bytes(b'bypass')
except OSError as error:
    assert error.errno == errno.EACCES
else:
    raise AssertionError('outside write escaped policy')
assert subprocess.run(['/bin/sh', '-c', 'cat "$1"', 'probe', sys.argv[1]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
pathlib.Path('boundary-pass').write_text('passed')
"#,
            )?;
            tasks[0]["command"] = json!(format!(
                "/usr/bin/python3 -I -S boundary.py '{}' || exit 97; {}",
                outside.display(),
                tasks[0]["command"].as_str().unwrap()
            ));
        }
        if restricted_tmux {
            tasks[0]["runner_kind"] = json!("local_tmux");
        }
        fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
        stage("approve_shift");
        let approval = prepare_shift_and_approve_with_read_policy(
            temp.path(),
            &plan,
            &review,
            "bound_run",
            start,
            stop,
            read_policy,
        )?;
        stage("arm_shift");
        let armed = forager_command(temp.path())
            .args([
                "offdesk",
                "shift",
                "arm",
                "bound_run",
                "--approval-id",
                &approval,
                "--json",
            ])
            .output()?;
        assert!(
            armed.status.success(),
            "{}",
            String::from_utf8_lossy(&armed.stderr)
        );
        if drift {
            fs::write(&source_path, format!("{source}\n"))?;
        }
        if exhausted {
            tasks[0]["attempt_count"] = json!(1);
            fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
        }
        stage("prepare_dispatch");
        let mut tick = forager_command(temp.path())
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "bound_run",
                "--task-id",
                "task_shift_1",
                "--json",
            ])
            .output()?;
        if drift {
            assert!(!tick.status.success(), "changed plan unexpectedly launched");
            let show = forager_command(temp.path())
                .args(["offdesk", "shift", "show", "bound_run", "--json"])
                .output()?;
            let record: Value = serde_json::from_slice(&show.stdout)?;
            assert_eq!(
                record["task_launch_reservations"].as_array().unwrap().len(),
                0
            );
            stage("verified_source_drift_refused");
            if evidence_directory.is_some() {
                let _ = temp.keep();
            }
            continue;
        }
        if !exhausted {
            assert_eq!(
                serde_json::from_slice::<Value>(&tick.stdout)?["pending_approval"],
                1
            );
            stage("approve_dispatch");
            approve_pending_dispatch(temp.path())?;
            stage("launch_receiver");
            tick = forager_command(temp.path())
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "bound_run",
                    "--task-id",
                    "task_shift_1",
                    "--json",
                ])
                .output()?;
        }
        assert!(
            tick.status.success(),
            "{}",
            String::from_utf8_lossy(&tick.stderr)
        );
        assert_eq!(
            serde_json::from_slice::<Value>(&tick.stdout)?["launched"],
            if exhausted { 0 } else { 1 }
        );
        if exhausted {
            assert_eq!(
                serde_json::from_slice::<Value>(&tick.stdout)?["bounded_shift"]["hold_reasons"],
                json!(["task_attempt_budget_exhausted"])
            );
            assert!(!result_path.exists());
        }
        if !exhausted {
            let native_trial = native_return_trial_enabled();
            let requires_receipt = native_trial || (read_policy && scenario != "supervisor_killed");
            stage("wait_receiver");
            let receipt_deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
            let mut verified_completion = !requires_receipt;
            while std::time::Instant::now() < receipt_deadline {
                if result_path.exists() {
                    if requires_receipt {
                        let mut probes: Vec<forager::offdesk::background::BackgroundProbe> =
                            serde_json::from_slice(&fs::read(
                                profile_dir(temp.path()).join("background_runs.json"),
                            )?)?;
                        verified_completion = probes.iter_mut().any(|probe| {
                            probe.local_exit_receipt.as_mut().is_some_and(|receipt| {
                                forager::offdesk::local_exit::refresh(&root, receipt).is_ok()
                                    && if native_trial {
                                        receipt.status == Some(0)
                                    } else {
                                        receipt.status.is_some()
                                    }
                            })
                        });
                    }
                    if verified_completion {
                        break;
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
            assert!(
                result_path.exists(),
                "bounded background receiver did not finish: scenario={scenario}, home={}",
                temp.path().display()
            );
            assert!(
                verified_completion,
                "required supervisor exit receipt missing: scenario={scenario}, home={}",
                temp.path().display()
            );
            if read_policy {
                assert_eq!(fs::read(&outside)?, b"private-outside");
                assert_eq!(fs::read_to_string(root.join("boundary-pass"))?, "passed");
                let probes: Vec<forager::offdesk::background::BackgroundProbe> =
                    serde_json::from_slice(&fs::read(
                        profile_dir(temp.path()).join("background_runs.json"),
                    )?)?;
                let evidence = probes[0]
                    .read_policy_launch_evidence
                    .as_ref()
                    .expect("mandatory read policy evidence");
                assert!(evidence.restrictions_applied_before_exec);
                assert!(evidence.landlock_abi >= 5);
                assert_eq!(
                    evidence.policy_sha256,
                    forager::offdesk::read_policy::ReadPolicy::canonical().sha256()?
                );
            }
            if scenario == "unknown_launch" {
                // Inject lost launch acknowledgement only in this temporary
                // profile after the deterministic receiver has finished.
                let ledger_path = profile_dir(temp.path()).join("offdesk_shifts.v1.json");
                let mut ledger: Value = serde_json::from_slice(&fs::read(&ledger_path)?)?;
                ledger["task_launch_ticket_receipts"] = json!([]);
                ledger["task_outcome_receipts"] = json!([]);
                fs::write(&ledger_path, serde_json::to_vec_pretty(&ledger)?)?;
                let background_path = profile_dir(temp.path()).join("background_runs.json");
                if background_path.exists() {
                    fs::remove_file(background_path)?;
                }
                tasks[0]["status"] = json!("queued");
                tasks[0]["attempt_count"] = json!(0);
                tasks[0]
                    .as_object_mut()
                    .unwrap()
                    .remove("background_ticket_id");
                fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
            }
            if scenario == "completed" {
                // Fixture-supplied evidence, not a claim that a model ran tests.
                use sha2::{Digest, Sha256};
                let check = b"deterministic fixture check output";
                fs::write(root.join("check.txt"), check)?;
                let current_tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
                let result = json!({"status":"completed", "task_id":current_tasks[0]["task_id"], "request_id":current_tasks[0]["request_id"], "ticket_id":current_tasks[0]["background_ticket_id"], "evidence":[{"kind":"check","description":"fixture check only","path":"check.txt","sha256":format!("{:x}",Sha256::digest(check))}]});
                fs::write(&result_path, serde_json::to_vec(&result)?)?;
            }
            stage("reconcile_receiver");
            let reconciled = forager_command(temp.path())
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "bound_run",
                    "--task-id",
                    "task_shift_1",
                    "--json",
                ])
                .output()?;
            assert!(
                reconciled.status.success(),
                "{}",
                String::from_utf8_lossy(&reconciled.stderr)
            );
            assert_eq!(
                serde_json::from_slice::<Value>(&reconciled.stdout)?["launched"],
                0
            );
        }
        stage("review_outcome");
        let reviewed = forager_command(temp.path())
            .args(["offdesk", "shift", "review", "bound_run", "--json"])
            .output()?;
        assert!(
            reviewed.status.success(),
            "{}",
            String::from_utf8_lossy(&reviewed.stderr)
        );
        assert_eq!(
            serde_json::from_slice::<Value>(&reviewed.stdout)?["schema"],
            "offdesk_shift_review_projection.v1"
        );
        let review_report: Value = serde_json::from_slice(&reviewed.stdout)?;
        if read_policy {
            assert_eq!(
                review_report["read_policy"]["provider"],
                "linux_landlock_workdir_and_runtime_only"
            );
            assert_eq!(review_report["read_policy"]["network_isolated"], false);
        }
        let closeout = &review_report["tasks"][0]["closeout_report"];
        assert_eq!(closeout["schema"], "offdesk_task_closeout_report.v1");
        assert_eq!(
            closeout["execution_status"],
            match scenario {
                "failed" => "recovery_required",
                "supervisor_killed" => "recovery_required",
                "unknown_launch" => "launch_unresolved",
                "budget_exhausted" => "not_attempted",
                _ => "terminal_completed",
            }
        );
        assert_eq!(closeout["source_status"], "launch_spec_matched");
        assert_eq!(
            closeout["historical_scope_status"],
            "preserved_source_hash_verified"
        );
        assert_eq!(closeout["original_goal"], "bounded deterministic execution");
        assert_eq!(
            closeout["original_completion_criteria"],
            json!(["one terminal receipt"])
        );
        assert_eq!(
            closeout["historical_source_sha256"],
            inspect(&source, "project")?.source_sha256
        );
        if scenario == "failed" {
            assert_eq!(closeout["terminal_phase"], "failed");
            assert_eq!(closeout["last_observed_supervisor_shell_status"], 1);
        }
        if scenario == "supervisor_killed" {
            assert_eq!(closeout["terminal_phase"], "stale_lost_callback");
            assert!(closeout["last_observed_supervisor_shell_status"].is_null());
            let repeat = forager_command(temp.path())
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "bound_run",
                    "--task-id",
                    "task_shift_1",
                    "--json",
                ])
                .output()?;
            assert!(
                repeat.status.success(),
                "{}",
                String::from_utf8_lossy(&repeat.stderr)
            );
            assert_eq!(
                serde_json::from_slice::<Value>(&repeat.stdout)?["launched"],
                0
            );
        }
        if scenario == "completed" {
            assert_eq!(closeout["terminal_phase"], "completed");
            assert_eq!(closeout["last_observed_supervisor_shell_status"], 0);
            assert_eq!(
                closeout["result_observation"]["reported_status"],
                "completed"
            );
            assert_eq!(
                closeout["result_observation"]["identity"],
                "self_reported_exact"
            );
            assert_eq!(
                closeout["result_observation"]["evidence"][0]["verification"],
                "current_bytes_hash_match_not_claim_verification"
            );
            assert_eq!(
                closeout["actual_changes_status"],
                "not_independently_verified"
            );
        }
        if scenario == "reported_failed" {
            assert_eq!(closeout["execution_status"], "terminal_completed");
            assert_eq!(closeout["result_observation"]["reported_status"], "failed");
            assert_eq!(
                closeout["result_observation"]["process_exit_verified"],
                false
            );
            assert!(closeout["next_actions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|action| action == "resolve_result_content_conflict_before_acceptance"));
        }
        if scenario == "reported_pending_review" {
            assert_eq!(closeout["execution_status"], "terminal_completed");
            assert_eq!(
                closeout["result_observation"]["reported_status"],
                "pending_user_review"
            );
            assert!(!closeout["next_actions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|action| action == "resolve_result_content_conflict_before_acceptance"));
            assert!(!review_report["attention_reasons"]
                .as_array()
                .unwrap()
                .iter()
                .any(|reason| reason == "result_content_requires_review"));
            assert!(closeout["next_actions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|action| action == "record_user_agreement_before_updating_ondesk"));
        }
        assert_eq!(closeout["review_disposition"], "pending_user_review");
        assert_eq!(closeout["plan_update_authorized"], false);
        assert!(!closeout["evidence_references"]
            .as_array()
            .unwrap()
            .is_empty());
        assert_eq!(
            fs::read_to_string(&source_path)?,
            source,
            "execution changed Ondesk acceptance"
        );
        // Morning review must not need the mutable source to still exist.
        fs::rename(&source_path, root.join("retired-plan.md"))?;
        let historical = forager_command(temp.path())
            .args(["offdesk", "shift", "review", "bound_run", "--json"])
            .output()?;
        assert!(
            historical.status.success(),
            "{}",
            String::from_utf8_lossy(&historical.stderr)
        );
        let historical: Value = serde_json::from_slice(&historical.stdout)?;
        assert_eq!(
            historical["tasks"][0]["closeout_report"]["original_goal"],
            closeout["original_goal"]
        );
        assert_eq!(
            historical["tasks"][0]["closeout_report"]["plan_update_authorized"],
            false
        );
        let show = forager_command(temp.path())
            .args(["offdesk", "shift", "show", "bound_run", "--json"])
            .output()?;
        let record: Value = serde_json::from_slice(&show.stdout)?;
        assert_eq!(
            record["task_launch_reservations"].as_array().unwrap().len(),
            if exhausted { 0 } else { 1 }
        );
        assert_eq!(
            record["task_outcome_receipts"].as_array().unwrap().len(),
            if exhausted { 0 } else { 1 }
        );
        if scenario == "completed" {
            use sha2::{Digest, Sha256};
            fs::rename(root.join("retired-plan.md"), &source_path)?;
            let report_path = root.join("night-review.json");
            fs::write(&report_path, &reviewed.stdout)?;
            let reference_path = root.join("review-reference.json");
            let return_reference = json!({"path":report_path,
                "sha256":format!("{:x}",Sha256::digest(&reviewed.stdout)),
                "shift_id":review_report["shift_id"],"manifest_sha256":review_report["manifest_sha256"]});
            fs::write(&reference_path, serde_json::to_vec(&return_reference)?)?;
            let checklist_path = root.join("TODO.md");
            let checklist = "# Existing checklist\n\n- [ ] Review the packet against the source.\n";
            fs::write(&checklist_path, checklist)?;
            let unstructured =
                morning_return_command(temp.path(), &root, &checklist_path, &reference_path)
                    .arg("--review-only-unstructured")
                    .output()?;
            assert!(
                unstructured.status.success(),
                "{}",
                String::from_utf8_lossy(&unstructured.stderr)
            );
            let returned: Value = serde_json::from_slice(&unstructured.stdout)?;
            assert_eq!(
                returned["morning_return"]["schema"],
                "ondesk_unstructured_morning_review.v1"
            );
            assert_eq!(returned["morning_return"]["authority"], "review_only");
            assert_eq!(
                returned["morning_return"]["tasks"][0]["canonical_plan_task_mapping"],
                "unverified"
            );
            assert_eq!(fs::read_to_string(&checklist_path)?, checklist);
            let rejected =
                morning_return_command(temp.path(), &root, &checklist_path, &reference_path)
                    .output()?;
            assert!(!rejected.status.success());
            stage("morning_return");
            qualify_morning_return(temp.path(), &root, &source_path, &source, &reference_path)?;
            let native_review = native_return_trial_enabled();
            let disposition = reconciled_disposition.unwrap_or(if native_review {
                "partially_accepted"
            } else {
                "accepted"
            });
            let review_source = if reconciled_disposition.is_some() {
                fs::write(root.join("execution-baseline.md"), &source)?;
                let current =
                    format!("{source}\nOperator note changed after execution; preserve it.\n");
                fs::write(&source_path, &current)?;
                current
            } else {
                source.clone()
            };
            let mut decisions = json!({
                "request_id":"morning-review-1", "project_key":"project",
                "baseline_sha256":inspect(&review_source, "project")?.source_sha256,
                "decision_ref":"operator:fixture-morning-agreement", "decided_by":"fixture operator",
                "shift_review":{
                    "path":report_path, "sha256":format!("{:x}", Sha256::digest(&reviewed.stdout)),
                    "shift_id":review_report["shift_id"], "manifest_sha256":review_report["manifest_sha256"]
                },
                "tasks":[{"task_id":"task_shift_1", "disposition":disposition,
                    "evidence_refs":[format!("shift-review-sha256:{:x}", Sha256::digest(&reviewed.stdout))],
                    "remaining_work":if native_review || matches!(disposition, "partially_accepted" | "rework") {
                        vec!["native-fixture-followup"]
                    } else { vec![] }, "unanswered_questions":[]}]
            });
            let decisions_path = root.join("decisions.json");
            fs::write(&decisions_path, serde_json::to_vec(&decisions)?)?;
            let proposal_args = [
                "ondesk",
                "plan-review-proposal",
                source_path.to_str().unwrap(),
                "--decisions",
                decisions_path.to_str().unwrap(),
            ];
            let mut proposal_command = forager_command(temp.path());
            proposal_command.args(proposal_args);
            if reconciled_disposition.is_some() {
                let refused = proposal_command.output()?;
                assert!(!refused.status.success());
                assert!(
                    String::from_utf8_lossy(&refused.stderr).contains("source baseline mismatch")
                );
                proposal_command.args([
                    "--reconcile-baseline",
                    root.join("execution-baseline.md").to_str().unwrap(),
                    "--reconciliation-reason",
                    "Only operator narrative changed; all structured tasks unchanged",
                ]);
            }
            let proposed = proposal_command.output()?;
            assert!(
                proposed.status.success(),
                "{}",
                String::from_utf8_lossy(&proposed.stderr)
            );
            assert_eq!(fs::read_to_string(&source_path)?, review_source);
            let proposal: Value = serde_json::from_slice(&proposed.stdout)?;
            if reconciled_disposition.is_some() {
                decisions = proposal["decisions"].clone();
                assert_eq!(
                    decisions["reconciliation"]["execution_baseline_markdown"],
                    source
                );
                assert!(proposal["candidate_markdown"]
                    .as_str()
                    .unwrap()
                    .ends_with("Operator note changed after execution; preserve it.\n"));
            }
            let request_path = root.join("request.json");
            let candidate_path = root.join("candidate.md");
            fs::write(
                &request_path,
                serde_json::to_vec(&proposal["change_request"])?,
            )?;
            fs::write(
                &candidate_path,
                proposal["candidate_markdown"].as_str().unwrap(),
            )?;
            let apply_args = [
                "ondesk",
                "plan-apply",
                request_path.to_str().unwrap(),
                "--candidate",
                candidate_path.to_str().unwrap(),
                "--confirm-plan-write",
            ];
            let unconfirmed = forager_command(temp.path())
                .args(&apply_args[..apply_args.len() - 1])
                .output()?;
            assert!(!unconfirmed.status.success());
            assert_eq!(fs::read_to_string(&source_path)?, review_source);
            for outcome in ["applied", "already_applied"] {
                let applied = forager_command(temp.path()).args(apply_args).output()?;
                assert!(
                    applied.status.success(),
                    "{}",
                    String::from_utf8_lossy(&applied.stderr)
                );
                assert_eq!(
                    serde_json::from_slice::<Value>(&applied.stdout)?["outcome"],
                    outcome
                );
            }
            let returned =
                morning_return_command(temp.path(), &root, &source_path, &reference_path)
                    .output()?;
            assert!(
                returned.status.success(),
                "{}",
                String::from_utf8_lossy(&returned.stderr)
            );
            let returned: Value = serde_json::from_slice(&returned.stdout)?;
            assert_eq!(
                returned["morning_return"]["tasks"][0]["baseline_status"],
                "tasks_changed"
            );
            assert_eq!(
                returned["morning_return"]["tasks"][0]["current_task"]["review_state"],
                disposition
            );
            assert_eq!(
                returned["morning_return"]["review_history"][0]["committed"],
                true
            );
            assert_eq!(
                returned["morning_return"]["review_history"][0]["candidate_is_current"],
                true
            );
            let mut returned_decisions = decisions.clone();
            if let Some(reconciliation) = returned_decisions.get_mut("reconciliation") {
                reconciliation
                    .as_object_mut()
                    .unwrap()
                    .remove("execution_baseline_markdown");
                reconciliation["exact_record_source"] = json!("owning_plan_journal");
            }
            assert_eq!(
                returned["morning_return"]["review_history"][0]["decisions"],
                returned_decisions
            );
            assert_eq!(returned["morning_return"]["pending_decisions"], json!([]));
            let stale_proposal = forager_command(temp.path())
                .args([
                    "ondesk",
                    "plan-review-proposal",
                    source_path.to_str().unwrap(),
                    "--decisions",
                    decisions_path.to_str().unwrap(),
                ])
                .output()?;
            assert!(!stale_proposal.status.success());
            assert_eq!(
                fs::read_to_string(&source_path)?,
                proposal["candidate_markdown"]
            );
            if deliver_return {
                if production_return {
                    qualify_return_cli_with_read_policy(
                        temp.path(),
                        &root,
                        &source_path,
                        &reference_path,
                        restricted_return,
                    )?;
                } else {
                    qualify_agreed_return_delivery(
                        temp.path(),
                        &root,
                        &source_path,
                        &reference_path,
                    )?;
                }
            }
            let refreshed = forager_command(temp.path())
                .args([
                    "ondesk",
                    "plan-inspect",
                    source_path.to_str().unwrap(),
                    "--project-key",
                    "project",
                ])
                .output()?;
            assert!(refreshed.status.success());
            let refreshed: Value = serde_json::from_slice(&refreshed.stdout)?;
            assert_eq!(refreshed["plan"]["tasks"][0]["review_state"], disposition);
            let journal: Value = serde_json::from_slice(&fs::read(
                root.join(".NEXT_ACTIONS.md.forager-plan-journal.json"),
            )?)?;
            let agreement = journal["entries"]
                .as_array()
                .unwrap()
                .iter()
                .find(|entry| entry["request"]["request_id"] == "morning-review-1")
                .unwrap();
            assert_eq!(agreement["request"]["review_record"], decisions);
            assert_eq!(
                journal["entries"][0]["request"]["schema"],
                "ondesk_plan_adoption_request.v1"
            );
            let unchanged_shift = forager_command(temp.path())
                .args(["offdesk", "shift", "show", "bound_run", "--json"])
                .output()?;
            assert!(unchanged_shift.status.success());
            assert_eq!(
                serde_json::from_slice::<Value>(&unchanged_shift.stdout)?,
                record
            );
        }
        stage("verified");
        if evidence_directory.is_some() {
            println!("R11_FIXTURE_EVIDENCE={}", temp.path().display());
            let _ = temp.keep();
        }
    }
    Ok(())
}

struct ReturnReceiverCleanup(Vec<forager::session::Instance>);

fn qualify_return_cli_with_read_policy(
    home: &Path,
    root: &Path,
    plan: &Path,
    reference: &Path,
    restricted: bool,
) -> Result<()> {
    if restricted && std::env::var("FORAGER_RETURN_LANDLOCK_NATIVE_TRIAL").as_deref() == Ok("1") {
        let status = Command::new("/usr/bin/python3")
            .args(["-I", "-S"])
            .arg(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/support/ondesk_landlock_native.py"),
            )
            .arg(env!("CARGO_BIN_EXE_forager"))
            .args([home, root, plan, reference])
            .status()?;
        anyhow::ensure!(
            status.success(),
            "integrated native read-policy return qualification failed"
        );
        return Ok(());
    }
    if native_return_trial_enabled() && !restricted {
        let driver =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/ondesk_native_tui.py");
        let status = Command::new("python3")
            .arg(driver)
            .arg(env!("CARGO_BIN_EXE_forager"))
            .args([home, root, plan, reference])
            .status()?;
        anyhow::ensure!(status.success(), "native return qualification failed");
        return Ok(());
    }
    use forager::session::Instance;
    let driver =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/ondesk_cycle_receiver.py");
    let quote = |p: &Path| format!("'{}'", p.to_string_lossy().replace('\'', "'\\''"));
    let plan_before = fs::read(plan)?;
    let shift_path = profile_dir(home).join("offdesk_shifts.v1.json");
    let shift_before = fs::read(&shift_path)?;
    let mut cleanup = ReturnReceiverCleanup(Vec::new());
    for mode in ["existing_session", "fresh_session"] {
        let fixture_dir = if restricted { root } else { home };
        let receipt = fixture_dir.join(format!("{mode}-production-ack.jsonl"));
        let scene = fixture_dir.join(format!("{mode}-scene.txt"));
        let ready = "READY_FOR_RETURN\n› \n";
        fs::write(&scene, ready)?;
        let mut instance = Instance::new("production return fixture", root.to_str().unwrap());
        instance.tool = "codex".into();
        let mut command = format!(
            "python3 -u {} receive-production {} {} {}",
            quote(&driver),
            quote(&receipt),
            quote(Path::new(env!("CARGO_BIN_EXE_forager"))),
            quote(&scene)
        );
        if restricted {
            let reader = root.join("restricted-return-reader.py");
            fs::copy(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/support/ondesk_restricted_receiver.py"),
                &reader,
            )?;
            let outside = home.join("return-private-canary");
            fs::write(&outside, b"outside-private")?;
            command = format!(
                "/usr/bin/python3 -I -S -u {} {} {} {}",
                quote(&reader),
                quote(&receipt),
                quote(&scene),
                quote(&outside)
            );
        }
        instance.command = command.clone();
        let tmux = instance.tmux_session()?;
        cleanup.0.push(instance.clone());
        fs::write(
            profile_dir(home).join("sessions.json"),
            serde_json::to_vec(&cleanup.0)?,
        )?;
        let policy_receipt = if restricted {
            let out = forager_command(home)
                .args([
                    "ondesk",
                    "return",
                    "launch-restricted",
                    "--session",
                    &instance.id,
                ])
                .output()?;
            assert!(
                out.status.success(),
                "{}",
                String::from_utf8_lossy(&out.stderr)
            );
            let value: Value = serde_json::from_slice(&out.stdout)?;
            assert_eq!(value["input_sent"], false);
            let again = forager_command(home)
                .args([
                    "ondesk",
                    "return",
                    "launch-restricted",
                    "--session",
                    &instance.id,
                ])
                .output()?;
            assert!(!again.status.success(), "existing session was relaunched");
            Some(PathBuf::from(
                value["read_policy_receipt"].as_str().unwrap(),
            ))
        } else {
            tmux.create(instance.project_path.as_str(), Some(&command))?;
            None
        };
        let wait_scene = |text: &str| -> Result<()> {
            for _ in 0..100 {
                if tmux.capture_pane(80)?.contains(text) {
                    return Ok(());
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            anyhow::bail!("return fixture did not show expected scene")
        };
        wait_scene("READY_FOR_RETURN")?;
        fs::write(
            profile_dir(home).join("sessions.json"),
            serde_json::to_vec(&cleanup.0)?,
        )?;
        let package = root.join(format!("{mode}-production-package.md"));
        let mut export = morning_return_command(home, root, plan, reference);
        export.arg("--output").arg(&package);
        if mode == "existing_session" {
            export.args(["--resume-session", &instance.id]);
        }
        let out = export.output()?;
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let ticket_path = home.join(format!("{mode}-ticket.json"));
        let preview = || -> Result<std::process::Output> {
            let mut c = forager_command(home);
            c.args([
                "ondesk",
                "return",
                "preview",
                "--package",
                package.to_str().unwrap(),
                "--session",
                &instance.id,
            ]);
            if let Some(path) = &policy_receipt {
                c.arg("--read-policy-receipt").arg(path);
            }
            Ok(c.output()?)
        };
        fs::write(&scene, "• Working (1s · esc to interrupt)\n› \n")?;
        wait_scene("Working")?;
        assert!(!preview()?.status.success(), "busy pane accepted");
        fs::write(&scene, "Allow this command?\n› 1. Yes, allow\n  2. No\n")?;
        wait_scene("Allow this command?")?;
        assert!(!preview()?.status.success(), "permission pane accepted");
        fs::write(&scene, ready)?;
        wait_scene("READY_FOR_RETURN")?;
        let out = preview()?;
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let value: Value = serde_json::from_slice(&out.stdout)?;
        let ticket = &value["ticket"];
        let request_id = ticket["request_id"].as_str().unwrap();
        let hash = value["preview_sha256"].as_str().unwrap();
        fs::write(&ticket_path, serde_json::to_vec(ticket)?)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;
            let linked = home.join(format!("{mode}-linked-package"));
            symlink(&package, &linked)?;
            let invalid = forager_command(home)
                .args([
                    "ondesk",
                    "return",
                    "preview",
                    "--package",
                    linked.to_str().unwrap(),
                    "--session",
                    &instance.id,
                ])
                .output()?;
            assert!(!invalid.status.success());
            fs::remove_file(&linked)?;
            fs::hard_link(&package, &linked)?;
            assert!(!preview()?.status.success());
            fs::remove_file(&linked)?;
        }
        let send = |hash: &str, confirm: bool| -> Result<std::process::Output> {
            let mut c = forager_command(home);
            c.args([
                "ondesk",
                "return",
                "send",
                ticket_path.to_str().unwrap(),
                "--expected-preview-sha256",
                hash,
            ]);
            if confirm {
                c.arg("--confirm-input");
            }
            Ok(c.output()?)
        };
        if let Some(path) = &policy_receipt {
            let before = fs::read(path)?;
            fs::write(path, [before.as_slice(), b"\n"].concat())?;
            assert!(
                !send(hash, true)?.status.success(),
                "changed restriction receipt accepted"
            );
            assert!(!receipt.exists());
            fs::write(path, &before)?;
            let mut changed = ticket.clone();
            changed["read_policy_binding"]["receipt"]["child_identity"] = json!("wrong-process");
            fs::write(&ticket_path, serde_json::to_vec(&changed)?)?;
            let changed: forager::offdesk::workflow::ondesk_delivery::ReturnTicket =
                serde_json::from_value(changed)?;
            let changed_hash =
                forager::offdesk::workflow::ondesk_delivery::ticket_sha256(&changed)?;
            assert!(!send(&changed_hash, true)?.status.success());
            assert!(!receipt.exists());
            fs::write(&ticket_path, serde_json::to_vec(ticket)?)?;
        }
        assert!(!send(hash, false)?.status.success());
        assert!(!send(&"0".repeat(64), true)?.status.success());
        assert!(!receipt.exists());
        let mut expired = ticket.clone();
        expired["created_at"] = json!((Utc::now() - Duration::minutes(10)).to_rfc3339());
        expired["expires_at"] = json!((Utc::now() - Duration::minutes(5)).to_rfc3339());
        fs::write(&ticket_path, serde_json::to_vec(&expired)?)?;
        let expired: forager::offdesk::workflow::ondesk_delivery::ReturnTicket =
            serde_json::from_value(expired)?;
        let expired_hash = forager::offdesk::workflow::ondesk_delivery::ticket_sha256(&expired)?;
        assert!(!send(&expired_hash, true)?.status.success());
        fs::write(&ticket_path, serde_json::to_vec(ticket)?)?;
        let package_before = fs::read(&package)?;
        fs::write(&package, b"changed package")?;
        assert!(!send(hash, true)?.status.success());
        fs::write(&package, &package_before)?;
        fs::write(
            plan,
            [plan_before.as_slice(), b"\nconcurrent edit\n"].concat(),
        )?;
        assert!(!send(hash, true)?.status.success());
        fs::write(plan, &plan_before)?;
        let concurrent = root.join("return-concurrent-edit.txt");
        fs::write(&concurrent, "unreviewed worktree change")?;
        assert!(!send(hash, true)?.status.success());
        fs::remove_file(&concurrent)?;
        let sessions_path = profile_dir(home).join("sessions.json");
        let sessions_before = fs::read(&sessions_path)?;
        let mut changed_sessions: Value = serde_json::from_slice(&sessions_before)?;
        changed_sessions.as_array_mut().unwrap().last_mut().unwrap()["command"] =
            json!("changed-harness-launch");
        fs::write(&sessions_path, serde_json::to_vec(&changed_sessions)?)?;
        assert!(!send(hash, true)?.status.success());
        fs::write(&sessions_path, &sessions_before)?;
        fs::write(&scene, "Different question\n› 1. Option\n")?;
        wait_scene("Different question")?;
        assert!(!send(hash, true)?.status.success());
        fs::write(&scene, ready)?;
        wait_scene("READY_FOR_RETURN")?;
        // A durable intent without a result models interruption before or
        // after transport. The CLI must never infer that another send is safe.
        let ledger_path = profile_dir(home).join("ondesk_return_deliveries.v1.json");
        let mut ledger: Value = if ledger_path.exists() {
            serde_json::from_slice(&fs::read(&ledger_path)?)?
        } else {
            json!({"schema":"ondesk_return_delivery_ledger.v1","entries":[]})
        };
        let mut uncertain = ticket.clone();
        uncertain["request_id"] = json!(uuid::Uuid::new_v4().simple().to_string());
        uncertain["nonce"] = json!(uuid::Uuid::new_v4().simple().to_string());
        ledger["entries"]
            .as_array_mut()
            .unwrap()
            .push(json!({"ticket":uncertain,"state":"send_intent",
            "attempted_at":Utc::now(),"observed_at":null,"acknowledgement_pane_sha256":null}));
        fs::write(&ledger_path, serde_json::to_vec(&ledger)?)?;
        #[cfg(unix)]
        fs::set_permissions(&ledger_path, fs::Permissions::from_mode(0o600))?;
        fs::write(&ticket_path, serde_json::to_vec(&uncertain)?)?;
        let uncertain_ticket = serde_json::from_value(uncertain.clone())?;
        let uncertain_hash =
            forager::offdesk::workflow::ondesk_delivery::ticket_sha256(&uncertain_ticket)?;
        let held = send(&uncertain_hash, true)?;
        assert!(held.status.success());
        let held: Value = serde_json::from_slice(&held.stdout)?;
        assert_eq!(held["state"], "send_intent");
        assert_eq!(held["new_send_attempt"], false);
        assert!(!receipt.exists());
        fs::write(&ticket_path, serde_json::to_vec(ticket)?)?;
        let ledger_before = fs::read(&ledger_path)?;
        fs::write(&ledger_path, b"")?;
        assert!(!send(hash, true)?.status.success());
        assert!(!receipt.exists());
        fs::write(&ledger_path, &ledger_before)?;
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .open(profile_dir(home).join(".ondesk_return_deliveries.v1.lock"))?;
        fs2::FileExt::lock_exclusive(&lock)?;
        assert!(!send(hash, true)?.status.success());
        assert!(!receipt.exists());
        fs2::FileExt::unlock(&lock)?;
        let delivered = send(hash, true)?;
        assert!(
            delivered.status.success(),
            "{}",
            String::from_utf8_lossy(&delivered.stderr)
        );
        let delivered: Value = serde_json::from_slice(&delivered.stdout)?;
        assert_eq!(delivered["state"], "input_queued");
        assert_eq!(delivered["read_claim_observed"], false);
        let replay = send(hash, true)?;
        assert!(replay.status.success());
        assert_eq!(
            serde_json::from_slice::<Value>(&replay.stdout)?["new_send_attempt"],
            false
        );
        let mut observed = Value::Null;
        // Only the explicitly enabled, disposable provider trial gets a longer
        // observation window. It still performs one transport attempt.
        let observations = if std::env::var("FORAGER_RETURN_REAL_TRIAL").as_deref() == Ok("1") {
            3000
        } else {
            100
        };
        let observation_started = std::time::Instant::now();
        for _ in 0..observations {
            if observation_started.elapsed() >= std::time::Duration::from_secs(140) {
                break;
            }
            let out = forager_command(home)
                .args(["ondesk", "return", "observe", request_id])
                .output()?;
            if out.status.success() {
                observed = serde_json::from_slice(&out.stdout)?;
                if observed["read_claim_observed"] == true {
                    break;
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        if std::env::var("FORAGER_RETURN_REAL_TRIAL").as_deref() == Ok("1") && receipt.exists() {
            println!(
                "REAL_RETURN_TRIAL_EVIDENCE={}",
                fs::read_to_string(&receipt)?.trim()
            );
        }
        assert_eq!(observed["state"], "agent_read_claim_observed", "{observed}");
        assert_eq!(observed["send_attempts"], 1);
        let records = fs::read_to_string(&receipt)?;
        assert_eq!(records.lines().count(), 1);
        let copied = home.join(format!("{mode}-copied-ticket.json"));
        fs::copy(&ticket_path, &copied)?;
        let replay = forager_command(home)
            .args([
                "ondesk",
                "return",
                "send",
                copied.to_str().unwrap(),
                "--expected-preview-sha256",
                hash,
                "--confirm-input",
            ])
            .output()?;
        assert!(replay.status.success());
        assert_eq!(
            serde_json::from_slice::<Value>(&replay.stdout)?["new_send_attempt"],
            false
        );
        assert_eq!(fs::read_to_string(&receipt)?, records);
        assert_eq!(fs::read(plan)?, plan_before);
        assert_eq!(fs::read(&shift_path)?, shift_before);
        if let Some(path) = &policy_receipt {
            assert_eq!(
                fs::read(home.join("return-private-canary"))?,
                b"outside-private"
            );
            tmux.kill()?;
            tmux.create(
                instance.project_path.as_str(),
                Some("printf 'READY_FOR_RETURN\n› \n'; sleep 30"),
            )?;
            wait_scene("READY_FOR_RETURN")?;
            assert!(
                !preview()?.status.success(),
                "unrestricted replacement accepted old receipt"
            );
            assert!(path.exists());
        }
        println!(
            "PRODUCTION_RETURN_EVIDENCE={}",
            json!({"resume_mode":mode,"receipt":observed,
            "receiver":serde_json::from_str::<Value>(records.trim())?})
        );
    }
    Ok(())
}

impl Drop for ReturnReceiverCleanup {
    fn drop(&mut self) {
        for instance in &self.0 {
            if let Ok(session) = instance.tmux_session() {
                let _ = session.kill();
            }
        }
    }
}

fn qualify_agreed_return_delivery(
    home: &Path,
    root: &Path,
    plan: &Path,
    reference: &Path,
) -> Result<()> {
    use forager::session::{Instance, RemoteSessionIdentityV1};
    use sha2::{Digest, Sha256};
    let driver =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/ondesk_cycle_receiver.py");
    let quote = |p: &Path| format!("'{}'", p.to_string_lossy().replace('\'', "'\\''"));
    let ledger = profile_dir(home).join("offdesk_shifts.v1.json");
    let ledger_before = fs::read(&ledger)?;
    let plan_before = fs::read(plan)?;
    let mut cleanup = ReturnReceiverCleanup(Vec::new());
    for mode in ["existing_session", "fresh_session"] {
        let receipt = root.join(format!("{mode}-ack.jsonl"));
        let mut instance = Instance::new("isolated morning receiver", root.to_str().unwrap());
        instance.tool = "generic_tmux".into();
        let command = format!(
            "python3 -u {} receive {} {}",
            quote(&driver),
            quote(&receipt),
            quote(Path::new(env!("CARGO_BIN_EXE_forager")))
        );
        instance.command = command.clone();
        let session = instance.tmux_session()?;
        session.create(instance.project_path.as_str(), Some(&command))?;
        cleanup.0.push(instance.clone());
        let wait_ready = || -> Result<()> {
            for _ in 0..100 {
                if session.capture_pane(30)?.contains("READY_FOR_RETURN") {
                    return Ok(());
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            anyhow::bail!("isolated morning receiver did not become ready")
        };
        wait_ready()?;
        let sessions = profile_dir(home).join("sessions.json");
        fs::write(&sessions, serde_json::to_vec(&cleanup.0)?)?;
        let package = root.join(format!("{mode}-package.md"));
        let mut export = morning_return_command(home, root, plan, reference);
        export.arg("--output").arg(&package);
        if mode == "existing_session" {
            export.args(["--resume-session", &instance.id]);
        }
        let exported = export.output()?;
        assert!(
            exported.status.success(),
            "{}",
            String::from_utf8_lossy(&exported.stderr)
        );
        let context: Value = serde_json::from_slice(&exported.stdout)?;
        assert_eq!(context["morning_return"]["resume_mode"], mode);
        assert_eq!(
            context["morning_return"]["session_input_status"],
            "not_assessed_no_delivery"
        );
        assert!(fs::read_to_string(&package)?.chars().count() > 2000);
        let identity = serde_json::to_value(RemoteSessionIdentityV1::observe(
            &instance,
            Some("project"),
        )?)?;
        let status = root.join(format!("{mode}-status.json"));
        let status_bytes = serde_json::to_vec(&json!({"sessions":[{
            "id":instance.id,"title":instance.title,"tool":instance.tool,
            "project":"project","status":"idle","remote_session_identity":identity
        }]}))?;
        fs::write(&status, &status_bytes)?;
        let envelope = root.join(format!("{mode}-envelope.json"));
        fs::write(
            &envelope,
            serde_json::to_vec(&json!({
                "schema":"fixture_morning_return_reference.v1", "session_id":instance.id,
                "request_id":format!("fixture-{mode}"), "package_path":package,
                "package_sha256":format!("{:x}",Sha256::digest(fs::read(&package)?)),
                "plan_path":plan,"plan_sha256":context["morning_return"]["current_plan_sha256"],
                "task_id":"task_shift_1"
            }))?,
        )?;
        let dispatched = forager_command(home);
        let mut probe = Command::new("python3");
        probe.envs(dispatched.get_envs().filter_map(|(k, v)| v.map(|v| (k, v))));
        let result = probe
            .arg(&driver)
            .arg("qualify")
            .arg(&status)
            .arg(&envelope)
            .arg(&receipt)
            .output()?;
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let evidence: Value = serde_json::from_slice(&result.stdout)?;
        assert_eq!(evidence["acknowledged_read"], true);
        assert_eq!(evidence["stale_prompt_rejected"], true);
        assert_eq!(evidence["stopped_target_rejected"], true);
        assert_eq!(evidence["wrong_identity_rejected"], true);
        assert_eq!(evidence["receiver_rejected_changed_package"], true);
        assert_eq!(evidence["receiver_rejected_changed_plan"], true);
        assert_eq!(evidence["ack_count"], 1);
        println!("CYCLE_RETURN_EVIDENCE={evidence}");
        assert_eq!(fs::read(&status)?, status_bytes);
        assert_eq!(fs::read(plan)?, plan_before);
        assert_eq!(fs::read(&ledger)?, ledger_before);
    }
    Ok(())
}

#[test]
fn shift_tick_closes_a_reservation_only_crash_without_relaunching() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_ticket_crash_1",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_ticket_crash_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());
    let launched = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_ticket_crash_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(launched.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&launched.stdout)?["pending_approval"],
        1
    );
    approve_pending_dispatch(temp.path())?;
    let launched = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_ticket_crash_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(launched.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&launched.stdout)?["launched"],
        1
    );

    let ledger_path = profile_dir(temp.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&ledger_path)?)?;
    assert_eq!(
        ledger["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    ledger["task_launch_ticket_receipts"] = json!([]);
    ledger["task_outcome_receipts"] = json!([]);
    fs::write(&ledger_path, serde_json::to_vec_pretty(&ledger)?)?;
    let background_path = profile_dir(temp.path()).join("background_runs.json");
    if background_path.exists() {
        fs::remove_file(&background_path)?;
    }
    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    tasks[0]["status"] = json!("queued");
    tasks[0]["attempt_count"] = json!(0);
    tasks[0]
        .as_object_mut()
        .unwrap()
        .remove("background_ticket_id");
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;

    let recovered = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_ticket_crash_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(
        recovered.status.success(),
        "reservation recovery failed: {}",
        String::from_utf8_lossy(&recovered.stderr)
    );
    let recovered: Value = serde_json::from_slice(&recovered.stdout)?;
    assert_eq!(recovered["launched"], 0);
    assert_eq!(recovered["bounded_shift"]["status"], "attention_required");

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_ticket_crash_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(record["task_launch_ticket_receipts"], json!([]));
    assert_eq!(record["task_outcome_receipts"].as_array().unwrap().len(), 1);
    assert_eq!(
        record["task_outcome_receipts"][0]["outcome"],
        "launch_unresolved"
    );

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_ticket_crash_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_ticket_crash_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(record["task_outcome_receipts"].as_array().unwrap().len(), 1);
    Ok(())
}

#[test]
fn shift_poll_records_failed_outcome_without_tick_or_relaunch() -> Result<()> {
    qualify_polled_failure(false, false)
}

#[test]
fn shift_poll_reconciles_expired_window_without_rearming() -> Result<()> {
    qualify_polled_failure(true, false)
}

#[test]
fn shift_poll_detects_exact_ticket_identity_drift() -> Result<()> {
    qualify_polled_failure(false, true)
}

fn qualify_polled_failure(expired: bool, identity_drift: bool) -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now()
        + if expired {
            Duration::seconds(120)
        } else {
            Duration::hours(1)
        };
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    tasks[0]["command"] = json!("sleep 1; exit 7");
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_poll_failed",
        starts_at,
        stops_at,
    )?;
    let arm = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_poll_failed",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        arm.status.success(),
        "{}",
        String::from_utf8_lossy(&arm.stderr)
    );
    let first = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_poll_failed",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(first.status.success());
    approve_pending_dispatch(temp.path())?;
    let launched = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_poll_failed",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(
        launched.status.success(),
        "{}",
        String::from_utf8_lossy(&launched.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&launched.stdout)?["launched"],
        1
    );
    let background_path = profile_dir(temp.path()).join("background_runs.json");
    let probes: Value = serde_json::from_slice(&fs::read(&background_path)?)?;
    let ticket = probes[0]["ticket_id"].as_str().unwrap();
    let receipt = project_root.join(
        probes[0]["local_exit_receipt"]["relative_path"]
            .as_str()
            .unwrap(),
    );
    let receipt_deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    while std::time::Instant::now() < receipt_deadline {
        if receipt.exists() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert!(receipt.exists());
    if expired {
        while Utc::now() <= stops_at {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
    }
    if identity_drift {
        let mut changed = probes.clone();
        changed[0]["project_key"] = json!("wrong-project");
        fs::write(&background_path, serde_json::to_vec_pretty(&changed)?)?;
    }
    let unrelated = profile_dir(temp.path()).join("unrelated-observation.txt");
    fs::write(&unrelated, "preserve")?;
    for _ in 0..2 {
        let poll = forager_command(temp.path())
            .args(["offdesk", "poll", ticket, "--json"])
            .output()?;
        if identity_drift {
            assert!(!poll.status.success());
            assert!(
                String::from_utf8_lossy(&poll.stderr).contains("background ticket identity drift")
            );
            let ledger: Value = serde_json::from_slice(&fs::read(
                profile_dir(temp.path()).join("offdesk_shifts.v1.json"),
            )?)?;
            assert_eq!(ledger["task_outcome_receipts"], json!([]));
            assert_eq!(
                ledger["task_launch_reservations"].as_array().unwrap().len(),
                1
            );
            return Ok(());
        }
        assert!(
            poll.status.success(),
            "{}",
            String::from_utf8_lossy(&poll.stderr)
        );
    }
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(temp.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert_eq!(ledger["task_outcome_receipts"].as_array().unwrap().len(), 1);
    assert_eq!(
        ledger["task_outcome_receipts"][0]["outcome"],
        if identity_drift {
            "identity_drift"
        } else {
            "recovery_required"
        }
    );
    assert_eq!(
        ledger["task_outcome_receipts"][0]["terminal_phase"],
        "failed"
    );
    assert_eq!(ledger["states"][0]["status"], "attention_required");
    assert_eq!(
        ledger["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    let tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    assert_eq!(tasks[0]["status"], "resume_pending");
    assert_eq!(tasks[0]["attempt_count"], 1);
    assert_eq!(fs::read_to_string(&unrelated)?, "preserve");
    let before = fs::read(profile_dir(temp.path()).join("offdesk_shifts.v1.json"))?;
    let review = forager_command(temp.path())
        .args(["offdesk", "shift", "review", "shift_poll_failed", "--json"])
        .output()?;
    assert!(review.status.success());
    let review: Value = serde_json::from_slice(&review.stdout)?;
    assert_eq!(review["lifecycle_status"], "attention_required");
    assert_eq!(review["summary"]["task_attempts_terminal"], 1);
    assert_eq!(review["summary"]["task_attempts_requiring_recovery"], 1);
    assert_eq!(review["authority"]["plan_revision_authorized"], false);
    assert_eq!(
        fs::read(profile_dir(temp.path()).join("offdesk_shifts.v1.json"))?,
        before
    );
    Ok(())
}

#[test]
fn shift_tick_records_one_terminal_outcome_for_the_exact_ticket() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let result_path = project_root.join("shift-result.json");
    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    tasks[0]["command"] = json!(format!(
        "printf '{{\"status\":\"completed\"}}' > '{}'",
        result_path.display()
    ));
    tasks[0]["result_artifact_path"] = json!(result_path.display().to_string());
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;

    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_terminal_outcome_1",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_terminal_outcome_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());
    let launched = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_terminal_outcome_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(launched.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&launched.stdout)?["pending_approval"],
        1
    );
    approve_pending_dispatch(temp.path())?;
    let launched = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_terminal_outcome_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(launched.status.success());
    for _ in 0..50 {
        if result_path.exists() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert!(result_path.exists());

    let completed = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_terminal_outcome_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(
        completed.status.success(),
        "terminal reconciliation failed: {}",
        String::from_utf8_lossy(&completed.stderr)
    );
    let completed: Value = serde_json::from_slice(&completed.stdout)?;
    assert_eq!(completed["completed"], 1);

    let show = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "show",
            "shift_terminal_outcome_1",
            "--json",
        ])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["task_launch_ticket_receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(record["task_outcome_receipts"].as_array().unwrap().len(), 1);
    assert_eq!(record["task_outcome_receipts"][0]["outcome"], "completed");
    assert_eq!(
        record["task_outcome_receipts"][0]["terminal_phase"],
        "completed"
    );
    assert_eq!(
        record["task_outcome_receipts"][0]["launch_ticket_receipt_sha256"]
            .as_str()
            .unwrap()
            .len(),
        64
    );

    let review = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "review",
            "shift_terminal_outcome_1",
            "--json",
        ])
        .output()?;
    assert!(
        review.status.success(),
        "shift review failed: {}",
        String::from_utf8_lossy(&review.stderr)
    );
    let review: Value = serde_json::from_slice(&review.stdout)?;
    assert_eq!(review["schema"], "offdesk_shift_review_projection.v1");
    assert_eq!(review["shift_id"], "shift_terminal_outcome_1");
    assert_eq!(review["summary"]["task_attempts_reserved"], 1);
    assert_eq!(review["summary"]["task_attempts_ticketed"], 1);
    assert_eq!(review["summary"]["task_attempts_terminal"], 1);
    assert_eq!(review["summary"]["task_attempts_completed"], 1);
    assert_eq!(
        review["tasks"][0]["attempts"][0]["live_probe_identity"],
        "exact"
    );
    assert_eq!(review["authority"]["review_only"], true);
    assert_eq!(review["authority"]["accepted_truth_authorized"], false);
    assert_eq!(review["authority"]["plan_revision_authorized"], false);
    assert_eq!(review["authority"]["wiki_promotion_authorized"], false);
    assert_eq!(
        review["evidence_drilldown"]["policy"],
        "bounded_operator_safe_review_evidence"
    );
    assert!(review["evidence_drilldown"]["items"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| {
            item["kind"] == "background_result_artifact"
                && item["reported_present"] == true
                && item["verification"] == "background_probe_reported_not_live_verified"
        }));
    assert_eq!(review["evidence_drilldown"]["max_items"], 64);
    assert_eq!(review["evidence_drilldown"]["max_text_chars"], 32768);

    let morning_surface = forager_command(temp.path())
        .args([
            "ondesk",
            "review-surface",
            "--shift-id",
            "shift_terminal_outcome_1",
            "--json",
        ])
        .output()?;
    assert!(
        morning_surface.status.success(),
        "morning surface failed: {}",
        String::from_utf8_lossy(&morning_surface.stderr)
    );
    let morning_surface: Value = serde_json::from_slice(&morning_surface.stdout)?;
    assert_eq!(
        morning_surface["offdesk_shift"]["manifest_sha256"],
        review["manifest_sha256"]
    );
    assert_eq!(
        morning_surface["offdesk_shift"]["summary"],
        review["summary"]
    );

    let workstation = forager_command(temp.path())
        .args(["ondesk", "workstation-surface", "--json"])
        .output()?;
    assert!(
        workstation.status.success(),
        "workstation surface failed: {}",
        String::from_utf8_lossy(&workstation.stderr)
    );
    let workstation: Value = serde_json::from_slice(&workstation.stdout)?;
    assert_eq!(
        workstation["offdesk_shift_review"]["manifest_sha256"],
        review["manifest_sha256"]
    );
    assert_eq!(
        workstation["offdesk_shift_review"]["summary"],
        review["summary"]
    );

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_terminal_outcome_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let show = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "show",
            "shift_terminal_outcome_1",
            "--json",
        ])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(record["task_outcome_receipts"].as_array().unwrap().len(), 1);
    Ok(())
}

#[test]
fn shift_tick_holds_before_window_and_after_disarm() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(10);
    let stops_at = starts_at + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_hold_1",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_hold_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());

    let before_window = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_hold_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(before_window.status.success());
    let report: Value = serde_json::from_slice(&before_window.stdout)?;
    assert_eq!(report["launched"], 0);
    assert_eq!(report["bounded_shift"]["status"], "armed");
    assert_eq!(report["bounded_shift"]["held"], 0);
    assert_eq!(
        report["bounded_shift"]["hold_reasons"],
        json!(["shift_window_not_started"])
    );

    let disarm = forager_command(temp.path())
        .args(["offdesk", "shift", "disarm", "shift_hold_1", "--json"])
        .output()?;
    assert!(disarm.status.success());
    let after_disarm = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_hold_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(after_disarm.status.success());
    let report: Value = serde_json::from_slice(&after_disarm.stdout)?;
    assert_eq!(report["launched"], 0);
    assert_eq!(report["bounded_shift"]["status"], "disarmed");
    assert_eq!(
        report["bounded_shift"]["hold_reasons"],
        json!(["shift_state_disarmed"])
    );
    Ok(())
}

#[test]
fn shift_tick_rejects_live_drift_and_holds_unbound_or_exhausted_tasks() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_tick_drift_1",
        starts_at,
        stops_at,
    )?;
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_tick_drift_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());

    let plan_dir = profile_dir(temp.path())
        .join("offdesk_plans")
        .join(&plan_id);
    let review_path = fs::read_dir(&plan_dir)?
        .filter_map(std::result::Result::ok)
        .map(|entry| entry.path())
        .find(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("plan_review_"))
        })
        .expect("plan review path");
    let review_bytes = fs::read(&review_path)?;
    let mut changed_review = review_bytes.clone();
    changed_review.push(b'\n');
    fs::write(&review_path, changed_review)?;
    let review_drift = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_drift_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(!review_drift.status.success());
    assert!(
        String::from_utf8_lossy(&review_drift.stderr).contains("reviewed plan authority changed")
    );
    fs::write(&review_path, review_bytes)?;

    let old_root = temp.path().join("project-root-before-tick-replacement");
    fs::rename(&project_root, &old_root)?;
    fs::create_dir(&project_root)?;
    let root_drift = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_drift_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(!root_drift.status.success());
    assert!(String::from_utf8_lossy(&root_drift.stderr).contains("root identity"));
    fs::remove_dir(&project_root)?;
    fs::rename(&old_root, &project_root)?;

    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let original_tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    let mut exhausted_tasks = original_tasks.clone();
    exhausted_tasks[0]["attempt_count"] = json!(1);
    fs::write(&tasks_path, serde_json::to_vec_pretty(&exhausted_tasks)?)?;
    let exhausted = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_drift_1",
            "--task-id",
            "task_shift_1",
            "--json",
        ])
        .output()?;
    assert!(exhausted.status.success());
    let exhausted: Value = serde_json::from_slice(&exhausted.stdout)?;
    assert_eq!(exhausted["launched"], 0);
    assert_eq!(
        exhausted["bounded_shift"]["hold_reasons"],
        json!(["task_attempt_budget_exhausted"])
    );

    let mut tasks = original_tasks;
    let mut unbound = tasks[0].clone();
    unbound["task_id"] = json!("task_unbound");
    unbound["request_id"] = json!("request_unbound");
    unbound["execution_brief"]["task_id"] = json!("task_unbound");
    unbound["execution_brief"]["request_id"] = json!("request_unbound");
    tasks.as_array_mut().unwrap().push(unbound);
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
    let unbound = forager_command(temp.path())
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "shift_tick_drift_1",
            "--task-id",
            "task_unbound",
            "--json",
        ])
        .output()?;
    assert!(unbound.status.success());
    let unbound: Value = serde_json::from_slice(&unbound.stdout)?;
    assert_eq!(unbound["launched"], 0);
    assert_eq!(
        unbound["bounded_shift"]["hold_reasons"],
        json!(["task_not_bound_to_active_shift"])
    );
    Ok(())
}

#[test]
fn shift_arm_rejects_review_task_root_and_approval_identity_drift() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let approval_id = prepare_shift_and_approve(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_drift_1",
        starts_at,
        stops_at,
    )?;

    let plan_dir = profile_dir(temp.path())
        .join("offdesk_plans")
        .join(&plan_id);
    let review_path = fs::read_dir(&plan_dir)?
        .filter_map(std::result::Result::ok)
        .map(|entry| entry.path())
        .find(|path| {
            path.file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with("plan_review_"))
        })
        .expect("plan review path");
    let review_bytes = fs::read(&review_path)?;
    let mut changed_review = review_bytes.clone();
    changed_review.push(b'\n');
    fs::write(&review_path, changed_review)?;
    let changed_review_arm = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_drift_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!changed_review_arm.status.success());
    assert!(String::from_utf8_lossy(&changed_review_arm.stderr)
        .contains("reviewed plan authority changed"));
    fs::write(&review_path, &review_bytes)?;

    let approval_path = profile_dir(temp.path()).join("pending_action_approvals.json");
    let approval_bytes = fs::read(&approval_path)?;
    let mut approvals: Value = serde_json::from_slice(&approval_bytes)?;
    approvals[0]["metadata"]["manifest_sha256"] = json!("f".repeat(64));
    fs::write(&approval_path, serde_json::to_vec_pretty(&approvals)?)?;
    let changed_approval_arm = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_drift_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!changed_approval_arm.status.success());
    assert!(String::from_utf8_lossy(&changed_approval_arm.stderr).contains("metadata mismatch"));
    fs::write(&approval_path, &approval_bytes)?;

    let tasks_path = profile_dir(temp.path()).join("offdesk_tasks.json");
    let task_bytes = fs::read(&tasks_path)?;
    let mut tasks: Value = serde_json::from_slice(&task_bytes)?;
    tasks[0]["command"] = json!("printf changed-after-review");
    fs::write(&tasks_path, serde_json::to_vec_pretty(&tasks)?)?;
    let changed_task_arm = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_drift_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!changed_task_arm.status.success());
    assert!(String::from_utf8_lossy(&changed_task_arm.stderr).contains("launch spec"));
    fs::write(&tasks_path, &task_bytes)?;

    let old_root = temp.path().join("project-root-before-replacement");
    fs::rename(&project_root, &old_root)?;
    fs::create_dir(&project_root)?;
    let changed_root_arm = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_drift_1",
            "--approval-id",
            &approval_id,
            "--json",
        ])
        .output()?;
    assert!(!changed_root_arm.status.success());
    assert!(String::from_utf8_lossy(&changed_root_arm.stderr).contains("root identity"));

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_drift_1", "--json"])
        .output()?;
    assert!(show.status.success());
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert!(record["state"].is_null());
    assert!(record["arm_receipt"].is_null());
    let stored_approvals: Value = serde_json::from_slice(&fs::read(&approval_path)?)?;
    assert_eq!(stored_approvals[0]["status"], "approved");
    Ok(())
}

#[test]
fn shift_wiki_routine_is_registry_bound_and_candidate_only() -> Result<()> {
    let temp = tempdir()?;
    let wiki_root = temp.path().join("wiki-root");
    fs::create_dir(&wiki_root)?;
    let registry_path = temp.path().join("projects.toml");
    let registry = "schema = \"forager_project_registry.v1\"\n\n[projects.project]\ndisplay_name = \"Project\"\nworkspace_patterns = [\"wiki-root\"]\nwiki_profile = \"project-wiki\"\n".to_string();
    fs::write(&registry_path, &registry)?;

    let plan_path = temp.path().join("WIKI_PLAN.json");
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
                "id": "wiki_phase",
                "objective": "Distill candidate-only Wiki lessons."
            }],
            "authority": {
                "read_only_plan": true,
                "does_not_authorize": [
                    "enqueue", "launch", "approval", "file movement", "archive",
                    "delete", "wiki promotion", "accepted truth"
                ]
            }
        }))?,
    )?;
    let registration_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan",
            plan_path.to_str().expect("UTF-8 plan path"),
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(registration_output.status.success());
    let registration: Value = serde_json::from_slice(&registration_output.stdout)?;
    let plan_id = Path::new(
        registration["artifacts"]["registry_dir"]
            .as_str()
            .expect("registry directory"),
    )
    .file_name()
    .expect("plan ID")
    .to_string_lossy()
    .into_owned();
    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "plan-review",
            &plan_id,
            "--decision",
            "approved",
            "--reason",
            "Candidate-only Wiki routine reviewed.",
            "--json",
        ])
        .output()?;
    assert!(review_output.status.success());
    let review: Value = serde_json::from_slice(&review_output.stdout)?;
    let review_id = review["review_id"].as_str().expect("review ID");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(2);
    let wiki_at = starts_at + Duration::minutes(30);

    let prepare = forager_command(temp.path())
        .env("FORAGER_PROJECT_REGISTRY", &registry_path)
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            review_id,
            "--shift-id",
            "shift_wiki_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--wiki-project-key",
            "project",
            "--wiki-profile",
            "project-wiki",
            "--wiki-root",
            wiki_root.to_str().expect("UTF-8 wiki root"),
            "--wiki-at",
            &rfc3339(wiki_at),
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "wiki shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let prepared: Value = serde_json::from_slice(&prepare.stdout)?;
    let routine = &prepared["record"]["manifest"]["routine_bindings"][0];
    assert_eq!(routine["routine_kind"], "wiki_candidate_distillation");
    assert_eq!(routine["project_key"], "project");
    assert_eq!(routine["wiki_profile"], "project-wiki");
    assert_eq!(
        prepared["record"]["manifest"]["authority"]["wiki_writes_are_candidate_only"],
        true
    );

    let pending_output = forager_command(temp.path())
        .env("FORAGER_PROJECT_REGISTRY", &registry_path)
        .args(["offdesk", "shift", "arm", "shift_wiki_1", "--json"])
        .output()?;
    assert!(pending_output.status.success());
    let pending: Value = serde_json::from_slice(&pending_output.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    let approval = forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(approval.status.success());

    fs::write(
        &registry_path,
        registry.replace("project-wiki", "other-wiki"),
    )?;
    let drifted = forager_command(temp.path())
        .env("FORAGER_PROJECT_REGISTRY", &registry_path)
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_wiki_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(!drifted.status.success());
    assert!(String::from_utf8_lossy(&drifted.stderr).contains("project registry"));

    fs::write(&registry_path, registry)?;
    let armed = forager_command(temp.path())
        .env("FORAGER_PROJECT_REGISTRY", &registry_path)
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_wiki_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        armed.status.success(),
        "wiki shift arm failed: {}",
        String::from_utf8_lossy(&armed.stderr)
    );
    let armed: Value = serde_json::from_slice(&armed.stdout)?;
    assert_eq!(armed["record"]["state"]["status"], "armed");
    Ok(())
}

#[test]
fn shift_routine_reservation_and_outcome_are_exact_idempotent_and_visible() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let morning_at = starts_at + Duration::seconds(30);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_routine_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "shift prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let pending = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", "shift_routine_1", "--json"])
        .output()?;
    assert!(pending.status.success());
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    assert!(forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--by", "operator", "--json"])
        .status()?
        .success());
    assert!(forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_routine_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .status()?
        .success());

    let start = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_routine_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(
        start.status.success(),
        "routine start failed: {}",
        String::from_utf8_lossy(&start.stderr)
    );
    let start: Value = serde_json::from_slice(&start.stdout)?;
    assert_eq!(start["schema"], "offdesk_shift_routine_start_report.v1");
    assert_eq!(start["outcome"], "reserved");
    assert_eq!(start["record"]["state"]["status"], "running");
    let run_id = start["reservation"]["run_id"]
        .as_str()
        .expect("run ID")
        .to_string();

    let current_review = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "review",
            "shift_routine_1",
            "--current-run-id",
            &run_id,
            "--json",
        ])
        .output()?;
    assert!(
        current_review.status.success(),
        "current routine review failed: {}",
        String::from_utf8_lossy(&current_review.stderr)
    );
    let current_review: Value = serde_json::from_slice(&current_review.stdout)?;
    assert_eq!(current_review["current_routine_run_id"], run_id);
    assert_eq!(
        current_review["routines"][0]["validation_status"],
        "projection_in_progress"
    );
    assert_eq!(
        current_review["summary"]["routine_runs_requiring_attention"],
        0
    );
    assert!(!current_review["attention_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|reason| reason == "routine_run_requires_attention"));

    let current_workstation = forager_command(temp.path())
        .args([
            "ondesk",
            "workstation-surface",
            "--shift-id",
            "shift_routine_1",
            "--current-run-id",
            &run_id,
            "--json",
        ])
        .output()?;
    assert!(
        current_workstation.status.success(),
        "current routine workstation projection failed: {}",
        String::from_utf8_lossy(&current_workstation.stderr)
    );
    let current_workstation: Value = serde_json::from_slice(&current_workstation.stdout)?;
    assert_eq!(
        current_workstation["offdesk_shift_review"]["routines"][0]["validation_status"],
        "projection_in_progress"
    );
    assert!(
        !current_workstation["offdesk_shift_review"]["attention_reasons"]
            .as_array()
            .unwrap()
            .iter()
            .any(|reason| reason == "routine_run_requires_attention")
    );

    let replay = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_routine_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(replay.status.success());
    let replay: Value = serde_json::from_slice(&replay.stdout)?;
    assert_eq!(replay["outcome"], "recovery_required");
    assert_eq!(replay["reservation"]["run_id"], run_id);

    let evidence_path = temp.path().join("tick-report.json");
    fs::write(&evidence_path, b"{\"status\":\"ok\"}\n")?;

    let unmeasured = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_1",
            &run_id,
            "--outcome",
            "succeeded",
            "--summary",
            "unmeasured routine result",
            "--evidence",
            evidence_path.to_str().expect("UTF-8 evidence path"),
            "--json",
        ])
        .output()?;
    assert!(!unmeasured.status.success());
    assert!(String::from_utf8_lossy(&unmeasured.stderr).contains("--usage-complete"));
    let after_unmeasured = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_routine_1", "--json"])
        .output()?;
    let after_unmeasured: Value = serde_json::from_slice(&after_unmeasured.stdout)?;
    assert_eq!(
        after_unmeasured["routine_outcome_receipts"]
            .as_array()
            .unwrap()
            .len(),
        0
    );

    let complete = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_1",
            &run_id,
            "--outcome",
            "succeeded",
            "--summary",
            "exact shift tick completed",
            "--evidence",
            evidence_path.to_str().expect("UTF-8 evidence path"),
            "--usage-complete",
            "--process-starts",
            "1",
            "--external-calls",
            "1",
            "--json",
        ])
        .output()?;
    assert!(
        complete.status.success(),
        "routine complete failed: {}",
        String::from_utf8_lossy(&complete.stderr)
    );
    let complete: Value = serde_json::from_slice(&complete.stdout)?;
    assert_eq!(
        complete["schema"],
        "offdesk_shift_routine_complete_report.v1"
    );
    assert_eq!(complete["outcome"], "succeeded");
    assert_eq!(complete["created"], true);
    assert_eq!(
        complete["receipt"]["evidence"][0]["size_bytes"],
        fs::metadata(&evidence_path)?.len()
    );
    assert_eq!(
        complete["receipt"]["evidence"][0]["sha256"]
            .as_str()
            .expect("evidence hash")
            .len(),
        64
    );

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_1",
            &run_id,
            "--outcome",
            "succeeded",
            "--summary",
            "exact shift tick completed",
            "--evidence",
            evidence_path.to_str().expect("UTF-8 evidence path"),
            "--usage-complete",
            "--process-starts",
            "1",
            "--external-calls",
            "1",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let repeated: Value = serde_json::from_slice(&repeated.stdout)?;
    assert_eq!(repeated["created"], false);

    let changed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_1",
            &run_id,
            "--outcome",
            "failed",
            "--summary",
            "contradictory retry",
            "--usage-complete",
            "--process-starts",
            "1",
            "--external-calls",
            "1",
            "--json",
        ])
        .output()?;
    assert!(!changed.status.success());
    assert!(String::from_utf8_lossy(&changed.stderr).contains("different content"));

    let same_slot = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_routine_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(same_slot.status.success());
    let same_slot: Value = serde_json::from_slice(&same_slot.stdout)?;
    assert_eq!(same_slot["outcome"], "already_completed");

    let status = forager_command(temp.path())
        .args(["offdesk", "shift", "status", "--json"])
        .output()?;
    assert!(status.status.success());
    let status: Value = serde_json::from_slice(&status.stdout)?;
    assert_eq!(status["shifts"][0]["routine_reservation_count"], 1);
    assert_eq!(status["shifts"][0]["routine_outcome_count"], 1);
    assert_eq!(status["shifts"][0]["unresolved_routine_count"], 0);

    let review = forager_command(temp.path())
        .args(["offdesk", "shift", "review", "shift_routine_1", "--json"])
        .output()?;
    assert!(review.status.success());
    let review: Value = serde_json::from_slice(&review.stdout)?;
    let routine_evidence = review["evidence_drilldown"]["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["kind"] == "routine_receipt_evidence")
        .expect("hash-bound routine evidence");
    assert_eq!(
        routine_evidence["reference"],
        evidence_path.display().to_string()
    );
    assert_eq!(
        routine_evidence["sha256"],
        complete["receipt"]["evidence"][0]["sha256"]
    );
    assert_eq!(
        routine_evidence["size_bytes"],
        complete["receipt"]["evidence"][0]["size_bytes"]
    );
    assert_eq!(
        routine_evidence["verification"],
        "receipt_hash_and_size_bound"
    );
    assert!(routine_evidence.get("reported_present").is_none());
    Ok(())
}

#[test]
fn shift_routine_failure_moves_the_shift_to_attention_required() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_routine_failed_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--json",
        ])
        .output()?;
    assert!(prepare.status.success());
    let pending = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_routine_failed_1",
            "--json",
        ])
        .output()?;
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    assert!(forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .status()?
        .success());
    assert!(forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_routine_failed_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .status()?
        .success());
    let start = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_routine_failed_1",
            "dispatch_tick",
            "--json",
        ])
        .output()?;
    let start: Value = serde_json::from_slice(&start.stdout)?;
    let run_id = start["reservation"]["run_id"].as_str().expect("run ID");
    let disguised_dispatch = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "review",
            "shift_routine_failed_1",
            "--current-run-id",
            run_id,
            "--json",
        ])
        .output()?;
    assert!(!disguised_dispatch.status.success());
    assert!(String::from_utf8_lossy(&disguised_dispatch.stderr)
        .contains("not a morning-review routine"));
    let failed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_failed_1",
            run_id,
            "--outcome",
            "skipped",
            "--summary",
            "required source was unavailable",
            "--usage-complete",
            "--json",
        ])
        .output()?;
    assert!(failed.status.success());
    let failed: Value = serde_json::from_slice(&failed.stdout)?;
    assert_eq!(failed["record"]["state"]["status"], "attention_required");
    assert_eq!(failed["record"]["state"]["sequence"], 3);

    let review = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "review",
            "shift_routine_failed_1",
            "--json",
        ])
        .output()?;
    assert!(review.status.success());
    let review: Value = serde_json::from_slice(&review.stdout)?;
    assert_eq!(review["lifecycle_status"], "attention_required");
    assert_eq!(review["review_status"], "needs_attention");
    assert_eq!(review["summary"]["routine_runs_requiring_attention"], 1);
    assert!(review["attention_reasons"]
        .as_array()
        .unwrap()
        .iter()
        .any(|reason| reason == "routine_run_requires_attention"));

    let council = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "council-packet",
            "shift_routine_failed_1",
            "--json",
        ])
        .output()?;
    assert!(
        council.status.success(),
        "Council packet failed: {}",
        String::from_utf8_lossy(&council.stderr)
    );
    let council: Value = serde_json::from_slice(&council.stdout)?;
    assert_eq!(
        council["drift"]["assessment"],
        "deterministic_signals_present"
    );
    assert!(council["drift"]["signals"]
        .as_array()
        .expect("drift signals")
        .iter()
        .any(|signal| signal == "routine:dispatch_tick:skipped"));
    assert_eq!(council["remaining_budget"]["routine"]["reserved_runs"], 1);
    assert_eq!(
        council["authority"]["recommendation_authorizes_execution"],
        false
    );

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_routine_failed_1",
            run_id,
            "--outcome",
            "skipped",
            "--summary",
            "required source was unavailable",
            "--usage-complete",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let repeated: Value = serde_json::from_slice(&repeated.stdout)?;
    assert_eq!(repeated["created"], false);
    assert_eq!(repeated["record"]["state"]["status"], "attention_required");
    assert_eq!(repeated["record"]["state"]["sequence"], 3);

    let after = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_routine_failed_1",
            "dispatch_tick",
            "--json",
        ])
        .output()?;
    assert!(after.status.success());
    let after: Value = serde_json::from_slice(&after.stdout)?;
    assert_eq!(after["outcome"], "already_completed");
    assert_eq!(after["receipt"], failed["receipt"]);
    Ok(())
}

#[test]
fn shift_routine_recovery_closes_start_only_without_claiming_success() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(2);
    let morning_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    prepare_and_arm_morning_shift(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_start_only_1",
        starts_at,
        stops_at,
        morning_at,
    )?;

    let start = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_start_only_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(start.status.success());
    let start: Value = serde_json::from_slice(&start.stdout)?;
    assert_eq!(start["outcome"], "reserved");
    let run_id = start["reservation"]["run_id"]
        .as_str()
        .expect("run ID")
        .to_string();

    let unacknowledged = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-recover",
            "shift_start_only_1",
            &run_id,
            "--reason",
            "timer exited after reserving",
            "--json",
        ])
        .output()?;
    assert!(!unacknowledged.status.success());
    assert!(
        String::from_utf8_lossy(&unacknowledged.stderr).contains("--acknowledge-execution-unknown")
    );

    let original_project_root = temp.path().join("project-root-before-recovery");
    fs::rename(&project_root, &original_project_root)?;
    fs::create_dir(&project_root)?;
    let recovered = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-recover",
            "shift_start_only_1",
            &run_id,
            "--reason",
            "timer exited after reserving",
            "--acknowledge-execution-unknown",
            "--json",
        ])
        .output()?;
    assert!(
        recovered.status.success(),
        "routine recovery failed: {}",
        String::from_utf8_lossy(&recovered.stderr)
    );
    let recovered: Value = serde_json::from_slice(&recovered.stdout)?;
    assert_eq!(
        recovered["schema"],
        "offdesk_shift_routine_recovery_report.v1"
    );
    assert_eq!(recovered["outcome"], "recovered_failed");
    assert_eq!(recovered["created"], true);
    assert_eq!(recovered["new_execution_result_asserted"], false);
    assert_eq!(recovered["receipt"]["outcome"], "failed");
    assert_eq!(
        recovered["receipt"]["recovery_acknowledgement"]["schema"],
        "offdesk_shift_routine_recovery_acknowledgement.v1"
    );
    assert_eq!(
        recovered["receipt"]["recovery_acknowledgement"]["execution_result_unknown"],
        true
    );
    assert_eq!(
        recovered["receipt"]["recovery_acknowledgement"]["success_not_asserted"],
        true
    );
    assert!(recovered["receipt"]["summary"]
        .as_str()
        .unwrap()
        .contains("execution as failed"));
    assert_eq!(recovered["record"]["state"]["status"], "attention_required");

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-recover",
            "shift_start_only_1",
            &run_id,
            "--reason",
            "repeat operator inspection",
            "--acknowledge-execution-unknown",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let repeated: Value = serde_json::from_slice(&repeated.stdout)?;
    assert_eq!(repeated["outcome"], "already_terminal");
    assert_eq!(repeated["created"], false);
    assert_eq!(repeated["receipt"], recovered["receipt"]);

    fs::remove_dir(&project_root)?;
    fs::rename(&original_project_root, &project_root)?;
    let start_again = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_start_only_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    assert!(start_again.status.success());
    let start_again: Value = serde_json::from_slice(&start_again.stdout)?;
    assert_eq!(start_again["outcome"], "already_completed");
    assert_eq!(start_again["receipt"], recovered["receipt"]);

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_start_only_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["routine_run_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["routine_outcome_receipts"].as_array().unwrap().len(),
        1
    );
    Ok(())
}

#[test]
fn rust_routine_claim_selects_only_the_manifest_bound_handler_policy() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(2);
    let morning_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    prepare_and_arm_morning_shift(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_rust_policy_1",
        starts_at,
        stops_at,
        morning_at,
    )?;

    let missing = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-claim",
            "shift_rust_policy_1",
            "--task",
            "tick",
            "--json",
        ])
        .output()?;
    assert!(missing.status.success());
    let missing: Value = serde_json::from_slice(&missing.stdout)?;
    assert_eq!(missing["outcome"], "not_configured");
    assert!(missing.get("policy").is_none());
    assert!(missing.get("reservation").is_none());

    let claimed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-claim",
            "shift_rust_policy_1",
            "--task",
            "brief",
            "--json",
        ])
        .output()?;
    assert!(
        claimed.status.success(),
        "routine claim failed: {}",
        String::from_utf8_lossy(&claimed.stderr)
    );
    let claimed: Value = serde_json::from_slice(&claimed.stdout)?;
    assert_eq!(claimed["schema"], "offdesk_shift_routine_claim_report.v1");
    assert_eq!(claimed["requested_task"], "brief");
    assert_eq!(claimed["outcome"], "reserved");
    assert_eq!(
        claimed["policy"]["schema"],
        "offdesk_shift_routine_execution_policy.v1"
    );
    assert_eq!(claimed["policy"]["routine_id"], "morning_review");
    assert_eq!(claimed["policy"]["routine_kind"], "morning_review_brief");
    assert_eq!(claimed["policy"]["handler"], "morning_review_delivery");
    assert_eq!(
        claimed["policy"]["allowed_effect"],
        "local_review_artifacts_and_notification"
    );
    assert_eq!(claimed["policy"]["disarm_after_terminal"], true);
    assert_eq!(
        claimed["policy"]["enterprise_policy_key"],
        "single_owner_local"
    );
    assert_eq!(claimed["policy"]["operator_mode"], "single_owner");
    assert_eq!(
        claimed["policy"]["notification_route"],
        "telegram_owner_private"
    );
    assert_eq!(
        claimed["policy"]["allowed_forager_network_effects"],
        json!(["loopback_model", "telegram_bot_api"])
    );
    assert_eq!(
        claimed["policy"]["child_task_network_boundary"],
        "host_policy_required"
    );
    assert_eq!(
        claimed["policy"]["forager_controls_child_task_network"],
        false
    );
    assert_eq!(
        claimed["policy"]["does_not_authorize"]
            .as_array()
            .unwrap()
            .len(),
        8
    );
    assert!(claimed["policy"]["does_not_authorize"]
        .as_array()
        .unwrap()
        .iter()
        .any(|value| value == "accepted_truth"));
    assert_eq!(
        claimed["reservation"]["routine_id"],
        claimed["policy"]["routine_id"]
    );
    assert_eq!(
        claimed["reservation"]["routine_kind"],
        claimed["policy"]["routine_kind"]
    );

    let repeated = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-claim",
            "shift_rust_policy_1",
            "--task",
            "brief",
            "--json",
        ])
        .output()?;
    assert!(repeated.status.success());
    let repeated: Value = serde_json::from_slice(&repeated.stdout)?;
    assert_eq!(repeated["outcome"], "recovery_required");
    assert_eq!(repeated["reservation"], claimed["reservation"]);
    assert_eq!(repeated["policy"], claimed["policy"]);
    Ok(())
}

#[test]
fn routine_claim_hard_stops_after_cumulative_process_budget_is_spent() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(25);
    let morning_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_resource_stop_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--morning-review-at",
            &rfc3339(morning_at),
            "--max-routine-process-starts",
            "1",
            "--json",
        ])
        .output()?;
    assert!(
        prepare.status.success(),
        "resource-bounded prepare failed: {}",
        String::from_utf8_lossy(&prepare.stderr)
    );
    let pending = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", "shift_resource_stop_1", "--json"])
        .output()?;
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    assert!(forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?
        .status
        .success());
    assert!(forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_resource_stop_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?
        .status
        .success());

    let tick = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-claim",
            "shift_resource_stop_1",
            "--task",
            "tick",
            "--json",
        ])
        .output()?;
    let tick: Value = serde_json::from_slice(&tick.stdout)?;
    assert_eq!(tick["outcome"], "reserved");
    assert_eq!(tick["policy"]["resource_allowance"]["process_starts"], 1);
    let run_id = tick["reservation"]["run_id"].as_str().expect("run ID");
    let complete = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_resource_stop_1",
            run_id,
            "--outcome",
            "succeeded",
            "--summary",
            "bounded observation-only tick",
            "--usage-complete",
            "--process-starts",
            "1",
            "--json",
        ])
        .output()?;
    assert!(
        complete.status.success(),
        "resource usage completion failed: {}",
        String::from_utf8_lossy(&complete.stderr)
    );

    let brief = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-claim",
            "shift_resource_stop_1",
            "--task",
            "brief",
            "--json",
        ])
        .output()?;
    assert!(brief.status.success());
    let brief: Value = serde_json::from_slice(&brief.stdout)?;
    assert_eq!(brief["outcome"], "held");
    assert_eq!(brief["reason"], "shift_routine_process_budget_exhausted");
    assert_eq!(brief["policy"]["resource_allowance"]["process_starts"], 0);
    assert_eq!(
        brief["record"]["routine_run_reservations"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    Ok(())
}

#[test]
fn python_adapter_rejects_a_noncanonical_rust_routine_policy() -> Result<()> {
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,subprocess,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
payload = {
    "schema": "offdesk_shift_routine_claim_report.v1",
    "requested_task": "brief",
    "outcome": "reserved",
    "record": {"manifest": {
        "shift_id": "shift_policy_forgery_1",
        "enterprise_policy": {
            "schema": "offdesk_shift_enterprise_policy.v1",
            "profile_key": "single_owner_local",
            "operator_mode": "single_owner",
            "notification_route": "telegram_owner_private",
            "allowed_forager_network_effects": ["loopback_model", "telegram_bot_api"],
            "child_task_network_boundary": "host_policy_required",
            "forager_controls_child_task_network": False,
        },
    }},
    "routine": {"routine_id": "morning_review", "routine_kind": "morning_review_brief"},
    "policy": {
        "schema": "offdesk_shift_routine_execution_policy.v1",
        "routine_id": "morning_review",
        "routine_kind": "morning_review_brief",
        "handler": "morning_review_delivery",
        "allowed_effect": "local_review_artifacts_and_notification",
        "disarm_after_terminal": True,
        "enterprise_policy_key": "single_owner_local",
        "operator_mode": "single_owner",
        "notification_route": "telegram_owner_private",
        "allowed_forager_network_effects": ["loopback_model", "telegram_bot_api"],
        "child_task_network_boundary": "host_policy_required",
        "forager_controls_child_task_network": False,
        "resource_allowance": {
            "process_starts": -1,
            "external_calls": 1,
            "disk_bytes_written": 1024,
            "llm_tokens": 0,
            "cost_micro_usd": 0,
            "cost_rate_micro_usd_per_million_tokens": 0,
        },
        "does_not_authorize": sorted(m.ROUTINE_POLICY_DENIALS),
    },
    "reservation": {
        "shift_id": "shift_policy_forgery_1",
        "routine_id": "morning_review",
        "routine_kind": "morning_review_brief",
        "run_id": "run_policy_forgery_1",
    },
}
m.run_forager = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), "")
try:
    m.claim_routine("default", "shift_policy_forgery_1", "brief")
except RuntimeError as error:
    print(str(error))
else:
    raise SystemExit("forged policy was accepted")
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .output()?;
    assert!(
        output.status.success(),
        "policy forgery probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("invalid resource allowance"));
    Ok(())
}

#[test]
#[cfg(unix)]
fn python_wiki_prereview_rejects_output_over_budget_before_writing() -> Result<()> {
    let temp = tempdir()?;
    let fake_forager = temp.path().join("fake-forager.py");
    fs::write(&fake_forager, "#!/usr/bin/env python3\nprint('[]')\n")?;
    fs::set_permissions(&fake_forager, fs::Permissions::from_mode(0o700))?;
    let packet = temp.path().join("packet.md");
    let report = temp.path().join("report.json");
    let script = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/offdesk_wiki_prereview.py");
    let output = Command::new("python3")
        .arg(script)
        .args([
            "--profile",
            "default",
            "--forager-bin",
            fake_forager.to_str().expect("UTF-8 fake forager"),
            "--packet",
            packet.to_str().expect("UTF-8 packet"),
            "--out",
            report.to_str().expect("UTF-8 report"),
            "--max-output-bytes",
            "1",
        ])
        .output()?;
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("exceeds"));
    assert!(!packet.exists());
    assert!(!report.exists());
    Ok(())
}

#[test]
fn python_wiki_adapter_uses_manifest_allowance_and_reports_exact_cost() -> Result<()> {
    let temp = tempdir()?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,pathlib,subprocess,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
m.forager_bin = lambda: 'forager'
def execute(command, **kwargs):
    report = pathlib.Path(command[command.index('--out') + 1])
    packet = pathlib.Path(command[command.index('--packet') + 1])
    assert command[command.index('--num-ctx') + 1] == '5000'
    assert command[command.index('--num-predict') + 1] == '1250'
    assert command[command.index('--max-output-bytes') + 1] == '4096'
    report.write_text(json.dumps({
        'schema': 'wiki_prereview_report.v1',
        'items': [{'id': 'candidate_1'}],
        'llm_usage': {'calls': 1, 'prompt_tokens': 1000, 'output_tokens': 500},
    }) + '\n', encoding='utf-8')
    packet.write_text('candidate packet\n', encoding='utf-8')
    return subprocess.CompletedProcess(command, 0, '', '')
m.subprocess.run = execute
policy = {
    'project_key': 'project',
    'wiki_profile': 'wiki-profile',
    'resource_allowance': {
        'process_starts': 2,
        'external_calls': 1,
        'disk_bytes_written': 4096,
        'llm_tokens': 5000,
        'cost_micro_usd': 5,
        'cost_rate_micro_usd_per_million_tokens': 1000,
    },
}
print(json.dumps(m.execute_distill('default', policy, 'run_wiki_usage_1')))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .env("FORAGER_SHIFT_OUTPUT_DIR", temp.path())
        .env("OFFDESK_LLM_BASE_URL", "http://127.0.0.1:11434")
        .env("OFFDESK_LLM_MODEL", "bounded-model")
        .output()?;
    assert!(
        output.status.success(),
        "Wiki usage probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result[0], "succeeded");
    assert_eq!(result[3]["process_starts"], 2);
    assert_eq!(result[3]["external_calls"], 1);
    assert_eq!(result[3]["llm_tokens"], 1500);
    assert_eq!(result[3]["cost_micro_usd"], 2);
    assert_eq!(result[3]["cost_rate_micro_usd_per_million_tokens"], 1000);
    Ok(())
}

#[test]
#[cfg(unix)]
fn python_timer_retries_disarm_after_an_outcome_only_crash() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(2);
    let morning_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    prepare_and_arm_morning_shift(
        temp.path(),
        &plan_id,
        &review_id,
        "shift_disarm_retry_1",
        starts_at,
        stops_at,
        morning_at,
    )?;
    let start = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-start",
            "shift_disarm_retry_1",
            "morning_review",
            "--json",
        ])
        .output()?;
    let start: Value = serde_json::from_slice(&start.stdout)?;
    let run_id = start["reservation"]["run_id"].as_str().expect("run ID");
    let complete = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "routine-complete",
            "shift_disarm_retry_1",
            run_id,
            "--outcome",
            "failed",
            "--summary",
            "review evidence exists but delivery failed",
            "--usage-complete",
            "--json",
        ])
        .output()?;
    assert!(complete.status.success());

    let marker = temp.path().join("reject-first-disarm");
    let wrapper = temp.path().join("forager-disarm-once.py");
    let real_bin = serde_json::to_string(env!("CARGO_BIN_EXE_forager"))?;
    let marker_path = serde_json::to_string(marker.to_str().expect("UTF-8 marker"))?;
    fs::write(
        &wrapper,
        format!(
            r#"#!/usr/bin/env python3
import pathlib
import subprocess
import sys

real_bin = {real_bin}
marker = pathlib.Path({marker_path})
args = sys.argv[1:]
if "disarm" in args and not marker.exists():
    marker.write_text("failed once\n", encoding="utf-8")
    print("injected disarm failure", file=sys.stderr)
    raise SystemExit(1)
result = subprocess.run([real_bin, *args], capture_output=True)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
raise SystemExit(result.returncode)
"#
        ),
    )?;
    fs::set_permissions(&wrapper, fs::Permissions::from_mode(0o700))?;
    let runner = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/offdesk_autonomy_run.py");
    let run_timer = || {
        let mut command = Command::new("python3");
        command
            .env("HOME", temp.path())
            .env("FORAGER_BIN", &wrapper)
            .env_remove("FORAGER_PROFILE")
            .env_remove("AGENT_OF_EMPIRES_PROFILE")
            .arg(&runner)
            .args([
                "--profile",
                "default",
                "--shift-id",
                "shift_disarm_retry_1",
                "--task",
                "brief",
            ]);
        #[cfg(target_os = "linux")]
        command.env("XDG_CONFIG_HOME", temp.path().join(".config"));
        command.output()
    };

    let first = run_timer()?;
    assert_eq!(first.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&first.stdout).contains("already_completed"));
    let after_first = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_disarm_retry_1", "--json"])
        .output()?;
    let after_first: Value = serde_json::from_slice(&after_first.stdout)?;
    assert_eq!(after_first["state"]["status"], "attention_required");
    assert_eq!(
        after_first["routine_outcome_receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );

    let second = run_timer()?;
    assert!(
        second.status.success(),
        "disarm retry failed: {}",
        String::from_utf8_lossy(&second.stderr)
    );
    assert!(String::from_utf8_lossy(&second.stdout).contains("already_completed"));
    let after_second = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_disarm_retry_1", "--json"])
        .output()?;
    let after_second: Value = serde_json::from_slice(&after_second.stdout)?;
    assert_eq!(after_second["state"]["status"], "disarmed");
    assert_eq!(
        after_second["routine_outcome_receipts"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    Ok(())
}

#[test]
fn python_morning_delivery_failure_preserves_local_evidence() -> Result<()> {
    let temp = tempdir()?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
paths = [pathlib.Path(sys.argv[2]) / "morning-review.json", pathlib.Path(sys.argv[2]) / "morning-review.md"]
for path in paths:
    path.write_text("evidence\n", encoding="utf-8")
m.write_local_brief = lambda profile, shift_id, run_id, max_bytes: (paths, "2 candidate(s)")
m.send_morning_attention = lambda profile, summary, shift_id, run_id, operator_mode: (False, True)
policy = {
    "operator_mode": "single_owner",
    "notification_route": "telegram_owner_private",
    "resource_allowance": {"process_starts": 1, "external_calls": 1, "disk_bytes_written": 1024, "llm_tokens": 0, "cost_micro_usd": 0, "cost_rate_micro_usd_per_million_tokens": 0},
}
print(json.dumps(m.execute_brief("default", "shift_delivery_1", "run_delivery_1", policy)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .arg(temp.path())
        .output()?;
    assert!(
        output.status.success(),
        "delivery failure probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result[0], "failed");
    assert_eq!(
        result[1],
        "shared morning review written but Telegram delivery failed"
    );
    assert_eq!(result[2].as_array().unwrap().len(), 2);
    assert!(result[2][0]
        .as_str()
        .unwrap()
        .ends_with("morning-review.json"));
    assert!(result[2][1]
        .as_str()
        .unwrap()
        .ends_with("morning-review.md"));
    assert_eq!(result[3]["process_starts"], 1);
    assert_eq!(result[3]["external_calls"], 1);
    assert_eq!(result[3]["disk_bytes_written"], 18);
    Ok(())
}

#[test]
fn python_airgapped_morning_review_stays_local_and_uses_no_external_call() -> Result<()> {
    let temp = tempdir()?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
paths = [pathlib.Path(sys.argv[2]) / "morning-review.json", pathlib.Path(sys.argv[2]) / "morning-review.md"]
for path in paths:
    path.write_text("evidence\n", encoding="utf-8")
m.write_local_brief = lambda profile, shift_id, run_id, max_bytes: (paths, "2 candidate(s)")
m.send_morning_attention = lambda *args: (_ for _ in ()).throw(AssertionError("Telegram must not be called"))
policy = {
    "operator_mode": "single_owner",
    "notification_route": "local_evidence_only",
    "resource_allowance": {"process_starts": 1, "external_calls": 0, "disk_bytes_written": 1024, "llm_tokens": 0, "cost_micro_usd": 0, "cost_rate_micro_usd_per_million_tokens": 0},
}
print(json.dumps(m.execute_brief("default", "shift_airgap_1", "run_airgap_1", policy)))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .arg(temp.path())
        .output()?;
    assert!(
        output.status.success(),
        "airgapped morning probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result[0], "succeeded");
    assert_eq!(
        result[1],
        "shared morning review written for local on-desk review"
    );
    assert_eq!(result[3]["external_calls"], 0);
    Ok(())
}

#[test]
fn python_wiki_enterprise_policy_rejects_non_loopback_model_url() -> Result<()> {
    let temp = tempdir()?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
policy = {
    "project_key": "project",
    "wiki_profile": "wiki-profile",
    "resource_allowance": {"process_starts": 2, "external_calls": 1, "disk_bytes_written": 4096, "llm_tokens": 5000, "cost_micro_usd": 5, "cost_rate_micro_usd_per_million_tokens": 1000},
}
print(json.dumps(m.execute_distill("default", policy, "run_remote_model_1")))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .env("FORAGER_SHIFT_OUTPUT_DIR", temp.path())
        .env("OFFDESK_LLM_BASE_URL", "https://model.example.invalid/v1")
        .env("OFFDESK_LLM_MODEL", "remote-model")
        .output()?;
    assert!(
        output.status.success(),
        "non-loopback model probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result[0], "identity-drift");
    assert!(result[1]
        .as_str()
        .unwrap()
        .contains("only a loopback Wiki model endpoint"));
    Ok(())
}

#[test]
fn python_morning_notification_rejects_operator_mode_mismatch_before_network() -> Result<()> {
    let temp = tempdir()?;
    let env_file = temp.path().join("telegram.env");
    fs::write(
        &env_file,
        "TELEGRAM_BOT_TOKEN=999999:fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\nTELEGRAM_OWNER_USER_ID=987654321\n",
    )?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = r#"import json,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
print(json.dumps(m.send_morning_attention("default", "0 candidate(s)", "shift_multi_1", "run_multi_1", "multi_operator")))
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .env("OFFDESK_TELEGRAM_ENV", &env_file)
        .output()?;
    assert!(
        output.status.success(),
        "operator-mode mismatch probe failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let result: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(result, json!([false, false]));
    Ok(())
}

#[test]
fn python_timer_adapter_discovers_then_pins_the_exact_rust_shift() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() - Duration::minutes(1);
    let stops_at = Utc::now() + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_python_timer_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--json",
        ])
        .output()?;
    assert!(prepare.status.success());
    let pending = forager_command(temp.path())
        .args(["offdesk", "shift", "arm", "shift_python_timer_1", "--json"])
        .output()?;
    let pending: Value = serde_json::from_slice(&pending.stdout)?;
    let approval_id = pending[0]["approval_id"].as_str().expect("approval ID");
    let approval = forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(approval.status.success());
    let armed = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "shift_python_timer_1",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(armed.status.success());

    let runner = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts/offdesk_autonomy_run.py");
    let mut command = Command::new("python3");
    command
        .env("HOME", temp.path())
        .env("FORAGER_BIN", env!("CARGO_BIN_EXE_forager"))
        .env_remove("FORAGER_PROFILE")
        .env_remove("AGENT_OF_EMPIRES_PROFILE")
        .arg(&runner)
        .args(["--profile", "default", "--task", "tick"]);
    #[cfg(target_os = "linux")]
    command.env("XDG_CONFIG_HOME", temp.path().join(".config"));
    let first = command.output()?;
    assert!(
        first.status.success(),
        "timer adapter failed: {}\n{}",
        String::from_utf8_lossy(&first.stdout),
        String::from_utf8_lossy(&first.stderr)
    );
    assert!(String::from_utf8_lossy(&first.stdout).contains("routine dispatch_tick: succeeded"));

    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_python_timer_1", "--json"])
        .output()?;
    assert!(show.status.success());
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["routine_run_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["routine_outcome_receipts"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["routine_outcome_receipts"][0]["outcome"],
        "succeeded"
    );
    assert!(!profile_dir(temp.path())
        .join("offdesk_autonomy_armed.json")
        .exists());

    let second = command.output()?;
    assert!(second.status.success());
    assert!(String::from_utf8_lossy(&second.stdout).contains("already_completed"));
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_python_timer_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(
        record["routine_run_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        record["routine_outcome_receipts"].as_array().unwrap().len(),
        1
    );
    Ok(())
}

#[test]
#[cfg(unix)]
fn python_timer_morning_brief_persists_the_shared_review_projection() -> Result<()> {
    let temp = tempdir()?;
    let fake_forager = temp.path().join("fake-forager.py");
    fs::write(
        &fake_forager,
        r#"#!/usr/bin/env python3
import json

print(json.dumps({
    "schema": "offdesk_shift_review_projection.v1",
    "shift_id": "shift_py_review_1",
    "current_routine_run_id": "run_py_review_1",
    "review_status": "needs_attention",
    "lifecycle_status": "review_ready",
    "summary": {
        "task_attempts_reserved": 1,
        "task_attempts_ticketed": 1,
        "task_attempts_terminal": 1,
        "task_attempts_completed": 1,
        "routine_runs_reserved": 2,
        "routine_runs_terminal": 1,
        "routine_runs_succeeded": 1
    },
    "tasks": [],
    "routines": [],
    "evidence_drilldown": {
        "policy": "bounded_operator_safe_review_evidence",
        "max_items": 64,
        "max_text_chars": 32768,
        "total_available": 1,
        "emitted_count": 1,
        "omitted_count": 0,
        "emitted_text_chars": 100,
        "truncated": False,
        "coverage": [{
            "source": "closeout",
            "detail_status": "summary_only",
            "available_items": 0,
            "emitted_items": 0,
            "omitted_items": 0,
            "note": "Closeout raw receipt content remains summary-only."
        }],
        "items": [{
            "source": "routine",
            "kind": "routine_receipt_evidence",
            "subject_id": "run_py_review_1",
            "label": "morning review",
            "reference": "morning-review.json",
            "reference_truncated": False,
            "text_truncated": False,
            "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "size_bytes": 10,
            "verification": "receipt_hash_and_size_bound"
        }]
    },
    "council": {"open_count": 0, "recent": []},
    "closeout": {"required_count": 1},
    "adaptive_wiki": {
        "candidate_count": 2,
        "review_due_count": 1,
        "targets": []
    },
    "attention_reasons": ["closeout_review_required", "wiki_review_required"],
    "authority": {
        "review_only": True,
        "task_dispatch_authorized": False,
        "accepted_truth_authorized": False,
        "plan_revision_authorized": False,
        "wiki_promotion_authorized": False
    }
}))
"#,
    )?;
    fs::set_permissions(&fake_forager, fs::Permissions::from_mode(0o700))?;
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let invocation = "import json,sys; sys.path.insert(0,sys.argv[1]); import offdesk_autonomy_run as m; print(json.dumps(([str(p) for p in m.write_local_brief('default','shift_py_review_1','run_py_review_1',1048576)[0]])))";
    let output = Command::new("python3")
        .arg("-c")
        .arg(invocation)
        .arg(&script_dir)
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .env("FORAGER_BIN", &fake_forager)
        .output()?;
    assert!(
        output.status.success(),
        "morning brief adapter failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let paths: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(paths.as_array().unwrap().len(), 2);
    let json_path = PathBuf::from(paths[0].as_str().unwrap());
    let markdown_path = PathBuf::from(paths[1].as_str().unwrap());
    let projection: Value = serde_json::from_slice(&fs::read(&json_path)?)?;
    assert_eq!(projection["schema"], "offdesk_shift_review_projection.v1");
    assert_eq!(projection["current_routine_run_id"], "run_py_review_1");
    assert_eq!(projection["authority"]["review_only"], true);
    let markdown = fs::read_to_string(markdown_path)?;
    assert!(markdown.contains("# Bounded Offdesk Shift Morning Review"));
    assert!(markdown.contains("## Bounded evidence drill-down"));
    assert!(markdown.contains("receipt_hash_and_size_bound"));
    assert!(markdown.contains("Closeout raw receipt content remains summary-only."));
    assert!(markdown.contains("This projection is review-only."));
    assert!(markdown.contains("closeout_review_required"));

    let capped_root = temp.path().join("capped-review");
    let cap_invocation = r#"import pathlib,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
try:
    m.write_local_brief('default','shift_py_review_1','run_py_review_1',1)
except RuntimeError as error:
    assert 'exceeds' in str(error)
else:
    raise SystemExit('over-budget review was written')
assert not any(path.is_file() for path in pathlib.Path(sys.argv[2]).rglob('*'))
"#;
    let capped = Command::new("python3")
        .arg("-c")
        .arg(cap_invocation)
        .arg(&script_dir)
        .arg(&capped_root)
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .env("FORAGER_BIN", &fake_forager)
        .env("FORAGER_SHIFT_OUTPUT_DIR", &capped_root)
        .output()?;
    assert!(
        capped.status.success(),
        "morning review output cap failed: {}",
        String::from_utf8_lossy(&capped.stderr)
    );

    let delivery_invocation = r#"import json,sys
sys.path.insert(0,sys.argv[1])
import offdesk_autonomy_run as m
import telegram_operator.config as config
import telegram_operator.transport as transport
config.default_telegram_env_file = lambda: None
config.resolve_telegram_config = lambda path, required: {"target_chat_id": "100", "token": "secret", "operator_mode": "single_owner"}
def capture(resolved, chat_id, message, args):
    print(json.dumps({"chat_id": chat_id, "message": message}))
    return 1
transport.send_message = capture
m.log = lambda message: None
assert m.send_morning_attention('ops-profile', '2 candidate(s)', 'shift_py_review_1', 'run_py_review_1', 'single_owner') == (True, True)
"#;
    let delivery = Command::new("python3")
        .arg("-c")
        .arg(delivery_invocation)
        .arg(&script_dir)
        .env("FORAGER_BIN", &fake_forager)
        .output()?;
    assert!(delivery.status.success());
    let delivery: Value = serde_json::from_slice(&delivery.stdout)?;
    assert_eq!(delivery["chat_id"], "100");
    assert!(delivery["message"]
        .as_str()
        .unwrap()
        .contains("shift_py_review_1"));
    Ok(())
}

#[test]
fn telegram_shift_confirmation_arms_only_the_observed_manifest_hash() -> Result<()> {
    let temp = tempdir()?;
    let project_root = temp.path().join("project-root");
    let starts_at = Utc::now() + Duration::minutes(2);
    let stops_at = starts_at + Duration::hours(1);
    let (plan_id, review_id) =
        register_review_and_enqueue(temp.path(), &project_root, starts_at, stops_at)?;
    let prepare = forager_command(temp.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan_id,
            "--review-id",
            &review_id,
            "--shift-id",
            "shift_telegram_arm_1",
            "--starts-at",
            &rfc3339(starts_at),
            "--stops-at",
            &rfc3339(stops_at),
            "--task-id",
            "task_shift_1",
            "--dispatch-interval-minutes",
            "10",
            "--json",
        ])
        .output()?;
    assert!(prepare.status.success());
    let prepared: Value = serde_json::from_slice(&prepare.stdout)?;
    let manifest_sha256 = prepared["manifest_sha256"]
        .as_str()
        .expect("manifest hash")
        .to_string();
    let script_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let call = "import json,sys; sys.path.insert(0,sys.argv[1]); import offdesk_remote_operator_telegram as m; print(json.dumps(m.arm_confirmed_shift(sys.argv[2], 'default', 'shift_telegram_arm_1', sys.argv[3])))";
    let telegram_command = |hash: &str| {
        let mut command = Command::new("python3");
        command
            .env("HOME", temp.path())
            .env_remove("FORAGER_PROFILE")
            .env_remove("AGENT_OF_EMPIRES_PROFILE")
            .args([
                "-c",
                call,
                script_dir.to_str().expect("UTF-8 script directory"),
                env!("CARGO_BIN_EXE_forager"),
                hash,
            ]);
        #[cfg(target_os = "linux")]
        command.env("XDG_CONFIG_HOME", temp.path().join(".config"));
        command
    };

    let wrong = telegram_command(&"f".repeat(64)).output()?;
    assert!(!wrong.status.success());
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_telegram_arm_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert!(record["state"].is_null());

    let correct = telegram_command(&manifest_sha256).output()?;
    assert!(
        correct.status.success(),
        "Telegram arm failed: {}\n{}",
        String::from_utf8_lossy(&correct.stdout),
        String::from_utf8_lossy(&correct.stderr)
    );
    let result: Value = serde_json::from_slice(&correct.stdout)?;
    assert_eq!(result["ok"], true);
    assert_eq!(result["shift_id"], "shift_telegram_arm_1");
    let show = forager_command(temp.path())
        .args(["offdesk", "shift", "show", "shift_telegram_arm_1", "--json"])
        .output()?;
    let record: Value = serde_json::from_slice(&show.stdout)?;
    assert_eq!(record["state"]["status"], "armed");
    assert_eq!(record["arm_receipt"]["approved_by"], "telegram");
    Ok(())
}

fn morning_return_command(home: &Path, root: &Path, plan: &Path, reference: &Path) -> Command {
    let mut command = forager_command(home);
    command.args([
        "ondesk",
        "prompt-package",
        root.to_str().unwrap(),
        "--morning-review",
        reference.to_str().unwrap(),
        "--owning-plan",
        plan.to_str().unwrap(),
        "--project-key",
        "project",
        "--json",
    ]);
    command
}

fn qualify_morning_return(
    home: &Path,
    root: &Path,
    plan_path: &Path,
    source: &str,
    reference_path: &Path,
) -> Result<()> {
    use forager::offdesk::workflow::ondesk_plan::{inspect, with_plan};
    let initial_journal_path = root.join(".NEXT_ACTIONS.md.forager-plan-journal.json");
    let initial_journal = if initial_journal_path.exists() {
        Some(fs::read(&initial_journal_path)?)
    } else {
        None
    };
    assert!(Command::new("git")
        .args([
            "-C",
            root.to_str().unwrap(),
            "init",
            "--initial-branch=morning"
        ])
        .output()?
        .status
        .success());
    let ledger_path = profile_dir(home).join("offdesk_shifts.v1.json");
    let ledger_before = fs::read(&ledger_path)?;
    let report_path = root.join("night-review.json");
    let report_before = fs::read(&report_path)?;
    let get = || -> Result<Value> {
        let output = morning_return_command(home, root, plan_path, reference_path).output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice::<Value>(&output.stdout)?["morning_return"].clone())
    };
    let original = get()?;
    assert_eq!(original["resume_mode"], "fresh_session");
    assert_eq!(original["session_input_status"], "not_assessed_no_delivery");
    assert_eq!(original["tasks"][0]["baseline_status"], "exact_baseline");
    assert_eq!(
        original["tasks"][0]["original_task"]["goal"],
        "bounded deterministic execution"
    );
    assert_eq!(
        original["tasks"][0]["closeout_report"]["result_observation"]["evidence"][0]
            ["verification"],
        "current_bytes_hash_match_not_claim_verification"
    );
    assert_eq!(original["current_git"]["branch"], "morning");
    assert_eq!(original["current_git"]["dirty"], true);
    assert_eq!(original["historical_git_status"], "not_preserved");
    assert_eq!(original["review_history"], json!([]));
    let branch = morning_return_command(home, root, plan_path, reference_path)
        .args(["--expected-branch", "wrong"])
        .output()?;
    assert!(!branch.status.success());
    assert!(String::from_utf8_lossy(&branch.stderr).contains("branch mismatch"));
    let branch = morning_return_command(home, root, plan_path, reference_path)
        .args(["--expected-branch", "morning"])
        .output()?;
    assert!(branch.status.success());
    fs::write(plan_path, format!("{source}\nNarrative changed.\n"))?;
    let changed = get()?;
    assert_eq!(
        changed["tasks"][0]["baseline_status"],
        "document_changed_tasks_equal"
    );
    assert_ne!(
        changed["current_plan_sha256"],
        original["current_plan_sha256"]
    );
    let mut edited = inspect(source, "project")?.plan;
    edited.tasks[0].goal = "updated current goal".into();
    let mut extra = edited.tasks[0].clone();
    extra.task_id = "new-task".into();
    edited.tasks.push(extra);
    fs::write(plan_path, with_plan(source, &edited)?)?;
    let changed = get()?;
    assert_eq!(changed["tasks"][0]["baseline_status"], "tasks_changed");
    assert_eq!(
        changed["tasks"][0]["changed_tasks"],
        json!(["task_shift_1"])
    );
    assert_eq!(changed["tasks"][0]["added_tasks"], json!(["new-task"]));
    assert_eq!(
        changed["tasks"][0]["original_task"],
        original["tasks"][0]["original_task"]
    );
    edited.tasks.remove(0);
    fs::write(plan_path, with_plan(source, &edited)?)?;
    let changed = get()?;
    assert!(changed["tasks"][0]["current_task"].is_null());
    assert_eq!(
        changed["tasks"][0]["removed_tasks"],
        json!(["task_shift_1"])
    );
    fs::write(plan_path, source)?;
    let mut other = inspect(source, "project")?.plan;
    other.project_key = "other".into();
    fs::write(
        plan_path,
        format!("```forager-plan\n{}\n```\n", serde_json::to_string(&other)?),
    )?;
    let wrong = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!wrong.status.success());
    fs::write(plan_path, source)?;
    let other_root = home.join("other-project");
    fs::create_dir(&other_root)?;
    let other_plan = other_root.join("plan.md");
    fs::write(&other_plan, source)?;
    let wrong = morning_return_command(home, &other_root, &other_plan, reference_path).output()?;
    assert!(!wrong.status.success());
    assert!(String::from_utf8_lossy(&wrong.stderr).contains("root identity changed"));
    let mut instance = forager::session::Instance::new("morning fixture", root.to_str().unwrap());
    let sessions_path = profile_dir(home).join("sessions.json");
    fs::write(&sessions_path, serde_json::to_vec(&vec![&instance])?)?;
    let sessions_before = fs::read(&sessions_path)?;
    let existing = morning_return_command(home, root, plan_path, reference_path)
        .args(["--resume-session", &instance.id])
        .output()?;
    assert!(
        existing.status.success(),
        "{}",
        String::from_utf8_lossy(&existing.stderr)
    );
    let existing: Value = serde_json::from_slice(&existing.stdout)?;
    assert_eq!(
        existing["morning_return"]["resume_mode"],
        "existing_session"
    );
    assert_eq!(
        existing["morning_return"]["session_input_status"],
        "not_assessed_no_delivery"
    );
    assert_eq!(fs::read(&sessions_path)?, sessions_before);
    instance.project_path = other_root.display().to_string();
    fs::write(&sessions_path, serde_json::to_vec(&vec![&instance])?)?;
    let wrong = morning_return_command(home, root, plan_path, reference_path)
        .args(["--resume-session", &instance.id])
        .output()?;
    assert!(!wrong.status.success());
    assert!(String::from_utf8_lossy(&wrong.stderr).contains("another workdir"));
    fs::remove_file(&sessions_path)?;
    let missing = morning_return_command(home, root, plan_path, reference_path)
        .args(["--resume-session", "missing"])
        .output()?;
    assert!(!missing.status.success());
    fs::remove_file(&report_path)?;
    let missing = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!missing.status.success());
    fs::write(&report_path, b"{}")?;
    let changed = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!changed.status.success());
    assert!(String::from_utf8_lossy(&changed.stderr).contains("artifact hash changed"));
    fs::write(&report_path, &report_before)?;
    let destination = root.join("morning-package.md");
    let saved = morning_return_command(home, root, plan_path, reference_path)
        .arg("--output")
        .arg(&destination)
        .output()?;
    assert!(saved.status.success());
    let saved: Value = serde_json::from_slice(&saved.stdout)?;
    assert_eq!(
        fs::read_to_string(&destination)?,
        saved["content"].as_str().unwrap()
    );
    #[cfg(unix)]
    {
        let limited_path = root.join("failed-return.md");
        let original = morning_return_command(home, root, plan_path, reference_path);
        let mut limited = Command::new("python3");
        limited.args(["-c","import os,resource,signal,sys; signal.signal(signal.SIGXFSZ,signal.SIG_IGN); resource.setrlimit(resource.RLIMIT_FSIZE,(128,128)); os.execv(sys.argv[1],sys.argv[1:])"])
            .arg(original.get_program()).args(original.get_args()).arg("--output").arg(&limited_path);
        for (key, value) in original.get_envs() {
            if let Some(value) = value {
                limited.env(key, value);
            } else {
                limited.env_remove(key);
            }
        }
        let failed = limited.output()?;
        assert!(!failed.status.success());
        assert!(String::from_utf8_lossy(&failed.stderr).contains("File too large"));
        assert!(
            !limited_path.exists(),
            "failed export published an incomplete package"
        );
        let retry = morning_return_command(home, root, plan_path, reference_path)
            .arg("--output")
            .arg(&limited_path)
            .output()?;
        assert!(
            retry.status.success(),
            "{}",
            String::from_utf8_lossy(&retry.stderr)
        );
    }
    let overwritten = morning_return_command(home, root, plan_path, reference_path)
        .arg("--output")
        .arg(plan_path)
        .output()?;
    assert!(!overwritten.status.success());
    assert_eq!(fs::read_to_string(plan_path)?, source);
    let mut secret = inspect(source, "project")?.plan;
    secret.tasks[0].goal = "api_key=fixture_secret_1234".into();
    fs::write(plan_path, with_plan(source, &secret)?)?;
    let redacted = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(redacted.status.success());
    assert!(!String::from_utf8_lossy(&redacted.stdout).contains("fixture_secret_1234"));
    fs::write(plan_path, source)?;
    let mut huge = inspect(source, "project")?.plan;
    huge.tasks[0].goal = "x".repeat(45_000);
    fs::write(plan_path, with_plan(source, &huge)?)?;
    let overflow_path = root.join("overflow.md");
    let overflow = morning_return_command(home, root, plan_path, reference_path)
        .arg("--output")
        .arg(&overflow_path)
        .output()?;
    assert!(!overflow.status.success());
    assert!(String::from_utf8_lossy(&overflow.stderr).contains("output budget"));
    assert!(!overflow_path.exists());
    fs::write(plan_path, source)?;
    let reference: Value = serde_json::from_slice(&fs::read(reference_path)?)?;
    let decisions = serde_json::from_value(
        json!({"request_id":"partial-return-fixture","project_key":"project",
        "baseline_sha256":inspect(source,"project")?.source_sha256,"decision_ref":"fixture:partial-review","decided_by":"fixture operator",
        "shift_review":reference,"tasks":[{"task_id":"task_shift_1","disposition":"partially_accepted",
            "evidence_refs":["fixture:reviewed-check"],"remaining_work":["finish follow-up"],"unanswered_questions":["which check remains?"]}]}),
    )?;
    let proposal =
        forager::offdesk::workflow::ondesk_review::propose(source, plan_path, decisions)?;
    forager::offdesk::workflow::ondesk_plan_store::apply(
        &proposal.change_request,
        &proposal.candidate_markdown,
        true,
    )?;
    let partial = get()?;
    assert_eq!(
        partial["review_history"][0]["decisions"]["tasks"][0]["unanswered_questions"],
        json!(["which check remains?"])
    );
    assert_eq!(
        partial["review_history"][0]["decisions"]["tasks"][0]["remaining_work"],
        json!(["finish follow-up"])
    );
    assert_eq!(
        partial["tasks"][0]["current_task"]["review_state"],
        "partially_accepted"
    );
    assert_eq!(partial["pending_decisions"], json!([]));
    let journal = root.join(".NEXT_ACTIONS.md.forager-plan-journal.json");
    let original_journal = fs::read(&journal)?;
    let mut inconsistent: Value = serde_json::from_slice(&original_journal)?;
    let review_index = inconsistent["entries"]
        .as_array()
        .unwrap()
        .iter()
        .position(|entry| entry["request"]["request_id"] == "partial-return-fixture")
        .unwrap();
    inconsistent["entries"][review_index]["request"]["review_record"]["tasks"][0]["disposition"] =
        json!("accepted");
    inconsistent["entries"][review_index]["request"]["review_record"]["tasks"][0]
        ["remaining_work"] = json!([]);
    inconsistent["entries"][review_index]["request"]["review_record"]["tasks"][0]
        ["unanswered_questions"] = json!([]);
    fs::write(&journal, serde_json::to_vec(&inconsistent)?)?;
    let refused = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("decisions do not match plan"));
    inconsistent = serde_json::from_slice(&original_journal)?;
    inconsistent["entries"][review_index]["request"]["review_record"]["tasks"][0]
        ["evidence_refs"] = json!(["missing-current-evidence"]);
    fs::write(&journal, serde_json::to_vec(&inconsistent)?)?;
    let refused = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("decisions do not match plan"));
    inconsistent = serde_json::from_slice(&original_journal)?;
    inconsistent["entries"][review_index]["request"]["review_record"]["tasks"][0]
        ["remaining_work"] = json!([]);
    fs::write(&journal, serde_json::to_vec(&inconsistent)?)?;
    let refused = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("requires remaining work"));
    fs::write(&journal, &original_journal)?;
    let mut journal_record: Value = serde_json::from_slice(&fs::read(&journal)?)?;
    journal_record["entries"][review_index]["committed"] = json!(false);
    fs::write(&journal, serde_json::to_vec(&journal_record)?)?;
    let pending = get()?;
    assert_eq!(
        pending["pending_plan_requests"],
        json!(["partial-return-fixture"])
    );
    assert_eq!(
        pending["pending_decisions"],
        json!(["resolve_pending_plan_journal_before_new_review"])
    );
    fs::write(plan_path, source)?;
    fs::write(&journal, b"{}")?;
    let invalid = morning_return_command(home, root, plan_path, reference_path).output()?;
    assert!(!invalid.status.success());
    if let Some(initial) = initial_journal {
        fs::write(&journal, initial)?;
    } else {
        fs::remove_file(journal)?;
    }
    assert_eq!(fs::read(&ledger_path)?, ledger_before);
    assert_eq!(fs::read(plan_path)?, source.as_bytes());
    assert_eq!(fs::read(report_path)?, report_before);
    Ok(())
}

#[test]
fn read_policy_probe_does_not_create_profile_or_migrate_state() -> Result<()> {
    let home = tempdir()?;
    let output = forager_command(home.path())
        .args(["offdesk", "shift", "read-policy-probe", "--json"])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["schema"], "offdesk_read_policy_report.v1");
    assert_eq!(report["creates_profile_state"], false);
    assert_eq!(report["authorizes_dispatch"], false);
    assert!(!app_dir(home.path()).exists());
    Ok(())
}

#[test]
fn read_policy_rejects_unsupported_runner_before_manifest_write() -> Result<()> {
    let home = tempdir()?;
    let root = home.path().join("project");
    let start = Utc::now() - Duration::minutes(1);
    let stop = Utc::now() + Duration::minutes(10);
    let (plan, review) = register_review_and_enqueue(home.path(), &root, start, stop)?;
    let tasks_path = profile_dir(home.path()).join("offdesk_tasks.json");
    let mut tasks: Value = serde_json::from_slice(&fs::read(&tasks_path)?)?;
    tasks[0]["runner_kind"] = json!("remote_worker");
    fs::write(&tasks_path, serde_json::to_vec(&tasks)?)?;
    let output = forager_command(home.path())
        .args([
            "offdesk",
            "shift",
            "prepare",
            &plan,
            "--review-id",
            &review,
            "--shift-id",
            "bad_runner",
            "--starts-at",
            &rfc3339(start),
            "--stops-at",
            &rfc3339(stop),
            "--task-id",
            "task_shift_1",
            "--read-policy",
            "linux-landlock-workdir-and-runtime-only",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("read policy requires every bound task to use local-background"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(!profile_dir(home.path())
        .join("offdesk_shifts.v1.json")
        .exists());
    assert!(!profile_dir(home.path())
        .join("background_runs.json")
        .exists());
    Ok(())
}

#[test]
fn removing_read_policy_invalidates_existing_arm_approval() -> Result<()> {
    if forager::offdesk::read_policy::report()["status"] != "verified" {
        return Ok(());
    }
    let home = tempdir()?;
    let root = home.path().join("project");
    let start = Utc::now() - Duration::minutes(1);
    let stop = Utc::now() + Duration::minutes(10);
    let (plan, review) = register_review_and_enqueue(home.path(), &root, start, stop)?;
    let approval = prepare_shift_and_approve_with_read_policy(
        home.path(),
        &plan,
        &review,
        "changed_policy",
        start,
        stop,
        true,
    )?;
    let ledger_path = profile_dir(home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&ledger_path)?)?;
    ledger["manifests"][0]
        .as_object_mut()
        .unwrap()
        .remove("read_policy");
    fs::write(&ledger_path, serde_json::to_vec_pretty(&ledger)?)?;
    let arm = forager_command(home.path())
        .args([
            "offdesk",
            "shift",
            "arm",
            "changed_policy",
            "--approval-id",
            &approval,
            "--json",
        ])
        .output()?;
    assert!(
        !arm.status.success(),
        "removed restriction reused stale approval"
    );
    assert!(!profile_dir(home.path())
        .join("background_runs.json")
        .exists());
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
#[ignore = "requires explicit native Codex model-call opt-in and staged credentials"]
fn ondesk_return_landlock_native_tui_reads_context_without_task_authority() -> Result<()> {
    if isolated_process::run(
        "ondesk_return_landlock_native_tui_reads_context_without_task_authority",
    )? {
        return Ok(());
    }
    anyhow::ensure!(
        std::env::var("FORAGER_RETURN_LANDLOCK_NATIVE_TRIAL").as_deref() == Ok("1"),
        "native read-policy trial requires explicit opt-in"
    );
    anyhow::ensure!(
        std::env::var_os("FORAGER_R11_EVIDENCE_DIR").is_some(),
        "preserved isolated fixture directory required"
    );
    anyhow::ensure!(
        forager::offdesk::read_policy::report()["status"] == "verified",
        "mandatory read-policy preflight failed"
    );
    qualify_bound_shift_cycle_options(true, true, true, true, true)
}

#[test]
#[cfg(target_os = "linux")]
#[ignore = "requires an explicitly authorized real Codex call and staged credentials"]
fn bound_shift_landlock_real_codex() -> Result<()> {
    anyhow::ensure!(
        std::env::var("FORAGER_LANDLOCK_CODEX_TRIAL").as_deref() == Ok("1"),
        "real Codex trial requires explicit opt-in"
    );
    let evidence = std::env::var("FORAGER_LANDLOCK_CODEX_EVIDENCE")?;
    let auth = std::env::var("FORAGER_LANDLOCK_AUTH_FILE")?;
    let codex = std::env::var("FORAGER_LANDLOCK_CODEX_BINARY")?;
    let model = std::env::var("FORAGER_LANDLOCK_CODEX_MODEL")?;
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"));
    let r9 = repo.join(".git/forager-checkpoints/20261008-read-policy-r9");
    let output = Command::new("/usr/bin/python3")
        .args(["-I", "-S"])
        .arg(repo.join("tests/support/offdesk_landlock_codex.py"))
        .args([
            "--evidence",
            &evidence,
            "--forager",
            env!("CARGO_BIN_EXE_forager"),
            "--codex",
            &codex,
            "--auth-file",
            &auth,
            "--model",
            &model,
        ])
        .arg("--r9-summary")
        .arg(r9.join("TEST_SUMMARY.json"))
        .arg("--r9-policy")
        .arg(r9.join("LIVE_READ_POLICY_PROBE.json"))
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let summary: Value =
        serde_json::from_slice(&fs::read(Path::new(&evidence).join("TEST_SUMMARY.json"))?)?;
    assert_eq!(summary["result"], "bounded_real_model_qualification_pass");
    assert_eq!(summary["temporary_auth_removed"], true);
    assert_eq!(summary["host_auth_unchanged"], true);
    Ok(())
}
