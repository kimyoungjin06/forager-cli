#![cfg(target_os = "linux")]

use std::fs;
use std::path::Path;
use std::process::{Command, Output};

use forager::offdesk::workflow::ondesk_plan::{inspect, PLAN_SCHEMA};
use serde_json::{json, Value};
use tempfile::tempdir;

fn document(goal: &str) -> String {
    format!(
        "# Plan\n\n```forager-plan\n{}\n```\n",
        json!({"schema":PLAN_SCHEMA,"project_key":"demo","tasks":[{
            "task_id":"T-1","goal":goal,"completion_criteria":["test passes"],
            "depends_on":[],"review_state":"unreviewed","evidence_refs":[]
        }]})
    )
}

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_forager"))
        .current_dir(root)
        .env("XDG_CONFIG_HOME", root.join("config"))
        .env_remove("FORAGER_PROFILE")
        .env_remove("AGENT_OF_EMPIRES_PROFILE")
        .env_remove("TMUX")
        .args(args)
        .output()
        .unwrap()
}

#[test]
fn explicit_plain_todo_adoption_preserves_source_then_uses_normal_plan_reviews() {
    use sha2::{Digest, Sha256};
    let temp = tempdir().unwrap();
    let source = "# 기존 TODO\n- [ ] selected task\n- [ ] unrelated task";
    let path = temp.path().join("TODO.md");
    fs::write(&path, source).unwrap();
    let plan = inspect(&document("selected task"), "demo").unwrap().plan;
    fs::write(
        temp.path().join("tasks.json"),
        serde_json::to_vec(&plan).unwrap(),
    )
    .unwrap();
    let hash = format!("{:x}", Sha256::digest(source.as_bytes()));
    let args = [
        "ondesk",
        "plan-adopt-proposal",
        path.to_str().unwrap(),
        "--tasks",
        "tasks.json",
        "--project-key",
        "demo",
        "--expected-sha256",
        &hash,
        "--request-id",
        "adopt-1",
        "--decision-ref",
        "operator:fixture-adoption",
    ];
    let out = run(temp.path(), &args);
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let proposal: Value = serde_json::from_slice(&out.stdout).unwrap();
    let candidate = proposal["candidate_markdown"].as_str().unwrap();
    assert!(candidate.starts_with(source));
    assert_eq!(inspect(candidate, "demo").unwrap().plan.tasks.len(), 1);
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp
        .path()
        .join(".TODO.md.forager-plan-journal.json")
        .exists());
    assert!(!temp.path().join("config/forager").exists());
    fs::write(temp.path().join("candidate.md"), candidate).unwrap();
    fs::write(
        temp.path().join("request.json"),
        serde_json::to_vec(&proposal["change_request"]).unwrap(),
    )
    .unwrap();
    let apply_args = [
        "ondesk",
        "plan-apply",
        "request.json",
        "--candidate",
        "candidate.md",
    ];
    assert!(!run(temp.path(), &apply_args).status.success());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp
        .path()
        .join(".TODO.md.forager-plan-journal.json")
        .exists());
    let mut apply_args = apply_args.to_vec();
    apply_args.push("--confirm-plan-write");
    for expected in ["applied", "already_applied"] {
        let out = run(temp.path(), &apply_args);
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert_eq!(
            serde_json::from_slice::<Value>(&out.stdout).unwrap()["outcome"],
            expected
        );
        assert_eq!(fs::read_to_string(&path).unwrap(), candidate);
    }
    assert!(!run(temp.path(), &args).status.success());
    let inspection = inspect(candidate, "demo").unwrap();
    let decisions = json!({"request_id":"review-after-adoption","project_key":"demo",
        "baseline_sha256":inspection.source_sha256,"decision_ref":"operator:review","decided_by":"local operator",
        "tasks":[{"task_id":"T-1","disposition":"rework","evidence_refs":["review:fixture"],
            "remaining_work":["fix remaining issue"],"unanswered_questions":[]}]});
    fs::write(
        temp.path().join("decisions.json"),
        serde_json::to_vec(&decisions).unwrap(),
    )
    .unwrap();
    let out = run(
        temp.path(),
        &[
            "ondesk",
            "plan-review-proposal",
            path.to_str().unwrap(),
            "--decisions",
            "decisions.json",
        ],
    );
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let review: Value = serde_json::from_slice(&out.stdout).unwrap();
    fs::write(
        temp.path().join("review-request.json"),
        serde_json::to_vec(&review["change_request"]).unwrap(),
    )
    .unwrap();
    fs::write(
        temp.path().join("review-candidate.md"),
        review["candidate_markdown"].as_str().unwrap(),
    )
    .unwrap();
    let out = run(
        temp.path(),
        &[
            "ondesk",
            "plan-apply",
            "review-request.json",
            "--candidate",
            "review-candidate.md",
            "--confirm-plan-write",
        ],
    );
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let updated = fs::read_to_string(&path).unwrap();
    assert!(updated.starts_with(source));
    assert_eq!(
        inspect(&updated, "demo").unwrap().plan.tasks[0].review_state,
        forager::offdesk::workflow::ondesk_plan::ReviewState::Rework
    );
    assert!(run(temp.path(), &apply_args).status.success());
    assert_eq!(fs::read_to_string(&path).unwrap(), updated);
    assert!(!temp.path().join("config/forager").exists());
}

