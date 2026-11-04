use anyhow::Result;
use forager::offdesk::{
    workflow::{
        ondesk_plan::ReviewState,
        ondesk_plan_store,
        ondesk_review::{self, ReviewDecisions, TaskDecision},
        review_link::{runtime_sha256, ReviewLinkRequest},
    },
    OffdeskTask, OffdeskTaskStore,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    process::{Command, Output},
};
use tempfile::{tempdir, TempDir};

fn hash(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

struct Fixture {
    _dir: TempDir,
    home: PathBuf,
    profile: PathBuf,
    request: PathBuf,
    result: PathBuf,
    source: String,
}

fn fixture() -> Result<Fixture> {
    fixture_with_state(ReviewState::Rework)
}

fn fixture_with_state(state: ReviewState) -> Result<Fixture> {
    let dir = tempdir()?;
    let home = dir.path().join("home");
    let profile = home.join(".config/forager/profiles/default");
    fs::create_dir_all(&profile)?;
    let result = dir.path().join("result.json");
    fs::write(
        &result,
        br#"{"task_id":"runtime-1","status":"pending_user_review"}"#,
    )?;
    let task: OffdeskTask = serde_json::from_value(
        json!({"task_id":"runtime-1","request_id":"request-1","project_key":"project","status":"completed","capability_id":"dispatch.runtime","runner_kind":"local_tmux","command":"test","workdir":dir.path(),"background_ticket_id":"ticket-1","created_at":"2026-10-08T00:00:00Z","updated_at":"2026-10-08T01:00:00Z","result_artifact_path":result}),
    )?;
    OffdeskTaskStore::new(&profile).save(std::slice::from_ref(&task))?;
    let plan = dir.path().join("TODO.md");
    let source = format!(
        "```forager-plan\n{}\n```\n",
        json!({"schema":"ondesk_task_plan.v1","project_key":"project","tasks":[{"task_id":"owner-1","goal":"review packet","completion_criteria":["record decision"],"depends_on":[],"review_state":"unreviewed","evidence_refs":[]}]})
    );
    fs::write(&plan, &source)?;
    let proposal = ondesk_review::propose(
        &source,
        &plan,
        ReviewDecisions {
            request_id: "decision-1".into(),
            project_key: "project".into(),
            baseline_sha256: hash(source.as_bytes()),
            decision_ref: "user:already-rework".into(),
            decided_by: "user".into(),
            tasks: vec![TaskDecision {
                task_id: "owner-1".into(),
                disposition: state,
                evidence_refs: vec![format!(
                    "sha256:{}:{}",
                    hash(&fs::read(&result)?),
                    result.display()
                )],
                remaining_work: if state == ReviewState::Accepted {
                    vec![]
                } else {
                    vec!["revise protocol".into()]
                },
                unanswered_questions: vec![],
            }],
            shift_review: None,
            reconciliation: None,
        },
    )?;
    ondesk_plan_store::apply(&proposal.change_request, &proposal.candidate_markdown, true)?;
    let request = dir.path().join("request.json");
    fs::write(
        &request,
        serde_json::to_vec(&ReviewLinkRequest {
            schema: "offdesk_review_link_request.v1".into(),
            link_request_id: "link-1".into(),
            profile_root: profile.clone(),
            task_id: task.task_id.clone(),
            request_id: task.request_id.clone(),
            background_ticket_id: "ticket-1".into(),
            runtime_sha256: runtime_sha256(&task)?,
            result_sha256: hash(&fs::read(&result)?),
            owning_plan: plan.clone(),
            owning_plan_sha256: hash(&fs::read(&plan)?),
            owning_journal_sha256: hash(&fs::read(
                plan.with_file_name(".TODO.md.forager-plan-journal.json"),
            )?),
            decision_request_id: "decision-1".into(),
            owning_task_id: "owner-1".into(),
            assessment: None,
            closeouts: vec![],
            historical_boundary: None,
            historical_prefix: None,
            narrative_reconciliation: None,
        })?,
    )?;
    Ok(Fixture {
        _dir: dir,
        home,
        profile,
        request,
        result,
        source,
    })
}

fn command(binary: &Path, home: &Path) -> Command {
    let mut c = Command::new(binary);
    c.env("HOME", home)
        .env("XDG_CONFIG_HOME", home.join(".config"))
        .env("XDG_STATE_HOME", home.join(".local/state"))
        .env("XDG_CACHE_HOME", home.join(".cache"))
        .env_remove("FORAGER_PROFILE");
    c
}

fn run(f: &Fixture, extra: &[&str]) -> Result<Output> {
    Ok(command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
        .args(["offdesk", "review-link"])
        .arg(&f.request)
        .args(extra)
        .output()?)
}

#[test]
fn review_link_cli_preview_confirm_replay_and_drift_are_bounded() -> Result<()> {
    let f = fixture()?;
    let queue = f.profile.join("offdesk_tasks.json");
    let before = fs::read(&queue)?;
    let original = fs::read(&f.result)?;
    let preview = run(&f, &[])?;
    assert!(
        preview.status.success(),
        "{}",
        String::from_utf8_lossy(&preview.stderr)
    );
    let view: Value = serde_json::from_slice(&preview.stdout)?;
    assert_eq!(view["outcome"], "preview");
    assert_eq!(before, fs::read(&queue)?);
    assert!(!run(&f, &["--confirm"])?.status.success());
    assert_eq!(before, fs::read(&queue)?);
    let hash = view["preview_sha256"].as_str().unwrap();
    let flags = [
        "--confirm",
        "--expected-preview-sha256",
        hash,
        "--by",
        "local-operator",
    ];
    let apply = run(&f, &flags)?;
    assert!(
        apply.status.success(),
        "{}",
        String::from_utf8_lossy(&apply.stderr)
    );
    let linked = fs::read(&queue)?;
    let repeat = run(&f, &flags)?;
    assert!(repeat.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&repeat.stdout)?["outcome"],
        "already_applied"
    );
    assert_eq!(linked, fs::read(&queue)?);
    assert_eq!(original, fs::read(&f.result)?);
    fs::write(&f.result, br#"{"task_id":"other"}"#)?;
    assert!(!run(&f, &flags)?.status.success());
    assert_eq!(linked, fs::read(&queue)?);
    assert!(!f.home.join(".config/forager/.schema_version").exists());
    Ok(())
}

#[test]
fn review_link_missing_profile_does_not_initialize_it() -> Result<()> {
    let f = fixture()?;
    let out = command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
        .args(["-p", "absent", "offdesk", "review-link"])
        .arg(&f.request)
        .output()?;
    assert!(!out.status.success());
    assert!(!f.home.join(".config/forager/profiles/absent").exists());
    assert!(!f.home.join(".config/forager/.schema_version").exists());
    Ok(())
}

#[test]
fn selected_legacy_reader_refuses_linked_queue_without_rewriting() -> Result<()> {
    let Some(binary) = std::env::var_os("FORAGER_LEGACY_TEST_BIN") else {
        return Ok(());
    };
    let f = fixture()?;
    let p = run(&f, &[])?;
    assert!(p.status.success());
    let view: Value = serde_json::from_slice(&p.stdout)?;
    let hash = view["preview_sha256"].as_str().unwrap();
    assert!(run(
        &f,
        &[
            "--confirm",
            "--expected-preview-sha256",
            hash,
            "--by",
            "operator"
        ]
    )?
    .status
    .success());
    let path = f.profile.join("offdesk_tasks.json");
    let before = fs::read(&path)?;
    let old = command(Path::new(&binary), &f.home)
        .args(["offdesk", "tasks", "--json"])
        .output()?;
    assert!(!old.status.success());
    assert_eq!(before, fs::read(path)?);
    Ok(())
}

fn cli_json(f: &Fixture, args: &[&str]) -> Result<Value> {
    let output = command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
        .args(args)
        .output()?;
    assert!(
        output.status.success(),
        "args {args:?}\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

#[test]
fn recorded_rework_and_administrative_closeout_agree_across_readers() -> Result<()> {
    let f = fixture()?;
    let dir = f.profile.join("offdesk_closeouts/fixture");
    fs::create_dir_all(&dir)?;
    let refs = json!([{"project_key":"project","task_id":"runtime-1","request_id":"request-1"}]);
    let plan = dir.join("closeout_plan.json");
    fs::write(
        &plan,
        serde_json::to_vec(
            &json!({"closeout_id":"closeout-fixture","profile":"default",
        "generated_at":"2026-10-18T00:00:00Z","tasks":refs,"summary":{},"artifacts":{}}),
        )?,
    )?;
    let source = dir.join("closeout_review_seed.json");
    fs::write(
        &source,
        serde_json::to_vec(
            &json!({"closeout_id":"closeout-fixture","profile":"default","review_id":"seed",
        "reviewed_at":"2026-10-18T01:00:00Z","verdict":"approved","applies_to_tasks":refs,
        "closeout_receipt":{"receipt_id":"receipt-seed","acceptance_status":"approved_with_followups","verification_status":"pending",
            "evidence_status":"missing","retention_review":"required","wiki_promotion_state":"no_candidate","stale_task_count":0,
            "missing_evidence":["Historical read-scope violation remains a limitation"],"unsafe_operations":[],
            "required_first_reads":[f.result],"open_decisions":[{"kind":"archive_review","detail":"one log needs a preservation decision","suggested_command":"inspect log"}]}}),
        )?,
    )?;
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    request.closeouts.push(
        forager::offdesk::workflow::review_link::AssessmentReference {
            path: plan.clone(),
            sha256: hash(&fs::read(&plan)?),
        },
    );
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    let preview: Value = serde_json::from_slice(&run(&f, &[])?.stdout)?;
    assert!(run(
        &f,
        &[
            "--confirm",
            "--expected-preview-sha256",
            preview["preview_sha256"].as_str().unwrap(),
            "--by",
            "operator"
        ]
    )?
    .status
    .success());
    let tasks = cli_json(&f, &["offdesk", "tasks", "--json"])?;
    let text = tasks.to_string();
    assert!(text.contains("recorded_rework"));
    assert!(!text.contains("cancel-task"));
    let expected = hash(&fs::read(&source)?);
    let read_ref = format!("{}={}", f.result.display(), hash(&fs::read(&f.result)?));
    let args = [
        "offdesk",
        "closeout-decision",
        "--closeout-id",
        "closeout-fixture",
        "--kind",
        "archive_review",
        "--decision",
        "preserve-in-place-administrative",
        "--expected-review-sha256",
        &expected,
        "--acknowledge-read",
        &read_ref,
        "--reason",
        "Preserve evidence; existing rework remains non-accepted",
        "--json",
    ];
    let resolved = cli_json(&f, &args)?;
    assert_eq!(
        resolved["closeout_receipt"]["acceptance_status"],
        "approved_with_followups"
    );
    assert_eq!(
        resolved["closeout_receipt"]["required_first_reads"],
        json!([])
    );
    assert_eq!(
        resolved["closeout_receipt"]["missing_evidence"],
        json!(["Historical read-scope violation remains a limitation"])
    );
    assert_eq!(
        resolved["decision_resolution"]["artifact_reads"][0]["sha256"],
        hash(&fs::read(&f.result)?)
    );
    assert_eq!(resolved["applies_file_operations"], false);
    let queue_before = fs::read(f.profile.join("offdesk_tasks.json"))?;
    let rows_before = fs::read_dir(&dir)?.count();
    let stale = command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
        .args(args)
        .output()?;
    assert!(!stale.status.success());
    assert_eq!(rows_before, fs::read_dir(&dir)?.count());
    assert_eq!(
        queue_before,
        fs::read(f.profile.join("offdesk_tasks.json"))?
    );
    let status = cli_json(&f, &["status", "--json"])?;
    assert_eq!(status["closeout_state"]["reviewed_nonaccepted"], 1);
    assert_eq!(status["closeout_state"]["accepted"], 0);
    assert_eq!(status["closeout_required_offdesk_tasks"], 0);
    let surface = cli_json(&f, &["ondesk", "workstation-surface", "--json"])?;
    assert_eq!(
        surface["projects"][0]["task_items"][0]["requires_operator_review"],
        false
    );
    assert_eq!(
        surface["accepted_truth_recovery"]["items"][0]["stage"],
        "recorded_nonaccepted"
    );
    assert_eq!(
        surface["accepted_truth_recovery"]["items"][0]["action_envelopes"],
        json!([])
    );
    let view: Value = serde_json::from_slice(
        &command(Path::new(env!("CARGO_BIN_EXE_forager")), &f.home)
            .args(["ondesk", "review-surface", "--json"])
            .output()?
            .stdout,
    )?;
    assert_ne!(view["accepted_truth"]["status"], "accepted");
    assert_eq!(
        queue_before,
        fs::read(f.profile.join("offdesk_tasks.json"))?
    );
    Ok(())
}

#[test]
fn packet_governance_requires_declared_owner_and_creates_no_competing_docs() -> Result<()> {
    let f = fixture()?;
    let packet = cli_json(
        &f,
        &["offdesk", "closeout", "--task-id", "runtime-1", "--json"],
    )?;
    assert_eq!(packet["documentation_governance"]["scope"], "packet");
    assert_eq!(
        packet["documentation_governance"]["recommendation_count"],
        0
    );
    assert!(!f._dir.path().join("PROJECT_STATE.md").exists());
    let selected = cli_json(
        &f,
        &[
            "offdesk",
            "closeout",
            "--task-id",
            "runtime-1",
            "--workdir",
            f._dir.path().to_str().unwrap(),
            "--owning-plan",
            "TODO.md",
            "--json",
        ],
    )?;
    assert_eq!(selected["documentation_governance"]["scope"], "project");
    assert_eq!(
        selected["documentation_governance"]["routing"]["selected_owning_plan"],
        "TODO.md"
    );
    assert!(
        selected["documentation_governance"]["recommendation_count"]
            .as_u64()
            .unwrap()
            > 0
    );
    assert!(!f._dir.path().join("PROJECT_STATE.md").exists());
    Ok(())
}

#[test]
fn explicit_historical_boundary_cli_confirms_partial_preserves_journal_and_rejects_older_reader(
) -> Result<()> {
    let f = fixture_with_state(ReviewState::PartiallyAccepted)?;
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let journal = request
        .owning_plan
        .with_file_name(".TODO.md.forager-plan-journal.json");
    let before_document = f._dir.path().join("before.md");
    let after_document = f._dir.path().join("after.md");
    fs::write(
        &before_document,
        f.source.replace("review packet", "earlier scope"),
    )?;
    fs::write(&after_document, &f.source)?;
    let mut j: Value = serde_json::from_slice(&fs::read(&journal)?)?;
    j["entries"].as_array_mut().unwrap().insert(0,json!({"request":{
        "schema":"ondesk_plan_change_request.v1","request_id":"old-prefix","project_key":"project",
        "source_path":request.owning_plan,"baseline_sha256":hash(b"unknown earlier document"),
        "candidate_sha256":hash(&fs::read(&before_document)?),"decision_ref":"fixture:historical"},"committed":true}));
    fs::write(&journal, serde_json::to_vec(&j)?)?;
    request.owning_journal_sha256 = hash(&fs::read(&journal)?);
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    let queue = f.profile.join("offdesk_tasks.json");
    let original_queue = fs::read(&queue)?;
    let original_plan = fs::read(&request.owning_plan)?;
    let original_journal = fs::read(&journal)?;
    let original_result = fs::read(&f.result)?;
    let refused = run(&f, &[])?;
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("discontinuous"));
    assert_eq!(original_queue, fs::read(&queue)?);
    let mut canonical = serde_json::to_value(&request)?;
    canonical.sort_all_objects();
    let request_hash = hash(&serde_json::to_vec(&canonical)?);
    let identity = forager::process::filesystem_file_identity(&fs::metadata(&f.profile)?).unwrap();
    let gap = json!({"previous_request_id":"old-prefix","next_request_id":"decision-1",
        "previous_candidate_sha256":hash(&fs::read(&before_document)?),"next_baseline_sha256":hash(&fs::read(&after_document)?)});
    let agreement = f._dir.path().join("agreement.json");
    fs::write(
        &agreement,
        serde_json::to_vec(&json!({
        "schema":"offdesk_historical_journal_boundary_agreement.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"journal_entries":2,"suffix_start_index":1,"gap":gap,
        "decided_by":"fixture-user","decision_ref":"fixture:explicit-boundary-partial",
        "permitted_effect":"link_current_decision_with_unknown_historical_transition"}))?,
    )?;
    let boundary = f._dir.path().join("boundary.json");
    fs::write(
        &boundary,
        serde_json::to_vec(&json!({
        "schema":"offdesk_historical_journal_boundary.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"journal_entries":2,"suffix_start_index":1,"continuous_suffix_entries":1,"gap":gap,
        "previous_document":{"path":before_document,"sha256":hash(&fs::read(&before_document)?)},
        "next_document":{"path":after_document,"sha256":hash(&fs::read(&after_document)?)},
        "agreement":{"path":agreement,"sha256":hash(&fs::read(&agreement)?)}}))?,
    )?;
    request.historical_boundary = Some(
        forager::offdesk::workflow::review_link::AssessmentReference {
            path: boundary.clone(),
            sha256: hash(&fs::read(&boundary)?),
        },
    );
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    let preview = run(&f, &[])?;
    assert!(
        preview.status.success(),
        "{}",
        String::from_utf8_lossy(&preview.stderr)
    );
    let p: Value = serde_json::from_slice(&preview.stdout)?;
    assert_eq!(
        p["preview"]["historical_boundary"]["historical_transition"],
        "unknown_explicitly_acknowledged_for_this_review_link"
    );
    assert_eq!(
        p["preview"]["decision"]["disposition"],
        "partially_accepted"
    );
    let flags = [
        "--confirm",
        "--expected-preview-sha256",
        p["preview_sha256"].as_str().unwrap(),
        "--by",
        "fixture-user",
    ];
    let applied = run(&f, &flags)?;
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    let linked = fs::read(&queue)?;
    assert_eq!(
        serde_json::from_slice::<Value>(&linked)?["schema"],
        "offdesk_task_queue.v3"
    );
    let replay = run(&f, &flags)?;
    assert!(replay.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&replay.stdout)?["outcome"],
        "already_applied"
    );
    assert_eq!(linked, fs::read(&queue)?);
    assert_eq!(original_journal, fs::read(&journal)?);
    assert_eq!(original_plan, fs::read(&request.owning_plan)?);
    assert_eq!(original_result, fs::read(&f.result)?);
    let tasks = OffdeskTaskStore::new(&f.profile).load()?;
    assert_eq!(
        tasks[0]
            .operator_view_with_profile(&f.profile)
            .next_safe_action
            .kind,
        "recorded_partial"
    );
    assert!(!f.home.join(".config/forager/.schema_version").exists());
    if let Some(old) = std::env::var_os("FORAGER_BOUNDARY_LEGACY_TEST_BIN") {
        let rejected = command(Path::new(&old), &f.home)
            .args(["offdesk", "tasks", "--json"])
            .output()?;
        assert!(!rejected.status.success());
        assert_eq!(linked, fs::read(&queue)?);
    }
    fs::write(&agreement, "{}")?;
    assert!(!run(&f, &flags)?.status.success());
    assert_eq!(linked, fs::read(&queue)?);
    Ok(())
}

fn select_prefix_request(f: &Fixture, snapshot: &Path, baseline: &Path) -> Result<()> {
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let j: Value = serde_json::from_slice(&fs::read(
        request
            .owning_plan
            .with_file_name(".TODO.md.forager-plan-journal.json"),
    )?)?;
    let prefix: Value = serde_json::from_slice(&fs::read(snapshot)?)?;
    let count = prefix["entries"].as_array().unwrap().len();
    let entries = j["entries"].as_array().unwrap();
    let mut gaps = Vec::new();
    for i in 1..=count {
        let a = &entries[i - 1]["request"];
        let b = &entries[i]["request"];
        if a["candidate_sha256"] != b["baseline_sha256"] {
            gaps.push(json!({"entry_index":i,"gap":{
            "previous_request_id":a["request_id"],"next_request_id":b["request_id"],
            "previous_candidate_sha256":a["candidate_sha256"],"next_baseline_sha256":b["baseline_sha256"]}}));
        }
    }
    request.historical_prefix = None;
    let mut canonical = serde_json::to_value(&request)?;
    canonical.sort_all_objects();
    let request_hash = hash(&serde_json::to_vec(&canonical)?);
    let identity = forager::process::filesystem_file_identity(&fs::metadata(&f.profile)?).unwrap();
    let agreement = f._dir.path().join("prefix-agreement.json");
    fs::write(
        &agreement,
        serde_json::to_vec(&json!({
        "schema":"offdesk_historical_journal_prefix_agreement.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"prefix_entries":count,"snapshot_sha256":hash(&fs::read(snapshot)?),
        "baseline_document_sha256":hash(&fs::read(baseline)?),"gaps":gaps,
        "decided_by":"fixture-user","decision_ref":"fixture:explicit-unknown-prefix",
        "permitted_effect":"link_current_decision_with_unknown_historical_prefix"}))?,
    )?;
    let artifact = f._dir.path().join("prefix.json");
    fs::write(
        &artifact,
        serde_json::to_vec(&json!({
        "schema":"offdesk_historical_journal_prefix.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"prefix_entries":count,"snapshot":{"path":snapshot,"sha256":hash(&fs::read(snapshot)?)},
        "baseline_document":{"path":baseline,"sha256":hash(&fs::read(baseline)?)},"gaps":gaps,
        "agreement":{"path":agreement,"sha256":hash(&fs::read(&agreement)?)}}))?,
    )?;
    request.historical_prefix = Some(
        forager::offdesk::workflow::review_link::AssessmentReference {
            path: artifact.clone(),
            sha256: hash(&fs::read(&artifact)?),
        },
    );
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    Ok(())
}

fn confirm_prefix_fixture(f: &Fixture) -> Result<Value> {
    let queue = f.profile.join("offdesk_tasks.json");
    let before = fs::read(&queue)?;
    let p = run(f, &[])?;
    assert!(p.status.success(), "{}", String::from_utf8_lossy(&p.stderr));
    let view: Value = serde_json::from_slice(&p.stdout)?;
    assert_eq!(before, fs::read(&queue)?);
    assert_eq!(
        view["preview"]["historical_prefix"]["historical_transition"],
        "unknown_prefix_explicitly_acknowledged_for_this_review_link"
    );
    let flags = [
        "--confirm",
        "--expected-preview-sha256",
        view["preview_sha256"].as_str().unwrap(),
        "--by",
        "fixture-user",
    ];
    let p = run(f, &flags)?;
    assert!(p.status.success(), "{}", String::from_utf8_lossy(&p.stderr));
    let linked = fs::read(&queue)?;
    assert_eq!(
        serde_json::from_slice::<Value>(&linked)?["schema"],
        "offdesk_task_queue.v4"
    );
    let repeat = run(f, &flags)?;
    assert!(repeat.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&repeat.stdout)?["outcome"],
        "already_applied"
    );
    assert_eq!(linked, fs::read(&queue)?);
    let task = OffdeskTaskStore::new(&f.profile).load()?.remove(0);
    assert_eq!(
        task.operator_view_with_profile(&f.profile)
            .next_safe_action
            .kind,
        "recorded_partial"
    );
    assert!(!f.home.join(".config/forager/.schema_version").exists());
    if let Some(old) = std::env::var_os("FORAGER_PREFIX_LEGACY_TEST_BIN") {
        let p = command(Path::new(&old), &f.home)
            .args(["offdesk", "tasks", "--json"])
            .output()?;
        assert!(!p.status.success());
        assert_eq!(linked, fs::read(&queue)?);
    }
    Ok(view)
}

