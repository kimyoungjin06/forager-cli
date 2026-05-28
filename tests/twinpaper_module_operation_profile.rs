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

const GENERIC_FORBIDDEN_CARD_SNIPPETS: &[&str] = &[
    "<pre>",
    "decision_request_id",
    "request_id",
    "state_path",
    "telegram_decision_state",
    "callback_data",
    "raw_text_preview",
    "fake-token-for-test",
    "123456789",
    "telegram.env",
];

struct TelegramCardQualitySpec<'a> {
    message_type: &'a str,
    required_message: &'a [&'a str],
    required_detail: &'a [&'a str],
    required_buttons: &'a [&'a str],
    forbidden_user_surface: &'a [&'a str],
    max_primary_lines: usize,
}

fn assert_contains_all(surface: &str, text: &str, snippets: &[&str]) {
    for snippet in snippets {
        assert!(
            text.contains(snippet),
            "{surface} should contain {snippet:?}; actual:\n{text}"
        );
    }
}

fn assert_contains_none(surface: &str, text: &str, snippets: &[&str]) {
    for snippet in snippets {
        assert!(
            !text.contains(snippet),
            "{surface} should not contain {snippet:?}; actual:\n{text}"
        );
    }
}

fn non_empty_line_count(text: &str) -> usize {
    text.lines().filter(|line| !line.trim().is_empty()).count()
}

fn assert_telegram_card_quality(result: &Value, spec: TelegramCardQualitySpec<'_>) {
    assert_eq!(result["message_type"], spec.message_type);
    let message_preview = result["message_preview"].as_str().expect("message preview");
    let detail_preview = result["detail_preview"].as_str().expect("detail preview");
    let labels: Vec<String> = result["keyboard"]["labels"]
        .as_array()
        .expect("keyboard labels")
        .iter()
        .map(|label| label.as_str().unwrap_or_default().to_string())
        .collect();

    assert_contains_all("message_preview", message_preview, &["<b>", "질문", "범위"]);
    assert_contains_all("message_preview", message_preview, spec.required_message);
    assert_contains_all("detail_preview", detail_preview, &["<b>", "선택별 의미"]);
    assert_contains_all("detail_preview", detail_preview, spec.required_detail);
    assert_contains_none(
        "message_preview",
        message_preview,
        GENERIC_FORBIDDEN_CARD_SNIPPETS,
    );
    assert_contains_none(
        "detail_preview",
        detail_preview,
        GENERIC_FORBIDDEN_CARD_SNIPPETS,
    );
    assert_contains_none(
        "message_preview",
        message_preview,
        spec.forbidden_user_surface,
    );
    assert_contains_none(
        "detail_preview",
        detail_preview,
        spec.forbidden_user_surface,
    );
    for expected in spec.required_buttons {
        assert!(
            labels.iter().any(|label| label == expected),
            "keyboard labels should include {expected:?}; actual: {labels:?}"
        );
    }
    assert!(
        non_empty_line_count(message_preview) <= spec.max_primary_lines,
        "primary card exceeded line budget ({} > {}):\n{}",
        non_empty_line_count(message_preview),
        spec.max_primary_lines,
        message_preview
    );
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
        .arg("--council-mode")
        .arg("prompt-package")
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
    assert!(prepared["preflight"]["warnings"]
        .as_array()
        .expect("preflight warnings")
        .iter()
        .any(|warning| warning.as_str()
            == Some(
                "prompt_package_council_requires_external_reviewer_execution_and_will_stop_on_needs_council_execution"
            )));
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
    assert_eq!(manifest["preflight"]["council"]["mode"], "prompt-package");
    assert_eq!(
        manifest["preflight"]["council"]["stop_on_non_continue"],
        true
    );
    assert!(manifest["preflight"]["warnings"]
        .as_array()
        .expect("manifest preflight warnings")
        .iter()
        .any(|warning| warning.as_str()
            == Some(
                "prompt_package_council_requires_external_reviewer_execution_and_will_stop_on_needs_council_execution"
            )));
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
    let validation_packet_path = PathBuf::from(
        manifest["artifacts"]["long_run_validation_packet"]
            .as_str()
            .expect("long-run validation packet path"),
    );
    assert!(manifest_path.with_file_name("preflight_ready").exists());
    assert!(launch_report_path.exists());
    assert!(validation_packet_path.exists());
    let launch_report = fs::read_to_string(&launch_report_path)?;
    assert!(launch_report.contains("TwinPaper Launch Dry Run"));
    assert!(launch_report.contains("ready_for_enqueue: `true`"));
    assert!(launch_report.contains("schedule_target_at: `null`"));
    assert!(!launch_report.contains("schedule_target_at: `None`"));
    assert!(launch_report.contains("module_preflight: `true`"));
    assert!(launch_report.contains("council_stop_on_non_continue: `true`"));
    assert!(launch_report.contains(
        "prompt_package_council_requires_external_reviewer_execution_and_will_stop_on_needs_council_execution"
    ));
    assert!(launch_report.contains("prompt-package council writes reviewer prompts"));
    assert!(launch_report.contains("Runtime dispatch still requires"));
    assert!(launch_report.contains("offdesk_enqueue_command.sh"));
    let validation_packet = fs::read_to_string(&validation_packet_path)?;
    assert!(validation_packet.contains("TwinPaper Long-Run Validation Packet"));
    assert!(validation_packet.contains("Gate 5: Closeout"));
    assert!(validation_packet.contains("Gate 6: Ondesk Return"));
    assert!(validation_packet.contains("Gate 7: Wiki Review"));
    assert!(validation_packet.contains("council_stop_on_non_continue: `true`"));
    assert!(validation_packet.contains(
        "prompt_package_council_requires_external_reviewer_execution_and_will_stop_on_needs_council_execution"
    ));
    assert!(validation_packet.contains("the run can stop before the scheduled duration"));
    assert!(validation_packet.contains("offdesk closeout"));
    assert!(validation_packet.contains("ondesk prompt-package"));
    assert!(validation_packet.contains("wiki candidates"));
    assert!(validation_packet
        .contains("offdesk tasks --project-key twinpaper --task-id twinpaper-autonomy-"));
    let manifest_text = fs::read_to_string(&manifest_path)?;
    assert!(!manifest_text.contains("sk-secretsecretsecretsecret"));
    assert!(!manifest_text.contains("scripts/build_twinpaper_evidence_bundle.py --secret"));
    assert!(!launch_report.contains("sk-secretsecretsecretsecret"));
    assert!(!launch_report.contains("scripts/build_twinpaper_evidence_bundle.py --secret"));
    assert!(!validation_packet.contains("sk-secretsecretsecretsecret"));
    assert!(!validation_packet.contains("scripts/build_twinpaper_evidence_bundle.py --secret"));

    let telegram_env = temp.path().join("telegram.env");
    write_file(
        &telegram_env,
        "TELEGRAM_BOT_TOKEN=fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n",
    )?;
    let relay_output = Command::new("python3")
        .env("HOME", temp.path())
        .env("XDG_CONFIG_HOME", temp.path().join(".config"))
        .arg(script_path("prepare_twinpaper_offdesk_task.py"))
        .arg("--repo")
        .arg(&repo)
        .arg("--out-root")
        .arg(temp.path().join("prepare-with-telegram"))
        .arg("--duration-minutes")
        .arg("0.1")
        .arg("--max-iterations")
        .arg("1")
        .arg("--role-gate-result")
        .arg(&role_gate)
        .arg("--review-artifact")
        .arg(&review)
        .arg("--council-mode")
        .arg("prompt-package")
        .arg("--council-operator-decision-relay")
        .arg("telegram")
        .arg("--telegram-env-file")
        .arg(&telegram_env)
        .arg("--telegram-decision-dry-run")
        .arg("--wiki-candidate-mode")
        .arg("disabled")
        .arg("--wiki-trial-mode")
        .arg("disabled")
        .output()?;

    assert!(
        relay_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&relay_output.stdout),
        String::from_utf8_lossy(&relay_output.stderr)
    );
    let relay_prepared: Value = serde_json::from_slice(&relay_output.stdout)?;
    let relay_manifest_path = PathBuf::from(
        relay_prepared["manifest"]
            .as_str()
            .expect("relay manifest path"),
    );
    let relay_manifest_text = fs::read_to_string(&relay_manifest_path)?;
    let relay_manifest: Value = serde_json::from_str(&relay_manifest_text)?;
    assert_eq!(
        relay_manifest["preflight"]["council"]["operator_decision_relay"]["mode"],
        "telegram"
    );
    assert_eq!(
        relay_manifest["preflight"]["council"]["operator_decision_relay"]["ready"],
        true
    );
    assert!(relay_manifest["workload_command"]
        .as_array()
        .expect("workload command")
        .iter()
        .any(|arg| arg.as_str() == Some("--council-operator-decision-relay")));
    assert!(!relay_manifest_text.contains("fake-token-for-test"));
    assert!(!relay_manifest_text.contains("123456789"));
    Ok(())
}

