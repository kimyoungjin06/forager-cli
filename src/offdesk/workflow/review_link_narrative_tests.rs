use super::*;
use serde_json::{json, Value};

fn prepare(f: &Fixture) -> Result<()> {
    let approved = std::fs::read(&f.request.owning_plan)?;
    std::fs::write(f._dir.path().join("approved.md"), &approved)?;
    let mut current = approved;
    current.extend_from_slice(b"\nUnrelated project narrative updated externally.\n");
    std::fs::write(&f.request.owning_plan, current)?;
    Ok(())
}

fn attach(f: &mut Fixture) -> Result<()> {
    let approved = f._dir.path().join("approved.md");
    let approved_hash = digest(&std::fs::read(&approved)?);
    let current_hash = digest(&std::fs::read(&f.request.owning_plan)?);
    let request_hash = narrative_reconciliation::request_sha256(&f.request)?;
    let identity = crate::process::filesystem_file_identity(&std::fs::metadata(&f.root)?).unwrap();
    let agreement = put_json(
        &f._dir.path().join("narrative-agreement.json"),
        &json!({"schema":"offdesk_review_narrative_reconciliation_agreement.v1",
        "review_request_sha256":request_hash,"profile_identity":identity,
        "approved_document_sha256":approved_hash,"current_document_sha256":current_hash,
        "decided_by":"fixture-user","decision_ref":"fixture:explicit-narrative-change",
        "permitted_effect":"link_existing_nonaccepted_decision_across_exact_narrative_change"}),
    )?;
    f.request.narrative_reconciliation = Some(put_json(
        &f._dir.path().join("narrative.json"),
        &json!({"schema":"offdesk_review_narrative_reconciliation.v1",
        "review_request_sha256":request_hash,"profile_identity":identity,
        "approved_document":{"path":approved,"sha256":approved_hash},
        "current_document_sha256":current_hash,"agreement":agreement}),
    )?);
    Ok(())
}

#[test]
fn narrative_change_still_refuses_without_explicit_reconciliation() -> Result<()> {
    let f = fixture()?;
    prepare(&f)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("review artifact changed"));
    Ok(())
}

#[test]
fn explicit_equal_task_reconciliation_preserves_history_and_replays_in_v5() -> Result<()> {
    let mut f = fixture_with_state(ondesk_plan::ReviewState::PartiallyAccepted)?;
    prepare(&f)?;
    attach(&mut f)?;
    let owner = std::fs::read(&f.request.owning_plan)?;
    let journal = journal_path(&f.request.owning_plan)?;
    let original = std::fs::read(&journal)?;
    let p = preview(&f.root, &f.task, &f.request)?;
    let observation = p.narrative_reconciliation.as_ref().unwrap();
    assert_eq!(observation.task_count, 1);
    assert_ne!(
        observation.current_document_sha256,
        f.request.owning_plan_sha256
    );
    let store = OffdeskTaskStore::new(&f.root);
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "applied"
    );
    let bytes = std::fs::read(store.path())?;
    let q: Value = serde_json::from_slice(&bytes)?;
    assert_eq!(q["schema"], "offdesk_task_queue.v5");
    let task = store.load()?.remove(0);
    assert_eq!(runtime_sha256(&task)?, runtime_sha256(&f.task)?);
    validate_record(&f.root, &task, &task.review_links[0])?;
    assert_eq!(
        task.operator_view_with_profile(&f.root)
            .review
            .unwrap()
            .state,
        "recorded_partial"
    );
    assert_eq!(
        store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?,
        "already_applied"
    );
    assert_eq!(bytes, std::fs::read(store.path())?);
    assert_eq!(owner, std::fs::read(&f.request.owning_plan)?);
    assert_eq!(original, std::fs::read(journal)?);
    for schema in [
        "offdesk_task_queue.v2",
        "offdesk_task_queue.v3",
        "offdesk_task_queue.v4",
    ] {
        let mut wrong = q.clone();
        wrong["schema"] = json!(schema);
        assert!(crate::migrations::v005_task_review_links::decode(&wrong.to_string()).is_err());
    }
    assert_eq!(
        crate::migrations::v005_task_review_links::encode(&[], Some(&q.to_string()))?["schema"],
        "offdesk_task_queue.v5"
    );
    Ok(())
}

