//! Authority-bearing preparation, arming, and disarming for bounded shifts.

pub(crate) mod local_review;
mod selection;

pub(super) fn propose_offdesk_shift(
    profile: &str,
    args: &super::ShiftProposalArgs,
) -> anyhow::Result<serde_json::Value> {
    selection::propose(profile, args)
}

use std::collections::{HashMap, HashSet};
use std::io::Read;
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::plan_queries::resolve_offdesk_plan_item;
use super::plan_registry::{load_offdesk_plan_reviews, offdesk_plan_registry_dir};
use super::{
    ShiftArmArgs, ShiftCouncilPacketArgs, ShiftDisarmArgs, ShiftPrepareArgs, ShiftRoutineClaimArgs,
    ShiftRoutineCompleteArgs, ShiftRoutineOutcomeArg, ShiftRoutineRecoverArgs,
    ShiftRoutineStartArgs, ShiftRoutineTaskArg, ShiftTemplateInstantiateArgs,
};
use crate::offdesk::shift_runtime::{
    complete_reserved_routine, ensure_boundaries_current, OffdeskShiftLiveSources,
    OffdeskShiftRoutineCompletionInput, OffdeskShiftRoutineExecutionPolicyV1,
    OffdeskShiftRoutineStartDecision, OffdeskShiftTickAuthority, ProfileSourceBoundary,
    ProjectRootBoundary,
};
use crate::offdesk::{
    build_offdesk_shift_council_escalation_packet, offdesk_shift_arm_receipt_sha256,
    offdesk_shift_manifest_sha256, pending_approval_operator_views, ActionApprovalMetadata,
    ActionApprovalRequest, ApprovalBrief, ApprovalBriefOption, ApprovalLedger, ApprovalMode,
    ApprovalScope, ApprovalStatus, OffdeskPendingApprovalView, OffdeskPlanRegistration,
    OffdeskPlanReviewRecord, OffdeskShiftArmApprovalMetadataV1, OffdeskShiftArmReceiptV1,
    OffdeskShiftAuthorityV1, OffdeskShiftBudgetV1, OffdeskShiftCouncilEscalationPacketV1,
    OffdeskShiftEnterprisePolicyV1, OffdeskShiftManifestV1, OffdeskShiftRecordV1,
    OffdeskShiftRoutineBindingV1, OffdeskShiftRoutineEvidenceV1, OffdeskShiftRoutineKindV1,
    OffdeskShiftRoutineOutcomeReceiptV1, OffdeskShiftRoutineOutcomeV1,
    OffdeskShiftRoutineRecoveryAcknowledgementV1, OffdeskShiftRoutineResourceBudgetV1,
    OffdeskShiftRoutineRunReservationV1, OffdeskShiftRoutineScheduleV1, OffdeskShiftRoutineUsageV1,
    OffdeskShiftStateV1, OffdeskShiftStatusV1, OffdeskShiftStore, OffdeskShiftTaskBindingV1,
    OffdeskShiftTaskObservation, OffdeskShiftTemplateInstantiationV1, OffdeskShiftWindowV1,
    OffdeskTask, RiskLevel, OFFDESK_PLAN_REGISTRATION_SCHEMA, OFFDESK_PLAN_REVIEW_SCHEMA,
    OFFDESK_SHIFT_ARM_APPROVAL_METADATA_SCHEMA, OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA,
    OFFDESK_SHIFT_MANIFEST_SCHEMA, OFFDESK_SHIFT_REQUIRED_DENIALS,
    OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA,
    OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS, OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA,
    OFFDESK_SHIFT_STATE_SCHEMA,
};
use crate::session::project_registry::{load_registry, resolve_unique_project_for_path};
use crate::session::{get_profile_dir, normalize_profile_name};

const SHIFT_ARM_ACTION: &str = "offdesk.shift.arm";
const SHIFT_ARM_SOURCE: &str = "cli.offdesk.shift";
const MAX_ROUTINE_EVIDENCE_BYTES: u64 = 16 * 1024 * 1024;
const MAX_ROUTINE_EVIDENCE_FILES: usize = 16;

pub(super) struct ShiftPrepareResult {
    pub record: OffdeskShiftRecordV1,
    pub created: bool,
}

pub(super) struct ShiftTemplateInstantiationResult {
    pub record: OffdeskShiftRecordV1,
    pub created: bool,
}

pub(super) enum ShiftArmResult {
    Pending(Vec<OffdeskPendingApprovalView>),
    Armed {
        record: Box<OffdeskShiftRecordV1>,
        created: bool,
    },
}

pub(super) struct ShiftDisarmResult {
    pub record: OffdeskShiftRecordV1,
    pub changed: bool,
}

pub(super) struct ShiftRoutineStartResult {
    pub record: OffdeskShiftRecordV1,
    pub routine: OffdeskShiftRoutineBindingV1,
    pub outcome: &'static str,
    pub reservation: Option<OffdeskShiftRoutineRunReservationV1>,
    pub receipt: Option<OffdeskShiftRoutineOutcomeReceiptV1>,
    pub reason: Option<String>,
}

pub(super) struct ShiftRoutineClaimResult {
    pub record: OffdeskShiftRecordV1,
    pub requested_task: &'static str,
    pub routine: Option<OffdeskShiftRoutineBindingV1>,
    pub policy: Option<OffdeskShiftRoutineExecutionPolicyV1>,
    pub outcome: &'static str,
    pub reservation: Option<OffdeskShiftRoutineRunReservationV1>,
    pub receipt: Option<OffdeskShiftRoutineOutcomeReceiptV1>,
    pub reason: Option<String>,
}

pub(super) struct ShiftRoutineCompleteResult {
    pub record: OffdeskShiftRecordV1,
    pub receipt: OffdeskShiftRoutineOutcomeReceiptV1,
    pub created: bool,
}

pub(super) struct ShiftRoutineRecoveryResult {
    pub record: OffdeskShiftRecordV1,
    pub receipt: OffdeskShiftRoutineOutcomeReceiptV1,
    pub created: bool,
    pub outcome: &'static str,
}

pub(super) fn build_shift_council_packet(
    profile: &str,
    args: &ShiftCouncilPacketArgs,
) -> Result<OffdeskShiftCouncilEscalationPacketV1> {
    let generated_at = Utc::now();
    let profile = normalize_profile_name(profile)?;
    let projection = crate::offdesk::build_offdesk_shift_review_projection(
        &profile,
        args.shift_id.as_deref(),
        args.current_run_id.as_deref(),
        generated_at,
    )?
    .context("no bounded Offdesk shift exists")?;
    let store = OffdeskShiftStore::read_only(&profile)?;
    let record = store
        .record(&projection.shift_id)?
        .context("Council packet shift disappeared after review projection")?;
    ensure!(
        offdesk_shift_manifest_sha256(&record.manifest)? == projection.manifest_sha256,
        "Council packet shift manifest changed after review projection"
    );

    let profile_dir = crate::session::profile_dir_path(&profile)?;
    let mut sources = ProfileSourceBoundary::open(profile_dir.clone())?;

    let plan = load_plan_authority(
        &profile,
        &mut sources,
        &record.manifest.plan_id,
        Some(&record.manifest.plan_review_id),
    )?;
    ensure!(
        plan.registration.source_sha256 == record.manifest.plan_source_sha256
            && plan.review_sha256 == record.manifest.plan_review_sha256,
        "Council packet plan authority changed after shift preparation"
    );
    let task_ids = record
        .manifest
        .task_bindings
        .iter()
        .map(|binding| binding.task_id.clone())
        .collect::<Vec<_>>();
    let tasks = load_selected_tasks(&mut sources, &task_ids, false)?;
    sources.ensure_current()?;
    let packet = build_offdesk_shift_council_escalation_packet(
        projection,
        &record,
        &plan.source_value,
        &plan.review,
        &tasks,
    )?;
    sources.ensure_current()?;
    ensure!(
        store.record(&record.manifest.shift_id)?.as_ref() == Some(&record),
        "Council packet shift evidence changed during construction"
    );
    Ok(packet)
}

pub(super) fn active_shift_tick_authority(
    profile: &str,
    shift_id: Option<&str>,
    now: DateTime<Utc>,
) -> Result<Option<OffdeskShiftTickAuthority>> {
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let snapshot = store.snapshot()?;
    let record = if let Some(shift_id) = shift_id {
        snapshot
            .records
            .into_iter()
            .find(|record| record.manifest.shift_id == shift_id)
            .with_context(|| format!("bounded Offdesk shift not found: {shift_id}"))?
    } else if let Some(active) = snapshot.active().cloned() {
        active
    } else {
        return Ok(None);
    };
    let live = revalidate_manifest_authority(&profile, &record.manifest, now, true, false)?;
    Ok(Some(OffdeskShiftTickAuthority::new(store, record, live)?))
}

pub(super) fn reconcile_polled_shift_tasks(
    profile: &str,
    outcomes: &[crate::offdesk::BackgroundPollOutcome],
    now: DateTime<Utc>,
) -> Result<()> {
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    for record in store.snapshot()?.records {
        if record.arm_receipt.is_none()
            || !record.task_launch_reservations.iter().any(|reservation| {
                outcomes
                    .iter()
                    .any(|outcome| outcome.probe.ticket_id == reservation.background_ticket_id)
                    && !record
                        .task_outcome_receipts
                        .iter()
                        .any(|receipt| receipt.launch_id == reservation.launch_id)
            })
        {
            continue;
        }
        let live = revalidate_manifest_authority(&profile, &record.manifest, now, true, false)?;
        let mut authority =
            OffdeskShiftTickAuthority::new(OffdeskShiftStore::writable(&profile)?, record, live)?;
        // A poll observes existing tickets; it never declares unobserved
        // reservations lost or launches work, including after window expiry.
        authority.reconcile_task_background(
            outcomes,
            &crate::offdesk::shift_runtime::OffdeskShiftBackgroundObservationScope::None,
            now,
        )?;
        authority.begin_tick(now)?;
    }
    Ok(())
}

