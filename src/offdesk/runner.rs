//! Gated background runner launch and durable polling.

use anyhow::{bail, ensure, Context, Result};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use uuid::Uuid;

use crate::process;
use crate::tmux::{self, Session};

use super::adaptive_wiki::{build_runtime_projection, AdaptiveWikiProjectionPolicy};
use super::approval::ExecutionBrief;
use super::background::{
    BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore, BackgroundRunnerKind,
    BackgroundRunnerPhase, BackgroundTerminationRecord, NotificationDecision,
};
use super::implementation_packet::{
    implementation_packet_record_from_path, operator_safe_implementation_packet_summary,
    work_slice_execution_receipts_from_path, ImplementationPacketSummary,
    WorkSliceExecutionReceipt, WorkSliceExecutionStatus, WorkSliceReceiptProducerRole,
    WorkSliceVerificationStatus, WORK_SLICE_EXECUTION_RECEIPTS_FILE,
    WORK_SLICE_EXECUTION_RECEIPT_SCHEMA,
};
use super::mode_contract::{assess_offdesk_mode, OffdeskModeAssessment, OffdeskModeLifecycle};
use super::redaction::operator_safe_text;
use super::scheduler::{SchedulerGate, SchedulerGateOutcome, SchedulerGateRequest};
use super::task_queue::{next_safe_action_for_background_poll, OffdeskNextSafeAction};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackgroundLaunchRequest {
    pub gate_request: SchedulerGateRequest,
    pub runner_kind: BackgroundRunnerKind,
    pub ticket_id: Option<String>,
    pub launch_spec_summary: Option<String>,
    pub implementation_packet: Option<ImplementationPacketSummary>,
    pub runtime_handle_alive: bool,
    pub provider_launch_spec_reconstructable: bool,
    pub ack_timeout_sec: i64,
    pub adaptive_wiki_runtime_enabled: bool,
    pub adaptive_wiki_context: Option<String>,
    pub adaptive_wiki_entry_ids: Vec<String>,
    pub adaptive_wiki_runtime_policy: Option<AdaptiveWikiProjectionPolicy>,
}

