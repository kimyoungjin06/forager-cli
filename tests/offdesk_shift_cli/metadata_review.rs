use super::*;
use forager::offdesk::{
    read_policy::ReadPolicy, workflow::metadata_consumer_invocation, OffdeskTaskStore,
};
use sha2::{Digest, Sha256};

fn prepared_metadata(policy: &str, provider_required: bool) -> Result<Fixture> {
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        let root = PathBuf::from(task["workdir"].as_str().unwrap());
        let selected = success(
            Command::new("/usr/bin/python3")
                .args(["-I", "-B", "tests/support/metadata_dispatch_fixture.py"])
                .arg(&root)
                .arg(task["task_id"].as_str().unwrap())
                .arg(task["request_id"].as_str().unwrap())
                .arg(task["project_key"].as_str().unwrap())
                .arg(ReadPolicy::metadata().sha256()?)
                .output()?,
        )?;
        let hash = selected["selection_sha256"].as_str().unwrap();
        task["command"] = json!(metadata_consumer_invocation::canonical(
            root.to_str().unwrap(),
            hash
        ));
        task["provider_id"] = Value::Null;
        task["model"] = Value::Null;
        task["result_artifact_path"] = json!(root.join("result.json"));
        task["artifact_refs"].as_array_mut().unwrap().push(json!({
            "artifact_id":format!("{}{hash}",metadata_consumer_invocation::PREFIX),
            "path":root.join("METADATA_SELECTION.json"),"present":true}));
    }
    f.save(&queue)?;
    let mut controls = vec![
        "--require-preparation-control",
        "require_bound_worker_execution_contract",
    ];
    if provider_required {
        controls.extend([
            "--require-preparation-control",
            "require_declared_provider_model",
        ]);
    }
    let order = ["task_other_2", "task_shift_1"];
    let mut proposal_args = controls.clone();
    proposal_args.extend(["--read-policy", "linux-landlock-metadata-boot-identity"]);
    let proposal = f.report(&order, &proposal_args)?;
    assert_eq!(
        proposal["selection_status"], "ready_for_review",
        "{proposal:#}"
    );
    let mut cmd = command(&f);
    cmd.args([
        "offdesk",
        "shift",
        "prepare",
        &f.plan,
        "--review-id",
        &f.review,
        "--shift-id",
        "local-review",
        "--starts-at",
        &rfc3339(f.start),
        "--stops-at",
        &rfc3339(f.stop),
        "--serial-proposal-sha256",
        proposal["proposal_sha256"].as_str().unwrap(),
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--json",
    ]);
    for task in order {
        cmd.args(["--task-id", task]);
    }
    let output = cmd
        .args(controls)
        .args(["--read-policy", policy])
        .output()?;
    if !output.status.success() {
        anyhow::bail!("{}", String::from_utf8_lossy(&output.stderr));
    }
    Ok(f)
}

