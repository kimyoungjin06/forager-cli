//! Approval-bound Phase 2C document lifecycle operations.

use super::{
    load_document_lifecycle_ledger_from_root, scan_project, ActivityState,
    ApprovalTargetIdentityV1, CatalogOptions, CrosswalkStatus, DetachedApprovalModeV1,
    DetachedApprovalScopeV1, DetachedApprovalStatusV1, DetachedRiskLevelV1, DocumentCatalogEntry,
    DocumentLifecycleApprovalSourceV1, DocumentLifecycleEventKindV1,
    DocumentLifecycleEventPayloadV1, DocumentLifecycleEventV1, DocumentLifecycleLedgerLoadV1,
    DocumentLifecycleObservationV1, DocumentLifecycleStateV1, LifecycleLedgerExpectationV1,
    LifecycleLedgerLoadOptions, LifecycleProjectionStatusV1, RetentionActionV1,
    RetentionApprovalMetadataV2, RetentionDisposition, StrictUtcTimestamp,
    DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA, DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA,
    DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA, RETENTION_APPROVAL_METADATA_SCHEMA_V2,
};
use crate::offdesk::approval::{
    ActionApprovalMetadata, ActionApprovalRequest, ApprovalBrief, ApprovalBriefOption,
    ApprovalLedger, ApprovalMode, ApprovalScope, ApprovalStatus, PendingActionApproval, RiskLevel,
};
use crate::offdesk::redaction::operator_safe_text;
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::{Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use uuid::Uuid;

pub const DOCUMENT_LIFECYCLE_PROPOSAL_SCHEMA: &str = "document_lifecycle_proposal.v1";
pub const DOCUMENT_LIFECYCLE_REQUEST_RESULT_SCHEMA: &str = "document_lifecycle_request_result.v1";
pub const DOCUMENT_LIFECYCLE_APPLY_RESULT_SCHEMA: &str = "document_lifecycle_apply_result.v1";
pub const DOCUMENT_LIFECYCLE_HISTORY_SCHEMA: &str = "document_lifecycle_history.v1";

const PROPOSAL_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.proposal.v1\0";
const METADATA_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.approval-metadata.v1\0";
const ROOT_INSTANCE_HASH_DOMAIN: &[u8] = b"forager.document-lifecycle.root-instance.v1\0";
const LIFECYCLE_ACTION: &str = "document.lifecycle.transition";
const LIFECYCLE_DIRECTORY: &str = "document-lifecycle";
const LIFECYCLE_VERSION_DIRECTORY: &str = "document-lifecycle/v1";
const LIFECYCLE_EVENTS_DIRECTORY: &str = "document-lifecycle/v1/events";
const LIFECYCLE_LOCK_PATH: &str = "document-lifecycle/v1/ledger.lock";
const RETENTION_APPLICATIONS_DIRECTORY: &str = "artifact_retention_applications";
const RETENTION_APPLICATION_RECEIPT_SCHEMA_V2: &str = "artifact_retention_application.v2";
const MAX_RETENTION_APPLICATION_RECEIPT_BYTES: u64 = 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleTransitionV1 {
    MintDocument,
    ActivityTransition,
    RevalidateObservation,
    RebindObservation,
}

impl LifecycleTransitionV1 {
    fn event_kind(self) -> DocumentLifecycleEventKindV1 {
        match self {
            Self::MintDocument => DocumentLifecycleEventKindV1::MintDocument,
            Self::ActivityTransition => DocumentLifecycleEventKindV1::ActivityTransition,
            Self::RevalidateObservation => DocumentLifecycleEventKindV1::RevalidateObservation,
            Self::RebindObservation => DocumentLifecycleEventKindV1::RebindObservation,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DocumentLifecycleReviewInput {
    pub project_key: String,
    pub observation_id: Option<String>,
    pub document_id: Option<String>,
    pub transition: LifecycleTransitionV1,
    pub activity_state: Option<ActivityState>,
    pub successor_document_id: Option<String>,
    pub active_owner_document_id: Option<String>,
    pub review_after: Option<StrictUtcTimestamp>,
    pub reason: String,
    pub catalog_options: CatalogOptions,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleProposalPayloadV1 {
    pub project_key: String,
    pub root_id: String,
    pub root_path_identity_sha256: String,
    pub root_instance_identity_sha256: String,
    pub filesystem_snapshot_sha256: String,
    pub scan_complete: bool,
    pub unresolved_identity_conflicts: u64,
    pub unresolved_artifact_crosswalk_conflicts: u64,
    pub target_identity: ApprovalTargetIdentityV1,
    pub event_kind: DocumentLifecycleEventKindV1,
    pub document_id: Option<String>,
    pub expected_document_head_event_id: Option<String>,
    pub observation: DocumentLifecycleObservationV1,
    pub before: Option<DocumentLifecycleStateV1>,
    pub after: DocumentLifecycleStateV1,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LifecycleProposalAuthorityV1 {
    pub read_only: bool,
    pub approval_required: bool,
    pub approval_request_authorized: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleProposalV1 {
    pub schema: String,
    pub generated_at: StrictUtcTimestamp,
    pub payload: DocumentLifecycleProposalPayloadV1,
    pub proposal_sha256: String,
    pub authority_boundary: LifecycleProposalAuthorityV1,
}

impl DocumentLifecycleProposalV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_PROPOSAL_SCHEMA {
            bail!("unsupported document lifecycle proposal schema");
        }
        validate_proposal_payload(&self.payload)?;
        if proposal_sha256(&self.payload)? != self.proposal_sha256 {
            bail!("proposal hash does not match its canonical payload");
        }
        if !self.authority_boundary.read_only
            || !self.authority_boundary.approval_required
            || self.authority_boundary.approval_request_authorized
                != (self.payload.scan_complete
                    && self.payload.unresolved_identity_conflicts == 0
                    && self.payload.unresolved_artifact_crosswalk_conflicts == 0)
            || self.authority_boundary.profile_state_write_authorized
            || self.authority_boundary.project_state_write_authorized
            || self.authority_boundary.file_mutation_authorized
        {
            bail!("proposal authority exceeds the Phase 2C review boundary");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleApprovalMetadataV1 {
    pub schema: String,
    pub generated_at: StrictUtcTimestamp,
    pub proposal: DocumentLifecycleProposalPayloadV1,
    pub proposal_sha256: String,
    pub request_id: String,
    pub task_id: String,
    pub document_id: String,
    pub idempotency_key: String,
    pub approval_brief: ApprovalBrief,
}

impl DocumentLifecycleApprovalMetadataV1 {
    pub fn validate(&self) -> Result<()> {
        if self.schema != DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA {
            bail!("unsupported document lifecycle approval metadata schema");
        }
        validate_proposal_payload(&self.proposal)?;
        if proposal_sha256(&self.proposal)? != self.proposal_sha256 {
            bail!("approval metadata proposal hash mismatch");
        }
        validate_identifier(&self.request_id, "request_id")?;
        validate_identifier(&self.task_id, "task_id")?;
        validate_prefixed_uuid(&self.document_id, "document_", "document_id")?;
        validate_identifier(&self.idempotency_key, "idempotency_key")?;
        if self
            .proposal
            .document_id
            .as_ref()
            .is_some_and(|document_id| document_id != &self.document_id)
        {
            bail!("approval metadata document ID does not match its proposal");
        }
        for reference in [
            self.proposal.after.successor_document_id.as_ref(),
            self.proposal.after.active_owner_document_id.as_ref(),
            self.proposal.after.summary_document_id.as_ref(),
        ]
        .into_iter()
        .flatten()
        {
            if reference == &self.document_id {
                bail!("approval metadata cannot make a lifecycle self-reference");
            }
        }
        if self.approval_brief.schema != "approval_brief.v1"
            || self.approval_brief.recommendation != "approve"
            || self.approval_brief.subject.trim().is_empty()
            || self.approval_brief.question.trim().is_empty()
        {
            bail!("document lifecycle approval brief is incomplete");
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleRequestResultV1 {
    pub schema: String,
    pub proposal_sha256: String,
    pub approval_id: String,
    pub status: ApprovalStatus,
    pub reused_existing: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleApplyResultV1 {
    pub schema: String,
    pub approval_id: String,
    pub event: DocumentLifecycleEventV1,
    pub reused_existing: bool,
    pub approval_consumed: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct RetentionLifecycleAppendV2 {
    pub event: DocumentLifecycleEventV1,
    pub reused_existing: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
struct RetentionIntegrityEventReceiptV2 {
    event_id: String,
    event_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
struct RetentionIntegrityReceiptV2 {
    schema: String,
    application_id: String,
    approval_id: String,
    project_key: String,
    proposal_sha256: String,
    approval_metadata_sha256: String,
    approval_preview_sha256: String,
    retention_target_id: String,
    requested_action: super::RetentionRequestedActionV2,
    lifecycle_event: Option<RetentionIntegrityEventReceiptV2>,
    plan_only: bool,
    file_mutation_performed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DocumentLifecycleHistoryV1 {
    pub schema: String,
    pub generated_at: StrictUtcTimestamp,
    pub project_key: String,
    pub document_id: String,
    pub ledger_status: LifecycleProjectionStatusV1,
    pub failure_code: Option<String>,
    pub events: Vec<DocumentLifecycleEventV1>,
    pub document_body_included: bool,
    pub profile_state_write_authorized: bool,
    pub project_state_write_authorized: bool,
    pub file_mutation_authorized: bool,
}

#[derive(Debug, Clone)]
struct DocumentHead {
    event_id: String,
    observation: DocumentLifecycleObservationV1,
    state: DocumentLifecycleStateV1,
}

#[derive(Debug)]
struct LiveLifecycleContext {
    expectation: LifecycleLedgerExpectationV1,
    load: DocumentLifecycleLedgerLoadV1,
    entries: Vec<DocumentCatalogEntry>,
    scan_complete: bool,
    unresolved_identity_conflicts: u64,
    unresolved_artifact_crosswalk_conflicts: u64,
    filesystem_snapshot_sha256: String,
}

pub fn review_document_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    input: &DocumentLifecycleReviewInput,
) -> Result<DocumentLifecycleProposalV1> {
    let context = load_live_context(profile_root, project_root, input)?;
    build_proposal(input, &context)
}

pub(crate) fn request_document_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    input: &DocumentLifecycleReviewInput,
    expected_proposal_sha256: &str,
    request_id: Option<&str>,
    task_id: Option<&str>,
    now: DateTime<Utc>,
) -> Result<DocumentLifecycleRequestResultV1> {
    validate_active_profile_root(profile_root)?;
    let ledger = ApprovalLedger::new(profile_root);
    let (mut session, _) = ledger.begin_bounded_session(now)?;
    let context = load_live_context(profile_root, project_root, input)?;
    let proposal = build_proposal(input, &context)?;
    if proposal.proposal_sha256 != expected_proposal_sha256 {
        bail!("live lifecycle proposal no longer matches --expected-proposal-sha256");
    }
    if !proposal.authority_boundary.approval_request_authorized {
        bail!("incomplete or conflicting evidence cannot open a lifecycle approval request");
    }

    ensure_no_pending_observation_conflict(session.approvals(), &proposal.payload, None, now)?;

    if let Some(existing) = find_existing_proposal_approval(
        session.approvals(),
        &proposal.payload,
        &proposal.proposal_sha256,
        now,
    )?
    .cloned()
    {
        session.flush()?;
        return Ok(DocumentLifecycleRequestResultV1 {
            schema: DOCUMENT_LIFECYCLE_REQUEST_RESULT_SCHEMA.to_string(),
            proposal_sha256: proposal.proposal_sha256,
            approval_id: existing.approval_id,
            status: existing.status,
            reused_existing: true,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        });
    }

    let request_id = request_id
        .map(str::to_string)
        .unwrap_or_else(|| format!("request_lifecycle_{}", &proposal.proposal_sha256[..16]));
    let task_id = task_id
        .map(str::to_string)
        .unwrap_or_else(|| format!("task_lifecycle_{}", &proposal.proposal_sha256[..16]));
    validate_identifier(&request_id, "request_id")?;
    validate_identifier(&task_id, "task_id")?;
    let document_id = proposal
        .payload
        .document_id
        .clone()
        .unwrap_or_else(|| format!("document_{}", Uuid::new_v4()));
    let idempotency_key = format!("lifecycle_{}", Uuid::new_v4());
    let metadata = DocumentLifecycleApprovalMetadataV1 {
        schema: DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(now),
        proposal: proposal.payload.clone(),
        proposal_sha256: proposal.proposal_sha256.clone(),
        request_id: request_id.clone(),
        task_id: task_id.clone(),
        document_id,
        idempotency_key,
        approval_brief: lifecycle_approval_brief(&proposal),
    };
    metadata.validate()?;
    let mut request = ActionApprovalRequest::new(
        &proposal.payload.project_key,
        request_id,
        task_id,
        LIFECYCLE_ACTION,
        RiskLevel::CanonicalMutation,
    );
    request.preview = lifecycle_preview(&proposal);
    request.reason = proposal.payload.reason.clone();
    request.source_surface = "forager.project.document_lifecycle".to_string();
    request.ttl = Duration::minutes(30);
    request.metadata = Some(ActionApprovalMetadata::DocumentLifecycle(Box::new(
        metadata,
    )));
    let approval = session
        .ensure_pending_without_consuming_grant(request, now)?
        .context("a resolved lifecycle request cannot be silently reopened")?;
    let stored_metadata = approval
        .metadata
        .as_ref()
        .and_then(ActionApprovalMetadata::as_document_lifecycle)
        .context("lifecycle request resolved to approval with wrong metadata")?;
    if stored_metadata.proposal_sha256 != proposal.proposal_sha256 {
        bail!("approval request key collides with different lifecycle metadata");
    }
    session.flush()?;
    Ok(DocumentLifecycleRequestResultV1 {
        schema: DOCUMENT_LIFECYCLE_REQUEST_RESULT_SCHEMA.to_string(),
        proposal_sha256: proposal.proposal_sha256,
        approval_id: approval.approval_id,
        status: approval.status,
        reused_existing: false,
        profile_state_write_authorized: true,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    })
}

pub(crate) fn apply_document_lifecycle(
    profile_root: &Path,
    project_root: &Path,
    approval_id: &str,
    now: DateTime<Utc>,
) -> Result<DocumentLifecycleApplyResultV1> {
    validate_active_profile_root(profile_root)?;
    let ledger = ApprovalLedger::new(profile_root);
    let (mut approval_session, _) = ledger.begin_bounded_session(now)?;
    let profile_root_identity = approval_session
        .bounded_root_identity()
        .context("lifecycle apply requires a descriptor-bound approval session")?;
    let approval = approval_session
        .approval(approval_id)
        .cloned()
        .with_context(|| format!("approval not found in active profile: {approval_id}"))?;
    let metadata = approval
        .metadata
        .as_ref()
        .and_then(ActionApprovalMetadata::as_document_lifecycle)
        .context("approval does not carry document lifecycle metadata")?
        .clone();
    metadata.validate()?;
    validate_approval_identity(&approval, &metadata)?;

    let mut lifecycle_session = DocumentLifecycleWriterSession::begin(
        profile_root,
        profile_root_identity,
        LifecycleLedgerExpectationV1 {
            project_key: metadata.proposal.project_key.clone(),
            root_id: metadata.proposal.root_id.clone(),
            root_path_identity_sha256: metadata.proposal.root_path_identity_sha256.clone(),
            root_instance_identity_sha256: metadata.proposal.root_instance_identity_sha256.clone(),
        },
    )?;
    let current_load = lifecycle_session.load(now)?;
    if let Some(existing) = current_load
        .events
        .iter()
        .find(|event| event.payload.idempotency_key == metadata.idempotency_key)
    {
        if !event_matches_approval(existing, &approval, &metadata)? {
            bail!("idempotency key already belongs to a different lifecycle payload");
        }
        let consumed = reconcile_consumed_approval(&mut approval_session, &approval)?;
        approval_session.flush()?;
        return Ok(DocumentLifecycleApplyResultV1 {
            schema: DOCUMENT_LIFECYCLE_APPLY_RESULT_SCHEMA.to_string(),
            approval_id: approval.approval_id,
            event: existing.clone(),
            reused_existing: true,
            approval_consumed: consumed,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        });
    }
    if approval.status == ApprovalStatus::Superseded {
        bail!("consumed lifecycle approval has no matching durable event");
    }
    if approval.status != ApprovalStatus::Approved || approval.expires_at < now {
        bail!("lifecycle apply requires one current approved approval");
    }

    let input = review_input_from_metadata(&metadata);
    let context = load_live_context_with_load(project_root, &input, current_load)?;
    let proposal = build_proposal(&input, &context)?;
    if proposal.payload != metadata.proposal || proposal.proposal_sha256 != metadata.proposal_sha256
    {
        bail!("current lifecycle proposal differs from the approved proposal");
    }
    if !proposal.authority_boundary.approval_request_authorized {
        bail!("current evidence cannot authorize lifecycle application");
    }
    ensure_no_pending_observation_conflict(
        approval_session.approvals(),
        &proposal.payload,
        Some(&approval.approval_id),
        now,
    )?;
    let approval_source = approval_source_from_live_approval(&approval, &metadata)?;
    let approval_source_sha256 =
        super::document_lifecycle_approval_source_sha256(&approval_source)?;
    let previous = context.load.events.last();
    let event = DocumentLifecycleEventV1::from_payload(DocumentLifecycleEventPayloadV1 {
        sequence: previous.map_or(1, |event| event.payload.sequence + 1),
        event_id: format!("document_event_{}", Uuid::new_v4()),
        event_kind: metadata.proposal.event_kind,
        recorded_at: StrictUtcTimestamp::from_datetime(now),
        previous_event_sha256: previous.map(|event| event.event_sha256.clone()),
        project_key: metadata.proposal.project_key.clone(),
        root_id: metadata.proposal.root_id.clone(),
        root_path_identity_sha256: metadata.proposal.root_path_identity_sha256.clone(),
        root_instance_identity_sha256: metadata.proposal.root_instance_identity_sha256.clone(),
        document_id: metadata.document_id.clone(),
        expected_document_head_event_id: metadata.proposal.expected_document_head_event_id.clone(),
        observation: metadata.proposal.observation.clone(),
        before: metadata.proposal.before.clone(),
        after: metadata.proposal.after.clone(),
        proposal_sha256: metadata.proposal_sha256.clone(),
        approval_source,
        approval_source_sha256,
        reason: metadata.proposal.reason.clone(),
        idempotency_key: metadata.idempotency_key.clone(),
    })?;
    let mut candidate = context.load.events.clone();
    candidate.push(event.clone());
    super::validate_document_lifecycle_event_chain(&candidate, &context.expectation)?;
    lifecycle_session.append(&event, now)?;
    let consumed = reconcile_consumed_approval(&mut approval_session, &approval)?;
    approval_session.flush()?;
    Ok(DocumentLifecycleApplyResultV1 {
        schema: DOCUMENT_LIFECYCLE_APPLY_RESULT_SCHEMA.to_string(),
        approval_id: approval.approval_id,
        event,
        reused_existing: false,
        approval_consumed: consumed,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    })
}

pub(crate) fn reconcile_existing_retention_event_v2(
    profile_root: &Path,
    profile_root_identity: (u64, u64),
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    now: DateTime<Utc>,
) -> Result<Option<RetentionLifecycleAppendV2>> {
    with_retention_event_v2_lock(
        profile_root,
        profile_root_identity,
        approval,
        metadata,
        false,
        now,
        |result| Ok(result.cloned()),
    )
}

pub(crate) fn with_retention_event_v2_lock<T>(
    profile_root: &Path,
    profile_root_identity: (u64, u64),
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    allow_append: bool,
    now: DateTime<Utc>,
    operation: impl FnOnce(Option<&RetentionLifecycleAppendV2>) -> Result<T>,
) -> Result<T> {
    metadata.validate()?;
    validate_retention_approval_identity_v2(approval, metadata)?;
    let Some(requested_action) = metadata.proposal.requested_action.lifecycle_action() else {
        return operation(None);
    };
    let Some(document_id) = metadata.proposal.target.document_id.as_deref() else {
        return operation(None);
    };
    let expected_head = metadata
        .proposal
        .target
        .expected_document_head_event_id
        .as_deref()
        .context("document-bound retention metadata lacks expected head")?;
    let before = metadata
        .proposal
        .before
        .as_ref()
        .context("document-bound retention metadata lacks before state")?;
    let after = metadata
        .proposal
        .after
        .as_ref()
        .context("document-bound retention metadata lacks after state")?;
    let approval_source = retention_approval_source_v2(
        approval,
        metadata,
        requested_action,
        document_id,
        expected_head,
    )?;
    let approval_source_sha256 =
        super::document_lifecycle_approval_source_sha256(&approval_source)?;
    let expectation = LifecycleLedgerExpectationV1 {
        project_key: metadata.proposal.project_key.clone(),
        root_id: metadata.proposal.root_id.clone(),
        root_path_identity_sha256: metadata.proposal.root_path_identity_sha256.clone(),
        root_instance_identity_sha256: metadata.proposal.root_instance_identity_sha256.clone(),
    };
    let mut lifecycle_session = DocumentLifecycleWriterSession::begin(
        profile_root,
        profile_root_identity,
        expectation.clone(),
    )?;
    let current_load = lifecycle_session.load(now)?;
    if let Some(existing) = current_load
        .events
        .iter()
        .find(|event| event.payload.idempotency_key == metadata.application_id)
    {
        if existing.payload.approval_source != approval_source
            || existing.payload.project_key != metadata.proposal.project_key
            || existing.payload.document_id != document_id
            || existing.payload.expected_document_head_event_id.as_deref() != Some(expected_head)
            || existing.payload.observation != metadata.proposal.target.observation
            || existing.payload.before.as_ref() != Some(before)
            || existing.payload.after != *after
            || existing.payload.proposal_sha256 != metadata.proposal_sha256
        {
            bail!("retention application ID belongs to a different lifecycle event");
        }
        let result = RetentionLifecycleAppendV2 {
            event: existing.clone(),
            reused_existing: true,
        };
        return operation(Some(&result));
    }
    if !allow_append {
        return operation(None);
    }
    if approval.status == ApprovalStatus::Superseded {
        bail!("consumed retention approval has no matching durable lifecycle event");
    }
    if approval.status != ApprovalStatus::Approved || approval.expires_at < now {
        bail!("retention apply requires one current approved approval");
    }
    let current_head = current_load
        .events
        .iter()
        .rev()
        .find(|event| event.payload.document_id == document_id)
        .context("retention target has no durable lifecycle head")?;
    if current_head.payload.event_id != expected_head
        || current_head.payload.observation != metadata.proposal.target.observation
        || current_head.payload.after != *before
    {
        bail!("current lifecycle head differs from the approved retention proposal");
    }
    let previous = current_load.events.last();
    let event = DocumentLifecycleEventV1::from_payload(DocumentLifecycleEventPayloadV1 {
        sequence: previous.map_or(1, |event| event.payload.sequence + 1),
        event_id: format!("document_event_{}", Uuid::new_v4()),
        event_kind: DocumentLifecycleEventKindV1::RetentionTransition,
        recorded_at: StrictUtcTimestamp::from_datetime(now),
        previous_event_sha256: previous.map(|event| event.event_sha256.clone()),
        project_key: metadata.proposal.project_key.clone(),
        root_id: metadata.proposal.root_id.clone(),
        root_path_identity_sha256: metadata.proposal.root_path_identity_sha256.clone(),
        root_instance_identity_sha256: metadata.proposal.root_instance_identity_sha256.clone(),
        document_id: document_id.to_string(),
        expected_document_head_event_id: Some(expected_head.to_string()),
        observation: metadata.proposal.target.observation.clone(),
        before: Some(before.clone()),
        after: after.clone(),
        proposal_sha256: metadata.proposal_sha256.clone(),
        approval_source,
        approval_source_sha256,
        reason: metadata.proposal.reason.clone(),
        idempotency_key: metadata.application_id.clone(),
    })?;
    let mut candidate = current_load.events;
    candidate.push(event.clone());
    super::validate_document_lifecycle_event_chain(&candidate, &expectation)?;
    lifecycle_session.append(&event, now)?;
    let result = RetentionLifecycleAppendV2 {
        event,
        reused_existing: false,
    };
    operation(Some(&result))
}

pub(crate) fn validate_retention_approval_identity_v2(
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
) -> Result<()> {
    metadata.validate()?;
    let expected_risk = match metadata.proposal.requested_action.expected_risk() {
        DetachedRiskLevelV1::CanonicalMutation => RiskLevel::CanonicalMutation,
        DetachedRiskLevelV1::Destructive => RiskLevel::Destructive,
    };
    if approval.project_key != metadata.proposal.project_key
        || approval.request_id != metadata.request_id
        || approval.task_id != metadata.task_id
        || approval.action != "maintenance.artifact_cleanup"
        || approval.scope != ApprovalScope::Once
        || approval.risk_level != expected_risk
        || approval.approval_mode != ApprovalMode::OperatorRequired
        || approval.action_id.is_empty()
        || approval.action_id == approval.approval_id
        || approval.reason != metadata.proposal.reason
        || approval.source_surface != "project.retention_request"
        || approval.created_at != *metadata.generated_at.as_datetime()
        || approval.expires_at <= approval.created_at
    {
        bail!("approval identity is not one exact retention v2 authority");
    }
    if !matches!(
        approval.status,
        ApprovalStatus::Approved | ApprovalStatus::Superseded
    ) {
        bail!("retention approval is neither approved nor already consumed");
    }
    if approval.resolved_at.is_none()
        || approval
            .resolved_by
            .as_deref()
            .map(operator_safe_text)
            .is_none_or(|value| value.is_empty())
    {
        bail!("approved retention approval lacks exact resolution provenance");
    }
    Ok(())
}

fn retention_approval_source_v2(
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    requested_action: RetentionActionV1,
    document_id: &str,
    expected_head: &str,
) -> Result<DocumentLifecycleApprovalSourceV1> {
    validate_retention_approval_identity_v2(approval, metadata)?;
    let resolved_at = approval
        .resolved_at
        .context("approved retention approval lacks resolution time")?;
    let resolved_by = approval
        .resolved_by
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.is_empty())
        .context("approved retention approval lacks resolution actor")?;
    let source = DocumentLifecycleApprovalSourceV1 {
        schema: DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA.to_string(),
        approval_id: approval.approval_id.clone(),
        action_id: approval.action_id.clone(),
        scope: DetachedApprovalScopeV1::Once,
        project_key: approval.project_key.clone(),
        request_id: approval.request_id.clone(),
        task_id: approval.task_id.clone(),
        action: approval.action.clone(),
        risk_level: requested_action.expected_risk(),
        approval_mode: DetachedApprovalModeV1::OperatorRequired,
        created_at: StrictUtcTimestamp::from_datetime(approval.created_at),
        expires_at: StrictUtcTimestamp::from_datetime(approval.expires_at),
        status: DetachedApprovalStatusV1::Approved,
        resolved_at: StrictUtcTimestamp::from_datetime(resolved_at),
        resolved_by,
        source_surface: approval.source_surface.clone(),
        preview_sha256: sha256_hex(approval.preview.as_bytes()),
        reason_sha256: sha256_hex(approval.reason.as_bytes()),
        metadata_schema: RETENTION_APPROVAL_METADATA_SCHEMA_V2.to_string(),
        metadata_sha256: super::retention_approval_metadata_sha256_v2(metadata)?,
        proposal_sha256: metadata.proposal_sha256.clone(),
        target_identity: ApprovalTargetIdentityV1::Retention {
            document_id: document_id.to_string(),
            expected_head_event_id: expected_head.to_string(),
            retention_target_id: metadata.proposal.target.retention_target_id.clone(),
            requested_action,
            application_id: metadata.application_id.clone(),
        },
    };
    source.validate()?;
    Ok(source)
}

pub(crate) fn retention_application_integrity_valid(
    profile_root: &Path,
    approvals: &[PendingActionApproval],
    load: &DocumentLifecycleLedgerLoadV1,
) -> bool {
    validate_retention_application_integrity(profile_root, approvals, load).is_ok()
}

fn validate_retention_application_integrity(
    profile_root: &Path,
    approvals: &[PendingActionApproval],
    load: &DocumentLifecycleLedgerLoadV1,
) -> Result<()> {
    let root = crate::process::open_bounded_root(profile_root)
        .with_context(|| format!("open retention integrity root {}", profile_root.display()))?;
    let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
        .context("retention integrity root lacks stable identity")?;
    for approval in approvals {
        let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_retention_v2)
        else {
            continue;
        };
        if metadata.proposal.project_key != load.expectation.project_key
            || metadata.proposal.root_id != load.expectation.root_id
            || metadata.proposal.root_path_identity_sha256
                != load.expectation.root_path_identity_sha256
            || metadata.proposal.root_instance_identity_sha256
                != load.expectation.root_instance_identity_sha256
        {
            continue;
        }
        let receipt = load_retention_integrity_receipt(&root, metadata)?;
        let event = load
            .events
            .iter()
            .find(|event| event.payload.idempotency_key == metadata.application_id);
        if !matches!(
            approval.status,
            ApprovalStatus::Approved | ApprovalStatus::Superseded
        ) {
            if receipt.is_some() || event.is_some() {
                bail!("non-applicable retention approval has durable application state");
            }
            continue;
        }
        validate_retention_approval_identity_v2(approval, metadata)?;
        if approval.status == ApprovalStatus::Superseded && receipt.is_none() {
            bail!("consumed retention approval lacks its deterministic receipt");
        }
        let Some(receipt) = receipt else {
            continue;
        };
        let lifecycle_action = metadata.proposal.requested_action.lifecycle_action();
        let lifecycle_expected =
            metadata.proposal.target.document_id.is_some() && lifecycle_action.is_some();
        if lifecycle_expected != event.is_some() {
            bail!("retention receipt and lifecycle event presence disagree");
        }
        if let (Some(event), Some(requested_action), Some(document_id), Some(expected_head)) = (
            event,
            lifecycle_action,
            metadata.proposal.target.document_id.as_deref(),
            metadata
                .proposal
                .target
                .expected_document_head_event_id
                .as_deref(),
        ) {
            let source = retention_approval_source_v2(
                approval,
                metadata,
                requested_action,
                document_id,
                expected_head,
            )?;
            let approved_after = metadata
                .proposal
                .after
                .as_ref()
                .context("retention event authority lacks approved after state")?;
            if event.payload.approval_source != source
                || event.payload.project_key != metadata.proposal.project_key
                || event.payload.document_id != document_id
                || event.payload.expected_document_head_event_id.as_deref() != Some(expected_head)
                || event.payload.observation != metadata.proposal.target.observation
                || event.payload.before != metadata.proposal.before
                || &event.payload.after != approved_after
                || event.payload.proposal_sha256 != metadata.proposal_sha256
            {
                bail!("retention event differs from its consumed approval authority");
            }
        }
        validate_retention_integrity_receipt(&receipt, approval, metadata, event)?;
    }
    for event in &load.events {
        if event.payload.event_kind != DocumentLifecycleEventKindV1::RetentionTransition
            || event.payload.approval_source.metadata_schema
                != RETENTION_APPROVAL_METADATA_SCHEMA_V2
        {
            continue;
        }
        let matching_approvals = approvals
            .iter()
            .filter(|approval| {
                approval.approval_id == event.payload.approval_source.approval_id
                    && approval
                        .metadata
                        .as_ref()
                        .and_then(ActionApprovalMetadata::as_retention_v2)
                        .is_some_and(|metadata| {
                            metadata.application_id == event.payload.idempotency_key
                                && metadata.proposal.project_key == load.expectation.project_key
                                && metadata.proposal.root_id == load.expectation.root_id
                                && metadata.proposal.root_path_identity_sha256
                                    == load.expectation.root_path_identity_sha256
                                && metadata.proposal.root_instance_identity_sha256
                                    == load.expectation.root_instance_identity_sha256
                        })
            })
            .count();
        if matching_approvals != 1 {
            bail!("retention lifecycle event lacks one exact canonical approval row");
        }
    }
    let active_root_metadata = fs::symlink_metadata(profile_root).with_context(|| {
        format!(
            "reinspect retention integrity root {}",
            profile_root.display()
        )
    })?;
    if active_root_metadata.file_type().is_symlink()
        || !active_root_metadata.is_dir()
        || crate::process::filesystem_file_identity(&active_root_metadata) != Some(root_identity)
        || crate::process::filesystem_file_identity(&root.metadata()?) != Some(root_identity)
    {
        bail!("retention integrity root changed during validation");
    }
    Ok(())
}

fn load_retention_integrity_receipt(
    root: &File,
    metadata: &RetentionApprovalMetadataV2,
) -> Result<Option<RetentionIntegrityReceiptV2>> {
    let receipt_directory = Path::new(RETENTION_APPLICATIONS_DIRECTORY);
    let initial_directory_snapshot =
        crate::process::bounded_directory_snapshot(root, receipt_directory).ok();
    let relative = Path::new(RETENTION_APPLICATIONS_DIRECTORY)
        .join(format!("{}.json", metadata.application_id));
    let mut file = match crate::process::open_bounded_regular_file(root, &relative) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error).context("open retention integrity receipt"),
    };
    let file_metadata = file
        .metadata()
        .context("inspect retention integrity receipt")?;
    if !file_metadata.is_file() {
        bail!("retention integrity receipt is not a regular file");
    }
    #[cfg(unix)]
    if file_metadata.permissions().mode() & 0o077 != 0 || file_metadata.nlink() != 1 {
        bail!("retention integrity receipt is not owner-only and singly linked");
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(MAX_RETENTION_APPLICATION_RECEIPT_BYTES + 1)
        .read_to_end(&mut bytes)
        .context("read retention integrity receipt")?;
    if bytes.len() as u64 > MAX_RETENTION_APPLICATION_RECEIPT_BYTES {
        bail!("retention integrity receipt exceeds its read budget");
    }
    let final_metadata = file
        .metadata()
        .context("reinspect retention integrity receipt")?;
    let active = crate::process::open_bounded_regular_file(root, &relative)
        .context("reopen active retention integrity receipt")?;
    let active_metadata = active
        .metadata()
        .context("inspect active retention integrity receipt")?;
    if crate::process::filesystem_file_identity(&file_metadata)
        != crate::process::filesystem_file_identity(&final_metadata)
        || crate::process::filesystem_file_identity(&final_metadata)
            != crate::process::filesystem_file_identity(&active_metadata)
        || file_metadata.len() != final_metadata.len()
        || final_metadata.len() != active_metadata.len()
        || file_metadata.modified().ok() != final_metadata.modified().ok()
        || final_metadata.modified().ok() != active_metadata.modified().ok()
        || initial_directory_snapshot.is_none()
        || crate::process::bounded_directory_snapshot(root, receipt_directory).ok()
            != initial_directory_snapshot
    {
        bail!("retention integrity receipt changed during bounded validation");
    }
    let receipt = serde_json::from_slice(&bytes).context("parse retention integrity receipt")?;
    Ok(Some(receipt))
}

fn validate_retention_integrity_receipt(
    receipt: &RetentionIntegrityReceiptV2,
    approval: &PendingActionApproval,
    metadata: &RetentionApprovalMetadataV2,
    event: Option<&DocumentLifecycleEventV1>,
) -> Result<()> {
    let event_receipt = event.map(|event| RetentionIntegrityEventReceiptV2 {
        event_id: event.payload.event_id.clone(),
        event_sha256: event.event_sha256.clone(),
    });
    if receipt.schema != RETENTION_APPLICATION_RECEIPT_SCHEMA_V2
        || receipt.application_id != metadata.application_id
        || receipt.approval_id != approval.approval_id
        || receipt.project_key != metadata.proposal.project_key
        || receipt.proposal_sha256 != metadata.proposal_sha256
        || receipt.approval_metadata_sha256
            != super::retention_approval_metadata_sha256_v2(metadata)?
        || receipt.approval_preview_sha256 != sha256_hex(approval.preview.as_bytes())
        || receipt.retention_target_id != metadata.proposal.target.retention_target_id
        || receipt.requested_action != metadata.proposal.requested_action
        || receipt.lifecycle_event != event_receipt
        || !receipt.plan_only
        || receipt.file_mutation_performed
    {
        bail!("retention application receipt differs from its approval authority");
    }
    Ok(())
}

pub fn document_lifecycle_history(
    profile_root: &Path,
    project_root: &Path,
    project_key: &str,
    document_id: &str,
    now: DateTime<Utc>,
) -> Result<DocumentLifecycleHistoryV1> {
    validate_prefixed_uuid(document_id, "document_", "document_id")?;
    let (canonical_root, root) = super::catalog::observe_explicit_root(project_root, project_key)?;
    let expectation = LifecycleLedgerExpectationV1 {
        project_key: root.project_key,
        root_id: root.root_id,
        root_path_identity_sha256: root.path_identity_sha256,
        root_instance_identity_sha256: root_instance_identity_sha256(&canonical_root)?,
    };
    let options = LifecycleLedgerLoadOptions {
        observed_at: StrictUtcTimestamp::from_datetime(now),
        ..LifecycleLedgerLoadOptions::default()
    };
    let load = if profile_root.exists() {
        load_document_lifecycle_ledger_from_root(profile_root, &expectation, &options)?
    } else {
        absent_ledger_load(&expectation, &options)?
    };
    let events = if load.status == LifecycleProjectionStatusV1::Valid {
        load.events
            .iter()
            .filter(|event| event.payload.document_id == document_id)
            .cloned()
            .collect()
    } else {
        Vec::new()
    };
    Ok(DocumentLifecycleHistoryV1 {
        schema: DOCUMENT_LIFECYCLE_HISTORY_SCHEMA.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(now),
        project_key: expectation.project_key,
        document_id: document_id.to_string(),
        ledger_status: load.status,
        failure_code: load.failure_code,
        events,
        document_body_included: false,
        profile_state_write_authorized: false,
        project_state_write_authorized: false,
        file_mutation_authorized: false,
    })
}

fn load_live_context(
    profile_root: &Path,
    project_root: &Path,
    input: &DocumentLifecycleReviewInput,
) -> Result<LiveLifecycleContext> {
    validate_identifier(&input.project_key, "project_key")?;
    let mut options = input.catalog_options.clone();
    options.project_key = Some(input.project_key.clone());
    let catalog = scan_project(project_root, &options)?;
    let root = catalog
        .roots
        .first()
        .context("document lifecycle requires one catalog root")?;
    let canonical_root = Path::new(&root.canonical_path);
    let root_instance_hash = root_instance_identity_sha256(canonical_root)?;
    let expectation = LifecycleLedgerExpectationV1 {
        project_key: catalog.project_key.clone(),
        root_id: root.root_id.clone(),
        root_path_identity_sha256: root.path_identity_sha256.clone(),
        root_instance_identity_sha256: root_instance_hash,
    };
    let load_options = LifecycleLedgerLoadOptions::default();
    let load = if profile_root.exists() {
        load_document_lifecycle_ledger_from_root(profile_root, &expectation, &load_options)?
    } else {
        absent_ledger_load(&expectation, &load_options)?
    };
    if !matches!(
        load.status,
        LifecycleProjectionStatusV1::Absent | LifecycleProjectionStatusV1::Valid
    ) {
        bail!(
            "lifecycle ledger cannot support a proposal: {}",
            load.failure_code.as_deref().unwrap_or("invalid_ledger")
        );
    }
    if root_instance_identity_sha256(canonical_root)? != expectation.root_instance_identity_sha256 {
        bail!("project root descriptor identity changed during the live scan");
    }
    let unresolved_identity_conflicts = catalog
        .crosswalk_findings
        .iter()
        .filter(|finding| finding.status == CrosswalkStatus::Conflict)
        .count() as u64;
    Ok(LiveLifecycleContext {
        expectation,
        load,
        entries: catalog.entries,
        scan_complete: catalog.scan.complete,
        unresolved_identity_conflicts,
        unresolved_artifact_crosswalk_conflicts: 0,
        filesystem_snapshot_sha256: catalog.source_identities.filesystem_snapshot.content_sha256,
    })
}

fn absent_ledger_load(
    expectation: &LifecycleLedgerExpectationV1,
    options: &LifecycleLedgerLoadOptions,
) -> Result<DocumentLifecycleLedgerLoadV1> {
    let load = DocumentLifecycleLedgerLoadV1 {
        schema: DOCUMENT_LIFECYCLE_LEDGER_LOAD_SCHEMA.to_string(),
        status: LifecycleProjectionStatusV1::Absent,
        expectation: expectation.clone(),
        budgets: options.budgets.clone(),
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

fn load_live_context_with_load(
    project_root: &Path,
    input: &DocumentLifecycleReviewInput,
    load: DocumentLifecycleLedgerLoadV1,
) -> Result<LiveLifecycleContext> {
    validate_identifier(&input.project_key, "project_key")?;
    let mut options = input.catalog_options.clone();
    options.project_key = Some(input.project_key.clone());
    let catalog = scan_project(project_root, &options)?;
    let root = catalog
        .roots
        .first()
        .context("document lifecycle requires one catalog root")?;
    let root_instance_identity_sha256 =
        root_instance_identity_sha256(Path::new(&root.canonical_path))?;
    let expectation = LifecycleLedgerExpectationV1 {
        project_key: catalog.project_key.clone(),
        root_id: root.root_id.clone(),
        root_path_identity_sha256: root.path_identity_sha256.clone(),
        root_instance_identity_sha256,
    };
    if load.expectation != expectation {
        bail!("live project root identity differs from the lifecycle lock identity");
    }
    if !matches!(
        load.status,
        LifecycleProjectionStatusV1::Absent | LifecycleProjectionStatusV1::Valid
    ) {
        bail!("lifecycle ledger is not valid under the acquired writer lock");
    }
    let unresolved_identity_conflicts = catalog
        .crosswalk_findings
        .iter()
        .filter(|finding| finding.status == CrosswalkStatus::Conflict)
        .count() as u64;
    Ok(LiveLifecycleContext {
        expectation,
        load,
        entries: catalog.entries,
        scan_complete: catalog.scan.complete,
        unresolved_identity_conflicts,
        unresolved_artifact_crosswalk_conflicts: 0,
        filesystem_snapshot_sha256: catalog.source_identities.filesystem_snapshot.content_sha256,
    })
}

fn build_proposal(
    input: &DocumentLifecycleReviewInput,
    context: &LiveLifecycleContext,
) -> Result<DocumentLifecycleProposalV1> {
    let safe_reason = operator_safe_text(&input.reason);
    if safe_reason.trim().is_empty() {
        bail!("lifecycle reason cannot be empty");
    }
    let heads = document_heads(&context.load.events);
    let payload = match input.transition {
        LifecycleTransitionV1::MintDocument => {
            if input.document_id.is_some() {
                bail!("mint_document accepts --observation-id, not --document-id");
            }
            let observation_id = input
                .observation_id
                .as_deref()
                .context("mint_document requires --observation-id")?;
            let entry = exact_entry(&context.entries, observation_id)?;
            if context
                .load
                .events
                .iter()
                .any(|event| event.payload.observation.observation_id == observation_id)
            {
                bail!("current observation is already bound to a durable document");
            }
            let activity_state = input
                .activity_state
                .context("mint_document requires --activity-state")?;
            let after = state_from_activity_input(input, activity_state, None)?;
            DocumentLifecycleProposalPayloadV1 {
                project_key: context.expectation.project_key.clone(),
                root_id: context.expectation.root_id.clone(),
                root_path_identity_sha256: context.expectation.root_path_identity_sha256.clone(),
                root_instance_identity_sha256: context
                    .expectation
                    .root_instance_identity_sha256
                    .clone(),
                filesystem_snapshot_sha256: context.filesystem_snapshot_sha256.clone(),
                scan_complete: context.scan_complete,
                unresolved_identity_conflicts: context.unresolved_identity_conflicts,
                unresolved_artifact_crosswalk_conflicts: context
                    .unresolved_artifact_crosswalk_conflicts,
                target_identity: ApprovalTargetIdentityV1::Mint {
                    observation_id: observation_id.to_string(),
                },
                event_kind: input.transition.event_kind(),
                document_id: None,
                expected_document_head_event_id: None,
                observation: lifecycle_observation(entry)?,
                before: None,
                after,
                reason: safe_reason,
            }
        }
        LifecycleTransitionV1::ActivityTransition
        | LifecycleTransitionV1::RevalidateObservation
        | LifecycleTransitionV1::RebindObservation => {
            let document_id = input
                .document_id
                .as_deref()
                .context("existing lifecycle transitions require --document-id")?;
            let head = heads
                .get(document_id)
                .with_context(|| format!("unknown lifecycle document: {document_id}"))?;
            let observation = match input.transition {
                LifecycleTransitionV1::ActivityTransition => {
                    let entry = entry_by_relative_path(
                        &context.entries,
                        &head.observation.relative_path,
                    )
                    .context("reviewed observation path is absent; use rebind_observation")?;
                    let current = lifecycle_observation(entry)?;
                    if current != head.observation {
                        bail!("reviewed observation bytes changed; use revalidate_observation");
                    }
                    current
                }
                LifecycleTransitionV1::RevalidateObservation => {
                    let entry = entry_by_relative_path(
                        &context.entries,
                        &head.observation.relative_path,
                    )
                    .context("reviewed observation path is absent; use rebind_observation")?;
                    let current = lifecycle_observation(entry)?;
                    if current.content_sha256 == head.observation.content_sha256 {
                        bail!("revalidate_observation requires changed bytes at the same path");
                    }
                    ensure_observation_unbound(&context.load.events, &current, document_id)?;
                    current
                }
                LifecycleTransitionV1::RebindObservation => {
                    if entry_by_relative_path(&context.entries, &head.observation.relative_path)
                        .is_some()
                    {
                        bail!("rebind_observation requires the prior path to be absent");
                    }
                    if !context.scan_complete {
                        bail!("rebind_observation requires one complete current scan");
                    }
                    let candidates = context
                        .entries
                        .iter()
                        .filter(|entry| {
                            entry.content_sha256.as_deref()
                                == Some(head.observation.content_sha256.as_str())
                        })
                        .collect::<Vec<_>>();
                    if candidates.len() != 1 {
                        bail!("rebind_observation requires exactly one same-hash candidate");
                    }
                    let current = lifecycle_observation(candidates[0])?;
                    ensure_observation_unbound(&context.load.events, &current, document_id)?;
                    current
                }
                LifecycleTransitionV1::MintDocument => unreachable!(),
            };
            if let Some(selector) = input.observation_id.as_deref() {
                if selector != observation.observation_id {
                    bail!("--observation-id and --document-id do not resolve to the same row");
                }
            }
            let after = match input.transition {
                LifecycleTransitionV1::ActivityTransition => {
                    let activity_state = input
                        .activity_state
                        .context("activity_transition requires --activity-state")?;
                    state_from_activity_input(input, activity_state, Some(&head.state))?
                }
                LifecycleTransitionV1::RevalidateObservation
                | LifecycleTransitionV1::RebindObservation => {
                    reject_state_options(input)?;
                    head.state.clone()
                }
                LifecycleTransitionV1::MintDocument => unreachable!(),
            };
            DocumentLifecycleProposalPayloadV1 {
                project_key: context.expectation.project_key.clone(),
                root_id: context.expectation.root_id.clone(),
                root_path_identity_sha256: context.expectation.root_path_identity_sha256.clone(),
                root_instance_identity_sha256: context
                    .expectation
                    .root_instance_identity_sha256
                    .clone(),
                filesystem_snapshot_sha256: context.filesystem_snapshot_sha256.clone(),
                scan_complete: context.scan_complete,
                unresolved_identity_conflicts: context.unresolved_identity_conflicts,
                unresolved_artifact_crosswalk_conflicts: context
                    .unresolved_artifact_crosswalk_conflicts,
                target_identity: ApprovalTargetIdentityV1::Document {
                    document_id: document_id.to_string(),
                    expected_head_event_id: head.event_id.clone(),
                },
                event_kind: input.transition.event_kind(),
                document_id: Some(document_id.to_string()),
                expected_document_head_event_id: Some(head.event_id.clone()),
                observation,
                before: Some(head.state.clone()),
                after,
                reason: safe_reason,
            }
        }
    };
    validate_proposal_references(&payload, &heads, &context.entries)?;
    validate_proposal_payload(&payload)?;
    let proposal = DocumentLifecycleProposalV1 {
        schema: DOCUMENT_LIFECYCLE_PROPOSAL_SCHEMA.to_string(),
        generated_at: StrictUtcTimestamp::from_datetime(Utc::now()),
        proposal_sha256: proposal_sha256(&payload)?,
        authority_boundary: LifecycleProposalAuthorityV1 {
            read_only: true,
            approval_required: true,
            approval_request_authorized: payload.scan_complete
                && payload.unresolved_identity_conflicts == 0
                && payload.unresolved_artifact_crosswalk_conflicts == 0,
            profile_state_write_authorized: false,
            project_state_write_authorized: false,
            file_mutation_authorized: false,
        },
        payload,
    };
    proposal.validate()?;
    Ok(proposal)
}

fn validate_proposal_references(
    payload: &DocumentLifecycleProposalPayloadV1,
    heads: &BTreeMap<String, DocumentHead>,
    entries: &[DocumentCatalogEntry],
) -> Result<()> {
    for (label, reference) in [
        ("successor", &payload.after.successor_document_id),
        ("active owner", &payload.after.active_owner_document_id),
        ("summary", &payload.after.summary_document_id),
    ] {
        let Some(reference) = reference else {
            continue;
        };
        let target = heads
            .get(reference)
            .with_context(|| format!("{label} references an unknown lifecycle document"))?;
        if target.state.activity_state != ActivityState::Active {
            bail!("{label} must reference a reviewed-active document");
        }
        let current = entry_by_relative_path(entries, &target.observation.relative_path)
            .with_context(|| format!("{label} reviewed observation is not currently present"))?;
        if lifecycle_observation(current)? != target.observation {
            bail!("{label} reviewed observation is stale");
        }
    }

    let Some(document_id) = payload.document_id.as_ref() else {
        return Ok(());
    };
    let mut states = heads
        .iter()
        .map(|(document_id, head)| (document_id.clone(), head.state.clone()))
        .collect::<BTreeMap<_, _>>();
    states.insert(document_id.clone(), payload.after.clone());
    fn visit<'a>(
        document_id: &'a str,
        states: &'a BTreeMap<String, DocumentLifecycleStateV1>,
        visiting: &mut std::collections::BTreeSet<&'a str>,
        complete: &mut std::collections::BTreeSet<&'a str>,
    ) -> Result<()> {
        if complete.contains(document_id) {
            return Ok(());
        }
        if !visiting.insert(document_id) {
            bail!("successor and owner graph must remain acyclic");
        }
        let state = states
            .get(document_id)
            .context("lifecycle graph references an unknown document")?;
        for reference in [
            state.successor_document_id.as_deref(),
            state.active_owner_document_id.as_deref(),
        ]
        .into_iter()
        .flatten()
        {
            visit(reference, states, visiting, complete)?;
        }
        visiting.remove(document_id);
        complete.insert(document_id);
        Ok(())
    }
    let mut complete = std::collections::BTreeSet::new();
    for document_id in states.keys() {
        visit(
            document_id,
            &states,
            &mut std::collections::BTreeSet::new(),
            &mut complete,
        )?;
    }
    Ok(())
}

fn validate_proposal_payload(payload: &DocumentLifecycleProposalPayloadV1) -> Result<()> {
    validate_identifier(&payload.project_key, "project_key")?;
    validate_prefixed_identifier(&payload.root_id, "root_", "root_id")?;
    validate_sha256(&payload.root_path_identity_sha256, "root path identity")?;
    validate_sha256(
        &payload.root_instance_identity_sha256,
        "root instance identity",
    )?;
    validate_sha256(&payload.filesystem_snapshot_sha256, "filesystem snapshot")?;
    if payload.reason.trim().is_empty() || operator_safe_text(&payload.reason) != payload.reason {
        bail!("proposal reason must already be operator-safe text");
    }
    match (&payload.event_kind, &payload.target_identity) {
        (
            DocumentLifecycleEventKindV1::MintDocument,
            ApprovalTargetIdentityV1::Mint { observation_id },
        ) if payload.document_id.is_none()
            && payload.expected_document_head_event_id.is_none()
            && payload.before.is_none()
            && observation_id == &payload.observation.observation_id => {}
        (
            DocumentLifecycleEventKindV1::ActivityTransition
            | DocumentLifecycleEventKindV1::RevalidateObservation
            | DocumentLifecycleEventKindV1::RebindObservation,
            ApprovalTargetIdentityV1::Document {
                document_id,
                expected_head_event_id,
            },
        ) if payload.document_id.as_ref() == Some(document_id)
            && payload.expected_document_head_event_id.as_ref() == Some(expected_head_event_id)
            && payload.before.is_some() =>
        {
            validate_prefixed_uuid(document_id, "document_", "document_id")?;
            validate_prefixed_uuid(
                expected_head_event_id,
                "document_event_",
                "expected head event ID",
            )?;
        }
        _ => bail!("proposal event kind and target identity are inconsistent"),
    }
    if payload.event_kind == DocumentLifecycleEventKindV1::RetentionTransition {
        bail!("Phase 2C cannot propose retention transitions");
    }
    if payload.after.activity_state == ActivityState::Unknown {
        bail!("reviewed lifecycle activity cannot be unknown");
    }
    if payload.after.activity_state == ActivityState::Superseded
        && payload.after.successor_document_id.is_none()
    {
        bail!("superseded activity requires one successor document");
    }
    if matches!(
        payload.after.activity_state,
        ActivityState::Draft
            | ActivityState::Candidate
            | ActivityState::Active
            | ActivityState::ReviewDue
    ) && payload.after.successor_document_id.is_some()
    {
        bail!("current activity cannot retain a successor document");
    }
    for (label, reference) in [
        (
            "successor_document_id",
            &payload.after.successor_document_id,
        ),
        (
            "active_owner_document_id",
            &payload.after.active_owner_document_id,
        ),
        ("summary_document_id", &payload.after.summary_document_id),
    ] {
        if let Some(reference) = reference {
            validate_prefixed_uuid(reference, "document_", label)?;
            if payload.document_id.as_ref() == Some(reference) {
                bail!("proposal lifecycle state cannot reference its own document");
            }
        }
    }
    if payload.after.retention_disposition
        != payload
            .before
            .as_ref()
            .map_or(RetentionDisposition::Unclassified, |before| {
                before.retention_disposition
            })
    {
        bail!("Phase 2C proposal cannot alter retention disposition");
    }
    validate_identifier(&payload.observation.observation_id, "observation_id")?;
    let relative_path = Path::new(&payload.observation.relative_path);
    if relative_path.is_absolute()
        || relative_path.as_os_str().is_empty()
        || !relative_path
            .components()
            .all(|component| matches!(component, std::path::Component::Normal(_)))
    {
        bail!("lifecycle observation path must be one bounded relative path");
    }
    validate_sha256(
        &payload.observation.content_sha256,
        "observation content hash",
    )?;
    let expected_observation_id = super::catalog::observation_id(
        &payload.root_id,
        &payload.observation.relative_path,
        payload.observation.size_bytes,
        Some(payload.observation.modified_at.as_datetime()),
        Some(&payload.observation.content_sha256),
    );
    if expected_observation_id != payload.observation.observation_id {
        bail!("proposal observation ID does not match the exact filesystem tuple");
    }
    Ok(())
}

fn proposal_sha256(payload: &DocumentLifecycleProposalPayloadV1) -> Result<String> {
    let bytes = serde_json::to_vec(payload).context("serialize canonical lifecycle proposal")?;
    Ok(domain_hash(PROPOSAL_HASH_DOMAIN, &[&bytes]))
}

pub fn document_lifecycle_approval_metadata_sha256(
    metadata: &DocumentLifecycleApprovalMetadataV1,
) -> Result<String> {
    metadata.validate()?;
    let canonical =
        serde_json::to_value(metadata).context("canonicalize lifecycle approval metadata")?;
    let bytes = serde_json::to_vec(&canonical).context("serialize lifecycle approval metadata")?;
    Ok(domain_hash(METADATA_HASH_DOMAIN, &[&bytes]))
}

fn document_heads(events: &[DocumentLifecycleEventV1]) -> BTreeMap<String, DocumentHead> {
    let mut heads = BTreeMap::new();
    for event in events {
        heads.insert(
            event.payload.document_id.clone(),
            DocumentHead {
                event_id: event.payload.event_id.clone(),
                observation: event.payload.observation.clone(),
                state: event.payload.after.clone(),
            },
        );
    }
    heads
}

fn exact_entry<'a>(
    entries: &'a [DocumentCatalogEntry],
    observation_id: &str,
) -> Result<&'a DocumentCatalogEntry> {
    let matches = entries
        .iter()
        .filter(|entry| entry.observation_id == observation_id)
        .collect::<Vec<_>>();
    match matches.as_slice() {
        [entry] => Ok(entry),
        [] => bail!("observation is not present in the current bounded catalog"),
        _ => bail!("observation selector is ambiguous in the current bounded catalog"),
    }
}

fn entry_by_relative_path<'a>(
    entries: &'a [DocumentCatalogEntry],
    relative_path: &str,
) -> Option<&'a DocumentCatalogEntry> {
    entries
        .iter()
        .find(|entry| entry.relative_path == relative_path)
}

fn lifecycle_observation(entry: &DocumentCatalogEntry) -> Result<DocumentLifecycleObservationV1> {
    Ok(DocumentLifecycleObservationV1 {
        observation_id: entry.observation_id.clone(),
        relative_path: entry.relative_path.clone(),
        size_bytes: entry.size_bytes,
        modified_at: StrictUtcTimestamp::from_datetime(
            entry
                .modified_at
                .context("lifecycle target lacks an exact modification time")?,
        ),
        content_sha256: entry
            .content_sha256
            .clone()
            .context("lifecycle target lacks one full content hash")?,
    })
}

fn ensure_observation_unbound(
    events: &[DocumentLifecycleEventV1],
    observation: &DocumentLifecycleObservationV1,
    target_document_id: &str,
) -> Result<()> {
    if events.iter().any(|event| {
        event.payload.observation.observation_id == observation.observation_id
            && event.payload.document_id != target_document_id
    }) {
        bail!("current observation is already bound to a different document");
    }
    Ok(())
}

fn state_from_activity_input(
    input: &DocumentLifecycleReviewInput,
    activity_state: ActivityState,
    before: Option<&DocumentLifecycleStateV1>,
) -> Result<DocumentLifecycleStateV1> {
    if activity_state == ActivityState::Unknown {
        bail!("reviewed lifecycle activity cannot be unknown");
    }
    if activity_state == ActivityState::Superseded && input.successor_document_id.is_none() {
        bail!("superseded activity requires --successor-document-id");
    }
    if matches!(
        activity_state,
        ActivityState::Draft
            | ActivityState::Candidate
            | ActivityState::Active
            | ActivityState::ReviewDue
    ) && input.successor_document_id.is_some()
    {
        bail!("current activity cannot retain a successor document");
    }
    let state = DocumentLifecycleStateV1 {
        activity_state,
        retention_disposition: before.map_or(RetentionDisposition::Unclassified, |state| {
            state.retention_disposition
        }),
        successor_document_id: input.successor_document_id.clone(),
        active_owner_document_id: input.active_owner_document_id.clone(),
        summary_document_id: before.and_then(|state| state.summary_document_id.clone()),
        review_after: input.review_after.clone(),
    };
    if before.is_some_and(|before| before == &state) {
        bail!("activity transition must change reviewed state");
    }
    Ok(state)
}

fn reject_state_options(input: &DocumentLifecycleReviewInput) -> Result<()> {
    if input.activity_state.is_some()
        || input.successor_document_id.is_some()
        || input.active_owner_document_id.is_some()
        || input.review_after.is_some()
    {
        bail!("observation-only transitions cannot alter lifecycle state");
    }
    Ok(())
}

fn lifecycle_preview(proposal: &DocumentLifecycleProposalV1) -> String {
    lifecycle_preview_payload(&proposal.payload)
}

fn lifecycle_preview_payload(payload: &DocumentLifecycleProposalPayloadV1) -> String {
    format!(
        "Apply {:?} for {} at {} without moving or editing project files",
        payload.event_kind,
        payload
            .document_id
            .as_deref()
            .unwrap_or(&payload.observation.observation_id),
        payload.observation.relative_path
    )
}

fn lifecycle_approval_brief(proposal: &DocumentLifecycleProposalV1) -> ApprovalBrief {
    let mut impacts = HashMap::new();
    impacts.insert(
        "approve".to_string(),
        "Allow one exact append-only lifecycle event after fresh filesystem and head validation."
            .to_string(),
    );
    impacts.insert(
        "deny".to_string(),
        "Leave lifecycle state unchanged and keep the current filesystem observation visible."
            .to_string(),
    );
    impacts.insert(
        "defer".to_string(),
        "Leave the request pending until the proposal evidence is reviewed.".to_string(),
    );
    let mut context = HashMap::new();
    context.insert(
        "proposal_sha256".to_string(),
        proposal.proposal_sha256.clone(),
    );
    context.insert(
        "relative_path".to_string(),
        proposal.payload.observation.relative_path.clone(),
    );
    ApprovalBrief {
        schema: "approval_brief.v1".to_string(),
        recommendation: "approve".to_string(),
        subject: "document lifecycle transition".to_string(),
        summary_lines: vec![
            lifecycle_preview(proposal),
            format!("Reason: {}", proposal.payload.reason),
        ],
        judgment_route_summary: Some(
            "Fresh bounded observation and current lifecycle head were used to build this proposal."
                .to_string(),
        ),
        evidence_sufficiency: Some(format!(
            "Scan complete: {}; exact observation and root identities are bound by the proposal hash.",
            proposal.payload.scan_complete
        )),
        default_if_no_reply: Some("defer".to_string()),
        scope: "Approves one profile-local lifecycle event only. It does not approve project file edits, movement, quarantine, archive, or disposal.".to_string(),
        question: "Approve this exact document lifecycle transition?".to_string(),
        options: vec![
            ApprovalBriefOption {
                id: "approve".to_string(),
                label: "Approve transition".to_string(),
                description: impacts["approve"].clone(),
                natural_input_prompt: None,
            },
            ApprovalBriefOption {
                id: "deny".to_string(),
                label: "Deny transition".to_string(),
                description: impacts["deny"].clone(),
                natural_input_prompt: Some("Explain why the lifecycle transition is unsafe or incorrect.".to_string()),
            },
            ApprovalBriefOption {
                id: "defer".to_string(),
                label: "Need more detail".to_string(),
                description: impacts["defer"].clone(),
                natural_input_prompt: Some("State what evidence should be checked before approval.".to_string()),
            },
        ],
        why_recommendation: vec![
            "The proposal is bound to a current exact observation and root identity.".to_string(),
            "Application revalidates the proposal while holding the approval and lifecycle locks."
                .to_string(),
        ],
        evidence: vec![
            format!("Observation: {}", proposal.payload.observation.observation_id),
            format!("Proposal: {}", proposal.proposal_sha256),
        ],
        decision_impacts: impacts,
        reply_examples: vec![
            "approve".to_string(),
            "deny - the selected lifecycle state is incorrect".to_string(),
            "defer - verify the successor first".to_string(),
        ],
        context,
        source: Some("project.document_lifecycle".to_string()),
    }
}

fn find_existing_proposal_approval<'a>(
    approvals: &'a [PendingActionApproval],
    proposal: &DocumentLifecycleProposalPayloadV1,
    proposal_sha256: &str,
    now: DateTime<Utc>,
) -> Result<Option<&'a PendingActionApproval>> {
    let mut exact = None;
    for approval in approvals.iter().filter(|approval| {
        matches!(
            approval.status,
            ApprovalStatus::Pending | ApprovalStatus::Approved
        ) && approval.expires_at >= now
    }) {
        let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_document_lifecycle)
        else {
            continue;
        };
        metadata.validate()?;
        if metadata.proposal.target_identity == proposal.target_identity {
            if metadata.proposal_sha256 == proposal_sha256 && metadata.proposal == *proposal {
                exact = Some(approval);
            } else {
                bail!("another unresolved lifecycle proposal already targets this document head");
            }
        }
    }
    Ok(exact)
}

fn ensure_no_pending_observation_conflict(
    approvals: &[PendingActionApproval],
    proposal: &DocumentLifecycleProposalPayloadV1,
    current_approval_id: Option<&str>,
    now: DateTime<Utc>,
) -> Result<()> {
    for approval in approvals.iter().filter(|approval| {
        Some(approval.approval_id.as_str()) != current_approval_id
            && matches!(
                approval.status,
                ApprovalStatus::Pending | ApprovalStatus::Approved
            )
            && approval.expires_at >= now
    }) {
        let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(ActionApprovalMetadata::as_document_lifecycle)
        else {
            continue;
        };
        metadata.validate()?;
        if metadata.proposal.observation.observation_id == proposal.observation.observation_id
            && (metadata.proposal.target_identity != proposal.target_identity
                || metadata.proposal_sha256 != proposal_sha256(proposal)?)
        {
            bail!("current observation has another unresolved lifecycle binding proposal");
        }
    }
    Ok(())
}

fn review_input_from_metadata(
    metadata: &DocumentLifecycleApprovalMetadataV1,
) -> DocumentLifecycleReviewInput {
    let (observation_id, document_id) = match &metadata.proposal.target_identity {
        ApprovalTargetIdentityV1::Mint { observation_id } => (Some(observation_id.clone()), None),
        ApprovalTargetIdentityV1::Document { document_id, .. } => (None, Some(document_id.clone())),
        ApprovalTargetIdentityV1::Retention { .. } => {
            unreachable!("metadata validation rejects retention")
        }
    };
    let transition = match metadata.proposal.event_kind {
        DocumentLifecycleEventKindV1::MintDocument => LifecycleTransitionV1::MintDocument,
        DocumentLifecycleEventKindV1::ActivityTransition => {
            LifecycleTransitionV1::ActivityTransition
        }
        DocumentLifecycleEventKindV1::RevalidateObservation => {
            LifecycleTransitionV1::RevalidateObservation
        }
        DocumentLifecycleEventKindV1::RebindObservation => LifecycleTransitionV1::RebindObservation,
        DocumentLifecycleEventKindV1::RetentionTransition => {
            unreachable!("metadata validation rejects retention")
        }
    };
    let state_options = transition == LifecycleTransitionV1::MintDocument
        || transition == LifecycleTransitionV1::ActivityTransition;
    DocumentLifecycleReviewInput {
        project_key: metadata.proposal.project_key.clone(),
        observation_id,
        document_id,
        transition,
        activity_state: state_options.then_some(metadata.proposal.after.activity_state),
        successor_document_id: state_options
            .then(|| metadata.proposal.after.successor_document_id.clone())
            .flatten(),
        active_owner_document_id: state_options
            .then(|| metadata.proposal.after.active_owner_document_id.clone())
            .flatten(),
        review_after: state_options
            .then(|| metadata.proposal.after.review_after.clone())
            .flatten(),
        reason: metadata.proposal.reason.clone(),
        catalog_options: CatalogOptions::default(),
    }
}

fn validate_approval_identity(
    approval: &PendingActionApproval,
    metadata: &DocumentLifecycleApprovalMetadataV1,
) -> Result<()> {
    if approval.project_key != metadata.proposal.project_key
        || approval.request_id != metadata.request_id
        || approval.task_id != metadata.task_id
        || approval.action != LIFECYCLE_ACTION
        || approval.scope != ApprovalScope::Once
        || approval.risk_level != RiskLevel::CanonicalMutation
        || approval.approval_mode != ApprovalMode::OperatorRequired
        || approval.action_id == approval.approval_id
        || approval.action_id.is_empty()
        || approval.reason != metadata.proposal.reason
        || approval.preview != lifecycle_preview_payload(&metadata.proposal)
    {
        bail!("approval identity is not one exact lifecycle transition authority");
    }
    if !matches!(
        approval.status,
        ApprovalStatus::Approved | ApprovalStatus::Superseded
    ) {
        bail!("lifecycle approval is neither approved nor already consumed");
    }
    Ok(())
}

fn approval_source_from_live_approval(
    approval: &PendingActionApproval,
    metadata: &DocumentLifecycleApprovalMetadataV1,
) -> Result<DocumentLifecycleApprovalSourceV1> {
    validate_approval_identity(approval, metadata)?;
    let resolved_at = approval
        .resolved_at
        .context("approved lifecycle approval lacks resolution time")?;
    let resolved_by = approval
        .resolved_by
        .as_deref()
        .map(operator_safe_text)
        .filter(|value| !value.is_empty())
        .context("approved lifecycle approval lacks resolution actor")?;
    let source = DocumentLifecycleApprovalSourceV1 {
        schema: DOCUMENT_LIFECYCLE_APPROVAL_SOURCE_SCHEMA.to_string(),
        approval_id: approval.approval_id.clone(),
        action_id: approval.action_id.clone(),
        scope: DetachedApprovalScopeV1::Once,
        project_key: approval.project_key.clone(),
        request_id: approval.request_id.clone(),
        task_id: approval.task_id.clone(),
        action: approval.action.clone(),
        risk_level: DetachedRiskLevelV1::CanonicalMutation,
        approval_mode: DetachedApprovalModeV1::OperatorRequired,
        created_at: StrictUtcTimestamp::from_datetime(approval.created_at),
        expires_at: StrictUtcTimestamp::from_datetime(approval.expires_at),
        status: DetachedApprovalStatusV1::Approved,
        resolved_at: StrictUtcTimestamp::from_datetime(resolved_at),
        resolved_by,
        source_surface: approval.source_surface.clone(),
        preview_sha256: sha256_hex(approval.preview.as_bytes()),
        reason_sha256: sha256_hex(approval.reason.as_bytes()),
        metadata_schema: DOCUMENT_LIFECYCLE_APPROVAL_METADATA_SCHEMA.to_string(),
        metadata_sha256: document_lifecycle_approval_metadata_sha256(metadata)?,
        proposal_sha256: metadata.proposal_sha256.clone(),
        target_identity: metadata.proposal.target_identity.clone(),
    };
    source.validate()?;
    Ok(source)
}

fn event_matches_approval(
    event: &DocumentLifecycleEventV1,
    approval: &PendingActionApproval,
    metadata: &DocumentLifecycleApprovalMetadataV1,
) -> Result<bool> {
    let payload = &event.payload;
    let expected_source = approval_source_from_live_approval(approval, metadata)?;
    if payload.approval_source != expected_source
        || payload.approval_source_sha256
            != super::document_lifecycle_approval_source_sha256(&expected_source)?
    {
        return Ok(false);
    }
    Ok(payload.project_key == metadata.proposal.project_key
        && payload.root_id == metadata.proposal.root_id
        && payload.root_path_identity_sha256 == metadata.proposal.root_path_identity_sha256
        && payload.root_instance_identity_sha256 == metadata.proposal.root_instance_identity_sha256
        && payload.document_id == metadata.document_id
        && payload.event_kind == metadata.proposal.event_kind
        && payload.expected_document_head_event_id
            == metadata.proposal.expected_document_head_event_id
        && payload.observation == metadata.proposal.observation
        && payload.before == metadata.proposal.before
        && payload.after == metadata.proposal.after
        && payload.proposal_sha256 == metadata.proposal_sha256
        && payload.reason == metadata.proposal.reason
        && payload.idempotency_key == metadata.idempotency_key)
}

fn reconcile_consumed_approval(
    session: &mut crate::offdesk::approval::ApprovalLedgerSession,
    approval: &PendingActionApproval,
) -> Result<bool> {
    match approval.status {
        ApprovalStatus::Approved => {
            session
                .supersede_approval(&approval.approval_id, "lifecycle_event_committed")
                .context("approved lifecycle row disappeared while its lock was held")?;
            Ok(true)
        }
        ApprovalStatus::Superseded => Ok(true),
        _ => bail!("only an approved lifecycle approval can be consumed"),
    }
}

struct DocumentLifecycleWriterSession {
    profile_root: PathBuf,
    root: File,
    root_identity: (u64, u64),
    _lock: LifecycleLockGuard,
    expectation: LifecycleLedgerExpectationV1,
}

struct LifecycleLockGuard {
    file: File,
}

impl Drop for LifecycleLockGuard {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

impl DocumentLifecycleWriterSession {
    fn begin(
        profile_root: &Path,
        expected_root_identity: (u64, u64),
        expectation: LifecycleLedgerExpectationV1,
    ) -> Result<Self> {
        expectation.validate()?;
        let metadata = fs::symlink_metadata(profile_root)
            .with_context(|| format!("read active profile root {}", profile_root.display()))?;
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            bail!("active profile root is not one safe directory");
        }
        let root = crate::process::open_bounded_root(profile_root)
            .with_context(|| format!("open active profile root {}", profile_root.display()))?;
        let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("active profile root lacks a stable filesystem identity")?;
        if root_identity != expected_root_identity {
            bail!("active profile root changed after the approval session was opened");
        }
        crate::process::ensure_bounded_directory(&root, Path::new(LIFECYCLE_DIRECTORY))?;
        crate::process::ensure_bounded_directory(&root, Path::new(LIFECYCLE_VERSION_DIRECTORY))?;
        crate::process::ensure_bounded_directory(&root, Path::new(LIFECYCLE_EVENTS_DIRECTORY))?;
        let lock_file = crate::process::open_or_create_bounded_regular_file(
            &root,
            Path::new(LIFECYCLE_LOCK_PATH),
        )?;
        ensure_owner_only_single_file(&lock_file, "lifecycle lock")?;
        lock_file
            .lock_exclusive()
            .context("lock lifecycle ledger")?;
        let session = Self {
            profile_root: profile_root.to_path_buf(),
            root,
            root_identity,
            _lock: LifecycleLockGuard { file: lock_file },
            expectation,
        };
        session.ensure_root_unchanged()?;
        Ok(session)
    }

    fn load(&self, now: DateTime<Utc>) -> Result<DocumentLifecycleLedgerLoadV1> {
        self.ensure_root_unchanged()?;
        let load = load_document_lifecycle_ledger_from_root(
            &self.profile_root,
            &self.expectation,
            &LifecycleLedgerLoadOptions {
                observed_at: StrictUtcTimestamp::from_datetime(now),
                ..LifecycleLedgerLoadOptions::default()
            },
        )?;
        self.ensure_root_unchanged()?;
        if !matches!(
            load.status,
            LifecycleProjectionStatusV1::Absent | LifecycleProjectionStatusV1::Valid
        ) {
            bail!(
                "lifecycle writer requires a complete valid ledger: {}",
                load.failure_code.as_deref().unwrap_or("invalid_ledger")
            );
        }
        Ok(load)
    }

    fn append(&mut self, event: &DocumentLifecycleEventV1, now: DateTime<Utc>) -> Result<()> {
        self.ensure_root_unchanged()?;
        let final_name = super::reviewed::lifecycle_event_filename(event);
        let partial_name = format!(".{final_name}.{}.partial", Uuid::new_v4());
        let partial_relative = Path::new(LIFECYCLE_EVENTS_DIRECTORY).join(&partial_name);
        let mut partial =
            crate::process::create_new_bounded_regular_file(&self.root, &partial_relative)
                .with_context(|| format!("create lifecycle partial {partial_name}"))?;
        ensure_owner_only_single_file(&partial, "lifecycle partial")?;
        let bytes = serde_json::to_vec_pretty(event).context("serialize lifecycle event")?;
        partial
            .write_all(&bytes)
            .context("write lifecycle partial")?;
        partial.sync_all().context("sync lifecycle partial")?;
        drop(partial);
        crate::process::finalize_bounded_file_noreplace(
            &self.root,
            Path::new(LIFECYCLE_EVENTS_DIRECTORY),
            Path::new(&partial_name),
            Path::new(&final_name),
        )
        .with_context(|| format!("finalize lifecycle event {final_name}"))?;
        self.ensure_root_unchanged()?;
        let reloaded = self.load(now)?;
        let durable = reloaded
            .events
            .last()
            .context("durable lifecycle append did not produce a ledger head")?;
        if durable != event {
            bail!("durable lifecycle head differs from the appended event");
        }
        Ok(())
    }

    fn ensure_root_unchanged(&self) -> Result<()> {
        let reopened = crate::process::open_bounded_root(&self.profile_root)
            .context("reopen active profile root")?;
        if crate::process::filesystem_file_identity(&reopened.metadata()?)
            != Some(self.root_identity)
        {
            bail!("active profile root was replaced while lifecycle lock was held");
        }
        Ok(())
    }
}

pub(crate) fn root_instance_identity_sha256(project_root: &Path) -> Result<String> {
    let root = crate::process::open_bounded_root(project_root)
        .with_context(|| format!("open project root descriptor {}", project_root.display()))?;
    root_instance_identity_sha256_from_descriptor(&root)
}

pub(crate) fn root_instance_identity_sha256_from_descriptor(root: &File) -> Result<String> {
    let (device, inode) = crate::process::filesystem_file_identity(&root.metadata()?)
        .context("project root lacks a stable descriptor identity")?;
    Ok(domain_hash(
        ROOT_INSTANCE_HASH_DOMAIN,
        &[&device.to_be_bytes(), &inode.to_be_bytes()],
    ))
}

fn validate_active_profile_root(profile_root: &Path) -> Result<()> {
    let path_metadata = fs::symlink_metadata(profile_root)
        .with_context(|| format!("inspect active profile root {}", profile_root.display()))?;
    if path_metadata.file_type().is_symlink() || !path_metadata.is_dir() {
        bail!("active profile root is not one safe directory");
    }
    let descriptor = crate::process::open_bounded_root(profile_root)
        .with_context(|| format!("open active profile root {}", profile_root.display()))?;
    if crate::process::filesystem_file_identity(&path_metadata)
        != crate::process::filesystem_file_identity(&descriptor.metadata()?)
    {
        bail!("active profile root changed while its descriptor was opened");
    }
    Ok(())
}

fn ensure_owner_only_single_file(file: &File, label: &str) -> Result<()> {
    let metadata = file
        .metadata()
        .with_context(|| format!("inspect {label}"))?;
    if !metadata.is_file() {
        bail!("{label} is not a regular file");
    }
    #[cfg(unix)]
    if metadata.mode() & 0o077 != 0 || metadata.nlink() != 1 {
        bail!("{label} must be owner-only with one filesystem link");
    }
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
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

fn validate_prefixed_identifier(value: &str, prefix: &str, label: &str) -> Result<()> {
    validate_identifier(value, label)?;
    if !value.starts_with(prefix) {
        bail!("{label} must start with {prefix}");
    }
    Ok(())
}

fn validate_prefixed_uuid(value: &str, prefix: &str, label: &str) -> Result<()> {
    let suffix = value
        .strip_prefix(prefix)
        .with_context(|| format!("{label} must start with {prefix}"))?;
    let parsed = Uuid::parse_str(suffix).with_context(|| format!("{label} must contain a UUID"))?;
    if parsed.to_string() != suffix {
        bail!("{label} UUID must use lowercase hyphenated form");
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Barrier};

    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf, String) {
        let base = tempfile::TempDir::new().unwrap();
        let project = base.path().join("project");
        let profile = base.path().join("profile");
        fs::create_dir_all(project.join("docs")).unwrap();
        fs::create_dir(&profile).unwrap();
        #[cfg(unix)]
        fs::set_permissions(&profile, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(project.join("docs/report.md"), "# Current report\n").unwrap();
        let catalog = scan_project(
            &project,
            &CatalogOptions {
                project_key: Some("phase2c-test".to_string()),
                ..CatalogOptions::default()
            },
        )
        .unwrap();
        let observation_id = catalog.entries[0].observation_id.clone();
        (base, project, profile, observation_id)
    }

    fn mint_input(observation_id: &str) -> DocumentLifecycleReviewInput {
        DocumentLifecycleReviewInput {
            project_key: "phase2c-test".to_string(),
            observation_id: Some(observation_id.to_string()),
            document_id: None,
            transition: LifecycleTransitionV1::MintDocument,
            activity_state: Some(ActivityState::Active),
            successor_document_id: None,
            active_owner_document_id: None,
            review_after: None,
            reason: "Establish the reviewed current report.".to_string(),
            catalog_options: CatalogOptions::default(),
        }
    }

    fn approved_mint(
        project: &Path,
        profile: &Path,
        observation_id: &str,
    ) -> (PendingActionApproval, DateTime<Utc>) {
        let input = mint_input(observation_id);
        let proposal = review_document_lifecycle(profile, project, &input).unwrap();
        let requested_at = Utc::now() - Duration::seconds(2);
        let request = request_document_lifecycle(
            profile,
            project,
            &input,
            &proposal.proposal_sha256,
            None,
            None,
            requested_at,
        )
        .unwrap();
        let approved_at = requested_at + Duration::seconds(1);
        let approval = ApprovalLedger::new(profile)
            .approve_pending(Some(&request.approval_id), "test-operator", approved_at)
            .unwrap()
            .unwrap();
        (approval, Utc::now())
    }

    #[test]
    fn review_is_read_only_and_request_is_exact_hash_bound() {
        let base = tempfile::TempDir::new().unwrap();
        let project = base.path().join("project");
        let missing_profile = base.path().join("missing-profile");
        fs::create_dir_all(project.join("docs")).unwrap();
        fs::write(project.join("docs/report.md"), "report\n").unwrap();
        let catalog = scan_project(
            &project,
            &CatalogOptions {
                project_key: Some("phase2c-test".to_string()),
                ..CatalogOptions::default()
            },
        )
        .unwrap();
        let input = mint_input(&catalog.entries[0].observation_id);
        let proposal = review_document_lifecycle(&missing_profile, &project, &input).unwrap();
        assert!(!missing_profile.exists());
        assert!(!proposal.authority_boundary.profile_state_write_authorized);
        assert!(!proposal.authority_boundary.file_mutation_authorized);

        fs::create_dir(&missing_profile).unwrap();
        #[cfg(unix)]
        fs::set_permissions(&missing_profile, fs::Permissions::from_mode(0o700)).unwrap();
        let error = request_document_lifecycle(
            &missing_profile,
            &project,
            &input,
            &"0".repeat(64),
            None,
            None,
            Utc::now(),
        )
        .unwrap_err();
        assert!(error.to_string().contains("no longer matches"));
        assert!(ApprovalLedger::new(&missing_profile)
            .load()
            .unwrap()
            .is_empty());
    }

    #[test]
    fn approved_mint_appends_once_consumes_last_and_history_has_no_body() {
        let (_base, project, profile, observation_id) = fixture();
        let original = fs::read(project.join("docs/report.md")).unwrap();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        let first =
            apply_document_lifecycle(&profile, &project, &approval.approval_id, apply_at).unwrap();
        assert!(!first.reused_existing);
        assert!(first.approval_consumed);
        assert_eq!(
            first.event.payload.event_kind,
            DocumentLifecycleEventKindV1::MintDocument
        );
        assert_eq!(fs::read(project.join("docs/report.md")).unwrap(), original);

        let second = apply_document_lifecycle(
            &profile,
            &project,
            &approval.approval_id,
            apply_at + Duration::seconds(1),
        )
        .unwrap();
        assert!(second.reused_existing);
        assert_eq!(second.event.event_sha256, first.event.event_sha256);
        let approvals = ApprovalLedger::new(&profile).load().unwrap();
        assert_eq!(approvals[0].status, ApprovalStatus::Superseded);

        let history = document_lifecycle_history(
            &profile,
            &project,
            " phase2c-test ",
            &first.event.payload.document_id,
            apply_at + Duration::seconds(2),
        )
        .unwrap();
        assert_eq!(history.project_key, "phase2c-test");
        assert_eq!(history.events, vec![first.event]);
        assert!(!history.document_body_included);
        assert!(!history.file_mutation_authorized);

        let event_path = fs::read_dir(profile.join(LIFECYCLE_EVENTS_DIRECTORY))
            .unwrap()
            .next()
            .unwrap()
            .unwrap()
            .path();
        #[cfg(unix)]
        assert_eq!(
            fs::metadata(event_path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    #[test]
    fn apply_rejects_changed_observation_without_consuming_approval() {
        let (_base, project, profile, observation_id) = fixture();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        fs::write(project.join("docs/report.md"), "# Replaced report\n").unwrap();
        let error = apply_document_lifecycle(&profile, &project, &approval.approval_id, apply_at)
            .unwrap_err();
        assert!(error.to_string().contains("current bounded catalog"));
        assert_eq!(
            ApprovalLedger::new(&profile).load().unwrap()[0].status,
            ApprovalStatus::Approved
        );
        let events = profile.join(LIFECYCLE_EVENTS_DIRECTORY);
        assert!(fs::read_dir(events).unwrap().next().is_none());
    }

    #[test]
    fn simultaneous_apply_reconciles_one_event_and_partial_blocks_authority() {
        let (_base, project, profile, observation_id) = fixture();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        let barrier = Arc::new(Barrier::new(3));
        let project = Arc::new(project);
        let profile = Arc::new(profile);
        let mut joins = Vec::new();
        for _ in 0..2 {
            let barrier = Arc::clone(&barrier);
            let project = Arc::clone(&project);
            let profile = Arc::clone(&profile);
            let approval_id = approval.approval_id.clone();
            joins.push(std::thread::spawn(move || {
                barrier.wait();
                apply_document_lifecycle(&profile, &project, &approval_id, apply_at).unwrap()
            }));
        }
        barrier.wait();
        let left = joins.remove(0).join().unwrap();
        let right = joins.remove(0).join().unwrap();
        assert_eq!(left.event.event_sha256, right.event.event_sha256);
        assert_ne!(left.reused_existing, right.reused_existing);
        assert_eq!(
            fs::read_dir(profile.join(LIFECYCLE_EVENTS_DIRECTORY))
                .unwrap()
                .count(),
            1
        );

        fs::write(
            profile
                .join(LIFECYCLE_EVENTS_DIRECTORY)
                .join("orphan.partial"),
            b"incomplete",
        )
        .unwrap();
        let error = apply_document_lifecycle(
            &profile,
            &project,
            &approval.approval_id,
            apply_at + Duration::seconds(1),
        )
        .unwrap_err();
        assert!(error.to_string().contains("partial_event_present"));
    }

    #[test]
    fn activity_revalidate_and_rebind_append_exact_successive_heads() {
        let (_base, project, profile, observation_id) = fixture();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        let mint = apply_document_lifecycle(&profile, &project, &approval.approval_id, apply_at)
            .unwrap()
            .event;

        let activity_input = DocumentLifecycleReviewInput {
            project_key: "phase2c-test".to_string(),
            observation_id: Some(mint.payload.observation.observation_id.clone()),
            document_id: Some(mint.payload.document_id.clone()),
            transition: LifecycleTransitionV1::ActivityTransition,
            activity_state: Some(ActivityState::ReviewDue),
            successor_document_id: None,
            active_owner_document_id: None,
            review_after: None,
            reason: "Schedule a reviewed follow-up.".to_string(),
            catalog_options: CatalogOptions::default(),
        };
        let activity = approve_and_apply(&profile, &project, &activity_input, Utc::now());
        assert_eq!(activity.payload.sequence, 2);
        assert_eq!(
            activity.payload.before.as_ref().unwrap().activity_state,
            ActivityState::Active
        );
        assert_eq!(
            activity.payload.after.activity_state,
            ActivityState::ReviewDue
        );

        fs::write(project.join("docs/report.md"), "# Revised current report\n").unwrap();
        let revalidate_input = DocumentLifecycleReviewInput {
            project_key: "phase2c-test".to_string(),
            observation_id: None,
            document_id: Some(mint.payload.document_id.clone()),
            transition: LifecycleTransitionV1::RevalidateObservation,
            activity_state: None,
            successor_document_id: None,
            active_owner_document_id: None,
            review_after: None,
            reason: "Revalidate the externally revised report.".to_string(),
            catalog_options: CatalogOptions::default(),
        };
        let revalidated = approve_and_apply(&profile, &project, &revalidate_input, Utc::now());
        assert_eq!(revalidated.payload.sequence, 3);
        assert_eq!(
            revalidated.payload.observation.relative_path,
            activity.payload.observation.relative_path
        );
        assert_ne!(
            revalidated.payload.observation.content_sha256,
            activity.payload.observation.content_sha256
        );
        assert_eq!(
            revalidated.payload.before,
            Some(revalidated.payload.after.clone())
        );

        fs::rename(
            project.join("docs/report.md"),
            project.join("docs/moved-report.md"),
        )
        .unwrap();
        let rebind_input = DocumentLifecycleReviewInput {
            project_key: "phase2c-test".to_string(),
            observation_id: None,
            document_id: Some(mint.payload.document_id.clone()),
            transition: LifecycleTransitionV1::RebindObservation,
            activity_state: None,
            successor_document_id: None,
            active_owner_document_id: None,
            review_after: None,
            reason: "Rebind the externally moved report.".to_string(),
            catalog_options: CatalogOptions::default(),
        };
        let rebound = approve_and_apply(&profile, &project, &rebind_input, Utc::now());
        assert_eq!(rebound.payload.sequence, 4);
        assert_eq!(
            rebound.payload.observation.relative_path,
            "docs/moved-report.md"
        );
        assert_eq!(
            rebound.payload.observation.content_sha256,
            revalidated.payload.observation.content_sha256
        );
    }

    #[test]
    fn request_deduplicates_exact_proposal_and_blocks_competing_head_proposal() {
        let (_base, project, profile, observation_id) = fixture();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        let mint = apply_document_lifecycle(&profile, &project, &approval.approval_id, apply_at)
            .unwrap()
            .event;
        let input = DocumentLifecycleReviewInput {
            project_key: "phase2c-test".to_string(),
            observation_id: None,
            document_id: Some(mint.payload.document_id.clone()),
            transition: LifecycleTransitionV1::ActivityTransition,
            activity_state: Some(ActivityState::ReviewDue),
            successor_document_id: None,
            active_owner_document_id: None,
            review_after: None,
            reason: "Schedule one reviewed follow-up.".to_string(),
            catalog_options: CatalogOptions::default(),
        };
        let proposal = review_document_lifecycle(&profile, &project, &input).unwrap();
        let first = request_document_lifecycle(
            &profile,
            &project,
            &input,
            &proposal.proposal_sha256,
            None,
            None,
            Utc::now(),
        )
        .unwrap();
        let duplicate = request_document_lifecycle(
            &profile,
            &project,
            &input,
            &proposal.proposal_sha256,
            None,
            None,
            Utc::now(),
        )
        .unwrap();
        assert_eq!(duplicate.approval_id, first.approval_id);
        assert!(duplicate.reused_existing);

        let mut competing = input;
        competing.activity_state = Some(ActivityState::Candidate);
        competing.reason = "Return the document to candidate review.".to_string();
        let competing_proposal = review_document_lifecycle(&profile, &project, &competing).unwrap();
        let error = request_document_lifecycle(
            &profile,
            &project,
            &competing,
            &competing_proposal.proposal_sha256,
            None,
            None,
            Utc::now(),
        )
        .unwrap_err();
        assert!(error.to_string().contains("unresolved lifecycle"));
        assert_eq!(
            ApprovalLedger::new(&profile)
                .load()
                .unwrap()
                .iter()
                .filter(|approval| approval.status == ApprovalStatus::Pending)
                .count(),
            1
        );
    }

    #[cfg(unix)]
    #[test]
    fn writer_rejects_symlinked_lifecycle_component_without_consuming_approval() {
        use std::os::unix::fs::symlink;

        let (base, project, profile, observation_id) = fixture();
        let (approval, apply_at) = approved_mint(&project, &profile, &observation_id);
        let outside = base.path().join("outside-ledger");
        fs::create_dir(&outside).unwrap();
        symlink(&outside, profile.join(LIFECYCLE_DIRECTORY)).unwrap();
        apply_document_lifecycle(&profile, &project, &approval.approval_id, apply_at).unwrap_err();
        assert!(fs::read_dir(&outside).unwrap().next().is_none());
        assert_eq!(
            ApprovalLedger::new(&profile).load().unwrap()[0].status,
            ApprovalStatus::Approved
        );
    }

    fn approve_and_apply(
        profile: &Path,
        project: &Path,
        input: &DocumentLifecycleReviewInput,
        now: DateTime<Utc>,
    ) -> DocumentLifecycleEventV1 {
        let proposal = review_document_lifecycle(profile, project, input).unwrap();
        let request = request_document_lifecycle(
            profile,
            project,
            input,
            &proposal.proposal_sha256,
            None,
            None,
            now,
        )
        .unwrap();
        ApprovalLedger::new(profile)
            .approve_pending(Some(&request.approval_id), "test-operator", now)
            .unwrap()
            .unwrap();
        apply_document_lifecycle(profile, project, &request.approval_id, now)
            .unwrap()
            .event
    }
}
