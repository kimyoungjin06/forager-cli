//! Read-only presentation for bounded Offdesk shift records.

use anyhow::{Context, Result};
use serde::Serialize;

use super::operator_safe_json_value;
use super::shift_commands::{
    ShiftArmResult, ShiftDisarmResult, ShiftPrepareResult, ShiftRoutineClaimResult,
    ShiftRoutineCompleteResult, ShiftRoutineRecoveryResult, ShiftRoutineStartResult,
    ShiftTemplateInstantiationResult,
};
use crate::offdesk::{
    offdesk_shift_manifest_sha256, operator_safe_text, OffdeskShiftCouncilEscalationPacketV1,
    OffdeskShiftEnterprisePolicyReportV1, OffdeskShiftHostPolicyReportV1, OffdeskShiftRecordV1,
    OffdeskShiftRetentionReportV1, OffdeskShiftReviewProjectionV1, OffdeskShiftStatusV1,
    OffdeskShiftStoreSnapshotV1,
};

const OFFDESK_SHIFT_STATUS_REPORT_SCHEMA: &str = "offdesk_shift_status_report.v1";
const OFFDESK_SHIFT_PREPARE_REPORT_SCHEMA: &str = "offdesk_shift_prepare_report.v1";
const OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_REPORT_SCHEMA: &str =
    "offdesk_shift_template_instantiation_report.v1";
const OFFDESK_SHIFT_ARM_REPORT_SCHEMA: &str = "offdesk_shift_arm_report.v1";
const OFFDESK_SHIFT_DISARM_REPORT_SCHEMA: &str = "offdesk_shift_disarm_report.v1";
const OFFDESK_SHIFT_ROUTINE_START_REPORT_SCHEMA: &str = "offdesk_shift_routine_start_report.v1";
const OFFDESK_SHIFT_ROUTINE_CLAIM_REPORT_SCHEMA: &str = "offdesk_shift_routine_claim_report.v1";
const OFFDESK_SHIFT_ROUTINE_COMPLETE_REPORT_SCHEMA: &str =
    "offdesk_shift_routine_complete_report.v1";
const OFFDESK_SHIFT_ROUTINE_RECOVERY_REPORT_SCHEMA: &str =
    "offdesk_shift_routine_recovery_report.v1";

pub(super) fn present_shift_enterprise_policy_report(
    report: &OffdeskShiftEnterprisePolicyReportV1,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!("Offdesk Shift Enterprise Policies");
    println!("  default: {}", report.default_profile_key.as_str());
    for policy in &report.profiles {
        println!("  {}", policy.profile_key.as_str());
        println!("    operator mode: {}", policy.operator_mode.as_str());
        println!("    notification: {}", policy.notification_route.as_str());
        println!(
            "    child task network: host policy required (Forager enforced: {})",
            policy.forager_controls_child_task_network
        );
    }
    println!("  read only: true");
    println!("  selects or arms shift: false");
    Ok(())
}

pub(super) fn present_shift_read_policy_report(
    report: &serde_json::Value,
    json: bool,
) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(report)?);
    } else {
        println!("Offdesk Shift Filesystem Read Policy Probe");
        println!(
            "  status: {}",
            report["status"].as_str().unwrap_or("unavailable")
        );
        if let Some(abi) = report["landlock_abi"].as_u64() {
            println!("  Landlock ABI: {abi}");
            println!("  filesystem reads: task directory and listed runtime paths only");
        }
        if let Some(reason) = report["reason"].as_str() {
            println!("  unavailable: {}", operator_safe_text(reason));
        }
        println!("  network, metadata, IPC and preopened descriptors covered: false");
        println!("  creates profile state: false");
        println!("  task dispatch authorized: false");
    }
    Ok(())
}

