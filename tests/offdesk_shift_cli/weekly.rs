use super::proposal::Fixture;
use super::*;

const ORDER: [&str; 2] = ["task_other_2", "task_shift_1"];
fn command(f: &Fixture) -> Command {
    let mut c = forager_command(f.home.path());
    c.env("FORAGER_PROJECT_REGISTRY", &f.registry);
    c
}
fn success(out: std::process::Output) -> Result<Value> {
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    Ok(serde_json::from_slice(&out.stdout)?)
}
fn inputs(f: &Fixture) -> Result<Vec<String>> {
    let root = profile_dir(f.home.path());
    let stamp = Utc::now().timestamp_millis() as f64 / 1000.0;
    let mut baselines = json!({});
    let mut high = json!({});
    let mut args = vec![
        "--weekly-usage-state".into(),
        root.join("offdesk_weekly_state.json")
            .to_str()
            .unwrap()
            .into(),
    ];
    for p in ["gpt", "claude"] {
        let value = json!({"schema":"offdesk_weekly_usage.v1","provider":p,"account":format!("fixture-{p}"),"observed_at":stamp,"weekly_meters":{"week":{"used_percent":20,"resets_at":stamp+604800.0}}});
        let path = root.join(format!("offdesk_weekly_{p}.json"));
        fs::write(&path, serde_json::to_vec(&value)?)?;
        baselines[p] = value;
        high[p] = json!({"week":20});
        args.extend([
            format!("--weekly-{p}-sample"),
            path.to_str().unwrap().into(),
        ]);
    }
    fs::write(
        root.join("offdesk_weekly_state.json"),
        serde_json::to_vec(
            &json!({"schema":"offdesk_weekly_usage_budget.v1","deadline":stamp+7200.0,"baselines":baselines,"limit_percentage_points":10,"held":false,"high_water":high}),
        )?,
    )?;
    Ok(args)
}
fn prepare(f: &Fixture, extra: &[String]) -> Result<Value> {
    let refs = extra.iter().map(String::as_str).collect::<Vec<_>>();
    let proposal = f.report(&ORDER, &refs)?;
    let mut cmd = command(f);
    cmd.args([
        "offdesk",
        "shift",
        "prepare",
        &f.plan,
        "--review-id",
        &f.review,
        "--shift-id",
        "serial-fixture",
        "--starts-at",
        &rfc3339(f.start),
        "--stops-at",
        &rfc3339(f.stop),
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--serial-proposal-sha256",
        proposal["proposal_sha256"].as_str().unwrap(),
        "--json",
    ]);
    for id in ORDER {
        cmd.args(["--task-id", id]);
    }
    cmd.args(extra);
    success(cmd.output()?)
}
fn sample(f: &Fixture, used: u8) -> Result<()> {
    let path = profile_dir(f.home.path()).join("offdesk_weekly_gpt.json");
    let mut v: Value = serde_json::from_slice(&fs::read(&path)?)?;
    v["weekly_meters"]["week"]["used_percent"] = json!(used);
    fs::write(path, serde_json::to_vec(&v)?)?;
    Ok(())
}
fn inspect(f: &Fixture) -> Result<Value> {
    success(
        command(f)
            .args([
                "offdesk",
                "shift",
                "start-review",
                "serial-fixture",
                "--project-key",
                "project",
                "--project-key",
                "other",
                "--json",
            ])
            .output()?,
    )
}

