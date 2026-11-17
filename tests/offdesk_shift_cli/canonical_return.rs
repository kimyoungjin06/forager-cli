use super::*;
use forager::offdesk::{
    workflow::{ondesk_task_binding, review_link},
    OffdeskTask, OffdeskTaskStore,
};
use sha2::{Digest, Sha256};

fn sha(path: &Path) -> Result<String> {
    Ok(format!("{:x}", Sha256::digest(fs::read(path)?)))
}

struct Mapped {
    f: Fixture,
    reference: PathBuf,
    owners: Vec<(OffdeskTask, PathBuf)>,
}

fn setup() -> Result<Mapped> {
    let f = complete_codex_fixture("evidence-v3")?;
    success(
        command(&f)
            .args(["offdesk", "shift", "disarm", "local-review", "--json"])
            .output()?,
    )?;
    let report = success(
        command(&f)
            .args(["offdesk", "shift", "review", "local-review", "--json"])
            .output()?,
    )?;
    let report_path = f.home.path().join("saved-review.json");
    fs::write(&report_path, serde_json::to_vec(&report)?)?;
    let reference = f.home.path().join("morning-reference.json");
    fs::write(
        &reference,
        serde_json::to_vec(&json!({"path":report_path,"sha256":sha(&report_path)?,
        "shift_id":"local-review","manifest_sha256":report["manifest_sha256"]}))?,
    )?;
    let mut owners = Vec::new();
    for task in OffdeskTaskStore::new(profile_dir(f.home.path())).load()? {
        let root = Path::new(&task.workdir);
        let source = task
            .artifact_refs
            .iter()
            .find(|v| {
                v.artifact_id
                    .starts_with(ondesk_task_binding::BINDING_PREFIX)
            })
            .unwrap();
        let path = Path::new(source.path.as_deref().unwrap());
        let source_path = if path.is_absolute() {
            path.to_path_buf()
        } else {
            root.join(path)
        };
        let canonical = f
            .home
            .path()
            .join("original-projects")
            .join(&task.project_key);
        fs::create_dir_all(&canonical)?;
        let owner = canonical.join("TODO.md");
        fs::copy(&source_path, &owner)?;
        let cfg: Value = serde_json::from_slice(&fs::read(root.join("REVIEW_SELECTION.json"))?)?;
        let mut origins = serde_json::Map::new();
        origins.insert(
            "owning_plan".into(),
            json!({"original_path":owner,"original_sha256":sha(&source_path)?,
            "copied_path":source_path.strip_prefix(root)?}),
        );
        for (key, value) in cfg["sources"].as_object().unwrap() {
            let path = root.join(value["path"].as_str().unwrap());
            origins.insert(
                key.clone(),
                json!({"original_path":canonical.join(path.file_name().unwrap()),
                "original_sha256":sha(&path)?,"copied_path":value["path"]}),
            );
        }
        fs::write(
            root.join("SOURCE_BINDINGS.json"),
            serde_json::to_vec(&origins)?,
        )?;
        owners.push((task, owner));
    }
    Ok(Mapped {
        f,
        reference,
        owners,
    })
}

fn prepare(m: &Mapped, task: &OffdeskTask, owner: &Path) -> Result<Value> {
    let before = snapshot(m.f.home.path())?;
    let value = success(
        command(&m.f)
            .args([
                "ondesk",
                "return-binding",
                owner.parent().unwrap().to_str().unwrap(),
                "--owning-plan",
                owner.to_str().unwrap(),
                "--project-key",
                &task.project_key,
                "--morning-review",
                m.reference.to_str().unwrap(),
                "--by",
                "fixture-user-only",
                "--decision-ref",
                "fixture:copied-root-mapping-no-acceptance",
            ])
            .output()?,
    )?;
    assert_eq!(snapshot(m.f.home.path())?, before);
    Ok(value)
}