#[test]
fn every_structured_task_field_and_added_task_refuse_even_with_repinned_current_bytes() -> Result<()>
{
    for field in [
        "goal",
        "completion_criteria",
        "depends_on",
        "review_state",
        "evidence_refs",
        "task_id",
        "added_task",
    ] {
        let mut f = fixture()?;
        prepare(&f)?;
        let source = std::fs::read_to_string(&f.request.owning_plan)?;
        let original = ondesk_plan::inspect(&source, "project")?.plan;
        let mut value = serde_json::to_value(original)?;
        match field {
            "goal" => value["tasks"][0][field] = json!("different goal"),
            "completion_criteria" => value["tasks"][0][field] = json!(["different criterion"]),
            "depends_on" => value["tasks"][0][field] = json!(["other"]),
            "review_state" => value["tasks"][0][field] = json!("deferred"),
            "evidence_refs" => value["tasks"][0][field] = json!(["different reference"]),
            "task_id" => value["tasks"][0][field] = json!("other"),
            _ => {
                let mut other = value["tasks"][0].clone();
                other["task_id"] = json!("other");
                value["tasks"].as_array_mut().unwrap().push(other);
            }
        }
        let candidate = format!("# Current narrative\n```forager-plan\n{}\n```\n", value);
        std::fs::write(&f.request.owning_plan, candidate)?;
        attach(&mut f)?;
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{field}");
    }
    Ok(())
}

#[test]
fn reconciliation_cannot_accept_a_new_disposition_or_uncommitted_snapshot() -> Result<()> {
    let mut accepted = fixture_with_state(ondesk_plan::ReviewState::Accepted)?;
    prepare(&accepted)?;
    attach(&mut accepted)?;
    assert!(preview(&accepted.root, &accepted.task, &accepted.request)
        .unwrap_err()
        .to_string()
        .contains("nonaccepted"));
    let mut f = fixture()?;
    prepare(&f)?;
    let approved = f._dir.path().join("approved.md");
    let changed = std::fs::read_to_string(&approved)? + "\nNot the committed document.\n";
    std::fs::write(&approved, changed)?;
    attach(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    f.request.owning_plan_sha256 = digest(&std::fs::read(approved)?);
    attach(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("committed journal version"));
    Ok(())
}

#[test]
fn separate_agreement_and_full_request_identity_are_required() -> Result<()> {
    let mut f = fixture()?;
    prepare(&f)?;
    attach(&mut f)?;
    let original = f.request.clone();
    for field in [
        "link_request_id",
        "owning_task_id",
        "decision_request_id",
        "result_sha256",
        "owning_journal_sha256",
    ] {
        let mut value = serde_json::to_value(&original)?;
        value[field] = json!("changed");
        f.request = serde_json::from_value(value)?;
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{field}");
    }
    f.request = original;
    for field in [
        "decided_by",
        "decision_ref",
        "permitted_effect",
        "current_document_sha256",
        "approved_document_sha256",
        "profile_identity",
    ] {
        attach(&mut f)?;
        let path = f._dir.path().join("narrative-agreement.json");
        let mut agreement: Value = serde_json::from_slice(&std::fs::read(&path)?)?;
        agreement[field] = if field == "profile_identity" {
            json!([0, 0])
        } else {
            json!("")
        };
        let pin = put_json(&path, &agreement)?;
        let artifact = f._dir.path().join("narrative.json");
        let mut value: Value = serde_json::from_slice(&std::fs::read(&artifact)?)?;
        value["agreement"] = serde_json::to_value(pin)?;
        f.request.narrative_reconciliation = Some(put_json(&artifact, &value)?);
        assert!(preview(&f.root, &f.task, &f.request).is_err(), "{field}");
    }
    Ok(())
}

#[test]
fn later_narrative_or_acknowledgment_changes_reopen_without_rewriting_the_link() -> Result<()> {
    let mut f = fixture()?;
    prepare(&f)?;
    attach(&mut f)?;
    let store = OffdeskTaskStore::new(&f.root);
    let p = preview(&f.root, &f.task, &f.request)?;
    store.link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())?;
    let bytes = std::fs::read(store.path())?;
    let task = store.load()?.remove(0);
    let version = attention_version(&task);
    std::fs::write(
        &f.request.owning_plan,
        std::fs::read_to_string(&f.request.owning_plan)? + "\nLater narrative.\n",
    )?;
    assert!(validate_record(&f.root, &task, &task.review_links[0]).is_err());
    assert_ne!(version, attention_version(&task));
    assert_eq!(bytes, std::fs::read(store.path())?);
    assert!(store
        .link_review(&f.request, &p.sha256()?, "fixture-user", Utc::now())
        .is_err());
    std::fs::write(
        &f.request.owning_plan,
        std::fs::read_to_string(f._dir.path().join("approved.md"))?
            + "\nUnrelated project narrative updated externally.\n",
    )?;
    assert!(validate_record(&f.root, &task, &task.review_links[0]).is_ok());
    std::fs::write(f._dir.path().join("narrative-agreement.json"), "{}")?;
    assert!(validate_record(&f.root, &task, &task.review_links[0]).is_err());
    assert_ne!(version, attention_version(&task));
    assert_eq!(bytes, std::fs::read(store.path())?);
    Ok(())
}