fn confirm_local_start(f: &Fixture) -> Result<()> {
    let scope = ["--project-key", "project", "--project-key", "other"];
    let review = inspect(f)?;
    assert_eq!(review["requestable"], true);
    let pending = success(
        command(f)
            .args(["offdesk", "shift", "request-arm-review", "serial-fixture"])
            .args(scope)
            .args([
                "--expected-review-sha256",
                review["review_sha256"].as_str().unwrap(),
                "--json",
            ])
            .output()?,
    )?;
    let fresh = inspect(f)?;
    assert_ne!(fresh["review_sha256"], review["review_sha256"]);
    assert_eq!(fresh["activation"]["approval_id"], pending["approval_id"]);
    let root = profile_dir(f.home.path());
    let paths = [
        root.join("offdesk_tasks.json"),
        root.join("offdesk_shifts.v1.json"),
        root.join("pending_action_approvals.json"),
    ];
    let snapshot = || {
        paths
            .iter()
            .map(fs::read)
            .collect::<std::io::Result<Vec<_>>>()
    };
    let before = snapshot()?;
    let confirm = |hash: &str| {
        command(f)
            .args(["offdesk", "shift", "confirm-arm-review", "serial-fixture"])
            .args(scope)
            .args([
                "--expected-review-sha256",
                hash,
                "--approval-id",
                pending["approval_id"].as_str().unwrap(),
                "--by",
                "fixture-local-operator",
                "--acknowledge-unknown-task-usage",
                "--json",
            ])
            .output()
    };
    assert!(!confirm(review["review_sha256"].as_str().unwrap())?
        .status
        .success());
    assert_eq!(snapshot()?, before);
    let hash = fresh["review_sha256"].as_str().unwrap();
    let armed = success(confirm(hash)?)?;
    assert_eq!(armed["state"]["status"], "armed");
    assert_eq!(
        armed["arm_receipt"]["approved_by"],
        "fixture-local-operator"
    );
    let after = snapshot()?;
    assert!(!confirm(hash)?.status.success());
    assert_eq!(snapshot()?, after, "duplicate confirmation changed state");
    for task in f.queue()?.as_array().unwrap() {
        assert_eq!(task["attempt_count"], 0);
        assert!(task["background_ticket_id"].is_null());
    }
    let ledger: Value = serde_json::from_slice(&fs::read(&paths[1])?)?;
    assert_eq!(ledger["arm_receipts"].as_array().unwrap().len(), 1);
    assert_eq!(ledger["task_launch_reservations"], json!([]));
    Ok(())
}

#[test]
fn nightly_five_point_binding_is_shared_by_proposal_review_and_arm() -> Result<()> {
    let f = Fixture::new()?;
    let mut options = inputs(&f)?;
    sample(&f, 25)?;
    let root = profile_dir(f.home.path());
    let output = Command::new("python3")
        .args([
            "-I",
            "-S",
            "-c",
            include_str!("../../scripts/offdesk_usage_guard.py"),
            "init-nightly",
            "--state-dir",
        ])
        .arg(&root)
        .arg("--gpt-sample")
        .arg(root.join("offdesk_weekly_gpt.json"))
        .arg("--claude-sample")
        .arg(root.join("offdesk_weekly_claude.json"))
        .output()?;
    let initialized = success(output)?;
    options[1] = initialized["state_path"].as_str().unwrap().into();
    let report = prepare(&f, &options)?;
    let contract = &report["record"]["manifest"]["weekly_usage"]["contract"];
    assert_eq!(contract["limit_percentage_points"], 5);
    assert_eq!(contract["nightly"], initialized["nightly"]);
    assert_eq!(
        contract["baselines"]["gpt"]["weekly_meters"]["week"]["used_percent"],
        25
    );
    let reviewed = inspect(&f)?;
    assert_eq!(reviewed["weekly_usage"]["allowed"], true);
    assert_eq!(reviewed["weekly_usage"]["binding"]["contract"], *contract);
    let pending = success(
        command(&f)
            .args(["offdesk", "shift", "arm", "serial-fixture", "--json"])
            .output()?,
    )?;
    assert!(serde_json::to_string(&pending)?
        .contains("Nightly allowance from nightly-start weekly-meter baseline: 5"));
    sample(&f, 30)?;
    let held = inspect(&f)?;
    assert_eq!(held["weekly_usage"]["allowed"], false);
    assert_eq!(
        held["weekly_usage"]["verdict"]["increment_percentage_points"]["gpt"]["week"],
        5
    );
    assert_eq!(held["weekly_usage"]["binding"]["contract"], *contract);
    for task in f.queue()?.as_array().unwrap() {
        assert_eq!(task["attempt_count"], 0);
        assert!(task["background_ticket_id"].is_null());
    }
    Ok(())
}

