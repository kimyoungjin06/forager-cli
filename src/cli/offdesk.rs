//! `forager offdesk` operator commands.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Args, Subcommand, ValueEnum};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

use crate::offdesk::{
    assess_offdesk_mode, build_graph_export_files, build_usage_records_with_policy,
    default_capability_registry, launch_background_command, launch_background_run,
    operator_safe_report, poll_background_runs, recommend_provider_fallback,
    reconcile_tasks_with_background_outcomes, run_offdesk_tick, ActionApprovalRequest,
    AdaptiveWikiActivationMode, AdaptiveWikiAgentMode, AdaptiveWikiAgentModeFilter,
    AdaptiveWikiAuditAction, AdaptiveWikiAuditRecord, AdaptiveWikiCandidate,
    AdaptiveWikiCandidateInput, AdaptiveWikiConfidence, AdaptiveWikiCorrectionRecurrenceReport,
    AdaptiveWikiEntry, AdaptiveWikiEpisodeEvaluationReport, AdaptiveWikiGraphReport,
    AdaptiveWikiHumanCandidate, AdaptiveWikiHumanEntry, AdaptiveWikiKind, AdaptiveWikiLintReport,
    AdaptiveWikiLiveEpisodeFilter, AdaptiveWikiLiveEpisodeTraceReport,
    AdaptiveWikiMarkdownExportReport, AdaptiveWikiOrigin, AdaptiveWikiProjectionBudget,
    AdaptiveWikiProjectionComparisonReport, AdaptiveWikiProjectionPolicy,
    AdaptiveWikiProjectionReport, AdaptiveWikiProjectionReviewExpiredPolicy,
    AdaptiveWikiPromotionEvidenceChainReport, AdaptiveWikiQuery, AdaptiveWikiReviewProposal,
    AdaptiveWikiReviewProposalAction, AdaptiveWikiReviewProposalDecision,
    AdaptiveWikiReviewProposalEventRecord, AdaptiveWikiReviewQueueFilter, AdaptiveWikiReviewReport,
    AdaptiveWikiRuntimePolicyAckScopeMode, AdaptiveWikiRuntimePolicyAcknowledgement,
    AdaptiveWikiRuntimePolicyDecision, AdaptiveWikiRuntimePolicyDecisionStatus, AdaptiveWikiScope,
    AdaptiveWikiScopeSuggestion, AdaptiveWikiSignalKind, AdaptiveWikiStore,
    AdaptiveWikiUsageContext, ApprovalLedger, ApprovalStatus, BackgroundLaunchOutcome,
    BackgroundLaunchRequest, BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore,
    BackgroundRunnerKind, BackgroundRunnerPhase, CapabilityArtifactRef, CapabilityDescriptor,
    ExecutionBrief, LocalCommandLaunchSpec, MutationRestoreOperation, MutationRestorePlan,
    MutationSnapshot, MutationSnapshotStore, MutationSnapshotVerification, OffdeskModeAssessment,
    OffdeskModeLifecycle, OffdeskNextSafeAction, OffdeskTask, OffdeskTaskInput,
    OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskStore, OffdeskTaskView,
    OffdeskTickOptions, PendingActionApproval, ProviderCapacityState, ProviderCapacityStore,
    ProviderFallbackRecommendation, ResumeStatus, RiskLevel, SchedulerGate, SchedulerGateRequest,
    SchedulerGateStatus, TaskResumeState, TaskResumeStore,
};
use crate::session::{get_profile_dir, resolved_app_dir_path, DEFAULT_PROFILE};

#[derive(Subcommand)]
pub enum OffdeskCommands {
    /// List pending action approvals
    Pending(PendingArgs),

    /// Evaluate whether an offdesk capability may execute now
    Gate(GateArgs),

    /// Gate and record a background runner launch
    Launch(LaunchArgs),

    /// Enqueue a durable offdesk task
    Enqueue(EnqueueArgs),

    /// Run one offdesk control-loop pass
    Tick(TickArgs),

    /// Show durable offdesk tasks
    Tasks(TasksArgs),

    /// Show provider capacity cooldown state
    ProviderCapacity(JsonArgs),

    /// Recommend provider/model fallbacks without retargeting tasks
    ProviderFallback(ProviderFallbackArgs),

    /// Mark a durable task cancelled without stopping its background runner
    CancelTask(CancelTaskArgs),

    /// Requeue a failed, resume-pending, or cancelled durable task
    RetryTask(RetryTaskArgs),

    /// Accept recovery for a resume-pending task and requeue it
    ResumeTask(TaskLifecycleArgs),

    /// Discard a failed or resume-pending task
    AbandonTask(TaskLifecycleArgs),

    /// Poll background runner probes, persist phase transitions, and reconcile task status
    Poll(PollArgs),

    /// Approve the oldest or targeted pending action
    #[command(alias = "approve")]
    Ok(ResolveArgs),

    /// Deny the oldest or targeted pending action
    #[command(alias = "deny")]
    Cancel(ResolveArgs),

    /// Show task resume artifacts
    Resume(JsonArgs),

    /// Show background runner recovery probes
    Background(JsonArgs),

    /// Show Task Team capability metadata
    Capabilities(JsonArgs),

    /// List pre-mutation checkpoint snapshots
    Snapshots(JsonArgs),

    /// Show and verify a pre-mutation checkpoint snapshot
    Snapshot(MutationSnapshotArgs),

    /// Show a dry-run rollback plan without modifying files
    RestorePlan(MutationSnapshotArgs),

    /// Emit a sanitized read-only debug bundle
    DebugBundle(DebugBundleArgs),

    /// Summarize read-only Offdesk maintenance risks
    MaintenanceReport(MaintenanceReportArgs),

    /// Create or reuse an approval request for a maintenance action
    MaintenanceRequest(MaintenanceRequestArgs),

    /// Generate a mandatory closeout plan and commercial review packet
    Closeout(CloseoutArgs),

    /// Record a reviewed closeout verdict without applying file operations
    CloseoutReview(CloseoutReviewArgs),

    /// Inspect adaptive wiki candidates, entries, projections, and lint
    Wiki(WikiArgs),
}

