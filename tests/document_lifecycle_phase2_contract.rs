use anyhow::{Context, Result};
use forager::document_lifecycle::{
    canonical_approval_source_bytes, document_lifecycle_approval_source_sha256,
    document_lifecycle_event_sha256, document_lifecycle_payload_sha256,
    load_document_lifecycle_ledger_from_root, validate_document_lifecycle_event_chain,
    ActivityState, ApprovalTargetIdentityV1, CatalogV2AuthorityBoundary, DocumentCatalogEntryV2,
    DocumentCatalogV1, DocumentCatalogV2, DocumentExactSelectorV2, DocumentLifecycleEventKindV1,
    DocumentLifecycleEventV1, DocumentLifecycleLedgerLoadV1, DocumentLifecycleStateV1,
    DocumentMatchKindV2, DocumentSearchMatchV2, DocumentSearchModeV2, DocumentSearchV2,
    LifecycleLedgerExpectationV1, LifecycleLedgerLoadBudgets, LifecycleLedgerLoadOptions,
    LifecycleLedgerSnapshotIdentityV1, LifecycleProjectionEnvelopeV2, LifecycleProjectionStatusV1,
    NegativeSearchEvidenceV2, RetentionDisposition, ScanIncompleteReason,
    SearchV2AuthorityBoundary, SearchVisibilityV2, StrictUtcTimestamp, DOCUMENT_CATALOG_SCHEMA_V2,
    DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA, DOCUMENT_LIFECYCLE_LEDGER_SNAPSHOT_SCHEMA,
    DOCUMENT_SEARCH_SCHEMA_V2,
};
use serde::de::DeserializeOwned;
use serde::Deserialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

const PROJECT_KEY: &str = "phase2a-project";
const ROOT_ID: &str = "root_phase2a";
const ROOT_PATH_HASH: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const ROOT_INSTANCE_HASH: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const SECOND_EVENT_ID: &str = "document_event_22222222-2222-4222-8222-222222222222";
const THIRD_EVENT_ID: &str = "document_event_33333333-3333-4333-8333-333333333333";
const FOURTH_EVENT_ID: &str = "document_event_44444444-4444-4444-8444-444444444444";
const FIRST_DOCUMENT_ID: &str = "document_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const SECOND_DOCUMENT_ID: &str = "document_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

fn fixture_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("document_lifecycle")
}

fn load_json<T: DeserializeOwned>(path: &Path) -> Result<T> {
    let bytes = fs::read(path).with_context(|| format!("read fixture {}", path.display()))?;
    serde_json::from_slice(&bytes).with_context(|| format!("parse fixture {}", path.display()))
}

fn phase2a_event_paths() -> Vec<PathBuf> {
    let root = fixture_root().join("phase2a");
    vec![
        root.join("00000000000000000001_document_event_11111111-1111-4111-8111-111111111111.json"),
        root.join("00000000000000000002_document_event_22222222-2222-4222-8222-222222222222.json"),
    ]
}

fn load_phase2a_events() -> Result<Vec<DocumentLifecycleEventV1>> {
    phase2a_event_paths()
        .iter()
        .map(|path| load_json(path))
        .collect()
}

fn expectation() -> LifecycleLedgerExpectationV1 {
    LifecycleLedgerExpectationV1 {
        project_key: PROJECT_KEY.to_string(),
        root_id: ROOT_ID.to_string(),
        root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
        root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
    }
}