#[test]
fn reconciliation_never_waives_journal_or_new_closeout_guards() -> Result<()> {
    for committed in [false, true] {
        let mut f = fixture()?;
        prepare(&f)?;
        attach(&mut f)?;
        let journal = journal_path(&f.request.owning_plan)?;
        let mut value: Value = serde_json::from_slice(&std::fs::read(&journal)?)?;
        value["entries"][0]["committed"] = json!(committed);
        value["entries"][0]["request"]["request_id"] = json!("different-selected-decision");
        std::fs::write(&journal, serde_json::to_vec(&value)?)?;
        f.request.owning_journal_sha256 = digest(&std::fs::read(journal)?);
        attach(&mut f)?;
        assert!(preview(&f.root, &f.task, &f.request).is_err());
    }
    let mut f = fixture()?;
    prepare(&f)?;
    attach(&mut f)?;
    let dir = f.root.join("offdesk_closeouts/new");
    std::fs::create_dir_all(&dir)?;
    std::fs::write(
        dir.join("closeout_plan.json"),
        serde_json::to_vec(&json!({"tasks":[{"task_id":"runtime-1","request_id":"request-1"}]}))?,
    )?;
    assert!(preview(&f.root, &f.task, &f.request)
        .unwrap_err()
        .to_string()
        .contains("explicit hash binding"));
    Ok(())
}

#[test]
fn unsafe_snapshot_paths_and_ambiguous_or_oversized_current_documents_refuse() -> Result<()> {
    let mut f = fixture()?;
    prepare(&f)?;
    attach(&mut f)?;
    let original = std::fs::read(&f.request.owning_plan)?;
    std::fs::write(
        &f.request.owning_plan,
        [original.as_slice(), b"\n```forager-plan\n{}\n```\n"].concat(),
    )?;
    attach(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    std::fs::write(
        &f.request.owning_plan,
        vec![b'x'; ondesk_plan::MAX_PLAN_BYTES + 1],
    )?;
    attach(&mut f)?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    std::fs::write(&f.request.owning_plan, original)?;
    attach(&mut f)?;
    let snapshot = f._dir.path().join("approved.md");
    let alias = f._dir.path().join("hardlink.md");
    std::fs::hard_link(&snapshot, &alias)?;
    assert!(preview(&f.root, &f.task, &f.request).is_err());
    std::fs::remove_file(alias)?;
    #[cfg(unix)]
    {
        let saved = f._dir.path().join("saved.md");
        std::fs::rename(&snapshot, &saved)?;
        std::os::unix::fs::symlink(saved, &snapshot)?;
        assert!(preview(&f.root, &f.task, &f.request).is_err());
    }
    Ok(())
}
