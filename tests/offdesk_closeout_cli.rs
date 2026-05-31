use anyhow::Result;
use chrono::Utc;
use serde_json::{json, Value};
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
fn offdesk_closeout_writes_dry_run_review_packet_and_return_package() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let artifact_dir = temp.path().join("run-artifacts");
    fs::create_dir_all(&artifact_dir)?;
    let result_path = artifact_dir.join("result.json");
    let log_path = artifact_dir.join("runner.log");
    let report_path = artifact_dir.join("REPORT.md");
    fs::write(&result_path, "{\"status\":\"ok\"}")?;
    fs::write(&log_path, "runner log token=sk-secretsecretsecretsecret")?;
    fs::write(&report_path, "# Report\n")?;
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
    for index in 0..3 {
        let path = temp.path().join(format!("outputs/doc-report-{index}.html"));
        fs::create_dir_all(path.parent().expect("output parent"))?;
        fs::write(path, "x".repeat((index + 1) * 10))?;
    }

    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task-closeout",
                "request_id": "request-closeout",
                "project_key": "project",
                "status": "completed",
                "capability_id": "inspect.status",
                "runner_kind": "local_tmux",
                "command": "echo token=sk-secretsecretsecretsecret",
                "workdir": temp.path().to_str().expect("utf-8 temp path"),
                "background_ticket_id": "ticket-closeout",
                "attempt_count": 1,
                "created_at": now,
                "updated_at": now,
                "artifact_refs": [
                    {
                        "artifact_id": "report",
                        "path": report_path.to_str().expect("utf-8 report path"),
                        "present": true
                    }
                ],
                "preview": "preview token=sk-secretsecretsecretsecret",
                "reason": "closeout test",
                "log_artifact_path": log_path.to_str().expect("utf-8 log path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 result path")
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket-closeout",
                "project_key": "project",
                "request_id": "request-closeout",
                "task_id": "task-closeout",
                "runner_kind": "local_tmux",
                "phase": "completed",
                "working_dir": temp.path().to_str().expect("utf-8 temp path"),
                "log_artifact_path": log_path.to_str().expect("utf-8 log path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 result path"),
                "runtime_handle_alive": false,
                "log_artifact_present": true,
                "result_artifact_present": true
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "offdesk",
            "closeout",
            "--project-key",
            "project",
            "--dry-run",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(report["dry_run"], true);
    assert_eq!(report["read_only_project_state"], true);
    assert_eq!(report["review_contract"]["required"], true);
    assert_eq!(report["summary"]["delete_candidates"], 0);
    assert!(report["summary"]["archive_candidates"].as_u64().unwrap() >= 1);
    assert!(
        report["documentation_governance"]["recommendation_count"]
            .as_u64()
            .expect("documentation governance recommendation count")
            >= 1
    );
    assert!(report["documentation_governance"]["recommendations"]
        .as_array()
        .expect("documentation governance recommendations")
        .iter()
        .any(|recommendation| recommendation["kind"] == "review_human_output_candidates"));
    assert!(report["verification_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(|command| command
            .as_str()
            .unwrap()
            .contains("forager project audit-docs")));
    assert!(report["open_decisions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|decision| decision["kind"] == "documentation_governance_review"));

    let plan_path = PathBuf::from(
        report["artifacts"]["closeout_plan_json"]
            .as_str()
            .expect("plan path"),
    );
    let manifest_path = PathBuf::from(
        report["artifacts"]["cleanup_manifest_json"]
            .as_str()
            .expect("manifest path"),
    );
    let review_packet_path = PathBuf::from(
        report["artifacts"]["commercial_review_packet"]
            .as_str()
            .expect("review packet path"),
    );
    let return_package_path = PathBuf::from(
        report["artifacts"]["return_package_markdown"]
            .as_str()
            .expect("return package path"),
    );
    assert!(plan_path.exists());
    assert!(manifest_path.exists());
    assert!(review_packet_path.exists());
    assert!(return_package_path.exists());

    let plan = fs::read_to_string(plan_path)?;
    assert!(plan.contains("[REDACTED]"));
    assert!(!plan.contains("sk-secretsecretsecretsecret"));

    let manifest: Value = serde_json::from_str(&fs::read_to_string(manifest_path)?)?;
    let operations = manifest.as_array().expect("manifest array");
    assert!(operations
        .iter()
        .any(|operation| operation["operation"] == "archive_candidate"
            && operation["requires_commercial_review"] == true
            && operation["requires_human_approval"] == true));
    assert!(!operations
        .iter()
        .any(|operation| operation["operation"] == "delete_candidate"));

    let review_packet = fs::read_to_string(review_packet_path)?;
    assert!(review_packet.contains("Commercial Model Closeout Review Packet"));
    assert!(review_packet.contains("\"verdict\": \"approved|revise|blocked\""));

    let return_package = fs::read_to_string(return_package_path)?;
    assert!(return_package.contains("Ondesk Return Package"));
    assert!(return_package.contains("Required First Reads"));
    assert!(return_package.contains("Documentation Governance Recommendations"));
    assert!(return_package.contains("review_human_output_candidates"));
    assert!(return_package.contains("RETENTION_REVIEW.md"));
    assert!(return_package.contains("outputs/doc-report-2.html"));
    assert!(result_path.exists());
    assert!(log_path.exists());

    let review_output = forager_command(temp.path())
        .args([
            "offdesk",
            "closeout-review",
            "--closeout-id",
            report["closeout_id"].as_str().expect("closeout id"),
            "--verdict",
            "approved",
            "--reviewer",
            "gpt-5.5",
            "--review-provider",
            "gpt-5.5",
            "--review-file",
            report["artifacts"]["commercial_review_packet"]
                .as_str()
                .expect("review packet"),
            "--unsafe-operation",
            "none",
            "--required-first-read",
            result_path.to_str().expect("utf-8 result path"),
            "--notes",
            "approved after review token=sk-secretsecretsecretsecret",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "{}",
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["verdict"], "approved");
    assert_eq!(review["read_only_project_state"], true);
    assert_eq!(review["applies_file_operations"], false);
    assert_eq!(review["closeout_id"], report["closeout_id"]);
    assert_eq!(review["applies_to_task_ids"][0], "task-closeout");
    assert_eq!(review["applies_to_tasks"][0]["project_key"], "project");
    assert_eq!(review["applies_to_tasks"][0]["task_id"], "task-closeout");
    assert!(review["notes"].as_str().unwrap().contains("[REDACTED]"));
    assert!(!review["notes"]
        .as_str()
        .unwrap()
        .contains("sk-secretsecretsecretsecret"));
    let review_record_path = PathBuf::from(
        review["artifacts"]["review_record_json"]
            .as_str()
            .expect("review record path"),
    );
    assert!(review_record_path.exists());
    Ok(())
}