#[test]
fn telegram_decision_relay_accepts_request_id_scoped_decision_without_leaking_secrets() -> Result<()>
{
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_file(
        &env_path,
        "TELEGRAM_BOT_TOKEN=fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n",
    )?;
    let request_path = temp.path().join("request.json");
    let episode_path = temp.path().join("episode.json");
    write_file(
        &episode_path,
        &serde_json::to_string_pretty(&json!({
            "iteration": 8,
            "case": "research_reportability_status_json",
            "passed": false,
            "failure_category": "contract_anchor_failure",
            "must_missing": ["primary_objective_gate"],
            "json": {
                "blocking_evidence": [
                    "primary objective gate failed despite execution",
                    "direction_review_checks.nooption_primary_validated_gate_pass = false",
                    "direction_review_checks.nooption_restart_validated_rate_gate_pass = false",
                    "direction_review_checks.nooption_q_gate_pass = false (in some paired runs)"
                ],
                "next_action": [
                    "diagnose primary objective gate failure",
                    "do not promote evidence to reportable claim"
                ],
                "baseline_evidence_status": "executed_primary_gate_failed",
                "claim_status": "pending_not_reportable"
            }
        }))?,
    )?;
    let council_path = temp.path().join("council.json");
    write_file(
        &council_path,
        &serde_json::to_string_pretty(&json!({
            "consensus": {
                "decision": "revise",
                "agreement": true,
                "requires_operator_review": true,
                "reviewer_decisions": {
                    "gpt": "revise",
                    "claude": "revise"
                },
                "evidence_gaps": ["missing_contract_anchor"]
            }
        }))?,
    )?;
    write_file(
        &request_path,
        &serde_json::to_string_pretty(&json!({
            "decision_request_id": "relay-test-1",
            "message_type": "council_decision",
            "title": "test council decision",
            "summary": {
                "council_decision": "needs_council_execution",
                "safety_boundary": "continuation only"
            },
            "artifacts": {
                "episode_record": episode_path,
                "council": council_path
            }
        }))?,
    )?;
    let out = temp.path().join("relay_result.json");

    let output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&request_path)
        .arg("--out")
        .arg(&out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("relay-test-1 continue because operator reviewed the prompt package")
        .arg("--dry-run")
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result_text = fs::read_to_string(&out)?;
    let result: Value = serde_json::from_str(&result_text)?;
    assert_telegram_card_quality(
        &result,
        TelegramCardQualitySpec {
            message_type: "council_decision",
            required_message: &[
                "수정 권고",
                "보고 가능성 상태 점검",
                "현재 결과는 reportable claim으로 승격할 수 없습니다",
                "Council: 수정 권고, 리뷰어 합의",
                "어떻게 진행할까요",
                "다음 episode 진행 방식만 승인",
            ],
            required_detail: &[
                "왜 이 추천인가",
                "실패 요약",
                "핵심 근거",
                "Council 판단",
                "답장 예시",
            ],
            required_buttons: &["계속", "수정(권장)", "보류", "중단", "근거 보기"],
            forbidden_user_surface: &["relay-test-1", "episode.json", "council_decision"],
            max_primary_lines: 14,
        },
    );
    assert_eq!(result["status"], "accepted");
    assert_eq!(result["decision"], "continue");
    assert!(result["target_chat_id_hash"]
        .as_str()
        .expect("target chat hash")
        .starts_with("sha256:"));
    assert_eq!(result["reply_keyboard_cleanup"]["enabled"], true);
    assert_eq!(result["reply_keyboard_cleanup"]["attempted"], false);
    assert_eq!(result["keyboard"]["labels"][0], "계속");
    assert!(result["keyboard"]["labels"]
        .as_array()
        .expect("keyboard labels")
        .iter()
        .any(|label| label.as_str() == Some("수정(권장)")));
    assert!(result["keyboard"]["labels"]
        .as_array()
        .expect("keyboard labels")
        .iter()
        .any(|label| label.as_str() == Some("근거 보기")));
    assert!(!result["keyboard"]["labels"]
        .as_array()
        .expect("keyboard labels")
        .iter()
        .any(|label| label.as_str() == Some("자료")
            || label.as_str() == Some("ID 복사")
            || label.as_str() == Some("더 자세히")));
    assert_eq!(result["message_type"], "council_decision");
    assert_eq!(result["keyboard"]["natural_input_required"][0], "revise");
    assert_eq!(result["keyboard"]["natural_input_required"][1], "block");
    assert_eq!(result["approval_brief_schema"], "approval_brief.v1");
    let first_state_path = PathBuf::from(result["state_path"].as_str().expect("state path"));
    let first_state: Value = serde_json::from_slice(&fs::read(&first_state_path)?)?;
    assert_eq!(
        first_state["request"]["approval_brief"]["schema"],
        "approval_brief.v1"
    );
    assert_eq!(
        first_state["request"]["approval_brief"]["source"],
        "operator_brief"
    );
    assert_eq!(
        first_state_path
            .file_name()
            .and_then(|name| name.to_str())
            .expect("state file name"),
        "relay_result.telegram_decision_state.json"
    );
    let message_preview = result["message_preview"].as_str().expect("message preview");
    assert!(message_preview.contains("수정 권고"));
    assert!(message_preview.contains("보고 가능성 상태 점검"));
    assert!(message_preview.contains("현재 결과는 reportable claim으로 승격할 수 없습니다"));
    assert!(message_preview.contains("이유: primary_objective_gate 미통과"));
    assert!(message_preview.contains("Council: 수정 권고, 리뷰어 합의"));
    assert!(message_preview.contains("질문"));
    assert!(message_preview.contains("어떻게 진행할까요"));
    assert!(message_preview.contains("수정/보류"));
    assert!(message_preview.contains("설명 답장"));
    assert!(!message_preview.contains("실패 요약"));
    assert!(!message_preview.contains("핵심 근거"));
    assert!(!message_preview.contains("Council 판단"));
    assert!(!message_preview.contains("요청 ID"));
    assert!(!message_preview.contains("relay-test-1"));
    assert!(!message_preview.contains("episode.json"));
    assert!(!message_preview.contains("<pre>"));
    assert!(!message_preview.contains("council_decision"));
    let detail_preview = result["detail_preview"].as_str().expect("detail preview");
    assert!(detail_preview.contains("수정 권고의 근거"));
    assert!(detail_preview.contains("왜 이 추천인가"));
    assert!(detail_preview.contains("<blockquote expandable>"));
    assert!(detail_preview.contains("선택별 의미"));
    assert!(detail_preview.contains("답장 예시"));
    assert!(!detail_preview.contains("episode.json"));
    assert!(!result_text.contains("fake-token-for-test"));
    assert!(!result_text.contains("123456789"));

    let explicit_request_path = temp.path().join("explicit_approval_request.json");
    write_file(
        &explicit_request_path,
        &serde_json::to_string_pretty(&json!({
            "decision_request_id": "approval-test-1",
            "message_type": "approval_request",
            "title": "provider fallback approval",
            "approval_brief": {
                "schema": "approval_brief.v1",
                "recommendation": "approve",
                "subject": "provider fallback",
                "summary_lines": [
                    "Provider/model retargeting is waiting for operator approval.",
                    "Reason: provider capacity cooldown active.",
                    "Candidate: openai model gpt-4.1-mini."
                ],
                "why_recommendation": [
                    "openai model gpt-4.1 is currently blocked by provider capacity state.",
                    "openai model gpt-4.1-mini is the first currently recommended fallback candidate."
                ],
                "evidence": [
                    "primary provider timeout rate increased",
                    "fallback provider cost is unknown",
                    "no approval exists for provider retargeting",
                    "operator scope is continuation only"
                ],
                "decision_impacts": {
                    "approve": "Retarget only this request; runtime dispatch still needs its own approval.",
                    "deny": "Keep the current provider/model queued until capacity recovers.",
                    "defer": "Leave the approval pending while reviewing cost, quality, or capacity evidence."
                },
                "options": [
                    {
                        "id": "approve",
                        "label": "Approve fallback",
                        "description": "Retarget only this request; runtime dispatch still needs its own approval."
                    },
                    {
                        "id": "deny",
                        "label": "Deny fallback",
                        "description": "Keep the current provider/model queued until capacity recovers.",
                        "natural_input_prompt": "Explain why this fallback should not be applied."
                    },
                    {
                        "id": "defer",
                        "label": "Need more detail",
                        "description": "Leave the approval pending while reviewing cost, quality, or capacity evidence.",
                        "natural_input_prompt": "State what provider, cost, or quality evidence you need first."
                    }
                ],
                "reply_examples": {
                    "deny": "fallback 후보와 비용 한계를 정리할 때까지 거부해."
                },
                "scope": "Approves provider/model retargeting for this request only; does not approve runtime dispatch, command/workdir changes, cleanup, or wiki promotion.",
                "question": "Approve this provider fallback retargeting?"
            }
        }))?,
    )?;
    let explicit_out = temp.path().join("explicit_approval_result.json");
    let explicit_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&explicit_request_path)
        .arg("--out")
        .arg(&explicit_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("approval-test-1 2 fallback 후보와 비용 한계를 정리할 때까지 거부")
        .arg("--dry-run")
        .output()?;

    assert!(
        explicit_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&explicit_output.stdout),
        String::from_utf8_lossy(&explicit_output.stderr)
    );
    let explicit: Value = serde_json::from_slice(&fs::read(&explicit_out)?)?;
    assert_telegram_card_quality(
        &explicit,
        TelegramCardQualitySpec {
            message_type: "approval_request",
            required_message: &[
                "승인 권고: provider fallback",
                "Provider/model retargeting is waiting for operator approval",
                "선택지",
                "Approve this provider fallback retargeting?",
                "does not approve runtime dispatch",
            ],
            required_detail: &["왜 이 추천인가", "핵심 근거", "선택별 의미", "답장 예시"],
            required_buttons: &[
                "1. Approve fallback(권장)",
                "2. Deny fallback",
                "3. Need more detail",
                "근거 보기",
            ],
            forbidden_user_surface: &["approval-test-1"],
            max_primary_lines: 16,
        },
    );
    assert_eq!(explicit["status"], "accepted");
    assert_eq!(explicit["decision"], "deny");
    assert_eq!(explicit["message_type"], "approval_request");
    assert_eq!(explicit["approval_brief_schema"], "approval_brief.v1");
    assert_eq!(explicit["approval_brief_validation"]["valid"], true);
    assert!(explicit["keyboard"]["labels"]
        .as_array()
        .expect("explicit labels")
        .iter()
        .any(|label| label.as_str() == Some("1. Approve fallback(권장)")));
    assert_eq!(explicit["keyboard"]["natural_input_required"][0], "deny");
    assert_eq!(explicit["keyboard"]["natural_input_required"][1], "defer");
    let explicit_preview = explicit["message_preview"]
        .as_str()
        .expect("explicit message preview");
    assert!(explicit_preview.contains("승인 권고: provider fallback"));
    assert!(explicit_preview.contains("선택지"));
    assert!(explicit_preview.contains("1. Approve fallback"));
    assert!(explicit_preview.contains("Approve this provider fallback retargeting?"));
    assert!(explicit_preview.contains("does not approve runtime dispatch"));
    assert!(!explicit_preview.contains("approval-test-1"));
    let explicit_detail = explicit["detail_preview"]
        .as_str()
        .expect("explicit detail preview");
    assert!(explicit_detail.contains("승인 권고의 근거"));
    assert!(explicit_detail.contains("선택별 의미"));
    assert!(explicit_detail.contains("fallback 후보와 비용 한계를 정리할 때까지 거부"));

    let invalid_request_path = temp.path().join("invalid_approval_request.json");
    write_file(
        &invalid_request_path,
        &serde_json::to_string_pretty(&json!({
            "decision_request_id": "invalid-approval-1",
            "message_type": "approval_request",
            "approval_brief": {
                "schema": "approval_brief.v1",
                "recommendation": "approve",
                "subject": "provider fallback",
                "summary_lines": [
                    "Read /tmp/raw/episode.json for invalid-approval-1 before deciding."
                ],
                "scope": "Approves all requested actions.",
                "options": [
                    {
                        "id": "approve",
                        "label": "Approve fallback",
                        "description": "Approve everything."
                    }
                ]
            },
            "artifacts": {
                "episode_record": "/tmp/raw/episode.json"
            }
        }))?,
    )?;
    let invalid_out = temp.path().join("invalid_approval_result.json");
    let invalid_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&invalid_request_path)
        .arg("--out")
        .arg(&invalid_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--dry-run")
        .output()?;

    assert_eq!(invalid_output.status.code(), Some(3));
    let invalid: Value = serde_json::from_slice(&fs::read(&invalid_out)?)?;
    assert_eq!(invalid["status"], "error");
    let invalid_error = invalid["error"].as_str().expect("invalid error");
    assert!(invalid_error.contains("approval_brief_validation_failed"));
    assert!(invalid_error.contains("question:missing"));
    assert!(invalid_error.contains("scope:missing_non_authorized_boundary"));
    assert!(invalid_error.contains("summary_lines[0]:raw_path"));
    assert!(invalid_error.contains("summary_lines[0]:artifact_filename"));
    assert!(invalid_error.contains("summary_lines[0]:request_id_leak"));

    let producer_probe = temp.path().join("producer_probe.py");
    write_file(
        &producer_probe,
        r#"
import importlib.util
import json
import pathlib
import sys
import types

script, episode_path, council_path, out_dir = sys.argv[1:]
spec = importlib.util.spec_from_file_location("workload", script)
workload = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = workload
spec.loader.exec_module(workload)
args = types.SimpleNamespace(task_id="producer-task-1", request_id="producer-request-1")
council_record = {
    "iteration": 8,
    "case": "research_reportability_status_json",
    "mode": "mock",
    "returncode": 0,
    "episode_record_path": episode_path,
    "council_path": council_path,
    "decision": "revise",
    "agreement": True,
    "requires_operator_review": True,
    "reviewer_decisions": {"gpt": "revise", "claude": "revise"},
}
request = workload.build_operator_decision_request(
    args=args,
    out_dir=pathlib.Path(out_dir),
    council_record=council_record,
)
print(json.dumps(request, ensure_ascii=False))
"#,
    )?;
    let producer_output = Command::new("python3")
        .arg(&producer_probe)
        .arg(script_path("offdesk_twinpaper_autonomy_workload.py"))
        .arg(&episode_path)
        .arg(&council_path)
        .arg(temp.path())
        .output()?;

    assert!(
        producer_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&producer_output.stdout),
        String::from_utf8_lossy(&producer_output.stderr)
    );
    let producer_request: Value = serde_json::from_slice(&producer_output.stdout)?;
    assert_eq!(
        producer_request["approval_brief"]["schema"],
        "approval_brief.v1"
    );
    assert_eq!(
        producer_request["approval_brief"]["source"],
        "offdesk_twinpaper_autonomy_workload"
    );
    assert_eq!(
        producer_request["approval_brief"]["recommendation"],
        "revise"
    );
    assert!(producer_request["approval_brief"]["summary_lines"]
        .as_array()
        .expect("producer summary lines")
        .iter()
        .any(|line| line
            .as_str()
            .unwrap_or_default()
            .contains("reportable claim으로 승격할 수 없습니다")));
    assert!(producer_request["approval_brief"]["evidence"]
        .as_array()
        .expect("producer evidence")
        .iter()
        .any(|line| line
            .as_str()
            .unwrap_or_default()
            .contains("primary objective gate failed")));

    let natural_out = temp.path().join("relay_result_natural.json");
    let natural_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&request_path)
        .arg("--out")
        .arg(&natural_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("좋아 진행해")
        .arg("--dry-run")
        .output()?;

    assert!(
        natural_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&natural_output.stdout),
        String::from_utf8_lossy(&natural_output.stderr)
    );
    let natural: Value = serde_json::from_slice(&fs::read(&natural_out)?)?;
    assert_eq!(natural["status"], "accepted");
    assert_eq!(natural["decision"], "continue");
    assert_eq!(natural["matched_request_id"], false);
    assert_eq!(natural["text_scope"]["reason"], "single_active_request");

    let ambiguous_registry = temp.path().join("ambiguous_active_requests.json");
    write_file(
        &ambiguous_registry,
        &serde_json::to_string_pretty(&json!({
            "schema": "telegram_active_requests.v1",
            "entries": [
                {
                    "key": "other-active-request",
                    "status": "pending",
                    "message_type": "council_decision",
                    "request_id_hash": "other",
                    "target_chat_id_hash": "sha256:other",
                    "created_at": "2026-05-28T00:00:00+00:00",
                    "expires_at": "2999-01-01T00:00:00+00:00"
                }
            ]
        }))?,
    )?;
    let ambiguous_out = temp.path().join("relay_result_ambiguous.json");
    let ambiguous_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&request_path)
        .arg("--out")
        .arg(&ambiguous_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--active-request-registry")
        .arg(&ambiguous_registry)
        .arg("--decision-text")
        .arg("좋아 진행해")
        .arg("--dry-run")
        .output()?;

    assert_eq!(ambiguous_output.status.code(), Some(2));
    let ambiguous: Value = serde_json::from_slice(&fs::read(&ambiguous_out)?)?;
    assert_eq!(ambiguous["status"], "ambiguous_input");
    assert_eq!(ambiguous["decision"], Value::Null);
    assert_eq!(ambiguous["candidate_decision"], "continue");
    assert_eq!(
        ambiguous["reason"],
        "unscoped_text_matches_multiple_active_requests"
    );
    assert_eq!(ambiguous["text_scope"]["active_request_count"], 2);

    let live_probe = temp.path().join("telegram_live_probe.py");
    write_file(
        &live_probe,
        r#"
import importlib.util
import json
import pathlib
import sys

script, registry_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("relay", script)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)

registry = pathlib.Path(registry_path)
out_path = registry.with_name("live_poll_result.json")
request = relay.request_with_approval_brief({
    "decision_request_id": "relay-live-1",
    "message_type": "council_decision",
    "approval_brief": {
        "schema": "approval_brief.v1",
        "recommendation": "revise",
        "subject": "보고 가능성 상태 점검",
        "summary_lines": [
            "현재 결과는 reportable claim으로 승격할 수 없습니다.",
            "Council: 수정 권고, 리뷰어 합의."
        ],
        "scope": "다음 episode 진행 방식만 승인합니다. 파일 변경, cleanup, provider 변경, wiki 승인은 별도 승인입니다.",
        "question": "어떻게 진행할까요?",
        "decision_impacts": {
            "continue": "현재 경고를 감수하고 다음 episode로 진행합니다.",
            "revise": "자연어로 수정 방향을 남기고 다음 episode를 그 방향으로 진행합니다.",
            "block": "지금은 멈추고 재개 조건이나 추가 확인이 필요하다고 기록합니다.",
            "stop": "이 런을 닫고 closeout 또는 별도 검토로 전환합니다."
        }
    }
})
request_id = relay.request_id_for(request, "relay-live-1")
state = relay.build_decision_state(request, request_id, out_path)
state["telegram_message_id"] = 777
current_key = relay.active_request_key(state)
relay.write_json(registry, {
    "schema": "telegram_active_requests.v1",
    "entries": [
        {
            "key": "other-active-request",
            "status": "pending",
            "message_type": "council_decision",
            "request_id_hash": "other",
            "target_chat_id_hash": "sha256:other",
            "created_at": "2026-05-28T00:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00"
        },
        {
            "key": current_key,
            "status": "pending",
            "message_type": "council_decision",
            "request_id_hash": "current",
            "target_chat_id_hash": "sha256:current",
            "created_at": "2026-05-28T00:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00"
        }
    ]
})

updates = [
    [
        {
            "update_id": 1,
            "message": {
                "message_id": 11,
                "chat": {"id": 123},
                "text": "좋아 진행해"
            }
        }
    ],
    [
        {
            "update_id": 2,
            "message": {
                "message_id": 12,
                "chat": {"id": 123},
                "reply_to_message": {"message_id": 777},
                "text": "좋아 진행해"
            }
        }
    ]
]
sent = []

def fake_get_updates(_token, *, offset, poll_timeout_sec):
    del offset, poll_timeout_sec
    return updates.pop(0) if updates else []

def fake_send_message(_token, chat_id, message, **kwargs):
    sent.append({"chat_id": str(chat_id), "message": message, "kwargs": kwargs})
    return 900 + len(sent)

relay.get_updates = fake_get_updates
relay.send_message = fake_send_message
relay.time.sleep = lambda _seconds: None

result = relay.poll_for_decision(
    token="fake-token",
    accepted_chat_ids={"123"},
    request_id=request_id,
    state=state,
    offset=0,
    timeout_sec=2,
    poll_interval_sec=0.2,
    active_registry_path=registry,
)
assert result["status"] == "accepted", result
assert result["decision"] == "continue", result
assert result["text_scope"]["reason"] == "telegram_reply_to_decision_card", result
assert len(result["ambiguous_events"]) == 1, result
assert result["ambiguous_events"][0]["reason"] == "unscoped_text_matches_multiple_active_requests", result
assert result["ambiguous_events"][0]["text_scope"]["active_request_count"] == 2, result
assert len(sent) == 1, sent
assert "확인이 필요" in sent[0]["message"], sent
print(json.dumps({"result": result, "sent": sent}, ensure_ascii=False))
"#,
    )?;
    let live_output = Command::new("python3")
        .arg(&live_probe)
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg(temp.path().join("live_active_requests.json"))
        .output()?;

    assert!(
        live_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&live_output.stdout),
        String::from_utf8_lossy(&live_output.stderr)
    );

    let registry_probe = temp.path().join("telegram_registry_probe.py");
    write_file(
        &registry_probe,
        r#"
import importlib.util
import json
import multiprocessing
import pathlib
import subprocess
import sys

script, registry_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("relay", script)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)

