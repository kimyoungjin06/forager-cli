//! Live filesystem authority held while a bounded shift is prepared, armed, or consumed.

use std::fs::{self, File};
use std::io::Read;
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::de::DeserializeOwned;
use serde::Serialize;
use sha2::{Digest, Sha256};

use super::validate_offdesk_shift_dispatch_authority;
use super::wiki_context_file::wiki_context_root_identity_sha256;
use super::{
    offdesk_shift_arm_receipt_sha256, offdesk_shift_background_probe_sha256,
    offdesk_shift_manifest_sha256, offdesk_shift_routine_run_id,
    offdesk_shift_task_ticket_receipt_sha256, BackgroundPollOutcome, BackgroundProbe,
    BackgroundRunnerPhase, OffdeskShiftChildTaskNetworkBoundaryV1, OffdeskShiftEnterprisePolicyV1,
    OffdeskShiftEnterpriseProfileKeyV1, OffdeskShiftForagerNetworkEffectV1,
    OffdeskShiftNotificationRouteV1, OffdeskShiftOperatorModeV1, OffdeskShiftRecordV1,
    OffdeskShiftRoutineBindingV1, OffdeskShiftRoutineEvidenceV1, OffdeskShiftRoutineKindV1,
    OffdeskShiftRoutineOutcomeReceiptV1, OffdeskShiftRoutineOutcomeV1,
    OffdeskShiftRoutineRecoveryAcknowledgementV1, OffdeskShiftRoutineRunReservationV1,
    OffdeskShiftRoutineUsageV1, OffdeskShiftStateV1, OffdeskShiftStatusV1, OffdeskShiftStore,
    OffdeskShiftTaskLaunchReservationV1, OffdeskShiftTaskLaunchTicketReceiptV1,
    OffdeskShiftTaskOutcomeReceiptV1, OffdeskShiftTaskOutcomeV1, OffdeskTask, OffdeskTaskStatus,
    OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA, OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA,
    OFFDESK_SHIFT_STATE_SCHEMA, OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA,
    OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA, OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA,
};
use crate::process::FilesystemObjectSnapshot;
use crate::session::project_registry::{load_registry, resolve_unique_project_for_path};

const MAX_SHIFT_SOURCE_BYTES: usize = 16 * 1024 * 1024;
pub(crate) const OFFDESK_SHIFT_ROUTINE_EXECUTION_POLICY_SCHEMA: &str =
    "offdesk_shift_routine_execution_policy.v1";
const OFFDESK_SHIFT_ROUTINE_EXECUTION_REQUIRED_DENIALS: &[&str] = &[
    "unbound_routine",
    "command_or_workdir_change",
    "provider_retargeting",
    "destructive_action",
    "unlisted_external_side_effect",
    "wiki_promotion",
    "accepted_truth",
    "plan_revision_without_review",
];

pub(crate) struct ProfileSourceBoundary {
    root_path: PathBuf,
    root: File,
    root_identity: (u64, u64),
    observations: Vec<(PathBuf, FilesystemObjectSnapshot)>,
}

impl ProfileSourceBoundary {
    pub(crate) fn open(root_path: PathBuf) -> Result<Self> {
        let metadata = fs::symlink_metadata(&root_path).context("inspect shift profile root")?;
        ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "shift profile root must be one safe directory"
        );
        let root =
            crate::process::open_bounded_root(&root_path).context("open shift profile root")?;
        let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("shift profile root lacks stable identity")?;
        ensure!(
            crate::process::filesystem_file_identity(&metadata) == Some(root_identity),
            "shift profile root path and descriptor identities differ"
        );
        Ok(Self {
            root_path,
            root,
            root_identity,
            observations: Vec::new(),
        })
    }

    pub(crate) fn read_json<T: DeserializeOwned>(&mut self, path: &Path) -> Result<T> {
        let bytes = self.read_bytes(path)?;
        serde_json::from_slice(&bytes)
            .with_context(|| format!("parse shift source {}", path.display()))
    }

    pub(crate) fn read_json_ephemeral<T: DeserializeOwned>(&mut self, path: &Path) -> Result<T> {
        let bytes = self.read_bytes_with_tracking(path, false)?;
        serde_json::from_slice(&bytes)
            .with_context(|| format!("parse shift source {}", path.display()))
    }

    pub(crate) fn read_task_queue(
        &mut self,
        path: &Path,
        mutable: bool,
    ) -> Result<Vec<OffdeskTask>> {
        let bytes = self.read_bytes_with_tracking(path, !mutable)?;
        crate::migrations::v005_task_review_links::decode(std::str::from_utf8(&bytes)?)
    }

    pub(crate) fn read_bytes(&mut self, path: &Path) -> Result<Vec<u8>> {
        self.read_bytes_with_tracking(path, true)
    }

    fn read_bytes_with_tracking(&mut self, path: &Path, track: bool) -> Result<Vec<u8>> {
        let relative = relative_to_root(&self.root_path, path)?;
        let mut file = crate::process::open_bounded_regular_file(&self.root, &relative)
            .with_context(|| format!("open bounded shift source {}", path.display()))?;
        ensure_single_link(&file, path)?;
        let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take((MAX_SHIFT_SOURCE_BYTES + 1) as u64)
            .read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() <= MAX_SHIFT_SOURCE_BYTES,
            "shift source exceeds its byte budget"
        );
        let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        ensure!(before == after, "shift source changed while read");
        self.ensure_active_file(&relative, &after)?;
        self.ensure_active_root()?;
        if track {
            self.observations.push((relative, after));
        }
        Ok(bytes)
    }

    pub(crate) fn ensure_current(&self) -> Result<()> {
        self.ensure_active_root()?;
        for (relative, snapshot) in &self.observations {
            self.ensure_active_file(relative, snapshot)?;
        }
        self.ensure_active_root()
    }

    pub(crate) fn root_identity(&self) -> (u64, u64) {
        self.root_identity
    }

    pub(crate) fn root_path(&self) -> &Path {
        &self.root_path
    }

    fn ensure_active_file(
        &self,
        relative: &Path,
        expected: &FilesystemObjectSnapshot,
    ) -> Result<()> {
        let file = crate::process::open_bounded_regular_file(&self.root, relative)
            .context("reopen active shift source")?;
        ensure_single_link(&file, relative)?;
        ensure!(
            FilesystemObjectSnapshot::from_metadata(&file.metadata()?) == *expected,
            "shift source was replaced after observation"
        );
        Ok(())
    }

    fn ensure_active_root(&self) -> Result<()> {
        let reopened = crate::process::open_bounded_root(&self.root_path)
            .context("reopen active shift profile root")?;
        ensure!(
            crate::process::filesystem_file_identity(&reopened.metadata()?)
                == Some(self.root_identity),
            "shift profile root changed during source validation"
        );
        Ok(())
    }
}

pub(crate) struct ProjectRootBoundary {
    path: PathBuf,
    root: File,
    identity: (u64, u64),
    pub(crate) identity_sha256: String,
}

impl ProjectRootBoundary {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        let path = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };
        let metadata = fs::symlink_metadata(&path)
            .with_context(|| format!("inspect shift project root {}", path.display()))?;
        ensure!(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "shift project root must be one safe directory"
        );
        let root = crate::process::open_bounded_root(&path)
            .with_context(|| format!("open shift project root {}", path.display()))?;
        let identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("shift project root lacks stable identity")?;
        ensure!(
            crate::process::filesystem_file_identity(&metadata) == Some(identity),
            "shift project root path and descriptor identities differ"
        );
        let boundary = Self {
            path,
            root,
            identity,
            identity_sha256: wiki_context_root_identity_sha256(identity),
        };
        boundary.ensure_current()?;
        Ok(boundary)
    }

    pub(crate) fn descriptor(&self) -> Result<File> {
        Ok(self.root.try_clone()?)
    }

    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn ensure_current(&self) -> Result<()> {
        let reopened = crate::process::open_bounded_root(&self.path)
            .with_context(|| format!("reopen shift project root {}", self.path.display()))?;
        ensure!(
            crate::process::filesystem_file_identity(&reopened.metadata()?) == Some(self.identity)
                && crate::process::filesystem_file_identity(&self.root.metadata()?)
                    == Some(self.identity),
            "shift project root changed after observation"
        );
        Ok(())
    }
}