#[test]
fn weekly_binding_is_shared_by_preview_local_status_and_approval_with_stale_refusal() -> Result<()>
{
    let f = Fixture::new()?;
    let options = inputs(&f)?;
    let report = prepare(&f, &options)?;
    let binding = &report["record"]["manifest"]["weekly_usage"];
    assert_eq!(binding["contract"]["limit_percentage_points"], 10);
    let before = fs::read(profile_dir(f.home.path()).join("offdesk_weekly_state.json"))?;
    let first = inspect(&f)?;
    assert_eq!(first["review_sha256"], inspect(&f)?["review_sha256"]);
    let status = success(
        command(&f)
            .args(["offdesk", "shift", "status", "--json"])
            .output()?,
    )?;
    assert_eq!(status["shifts"][0]["weekly_usage"], first["weekly_usage"]);
    assert_eq!(
        before,
        fs::read(profile_dir(f.home.path()).join("offdesk_weekly_state.json"))?
    );
    let pending = success(
        command(&f)
            .args(["offdesk", "shift", "arm", "serial-fixture", "--json"])
            .output()?,
    )?;
    assert!(serde_json::to_string(&pending)?
        .contains("Run allowance from approved weekly-meter baseline: 10"));
    let reviewed = inspect(&f)?;
    sample(&f, 30)?;
    let held = inspect(&f)?;
    assert_eq!(held["requestable"], false);
    assert!(held["activation"]["approval_id"].is_null());
    let old_hash = reviewed["review_sha256"].as_str().unwrap();
    let id = pending[0]["approval_id"].as_str().unwrap();
    let rejected = command(&f)
        .args([
            "offdesk",
            "shift",
            "confirm-arm-review",
            "serial-fixture",
            "--project-key",
            "project",
            "--project-key",
            "other",
            "--expected-review-sha256",
            old_hash,
            "--approval-id",
            id,
            "--by",
            "fixture",
            "--acknowledge-unknown-task-usage",
            "--json",
        ])
        .output()?;
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("local review changed"));
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert_eq!(ledger["arm_receipts"], json!([]));
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn weekly_limit_stops_next_project_without_duplicate_reservation_or_todo_acceptance() -> Result<()>
{
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        task["command"] = json!("printf '{\"status\":\"pending_user_review\"}' > result.json");
        task["result_artifact_path"] =
            json!(Path::new(task["workdir"].as_str().unwrap()).join("result.json"));
    }
    f.save(&queue)?;
    let mut options = inputs(&f)?;
    for control in [
        "stop_new_dispatch_at_window_end",
        "hold_unaccepted_dependencies",
        "hold_source_drift",
        "hold_active_or_uncertain_workdir",
        "hold_uncertain_launch",
        "no_automatic_task_retry",
        "hold_failed_serial_predecessor",
        "guard_weekly_provider_usage",
        "require_declared_task_output",
    ] {
        options.extend(["--require-preparation-control".into(), control.into()]);
    }
    prepare(&f, &options)?;
    assert_eq!(
        inspect(&f)?["preparation_capabilities"]["requirements_satisfied"],
        true
    );
    super::serial::arm(&f)?;
    let first = super::serial::tick(&f, ORDER[0])?;
    assert_eq!(first["launched"], 0);
    super::serial::approve_dispatch(&f, ORDER[0])?;
    assert_eq!(super::serial::tick(&f, ORDER[0])?["launched"], 1);
    super::serial::wait_for_completed(&f, ORDER[0])?;
    let roots = [
        f.home.path().join("project/TODO.md"),
        f.home.path().join("secondary/NEXT_ACTIONS.md"),
    ];
    let before = roots
        .iter()
        .map(fs::read)
        .collect::<std::io::Result<Vec<_>>>()?;
    sample(&f, 30)?;
    let held = super::serial::tick(&f, ORDER[1])?;
    assert_eq!(held["launched"], 0);
    assert!(serde_json::to_string(&held)?.contains("weekly_usage_hold"));
    sample(&f, 31)?;
    assert_eq!(super::serial::tick(&f, ORDER[1])?["launched"], 0);
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert_eq!(
        ledger["task_launch_reservations"].as_array().unwrap().len(),
        1
    );
    assert_eq!(
        roots
            .iter()
            .map(fs::read)
            .collect::<std::io::Result<Vec<_>>>()?,
        before
    );
    Ok(())
}