registry = pathlib.Path(registry_path)

def build_state(name):
    request = relay.request_with_approval_brief({
        "decision_request_id": f"registry-{name}",
        "message_type": "council_decision",
        "approval_brief": {
            "schema": "approval_brief.v1",
            "recommendation": "continue",
            "subject": f"registry probe {name}",
            "summary_lines": [f"Registry probe request {name} is waiting."],
            "scope": "다음 polling decision만 승인합니다. 파일 변경, cleanup, provider 변경, wiki 승인은 별도 승인입니다.",
            "question": "Proceed with this registry probe?",
        },
    })
    request_id = relay.request_id_for(request, f"registry-{name}")
    state = relay.build_decision_state(request, request_id, registry.with_name(f"registry_{name}.json"))
    return state, request_id

relay.write_json(registry, {
    "schema": "telegram_active_requests.v1",
    "entries": [
        {
            "key": "expired-request",
            "status": "pending",
            "message_type": "council_decision",
            "request_id_hash": "expired",
            "target_chat_id_hash": "sha256:expired",
            "created_at": "2026-05-28T00:00:00+00:00",
            "expires_at": "2000-01-01T00:00:00+00:00",
        },
        {
            "key": "completed-request",
            "status": "accepted",
            "message_type": "council_decision",
            "request_id_hash": "accepted",
            "target_chat_id_hash": "sha256:accepted",
            "created_at": "2026-05-28T00:00:00+00:00",
            "expires_at": "2999-01-01T00:00:00+00:00",
        },
        "malformed-entry",
    ],
})

