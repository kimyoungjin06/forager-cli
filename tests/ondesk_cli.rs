use anyhow::Result;
use chrono::{Duration, Utc};
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

fn offdesk_task_fixture(
    task_id: &str,
    status: &str,
    now: chrono::DateTime<Utc>,
) -> serde_json::Value {
    json!({
        "task_id": task_id,
        "request_id": format!("request-{task_id}"),
        "project_key": "project",
        "status": status,
        "capability_id": "dispatch.runtime",
        "runner_kind": "local_background",
        "command": "true",
        "workdir": "/tmp",
        "created_at": now,
        "updated_at": now
    })
}

#[test]
#[serial]
fn ondesk_note_json_redacts_and_persists_note() -> Result<()> {
    let temp = tempdir()?;
    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "note",
            "--project-key",
            "twinpaper",
            "--mode",
            "writing",
            "--text",
            "draft note token=sk-secretsecretsecretsecret",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["project_key"], "twinpaper");
    assert_eq!(json["mode"], "writing");

    let notes = fs::read_to_string(profile_dir(temp.path()).join("ondesk_notes.jsonl"))?;
    assert!(notes.contains("twinpaper"));
    assert!(notes.contains("writing"));
    assert!(notes.contains("[REDACTED]"));
    assert!(!notes.contains("sk-secretsecretsecretsecret"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_uses_recent_redacted_notes() -> Result<()> {
    let temp = tempdir()?;
    let note_output = forager_command(temp.path())
        .args([
            "ondesk",
            "note",
            "--project-key",
            "research",
            "--mode",
            "analysis",
            "--text",
            "compare evidence before claim token=sk-secretsecretsecretsecret",
        ])
        .output()?;
    assert!(
        note_output.status.success(),
        "{}",
        String::from_utf8_lossy(&note_output.stderr)
    );

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "research",
            "--mode",
            "analysis",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["project_key"], "research");
    assert_eq!(json["note_count"], 1);
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("compare evidence before claim"));
    assert!(content.contains("Instructions For The Next Harness"));
    assert!(content.contains("[REDACTED]"));
    assert!(!content.contains("sk-secretsecretsecretsecret"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_review_surface_json_agrees_with_status_next_safe_action() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            offdesk_task_fixture("approval-task", "pending_approval", now),
            offdesk_task_fixture("completed-task", "completed", now),
            offdesk_task_fixture("running-task", "running", now)
        ]))?,
    )?;

    let status_output = forager_command(temp.path())
        .args(["status", "--json"])
        .output()?;
    assert!(
        status_output.status.success(),
        "{}",
        String::from_utf8_lossy(&status_output.stderr)
    );
    let status_json: Value = serde_json::from_slice(&status_output.stdout)?;

    let surface_output = forager_command(temp.path())
        .args([
            "ondesk",
            "review-surface",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(
        surface_output.status.success(),
        "{}",
        String::from_utf8_lossy(&surface_output.stderr)
    );
    let surface: Value = serde_json::from_slice(&surface_output.stdout)?;

    assert_eq!(surface["schema"], "review_surface.v1");
    assert_eq!(surface["project_key"], "project");
    assert_eq!(surface["status"]["label"], "needs_review");
    assert_eq!(
        surface["next_safe_actions"][0],
        status_json["offdesk_next_safe_actions"][0]
    );
    assert_eq!(surface["sources"]["status_json"], "forager status --json");
    assert_eq!(surface["sources"]["artifact_index"], "artifact_index.v1");
    assert_eq!(
        surface["sources"]["artifact_retention_review"],
        "artifact_retention_review.v1"
    );
    assert_eq!(surface["artifacts"]["index"]["schema"], "artifact_index.v1");
    assert_eq!(
        surface["artifacts"]["retention_review"]["schema"],
        "artifact_retention_review.v1"
    );
    Ok(())
}

#[test]
#[serial]
fn ondesk_review_surface_default_output_is_human_summary() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([offdesk_task_fixture(
            "approval-task",
            "pending_approval",
            now
        )]))?,
    )?;

    let output = forager_command(temp.path())
        .args(["ondesk", "review-surface", "--project-key", "project"])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout)?;
    assert!(stdout.contains("Morning Review Surface"));
    assert!(stdout.contains("status: needs_review"));
    assert!(stdout.contains("accepted truth:"));
    assert!(stdout.contains("next safe actions:"));
    assert!(stdout.contains("refs: use --json for audit paths"));
    assert!(!stdout.contains(profile_dir.to_string_lossy().as_ref()));
    Ok(())
}

