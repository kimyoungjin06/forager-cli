use super::*;
use forager::offdesk::workflow::ondesk_plan;

fn pin(path: &Path) -> Result<Value> {
    Ok(json!({"path":path,"sha256":hash(&fs::read(path)?)}))
}

fn attach(f: &Fixture, approved: &Path) -> Result<()> {
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let mut base = serde_json::to_value(&request)?;
    for field in [
        "narrative_reconciliation",
        "historical_prefix",
        "historical_boundary",
    ] {
        base.as_object_mut().unwrap().remove(field);
    }
    base.sort_all_objects();
    let digest = hash(&serde_json::to_vec(&base)?);
    let identity = forager::process::filesystem_file_identity(&fs::metadata(&f.profile)?).unwrap();
    let current_hash = hash(&fs::read(&request.owning_plan)?);
    let agreement = f._dir.path().join("narrative-agreement.json");
    fs::write(
        &agreement,
        serde_json::to_vec(&json!({
            "schema":"offdesk_review_narrative_reconciliation_agreement.v1",
            "review_request_sha256":digest,"profile_identity":identity,
            "approved_document_sha256":hash(&fs::read(approved)?),"current_document_sha256":current_hash,
            "decided_by":"fixture-user","decision_ref":"fixture:narrative-only-choice",
            "permitted_effect":"link_existing_nonaccepted_decision_across_exact_narrative_change"
        }))?,
    )?;
    let artifact = f._dir.path().join("narrative.json");
    fs::write(
        &artifact,
        serde_json::to_vec(&json!({
            "schema":"offdesk_review_narrative_reconciliation.v1",
            "review_request_sha256":digest,"profile_identity":identity,
            "approved_document":pin(approved)?,"current_document_sha256":current_hash,
            "agreement":pin(&agreement)?
        }))?,
    )?;
    request.narrative_reconciliation = Some(serde_json::from_value(pin(&artifact)?)?);
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    Ok(())
}

fn confirm(f: &Fixture) -> Result<Value> {
    let out = run(f, &[])?;
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let preview: Value = serde_json::from_slice(&out.stdout)?;
    let extra = [
        "--confirm",
        "--expected-preview-sha256",
        preview["preview_sha256"].as_str().unwrap(),
        "--by",
        "fixture-user",
    ];
    let applied = run(f, &extra)?;
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    assert_eq!(
        serde_json::from_slice::<Value>(&applied.stdout)?["outcome"],
        "applied"
    );
    let queue = fs::read(f.profile.join("offdesk_tasks.json"))?;
    let replay = run(f, &extra)?;
    assert!(replay.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&replay.stdout)?["outcome"],
        "already_applied"
    );
    assert_eq!(queue, fs::read(f.profile.join("offdesk_tasks.json"))?);
    assert_eq!(
        serde_json::from_slice::<Value>(&queue)?["schema"],
        "offdesk_task_queue.v5"
    );
    if let Some(binary) = std::env::var_os("FORAGER_NARRATIVE_LEGACY_TEST_BIN") {
        assert!(!command(Path::new(&binary), &f.home)
            .args(["offdesk", "tasks", "--json"])
            .output()?
            .status
            .success());
        assert_eq!(queue, fs::read(f.profile.join("offdesk_tasks.json"))?);
    }
    Ok(preview)
}

#[test]
fn explicit_narrative_cli_preserves_the_committed_decision_and_refuses_later_drift() -> Result<()> {
    let f = fixture_with_state(ReviewState::PartiallyAccepted)?;
    let request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let approved = f._dir.path().join("approved.md");
    fs::copy(&request.owning_plan, &approved)?;
    let current = fs::read_to_string(&approved)? + "\nUnrelated narrative changed.\n";
    fs::write(&request.owning_plan, &current)?;
    assert!(!run(&f, &[])?.status.success());
    let journal = request
        .owning_plan
        .with_file_name(".TODO.md.forager-plan-journal.json");
    let before = fs::read(&journal)?;
    let result = fs::read(&f.result)?;
    attach(&f, &approved)?;
    let preview = confirm(&f)?;
    assert_eq!(
        preview["preview"]["narrative_reconciliation"]["task_count"],
        1
    );
    assert_eq!(
        preview["preview"]["request"]["owning_plan_sha256"],
        hash(&fs::read(&approved)?)
    );
    assert_eq!(fs::read_to_string(&request.owning_plan)?, current);
    assert_eq!(before, fs::read(journal)?);
    assert_eq!(result, fs::read(&f.result)?);
    let views = cli_json(&f, &["offdesk", "tasks", "--json"])?;
    assert_eq!(views[0]["review"]["state"], "recorded_partial");
    fs::write(&request.owning_plan, current + "\nAnother later edit.\n")?;
    let queue = fs::read(f.profile.join("offdesk_tasks.json"))?;
    assert!(!run(&f, &[])?.status.success());
    let views = cli_json(&f, &["offdesk", "tasks", "--json"])?;
    assert_eq!(views[0]["review"]["state"], "review_binding_changed");
    assert_eq!(queue, fs::read(f.profile.join("offdesk_tasks.json"))?);
    Ok(())
}

