use super::*;
use crate::offdesk::{workflow::ondesk_review, OffdeskTaskStore};
use tempfile::{tempdir, TempDir};

struct Fixture {
    _dir: TempDir,
    root: PathBuf,
    task: OffdeskTask,
    request: ReviewLinkRequest,
    evidence: PathBuf,
    source: String,
}

fn fixture() -> Result<Fixture> {
    fixture_with_state(ondesk_plan::ReviewState::Rework)
}

fn fixture_with_state(state: ondesk_plan::ReviewState) -> Result<Fixture> {
    let dir = tempdir()?;
    let root = dir.path().join("profile");
    std::fs::create_dir(&root)?;
    let plan_path = dir.path().join("TODO.md");
    let evidence = dir.path().join("evidence.md");
    std::fs::write(&evidence, "bounded historical evidence")?;
    let reference = format!(
        "sha256:{}:{}",
        digest(&std::fs::read(&evidence)?),
        evidence.display()
    );
    let task: OffdeskTask = serde_json::from_value(serde_json::json!({
        "task_id":"runtime-1", "request_id":"request-1", "project_key":"project",
        "status":"completed", "capability_id":"dispatch.runtime", "runner_kind":"local_tmux",
        "command":"test-runner", "workdir":dir.path(), "background_ticket_id":"ticket-1",
        "attempt_count":1, "created_at":"2026-10-08T00:00:00Z", "updated_at":"2026-10-08T01:00:00Z",
        "result_artifact_path":"result.json"
    }))?;
    std::fs::write(
        dir.path().join("result.json"),
        br#"{"task_id":"runtime-1","status":"pending_user_review"}"#,
    )?;
    let source = format!(
        "# Existing task\n```forager-plan\n{}\n```\n",
        serde_json::json!({
            "schema":"ondesk_task_plan.v1", "project_key":"project", "tasks":[{
                "task_id":"owning-1", "goal":"bounded review", "completion_criteria":["record disposition"],
                "depends_on":[], "review_state":"unreviewed", "evidence_refs":["owning-task:owning-1"]
            }]
        })
    );
    std::fs::write(&plan_path, &source)?;
    let decisions = ondesk_review::ReviewDecisions {
        request_id: "review-1".into(),
        project_key: "project".into(),
        baseline_sha256: digest(source.as_bytes()),
        decision_ref: "user:already-approved-rework".into(),
        decided_by: "fixture-user".into(),
        tasks: vec![TaskDecision {
            task_id: "owning-1".into(),
            disposition: state,
            evidence_refs: vec![reference],
            remaining_work: if state == ondesk_plan::ReviewState::Accepted {
                vec![]
            } else {
                vec!["revise the bounded protocol".into()]
            },
            unanswered_questions: vec![],
        }],
        shift_review: None,
        reconciliation: None,
    };
    let proposal = ondesk_review::propose(&source, &plan_path, decisions)?;
    ondesk_plan_store::apply(&proposal.change_request, &proposal.candidate_markdown, true)?;
    let request = ReviewLinkRequest {
        schema: "offdesk_review_link_request.v1".into(),
        link_request_id: "link-1".into(),
        profile_root: root.clone(),
        task_id: task.task_id.clone(),
        request_id: task.request_id.clone(),
        background_ticket_id: "ticket-1".into(),
        runtime_sha256: runtime_sha256(&task)?,
        result_sha256: digest(&std::fs::read(dir.path().join("result.json"))?),
        owning_plan: plan_path.clone(),
        owning_plan_sha256: digest(&std::fs::read(&plan_path)?),
        owning_journal_sha256: digest(&std::fs::read(journal_path(&plan_path)?)?),
        decision_request_id: "review-1".into(),
        owning_task_id: "owning-1".into(),
        assessment: None,
        closeouts: vec![],
        historical_boundary: None,
        historical_prefix: None,
        narrative_reconciliation: None,
    };
    OffdeskTaskStore::new(&root).save(std::slice::from_ref(&task))?;
    Ok(Fixture {
        _dir: dir,
        root,
        task,
        request,
        evidence,
        source,
    })
}

