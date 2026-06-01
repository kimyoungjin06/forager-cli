use anyhow::Result;
use serde_json::Value;
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

fn write_file(path: &Path, contents: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, contents)?;
    Ok(())
}

fn write_synthetic_project(repo: &Path) -> Result<()> {
    write_file(repo.join("AGENTS.md").as_path(), "Project instructions\n")?;
    write_file(repo.join("README.md").as_path(), "# Synthetic Project\n")?;
    write_file(
        repo.join("pyproject.toml").as_path(),
        "[project]\nname='demo'\n",
    )?;
    write_file(
        repo.join("docs/operations/RunLog.md").as_path(),
        "# RunLog\n- initial note\n",
    )?;
    write_file(
        repo.join("modules/01_research/README.md").as_path(),
        "# Module 01\n",
    )?;
    write_file(
        repo.join("modules/01_research/contract.yaml").as_path(),
        "name: module01\n",
    )?;
    write_file(
        repo.join("modules/01_research/scripts/run.sh").as_path(),
        "#!/usr/bin/env bash\necho run\n",
    )?;
    write_file(
        repo.join("modules/01_research/tests/test_placeholder.py")
            .as_path(),
        "def test_placeholder():\n    assert True\n",
    )?;
    Ok(())
}

#[test]
#[serial]
fn project_init_writes_read_only_initialization_packet() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let before_entries = fs::read_dir(&repo)?.count();
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/01_research",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["kind"], "forager_project_initialization");
    assert_eq!(json["project_key"], "demo-project");
    assert_eq!(json["read_only_project_state"], true);
    assert_eq!(json["requires_operator_review"], true);
    assert_eq!(json["summary"]["module_candidate_count"], 1);
    assert_eq!(json["summary"]["operation_target_count"], 1);
    assert_eq!(json["summary"]["ready_for_offdesk_runtime"], false);

    let artifacts = &json["artifacts"];
    for key in [
        "operation_profile_json",
        "onboarding_markdown",
        "module_candidates_json",
        "module_operation_preflight_json",
        "evidence_collector_plan_markdown",
        "governance_surface_hints_markdown",
        "wiki_seed_candidates_json",
        "ondesk_start_package_markdown",
        "offdesk_ready_check_json",
    ] {
        let path = PathBuf::from(artifacts[key].as_str().expect("artifact path"));
        assert!(path.exists(), "missing artifact {key}: {}", path.display());
    }

    let profile_path = PathBuf::from(
        artifacts["operation_profile_json"]
            .as_str()
            .expect("profile path"),
    );
    let profile: Value = serde_json::from_str(&fs::read_to_string(profile_path)?)?;
    assert_eq!(profile["kind"], "forager_project_operation_profile");
    assert_eq!(
        profile["scope_model"]["project_target"]["scope_ref"],
        "demo-project"
    );
    assert_eq!(
        profile["scope_model"]["operation_targets"][0]["scope_ref"],
        "module01_research"
    );
    assert_eq!(
        profile["scope_model"]["operation_targets"][0]["role"],
        "module_operation_target"
    );
    assert_eq!(
        profile["initialization_policy"]["grants_execution_authority"],
        false
    );
    assert!(profile["ondesk_bridge"]["first_reads"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "MODULE_OPERATION_PREFLIGHT.json"));
    assert!(profile["ondesk_bridge"]["first_reads"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "GOVERNANCE_SURFACE_HINTS.md"));
    assert_eq!(
        profile["module_operation_preflight_path"],
        artifacts["module_operation_preflight_json"]
    );
    assert_eq!(
        profile["governance_surface_hints_path"],
        artifacts["governance_surface_hints_markdown"]
    );
    assert!(profile["agent_modes"]
        .as_array()
        .unwrap()
        .iter()
        .any(|mode| mode["mode"] == "critique"));
    assert!(
        profile["safety_policy"]["forbidden_without_separate_approval"]
            .as_array()
            .unwrap()
            .iter()
            .any(|item| item.as_str().unwrap().contains("delete"))
    );

    let modules_path = PathBuf::from(
        artifacts["module_candidates_json"]
            .as_str()
            .expect("modules path"),
    );
    let modules: Value = serde_json::from_str(&fs::read_to_string(modules_path)?)?;
    let candidate = &modules["candidates"][0];
    assert_eq!(candidate["module_id"], "module01_research");
    assert_eq!(candidate["scope_kind"], "module");
    assert_eq!(candidate["scope_ref"], "module01_research");
    assert_eq!(candidate["parent_project_key"], "demo-project");
    assert_eq!(candidate["selected_operation_target"], true);
    assert_eq!(candidate["path"], "modules/01_research");
    assert_eq!(
        candidate["operation_profile_status"],
        "operation_target_requires_module_profile_review"
    );
    assert!(candidate["entrypoints"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["path"] == "modules/01_research/scripts/run.sh"));

    let preflight_path = PathBuf::from(
        artifacts["module_operation_preflight_json"]
            .as_str()
            .expect("preflight path"),
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(preflight_path)?)?;
    assert_eq!(preflight["kind"], "forager_module_operation_preflight");
    assert_eq!(preflight["ready_for_offdesk_runtime"], false);
    assert_eq!(
        preflight["operation_targets"][0]["scope_ref"],
        "module01_research"
    );
    assert_eq!(
        preflight["operation_targets"][0]["readiness_level"],
        "manual_profile_authoring_required"
    );
    assert!(preflight["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "no_known_module_profile_builder"));

    let ready_path = PathBuf::from(
        artifacts["offdesk_ready_check_json"]
            .as_str()
            .expect("ready path"),
    );
    let ready: Value = serde_json::from_str(&fs::read_to_string(ready_path)?)?;
    assert_eq!(ready["ready_for_ondesk_start"], true);
    assert_eq!(ready["ready_for_offdesk_runtime"], false);
    assert!(ready["blockers"]
        .as_array()
        .unwrap()
        .iter()
        .any(|item| item == "operation_targets_require_module_profile_review"));
    assert_eq!(json["summary"]["governance_surface_missing_count"], 4);

    let governance_hints = fs::read_to_string(
        artifacts["governance_surface_hints_markdown"]
            .as_str()
            .expect("governance hints path"),
    )?;
    assert!(governance_hints.contains("Governance Surface Hints"));
    assert!(governance_hints.contains("`PROJECT_STATE.md`"));
    assert!(governance_hints.contains("`NEXT_ACTIONS.md`"));
    assert!(governance_hints.contains("`DECISIONS.md`"));
    assert!(governance_hints.contains("`DELIVERABLES.md`"));
    assert!(governance_hints.contains("forager project audit-docs"));

    let start_package = fs::read_to_string(
        artifacts["ondesk_start_package_markdown"]
            .as_str()
            .expect("start package path"),
    )?;
    assert!(start_package.contains("Ondesk Start Package"));
    assert!(start_package.contains("scope=`module:module01_research`"));
    assert!(start_package.contains("Runtime execution, wiki promotion, and file cleanup"));

    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    assert!(!repo.join(".forager").exists());
    Ok(())
}

#[test]
#[serial]
fn project_init_rejects_unknown_operation_target() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--operation-target",
            "modules/missing",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("operation target not found"));
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_dry_run_does_not_write_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["kind"], "forager_project_governance_hints_application");
    assert_eq!(json["reviewed"], false);
    assert_eq!(json["writes_target_project_state"], false);
    assert_eq!(json["requires_operator_review"], true);
    assert_eq!(json["planned_count"], 4);
    assert_eq!(json["created_count"], 0);
    assert_eq!(json["skipped_existing_count"], 0);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .all(|operation| operation["status"] == "planned_create"));
    for path in [
        "PROJECT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    ] {
        assert!(!repo.join(path).exists(), "dry-run wrote {path}");
    }
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_reviewed_creates_missing_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--reviewed",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["reviewed"], true);
    assert_eq!(json["writes_target_project_state"], true);
    assert_eq!(json["requires_operator_review"], false);
    assert_eq!(json["planned_count"], 4);
    assert_eq!(json["created_count"], 4);
    assert_eq!(json["skipped_existing_count"], 0);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .all(|operation| operation["status"] == "created"));
    for path in [
        "PROJECT_STATE.md",
        "NEXT_ACTIONS.md",
        "DECISIONS.md",
        "DELIVERABLES.md",
    ] {
        assert!(repo.join(path).exists(), "missing created {path}");
    }

    let audit = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;
    assert!(
        audit.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&audit.stdout),
        String::from_utf8_lossy(&audit.stderr)
    );
    let audit_json: Value = serde_json::from_slice(&audit.stdout)?;
    assert_eq!(audit_json["findings"].as_array().unwrap().len(), 0);
    assert_eq!(audit_json["recommendations"].as_array().unwrap().len(), 0);
    Ok(())
}