pub(super) fn present_shift_host_policy_report(
    report: &OffdeskShiftHostPolicyReportV1,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!("Offdesk Shift Host Policy Probe");
    println!("  provider: {}", report.provider.as_str());
    println!("  status: {:?}", report.status);
    if let Some(attestation) = &report.attestation {
        println!("  launcher: {}", attestation.launcher_path);
        println!("  network boundary: isolated IP namespace");
        println!(
            "  filesystem boundary: {}",
            attestation.filesystem_boundary().as_str()
        );
        if let Some(policy) = &attestation.filesystem_policy {
            println!(
                "  host filesystem reads allowed: {}",
                policy.host_filesystem_read_allowed
            );
            println!(
                "  writes outside working directory allowed: {}",
                policy.host_filesystem_write_outside_working_directory_allowed
            );
            println!("  private /tmp: {}", policy.private_tmp);
            println!(
                "  hardlink aliases outside working directory covered: {}",
                policy.hardlink_aliases_outside_working_directory_covered
            );
            println!(
                "  nested mounts inside working directory covered: {}",
                policy.nested_mounts_inside_working_directory_covered
            );
            println!(
                "  device and kernel interfaces covered: {}",
                policy.device_and_kernel_interfaces_covered
            );
        }
        println!("  Unix domain sockets covered: false");
    }
    if let Some(reason) = &report.unavailable_reason {
        println!("  unavailable: {}", operator_safe_text(reason));
    }
    println!("  read only: true");
    println!("  task dispatch authorized: false");
    Ok(())
}

pub(super) fn present_shift_retention_report(
    report: &OffdeskShiftRetentionReportV1,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!("Offdesk Shift Retention Report");
    println!("  profile: {}", operator_safe_text(&report.profile));
    if let Some(shift_id) = &report.selected_shift_id {
        println!("  shift: {}", operator_safe_text(shift_id));
    } else {
        println!("  shifts: {}", report.summary.shift_count);
    }
    println!(
        "  canonical ledger records preserved: {}",
        report.summary.canonical_ledger_record_count
    );
    println!(
        "  canonical receipts preserved: {}",
        report.summary.canonical_receipt_count
    );
    println!(
        "  evidence references: {} preserved, {} compaction candidate(s)",
        report.summary.preserved_evidence_reference_count,
        report.summary.compaction_candidate_count
    );
    println!(
        "  candidate bytes: {}",
        report.summary.compaction_candidate_bytes
    );
    if report.summary.omitted_item_count > 0 {
        println!(
            "  omitted evidence items: {}",
            report.summary.omitted_item_count
        );
    }
    println!("  source or evidence mutation performed: false");
    println!("  compaction authorized: false");
    println!("  separate retention approval required: true");
    Ok(())
}

#[derive(Serialize)]
struct ShiftMutationReport<'a> {
    schema: &'static str,
    outcome: &'static str,
    changed: bool,
    manifest_sha256: String,
    record: &'a OffdeskShiftRecordV1,
}

pub(super) fn present_shift_prepare_result(result: &ShiftPrepareResult, json: bool) -> Result<()> {
    present_shift_mutation(
        OFFDESK_SHIFT_PREPARE_REPORT_SCHEMA,
        if result.created {
            "prepared"
        } else {
            "unchanged"
        },
        result.created,
        &result.record,
        json,
    )
}

