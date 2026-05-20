//! Scheduler-facing offdesk execution gate.

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

use super::adaptive_wiki::{
    AdaptiveWikiAgentMode, AdaptiveWikiAgentModeFilter, AdaptiveWikiAiProjection,
    AdaptiveWikiProjectionBudget, AdaptiveWikiProjectionPolicy,
    AdaptiveWikiProjectionReviewExpiredPolicy, AdaptiveWikiQuery,
    AdaptiveWikiRuntimePolicyDecision, AdaptiveWikiStore,
};
use super::approval::{
    ActionApprovalRequest, ApprovalDecision, ApprovalLedger, ApprovalLedgerSession, ApprovalMode,
    ExecutionBrief, PendingActionApproval,
};
use super::capability::{
    default_capability_registry, CapabilityArtifactCheck, CapabilityArtifactRef,
    CapabilityDescriptor, CapabilityRegistry,
};
use super::provider::{
    recommend_provider_fallback, ProviderCapacityState, ProviderCapacityStatus,
    ProviderCapacityStore, ProviderFallbackRecommendation,
};
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
    pub artifact_refs: Vec<CapabilityArtifactRef>,
    pub provider_id: Option<String>,
    pub model: Option<String>,
    pub runner_role: String,
    pub artifact_kind: Option<String>,
    pub agent_mode: Option<AdaptiveWikiAgentMode>,
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
            artifact_refs: Vec::new(),
            provider_id: None,
            model: None,
            runner_role: "worker".to_string(),
            artifact_kind: None,
            agent_mode: None,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artifact_check: Option<CapabilityArtifactCheck>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_capacity: Option<ProviderCapacityGateSummary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider_fallback: Option<ProviderFallbackRecommendation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub retry_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub adaptive_wiki: Vec<AdaptiveWikiAiProjection>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub adaptive_wiki_runtime: Vec<AdaptiveWikiAiProjection>,
    #[serde(default)]
    pub adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub adaptive_wiki_runtime_decision: Option<AdaptiveWikiRuntimePolicyDecision>,
    pub reason: String,
    pub scheduler_may_continue_other_work: bool,
}