#[test]
fn existing_rework_link_is_exact_idempotent_and_preserves_runtime() -> Result<()> {
    let f = fixture()?;
    let store = OffdeskTaskStore::new(&f.root);
    let original_result = std::fs::read(f._dir.path().join("result.json"))?;
    let preview = preview(&f.root, &f.task, &f.request)?;
    assert_eq!(
        preview.decision.disposition,
        ondesk_plan::ReviewState::Rework
    );
    let hash = preview.sha256()?;
    assert_eq!(
        store.link_review(&f.request, &hash, "local-operator", Utc::now())?,
        "applied"
    );
    let bytes = std::fs::read(store.path())?;
    assert!(
        serde_json::from_slice::<Vec<OffdeskTask>>(&bytes).is_err(),
        "old Vec reader must refuse the versioned queue"
    );
    let tasks = store.load()?;
    assert_eq!(runtime_sha256(&tasks[0])?, runtime_sha256(&f.task)?);
    validate_record(&f.root, &tasks[0], &tasks[0].review_links[0])?;
    assert_eq!(
        store.link_review(&f.request, &hash, "other-recorder", Utc::now())?,
        "already_applied"
    );
    assert_eq!(bytes, std::fs::read(store.path())?);
    assert_eq!(
        original_result,
        std::fs::read(f._dir.path().join("result.json"))?
    );
    assert_eq!(tasks[0].review_links[0].recorded_by, "local-operator");
    Ok(())
}

#[test]
fn changed_identity_sources_and_preview_refuse_without_queue_writes() -> Result<()> {
    let f = fixture()?;
    let store = OffdeskTaskStore::new(&f.root);
    let before = std::fs::read(store.path())?;
    let hash = preview(&f.root, &f.task, &f.request)?.sha256()?;
    let mut wrong = f.request.clone();
    wrong.background_ticket_id = "other-ticket".into();
    assert!(store
        .link_review(&wrong, &hash, "operator", Utc::now())
        .is_err());
    assert!(store
        .link_review(&f.request, "wrong-preview", "operator", Utc::now())
        .is_err());
    std::fs::write(&f.evidence, "changed evidence")?;
    assert!(store
        .link_review(&f.request, &hash, "operator", Utc::now())
        .is_err());
    assert_eq!(before, std::fs::read(store.path())?);
    Ok(())
}