pub(super) fn present_shift_template_instantiation_result(
    result: &ShiftTemplateInstantiationResult,
    json: bool,
) -> Result<()> {
    #[derive(Serialize)]
    struct Authority {
        manifest_only: bool,
        arm_approval_granted_by_instantiation: bool,
        recurrence_authorized: bool,
        next_occurrence_authorized: bool,
        does_not_authorize: [&'static str; 5],
    }

    #[derive(Serialize)]
    struct Report<'a> {
        schema: &'static str,
        outcome: &'static str,
        created: bool,
        shift_id: &'a str,
        manifest_sha256: String,
        template: &'a crate::offdesk::OffdeskShiftTemplateInstantiationV1,
        arm_receipt_present: bool,
        lifecycle_state_present: bool,
        authority: Authority,
        record: &'a OffdeskShiftRecordV1,
    }

    let template = result
        .record
        .manifest
        .template_instantiation
        .as_ref()
        .context("template result does not carry template provenance")?;
    let report = Report {
        schema: OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_REPORT_SCHEMA,
        outcome: if result.created {
            "prepared"
        } else {
            "unchanged"
        },
        created: result.created,
        shift_id: &result.record.manifest.shift_id,
        manifest_sha256: offdesk_shift_manifest_sha256(&result.record.manifest)?,
        template,
        arm_receipt_present: result.record.arm_receipt.is_some(),
        lifecycle_state_present: result.record.state.is_some(),
        authority: Authority {
            manifest_only: true,
            arm_approval_granted_by_instantiation: false,
            recurrence_authorized: false,
            next_occurrence_authorized: false,
            does_not_authorize: [
                "shift_arm",
                "task_dispatch",
                "routine_execution",
                "next_template_occurrence",
                "canonical_acceptance",
            ],
        },
        record: &result.record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!("Offdesk Shift Template Instantiation");
    println!("  outcome: {}", report.outcome);
    println!("  template: {}", operator_safe_text(&template.template_key));
    println!("  cadence: {:?}", template.cadence);
    println!("  slot: {} ({})", template.slot, template.slot_basis);
    println!("  shift: {}", operator_safe_text(report.shift_id));
    println!("  authority: manifest only; separate arm approval required");
    println!("  next occurrence: requires a new manifest");
    Ok(())
}

pub(super) fn present_shift_arm_result(result: &ShiftArmResult, json: bool) -> Result<()> {
    match result {
        ShiftArmResult::Pending(approvals) => {
            super::present_pending_approval_views(approvals, json)
        }
        ShiftArmResult::Armed { record, created } => present_shift_mutation(
            OFFDESK_SHIFT_ARM_REPORT_SCHEMA,
            if *created { "armed" } else { "reconciled" },
            *created,
            record,
            json,
        ),
    }
}

pub(super) fn present_shift_disarm_result(result: &ShiftDisarmResult, json: bool) -> Result<()> {
    present_shift_mutation(
        OFFDESK_SHIFT_DISARM_REPORT_SCHEMA,
        if result.changed {
            "disarmed"
        } else {
            "already_terminal"
        },
        result.changed,
        &result.record,
        json,
    )
}

pub(super) fn present_shift_routine_start_result(
    result: &ShiftRoutineStartResult,
    json: bool,
) -> Result<()> {
    #[derive(Serialize)]
    struct Report<'a> {
        schema: &'static str,
        outcome: &'static str,
        routine: &'a crate::offdesk::OffdeskShiftRoutineBindingV1,
        #[serde(skip_serializing_if = "Option::is_none")]
        reservation: Option<&'a crate::offdesk::OffdeskShiftRoutineRunReservationV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        receipt: Option<&'a crate::offdesk::OffdeskShiftRoutineOutcomeReceiptV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<&'a str>,
        record: &'a OffdeskShiftRecordV1,
    }
    let report = Report {
        schema: OFFDESK_SHIFT_ROUTINE_START_REPORT_SCHEMA,
        outcome: result.outcome,
        routine: &result.routine,
        reservation: result.reservation.as_ref(),
        receipt: result.receipt.as_ref(),
        reason: result.reason.as_deref(),
        record: &result.record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!(
        "Shift routine {}: {} ({})",
        result.outcome,
        operator_safe_text(&result.routine.routine_id),
        operator_safe_text(&result.record.manifest.shift_id)
    );
    if let Some(reservation) = &result.reservation {
        println!("  run: {}", operator_safe_text(&reservation.run_id));
        println!("  scheduled: {}", reservation.scheduled_at);
    }
    if let Some(reason) = &result.reason {
        println!("  reason: {}", operator_safe_text(reason));
    }
    Ok(())
}

pub(super) fn present_shift_routine_claim_result(
    result: &ShiftRoutineClaimResult,
    json: bool,
) -> Result<()> {
    #[derive(Serialize)]
    struct Report<'a> {
        schema: &'static str,
        requested_task: &'static str,
        outcome: &'static str,
        #[serde(skip_serializing_if = "Option::is_none")]
        routine: Option<&'a crate::offdesk::OffdeskShiftRoutineBindingV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        policy: Option<&'a crate::offdesk::shift_runtime::OffdeskShiftRoutineExecutionPolicyV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        reservation: Option<&'a crate::offdesk::OffdeskShiftRoutineRunReservationV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        receipt: Option<&'a crate::offdesk::OffdeskShiftRoutineOutcomeReceiptV1>,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<&'a str>,
        record: &'a OffdeskShiftRecordV1,
    }
    let report = Report {
        schema: OFFDESK_SHIFT_ROUTINE_CLAIM_REPORT_SCHEMA,
        requested_task: result.requested_task,
        outcome: result.outcome,
        routine: result.routine.as_ref(),
        policy: result.policy.as_ref(),
        reservation: result.reservation.as_ref(),
        receipt: result.receipt.as_ref(),
        reason: result.reason.as_deref(),
        record: &result.record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!(
        "Shift routine claim: {} ({})",
        result.outcome,
        operator_safe_text(&result.record.manifest.shift_id)
    );
    println!("  requested task: {}", result.requested_task);
    if let Some(policy) = &result.policy {
        println!("  routine: {}", operator_safe_text(&policy.routine_id));
        println!("  handler: {:?}", policy.handler);
        println!("  allowed effect: {:?}", policy.allowed_effect);
        println!("  disarm after terminal: {}", policy.disarm_after_terminal);
    }
    if let Some(reservation) = &result.reservation {
        println!("  run: {}", operator_safe_text(&reservation.run_id));
        println!("  scheduled: {}", reservation.scheduled_at);
    }
    if let Some(reason) = &result.reason {
        println!("  reason: {}", operator_safe_text(reason));
    }
    Ok(())
}

pub(super) fn present_shift_routine_complete_result(
    result: &ShiftRoutineCompleteResult,
    json: bool,
) -> Result<()> {
    #[derive(Serialize)]
    struct Report<'a> {
        schema: &'static str,
        outcome: crate::offdesk::OffdeskShiftRoutineOutcomeV1,
        created: bool,
        receipt: &'a crate::offdesk::OffdeskShiftRoutineOutcomeReceiptV1,
        record: &'a OffdeskShiftRecordV1,
    }
    let report = Report {
        schema: OFFDESK_SHIFT_ROUTINE_COMPLETE_REPORT_SCHEMA,
        outcome: result.receipt.outcome,
        created: result.created,
        receipt: &result.receipt,
        record: &result.record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!(
        "Shift routine {:?}: {}",
        result.receipt.outcome,
        operator_safe_text(&result.receipt.routine_id)
    );
    println!("  run: {}", operator_safe_text(&result.receipt.run_id));
    println!("  receipt created: {}", result.created);
    println!("  summary: {}", operator_safe_text(&result.receipt.summary));
    Ok(())
}

pub(super) fn present_shift_routine_recovery_result(
    result: &ShiftRoutineRecoveryResult,
    json: bool,
) -> Result<()> {
    #[derive(Serialize)]
    struct Report<'a> {
        schema: &'static str,
        outcome: &'static str,
        created: bool,
        new_execution_result_asserted: bool,
        receipt: &'a crate::offdesk::OffdeskShiftRoutineOutcomeReceiptV1,
        record: &'a OffdeskShiftRecordV1,
    }
    let report = Report {
        schema: OFFDESK_SHIFT_ROUTINE_RECOVERY_REPORT_SCHEMA,
        outcome: result.outcome,
        created: result.created,
        new_execution_result_asserted: false,
        receipt: &result.receipt,
        record: &result.record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!(
        "Shift routine recovery {}: {}",
        result.outcome,
        operator_safe_text(&result.receipt.routine_id)
    );
    println!("  run: {}", operator_safe_text(&result.receipt.run_id));
    println!("  new execution result asserted: false");
    println!("  receipt created: {}", result.created);
    println!("  summary: {}", operator_safe_text(&result.receipt.summary));
    Ok(())
}

fn present_shift_mutation(
    schema: &'static str,
    outcome: &'static str,
    changed: bool,
    record: &OffdeskShiftRecordV1,
    json: bool,
) -> Result<()> {
    let report = ShiftMutationReport {
        schema,
        outcome,
        changed,
        manifest_sha256: offdesk_shift_manifest_sha256(&record.manifest)?,
        record,
    };
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }
    println!(
        "Bounded Offdesk shift {}: {}",
        outcome,
        operator_safe_text(&record.manifest.shift_id)
    );
    println!("  changed: {changed}");
    println!("  manifest sha256: {}", report.manifest_sha256);
    println!(
        "  status: {}",
        record
            .state
            .as_ref()
            .map(|state| status_label(state.status))
            .unwrap_or("prepared")
    );
    Ok(())
}