fn timestamp(value: &str) -> StrictUtcTimestamp {
    StrictUtcTimestamp::parse(value).unwrap()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GoldenHashes {
    schema: String,
    approval_source_sha256: Vec<String>,
    payload_sha256: Vec<String>,
    event_sha256: Vec<String>,
    ordered_event_hashes_sha256: String,
}

#[test]
fn phase2a_golden_events_freeze_canonical_bytes_and_hashes() -> Result<()> {
    let events = load_phase2a_events()?;
    let golden: GoldenHashes =
        load_json(&fixture_root().join("phase2a").join("golden_hashes.v1.json"))?;
    assert_eq!(golden.schema, "document_lifecycle_golden_hashes.v1");

    for (index, event) in events.iter().enumerate() {
        event.validate()?;
        assert_eq!(
            document_lifecycle_approval_source_sha256(&event.payload.approval_source)?,
            golden.approval_source_sha256[index]
        );
        assert_eq!(
            document_lifecycle_payload_sha256(&event.payload)?,
            golden.payload_sha256[index]
        );
        assert_eq!(
            document_lifecycle_event_sha256(&event.payload, &event.payload_sha256)?,
            golden.event_sha256[index]
        );
        let first = canonical_approval_source_bytes(&event.payload.approval_source)?;
        let second = canonical_approval_source_bytes(&event.payload.approval_source)?;
        assert_eq!(first, second);
    }
    let summary = validate_document_lifecycle_event_chain(&events, &expectation())?;
    assert_eq!(
        summary.ordered_event_hashes_sha256,
        golden.ordered_event_hashes_sha256
    );
    Ok(())
}

#[test]
fn phase2a_loader_reads_only_the_supplied_root_and_is_non_mutating() -> Result<()> {
    let temp = TempDir::new()?;
    let event_dir = temp
        .path()
        .join("document-lifecycle")
        .join("v1")
        .join("events");
    fs::create_dir_all(&event_dir)?;
    for source in phase2a_event_paths() {
        fs::copy(&source, event_dir.join(source.file_name().unwrap()))?;
    }
    let before = event_dir
        .read_dir()?
        .map(|entry| {
            let path = entry?.path();
            Ok((path.file_name().unwrap().to_owned(), fs::read(path)?))
        })
        .collect::<std::io::Result<Vec<_>>>()?;

    let report = load_document_lifecycle_ledger_from_root(
        temp.path(),
        &expectation(),
        &LifecycleLedgerLoadOptions {
            observed_at: timestamp("2026-08-20T00:40:00Z"),
            ..LifecycleLedgerLoadOptions::default()
        },
    )?;
    assert_eq!(report.status, LifecycleProjectionStatusV1::Valid);
    assert_eq!(report.events.len(), 2);
    assert!(!report.lifecycle_projection_authorized);
    assert!(!report.search_filtering_authorized);
    assert!(!report.profile_state_write_authorized);
    assert!(!report.project_state_write_authorized);
    assert!(!report.file_mutation_authorized);

    let after = event_dir
        .read_dir()?
        .map(|entry| {
            let path = entry?.path();
            Ok((path.file_name().unwrap().to_owned(), fs::read(path)?))
        })
        .collect::<std::io::Result<Vec<_>>>()?;
    assert_eq!(before, after);
    Ok(())
}

#[test]
fn altered_approval_and_event_fields_fail_closed() -> Result<()> {
    let event = load_phase2a_events()?.remove(0);
    let value = serde_json::to_value(&event)?;

    for pointer in [
        "/payload/approval_source/metadata_sha256",
        "/payload/approval_source/proposal_sha256",
        "/payload/approval_source/resolved_by",
        "/payload/approval_source/target_identity/observation_id",
    ] {
        let mut altered = value.clone();
        *altered.pointer_mut(pointer).context("fixture pointer")? = match pointer {
            "/payload/approval_source/resolved_by" => serde_json::json!("telegram:other"),
            "/payload/approval_source/target_identity/observation_id" => {
                serde_json::json!("doc_observation_999999999999999999999999")
            }
            _ => serde_json::json!(
                "9999999999999999999999999999999999999999999999999999999999999999"
            ),
        };
        let altered: DocumentLifecycleEventV1 = serde_json::from_value(altered)?;
        assert!(altered.validate().is_err());
    }

    let mut denied = value.clone();
    *denied
        .pointer_mut("/payload/approval_source/status")
        .context("status pointer")? = serde_json::json!("denied");
    assert!(serde_json::from_value::<DocumentLifecycleEventV1>(denied).is_err());

    let receipt = serde_json::json!({
        "schema": "artifact_retention_application.v1",
        "approval_id": "approval_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    });
    assert!(serde_json::from_value::<DocumentLifecycleEventV1>(receipt).is_err());

    let mut alternate_id = event.payload.clone();
    alternate_id.observation.observation_id =
        "doc_observation_999999999999999999999999".to_string();
    alternate_id.approval_source.target_identity = ApprovalTargetIdentityV1::Mint {
        observation_id: alternate_id.observation.observation_id.clone(),
    };
    alternate_id.approval_source_sha256 =
        document_lifecycle_approval_source_sha256(&alternate_id.approval_source)?;
    assert!(DocumentLifecycleEventV1::from_payload(alternate_id).is_err());
    Ok(())
}

fn phase1_base_catalog(two_entries: bool) -> Result<DocumentCatalogV1> {
    let mut catalog: DocumentCatalogV1 = load_json(
        &fixture_root()
            .join("non_git")
            .join("document_catalog.v1.json"),
    )?;
    if two_entries {
        catalog.project_key = PROJECT_KEY.to_string();
        catalog.roots[0].project_key = PROJECT_KEY.to_string();
        catalog.roots[0].root_id = ROOT_ID.to_string();
        catalog.roots[0].path_identity_sha256 = ROOT_PATH_HASH.to_string();
        catalog.generated_at = timestamp("2026-08-20T00:40:00Z").as_datetime().to_owned();
        catalog.entries[0].project_key = PROJECT_KEY.to_string();
        catalog.entries[0].root_id = ROOT_ID.to_string();
        catalog.entries[0].observation_id = "doc_observation_c6e3d97cf2e444c8d9a791db".to_string();
        catalog.entries[0].relative_path = "docs/report.md".to_string();
        catalog.entries[0].size_bytes = 124;
        catalog.entries[0].modified_at =
            Some(timestamp("2026-08-20T00:20:00Z").as_datetime().to_owned());
        catalog.entries[0].content_sha256 =
            Some("dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd".to_string());
        catalog.crosswalk_findings[0].observation_id = catalog.entries[0].observation_id.clone();
        let mut second = catalog.entries[0].clone();
        second.observation_id = "doc_observation_f0eb35b29a47910cba670948".to_string();
        second.relative_path = "docs/current.md".to_string();
        second.size_bytes = 200;
        second.modified_at = Some(timestamp("2026-08-20T00:31:00Z").as_datetime().to_owned());
        second.content_sha256 =
            Some("abababababababababababababababababababababababababababababababab".to_string());
        catalog.entries.push(second);
        catalog.scan.usage.files_observed = 2;
        catalog.scan.usage.directory_entries_observed = 2;
        catalog.scan.usage.bytes_hashed = 324;
        catalog.scan.usage.bytes_extracted = 324;
        catalog.scan.usage.largest_file_bytes_read = 200;
        catalog.coverage[0].observed_files = 2;
        catalog.coverage[0].metadata_complete_files = 2;
        catalog.coverage[0].body_searchable_files = 2;
        catalog.coverage[0].body_extracted_files = 2;
        let mut crosswalk = catalog.crosswalk_findings[0].clone();
        crosswalk.observation_id = "doc_observation_f0eb35b29a47910cba670948".to_string();
        catalog.crosswalk_findings.push(crosswalk);
    }
    catalog.validate()?;
    Ok(catalog)
}

fn catalog_expectation(base: &DocumentCatalogV1) -> LifecycleLedgerExpectationV1 {
    let root = &base.roots[0];
    LifecycleLedgerExpectationV1 {
        project_key: base.project_key.clone(),
        root_id: root.root_id.clone(),
        root_path_identity_sha256: root.path_identity_sha256.clone(),
        root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
    }
}

fn active_state() -> DocumentLifecycleStateV1 {
    DocumentLifecycleStateV1 {
        activity_state: ActivityState::Active,
        retention_disposition: RetentionDisposition::Unclassified,
        successor_document_id: None,
        active_owner_document_id: None,
        summary_document_id: None,
        review_after: None,
    }
}

fn cold_events() -> Result<Vec<DocumentLifecycleEventV1>> {
    let mut events = load_phase2a_events()?;

    let third_proposal = "3434343434343434343434343434343434343434343434343434343434343434";
    let mut third_approval = events[0].payload.approval_source.clone();
    third_approval.approval_id = "approval_33333333-3333-4333-8333-333333333333".to_string();
    third_approval.action_id = "action_33333333-3333-4333-8333-333333333333".to_string();
    third_approval.created_at = timestamp("2026-08-20T00:30:00Z");
    third_approval.resolved_at = timestamp("2026-08-20T00:32:00Z");
    third_approval.expires_at = timestamp("2026-08-20T01:30:00Z");
    third_approval.proposal_sha256 = third_proposal.to_string();
    third_approval.target_identity = ApprovalTargetIdentityV1::Mint {
        observation_id: "doc_observation_f0eb35b29a47910cba670948".to_string(),
    };
    let third_approval_sha = document_lifecycle_approval_source_sha256(&third_approval)?;
    let mut third_payload = events[0].payload.clone();
    third_payload.sequence = 3;
    third_payload.event_id = THIRD_EVENT_ID.to_string();
    third_payload.recorded_at = timestamp("2026-08-20T00:33:00Z");
    third_payload.previous_event_sha256 = Some(events[1].event_sha256.clone());
    third_payload.document_id = SECOND_DOCUMENT_ID.to_string();
    third_payload.observation.observation_id =
        "doc_observation_f0eb35b29a47910cba670948".to_string();
    third_payload.observation.relative_path = "docs/current.md".to_string();
    third_payload.observation.size_bytes = 200;
    third_payload.observation.modified_at = timestamp("2026-08-20T00:31:00Z");
    third_payload.observation.content_sha256 =
        "abababababababababababababababababababababababababababababababab".to_string();
    third_payload.proposal_sha256 = third_proposal.to_string();
    third_payload.approval_source = third_approval;
    third_payload.approval_source_sha256 = third_approval_sha;
    third_payload.reason = "Establish the exact active successor document.".to_string();
    third_payload.idempotency_key = "lifecycle_apply_three".to_string();
    let third = DocumentLifecycleEventV1::from_payload(third_payload)?;

    let fourth_proposal = "4545454545454545454545454545454545454545454545454545454545454545";
    let mut fourth_approval = events[1].payload.approval_source.clone();
    fourth_approval.approval_id = "approval_44444444-4444-4444-8444-444444444444".to_string();
    fourth_approval.action_id = "action_44444444-4444-4444-8444-444444444444".to_string();
    fourth_approval.created_at = timestamp("2026-08-20T00:34:00Z");
    fourth_approval.resolved_at = timestamp("2026-08-20T00:35:00Z");
    fourth_approval.expires_at = timestamp("2026-08-20T01:34:00Z");
    fourth_approval.proposal_sha256 = fourth_proposal.to_string();
    fourth_approval.target_identity = ApprovalTargetIdentityV1::Document {
        document_id: FIRST_DOCUMENT_ID.to_string(),
        expected_head_event_id: SECOND_EVENT_ID.to_string(),
    };
    let fourth_approval_sha = document_lifecycle_approval_source_sha256(&fourth_approval)?;
    let mut fourth_payload = events[1].payload.clone();
    fourth_payload.sequence = 4;
    fourth_payload.event_id = FOURTH_EVENT_ID.to_string();
    fourth_payload.event_kind = DocumentLifecycleEventKindV1::ActivityTransition;
    fourth_payload.recorded_at = timestamp("2026-08-20T00:36:00Z");
    fourth_payload.previous_event_sha256 = Some(third.event_sha256.clone());
    fourth_payload.expected_document_head_event_id = Some(SECOND_EVENT_ID.to_string());
    fourth_payload.after = DocumentLifecycleStateV1 {
        activity_state: ActivityState::Superseded,
        successor_document_id: Some(SECOND_DOCUMENT_ID.to_string()),
        ..active_state()
    };
    fourth_payload.proposal_sha256 = fourth_proposal.to_string();
    fourth_payload.approval_source = fourth_approval;
    fourth_payload.approval_source_sha256 = fourth_approval_sha;
    fourth_payload.reason = "Supersede the report with its exact active successor.".to_string();
    fourth_payload.idempotency_key = "lifecycle_apply_four".to_string();
    let fourth = DocumentLifecycleEventV1::from_payload(fourth_payload)?;

    events.extend([third, fourth]);
    validate_document_lifecycle_event_chain(&events, &expectation())?;
    Ok(events)
}

fn cold_load(events: Vec<DocumentLifecycleEventV1>) -> Result<DocumentLifecycleLedgerLoadV1> {
    let summary = validate_document_lifecycle_event_chain(&events, &expectation())?;
    let source_identity = LifecycleLedgerSnapshotIdentityV1 {
        schema: DOCUMENT_LIFECYCLE_LEDGER_SNAPSHOT_SCHEMA.to_string(),
        first_sequence: summary.first_sequence,
        last_sequence: summary.last_sequence,
        event_count: summary.event_count,
        final_event_sha256: summary.final_event_sha256,
        ordered_event_hashes_sha256: summary.ordered_event_hashes_sha256,
        observed_at: timestamp("2026-08-20T00:40:00Z"),
    };
    let load = DocumentLifecycleLedgerLoadV1 {
        schema: DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA.to_string(),
        status: LifecycleProjectionStatusV1::Valid,
        expectation: expectation(),
        budgets: LifecycleLedgerLoadBudgets::default(),
        source_identity: Some(source_identity),
        events,
        failure_code: None,
        lifecycle_projection_authorized: false,
        search_filtering_authorized: false,
        profile_state_write_authorized: false,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    };
    load.validate()?;
    Ok(load)
}

fn cold_contract() -> Result<(DocumentCatalogV2, DocumentLifecycleLedgerLoadV1)> {
    let load = cold_load(cold_events()?)?;
    let mut base = phase1_base_catalog(true)?;
    base.scan.usage.bytes_extracted = 0;
    for coverage in &mut base.coverage {
        coverage.body_extracted_files = 0;
        coverage.body_coverage = forager::document_lifecycle::BodyCoverage::Partial;
    }
    base.validate()?;
    let lifecycle_expectation = catalog_expectation(&base);
    let cold_state = DocumentLifecycleStateV1 {
        activity_state: ActivityState::Superseded,
        successor_document_id: Some(SECOND_DOCUMENT_ID.to_string()),
        ..active_state()
    };
    let entries = vec![
        DocumentCatalogEntryV2 {
            observation: base.entries[0].clone(),
            document_id: Some(FIRST_DOCUMENT_ID.to_string()),
            lifecycle_head_event_id: Some(FOURTH_EVENT_ID.to_string()),
            reviewed_state: Some(cold_state),
            lifecycle_stale: false,
            visibility: SearchVisibilityV2::Cold,
            visibility_evidence: "Reviewed inactive document has exact active replacement evidence; default query filtering is authorized."
                .to_string(),
        },
        DocumentCatalogEntryV2 {
            observation: base.entries[1].clone(),
            document_id: Some(SECOND_DOCUMENT_ID.to_string()),
            lifecycle_head_event_id: Some(THIRD_EVENT_ID.to_string()),
            reviewed_state: Some(active_state()),
            lifecycle_stale: false,
            visibility: SearchVisibilityV2::Visible,
            visibility_evidence:
                "Reviewed lifecycle state is current or reviewable; document remains visible."
                    .to_string(),
        },
    ];
    let catalog = DocumentCatalogV2 {
        schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
        base_catalog: base,
        lifecycle: LifecycleProjectionEnvelopeV2 {
            status: LifecycleProjectionStatusV1::Valid,
            expectation: Some(lifecycle_expectation),
            source_identity: load.source_identity.clone(),
            filtering_authorized: true,
            failure_code: None,
        },
        entries,
        lifecycle_findings: Vec::new(),
        authority_boundary: CatalogV2AuthorityBoundary {
            projection_only: true,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
    };
    assert!(catalog.validate().is_err());
    catalog.validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)?;
    Ok((catalog, load))
}

fn search_authority(filtering_applied: bool) -> SearchV2AuthorityBoundary {
    SearchV2AuthorityBoundary {
        read_only: true,
        lifecycle_filtering_applied: filtering_applied,
        search_visibility_mutation_authorized: false,
        profile_state_write_authorized: false,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    }
}

#[test]
fn cold_prefilter_matches_never_become_negative_content_or_owner_evidence() -> Result<()> {
    let (catalog, load) = cold_contract()?;
    let bodies = BTreeMap::new();
    let cold_only = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: timestamp("2026-08-20T00:41:00Z"),
        mode: DocumentSearchModeV2::Query,
        query: Some("report".to_string()),
        exact_selector: None,
        include_cold: false,
        catalog: catalog.clone(),
        prefilter_match_count: 1,
        visible_match_count: 0,
        cold_matches_omitted: 1,
        matches: Vec::new(),
        omitted_cold_matches: vec![DocumentSearchMatchV2 {
            entry: catalog.entries[0].clone(),
            match_kinds: vec![
                DocumentMatchKindV2::RelativePath,
                DocumentMatchKindV2::FileName,
            ],
            snippets: Vec::new(),
            cold_warning: Some(
                "This reviewed cold document matched the query and was omitted from default navigation."
                    .to_string(),
            ),
        }],
        negative_evidence: Some(NegativeSearchEvidenceV2 {
            content_absence_established: false,
            owner_absence_authorized: false,
            reason: "Matching cold documents were omitted from default navigation; no content or owner absence is established."
                .to_string(),
            metadata_only_families: vec![
                forager::document_lifecycle::MediaFamily::RestructuredText,
            ],
        }),
        authority_boundary: search_authority(true),
    };
    cold_only.validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)?;

    let returned_cold = DocumentSearchV2 {
        matches: vec![DocumentSearchMatchV2 {
            entry: catalog.entries[0].clone(),
            match_kinds: vec![
                DocumentMatchKindV2::RelativePath,
                DocumentMatchKindV2::FileName,
            ],
            snippets: Vec::new(),
            cold_warning: Some("Cold result should require include-cold.".to_string()),
        }],
        negative_evidence: None,
        prefilter_match_count: 1,
        visible_match_count: 0,
        cold_matches_omitted: 0,
        omitted_cold_matches: Vec::new(),
        authority_boundary: search_authority(false),
        ..cold_only.clone()
    };
    assert!(returned_cold
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());

    let mut false_absence = cold_only.clone();
    false_absence
        .negative_evidence
        .as_mut()
        .unwrap()
        .content_absence_established = true;
    assert!(false_absence
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());
    let mut false_owner = cold_only.clone();
    false_owner
        .negative_evidence
        .as_mut()
        .unwrap()
        .owner_absence_authorized = true;
    assert!(false_owner
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());

    let mut fabricated_omission = cold_only.clone();
    fabricated_omission.omitted_cold_matches[0].entry = catalog.entries[1].clone();
    assert!(fabricated_omission
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());
    let mut unrelated_omission = cold_only.clone();
    unrelated_omission.query = Some("current".to_string());
    assert!(unrelated_omission
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());

    let visible_match = DocumentSearchMatchV2 {
        entry: catalog.entries[1].clone(),
        match_kinds: vec![DocumentMatchKindV2::RelativePath],
        snippets: Vec::new(),
        cold_warning: None,
    };
    let mixed = DocumentSearchV2 {
        query: Some("docs".to_string()),
        matches: vec![visible_match],
        omitted_cold_matches: vec![DocumentSearchMatchV2 {
            entry: catalog.entries[0].clone(),
            match_kinds: vec![DocumentMatchKindV2::RelativePath],
            snippets: Vec::new(),
            cold_warning: Some(
                "This reviewed cold document matched the query and was omitted from default navigation."
                    .to_string(),
            ),
        }],
        negative_evidence: None,
        prefilter_match_count: 2,
        visible_match_count: 1,
        ..cold_only
    };
    mixed.validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)?;

    let duplicate_match = DocumentSearchV2 {
        matches: vec![mixed.matches[0].clone(), mixed.matches[0].clone()],
        prefilter_match_count: 2,
        visible_match_count: 2,
        ..mixed
    };
    assert!(duplicate_match
        .validate_against_lifecycle_and_live_bodies(&load, ROOT_INSTANCE_HASH, &bodies)
        .is_err());
    Ok(())
}

