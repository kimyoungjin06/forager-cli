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
    let mut artifact_refs = vec![json!({
        "artifact_id": "report",
        "path": report_path.to_str().expect("utf-8 report path"),
        "present": true
    })];
    for index in 0..8 {
        let extra_path = artifact_dir.join(format!("extra-report-{index}.md"));
        fs::write(&extra_path, format!("# Extra Report {index}\n"))?;
        artifact_refs.push(json!({
            "artifact_id": format!("extra-report-{index}"),
            "path": extra_path.to_str().expect("utf-8 extra artifact path"),
            "present": true
        }));
    }
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
        artifact_dir.join("offdesk_decisions.jsonl"),
        format!(
            "{}\n",
            serde_json::to_string(&json!({
                "schema": "decision_record.v1",
                "decision_id": "decision-closeout",
                "project_key": "project",
                "request_id": "request-closeout",
                "task_id": "task-closeout",
                "raised_by": "council",
                "source_surface": "offdesk.council",
                "materiality": "high",
                "status": "user_pending",
                "created_at": now,
                "updated_at": now,
                "decision_request": {
                    "kind": "episode_council_continuation",
                    "summary": "Council recommends revising before accepting the run.",
                    "decision_needed": "Choose whether to revise or close out.",
                    "current_scope": "Next episode continuation only.",
                    "non_authorized_scope": [
                        "runtime dispatch",
                        "cleanup",
                        "provider retargeting",
                        "wiki promotion"
                    ]
                },
                "route": {
                    "materiality": "high",
                    "target": "user",
                    "reason": "Council returned a non-continue decision.",
                    "default_if_no_reply": "defer"
                },
                "approval_brief": {
                    "schema": "approval_brief.v1",
                    "source": "offdesk_twinpaper_autonomy_workload",
                    "recommendation": "revise",
                    "subject": "Council continuation decision",
                    "summary_lines": ["Council recommends revising before accepting the run."],
                    "scope": "Next episode continuation only; does not approve cleanup or provider retargeting.",
                    "question": "How should closeout treat this decision?"
                }
            }))?
        ),
    )?;
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
                "artifact_refs": artifact_refs,
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
    assert_eq!(report["summary"]["decision_records_scanned"], 1);
    assert_eq!(report["summary"]["open_decision_records"], 1);
    assert!(report["decision_records"]
        .as_array()
        .expect("decision records")
        .iter()
        .any(|decision| decision["record"]["decision_id"] == "decision-closeout"));
    assert!(report["open_decisions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|decision| decision["kind"] == "decision_record_review"));
    assert!(report["required_first_reads"]
        .as_array()
        .expect("first reads")
        .iter()
        .any(|read| read["path"]
            .as_str()
            .unwrap_or_default()
            .ends_with("offdesk_decisions.jsonl")));

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
    assert!(return_package.contains("## Status"));
    assert!(return_package.contains("## Decision Needed"));
    assert!(return_package.contains("Required First Reads"));
    assert!(return_package.contains("Result evidence"));
    assert!(
        return_package.contains("more first-read candidate(s) are listed in `closeout_plan.json`.")
    );
    assert!(return_package.contains("## What Changed"));
    assert!(return_package.contains("## Evidence"));
    assert!(return_package.contains("Kept review evidence"));
    assert!(
        return_package.contains("... 5 more `keep` item(s) are listed in `cleanup_manifest.json`.")
    );
    assert!(return_package.contains("Documentation Governance Recommendations"));
    assert!(return_package.contains("review_human_output_candidates"));
    assert!(return_package.contains("RETENTION_REVIEW.md"));
    assert!(return_package.contains("outputs/doc-report-2.html"));
    assert!(return_package.contains("## Next Safe Action"));
    assert!(return_package.contains("documentation_governance_review"));
    assert!(!return_package.contains("extra-report-7.md"));
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

#[test]
#[serial]
fn offdesk_closeout_prefers_prepared_repo_for_documentation_governance() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;

    let harness_dir = temp.path().join("harness");
    let run_dir = harness_dir.join("target").join("offdesk-run");
    fs::create_dir_all(&run_dir)?;
    fs::write(harness_dir.join("README.md"), "# Harness\n")?;
    fs::write(
        harness_dir.join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(harness_dir.join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        harness_dir.join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: harness overview.\n",
    )?;

    let target_repo = temp.path().join("target-repo");
    fs::create_dir_all(target_repo.join("outputs"))?;
    fs::write(target_repo.join("README.md"), "# Target Repo\n")?;
    fs::write(
        target_repo.join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(target_repo.join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        target_repo.join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: target overview.\n",
    )?;
    fs::write(
        target_repo.join("outputs").join("target-report.html"),
        "<h1>target</h1>",
    )?;

    let result_path = run_dir.join("result.json");
    let log_path = run_dir.join("runner.log");
    fs::write(&result_path, "{\"status\":\"ok\"}")?;
    fs::write(&log_path, "runner log")?;
    fs::write(
        run_dir.join("prepared_task.json"),
        serde_json::to_string_pretty(&json!({
            "project_key": "target",
            "repo": target_repo.to_str().expect("utf-8 target repo"),
            "out_dir": run_dir.to_str().expect("utf-8 run dir")
        }))?,
    )?;

    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "task-target",
                "request_id": "request-target",
                "project_key": "target",
                "status": "completed",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_tmux",
                "command": "bash run_workload.sh",
                "workdir": harness_dir.to_str().expect("utf-8 harness path"),
                "background_ticket_id": "ticket-target",
                "attempt_count": 1,
                "created_at": now,
                "updated_at": now,
                "log_artifact_path": log_path.to_str().expect("utf-8 log path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 result path")
            }
        ]))?,
    )?;
    fs::write(
        profile_dir.join("background_runs.json"),
        serde_json::to_string_pretty(&json!([
            {
                "ticket_id": "ticket-target",
                "project_key": "target",
                "request_id": "request-target",
                "task_id": "task-target",
                "runner_kind": "local_tmux",
                "phase": "completed",
                "working_dir": harness_dir.to_str().expect("utf-8 harness path"),
                "log_artifact_path": log_path.to_str().expect("utf-8 log path"),
                "result_artifact_path": result_path.to_str().expect("utf-8 result path"),
                "runtime_handle_alive": false,
                "log_artifact_present": true,
                "result_artifact_present": true
            }
        ]))?,
    )?;

    let output = forager_command(temp.path())
        .current_dir(&harness_dir)
        .args([
            "offdesk",
            "closeout",
            "--project-key",
            "target",
            "--task-id",
            "task-target",
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
    assert_eq!(
        report["documentation_governance"]["workdir"],
        target_repo.to_string_lossy().as_ref()
    );
    assert!(report["documentation_governance"]["recommendations"]
        .as_array()
        .expect("documentation governance recommendations")
        .iter()
        .any(|recommendation| recommendation["kind"] == "review_human_output_candidates"));

    let return_package_path = PathBuf::from(
        report["artifacts"]["return_package_markdown"]
            .as_str()
            .expect("return package path"),
    );
    let return_package = fs::read_to_string(return_package_path)?;
    assert!(return_package.contains("target-report.html"));
    assert!(return_package.contains(target_repo.to_str().expect("utf-8 target repo")));
    assert!(!return_package.contains("harness overview"));
    Ok(())
}
