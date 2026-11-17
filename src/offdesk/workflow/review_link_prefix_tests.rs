use super::*;
use serde_json::{json, Value};

fn prefix_fixture() -> Result<Fixture> {
    let mut f = fixture_with_state(ondesk_plan::ReviewState::PartiallyAccepted)?;
    let journal = journal_path(&f.request.owning_plan)?;
    let mut j: Value = serde_json::from_slice(&std::fs::read(&journal)?)?;
    let prefix: Vec<_> = (0..3)
        .map(|i| {
            json!({"request":{
        "schema":"ondesk_plan_change_request.v1","request_id":format!("old-{i}"),
        "project_key":"project","source_path":f.request.owning_plan,
        "baseline_sha256":digest(format!("unrecorded-base-{i}").as_bytes()),
        "candidate_sha256":digest(format!("old-candidate-{i}").as_bytes()),
        "decision_ref":"fixture:unknown-past"},"committed":true})
        })
        .collect();
    let mut snapshot = j.clone();
    snapshot["entries"] = json!(prefix);
    std::fs::write(
        f._dir.path().join("prefix-snapshot.json"),
        serde_json::to_vec(&snapshot)?,
    )?;
    let mut entries = prefix;
    entries.extend(j["entries"].as_array().unwrap().iter().cloned());
    j["entries"] = json!(entries);
    std::fs::write(&journal, serde_json::to_vec(&j)?)?;
    std::fs::write(f._dir.path().join("prefix-baseline.md"), &f.source)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(journal)?);
    Ok(f)
}

fn attach_prefix(f: &mut Fixture) -> Result<()> {
    let snapshot_path = f._dir.path().join("prefix-snapshot.json");
    let snapshot: Value = serde_json::from_slice(&std::fs::read(&snapshot_path)?)?;
    let count = snapshot["entries"].as_array().unwrap().len();
    let journal: Value =
        serde_json::from_slice(&std::fs::read(journal_path(&f.request.owning_plan)?)?)?;
    let entries = journal["entries"].as_array().unwrap();
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
    let identity = crate::process::filesystem_file_identity(&std::fs::metadata(&f.root)?).unwrap();
    let request_hash = historical_prefix::request_sha256(&f.request)?;
    let baseline = f._dir.path().join("prefix-baseline.md");
    let agreement = put_json(
        &f._dir.path().join("prefix-agreement.json"),
        &json!({
        "schema":"offdesk_historical_journal_prefix_agreement.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"prefix_entries":count,"snapshot_sha256":digest(&std::fs::read(&snapshot_path)?),
        "baseline_document_sha256":digest(&std::fs::read(&baseline)?),"gaps":gaps,
        "decided_by":"fixture-user","decision_ref":"fixture:explicit-complete-prefix",
        "permitted_effect":"link_current_decision_with_unknown_historical_prefix"}),
    )?;
    f.request.historical_prefix = Some(put_json(
        &f._dir.path().join("prefix.json"),
        &json!({
        "schema":"offdesk_historical_journal_prefix.v1","review_request_sha256":request_hash,
        "profile_identity":identity,"prefix_entries":count,
        "snapshot":{"path":snapshot_path,"sha256":digest(&std::fs::read(&snapshot_path)?)},
        "baseline_document":{"path":baseline,"sha256":digest(&std::fs::read(&baseline)?)},
        "gaps":gaps,"agreement":agreement}),
    )?);
    Ok(())
}

#[test]
fn multiple_gaps_without_prefix_acknowledgment_still_refuse() -> Result<()> {
    let f = prefix_fixture()?;
    let path = OffdeskTaskStore::new(&f.root).path();
    let before = std::fs::read(&path)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("discontinuous"));
    assert_eq!(before, std::fs::read(path)?);
    Ok(())
}

#[test]
fn exact_prefix_reproduces_new_decision_and_links_without_rewriting_old_history() -> Result<()> {
    let mut f = prefix_fixture()?;
    attach_prefix(&mut f)?;
    let journal = journal_path(&f.request.owning_plan)?;
    let before = std::fs::read(&journal)?;
    let p = preview(&f.root, &f.task, &f.request)?;
    let prefix = p.historical_prefix.as_ref().unwrap();
    assert_eq!(prefix.prefix_entries, 3);
    assert_eq!(prefix.gaps.len(), 3);
    assert_eq!(
        prefix.historical_transition,
        "unknown_prefix_explicitly_acknowledged_for_this_review_link"
    );
    let store = OffdeskTaskStore::new(&f.root);
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "applied"
    );
    let bytes = std::fs::read(store.path())?;
    let q: Value = serde_json::from_slice(&bytes)?;
    assert_eq!(q["schema"], "offdesk_task_queue.v4");
    let task = store.load()?.remove(0);
    assert_eq!(runtime_sha256(&task)?, runtime_sha256(&f.task)?);
    validate_record(&f.root, &task, &task.review_links[0])?;
    assert_eq!(
        task.operator_view_with_profile(&f.root)
            .next_safe_action
            .kind,
        "recorded_partial"
    );
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "already_applied"
    );
    assert_eq!(bytes, std::fs::read(store.path())?);
    assert_eq!(before, std::fs::read(journal)?);
    let mut disguised = q.clone();
    disguised["schema"] = json!("offdesk_task_queue.v3");
    assert!(crate::migrations::v005_task_review_links::decode(&disguised.to_string()).is_err());
    assert_eq!(
        crate::migrations::v005_task_review_links::encode(&[], Some(&q.to_string()))?["schema"],
        "offdesk_task_queue.v4"
    );
    Ok(())
}