#[test]
fn wrong_profile_copied_record_and_unknown_queue_schema_are_held() -> Result<()> {
    let f = fixture()?;
    let store = OffdeskTaskStore::new(&f.root);
    let observed = preview(&f.root, &f.task, &f.request)?;
    store.link_review(&f.request, &observed.sha256()?, "operator", Utc::now())?;
    let other = f._dir.path().join("other-profile");
    std::fs::create_dir(&other)?;
    let tasks = store.load()?;
    assert!(validate_record(&other, &tasks[0], &tasks[0].review_links[0]).is_err());
    std::fs::write(store.path(), br#"{"schema":"future","tasks":[]}"#)?;
    assert!(store.load().is_err());
    Ok(())
}

#[test]
fn malformed_result_and_unfinished_journal_cannot_be_linked() -> Result<()> {
    let f = fixture()?;
    let path = f._dir.path().join("result.json");
    std::fs::write(&path, br#"{"task_id":"other"}"#)?;
    let mut request = f.request.clone();
    request.result_sha256 = digest(&std::fs::read(&path)?);
    assert!(preview(&f.root, &f.task, &request).is_err());
    std::fs::write(&path, br#"{"task_id":"runtime-1"}"#)?;
    request.result_sha256 = digest(&std::fs::read(&path)?);
    let journal_path = journal_path(&request.owning_plan)?;
    let mut journal: serde_json::Value = serde_json::from_slice(&std::fs::read(&journal_path)?)?;
    journal["entries"][0]["committed"] = serde_json::json!(false);
    std::fs::write(&journal_path, serde_json::to_vec(&journal)?)?;
    request.owning_journal_sha256 = digest(&std::fs::read(&journal_path)?);
    assert!(preview(&f.root, &f.task, &request).is_err());
    Ok(())
}

#[test]
fn queue_lock_contention_and_replay_do_not_drop_history() -> Result<()> {
    let f = fixture()?;
    let store = OffdeskTaskStore::new(&f.root);
    let observed = preview(&f.root, &f.task, &f.request)?;
    let before = std::fs::read(store.path())?;
    let guard = crate::offdesk::tick_lock::OffdeskTickLockGuard::acquire(
        &f.root,
        Utc::now(),
        chrono::Duration::minutes(30),
    )?;
    assert!(store
        .link_review(&f.request, &observed.sha256()?, "operator", Utc::now())
        .is_err());
    assert_eq!(before, std::fs::read(store.path())?);
    drop(guard);
    store.link_review(&f.request, &observed.sha256()?, "operator", Utc::now())?;
    let linked = std::fs::read(store.path())?;
    assert_eq!(
        store.link_review(&f.request, &observed.sha256()?, "operator", Utc::now())?,
        "already_applied"
    );
    assert_eq!(linked, std::fs::read(store.path())?);
    Ok(())
}

#[test]
fn reviewed_rework_projection_reopens_on_evidence_drift_without_mutation() -> Result<()> {
    let f = fixture()?;
    let store = OffdeskTaskStore::new(&f.root);
    let hash = preview(&f.root, &f.task, &f.request)?.sha256()?;
    store.link_review(&f.request, &hash, "operator", Utc::now())?;
    let task = store.load()?.remove(0);
    let before = std::fs::read(store.path())?;
    let view = task.operator_view_with_profile(&f.root);
    assert_eq!(view.next_safe_action.kind, "recorded_rework");
    assert!(!view.next_safe_action.requires_operator_review);
    assert_eq!(
        view.review.unwrap().remaining_work,
        ["revise the bounded protocol"]
    );
    let version = attention_version(&task);
    std::fs::write(&f.evidence, "changed")?;
    let view = task.operator_view_with_profile(&f.root);
    assert_eq!(view.next_safe_action.kind, "review_binding_changed");
    assert!(view.next_safe_action.requires_operator_review);
    assert_ne!(version, attention_version(&task));
    assert_eq!(before, std::fs::read(store.path())?);
    Ok(())
}

#[test]
fn invalid_predecessor_preserves_completed_history_and_binds_successor_evidence() -> Result<()> {
    let mut f = fixture()?;
    let mut successor = f.task.clone();
    successor.task_id = "runtime-2".into();
    successor.request_id = "request-2".into();
    successor.background_ticket_id = Some("ticket-2".into());
    successor.result_artifact_path = Some("successor.json".into());
    let next_path = f._dir.path().join("successor.json");
    let next = serde_json::json!({"task_id":"runtime-2", "request_id":"request-2", "ticket_id":"ticket-2", "status":"pending_user_review",
        "evidence":[{"kind":"check", "path":"evidence.md", "sha256":digest(&std::fs::read(&f.evidence)?)}]});
    std::fs::write(&next_path, serde_json::to_vec(&next)?)?;
    OffdeskTaskStore::new(&f.root).save(&[f.task.clone(), successor.clone()])?;
    let current = preview(&f.root, &f.task, &f.request)?;
    let assessment = ResultAssessment {
        schema: "offdesk_result_assessment.v1".into(),
        task_id: f.task.task_id.clone(),
        request_id: f.task.request_id.clone(),
        ticket_id: "ticket-1".into(),
        result_sha256: f.request.result_sha256.clone(),
        classification: "invalid_result".into(),
        reason: "Historical blocked/no-read attempt, despite a completed wrapper".into(),
        evidence_refs: current.decision.evidence_refs,
        successor: Some(SuccessorReference {
            task_id: successor.task_id.clone(),
            request_id: successor.request_id.clone(),
            ticket_id: "ticket-2".into(),
            runtime_sha256: runtime_sha256(&successor)?,
            result_sha256: digest(&std::fs::read(&next_path)?),
            legacy_evidence: vec![],
        }),
    };
    let path = f._dir.path().join("assessment.json");
    std::fs::write(&path, serde_json::to_vec(&assessment)?)?;
    f.request.assessment = Some(AssessmentReference {
        path: path.clone(),
        sha256: digest(&std::fs::read(&path)?),
    });
    let p = preview(&f.root, &f.task, &f.request)?;
    assert_eq!(p.result_validity, "invalid_result");
    assert!(p.assessment.unwrap().successor.is_some());
    assert_eq!(f.task.status, OffdeskTaskStatus::Completed);
    let before = std::fs::read(OffdeskTaskStore::new(&f.root).path())?;
    std::fs::write(&next_path, b"{broken")?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    assert_eq!(
        before,
        std::fs::read(OffdeskTaskStore::new(&f.root).path())?
    );
    Ok(())
}

#[test]
fn blocked_result_cannot_be_reclassified_as_successful_review() -> Result<()> {
    let mut f = fixture()?;
    std::fs::write(
        f._dir.path().join("result.json"),
        br#"{"task_id":"runtime-1","status":"blocked","exit_code":0}"#,
    )?;
    f.request.result_sha256 = digest(&std::fs::read(f._dir.path().join("result.json"))?);
    let p = preview(&f.root, &f.task, &f.request)?;
    assert_eq!(p.result_validity, "invalid_result");
    let assessment = ResultAssessment {
        schema: "offdesk_result_assessment.v1".into(),
        task_id: f.task.task_id.clone(),
        request_id: f.task.request_id.clone(),
        ticket_id: "ticket-1".into(),
        result_sha256: f.request.result_sha256.clone(),
        classification: "evidence_reviewed".into(),
        reason: "unsupported claim".into(),
        evidence_refs: p.decision.evidence_refs,
        successor: None,
    };
    let path = f._dir.path().join("assessment.json");
    std::fs::write(&path, serde_json::to_vec(&assessment)?)?;
    f.request.assessment = Some(AssessmentReference {
        sha256: digest(&std::fs::read(&path)?),
        path,
    });
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    Ok(())
}

#[test]
fn applicable_closeout_requires_exact_profile_attempt_and_hash() -> Result<()> {
    let mut f = fixture()?;
    let path = f.root.join("offdesk_closeouts/run/closeout_plan.json");
    std::fs::create_dir_all(path.parent().unwrap())?;
    std::fs::write(
        &path,
        serde_json::to_vec(
            &serde_json::json!({"profile":"profile","closeout_id":"closeout-1",
        "tasks":[{"task_id":"runtime-1","request_id":"request-1","project_key":"project"}]}),
        )?,
    )?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    f.request.closeouts.push(AssessmentReference {
        path: path.clone(),
        sha256: digest(&std::fs::read(&path)?),
    });
    assert!(preview(&f.root, &f.task, &f.request).is_ok());
    std::fs::write(&path, "{}")?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    Ok(())
}

#[test]
fn partial_rework_and_defer_keep_owning_followups_and_do_not_repeat_initial_review() -> Result<()> {
    for (state, expected) in [
        (
            ondesk_plan::ReviewState::PartiallyAccepted,
            "recorded_partial",
        ),
        (ondesk_plan::ReviewState::Rework, "recorded_rework"),
        (ondesk_plan::ReviewState::Deferred, "recorded_defer"),
    ] {
        let f = fixture_with_state(state)?;
        let p = preview(&f.root, &f.task, &f.request)?;
        let store = OffdeskTaskStore::new(&f.root);
        store.link_review(&f.request, &p.sha256()?, "operator", Utc::now())?;
        let task = store.load()?.remove(0);
        let view = task.operator_view_with_profile(&f.root);
        assert_eq!(view.next_safe_action.kind, expected);
        assert!(!view.next_safe_action.requires_operator_review);
        assert!(!view.review.unwrap().remaining_work.is_empty());
        assert_eq!(task.status, OffdeskTaskStatus::Completed);
    }
    let f = fixture_with_state(ondesk_plan::ReviewState::Accepted)?;
    assert!(
        preview(&f.root, &f.task, &f.request).is_err(),
        "completion without an evidence assessment must not qualify accepted work"
    );
    Ok(())
}

fn put_json(path: &Path, value: &serde_json::Value) -> Result<AssessmentReference> {
    std::fs::write(path, serde_json::to_vec(value)?)?;
    Ok(AssessmentReference {
        path: path.to_owned(),
        sha256: digest(&std::fs::read(path)?),
    })
}

fn historical_fixture() -> Result<Fixture> {
    let mut f = fixture_with_state(ondesk_plan::ReviewState::PartiallyAccepted)?;
    let before = f._dir.path().join("historical-before.md");
    let after = f._dir.path().join("historical-after.md");
    std::fs::write(&before, f.source.replace("bounded review", "earlier scope"))?;
    std::fs::write(&after, &f.source)?;
    let path = journal_path(&f.request.owning_plan)?;
    let mut j: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)?;
    j["entries"].as_array_mut().unwrap().insert(
        0,
        serde_json::json!({
        "request":{"schema":"ondesk_plan_change_request.v1","request_id":"historical-1",
        "project_key":"project","source_path":f.request.owning_plan,
        "baseline_sha256":digest(b"unknown earlier version"),
        "candidate_sha256":digest(&std::fs::read(&before)?),"decision_ref":"fixture:historical"},
        "committed":true}),
    );
    std::fs::write(&path, serde_json::to_vec(&j)?)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(&path)?);
    Ok(f)
}

fn attach_boundary(f: &mut Fixture, start: usize) -> Result<()> {
    let j: serde_json::Value =
        serde_json::from_slice(&std::fs::read(journal_path(&f.request.owning_plan)?)?)?;
    let entries = j["entries"].as_array().unwrap();
    let gap = serde_json::json!({"previous_request_id":entries[start-1]["request"]["request_id"],
        "next_request_id":entries[start]["request"]["request_id"],
        "previous_candidate_sha256":entries[start-1]["request"]["candidate_sha256"],
        "next_baseline_sha256":entries[start]["request"]["baseline_sha256"]});
    let identity =
        crate::process::filesystem_file_identity(&std::fs::File::open(&f.root)?.metadata()?)
            .unwrap();
    let request_hash = historical_boundary::request_sha256(&f.request)?;
    let agreement_path = f._dir.path().join("boundary-agreement.json");
    let agreement = put_json(
        &agreement_path,
        &serde_json::json!({
        "schema":"offdesk_historical_journal_boundary_agreement.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"journal_entries":entries.len(),"suffix_start_index":start,"gap":gap,
        "decided_by":"fixture-user","decision_ref":"fixture:explicit-historical-boundary",
        "permitted_effect":"link_current_decision_with_unknown_historical_transition"}),
    )?;
    let boundary_path = f._dir.path().join("boundary.json");
    f.request.historical_boundary = Some(put_json(
        &boundary_path,
        &serde_json::json!({
        "schema":"offdesk_historical_journal_boundary.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"journal_entries":entries.len(),"suffix_start_index":start,
        "continuous_suffix_entries":entries.len()-start,"gap":gap,
        "previous_document":{"path":f._dir.path().join("historical-before.md"),"sha256":gap["previous_candidate_sha256"]},
        "next_document":{"path":f._dir.path().join("historical-after.md"),"sha256":gap["next_baseline_sha256"]},
        "agreement":agreement}),
    )?);
    Ok(())
}

#[test]
fn historical_gap_without_explicit_boundary_keeps_default_refusal() -> Result<()> {
    let f = historical_fixture()?;
    let queue = std::fs::read(OffdeskTaskStore::new(&f.root).path())?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("discontinuous"));
    assert_eq!(queue, std::fs::read(OffdeskTaskStore::new(&f.root).path())?);
    Ok(())
}