impl BackgroundLaunchRequest {
    pub fn new(gate_request: SchedulerGateRequest, runner_kind: BackgroundRunnerKind) -> Self {
        Self {
            gate_request,
            runner_kind,
            ticket_id: None,
            launch_spec_summary: None,
            implementation_packet: None,
            runtime_handle_alive: true,
            provider_launch_spec_reconstructable: false,
            ack_timeout_sec: 300,
            adaptive_wiki_runtime_enabled: true,
            adaptive_wiki_context: None,
            adaptive_wiki_entry_ids: Vec::new(),
            adaptive_wiki_runtime_policy: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalCommandLaunchSpec {
    pub command: String,
    pub working_dir: PathBuf,
    pub log_artifact_path: Option<PathBuf>,
    pub result_artifact_path: Option<PathBuf>,
    /// Internal adapter input, never inferred from the current/shared cgroup.
    pub containment_parent: Option<PathBuf>,
    pub host_policy_attestation:
        Option<super::shift_host_policy::OffdeskShiftHostPolicyAttestationV1>,
    pub read_policy: Option<super::read_policy::ReadPolicy>,
    pub read_policy_root_identity_sha256: Option<String>,
}

impl LocalCommandLaunchSpec {
    pub fn new(command: impl Into<String>, working_dir: impl Into<PathBuf>) -> Self {
        Self {
            command: command.into(),
            working_dir: working_dir.into(),
            log_artifact_path: None,
            result_artifact_path: None,
            containment_parent: None,
            host_policy_attestation: None,
            read_policy: None,
            read_policy_root_identity_sha256: None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackgroundLaunchOutcome {
    pub gate: SchedulerGateOutcome,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub probe: Option<BackgroundProbe>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BackgroundPollOutcome {
    pub probe: BackgroundProbe,
    pub decision: BackgroundRecoveryDecision,
    #[serde(flatten)]
    pub mode_assessment: OffdeskModeAssessment,
    pub next_safe_action: OffdeskNextSafeAction,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notification: Option<NotificationDecision>,
}

pub const BACKGROUND_TERMINATION_REPORT_SCHEMA: &str = "background_termination_report.v1";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BackgroundTerminationReport {
    pub schema: &'static str,
    pub ticket_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    pub runner_kind: BackgroundRunnerKind,
    pub previous_phase: BackgroundRunnerPhase,
    pub phase: BackgroundRunnerPhase,
    pub changed: bool,
    pub runtime_was_alive: bool,
    pub runtime_alive: bool,
    pub evidence: String,
}

pub fn launch_background_run(
    gate: &SchedulerGate,
    store: &BackgroundRunStore,
    request: BackgroundLaunchRequest,
    brief: Option<&ExecutionBrief>,
    now: DateTime<Utc>,
) -> Result<BackgroundLaunchOutcome> {
    let gate_outcome = gate.evaluate(request.gate_request.clone(), brief, now)?;
    let request = with_adaptive_wiki_runtime_context(request, &gate_outcome);
    if !gate_outcome.can_execute_requested_action() {
        return Ok(BackgroundLaunchOutcome {
            gate: gate_outcome,
            probe: None,
        });
    }

    let probe = build_background_probe(request, now);
    store.upsert(probe.clone())?;

    Ok(BackgroundLaunchOutcome {
        gate: gate_outcome,
        probe: Some(probe),
    })
}

pub fn launch_background_command(
    gate: &SchedulerGate,
    store: &BackgroundRunStore,
    request: BackgroundLaunchRequest,
    brief: Option<&ExecutionBrief>,
    now: DateTime<Utc>,
    command_spec: LocalCommandLaunchSpec,
) -> Result<BackgroundLaunchOutcome> {
    let gate_outcome = gate.evaluate(request.gate_request.clone(), brief, now)?;
    launch_background_command_with_gate_outcome(store, request, gate_outcome, now, command_spec)
}

pub fn launch_background_command_with_gate_outcome(
    store: &BackgroundRunStore,
    request: BackgroundLaunchRequest,
    gate_outcome: SchedulerGateOutcome,
    now: DateTime<Utc>,
    command_spec: LocalCommandLaunchSpec,
) -> Result<BackgroundLaunchOutcome> {
    let request = with_adaptive_wiki_runtime_context(request, &gate_outcome);
    ensure!(
        command_spec.read_policy.is_some()
            == command_spec.read_policy_root_identity_sha256.is_some(),
        "read policy requires an exact approved working directory identity"
    );
    if let Some(policy) = &command_spec.read_policy {
        super::read_policy::validate_runners(policy, std::iter::once(request.runner_kind))?;
        ensure!(
            command_spec.host_policy_attestation.is_none()
                && command_spec.containment_parent.is_none(),
            "read policy cannot be combined with host policy or explicit containment"
        );
    }
    if command_spec.containment_parent.is_some()
        && (request.runner_kind != BackgroundRunnerKind::LocalBackground
            || command_spec.host_policy_attestation.is_some())
    {
        bail!("explicit containment only supports unattested local-background commands");
    }
    if matches!(
        request.runner_kind,
        BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker
    ) {
        bail!("--cmd is only supported for local-background and local-tmux runners");
    }

    if !gate_outcome.can_execute_requested_action() {
        return Ok(BackgroundLaunchOutcome {
            gate: gate_outcome,
            probe: None,
        });
    }

    let mut probe = build_background_probe(request, now);

    prepare_command_probe_metadata(store, &mut probe, &command_spec)?;

    match probe.runner_kind {
        BackgroundRunnerKind::LocalBackground => {
            spawn_local_background_command(store, &mut probe, &command_spec)?;
        }
        BackgroundRunnerKind::LocalTmux => {
            spawn_local_tmux_command(&mut probe, &command_spec)?;
        }
        BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker => unreachable!(),
    }

    refresh_probe_runtime_evidence(&mut probe, now)?;
    store.upsert(probe.clone())?;

    Ok(BackgroundLaunchOutcome {
        gate: gate_outcome,
        probe: Some(probe),
    })
}

fn build_background_probe(request: BackgroundLaunchRequest, now: DateTime<Utc>) -> BackgroundProbe {
    let mut probe = BackgroundProbe::new(
        request
            .ticket_id
            .unwrap_or_else(|| format!("bg_{}", Uuid::new_v4())),
        request.runner_kind,
    );
    probe.capability_id = Some(request.gate_request.capability_id);
    probe.project_key = Some(request.gate_request.project_key);
    probe.request_id = Some(request.gate_request.request_id);
    probe.task_id = Some(request.gate_request.task_id);
    probe.agent_mode = request.gate_request.agent_mode;
    probe.launch_spec_summary = request
        .launch_spec_summary
        .as_deref()
        .map(operator_safe_text);
    probe.implementation_packet = request
        .implementation_packet
        .as_ref()
        .map(operator_safe_implementation_packet_summary);
    probe.runtime_handle_alive = request.runtime_handle_alive;
    probe.provider_launch_spec_reconstructable = request.provider_launch_spec_reconstructable;
    probe.ack_timeout_sec = request.ack_timeout_sec.max(1);
    if let Some(context) = request.adaptive_wiki_context.as_deref() {
        probe.adaptive_wiki_context = Some(operator_safe_text(context));
    }
    probe.adaptive_wiki_entry_ids = request
        .adaptive_wiki_entry_ids
        .into_iter()
        .map(|entry_id| operator_safe_text(&entry_id))
        .collect();
    probe.adaptive_wiki_runtime_policy = request.adaptive_wiki_runtime_policy;

    if matches!(
        probe.runner_kind,
        BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker
    ) {
        probe.phase = BackgroundRunnerPhase::HandoffEmitted;
        probe.handoff_emitted_at = Some(now);
    }

    probe
}

fn with_adaptive_wiki_runtime_context(
    mut request: BackgroundLaunchRequest,
    gate_outcome: &SchedulerGateOutcome,
) -> BackgroundLaunchRequest {
    if !request.adaptive_wiki_runtime_enabled
        || !adaptive_wiki_runtime_context_enabled()
        || !gate_outcome.can_execute_requested_action()
    {
        return request;
    }
    let Some(projection) = build_runtime_projection(&gate_outcome.adaptive_wiki_runtime) else {
        return request;
    };
    request.adaptive_wiki_entry_ids = projection.entry_ids;
    request.adaptive_wiki_runtime_policy = Some(gate_outcome.adaptive_wiki_runtime_policy);
    request.adaptive_wiki_context = Some(projection.context);
    request
}

fn adaptive_wiki_runtime_context_enabled() -> bool {
    std::env::var("FORAGER_ADAPTIVE_WIKI_RUNTIME")
        .map(|value| {
            !matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "0" | "false" | "off" | "no" | "disabled"
            )
        })
        .unwrap_or(true)
}

pub fn poll_background_runs(
    store: &BackgroundRunStore,
    ticket_id: Option<&str>,
    now: DateTime<Utc>,
    notification_cooldown: Option<Duration>,
) -> Result<Vec<BackgroundPollOutcome>> {
    let mut probes = store.load()?;
    let mut outcomes = Vec::new();

    for probe in probes.iter_mut() {
        if ticket_id.is_some_and(|ticket_id| ticket_id != probe.ticket_id) {
            continue;
        }

        refresh_probe_runtime_evidence(probe, now)?;
        let decision = probe.evaluate(now);
        probe.phase = decision.phase;
        let _ = emit_runner_work_slice_receipts(probe, &decision, now);
        probe.last_observed_at = Some(now);
        probe.last_recovery_evidence = Some(decision.evidence.clone());
        probe.last_recovery_terminal = Some(decision.terminal);
        let notification =
            notification_cooldown.map(|cooldown| probe.record_notification_attempt(now, cooldown));
        let mode_assessment = assess_offdesk_mode(
            probe.agent_mode,
            background_mode_lifecycle(&decision, probe.result_artifact_present),
        );
        let next_safe_action = next_safe_action_for_background_poll(
            probe,
            &decision,
            mode_assessment.review_stage_required,
        );
        outcomes.push(BackgroundPollOutcome {
            probe: probe.clone(),
            decision,
            mode_assessment,
            next_safe_action,
            notification,
        });
    }

    store.save(&probes)?;
    Ok(outcomes)
}

/// Terminate one verified local background runtime and persist the evidence.
///
/// A PID is never sufficient authority by itself. Local process termination
/// requires the launch-time process identity to match the current process, and
/// tmux termination additionally requires the exact ticket-derived session.
pub fn terminate_background_run(
    store: &BackgroundRunStore,
    ticket_id: &str,
    terminated_by: &str,
    reason: Option<&str>,
    now: DateTime<Utc>,
) -> Result<BackgroundTerminationReport> {
    let mut probes = store.load()?;
    let index = probes
        .iter()
        .position(|probe| probe.ticket_id == ticket_id)
        .with_context(|| format!("background ticket not found: {ticket_id}"))?;
    let probe = &mut probes[index];
    let previous_phase = probe.phase;

    if probe.termination.is_some() || probe.phase == BackgroundRunnerPhase::Terminated {
        return Ok(BackgroundTerminationReport {
            schema: BACKGROUND_TERMINATION_REPORT_SCHEMA,
            ticket_id: probe.ticket_id.clone(),
            task_id: probe.task_id.clone(),
            runner_kind: probe.runner_kind,
            previous_phase,
            phase: BackgroundRunnerPhase::Terminated,
            changed: false,
            runtime_was_alive: false,
            runtime_alive: false,
            evidence: "background runner was already terminated".to_string(),
        });
    }

    if matches!(
        probe.runner_kind,
        BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker
    ) {
        bail!(
            "runner kind {:?} cannot be terminated by the local operator",
            probe.runner_kind
        );
    }

    refresh_probe_runtime_evidence(probe, now)?;
    let runtime_was_alive = probe.runtime_handle_alive;
    if runtime_was_alive {
        terminate_verified_local_runtime(probe)?;
        refresh_probe_runtime_evidence(probe, now)?;
        if probe.runtime_handle_alive {
            bail!("verified background runtime is still alive after termination");
        }
    }

    let terminated_by = operator_safe_text(terminated_by.trim());
    let terminated_by = if terminated_by.is_empty() {
        "operator".to_string()
    } else {
        terminated_by
    };
    let reason = reason
        .map(str::trim)
        .filter(|reason| !reason.is_empty())
        .map(operator_safe_text)
        .unwrap_or_else(|| "operator requested runtime termination".to_string());
    probe.phase = BackgroundRunnerPhase::Terminated;
    probe.runtime_handle_alive = false;
    probe.termination = Some(BackgroundTerminationRecord {
        terminated_at: now,
        terminated_by,
        reason,
        previous_phase,
        runtime_pid: probe.runtime_pid,
        tmux_session_name: probe.tmux_session_name.clone(),
    });
    probe.last_observed_at = Some(now);
    probe.last_recovery_evidence = Some(if runtime_was_alive {
        "verified local background runtime terminated".to_string()
    } else {
        "local background runtime was already stopped; termination recorded".to_string()
    });
    probe.last_recovery_terminal = Some(true);
    store.save(&probes)?;

    Ok(BackgroundTerminationReport {
        schema: BACKGROUND_TERMINATION_REPORT_SCHEMA,
        ticket_id: ticket_id.to_string(),
        task_id: probes[index].task_id.clone(),
        runner_kind: probes[index].runner_kind,
        previous_phase,
        phase: BackgroundRunnerPhase::Terminated,
        changed: runtime_was_alive,
        runtime_was_alive,
        runtime_alive: false,
        evidence: probes[index]
            .last_recovery_evidence
            .clone()
            .unwrap_or_default(),
    })
}

fn terminate_verified_local_runtime(probe: &BackgroundProbe) -> Result<()> {
    let pid = probe
        .runtime_pid
        .context("live background runtime is missing its recorded PID")?;
    let expected_identity = probe
        .runtime_identity
        .as_deref()
        .context("live background runtime predates verified termination identity")?;
    let current_identity = process::process_identity(pid)
        .context("recorded background runtime is no longer available for identity verification")?;
    if current_identity != expected_identity {
        bail!("recorded background PID now belongs to a different process");
    }

    match probe.runner_kind {
        BackgroundRunnerKind::LocalBackground => {
            process::kill_process_tree(pid);
            if process::process_identity(pid).as_deref() == Some(expected_identity) {
                bail!("local background process tree survived termination");
            }
        }
        BackgroundRunnerKind::LocalTmux => {
            let (session, expected_session) =
                if let Some(evidence) = &probe.read_policy_launch_evidence {
                    super::read_policy_tmux::task_session(
                        &probe.ticket_id,
                        &evidence.working_directory_identity_sha256,
                    )?
                } else {
                    (
                        Session::new(&probe.ticket_id, "offdesk")?,
                        Session::generate_name(&probe.ticket_id, "offdesk"),
                    )
                };
            let recorded_session = probe
                .tmux_session_name
                .as_deref()
                .context("live tmux runtime is missing its recorded session")?;
            if recorded_session != expected_session {
                bail!("recorded tmux session is not the exact ticket-derived session");
            }
            let current_pane_pid = session
                .get_pane_pid()
                .context("recorded tmux session has no verifiable pane PID")?;
            if current_pane_pid != pid {
                bail!("recorded tmux pane PID no longer matches the runtime probe");
            }
            session.kill()?;
            if session.exists() {
                bail!("tmux session survived termination");
            }
        }
        BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker => {
            unreachable!("external runners are rejected before local termination")
        }
    }
    Ok(())
}

fn emit_runner_work_slice_receipts(
    probe: &BackgroundProbe,
    decision: &BackgroundRecoveryDecision,
    now: DateTime<Utc>,
) -> Result<()> {
    if !probe.result_artifact_present || !decision.terminal {
        return Ok(());
    }
    let Some(summary) = probe.implementation_packet.as_ref() else {
        return Ok(());
    };
    let Some(result_path) = probe.result_artifact_path.as_deref() else {
        return Ok(());
    };
    let receipt_dir = Path::new(result_path).parent().unwrap_or(Path::new("."));
    let receipt_path = receipt_dir.join(WORK_SLICE_EXECUTION_RECEIPTS_FILE);
    let packet = implementation_packet_record_from_path(Path::new(&summary.packet_path))?;
    if packet.design.work_slices.is_empty() {
        return Ok(());
    }

    let existing = work_slice_execution_receipts_from_path(&receipt_path).unwrap_or_default();
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&receipt_path)
        .with_context(|| format!("open work-slice receipt sidecar {}", receipt_path.display()))?;
    for (index, slice) in packet.design.work_slices.iter().enumerate() {
        if existing.iter().any(|receipt| {
            work_slice_receipt_covers_slice(receipt, &packet.packet_id, index, slice)
        }) {
            continue;
        }
        let receipt = WorkSliceExecutionReceipt {
            schema: WORK_SLICE_EXECUTION_RECEIPT_SCHEMA.to_string(),
            packet_id: packet.packet_id.clone(),
            project_key: packet.project_key.clone(),
            task_id: probe.task_id.clone(),
            background_ticket_id: Some(probe.ticket_id.clone()),
            generated_at: now.to_rfc3339(),
            producer: "runner_poll".to_string(),
            producer_role: Some(WorkSliceReceiptProducerRole::RunnerObservation),
            slice_id: Some(format!("slice-{index}")),
            slice_index: Some(index),
            slice_label: slice.clone(),
            status: WorkSliceExecutionStatus::Deferred,
            claim_status: None,
            verification_status: WorkSliceVerificationStatus::EvidenceObserved,
            verification_summary: "Runner observed terminal background state and a result artifact; it did not verify semantic slice completion.".to_string(),
            verification_refs: runner_receipt_evidence_refs(probe, decision),
            summary: "Runner observed a terminal result artifact, but no worker-authored slice receipt was available for this planned work slice.".to_string(),
            evidence_refs: runner_receipt_evidence_refs(probe, decision),
            validation_refs: Vec::new(),
            artifact_refs: probe
                .result_artifact_path
                .iter()
                .map(|path| operator_safe_text(path))
                .collect(),
            open_questions: vec![
                "Confirm whether this planned work slice was actually completed by inspecting result evidence.".to_string(),
            ],
            drift_signals: Vec::new(),
            next_safe_action: "Review result evidence or replace this runner-generated receipt with worker-authored slice evidence before accepting truth.".to_string(),
        };
        writeln!(file, "{}", serde_json::to_string(&receipt)?)?;
    }
    Ok(())
}

fn work_slice_receipt_covers_slice(
    receipt: &WorkSliceExecutionReceipt,
    packet_id: &str,
    slice_index: usize,
    slice_label: &str,
) -> bool {
    let expected_slice_id = format!("slice-{slice_index}");
    receipt.packet_id.trim() == packet_id.trim()
        && (receipt.slice_index == Some(slice_index)
            || receipt_match_text(&receipt.slice_label) == receipt_match_text(slice_label)
            || receipt
                .slice_id
                .as_deref()
                .is_some_and(|slice_id| slice_id == expected_slice_id))
}

fn runner_receipt_evidence_refs(
    probe: &BackgroundProbe,
    decision: &BackgroundRecoveryDecision,
) -> Vec<String> {
    let mut refs = Vec::new();
    refs.push(operator_safe_text(&format!(
        "background:{}:phase:{}",
        probe.ticket_id,
        runner_phase_label(decision.phase)
    )));
    if let Some(result_path) = probe.result_artifact_path.as_deref() {
        refs.push(operator_safe_text(&format!(
            "background:{}:result:{}",
            probe.ticket_id,
            path_tail(result_path)
        )));
    }
    if let Some(log_path) = probe.log_artifact_path.as_deref() {
        refs.push(operator_safe_text(&format!(
            "background:{}:log:{}",
            probe.ticket_id,
            path_tail(log_path)
        )));
    }
    refs
}

fn runner_phase_label(phase: BackgroundRunnerPhase) -> &'static str {
    match phase {
        BackgroundRunnerPhase::Launched => "launched",
        BackgroundRunnerPhase::HandoffEmitted => "handoff_emitted",
        BackgroundRunnerPhase::PickupAcknowledged => "pickup_acknowledged",
        BackgroundRunnerPhase::ResultReceived => "result_received",
        BackgroundRunnerPhase::Completed => "completed",
        BackgroundRunnerPhase::Failed => "failed",
        BackgroundRunnerPhase::StaleNoAck => "stale_no_ack",
        BackgroundRunnerPhase::StaleLostCallback => "stale_lost_callback",
        BackgroundRunnerPhase::Reconstructable => "reconstructable",
        BackgroundRunnerPhase::RecoveryAcknowledged => "recovery_acknowledged",
        BackgroundRunnerPhase::Terminated => "terminated",
    }
}

fn path_tail(path: &str) -> &str {
    Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or(path)
}

fn receipt_match_text(value: &str) -> String {
    let mut out = String::new();
    let mut last_space = false;
    for ch in value.chars().flat_map(char::to_lowercase) {
        if ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '-' | '/' | '\\') {
            out.push(ch);
            last_space = false;
        } else if !last_space {
            out.push(' ');
            last_space = true;
        }
    }
    out.trim().to_string()
}

fn background_mode_lifecycle(
    decision: &BackgroundRecoveryDecision,
    result_artifact_present: bool,
) -> OffdeskModeLifecycle {
    match decision.phase {
        BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived
            if result_artifact_present =>
        {
            OffdeskModeLifecycle::CompletedWithResult
        }
        BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived => {
            OffdeskModeLifecycle::CompletedWithoutResult
        }
        BackgroundRunnerPhase::Failed
        | BackgroundRunnerPhase::StaleNoAck
        | BackgroundRunnerPhase::StaleLostCallback
        | BackgroundRunnerPhase::Reconstructable => OffdeskModeLifecycle::Blocked,
        BackgroundRunnerPhase::RecoveryAcknowledged | BackgroundRunnerPhase::Terminated => {
            OffdeskModeLifecycle::Cancelled
        }
        BackgroundRunnerPhase::Launched
        | BackgroundRunnerPhase::HandoffEmitted
        | BackgroundRunnerPhase::PickupAcknowledged => OffdeskModeLifecycle::Running,
    }
}

fn prepare_command_probe_metadata(
    store: &BackgroundRunStore,
    probe: &mut BackgroundProbe,
    spec: &LocalCommandLaunchSpec,
) -> Result<()> {
    let working_dir = normalize_path(&spec.working_dir, Path::new("."));
    let log_path = spec
        .log_artifact_path
        .as_deref()
        .map(|path| normalize_path(path, &working_dir))
        .unwrap_or_else(|| default_log_artifact_path(store, &probe.ticket_id));
    let result_path = spec
        .result_artifact_path
        .as_deref()
        .map(|path| normalize_path(path, &working_dir));

    probe.working_dir = Some(path_to_string(&working_dir));
    probe.log_artifact_path = Some(path_to_string(&log_path));
    probe.result_artifact_path = result_path.as_ref().map(|path| path_to_string(path));
    probe.launch_spec_summary = Some(operator_safe_text(&summarize_command(&spec.command)));

    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent)?;
    }
    if let Some(result_path) = result_path.as_ref().and_then(|path| path.parent()) {
        fs::create_dir_all(result_path)?;
    }

    Ok(())
}

fn spawn_local_background_command(
    store: &BackgroundRunStore,
    probe: &mut BackgroundProbe,
    spec: &LocalCommandLaunchSpec,
) -> Result<()> {
    ensure!(
        spec.read_policy.is_some() == spec.read_policy_root_identity_sha256.is_some(),
        "read policy requires an exact approved working directory identity"
    );
    let working_dir = probe_working_dir(probe)?;
    let log_path = probe_log_path(probe)?;
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .with_context(|| format!("failed to open background log {}", log_path.display()))?;
    let stderr = log.try_clone()?;

    let child = if let Some(attestation) = &spec.host_policy_attestation {
        let (child, evidence) = super::shift_host_policy::spawn_attested_local_background_command(
            attestation,
            &spec.command,
            &working_dir,
            Stdio::from(log),
            Stdio::from(stderr),
            Utc::now(),
        )?;
        probe.host_policy_launch_evidence = Some(evidence);
        child
    } else {
        let (mut command, receipt) = if spec.containment_parent.is_some() {
            super::local_exit::contained_command(&working_dir, &spec.command)?
        } else {
            super::local_exit::command(&working_dir, &spec.command)?
        };
        if let Some(parent) = &spec.containment_parent {
            let binding = process::prepare_contained_command(&mut command, parent)?;
            let path = working_dir
                .join(&receipt.relative_path)
                .with_file_name("containment.json");
            let record = serde_json::json!({
                "schema": "offdesk_containment_binding.v1",
                "ticket_id": probe.ticket_id,
                "task_id": probe.task_id,
                "project_key": probe.project_key,
                "request_id": probe.request_id,
                "nonce": receipt.nonce,
                "containment": binding
            });
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)?;
            file.write_all(&serde_json::to_vec(&record)?)?;
            file.sync_all()?;
            std::fs::File::open(
                path.parent()
                    .context("containment evidence parent missing")?,
            )?
            .sync_all()?;
            probe.local_exit_receipt = Some(receipt.clone());
            probe.runtime_handle_alive = false;
            // Persist the evidence locator before user code can run. A failed
            // placement leaves a held prepared record, never a fallback launch.
            store.upsert(probe.clone())?;
        }
        probe.local_exit_receipt = Some(receipt);
        let read_evidence = spec
            .read_policy
            .as_ref()
            .map(|policy| {
                super::read_policy::apply_to_command_bound(
                    policy,
                    &mut command,
                    &working_dir,
                    spec.read_policy_root_identity_sha256.as_deref(),
                )
            })
            .transpose()?;
        // Runtime identity is supplied by the admitted launch, never inherited.
        command.env("FORAGER_OFFDESK_RUNTIME_BINDING", serde_json::to_string(&serde_json::json!({
            "schema":"offdesk_runtime_binding.v1", "task_id":probe.task_id,
            "request_id":probe.request_id, "project_key":probe.project_key, "ticket_id":probe.ticket_id
        }))?);
        let child = command
            .current_dir(&working_dir)
            .stdin(Stdio::null())
            .stdout(Stdio::from(log))
            .stderr(Stdio::from(stderr))
            .spawn()
            .with_context(|| format!("failed to spawn command in {}", working_dir.display()))?;
        probe.read_policy_launch_evidence = read_evidence;
        child
    };