#[test]
fn omitted_extra_or_rehashed_gap_cannot_be_acknowledged_implicitly() -> Result<()> {
    let mut f = prefix_fixture()?;
    attach_prefix(&mut f)?;
    let pin = f.request.historical_prefix.clone().unwrap();
    let original: Value = serde_json::from_slice(&std::fs::read(&pin.path)?)?;
    for mode in 0..3 {
        let mut wrong = original.clone();
        if mode == 0 {
            wrong["gaps"].as_array_mut().unwrap().pop();
        }
        if mode == 1 {
            wrong["gaps"]
                .as_array_mut()
                .unwrap()
                .push(original["gaps"][0].clone());
        }
        if mode == 2 {
            wrong["gaps"][0]["entry_index"] = json!(999);
        }
        f.request.historical_prefix = Some(put_json(&pin.path, &wrong)?);
        assert!(preview(&f.root, &f.task, &f.request).is_err());
    }
    Ok(())
}

#[test]
fn exact_snapshot_and_predecision_document_are_required_even_when_repinned() -> Result<()> {
    let mut f = prefix_fixture()?;
    attach_prefix(&mut f)?;
    let snapshot = f._dir.path().join("prefix-snapshot.json");
    let original = std::fs::read(&snapshot)?;
    let mut value: Value = serde_json::from_slice(&original)?;
    value["entries"][0]["request"]["decision_ref"] = json!("changed");
    std::fs::write(&snapshot, serde_json::to_vec(&value)?)?;
    attach_prefix(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("exact journal prefix"));
    std::fs::write(snapshot, original)?;
    std::fs::write(f._dir.path().join("prefix-baseline.md"), "changed baseline")?;
    attach_prefix(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    Ok(())
}

#[test]
fn acknowledged_prefix_still_checks_every_old_scope_id_and_commit() -> Result<()> {
    for mode in 0..3 {
        let mut f = prefix_fixture()?;
        let journal = journal_path(&f.request.owning_plan)?;
        let mut j: Value = serde_json::from_slice(&std::fs::read(&journal)?)?;
        if mode == 0 {
            j["entries"][0]["committed"] = json!(false);
        }
        if mode == 1 {
            j["entries"][0]["request"]["project_key"] = json!("other");
        }
        if mode == 2 {
            j["entries"][0]["request"]["request_id"] =
                j["entries"][1]["request"]["request_id"].clone();
        }
        std::fs::write(&journal, serde_json::to_vec(&j)?)?;
        f.request.owning_journal_sha256 = digest(&std::fs::read(&journal)?);
        let mut snapshot = j.clone();
        snapshot["entries"] = json!(&j["entries"].as_array().unwrap()[..3]);
        std::fs::write(
            f._dir.path().join("prefix-snapshot.json"),
            serde_json::to_vec(&snapshot)?,
        )?;
        attach_prefix(&mut f)?;
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{mode}");
    }
    Ok(())
}

#[test]
fn prefix_does_not_waive_new_suffix_gaps_or_changed_selected_decision() -> Result<()> {
    let mut f = prefix_fixture()?;
    let journal = journal_path(&f.request.owning_plan)?;
    let mut j: Value = serde_json::from_slice(&std::fs::read(&journal)?)?;
    let mut later = j["entries"][0].clone();
    later["request"]["request_id"] = json!("new-gap");
    later["request"]["candidate_sha256"] = json!(f.request.owning_plan_sha256);
    j["entries"].as_array_mut().unwrap().push(later);
    std::fs::write(&journal, serde_json::to_vec(&j)?)?;
    f.request.owning_journal_sha256 = digest(&std::fs::read(&journal)?);
    attach_prefix(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("discontinuous"));
    let mut f = prefix_fixture()?;
    f.request.decision_request_id = "different-new-decision".into();
    attach_prefix(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("start the strict suffix"));
    Ok(())
}

#[test]
fn prefix_agreement_and_request_scope_cannot_be_substituted() -> Result<()> {
    let mut f = prefix_fixture()?;
    attach_prefix(&mut f)?;
    for field in [
        "task_id",
        "request_id",
        "background_ticket_id",
        "runtime_sha256",
        "result_sha256",
        "owning_journal_sha256",
    ] {
        let mut value = serde_json::to_value(&f.request)?;
        value[field] = json!("wrong");
        assert!(preview(&f.root, &f.task, &serde_json::from_value(value)?).is_err());
    }
    let mut both = f.request.clone();
    both.historical_boundary = both.historical_prefix.clone();
    assert!(preview(&f.root, &f.task, &both)
        .unwrap_err()
        .to_string()
        .contains("only one"));
    let agreement = f._dir.path().join("prefix-agreement.json");
    let mut a: Value = serde_json::from_slice(&std::fs::read(&agreement)?)?;
    a["permitted_effect"] = json!("accept_science");
    let pin = put_json(&agreement, &a)?;
    let prefix = f.request.historical_prefix.clone().unwrap();
    let mut v: Value = serde_json::from_slice(&std::fs::read(&prefix.path)?)?;
    v["agreement"] = serde_json::to_value(pin)?;
    f.request.historical_prefix = Some(put_json(&prefix.path, &v)?);
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    Ok(())
}

#[test]
fn stored_prefix_drift_reopens_review_without_touching_results_or_queue() -> Result<()> {
    let mut f = prefix_fixture()?;
    attach_prefix(&mut f)?;
    let p = preview(&f.root, &f.task, &f.request)?;
    let store = OffdeskTaskStore::new(&f.root);
    store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?;
    let task = store.load()?.remove(0);
    let before = std::fs::read(store.path())?;
    let version = attention_version(&task);
    std::fs::write(f._dir.path().join("prefix-snapshot.json"), "{}")?;
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