#[test]
fn exact_boundary_links_only_the_current_decision_and_preserves_history() -> Result<()> {
    let mut f = historical_fixture()?;
    attach_boundary(&mut f, 1)?;
    let journal = journal_path(&f.request.owning_plan)?;
    let old_journal = std::fs::read(&journal)?;
    let old_plan = std::fs::read(&f.request.owning_plan)?;
    let old_result = std::fs::read(f._dir.path().join("result.json"))?;
    let p = preview(&f.root, &f.task, &f.request)?;
    assert_eq!(
        p.historical_boundary
            .as_ref()
            .unwrap()
            .historical_transition,
        "unknown_explicitly_acknowledged_for_this_review_link"
    );
    let store = OffdeskTaskStore::new(&f.root);
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "applied"
    );
    let bytes = std::fs::read(store.path())?;
    let value: serde_json::Value = serde_json::from_slice(&bytes)?;
    assert_eq!(value["schema"], "offdesk_task_queue.v3");
    let tasks = store.load()?;
    assert_eq!(runtime_sha256(&tasks[0])?, runtime_sha256(&f.task)?);
    validate_record(&f.root, &tasks[0], &tasks[0].review_links[0])?;
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "already_applied"
    );
    assert_eq!(bytes, std::fs::read(store.path())?);
    assert_eq!(old_journal, std::fs::read(journal)?);
    assert_eq!(old_plan, std::fs::read(&f.request.owning_plan)?);
    assert_eq!(
        old_result,
        std::fs::read(f._dir.path().join("result.json"))?
    );
    let mut disguised = value.clone();
    disguised["schema"] = serde_json::json!("offdesk_task_queue.v2");
    assert!(crate::migrations::v005_task_review_links::decode(&disguised.to_string()).is_err());
    assert_eq!(
        crate::migrations::v005_task_review_links::encode(&[], Some(&value.to_string()))?["schema"],
        "offdesk_task_queue.v3"
    );
    Ok(())
}