#[test]
fn metadata_two_roots_use_admitted_tickets_reports_and_separate_todo_journals() -> Result<()> {
    let f = prepared_metadata("linux-landlock-metadata-boot-identity", false)?;
    let owners = [
        (
            "other",
            "task_other_2",
            f.home.path().join("secondary/NEXT_ACTIONS.md"),
        ),
        (
            "project",
            "task_shift_1",
            f.home.path().join("project/TODO.md"),
        ),
    ];
    let original = owners
        .iter()
        .map(|(_, _, p)| fs::read(p))
        .collect::<std::io::Result<Vec<_>>>()?;
    let before = snapshot(f.home.path())?;
    let shown = inspect(&f, &SCOPE)?;
    assert_eq!(before, snapshot(f.home.path())?);
    assert_eq!(shown["requestable"], true, "{shown:#}");
    let execution = &shown["task_execution_choices"]["tasks"][0];
    assert_eq!(execution["executor_kind"], "deterministic_local_no_model");
    assert_eq!(execution["provider_model_declared"], false);
    assert_eq!(
        execution["worker_timeout_retry_checkpoint"],
        "bound_deadline_committed_intra_attempt_batches_no_dispatcher_restart"
    );
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    for (_, task, _) in &owners {
        success(
            command(&f)
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "local-review",
                    "--task-id",
                    task,
                    "--json",
                ])
                .output()?,
        )?;
        super::super::serial::approve_dispatch(&f, task)?;
        let launched = success(
            command(&f)
                .env("FORAGER_OFFDESK_RUNTIME_BINDING", "forged inherited value")
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "local-review",
                    "--task-id",
                    task,
                    "--json",
                ])
                .output()?,
        )?;
        assert_eq!(launched["launched"], 1, "{launched:#}");
        super::super::serial::wait_for_completed(&f, task)?;
    }
    assert_eq!(
        owners
            .iter()
            .map(|(_, _, p)| fs::read(p))
            .collect::<std::io::Result<Vec<_>>>()?,
        original
    );
    let tasks = OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let report = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    for task in &tasks {
        let result: Value =
            serde_json::from_slice(&fs::read(Path::new(&task.workdir).join("result.json"))?)?;
        assert_eq!(
            result["ticket_id"].as_str(),
            task.background_ticket_id.as_deref()
        );
        assert_eq!(result["project_key"], task.project_key);
        assert_eq!(result["counts"], json!({"compute":0,"reuse":0,"exclude":4}));
        assert_eq!(result["checkpoints"].as_array().unwrap().len(), 4);
        assert_eq!(result["scientific_acceptance"], false);
        assert_eq!(task.attempt_count, 1);
        let detail = report["tasks"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["task_id"] == task.task_id)
            .unwrap();
        assert_eq!(
            detail["closeout_report"]["result_observation"]["identity"],
            "self_reported_exact"
        );
    }
    assert_ne!(tasks[0].background_ticket_id, tasks[1].background_ticket_id);
    let report_path = f.home.path().join("metadata-morning-report.json");
    let report_bytes = serde_json::to_vec(&report)?;
    fs::write(&report_path, &report_bytes)?;
    let reference_path = f.home.path().join("metadata-morning-reference.json");
    fs::write(
        &reference_path,
        serde_json::to_vec(&json!({"path":report_path,
        "sha256":format!("{:x}",Sha256::digest(&report_bytes)), "shift_id":"local-review",
        "manifest_sha256":report["manifest_sha256"]}))?,
    )?;
    for (project, _, owner) in &owners {
        let before = snapshot(f.home.path())?;
        let morning = success(
            command(&f)
                .args([
                    "ondesk",
                    "prompt-package",
                    owner.parent().unwrap().to_str().unwrap(),
                    "--morning-review",
                    reference_path.to_str().unwrap(),
                    "--owning-plan",
                    owner.to_str().unwrap(),
                    "--project-key",
                    project,
                    "--json",
                ])
                .output()?,
        )?;
        assert_eq!(morning["morning_return"]["project_key"], *project);
        assert_eq!(
            morning["morning_return"]["tasks"].as_array().unwrap().len(),
            1
        );
        assert_eq!(snapshot(f.home.path())?, before);
    }
    let again = success(
        command(&f)
            .args(["offdesk", "tick", "--shift-id", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(again["launched"], 0);
    // Synthetic dispositions qualify the journal path, not real task acceptance.
    for (index, (project, id, owner)) in owners.iter().enumerate() {
        let inspected = success(
            command(&f)
                .args([
                    "ondesk",
                    "plan-inspect",
                    owner.to_str().unwrap(),
                    "--project-key",
                    project,
                ])
                .output()?,
        )?;
        let task = tasks.iter().find(|t| t.task_id == *id).unwrap();
        let result = task.result_artifact_path.as_deref().unwrap();
        let result_hash = format!("{:x}", Sha256::digest(fs::read(result)?));
        let decisions = json!({"request_id":format!("metadata-fixture-return-{index}"),"project_key":project,
            "baseline_sha256":inspected["source_sha256"],"decision_ref":"fixture:explicit-review","decided_by":"fixture-user-only",
            "tasks":[{"task_id":id,"disposition":"rework","evidence_refs":[format!("sha256:{result_hash}:{result}")],
            "remaining_work":["synthetic qualification only"],"unanswered_questions":[]}]});
        let decision_path = f
            .home
            .path()
            .join(format!("metadata-decision-{index}.json"));
        fs::write(&decision_path, serde_json::to_vec(&decisions)?)?;
        let proposed = success(
            command(&f)
                .args([
                    "ondesk",
                    "plan-review-proposal",
                    owner.to_str().unwrap(),
                    "--decisions",
                    decision_path.to_str().unwrap(),
                ])
                .output()?,
        )?;
        let candidate = f.home.path().join(format!("metadata-candidate-{index}.md"));
        let request_path = f.home.path().join(format!("metadata-request-{index}.json"));
        fs::write(&candidate, proposed["candidate_markdown"].as_str().unwrap())?;
        fs::write(
            &request_path,
            serde_json::to_vec(&proposed["change_request"])?,
        )?;
        for expected in ["applied", "already_applied"] {
            let applied = success(
                command(&f)
                    .args([
                        "ondesk",
                        "plan-apply",
                        request_path.to_str().unwrap(),
                        "--candidate",
                        candidate.to_str().unwrap(),
                        "--confirm-plan-write",
                    ])
                    .output()?,
            )?;
            assert_eq!(applied["outcome"], expected);
        }
        assert!(owner
            .with_file_name(format!(
                ".{}.forager-plan-journal.json",
                owner.file_name().unwrap().to_str().unwrap()
            ))
            .exists());
        let runtime = OffdeskTaskStore::new(profile_dir(f.home.path()))
            .load()?
            .into_iter()
            .find(|task| task.task_id == *id)
            .unwrap();
        let journal = owner.with_file_name(format!(
            ".{}.forager-plan-journal.json",
            owner.file_name().unwrap().to_str().unwrap()
        ));
        let link = json!({"schema":"offdesk_review_link_request.v1","link_request_id":format!("metadata-fixture-link-{index}"),
            "profile_root":profile_dir(f.home.path()),"task_id":runtime.task_id,"request_id":runtime.request_id,
            "background_ticket_id":runtime.background_ticket_id,
            "runtime_sha256":forager::offdesk::workflow::review_link::runtime_sha256(&runtime)?,
            "result_sha256":result_hash,"owning_plan":owner,
            "owning_plan_sha256":format!("{:x}",Sha256::digest(fs::read(owner)?)),
            "owning_journal_sha256":format!("{:x}",Sha256::digest(fs::read(journal)?)),
            "decision_request_id":format!("metadata-fixture-return-{index}"),"owning_task_id":id});
        let link_path = f.home.path().join(format!("metadata-link-{index}.json"));
        let wrong_path = f
            .home
            .path()
            .join(format!("metadata-wrong-link-{index}.json"));
        let mut wrong = link.clone();
        wrong["background_ticket_id"] = json!("another-task-ticket");
        fs::write(&wrong_path, serde_json::to_vec(&wrong)?)?;
        let queue_before = fs::read(f.queue_path())?;
        assert!(!command(&f)
            .args(["offdesk", "review-link", wrong_path.to_str().unwrap()])
            .output()?
            .status
            .success());
        assert_eq!(fs::read(f.queue_path())?, queue_before);
        fs::write(&link_path, serde_json::to_vec(&link)?)?;
        let preview = success(
            command(&f)
                .args(["offdesk", "review-link", link_path.to_str().unwrap()])
                .output()?,
        )?;
        assert_eq!(fs::read(f.queue_path())?, queue_before);
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
    }
    if let Some(destination) = std::env::var_os("FORAGER_METADATA_FIXTURE_EVIDENCE_DIR") {
        let destination = PathBuf::from(destination).join("two-project");
        fs::create_dir(&destination)?;
        let mut hashes = serde_json::Map::new();
        for (path, bytes) in snapshot(f.home.path())? {
            let relative = path.strip_prefix(f.home.path())?;
            let saved = destination.join(relative);
            fs::create_dir_all(saved.parent().unwrap())?;
            fs::write(saved, &bytes)?;
            hashes.insert(
                relative.to_string_lossy().to_string(),
                json!(format!("{:x}", Sha256::digest(&bytes))),
            );
        }
        fs::write(
            destination.join("FIXTURE_SHA256.json"),
            serde_json::to_vec_pretty(&hashes)?,
        )?;
    }
    Ok(())
}

#[test]
fn metadata_requires_dedicated_policy_without_faking_provider() -> Result<()> {
    assert!(prepared_metadata("linux-landlock-workdir-and-runtime-only", false).is_err());
    let f = prepared_metadata("linux-landlock-metadata-boot-identity", true)?;
    assert_eq!(inspect(&f, &SCOPE)?["requestable"], false);
    Ok(())
}

#[test]
fn metadata_source_or_selected_limit_drift_holds_confirmation() -> Result<()> {
    for field in ["input.jsonl", "METADATA_SELECTION.json"] {
        let f = prepared_metadata("linux-landlock-metadata-boot-identity", false)?;
        let (review, id) = pending_review(&f)?;
        let path = f.home.path().join("secondary").join(field);
        fs::write(&path, b"changed after review")?;
        let before = snapshot(f.home.path())?;
        assert!(!confirm(&f, &review, &id, true)?.status.success());
        assert_eq!(snapshot(f.home.path())?, before);
        assert!(!f.home.path().join("secondary/ATTEMPT.json").exists());
    }
    Ok(())
}

#[test]
fn pending_review_is_consistent_and_coexisting_failure_survives_saved_morning_return() -> Result<()>
{
    let f = super::complete_codex_fixture("evidence-v3")?;
    let tasks = OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let task = &tasks[0];
    let root = Path::new(&task.workdir);
    let clean = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    let clean_task = clean["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["task_id"] == task.task_id)
        .unwrap();
    let clean_closeout = &clean_task["closeout_report"];
    assert!(!clean_closeout["next_actions"]
        .as_array()
        .unwrap()
        .contains(&json!("resolve_result_content_conflict_before_acceptance")));
    let result = fs::read(task.result_artifact_path.as_ref().unwrap())?;
    let failure = json!({"task_id":task.task_id,"request_id":task.request_id,"ticket_id":task.background_ticket_id,
        "status":"failed_review_pending","error_kind":"PostPublicationDrift","automatic_retry":false});
    fs::write(root.join("FAILURE.json"), serde_json::to_vec(&failure)?)?;
    let report = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    let shown = report["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["task_id"] == task.task_id)
        .unwrap();
    let observation = &shown["closeout_report"]["result_observation"];
    assert_eq!(observation["reported_status"], "pending_user_review");
    assert_eq!(observation["reported_exit_code"], 0);
    assert_eq!(
        observation["coexisting_failure"]["identity"],
        "self_reported_exact"
    );
    assert_eq!(
        observation["coexisting_failure"]["error_kind"],
        "PostPublicationDrift"
    );
    assert_eq!(
        shown["closeout_report"]["next_actions"][0],
        "inspect_coexisting_failure_before_acceptance"
    );
    assert_eq!(
        fs::read(task.result_artifact_path.as_ref().unwrap())?,
        result
    );
    let report_path = f.home.path().join("failure-context-report.json");
    fs::write(&report_path, serde_json::to_vec(&report)?)?;
    let reference = f.home.path().join("failure-context-reference.json");
    fs::write(
        &reference,
        serde_json::to_vec(
            &json!({"path":report_path,"sha256":format!("{:x}",Sha256::digest(fs::read(&report_path)?)),
        "shift_id":"local-review","manifest_sha256":report["manifest_sha256"]}),
        )?,
    )?;
    let owner = task
        .artifact_refs
        .iter()
        .find(|v| v.artifact_id.starts_with("ondesk-plan-sha256:"))
        .unwrap()
        .path
        .as_ref()
        .unwrap();
    let before = snapshot(f.home.path())?;
    let morning = success(
        command(&f)
            .args([
                "ondesk",
                "prompt-package",
                root.to_str().unwrap(),
                "--owning-plan",
                owner,
                "--project-key",
                &task.project_key,
                "--morning-review",
                reference.to_str().unwrap(),
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        morning["morning_return"]["tasks"][0]["closeout_report"]["result_observation"]
            ["coexisting_failure"],
        observation["coexisting_failure"]
    );
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn large_evidence_survives_shift_and_morning_return_but_oversized_evidence_still_holds(
) -> Result<()> {
    let f = super::complete_codex_fixture("evidence-v3")?;
    let tasks = OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let task = &tasks[0];
    let root = Path::new(&task.workdir);
    let payload = vec![b'x'; 70869];
    fs::write(root.join("large-evidence.bin"), &payload)?;
    let result_path = Path::new(task.result_artifact_path.as_ref().unwrap());
    let mut result: Value = serde_json::from_slice(&fs::read(result_path)?)?;
    result["evidence"].as_array_mut().unwrap().push(json!({"kind":"check","path":"large-evidence.bin","sha256":format!("{:x}",Sha256::digest(&payload))}));
    fs::write(result_path, serde_json::to_vec(&result)?)?;
    let result_bytes = fs::read(result_path)?;
    let report = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    let shown = report["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["task_id"] == task.task_id)
        .unwrap();
    let evidence = shown["closeout_report"]["result_observation"]["evidence"]
        .as_array()
        .unwrap();
    assert_eq!(
        evidence.last().unwrap()["verification"],
        "current_bytes_hash_match_not_claim_verification"
    );
    assert!(!shown["closeout_report"]["next_actions"]
        .as_array()
        .unwrap()
        .contains(&json!("resolve_result_content_conflict_before_acceptance")));
    let saved = f.home.path().join("large-evidence-shift.json");
    fs::write(&saved, serde_json::to_vec(&report)?)?;
    let reference = f.home.path().join("large-evidence-reference.json");
    fs::write(
        &reference,
        serde_json::to_vec(
            &json!({"path":saved,"sha256":format!("{:x}",Sha256::digest(fs::read(&saved)?)),"shift_id":"local-review","manifest_sha256":report["manifest_sha256"]}),
        )?,
    )?;
    let owner = task
        .artifact_refs
        .iter()
        .find(|v| v.artifact_id.starts_with("ondesk-plan-sha256:"))
        .unwrap()
        .path
        .as_ref()
        .unwrap();
    let morning = success(
        command(&f)
            .args([
                "ondesk",
                "prompt-package",
                root.to_str().unwrap(),
                "--owning-plan",
                owner,
                "--project-key",
                &task.project_key,
                "--morning-review",
                reference.to_str().unwrap(),
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(
        morning["morning_return"]["tasks"][0]["closeout_report"]["result_observation"]["evidence"],
        shown["closeout_report"]["result_observation"]["evidence"]
    );
    assert_eq!(result_bytes, fs::read(result_path)?);
    fs::File::options()
        .write(true)
        .open(root.join("large-evidence.bin"))?
        .set_len(16 * 1024 * 1024 + 1)?;
    let held = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    let shown = held["tasks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["task_id"] == task.task_id)
        .unwrap();
    assert_eq!(
        shown["closeout_report"]["result_observation"]["evidence"]
            .as_array()
            .unwrap()
            .last()
            .unwrap()["verification"],
        "unavailable_or_unsafe"
    );
    assert!(shown["closeout_report"]["next_actions"]
        .as_array()
        .unwrap()
        .contains(&json!("resolve_result_content_conflict_before_acceptance")));
    assert_eq!(result_bytes, fs::read(result_path)?);
    Ok(())
}