#[test]
fn adoption_refuses_stale_tampered_and_linked_sources_without_writing() {
    use forager::offdesk::workflow::{ondesk_plan_adoption, ondesk_plan_store};
    use sha2::{Digest, Sha256};
    let temp = tempdir().unwrap();
    let path = temp.path().join("TODO.md");
    let source = "# TODO\n- [ ] original task\n";
    fs::write(&path, source).unwrap();
    let plan = inspect(&document("original task"), "demo").unwrap().plan;
    let hash = format!("{:x}", Sha256::digest(source.as_bytes()));
    let proposal = ondesk_plan_adoption::propose(
        &path,
        plan.clone(),
        "demo",
        &hash,
        "adopt-1".into(),
        "operator:fixture".into(),
    )
    .unwrap();
    assert!(ondesk_plan_adoption::propose(
        &path,
        plan.clone(),
        "other",
        &hash,
        "adopt-1".into(),
        "operator:fixture".into()
    )
    .is_err());
    let changed_candidate = proposal
        .candidate_markdown
        .replace("original task", "replaced task");
    let mut request = proposal.change_request.clone();
    request.candidate_sha256 = format!("{:x}", Sha256::digest(changed_candidate.as_bytes()));
    assert!(ondesk_plan_store::apply(&request, &changed_candidate, true).is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp
        .path()
        .join(".TODO.md.forager-plan-journal.json")
        .exists());
    let changed_source = format!("{source}- [ ] other writer\n");
    fs::write(&path, &changed_source).unwrap();
    assert!(
        ondesk_plan_store::apply(&proposal.change_request, &proposal.candidate_markdown, true)
            .is_err()
    );
    assert_eq!(fs::read_to_string(&path).unwrap(), changed_source);
    fs::write(&path, source).unwrap();
    let linked = temp.path().join("linked.md");
    std::os::unix::fs::symlink(&path, &linked).unwrap();
    assert!(ondesk_plan_adoption::read_source(&linked).is_err());
    fs::remove_file(&linked).unwrap();
    fs::hard_link(&path, &linked).unwrap();
    assert!(ondesk_plan_adoption::read_source(&path).is_err());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
}