#[test]
fn rehashed_boundary_cannot_change_scope_profile_range_or_endpoints() -> Result<()> {
    let mut f = historical_fixture()?;
    attach_boundary(&mut f, 1)?;
    let pin = f.request.historical_boundary.clone().unwrap();
    let original: serde_json::Value = serde_json::from_slice(&std::fs::read(&pin.path)?)?;
    for (key, value) in [
        ("review_request_sha256", serde_json::json!("0".repeat(64))),
        ("profile_identity", serde_json::json!([0, 0])),
        ("journal_entries", serde_json::json!(3)),
        ("suffix_start_index", serde_json::json!(0)),
        ("continuous_suffix_entries", serde_json::json!(0)),
        (
            "gap",
            serde_json::json!({"previous_request_id":"wrong","next_request_id":"review-1","previous_candidate_sha256":"0".repeat(64),"next_baseline_sha256":"1".repeat(64)}),
        ),
        (
            "previous_document",
            serde_json::json!({"path":f.evidence,"sha256":"0".repeat(64)}),
        ),
    ] {
        let mut wrong = original.clone();
        wrong[key] = value;
        f.request.historical_boundary = Some(put_json(&pin.path, &wrong)?);
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{key}");
    }
    Ok(())
}

#[test]
fn boundary_agreement_must_independently_match_the_selected_request_and_gap() -> Result<()> {
    let mut f = historical_fixture()?;
    attach_boundary(&mut f, 1)?;
    let pin = f.request.historical_boundary.clone().unwrap();
    let boundary: serde_json::Value = serde_json::from_slice(&std::fs::read(&pin.path)?)?;
    let path = f._dir.path().join("boundary-agreement.json");
    let original: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)?;
    for (key, value) in [
        ("review_request_sha256", serde_json::json!("0".repeat(64))),
        ("suffix_start_index", serde_json::json!(0)),
        ("decided_by", serde_json::json!("")),
        ("permitted_effect", serde_json::json!("accept_science")),
    ] {
        let mut wrong = original.clone();
        wrong[key] = value;
        let mut selected = boundary.clone();
        selected["agreement"] = serde_json::to_value(put_json(&path, &wrong)?)?;
        f.request.historical_boundary = Some(put_json(&pin.path, &selected)?);
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{key}");
    }
    Ok(())
}

