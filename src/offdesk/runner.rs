//! Gated background runner launch and durable polling.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use uuid::Uuid;

use crate::tmux::{self, Session};

use super::adaptive_wiki::{build_runtime_projection, AdaptiveWikiProjectionPolicy};
use super::approval::ExecutionBrief;
use super::background::{
    BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore, BackgroundRunnerKind,
    BackgroundRunnerPhase, NotificationDecision,
};
use super::mode_contract::{assess_offdesk_mode, OffdeskModeAssessment, OffdeskModeLifecycle};
use super::redaction::operator_safe_text;
use super::scheduler::{SchedulerGate, SchedulerGateOutcome, SchedulerGateRequest};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackgroundLaunchRequest {
    pub gate_request: SchedulerGateRequest,
    pub runner_kind: BackgroundRunnerKind,
    pub ticket_id: Option<String>,
    pub launch_spec_summary: Option<String>,
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
}

impl LocalCommandLaunchSpec {
    pub fn new(command: impl Into<String>, working_dir: impl Into<PathBuf>) -> Self {
        Self {
            command: command.into(),
            working_dir: working_dir.into(),
            log_artifact_path: None,
            result_artifact_path: None,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notification: Option<NotificationDecision>,
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
            spawn_local_background_command(&mut probe, &command_spec)?;
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
        probe.last_observed_at = Some(now);
        probe.last_recovery_evidence = Some(decision.evidence.clone());
        probe.last_recovery_terminal = Some(decision.terminal);
        let notification =
            notification_cooldown.map(|cooldown| probe.record_notification_attempt(now, cooldown));
        let mode_assessment = assess_offdesk_mode(
            probe.agent_mode,
            background_mode_lifecycle(&decision, probe.result_artifact_present),
        );
        outcomes.push(BackgroundPollOutcome {
            probe: probe.clone(),
            decision,
            mode_assessment,
            notification,
        });
    }

    store.save(&probes)?;
    Ok(outcomes)
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
    probe: &mut BackgroundProbe,
    spec: &LocalCommandLaunchSpec,
) -> Result<()> {
    let working_dir = probe_working_dir(probe)?;
    let log_path = probe_log_path(probe)?;
    let log = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .with_context(|| format!("failed to open background log {}", log_path.display()))?;
    let stderr = log.try_clone()?;

    let child = Command::new("sh")
        .arg("-c")
        .arg(&spec.command)
        .current_dir(&working_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(stderr))
        .spawn()
        .with_context(|| format!("failed to spawn command in {}", working_dir.display()))?;

    probe.runtime_pid = Some(child.id());
    probe.runtime_handle_alive = true;
    Ok(())
}

fn spawn_local_tmux_command(
    probe: &mut BackgroundProbe,
    spec: &LocalCommandLaunchSpec,
) -> Result<()> {
    if !tmux::is_tmux_available() {
        bail!("tmux is not available");
    }

    let working_dir = probe_working_dir(probe)?;
    let log_path = probe_log_path(probe)?;
    let session_name = Session::generate_name(&probe.ticket_id, "offdesk");
    let session = Session::new(&probe.ticket_id, "offdesk")?;
    let command = redirect_command_to_log(&spec.command, &log_path);
    session.create(&path_to_string(&working_dir), Some(&command))?;

    probe.tmux_session_name = Some(session_name);
    probe.runtime_pid = session.get_pane_pid();
    probe.runtime_handle_alive = session.exists();
    Ok(())
}

fn refresh_probe_runtime_evidence(probe: &mut BackgroundProbe, now: DateTime<Utc>) -> Result<()> {
    if let Some(pid) = probe.runtime_pid {
        probe.runtime_handle_alive = process_alive(pid);
    }
    if let Some(session_name) = &probe.tmux_session_name {
        probe.runtime_handle_alive = tmux_session_alive(session_name);
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
    #[cfg(target_os = "linux")]
    {
        Path::new("/proc").join(pid.to_string()).exists()
    }
    #[cfg(not(target_os = "linux"))]
    {
        Command::new("kill")
            .arg("-0")
            .arg(pid.to_string())
            .status()
            .map(|status| status.success())
            .unwrap_or(false)
    }
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
}