impl SchedulerGateOutcome {
    pub fn can_execute_requested_action(&self) -> bool {
        self.status == SchedulerGateStatus::Proceed
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProviderCapacityGateSummary {
    pub provider_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
    pub matched_scope: String,
    pub status: ProviderCapacityStatus,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cooldown_until: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_error_summary: Option<String>,
}

#[derive(Debug, Clone)]
pub struct SchedulerGate {
    registry: CapabilityRegistry,
    ledger: ApprovalLedger,
    provider_capacity: Option<ProviderCapacityStore>,
    adaptive_wiki: Option<AdaptiveWikiStore>,
}

impl SchedulerGate {
    pub fn new(ledger: ApprovalLedger) -> Self {
        Self {
            registry: default_capability_registry(),
            ledger,
            provider_capacity: None,
            adaptive_wiki: None,
        }
    }

    pub fn with_registry(registry: CapabilityRegistry, ledger: ApprovalLedger) -> Self {
        Self {
            registry,
            ledger,
            provider_capacity: None,
            adaptive_wiki: None,
        }
    }

    pub fn with_provider_capacity(
        ledger: ApprovalLedger,
        provider_capacity: ProviderCapacityStore,
    ) -> Self {
        Self {
            registry: default_capability_registry(),
            ledger,
            provider_capacity: Some(provider_capacity),
            adaptive_wiki: None,
        }
    }

    pub fn with_registry_and_provider_capacity(
        registry: CapabilityRegistry,
        ledger: ApprovalLedger,
        provider_capacity: ProviderCapacityStore,
    ) -> Self {
        Self {
            registry,
            ledger,
            provider_capacity: Some(provider_capacity),
            adaptive_wiki: None,
        }
    }

    pub fn with_adaptive_wiki(mut self, adaptive_wiki: AdaptiveWikiStore) -> Self {
        self.adaptive_wiki = Some(adaptive_wiki);
        self
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
        let artifact_check = capability.validate_required_artifacts(&request.artifact_refs);
        if !artifact_check.satisfied {
            return Ok(blocked_outcome_with_artifacts(
                &capability.capability_id,
                &format!("{:?}", capability.risk_level).to_lowercase(),
                ApprovalMode::PolicyDenied,
                format!(
                    "missing required artifacts: {}",
                    artifact_check.missing_artifact_ids.join(", ")
                ),
                artifact_check,
            ));
        }
        if let Some(outcome) =
            self.provider_capacity_outcome(&request, capability, Some(artifact_check.clone()), now)?
        {
            return Ok(outcome);
        }

        let approval_request = approval_request_from_capability(&request, capability);
        if self.ledger.denied_matches(&approval_request)? {
            return Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::OperatorRequired,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: operator_safe_text(
                    "matching action was previously denied and needs a new approval object",
                ),
                scheduler_may_continue_other_work: true,
            });
        }

        let outcome = match self.ledger.evaluate_action(approval_request, brief, now)? {
            ApprovalDecision::Proceed(mode) => SchedulerGateOutcome {
                status: SchedulerGateStatus::Proceed,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: mode,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: "capability gate passed".to_string(),
                scheduler_may_continue_other_work: true,
            },
            ApprovalDecision::Pending(approval) => SchedulerGateOutcome {
                status: SchedulerGateStatus::PendingApproval,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: approval.approval_mode,
                approval: Some(*approval),
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: "operator approval required".to_string(),
                scheduler_may_continue_other_work: true,
            },
            ApprovalDecision::Denied(reason) => SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::PolicyDenied,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: operator_safe_text(&reason),
                scheduler_may_continue_other_work: true,
            },
        };
        self.with_adaptive_wiki_outcome(outcome, &request, now)
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
        let artifact_check = capability.validate_required_artifacts(&request.artifact_refs);
        if !artifact_check.satisfied {
            return Ok(blocked_outcome_with_artifacts(
                &capability.capability_id,
                &format!("{:?}", capability.risk_level).to_lowercase(),
                ApprovalMode::PolicyDenied,
                format!(
                    "missing required artifacts: {}",
                    artifact_check.missing_artifact_ids.join(", ")
                ),
                artifact_check,
            ));
        }
        if let Some(outcome) =
            self.provider_capacity_outcome(&request, capability, Some(artifact_check.clone()), now)?
        {
            return Ok(outcome);
        }