#[test]
fn even_rebound_boundary_does_not_allow_an_additional_gap_or_uncommitted_entry() -> Result<()> {
    let mut f = historical_fixture()?;
    let path = journal_path(&f.request.owning_plan)?;
    let mut journal: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)?;
    let mut prefix = journal["entries"][0].clone();
    prefix["request"]["request_id"] = serde_json::json!("extra-prefix");
    prefix["request"]["candidate_sha256"] = serde_json::json!("a".repeat(64));
    journal["entries"].as_array_mut().unwrap().insert(0, prefix);
    std::fs::write(&path, serde_json::to_vec(&journal)?)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(&path)?);
    attach_boundary(&mut f, 2)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("discontinuous"));
    journal["entries"].as_array_mut().unwrap().remove(0);
    journal["entries"][0]["committed"] = serde_json::json!(false);
    std::fs::write(&path, serde_json::to_vec(&journal)?)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(&path)?);
    attach_boundary(&mut f, 1)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("unfinished"));
    Ok(())
}

#[test]
fn boundary_request_changes_and_later_decisions_cannot_reuse_agreement() -> Result<()> {
    let mut f = historical_fixture()?;
    attach_boundary(&mut f, 1)?;
    for field in [
        "link_request_id",
        "task_id",
        "request_id",
        "decision_request_id",
        "owning_task_id",
        "result_sha256",
        "runtime_sha256",
        "owning_plan_sha256",
        "owning_journal_sha256",
    ] {
        let mut value = serde_json::to_value(&f.request)?;
        value[field] = serde_json::json!("wrong");
        let changed: ReviewLinkRequest = serde_json::from_value(value)?;
        assert!(preview(&f.root, &f.task, &changed).is_err(), "{field}");
    }
    let path = journal_path(&f.request.owning_plan)?;
    let mut j: serde_json::Value = serde_json::from_slice(&std::fs::read(&path)?)?;
    let mut next = j["entries"][1].clone();
    next["request"]["request_id"] = serde_json::json!("review-2");
    next["request"]["baseline_sha256"] = serde_json::json!(f.request.owning_plan_sha256);
    next["request"]["review_record"]["request_id"] = serde_json::json!("review-2");
    next["request"]["review_record"]["baseline_sha256"] =
        serde_json::json!(f.request.owning_plan_sha256);
    j["entries"].as_array_mut().unwrap().push(next);
    std::fs::write(&path, serde_json::to_vec(&j)?)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(&path)?);
    attach_boundary(&mut f, 1)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("superseded"));
    Ok(())
}

#[test]
fn linked_boundary_artifact_drift_reopens_review_without_rewriting_history() -> Result<()> {
    let mut f = historical_fixture()?;
    attach_boundary(&mut f, 1)?;
    let store = OffdeskTaskStore::new(&f.root);
    let p = preview(&f.root, &f.task, &f.request)?;
    store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?;
    let task = store.load()?.remove(0);
    let before = std::fs::read(store.path())?;
    assert_eq!(
        task.operator_view_with_profile(&f.root)
            .next_safe_action
            .kind,
        "recorded_partial"
    );
    let version = attention_version(&task);
    std::fs::write(f._dir.path().join("boundary-agreement.json"), "{}")?;
    assert!(validate_record(&f.root, &task, &task.review_links[0]).is_err());
    assert_eq!(
        task.operator_view_with_profile(&f.root)
            .next_safe_action
            .kind,
        "review_binding_changed"
    );
    assert_ne!(version, attention_version(&task));
    assert_eq!(before, std::fs::read(store.path())?);
    Ok(())
}

#[path = "review_link_prefix_tests.rs"]
mod prefix_tests;

#[path = "review_link_narrative_tests.rs"]
mod narrative_tests;
