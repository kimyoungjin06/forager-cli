//! Scheduler-facing offdesk execution gate.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use super::approval::{
    ActionApprovalRequest, ApprovalDecision, ApprovalLedger, ApprovalLedgerSession, ApprovalMode,
    ExecutionBrief, PendingActionApproval,
};
use super::capability::{default_capability_registry, CapabilityDescriptor, CapabilityRegistry};
use super::redaction::operator_safe_text;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchedulerGateRequest {
    pub capability_id: String,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub mutation_class: Option<String>,
    pub preview: String,
    pub reason: String,
    pub source_surface: String,
    pub ttl: Duration,
}

impl SchedulerGateRequest {
    pub fn new(
        capability_id: impl Into<String>,
        project_key: impl Into<String>,
        request_id: impl Into<String>,
        task_id: impl Into<String>,
    ) -> Self {
        Self {
            capability_id: capability_id.into(),
            project_key: project_key.into(),
            request_id: request_id.into(),
            task_id: task_id.into(),
            mutation_class: None,
            preview: String::new(),
            reason: String::new(),
            source_surface: "offdesk.scheduler".to_string(),
            ttl: Duration::minutes(30),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SchedulerGateStatus {
    Proceed,
    PendingApproval,
    Denied,
    Blocked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SchedulerGateOutcome {
    pub status: SchedulerGateStatus,
    pub capability_id: String,
    pub risk_level: String,
    pub approval_mode: ApprovalMode,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approval: Option<PendingActionApproval>,
    pub reason: String,
    pub scheduler_may_continue_other_work: bool,
}

impl SchedulerGateOutcome {
    pub fn can_execute_requested_action(&self) -> bool {
        self.status == SchedulerGateStatus::Proceed
    }
}

#[derive(Debug, Clone)]
pub struct SchedulerGate {
    registry: CapabilityRegistry,
    ledger: ApprovalLedger,
}

impl SchedulerGate {
    pub fn new(ledger: ApprovalLedger) -> Self {
        Self {
            registry: default_capability_registry(),
            ledger,
        }
    }

    pub fn with_registry(registry: CapabilityRegistry, ledger: ApprovalLedger) -> Self {
        Self { registry, ledger }
    }

    pub fn evaluate(
        &self,
        request: SchedulerGateRequest,
        brief: Option<&ExecutionBrief>,
        now: DateTime<Utc>,
    ) -> Result<SchedulerGateOutcome> {
        let Some(capability) = self.registry.get(&request.capability_id) else {
            return Ok(blocked_outcome(
                &request.capability_id,
                "unknown",
                ApprovalMode::PolicyDenied,
                format!("unknown capability: {}", request.capability_id),
            ));
        };

        if !capability.offdesk_allowed {
            return Ok(blocked_outcome(
                &capability.capability_id,
                &format!("{:?}", capability.risk_level).to_lowercase(),
                ApprovalMode::PolicyDenied,
                format!(
                    "{} is not allowed in offdesk mode",
                    capability.capability_id
                ),
            ));
        }

        let approval_request = approval_request_from_capability(&request, capability);
        if self.ledger.denied_matches(&approval_request)? {
            return Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::OperatorRequired,
                approval: None,
                reason: operator_safe_text(
                    "matching action was previously denied and needs a new approval object",
                ),
                scheduler_may_continue_other_work: true,
            });
        }

        match self.ledger.evaluate_action(approval_request, brief, now)? {
            ApprovalDecision::Proceed(mode) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Proceed,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: mode,
                approval: None,
                reason: "capability gate passed".to_string(),
                scheduler_may_continue_other_work: true,
            }),
            ApprovalDecision::Pending(approval) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::PendingApproval,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: approval.approval_mode,
                approval: Some(*approval),
                reason: "operator approval required".to_string(),
                scheduler_may_continue_other_work: true,
            }),
            ApprovalDecision::Denied(reason) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::PolicyDenied,
                approval: None,
                reason: operator_safe_text(&reason),
                scheduler_may_continue_other_work: true,
            }),
        }
    }

    pub fn evaluate_with_session(
        &self,
        request: SchedulerGateRequest,
        brief: Option<&ExecutionBrief>,
        now: DateTime<Utc>,
        approvals: &mut ApprovalLedgerSession,
    ) -> Result<SchedulerGateOutcome> {
        let Some(capability) = self.registry.get(&request.capability_id) else {
            return Ok(blocked_outcome(
                &request.capability_id,
                "unknown",
                ApprovalMode::PolicyDenied,
                format!("unknown capability: {}", request.capability_id),
            ));
        };

        if !capability.offdesk_allowed {
            return Ok(blocked_outcome(
                &capability.capability_id,
                &format!("{:?}", capability.risk_level).to_lowercase(),
                ApprovalMode::PolicyDenied,
                format!(
                    "{} is not allowed in offdesk mode",
                    capability.capability_id
                ),
            ));
        }

        let approval_request = approval_request_from_capability(&request, capability);
        if approvals.denied_matches(&approval_request) {
            return Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::OperatorRequired,
                approval: None,
                reason: operator_safe_text(
                    "matching action was previously denied and needs a new approval object",
                ),
                scheduler_may_continue_other_work: true,
            });
        }

        match approvals.evaluate_action(approval_request, brief, now)? {
            ApprovalDecision::Proceed(mode) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Proceed,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: mode,
                approval: None,
                reason: "capability gate passed".to_string(),
                scheduler_may_continue_other_work: true,
            }),
            ApprovalDecision::Pending(approval) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::PendingApproval,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: approval.approval_mode,
                approval: Some(*approval),
                reason: "operator approval required".to_string(),
                scheduler_may_continue_other_work: true,
            }),
            ApprovalDecision::Denied(reason) => Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::PolicyDenied,
                approval: None,
                reason: operator_safe_text(&reason),
                scheduler_may_continue_other_work: true,
            }),
        }
    }
}

