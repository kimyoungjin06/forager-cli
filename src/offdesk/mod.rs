//! Offdesk orchestration safety rails and durable artifacts.
//!
//! This module keeps the canonical state in AOE-owned JSON artifacts. The
//! helpers are intentionally side-effect-light so the scheduler, dashboard,
//! Telegram bridge, and future worker backends can share the same policy logic.

pub mod approval;
pub mod background;
pub mod capability;
pub mod control_loop;
pub mod mutation;
pub mod provider;
pub mod redaction;
pub mod resume;
pub mod runner;
pub mod scheduler;
pub mod task_queue;
pub mod tick_lock;

pub use approval::{
    ActionApprovalRequest, ApprovalDecision, ApprovalLedger, ApprovalLedgerSession, ApprovalMode,
    ApprovalScope, ApprovalStatus, ExecutionBrief, PendingActionApproval, RiskLevel,
};
pub use background::{
    BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore, BackgroundRunnerKind,
    BackgroundRunnerPhase,
};
pub use capability::{
    default_capability_registry, CapabilityDescriptor, CapabilityRegistry, CapabilityRisk,
};
pub use control_loop::{
    load_offdesk_status_summary, run_offdesk_tick, OffdeskStatusSummary, OffdeskTickOptions,
    OffdeskTickReport,
};
pub use mutation::{
    MutationSnapshot, MutationSnapshotRequest, MutationSnapshotStore, SnapshotPolicy,
};
pub use provider::{
    classify_provider_error, ProviderDescriptor, ProviderErrorClassification, ProviderErrorReason,
    ProviderKind,
};
pub use redaction::{force_redact, operator_safe_text, strip_runner_context};
pub use resume::{ResumePendingInput, ResumeStatus, TaskResumeState, TaskResumeStore};
pub use runner::{
    launch_background_command, launch_background_command_with_gate_outcome, launch_background_run,
    poll_background_runs, BackgroundLaunchOutcome, BackgroundLaunchRequest, BackgroundPollOutcome,
    LocalCommandLaunchSpec,
};
pub use scheduler::{
    SchedulerGate, SchedulerGateOutcome, SchedulerGateRequest, SchedulerGateStatus,
};
pub use task_queue::{
    count_tasks, OffdeskTask, OffdeskTaskCounts, OffdeskTaskInput, OffdeskTaskLifecycleAction,
    OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskStore, OffdeskTaskView,
};
pub use tick_lock::{OffdeskTickLockGuard, OffdeskTickLockMetadata};