pub(super) fn start_offdesk_shift_routine(
    profile: &str,
    args: &ShiftRoutineStartArgs,
) -> Result<ShiftRoutineStartResult> {
    let now = Utc::now();
    let mut authority = active_shift_tick_authority(profile, Some(&args.shift_id), now)?
        .context("selected shift has no routine authority")?;
    let routine = authority
        .record()
        .manifest
        .routine_bindings
        .iter()
        .find(|routine| routine.routine_id == args.routine_id)
        .cloned()
        .context("routine is not bound to the selected shift")?;
    let decision = authority.start_routine(&args.routine_id, now)?;
    let (outcome, reservation, receipt, reason) = match decision {
        OffdeskShiftRoutineStartDecision::Reserved(reservation) => {
            ("reserved", Some(reservation), None, None)
        }
        OffdeskShiftRoutineStartDecision::AlreadyCompleted(receipt) => {
            ("already_completed", None, Some(*receipt), None)
        }
        OffdeskShiftRoutineStartDecision::RecoveryRequired(reservation) => (
            "recovery_required",
            Some(reservation),
            None,
            Some("routine reservation has no terminal outcome".to_string()),
        ),
        OffdeskShiftRoutineStartDecision::NotDue(_) => (
            "not_due",
            None,
            None,
            Some("routine is not due".to_string()),
        ),
        OffdeskShiftRoutineStartDecision::Held(reason) => ("held", None, None, Some(reason)),
    };
    Ok(ShiftRoutineStartResult {
        record: authority.record().clone(),
        routine,
        outcome,
        reservation,
        receipt,
        reason,
    })
}

pub(super) fn claim_offdesk_shift_routine(
    profile: &str,
    args: &ShiftRoutineClaimArgs,
) -> Result<ShiftRoutineClaimResult> {
    let now = Utc::now();
    let mut authority = active_shift_tick_authority(profile, Some(&args.shift_id), now)?
        .context("selected shift has no routine authority")?;
    let expected_kind = match args.task {
        ShiftRoutineTaskArg::Tick => OffdeskShiftRoutineKindV1::DispatchTick,
        ShiftRoutineTaskArg::Distill => OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
        ShiftRoutineTaskArg::Brief => OffdeskShiftRoutineKindV1::MorningReviewBrief,
    };
    let requested_task = match args.task {
        ShiftRoutineTaskArg::Tick => "tick",
        ShiftRoutineTaskArg::Distill => "distill",
        ShiftRoutineTaskArg::Brief => "brief",
    };
    let routines = authority
        .record()
        .manifest
        .routine_bindings
        .iter()
        .filter(|routine| routine.routine_kind == expected_kind)
        .cloned()
        .collect::<Vec<_>>();
    ensure!(
        routines.len() <= 1,
        "shift routine policy found multiple handlers for one timer task"
    );
    let Some(routine) = routines.into_iter().next() else {
        return Ok(ShiftRoutineClaimResult {
            record: authority.record().clone(),
            requested_task,
            routine: None,
            policy: None,
            outcome: "not_configured",
            reservation: None,
            receipt: None,
            reason: Some("reviewed manifest has no routine for this timer task".to_string()),
        });
    };
    let decision = authority.start_routine(&routine.routine_id, now)?;
    let resource_allowance = authority.routine_resource_allowance()?.unwrap_or_else(
        crate::offdesk::shift_runtime::OffdeskShiftRoutineResourceAllowanceV1::unavailable,
    );
    let policy = OffdeskShiftRoutineExecutionPolicyV1::from_binding(
        &routine,
        resource_allowance,
        authority.record().manifest.enterprise_policy()?,
    )?;
    let (outcome, reservation, receipt, reason) = match decision {
        OffdeskShiftRoutineStartDecision::Reserved(reservation) => {
            ("reserved", Some(reservation), None, None)
        }
        OffdeskShiftRoutineStartDecision::AlreadyCompleted(receipt) => {
            ("already_completed", None, Some(*receipt), None)
        }
        OffdeskShiftRoutineStartDecision::RecoveryRequired(reservation) => (
            "recovery_required",
            Some(reservation),
            None,
            Some("routine reservation has no terminal outcome".to_string()),
        ),
        OffdeskShiftRoutineStartDecision::NotDue(_) => (
            "not_due",
            None,
            None,
            Some("routine is not due".to_string()),
        ),
        OffdeskShiftRoutineStartDecision::Held(reason) => ("held", None, None, Some(reason)),
    };
    if let Some(reservation) = &reservation {
        ensure!(
            reservation.routine_id == policy.routine_id
                && reservation.routine_kind == policy.routine_kind,
            "shift routine policy does not match its reservation"
        );
    }
    Ok(ShiftRoutineClaimResult {
        record: authority.record().clone(),
        requested_task,
        routine: Some(routine),
        policy: Some(policy),
        outcome,
        reservation,
        receipt,
        reason,
    })
}

pub(super) fn complete_offdesk_shift_routine(
    profile: &str,
    args: &ShiftRoutineCompleteArgs,
) -> Result<ShiftRoutineCompleteResult> {
    let now = Utc::now();
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let record = store
        .record(&args.shift_id)?
        .with_context(|| format!("bounded Offdesk shift not found: {}", args.shift_id))?;
    let outcome = match args.outcome {
        ShiftRoutineOutcomeArg::Succeeded => OffdeskShiftRoutineOutcomeV1::Succeeded,
        ShiftRoutineOutcomeArg::Failed => OffdeskShiftRoutineOutcomeV1::Failed,
        ShiftRoutineOutcomeArg::Skipped => OffdeskShiftRoutineOutcomeV1::Skipped,
        ShiftRoutineOutcomeArg::BudgetExhausted => OffdeskShiftRoutineOutcomeV1::BudgetExhausted,
        ShiftRoutineOutcomeArg::IdentityDrift => OffdeskShiftRoutineOutcomeV1::IdentityDrift,
    };
    let evidence = observe_routine_evidence(&args.evidence_paths)?;
    let usage = if record.manifest.routine_resource_budget.is_some() {
        ensure!(
            args.usage_complete,
            "routine completion requires --usage-complete for this manifest"
        );
        let disk_bytes_written = evidence.iter().try_fold(0_u64, |total, item| {
            total
                .checked_add(item.size_bytes)
                .context("routine evidence byte total overflow")
        })?;
        Some(OffdeskShiftRoutineUsageV1 {
            schema: OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA.to_string(),
            measurement_complete: true,
            process_starts: args.process_starts,
            external_calls: args.external_calls,
            disk_bytes_written,
            llm_tokens: args.llm_tokens,
            cost_micro_usd: args.cost_micro_usd,
            cost_rate_micro_usd_per_million_tokens: args.cost_rate_micro_usd_per_million_tokens,
        })
    } else {
        ensure!(
            !args.usage_complete
                && args.process_starts == 0
                && args.external_calls == 0
                && args.llm_tokens == 0
                && args.cost_micro_usd == 0
                && args.cost_rate_micro_usd_per_million_tokens.is_none(),
            "legacy routine completion cannot add unbound resource usage"
        );
        None
    };
    let (receipt, created, record) = complete_reserved_routine(
        &store,
        &record,
        OffdeskShiftRoutineCompletionInput {
            run_id: &args.run_id,
            outcome,
            summary: &args.summary,
            evidence: &evidence,
            usage,
            recovery_acknowledgement: None,
        },
        now,
    )?;
    Ok(ShiftRoutineCompleteResult {
        record,
        receipt,
        created,
    })
}

pub(super) fn recover_offdesk_shift_routine(
    profile: &str,
    args: &ShiftRoutineRecoverArgs,
) -> Result<ShiftRoutineRecoveryResult> {
    ensure!(
        args.acknowledge_execution_unknown,
        "routine recovery requires --acknowledge-execution-unknown"
    );
    ensure!(
        !args.reason.trim().is_empty(),
        "routine recovery requires a non-empty reason"
    );
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let record = store
        .record(&args.shift_id)?
        .with_context(|| format!("bounded Offdesk shift not found: {}", args.shift_id))?;
    ensure!(
        record
            .routine_run_reservations
            .iter()
            .any(|reservation| reservation.run_id == args.run_id),
        "routine recovery has no exact reservation"
    );
    if let Some(receipt) = record
        .routine_outcome_receipts
        .iter()
        .find(|receipt| receipt.run_id == args.run_id)
        .cloned()
    {
        return Ok(ShiftRoutineRecoveryResult {
            record,
            receipt,
            created: false,
            outcome: "already_terminal",
        });
    }

    let evidence = observe_routine_evidence(&args.evidence_paths)?;
    let safe_reason = crate::offdesk::operator_safe_text(args.reason.trim());
    let summary =
        format!("operator recovery closed unknown routine execution as failed: {safe_reason}");
    let now = Utc::now();
    let recovery_acknowledgement = OffdeskShiftRoutineRecoveryAcknowledgementV1 {
        schema: OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA.to_string(),
        acknowledged_at: now,
        acknowledged_by: "local_operator".to_string(),
        reason: safe_reason,
        execution_result_unknown: true,
        success_not_asserted: true,
        does_not_authorize: OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS
            .iter()
            .map(|value| (*value).to_string())
            .collect(),
    };
    let (receipt, created, record) = complete_reserved_routine(
        &store,
        &record,
        OffdeskShiftRoutineCompletionInput {
            run_id: &args.run_id,
            outcome: OffdeskShiftRoutineOutcomeV1::Failed,
            summary: &summary,
            evidence: &evidence,
            usage: None,
            recovery_acknowledgement: Some(recovery_acknowledgement),
        },
        now,
    )?;
    Ok(ShiftRoutineRecoveryResult {
        record,
        receipt,
        created,
        outcome: if created {
            "recovered_failed"
        } else {
            "already_terminal"
        },
    })
}