#[test]
fn exact_relative_path_retrieves_cold_document_with_warning() -> Result<()> {
    let (catalog, load) = cold_contract()?;
    let relative_path = catalog.entries[0].observation.relative_path.clone();
    let result = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: timestamp("2026-08-20T00:41:00Z"),
        mode: DocumentSearchModeV2::ExactDetail,
        query: None,
        exact_selector: Some(DocumentExactSelectorV2 {
            observation_id: None,
            document_id: Some(FIRST_DOCUMENT_ID.to_string()),
            relative_path: Some(relative_path),
        }),
        include_cold: false,
        catalog: catalog.clone(),
        prefilter_match_count: 1,
        visible_match_count: 0,
        cold_matches_omitted: 0,
        matches: vec![DocumentSearchMatchV2 {
            entry: catalog.entries[0].clone(),
            match_kinds: vec![
                DocumentMatchKindV2::DocumentId,
                DocumentMatchKindV2::ExactRelativePath,
            ],
            snippets: Vec::new(),
            cold_warning: Some(
                "This exact document is cold in default navigation but remains retrievable."
                    .to_string(),
            ),
        }],
        omitted_cold_matches: Vec::new(),
        negative_evidence: None,
        authority_boundary: search_authority(false),
    };
    result.validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)?;
    let mut omitted_existing = result.clone();
    omitted_existing.matches.clear();
    omitted_existing.prefilter_match_count = 0;
    omitted_existing.visible_match_count = 0;
    omitted_existing.negative_evidence = Some(NegativeSearchEvidenceV2 {
        content_absence_established: false,
        owner_absence_authorized: false,
        reason: "The exact selector does not resolve to a current observation.".to_string(),
        metadata_only_families: Vec::new(),
    });
    assert!(omitted_existing
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());
    let mut wrong_warning = result;
    wrong_warning.matches[0].cold_warning = Some("Cold but trust this result.".to_string());
    assert!(wrong_warning
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());
    Ok(())
}

