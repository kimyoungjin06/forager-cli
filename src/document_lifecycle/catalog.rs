use super::{
    ActivityState, BodyCoverage, CanonicalRootObservation, CatalogAuthorityBoundary,
    CatalogFinding, CatalogSourceIdentities, CatalogV2AuthorityBoundary, ClassificationAuthority,
    CrosswalkStatus, DocumentCatalogEntry, DocumentCatalogEntryV2, DocumentCatalogV1,
    DocumentCatalogV2, DocumentExactSelectorV2, DocumentLifecycleLedgerLoadV1,
    DocumentLifecycleObservationV1, DocumentMatchKindV2, DocumentRole, DocumentSearchMatchV2,
    DocumentSearchModeV2, DocumentSearchV2, ExcludedScope, ExclusionReason, GitState,
    HygieneSeverity, IdentityCrosswalkFinding, IdentityReference, IdentityReferenceKind,
    IncompleteScope, LifecycleLedgerExpectationV1, LifecycleLedgerLoadOptions,
    LifecycleProjectionEnvelopeV2, LifecycleProjectionFindingV2, LifecycleProjectionStatusV1,
    MediaCoverage, MediaFamily, ProjectionSourceIdentity, RetentionDisposition, RootSourceKind,
    ScanBudgets, ScanEnvelope, ScanIncompleteReason, ScanLimit, ScanUsage,
    SearchV2AuthorityBoundary, SearchVisibilityV2, StrictUtcTimestamp, DOCUMENT_CATALOG_SCHEMA,
    DOCUMENT_CATALOG_SCHEMA_V2, DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA, DOCUMENT_SEARCH_SCHEMA_V2,
    FILESYSTEM_OBSERVATION_SOURCE_SCHEMA,
};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use git2::{Repository, Status};
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::{ErrorKind, Read};
use std::path::{Component, Path, PathBuf};
use std::sync::OnceLock;
use std::time::Instant;

pub const DOCUMENT_SEARCH_SCHEMA: &str = "document_search.v1";
pub const DEFAULT_MAX_FILES: u64 = 10_000;
pub const DEFAULT_MAX_DIRECTORY_ENTRIES: u64 = 100_000;
pub const DEFAULT_MAX_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
pub const DEFAULT_MAX_FILE_BYTES: u64 = 8 * 1024 * 1024;
pub const DEFAULT_MAX_ELAPSED_MS: u64 = 5_000;

type CatalogApprovalSnapshot = (
    Vec<crate::offdesk::approval::PendingActionApproval>,
    String,
    Option<(u64, u64)>,
);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogOptions {
    pub project_key: Option<String>,
    pub budgets: ScanBudgets,
}

impl Default for CatalogOptions {
    fn default() -> Self {
        Self {
            project_key: None,
            budgets: ScanBudgets {
                max_files: DEFAULT_MAX_FILES,
                max_directory_entries: DEFAULT_MAX_DIRECTORY_ENTRIES,
                max_total_bytes: DEFAULT_MAX_TOTAL_BYTES,
                max_file_bytes: DEFAULT_MAX_FILE_BYTES,
                max_elapsed_ms: DEFAULT_MAX_ELAPSED_MS,
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentSearchMode {
    Query,
    ObservationDetail,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentMatchKind {
    RelativePath,
    FileName,
    Body,
    ObservationId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentSearchMatch {
    pub match_kinds: Vec<DocumentMatchKind>,
    pub snippets: Vec<String>,
    pub entry: DocumentCatalogEntry,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchFreshness {
    pub mode: String,
    pub catalog_generated_at: DateTime<Utc>,
    pub stale: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchAuthorityBoundary {
    pub visibility_neutral: bool,
    pub lifecycle_filtering_applied: bool,
    pub negative_owner_conclusion_authorized: bool,
    pub search_visibility_mutation_authorized: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NegativeSearchEvidence {
    pub absence_established: bool,
    pub reason: String,
    pub metadata_only_families: Vec<MediaFamily>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentSearchV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub mode: DocumentSearchMode,
    pub query: Option<String>,
    pub observation_id: Option<String>,
    pub project_key: String,
    pub root: CanonicalRootObservation,
    pub filesystem_snapshot: ProjectionSourceIdentity,
    pub freshness: SearchFreshness,
    pub scan: ScanEnvelope,
    pub coverage: Vec<MediaCoverage>,
    pub authority_boundary: SearchAuthorityBoundary,
    pub matches: Vec<DocumentSearchMatch>,
    pub negative_evidence: Option<NegativeSearchEvidence>,
}

impl DocumentSearchV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_SEARCH_SCHEMA {
            bail!("unsupported document search schema: {}", self.schema);
        }
        if self.project_key.trim().is_empty() || self.root.project_key != self.project_key {
            bail!("document search project identity is inconsistent");
        }
        let root_path = Path::new(&self.root.canonical_path);
        if self.root.source_kind != RootSourceKind::Explicit
            || self.root.root_id.trim().is_empty()
            || !root_path.is_absolute()
            || self.root.canonical_path == "/"
            || self.root.path_identity_sha256.len() != 64
            || !self
                .root
                .path_identity_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            bail!("document search root is not one bounded explicit root");
        }
        if self.filesystem_snapshot.schema != FILESYSTEM_OBSERVATION_SOURCE_SCHEMA
            || self.filesystem_snapshot.identity.trim().is_empty()
            || self.filesystem_snapshot.content_sha256.len() != 64
            || !self
                .filesystem_snapshot
                .content_sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            bail!("document search requires an exact filesystem snapshot identity");
        }
        if self.freshness.mode != "live_bounded_scan" || self.freshness.stale {
            bail!("Phase 1 document search must report a fresh live bounded scan");
        }
        if self.freshness.catalog_generated_at != self.filesystem_snapshot.observed_at
            || self.generated_at < self.freshness.catalog_generated_at
        {
            bail!("document search freshness does not match its filesystem snapshot");
        }
        super::validate_scan_envelope(&self.scan)?;
        if !self.authority_boundary.visibility_neutral
            || self.authority_boundary.lifecycle_filtering_applied
            || self.authority_boundary.negative_owner_conclusion_authorized
            || self
                .authority_boundary
                .search_visibility_mutation_authorized
            || self.authority_boundary.profile_state_write_authorized
            || self.authority_boundary.project_state_write_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("document search authority exceeds the Phase 1 read-only boundary");
        }
        match self.mode {
            DocumentSearchMode::Query => {
                if self
                    .query
                    .as_deref()
                    .is_none_or(|query| query.trim().is_empty())
                    || self.observation_id.is_some()
                {
                    bail!("query search requires exactly one non-empty query");
                }
                if self.matches.is_empty() != self.negative_evidence.is_some() {
                    bail!("query search requires negative evidence exactly when no match exists");
                }
                if self.matches.iter().any(|result_match| {
                    result_match
                        .match_kinds
                        .contains(&DocumentMatchKind::ObservationId)
                }) {
                    bail!("query matches cannot claim observation-id selection");
                }
            }
            DocumentSearchMode::ObservationDetail => {
                if self.query.is_some()
                    || self
                        .observation_id
                        .as_deref()
                        .is_none_or(|identity| identity.trim().is_empty())
                    || self.matches.len() != 1
                    || self.matches[0].entry.observation_id.as_str()
                        != self.observation_id.as_deref().unwrap_or_default()
                    || self.matches[0].match_kinds != [DocumentMatchKind::ObservationId]
                    || !self.matches[0].snippets.is_empty()
                {
                    bail!("observation detail requires one exact current observation");
                }
            }
        }
        if !self.matches.is_empty() && self.negative_evidence.is_some() {
            bail!("matched search results cannot carry negative evidence");
        }
        if let Some(negative) = &self.negative_evidence {
            let full_body_coverage = self
                .coverage
                .iter()
                .all(|coverage| coverage.body_coverage == BodyCoverage::Complete);
            if negative.absence_established
                && (!self.scan.complete
                    || !full_body_coverage
                    || !negative.metadata_only_families.is_empty())
            {
                bail!("incomplete search evidence cannot establish absence");
            }
        }
        let mut observation_ids = BTreeSet::new();
        for result_match in &self.matches {
            if result_match.match_kinds.is_empty()
                || !observation_ids.insert(result_match.entry.observation_id.as_str())
                || result_match.entry.project_key != self.project_key
                || result_match.entry.root_id != self.root.root_id
                || result_match.entry.document_id.is_some()
                || result_match.entry.artifact_id.is_some()
                || result_match.entry.subject_key.is_some()
                || result_match.entry.subject_authority.is_some()
                || result_match.entry.activity_state != ActivityState::Unknown
                || result_match.entry.activity_authority.is_some()
                || result_match.entry.retention_disposition != RetentionDisposition::Unclassified
                || result_match.entry.retention_authority.is_some()
                || result_match.entry.generated.is_some()
                || result_match.entry.generated_authority.is_some()
            {
                bail!("search match exceeds filesystem-observation authority");
            }
        }
        Ok(())
    }
}

struct ScannedDocument {
    entry: DocumentCatalogEntry,
    body: Option<String>,
    extraction_error: bool,
}

struct LiveCatalog {
    catalog: DocumentCatalogV1,
    bodies: BTreeMap<String, String>,
    root_instance_identity_sha256: String,
}

struct LiveLifecycleCatalog {
    catalog: DocumentCatalogV2,
    bodies: BTreeMap<String, String>,
    lifecycle_load: DocumentLifecycleLedgerLoadV1,
    root_instance_identity_sha256: String,
}

struct ScanState<'a> {
    root: &'a Path,
    root_handle: fs::File,
    filesystem_boundary: crate::process::FilesystemBoundary,
    project_key: &'a str,
    root_id: &'a str,
    budgets: ScanBudgets,
    started: Instant,
    usage: ScanUsage,
    reached_limits: Vec<ScanLimit>,
    excluded_scope: Vec<ExcludedScope>,
    incomplete_scope: Vec<IncompleteScope>,
    documents: Vec<ScannedDocument>,
    git: Option<GitContext>,
    root_snapshot: crate::process::FilesystemObjectSnapshot,
    stopped: bool,
}

struct GitContext {
    repository: Repository,
    workdir: PathBuf,
}

#[derive(Debug, Clone, Copy)]
struct MediaSpec {
    family: MediaFamily,
    media_type: &'static str,
    body_searchable: bool,
}

pub fn scan_project(root: &Path, options: &CatalogOptions) -> Result<DocumentCatalogV1> {
    Ok(build_live_catalog(root, options)?.catalog)
}

pub fn scan_project_with_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
) -> Result<DocumentCatalogV2> {
    scan_project_with_lifecycle_hook(profile_root, project_root, options, || {})
}

fn scan_project_with_lifecycle_hook<F>(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
    after_initial_load: F,
) -> Result<DocumentCatalogV2>
where
    F: FnOnce(),
{
    Ok(build_live_catalog_with_lifecycle_hook(
        profile_root,
        project_root,
        options,
        after_initial_load,
    )?
    .catalog)
}

fn build_live_catalog_with_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
) -> Result<LiveLifecycleCatalog> {
    build_live_catalog_with_lifecycle_hook(profile_root, project_root, options, || {})
}

fn build_live_catalog_with_lifecycle_hook<F>(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
    after_initial_load: F,
) -> Result<LiveLifecycleCatalog>
where
    F: FnOnce(),
{
    let (canonical_root, expected_root) =
        observe_explicit_root_optional(project_root, options.project_key.as_deref())?;
    let initial_root = crate::process::open_bounded_root(&canonical_root)
        .with_context(|| format!("open project root {}", canonical_root.display()))?;
    let expected_root_instance =
        super::lifecycle::root_instance_identity_sha256_from_descriptor(&initial_root)?;
    let expectation = LifecycleLedgerExpectationV1 {
        project_key: expected_root.project_key.clone(),
        root_id: expected_root.root_id.clone(),
        root_path_identity_sha256: expected_root.path_identity_sha256.clone(),
        root_instance_identity_sha256: expected_root_instance.clone(),
    };
    let initial_approvals = load_catalog_approvals(profile_root).ok();
    let initial_load = load_catalog_lifecycle(
        profile_root,
        &expectation,
        StrictUtcTimestamp::from_datetime(Utc::now()),
    )?;
    after_initial_load();
    let live = build_live_catalog(project_root, options)?;
    let final_load = load_catalog_lifecycle(
        profile_root,
        &expectation,
        StrictUtcTimestamp::from_datetime(Utc::now()),
    )?;
    let final_approvals = load_catalog_approvals(profile_root).ok();
    let current_root_instance = crate::process::open_bounded_root(&canonical_root)
        .ok()
        .and_then(|root| {
            super::lifecycle::root_instance_identity_sha256_from_descriptor(&root).ok()
        });
    let root_stable = live.catalog.roots.first() == Some(&expected_root)
        && live.root_instance_identity_sha256 == expected_root_instance
        && current_root_instance.as_deref() == Some(expected_root_instance.as_str());
    let lifecycle_stable = lifecycle_load_content_equal(&initial_load, &final_load);
    let retention_integrity_valid = match (&initial_approvals, &final_approvals) {
        (
            Some((initial, initial_snapshot, initial_root_identity)),
            Some((current, current_snapshot, current_root_identity)),
        ) if initial == current
            && initial_snapshot == current_snapshot
            && initial_root_identity == current_root_identity =>
        {
            (initial_load.status != LifecycleProjectionStatusV1::Valid
                || super::lifecycle::retention_application_integrity_valid(
                    profile_root,
                    initial,
                    &initial_load,
                ))
                && catalog_profile_root_active(profile_root, *initial_root_identity)
        }
        _ => false,
    };
    let catalog = build_catalog_v2(
        live.catalog,
        &initial_load,
        &expected_root_instance,
        root_stable && lifecycle_stable,
        retention_integrity_valid,
    )?;
    Ok(LiveLifecycleCatalog {
        catalog,
        bodies: live.bodies,
        lifecycle_load: initial_load,
        root_instance_identity_sha256: expected_root_instance,
    })
}

fn catalog_profile_root_active(profile_root: &Path, expected_identity: Option<(u64, u64)>) -> bool {
    match (fs::symlink_metadata(profile_root), expected_identity) {
        (Err(error), None) if error.kind() == ErrorKind::NotFound => true,
        (Ok(metadata), Some(identity)) => {
            !metadata.file_type().is_symlink()
                && metadata.is_dir()
                && crate::process::filesystem_file_identity(&metadata) == Some(identity)
        }
        _ => false,
    }
}

fn load_catalog_approvals(profile_root: &Path) -> Result<CatalogApprovalSnapshot> {
    match fs::symlink_metadata(profile_root) {
        Err(error) if error.kind() == ErrorKind::NotFound => {
            Ok((Vec::new(), "absent".to_string(), None))
        }
        Err(error) => Err(error).context("inspect profile root for approval integrity"),
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
            bail!("profile root is not one bounded directory")
        }
        Ok(_) => crate::offdesk::approval::load_approvals_bounded_read_only(profile_root)
            .map(|(approvals, snapshot, identity)| (approvals, snapshot, Some(identity))),
    }
}