#[derive(Args)]
pub struct PendingArgs {
    /// Include resolved and expired approvals
    #[arg(long)]
    all: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct GateArgs {
    /// Capability ID from `forager offdesk capabilities`
    capability_id: String,

    /// Project key for approval and audit correlation
    #[arg(long)]
    project_key: String,

    /// Request ID for approval and audit correlation
    #[arg(long)]
    request_id: String,

    /// Task ID for approval and audit correlation
    #[arg(long)]
    task_id: String,

    /// Mutation class to match against an ExecutionBrief envelope
    #[arg(long)]
    mutation_class: Option<String>,

    /// JSON file containing an ExecutionBrief
    #[arg(long)]
    brief: Option<PathBuf>,

    /// Provider ID to check against provider capacity cooldown state
    #[arg(long)]
    provider_id: Option<String>,

    /// Provider model to check against provider capacity cooldown state
    #[arg(long)]
    model: Option<String>,

    /// Artifact reference in ARTIFACT_ID=PATH form
    #[arg(long = "artifact", value_parser = parse_artifact_ref)]
    artifact_refs: Vec<CapabilityArtifactRef>,

    /// Artifact kind used to match adaptive wiki entries
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode used to match adaptive wiki entries
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Operator-safe action preview
    #[arg(long, default_value = "")]
    preview: String,

    /// Reason shown when approval is required
    #[arg(long, default_value = "")]
    reason: String,

    /// Source surface recorded on generated approval rows
    #[arg(long, default_value = "cli")]
    source_surface: String,

    /// Pending approval TTL in minutes
    #[arg(long, default_value_t = 30)]
    ttl_minutes: i64,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct LaunchArgs {
    /// Capability ID from `forager offdesk capabilities`
    capability_id: String,

    /// Runner backend to record: local-tmux, local-background, github-runner, remote-worker
    #[arg(long, value_parser = parse_background_runner_kind)]
    runner: BackgroundRunnerKind,

    /// Project key for approval and audit correlation
    #[arg(long)]
    project_key: String,

    /// Request ID for approval and audit correlation
    #[arg(long)]
    request_id: String,

    /// Task ID for approval and audit correlation
    #[arg(long)]
    task_id: String,

    /// Mutation class to match against an ExecutionBrief envelope
    #[arg(long)]
    mutation_class: Option<String>,

    /// JSON file containing an ExecutionBrief
    #[arg(long)]
    brief: Option<PathBuf>,

    /// Provider ID to check against provider capacity cooldown state
    #[arg(long)]
    provider_id: Option<String>,

    /// Provider model to check against provider capacity cooldown state
    #[arg(long)]
    model: Option<String>,

    /// Artifact reference in ARTIFACT_ID=PATH form
    #[arg(long = "artifact", value_parser = parse_artifact_ref)]
    artifact_refs: Vec<CapabilityArtifactRef>,

    /// Artifact kind used to match adaptive wiki entries
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode used to match adaptive wiki entries
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Stable ticket ID. Generated if omitted.
    #[arg(long)]
    ticket_id: Option<String>,

    /// Redacted launch spec summary to store with the ticket
    #[arg(long)]
    launch_spec: Option<String>,

    /// Shell command to execute for local-background or local-tmux runners
    #[arg(long = "cmd")]
    command: Option<String>,

    /// Working directory for --cmd. Defaults to the current directory.
    #[arg(long)]
    workdir: Option<PathBuf>,

    /// Log artifact path for --cmd stdout and stderr
    #[arg(long)]
    log_artifact: Option<PathBuf>,

    /// Result sidecar path used by poll to mark the ticket completed
    #[arg(long)]
    result_artifact: Option<PathBuf>,

    /// Whether a local runtime handle is alive immediately after launch
    #[arg(long, default_value_t = true)]
    runtime_alive: bool,

    /// Whether a local_background launch spec can be reconstructed after restart
    #[arg(long)]
    provider_launch_spec_reconstructable: bool,

    /// External ack timeout in seconds
    #[arg(long, default_value_t = 300)]
    ack_timeout_sec: i64,

    /// Operator-safe action preview
    #[arg(long, default_value = "")]
    preview: String,

    /// Reason shown when approval is required
    #[arg(long, default_value = "")]
    reason: String,

    /// Source surface recorded on generated approval rows
    #[arg(long, default_value = "cli")]
    source_surface: String,

    /// Pending approval TTL in minutes
    #[arg(long, default_value_t = 30)]
    ttl_minutes: i64,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct EnqueueArgs {
    /// Capability ID from `forager offdesk capabilities`
    capability_id: String,

    /// Runner backend to use: local-tmux or local-background
    #[arg(long, value_parser = parse_background_runner_kind)]
    runner: BackgroundRunnerKind,

    /// Project key for approval and audit correlation
    #[arg(long)]
    project_key: String,

    /// Request ID for approval and audit correlation
    #[arg(long)]
    request_id: String,

    /// Task ID. Generated if omitted.
    #[arg(long)]
    task_id: Option<String>,

    /// Shell command to execute when the task is dispatched
    #[arg(long = "cmd")]
    command: String,

    /// Working directory for --cmd. Defaults to the current directory.
    #[arg(long)]
    workdir: Option<PathBuf>,

    /// JSON file containing an ExecutionBrief to store with the task
    #[arg(long)]
    brief: Option<PathBuf>,

    /// Mutation class to match against an ExecutionBrief envelope
    #[arg(long)]
    mutation_class: Option<String>,

    /// Provider ID to check against provider capacity cooldown state when dispatched
    #[arg(long)]
    provider_id: Option<String>,

    /// Provider model to check against provider capacity cooldown state when dispatched
    #[arg(long)]
    model: Option<String>,

    /// Artifact reference in ARTIFACT_ID=PATH form
    #[arg(long = "artifact", value_parser = parse_artifact_ref)]
    artifact_refs: Vec<CapabilityArtifactRef>,

    /// Artifact kind used to match adaptive wiki entries
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode used to match adaptive wiki entries
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Operator-safe action preview
    #[arg(long, default_value = "")]
    preview: String,

    /// Reason shown when approval is required
    #[arg(long, default_value = "")]
    reason: String,

    /// Do not dispatch before this RFC3339 timestamp
    #[arg(long)]
    not_before: Option<String>,

    /// Log artifact path for command stdout and stderr
    #[arg(long)]
    log_artifact: Option<PathBuf>,

    /// Result sidecar path used by tick to mark the task completed
    #[arg(long)]
    result_artifact: Option<PathBuf>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct TickArgs {
    /// Maximum queued tasks to dispatch in this tick
    #[arg(long, default_value_t = 10)]
    limit: usize,

    /// Treat previous free lock metadata as stale after this many minutes
    #[arg(long, default_value_t = 30)]
    lock_stale_minutes: i64,

    /// Record notification cooldown state in minutes while polling background runs
    #[arg(long)]
    notify_cooldown_minutes: Option<i64>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct PollArgs {
    /// Ticket ID to poll. Defaults to all tickets.
    ticket_id: Option<String>,

    /// Record notification cooldown state in minutes
    #[arg(long)]
    notify_cooldown_minutes: Option<i64>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ResolveArgs {
    /// Approval ID to resolve. Defaults to the oldest pending approval.
    approval_id: Option<String>,

    /// Operator or surface resolving this approval
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct JsonArgs {
    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct TasksArgs {
    /// Filter tasks by project key
    #[arg(long)]
    project_key: Option<String>,

    /// Filter tasks by exact task ID
    #[arg(long)]
    task_id: Option<String>,

    /// Filter tasks by status. Repeat for multiple statuses.
    #[arg(long, value_parser = parse_offdesk_task_status)]
    status: Vec<OffdeskTaskStatus>,

    /// Return only the newest matching task by updated_at
    #[arg(long)]
    latest: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiProposalEventsArgs {
    /// Filter lifecycle events by proposal id
    #[arg(long)]
    proposal_id: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRecordProposalEventArgs {
    /// Curator review proposal id
    proposal_id: String,

    /// Operator decision for the proposal
    #[arg(long, value_parser = parse_adaptive_wiki_proposal_decision)]
    decision: AdaptiveWikiReviewProposalDecision,

    /// Proposal action that was reviewed
    #[arg(long, value_parser = parse_adaptive_wiki_review_action)]
    proposal_action: Option<AdaptiveWikiReviewProposalAction>,

    /// Proposal subject kind, such as entry or candidate
    #[arg(long, default_value = "")]
    subject_kind: String,

    /// Proposal subject id
    #[arg(long, default_value = "")]
    subject_id: String,

    /// Operator or surface recording the decision
    #[arg(long, default_value = "cli")]
    by: String,

    /// Required reason for accepting, rejecting, or superseding the proposal
    #[arg(long)]
    reason: String,

    /// Evidence ref that supports this proposal decision
    #[arg(long = "evidence-ref")]
    evidence_refs: Vec<String>,

    /// Previous proposal id superseded by this decision
    #[arg(long)]
    supersedes: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiCloseProposalArgs {
    /// Current curator review proposal id
    proposal_id: String,

    /// Operator or surface recording the decision
    #[arg(long, default_value = "cli")]
    by: String,

    /// Required reason for accepting, rejecting, or superseding the proposal
    #[arg(long)]
    reason: String,

    /// Extra evidence ref that supports this proposal decision
    #[arg(long = "evidence-ref")]
    evidence_refs: Vec<String>,

    /// Previous proposal id superseded by this decision
    #[arg(long)]
    supersedes: Option<String>,

    /// Allow recording a new lifecycle event for a non-stale decided proposal
    #[arg(long)]
    allow_decided: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiProposalHandoffArgs {
    /// Current curator review proposal id
    proposal_id: String,

    /// Operator-selected mutation path to preview when the proposal is manual
    #[arg(long, value_parser = parse_wiki_proposal_handoff_mutation)]
    mutation: Option<WikiProposalHandoffMutation>,

    /// Scope for a parameterized rescope handoff
    #[arg(long, value_parser = parse_adaptive_wiki_scope)]
    scope: Option<AdaptiveWikiScope>,

    /// Scope reference for a parameterized rescope handoff
    #[arg(long)]
    scope_ref: Option<String>,

    /// Evidence ref for a parameterized counterexample handoff
    #[arg(long = "evidence-ref")]
    evidence_ref: Option<String>,

    /// Entry to deprecate for a parameterized merge cleanup or conflict handoff
    #[arg(long = "deprecated-entry-id")]
    deprecated_entry_id: Option<String>,

    /// Operator rationale to include in the previewed mutation command
    #[arg(long)]
    reason: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiProposalReceiptArgs {
    /// Curator review proposal id that the receipt should link
    proposal_id: String,

    /// Adaptive wiki mutation audit id produced by the executed mutation command
    #[arg(long)]
    audit_id: String,

    /// Proposal lifecycle event id recorded for the operator decision
    #[arg(long)]
    event_id: String,

    /// Previewed handoff command that the operator executed or reviewed
    #[arg(long)]
    command: String,

    /// Write the sanitized receipt JSON to an audit artifact file
    #[arg(long)]
    export: bool,

    /// Write the sanitized receipt JSON to this path
    #[arg(long)]
    output: Option<PathBuf>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum WikiProposalHandoffMutation {
    Rescope,
    Deprecate,
    AddCounterexample,
    DeprecateDuplicate,
    Split,
}

#[derive(Args)]
pub struct DebugBundleArgs {
    /// Output as JSON
    #[arg(long)]
    json: bool,

    /// Write the sanitized bundle JSON to a diagnostics file
    #[arg(long)]
    export: bool,

    /// Write the sanitized bundle JSON to this path
    #[arg(long)]
    output: Option<PathBuf>,
}

#[derive(Args)]
pub struct MaintenanceReportArgs {
    /// Output as JSON
    #[arg(long)]
    json: bool,

    /// Hours before review_after expiry to flag adaptive wiki entries
    #[arg(long, default_value_t = 168)]
    wiki_review_near_expiry_hours: i64,

    /// Hours before runtime policy acknowledgement expiry to flag attention
    #[arg(long, default_value_t = 6)]
    wiki_runtime_ack_near_expiry_hours: i64,
}

#[derive(Args)]
pub struct MaintenanceRequestArgs {
    /// Bounded maintenance action kind to request approval for
    #[arg(long, value_parser = parse_maintenance_action_kind)]
    kind: MaintenanceActionKind,

    /// Project key for approval and audit correlation
    #[arg(long)]
    project_key: String,

    /// Request ID for approval and audit correlation
    #[arg(long)]
    request_id: String,

    /// Task ID for approval identity. Defaults to maintenance-<kind>-<target-id>
    #[arg(long)]
    task_id: Option<String>,

    /// Optional target identifier used for approval deduplication and review
    #[arg(long)]
    target_id: Option<String>,

    /// Override the default risk for this maintenance kind
    #[arg(long, value_parser = parse_risk_level)]
    risk: Option<RiskLevel>,

    /// Operator-safe action preview
    #[arg(long)]
    preview: String,

    /// Reason shown when approval is required
    #[arg(long)]
    reason: String,

    /// Source surface recorded on generated approval rows
    #[arg(long, default_value = "cli")]
    source_surface: String,

    /// Pending approval TTL in minutes
    #[arg(long, default_value_t = 30)]
    ttl_minutes: i64,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct CloseoutArgs {
    /// Project key to close out. Defaults to all projects in the profile.
    #[arg(long)]
    project_key: Option<String>,

    /// Request ID to close out
    #[arg(long)]
    request_id: Option<String>,

    /// Task ID to close out
    #[arg(long)]
    task_id: Option<String>,

    /// Optional project workdir for read-only git status evidence
    #[arg(long)]
    workdir: Option<PathBuf>,

    /// Include read-only git status and diff-stat from --workdir or matched task workdir
    #[arg(long)]
    include_git: bool,

    /// Commercial model/provider label expected to review move/delete/archive decisions
    #[arg(long, default_value = "commercial")]
    review_provider: String,

    /// Write closeout artifacts to this directory
    #[arg(long)]
    output: Option<PathBuf>,

    /// Accepted for explicit operator intent; closeout never applies file operations
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct CloseoutReviewArgs {
    /// Closeout ID from `forager offdesk closeout`
    #[arg(long)]
    closeout_id: Option<String>,

    /// Closeout artifact directory containing closeout_plan.json
    #[arg(long)]
    artifact_dir: Option<PathBuf>,

    /// Commercial review verdict
    #[arg(long, value_enum)]
    verdict: CloseoutReviewVerdict,

    /// Reviewer or reviewing model label
    #[arg(long, default_value = "operator")]
    reviewer: String,

    /// Commercial model/provider label used for review
    #[arg(long)]
    review_provider: Option<String>,

    /// Optional path to the raw commercial review output
    #[arg(long)]
    review_file: Option<PathBuf>,

    /// Unsafe operation reported by review; may be passed multiple times
    #[arg(long)]
    unsafe_operation: Vec<String>,

    /// Missing evidence reported by review; may be passed multiple times
    #[arg(long)]
    missing_evidence: Vec<String>,

    /// Required first-read path reported by review; may be passed multiple times
    #[arg(long)]
    required_first_read: Vec<String>,

    /// Short review note. Secrets are redacted before persistence.
    #[arg(long)]
    notes: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ValueEnum)]
#[serde(rename_all = "snake_case")]
enum CloseoutReviewVerdict {
    Approved,
    Revise,
    Blocked,
}

impl CloseoutReviewVerdict {
    fn as_str(self) -> &'static str {
        match self {
            Self::Approved => "approved",
            Self::Revise => "revise",
            Self::Blocked => "blocked",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum MaintenanceActionKind {
    RuntimeRecovery,
    WikiRuntimeAck,
    WikiReviewAfter,
    WikiMutation,
    ProviderCapacity,
    ArtifactCleanup,
    ServiceRestart,
    SystemChange,
}

impl MaintenanceActionKind {
    fn cli_value(self) -> &'static str {
        match self {
            Self::RuntimeRecovery => "runtime_recovery",
            Self::WikiRuntimeAck => "wiki_runtime_ack",
            Self::WikiReviewAfter => "wiki_review_after",
            Self::WikiMutation => "wiki_mutation",
            Self::ProviderCapacity => "provider_capacity",
            Self::ArtifactCleanup => "artifact_cleanup",
            Self::ServiceRestart => "service_restart",
            Self::SystemChange => "system_change",
        }
    }

    fn action_id(self) -> &'static str {
        match self {
            Self::RuntimeRecovery => "maintenance.runtime_recovery",
            Self::WikiRuntimeAck => "maintenance.wiki_runtime_ack",
            Self::WikiReviewAfter => "maintenance.wiki_review_after",
            Self::WikiMutation => "maintenance.wiki_mutation",
            Self::ProviderCapacity => "maintenance.provider_capacity",
            Self::ArtifactCleanup => "maintenance.artifact_cleanup",
            Self::ServiceRestart => "maintenance.service_restart",
            Self::SystemChange => "maintenance.system_change",
        }
    }

    fn default_risk(self) -> RiskLevel {
        match self {
            Self::RuntimeRecovery | Self::ProviderCapacity => RiskLevel::RuntimeMutation,
            Self::WikiRuntimeAck | Self::WikiReviewAfter | Self::WikiMutation => {
                RiskLevel::CanonicalMutation
            }
            Self::ArtifactCleanup => RiskLevel::Destructive,
            Self::ServiceRestart | Self::SystemChange => RiskLevel::ExternalSideEffect,
        }
    }
}

#[derive(Args)]
pub struct ProviderFallbackArgs {
    /// Current provider ID that is blocked or under review
    #[arg(long)]
    provider_id: String,

    /// Current provider model to exclude from fallback candidates
    #[arg(long)]
    model: Option<String>,

    /// Runner role used to filter compatible cross-provider candidates
    #[arg(long, default_value = "worker")]
    runner_role: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct CancelTaskArgs {
    /// Offdesk task ID to cancel
    task_id: String,

    /// Operator reason to store on the task
    #[arg(long)]
    reason: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct TaskLifecycleArgs {
    /// Offdesk task ID to update
    task_id: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct RetryTaskArgs {
    /// Offdesk task ID to retry
    task_id: String,

    /// Supersede matching denied approval rows so the next tick creates a new approval
    #[arg(long)]
    new_approval: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct MutationSnapshotArgs {
    /// Mutation snapshot ID
    mutation_id: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiArgs {
    #[command(subcommand)]
    command: WikiCommands,
}

#[derive(Subcommand)]
pub enum WikiCommands {
    /// List first-class adaptive wiki correction records
    Corrections(JsonArgs),

    /// List adaptive wiki review proposal lifecycle events
    ProposalEvents(WikiProposalEventsArgs),

    /// Record an operator decision for a curator review proposal
    RecordProposalEvent(WikiRecordProposalEventArgs),

    /// Accept a current curator review proposal and copy its metadata into the event
    AcceptProposal(WikiCloseProposalArgs),

    /// Reject a current curator review proposal and copy its metadata into the event
    RejectProposal(WikiCloseProposalArgs),

    /// Mark a current curator review proposal superseded and copy its metadata into the event
    SupersedeProposal(WikiCloseProposalArgs),

    /// Preview the governed mutation handoff command for a current proposal
    ProposalHandoff(WikiProposalHandoffArgs),

    /// Link a handoff preview, mutation audit, and lifecycle event without mutating state
    ProposalReceipt(WikiProposalReceiptArgs),

    /// List adaptive wiki candidates
    Candidates(WikiListArgs),

    /// List adaptive wiki entries
    Entries(WikiListArgs),

    /// Show one adaptive wiki entry or candidate
    Show(WikiShowArgs),

    /// Show the AI projection for a scope
    Projection(WikiProjectionArgs),

    /// List strict runtime projection policy acknowledgements
    RuntimePolicyAcks(JsonArgs),

    /// Report strict runtime projection acknowledgements that need attention
    RuntimePolicyAckReport(WikiRuntimePolicyAckReportArgs),

    /// Report promoted entries whose review_after needs attention
    ReviewAfterReport(WikiReviewAfterReportArgs),

    /// Acknowledge strict review_after exclusion for runtime projection
    AckRuntimePolicy(WikiRuntimePolicyAckArgs),

    /// Lint adaptive wiki state
    Lint(JsonArgs),

    /// Export adaptive wiki state as a one-way markdown vault
    ExportMarkdown(WikiExportMarkdownArgs),

    /// Export a read-only adaptive wiki tag graph
    Graph(WikiGraphArgs),

    /// Generate a recommendation-only adaptive wiki review report
    Review(WikiReviewArgs),

    /// Evaluate one adaptive wiki entry across in-scope and out-of-scope projections
    EvaluateEpisode(WikiEpisodeArgs),

    /// Trace live task/probe/wiki evidence for adaptive behavior review
    EpisodeTrace(WikiEpisodeTraceArgs),

    /// Evaluate whether corrections recur after an entry is promoted
    EvaluateRecurrence(WikiRecurrenceArgs),

    /// Reconstruct the evidence chain captured at promotion time
    PromotionChain(WikiPromotionChainArgs),

    /// Promote a candidate into a scoped wiki entry
    Promote(WikiPromoteArgs),

    /// Reject a candidate without creating an entry
    Reject(WikiRejectArgs),

    /// Change an entry scope
    Rescope(WikiRescopeArgs),

    /// Deprecate an entry so it no longer appears in AI projection
    Deprecate(WikiDeprecateArgs),

    /// Renew an entry review_after timestamp without changing scope or instruction
    RenewReviewAfter(WikiRenewReviewAfterArgs),

    /// Add a counterexample evidence ref to an entry
    AddCounterexample(WikiCounterexampleArgs),

    /// Attach governed runbook support refs to a procedure entry
    UpdateRunbook(WikiRunbookArgs),
}

#[derive(Args)]
pub struct WikiListArgs {
    /// Session/request scope to match
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to match
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiProjectionArgs {
    /// Session/request scope to match
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to match
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Use the scheduler's shared-only default when no agent mode is supplied.
    #[arg(long, hide = true)]
    runtime_agent_mode_default: bool,

    /// Return the projection policy report instead of only selected entries
    #[arg(long)]
    report: bool,

    /// Compare default warn policy with strict review_after exclusion
    #[arg(long)]
    compare_review_expired_policy: bool,

    /// Maximum selected projection entries
    #[arg(long)]
    max_entries: Option<usize>,

    /// Maximum estimated runtime context characters
    #[arg(long)]
    max_context_chars: Option<usize>,

    /// Maximum characters kept per projected instruction; 0 disables truncation
    #[arg(long)]
    max_instruction_chars: Option<usize>,

    /// Exclude entries that are past review_after from the projection report
    #[arg(long)]
    exclude_review_expired: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRuntimePolicyAckArgs {
    /// Session/request scope to match exactly
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to match
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Acknowledgement scope: exact-query or project-artifact
    #[arg(long, default_value = "exact-query", value_parser = parse_adaptive_wiki_runtime_policy_ack_scope_mode)]
    scope_mode: AdaptiveWikiRuntimePolicyAckScopeMode,

    /// Maximum selected projection entries
    #[arg(long)]
    max_entries: Option<usize>,

    /// Maximum estimated runtime context characters
    #[arg(long)]
    max_context_chars: Option<usize>,

    /// Maximum characters kept per projected instruction; 0 disables truncation
    #[arg(long)]
    max_instruction_chars: Option<usize>,

    /// Acknowledgement TTL in hours
    #[arg(long, default_value_t = 24)]
    ttl_hours: i64,

    /// Operator reason for enabling strict runtime projection in this scope
    #[arg(long, default_value = "")]
    reason: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRuntimePolicyAckReportArgs {
    /// Session/request scope to evaluate for query-specific ack applicability
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to evaluate for query-specific ack applicability
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to evaluate for query-specific ack applicability
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to evaluate for query-specific ack applicability
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Maximum selected projection entries
    #[arg(long)]
    max_entries: Option<usize>,

    /// Maximum estimated runtime context characters
    #[arg(long)]
    max_context_chars: Option<usize>,

    /// Maximum characters kept per projected instruction; 0 disables truncation
    #[arg(long)]
    max_instruction_chars: Option<usize>,

    /// Mark active acknowledgements expiring within this many hours
    #[arg(long, default_value_t = 6)]
    near_expiry_hours: i64,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiReviewAfterReportArgs {
    /// Session/request scope to match
    #[arg(long)]
    session_id: Option<String>,

    /// Project key scope to match
    #[arg(long)]
    project_key: Option<String>,

    /// Artifact kind scope to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Agent work mode scope to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Mark entries needing review within this many hours
    #[arg(long, default_value_t = 168)]
    near_expiry_hours: i64,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiShowArgs {
    /// Adaptive wiki entry or candidate id
    id: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiExportMarkdownArgs {
    /// Directory to write the markdown vault into
    #[arg(long)]
    output: PathBuf,

    /// Preview export files without writing them
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiGraphArgs {
    /// Optional directory to write graph.json and graph.md into
    #[arg(long)]
    output: Option<PathBuf>,

    /// Preview graph export files without writing them
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiReviewArgs {
    /// Preview recommendations without writing report files
    #[arg(long)]
    dry_run: bool,

    /// Show proposals that are open or have stale lifecycle decisions
    #[arg(long)]
    active_only: bool,

    /// Show proposals with non-stale accepted, rejected, or superseded decisions
    #[arg(long)]
    decided_only: bool,

    /// Show proposals whose latest lifecycle decision is stale
    #[arg(long)]
    stale_only: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiEpisodeArgs {
    /// Promoted adaptive wiki entry id expected to appear only in the in-scope projection
    entry_id: String,

    /// In-scope session/request id to match
    #[arg(long)]
    session_id: Option<String>,

    /// In-scope project key to match
    #[arg(long)]
    project_key: Option<String>,

    /// In-scope artifact kind to match
    #[arg(long)]
    artifact_kind: Option<String>,

    /// In-scope agent work mode to match
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Out-of-scope session/request id. Defaults to a generated non-matching value.
    #[arg(long)]
    out_session_id: Option<String>,

    /// Out-of-scope project key. Defaults to a generated non-matching value.
    #[arg(long)]
    out_project_key: Option<String>,

    /// Out-of-scope artifact kind. Defaults to a generated non-matching value.
    #[arg(long)]
    out_artifact_kind: Option<String>,

    /// Out-of-scope agent work mode. Defaults to a generated non-matching mode when possible.
    #[arg(long, value_parser = parse_adaptive_wiki_agent_mode)]
    out_agent_mode: Option<AdaptiveWikiAgentMode>,

    /// Preview the report without writing report files
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiEpisodeTraceArgs {
    /// Filter trace events by request id
    #[arg(long)]
    request_id: Option<String>,

    /// Filter trace events by task id
    #[arg(long)]
    task_id: Option<String>,

    /// Filter trace events by project key
    #[arg(long)]
    project_key: Option<String>,

    /// Filter trace events by artifact kind
    #[arg(long)]
    artifact_kind: Option<String>,

    /// Filter trace events by adaptive wiki entry id
    #[arg(long)]
    entry_id: Option<String>,

    /// Preview the trace without writing report files
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRecurrenceArgs {
    /// Promoted adaptive wiki entry id to evaluate
    entry_id: String,

    /// Preview the report without writing report files
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiPromotionChainArgs {
    /// Promoted adaptive wiki entry id to reconstruct
    entry_id: String,

    /// Preview the report without writing report files
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiPromoteArgs {
    /// Adaptive wiki candidate id
    candidate_id: String,

    /// Scope for the promoted entry. Defaults to the candidate scope.
    #[arg(long, value_parser = parse_adaptive_wiki_scope)]
    scope: Option<AdaptiveWikiScope>,

    /// Scope reference for the promoted entry. Required when --scope is used.
    #[arg(long)]
    scope_ref: Option<String>,

    /// Activation mode for the promoted entry
    #[arg(long, default_value = "confirm", value_parser = parse_adaptive_wiki_activation_mode)]
    activation_mode: AdaptiveWikiActivationMode,

    /// Agent work mode this promoted entry should apply to. Repeat for multiple modes; omit to keep candidate modes.
    #[arg(long = "agent-mode", value_parser = parse_adaptive_wiki_agent_mode)]
    agent_modes: Vec<AdaptiveWikiAgentMode>,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Optional promotion reason for audit
    #[arg(long, default_value = "")]
    reason: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRejectArgs {
    /// Adaptive wiki candidate id
    candidate_id: String,

    /// Reason for rejecting the candidate
    #[arg(long)]
    reason: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRescopeArgs {
    /// Adaptive wiki entry id
    entry_id: String,

    /// New entry scope
    #[arg(long, value_parser = parse_adaptive_wiki_scope)]
    scope: AdaptiveWikiScope,

    /// New entry scope reference
    #[arg(long)]
    scope_ref: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Optional rescope reason for audit
    #[arg(long, default_value = "")]
    reason: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiDeprecateArgs {
    /// Adaptive wiki entry id
    entry_id: String,

    /// Reason for deprecating the entry
    #[arg(long)]
    reason: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRenewReviewAfterArgs {
    /// Adaptive wiki entry id
    entry_id: String,

    /// New review_after timestamp in RFC3339 format
    #[arg(long, value_parser = parse_rfc3339_datetime)]
    review_after: DateTime<Utc>,

    /// Reason for renewing the review timestamp
    #[arg(long)]
    reason: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiCounterexampleArgs {
    /// Adaptive wiki entry id
    entry_id: String,

    /// Evidence ref that contradicts or limits the entry
    #[arg(long)]
    evidence_ref: String,

    /// Reason for recording the counterexample
    #[arg(long)]
    reason: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct WikiRunbookArgs {
    /// Adaptive wiki procedure entry id
    entry_id: String,

    /// Human/export support ref such as references/foo.md, templates/foo.md, or scripts/foo.sh
    #[arg(long)]
    support_ref: Vec<String>,

    /// Capability id this procedure is relevant to
    #[arg(long)]
    capability_id: Vec<String>,

    /// Required artifact kind this procedure depends on
    #[arg(long)]
    required_artifact_kind: Vec<String>,

    /// Reason for updating the runbook metadata
    #[arg(long)]
    reason: String,

    /// Operator or surface performing the review
    #[arg(long, default_value = "cli")]
    by: String,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
struct BackgroundProbeStatus {
    probe: BackgroundProbe,
    decision: BackgroundRecoveryDecision,
    #[serde(flatten)]
    mode_assessment: OffdeskModeAssessment,
}

#[derive(Serialize)]
struct RetryTaskLifecycleReport<'a> {
    #[serde(flatten)]
    report: &'a OffdeskTaskLifecycleReport,
    superseded_denied_approvals: usize,
}

#[derive(Serialize)]
struct WikiProposalHandoffPreview {
    proposal_id: String,
    action: AdaptiveWikiReviewProposalAction,
    subject_kind: String,
    subject_id: String,
    status: &'static str,
    command: Option<String>,
    reason: String,
    lifecycle_decision: Option<AdaptiveWikiReviewProposalDecision>,
    lifecycle_stale: bool,
    evidence_refs: Vec<String>,
    required_inputs: Vec<WikiProposalHandoffInput>,
    mutation_options: Vec<WikiProposalHandoffMutationOption>,
}

#[derive(Serialize)]
struct WikiProposalHandoffInput {
    name: &'static str,
    cli_flag: Option<&'static str>,
    required: bool,
    description: &'static str,
}

#[derive(Serialize)]
struct WikiProposalHandoffMutationOption {
    mutation: &'static str,
    command_template: String,
    required_inputs: Vec<&'static str>,
    description: &'static str,
}

#[derive(Serialize)]
struct WikiProposalReceipt {
    generated_at: DateTime<Utc>,
    read_only: bool,
    status: &'static str,
    proposal: WikiProposalReceiptSubject,
    preview_command: String,
    preview_command_sha256: String,
    audit: Option<AdaptiveWikiAuditRecord>,
    event: Option<AdaptiveWikiReviewProposalEventRecord>,
    checks: Vec<WikiProposalReceiptCheck>,
    blockers: Vec<String>,
}

#[derive(Serialize)]
struct WikiProposalReceiptSubject {
    proposal_id: String,
    current: bool,
    action: Option<AdaptiveWikiReviewProposalAction>,
    subject_kind: String,
    subject_id: String,
    lifecycle_decision: Option<AdaptiveWikiReviewProposalDecision>,
    lifecycle_event_id: Option<String>,
    evidence_refs: Vec<String>,
}

#[derive(Serialize)]
struct WikiProposalReceiptCheck {
    name: &'static str,
    passed: bool,
    detail: String,
}

#[derive(Serialize)]
struct WikiProposalReceiptExportReceipt<'a> {
    exported_to: String,
    bytes_written: usize,
    receipt: &'a WikiProposalReceipt,
}

struct WikiProposalReceiptExport {
    path: PathBuf,
    bytes_written: usize,
}

#[derive(Serialize)]
struct MutationSnapshotListItem {
    mutation_id: String,
    target_path: String,
    mutation_kind: String,
    created_at: DateTime<Utc>,
    rollback_available: bool,
    blockers: Vec<String>,
}

#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum WikiShowResult {
    Entry {
        entry: AdaptiveWikiHumanEntry,
    },
    Candidate {
        candidate: AdaptiveWikiHumanCandidate,
    },
}

#[derive(Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum WikiMutationResult {
    Promote {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    Reject {
        candidate: AdaptiveWikiHumanCandidate,
        audit: AdaptiveWikiAuditRecord,
    },
    Rescope {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    Deprecate {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    AddCounterexample {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    UpdateRunbook {
        entry: AdaptiveWikiHumanEntry,
        audit: AdaptiveWikiAuditRecord,
    },
    RenewReviewAfter {
        entry: AdaptiveWikiHumanEntry,
        previous_review_after: Option<DateTime<Utc>>,
        audit: AdaptiveWikiAuditRecord,
    },
}

#[derive(Serialize)]
struct WikiRuntimePolicyAckReport {
    generated_at: DateTime<Utc>,
    near_expiry_hours: i64,
    #[serde(skip_serializing_if = "Option::is_none")]
    query: Option<AdaptiveWikiQuery>,
    #[serde(skip_serializing_if = "Option::is_none")]
    budget: Option<AdaptiveWikiProjectionBudget>,
    #[serde(skip_serializing_if = "Option::is_none")]
    decision: Option<AdaptiveWikiRuntimePolicyDecision>,
    summary: WikiRuntimePolicyAckReportSummary,
    acknowledgements: Vec<WikiRuntimePolicyAckReportItem>,
}

#[derive(Default, Serialize)]
struct WikiRuntimePolicyAckReportSummary {
    total: usize,
    active: usize,
    expired: usize,
    near_expiry: usize,
    suggested_actions: usize,
    query_applied: usize,
    query_blocked: usize,
    query_stale: usize,
    query_expired: usize,
}

#[derive(Serialize)]
struct WikiRuntimePolicyAckReportItem {
    id: String,
    scope_mode: AdaptiveWikiRuntimePolicyAckScopeMode,
    query: AdaptiveWikiQuery,
    policy: AdaptiveWikiProjectionPolicy,
    created_at: DateTime<Utc>,
    expires_at: DateTime<Utc>,
    minutes_until_expiry: i64,
    status: Vec<String>,
    review_expired_excluded: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    suggested_action: Option<WikiRuntimePolicyAckSuggestedAction>,
}

#[derive(Serialize)]
struct WikiRuntimePolicyAckSuggestedAction {
    kind: String,
    detail: String,
    compare_command_template: String,
    ack_command_template: String,
}

#[derive(Serialize)]
struct WikiReviewAfterReport {
    generated_at: DateTime<Utc>,
    query: AdaptiveWikiQuery,
    near_expiry_hours: i64,
    summary: WikiReviewAfterReportSummary,
    entries: Vec<WikiReviewAfterReportItem>,
}

#[derive(Default, Serialize)]
struct WikiReviewAfterReportSummary {
    scoped_promoted: usize,
    with_review_after: usize,
    missing_review_after: usize,
    expired: usize,
    near_expiry: usize,
    attention: usize,
}

#[derive(Serialize)]
struct WikiReviewAfterReportItem {
    id: String,
    kind: AdaptiveWikiKind,
    scope: AdaptiveWikiScope,
    scope_ref: String,
    review_after: DateTime<Utc>,
    hours_until_review: i64,
    status: String,
    renew_command_template: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
struct DebugBundleRedactionSummary {
    text_fields_checked: usize,
    changed_text_fields: usize,
    runner_context_removed: usize,
    secrets_redacted: usize,
}

#[derive(Default)]
struct DebugBundleRedactor {
    summary: DebugBundleRedactionSummary,
}

#[derive(Serialize)]
struct OffdeskDebugBundle {
    generated_at: DateTime<Utc>,
    profile: String,
    profile_dir: String,
    read_only: bool,
    redaction_applied: bool,
    approvals: Value,
    tasks: Value,
    resume_states: Value,
    background_runs: Value,
    capabilities: Value,
    provider_capacity: Value,
    adaptive_wiki: Value,
    adaptive_wiki_usage: Value,
    adaptive_wiki_corrections: Value,
    adaptive_wiki_review_events: Value,
    adaptive_wiki_runtime_policy_acknowledgements: Value,
    adaptive_wiki_runtime_policy_ack_attention_summary: WikiRuntimePolicyAckReportSummary,
    adaptive_wiki_review_after_attention_summary: WikiReviewAfterReportSummary,
    redaction_summary: DebugBundleRedactionSummary,
}

#[derive(Serialize)]
struct DebugBundleExportReceipt<'a> {
    exported_to: String,
    bytes_written: usize,
    bundle: &'a OffdeskDebugBundle,
}

struct DebugBundleExport {
    path: PathBuf,
    bytes_written: usize,
}

#[derive(Serialize)]
struct OffdeskMaintenanceReport {
    generated_at: DateTime<Utc>,
    profile: String,
    profile_dir: String,
    read_only: bool,
    tasks: MaintenanceTaskSummary,
    background_runs: MaintenanceBackgroundSummary,
    approvals: MaintenanceApprovalSummary,
    resume_states: MaintenanceResumeSummary,
    provider_capacity: MaintenanceProviderCapacitySummary,
    adaptive_wiki_runtime_policy_ack_attention_summary: WikiRuntimePolicyAckReportSummary,
    adaptive_wiki_review_after_attention_summary: WikiReviewAfterReportSummary,
    recommended_actions: Vec<MaintenanceRecommendedAction>,
}

#[derive(Serialize)]
struct MaintenanceApprovalRequestReport {
    generated_at: DateTime<Utc>,
    action_kind: MaintenanceActionKind,
    action: String,
    project_key: String,
    request_id: String,
    task_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    target_id: Option<String>,
    risk_level: RiskLevel,
    status: String,
    detail: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    approval: Option<Value>,
    next_commands: Vec<String>,
}

#[derive(Default, Serialize)]
struct MaintenanceModeSummary {
    by_verdict: BTreeMap<String, usize>,
    by_risk: BTreeMap<String, usize>,
    review_stage_required: usize,
}

#[derive(Default, Serialize)]
struct MaintenanceTaskSummary {
    total: usize,
    by_status: BTreeMap<String, usize>,
    by_agent_mode: BTreeMap<String, usize>,
    missing_agent_mode: usize,
    mode: MaintenanceModeSummary,
}

#[derive(Default, Serialize)]
struct MaintenanceBackgroundSummary {
    total: usize,
    by_phase: BTreeMap<String, usize>,
    by_agent_mode: BTreeMap<String, usize>,
    missing_agent_mode: usize,
    mode: MaintenanceModeSummary,
}

#[derive(Default, Serialize)]
struct MaintenanceApprovalSummary {
    total: usize,
    by_status: BTreeMap<String, usize>,
    pending: usize,
}

#[derive(Default, Serialize)]
struct MaintenanceResumeSummary {
    total: usize,
    by_status: BTreeMap<String, usize>,
}

#[derive(Default, Serialize)]
struct MaintenanceProviderCapacitySummary {
    total: usize,
    by_status: BTreeMap<String, usize>,
    attention: usize,
}

#[derive(Serialize)]
struct MaintenanceRecommendedAction {
    kind: &'static str,
    detail: String,
    command: &'static str,
}

#[derive(Serialize)]
struct OffdeskCloseoutReport {
    generated_at: DateTime<Utc>,
    closeout_id: String,
    profile: String,
    profile_dir: String,
    artifact_dir: String,
    dry_run: bool,
    operator_requested_dry_run: bool,
    read_only_project_state: bool,
    filters: CloseoutFilters,
    summary: CloseoutSummary,
    tasks: Vec<CloseoutTask>,
    background_runs: Vec<CloseoutBackgroundRun>,
    file_operations: Vec<CloseoutFileOperation>,
    required_first_reads: Vec<CloseoutReadRef>,
    open_decisions: Vec<CloseoutDecision>,
    verification_commands: Vec<String>,
    review_contract: CloseoutReviewContract,
    #[serde(skip_serializing_if = "Option::is_none")]
    git_snapshot: Option<CloseoutGitSnapshot>,
    artifacts: CloseoutArtifactPaths,
}

#[derive(Default, Serialize)]
struct CloseoutFilters {
    #[serde(skip_serializing_if = "Option::is_none")]
    project_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    task_id: Option<String>,
}

#[derive(Default, Serialize)]
struct CloseoutSummary {
    tasks_scanned: usize,
    background_runs_scanned: usize,
    completed_tasks: usize,
    active_or_blocked_tasks: usize,
    file_operations: usize,
    keep_operations: usize,
    archive_candidates: usize,
    delete_candidates: usize,
    operations_requiring_commercial_review: usize,
    operations_requiring_human_approval: usize,
    missing_artifacts: usize,
    return_package_required: bool,
}

#[derive(Serialize)]
struct CloseoutTask {
    task_id: String,
    request_id: String,
    project_key: String,
    status: OffdeskTaskStatus,
    capability_id: String,
    runner_kind: BackgroundRunnerKind,
    workdir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    agent_mode: Option<AdaptiveWikiAgentMode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    background_ticket_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result_artifact_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    log_artifact_path: Option<String>,
    artifact_refs: Vec<CapabilityArtifactRef>,
    preview: String,
    reason: String,
}

#[derive(Serialize)]
struct CloseoutBackgroundRun {
    ticket_id: String,
    runner_kind: BackgroundRunnerKind,
    phase: BackgroundRunnerPhase,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    request_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    working_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result_artifact_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    log_artifact_path: Option<String>,
    runtime_handle_alive: bool,
    result_artifact_present: bool,
    log_artifact_present: bool,
}

#[derive(Serialize)]
struct CloseoutFileOperation {
    operation: &'static str,
    path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    destination: Option<String>,
    source: String,
    risk: &'static str,
    reason: String,
    evidence_refs: Vec<String>,
    present: bool,
    requires_commercial_review: bool,
    requires_human_approval: bool,
}

#[derive(Serialize)]
struct CloseoutReadRef {
    path: String,
    reason: String,
    present: bool,
}

#[derive(Serialize)]
struct CloseoutDecision {
    kind: &'static str,
    detail: String,
    suggested_command: String,
}

#[derive(Serialize)]
struct CloseoutReviewContract {
    provider: String,
    required: bool,
    applies_to_operations: Vec<&'static str>,
    required_verdicts: Vec<&'static str>,
    decision_schema: Value,
    safety_rules: Vec<&'static str>,
    packet_path: String,
}

#[derive(Serialize)]
struct CloseoutGitSnapshot {
    workdir: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    status_short: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    diff_stat: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Serialize)]
struct CloseoutArtifactPaths {
    closeout_plan_json: String,
    closeout_plan_markdown: String,
    cleanup_manifest_json: String,
    commercial_review_packet: String,
    return_package_markdown: String,
}

#[derive(Serialize)]
struct CloseoutReviewRecord {
    reviewed_at: DateTime<Utc>,
    review_id: String,
    closeout_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    closeout_generated_at: Option<DateTime<Utc>>,
    profile: String,
    artifact_dir: String,
    verdict: CloseoutReviewVerdict,
    reviewer: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    review_provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    review_file: Option<String>,
    unsafe_operations: Vec<String>,
    missing_evidence: Vec<String>,
    required_first_reads: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    notes: Option<String>,
    applies_to_task_ids: Vec<String>,
    applies_to_tasks: Vec<CloseoutReviewTaskRef>,
    read_only_project_state: bool,
    applies_file_operations: bool,
    artifacts: CloseoutReviewArtifactPaths,
}

#[derive(Serialize)]
struct CloseoutReviewTaskRef {
    project_key: String,
    request_id: String,
    task_id: String,
}

#[derive(Serialize)]
struct CloseoutReviewArtifactPaths {
    closeout_plan_json: String,
    review_record_json: String,
}

pub async fn run(profile: &str, command: OffdeskCommands) -> Result<()> {
    match command {
        OffdeskCommands::Pending(args) => pending(profile, args).await,
        OffdeskCommands::Gate(args) => gate(profile, args).await,
        OffdeskCommands::Launch(args) => launch(profile, args).await,
        OffdeskCommands::Enqueue(args) => enqueue(profile, args).await,
        OffdeskCommands::Tick(args) => tick(profile, args).await,
        OffdeskCommands::Tasks(args) => tasks(profile, args).await,
        OffdeskCommands::ProviderCapacity(args) => provider_capacity(profile, args).await,
        OffdeskCommands::ProviderFallback(args) => provider_fallback(profile, args).await,
        OffdeskCommands::CancelTask(args) => cancel_task(profile, args).await,
        OffdeskCommands::RetryTask(args) => retry_task(profile, args).await,
        OffdeskCommands::ResumeTask(args) => resume_task(profile, args).await,
        OffdeskCommands::AbandonTask(args) => abandon_task(profile, args).await,
        OffdeskCommands::Poll(args) => poll(profile, args).await,
        OffdeskCommands::Ok(args) => resolve(profile, args, true).await,
        OffdeskCommands::Cancel(args) => resolve(profile, args, false).await,
        OffdeskCommands::Resume(args) => resume(profile, args).await,
        OffdeskCommands::Background(args) => background(profile, args).await,
        OffdeskCommands::Capabilities(args) => capabilities(args).await,
        OffdeskCommands::Snapshots(args) => snapshots(profile, args).await,
        OffdeskCommands::Snapshot(args) => snapshot(profile, args).await,
        OffdeskCommands::RestorePlan(args) => restore_plan(profile, args).await,
        OffdeskCommands::DebugBundle(args) => debug_bundle(profile, args).await,
        OffdeskCommands::MaintenanceReport(args) => maintenance_report(profile, args).await,
        OffdeskCommands::MaintenanceRequest(args) => maintenance_request(profile, args).await,
        OffdeskCommands::Closeout(args) => closeout(profile, args).await,
        OffdeskCommands::CloseoutReview(args) => closeout_review(profile, args).await,
        OffdeskCommands::Wiki(args) => wiki(profile, args).await,
    }
}

async fn enqueue(profile: &str, args: EnqueueArgs) -> Result<()> {
    let now = Utc::now();
    let brief = load_execution_brief(args.brief.as_ref())?;
    let task = OffdeskTask::new(
        OffdeskTaskInput {
            task_id: args.task_id,
            request_id: args.request_id,
            project_key: args.project_key,
            capability_id: args.capability_id,
            runner_kind: args.runner,
            command: args.command,
            workdir: args
                .workdir
                .unwrap_or(std::env::current_dir()?)
                .to_string_lossy()
                .into_owned(),
            execution_brief: brief,
            not_before: parse_rfc3339(args.not_before.as_deref())?,
            mutation_class: args.mutation_class,
            artifact_refs: args.artifact_refs,
            artifact_kind: args.artifact_kind,
            agent_mode: args.agent_mode,
            provider_id: args.provider_id,
            model: args.model,
            preview: args.preview,
            reason: args.reason,
            log_artifact_path: args
                .log_artifact
                .map(|path| path.to_string_lossy().into_owned()),
            result_artifact_path: args
                .result_artifact
                .map(|path| path.to_string_lossy().into_owned()),
        },
        now,
    );

    task_store(profile)?.enqueue(task.clone())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&task.operator_view())?);
        return Ok(());
    }

    println!("Enqueued offdesk task {}", task.task_id);
    println!("  capability: {}", task.capability_id);
    println!("  runner:     {:?}", task.runner_kind);
    Ok(())
}

async fn tick(profile: &str, args: TickArgs) -> Result<()> {
    let mut options = OffdeskTickOptions::new(Utc::now());
    options.limit = args.limit.max(1);
    options.lock_stale_after = Duration::minutes(args.lock_stale_minutes.max(1));
    options.notification_cooldown = args
        .notify_cooldown_minutes
        .map(|minutes| Duration::minutes(minutes.max(1)));
    let report = run_offdesk_tick(get_profile_dir(profile)?, options)?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    println!(
        "Tick: {} launched, {} pending approval, {} completed, {} resume pending, {} failed",
        report.launched,
        report.pending_approval,
        report.completed,
        report.resume_pending,
        report.failed
    );
    if report.provider_deferred > 0 {
        println!("  provider deferred: {}", report.provider_deferred);
    }
    if report.provider_retargeted > 0 {
        println!("  provider retargeted: {}", report.provider_retargeted);
    }
    if report.skipped > 0 {
        println!("  skipped by limit: {}", report.skipped);
    }
    print_next_safe_actions(&report.next_safe_actions);
    Ok(())
}

async fn tasks(profile: &str, args: TasksArgs) -> Result<()> {
    let mut task_views: Vec<OffdeskTaskView> = task_store(profile)?
        .load()?
        .into_iter()
        .filter(|task| task_matches_tasks_filter(task, &args))
        .map(|task| task.operator_view())
        .collect();
    if args.latest {
        task_views.sort_by_key(|task| task.updated_at);
        if let Some(latest) = task_views.pop() {
            task_views = vec![latest];
        }
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&task_views)?);
        return Ok(());
    }

    if task_views.is_empty() {
        println!("No offdesk tasks found.");
        return Ok(());
    }

    print_tasks(&task_views);
    Ok(())
}

fn task_matches_tasks_filter(task: &OffdeskTask, args: &TasksArgs) -> bool {
    if let Some(project_key) = args.project_key.as_deref() {
        if task.project_key != project_key {
            return false;
        }
    }
    if let Some(task_id) = args.task_id.as_deref() {
        if task.task_id != task_id {
            return false;
        }
    }
    if !args.status.is_empty() && !args.status.contains(&task.status) {
        return false;
    }
    true
}

async fn provider_capacity(profile: &str, args: JsonArgs) -> Result<()> {
    let states = ProviderCapacityStore::new(read_only_profile_dir(profile)?).load()?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&states)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if states.is_empty() {
        println!("No provider capacity state found.");
        return Ok(());
    }

    print_provider_capacity(&states);
    Ok(())
}

async fn provider_fallback(profile: &str, args: ProviderFallbackArgs) -> Result<()> {
    let profile_dir = read_only_profile_dir(profile)?;
    let recommendation = recommend_provider_fallback(
        &ProviderCapacityStore::new(profile_dir),
        &args.provider_id,
        args.model.as_deref(),
        "operator requested provider fallback recommendation",
        &args.runner_role,
        Utc::now(),
    )?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&recommendation)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    print_provider_fallback(&recommendation);
    Ok(())
}

async fn wiki(profile: &str, args: WikiArgs) -> Result<()> {
    match args.command {
        WikiCommands::Corrections(args) => wiki_corrections(profile, args).await,
        WikiCommands::ProposalEvents(args) => wiki_proposal_events(profile, args).await,
        WikiCommands::RecordProposalEvent(args) => wiki_record_proposal_event(profile, args).await,
        WikiCommands::AcceptProposal(args) => {
            wiki_close_proposal(profile, args, AdaptiveWikiReviewProposalDecision::Accepted).await
        }
        WikiCommands::RejectProposal(args) => {
            wiki_close_proposal(profile, args, AdaptiveWikiReviewProposalDecision::Rejected).await
        }
        WikiCommands::SupersedeProposal(args) => {
            wiki_close_proposal(
                profile,
                args,
                AdaptiveWikiReviewProposalDecision::Superseded,
            )
            .await
        }
        WikiCommands::ProposalHandoff(args) => wiki_proposal_handoff(profile, args).await,
        WikiCommands::ProposalReceipt(args) => wiki_proposal_receipt(profile, args).await,
        WikiCommands::Candidates(args) => wiki_candidates(profile, args).await,
        WikiCommands::Entries(args) => wiki_entries(profile, args).await,
        WikiCommands::Show(args) => wiki_show(profile, args).await,
        WikiCommands::Projection(args) => wiki_projection(profile, args).await,
        WikiCommands::RuntimePolicyAcks(args) => wiki_runtime_policy_acks(profile, args).await,
        WikiCommands::RuntimePolicyAckReport(args) => {
            wiki_runtime_policy_ack_report(profile, args).await
        }
        WikiCommands::ReviewAfterReport(args) => wiki_review_after_report(profile, args).await,
        WikiCommands::AckRuntimePolicy(args) => wiki_ack_runtime_policy(profile, args).await,
        WikiCommands::Lint(args) => wiki_lint(profile, args).await,
        WikiCommands::ExportMarkdown(args) => wiki_export_markdown(profile, args).await,
        WikiCommands::Graph(args) => wiki_graph(profile, args).await,
        WikiCommands::Review(args) => wiki_review(profile, args).await,
        WikiCommands::EvaluateEpisode(args) => wiki_evaluate_episode(profile, args).await,
        WikiCommands::EpisodeTrace(args) => wiki_episode_trace(profile, args).await,
        WikiCommands::EvaluateRecurrence(args) => wiki_evaluate_recurrence(profile, args).await,
        WikiCommands::PromotionChain(args) => wiki_promotion_chain(profile, args).await,
        WikiCommands::Promote(args) => wiki_promote(profile, args).await,
        WikiCommands::Reject(args) => wiki_reject(profile, args).await,
        WikiCommands::Rescope(args) => wiki_rescope(profile, args).await,
        WikiCommands::Deprecate(args) => wiki_deprecate(profile, args).await,
        WikiCommands::RenewReviewAfter(args) => wiki_renew_review_after(profile, args).await,
        WikiCommands::AddCounterexample(args) => wiki_add_counterexample(profile, args).await,
        WikiCommands::UpdateRunbook(args) => wiki_update_runbook(profile, args).await,
    }
}

async fn wiki_proposal_events(profile: &str, args: WikiProposalEventsArgs) -> Result<()> {
    let mut events = wiki_store(profile)?.load_review_proposal_events()?;
    if let Some(proposal_id) = args.proposal_id.as_deref() {
        events.retain(|event| event.proposal_id == proposal_id);
    }

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&events)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if events.is_empty() {
        println!("No adaptive wiki proposal lifecycle events found.");
        return Ok(());
    }
    for event in events {
        println!(
            "{} {:?} proposal={} action={} subject={}:{} by={} {}",
            event.id,
            event.decision,
            event.proposal_id,
            event
                .proposal_action
                .map(|action| format!("{action:?}"))
                .unwrap_or_else(|| "-".to_string()),
            empty_dash(&event.subject_kind),
            empty_dash(&event.subject_id),
            empty_dash(&event.actor),
            crate::offdesk::operator_safe_text(&event.reason)
        );
    }
    Ok(())
}

async fn wiki_record_proposal_event(
    profile: &str,
    args: WikiRecordProposalEventArgs,
) -> Result<()> {
    require_non_empty_arg("proposal_id", &args.proposal_id)?;
    require_non_empty_arg("--reason", &args.reason)?;
    let evidence_refs = args
        .evidence_refs
        .iter()
        .map(|value| crate::offdesk::operator_safe_text(value.trim()))
        .filter(|value| !value.is_empty())
        .collect();
    let event = AdaptiveWikiReviewProposalEventRecord {
        id: format!("wiki_review_event_{}", Uuid::new_v4()),
        proposal_id: crate::offdesk::operator_safe_text(args.proposal_id.trim()),
        decision: args.decision,
        proposal_action: args.proposal_action,
        subject_kind: crate::offdesk::operator_safe_text(args.subject_kind.trim()),
        subject_id: crate::offdesk::operator_safe_text(args.subject_id.trim()),
        actor: crate::offdesk::operator_safe_text(args.by.trim()),
        reason: crate::offdesk::operator_safe_text(args.reason.trim()),
        evidence_refs,
        supersedes: args
            .supersedes
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(crate::offdesk::operator_safe_text),
        created_at: Utc::now(),
    };
    writable_wiki_store(profile)?.append_review_proposal_event(&event)?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&event)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "Recorded adaptive wiki proposal event {} for {} ({:?})",
        event.id, event.proposal_id, event.decision
    );
    Ok(())
}

async fn wiki_close_proposal(
    profile: &str,
    args: WikiCloseProposalArgs,
    decision: AdaptiveWikiReviewProposalDecision,
) -> Result<()> {
    require_non_empty_arg("proposal_id", &args.proposal_id)?;
    require_non_empty_arg("--reason", &args.reason)?;
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let report =
        store.generate_review_report_filtered(true, now, AdaptiveWikiReviewQueueFilter::All)?;
    let proposal = report
        .proposals
        .iter()
        .find(|proposal| proposal.id == args.proposal_id)
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Adaptive wiki review proposal not found: {}",
                args.proposal_id
            )
        })?;
    if !args.allow_decided && proposal_has_non_stale_decision(proposal) {
        bail!(
            "proposal {} already has a non-stale lifecycle decision; pass --allow-decided to record another event",
            proposal.id
        );
    }

    let event = AdaptiveWikiReviewProposalEventRecord {
        id: format!("wiki_review_event_{}", Uuid::new_v4()),
        proposal_id: crate::offdesk::operator_safe_text(&proposal.id),
        decision,
        proposal_action: Some(proposal.action),
        subject_kind: crate::offdesk::operator_safe_text(&proposal.subject_kind),
        subject_id: crate::offdesk::operator_safe_text(&proposal.subject_id),
        actor: crate::offdesk::operator_safe_text(args.by.trim()),
        reason: crate::offdesk::operator_safe_text(args.reason.trim()),
        evidence_refs: proposal_decision_evidence_refs(proposal, &args.evidence_refs),
        supersedes: args
            .supersedes
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(crate::offdesk::operator_safe_text),
        created_at: now,
    };
    store.append_review_proposal_event(&event)?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&event)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "Recorded adaptive wiki proposal event {} for {} ({:?})",
        event.id, event.proposal_id, event.decision
    );
    Ok(())
}

fn proposal_has_non_stale_decision(proposal: &AdaptiveWikiReviewProposal) -> bool {
    proposal.lifecycle.as_ref().is_some_and(|lifecycle| {
        !lifecycle.stale && lifecycle.decision != AdaptiveWikiReviewProposalDecision::Unknown
    })
}

fn proposal_decision_evidence_refs(
    proposal: &AdaptiveWikiReviewProposal,
    extra_refs: &[String],
) -> Vec<String> {
    let mut refs = Vec::new();
    for value in proposal.evidence_refs.iter().chain(extra_refs.iter()) {
        let safe = crate::offdesk::operator_safe_text(value.trim());
        if !safe.is_empty() && !refs.contains(&safe) {
            refs.push(safe);
        }
    }
    refs
}

async fn wiki_proposal_handoff(profile: &str, args: WikiProposalHandoffArgs) -> Result<()> {
    require_non_empty_arg("proposal_id", &args.proposal_id)?;
    let report = wiki_store(profile)?.generate_review_report_filtered(
        true,
        Utc::now(),
        AdaptiveWikiReviewQueueFilter::All,
    )?;
    let proposal = report
        .proposals
        .iter()
        .find(|proposal| proposal.id == args.proposal_id)
        .ok_or_else(|| {
            anyhow::anyhow!(
                "Adaptive wiki review proposal not found: {}",
                args.proposal_id
            )
        })?;
    let preview = wiki_proposal_handoff_preview(proposal, &args);

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&preview)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    print_wiki_proposal_handoff(&preview);
    Ok(())
}

async fn wiki_proposal_receipt(profile: &str, args: WikiProposalReceiptArgs) -> Result<()> {
    let proposal_id = require_non_empty_arg("proposal_id", &args.proposal_id)?;
    let audit_id = require_non_empty_arg("--audit-id", &args.audit_id)?;
    let event_id = require_non_empty_arg("--event-id", &args.event_id)?;
    let command = require_non_empty_arg("--command", &args.command)?;
    let now = Utc::now();
    let store = wiki_store(profile)?;
    let report =
        store.generate_review_report_filtered(true, now, AdaptiveWikiReviewQueueFilter::All)?;
    let audits = store.load_audit_records()?;
    let events = store.load_review_proposal_events()?;
    let audit = audits.into_iter().find(|audit| audit.id == audit_id);
    let event = events.into_iter().find(|event| event.id == event_id);
    let current_proposal = report
        .proposals
        .iter()
        .find(|proposal| proposal.id == proposal_id);
    let subject = wiki_proposal_receipt_subject(proposal_id, current_proposal, event.as_ref());
    let safe_command = crate::offdesk::operator_safe_text(command);

    let mut checks = Vec::new();
    checks.push(wiki_proposal_receipt_check(
        "preview_command_supplied",
        !safe_command.is_empty(),
        "preview command is present",
    ));
    checks.push(wiki_proposal_receipt_check(
        "audit_found",
        audit.is_some(),
        audit.as_ref().map_or_else(
            || format!("audit id {audit_id} was not found"),
            |audit| format!("found {}", audit_receipt_summary(audit)),
        ),
    ));
    checks.push(wiki_proposal_receipt_check(
        "event_found",
        event.is_some(),
        event.as_ref().map_or_else(
            || format!("event id {event_id} was not found"),
            |event| format!("found {}", event_receipt_summary(event)),
        ),
    ));
    let event_matches = event
        .as_ref()
        .is_some_and(|event| event_matches_receipt_subject(&subject, event));
    checks.push(wiki_proposal_receipt_check(
        "event_matches_proposal",
        event_matches,
        event.as_ref().map_or_else(
            || "event metadata unavailable because event was not found".to_string(),
            |event| {
                receipt_match_detail(
                    event_matches,
                    "event",
                    event_receipt_summary(event),
                    receipt_subject_summary(&subject),
                )
            },
        ),
    ));
    let audit_matches = audit
        .as_ref()
        .is_some_and(|audit| audit_matches_receipt_subject(&subject, audit));
    checks.push(wiki_proposal_receipt_check(
        "audit_matches_proposal",
        audit_matches,
        audit.as_ref().map_or_else(
            || "audit metadata unavailable because audit was not found".to_string(),
            |audit| {
                receipt_match_detail(
                    audit_matches,
                    "audit",
                    audit_receipt_summary(audit),
                    receipt_subject_summary(&subject),
                )
            },
        ),
    ));
    let audit_event_aligned = audit
        .as_ref()
        .zip(event.as_ref())
        .is_some_and(|(audit, event)| audit_event_targets_align(audit, event, &subject));
    checks.push(wiki_proposal_receipt_check(
        "audit_event_target_alignment",
        audit_event_aligned,
        audit.as_ref().zip(event.as_ref()).map_or_else(
            || "audit/event alignment unavailable because audit or event was not found".to_string(),
            |(audit, event)| {
                receipt_match_detail(
                    audit_event_aligned,
                    "audit/event",
                    audit_receipt_summary(audit),
                    event_receipt_summary(event),
                )
            },
        ),
    ));

    let blockers = checks
        .iter()
        .filter(|check| !check.passed)
        .map(|check| check.detail.clone())
        .collect::<Vec<_>>();
    let receipt = WikiProposalReceipt {
        generated_at: now,
        read_only: true,
        status: if blockers.is_empty() {
            "linked"
        } else {
            "incomplete"
        },
        proposal: subject,
        preview_command_sha256: sha256_hex(safe_command.as_bytes()),
        preview_command: safe_command,
        audit,
        event,
        checks,
        blockers,
    };
    let export = if args.export || args.output.is_some() {
        Some(write_wiki_proposal_receipt_export(
            profile,
            &receipt,
            args.output.as_ref(),
        )?)
    } else {
        None
    };

    if args.json {
        let value = if let Some(export) = export.as_ref() {
            operator_safe_json_value(serde_json::to_value(WikiProposalReceiptExportReceipt {
                exported_to: export.path.to_string_lossy().to_string(),
                bytes_written: export.bytes_written,
                receipt: &receipt,
            })?)
        } else {
            operator_safe_json_value(serde_json::to_value(&receipt)?)
        };
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    print_wiki_proposal_receipt(&receipt);
    if let Some(export) = export.as_ref() {
        println!(
            "  exported_to: {}",
            crate::offdesk::operator_safe_text(export.path.to_string_lossy().as_ref())
        );
        println!("  bytes_written: {}", export.bytes_written);
    }
    Ok(())
}

fn wiki_proposal_receipt_subject(
    proposal_id: &str,
    current_proposal: Option<&AdaptiveWikiReviewProposal>,
    event: Option<&AdaptiveWikiReviewProposalEventRecord>,
) -> WikiProposalReceiptSubject {
    if let Some(proposal) = current_proposal {
        return WikiProposalReceiptSubject {
            proposal_id: crate::offdesk::operator_safe_text(proposal_id),
            current: true,
            action: Some(proposal.action),
            subject_kind: crate::offdesk::operator_safe_text(&proposal.subject_kind),
            subject_id: crate::offdesk::operator_safe_text(&proposal.subject_id),
            lifecycle_decision: proposal
                .lifecycle
                .as_ref()
                .map(|lifecycle| lifecycle.decision),
            lifecycle_event_id: proposal
                .lifecycle
                .as_ref()
                .map(|lifecycle| crate::offdesk::operator_safe_text(&lifecycle.latest_event_id)),
            evidence_refs: proposal
                .evidence_refs
                .iter()
                .map(|value| crate::offdesk::operator_safe_text(value))
                .collect(),
        };
    }

    if let Some(event) = event.filter(|event| event.proposal_id == proposal_id) {
        return WikiProposalReceiptSubject {
            proposal_id: crate::offdesk::operator_safe_text(proposal_id),
            current: false,
            action: event.proposal_action,
            subject_kind: crate::offdesk::operator_safe_text(&event.subject_kind),
            subject_id: crate::offdesk::operator_safe_text(&event.subject_id),
            lifecycle_decision: Some(event.decision),
            lifecycle_event_id: Some(crate::offdesk::operator_safe_text(&event.id)),
            evidence_refs: event
                .evidence_refs
                .iter()
                .map(|value| crate::offdesk::operator_safe_text(value))
                .collect(),
        };
    }

    WikiProposalReceiptSubject {
        proposal_id: crate::offdesk::operator_safe_text(proposal_id),
        current: false,
        action: None,
        subject_kind: String::new(),
        subject_id: String::new(),
        lifecycle_decision: None,
        lifecycle_event_id: None,
        evidence_refs: Vec::new(),
    }
}

fn wiki_proposal_receipt_check(
    name: &'static str,
    passed: bool,
    detail: impl Into<String>,
) -> WikiProposalReceiptCheck {
    WikiProposalReceiptCheck {
        name,
        passed,
        detail: crate::offdesk::operator_safe_text(&detail.into()),
    }
}

fn receipt_match_detail(passed: bool, label: &str, actual: String, expected: String) -> String {
    if passed {
        format!("{label} matches {expected}")
    } else {
        format!("{label} mismatch: actual {actual}; expected {expected}")
    }
}

fn receipt_subject_summary(subject: &WikiProposalReceiptSubject) -> String {
    format!(
        "proposal={} action={} subject={}:{}",
        empty_dash(&subject.proposal_id),
        subject
            .action
            .map(|action| format!("{action:?}"))
            .unwrap_or_else(|| "-".to_string()),
        empty_dash(&subject.subject_kind),
        empty_dash(&subject.subject_id)
    )
}

fn event_receipt_summary(event: &AdaptiveWikiReviewProposalEventRecord) -> String {
    format!(
        "event={} decision={:?} proposal={} action={} subject={}:{}",
        event.id,
        event.decision,
        empty_dash(&event.proposal_id),
        event
            .proposal_action
            .map(|action| format!("{action:?}"))
            .unwrap_or_else(|| "-".to_string()),
        empty_dash(&event.subject_kind),
        empty_dash(&event.subject_id)
    )
}

fn audit_receipt_summary(audit: &AdaptiveWikiAuditRecord) -> String {
    format!(
        "audit={} action={:?} subject={} candidate={} entry={}",
        audit.id,
        audit.action,
        empty_dash(&audit.subject_id),
        audit.candidate_id.as_deref().unwrap_or("-"),
        audit.entry_id.as_deref().unwrap_or("-")
    )
}

fn event_matches_receipt_subject(
    subject: &WikiProposalReceiptSubject,
    event: &AdaptiveWikiReviewProposalEventRecord,
) -> bool {
    if event.proposal_id != subject.proposal_id
        || event.decision == AdaptiveWikiReviewProposalDecision::Unknown
    {
        return false;
    }
    if let (Some(subject_action), Some(event_action)) = (subject.action, event.proposal_action) {
        if subject_action != event_action {
            return false;
        }
    }
    if !subject.subject_kind.is_empty()
        && !event.subject_kind.is_empty()
        && subject.subject_kind != event.subject_kind
    {
        return false;
    }
    if !subject.subject_id.is_empty()
        && !event.subject_id.is_empty()
        && subject.subject_id != event.subject_id
    {
        return false;
    }
    true
}

fn audit_matches_receipt_subject(
    subject: &WikiProposalReceiptSubject,
    audit: &AdaptiveWikiAuditRecord,
) -> bool {
    let Some(action) = subject.action else {
        return false;
    };
    if subject.subject_id.is_empty() || subject.subject_kind.is_empty() {
        return false;
    }
    match (action, subject.subject_kind.as_str()) {
        (AdaptiveWikiReviewProposalAction::Promote, "candidate") => {
            audit.action == AdaptiveWikiAuditAction::Promote
                && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::Reject, "candidate") => {
            audit.action == AdaptiveWikiAuditAction::Reject
                && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::Rescope, "entry") => {
            audit.action == AdaptiveWikiAuditAction::Rescope
                && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::Deprecate, "entry") => {
            audit.action == AdaptiveWikiAuditAction::Deprecate
                && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::AddCounterexample, "entry") => {
            audit.action == AdaptiveWikiAuditAction::AddCounterexample
                && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::RenewReview, "entry") => {
            matches!(
                audit.action,
                AdaptiveWikiAuditAction::RenewReviewAfter
                    | AdaptiveWikiAuditAction::Rescope
                    | AdaptiveWikiAuditAction::Deprecate
                    | AdaptiveWikiAuditAction::AddCounterexample
            ) && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::Split, "entry") => {
            matches!(
                audit.action,
                AdaptiveWikiAuditAction::Rescope | AdaptiveWikiAuditAction::AddCounterexample
            ) && audit_targets_id(audit, &subject.subject_id)
        }
        (AdaptiveWikiReviewProposalAction::Merge, "entry") => {
            audit.action == AdaptiveWikiAuditAction::Deprecate
                && (audit_targets_id(audit, &subject.subject_id) || {
                    let target = audit_primary_target_id(audit);
                    let target_ref = format!("entry:{target}");
                    !target.is_empty()
                        && subject
                            .evidence_refs
                            .iter()
                            .any(|evidence_ref| evidence_ref == &target_ref)
                })
        }
        _ => false,
    }
}

fn audit_event_targets_align(
    audit: &AdaptiveWikiAuditRecord,
    event: &AdaptiveWikiReviewProposalEventRecord,
    subject: &WikiProposalReceiptSubject,
) -> bool {
    if !event.subject_id.is_empty() && audit_targets_id(audit, &event.subject_id) {
        return true;
    }
    if event.proposal_action == Some(AdaptiveWikiReviewProposalAction::Merge)
        && audit.action == AdaptiveWikiAuditAction::Deprecate
    {
        let target = audit_primary_target_id(audit);
        let target_ref = format!("entry:{target}");
        return !target.is_empty()
            && event
                .evidence_refs
                .iter()
                .chain(subject.evidence_refs.iter())
                .any(|evidence_ref| evidence_ref == &target_ref);
    }
    false
}

fn audit_targets_id(audit: &AdaptiveWikiAuditRecord, id: &str) -> bool {
    audit.subject_id == id
        || audit.candidate_id.as_deref() == Some(id)
        || audit.entry_id.as_deref() == Some(id)
}

fn audit_primary_target_id(audit: &AdaptiveWikiAuditRecord) -> &str {
    audit
        .entry_id
        .as_deref()
        .or(audit.candidate_id.as_deref())
        .unwrap_or(audit.subject_id.as_str())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn write_wiki_proposal_receipt_export(
    profile: &str,
    receipt: &WikiProposalReceipt,
    output: Option<&PathBuf>,
) -> Result<WikiProposalReceiptExport> {
    let bytes = serde_json::to_vec_pretty(receipt)?;

    if let Some(path) = output {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).with_context(|| {
                format!(
                    "create adaptive wiki proposal receipt export directory {}",
                    parent.display()
                )
            })?;
        }
        let bytes_written = write_new_file(path, &bytes).with_context(|| {
            format!(
                "write adaptive wiki proposal receipt export {}",
                path.display()
            )
        })?;
        return Ok(WikiProposalReceiptExport {
            path: path.clone(),
            bytes_written,
        });
    }

    let export_dir = read_only_profile_dir(profile)?.join("adaptive_wiki_proposal_receipts");
    fs::create_dir_all(&export_dir).with_context(|| {
        format!(
            "create adaptive wiki proposal receipt export directory {}",
            export_dir.display()
        )
    })?;
    let timestamp = receipt.generated_at.format("%Y%m%dT%H%M%SZ");
    let proposal_id = receipt.proposal.proposal_id.replace(['/', '\\', ':'], "_");
    for attempt in 0..1000 {
        let filename = if attempt == 0 {
            format!("adaptive_wiki_proposal_receipt_{timestamp}_{proposal_id}.json")
        } else {
            format!("adaptive_wiki_proposal_receipt_{timestamp}_{proposal_id}_{attempt:03}.json")
        };
        let path = export_dir.join(filename);
        match write_new_file(&path, &bytes) {
            Ok(bytes_written) => {
                return Ok(WikiProposalReceiptExport {
                    path,
                    bytes_written,
                })
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(error).with_context(|| {
                    format!(
                        "write adaptive wiki proposal receipt export {}",
                        path.display()
                    )
                });
            }
        }
    }

    bail!(
        "could not allocate adaptive wiki proposal receipt export path in {}",
        export_dir.display()
    )
}

fn wiki_proposal_handoff_preview(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> WikiProposalHandoffPreview {
    let (required_inputs, mutation_options) = manual_handoff_contract(proposal);
    let lifecycle_decision = proposal
        .lifecycle
        .as_ref()
        .map(|lifecycle| lifecycle.decision);
    let lifecycle_stale = proposal
        .lifecycle
        .as_ref()
        .is_some_and(|lifecycle| lifecycle.stale);
    if proposal_has_non_stale_decision(proposal) {
        return WikiProposalHandoffPreview {
            proposal_id: proposal.id.clone(),
            action: proposal.action,
            subject_kind: proposal.subject_kind.clone(),
            subject_id: proposal.subject_id.clone(),
            status: "blocked_by_decision",
            command: None,
            reason: "The proposal already has a non-stale lifecycle decision.".to_string(),
            lifecycle_decision,
            lifecycle_stale,
            evidence_refs: proposal.evidence_refs.clone(),
            required_inputs: Vec::new(),
            mutation_options: Vec::new(),
        };
    }

    if let Some(parameterized) = parameterized_handoff_preview(proposal, args) {
        match parameterized {
            ParameterizedHandoffPreview::Ready { command, reason } => {
                return WikiProposalHandoffPreview {
                    proposal_id: proposal.id.clone(),
                    action: proposal.action,
                    subject_kind: proposal.subject_kind.clone(),
                    subject_id: proposal.subject_id.clone(),
                    status: "ready",
                    command: Some(command),
                    reason,
                    lifecycle_decision,
                    lifecycle_stale,
                    evidence_refs: proposal.evidence_refs.clone(),
                    required_inputs: Vec::new(),
                    mutation_options: Vec::new(),
                };
            }
            ParameterizedHandoffPreview::ManualRequired { reason } => {
                return WikiProposalHandoffPreview {
                    proposal_id: proposal.id.clone(),
                    action: proposal.action,
                    subject_kind: proposal.subject_kind.clone(),
                    subject_id: proposal.subject_id.clone(),
                    status: "manual_required",
                    command: None,
                    reason,
                    lifecycle_decision,
                    lifecycle_stale,
                    evidence_refs: proposal.evidence_refs.clone(),
                    required_inputs,
                    mutation_options,
                };
            }
        }
    }

    if let Some(command) = proposal.suggested_command.as_deref() {
        return WikiProposalHandoffPreview {
            proposal_id: proposal.id.clone(),
            action: proposal.action,
            subject_kind: proposal.subject_kind.clone(),
            subject_id: proposal.subject_id.clone(),
            status: "ready",
            command: Some(crate::offdesk::operator_safe_text(command)),
            reason: "The proposal already includes an exact governed mutation command.".to_string(),
            lifecycle_decision,
            lifecycle_stale,
            evidence_refs: proposal.evidence_refs.clone(),
            required_inputs: Vec::new(),
            mutation_options: Vec::new(),
        };
    }

    if let Some(command) = fallback_proposal_handoff_command(proposal) {
        return WikiProposalHandoffPreview {
            proposal_id: proposal.id.clone(),
            action: proposal.action,
            subject_kind: proposal.subject_kind.clone(),
            subject_id: proposal.subject_id.clone(),
            status: "ready",
            command: Some(command),
            reason: "An exact governed mutation command can be derived from the proposal subject."
                .to_string(),
            lifecycle_decision,
            lifecycle_stale,
            evidence_refs: proposal.evidence_refs.clone(),
            required_inputs: Vec::new(),
            mutation_options: Vec::new(),
        };
    }

    WikiProposalHandoffPreview {
        proposal_id: proposal.id.clone(),
        action: proposal.action,
        subject_kind: proposal.subject_kind.clone(),
        subject_id: proposal.subject_id.clone(),
        status: "manual_required",
        command: None,
        reason: manual_handoff_reason(proposal).to_string(),
        lifecycle_decision,
        lifecycle_stale,
        evidence_refs: proposal.evidence_refs.clone(),
        required_inputs,
        mutation_options,
    }
}

enum ParameterizedHandoffPreview {
    Ready { command: String, reason: String },
    ManualRequired { reason: String },
}

fn parameterized_handoff_preview(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> Option<ParameterizedHandoffPreview> {
    let mutation = args.mutation?;
    Some(match mutation {
        WikiProposalHandoffMutation::Rescope => parameterized_rescope_handoff(proposal, args),
        WikiProposalHandoffMutation::Deprecate => parameterized_deprecate_handoff(proposal, args),
        WikiProposalHandoffMutation::AddCounterexample => {
            parameterized_counterexample_handoff(proposal, args)
        }
        WikiProposalHandoffMutation::DeprecateDuplicate => {
            parameterized_deprecate_duplicate_handoff(proposal, args)
        }
        WikiProposalHandoffMutation::Split => parameterized_split_handoff(proposal),
    })
}

fn parameterized_rescope_handoff(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> ParameterizedHandoffPreview {
    if proposal.subject_kind != "entry"
        || !matches!(
            proposal.action,
            AdaptiveWikiReviewProposalAction::Rescope
                | AdaptiveWikiReviewProposalAction::RenewReview
                | AdaptiveWikiReviewProposalAction::Split
        )
    {
        return manual_handoff_missing(
            "--mutation rescope is only supported for entry rescope, renew-review, or split proposals.",
        );
    }
    let Some(scope) = args.scope else {
        return manual_handoff_missing("--mutation rescope requires --scope.");
    };
    let Some(scope_ref) = handoff_arg_value(args.scope_ref.as_deref()) else {
        return manual_handoff_missing("--mutation rescope requires --scope-ref.");
    };
    let mut command = format!(
        "forager offdesk wiki rescope {} --scope {} --scope-ref {}",
        handoff_subject_arg(&proposal.subject_id),
        adaptive_wiki_scope_arg(scope),
        shell_quote_arg(&scope_ref)
    );
    if let Some(reason) = handoff_arg_value(args.reason.as_deref()) {
        command.push_str(" --reason ");
        command.push_str(&shell_quote_arg(&reason));
    }
    ParameterizedHandoffPreview::Ready {
        command,
        reason: "Operator supplied enough inputs for an exact rescope mutation preview."
            .to_string(),
    }
}

fn parameterized_deprecate_handoff(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> ParameterizedHandoffPreview {
    let supported_standard = proposal.subject_kind == "entry"
        && matches!(
            proposal.action,
            AdaptiveWikiReviewProposalAction::Deprecate
                | AdaptiveWikiReviewProposalAction::RenewReview
        );
    let supported_conflict = proposal_is_projection_conflict(proposal);
    if !supported_standard && !supported_conflict {
        return manual_handoff_missing(
            "--mutation deprecate is only supported for entry deprecate, renew-review, or projection-conflict split proposals.",
        );
    }
    let Some(reason) = handoff_arg_value(args.reason.as_deref()) else {
        return manual_handoff_missing("--mutation deprecate requires --reason.");
    };
    let target_entry_id = if supported_conflict {
        match handoff_arg_value(args.deprecated_entry_id.as_deref()) {
            Some(deprecated_entry_id) => {
                if !projection_conflict_entry_ids(proposal)
                    .iter()
                    .any(|entry_id| entry_id == &deprecated_entry_id)
                {
                    return manual_handoff_missing(
                        "--mutation deprecate for conflict proposals requires --deprecated-entry-id to match the proposal subject or a conflicting entry evidence ref.",
                    );
                }
                deprecated_entry_id
            }
            None => proposal.subject_id.clone(),
        }
    } else {
        proposal.subject_id.clone()
    };
    ParameterizedHandoffPreview::Ready {
        command: deprecate_command(&target_entry_id, &reason),
        reason: "Operator supplied enough inputs for an exact deprecate mutation preview."
            .to_string(),
    }
}

fn parameterized_counterexample_handoff(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> ParameterizedHandoffPreview {
    if proposal.subject_kind != "entry"
        || !matches!(
            proposal.action,
            AdaptiveWikiReviewProposalAction::AddCounterexample
                | AdaptiveWikiReviewProposalAction::RenewReview
                | AdaptiveWikiReviewProposalAction::Split
        )
    {
        return manual_handoff_missing(
            "--mutation add-counterexample is only supported for entry counterexample, renew-review, or split proposals.",
        );
    }
    let Some(evidence_ref) = handoff_arg_value(args.evidence_ref.as_deref()) else {
        return manual_handoff_missing("--mutation add-counterexample requires --evidence-ref.");
    };
    let Some(reason) = handoff_arg_value(args.reason.as_deref()) else {
        return manual_handoff_missing("--mutation add-counterexample requires --reason.");
    };
    ParameterizedHandoffPreview::Ready {
        command: counterexample_command(&proposal.subject_id, &evidence_ref, &reason),
        reason: "Operator supplied enough inputs for an exact add-counterexample mutation preview."
            .to_string(),
    }
}

fn parameterized_deprecate_duplicate_handoff(
    proposal: &AdaptiveWikiReviewProposal,
    args: &WikiProposalHandoffArgs,
) -> ParameterizedHandoffPreview {
    if proposal.subject_kind != "entry"
        || proposal.action != AdaptiveWikiReviewProposalAction::Merge
    {
        return manual_handoff_missing(
            "--mutation deprecate-duplicate is only supported for entry merge proposals.",
        );
    }
    let Some(deprecated_entry_id) = handoff_arg_value(args.deprecated_entry_id.as_deref()) else {
        return manual_handoff_missing(
            "--mutation deprecate-duplicate requires --deprecated-entry-id.",
        );
    };
    let Some(reason) = handoff_arg_value(args.reason.as_deref()) else {
        return manual_handoff_missing("--mutation deprecate-duplicate requires --reason.");
    };
    ParameterizedHandoffPreview::Ready {
        command: deprecate_command(&deprecated_entry_id, &reason),
        reason:
            "Operator supplied enough inputs for an exact duplicate deprecate mutation preview."
                .to_string(),
    }
}

fn parameterized_split_handoff(
    proposal: &AdaptiveWikiReviewProposal,
) -> ParameterizedHandoffPreview {
    if proposal.subject_kind != "entry"
        || proposal.action != AdaptiveWikiReviewProposalAction::Split
    {
        return manual_handoff_missing(
            "--mutation split is only supported for entry split proposals.",
        );
    }
    if proposal_is_projection_conflict(proposal) {
        return manual_handoff_missing(
            "Projection-conflict splits require one or more governed mutations; preview rescope, deprecate, or add-counterexample paths and then link the executed mutation with a proposal receipt.",
        );
    }
    manual_handoff_missing(
        "Split proposals require manual scope design before a governed mutation command is exact.",
    )
}

fn manual_handoff_missing(reason: &str) -> ParameterizedHandoffPreview {
    ParameterizedHandoffPreview::ManualRequired {
        reason: reason.to_string(),
    }
}

fn fallback_proposal_handoff_command(proposal: &AdaptiveWikiReviewProposal) -> Option<String> {
    let reason = format!("curator review: {}", proposal.title);
    let subject_id = crate::offdesk::operator_safe_text(&proposal.subject_id);
    let reason = crate::offdesk::operator_safe_text(&reason);
    match (proposal.action, proposal.subject_kind.as_str()) {
        (AdaptiveWikiReviewProposalAction::Reject, "candidate") => Some(format!(
            "forager offdesk wiki reject {} --reason {}",
            shell_quote_arg(&subject_id),
            shell_quote_arg(&reason)
        )),
        (AdaptiveWikiReviewProposalAction::Deprecate, "entry") => {
            Some(deprecate_command(&subject_id, &reason))
        }
        _ => None,
    }
}

fn manual_handoff_contract(
    proposal: &AdaptiveWikiReviewProposal,
) -> (
    Vec<WikiProposalHandoffInput>,
    Vec<WikiProposalHandoffMutationOption>,
) {
    if proposal_is_projection_conflict(proposal) {
        return projection_conflict_handoff_contract(proposal);
    }
    match (proposal.action, proposal.subject_kind.as_str()) {
        (AdaptiveWikiReviewProposalAction::Rescope, "entry") => (
            vec![
                handoff_input(
                    "scope",
                    Some("--scope"),
                    true,
                    "New entry scope: session, project, artifact_kind, or user_global.",
                ),
                handoff_input(
                    "scope_ref",
                    Some("--scope-ref"),
                    true,
                    "Scope reference for the selected scope.",
                ),
                handoff_input(
                    "reason",
                    Some("--reason"),
                    false,
                    "Operator rationale to preserve in the mutation audit.",
                ),
            ],
            vec![handoff_option(
                "rescope",
                rescope_command_template(&proposal.subject_id),
                vec!["scope", "scope_ref"],
                "Narrow or widen the promoted entry after reviewing correction evidence.",
            )],
        ),
        (AdaptiveWikiReviewProposalAction::RenewReview, "entry") => (
            vec![
                handoff_input(
                    "mutation",
                    None,
                    true,
                    "Operator-selected mutation path: renew_review_after, rescope, deprecate, or add_counterexample.",
                ),
                handoff_input(
                    "review_after",
                    Some("--review-after"),
                    false,
                    "Required when mutation is renew_review_after.",
                ),
                handoff_input(
                    "scope",
                    Some("--scope"),
                    false,
                    "Required when mutation is rescope.",
                ),
                handoff_input(
                    "scope_ref",
                    Some("--scope-ref"),
                    false,
                    "Required when mutation is rescope.",
                ),
                handoff_input(
                    "evidence_ref",
                    Some("--evidence-ref"),
                    false,
                    "Required when mutation is add_counterexample.",
                ),
                handoff_input(
                    "reason",
                    Some("--reason"),
                    true,
                    "Operator rationale to preserve in the mutation audit.",
                ),
            ],
            vec![
                handoff_option(
                    "renew_review_after",
                    renew_review_after_command_template(&proposal.subject_id),
                    vec!["mutation", "review_after", "reason"],
                    "Keep the entry unchanged and move its next review timestamp.",
                ),
                handoff_option(
                    "rescope",
                    rescope_command_template(&proposal.subject_id),
                    vec!["mutation", "scope", "scope_ref"],
                    "Keep the entry promoted but adjust where it applies.",
                ),
                handoff_option(
                    "deprecate",
                    deprecate_command_template(&proposal.subject_id),
                    vec!["mutation", "reason"],
                    "Retire the entry when review finds it should no longer project.",
                ),
                handoff_option(
                    "add_counterexample",
                    counterexample_command_template(&proposal.subject_id),
                    vec!["mutation", "evidence_ref", "reason"],
                    "Keep the entry but attach limiting evidence for future review.",
                ),
            ],
        ),
        (AdaptiveWikiReviewProposalAction::Split, "entry") => (
            vec![
                handoff_input(
                    "mutation",
                    None,
                    true,
                    "Operator-selected mutation path after designing the narrower split.",
                ),
                handoff_input(
                    "scope",
                    Some("--scope"),
                    false,
                    "Required when the split is represented as a rescope of the current entry.",
                ),
                handoff_input(
                    "scope_ref",
                    Some("--scope-ref"),
                    false,
                    "Required when the split is represented as a rescope of the current entry.",
                ),
                handoff_input(
                    "evidence_ref",
                    Some("--evidence-ref"),
                    false,
                    "Required when the split is preserved as counterexample evidence.",
                ),
                handoff_input(
                    "reason",
                    Some("--reason"),
                    true,
                    "Operator rationale to preserve in the mutation audit.",
                ),
            ],
            vec![
                handoff_option(
                    "rescope",
                    rescope_command_template(&proposal.subject_id),
                    vec!["mutation", "scope", "scope_ref"],
                    "Represent the split by narrowing the current entry scope.",
                ),
                handoff_option(
                    "add_counterexample",
                    counterexample_command_template(&proposal.subject_id),
                    vec!["mutation", "evidence_ref", "reason"],
                    "Represent the split pressure as limiting evidence before creating variants.",
                ),
            ],
        ),
        (AdaptiveWikiReviewProposalAction::Merge, "entry") => (
            vec![
                handoff_input(
                    "survivor_entry_id",
                    None,
                    true,
                    "Entry that should remain promoted after duplicate review.",
                ),
                handoff_input(
                    "deprecated_entry_id",
                    None,
                    true,
                    "Duplicate entry to retire with an audited deprecate mutation.",
                ),
                handoff_input(
                    "reason",
                    Some("--reason"),
                    true,
                    "Operator rationale to preserve in the mutation audit.",
                ),
            ],
            vec![handoff_option(
                "deprecate_duplicate",
                "forager offdesk wiki deprecate <deprecated-entry-id> --reason <reason>"
                    .to_string(),
                vec!["survivor_entry_id", "deprecated_entry_id", "reason"],
                "The current mutation surface represents merge cleanup by deprecating duplicates.",
            )],
        ),
        (AdaptiveWikiReviewProposalAction::AddCounterexample, "entry") => (
            vec![
                handoff_input(
                    "evidence_ref",
                    Some("--evidence-ref"),
                    true,
                    "Evidence ref that contradicts or limits the entry.",
                ),
                handoff_input(
                    "reason",
                    Some("--reason"),
                    true,
                    "Operator rationale to preserve in the mutation audit.",
                ),
            ],
            vec![handoff_option(
                "add_counterexample",
                counterexample_command_template(&proposal.subject_id),
                vec!["evidence_ref", "reason"],
                "Attach limiting evidence to the promoted entry.",
            )],
        ),
        (AdaptiveWikiReviewProposalAction::AddCounterexample, "candidate") => (
            vec![
                handoff_input(
                    "candidate_evidence_source",
                    None,
                    true,
                    "Audit or source evidence to attach to the candidate.",
                ),
                handoff_input(
                    "mutation_path",
                    None,
                    true,
                    "Operator choice for re-recording, promoting, or rejecting the candidate.",
                ),
            ],
            Vec::new(),
        ),
        _ => (Vec::new(), Vec::new()),
    }
}

fn projection_conflict_handoff_contract(
    proposal: &AdaptiveWikiReviewProposal,
) -> (
    Vec<WikiProposalHandoffInput>,
    Vec<WikiProposalHandoffMutationOption>,
) {
    (
        vec![
            handoff_input(
                "mutation",
                None,
                true,
                "Operator-selected conflict path: rescope, deprecate, split, or add_counterexample.",
            ),
            handoff_input(
                "scope",
                Some("--scope"),
                false,
                "Required when mutation is rescope.",
            ),
            handoff_input(
                "scope_ref",
                Some("--scope-ref"),
                false,
                "Required when mutation is rescope.",
            ),
            handoff_input(
                "deprecated_entry_id",
                Some("--deprecated-entry-id"),
                false,
                "Optional when mutation is deprecate; defaults to the proposal subject and may target a conflicting entry evidence ref.",
            ),
            handoff_input(
                "evidence_ref",
                Some("--evidence-ref"),
                false,
                "Required when mutation is add_counterexample.",
            ),
            handoff_input(
                "reason",
                Some("--reason"),
                true,
                "Operator rationale to preserve in the mutation audit.",
            ),
        ],
        vec![
            handoff_option(
                "rescope",
                rescope_command_template(&proposal.subject_id),
                vec!["mutation", "scope", "scope_ref"],
                "Keep the entry promoted but narrow or widen where this side of the conflict applies.",
            ),
            handoff_option(
                "deprecate",
                deprecate_command_template(&proposal.subject_id),
                vec!["mutation", "reason"],
                "Retire the proposal subject, or pass --deprecated-entry-id for a conflicting entry evidence ref.",
            ),
            handoff_option(
                "add_counterexample",
                counterexample_command_template(&proposal.subject_id),
                vec!["mutation", "evidence_ref", "reason"],
                "Keep the entry but attach limiting evidence that explains the conflict.",
            ),
            handoff_option(
                "split",
                "manual: combine rescope, deprecate, and/or add-counterexample mutations, then record a proposal receipt".to_string(),
                vec!["mutation", "reason"],
                "Use when resolving the conflict needs multiple governed wiki mutations instead of one exact command.",
            ),
        ],
    )
}

fn proposal_is_projection_conflict(proposal: &AdaptiveWikiReviewProposal) -> bool {
    proposal.action == AdaptiveWikiReviewProposalAction::Split
        && proposal.subject_kind == "entry"
        && (proposal.title == "Resolve conflicting promoted entries"
            || proposal
                .evidence_refs
                .iter()
                .any(|value| value == "projection:conflict"))
}

fn projection_conflict_entry_ids(proposal: &AdaptiveWikiReviewProposal) -> Vec<String> {
    let mut ids = vec![proposal.subject_id.clone()];
    for evidence_ref in &proposal.evidence_refs {
        let Some(entry_id) = evidence_ref.strip_prefix("entry:") else {
            continue;
        };
        let entry_id = crate::offdesk::operator_safe_text(entry_id);
        if !entry_id.is_empty() && !ids.iter().any(|existing| existing == &entry_id) {
            ids.push(entry_id);
        }
    }
    ids
}

fn handoff_input(
    name: &'static str,
    cli_flag: Option<&'static str>,
    required: bool,
    description: &'static str,
) -> WikiProposalHandoffInput {
    WikiProposalHandoffInput {
        name,
        cli_flag,
        required,
        description,
    }
}

fn handoff_option(
    mutation: &'static str,
    command_template: String,
    required_inputs: Vec<&'static str>,
    description: &'static str,
) -> WikiProposalHandoffMutationOption {
    WikiProposalHandoffMutationOption {
        mutation,
        command_template,
        required_inputs,
        description,
    }
}

fn rescope_command_template(entry_id: &str) -> String {
    format!(
        "forager offdesk wiki rescope {} --scope <scope> --scope-ref <scope-ref> --reason <reason>",
        handoff_subject_arg(entry_id)
    )
}

fn deprecate_command_template(entry_id: &str) -> String {
    format!(
        "forager offdesk wiki deprecate {} --reason <reason>",
        handoff_subject_arg(entry_id)
    )
}

fn counterexample_command_template(entry_id: &str) -> String {
    format!(
        "forager offdesk wiki add-counterexample {} --evidence-ref <evidence-ref> --reason <reason>",
        handoff_subject_arg(entry_id)
    )
}

fn renew_review_after_command_template(entry_id: &str) -> String {
    format!(
        "forager offdesk wiki renew-review-after {} --review-after <rfc3339> --reason <reason>",
        handoff_subject_arg(entry_id)
    )
}

fn deprecate_command(entry_id: &str, reason: &str) -> String {
    format!(
        "forager offdesk wiki deprecate {} --reason {}",
        handoff_subject_arg(entry_id),
        shell_quote_arg(&crate::offdesk::operator_safe_text(reason))
    )
}

fn counterexample_command(entry_id: &str, evidence_ref: &str, reason: &str) -> String {
    format!(
        "forager offdesk wiki add-counterexample {} --evidence-ref {} --reason {}",
        handoff_subject_arg(entry_id),
        shell_quote_arg(&crate::offdesk::operator_safe_text(evidence_ref)),
        shell_quote_arg(&crate::offdesk::operator_safe_text(reason))
    )
}

fn handoff_subject_arg(subject_id: &str) -> String {
    shell_quote_arg(&crate::offdesk::operator_safe_text(subject_id))
}

fn handoff_arg_value(value: Option<&str>) -> Option<String> {
    value
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(crate::offdesk::operator_safe_text)
}

fn adaptive_wiki_scope_arg(scope: AdaptiveWikiScope) -> &'static str {
    match scope {
        AdaptiveWikiScope::Session => "session",
        AdaptiveWikiScope::Project => "project",
        AdaptiveWikiScope::ArtifactKind => "artifact_kind",
        AdaptiveWikiScope::UserGlobal => "user_global",
    }
}

fn manual_handoff_reason(proposal: &AdaptiveWikiReviewProposal) -> &'static str {
    if proposal_is_projection_conflict(proposal) {
        return "Projection-conflict proposals require choosing whether to rescope, deprecate one side, preserve counterexample evidence, or split with multiple governed mutations.";
    }
    match proposal.action {
        AdaptiveWikiReviewProposalAction::Rescope => {
            "Rescope proposals require an operator-selected --scope and --scope-ref."
        }
        AdaptiveWikiReviewProposalAction::RenewReview => {
            "Renew-review proposals require choosing whether to renew, rescope, deprecate, or add evidence."
        }
        AdaptiveWikiReviewProposalAction::Split => {
            "Split proposals require manual scope design before a governed mutation command is exact."
        }
        AdaptiveWikiReviewProposalAction::Merge => {
            "Merge proposals require choosing the surviving entry and migration plan."
        }
        AdaptiveWikiReviewProposalAction::AddCounterexample => {
            "Counterexample proposals require a specific evidence ref and target mutation choice."
        }
        AdaptiveWikiReviewProposalAction::Promote
        | AdaptiveWikiReviewProposalAction::Reject
        | AdaptiveWikiReviewProposalAction::Deprecate => {
            "This proposal does not contain enough information for an exact governed mutation command."
        }
    }
}

async fn wiki_corrections(profile: &str, args: JsonArgs) -> Result<()> {
    let corrections = wiki_store(profile)?.load_correction_records()?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&corrections)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if corrections.is_empty() {
        println!("No adaptive wiki correction records found.");
        return Ok(());
    }
    for correction in corrections {
        println!(
            "{} {:?} task={} request={} entry={} {}",
            correction.id,
            correction.correction_kind,
            correction.task_id.as_deref().unwrap_or("-"),
            correction.request_id.as_deref().unwrap_or("-"),
            correction.entry_id.as_deref().unwrap_or("-"),
            crate::offdesk::operator_safe_text(&correction.summary)
        );
    }
    Ok(())
}

async fn wiki_candidates(profile: &str, args: WikiListArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    ))?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&projection.candidates)?);
        return Ok(());
    }

    if projection.candidates.is_empty() {
        println!("No adaptive wiki candidates found.");
        return Ok(());
    }

    print_wiki_candidates(&projection.candidates);
    Ok(())
}

async fn wiki_entries(profile: &str, args: WikiListArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    ))?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&projection.entries)?);
        return Ok(());
    }

    if projection.entries.is_empty() {
        println!("No adaptive wiki entries found.");
        return Ok(());
    }

    print_wiki_entries(&projection.entries);
    Ok(())
}

async fn wiki_show(profile: &str, args: WikiShowArgs) -> Result<()> {
    let projection = wiki_store(profile)?.human_projection(&AdaptiveWikiQuery::default())?;
    let result = projection
        .entries
        .into_iter()
        .find(|entry| entry.id == args.id)
        .map(|entry| WikiShowResult::Entry { entry })
        .or_else(|| {
            projection
                .candidates
                .into_iter()
                .find(|candidate| candidate.id == args.id)
                .map(|candidate| WikiShowResult::Candidate { candidate })
        });

    let Some(result) = result else {
        bail!("Adaptive wiki entry or candidate not found: {}", args.id);
    };

    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
        return Ok(());
    }

    print_wiki_show(&result);
    Ok(())
}

async fn wiki_projection(profile: &str, args: WikiProjectionArgs) -> Result<()> {
    let mut query = wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    );
    if args.runtime_agent_mode_default {
        query.agent_mode_filter = AdaptiveWikiAgentModeFilter::SharedWhenUnspecified;
    }
    let budget = wiki_projection_budget(&args);
    if args.compare_review_expired_policy {
        if args.exclude_review_expired {
            bail!(
                "--compare-review-expired-policy already compares warn and exclude policies; omit --exclude-review-expired"
            );
        }
        let comparison =
            wiki_store(profile)?.ai_projection_review_expired_policy_comparison(&query, budget)?;
        if args.json {
            println!("{}", serde_json::to_string_pretty(&comparison)?);
            return Ok(());
        }
        print_wiki_projection_comparison_report(&comparison);
        return Ok(());
    }
    let policy = wiki_projection_policy(&args);
    let report = wiki_store(profile)?.ai_projection_report_with_policy(&query, budget, policy)?;

    if args.json {
        if args.report {
            println!("{}", serde_json::to_string_pretty(&report)?);
        } else {
            println!("{}", serde_json::to_string_pretty(&report.selected)?);
        }
        return Ok(());
    }

    if args.report {
        print_wiki_projection_report(&report);
        return Ok(());
    }

    if report.selected.is_empty() {
        println!("No adaptive wiki projection entries found.");
        return Ok(());
    }

    println!(
        "{:<44} {:<16} {:<16} {:<18} INSTRUCTION",
        "ID", "SCOPE", "ACTIVATION", "AGENT_MODES"
    );
    for entry in report.selected {
        println!(
            "{:<44} {:<16} {:<16} {:<18} {}",
            entry.id,
            format!("{:?}", entry.scope).to_lowercase(),
            format!("{:?}", entry.activation_mode).to_lowercase(),
            adaptive_wiki_agent_modes_label(&entry.agent_modes),
            entry.instruction
        );
    }
    Ok(())
}

fn wiki_projection_budget(args: &WikiProjectionArgs) -> AdaptiveWikiProjectionBudget {
    let mut budget = AdaptiveWikiProjectionBudget::default();
    if let Some(max_entries) = args.max_entries {
        budget.max_entries = max_entries;
    }
    if let Some(max_context_chars) = args.max_context_chars {
        budget.max_context_chars = max_context_chars;
    }
    if let Some(max_instruction_chars) = args.max_instruction_chars {
        budget.max_instruction_chars = max_instruction_chars;
    }
    budget
}

fn wiki_projection_policy(args: &WikiProjectionArgs) -> AdaptiveWikiProjectionPolicy {
    AdaptiveWikiProjectionPolicy {
        review_expired: if args.exclude_review_expired {
            AdaptiveWikiProjectionReviewExpiredPolicy::Exclude
        } else {
            AdaptiveWikiProjectionReviewExpiredPolicy::Warn
        },
    }
}

async fn wiki_runtime_policy_acks(profile: &str, args: JsonArgs) -> Result<()> {
    let acknowledgements = wiki_store(profile)?.load_runtime_policy_acknowledgements()?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&acknowledgements)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    if acknowledgements.is_empty() {
        println!("No adaptive wiki runtime policy acknowledgements found.");
        return Ok(());
    }

    print_wiki_runtime_policy_acknowledgements(&acknowledgements);
    Ok(())
}

async fn wiki_runtime_policy_ack_report(
    profile: &str,
    args: WikiRuntimePolicyAckReportArgs,
) -> Result<()> {
    let now = Utc::now();
    let near_expiry_hours = args.near_expiry_hours.max(1);
    let near_expiry_window = Duration::hours(near_expiry_hours);
    let store = wiki_store(profile)?;
    let acknowledgements = store.load_runtime_policy_acknowledgements()?;
    let query = if args.session_id.is_some()
        || args.project_key.is_some()
        || args.artifact_kind.is_some()
        || args.agent_mode.is_some()
    {
        Some(runtime_wiki_query(
            &args.session_id,
            &args.project_key,
            &args.artifact_kind,
            args.agent_mode,
        ))
    } else {
        None
    };
    let budget = query
        .as_ref()
        .map(|_| wiki_runtime_policy_ack_report_budget(&args));
    let decision = if let (Some(query), Some(budget)) = (query.as_ref(), budget.clone()) {
        Some(
            store
                .runtime_projection_with_policy_acknowledgement(
                    query,
                    budget,
                    strict_runtime_review_expired_policy(),
                    now,
                )?
                .decision,
        )
    } else {
        None
    };
    let report = build_runtime_policy_ack_report(
        acknowledgements,
        query,
        budget,
        decision,
        near_expiry_hours,
        near_expiry_window,
        now,
    );

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    print_wiki_runtime_policy_ack_report(&report);
    Ok(())
}

async fn wiki_review_after_report(profile: &str, args: WikiReviewAfterReportArgs) -> Result<()> {
    let now = Utc::now();
    let near_expiry_hours = args.near_expiry_hours.max(1);
    let near_expiry_window = Duration::hours(near_expiry_hours);
    let query = wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    );
    let projection = wiki_store(profile)?.human_projection(&query)?;
    let report = build_review_after_report(
        projection.entries,
        query,
        near_expiry_hours,
        near_expiry_window,
        now,
    );

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&report)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    print_wiki_review_after_report(&report);
    Ok(())
}

async fn wiki_ack_runtime_policy(profile: &str, args: WikiRuntimePolicyAckArgs) -> Result<()> {
    if args.session_id.is_none()
        && args.project_key.is_none()
        && args.artifact_kind.is_none()
        && args.agent_mode.is_none()
    {
        bail!(
            "strict runtime policy acknowledgement requires at least one scope: --session-id, --project-key, --artifact-kind, or --agent-mode"
        );
    }
    let query = match args.scope_mode {
        AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery => runtime_wiki_query(
            &args.session_id,
            &args.project_key,
            &args.artifact_kind,
            args.agent_mode,
        ),
        AdaptiveWikiRuntimePolicyAckScopeMode::ProjectArtifact => {
            if args.session_id.is_some() {
                bail!("--scope-mode project-artifact must omit --session-id");
            }
            if args.project_key.is_none() || args.artifact_kind.is_none() {
                bail!("--scope-mode project-artifact requires --project-key and --artifact-kind");
            }
            runtime_wiki_query(
                &None,
                &args.project_key,
                &args.artifact_kind,
                args.agent_mode,
            )
        }
    };
    let budget = wiki_runtime_policy_ack_budget(&args);
    let acknowledgement = wiki_store(profile)?.acknowledge_runtime_strict_review_expired_policy(
        &query,
        budget,
        args.scope_mode,
        Duration::hours(args.ttl_hours.max(1)),
        &args.reason,
        Utc::now(),
    )?;

    if args.json {
        let value = operator_safe_json_value(serde_json::to_value(&acknowledgement)?);
        println!("{}", serde_json::to_string_pretty(&value)?);
        return Ok(());
    }

    println!(
        "Recorded adaptive wiki runtime policy acknowledgement {}",
        acknowledgement.id
    );
    print_wiki_runtime_policy_acknowledgement(&acknowledgement);
    Ok(())
}

fn build_review_after_report(
    entries: Vec<AdaptiveWikiHumanEntry>,
    query: AdaptiveWikiQuery,
    near_expiry_hours: i64,
    near_expiry_window: Duration,
    now: DateTime<Utc>,
) -> WikiReviewAfterReport {
    let mut summary = WikiReviewAfterReportSummary::default();
    let mut attention = Vec::new();
    for entry in entries
        .into_iter()
        .filter(|entry| entry.status == crate::offdesk::AdaptiveWikiStatus::Promoted)
    {
        summary.scoped_promoted += 1;
        let Some(review_after) = entry.review_after else {
            summary.missing_review_after += 1;
            continue;
        };
        summary.with_review_after += 1;
        if review_after <= now {
            summary.expired += 1;
            attention.push(review_after_report_item(
                entry,
                review_after,
                "expired",
                now,
            ));
        } else if review_after <= now + near_expiry_window {
            summary.near_expiry += 1;
            attention.push(review_after_report_item(
                entry,
                review_after,
                "near_expiry",
                now,
            ));
        }
    }
    summary.attention = attention.len();
    attention.sort_by_key(|entry| (review_after_status_order(&entry.status), entry.review_after));
    WikiReviewAfterReport {
        generated_at: now,
        query,
        near_expiry_hours,
        summary,
        entries: attention,
    }
}

fn review_after_report_item(
    entry: AdaptiveWikiHumanEntry,
    review_after: DateTime<Utc>,
    status: &str,
    now: DateTime<Utc>,
) -> WikiReviewAfterReportItem {
    WikiReviewAfterReportItem {
        renew_command_template: renew_review_after_command_template(&entry.id),
        id: entry.id,
        kind: entry.kind,
        scope: entry.scope,
        scope_ref: entry.scope_ref,
        review_after,
        hours_until_review: review_after.signed_duration_since(now).num_hours(),
        status: status.to_string(),
    }
}

fn review_after_status_order(status: &str) -> u8 {
    match status {
        "expired" => 0,
        "near_expiry" => 1,
        _ => 2,
    }
}

fn wiki_runtime_policy_ack_budget(args: &WikiRuntimePolicyAckArgs) -> AdaptiveWikiProjectionBudget {
    let mut budget = AdaptiveWikiProjectionBudget::default();
    if let Some(max_entries) = args.max_entries {
        budget.max_entries = max_entries;
    }
    if let Some(max_context_chars) = args.max_context_chars {
        budget.max_context_chars = max_context_chars;
    }
    if let Some(max_instruction_chars) = args.max_instruction_chars {
        budget.max_instruction_chars = max_instruction_chars;
    }
    budget
}

fn wiki_runtime_policy_ack_report_budget(
    args: &WikiRuntimePolicyAckReportArgs,
) -> AdaptiveWikiProjectionBudget {
    let mut budget = AdaptiveWikiProjectionBudget::default();
    if let Some(max_entries) = args.max_entries {
        budget.max_entries = max_entries;
    }
    if let Some(max_context_chars) = args.max_context_chars {
        budget.max_context_chars = max_context_chars;
    }
    if let Some(max_instruction_chars) = args.max_instruction_chars {
        budget.max_instruction_chars = max_instruction_chars;
    }
    budget
}

fn strict_runtime_review_expired_policy() -> AdaptiveWikiProjectionPolicy {
    AdaptiveWikiProjectionPolicy {
        review_expired: AdaptiveWikiProjectionReviewExpiredPolicy::Exclude,
    }
}

fn build_runtime_policy_ack_report(
    acknowledgements: Vec<AdaptiveWikiRuntimePolicyAcknowledgement>,
    query: Option<AdaptiveWikiQuery>,
    budget: Option<AdaptiveWikiProjectionBudget>,
    decision: Option<AdaptiveWikiRuntimePolicyDecision>,
    near_expiry_hours: i64,
    near_expiry_window: Duration,
    now: DateTime<Utc>,
) -> WikiRuntimePolicyAckReport {
    let decision_ack_id = decision
        .as_ref()
        .and_then(|decision| decision.acknowledgement_id.as_deref());
    let decision_status = decision.as_ref().map(|decision| decision.status);
    let query_ref = query.as_ref();
    let budget_ref = budget.as_ref();
    let mut summary = WikiRuntimePolicyAckReportSummary {
        total: acknowledgements.len(),
        ..WikiRuntimePolicyAckReportSummary::default()
    };
    let acknowledgements = acknowledgements
        .into_iter()
        .map(|acknowledgement| {
            let mut status = Vec::new();
            let expired = acknowledgement.expires_at <= now;
            let near_expiry =
                !expired && acknowledgement.expires_at <= now + near_expiry_window;
            if expired {
                summary.expired += 1;
                status.push("expired".to_string());
            } else {
                summary.active += 1;
                status.push("active".to_string());
            }
            if near_expiry {
                summary.near_expiry += 1;
                status.push("near_expiry".to_string());
            }
            let mut query_status = None;
            if decision_ack_id == Some(acknowledgement.id.as_str()) {
                match decision_status {
                    Some(AdaptiveWikiRuntimePolicyDecisionStatus::AppliedAcknowledged)
                    | Some(
                        AdaptiveWikiRuntimePolicyDecisionStatus::AppliedProjectArtifactAcknowledged,
                    ) => {
                        summary.query_applied += 1;
                        status.push("query_applied".to_string());
                    }
                    Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedScopeModeBlocked) => {
                        summary.query_blocked += 1;
                        status.push("query_blocked_by_session_scope".to_string());
                        query_status = decision_status;
                    }
                    Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedStaleAcknowledgement) => {
                        summary.query_stale += 1;
                        status.push("query_stale_comparison".to_string());
                        query_status = decision_status;
                    }
                    Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedExpiredAcknowledgement) => {
                        summary.query_expired += 1;
                        status.push("query_expired_acknowledgement".to_string());
                        query_status = decision_status;
                    }
                    _ => {}
                }
            }
            let suggested_action = runtime_policy_ack_suggested_action(
                &acknowledgement,
                expired,
                near_expiry,
                query_status,
                query_ref,
                budget_ref,
            );
            if suggested_action.is_some() {
                summary.suggested_actions += 1;
            }
            WikiRuntimePolicyAckReportItem {
                id: acknowledgement.id,
                scope_mode: acknowledgement.scope_mode,
                query: acknowledgement.query,
                policy: acknowledgement.policy,
                created_at: acknowledgement.created_at,
                expires_at: acknowledgement.expires_at,
                minutes_until_expiry: acknowledgement
                    .expires_at
                    .signed_duration_since(now)
                    .num_minutes(),
                status,
                review_expired_excluded: acknowledgement.review_expired_excluded,
                suggested_action,
            }
        })
        .collect();

    WikiRuntimePolicyAckReport {
        generated_at: now,
        near_expiry_hours,
        query,
        budget,
        decision,
        summary,
        acknowledgements,
    }
}

fn runtime_policy_ack_suggested_action(
    acknowledgement: &AdaptiveWikiRuntimePolicyAcknowledgement,
    expired: bool,
    near_expiry: bool,
    query_status: Option<AdaptiveWikiRuntimePolicyDecisionStatus>,
    report_query: Option<&AdaptiveWikiQuery>,
    report_budget: Option<&AdaptiveWikiProjectionBudget>,
) -> Option<WikiRuntimePolicyAckSuggestedAction> {
    match query_status {
        Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedScopeModeBlocked) => {
            let query = report_query.unwrap_or(&acknowledgement.query);
            let budget = report_budget.unwrap_or(&acknowledgement.budget);
            Some(WikiRuntimePolicyAckSuggestedAction {
                kind: "record_exact_query_acknowledgement".to_string(),
                detail: "Project/artifact acknowledgement cannot apply while session-scoped projection entries are present; review the exact query comparison and append a new exact-query acknowledgement.".to_string(),
                compare_command_template: runtime_policy_compare_command_template(query, budget),
                ack_command_template: runtime_policy_ack_command_template(
                    query,
                    budget,
                    AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery,
                ),
            })
        }
        Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedStaleAcknowledgement) => {
            Some(WikiRuntimePolicyAckSuggestedAction {
                kind: "recompare_and_append_acknowledgement".to_string(),
                detail: "The current strict runtime comparison no longer matches this acknowledgement hash; review the comparison again and append a new acknowledgement.".to_string(),
                compare_command_template: runtime_policy_compare_command_template(
                    &acknowledgement.query,
                    &acknowledgement.budget,
                ),
                ack_command_template: runtime_policy_ack_command_template(
                    &acknowledgement.query,
                    &acknowledgement.budget,
                    acknowledgement.scope_mode,
                ),
            })
        }
        Some(AdaptiveWikiRuntimePolicyDecisionStatus::StrictRequestedExpiredAcknowledgement) => {
            Some(WikiRuntimePolicyAckSuggestedAction {
                kind: "recompare_and_append_acknowledgement".to_string(),
                detail: "The matching strict runtime acknowledgement is expired; review the comparison again and append a new acknowledgement instead of extending the old record.".to_string(),
                compare_command_template: runtime_policy_compare_command_template(
                    &acknowledgement.query,
                    &acknowledgement.budget,
                ),
                ack_command_template: runtime_policy_ack_command_template(
                    &acknowledgement.query,
                    &acknowledgement.budget,
                    acknowledgement.scope_mode,
                ),
            })
        }
        _ if expired => Some(WikiRuntimePolicyAckSuggestedAction {
            kind: "recompare_and_append_acknowledgement".to_string(),
            detail: "This acknowledgement is expired; review the comparison again and append a new acknowledgement instead of extending the old record.".to_string(),
            compare_command_template: runtime_policy_compare_command_template(
                &acknowledgement.query,
                &acknowledgement.budget,
            ),
            ack_command_template: runtime_policy_ack_command_template(
                &acknowledgement.query,
                &acknowledgement.budget,
                acknowledgement.scope_mode,
            ),
        }),
        _ if near_expiry => Some(WikiRuntimePolicyAckSuggestedAction {
            kind: "review_before_expiry".to_string(),
            detail: "This acknowledgement is near expiry; review the comparison before it expires and append a new acknowledgement if strict runtime should continue.".to_string(),
            compare_command_template: runtime_policy_compare_command_template(
                &acknowledgement.query,
                &acknowledgement.budget,
            ),
            ack_command_template: runtime_policy_ack_command_template(
                &acknowledgement.query,
                &acknowledgement.budget,
                acknowledgement.scope_mode,
            ),
        }),
        _ => None,
    }
}

fn runtime_policy_compare_command_template(
    query: &AdaptiveWikiQuery,
    budget: &AdaptiveWikiProjectionBudget,
) -> String {
    let mut parts = vec![
        "forager".to_string(),
        "offdesk".to_string(),
        "wiki".to_string(),
        "projection".to_string(),
    ];
    append_runtime_policy_query_args(&mut parts, query);
    if query.agent_mode_filter == AdaptiveWikiAgentModeFilter::SharedWhenUnspecified {
        parts.push("--runtime-agent-mode-default".to_string());
    }
    append_runtime_policy_budget_args(&mut parts, budget);
    parts.push("--compare-review-expired-policy".to_string());
    parts.push("--json".to_string());
    parts.join(" ")
}

fn runtime_policy_ack_command_template(
    query: &AdaptiveWikiQuery,
    budget: &AdaptiveWikiProjectionBudget,
    scope_mode: AdaptiveWikiRuntimePolicyAckScopeMode,
) -> String {
    let mut parts = vec![
        "forager".to_string(),
        "offdesk".to_string(),
        "wiki".to_string(),
        "ack-runtime-policy".to_string(),
    ];
    if scope_mode != AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery {
        parts.push("--scope-mode".to_string());
        parts.push(shell_quote_arg(runtime_ack_scope_mode_cli_value(
            scope_mode,
        )));
    }
    append_runtime_policy_query_args(&mut parts, query);
    append_runtime_policy_budget_args(&mut parts, budget);
    parts.push("--reason".to_string());
    parts.push("<reason>".to_string());
    parts
        .into_iter()
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn append_runtime_policy_query_args(parts: &mut Vec<String>, query: &AdaptiveWikiQuery) {
    if let Some(session_id) = query.session_id.as_deref() {
        parts.push("--session-id".to_string());
        parts.push(shell_quote_arg(session_id));
    }
    if let Some(project_key) = query.project_key.as_deref() {
        parts.push("--project-key".to_string());
        parts.push(shell_quote_arg(project_key));
    }
    if let Some(artifact_kind) = query.artifact_kind.as_deref() {
        parts.push("--artifact-kind".to_string());
        parts.push(shell_quote_arg(artifact_kind));
    }
    if let Some(agent_mode) = query.agent_mode {
        parts.push("--agent-mode".to_string());
        parts.push(shell_quote_arg(adaptive_wiki_agent_mode_cli_value(
            agent_mode,
        )));
    }
}

fn append_runtime_policy_budget_args(
    parts: &mut Vec<String>,
    budget: &AdaptiveWikiProjectionBudget,
) {
    let default = AdaptiveWikiProjectionBudget::default();
    if budget.max_entries != default.max_entries {
        parts.push("--max-entries".to_string());
        parts.push(budget.max_entries.to_string());
    }
    if budget.max_context_chars != default.max_context_chars {
        parts.push("--max-context-chars".to_string());
        parts.push(budget.max_context_chars.to_string());
    }
    if budget.max_instruction_chars != default.max_instruction_chars {
        parts.push("--max-instruction-chars".to_string());
        parts.push(budget.max_instruction_chars.to_string());
    }
}

fn runtime_ack_scope_mode_cli_value(mode: AdaptiveWikiRuntimePolicyAckScopeMode) -> &'static str {
    match mode {
        AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery => "exact-query",
        AdaptiveWikiRuntimePolicyAckScopeMode::ProjectArtifact => "project-artifact",
    }
}

fn adaptive_wiki_agent_mode_cli_value(mode: AdaptiveWikiAgentMode) -> &'static str {
    match mode {
        AdaptiveWikiAgentMode::Planning => "planning",
        AdaptiveWikiAgentMode::Development => "development",
        AdaptiveWikiAgentMode::Analysis => "analysis",
        AdaptiveWikiAgentMode::Writing => "writing",
        AdaptiveWikiAgentMode::Critique => "critique",
        AdaptiveWikiAgentMode::Review => "review",
        AdaptiveWikiAgentMode::Maintenance => "maintenance",
    }
}

fn adaptive_wiki_agent_modes_label(modes: &[AdaptiveWikiAgentMode]) -> String {
    if modes.is_empty() {
        return "all".to_string();
    }
    modes
        .iter()
        .map(|mode| adaptive_wiki_agent_mode_cli_value(*mode))
        .collect::<Vec<_>>()
        .join(",")
}

async fn wiki_lint(profile: &str, args: JsonArgs) -> Result<()> {
    let report = wiki_store(profile)?.lint(Utc::now())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_lint(&report);
    Ok(())
}

async fn wiki_export_markdown(profile: &str, args: WikiExportMarkdownArgs) -> Result<()> {
    let output = args.output;
    let report = wiki_store(profile)?.export_markdown(&output, args.dry_run, Utc::now())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_markdown_export(&report);
    Ok(())
}

async fn wiki_graph(profile: &str, args: WikiGraphArgs) -> Result<()> {
    let report = wiki_store(profile)?.graph_report(Utc::now())?;
    let files = if args.output.is_some() {
        build_graph_export_files(&report)?
    } else {
        Vec::new()
    };
    if let Some(output) = args.output.as_ref() {
        if !args.dry_run {
            write_wiki_graph_export(output, &files)?;
        }
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_graph_report(&report, args.output.as_deref(), args.dry_run, files.len());
    Ok(())
}

fn write_wiki_graph_export(output: &Path, files: &[(String, String)]) -> Result<()> {
    fs::create_dir_all(output)
        .with_context(|| format!("create adaptive wiki graph export {}", output.display()))?;
    for (relative_path, content) in files {
        let path = output.join(relative_path);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).with_context(|| {
                format!(
                    "create adaptive wiki graph export directory {}",
                    parent.display()
                )
            })?;
        }
        fs::write(&path, content)
            .with_context(|| format!("write adaptive wiki graph export {}", path.display()))?;
    }
    Ok(())
}

async fn wiki_review(profile: &str, args: WikiReviewArgs) -> Result<()> {
    let store = if args.dry_run {
        wiki_store(profile)?
    } else {
        writable_wiki_store(profile)?
    };
    let queue_filter = wiki_review_queue_filter(&args)?;
    let report = store.generate_review_report_filtered(args.dry_run, Utc::now(), queue_filter)?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_review_report(&report);
    Ok(())
}

fn wiki_review_queue_filter(args: &WikiReviewArgs) -> Result<AdaptiveWikiReviewQueueFilter> {
    let selected = args.active_only as u8 + args.decided_only as u8 + args.stale_only as u8;
    if selected > 1 {
        bail!("choose only one of --active-only, --decided-only, or --stale-only");
    }
    if args.active_only {
        Ok(AdaptiveWikiReviewQueueFilter::Active)
    } else if args.decided_only {
        Ok(AdaptiveWikiReviewQueueFilter::Decided)
    } else if args.stale_only {
        Ok(AdaptiveWikiReviewQueueFilter::Stale)
    } else {
        Ok(AdaptiveWikiReviewQueueFilter::All)
    }
}

async fn wiki_evaluate_episode(profile: &str, args: WikiEpisodeArgs) -> Result<()> {
    let in_scope_query = wiki_query(
        &args.session_id,
        &args.project_key,
        &args.artifact_kind,
        args.agent_mode,
    );
    let out_of_scope_query = wiki_episode_out_of_scope_query(&args);
    let store = if args.dry_run {
        wiki_store(profile)?
    } else {
        writable_wiki_store(profile)?
    };
    let report = store.generate_episode_evaluation_report(
        &args.entry_id,
        in_scope_query,
        out_of_scope_query,
        args.dry_run,
        Utc::now(),
    )?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_episode_evaluation_report(&report);
    Ok(())
}

async fn wiki_episode_trace(profile: &str, args: WikiEpisodeTraceArgs) -> Result<()> {
    let profile_dir = if args.dry_run {
        read_only_profile_dir(profile)?
    } else {
        get_profile_dir(profile)?
    };
    let filter = AdaptiveWikiLiveEpisodeFilter {
        request_id: clean_optional_string(&args.request_id),
        task_id: clean_optional_string(&args.task_id),
        project_key: clean_optional_string(&args.project_key),
        artifact_kind: clean_optional_string(&args.artifact_kind),
        entry_id: clean_optional_string(&args.entry_id),
    };
    let store = AdaptiveWikiStore::new(&profile_dir);
    let report = store.generate_live_episode_trace_report(
        &OffdeskTaskStore::new(&profile_dir).load()?,
        &BackgroundRunStore::new(&profile_dir).load()?,
        &TaskResumeStore::new(&profile_dir).load()?,
        filter,
        args.dry_run,
        Utc::now(),
    )?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_live_episode_trace_report(&report);
    Ok(())
}

async fn wiki_evaluate_recurrence(profile: &str, args: WikiRecurrenceArgs) -> Result<()> {
    let profile_dir = if args.dry_run {
        read_only_profile_dir(profile)?
    } else {
        get_profile_dir(profile)?
    };
    let report = AdaptiveWikiStore::new(&profile_dir).generate_correction_recurrence_report(
        &OffdeskTaskStore::new(&profile_dir).load()?,
        &BackgroundRunStore::new(&profile_dir).load()?,
        &TaskResumeStore::new(&profile_dir).load()?,
        &args.entry_id,
        args.dry_run,
        Utc::now(),
    )?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_correction_recurrence_report(&report);
    Ok(())
}

async fn wiki_promotion_chain(profile: &str, args: WikiPromotionChainArgs) -> Result<()> {
    let profile_dir = if args.dry_run {
        read_only_profile_dir(profile)?
    } else {
        get_profile_dir(profile)?
    };
    let report = AdaptiveWikiStore::new(&profile_dir).generate_promotion_evidence_chain_report(
        &args.entry_id,
        args.dry_run,
        Utc::now(),
    )?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_wiki_promotion_chain_report(&report);
    Ok(())
}

async fn wiki_promote(profile: &str, args: WikiPromoteArgs) -> Result<()> {
    if args.scope_ref.is_some() && args.scope.is_none() {
        bail!("--scope-ref requires --scope for wiki promote");
    }
    if args
        .scope
        .is_some_and(|scope| scope != AdaptiveWikiScope::UserGlobal)
        && args
            .scope_ref
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .is_none()
    {
        bail!("--scope-ref is required when --scope is not user_global");
    }
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let candidate = find_wiki_candidate(&store, &args.candidate_id)?.ok_or_else(|| {
        anyhow::anyhow!("Adaptive wiki candidate not found: {}", args.candidate_id)
    })?;
    let scope_override = args.scope.map(|scope| AdaptiveWikiScopeSuggestion {
        scope,
        scope_ref: args
            .scope_ref
            .clone()
            .unwrap_or_else(|| default_wiki_scope_ref(scope)),
    });
    let entry = store
        .promote_candidate_scoped_with_agent_modes(
            &args.candidate_id,
            args.activation_mode,
            scope_override.clone(),
            args.agent_modes.clone(),
            now,
        )?
        .ok_or_else(|| {
            anyhow::anyhow!("Adaptive wiki candidate not found: {}", args.candidate_id)
        })?;
    let candidate_snapshot = human_candidate(candidate.clone());
    let entry_snapshot = human_entry(entry.clone());
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::Promote,
        subject_id: &entry.id,
        candidate_id: Some(&candidate.id),
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: None,
        before_scope: Some(wiki_candidate_scope(&candidate)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: Some(args.activation_mode),
        candidate_snapshot: Some(candidate_snapshot),
        entry_snapshot: Some(entry_snapshot.clone()),
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::Promote {
        entry: entry_snapshot,
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_reject(profile: &str, args: WikiRejectArgs) -> Result<()> {
    require_non_empty_arg("--reason", &args.reason)?;
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let candidate = store.reject_candidate(&args.candidate_id)?.ok_or_else(|| {
        anyhow::anyhow!("Adaptive wiki candidate not found: {}", args.candidate_id)
    })?;
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::Reject,
        subject_id: &candidate.id,
        candidate_id: Some(&candidate.id),
        entry_id: None,
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: None,
        before_scope: Some(wiki_candidate_scope(&candidate)),
        after_scope: None,
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: None,
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::Reject {
        candidate: human_candidate(candidate),
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_rescope(profile: &str, args: WikiRescopeArgs) -> Result<()> {
    require_non_empty_arg("--scope-ref", &args.scope_ref)?;
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let before = find_wiki_entry(&store, &args.entry_id)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let entry = store
        .rescope_entry(&args.entry_id, args.scope, &args.scope_ref, now)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::Rescope,
        subject_id: &entry.id,
        candidate_id: None,
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: None,
        before_scope: Some(wiki_entry_scope(&before)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: None,
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::Rescope {
        entry: human_entry(entry),
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_deprecate(profile: &str, args: WikiDeprecateArgs) -> Result<()> {
    require_non_empty_arg("--reason", &args.reason)?;
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let before = find_wiki_entry(&store, &args.entry_id)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let entry = store
        .deprecate_entry(&args.entry_id, now)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::Deprecate,
        subject_id: &entry.id,
        candidate_id: None,
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: None,
        before_scope: Some(wiki_entry_scope(&before)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: None,
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::Deprecate {
        entry: human_entry(entry),
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_renew_review_after(profile: &str, args: WikiRenewReviewAfterArgs) -> Result<()> {
    require_non_empty_arg("--reason", &args.reason)?;
    let now = Utc::now();
    if args.review_after <= now {
        bail!("--review-after must be in the future");
    }
    let store = writable_wiki_store(profile)?;
    let before = find_wiki_entry(&store, &args.entry_id)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let previous_review_after = before.review_after;
    let entry = store
        .renew_review_after(&args.entry_id, args.review_after, now)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let entry_snapshot = human_entry(entry.clone());
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::RenewReviewAfter,
        subject_id: &entry.id,
        candidate_id: None,
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: None,
        before_scope: Some(wiki_entry_scope(&before)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: Some(entry_snapshot.clone()),
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::RenewReviewAfter {
        entry: entry_snapshot,
        previous_review_after,
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_add_counterexample(profile: &str, args: WikiCounterexampleArgs) -> Result<()> {
    require_non_empty_arg("--evidence-ref", &args.evidence_ref)?;
    require_non_empty_arg("--reason", &args.reason)?;
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let before = find_wiki_entry(&store, &args.entry_id)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let entry = store
        .add_counterexample(&args.entry_id, &args.evidence_ref, now)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::AddCounterexample,
        subject_id: &entry.id,
        candidate_id: None,
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: Some(&args.evidence_ref),
        before_scope: Some(wiki_entry_scope(&before)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: None,
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::AddCounterexample {
        entry: human_entry(entry),
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn wiki_update_runbook(profile: &str, args: WikiRunbookArgs) -> Result<()> {
    require_non_empty_arg("--reason", &args.reason)?;
    if args.support_ref.is_empty()
        && args.capability_id.is_empty()
        && args.required_artifact_kind.is_empty()
    {
        bail!(
            "at least one --support-ref, --capability-id, or --required-artifact-kind is required"
        );
    }
    let now = Utc::now();
    let store = writable_wiki_store(profile)?;
    let before = find_wiki_entry(&store, &args.entry_id)?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    if before.kind != AdaptiveWikiKind::Procedure {
        bail!(
            "Adaptive wiki entry {} is {:?}, not Procedure",
            args.entry_id,
            before.kind
        );
    }
    let entry = store
        .update_runbook_refs(
            &args.entry_id,
            &args.support_ref,
            &args.capability_id,
            &args.required_artifact_kind,
            now,
        )?
        .ok_or_else(|| anyhow::anyhow!("Adaptive wiki entry not found: {}", args.entry_id))?;
    let audit = wiki_audit_record(WikiAuditRecordInput {
        action: AdaptiveWikiAuditAction::UpdateRunbook,
        subject_id: &entry.id,
        candidate_id: None,
        entry_id: Some(&entry.id),
        actor: &args.by,
        reason: &args.reason,
        evidence_ref: args.support_ref.first().map(String::as_str),
        before_scope: Some(wiki_entry_scope(&before)),
        after_scope: Some(wiki_entry_scope(&entry)),
        activation_mode: None,
        candidate_snapshot: None,
        entry_snapshot: None,
        now,
    });
    store.append_audit(&audit)?;
    let result = WikiMutationResult::UpdateRunbook {
        entry: human_entry(entry),
        audit,
    };
    print_wiki_mutation(&result, args.json)
}

async fn cancel_task(profile: &str, args: CancelTaskArgs) -> Result<()> {
    let report =
        task_store(profile)?.cancel_task(&args.task_id, args.reason.as_deref(), Utc::now())?;
    print_lifecycle_report(&report, args.json)
}

async fn retry_task(profile: &str, args: RetryTaskArgs) -> Result<()> {
    let now = Utc::now();
    let report = task_store(profile)?.retry_task(&args.task_id, now)?;
    let superseded_denied_approvals = if args.new_approval {
        approval_ledger(profile)?
            .supersede_denied_for_task(
                &report.task.project_key,
                &report.task.request_id,
                &report.task.task_id,
                &report.task.capability_id,
                "cli",
                now,
            )?
            .len()
    } else {
        0
    };
    print_retry_lifecycle_report(
        &report,
        superseded_denied_approvals,
        args.json,
        args.new_approval,
    )
}

async fn resume_task(profile: &str, args: TaskLifecycleArgs) -> Result<()> {
    let report = task_store(profile)?.resume_task(&args.task_id, Utc::now())?;
    print_lifecycle_report(&report, args.json)
}

async fn abandon_task(profile: &str, args: TaskLifecycleArgs) -> Result<()> {
    let report = task_store(profile)?.abandon_task(&args.task_id, Utc::now())?;
    print_lifecycle_report(&report, args.json)
}

async fn gate(profile: &str, args: GateArgs) -> Result<()> {
    let brief = load_execution_brief(args.brief.as_ref())?;

    let mut request = SchedulerGateRequest::new(
        args.capability_id,
        args.project_key,
        args.request_id,
        args.task_id,
    );
    request.mutation_class = args.mutation_class;
    request.artifact_refs = args.artifact_refs;
    request.artifact_kind = args.artifact_kind;
    request.agent_mode = args.agent_mode;
    request.preview = args.preview;
    request.reason = args.reason;
    request.source_surface = args.source_surface;
    request.ttl = Duration::minutes(args.ttl_minutes.max(1));
    request.provider_id = args.provider_id;
    request.model = args.model;

    let profile_dir = get_profile_dir(profile)?;
    let outcome = SchedulerGate::with_provider_capacity(
        ApprovalLedger::new(&profile_dir),
        ProviderCapacityStore::new(&profile_dir),
    )
    .with_adaptive_wiki(AdaptiveWikiStore::new(&profile_dir))
    .evaluate(request, brief.as_ref(), Utc::now())?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&outcome)?);
        return Ok(());
    }

    print_gate_outcome(&outcome);
    Ok(())
}

async fn launch(profile: &str, args: LaunchArgs) -> Result<()> {
    let command = args.command;
    let workdir = args.workdir;
    let log_artifact = args.log_artifact;
    let result_artifact = args.result_artifact;
    let agent_mode = args.agent_mode;
    let json = args.json;
    let brief = load_execution_brief(args.brief.as_ref())?;
    let mut gate_request = SchedulerGateRequest::new(
        args.capability_id,
        args.project_key,
        args.request_id,
        args.task_id,
    );
    gate_request.mutation_class = args.mutation_class;
    gate_request.artifact_refs = args.artifact_refs;
    gate_request.artifact_kind = args.artifact_kind;
    gate_request.agent_mode = agent_mode;
    gate_request.preview = args.preview;
    gate_request.reason = args.reason;
    gate_request.source_surface = args.source_surface;
    gate_request.ttl = Duration::minutes(args.ttl_minutes.max(1));
    gate_request.provider_id = args.provider_id;
    gate_request.model = args.model;

    let mut launch_request = BackgroundLaunchRequest::new(gate_request, args.runner);
    launch_request.ticket_id = args.ticket_id;
    launch_request.launch_spec_summary = args.launch_spec;
    launch_request.runtime_handle_alive = args.runtime_alive;
    launch_request.provider_launch_spec_reconstructable = args.provider_launch_spec_reconstructable;
    launch_request.ack_timeout_sec = args.ack_timeout_sec;

    let profile_dir = get_profile_dir(profile)?;
    let gate = SchedulerGate::with_provider_capacity(
        ApprovalLedger::new(&profile_dir),
        ProviderCapacityStore::new(&profile_dir),
    )
    .with_adaptive_wiki(AdaptiveWikiStore::new(&profile_dir));
    let store = BackgroundRunStore::new(&profile_dir);
    let now = Utc::now();
    let outcome = if let Some(command) = command {
        let mut command_spec =
            LocalCommandLaunchSpec::new(command, workdir.unwrap_or(std::env::current_dir()?));
        command_spec.log_artifact_path = log_artifact;
        command_spec.result_artifact_path = result_artifact;
        launch_background_command(
            &gate,
            &store,
            launch_request,
            brief.as_ref(),
            now,
            command_spec,
        )?
    } else {
        launch_background_run(&gate, &store, launch_request, brief.as_ref(), now)?
    };
    append_adaptive_wiki_usage_for_launch(&profile_dir, &outcome, agent_mode, now)?;

    if json {
        println!("{}", serde_json::to_string_pretty(&outcome)?);
        return Ok(());
    }

    print_gate_outcome(&outcome.gate);
    if let Some(probe) = outcome.probe {
        println!("  ticket_id: {}", probe.ticket_id);
        println!("  runner:    {:?}", probe.runner_kind);
        println!("  phase:     {:?}", probe.phase);
        if let Some(agent_mode) = probe.agent_mode {
            println!(
                "  agent_mode: {}",
                adaptive_wiki_agent_mode_cli_value(agent_mode)
            );
        }
    }
    Ok(())
}

async fn poll(profile: &str, args: PollArgs) -> Result<()> {
    let now = Utc::now();
    let notification_cooldown = args
        .notify_cooldown_minutes
        .map(|minutes| Duration::minutes(minutes.max(1)));
    let outcomes = poll_background_runs(
        &background_store(profile)?,
        args.ticket_id.as_deref(),
        now,
        notification_cooldown,
    )?;
    reconcile_tasks_with_background_outcomes(get_profile_dir(profile)?, &outcomes, now)?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&outcomes)?);
        return Ok(());
    }

    if outcomes.is_empty() {
        println!("No matching background runner probes found.");
        return Ok(());
    }

    for outcome in outcomes {
        println!(
            "{} {:?} -> {:?}: {}",
            outcome.probe.ticket_id,
            outcome.probe.runner_kind,
            outcome.decision.phase,
            outcome.decision.evidence
        );
        print_mode_assessment(&outcome.mode_assessment);
        if let Some(observed_at) = outcome.probe.last_observed_at {
            println!("  observed_at: {observed_at}");
        }
        if let Some(tail) = outcome.probe.last_log_tail.as_deref() {
            println!("  tail: {tail}");
        }
        print_next_safe_action(&outcome.next_safe_action);
    }
    Ok(())
}

async fn pending(profile: &str, args: PendingArgs) -> Result<()> {
    let ledger = approval_ledger(profile)?;
    ledger.expire_due(Utc::now())?;
    let approvals: Vec<PendingActionApproval> = ledger
        .load()?
        .into_iter()
        .filter(|approval| args.all || approval.status == ApprovalStatus::Pending)
        .collect();

    if args.json {
        println!("{}", serde_json::to_string_pretty(&approvals)?);
        return Ok(());
    }

    if approvals.is_empty() {
        println!("No offdesk approvals found.");
        return Ok(());
    }

    print_approvals(&approvals);
    Ok(())
}

async fn resolve(profile: &str, args: ResolveArgs, approve: bool) -> Result<()> {
    let ledger = approval_ledger(profile)?;
    let now = Utc::now();
    let resolved = if approve {
        ledger.approve_pending(args.approval_id.as_deref(), &args.by, now)?
    } else {
        ledger.deny_pending(args.approval_id.as_deref(), &args.by, now)?
    };

    let Some(resolved) = resolved else {
        if let Some(approval_id) = args.approval_id {
            bail!("Pending offdesk approval not found: {}", approval_id);
        }
        println!("No pending offdesk approvals.");
        return Ok(());
    };

    if !approve {
        record_approval_denial_candidate(profile, &resolved, now)?;
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&resolved)?);
        return Ok(());
    }

    let verb = if approve { "Approved" } else { "Denied" };
    println!(
        "{} offdesk approval {}: {} ({:?})",
        verb, resolved.approval_id, resolved.action, resolved.risk_level
    );
    Ok(())
}

fn record_approval_denial_candidate(
    profile: &str,
    approval: &PendingActionApproval,
    now: DateTime<Utc>,
) -> Result<()> {
    if approval.status != ApprovalStatus::Denied {
        return Ok(());
    }

    let (scope, scope_ref) = if !approval.project_key.trim().is_empty() {
        (AdaptiveWikiScope::Project, approval.project_key.clone())
    } else {
        (AdaptiveWikiScope::Session, approval.request_id.clone())
    };
    let denial_detail = first_non_empty(&[&approval.reason, &approval.preview, &approval.action])
        .unwrap_or("operator denied approval");
    let safe_action = crate::offdesk::operator_safe_text(&approval.action);
    let safe_detail = crate::offdesk::operator_safe_text(denial_detail);
    let safe_task_id = crate::offdesk::operator_safe_text(&approval.task_id);
    let claim = format!(
        "Operator denied `{}` for task `{}`: {}",
        safe_action, safe_task_id, safe_detail
    );
    let instruction = format!(
        "Before retrying `{}`, review the previous denial and ask for explicit operator confirmation.",
        safe_action
    );
    let source_refs = vec![
        format!(
            "approval:{}",
            crate::offdesk::operator_safe_text(&approval.approval_id)
        ),
        format!("task:{}", safe_task_id),
        format!(
            "request:{}",
            crate::offdesk::operator_safe_text(&approval.request_id)
        ),
    ];
    let suggested_scope = AdaptiveWikiScopeSuggestion {
        scope,
        scope_ref: scope_ref.clone(),
    };

    AdaptiveWikiStore::new(get_profile_dir(profile)?).record_candidate(
        AdaptiveWikiCandidateInput {
            kind: AdaptiveWikiKind::PolicyRule,
            scope,
            scope_ref,
            claim,
            suggested_ai_instruction: instruction,
            human_summary: "Captured from an explicit operator approval denial.".to_string(),
            evidence_ref: Some(format!(
                "approval:{}",
                crate::offdesk::operator_safe_text(&approval.approval_id)
            )),
            signal_kind: AdaptiveWikiSignalKind::ApprovalDenial,
            origin: AdaptiveWikiOrigin::OperatorExplicit,
            source_refs,
            source_hashes: Vec::new(),
            suggested_scope: Some(suggested_scope),
            agent_modes: Vec::new(),
            core_tags: vec!["risk/operator-denial".to_string()],
            proposed_tags: Vec::new(),
            review_reason:
                "Operator denied an Offdesk approval; review before promoting as durable policy."
                    .to_string(),
            confidence: AdaptiveWikiConfidence::Explicit,
        },
        now,
    )?;

    Ok(())
}

fn append_adaptive_wiki_usage_for_launch(
    profile_dir: &Path,
    outcome: &BackgroundLaunchOutcome,
    agent_mode: Option<AdaptiveWikiAgentMode>,
    now: DateTime<Utc>,
) -> Result<()> {
    let Some(probe) = outcome.probe.as_ref() else {
        return Ok(());
    };
    if probe.adaptive_wiki_entry_ids.is_empty() {
        return Ok(());
    }
    let records = build_usage_records_with_policy(
        &outcome.gate.adaptive_wiki_runtime,
        AdaptiveWikiUsageContext {
            task_id: probe.task_id.as_deref().unwrap_or("-"),
            request_id: probe.request_id.as_deref().unwrap_or("-"),
            project_key: probe.project_key.as_deref().unwrap_or("-"),
            artifact_kind: None,
            agent_mode,
            projection_kind: "runtime_probe",
            projection_policy: Some(outcome.gate.adaptive_wiki_runtime_policy),
            now,
        },
    );
    AdaptiveWikiStore::new(profile_dir).append_usage_records(&records)
}

fn background_probe_status(probe: BackgroundProbe, now: DateTime<Utc>) -> BackgroundProbeStatus {
    let decision = probe.evaluate(now);
    let mode_assessment = assess_offdesk_mode(
        probe.agent_mode,
        background_mode_lifecycle(&decision, probe.result_artifact_present),
    );
    BackgroundProbeStatus {
        probe,
        decision,
        mode_assessment,
    }
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

async fn resume(profile: &str, args: JsonArgs) -> Result<()> {
    let states = resume_store(profile)?.load()?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&states)?);
        return Ok(());
    }

    if states.is_empty() {
        println!("No task resume artifacts found.");
        return Ok(());
    }

    print_resume_states(&states);
    Ok(())
}

async fn background(profile: &str, args: JsonArgs) -> Result<()> {
    let now = Utc::now();
    let statuses: Vec<BackgroundProbeStatus> =
        poll_background_runs(&background_store(profile)?, None, now, None)?
            .into_iter()
            .map(|outcome| BackgroundProbeStatus {
                mode_assessment: outcome.mode_assessment,
                decision: outcome.decision,
                probe: outcome.probe,
            })
            .collect();

    if args.json {
        println!("{}", serde_json::to_string_pretty(&statuses)?);
        return Ok(());
    }

    if statuses.is_empty() {
        println!("No background runner probes found.");
        return Ok(());
    }

    for status in statuses {
        println!(
            "{} {:?} -> {:?}: {}",
            status.probe.ticket_id,
            status.probe.runner_kind,
            status.decision.phase,
            status.decision.evidence
        );
        print_mode_assessment(&status.mode_assessment);
        if let Some(observed_at) = status.probe.last_observed_at {
            println!("  observed_at: {observed_at}");
        }
        if let Some(tail) = status.probe.last_log_tail.as_deref() {
            println!("  tail: {tail}");
        }
    }
    Ok(())
}

async fn capabilities(args: JsonArgs) -> Result<()> {
    let registry = default_capability_registry();
    let capabilities = registry.all();

    if args.json {
        println!("{}", serde_json::to_string_pretty(capabilities)?);
        return Ok(());
    }

    print_capabilities(capabilities);
    Ok(())
}

async fn snapshots(profile: &str, args: JsonArgs) -> Result<()> {
    let store = mutation_snapshot_store(profile)?;
    let now = Utc::now();
    let items = store
        .list()?
        .into_iter()
        .map(|snapshot| {
            let verification = store.verify_snapshot(&snapshot.mutation_id, now)?;
            Ok(snapshot_list_item(snapshot, verification))
        })
        .collect::<Result<Vec<_>>>()?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&items)?);
        return Ok(());
    }

    if items.is_empty() {
        println!("No mutation snapshots found.");
        return Ok(());
    }

    print_snapshot_list(&items);
    Ok(())
}

async fn snapshot(profile: &str, args: MutationSnapshotArgs) -> Result<()> {
    let verification =
        mutation_snapshot_store(profile)?.verify_snapshot(&args.mutation_id, Utc::now())?;
    if !verification.snapshot_present {
        bail!("Mutation snapshot not found: {}", args.mutation_id);
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&verification)?);
        return Ok(());
    }

    print_snapshot_verification(&verification);
    Ok(())
}

async fn restore_plan(profile: &str, args: MutationSnapshotArgs) -> Result<()> {
    let plan = mutation_snapshot_store(profile)?.restore_plan(&args.mutation_id, Utc::now())?;
    if plan.target_path.is_empty() {
        bail!("Mutation snapshot not found: {}", args.mutation_id);
    }

    if args.json {
        println!("{}", serde_json::to_string_pretty(&plan)?);
        return Ok(());
    }

    print_restore_plan(&plan);
    Ok(())
}

async fn debug_bundle(profile: &str, args: DebugBundleArgs) -> Result<()> {
    let bundle = build_debug_bundle(profile)?;
    let export = if args.export || args.output.is_some() {
        Some(write_debug_bundle_export(
            profile,
            &bundle,
            args.output.as_ref(),
        )?)
    } else {
        None
    };

    if args.json {
        if let Some(export) = export.as_ref() {
            let receipt = DebugBundleExportReceipt {
                exported_to: operator_safe_report(export.path.to_string_lossy().as_ref()).text,
                bytes_written: export.bytes_written,
                bundle: &bundle,
            };
            println!("{}", serde_json::to_string_pretty(&receipt)?);
        } else {
            println!("{}", serde_json::to_string_pretty(&bundle)?);
        }
        return Ok(());
    }

    print_debug_bundle_summary(&bundle);
    if let Some(export) = export.as_ref() {
        println!(
            "  exported_to:        {}",
            operator_safe_report(export.path.to_string_lossy().as_ref()).text
        );
        println!("  bytes_written:      {}", export.bytes_written);
    }
    Ok(())
}

fn build_debug_bundle(profile: &str) -> Result<OffdeskDebugBundle> {
    let profile_dir = read_only_profile_dir(profile)?;
    let generated_at = Utc::now();
    let mut redactor = DebugBundleRedactor::default();

    let approvals = redactor.value(serde_json::to_value(
        ApprovalLedger::new(&profile_dir).load()?,
    )?);

    let task_views = OffdeskTaskStore::new(&profile_dir)
        .load()?
        .into_iter()
        .map(|task| task.operator_view())
        .collect::<Vec<_>>();
    let tasks = redactor.value(serde_json::to_value(task_views)?);

    let resume_states = redactor.value(serde_json::to_value(
        TaskResumeStore::new(&profile_dir).load()?,
    )?);

    let background_runs = BackgroundRunStore::new(&profile_dir)
        .load()?
        .into_iter()
        .map(|probe| background_probe_status(probe, generated_at))
        .collect::<Vec<_>>();
    let background_runs = redactor.value(serde_json::to_value(background_runs)?);

    let capabilities = redactor.value(serde_json::to_value(default_capability_registry().all())?);

    let provider_capacity = redactor.value(serde_json::to_value(
        ProviderCapacityStore::new(&profile_dir).load()?,
    )?);

    let wiki_store = AdaptiveWikiStore::new(&profile_dir);
    let all_wiki_query = crate::offdesk::AdaptiveWikiQuery {
        session_id: None,
        project_key: None,
        artifact_kind: None,
        agent_mode: None,
        agent_mode_filter: AdaptiveWikiAgentModeFilter::AllWhenUnspecified,
    };
    let wiki_projection = wiki_store.human_projection(&all_wiki_query)?;
    let adaptive_wiki_review_after_attention_summary = build_review_after_report(
        wiki_projection.entries.clone(),
        all_wiki_query,
        168,
        Duration::hours(168),
        generated_at,
    )
    .summary;
    let adaptive_wiki = redactor.value(serde_json::to_value(wiki_projection)?);
    let adaptive_wiki_usage =
        redactor.value(serde_json::to_value(wiki_store.load_usage_records()?)?);
    let adaptive_wiki_corrections =
        redactor.value(serde_json::to_value(wiki_store.load_correction_records()?)?);
    let adaptive_wiki_review_events = redactor.value(serde_json::to_value(
        wiki_store.load_review_proposal_events()?,
    )?);
    let runtime_policy_acknowledgements = wiki_store.load_runtime_policy_acknowledgements()?;
    let adaptive_wiki_runtime_policy_ack_attention_summary = build_runtime_policy_ack_report(
        runtime_policy_acknowledgements.clone(),
        None,
        None,
        None,
        6,
        Duration::hours(6),
        generated_at,
    )
    .summary;
    let adaptive_wiki_runtime_policy_acknowledgements =
        redactor.value(serde_json::to_value(runtime_policy_acknowledgements)?);

    let profile_name = if profile.is_empty() {
        DEFAULT_PROFILE
    } else {
        profile
    };
    let profile = redactor.text(profile_name);
    let profile_dir = redactor.text(profile_dir.to_string_lossy().as_ref());
    let redaction_summary = redactor.summary;
    Ok(OffdeskDebugBundle {
        generated_at,
        profile,
        profile_dir,
        read_only: true,
        redaction_applied: true,
        approvals,
        tasks,
        resume_states,
        background_runs,
        capabilities,
        provider_capacity,
        adaptive_wiki,
        adaptive_wiki_usage,
        adaptive_wiki_corrections,
        adaptive_wiki_review_events,
        adaptive_wiki_runtime_policy_acknowledgements,
        adaptive_wiki_runtime_policy_ack_attention_summary,
        adaptive_wiki_review_after_attention_summary,
        redaction_summary,
    })
}

async fn maintenance_report(profile: &str, args: MaintenanceReportArgs) -> Result<()> {
    let report = build_maintenance_report(profile, &args)?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_maintenance_report(&report);
    Ok(())
}

async fn maintenance_request(profile: &str, args: MaintenanceRequestArgs) -> Result<()> {
    let json = args.json;
    let report = build_maintenance_request(profile, args)?;

    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_maintenance_request_report(&report);
    Ok(())
}

async fn closeout(profile: &str, args: CloseoutArgs) -> Result<()> {
    let json = args.json;
    let report = build_closeout_report(profile, &args)?;

    if json {
        println!("{}", serde_json::to_string_pretty(&report)?);
        return Ok(());
    }

    print_closeout_report(&report);
    Ok(())
}

async fn closeout_review(profile: &str, args: CloseoutReviewArgs) -> Result<()> {
    let json = args.json;
    let record = build_closeout_review_record(profile, &args)?;

    if json {
        println!("{}", serde_json::to_string_pretty(&record)?);
        return Ok(());
    }

    print_closeout_review_record(&record);
    Ok(())
}

fn build_closeout_report(profile: &str, args: &CloseoutArgs) -> Result<OffdeskCloseoutReport> {
    let profile_dir = get_profile_dir(profile)?;
    let profile_name = if profile.is_empty() {
        DEFAULT_PROFILE
    } else {
        profile
    };
    let generated_at = Utc::now();
    let closeout_id = format!("closeout_{}", short_uuid());
    let artifact_dir = allocate_closeout_artifact_dir(
        &profile_dir,
        args.output.as_ref(),
        generated_at,
        &closeout_id,
    )?;

    let filters = CloseoutFilters {
        project_key: args
            .project_key
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        request_id: args
            .request_id
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        task_id: args
            .task_id
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
    };

    let tasks = OffdeskTaskStore::new(&profile_dir)
        .load()?
        .into_iter()
        .filter(|task| closeout_task_matches(task, args))
        .collect::<Vec<_>>();
    let background_runs = BackgroundRunStore::new(&profile_dir)
        .load()?
        .into_iter()
        .filter(|probe| closeout_probe_matches(probe, args))
        .collect::<Vec<_>>();

    let closeout_tasks = tasks.iter().map(closeout_task_summary).collect::<Vec<_>>();
    let closeout_background_runs = background_runs
        .iter()
        .map(closeout_background_summary)
        .collect::<Vec<_>>();

    let mut file_operations = closeout_file_operations(&tasks, &background_runs);
    file_operations.sort_by(|left, right| {
        (left.path.as_str(), left.operation).cmp(&(right.path.as_str(), right.operation))
    });
    file_operations.dedup_by(|left, right| {
        left.path == right.path && left.operation == right.operation && left.source == right.source
    });

    let mut required_first_reads = file_operations
        .iter()
        .filter(|operation| operation.present && operation.operation == "keep")
        .map(|operation| CloseoutReadRef {
            path: operation.path.clone(),
            reason: operation.reason.clone(),
            present: operation.present,
        })
        .collect::<Vec<_>>();
    required_first_reads.truncate(20);

    let git_snapshot = if args.include_git {
        closeout_git_snapshot(args, &tasks)?
    } else {
        None
    };
    let open_decisions =
        closeout_open_decisions(&tasks, &file_operations, git_snapshot.as_ref(), args);
    let verification_commands = closeout_verification_commands(args);

    let artifacts = CloseoutArtifactPaths {
        closeout_plan_json: artifact_dir
            .join("closeout_plan.json")
            .display()
            .to_string(),
        closeout_plan_markdown: artifact_dir.join("CLOSEOUT_PLAN.md").display().to_string(),
        cleanup_manifest_json: artifact_dir
            .join("cleanup_manifest.json")
            .display()
            .to_string(),
        commercial_review_packet: artifact_dir
            .join("COMMERCIAL_REVIEW_PACKET.md")
            .display()
            .to_string(),
        return_package_markdown: artifact_dir.join("RETURN_PACKAGE.md").display().to_string(),
    };
    let review_contract = CloseoutReviewContract {
        provider: crate::offdesk::operator_safe_text(&args.review_provider),
        required: true,
        applies_to_operations: vec!["archive_candidate", "delete_candidate", "move_candidate"],
        required_verdicts: vec!["approved", "revise", "blocked"],
        decision_schema: serde_json::json!({
            "verdict": "approved|revise|blocked",
            "unsafe_operations": ["operation path or id"],
            "missing_evidence": ["required file, artifact, or command"],
            "required_first_reads": ["paths the next Ondesk harness must read first"],
            "notes": "short rationale"
        }),
        safety_rules: vec![
            "Never approve delete or move for git-tracked source files without explicit human approval.",
            "Never treat closeout as completion proof; require result and review artifacts.",
            "Archive raw logs before deletion is considered.",
            "Reject plans that touch hidden config, env, mount, symlink, external drive, or system paths without dedicated evidence.",
            "Prefer keep or archive when provenance is uncertain.",
        ],
        packet_path: artifacts.commercial_review_packet.clone(),
    };

    let summary = summarize_closeout(&closeout_tasks, &closeout_background_runs, &file_operations);

    let report = OffdeskCloseoutReport {
        generated_at,
        closeout_id,
        profile: crate::offdesk::operator_safe_text(profile_name),
        profile_dir: crate::offdesk::operator_safe_text(profile_dir.to_string_lossy().as_ref()),
        artifact_dir: artifact_dir.display().to_string(),
        dry_run: true,
        operator_requested_dry_run: args.dry_run,
        read_only_project_state: true,
        filters,
        summary,
        tasks: closeout_tasks,
        background_runs: closeout_background_runs,
        file_operations,
        required_first_reads,
        open_decisions,
        verification_commands,
        review_contract,
        git_snapshot,
        artifacts,
    };

    write_closeout_artifacts(&report)?;
    Ok(report)
}

fn build_closeout_review_record(
    profile: &str,
    args: &CloseoutReviewArgs,
) -> Result<CloseoutReviewRecord> {
    let profile_dir = get_profile_dir(profile)?;
    let profile_name = if profile.is_empty() {
        DEFAULT_PROFILE
    } else {
        profile
    };
    let artifact_dir = resolve_closeout_artifact_dir(&profile_dir, args)?;
    let plan_path = artifact_dir.join("closeout_plan.json");
    let plan: Value = serde_json::from_str(&fs::read_to_string(&plan_path).with_context(|| {
        format!(
            "read closeout plan for review record {}",
            plan_path.display()
        )
    })?)
    .with_context(|| format!("parse closeout plan {}", plan_path.display()))?;

    let closeout_id = plan
        .get("closeout_id")
        .and_then(Value::as_str)
        .map(crate::offdesk::operator_safe_text)
        .ok_or_else(|| anyhow::anyhow!("closeout plan is missing closeout_id"))?;
    if let Some(expected) = args.closeout_id.as_deref() {
        let expected = crate::offdesk::operator_safe_text(expected);
        if expected != closeout_id {
            bail!(
                "closeout id mismatch: requested {}, artifact contains {}",
                expected,
                closeout_id
            );
        }
    }

    let closeout_generated_at = plan
        .get("generated_at")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc));
    let applies_to_task_ids = plan
        .get("tasks")
        .and_then(Value::as_array)
        .map(|tasks| {
            tasks
                .iter()
                .filter_map(|task| task.get("task_id").and_then(Value::as_str))
                .map(crate::offdesk::operator_safe_text)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let applies_to_tasks = plan
        .get("tasks")
        .and_then(Value::as_array)
        .map(|tasks| {
            tasks
                .iter()
                .filter_map(|task| {
                    Some(CloseoutReviewTaskRef {
                        project_key: crate::offdesk::operator_safe_text(
                            task.get("project_key")?.as_str()?,
                        ),
                        request_id: crate::offdesk::operator_safe_text(
                            task.get("request_id")?.as_str()?,
                        ),
                        task_id: crate::offdesk::operator_safe_text(task.get("task_id")?.as_str()?),
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    let reviewed_at = Utc::now();
    let review_id = format!("closeout_review_{}", short_uuid());
    let review_record_path = allocate_closeout_review_record_path(&artifact_dir, reviewed_at)?;
    let artifacts = CloseoutReviewArtifactPaths {
        closeout_plan_json: plan_path.display().to_string(),
        review_record_json: review_record_path.display().to_string(),
    };
    let record = CloseoutReviewRecord {
        reviewed_at,
        review_id,
        closeout_id,
        closeout_generated_at,
        profile: crate::offdesk::operator_safe_text(profile_name),
        artifact_dir: artifact_dir.display().to_string(),
        verdict: args.verdict,
        reviewer: crate::offdesk::operator_safe_text(args.reviewer.trim()),
        review_provider: args
            .review_provider
            .as_deref()
            .map(|value| crate::offdesk::operator_safe_text(value.trim())),
        review_file: args
            .review_file
            .as_ref()
            .map(|path| crate::offdesk::operator_safe_text(path.to_string_lossy().as_ref())),
        unsafe_operations: safe_text_list(&args.unsafe_operation),
        missing_evidence: safe_text_list(&args.missing_evidence),
        required_first_reads: safe_text_list(&args.required_first_read),
        notes: args
            .notes
            .as_deref()
            .map(|value| truncate_closeout_text(&crate::offdesk::operator_safe_text(value), 2000)),
        applies_to_task_ids,
        applies_to_tasks,
        read_only_project_state: true,
        applies_file_operations: false,
        artifacts,
    };

    write_closeout_review_record(&record)?;
    Ok(record)
}

fn resolve_closeout_artifact_dir(profile_dir: &Path, args: &CloseoutReviewArgs) -> Result<PathBuf> {
    if let Some(artifact_dir) = args.artifact_dir.as_ref() {
        return Ok(artifact_dir.clone());
    }

    let closeouts_dir = profile_dir.join("offdesk_closeouts");
    let entries = fs::read_dir(&closeouts_dir)
        .with_context(|| format!("read closeout artifact root {}", closeouts_dir.display()))?;
    let mut candidates = Vec::new();
    for entry in entries {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let plan_path = artifact_dir.join("closeout_plan.json");
        let Ok(content) = fs::read_to_string(&plan_path) else {
            continue;
        };
        let Ok(plan) = serde_json::from_str::<Value>(&content) else {
            continue;
        };
        let plan_closeout_id = plan.get("closeout_id").and_then(Value::as_str);
        if let Some(expected) = args.closeout_id.as_deref() {
            if plan_closeout_id != Some(expected) {
                continue;
            }
        }
        let generated_at = plan
            .get("generated_at")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc))
            .unwrap_or(DateTime::<Utc>::UNIX_EPOCH);
        candidates.push((generated_at, artifact_dir));
    }

    candidates.sort_by_key(|(generated_at, _)| *generated_at);
    candidates.pop().map(|(_, path)| path).ok_or_else(|| {
        if let Some(closeout_id) = args.closeout_id.as_deref() {
            anyhow::anyhow!(
                "no closeout artifact found for closeout_id {}",
                crate::offdesk::operator_safe_text(closeout_id)
            )
        } else {
            anyhow::anyhow!("no closeout artifact found; run `forager offdesk closeout` first")
        }
    })
}

fn allocate_closeout_review_record_path(
    artifact_dir: &Path,
    reviewed_at: DateTime<Utc>,
) -> Result<PathBuf> {
    fs::create_dir_all(artifact_dir)
        .with_context(|| format!("create closeout artifact dir {}", artifact_dir.display()))?;
    let timestamp = reviewed_at.format("%Y%m%dT%H%M%SZ");
    for attempt in 0..1000 {
        let filename = if attempt == 0 {
            format!("closeout_review_{timestamp}.json")
        } else {
            format!("closeout_review_{timestamp}_{attempt:03}.json")
        };
        let path = artifact_dir.join(filename);
        if !path.exists() {
            return Ok(path);
        }
    }

    bail!(
        "could not allocate closeout review record path in {}",
        artifact_dir.display()
    )
}

fn write_closeout_review_record(record: &CloseoutReviewRecord) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(record)?;
    write_new_file(Path::new(&record.artifacts.review_record_json), &bytes)
        .with_context(|| format!("write {}", record.artifacts.review_record_json))?;
    Ok(())
}

fn safe_text_list(values: &[String]) -> Vec<String> {
    values
        .iter()
        .map(|value| crate::offdesk::operator_safe_text(value.trim()))
        .filter(|value| !value.is_empty())
        .collect()
}

fn allocate_closeout_artifact_dir(
    profile_dir: &Path,
    output: Option<&PathBuf>,
    generated_at: DateTime<Utc>,
    closeout_id: &str,
) -> Result<PathBuf> {
    if let Some(output) = output {
        fs::create_dir_all(output)
            .with_context(|| format!("create closeout output directory {}", output.display()))?;
        return Ok(output.clone());
    }

    let base = profile_dir.join("offdesk_closeouts");
    fs::create_dir_all(&base)
        .with_context(|| format!("create closeout artifact root {}", base.display()))?;
    let timestamp = generated_at.format("%Y%m%dT%H%M%SZ");
    for attempt in 0..1000 {
        let dirname = if attempt == 0 {
            format!("{timestamp}_{closeout_id}")
        } else {
            format!("{timestamp}_{closeout_id}_{attempt:03}")
        };
        let path = base.join(dirname);
        match fs::create_dir(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("create closeout artifact dir {}", path.display()));
            }
        }
    }

    bail!(
        "could not allocate closeout artifact directory in {}",
        base.display()
    )
}

fn closeout_task_matches(task: &OffdeskTask, args: &CloseoutArgs) -> bool {
    option_matches(args.project_key.as_deref(), &task.project_key)
        && option_matches(args.request_id.as_deref(), &task.request_id)
        && option_matches(args.task_id.as_deref(), &task.task_id)
}

fn closeout_probe_matches(probe: &BackgroundProbe, args: &CloseoutArgs) -> bool {
    option_matches(
        args.project_key.as_deref(),
        probe.project_key.as_deref().unwrap_or(""),
    ) && option_matches(
        args.request_id.as_deref(),
        probe.request_id.as_deref().unwrap_or(""),
    ) && option_matches(
        args.task_id.as_deref(),
        probe.task_id.as_deref().unwrap_or(""),
    )
}

fn option_matches(filter: Option<&str>, value: &str) -> bool {
    match filter {
        Some(filter) => filter == value,
        None => true,
    }
}

fn closeout_task_summary(task: &OffdeskTask) -> CloseoutTask {
    let view = task.operator_view();
    CloseoutTask {
        task_id: view.task_id,
        request_id: view.request_id,
        project_key: view.project_key,
        status: view.status,
        capability_id: view.capability_id,
        runner_kind: view.runner_kind,
        workdir: crate::offdesk::operator_safe_text(&view.workdir),
        agent_mode: view.agent_mode,
        background_ticket_id: view.background_ticket_id,
        result_artifact_path: view
            .result_artifact_path
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        log_artifact_path: view
            .log_artifact_path
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        artifact_refs: view.artifact_refs,
        preview: view.preview,
        reason: view.reason,
    }
}

fn closeout_background_summary(probe: &BackgroundProbe) -> CloseoutBackgroundRun {
    CloseoutBackgroundRun {
        ticket_id: crate::offdesk::operator_safe_text(&probe.ticket_id),
        runner_kind: probe.runner_kind,
        phase: probe.phase,
        project_key: probe
            .project_key
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        request_id: probe
            .request_id
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        task_id: probe
            .task_id
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        working_dir: probe
            .working_dir
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        result_artifact_path: probe
            .result_artifact_path
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        log_artifact_path: probe
            .log_artifact_path
            .as_deref()
            .map(crate::offdesk::operator_safe_text),
        runtime_handle_alive: probe.runtime_handle_alive,
        result_artifact_present: probe.result_artifact_present,
        log_artifact_present: probe.log_artifact_present,
    }
}

struct CloseoutFileOperationInput<'a> {
    operation: &'static str,
    path: &'a str,
    destination: Option<String>,
    source: String,
    risk: &'static str,
    reason: &'a str,
    evidence_refs: Vec<String>,
    present: bool,
    requires_commercial_review: bool,
    requires_human_approval: bool,
}

fn closeout_file_operation(input: CloseoutFileOperationInput<'_>) -> CloseoutFileOperation {
    CloseoutFileOperation {
        operation: input.operation,
        path: crate::offdesk::operator_safe_text(input.path),
        destination: input
            .destination
            .map(|value| crate::offdesk::operator_safe_text(&value)),
        source: crate::offdesk::operator_safe_text(&input.source),
        risk: input.risk,
        reason: crate::offdesk::operator_safe_text(input.reason),
        evidence_refs: input
            .evidence_refs
            .into_iter()
            .map(|value| crate::offdesk::operator_safe_text(&value))
            .collect(),
        present: input.present,
        requires_commercial_review: input.requires_commercial_review,
        requires_human_approval: input.requires_human_approval,
    }
}

fn closeout_file_operations(
    tasks: &[OffdeskTask],
    background_runs: &[BackgroundProbe],
) -> Vec<CloseoutFileOperation> {
    let mut operations = Vec::new();

    for task in tasks {
        let evidence = vec![format!("task:{}", task.task_id)];
        if let Some(path) = task.result_artifact_path.as_deref() {
            operations.push(closeout_file_operation(CloseoutFileOperationInput {
                operation: "keep",
                path,
                destination: None,
                source: format!("task:{} result_artifact", task.task_id),
                risk: "low",
                reason: "Result artifacts are provenance anchors for Ondesk return.",
                evidence_refs: evidence.clone(),
                present: path_present(path, None),
                requires_commercial_review: false,
                requires_human_approval: false,
            }));
        }
        if let Some(path) = task.log_artifact_path.as_deref() {
            operations.push(closeout_file_operation(CloseoutFileOperationInput {
                operation: "archive_candidate",
                path,
                destination: archive_destination_for(path),
                source: format!("task:{} log_artifact", task.task_id),
                risk: "medium",
                reason:
                    "Raw logs should be preserved or archived before any deletion is considered.",
                evidence_refs: evidence.clone(),
                present: path_present(path, None),
                requires_commercial_review: true,
                requires_human_approval: true,
            }));
        }
        for artifact in &task.artifact_refs {
            if let Some(path) = artifact.path.as_deref() {
                operations.push(closeout_file_operation(CloseoutFileOperationInput {
                    operation: "keep",
                    path,
                    destination: None,
                    source: format!(
                        "task:{} artifact_ref:{}",
                        task.task_id, artifact.artifact_id
                    ),
                    risk: "low",
                    reason: "Declared task artifacts must remain available for review.",
                    evidence_refs: vec![
                        format!("task:{}", task.task_id),
                        format!("artifact:{}", artifact.artifact_id),
                    ],
                    present: path_present(path, Some(artifact.present)),
                    requires_commercial_review: false,
                    requires_human_approval: false,
                }));
            }
        }
    }

    for probe in background_runs {
        let evidence = vec![format!("background:{}", probe.ticket_id)];
        if let Some(path) = probe.result_artifact_path.as_deref() {
            operations.push(closeout_file_operation(CloseoutFileOperationInput {
                operation: "keep",
                path,
                destination: None,
                source: format!("background:{} result_artifact", probe.ticket_id),
                risk: "low",
                reason: "Background result artifacts are required for morning review.",
                evidence_refs: evidence.clone(),
                present: path_present(path, Some(probe.result_artifact_present)),
                requires_commercial_review: false,
                requires_human_approval: false,
            }));
        }
        if let Some(path) = probe.log_artifact_path.as_deref() {
            operations.push(closeout_file_operation(CloseoutFileOperationInput {
                operation: "archive_candidate",
                path,
                destination: archive_destination_for(path),
                source: format!("background:{} log_artifact", probe.ticket_id),
                risk: "medium",
                reason: "Background logs may be large but should be archived while referenced.",
                evidence_refs: evidence,
                present: path_present(path, Some(probe.log_artifact_present)),
                requires_commercial_review: true,
                requires_human_approval: true,
            }));
        }
    }

    operations
}

fn path_present(path: &str, explicit: Option<bool>) -> bool {
    explicit.unwrap_or(false) || Path::new(path).exists()
}

fn archive_destination_for(path: &str) -> Option<String> {
    Path::new(path)
        .file_name()
        .and_then(|name| name.to_str())
        .map(|name| format!("archive/{name}"))
}

fn closeout_git_snapshot(
    args: &CloseoutArgs,
    tasks: &[OffdeskTask],
) -> Result<Option<CloseoutGitSnapshot>> {
    let workdir = args
        .workdir
        .clone()
        .or_else(|| tasks.first().map(|task| PathBuf::from(&task.workdir)));
    let Some(workdir) = workdir else {
        return Ok(Some(CloseoutGitSnapshot {
            workdir: "-".to_string(),
            status_short: None,
            diff_stat: None,
            error: Some("no workdir supplied and no matched task workdir found".to_string()),
        }));
    };
    let workdir_label = crate::offdesk::operator_safe_text(workdir.to_string_lossy().as_ref());
    if !workdir.exists() {
        return Ok(Some(CloseoutGitSnapshot {
            workdir: workdir_label,
            status_short: None,
            diff_stat: None,
            error: Some("workdir does not exist".to_string()),
        }));
    }
    Ok(Some(CloseoutGitSnapshot {
        workdir: workdir_label,
        status_short: closeout_git_output(&workdir, &["status", "--short"])?,
        diff_stat: closeout_git_output(&workdir, &["diff", "--stat"])?,
        error: None,
    }))
}

fn closeout_git_output(workdir: &Path, args: &[&str]) -> Result<Option<String>> {
    let output = Command::new("git")
        .args(args)
        .current_dir(workdir)
        .output()?;
    let raw = if output.status.success() {
        String::from_utf8_lossy(&output.stdout).to_string()
    } else {
        String::from_utf8_lossy(&output.stderr).to_string()
    };
    let safe = crate::offdesk::operator_safe_text(raw.trim());
    if safe.is_empty() {
        Ok(None)
    } else {
        Ok(Some(truncate_closeout_text(&safe, 12_000)))
    }
}

fn closeout_open_decisions(
    tasks: &[OffdeskTask],
    operations: &[CloseoutFileOperation],
    git_snapshot: Option<&CloseoutGitSnapshot>,
    args: &CloseoutArgs,
) -> Vec<CloseoutDecision> {
    let mut decisions = Vec::new();
    let active_or_blocked = tasks
        .iter()
        .filter(|task| {
            !matches!(
                task.status,
                OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled
            )
        })
        .count();
    if active_or_blocked > 0 {
        decisions.push(CloseoutDecision {
            kind: "non_terminal_task",
            detail: format!("{active_or_blocked} matched tasks are not terminal yet."),
            suggested_command: "forager offdesk tasks --json".to_string(),
        });
    }
    let missing = operations
        .iter()
        .filter(|operation| !operation.present)
        .count();
    if missing > 0 {
        decisions.push(CloseoutDecision {
            kind: "missing_artifact",
            detail: format!("{missing} referenced artifacts are missing or not yet observed."),
            suggested_command: "forager offdesk poll --json".to_string(),
        });
    }
    let archive_candidates = operations
        .iter()
        .filter(|operation| operation.operation == "archive_candidate")
        .count();
    if archive_candidates > 0 {
        decisions.push(CloseoutDecision {
            kind: "archive_review",
            detail: format!(
                "{archive_candidates} archive candidates require commercial review and human approval."
            ),
            suggested_command: format!(
                "Review {}",
                args.output
                    .as_ref()
                    .map(|path| path.join("COMMERCIAL_REVIEW_PACKET.md").display().to_string())
                    .unwrap_or_else(|| "COMMERCIAL_REVIEW_PACKET.md".to_string())
            ),
        });
    }
    if let Some(snapshot) = git_snapshot {
        if snapshot.status_short.is_some()
            || snapshot.diff_stat.is_some()
            || snapshot.error.is_some()
        {
            decisions.push(CloseoutDecision {
                kind: "git_state_review",
                detail: "Git state is included and must be reviewed before Ondesk return."
                    .to_string(),
                suggested_command: "git status --short && git diff --stat".to_string(),
            });
        }
    }
    decisions
}

fn closeout_verification_commands(args: &CloseoutArgs) -> Vec<String> {
    let mut commands = vec![
        "forager offdesk poll --json".to_string(),
        "forager offdesk tasks --json".to_string(),
        "forager offdesk maintenance-report --json".to_string(),
        "forager offdesk wiki review --json".to_string(),
    ];
    if let Some(project_key) = args.project_key.as_deref() {
        commands.push(format!(
            "forager ondesk prompt-package --project-key {}",
            crate::offdesk::operator_safe_text(project_key)
        ));
    }
    commands
}

fn summarize_closeout(
    tasks: &[CloseoutTask],
    background_runs: &[CloseoutBackgroundRun],
    operations: &[CloseoutFileOperation],
) -> CloseoutSummary {
    let mut summary = CloseoutSummary {
        tasks_scanned: tasks.len(),
        background_runs_scanned: background_runs.len(),
        completed_tasks: tasks
            .iter()
            .filter(|task| task.status == OffdeskTaskStatus::Completed)
            .count(),
        active_or_blocked_tasks: tasks
            .iter()
            .filter(|task| {
                !matches!(
                    task.status,
                    OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled
                )
            })
            .count(),
        file_operations: operations.len(),
        return_package_required: true,
        ..CloseoutSummary::default()
    };
    for operation in operations {
        match operation.operation {
            "keep" => summary.keep_operations += 1,
            "archive_candidate" => summary.archive_candidates += 1,
            "delete_candidate" => summary.delete_candidates += 1,
            _ => {}
        }
        if operation.requires_commercial_review {
            summary.operations_requiring_commercial_review += 1;
        }
        if operation.requires_human_approval {
            summary.operations_requiring_human_approval += 1;
        }
        if !operation.present {
            summary.missing_artifacts += 1;
        }
    }
    summary
}

fn write_closeout_artifacts(report: &OffdeskCloseoutReport) -> Result<()> {
    let plan_json = serde_json::to_vec_pretty(report)?;
    write_new_file(Path::new(&report.artifacts.closeout_plan_json), &plan_json)
        .with_context(|| format!("write {}", report.artifacts.closeout_plan_json))?;

    let manifest_json = serde_json::to_vec_pretty(&report.file_operations)?;
    write_new_file(
        Path::new(&report.artifacts.cleanup_manifest_json),
        &manifest_json,
    )
    .with_context(|| format!("write {}", report.artifacts.cleanup_manifest_json))?;

    write_new_file(
        Path::new(&report.artifacts.closeout_plan_markdown),
        render_closeout_plan_markdown(report).as_bytes(),
    )
    .with_context(|| format!("write {}", report.artifacts.closeout_plan_markdown))?;

    write_new_file(
        Path::new(&report.artifacts.return_package_markdown),
        render_closeout_return_package(report).as_bytes(),
    )
    .with_context(|| format!("write {}", report.artifacts.return_package_markdown))?;

    write_new_file(
        Path::new(&report.artifacts.commercial_review_packet),
        render_commercial_review_packet(report).as_bytes(),
    )
    .with_context(|| format!("write {}", report.artifacts.commercial_review_packet))?;
    Ok(())
}

fn render_closeout_plan_markdown(report: &OffdeskCloseoutReport) -> String {
    let mut output = String::new();
    output.push_str("# Offdesk Closeout Plan\n\n");
    output.push_str(&format!("- closeout_id: {}\n", report.closeout_id));
    output.push_str(&format!("- generated_at: {}\n", report.generated_at));
    output.push_str(&format!("- profile: {}\n", report.profile));
    output.push_str("- dry_run: true\n");
    output.push_str("- project file mutations: none\n\n");
    output.push_str("## Summary\n");
    output.push_str(&format!(
        "- tasks: {} scanned, {} completed, {} active_or_blocked\n",
        report.summary.tasks_scanned,
        report.summary.completed_tasks,
        report.summary.active_or_blocked_tasks
    ));
    output.push_str(&format!(
        "- file operations: {} keep, {} archive candidates, {} delete candidates\n",
        report.summary.keep_operations,
        report.summary.archive_candidates,
        report.summary.delete_candidates
    ));
    output.push_str(&format!(
        "- commercial review required: {}\n\n",
        report.summary.operations_requiring_commercial_review
    ));
    output.push_str("## File Operations\n");
    if report.file_operations.is_empty() {
        output.push_str("- No file operations proposed.\n");
    } else {
        for operation in &report.file_operations {
            output.push_str(&format!(
                "- {} `{}` risk={} present={} review={} approval={}\n  - reason: {}\n",
                operation.operation,
                operation.path,
                operation.risk,
                operation.present,
                operation.requires_commercial_review,
                operation.requires_human_approval,
                operation.reason
            ));
        }
    }
    output.push_str("\n## Open Decisions\n");
    if report.open_decisions.is_empty() {
        output.push_str("- None recorded.\n");
    } else {
        for decision in &report.open_decisions {
            output.push_str(&format!(
                "- {}: {}\n  - command: `{}`\n",
                decision.kind, decision.detail, decision.suggested_command
            ));
        }
    }
    output
}

fn render_closeout_return_package(report: &OffdeskCloseoutReport) -> String {
    let mut output = String::new();
    output.push_str("# Ondesk Return Package\n\n");
    output.push_str("Use this package to rehydrate a fresh Ondesk harness after Offdesk work.\n\n");
    output.push_str("## Required First Reads\n");
    if report.required_first_reads.is_empty() {
        output.push_str(
            "- No present result artifacts were found. Start with `closeout_plan.json`.\n",
        );
    } else {
        for read in &report.required_first_reads {
            output.push_str(&format!("- `{}`: {}\n", read.path, read.reason));
        }
    }
    output.push_str("\n## Open Decisions\n");
    if report.open_decisions.is_empty() {
        output.push_str("- None recorded.\n");
    } else {
        for decision in &report.open_decisions {
            output.push_str(&format!("- {}: {}\n", decision.kind, decision.detail));
        }
    }
    output.push_str("\n## Verification Commands\n");
    for command in &report.verification_commands {
        output.push_str(&format!("- `{command}`\n"));
    }
    output.push_str("\n## Context Policy\n");
    output.push_str("- Treat Offdesk results as evidence, not final truth.\n");
    output.push_str("- Re-read listed artifacts before continuing work.\n");
    output.push_str("- Do not delete or move files until commercial review and human approval are both recorded.\n");
    output
}

fn render_commercial_review_packet(report: &OffdeskCloseoutReport) -> String {
    let mut output = String::new();
    output.push_str("# Commercial Model Closeout Review Packet\n\n");
    output.push_str(
        "Review the proposed closeout plan for file movement, archive, and deletion risk.\n",
    );
    output
        .push_str("Do not execute shell commands. Return only a review verdict and rationale.\n\n");
    output.push_str("## Required Verdict Schema\n");
    output.push_str("```json\n");
    output.push_str(
        "{\n  \"verdict\": \"approved|revise|blocked\",\n  \"unsafe_operations\": [],\n  \"missing_evidence\": [],\n  \"required_first_reads\": [],\n  \"notes\": \"\"\n}\n",
    );
    output.push_str("```\n\n");
    output.push_str("## Safety Rules\n");
    for rule in &report.review_contract.safety_rules {
        output.push_str(&format!("- {rule}\n"));
    }
    output.push_str("\n## Candidate Operations\n");
    if report.file_operations.is_empty() {
        output.push_str("- No file operations proposed.\n");
    } else {
        for operation in &report.file_operations {
            output.push_str(&format!(
                "- operation: {}\n  path: `{}`\n  destination: `{}`\n  risk: {}\n  present: {}\n  reason: {}\n  evidence: {}\n",
                operation.operation,
                operation.path,
                operation.destination.as_deref().unwrap_or("-"),
                operation.risk,
                operation.present,
                operation.reason,
                operation.evidence_refs.join(", ")
            ));
        }
    }
    output.push_str("\n## Open Decisions\n");
    for decision in &report.open_decisions {
        output.push_str(&format!("- {}: {}\n", decision.kind, decision.detail));
    }
    output
}

fn print_closeout_report(report: &OffdeskCloseoutReport) {
    println!("Offdesk closeout plan");
    println!("  generated_at: {}", report.generated_at);
    println!("  closeout_id:  {}", report.closeout_id);
    println!("  profile:      {}", report.profile);
    println!("  artifact_dir: {}", report.artifact_dir);
    println!(
        "  tasks:        scanned={} completed={} active_or_blocked={}",
        report.summary.tasks_scanned,
        report.summary.completed_tasks,
        report.summary.active_or_blocked_tasks
    );
    println!(
        "  operations:   keep={} archive={} delete={} review_required={}",
        report.summary.keep_operations,
        report.summary.archive_candidates,
        report.summary.delete_candidates,
        report.summary.operations_requiring_commercial_review
    );
    println!("  dry_run:      true (no project files moved or deleted)");
    println!("Artifacts:");
    println!("  plan:         {}", report.artifacts.closeout_plan_json);
    println!(
        "  markdown:     {}",
        report.artifacts.closeout_plan_markdown
    );
    println!(
        "  review:       {}",
        report.artifacts.commercial_review_packet
    );
    println!(
        "  return:       {}",
        report.artifacts.return_package_markdown
    );
    if !report.open_decisions.is_empty() {
        println!("Open decisions:");
        for decision in &report.open_decisions {
            println!("  - {}: {}", decision.kind, decision.detail);
        }
    }
}

fn print_closeout_review_record(record: &CloseoutReviewRecord) {
    println!("Offdesk closeout review");
    println!("  reviewed_at:  {}", record.reviewed_at);
    println!("  review_id:    {}", record.review_id);
    println!("  closeout_id:  {}", record.closeout_id);
    println!("  verdict:      {}", record.verdict.as_str());
    println!("  reviewer:     {}", record.reviewer);
    if let Some(provider) = record.review_provider.as_deref() {
        println!("  provider:     {provider}");
    }
    println!("  project file mutations: none");
    println!("Artifacts:");
    println!("  plan:         {}", record.artifacts.closeout_plan_json);
    println!("  review:       {}", record.artifacts.review_record_json);
    if !record.unsafe_operations.is_empty() {
        println!("Unsafe operations:");
        for operation in &record.unsafe_operations {
            println!("  - {operation}");
        }
    }
    if !record.missing_evidence.is_empty() {
        println!("Missing evidence:");
        for evidence in &record.missing_evidence {
            println!("  - {evidence}");
        }
    }
}

fn truncate_closeout_text(value: &str, max_chars: usize) -> String {
    if value.chars().count() <= max_chars {
        value.to_string()
    } else {
        format!(
            "{}...[truncated]",
            value.chars().take(max_chars).collect::<String>()
        )
    }
}

fn short_uuid() -> String {
    Uuid::new_v4().to_string()[..8].to_string()
}

fn build_maintenance_request(
    profile: &str,
    args: MaintenanceRequestArgs,
) -> Result<MaintenanceApprovalRequestReport> {
    let preview = require_non_empty_arg("--preview", &args.preview)?.to_string();
    let reason = require_non_empty_arg("--reason", &args.reason)?.to_string();
    let project_key = require_non_empty_arg("--project-key", &args.project_key)?.to_string();
    let request_id = require_non_empty_arg("--request-id", &args.request_id)?.to_string();
    let target_id = clean_optional_string(&args.target_id);
    let task_id = clean_optional_string(&args.task_id)
        .unwrap_or_else(|| maintenance_default_task_id(args.kind, target_id.as_deref()));
    let risk_level = args.risk.unwrap_or_else(|| args.kind.default_risk());
    if risk_level == RiskLevel::Safe {
        bail!("maintenance-request requires an approval-gated risk; use maintenance-report for read-only checks");
    }

    let generated_at = Utc::now();
    let action = args.kind.action_id().to_string();
    let mut request = ActionApprovalRequest::new(
        project_key.clone(),
        request_id.clone(),
        task_id.clone(),
        action.clone(),
        risk_level,
    );
    request.mutation_class = Some(action.clone());
    request.preview = preview;
    request.reason = reason;
    request.source_surface = args.source_surface;
    request.ttl = Duration::minutes(args.ttl_minutes.max(1));

    let ledger = ApprovalLedger::new(get_profile_dir(profile)?);
    let (mut session, _) = ledger.begin_session(generated_at)?;
    let pending = session.ensure_pending_without_consuming_grant(request, generated_at)?;
    session.flush()?;

    let approvals = ledger.load()?;
    let approval = pending
        .or_else(|| {
            matching_maintenance_approval(
                &approvals,
                &project_key,
                &request_id,
                &task_id,
                &action,
                risk_level,
            )
        })
        .map(|approval| serde_json::to_value(approval).map(operator_safe_json_value))
        .transpose()?;
    let approval_status = approval
        .as_ref()
        .and_then(|approval| approval["status"].as_str())
        .unwrap_or("not_created");
    let status = maintenance_request_status(approval_status).to_string();
    let detail = maintenance_request_detail(approval_status);
    let next_commands = maintenance_request_next_commands(approval.as_ref());

    Ok(MaintenanceApprovalRequestReport {
        generated_at,
        action_kind: args.kind,
        action,
        project_key: crate::offdesk::operator_safe_text(&project_key),
        request_id: crate::offdesk::operator_safe_text(&request_id),
        task_id: crate::offdesk::operator_safe_text(&task_id),
        target_id: target_id.map(|value| crate::offdesk::operator_safe_text(&value)),
        risk_level,
        status,
        detail,
        approval,
        next_commands,
    })
}

fn matching_maintenance_approval(
    approvals: &[PendingActionApproval],
    project_key: &str,
    request_id: &str,
    task_id: &str,
    action: &str,
    risk_level: RiskLevel,
) -> Option<PendingActionApproval> {
    approvals
        .iter()
        .find(|approval| {
            approval.project_key == project_key
                && approval.request_id == request_id
                && approval.task_id == task_id
                && approval.action == action
                && approval.risk_level == risk_level
                && approval.status == ApprovalStatus::Pending
        })
        .or_else(|| {
            approvals.iter().find(|approval| {
                approval.project_key == project_key
                    && approval.request_id == request_id
                    && approval.task_id == task_id
                    && approval.action == action
                    && approval.risk_level == risk_level
            })
        })
        .cloned()
}

fn maintenance_request_status(approval_status: &str) -> &'static str {
    match approval_status {
        "pending" => "pending_approval",
        "approved" => "already_approved",
        "denied" => "previously_denied",
        "expired" => "expired",
        "superseded" => "superseded",
        _ => "not_created",
    }
}

fn maintenance_request_detail(approval_status: &str) -> String {
    match approval_status {
        "pending" => "Maintenance action approval is pending or was reused.".to_string(),
        "approved" => {
            "A matching maintenance approval already exists; this command did not consume it."
                .to_string()
        }
        "denied" => {
            "A matching maintenance approval was previously denied; create a new scoped request if needed."
                .to_string()
        }
        "expired" => "A matching maintenance approval is expired.".to_string(),
        "superseded" => "A matching maintenance approval is superseded.".to_string(),
        _ => "No maintenance approval was created.".to_string(),
    }
}

fn maintenance_request_next_commands(approval: Option<&Value>) -> Vec<String> {
    let Some(approval_id) = approval.and_then(|approval| approval["approval_id"].as_str()) else {
        return vec!["forager offdesk pending".to_string()];
    };
    vec![
        format!("forager offdesk ok {approval_id}"),
        format!("forager offdesk deny {approval_id}"),
        "after approval, run the reviewed maintenance command explicitly".to_string(),
    ]
}

fn build_maintenance_report(
    profile: &str,
    args: &MaintenanceReportArgs,
) -> Result<OffdeskMaintenanceReport> {
    let profile_dir = read_only_profile_dir(profile)?;
    let generated_at = Utc::now();

    let tasks = OffdeskTaskStore::new(&profile_dir)
        .load()?
        .into_iter()
        .map(|task| task.operator_view())
        .collect::<Vec<_>>();
    let task_summary = summarize_maintenance_tasks(&tasks);

    let background_runs = BackgroundRunStore::new(&profile_dir)
        .load()?
        .into_iter()
        .map(|probe| background_probe_status(probe, generated_at))
        .collect::<Vec<_>>();
    let background_summary = summarize_maintenance_background(&background_runs);

    let approvals = ApprovalLedger::new(&profile_dir).load()?;
    let approval_summary = summarize_maintenance_approvals(&approvals);

    let resume_states = TaskResumeStore::new(&profile_dir).load()?;
    let resume_summary = summarize_maintenance_resume(&resume_states);

    let provider_capacity_states = ProviderCapacityStore::new(&profile_dir).load()?;
    let provider_capacity_summary =
        summarize_maintenance_provider_capacity(&provider_capacity_states);

    let wiki_store = AdaptiveWikiStore::new(&profile_dir);
    let all_wiki_query = AdaptiveWikiQuery {
        session_id: None,
        project_key: None,
        artifact_kind: None,
        agent_mode: None,
        agent_mode_filter: AdaptiveWikiAgentModeFilter::AllWhenUnspecified,
    };
    let wiki_projection = wiki_store.human_projection(&all_wiki_query)?;
    let wiki_review_near_expiry_hours = args.wiki_review_near_expiry_hours.max(1);
    let adaptive_wiki_review_after_attention_summary = build_review_after_report(
        wiki_projection.entries,
        all_wiki_query,
        wiki_review_near_expiry_hours,
        Duration::hours(wiki_review_near_expiry_hours),
        generated_at,
    )
    .summary;

    let runtime_policy_acknowledgements = wiki_store.load_runtime_policy_acknowledgements()?;
    let wiki_runtime_ack_near_expiry_hours = args.wiki_runtime_ack_near_expiry_hours.max(1);
    let adaptive_wiki_runtime_policy_ack_attention_summary = build_runtime_policy_ack_report(
        runtime_policy_acknowledgements,
        None,
        None,
        None,
        wiki_runtime_ack_near_expiry_hours,
        Duration::hours(wiki_runtime_ack_near_expiry_hours),
        generated_at,
    )
    .summary;

    let recommended_actions = maintenance_recommended_actions(
        &task_summary,
        &background_summary,
        &approval_summary,
        &resume_summary,
        &provider_capacity_summary,
        &adaptive_wiki_runtime_policy_ack_attention_summary,
        &adaptive_wiki_review_after_attention_summary,
    );

    let profile_name = if profile.is_empty() {
        DEFAULT_PROFILE
    } else {
        profile
    };
    Ok(OffdeskMaintenanceReport {
        generated_at,
        profile: operator_safe_report(profile_name).text,
        profile_dir: operator_safe_report(profile_dir.to_string_lossy().as_ref()).text,
        read_only: true,
        tasks: task_summary,
        background_runs: background_summary,
        approvals: approval_summary,
        resume_states: resume_summary,
        provider_capacity: provider_capacity_summary,
        adaptive_wiki_runtime_policy_ack_attention_summary,
        adaptive_wiki_review_after_attention_summary,
        recommended_actions,
    })
}

fn summarize_maintenance_tasks(tasks: &[OffdeskTaskView]) -> MaintenanceTaskSummary {
    let mut summary = MaintenanceTaskSummary {
        total: tasks.len(),
        ..MaintenanceTaskSummary::default()
    };
    for task in tasks {
        increment_count(&mut summary.by_status, enum_label(task.status));
        record_agent_mode(task.agent_mode, &mut summary.by_agent_mode);
        if task.agent_mode.is_none() {
            summary.missing_agent_mode += 1;
        }
        record_mode_assessment(&task.mode_assessment, &mut summary.mode);
    }
    summary
}

fn summarize_maintenance_background(
    statuses: &[BackgroundProbeStatus],
) -> MaintenanceBackgroundSummary {
    let mut summary = MaintenanceBackgroundSummary {
        total: statuses.len(),
        ..MaintenanceBackgroundSummary::default()
    };
    for status in statuses {
        increment_count(&mut summary.by_phase, enum_label(status.probe.phase));
        record_agent_mode(status.probe.agent_mode, &mut summary.by_agent_mode);
        if status.probe.agent_mode.is_none() {
            summary.missing_agent_mode += 1;
        }
        record_mode_assessment(&status.mode_assessment, &mut summary.mode);
    }
    summary
}

fn summarize_maintenance_approvals(
    approvals: &[PendingActionApproval],
) -> MaintenanceApprovalSummary {
    let mut summary = MaintenanceApprovalSummary {
        total: approvals.len(),
        ..MaintenanceApprovalSummary::default()
    };
    for approval in approvals {
        let status = enum_label(approval.status);
        if status == "pending" {
            summary.pending += 1;
        }
        increment_count(&mut summary.by_status, status);
    }
    summary
}

fn summarize_maintenance_resume(states: &[TaskResumeState]) -> MaintenanceResumeSummary {
    let mut summary = MaintenanceResumeSummary {
        total: states.len(),
        ..MaintenanceResumeSummary::default()
    };
    for state in states {
        increment_count(&mut summary.by_status, enum_label(state.status));
    }
    summary
}

fn summarize_maintenance_provider_capacity(
    states: &[ProviderCapacityState],
) -> MaintenanceProviderCapacitySummary {
    let mut summary = MaintenanceProviderCapacitySummary {
        total: states.len(),
        ..MaintenanceProviderCapacitySummary::default()
    };
    for state in states {
        let status = enum_label(state.status);
        if status != "available" {
            summary.attention += 1;
        }
        increment_count(&mut summary.by_status, status);
    }
    summary
}

fn record_agent_mode(
    agent_mode: Option<AdaptiveWikiAgentMode>,
    counts: &mut BTreeMap<String, usize>,
) {
    let label = agent_mode
        .map(adaptive_wiki_agent_mode_cli_value)
        .unwrap_or("missing");
    increment_count(counts, label.to_string());
}

fn record_mode_assessment(
    assessment: &OffdeskModeAssessment,
    summary: &mut MaintenanceModeSummary,
) {
    increment_count(
        &mut summary.by_verdict,
        assessment.mode_verdict.label().to_string(),
    );
    increment_count(
        &mut summary.by_risk,
        assessment.mode_risk.label().to_string(),
    );
    if assessment.review_stage_required {
        summary.review_stage_required += 1;
    }
}

fn increment_count(counts: &mut BTreeMap<String, usize>, key: String) {
    *counts.entry(key).or_insert(0) += 1;
}

fn enum_label(value: impl Serialize) -> String {
    match serde_json::to_value(value) {
        Ok(Value::String(value)) => value,
        Ok(value) => value.to_string(),
        Err(_) => "unknown".to_string(),
    }
}

fn maintenance_risk_count(summary: &MaintenanceModeSummary, risk: &str) -> usize {
    summary.by_risk.get(risk).copied().unwrap_or(0)
}

fn maintenance_recommended_actions(
    tasks: &MaintenanceTaskSummary,
    background_runs: &MaintenanceBackgroundSummary,
    approvals: &MaintenanceApprovalSummary,
    resume_states: &MaintenanceResumeSummary,
    provider_capacity: &MaintenanceProviderCapacitySummary,
    runtime_ack_summary: &WikiRuntimePolicyAckReportSummary,
    review_after_summary: &WikiReviewAfterReportSummary,
) -> Vec<MaintenanceRecommendedAction> {
    let mut actions = Vec::new();
    if approvals.pending > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "pending_approval",
            detail: format!(
                "{} pending approvals need an operator decision.",
                approvals.pending
            ),
            command: "forager offdesk pending",
        });
    }
    let review_required = maintenance_risk_count(&tasks.mode, "operator_review_required")
        + maintenance_risk_count(&background_runs.mode, "operator_review_required");
    if review_required > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "operator_review",
            detail: format!("{review_required} completed mode-scoped items need separate review."),
            command: "forager offdesk tasks",
        });
    }
    let missing_result = maintenance_risk_count(&tasks.mode, "missing_result_artifact")
        + maintenance_risk_count(&background_runs.mode, "missing_result_artifact");
    if missing_result > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "missing_result_artifact",
            detail: format!("{missing_result} completed items have no result artifact to inspect."),
            command: "forager offdesk tasks --json",
        });
    }
    let runtime_recovery = maintenance_risk_count(&tasks.mode, "runtime_recovery_required")
        + maintenance_risk_count(&background_runs.mode, "runtime_recovery_required");
    if runtime_recovery > 0 || resume_states.total > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "runtime_recovery",
            detail: format!(
                "{runtime_recovery} mode assessments need recovery; {} resume records exist.",
                resume_states.total
            ),
            command: "forager offdesk resume",
        });
    }
    let missing_mode = tasks.missing_agent_mode + background_runs.missing_agent_mode;
    if missing_mode > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "missing_agent_mode",
            detail: format!("{missing_mode} durable records are missing agent_mode scope."),
            command: "forager offdesk debug-bundle",
        });
    }
    if provider_capacity.attention > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "provider_capacity",
            detail: format!(
                "{} provider capacity records are cooling down or blocked.",
                provider_capacity.attention
            ),
            command: "forager offdesk provider-capacity",
        });
    }
    let runtime_ack_attention = runtime_ack_summary.expired
        + runtime_ack_summary.near_expiry
        + runtime_ack_summary.suggested_actions;
    if runtime_ack_attention > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "wiki_runtime_ack",
            detail: format!(
                "{runtime_ack_attention} runtime policy acknowledgement signals need attention."
            ),
            command: "forager offdesk wiki runtime-policy-ack-report",
        });
    }
    if review_after_summary.attention > 0 {
        actions.push(MaintenanceRecommendedAction {
            kind: "wiki_review_after",
            detail: format!(
                "{} adaptive wiki entries are expired or near review_after.",
                review_after_summary.attention
            ),
            command: "forager offdesk wiki review-after-report",
        });
    }
    actions
}