#[test]
fn exact_selector_absence_is_valid_but_never_project_wide_content_absence() -> Result<()> {
    let (catalog, load) = cold_contract()?;
    let result = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: timestamp("2026-08-20T00:41:00Z"),
        mode: DocumentSearchModeV2::ExactDetail,
        query: None,
        exact_selector: Some(DocumentExactSelectorV2 {
            observation_id: None,
            document_id: Some("document_cccccccc-cccc-4ccc-8ccc-cccccccccccc".to_string()),
            relative_path: None,
        }),
        include_cold: false,
        catalog,
        prefilter_match_count: 0,
        visible_match_count: 0,
        cold_matches_omitted: 0,
        matches: Vec::new(),
        omitted_cold_matches: Vec::new(),
        negative_evidence: Some(NegativeSearchEvidenceV2 {
            content_absence_established: false,
            owner_absence_authorized: false,
            reason: "The exact selector does not resolve to a current observation.".to_string(),
            metadata_only_families: vec![
                forager::document_lifecycle::MediaFamily::RestructuredText,
            ],
        }),
        authority_boundary: search_authority(false),
    };
    result.validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)?;

    let mut false_absence = result;
    false_absence
        .negative_evidence
        .as_mut()
        .unwrap()
        .content_absence_established = true;
    assert!(false_absence
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());
    Ok(())
}