fn observe_routine_evidence(paths: &[PathBuf]) -> Result<Vec<OffdeskShiftRoutineEvidenceV1>> {
    ensure!(
        paths.len() <= MAX_ROUTINE_EVIDENCE_FILES,
        "shift routine evidence exceeds its file budget"
    );
    paths
        .iter()
        .map(|path| {
            ensure!(
                path.is_absolute(),
                "shift routine evidence path must be absolute"
            );
            let parent = path
                .parent()
                .context("shift routine evidence has no parent directory")?;
            let name = path
                .file_name()
                .context("shift routine evidence has no file name")?;
            let relative = Path::new(name);
            let root = crate::process::open_bounded_root(parent).with_context(|| {
                format!("open shift routine evidence parent: {}", parent.display())
            })?;
            let root_identity = crate::process::filesystem_file_identity(&root.metadata()?);
            let mut file = crate::process::open_bounded_regular_file(&root, relative)
                .with_context(|| format!("open shift routine evidence: {}", path.display()))?;
            let metadata = file.metadata()?;
            ensure!(
                metadata.is_file(),
                "shift routine evidence is not a regular file"
            );
            #[cfg(unix)]
            ensure!(
                metadata.nlink() == 1,
                "shift routine evidence must have exactly one link"
            );
            ensure!(
                metadata.len() <= MAX_ROUTINE_EVIDENCE_BYTES,
                "shift routine evidence exceeds the byte budget"
            );
            let initial = crate::process::FilesystemObjectSnapshot::from_metadata(&metadata);
            let mut bytes = Vec::with_capacity(usize::try_from(metadata.len())?);
            (&mut file)
                .take(MAX_ROUTINE_EVIDENCE_BYTES + 1)
                .read_to_end(&mut bytes)?;
            ensure!(
                u64::try_from(bytes.len())? <= MAX_ROUTINE_EVIDENCE_BYTES,
                "shift routine evidence grew beyond the byte budget"
            );
            ensure!(
                crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?)
                    == initial,
                "shift routine evidence changed while hashing"
            );
            let active_file = crate::process::open_bounded_regular_file(&root, relative)?;
            ensure!(
                crate::process::FilesystemObjectSnapshot::from_metadata(&active_file.metadata()?)
                    == initial,
                "shift routine evidence path changed while hashing"
            );
            let active_root = crate::process::open_bounded_root(parent)?;
            ensure!(
                crate::process::filesystem_file_identity(&active_root.metadata()?) == root_identity,
                "shift routine evidence parent changed while hashing"
            );
            Ok(OffdeskShiftRoutineEvidenceV1 {
                reference: crate::offdesk::operator_safe_text(&path.display().to_string()),
                sha256: format!("{:x}", Sha256::digest(&bytes)),
                size_bytes: u64::try_from(bytes.len())?,
            })
        })
        .collect()
}

pub(super) fn prepare_offdesk_shift(
    profile: &str,
    args: &ShiftPrepareArgs,
) -> Result<ShiftPrepareResult> {
    prepare_offdesk_shift_inner(profile, args, None)
}

pub(super) fn instantiate_offdesk_shift_template(
    profile: &str,
    args: &ShiftTemplateInstantiateArgs,
) -> Result<ShiftTemplateInstantiationResult> {
    ensure!(
        args.prepare.shift_id.is_none(),
        "instantiate-template derives the shift ID; --shift-id is not allowed"
    );
    let cadence = args.cadence.into();
    let template_instantiation = OffdeskShiftTemplateInstantiationV1::new(
        &args.template_key,
        cadence,
        args.prepare.starts_at,
    )?;
    let mut prepare = args.prepare.clone();
    prepare.shift_id = Some(crate::offdesk::offdesk_shift_template_shift_id(
        &template_instantiation.template_key,
        cadence,
        prepare.starts_at,
    )?);
    let result = prepare_offdesk_shift_inner(profile, &prepare, Some(template_instantiation))?;
    Ok(ShiftTemplateInstantiationResult {
        record: result.record,
        created: result.created,
    })
}

fn prepare_offdesk_shift_inner(
    profile: &str,
    args: &ShiftPrepareArgs,
    template_instantiation: Option<OffdeskShiftTemplateInstantiationV1>,
) -> Result<ShiftPrepareResult> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    validate_prepare_args(args, Utc::now())?;
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::read_only(&profile)?;
    let (manifest, sources, project_roots) =
        build_shift_manifest(&profile, args, template_instantiation, &store)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let created = store.save_manifest(manifest.clone())?;
    ensure_boundaries_current(&sources, &project_roots).with_context(|| {
        format!(
            "shift {} has a stored manifest, but input revalidation failed; inspect shift state before explicit recovery",
            manifest.shift_id
        )
    })?;
    let record = store
        .record(&manifest.shift_id)?
        .context("prepared shift was not durably readable")?;
    let mut comparable_manifest = manifest;
    comparable_manifest.created_at = record.manifest.created_at;
    ensure!(
        record.manifest == comparable_manifest,
        "prepared shift manifest changed"
    );
    Ok(ShiftPrepareResult { record, created })
}

pub(super) fn preview_shift_candidates(
    profile: &str,
    args: &super::ShiftCandidateArgs,
) -> Result<Value> {
    let prepare = &args.prepare;
    validate_prepare_window(prepare, Utc::now())?;
    ensure!(
        prepare.task_ids.is_empty() && prepare.shift_id.is_none(),
        "candidates discovers existing tasks; --task-id and --shift-id are not allowed"
    );
    ensure!(
        prepare.host_policy.is_none(),
        "candidates does not run host-policy probes"
    );
    ensure!(
        prepare.serial_proposal_sha256.is_none() && prepare.serial_project_keys.is_empty(),
        "serial binding flags belong to prepare/preview, not candidates"
    );
    let profile = normalize_profile_name(profile)?;
    let registry = load_registry();
    for key in &args.project_keys {
        ensure!(
            registry.iter().filter(|entry| entry.key == *key).count() == 1,
            "candidate project must be uniquely registered: {key}"
        );
    }
    let mut sources = ProfileSourceBoundary::open(crate::session::profile_dir_path(&profile)?)?;
    let plan = load_plan_authority(
        &profile,
        &mut sources,
        &prepare.plan_ref,
        prepare.review_id.as_deref(),
    )?;
    let queue_path = sources.root_path().join("offdesk_tasks.json");
    let queue_bytes = if queue_path.try_exists()? {
        Some(sources.read_bytes(&queue_path)?)
    } else {
        None
    };
    let queue_sha256 = queue_bytes.as_ref().map(|bytes| sha256_hex(bytes));
    let mut tasks: Vec<OffdeskTask> = if let Some(bytes) = &queue_bytes {
        crate::migrations::v005_task_review_links::decode(std::str::from_utf8(bytes)?)?
    } else {
        Vec::new()
    };
    ensure!(
        tasks.len() <= 1024,
        "candidate queue exceeds 1024-task inspection limit"
    );
    let mut ids = HashSet::new();
    ensure!(
        tasks.iter().all(|task| ids.insert(task.task_id.clone())),
        "candidate queue has duplicate task IDs"
    );
    tasks.sort_by(|a, b| a.task_id.cmp(&b.task_id));
    let outside_scope_count = tasks
        .iter()
        .filter(|task| !args.project_keys.contains(&task.project_key))
        .count();
    let store = OffdeskShiftStore::read_only(&profile)?;
    let mut candidates = Vec::new();
    let mut eligible_ids = Vec::new();
    let mut boundaries = Vec::new();
    for task in tasks
        .iter()
        .filter(|task| args.project_keys.contains(&task.project_key))
    {
        let mut selected = prepare.clone();
        selected.task_ids = vec![task.task_id.clone()];
        selected.review_id = Some(plan.review.review_id.clone());
        let inspected = (|| -> Result<_> {
            let entry = resolve_unique_project_for_path(Path::new(&task.workdir), &registry)?
                .context("candidate workdir is not uniquely registered")?;
            ensure!(
                entry.key == task.project_key,
                "candidate workdir belongs to another project"
            );
            build_shift_manifest(&profile, &selected, None, &store)
        })();
        match inspected {
            Ok((manifest, source, roots)) => {
                eligible_ids.push(task.task_id.clone());
                candidates.push(serde_json::json!({"task_id":task.task_id, "project_key":task.project_key,
                    "status":"individually_passed_prepare_checks", "launch_spec_sha256":manifest.task_bindings[0].launch_spec_sha256}));
                boundaries.push((source, roots));
            }
            Err(error) => candidates.push(serde_json::json!({"task_id":task.task_id,
                "project_key":task.project_key, "status":"excluded", "reason":error.to_string()})),
        }
    }
    for (source, roots) in &boundaries {
        ensure_boundaries_current(source, roots)?;
    }
    let queue_conflicts = crate::offdesk::workdir_conflicts::inspect_with_runtime(
        sources.root_path(),
        &tasks,
        &eligible_ids,
    )?;
    sources.ensure_current()?;
    Ok(serde_json::json!({
        "schema":"offdesk_shift_candidates.v1", "profile":profile,
        "plan_id":plan.review.plan_id, "plan_review_id":plan.review.review_id,
        "queue_sha256":queue_sha256,
        "project_keys":args.project_keys,
        "window":{"starts_at":prepare.starts_at,"stops_at":prepare.stops_at,"timezone":prepare.timezone},
        "candidate_count":candidates.len(), "eligible_task_ids":eligible_ids,
        "outside_scope_count":outside_scope_count, "candidates":candidates,
        "selection_status":if eligible_ids.is_empty() { "no_eligible_work" } else { "individual_candidates_only" },
        "execution_order":"not_established", "estimated_wall_time":"unavailable",
        "ownership_and_resource_conflicts":"not_verified",
        "queue_conflicts":queue_conflicts,
        "task_cost_ceiling":"not_established",
        "does_not_authorize":["plan_write","enqueue","approval","arm","launch","session_input"],
        "next_step":"choose exact tasks and run shift preview; individual eligibility is not combined approval"
    }))
}

fn preparation_draft(manifest: &OffdeskShiftManifestV1, tasks: &[OffdeskTask]) -> Value {
    crate::offdesk::workflow::offdesk_preparation::from_shift(manifest, tasks)
}

