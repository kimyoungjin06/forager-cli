use super::*;
use forager::offdesk::workflow::review_link::{self, AssessmentReference};

fn accepted_fixture() -> Result<(Fixture, PathBuf, Value)> {
    let f = fixture_with_state(ReviewState::Accepted)?;
    let dir = f.profile.join("offdesk_closeouts/fixture");
    fs::create_dir_all(&dir)?;
    let refs = json!([{"project_key":"project","task_id":"runtime-1","request_id":"request-1"}]);
    let plan = dir.join("closeout_plan.json");
    fs::write(
        &plan,
        serde_json::to_vec(
            &json!({"closeout_id":"accepted-fixture","profile":"default",
        "generated_at":"2026-10-18T00:00:00Z","tasks":refs,"summary":{},"artifacts":{}}),
        )?,
    )?;
    let review = json!({"closeout_id":"accepted-fixture","profile":"default","review_id":"seed",
        "reviewed_at":"2026-10-18T01:00:00Z","verdict":"approved","applies_to_tasks":refs,
        "closeout_receipt":{"receipt_id":"receipt-seed","verdict":"approved","generated_at":"2026-10-18T01:00:00Z",
            "acceptance_status":"approved_with_followups","verification_status":"pending",
            "evidence_status":"review_ready","retention_review":"not_required","wiki_promotion_state":"no_candidate","stale_task_count":0,
            "missing_evidence":[],"unsafe_operations":[],"required_first_reads":[],"open_decisions":[]}});
    let review_path = dir.join("closeout_review_seed.json");
    fs::write(&review_path, serde_json::to_vec(&review)?)?;
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let assessment = f._dir.path().join("assessment.json");
    fs::write(
        &assessment,
        serde_json::to_vec(&json!({"schema":"offdesk_result_assessment.v1",
        "task_id":"runtime-1","request_id":"request-1","ticket_id":"ticket-1","result_sha256":request.result_sha256,
        "classification":"evidence_reviewed","reason":"Fixture user reviewed this exact result",
        "evidence_refs":[format!("sha256:{}:{}",hash(&fs::read(&f.result)?),f.result.display())]}))?,
    )?;
    request.assessment = Some(AssessmentReference {
        path: assessment.clone(),
        sha256: hash(&fs::read(&assessment)?),
    });
    request.closeouts.push(AssessmentReference {
        path: plan.clone(),
        sha256: hash(&fs::read(plan)?),
    });
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    Ok((f, review_path, review))
}

fn confirm(f: &Fixture) -> Result<()> {
    let preview = run(f, &[])?;
    assert!(
        preview.status.success(),
        "{}",
        String::from_utf8_lossy(&preview.stderr)
    );
    let value: Value = serde_json::from_slice(&preview.stdout)?;
    let output = run(
        f,
        &[
            "--confirm",
            "--expected-preview-sha256",
            value["preview_sha256"].as_str().unwrap(),
            "--by",
            "fixture-user",
        ],
    )?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}

fn bind_review(f: &Fixture, path: &Path) -> Result<()> {
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    request.link_request_id = "link-exact-admin-receipt".into();
    request.closeouts.truncate(1);
    request.closeouts.push(AssessmentReference {
        path: path.into(),
        sha256: hash(&fs::read(path)?),
    });
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    confirm(f)
}

