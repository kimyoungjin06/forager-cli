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

fn write_json(path: &Path, value: &Value) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_vec_pretty(value)?)?;
    Ok(())
}

fn assert_command_ok(output: &std::process::Output) {
    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn offdesk_plan_harness_runs_without_domain_profile() -> Result<()> {
    let temp = tempdir()?;
    let evidence_bundle = temp.path().join("evidence_bundle.json");
    write_json(
        &evidence_bundle,
        &json!({
            "kind": "generic_evidence_bundle",
            "repo": {
                "path": temp.path().display().to_string(),
                "branch": "test"
            },
            "current_state": {
                "evidence_status": "incomplete",
                "claim_status": "pending_review"
            },
            "next_actions": [
                {"action": "inspect current evidence"},
                {"action": "write operator review packet"}
            ],
            "allowed_operations": [],
            "forbidden_actions": [
                "runtime execution without operator approval"
            ],
            "evidence_contract": {
                "primary_artifacts": [
                    "docs/runlog.md",
                    "artifacts/summary.json"
                ]
            }
        }),
    )?;

    let multiturn_dir = temp.path().join("multiturn");
    let multiturn = Command::new("python3")
        .arg(script_path("build_offdesk_multiturn_plan.py"))
        .arg("--evidence-bundle")
        .arg(&evidence_bundle)
        .arg("--out-dir")
        .arg(&multiturn_dir)
        .arg("--mock")
        .output()?;
    assert_command_ok(&multiturn);
    let multiturn_result: Value = serde_json::from_slice(&multiturn.stdout)?;
    assert_eq!(
        multiturn_result["schema"],
        "offdesk_multiturn_plan_pipeline_result.v1"
    );
    assert_eq!(multiturn_result["profile_key"], "generic");
    assert_eq!(multiturn_result["status"], "passed");
    assert_eq!(multiturn_result["validation_failures"], json!([]));

    let final_plan_path = multiturn_dir.join("OVERNIGHT_PLAN.json");
    let final_plan_text = fs::read_to_string(&final_plan_path)?;
    let final_plan: Value = serde_json::from_str(&final_plan_text)?;
    assert_eq!(final_plan["schema"], "offdesk_multiturn_plan.v1");
    assert_eq!(final_plan["profile_key"], "generic");
    assert_eq!(final_plan["decision"]["ready_for_enqueue"], false);

    let council_dir = temp.path().join("council");
    let council = Command::new("python3")
        .arg(script_path("build_offdesk_planner_council.py"))
        .arg("--evidence-bundle")
        .arg(&evidence_bundle)
        .arg("--out-dir")
        .arg(&council_dir)
        .arg("--mode")
        .arg("mock")
        .output()?;
    assert_command_ok(&council);
    let council_result: Value = serde_json::from_slice(&council.stdout)?;
    assert_eq!(council_result["schema"], "offdesk_planner_council.v1");
    assert_eq!(council_result["profile_key"], "generic");
    assert_eq!(
        council_result["consensus"]["ready_for_operator_review"],
        true
    );
    assert_eq!(council_result["validation_failures"], json!([]));

    let quality_out = temp.path().join("quality.json");
    let quality = Command::new("python3")
        .arg(script_path("compare_offdesk_plan_quality.py"))
        .arg("--run")
        .arg(format!("generic={}", multiturn_dir.display()))
        .arg("--out")
        .arg(&quality_out)
        .output()?;
    assert_command_ok(&quality);
    let quality_result: Value = serde_json::from_slice(&quality.stdout)?;
    assert_eq!(
        quality_result["schema"],
        "offdesk_plan_quality_comparison.v1"
    );
    assert_eq!(quality_result["profile_key"], "generic");
    assert_eq!(quality_result["runs"][0]["risk_flags"], json!([]));

    Ok(())
}
