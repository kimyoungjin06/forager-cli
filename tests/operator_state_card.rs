use anyhow::Result;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn script_path(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("scripts")
        .join(name)
}

fn fixture_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("ui")
        .join("operator_state_cards")
}

fn render_fixture(path: &Path) -> Result<Value> {
    let output = Command::new("python3")
        .arg(script_path("operator_state_card.py"))
        .arg("--fixture")
        .arg(path)
        .output()?;
    assert!(
        output.status.success(),
        "operator_state_card.py failed for {:?}\nstdout:\n{}\nstderr:\n{}",
        path,
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
fn operator_state_card_fixture_set_covers_core_ui_states() -> Result<()> {
    let expected = BTreeSet::from([
        "agent_outage".to_string(),
        "approval_pending".to_string(),
        "closeout_required".to_string(),
        "failed_offdesk_task".to_string(),
        "no_work_pending".to_string(),
        "plan_ready_for_review".to_string(),
    ]);
    let mut actual = BTreeSet::new();
    for entry in fs::read_dir(fixture_dir())? {
        let path = entry?.path();
        if path.extension().and_then(|ext| ext.to_str()) == Some("json") {
            let card: Value = serde_json::from_slice(&fs::read(&path)?)?;
            actual.insert(
                card["id"]
                    .as_str()
                    .unwrap_or_else(|| panic!("fixture {:?} missing id", path))
                    .to_string(),
            );
        }
    }
    assert_eq!(actual, expected);
    Ok(())
}

#[test]
fn operator_state_card_renders_all_surface_projections() -> Result<()> {
    let mut rendered_count = 0;
    for entry in fs::read_dir(fixture_dir())? {
        let path = entry?.path();
        if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
            continue;
        }
        let rendered = render_fixture(&path)?;
        rendered_count += 1;
        assert_eq!(rendered["schema"], "operator_state_card_render.v1");
        assert_eq!(rendered["source_schema"], "operator_state_card.v1");

        let telegram = rendered["telegram"]["text"]
            .as_str()
            .expect("telegram text");
        let first_line = telegram.lines().next().unwrap_or_default();
        assert!(
            first_line.starts_with("<b>") && first_line.ends_with("</b>"),
            "telegram title should be bold:\n{}",
            telegram
        );
        assert!(
            telegram.contains("다음 조치:"),
            "telegram primary card should include the next safe action:\n{}",
            telegram
        );
        for forbidden in [
            "request_id",
            "sha256:",
            "/home/",
            "/tmp/",
            ".telegram_decision_state",
            "runtime_handle_alive",
        ] {
            assert!(
                !telegram.contains(forbidden),
                "telegram card leaked {forbidden} for {:?}:\n{}",
                path,
                telegram
            );
        }

        let contract = &rendered["telegram"]["mobile_card_contract"];
        assert_eq!(contract["schema"], "telegram_mobile_card_contract.v1");
        assert_eq!(
            contract["warnings"].as_array().expect("warnings").len(),
            0,
            "mobile card warnings for {:?}: {:?}\n{}",
            path,
            contract["warnings"],
            telegram
        );
        assert!(
            contract["line_count"].as_u64().expect("line count")
                <= contract["max_lines"].as_u64().expect("max lines"),
            "line budget exceeded for {:?}:\n{}",
            path,
            telegram
        );
        assert!(
            contract["char_count"].as_u64().expect("char count")
                <= contract["max_chars"].as_u64().expect("max chars"),
            "char budget exceeded for {:?}:\n{}",
            path,
            telegram
        );

        let rows = rendered["tui_rows"].as_array().expect("tui rows");
        assert!(rows.len() >= 4, "TUI rows should be non-empty");
        assert!(
            rows.iter().any(|row| row["label"] == "상태"),
            "TUI rows should include status"
        );
        assert!(
            rows.iter().any(|row| row["label"] == "다음 조치"),
            "TUI rows should include next safe action"
        );
        assert!(
            rows.iter().any(|row| row["label"] == "권한 경계"),
            "TUI rows should include authorization boundary"
        );

        let webui = &rendered["webui_card"];
        assert_eq!(webui["schema"], "operator_state_card.webui_card.v1");
        assert_eq!(webui["id"], rendered["id"]);
        assert!(webui["detail_ref"].is_object());
        assert!(webui["next_safe_action"].is_object());
        assert!(webui["authorization_boundary"].as_str().is_some());
    }
    assert_eq!(rendered_count, 6);
    Ok(())
}

#[test]
fn operator_state_card_keeps_detail_refs_out_of_compact_telegram_text() -> Result<()> {
    let fixture = fixture_dir().join("approval_pending.json");
    let rendered = render_fixture(&fixture)?;
    let telegram = rendered["telegram"]["text"]
        .as_str()
        .expect("telegram text");
    assert!(!telegram.contains("/tmp/forager-fixture/approval_pending.json"));
    assert_eq!(
        rendered["webui_card"]["detail_ref"]["path"],
        "/tmp/forager-fixture/approval_pending.json"
    );
    Ok(())
}