#[test]
#[serial]
fn project_apply_governance_hints_reviewed_never_overwrites_existing_surface() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nsentinel-current-state\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "apply-governance-hints",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--reviewed",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["planned_count"], 3);
    assert_eq!(json["created_count"], 3);
    assert_eq!(json["skipped_existing_count"], 1);
    assert!(json["operations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|operation| operation["role"] == "current_state"
            && operation["status"] == "skipped_existing"
            && operation["path"] == "PROJECT_STATE.md"));
    assert!(fs::read_to_string(repo.join("PROJECT_STATE.md"))?.contains("sentinel-current-state"));
    Ok(())
}

#[test]
#[serial]
fn project_init_preflight_recognizes_twinpaper_module03() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("twinpaper");
    write_file(repo.join("AGENTS.md").as_path(), "TwinPaper instructions\n")?;
    write_file(repo.join("README.md").as_path(), "# TwinPaper\n")?;
    write_file(
        repo.join("docs/operations/RunLog.md").as_path(),
        "# RunLog\n",
    )?;
    write_file(
        repo.join("modules/03_regspec_machine/README.md").as_path(),
        "# RegSpec Machine\n",
    )?;
    write_file(
        repo.join("modules/03_regspec_machine/contract.yaml")
            .as_path(),
        "name: regspec\n",
    )?;
    write_file(
        repo.join("modules/03_regspec_machine/pyproject.toml")
            .as_path(),
        "[project]\nname='regspec'\n",
    )?;
    let out_dir = temp.path().join("init-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "twinpaper",
            "--operation-target",
            "modules/03_regspec_machine",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    let preflight_path = PathBuf::from(
        json["artifacts"]["module_operation_preflight_json"]
            .as_str()
            .expect("preflight path"),
    );
    let preflight: Value = serde_json::from_str(&fs::read_to_string(preflight_path)?)?;
    let target = &preflight["operation_targets"][0];
    assert_eq!(target["scope_ref"], "module03_regspec_machine");
    assert_eq!(
        target["recognized_profile_kind"],
        "twinpaper_module03_regspec_machine"
    );
    assert_eq!(target["profile_builder_available"], true);
    assert_eq!(target["evidence_bundle_builder_available"], true);
    assert!(target["recommended_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(|command| command["purpose"] == "build_evidence_bundle"
            && command["command"]
                .as_str()
                .unwrap()
                .contains("build_twinpaper_evidence_bundle.py")));
    assert!(target["recommended_commands"]
        .as_array()
        .unwrap()
        .iter()
        .any(
            |command| command["purpose"] == "prepare_offdesk_task_after_review"
                && command["requires_runtime_approval"] == true
        ));
    Ok(())
}