        let approval_request = approval_request_from_capability(&request, capability);
        if approvals.denied_matches(&approval_request) {
            return Ok(SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::OperatorRequired,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: operator_safe_text(
                    "matching action was previously denied and needs a new approval object",
                ),
                scheduler_may_continue_other_work: true,
            });
        }

        let outcome = match approvals.evaluate_action(approval_request, brief, now)? {
            ApprovalDecision::Proceed(mode) => SchedulerGateOutcome {
                status: SchedulerGateStatus::Proceed,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: mode,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: "capability gate passed".to_string(),
                scheduler_may_continue_other_work: true,
            },
            ApprovalDecision::Pending(approval) => SchedulerGateOutcome {
                status: SchedulerGateStatus::PendingApproval,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: approval.approval_mode,
                approval: Some(*approval),
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: "operator approval required".to_string(),
                scheduler_may_continue_other_work: true,
            },
            ApprovalDecision::Denied(reason) => SchedulerGateOutcome {
                status: SchedulerGateStatus::Denied,
                capability_id: capability.capability_id.clone(),
                risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
                approval_mode: ApprovalMode::PolicyDenied,
                approval: None,
                artifact_check: Some(artifact_check),
                provider_capacity: None,
                provider_fallback: None,
                retry_at: None,
                adaptive_wiki: Vec::new(),
                adaptive_wiki_runtime: Vec::new(),
                adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
                adaptive_wiki_runtime_decision: None,
                reason: operator_safe_text(&reason),
                scheduler_may_continue_other_work: true,
            },
        };
        self.with_adaptive_wiki_outcome(outcome, &request, now)
    }

    fn with_adaptive_wiki_outcome(
        &self,
        mut outcome: SchedulerGateOutcome,
        request: &SchedulerGateRequest,
        now: DateTime<Utc>,
    ) -> Result<SchedulerGateOutcome> {
        let Some(store) = self.adaptive_wiki.as_ref() else {
            return Ok(outcome);
        };
        let query = AdaptiveWikiQuery {
            // Offdesk does not yet have Hermes' conversational session id, so
            // request_id is the bounded session-like scope for preflight.
            session_id: Some(request.request_id.clone()),
            project_key: Some(request.project_key.clone()),
            artifact_kind: request.artifact_kind.clone(),
            agent_mode: request.agent_mode,
            agent_mode_filter: AdaptiveWikiAgentModeFilter::SharedWhenUnspecified,
        };
        let budget = AdaptiveWikiProjectionBudget::default();
        let report = store.ai_projection_report(&query, budget.clone())?;
        let runtime = store.runtime_projection_with_policy_acknowledgement(
            &query,
            budget,
            requested_adaptive_wiki_runtime_policy(),
            now,
        )?;
        outcome.adaptive_wiki = report.selected.clone();
        outcome.adaptive_wiki_runtime = runtime
            .report
            .as_ref()
            .map(|report| report.selected.clone())
            .unwrap_or_default();
        outcome.adaptive_wiki_runtime_policy = runtime
            .decision
            .applied_policy
            .unwrap_or(runtime.decision.requested_policy);
        outcome.adaptive_wiki_runtime_decision = Some(runtime.decision);
        Ok(outcome)
    }

    fn provider_capacity_outcome(
        &self,
        request: &SchedulerGateRequest,
        capability: &CapabilityDescriptor,
        artifact_check: Option<CapabilityArtifactCheck>,
        now: DateTime<Utc>,
    ) -> Result<Option<SchedulerGateOutcome>> {
        let Some(store) = &self.provider_capacity else {
            return Ok(None);
        };
        let Some(provider_id) = request
            .provider_id
            .as_deref()
            .map(str::trim)
            .filter(|provider_id| !provider_id.is_empty())
        else {
            return Ok(None);
        };
        let model = request
            .model
            .as_deref()
            .map(str::trim)
            .filter(|model| !model.is_empty());
        let Some(state) = store.scheduling_match(provider_id, model)? else {
            return Ok(None);
        };
        if !state.is_cooling_down_at(now) {
            return Ok(None);
        }
        let retry_at = state.cooldown_until;
        let summary = provider_capacity_summary(state, model);
        let provider_fallback = recommend_provider_fallback(
            store,
            provider_id,
            model,
            "provider capacity cooldown active",
            &request.runner_role,
            now,
        )?;
        Ok(Some(SchedulerGateOutcome {
            status: SchedulerGateStatus::Blocked,
            capability_id: capability.capability_id.clone(),
            risk_level: format!("{:?}", capability.risk_level).to_lowercase(),
            approval_mode: ApprovalMode::PolicyDenied,
            approval: None,
            artifact_check,
            provider_capacity: Some(summary),
            provider_fallback: Some(provider_fallback),
            retry_at,
            adaptive_wiki: Vec::new(),
            adaptive_wiki_runtime: Vec::new(),
            adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
            adaptive_wiki_runtime_decision: None,
            reason: operator_safe_text("provider capacity cooldown active"),
            scheduler_may_continue_other_work: true,
        }))
    }
}

pub fn is_provider_capacity_block(outcome: &SchedulerGateOutcome) -> bool {
    outcome.status == SchedulerGateStatus::Blocked
        && outcome.provider_capacity.is_some()
        && outcome.retry_at.is_some()
}

