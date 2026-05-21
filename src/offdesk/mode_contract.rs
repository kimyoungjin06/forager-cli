//! Operator-facing Offdesk mode contract assessment.
//!
//! This is a derived surface. It does not grant authority or mutate persisted
//! task state; it only summarizes whether the current task/probe state is
//! enough for an operator to trust the selected agent mode.

use serde::Serialize;

use super::adaptive_wiki::AdaptiveWikiAgentMode;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskModeVerdict {
    Unscoped,
    Pending,
    Running,
    EvidenceReady,
    ReviewReady,
    CompletionUnverified,
    Blocked,
    Cancelled,
}

impl OffdeskModeVerdict {
    pub fn label(self) -> &'static str {
        match self {
            Self::Unscoped => "unscoped",
            Self::Pending => "pending",
            Self::Running => "running",
            Self::EvidenceReady => "evidence_ready",
            Self::ReviewReady => "review_ready",
            Self::CompletionUnverified => "completion_unverified",
            Self::Blocked => "blocked",
            Self::Cancelled => "cancelled",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskModeRisk {
    MissingAgentMode,
    AwaitingLaunch,
    AwaitingRuntimeEvidence,
    OperatorReviewRequired,
    MissingResultArtifact,
    RuntimeRecoveryRequired,
    Cancelled,
    None,
}

impl OffdeskModeRisk {
    pub fn label(self) -> &'static str {
        match self {
            Self::MissingAgentMode => "missing_agent_mode",
            Self::AwaitingLaunch => "awaiting_launch",
            Self::AwaitingRuntimeEvidence => "awaiting_runtime_evidence",
            Self::OperatorReviewRequired => "operator_review_required",
            Self::MissingResultArtifact => "missing_result_artifact",
            Self::RuntimeRecoveryRequired => "runtime_recovery_required",
            Self::Cancelled => "cancelled",
            Self::None => "none",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskModeAssessment {
    pub mode_verdict: OffdeskModeVerdict,
    pub mode_risk: OffdeskModeRisk,
    pub mode_risk_detail: String,
    pub review_stage_required: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OffdeskModeLifecycle {
    Pending,
    Running,
    CompletedWithResult,
    CompletedWithoutResult,
    Blocked,
    Cancelled,
}

pub fn assess_offdesk_mode(
    agent_mode: Option<AdaptiveWikiAgentMode>,
    lifecycle: OffdeskModeLifecycle,
) -> OffdeskModeAssessment {
    let review_stage_required = mode_requires_separate_review(agent_mode);
    let Some(agent_mode) = agent_mode else {
        return OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::Unscoped,
            mode_risk: OffdeskModeRisk::MissingAgentMode,
            mode_risk_detail:
                "No agent_mode was set, so mode-specific contract checks cannot be scoped."
                    .to_string(),
            review_stage_required,
        };
    };

    match lifecycle {
        OffdeskModeLifecycle::Pending => OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::Pending,
            mode_risk: OffdeskModeRisk::AwaitingLaunch,
            mode_risk_detail: "Mode is selected, but runtime evidence does not exist yet."
                .to_string(),
            review_stage_required,
        },
        OffdeskModeLifecycle::Running => OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::Running,
            mode_risk: OffdeskModeRisk::AwaitingRuntimeEvidence,
            mode_risk_detail: "Mode-scoped work is still running; inspect artifacts before claims."
                .to_string(),
            review_stage_required,
        },
        OffdeskModeLifecycle::CompletedWithResult => {
            if agent_mode == AdaptiveWikiAgentMode::Review {
                OffdeskModeAssessment {
                    mode_verdict: OffdeskModeVerdict::ReviewReady,
                    mode_risk: OffdeskModeRisk::None,
                    mode_risk_detail:
                        "Review-mode task has a result artifact for operator inspection."
                            .to_string(),
                    review_stage_required,
                }
            } else {
                OffdeskModeAssessment {
                    mode_verdict: OffdeskModeVerdict::EvidenceReady,
                    mode_risk: OffdeskModeRisk::OperatorReviewRequired,
                    mode_risk_detail:
                        "Result artifact exists; separate review is still required before relying on the work."
                            .to_string(),
                    review_stage_required,
                }
            }
        }
        OffdeskModeLifecycle::CompletedWithoutResult => OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::CompletionUnverified,
            mode_risk: OffdeskModeRisk::MissingResultArtifact,
            mode_risk_detail:
                "Task is completed, but no result artifact path is available for mode review."
                    .to_string(),
            review_stage_required,
        },
        OffdeskModeLifecycle::Blocked => OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::Blocked,
            mode_risk: OffdeskModeRisk::RuntimeRecoveryRequired,
            mode_risk_detail:
                "Runtime failed or went stale; inspect recovery evidence before retrying."
                    .to_string(),
            review_stage_required,
        },
        OffdeskModeLifecycle::Cancelled => OffdeskModeAssessment {
            mode_verdict: OffdeskModeVerdict::Cancelled,
            mode_risk: OffdeskModeRisk::Cancelled,
            mode_risk_detail: "Task was cancelled before the mode contract could complete."
                .to_string(),
            review_stage_required,
        },
    }
}

pub fn mode_requires_separate_review(agent_mode: Option<AdaptiveWikiAgentMode>) -> bool {
    matches!(
        agent_mode,
        Some(
            AdaptiveWikiAgentMode::Planning
                | AdaptiveWikiAgentMode::Development
                | AdaptiveWikiAgentMode::Analysis
                | AdaptiveWikiAgentMode::Writing
                | AdaptiveWikiAgentMode::Critique
                | AdaptiveWikiAgentMode::Maintenance
        )
    )
}