#[derive(Debug, Serialize)]
struct OffdeskShiftStatusReport {
    schema: &'static str,
    profile: String,
    record_count: usize,
    active_shift_id: Option<String>,
    latest_shift_id: Option<String>,
    shifts: Vec<OffdeskShiftStatusItem>,
}

#[derive(Debug, Serialize)]
struct OffdeskShiftStatusItem {
    weekly_usage: serde_json::Value,
    shift_id: String,
    manifest_sha256: String,
    starts_at: chrono::DateTime<chrono::Utc>,
    stops_at: chrono::DateTime<chrono::Utc>,
    task_count: usize,
    routine_count: usize,
    transition_count: usize,
    task_launch_reservation_count: usize,
    task_launch_ticket_count: usize,
    task_outcome_count: usize,
    unresolved_task_launch_count: usize,
    routine_reservation_count: usize,
    routine_outcome_count: usize,
    unresolved_routine_count: usize,
    status: Option<OffdeskShiftStatusV1>,
    sequence: Option<u64>,
    terminal: bool,
}

pub(super) fn present_shift_status(
    profile: &str,
    snapshot: &OffdeskShiftStoreSnapshotV1,
    json: bool,
) -> Result<()> {
    let report = build_status_report(profile, snapshot)?;
    if json {
        let value = operator_safe_json_value(serde_json::to_value(report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if report.shifts.is_empty() {
        println!(
            "No bounded Offdesk shifts found for profile {}.",
            operator_safe_text(profile)
        );
        return Ok(());
    }

    println!(
        "{:<28} {:<20} {:<8} {:<12} {:<8} {:<8} WINDOW",
        "SHIFT ID", "STATUS", "TASKS", "TASK R/T/O", "ROUTINES", "SEQ"
    );
    for shift in &report.shifts {
        println!(
            "{:<28} {:<20} {:<8} {:<12} {:<8} {:<8} {} -> {}",
            operator_safe_text(&shift.shift_id),
            shift.status.map(status_label).unwrap_or("prepared"),
            shift.task_count,
            format!(
                "{}/{}/{}",
                shift.task_launch_reservation_count,
                shift.task_launch_ticket_count,
                shift.task_outcome_count
            ),
            shift.routine_count,
            shift
                .sequence
                .map(|sequence| sequence.to_string())
                .unwrap_or_else(|| "-".to_string()),
            shift.starts_at,
            shift.stops_at,
        );
    }
    Ok(())
}

pub(super) fn present_shift_record(record: &OffdeskShiftRecordV1, json: bool) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(record)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    let manifest = &record.manifest;
    println!("Bounded Offdesk Shift");
    println!("  shift: {}", operator_safe_text(&manifest.shift_id));
    println!("  profile: {}", operator_safe_text(&manifest.profile));
    println!(
        "  status: {}",
        record
            .state
            .as_ref()
            .map(|state| status_label(state.status))
            .unwrap_or("prepared")
    );
    println!(
        "  window: {} -> {} ({})",
        manifest.window.starts_at,
        manifest.window.stops_at,
        operator_safe_text(&manifest.window.timezone)
    );
    println!(
        "  plan: {} / review {}",
        operator_safe_text(&manifest.plan_id),
        operator_safe_text(&manifest.plan_review_id)
    );
    println!(
        "  manifest sha256: {}",
        offdesk_shift_manifest_sha256(manifest)?
    );
    println!(
        "  bindings: {} tasks, {} routines",
        manifest.task_bindings.len(),
        manifest.routine_bindings.len()
    );
    if let Some(policy) = &manifest.read_policy {
        println!(
            "  filesystem reads: task directory and listed runtime paths only ({})",
            policy.provider
        );
        println!("  read policy sha256: {}", policy.sha256()?);
        println!("  read policy: network, metadata, IPC and preopened descriptors not covered");
    }
    println!("  transitions: {}", record.transitions.len());
    println!(
        "  task launches: {} reserved, {} ticketed, {} terminal",
        record.task_launch_reservations.len(),
        record.task_launch_ticket_receipts.len(),
        record.task_outcome_receipts.len()
    );
    println!(
        "  unresolved task launches: {}",
        record
            .task_launch_reservations
            .iter()
            .filter(|reservation| !record
                .task_outcome_receipts
                .iter()
                .any(|outcome| outcome.launch_id == reservation.launch_id))
            .count()
    );
    println!(
        "  routine runs: {} reserved, {} completed",
        record.routine_run_reservations.len(),
        record.routine_outcome_receipts.len()
    );
    if let Some(state) = &record.state {
        println!("  sequence: {}", state.sequence);
        println!("  reason: {}", operator_safe_text(&state.reason));
    }
    Ok(())
}

pub(super) fn present_shift_review(
    projection: &OffdeskShiftReviewProjectionV1,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(projection)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!("Bounded Offdesk Shift Morning Review");
    println!("  shift: {}", operator_safe_text(&projection.shift_id));
    println!("  review status: {}", projection.review_status);
    println!("  lifecycle: {}", projection.lifecycle_status);
    println!(
        "  task attempts: {} reserved, {} ticketed, {} terminal, {} completed",
        projection.summary.task_attempts_reserved,
        projection.summary.task_attempts_ticketed,
        projection.summary.task_attempts_terminal,
        projection.summary.task_attempts_completed
    );
    println!(
        "  routine runs: {} reserved, {} terminal, {} succeeded",
        projection.summary.routine_runs_reserved,
        projection.summary.routine_runs_terminal,
        projection.summary.routine_runs_succeeded
    );
    println!(
        "  review queues: {} Council, {} closeout, {} Wiki candidate(s)",
        projection.summary.open_council_decisions,
        projection.summary.closeout_required,
        projection.summary.wiki_candidates
    );
    println!(
        "  evidence drill-down: {} of {} item(s) shown{}",
        projection.evidence_drilldown.emitted_count,
        projection.evidence_drilldown.total_available,
        if projection.evidence_drilldown.truncated {
            " (bounded; omissions or truncated references are declared)"
        } else {
            ""
        }
    );
    for item in &projection.evidence_drilldown.items {
        println!(
            "    - [{}] {}: {} ({})",
            item.source,
            operator_safe_text(&item.subject_id),
            operator_safe_text(item.reference.as_deref().unwrap_or(&item.label)),
            item.verification
        );
    }
    if !projection.attention_reasons.is_empty() {
        println!("  attention:");
        for reason in &projection.attention_reasons {
            println!("    - {}", operator_safe_text(reason));
        }
    }
    println!("  authority: review-only; no dispatch, acceptance, revision, or Wiki promotion");
    Ok(())
}

pub(super) fn present_shift_council_packet(
    packet: &OffdeskShiftCouncilEscalationPacketV1,
    json: bool,
) -> Result<()> {
    if json {
        let value = operator_safe_json_value(serde_json::to_value(packet)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!("Bounded Offdesk Shift Council Escalation Packet");
    println!("  shift: {}", operator_safe_text(&packet.shift_id));
    println!(
        "  original goal: {}",
        operator_safe_text(&packet.original_goal.summary)
    );
    println!("  review status: {}", packet.current_evidence.review_status);
    println!("  drift: {}", packet.drift.assessment);
    if !packet.drift.signals.is_empty() {
        println!("  signals:");
        for signal in &packet.drift.signals {
            println!("    - {}", operator_safe_text(signal));
        }
    }
    println!(
        "  task budget: {} unique launch(es), {} routine run(s) remaining",
        packet.remaining_budget.task.remaining_unique_task_launches,
        packet.remaining_budget.routine.remaining_runs
    );
    if let Some(resources) = &packet.remaining_budget.routine_resources {
        println!(
            "  routine resources remaining: {} processes, {} calls, {} bytes, {} tokens, {} micro-USD",
            resources.remaining.process_starts,
            resources.remaining.external_calls,
            resources.remaining.disk_bytes_written,
            resources.remaining.llm_tokens,
            resources.remaining.cost_micro_usd
        );
    }
    println!("  choices: continue, revise, handoff, block");
    println!("  default without a decision: block");
    println!(
        "  authority: review-only; Council recommendation does not authorize execution or revision"
    );
    Ok(())
}

fn build_status_report(
    profile: &str,
    snapshot: &OffdeskShiftStoreSnapshotV1,
) -> Result<OffdeskShiftStatusReport> {
    let shifts = snapshot
        .records
        .iter()
        .map(|record| {
            Ok(OffdeskShiftStatusItem {
                weekly_usage: crate::offdesk::weekly_usage::projection(
                    &crate::session::profile_dir_path(profile)?,
                    record.manifest.weekly_usage.as_ref(),
                    chrono::Utc::now(),
                ),
                shift_id: record.manifest.shift_id.clone(),
                manifest_sha256: offdesk_shift_manifest_sha256(&record.manifest)?,
                starts_at: record.manifest.window.starts_at,
                stops_at: record.manifest.window.stops_at,
                task_count: record.manifest.task_bindings.len(),
                routine_count: record.manifest.routine_bindings.len(),
                transition_count: record.transitions.len(),
                task_launch_reservation_count: record.task_launch_reservations.len(),
                task_launch_ticket_count: record.task_launch_ticket_receipts.len(),
                task_outcome_count: record.task_outcome_receipts.len(),
                unresolved_task_launch_count: record
                    .task_launch_reservations
                    .iter()
                    .filter(|reservation| {
                        !record
                            .task_outcome_receipts
                            .iter()
                            .any(|receipt| receipt.launch_id == reservation.launch_id)
                    })
                    .count(),
                routine_reservation_count: record.routine_run_reservations.len(),
                routine_outcome_count: record.routine_outcome_receipts.len(),
                unresolved_routine_count: record
                    .routine_run_reservations
                    .iter()
                    .filter(|reservation| {
                        !record
                            .routine_outcome_receipts
                            .iter()
                            .any(|receipt| receipt.run_id == reservation.run_id)
                    })
                    .count(),
                status: record.state.as_ref().map(|state| state.status),
                sequence: record.state.as_ref().map(|state| state.sequence),
                terminal: record
                    .state
                    .as_ref()
                    .is_some_and(|state| state.status.is_terminal()),
            })
        })
        .collect::<Result<Vec<_>>>()?;
    Ok(OffdeskShiftStatusReport {
        schema: OFFDESK_SHIFT_STATUS_REPORT_SCHEMA,
        profile: profile.to_string(),
        record_count: shifts.len(),
        active_shift_id: snapshot
            .active()
            .map(|record| record.manifest.shift_id.clone()),
        latest_shift_id: snapshot
            .latest()
            .map(|record| record.manifest.shift_id.clone()),
        shifts,
    })
}

fn status_label(status: OffdeskShiftStatusV1) -> &'static str {
    match status {
        OffdeskShiftStatusV1::Armed => "armed",
        OffdeskShiftStatusV1::Running => "running",
        OffdeskShiftStatusV1::AttentionRequired => "attention_required",
        OffdeskShiftStatusV1::ReviewReady => "review_ready",
        OffdeskShiftStatusV1::RevisionRequired => "revision_required",
        OffdeskShiftStatusV1::Blocked => "blocked",
        OffdeskShiftStatusV1::Disarmed => "disarmed",
        OffdeskShiftStatusV1::Accepted => "accepted",
    }
}