pub(super) fn preview_offdesk_shift(profile: &str, args: &ShiftPrepareArgs) -> Result<Value> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    ensure!(
        args.host_policy.is_none(),
        "preview does not run host-policy probes; select and verify isolation during prepare"
    );
    let store = OffdeskShiftStore::read_only(profile)?;
    let (manifest, mut sources, roots) = build_shift_manifest(profile, args, None, &store)?;
    let queue_tasks: Vec<OffdeskTask> = if args.task_ids.is_empty() {
        Vec::new()
    } else {
        sources.read_task_queue(&sources.root_path().join("offdesk_tasks.json"), false)?
    };
    let queue_conflicts = crate::offdesk::workdir_conflicts::inspect_with_runtime(
        sources.root_path(),
        &queue_tasks,
        &args.task_ids,
    )?;
    ensure_boundaries_current(&sources, &roots)?;
    let tasks = manifest.task_bindings.iter().map(|binding| {
        let original = binding.ondesk_source_snapshot.as_ref()
            .map(|snapshot| snapshot.task(&binding.project_key, &binding.task_id)).transpose()?;
        Ok(serde_json::json!({
            "task_id": binding.task_id,
            "project_key": binding.project_key,
            "runner_kind": binding.runner_kind,
            "launch_spec_sha256": binding.launch_spec_sha256,
            "source_sha256": binding.ondesk_source_snapshot.as_ref().map(|snapshot| &snapshot.source_sha256),
            "goal": original.as_ref().map(|task| &task.goal),
            "completion_criteria": original.as_ref().map(|task| &task.completion_criteria),
            "dependencies": original.as_ref().map(|task| &task.depends_on),
            "dependency_status": if original.is_some() { "all_source_dependencies_accepted" } else { "unbound_not_verified" },
            "selection_status": "passed_current_prepare_checks"
        }))
    }).collect::<Result<Vec<Value>>>()?;
    let capabilities =
        crate::offdesk::workflow::preparation_capabilities::assess(&manifest, &queue_tasks)?;
    let execution_choices = crate::offdesk::workflow::task_execution_review::report_with_workers(
        &manifest,
        &queue_tasks,
        capabilities["worker_invocations"]
            .as_array()
            .context("worker invocation views missing")?,
    )?;
    Ok(serde_json::json!({
        "schema":"offdesk_shift_start_preview.v1",
        "profile":manifest.profile,
        "plan_id":manifest.plan_id,
        "plan_review_id":manifest.plan_review_id,
        "window":manifest.window,
        "task_count":manifest.task_bindings.len(),
        "tasks":tasks,
        "preparation":preparation_draft(&manifest, &queue_tasks),
        "preparation_review_binding":manifest.preparation_review,
        "task_execution_choices":execution_choices,
        "preparation_capabilities":capabilities,
        "queue_conflicts":queue_conflicts,
        "routines":manifest.routine_bindings,
        "budget":manifest.budget,
        "routine_resource_budget":manifest.routine_resource_budget,
        "weekly_usage":crate::offdesk::weekly_usage::projection(sources.root_path(), manifest.weekly_usage.as_ref(), Utc::now()),
        "execution_order":if manifest.serial_selection.is_some() {serde_json::json!(args.task_ids)} else {serde_json::json!("not_inferred_from_selection_order")},
        "serial_selection":manifest.serial_selection,
        "ordering_enforcement":if manifest.serial_selection.is_some() {"serial_preflight_and_reservation_when_armed"} else {"unspecified"},
        "estimated_wall_time":"unavailable",
        "task_cost_ceiling":"not_established_by_routine_budget",
        "stop_policy":"stop_new_dispatch_not_process_shutdown",
        "next_steps":["prepare_exact_selection","review_manifest_and_explicitly_arm"],
        "does_not_authorize":["plan_write","enqueue","approval","arm","launch","session_input"],
        "limitations":["explicit queued task selection only; no automatic TODO import", "all selected tasks must pass current gates", "prepare revalidates inputs; this preview is not an approval artifact"]
    }))
}

fn build_shift_manifest(
    profile: &str,
    args: &ShiftPrepareArgs,
    template_instantiation: Option<OffdeskShiftTemplateInstantiationV1>,
    store: &OffdeskShiftStore,
) -> Result<(
    OffdeskShiftManifestV1,
    ProfileSourceBoundary,
    Vec<ProjectRootBoundary>,
)> {
    let serial = selection::bind(profile, args)?;
    let (mut manifest, mut sources, roots) =
        build_shift_manifest_unbound(profile, args, template_instantiation, store, true)?;
    manifest.serial_selection = serial;
    manifest.validate()?;
    selection::ensure_serial_inputs(&manifest, &mut sources, true)?;
    ensure_boundaries_current(&sources, &roots)?;
    Ok((manifest, sources, roots))
}

fn build_shift_manifest_unbound(
    profile: &str,
    args: &ShiftPrepareArgs,
    template_instantiation: Option<OffdeskShiftTemplateInstantiationV1>,
    store: &OffdeskShiftStore,
    verify_isolation: bool,
) -> Result<(
    OffdeskShiftManifestV1,
    ProfileSourceBoundary,
    Vec<ProjectRootBoundary>,
)> {
    let now = Utc::now();
    let profile = normalize_profile_name(profile)?;
    validate_prepare_args(args, now)?;
    let enterprise_policy = OffdeskShiftEnterprisePolicyV1::built_in(args.enterprise_policy.into());
    let profile_dir = crate::session::profile_dir_path(&profile)?;
    let existing_manifest = args
        .shift_id
        .as_deref()
        .map(|shift_id| store.record(shift_id))
        .transpose()?
        .flatten()
        .map(|record| record.manifest);
    let mut sources = ProfileSourceBoundary::open(profile_dir.clone())?;
    let plan = load_plan_authority(
        &profile,
        &mut sources,
        &args.plan_ref,
        args.review_id.as_deref(),
    )?;
    let tasks = load_selected_tasks(&mut sources, &args.task_ids, false)?;
    validate_plan_task_correlation(&plan.registration, &tasks)?;

    let mut project_roots = tasks
        .iter()
        .map(|task| ProjectRootBoundary::open(Path::new(&task.workdir)))
        .collect::<Result<Vec<_>>>()?;
    let task_bindings = tasks
        .iter()
        .zip(&project_roots)
        .map(|(task, root)| OffdeskShiftTaskBindingV1::from_task(task, &root.identity_sha256, now))
        .collect::<Result<Vec<_>>>()?;
    let host_policy_attestation = match args.host_policy.map(Into::into) {
        Some(provider) => match existing_manifest
            .as_ref()
            .and_then(|manifest| manifest.host_policy_attestation.as_ref())
            .filter(|attestation| attestation.provider == provider)
        {
            Some(attestation) => {
                crate::offdesk::reverify_offdesk_shift_host_policy_attestation(attestation, now)?;
                Some(attestation.clone())
            }
            None => {
                Some(crate::offdesk::require_offdesk_shift_host_policy_attestation(provider, now)?)
            }
        },
        None => None,
    };
    if let Some(attestation) = &host_policy_attestation {
        ensure!(
            !task_bindings.is_empty()
                && task_bindings
                    .iter()
                    .all(|binding| attestation.allowed_runners.contains(&binding.runner_kind)),
            "selected host policy requires every bound task to use local-background"
        );
    }

    let read_policy = args.read_policy.map(|kind| match kind {
        super::ShiftReadPolicyArg::LinuxLandlockWorkdirAndRuntimeOnly => {
            crate::offdesk::read_policy::ReadPolicy::canonical()
        }
        super::ShiftReadPolicyArg::LinuxLandlockMetadataBootIdentity => {
            crate::offdesk::read_policy::ReadPolicy::metadata()
        }
    });
    if let Some(policy) = &read_policy {
        ensure!(
            host_policy_attestation.is_none(),
            "read policy cannot be combined with host policy"
        );
        crate::offdesk::read_policy::validate_runners(
            policy,
            task_bindings.iter().map(|b| b.runner_kind),
        )?;
        if verify_isolation {
            for task in &tasks {
                crate::offdesk::read_policy::validate_workdir(policy, Path::new(&task.workdir))?;
            }
            crate::offdesk::read_policy::verify(policy)?;
        }
    }
    let window = OffdeskShiftWindowV1 {
        starts_at: args.starts_at,
        stops_at: args.stops_at,
        timezone: args.timezone.trim().to_string(),
    };
    let mut routine_bindings = Vec::new();
    if let Some(every_minutes) = args.dispatch_interval_minutes {
        ensure!(
            !tasks.is_empty(),
            "dispatch tick routine requires at least one bound task"
        );
        routine_bindings.push(OffdeskShiftRoutineBindingV1 {
            routine_id: "dispatch_tick".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::DispatchTick,
            schedule: OffdeskShiftRoutineScheduleV1::Interval {
                starts_at: args.starts_at,
                stops_at: args.stops_at,
                every_minutes,
            },
            project_key: None,
            wiki_profile: None,
            project_root_identity_sha256: None,
            project_root_path: None,
        });
    }

    if let Some(wiki) = wiki_args(args)? {
        let root = ProjectRootBoundary::open(wiki.root)?;
        validate_wiki_registry_target(wiki.project_key, wiki.wiki_profile, root.path())?;
        routine_bindings.push(OffdeskShiftRoutineBindingV1 {
            routine_id: "wiki_candidate_distillation".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
            schedule: OffdeskShiftRoutineScheduleV1::Once { at: wiki.at },
            project_key: Some(wiki.project_key.to_string()),
            wiki_profile: Some(wiki.wiki_profile.to_string()),
            project_root_identity_sha256: Some(root.identity_sha256.clone()),
            project_root_path: Some(root.path().display().to_string()),
        });
        project_roots.push(root);
    }
    if let Some(at) = args.morning_review_at {
        routine_bindings.push(OffdeskShiftRoutineBindingV1 {
            routine_id: "morning_review".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::MorningReviewBrief,
            schedule: OffdeskShiftRoutineScheduleV1::Once { at },
            project_key: None,
            wiki_profile: None,
            project_root_identity_sha256: None,
            project_root_path: None,
        });
    }

    let task_count = task_bindings.len();
    let max_concurrent_tasks = match (task_count, args.max_concurrent_tasks) {
        (0, None | Some(0)) => 0,
        (0, Some(_)) => bail!("max_concurrent_tasks must be zero without bound tasks"),
        (_, Some(value)) => value,
        (_, None) => 1,
    };
    let max_routine_runs = scheduled_routine_runs(&routine_bindings)?;
    let routine_resource_budget = if routine_bindings.is_empty() {
        None
    } else {
        let additional_child_processes = routine_bindings
            .iter()
            .filter(|routine| {
                routine.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
            })
            .count();
        let task_processes = if routine_bindings
            .iter()
            .any(|routine| routine.routine_kind == OffdeskShiftRoutineKindV1::DispatchTick)
        {
            u64::try_from(task_count)?
        } else {
            0
        };
        let default_process_starts = u64::from(max_routine_runs)
            .checked_add(u64::try_from(additional_child_processes)?)
            .and_then(|value| value.checked_add(task_processes))
            .context("routine process budget overflow")?;
        let external_routines = routine_bindings
            .iter()
            .filter(|routine| {
                routine.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
                    || (routine.routine_kind == OffdeskShiftRoutineKindV1::MorningReviewBrief
                        && enterprise_policy.notification_route
                            == crate::offdesk::OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate)
            })
            .count();
        let has_wiki = routine_bindings.iter().any(|routine| {
            routine.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
        });
        Some(OffdeskShiftRoutineResourceBudgetV1 {
            max_process_starts: args
                .max_routine_process_starts
                .unwrap_or(default_process_starts),
            max_external_calls: args
                .max_routine_external_calls
                .unwrap_or(u64::try_from(external_routines)?),
            max_disk_bytes_written: args.max_routine_disk_bytes,
            max_llm_tokens: args.max_routine_llm_tokens.unwrap_or(if has_wiki {
                16_384
            } else {
                0
            }),
            max_cost_micro_usd: args.max_routine_cost_micro_usd,
            llm_cost_rate_micro_usd_per_million_tokens: args
                .routine_llm_cost_rate_micro_usd_per_million_tokens,
        })
    };
    let mut manifest = OffdeskShiftManifestV1 {
        serial_selection: None,
        preparation_review: None,
        preparation_requirements: if args.required_preparation_controls.is_empty() {
            None
        } else {
            Some(
                crate::offdesk::workflow::preparation_capabilities::PreparationRequirementsV1::new(
                    args.required_preparation_controls.clone(),
                )?,
            )
        },
        weekly_usage: args
            .weekly_usage_state
            .as_ref()
            .map(|state| {
                let samples = [
                    ("gpt", args.weekly_gpt_sample.clone()),
                    ("claude", args.weekly_claude_sample.clone()),
                ]
                .into_iter()
                .filter_map(|(provider, path)| path.map(|path| (provider.to_string(), path)))
                .collect();
                crate::offdesk::weekly_usage::bind(
                    &profile_dir,
                    state,
                    samples,
                    args.weekly_max_age_seconds,
                    args.stops_at,
                    now,
                )
            })
            .transpose()?,
        schema: OFFDESK_SHIFT_MANIFEST_SCHEMA.to_string(),
        shift_id: args
            .shift_id
            .clone()
            .unwrap_or_else(|| format!("shift_{}", &Uuid::new_v4().to_string()[..12])),
        profile: profile.clone(),
        created_at: now,
        window,
        plan_id: plan.plan_id,
        plan_review_id: plan.review.review_id,
        plan_source_sha256: plan.registration.source_sha256,
        plan_review_sha256: plan.review_sha256,
        task_bindings,
        routine_bindings,
        budget: OffdeskShiftBudgetV1 {
            max_concurrent_tasks,
            max_task_launches: u16::try_from(task_count).context("shift task count exceeds u16")?,
            max_attempts_per_task: if task_count == 0 {
                0
            } else {
                args.max_attempts_per_task
            },
            max_routine_runs,
        },
        enterprise_policy: Some(enterprise_policy),
        host_policy_attestation,
        read_policy,
        template_instantiation,
        routine_resource_budget,
        authority: OffdeskShiftAuthorityV1 {
            task_dispatch_requires_fresh_approval: true,
            task_launch_spec_is_immutable: true,
            wiki_writes_are_candidate_only: true,
            plan_revision_requires_review: true,
            acceptance_requires_ondesk_review: true,
            does_not_authorize: OFFDESK_SHIFT_REQUIRED_DENIALS
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
        },
    };
    manifest.preparation_review =
        crate::offdesk::workflow::offdesk_preparation::review_binding(&manifest, &tasks)?;
    manifest.validate()?;
    if let Some(binding) = &manifest.weekly_usage {
        sources.read_bytes(&profile_dir.join(&binding.state_file))?;
        for name in binding.sample_files.values() {
            sources.read_bytes(&profile_dir.join(name))?;
        }
        let current =
            crate::offdesk::weekly_usage::check(&profile_dir, binding, Utc::now(), false)?;
        ensure!(
            current.verdict["allowed"] == true,
            "weekly usage changed during preparation"
        );
        current.ensure_current()?;
    }
    validate_manifest_tasks(&manifest, &tasks, &project_roots[..tasks.len()], now)?;
    crate::offdesk::workflow::worker_invocation::validate_selected(&manifest, &tasks, now)?;
    ensure_boundaries_current(&sources, &project_roots)?;

    Ok((manifest, sources, project_roots))
}

