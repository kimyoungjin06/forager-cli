use anyhow::{Context, Result};
use forager::document_lifecycle::{
    document_hygiene_contract_sha256, document_hygiene_contract_v1,
    governance_lifecycle_crosswalk_v1, ActivityState, BodyCoverage, ClassificationAuthority,
    CrosswalkStatus, DocumentCatalogV1, DocumentHygieneReportV1, DocumentIdentityCrosswalkV1,
    GitState, IncompleteScope, MediaFamily, RetentionDisposition, RetentionResolutionStatus,
    RetentionTargetSourceKind, ScanIncompleteReason, ScanLimit, ACTIVITY_STATES,
    CLASSIFICATION_AUTHORITIES, DOCUMENT_HYGIENE_CONTRACT_SCHEMA, DOCUMENT_HYGIENE_CONTRACT_SHA256,
    DOCUMENT_INTENT_KINDS, RETENTION_DISPOSITIONS,
};
use serde::de::DeserializeOwned;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("document_lifecycle")
}

fn load_json<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let content = fs::read_to_string(path)
        .with_context(|| format!("failed to read fixture {}", path.display()))?;
    serde_json::from_str(&content)
        .with_context(|| format!("failed to parse fixture {}", path.display()))
}

fn load_catalog(name: &str) -> Result<DocumentCatalogV1> {
    load_json(&fixture_root().join(name).join("document_catalog.v1.json"))
}

#[test]
fn canonical_agent_hygiene_payload_is_exact_and_non_mutating() {
    let contract = document_hygiene_contract_v1();
    assert_eq!(contract.schema, DOCUMENT_HYGIENE_CONTRACT_SCHEMA);
    assert_eq!(
        document_hygiene_contract_sha256(),
        DOCUMENT_HYGIENE_CONTRACT_SHA256
    );
    assert_eq!(contract.sha256, DOCUMENT_HYGIENE_CONTRACT_SHA256);
    assert_eq!(contract.intents, DOCUMENT_INTENT_KINDS);
    assert!(contract.prompt.contains("Search the active document view"));
    assert!(contract
        .prompt
        .contains("Updating an existing current owner is the default"));
    assert!(!contract.search_visibility_mutation_authorized);
    assert!(!contract.file_mutation_authorized);
    assert!(!contract.strict_closeout_authorized);
}

#[test]
fn vocabulary_is_complete_and_keeps_unknown_authority_out_of_the_enum() -> Result<()> {
    let activity = serde_json::to_value(ACTIVITY_STATES)?;
    let retention = serde_json::to_value(RETENTION_DISPOSITIONS)?;
    let authorities = serde_json::to_value(CLASSIFICATION_AUTHORITIES)?;

    assert_eq!(
        activity,
        serde_json::json!([
            "draft",
            "candidate",
            "active",
            "review_due",
            "superseded",
            "retired",
            "unknown"
        ])
    );
    assert_eq!(
        retention,
        serde_json::json!([
            "unclassified",
            "keep",
            "preserved",
            "summary_kept",
            "retirement_candidate",
            "archived",
            "quarantine_candidate",
            "quarantined",
            "disposal_candidate",
            "disposed"
        ])
    );
    assert_eq!(
        authorities,
        serde_json::json!(["observed", "declared", "inferred", "reviewed"])
    );
    assert!(serde_json::from_str::<ClassificationAuthority>("\"unknown\"").is_err());

    let crosswalk = governance_lifecycle_crosswalk_v1();
    assert!(crosswalk
        .iter()
        .all(|entry| { entry.activity_state.is_some() ^ entry.retention_disposition.is_some() }));
    assert_eq!(
        crosswalk
            .iter()
            .find(|entry| entry.legacy_value == "active")
            .and_then(|entry| entry.activity_state),
        Some(ActivityState::Active)
    );
    assert_eq!(
        crosswalk
            .iter()
            .find(|entry| entry.legacy_value == "preserved")
            .and_then(|entry| entry.retention_disposition),
        Some(RetentionDisposition::Preserved)
    );
    Ok(())
}

#[test]
fn bounded_catalog_fixtures_cover_all_phase_zero_project_shapes() -> Result<()> {
    let names = [
        "software",
        "research_longrun",
        "non_git",
        "mixed_git_states",
    ];
    for name in names {
        let catalog = load_catalog(name)?;
        catalog
            .validate()
            .with_context(|| format!("invalid {name} catalog fixture"))?;
        assert!(catalog.authority_boundary.projection_only);
        assert!(!catalog.authority_boundary.file_mutation_authorized);
        assert!(
            !catalog
                .authority_boundary
                .search_visibility_mutation_authorized
        );
    }

    let non_git = load_catalog("non_git")?;
    assert_eq!(non_git.entries[0].git_state, GitState::OutsideGit);
    assert_eq!(non_git.entries[0].activity_state, ActivityState::Unknown);
    assert_eq!(
        non_git.entries[0].retention_disposition,
        RetentionDisposition::Unclassified
    );

    let mixed = load_catalog("mixed_git_states")?;
    let git_states = mixed
        .entries
        .iter()
        .map(|entry| entry.git_state)
        .collect::<BTreeSet<_>>();
    assert_eq!(
        git_states,
        BTreeSet::from([GitState::Ignored, GitState::Untracked])
    );
    assert!(mixed.entries.iter().all(|entry| {
        entry.retention_disposition == RetentionDisposition::Unclassified
            && entry.retention_authority.is_none()
    }));
    Ok(())
}