fn requested_adaptive_wiki_runtime_policy() -> AdaptiveWikiProjectionPolicy {
    let review_expired = std::env::var("FORAGER_ADAPTIVE_WIKI_RUNTIME_REVIEW_EXPIRED")
        .ok()
        .map(|value| match value.trim().to_ascii_lowercase().as_str() {
            "exclude" | "strict" | "strict_exclude" | "strict-review-expired" => {
                AdaptiveWikiProjectionReviewExpiredPolicy::Exclude
            }
            _ => AdaptiveWikiProjectionReviewExpiredPolicy::Warn,
        })
        .unwrap_or(AdaptiveWikiProjectionReviewExpiredPolicy::Warn);
    AdaptiveWikiProjectionPolicy { review_expired }
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

fn provider_capacity_summary(
    state: ProviderCapacityState,
    requested_model: Option<&str>,
) -> ProviderCapacityGateSummary {
    let matched_scope = if requested_model.is_some() && state.model.is_some() {
        "provider_model"
    } else {
        "provider"
    };
    ProviderCapacityGateSummary {
        provider_id: operator_safe_text(&state.provider_id),
        model: state.model.map(|model| operator_safe_text(&model)),
        matched_scope: matched_scope.to_string(),
        status: state.status,
        reason: format!("{:?}", state.reason).to_lowercase(),
        cooldown_until: state.cooldown_until,
        last_error_summary: state.last_error_summary.as_deref().map(operator_safe_text),
    }
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
        artifact_check: None,
        provider_capacity: None,
        provider_fallback: None,
        retry_at: None,
        adaptive_wiki: Vec::new(),
        adaptive_wiki_runtime: Vec::new(),
        adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
        adaptive_wiki_runtime_decision: None,
        reason: operator_safe_text(reason.as_ref()),
        scheduler_may_continue_other_work: true,
    }
}