#[test]
#[serial]
fn project_init_refuses_non_empty_output_without_force() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("init-packet");
    fs::create_dir_all(&out_dir)?;
    fs::write(out_dir.join("existing.txt"), "keep")?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "init",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--out",
            out_dir.to_str().expect("utf-8 out path"),
            "--json",
        ])
        .output()?;

    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("output directory is not empty"));
    assert!(out_dir.join("existing.txt").exists());
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_accepts_clean_governance_surfaces() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("AGENTS.md").as_path(),
        "First read `PROJECT_STATE.md`, `NEXT_ACTIONS.md`, `DECISIONS.md`, and `DELIVERABLES.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("NEXT_ACTIONS.md").as_path(), "# Next Actions\n")?;
    write_file(
        repo.join("DECISIONS.md").as_path(),
        "# Decisions\n\n- Source: `PROJECT_STATE.md`\n",
    )?;
    write_file(
        repo.join("outputs/report.html").as_path(),
        "<h1>Report</h1>\n",
    )?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `outputs/report.html`: inspection report.\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "research-longrun",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["schema"], "documentation_governance_audit_v1");
    assert_eq!(json["profile"], "research-longrun");
    assert_eq!(json["findings"].as_array().unwrap().len(), 0);
    assert_eq!(json["recommendations"].as_array().unwrap().len(), 0);
    assert_eq!(json["summary"]["surfaces"]["current_present"], true);
    assert_eq!(json["summary"]["deliverables"]["output_candidates"], 1);
    assert_eq!(
        json["summary"]["deliverables"]["referenced_human_outputs"],
        1
    );
    assert_eq!(
        json["summary"]["deliverables"]["unreferenced_human_outputs"],
        0
    );
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_rejects_missing_deliverable_path() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `outputs/missing.html`: missing report.\n",
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        !output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert!(json["findings"].as_array().unwrap().iter().any(|finding| {
        finding["severity"] == "error" && finding["code"] == "missing_deliverable_path"
    }));
    assert!(json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|recommendation| recommendation["kind"] == "repair_deliverables_surface"));
    assert_eq!(
        json["summary"]["deliverables"]["missing_paths"][0],
        "outputs/missing.html"
    );
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("documentation governance audit found error findings"));
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_recommends_focused_output_review() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- `README.md`: source overview.\n",
    )?;
    for index in 0..7 {
        write_file(
            repo.join(format!("outputs/report-{index}.html")).as_path(),
            &"x".repeat((index + 1) * 10),
        )?;
    }
    let markdown_path = temp.path().join("audit.md");

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--current-stale-days",
            "36500",
            "--md-out",
            markdown_path.to_str().expect("utf-8 markdown path"),
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["summary"]["deliverables"]["unreferenced_human_outputs"],
        7
    );
    let recommendation = json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|recommendation| recommendation["kind"] == "review_human_output_candidates")
        .expect("focused output review recommendation");
    assert_eq!(recommendation["priority"], "normal");
    assert!(recommendation["suggested_action"]
        .as_str()
        .unwrap()
        .contains("RETENTION_REVIEW.md"));
    let paths = recommendation["paths"].as_array().unwrap();
    assert_eq!(paths.len(), 5);
    assert_eq!(paths[0], "outputs/report-6.html");
    assert_eq!(paths[4], "outputs/report-2.html");

    let markdown = fs::read_to_string(markdown_path)?;
    assert!(markdown.contains("Recommendations"));
    assert!(markdown.contains("review_human_output_candidates"));
    assert!(markdown.contains("RETENTION_REVIEW.md"));
    assert!(markdown.contains("For the full machine-readable summary"));
    assert!(!markdown.contains("Machine Summary"));
    Ok(())
}