def register(name):
    state, request_id = build_state(name)
    return relay.register_active_request(
        registry,
        state=state,
        request_id=request_id,
        message_type="council_decision",
        target_chat_id_hash="sha256:test-chat",
        timeout_sec=60,
    )

with multiprocessing.get_context("fork").Pool(2) as pool:
    guards = pool.map(register, ["a", "b"])

active = relay.active_registry_entries(relay.load_active_registry(registry))
active_outs = {pathlib.Path(entry["out_path"]).name for entry in active}
assert active_outs == {"registry_a.json", "registry_b.json"}, active
assert relay.active_request_count(registry) == 2, relay.load_active_registry(registry)
assert any(guard["stale_removed"] >= 1 for guard in guards), guards
assert all(guard["write_mode"] == "locked_atomic" for guard in guards), guards
assert not list(registry.parent.glob(f".{registry.name}.*.tmp"))

lock_snippet = """
import importlib.util
import pathlib
import sys

script, registry_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("relay", script)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)
with relay.locked_active_registry(pathlib.Path(registry_path), timeout_sec=0.1):
    print("unexpected-lock")
"""
lock_test = subprocess.run(
    [
        sys.executable,
        "-c",
        lock_snippet,
        script,
        str(registry),
    ],
    capture_output=True,
    text=True,
)
with relay.locked_active_registry(registry, timeout_sec=0.1):
    blocked = subprocess.run(
        [
            sys.executable,
            "-c",
            lock_snippet,
            script,
            str(registry),
        ],
        capture_output=True,
        text=True,
    )
