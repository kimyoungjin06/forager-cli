use std::fs;
use std::path::Path;
use std::process::{Command, Output};

use serde_json::{json, Value};
use tempfile::tempdir;

fn document(project: &str) -> String {
    format!(
        "# Owning plan\n```forager-plan\n{}\n```\n",
        json!({
            "schema":"ondesk_task_plan.v1","project_key":project,"tasks":[
                {"task_id":"restore","goal":"restore field evidence","completion_criteria":["read the pinned inputs"],"depends_on":[],"review_state":"unreviewed","evidence_refs":[]},
                {"task_id":"next","goal":"next field task","completion_criteria":["field draft"],"depends_on":["restore"],"review_state":"unreviewed","evidence_refs":[]}
            ]
        })
    )
}

fn run(root: &Path, args: &[String]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_forager"))
        .env("HOME", root)
        .env("XDG_CONFIG_HOME", root.join("config"))
        .env("FORAGER_PROFILE", "invalid/profile/must-not-load")
        .env_remove("TMUX")
        .env_remove("TMUX_PANE")
        .current_dir(root)
        .args(["ondesk", "prepare-offdesk"])
        .args(args)
        .output()
        .unwrap()
}

fn args(project: &str, path: &Path, task: &str) -> Vec<String> {
    vec![
        "--source".into(),
        format!("{project}={}", path.display()),
        "--task".into(),
        format!("{project}/{task}"),
    ]
}

#[test]
fn preparation_is_inert_profile_independent_and_predicts_acceptance_stop() {
    let temp = tempdir().unwrap();
    let root = temp.path();
    let path = root.join("TODO.md");
    let source = document("nano");
    fs::write(&path, &source).unwrap();
    let config = root.join("config/forager");
    fs::create_dir_all(&config).unwrap();
    fs::write(config.join("config.toml"), "[not valid config").unwrap();
    fs::write(config.join(".schema_version"), "5\n").unwrap();
    let out = run(root, &args("nano", &path, "restore"));
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let report: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(report["tasks"][0]["source_eligible"], true);
    assert_eq!(report["dependent_followups"][0]["task_id"], "next");
    assert_eq!(report["ready_for_launch"], false);
    assert_eq!(report["decisions"].as_array().unwrap().len(), 12);
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert_eq!(
        fs::read_to_string(config.join(".schema_version")).unwrap(),
        "5\n"
    );
    assert!(!config.join("profiles").exists());
    assert!(!root.join(".TODO.md.forager-plan-journal.json").exists());
}

#[test]
fn exact_source_hash_rejects_even_narrative_drift_without_writes() {
    let temp = tempdir().unwrap();
    let root = temp.path();
    let path = root.join("TODO.md");
    fs::write(&path, document("nano")).unwrap();
    let mut argv = args("nano", &path, "restore");
    let first = run(root, &argv);
    assert!(first.status.success());
    let report: Value = serde_json::from_slice(&first.stdout).unwrap();
    let hash = report["sources"][0]["source_sha256"].as_str().unwrap();
    argv.extend(["--expected-source-sha256".into(), format!("nano={hash}")]);
    let second = run(root, &argv);
    assert!(second.status.success());
    assert_eq!(first.stdout, second.stdout);
    let changed = format!(
        "{}\nA concurrent source change.\n",
        fs::read_to_string(&path).unwrap()
    );
    fs::write(&path, &changed).unwrap();
    let out = run(root, &argv);
    assert!(!out.status.success());
    assert!(out.stdout.is_empty());
    assert_eq!(fs::read_to_string(&path).unwrap(), changed);
    assert!(!root.join("config").exists());
}

#[test]
fn two_projects_and_a_selected_dependent_keep_scope_and_eligibility_separate() {
    let temp = tempdir().unwrap();
    let root = temp.path();
    let a = root.join("nano.md");
    let b = root.join("forager.md");
    fs::write(&a, document("nano")).unwrap();
    fs::write(&b, document("forager")).unwrap();
    let mut argv = args("nano", &a, "restore");
    argv.extend(args("forager", &b, "restore"));
    argv.extend(["--task".into(), "nano/next".into()]);
    let out = run(root, &argv);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let report: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(report["sources"].as_array().unwrap().len(), 2);
    assert_eq!(report["tasks"].as_array().unwrap().len(), 3);
    assert_eq!(report["tasks"][2]["source_eligible"], false);
    assert_eq!(report["tasks"][1]["project_key"], "forager");
    assert_eq!(report["status"], "owning_source_holds");
    assert!(!root.join("config").exists());
}

#[test]
fn unselected_source_and_ambiguous_project_or_expected_hash_are_refused() {
    let temp = tempdir().unwrap();
    let root = temp.path();
    let p = root.join("TODO.md");
    fs::write(&p, document("nano")).unwrap();
    let base = args("nano", &p, "restore");
    for extra in [
        vec!["--source".into(), format!("nano={}", p.display())],
        vec![
            "--expected-source-sha256".into(),
            format!("other={}", "a".repeat(64)),
        ],
        vec!["--task".into(), "nano/restore".into()],
    ] {
        let mut argv = base.clone();
        argv.extend(extra);
        assert!(!run(root, &argv).status.success());
    }
    assert!(!root.join("config").exists());
}