fn write_debug_bundle_export(
    profile: &str,
    bundle: &OffdeskDebugBundle,
    output: Option<&PathBuf>,
) -> Result<DebugBundleExport> {
    let bytes = serde_json::to_vec_pretty(bundle)?;

    if let Some(path) = output {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent).with_context(|| {
                format!("create debug bundle export directory {}", parent.display())
            })?;
        }
        let bytes_written = write_new_file(path, &bytes)
            .with_context(|| format!("write debug bundle export {}", path.display()))?;
        return Ok(DebugBundleExport {
            path: path.clone(),
            bytes_written,
        });
    }

    let export_dir = read_only_profile_dir(profile)?.join("debug_bundles");
    fs::create_dir_all(&export_dir).with_context(|| {
        format!(
            "create debug bundle export directory {}",
            export_dir.display()
        )
    })?;
    let timestamp = bundle.generated_at.format("%Y%m%dT%H%M%SZ");
    for attempt in 0..1000 {
        let filename = if attempt == 0 {
            format!("offdesk_debug_bundle_{timestamp}.json")
        } else {
            format!("offdesk_debug_bundle_{timestamp}_{attempt:03}.json")
        };
        let path = export_dir.join(filename);
        match write_new_file(&path, &bytes) {
            Ok(bytes_written) => {
                return Ok(DebugBundleExport {
                    path,
                    bytes_written,
                })
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => {
                return Err(error)
                    .with_context(|| format!("write debug bundle export {}", path.display()));
            }
        }
    }

    bail!(
        "could not allocate debug bundle export path in {}",
        export_dir.display()
    )
}