#[test]
fn explicit_prefix_cli_preserves_legacy_entries_and_requires_complete_acknowledgment() -> Result<()>
{
    let f = fixture_with_state(ReviewState::PartiallyAccepted)?;
    let mut request: ReviewLinkRequest = serde_json::from_slice(&fs::read(&f.request)?)?;
    let journal = request
        .owning_plan
        .with_file_name(".TODO.md.forager-plan-journal.json");
    let mut j: Value = serde_json::from_slice(&fs::read(&journal)?)?;
    let prefix:Vec<_>=(0..3).map(|i|json!({"request":{
        "schema":"ondesk_plan_change_request.v1","request_id":format!("prefix-{i}"),"project_key":"project",
        "source_path":request.owning_plan,"baseline_sha256":hash(format!("unknown-{i}").as_bytes()),
        "candidate_sha256":hash(format!("candidate-{i}").as_bytes()),"decision_ref":"fixture:unknown-history"},"committed":true})).collect();
    let mut snapshot = j.clone();
    snapshot["entries"] = json!(prefix);
    let snapshot_path = f._dir.path().join("snapshot.json");
    fs::write(&snapshot_path, serde_json::to_vec(&snapshot)?)?;
    let mut entries = prefix;
    entries.extend(j["entries"].as_array().unwrap().iter().cloned());
    j["entries"] = json!(entries);
    fs::write(&journal, serde_json::to_vec(&j)?)?;
    request.owning_journal_sha256 = hash(&fs::read(&journal)?);
    fs::write(&f.request, serde_json::to_vec(&request)?)?;
    let refused = run(&f, &[])?;
    assert!(!refused.status.success());
    let baseline = f._dir.path().join("baseline.md");
    fs::write(&baseline, &f.source)?;
    select_prefix_request(&f, &snapshot_path, &baseline)?;
    let original = fs::read(&journal)?;
    let original_result = fs::read(&f.result)?;
    let view = confirm_prefix_fixture(&f)?;
    assert_eq!(
        view["preview"]["historical_prefix"]["gaps"]
            .as_array()
            .unwrap()
            .len(),
        3
    );
    assert_eq!(original, fs::read(&journal)?);
    assert_eq!(original_result, fs::read(&f.result)?);
    fs::write(&snapshot_path, "{}")?;
    assert!(!run(&f, &[])?.status.success());
    Ok(())
}