pub(super) fn arm_offdesk_shift(profile: &str, args: &ShiftArmArgs) -> Result<ShiftArmResult> {
    arm_offdesk_shift_bound(profile, args, None)
}

fn arm_offdesk_shift_bound(
    profile: &str,
    args: &ShiftArmArgs,
    expected_approval: Option<&crate::offdesk::PendingActionApproval>,
) -> Result<ShiftArmResult> {
    let _inputs = crate::offdesk::workflow::invocation_inputs::ReviewInputScope::begin();
    ensure!(
        (1..=1440).contains(&args.approval_ttl_minutes),
        "approval TTL must be between 1 and 1440 minutes"
    );
    let now = Utc::now();
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let record = select_prepared_shift(&store, args.shift_id.as_deref())?;
    let manifest = record.manifest.clone();
    manifest.enterprise_policy()?.validate()?;
    if let Some(attestation) = &manifest.host_policy_attestation {
        crate::offdesk::reverify_offdesk_shift_host_policy_attestation(attestation, now)?;
    }
    if let Some(policy) = &manifest.read_policy {
        crate::offdesk::read_policy::verify(policy)?;
    }
    ensure!(
        now <= manifest.window.stops_at,
        "cannot arm a shift after its stop time"
    );
    if manifest.serial_selection.is_some() && record.arm_receipt.is_none() {
        revalidate_manifest_authority(&profile, &manifest, now, false, true)?.ensure_current()?;
    }
    let profile_dir = get_profile_dir(&profile)?;
    let ledger = ApprovalLedger::new(&profile_dir);
    let (mut session, _) = ledger.begin_bounded_session(now)?;
    session.flush()?;
    let profile_identity = session
        .bounded_root_identity()
        .context("shift arm approval session lacks profile root identity")?;
    store.ensure_profile_root_identity(profile_identity)?;

    let selected = select_or_create_arm_approval(&mut session, &manifest, args, now)?;
    let Some(approval) = selected else {
        session.ensure_bounded_root_active()?;
        session.flush()?;
        let pending = find_exact_pending_approval(&session, &manifest, now)?
            .context("shift arm approval was not created")?;
        return Ok(ShiftArmResult::Pending(pending_approval_operator_views(
            vec![pending],
            now,
        )));
    };

    if let Some(expected) = expected_approval {
        ensure!(&approval == expected, "approved arm request changed after local confirmation; inspect shift state before retry");
    }
    validate_arm_approval(&approval, &manifest, now)?;
    match approval.status {
        ApprovalStatus::Pending => {
            session.flush()?;
            Ok(ShiftArmResult::Pending(pending_approval_operator_views(
                vec![approval],
                now,
            )))
        }
        ApprovalStatus::Approved => {
            let live = revalidate_manifest_authority(
                &profile,
                &manifest,
                now,
                false,
                record.arm_receipt.is_none(),
            )?;
            session.ensure_bounded_root_active()?;
            store.ensure_profile_root_identity(profile_identity)?;
            let receipt = arm_receipt(&manifest, &approval)?;
            let initial_state = initial_armed_state(&manifest, &receipt)?;
            let transition_id = format!("shift_arm_{}", approval.approval_id);
            let created = store.arm_shift(receipt, initial_state, &transition_id)?;
            live.ensure_current().with_context(|| {
                format!(
                    "shift {} has a stored arm receipt, but input revalidation failed; inspect shift state before explicit recovery; disarm to stop authority",
                    manifest.shift_id
                )
            })?;
            session.ensure_bounded_root_active()?;
            session
                .supersede_approval(&approval.approval_id, "consumed_by_offdesk_shift_arm")
                .context("approved shift arm approval disappeared")?;
            session.flush()?;
            store.ensure_profile_root_identity(profile_identity)?;
            let record = store
                .record(&manifest.shift_id)?
                .context("armed shift was not durably readable")?;
            Ok(ShiftArmResult::Armed {
                record: Box::new(record),
                created,
            })
        }
        ApprovalStatus::Superseded => {
            let record = store
                .record(&manifest.shift_id)?
                .context("consumed arm approval has no shift record")?;
            ensure!(
                record
                    .arm_receipt
                    .as_ref()
                    .is_some_and(|receipt| receipt.approval_id == approval.approval_id),
                "consumed arm approval is not joined to the exact shift receipt"
            );
            Ok(ShiftArmResult::Armed {
                record: Box::new(record),
                created: false,
            })
        }
        ApprovalStatus::Denied => bail!("shift arm approval was denied"),
        ApprovalStatus::Expired => bail!("shift arm approval expired before consumption"),
    }
}

pub(super) fn disarm_offdesk_shift(
    profile: &str,
    args: &ShiftDisarmArgs,
) -> Result<ShiftDisarmResult> {
    let profile = normalize_profile_name(profile)?;
    let store = OffdeskShiftStore::writable(&profile)?;
    let record = if let Some(shift_id) = args.shift_id.as_deref() {
        store
            .record(shift_id)?
            .with_context(|| format!("bounded Offdesk shift not found: {shift_id}"))?
    } else {
        store
            .snapshot()?
            .active()
            .cloned()
            .context("no active bounded Offdesk shift exists")?
    };
    let state = record
        .state
        .as_ref()
        .context("prepared shift has no active authority to disarm")?;
    if state.status.is_terminal() {
        return Ok(ShiftDisarmResult {
            record,
            changed: false,
        });
    }
    let now = Utc::now().max(state.updated_at);
    let next = OffdeskShiftStateV1 {
        schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
        shift_id: state.shift_id.clone(),
        manifest_sha256: state.manifest_sha256.clone(),
        arm_receipt_sha256: state.arm_receipt_sha256.clone(),
        sequence: state.sequence + 1,
        status: OffdeskShiftStatusV1::Disarmed,
        updated_at: now,
        reason: crate::offdesk::operator_safe_text(args.reason.trim()),
        acceptance_receipt_id: None,
        acceptance_receipt_sha256: None,
    };
    ensure!(!next.reason.is_empty(), "shift disarm reason is required");
    let transition_id = format!("shift_disarm_{}", Uuid::new_v4());
    store.transition(next, &transition_id)?;
    let record = store
        .record(&record.manifest.shift_id)?
        .context("disarmed shift was not durably readable")?;
    Ok(ShiftDisarmResult {
        record,
        changed: true,
    })
}

struct PlanAuthority {
    plan_id: String,
    registration: OffdeskPlanRegistration,
    review: OffdeskPlanReviewRecord,
    review_sha256: String,
    source_value: Value,
}