fn write_new_file(path: &Path, bytes: &[u8]) -> io::Result<usize> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    Ok(bytes.len())
}

fn operator_safe_json_value(value: Value) -> Value {
    match value {
        Value::String(text) => Value::String(crate::offdesk::operator_safe_text(&text)),
        Value::Array(values) => {
            Value::Array(values.into_iter().map(operator_safe_json_value).collect())
        }
        Value::Object(map) => Value::Object(
            map.into_iter()
                .map(|(key, value)| (key, operator_safe_json_value(value)))
                .collect(),
        ),
        other => other,
    }
}

fn approval_ledger(profile: &str) -> Result<ApprovalLedger> {
    Ok(ApprovalLedger::new(get_profile_dir(profile)?))
}

fn resume_store(profile: &str) -> Result<TaskResumeStore> {
    Ok(TaskResumeStore::new(get_profile_dir(profile)?))
}

fn background_store(profile: &str) -> Result<BackgroundRunStore> {
    Ok(BackgroundRunStore::new(get_profile_dir(profile)?))
}

fn task_store(profile: &str) -> Result<OffdeskTaskStore> {
    Ok(OffdeskTaskStore::new(get_profile_dir(profile)?))
}

fn wiki_store(profile: &str) -> Result<AdaptiveWikiStore> {
    Ok(AdaptiveWikiStore::new(read_only_profile_dir(profile)?))
}