#[test]
#[serial]
fn ondesk_review_surface_summarizes_closeout_receipt_before_paths() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    fs::create_dir_all(&profile_dir)?;
    let now = Utc::now();
    let task_updated = now - Duration::minutes(10);
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([offdesk_task_fixture(
            "completed-task",
            "completed",
            task_updated
        )]))?,
    )?;

    let closeout_dir = profile_dir
        .join("offdesk_closeouts")
        .join("20260601T000000Z_closeout_project");
    fs::create_dir_all(&closeout_dir)?;
    let return_package_path = closeout_dir.join("RETURN_PACKAGE.md");
    let receipt_path = closeout_dir.join("closeout_receipt_20260601T000000Z.json");
    fs::write(&return_package_path, "# Ondesk Return Package\n")?;
    fs::write(
        closeout_dir.join("closeout_plan.json"),
        serde_json::to_string_pretty(&json!({
            "closeout_id": "closeout_project",
            "generated_at": now,
            "filters": {
                "project_key": "project"
            },
            "tasks": [
                {
                    "project_key": "project",
                    "request_id": "request-completed-task",
                    "task_id": "completed-task"
                }
            ],
            "implementation_packet_coverage": {
                "packet_count": 1,
                "completed": 1,
                "deferred": 0,
                "missing": 0,
                "drifted": 0,
                "detail_items": 3,
                "detail_items_completed": 1,
                "detail_items_deferred": 0,
                "detail_items_missing": 1,
                "detail_items_drifted": 1,
                "items": [
                    {
                        "packet_id": "packet-closeout-project",
                        "project_key": "project",
                        "goal": "Return packet coverage to Ondesk.",
                        "success_state": "Missing packet evidence remains visible.",
                        "goal_status": "completed",
                        "reason": "Execution evidence exists; closeout review still applies.",
                        "detail_source": "implementation_packet",
                        "work_slices": [
                            {
                                "category": "work_slice",
                                "label": "receipt drifted slice",
                                "status": "drifted",
                                "reason": "Work-slice execution receipt reports drifted.",
                                "evidence_refs": ["slice-drift-log.txt"],
                                "summary": "The worker changed the slice boundary.",
                                "next_safe_action": "Revise the packet before accepting truth."
                            }
                        ],
                        "validation_items": [
                            {
                                "category": "validation_test",
                                "label": "missing-validation.txt",
                                "status": "missing",
                                "reason": "No closeout artifact matched this item.",
                                "evidence_refs": []
                            }
                        ],
                        "expected_artifacts": [
                            {
                                "category": "expected_artifact",
                                "label": "result.json",
                                "status": "completed",
                                "reason": "Closeout evidence matched this item.",
                                "evidence_refs": ["task:completed-task:result:result.json"]
                            }
                        ]
                    }
                ]
            },
            "artifacts": {
                "return_package_markdown": return_package_path
            }
        }))?,
    )?;
    let receipt = json!({
        "schema": "closeout_receipt.v1",
        "receipt_id": "receipt-followup",
        "closeout_id": "closeout_project",
        "review_id": "review-followup",
        "acceptance_status": "approved_with_followups",
        "verification_status": "pending",
        "open_decisions": [
            {
                "kind": "operator_followup",
                "detail": "Confirm whether to promote the wiki lesson.",
                "suggested_command": "forager offdesk wiki review"
            }
        ],
        "missing_evidence": ["verification screenshot"],
        "required_first_reads": [],
        "unsafe_operations": [],
        "retention_review": "not_required",
        "wiki_promotion_state": "not_required",
        "stale_task_count": 0,
        "next_safe_action": "Review missing evidence before acceptance."
    });
    fs::write(&receipt_path, serde_json::to_string_pretty(&receipt)?)?;
    fs::write(
        closeout_dir.join("closeout_review_20260601T000000Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": now,
            "verdict": "approved",
            "applies_to_tasks": [
                {
                    "project_key": "project",
                    "request_id": "request-completed-task",
                    "task_id": "completed-task"
                }
            ],
            "closeout_receipt": receipt,
            "artifacts": {
                "closeout_receipt_json": receipt_path
            }
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "review-surface",
            "--project-key",
            "project",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let surface: Value = serde_json::from_slice(&output.stdout)?;

    assert_eq!(surface["accepted_truth"]["status"], "pending");
    assert_eq!(
        surface["accepted_truth"]["receipt_acceptance_status"],
        "approved_with_followups"
    );
    assert_eq!(
        surface["accepted_truth"]["reason"],
        "Review missing evidence before acceptance."
    );
    assert_eq!(surface["closeout"]["latest_receipt_id"], "receipt-followup");
    assert_eq!(
        surface["closeout"]["review_status"],
        "approved_with_followups"
    );
    assert_eq!(
        surface["closeout"]["implementation_packet_coverage"]["packet_count"],
        1
    );
    assert_eq!(
        surface["closeout"]["implementation_packet_coverage"]["detail_items_missing"],
        1
    );
    assert!(
        surface["closeout"]["implementation_packet_coverage"]["items"][0]["validation_items"]
            .as_array()
            .expect("coverage validation items")
            .iter()
            .any(|item| item["label"] == "missing-validation.txt" && item["status"] == "missing")
    );
    assert!(surface["closeout"]["unresolved_risks"]
        .as_array()
        .expect("unresolved risks")
        .iter()
        .any(|risk| risk
            .as_str()
            .expect("risk string")
            .contains("missing evidence")));

    let summaries = surface["artifacts"]["summary"]
        .as_array()
        .expect("artifact summaries");
    assert!(summaries
        .iter()
        .any(|summary| summary["label"] == "Closeout receipt"
            && summary["retention_class"] == "acceptance"));
    for summary in summaries {
        assert!(
            !summary
                .to_string()
                .contains(closeout_dir.to_string_lossy().as_ref()),
            "artifact summary should explain meaning before exposing paths: {summary}"
        );
    }
    assert!(surface["artifacts"]["refs"]
        .as_array()
        .expect("artifact refs")
        .iter()
        .any(|reference| reference["id"] == "closeout_receipt" && reference["present"] == true));
    let artifact_index = &surface["artifacts"]["index"];
    assert_eq!(artifact_index["schema"], "artifact_index.v1");
    assert!(
        artifact_index["summary"]["total_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 3
    );
    assert!(artifact_index["entries"]
        .as_array()
        .expect("artifact index entries")
        .iter()
        .any(|entry| entry["kind"] == "closeout_receipt"
            && entry["label"] == "Closeout receipt"
            && entry["present"] == true));
    let retention_review = &surface["artifacts"]["retention_review"];
    assert_eq!(retention_review["schema"], "artifact_retention_review.v1");
    assert!(
        retention_review["summary"]["total_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 3
    );
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_includes_latest_offdesk_return_package() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let closeout_dir = profile_dir
        .join("offdesk_closeouts")
        .join("20260521T000000Z_closeout_test");
    fs::create_dir_all(&closeout_dir)?;
    let return_package_path = closeout_dir.join("RETURN_PACKAGE.md");
    fs::write(
        &return_package_path,
        "# Ondesk Return Package\n\nNight result summary token=sk-secretsecretsecretsecret\n",
    )?;
    let generated_at = Utc::now();
    fs::write(
        closeout_dir.join("closeout_plan.json"),
        serde_json::to_string_pretty(&json!({
            "closeout_id": "closeout_test",
            "generated_at": generated_at,
            "filters": {
                "project_key": "twinpaper"
            },
            "tasks": [
                {
                    "project_key": "twinpaper",
                    "request_id": "request",
                    "task_id": "task"
                }
            ],
            "implementation_packet_coverage": {
                "packet_count": 1,
                "completed": 1,
                "deferred": 0,
                "missing": 0,
                "drifted": 0,
                "detail_items": 3,
                "detail_items_completed": 1,
                "detail_items_deferred": 0,
                "detail_items_missing": 1,
                "detail_items_drifted": 1,
                "items": [
                    {
                        "packet_id": "packet-twinpaper",
                        "project_key": "twinpaper",
                        "goal": "Return closeout packet coverage to Ondesk.",
                        "success_state": "Ondesk prompt shows missing packet details.",
                        "goal_status": "completed",
                        "reason": "Execution evidence exists; closeout review still applies.",
                        "detail_source": "implementation_packet",
                        "work_slices": [
                            {
                                "category": "work_slice",
                                "label": "receipt drifted slice",
                                "status": "drifted",
                                "reason": "Work-slice execution receipt reports drifted.",
                                "evidence_refs": ["slice-drift-log.txt"],
                                "summary": "The worker changed the slice boundary.",
                                "next_safe_action": "Revise the packet before accepting truth."
                            }
                        ],
                        "validation_items": [
                            {
                                "category": "validation_test",
                                "label": "missing-validation.txt",
                                "status": "missing",
                                "reason": "No closeout artifact matched this item.",
                                "evidence_refs": []
                            }
                        ],
                        "expected_artifacts": [
                            {
                                "category": "expected_artifact",
                                "label": "result.json",
                                "status": "completed",
                                "reason": "Closeout evidence matched this item.",
                                "evidence_refs": ["task:task:result:result.json"]
                            }
                        ]
                    }
                ]
            },
            "artifacts": {
                "return_package_markdown": return_package_path
            }
        }))?,
    )?;
    fs::write(
        closeout_dir.join("closeout_review_20260521T000000Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": generated_at,
            "verdict": "approved",
            "closeout_receipt": {
                "schema": "closeout_receipt.v1",
                "acceptance_status": "approved_with_followups"
            },
            "artifacts": {
                "closeout_receipt_json": closeout_dir.join("closeout_receipt_20260521T000000Z.json")
            }
        }))?,
    )?;
    let decision_record = json!({
        "schema": "decision_record.v1",
        "decision_id": "decision-council-route",
        "project_key": "twinpaper",
        "request_id": "request-council-route",
        "task_id": "task-council-route",
        "raised_by": "council",
        "source_surface": "offdesk.council",
        "materiality": "medium",
        "status": "auto_resolved",
        "created_at": generated_at,
        "updated_at": generated_at,
        "decision_request": {
            "kind": "episode_council_continuation",
            "summary": "Council selected the next safe continuation path.",
            "decision_needed": "Record why this route was not escalated further.",
            "current_scope": "Next episode continuation only.",
            "non_authorized_scope": ["cleanup", "provider retargeting"]
        },
        "judgment_route": {
            "schema": "judgment_route.v1",
            "evaluator": "council",
            "reason": "Council compared reviewer outputs and found the decision stayed inside the approved scope.",
            "policy_basis": ["read-only council checkpoint"],
            "evidence_refs": [],
            "selected_by": "offdesk.council",
            "selected_at": generated_at,
            "default_if_no_reply": "continue"
        }
    });
    fs::write(
        profile_dir.join("offdesk_decisions.jsonl"),
        format!("{}\n", serde_json::to_string(&decision_record)?),
    )?;

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "twinpaper",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["review_surface"]["schema"], "review_surface.v1");
    assert_eq!(
        json["review_surface"]["accepted_truth"]["receipt_acceptance_status"],
        "approved_with_followups"
    );
    assert_eq!(json["latest_closeout"]["closeout_id"], "closeout_test");
    assert_eq!(json["latest_closeout"]["review_verdict"], "approved");
    assert_eq!(
        json["latest_closeout"]["receipt_status"],
        "approved_with_followups"
    );
    assert_eq!(
        json["documentation_governance"]["source"],
        "latest_closeout_return_package"
    );
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("Morning Review Surface"));
    assert!(content.contains("accepted_truth: pending via closeout_receipt.v1"));
    assert!(content.contains("receipt_acceptance_status: approved_with_followups"));
    assert!(content.contains("closeout_implementation_packet_coverage:"));
    assert!(content.contains("detail_items: 1 completed, 0 deferred, 1 missing, 1 drifted"));
    assert!(content.contains("packet packet-twinpaper: completed"));
    assert!(content.contains(
        "work_slices: [drifted] receipt drifted slice (next: Revise the packet before accepting truth.)"
    ));
    assert!(content.contains("validation_items: [missing] missing-validation.txt"));
    assert!(content.contains("judgment_routes:"));
    assert!(content.contains("decision-council-route: council"));
    assert!(content.contains("Council compared reviewer outputs"));
    assert!(content.contains("artifact_index:"));
    assert!(content.contains("retention_review:"));
    assert!(content.contains("artifact_summaries"));
    assert!(content.contains("Documentation Governance Source"));
    assert!(content.contains("source: `latest_closeout_return_package`"));
    assert!(content.contains("Latest Offdesk Return Package"));
    assert!(content.contains("Night result summary"));
    assert!(content.contains("review_verdict: approved"));
    assert!(content.contains("closeout_receipt_status: approved_with_followups"));
    assert!(content.contains("closeout_acceptance: not accepted truth"));
    assert!(content.contains("review receipt follow-ups"));
    assert!(content.contains("[REDACTED]"));
    assert!(!content.contains("sk-secretsecretsecretsecret"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_can_include_fresh_documentation_audit_without_closeout() -> Result<()> {
    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir_all(project.join("outputs"))?;
    fs::write(project.join("README.md"), "# Project\n")?;
    fs::write(
        project.join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(project.join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        project.join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: project overview.\n",
    )?;
    fs::write(
        project.join("outputs").join("unpromoted-report.html"),
        "<h1>report</h1>",
    )?;

    let output = forager_command(temp.path())
        .current_dir(&project)
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "project",
            "--include-doc-audit",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["documentation_governance"]["source"],
        "fresh_project_audit"
    );
    assert_eq!(
        json["documentation_governance"]["requested_fresh_audit"],
        true
    );
    assert!(
        json["documentation_governance"]["recommendation_count"]
            .as_u64()
            .expect("recommendation count")
            >= 1
    );
    assert_eq!(
        json["documentation_governance"]["recommendations"][0]["kind"],
        "review_human_output_candidates"
    );
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("Documentation Governance Source"));
    assert!(content.contains("source: `fresh_project_audit`"));
    assert!(content.contains("review_human_output_candidates"));
    assert!(content.contains("outputs/unpromoted-report.html"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_prefers_closeout_workdir_for_fresh_documentation_audit() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let closeout_dir = profile_dir
        .join("offdesk_closeouts")
        .join("20260522T000000Z_closeout_twinpaper");
    fs::create_dir_all(&closeout_dir)?;

    let target_project = temp.path().join("twinpaper_project");
    fs::create_dir_all(target_project.join("outputs"))?;
    fs::write(target_project.join("README.md"), "# TwinPaper\n")?;
    fs::write(
        target_project.join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(target_project.join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        target_project.join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: project overview.\n",
    )?;
    fs::write(
        target_project.join("outputs").join("target-report.html"),
        "<h1>target</h1>",
    )?;

    let harness_cwd = temp.path().join("harness_cwd");
    fs::create_dir_all(harness_cwd.join("outputs"))?;
    fs::write(harness_cwd.join("README.md"), "# Harness\n")?;
    fs::write(
        harness_cwd.join("PROJECT_STATE.md"),
        format!("# Project State\n\nUpdated: {}\n", Utc::now().date_naive()),
    )?;
    fs::write(harness_cwd.join("DECISIONS.md"), "# Decisions\n")?;
    fs::write(
        harness_cwd.join("DELIVERABLES.md"),
        "# Deliverables\n\n- `README.md`: project overview.\n",
    )?;
    fs::write(
        harness_cwd.join("outputs").join("harness-report.html"),
        "<h1>harness</h1>",
    )?;

    let return_package_path = closeout_dir.join("RETURN_PACKAGE.md");
    fs::write(&return_package_path, "# Ondesk Return Package\n")?;
    fs::write(
        closeout_dir.join("closeout_plan.json"),
        serde_json::to_string_pretty(&json!({
            "closeout_id": "closeout_twinpaper",
            "generated_at": Utc::now(),
            "filters": {
                "project_key": "twinpaper"
            },
            "documentation_governance": {
                "workdir": target_project.to_str().expect("utf-8 target path"),
                "audit_profile": "standard"
            },
            "tasks": [
                {
                    "project_key": "twinpaper",
                    "request_id": "request",
                    "task_id": "task",
                    "workdir": harness_cwd.to_str().expect("utf-8 harness path")
                }
            ],
            "artifacts": {
                "return_package_markdown": return_package_path
            }
        }))?,
    )?;

    let output = forager_command(temp.path())
        .current_dir(&harness_cwd)
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "twinpaper",
            "--include-doc-audit",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["documentation_governance"]["project_path"],
        target_project.to_string_lossy().as_ref()
    );
    assert_eq!(
        json["documentation_governance"]["source"],
        "fresh_project_audit"
    );
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("target-report.html"));
    assert!(!content.contains("harness-report.html"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_includes_latest_project_initialization() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let init_dir = profile_dir
        .join("project_initializations")
        .join("20260521T000000Z_twinpaper");
    fs::create_dir_all(&init_dir)?;
    let generated_at = Utc::now();
    let start_package_path = init_dir.join("ONDESK_START_PACKAGE.md");
    let ready_check_path = init_dir.join("OFFDESK_READY_CHECK.json");
    let module_preflight_path = init_dir.join("MODULE_OPERATION_PREFLIGHT.json");
    fs::write(
        &start_package_path,
        "# Ondesk Start Package\n\nRead Module03 first token=sk-secretsecretsecretsecret\n",
    )?;
    fs::write(
        &ready_check_path,
        serde_json::to_string_pretty(&json!({
            "ready_for_ondesk_start": true,
            "ready_for_offdesk_runtime": false,
            "requires_operator_review": true
        }))?,
    )?;
    fs::write(
        &module_preflight_path,
        serde_json::to_string_pretty(&json!({
            "kind": "forager_module_operation_preflight",
            "ready_for_offdesk_runtime": false,
            "blockers": [
                "operator_review_required_before_runtime_enqueue",
                "module_operation_profile_requires_review"
            ],
            "operation_targets": [
                {
                    "scope_ref": "module03_regspec_machine",
                    "readiness_level": "known_profile_builder_available",
                    "recognized_profile_kind": "twinpaper_module03_regspec_machine",
                    "profile_builder_available": true,
                    "evidence_bundle_builder_available": true,
                    "evidence_review_builder_available": true,
                    "blockers": ["evidence_bundle_requires_review"],
                    "recommended_commands": [
                        {
                            "purpose": "build_evidence_bundle",
                            "command": "scripts/build_bundle.py --token sk-secretsecretsecretsecret"
                        },
                        {
                            "purpose": "review_evidence_bundle",
                            "command": "scripts/review_bundle.py"
                        }
                    ]
                }
            ]
        }))?,
    )?;
    fs::write(
        init_dir.join("PROJECT_OPERATION_PROFILE.json"),
        serde_json::to_string_pretty(&json!({
            "kind": "forager_project_operation_profile",
            "id": "project-init-test",
            "generated_at": generated_at,
            "project_key": "twinpaper",
            "scope_model": {
                "operation_targets": [
                    {
                        "scope_ref": "module03_regspec_machine",
                        "role": "module_operation_target"
                    }
                ]
            },
            "ondesk_start_package_path": start_package_path,
            "offdesk_ready_check_path": ready_check_path,
            "module_operation_preflight_path": module_preflight_path
        }))?,
    )?;

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "twinpaper",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["latest_project_initialization"]["initialization_id"],
        "project-init-test"
    );
    assert_eq!(
        json["latest_project_initialization"]["operation_targets"][0],
        "module03_regspec_machine"
    );
    assert_eq!(
        json["latest_project_initialization"]["ready_for_ondesk_start"],
        true
    );
    assert_eq!(
        json["latest_project_initialization"]["ready_for_offdesk_runtime"],
        false
    );
    assert_eq!(
        json["latest_project_initialization"]["module_operation_preflight"]
            ["ready_for_offdesk_runtime"],
        false
    );
    assert_eq!(
        json["latest_project_initialization"]["module_operation_preflight"]["blocker_count"],
        2
    );
    assert_eq!(
        json["latest_project_initialization"]["module_operation_preflight"]["operation_targets"][0]
            ["readiness_level"],
        "known_profile_builder_available"
    );
    assert_eq!(
        json["latest_project_initialization"]["module_operation_preflight"]["operation_targets"][0]
            ["recognized_profile_kind"],
        "twinpaper_module03_regspec_machine"
    );
    assert_eq!(
        json["latest_project_initialization"]["module_operation_preflight"]["operation_targets"][0]
            ["recommended_command_purposes"][0],
        "build_evidence_bundle"
    );
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("Latest Project Initialization"));
    assert!(content.contains("Latest Module Operation Preflight"));
    assert!(content.contains("module03_regspec_machine"));
    assert!(content.contains("known_profile_builder_available"));
    assert!(content.contains("build_evidence_bundle"));
    assert!(content.contains("Read Module03 first"));
    assert!(content.contains("[REDACTED]"));
    assert!(!content.contains("sk-secretsecretsecretsecret"));
    assert!(!content.contains("scripts/build_bundle.py"));
    Ok(())
}

#[test]
#[serial]
fn ondesk_prompt_package_includes_latest_implementation_packet() -> Result<()> {
    let temp = tempdir()?;
    let profile_dir = profile_dir(temp.path());
    let packet_dir = profile_dir
        .join("implementation_packets")
        .join("20260603T000000Z_twinpaper");
    fs::create_dir_all(&packet_dir)?;
    let generated_at = Utc::now();
    let packet = json!({
        "schema": "implementation_packet.v1",
        "packet_id": "implementation-packet-test",
        "created_at": generated_at,
        "project_key": "twinpaper",
        "project_root": temp.path().display().to_string(),
        "source_intent": {
            "user_goal": "Project the implementation packet into Ondesk surfaces.",
            "why_now": "The packet is not useful until a returning operator can see it.",
            "success_state": "The prompt package explains delegation readiness without opening raw JSON."
        },
        "alignment": {
            "north_star_fit": "Keeps implementation anchored to the user's original intent.",
            "brand_fit": "Supports design-first harness work.",
            "product_boundary": "Read-only projection; no runtime authority is granted.",
            "anti_drift_notes": ["Do not treat execution as accepted truth."]
        },
        "scope": {
            "included": ["review_surface projection", "ondesk prompt rendering"],
            "excluded": ["runtime launch authority"],
            "allowed_files": ["src/cli/review_surface.rs", "src/cli/ondesk.rs"],
            "mutation_boundary": "Only prompt and review-surface summary fields are changed.",
            "non_authorized_actions": ["Do not enqueue offdesk work."]
        },
        "capability_mapping": [
            {
                "capability_id": "FD-016",
                "reason": "Ondesk handoff should surface planning state."
            }
        ],
        "design": {
            "approach": "Summarize the latest packet by project key.",
            "work_slices": ["scan packet directory", "render prompt section"],
            "interfaces": ["review_surface.v1", "ondesk prompt-package"],
            "data_contracts": ["implementation_packet.v1"],
            "compatibility_notes": ["Missing packet means the field is omitted."]
        },
        "execution": {
            "preferred_worker": "hosted_harness",
            "worker_requirements": ["read-only packet projection"],
            "commands": ["cargo test --test ondesk_cli ondesk_prompt_package_includes_latest_implementation_packet"],
            "stop_conditions": ["implementation packet is not parseable"],
            "rollback_or_recovery": ["remove the optional prompt section"]
        },
        "validation": {
            "tests": ["cargo test --test ondesk_cli ondesk_prompt_package_includes_latest_implementation_packet"],
            "smoke_checks": ["forager ondesk prompt-package --project-key twinpaper --json"],
            "manual_review": ["confirm prompt content carries user-facing decision context"],
            "evidence_required": ["prompt package JSON contains implementation_packet"]
        },
        "closeout": {
            "expected_artifacts": ["IMPLEMENTATION_PACKET.json", "RECURSIVE_ALIGNMENT_REVIEW.json", "IMPLEMENTATION_PACKET.md"],
            "accepted_truth_rule": "Execution completion is not acceptance.",
            "handoff_summary_requirements": ["state safe_to_delegate and required revisions"]
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
        "# Implementation Packet\n\nProject the implementation packet into Ondesk surfaces.\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "ondesk",
            "prompt-package",
            "--project-key",
            "twinpaper",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["review_surface"]["implementation_packet"]["packet_id"],
        "implementation-packet-test"
    );
    assert_eq!(
        json["review_surface"]["implementation_packet"]["outcome"],
        "pass"
    );
    assert_eq!(
        json["review_surface"]["implementation_packet"]["safe_to_delegate"],
        true
    );
    assert_eq!(
        json["review_surface"]["implementation_packet"]["work_slice_count"],
        2
    );
    assert_eq!(
        json["review_surface"]["sources"]["implementation_packet"],
        "implementation_packet.v1"
    );
    let content = json["content"].as_str().expect("content string");
    assert!(content.contains("implementation_packet:"));
    assert!(content.contains("implementation-packet-test"));
    assert!(content.contains("safe_to_delegate=true"));
    assert!(content.contains("worker=hosted_harness"));
    assert!(content.contains("Project the implementation packet into Ondesk surfaces."));
    Ok(())
}

#[test]
#[serial]
fn ondesk_capture_writes_artifacts_without_requiring_running_tmux() -> Result<()> {
    let temp = tempdir()?;
    let project = temp.path().join("project");
    fs::create_dir_all(&project)?;

    let add_output = forager_command(temp.path())
        .args([
            "add",
            project.to_str().expect("utf-8 project path"),
            "--title",
            "codex-harness",
            "--cmd",
            "codex",
        ])
        .output()?;
    assert!(
        add_output.status.success(),
        "{}",
        String::from_utf8_lossy(&add_output.stderr)
    );

    let capture_output = forager_command(temp.path())
        .args([
            "ondesk",
            "capture",
            "codex-harness",
            "--project-key",
            "project",
            "--lines",
            "50",
            "--json",
        ])
        .output()?;
    assert!(
        capture_output.status.success(),
        "{}",
        String::from_utf8_lossy(&capture_output.stderr)
    );
    let json: Value = serde_json::from_slice(&capture_output.stdout)?;
    assert_eq!(json["project_key"], "project");
    assert_eq!(json["session_running"], false);

    let capture_path = PathBuf::from(json["capture_path"].as_str().expect("capture path"));
    let prompt_path = PathBuf::from(
        json["prompt_package_path"]
            .as_str()
            .expect("prompt package path"),
    );
    assert!(capture_path.exists());
    assert!(prompt_path.exists());

    let capture: Value = serde_json::from_str(&fs::read_to_string(capture_path)?)?;
    assert_eq!(capture["session"]["title"], "codex-harness");
    assert_eq!(capture["scrollback"], "");

    let prompt = fs::read_to_string(prompt_path)?;
    assert!(prompt.contains("Captured Harness Scrollback"));
    assert!(prompt.contains("No live tmux scrollback was available"));
    Ok(())
}
