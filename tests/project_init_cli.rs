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
    assert_eq!(json["summary"]["ready_for_offdesk_runtime"], false);

    let artifacts = &json["artifacts"];
    for key in [
        "operation_profile_json",
        "onboarding_markdown",
        "module_candidates_json",
        "evidence_collector_plan_markdown",
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
        profile["initialization_policy"]["grants_execution_authority"],
        false
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
    assert_eq!(candidate["path"], "modules/01_research");
    assert_eq!(
        candidate["operation_profile_status"],
        "candidate_requires_operator_review"
    );
    assert!(candidate["entrypoints"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| entry["path"] == "modules/01_research/scripts/run.sh"));

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
        .any(|item| item == "operator_review_required_before_runtime_enqueue"));

    let start_package = fs::read_to_string(
        artifacts["ondesk_start_package_markdown"]
            .as_str()
            .expect("start package path"),
    )?;
    assert!(start_package.contains("Ondesk Start Package"));
    assert!(start_package.contains("Runtime execution, wiki promotion, and file cleanup"));

    assert_eq!(fs::read_dir(&repo)?.count(), before_entries);
    assert!(!repo.join(".forager").exists());
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