fn writable_wiki_store(profile: &str) -> Result<AdaptiveWikiStore> {
    Ok(AdaptiveWikiStore::new(get_profile_dir(profile)?))
}

fn mutation_snapshot_store(profile: &str) -> Result<MutationSnapshotStore> {
    Ok(MutationSnapshotStore::new(get_profile_dir(profile)?))
}

fn wiki_query(
    session_id: &Option<String>,
    project_key: &Option<String>,
    artifact_kind: &Option<String>,
    agent_mode: Option<AdaptiveWikiAgentMode>,
) -> AdaptiveWikiQuery {
    AdaptiveWikiQuery {
        session_id: clean_optional_string(session_id),
        project_key: clean_optional_string(project_key),
        artifact_kind: clean_optional_string(artifact_kind),
        agent_mode,
        agent_mode_filter: AdaptiveWikiAgentModeFilter::AllWhenUnspecified,
    }
}

fn runtime_wiki_query(
    session_id: &Option<String>,
    project_key: &Option<String>,
    artifact_kind: &Option<String>,
    agent_mode: Option<AdaptiveWikiAgentMode>,
) -> AdaptiveWikiQuery {
    let mut query = wiki_query(session_id, project_key, artifact_kind, agent_mode);
    query.agent_mode_filter = AdaptiveWikiAgentModeFilter::SharedWhenUnspecified;
    query
}