assert lock_test.returncode == 0, lock_test.stderr
assert blocked.returncode != 0, blocked.stdout + blocked.stderr
assert "active_request_registry_lock_timeout" in blocked.stderr, blocked.stdout + blocked.stderr

state_a, _ = build_state("a")
relay.complete_active_request(registry, state_a, status="accepted")
assert relay.active_request_count(registry) == 1, relay.load_active_registry(registry)
state_b, _ = build_state("b")
relay.complete_active_request(registry, state_b, status="ambiguous_input")
assert relay.active_request_count(registry) == 1, relay.load_active_registry(registry)
relay.complete_active_request(registry, state_b, status="accepted")
assert relay.active_request_count(registry) == 0, relay.load_active_registry(registry)
final_registry = relay.load_active_registry(registry)
assert final_registry["write_mode"] == "locked_atomic", final_registry
assert relay.active_registry_lock_path(registry).exists()
print(json.dumps({"guards": guards, "final_registry": final_registry}, ensure_ascii=False))
"#,
    )?;
    let registry_output = Command::new("python3")
        .arg(&registry_probe)
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg(temp.path().join("registry_atomic_probe.json"))
        .output()?;

    assert!(
        registry_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&registry_output.stdout),
        String::from_utf8_lossy(&registry_output.stderr)
    );

    let failure_cleanup_probe = temp.path().join("telegram_failure_cleanup_probe.py");
    write_file(
        &failure_cleanup_probe,
        r#"
import importlib.util
import json
import pathlib
import sys
import types

script, root = sys.argv[1:]
root = pathlib.Path(root)
spec = importlib.util.spec_from_file_location("relay", script)
relay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = relay
spec.loader.exec_module(relay)

env_path = root / "failure_cleanup.env"
env_path.write_text("TELEGRAM_BOT_TOKEN=fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n", encoding="utf-8")
request_path = root / "failure_cleanup_request.json"
out_path = root / "failure_cleanup_result.json"
registry = root / "failure_cleanup_active_requests.json"
relay.write_json(request_path, {
    "decision_request_id": "failure-cleanup-1",
    "message_type": "council_decision",
    "approval_brief": {
        "schema": "approval_brief.v1",
        "recommendation": "continue",
        "subject": "send failure cleanup",
        "summary_lines": ["A live Telegram send failure must not leave an active request behind."],
        "scope": "다음 polling decision만 승인합니다. 파일 변경, cleanup, provider 변경, wiki 승인은 별도 승인입니다.",
        "question": "Proceed with this failure cleanup probe?",
    },
})

relay.current_update_offset = lambda _token: 0
relay.cleanup_reply_keyboard = lambda *_args, **_kwargs: {
    "enabled": True,
    "attempted": True,
    "status": "mocked",
}

def fail_send_message(*_args, **_kwargs):
    raise relay.RelayError("mock_send_failure")

relay.send_message = fail_send_message
args = types.SimpleNamespace(
    request=request_path,
    out=out_path,
    env_file=env_path,
    timeout_sec=60,
    poll_interval_sec=0.2,
    dry_run=False,
    keep_reply_keyboard=False,
    decision_text=None,
    active_request_registry=registry,
)
try:
    relay.run(args)
except relay.RelayError as error:
    assert "mock_send_failure" in str(error), error
else:
    raise AssertionError("expected mocked send failure")

registry_json = relay.load_active_registry(registry)
assert relay.active_request_count(registry) == 0, registry_json
assert registry_json["write_mode"] == "locked_atomic", registry_json
state_files = sorted(path.name for path in root.glob("failure_cleanup_result.telegram_decision_state.json"))
assert state_files == ["failure_cleanup_result.telegram_decision_state.json"], state_files
assert not (root / "telegram_decision_state.json").exists()
print(json.dumps({"registry": registry_json, "state_files": state_files}, ensure_ascii=False))
"#,
    )?;
    let failure_cleanup_output = Command::new("python3")
        .arg(&failure_cleanup_probe)
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg(temp.path())
        .output()?;

    assert!(
        failure_cleanup_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&failure_cleanup_output.stdout),
        String::from_utf8_lossy(&failure_cleanup_output.stderr)
    );

    let revise_out = temp.path().join("relay_result_revise.json");
    let revise_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&request_path)
        .arg("--out")
        .arg(&revise_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("수정 evidence path를 다시 확인해")
        .arg("--dry-run")
        .output()?;

    assert!(
        revise_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&revise_output.stdout),
        String::from_utf8_lossy(&revise_output.stderr)
    );
    let revise: Value = serde_json::from_slice(&fs::read(&revise_out)?)?;
    assert_eq!(revise["status"], "accepted");
    assert_eq!(revise["decision"], "revise");
    assert!(revise["reason"]
        .as_str()
        .expect("revise reason")
        .contains("evidence path"));

    let direction_request_path = temp.path().join("direction_request.json");
    write_file(
        &direction_request_path,
        &serde_json::to_string_pretty(&json!({
            "decision_request_id": "plan-choice-1",
            "message_type": "direction_choice",
            "title": "choose tomorrow morning autonomy direction",
            "summary": {
                "recommendation": "pick a bounded validation direction"
            },
            "options": [
                {
                    "id": "stabilize_first",
                    "label": "안정화 먼저",
                    "description": "기존 Telegram/council relay를 굳히고 짧은 테스트런을 반복한다."
                },
                {
                    "id": "expand_templates",
                    "label": "템플릿 확장",
                    "description": "execution failure와 artifact review 타입까지 템플릿을 넓힌다."
                }
            ]
        }))?,
    )?;
    let direction_out = temp.path().join("direction_result.json");
    let direction_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&direction_request_path)
        .arg("--out")
        .arg(&direction_out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("기타 안정화를 먼저 하되 템플릿 확장은 문서만 남겨")
        .arg("--dry-run")
        .output()?;

    assert!(
        direction_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&direction_output.stdout),
        String::from_utf8_lossy(&direction_output.stderr)
    );
    let direction: Value = serde_json::from_slice(&fs::read(&direction_out)?)?;
    assert_telegram_card_quality(
        &direction,
        TelegramCardQualitySpec {
            message_type: "direction_choice",
            required_message: &[
                "방향 선택",
                "선택지",
                "1. 안정화 먼저",
                "2. 템플릿 확장",
                "기타",
                "어떤 방향으로 진행할까요",
            ],
            required_detail: &["상세 정보 부족", "선택별 의미"],
            required_buttons: &["1. 안정화 먼저", "2. 템플릿 확장", "기타", "근거 보기"],
            forbidden_user_surface: &["plan-choice-1"],
            max_primary_lines: 16,
        },
    );
    assert_eq!(direction["status"], "accepted");
    assert_eq!(direction["message_type"], "direction_choice");
    assert_eq!(direction["decision"], "custom_direction");
    assert_eq!(direction["keyboard"]["labels"][0], "1. 안정화 먼저");
    assert_eq!(
        direction["keyboard"]["natural_input_required"][0],
        "custom_direction"
    );
    let direction_preview = direction["message_preview"]
        .as_str()
        .expect("direction preview");
    assert!(direction_preview.contains("방향 선택"));
    assert!(direction_preview.contains("선택지"));
    assert!(direction_preview.contains("1. 안정화 먼저"));
    assert!(direction_preview.contains("기타"));
    assert!(direction["reason"]
        .as_str()
        .expect("custom reason")
        .contains("안정화를 먼저"));
    let direction_state_path = PathBuf::from(
        direction["state_path"]
            .as_str()
            .expect("direction state path"),
    );
    let state_text = fs::read_to_string(&direction_state_path)?;
    let state: Value = serde_json::from_str(&state_text)?;
    assert_eq!(
        direction_state_path
            .file_name()
            .and_then(|name| name.to_str())
            .expect("direction state file name"),
        "direction_result.telegram_decision_state.json"
    );
    assert!(!temp.path().join("telegram_decision_state.json").exists());
    assert_eq!(state["status"], "accepted");
    assert!(state.get("tokens").is_none());
    assert!(state["token_hashes"].is_object());
    assert!(!state_text.contains("fake-token-for-test"));
    assert!(!state_text.contains("123456789"));
    Ok(())
}

