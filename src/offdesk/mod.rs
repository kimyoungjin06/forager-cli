//! Offdesk orchestration safety rails and durable artifacts.
//!
//! This module keeps the canonical state in AOE-owned JSON artifacts. The
//! helpers are intentionally side-effect-light so the scheduler, dashboard,
//! Telegram bridge, and future worker backends can share the same policy logic.

pub mod adaptive_wiki;
pub mod approval;
pub mod background;
pub mod capability;
pub mod control_loop;
pub mod mode_contract;
pub mod mutation;
pub mod provider;
pub mod redaction;
pub mod resume;
pub mod runner;
pub mod scheduler;
pub mod task_queue;
pub mod tick_lock;

pub use adaptive_wiki::{
    build_ai_projection, build_ai_projection_report, build_graph_export_files,
    build_human_projection, build_runtime_projection, build_usage_records,
    build_usage_records_with_policy, AdaptiveWikiActivationMode, AdaptiveWikiAgentMode,
    AdaptiveWikiAgentModeFilter, AdaptiveWikiAiProjection, AdaptiveWikiAuditAction,
    AdaptiveWikiAuditRecord, AdaptiveWikiCandidate, AdaptiveWikiCandidateInput,
    AdaptiveWikiCandidateState, AdaptiveWikiConfidence, AdaptiveWikiCorrectionKind,
    AdaptiveWikiCorrectionRecord, AdaptiveWikiCorrectionRecurrenceAssessment,
    AdaptiveWikiCorrectionRecurrenceReport, AdaptiveWikiCorrectionRecurrenceSummary,
    AdaptiveWikiEntry, AdaptiveWikiEntryState, AdaptiveWikiEpisodeEvaluationReport,
    AdaptiveWikiEpisodeEvaluationSummary, AdaptiveWikiEpisodeTraceStep, AdaptiveWikiGraphEdge,
    AdaptiveWikiGraphNode, AdaptiveWikiGraphReport, AdaptiveWikiGraphSummary,
    AdaptiveWikiHumanCandidate, AdaptiveWikiHumanEntry, AdaptiveWikiHumanProjection,
    AdaptiveWikiKind, AdaptiveWikiLintIssue, AdaptiveWikiLintReport, AdaptiveWikiLintSeverity,
    AdaptiveWikiLintSummary, AdaptiveWikiLiveEpisodeEvent, AdaptiveWikiLiveEpisodeEventKind,
    AdaptiveWikiLiveEpisodeFilter, AdaptiveWikiLiveEpisodeSummary,
    AdaptiveWikiLiveEpisodeTraceReport, AdaptiveWikiMarkdownExportFile,
    AdaptiveWikiMarkdownExportReport, AdaptiveWikiMarkdownExportSummary, AdaptiveWikiOrigin,
    AdaptiveWikiProjectionBudget, AdaptiveWikiProjectionComparisonReport,
    AdaptiveWikiProjectionComparisonSummary, AdaptiveWikiProjectionConflict,
    AdaptiveWikiProjectionConflictPolarity, AdaptiveWikiProjectionPolicy,
    AdaptiveWikiProjectionRejection, AdaptiveWikiProjectionRejectionReason,
    AdaptiveWikiProjectionReport, AdaptiveWikiProjectionReviewExpired,
    AdaptiveWikiProjectionReviewExpiredPolicy, AdaptiveWikiProjectionSummary,
    AdaptiveWikiPromotionEvidenceChainReport, AdaptiveWikiPromotionEvidenceChainSummary,
    AdaptiveWikiQuery, AdaptiveWikiReviewProposal, AdaptiveWikiReviewProposalAction,
    AdaptiveWikiReviewProposalDecision, AdaptiveWikiReviewProposalEventRecord,
    AdaptiveWikiReviewProposalLifecycle, AdaptiveWikiReviewQueueFilter, AdaptiveWikiReviewReport,
    AdaptiveWikiReviewReportSummary, AdaptiveWikiReviewRisk, AdaptiveWikiRuntimePolicyAckScopeMode,
    AdaptiveWikiRuntimePolicyAcknowledgement, AdaptiveWikiRuntimePolicyDecision,
    AdaptiveWikiRuntimePolicyDecisionStatus, AdaptiveWikiRuntimeProjection,
    AdaptiveWikiRuntimeProjectionResolution, AdaptiveWikiScope, AdaptiveWikiScopeSuggestion,
    AdaptiveWikiSignalKind, AdaptiveWikiStatus, AdaptiveWikiStore, AdaptiveWikiUsageContext,
    AdaptiveWikiUsageRecord,
};
pub use approval::{
    ActionApprovalMetadata, ActionApprovalRequest, ApprovalBrief, ApprovalBriefOption,
    ApprovalDecision, ApprovalLedger, ApprovalLedgerSession, ApprovalMode, ApprovalScope,
    ApprovalStatus, ExecutionBrief, PendingActionApproval, ProviderFallbackApplyScope,
    ProviderFallbackApprovalMetadata, RiskLevel,
};
pub use background::{
    BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore, BackgroundRunnerKind,
    BackgroundRunnerPhase,
};
pub use capability::{
    default_capability_registry, CapabilityArtifactCheck, CapabilityArtifactContract,
    CapabilityArtifactRef, CapabilityDescriptor, CapabilityRegistry, CapabilityRisk,
};
pub use control_loop::{
    load_offdesk_status_summary, reconcile_tasks_with_background_outcomes, run_offdesk_tick,
    OffdeskStatusSummary, OffdeskTickOptions, OffdeskTickReport,
};
pub use mode_contract::{
    assess_offdesk_mode, mode_requires_separate_review, OffdeskModeAssessment,
    OffdeskModeLifecycle, OffdeskModeRisk, OffdeskModeVerdict,
};
pub use mutation::{
    MutationRestoreOperation, MutationRestorePlan, MutationSnapshot, MutationSnapshotRequest,
    MutationSnapshotStore, MutationSnapshotVerification, SnapshotPolicy,
};
pub use provider::{
    classify_provider_error, classify_provider_error_with_context, default_provider_profile,
    default_provider_profiles, recommend_provider_fallback, ProviderCapacityState,
    ProviderCapacityStatus, ProviderCapacityStore, ProviderDescriptor, ProviderErrorClassification,
    ProviderErrorInput, ProviderErrorReason, ProviderFallbackAuthStatus, ProviderFallbackCandidate,
    ProviderFallbackRecommendation, ProviderFallbackSource, ProviderKind, ProviderProfile,
    ProviderRecoveryAction,
};
pub use redaction::{
    force_redact, force_redact_with_report, operator_safe_report, operator_safe_text,
    strip_runner_context, strip_runner_context_with_report, RedactionOutcome,
};
pub use resume::{
    ResumeEvidence, ResumePendingInput, ResumeStatus, TaskResumeState, TaskResumeStore,
};
pub use runner::{
    launch_background_command, launch_background_command_with_gate_outcome, launch_background_run,
    poll_background_runs, BackgroundLaunchOutcome, BackgroundLaunchRequest, BackgroundPollOutcome,
    LocalCommandLaunchSpec,
};
pub use scheduler::{
    is_provider_capacity_block, ProviderCapacityGateSummary, SchedulerGate, SchedulerGateOutcome,
    SchedulerGateRequest, SchedulerGateStatus,
};
pub use task_queue::{
    count_tasks, OffdeskTask, OffdeskTaskCounts, OffdeskTaskInput, OffdeskTaskLifecycleAction,
    OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskStore, OffdeskTaskView,
};
pub use tick_lock::{OffdeskTickLockGuard, OffdeskTickLockMetadata};
