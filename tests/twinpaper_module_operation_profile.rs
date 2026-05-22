use anyhow::Result;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use tempfile::tempdir;

fn script_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join(name)
}

fn write_file(path: &Path, contents: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, contents)?;
    Ok(())
}

fn write_synthetic_twinpaper(repo: &Path) -> Result<()> {
    write_file(
        &repo.join("AGENTS.md"),
        "Primary Objective Lock: keep no-option plus singlex coupled.\n",
    )?;
    write_file(
        &repo.join("docs/operations/RunLog.md"),
        "2026-05-22 direction-review no-option singlex validated_candidate p/q restart_stability primary_objective_gate false open-explore\n",
    )?;
    let module = repo.join("modules/03_regspec_machine");
    write_file(
        &module.join("README.md"),
        "# Module 03\nEntrypoint: scripts/run_module_03.sh\n",
    )?;
    write_file(&module.join("contract.yaml"), "name: module03\n")?;
    write_file(
        &module.join("pyproject.toml"),
        "[project]\nname = \"module03\"\n",
    )?;
    write_file(
        &module.join("scripts/run_module_03.sh"),
        r#"#!/usr/bin/env bash
case "${1:-plan}" in
  plan) ;;
  single-nooption) ;;
  single-singlex) ;;
  paired) ;;
  overnight) ;;
  migration-smoke) ;;
  contract-ci) ;;
esac
"#,
    )?;
    write_file(
        &module.join("scripts/modeling/run_phase_b_regspec_preset.py"),
        "print('preset')\n",
    )?;
    write_file(
        &module.join("scripts/modeling/run_phase_b_bikard_machine_scientist_scan.py"),
        "print('scan')\n",
    )?;
    write_file(
        &module.join("scripts/reporting/build_phase_b_regspec_dashboard.py"),
        "print('dashboard')\n",
    )?;
    write_file(
        &module.join("regspec_machine/orchestrator.py"),
        "class Orchestrator: pass\n",
    )?;
    write_file(
        &module.join("tests/test_orchestrator.py"),
        "def test_placeholder(): assert True\n",
    )?;
    write_file(
        &repo.join("data/metadata/phase_b_bikard_machine_scientist_direction_review_test.json"),
        &serde_json::to_string_pretty(&json!({
            "checks": {
                "primary_objective_gate_pass": false,
                "validated_candidate": 2,
                "restart_stability": "low",
                "p_value": 0.04,
                "q_value": 0.2
            }
        }))?,
    )?;
    write_file(
        &repo
            .join("data/metadata/phase_b_bikard_machine_scientist_paired_preset_summary_test.json"),
        &serde_json::to_string_pretty(&json!({
            "nooption": {"validated_candidate": 1},
            "singlex": {"validated_candidate": 2}
        }))?,
    )?;
    Ok(())
}