#[test]
fn accepted_result_requires_exact_resolved_receipt_and_agrees_across_readers() -> Result<()> {
    let (f, source, mut review) = accepted_fixture()?;
    review["closeout_receipt"]["retention_review"] = json!("required");
    review["closeout_receipt"]["open_decisions"] =
        json!([{"kind":"archive_review","detail":"preserve log","suggested_command":"inspect"}]);
    fs::write(&source, serde_json::to_vec(&review)?)?;
    confirm(&f)?;
    assert_eq!(
        cli_json(&f, &["status", "--json"])?["closeout_required_offdesk_tasks"],
        1
    );
    let resolved = cli_json(
        &f,
        &[
            "offdesk",
            "closeout-decision",
            "--closeout-id",
            "accepted-fixture",
            "--kind",
            "archive_review",
            "--decision",
            "preserve-in-place",
            "--reason",
            "Fixture preservation decision",
            "--json",
        ],
    )?;
    assert_eq!(
        resolved["closeout_receipt"]["retention_review"],
        "resolved_preserve_in_place"
    );
    assert_eq!(
        cli_json(&f, &["status", "--json"])?["closeout_required_offdesk_tasks"],
        1
    );
    let path = PathBuf::from(
        resolved["artifacts"]["review_record_json"]
            .as_str()
            .unwrap(),
    );
    let receipt = PathBuf::from(
        resolved["artifacts"]["closeout_receipt_json"]
            .as_str()
            .unwrap(),
    );
    let raw = fs::read(&path)?;
    let raw_receipt = fs::read(&receipt)?;
    bind_review(&f, &path)?;
    let queue = fs::read(f.profile.join("offdesk_tasks.json"))?;
    let status = cli_json(&f, &["status", "--json"])?;
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    assert_eq!(status["closeout_state"]["accepted"], 1);
    assert_eq!(status["closeout_state"]["reviewed_nonaccepted"], 0);
    let workstation = cli_json(&f, &["ondesk", "workstation-surface", "--json"])?;
    assert_eq!(
        workstation["projects"][0]["task_items"][0]["requires_operator_review"],
        false
    );
    assert_eq!(workstation["accepted_truth_recovery"]["items"], json!([]));
    let surface = cli_json(
        &f,
        &[
            "ondesk",
            "review-surface",
            "--project-key",
            "project",
            "--json",
        ],
    )?;
    assert_eq!(surface["accepted_truth"]["status"], "accepted");
    assert_eq!(surface["closeout"]["receipt_status"], "accepted");
    assert_eq!(
        surface["closeout"]["recorded_receipt_status"],
        "approved_with_followups"
    );
    assert_eq!(
        surface["closeout"]["acceptance_basis"],
        "accepted_owning_decision_and_exact_administrative_receipt"
    );
    assert_eq!(surface["closeout"]["verification_status"], "recorded");
    let prompt = cli_json(
        &f,
        &[
            "ondesk",
            "prompt-package",
            "--project-key",
            "project",
            "--json",
        ],
    )?;
    assert_eq!(
        prompt["review_surface"]["accepted_truth"]["status"],
        "accepted"
    );
    assert!(prompt
        .to_string()
        .contains("closeout_receipt_status: accepted"));
    let retired = command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
        .args([
            "offdesk",
            "closeout-retire",
            "--closeout-id",
            "accepted-fixture",
            "--reason",
            "must refuse",
            "--json",
        ])
        .output()?;
    assert!(!retired.status.success());
    confirm(&f)?;
    assert_eq!(queue, fs::read(f.profile.join("offdesk_tasks.json"))?);
    assert_eq!(raw, fs::read(&path)?);
    assert_eq!(raw_receipt, fs::read(&receipt)?);
    assert_eq!(
        resolved["closeout_receipt"]["acceptance_status"],
        "approved_with_followups"
    );
    fs::write(&f.result, b"changed result")?;
    assert_eq!(
        cli_json(&f, &["status", "--json"])?["closeout_required_offdesk_tasks"],
        1
    );
    assert_ne!(
        cli_json(
            &f,
            &[
                "ondesk",
                "review-surface",
                "--project-key",
                "project",
                "--json"
            ]
        )?["accepted_truth"]["status"],
        "accepted"
    );
    Ok(())
}

#[test]
fn even_explicitly_bound_receipts_keep_all_adverse_gates() -> Result<()> {
    for (field, value) in [
        ("evidence_status", json!("missing")),
        ("stale_task_count", json!(1)),
        ("verification_status", json!("failed")),
        ("retention_review", json!("required")),
        ("wiki_promotion_state", json!("pending")),
        ("open_decisions", json!([{"kind":"archive_review"}])),
        ("missing_evidence", json!(["not inspected"])),
        ("required_first_reads", json!(["unread"])),
        ("unsafe_operations", json!(["delete_candidate:log"])),
        ("verdict", json!("blocked")),
        ("missing_evidence", Value::Null),
    ] {
        let (f, path, mut review) = accepted_fixture()?;
        review["closeout_receipt"][field] = value;
        fs::write(&path, serde_json::to_vec(&review)?)?;
        bind_review(&f, &path)?;
        let tasks = OffdeskTaskStore::new(&f.profile).load()?;
        assert!(
            !review_link::reviewed_accepted_receipt(&f.profile, &tasks, &review),
            "{field}"
        );
        assert_ne!(
            review_link::effective_closeout_receipt(&f.profile, &tasks, &review)
                ["acceptance_status"],
            "accepted",
            "{field}"
        );
        let status = cli_json(&f, &["status", "--json"])?;
        assert_eq!(status["closeout_required_offdesk_tasks"], 1, "{field}");
        assert_eq!(status["closeout_state"]["accepted"], 0, "{field}");
    }
    Ok(())
}

#[test]
fn administrative_approval_alone_and_cross_attempt_scope_never_accept_results() -> Result<()> {
    let (f, path, review) = accepted_fixture()?;
    let tasks = OffdeskTaskStore::new(&f.profile).load()?;
    assert!(!review_link::reviewed_accepted_receipt(
        &f.profile, &tasks, &review
    ));
    assert_eq!(
        cli_json(&f, &["status", "--json"])?["closeout_state"]["accepted"],
        0
    );
    bind_review(&f, &path)?;
    let tasks = OffdeskTaskStore::new(&f.profile).load()?;
    for pointer in [
        "/applies_to_tasks/0/request_id",
        "/applies_to_tasks/0/project_key",
        "/profile",
    ] {
        let mut bad = review.clone();
        *bad.pointer_mut(pointer).unwrap() = json!("other");
        assert!(!review_link::reviewed_accepted_receipt(
            &f.profile, &tasks, &bad
        ));
    }
    let mut mixed = review.clone();
    mixed["applies_to_tasks"]
        .as_array_mut()
        .unwrap()
        .push(json!({"task_id":"unreviewed","request_id":"other","project_key":"project"}));
    assert!(!review_link::reviewed_accepted_receipt(
        &f.profile, &tasks, &mixed
    ));
    Ok(())
}