    let pid = child.id();
    let Some(identity) = process::process_identity(pid) else {
        process::kill_process_tree(pid);
        bail!("failed to record background process identity");
    };
    probe.runtime_pid = Some(pid);
    probe.runtime_identity = Some(identity);
    probe.runtime_handle_alive = true;
    Ok(())
}

fn spawn_local_tmux_command(
    probe: &mut BackgroundProbe,
    spec: &LocalCommandLaunchSpec,
) -> Result<()> {
    if spec.host_policy_attestation.is_some() {
        bail!("selected isolation policy does not support local-tmux task launches");
    }
    if !tmux::is_tmux_available() {
        bail!("tmux is not available");
    }

    let working_dir = probe_working_dir(probe)?;
    let log_path = probe_log_path(probe)?;
    let session_name = Session::generate_name(&probe.ticket_id, "offdesk");
    let session = Session::new(&probe.ticket_id, "offdesk")?;
    if let Some(policy) = &spec.read_policy {
        let root_identity = spec
            .read_policy_root_identity_sha256
            .as_deref()
            .context("restricted tmux root identity missing")?;
        let (session, session_name) =
            super::read_policy_tmux::task_session(&probe.ticket_id, root_identity)?;
        let (command, receipt) = super::local_exit::command(&working_dir, &spec.command)?;
        let binding = super::read_policy_tmux::launch(
            &std::env::current_exe()?,
            &session,
            &working_dir,
            &command,
            policy,
            spec.read_policy_root_identity_sha256
                .as_deref()
                .context("restricted tmux root identity missing")?,
            Some(&log_path),
        )?;
        probe.read_policy_launch_evidence = Some(binding.receipt.evidence);
        probe.local_exit_receipt = Some(receipt);
        probe.tmux_session_name = Some(session_name);
        probe.runtime_pid = Some(binding.receipt.helper_pid);
        probe.runtime_identity = Some(binding.receipt.helper_identity);
        probe.runtime_handle_alive = session.exists();
        return Ok(());
    }
    let command = redirect_command_to_log(&spec.command, &log_path);
    session.create(&path_to_string(&working_dir), Some(&command))?;

    probe.tmux_session_name = Some(session_name);
    probe.runtime_pid = session.get_pane_pid();
    let Some(pid) = probe.runtime_pid else {
        session.kill()?;
        bail!("failed to record tmux pane PID");
    };
    let Some(identity) = process::process_identity(pid) else {
        session.kill()?;
        bail!("failed to record tmux process identity");
    };
    probe.runtime_identity = Some(identity);
    probe.runtime_handle_alive = session.exists();
    Ok(())
}