fn approval_request_from_capability(
    request: &SchedulerGateRequest,
    capability: &CapabilityDescriptor,
) -> ActionApprovalRequest {
    let mut approval_request = ActionApprovalRequest::new(
        &request.project_key,
        &request.request_id,
        &request.task_id,
        &request.capability_id,
        capability.risk_level.into(),
    );
    approval_request.mutation_class = request
        .mutation_class
        .clone()
        .or_else(|| Some(request.capability_id.clone()));
    approval_request.preview = request.preview.clone();
    approval_request.reason = request.reason.clone();
    approval_request.source_surface = request.source_surface.clone();
    approval_request.ttl = request.ttl;
    approval_request
}

fn blocked_outcome(
    capability_id: &str,
    risk_level: &str,
    approval_mode: ApprovalMode,
    reason: impl AsRef<str>,
) -> SchedulerGateOutcome {
    SchedulerGateOutcome {
        status: SchedulerGateStatus::Blocked,
        capability_id: capability_id.to_string(),
        risk_level: risk_level.to_string(),
        approval_mode,
        approval: None,
        reason: operator_safe_text(reason.as_ref()),
        scheduler_may_continue_other_work: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn safe_capability_proceeds_without_approval() -> Result<()> {
        let temp = tempdir()?;
        let gate = SchedulerGate::new(ApprovalLedger::new(temp.path()));
        let request = SchedulerGateRequest::new("inspect.status", "project", "request", "task");

        let outcome = gate.evaluate(request, None, Utc::now())?;

        assert_eq!(outcome.status, SchedulerGateStatus::Proceed);
        assert!(outcome.can_execute_requested_action());
        Ok(())
    }

    #[test]
    fn runtime_mutation_inside_execution_brief_proceeds() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let gate = SchedulerGate::new(ApprovalLedger::new(temp.path()));
        let request = SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        let brief = ExecutionBrief {
            request_id: "request".to_string(),
            task_id: "task".to_string(),
            project_key: "project".to_string(),
            approved: true,
            allowed_runtime_mutations: vec!["dispatch.runtime".to_string()],
            allowed_canonical_mutations: vec![],
            fresh_until: Some(now + Duration::minutes(5)),
        };

        let outcome = gate.evaluate(request, Some(&brief), now)?;

        assert_eq!(outcome.status, SchedulerGateStatus::Proceed);
        assert!(ApprovalLedger::new(temp.path()).load()?.is_empty());
        Ok(())
    }

    #[test]
    fn runtime_mutation_outside_envelope_creates_pending_approval() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let gate = SchedulerGate::new(ledger.clone());
        let mut request =
            SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        request.preview = "token=sk-secretsecretsecretsecret".to_string();

        let outcome = gate.evaluate(request, None, Utc::now())?;

        assert_eq!(outcome.status, SchedulerGateStatus::PendingApproval);
        assert_eq!(ledger.load()?.len(), 1);
        assert!(!outcome
            .approval
            .expect("approval")
            .preview
            .contains("sk-secret"));
        Ok(())
    }

    #[test]
    fn denied_action_is_not_retried_without_new_approval_object() -> Result<()> {
        let temp = tempdir()?;
        let ledger = ApprovalLedger::new(temp.path());
        let gate = SchedulerGate::new(ledger.clone());
        let request = SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        let now = Utc::now();

        let first = gate.evaluate(request.clone(), None, now)?;
        let approval_id = first.approval.expect("approval").approval_id;
        ledger.deny_pending(Some(&approval_id), "operator", now + Duration::seconds(1))?;

        let second = gate.evaluate(request, None, now + Duration::seconds(2))?;

        assert_eq!(second.status, SchedulerGateStatus::Denied);
        assert_eq!(ledger.load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn offdesk_blocks_disallowed_capability() -> Result<()> {
        let temp = tempdir()?;
        let gate = SchedulerGate::new(ApprovalLedger::new(temp.path()));
        let request = SchedulerGateRequest::new("canonical.apply", "project", "request", "task");

        let outcome = gate.evaluate(request, None, Utc::now())?;

        assert_eq!(outcome.status, SchedulerGateStatus::Blocked);
        assert_eq!(outcome.approval_mode, ApprovalMode::PolicyDenied);
        Ok(())
    }
}