#[test]
fn review_proposal_is_inert_then_uses_confirmed_journaled_apply() {
    let temp = tempdir().unwrap();
    let source = format!(
        "````markdown\n```forager-plan\nexample only\n```\n````\n{}\nKeep this narrative.\n",
        document("original goal")
    );
    let path = temp.path().join("plan.md");
    fs::write(&path, &source).unwrap();
    let decisions = json!({"request_id":"review-1","project_key":"demo", "baseline_sha256":inspect(&source,"demo").unwrap().source_sha256,
        "decision_ref":"operator-review-1", "decided_by":"local operator", "tasks":[{"task_id":"T-1","disposition":"accepted","evidence_refs":["receipt:test"],"remaining_work":[],"unanswered_questions":[]}]});
    fs::write(
        temp.path().join("decisions.json"),
        serde_json::to_vec(&decisions).unwrap(),
    )
    .unwrap();
    let output = run(
        temp.path(),
        &[
            "ondesk",
            "plan-review-proposal",
            path.to_str().unwrap(),
            "--decisions",
            "decisions.json",
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let proposal: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert!(proposal["decisions"].get("shift_review").is_none());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp.path().join("config/forager").exists());
    let candidate = proposal["candidate_markdown"].as_str().unwrap();
    assert!(candidate.starts_with("````markdown\n```forager-plan\nexample only\n```\n````\n"));
    assert!(candidate.ends_with("Keep this narrative.\n"));
    assert_eq!(
        inspect(candidate, "demo").unwrap().plan.tasks[0].review_state,
        forager::offdesk::workflow::ondesk_plan::ReviewState::Accepted
    );
    fs::write(temp.path().join("candidate.md"), candidate).unwrap();
    fs::write(
        temp.path().join("request.json"),
        serde_json::to_vec(&proposal["change_request"]).unwrap(),
    )
    .unwrap();
    let denied = run(
        temp.path(),
        &[
            "ondesk",
            "plan-apply",
            "request.json",
            "--candidate",
            "candidate.md",
        ],
    );
    assert!(!denied.status.success());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    for expected in ["applied", "already_applied"] {
        let applied = run(
            temp.path(),
            &[
                "ondesk",
                "plan-apply",
                "request.json",
                "--candidate",
                "candidate.md",
                "--confirm-plan-write",
            ],
        );
        assert!(
            applied.status.success(),
            "{}",
            String::from_utf8_lossy(&applied.stderr)
        );
        assert_eq!(
            serde_json::from_slice::<Value>(&applied.stdout).unwrap()["outcome"],
            expected
        );
    }
    let journal: Value = serde_json::from_slice(
        &fs::read(temp.path().join(".plan.md.forager-plan-journal.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(
        journal["entries"][0]["request"]["review_record"]["decided_by"],
        "local operator"
    );
    assert_eq!(
        journal["entries"][0]["request"]["review_record"]["tasks"][0]["disposition"],
        "accepted"
    );
    let mut changed_request = proposal["change_request"].clone();
    changed_request["review_record"]["decided_by"] = json!("different operator");
    fs::write(
        temp.path().join("changed-request.json"),
        serde_json::to_vec(&changed_request).unwrap(),
    )
    .unwrap();
    assert!(!run(
        temp.path(),
        &[
            "ondesk",
            "plan-apply",
            "changed-request.json",
            "--candidate",
            "candidate.md",
            "--confirm-plan-write"
        ]
    )
    .status
    .success());
    let stale = run(
        temp.path(),
        &[
            "ondesk",
            "plan-review-proposal",
            path.to_str().unwrap(),
            "--decisions",
            "decisions.json",
        ],
    );
    assert!(!stale.status.success());
}

#[test]
fn report_bound_review_rechecks_before_apply_and_preserves_replay_identity() {
    qualify_report_bound_review(false);
}

#[test]
fn narrative_reconciliation_uses_current_version_and_preserves_journaled_decisions() {
    qualify_report_bound_review(true);
}

fn qualify_report_bound_review(reconcile: bool) {
    use sha2::{Digest, Sha256};
    let temp = tempdir().unwrap();
    let execution_source = document("original goal");
    let source = if reconcile {
        format!("{execution_source}\nKeep this new operator note.\n")
    } else {
        execution_source.clone()
    };
    let path = temp.path().join("plan.md");
    fs::write(&path, &source).unwrap();
    let report_path = temp.path().join("night-review.json");
    let baseline = inspect(&source, "demo").unwrap().source_sha256;
    let report = serde_json::to_vec(&json!({
        "schema":"offdesk_shift_review_projection.v1", "shift_id":"night-1",
        "manifest_sha256":"a".repeat(64), "tasks":[{
            "project_key":"demo", "task_id":"T-1", "closeout_report":{
                "schema":"offdesk_task_closeout_report.v1",
                "historical_scope_status":"preserved_source_hash_verified",
                "historical_source_sha256":inspect(&execution_source, "demo").unwrap().source_sha256,
                "review_disposition":"pending_user_review", "plan_update_authorized":false
            }
        }]
    }))
    .unwrap();
    fs::write(&report_path, &report).unwrap();
    let reference = json!({"path":report_path, "sha256":format!("{:x}", Sha256::digest(&report)),
        "shift_id":"night-1", "manifest_sha256":"a".repeat(64)});
    let mut decisions = json!({"request_id":"bound-review-1","project_key":"demo", "baseline_sha256":baseline,
        "decision_ref":"operator-review-1", "decided_by":"local operator", "shift_review":reference,
        "tasks":[{"task_id":"T-1","disposition":"partially_accepted","evidence_refs":["check:reviewed"],
            "remaining_work":["finish remaining behavior"],"unanswered_questions":["how to resume?"]}]});
    fs::write(
        temp.path().join("decisions.json"),
        serde_json::to_vec(&decisions).unwrap(),
    )
    .unwrap();
    let mut proposal_args = vec![
        "ondesk",
        "plan-review-proposal",
        path.to_str().unwrap(),
        "--decisions",
        "decisions.json",
    ];
    if reconcile {
        assert!(!run(temp.path(), &proposal_args).status.success());
        fs::write(temp.path().join("execution.md"), &execution_source).unwrap();
        proposal_args.extend(["--reconcile-baseline", "execution.md"]);
        assert!(!run(temp.path(), &proposal_args).status.success());
        proposal_args.extend([
            "--reconciliation-reason",
            "Only prose changed; task plan unchanged",
        ]);
    }
    let output = run(temp.path(), &proposal_args);
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let proposal: Value = serde_json::from_slice(&output.stdout).unwrap();
    if reconcile {
        decisions = proposal["decisions"].clone();
        assert_eq!(
            decisions["reconciliation"]["execution_baseline_markdown"],
            execution_source
        );
        assert!(proposal["candidate_markdown"]
            .as_str()
            .unwrap()
            .ends_with("Keep this new operator note.\n"));
    }
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp
        .path()
        .join(".plan.md.forager-plan-journal.json")
        .exists());
    assert!(!temp.path().join("config").exists());
    fs::write(
        temp.path().join("candidate.md"),
        proposal["candidate_markdown"].as_str().unwrap(),
    )
    .unwrap();
    fs::write(
        temp.path().join("request.json"),
        serde_json::to_vec(&proposal["change_request"]).unwrap(),
    )
    .unwrap();
    let apply_args = [
        "ondesk",
        "plan-apply",
        "request.json",
        "--candidate",
        "candidate.md",
        "--confirm-plan-write",
    ];
    if reconcile {
        assert!(!run(temp.path(), &apply_args[..apply_args.len() - 1])
            .status
            .success());
        fs::write(&path, format!("{source}\nConcurrent edit\n")).unwrap();
        assert!(!run(temp.path(), &apply_args).status.success());
        assert!(fs::read_to_string(&path)
            .unwrap()
            .ends_with("Concurrent edit\n"));
        fs::write(&path, &source).unwrap();
    }
    fs::write(&report_path, b"{}").unwrap();
    let denied = run(temp.path(), &apply_args);
    assert!(!denied.status.success());
    assert!(String::from_utf8_lossy(&denied.stderr).contains("shift review artifact hash changed"));
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    assert!(!temp
        .path()
        .join(".plan.md.forager-plan-journal.json")
        .exists());
    fs::write(&report_path, &report).unwrap();
    let applied = run(temp.path(), &apply_args);
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    let current = fs::read_to_string(&path).unwrap();
    assert_eq!(
        inspect(&current, "demo").unwrap().plan.tasks[0].review_state,
        forager::offdesk::workflow::ondesk_plan::ReviewState::PartiallyAccepted
    );
    let journal: Value = serde_json::from_slice(
        &fs::read(temp.path().join(".plan.md.forager-plan-journal.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(journal["entries"][0]["request"]["review_record"], decisions);
    fs::remove_file(&report_path).unwrap();
    let replay = run(temp.path(), &apply_args);
    assert!(
        replay.status.success(),
        "{}",
        String::from_utf8_lossy(&replay.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&replay.stdout).unwrap()["outcome"],
        "already_applied"
    );
    assert_eq!(fs::read_to_string(&path).unwrap(), current);
    let mut changed = proposal["change_request"].clone();
    changed["review_record"]["shift_review"]["manifest_sha256"] = json!("b".repeat(64));
    fs::write(
        temp.path().join("request.json"),
        serde_json::to_vec(&changed).unwrap(),
    )
    .unwrap();
    assert!(!run(temp.path(), &apply_args).status.success());
    assert_eq!(fs::read_to_string(&path).unwrap(), current);
}

#[test]
fn inspect_and_preview_do_not_change_the_owning_plan() {
    let temp = tempdir().unwrap();
    let source = document("original goal");
    let candidate = document("revised goal");
    fs::write(temp.path().join("NEXT_ACTIONS.md"), &source).unwrap();
    fs::write(temp.path().join("candidate.md"), &candidate).unwrap();
    let output = run(
        temp.path(),
        &[
            "ondesk",
            "plan-inspect",
            "NEXT_ACTIONS.md",
            "--project-key",
            "demo",
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let report: Value = serde_json::from_slice(&output.stdout).unwrap();
    let hash = report["source_sha256"].as_str().unwrap();
    assert_eq!(hash, inspect(&source, "demo").unwrap().source_sha256);
    let output = run(
        temp.path(),
        &[
            "ondesk",
            "plan-inspect",
            "NEXT_ACTIONS.md",
            "--project-key",
            "demo",
            "--candidate",
            "candidate.md",
            "--expected-sha256",
            hash,
        ],
    );
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(preview["changed"], json!(["T-1"]));
    assert!(preview["does_not_authorize"]
        .as_array()
        .unwrap()
        .contains(&json!("plan_write")));
    assert_eq!(
        fs::read_to_string(temp.path().join("NEXT_ACTIONS.md")).unwrap(),
        source
    );
    assert_eq!(
        fs::read_to_string(temp.path().join("candidate.md")).unwrap(),
        candidate
    );
    assert!(
        !temp.path().join("config").exists(),
        "read-only inspection created app state"
    );
}

#[test]
fn rejects_plain_checklists_wrong_project_and_stale_preview() {
    let temp = tempdir().unwrap();
    fs::write(temp.path().join("plan.md"), "- [ ] existing task\n").unwrap();
    let args = ["ondesk", "plan-inspect", "plan.md", "--project-key", "demo"];
    assert!(!run(temp.path(), &args).status.success());
    fs::write(temp.path().join("plan.md"), document("goal")).unwrap();
    assert!(!run(
        temp.path(),
        &[
            "ondesk",
            "plan-inspect",
            "plan.md",
            "--project-key",
            "other"
        ]
    )
    .status
    .success());
    assert!(!run(
        temp.path(),
        &[
            "ondesk",
            "plan-inspect",
            "plan.md",
            "--project-key",
            "demo",
            "--candidate",
            "plan.md",
            "--expected-sha256",
            &"0".repeat(64)
        ]
    )
    .status
    .success());
    assert!(!run(
        temp.path(),
        &[
            "ondesk",
            "plan-inspect",
            "plan.md",
            "--project-key",
            "demo",
            "--candidate",
            "plan.md"
        ]
    )
    .status
    .success());
}

#[test]
fn confirmed_apply_is_bound_and_replay_safe_without_profile_state() {
    let temp = tempdir().unwrap();
    let source = document("original");
    let candidate = document("updated");
    let path = temp.path().join("plan.md");
    fs::write(&path, &source).unwrap();
    fs::write(temp.path().join("candidate.md"), &candidate).unwrap();
    let request = json!({"schema":"ondesk_plan_change_request.v1", "request_id":"request-1",
        "project_key":"demo", "source_path":path,
        "baseline_sha256":inspect(&source,"demo").unwrap().source_sha256,
        "candidate_sha256":inspect(&candidate,"demo").unwrap().source_sha256,
        "decision_ref":"operator:test-agreement"});
    fs::write(
        temp.path().join("request.json"),
        serde_json::to_vec(&request).unwrap(),
    )
    .unwrap();
    assert!(!run(
        temp.path(),
        &[
            "ondesk",
            "plan-apply",
            "request.json",
            "--candidate",
            "candidate.md"
        ]
    )
    .status
    .success());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    for expected in ["applied", "already_applied"] {
        let output = run(
            temp.path(),
            &[
                "ondesk",
                "plan-apply",
                "request.json",
                "--candidate",
                "candidate.md",
                "--confirm-plan-write",
            ],
        );
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let value: Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["outcome"], expected);
        assert_eq!(fs::read_to_string(&path).unwrap(), candidate);
    }
    assert!(!temp.path().join("config").exists());
}

#[test]
fn morning_recovery_missing_profile_is_inert_and_options_are_explicit() {
    use sha2::{Digest, Sha256};
    let temp = tempdir().unwrap();
    let path = temp.path().join("plan.md");
    let source = document("original");
    fs::write(&path, &source).unwrap();
    let report_path = temp.path().join("review.json");
    let report=serde_json::to_vec(&json!({"schema":"offdesk_shift_review_projection.v1","shift_id":"night-1",
        "manifest_sha256":"a".repeat(64),"council":{},"tasks":[{"project_key":"demo","task_id":"T-1",
            "closeout_report":{"schema":"offdesk_task_closeout_report.v1","historical_scope_status":"preserved_source_hash_verified",
                "historical_source_sha256":inspect(&source,"demo").unwrap().source_sha256,"review_disposition":"pending_user_review","plan_update_authorized":false}}]})).unwrap();
    fs::write(&report_path, &report).unwrap();
    let reference_path = temp.path().join("reference.json");
    let reference = json!({"path":report_path,"sha256":format!("{:x}",Sha256::digest(&report)),"shift_id":"night-1","manifest_sha256":"a".repeat(64)});
    fs::write(&reference_path, serde_json::to_vec(&reference).unwrap()).unwrap();
    let args = [
        "ondesk",
        "prompt-package",
        temp.path().to_str().unwrap(),
        "--morning-review",
        reference_path.to_str().unwrap(),
        "--owning-plan",
        path.to_str().unwrap(),
        "--project-key",
        "demo",
        "--json",
    ];
    let output = run(temp.path(), &args);
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("morning shift record missing"));
    assert!(!temp.path().join("config").exists());
    assert_eq!(fs::read_to_string(&path).unwrap(), source);
    let output = run(
        temp.path(),
        &[
            "ondesk",
            "prompt-package",
            "--morning-review",
            reference_path.to_str().unwrap(),
        ],
    );
    assert!(!output.status.success());
    assert!(!temp.path().join("config").exists());
    for flag in ["--capture-id", "--mode"] {
        let mut conflicting = args.to_vec();
        conflicting.extend([flag, "old"]);
        let output = run(temp.path(), &conflicting);
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("cannot be used with"));
    }
}