fn wiki_episode_out_of_scope_query(args: &WikiEpisodeArgs) -> AdaptiveWikiQuery {
    let mut query = wiki_query(
        &args.out_session_id,
        &args.out_project_key,
        &args.out_artifact_kind,
        args.out_agent_mode,
    );
    if query.session_id.is_none() {
        query.session_id =
            clean_optional_string(&args.session_id).map(|value| format!("out-of-scope-{value}"));
    }
    if query.project_key.is_none() {
        query.project_key =
            clean_optional_string(&args.project_key).map(|value| format!("out-of-scope-{value}"));
    }
    if query.artifact_kind.is_none() {
        query.artifact_kind =
            clean_optional_string(&args.artifact_kind).map(|value| format!("out-of-scope-{value}"));
    }
    if query.agent_mode.is_none() {
        query.agent_mode = args.agent_mode.map(out_of_scope_agent_mode);
    }
    if query.session_id.is_none()
        && query.project_key.is_none()
        && query.artifact_kind.is_none()
        && query.agent_mode.is_none()
    {
        query.project_key = Some("episode-out-of-scope".to_string());
    }
    query
}

fn out_of_scope_agent_mode(mode: AdaptiveWikiAgentMode) -> AdaptiveWikiAgentMode {
    match mode {
        AdaptiveWikiAgentMode::Planning => AdaptiveWikiAgentMode::Development,
        AdaptiveWikiAgentMode::Development => AdaptiveWikiAgentMode::Analysis,
        AdaptiveWikiAgentMode::Analysis => AdaptiveWikiAgentMode::Writing,
        AdaptiveWikiAgentMode::Writing => AdaptiveWikiAgentMode::Critique,
        AdaptiveWikiAgentMode::Critique => AdaptiveWikiAgentMode::Review,
        AdaptiveWikiAgentMode::Review => AdaptiveWikiAgentMode::Maintenance,
        AdaptiveWikiAgentMode::Maintenance => AdaptiveWikiAgentMode::Planning,
    }
}