fn refresh_probe_runtime_evidence(probe: &mut BackgroundProbe, now: DateTime<Utc>) -> Result<()> {
    if let (Some(root), Some(receipt)) = (&probe.working_dir, &mut probe.local_exit_receipt) {
        // A lost or torn receipt is uncertainty, never a successful exit.
        let _ = super::local_exit::refresh(Path::new(root), receipt);
    }
    if let Some(pid) = probe.runtime_pid {
        probe.runtime_handle_alive = process_alive(pid);
        if probe.local_exit_receipt.is_some() {
            probe.runtime_handle_alive &= probe
                .runtime_identity
                .as_ref()
                .is_some_and(|expected| process::process_identity(pid).as_ref() == Some(expected));
        }
    }
    if let Some(session_name) = &probe.tmux_session_name {
        probe.runtime_handle_alive = tmux_session_alive(session_name)
            && (probe.local_exit_receipt.is_none() || probe.runtime_handle_alive);
    }

    if let Some(log_path) = probe.log_artifact_path.as_deref() {
        let path = Path::new(log_path);
        probe.log_artifact_present = path.is_file();
        probe.last_log_tail = read_log_tail(path)?;
    }

    if let Some(result_path) = probe.result_artifact_path.as_deref() {
        probe.result_artifact_present = Path::new(result_path).is_file();
    }
    if let Some(heartbeat_at) = probe.worker_heartbeat_at {
        let timeout = Duration::seconds(probe.heartbeat_timeout_sec.max(1));
        probe.worker_heartbeat_stale = heartbeat_at + timeout <= now;
    }

    Ok(())
}