#[test]
fn selected_real_metadata_shadow_qualifies_all_nano_gaps_before_any_actual_write() -> Result<()> {
    let Some(input) = std::env::var_os("FORAGER_PREFIX_REAL_METADATA_FIXTURE") else {
        return Ok(());
    };
    let input: Value = serde_json::from_slice(&fs::read(input)?)?;
    let source = fs::read_to_string(input["source"].as_str().unwrap())?;
    let raw_journal = fs::read(input["journal"].as_str().unwrap())?;
    assert_eq!(hash(source.as_bytes()), input["input_source_sha256"]);
    assert_eq!(hash(&raw_journal), input["input_journal_sha256"]);
    let dir = tempdir()?;
    let home = dir.path().join("home");
    let profile = home.join(".config/forager/profiles/default");
    fs::create_dir_all(&profile)?;
    let owner = dir.path().join("TODO.md");
    fs::write(&owner, &source)?;
    let mut snapshot: Value = serde_json::from_slice(&raw_journal)?;
    assert_eq!(snapshot["entries"].as_array().unwrap().len(), 17);
    snapshot["source_path"] = json!(owner);
    for entry in snapshot["entries"].as_array_mut().unwrap() {
        entry["request"]["source_path"] = json!(owner);
    }
    let snapshot_path = dir.path().join("snapshot.json");
    fs::write(&snapshot_path, serde_json::to_vec(&snapshot)?)?;
    let journal = owner.with_file_name(".TODO.md.forager-plan-journal.json");
    fs::write(&journal, serde_json::to_vec(&snapshot)?)?;
    let mut decisions: ReviewDecisions =
        serde_json::from_slice(&fs::read(input["decisions"].as_str().unwrap())?)?;
    decisions.request_id = "shadow-nano-review".into();
    decisions.decision_ref = "fixture:real-metadata-shadow-only".into();
    decisions.decided_by = "fixture-user".into();
    let proposed = ondesk_review::propose(&source, &owner, decisions)?;
    ondesk_plan_store::apply(&proposed.change_request, &proposed.candidate_markdown, true)?;
    let mut task: OffdeskTask = serde_json::from_value(input["runtime_task"].clone())?;
    let original_root = PathBuf::from(&task.workdir);
    let original_result = PathBuf::from(task.result_artifact_path.as_ref().unwrap());
    let result = dir.path().join("result.json");
    fs::copy(&original_result, &result)?;
    for name in ["BOUND_REVIEW.json", "NATIVE_EVENTS.jsonl"] {
        let path = original_root.join(name);
        assert!(fs::symlink_metadata(&path)?.file_type().is_file());
        assert!(fs::metadata(&path)?.len() <= 16 * 1024 * 1024);
        fs::copy(path, dir.path().join(name))?;
    }
    task.workdir = dir.path().to_string_lossy().into_owned();
    task.result_artifact_path = Some(result.to_string_lossy().into_owned());
    task.review_links.clear();
    OffdeskTaskStore::new(&profile).save(std::slice::from_ref(&task))?;
    let request = dir.path().join("request.json");
    fs::write(
        &request,
        serde_json::to_vec(&ReviewLinkRequest {
            schema: "offdesk_review_link_request.v1".into(),
            link_request_id: "shadow-prefix-link".into(),
            profile_root: profile.clone(),
            task_id: task.task_id.clone(),
            request_id: task.request_id.clone(),
            background_ticket_id: task.background_ticket_id.clone().unwrap(),
            runtime_sha256: runtime_sha256(&task)?,
            result_sha256: hash(&fs::read(&result)?),
            owning_plan: owner.clone(),
            owning_plan_sha256: hash(&fs::read(&owner)?),
            owning_journal_sha256: hash(&fs::read(&journal)?),
            decision_request_id: proposed.change_request.request_id,
            owning_task_id: task.task_id.clone(),
            assessment: None,
            closeouts: vec![],
            historical_boundary: None,
            historical_prefix: None,
            narrative_reconciliation: None,
        })?,
    )?;
    let baseline = dir.path().join("baseline.md");
    fs::write(&baseline, &source)?;
    let f = Fixture {
        _dir: dir,
        home,
        profile,
        request,
        result,
        source,
    };
    assert!(!run(&f, &[])?.status.success());
    select_prefix_request(&f, &snapshot_path, &baseline)?;
    let before = fs::read(&journal)?;
    let view = confirm_prefix_fixture(&f)?;
    assert_eq!(view["preview"]["historical_prefix"]["prefix_entries"], 17);
    assert_eq!(
        view["preview"]["historical_prefix"]["gaps"]
            .as_array()
            .unwrap()
            .len(),
        11
    );
    assert_eq!(before, fs::read(journal)?);
    assert_eq!(hash(&raw_journal), input["input_journal_sha256"]);
    Ok(())
}

#[path = "offdesk_review_link_cli/narrative.rs"]
mod narrative;

#[path = "offdesk_review_link_cli/accepted_closeout.rs"]
mod accepted_closeout;