fn clean_optional_string(value: &Option<String>) -> Option<String> {
    value
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn maintenance_default_task_id(kind: MaintenanceActionKind, target_id: Option<&str>) -> String {
    let mut task_id = format!("maintenance-{}", kind.cli_value().replace('_', "-"));
    if let Some(target_id) = target_id {
        task_id.push('-');
        task_id.push_str(&sanitize_id_fragment(target_id));
    }
    task_id
}

fn sanitize_id_fragment(value: &str) -> String {
    let mut sanitized = value
        .chars()
        .filter_map(|ch| {
            if ch.is_ascii_alphanumeric() {
                Some(ch.to_ascii_lowercase())
            } else if ch == '-' || ch == '_' {
                Some(ch)
            } else if ch.is_whitespace() || ch == '/' || ch == '.' || ch == ':' {
                Some('-')
            } else {
                None
            }
        })
        .collect::<String>();
    while sanitized.contains("--") {
        sanitized = sanitized.replace("--", "-");
    }
    sanitized = sanitized.trim_matches('-').to_string();
    if sanitized.is_empty() {
        "target".to_string()
    } else {
        sanitized.chars().take(64).collect()
    }
}

fn first_non_empty<'a>(values: &[&'a str]) -> Option<&'a str> {
    values
        .iter()
        .map(|value| value.trim())
        .find(|value| !value.is_empty())
}

fn require_non_empty_arg<'a>(name: &str, value: &'a str) -> Result<&'a str> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        bail!("{name} must not be empty");
    }
    Ok(trimmed)
}

fn find_wiki_candidate(
    store: &AdaptiveWikiStore,
    candidate_id: &str,
) -> Result<Option<AdaptiveWikiCandidate>> {
    Ok(store
        .load_candidates()?
        .candidates
        .into_iter()
        .find(|candidate| candidate.id == candidate_id))
}

fn find_wiki_entry(store: &AdaptiveWikiStore, entry_id: &str) -> Result<Option<AdaptiveWikiEntry>> {
    Ok(store
        .load_entries()?
        .entries
        .into_iter()
        .find(|entry| entry.id == entry_id))
}

fn human_entry(entry: AdaptiveWikiEntry) -> AdaptiveWikiHumanEntry {
    crate::offdesk::build_human_projection(&[entry], &[], &AdaptiveWikiQuery::default())
        .entries
        .into_iter()
        .next()
        .expect("one human entry projection")
}

fn human_candidate(candidate: AdaptiveWikiCandidate) -> AdaptiveWikiHumanCandidate {
    crate::offdesk::build_human_projection(&[], &[candidate], &AdaptiveWikiQuery::default())
        .candidates
        .into_iter()
        .next()
        .expect("one human candidate projection")
}

fn wiki_entry_scope(entry: &AdaptiveWikiEntry) -> AdaptiveWikiScopeSuggestion {
    AdaptiveWikiScopeSuggestion {
        scope: entry.scope,
        scope_ref: crate::offdesk::operator_safe_text(&entry.scope_ref),
    }
}

fn wiki_candidate_scope(candidate: &AdaptiveWikiCandidate) -> AdaptiveWikiScopeSuggestion {
    AdaptiveWikiScopeSuggestion {
        scope: candidate.scope,
        scope_ref: crate::offdesk::operator_safe_text(&candidate.scope_ref),
    }
}

struct WikiAuditRecordInput<'a> {
    action: AdaptiveWikiAuditAction,
    subject_id: &'a str,
    candidate_id: Option<&'a str>,
    entry_id: Option<&'a str>,
    actor: &'a str,
    reason: &'a str,
    evidence_ref: Option<&'a str>,
    before_scope: Option<AdaptiveWikiScopeSuggestion>,
    after_scope: Option<AdaptiveWikiScopeSuggestion>,
    activation_mode: Option<AdaptiveWikiActivationMode>,
    candidate_snapshot: Option<AdaptiveWikiHumanCandidate>,
    entry_snapshot: Option<AdaptiveWikiHumanEntry>,
    now: DateTime<Utc>,
}

fn wiki_audit_record(input: WikiAuditRecordInput<'_>) -> AdaptiveWikiAuditRecord {
    AdaptiveWikiAuditRecord {
        id: format!("wiki_audit_{}", uuid::Uuid::new_v4()),
        action: input.action,
        subject_id: crate::offdesk::operator_safe_text(input.subject_id),
        candidate_id: input.candidate_id.map(crate::offdesk::operator_safe_text),
        entry_id: input.entry_id.map(crate::offdesk::operator_safe_text),
        actor: crate::offdesk::operator_safe_text(input.actor.trim()),
        reason: crate::offdesk::operator_safe_text(input.reason.trim()),
        evidence_ref: input
            .evidence_ref
            .map(|value| crate::offdesk::operator_safe_text(value.trim()))
            .filter(|value| !value.is_empty()),
        before_scope: input.before_scope,
        after_scope: input.after_scope,
        activation_mode: input.activation_mode,
        candidate_snapshot: input.candidate_snapshot,
        entry_snapshot: input.entry_snapshot,
        created_at: input.now,
    }
}

fn default_wiki_scope_ref(scope: AdaptiveWikiScope) -> String {
    match scope {
        AdaptiveWikiScope::UserGlobal => "*".to_string(),
        AdaptiveWikiScope::Session => "-".to_string(),
        AdaptiveWikiScope::ArtifactKind | AdaptiveWikiScope::Project => "*".to_string(),
    }
}

fn read_only_profile_dir(profile: &str) -> Result<PathBuf> {
    let profile_name = crate::session::normalize_profile_name(profile)?;
    Ok(resolved_app_dir_path()?.join("profiles").join(profile_name))
}

impl DebugBundleRedactor {
    fn text(&mut self, input: &str) -> String {
        self.summary.text_fields_checked += 1;
        let outcome = operator_safe_report(input);
        if outcome.changed {
            self.summary.changed_text_fields += 1;
            self.summary.runner_context_removed += outcome.runner_context_removed;
            self.summary.secrets_redacted += outcome.secrets_redacted;
        }
        outcome.text
    }

    fn value(&mut self, value: Value) -> Value {
        match value {
            Value::String(text) => Value::String(self.text(&text)),
            Value::Array(values) => {
                Value::Array(values.into_iter().map(|value| self.value(value)).collect())
            }
            Value::Object(map) => Value::Object(
                map.into_iter()
                    .map(|(key, value)| (key, self.value(value)))
                    .collect(),
            ),
            other => other,
        }
    }
}

fn load_execution_brief(path: Option<&PathBuf>) -> Result<Option<ExecutionBrief>> {
    let Some(path) = path else {
        return Ok(None);
    };
    let content = std::fs::read_to_string(path)?;
    Ok(Some(serde_json::from_str::<ExecutionBrief>(&content)?))
}

fn parse_rfc3339(value: Option<&str>) -> Result<Option<DateTime<Utc>>> {
    let Some(value) = value else {
        return Ok(None);
    };
    Ok(Some(
        DateTime::parse_from_rfc3339(value)?.with_timezone(&Utc),
    ))
}

fn parse_rfc3339_datetime(value: &str) -> std::result::Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .map_err(|err| format!("timestamp must be RFC3339: {err}"))
}

fn parse_background_runner_kind(value: &str) -> std::result::Result<BackgroundRunnerKind, String> {
    value.parse()
}

fn parse_maintenance_action_kind(
    value: &str,
) -> std::result::Result<MaintenanceActionKind, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "runtime_recovery" | "runtime-recovery" | "recovery" => {
            Ok(MaintenanceActionKind::RuntimeRecovery)
        }
        "wiki_runtime_ack" | "wiki-runtime-ack" | "runtime_ack" | "runtime-ack" => {
            Ok(MaintenanceActionKind::WikiRuntimeAck)
        }
        "wiki_review_after" | "wiki-review-after" | "review_after" | "review-after" => {
            Ok(MaintenanceActionKind::WikiReviewAfter)
        }
        "wiki_mutation" | "wiki-mutation" | "wiki" => Ok(MaintenanceActionKind::WikiMutation),
        "provider_capacity" | "provider-capacity" | "capacity" => {
            Ok(MaintenanceActionKind::ProviderCapacity)
        }
        "artifact_cleanup" | "artifact-cleanup" | "cleanup" => {
            Ok(MaintenanceActionKind::ArtifactCleanup)
        }
        "service_restart" | "service-restart" | "restart" => {
            Ok(MaintenanceActionKind::ServiceRestart)
        }
        "system_change" | "system-change" | "system" => Ok(MaintenanceActionKind::SystemChange),
        _ => Err(
            "maintenance kind must be one of runtime_recovery, wiki_runtime_ack, wiki_review_after, wiki_mutation, provider_capacity, artifact_cleanup, service_restart, system_change"
                .to_string(),
        ),
    }
}

fn parse_risk_level(value: &str) -> std::result::Result<RiskLevel, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "safe" => Ok(RiskLevel::Safe),
        "runtime_mutation" | "runtime-mutation" | "runtime" => Ok(RiskLevel::RuntimeMutation),
        "canonical_mutation" | "canonical-mutation" | "canonical" => {
            Ok(RiskLevel::CanonicalMutation)
        }
        "destructive" | "delete" | "cleanup" => Ok(RiskLevel::Destructive),
        "external_side_effect" | "external-side-effect" | "external" => {
            Ok(RiskLevel::ExternalSideEffect)
        }
        _ => Err(
            "risk must be one of safe, runtime_mutation, canonical_mutation, destructive, external_side_effect"
                .to_string(),
        ),
    }
}

fn parse_artifact_ref(value: &str) -> std::result::Result<CapabilityArtifactRef, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("artifact reference must not be empty".to_string());
    }
    if let Some((artifact_id, path)) = trimmed.split_once('=') {
        let artifact_id = artifact_id.trim();
        let path = path.trim();
        if artifact_id.is_empty() || path.is_empty() {
            return Err("artifact reference must use ARTIFACT_ID=PATH".to_string());
        }
        Ok(CapabilityArtifactRef::new(
            artifact_id.to_string(),
            Some(path.to_string()),
        ))
    } else {
        Ok(CapabilityArtifactRef::new(
            trimmed.to_string(),
            None::<String>,
        ))
    }
}

fn parse_adaptive_wiki_scope(value: &str) -> std::result::Result<AdaptiveWikiScope, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "session" => Ok(AdaptiveWikiScope::Session),
        "artifact_kind" | "artifact-kind" | "artifact" => Ok(AdaptiveWikiScope::ArtifactKind),
        "project" => Ok(AdaptiveWikiScope::Project),
        "user_global" | "user-global" | "global" => Ok(AdaptiveWikiScope::UserGlobal),
        _ => Err("scope must be one of session, artifact_kind, project, user_global".to_string()),
    }
}

fn parse_adaptive_wiki_runtime_policy_ack_scope_mode(
    value: &str,
) -> std::result::Result<AdaptiveWikiRuntimePolicyAckScopeMode, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "exact_query" | "exact-query" | "exact" => {
            Ok(AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery)
        }
        "project_artifact" | "project-artifact" => {
            Ok(AdaptiveWikiRuntimePolicyAckScopeMode::ProjectArtifact)
        }
        _ => Err("scope mode must be one of exact_query, project_artifact".to_string()),
    }
}

fn parse_adaptive_wiki_activation_mode(
    value: &str,
) -> std::result::Result<AdaptiveWikiActivationMode, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "context_only" | "context-only" => Ok(AdaptiveWikiActivationMode::ContextOnly),
        "confirm" => Ok(AdaptiveWikiActivationMode::Confirm),
        "auto_apply" | "auto-apply" => Ok(AdaptiveWikiActivationMode::AutoApply),
        _ => Err("activation mode must be one of context_only, confirm, auto_apply".to_string()),
    }
}

fn parse_adaptive_wiki_agent_mode(
    value: &str,
) -> std::result::Result<AdaptiveWikiAgentMode, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "planning" | "plan" | "planner" => Ok(AdaptiveWikiAgentMode::Planning),
        "code_development" | "code-development" | "code" | "coding" | "development" => {
            Ok(AdaptiveWikiAgentMode::Development)
        }
        "analysis" | "analyze" | "analyse" | "diagnostics" | "diagnostic" => {
            Ok(AdaptiveWikiAgentMode::Analysis)
        }
        "research_writing" | "research-writing" | "research" | "writing" | "editing" => {
            Ok(AdaptiveWikiAgentMode::Writing)
        }
        "critique" | "critic" => Ok(AdaptiveWikiAgentMode::Critique),
        "review" | "reviewer" => Ok(AdaptiveWikiAgentMode::Review),
        "maintenance" | "maintain" | "maintainer" | "ops" | "health" => {
            Ok(AdaptiveWikiAgentMode::Maintenance)
        }
        _ => Err(
            "agent mode must be one of planning, development, analysis, writing, critique, review, maintenance".to_string(),
        ),
    }
}

fn parse_adaptive_wiki_review_action(
    value: &str,
) -> std::result::Result<AdaptiveWikiReviewProposalAction, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "promote" => Ok(AdaptiveWikiReviewProposalAction::Promote),
        "reject" => Ok(AdaptiveWikiReviewProposalAction::Reject),
        "rescope" => Ok(AdaptiveWikiReviewProposalAction::Rescope),
        "deprecate" => Ok(AdaptiveWikiReviewProposalAction::Deprecate),
        "add_counterexample" | "add-counterexample" => {
            Ok(AdaptiveWikiReviewProposalAction::AddCounterexample)
        }
        "renew_review" | "renew-review" => Ok(AdaptiveWikiReviewProposalAction::RenewReview),
        "split" => Ok(AdaptiveWikiReviewProposalAction::Split),
        "merge" => Ok(AdaptiveWikiReviewProposalAction::Merge),
        _ => Err("proposal action must be one of promote, reject, rescope, deprecate, add_counterexample, renew_review, split, merge".to_string()),
    }
}

fn parse_wiki_proposal_handoff_mutation(
    value: &str,
) -> std::result::Result<WikiProposalHandoffMutation, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "rescope" => Ok(WikiProposalHandoffMutation::Rescope),
        "deprecate" => Ok(WikiProposalHandoffMutation::Deprecate),
        "add_counterexample" | "add-counterexample" => {
            Ok(WikiProposalHandoffMutation::AddCounterexample)
        }
        "deprecate_duplicate" | "deprecate-duplicate" => {
            Ok(WikiProposalHandoffMutation::DeprecateDuplicate)
        }
        "split" => Ok(WikiProposalHandoffMutation::Split),
        _ => Err(
            "mutation must be one of rescope, deprecate, add_counterexample, deprecate_duplicate, split"
                .to_string(),
        ),
    }
}

fn parse_adaptive_wiki_proposal_decision(
    value: &str,
) -> std::result::Result<AdaptiveWikiReviewProposalDecision, String> {
    match value.trim().to_ascii_lowercase().as_str() {
        "accepted" | "accept" => Ok(AdaptiveWikiReviewProposalDecision::Accepted),
        "rejected" | "reject" => Ok(AdaptiveWikiReviewProposalDecision::Rejected),
        "superseded" | "supersede" => Ok(AdaptiveWikiReviewProposalDecision::Superseded),
        _ => Err("proposal decision must be one of accepted, rejected, superseded".to_string()),
    }
}

fn empty_dash(value: &str) -> &str {
    if value.trim().is_empty() {
        "-"
    } else {
        value
    }
}

fn shell_quote_arg(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

fn print_gate_outcome(outcome: &crate::offdesk::SchedulerGateOutcome) {
    match outcome.status {
        SchedulerGateStatus::Proceed => {
            println!(
                "Proceed: {} ({}) via {:?}",
                outcome.capability_id, outcome.risk_level, outcome.approval_mode
            );
        }
        SchedulerGateStatus::PendingApproval => {
            println!(
                "Pending approval: {} ({})",
                outcome.capability_id, outcome.risk_level
            );
            if let Some(approval) = &outcome.approval {
                println!("  approval_id: {}", approval.approval_id);
                println!("  action_id:   {}", approval.action_id());
                if !approval.preview.trim().is_empty() {
                    println!("  preview:     {}", approval.preview);
                }
                if !approval.reason.trim().is_empty() {
                    println!("  reason:      {}", approval.reason);
                }
            }
        }
        SchedulerGateStatus::Denied => {
            println!("Denied: {} - {}", outcome.capability_id, outcome.reason);
        }
        SchedulerGateStatus::Blocked => {
            println!("Blocked: {} - {}", outcome.capability_id, outcome.reason);
            if let Some(capacity) = outcome.provider_capacity.as_ref() {
                println!("  provider:  {}", capacity.provider_id);
                println!("  model:     {}", capacity.model.as_deref().unwrap_or("-"));
                println!("  scope:     {}", capacity.matched_scope);
                if let Some(retry_at) = outcome.retry_at {
                    println!("  retry_at:  {retry_at}");
                }
            }
            if let Some(fallback) = outcome.provider_fallback.as_ref() {
                let recommended = fallback
                    .candidates
                    .iter()
                    .filter(|candidate| candidate.recommended)
                    .count();
                println!(
                    "  fallback:  {} candidates, {} recommended",
                    fallback.candidates.len(),
                    recommended
                );
            }
        }
    }
    if !outcome.adaptive_wiki.is_empty() {
        println!("  adaptive_wiki: {} entries", outcome.adaptive_wiki.len());
        for entry in outcome.adaptive_wiki.iter().take(3) {
            println!(
                "    - {} {:?} {:?} agent_modes={}: {}",
                entry.id,
                entry.scope,
                entry.activation_mode,
                adaptive_wiki_agent_modes_label(&entry.agent_modes),
                entry.instruction
            );
        }
    }
    if !outcome.adaptive_wiki_runtime.is_empty() {
        println!(
            "  adaptive_wiki_runtime: {} entries policy review_expired={:?}",
            outcome.adaptive_wiki_runtime.len(),
            outcome.adaptive_wiki_runtime_policy.review_expired
        );
    }
    if let Some(decision) = outcome.adaptive_wiki_runtime_decision.as_ref() {
        println!(
            "  adaptive_wiki_runtime_decision: {:?} ({})",
            decision.status, decision.reason
        );
    }
}

fn print_wiki_entries(entries: &[AdaptiveWikiHumanEntry]) {
    println!(
        "{:<44} {:<12} {:<14} {:<16} {:<18} CLAIM",
        "ID", "STATUS", "SCOPE", "ACTIVATION", "AGENT_MODES"
    );
    for entry in entries {
        println!(
            "{:<44} {:<12} {:<14} {:<16} {:<18} {}",
            entry.id,
            format!("{:?}", entry.status).to_lowercase(),
            wiki_scope_label(entry.scope, &entry.scope_ref),
            format!("{:?}", entry.activation_mode).to_lowercase(),
            adaptive_wiki_agent_modes_label(&entry.agent_modes),
            entry.claim
        );
        if !entry.human_summary.trim().is_empty() {
            println!("  summary: {}", entry.human_summary);
        }
        if !entry.evidence_refs.is_empty() {
            println!("  evidence: {}", entry.evidence_refs.join(", "));
        }
        if !entry.support_refs.is_empty() {
            println!("  support: {}", entry.support_refs.join(", "));
        }
        if !entry.capability_ids.is_empty() {
            println!("  capabilities: {}", entry.capability_ids.join(", "));
        }
        if !entry.required_artifact_kinds.is_empty() {
            println!("  artifacts: {}", entry.required_artifact_kinds.join(", "));
        }
    }
}

fn print_wiki_candidates(candidates: &[AdaptiveWikiHumanCandidate]) {
    println!(
        "{:<44} {:<14} {:<18} {:<18} {:<8} CLAIM",
        "ID", "SCOPE", "SIGNAL", "AGENT_MODES", "HITS"
    );
    for candidate in candidates {
        println!(
            "{:<44} {:<14} {:<18} {:<18} {:<8} {}",
            candidate.id,
            wiki_scope_label(candidate.scope, &candidate.scope_ref),
            format!("{:?}", candidate.signal_kind).to_lowercase(),
            adaptive_wiki_agent_modes_label(&candidate.agent_modes),
            candidate.occurrence_count,
            candidate.claim
        );
        if !candidate.review_reason.trim().is_empty() {
            println!("  review: {}", candidate.review_reason);
        }
        if !candidate.source_refs.is_empty() {
            println!("  sources: {}", candidate.source_refs.join(", "));
        } else if !candidate.evidence_refs.is_empty() {
            println!("  evidence: {}", candidate.evidence_refs.join(", "));
        }
    }
}

fn print_wiki_show(result: &WikiShowResult) {
    match result {
        WikiShowResult::Entry { entry } => {
            println!("Adaptive wiki entry {}", entry.id);
            println!("  status:     {:?}", entry.status);
            println!("  kind:       {:?}", entry.kind);
            println!(
                "  scope:      {}",
                wiki_scope_label(entry.scope, &entry.scope_ref)
            );
            println!("  activation: {:?}", entry.activation_mode);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&entry.agent_modes)
            );
            println!("  confidence: {:?}", entry.confidence);
            println!("  claim:      {}", entry.claim);
            if !entry.human_summary.trim().is_empty() {
                println!("  summary:    {}", entry.human_summary);
            }
            if !entry.evidence_refs.is_empty() {
                println!("  evidence:   {}", entry.evidence_refs.join(", "));
            }
            if !entry.support_refs.is_empty() {
                println!("  support:    {}", entry.support_refs.join(", "));
            }
            if !entry.capability_ids.is_empty() {
                println!("  capabilities: {}", entry.capability_ids.join(", "));
            }
            if !entry.required_artifact_kinds.is_empty() {
                println!("  artifacts:  {}", entry.required_artifact_kinds.join(", "));
            }
        }
        WikiShowResult::Candidate { candidate } => {
            println!("Adaptive wiki candidate {}", candidate.id);
            println!("  kind:       {:?}", candidate.kind);
            println!(
                "  scope:      {}",
                wiki_scope_label(candidate.scope, &candidate.scope_ref)
            );
            println!("  signal:     {:?}", candidate.signal_kind);
            println!("  origin:     {:?}", candidate.origin);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&candidate.agent_modes)
            );
            println!("  hits:       {}", candidate.occurrence_count);
            println!("  confidence: {:?}", candidate.confidence);
            println!("  claim:      {}", candidate.claim);
            if !candidate.human_summary.trim().is_empty() {
                println!("  summary:    {}", candidate.human_summary);
            }
            if !candidate.review_reason.trim().is_empty() {
                println!("  review:     {}", candidate.review_reason);
            }
            if !candidate.source_refs.is_empty() {
                println!("  sources:    {}", candidate.source_refs.join(", "));
            }
        }
    }
}

fn print_wiki_mutation(result: &WikiMutationResult, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(result)?);
        return Ok(());
    }

    match result {
        WikiMutationResult::Promote { entry, audit } => {
            println!("Promoted adaptive wiki candidate to entry {}", entry.id);
            println!(
                "  scope: {}",
                wiki_scope_label(entry.scope, &entry.scope_ref)
            );
            println!("  activation:  {:?}", entry.activation_mode);
            println!(
                "  agent_modes: {}",
                adaptive_wiki_agent_modes_label(&entry.agent_modes)
            );
            println!("  audit: {}", audit.id);
        }
        WikiMutationResult::Reject { candidate, audit } => {
            println!("Rejected adaptive wiki candidate {}", candidate.id);
            println!("  reason: {}", audit.reason);
            println!("  audit:  {}", audit.id);
        }
        WikiMutationResult::Rescope { entry, audit } => {
            println!("Rescoped adaptive wiki entry {}", entry.id);
            println!(
                "  scope: {}",
                wiki_scope_label(entry.scope, &entry.scope_ref)
            );
            println!("  audit: {}", audit.id);
        }
        WikiMutationResult::Deprecate { entry, audit } => {
            println!("Deprecated adaptive wiki entry {}", entry.id);
            println!("  reason: {}", audit.reason);
            println!("  audit:  {}", audit.id);
        }
        WikiMutationResult::AddCounterexample { entry, audit } => {
            println!("Added adaptive wiki counterexample to {}", entry.id);
            if let Some(evidence_ref) = audit.evidence_ref.as_deref() {
                println!("  evidence: {evidence_ref}");
            }
            println!("  audit:    {}", audit.id);
        }
        WikiMutationResult::UpdateRunbook { entry, audit } => {
            println!("Updated adaptive wiki runbook {}", entry.id);
            if !entry.support_refs.is_empty() {
                println!("  support: {}", entry.support_refs.join(", "));
            }
            if !entry.capability_ids.is_empty() {
                println!("  capabilities: {}", entry.capability_ids.join(", "));
            }
            if !entry.required_artifact_kinds.is_empty() {
                println!("  artifacts: {}", entry.required_artifact_kinds.join(", "));
            }
            println!("  audit:   {}", audit.id);
        }
        WikiMutationResult::RenewReviewAfter {
            entry,
            previous_review_after,
            audit,
        } => {
            println!("Renewed adaptive wiki review_after {}", entry.id);
            println!(
                "  previous: {}",
                previous_review_after
                    .as_ref()
                    .map(DateTime::<Utc>::to_rfc3339)
                    .unwrap_or_else(|| "-".to_string())
            );
            println!(
                "  review_after: {}",
                entry
                    .review_after
                    .as_ref()
                    .map(DateTime::<Utc>::to_rfc3339)
                    .unwrap_or_else(|| "-".to_string())
            );
            println!("  audit: {}", audit.id);
        }
    }
    Ok(())
}

fn print_wiki_proposal_handoff(preview: &WikiProposalHandoffPreview) {
    println!(
        "Adaptive wiki proposal handoff {}: {}",
        preview.proposal_id, preview.status
    );
    println!(
        "  proposal: {:?} {} {}",
        preview.action, preview.subject_kind, preview.subject_id
    );
    println!("  reason: {}", preview.reason);
    if let Some(decision) = preview.lifecycle_decision {
        println!(
            "  lifecycle: {:?}{}",
            decision,
            if preview.lifecycle_stale {
                " stale"
            } else {
                ""
            }
        );
    }
    if let Some(command) = preview.command.as_deref() {
        println!("  command: {command}");
    }
    if !preview.required_inputs.is_empty() {
        println!("  required inputs:");
        for input in &preview.required_inputs {
            let flag = input.cli_flag.unwrap_or(input.name);
            let required = if input.required {
                "required"
            } else {
                "conditional"
            };
            println!("    {flag} ({required}): {}", input.description);
        }
    }
    if !preview.mutation_options.is_empty() {
        println!("  mutation options:");
        for option in &preview.mutation_options {
            println!("    {}: {}", option.mutation, option.command_template);
            println!("      {}", option.description);
        }
    }
    if !preview.evidence_refs.is_empty() {
        println!("  evidence: {}", preview.evidence_refs.join(", "));
    }
}

fn print_wiki_proposal_receipt(receipt: &WikiProposalReceipt) {
    println!(
        "Adaptive wiki proposal receipt {}: {}",
        receipt.proposal.proposal_id, receipt.status
    );
    println!(
        "  proposal: {} {} current={}",
        empty_dash(&receipt.proposal.subject_kind),
        empty_dash(&receipt.proposal.subject_id),
        receipt.proposal.current
    );
    if let Some(action) = receipt.proposal.action {
        println!("  action: {:?}", action);
    }
    if let Some(decision) = receipt.proposal.lifecycle_decision {
        println!("  lifecycle: {:?}", decision);
    }
    if let Some(event_id) = receipt.proposal.lifecycle_event_id.as_deref() {
        println!("  event: {event_id}");
    }
    if let Some(audit) = receipt.audit.as_ref() {
        println!("  audit: {}", audit.id);
    }
    println!("  command_sha256: {}", receipt.preview_command_sha256);
    println!("  command: {}", receipt.preview_command);
    println!("  checks:");
    for check in &receipt.checks {
        println!(
            "    {}: {} ({})",
            check.name,
            if check.passed { "pass" } else { "fail" },
            check.detail
        );
    }
    if !receipt.blockers.is_empty() {
        println!("  blockers:");
        for blocker in &receipt.blockers {
            println!("    - {blocker}");
        }
    }
    if !receipt.proposal.evidence_refs.is_empty() {
        println!("  evidence: {}", receipt.proposal.evidence_refs.join(", "));
    }
}

fn print_wiki_lint(report: &AdaptiveWikiLintReport) {
    println!(
        "Adaptive wiki lint: {} errors, {} warnings, {} info ({} entries, {} candidates)",
        report.summary.errors,
        report.summary.warnings,
        report.summary.info,
        report.summary.entries_checked,
        report.summary.candidates_checked
    );
    for issue in &report.issues {
        println!(
            "  - {:?} {} {}: {}",
            issue.severity, issue.subject_kind, issue.subject_id, issue.message
        );
    }
}

fn print_wiki_projection_report(report: &AdaptiveWikiProjectionReport) {
    println!(
        "Adaptive wiki projection: {} selected, {} rejected, {} conflicts, {} review-expired ({} matching promoted entries)",
        report.summary.selected,
        report.summary.rejected,
        report.summary.conflicts,
        report.summary.review_expired_projected,
        report.summary.promoted_scope_matches
    );
    println!(
        "  budget: entries={} context_chars={} instruction_chars={}",
        report.budget.max_entries,
        report.budget.max_context_chars,
        report.budget.max_instruction_chars
    );
    println!(
        "  policy: review_expired={:?}",
        report.policy.review_expired
    );
    println!(
        "  estimated_context_chars: {}",
        report.summary.estimated_context_chars
    );
    if report.summary.instructions_truncated > 0 {
        println!(
            "  instructions_truncated: {}",
            report.summary.instructions_truncated
        );
    }
    if !report.selected.is_empty() {
        println!("  selected:");
        for entry in &report.selected {
            println!(
                "    {} {:?} {:?}:{} {:?} agent_modes={} evidence={}",
                entry.id,
                entry.kind,
                entry.scope,
                entry.scope_ref,
                entry.confidence,
                adaptive_wiki_agent_modes_label(&entry.agent_modes),
                entry.evidence_count
            );
        }
    }
    if !report.rejected.is_empty() {
        println!("  rejected:");
        for rejection in &report.rejected {
            println!(
                "    {} {:?}: {}",
                rejection.entry_id, rejection.reason, rejection.detail
            );
        }
    }
    if !report.conflicts.is_empty() {
        println!("  conflicts:");
        for conflict in &report.conflicts {
            println!(
                "    {} <-> {} {}: {}",
                conflict.entry_id,
                conflict.conflicting_entry_id,
                conflict.signature,
                conflict.detail
            );
        }
    }
    if !report.review_expired.is_empty() {
        println!("  review_expired:");
        for warning in &report.review_expired {
            println!(
                "    {} {:?}: review_after={} {}",
                warning.entry_id, warning.scope, warning.review_after, warning.detail
            );
        }
    }
}

fn print_wiki_projection_comparison_report(report: &AdaptiveWikiProjectionComparisonReport) {
    println!("Adaptive wiki projection review-expired policy comparison");
    println!(
        "  budget: entries={} context_chars={} instruction_chars={}",
        report.budget.max_entries,
        report.budget.max_context_chars,
        report.budget.max_instruction_chars
    );
    println!(
        "  warn:   selected={} rejected={} review_expired_projected={} context_chars={}",
        report.summary.warn_selected,
        report.summary.warn_rejected,
        report.warn.summary.review_expired_projected,
        report.summary.warn_estimated_context_chars
    );
    println!(
        "  strict: selected={} rejected={} review_expired_projected={} context_chars={}",
        report.summary.strict_selected,
        report.summary.strict_rejected,
        report.strict.summary.review_expired_projected,
        report.summary.strict_estimated_context_chars
    );
    if !report.summary.selected_only_in_warn.is_empty() {
        println!(
            "  selected only in warn: {}",
            report.summary.selected_only_in_warn.join(", ")
        );
    }
    if !report.summary.selected_only_in_strict.is_empty() {
        println!(
            "  selected only in strict: {}",
            report.summary.selected_only_in_strict.join(", ")
        );
    }
    if !report.summary.review_expired_excluded.is_empty() {
        println!(
            "  review_expired_excluded: {}",
            report.summary.review_expired_excluded.join(", ")
        );
    }
}