#[test]
fn telegram_ondesk_handoff_uses_webui_link_without_path_dump() -> Result<()> {
    let temp = tempdir()?;
    let env_path = temp.path().join("telegram.env");
    write_file(
        &env_path,
        "TELEGRAM_BOT_TOKEN=fake-token-for-test\nTELEGRAM_OWNER_CHAT_ID=123456789\n",
    )?;
    let closeout_dir = temp.path().join("closeout");
    write_file(
        &closeout_dir.join("closeout_plan.json"),
        &serde_json::to_string_pretty(&json!({
            "closeout_id": "closeout_test",
            "generated_at": "2026-05-28T01:10:58Z",
            "summary": {
                "tasks_scanned": 15,
                "completed_tasks": 13,
                "missing_artifacts": 4,
                "archive_candidates": 27
            },
            "open_decisions": [
                {
                    "kind": "missing_artifact",
                    "detail": "4 referenced artifacts are missing or not yet observed."
                },
                {
                    "kind": "archive_review",
                    "detail": "27 archive candidates require commercial review and human approval."
                },
                {
                    "kind": "git_state_review",
                    "detail": "Git state is included and must be reviewed before Ondesk return."
                }
            ]
        }))?,
    )?;
    write_file(
        &closeout_dir.join("closeout_review_20260528T011100Z.json"),
        &serde_json::to_string_pretty(&json!({
            "verdict": "approved_with_followups"
        }))?,
    )?;
    let prompt_package = temp.path().join("ondesk_prompt_package.md");
    write_file(&prompt_package, "# prompt package\n")?;
    let request_path = temp.path().join("ondesk_request.json");
    let builder_output = Command::new("python3")
        .arg(script_path("build_ondesk_handoff_request.py"))
        .arg("--project-key")
        .arg("twinpaper")
        .arg("--subject")
        .arg("TwinPaper")
        .arg("--closeout-artifact-dir")
        .arg(&closeout_dir)
        .arg("--prompt-package")
        .arg(&prompt_package)
        .arg("--webui-url")
        .arg("http://127.0.0.1:3000/ondesk/twinpaper")
        .arg("--handoff-local-time")
        .arg("08:30")
        .arg("--timezone")
        .arg("Asia/Seoul")
        .arg("--now")
        .arg("2026-05-28T08:30:00+09:00")
        .arg("--out")
        .arg(&request_path)
        .output()?;

    assert!(
        builder_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&builder_output.stdout),
        String::from_utf8_lossy(&builder_output.stderr)
    );
    let request: Value = serde_json::from_slice(&fs::read(&request_path)?)?;
    assert_eq!(request["message_type"], "ondesk_handoff");
    assert_eq!(
        request["approval_brief"]["schema"],
        "ondesk_handoff_brief.v1"
    );
    assert_eq!(
        request["approval_brief"]["recommendation"],
        "start_ondesk_review"
    );
    assert_eq!(request["summary"]["open_decisions"], 3);

    let out = temp.path().join("ondesk_relay_result.json");
    let relay_output = Command::new("python3")
        .arg(script_path("offdesk_telegram_decision_relay.py"))
        .arg("--request")
        .arg(&request_path)
        .arg("--out")
        .arg(&out)
        .arg("--env-file")
        .arg(&env_path)
        .arg("--decision-text")
        .arg("좋아 진행하자")
        .arg("--dry-run")
        .output()?;

    assert!(
        relay_output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&relay_output.stdout),
        String::from_utf8_lossy(&relay_output.stderr)
    );
    let result_text = fs::read_to_string(&out)?;
    let result: Value = serde_json::from_str(&result_text)?;
    let temp_path_string = temp.path().to_string_lossy().to_string();
    let ondesk_forbidden = [
        "closeout_test",
        "closeout_plan.json",
        "ondesk_prompt_package.md",
        temp_path_string.as_str(),
    ];
    assert_telegram_card_quality(
        &result,
        TelegramCardQualitySpec {
            message_type: "ondesk_handoff",
            required_message: &[
                "Ondesk 전환 브리핑: TwinPaper",
                "08:30 Asia/Seoul",
                "Closeout 요약",
                "남은 사용자 결정 3건",
                "WebUI에서 ondesk 검토를 시작할까요",
                "Telegram은 ondesk 검토 진입/대기만 기록",
            ],
            required_detail: &[
                "Ondesk 전환 상세",
                "왜 이 추천인가",
                "핵심 근거",
                "선택별 의미",
            ],
            required_buttons: &[
                "WebUI 열기",
                "1. WebUI 검토 시작(권장)",
                "2. 대기 유지",
                "3. 나중에",
                "근거 보기",
            ],
            forbidden_user_surface: &ondesk_forbidden,
            max_primary_lines: 16,
        },
    );
    assert_eq!(result["status"], "accepted");
    assert_eq!(result["message_type"], "ondesk_handoff");
    assert_eq!(result["decision"], "start_ondesk_review");
    assert_eq!(result["keyboard"]["labels"][0], "WebUI 열기");
    assert_eq!(result["keyboard"]["labels"][1], "1. WebUI 검토 시작(권장)");
    let message_preview = result["message_preview"].as_str().expect("message preview");
    assert!(message_preview.contains("Ondesk 전환 브리핑: TwinPaper"));
    assert!(message_preview.contains("08:30 Asia/Seoul"));
    assert!(message_preview.contains("Closeout 요약"));
    assert!(message_preview.contains("남은 사용자 결정 3건"));
    assert!(message_preview.contains("WebUI에서 ondesk 검토를 시작할까요"));
    assert!(message_preview.contains("Telegram은 ondesk 검토 진입/대기만 기록"));
    assert!(!message_preview.contains("closeout_test"));
    assert!(!message_preview.contains("closeout_plan.json"));
    assert!(!message_preview.contains(temp.path().to_str().expect("temp path")));
    let detail_preview = result["detail_preview"].as_str().expect("detail preview");
    assert!(detail_preview.contains("Ondesk 전환 상세"));
    assert!(detail_preview.contains("왜 이 추천인가"));
    assert!(detail_preview.contains("핵심 근거"));
    assert!(detail_preview.contains("선택별 의미"));
    assert!(!result_text.contains("fake-token-for-test"));
    assert!(!result_text.contains("123456789"));
    Ok(())
}