pub(crate) struct OffdeskShiftLiveSources {
    profile: ProfileSourceBoundary,
    project_roots: Vec<ProjectRootBoundary>,
}

impl OffdeskShiftLiveSources {
    pub(crate) fn new(
        profile: ProfileSourceBoundary,
        project_roots: Vec<ProjectRootBoundary>,
    ) -> Self {
        Self {
            profile,
            project_roots,
        }
    }

    pub(crate) fn ensure_current(&self) -> Result<()> {
        self.profile.ensure_current()?;
        for root in &self.project_roots {
            root.ensure_current()?;
        }
        super::workflow::invocation_inputs::ReviewInputScope::ensure_current()?;
        self.profile.ensure_current()
    }

    pub(crate) fn profile_root_identity(&self) -> (u64, u64) {
        self.profile.root_identity()
    }

    pub(crate) fn project_root_for_path(&self, path: &Path) -> Option<&ProjectRootBoundary> {
        let normalized = normalize_absolute(path).ok()?;
        self.project_roots
            .iter()
            .find(|root| normalize_absolute(root.path()).ok().as_ref() == Some(&normalized))
    }

    pub(crate) fn read_current_tasks(&mut self) -> Result<Vec<OffdeskTask>> {
        let path = self.profile.root_path().join("offdesk_tasks.json");
        self.profile.read_task_queue(&path, true)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum OffdeskShiftLaunchDecision {
    Proceed,
    Held(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum OffdeskShiftRoutineStartDecision {
    Reserved(OffdeskShiftRoutineRunReservationV1),
    AlreadyCompleted(Box<OffdeskShiftRoutineOutcomeReceiptV1>),
    RecoveryRequired(OffdeskShiftRoutineRunReservationV1),
    NotDue(OffdeskShiftRoutineBindingV1),
    Held(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum OffdeskShiftRoutineAdapterHandlerV1 {
    DispatchTick,
    WikiCandidatePrereview,
    MorningReviewDelivery,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum OffdeskShiftRoutineAllowedEffectV1 {
    BoundedShiftTick,
    CandidateOnlyWikiPrereview,
    LocalReviewArtifactsAndNotification,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct OffdeskShiftRoutineResourceAllowanceV1 {
    pub process_starts: u64,
    pub external_calls: u64,
    pub disk_bytes_written: u64,
    pub llm_tokens: u64,
    pub cost_micro_usd: u64,
    pub cost_rate_micro_usd_per_million_tokens: u64,
}

impl OffdeskShiftRoutineResourceAllowanceV1 {
    pub(crate) fn unavailable() -> Self {
        Self {
            process_starts: 0,
            external_calls: 0,
            disk_bytes_written: 0,
            llm_tokens: 0,
            cost_micro_usd: 0,
            cost_rate_micro_usd_per_million_tokens: 0,
        }
    }

    fn hold_reason(
        &self,
        kind: OffdeskShiftRoutineKindV1,
        notification_route: OffdeskShiftNotificationRouteV1,
    ) -> Option<&'static str> {
        let requirements = match kind {
            OffdeskShiftRoutineKindV1::DispatchTick => (1, 0, 0, 0),
            OffdeskShiftRoutineKindV1::WikiCandidateDistillation => (2, 1, 1, 512),
            OffdeskShiftRoutineKindV1::MorningReviewBrief => (
                1,
                u64::from(
                    notification_route == OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
                ),
                1,
                0,
            ),
        };
        if self.process_starts < requirements.0 {
            Some("shift_routine_process_budget_exhausted")
        } else if self.external_calls < requirements.1 {
            Some("shift_routine_external_call_budget_exhausted")
        } else if self.disk_bytes_written < requirements.2 {
            Some("shift_routine_disk_budget_exhausted")
        } else if self.llm_tokens < requirements.3 {
            Some("shift_routine_llm_token_budget_exhausted")
        } else {
            None
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct OffdeskShiftRoutineExecutionPolicyV1 {
    pub schema: String,
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub handler: OffdeskShiftRoutineAdapterHandlerV1,
    pub allowed_effect: OffdeskShiftRoutineAllowedEffectV1,
    pub disarm_after_terminal: bool,
    pub resource_allowance: OffdeskShiftRoutineResourceAllowanceV1,
    pub enterprise_policy_key: OffdeskShiftEnterpriseProfileKeyV1,
    pub operator_mode: OffdeskShiftOperatorModeV1,
    pub notification_route: OffdeskShiftNotificationRouteV1,
    pub allowed_forager_network_effects: Vec<OffdeskShiftForagerNetworkEffectV1>,
    pub child_task_network_boundary: OffdeskShiftChildTaskNetworkBoundaryV1,
    pub forager_controls_child_task_network: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub wiki_profile: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_root_identity_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub project_root_path: Option<String>,
    pub does_not_authorize: Vec<String>,
}

impl OffdeskShiftRoutineExecutionPolicyV1 {
    pub(crate) fn from_binding(
        binding: &OffdeskShiftRoutineBindingV1,
        resource_allowance: OffdeskShiftRoutineResourceAllowanceV1,
        enterprise_policy: &OffdeskShiftEnterprisePolicyV1,
    ) -> Result<Self> {
        let (handler, allowed_effect, disarm_after_terminal) =
            Self::from_kind(binding.routine_kind);
        let policy = Self {
            schema: OFFDESK_SHIFT_ROUTINE_EXECUTION_POLICY_SCHEMA.to_string(),
            routine_id: binding.routine_id.clone(),
            routine_kind: binding.routine_kind,
            handler,
            allowed_effect,
            disarm_after_terminal,
            resource_allowance,
            enterprise_policy_key: enterprise_policy.profile_key,
            operator_mode: enterprise_policy.operator_mode,
            notification_route: enterprise_policy.notification_route,
            allowed_forager_network_effects: enterprise_policy
                .allowed_forager_network_effects
                .clone(),
            child_task_network_boundary: enterprise_policy.child_task_network_boundary,
            forager_controls_child_task_network: enterprise_policy
                .forager_controls_child_task_network,
            project_key: binding.project_key.clone(),
            wiki_profile: binding.wiki_profile.clone(),
            project_root_identity_sha256: binding.project_root_identity_sha256.clone(),
            project_root_path: binding.project_root_path.clone(),
            does_not_authorize: OFFDESK_SHIFT_ROUTINE_EXECUTION_REQUIRED_DENIALS
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
        };
        policy.validate_against(binding, enterprise_policy)?;
        Ok(policy)
    }

    pub(crate) fn validate_against(
        &self,
        binding: &OffdeskShiftRoutineBindingV1,
        enterprise_policy: &OffdeskShiftEnterprisePolicyV1,
    ) -> Result<()> {
        enterprise_policy.validate()?;
        ensure!(
            self.schema == OFFDESK_SHIFT_ROUTINE_EXECUTION_POLICY_SCHEMA
                && self.routine_id == binding.routine_id
                && self.routine_kind == binding.routine_kind
                && self.project_key == binding.project_key
                && self.wiki_profile == binding.wiki_profile
                && self.project_root_identity_sha256 == binding.project_root_identity_sha256
                && self.project_root_path == binding.project_root_path,
            "shift routine execution policy does not match its manifest binding"
        );
        let expected = Self::from_kind(binding.routine_kind);
        ensure!(
            self.handler == expected.0
                && self.allowed_effect == expected.1
                && self.disarm_after_terminal == expected.2,
            "shift routine execution policy has a non-canonical handler"
        );
        ensure!(
            self.enterprise_policy_key == enterprise_policy.profile_key
                && self.operator_mode == enterprise_policy.operator_mode
                && self.notification_route == enterprise_policy.notification_route
                && self.allowed_forager_network_effects
                    == enterprise_policy.allowed_forager_network_effects
                && self.child_task_network_boundary
                    == enterprise_policy.child_task_network_boundary
                && self.forager_controls_child_task_network
                    == enterprise_policy.forager_controls_child_task_network,
            "shift routine execution policy does not match its enterprise policy"
        );
        ensure!(
            self.does_not_authorize.len() == OFFDESK_SHIFT_ROUTINE_EXECUTION_REQUIRED_DENIALS.len()
                && OFFDESK_SHIFT_ROUTINE_EXECUTION_REQUIRED_DENIALS
                    .iter()
                    .all(|required| self
                        .does_not_authorize
                        .iter()
                        .any(|value| value == required)),
            "shift routine execution policy authority denials are incomplete"
        );
        Ok(())
    }

    fn from_kind(
        kind: OffdeskShiftRoutineKindV1,
    ) -> (
        OffdeskShiftRoutineAdapterHandlerV1,
        OffdeskShiftRoutineAllowedEffectV1,
        bool,
    ) {
        match kind {
            OffdeskShiftRoutineKindV1::DispatchTick => (
                OffdeskShiftRoutineAdapterHandlerV1::DispatchTick,
                OffdeskShiftRoutineAllowedEffectV1::BoundedShiftTick,
                false,
            ),
            OffdeskShiftRoutineKindV1::WikiCandidateDistillation => (
                OffdeskShiftRoutineAdapterHandlerV1::WikiCandidatePrereview,
                OffdeskShiftRoutineAllowedEffectV1::CandidateOnlyWikiPrereview,
                false,
            ),
            OffdeskShiftRoutineKindV1::MorningReviewBrief => (
                OffdeskShiftRoutineAdapterHandlerV1::MorningReviewDelivery,
                OffdeskShiftRoutineAllowedEffectV1::LocalReviewArtifactsAndNotification,
                true,
            ),
        }
    }
}

pub(crate) struct OffdeskShiftRoutineCompletionInput<'a> {
    pub run_id: &'a str,
    pub outcome: OffdeskShiftRoutineOutcomeV1,
    pub summary: &'a str,
    pub evidence: &'a [OffdeskShiftRoutineEvidenceV1],
    pub usage: Option<OffdeskShiftRoutineUsageV1>,
    pub recovery_acknowledgement: Option<OffdeskShiftRoutineRecoveryAcknowledgementV1>,
}

pub(crate) fn complete_reserved_routine(
    store: &OffdeskShiftStore,
    record: &OffdeskShiftRecordV1,
    input: OffdeskShiftRoutineCompletionInput<'_>,
    now: DateTime<Utc>,
) -> Result<(
    OffdeskShiftRoutineOutcomeReceiptV1,
    bool,
    OffdeskShiftRecordV1,
)> {
    let current = store
        .record(&record.manifest.shift_id)?
        .context("shift routine completion lost its exact shift")?;
    ensure!(
        current.manifest == record.manifest && current.arm_receipt == record.arm_receipt,
        "shift routine authority changed before completion"
    );
    let arm_receipt = current
        .arm_receipt
        .as_ref()
        .context("shift routine completion has no arm receipt")?;
    let state = current
        .state
        .as_ref()
        .context("shift routine completion has no current state")?;
    state.validate_against(&current.manifest, arm_receipt)?;

    let safe_summary = super::operator_safe_text(input.summary.trim());
    if let Some(existing) = current
        .routine_outcome_receipts
        .iter()
        .find(|receipt| receipt.run_id == input.run_id)
    {
        ensure!(
            existing.outcome == input.outcome
                && existing.summary == safe_summary
                && existing.evidence == input.evidence
                && existing.usage == input.usage
                && existing.recovery_acknowledgement == input.recovery_acknowledgement,
            "shift routine outcome already exists with different content"
        );
        return Ok((existing.clone(), false, current));
    }
    let reservation = current
        .routine_run_reservations
        .iter()
        .find(|reservation| reservation.run_id == input.run_id)
        .cloned()
        .context("shift routine completion has no exact reservation")?;
    let receipt = OffdeskShiftRoutineOutcomeReceiptV1 {
        schema: OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA.to_string(),
        receipt_id: format!("shift_routine_outcome_{}", reservation.run_id),
        run_id: reservation.run_id.clone(),
        shift_id: reservation.shift_id.clone(),
        manifest_sha256: reservation.manifest_sha256.clone(),
        arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
        routine_id: reservation.routine_id.clone(),
        routine_kind: reservation.routine_kind,
        scheduled_at: reservation.scheduled_at,
        reserved_at: reservation.reserved_at,
        completed_at: now.max(reservation.reserved_at),
        outcome: input.outcome,
        summary: safe_summary,
        evidence: input.evidence.to_vec(),
        usage: input.usage,
        recovery_acknowledgement: input.recovery_acknowledgement,
    };
    let attention_state = (input.outcome.requires_attention()
        && state.status == OffdeskShiftStatusV1::Running)
        .then(|| OffdeskShiftStateV1 {
            schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
            shift_id: state.shift_id.clone(),
            manifest_sha256: state.manifest_sha256.clone(),
            arm_receipt_sha256: state.arm_receipt_sha256.clone(),
            sequence: state.sequence + 1,
            status: OffdeskShiftStatusV1::AttentionRequired,
            updated_at: now.max(state.updated_at),
            reason: format!(
                "typed routine {} recorded {:?}",
                reservation.routine_id, input.outcome
            )
            .to_lowercase(),
            acceptance_receipt_id: None,
            acceptance_receipt_sha256: None,
        });
    let transition_id = format!("shift_routine_attention_{}", reservation.run_id);
    let created = store.complete_routine_run(receipt.clone(), attention_state, &transition_id)?;
    let updated = store
        .record(&record.manifest.shift_id)?
        .context("shift routine completion was not durably readable")?;
    ensure!(
        updated
            .routine_outcome_receipts
            .iter()
            .any(|stored| stored == &receipt),
        "shift routine completion receipt changed after write"
    );
    Ok((receipt, created, updated))
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum OffdeskShiftBackgroundObservationScope {
    Complete,
    ExactTicket(String),
    None,
}

impl OffdeskShiftBackgroundObservationScope {
    fn authorizes_absence(&self, ticket_id: &str) -> bool {
        match self {
            Self::Complete => true,
            Self::ExactTicket(observed) => observed == ticket_id,
            Self::None => false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftTickView {
    pub shift_id: String,
    pub status: OffdeskShiftStatusV1,
    pub window_starts_at: DateTime<Utc>,
    pub window_stops_at: DateTime<Utc>,
    pub bound_tasks: usize,
    pub held: usize,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hold_reasons: Vec<String>,
}

pub(crate) struct OffdeskShiftTickAuthority {
    store: OffdeskShiftStore,
    record: OffdeskShiftRecordV1,
    live: OffdeskShiftLiveSources,
}

impl OffdeskShiftTickAuthority {
    pub(crate) fn new(
        store: OffdeskShiftStore,
        record: OffdeskShiftRecordV1,
        live: OffdeskShiftLiveSources,
    ) -> Result<Self> {
        let authority = Self {
            store,
            record,
            live,
        };
        authority.validate_record()?;
        authority
            .store
            .ensure_profile_root_identity(authority.live.profile_root_identity())?;
        authority.live.ensure_current()?;
        authority.validate_wiki_registry_targets()?;
        Ok(authority)
    }

    pub(crate) fn begin_tick(&mut self, now: DateTime<Utc>) -> Result<()> {
        self.refresh_current_record()?;
        let receipt = self.arm_receipt()?;
        let state = self.state()?;
        if now > receipt.expires_at
            && matches!(
                state.status,
                OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running
            )
        {
            let next = OffdeskShiftStateV1 {
                schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
                shift_id: state.shift_id.clone(),
                manifest_sha256: state.manifest_sha256.clone(),
                arm_receipt_sha256: state.arm_receipt_sha256.clone(),
                sequence: state.sequence + 1,
                status: OffdeskShiftStatusV1::ReviewReady,
                updated_at: now.max(state.updated_at),
                reason: "bounded shift window expired before new dispatch".to_string(),
                acceptance_receipt_id: None,
                acceptance_receipt_sha256: None,
            };
            let transition_id = format!("shift_window_closed_{}", state.shift_id);
            self.store.transition(next, &transition_id)?;
            self.refresh_current_record()?;
        }
        self.live.ensure_current()?;
        self.validate_wiki_registry_targets()
    }

    pub(crate) fn current_hold_reason(&self, now: DateTime<Utc>) -> Result<Option<String>> {
        self.launch_hold_reason(now)
    }

    pub(crate) fn record(&self) -> &OffdeskShiftRecordV1 {
        &self.record
    }

    pub(crate) fn routine_resource_allowance(
        &self,
    ) -> Result<Option<OffdeskShiftRoutineResourceAllowanceV1>> {
        let Some(budget) = self.record.manifest.routine_resource_budget.as_ref() else {
            return Ok(None);
        };
        let mut used = [0_u64; 5];
        for receipt in &self.record.routine_outcome_receipts {
            let Some(usage) = &receipt.usage else {
                continue;
            };
            for (total, value) in used.iter_mut().zip([
                usage.process_starts,
                usage.external_calls,
                usage.disk_bytes_written,
                usage.llm_tokens,
                usage.cost_micro_usd,
            ]) {
                *total = total
                    .checked_add(value)
                    .context("shift routine usage total overflow")?;
            }
        }
        Ok(Some(OffdeskShiftRoutineResourceAllowanceV1 {
            process_starts: budget
                .max_process_starts
                .checked_sub(used[0])
                .context("shift routine process usage exceeds its budget")?,
            external_calls: budget
                .max_external_calls
                .checked_sub(used[1])
                .context("shift routine external-call usage exceeds its budget")?,
            disk_bytes_written: budget
                .max_disk_bytes_written
                .checked_sub(used[2])
                .context("shift routine disk usage exceeds its budget")?,
            llm_tokens: budget
                .max_llm_tokens
                .checked_sub(used[3])
                .context("shift routine LLM usage exceeds its budget")?,
            cost_micro_usd: budget
                .max_cost_micro_usd
                .checked_sub(used[4])
                .context("shift routine cost usage exceeds its budget")?,
            cost_rate_micro_usd_per_million_tokens: budget
                .llm_cost_rate_micro_usd_per_million_tokens,
        }))
    }

    pub(crate) fn start_routine(
        &mut self,
        routine_id: &str,
        now: DateTime<Utc>,
    ) -> Result<OffdeskShiftRoutineStartDecision> {
        self.begin_tick(now)?;
        let binding = self
            .record
            .manifest
            .routine_bindings
            .iter()
            .find(|binding| binding.routine_id == routine_id)
            .cloned()
            .context("routine is not bound to the selected shift")?;
        let scheduled_at = binding.schedule.latest_due_occurrence(now);
        if let Some(scheduled_at) = scheduled_at {
            let run_id = offdesk_shift_routine_run_id(
                &self.record.manifest.shift_id,
                &binding.routine_id,
                scheduled_at,
            );
            if let Some(outcome) = self
                .record
                .routine_outcome_receipts
                .iter()
                .find(|outcome| outcome.run_id == run_id)
            {
                return Ok(OffdeskShiftRoutineStartDecision::AlreadyCompleted(
                    Box::new(outcome.clone()),
                ));
            }
            if let Some(reservation) = self
                .record
                .routine_run_reservations
                .iter()
                .find(|reservation| reservation.run_id == run_id)
            {
                return Ok(OffdeskShiftRoutineStartDecision::RecoveryRequired(
                    reservation.clone(),
                ));
            }
        }
        if let Some(unresolved) = self
            .record
            .routine_run_reservations
            .iter()
            .find(|reservation| {
                !self
                    .record
                    .routine_outcome_receipts
                    .iter()
                    .any(|outcome| outcome.run_id == reservation.run_id)
            })
        {
            return Ok(OffdeskShiftRoutineStartDecision::RecoveryRequired(
                unresolved.clone(),
            ));
        }
        if let Some(reason) = self.launch_hold_reason(now)? {
            return Ok(OffdeskShiftRoutineStartDecision::Held(reason));
        }
        let weekly = if binding.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
        {
            let checked = self.weekly_usage_gate(Utc::now())?;
            if self.record.manifest.weekly_usage.is_some() && checked.is_none() {
                return Ok(OffdeskShiftRoutineStartDecision::Held(
                    self.weekly_hold_reason()?.to_string(),
                ));
            }
            checked
        } else {
            None
        };
        let Some(resource_allowance) = self.routine_resource_allowance()? else {
            return Ok(OffdeskShiftRoutineStartDecision::Held(
                "shift_routine_resource_budget_unconfigured".to_string(),
            ));
        };
        let enterprise_policy = self.record.manifest.enterprise_policy()?;
        if let Some(reason) = resource_allowance
            .hold_reason(binding.routine_kind, enterprise_policy.notification_route)
        {
            return Ok(OffdeskShiftRoutineStartDecision::Held(reason.to_string()));
        }
        validate_offdesk_shift_dispatch_authority(
            &self.record.manifest,
            self.arm_receipt()?,
            self.state()?,
            now,
        )?;
        let Some(scheduled_at) = scheduled_at else {
            return Ok(OffdeskShiftRoutineStartDecision::NotDue(binding));
        };
        let run_id = offdesk_shift_routine_run_id(
            &self.record.manifest.shift_id,
            &binding.routine_id,
            scheduled_at,
        );
        if self.record.routine_run_reservations.len()
            >= usize::from(self.record.manifest.budget.max_routine_runs)
        {
            self.record_attention(now, "shift routine run budget is exhausted")?;
            return Ok(OffdeskShiftRoutineStartDecision::Held(
                "shift_routine_budget_exhausted".to_string(),
            ));
        }

        let state = self.state()?.clone();
        let running_state = if state.status == OffdeskShiftStatusV1::Armed {
            Some(OffdeskShiftStateV1 {
                schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
                shift_id: state.shift_id.clone(),
                manifest_sha256: state.manifest_sha256.clone(),
                arm_receipt_sha256: state.arm_receipt_sha256.clone(),
                sequence: state.sequence + 1,
                status: OffdeskShiftStatusV1::Running,
                updated_at: now.max(state.updated_at),
                reason: "first due typed routine reserved by the bounded shift".to_string(),
                acceptance_receipt_id: None,
                acceptance_receipt_sha256: None,
            })
        } else {
            None
        };
        let reservation = OffdeskShiftRoutineRunReservationV1 {
            schema: OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA.to_string(),
            run_id,
            shift_id: self.record.manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(&self.record.manifest)?,
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(
                self.arm_receipt()?,
                &self.record.manifest,
            )?,
            routine_id: binding.routine_id,
            routine_kind: binding.routine_kind,
            scheduled_at,
            reserved_at: now,
        };
        let transition_id = format!("shift_routine_running_{}", reservation.run_id);
        let created =
            self.store
                .reserve_routine_run(reservation.clone(), running_state, &transition_id)?;
        if let Some(check) = &weekly {
            check.ensure_current()?;
        }
        self.refresh_current_record()?;
        if !created {
            return Ok(OffdeskShiftRoutineStartDecision::RecoveryRequired(
                reservation,
            ));
        }
        Ok(OffdeskShiftRoutineStartDecision::Reserved(reservation))
    }

    fn weekly_usage_gate(
        &mut self,
        now: DateTime<Utc>,
    ) -> Result<Option<super::weekly_usage::WeeklyUsageCheck>> {
        let Some(binding) = self.record.manifest.weekly_usage.as_ref() else {
            return Ok(None);
        };
        let root = crate::session::profile_dir_path(&self.record.manifest.profile)?;
        let checked = super::weekly_usage::check(&root, binding, now, true);
        match checked {
            Ok(check) if check.verdict["allowed"] == true => Ok(Some(check)),
            Err(error)
                if error
                    .downcast_ref::<std::io::Error>()
                    .is_some_and(|e| e.kind() == std::io::ErrorKind::WouldBlock) =>
            {
                Ok(None)
            }
            _ => {
                self.record_attention(now, "shift_weekly_usage_hold_requires_review")?;
                Ok(None)
            }
        }
    }

    fn weekly_hold_reason(&self) -> Result<&'static str> {
        Ok(
            if self.state()?.status == OffdeskShiftStatusV1::AttentionRequired {
                "shift_weekly_usage_hold_requires_review"
            } else {
                "shift_weekly_usage_guard_busy_retry_after_current_check"
            },
        )
    }

    pub(crate) fn preflight_task(
        &mut self,
        task: &OffdeskTask,
        tasks: &[OffdeskTask],
        now: DateTime<Utc>,
    ) -> Result<OffdeskShiftLaunchDecision> {
        self.refresh_current_record()?;
        super::workflow::offdesk_preparation::validate_review_binding(
            &self.record.manifest,
            tasks,
        )?;
        super::workflow::preparation_capabilities::validate_launch(&self.record.manifest, tasks)?;
        if let Some(reason) = self.launch_hold_reason(now)? {
            return Ok(OffdeskShiftLaunchDecision::Held(reason));
        }
        let weekly = self.weekly_usage_gate(Utc::now())?;
        if self.record.manifest.weekly_usage.is_some() && weekly.is_none() {
            return Ok(OffdeskShiftLaunchDecision::Held(
                self.weekly_hold_reason()?.to_string(),
            ));
        }
        validate_offdesk_shift_dispatch_authority(
            &self.record.manifest,
            self.arm_receipt()?,
            self.state()?,
            now,
        )?;
        if let Some(attestation) = &self.record.manifest.host_policy_attestation {
            super::reverify_offdesk_shift_host_policy_attestation(attestation, now)?;
        }
        let Some(binding) = self
            .record
            .manifest
            .task_bindings
            .iter()
            .find(|binding| binding.task_id == task.task_id)
        else {
            return Ok(OffdeskShiftLaunchDecision::Held(
                "task_not_bound_to_active_shift".to_string(),
            ));
        };
        let root = self
            .live
            .project_root_for_path(Path::new(&task.workdir))
            .context("bound shift task root was not observed")?;
        binding.validate_against_task(task, &root.identity_sha256, now)?;
        if let Some(reason) = self.serial_order_hold_reason(task, tasks)? {
            return Ok(OffdeskShiftLaunchDecision::Held(reason));
        }
        if let Some(policy) = &self.record.manifest.read_policy {
            super::read_policy::validate_workdir(policy, Path::new(&task.workdir))?;
            super::read_policy::verify(policy)?;
        }

        // The caller holds the profile tick lock across the queue snapshot,
        // reservation, launch and durable task update. Recheck on commit too.
        if let Some(reason) = super::workdir_conflicts::launch_hold_reason(
            self.live.profile.root_path(),
            tasks,
            &task.task_id,
        )? {
            return Ok(OffdeskShiftLaunchDecision::Held(reason.to_string()));
        }

        let attempt_number = task
            .attempt_count
            .checked_add(1)
            .context("shift task attempt number overflow")?;
        if self
            .record
            .task_launch_reservations
            .iter()
            .any(|reservation| {
                reservation.task_id == task.task_id && reservation.attempt_number == attempt_number
            })
        {
            return Ok(OffdeskShiftLaunchDecision::Held(
                "shift_launch_reservation_requires_recovery".to_string(),
            ));
        }

        let bound = self
            .record
            .manifest
            .task_bindings
            .iter()
            .map(|binding| binding.task_id.as_str())
            .collect::<std::collections::HashSet<_>>();
        let active = tasks
            .iter()
            .filter(|candidate| {
                bound.contains(candidate.task_id.as_str())
                    && matches!(
                        candidate.status,
                        OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running
                    )
            })
            .count();
        let mut started = tasks
            .iter()
            .filter(|candidate| {
                bound.contains(candidate.task_id.as_str()) && candidate.attempt_count > 0
            })
            .map(|task| task.task_id.as_str())
            .collect::<std::collections::HashSet<_>>();
        for reservation in &self.record.task_launch_reservations {
            started.insert(reservation.task_id.as_str());
        }
        let budget = &self.record.manifest.budget;
        let reserved_attempts = self
            .record
            .task_launch_reservations
            .iter()
            .filter(|reservation| reservation.task_id == task.task_id)
            .count();
        if task.attempt_count >= u32::from(budget.max_attempts_per_task)
            || reserved_attempts >= usize::from(budget.max_attempts_per_task)
        {
            return Ok(OffdeskShiftLaunchDecision::Held(
                "task_attempt_budget_exhausted".to_string(),
            ));
        }
        if task.attempt_count == 0
            && !started.contains(task.task_id.as_str())
            && started.len() >= usize::from(budget.max_task_launches)
        {
            return Ok(OffdeskShiftLaunchDecision::Held(
                "shift_task_launch_budget_exhausted".to_string(),
            ));
        }
        if active >= usize::from(budget.max_concurrent_tasks) {
            return Ok(OffdeskShiftLaunchDecision::Held(
                "shift_concurrency_budget_exhausted".to_string(),
            ));
        }
        self.live.ensure_current()?;
        self.validate_wiki_registry_targets()?;
        Ok(OffdeskShiftLaunchDecision::Proceed)
    }

    fn serial_order_hold_reason(
        &self,
        task: &OffdeskTask,
        tasks: &[OffdeskTask],
    ) -> Result<Option<String>> {
        let manifest = &self.record.manifest;
        let Some(serial) = &manifest.serial_selection else {
            return Ok(None);
        };
        let registry = crate::session::project_registry::load_registry_checked(
            &crate::session::project_registry::registry_path(),
        )?;
        if registry.content_sha256 != serial.registry_sha256 {
            return Ok(Some("serial_registry_changed".into()));
        }
        for binding in &manifest.task_bindings {
            let current = tasks
                .iter()
                .find(|current| current.task_id == binding.task_id)
                .context("serial selected task disappeared")?;
            let root = self
                .live
                .project_root_for_path(Path::new(&current.workdir))
                .context("serial selected root disappeared")?;
            binding.validate_identity_against_task(current, &root.identity_sha256)?;
            super::workflow::ondesk_task_binding::validate_task_source(current)?;
        }
        let position = manifest
            .task_bindings
            .iter()
            .position(|binding| binding.task_id == task.task_id)
            .context("serial task is not selected")?;
        for binding in &manifest.task_bindings[..position] {
            let previous = tasks
                .iter()
                .find(|current| current.task_id == binding.task_id)
                .context("serial predecessor disappeared")?;
            if previous.status != OffdeskTaskStatus::Completed {
                return Ok(Some("serial_predecessor_not_completed".into()));
            }
            let reservation = self
                .record
                .task_launch_reservations
                .iter()
                .find(|reservation| {
                    reservation.task_id == previous.task_id
                        && reservation.attempt_number == previous.attempt_count
                });
            let Some(reservation) = reservation else {
                return Ok(Some("serial_predecessor_launch_unverified".into()));
            };
            if previous.background_ticket_id.as_deref() != Some(&reservation.background_ticket_id)
                || !self.record.task_outcome_receipts.iter().any(|outcome| {
                    outcome.launch_id == reservation.launch_id
                        && outcome.outcome == OffdeskShiftTaskOutcomeV1::Completed
                })
            {
                return Ok(Some("serial_predecessor_outcome_unverified".into()));
            }
            if !super::runtime_occupancy::released(self.live.profile.root_path(), previous)? {
                return Ok(Some("serial_predecessor_release_unverified".into()));
            }
        }
        Ok(None)
    }

    pub(crate) fn commit_task_launch(
        &mut self,
        task: &OffdeskTask,
        tasks: &[OffdeskTask],
        now: DateTime<Utc>,
    ) -> Result<Option<OffdeskShiftTaskLaunchReservationV1>> {
        let decision = self.preflight_task(task, tasks, now)?;
        if decision != OffdeskShiftLaunchDecision::Proceed {
            return Ok(None);
        }
        let state = self.state()?.clone();
        let running_state = if state.status == OffdeskShiftStatusV1::Armed {
            Some(OffdeskShiftStateV1 {
                schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
                shift_id: state.shift_id.clone(),
                manifest_sha256: state.manifest_sha256.clone(),
                arm_receipt_sha256: state.arm_receipt_sha256.clone(),
                sequence: state.sequence + 1,
                status: OffdeskShiftStatusV1::Running,
                updated_at: now.max(state.updated_at),
                reason: "first bound task passed shift and task launch gates".to_string(),
                acceptance_receipt_id: None,
                acceptance_receipt_sha256: None,
            })
        } else {
            None
        };
        let binding = self
            .record
            .manifest
            .task_bindings
            .iter()
            .find(|binding| binding.task_id == task.task_id)
            .context("bound task disappeared before shift launch reservation")?;
        let attempt_number = task
            .attempt_count
            .checked_add(1)
            .context("shift task attempt number overflow")?;
        let launch_id = shift_task_launch_id(
            &self.record.manifest.shift_id,
            &task.task_id,
            attempt_number,
        );
        let reservation = OffdeskShiftTaskLaunchReservationV1 {
            schema: OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA.to_string(),
            launch_id: launch_id.clone(),
            shift_id: self.record.manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(&self.record.manifest)?,
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(
                self.arm_receipt()?,
                &self.record.manifest,
            )?,
            task_id: task.task_id.clone(),
            background_ticket_id: launch_id,
            runner_kind: binding.runner_kind,
            launch_spec_sha256: binding.launch_spec_sha256.clone(),
            execution_brief_sha256: binding.execution_brief_sha256.clone(),
            project_root_identity_sha256: binding.project_root_identity_sha256.clone(),
            host_policy_attestation_sha256: self
                .record
                .manifest
                .host_policy_attestation
                .as_ref()
                .map(super::OffdeskShiftHostPolicyAttestationV1::sha256)
                .transpose()?,
            attempt_number,
            reserved_at: now,
        };
        let weekly = self.weekly_usage_gate(Utc::now())?;
        if self.record.manifest.weekly_usage.is_some() && weekly.is_none() {
            return Ok(None);
        }
        if let Some(check) = &weekly {
            check.ensure_current()?;
        }
        let transition_id = format!("shift_running_{}", state.shift_id);
        let created =
            self.store
                .reserve_task_launch(reservation.clone(), running_state, &transition_id)?;
        if let Some(check) = &weekly {
            check.ensure_current()?;
        }
        self.refresh_current_record()?;
        if !created {
            return Ok(None);
        }
        self.live.ensure_current()?;
        self.validate_wiki_registry_targets()?;
        Ok(Some(reservation))
    }

    pub(crate) fn after_task_store_save(&mut self, task_id: &str) -> Result<()> {
        self.refresh_current_record()?;
        let tasks = self.live.read_current_tasks()?;
        super::workflow::offdesk_preparation::validate_review_binding(
            &self.record.manifest,
            &tasks,
        )?;
        super::workflow::preparation_capabilities::validate_launch(&self.record.manifest, &tasks)?;
        let task = tasks
            .iter()
            .find(|task| task.task_id == task_id)
            .context("launched shift task disappeared after durable task write")?;
        ensure!(
            matches!(
                task.status,
                OffdeskTaskStatus::Launched | OffdeskTaskStatus::Running
            ),
            "launched shift task was not durably recorded as active"
        );
        let binding = self
            .record
            .manifest
            .task_bindings
            .iter()
            .find(|binding| binding.task_id == task_id)
            .context("launched task is no longer bound to the active shift")?;
        let root = self
            .live
            .project_root_for_path(Path::new(&task.workdir))
            .context("launched shift task root was not observed")?;
        binding.validate_identity_against_task(task, &root.identity_sha256)?;
        self.live.ensure_current()?;
        self.validate_wiki_registry_targets()
    }

    pub(crate) fn expected_background_ticket_for_task(&self, task_id: &str) -> Option<String> {
        self.record
            .task_launch_reservations
            .iter()
            .filter(|reservation| reservation.task_id == task_id)
            .max_by_key(|reservation| reservation.attempt_number)
            .map(|reservation| reservation.background_ticket_id.clone())
    }

    pub(crate) fn reconcile_task_record(
        &self,
        task: &mut OffdeskTask,
        now: DateTime<Utc>,
    ) -> Result<bool> {
        let Some(reservation) = self
            .record
            .task_launch_reservations
            .iter()
            .filter(|reservation| reservation.task_id == task.task_id)
            .max_by_key(|reservation| reservation.attempt_number)
        else {
            return Ok(false);
        };
        if !self
            .record
            .task_launch_ticket_receipts
            .iter()
            .any(|ticket| ticket.launch_id == reservation.launch_id)
        {
            return Ok(false);
        }
        if let Some(existing) = task.background_ticket_id.as_deref() {
            ensure!(
                existing == reservation.background_ticket_id,
                "task background ticket differs from its exact shift launch"
            );
        }
        if matches!(
            task.status,
            OffdeskTaskStatus::Completed | OffdeskTaskStatus::Failed | OffdeskTaskStatus::Cancelled
        ) {
            return Ok(false);
        }
        let changed = task.background_ticket_id.as_deref()
            != Some(reservation.background_ticket_id.as_str())
            || task.attempt_count < reservation.attempt_number
            || matches!(
                task.status,
                OffdeskTaskStatus::Queued | OffdeskTaskStatus::PendingApproval
            );
        if !changed {
            return Ok(false);
        }
        task.background_ticket_id = Some(reservation.background_ticket_id.clone());
        task.attempt_count = task.attempt_count.max(reservation.attempt_number);
        if matches!(
            task.status,
            OffdeskTaskStatus::Queued | OffdeskTaskStatus::PendingApproval
        ) {
            task.status = OffdeskTaskStatus::Launched;
        }
        task.last_error = None;
        task.updated_at = now.max(task.updated_at);
        Ok(true)
    }

    pub(crate) fn record_task_launch_ticket(
        &mut self,
        reservation: &OffdeskShiftTaskLaunchReservationV1,
        probe: &BackgroundProbe,
        now: DateTime<Utc>,
    ) -> Result<bool> {
        self.refresh_current_record()?;
        let stored = self
            .record
            .task_launch_reservations
            .iter()
            .find(|stored| stored.launch_id == reservation.launch_id)
            .context("shift task launch reservation disappeared before ticket binding")?;
        ensure!(
            stored == reservation,
            "shift task launch reservation changed before ticket binding"
        );
        if let Some(existing) = self
            .record
            .task_launch_ticket_receipts
            .iter()
            .find(|ticket| ticket.launch_id == reservation.launch_id)
        {
            existing.validate_against(reservation, &self.record.manifest, self.arm_receipt()?)?;
            ensure!(
                existing.background_ticket_id == probe.ticket_id,
                "existing shift task ticket differs from the observed background ticket"
            );
            return Ok(false);
        }
        let ticket = self.task_launch_ticket_receipt(reservation, probe, now)?;
        let created = self.store.record_task_launch_ticket(ticket)?;
        self.refresh_current_record()?;
        Ok(created)
    }

    fn task_launch_ticket_receipt(
        &self,
        reservation: &OffdeskShiftTaskLaunchReservationV1,
        probe: &BackgroundProbe,
        now: DateTime<Utc>,
    ) -> Result<OffdeskShiftTaskLaunchTicketReceiptV1> {
        match (
            self.record.manifest.host_policy_attestation.as_ref(),
            probe.host_policy_launch_evidence.as_ref(),
        ) {
            (Some(attestation), Some(evidence)) => evidence.validate_against(attestation)?,
            (Some(_), None) => {
                anyhow::bail!("attested shift task probe omitted host policy launch evidence")
            }
            (None, Some(_)) => {
                anyhow::bail!("unattested shift task probe claimed host policy launch evidence")
            }
            (None, None) => {}
        }
        super::read_policy::validate_launch_evidence(
            self.record.manifest.read_policy.as_ref(),
            probe.read_policy_launch_evidence.as_ref(),
            &reservation.project_root_identity_sha256,
        )?;
        if let Some(evidence) = &probe.host_policy_launch_evidence {
            ensure!(
                evidence.verified_at >= reservation.reserved_at,
                "shift host policy launch evidence predates its reservation"
            );
        }
        let recorded_at = probe
            .host_policy_launch_evidence
            .as_ref()
            .map(|evidence| evidence.verified_at)
            .unwrap_or(now)
            .max(now)
            .max(reservation.reserved_at);
        let ticket = OffdeskShiftTaskLaunchTicketReceiptV1 {
            schema: OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("shift_task_ticket_{}", reservation.launch_id),
            launch_id: reservation.launch_id.clone(),
            shift_id: reservation.shift_id.clone(),
            manifest_sha256: reservation.manifest_sha256.clone(),
            arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
            task_id: reservation.task_id.clone(),
            attempt_number: reservation.attempt_number,
            background_ticket_id: reservation.background_ticket_id.clone(),
            runner_kind: reservation.runner_kind,
            background_probe_sha256: offdesk_shift_background_probe_sha256(probe)?,
            recorded_at,
        };
        ticket.validate_probe(
            probe,
            reservation,
            &self.record.manifest,
            self.arm_receipt()?,
        )?;
        Ok(ticket)
    }

    pub(crate) fn record_launch_unresolved(
        &mut self,
        launch_id: &str,
        now: DateTime<Utc>,
        reason: &str,
    ) -> Result<bool> {
        self.record_task_outcome(
            launch_id,
            OffdeskShiftTaskOutcomeV1::LaunchUnresolved,
            None,
            None,
            now,
            reason,
        )
    }

    pub(crate) fn reconcile_task_background(
        &mut self,
        outcomes: &[BackgroundPollOutcome],
        scope: &OffdeskShiftBackgroundObservationScope,
        now: DateTime<Utc>,
    ) -> Result<()> {
        self.refresh_current_record()?;
        let reservations = self.record.task_launch_reservations.clone();
        for reservation in reservations {
            if self
                .record
                .task_outcome_receipts
                .iter()
                .any(|outcome| outcome.launch_id == reservation.launch_id)
            {
                continue;
            }
            let observed = outcomes
                .iter()
                .find(|outcome| outcome.probe.ticket_id == reservation.background_ticket_id);
            let has_ticket = self
                .record
                .task_launch_ticket_receipts
                .iter()
                .any(|ticket| ticket.launch_id == reservation.launch_id);
            if !has_ticket {
                match observed {
                    Some(observed) => {
                        let ticket = match self.task_launch_ticket_receipt(
                            &reservation,
                            &observed.probe,
                            now,
                        ) {
                            Ok(ticket) => ticket,
                            Err(error) => {
                                let summary = format!(
                                    "background ticket identity drift: {}",
                                    super::operator_safe_text(&error.to_string())
                                );
                                self.record_task_outcome(
                                    &reservation.launch_id,
                                    OffdeskShiftTaskOutcomeV1::IdentityDrift,
                                    Some(observed.decision.phase),
                                    Some(&observed.probe),
                                    now,
                                    &summary,
                                )?;
                                continue;
                            }
                        };
                        self.store.record_task_launch_ticket(ticket)?;
                        self.refresh_current_record()?;
                    }
                    None if scope.authorizes_absence(&reservation.background_ticket_id) => {
                        self.record_launch_unresolved(
                            &reservation.launch_id,
                            now,
                            "prior shift launch reservation has no exact background ticket",
                        )?;
                        continue;
                    }
                    None => continue,
                }
            }
            self.refresh_current_record()?;
            let Some(observed) = observed else {
                continue;
            };
            if has_ticket {
                self.task_launch_ticket_receipt(&reservation, &observed.probe, now)
                    .context("background ticket identity drift after launch")?;
            }
            let Some(task_outcome) = task_outcome_for_phase(observed.decision.phase) else {
                continue;
            };
            self.record_task_outcome(
                &reservation.launch_id,
                task_outcome,
                Some(observed.decision.phase),
                Some(&observed.probe),
                now,
                &observed.decision.evidence,
            )?;
        }
        Ok(())
    }

    fn record_task_outcome(
        &mut self,
        launch_id: &str,
        outcome: OffdeskShiftTaskOutcomeV1,
        terminal_phase: Option<BackgroundRunnerPhase>,
        probe: Option<&BackgroundProbe>,
        now: DateTime<Utc>,
        summary: &str,
    ) -> Result<bool> {
        self.refresh_current_record()?;
        if self
            .record
            .task_outcome_receipts
            .iter()
            .any(|receipt| receipt.launch_id == launch_id)
        {
            return Ok(false);
        }
        let reservation = self
            .record
            .task_launch_reservations
            .iter()
            .find(|reservation| reservation.launch_id == launch_id)
            .cloned()
            .context("shift task outcome has no launch reservation")?;
        let ticket = self
            .record
            .task_launch_ticket_receipts
            .iter()
            .find(|ticket| ticket.launch_id == launch_id);
        let ticket_hash = ticket
            .map(|ticket| {
                offdesk_shift_task_ticket_receipt_sha256(
                    ticket,
                    &reservation,
                    &self.record.manifest,
                    self.arm_receipt()?,
                )
            })
            .transpose()?;
        let receipt = OffdeskShiftTaskOutcomeReceiptV1 {
            schema: OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("shift_task_outcome_{}", reservation.launch_id),
            launch_id: reservation.launch_id.clone(),
            shift_id: reservation.shift_id.clone(),
            manifest_sha256: reservation.manifest_sha256.clone(),
            arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
            task_id: reservation.task_id.clone(),
            attempt_number: reservation.attempt_number,
            background_ticket_id: reservation.background_ticket_id.clone(),
            outcome,
            terminal_phase,
            launch_ticket_receipt_sha256: ticket_hash,
            background_probe_sha256: probe
                .map(offdesk_shift_background_probe_sha256)
                .transpose()?,
            completed_at: now.max(reservation.reserved_at),
            summary: super::operator_safe_text(summary.trim()),
        };
        let state = self.state()?.clone();
        let attention_state = (outcome.requires_attention()
            && state.status == OffdeskShiftStatusV1::Running)
            .then(|| OffdeskShiftStateV1 {
                schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
                shift_id: state.shift_id.clone(),
                manifest_sha256: state.manifest_sha256.clone(),
                arm_receipt_sha256: state.arm_receipt_sha256.clone(),
                sequence: state.sequence + 1,
                status: OffdeskShiftStatusV1::AttentionRequired,
                updated_at: now.max(state.updated_at),
                reason: format!(
                    "task launch {} recorded {:?}",
                    reservation.launch_id, outcome
                )
                .to_lowercase(),
                acceptance_receipt_id: None,
                acceptance_receipt_sha256: None,
            });
        let transition_id = format!("shift_task_attention_{}", reservation.launch_id);
        let created = self
            .store
            .complete_task_launch(receipt, attention_state, &transition_id)?;
        self.refresh_current_record()?;
        Ok(created)
    }

    fn record_attention(&mut self, now: DateTime<Utc>, reason: &str) -> Result<()> {
        let state = self.state()?.clone();
        if !matches!(
            state.status,
            OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running
        ) {
            return Ok(());
        }
        let next = OffdeskShiftStateV1 {
            schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
            shift_id: state.shift_id.clone(),
            manifest_sha256: state.manifest_sha256.clone(),
            arm_receipt_sha256: state.arm_receipt_sha256.clone(),
            sequence: state.sequence + 1,
            status: OffdeskShiftStatusV1::AttentionRequired,
            updated_at: now.max(state.updated_at),
            reason: reason.to_string(),
            acceptance_receipt_id: None,
            acceptance_receipt_sha256: None,
        };
        let transition_id = format!("shift_attention_{}_{}", state.shift_id, next.sequence);
        self.store.transition(next, &transition_id)?;
        self.refresh_current_record()
    }

    pub(crate) fn view(
        &self,
        held: usize,
        hold_reasons: Vec<String>,
    ) -> Result<OffdeskShiftTickView> {
        Ok(OffdeskShiftTickView {
            shift_id: self.record.manifest.shift_id.clone(),
            status: self.state()?.status,
            window_starts_at: self.record.manifest.window.starts_at,
            window_stops_at: self.record.manifest.window.stops_at,
            bound_tasks: self.record.manifest.task_bindings.len(),
            held,
            hold_reasons,
        })
    }

    fn launch_hold_reason(&self, now: DateTime<Utc>) -> Result<Option<String>> {
        let receipt = self.arm_receipt()?;
        let state = self.state()?;
        let inactive_reason = match state.status {
            OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running => None,
            OffdeskShiftStatusV1::AttentionRequired => Some("shift_state_attention_required"),
            OffdeskShiftStatusV1::ReviewReady => Some("shift_state_review_ready"),
            OffdeskShiftStatusV1::RevisionRequired => Some("shift_state_revision_required"),
            OffdeskShiftStatusV1::Blocked => Some("shift_state_blocked"),
            OffdeskShiftStatusV1::Disarmed => Some("shift_state_disarmed"),
            OffdeskShiftStatusV1::Accepted => Some("shift_state_accepted"),
        };
        if let Some(reason) = inactive_reason {
            return Ok(Some(reason.to_string()));
        }
        if now < self.record.manifest.window.starts_at {
            return Ok(Some("shift_window_not_started".to_string()));
        }
        if now > self.record.manifest.window.stops_at || now > receipt.expires_at {
            return Ok(Some("shift_window_expired".to_string()));
        }
        Ok(None)
    }

    fn refresh_current_record(&mut self) -> Result<()> {
        self.live.ensure_current()?;
        self.store
            .ensure_profile_root_identity(self.live.profile_root_identity())?;
        let current = self
            .store
            .record(&self.record.manifest.shift_id)?
            .context("active shift disappeared during tick")?;
        ensure!(
            current.manifest == self.record.manifest
                && current.arm_receipt == self.record.arm_receipt,
            "active shift authority changed during tick"
        );
        self.record = current;
        self.validate_record()?;
        self.live.ensure_current()
    }

    fn validate_record(&self) -> Result<()> {
        let receipt = self.arm_receipt()?;
        let state = self.state()?;
        state.validate_against(&self.record.manifest, receipt)
    }

    fn arm_receipt(&self) -> Result<&super::OffdeskShiftArmReceiptV1> {
        self.record
            .arm_receipt
            .as_ref()
            .context("active shift has no arm receipt")
    }

    fn state(&self) -> Result<&OffdeskShiftStateV1> {
        self.record
            .state
            .as_ref()
            .context("active shift has no current state")
    }

    fn validate_wiki_registry_targets(&self) -> Result<()> {
        let registry = load_registry();
        for routine in &self.record.manifest.routine_bindings {
            if routine.routine_kind != super::OffdeskShiftRoutineKindV1::WikiCandidateDistillation {
                continue;
            }
            let path = Path::new(
                routine
                    .project_root_path
                    .as_deref()
                    .context("wiki routine has no project root path")?,
            );
            let root = self
                .live
                .project_root_for_path(path)
                .context("wiki routine root was not observed")?;
            let project_key = routine
                .project_key
                .as_deref()
                .context("wiki routine project key missing")?;
            let wiki_profile = routine
                .wiki_profile
                .as_deref()
                .context("wiki routine profile missing")?;
            let entry = resolve_unique_project_for_path(path, &registry)?
                .context("wiki routine root is not uniquely registered")?;
            ensure!(
                entry.key == project_key && entry.wiki_profile.as_deref() == Some(wiki_profile),
                "wiki routine target no longer matches the project registry"
            );
            routine.validate_against_project(project_key, wiki_profile, &root.identity_sha256)?;
        }
        Ok(())
    }
}

pub(crate) fn ensure_boundaries_current(
    profile: &ProfileSourceBoundary,
    project_roots: &[ProjectRootBoundary],
) -> Result<()> {
    profile.ensure_current()?;
    for root in project_roots {
        root.ensure_current()?;
    }
    super::workflow::invocation_inputs::ReviewInputScope::ensure_current()?;
    profile.ensure_current()
}

fn relative_to_root(root: &Path, path: &Path) -> Result<PathBuf> {
    let normalized_root = normalize_macos_private(root);
    let normalized_path = normalize_macos_private(path);
    let relative = normalized_path
        .strip_prefix(&normalized_root)
        .with_context(|| format!("shift source is outside profile root: {}", path.display()))?;
    ensure!(
        !relative.as_os_str().is_empty()
            && relative
                .components()
                .all(|component| matches!(component, Component::Normal(_))),
        "shift source path is not one safe profile-relative path"
    );
    Ok(relative.to_path_buf())
}

fn normalize_absolute(path: &Path) -> Result<PathBuf> {
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    Ok(normalize_macos_private(&path))
}

fn normalize_macos_private(path: &Path) -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        path.strip_prefix("/private").unwrap_or(path).to_path_buf()
    }
    #[cfg(not(target_os = "macos"))]
    {
        path.to_path_buf()
    }
}

fn ensure_single_link(file: &File, path: &Path) -> Result<()> {
    let metadata = file.metadata()?;
    ensure!(metadata.is_file(), "shift source is not a regular file");
    #[cfg(unix)]
    ensure!(
        metadata.nlink() == 1,
        "shift source must have exactly one link: {}",
        path.display()
    );
    Ok(())
}

fn shift_task_launch_id(shift_id: &str, task_id: &str, attempt_number: u32) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"forager.offdesk-shift.task-launch-reservation.v1\0");
    hasher.update(shift_id.as_bytes());
    hasher.update([0]);
    hasher.update(task_id.as_bytes());
    hasher.update([0]);
    hasher.update(attempt_number.to_be_bytes());
    format!("shift_launch_{:x}", hasher.finalize())
}

fn task_outcome_for_phase(phase: BackgroundRunnerPhase) -> Option<OffdeskShiftTaskOutcomeV1> {
    match phase {
        BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived => {
            Some(OffdeskShiftTaskOutcomeV1::Completed)
        }
        BackgroundRunnerPhase::Failed
        | BackgroundRunnerPhase::StaleNoAck
        | BackgroundRunnerPhase::StaleLostCallback
        | BackgroundRunnerPhase::Reconstructable => {
            Some(OffdeskShiftTaskOutcomeV1::RecoveryRequired)
        }
        BackgroundRunnerPhase::Terminated => Some(OffdeskShiftTaskOutcomeV1::Terminated),
        BackgroundRunnerPhase::Launched
        | BackgroundRunnerPhase::HandoffEmitted
        | BackgroundRunnerPhase::PickupAcknowledged
        | BackgroundRunnerPhase::RecoveryAcknowledged => None,
    }
}

#[cfg(test)]
mod tests {
    use chrono::{Duration, Utc};

    use super::*;
    use crate::offdesk::OffdeskShiftRoutineScheduleV1;

    #[test]
    fn routine_execution_policy_is_canonical_for_each_manifest_kind() {
        let now = Utc::now();
        let cases = [
            (
                OffdeskShiftRoutineBindingV1 {
                    routine_id: "dispatch_tick".to_string(),
                    routine_kind: OffdeskShiftRoutineKindV1::DispatchTick,
                    schedule: OffdeskShiftRoutineScheduleV1::Interval {
                        starts_at: now,
                        stops_at: now + Duration::hours(1),
                        every_minutes: 10,
                    },
                    project_key: None,
                    wiki_profile: None,
                    project_root_identity_sha256: None,
                    project_root_path: None,
                },
                OffdeskShiftRoutineAdapterHandlerV1::DispatchTick,
                OffdeskShiftRoutineAllowedEffectV1::BoundedShiftTick,
                false,
            ),
            (
                OffdeskShiftRoutineBindingV1 {
                    routine_id: "wiki_candidate_distillation".to_string(),
                    routine_kind: OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
                    schedule: OffdeskShiftRoutineScheduleV1::Once { at: now },
                    project_key: Some("project".to_string()),
                    wiki_profile: Some("wiki".to_string()),
                    project_root_identity_sha256: Some("a".repeat(64)),
                    project_root_path: Some("/tmp/project".to_string()),
                },
                OffdeskShiftRoutineAdapterHandlerV1::WikiCandidatePrereview,
                OffdeskShiftRoutineAllowedEffectV1::CandidateOnlyWikiPrereview,
                false,
            ),
            (
                OffdeskShiftRoutineBindingV1 {
                    routine_id: "morning_review".to_string(),
                    routine_kind: OffdeskShiftRoutineKindV1::MorningReviewBrief,
                    schedule: OffdeskShiftRoutineScheduleV1::Once { at: now },
                    project_key: None,
                    wiki_profile: None,
                    project_root_identity_sha256: None,
                    project_root_path: None,
                },
                OffdeskShiftRoutineAdapterHandlerV1::MorningReviewDelivery,
                OffdeskShiftRoutineAllowedEffectV1::LocalReviewArtifactsAndNotification,
                true,
            ),
        ];

        for (binding, handler, effect, disarm) in cases {
            let enterprise_policy = OffdeskShiftEnterprisePolicyV1::built_in(
                OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
            );
            let policy = OffdeskShiftRoutineExecutionPolicyV1::from_binding(
                &binding,
                OffdeskShiftRoutineResourceAllowanceV1 {
                    process_starts: 4,
                    external_calls: 2,
                    disk_bytes_written: 1024,
                    llm_tokens: 2048,
                    cost_micro_usd: 10,
                    cost_rate_micro_usd_per_million_tokens: 100,
                },
                &enterprise_policy,
            )
            .unwrap();
            assert_eq!(policy.handler, handler);
            assert_eq!(policy.allowed_effect, effect);
            assert_eq!(policy.disarm_after_terminal, disarm);
            assert_eq!(policy.project_key, binding.project_key);
            assert_eq!(
                policy.does_not_authorize.len(),
                OFFDESK_SHIFT_ROUTINE_EXECUTION_REQUIRED_DENIALS.len()
            );

            let mut forged = policy;
            forged.handler = OffdeskShiftRoutineAdapterHandlerV1::DispatchTick;
            if handler == OffdeskShiftRoutineAdapterHandlerV1::DispatchTick {
                forged.allowed_effect =
                    OffdeskShiftRoutineAllowedEffectV1::LocalReviewArtifactsAndNotification;
            }
            assert!(forged
                .validate_against(&binding, &enterprise_policy)
                .is_err());
        }
    }
}