#[test]
fn research_fixture_keeps_active_preserved_authority_visible_and_pdf_metadata_only() -> Result<()> {
    let catalog = load_catalog("research_longrun")?;
    catalog.validate()?;

    let active_preserved = catalog
        .entries
        .iter()
        .filter(|entry| {
            entry.activity_state == ActivityState::Active
                && entry.retention_disposition == RetentionDisposition::Preserved
        })
        .count();
    assert_eq!(active_preserved, 2);

    let pdf_coverage = catalog
        .coverage
        .iter()
        .find(|coverage| coverage.media_family == MediaFamily::Pdf)
        .expect("PDF coverage row");
    assert_eq!(pdf_coverage.body_coverage, BodyCoverage::MetadataOnly);
    assert_eq!(pdf_coverage.body_searchable_files, 0);

    let report = catalog
        .entries
        .iter()
        .find(|entry| entry.relative_path.ends_with("final-report.pdf"))
        .expect("report entry");
    assert!(report.document_id.is_some());
    assert!(report.artifact_id.is_some());
    Ok(())
}

#[test]
fn identity_crosswalk_uses_one_approval_ledger_and_fails_closed() -> Result<()> {
    let crosswalk: DocumentIdentityCrosswalkV1 =
        load_json(&fixture_root().join("document_identity_crosswalk.v1.json"))?;
    crosswalk.validate()?;

    assert_eq!(
        crosswalk.retention_target_contract.approval_ledger,
        "existing_offdesk_approval_ledger"
    );
    assert!(
        !crosswalk
            .retention_target_contract
            .fabricated_artifact_identity_allowed
    );
    assert!(
        !crosswalk
            .retention_target_contract
            .duplicate_approval_allowed
    );
    assert_eq!(
        crosswalk
            .retention_target_contract
            .existing_application_receipt_authority,
        "plan_provenance_only"
    );
    assert!(
        !crosswalk
            .retention_target_contract
            .existing_application_receipt_mutation_authorized
    );

    let catalog_only = crosswalk
        .cases
        .iter()
        .find(|case| case.case_id == "catalog_only_document")
        .expect("catalog-only case");
    assert_eq!(
        catalog_only.expected.status,
        RetentionResolutionStatus::Mint
    );
    assert_eq!(
        catalog_only.expected.source_kind,
        Some(RetentionTargetSourceKind::Document)
    );

    let artifact_backed = crosswalk
        .cases
        .iter()
        .find(|case| case.case_id == "artifact_backed_document")
        .expect("artifact-backed case");
    assert_eq!(
        artifact_backed.expected.status,
        RetentionResolutionStatus::Reuse
    );
    assert_eq!(
        artifact_backed.expected.source_kind,
        Some(RetentionTargetSourceKind::Artifact)
    );

    let blocked = crosswalk
        .cases
        .iter()
        .filter(|case| case.expected.status == RetentionResolutionStatus::Blocked)
        .collect::<Vec<_>>();
    assert_eq!(blocked.len(), 2);
    assert!(blocked
        .iter()
        .all(|case| case.expected.retention_target_id.is_none()
            && case.expected.source_kind.is_none()
            && !case.expected.finding_codes.is_empty()));
    Ok(())
}

#[test]
fn incomplete_hygiene_fixture_cannot_claim_a_clean_assessment() -> Result<()> {
    let report: DocumentHygieneReportV1 =
        load_json(&fixture_root().join("document_hygiene_report.v1.json"))?;
    report.validate()?;
    assert!(!report.post_work.complete);
    assert!(!report.assessment_complete);
    assert_eq!(report.summary.warnings, 2);
    assert!(report.authority_boundary.advisory_only);
    assert!(!report.authority_boundary.strict_closeout_authorized);
    Ok(())
}

#[test]
fn invalid_authority_and_identity_inputs_fail_closed() -> Result<()> {
    let path = fixture_root()
        .join("software")
        .join("document_catalog.v1.json");
    let mut raw: Value = load_json(&path)?;
    raw["entries"][0]["activity_authority"] = Value::String("unknown".to_string());
    assert!(serde_json::from_value::<DocumentCatalogV1>(raw).is_err());

    let mut catalog = load_catalog("software")?;
    catalog.entries[0].document_id = Some("document_without_ledger".to_string());
    assert!(catalog.validate().is_err());
    Ok(())
}