#[test]
fn catalog_filtering_requires_exact_root_time_and_complete_current_observation() -> Result<()> {
    let (mut wrong_root, load) = cold_contract()?;
    wrong_root
        .lifecycle
        .expectation
        .as_mut()
        .unwrap()
        .root_path_identity_sha256 =
        "9999999999999999999999999999999999999999999999999999999999999999".to_string();
    assert!(wrong_root
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut future_source, load) = cold_contract()?;
    future_source
        .lifecycle
        .source_identity
        .as_mut()
        .unwrap()
        .observed_at = timestamp("2026-08-20T00:40:01Z");
    assert!(future_source
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut incomplete, load) = cold_contract()?;
    incomplete.base_catalog.scan.complete = false;
    incomplete.base_catalog.scan.incomplete_scope.push(
        forager::document_lifecycle::IncompleteScope {
            relative_path: "unreadable".to_string(),
            reason: ScanIncompleteReason::IoError,
            detail: "One bounded path could not be read.".to_string(),
        },
    );
    incomplete.base_catalog.validate()?;
    assert!(incomplete
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut missing_modified_at, load) = cold_contract()?;
    missing_modified_at.base_catalog.entries[0].modified_at = None;
    missing_modified_at.entries[0].observation.modified_at = None;
    assert!(missing_modified_at.base_catalog.validate().is_ok());
    assert!(missing_modified_at
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut fabricated_state, load) = cold_contract()?;
    fabricated_state.entries[1]
        .reviewed_state
        .as_mut()
        .unwrap()
        .activity_state = ActivityState::ReviewDue;
    assert!(fabricated_state
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut missing_successor, load) = cold_contract()?;
    let successor = &mut missing_successor.entries[1];
    successor.document_id = None;
    successor.lifecycle_head_event_id = None;
    successor.reviewed_state = None;
    successor.lifecycle_stale = true;
    successor.visibility = SearchVisibilityV2::VisibleFailOpen;
    assert!(missing_successor
        .validate_against_lifecycle_load(&load, ROOT_INSTANCE_HASH)
        .is_err());

    let (mut source_less_valid, _) = cold_contract()?;
    source_less_valid.lifecycle.filtering_authorized = false;
    for entry in &mut source_less_valid.entries {
        entry.document_id = None;
        entry.lifecycle_head_event_id = None;
        entry.reviewed_state = None;
        entry.lifecycle_stale = true;
        entry.visibility = SearchVisibilityV2::VisibleFailOpen;
    }
    assert!(source_less_valid.validate().is_err());
    Ok(())
}