#[test]
fn selected_real_nano_narrative_shadow_closes_one_partial_review_without_a_new_journal_decision(
) -> Result<()> {
    let Some(path) = std::env::var_os("FORAGER_NARRATIVE_REAL_METADATA_FIXTURE") else {
        return Ok(());
    };
    let input: Value = serde_json::from_slice(&fs::read(path)?)?;
    let approved_text = fs::read_to_string(input["approved_document"].as_str().unwrap())?;
    let current_text = fs::read_to_string(input["current_document"].as_str().unwrap())?;
    let journal_bytes = fs::read(input["journal"].as_str().unwrap())?;
    assert_eq!(
        hash(approved_text.as_bytes()),
        input["approved_document_sha256"]
    );
    assert_eq!(
        hash(current_text.as_bytes()),
        input["current_document_sha256"]
    );
    assert_eq!(hash(&journal_bytes), input["journal_sha256"]);
    let approved_plan = ondesk_plan::inspect(&approved_text, "nanoclustering")?.plan;
    assert_eq!(
        approved_plan,
        ondesk_plan::inspect(&current_text, "nanoclustering")?.plan
    );
    assert_eq!(approved_plan.tasks.len(), 11);
    let dir = tempdir()?;
    let home = dir.path().join("home");
    let profile = home.join(".config/forager/profiles/default");
    fs::create_dir_all(&profile)?;
    let owner = dir.path().join("TODO.md");
    let approved = dir.path().join("approved.md");
    fs::write(&owner, &current_text)?;
    fs::write(&approved, &approved_text)?;
    let journal = owner.with_file_name(".TODO.md.forager-plan-journal.json");
    let mut value: Value = serde_json::from_slice(&journal_bytes)?;
    assert_eq!(value["entries"].as_array().unwrap().len(), 18);
    value["source_path"] = json!(owner);
    for item in value["entries"].as_array_mut().unwrap() {
        item["request"]["source_path"] = json!(owner);
    }
    fs::write(&journal, serde_json::to_vec(&value)?)?;
    let mut snapshot = value;
    snapshot["entries"].as_array_mut().unwrap().pop();
    let snapshot_path = dir.path().join("snapshot.json");
    fs::write(&snapshot_path, serde_json::to_vec(&snapshot)?)?;
    let baseline = dir.path().join("baseline.md");
    fs::copy(input["prefix_baseline"].as_str().unwrap(), &baseline)?;
    let mut task: OffdeskTask = serde_json::from_value(input["runtime_task"].clone())?;
    let result = dir.path().join("result.json");
    fs::copy(task.result_artifact_path.as_ref().unwrap(), &result)?;
    for name in ["BOUND_REVIEW.json", "NATIVE_EVENTS.jsonl"] {
        let source = Path::new(&task.workdir).join(name);
        assert!(fs::metadata(&source)?.len() <= 16 * 1024 * 1024);
        fs::copy(source, dir.path().join(name))?;
    }
    task.workdir = dir.path().to_string_lossy().into_owned();
    task.result_artifact_path = Some(result.to_string_lossy().into_owned());
    task.review_links.clear();
    OffdeskTaskStore::new(&profile).save(std::slice::from_ref(&task))?;
    let closeouts = profile.join("offdesk_closeouts/real-metadata");
    fs::create_dir_all(&closeouts)?;
    let closeout_plan = closeouts.join("closeout_plan.json");
    let closeout_review = closeouts.join("closeout_review_latest.json");
    fs::copy(input["closeout_plan"].as_str().unwrap(), &closeout_plan)?;
    fs::copy(input["closeout_review"].as_str().unwrap(), &closeout_review)?;
    let mut request: ReviewLinkRequest = serde_json::from_value(input["original_request"].clone())?;
    request.link_request_id = "shadow-narrative-link".into();
    request.profile_root = profile.clone();
    request.runtime_sha256 = runtime_sha256(&task)?;
    request.owning_plan = owner.clone();
    request.owning_journal_sha256 = hash(&fs::read(&journal)?);
    request.historical_prefix = None;
    request.narrative_reconciliation = None;
    request.closeouts = vec![
        serde_json::from_value(pin(&closeout_plan)?)?,
        serde_json::from_value(pin(&closeout_review)?)?,
    ];
    let request_path = dir.path().join("request.json");
    fs::write(&request_path, serde_json::to_vec(&request)?)?;
    let f = Fixture {
        _dir: dir,
        home,
        profile,
        request: request_path,
        result,
        source: current_text.clone(),
    };
    let original = fs::read(&journal)?;
    let result_before = fs::read(&f.result)?;
    assert!(!run(&f, &[])?.status.success());
    attach(&f, &approved)?;
    select_prefix_request(&f, &snapshot_path, &baseline)?;
    let preview = confirm(&f)?;
    assert_eq!(
        preview["preview"]["narrative_reconciliation"]["task_count"],
        11
    );
    assert_eq!(
        preview["preview"]["historical_prefix"]["prefix_entries"],
        17
    );
    assert_eq!(
        preview["preview"]["historical_prefix"]["gaps"]
            .as_array()
            .unwrap()
            .len(),
        11
    );
    let status: Value = serde_json::from_slice(
        &command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
            .args(["status", "--json"])
            .output()?
            .stdout,
    )?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    assert_eq!(status["closeout_state"]["reviewed_nonaccepted"], 1);
    assert_eq!(status["closeout_state"]["accepted"], 0);
    let queue = fs::read(f.profile.join("offdesk_tasks.json"))?;
    for mode in 0..2 {
        let mut wrong = approved_plan.clone();
        if mode == 0 {
            wrong.tasks[0].goal.push_str(" changed other task");
        } else {
            wrong.tasks.swap(0, 1);
        }
        fs::write(&owner, ondesk_plan::with_plan(&current_text, &wrong)?)?;
        attach(&f, &approved)?;
        select_prefix_request(&f, &snapshot_path, &baseline)?;
        assert!(!run(&f, &[])?.status.success());
        assert_eq!(queue, fs::read(f.profile.join("offdesk_tasks.json"))?);
    }
    assert_eq!(original, fs::read(journal)?);
    assert_eq!(result_before, fs::read(&f.result)?);
    Ok(())
}
