//! Versioned contracts for document lifecycle governance.
//!
//! Phase 0 freezes the wire vocabulary, Phase 1 adds one bounded project's
//! visibility-neutral catalog, Phase 2C appends approval-bound lifecycle
//! events, and Phase 2D joins one exact ledger snapshot into a fail-open catalog
//! projection. Phase 2E derives reviewed cold visibility in a read-only live
//! search. No lifecycle command changes project files.

mod catalog;
mod lifecycle;
mod reviewed;

pub(crate) use catalog::observation_id;
pub use catalog::*;
pub(crate) use lifecycle::root_instance_identity_sha256;
pub use lifecycle::*;
pub use reviewed::*;

use anyhow::{bail, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path};

pub const DOCUMENT_HYGIENE_CONTRACT_SCHEMA: &str = "document_hygiene_contract.v1";
pub const DOCUMENT_CATALOG_SCHEMA: &str = "document_catalog.v1";
pub const DOCUMENT_HYGIENE_REPORT_SCHEMA: &str = "document_hygiene_report.v1";
pub const DOCUMENT_IDENTITY_CROSSWALK_SCHEMA: &str = "document_identity_crosswalk.v1";
pub const FILESYSTEM_OBSERVATION_SOURCE_SCHEMA: &str = "filesystem_observation.v1";
pub const DOCUMENT_LIFECYCLE_LEDGER_SOURCE_SCHEMA: &str = "document_lifecycle_ledger.v1";
pub const ARTIFACT_INDEX_SOURCE_SCHEMA: &str = "artifact_index.v1";
pub const DOCUMENT_HYGIENE_CONTRACT_SHA256: &str =
    "6566cd49d7942d67013c3ec5b2e18d4b9a0407b6c88346aed76f79ebd8c7961c";
pub const DOCUMENT_HYGIENE_CONTRACT_PROMPT: &str = include_str!("document_hygiene_contract_v1.txt");

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentRole {
    Entrypoint,
    CurrentState,
    NextActions,
    Decision,
    Runbook,
    Reference,
    Evidence,
    Review,
    Log,
    Deliverable,
    FailureRecord,
    ArchiveRecord,
    WorkingNote,
    GeneratedProjection,
    Unknown,
}