#[test]
fn only_empty_complete_prefilter_evidence_can_establish_content_absence() -> Result<()> {
    let mut base = phase1_base_catalog(false)?;
    base.entries.clear();
    base.crosswalk_findings.clear();
    base.coverage.clear();
    base.scan.usage.files_observed = 0;
    base.scan.usage.bytes_hashed = 0;
    base.scan.usage.bytes_extracted = 0;
    base.scan.usage.largest_file_bytes_read = 0;
    base.validate()?;
    let catalog = DocumentCatalogV2 {
        schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
        base_catalog: base,
        lifecycle: LifecycleProjectionEnvelopeV2 {
            status: LifecycleProjectionStatusV1::Absent,
            expectation: None,
            source_identity: None,
            filtering_authorized: false,
            failure_code: None,
        },
        entries: Vec::new(),
        lifecycle_findings: Vec::new(),
        authority_boundary: CatalogV2AuthorityBoundary {
            projection_only: true,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
    };
    let search = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: timestamp("2026-08-20T00:41:00Z"),
        mode: DocumentSearchModeV2::Query,
        query: Some("not-present".to_string()),
        exact_selector: None,
        include_cold: false,
        catalog,
        prefilter_match_count: 0,
        visible_match_count: 0,
        cold_matches_omitted: 0,
        matches: Vec::new(),
        omitted_cold_matches: Vec::new(),
        negative_evidence: Some(NegativeSearchEvidenceV2 {
            content_absence_established: true,
            owner_absence_authorized: false,
            reason:
                "The live bounded scan completed with full body coverage and found no matching content."
                    .to_string(),
            metadata_only_families: Vec::new(),
        }),
        authority_boundary: search_authority(false),
    };
    search.validate()?;
    Ok(())
}