#[test]
fn builds_module03_operation_profile_from_evidence_state() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("TwinPaper");
    write_synthetic_twinpaper(&repo)?;
    let evidence = temp.path().join("evidence_bundle.json");
    write_file(
        &evidence,
        &serde_json::to_string_pretty(&json!({
            "current_state": {
                "baseline_evidence_status": "executed_primary_gate_failed",
                "claim_status": "pending_not_reportable",
                "latest_direction_review_artifact": "data/metadata/review.json",
                "has_nooption_evidence": true,
                "has_singlex_evidence": true,
                "has_openexplore_evidence": true,
                "has_direction_review_evidence": true
            }
        }))?,
    )?;
    let out = temp.path().join("profile.json");

    let output = Command::new("python3")
        .arg(script_path("build_twinpaper_module03_operation_profile.py"))
        .arg("--repo")
        .arg(&repo)
        .arg("--evidence-bundle")
        .arg(&evidence)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let profile: Value = serde_json::from_slice(&fs::read(&out)?)?;
    assert_eq!(profile["kind"], "twinpaper_module_operation_profile");
    assert_eq!(
        profile["current_state"]["baseline_evidence_status"],
        "executed_primary_gate_failed"
    );
    assert_eq!(profile["operation_gates"]["canonical_modes_present"], true);
    assert!(profile["allowed_operations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|operation| operation["id"] == "single-nooption"
            && operation["approval_required"] == true
            && operation["command"].as_str().unwrap().contains(
                "modules/03_regspec_machine/scripts/run_module_03.sh single-nooption --exec"
            )));
    assert!(profile["next_actions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|action| action["action"] == "diagnose_primary_gate_failure"));
    assert!(out.with_file_name("MODULE03_OPERATION_PROFILE.md").exists());
    Ok(())
}

#[test]
fn evidence_bundle_embeds_compact_module03_operation_profile() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("TwinPaper");
    write_synthetic_twinpaper(&repo)?;
    let out = temp.path().join("evidence").join("evidence_bundle.json");

    let output = Command::new("python3")
        .arg(script_path("build_twinpaper_evidence_bundle.py"))
        .arg("--repo")
        .arg(&repo)
        .arg("--out")
        .arg(&out)
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let bundle: Value = serde_json::from_slice(&fs::read(&out)?)?;
    let profile = &bundle["module_operation_profiles"]["module03_regspec_machine"];
    assert_eq!(profile["kind"], "twinpaper_module_operation_profile");
    assert_eq!(profile["operation_gates"]["canonical_modes_present"], true);
    assert!(profile["reportability_vocabulary"]
        .as_object()
        .unwrap()
        .contains_key("executed_primary_gate_failed"));
    assert!(profile["allowed_operations"]
        .as_array()
        .unwrap()
        .iter()
        .any(|operation| operation["id"] == "paired"
            && operation["command"]
                .as_str()
                .unwrap()
                .contains("modules/03_regspec_machine/scripts/run_module_03.sh paired --exec")));

    let review_out = temp.path().join("evidence").join("evidence_review.json");
    let review_output = Command::new("python3")
        .arg(script_path("review_evidence_bundle.py"))
        .arg("--bundle")
        .arg(&out)
        .arg("--out")
        .arg(&review_out)
        .output()?;
    assert!(
        review_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&review_output.stdout),
        String::from_utf8_lossy(&review_output.stderr)
    );
    let review: Value = serde_json::from_slice(&fs::read(&review_out)?)?;
    assert_eq!(review["passed"], true);
    assert_eq!(review["decision"], "sufficient");
    Ok(())
}