fn load_plan_authority(
    profile: &str,
    sources: &mut ProfileSourceBoundary,
    plan_ref: &str,
    review_id: Option<&str>,
) -> Result<PlanAuthority> {
    let item = resolve_offdesk_plan_item(profile, plan_ref)?;
    ensure!(
        item.registration.schema == OFFDESK_PLAN_REGISTRATION_SCHEMA,
        "shift plan registration schema is invalid"
    );
    ensure!(
        item.registration.forager_profile == profile,
        "shift plan registration belongs to a different profile"
    );
    let registration: OffdeskPlanRegistration =
        sources.read_json(Path::new(&item.registration_path))?;
    ensure_json_equal(
        &registration,
        &item.registration,
        "plan registration changed",
    )?;
    let copied_source = item
        .registration
        .artifacts
        .copied_source_json
        .as_deref()
        .context("shift plan registration has no durable source copy")?;
    let source_bytes = sources.read_bytes(Path::new(copied_source))?;
    ensure!(
        sha256_hex(&source_bytes) == item.registration.source_sha256,
        "shift plan source bytes do not match registration"
    );
    let source_value = serde_json::from_slice(&source_bytes).context("parse shift plan source")?;
    crate::offdesk::validate_offdesk_plan_input(&source_value)?;

    let registry_dir = offdesk_plan_registry_dir(&item)?;
    let reviews = load_offdesk_plan_reviews(&registry_dir)?;
    let review = crate::offdesk::select_offdesk_plan_review(&reviews, review_id)?.clone();
    crate::offdesk::validate_offdesk_plan_launch_prep(&review, &item.registration.source_sha256)?;
    ensure!(
        review.schema == OFFDESK_PLAN_REVIEW_SCHEMA
            && review.plan_id == item.plan_id
            && review.forager_profile == profile
            && review.registration_path == item.registration_path,
        "shift plan review identity does not match its registration"
    );
    let review_bytes = sources.read_bytes(Path::new(&review.artifacts.review_record_json))?;
    let stored_review: OffdeskPlanReviewRecord =
        serde_json::from_slice(&review_bytes).context("parse shift plan review")?;
    ensure_json_equal(&stored_review, &review, "shift plan review changed")?;

    Ok(PlanAuthority {
        plan_id: item.plan_id,
        registration: item.registration,
        review,
        review_sha256: sha256_hex(&review_bytes),
        source_value,
    })
}

fn validate_prepare_args(args: &ShiftPrepareArgs, now: DateTime<Utc>) -> Result<()> {
    validate_prepare_window(args, now)?;
    ensure!(
        args.weekly_usage_state.is_some() || args.weekly_max_age_seconds == 300,
        "weekly freshness override requires a selected weekly usage state"
    );
    let unique = args.task_ids.iter().collect::<HashSet<_>>();
    ensure!(
        unique.len() == args.task_ids.len(),
        "shift task IDs must be unique"
    );
    ensure!(
        !args.task_ids.is_empty()
            || args.dispatch_interval_minutes.is_some()
            || args.wiki_at.is_some()
            || args.morning_review_at.is_some(),
        "shift preparation requires at least one task or routine"
    );
    Ok(())
}

fn validate_prepare_window(args: &ShiftPrepareArgs, now: DateTime<Utc>) -> Result<()> {
    ensure!(args.stops_at > now, "shift stop time must be in the future");
    ensure!(
        args.stops_at > args.starts_at,
        "shift stop time must be after start time"
    );
    ensure!(
        args.stops_at - args.starts_at <= Duration::hours(24),
        "shift duration must not exceed 24 hours"
    );
    Ok(())
}

struct WikiRoutineArgs<'a> {
    project_key: &'a str,
    wiki_profile: &'a str,
    root: &'a Path,
    at: DateTime<Utc>,
}

fn wiki_args(args: &ShiftPrepareArgs) -> Result<Option<WikiRoutineArgs<'_>>> {
    match (
        args.wiki_project_key.as_deref(),
        args.wiki_profile.as_deref(),
        args.wiki_root.as_deref(),
        args.wiki_at,
    ) {
        (None, None, None, None) => Ok(None),
        (Some(project_key), Some(wiki_profile), Some(root), Some(at)) => Ok(Some(
            WikiRoutineArgs {
                project_key,
                wiki_profile,
                root,
                at,
            },
        )),
        _ => bail!(
            "wiki routine requires --wiki-project-key, --wiki-profile, --wiki-root, and --wiki-at together"
        ),
    }
}

fn scheduled_routine_runs(routines: &[OffdeskShiftRoutineBindingV1]) -> Result<u16> {
    let total = routines.iter().try_fold(0u32, |total, routine| {
        let runs = match routine.schedule {
            OffdeskShiftRoutineScheduleV1::Once { .. } => 1,
            OffdeskShiftRoutineScheduleV1::Interval {
                starts_at,
                stops_at,
                every_minutes,
            } => {
                ensure!(every_minutes > 0, "routine interval must be positive");
                u32::try_from((stops_at - starts_at).num_minutes())? / u32::from(every_minutes) + 1
            }
        };
        total
            .checked_add(runs)
            .context("routine run count overflow")
    })?;
    u16::try_from(total).context("routine run count exceeds its budget")
}

fn load_selected_tasks(
    sources: &mut ProfileSourceBoundary,
    task_ids: &[String],
    mutable_source: bool,
) -> Result<Vec<OffdeskTask>> {
    if task_ids.is_empty() {
        return Ok(Vec::new());
    }
    let path = sources.root_path().join("offdesk_tasks.json");
    let tasks = sources.read_task_queue(&path, mutable_source)?;
    task_ids
        .iter()
        .map(|task_id| {
            tasks
                .iter()
                .find(|task| task.task_id == *task_id)
                .cloned()
                .with_context(|| format!("bound Offdesk task not found: {task_id}"))
        })
        .collect()
}

fn validate_plan_task_correlation(
    registration: &OffdeskPlanRegistration,
    tasks: &[OffdeskTask],
) -> Result<()> {
    if let Some(task_id) = registration.task_id.as_deref() {
        ensure!(
            tasks.len() == 1 && tasks[0].task_id == task_id,
            "plan registration task_id must exactly match the bound task set"
        );
    }
    for task in tasks {
        if let Some(project_key) = registration.project_key.as_deref() {
            ensure!(
                task.project_key == project_key,
                "bound task project_key does not match the reviewed plan"
            );
        }
        if let Some(request_id) = registration.request_id.as_deref() {
            ensure!(
                task.request_id == request_id,
                "bound task request_id does not match the reviewed plan"
            );
        }
    }
    Ok(())
}

fn validate_manifest_tasks(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
    roots: &[ProjectRootBoundary],
    now: DateTime<Utc>,
) -> Result<()> {
    let observations = tasks
        .iter()
        .zip(roots)
        .map(|(task, root)| OffdeskShiftTaskObservation {
            task,
            project_root_identity_sha256: &root.identity_sha256,
        })
        .collect::<Vec<_>>();
    manifest.validate_against_tasks(&observations, now)
}

fn validate_manifest_task_identities(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
    roots: &[ProjectRootBoundary],
) -> Result<()> {
    ensure!(
        tasks.len() == manifest.task_bindings.len() && tasks.len() == roots.len(),
        "current task set must exactly match shift bindings"
    );
    for binding in &manifest.task_bindings {
        let (task, root) = tasks
            .iter()
            .zip(roots)
            .find(|(task, _)| task.task_id == binding.task_id)
            .context("bound shift task is missing")?;
        binding.validate_identity_against_task(task, &root.identity_sha256)?;
    }
    Ok(())
}

fn validate_wiki_registry_target(project_key: &str, wiki_profile: &str, root: &Path) -> Result<()> {
    let registry = load_registry();
    let entry = resolve_unique_project_for_path(root, &registry)?
        .context("wiki routine root is not uniquely registered")?;
    ensure!(
        entry.key == project_key && entry.wiki_profile.as_deref() == Some(wiki_profile),
        "wiki routine target does not match the project registry"
    );
    Ok(())
}

fn revalidate_manifest_authority(
    profile: &str,
    manifest: &OffdeskShiftManifestV1,
    now: DateTime<Utc>,
    mutable_task_source: bool,
    initial_serial_confirmation: bool,
) -> Result<OffdeskShiftLiveSources> {
    let profile_dir = crate::session::profile_dir_path(profile)?;
    let mut sources = ProfileSourceBoundary::open(profile_dir.clone())?;
    if !mutable_task_source {
        if let Some(binding) = &manifest.weekly_usage {
            let checked = crate::offdesk::weekly_usage::check(&profile_dir, binding, now, false)?;
            ensure!(checked.verdict["allowed"] == true, "weekly usage is held");
            sources.read_bytes(&profile_dir.join(&binding.state_file))?;
            for name in binding.sample_files.values() {
                sources.read_bytes(&profile_dir.join(name))?;
            }
            checked.ensure_current()?;
        }
    }
    let plan = load_plan_authority(
        profile,
        &mut sources,
        &manifest.plan_id,
        Some(&manifest.plan_review_id),
    )?;
    ensure!(
        plan.registration.source_sha256 == manifest.plan_source_sha256
            && plan.review_sha256 == manifest.plan_review_sha256,
        "reviewed plan authority changed after shift preparation"
    );
    let task_ids = manifest
        .task_bindings
        .iter()
        .map(|binding| binding.task_id.clone())
        .collect::<Vec<_>>();
    let tasks = load_selected_tasks(&mut sources, &task_ids, mutable_task_source)?;
    validate_plan_task_correlation(&plan.registration, &tasks)?;
    let mut roots = tasks
        .iter()
        .map(|task| ProjectRootBoundary::open(Path::new(&task.workdir)))
        .collect::<Result<Vec<_>>>()?;
    if mutable_task_source {
        validate_manifest_task_identities(manifest, &tasks, &roots)?;
    } else {
        validate_manifest_tasks(manifest, &tasks, &roots, now)?;
    }
    crate::offdesk::workflow::offdesk_preparation::validate_review_binding(manifest, &tasks)?;
    crate::offdesk::workflow::preparation_capabilities::validate_launch(manifest, &tasks)?;

    for routine in &manifest.routine_bindings {
        if routine.routine_kind != OffdeskShiftRoutineKindV1::WikiCandidateDistillation {
            continue;
        }
        let path = routine
            .project_root_path
            .as_deref()
            .context("wiki routine has no bound project root path")?;
        let root = ProjectRootBoundary::open(Path::new(path))?;
        let project_key = routine
            .project_key
            .as_deref()
            .context("wiki project key missing")?;
        let wiki_profile = routine
            .wiki_profile
            .as_deref()
            .context("wiki profile missing")?;
        validate_wiki_registry_target(project_key, wiki_profile, root.path())?;
        routine.validate_against_project(project_key, wiki_profile, &root.identity_sha256)?;
        roots.push(root);
    }
    selection::ensure_serial_inputs(manifest, &mut sources, initial_serial_confirmation)?;
    ensure_boundaries_current(&sources, &roots)?;
    Ok(OffdeskShiftLiveSources::new(sources, roots))
}

