//! Detached Phase 2A document lifecycle contracts.
//!
//! This module deliberately has no dependency on the active profile, the
//! Offdesk approval enum, or a production command handler. It can validate
//! supplied data and read a caller-supplied ledger root, but it cannot append
//! events or change search visibility.

use super::{
    ActivityState, BodyCoverage, DocumentCatalogEntry, DocumentCatalogV1, HygieneSeverity,
    IdentityConflictKind, MediaFamily, RetentionDisposition, RetentionTargetSourceKind,
};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, SecondsFormat, Utc};
use serde::{de::Error as _, Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};
use std::time::Instant;
use uuid::Uuid;

pub const DOCUMENT_LIFECYCLE_EVENT_SCHEMA: &str = "document_lifecycle_event.v1";
pub const DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA: &str = "document_lifecycle_approval_source.v1";
pub const DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA: &str =
    "document_lifecycle_approval_metadata.v1";
pub const RETENTION_APPROVAL_METADATA_SCHEMA_V2: &str = "retention_approval_metadata.v2";
pub const DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA: &str = "document_lifecycle_ledger_load.v1";
pub const DOCUMENT_LIFECYCLE_LEDGER_SNAPSHOT_SCHEMA: &str = "document_lifecycle_ledger_snapshot.v1";
pub const DOCUMENT_CATALOG_SCHEMA_V2: &str = "document_catalog.v2";
pub const DOCUMENT_SEARCH_SCHEMA_V2: &str = "document_search.v2";

const APPROVAL_SOURCE_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.approval-source.v1\0";
const PAYLOAD_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.payload.v1\0";
const EVENT_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.event.v1\0";
const LEDGER_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.ledger.v1\0";
const GENESIS_MARKER: &[u8] = b"genesis";

pub const DEFAULT_LIFECYCLE_MAX_EVENTS: u64 = 100_000;
pub const DEFAULT_LIFECYCLE_MAX_DIRECTORY_ENTRIES: u64 = 100_256;
pub const DEFAULT_LIFECYCLE_MAX_EVENT_BYTES: u64 = 256 * 1024;
pub const DEFAULT_LIFECYCLE_MAX_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
pub const DEFAULT_LIFECYCLE_MAX_ELAPSED_MS: u64 = 5_000;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct StrictUtcTimestamp(DateTime<Utc>);

impl StrictUtcTimestamp {
    pub fn parse(value: &str) -> Result<Self> {
        let parsed = DateTime::parse_from_rfc3339(value)
            .with_context(|| format!("invalid UTC timestamp: {value}"))?
            .with_timezone(&Utc);
        let canonical = parsed.to_rfc3339_opts(SecondsFormat::AutoSi, true);
        if value != canonical {
            bail!("timestamp must use canonical UTC precision with a Z suffix");
        }
        Ok(Self(parsed))
    }

    pub fn from_datetime(value: DateTime<Utc>) -> Self {
        let canonical = value.to_rfc3339_opts(SecondsFormat::AutoSi, true);
        Self::parse(&canonical).expect("UTC DateTime always has a canonical representation")
    }

    pub fn as_datetime(&self) -> &DateTime<Utc> {
        &self.0
    }

    pub fn as_str(&self) -> String {
        self.0.to_rfc3339_opts(SecondsFormat::AutoSi, true)
    }
}

impl Serialize for StrictUtcTimestamp {
    fn serialize<S>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(&self.as_str())
    }
}

impl<'de> Deserialize<'de> for StrictUtcTimestamp {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::parse(&value).map_err(D::Error::custom)
    }
}