fn load_catalog_lifecycle(
    profile_root: &Path,
    expectation: &LifecycleLedgerExpectationV1,
    observed_at: StrictUtcTimestamp,
) -> Result<DocumentLifecycleLedgerLoadV1> {
    match fs::symlink_metadata(profile_root) {
        Err(error) if error.kind() == ErrorKind::NotFound => {
            let load = DocumentLifecycleLedgerLoadV1 {
                schema: DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA.to_string(),
                status: LifecycleProjectionStatusV1::Absent,
                expectation: expectation.clone(),
                budgets: super::LifecycleLedgerLoadBudgets::default(),
                source_identity: None,
                events: Vec::new(),
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
        _ => super::load_document_lifecycle_ledger_from_root(
            profile_root,
            expectation,
            &LifecycleLedgerLoadOptions {
                observed_at,
                ..LifecycleLedgerLoadOptions::default()
            },
        ),
    }
}

fn lifecycle_load_content_equal(
    left: &DocumentLifecycleLedgerLoadV1,
    right: &DocumentLifecycleLedgerLoadV1,
) -> bool {
    let source_equal = match (&left.source_identity, &right.source_identity) {
        (Some(left), Some(right)) => {
            left.schema == right.schema
                && left.first_sequence == right.first_sequence
                && left.last_sequence == right.last_sequence
                && left.event_count == right.event_count
                && left.final_event_sha256 == right.final_event_sha256
                && left.ordered_event_hashes_sha256 == right.ordered_event_hashes_sha256
        }
        (None, None) => true,
        _ => false,
    };
    left.schema == right.schema
        && left.status == right.status
        && left.expectation == right.expectation
        && left.budgets == right.budgets
        && source_equal
        && left.events == right.events
        && left.failure_code == right.failure_code
}

fn build_catalog_v2(
    base_catalog: DocumentCatalogV1,
    load: &DocumentLifecycleLedgerLoadV1,
    root_instance_identity_sha256: &str,
    snapshot_stable: bool,
    retention_integrity_valid: bool,
) -> Result<DocumentCatalogV2> {
    if !snapshot_stable {
        let catalog = invalid_catalog_v2(
            base_catalog,
            LifecycleProjectionStatusV1::IdentityMismatch,
            Some("lifecycle_or_root_changed_during_catalog_scan".to_string()),
        );
        catalog.validate()?;
        return Ok(catalog);
    }
    if !retention_integrity_valid {
        let catalog = invalid_catalog_v2(
            base_catalog,
            LifecycleProjectionStatusV1::Corrupt,
            Some("retention_application_integrity_error".to_string()),
        );
        catalog.validate()?;
        return Ok(catalog);
    }
    if load.status != LifecycleProjectionStatusV1::Valid {
        let catalog = invalid_catalog_v2(base_catalog, load.status, load.failure_code.clone());
        catalog.validate()?;
        return Ok(catalog);
    }

    let mut heads = BTreeMap::new();
    for event in &load.events {
        heads.insert(event.payload.document_id.clone(), event);
    }
    let mut exact_by_observation = BTreeMap::new();
    let mut unprojected_document_ids = Vec::new();
    for (document_id, head) in &heads {
        let exact = base_catalog
            .entries
            .iter()
            .filter(|entry| {
                current_lifecycle_observation(entry).as_ref() == Some(&head.payload.observation)
            })
            .collect::<Vec<_>>();
        if exact.len() == 1 {
            if exact_by_observation
                .insert(exact[0].observation_id.clone(), (*document_id).clone())
                .is_some()
            {
                bail!("one current observation cannot project multiple lifecycle documents");
            }
            continue;
        }
        if exact.len() > 1 {
            bail!("one lifecycle head matched multiple identical current observations");
        }
        unprojected_document_ids.push((*document_id).clone());
    }
    let exact_bound_observation_ids = exact_by_observation
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut unprojected_head_counts_by_hash = BTreeMap::<String, usize>::new();
    for document_id in &unprojected_document_ids {
        let head = heads
            .get(document_id)
            .expect("unprojected lifecycle head was collected above");
        if !base_catalog
            .entries
            .iter()
            .any(|entry| entry.relative_path == head.payload.observation.relative_path)
        {
            *unprojected_head_counts_by_hash
                .entry(head.payload.observation.content_sha256.clone())
                .or_default() += 1;
        }
    }
    let mut findings = Vec::new();
    let mut stale_current_observations = BTreeSet::new();
    for document_id in unprojected_document_ids {
        let head = heads
            .get(&document_id)
            .expect("unprojected lifecycle head was collected above");
        let finding = lifecycle_projection_finding(
            document_id,
            head,
            &base_catalog.entries,
            base_catalog.scan.complete,
            &exact_bound_observation_ids,
            unprojected_head_counts_by_hash
                .get(&head.payload.observation.content_sha256)
                .copied()
                .unwrap_or_default(),
        )?;
        stale_current_observations.extend(
            finding
                .current_observation_ids
                .iter()
                .filter(|observation_id| !exact_by_observation.contains_key(*observation_id))
                .cloned(),
        );
        findings.push(finding);
    }
    let filtering_authorized = base_catalog.scan.complete
        && findings.is_empty()
        && exact_by_observation.len() == heads.len();
    let exact_active_document_ids = if filtering_authorized {
        heads
            .iter()
            .filter(|(_, event)| event.payload.after.activity_state == ActivityState::Active)
            .map(|(document_id, _)| (*document_id).clone())
            .collect::<BTreeSet<_>>()
    } else {
        BTreeSet::new()
    };

    let entries = base_catalog
        .entries
        .iter()
        .map(|observation| {
            let exact_document = exact_by_observation.get(&observation.observation_id);
            let head = exact_document.and_then(|document_id| heads.get(document_id));
            let lifecycle_stale = stale_current_observations.contains(&observation.observation_id);
            let (visibility, visibility_evidence) = if filtering_authorized {
                super::reviewed::lifecycle_filtering_visibility_v2(
                    exact_document.map(String::as_str),
                    head.map(|event| &event.payload.after),
                    &exact_active_document_ids,
                )
            } else {
                (
                    SearchVisibilityV2::VisibleFailOpen,
                    super::reviewed::lifecycle_disabled_visibility_evidence_v2(
                        LifecycleProjectionStatusV1::Valid,
                        None,
                        head.is_some(),
                        lifecycle_stale,
                    ),
                )
            };
            DocumentCatalogEntryV2 {
                observation: observation.clone(),
                document_id: exact_document.cloned(),
                lifecycle_head_event_id: head.map(|event| event.payload.event_id.clone()),
                reviewed_state: head.map(|event| event.payload.after.clone()),
                lifecycle_stale,
                visibility,
                visibility_evidence,
            }
        })
        .collect();
    let catalog = DocumentCatalogV2 {
        schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
        base_catalog,
        lifecycle: LifecycleProjectionEnvelopeV2 {
            status: LifecycleProjectionStatusV1::Valid,
            expectation: Some(load.expectation.clone()),
            source_identity: load.source_identity.clone(),
            filtering_authorized,
            failure_code: None,
        },
        entries,
        lifecycle_findings: findings,
        authority_boundary: read_only_catalog_v2_authority(),
    };
    catalog.validate_against_lifecycle_load(load, root_instance_identity_sha256)?;
    Ok(catalog)
}

fn invalid_catalog_v2(
    base_catalog: DocumentCatalogV1,
    status: LifecycleProjectionStatusV1,
    failure_code: Option<String>,
) -> DocumentCatalogV2 {
    let entries = base_catalog
        .entries
        .iter()
        .cloned()
        .map(|observation| DocumentCatalogEntryV2 {
            observation,
            document_id: None,
            lifecycle_head_event_id: None,
            reviewed_state: None,
            lifecycle_stale: true,
            visibility: SearchVisibilityV2::VisibleFailOpen,
            visibility_evidence: super::reviewed::lifecycle_disabled_visibility_evidence_v2(
                status,
                failure_code.as_deref(),
                false,
                true,
            ),
        })
        .collect();
    DocumentCatalogV2 {
        schema: DOCUMENT_CATALOG_SCHEMA_V2.to_string(),
        base_catalog,
        lifecycle: LifecycleProjectionEnvelopeV2 {
            status,
            expectation: None,
            source_identity: None,
            filtering_authorized: false,
            failure_code,
        },
        entries,
        lifecycle_findings: Vec::new(),
        authority_boundary: read_only_catalog_v2_authority(),
    }
}

fn read_only_catalog_v2_authority() -> CatalogV2AuthorityBoundary {
    CatalogV2AuthorityBoundary {
        projection_only: true,
        profile_state_write_authorized: false,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    }
}

fn lifecycle_projection_finding(
    document_id: String,
    head: &super::DocumentLifecycleEventV1,
    entries: &[DocumentCatalogEntry],
    scan_complete: bool,
    exact_bound_observation_ids: &BTreeSet<String>,
    competing_unprojected_heads: usize,
) -> Result<LifecycleProjectionFindingV2> {
    let evidence = super::reviewed::derive_lifecycle_finding_evidence_v2(
        &head.payload.observation,
        entries,
        scan_complete,
        exact_bound_observation_ids,
        competing_unprojected_heads,
    )?;
    Ok(LifecycleProjectionFindingV2 {
        code: evidence.code.to_string(),
        severity: HygieneSeverity::Warn,
        document_id,
        reviewed_observation_id: head.payload.observation.observation_id.clone(),
        current_observation_ids: evidence.current_observation_ids,
        conflict: evidence.conflict,
        message: evidence.message.to_string(),
    })
}

fn current_lifecycle_observation(
    entry: &DocumentCatalogEntry,
) -> Option<DocumentLifecycleObservationV1> {
    Some(DocumentLifecycleObservationV1 {
        observation_id: entry.observation_id.clone(),
        relative_path: entry.relative_path.clone(),
        size_bytes: entry.size_bytes,
        modified_at: StrictUtcTimestamp::from_datetime(entry.modified_at?),
        content_sha256: entry.content_sha256.clone()?,
    })
}

pub fn search_project(
    root: &Path,
    options: &CatalogOptions,
    query: &str,
) -> Result<DocumentSearchV1> {
    let query = query.trim();
    if query.is_empty() {
        bail!("document search query must not be empty");
    }

    let live = build_live_catalog(root, options)?;
    let normalized_query = query.to_lowercase();
    let mut matches = Vec::new();

    for entry in &live.catalog.entries {
        let normalized_path = entry.relative_path.to_lowercase();
        let normalized_file_name = Path::new(&entry.relative_path)
            .file_name()
            .map(|name| name.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let mut match_kinds = BTreeSet::new();
        if normalized_path.contains(&normalized_query) {
            match_kinds.insert(DocumentMatchKind::RelativePath);
        }
        if normalized_file_name.contains(&normalized_query) {
            match_kinds.insert(DocumentMatchKind::FileName);
        }

        let snippets = live
            .bodies
            .get(&entry.observation_id)
            .map(|body| matching_snippets(body, &normalized_query))
            .unwrap_or_default();
        if !snippets.is_empty() {
            match_kinds.insert(DocumentMatchKind::Body);
        }
        if !match_kinds.is_empty() {
            matches.push(DocumentSearchMatch {
                match_kinds: match_kinds.into_iter().collect(),
                snippets,
                entry: entry.clone(),
            });
        }
    }

    let negative_evidence = matches
        .is_empty()
        .then(|| negative_search_evidence(&live.catalog));
    let result = search_envelope(
        live.catalog,
        DocumentSearchMode::Query,
        Some(query.to_string()),
        None,
        matches,
        negative_evidence,
    );
    result.validate()?;
    Ok(result)
}

pub fn lookup_observation(
    root: &Path,
    options: &CatalogOptions,
    observation_id: &str,
) -> Result<DocumentSearchV1> {
    let observation_id = observation_id.trim();
    if observation_id.is_empty() {
        bail!("observation_id must not be empty");
    }

    let live = build_live_catalog(root, options)?;
    let entry = live
        .catalog
        .entries
        .iter()
        .find(|entry| entry.observation_id == observation_id)
        .cloned()
        .with_context(|| {
            format!("observation_id {observation_id} is not present in the current live catalog")
        })?;
    let result = DocumentSearchMatch {
        match_kinds: vec![DocumentMatchKind::ObservationId],
        snippets: Vec::new(),
        entry,
    };
    let result = search_envelope(
        live.catalog,
        DocumentSearchMode::ObservationDetail,
        None,
        Some(observation_id.to_string()),
        vec![result],
        None,
    );
    result.validate()?;
    Ok(result)
}

pub fn search_project_with_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
    query: &str,
    include_cold: bool,
) -> Result<DocumentSearchV2> {
    let query = query.trim();
    if query.is_empty() {
        bail!("document search query must not be empty");
    }
    let live = build_live_catalog_with_lifecycle(profile_root, project_root, options)?;
    let normalized_query = query.to_lowercase();
    let mut prefilter_matches = Vec::new();
    for entry in &live.catalog.entries {
        let relative_path = entry.observation.relative_path.to_lowercase();
        let file_name = Path::new(&entry.observation.relative_path)
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default()
            .to_lowercase();
        let mut match_kinds = BTreeSet::new();
        if relative_path.contains(&normalized_query) {
            match_kinds.insert(DocumentMatchKindV2::RelativePath);
        }
        if file_name.contains(&normalized_query) {
            match_kinds.insert(DocumentMatchKindV2::FileName);
        }
        let snippets = live
            .bodies
            .get(&entry.observation.observation_id)
            .map(|body| super::reviewed::matching_snippets_v2(body, &normalized_query))
            .unwrap_or_default();
        if !snippets.is_empty() {
            match_kinds.insert(DocumentMatchKindV2::Body);
        }
        if !match_kinds.is_empty() {
            prefilter_matches.push(DocumentSearchMatchV2 {
                entry: entry.clone(),
                match_kinds: match_kinds.into_iter().collect(),
                snippets,
                cold_warning: (entry.visibility == SearchVisibilityV2::Cold)
                    .then(|| super::reviewed::QUERY_COLD_WARNING_V2.to_string()),
            });
        }
    }
    prefilter_matches.sort_by(|left, right| {
        super::reviewed::compare_search_entries_v2(&left.entry, &right.entry)
    });
    let prefilter_match_count = prefilter_matches.len() as u64;
    let mut matches = Vec::new();
    let mut omitted_cold_matches = Vec::new();
    for result_match in prefilter_matches {
        if result_match.entry.visibility == SearchVisibilityV2::Cold && !include_cold {
            omitted_cold_matches.push(result_match);
        } else {
            matches.push(result_match);
        }
    }
    let visible_match_count = matches
        .iter()
        .filter(|result_match| result_match.entry.visibility != SearchVisibilityV2::Cold)
        .count() as u64;
    let cold_matches_omitted = omitted_cold_matches.len() as u64;
    let negative_evidence = matches.is_empty().then(|| {
        super::reviewed::expected_negative_search_evidence_v2(
            DocumentSearchModeV2::Query,
            prefilter_match_count,
            &live.catalog,
        )
    });
    let result = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(Utc::now()),
        mode: DocumentSearchModeV2::Query,
        query: Some(query.to_string()),
        exact_selector: None,
        include_cold,
        catalog: live.catalog,
        prefilter_match_count,
        visible_match_count,
        cold_matches_omitted,
        matches,
        omitted_cold_matches,
        negative_evidence,
        authority_boundary: SearchV2AuthorityBoundary {
            read_only: true,
            lifecycle_filtering_applied: cold_matches_omitted > 0,
            search_visibility_mutation_authorized: false,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
    };
    validate_live_search_v2(
        &result,
        &live.lifecycle_load,
        &live.root_instance_identity_sha256,
        &live.bodies,
    )?;
    Ok(result)
}

pub fn lookup_document_with_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    options: &CatalogOptions,
    selector: DocumentExactSelectorV2,
) -> Result<DocumentSearchV2> {
    selector.validate()?;
    let live = build_live_catalog_with_lifecycle(profile_root, project_root, options)?;
    let mut resolved = live
        .catalog
        .entries
        .iter()
        .filter(|entry| selector.matches(entry))
        .cloned()
        .collect::<Vec<_>>();
    if resolved.len() > 1 {
        bail!("exact document selectors resolve to multiple current observations");
    }
    if resolved.is_empty() && selector.component_matches_any(&live.catalog.entries) {
        bail!("exact document selectors do not resolve to the same current observation");
    }
    let matches = resolved
        .pop()
        .map(|entry| {
            let mut match_kinds = BTreeSet::new();
            if selector.observation_id.is_some() {
                match_kinds.insert(DocumentMatchKindV2::ObservationId);
            }
            if selector.document_id.is_some() {
                match_kinds.insert(DocumentMatchKindV2::DocumentId);
            }
            if selector.relative_path.is_some() {
                match_kinds.insert(DocumentMatchKindV2::ExactRelativePath);
            }
            let cold_warning = (entry.visibility == SearchVisibilityV2::Cold)
                .then(|| super::reviewed::EXACT_COLD_WARNING_V2.to_string());
            DocumentSearchMatchV2 {
                entry,
                match_kinds: match_kinds.into_iter().collect(),
                snippets: Vec::new(),
                cold_warning,
            }
        })
        .into_iter()
        .collect::<Vec<_>>();
    let prefilter_match_count = matches.len() as u64;
    let visible_match_count = matches
        .iter()
        .filter(|result_match| result_match.entry.visibility != SearchVisibilityV2::Cold)
        .count() as u64;
    let negative_evidence = matches.is_empty().then(|| {
        super::reviewed::expected_negative_search_evidence_v2(
            DocumentSearchModeV2::ExactDetail,
            prefilter_match_count,
            &live.catalog,
        )
    });
    let result = DocumentSearchV2 {
        schema: DOCUMENT_SEARCH_SCHEMA_V2.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(Utc::now()),
        mode: DocumentSearchModeV2::ExactDetail,
        query: None,
        exact_selector: Some(selector),
        include_cold: false,
        catalog: live.catalog,
        prefilter_match_count,
        visible_match_count,
        cold_matches_omitted: 0,
        matches,
        omitted_cold_matches: Vec::new(),
        negative_evidence,
        authority_boundary: SearchV2AuthorityBoundary {
            read_only: true,
            lifecycle_filtering_applied: false,
            search_visibility_mutation_authorized: false,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
    };
    validate_live_search_v2(
        &result,
        &live.lifecycle_load,
        &live.root_instance_identity_sha256,
        &live.bodies,
    )?;
    Ok(result)
}

fn validate_live_search_v2(
    result: &DocumentSearchV2,
    load: &DocumentLifecycleLedgerLoadV1,
    root_instance_identity_sha256: &str,
    bodies: &BTreeMap<String, String>,
) -> Result<()> {
    if result.catalog.lifecycle.status == LifecycleProjectionStatusV1::Valid {
        result.validate_against_lifecycle_and_live_bodies(
            load,
            root_instance_identity_sha256,
            bodies,
        )
    } else {
        result.validate_against_live_bodies(bodies)
    }
}

fn build_live_catalog(root: &Path, options: &CatalogOptions) -> Result<LiveCatalog> {
    build_live_catalog_with_hook(root, options, &mut |_| {})
}

fn build_live_catalog_with_hook<F>(
    root: &Path,
    options: &CatalogOptions,
    hook: &mut F,
) -> Result<LiveCatalog>
where
    F: FnMut(&Path),
{
    let canonical_root = validate_root(root)?;
    let canonical_path = canonical_root
        .to_str()
        .context("project root must be valid UTF-8")?
        .to_string();
    let project_key = resolve_project_key(&canonical_root, options.project_key.as_deref())?;
    let path_identity_sha256 = sha256_hex(canonical_path.as_bytes());
    let root_id = format!("root_{}", &path_identity_sha256[..24]);
    let started = Instant::now();
    let git = GitContext::open(&canonical_root);
    let root_metadata = fs::symlink_metadata(&canonical_root)
        .with_context(|| format!("inspect canonical project root {canonical_path}"))?;
    let root_handle = crate::process::open_bounded_root(&canonical_root)
        .with_context(|| format!("open bounded project root {canonical_path}"))?;
    let root_handle_metadata = root_handle
        .metadata()
        .with_context(|| format!("inspect bounded project root {canonical_path}"))?;
    if !root_handle_metadata.is_dir()
        || !same_filesystem_object(&root_metadata, &root_handle_metadata)
    {
        bail!("project root changed while its bounded descriptor was opened");
    }
    let filesystem_boundary =
        crate::process::FilesystemBoundary::for_root(&canonical_root, &root_handle_metadata);
    let root_snapshot =
        crate::process::FilesystemObjectSnapshot::from_metadata(&root_handle_metadata);
    let mut state = ScanState {
        root: &canonical_root,
        root_handle,
        filesystem_boundary,
        project_key: &project_key,
        root_id: &root_id,
        budgets: options.budgets.clone(),
        started,
        usage: ScanUsage {
            files_observed: 0,
            directory_entries_observed: 0,
            bytes_hashed: 0,
            bytes_extracted: 0,
            largest_file_bytes_read: 0,
            elapsed_ms: 0,
        },
        reached_limits: Vec::new(),
        excluded_scope: Vec::new(),
        incomplete_scope: Vec::new(),
        documents: Vec::new(),
        git,
        root_snapshot,
        stopped: false,
    };

    walk_directory(&canonical_root, &mut state, hook)?;
    revalidate_root_path(&canonical_root, &mut state);
    link_markdown_documents(&mut state.documents);
    link_exact_duplicates(&mut state.documents);
    state.documents.sort_by(|left, right| {
        left.entry
            .relative_path
            .cmp(&right.entry.relative_path)
            .then_with(|| left.entry.observation_id.cmp(&right.entry.observation_id))
    });
    state
        .excluded_scope
        .sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    let elapsed_ms = state.started.elapsed().as_millis() as u64;
    if elapsed_ms > state.budgets.max_elapsed_ms
        && !state.reached_limits.contains(&ScanLimit::ElapsedTime)
    {
        record_limit(&mut state, ScanLimit::ElapsedTime);
        state.incomplete_scope.push(IncompleteScope {
            relative_path: ".".to_string(),
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: "elapsed-time budget was exceeded before an in-budget scan could be certified"
                .to_string(),
        });
    }
    state
        .incomplete_scope
        .sort_by(|left, right| left.relative_path.cmp(&right.relative_path));
    state.usage.elapsed_ms = elapsed_ms;
    let snapshot_sha256 = filesystem_snapshot_sha256(&state);
    let root_instance_identity_sha256 =
        super::lifecycle::root_instance_identity_sha256_from_descriptor(&state.root_handle)?;
    let generated_at = Utc::now();
    let coverage = media_coverage(&state.documents);
    let entries: Vec<_> = state
        .documents
        .iter()
        .map(|document| document.entry.clone())
        .collect();
    let bodies = state
        .documents
        .iter()
        .filter_map(|document| {
            document
                .body
                .as_ref()
                .map(|body| (document.entry.observation_id.clone(), body.clone()))
        })
        .collect();
    let crosswalk_findings = entries
        .iter()
        .map(|entry| IdentityCrosswalkFinding {
            observation_id: entry.observation_id.clone(),
            document_id: None,
            artifact_id: None,
            status: CrosswalkStatus::CatalogOnly,
            conflict: None,
            message: "filesystem observation only; no durable lifecycle or artifact identity was consulted"
                .to_string(),
        })
        .collect();

    let catalog = DocumentCatalogV1 {
        schema: DOCUMENT_CATALOG_SCHEMA.to_string(),
        generated_at,
        project_key: project_key.clone(),
        roots: vec![CanonicalRootObservation {
            root_id: root_id.clone(),
            source_kind: RootSourceKind::Explicit,
            project_key: project_key.clone(),
            canonical_path,
            path_identity_sha256,
        }],
        scan: ScanEnvelope {
            complete: state.incomplete_scope.is_empty(),
            budgets: state.budgets,
            usage: state.usage,
            reached_limits: state.reached_limits,
            excluded_scope: state.excluded_scope,
            incomplete_scope: state.incomplete_scope,
        },
        coverage,
        source_identities: CatalogSourceIdentities {
            filesystem_snapshot: ProjectionSourceIdentity {
                schema: FILESYSTEM_OBSERVATION_SOURCE_SCHEMA.to_string(),
                identity: format!("filesystem_snapshot_{}", &snapshot_sha256[..24]),
                content_sha256: snapshot_sha256,
                observed_at: generated_at,
            },
            lifecycle_ledger: None,
            artifact_index: None,
        },
        authority_boundary: CatalogAuthorityBoundary {
            projection_only: true,
            filesystem_observation_owns: vec![
                "canonical_path".to_string(),
                "size_bytes".to_string(),
                "modified_at".to_string(),
                "content_sha256_when_within_budget".to_string(),
                "git_state".to_string(),
                "markdown_links".to_string(),
                "exact_duplicate_bytes".to_string(),
            ],
            lifecycle_ledger_owns: vec![
                "document_id".to_string(),
                "activity_state".to_string(),
                "retention_disposition".to_string(),
                "search_visibility".to_string(),
            ],
            artifact_index_owns: vec![
                "artifact_id".to_string(),
                "artifact_role".to_string(),
                "artifact_retention_evidence".to_string(),
            ],
            search_visibility_mutation_authorized: false,
            file_mutation_authorized: false,
        },
        entries,
        crosswalk_findings,
    };
    catalog.validate()?;
    Ok(LiveCatalog {
        catalog,
        bodies,
        root_instance_identity_sha256,
    })
}

fn validate_root(root: &Path) -> Result<PathBuf> {
    let input_metadata = fs::symlink_metadata(root)
        .with_context(|| format!("inspect project root {}", root.display()))?;
    if input_metadata.file_type().is_symlink() {
        bail!("project root must not be a symlink: {}", root.display());
    }
    if !input_metadata.is_dir() {
        bail!("project root must be a directory: {}", root.display());
    }
    let canonical = root
        .canonicalize()
        .with_context(|| format!("resolve project root {}", root.display()))?;
    let normal_components = canonical
        .components()
        .filter(|component| matches!(component, Component::Normal(_)))
        .count();
    if canonical.parent().is_none() || normal_components < 2 {
        bail!(
            "project root is too broad for a bounded document scan: {}",
            canonical.display()
        );
    }
    if dirs::home_dir()
        .and_then(|home| home.canonicalize().ok())
        .as_ref()
        == Some(&canonical)
    {
        bail!(
            "the home directory is too broad for a bounded document scan: {}",
            canonical.display()
        );
    }
    Ok(canonical)
}

pub(super) fn observe_explicit_root(
    root: &Path,
    project_key: &str,
) -> Result<(PathBuf, CanonicalRootObservation)> {
    observe_explicit_root_optional(root, Some(project_key))
}

fn observe_explicit_root_optional(
    root: &Path,
    project_key: Option<&str>,
) -> Result<(PathBuf, CanonicalRootObservation)> {
    let canonical_root = validate_root(root)?;
    let canonical_path = canonical_root
        .to_str()
        .context("project root must be valid UTF-8")?
        .to_string();
    let project_key = resolve_project_key(&canonical_root, project_key)?;
    let path_identity_sha256 = sha256_hex(canonical_path.as_bytes());
    let root_id = format!("root_{}", &path_identity_sha256[..24]);
    Ok((
        canonical_root,
        CanonicalRootObservation {
            root_id,
            source_kind: RootSourceKind::Explicit,
            project_key,
            canonical_path,
            path_identity_sha256,
        },
    ))
}

fn resolve_project_key(root: &Path, explicit: Option<&str>) -> Result<String> {
    if let Some(explicit) = explicit {
        let explicit = explicit.trim();
        if explicit.is_empty() {
            bail!("project_key must not be empty");
        }
        return Ok(explicit.to_string());
    }
    let candidate = root
        .file_name()
        .map(|name| name.to_string_lossy())
        .unwrap_or_default();
    let mut key = String::new();
    let mut separator_pending = false;
    for character in candidate.chars() {
        if character.is_ascii_alphanumeric() {
            if separator_pending && !key.is_empty() {
                key.push('-');
            }
            key.push(character.to_ascii_lowercase());
            separator_pending = false;
        } else {
            separator_pending = true;
        }
    }
    if key.is_empty() {
        bail!("could not derive a project_key from {}", root.display());
    }
    Ok(key)
}

fn walk_directory<F>(directory: &Path, state: &mut ScanState<'_>, hook: &mut F) -> Result<()>
where
    F: FnMut(&Path),
{
    if state.stopped {
        return Ok(());
    }
    let relative_directory = directory.strip_prefix(state.root).unwrap_or(directory);
    let remaining_directory_entries = state
        .budgets
        .max_directory_entries
        .saturating_sub(state.usage.directory_entries_observed);
    let directory_read = match crate::process::read_bounded_directory(
        &state.root_handle,
        relative_directory,
        remaining_directory_entries,
        state.started,
        state.budgets.max_elapsed_ms,
    ) {
        Ok(read) => read,
        Err(error) => {
            state.incomplete_scope.push(IncompleteScope {
                relative_path: relative_path(state.root, directory),
                reason: bounded_open_reason(&error),
                detail: format!("directory could not be read through the bounded root: {error}"),
            });
            return Ok(());
        }
    };
    state.usage.directory_entries_observed = state
        .usage
        .directory_entries_observed
        .saturating_add(directory_read.entries_observed);
    let directory_snapshot = directory_read.snapshot.clone();
    hook(directory);
    if !directory_read.stable
        || !crate::process::bounded_directory_snapshot(&state.root_handle, relative_directory)
            .is_ok_and(|current| current == directory_snapshot)
    {
        record_changed_directory(
            directory,
            state,
            "directory identity or contents changed during enumeration",
        );
        return Ok(());
    }
    if let Some(limit) = directory_read.limit {
        let scan_limit = match limit {
            crate::process::DirectoryReadLimit::EntryCount => ScanLimit::DirectoryEntries,
            crate::process::DirectoryReadLimit::ElapsedTime => ScanLimit::ElapsedTime,
        };
        record_limit(state, scan_limit);
        state.incomplete_scope.push(IncompleteScope {
            relative_path: relative_path(state.root, directory),
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: match limit {
                crate::process::DirectoryReadLimit::EntryCount => {
                    "directory-entry budget reached before this directory could be enumerated"
                        .to_string()
                }
                crate::process::DirectoryReadLimit::ElapsedTime => {
                    "elapsed-time budget reached while this directory was being enumerated"
                        .to_string()
                }
            },
        });
        state.stopped = true;
        return Ok(());
    }
    let paths: Vec<_> = directory_read
        .names
        .into_iter()
        .map(|name| directory.join(name))
        .collect();

    for (index, path) in paths.iter().enumerate() {
        if state.started.elapsed().as_millis() as u64 >= state.budgets.max_elapsed_ms {
            record_limit(state, ScanLimit::ElapsedTime);
            record_remaining_scope(
                state,
                &paths[index..],
                "elapsed-time budget reached before this path was inspected",
            );
            state.stopped = true;
            break;
        }

        let relative = relative_path(state.root, path);
        let metadata = match fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) => {
                state.incomplete_scope.push(IncompleteScope {
                    relative_path: relative,
                    reason: incomplete_reason(&error),
                    detail: format!("path metadata could not be read: {error}"),
                });
                continue;
            }
        };
        let file_type = metadata.file_type();
        if file_type.is_symlink() {
            record_symlink_exclusion(path, &relative, state);
            continue;
        }
        if state
            .filesystem_boundary
            .is_descendant_mount(path, &metadata)
        {
            state.excluded_scope.push(ExcludedScope {
                relative_path: relative,
                reason: ExclusionReason::MountPoint,
                detail: "mounted filesystem boundary was not traversed".to_string(),
            });
            continue;
        }
        if metadata.is_dir() {
            if let Some((reason, detail)) = excluded_directory(path, state.root) {
                state.excluded_scope.push(ExcludedScope {
                    relative_path: relative,
                    reason,
                    detail,
                });
                continue;
            }
            walk_directory(path, state, hook)?;
            if state.stopped {
                record_remaining_scope(
                    state,
                    &paths[index + 1..],
                    "ancestor traversal stopped after a nested scan budget was reached",
                );
                break;
            }
            continue;
        }
        if !metadata.is_file() {
            state.excluded_scope.push(ExcludedScope {
                relative_path: relative,
                reason: ExclusionReason::SpecialFile,
                detail: "non-regular filesystem object was not read".to_string(),
            });
            continue;
        }
        let Some(media) = media_spec(path) else {
            continue;
        };
        if state.usage.files_observed >= state.budgets.max_files {
            record_limit(state, ScanLimit::FileCount);
            record_remaining_scope(
                state,
                &paths[index..],
                "file-count budget reached before this path was inspected",
            );
            state.stopped = true;
            break;
        }
        observe_document(path, relative, metadata, media, state);
    }
    if !crate::process::bounded_directory_snapshot(&state.root_handle, relative_directory)
        .is_ok_and(|current| current == directory_snapshot)
    {
        record_changed_directory(
            directory,
            state,
            "directory identity or contents changed after enumeration",
        );
    }
    Ok(())
}

fn record_changed_directory(directory: &Path, state: &mut ScanState<'_>, detail: &str) {
    let relative_path = relative_path(state.root, directory);
    if state.incomplete_scope.iter().any(|scope| {
        scope.relative_path == relative_path && scope.reason == ScanIncompleteReason::Interrupted
    }) {
        return;
    }
    state.incomplete_scope.push(IncompleteScope {
        relative_path,
        reason: ScanIncompleteReason::Interrupted,
        detail: detail.to_string(),
    });
}

fn revalidate_root_path(root: &Path, state: &mut ScanState<'_>) {
    let path_snapshot = fs::symlink_metadata(root)
        .ok()
        .filter(|metadata| !metadata.file_type().is_symlink())
        .map(|metadata| crate::process::FilesystemObjectSnapshot::from_metadata(&metadata));
    let reopened_snapshot = crate::process::open_bounded_root(root)
        .and_then(|handle| handle.metadata())
        .ok()
        .map(|metadata| crate::process::FilesystemObjectSnapshot::from_metadata(&metadata));
    let descriptor_snapshot = state
        .root_handle
        .metadata()
        .ok()
        .map(|metadata| crate::process::FilesystemObjectSnapshot::from_metadata(&metadata));
    if path_snapshot.as_ref() != Some(&state.root_snapshot)
        || reopened_snapshot.as_ref() != Some(&state.root_snapshot)
        || descriptor_snapshot.as_ref() != Some(&state.root_snapshot)
    {
        record_changed_directory(
            root,
            state,
            "project root identity or contents changed during the scan",
        );
    }
}

fn observe_document(
    path: &Path,
    relative_path: String,
    path_metadata: fs::Metadata,
    media: MediaSpec,
    state: &mut ScanState<'_>,
) {
    let mut file = match crate::process::open_bounded_regular_file(
        &state.root_handle,
        Path::new(&relative_path),
    ) {
        Ok(file) => file,
        Err(error) => {
            state.incomplete_scope.push(IncompleteScope {
                relative_path,
                reason: bounded_open_reason(&error),
                detail: format!("bounded no-follow file open failed: {error}"),
            });
            return;
        }
    };
    let metadata = match file.metadata() {
        Ok(metadata) if metadata.is_file() => metadata,
        Ok(_) => {
            state.incomplete_scope.push(IncompleteScope {
                relative_path,
                reason: ScanIncompleteReason::UnresolvedBoundary,
                detail: "bounded descriptor was not a regular file".to_string(),
            });
            return;
        }
        Err(error) => {
            state.incomplete_scope.push(IncompleteScope {
                relative_path,
                reason: incomplete_reason(&error),
                detail: format!("bounded descriptor metadata could not be read: {error}"),
            });
            return;
        }
    };
    if !same_filesystem_object(&path_metadata, &metadata) {
        state.incomplete_scope.push(IncompleteScope {
            relative_path,
            reason: ScanIncompleteReason::UnresolvedBoundary,
            detail: "path identity changed before the bounded descriptor was opened".to_string(),
        });
        return;
    }

    state.usage.files_observed += 1;
    let size_bytes = metadata.len();
    let initial_modified = metadata.modified().ok();
    let modified_at = initial_modified.map(DateTime::<Utc>::from);
    let mut content_sha256 = None;
    let mut body = None;
    let mut extraction_error = false;
    let mut findings = Vec::new();

    if size_bytes > state.budgets.max_file_bytes {
        record_limit(state, ScanLimit::PerFileBytes);
        state.incomplete_scope.push(IncompleteScope {
            relative_path: relative_path.clone(),
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: format!(
                "document size {size_bytes} exceeds per-file budget {}",
                state.budgets.max_file_bytes
            ),
        });
        findings.push(observed_finding(
            "per_file_budget_reached",
            "metadata was observed but bytes were not read because the per-file budget was reached",
        ));
    } else if state.usage.bytes_hashed.saturating_add(size_bytes) > state.budgets.max_total_bytes {
        record_limit(state, ScanLimit::TotalBytes);
        state.incomplete_scope.push(IncompleteScope {
            relative_path: relative_path.clone(),
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: "total byte budget was reached before document bytes were read".to_string(),
        });
        findings.push(observed_finding(
            "total_byte_budget_reached",
            "metadata was observed but bytes were not read because the total byte budget was reached",
        ));
    } else {
        let remaining_total_bytes = state
            .budgets
            .max_total_bytes
            .saturating_sub(state.usage.bytes_hashed);
        let read_limit = state.budgets.max_file_bytes.min(remaining_total_bytes);
        let mut bytes = Vec::new();
        let read_result = read_bounded_descriptor(&mut file, read_limit, &mut bytes);
        let observed_bytes_sha256 = sha256_hex(&bytes);
        state.usage.bytes_hashed += bytes.len() as u64;
        state.usage.largest_file_bytes_read =
            state.usage.largest_file_bytes_read.max(bytes.len() as u64);
        match read_result {
            Ok(_) => {
                let final_descriptor_metadata = file.metadata().ok();
                let final_path_metadata = fs::symlink_metadata(path).ok();
                let stable = final_descriptor_metadata
                    .as_ref()
                    .zip(final_path_metadata.as_ref())
                    .is_some_and(|(final_descriptor, final_path)| {
                        descriptor_snapshot_stable(
                            &metadata,
                            final_descriptor,
                            final_path,
                            bytes.len() as u64,
                        )
                    });
                if stable {
                    content_sha256 = Some(observed_bytes_sha256);
                    if media.body_searchable {
                        match String::from_utf8(bytes) {
                            Ok(text) => {
                                state.usage.bytes_extracted += text.len() as u64;
                                body = Some(text);
                            }
                            Err(_) => {
                                extraction_error = true;
                                findings.push(observed_finding(
                                    "text_decode_failed",
                                    "document bytes were hashed but were not valid UTF-8 searchable text",
                                ));
                            }
                        }
                    }
                } else {
                    let final_size = final_descriptor_metadata
                        .as_ref()
                        .map(fs::Metadata::len)
                        .unwrap_or(size_bytes);
                    let mut reason = ScanIncompleteReason::Interrupted;
                    if final_size > state.budgets.max_file_bytes {
                        record_limit(state, ScanLimit::PerFileBytes);
                        reason = ScanIncompleteReason::BudgetTruncation;
                    }
                    if final_size > remaining_total_bytes {
                        record_limit(state, ScanLimit::TotalBytes);
                        reason = ScanIncompleteReason::BudgetTruncation;
                    }
                    state.incomplete_scope.push(IncompleteScope {
                        relative_path: relative_path.clone(),
                        reason,
                        detail:
                            "file identity, size, or modification time changed during the bounded read"
                                .to_string(),
                    });
                    findings.push(observed_finding(
                        "document_changed_during_scan",
                        "document bytes were bounded but not accepted because the file changed during the scan",
                    ));
                }
            }
            Err(error) => {
                state.incomplete_scope.push(IncompleteScope {
                    relative_path: relative_path.clone(),
                    reason: incomplete_reason(&error),
                    detail: format!("document bytes could not be read: {error}"),
                });
                findings.push(observed_finding(
                    "document_read_failed",
                    "metadata was observed but document bytes could not be read",
                ));
            }
        }
    }

    let observation_id = observation_id(
        state.root_id,
        &relative_path,
        size_bytes,
        modified_at.as_ref(),
        content_sha256.as_deref(),
    );
    let (role, role_authority) = filename_inferred_role(&relative_path);
    let entry = DocumentCatalogEntry {
        observation_id,
        document_id: None,
        artifact_id: None,
        project_key: state.project_key.to_string(),
        root_id: state.root_id.to_string(),
        relative_path: relative_path.clone(),
        media_family: media.family,
        media_type: media.media_type.to_string(),
        size_bytes,
        modified_at,
        content_sha256,
        git_state: state
            .git
            .as_ref()
            .map(|git| git.state(path))
            .unwrap_or(GitState::OutsideGit),
        role,
        role_authority,
        subject_key: None,
        subject_authority: None,
        audience: Vec::new(),
        scope_ref: None,
        owner_entrypoint_ref: None,
        activity_state: ActivityState::Unknown,
        activity_authority: None,
        retention_disposition: RetentionDisposition::Unclassified,
        retention_authority: None,
        generated: None,
        generated_authority: None,
        rebuild_ref: None,
        entrypoint_refs: Vec::new(),
        outbound_refs: Vec::new(),
        inbound_ref_count: 0,
        exact_duplicate_refs: Vec::new(),
        possible_predecessor_refs: Vec::new(),
        review_after: None,
        scan_findings: findings,
    };
    state.documents.push(ScannedDocument {
        entry,
        body,
        extraction_error,
    });
}

fn record_remaining_scope(state: &mut ScanState<'_>, paths: &[PathBuf], detail: &str) {
    for path in paths {
        let relative_path = relative_path(state.root, path);
        if state.incomplete_scope.iter().any(|scope| {
            scope.relative_path == relative_path
                && scope.reason == ScanIncompleteReason::BudgetTruncation
        }) {
            continue;
        }
        state.incomplete_scope.push(IncompleteScope {
            relative_path,
            reason: ScanIncompleteReason::BudgetTruncation,
            detail: detail.to_string(),
        });
    }
}

fn read_bounded_descriptor(
    file: &mut fs::File,
    limit: u64,
    bytes: &mut Vec<u8>,
) -> std::io::Result<usize> {
    file.take(limit).read_to_end(bytes)
}

fn descriptor_snapshot_stable(
    initial: &fs::Metadata,
    final_descriptor: &fs::Metadata,
    final_path: &fs::Metadata,
    bytes_read: u64,
) -> bool {
    initial.is_file()
        && final_descriptor.is_file()
        && !final_path.file_type().is_symlink()
        && same_filesystem_object(initial, final_descriptor)
        && same_filesystem_object(final_descriptor, final_path)
        && final_descriptor.len() == initial.len()
        && final_descriptor.modified().ok() == initial.modified().ok()
        && bytes_read == final_descriptor.len()
}

fn record_symlink_exclusion(path: &Path, relative: &str, state: &mut ScanState<'_>) {
    let (reason, detail) = match path.canonicalize() {
        Ok(target) if !target.starts_with(state.root) => (
            ExclusionReason::SymlinkEscape,
            "symlink target resolves outside the bounded project root".to_string(),
        ),
        Ok(_) => (
            ExclusionReason::SpecialFile,
            "symlink was not followed during the read-only scan".to_string(),
        ),
        Err(error) => (
            ExclusionReason::SpecialFile,
            format!("unresolved symlink was not followed: {error}"),
        ),
    };
    state.excluded_scope.push(ExcludedScope {
        relative_path: relative.to_string(),
        reason,
        detail,
    });
}

fn excluded_directory(path: &Path, root: &Path) -> Option<(ExclusionReason, String)> {
    let name = path.file_name()?.to_string_lossy().to_ascii_lowercase();
    let fixed = match name.as_str() {
        ".git" => Some((
            ExclusionReason::VcsMetadata,
            "Git metadata is outside the document body scan".to_string(),
        )),
        "node_modules" | "vendor" => Some((
            ExclusionReason::DependencyCache,
            "dependency tree is excluded from the project document catalog".to_string(),
        )),
        ".cache" => Some((
            ExclusionReason::PackageCache,
            "package cache is excluded from the project document catalog".to_string(),
        )),
        "target" | "build" | "dist" | "out" | ".next" => Some((
            ExclusionReason::BuildOutput,
            "build output is excluded from the authored document catalog".to_string(),
        )),
        ".venv" | "venv" => Some((
            ExclusionReason::VirtualEnvironment,
            "virtual environment is excluded from the project document catalog".to_string(),
        )),
        ".forager" => Some((
            ExclusionReason::ConfiguredDataRoot,
            "Forager project-local state is not part of the document catalog".to_string(),
        )),
        _ => None,
    };
    if fixed.is_some() {
        return fixed;
    }
    if path != root && path.join(".git").exists() {
        return Some((
            ExclusionReason::NestedRepository,
            "nested repository is outside this single-root project scan".to_string(),
        ));
    }
    None
}

fn media_spec(path: &Path) -> Option<MediaSpec> {
    let extension = path.extension()?.to_string_lossy().to_ascii_lowercase();
    match extension.as_str() {
        "md" | "markdown" => Some(MediaSpec {
            family: MediaFamily::Markdown,
            media_type: "text/markdown",
            body_searchable: true,
        }),
        "txt" => Some(MediaSpec {
            family: MediaFamily::PlainText,
            media_type: "text/plain",
            body_searchable: true,
        }),
        "rst" => Some(MediaSpec {
            family: MediaFamily::RestructuredText,
            media_type: "text/x-rst",
            body_searchable: true,
        }),
        "adoc" | "asciidoc" => Some(MediaSpec {
            family: MediaFamily::Asciidoc,
            media_type: "text/asciidoc",
            body_searchable: true,
        }),
        "org" => Some(MediaSpec {
            family: MediaFamily::Org,
            media_type: "text/org",
            body_searchable: true,
        }),
        "pdf" => Some(MediaSpec {
            family: MediaFamily::Pdf,
            media_type: "application/pdf",
            body_searchable: false,
        }),
        "docx" => Some(MediaSpec {
            family: MediaFamily::Docx,
            media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            body_searchable: false,
        }),
        "pptx" => Some(MediaSpec {
            family: MediaFamily::Pptx,
            media_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            body_searchable: false,
        }),
        "odt" => Some(MediaSpec {
            family: MediaFamily::Odt,
            media_type: "application/vnd.oasis.opendocument.text",
            body_searchable: false,
        }),
        "html" | "htm" => Some(MediaSpec {
            family: MediaFamily::Html,
            media_type: "text/html",
            body_searchable: false,
        }),
        _ => None,
    }
}

fn filename_inferred_role(relative_path: &str) -> (DocumentRole, Option<ClassificationAuthority>) {
    let name = Path::new(relative_path)
        .file_name()
        .map(|name| name.to_string_lossy().to_ascii_uppercase())
        .unwrap_or_default();
    let role = match name.as_str() {
        "README.MD" | "README.MARKDOWN" | "AGENTS.MD" => DocumentRole::Entrypoint,
        "PROJECT_STATE.MD" | "CURRENT_STATE.MD" | "STATUS.MD" => DocumentRole::CurrentState,
        "NEXT_ACTIONS.MD" | "TODO.MD" => DocumentRole::NextActions,
        "DECISIONS.MD" | "DECISION_LOG.MD" => DocumentRole::Decision,
        "RUNBOOK.MD" | "OPERATIONS.MD" => DocumentRole::Runbook,
        "DELIVERABLES.MD" => DocumentRole::Deliverable,
        _ => DocumentRole::Unknown,
    };
    let authority = (role != DocumentRole::Unknown).then_some(ClassificationAuthority::Inferred);
    (role, authority)
}

fn link_markdown_documents(documents: &mut [ScannedDocument]) {
    let path_to_observation: BTreeMap<String, String> = documents
        .iter()
        .map(|document| {
            (
                document.entry.relative_path.clone(),
                document.entry.observation_id.clone(),
            )
        })
        .collect();
    let entrypoint_ids: BTreeSet<String> = documents
        .iter()
        .filter(|document| {
            matches!(
                document.entry.role,
                DocumentRole::Entrypoint | DocumentRole::CurrentState | DocumentRole::NextActions
            )
        })
        .map(|document| document.entry.observation_id.clone())
        .collect();
    let mut inbound = BTreeMap::<String, u64>::new();
    let mut entrypoint_refs = BTreeMap::<String, Vec<IdentityReference>>::new();

    for document in documents.iter_mut() {
        if document.entry.media_family != MediaFamily::Markdown {
            continue;
        }
        let Some(body) = document.body.as_deref() else {
            continue;
        };
        let mut references = BTreeMap::new();
        for target in markdown_targets(body) {
            let reference = match normalize_markdown_target(&document.entry.relative_path, &target)
            {
                Some(relative_target) => path_to_observation
                    .get(&relative_target)
                    .map(|observation_id| IdentityReference {
                        kind: IdentityReferenceKind::Observation,
                        value: observation_id.clone(),
                    })
                    .unwrap_or(IdentityReference {
                        kind: IdentityReferenceKind::RelativePath,
                        value: relative_target,
                    }),
                None => IdentityReference {
                    kind: IdentityReferenceKind::External,
                    value: target,
                },
            };
            let reference_key = reference_sort_key(&reference);
            if !references.contains_key(&reference_key)
                && reference.kind == IdentityReferenceKind::Observation
            {
                *inbound.entry(reference.value.clone()).or_default() += 1;
                if entrypoint_ids.contains(&document.entry.observation_id) {
                    entrypoint_refs
                        .entry(reference.value.clone())
                        .or_default()
                        .push(IdentityReference {
                            kind: IdentityReferenceKind::Observation,
                            value: document.entry.observation_id.clone(),
                        });
                }
            }
            references.insert(reference_key, reference);
        }
        document.entry.outbound_refs = references.into_values().collect();
    }

    for document in documents {
        document.entry.inbound_ref_count = inbound
            .get(&document.entry.observation_id)
            .copied()
            .unwrap_or_default();
        if let Some(mut refs) = entrypoint_refs.remove(&document.entry.observation_id) {
            refs.sort_by_key(reference_sort_key);
            refs.dedup();
            document.entry.owner_entrypoint_ref = refs.first().cloned();
            document.entry.entrypoint_refs = refs;
        }
    }
}

fn link_exact_duplicates(documents: &mut [ScannedDocument]) {
    let mut groups = BTreeMap::<String, Vec<(String, String)>>::new();
    for document in documents.iter() {
        if let Some(hash) = &document.entry.content_sha256 {
            groups.entry(hash.clone()).or_default().push((
                document.entry.relative_path.clone(),
                document.entry.observation_id.clone(),
            ));
        }
    }
    let duplicates: BTreeMap<String, Vec<IdentityReference>> = groups
        .into_values()
        .filter(|group| group.len() > 1)
        .flat_map(|mut group| {
            group.sort();
            group
                .iter()
                .map(|(_, observation_id)| {
                    let refs = group
                        .iter()
                        .filter(|(_, candidate)| candidate != observation_id)
                        .map(|(_, candidate)| IdentityReference {
                            kind: IdentityReferenceKind::Observation,
                            value: candidate.clone(),
                        })
                        .collect();
                    (observation_id.clone(), refs)
                })
                .collect::<Vec<_>>()
        })
        .collect();
    for document in documents {
        document.entry.exact_duplicate_refs = duplicates
            .get(&document.entry.observation_id)
            .cloned()
            .unwrap_or_default();
    }
}

fn markdown_targets(body: &str) -> Vec<String> {
    static INLINE_LINK: OnceLock<Regex> = OnceLock::new();
    static REFERENCE_LINK: OnceLock<Regex> = OnceLock::new();
    let inline = INLINE_LINK.get_or_init(|| {
        Regex::new(r"!?\[[^\]]*\]\((?P<target>[^)]+)\)")
            .expect("inline Markdown link regex is valid")
    });
    let reference = REFERENCE_LINK.get_or_init(|| {
        Regex::new(r"(?m)^\s*\[[^\]]+\]:\s*(?P<target>\S+)")
            .expect("reference Markdown link regex is valid")
    });
    inline
        .captures_iter(body)
        .chain(reference.captures_iter(body))
        .filter_map(|capture| capture.name("target"))
        .map(|target| {
            target
                .as_str()
                .trim()
                .trim_matches(['<', '>'])
                .split_whitespace()
                .next()
                .unwrap_or_default()
                .to_string()
        })
        .filter(|target| !target.is_empty() && !target.starts_with('#'))
        .collect()
}

fn normalize_markdown_target(source_path: &str, target: &str) -> Option<String> {
    let target = target.split(['#', '?']).next()?.trim();
    if target.is_empty()
        || target.starts_with('/')
        || target.starts_with("//")
        || target.contains("://")
        || target.starts_with("mailto:")
    {
        return None;
    }
    let mut components = Vec::<String>::new();
    if let Some(parent) = Path::new(source_path).parent() {
        for component in parent.components() {
            if let Component::Normal(value) = component {
                components.push(value.to_string_lossy().to_string());
            }
        }
    }
    for component in Path::new(target).components() {
        match component {
            Component::CurDir => {}
            Component::Normal(value) => components.push(value.to_string_lossy().to_string()),
            Component::ParentDir => {
                components.pop()?;
            }
            Component::RootDir | Component::Prefix(_) => return None,
        }
    }
    (!components.is_empty()).then(|| components.join("/"))
}

fn media_coverage(documents: &[ScannedDocument]) -> Vec<MediaCoverage> {
    let mut rows = BTreeMap::<MediaFamily, MediaCoverage>::new();
    for document in documents {
        let searchable = is_body_searchable(document.entry.media_family);
        let row = rows
            .entry(document.entry.media_family)
            .or_insert(MediaCoverage {
                media_family: document.entry.media_family,
                observed_files: 0,
                metadata_complete_files: 0,
                body_searchable_files: 0,
                body_extracted_files: 0,
                extraction_errors: 0,
                body_coverage: if searchable {
                    BodyCoverage::Complete
                } else {
                    BodyCoverage::MetadataOnly
                },
            });
        row.observed_files += 1;
        row.metadata_complete_files += 1;
        if searchable {
            row.body_searchable_files += 1;
            if document.body.is_some() {
                row.body_extracted_files += 1;
            }
            if document.extraction_error {
                row.extraction_errors += 1;
            }
        }
    }
    for row in rows.values_mut() {
        if row.body_searchable_files > row.body_extracted_files {
            row.body_coverage = BodyCoverage::Partial;
        }
    }
    rows.into_values().collect()
}

fn filesystem_snapshot_sha256(state: &ScanState<'_>) -> String {
    let mut hasher = Sha256::new();
    hasher.update(state.root_id.as_bytes());
    hasher.update([0]);
    for document in &state.documents {
        let entry = &document.entry;
        hasher.update(entry.relative_path.as_bytes());
        hasher.update([0]);
        hasher.update(entry.size_bytes.to_le_bytes());
        hasher.update([0]);
        if let Some(modified_at) = entry.modified_at {
            hasher.update(modified_at.to_rfc3339().as_bytes());
        }
        hasher.update([0]);
        if let Some(content_sha256) = &entry.content_sha256 {
            hasher.update(content_sha256.as_bytes());
        }
        hasher.update([0]);
        hasher.update(git_state_name(entry.git_state).as_bytes());
        hasher.update([0]);
    }
    for excluded in &state.excluded_scope {
        hasher.update(excluded.relative_path.as_bytes());
        hasher.update(format!("{:?}", excluded.reason).as_bytes());
        hasher.update([0]);
    }
    for incomplete in &state.incomplete_scope {
        hasher.update(incomplete.relative_path.as_bytes());
        hasher.update(format!("{:?}", incomplete.reason).as_bytes());
        hasher.update([0]);
    }
    format!("{:x}", hasher.finalize())
}

fn negative_search_evidence(catalog: &DocumentCatalogV1) -> NegativeSearchEvidence {
    let metadata_only_families: Vec<_> = catalog
        .coverage
        .iter()
        .filter(|coverage| coverage.observed_files > 0)
        .filter(|coverage| {
            matches!(
                coverage.body_coverage,
                BodyCoverage::MetadataOnly | BodyCoverage::Unsupported | BodyCoverage::Partial
            )
        })
        .map(|coverage| coverage.media_family)
        .collect();
    let complete_body_coverage = catalog
        .coverage
        .iter()
        .all(|coverage| coverage.body_coverage == BodyCoverage::Complete);
    let absence_established =
        catalog.scan.complete && complete_body_coverage && metadata_only_families.is_empty();
    let reason = if !catalog.scan.complete {
        "scan is incomplete, so no negative owner or content conclusion is authorized"
    } else if !metadata_only_families.is_empty() {
        "one or more observed media families lack complete body extraction"
    } else {
        "the live bounded scan completed with full body coverage for every observed document"
    };
    NegativeSearchEvidence {
        absence_established,
        reason: reason.to_string(),
        metadata_only_families,
    }
}

fn search_envelope(
    catalog: DocumentCatalogV1,
    mode: DocumentSearchMode,
    query: Option<String>,
    observation_id: Option<String>,
    matches: Vec<DocumentSearchMatch>,
    negative_evidence: Option<NegativeSearchEvidence>,
) -> DocumentSearchV1 {
    let generated_at = Utc::now();
    DocumentSearchV1 {
        schema: DOCUMENT_SEARCH_SCHEMA.to_string(),
        generated_at,
        mode,
        query,
        observation_id,
        project_key: catalog.project_key.clone(),
        root: catalog.roots[0].clone(),
        filesystem_snapshot: catalog.source_identities.filesystem_snapshot.clone(),
        freshness: SearchFreshness {
            mode: "live_bounded_scan".to_string(),
            catalog_generated_at: catalog.generated_at,
            stale: false,
        },
        scan: catalog.scan,
        coverage: catalog.coverage,
        authority_boundary: SearchAuthorityBoundary {
            visibility_neutral: true,
            lifecycle_filtering_applied: false,
            negative_owner_conclusion_authorized: false,
            search_visibility_mutation_authorized: false,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
        matches,
        negative_evidence,
    }
}

fn matching_snippets(body: &str, normalized_query: &str) -> Vec<String> {
    super::reviewed::matching_snippets_v2(body, normalized_query)
}

pub(crate) fn observation_id(
    root_id: &str,
    relative_path: &str,
    size_bytes: u64,
    modified_at: Option<&DateTime<Utc>>,
    content_sha256: Option<&str>,
) -> String {
    let identity = format!(
        "{root_id}\0{relative_path}\0{size_bytes}\0{}\0{}",
        modified_at
            .map(|value| value.to_rfc3339())
            .unwrap_or_default(),
        content_sha256.unwrap_or("unread")
    );
    let digest = sha256_hex(identity.as_bytes());
    format!("doc_observation_{}", &digest[..24])
}

fn relative_path(root: &Path, path: &Path) -> String {
    let relative = path.strip_prefix(root).unwrap_or(path);
    let components: Vec<_> = relative
        .components()
        .filter_map(|component| match component {
            Component::Normal(value) => Some(value.to_string_lossy().to_string()),
            _ => None,
        })
        .collect();
    if components.is_empty() {
        ".".to_string()
    } else {
        components.join("/")
    }
}

fn incomplete_reason(error: &std::io::Error) -> ScanIncompleteReason {
    if error.kind() == std::io::ErrorKind::PermissionDenied {
        ScanIncompleteReason::PermissionDenied
    } else {
        ScanIncompleteReason::IoError
    }
}

fn bounded_open_reason(error: &std::io::Error) -> ScanIncompleteReason {
    if error.kind() == std::io::ErrorKind::PermissionDenied {
        ScanIncompleteReason::PermissionDenied
    } else {
        ScanIncompleteReason::UnresolvedBoundary
    }
}

fn same_filesystem_object(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    crate::process::filesystem_file_identity(left)
        .zip(crate::process::filesystem_file_identity(right))
        .is_some_and(|(left, right)| left == right)
}

fn observed_finding(code: &str, message: &str) -> CatalogFinding {
    CatalogFinding {
        code: code.to_string(),
        severity: HygieneSeverity::Info,
        authority: Some(ClassificationAuthority::Observed),
        message: message.to_string(),
    }
}

fn record_limit(state: &mut ScanState<'_>, limit: ScanLimit) {
    if !state.reached_limits.contains(&limit) {
        state.reached_limits.push(limit);
    }
}

fn is_body_searchable(family: MediaFamily) -> bool {
    matches!(
        family,
        MediaFamily::Markdown
            | MediaFamily::PlainText
            | MediaFamily::RestructuredText
            | MediaFamily::Asciidoc
            | MediaFamily::Org
    )
}

fn reference_sort_key(reference: &IdentityReference) -> (u8, String) {
    let rank = match reference.kind {
        IdentityReferenceKind::Observation => 0,
        IdentityReferenceKind::Document => 1,
        IdentityReferenceKind::Artifact => 2,
        IdentityReferenceKind::RelativePath => 3,
        IdentityReferenceKind::External => 4,
    };
    (rank, reference.value.clone())
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn git_state_name(state: GitState) -> &'static str {
    match state {
        GitState::Tracked => "tracked",
        GitState::Untracked => "untracked",
        GitState::Ignored => "ignored",
        GitState::OutsideGit => "outside_git",
        GitState::Unknown => "unknown",
    }
}

impl GitContext {
    fn open(root: &Path) -> Option<Self> {
        let repository = Repository::discover(root).ok()?;
        let workdir = repository.workdir()?.canonicalize().ok()?;
        if !root.starts_with(&workdir) {
            return None;
        }
        Some(Self {
            repository,
            workdir,
        })
    }

    fn state(&self, path: &Path) -> GitState {
        let Ok(relative) = path.strip_prefix(&self.workdir) else {
            return GitState::OutsideGit;
        };
        let Ok(status) = self.repository.status_file(relative) else {
            return GitState::Unknown;
        };
        if status.contains(Status::IGNORED) {
            GitState::Ignored
        } else if status.contains(Status::WT_NEW) && !status.contains(Status::INDEX_NEW) {
            GitState::Untracked
        } else {
            GitState::Tracked
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn growing_descriptor_is_bounded_and_rejected_as_a_stable_snapshot() {
        let temp = tempfile::tempdir().expect("tempdir");
        let path = temp.path().join("growing.md");
        fs::write(&path, "one").expect("initial document");
        let mut descriptor = fs::File::open(&path).expect("document descriptor");
        let initial = descriptor.metadata().expect("initial metadata");

        fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .expect("append descriptor")
            .write_all(b"-expanded-beyond-budget")
            .expect("grow document");

        let mut bytes = Vec::new();
        read_bounded_descriptor(&mut descriptor, 4, &mut bytes).expect("bounded read");
        let final_descriptor = descriptor.metadata().expect("final descriptor metadata");
        let final_path = fs::symlink_metadata(&path).expect("final path metadata");
        assert_eq!(bytes.len(), 4);
        assert!(final_descriptor.len() > bytes.len() as u64);
        assert!(!descriptor_snapshot_stable(
            &initial,
            &final_descriptor,
            &final_path,
            bytes.len() as u64
        ));
    }

    #[test]
    fn replaced_directory_cannot_produce_a_complete_catalog() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path().join("project");
        fs::create_dir_all(root.join("docs")).expect("docs");
        fs::write(root.join("docs/a.md"), "old").expect("old document");
        let mut replaced = false;
        let live =
            build_live_catalog_with_hook(&root, &CatalogOptions::default(), &mut |directory| {
                if !replaced && directory.ends_with("docs") {
                    fs::rename(root.join("docs"), root.join("docs-old"))
                        .expect("move enumerated directory");
                    fs::create_dir(root.join("docs")).expect("replacement directory");
                    fs::write(root.join("docs/b.md"), "new").expect("replacement document");
                    replaced = true;
                }
            })
            .expect("catalog projection");
        assert!(!live.catalog.scan.complete);
        assert!(live.catalog.scan.incomplete_scope.iter().any(|scope| {
            scope.relative_path == "docs" && scope.reason == ScanIncompleteReason::Interrupted
        }));
    }

    #[test]
    fn replaced_root_cannot_produce_a_complete_catalog() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path().join("project");
        fs::create_dir_all(&root).expect("root");
        fs::write(root.join("a.md"), "old").expect("old document");
        let mut replaced = false;
        let live =
            build_live_catalog_with_hook(&root, &CatalogOptions::default(), &mut |directory| {
                if !replaced && directory == root {
                    fs::rename(&root, temp.path().join("project-old"))
                        .expect("move enumerated root");
                    fs::create_dir(&root).expect("replacement root");
                    fs::write(root.join("b.md"), "new").expect("replacement document");
                    replaced = true;
                }
            })
            .expect("catalog projection");
        assert!(!live.catalog.scan.complete);
        assert!(live.catalog.scan.incomplete_scope.iter().any(|scope| {
            scope.relative_path == "." && scope.reason == ScanIncompleteReason::Interrupted
        }));
    }

    #[test]
    fn lifecycle_change_between_catalog_reads_is_global_fail_open() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path().join("project");
        let profile = temp.path().join("profile");
        fs::create_dir(&root).expect("project root");
        fs::create_dir(&profile).expect("profile root");
        #[cfg(unix)]
        fs::set_permissions(&profile, fs::Permissions::from_mode(0o700))
            .expect("profile permissions");
        fs::write(root.join("a.md"), "current").expect("document");

        let catalog = scan_project_with_lifecycle_hook(
            &profile,
            &root,
            &CatalogOptions {
                project_key: Some("phase2d-race".to_string()),
                ..CatalogOptions::default()
            },
            || {
                let lifecycle = profile.join("document-lifecycle");
                let version = lifecycle.join("v1");
                let events = version.join("events");
                fs::create_dir(&lifecycle).expect("lifecycle directory");
                fs::create_dir(&version).expect("version directory");
                fs::create_dir(&events).expect("events directory");
                #[cfg(unix)]
                for directory in [&lifecycle, &version, &events] {
                    fs::set_permissions(directory, fs::Permissions::from_mode(0o700))
                        .expect("lifecycle permissions");
                }
                fs::write(events.join(".interrupted.partial"), "partial").expect("partial event");
            },
        )
        .expect("fail-open catalog");

        assert_eq!(
            catalog.lifecycle.status,
            LifecycleProjectionStatusV1::IdentityMismatch
        );
        assert_eq!(
            catalog.lifecycle.failure_code.as_deref(),
            Some("lifecycle_or_root_changed_during_catalog_scan")
        );
        assert!(catalog.entries.iter().all(|entry| {
            entry.document_id.is_none()
                && entry.lifecycle_stale
                && entry.visibility == SearchVisibilityV2::VisibleFailOpen
        }));
    }

    #[test]
    fn profile_root_replacement_during_catalog_scan_is_global_fail_open() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path().join("project");
        let profile = temp.path().join("profile");
        let old_profile = temp.path().join("profile-old");
        fs::create_dir(&root).expect("project root");
        fs::create_dir(&profile).expect("profile root");
        #[cfg(unix)]
        fs::set_permissions(&profile, fs::Permissions::from_mode(0o700))
            .expect("profile permissions");
        fs::write(root.join("a.md"), "current").expect("document");

        let catalog = scan_project_with_lifecycle_hook(
            &profile,
            &root,
            &CatalogOptions {
                project_key: Some("phase2f-profile-race".to_string()),
                ..CatalogOptions::default()
            },
            || {
                fs::rename(&profile, &old_profile).expect("move old profile root");
                fs::create_dir(&profile).expect("replacement profile root");
                #[cfg(unix)]
                fs::set_permissions(&profile, fs::Permissions::from_mode(0o700))
                    .expect("replacement profile permissions");
            },
        )
        .expect("fail-open catalog");

        assert_eq!(
            catalog.lifecycle.status,
            LifecycleProjectionStatusV1::Corrupt
        );
        assert_eq!(
            catalog.lifecycle.failure_code.as_deref(),
            Some("retention_application_integrity_error")
        );
        assert!(!catalog.lifecycle.filtering_authorized);
        assert!(catalog
            .entries
            .iter()
            .all(|entry| entry.visibility == SearchVisibilityV2::VisibleFailOpen));
    }

    #[test]
    fn project_root_replacement_between_lifecycle_and_catalog_is_fail_open() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path().join("project");
        let old_root = temp.path().join("project-old");
        let missing_profile = temp.path().join("missing-profile");
        fs::create_dir(&root).expect("project root");
        fs::write(root.join("a.md"), "old").expect("old document");

        let catalog = scan_project_with_lifecycle_hook(
            &missing_profile,
            &root,
            &CatalogOptions {
                project_key: Some("phase2d-root-race".to_string()),
                ..CatalogOptions::default()
            },
            || {
                fs::rename(&root, &old_root).expect("detach old root");
                fs::create_dir(&root).expect("replacement root");
                fs::write(root.join("b.md"), "new").expect("new document");
            },
        )
        .expect("fail-open catalog");

        assert_eq!(
            catalog.lifecycle.status,
            LifecycleProjectionStatusV1::IdentityMismatch
        );
        assert!(!catalog.lifecycle.filtering_authorized);
        assert!(catalog
            .entries
            .iter()
            .all(|entry| entry.visibility == SearchVisibilityV2::VisibleFailOpen));
    }

    #[test]
    fn long_body_snippet_keeps_the_matching_query_in_its_bounded_window() {
        let line = format!("{}needle{}", "a".repeat(400), "b".repeat(400));
        let snippets = matching_snippets(&line, "needle");
        assert_eq!(snippets.len(), 1);
        assert!(snippets[0].contains("needle"));
        assert!(snippets[0].chars().count() <= 240);
    }
}