#[test]
fn versioned_queue_is_read_by_proposal_prepare_and_local_review() -> Result<()> {
    let f = Fixture::new()?;
    let tasks = f.queue()?;
    fs::write(
        profile_dir(f.home.path()).join("offdesk_tasks.json"),
        serde_json::to_vec(&json!({"schema":"offdesk_task_queue.v2","tasks":tasks}))?,
    )?;
    let options = inputs(&f)?;
    prepare(&f, &options)?;
    assert_eq!(inspect(&f)?["weekly_usage"]["allowed"], true);
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn two_project_fixture_returns_separate_reports_and_versioned_review_journals() -> Result<()> {
    use forager::offdesk::{workflow::review_link::runtime_sha256, OffdeskTaskStore};
    use sha2::{Digest, Sha256};
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        let result = json!({"task_id":task["task_id"],"status":"pending_user_review","scientific_acceptance":false});
        task["command"] = json!(format!("printf '%s' '{result}' > result.json"));
        task["result_artifact_path"] =
            json!(Path::new(task["workdir"].as_str().unwrap()).join("result.json"));
    }
    f.save(&queue)?;
    let mut options = inputs(&f)?;
    for control in [
        "stop_new_dispatch_at_window_end",
        "hold_unaccepted_dependencies",
        "hold_source_drift",
        "hold_active_or_uncertain_workdir",
        "hold_uncertain_launch",
        "no_automatic_task_retry",
        "hold_failed_serial_predecessor",
        "guard_weekly_provider_usage",
        "require_declared_task_output",
    ] {
        options.extend(["--require-preparation-control".into(), control.into()]);
    }
    prepare(&f, &options)?;
    assert_eq!(
        inspect(&f)?["preparation_capabilities"]["requirements_satisfied"],
        true
    );
    confirm_local_start(&f)?;
    let owners = [
        (
            "other",
            ORDER[0],
            f.home.path().join("secondary/NEXT_ACTIONS.md"),
        ),
        ("project", ORDER[1], f.home.path().join("project/TODO.md")),
    ];
    let originals = owners
        .iter()
        .map(|(_, _, p)| fs::read(p))
        .collect::<std::io::Result<Vec<_>>>()?;
    for id in ORDER {
        // A completed predecessor alone is not sufficient; wait for its exact
        // supported release and a fresh dispatch approval for this task.
        let mut requested = false;
        for _ in 0..60 {
            super::serial::tick(&f, id)?;
            let pending = success(
                command(&f)
                    .args(["offdesk", "pending", "--json"])
                    .output()?,
            )?;
            if pending
                .as_array()
                .unwrap()
                .iter()
                .any(|p| p["task_id"] == id && p["action"] == "dispatch.runtime")
            {
                requested = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        assert!(requested);
        super::serial::approve_dispatch(&f, id)?;
        assert_eq!(super::serial::tick(&f, id)?["launched"], 1);
        super::serial::wait_for_completed(&f, id)?;
    }
    success(command(&f).args(["offdesk", "poll", "--json"]).output()?)?;
    let report = success(
        command(&f)
            .args(["offdesk", "shift", "review", "serial-fixture", "--json"])
            .output()?,
    )?;
    assert_eq!(report["summary"]["task_attempts_completed"], 2);
    assert_eq!(report["tasks"].as_array().unwrap().len(), 2);
    for (project, id, _) in &owners {
        let queue = f.queue()?;
        let task = queue
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["task_id"] == *id)
            .unwrap();
        let detail = report["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["task_id"] == *id)
            .unwrap();
        assert_eq!(detail["project_key"], *project);
        assert_eq!(detail["attempts"].as_array().unwrap().len(), 1);
        assert_eq!(
            detail["attempts"][0]["background_ticket_id"],
            task["background_ticket_id"]
        );
        let result = fs::read(task["result_artifact_path"].as_str().unwrap())?;
        let observation = &detail["closeout_report"]["result_observation"];
        assert_eq!(
            observation["content_sha256"],
            format!("{:x}", Sha256::digest(&result))
        );
        assert_eq!(observation["reported_status"], "pending_user_review");
        assert_eq!(observation["identity"], "missing");
        assert!(detail["closeout_report"]["recorded_review"].is_null());
    }
    assert_eq!(
        owners
            .iter()
            .map(|(_, _, p)| fs::read(p))
            .collect::<std::io::Result<Vec<_>>>()?,
        originals
    );
    for (index, (project, id, path)) in owners.iter().enumerate() {
        // These are explicitly fixture dispositions. They do not stand in for
        // a user's acceptance of any real project or scientific result.
        let inspected = success(
            command(&f)
                .args([
                    "ondesk",
                    "plan-inspect",
                    path.to_str().unwrap(),
                    "--project-key",
                    project,
                ])
                .output()?,
        )?;
        let result = OffdeskTaskStore::new(profile_dir(f.home.path()))
            .load()?
            .into_iter()
            .find(|task| task.task_id == *id)
            .unwrap()
            .result_artifact_path
            .unwrap();
        let result_hash = format!("{:x}", Sha256::digest(fs::read(&result)?));
        let decisions = json!({"request_id":format!("fixture-return-{index}"),"project_key":project,"baseline_sha256":inspected["source_sha256"],"decision_ref":"fixture:explicit-review","decided_by":"fixture-user-only",
            "tasks":[{"task_id":id,"disposition":"rework","evidence_refs":[format!("sha256:{result_hash}:{result}")],"remaining_work":["fixture follow-up remains; no scientific acceptance"],"unanswered_questions":[]}]});
        let decision_path = f.home.path().join(format!("decisions-{index}.json"));
        fs::write(&decision_path, serde_json::to_vec(&decisions)?)?;
        let proposal = success(
            command(&f)
                .args([
                    "ondesk",
                    "plan-review-proposal",
                    path.to_str().unwrap(),
                    "--decisions",
                    decision_path.to_str().unwrap(),
                ])
                .output()?,
        )?;
        assert_eq!(fs::read(path)?, originals[index]);
        let candidate = f.home.path().join(format!("candidate-{index}.md"));
        fs::write(&candidate, proposal["candidate_markdown"].as_str().unwrap())?;
        let request = f.home.path().join(format!("request-{index}.json"));
        fs::write(&request, serde_json::to_vec(&proposal["change_request"])?)?;
        for expected in ["applied", "already_applied"] {
            let applied = success(
                command(&f)
                    .args([
                        "ondesk",
                        "plan-apply",
                        request.to_str().unwrap(),
                        "--candidate",
                        candidate.to_str().unwrap(),
                        "--confirm-plan-write",
                    ])
                    .output()?,
            )?;
            assert_eq!(applied["outcome"], expected);
        }
        let bytes = fs::read(path)?;
        assert_ne!(
            format!("{:x}", Sha256::digest(&bytes)),
            format!("{:x}", Sha256::digest(&originals[index]))
        );
        let updated = success(
            command(&f)
                .args([
                    "ondesk",
                    "plan-inspect",
                    path.to_str().unwrap(),
                    "--project-key",
                    project,
                ])
                .output()?,
        )?;
        assert!(updated["plan"]["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .any(|t| t["task_id"] == *id && t["review_state"] == "rework"));
        let task = OffdeskTaskStore::new(profile_dir(f.home.path()))
            .load()?
            .into_iter()
            .find(|task| task.task_id == *id)
            .unwrap();
        let journal = path.with_file_name(format!(
            ".{}.forager-plan-journal.json",
            path.file_name().unwrap().to_str().unwrap()
        ));
        let link = json!({"schema":"offdesk_review_link_request.v1","link_request_id":format!("fixture-link-{index}"),
            "profile_root":profile_dir(f.home.path()),"task_id":task.task_id,"request_id":task.request_id,
            "background_ticket_id":task.background_ticket_id,"runtime_sha256":runtime_sha256(&task)?,
            "result_sha256":result_hash,"owning_plan":path,"owning_plan_sha256":format!("{:x}",Sha256::digest(fs::read(path)?)),
            "owning_journal_sha256":format!("{:x}",Sha256::digest(fs::read(journal)?)),
            "decision_request_id":format!("fixture-return-{index}"),"owning_task_id":id});
        let link_path = f.home.path().join(format!("link-{index}.json"));
        fs::write(&link_path, serde_json::to_vec(&link)?)?;
        let queue_before = fs::read(profile_dir(f.home.path()).join("offdesk_tasks.json"))?;
        let wrong_path = f.home.path().join(format!("wrong-link-{index}.json"));
        let mut wrong = link.clone();
        wrong["background_ticket_id"] = json!("another-task-ticket");
        fs::write(&wrong_path, serde_json::to_vec(&wrong)?)?;
        let refused = command(&f)
            .args(["offdesk", "review-link", wrong_path.to_str().unwrap()])
            .output()?;
        assert!(!refused.status.success());
        assert!(
            String::from_utf8_lossy(&refused.stderr).contains("runtime review identity mismatch")
        );
        assert_eq!(
            queue_before,
            fs::read(profile_dir(f.home.path()).join("offdesk_tasks.json"))?
        );
        let preview = success(
            command(&f)
                .args(["offdesk", "review-link", link_path.to_str().unwrap()])
                .output()?,
        )?;
        assert_eq!(preview["outcome"], "preview");
        assert_eq!(
            queue_before,
            fs::read(profile_dir(f.home.path()).join("offdesk_tasks.json"))?
        );
        for expected in ["applied", "already_applied"] {
            let linked = success(
                command(&f)
                    .args([
                        "offdesk",
                        "review-link",
                        link_path.to_str().unwrap(),
                        "--confirm",
                        "--expected-preview-sha256",
                        preview["preview_sha256"].as_str().unwrap(),
                        "--by",
                        "fixture-user-only",
                    ])
                    .output()?,
            )?;
            assert_eq!(linked["outcome"], expected);
        }
        let returned = success(
            command(&f)
                .args(["offdesk", "shift", "review", "serial-fixture", "--json"])
                .output()?,
        )?;
        let recorded = &returned["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|t| t["task_id"] == *id)
            .unwrap()["closeout_report"]["recorded_review"];
        assert_eq!(recorded["state"], "recorded_rework");
        assert_eq!(
            recorded["remaining_work"],
            json!(["fixture follow-up remains; no scientific acceptance"])
        );
        assert_eq!(
            format!("{:x}", Sha256::digest(fs::read(&result)?)),
            result_hash
        );
        if index == 0 {
            assert_eq!(fs::read(&owners[1].2)?, originals[1]);
        }
    }
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert_eq!(
        ledger["task_launch_reservations"].as_array().unwrap().len(),
        2
    );
    assert_eq!(ledger["task_outcome_receipts"].as_array().unwrap().len(), 2);
    Ok(())
}

#[test]
fn busy_usage_guard_holds_this_tick_without_creating_a_persistent_alarm() -> Result<()> {
    use fs2::FileExt;
    let f = Fixture::new()?;
    let options = inputs(&f)?;
    prepare(&f, &options)?;
    super::serial::arm(&f)?;
    let lock = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(profile_dir(f.home.path()).join("offdesk_weekly_state.json.lock"))?;
    lock.lock_exclusive()?;
    let held = super::serial::tick(&f, ORDER[0])?;
    assert_eq!(held["launched"], 0);
    assert!(serde_json::to_string(&held)?.contains("guard_busy"));
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert!(ledger["states"]
        .as_array()
        .unwrap()
        .iter()
        .all(|s| s["status"] != "attention_required"));
    assert!(ledger["task_launch_reservations"]
        .as_array()
        .unwrap()
        .is_empty());
    FileExt::unlock(&lock)?;
    super::serial::tick(&f, ORDER[0])?;
    super::serial::approve_dispatch(&f, ORDER[0])?;
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn failed_project_preserves_failure_while_independent_work_is_eligible_for_new_review() -> Result<()>
{
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    queue
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|t| t["task_id"] == ORDER[0])
        .unwrap()["command"] = json!("exit 7");
    f.save(&queue)?;
    let options = inputs(&f)?;
    prepare(&f, &options)?;
    super::serial::arm(&f)?;
    super::serial::tick(&f, ORDER[0])?;
    super::serial::approve_dispatch(&f, ORDER[0])?;
    assert_eq!(super::serial::tick(&f, ORDER[0])?["launched"], 1);
    let mut failed = false;
    for _ in 0..60 {
        success(command(&f).args(["offdesk", "poll", "--json"]).output()?)?;
        if f.queue()?
            .as_array()
            .unwrap()
            .iter()
            .any(|t| t["task_id"] == ORDER[0] && t["status"] == "resume_pending")
        {
            failed = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(failed);
    let held = super::serial::tick(&f, ORDER[1])?;
    assert_eq!(held["launched"], 0);
    let before = fs::read(profile_dir(f.home.path()).join("offdesk_shifts.v1.json"))?;
    let refs = options.iter().map(String::as_str).collect::<Vec<_>>();
    let new_review = f.report(&[ORDER[1]], &refs)?;
    assert_eq!(new_review["selection_status"], "ready_for_review");
    assert_eq!(new_review["serial_order"], json!([ORDER[1]]));
    assert_eq!(
        before,
        fs::read(profile_dir(f.home.path()).join("offdesk_shifts.v1.json"))?
    );
    assert!(f
        .queue()?
        .as_array()
        .unwrap()
        .iter()
        .any(|t| t["task_id"] == ORDER[0] && t["status"] == "resume_pending"));
    // A fresh inert selection does not transfer the failed run's ownership or
    // approve dispatch. Failure closeout and the new review remain explicit.
    assert_eq!(super::serial::tick(&f, ORDER[1])?["launched"], 0);
    Ok(())
}
