//! Background runner recovery probes.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use std::str::FromStr;

use super::adaptive_wiki::{AdaptiveWikiAgentMode, AdaptiveWikiProjectionPolicy};
use super::redaction::operator_safe_text;

const BACKGROUND_RUNS_FILE: &str = "background_runs.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BackgroundRunnerKind {
    LocalTmux,
    LocalBackground,
    GithubRunner,
    RemoteWorker,
}

impl FromStr for BackgroundRunnerKind {
    type Err = String;

    fn from_str(value: &str) -> std::result::Result<Self, Self::Err> {
        match value.trim().to_ascii_lowercase().as_str() {
            "local_tmux" | "local-tmux" | "tmux" => Ok(Self::LocalTmux),
            "local_background" | "local-background" | "background" => Ok(Self::LocalBackground),
            "github_runner" | "github-runner" | "github" => Ok(Self::GithubRunner),
            "remote_worker" | "remote-worker" | "remote" => Ok(Self::RemoteWorker),
            _ => Err(format!("unknown background runner kind: {value}")),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BackgroundRunnerPhase {
    Launched,
    HandoffEmitted,
    PickupAcknowledged,
    ResultReceived,
    Completed,
    Failed,
    StaleNoAck,
    StaleLostCallback,
    Reconstructable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackgroundProbe {
    pub ticket_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capability_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_mode: Option<AdaptiveWikiAgentMode>,
    pub runner_kind: BackgroundRunnerKind,
    pub phase: BackgroundRunnerPhase,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub launch_spec_summary: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub runtime_pid: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tmux_session_name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub working_dir: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_artifact_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result_artifact_path: Option<String>,
    #[serde(default)]
    pub runtime_handle_alive: bool,
    #[serde(default)]
    pub worker_heartbeat_stale: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub worker_heartbeat_at: Option<DateTime<Utc>>,
    #[serde(default = "default_heartbeat_timeout_sec")]
    pub heartbeat_timeout_sec: i64,
    #[serde(default)]
    pub log_artifact_present: bool,
    #[serde(default)]
    pub result_artifact_present: bool,
    #[serde(default)]
    pub external_ack_present: bool,
    #[serde(default)]
    pub external_result_present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_log_tail: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_observed_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_recovery_evidence: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_recovery_terminal: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notification_cooldown_until: Option<DateTime<Utc>>,
    #[serde(default)]
    pub notification_suppressed_count: u64,
    #[serde(default)]
    pub provider_launch_spec_reconstructable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub handoff_emitted_at: Option<DateTime<Utc>>,
    #[serde(default = "default_ack_timeout_sec")]
    pub ack_timeout_sec: i64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub adaptive_wiki_entry_ids: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub adaptive_wiki_runtime_policy: Option<AdaptiveWikiProjectionPolicy>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub adaptive_wiki_context: Option<String>,
}

impl BackgroundProbe {
    pub fn new(ticket_id: impl Into<String>, runner_kind: BackgroundRunnerKind) -> Self {
        Self {
            ticket_id: ticket_id.into(),
            capability_id: None,
            project_key: None,
            request_id: None,
            task_id: None,
            agent_mode: None,
            runner_kind,
            phase: BackgroundRunnerPhase::Launched,
            launch_spec_summary: None,
            runtime_pid: None,
            tmux_session_name: None,
            working_dir: None,
            log_artifact_path: None,
            result_artifact_path: None,
            runtime_handle_alive: false,
            worker_heartbeat_stale: false,
            worker_heartbeat_at: None,
            heartbeat_timeout_sec: default_heartbeat_timeout_sec(),
            log_artifact_present: false,
            result_artifact_present: false,
            external_ack_present: false,
            external_result_present: false,
            last_log_tail: None,
            last_observed_at: None,
            last_recovery_evidence: None,
            last_recovery_terminal: None,
            notification_cooldown_until: None,
            notification_suppressed_count: 0,
            provider_launch_spec_reconstructable: false,
            handoff_emitted_at: None,
            ack_timeout_sec: default_ack_timeout_sec(),
            adaptive_wiki_entry_ids: Vec::new(),
            adaptive_wiki_runtime_policy: None,
            adaptive_wiki_context: None,
        }
    }

    pub fn evaluate(&self, now: DateTime<Utc>) -> BackgroundRecoveryDecision {
        match self.runner_kind {
            BackgroundRunnerKind::LocalTmux => self.evaluate_local_tmux(),
            BackgroundRunnerKind::LocalBackground => self.evaluate_local_background(),
            BackgroundRunnerKind::GithubRunner | BackgroundRunnerKind::RemoteWorker => {
                self.evaluate_external(now)
            }
        }
    }

    pub fn record_notification_attempt(
        &mut self,
        now: DateTime<Utc>,
        cooldown: Duration,
    ) -> NotificationDecision {
        if self
            .notification_cooldown_until
            .is_some_and(|cooldown_until| cooldown_until > now)
        {
            self.notification_suppressed_count += 1;
            NotificationDecision::Suppress {
                suppressed_count: self.notification_suppressed_count,
                cooldown_until: self.notification_cooldown_until,
            }
        } else {
            let suppressed_count = self.notification_suppressed_count;
            self.notification_cooldown_until = Some(now + cooldown);
            self.notification_suppressed_count = 0;
            NotificationDecision::Send {
                previous_suppressed_count: suppressed_count,
                cooldown_until: self.notification_cooldown_until,
            }
        }
    }

    fn evaluate_local_tmux(&self) -> BackgroundRecoveryDecision {
        if self.result_artifact_present {
            return BackgroundRecoveryDecision::completed(
                "local tmux result sidecar present",
                self,
            );
        }
        if !self.runtime_handle_alive {
            return BackgroundRecoveryDecision::failed(
                "local tmux session missing and no result sidecar",
                self,
            );
        }
        if self.worker_heartbeat_stale {
            return BackgroundRecoveryDecision::stale(
                BackgroundRunnerPhase::StaleLostCallback,
                "local tmux heartbeat is stale",
                self,
            );
        }
        BackgroundRecoveryDecision::running("local tmux runtime handle is alive", self)
    }

    fn evaluate_local_background(&self) -> BackgroundRecoveryDecision {
        if self.result_artifact_present {
            return BackgroundRecoveryDecision::completed(
                "local background result artifact present",
                self,
            );
        }
        if self.runtime_handle_alive {
            if self.worker_heartbeat_stale {
                return BackgroundRecoveryDecision::stale(
                    BackgroundRunnerPhase::StaleLostCallback,
                    "local background heartbeat is stale",
                    self,
                );
            }
            return BackgroundRecoveryDecision::running("local background callback alive", self);
        }
        if self.provider_launch_spec_reconstructable {
            return BackgroundRecoveryDecision::stale(
                BackgroundRunnerPhase::Reconstructable,
                "local callback missing but provider launch spec can be reconstructed",
                self,
            );
        }
        BackgroundRecoveryDecision::stale(
            BackgroundRunnerPhase::StaleLostCallback,
            "local callback missing after restart",
            self,
        )
    }

    fn evaluate_external(&self, now: DateTime<Utc>) -> BackgroundRecoveryDecision {
        if self.external_result_present || self.result_artifact_present {
            return BackgroundRecoveryDecision::completed("external result received", self);
        }
        if self.external_ack_present {
            return BackgroundRecoveryDecision::running("external pickup acknowledged", self)
                .with_phase(BackgroundRunnerPhase::PickupAcknowledged);
        }
        if let Some(handoff_at) = self.handoff_emitted_at {
            let ack_deadline = handoff_at + Duration::seconds(self.ack_timeout_sec);
            if now >= ack_deadline {
                return BackgroundRecoveryDecision::stale(
                    BackgroundRunnerPhase::StaleNoAck,
                    "external handoff emitted but no pickup ack before timeout",
                    self,
                );
            }
            return BackgroundRecoveryDecision::running("external handoff emitted", self)
                .with_phase(BackgroundRunnerPhase::HandoffEmitted);
        }
        BackgroundRecoveryDecision::running("waiting for external handoff", self)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum NotificationDecision {
    Send {
        previous_suppressed_count: u64,
        cooldown_until: Option<DateTime<Utc>>,
    },
    Suppress {
        suppressed_count: u64,
        cooldown_until: Option<DateTime<Utc>>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BackgroundRecoveryDecision {
    pub phase: BackgroundRunnerPhase,
    pub terminal: bool,
    pub evidence: String,
    pub last_log_tail: Option<String>,
}

impl BackgroundRecoveryDecision {
    fn completed(evidence: &str, probe: &BackgroundProbe) -> Self {
        Self {
            phase: BackgroundRunnerPhase::Completed,
            terminal: true,
            evidence: operator_safe_text(evidence),
            last_log_tail: probe.last_log_tail.as_deref().map(operator_safe_text),
        }
    }

    fn failed(evidence: &str, probe: &BackgroundProbe) -> Self {
        Self {
            phase: BackgroundRunnerPhase::Failed,
            terminal: true,
            evidence: operator_safe_text(evidence),
            last_log_tail: probe.last_log_tail.as_deref().map(operator_safe_text),
        }
    }

    fn stale(phase: BackgroundRunnerPhase, evidence: &str, probe: &BackgroundProbe) -> Self {
        Self {
            phase,
            terminal: false,
            evidence: operator_safe_text(evidence),
            last_log_tail: probe.last_log_tail.as_deref().map(operator_safe_text),
        }
    }

    fn running(evidence: &str, probe: &BackgroundProbe) -> Self {
        Self {
            phase: probe.phase,
            terminal: false,
            evidence: operator_safe_text(evidence),
            last_log_tail: probe.last_log_tail.as_deref().map(operator_safe_text),
        }
    }

    fn with_phase(mut self, phase: BackgroundRunnerPhase) -> Self {
        self.phase = phase;
        self
    }
}

#[derive(Debug, Clone)]
pub struct BackgroundRunStore {
    root: PathBuf,
}

impl BackgroundRunStore {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn path(&self) -> PathBuf {
        self.root.join(BACKGROUND_RUNS_FILE)
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn load(&self) -> Result<Vec<BackgroundProbe>> {
        read_background_runs(&self.path())
    }

    pub fn save(&self, probes: &[BackgroundProbe]) -> Result<()> {
        write_background_runs(&self.path(), probes)
    }

    pub fn upsert(&self, probe: BackgroundProbe) -> Result<()> {
        let mut probes = self.load()?;
        if let Some(existing) = probes
            .iter_mut()
            .find(|existing| existing.ticket_id == probe.ticket_id)
        {
            *existing = probe;
        } else {
            probes.push(probe);
        }
        self.save(&probes)
    }
}

fn default_ack_timeout_sec() -> i64 {
    300
}

fn default_heartbeat_timeout_sec() -> i64 {
    900
}

fn read_background_runs(path: &Path) -> Result<Vec<BackgroundProbe>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)?;
    if content.trim().is_empty() {
        return Ok(Vec::new());
    }
    Ok(serde_json::from_str(&content)?)
}

fn write_background_runs(path: &Path, probes: &[BackgroundProbe]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(path, serde_json::to_string_pretty(probes)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn local_tmux_missing_without_result_marks_failed() {
        let probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalTmux);
        let decision = probe.evaluate(Utc::now());
        assert_eq!(decision.phase, BackgroundRunnerPhase::Failed);
        assert!(decision.terminal);
        assert!(decision.evidence.contains("tmux session missing"));
    }

    #[test]
    fn local_tmux_result_sidecar_marks_completed() {
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalTmux);
        probe.result_artifact_present = true;
        let decision = probe.evaluate(Utc::now());
        assert_eq!(decision.phase, BackgroundRunnerPhase::Completed);
        assert!(decision.terminal);
    }

    #[test]
    fn external_handoff_without_ack_becomes_stale_after_timeout() {
        let now = Utc::now();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::RemoteWorker);
        probe.handoff_emitted_at = Some(now - Duration::seconds(600));
        probe.ack_timeout_sec = 300;

        let decision = probe.evaluate(now);

        assert_eq!(decision.phase, BackgroundRunnerPhase::StaleNoAck);
    }

    #[test]
    fn external_ack_updates_phase_to_pickup_acknowledged() {
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::GithubRunner);
        probe.external_ack_present = true;
        let decision = probe.evaluate(Utc::now());
        assert_eq!(decision.phase, BackgroundRunnerPhase::PickupAcknowledged);
    }

    #[test]
    fn local_background_stale_heartbeat_marks_stale_even_when_handle_alive() {
        let now = Utc::now();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);
        probe.runtime_handle_alive = true;
        probe.worker_heartbeat_stale = true;

        let decision = probe.evaluate(now);

        assert_eq!(decision.phase, BackgroundRunnerPhase::StaleLostCallback);
        assert!(!decision.terminal);
        assert!(decision.evidence.contains("heartbeat is stale"));
    }

    #[test]
    fn notification_cooldown_suppresses_repeated_alerts() {
        let now = Utc::now();
        let mut probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalBackground);

        assert!(matches!(
            probe.record_notification_attempt(now, Duration::minutes(5)),
            NotificationDecision::Send { .. }
        ));
        assert!(matches!(
            probe.record_notification_attempt(now + Duration::minutes(1), Duration::minutes(5)),
            NotificationDecision::Suppress {
                suppressed_count: 1,
                ..
            }
        ));
        assert!(matches!(
            probe.record_notification_attempt(now + Duration::minutes(6), Duration::minutes(5)),
            NotificationDecision::Send {
                previous_suppressed_count: 1,
                ..
            }
        ));
    }

    #[test]
    fn background_store_roundtrips_canonical_file() -> Result<()> {
        let temp = tempdir()?;
        let store = BackgroundRunStore::new(temp.path());
        let probe = BackgroundProbe::new("ticket", BackgroundRunnerKind::LocalTmux);
        store.save(std::slice::from_ref(&probe))?;
        assert_eq!(store.load()?, vec![probe]);
        Ok(())
    }
}