fn probe_working_dir(probe: &BackgroundProbe) -> Result<PathBuf> {
    probe
        .working_dir
        .as_deref()
        .map(PathBuf::from)
        .context("background probe is missing working_dir")
}

fn probe_log_path(probe: &BackgroundProbe) -> Result<PathBuf> {
    probe
        .log_artifact_path
        .as_deref()
        .map(PathBuf::from)
        .context("background probe is missing log_artifact_path")
}

fn normalize_path(path: &Path, working_dir: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        working_dir.join(path)
    }
}

fn default_log_artifact_path(store: &BackgroundRunStore, ticket_id: &str) -> PathBuf {
    store
        .root()
        .join("background_logs")
        .join(format!("{}.log", safe_artifact_name(ticket_id)))
}

fn safe_artifact_name(value: &str) -> String {
    value
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect()
}

fn summarize_command(command: &str) -> String {
    let trimmed = command.trim();
    if trimmed.chars().count() <= 200 {
        return trimmed.to_string();
    }
    format!("{}...", trimmed.chars().take(200).collect::<String>())
}

fn redirect_command_to_log(command: &str, log_path: &Path) -> String {
    format!("{command} > {} 2>&1", shell_quote_path(log_path))
}

fn shell_quote_path(path: &Path) -> String {
    let value = path_to_string(path);
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn path_to_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn read_log_tail(path: &Path) -> Result<Option<String>> {
    if !path.is_file() {
        return Ok(None);
    }

    let mut file = fs::File::open(path)?;
    let len = file.metadata()?.len();
    file.seek(SeekFrom::Start(len.saturating_sub(4096)))?;

    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes)?;
    let text = String::from_utf8_lossy(&bytes);
    let lines = text.lines().rev().take(20).collect::<Vec<_>>();
    if lines.is_empty() {
        return Ok(None);
    }
    let tail = lines.into_iter().rev().collect::<Vec<_>>().join("\n");
    Ok(Some(operator_safe_text(&tail)))
}