pub const DOCUMENT_ROLES: &[DocumentRole] = &[
    DocumentRole::Entrypoint,
    DocumentRole::CurrentState,
    DocumentRole::NextActions,
    DocumentRole::Decision,
    DocumentRole::Runbook,
    DocumentRole::Reference,
    DocumentRole::Evidence,
    DocumentRole::Review,
    DocumentRole::Log,
    DocumentRole::Deliverable,
    DocumentRole::FailureRecord,
    DocumentRole::ArchiveRecord,
    DocumentRole::WorkingNote,
    DocumentRole::GeneratedProjection,
    DocumentRole::Unknown,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActivityState {
    Draft,
    Candidate,
    Active,
    ReviewDue,
    Superseded,
    Retired,
    Unknown,
}

pub const ACTIVITY_STATES: &[ActivityState] = &[
    ActivityState::Draft,
    ActivityState::Candidate,
    ActivityState::Active,
    ActivityState::ReviewDue,
    ActivityState::Superseded,
    ActivityState::Retired,
    ActivityState::Unknown,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetentionDisposition {
    Unclassified,
    Keep,
    Preserved,
    SummaryKept,
    RetirementCandidate,
    Archived,
    QuarantineCandidate,
    Quarantined,
    DisposalCandidate,
    Disposed,
}

pub const RETENTION_DISPOSITIONS: &[RetentionDisposition] = &[
    RetentionDisposition::Unclassified,
    RetentionDisposition::Keep,
    RetentionDisposition::Preserved,
    RetentionDisposition::SummaryKept,
    RetentionDisposition::RetirementCandidate,
    RetentionDisposition::Archived,
    RetentionDisposition::QuarantineCandidate,
    RetentionDisposition::Quarantined,
    RetentionDisposition::DisposalCandidate,
    RetentionDisposition::Disposed,
];

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GovernanceLifecycleCrosswalkEntry {
    pub legacy_value: String,
    pub activity_state: Option<ActivityState>,
    pub retention_disposition: Option<RetentionDisposition>,
}

pub fn governance_lifecycle_crosswalk_v1() -> Vec<GovernanceLifecycleCrosswalkEntry> {
    vec![
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "draft".to_string(),
            activity_state: Some(ActivityState::Draft),
            retention_disposition: None,
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "candidate".to_string(),
            activity_state: Some(ActivityState::Candidate),
            retention_disposition: None,
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "active".to_string(),
            activity_state: Some(ActivityState::Active),
            retention_disposition: None,
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "review_due".to_string(),
            activity_state: Some(ActivityState::ReviewDue),
            retention_disposition: None,
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "superseded".to_string(),
            activity_state: Some(ActivityState::Superseded),
            retention_disposition: None,
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "preserved".to_string(),
            activity_state: None,
            retention_disposition: Some(RetentionDisposition::Preserved),
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "summary_kept".to_string(),
            activity_state: None,
            retention_disposition: Some(RetentionDisposition::SummaryKept),
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "retirement_candidate".to_string(),
            activity_state: None,
            retention_disposition: Some(RetentionDisposition::RetirementCandidate),
        },
        GovernanceLifecycleCrosswalkEntry {
            legacy_value: "archived".to_string(),
            activity_state: None,
            retention_disposition: Some(RetentionDisposition::Archived),
        },
    ]
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClassificationAuthority {
    Observed,
    Declared,
    Inferred,
    Reviewed,
}

pub const CLASSIFICATION_AUTHORITIES: &[ClassificationAuthority] = &[
    ClassificationAuthority::Observed,
    ClassificationAuthority::Declared,
    ClassificationAuthority::Inferred,
    ClassificationAuthority::Reviewed,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentIntentKind {
    UpdateExisting,
    AppendRecord,
    CreateVersionedRecord,
    CreateNewDocument,
    NoDocument,
}

pub const DOCUMENT_INTENT_KINDS: &[DocumentIntentKind] = &[
    DocumentIntentKind::UpdateExisting,
    DocumentIntentKind::AppendRecord,
    DocumentIntentKind::CreateVersionedRecord,
    DocumentIntentKind::CreateNewDocument,
    DocumentIntentKind::NoDocument,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RootSourceKind {
    Explicit,
    ProjectRegistry,
}

pub const ROOT_SOURCE_KINDS: &[RootSourceKind] =
    &[RootSourceKind::Explicit, RootSourceKind::ProjectRegistry];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MediaFamily {
    Markdown,
    PlainText,
    RestructuredText,
    Asciidoc,
    Org,
    Pdf,
    Docx,
    Pptx,
    Odt,
    Html,
}

pub const MEDIA_FAMILIES: &[MediaFamily] = &[
    MediaFamily::Markdown,
    MediaFamily::PlainText,
    MediaFamily::RestructuredText,
    MediaFamily::Asciidoc,
    MediaFamily::Org,
    MediaFamily::Pdf,
    MediaFamily::Docx,
    MediaFamily::Pptx,
    MediaFamily::Odt,
    MediaFamily::Html,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExclusionReason {
    VcsMetadata,
    DependencyCache,
    PackageCache,
    BuildOutput,
    VirtualEnvironment,
    ConfiguredDataRoot,
    SpecialFile,
    SymlinkEscape,
    NestedRepository,
    Submodule,
    MountPoint,
    SeparatelyRegisteredRoot,
    UnsupportedMedia,
}

pub const EXCLUSION_REASONS: &[ExclusionReason] = &[
    ExclusionReason::VcsMetadata,
    ExclusionReason::DependencyCache,
    ExclusionReason::PackageCache,
    ExclusionReason::BuildOutput,
    ExclusionReason::VirtualEnvironment,
    ExclusionReason::ConfiguredDataRoot,
    ExclusionReason::SpecialFile,
    ExclusionReason::SymlinkEscape,
    ExclusionReason::NestedRepository,
    ExclusionReason::Submodule,
    ExclusionReason::MountPoint,
    ExclusionReason::SeparatelyRegisteredRoot,
    ExclusionReason::UnsupportedMedia,
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GitState {
    Tracked,
    Untracked,
    Ignored,
    OutsideGit,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScanLimit {
    FileCount,
    DirectoryEntries,
    TotalBytes,
    PerFileBytes,
    ElapsedTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScanIncompleteReason {
    BudgetTruncation,
    PermissionDenied,
    IoError,
    Interrupted,
    UnreadablePath,
    UnresolvedBoundary,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BodyCoverage {
    Complete,
    Partial,
    MetadataOnly,
    Unsupported,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityReferenceKind {
    Observation,
    Document,
    Artifact,
    RelativePath,
    External,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityPlane {
    FilesystemObservation,
    LifecycleLedger,
    ArtifactIndex,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IdentityConflictKind {
    PathMismatch,
    ContentHashMismatch,
    MultipleArtifactMatches,
    MultipleDocumentMatches,
    StaleLifecycleIdentity,
    MissingSourceIdentity,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CrosswalkStatus {
    CatalogOnly,
    Unified,
    Conflict,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetentionTargetSourceKind {
    Document,
    Artifact,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetentionResolutionStatus {
    Mint,
    Reuse,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentChangeKind {
    Created,
    Replaced,
    MateriallyRewritten,
    Unchanged,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HygieneSeverity {
    Error,
    Warn,
    Info,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HygieneFindingKind {
    ImmutableRecordRewritten,
    InvalidRoot,
    SymlinkEscape,
    FalseCompleteScanClaim,
    CompetingCurrentOwner,
    UnlinkedMeaningfulDocument,
    VersionLikeNameWithoutPredecessor,
    MissingPredecessor,
    GeneratedProjectionPresentedAsAuthored,
    ReviewDue,
    ExactDuplicate,
    ColdCurrentEntrypoint,
    UnsafeRetentionProposal,
    CoverageIncomplete,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentHygieneContractV1 {
    pub schema: String,
    pub sha256: String,
    pub enforcement: String,
    pub prompt: String,
    pub intents: Vec<DocumentIntentKind>,
    pub search_visibility_mutation_authorized: bool,
    pub file_mutation_authorized: bool,
    pub strict_closeout_authorized: bool,
}

pub fn document_hygiene_contract_sha256() -> String {
    format!(
        "{:x}",
        Sha256::digest(DOCUMENT_HYGIENE_CONTRACT_PROMPT.as_bytes())
    )
}

pub fn document_hygiene_contract_v1() -> DocumentHygieneContractV1 {
    DocumentHygieneContractV1 {
        schema: DOCUMENT_HYGIENE_CONTRACT_SCHEMA.to_string(),
        sha256: document_hygiene_contract_sha256(),
        enforcement: "advisory_with_observable_closeout".to_string(),
        prompt: DOCUMENT_HYGIENE_CONTRACT_PROMPT.to_string(),
        intents: DOCUMENT_INTENT_KINDS.to_vec(),
        search_visibility_mutation_authorized: false,
        file_mutation_authorized: false,
        strict_closeout_authorized: false,
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanonicalRootObservation {
    pub root_id: String,
    pub source_kind: RootSourceKind,
    pub project_key: String,
    pub canonical_path: String,
    pub path_identity_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScanBudgets {
    pub max_files: u64,
    pub max_directory_entries: u64,
    pub max_total_bytes: u64,
    pub max_file_bytes: u64,
    pub max_elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScanUsage {
    pub files_observed: u64,
    pub directory_entries_observed: u64,
    pub bytes_hashed: u64,
    pub bytes_extracted: u64,
    pub largest_file_bytes_read: u64,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExcludedScope {
    pub relative_path: String,
    pub reason: ExclusionReason,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IncompleteScope {
    pub relative_path: String,
    pub reason: ScanIncompleteReason,
    pub detail: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScanEnvelope {
    pub complete: bool,
    pub budgets: ScanBudgets,
    pub usage: ScanUsage,
    pub reached_limits: Vec<ScanLimit>,
    pub excluded_scope: Vec<ExcludedScope>,
    pub incomplete_scope: Vec<IncompleteScope>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MediaCoverage {
    pub media_family: MediaFamily,
    pub observed_files: u64,
    pub metadata_complete_files: u64,
    pub body_searchable_files: u64,
    pub body_extracted_files: u64,
    pub extraction_errors: u64,
    pub body_coverage: BodyCoverage,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionSourceIdentity {
    pub schema: String,
    pub identity: String,
    pub content_sha256: String,
    pub observed_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogSourceIdentities {
    pub filesystem_snapshot: ProjectionSourceIdentity,
    pub lifecycle_ledger: Option<ProjectionSourceIdentity>,
    pub artifact_index: Option<ProjectionSourceIdentity>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogAuthorityBoundary {
    pub projection_only: bool,
    pub filesystem_observation_owns: Vec<String>,
    pub lifecycle_ledger_owns: Vec<String>,
    pub artifact_index_owns: Vec<String>,
    pub search_visibility_mutation_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityReference {
    pub kind: IdentityReferenceKind,
    pub value: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogFinding {
    pub code: String,
    pub severity: HygieneSeverity,
    pub authority: Option<ClassificationAuthority>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentCatalogEntry {
    pub observation_id: String,
    pub document_id: Option<String>,
    pub artifact_id: Option<String>,
    pub project_key: String,
    pub root_id: String,
    pub relative_path: String,
    pub media_family: MediaFamily,
    pub media_type: String,
    pub size_bytes: u64,
    pub modified_at: Option<DateTime<Utc>>,
    pub content_sha256: Option<String>,
    pub git_state: GitState,
    pub role: DocumentRole,
    pub role_authority: Option<ClassificationAuthority>,
    pub subject_key: Option<String>,
    pub subject_authority: Option<ClassificationAuthority>,
    pub audience: Vec<String>,
    pub scope_ref: Option<String>,
    pub owner_entrypoint_ref: Option<IdentityReference>,
    pub activity_state: ActivityState,
    pub activity_authority: Option<ClassificationAuthority>,
    pub retention_disposition: RetentionDisposition,
    pub retention_authority: Option<ClassificationAuthority>,
    pub generated: Option<bool>,
    pub generated_authority: Option<ClassificationAuthority>,
    pub rebuild_ref: Option<String>,
    pub entrypoint_refs: Vec<IdentityReference>,
    pub outbound_refs: Vec<IdentityReference>,
    pub inbound_ref_count: u64,
    pub exact_duplicate_refs: Vec<IdentityReference>,
    pub possible_predecessor_refs: Vec<IdentityReference>,
    pub review_after: Option<DateTime<Utc>>,
    pub scan_findings: Vec<CatalogFinding>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityCrosswalkFinding {
    pub observation_id: String,
    pub document_id: Option<String>,
    pub artifact_id: Option<String>,
    pub status: CrosswalkStatus,
    pub conflict: Option<IdentityConflictKind>,
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentCatalogV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub project_key: String,
    pub roots: Vec<CanonicalRootObservation>,
    pub scan: ScanEnvelope,
    pub coverage: Vec<MediaCoverage>,
    pub source_identities: CatalogSourceIdentities,
    pub authority_boundary: CatalogAuthorityBoundary,
    pub entries: Vec<DocumentCatalogEntry>,
    pub crosswalk_findings: Vec<IdentityCrosswalkFinding>,
}

impl DocumentCatalogV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_CATALOG_SCHEMA {
            bail!("unsupported document catalog schema: {}", self.schema);
        }
        if self.project_key.trim().is_empty() {
            bail!("document catalog project_key must not be empty");
        }
        if self.roots.is_empty() {
            bail!("document catalog must include at least one root");
        }
        if !self.authority_boundary.projection_only
            || self
                .authority_boundary
                .search_visibility_mutation_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("document catalog authority must remain read-only");
        }
        validate_scan_envelope(&self.scan)?;
        if self.scan.usage.files_observed != self.entries.len() as u64 {
            bail!("scan usage must match the number of catalog entries");
        }

        let mut root_ids = BTreeSet::new();
        for root in &self.roots {
            if root.project_key != self.project_key {
                bail!("catalog root project_key does not match the envelope");
            }
            if !root_ids.insert(root.root_id.as_str()) {
                bail!("duplicate root_id: {}", root.root_id);
            }
            let canonical_path = Path::new(&root.canonical_path);
            if root.canonical_path.trim().is_empty()
                || !canonical_path.is_absolute()
                || root.canonical_path == "/"
            {
                bail!("catalog roots must be bounded canonical absolute paths");
            }
            if !is_sha256(&root.path_identity_sha256) {
                bail!("catalog root path identity must be a SHA-256 digest");
            }
        }

        validate_projection_source_identity(
            &self.source_identities.filesystem_snapshot,
            FILESYSTEM_OBSERVATION_SOURCE_SCHEMA,
            "filesystem snapshot",
        )?;
        if let Some(source) = &self.source_identities.lifecycle_ledger {
            validate_projection_source_identity(
                source,
                DOCUMENT_LIFECYCLE_LEDGER_SOURCE_SCHEMA,
                "lifecycle ledger",
            )?;
        }
        if let Some(source) = &self.source_identities.artifact_index {
            validate_projection_source_identity(
                source,
                ARTIFACT_INDEX_SOURCE_SCHEMA,
                "artifact index",
            )?;
        }

        let mut observation_ids = BTreeSet::new();
        for entry in &self.entries {
            validate_catalog_entry(self, entry, &root_ids)?;
            if !observation_ids.insert(entry.observation_id.as_str()) {
                bail!("duplicate observation_id: {}", entry.observation_id);
            }
        }

        for entry in &self.entries {
            for reference in &entry.exact_duplicate_refs {
                if reference.kind == IdentityReferenceKind::Observation
                    && !observation_ids.contains(reference.value.as_str())
                {
                    bail!(
                        "exact duplicate reference {} is not present in the catalog",
                        reference.value
                    );
                }
                if reference.kind == IdentityReferenceKind::Observation
                    && reference.value == entry.observation_id
                {
                    bail!("an observation cannot be an exact duplicate of itself");
                }
            }
        }

        validate_media_coverage(self)?;
        validate_crosswalk_findings(self, &observation_ids)?;
        Ok(())
    }
}

fn validate_catalog_entry(
    catalog: &DocumentCatalogV1,
    entry: &DocumentCatalogEntry,
    root_ids: &BTreeSet<&str>,
) -> Result<()> {
    if entry.project_key != catalog.project_key {
        bail!("catalog entry project_key does not match the envelope");
    }
    if !root_ids.contains(entry.root_id.as_str()) {
        bail!("catalog entry references an unknown root_id");
    }
    if entry.observation_id.trim().is_empty() {
        bail!("observation_id must not be empty");
    }
    if !is_bounded_relative_path(&entry.relative_path) {
        bail!("catalog entry path must be a bounded relative path");
    }
    if let Some(document_id) = &entry.document_id {
        if document_id.trim().is_empty() {
            bail!("durable document_id must not be empty");
        }
        if catalog.source_identities.lifecycle_ledger.is_none() {
            bail!("durable document_id requires a lifecycle ledger source identity");
        }
    }
    if let Some(artifact_id) = &entry.artifact_id {
        if artifact_id.trim().is_empty() {
            bail!("artifact_id must not be empty");
        }
        if catalog.source_identities.artifact_index.is_none() {
            bail!("artifact_id requires an artifact index source identity");
        }
    }
    if entry
        .content_sha256
        .as_deref()
        .is_some_and(|digest| !is_sha256(digest))
    {
        bail!("catalog entry content identity must be a SHA-256 digest");
    }
    if (entry.role == DocumentRole::Unknown) != entry.role_authority.is_none() {
        bail!("known document roles require authority and unknown roles require null");
    }
    if (entry.activity_state == ActivityState::Unknown) != entry.activity_authority.is_none() {
        bail!("known activity states require authority and unknown states require null");
    }
    if (entry.retention_disposition == RetentionDisposition::Unclassified)
        != entry.retention_authority.is_none()
    {
        bail!("known retention states require authority and unclassified requires null");
    }
    if entry.subject_key.is_none() != entry.subject_authority.is_none() {
        bail!("subject_key and subject_authority must be present or null together");
    }
    if entry.generated.is_none() != entry.generated_authority.is_none() {
        bail!("generated and generated_authority must be present or null together");
    }
    if entry.generated == Some(true)
        && entry
            .rebuild_ref
            .as_deref()
            .is_none_or(|reference| reference.trim().is_empty())
    {
        bail!("a document declared generated requires a non-empty rebuild_ref");
    }
    let has_reviewed_lifecycle_state = entry.activity_authority
        == Some(ClassificationAuthority::Reviewed)
        || entry.retention_authority == Some(ClassificationAuthority::Reviewed);
    if has_reviewed_lifecycle_state
        && (entry.document_id.is_none() || catalog.source_identities.lifecycle_ledger.is_none())
    {
        bail!("reviewed activity or retention requires durable identity from the lifecycle ledger");
    }
    Ok(())
}

fn validate_scan_envelope(scan: &ScanEnvelope) -> Result<()> {
    let mut reached_limits = BTreeSet::new();
    for limit in &scan.reached_limits {
        if !reached_limits.insert(*limit as u8) {
            bail!("scan reached_limits cannot contain duplicates");
        }
    }

    let exceeded_limits = [
        (
            scan.usage.files_observed > scan.budgets.max_files,
            ScanLimit::FileCount,
        ),
        (
            scan.usage.directory_entries_observed > scan.budgets.max_directory_entries,
            ScanLimit::DirectoryEntries,
        ),
        (
            scan.usage.bytes_hashed > scan.budgets.max_total_bytes
                || scan.usage.bytes_extracted > scan.budgets.max_total_bytes,
            ScanLimit::TotalBytes,
        ),
        (
            scan.usage.largest_file_bytes_read > scan.budgets.max_file_bytes,
            ScanLimit::PerFileBytes,
        ),
        (
            scan.usage.elapsed_ms > scan.budgets.max_elapsed_ms,
            ScanLimit::ElapsedTime,
        ),
    ];

    if scan.complete {
        if !scan.reached_limits.is_empty() || !scan.incomplete_scope.is_empty() {
            bail!("a complete scan cannot report reached limits or incomplete scope");
        }
        if exceeded_limits.iter().any(|(exceeded, _)| *exceeded) {
            bail!("a complete scan cannot exceed its declared budgets");
        }
    } else if scan.incomplete_scope.is_empty() {
        bail!("an incomplete scan must identify bounded unscanned scope");
    } else if !scan.reached_limits.is_empty()
        && !scan
            .incomplete_scope
            .iter()
            .any(|scope| scope.reason == ScanIncompleteReason::BudgetTruncation)
    {
        bail!("budget truncation requires an exact unscanned scope reason");
    }

    if scan.reached_limits.is_empty()
        && scan
            .incomplete_scope
            .iter()
            .any(|scope| scope.reason == ScanIncompleteReason::BudgetTruncation)
    {
        bail!("budget-truncated scope requires at least one reached limit");
    }

    for (exceeded, limit) in exceeded_limits {
        if exceeded && !scan.reached_limits.contains(&limit) {
            bail!("an exceeded scan budget must identify the reached limit");
        }
    }
    for scope in &scan.incomplete_scope {
        if !is_bounded_relative_path(&scope.relative_path) || scope.detail.trim().is_empty() {
            bail!("incomplete scope requires a bounded path and non-empty detail");
        }
    }
    Ok(())
}

fn validate_projection_source_identity(
    source: &ProjectionSourceIdentity,
    expected_schema: &str,
    label: &str,
) -> Result<()> {
    if source.schema != expected_schema {
        bail!("{label} source identity has an unsupported schema");
    }
    if source.identity.trim().is_empty() {
        bail!("{label} source identity must not be empty");
    }
    if !is_sha256(&source.content_sha256) {
        bail!("{label} source identity must include a SHA-256 digest");
    }
    Ok(())
}

fn is_bounded_relative_path(value: &str) -> bool {
    let path = Path::new(value);
    !value.trim().is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::Normal(_) | Component::CurDir))
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn validate_media_coverage(catalog: &DocumentCatalogV1) -> Result<()> {
    let mut covered_families = BTreeSet::new();
    let observed_by_family =
        catalog
            .entries
            .iter()
            .fold(BTreeMap::<MediaFamily, u64>::new(), |mut counts, entry| {
                *counts.entry(entry.media_family).or_default() += 1;
                counts
            });
    for coverage in &catalog.coverage {
        if !covered_families.insert(coverage.media_family) {
            bail!("duplicate media coverage row");
        }
        let body_searchable = matches!(
            coverage.media_family,
            MediaFamily::Markdown
                | MediaFamily::PlainText
                | MediaFamily::RestructuredText
                | MediaFamily::Asciidoc
                | MediaFamily::Org
        );
        let expected_body_searchable_files = if body_searchable {
            coverage.observed_files
        } else {
            0
        };
        if coverage.metadata_complete_files > coverage.observed_files
            || coverage.body_searchable_files != expected_body_searchable_files
            || coverage.body_extracted_files > coverage.body_searchable_files
            || coverage.extraction_errors
                > coverage
                    .body_searchable_files
                    .saturating_sub(coverage.body_extracted_files)
        {
            bail!("media coverage counts are inconsistent");
        }
        match coverage.body_coverage {
            BodyCoverage::Complete
                if coverage.body_extracted_files != coverage.body_searchable_files
                    || coverage.extraction_errors != 0 =>
            {
                bail!("complete body coverage requires every searchable body to be extracted")
            }
            BodyCoverage::Partial
                if coverage.body_searchable_files == 0
                    || coverage.body_extracted_files == coverage.body_searchable_files =>
            {
                bail!("partial body coverage requires at least one unextracted searchable body")
            }
            BodyCoverage::MetadataOnly
                if coverage.body_searchable_files != 0
                    || coverage.body_extracted_files != 0
                    || coverage.extraction_errors != 0 =>
            {
                bail!("metadata-only coverage cannot claim searchable or extracted bodies")
            }
            _ => {}
        }
        if !body_searchable && coverage.body_coverage != BodyCoverage::MetadataOnly {
            bail!("non-searchable media families must remain metadata-only");
        }
        if observed_by_family
            .get(&coverage.media_family)
            .copied()
            .unwrap_or_default()
            != coverage.observed_files
        {
            bail!("media coverage observed count does not match catalog entries");
        }
    }

    for entry in &catalog.entries {
        if !covered_families.contains(&entry.media_family) {
            bail!("every observed media family requires a coverage row");
        }
    }
    Ok(())
}

fn validate_crosswalk_findings(
    catalog: &DocumentCatalogV1,
    observation_ids: &BTreeSet<&str>,
) -> Result<()> {
    let mut finding_observation_ids = BTreeSet::new();
    for finding in &catalog.crosswalk_findings {
        if !observation_ids.contains(finding.observation_id.as_str()) {
            bail!("crosswalk finding references an unknown observation");
        }
        if !finding_observation_ids.insert(finding.observation_id.as_str()) {
            bail!("each observation must have exactly one crosswalk finding");
        }
        let entry = catalog
            .entries
            .iter()
            .find(|entry| entry.observation_id == finding.observation_id)
            .expect("crosswalk observation was checked above");
        if entry.document_id != finding.document_id || entry.artifact_id != finding.artifact_id {
            bail!("crosswalk identity does not match the catalog observation");
        }
        match finding.status {
            CrosswalkStatus::Conflict if finding.conflict.is_none() => {
                bail!("crosswalk conflict must identify its conflict kind")
            }
            CrosswalkStatus::CatalogOnly
                if finding.conflict.is_some() || finding.artifact_id.is_some() =>
            {
                bail!("catalog-only crosswalks cannot carry artifact identity or conflict")
            }
            CrosswalkStatus::Unified
                if finding.conflict.is_some() || finding.artifact_id.is_none() =>
            {
                bail!("unified crosswalks require artifact identity and no conflict")
            }
            _ => {}
        }
    }
    if finding_observation_ids.len() != observation_ids.len() {
        bail!("every catalog observation requires one crosswalk finding");
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityCrosswalkPrecedence {
    pub current_path_and_bytes: IdentityPlane,
    pub reviewed_lifecycle_state: IdentityPlane,
    pub artifact_role_and_retention_evidence: IdentityPlane,
    pub ambiguous_match_action: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionTargetContract {
    pub approval_ledger: String,
    pub catalog_only_source_kind: RetentionTargetSourceKind,
    pub artifact_backed_source_kind: RetentionTargetSourceKind,
    pub exact_current_path_and_hash_required: bool,
    pub fabricated_artifact_identity_allowed: bool,
    pub duplicate_approval_allowed: bool,
    pub existing_application_receipt_authority: String,
    pub existing_application_receipt_mutation_authorized: bool,
    pub ambiguous_match_action: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionTargetResolution {
    pub status: RetentionResolutionStatus,
    pub retention_target_id: Option<String>,
    pub source_kind: Option<RetentionTargetSourceKind>,
    pub fabricated_artifact_identity: bool,
    pub finding_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IdentityCrosswalkCase {
    pub case_id: String,
    pub observation_id: String,
    pub document_id: Option<String>,
    pub artifact_ids: Vec<String>,
    pub current_path_exact: bool,
    pub content_hash_exact: bool,
    pub expected: RetentionTargetResolution,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentIdentityCrosswalkV1 {
    pub schema: String,
    pub precedence: IdentityCrosswalkPrecedence,
    pub retention_target_contract: RetentionTargetContract,
    pub cases: Vec<IdentityCrosswalkCase>,
}

impl DocumentIdentityCrosswalkV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_IDENTITY_CROSSWALK_SCHEMA {
            bail!("unsupported document identity crosswalk schema");
        }
        if self.precedence.current_path_and_bytes != IdentityPlane::FilesystemObservation
            || self.precedence.reviewed_lifecycle_state != IdentityPlane::LifecycleLedger
            || self.precedence.artifact_role_and_retention_evidence != IdentityPlane::ArtifactIndex
            || self.precedence.ambiguous_match_action
                != "report_conflict_and_block_retention_selection"
        {
            bail!("identity precedence does not match the Phase 0 authority contract");
        }
        let contract = &self.retention_target_contract;
        if contract.approval_ledger != "existing_offdesk_approval_ledger"
            || contract.catalog_only_source_kind != RetentionTargetSourceKind::Document
            || contract.artifact_backed_source_kind != RetentionTargetSourceKind::Artifact
            || !contract.exact_current_path_and_hash_required
            || contract.fabricated_artifact_identity_allowed
            || contract.duplicate_approval_allowed
            || contract.existing_application_receipt_authority != "plan_provenance_only"
            || contract.existing_application_receipt_mutation_authorized
            || contract.ambiguous_match_action != "block_without_minting_target"
        {
            bail!("retention target contract would create a second or ambiguous authority plane");
        }

        let mut case_ids = BTreeSet::new();
        for case in &self.cases {
            if !case_ids.insert(case.case_id.as_str()) {
                bail!("duplicate crosswalk case_id");
            }
            if case.expected.fabricated_artifact_identity {
                bail!("crosswalk cases cannot fabricate artifact identity");
            }
            match case.expected.status {
                RetentionResolutionStatus::Mint => {
                    if !case.artifact_ids.is_empty()
                        || case.expected.source_kind != Some(RetentionTargetSourceKind::Document)
                        || case.expected.retention_target_id.is_none()
                        || !case.current_path_exact
                        || !case.content_hash_exact
                    {
                        bail!("catalog-only mint case violates the retention target contract");
                    }
                }
                RetentionResolutionStatus::Reuse => {
                    if case.artifact_ids.len() != 1
                        || case.expected.source_kind != Some(RetentionTargetSourceKind::Artifact)
                        || case.expected.retention_target_id.is_none()
                        || !case.current_path_exact
                        || !case.content_hash_exact
                    {
                        bail!("artifact-backed reuse case violates the retention target contract");
                    }
                }
                RetentionResolutionStatus::Blocked => {
                    if case.expected.source_kind.is_some()
                        || case.expected.retention_target_id.is_some()
                        || case.expected.finding_codes.is_empty()
                    {
                        bail!("blocked crosswalk case must remain unresolved with evidence");
                    }
                }
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogSnapshotRef {
    pub identity: String,
    pub generated_at: DateTime<Utc>,
    pub complete: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentIntentRecord {
    pub kind: DocumentIntentKind,
    pub subject_key: Option<String>,
    pub target_ref: Option<IdentityReference>,
    pub why_update_is_insufficient: Option<String>,
    pub active_entrypoint_ref: Option<IdentityReference>,
}

impl DocumentIntentRecord {
    fn validate(&self) -> Result<()> {
        match self.kind {
            DocumentIntentKind::UpdateExisting if self.target_ref.is_none() => {
                bail!("update_existing intent requires a target_ref")
            }
            DocumentIntentKind::CreateVersionedRecord | DocumentIntentKind::CreateNewDocument
                if self
                    .why_update_is_insufficient
                    .as_deref()
                    .is_none_or(|value| value.trim().is_empty())
                    || self.active_entrypoint_ref.is_none() =>
            {
                bail!("create intents require rationale and an active entrypoint")
            }
            _ => Ok(()),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentChange {
    pub change: DocumentChangeKind,
    pub before: Option<IdentityReference>,
    pub after: Option<IdentityReference>,
    pub intent: Option<DocumentIntentRecord>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HygieneFinding {
    pub finding_id: String,
    pub kind: HygieneFindingKind,
    pub severity: HygieneSeverity,
    pub authority: Option<ClassificationAuthority>,
    pub document_ref: Option<IdentityReference>,
    pub summary: String,
    pub evidence: Vec<String>,
    pub review_required: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HygieneSummary {
    pub errors: u64,
    pub warnings: u64,
    pub infos: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HygieneAuthorityBoundary {
    pub advisory_only: bool,
    pub strict_closeout_authorized: bool,
    pub search_visibility_mutation_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentHygieneReportV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub project_key: String,
    pub baseline: CatalogSnapshotRef,
    pub post_work: CatalogSnapshotRef,
    pub assessment_complete: bool,
    pub changes: Vec<DocumentChange>,
    pub findings: Vec<HygieneFinding>,
    pub summary: HygieneSummary,
    pub authority_boundary: HygieneAuthorityBoundary,
}

impl DocumentHygieneReportV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_HYGIENE_REPORT_SCHEMA {
            bail!("unsupported document hygiene report schema");
        }
        if self.project_key.trim().is_empty() {
            bail!("document hygiene report project_key must not be empty");
        }
        if !self.authority_boundary.advisory_only
            || self.authority_boundary.strict_closeout_authorized
            || self
                .authority_boundary
                .search_visibility_mutation_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("Phase 0 hygiene reports must remain advisory and non-mutating");
        }
        if (!self.baseline.complete || !self.post_work.complete) && self.assessment_complete {
            bail!("incomplete catalog input cannot produce a complete hygiene assessment");
        }
        if !self.assessment_complete
            && !self
                .findings
                .iter()
                .any(|finding| finding.kind == HygieneFindingKind::CoverageIncomplete)
        {
            bail!("incomplete assessment must expose a coverage_incomplete finding");
        }

        let mut finding_ids = BTreeSet::new();
        let mut actual = HygieneSummary {
            errors: 0,
            warnings: 0,
            infos: 0,
        };
        for finding in &self.findings {
            if !finding_ids.insert(finding.finding_id.as_str()) {
                bail!("duplicate hygiene finding_id");
            }
            match finding.severity {
                HygieneSeverity::Error => actual.errors += 1,
                HygieneSeverity::Warn => actual.warnings += 1,
                HygieneSeverity::Info => actual.infos += 1,
            }
        }
        if actual != self.summary {
            bail!("hygiene summary does not match finding severities");
        }
        for change in &self.changes {
            if let Some(intent) = &change.intent {
                intent.validate()?;
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_prompt_hash_is_frozen() {
        assert_eq!(
            document_hygiene_contract_sha256(),
            DOCUMENT_HYGIENE_CONTRACT_SHA256
        );
        let contract = document_hygiene_contract_v1();
        assert_eq!(contract.schema, DOCUMENT_HYGIENE_CONTRACT_SCHEMA);
        assert_eq!(contract.intents, DOCUMENT_INTENT_KINDS);
        assert!(!contract.file_mutation_authorized);
        assert!(!contract.search_visibility_mutation_authorized);
        assert!(!contract.strict_closeout_authorized);
    }

    #[test]
    fn vocabulary_keeps_activity_and_retention_separate() {
        assert!(ACTIVITY_STATES.contains(&ActivityState::Active));
        assert!(ACTIVITY_STATES.contains(&ActivityState::Retired));
        assert!(RETENTION_DISPOSITIONS.contains(&RetentionDisposition::Preserved));
        assert!(RETENTION_DISPOSITIONS.contains(&RetentionDisposition::Archived));
        assert!(CLASSIFICATION_AUTHORITIES.contains(&ClassificationAuthority::Reviewed));
    }
}
