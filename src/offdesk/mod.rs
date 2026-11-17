//! Offdesk orchestration safety rails and durable artifacts.
//!
//! This module keeps the canonical state in Forager-owned JSON artifacts. The
//! helpers are intentionally side-effect-light so the scheduler, dashboard,
//! Telegram bridge, and future worker backends can share the same policy logic.

pub mod adaptive_wiki;
pub mod approval;
pub mod background;
pub mod capability;
pub mod containment_seal;
pub mod control_loop;
pub mod decision;
pub mod failure_history;
pub mod implementation_packet;
pub mod learning_signals;
pub mod local_exit;
pub mod mode_contract;
pub mod mutation;
pub mod operator_pause;
pub mod orchestration;
pub mod provider;
pub mod read_policy;
pub mod read_policy_tmux;
pub mod redaction;
pub mod remote_session;
pub mod result_observation;
pub mod resume;
pub mod runner;
pub(crate) mod runtime_occupancy;
pub mod scheduler;
pub mod shift;
pub mod shift_enterprise;
pub mod shift_host_policy;
pub mod shift_retention;
pub mod shift_review;
pub(crate) mod shift_runtime;
pub mod shift_store;
pub mod task_queue;
pub(crate) mod task_report;
pub mod task_termination;
pub mod tick_lock;
pub mod wiki_context;
pub(crate) mod wiki_context_file;
pub(crate) mod wiki_context_receipts;
pub(crate) mod workdir_conflicts;
pub mod workflow;