fn process_alive(pid: u32) -> bool {
    process::process_identity(pid).is_some()
}

fn tmux_session_alive(session_name: &str) -> bool {
    Command::new("tmux")
        .args(["has-session", "-t", session_name])
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{
        AdaptiveWikiActivationMode, AdaptiveWikiAiProjection, AdaptiveWikiConfidence,
        AdaptiveWikiKind, AdaptiveWikiProjectionPolicy, AdaptiveWikiScope, ApprovalLedger,
        ApprovalMode, SchedulerGateStatus,
    };
    use tempfile::tempdir;

    #[test]
    fn supervised_poll_rejects_live_pid_with_missing_or_wrong_identity() -> Result<()> {
        let root = tempdir()?;
        let mut probe =
            BackgroundProbe::new("identity-test", BackgroundRunnerKind::LocalBackground);
        probe.working_dir = Some(root.path().display().to_string());
        probe.runtime_pid = Some(std::process::id());
        probe.local_exit_receipt = Some(super::super::local_exit::LocalExitReceipt {
            relative_path: "missing.json".into(),
            nonce: "test".into(),
            status: None,
            wait_scope: None,
        });
        for identity in [None, Some("not-this-process".to_owned())] {
            probe.runtime_identity = identity;
            refresh_probe_runtime_evidence(&mut probe, Utc::now())?;
            assert!(!probe.runtime_handle_alive);
            assert_eq!(
                probe.evaluate(Utc::now()).phase,
                BackgroundRunnerPhase::StaleLostCallback
            );
        }
        probe.runtime_identity = process::process_identity(std::process::id());
        assert!(probe.runtime_identity.is_some());
        refresh_probe_runtime_evidence(&mut probe, Utc::now())?;
        assert!(probe.runtime_handle_alive);
        assert!(!probe.evaluate(Utc::now()).terminal);
        Ok(())
    }

    fn brief(now: DateTime<Utc>) -> ExecutionBrief {
        ExecutionBrief {
            request_id: "request".to_string(),
            task_id: "task".to_string(),
            project_key: "project".to_string(),
            approved: true,
            allowed_runtime_mutations: vec!["background.launch".to_string()],
            allowed_canonical_mutations: vec![],
            fresh_until: Some(now + Duration::minutes(5)),
        }
    }

    fn launch_request() -> BackgroundLaunchRequest {
        let gate_request =
            SchedulerGateRequest::new("background.launch", "project", "request", "task");
        let mut request =
            BackgroundLaunchRequest::new(gate_request, BackgroundRunnerKind::LocalBackground);
        request.ticket_id = Some("ticket".to_string());
        request
    }

    fn wiki_projection(id: &str, instruction: &str) -> AdaptiveWikiAiProjection {
        AdaptiveWikiAiProjection {
            id: id.to_string(),
            kind: AdaptiveWikiKind::Procedure,
            scope: AdaptiveWikiScope::Project,
            scope_ref: "project".to_string(),
            activation_mode: AdaptiveWikiActivationMode::Confirm,
            agent_modes: Vec::new(),
            instruction: instruction.to_string(),
            confidence: AdaptiveWikiConfidence::Explicit,
            evidence_count: 1,
        }
    }

    fn proceed_outcome(
        adaptive_wiki: Vec<AdaptiveWikiAiProjection>,
        adaptive_wiki_runtime: Vec<AdaptiveWikiAiProjection>,
    ) -> SchedulerGateOutcome {
        SchedulerGateOutcome {
            status: SchedulerGateStatus::Proceed,
            capability_id: "background.launch".to_string(),
            risk_level: "runtime_mutation".to_string(),
            approval_mode: ApprovalMode::EnvelopeAuto,
            approval: None,
            artifact_check: None,
            provider_capacity: None,
            provider_fallback: None,
            retry_at: None,
            adaptive_wiki,
            adaptive_wiki_runtime,
            adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
            adaptive_wiki_runtime_decision: None,
            reason: "capability gate passed".to_string(),
            scheduler_may_continue_other_work: true,
        }
    }

    #[test]
    fn launch_without_gate_approval_creates_no_probe() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let gate = SchedulerGate::new(ledger);
        let store = BackgroundRunStore::new(temp.path());

        let outcome = launch_background_run(&gate, &store, launch_request(), None, Utc::now())?;

        assert_eq!(outcome.gate.status, SchedulerGateStatus::PendingApproval);
        assert!(outcome.probe.is_none());
        assert!(store.load()?.is_empty());
        Ok(())
    }

    #[test]
    fn launch_with_execution_brief_writes_probe() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let ledger = ApprovalLedger::new(temp.path());
        let gate = SchedulerGate::new(ledger);
        let store = BackgroundRunStore::new(temp.path());

        let outcome =
            launch_background_run(&gate, &store, launch_request(), Some(&brief(now)), now)?;

        assert_eq!(outcome.gate.status, SchedulerGateStatus::Proceed);
        let probe = outcome.probe.expect("probe");
        assert_eq!(probe.ticket_id, "ticket");
        assert_eq!(probe.phase, BackgroundRunnerPhase::Launched);
        assert_eq!(store.load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn runtime_context_uses_runtime_projection_not_preflight_projection() {
        let request = launch_request();
        let outcome = proceed_outcome(
            vec![wiki_projection("wiki_preflight", "Preflight only")],
            vec![wiki_projection("wiki_runtime", "Runtime only")],
        );

        let request = with_adaptive_wiki_runtime_context(request, &outcome);

        assert_eq!(request.adaptive_wiki_entry_ids, vec!["wiki_runtime"]);
        let context = request
            .adaptive_wiki_context
            .as_deref()
            .expect("runtime context");
        assert!(context.contains("wiki_runtime"));
        assert!(!context.contains("wiki_preflight"));
        assert_eq!(
            request.adaptive_wiki_runtime_policy,
            Some(AdaptiveWikiProjectionPolicy::default())
        );
    }

    #[test]
    fn poll_updates_phase_and_persists_decision() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let now = Utc::now();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.result_artifact_present = true;
        store.upsert(probe)?;

        let outcomes = poll_background_runs(&store, Some("ticket"), now, None)?;

        assert_eq!(outcomes.len(), 1);
        assert_eq!(outcomes[0].decision.phase, BackgroundRunnerPhase::Completed);
        let stored = store.load()?.remove(0);
        assert_eq!(stored.phase, BackgroundRunnerPhase::Completed);
        assert_eq!(stored.last_observed_at, Some(now));
        assert_eq!(
            stored.last_recovery_evidence.as_deref(),
            Some("local background result artifact present")
        );
        assert_eq!(stored.last_recovery_terminal, Some(true));
        Ok(())
    }

    #[test]
    fn read_policy_errors_never_execute_an_unrestricted_fallback() -> Result<()> {
        for variant in 0..4 {
            let temp = tempdir()?;
            let store = BackgroundRunStore::new(temp.path());
            let mut spec = LocalCommandLaunchSpec::new("touch must-not-run", temp.path());
            spec.read_policy = Some(super::super::read_policy::ReadPolicy::canonical());
            let mut request = launch_request();
            if variant != 0 {
                spec.read_policy_root_identity_sha256 = Some("b".repeat(64));
            }
            if variant == 1 {
                request.runner_kind = BackgroundRunnerKind::LocalTmux;
            }
            if variant == 2 {
                spec.containment_parent = Some(temp.path().to_owned());
            }
            let outcome = launch_background_command_with_gate_outcome(
                &store,
                request,
                proceed_outcome(Vec::new(), Vec::new()),
                Utc::now(),
                spec,
            );
            assert!(
                outcome.is_err(),
                "variant {variant} bypassed mandatory policy"
            );
            assert!(!temp.path().join("must-not-run").exists());
            assert!(store.load()?.is_empty());
        }
        Ok(())
    }

    #[test]
    fn containment_rejects_fake_parent_without_executing_command() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let mut probe = build_background_probe(launch_request(), Utc::now());
        let mut spec = LocalCommandLaunchSpec::new("touch must-not-run", temp.path());
        spec.containment_parent = Some(temp.path().to_owned());
        prepare_command_probe_metadata(&store, &mut probe, &spec)?;
        assert!(spawn_local_background_command(&store, &mut probe, &spec).is_err());
        assert!(!temp.path().join("must-not-run").exists());
        assert!(store.load()?.is_empty());
        Ok(())
    }

    #[cfg(target_os = "linux")]
    #[test]
    #[ignore = "run only inside a disposable delegated service"]
    fn contained_runner_survives_supervisor_loss_as_observable_domain() -> Result<()> {
        use crate::process::{inspect_cgroup_v2, CgroupV2Identity};
        use std::time::{Duration as WaitDuration, Instant};
        let parent = PathBuf::from(std::env::var("FORAGER_TEST_DELEGATED_PARENT")?);
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let mut probe = build_background_probe(launch_request(), Utc::now());
        // The descendant has a finite fallback even if an assertion fails.
        let source = "setsid /bin/sh -c 'cat /proc/self/cgroup > membership.tmp && mv membership.tmp membership; n=0; while [ ! -f release ] && [ \"$n\" -lt 500 ]; do n=$((n+1)); sleep 0.01; done' >/dev/null 2>&1 & exit 0";
        let mut spec = LocalCommandLaunchSpec::new(source, temp.path());
        spec.containment_parent = Some(parent.clone());
        prepare_command_probe_metadata(&store, &mut probe, &spec)?;
        spawn_local_background_command(&store, &mut probe, &spec)?;
        let deadline = Instant::now() + WaitDuration::from_secs(4);
        while !temp.path().join("membership").exists() && Instant::now() < deadline {
            std::thread::sleep(WaitDuration::from_millis(10));
        }
        let prepared = store.load()?.remove(0);
        assert!(prepared.runtime_pid.is_none());
        let mut receipt = prepared
            .local_exit_receipt
            .expect("pre-exec persisted locator");
        let evidence = temp
            .path()
            .join(&receipt.relative_path)
            .with_file_name("containment.json");
        let record: serde_json::Value = serde_json::from_slice(&fs::read(evidence)?)?;
        assert_eq!(record["ticket_id"], probe.ticket_id);
        assert_eq!(record["task_id"], "task");
        assert_eq!(record["nonce"], receipt.nonce);
        let task: super::super::OffdeskTask = serde_json::from_value(serde_json::json!({
            "task_id":"task", "request_id":"request", "project_key":"project",
            "status":"failed", "capability_id":"background.launch", "runner_kind":"local_background",
            "command":source, "workdir":temp.path(), "background_ticket_id":probe.ticket_id,
            "created_at":Utc::now(), "updated_at":Utc::now()
        }))?;
        let assert_recovery = |expected: &str| -> Result<()> {
            let before = serde_json::to_vec(&store.load()?)?;
            let report = super::super::runtime_occupancy::inspect(temp.path(), &task)?;
            assert_eq!(report.reason, expected);
            assert!(!report.release_verified);
            assert!(!report.automatic_recovery);
            assert!(!super::super::runtime_occupancy::released(
                temp.path(),
                &task
            )?);
            assert_eq!(before, serde_json::to_vec(&store.load()?)?);
            Ok(())
        };
        let path = PathBuf::from(record["containment"]["path"].as_str().unwrap());
        assert_eq!(path.parent(), Some(parent.as_path()));
        let identity: CgroupV2Identity =
            serde_json::from_value(record["containment"]["identity"].clone())?;
        let membership = fs::read_to_string(temp.path().join("membership"))?;
        assert!(membership.contains(path.file_name().unwrap().to_str().unwrap()));
        assert!(inspect_cgroup_v2(&path, Some(&identity))?.populated);
        assert_recovery("containment_populated")?;
        assert!(process::seal_empty_cgroup_v2(&path, &identity).is_err());
        assert!(inspect_cgroup_v2(&path, Some(&identity))?.populated);
        nix::sys::signal::kill(
            nix::unistd::Pid::from_raw(probe.runtime_pid.unwrap() as i32),
            nix::sys::signal::Signal::SIGKILL,
        )?;
        assert!(inspect_cgroup_v2(&path, Some(&identity))?.populated);
        fs::write(temp.path().join("release"), "release owned test descendant")?;
        let deadline = Instant::now() + WaitDuration::from_secs(8);
        while inspect_cgroup_v2(&path, Some(&identity))?.populated && Instant::now() < deadline {
            std::thread::sleep(WaitDuration::from_millis(10));
        }
        assert!(!inspect_cgroup_v2(&path, Some(&identity))?.populated);
        assert_recovery("containment_empty_admission_unfenced")?;
        assert!(super::super::local_exit::refresh(temp.path(), &mut receipt).is_err());
        super::super::OffdeskTaskStore::new(temp.path()).save(std::slice::from_ref(&task))?;
        for _ in 0..2 {
            assert_eq!(
                super::super::containment_seal::seal_task(temp.path(), &task.task_id)?,
                super::super::containment_seal::SealJournalState::Completed
            );
        }
        assert!(process::seal_empty_cgroup_v2(&path, &identity).is_err());
        assert!(inspect_cgroup_v2(&path, Some(&identity)).is_err());
        assert_recovery("containment_sealed_migration_unverified")?;
        // An opened but removed destination must fail before any user code.
        let mut command = Command::new("sh");
        command
            .args(["-c", "touch must-not-run"])
            .current_dir(temp.path());
        let binding = process::prepare_contained_command(&mut command, &parent)?;
        let removed = PathBuf::from(binding["path"].as_str().unwrap());
        let removed_identity = serde_json::from_value(binding["identity"].clone())?;
        // Empty subgroups still prevent closure, even when populated is zero.
        fs::create_dir(removed.join("test-child"))?;
        assert!(process::seal_empty_cgroup_v2(&removed, &removed_identity).is_err());
        fs::remove_dir(removed.join("test-child"))?;
        process::seal_empty_cgroup_v2(&removed, &removed_identity)?;
        // Reusing the path cannot redirect the already prepared command.
        fs::create_dir(&removed)?;
        assert!(inspect_cgroup_v2(&removed, Some(&removed_identity)).is_err());
        assert!(command.spawn().is_err());
        assert!(!temp.path().join("must-not-run").exists());
        fs::remove_dir(&removed)?;
        Ok(())
    }

    #[test]
    fn poll_marks_heartbeat_stale_from_timestamp() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let now = Utc::now();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.runtime_handle_alive = true;
        probe.worker_heartbeat_at = Some(now - Duration::minutes(20));
        probe.heartbeat_timeout_sec = 300;
        store.upsert(probe)?;

        let outcomes = poll_background_runs(&store, Some("ticket"), now, None)?;

        assert_eq!(outcomes.len(), 1);
        assert_eq!(
            outcomes[0].decision.phase,
            BackgroundRunnerPhase::StaleLostCallback
        );
        let stored = store.load()?.remove(0);
        assert!(stored.worker_heartbeat_stale);
        assert_eq!(stored.last_recovery_terminal, Some(false));
        assert!(stored
            .last_recovery_evidence
            .as_deref()
            .is_some_and(|evidence| evidence.contains("heartbeat is stale")));
        Ok(())
    }

    #[test]
    fn terminate_background_run_stops_only_matching_local_process() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let now = Utc::now();
        let mut child = Command::new("sh").args(["-c", "sleep 60"]).spawn()?;
        let pid = child.id();
        let identity = process::process_identity(pid).expect("spawned process identity");
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.task_id = Some("task".to_string());
        probe.runtime_pid = Some(pid);
        probe.runtime_identity = Some(identity);
        probe.runtime_handle_alive = true;
        store.upsert(probe)?;

        let result =
            terminate_background_run(&store, "ticket", "test", Some("stop requested"), now);
        if result.is_err() {
            let _ = child.kill();
        }
        let report = result?;
        let _ = child.wait();

        assert!(report.changed);
        assert!(report.runtime_was_alive);
        assert!(!report.runtime_alive);
        assert_eq!(report.phase, BackgroundRunnerPhase::Terminated);
        let stored = store.load()?.remove(0);
        assert_eq!(stored.phase, BackgroundRunnerPhase::Terminated);
        let termination = stored.termination.expect("termination evidence");
        assert_eq!(termination.terminated_by, "test");
        assert_eq!(termination.reason, "stop requested");
        assert_eq!(termination.runtime_pid, Some(pid));
        Ok(())
    }

    #[test]
    fn terminate_background_run_rejects_reused_or_mismatched_pid() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let mut child = Command::new("sh").args(["-c", "sleep 60"]).spawn()?;
        let pid = child.id();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.task_id = Some("task".to_string());
        probe.runtime_pid = Some(pid);
        probe.runtime_identity = Some("different-launch".to_string());
        probe.runtime_handle_alive = true;
        store.upsert(probe)?;

        let error = terminate_background_run(&store, "ticket", "test", None, Utc::now())
            .expect_err("mismatched identity must fail closed");
        assert!(error.to_string().contains("different process"));
        assert!(process::process_identity(pid).is_some());
        let stored = store.load()?.remove(0);
        assert_eq!(stored.phase, BackgroundRunnerPhase::Launched);
        assert!(stored.termination.is_none());

        let _ = child.kill();
        let _ = child.wait();
        Ok(())
    }

    #[test]
    fn terminate_background_run_records_already_stopped_local_runtime() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.task_id = Some("task".to_string());
        store.upsert(probe)?;

        let report = terminate_background_run(&store, "ticket", "test", None, Utc::now())?;

        assert!(!report.changed);
        assert!(!report.runtime_was_alive);
        assert_eq!(report.phase, BackgroundRunnerPhase::Terminated);
        assert!(report.evidence.contains("already stopped"));
        Ok(())
    }

    #[test]
    fn terminate_background_run_stops_exact_ticket_tmux_session() -> Result<()> {
        if !tmux::is_tmux_available() {
            return Ok(());
        }
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let ticket_id = Uuid::new_v4().to_string();
        let mut probe = BackgroundProbe::new(&ticket_id, BackgroundRunnerKind::LocalTmux);
        probe.task_id = Some("task".to_string());
        let spec = LocalCommandLaunchSpec::new("sleep 60", temp.path());
        prepare_command_probe_metadata(&store, &mut probe, &spec)?;
        spawn_local_tmux_command(&mut probe, &spec)?;
        let session = Session::new(&ticket_id, "offdesk")?;
        store.upsert(probe)?;

        let result =
            terminate_background_run(&store, &ticket_id, "test", Some("tmux stop"), Utc::now());
        if result.is_err() {
            let _ = session.kill();
        }
        let report = result?;

        assert!(report.changed);
        assert_eq!(report.runner_kind, BackgroundRunnerKind::LocalTmux);
        assert!(!session.exists());
        assert_eq!(
            store.load()?.remove(0).phase,
            BackgroundRunnerPhase::Terminated
        );
        Ok(())
    }
}