fn morning(
    m: &Mapped,
    task: &OffdeskTask,
    owner: &Path,
    binding: Option<&Path>,
) -> Result<std::process::Output> {
    let mut cmd = command(&m.f);
    cmd.args([
        "ondesk",
        "prompt-package",
        owner.parent().unwrap().to_str().unwrap(),
        "--owning-plan",
        owner.to_str().unwrap(),
        "--project-key",
        &task.project_key,
        "--morning-review",
        m.reference.to_str().unwrap(),
        "--json",
    ]);
    if let Some(path) = binding {
        cmd.args([
            "--return-binding",
            path.to_str().unwrap(),
            "--return-binding-sha256",
            &sha(path)?,
        ]);
    }
    let before = snapshot(m.f.home.path())?;
    let output = cmd.output()?;
    assert_eq!(snapshot(m.f.home.path())?, before);
    Ok(output)
}

#[test]
fn canonical_return_two_copies_reach_original_plan_apply_and_review_link_only_with_fixture_decisions(
) -> Result<()> {
    let m = setup()?;
    for (index, (task, owner)) in m.owners.iter().enumerate() {
        let refused = morning(&m, task, owner, None)?;
        assert!(!refused.status.success());
        assert!(String::from_utf8_lossy(&refused.stderr)
            .contains("morning project root identity changed"));
        let binding = prepare(&m, task, owner)?;
        assert_eq!(
            binding["effect"],
            "read_only_pre_agreement_no_plan_write_or_task_acceptance"
        );
        let pin_path = m.f.home.path().join(format!("return-binding-{index}.json"));
        fs::write(&pin_path, serde_json::to_vec(&binding)?)?;
        let package = success(morning(&m, task, owner, Some(&pin_path))?)?;
        let context = &package["morning_return"];
        assert_eq!(context["schema"], "ondesk_morning_return.v2");
        assert_eq!(context["tasks"][0]["task_id"], task.task_id);
        assert_eq!(context["tasks"][0]["baseline_status"], "exact_baseline");
        assert_eq!(context["canonical_return_binding"]["binding"], binding);
        assert_eq!(context["owning_plan"], owner.to_string_lossy().as_ref());
        assert!(context["does_not_authorize"]
            .as_array()
            .unwrap()
            .contains(&json!("user_acceptance")));
        let package_path = m.f.home.path().join(format!("mapped-package-{index}.md"));
        fs::write(&package_path, package["content"].as_str().unwrap())?;
        let before = snapshot(m.f.home.path())?;
        let delivery = command(&m.f)
            .args([
                "ondesk",
                "return",
                "preview",
                "--package",
                package_path.to_str().unwrap(),
                "--session",
                "not-a-real-session",
            ])
            .output()?;
        assert!(!delivery.status.success());
        assert!(String::from_utf8_lossy(&delivery.stderr)
            .contains("morning agreement or reconciliation remains pending"));
        assert_eq!(snapshot(m.f.home.path())?, before);
        let result_path = Path::new(task.result_artifact_path.as_deref().unwrap());
        let result_sha = sha(result_path)?;
        let decisions = json!({"request_id":format!("canonical-fixture-decision-{index}"),"project_key":task.project_key,
            "baseline_sha256":sha(owner)?,"decision_ref":"fixture:explicit-rework","decided_by":"fixture-user-only",
            "shift_review":serde_json::from_slice::<Value>(&fs::read(&m.reference)?)?,
            "tasks":[{"task_id":task.task_id,"disposition":"rework",
                "evidence_refs":[format!("sha256:{result_sha}:{}",result_path.display()),format!("sha256:{}:{}",sha(&pin_path)?,pin_path.display())],
                "remaining_work":["synthetic qualification only; no actual user acceptance"],"unanswered_questions":[]}]});
        let decision_path = m.f.home.path().join(format!("decision-{index}.json"));
        fs::write(&decision_path, serde_json::to_vec(&decisions)?)?;
        let proposed = success(
            command(&m.f)
                .args([
                    "ondesk",
                    "plan-review-proposal",
                    owner.to_str().unwrap(),
                    "--decisions",
                    decision_path.to_str().unwrap(),
                ])
                .output()?,
        )?;
        let request = m.f.home.path().join(format!("write-{index}.json"));
        let candidate = m.f.home.path().join(format!("candidate-{index}.md"));
        fs::write(&request, serde_json::to_vec(&proposed["change_request"])?)?;
        fs::write(&candidate, proposed["candidate_markdown"].as_str().unwrap())?;
        for expected in ["applied", "already_applied"] {
            let applied = success(
                command(&m.f)
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
        let journal = owner.with_file_name(".TODO.md.forager-plan-journal.json");
        let link = json!({"schema":"offdesk_review_link_request.v1","link_request_id":format!("mapped-link-{index}"),
            "profile_root":profile_dir(m.f.home.path()),"task_id":task.task_id,"request_id":task.request_id,
            "background_ticket_id":task.background_ticket_id,"runtime_sha256":review_link::runtime_sha256(task)?,
            "result_sha256":result_sha,"owning_plan":owner,"owning_plan_sha256":sha(owner)?,"owning_journal_sha256":sha(&journal)?,
            "decision_request_id":format!("canonical-fixture-decision-{index}"),"owning_task_id":task.task_id});
        let link_path = m.f.home.path().join(format!("link-{index}.json"));
        fs::write(&link_path, serde_json::to_vec(&link)?)?;
        let preview = success(
            command(&m.f)
                .args(["offdesk", "review-link", link_path.to_str().unwrap()])
                .output()?,
        )?;
        for expected in ["applied", "already_applied"] {
            let linked = success(
                command(&m.f)
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
        assert!(
            !morning(&m, task, owner, Some(&pin_path))?.status.success(),
            "pre-agreement mapping cannot survive a plan/journal decision write"
        );
    }
    Ok(())
}

#[test]
fn canonical_return_rejects_rehashed_wrong_ticket_profile_origin_or_missing_task_mapping(
) -> Result<()> {
    let m = setup()?;
    let (task, owner) = &m.owners[0];
    let original = prepare(&m, task, owner)?;
    let path = m.f.home.path().join("changed-binding.json");
    for mode in [
        "ticket",
        "profile",
        "project",
        "origin",
        "task_set",
        "root",
        "authority",
        "schema",
    ] {
        let mut value = original.clone();
        match mode {
            "ticket" => value["tasks"][0]["ticket_id"] = json!("other-ticket"),
            "profile" => value["profile_root"]["identity_sha256"] = json!("a".repeat(64)),
            "project" => value["project_key"] = json!("other"),
            "origin" => value["tasks"][0]["execution_plan"]["sha256"] = json!("a".repeat(64)),
            "task_set" => value["tasks"] = json!([]),
            "root" => value["canonical_root"]["identity_sha256"] = json!("a".repeat(64)),
            "authority" => value["effect"] = json!("accept_owning_task"),
            "schema" => value["extra"] = json!(true),
            _ => unreachable!(),
        }
        fs::write(&path, serde_json::to_vec(&value)?)?;
        assert!(
            !morning(&m, task, owner, Some(&path))?.status.success(),
            "{mode}"
        );
    }
    fs::write(&path, serde_json::to_vec(&original)?)?;
    let before = snapshot(m.f.home.path())?;
    let output = command(&m.f)
        .args([
            "ondesk",
            "prompt-package",
            owner.parent().unwrap().to_str().unwrap(),
            "--owning-plan",
            owner.to_str().unwrap(),
            "--project-key",
            &task.project_key,
            "--morning-review",
            m.reference.to_str().unwrap(),
            "--return-binding",
            path.to_str().unwrap(),
            "--return-binding-sha256",
            &"0".repeat(64),
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert_eq!(snapshot(m.f.home.path())?, before);
    Ok(())
}

#[test]
fn canonical_return_rejects_plan_journal_copied_source_raw_report_result_and_origin_drift(
) -> Result<()> {
    let m = setup()?;
    let (task, owner) = &m.owners[0];
    let original = prepare(&m, task, owner)?;
    let path = m.f.home.path().join("frozen-binding.json");
    fs::write(&path, serde_json::to_vec(&original)?)?;
    let execution = Path::new(&task.workdir);
    let copied_plan = PathBuf::from(
        original["tasks"][0]["execution_plan"]["path"]
            .as_str()
            .unwrap(),
    );
    let copied_source = execution.join("input/code.py");
    for target in [
        owner.as_path(),
        copied_plan.as_path(),
        copied_source.as_path(),
        &execution.join("MODEL_REPORT.json"),
        &execution.join("HOST_QUOTED_REPORT.json"),
        &execution.join("REVIEW_SELECTION.json"),
        Path::new(task.result_artifact_path.as_deref().unwrap()),
        &execution.join("SOURCE_BINDINGS.json"),
    ] {
        let before = fs::read(target)?;
        let mut altered = before.clone();
        altered.push(b' ');
        fs::write(target, altered)?;
        assert!(
            !morning(&m, task, owner, Some(&path))?.status.success(),
            "{}",
            target.display()
        );
        fs::write(target, before)?;
    }
    let journal = owner.with_file_name(".TODO.md.forager-plan-journal.json");
    fs::write(
        &journal,
        serde_json::to_vec(
            &json!({"schema":"ondesk_plan_change_journal.v1","source_path":owner,"entries":[]}),
        )?,
    )?;
    assert!(!morning(&m, task, owner, Some(&path))?.status.success());
    fs::remove_file(&journal)?;
    let failure = execution.join("FAILURE.json");
    fs::write(&failure, "{}")?;
    assert!(!morning(&m, task, owner, Some(&path))?.status.success());
    fs::remove_file(failure)?;
    let queue = m.f.queue()?;
    let mut changed = queue.clone();
    for row in changed.as_array_mut().unwrap() {
        if row["task_id"] == task.task_id {
            row["status"] = json!("running");
        }
    }
    m.f.save(&changed)?;
    assert!(!morning(&m, task, owner, Some(&path))?.status.success());
    m.f.save(&queue)?;
    assert!(morning(&m, task, owner, Some(&path))?.status.success());
    Ok(())
}

#[test]
fn canonical_return_requires_an_original_source_record_and_refuses_pending_journal_or_symlink(
) -> Result<()> {
    let m = setup()?;
    let (task, owner) = &m.owners[0];
    let original = prepare(&m, task, owner)?;
    let path = m.f.home.path().join("frozen-binding.json");
    fs::write(&path, serde_json::to_vec(&original)?)?;
    let origin = Path::new(&task.workdir).join("SOURCE_BINDINGS.json");
    let before = fs::read(&origin)?;
    let mut value: Value = serde_json::from_slice(&before)?;
    value["owning_plan"]["original_path"] = json!(m.owners[1].1);
    fs::write(&origin, serde_json::to_vec(&value)?)?;
    let refused = command(&m.f)
        .args([
            "ondesk",
            "return-binding",
            owner.parent().unwrap().to_str().unwrap(),
            "--owning-plan",
            owner.to_str().unwrap(),
            "--project-key",
            &task.project_key,
            "--morning-review",
            m.reference.to_str().unwrap(),
            "--by",
            "fixture-user-only",
            "--decision-ref",
            "fixture:bad-origin",
        ])
        .output()?;
    assert!(!refused.status.success());
    fs::write(&origin, &before)?;
    let mut incomplete: Value = serde_json::from_slice(&before)?;
    incomplete.as_object_mut().unwrap().remove("code");
    fs::write(&origin, serde_json::to_vec(&incomplete)?)?;
    let refused = command(&m.f)
        .args([
            "ondesk",
            "return-binding",
            owner.parent().unwrap().to_str().unwrap(),
            "--owning-plan",
            owner.to_str().unwrap(),
            "--project-key",
            &task.project_key,
            "--morning-review",
            m.reference.to_str().unwrap(),
            "--by",
            "fixture-user-only",
            "--decision-ref",
            "fixture:incomplete-origin",
        ])
        .output()?;
    assert!(!refused.status.success());
    fs::write(&origin, before)?;
    let journal = owner.with_file_name(".TODO.md.forager-plan-journal.json");
    fs::write(
        &journal,
        serde_json::to_vec(
            &json!({"schema":"ondesk_plan_change_journal.v1","source_path":owner,
        "entries":[{"committed":false,"request":{"schema":"ondesk_plan_change_request.v1","request_id":"pending-fixture",
        "project_key":task.project_key,"source_path":owner,"baseline_sha256":sha(owner)?,"candidate_sha256":"a".repeat(64),"decision_ref":"fixture:pending"}}]}),
        )?,
    )?;
    assert!(!morning(&m, task, owner, Some(&path))?.status.success());
    fs::remove_file(journal)?;
    let alias = m.f.home.path().join("binding-link.json");
    std::os::unix::fs::symlink(&path, &alias)?;
    assert!(!morning(&m, task, owner, Some(&alias))?.status.success());
    Ok(())
}
