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
fn project_implementation_packet_writes_read_only_alignment_packet() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let before_entries = fs::read_dir(&repo)?.count();
    let out_dir = temp.path().join("implementation-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "implementation-packet",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--goal",
            "Make delegated implementation preserve the original Forager product intent.",
            "--success-state",
            "A worker can execute from a packet without using chat scrollback.",
            "--why-now",
            "Local model overnight work needs stronger design before execution.",
            "--north-star-fit",
            "Returns the operator to evidence, choices, and continuity.",
            "--brand-fit",
            "Keeps Forager as a local meta-harness rather than one hosted agent.",
            "--scope",
            "Typed implementation packet state",
            "--exclude",
            "Runtime launch approval",
            "--allowed-file",
            "src/offdesk/implementation_packet.rs",
            "--capability",
            "FD-016: implementation packet and recursive alignment review",
            "--approach",
            "Create typed state plus a read-only project CLI packet generator.",
            "--work-slice",
            "Add Rust state contract",
            "--work-slice",
            "Add CLI JSON projection",
            "--data-contract",
            "implementation_packet.v1",
            "--data-contract",
            "recursive_alignment_review.v1",
            "--preferred-worker",
            "deterministic_script",
            "--stop-condition",
            "Missing validation command or expected artifact",
            "--validation-command",
            "cargo test --test project_init_cli project_implementation_packet_writes_read_only_alignment_packet",
            "--evidence-ref",
            "docs/implementation-packet.md",
            "--expected-artifact",
            "IMPLEMENTATION_PACKET.json",
            "--handoff-requirement",
            "Morning Ondesk review must show whether original intent was served.",
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
    assert_eq!(json["kind"], "forager_project_implementation_packet");
    assert_eq!(json["project_key"], "demo-project");
    assert_eq!(json["read_only_project_state"], true);
    assert_eq!(json["grants_runtime_authority"], false);
    assert_eq!(json["summary"]["safe_to_delegate"], true);
    assert_eq!(json["summary"]["outcome"], "pass");
    assert_eq!(json["summary"]["required_revision_count"], 0);
    assert_eq!(json["packet"]["schema"], "implementation_packet.v1");
    assert_eq!(
        json["packet"]["recursive_alignment_review"]["schema"],
        "recursive_alignment_review.v1"
    );
    assert_eq!(
        json["packet"]["recursive_alignment_review"]["checks"]["scope_balance"],
        "right_sized"
    );
    assert_eq!(
        json["packet"]["closeout"]["accepted_truth_rule"],
        "Execution completion is not acceptance; closeout must compare actual results against this implementation packet."
    );

    let artifacts = &json["artifacts"];
    for key in [
        "implementation_packet_json",
        "recursive_alignment_review_json",
        "implementation_packet_markdown",
    ] {
        let path = PathBuf::from(artifacts[key].as_str().expect("artifact path"));
        assert!(path.exists(), "missing artifact {key}: {}", path.display());
    }

    let packet_path = PathBuf::from(
        artifacts["implementation_packet_json"]
            .as_str()
            .expect("packet path"),
    );
    let packet: Value = serde_json::from_str(&fs::read_to_string(packet_path)?)?;
    assert_eq!(packet["packet_id"], json["packet_id"]);
    assert_eq!(packet["capability_mapping"][0]["capability_id"], "FD-016");
    assert_eq!(
        packet["validation"]["evidence_required"][0],
        "docs/implementation-packet.md"
    );

    let review_path = PathBuf::from(
        artifacts["recursive_alignment_review_json"]
            .as_str()
            .expect("review path"),
    );
    let review: Value = serde_json::from_str(&fs::read_to_string(review_path)?)?;
    assert_eq!(review["outcome"], "pass");
    assert_eq!(review["safe_to_delegate"], true);

    let markdown = fs::read_to_string(
        artifacts["implementation_packet_markdown"]
            .as_str()
            .expect("markdown path"),
    )?;
    assert!(markdown.contains("Implementation Packet"));
    assert!(markdown.contains("safe_to_delegate: `true`"));
    assert!(markdown.contains("Runtime launch approval"));

    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    assert!(!repo.join(".forager").exists());
    Ok(())
}