#[test]
fn twinpaper_review_accepts_gate_aliases_and_comparability_language() -> Result<()> {
    let temp = tempdir()?;
    let probe_path = temp.path().join("twinpaper_review_probe.py");
    write_file(
        &probe_path,
        r#"
import importlib.util
import json
import sys

workload_script, review_script = sys.argv[1:]

workload_spec = importlib.util.spec_from_file_location("workload_probe", workload_script)
workload = importlib.util.module_from_spec(workload_spec)
sys.modules[workload_spec.name] = workload
workload_spec.loader.exec_module(workload)

review_spec = importlib.util.spec_from_file_location("review_probe", review_script)
review = importlib.util.module_from_spec(review_spec)
sys.modules[review_spec.name] = review
review_spec.loader.exec_module(review)

matched, alias = workload.term_match(
    "no-option evidence fails primary objective gate despite execution",
    "primary_objective_gate",
)
assert matched, alias
assert alias == "primary objective gate", alias

matched, alias = workload.term_match(
    "no-option primary objective gates are failing",
    "primary_objective_gate",
)
assert matched, alias

critique = (
    "Open-explore has exploratory validated_candidate and p/q evidence, "
    "but it is not directly comparable to promotion-ready direction-review evidence "
    "until the same threshold and restart comparability are checked."
)
assert review.distinguishes_exploratory_from_promotion_gate(critique)

case = workload.WorkloadCase(
    name="research_reportability_status_json",
    prompt="",
    format_json=True,
    must_have=(
        "executed_primary_gate_failed",
        "pending_not_reportable",
        "evidence_refs",
        "validated_candidate",
        "p/q",
        "restart_stability",
        "no-option",
        "singlex",
        "primary_objective_gate",
    ),
    json_required={
        "reportability_contract_schema": "reportability_contract.v1",
        "evidence_bundle_used": True,
        "evidence_review_decision": "sufficient",
        "baseline_evidence_status": "executed_primary_gate_failed",
        "claim_status": "pending_not_reportable",
        "required_metrics": ["validated_candidate", "p/q", "restart_stability"],
        "coupled_modes": ["no-option", "singlex"],
        "runlog_path": "docs/operations/RunLog.md",
        "evidence_refs": ["docs/operations/RunLog.md", "data/metadata"],
    },
)
valid = {
    "reportability_contract_schema": "reportability_contract.v1",
    "evidence_bundle_used": True,
    "evidence_review_decision": "sufficient",
    "baseline_evidence_status": "executed_primary_gate_failed",
    "claim_status": "pending_not_reportable",
    "evidence_available": ["no-option and singlex baseline evidence exists"],
    "blocking_anchors": [
        {
            "id": "primary_objective_gate",
            "status": "failed",
            "reason_code": "executed_primary_gate_failed",
            "evidence_refs": ["data/metadata/phase_b_direction_review.json"],
        }
    ],
    "blocking_evidence": ["no-option evidence fails the gate despite execution"],
    "next_action": ["diagnose the failed gate before promotion"],
    "required_metrics": ["validated_candidate", "p/q", "restart_stability"],
    "coupled_modes": ["no-option", "singlex"],
    "runlog_path": "docs/operations/RunLog.md",
    "evidence_refs": ["docs/operations/RunLog.md L1", "data/metadata/phase_b_direction_review.json"],
}
evaluation = workload.evaluate(case, json.dumps(valid))
assert evaluation["passed"], evaluation
findings = []
review.review_json_case({"case": "research_reportability_status_json", "iteration": 1, "json": valid}, findings)
assert not [finding for finding in findings if finding["severity"] == "blocker"], findings

missing_schema = dict(valid)
missing_schema.pop("reportability_contract_schema")
evaluation = workload.evaluate(case, json.dumps(missing_schema))
assert not evaluation["passed"], evaluation
assert "reportability_contract_schema:expected:reportability_contract.v1" in evaluation["json_failures"], evaluation
findings = []
review.review_json_case(
    {"case": "research_reportability_status_json", "iteration": 1, "json": missing_schema},
    findings,
)
assert any(finding["category"] == "reportability_contract_schema_missing" for finding in findings), findings

missing_structured = dict(valid)
missing_structured.pop("blocking_anchors")
evaluation = workload.evaluate(case, json.dumps(missing_structured))
assert not evaluation["passed"], evaluation
assert "blocking_anchors:not_list" in evaluation["json_failures"], evaluation
findings = []
review.review_json_case(
    {"case": "research_reportability_status_json", "iteration": 1, "json": missing_structured},
    findings,
)
assert any(finding["category"] == "reportability_blocking_anchors_missing" for finding in findings), findings

alias_anchor = dict(valid)
alias_anchor["blocking_anchors"] = [dict(valid["blocking_anchors"][0], id="primary objective gate")]
evaluation = workload.evaluate(case, json.dumps(alias_anchor))
assert not evaluation["passed"], evaluation
assert any(":id:not_canonical:primary objective gate" in item for item in evaluation["json_failures"]), evaluation
findings = []
review.review_json_case({"case": "research_reportability_status_json", "iteration": 1, "json": alias_anchor}, findings)
assert any(finding["category"] == "reportability_blocking_anchor_invalid" for finding in findings), findings
"#,
    )?;

    let output = Command::new("python3")
        .arg(&probe_path)
        .arg(script_path("offdesk_twinpaper_autonomy_workload.py"))
        .arg(script_path("review_twinpaper_offdesk_result.py"))
        .output()?;

    assert!(
        output.status.success(),
        "stdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}