#[test]
fn complete_body_coverage_cannot_hide_an_unextracted_searchable_body() -> Result<()> {
    let mut base = phase1_base_catalog(false)?;
    base.scan.usage.bytes_extracted = 0;
    base.coverage[0].body_extracted_files = 0;
    base.coverage[0].body_coverage = forager::document_lifecycle::BodyCoverage::Partial;
    base.validate()?;
    let entry = DocumentCatalogEntryV2 {
        observation: base.entries[0].clone(),
        document_id: None,
        lifecycle_head_event_id: None,
        reviewed_state: None,
        lifecycle_stale: true,
        visibility: SearchVisibilityV2::VisibleFailOpen,
        visibility_evidence: "Lifecycle projection unavailable (lifecycle ledger absent); document remains visible fail open."
            .to_string(),
    };
    let partial_catalog = DocumentCatalogV2 {
        schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
        base_catalog: base,
        lifecycle: LifecycleProjectionEnvelopeV2 {
            status: LifecycleProjectionStatusV1::Absent,
            expectation: None,
            source_identity: None,
            filtering_authorized: false,
            failure_code: None,
        },
        entries: vec![entry],
        lifecycle_findings: Vec::new(),
        authority_boundary: CatalogV2AuthorityBoundary {
            projection_only: true,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
    };
    let partial_search = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: timestamp("2026-08-20T00:41:00Z"),
        mode: DocumentSearchModeV2::Query,
        query: Some("report body text".to_string()),
        exact_selector: None,
        include_cold: false,
        catalog: partial_catalog,
        prefilter_match_count: 0,
        visible_match_count: 0,
        cold_matches_omitted: 0,
        matches: Vec::new(),
        omitted_cold_matches: Vec::new(),
        negative_evidence: Some(NegativeSearchEvidenceV2 {
            content_absence_established: false,
            owner_absence_authorized: false,
            reason: "One or more observed media families lack complete body extraction."
                .to_string(),
            metadata_only_families: vec![
                forager::document_lifecycle::MediaFamily::RestructuredText,
            ],
        }),
        authority_boundary: search_authority(false),
    };
    assert!(partial_search.validate().is_err());
    partial_search.validate_against_live_bodies(&BTreeMap::new())?;

    let mut forged_absence = partial_search;
    forged_absence.catalog.base_catalog.coverage[0].body_searchable_files = 0;
    forged_absence.catalog.base_catalog.coverage[0].body_coverage =
        forager::document_lifecycle::BodyCoverage::Complete;
    let negative = forged_absence
        .negative_evidence
        .as_mut()
        .context("forged absence evidence")?;
    negative.content_absence_established = true;
    negative.reason =
        "The live bounded scan completed with full body coverage and found no matching content."
            .to_string();
    negative.metadata_only_families.clear();
    assert!(forged_absence
        .validate_against_live_bodies(&BTreeMap::new())
        .is_err());
    Ok(())
}