#[test]
fn prepare_twinpaper_offdesk_task_requires_module_preflight() -> Result<()> {
    let temp = tempdir()?;
    let repo = temp.path().join("TwinPaper");
    write_synthetic_twinpaper(&repo)?;
    let role_gate = temp.path().join("role_gate_results.json");
    write_file(
        &role_gate,
        &serde_json::to_string_pretty(&json!({
            "passed": true,
            "summary": {
                "total": 1,
                "failed": 0,
                "pass_rate": 1.0,
                "failure_category_counts": {},
                "quality_gate": {
                    "ready_for_long_workload": true
                }
            }
        }))?,
    )?;
    let review = temp.path().join("workload_review_results.json");
    write_file(
        &review,
        &serde_json::to_string_pretty(&json!({
            "summary": {
                "failed": 0
            },
            "results": [
                {
                    "case": "workload_manifest_review",
                    "passed": true,
                    "review_stage_decision": "needs_approval"
                }
            ]
        }))?,
    )?;
    let project_init_dir = temp
        .path()
        .join(".config/forager/profiles/twinpaper-adaptive-debug/project_initializations/20260522T000000Z_twinpaper");
    let module_preflight = project_init_dir.join("MODULE_OPERATION_PREFLIGHT.json");
    write_file(
        &module_preflight,
        &serde_json::to_string_pretty(&json!({
            "kind": "forager_module_operation_preflight",
            "project_key": "twinpaper",
            "ready_for_offdesk_runtime": false,
            "operation_targets": [
                {
                    "scope_ref": "module03_regspec_machine",
                    "readiness_level": "known_profile_builder_available",
                    "recognized_profile_kind": "twinpaper_module03_regspec_machine",
                    "profile_builder_available": true,
                    "evidence_bundle_builder_available": true,
                    "evidence_review_builder_available": true,
                    "blockers": [
                        "operator_review_required_before_runtime_enqueue",
                        "module_operation_profile_requires_review",
                        "evidence_bundle_requires_review"
                    ],
                    "recommended_commands": [
                        {
                            "purpose": "build_evidence_bundle",
                            "command": "scripts/build_twinpaper_evidence_bundle.py --secret sk-secretsecretsecretsecret"
                        },
                        {
                            "purpose": "review_evidence_bundle",
                            "command": "scripts/review_evidence_bundle.py"
                        },
                        {
                            "purpose": "build_module_operation_profile",
                            "command": "scripts/build_twinpaper_module03_operation_profile.py"
                        },
                        {
                            "purpose": "prepare_offdesk_task_after_review",
                            "command": "scripts/prepare_twinpaper_offdesk_task.py"
                        }
                    ]
                }
            ]
        }))?,
    )?;
    write_file(
        &project_init_dir.join("PROJECT_OPERATION_PROFILE.json"),
        &serde_json::to_string_pretty(&json!({
            "kind": "forager_project_operation_profile",
            "generated_at": "2026-05-22T00:00:00Z",
            "project_key": "twinpaper",
            "module_operation_preflight_path": module_preflight
        }))?,
    )?;
    let out_root = temp.path().join("prepare");

    let output = Command::new("python3")
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .arg(script_path("prepare_twinpaper_offdesk_task.py"))
        .arg("--repo")
        .arg(&repo)
        .arg("--out-root")
        .arg(&out_root)
        .arg("--duration-minutes")
        .arg("0.1")
        .arg("--max-iterations")
        .arg("1")
        .arg("--role-gate-result")
        .arg(&role_gate)
        .arg("--review-artifact")
        .arg(&review)
        .arg("--wiki-candidate-mode")
        .arg("disabled")
        .arg("--wiki-trial-mode")
        .arg("disabled")
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let prepared: Value = serde_json::from_slice(&output.stdout)?;
    assert_eq!(prepared["preflight"]["ready_for_enqueue"], true);
    let launch_report_path = PathBuf::from(
        prepared["launch_dry_run_report"]
            .as_str()
            .expect("launch dry-run report path"),
    );
    assert_eq!(
        prepared["preflight"]["module_operation_preflight"]["reason"],
        "module_preflight_target_ready"
    );
    let manifest_path = PathBuf::from(prepared["manifest"].as_str().expect("manifest path"));
    let manifest: Value = serde_json::from_slice(&fs::read(&manifest_path)?)?;
    assert_eq!(
        manifest["preflight"]["module_operation_preflight"]["ready"],
        true
    );
    assert_eq!(
        manifest["preflight"]["module_operation_preflight"]["recommended_command_purposes"][0],
        "build_evidence_bundle"
    );
    assert_eq!(
        manifest["safety"]["module_operation_preflight_required"],
        true
    );
    assert_eq!(
        manifest["artifacts"]["launch_dry_run_report"]
            .as_str()
            .expect("launch dry-run artifact path"),
        launch_report_path
            .to_str()
            .expect("utf-8 launch report path")
    );
    assert!(manifest_path.with_file_name("preflight_ready").exists());
    assert!(launch_report_path.exists());
    let launch_report = fs::read_to_string(&launch_report_path)?;
    assert!(launch_report.contains("TwinPaper Launch Dry Run"));
    assert!(launch_report.contains("ready_for_enqueue: `true`"));
    assert!(launch_report.contains("module_preflight: `true`"));
    assert!(launch_report.contains("Runtime dispatch still requires"));
    assert!(launch_report.contains("offdesk_enqueue_command.sh"));
    let manifest_text = fs::read_to_string(&manifest_path)?;
    assert!(!manifest_text.contains("sk-secretsecretsecretsecret"));
    assert!(!manifest_text.contains("scripts/build_twinpaper_evidence_bundle.py --secret"));
    assert!(!launch_report.contains("sk-secretsecretsecretsecret"));
    assert!(!launch_report.contains("scripts/build_twinpaper_evidence_bundle.py --secret"));
    Ok(())
}
