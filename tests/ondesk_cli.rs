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