#[test]
#[serial]
fn project_implementation_packet_flags_missing_delegation_requirements() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("project");
    write_synthetic_project(&repo)?;
    let out_dir = temp.path().join("implementation-packet");

    let output = forager_command(temp.path())
        .args([
            "project",
            "implementation-packet",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--goal",
            "Draft a weak packet for validation.",
            "--success-state",
            "The packet exposes missing execution requirements.",
            "--scope",
            "Packet shell only",
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
    assert_eq!(json["summary"]["safe_to_delegate"], false);
    assert_eq!(json["summary"]["outcome"], "revise");
    let revisions = json["packet"]["recursive_alignment_review"]["required_revisions"]
        .as_array()
        .expect("required revisions");
    for expected in [
        "excluded_scope_missing",
        "work_slices_missing",
        "stop_conditions_missing",
        "validation_plan_missing",
        "expected_artifacts_missing",
    ] {
        assert!(
            revisions.iter().any(|item| item == expected),
            "missing revision {expected}: {revisions:?}"
        );
    }
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

    let review_output = forager_command(temp.path())
        .args([
            "project",
            "retention-review",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--json",
        ])
        .output()?;
    assert!(
        review_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&review_output.stdout),
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: Value = serde_json::from_slice(&review_output.stdout)?;
    assert_eq!(review["schema"], "artifact_retention_review.v1");
    assert!(review["authority"]["read_only"].as_bool().unwrap_or(false));
    assert_eq!(review["summary"]["missing_entries"], 1);
    assert!(
        review["summary"]["action_required_entries"]
            .as_u64()
            .unwrap_or_default()
            >= 2
    );
    assert!(review["recommendations"]
        .as_array()
        .expect("retention recommendations")
        .iter()
        .any(
            |recommendation| recommendation["kind"] == "restore_or_update_missing_artifacts"
                && recommendation["priority"] == "high"
        ));
    let action_items = review["queues"]["action_required"]
        .as_array()
        .expect("action required items");
    assert!(action_items.iter().any(|item| {
        item["relative_path"] == "outputs/missing.pdf"
            && item["recommended_action"] == "restore_or_update_reference"
    }));
    assert!(action_items.iter().any(|item| {
        item["relative_path"] == "outputs/plot.png"
            && item["recommended_action"] == "promote_to_deliverables_or_mark_disposable"
    }));

    let request_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        request_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&request_output.stdout),
        String::from_utf8_lossy(&request_output.stderr)
    );
    let request: Value = serde_json::from_slice(&request_output.stdout)?;
    assert_eq!(request["schema"], "artifact_retention_approval_request.v1");
    assert_eq!(request["status"], "pending_approval");
    assert_eq!(request["action"], "maintenance.artifact_cleanup");
    assert_eq!(request["requested_action"], "promote");
    assert_eq!(request["risk_level"], "canonical_mutation");
    assert_eq!(request["target"]["relative_path"], "outputs/plot.png");
    assert_eq!(request["target"]["review_status"], "needs_triage");
    assert_eq!(request["authority"]["records_approval_only"], true);
    assert!(request["authority"]["does_not_authorize"]
        .as_array()
        .expect("authority")
        .iter()
        .any(|item| item == "delete files"));
    assert!(request["next_commands"]
        .as_array()
        .expect("next commands")
        .iter()
        .all(|command| !command
            .as_str()
            .unwrap_or_default()
            .contains("retention-apply")));

    let approval = &request["approval"];
    assert_eq!(approval["status"], "pending");
    assert_eq!(approval["source_surface"], "project.retention_request");
    assert_eq!(approval["metadata"]["kind"], "artifact_retention");
    assert_eq!(approval["metadata"]["artifact_kind"], "png");
    assert_eq!(approval["metadata"]["requested_action"], "promote");
    assert_eq!(
        approval["metadata"]["approval_brief"]["schema"],
        "approval_brief.v1"
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["source"],
        "project.retention_request"
    );
    assert!(approval["metadata"]["approval_brief"]["scope"]
        .as_str()
        .unwrap_or_default()
        .contains("does not delete, move, archive"));
    assert!(
        approval["metadata"]["approval_brief"]["judgment_route_summary"]
            .as_str()
            .unwrap_or_default()
            .contains("사용자")
    );
    assert!(
        approval["metadata"]["approval_brief"]["evidence_sufficiency"]
            .as_str()
            .unwrap_or_default()
            .contains("Retention review")
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["default_if_no_reply"],
        "defer"
    );
    assert!(approval["metadata"]["approval_brief"]["options"]
        .as_array()
        .expect("options")
        .iter()
        .any(|option| option["id"] == "defer"
            && option["natural_input_prompt"]
                .as_str()
                .unwrap_or_default()
                .contains("evidence")));

    let approvals_path = profile_dir(temp.path()).join("pending_action_approvals.json");
    let stored: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored.as_array().expect("stored approvals").len(), 1);
    assert_eq!(stored[0]["metadata"], approval["metadata"]);

    let request_again_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        request_again_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&request_again_output.stdout),
        String::from_utf8_lossy(&request_again_output.stderr)
    );
    let request_again: Value = serde_json::from_slice(&request_again_output.stdout)?;
    assert_eq!(
        request_again["approval"]["approval_id"],
        request["approval"]["approval_id"]
    );
    let stored_again: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored_again.as_array().expect("stored approvals").len(), 1);

    let pending_output = forager_command(temp.path())
        .args(["offdesk", "pending"])
        .output()?;
    assert!(
        pending_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&pending_output.stdout),
        String::from_utf8_lossy(&pending_output.stderr)
    );
    let pending_stdout = String::from_utf8_lossy(&pending_output.stdout);
    assert!(
        pending_stdout.contains("prompt: promote recommendation for artifact retention promote")
    );
    assert!(pending_stdout.contains("Approve the promote retention follow-up?"));
    assert!(pending_stdout.contains("artifact: Unreferenced human-facing output"));
    assert!(!pending_stdout.contains("outputs/plot.png"));

    let approval_id = approval["approval_id"].as_str().expect("approval id");
    let approve_output = forager_command(temp.path())
        .args(["offdesk", "ok", approval_id, "--json"])
        .output()?;
    assert!(
        approve_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&approve_output.stdout),
        String::from_utf8_lossy(&approve_output.stderr)
    );
    let approved: Value = serde_json::from_slice(&approve_output.stdout)?;
    assert_eq!(approved["approval_id"], approval_id);
    assert_eq!(approved["status"], "approved");

    let approved_request_output = forager_command(temp.path())
        .args([
            "project",
            "retention-request",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--path",
            "outputs/plot.png",
            "--action",
            "promote",
            "--json",
        ])
        .output()?;
    assert!(
        approved_request_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&approved_request_output.stdout),
        String::from_utf8_lossy(&approved_request_output.stderr)
    );
    let approved_request: Value = serde_json::from_slice(&approved_request_output.stdout)?;
    assert_eq!(approved_request["status"], "already_approved");
    assert!(approved_request["next_commands"]
        .as_array()
        .expect("approved next commands")
        .iter()
        .any(|command| command
            .as_str()
            .unwrap_or_default()
            .contains("retention-apply")));

    let deliverables_before = fs::read_to_string(repo.join("DELIVERABLES.md"))?;
    let apply_output = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        apply_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&apply_output.stdout),
        String::from_utf8_lossy(&apply_output.stderr)
    );
    let application: Value = serde_json::from_slice(&apply_output.stdout)?;
    assert_eq!(application["schema"], "artifact_retention_application.v1");
    assert_eq!(application["status"], "applied_plan_recorded");
    assert_eq!(application["approval_id"], approval_id);
    assert_eq!(application["requested_action"], "promote");
    assert_eq!(application["target"]["relative_path"], "outputs/plot.png");
    assert_eq!(application["operation"]["mutation_performed"], false);
    assert_eq!(application["operation"]["plan_only"], true);
    assert!(application["operation"]["blockers"]
        .as_array()
        .expect("blockers")
        .iter()
        .any(|blocker| blocker
            .as_str()
            .unwrap_or_default()
            .contains("DELIVERABLES.md")));
    assert_eq!(application["approval"]["before_status"], "approved");
    assert_eq!(application["approval"]["after_status"], "superseded");
    assert_eq!(application["approval"]["consumed"], true);
    assert_eq!(application["authority"]["writes_project_state"], false);
    assert_eq!(application["authority"]["writes_files"], false);
    let receipt_path = application["receipt_path"].as_str().expect("receipt path");
    assert!(Path::new(receipt_path).exists());
    let receipt: Value = serde_json::from_str(&fs::read_to_string(receipt_path)?)?;
    assert_eq!(receipt["approval_id"], approval_id);
    assert_eq!(
        fs::read_to_string(repo.join("DELIVERABLES.md"))?,
        deliverables_before
    );

    let promote_plan_output = forager_command(temp.path())
        .args([
            "project",
            "retention-promote",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(
        promote_plan_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&promote_plan_output.stdout),
        String::from_utf8_lossy(&promote_plan_output.stderr)
    );
    let promote_plan: Value = serde_json::from_slice(&promote_plan_output.stdout)?;
    assert_eq!(promote_plan["schema"], "artifact_retention_promotion.v1");
    assert_eq!(promote_plan["status"], "planned_review_required");
    assert_eq!(promote_plan["mutation_performed"], false);
    assert_eq!(promote_plan["authority"]["writes_project_state"], false);
    assert_eq!(
        promote_plan["deliverables_entry"],
        "- Unreferenced human-facing output: `outputs/plot.png`"
    );
    assert_eq!(
        fs::read_to_string(repo.join("DELIVERABLES.md"))?,
        deliverables_before
    );

    let promote_output = forager_command(temp.path())
        .args([
            "project",
            "retention-promote",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--reviewed",
            "--json",
        ])
        .output()?;
    assert!(
        promote_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&promote_output.stdout),
        String::from_utf8_lossy(&promote_output.stderr)
    );
    let promotion: Value = serde_json::from_slice(&promote_output.stdout)?;
    assert_eq!(promotion["schema"], "artifact_retention_promotion.v1");
    assert_eq!(promotion["status"], "promoted");
    assert_eq!(promotion["mutation_performed"], true);
    assert_eq!(promotion["reviewed"], true);
    assert_eq!(promotion["authority"]["writes_project_state"], true);
    assert_eq!(
        promotion["snapshot_verification"]["rollback_available"],
        true
    );
    assert_eq!(promotion["restore_plan"]["rollback_available"], true);
    assert!(promotion["promotion_receipt_path"]
        .as_str()
        .map(Path::new)
        .is_some_and(Path::exists));
    let snapshot_id = promotion["snapshot_verification"]["mutation_id"]
        .as_str()
        .expect("snapshot mutation id");
    assert!(profile_dir(temp.path())
        .join("mutation_snapshots")
        .join(format!("{snapshot_id}.json"))
        .exists());
    let promoted_deliverables = fs::read_to_string(repo.join("DELIVERABLES.md"))?;
    assert!(promoted_deliverables.contains("`outputs/plot.png`"));

    let promote_again_output = forager_command(temp.path())
        .args([
            "project",
            "retention-promote",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--reviewed",
            "--json",
        ])
        .output()?;
    assert!(
        promote_again_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&promote_again_output.stdout),
        String::from_utf8_lossy(&promote_again_output.stderr)
    );
    let promote_again: Value = serde_json::from_slice(&promote_again_output.stdout)?;
    assert_eq!(promote_again["status"], "already_promoted");
    assert_eq!(promote_again["mutation_performed"], false);
    assert_eq!(
        fs::read_to_string(repo.join("DELIVERABLES.md"))?,
        promoted_deliverables
    );

    let stored_after_apply: Value = serde_json::from_str(&fs::read_to_string(&approvals_path)?)?;
    assert_eq!(stored_after_apply[0]["approval_id"], approval_id);
    assert_eq!(stored_after_apply[0]["status"], "superseded");

    let apply_again_output = forager_command(temp.path())
        .args([
            "project",
            "retention-apply",
            repo.to_str().expect("utf-8 repo path"),
            "--project-key",
            "demo-project",
            "--approval-id",
            approval_id,
            "--json",
        ])
        .output()?;
    assert!(!apply_again_output.status.success());
    assert!(String::from_utf8_lossy(&apply_again_output.stderr)
        .contains("must be approved before retention-apply"));
    Ok(())
}