fn blocked_outcome_with_artifacts(
    capability_id: &str,
    risk_level: &str,
    approval_mode: ApprovalMode,
    reason: impl AsRef<str>,
    artifact_check: CapabilityArtifactCheck,
) -> SchedulerGateOutcome {
    SchedulerGateOutcome {
        status: SchedulerGateStatus::Blocked,
        capability_id: capability_id.to_string(),
        risk_level: risk_level.to_string(),
        approval_mode,
        approval: None,
        artifact_check: Some(artifact_check),
        provider_capacity: None,
        provider_fallback: None,
        retry_at: None,
        adaptive_wiki: Vec::new(),
        adaptive_wiki_runtime: Vec::new(),
        adaptive_wiki_runtime_policy: AdaptiveWikiProjectionPolicy::default(),
        adaptive_wiki_runtime_decision: None,
        reason: operator_safe_text(reason.as_ref()),
        scheduler_may_continue_other_work: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::provider::ProviderErrorReason;
    use tempfile::tempdir;

    fn capacity_state(
        provider_id: &str,
        model: Option<&str>,
        cooldown_until: DateTime<Utc>,
        now: DateTime<Utc>,
    ) -> ProviderCapacityState {
        ProviderCapacityState {
            provider_id: provider_id.to_string(),
            model: model.map(str::to_string),
            status: ProviderCapacityStatus::CoolingDown,
            reason: ProviderErrorReason::RateLimit,
            cooldown_until: Some(cooldown_until),
            last_error_summary: Some("rate limit".to_string()),
            updated_at: now,
        }
    }

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

    #[test]
    fn required_artifacts_block_before_approval() -> Result<()> {
        let temp = tempdir()?;
        let gate = SchedulerGate::new(ApprovalLedger::new(temp.path()));
        let request = SchedulerGateRequest::new("canonical.syncback", "project", "request", "task");

        let outcome = gate.evaluate(request, None, Utc::now())?;

        assert_eq!(outcome.status, SchedulerGateStatus::Blocked);
        assert!(outcome.reason.contains("mutation_snapshot"));
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 0);
        Ok(())
    }

    #[test]
    fn supplied_required_artifacts_allow_approval_flow() -> Result<()> {
        let temp = tempdir()?;
        let gate = SchedulerGate::new(ApprovalLedger::new(temp.path()));
        let mut request =
            SchedulerGateRequest::new("canonical.syncback", "project", "request", "task");
        request.artifact_refs = vec![CapabilityArtifactRef {
            artifact_id: "mutation_snapshot".to_string(),
            path: None,
            present: true,
        }];

        let outcome = gate.evaluate(request, None, Utc::now())?;

        assert_eq!(outcome.status, SchedulerGateStatus::PendingApproval);
        assert!(outcome
            .artifact_check
            .as_ref()
            .is_some_and(|check| check.satisfied));
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn provider_model_cooldown_blocks_before_approval() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        ProviderCapacityStore::new(temp.path()).upsert(capacity_state(
            "openai",
            Some("gpt-4.1"),
            now + Duration::minutes(2),
            now,
        ))?;
        let gate = SchedulerGate::with_provider_capacity(
            ApprovalLedger::new(temp.path()),
            ProviderCapacityStore::new(temp.path()),
        );
        let mut request =
            SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        request.provider_id = Some("openai".to_string());
        request.model = Some("gpt-4.1".to_string());

        let outcome = gate.evaluate(request, None, now)?;

        assert_eq!(outcome.status, SchedulerGateStatus::Blocked);
        assert_eq!(outcome.retry_at, Some(now + Duration::minutes(2)));
        assert_eq!(
            outcome
                .provider_capacity
                .as_ref()
                .expect("capacity")
                .matched_scope,
            "provider_model"
        );
        let fallback = outcome.provider_fallback.as_ref().expect("fallback");
        assert_eq!(fallback.current_provider_id, "openai");
        assert_eq!(fallback.current_model.as_deref(), Some("gpt-4.1"));
        assert!(fallback
            .candidates
            .iter()
            .all(|candidate| !(candidate.provider_id == "openai"
                && candidate.model.as_deref() == Some("gpt-4.1"))));
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 0);
        Ok(())
    }

    #[test]
    fn provider_wide_cooldown_blocks_model_specific_request() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        ProviderCapacityStore::new(temp.path()).upsert(capacity_state(
            "openai",
            None,
            now + Duration::minutes(1),
            now,
        ))?;
        let gate = SchedulerGate::with_provider_capacity(
            ApprovalLedger::new(temp.path()),
            ProviderCapacityStore::new(temp.path()),
        );
        let mut request =
            SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        request.provider_id = Some("openai".to_string());
        request.model = Some("gpt-4.1".to_string());

        let outcome = gate.evaluate(request, None, now)?;

        assert_eq!(outcome.status, SchedulerGateStatus::Blocked);
        assert_eq!(
            outcome
                .provider_capacity
                .as_ref()
                .expect("capacity")
                .matched_scope,
            "provider"
        );
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 0);
        Ok(())
    }

    #[test]
    fn expired_provider_cooldown_does_not_block_gate() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        ProviderCapacityStore::new(temp.path()).upsert(capacity_state(
            "openai",
            Some("gpt-4.1"),
            now - Duration::seconds(1),
            now - Duration::minutes(1),
        ))?;
        let gate = SchedulerGate::with_provider_capacity(
            ApprovalLedger::new(temp.path()),
            ProviderCapacityStore::new(temp.path()),
        );
        let mut request =
            SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");
        request.provider_id = Some("openai".to_string());
        request.model = Some("gpt-4.1".to_string());

        let outcome = gate.evaluate(request, None, now)?;

        assert_eq!(outcome.status, SchedulerGateStatus::PendingApproval);
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 1);
        Ok(())
    }

    #[test]
    fn missing_provider_info_keeps_existing_gate_behavior() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        ProviderCapacityStore::new(temp.path()).upsert(capacity_state(
            "openai",
            None,
            now + Duration::minutes(1),
            now,
        ))?;
        let gate = SchedulerGate::with_provider_capacity(
            ApprovalLedger::new(temp.path()),
            ProviderCapacityStore::new(temp.path()),
        );
        let request = SchedulerGateRequest::new("dispatch.runtime", "project", "request", "task");

        let outcome = gate.evaluate(request, None, now)?;

        assert_eq!(outcome.status, SchedulerGateStatus::PendingApproval);
        assert_eq!(ApprovalLedger::new(temp.path()).load()?.len(), 1);
        Ok(())
    }
}