#[test]
#[serial]
fn project_audit_docs_recommends_adaptive_wiki_projection_export() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("README.md").as_path(),
        "# Synthetic Project\n\nStart with `PROJECT_STATE.md`.\n",
    )?;
    write_file(
        repo.join("AGENTS.md").as_path(),
        "First read `PROJECT_STATE.md`, `NEXT_ACTIONS.md`, `DECISIONS.md`, and `DELIVERABLES.md`.\n",
    )?;
    write_file(
        repo.join("PROJECT_STATE.md").as_path(),
        "# Project State\n\nUpdated: 2026-05-30\n",
    )?;
    write_file(repo.join("NEXT_ACTIONS.md").as_path(), "# Next Actions\n")?;
    write_file(repo.join("DECISIONS.md").as_path(), "# Decisions\n")?;
    write_file(repo.join("DELIVERABLES.md").as_path(), "# Deliverables\n")?;

    let adaptive_profile = temp
        .path()
        .join(".config")
        .join("forager")
        .join("profiles")
        .join("demo");
    write_file(
        adaptive_profile
            .join("adaptive_wiki_entries.json")
            .as_path(),
        r#"{"version":"2026-05-14.v0","entries":[]}"#,
    )?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "audit-docs",
            repo.to_str().expect("utf-8 repo path"),
            "--audit-profile",
            "standard",
            "--adaptive-profile-dir",
            adaptive_profile.to_str().expect("utf-8 profile path"),
            "--current-stale-days",
            "36500",
            "--json",
        ])
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(
        json["summary"]["adaptive_wiki"]["projection_state"],
        "missing"
    );
    assert!(json["findings"].as_array().unwrap().iter().any(|finding| {
        finding["severity"] == "warn" && finding["code"] == "missing_adaptive_wiki_projection"
    }));
    let recommendation = json["recommendations"]
        .as_array()
        .unwrap()
        .iter()
        .find(|recommendation| recommendation["kind"] == "reexport_adaptive_wiki_projection")
        .expect("adaptive wiki projection recommendation");
    assert_eq!(recommendation["priority"], "normal");
    assert_eq!(
        recommendation["command"],
        "forager -p demo offdesk wiki export-markdown"
    );
    Ok(())
}

#[test]
#[serial]
fn project_artifact_index_tracks_human_outputs_and_missing_refs() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_file(
        repo.join("DELIVERABLES.md").as_path(),
        "# Deliverables\n\n- Main report: `outputs/report.html`\n- Missing export: `outputs/missing.pdf`\n",
    )?;
    write_file(
        repo.join("outputs/report.html").as_path(),
        "<html><body>report</body></html>\n",
    )?;
    write_file(repo.join("outputs/plot.png").as_path(), "png-bytes\n")?;

    let output = forager_command(temp.path())
        .args([
            "project",
            "artifact-index",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let json: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(json["schema"], "artifact_index.v1");
    assert_eq!(json["project_key"], "demo-project");
    assert!(json["authority"]["read_only"].as_bool().unwrap_or(false));
    assert!(
        json["summary"]["human_facing_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 3
    );
    assert_eq!(json["summary"]["missing_entries"], 1);

    let entries = json["entries"].as_array().expect("artifact entries");
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/report.html"
            && entry["source"] == "project_deliverables"
            && entry["retention_class"] == "handoff"
            && entry["present"] == true
    }));
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/plot.png"
            && entry["source"] == "project_output_scan"
            && entry["review_status"] == "needs_triage"
    }));
    assert!(entries.iter().any(|entry| {
        entry["relative_path"] == "outputs/missing.pdf"
            && entry["source"] == "project_deliverables"
            && entry["present"] == false
            && entry["review_status"] == "missing"
    }));
    Ok(())
}