fn print_wiki_runtime_policy_acknowledgements(
    acknowledgements: &[AdaptiveWikiRuntimePolicyAcknowledgement],
) {
    println!(
        "{:<48} {:<18} {:<22} {:<22} POLICY",
        "ID", "SCOPE_MODE", "CREATED_AT", "EXPIRES_AT"
    );
    for acknowledgement in acknowledgements {
        println!(
            "{:<48} {:<18} {:<22} {:<22} review_expired={:?}",
            acknowledgement.id,
            runtime_ack_scope_mode_label(acknowledgement.scope_mode),
            acknowledgement.created_at,
            acknowledgement.expires_at,
            acknowledgement.policy.review_expired
        );
    }
}

fn print_wiki_runtime_policy_acknowledgement(
    acknowledgement: &AdaptiveWikiRuntimePolicyAcknowledgement,
) {
    println!(
        "  policy: review_expired={:?}",
        acknowledgement.policy.review_expired
    );
    println!(
        "  scope_mode: {}",
        runtime_ack_scope_mode_label(acknowledgement.scope_mode)
    );
    println!("  comparison_hash: {}", acknowledgement.comparison_hash);
    println!("  expires_at: {}", acknowledgement.expires_at);
    if !acknowledgement.review_expired_excluded.is_empty() {
        println!(
            "  review_expired_excluded: {}",
            acknowledgement.review_expired_excluded.join(", ")
        );
    }
    if !acknowledgement.selected_only_in_warn.is_empty() {
        println!(
            "  selected_only_in_warn: {}",
            acknowledgement.selected_only_in_warn.join(", ")
        );
    }
    if !acknowledgement.selected_only_in_strict.is_empty() {
        println!(
            "  selected_only_in_strict: {}",
            acknowledgement.selected_only_in_strict.join(", ")
        );
    }
    if !acknowledgement.reason.trim().is_empty() {
        println!("  reason: {}", acknowledgement.reason);
    }
}

fn print_wiki_runtime_policy_ack_report(report: &WikiRuntimePolicyAckReport) {
    println!("Adaptive wiki runtime policy acknowledgement report");
    println!(
        "  total: {}  active: {}  near_expiry: {}  expired: {}",
        report.summary.total,
        report.summary.active,
        report.summary.near_expiry,
        report.summary.expired
    );
    if let Some(decision) = report.decision.as_ref() {
        println!(
            "  query_decision: {:?} ack={} reason={}",
            decision.status,
            decision.acknowledgement_id.as_deref().unwrap_or("-"),
            decision.reason
        );
    }
    if report.acknowledgements.is_empty() {
        println!("No adaptive wiki runtime policy acknowledgements found.");
        return;
    }
    println!(
        "{:<48} {:<18} {:<34} {:<22} STATUS",
        "ID", "SCOPE_MODE", "QUERY", "EXPIRES_AT"
    );
    for acknowledgement in &report.acknowledgements {
        println!(
            "{:<48} {:<18} {:<34} {:<22} {}",
            acknowledgement.id,
            runtime_ack_scope_mode_label(acknowledgement.scope_mode),
            runtime_ack_query_label(&acknowledgement.query),
            acknowledgement.expires_at,
            acknowledgement.status.join(",")
        );
        if let Some(action) = acknowledgement.suggested_action.as_ref() {
            println!("  suggested_action: {}", action.kind);
            println!("    {}", action.detail);
            println!("    compare: {}", action.compare_command_template);
            println!("    ack:     {}", action.ack_command_template);
        }
    }
}

fn print_wiki_review_after_report(report: &WikiReviewAfterReport) {
    println!("Adaptive wiki review_after attention report");
    println!(
        "  scoped_promoted: {}  with_review_after: {}  missing_review_after: {}",
        report.summary.scoped_promoted,
        report.summary.with_review_after,
        report.summary.missing_review_after
    );
    println!(
        "  expired: {}  near_expiry: {}  attention: {}",
        report.summary.expired, report.summary.near_expiry, report.summary.attention
    );
    if report.entries.is_empty() {
        println!("No promoted adaptive wiki entries need review_after attention.");
        return;
    }
    println!(
        "{:<40} {:<12} {:<28} {:<14} SCOPE",
        "ID", "STATUS", "REVIEW_AFTER", "HOURS_LEFT"
    );
    for entry in &report.entries {
        println!(
            "{:<40} {:<12} {:<28} {:<14} {}",
            entry.id,
            entry.status,
            entry.review_after,
            entry.hours_until_review,
            wiki_scope_label(entry.scope, &entry.scope_ref)
        );
    }
}

fn runtime_ack_scope_mode_label(mode: AdaptiveWikiRuntimePolicyAckScopeMode) -> &'static str {
    match mode {
        AdaptiveWikiRuntimePolicyAckScopeMode::ExactQuery => "exact_query",
        AdaptiveWikiRuntimePolicyAckScopeMode::ProjectArtifact => "project_artifact",
    }
}

fn runtime_ack_query_label(query: &AdaptiveWikiQuery) -> String {
    let session = query.session_id.as_deref().unwrap_or("-");
    let project = query.project_key.as_deref().unwrap_or("-");
    let artifact = query.artifact_kind.as_deref().unwrap_or("-");
    let agent_mode = query
        .agent_mode
        .map(adaptive_wiki_agent_mode_cli_value)
        .unwrap_or("-");
    format!("s:{session} p:{project} a:{artifact} m:{agent_mode}")
}

fn print_wiki_markdown_export(report: &AdaptiveWikiMarkdownExportReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki markdown export {} {} files to {}",
        action, report.summary.files_planned, report.output_dir
    );
    println!(
        "  entries: {}  candidates: {}",
        report.summary.entries_exported, report.summary.candidates_exported
    );
    for file in &report.files {
        println!(
            "  - {} ({} bytes, sha256:{})",
            file.path, file.bytes, file.sha256
        );
    }
}

fn print_wiki_graph_report(
    report: &AdaptiveWikiGraphReport,
    output: Option<&Path>,
    dry_run: bool,
    files: usize,
) {
    println!(
        "Adaptive wiki tag graph: {} nodes, {} edges, {} review issues",
        report.nodes.len(),
        report.edges.len(),
        report.review_issues.len()
    );
    println!(
        "  entries: {}  candidates: {}  tag_nodes: {}",
        report.summary.entries, report.summary.candidates, report.summary.tag_nodes
    );
    println!(
        "  tag_edges: derived_core={} core={} proposed={}",
        report.summary.derived_core_tag_edges,
        report.summary.core_tag_edges,
        report.summary.proposed_tag_edges
    );
    if let Some(output) = output {
        let action = if dry_run { "planned" } else { "wrote" };
        println!(
            "  export: {} {} files to {}",
            action,
            files,
            output.display()
        );
    }
    for issue in report.review_issues.iter().take(8) {
        println!(
            "  - {:?} {}:{} #{} {}",
            issue.severity, issue.subject_kind, issue.subject_id, issue.tag, issue.code
        );
    }
}

fn print_wiki_review_report(report: &AdaptiveWikiReviewReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki review report {} {} proposals ({} open, {} filtered out) at {}",
        action,
        report.summary.proposals,
        report.summary.open_proposals,
        report.summary.filtered_out_proposals,
        report.report_dir
    );
    println!(
        "  checked: {} entries, {} candidates, {} usage records, {} audit records, {} correction records, {} review events",
        report.summary.entries_checked,
        report.summary.candidates_checked,
        report.summary.usage_records_checked,
        report.summary.audit_records_checked,
        report.summary.correction_records_checked,
        report.summary.review_events_checked
    );
    println!(
        "  lint: {} errors, {} warnings, {} info",
        report.summary.lint_errors, report.summary.lint_warnings, report.summary.lint_info
    );
    println!(
        "  lifecycle: {} with events, {} accepted, {} rejected, {} superseded",
        report.summary.proposals_with_events,
        report.summary.accepted_proposals,
        report.summary.rejected_proposals,
        report.summary.superseded_proposals
    );
    if report.summary.stale_decision_proposals > 0 {
        println!(
            "  stale decisions: {} need renewed review",
            report.summary.stale_decision_proposals
        );
    }
    for proposal in &report.proposals {
        let lifecycle = proposal
            .lifecycle
            .as_ref()
            .map(|lifecycle| {
                let stale = if lifecycle.stale { ", stale" } else { "" };
                format!(
                    "{:?} by {}{}",
                    lifecycle.decision,
                    empty_dash(&lifecycle.actor),
                    stale
                )
            })
            .unwrap_or_else(|| "Open".to_string());
        println!(
            "  - {:?} {} {} ({:?}, {}): {}",
            proposal.action,
            proposal.subject_kind,
            proposal.subject_id,
            proposal.risk,
            lifecycle,
            proposal.title
        );
        if let Some(lifecycle) = proposal.lifecycle.as_ref() {
            println!(
                "    lifecycle: event={} at={} reason={}",
                lifecycle.latest_event_id,
                lifecycle.decided_at.to_rfc3339(),
                empty_dash(&lifecycle.reason)
            );
            if !lifecycle.stale_evidence_refs.is_empty() {
                println!(
                    "    stale evidence: {}",
                    lifecycle.stale_evidence_refs.join(", ")
                );
            }
        }
        if let Some(command) = proposal.suggested_command.as_deref() {
            println!("    command: {command}");
        }
        println!("    evidence: {}", proposal.evidence_refs.join(", "));
    }
}

fn print_wiki_episode_evaluation_report(report: &AdaptiveWikiEpisodeEvaluationReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    let status = if report.passed { "passed" } else { "failed" };
    println!(
        "Adaptive wiki episode evaluation {action} at {} ({status})",
        report.report_dir
    );
    println!("  target: {}", report.target_entry_id);
    println!(
        "  in-scope: {} entries  out-of-scope: {} entries",
        report.summary.in_scope_projection_count, report.summary.out_of_scope_projection_count
    );
    println!(
        "  checks: target_in_scope={} target_out_of_scope={} scope_leakage={} review_expired_projected={} deprecated_projected={} projected_without_evidence={}",
        report.summary.target_entry_in_scope,
        report.summary.target_entry_out_of_scope,
        report.summary.scope_leakage_count,
        report.summary.review_expired_entry_projected,
        report.summary.deprecated_entry_projected,
        report.summary.projected_without_evidence
    );
    if report.failures.is_empty() {
        println!("  failures: none");
    } else {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {failure}");
        }
    }
    if !report.in_scope_projection.is_empty() {
        println!(
            "  in-scope ids: {}",
            report
                .in_scope_projection
                .iter()
                .map(|entry| entry.id.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    if !report.out_of_scope_projection.is_empty() {
        println!(
            "  out-of-scope ids: {}",
            report
                .out_of_scope_projection
                .iter()
                .map(|entry| entry.id.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
}

fn print_wiki_live_episode_trace_report(report: &AdaptiveWikiLiveEpisodeTraceReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki live episode trace {action} {} events at {}",
        report.summary.events, report.report_dir
    );
    println!(
        "  tasks: {}  runtime usage: {}  projections: {}  candidates: {}  corrections: {}",
        report.summary.task_events,
        report.summary.runtime_usage_events,
        report.summary.projection_events,
        report.summary.candidate_events,
        report.summary.correction_events
    );
    println!(
        "  promotions: {}  completed: {}  failed: {}  resume pending: {}  rollbacks: {}",
        report.summary.promotion_events,
        report.summary.completion_events,
        report.summary.failure_events,
        report.summary.resume_pending_events,
        report.summary.rollback_events
    );
    if report.summary.usage_without_task > 0 {
        println!(
            "  usage without matching task: {}",
            report.summary.usage_without_task
        );
    }
    for event in &report.events {
        println!(
            "  - {:?} {} task={} request={} entries={} {}",
            event.kind,
            event.occurred_at.to_rfc3339(),
            event.task_id.as_deref().unwrap_or("-"),
            event.request_id.as_deref().unwrap_or("-"),
            event.entry_ids.join(","),
            event.summary
        );
    }
}

fn print_wiki_correction_recurrence_report(report: &AdaptiveWikiCorrectionRecurrenceReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki correction recurrence {action} at {} ({:?})",
        report.report_dir, report.assessment
    );
    println!("  entry: {}", report.entry_id);
    if let Some(scope) = &report.scope {
        println!(
            "  scope: {}",
            wiki_scope_label(scope.scope, &scope.scope_ref)
        );
    }
    if let Some(promotion_at) = report.promotion_at {
        println!("  promotion: {}", promotion_at.to_rfc3339());
    }
    println!(
        "  corrections: pre={} post={} delta={}",
        report.summary.pre_promotion_correction_events,
        report.summary.post_promotion_correction_events,
        report.summary.recurrence_delta
    );
    println!(
        "  post usage={} failures={} counterexamples={} recurrence_per_1000={}",
        report.summary.post_promotion_usage_events,
        report.summary.post_promotion_failure_events,
        report.summary.post_promotion_counterexample_events,
        report.summary.post_promotion_recurrence_per_1000
    );
    if !report.failures.is_empty() {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {failure}");
        }
    }
}

fn print_wiki_promotion_chain_report(report: &AdaptiveWikiPromotionEvidenceChainReport) {
    let action = if report.dry_run { "planned" } else { "wrote" };
    println!(
        "Adaptive wiki promotion evidence chain {action} at {}",
        report.report_dir
    );
    println!("  entry: {}", report.entry_id);
    println!(
        "  promotion audit={} candidate snapshot={} entry snapshot={} current entry={}",
        report.summary.promotion_audit_found,
        report.summary.candidate_snapshot_present,
        report.summary.entry_snapshot_present,
        report.summary.current_entry_present
    );
    println!(
        "  usage records={} related audits={} failures={}",
        report.summary.usage_records, report.summary.related_audit_records, report.summary.failures
    );
    if let Some(audit) = &report.promotion_audit {
        println!(
            "  promoted at {} candidate={} actor={}",
            audit.created_at.to_rfc3339(),
            audit.candidate_id.as_deref().unwrap_or("-"),
            audit.actor
        );
    }
    if !report.failures.is_empty() {
        println!("  failures:");
        for failure in &report.failures {
            println!("    - {failure}");
        }
    }
}

fn wiki_scope_label(scope: AdaptiveWikiScope, scope_ref: &str) -> String {
    format!("{:?}:{}", scope, scope_ref).to_lowercase()
}

fn print_approvals(approvals: &[PendingActionApproval]) {
    println!(
        "{:<44} {:<44} {:<10} {:<18} {:<24} ACTION",
        "APPROVAL ID", "ACTION ID", "STATUS", "RISK", "TASK"
    );
    for approval in approvals {
        println!(
            "{:<44} {:<44} {:<10} {:<18} {:<24} {}",
            approval.approval_id,
            approval.action_id(),
            format!("{:?}", approval.status).to_lowercase(),
            format!("{:?}", approval.risk_level).to_lowercase(),
            approval.task_id,
            approval.action
        );
        if !approval.preview.trim().is_empty() {
            println!("  preview: {}", approval.preview);
        }
        if !approval.reason.trim().is_empty() {
            println!("  reason:  {}", approval.reason);
        }
        if let Some(metadata) = approval
            .metadata
            .as_ref()
            .and_then(crate::offdesk::ActionApprovalMetadata::as_provider_fallback)
        {
            if let Some(brief) = metadata.approval_brief.as_ref() {
                println!(
                    "  prompt: {} recommendation for {}",
                    brief.recommendation, brief.subject
                );
                for line in brief.summary_lines.iter().take(3) {
                    println!("    {}", line);
                }
                println!("  question: {}", brief.question);
                println!("  scope: {}", brief.scope);
            }
            println!(
                "  fallback target: {} model {} ({})",
                metadata.current_provider_id,
                metadata.current_model.as_deref().unwrap_or("-"),
                format!("{:?}", metadata.apply_scope).to_lowercase()
            );
            for candidate in metadata.candidates.iter().take(metadata.candidate_limit) {
                println!(
                    "    - {} {} ({:?})",
                    candidate.provider_id,
                    candidate.model.as_deref().unwrap_or("-"),
                    candidate.source
                );
            }
        }
    }
}

fn print_resume_states(states: &[TaskResumeState]) {
    let now = Utc::now();
    println!(
        "{:<24} {:<16} {:<8} {:<18} NEXT STEP",
        "TASK", "STATUS", "FRESH", "RUNNER"
    );
    for state in states {
        let fresh = if state.status == ResumeStatus::ResumePending {
            if state.is_fresh_at(now) {
                "fresh"
            } else {
                "stale"
            }
        } else {
            "-"
        };
        println!(
            "{:<24} {:<16} {:<8} {:<18} {}",
            state.task_id,
            format!("{:?}", state.status).to_lowercase(),
            fresh,
            state.runner_target,
            state.next_safe_resume_step
        );
        println!("  resume_id: {}", state.resume_id());
        for evidence in state.evidence.iter().take(3) {
            let present = evidence
                .present
                .map(|present| if present { "present" } else { "missing" });
            match (evidence.path.as_deref(), present) {
                (Some(path), Some(present)) => {
                    println!(
                        "  evidence: {}: {} ({present}, {path})",
                        evidence.kind, evidence.summary
                    );
                }
                (Some(path), None) => {
                    println!(
                        "  evidence: {}: {} ({path})",
                        evidence.kind, evidence.summary
                    );
                }
                _ => println!("  evidence: {}: {}", evidence.kind, evidence.summary),
            }
        }
        if state.evidence.len() > 3 {
            println!("  evidence: +{} more", state.evidence.len() - 3);
        }
        if let Some(tail) = state.last_log_tail.as_deref() {
            println!("  tail: {tail}");
        }
    }
}

fn print_lifecycle_report(report: &OffdeskTaskLifecycleReport, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(report)?);
        return Ok(());
    }

    println!(
        "{} offdesk task {}: {} -> {} ({})",
        if report.changed {
            "Updated"
        } else {
            "Unchanged"
        },
        report.task.task_id,
        status_label(report.previous_status),
        status_label(report.status),
        report.message
    );
    if let Some(ticket_id) = report.task.background_ticket_id.as_deref() {
        println!("  ticket: {}", ticket_id);
    }
    if !report.task.reason.trim().is_empty() {
        println!("  reason: {}", report.task.reason);
    }
    if let Some(error) = report.task.last_error.as_deref() {
        println!("  error:  {}", error);
    }
    Ok(())
}

fn print_retry_lifecycle_report(
    report: &OffdeskTaskLifecycleReport,
    superseded_denied_approvals: usize,
    json: bool,
    include_denied_reset: bool,
) -> Result<()> {
    if json {
        println!(
            "{}",
            serde_json::to_string_pretty(&RetryTaskLifecycleReport {
                report,
                superseded_denied_approvals,
            })?
        );
        return Ok(());
    }

    print_lifecycle_report(report, false)?;
    if include_denied_reset {
        println!(
            "  superseded denied approvals: {}",
            superseded_denied_approvals
        );
    }
    Ok(())
}

fn print_tasks(tasks: &[OffdeskTaskView]) {
    let open = tasks
        .iter()
        .filter(|task| !is_terminal_task_status(task.status))
        .collect::<Vec<_>>();
    let terminal = tasks
        .iter()
        .filter(|task| is_terminal_task_status(task.status))
        .collect::<Vec<_>>();

    if !open.is_empty() {
        println!("Open tasks:");
        print_task_rows(&open);
    }
    if !terminal.is_empty() {
        if !open.is_empty() {
            println!();
        }
        println!("Terminal tasks:");
        print_task_rows(&terminal);
    }
}

fn print_task_rows(tasks: &[&OffdeskTaskView]) {
    println!(
        "{:<24} {:<18} {:<18} {:<14} TICKET",
        "TASK", "STATUS", "CAPABILITY", "RUNNER"
    );
    for task in tasks {
        println!(
            "{:<24} {:<18} {:<18} {:<14} {}",
            task.task_id,
            status_label(task.status),
            task.capability_id,
            format!("{:?}", task.runner_kind).to_lowercase(),
            task.background_ticket_id.as_deref().unwrap_or("-")
        );
        if !task.preview.trim().is_empty() {
            println!("  preview: {}", task.preview);
        }
        if let Some(last_error) = task.last_error.as_deref() {
            println!("  error:   {}", last_error);
        }
        if !task.last_adaptive_wiki_entry_ids.is_empty() {
            println!(
                "  adaptive_wiki: {}",
                task.last_adaptive_wiki_entry_ids.join(", ")
            );
        }
        if let Some(agent_mode) = task.agent_mode {
            println!(
                "  agent_mode: {}",
                adaptive_wiki_agent_mode_cli_value(agent_mode)
            );
        }
        print_mode_assessment(&task.mode_assessment);
        if task.provider_id.is_some() || task.model.is_some() {
            println!(
                "  provider: {} model: {}",
                task.provider_id.as_deref().unwrap_or("-"),
                task.model.as_deref().unwrap_or("-")
            );
        }
        if let Some(artifact_kind) = task.artifact_kind.as_deref() {
            println!("  artifact_kind: {}", artifact_kind);
        }
        if let Some(fallback) = task.last_provider_fallback.as_ref() {
            let recommended = fallback
                .candidates
                .iter()
                .filter(|candidate| candidate.recommended)
                .count();
            println!(
                "  fallback: {} candidates, {} recommended",
                fallback.candidates.len(),
                recommended
            );
            for candidate in fallback
                .candidates
                .iter()
                .filter(|candidate| candidate.recommended)
                .take(3)
            {
                println!(
                    "    - {} {} ({:?})",
                    candidate.provider_id,
                    candidate.model.as_deref().unwrap_or("-"),
                    candidate.source
                );
            }
        }
        if let Some(not_before) = task.not_before {
            println!("  not_before: {not_before}");
        }
        if let Some(last_gate_status) = task.last_gate_status {
            println!(
                "  gate:    {}",
                format!("{:?}", last_gate_status).to_lowercase()
            );
        }
        print_next_safe_action(&task.next_safe_action);
    }
}

fn print_next_safe_actions(actions: &[OffdeskNextSafeAction]) {
    if actions.is_empty() {
        return;
    }
    println!("Next safe actions:");
    for action in actions {
        print_next_safe_action(action);
    }
}

fn print_next_safe_action(action: &OffdeskNextSafeAction) {
    println!("  next:    {}", action.detail);
    if !action.commands.is_empty() {
        println!("  command: {}", action.commands.join(" | "));
    }
    if action.requires_operator_review {
        println!("  review:  operator review required");
    }
}

fn print_mode_assessment(assessment: &OffdeskModeAssessment) {
    println!(
        "  mode_verdict: {} risk: {}",
        assessment.mode_verdict.label(),
        assessment.mode_risk.label()
    );
    println!("  mode_risk_detail: {}", assessment.mode_risk_detail);
    if assessment.review_stage_required {
        println!("  review_stage_required: true");
    }
}

fn print_provider_fallback(recommendation: &ProviderFallbackRecommendation) {
    println!(
        "Provider fallback for {} model {}",
        recommendation.current_provider_id,
        recommendation.current_model.as_deref().unwrap_or("-")
    );
    println!("  trigger: {}", recommendation.trigger_reason);
    if recommendation.candidates.is_empty() {
        println!("  no fallback candidates found");
        return;
    }
    println!(
        "{:<20} {:<28} {:<30} {:<14} {:<14} RECOMMENDED",
        "PROVIDER", "MODEL", "SOURCE", "AUTH", "CAPACITY"
    );
    for candidate in &recommendation.candidates {
        println!(
            "{:<20} {:<28} {:<30} {:<14} {:<14} {}",
            candidate.provider_id,
            candidate.model.as_deref().unwrap_or("-"),
            format!("{:?}", candidate.source).to_lowercase(),
            format!("{:?}", candidate.auth_status).to_lowercase(),
            format!("{:?}", candidate.capacity_status).to_lowercase(),
            if candidate.recommended { "yes" } else { "no" }
        );
        println!("  reason: {}", candidate.reason);
    }
}

fn print_provider_capacity(states: &[ProviderCapacityState]) {
    println!(
        "{:<20} {:<24} {:<14} {:<16} COOLDOWN_UNTIL",
        "PROVIDER", "MODEL", "STATUS", "REASON"
    );
    for state in states {
        println!(
            "{:<20} {:<24} {:<14} {:<16} {}",
            crate::offdesk::operator_safe_text(&state.provider_id),
            state
                .model
                .as_deref()
                .map(crate::offdesk::operator_safe_text)
                .unwrap_or_else(|| "-".to_string()),
            format!("{:?}", state.status).to_lowercase(),
            format!("{:?}", state.reason).to_lowercase(),
            state
                .cooldown_until
                .map(|cooldown_until| cooldown_until.to_string())
                .unwrap_or_else(|| "-".to_string())
        );
        if let Some(summary) = state.last_error_summary.as_deref() {
            println!("  summary: {}", crate::offdesk::operator_safe_text(summary));
        }
    }
}

fn is_terminal_task_status(status: OffdeskTaskStatus) -> bool {
    matches!(
        status,
        OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled
    )
}

fn parse_offdesk_task_status(value: &str) -> std::result::Result<OffdeskTaskStatus, String> {
    match value.trim().to_ascii_lowercase().replace('_', "-").as_str() {
        "queued" => Ok(OffdeskTaskStatus::Queued),
        "pending-approval" => Ok(OffdeskTaskStatus::PendingApproval),
        "launched" => Ok(OffdeskTaskStatus::Launched),
        "running" => Ok(OffdeskTaskStatus::Running),
        "completed" => Ok(OffdeskTaskStatus::Completed),
        "failed" => Ok(OffdeskTaskStatus::Failed),
        "resume-pending" => Ok(OffdeskTaskStatus::ResumePending),
        "cancelled" => Ok(OffdeskTaskStatus::Cancelled),
        _ => Err("expected one of: queued, pending-approval, launched, running, completed, failed, resume-pending, cancelled".to_string()),
    }
}

fn status_label(status: OffdeskTaskStatus) -> String {
    match status {
        OffdeskTaskStatus::Queued => "queued",
        OffdeskTaskStatus::PendingApproval => "pending-approval",
        OffdeskTaskStatus::Launched => "launched",
        OffdeskTaskStatus::Running => "running",
        OffdeskTaskStatus::Completed => "completed",
        OffdeskTaskStatus::Failed => "failed",
        OffdeskTaskStatus::ResumePending => "resume-pending",
        OffdeskTaskStatus::Cancelled => "cancelled",
    }
    .to_string()
}

fn print_capabilities(capabilities: &[CapabilityDescriptor]) {
    println!(
        "{:<24} {:<20} {:<18} {:<8} LABEL",
        "CAPABILITY", "OWNER", "RISK", "OFFDESK"
    );
    for capability in capabilities {
        println!(
            "{:<24} {:<20} {:<18} {:<8} {}",
            capability.capability_id,
            capability.owner_module,
            format!("{:?}", capability.risk_level).to_lowercase(),
            if capability.offdesk_allowed {
                "yes"
            } else {
                "no"
            },
            capability.dashboard_label
        );
    }
}

fn print_debug_bundle_summary(bundle: &OffdeskDebugBundle) {
    println!("Offdesk debug bundle");
    println!("  generated_at:       {}", bundle.generated_at);
    println!("  profile:            {}", bundle.profile);
    println!("  profile_dir:        {}", bundle.profile_dir);
    println!("  read_only:          {}", bundle.read_only);
    println!(
        "  approvals:          {}",
        json_array_len(&bundle.approvals)
    );
    println!("  tasks:              {}", json_array_len(&bundle.tasks));
    println!(
        "  resume_states:      {}",
        json_array_len(&bundle.resume_states)
    );
    println!(
        "  background_runs:    {}",
        json_array_len(&bundle.background_runs)
    );
    println!(
        "  provider_capacity:  {}",
        json_array_len(&bundle.provider_capacity)
    );
    println!(
        "  wiki_usage:         {}",
        json_array_len(&bundle.adaptive_wiki_usage)
    );
    println!(
        "  wiki_corrections:   {}",
        json_array_len(&bundle.adaptive_wiki_corrections)
    );
    println!(
        "  wiki_review_events: {}",
        json_array_len(&bundle.adaptive_wiki_review_events)
    );
    println!(
        "  wiki_runtime_acks:  {}",
        json_array_len(&bundle.adaptive_wiki_runtime_policy_acknowledgements)
    );
    println!(
        "  wiki_ack_attention: expired={} near_expiry={} suggested_actions={}",
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .expired,
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .near_expiry,
        bundle
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .suggested_actions
    );
    println!(
        "  wiki_review_after:  expired={} near_expiry={} missing_review_after={}",
        bundle.adaptive_wiki_review_after_attention_summary.expired,
        bundle
            .adaptive_wiki_review_after_attention_summary
            .near_expiry,
        bundle
            .adaptive_wiki_review_after_attention_summary
            .missing_review_after
    );
    println!(
        "  redaction:          {} changed fields, {} context blocks, {} secrets",
        bundle.redaction_summary.changed_text_fields,
        bundle.redaction_summary.runner_context_removed,
        bundle.redaction_summary.secrets_redacted
    );
}

fn print_maintenance_report(report: &OffdeskMaintenanceReport) {
    println!("Offdesk maintenance report");
    println!("  generated_at:       {}", report.generated_at);
    println!("  profile:            {}", report.profile);
    println!("  profile_dir:        {}", report.profile_dir);
    println!("  read_only:          {}", report.read_only);
    println!(
        "  tasks:              total={} status=[{}] risk=[{}]",
        report.tasks.total,
        format_counts(&report.tasks.by_status),
        format_counts(&report.tasks.mode.by_risk)
    );
    println!(
        "  task_modes:         agent=[{}] review_required={}",
        format_counts(&report.tasks.by_agent_mode),
        report.tasks.mode.review_stage_required
    );
    println!(
        "  background_runs:    total={} phase=[{}] risk=[{}]",
        report.background_runs.total,
        format_counts(&report.background_runs.by_phase),
        format_counts(&report.background_runs.mode.by_risk)
    );
    println!(
        "  approvals:          total={} pending={} status=[{}]",
        report.approvals.total,
        report.approvals.pending,
        format_counts(&report.approvals.by_status)
    );
    println!(
        "  resume_states:      total={} status=[{}]",
        report.resume_states.total,
        format_counts(&report.resume_states.by_status)
    );
    println!(
        "  provider_capacity:  total={} attention={} status=[{}]",
        report.provider_capacity.total,
        report.provider_capacity.attention,
        format_counts(&report.provider_capacity.by_status)
    );
    println!(
        "  wiki_ack_attention: expired={} near_expiry={} suggested_actions={}",
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .expired,
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .near_expiry,
        report
            .adaptive_wiki_runtime_policy_ack_attention_summary
            .suggested_actions
    );
    println!(
        "  wiki_review_after:  expired={} near_expiry={} missing_review_after={}",
        report.adaptive_wiki_review_after_attention_summary.expired,
        report
            .adaptive_wiki_review_after_attention_summary
            .near_expiry,
        report
            .adaptive_wiki_review_after_attention_summary
            .missing_review_after
    );
    if report.recommended_actions.is_empty() {
        println!("No maintenance actions recommended.");
    } else {
        println!("Recommended actions:");
        for action in &report.recommended_actions {
            println!("  - {}: {}", action.kind, action.detail);
            println!("    command: {}", action.command);
        }
    }
}

fn print_maintenance_request_report(report: &MaintenanceApprovalRequestReport) {
    println!("Offdesk maintenance approval request");
    println!("  generated_at: {}", report.generated_at);
    println!("  action:       {}", report.action);
    println!("  kind:         {}", report.action_kind.cli_value());
    println!("  risk:         {}", enum_label(report.risk_level));
    println!("  status:       {}", report.status);
    println!("  detail:       {}", report.detail);
    println!("  project_key:  {}", report.project_key);
    println!("  request_id:   {}", report.request_id);
    println!("  task_id:      {}", report.task_id);
    if let Some(target_id) = &report.target_id {
        println!("  target_id:    {}", target_id);
    }
    if let Some(approval) = &report.approval {
        if let Some(approval_id) = approval["approval_id"].as_str() {
            println!("  approval_id:  {}", approval_id);
        }
    }
    if !report.next_commands.is_empty() {
        println!("Next commands:");
        for command in &report.next_commands {
            println!("  - {}", command);
        }
    }
}

fn format_counts(counts: &BTreeMap<String, usize>) -> String {
    if counts.is_empty() {
        return "-".to_string();
    }
    counts
        .iter()
        .map(|(key, value)| format!("{key}={value}"))
        .collect::<Vec<_>>()
        .join(",")
}

fn json_array_len(value: &Value) -> usize {
    value.as_array().map_or(0, Vec::len)
}

fn snapshot_list_item(
    snapshot: MutationSnapshot,
    verification: MutationSnapshotVerification,
) -> MutationSnapshotListItem {
    MutationSnapshotListItem {
        mutation_id: snapshot.mutation_id,
        target_path: snapshot.target_path,
        mutation_kind: snapshot.mutation_kind,
        created_at: snapshot.created_at,
        rollback_available: verification.rollback_available,
        blockers: verification.blockers,
    }
}

fn print_snapshot_list(items: &[MutationSnapshotListItem]) {
    println!(
        "{:<44} {:<14} {:<9} TARGET",
        "MUTATION ID", "KIND", "ROLLBACK"
    );
    for item in items {
        println!(
            "{:<44} {:<14} {:<9} {}",
            item.mutation_id,
            item.mutation_kind,
            if item.rollback_available { "yes" } else { "no" },
            item.target_path
        );
        if !item.blockers.is_empty() {
            println!("  blockers: {}", item.blockers.join("; "));
        }
    }
}

fn print_snapshot_verification(verification: &MutationSnapshotVerification) {
    let Some(snapshot) = verification.snapshot.as_ref() else {
        println!("Mutation snapshot not found: {}", verification.mutation_id);
        return;
    };
    println!("Snapshot {}", snapshot.mutation_id);
    println!("  target:              {}", snapshot.target_path);
    println!("  mutation_kind:       {}", snapshot.mutation_kind);
    println!("  rollback_available:  {}", verification.rollback_available);
    println!(
        "  target_exists_now:   {}",
        verification
            .target_exists_now
            .map(|exists| exists.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
    println!(
        "  target_matches_before: {}",
        verification
            .target_current_matches_before
            .map(|matches| matches.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
    if let Some(path) = verification.before_snapshot_path.as_deref() {
        println!("  before_snapshot:     {path}");
    }
    if !verification.blockers.is_empty() {
        println!(
            "  blockers:            {}",
            verification.blockers.join("; ")
        );
    }
}

fn print_restore_plan(plan: &MutationRestorePlan) {
    println!("Restore plan {}", plan.mutation_id);
    println!("  target:             {}", plan.target_path);
    println!("  operation:          {:?}", plan.operation);
    println!("  rollback_available: {}", plan.rollback_available);
    match plan.operation {
        MutationRestoreOperation::RestoreFile => {
            if let Some(path) = plan.before_snapshot_path.as_deref() {
                println!("  source:             {path}");
            } else {
                println!("  source:             empty file");
            }
        }
        MutationRestoreOperation::DeleteFile => {
            println!("  source:             target did not exist before mutation");
        }
        MutationRestoreOperation::Unavailable => {}
    }
    if !plan.blockers.is_empty() {
        println!("  blockers:           {}", plan.blockers.join("; "));
    }
}