#[test]
fn every_invalid_lifecycle_status_is_global_visible_fail_open() -> Result<()> {
    for status in [
        LifecycleProjectionStatusV1::Absent,
        LifecycleProjectionStatusV1::Corrupt,
        LifecycleProjectionStatusV1::UnsupportedSchema,
        LifecycleProjectionStatusV1::PartialWritePresent,
        LifecycleProjectionStatusV1::IdentityMismatch,
        LifecycleProjectionStatusV1::UnsafeStorage,
        LifecycleProjectionStatusV1::BudgetExceeded,
    ] {
        let base = phase1_base_catalog(false)?;
        let catalog = DocumentCatalogV2 {
            schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
            entries: vec![DocumentCatalogEntryV2 {
                observation: base.entries[0].clone(),
                document_id: None,
                lifecycle_head_event_id: None,
                reviewed_state: None,
                lifecycle_stale: true,
                visibility: SearchVisibilityV2::VisibleFailOpen,
                visibility_evidence: format!(
                    "Lifecycle projection unavailable ({}); document remains visible fail open.",
                    if status == LifecycleProjectionStatusV1::Absent {
                        "lifecycle ledger absent"
                    } else {
                        "lifecycle unavailable"
                    }
                ),
            }],
            base_catalog: base,
            lifecycle: LifecycleProjectionEnvelopeV2 {
                status,
                expectation: None,
                source_identity: None,
                filtering_authorized: false,
                failure_code: (status != LifecycleProjectionStatusV1::Absent)
                    .then(|| "lifecycle_unavailable".to_string()),
            },
            lifecycle_findings: Vec::new(),
            authority_boundary: CatalogV2AuthorityBoundary {
                projection_only: true,
                profile_state_write_authorized: false,
                project_state_write_authorized: false,
                file_mutation_authorized: false,
            },
        };
        catalog.validate()?;
        let mut leaked = catalog.clone();
        leaked.entries[0].visibility = SearchVisibilityV2::Visible;
        assert!(leaked.validate().is_err());
    }
    Ok(())
}

#[test]
fn loader_identity_mismatch_and_wrong_filename_never_return_events() -> Result<()> {
    let temp = TempDir::new()?;
    let event_dir = temp
        .path()
        .join("document-lifecycle")
        .join("v1")
        .join("events");
    fs::create_dir_all(&event_dir)?;
    for source in phase2a_event_paths() {
        fs::copy(&source, event_dir.join(source.file_name().unwrap()))?;
    }

    let mut wrong_identity = expectation();
    wrong_identity.root_instance_identity_sha256 =
        "9999999999999999999999999999999999999999999999999999999999999999".to_string();
    let report = load_document_lifecycle_ledger_from_root(
        temp.path(),
        &wrong_identity,
        &LifecycleLedgerLoadOptions::default(),
    )?;
    assert_eq!(report.status, LifecycleProjectionStatusV1::IdentityMismatch);
    assert!(report.events.is_empty());

    let first = event_dir.join(phase2a_event_paths()[0].file_name().unwrap());
    fs::rename(&first, event_dir.join("00000000000000000009_wrong.json"))?;
    let report: DocumentLifecycleLedgerLoadV1 = load_document_lifecycle_ledger_from_root(
        temp.path(),
        &expectation(),
        &LifecycleLedgerLoadOptions::default(),
    )?;
    assert_eq!(report.status, LifecycleProjectionStatusV1::Corrupt);
    assert!(report.events.is_empty());
    Ok(())
}