fn select_prepared_shift(
    store: &OffdeskShiftStore,
    shift_id: Option<&str>,
) -> Result<OffdeskShiftRecordV1> {
    if let Some(shift_id) = shift_id {
        return store
            .record(shift_id)?
            .with_context(|| format!("bounded Offdesk shift not found: {shift_id}"));
    }
    store
        .snapshot()?
        .records
        .into_iter()
        .rev()
        .find(|record| record.state.is_none())
        .context("no prepared bounded Offdesk shift exists")
}

fn select_or_create_arm_approval(
    session: &mut crate::offdesk::ApprovalLedgerSession,
    manifest: &OffdeskShiftManifestV1,
    args: &ShiftArmArgs,
    now: DateTime<Utc>,
) -> Result<Option<crate::offdesk::PendingActionApproval>> {
    if let Some(approval_id) = args.approval_id.as_deref() {
        return session
            .approval(approval_id)
            .cloned()
            .with_context(|| format!("shift arm approval not found: {approval_id}"))
            .map(Some);
    }

    let matching = session
        .approvals()
        .iter()
        .filter(|approval| arm_approval_identity_matches(approval, manifest))
        .cloned()
        .collect::<Vec<_>>();
    for approval in &matching {
        validate_arm_approval_shape(approval, manifest)?;
    }
    let active = matching
        .iter()
        .filter(|approval| {
            matches!(
                approval.status,
                ApprovalStatus::Pending | ApprovalStatus::Approved
            ) && approval.expires_at >= now
        })
        .collect::<Vec<_>>();
    ensure!(
        active.len() <= 1,
        "multiple active approvals match one shift manifest"
    );
    if let Some(approval) = active.first() {
        return Ok(Some((*approval).clone()));
    }
    if let Some(approval) = matching
        .iter()
        .find(|approval| approval.status == ApprovalStatus::Superseded)
    {
        return Ok(Some(approval.clone()));
    }
    ensure!(
        !matching
            .iter()
            .any(|approval| approval.status == ApprovalStatus::Denied),
        "shift arm approval was denied; prepare a new manifest for another review"
    );

    let request = arm_approval_request(manifest, now, args.approval_ttl_minutes)?;
    let pending = session.ensure_pending_without_consuming_grant(request, now)?;
    session.flush()?;
    Ok(pending)
}

fn find_exact_pending_approval(
    session: &crate::offdesk::ApprovalLedgerSession,
    manifest: &OffdeskShiftManifestV1,
    now: DateTime<Utc>,
) -> Result<Option<crate::offdesk::PendingActionApproval>> {
    let matches = session
        .approvals()
        .iter()
        .filter(|approval| {
            arm_approval_identity_matches(approval, manifest)
                && approval.status == ApprovalStatus::Pending
                && approval.expires_at >= now
        })
        .cloned()
        .collect::<Vec<_>>();
    ensure!(matches.len() <= 1, "multiple pending shift arm approvals");
    Ok(matches.into_iter().next())
}

fn arm_approval_request(
    manifest: &OffdeskShiftManifestV1,
    generated_at: DateTime<Utc>,
    ttl_minutes: i64,
) -> Result<ActionApprovalRequest> {
    let manifest_sha256 = offdesk_shift_manifest_sha256(manifest)?;
    let mut request = ActionApprovalRequest::new(
        manifest.plan_id.clone(),
        format!("shift_arm_{}", &manifest_sha256[..32]),
        manifest.shift_id.clone(),
        SHIFT_ARM_ACTION,
        RiskLevel::CanonicalMutation,
    );
    request.mutation_class = Some(SHIFT_ARM_ACTION.to_string());
    request.preview = format!(
        "Arm bounded shift manifest sha256={} for {} tasks and {} routines",
        manifest_sha256,
        manifest.task_bindings.len(),
        manifest.routine_bindings.len()
    );
    if let Some(serial) = &manifest.serial_selection {
        request.preview.push_str(&format!(
            "; serial proposal {} order: {}",
            serial.proposal_sha256,
            manifest
                .task_bindings
                .iter()
                .map(|task| task.task_id.as_str())
                .collect::<Vec<_>>()
                .join(" -> ")
        ));
    }
    request.reason = "Enable only the exact reviewed shift envelope; every task dispatch still requires fresh approval".to_string();
    request.source_surface = SHIFT_ARM_SOURCE.to_string();
    request.ttl = Duration::minutes(ttl_minutes);
    request.metadata = Some(ActionApprovalMetadata::OffdeskShiftArm(Box::new(
        arm_approval_metadata(manifest, generated_at)?,
    )));
    Ok(request)
}