#[test]
fn known_classifications_require_explicit_authority() -> Result<()> {
    let base = load_catalog("software")?;

    let mut role_without_authority = base.clone();
    role_without_authority.entries[0].role_authority = None;
    assert!(role_without_authority.validate().is_err());

    let mut activity_without_authority = base.clone();
    activity_without_authority.entries[0].activity_state = ActivityState::Active;
    assert!(activity_without_authority.validate().is_err());

    let mut retention_without_authority = base.clone();
    retention_without_authority.entries[0].retention_disposition = RetentionDisposition::Preserved;
    assert!(retention_without_authority.validate().is_err());

    let mut generated_without_authority = base.clone();
    generated_without_authority.entries[0].generated = Some(false);
    generated_without_authority.entries[0].generated_authority = None;
    assert!(generated_without_authority.validate().is_err());

    let mut generated_without_rebuild = base.clone();
    generated_without_rebuild.entries[0].generated = Some(true);
    generated_without_rebuild.entries[0].generated_authority =
        Some(ClassificationAuthority::Declared);
    generated_without_rebuild.entries[0].rebuild_ref = None;
    assert!(generated_without_rebuild.validate().is_err());

    let mut subject_without_authority = base;
    subject_without_authority.entries[0].subject_authority = None;
    assert!(subject_without_authority.validate().is_err());
    Ok(())
}

#[test]
fn owning_source_identities_require_exact_schema_and_nonempty_identity() -> Result<()> {
    let base = load_catalog("research_longrun")?;

    let mut wrong_ledger_schema = base.clone();
    wrong_ledger_schema
        .source_identities
        .lifecycle_ledger
        .as_mut()
        .expect("lifecycle source")
        .schema = "document_lifecycle_ledger.v2".to_string();
    assert!(wrong_ledger_schema.validate().is_err());

    let mut empty_artifact_identity = base.clone();
    empty_artifact_identity
        .source_identities
        .artifact_index
        .as_mut()
        .expect("artifact source")
        .identity
        .clear();
    assert!(empty_artifact_identity.validate().is_err());

    let mut wrong_filesystem_schema = base;
    wrong_filesystem_schema
        .source_identities
        .filesystem_snapshot
        .schema = "filesystem_observation.v2".to_string();
    assert!(wrong_filesystem_schema.validate().is_err());

    let mut reviewed_without_ledger = load_catalog("software")?;
    reviewed_without_ledger.entries[0].activity_state = ActivityState::Active;
    reviewed_without_ledger.entries[0].activity_authority = Some(ClassificationAuthority::Reviewed);
    assert!(reviewed_without_ledger.validate().is_err());

    let mut reviewed_retention_without_ledger = load_catalog("software")?;
    reviewed_retention_without_ledger.entries[0].retention_disposition =
        RetentionDisposition::Preserved;
    reviewed_retention_without_ledger.entries[0].retention_authority =
        Some(ClassificationAuthority::Reviewed);
    assert!(reviewed_retention_without_ledger.validate().is_err());
    Ok(())
}

#[test]
fn crosswalk_status_and_scan_completeness_reject_contradictions() -> Result<()> {
    let mut unified_without_artifact = load_catalog("software")?;
    unified_without_artifact.crosswalk_findings[0].status = CrosswalkStatus::Unified;
    assert!(unified_without_artifact.validate().is_err());

    let mut catalog_only_with_artifact = load_catalog("research_longrun")?;
    let report_finding = catalog_only_with_artifact
        .crosswalk_findings
        .iter_mut()
        .find(|finding| finding.artifact_id.is_some())
        .expect("artifact crosswalk");
    report_finding.status = CrosswalkStatus::CatalogOnly;
    assert!(catalog_only_with_artifact.validate().is_err());

    let mut false_complete = load_catalog("software")?;
    false_complete.scan.usage.files_observed = false_complete.scan.budgets.max_files + 1;
    assert!(false_complete.validate().is_err());

    let mut false_directory_complete = load_catalog("software")?;
    false_directory_complete
        .scan
        .usage
        .directory_entries_observed =
        false_directory_complete.scan.budgets.max_directory_entries + 1;
    assert!(false_directory_complete.validate().is_err());

    let mut unexplained_incomplete = load_catalog("software")?;
    unexplained_incomplete.scan.complete = false;
    assert!(unexplained_incomplete.validate().is_err());

    let mut explained_incomplete = load_catalog("software")?;
    explained_incomplete.scan.complete = false;
    explained_incomplete.scan.usage.elapsed_ms =
        explained_incomplete.scan.budgets.max_elapsed_ms + 1;
    explained_incomplete
        .scan
        .reached_limits
        .push(ScanLimit::ElapsedTime);
    explained_incomplete
        .scan
        .incomplete_scope
        .push(IncompleteScope {
            relative_path: "remaining-documents".to_string(),
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: "The elapsed-time budget stopped traversal before this bounded scope."
                .to_string(),
        });
    explained_incomplete.validate()?;

    let mut limit_without_scope = load_catalog("software")?;
    limit_without_scope.scan.complete = false;
    limit_without_scope
        .scan
        .reached_limits
        .push(ScanLimit::ElapsedTime);
    assert!(limit_without_scope.validate().is_err());
    Ok(())
}