pub use adaptive_wiki::{
    build_ai_projection, build_ai_projection_report, build_graph_export_files,
    build_human_projection, build_runtime_projection, build_usage_records,
    build_usage_records_with_policy, AdaptiveWikiActivationMode, AdaptiveWikiAgentMode,
    AdaptiveWikiAgentModeFilter, AdaptiveWikiAiProjection, AdaptiveWikiAuditAction,
    AdaptiveWikiAuditRecord, AdaptiveWikiCandidate, AdaptiveWikiCandidateInput,
    AdaptiveWikiCandidateState, AdaptiveWikiConfidence, AdaptiveWikiCorrectionKind,
    AdaptiveWikiCorrectionRecord, AdaptiveWikiCorrectionRecurrenceAssessment,
    AdaptiveWikiCorrectionRecurrenceReport, AdaptiveWikiCorrectionRecurrenceSummary,
    AdaptiveWikiEntry, AdaptiveWikiEntryEdit, AdaptiveWikiEntryState,
    AdaptiveWikiEpisodeEvaluationReport, AdaptiveWikiEpisodeEvaluationSummary,
    AdaptiveWikiEpisodeTraceStep, AdaptiveWikiGraphEdge, AdaptiveWikiGraphNode,
    AdaptiveWikiGraphReport, AdaptiveWikiGraphSummary, AdaptiveWikiHumanCandidate,
    AdaptiveWikiHumanEntry, AdaptiveWikiHumanProjection, AdaptiveWikiKind, AdaptiveWikiLintIssue,
    AdaptiveWikiLintReport, AdaptiveWikiLintSeverity, AdaptiveWikiLintSummary,
    AdaptiveWikiLiveEpisodeEvent, AdaptiveWikiLiveEpisodeEventKind, AdaptiveWikiLiveEpisodeFilter,
    AdaptiveWikiLiveEpisodeSummary, AdaptiveWikiLiveEpisodeTraceReport,
    AdaptiveWikiMarkdownExportFile, AdaptiveWikiMarkdownExportReport,
    AdaptiveWikiMarkdownExportSummary, AdaptiveWikiOrigin, AdaptiveWikiProjectionBudget,
    AdaptiveWikiProjectionComparisonReport, AdaptiveWikiProjectionComparisonSummary,
    AdaptiveWikiProjectionConflict, AdaptiveWikiProjectionConflictPolarity,
    AdaptiveWikiProjectionPolicy, AdaptiveWikiProjectionRejection,
    AdaptiveWikiProjectionRejectionReason, AdaptiveWikiProjectionReport,
    AdaptiveWikiProjectionReviewExpired, AdaptiveWikiProjectionReviewExpiredPolicy,
    AdaptiveWikiProjectionSummary, AdaptiveWikiPromotionEvidenceChainReport,
    AdaptiveWikiPromotionEvidenceChainSummary, AdaptiveWikiPromotionReceipt,
    AdaptiveWikiPromotionReceiptAuthority, AdaptiveWikiQuery, AdaptiveWikiReviewProposal,
    AdaptiveWikiReviewProposalAction, AdaptiveWikiReviewProposalDecision,
    AdaptiveWikiReviewProposalEventRecord, AdaptiveWikiReviewProposalLifecycle,
    AdaptiveWikiReviewQueueFilter, AdaptiveWikiReviewReport, AdaptiveWikiReviewReportSummary,
    AdaptiveWikiReviewRisk, AdaptiveWikiRuntimePolicyAckScopeMode,
    AdaptiveWikiRuntimePolicyAcknowledgement, AdaptiveWikiRuntimePolicyDecision,
    AdaptiveWikiRuntimePolicyDecisionStatus, AdaptiveWikiRuntimeProjection,
    AdaptiveWikiRuntimeProjectionResolution, AdaptiveWikiScope, AdaptiveWikiScopeSuggestion,
    AdaptiveWikiSignalKind, AdaptiveWikiStatus, AdaptiveWikiStore, AdaptiveWikiUsageContext,
    AdaptiveWikiUsageRecord,
};
pub use approval::{
    ActionApprovalMetadata, ActionApprovalRequest, ApprovalBrief, ApprovalBriefOption,
    ApprovalDecision, ApprovalLedger, ApprovalLedgerSession, ApprovalMode, ApprovalScope,
    ApprovalStatus, ArtifactRetentionApprovalMetadata, ExecutionBrief,
    OffdeskShiftArmApprovalMetadataV1, PendingActionApproval, ProviderFallbackApplyScope,
    ProviderFallbackApprovalMetadata, RiskLevel, OFFDESK_SHIFT_ARM_APPROVAL_METADATA_SCHEMA,
};
pub use background::{
    BackgroundProbe, BackgroundRecoveryAcknowledgement, BackgroundRecoveryDecision,
    BackgroundRunStore, BackgroundRunnerKind, BackgroundRunnerPhase,
};
pub use capability::{
    default_capability_registry, CapabilityArtifactCheck, CapabilityArtifactContract,
    CapabilityArtifactRef, CapabilityDescriptor, CapabilityRegistry, CapabilityRisk,
};
pub use control_loop::{
    load_offdesk_status_summary, reconcile_tasks_with_background_outcomes, run_offdesk_tick,
    OffdeskStatusSummary, OffdeskTickOptions, OffdeskTickReport,
};
pub use decision::{
    CouncilReview, DecisionLedger, DecisionMateriality, DecisionOption, DecisionRaisedBy,
    DecisionReceipt, DecisionRecord, DecisionRecordView, DecisionRequest, DecisionRoute,
    DecisionRouteTarget, DecisionStatus, DecisionTraceRef, DecisionValidationIssue,
    DecisionValidationSeverity, ExecutionHandoff, JudgmentEvaluator, JudgmentRoute,
    DECISION_RECORD_SCHEMA, JUDGMENT_ROUTE_SCHEMA,
};
pub use implementation_packet::{
    draft_implementation_packet, implementation_packet_from_path,
    implementation_packet_record_from_path, latest_implementation_packet_for_project,
    operator_safe_implementation_packet_summary, work_slice_execution_receipts_from_path,
    AlignmentReviewOutcome, ImplementationAlignment, ImplementationCapabilityMapping,
    ImplementationCloseout, ImplementationDesign, ImplementationExecution, ImplementationPacket,
    ImplementationPacketDraftInput, ImplementationPacketSummary, ImplementationScope,
    ImplementationSourceIntent, ImplementationValidation, LatestImplementationPacket,
    RecursiveAlignmentChecks, RecursiveAlignmentReview, WorkSliceExecutionReceipt,
    WorkSliceExecutionStatus, WorkSliceReceiptProducerRole, WorkSliceVerificationStatus,
    IMPLEMENTATION_PACKETS_DIR, IMPLEMENTATION_PACKET_FILE, IMPLEMENTATION_PACKET_MD_FILE,
    IMPLEMENTATION_PACKET_SCHEMA, RECURSIVE_ALIGNMENT_REVIEW_FILE,
    RECURSIVE_ALIGNMENT_REVIEW_SCHEMA, WORK_SLICE_EXECUTION_RECEIPTS_FILE,
    WORK_SLICE_EXECUTION_RECEIPT_SCHEMA,
};
pub use learning_signals::{
    scan_and_emit_learning_signals, EmittedLearningSignal, LearningScanReport,
    LearningSignalSource, LearningSignalStore, LEARNING_SIGNALS_FILE, LEARNING_SIGNALS_SCHEMA,
};
pub use mode_contract::{
    assess_offdesk_mode, mode_requires_separate_review, OffdeskModeAssessment,
    OffdeskModeLifecycle, OffdeskModeRisk, OffdeskModeVerdict,
};
pub use mutation::{
    MutationRestoreOperation, MutationRestorePlan, MutationSnapshot, MutationSnapshotRequest,
    MutationSnapshotStore, MutationSnapshotVerification, SnapshotPolicy,
};
pub use operator_pause::{
    OperatorPauseState, OperatorPauseStore, OPERATOR_PAUSE_FILE, OPERATOR_PAUSE_SCHEMA,
};
pub use orchestration::{load_orchestration_signals, OrchestrationSignals};
pub use provider::{
    classify_provider_error, classify_provider_error_with_context, default_provider_profile,
    default_provider_profiles, recommend_provider_fallback, ProviderCapacityState,
    ProviderCapacityStatus, ProviderCapacityStore, ProviderDescriptor, ProviderErrorClassification,
    ProviderErrorInput, ProviderErrorReason, ProviderFallbackAuthStatus, ProviderFallbackCandidate,
    ProviderFallbackRecommendation, ProviderFallbackSource, ProviderKind, ProviderProfile,
    ProviderRecoveryAction,
};
pub use redaction::{
    force_redact, force_redact_with_report, operator_safe_identity,
    operator_safe_identity_selector, operator_safe_report, operator_safe_text,
    strip_runner_context, strip_runner_context_with_report, RedactionOutcome,
};
pub use remote_session::*;
pub use resume::{
    ResumeEvidence, ResumePendingInput, ResumeStatus, TaskResumeState, TaskResumeStore,
};
pub use runner::{
    launch_background_command, launch_background_command_with_gate_outcome, launch_background_run,
    poll_background_runs, terminate_background_run, BackgroundLaunchOutcome,
    BackgroundLaunchRequest, BackgroundPollOutcome, BackgroundTerminationReport,
    LocalCommandLaunchSpec, BACKGROUND_TERMINATION_REPORT_SCHEMA,
};
pub use scheduler::{
    is_provider_capacity_block, ProviderCapacityGateSummary, SchedulerGate, SchedulerGateOutcome,
    SchedulerGateRequest, SchedulerGateStatus,
};
pub use shift::{
    execution_brief_sha256, normalize_offdesk_shift_template_key, offdesk_shift_arm_receipt_sha256,
    offdesk_shift_background_probe_sha256, offdesk_shift_manifest_sha256,
    offdesk_shift_routine_run_id, offdesk_shift_task_ticket_receipt_sha256,
    offdesk_shift_template_shift_id, offdesk_shift_template_slot, offdesk_task_launch_spec_sha256,
    validate_offdesk_shift_dispatch_authority, validate_offdesk_shift_state_transition,
    OffdeskShiftArmReceiptV1, OffdeskShiftAuthorityV1, OffdeskShiftBudgetV1,
    OffdeskShiftManifestV1, OffdeskShiftRoutineBindingV1, OffdeskShiftRoutineEvidenceV1,
    OffdeskShiftRoutineKindV1, OffdeskShiftRoutineOutcomeReceiptV1, OffdeskShiftRoutineOutcomeV1,
    OffdeskShiftRoutineRecoveryAcknowledgementV1, OffdeskShiftRoutineResourceBudgetV1,
    OffdeskShiftRoutineRunReservationV1, OffdeskShiftRoutineScheduleV1, OffdeskShiftRoutineUsageV1,
    OffdeskShiftStateV1, OffdeskShiftStatusV1, OffdeskShiftTaskBindingV1,
    OffdeskShiftTaskLaunchReservationV1, OffdeskShiftTaskLaunchTicketReceiptV1,
    OffdeskShiftTaskObservation, OffdeskShiftTaskOutcomeReceiptV1, OffdeskShiftTaskOutcomeV1,
    OffdeskShiftTemplateCadenceV1, OffdeskShiftTemplateInstantiationV1,
    OffdeskShiftTransitionReceiptV1, OffdeskShiftWindowV1, OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA,
    OFFDESK_SHIFT_MANIFEST_SCHEMA, OFFDESK_SHIFT_REQUIRED_DENIALS,
    OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA,
    OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA,
    OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS, OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA,
    OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA, OFFDESK_SHIFT_STATE_SCHEMA,
    OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA, OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA,
    OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA, OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_SCHEMA,
    OFFDESK_SHIFT_TRANSITION_RECEIPT_SCHEMA,
};
pub use shift_enterprise::{
    build_offdesk_shift_enterprise_policy_report, OffdeskShiftChildTaskNetworkBoundaryV1,
    OffdeskShiftEnterpriseBudgetCeilingV1, OffdeskShiftEnterprisePolicyReportAuthorityV1,
    OffdeskShiftEnterprisePolicyReportV1, OffdeskShiftEnterprisePolicyV1,
    OffdeskShiftEnterpriseProfileKeyV1, OffdeskShiftForagerNetworkEffectV1,
    OffdeskShiftMandatoryReviewsV1, OffdeskShiftNotificationRouteV1, OffdeskShiftOperatorModeV1,
    OFFDESK_SHIFT_ENTERPRISE_POLICY_REPORT_SCHEMA, OFFDESK_SHIFT_ENTERPRISE_POLICY_SCHEMA,
};
pub use shift_host_policy::{
    probe_offdesk_shift_host_policy, require_offdesk_shift_host_policy_attestation,
    reverify_offdesk_shift_host_policy_attestation, OffdeskShiftHostFilesystemPolicyV1,
    OffdeskShiftHostPolicyAttestationV1, OffdeskShiftHostPolicyFilesystemBoundaryV1,
    OffdeskShiftHostPolicyLaunchEvidenceV1, OffdeskShiftHostPolicyNetworkBoundaryV1,
    OffdeskShiftHostPolicyProbeStatusV1, OffdeskShiftHostPolicyProviderV1,
    OffdeskShiftHostPolicyReportAuthorityV1, OffdeskShiftHostPolicyReportV1,
    OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA, OFFDESK_SHIFT_HOST_POLICY_LAUNCH_EVIDENCE_SCHEMA,
    OFFDESK_SHIFT_HOST_POLICY_REPORT_SCHEMA,
};
pub use shift_retention::{
    build_offdesk_shift_retention_report, OffdeskShiftEvidenceRetentionDispositionV1,
    OffdeskShiftEvidenceRetentionReasonV1, OffdeskShiftRetentionAuthorityV1,
    OffdeskShiftRetentionItemV1, OffdeskShiftRetentionPolicyV1, OffdeskShiftRetentionReportV1,
    OffdeskShiftRetentionSummaryV1, OFFDESK_SHIFT_RETENTION_POLICY_SCHEMA,
    OFFDESK_SHIFT_RETENTION_REPORT_SCHEMA,
};
pub use shift_review::{
    build_offdesk_shift_council_escalation_packet, build_offdesk_shift_review_projection,
    OffdeskShiftCloseoutReviewV1, OffdeskShiftCouncilCurrentEvidenceV1,
    OffdeskShiftCouncilDecisionRequestV1, OffdeskShiftCouncilDecisionV1,
    OffdeskShiftCouncilDriftV1, OffdeskShiftCouncilEscalationPacketV1, OffdeskShiftCouncilOptionV1,
    OffdeskShiftCouncilOriginalGoalV1, OffdeskShiftCouncilPacketAuthorityV1,
    OffdeskShiftCouncilPacketSourcesV1, OffdeskShiftCouncilRemainingBudgetV1,
    OffdeskShiftCouncilResourceAmountsV1, OffdeskShiftCouncilResourceBudgetV1,
    OffdeskShiftCouncilReviewV1, OffdeskShiftCouncilRoutineBudgetV1,
    OffdeskShiftCouncilTaskAttemptBudgetV1, OffdeskShiftCouncilTaskBudgetV1,
    OffdeskShiftEvidenceCoverageV1, OffdeskShiftEvidenceDrilldownV1, OffdeskShiftEvidenceItemV1,
    OffdeskShiftReviewAuthorityV1, OffdeskShiftReviewProjectionV1, OffdeskShiftReviewSourcesV1,
    OffdeskShiftReviewSummaryV1, OffdeskShiftRoutineReviewV1, OffdeskShiftTaskAttemptReviewV1,
    OffdeskShiftTaskReviewV1, OffdeskShiftWikiReviewV1, OffdeskShiftWikiTargetReviewV1,
    OFFDESK_SHIFT_COUNCIL_ESCALATION_PACKET_SCHEMA, OFFDESK_SHIFT_REVIEW_PROJECTION_SCHEMA,
};
pub use shift_store::{
    OffdeskShiftRecordV1, OffdeskShiftStore, OffdeskShiftStoreSnapshotV1,
    OFFDESK_SHIFT_LEDGER_FILE, OFFDESK_SHIFT_LEDGER_SCHEMA, OFFDESK_SHIFT_STORE_SNAPSHOT_SCHEMA,
};
pub use task_queue::{
    count_tasks, ensure_resume_review_next_safe_action, next_safe_action_for_background_poll,
    next_safe_action_for_pending_approval, pending_approval_operator_view,
    pending_approval_operator_views, status_next_safe_actions_from_summary,
    tick_next_safe_actions_from_report, OffdeskCloseoutStateSummary, OffdeskNextSafeAction,
    OffdeskPendingApprovalView, OffdeskStatusNextSafeActionInput, OffdeskTask, OffdeskTaskCounts,
    OffdeskTaskInput, OffdeskTaskLifecycleAction, OffdeskTaskLifecycleReport,
    OffdeskTaskNextSafeAction, OffdeskTaskStatus, OffdeskTaskStore, OffdeskTaskView,
    OffdeskTickReportInput,
};
pub use task_termination::{
    terminate_offdesk_task, OffdeskTaskTerminationReport, OFFDESK_TASK_TERMINATION_REPORT_SCHEMA,
};
pub use tick_lock::{OffdeskTickLockGuard, OffdeskTickLockMetadata};
pub use wiki_context::{
    wiki_context_projection_sha256, WikiContextAgentAcknowledgementV1, WikiContextAgentMode,
    WikiContextAgentTool, WikiContextBoundFailureV1, WikiContextManifestOutcomeV1,
    WikiContextManifestV1, WikiContextOperatorVerificationV1, WikiContextPresentationReceiptV1,
    WikiContextPresentationStatus, WikiContextProjectionEntryV1, WikiContextProjectionV1,
    WikiContextUnregisteredProjectV1, WIKI_CONTEXT_MANIFEST_SCHEMA,
    WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA,
};
pub use workflow::{
    assess_implementation_packet_detail, assess_implementation_packet_goal,
    assess_work_slice_receipt, build_adaptive_wiki_proposal_receipt,
    build_closeout_decision_record, build_closeout_implementation_packet_coverage,
    build_closeout_receipt, build_closeout_retirement_record, build_closeout_review_record,
    build_offdesk_plan_launch_prep_packet, build_offdesk_plan_registration,
    build_offdesk_plan_registry_detail, build_offdesk_plan_registry_item,
    build_offdesk_plan_review_record, build_offdesk_plan_review_state,
    closeout_receipt_decisions_from_value, closeout_receipt_evidence_status,
    closeout_receipt_next_safe_action, closeout_receipt_string_list, closeout_receipt_wiki_state,
    closeout_retention_status_after_preserve_in_place, normalize_decision_choice,
    offdesk_plan_launch_prep_denials, offdesk_plan_registration_denials,
    offdesk_plan_review_denials, receipt_decision_record, resolve_decision_record,
    select_offdesk_plan_review, validate_offdesk_plan_input, validate_offdesk_plan_launch_prep,
    validate_offdesk_plan_review_input, AdaptiveWikiProposalReceipt,
    AdaptiveWikiProposalReceiptCheck, AdaptiveWikiProposalReceiptInput,
    AdaptiveWikiProposalReceiptSubject, CloseoutDecisionRecordBuildInput,
    CloseoutDecisionResolution, CloseoutDecisionResolutionRecord,
    CloseoutImplementationPacketCoverage, CloseoutImplementationPacketCoverageInput,
    CloseoutImplementationPacketCoverageItem, CloseoutPacketCoverageDetail, CloseoutReceipt,
    CloseoutReceiptArtifactPathsInput, CloseoutReceiptArtifacts, CloseoutReceiptBuildInput,
    CloseoutReceiptDecision, CloseoutReceiptTaskRef, CloseoutResolvedDecision,
    CloseoutRetirementRecord, CloseoutRetirementRecordBuildInput, CloseoutReviewArtifactPaths,
    CloseoutReviewRecord, CloseoutReviewRecordBuildInput, CloseoutVerdict, DecisionReceiptInput,
    DecisionResolutionInput, ImplementationPacketCoverageStatus,
    ImplementationPacketDetailCoverage, ImplementationPacketExecutionEvidence,
    ImplementationPacketGoalCoverage, OffdeskPlanInputSummary, OffdeskPlanLaunchPrepArtifacts,
    OffdeskPlanLaunchPrepBuildInput, OffdeskPlanLaunchPrepPacket, OffdeskPlanRegistration,
    OffdeskPlanRegistrationArtifacts, OffdeskPlanRegistrationBuildInput, OffdeskPlanRegistryDetail,
    OffdeskPlanRegistryItem, OffdeskPlanReviewArtifacts, OffdeskPlanReviewBuildInput,
    OffdeskPlanReviewDecision, OffdeskPlanReviewRecord, OffdeskPlanReviewState,
    WorkSliceReceiptCoverage, WorkSliceReceiptTrustTier, OFFDESK_PLAN_LAUNCH_PREP_SCHEMA,
    OFFDESK_PLAN_REGISTRATION_SCHEMA, OFFDESK_PLAN_REQUIRED_DENIALS, OFFDESK_PLAN_REVIEW_SCHEMA,
};

pub mod weekly_usage;