fn arm_approval_metadata(
    manifest: &OffdeskShiftManifestV1,
    generated_at: DateTime<Utc>,
) -> Result<OffdeskShiftArmApprovalMetadataV1> {
    let manifest_sha256 = offdesk_shift_manifest_sha256(manifest)?;
    let enterprise_policy = manifest.enterprise_policy()?;
    let mut context = HashMap::new();
    context.insert("manifest_sha256".to_string(), manifest_sha256.clone());
    context.insert("plan_id".to_string(), manifest.plan_id.clone());
    context.insert(
        "plan_review_id".to_string(),
        manifest.plan_review_id.clone(),
    );
    context.insert(
        "enterprise_policy_key".to_string(),
        enterprise_policy.profile_key.as_str().to_string(),
    );
    context.insert(
        "operator_mode".to_string(),
        enterprise_policy.operator_mode.as_str().to_string(),
    );
    context.insert(
        "notification_route".to_string(),
        enterprise_policy.notification_route.as_str().to_string(),
    );
    if let Some(attestation) = &manifest.host_policy_attestation {
        context.insert(
            "host_policy_provider".to_string(),
            attestation.provider.as_str().to_string(),
        );
        context.insert(
            "host_policy_attestation_sha256".to_string(),
            attestation.sha256()?,
        );
    }
    if let Some(policy) = &manifest.read_policy {
        context.insert("read_policy_provider".into(), policy.provider.clone());
        context.insert("read_policy_sha256".into(), policy.sha256()?);
    }
    let mut summary_lines = vec![
        format!("Manifest: {manifest_sha256}"),
        format!(
            "Window: {} through {}",
            manifest.window.starts_at, manifest.window.stops_at
        ),
        format!(
            "Scope: {} exact tasks and {} fixed routines",
            manifest.task_bindings.len(),
            manifest.routine_bindings.len()
        ),
        format!(
            "Enterprise policy: {}",
            enterprise_policy.profile_key.as_str()
        ),
    ];
    if let Some(weekly) = &manifest.weekly_usage {
        let scope = if weekly.contract["nightly"].is_object() {
            "Nightly allowance from nightly-start weekly-meter baseline"
        } else {
            "Run allowance from approved weekly-meter baseline"
        };
        summary_lines.push(format!("{}: {} percentage points per bound provider; fresh samples <= {}s; stop new dispatch, in-flight work can overshoot; other sessions included. No automatic polling or USD/token cap.", scope, weekly.contract["limit_percentage_points"], weekly.max_age_seconds));
    }
    let mut evidence = vec![
        format!("plan_source_sha256={}", manifest.plan_source_sha256),
        format!("plan_review_sha256={}", manifest.plan_review_sha256),
        "child_task_network_boundary=host_policy_required".to_string(),
        "forager_controls_child_task_network=false".to_string(),
    ];
    if let Some(requirements) = &manifest.preparation_requirements {
        let root = crate::session::profile_dir_path(&manifest.profile)?;
        let mut sources = ProfileSourceBoundary::open(root.clone())?;
        let tasks = if manifest.task_bindings.is_empty() {
            Vec::new()
        } else {
            sources.read_task_queue(&root.join("offdesk_tasks.json"), false)?
        };
        let capabilities =
            crate::offdesk::workflow::preparation_capabilities::assess(manifest, &tasks)?;
        let choices = crate::offdesk::workflow::task_execution_review::report_with_workers(
            manifest,
            &tasks,
            capabilities["worker_invocations"]
                .as_array()
                .context("worker invocation views missing")?,
        )?;
        ensure!(
            choices["all_task_identities_match"] == true,
            "task execution choices do not match the manifest"
        );
        context.insert(
            "task_execution_choice_review_sha256".into(),
            choices["review_sha256"]
                .as_str()
                .context("execution choice review hash missing")?
                .to_string(),
        );
        context.insert(
            "worker_execution_contracts_sha256".into(),
            choices["worker_execution_contracts_sha256"]
                .as_str()
                .context("worker execution contract hash missing")?
                .to_string(),
        );
        summary_lines.push("Task execution choices are declarations; worker qualification and child policies remain unverified.".into());
        for task in choices["tasks"]
            .as_array()
            .context("execution choice task list missing")?
        {
            summary_lines.push(crate::offdesk::operator_safe_text(&format!(
                "Task {} / {}: provider={} model={} result={}",
                task["project_key"],
                task["task_id"],
                task["provider_id"],
                task["model"],
                task["result_artifact_path"]
            )));
            if let Some(contract) = task["worker_execution_contract"].as_object() {
                summary_lines.push(format!(
                    "Execution {}: deadline {}; command timeout {}s; worker starts 1; automatic retries 0; checkpoint unsupported; return needs Ondesk review. Model/task qualification is separate.",
                    task["task_id"],contract["deadline"],contract["command_timeout_seconds"]));
            }
        }
        for native in capabilities["worker_invocations"]
            .as_array()
            .into_iter()
            .flatten()
        {
            if native["entry_preflight"].is_object() {
                let policy = &native["entry_preflight"];
                summary_lines.push(format!("Entry preflight {}: at most {}s; minimum worker {}s; closeout reserve {}s; deadline {}. Tests run inside the approved task, never during review.",native["task_id"],policy["timeout_seconds"],policy["minimum_worker_seconds"],policy["closeout_reserve_seconds"],native["deadline"]));
                evidence.push(format!(
                    "native_invocation_sha256={};entry_preflight_policy={}",
                    native["invocation_sha256"], policy
                ));
            }
        }
        sources.ensure_current()?;
        context.insert(
            "preparation_required_controls".into(),
            requirements.required_controls.join(","),
        );
        context.insert(
            "preparation_capability_rules_sha256".into(),
            requirements.capability_rules_sha256.clone(),
        );
        summary_lines.push(format!(
            "Required preparation controls: {}",
            requirements.required_controls.join(", ")
        ));
        evidence.push(format!("preparation_capability_rules_sha256={};requirements_only_no_new_runtime_authority=true", requirements.capability_rules_sha256));
    }
    if let Some(binding) = &manifest.preparation_review {
        context.insert("preparation_review_schema".into(), binding.schema.clone());
        context.insert(
            "preparation_report_sha256".into(),
            binding.report_sha256.clone(),
        );
        context.insert(
            "preparation_decision_rules_sha256".into(),
            binding.decision_rules_sha256.clone(),
        );
        summary_lines.push(format!("Preparation advisory version: {}; runtime branch policy is not authorized by this binding",binding.report_sha256));
        evidence.push(format!("preparation_report_sha256={};decision_rules_sha256={};advisory_only=true;runtime_policy_authorized=false",binding.report_sha256,binding.decision_rules_sha256));
    }
    if let Some(serial) = &manifest.serial_selection {
        let order = manifest
            .task_bindings
            .iter()
            .map(|task| task.task_id.as_str())
            .collect::<Vec<_>>()
            .join(" -> ");
        context.insert(
            "serial_proposal_sha256".into(),
            serial.proposal_sha256.clone(),
        );
        context.insert("serial_order".into(), order.clone());
        summary_lines.push(format!(
            "Serial order: {order}; completed predecessor and verified runtime release required"
        ));
        evidence.push(format!("serial_proposal_sha256={}", serial.proposal_sha256));
    }
    if let Some(attestation) = &manifest.host_policy_attestation {
        summary_lines.push(format!(
            "Host policy: {} with live revalidation required",
            attestation.provider.as_str()
        ));
        evidence.extend([
            format!("host_policy_attestation_sha256={}", attestation.sha256()?),
            "forager_enforces_attested_launcher=true".to_string(),
            "external_ip_network_allowed=false".to_string(),
            "unix_domain_sockets_covered=false".to_string(),
        ]);
        if let Some(policy) = &attestation.filesystem_policy {
            evidence.extend([
                format!("filesystem_boundary={}", policy.boundary.as_str()),
                format!(
                    "host_filesystem_read_allowed={}",
                    policy.host_filesystem_read_allowed
                ),
                format!(
                    "host_filesystem_write_outside_working_directory_allowed={}",
                    policy.host_filesystem_write_outside_working_directory_allowed
                ),
                format!("private_tmp={}", policy.private_tmp),
                "hardlink_aliases_outside_working_directory_covered=false".to_string(),
                "nested_mounts_inside_working_directory_covered=false".to_string(),
                "device_and_kernel_interfaces_covered=false".to_string(),
            ]);
        }
    }
    if let Some(policy) = &manifest.read_policy {
        summary_lines.push(format!(
            "Filesystem read policy: {}; task directory and listed runtime paths only",
            policy.provider
        ));
        evidence.extend([
            format!("read_policy_sha256={}", policy.sha256()?),
            "outside_workdir_reads=denied_except_listed_runtime_paths".into(),
            "read_policy_network_isolated=false".into(),
            "read_policy_metadata_ipc_and_preopened_descriptors_covered=false".into(),
        ]);
    }
    if let Some(template) = &manifest.template_instantiation {
        context.insert("template_key".to_string(), template.template_key.clone());
        context.insert(
            "template_cadence".to_string(),
            template.cadence.as_str().to_string(),
        );
        context.insert("template_slot".to_string(), template.slot.clone());
        summary_lines.push(format!(
            "Template: {} {} slot {}",
            template.template_key,
            template.cadence.as_str(),
            template.slot
        ));
        evidence.push("template_recurrence_authorized=false".to_string());
        evidence.push("template_next_occurrence_requires_new_manifest=true".to_string());
    }
    Ok(OffdeskShiftArmApprovalMetadataV1 {
        schema: OFFDESK_SHIFT_ARM_APPROVAL_METADATA_SCHEMA.to_string(),
        generated_at,
        shift_id: manifest.shift_id.clone(),
        manifest_sha256: manifest_sha256.clone(),
        plan_id: manifest.plan_id.clone(),
        plan_review_id: manifest.plan_review_id.clone(),
        starts_at: manifest.window.starts_at,
        stops_at: manifest.window.stops_at,
        task_count: manifest.task_bindings.len(),
        routine_count: manifest.routine_bindings.len(),
        approval_brief: ApprovalBrief {
            schema: "approval_brief.v1".to_string(),
            recommendation: "approve".to_string(),
            subject: "bounded Offdesk shift".to_string(),
            summary_lines,
            judgment_route_summary: Some(
                "Operator review selects one immutable manifest; runtime task approvals remain separate."
                    .to_string(),
            ),
            evidence_sufficiency: Some(
                "The reviewed plan source, review bytes, task launch specs, execution briefs, and roots are hash-bound."
                    .to_string(),
            ),
            default_if_no_reply: Some("defer".to_string()),
            scope: "Arms only this manifest until its stop time. It does not approve task dispatch, command changes, Wiki promotion, destructive work, or accepted truth."
                .to_string(),
            question: "Arm this exact bounded Offdesk shift?".to_string(),
            options: vec![
                ApprovalBriefOption {
                    id: "approve".to_string(),
                    label: "Arm shift".to_string(),
                    description: "Allow the fixed shift envelope while preserving every inner approval gate."
                        .to_string(),
                    natural_input_prompt: None,
                },
                ApprovalBriefOption {
                    id: "deny".to_string(),
                    label: "Do not arm".to_string(),
                    description: "Keep the shift inert and require a newly reviewed manifest for changes."
                        .to_string(),
                    natural_input_prompt: Some("Explain what must change before arming.".to_string()),
                },
                ApprovalBriefOption {
                    id: "defer".to_string(),
                    label: "Review details".to_string(),
                    description: "Leave the approval pending while inspecting the manifest."
                        .to_string(),
                    natural_input_prompt: Some("State which task, routine, root, or budget needs review.".to_string()),
                },
            ],
            why_recommendation: vec![
                "The selected plan has an approved, byte-bound review.".to_string(),
                "The manifest cannot change after this approval is created.".to_string(),
                "Disarm remains an immediate safe terminal action.".to_string(),
            ],
            evidence,
            decision_impacts: HashMap::from([
                (
                    "approve".to_string(),
                    "Create one hash-bound arm receipt and initial armed state.".to_string(),
                ),
                (
                    "deny".to_string(),
                    "Keep this manifest inert.".to_string(),
                ),
                (
                    "defer".to_string(),
                    "Make no authority change.".to_string(),
                ),
            ]),
            reply_examples: vec!["approve".to_string(), "deny - revise the task set".to_string()],
            context,
            source: Some(SHIFT_ARM_SOURCE.to_string()),
        },
    })
}

fn arm_approval_identity_matches(
    approval: &crate::offdesk::PendingActionApproval,
    manifest: &OffdeskShiftManifestV1,
) -> bool {
    let Ok(request) = arm_approval_request(manifest, approval.created_at, 30) else {
        return false;
    };
    approval.project_key == request.project_key
        && approval.request_id == request.request_id
        && approval.task_id == request.task_id
        && approval.action == request.action
        && approval.risk_level == request.risk_level
}

fn validate_arm_approval_shape(
    approval: &crate::offdesk::PendingActionApproval,
    manifest: &OffdeskShiftManifestV1,
) -> Result<()> {
    let expected = arm_approval_request(manifest, approval.created_at, 30)?;
    ensure!(
        arm_approval_identity_matches(approval, manifest),
        "shift arm approval identity mismatch"
    );
    ensure!(
        approval.scope == ApprovalScope::Once,
        "shift arm approval must be once-scoped"
    );
    ensure!(
        approval.approval_mode == ApprovalMode::OperatorRequired,
        "shift arm approval must require the operator"
    );
    ensure!(
        approval.preview == expected.preview
            && approval.reason == expected.reason
            && approval.source_surface == expected.source_surface
            && approval.metadata == expected.metadata,
        "shift arm approval presentation or manifest metadata mismatch"
    );
    ensure!(
        approval.expires_at > approval.created_at
            && approval.expires_at <= approval.created_at + Duration::hours(24),
        "shift arm approval expiry is outside its bounded range"
    );
    Ok(())
}

fn validate_arm_approval(
    approval: &crate::offdesk::PendingActionApproval,
    manifest: &OffdeskShiftManifestV1,
    now: DateTime<Utc>,
) -> Result<()> {
    validate_arm_approval_shape(approval, manifest)?;
    if approval.status == ApprovalStatus::Approved {
        ensure!(approval.expires_at >= now, "shift arm approval has expired");
        let resolved_at = approval
            .resolved_at
            .context("approved shift arm has no resolution time")?;
        ensure!(
            resolved_at >= approval.created_at && resolved_at <= now,
            "shift arm approval resolution time is invalid"
        );
        ensure!(
            approval
                .resolved_by
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty()),
            "approved shift arm has no operator identity"
        );
    }
    Ok(())
}

fn arm_receipt(
    manifest: &OffdeskShiftManifestV1,
    approval: &crate::offdesk::PendingActionApproval,
) -> Result<OffdeskShiftArmReceiptV1> {
    let approved_at = approval
        .resolved_at
        .context("approved shift arm has no resolution time")?;
    let receipt = OffdeskShiftArmReceiptV1 {
        schema: OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA.to_string(),
        receipt_id: format!("shift_arm_receipt_{}", approval.approval_id),
        shift_id: manifest.shift_id.clone(),
        manifest_sha256: offdesk_shift_manifest_sha256(manifest)?,
        approval_id: approval.approval_id.clone(),
        approved_at,
        expires_at: manifest.window.stops_at,
        approved_by: approval
            .resolved_by
            .clone()
            .context("approved shift arm has no operator")?,
        task_dispatch_still_requires_fresh_approval: true,
    };
    receipt.validate_against_manifest(manifest)?;
    Ok(receipt)
}

fn initial_armed_state(
    manifest: &OffdeskShiftManifestV1,
    receipt: &OffdeskShiftArmReceiptV1,
) -> Result<OffdeskShiftStateV1> {
    let state = OffdeskShiftStateV1 {
        schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
        shift_id: manifest.shift_id.clone(),
        manifest_sha256: receipt.manifest_sha256.clone(),
        arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(receipt, manifest)?,
        sequence: 1,
        status: OffdeskShiftStatusV1::Armed,
        updated_at: receipt.approved_at,
        reason: "operator approved exact bounded shift manifest".to_string(),
        acceptance_receipt_id: None,
        acceptance_receipt_sha256: None,
    };
    state.validate_against(manifest, receipt)?;
    Ok(state)
}

fn ensure_json_equal<T: Serialize>(left: &T, right: &T, message: &str) -> Result<()> {
    ensure!(
        serde_json::to_value(left)? == serde_json::to_value(right)?,
        "{message}"
    );
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}