impl fmt::Display for StrictUtcTimestamp {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetachedApprovalScopeV1 {
    Once,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetachedApprovalStatusV1 {
    Approved,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetachedApprovalModeV1 {
    OperatorRequired,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DetachedRiskLevelV1 {
    CanonicalMutation,
    Destructive,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetentionActionV1 {
    Keep,
    Preserve,
    #[serde(rename = "summary-keep")]
    SummaryKeep,
    Archive,
    Quarantine,
    Dispose,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RetentionRequestedActionV2 {
    Keep,
    Preserve,
    #[serde(rename = "summary-keep")]
    SummaryKeep,
    Promote,
    Archive,
    Quarantine,
    Dispose,
}

impl RetentionRequestedActionV2 {
    fn as_str(self) -> &'static str {
        match self {
            Self::Keep => "keep",
            Self::Preserve => "preserve",
            Self::SummaryKeep => "summary-keep",
            Self::Promote => "promote",
            Self::Archive => "archive",
            Self::Quarantine => "quarantine",
            Self::Dispose => "dispose",
        }
    }

    pub(crate) fn lifecycle_action(self) -> Option<RetentionActionV1> {
        match self {
            Self::Keep => Some(RetentionActionV1::Keep),
            Self::Preserve => Some(RetentionActionV1::Preserve),
            Self::SummaryKeep => Some(RetentionActionV1::SummaryKeep),
            Self::Promote => None,
            Self::Archive => Some(RetentionActionV1::Archive),
            Self::Quarantine => Some(RetentionActionV1::Quarantine),
            Self::Dispose => Some(RetentionActionV1::Dispose),
        }
    }

    pub(crate) fn expected_risk(self) -> DetachedRiskLevelV1 {
        self.lifecycle_action()
            .map_or(DetachedRiskLevelV1::CanonicalMutation, |action| {
                action.expected_risk()
            })
    }
}

impl RetentionActionV1 {
    pub(crate) fn expected_risk(self) -> DetachedRiskLevelV1 {
        match self {
            Self::Keep | Self::Preserve | Self::SummaryKeep => {
                DetachedRiskLevelV1::CanonicalMutation
            }
            Self::Archive | Self::Quarantine | Self::Dispose => DetachedRiskLevelV1::Destructive,
        }
    }

    pub(crate) fn expected_disposition(self) -> RetentionDisposition {
        match self {
            Self::Keep => RetentionDisposition::Keep,
            Self::Preserve => RetentionDisposition::Preserved,
            Self::SummaryKeep => RetentionDisposition::SummaryKept,
            Self::Archive => RetentionDisposition::RetirementCandidate,
            Self::Quarantine => RetentionDisposition::QuarantineCandidate,
            Self::Dispose => RetentionDisposition::DisposalCandidate,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionTargetV2 {
    pub retention_target_id: String,
    pub source_kind: RetentionTargetSourceKind,
    pub artifact_id: Option<String>,
    pub document_id: Option<String>,
    pub expected_document_head_event_id: Option<String>,
    pub observation: DocumentLifecycleObservationV1,
}

impl RetentionTargetV2 {
    fn validate(&self) -> Result<()> {
        validate_identifier(&self.retention_target_id, "retention_target_id")?;
        self.observation.validate()?;
        match self.source_kind {
            RetentionTargetSourceKind::Artifact => {
                let artifact_id = self
                    .artifact_id
                    .as_deref()
                    .context("artifact retention target requires artifact_id")?;
                validate_identifier(artifact_id, "artifact_id")?;
                if self.retention_target_id != artifact_id {
                    bail!("artifact retention target must reuse its artifact ID");
                }
            }
            RetentionTargetSourceKind::Document => {
                if self.artifact_id.is_some() {
                    bail!("catalog-only retention target cannot claim an artifact ID");
                }
                let document_id = self
                    .document_id
                    .as_deref()
                    .context("document retention target requires document_id")?;
                validate_prefixed_uuid(document_id, "document_", "document_id")?;
                if self.retention_target_id != document_id {
                    bail!("catalog-only retention target must reuse its document ID");
                }
            }
        }
        if self.document_id.is_some() != self.expected_document_head_event_id.is_some() {
            bail!("document retention identity fields must be present together");
        }
        if let Some(document_id) = &self.document_id {
            validate_prefixed_uuid(document_id, "document_", "document_id")?;
            validate_prefixed_uuid(
                self.expected_document_head_event_id
                    .as_deref()
                    .unwrap_or_default(),
                "document_event_",
                "expected document head",
            )?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionProposalV2 {
    pub project_key: String,
    pub root_id: String,
    pub root_path_identity_sha256: String,
    pub root_instance_identity_sha256: String,
    pub filesystem_snapshot_sha256: String,
    pub artifact_snapshot_sha256: String,
    pub target: RetentionTargetV2,
    pub requested_action: RetentionRequestedActionV2,
    pub before: Option<DocumentLifecycleStateV1>,
    pub after: Option<DocumentLifecycleStateV1>,
    pub reason: String,
}

impl RetentionProposalV2 {
    pub fn validate(&self) -> Result<()> {
        validate_identifier(&self.project_key, "project_key")?;
        validate_prefixed_identifier(&self.root_id, "root_", "root_id")?;
        validate_sha256(&self.root_path_identity_sha256, "root path identity")?;
        validate_sha256(
            &self.root_instance_identity_sha256,
            "root instance identity",
        )?;
        validate_sha256(&self.filesystem_snapshot_sha256, "filesystem snapshot")?;
        validate_sha256(&self.artifact_snapshot_sha256, "artifact snapshot")?;
        validate_safe_text(&self.reason, "reason")?;
        if self.reason.is_empty() {
            bail!("retention proposal reason cannot be empty");
        }
        self.target.validate()?;
        self.target.observation.validate_for_root(&self.root_id)?;
        match (&self.target.document_id, &self.before, &self.after) {
            (Some(_), None, None)
                if self.requested_action == RetentionRequestedActionV2::Promote => {}
            (Some(document_id), Some(before), Some(after)) => {
                before.validate_shape(document_id)?;
                after.validate_shape(document_id)?;
                if before == after
                    || before.activity_state != after.activity_state
                    || before.successor_document_id != after.successor_document_id
                    || before.active_owner_document_id != after.active_owner_document_id
                    || before.review_after != after.review_after
                    || self.requested_action.lifecycle_action().is_none()
                    || after.retention_disposition
                        != self
                            .requested_action
                            .lifecycle_action()
                            .expect("lifecycle action checked")
                            .expected_disposition()
                {
                    bail!("retention proposal must change only the mapped retention state");
                }
            }
            (None, None, None) => {}
            _ => bail!("document-bound retention proposal state is incomplete"),
        }
        if self.requested_action == RetentionRequestedActionV2::SummaryKeep
            && self.target.document_id.is_none()
        {
            bail!("summary-keep requires one exact document-bound target");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionApprovalMetadataV2 {
    pub schema: String,
    pub generated_at: StrictUtcTimestamp,
    pub proposal: RetentionProposalV2,
    pub proposal_sha256: String,
    pub application_id: String,
    pub request_id: String,
    pub task_id: String,
    pub approval_brief: crate::offdesk::ApprovalBrief,
}

impl RetentionApprovalMetadataV2 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != RETENTION_APPROVAL_METADATA_SCHEMA_V2 {
            bail!("unsupported retention approval metadata schema");
        }
        self.proposal.validate()?;
        validate_identifier(&self.application_id, "application_id")?;
        validate_identifier(&self.request_id, "request_id")?;
        validate_identifier(&self.task_id, "task_id")?;
        let proposal_sha256 = retention_proposal_sha256_v2(&self.proposal)?;
        if self.proposal_sha256 != proposal_sha256 {
            bail!("retention approval proposal hash mismatch");
        }
        if self.application_id != format!("retention_application_{proposal_sha256}") {
            bail!("retention application ID is not the deterministic proposal identity");
        }
        if self.approval_brief.schema != "approval_brief.v1"
            || self.approval_brief.recommendation != self.proposal.requested_action.as_str()
            || self.approval_brief.subject.trim().is_empty()
            || self.approval_brief.question.trim().is_empty()
            || self.approval_brief.source.as_deref() != Some("project.retention_request")
        {
            bail!("retention approval brief is incomplete or from the wrong surface");
        }
        Ok(())
    }
}

pub fn retention_proposal_sha256_v2(proposal: &RetentionProposalV2) -> Result<String> {
    proposal.validate()?;
    let bytes = serde_json::to_vec(proposal).context("serialize canonical retention proposal")?;
    Ok(domain_hash(b"forager.retention.proposal.v2", &[&bytes]))
}

pub fn retention_approval_metadata_sha256_v2(
    metadata: &RetentionApprovalMetadataV2,
) -> Result<String> {
    metadata.validate()?;
    let canonical =
        serde_json::to_value(metadata).context("canonicalize retention approval metadata")?;
    let bytes = serde_json::to_vec(&canonical).context("serialize retention approval metadata")?;
    Ok(domain_hash(
        b"forager.retention.approval-metadata.v2",
        &[&bytes],
    ))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ApprovalTargetIdentityV1 {
    Mint {
        observation_id: String,
    },
    Document {
        document_id: String,
        expected_head_event_id: String,
    },
    Retention {
        document_id: String,
        expected_head_event_id: String,
        retention_target_id: String,
        requested_action: RetentionActionV1,
        application_id: String,
    },
}

impl ApprovalTargetIdentityV1 {
    fn validate(&self) -> Result<()> {
        match self {
            Self::Mint { observation_id } => validate_observation_id(observation_id),
            Self::Document {
                document_id,
                expected_head_event_id,
            } => {
                validate_prefixed_uuid(document_id, "document_", "document_id")?;
                validate_prefixed_uuid(
                    expected_head_event_id,
                    "document_event_",
                    "expected_head_event_id",
                )
            }
            Self::Retention {
                document_id,
                expected_head_event_id,
                retention_target_id,
                application_id,
                ..
            } => {
                validate_prefixed_uuid(document_id, "document_", "retention document_id")?;
                validate_prefixed_uuid(
                    expected_head_event_id,
                    "document_event_",
                    "retention expected head",
                )?;
                validate_identifier(retention_target_id, "retention_target_id")?;
                validate_identifier(application_id, "application_id")
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleApprovalSourceV1 {
    pub schema: String,
    pub approval_id: String,
    pub action_id: String,
    pub scope: DetachedApprovalScopeV1,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub action: String,
    pub risk_level: DetachedRiskLevelV1,
    pub approval_mode: DetachedApprovalModeV1,
    pub created_at: StrictUtcTimestamp,
    pub expires_at: StrictUtcTimestamp,
    pub status: DetachedApprovalStatusV1,
    pub resolved_at: StrictUtcTimestamp,
    pub resolved_by: String,
    pub source_surface: String,
    pub preview_sha256: String,
    pub reason_sha256: String,
    pub metadata_schema: String,
    pub metadata_sha256: String,
    pub proposal_sha256: String,
    pub target_identity: ApprovalTargetIdentityV1,
}

impl DocumentLifecycleApprovalSourceV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA {
            bail!("unsupported document lifecycle approval source schema");
        }
        validate_prefixed_uuid(&self.approval_id, "approval_", "approval_id")?;
        validate_prefixed_uuid(&self.action_id, "action_", "action_id")?;
        validate_identifier(&self.project_key, "project_key")?;
        validate_identifier(&self.request_id, "request_id")?;
        validate_identifier(&self.task_id, "task_id")?;
        validate_safe_text(&self.resolved_by, "resolved_by")?;
        validate_identifier(&self.source_surface, "source_surface")?;
        validate_sha256(&self.preview_sha256, "preview_sha256")?;
        validate_sha256(&self.reason_sha256, "reason_sha256")?;
        validate_sha256(&self.metadata_sha256, "metadata_sha256")?;
        validate_sha256(&self.proposal_sha256, "proposal_sha256")?;
        self.target_identity.validate()?;
        if self.created_at > self.resolved_at || self.resolved_at > self.expires_at {
            bail!("approval timestamps must satisfy created <= resolved <= expires");
        }
        match &self.target_identity {
            ApprovalTargetIdentityV1::Mint { .. } | ApprovalTargetIdentityV1::Document { .. } => {
                if self.action != "document.lifecycle.transition"
                    || self.risk_level != DetachedRiskLevelV1::CanonicalMutation
                    || self.metadata_schema != DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA
                {
                    bail!("document lifecycle approval source fields are inconsistent");
                }
            }
            ApprovalTargetIdentityV1::Retention {
                requested_action, ..
            } => {
                if self.action != "maintenance.artifact_cleanup"
                    || self.risk_level != requested_action.expected_risk()
                    || self.metadata_schema != RETENTION_APPROVAL_METADATA_SCHEMA_V2
                {
                    bail!("retention approval source fields are inconsistent");
                }
            }
        }
        Ok(())
    }
}

pub fn canonical_approval_source_bytes(
    source: &DocumentLifecycleApprovalSourceV1,
) -> Result<Vec<u8>> {
    source.validate()?;
    serde_json::to_vec(source).context("serialize canonical lifecycle approval source")
}

pub fn document_lifecycle_approval_source_sha256(
    source: &DocumentLifecycleApprovalSourceV1,
) -> Result<String> {
    let bytes = canonical_approval_source_bytes(source)?;
    Ok(domain_hash(APPROVAL_SOURCE_HASH_DOMAIN, &[&bytes]))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentLifecycleEventKindV1 {
    MintDocument,
    ActivityTransition,
    RevalidateObservation,
    RebindObservation,
    RetentionTransition,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleObservationV1 {
    pub observation_id: String,
    pub relative_path: String,
    pub size_bytes: u64,
    pub modified_at: StrictUtcTimestamp,
    pub content_sha256: String,
}

impl DocumentLifecycleObservationV1 {
    fn validate(&self) -> Result<()> {
        validate_observation_id(&self.observation_id)?;
        validate_bounded_relative_path(&self.relative_path)?;
        validate_sha256(&self.content_sha256, "observation content_sha256")
    }

    fn validate_for_root(&self, root_id: &str) -> Result<()> {
        self.validate()?;
        let expected = super::catalog::observation_id(
            root_id,
            &self.relative_path,
            self.size_bytes,
            Some(self.modified_at.as_datetime()),
            Some(&self.content_sha256),
        );
        if self.observation_id != expected {
            bail!("observation_id does not match the exact Phase 1 observation tuple");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleStateV1 {
    pub activity_state: ActivityState,
    pub retention_disposition: RetentionDisposition,
    pub successor_document_id: Option<String>,
    pub active_owner_document_id: Option<String>,
    pub summary_document_id: Option<String>,
    pub review_after: Option<StrictUtcTimestamp>,
}

impl DocumentLifecycleStateV1 {
    fn validate_shape(&self, document_id: &str) -> Result<()> {
        if self.activity_state == ActivityState::Unknown {
            bail!("reviewed lifecycle state cannot use unknown activity");
        }
        if matches!(
            self.retention_disposition,
            RetentionDisposition::Archived
                | RetentionDisposition::Quarantined
                | RetentionDisposition::Disposed
        ) {
            bail!("Phase 2 lifecycle state cannot claim a completed file mutation");
        }
        for (label, reference) in [
            ("successor_document_id", &self.successor_document_id),
            ("active_owner_document_id", &self.active_owner_document_id),
            ("summary_document_id", &self.summary_document_id),
        ] {
            if let Some(reference) = reference {
                validate_prefixed_uuid(reference, "document_", label)?;
                if reference == document_id {
                    bail!("lifecycle state cannot reference its own document as {label}");
                }
            }
        }
        if self.activity_state == ActivityState::Superseded && self.successor_document_id.is_none()
        {
            bail!("superseded activity requires a successor document");
        }
        if matches!(
            self.activity_state,
            ActivityState::Draft
                | ActivityState::Candidate
                | ActivityState::Active
                | ActivityState::ReviewDue
        ) && self.successor_document_id.is_some()
        {
            bail!("current activity cannot retain a successor binding");
        }
        if self.retention_disposition == RetentionDisposition::SummaryKept
            && self.summary_document_id.is_none()
        {
            bail!("summary_kept requires an exact summary document");
        }
        if self.retention_disposition != RetentionDisposition::SummaryKept
            && self.summary_document_id.is_some()
        {
            bail!("summary document is valid only for summary_kept retention");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleEventPayloadV1 {
    pub sequence: u64,
    pub event_id: String,
    pub event_kind: DocumentLifecycleEventKindV1,
    pub recorded_at: StrictUtcTimestamp,
    pub previous_event_sha256: Option<String>,
    pub project_key: String,
    pub root_id: String,
    pub root_path_identity_sha256: String,
    pub root_instance_identity_sha256: String,
    pub document_id: String,
    pub expected_document_head_event_id: Option<String>,
    pub observation: DocumentLifecycleObservationV1,
    pub before: Option<DocumentLifecycleStateV1>,
    pub after: DocumentLifecycleStateV1,
    pub proposal_sha256: String,
    pub approval_source: DocumentLifecycleApprovalSourceV1,
    pub approval_source_sha256: String,
    pub reason: String,
    pub idempotency_key: String,
}

impl DocumentLifecycleEventPayloadV1 {
    pub fn validate(&self) -> Result<()> {
        if self.sequence == 0 {
            bail!("lifecycle sequence starts at one");
        }
        validate_prefixed_uuid(&self.event_id, "document_event_", "event_id")?;
        validate_identifier(&self.project_key, "project_key")?;
        validate_prefixed_identifier(&self.root_id, "root_", "root_id")?;
        validate_sha256(&self.root_path_identity_sha256, "root_path_identity_sha256")?;
        validate_sha256(
            &self.root_instance_identity_sha256,
            "root_instance_identity_sha256",
        )?;
        validate_prefixed_uuid(&self.document_id, "document_", "document_id")?;
        if let Some(head) = &self.expected_document_head_event_id {
            validate_prefixed_uuid(head, "document_event_", "expected document head")?;
        }
        if let Some(previous) = &self.previous_event_sha256 {
            validate_sha256(previous, "previous_event_sha256")?;
        }
        self.observation.validate_for_root(&self.root_id)?;
        self.after.validate_shape(&self.document_id)?;
        if let Some(before) = &self.before {
            before.validate_shape(&self.document_id)?;
        }
        validate_sha256(&self.proposal_sha256, "proposal_sha256")?;
        self.approval_source.validate()?;
        validate_sha256(&self.approval_source_sha256, "approval_source_sha256")?;
        if document_lifecycle_approval_source_sha256(&self.approval_source)?
            != self.approval_source_sha256
        {
            bail!("approval source hash does not match canonical approval bytes");
        }
        if self.proposal_sha256 != self.approval_source.proposal_sha256 {
            bail!("event proposal hash does not match its approval source");
        }
        if self.project_key != self.approval_source.project_key {
            bail!("event project does not match its approval source");
        }
        if self.approval_source.resolved_at > self.recorded_at
            || self.recorded_at > self.approval_source.expires_at
        {
            bail!("event must be recorded after resolution and before approval expiry");
        }
        if self.observation.modified_at > self.recorded_at {
            bail!("event cannot bind an observation modified in the future");
        }
        validate_safe_text(&self.reason, "reason")?;
        validate_identifier(&self.idempotency_key, "idempotency_key")?;
        self.validate_target_binding()?;
        Ok(())
    }

    fn validate_target_binding(&self) -> Result<()> {
        match (&self.event_kind, &self.approval_source.target_identity) {
            (
                DocumentLifecycleEventKindV1::MintDocument,
                ApprovalTargetIdentityV1::Mint { observation_id },
            ) if observation_id == &self.observation.observation_id => Ok(()),
            (
                DocumentLifecycleEventKindV1::ActivityTransition
                | DocumentLifecycleEventKindV1::RevalidateObservation
                | DocumentLifecycleEventKindV1::RebindObservation,
                ApprovalTargetIdentityV1::Document {
                    document_id,
                    expected_head_event_id,
                },
            ) if document_id == &self.document_id
                && Some(expected_head_event_id)
                    == self.expected_document_head_event_id.as_ref() =>
            {
                Ok(())
            }
            (
                DocumentLifecycleEventKindV1::RetentionTransition,
                ApprovalTargetIdentityV1::Retention {
                    document_id,
                    expected_head_event_id,
                    application_id,
                    ..
                },
            ) if document_id == &self.document_id
                && Some(expected_head_event_id)
                    == self.expected_document_head_event_id.as_ref()
                && application_id == &self.idempotency_key =>
            {
                Ok(())
            }
            _ => bail!("event kind and approval target identity are inconsistent"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleEventV1 {
    pub schema: String,
    pub payload: DocumentLifecycleEventPayloadV1,
    pub payload_sha256: String,
    pub event_sha256: String,
}

impl DocumentLifecycleEventV1 {
    pub fn from_payload(payload: DocumentLifecycleEventPayloadV1) -> Result<Self> {
        payload.validate()?;
        let payload_sha256 = document_lifecycle_payload_sha256(&payload)?;
        let event_sha256 = document_lifecycle_event_sha256(&payload, &payload_sha256)?;
        Ok(Self {
            schema: DOCUMENT_LIFECYCLE_EVENT_SCHEMA.to_string(),
            payload,
            payload_sha256,
            event_sha256,
        })
    }

    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_EVENT_SCHEMA {
            bail!("unsupported document lifecycle event schema");
        }
        self.payload.validate()?;
        validate_sha256(&self.payload_sha256, "payload_sha256")?;
        validate_sha256(&self.event_sha256, "event_sha256")?;
        if document_lifecycle_payload_sha256(&self.payload)? != self.payload_sha256 {
            bail!("payload hash does not match canonical payload bytes");
        }
        if document_lifecycle_event_sha256(&self.payload, &self.payload_sha256)?
            != self.event_sha256
        {
            bail!("event hash does not match its payload and chain predecessor");
        }
        Ok(())
    }
}

pub fn canonical_document_lifecycle_payload_bytes(
    payload: &DocumentLifecycleEventPayloadV1,
) -> Result<Vec<u8>> {
    payload.validate()?;
    serde_json::to_vec(payload).context("serialize canonical document lifecycle payload")
}

pub fn document_lifecycle_payload_sha256(
    payload: &DocumentLifecycleEventPayloadV1,
) -> Result<String> {
    let bytes = canonical_document_lifecycle_payload_bytes(payload)?;
    Ok(domain_hash(PAYLOAD_HASH_DOMAIN, &[&bytes]))
}

pub fn document_lifecycle_event_sha256(
    payload: &DocumentLifecycleEventPayloadV1,
    payload_sha256: &str,
) -> Result<String> {
    validate_sha256(payload_sha256, "payload_sha256")?;
    let previous = payload
        .previous_event_sha256
        .as_deref()
        .map(str::as_bytes)
        .unwrap_or(GENESIS_MARKER);
    Ok(domain_hash(
        EVENT_HASH_DOMAIN,
        &[previous, payload_sha256.as_bytes()],
    ))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleLedgerExpectationV1 {
    pub project_key: String,
    pub root_id: String,
    pub root_path_identity_sha256: String,
    pub root_instance_identity_sha256: String,
}

impl LifecycleLedgerExpectationV1 {
    pub fn validate(&self) -> Result<()> {
        validate_identifier(&self.project_key, "expected project_key")?;
        validate_prefixed_identifier(&self.root_id, "root_", "expected root_id")?;
        validate_sha256(
            &self.root_path_identity_sha256,
            "expected root_path_identity_sha256",
        )?;
        validate_sha256(
            &self.root_instance_identity_sha256,
            "expected root_instance_identity_sha256",
        )
    }

    fn matches(&self, payload: &DocumentLifecycleEventPayloadV1) -> bool {
        self.project_key == payload.project_key
            && self.root_id == payload.root_id
            && self.root_path_identity_sha256 == payload.root_path_identity_sha256
            && self.root_instance_identity_sha256 == payload.root_instance_identity_sha256
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocumentLifecycleChainSummary {
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub event_count: u64,
    pub final_event_sha256: String,
    pub ordered_event_hashes_sha256: String,
}

#[derive(Debug, Clone)]
struct DocumentHead {
    event_id: String,
    observation: DocumentLifecycleObservationV1,
    state: DocumentLifecycleStateV1,
}

pub fn validate_document_lifecycle_event_chain(
    events: &[DocumentLifecycleEventV1],
    expectation: &LifecycleLedgerExpectationV1,
) -> Result<DocumentLifecycleChainSummary> {
    expectation.validate()?;
    if events.is_empty() {
        bail!("a valid lifecycle ledger must contain at least one event");
    }

    let mut event_ids = BTreeSet::new();
    let mut idempotency_keys = BTreeMap::<&str, &str>::new();
    let mut document_heads = BTreeMap::<String, DocumentHead>::new();
    let mut observation_bindings =
        BTreeMap::<String, (String, DocumentLifecycleObservationV1)>::new();
    let mut previous_recorded_at: Option<&StrictUtcTimestamp> = None;
    let mut previous_hash: Option<&str> = None;

    for (index, event) in events.iter().enumerate() {
        event.validate()?;
        let payload = &event.payload;
        let expected_sequence = index as u64 + 1;
        if payload.sequence != expected_sequence {
            bail!("lifecycle sequence is missing, duplicated, or out of order");
        }
        if payload.previous_event_sha256.as_deref() != previous_hash {
            bail!("lifecycle previous-event hash does not match the complete chain");
        }
        if !expectation.matches(payload) {
            bail!("lifecycle event identity does not match the requested project root");
        }
        if !event_ids.insert(payload.event_id.as_str()) {
            bail!("duplicate lifecycle event_id");
        }
        if let Some(existing_hash) = idempotency_keys.insert(
            payload.idempotency_key.as_str(),
            event.event_sha256.as_str(),
        ) {
            if existing_hash != event.event_sha256 {
                bail!("one idempotency key maps to different lifecycle events");
            }
            bail!("duplicate lifecycle event for one idempotency key");
        }
        if previous_recorded_at.is_some_and(|previous| previous > &payload.recorded_at) {
            bail!("lifecycle recorded_at values must be globally nondecreasing");
        }

        validate_event_against_document_head(payload, &document_heads)?;
        if let Some((owner, observation)) =
            observation_bindings.get(&payload.observation.observation_id)
        {
            if owner != &payload.document_id || observation != &payload.observation {
                bail!("one observation ID must remain bound to one exact document observation");
            }
        } else {
            observation_bindings.insert(
                payload.observation.observation_id.clone(),
                (payload.document_id.clone(), payload.observation.clone()),
            );
        }

        document_heads.insert(
            payload.document_id.clone(),
            DocumentHead {
                event_id: payload.event_id.clone(),
                observation: payload.observation.clone(),
                state: payload.after.clone(),
            },
        );
        validate_lifecycle_references(&document_heads)?;

        previous_recorded_at = Some(&payload.recorded_at);
        previous_hash = Some(&event.event_sha256);
    }

    let ordered_hashes = events
        .iter()
        .map(|event| event.event_sha256.as_bytes())
        .collect::<Vec<_>>();
    let final_event_sha256 = events
        .last()
        .expect("non-empty lifecycle event chain")
        .event_sha256
        .clone();
    Ok(DocumentLifecycleChainSummary {
        first_sequence: 1,
        last_sequence: events.len() as u64,
        event_count: events.len() as u64,
        final_event_sha256,
        ordered_event_hashes_sha256: domain_hash(LEDGER_HASH_DOMAIN, &ordered_hashes),
    })
}

fn validate_event_against_document_head(
    payload: &DocumentLifecycleEventPayloadV1,
    heads: &BTreeMap<String, DocumentHead>,
) -> Result<()> {
    let current = heads.get(&payload.document_id);
    match payload.event_kind {
        DocumentLifecycleEventKindV1::MintDocument => {
            if current.is_some()
                || payload.expected_document_head_event_id.is_some()
                || payload.before.is_some()
            {
                bail!("mint requires a new document with null before and expected head");
            }
            if payload.after.retention_disposition != RetentionDisposition::Unclassified {
                bail!("mint cannot bypass the retention approval flow");
            }
        }
        _ => {
            let current = current.context("non-mint event requires an existing document head")?;
            if payload.expected_document_head_event_id.as_deref() != Some(current.event_id.as_str())
                || payload.before.as_ref() != Some(&current.state)
            {
                bail!("event before state or expected document head is stale");
            }
            match payload.event_kind {
                DocumentLifecycleEventKindV1::ActivityTransition => {
                    if payload.observation != current.observation
                        || payload.after == current.state
                        || payload.after.retention_disposition
                            != current.state.retention_disposition
                        || payload.after.summary_document_id != current.state.summary_document_id
                    {
                        bail!("activity transition cannot alter observation or retention state");
                    }
                }
                DocumentLifecycleEventKindV1::RetentionTransition => {
                    if payload.observation != current.observation
                        || payload.after == current.state
                        || payload.after.activity_state != current.state.activity_state
                        || payload.after.successor_document_id
                            != current.state.successor_document_id
                        || payload.after.active_owner_document_id
                            != current.state.active_owner_document_id
                        || payload.after.review_after != current.state.review_after
                    {
                        bail!("retention transition cannot alter observation or activity state");
                    }
                    let ApprovalTargetIdentityV1::Retention {
                        requested_action, ..
                    } = &payload.approval_source.target_identity
                    else {
                        bail!("retention event requires retention approval metadata");
                    };
                    if payload.after.retention_disposition
                        != requested_action.expected_disposition()
                    {
                        bail!("retention action does not match the resulting disposition");
                    }
                }
                DocumentLifecycleEventKindV1::RevalidateObservation => {
                    if payload.observation.relative_path != current.observation.relative_path
                        || payload.observation.content_sha256 == current.observation.content_sha256
                        || payload.after != current.state
                    {
                        bail!("revalidation requires changed bytes at the same path and state");
                    }
                }
                DocumentLifecycleEventKindV1::RebindObservation => {
                    if payload.observation.relative_path == current.observation.relative_path
                        || payload.observation.content_sha256 != current.observation.content_sha256
                        || payload.after != current.state
                    {
                        bail!("rebind requires the same bytes at one different path and state");
                    }
                }
                DocumentLifecycleEventKindV1::MintDocument => unreachable!(),
            }
        }
    }
    Ok(())
}

fn validate_lifecycle_references(heads: &BTreeMap<String, DocumentHead>) -> Result<()> {
    for (document_id, head) in heads {
        for (label, reference) in [
            ("successor", &head.state.successor_document_id),
            ("active owner", &head.state.active_owner_document_id),
            ("summary", &head.state.summary_document_id),
        ] {
            if let Some(reference) = reference {
                let target = heads
                    .get(reference)
                    .with_context(|| format!("{label} references an unknown document"))?;
                if target.state.activity_state != ActivityState::Active {
                    bail!("{label} must reference an exact reviewed-active document");
                }
                if reference == document_id {
                    bail!("{label} cannot reference the same document");
                }
            }
        }
    }
    validate_reference_graph_acyclic(heads)
}

fn validate_reference_graph_acyclic(heads: &BTreeMap<String, DocumentHead>) -> Result<()> {
    fn visit<'a>(
        document_id: &'a str,
        heads: &'a BTreeMap<String, DocumentHead>,
        visiting: &mut BTreeSet<&'a str>,
        complete: &mut BTreeSet<&'a str>,
    ) -> Result<()> {
        if complete.contains(document_id) {
            return Ok(());
        }
        if !visiting.insert(document_id) {
            bail!("successor and owner graph must be acyclic");
        }
        let head = heads
            .get(document_id)
            .context("graph references an unknown document")?;
        for target in [
            head.state.successor_document_id.as_deref(),
            head.state.active_owner_document_id.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            visit(target, heads, visiting, complete)?;
        }
        visiting.remove(document_id);
        complete.insert(document_id);
        Ok(())
    }

    let mut complete = BTreeSet::new();
    for document_id in heads.keys() {
        visit(document_id, heads, &mut BTreeSet::new(), &mut complete)?;
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentMintEvidenceV1 {
    pub scan_complete: bool,
    pub root_path_identity_exact: bool,
    pub root_instance_identity_exact: bool,
    pub observation_exact: bool,
    pub descriptor_read_stable: bool,
    pub existing_observation_bindings: u64,
    pub unresolved_identity_conflicts: u64,
    pub unresolved_artifact_crosswalk_conflicts: u64,
    pub approval_current_and_exact: bool,
}

impl DocumentMintEvidenceV1 {
    pub fn validate_authority(&self) -> Result<()> {
        if !self.scan_complete
            || !self.root_path_identity_exact
            || !self.root_instance_identity_exact
            || !self.observation_exact
            || !self.descriptor_read_stable
            || self.existing_observation_bindings != 0
            || self.unresolved_identity_conflicts != 0
            || self.unresolved_artifact_crosswalk_conflicts != 0
            || !self.approval_current_and_exact
        {
            bail!("mint evidence does not authorize durable identity creation");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentRebindEvidenceV1 {
    pub scan_complete: bool,
    pub old_relative_path: String,
    pub old_path_present: bool,
    pub candidate: DocumentLifecycleObservationV1,
    pub same_hash_candidate_observation_ids: Vec<String>,
    pub candidate_bound_document_id: Option<String>,
    pub candidate_pending_document_id: Option<String>,
    pub reviewed_content_sha256: String,
    pub reviewed_root_path_identity_sha256: String,
    pub current_root_path_identity_sha256: String,
    pub reviewed_root_instance_identity_sha256: String,
    pub current_root_instance_identity_sha256: String,
    pub expected_head_event_id: String,
    pub current_head_event_id: String,
}

impl DocumentRebindEvidenceV1 {
    pub fn validate_authority(&self) -> Result<()> {
        validate_bounded_relative_path(&self.old_relative_path)?;
        self.candidate.validate()?;
        validate_sha256(&self.reviewed_content_sha256, "reviewed content hash")?;
        validate_sha256(
            &self.reviewed_root_path_identity_sha256,
            "reviewed root path identity",
        )?;
        validate_sha256(
            &self.current_root_path_identity_sha256,
            "current root path identity",
        )?;
        validate_sha256(
            &self.reviewed_root_instance_identity_sha256,
            "reviewed root instance identity",
        )?;
        validate_sha256(
            &self.current_root_instance_identity_sha256,
            "current root instance identity",
        )?;
        validate_prefixed_uuid(
            &self.expected_head_event_id,
            "document_event_",
            "expected head",
        )?;
        validate_prefixed_uuid(
            &self.current_head_event_id,
            "document_event_",
            "current head",
        )?;
        if let Some(document_id) = &self.candidate_bound_document_id {
            validate_prefixed_uuid(document_id, "document_", "candidate binding")?;
        }
        if let Some(document_id) = &self.candidate_pending_document_id {
            validate_prefixed_uuid(document_id, "document_", "pending candidate binding")?;
        }
        let candidate_is_unique =
            self.same_hash_candidate_observation_ids == [self.candidate.observation_id.clone()];
        if !self.scan_complete
            || self.old_path_present
            || self.old_relative_path == self.candidate.relative_path
            || !candidate_is_unique
            || self.candidate_bound_document_id.is_some()
            || self.candidate_pending_document_id.is_some()
            || self.reviewed_content_sha256 != self.candidate.content_sha256
            || self.reviewed_root_path_identity_sha256 != self.current_root_path_identity_sha256
            || self.reviewed_root_instance_identity_sha256
                != self.current_root_instance_identity_sha256
            || self.expected_head_event_id != self.current_head_event_id
        {
            bail!("rebind evidence does not prove one exact external move");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleProjectionStatusV1 {
    Absent,
    Valid,
    Corrupt,
    UnsupportedSchema,
    PartialWritePresent,
    IdentityMismatch,
    UnsafeStorage,
    BudgetExceeded,
}

impl LifecycleProjectionStatusV1 {
    fn is_valid(self) -> bool {
        self == Self::Valid
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleLedgerSnapshotIdentityV1 {
    pub schema: String,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub event_count: u64,
    pub final_event_sha256: String,
    pub ordered_event_hashes_sha256: String,
    pub observed_at: StrictUtcTimestamp,
}

impl LifecycleLedgerSnapshotIdentityV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_LEDGER_SNAPSHOT_SCHEMA
            || self.first_sequence != 1
            || self.last_sequence != self.event_count
            || self.event_count == 0
        {
            bail!("lifecycle ledger snapshot sequence identity is inconsistent");
        }
        validate_sha256(&self.final_event_sha256, "final_event_sha256")?;
        validate_sha256(
            &self.ordered_event_hashes_sha256,
            "ordered_event_hashes_sha256",
        )
    }
}

impl DocumentLifecycleChainSummary {
    fn snapshot(&self, observed_at: StrictUtcTimestamp) -> LifecycleLedgerSnapshotIdentityV1 {
        LifecycleLedgerSnapshotIdentityV1 {
            schema: DOCUMENT_LIFECYCLE_LEDGER_SNAPSHOT_SCHEMA.to_string(),
            first_sequence: self.first_sequence,
            last_sequence: self.last_sequence,
            event_count: self.event_count,
            final_event_sha256: self.final_event_sha256.clone(),
            ordered_event_hashes_sha256: self.ordered_event_hashes_sha256.clone(),
            observed_at,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleProjectionEnvelopeV2 {
    pub status: LifecycleProjectionStatusV1,
    pub expectation: Option<LifecycleLedgerExpectationV1>,
    pub source_identity: Option<LifecycleLedgerSnapshotIdentityV1>,
    pub filtering_authorized: bool,
    pub failure_code: Option<String>,
}

impl LifecycleProjectionEnvelopeV2 {
    pub fn validate(&self) -> Result<()> {
        if self.status.is_valid() {
            self.expectation
                .as_ref()
                .context("valid lifecycle projection requires project-root expectation")?
                .validate()?;
            self.source_identity
                .as_ref()
                .context("valid lifecycle projection requires source identity")?
                .validate()?;
            if self.failure_code.is_some() {
                bail!("valid lifecycle projection cannot carry a failure code");
            }
        } else {
            if self.expectation.is_some()
                || self.source_identity.is_some()
                || self.filtering_authorized
            {
                bail!("invalid lifecycle state cannot project a prefix or authorize filtering");
            }
            if self.status == LifecycleProjectionStatusV1::Absent {
                if self.failure_code.is_some() {
                    bail!("absent lifecycle state does not carry an integrity failure");
                }
            } else if self
                .failure_code
                .as_deref()
                .is_none_or(|code| code.trim().is_empty())
            {
                bail!("invalid lifecycle state requires an operator-visible failure code");
            } else if let Some(code) = &self.failure_code {
                validate_identifier(code, "lifecycle failure_code")?;
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SearchVisibilityV2 {
    Visible,
    Cold,
    VisibleFailOpen,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentCatalogEntryV2 {
    pub observation: DocumentCatalogEntry,
    pub document_id: Option<String>,
    pub lifecycle_head_event_id: Option<String>,
    pub reviewed_state: Option<DocumentLifecycleStateV1>,
    pub lifecycle_stale: bool,
    pub visibility: SearchVisibilityV2,
    pub visibility_evidence: String,
}

impl DocumentCatalogEntryV2 {
    fn validate(&self, lifecycle: &LifecycleProjectionEnvelopeV2, project_key: &str) -> Result<()> {
        if self.observation.project_key != project_key {
            bail!("catalog v2 observation belongs to another project");
        }
        validate_safe_text(&self.visibility_evidence, "visibility_evidence")?;
        if !lifecycle.filtering_authorized {
            let expected_evidence = lifecycle_disabled_visibility_evidence_v2(
                lifecycle.status,
                lifecycle.failure_code.as_deref(),
                self.document_id.is_some(),
                self.lifecycle_stale,
            );
            if self.visibility_evidence != expected_evidence {
                bail!("visibility evidence does not match the lifecycle projection state");
            }
        }
        if !lifecycle.status.is_valid() {
            if self.document_id.is_some()
                || self.lifecycle_head_event_id.is_some()
                || self.reviewed_state.is_some()
                || !self.lifecycle_stale
                || self.visibility != SearchVisibilityV2::VisibleFailOpen
            {
                bail!("invalid lifecycle source must make every entry globally fail open");
            }
            return Ok(());
        }

        if self.document_id.is_some() != self.lifecycle_head_event_id.is_some()
            || self.document_id.is_some() != self.reviewed_state.is_some()
        {
            bail!("durable lifecycle projection fields must be present together");
        }
        if let Some(document_id) = &self.document_id {
            validate_prefixed_uuid(document_id, "document_", "catalog document_id")?;
            validate_prefixed_uuid(
                self.lifecycle_head_event_id.as_deref().unwrap_or_default(),
                "document_event_",
                "catalog lifecycle head",
            )?;
            self.reviewed_state
                .as_ref()
                .expect("reviewed state presence checked")
                .validate_shape(document_id)?;
        }
        if self.lifecycle_stale && self.visibility == SearchVisibilityV2::Cold {
            bail!("stale lifecycle state cannot make a document cold");
        }
        match self.visibility {
            SearchVisibilityV2::Cold => {
                if !lifecycle.filtering_authorized || self.lifecycle_stale {
                    bail!("cold visibility requires exact filtering authority");
                }
                let state = self
                    .reviewed_state
                    .as_ref()
                    .context("cold visibility requires reviewed lifecycle state")?;
                if !matches!(
                    state.activity_state,
                    ActivityState::Superseded | ActivityState::Retired
                ) || (state.successor_document_id.is_none()
                    && state.active_owner_document_id.is_none())
                {
                    bail!("cold visibility requires a reviewed inactive document and replacement");
                }
            }
            SearchVisibilityV2::VisibleFailOpen => {
                if lifecycle.filtering_authorized && !self.lifecycle_stale {
                    bail!("fail-open visibility requires stale or disabled lifecycle authority");
                }
            }
            SearchVisibilityV2::Visible => {}
        }
        Ok(())
    }
}

pub(super) fn lifecycle_disabled_visibility_evidence_v2(
    status: LifecycleProjectionStatusV1,
    failure_code: Option<&str>,
    exact_projection: bool,
    lifecycle_stale: bool,
) -> String {
    if !status.is_valid() {
        let reason = failure_code
            .unwrap_or("lifecycle_ledger_absent")
            .replace('_', " ");
        return format!(
            "Lifecycle projection unavailable ({reason}); document remains visible fail open."
        );
    }
    if exact_projection {
        "Exact reviewed lifecycle binding projected; Phase 2D filtering remains disabled."
            .to_string()
    } else if lifecycle_stale {
        "Current observation conflicts with a stale reviewed lifecycle binding; filtering remains disabled."
            .to_string()
    } else {
        "No exact reviewed lifecycle binding; Phase 2D filtering remains disabled.".to_string()
    }
}

pub(super) fn lifecycle_filtering_visibility_v2(
    document_id: Option<&str>,
    state: Option<&DocumentLifecycleStateV1>,
    exact_active_document_ids: &BTreeSet<String>,
) -> (SearchVisibilityV2, String) {
    let (Some(document_id), Some(state)) = (document_id, state) else {
        return (
            SearchVisibilityV2::Visible,
            "No reviewed lifecycle binding; document remains visible.".to_string(),
        );
    };
    let replacement_ids = [
        state.successor_document_id.as_deref(),
        state.active_owner_document_id.as_deref(),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>();
    let has_exact_active_replacement = !replacement_ids.is_empty()
        && replacement_ids.iter().all(|replacement_id| {
            *replacement_id != document_id && exact_active_document_ids.contains(*replacement_id)
        });
    if matches!(
        state.activity_state,
        ActivityState::Superseded | ActivityState::Retired
    ) && has_exact_active_replacement
    {
        return (
            SearchVisibilityV2::Cold,
            "Reviewed inactive document has exact active replacement evidence; default query filtering is authorized."
                .to_string(),
        );
    }
    if matches!(
        state.activity_state,
        ActivityState::Superseded | ActivityState::Retired
    ) {
        return (
            SearchVisibilityV2::Visible,
            "Reviewed inactive document lacks complete exact active replacement evidence; document remains visible."
                .to_string(),
        );
    }
    (
        SearchVisibilityV2::Visible,
        "Reviewed lifecycle state is current or reviewable; document remains visible.".to_string(),
    )
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogV2AuthorityBoundary {
    pub projection_only: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleProjectionFindingV2 {
    pub code: String,
    pub severity: HygieneSeverity,
    pub document_id: String,
    pub reviewed_observation_id: String,
    pub current_observation_ids: Vec<String>,
    pub conflict: IdentityConflictKind,
    pub message: String,
}

impl LifecycleProjectionFindingV2 {
    fn validate(&self) -> Result<()> {
        validate_identifier(&self.code, "lifecycle finding code")?;
        validate_prefixed_uuid(
            &self.document_id,
            "document_",
            "lifecycle finding document_id",
        )?;
        validate_observation_id(&self.reviewed_observation_id)?;
        validate_safe_text(&self.message, "lifecycle finding message")?;
        let mut current = BTreeSet::new();
        for observation_id in &self.current_observation_ids {
            validate_observation_id(observation_id)?;
            if observation_id == &self.reviewed_observation_id {
                bail!("stale lifecycle finding cannot name the reviewed observation as current");
            }
            if !current.insert(observation_id) {
                bail!("lifecycle finding current observations must be unique");
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentCatalogV2 {
    pub schema: String,
    pub base_catalog: DocumentCatalogV1,
    pub lifecycle: LifecycleProjectionEnvelopeV2,
    pub entries: Vec<DocumentCatalogEntryV2>,
    pub lifecycle_findings: Vec<LifecycleProjectionFindingV2>,
    pub authority_boundary: CatalogV2AuthorityBoundary,
}

impl DocumentCatalogV2 {
    pub fn validate(&self) -> Result<()> {
        self.validate_with_lifecycle_source(None, None)
    }

    pub fn validate_against_lifecycle_load(
        &self,
        load: &DocumentLifecycleLedgerLoadV1,
        current_root_instance_identity_sha256: &str,
    ) -> Result<()> {
        self.validate_with_lifecycle_source(Some(load), Some(current_root_instance_identity_sha256))
    }

    fn validate_with_lifecycle_source(
        &self,
        load: Option<&DocumentLifecycleLedgerLoadV1>,
        current_root_instance_identity_sha256: Option<&str>,
    ) -> Result<()> {
        if self.schema != DOCUMENT_CATALOG_SCHEMA_V2 {
            bail!("unsupported document catalog v2 schema");
        }
        self.base_catalog.validate()?;
        if self
            .base_catalog
            .source_identities
            .lifecycle_ledger
            .is_some()
            || self.base_catalog.entries.iter().any(|entry| {
                entry.document_id.is_some()
                    || entry.activity_state != ActivityState::Unknown
                    || entry.activity_authority.is_some()
                    || entry.retention_disposition != RetentionDisposition::Unclassified
                    || entry.retention_authority.is_some()
            })
        {
            bail!("catalog v2 base must remain the visibility-neutral Phase 1 observation");
        }
        self.lifecycle.validate()?;
        if self.base_catalog.roots.len() != 1 {
            bail!("catalog v2 supports exactly one explicit project root");
        }
        if let Some(expectation) = &self.lifecycle.expectation {
            let root = &self.base_catalog.roots[0];
            if expectation.project_key != self.base_catalog.project_key
                || expectation.root_id != root.root_id
                || expectation.root_path_identity_sha256 != root.path_identity_sha256
            {
                bail!("catalog v2 lifecycle source does not match its current project root");
            }
        }
        if let Some(source_identity) = &self.lifecycle.source_identity {
            if source_identity.observed_at.as_datetime() > &self.base_catalog.generated_at {
                bail!("catalog v2 cannot predate its lifecycle snapshot");
            }
        }
        if self.lifecycle.filtering_authorized && !self.base_catalog.scan.complete {
            bail!("incomplete current catalog cannot authorize lifecycle filtering");
        }
        let source_heads = match (load, current_root_instance_identity_sha256) {
            (Some(load), Some(root_instance)) => {
                Some(self.validate_lifecycle_source(load, root_instance)?)
            }
            (None, None) => None,
            _ => bail!("catalog v2 lifecycle source inputs must be supplied together"),
        };
        let projects_lifecycle_state = self.lifecycle.status.is_valid();
        if projects_lifecycle_state && source_heads.is_none() {
            bail!("lifecycle projection requires a validated supplied ledger load");
        }
        if !self.authority_boundary.projection_only
            || self.authority_boundary.profile_state_write_authorized
            || self.authority_boundary.project_state_write_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("catalog v2 contract must remain a read-only projection");
        }
        if self.entries.len() != self.base_catalog.entries.len() {
            bail!("catalog v2 must project every current observation exactly once");
        }
        let mut projected_document_ids = BTreeSet::new();
        let mut projected_entries_by_document = BTreeMap::new();
        for (entry, base) in self.entries.iter().zip(&self.base_catalog.entries) {
            if &entry.observation != base {
                bail!("catalog v2 entry order and observation bytes must match its base catalog");
            }
            entry.validate(&self.lifecycle, &self.base_catalog.project_key)?;
            if let (Some(document_id), Some(event_id), Some(state)) = (
                &entry.document_id,
                &entry.lifecycle_head_event_id,
                &entry.reviewed_state,
            ) {
                if !projected_document_ids.insert(document_id.as_str()) {
                    bail!("catalog v2 cannot project one document ID more than once");
                }
                projected_entries_by_document.insert(document_id.as_str(), entry);
                let current_observation = DocumentLifecycleObservationV1 {
                    observation_id: entry.observation.observation_id.clone(),
                    relative_path: entry.observation.relative_path.clone(),
                    size_bytes: entry.observation.size_bytes,
                    modified_at: StrictUtcTimestamp::from_datetime(
                        entry
                            .observation
                            .modified_at
                            .context("lifecycle projection requires current modified_at")?,
                    ),
                    content_sha256: entry
                        .observation
                        .content_sha256
                        .clone()
                        .context("lifecycle projection requires current content hash")?,
                };
                current_observation.validate_for_root(&entry.observation.root_id)?;
                let head = source_heads
                    .as_ref()
                    .and_then(|heads| heads.get(document_id))
                    .context("catalog lifecycle document is absent from the supplied ledger")?;
                if event_id != &head.event_id || state != &head.state {
                    bail!("catalog lifecycle head or reviewed state differs from the ledger");
                }
                if entry.lifecycle_stale || current_observation != head.observation {
                    bail!(
                        "catalog durable lifecycle projection requires one exact current observation"
                    );
                }
            }
        }
        let mut findings_by_document = BTreeMap::new();
        for finding in &self.lifecycle_findings {
            finding.validate()?;
            if findings_by_document
                .insert(finding.document_id.as_str(), finding)
                .is_some()
            {
                bail!("catalog v2 requires exactly one finding per stale lifecycle head");
            }
        }
        if !self.lifecycle.status.is_valid() && !self.lifecycle_findings.is_empty() {
            bail!("invalid lifecycle source cannot project document-level findings");
        }
        let finding_current_observations = self
            .lifecycle_findings
            .iter()
            .flat_map(|finding| finding.current_observation_ids.iter().map(String::as_str))
            .collect::<BTreeSet<_>>();
        if self.lifecycle.status.is_valid() {
            for entry in self
                .entries
                .iter()
                .filter(|entry| entry.document_id.is_none())
            {
                let expected_stale = finding_current_observations
                    .contains(entry.observation.observation_id.as_str());
                if entry.lifecycle_stale != expected_stale {
                    bail!("catalog lifecycle staleness must match its current finding evidence");
                }
            }
        }
        if let Some(source_heads) = source_heads.as_ref() {
            let observations_by_id = self
                .base_catalog
                .entries
                .iter()
                .map(|entry| (entry.observation_id.as_str(), entry))
                .collect::<BTreeMap<_, _>>();
            let exact_bound_observation_ids = projected_entries_by_document
                .values()
                .map(|entry| entry.observation.observation_id.clone())
                .collect::<BTreeSet<_>>();
            let mut unprojected_head_counts_by_hash = BTreeMap::<String, usize>::new();
            for (document_id, head) in source_heads {
                if !projected_entries_by_document.contains_key(document_id.as_str())
                    && !self
                        .base_catalog
                        .entries
                        .iter()
                        .any(|entry| entry.relative_path == head.observation.relative_path)
                {
                    *unprojected_head_counts_by_hash
                        .entry(head.observation.content_sha256.clone())
                        .or_default() += 1;
                }
            }
            for (document_id, finding) in &findings_by_document {
                let head = source_heads
                    .get(*document_id)
                    .context("lifecycle finding names a document absent from the ledger")?;
                if finding.reviewed_observation_id != head.observation.observation_id {
                    bail!("lifecycle finding reviewed observation differs from the ledger head");
                }
                if projected_entries_by_document.contains_key(*document_id) {
                    bail!("one lifecycle head cannot be both exact and stale");
                }
                for observation_id in &finding.current_observation_ids {
                    if !observations_by_id.contains_key(observation_id.as_str()) {
                        bail!("lifecycle finding names a non-current observation");
                    }
                }
                validate_lifecycle_finding_evidence(
                    finding,
                    head,
                    &self.base_catalog.entries,
                    self.base_catalog.scan.complete,
                    &exact_bound_observation_ids,
                    unprojected_head_counts_by_hash
                        .get(&head.observation.content_sha256)
                        .copied()
                        .unwrap_or_default(),
                )?;
            }
            if source_heads.keys().any(|document_id| {
                !projected_entries_by_document.contains_key(document_id.as_str())
                    && !findings_by_document.contains_key(document_id.as_str())
            }) || findings_by_document
                .keys()
                .any(|document_id| !source_heads.contains_key(*document_id))
            {
                bail!("catalog v2 must account for every lifecycle head exactly once");
            }
        }
        if self.lifecycle.filtering_authorized {
            let source_heads = source_heads
                .as_ref()
                .expect("filtering authority requires a validated lifecycle source");
            if projected_entries_by_document.len() != source_heads.len()
                || source_heads.keys().any(|document_id| {
                    !projected_entries_by_document.contains_key(document_id.as_str())
                })
                || projected_entries_by_document
                    .values()
                    .any(|entry| entry.lifecycle_stale)
            {
                bail!("lifecycle filtering requires an exact non-stale projection of every ledger head");
            }
            let exact_active_document_ids = projected_entries_by_document
                .iter()
                .filter(|(_, entry)| {
                    entry
                        .reviewed_state
                        .as_ref()
                        .is_some_and(|state| state.activity_state == ActivityState::Active)
                })
                .map(|(document_id, _)| (*document_id).to_string())
                .collect::<BTreeSet<_>>();
            for entry in &self.entries {
                let (expected_visibility, expected_evidence) = lifecycle_filtering_visibility_v2(
                    entry.document_id.as_deref(),
                    entry.reviewed_state.as_ref(),
                    &exact_active_document_ids,
                );
                if entry.visibility != expected_visibility
                    || entry.visibility_evidence != expected_evidence
                {
                    bail!("catalog visibility does not match exact reviewed lifecycle evidence");
                }
            }
            for entry in projected_entries_by_document.values() {
                if entry.visibility != SearchVisibilityV2::Cold {
                    continue;
                }
                let state = entry
                    .reviewed_state
                    .as_ref()
                    .expect("cold entry state presence validated above");
                for replacement_id in [
                    state.successor_document_id.as_deref(),
                    state.active_owner_document_id.as_deref(),
                ]
                .into_iter()
                .flatten()
                {
                    let replacement = projected_entries_by_document
                        .get(replacement_id)
                        .context("cold replacement is absent from the exact current projection")?;
                    if replacement.lifecycle_stale
                        || replacement.visibility != SearchVisibilityV2::Visible
                        || replacement
                            .reviewed_state
                            .as_ref()
                            .is_none_or(|replacement_state| {
                                replacement_state.activity_state != ActivityState::Active
                            })
                    {
                        bail!("cold replacement must be one exact active visible projection");
                    }
                }
            }
        } else if self.lifecycle.status.is_valid()
            && self
                .entries
                .iter()
                .any(|entry| entry.visibility != SearchVisibilityV2::VisibleFailOpen)
        {
            bail!("disabled lifecycle filtering must keep every entry globally fail open");
        }
        Ok(())
    }

    fn validate_lifecycle_source(
        &self,
        load: &DocumentLifecycleLedgerLoadV1,
        current_root_instance_identity_sha256: &str,
    ) -> Result<BTreeMap<String, DocumentHead>> {
        if !self.lifecycle.status.is_valid() || load.status != LifecycleProjectionStatusV1::Valid {
            bail!("catalog lifecycle projection requires one valid complete ledger load");
        }
        load.validate()?;
        validate_sha256(
            current_root_instance_identity_sha256,
            "current root instance identity",
        )?;
        let expectation = self
            .lifecycle
            .expectation
            .as_ref()
            .context("valid catalog lifecycle projection requires root expectation")?;
        if &load.expectation != expectation
            || current_root_instance_identity_sha256 != expectation.root_instance_identity_sha256
            || load.source_identity.as_ref() != self.lifecycle.source_identity.as_ref()
        {
            bail!("catalog lifecycle source is not the exact supplied ledger and current root");
        }
        let mut heads = BTreeMap::new();
        for event in &load.events {
            heads.insert(
                event.payload.document_id.clone(),
                DocumentHead {
                    event_id: event.payload.event_id.clone(),
                    observation: event.payload.observation.clone(),
                    state: event.payload.after.clone(),
                },
            );
        }
        Ok(heads)
    }
}

fn validate_lifecycle_finding_evidence(
    finding: &LifecycleProjectionFindingV2,
    head: &DocumentHead,
    entries: &[DocumentCatalogEntry],
    scan_complete: bool,
    exact_bound_observation_ids: &BTreeSet<String>,
    competing_unprojected_heads: usize,
) -> Result<()> {
    let expected = derive_lifecycle_finding_evidence_v2(
        &head.observation,
        entries,
        scan_complete,
        exact_bound_observation_ids,
        competing_unprojected_heads,
    )?;
    if finding.code != expected.code
        || finding.severity != HygieneSeverity::Warn
        || finding.conflict != expected.conflict
        || finding.current_observation_ids != expected.current_observation_ids
        || finding.message != expected.message
    {
        bail!("lifecycle finding does not match the current observation evidence");
    }
    Ok(())
}

pub(super) struct LifecycleFindingEvidenceV2 {
    pub(super) code: &'static str,
    pub(super) conflict: IdentityConflictKind,
    pub(super) current_observation_ids: Vec<String>,
    pub(super) message: &'static str,
}

pub(super) fn derive_lifecycle_finding_evidence_v2(
    reviewed_observation: &DocumentLifecycleObservationV1,
    entries: &[DocumentCatalogEntry],
    scan_complete: bool,
    exact_bound_observation_ids: &BTreeSet<String>,
    competing_unprojected_heads: usize,
) -> Result<LifecycleFindingEvidenceV2> {
    if entries
        .iter()
        .filter_map(current_lifecycle_observation)
        .any(|observation| observation == *reviewed_observation)
    {
        bail!("an exact current lifecycle observation must be projected, not reported stale");
    }
    let same_path = entries
        .iter()
        .filter(|entry| entry.relative_path == reviewed_observation.relative_path)
        .map(|entry| entry.observation_id.clone())
        .collect::<Vec<_>>();
    let same_hash = entries
        .iter()
        .filter(|entry| {
            entry.content_sha256.as_deref() == Some(reviewed_observation.content_sha256.as_str())
        })
        .map(|entry| entry.observation_id.clone())
        .collect::<Vec<_>>();
    let has_global_identity_conflict = competing_unprojected_heads > 1
        || same_hash
            .iter()
            .any(|observation_id| exact_bound_observation_ids.contains(observation_id));
    let evidence = if !same_path.is_empty() {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_binding_stale_same_path",
            conflict: IdentityConflictKind::StaleLifecycleIdentity,
            current_observation_ids: same_path,
            message:
                "Reviewed lifecycle binding differs from the current observation at its prior path.",
        }
    } else if same_hash.len() > 1 {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_rebind_ambiguous",
            conflict: IdentityConflictKind::MultipleDocumentMatches,
            current_observation_ids: same_hash,
            message:
                "Reviewed bytes match multiple current observations, so identity remains ambiguous.",
        }
    } else if !scan_complete {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_binding_unresolved_incomplete_scan",
            conflict: IdentityConflictKind::MissingSourceIdentity,
            current_observation_ids: same_hash,
            message:
                "The bounded catalog scan is incomplete, so lifecycle binding uniqueness is unresolved.",
        }
    } else if same_hash.len() == 1 && has_global_identity_conflict {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_rebind_identity_conflict",
            conflict: IdentityConflictKind::MultipleDocumentMatches,
            current_observation_ids: same_hash,
            message: "Reviewed bytes map to a current observation that is already bound or claimed by multiple lifecycle documents.",
        }
    } else if same_hash.len() == 1 {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_rebind_candidate",
            conflict: IdentityConflictKind::PathMismatch,
            current_observation_ids: same_hash,
            message:
                "Reviewed bytes have one current path candidate, but rebind approval is required.",
        }
    } else {
        LifecycleFindingEvidenceV2 {
            code: "lifecycle_binding_missing",
            conflict: IdentityConflictKind::StaleLifecycleIdentity,
            current_observation_ids: Vec::new(),
            message: "Reviewed lifecycle binding has no exact current observation.",
        }
    };
    Ok(evidence)
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

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentSearchModeV2 {
    Query,
    ExactDetail,
}

pub(super) const QUERY_COLD_WARNING_V2: &str =
    "This reviewed cold document matched the query and was omitted from default navigation.";
pub(super) const EXACT_COLD_WARNING_V2: &str =
    "This exact document is cold in default navigation but remains retrievable.";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentExactSelectorV2 {
    pub observation_id: Option<String>,
    pub document_id: Option<String>,
    pub relative_path: Option<String>,
}

impl DocumentExactSelectorV2 {
    pub(super) fn validate(&self) -> Result<()> {
        if self.observation_id.is_none()
            && self.document_id.is_none()
            && self.relative_path.is_none()
        {
            bail!("exact detail requires at least one selector");
        }
        if let Some(observation_id) = &self.observation_id {
            validate_observation_id(observation_id)?;
        }
        if let Some(document_id) = &self.document_id {
            validate_prefixed_uuid(document_id, "document_", "exact document selector")?;
        }
        if let Some(relative_path) = &self.relative_path {
            validate_bounded_relative_path(relative_path)?;
        }
        Ok(())
    }

    pub(super) fn matches(&self, entry: &DocumentCatalogEntryV2) -> bool {
        self.observation_id
            .as_ref()
            .is_none_or(|value| value == &entry.observation.observation_id)
            && self
                .document_id
                .as_ref()
                .is_none_or(|value| entry.document_id.as_ref() == Some(value))
            && self
                .relative_path
                .as_ref()
                .is_none_or(|value| value == &entry.observation.relative_path)
    }

    pub(super) fn component_matches_any(&self, entries: &[DocumentCatalogEntryV2]) -> bool {
        self.observation_id.as_ref().is_some_and(|value| {
            entries
                .iter()
                .any(|entry| &entry.observation.observation_id == value)
        }) || self.document_id.as_ref().is_some_and(|value| {
            entries
                .iter()
                .any(|entry| entry.document_id.as_ref() == Some(value))
        }) || self.relative_path.as_ref().is_some_and(|value| {
            entries
                .iter()
                .any(|entry| &entry.observation.relative_path == value)
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentSearchMatchV2 {
    pub entry: DocumentCatalogEntryV2,
    pub match_kinds: Vec<DocumentMatchKindV2>,
    pub snippets: Vec<String>,
    pub cold_warning: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentMatchKindV2 {
    RelativePath,
    FileName,
    Body,
    ObservationId,
    DocumentId,
    ExactRelativePath,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NegativeSearchEvidenceV2 {
    pub content_absence_established: bool,
    pub owner_absence_authorized: bool,
    pub reason: String,
    pub metadata_only_families: Vec<MediaFamily>,
}

pub(super) fn expected_negative_search_evidence_v2(
    mode: DocumentSearchModeV2,
    prefilter_match_count: u64,
    catalog: &DocumentCatalogV2,
) -> NegativeSearchEvidenceV2 {
    let metadata_only_families = catalog
        .base_catalog
        .coverage
        .iter()
        .filter(|coverage| coverage.observed_files > 0)
        .filter(|coverage| coverage.body_coverage != BodyCoverage::Complete)
        .map(|coverage| coverage.media_family)
        .collect::<Vec<_>>();
    let full_body_coverage = catalog
        .base_catalog
        .coverage
        .iter()
        .all(|coverage| coverage.body_coverage == BodyCoverage::Complete);
    let content_absence_established = mode == DocumentSearchModeV2::Query
        && prefilter_match_count == 0
        && catalog.base_catalog.scan.complete
        && full_body_coverage
        && metadata_only_families.is_empty();
    let reason = if mode == DocumentSearchModeV2::ExactDetail {
        "The exact selector does not resolve to a current observation."
    } else if prefilter_match_count > 0 {
        "Matching cold documents were omitted from default navigation; no content or owner absence is established."
    } else if !catalog.base_catalog.scan.complete {
        "The bounded scan is incomplete, so no content or owner absence is established."
    } else if !metadata_only_families.is_empty() {
        "One or more observed media families lack complete body extraction."
    } else {
        "The live bounded scan completed with full body coverage and found no matching content."
    };
    NegativeSearchEvidenceV2 {
        content_absence_established,
        owner_absence_authorized: false,
        reason: reason.to_string(),
        metadata_only_families,
    }
}

pub(super) fn compare_search_entries_v2(
    left: &DocumentCatalogEntryV2,
    right: &DocumentCatalogEntryV2,
) -> std::cmp::Ordering {
    search_entry_rank_v2(left)
        .cmp(&search_entry_rank_v2(right))
        .then_with(|| {
            left.observation
                .relative_path
                .cmp(&right.observation.relative_path)
        })
        .then_with(|| {
            left.observation
                .observation_id
                .cmp(&right.observation.observation_id)
        })
}

fn search_entry_rank_v2(entry: &DocumentCatalogEntryV2) -> u8 {
    if entry
        .reviewed_state
        .as_ref()
        .is_some_and(|state| state.activity_state == ActivityState::Active)
    {
        0
    } else if entry.visibility == SearchVisibilityV2::Cold {
        2
    } else {
        1
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SearchV2AuthorityBoundary {
    pub read_only: bool,
    pub lifecycle_filtering_applied: bool,
    pub search_visibility_mutation_authorized: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentSearchV2 {
    pub schema: String,
    pub generated_at: StrictUtcTimestamp,
    pub mode: DocumentSearchModeV2,
    pub query: Option<String>,
    pub exact_selector: Option<DocumentExactSelectorV2>,
    pub include_cold: bool,
    pub catalog: DocumentCatalogV2,
    pub prefilter_match_count: u64,
    pub visible_match_count: u64,
    pub cold_matches_omitted: u64,
    pub matches: Vec<DocumentSearchMatchV2>,
    pub omitted_cold_matches: Vec<DocumentSearchMatchV2>,
    pub negative_evidence: Option<NegativeSearchEvidenceV2>,
    pub authority_boundary: SearchV2AuthorityBoundary,
}

impl DocumentSearchV2 {
    pub fn validate(&self) -> Result<()> {
        self.validate_with_lifecycle_source(None, None, None)
    }

    pub fn validate_against_lifecycle_load(
        &self,
        load: &DocumentLifecycleLedgerLoadV1,
        current_root_instance_identity_sha256: &str,
    ) -> Result<()> {
        self.validate_with_lifecycle_source(
            Some(load),
            Some(current_root_instance_identity_sha256),
            None,
        )
    }

    pub fn validate_against_live_bodies(&self, bodies: &BTreeMap<String, String>) -> Result<()> {
        self.validate_with_lifecycle_source(None, None, Some(bodies))
    }

    pub fn validate_against_lifecycle_and_live_bodies(
        &self,
        load: &DocumentLifecycleLedgerLoadV1,
        current_root_instance_identity_sha256: &str,
        bodies: &BTreeMap<String, String>,
    ) -> Result<()> {
        self.validate_with_lifecycle_source(
            Some(load),
            Some(current_root_instance_identity_sha256),
            Some(bodies),
        )
    }

    fn validate_with_lifecycle_source(
        &self,
        load: Option<&DocumentLifecycleLedgerLoadV1>,
        current_root_instance_identity_sha256: Option<&str>,
        bodies: Option<&BTreeMap<String, String>>,
    ) -> Result<()> {
        if self.schema != DOCUMENT_SEARCH_SCHEMA_V2 {
            bail!("unsupported document search v2 schema");
        }
        match (load, current_root_instance_identity_sha256) {
            (Some(load), Some(root_instance)) => self
                .catalog
                .validate_against_lifecycle_load(load, root_instance)?,
            (None, None) => self.catalog.validate()?,
            _ => bail!("search v2 lifecycle source inputs must be supplied together"),
        }
        if let Some(bodies) = bodies {
            validate_search_body_source_v2(&self.catalog, bodies)?;
        }
        if self.generated_at.as_datetime() < &self.catalog.base_catalog.generated_at {
            bail!("search v2 cannot predate its current catalog snapshot");
        }
        if !self.authority_boundary.read_only
            || self
                .authority_boundary
                .search_visibility_mutation_authorized
            || self.authority_boundary.profile_state_write_authorized
            || self.authority_boundary.project_state_write_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("document search v2 exceeds its read-only authority");
        }
        let returned_cold = self
            .matches
            .iter()
            .filter(|result| result.entry.visibility == SearchVisibilityV2::Cold)
            .count() as u64;
        if self.omitted_cold_matches.len() as u64 != self.cold_matches_omitted {
            bail!("cold omission count must equal its exact omitted match evidence");
        }
        let returned_visible = self.matches.len() as u64 - returned_cold;
        if returned_visible != self.visible_match_count
            || self.prefilter_match_count
                != self.visible_match_count + returned_cold + self.cold_matches_omitted
        {
            bail!("search v2 pre-filter and post-filter counts are inconsistent");
        }
        if self.cold_matches_omitted > 0
            && (!self.catalog.lifecycle.filtering_authorized || self.include_cold)
        {
            bail!("cold omission requires exact filtering authority and default query mode");
        }
        if self.authority_boundary.lifecycle_filtering_applied != (self.cold_matches_omitted > 0) {
            bail!("search authority must report whether cold matches were omitted");
        }
        match self.mode {
            DocumentSearchModeV2::Query => {
                if self
                    .query
                    .as_deref()
                    .is_none_or(|query| query.trim().is_empty())
                    || self.exact_selector.is_some()
                {
                    bail!("query mode requires only one non-empty query");
                }
                validate_safe_text(
                    self.query.as_deref().expect("non-empty query checked"),
                    "document search query",
                )?;
                if self.include_cold && self.cold_matches_omitted != 0 {
                    bail!("include-cold query cannot omit cold matches");
                }
                if !self.include_cold && returned_cold != 0 {
                    bail!("default query cannot return a reviewed cold match");
                }
                if bodies.is_none()
                    && self
                        .catalog
                        .base_catalog
                        .coverage
                        .iter()
                        .any(|coverage| coverage.body_searchable_files > 0)
                {
                    bail!("query validation requires the exact live body-source result");
                }
            }
            DocumentSearchModeV2::ExactDetail => {
                let selector = self
                    .exact_selector
                    .as_ref()
                    .context("exact detail requires a selector")?;
                selector.validate()?;
                if self.query.is_some()
                    || self.include_cold
                    || self.matches.len() > 1
                    || self.prefilter_match_count > 1
                    || self.cold_matches_omitted != 0
                    || self
                        .matches
                        .first()
                        .is_some_and(|result| !selector.matches(&result.entry))
                {
                    bail!("exact selectors resolve to at most one current entry without filtering");
                }
                let resolved = self
                    .catalog
                    .entries
                    .iter()
                    .filter(|entry| selector.matches(entry))
                    .collect::<Vec<_>>();
                if resolved.len() > 1
                    || resolved.len() != self.matches.len()
                    || resolved
                        .first()
                        .zip(self.matches.first())
                        .is_some_and(|(entry, result_match)| *entry != &result_match.entry)
                    || (resolved.is_empty()
                        && selector.component_matches_any(&self.catalog.entries))
                {
                    bail!("exact selectors must resolve completely to the same current entry");
                }
            }
        }
        let mut matched_observations = BTreeSet::new();
        for result_match in &self.matches {
            if result_match.match_kinds.is_empty()
                || !matched_observations
                    .insert(result_match.entry.observation.observation_id.as_str())
                || result_match.entry.observation.project_key
                    != self.catalog.base_catalog.project_key
                || !self
                    .catalog
                    .entries
                    .iter()
                    .any(|entry| entry == &result_match.entry)
            {
                bail!("search v2 match is not bound to the current catalog");
            }
            let kinds = result_match
                .match_kinds
                .iter()
                .copied()
                .collect::<BTreeSet<_>>();
            if kinds.iter().copied().collect::<Vec<_>>() != result_match.match_kinds {
                bail!("search v2 match kinds must be unique and canonically ordered");
            }
            match self.mode {
                DocumentSearchModeV2::Query => {
                    if kinds.iter().any(|kind| {
                        matches!(
                            kind,
                            DocumentMatchKindV2::ObservationId
                                | DocumentMatchKindV2::DocumentId
                                | DocumentMatchKindV2::ExactRelativePath
                        )
                    }) {
                        bail!("query matches cannot claim exact selector evidence");
                    }
                    validate_query_match_claims(
                        self.query.as_deref().expect("query validated above"),
                        result_match,
                        &kinds,
                        bodies.and_then(|bodies| {
                            bodies
                                .get(&result_match.entry.observation.observation_id)
                                .map(String::as_str)
                        }),
                    )?;
                }
                DocumentSearchModeV2::ExactDetail => {
                    let selector = self
                        .exact_selector
                        .as_ref()
                        .expect("exact selector validated above");
                    if selector.observation_id.is_some()
                        != kinds.contains(&DocumentMatchKindV2::ObservationId)
                        || selector.document_id.is_some()
                            != kinds.contains(&DocumentMatchKindV2::DocumentId)
                        || selector.relative_path.is_some()
                            != kinds.contains(&DocumentMatchKindV2::ExactRelativePath)
                        || kinds.iter().any(|kind| {
                            matches!(
                                kind,
                                DocumentMatchKindV2::RelativePath
                                    | DocumentMatchKindV2::FileName
                                    | DocumentMatchKindV2::Body
                            )
                        })
                        || !result_match.snippets.is_empty()
                    {
                        bail!("exact detail match kinds must mirror its exact selectors");
                    }
                }
            }
            if kinds.contains(&DocumentMatchKindV2::Body) == result_match.snippets.is_empty() {
                bail!("body match evidence and snippets must be present together");
            }
            for snippet in &result_match.snippets {
                validate_safe_text(snippet, "search result snippet")?;
            }
            let is_cold = result_match.entry.visibility == SearchVisibilityV2::Cold;
            let expected_warning = if is_cold {
                Some(match self.mode {
                    DocumentSearchModeV2::Query => QUERY_COLD_WARNING_V2,
                    DocumentSearchModeV2::ExactDetail => EXACT_COLD_WARNING_V2,
                })
            } else {
                None
            };
            if result_match.cold_warning.as_deref() != expected_warning {
                bail!("cold result warning does not match its exact retrieval mode");
            }
        }
        for omitted_match in &self.omitted_cold_matches {
            if self.mode != DocumentSearchModeV2::Query
                || omitted_match.entry.visibility != SearchVisibilityV2::Cold
                || omitted_match.match_kinds.is_empty()
                || !matched_observations
                    .insert(omitted_match.entry.observation.observation_id.as_str())
                || !self
                    .catalog
                    .entries
                    .iter()
                    .any(|entry| entry == &omitted_match.entry)
            {
                bail!("omitted cold match is not one distinct current cold query result");
            }
            let kinds = omitted_match
                .match_kinds
                .iter()
                .copied()
                .collect::<BTreeSet<_>>();
            if kinds.iter().copied().collect::<Vec<_>>() != omitted_match.match_kinds
                || kinds.iter().any(|kind| {
                    matches!(
                        kind,
                        DocumentMatchKindV2::ObservationId
                            | DocumentMatchKindV2::DocumentId
                            | DocumentMatchKindV2::ExactRelativePath
                    )
                })
                || kinds.contains(&DocumentMatchKindV2::Body) == omitted_match.snippets.is_empty()
            {
                bail!("omitted cold match evidence is inconsistent");
            }
            validate_query_match_claims(
                self.query.as_deref().expect("query validated above"),
                omitted_match,
                &kinds,
                bodies.and_then(|bodies| {
                    bodies
                        .get(&omitted_match.entry.observation.observation_id)
                        .map(String::as_str)
                }),
            )?;
            for snippet in &omitted_match.snippets {
                validate_safe_text(snippet, "omitted cold match snippet")?;
            }
            if omitted_match.cold_warning.as_deref() != Some(QUERY_COLD_WARNING_V2) {
                bail!("omitted cold match requires the canonical query warning");
            }
        }
        if self.matches.windows(2).any(|pair| {
            compare_search_entries_v2(&pair[0].entry, &pair[1].entry) == std::cmp::Ordering::Greater
        }) || self.omitted_cold_matches.windows(2).any(|pair| {
            compare_search_entries_v2(&pair[0].entry, &pair[1].entry) == std::cmp::Ordering::Greater
        }) {
            bail!("search v2 results must use deterministic active-first ordering");
        }
        if self.mode == DocumentSearchModeV2::Query {
            let normalized_query = self
                .query
                .as_deref()
                .expect("query validated above")
                .to_lowercase();
            for entry in &self.catalog.entries {
                let relative_path = entry.observation.relative_path.to_lowercase();
                let file_name = Path::new(&entry.observation.relative_path)
                    .file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or_default()
                    .to_lowercase();
                let body_matches = bodies
                    .and_then(|bodies| bodies.get(&entry.observation.observation_id))
                    .is_some_and(|body| !matching_snippets_v2(body, &normalized_query).is_empty());
                if (relative_path.contains(&normalized_query)
                    || file_name.contains(&normalized_query)
                    || body_matches)
                    && !matched_observations.contains(entry.observation.observation_id.as_str())
                {
                    bail!("live query match is missing from pre-filter evidence");
                }
            }
        }
        if self.matches.is_empty() != self.negative_evidence.is_some() {
            bail!("negative evidence is present exactly when post-filter results are empty");
        }
        if let Some(negative) = &self.negative_evidence {
            validate_safe_text(&negative.reason, "negative evidence reason")?;
            let expected = expected_negative_search_evidence_v2(
                self.mode,
                self.prefilter_match_count,
                &self.catalog,
            );
            if negative != &expected {
                bail!("negative evidence does not match the exact pre-filter evidence");
            }
        }
        Ok(())
    }
}

fn validate_query_match_claims(
    query: &str,
    result_match: &DocumentSearchMatchV2,
    kinds: &BTreeSet<DocumentMatchKindV2>,
    body: Option<&str>,
) -> Result<()> {
    let normalized_query = query.to_lowercase();
    let relative_path = result_match.entry.observation.relative_path.to_lowercase();
    let file_name = Path::new(&result_match.entry.observation.relative_path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default()
        .to_lowercase();
    let expected_snippets = body
        .map(|body| matching_snippets_v2(body, &normalized_query))
        .unwrap_or_default();
    if kinds.contains(&DocumentMatchKindV2::RelativePath)
        != relative_path.contains(&normalized_query)
        || kinds.contains(&DocumentMatchKindV2::FileName) != file_name.contains(&normalized_query)
        || kinds.contains(&DocumentMatchKindV2::Body) == expected_snippets.is_empty()
        || result_match.snippets != expected_snippets
    {
        bail!("query match evidence must equal the exact live path, filename, and body matches");
    }
    for kind in kinds {
        let supported = match kind {
            DocumentMatchKindV2::RelativePath => relative_path.contains(&normalized_query),
            DocumentMatchKindV2::FileName => file_name.contains(&normalized_query),
            DocumentMatchKindV2::Body => !expected_snippets.is_empty(),
            DocumentMatchKindV2::ObservationId
            | DocumentMatchKindV2::DocumentId
            | DocumentMatchKindV2::ExactRelativePath => false,
        };
        if !supported {
            bail!("query match kind is not supported by the exact match evidence");
        }
    }
    Ok(())
}

fn validate_search_body_source_v2(
    catalog: &DocumentCatalogV2,
    bodies: &BTreeMap<String, String>,
) -> Result<()> {
    let entries_by_observation = catalog
        .entries
        .iter()
        .map(|entry| (entry.observation.observation_id.as_str(), entry))
        .collect::<BTreeMap<_, _>>();
    let mut body_counts_by_family = BTreeMap::<MediaFamily, u64>::new();
    let mut extracted_bytes = 0u64;
    for (observation_id, body) in bodies {
        let entry = entries_by_observation
            .get(observation_id.as_str())
            .with_context(|| {
                format!("live body references unknown observation {observation_id}")
            })?;
        let body_bytes = body.as_bytes();
        let body_sha256 = format!("{:x}", Sha256::digest(body_bytes));
        if entry.observation.size_bytes != body_bytes.len() as u64
            || entry.observation.content_sha256.as_deref() != Some(body_sha256.as_str())
        {
            bail!("live body does not match its exact current observation");
        }
        *body_counts_by_family
            .entry(entry.observation.media_family)
            .or_default() += 1;
        extracted_bytes = extracted_bytes
            .checked_add(body_bytes.len() as u64)
            .context("live extracted-body byte count overflow")?;
    }
    for coverage in &catalog.base_catalog.coverage {
        if body_counts_by_family
            .get(&coverage.media_family)
            .copied()
            .unwrap_or_default()
            != coverage.body_extracted_files
        {
            bail!("live body source does not match catalog extraction coverage");
        }
    }
    if extracted_bytes != catalog.base_catalog.scan.usage.bytes_extracted {
        bail!("live body source does not match catalog extracted-byte usage");
    }
    Ok(())
}

pub(super) fn matching_snippets_v2(body: &str, normalized_query: &str) -> Vec<String> {
    body.lines()
        .filter(|line| line.to_lowercase().contains(normalized_query))
        .take(3)
        .map(|line| matching_line_window_v2(line.trim(), normalized_query))
        .collect()
}

fn matching_line_window_v2(line: &str, normalized_query: &str) -> String {
    let normalized_line = line.to_lowercase();
    let Some(match_byte) = normalized_line.find(normalized_query) else {
        return truncate_chars_v2(line, 240);
    };
    let max_chars = 240usize.max(normalized_query.chars().count()).min(4096);
    let match_char = normalized_line[..match_byte].chars().count();
    let query_chars = normalized_query.chars().count();
    let line_chars = line.chars().collect::<Vec<_>>();
    if line_chars.len() <= max_chars {
        return line.to_string();
    }
    let mut start = match_char.saturating_sub(max_chars.saturating_sub(query_chars) / 2);
    let end = (start + max_chars).min(line_chars.len());
    start = end.saturating_sub(max_chars);
    let candidate = line_chars[start..end].iter().collect::<String>();
    if candidate.to_lowercase().contains(normalized_query) {
        return candidate;
    }
    let normalized_chars = normalized_line.chars().collect::<Vec<_>>();
    let mut normalized_start = match_char.saturating_sub(max_chars.saturating_sub(query_chars) / 2);
    let normalized_end = (normalized_start + max_chars).min(normalized_chars.len());
    normalized_start = normalized_end.saturating_sub(max_chars);
    normalized_chars[normalized_start..normalized_end]
        .iter()
        .collect()
}

fn truncate_chars_v2(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        return value.to_string();
    }
    let mut truncated = value
        .chars()
        .take(max_chars.saturating_sub(3))
        .collect::<String>();
    truncated.push_str("...");
    truncated
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleLedgerLoadBudgets {
    pub max_events: u64,
    pub max_directory_entries: u64,
    pub max_event_bytes: u64,
    pub max_total_bytes: u64,
    pub max_elapsed_ms: u64,
}

impl Default for LifecycleLedgerLoadBudgets {
    fn default() -> Self {
        Self {
            max_events: DEFAULT_LIFECYCLE_MAX_EVENTS,
            max_directory_entries: DEFAULT_LIFECYCLE_MAX_DIRECTORY_ENTRIES,
            max_event_bytes: DEFAULT_LIFECYCLE_MAX_EVENT_BYTES,
            max_total_bytes: DEFAULT_LIFECYCLE_MAX_TOTAL_BYTES,
            max_elapsed_ms: DEFAULT_LIFECYCLE_MAX_ELAPSED_MS,
        }
    }
}

impl LifecycleLedgerLoadBudgets {
    fn validate(&self) -> Result<()> {
        if self.max_events == 0
            || self.max_directory_entries == 0
            || self.max_event_bytes == 0
            || self.max_total_bytes == 0
            || self.max_elapsed_ms == 0
            || self.max_event_bytes > self.max_total_bytes
        {
            bail!("lifecycle loader budgets must be positive and internally bounded");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LifecycleLedgerLoadOptions {
    pub budgets: LifecycleLedgerLoadBudgets,
    pub observed_at: StrictUtcTimestamp,
}

impl Default for LifecycleLedgerLoadOptions {
    fn default() -> Self {
        Self {
            budgets: LifecycleLedgerLoadBudgets::default(),
            observed_at: StrictUtcTimestamp::from_datetime(Utc::now()),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleLedgerLoadV1 {
    pub schema: String,
    pub status: LifecycleProjectionStatusV1,
    pub expectation: LifecycleLedgerExpectationV1,
    pub budgets: LifecycleLedgerLoadBudgets,
    pub source_identity: Option<LifecycleLedgerSnapshotIdentityV1>,
    pub events: Vec<DocumentLifecycleEventV1>,
    pub failure_code: Option<String>,
    pub lifecycle_projection_authorized: bool,
    pub search_filtering_authorized: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

impl DocumentLifecycleLedgerLoadV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA {
            bail!("unsupported document lifecycle ledger load schema");
        }
        self.expectation.validate()?;
        self.budgets.validate()?;
        if self.lifecycle_projection_authorized
            || self.search_filtering_authorized
            || self.profile_state_write_authorized
            || self.project_state_write_authorized
            || self.file_mutation_authorized
        {
            bail!("Phase 2A loader cannot grant production or mutation authority");
        }
        if self.status.is_valid() {
            let summary = validate_document_lifecycle_event_chain(&self.events, &self.expectation)?;
            let source = self
                .source_identity
                .as_ref()
                .context("valid lifecycle load requires an exact source identity")?;
            source.validate()?;
            if source.first_sequence != summary.first_sequence
                || source.last_sequence != summary.last_sequence
                || source.event_count != summary.event_count
                || source.final_event_sha256 != summary.final_event_sha256
                || source.ordered_event_hashes_sha256 != summary.ordered_event_hashes_sha256
                || self.failure_code.is_some()
            {
                bail!("valid lifecycle load source identity does not match its events");
            }
            if self
                .events
                .last()
                .is_some_and(|event| event.payload.recorded_at > source.observed_at)
            {
                bail!("lifecycle snapshot cannot predate its final event");
            }
        } else if !self.events.is_empty() || self.source_identity.is_some() {
            bail!("invalid lifecycle load cannot project a previously valid prefix");
        } else if self.status == LifecycleProjectionStatusV1::Absent {
            if self.failure_code.is_some() {
                bail!("absent lifecycle ledger is not an integrity failure");
            }
        } else if self
            .failure_code
            .as_deref()
            .is_none_or(|code| code.trim().is_empty())
        {
            bail!("invalid lifecycle load requires a bounded failure code");
        } else if let Some(code) = &self.failure_code {
            validate_identifier(code, "lifecycle load failure_code")?;
        }
        Ok(())
    }
}

#[derive(Debug)]
struct LoadFailure {
    status: LifecycleProjectionStatusV1,
    code: &'static str,
}

#[derive(Debug)]
struct LoadedEventSnapshot {
    relative_path: PathBuf,
    snapshot: crate::process::FilesystemObjectSnapshot,
}

impl LoadFailure {
    fn new(status: LifecycleProjectionStatusV1, code: &'static str) -> Self {
        Self { status, code }
    }

    fn unsafe_storage(code: &'static str) -> Self {
        Self::new(LifecycleProjectionStatusV1::UnsafeStorage, code)
    }

    fn corrupt(code: &'static str) -> Self {
        Self::new(LifecycleProjectionStatusV1::Corrupt, code)
    }

    fn budget(code: &'static str) -> Self {
        Self::new(LifecycleProjectionStatusV1::BudgetExceeded, code)
    }
}

pub fn load_document_lifecycle_ledger_from_root(
    supplied_profile_root: &Path,
    expectation: &LifecycleLedgerExpectationV1,
    options: &LifecycleLedgerLoadOptions,
) -> Result<DocumentLifecycleLedgerLoadV1> {
    load_document_lifecycle_ledger_with_hook(
        supplied_profile_root,
        expectation,
        options,
        &mut |_| {},
    )
}

fn load_document_lifecycle_ledger_with_hook<F>(
    supplied_profile_root: &Path,
    expectation: &LifecycleLedgerExpectationV1,
    options: &LifecycleLedgerLoadOptions,
    hook: &mut F,
) -> Result<DocumentLifecycleLedgerLoadV1>
where
    F: FnMut(Option<usize>),
{
    expectation.validate()?;
    options.budgets.validate()?;
    let result =
        load_document_lifecycle_ledger_inner(supplied_profile_root, expectation, options, hook);
    let report = match result {
        Ok(None) => lifecycle_load_report(
            LifecycleProjectionStatusV1::Absent,
            expectation,
            options,
            None,
            Vec::new(),
            None,
        ),
        Ok(Some((events, summary))) => lifecycle_load_report(
            LifecycleProjectionStatusV1::Valid,
            expectation,
            options,
            Some(summary.snapshot(options.observed_at.clone())),
            events,
            None,
        ),
        Err(failure) => lifecycle_load_report(
            failure.status,
            expectation,
            options,
            None,
            Vec::new(),
            Some(failure.code.to_string()),
        ),
    };
    report.validate()?;
    Ok(report)
}

fn lifecycle_load_report(
    status: LifecycleProjectionStatusV1,
    expectation: &LifecycleLedgerExpectationV1,
    options: &LifecycleLedgerLoadOptions,
    source_identity: Option<LifecycleLedgerSnapshotIdentityV1>,
    events: Vec<DocumentLifecycleEventV1>,
    failure_code: Option<String>,
) -> DocumentLifecycleLedgerLoadV1 {
    DocumentLifecycleLedgerLoadV1 {
        schema: DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA.to_string(),
        status,
        expectation: expectation.clone(),
        budgets: options.budgets.clone(),
        source_identity,
        events,
        failure_code,
        lifecycle_projection_authorized: false,
        search_filtering_authorized: false,
        profile_state_write_authorized: false,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    }
}

fn load_document_lifecycle_ledger_inner<F>(
    supplied_profile_root: &Path,
    expectation: &LifecycleLedgerExpectationV1,
    options: &LifecycleLedgerLoadOptions,
    hook: &mut F,
) -> std::result::Result<
    Option<(Vec<DocumentLifecycleEventV1>, DocumentLifecycleChainSummary)>,
    LoadFailure,
>
where
    F: FnMut(Option<usize>),
{
    let started = Instant::now();
    let path_metadata = fs::symlink_metadata(supplied_profile_root)
        .map_err(|_| LoadFailure::unsafe_storage("profile_root_unavailable"))?;
    if path_metadata.file_type().is_symlink() || !path_metadata.is_dir() {
        return Err(LoadFailure::unsafe_storage(
            "profile_root_not_safe_directory",
        ));
    }
    let root = crate::process::open_bounded_root(supplied_profile_root)
        .map_err(|_| LoadFailure::unsafe_storage("profile_root_bounded_open_failed"))?;
    let root_snapshot = crate::process::FilesystemObjectSnapshot::from_metadata(
        &root
            .metadata()
            .map_err(|_| LoadFailure::unsafe_storage("profile_root_metadata_failed"))?,
    );
    if root_snapshot != crate::process::FilesystemObjectSnapshot::from_metadata(&path_metadata) {
        return Err(LoadFailure::unsafe_storage(
            "profile_root_replaced_during_open",
        ));
    }

    let mut entries_observed = 0_u64;
    let lifecycle = match read_ledger_directory(
        &root,
        Path::new("document-lifecycle"),
        started,
        options,
        &mut entries_observed,
    ) {
        Ok(directory) => directory,
        Err(failure)
            if failure.status == LifecycleProjectionStatusV1::UnsafeStorage
                && failure.code == "ledger_directory_missing" =>
        {
            revalidate_supplied_root(supplied_profile_root, &root_snapshot)?;
            return Ok(None);
        }
        Err(failure) => return Err(failure),
    };
    if lifecycle.names != [std::ffi::OsString::from("v1")] {
        return Err(LoadFailure::corrupt("ledger_root_layout_invalid"));
    }

    let version = read_ledger_directory(
        &root,
        Path::new("document-lifecycle/v1"),
        started,
        options,
        &mut entries_observed,
    )?;
    let version_names = version
        .names
        .iter()
        .map(|name| name.to_string_lossy().to_string())
        .collect::<BTreeSet<_>>();
    if !version_names.contains("events")
        || !version_names
            .iter()
            .all(|name| name == "events" || name == "ledger.lock")
    {
        return Err(LoadFailure::corrupt("ledger_version_layout_invalid"));
    }
    if version_names.contains("ledger.lock") {
        validate_safe_ledger_file(&root, Path::new("document-lifecycle/v1/ledger.lock"))?;
    }

    let event_directory = read_ledger_directory(
        &root,
        Path::new("document-lifecycle/v1/events"),
        started,
        options,
        &mut entries_observed,
    )?;
    hook(None);
    if event_directory.names.iter().any(|name| {
        let name = name.to_string_lossy();
        name.ends_with(".partial") || name.ends_with(".tmp")
    }) {
        return Err(LoadFailure::new(
            LifecycleProjectionStatusV1::PartialWritePresent,
            "partial_event_present",
        ));
    }
    if event_directory.names.is_empty() {
        revalidate_directory(&root, Path::new("document-lifecycle"), &lifecycle.snapshot)?;
        revalidate_directory(&root, Path::new("document-lifecycle/v1"), &version.snapshot)?;
        revalidate_directory(
            &root,
            Path::new("document-lifecycle/v1/events"),
            &event_directory.snapshot,
        )?;
        revalidate_supplied_root(supplied_profile_root, &root_snapshot)?;
        return Ok(None);
    }
    if event_directory.names.len() as u64 > options.budgets.max_events {
        return Err(LoadFailure::budget("event_count_budget_exceeded"));
    }

    let mut total_bytes = 0_u64;
    let mut events = Vec::with_capacity(event_directory.names.len());
    let mut event_snapshots = Vec::with_capacity(event_directory.names.len());
    for (index, name) in event_directory.names.iter().enumerate() {
        ensure_elapsed(started, options)?;
        let name = name
            .to_str()
            .ok_or_else(|| LoadFailure::corrupt("event_filename_not_utf8"))?;
        let relative_path = Path::new("document-lifecycle/v1/events").join(name);
        let mut file = crate::process::open_bounded_regular_file(&root, &relative_path)
            .map_err(|_| LoadFailure::unsafe_storage("event_bounded_open_failed"))?;
        let initial_metadata = file
            .metadata()
            .map_err(|_| LoadFailure::unsafe_storage("event_metadata_failed"))?;
        if !initial_metadata.is_file() || metadata_link_count(&initial_metadata) != Some(1) {
            return Err(LoadFailure::unsafe_storage("event_not_single_regular_file"));
        }
        if initial_metadata.len() > options.budgets.max_event_bytes {
            return Err(LoadFailure::budget("per_event_byte_budget_exceeded"));
        }
        if total_bytes.saturating_add(initial_metadata.len()) > options.budgets.max_total_bytes {
            return Err(LoadFailure::budget("total_event_byte_budget_exceeded"));
        }
        let initial_snapshot =
            crate::process::FilesystemObjectSnapshot::from_metadata(&initial_metadata);
        let bytes = read_event_bytes(&mut file, started, options, total_bytes)?;
        total_bytes = total_bytes.saturating_add(bytes.len() as u64);
        hook(Some(index));
        let final_snapshot = crate::process::FilesystemObjectSnapshot::from_metadata(
            &file
                .metadata()
                .map_err(|_| LoadFailure::unsafe_storage("event_final_metadata_failed"))?,
        );
        let reopened = crate::process::open_bounded_regular_file(&root, &relative_path)
            .map_err(|_| LoadFailure::unsafe_storage("event_reopen_failed"))?;
        let reopened_metadata = reopened
            .metadata()
            .map_err(|_| LoadFailure::unsafe_storage("event_reopen_metadata_failed"))?;
        let reopened_snapshot =
            crate::process::FilesystemObjectSnapshot::from_metadata(&reopened_metadata);
        if initial_snapshot != final_snapshot
            || initial_snapshot != reopened_snapshot
            || metadata_link_count(&reopened_metadata) != Some(1)
            || initial_metadata.len() != bytes.len() as u64
        {
            return Err(LoadFailure::unsafe_storage("event_replaced_during_read"));
        }
        event_snapshots.push(LoadedEventSnapshot {
            relative_path: relative_path.clone(),
            snapshot: initial_snapshot,
        });

        let event: DocumentLifecycleEventV1 = serde_json::from_slice(&bytes)
            .map_err(|_| LoadFailure::corrupt("event_contract_invalid"))?;
        if event.schema != DOCUMENT_LIFECYCLE_EVENT_SCHEMA
            || event.payload.approval_source.schema != DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA
        {
            return Err(LoadFailure::new(
                LifecycleProjectionStatusV1::UnsupportedSchema,
                "event_schema_unsupported",
            ));
        }
        event
            .validate()
            .map_err(|_| LoadFailure::corrupt("event_validation_failed"))?;
        if lifecycle_event_filename(&event) != name {
            return Err(LoadFailure::corrupt("event_filename_mismatch"));
        }
        if !expectation.matches(&event.payload) {
            return Err(LoadFailure::new(
                LifecycleProjectionStatusV1::IdentityMismatch,
                "event_identity_mismatch",
            ));
        }
        events.push(event);
    }

    revalidate_event_files(&root, &event_snapshots, started, options)?;
    revalidate_directory(&root, Path::new("document-lifecycle"), &lifecycle.snapshot)?;
    revalidate_directory(&root, Path::new("document-lifecycle/v1"), &version.snapshot)?;
    revalidate_directory(
        &root,
        Path::new("document-lifecycle/v1/events"),
        &event_directory.snapshot,
    )?;
    revalidate_supplied_root(supplied_profile_root, &root_snapshot)?;
    ensure_elapsed(started, options)?;
    let summary = validate_document_lifecycle_event_chain(&events, expectation)
        .map_err(|_| LoadFailure::corrupt("event_chain_invalid"))?;
    if events
        .last()
        .is_some_and(|event| event.payload.recorded_at > options.observed_at)
    {
        return Err(LoadFailure::new(
            LifecycleProjectionStatusV1::IdentityMismatch,
            "ledger_observed_at_predates_event",
        ));
    }
    Ok(Some((events, summary)))
}

fn revalidate_event_files(
    root: &fs::File,
    events: &[LoadedEventSnapshot],
    started: Instant,
    options: &LifecycleLedgerLoadOptions,
) -> std::result::Result<(), LoadFailure> {
    for event in events {
        ensure_elapsed(started, options)?;
        let reopened = crate::process::open_bounded_regular_file(root, &event.relative_path)
            .map_err(|_| LoadFailure::unsafe_storage("event_final_reopen_failed"))?;
        let metadata = reopened
            .metadata()
            .map_err(|_| LoadFailure::unsafe_storage("event_final_metadata_failed"))?;
        if crate::process::FilesystemObjectSnapshot::from_metadata(&metadata) != event.snapshot
            || metadata_link_count(&metadata) != Some(1)
        {
            return Err(LoadFailure::unsafe_storage(
                "earlier_event_changed_during_load",
            ));
        }
    }
    Ok(())
}

fn read_ledger_directory(
    root: &fs::File,
    relative_path: &Path,
    started: Instant,
    options: &LifecycleLedgerLoadOptions,
    entries_observed: &mut u64,
) -> std::result::Result<crate::process::BoundedDirectoryRead, LoadFailure> {
    ensure_elapsed(started, options)?;
    let remaining = options
        .budgets
        .max_directory_entries
        .saturating_sub(*entries_observed);
    let read = crate::process::read_bounded_directory(
        root,
        relative_path,
        remaining,
        started,
        options.budgets.max_elapsed_ms,
    )
    .map_err(|error| {
        let missing = error.kind() == std::io::ErrorKind::NotFound
            && relative_path == Path::new("document-lifecycle");
        if missing {
            LoadFailure::unsafe_storage("ledger_directory_missing")
        } else {
            LoadFailure::unsafe_storage("ledger_directory_bounded_read_failed")
        }
    })?;
    *entries_observed = entries_observed.saturating_add(read.entries_observed);
    if read.limit.is_some() || *entries_observed > options.budgets.max_directory_entries {
        return Err(LoadFailure::budget("directory_entry_budget_exceeded"));
    }
    if !read.stable {
        return Err(LoadFailure::unsafe_storage(
            "ledger_directory_replaced_during_read",
        ));
    }
    Ok(read)
}

fn validate_safe_ledger_file(
    root: &fs::File,
    relative_path: &Path,
) -> std::result::Result<(), LoadFailure> {
    let file = crate::process::open_bounded_regular_file(root, relative_path)
        .map_err(|_| LoadFailure::unsafe_storage("ledger_lock_bounded_open_failed"))?;
    let metadata = file
        .metadata()
        .map_err(|_| LoadFailure::unsafe_storage("ledger_lock_metadata_failed"))?;
    if !metadata.is_file() || metadata_link_count(&metadata) != Some(1) {
        return Err(LoadFailure::unsafe_storage("ledger_lock_not_safe_file"));
    }
    Ok(())
}

fn read_event_bytes(
    file: &mut fs::File,
    started: Instant,
    options: &LifecycleLedgerLoadOptions,
    prior_total: u64,
) -> std::result::Result<Vec<u8>, LoadFailure> {
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        ensure_elapsed(started, options)?;
        let count = file
            .read(&mut buffer)
            .map_err(|_| LoadFailure::unsafe_storage("event_read_failed"))?;
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        if bytes.len() as u64 > options.budgets.max_event_bytes {
            return Err(LoadFailure::budget("per_event_byte_budget_exceeded"));
        }
        if prior_total.saturating_add(bytes.len() as u64) > options.budgets.max_total_bytes {
            return Err(LoadFailure::budget("total_event_byte_budget_exceeded"));
        }
    }
    Ok(bytes)
}

fn ensure_elapsed(
    started: Instant,
    options: &LifecycleLedgerLoadOptions,
) -> std::result::Result<(), LoadFailure> {
    if started.elapsed().as_millis() as u64 >= options.budgets.max_elapsed_ms {
        Err(LoadFailure::budget("elapsed_time_budget_exceeded"))
    } else {
        Ok(())
    }
}

fn revalidate_directory(
    root: &fs::File,
    relative_path: &Path,
    expected: &crate::process::FilesystemObjectSnapshot,
) -> std::result::Result<(), LoadFailure> {
    let current = crate::process::bounded_directory_snapshot(root, relative_path)
        .map_err(|_| LoadFailure::unsafe_storage("ledger_directory_reopen_failed"))?;
    if &current != expected {
        return Err(LoadFailure::unsafe_storage(
            "ledger_directory_replaced_after_read",
        ));
    }
    Ok(())
}

fn revalidate_supplied_root(
    supplied_profile_root: &Path,
    expected: &crate::process::FilesystemObjectSnapshot,
) -> std::result::Result<(), LoadFailure> {
    let path_metadata = fs::symlink_metadata(supplied_profile_root)
        .map_err(|_| LoadFailure::unsafe_storage("profile_root_reopen_failed"))?;
    if path_metadata.file_type().is_symlink() {
        return Err(LoadFailure::unsafe_storage(
            "profile_root_replaced_by_symlink",
        ));
    }
    let reopened = crate::process::open_bounded_root(supplied_profile_root)
        .map_err(|_| LoadFailure::unsafe_storage("profile_root_reopen_failed"))?;
    let current = crate::process::FilesystemObjectSnapshot::from_metadata(
        &reopened
            .metadata()
            .map_err(|_| LoadFailure::unsafe_storage("profile_root_reopen_metadata_failed"))?,
    );
    if &current != expected
        || crate::process::FilesystemObjectSnapshot::from_metadata(&path_metadata) != *expected
    {
        return Err(LoadFailure::unsafe_storage(
            "profile_root_replaced_after_read",
        ));
    }
    Ok(())
}

pub(crate) fn lifecycle_event_filename(event: &DocumentLifecycleEventV1) -> String {
    format!(
        "{:020}_{}.json",
        event.payload.sequence, event.payload.event_id
    )
}

fn domain_hash(domain: &[u8], parts: &[&[u8]]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(domain);
    for part in parts {
        hasher.update((part.len() as u64).to_be_bytes());
        hasher.update(part);
    }
    format!("{:x}", hasher.finalize())
}

fn validate_sha256(value: &str, label: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        bail!("{label} must be one lowercase SHA-256 digest");
    }
    Ok(())
}

fn validate_identifier(value: &str, label: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 256
        || value.trim() != value
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.' | b':'))
    {
        bail!("{label} must be one bounded non-empty identifier");
    }
    Ok(())
}

fn validate_safe_text(value: &str, label: &str) -> Result<()> {
    if value.trim().is_empty()
        || value.len() > 4096
        || value
            .chars()
            .any(|character| character.is_control() && !matches!(character, '\n' | '\t'))
    {
        bail!("{label} must be bounded operator-safe text");
    }
    Ok(())
}

fn validate_prefixed_uuid(value: &str, prefix: &str, label: &str) -> Result<()> {
    let suffix = value
        .strip_prefix(prefix)
        .with_context(|| format!("{label} must start with {prefix}"))?;
    let parsed = Uuid::parse_str(suffix).with_context(|| format!("{label} must carry a UUID"))?;
    if parsed.hyphenated().to_string() != suffix {
        bail!("{label} must carry one canonical lowercase hyphenated UUID");
    }
    Ok(())
}

fn validate_prefixed_identifier(value: &str, prefix: &str, label: &str) -> Result<()> {
    validate_identifier(value, label)?;
    if value
        .strip_prefix(prefix)
        .is_none_or(|suffix| suffix.is_empty())
    {
        bail!("{label} must start with {prefix} and include an identity suffix");
    }
    Ok(())
}

fn validate_observation_id(value: &str) -> Result<()> {
    let suffix = value
        .strip_prefix("doc_observation_")
        .context("observation_id must start with doc_observation_")?;
    if suffix.len() != 24
        || !suffix
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
    {
        bail!("observation_id must carry the exact 24-character lowercase digest suffix");
    }
    Ok(())
}

fn validate_bounded_relative_path(value: &str) -> Result<()> {
    let path = Path::new(value);
    if value.is_empty()
        || value.len() > 4096
        || value.contains('\\')
        || path.is_absolute()
        || !path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        bail!("lifecycle path must contain only bounded normal relative components");
    }
    let components = path.components().collect::<Vec<_>>();
    if components.len() > 256
        || components
            .iter()
            .any(|component| component.as_os_str().len() > 255)
    {
        bail!("lifecycle path exceeds its component or depth bound");
    }
    let canonical = path
        .components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/");
    if canonical != value {
        bail!("lifecycle path must use canonical relative separators");
    }
    Ok(())
}

#[cfg(unix)]
fn metadata_link_count(metadata: &fs::Metadata) -> Option<u64> {
    use std::os::unix::fs::MetadataExt;
    Some(metadata.nlink())
}

#[cfg(not(unix))]
fn metadata_link_count(_metadata: &fs::Metadata) -> Option<u64> {
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    const PROJECT_KEY: &str = "phase2a-project";
    const ROOT_ID: &str = "root_phase2a";
    const ROOT_PATH_HASH: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const ROOT_INSTANCE_HASH: &str =
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const DOCUMENT_ID: &str = "document_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const EVENT_ONE_ID: &str = "document_event_11111111-1111-4111-8111-111111111111";
    const EVENT_TWO_ID: &str = "document_event_22222222-2222-4222-8222-222222222222";
    const OBSERVATION_ONE_ID: &str = "doc_observation_971af5ef9b50147546a37d09";
    const OBSERVATION_TWO_ID: &str = "doc_observation_c6e3d97cf2e444c8d9a791db";

    fn timestamp(value: &str) -> StrictUtcTimestamp {
        StrictUtcTimestamp::parse(value).unwrap()
    }

    fn expectation() -> LifecycleLedgerExpectationV1 {
        LifecycleLedgerExpectationV1 {
            project_key: PROJECT_KEY.to_string(),
            root_id: ROOT_ID.to_string(),
            root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
        }
    }

    fn state() -> DocumentLifecycleStateV1 {
        DocumentLifecycleStateV1 {
            activity_state: ActivityState::Active,
            retention_disposition: RetentionDisposition::Unclassified,
            successor_document_id: None,
            active_owner_document_id: None,
            summary_document_id: None,
            review_after: None,
        }
    }

    fn approval_source(
        approval_id: &str,
        action_id: &str,
        proposal_sha256: &str,
        target_identity: ApprovalTargetIdentityV1,
        created_at: &str,
        resolved_at: &str,
        expires_at: &str,
    ) -> DocumentLifecycleApprovalSourceV1 {
        let (action, risk_level, metadata_schema) = match &target_identity {
            ApprovalTargetIdentityV1::Retention {
                requested_action, ..
            } => (
                "maintenance.artifact_cleanup",
                requested_action.expected_risk(),
                RETENTION_APPROVAL_METADATA_SCHEMA_V2,
            ),
            _ => (
                "document.lifecycle.transition",
                DetachedRiskLevelV1::CanonicalMutation,
                DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA,
            ),
        };
        DocumentLifecycleApprovalSourceV1 {
            schema: DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA.to_string(),
            approval_id: approval_id.to_string(),
            action_id: action_id.to_string(),
            scope: DetachedApprovalScopeV1::Once,
            project_key: PROJECT_KEY.to_string(),
            request_id: "request_phase2a".to_string(),
            task_id: "task_phase2a".to_string(),
            action: action.to_string(),
            risk_level,
            approval_mode: DetachedApprovalModeV1::OperatorRequired,
            created_at: timestamp(created_at),
            expires_at: timestamp(expires_at),
            status: DetachedApprovalStatusV1::Approved,
            resolved_at: timestamp(resolved_at),
            resolved_by: "telegram:owner".to_string(),
            source_surface: "telegram".to_string(),
            preview_sha256: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
                .to_string(),
            reason_sha256: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
                .to_string(),
            metadata_schema: metadata_schema.to_string(),
            metadata_sha256: "1111111111111111111111111111111111111111111111111111111111111111"
                .to_string(),
            proposal_sha256: proposal_sha256.to_string(),
            target_identity,
        }
    }

    fn sample_events() -> Vec<DocumentLifecycleEventV1> {
        let proposal_one = "2222222222222222222222222222222222222222222222222222222222222222";
        let approval_one = approval_source(
            "approval_bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "action_cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            proposal_one,
            ApprovalTargetIdentityV1::Mint {
                observation_id: OBSERVATION_ONE_ID.to_string(),
            },
            "2026-08-20T00:00:00Z",
            "2026-08-20T00:05:00Z",
            "2026-08-20T01:00:00Z",
        );
        let approval_one_sha = document_lifecycle_approval_source_sha256(&approval_one).unwrap();
        let event_one = DocumentLifecycleEventV1::from_payload(DocumentLifecycleEventPayloadV1 {
            sequence: 1,
            event_id: EVENT_ONE_ID.to_string(),
            event_kind: DocumentLifecycleEventKindV1::MintDocument,
            recorded_at: timestamp("2026-08-20T00:10:00Z"),
            previous_event_sha256: None,
            project_key: PROJECT_KEY.to_string(),
            root_id: ROOT_ID.to_string(),
            root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
            document_id: DOCUMENT_ID.to_string(),
            expected_document_head_event_id: None,
            observation: DocumentLifecycleObservationV1 {
                observation_id: OBSERVATION_ONE_ID.to_string(),
                relative_path: "docs/report.md".to_string(),
                size_bytes: 123,
                modified_at: timestamp("2026-08-20T00:00:00Z"),
                content_sha256: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
                    .to_string(),
            },
            before: None,
            after: state(),
            proposal_sha256: proposal_one.to_string(),
            approval_source: approval_one,
            approval_source_sha256: approval_one_sha,
            reason: "Establish the reviewed current report.".to_string(),
            idempotency_key: "lifecycle_apply_one".to_string(),
        })
        .unwrap();

        let proposal_two = "3333333333333333333333333333333333333333333333333333333333333333";
        let approval_two = approval_source(
            "approval_dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            "action_eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            proposal_two,
            ApprovalTargetIdentityV1::Document {
                document_id: DOCUMENT_ID.to_string(),
                expected_head_event_id: EVENT_ONE_ID.to_string(),
            },
            "2026-08-20T00:20:00Z",
            "2026-08-20T00:25:00Z",
            "2026-08-20T01:20:00Z",
        );
        let approval_two_sha = document_lifecycle_approval_source_sha256(&approval_two).unwrap();
        let event_two = DocumentLifecycleEventV1::from_payload(DocumentLifecycleEventPayloadV1 {
            sequence: 2,
            event_id: EVENT_TWO_ID.to_string(),
            event_kind: DocumentLifecycleEventKindV1::RevalidateObservation,
            recorded_at: timestamp("2026-08-20T00:30:00Z"),
            previous_event_sha256: Some(event_one.event_sha256.clone()),
            project_key: PROJECT_KEY.to_string(),
            root_id: ROOT_ID.to_string(),
            root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
            document_id: DOCUMENT_ID.to_string(),
            expected_document_head_event_id: Some(EVENT_ONE_ID.to_string()),
            observation: DocumentLifecycleObservationV1 {
                observation_id: OBSERVATION_TWO_ID.to_string(),
                relative_path: "docs/report.md".to_string(),
                size_bytes: 124,
                modified_at: timestamp("2026-08-20T00:20:00Z"),
                content_sha256: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
                    .to_string(),
            },
            before: Some(state()),
            after: state(),
            proposal_sha256: proposal_two.to_string(),
            approval_source: approval_two,
            approval_source_sha256: approval_two_sha,
            reason: "Revalidate changed bytes at the reviewed path.".to_string(),
            idempotency_key: "lifecycle_apply_two".to_string(),
        })
        .unwrap();
        vec![event_one, event_two]
    }

    fn fixture_options() -> LifecycleLedgerLoadOptions {
        LifecycleLedgerLoadOptions {
            budgets: LifecycleLedgerLoadBudgets::default(),
            observed_at: timestamp("2026-08-20T00:40:00Z"),
        }
    }

    fn write_event_chain(
        root: &TempDir,
        events: &[DocumentLifecycleEventV1],
    ) -> std::path::PathBuf {
        let event_dir = root
            .path()
            .join("document-lifecycle")
            .join("v1")
            .join("events");
        fs::create_dir_all(&event_dir).unwrap();
        for event in events {
            fs::write(
                event_dir.join(lifecycle_event_filename(event)),
                serde_json::to_vec(event).unwrap(),
            )
            .unwrap();
        }
        event_dir
    }

    #[test]
    fn canonical_contracts_reject_noncanonical_authority() {
        let events = sample_events();
        for event in &events {
            event.validate().unwrap();
        }
        validate_document_lifecycle_event_chain(&events, &expectation()).unwrap();

        let source = &events[0].payload.approval_source;
        let first = canonical_approval_source_bytes(source).unwrap();
        let second = canonical_approval_source_bytes(source).unwrap();
        assert_eq!(first, second);
        assert_eq!(
            document_lifecycle_approval_source_sha256(source).unwrap(),
            events[0].payload.approval_source_sha256
        );

        let mut changed_actor = source.clone();
        changed_actor.resolved_by = "telegram:other-owner".to_string();
        assert_ne!(
            document_lifecycle_approval_source_sha256(&changed_actor).unwrap(),
            events[0].payload.approval_source_sha256
        );
        assert!(StrictUtcTimestamp::parse("2026-08-20T09:00:00+09:00").is_err());
        assert_eq!(
            StrictUtcTimestamp::parse("2026-08-20T00:00:00.123Z")
                .unwrap()
                .as_str(),
            "2026-08-20T00:00:00.123Z"
        );
        assert!(StrictUtcTimestamp::parse("2026-08-20T00:00:00.1Z").is_err());
        assert!(validate_prefixed_uuid(
            "document_AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "document_",
            "document_id"
        )
        .is_err());

        let mut empty_identifier = source.clone();
        empty_identifier.request_id.clear();
        assert!(empty_identifier.validate().is_err());

        let mut unknown_field = serde_json::to_value(&events[0]).unwrap();
        unknown_field
            .as_object_mut()
            .unwrap()
            .insert("unknown".to_string(), serde_json::json!(true));
        assert!(serde_json::from_value::<DocumentLifecycleEventV1>(unknown_field).is_err());

        let mut missing_field = serde_json::to_value(&events[0]).unwrap();
        missing_field
            .get_mut("payload")
            .unwrap()
            .as_object_mut()
            .unwrap()
            .remove("root_id");
        assert!(serde_json::from_value::<DocumentLifecycleEventV1>(missing_field).is_err());

        let mut after_expiry = events[0].payload.clone();
        after_expiry.recorded_at = timestamp("2026-08-20T01:00:01Z");
        assert!(after_expiry.validate().is_err());
        let mut future_observation = events[0].payload.clone();
        future_observation.observation.modified_at = timestamp("2026-08-20T00:10:01Z");
        assert!(future_observation.validate().is_err());
        let mut overlong_path = events[0].payload.clone();
        overlong_path.observation.relative_path = format!("{}.md", "a".repeat(4096));
        assert!(overlong_path.validate().is_err());
    }

    #[test]
    fn chain_rejects_broken_hash_and_stale_before_state() {
        let mut broken = sample_events();
        broken[1].payload.previous_event_sha256 =
            Some("9999999999999999999999999999999999999999999999999999999999999999".to_string());
        assert!(validate_document_lifecycle_event_chain(&broken, &expectation()).is_err());

        let mut stale = sample_events();
        stale[1].payload.before.as_mut().unwrap().activity_state = ActivityState::Draft;
        let payload_sha = document_lifecycle_payload_sha256(&stale[1].payload).unwrap();
        stale[1].payload_sha256 = payload_sha.clone();
        stale[1].event_sha256 =
            document_lifecycle_event_sha256(&stale[1].payload, &payload_sha).unwrap();
        assert!(validate_document_lifecycle_event_chain(&stale, &expectation()).is_err());

        let mut missing_sequence = sample_events();
        missing_sequence[1].payload.sequence = 3;
        let payload_sha = document_lifecycle_payload_sha256(&missing_sequence[1].payload).unwrap();
        missing_sequence[1].payload_sha256 = payload_sha.clone();
        missing_sequence[1].event_sha256 =
            document_lifecycle_event_sha256(&missing_sequence[1].payload, &payload_sha).unwrap();
        assert!(
            validate_document_lifecycle_event_chain(&missing_sequence, &expectation()).is_err()
        );

        let mut duplicate_event_id = sample_events();
        duplicate_event_id[1].payload.event_id = EVENT_ONE_ID.to_string();
        let payload_sha =
            document_lifecycle_payload_sha256(&duplicate_event_id[1].payload).unwrap();
        duplicate_event_id[1].payload_sha256 = payload_sha.clone();
        duplicate_event_id[1].event_sha256 =
            document_lifecycle_event_sha256(&duplicate_event_id[1].payload, &payload_sha).unwrap();
        assert!(
            validate_document_lifecycle_event_chain(&duplicate_event_id, &expectation()).is_err()
        );

        let mut wrong_payload_hash = sample_events();
        wrong_payload_hash[0].payload_sha256 =
            "9999999999999999999999999999999999999999999999999999999999999999".to_string();
        assert!(wrong_payload_hash[0].validate().is_err());
        let mut wrong_event_hash = sample_events();
        wrong_event_hash[0].event_sha256 =
            "9999999999999999999999999999999999999999999999999999999999999999".to_string();
        assert!(wrong_event_hash[0].validate().is_err());

        let mut reused_observation_id = sample_events();
        reused_observation_id[1].payload.observation.observation_id =
            OBSERVATION_ONE_ID.to_string();
        assert!(document_lifecycle_payload_sha256(&reused_observation_id[1].payload).is_err());

        let mut mint_with_retention = sample_events();
        mint_with_retention[0].payload.after.retention_disposition = RetentionDisposition::Keep;
        let payload_sha =
            document_lifecycle_payload_sha256(&mint_with_retention[0].payload).unwrap();
        mint_with_retention[0].payload_sha256 = payload_sha.clone();
        mint_with_retention[0].event_sha256 =
            document_lifecycle_event_sha256(&mint_with_retention[0].payload, &payload_sha).unwrap();
        assert!(
            validate_document_lifecycle_event_chain(&mint_with_retention, &expectation()).is_err()
        );
    }

    #[test]
    fn retention_provenance_and_completed_mutation_states_fail_closed() {
        let mut events = sample_events();
        let proposal = "4444444444444444444444444444444444444444444444444444444444444444";
        let approval = approval_source(
            "approval_44444444-4444-4444-8444-444444444444",
            "action_55555555-5555-4555-8555-555555555555",
            proposal,
            ApprovalTargetIdentityV1::Retention {
                document_id: DOCUMENT_ID.to_string(),
                expected_head_event_id: EVENT_TWO_ID.to_string(),
                retention_target_id: DOCUMENT_ID.to_string(),
                requested_action: RetentionActionV1::Keep,
                application_id: "retention_application_three".to_string(),
            },
            "2026-08-20T00:40:00Z",
            "2026-08-20T00:45:00Z",
            "2026-08-20T01:40:00Z",
        );
        let approval_sha = document_lifecycle_approval_source_sha256(&approval).unwrap();
        let event = DocumentLifecycleEventV1::from_payload(DocumentLifecycleEventPayloadV1 {
            sequence: 3,
            event_id: "document_event_33333333-3333-4333-8333-333333333333".to_string(),
            event_kind: DocumentLifecycleEventKindV1::RetentionTransition,
            recorded_at: timestamp("2026-08-20T00:50:00Z"),
            previous_event_sha256: Some(events[1].event_sha256.clone()),
            project_key: PROJECT_KEY.to_string(),
            root_id: ROOT_ID.to_string(),
            root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
            document_id: DOCUMENT_ID.to_string(),
            expected_document_head_event_id: Some(EVENT_TWO_ID.to_string()),
            observation: events[1].payload.observation.clone(),
            before: Some(state()),
            after: DocumentLifecycleStateV1 {
                retention_disposition: RetentionDisposition::Keep,
                ..state()
            },
            proposal_sha256: proposal.to_string(),
            approval_source: approval,
            approval_source_sha256: approval_sha,
            reason: "Keep the exact reviewed document in the retention plan.".to_string(),
            idempotency_key: "retention_application_three".to_string(),
        })
        .unwrap();
        events.push(event);
        validate_document_lifecycle_event_chain(&events, &expectation()).unwrap();

        let mut wrong_source = events.clone();
        wrong_source[2].payload.approval_source_sha256 =
            "9999999999999999999999999999999999999999999999999999999999999999".to_string();
        assert!(validate_document_lifecycle_event_chain(&wrong_source, &expectation()).is_err());

        for disposition in [
            RetentionDisposition::Archived,
            RetentionDisposition::Quarantined,
            RetentionDisposition::Disposed,
        ] {
            let mut completed_mutation = events[2].payload.clone();
            completed_mutation.after.retention_disposition = disposition;
            assert!(completed_mutation.validate().is_err());
        }
    }

    #[test]
    fn successor_owner_cycles_and_non_active_summary_targets_are_rejected() {
        let observation = sample_events()[0].payload.observation.clone();
        let document_a = "document_11111111-1111-4111-8111-111111111111";
        let document_b = "document_22222222-2222-4222-8222-222222222222";
        let mut heads = BTreeMap::from([
            (
                document_a.to_string(),
                DocumentHead {
                    event_id: EVENT_ONE_ID.to_string(),
                    observation: observation.clone(),
                    state: DocumentLifecycleStateV1 {
                        active_owner_document_id: Some(document_b.to_string()),
                        ..state()
                    },
                },
            ),
            (
                document_b.to_string(),
                DocumentHead {
                    event_id: EVENT_TWO_ID.to_string(),
                    observation,
                    state: DocumentLifecycleStateV1 {
                        active_owner_document_id: Some(document_a.to_string()),
                        ..state()
                    },
                },
            ),
        ]);
        assert!(validate_lifecycle_references(&heads).is_err());

        heads.get_mut(document_a).unwrap().state = DocumentLifecycleStateV1 {
            retention_disposition: RetentionDisposition::SummaryKept,
            summary_document_id: Some(document_b.to_string()),
            ..state()
        };
        heads.get_mut(document_b).unwrap().state = DocumentLifecycleStateV1 {
            activity_state: ActivityState::Draft,
            ..state()
        };
        assert!(validate_lifecycle_references(&heads).is_err());
    }

    #[test]
    fn mint_and_rebind_evidence_fail_closed() {
        let valid_mint = DocumentMintEvidenceV1 {
            scan_complete: true,
            root_path_identity_exact: true,
            root_instance_identity_exact: true,
            observation_exact: true,
            descriptor_read_stable: true,
            existing_observation_bindings: 0,
            unresolved_identity_conflicts: 0,
            unresolved_artifact_crosswalk_conflicts: 0,
            approval_current_and_exact: true,
        };
        valid_mint.validate_authority().unwrap();
        let mut incomplete = valid_mint.clone();
        incomplete.scan_complete = false;
        assert!(incomplete.validate_authority().is_err());
        let mut equal_bytes_already_bound = valid_mint.clone();
        equal_bytes_already_bound.existing_observation_bindings = 1;
        assert!(equal_bytes_already_bound.validate_authority().is_err());

        let candidate = sample_events()[0].payload.observation.clone();
        let valid_rebind = DocumentRebindEvidenceV1 {
            scan_complete: true,
            old_relative_path: "docs/old-report.md".to_string(),
            old_path_present: false,
            candidate: candidate.clone(),
            same_hash_candidate_observation_ids: vec![candidate.observation_id.clone()],
            candidate_bound_document_id: None,
            candidate_pending_document_id: None,
            reviewed_content_sha256: candidate.content_sha256.clone(),
            reviewed_root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            current_root_path_identity_sha256: ROOT_PATH_HASH.to_string(),
            reviewed_root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
            current_root_instance_identity_sha256: ROOT_INSTANCE_HASH.to_string(),
            expected_head_event_id: EVENT_ONE_ID.to_string(),
            current_head_event_id: EVENT_ONE_ID.to_string(),
        };
        valid_rebind.validate_authority().unwrap();
        for invalid in [
            {
                let mut value = valid_rebind.clone();
                value.scan_complete = false;
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.old_path_present = true;
                value
            },
            {
                let mut value = valid_rebind.clone();
                value
                    .same_hash_candidate_observation_ids
                    .push("doc_observation_333333333333333333333333".to_string());
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.candidate_bound_document_id = Some(DOCUMENT_ID.to_string());
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.candidate_pending_document_id = Some(DOCUMENT_ID.to_string());
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.current_root_path_identity_sha256 =
                    "9999999999999999999999999999999999999999999999999999999999999999".to_string();
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.current_root_instance_identity_sha256 =
                    "9999999999999999999999999999999999999999999999999999999999999999".to_string();
                value
            },
            {
                let mut value = valid_rebind.clone();
                value.current_head_event_id = EVENT_TWO_ID.to_string();
                value
            },
        ] {
            assert!(invalid.validate_authority().is_err());
        }
    }

    #[test]
    fn loader_accepts_only_the_complete_supplied_chain() {
        let root = TempDir::new().unwrap();
        let events = sample_events();
        write_event_chain(&root, &events);
        let report = load_document_lifecycle_ledger_from_root(
            root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::Valid);
        assert_eq!(report.events, events);
        assert!(!report.lifecycle_projection_authorized);
        assert!(!report.search_filtering_authorized);
        assert!(!report.profile_state_write_authorized);
        report.validate().unwrap();

        let before_final_event = LifecycleLedgerLoadOptions {
            observed_at: timestamp("2026-08-20T00:29:59Z"),
            ..fixture_options()
        };
        let report = load_document_lifecycle_ledger_from_root(
            root.path(),
            &expectation(),
            &before_final_event,
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::IdentityMismatch);
        assert!(report.events.is_empty());
    }

    #[test]
    fn loader_never_projects_a_valid_prefix_after_corruption() {
        let root = TempDir::new().unwrap();
        let events = sample_events();
        let event_dir = write_event_chain(&root, &events);
        fs::write(
            event_dir.join(lifecycle_event_filename(&events[1])),
            b"{\"schema\":\"document_lifecycle_event.v1\",\"broken\":true}",
        )
        .unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::Corrupt);
        assert!(report.events.is_empty());
        assert!(report.source_identity.is_none());

        let duplicate_root = TempDir::new().unwrap();
        let duplicate_events = write_event_chain(&duplicate_root, &events);
        let first = duplicate_events.join(lifecycle_event_filename(&events[0]));
        let serialized = serde_json::to_string(&events[0]).unwrap();
        let duplicate_schema =
            serialized.replacen('{', "{\"schema\":\"document_lifecycle_event.v1\",", 1);
        fs::write(first, duplicate_schema).unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            duplicate_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::Corrupt);
        assert!(report.events.is_empty());
    }

    #[test]
    fn loader_reports_partial_and_every_budget_without_events() {
        let events = sample_events();
        let root = TempDir::new().unwrap();
        let event_dir = write_event_chain(&root, &events);
        fs::write(event_dir.join("event.partial"), b"partial").unwrap();
        let partial = load_document_lifecycle_ledger_from_root(
            root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(
            partial.status,
            LifecycleProjectionStatusV1::PartialWritePresent
        );
        assert!(partial.events.is_empty());

        for budgets in [
            LifecycleLedgerLoadBudgets {
                max_events: 1,
                ..LifecycleLedgerLoadBudgets::default()
            },
            LifecycleLedgerLoadBudgets {
                max_directory_entries: 1,
                ..LifecycleLedgerLoadBudgets::default()
            },
            LifecycleLedgerLoadBudgets {
                max_event_bytes: 32,
                ..LifecycleLedgerLoadBudgets::default()
            },
        ] {
            let root = TempDir::new().unwrap();
            write_event_chain(&root, &events);
            let options = LifecycleLedgerLoadOptions {
                budgets,
                observed_at: timestamp("2026-08-20T00:40:00Z"),
            };
            let report =
                load_document_lifecycle_ledger_from_root(root.path(), &expectation(), &options)
                    .unwrap();
            assert_eq!(report.status, LifecycleProjectionStatusV1::BudgetExceeded);
            assert!(report.events.is_empty());
        }

        let serialized_sizes = events
            .iter()
            .map(|event| serde_json::to_vec(event).unwrap().len() as u64)
            .collect::<Vec<_>>();
        let total_options = LifecycleLedgerLoadOptions {
            budgets: LifecycleLedgerLoadBudgets {
                max_event_bytes: *serialized_sizes.iter().max().unwrap(),
                max_total_bytes: serialized_sizes.iter().sum::<u64>() - 1,
                ..LifecycleLedgerLoadBudgets::default()
            },
            observed_at: timestamp("2026-08-20T00:40:00Z"),
        };
        let total_root = TempDir::new().unwrap();
        write_event_chain(&total_root, &events);
        let total = load_document_lifecycle_ledger_from_root(
            total_root.path(),
            &expectation(),
            &total_options,
        )
        .unwrap();
        assert_eq!(total.status, LifecycleProjectionStatusV1::BudgetExceeded);
        assert_eq!(
            total.failure_code.as_deref(),
            Some("total_event_byte_budget_exceeded")
        );

        let elapsed_root = TempDir::new().unwrap();
        write_event_chain(&elapsed_root, &events);
        let elapsed_options = LifecycleLedgerLoadOptions {
            budgets: LifecycleLedgerLoadBudgets {
                max_elapsed_ms: 1,
                ..LifecycleLedgerLoadBudgets::default()
            },
            observed_at: timestamp("2026-08-20T00:40:00Z"),
        };
        let elapsed = load_document_lifecycle_ledger_with_hook(
            elapsed_root.path(),
            &expectation(),
            &elapsed_options,
            &mut |index| {
                if index.is_none() {
                    std::thread::sleep(std::time::Duration::from_millis(3));
                }
            },
        )
        .unwrap();
        assert_eq!(elapsed.status, LifecycleProjectionStatusV1::BudgetExceeded);
        assert_eq!(
            elapsed.failure_code.as_deref(),
            Some("elapsed_time_budget_exceeded")
        );
    }

    #[cfg(unix)]
    #[test]
    fn loader_rejects_symlink_hardlink_and_special_event_files() {
        use std::os::unix::fs::symlink;
        use std::os::unix::net::UnixListener;

        let events = sample_events();

        let symlink_root = TempDir::new().unwrap();
        let symlink_events = write_event_chain(&symlink_root, &events);
        let first = symlink_events.join(lifecycle_event_filename(&events[0]));
        fs::remove_file(&first).unwrap();
        symlink("/etc/passwd", &first).unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            symlink_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let component_root = TempDir::new().unwrap();
        let outside = TempDir::new().unwrap();
        fs::create_dir(component_root.path().join("document-lifecycle")).unwrap();
        symlink(
            outside.path(),
            component_root.path().join("document-lifecycle").join("v1"),
        )
        .unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            component_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let lifecycle_link_root = TempDir::new().unwrap();
        let lifecycle_link_target = TempDir::new().unwrap();
        write_event_chain(&lifecycle_link_target, &events);
        symlink(
            lifecycle_link_target.path().join("document-lifecycle"),
            lifecycle_link_root.path().join("document-lifecycle"),
        )
        .unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            lifecycle_link_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let ancestor_parent = TempDir::new().unwrap();
        let ancestor_target = TempDir::new().unwrap();
        let profile = ancestor_target.path().join("profile");
        let profile_events = profile.join("document-lifecycle").join("v1").join("events");
        fs::create_dir_all(&profile_events).unwrap();
        for event in &events {
            fs::write(
                profile_events.join(lifecycle_event_filename(event)),
                serde_json::to_vec(event).unwrap(),
            )
            .unwrap();
        }
        symlink(ancestor_target.path(), ancestor_parent.path().join("alias")).unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            &ancestor_parent.path().join("alias").join("profile"),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let hardlink_root = TempDir::new().unwrap();
        let hardlink_events = write_event_chain(&hardlink_root, &events);
        let first = hardlink_events.join(lifecycle_event_filename(&events[0]));
        fs::hard_link(&first, hardlink_events.join("copy.json")).unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            hardlink_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let special_root = TempDir::new().unwrap();
        let special_events = write_event_chain(&special_root, &events);
        let first = special_events.join(lifecycle_event_filename(&events[0]));
        fs::remove_file(&first).unwrap();
        let short_socket = special_events.join("event.sock");
        let _listener = UnixListener::bind(&short_socket).unwrap();
        fs::rename(short_socket, &first).unwrap();
        let report = load_document_lifecycle_ledger_from_root(
            special_root.path(),
            &expectation(),
            &fixture_options(),
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);
    }

    #[test]
    fn loader_detects_controlled_file_and_directory_replacement() {
        let events = sample_events();

        let file_root = TempDir::new().unwrap();
        let file_events = write_event_chain(&file_root, &events);
        let first = file_events.join(lifecycle_event_filename(&events[0]));
        let replacement = serde_json::to_vec(&events[0]).unwrap();
        let mut replaced = false;
        let report = load_document_lifecycle_ledger_with_hook(
            file_root.path(),
            &expectation(),
            &fixture_options(),
            &mut |index| {
                if index == Some(0) && !replaced {
                    let old = first.with_extension("old");
                    fs::rename(&first, old).unwrap();
                    fs::write(&first, &replacement).unwrap();
                    replaced = true;
                }
            },
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);

        let earlier_root = TempDir::new().unwrap();
        let earlier_events = write_event_chain(&earlier_root, &events);
        let first = earlier_events.join(lifecycle_event_filename(&events[0]));
        let replacement = serde_json::to_vec(&events[0]).unwrap();
        let mut changed = false;
        let report = load_document_lifecycle_ledger_with_hook(
            earlier_root.path(),
            &expectation(),
            &fixture_options(),
            &mut |index| {
                if index == Some(1) && !changed {
                    fs::write(&first, &replacement).unwrap();
                    changed = true;
                }
            },
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);
        assert_eq!(
            report.failure_code.as_deref(),
            Some("earlier_event_changed_during_load")
        );

        let directory_root = TempDir::new().unwrap();
        let event_dir = write_event_chain(&directory_root, &events);
        let mut replaced = false;
        let report = load_document_lifecycle_ledger_with_hook(
            directory_root.path(),
            &expectation(),
            &fixture_options(),
            &mut |index| {
                if index.is_none() && !replaced {
                    let old = event_dir.with_extension("old");
                    fs::rename(&event_dir, old).unwrap();
                    fs::create_dir(&event_dir).unwrap();
                    replaced = true;
                }
            },
        )
        .unwrap();
        assert_eq!(report.status, LifecycleProjectionStatusV1::UnsafeStorage);
    }
}
