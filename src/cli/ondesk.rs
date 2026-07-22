//! `forager ondesk` subcommands for bridging live external harness work.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use clap::{Args, Subcommand};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::str::FromStr;
use uuid::Uuid;

use super::project_audit::{
    audit_recommendations_for_project, AuditRecommendation, DocumentationAuditProfile,
};
use super::review_surface::{self, ReviewSurfaceArgs};
use super::workstation_surface::{self, WorkstationSurfaceArgs};
use crate::offdesk::{
    operator_safe_text, BackgroundRunnerKind, DecisionLedger, DecisionReceipt, DecisionRecord,
    DecisionStatus, DecisionTraceRef, ExecutionHandoff, OffdeskTask, OffdeskTaskInput,
    OffdeskTaskStore,
};
use crate::session::{get_profile_dir, Instance, Storage};

const NOTES_FILE: &str = "ondesk_notes.jsonl";
const ACTION_ENVELOPE_SCHEMA: &str = "action_envelope.v1";
const ACTION_ENVELOPE_RECEIPT_SCHEMA: &str = "action_envelope_receipt.v1";
const ACTION_ENVELOPE_RECEIPTS_FILE: &str = "action_envelope_receipts.jsonl";
const ACCEPTED_TRUTH_RECOVERY_ACTION_ENVELOPE_SCHEMA: &str =
    "accepted_truth_recovery_action_envelope.v1";
const ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPT_SCHEMA: &str =
    "accepted_truth_recovery_action_receipt.v1";
const ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPTS_FILE: &str =
    "accepted_truth_recovery_action_receipts.jsonl";
const ACTION_EXECUTION_PREFLIGHT_SCHEMA: &str = "action_execution_preflight.v1";
const ACTION_EXECUTION_PREFLIGHTS_FILE: &str = "action_execution_preflights.jsonl";
const DECISION_ACTION_EXECUTION_SCHEMA: &str = "decision_action_execution.v1";
const DECISION_ACTION_EXECUTIONS_FILE: &str = "decision_action_executions.jsonl";
const DECISION_ACTION_CLOSEOUT_SCHEMA: &str = "decision_action_closeout.v1";
const DECISION_ACTION_CLOSEOUTS_FILE: &str = "decision_action_closeouts.jsonl";
const RUNTIME_DISPATCH_PREFLIGHT_SCHEMA: &str = "runtime_dispatch_preflight.v1";
const RUNTIME_DISPATCH_PREFLIGHTS_FILE: &str = "runtime_dispatch_preflights.jsonl";
const RUNTIME_DISPATCH_RECEIPT_SCHEMA: &str = "runtime_dispatch_receipt.v1";
const RUNTIME_DISPATCH_RECEIPTS_FILE: &str = "runtime_dispatch_receipts.jsonl";
const CAPTURES_DIR: &str = "ondesk_captures";
const PROMPT_CONTEXT_FILE: &str = "PROMPT_CONTEXT.md";
const CAPTURE_FILE: &str = "capture.json";
const MAX_CAPTURE_CHARS: usize = 30_000;
const MAX_GIT_CHARS: usize = 12_000;
const MAX_PROMPT_CHARS: usize = 40_000;
const MAX_CLOSEOUT_CHARS: usize = 16_000;
const MAX_PROJECT_INIT_CHARS: usize = 16_000;
const MAX_MODULE_PREFLIGHT_TARGETS: usize = 6;
const MAX_MODULE_PREFLIGHT_BLOCKERS: usize = 8;
const MAX_MODULE_PREFLIGHT_COMMANDS: usize = 8;
const MAX_RECENT_NOTES: usize = 20;
const MAX_DOC_AUDIT_RECOMMENDATIONS: usize = 5;
const MAX_DOC_AUDIT_PATHS: usize = 3;

#[derive(Subcommand)]
pub enum OndeskCommands {
    /// Append a safe operator note for an ondesk session or project
    Note(NoteArgs),

    /// Capture live harness scrollback into an inspectable prompt package
    Capture(CaptureArgs),

    /// Build a markdown prompt package from recent notes and optional capture
    #[command(name = "prompt-package")]
    PromptPackage(PromptPackageArgs),

    /// Emit the shared review surface for Ondesk and future rich UIs
    #[command(name = "review-surface")]
    ReviewSurface(ReviewSurfaceArgs),

    /// Emit the workstation dashboard surface for the Web UI control plane
    #[command(name = "workstation-surface")]
    WorkstationSurface(WorkstationSurfaceArgs),

    /// Validate a Web UI action envelope and record a receipt
    #[command(name = "action-envelope")]
    ActionEnvelope(ActionEnvelopeProcessArgs),

    /// Validate an accepted-truth recovery envelope and record a receipt
    #[command(name = "accepted-truth-recovery-envelope")]
    AcceptedTruthRecoveryEnvelope(AcceptedTruthRecoveryEnvelopeProcessArgs),

    /// Preflight a validated action receipt before any mutation-capable executor
    #[command(name = "action-preflight")]
    ActionPreflight(ActionPreflightArgs),

    /// Execute a supported decision action from a ready action preflight
    #[command(name = "action-decision")]
    ActionDecision(ActionDecisionArgs),

    /// Close an applied decision action execution with a canonical decision receipt
    #[command(name = "action-closeout")]
    ActionCloseout(ActionCloseoutArgs),

    /// Preflight a receipted decision action closeout before runtime dispatch
    #[command(name = "runtime-preflight")]
    RuntimePreflight(RuntimePreflightArgs),

    /// Queue runtime work from a ready runtime dispatch preflight
    #[command(name = "runtime-dispatch")]
    RuntimeDispatch(RuntimeDispatchArgs),
}

#[derive(Args)]
pub struct NoteArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Operator note text to persist
    #[arg(long)]
    text: String,

    /// Work mode label, e.g. planning, analysis, writing, critique
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key for grouping ondesk knowledge
    #[arg(long)]
    project_key: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct CaptureArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Number of tmux scrollback lines to capture
    #[arg(long, default_value_t = 200)]
    lines: usize,

    /// Work mode label, e.g. planning, analysis, writing, critique
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key for grouping ondesk knowledge
    #[arg(long)]
    project_key: Option<String>,

    /// Include read-only git status and diff-stat from the session/project path
    #[arg(long)]
    include_git: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct PromptPackageArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Existing capture ID to render
    #[arg(long)]
    capture_id: Option<String>,

    /// Work mode label used to filter notes
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key used to filter notes
    #[arg(long)]
    project_key: Option<String>,

    /// Include a fresh documentation governance audit from the latest closeout workdir or resolved project path
    #[arg(long)]
    include_doc_audit: bool,

    /// Write markdown package to a file instead of stdout
    #[arg(long)]
    output: Option<PathBuf>,

    /// Output metadata as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ActionEnvelopeProcessArgs {
    /// JSON file containing an action_envelope.v1 preview
    #[arg(long)]
    envelope: PathBuf,

    /// Validate without writing action_envelope_receipts.jsonl
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct AcceptedTruthRecoveryEnvelopeProcessArgs {
    /// JSON file containing an accepted_truth_recovery_action_envelope.v1 preview
    #[arg(long)]
    envelope: PathBuf,

    /// Validate without writing accepted_truth_recovery_action_receipts.jsonl
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ActionPreflightArgs {
    /// Receipt ID from action_envelope_receipts.jsonl
    #[arg(long)]
    receipt_id: String,

    /// Validate without writing action_execution_preflights.jsonl
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ActionDecisionArgs {
    /// Ready action_execution_preflight.v1 ID
    #[arg(long)]
    preflight_id: String,

    /// Required bounded direction for revise/block/custom decisions
    #[arg(long, default_value = "")]
    note: String,

    /// Actor recording the decision action
    #[arg(long, default_value = "operator")]
    by: String,

    /// Override execution handoff target
    #[arg(long)]
    target: Option<String>,

    /// Validate without appending the decision record or execution receipt
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ActionCloseoutArgs {
    /// Applied decision_action_execution.v1 ID
    #[arg(long)]
    execution_id: String,

    /// Actor recording the closeout receipt
    #[arg(long, default_value = "operator")]
    by: String,

    /// Result status for the consumed decision action handoff
    #[arg(long, default_value = "closed")]
    result_status: String,

    /// Evidence summary line. Repeat for multiple lines.
    #[arg(long = "evidence")]
    evidence_summary: Vec<String>,

    /// Remaining review item. Repeat for multiple lines.
    #[arg(long = "remaining-review")]
    remaining_review: Vec<String>,

    /// Validate without appending the decision receipt or closeout record
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct RuntimePreflightArgs {
    /// Receipted decision_action_closeout.v1 ID
    #[arg(long)]
    closeout_id: String,

    /// Validate without writing runtime_dispatch_preflights.jsonl
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct RuntimeDispatchArgs {
    /// Ready runtime_dispatch_preflight.v1 ID
    #[arg(long)]
    preflight_id: String,

    /// Runner backend to queue for later offdesk tick dispatch
    #[arg(long)]
    runner: String,

    /// Shell command to execute when the queued task is dispatched
    #[arg(long = "cmd")]
    command: String,

    /// Working directory for --cmd. Defaults to the current directory.
    #[arg(long)]
    workdir: Option<PathBuf>,

    /// Task ID. Generated deterministically if omitted.
    #[arg(long)]
    task_id: Option<String>,

    /// Capability ID. Currently restricted to dispatch.runtime.
    #[arg(long, default_value = "dispatch.runtime")]
    capability_id: String,

    /// Provider ID to check against provider capacity cooldown state when dispatched
    #[arg(long)]
    provider_id: Option<String>,

    /// Provider model to check against provider capacity cooldown state when dispatched
    #[arg(long)]
    model: Option<String>,

    /// Log artifact path for command stdout and stderr
    #[arg(long)]
    log_artifact: Option<PathBuf>,

    /// Result sidecar path used by tick to mark the task completed
    #[arg(long)]
    result_artifact: Option<PathBuf>,

    /// Validate without writing offdesk_tasks.json or runtime_dispatch_receipts.jsonl
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionRef {
    id: String,
    title: String,
    path: String,
    group: String,
    tool: String,
    command: String,
    status: String,
}

impl SessionRef {
    fn from_instance(instance: &Instance) -> Self {
        Self {
            id: safe(&instance.id),
            title: safe(&instance.title),
            path: safe(&instance.project_path),
            group: safe(&instance.group_path),
            tool: safe(&instance.tool),
            command: safe(&instance.command),
            status: format!("{:?}", instance.status).to_lowercase(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OndeskNoteRecord {
    id: String,
    created_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct GitSnapshot {
    #[serde(skip_serializing_if = "Option::is_none")]
    status_short: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    diff_stat: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OndeskCaptureRecord {
    id: String,
    created_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session: Option<SessionRef>,
    lines_requested: usize,
    session_running: bool,
    scrollback: String,
    scrollback_char_count: usize,
    scrollback_truncated: bool,
    notes: Vec<OndeskNoteRecord>,
    #[serde(skip_serializing_if = "Option::is_none")]
    git: Option<GitSnapshot>,
    artifact_dir: String,
    capture_path: String,
    prompt_package_path: String,
}

#[derive(Debug, Serialize)]
struct NoteOutput {
    id: String,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    notes_path: String,
}

#[derive(Debug, Serialize)]
struct CaptureOutput {
    id: String,
    profile: String,
    project_key: String,
    session_running: bool,
    scrollback_char_count: usize,
    scrollback_truncated: bool,
    note_count: usize,
    artifact_dir: String,
    capture_path: String,
    prompt_package_path: String,
}

#[derive(Debug, Serialize)]
struct PromptPackageOutput {
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    capture_id: Option<String>,
    note_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    latest_closeout: Option<OndeskCloseoutSummary>,
    #[serde(skip_serializing_if = "Option::is_none")]
    latest_project_initialization: Option<OndeskProjectInitializationSummary>,
    review_surface: Value,
    documentation_governance: OndeskDocumentationGovernanceSummary,
    #[serde(skip_serializing_if = "Option::is_none")]
    output_path: Option<String>,
    content: String,
}

#[derive(Debug, Deserialize)]
struct ActionEnvelopeInput {
    schema: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    target_ref: ActionEnvelopeTargetRef,
    observed_hash: String,
    nonce: String,
    ttl: String,
    idempotency_key: String,
    preview: String,
    allowed_command: String,
    forbidden_effects: Vec<String>,
    expected_receipt_schema: String,
    requires_confirmation: bool,
    #[serde(default)]
    confirmation_phrase: Option<String>,
    stale_rejection_reason: String,
    #[serde(default)]
    issued_at: Option<DateTime<Utc>>,
    #[serde(default)]
    expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Deserialize)]
struct ActionEnvelopeTargetRef {
    kind: String,
    decision_id: String,
    status: String,
    updated_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
struct AcceptedTruthRecoveryEnvelopeInput {
    schema: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    target_ref: AcceptedTruthRecoveryTargetRef,
    observed_hash: String,
    nonce: String,
    ttl: String,
    idempotency_key: String,
    preview: String,
    allowed_command: String,
    forbidden_effects: Vec<String>,
    expected_receipt_schema: String,
    requires_confirmation: bool,
    confirmation_phrase: String,
    stale_rejection_reason: String,
    #[serde(default)]
    issued_at: Option<DateTime<Utc>>,
    #[serde(default)]
    expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Deserialize)]
struct AcceptedTruthRecoveryTargetRef {
    kind: String,
    closeout_id: String,
    review_id: String,
    receipt_id: String,
    acceptance_status: String,
    reviewed_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
struct ActionEnvelopeProcessOutput {
    receipt: ActionEnvelopeReceipt,
    receipt_path: String,
    receipt_appended: bool,
    dry_run: bool,
}

#[derive(Debug, Serialize)]
struct AcceptedTruthRecoveryEnvelopeProcessOutput {
    receipt: AcceptedTruthRecoveryActionReceipt,
    receipt_path: String,
    receipt_appended: bool,
    dry_run: bool,
}

#[derive(Debug, Serialize)]
struct ActionEnvelopeReceipt {
    schema: &'static str,
    receipt_id: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    decision_id: String,
    processed_at: DateTime<Utc>,
    result_status: &'static str,
    stale: bool,
    reason: String,
    observed_hash: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    current_hash: Option<String>,
    idempotency_key: String,
    expected_receipt_schema: String,
    allowed_command: String,
    forbidden_effects: Vec<String>,
    checks: Vec<ActionEnvelopeCheck>,
}

#[derive(Debug, Serialize)]
struct AcceptedTruthRecoveryActionReceipt {
    schema: &'static str,
    receipt_id: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    closeout_id: String,
    review_id: String,
    receipt_source_id: String,
    processed_at: DateTime<Utc>,
    result_status: &'static str,
    stale: bool,
    reason: String,
    observed_hash: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    current_hash: Option<String>,
    idempotency_key: String,
    expected_receipt_schema: String,
    allowed_command: String,
    forbidden_effects: Vec<String>,
    checks: Vec<ActionEnvelopeCheck>,
}

#[derive(Debug, Serialize)]
struct ActionEnvelopeCheck {
    name: &'static str,
    status: &'static str,
    detail: String,
}

#[derive(Debug, Clone, Deserialize)]
struct StoredActionEnvelopeReceipt {
    schema: String,
    receipt_id: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    decision_id: String,
    processed_at: DateTime<Utc>,
    result_status: String,
    stale: bool,
    observed_hash: String,
    #[serde(default)]
    current_hash: Option<String>,
    idempotency_key: String,
    allowed_command: String,
    forbidden_effects: Vec<String>,
}

#[derive(Debug, Serialize)]
struct ActionPreflightOutput {
    preflight: ActionExecutionPreflight,
    preflight_path: String,
    preflight_appended: bool,
    dry_run: bool,
}

#[derive(Debug, Serialize)]
struct ActionExecutionPreflight {
    schema: &'static str,
    preflight_id: String,
    source_receipt_id: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    decision_id: String,
    processed_at: DateTime<Utc>,
    result_status: &'static str,
    executor_required: bool,
    mutation_allowed_by_this_command: bool,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    current_hash: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_current_hash: Option<String>,
    idempotency_key: String,
    next_step: String,
    checks: Vec<ActionPreflightCheck>,
}

#[derive(Debug, Serialize)]
struct ActionPreflightCheck {
    name: &'static str,
    status: &'static str,
    detail: String,
}

#[derive(Debug, Clone, Deserialize)]
struct StoredActionExecutionPreflight {
    schema: String,
    preflight_id: String,
    source_receipt_id: String,
    action_id: String,
    action_kind: String,
    profile: String,
    project_key: String,
    decision_id: String,
    result_status: String,
    #[serde(default)]
    current_hash: Option<String>,
    idempotency_key: String,
}

#[derive(Debug, Serialize)]
struct ActionDecisionOutput {
    execution: Value,
    execution_path: String,
    execution_appended: bool,
    decision_appended: bool,
    dry_run: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    updated_record: Option<DecisionRecord>,
}

#[derive(Debug, Serialize)]
struct ActionCloseoutOutput {
    closeout: Value,
    closeout_path: String,
    closeout_appended: bool,
    decision_appended: bool,
    dry_run: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    updated_record: Option<DecisionRecord>,
}

#[derive(Debug, Serialize)]
struct DecisionActionExecution {
    schema: &'static str,
    execution_id: String,
    preflight_id: String,
    source_receipt_id: String,
    action_id: String,
    action_kind: String,
    decision: String,
    profile: String,
    project_key: String,
    decision_id: String,
    executed_at: DateTime<Utc>,
    result_status: &'static str,
    mutation_allowed_by_this_command: bool,
    decision_appended: bool,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    handoff_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    current_hash: Option<String>,
    idempotency_key: String,
    checks: Vec<ActionDecisionCheck>,
}

#[derive(Debug, Serialize)]
struct ActionDecisionCheck {
    name: &'static str,
    status: &'static str,
    detail: String,
}

#[derive(Debug, Clone, Deserialize)]
struct StoredDecisionActionExecution {
    schema: String,
    execution_id: String,
    preflight_id: String,
    action_kind: String,
    decision: String,
    profile: String,
    project_key: String,
    decision_id: String,
    result_status: String,
    mutation_allowed_by_this_command: bool,
    decision_appended: bool,
    #[serde(default)]
    handoff_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct DecisionActionCloseout {
    schema: &'static str,
    closeout_id: String,
    execution_id: String,
    preflight_id: String,
    action_kind: String,
    decision: String,
    profile: String,
    project_key: String,
    decision_id: String,
    recorded_at: DateTime<Utc>,
    result_status: &'static str,
    receipt_result_status: String,
    mutation_allowed_by_this_command: bool,
    decision_appended: bool,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    handoff_id: Option<String>,
    evidence_summary: Vec<String>,
    remaining_review: Vec<String>,
    checks: Vec<ActionCloseoutCheck>,
}

#[derive(Debug, Serialize)]
struct ActionCloseoutCheck {
    name: &'static str,
    status: &'static str,
    detail: String,
}

#[derive(Debug, Clone, Deserialize)]
struct StoredDecisionActionCloseout {
    schema: String,
    closeout_id: String,
    execution_id: String,
    preflight_id: String,
    action_kind: String,
    decision: String,
    profile: String,
    project_key: String,
    decision_id: String,
    result_status: String,
    mutation_allowed_by_this_command: bool,
    decision_appended: bool,
    #[serde(default)]
    receipt_id: Option<String>,
    #[serde(default)]
    handoff_id: Option<String>,
}

#[derive(Debug, Serialize)]
struct RuntimePreflightOutput {
    preflight: RuntimeDispatchPreflight,
    preflight_path: String,
    preflight_appended: bool,
    dry_run: bool,
}

#[derive(Debug, Serialize)]
struct RuntimeDispatchOutput {
    receipt: Value,
    receipt_path: String,
    receipt_appended: bool,
    task_enqueued: bool,
    dry_run: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    task: Option<crate::offdesk::OffdeskTaskView>,
}

#[derive(Debug, Serialize)]
struct RuntimeDispatchPreflight {
    schema: &'static str,
    preflight_id: String,
    source_closeout_id: String,
    source_execution_id: String,
    source_action_preflight_id: String,
    action_kind: String,
    decision: String,
    profile: String,
    project_key: String,
    decision_id: String,
    request_id: String,
    source_task_id: String,
    processed_at: DateTime<Utc>,
    result_status: &'static str,
    mutation_allowed_by_this_command: bool,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    handoff_id: Option<String>,
    next_step: String,
    checks: Vec<RuntimeDispatchCheck>,
}

#[derive(Debug, Clone, Deserialize)]
struct StoredRuntimeDispatchPreflight {
    schema: String,
    preflight_id: String,
    source_closeout_id: String,
    source_execution_id: String,
    profile: String,
    project_key: String,
    decision_id: String,
    request_id: String,
    result_status: String,
}

#[derive(Debug, Serialize)]
struct RuntimeDispatchReceipt {
    schema: &'static str,
    receipt_id: String,
    preflight_id: String,
    source_closeout_id: String,
    source_execution_id: String,
    profile: String,
    project_key: String,
    decision_id: String,
    request_id: String,
    task_id: String,
    capability_id: String,
    runner_kind: BackgroundRunnerKind,
    command: String,
    workdir: String,
    recorded_at: DateTime<Utc>,
    result_status: &'static str,
    mutation_allowed_by_this_command: bool,
    task_enqueued: bool,
    reason: String,
    next_step: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    provider_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    model: Option<String>,
    checks: Vec<RuntimeDispatchCheck>,
}

#[derive(Debug, Clone, Serialize)]
struct RuntimeDispatchCheck {
    name: &'static str,
    status: &'static str,
    detail: String,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskCloseoutSummary {
    closeout_id: String,
    generated_at: String,
    artifact_dir: String,
    return_package_path: String,
    return_package_truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    review_verdict: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    review_record_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_path: Option<String>,
}

#[derive(Debug, Clone)]
struct OndeskCloseoutPackage {
    summary: OndeskCloseoutSummary,
    return_package: String,
    audit_project_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskProjectInitializationSummary {
    initialization_id: String,
    generated_at: String,
    artifact_dir: String,
    operation_profile_path: String,
    ondesk_start_package_path: String,
    offdesk_ready_check_path: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    module_operation_preflight_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    module_operation_preflight: Option<OndeskModuleOperationPreflightSummary>,
    operation_targets: Vec<String>,
    ready_for_ondesk_start: Option<bool>,
    ready_for_offdesk_runtime: Option<bool>,
    requires_operator_review: Option<bool>,
    start_package_truncated: bool,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskModuleOperationPreflightSummary {
    path: String,
    ready_for_offdesk_runtime: Option<bool>,
    blocker_count: usize,
    blockers: Vec<String>,
    operation_targets: Vec<OndeskModuleOperationPreflightTargetSummary>,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskModuleOperationPreflightTargetSummary {
    scope_ref: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    readiness_level: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    recognized_profile_kind: Option<String>,
    profile_builder_available: Option<bool>,
    evidence_bundle_builder_available: Option<bool>,
    evidence_review_builder_available: Option<bool>,
    blockers: Vec<String>,
    recommended_command_purposes: Vec<String>,
}

#[derive(Debug, Clone)]
struct OndeskProjectInitializationPackage {
    summary: OndeskProjectInitializationSummary,
    start_package: String,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskDocumentationGovernanceSummary {
    source: String,
    requested_fresh_audit: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    command: Option<String>,
    recommendation_count: usize,
    recommendations: Vec<OndeskDocumentationRecommendation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    closeout_return_package_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskDocumentationRecommendation {
    priority: String,
    kind: String,
    title: String,
    suggested_action: String,
    paths: Vec<String>,
}

struct ResolvedOndeskContext {
    profile: String,
    profile_dir: PathBuf,
    session: Option<Instance>,
    project_path: PathBuf,
    project_key: String,
    mode: Option<String>,
}

pub async fn run(profile: &str, command: OndeskCommands) -> Result<()> {
    match command {
        OndeskCommands::Note(args) => note(profile, args).await,
        OndeskCommands::Capture(args) => capture(profile, args).await,
        OndeskCommands::PromptPackage(args) => prompt_package(profile, args).await,
        OndeskCommands::ReviewSurface(args) => review_surface::run(profile, args).await,
        OndeskCommands::WorkstationSurface(args) => workstation_surface::run(profile, args).await,
        OndeskCommands::ActionEnvelope(args) => action_envelope(profile, args).await,
        OndeskCommands::AcceptedTruthRecoveryEnvelope(args) => {
            accepted_truth_recovery_envelope(profile, args).await
        }
        OndeskCommands::ActionPreflight(args) => action_preflight(profile, args).await,
        OndeskCommands::ActionDecision(args) => action_decision(profile, args).await,
        OndeskCommands::ActionCloseout(args) => action_closeout(profile, args).await,
        OndeskCommands::RuntimePreflight(args) => runtime_preflight(profile, args).await,
        OndeskCommands::RuntimeDispatch(args) => runtime_dispatch(profile, args).await,
    }
}

async fn note(profile: &str, args: NoteArgs) -> Result<()> {
    let context = resolve_context(
        profile,
        args.identifier.as_deref(),
        args.project_key,
        args.mode,
    )?;
    let notes_path = context.profile_dir.join(NOTES_FILE);
    let record = OndeskNoteRecord {
        id: short_id("ondesk-note"),
        created_at: Utc::now(),
        profile: context.profile.clone(),
        project_key: context.project_key.clone(),
        session_id: context.session.as_ref().map(|session| safe(&session.id)),
        session_title: context.session.as_ref().map(|session| safe(&session.title)),
        session_path: context
            .session
            .as_ref()
            .map(|session| safe(&session.project_path)),
        mode: context.mode.clone(),
        text: safe(&args.text),
    };

    append_note(&notes_path, &record)?;

    if args.json {
        let output = NoteOutput {
            id: record.id,
            profile: context.profile,
            project_key: context.project_key,
            session_id: record.session_id,
            mode: record.mode,
            notes_path: notes_path.display().to_string(),
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!("Recorded ondesk note: {}", record.id);
        println!("  Project: {}", context.project_key);
        if let Some(session) = &context.session {
            println!("  Session: {} ({})", session.title, session.id);
        }
        println!("  Path:    {}", notes_path.display());
    }

    Ok(())
}

async fn capture(profile: &str, args: CaptureArgs) -> Result<()> {
    let context = resolve_context(
        profile,
        args.identifier.as_deref(),
        args.project_key,
        args.mode,
    )?;
    let notes = matching_recent_notes(&context.profile_dir, &context)?;
    let (scrollback, session_running) = capture_scrollback(context.session.as_ref(), args.lines)?;
    let safe_scrollback = safe(&scrollback);
    let (scrollback, scrollback_truncated) = truncate_chars(&safe_scrollback, MAX_CAPTURE_CHARS);
    let scrollback_char_count = scrollback.chars().count();
    let git = if args.include_git {
        context
            .session
            .as_ref()
            .map(|session| git_snapshot(Path::new(&session.project_path)))
            .transpose()?
    } else {
        None
    };

    let capture_id = short_id("ondesk-cap");
    let now = Utc::now();
    let capture_dir = context.profile_dir.join(CAPTURES_DIR).join(format!(
        "{}_{}",
        now.format("%Y%m%dT%H%M%SZ"),
        capture_id
    ));
    fs::create_dir_all(&capture_dir)?;

    let capture_path = capture_dir.join(CAPTURE_FILE);
    let prompt_package_path = capture_dir.join(PROMPT_CONTEXT_FILE);
    let record = OndeskCaptureRecord {
        id: capture_id.clone(),
        created_at: now,
        profile: context.profile.clone(),
        project_key: context.project_key.clone(),
        mode: context.mode.clone(),
        session: context.session.as_ref().map(SessionRef::from_instance),
        lines_requested: args.lines,
        session_running,
        scrollback,
        scrollback_char_count,
        scrollback_truncated,
        notes,
        git,
        artifact_dir: capture_dir.display().to_string(),
        capture_path: capture_path.display().to_string(),
        prompt_package_path: prompt_package_path.display().to_string(),
    };

    let project_initialization =
        latest_project_initialization(&context.profile_dir, &context.project_key)?;
    let review_surface =
        review_surface::build_review_surface_value(&context.profile, Some(&context.project_key))?;
    let documentation_governance = prompt_documentation_governance(
        false,
        Some(context.project_path.as_path()),
        &context.project_key,
        None,
    );
    let package = render_prompt_package(PromptPackageContext::Capture {
        capture: &record,
        closeout: None,
        project_initialization: project_initialization.as_ref(),
        review_surface: Some(&review_surface),
        documentation_governance: &documentation_governance,
    });
    fs::write(&capture_path, serde_json::to_string_pretty(&record)?)?;
    fs::write(&prompt_package_path, package)?;

    if args.json {
        let output = CaptureOutput {
            id: record.id,
            profile: record.profile,
            project_key: record.project_key,
            session_running: record.session_running,
            scrollback_char_count: record.scrollback_char_count,
            scrollback_truncated: record.scrollback_truncated,
            note_count: record.notes.len(),
            artifact_dir: record.artifact_dir,
            capture_path: record.capture_path,
            prompt_package_path: record.prompt_package_path,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!("Captured ondesk context: {}", record.id);
        println!("  Project: {}", record.project_key);
        println!("  Running: {}", record.session_running);
        println!("  Notes:   {}", record.notes.len());
        println!("  Package: {}", record.prompt_package_path);
    }

    Ok(())
}

async fn prompt_package(profile: &str, args: PromptPackageArgs) -> Result<()> {
    let profile_dir = get_profile_dir(profile)?;
    let profile_name = Storage::new(profile)?.profile().to_string();
    let (
        content,
        project_key,
        note_count,
        capture_id,
        latest_closeout,
        latest_project_initialization,
        review_surface,
        documentation_governance,
    ) = if let Some(capture_id) = args.capture_id {
        let capture = load_capture_by_id(&profile_dir, &capture_id)?;
        let closeout = latest_closeout_package(&profile_dir, &capture.project_key)?;
        let project_initialization =
            latest_project_initialization(&profile_dir, &capture.project_key)?;
        let review_surface =
            review_surface::build_review_surface_value(&profile_name, Some(&capture.project_key))?;
        let capture_audit_path = prompt_audit_path_for_capture(&capture)?;
        let audit_path =
            prompt_documentation_governance_project_path(closeout.as_ref(), &capture_audit_path);
        let documentation_governance = prompt_documentation_governance(
            args.include_doc_audit,
            Some(audit_path.as_path()),
            &capture.project_key,
            closeout.as_ref(),
        );
        let note_count = capture.notes.len();
        let project_key = capture.project_key.clone();
        (
            render_prompt_package(PromptPackageContext::Capture {
                capture: &capture,
                closeout: closeout.as_ref(),
                project_initialization: project_initialization.as_ref(),
                review_surface: Some(&review_surface),
                documentation_governance: &documentation_governance,
            }),
            project_key,
            note_count,
            Some(capture.id),
            closeout.map(|package| package.summary),
            project_initialization.map(|package| package.summary),
            review_surface,
            documentation_governance,
        )
    } else {
        let context = resolve_context(
            profile,
            args.identifier.as_deref(),
            args.project_key,
            args.mode,
        )?;
        let notes = matching_recent_notes(&context.profile_dir, &context)?;
        let session_ref = context.session.as_ref().map(SessionRef::from_instance);
        let closeout = latest_closeout_package(&context.profile_dir, &context.project_key)?;
        let project_initialization =
            latest_project_initialization(&context.profile_dir, &context.project_key)?;
        let review_surface = review_surface::build_review_surface_value(
            &context.profile,
            Some(&context.project_key),
        )?;
        let audit_path =
            prompt_documentation_governance_project_path(closeout.as_ref(), &context.project_path);
        let documentation_governance = prompt_documentation_governance(
            args.include_doc_audit,
            Some(audit_path.as_path()),
            &context.project_key,
            closeout.as_ref(),
        );
        let content = render_prompt_package(PromptPackageContext::Live {
            profile: &context.profile,
            project_key: &context.project_key,
            mode: context.mode.as_deref(),
            session: session_ref.as_ref(),
            notes: &notes,
            closeout: closeout.as_ref(),
            project_initialization: project_initialization.as_ref(),
            review_surface: Some(&review_surface),
            documentation_governance: &documentation_governance,
        });
        (
            content,
            context.project_key,
            notes.len(),
            None,
            closeout.map(|package| package.summary),
            project_initialization.map(|package| package.summary),
            review_surface,
            documentation_governance,
        )
    };

    let (content, truncated) = truncate_chars(&content, MAX_PROMPT_CHARS);
    let output_path = if let Some(path) = args.output {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, &content)?;
        Some(path.display().to_string())
    } else {
        None
    };

    if args.json {
        let output = PromptPackageOutput {
            profile: profile_name,
            project_key,
            capture_id,
            note_count,
            latest_closeout,
            latest_project_initialization,
            review_surface,
            documentation_governance,
            output_path,
            content: if truncated {
                format!("{}\n\n[package truncated for CLI output]", content)
            } else {
                content
            },
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else if let Some(path) = output_path {
        println!("Wrote ondesk prompt package: {}", path);
    } else {
        print!("{}", content);
        if truncated {
            println!("\n\n[package truncated for CLI output]");
        }
    }

    Ok(())
}

async fn action_envelope(profile: &str, args: ActionEnvelopeProcessArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let envelope_content = fs::read_to_string(&args.envelope)
        .with_context(|| format!("read action envelope {}", args.envelope.display()))?;
    let envelope: ActionEnvelopeInput = serde_json::from_str(&envelope_content)
        .with_context(|| format!("parse action envelope {}", args.envelope.display()))?;
    let record = DecisionLedger::new(&profile_dir).find(&envelope.target_ref.decision_id)?;
    let receipt = build_action_envelope_receipt(&profile_name, &envelope, record.as_ref());
    let receipt_path = profile_dir.join(ACTION_ENVELOPE_RECEIPTS_FILE);
    let receipt_appended = if args.dry_run {
        false
    } else {
        append_action_envelope_receipt(&receipt_path, &receipt)?
    };

    if args.json {
        let output = ActionEnvelopeProcessOutput {
            receipt,
            receipt_path: receipt_path.display().to_string(),
            receipt_appended,
            dry_run: args.dry_run,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Action envelope {}: {}",
            receipt.result_status, receipt.receipt_id
        );
        println!("  Decision: {}", receipt.decision_id);
        println!("  Stale:    {}", receipt.stale);
        println!("  Appended: {}", receipt_appended);
        println!("  Receipt:  {}", receipt_path.display());
        if receipt.stale {
            println!("  Reason:   {}", receipt.reason);
        }
    }

    Ok(())
}

async fn accepted_truth_recovery_envelope(
    profile: &str,
    args: AcceptedTruthRecoveryEnvelopeProcessArgs,
) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let envelope_content = fs::read_to_string(&args.envelope).with_context(|| {
        format!(
            "read accepted truth recovery envelope {}",
            args.envelope.display()
        )
    })?;
    let envelope: AcceptedTruthRecoveryEnvelopeInput = serde_json::from_str(&envelope_content)
        .with_context(|| {
            format!(
                "parse accepted truth recovery envelope {}",
                args.envelope.display()
            )
        })?;
    let surface = workstation_surface::accepted_truth_recovery_surface_value(&profile_name)?;
    let current_item = accepted_truth_recovery_current_item(&surface, &envelope);
    let receipt =
        build_accepted_truth_recovery_action_receipt(&profile_name, &envelope, current_item);
    let receipt_path = profile_dir.join(ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPTS_FILE);
    let receipt_appended = if args.dry_run {
        false
    } else {
        append_accepted_truth_recovery_action_receipt(&receipt_path, &receipt)?
    };

    if args.json {
        let output = AcceptedTruthRecoveryEnvelopeProcessOutput {
            receipt,
            receipt_path: receipt_path.display().to_string(),
            receipt_appended,
            dry_run: args.dry_run,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Accepted-truth recovery envelope {}: {}",
            receipt.result_status, receipt.receipt_id
        );
        println!("  Closeout: {}", receipt.closeout_id);
        println!("  Stale:    {}", receipt.stale);
        println!("  Appended: {}", receipt_appended);
        println!("  Receipt:  {}", receipt_path.display());
        if receipt.stale {
            println!("  Reason:   {}", receipt.reason);
        }
    }

    Ok(())
}

async fn action_preflight(profile: &str, args: ActionPreflightArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let receipts = read_action_envelope_receipts(&profile_dir)?;
    let source_receipt = receipts
        .iter()
        .find(|receipt| receipt.receipt_id == args.receipt_id);
    let record = source_receipt
        .map(|receipt| {
            DecisionLedger::new(&profile_dir)
                .find(&receipt.decision_id)
                .with_context(|| format!("read decision {}", receipt.decision_id))
        })
        .transpose()?
        .flatten();
    let preflight = build_action_execution_preflight(
        &profile_name,
        &args.receipt_id,
        source_receipt,
        &receipts,
        record.as_ref(),
    );
    let preflight_path = profile_dir.join(ACTION_EXECUTION_PREFLIGHTS_FILE);
    let preflight_appended = if args.dry_run {
        false
    } else {
        append_action_execution_preflight(&preflight_path, &preflight)?
    };

    if args.json {
        let output = ActionPreflightOutput {
            preflight,
            preflight_path: preflight_path.display().to_string(),
            preflight_appended,
            dry_run: args.dry_run,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Action preflight {}: {}",
            preflight.result_status, preflight.preflight_id
        );
        println!("  Receipt:  {}", preflight.source_receipt_id);
        println!("  Decision: {}", preflight.decision_id);
        println!("  Appended: {}", preflight_appended);
        println!("  Path:     {}", preflight_path.display());
        if preflight.result_status != "ready_for_executor" {
            println!("  Reason:   {}", preflight.reason);
        }
    }

    Ok(())
}

async fn action_decision(profile: &str, args: ActionDecisionArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let preflights = read_action_execution_preflights(&profile_dir)?;
    let source_preflight = preflights
        .iter()
        .find(|preflight| preflight.preflight_id == args.preflight_id);
    let ledger = DecisionLedger::new(&profile_dir);
    let execution_path = profile_dir.join(DECISION_ACTION_EXECUTIONS_FILE);
    if let Some(existing_execution) =
        find_decision_action_execution_for_preflight(&execution_path, &args.preflight_id)?
    {
        if args.json {
            let output = ActionDecisionOutput {
                execution: existing_execution,
                execution_path: execution_path.display().to_string(),
                execution_appended: false,
                decision_appended: false,
                dry_run: args.dry_run,
                updated_record: None,
            };
            println!("{}", serde_json::to_string_pretty(&output)?);
        } else {
            println!("Decision action already recorded for {}", args.preflight_id);
            println!("  Receipt: {}", execution_path.display());
        }
        return Ok(());
    }

    let record = source_preflight
        .map(|preflight| {
            ledger
                .find(&preflight.decision_id)
                .with_context(|| format!("read decision {}", preflight.decision_id))
        })
        .transpose()?
        .flatten();
    let execution = build_decision_action_execution(
        &profile_name,
        &args.preflight_id,
        source_preflight,
        record.as_ref(),
        &args,
    );
    let mut updated_record = None;
    let mut decision_appended = false;
    let mut execution_appended = false;

    if !args.dry_run {
        if execution.result_status == "applied" {
            if let Some(record) = record {
                let updated = apply_decision_action(record, &execution, &args);
                ledger.append(&updated)?;
                decision_appended = true;
                updated_record = Some(updated);
            }
        }
        execution_appended = append_decision_action_execution(&execution_path, &execution)?;
    }

    if args.json {
        let execution_value = serde_json::to_value(&execution)?;
        let output = ActionDecisionOutput {
            execution: execution_value,
            execution_path: execution_path.display().to_string(),
            execution_appended,
            decision_appended,
            dry_run: args.dry_run,
            updated_record,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Decision action {}: {}",
            execution.result_status, execution.execution_id
        );
        println!("  Preflight: {}", execution.preflight_id);
        println!("  Decision:  {}", execution.decision_id);
        println!("  Appended:  {}", decision_appended);
        println!("  Receipt:   {}", execution_path.display());
        if execution.result_status != "applied" {
            println!("  Reason:    {}", execution.reason);
        }
    }

    Ok(())
}

async fn action_closeout(profile: &str, args: ActionCloseoutArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let execution_path = profile_dir.join(DECISION_ACTION_EXECUTIONS_FILE);
    let closeout_path = profile_dir.join(DECISION_ACTION_CLOSEOUTS_FILE);

    if let Some(existing_closeout) =
        find_decision_action_closeout_for_execution(&closeout_path, &args.execution_id)?
    {
        if args.json {
            let output = ActionCloseoutOutput {
                closeout: existing_closeout,
                closeout_path: closeout_path.display().to_string(),
                closeout_appended: false,
                decision_appended: false,
                dry_run: args.dry_run,
                updated_record: None,
            };
            println!("{}", serde_json::to_string_pretty(&output)?);
        } else {
            println!(
                "Decision action closeout already recorded for {}",
                args.execution_id
            );
            println!("  Receipt: {}", closeout_path.display());
        }
        return Ok(());
    }

    let source_execution = find_decision_action_execution(&execution_path, &args.execution_id)?;
    let ledger = DecisionLedger::new(&profile_dir);
    let record = source_execution
        .as_ref()
        .map(|execution| {
            ledger
                .find(&execution.decision_id)
                .with_context(|| format!("read decision {}", execution.decision_id))
        })
        .transpose()?
        .flatten();
    let closeout = build_decision_action_closeout(
        &profile_name,
        &args.execution_id,
        source_execution.as_ref(),
        record.as_ref(),
        &args,
    );
    let mut updated_record = None;
    let mut decision_appended = false;
    let mut closeout_appended = false;

    if !args.dry_run {
        if closeout.result_status == "receipted" {
            if let Some(record) = record {
                let updated = apply_decision_action_closeout(record, &closeout, &args);
                ledger.append(&updated)?;
                decision_appended = true;
                updated_record = Some(updated);
            }
        }
        closeout_appended = append_decision_action_closeout(&closeout_path, &closeout)?;
    }

    if args.json {
        let output = ActionCloseoutOutput {
            closeout: serde_json::to_value(&closeout)?,
            closeout_path: closeout_path.display().to_string(),
            closeout_appended,
            decision_appended,
            dry_run: args.dry_run,
            updated_record,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Decision action closeout {}: {}",
            closeout.result_status, closeout.closeout_id
        );
        println!("  Execution: {}", closeout.execution_id);
        println!("  Decision:  {}", closeout.decision_id);
        println!("  Appended:  {}", decision_appended);
        println!("  Receipt:   {}", closeout_path.display());
        if closeout.result_status != "receipted" {
            println!("  Reason:    {}", closeout.reason);
        }
    }

    Ok(())
}

async fn runtime_preflight(profile: &str, args: RuntimePreflightArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let closeout_path = profile_dir.join(DECISION_ACTION_CLOSEOUTS_FILE);
    let closeouts = read_decision_action_closeouts(&closeout_path)?;
    let source_closeout = closeouts
        .iter()
        .find(|closeout| closeout.closeout_id == args.closeout_id);
    let record = source_closeout
        .map(|closeout| {
            DecisionLedger::new(&profile_dir)
                .find(&closeout.decision_id)
                .with_context(|| format!("read decision {}", closeout.decision_id))
        })
        .transpose()?
        .flatten();
    let preflight = build_runtime_dispatch_preflight(
        &profile_name,
        &args.closeout_id,
        source_closeout,
        record.as_ref(),
    );
    let preflight_path = profile_dir.join(RUNTIME_DISPATCH_PREFLIGHTS_FILE);
    let preflight_appended = if args.dry_run {
        false
    } else {
        append_runtime_dispatch_preflight(&preflight_path, &preflight)?
    };

    if args.json {
        let output = RuntimePreflightOutput {
            preflight,
            preflight_path: preflight_path.display().to_string(),
            preflight_appended,
            dry_run: args.dry_run,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Runtime dispatch preflight {}: {}",
            preflight.result_status, preflight.preflight_id
        );
        println!("  Closeout: {}", preflight.source_closeout_id);
        println!("  Decision: {}", preflight.decision_id);
        println!("  Appended: {}", preflight_appended);
        println!("  Path:     {}", preflight_path.display());
        if preflight.result_status != "ready_for_runtime_dispatch" {
            println!("  Reason:   {}", preflight.reason);
        }
    }

    Ok(())
}

async fn runtime_dispatch(profile: &str, args: RuntimeDispatchArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let preflights = read_runtime_dispatch_preflights(&profile_dir)?;
    let source_preflight = preflights
        .iter()
        .find(|preflight| preflight.preflight_id == args.preflight_id);
    let receipt_path = profile_dir.join(RUNTIME_DISPATCH_RECEIPTS_FILE);
    if let Some(existing_receipt) =
        find_runtime_dispatch_receipt_for_preflight(&receipt_path, &args.preflight_id)?
    {
        if args.json {
            let output = RuntimeDispatchOutput {
                receipt: existing_receipt,
                receipt_path: receipt_path.display().to_string(),
                receipt_appended: false,
                task_enqueued: false,
                dry_run: args.dry_run,
                task: None,
            };
            println!("{}", serde_json::to_string_pretty(&output)?);
        } else {
            println!(
                "Runtime dispatch already recorded for {}",
                args.preflight_id
            );
            println!("  Receipt: {}", receipt_path.display());
        }
        return Ok(());
    }

    let task_store = OffdeskTaskStore::new(&profile_dir);
    let existing_tasks = task_store.load().unwrap_or_default();
    let receipt = build_runtime_dispatch_receipt(
        &profile_name,
        &args.preflight_id,
        source_preflight,
        &preflights,
        &existing_tasks,
        &args,
    );
    let mut task = None;
    let mut task_enqueued = false;
    let mut receipt_appended = false;

    if !args.dry_run {
        if receipt.result_status == "queued" {
            let queued_task = runtime_dispatch_task(&receipt, &args);
            task_store.enqueue(queued_task.clone())?;
            task = Some(queued_task.operator_view());
            task_enqueued = true;
        }
        receipt_appended = append_runtime_dispatch_receipt(&receipt_path, &receipt)?;
    }

    if args.json {
        let output = RuntimeDispatchOutput {
            receipt: serde_json::to_value(&receipt)?,
            receipt_path: receipt_path.display().to_string(),
            receipt_appended,
            task_enqueued,
            dry_run: args.dry_run,
            task,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!(
            "Runtime dispatch {}: {}",
            receipt.result_status, receipt.receipt_id
        );
        println!("  Preflight: {}", receipt.preflight_id);
        println!("  Task:      {}", receipt.task_id);
        println!("  Appended:  {}", receipt_appended);
        println!("  Receipt:   {}", receipt_path.display());
        if receipt.result_status != "queued" {
            println!("  Reason:    {}", receipt.reason);
        }
    }

    Ok(())
}

fn build_action_envelope_receipt(
    profile: &str,
    envelope: &ActionEnvelopeInput,
    record: Option<&DecisionRecord>,
) -> ActionEnvelopeReceipt {
    let processed_at = Utc::now();
    let current_hash =
        record.map(|record| action_envelope_observed_hash(record, &envelope.action_kind));
    let expected_profile = safe(profile);
    let mut checks = Vec::new();
    let mut blockers = Vec::new();

    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "schema",
        envelope.schema == ACTION_ENVELOPE_SCHEMA,
        format!("schema is {ACTION_ENVELOPE_SCHEMA}"),
        format!(
            "expected {ACTION_ENVELOPE_SCHEMA}, got {}",
            safe(&envelope.schema)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "expected_receipt_schema",
        envelope.expected_receipt_schema == ACTION_ENVELOPE_RECEIPT_SCHEMA,
        format!("receipt schema is {ACTION_ENVELOPE_RECEIPT_SCHEMA}"),
        format!(
            "expected {ACTION_ENVELOPE_RECEIPT_SCHEMA}, got {}",
            safe(&envelope.expected_receipt_schema)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "profile",
        envelope.profile == expected_profile,
        format!("profile matches {expected_profile}"),
        format!(
            "expected profile {expected_profile}, got {}",
            safe(&envelope.profile)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "action_id",
        !envelope.action_id.trim().is_empty(),
        "action id is present".to_string(),
        "action id is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "action_kind",
        !envelope.action_kind.trim().is_empty(),
        "action kind is present".to_string(),
        "action kind is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "target_kind",
        envelope.target_ref.kind == "decision_record.v1",
        "target kind is decision_record.v1".to_string(),
        format!("unexpected target kind {}", safe(&envelope.target_ref.kind)),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "decision_exists",
        record.is_some(),
        format!(
            "decision {} is present",
            safe(&envelope.target_ref.decision_id)
        ),
        format!(
            "decision {} was not found",
            safe(&envelope.target_ref.decision_id)
        ),
    );

    if let Some(record) = record {
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "project_key",
            envelope.project_key == record.project_key,
            format!("project key matches {}", safe(&record.project_key)),
            format!(
                "expected project {}, got {}",
                safe(&record.project_key),
                safe(&envelope.project_key)
            ),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_status",
            envelope.target_ref.status == record.status.as_str(),
            format!("status matches {}", record.status.as_str()),
            format!(
                "expected status {}, got {}",
                record.status.as_str(),
                safe(&envelope.target_ref.status)
            ),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_updated_at",
            envelope.target_ref.updated_at == record.updated_at,
            format!("updated_at matches {}", record.updated_at.to_rfc3339()),
            format!(
                "expected {}, got {}",
                record.updated_at.to_rfc3339(),
                envelope.target_ref.updated_at.to_rfc3339()
            ),
        );
        let expected_command = format!(
            "forager offdesk decision show --json {}",
            operator_safe_text(&record.decision_id)
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "allowed_command",
            envelope.allowed_command == expected_command,
            "allowed command is the read-only decision inspector".to_string(),
            format!(
                "expected {}, got {}",
                expected_command,
                safe(&envelope.allowed_command)
            ),
        );
    } else {
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "project_key",
            false,
            String::new(),
            "cannot verify project key without a decision record".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_status",
            false,
            String::new(),
            "cannot verify target status without a decision record".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_updated_at",
            false,
            String::new(),
            "cannot verify target timestamp without a decision record".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "allowed_command",
            false,
            String::new(),
            "cannot verify allowed command without a decision record".to_string(),
        );
    }

    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "observed_hash",
        current_hash.as_deref() == Some(envelope.observed_hash.as_str()),
        format!(
            "observed hash matches {}",
            current_hash.as_deref().unwrap_or("missing")
        ),
        format!(
            "expected {}, got {}",
            current_hash.as_deref().unwrap_or("missing"),
            safe(&envelope.observed_hash)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "ttl",
        envelope.ttl == "PT10M",
        "ttl is PT10M".to_string(),
        format!("expected PT10M, got {}", safe(&envelope.ttl)),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "issued_at",
        envelope.issued_at.is_some(),
        envelope
            .issued_at
            .map(|value| format!("issued_at is {}", value.to_rfc3339()))
            .unwrap_or_default(),
        "issued_at is required for stale rejection".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "expires_at",
        envelope
            .expires_at
            .map(|expires_at| expires_at >= processed_at)
            .unwrap_or(false),
        envelope
            .expires_at
            .map(|value| format!("expires_at is {}", value.to_rfc3339()))
            .unwrap_or_default(),
        envelope
            .expires_at
            .map(|value| {
                format!(
                    "envelope expired at {}, processed at {}",
                    value.to_rfc3339(),
                    processed_at.to_rfc3339()
                )
            })
            .unwrap_or_else(|| "expires_at is required for stale rejection".to_string()),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "issued_before_expiry",
        envelope
            .issued_at
            .zip(envelope.expires_at)
            .map(|(issued_at, expires_at)| issued_at <= expires_at)
            .unwrap_or(false),
        "issued_at is not after expires_at".to_string(),
        "issued_at must be less than or equal to expires_at".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "idempotency_key",
        !envelope.idempotency_key.trim().is_empty(),
        "idempotency key is present".to_string(),
        "idempotency key is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "nonce",
        !envelope.nonce.trim().is_empty(),
        "nonce is present".to_string(),
        "nonce is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "preview",
        !envelope.preview.trim().is_empty(),
        "preview text is present".to_string(),
        "preview text is required".to_string(),
    );
    let all_forbidden_effects_present = [
        "project_file_mutation",
        "runtime_dispatch",
        "approval_ledger_mutation",
        "accepted_truth_mutation",
        "arbitrary_shell",
    ]
    .iter()
    .all(|expected| {
        envelope
            .forbidden_effects
            .iter()
            .any(|effect| effect == expected)
    });
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "forbidden_effects",
        all_forbidden_effects_present,
        "forbidden effects include mutation and arbitrary shell boundaries".to_string(),
        "forbidden effects must include project/runtime/approval/truth/arbitrary_shell boundaries"
            .to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "confirmation_phrase",
        !envelope.requires_confirmation
            || envelope
                .confirmation_phrase
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty()),
        if envelope.requires_confirmation {
            "confirmation phrase is present".to_string()
        } else {
            "confirmation phrase is not required".to_string()
        },
        "confirmation phrase is required when requires_confirmation=true".to_string(),
    );

    let stale = !blockers.is_empty();
    let result_status = if stale {
        "rejected"
    } else {
        "validated_preview"
    };
    let reason = if stale {
        format!(
            "{} Checks failed: {}",
            safe(&envelope.stale_rejection_reason),
            blockers.join("; ")
        )
    } else {
        "Envelope matches current decision ledger; processor remains read-only.".to_string()
    };
    let receipt_id = action_envelope_receipt_id(
        &envelope.idempotency_key,
        result_status,
        current_hash.as_deref(),
        &reason,
        &envelope.observed_hash,
    );

    ActionEnvelopeReceipt {
        schema: ACTION_ENVELOPE_RECEIPT_SCHEMA,
        receipt_id,
        action_id: safe(&envelope.action_id),
        action_kind: safe(&envelope.action_kind),
        profile: safe(&envelope.profile),
        project_key: safe(&envelope.project_key),
        decision_id: safe(&envelope.target_ref.decision_id),
        processed_at,
        result_status,
        stale,
        reason,
        observed_hash: safe(&envelope.observed_hash),
        current_hash,
        idempotency_key: safe(&envelope.idempotency_key),
        expected_receipt_schema: safe(&envelope.expected_receipt_schema),
        allowed_command: safe(&envelope.allowed_command),
        forbidden_effects: envelope
            .forbidden_effects
            .iter()
            .map(|effect| safe(effect))
            .collect(),
        checks,
    }
}

fn accepted_truth_recovery_current_item<'a>(
    surface: &'a Value,
    envelope: &AcceptedTruthRecoveryEnvelopeInput,
) -> Option<&'a Value> {
    surface
        .get("items")
        .and_then(Value::as_array)?
        .iter()
        .find(|item| {
            item.get("closeout_id")
                .and_then(Value::as_str)
                .is_some_and(|value| value == envelope.target_ref.closeout_id)
                && item
                    .get("review_id")
                    .and_then(Value::as_str)
                    .is_some_and(|value| value == envelope.target_ref.review_id)
                && item
                    .get("receipt_id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    == envelope.target_ref.receipt_id
        })
}

fn build_accepted_truth_recovery_action_receipt(
    profile: &str,
    envelope: &AcceptedTruthRecoveryEnvelopeInput,
    current_item: Option<&Value>,
) -> AcceptedTruthRecoveryActionReceipt {
    let processed_at = Utc::now();
    let current_hash = current_item.map(|item| {
        workstation_surface::accepted_truth_recovery_observed_hash_from_value(
            item,
            &envelope.action_kind,
        )
    });
    let expected_profile = safe(profile);
    let mut checks = Vec::new();
    let mut blockers = Vec::new();

    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "schema",
        envelope.schema == ACCEPTED_TRUTH_RECOVERY_ACTION_ENVELOPE_SCHEMA,
        format!("schema is {ACCEPTED_TRUTH_RECOVERY_ACTION_ENVELOPE_SCHEMA}"),
        format!(
            "expected {ACCEPTED_TRUTH_RECOVERY_ACTION_ENVELOPE_SCHEMA}, got {}",
            safe(&envelope.schema)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "expected_receipt_schema",
        envelope.expected_receipt_schema == ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPT_SCHEMA,
        format!("receipt schema is {ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPT_SCHEMA}"),
        format!(
            "expected {ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPT_SCHEMA}, got {}",
            safe(&envelope.expected_receipt_schema)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "profile",
        envelope.profile == expected_profile,
        format!("profile matches {expected_profile}"),
        format!(
            "expected profile {expected_profile}, got {}",
            safe(&envelope.profile)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "action_id",
        !envelope.action_id.trim().is_empty(),
        "action id is present".to_string(),
        "action id is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "action_kind",
        matches!(
            envelope.action_kind.as_str(),
            "resolve_followup" | "retire_closeout"
        ),
        "action kind is supported".to_string(),
        format!(
            "unsupported accepted-truth recovery action {}",
            safe(&envelope.action_kind)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "target_kind",
        envelope.target_ref.kind == "accepted_truth_recovery.v1",
        "target kind is accepted_truth_recovery.v1".to_string(),
        format!("unexpected target kind {}", safe(&envelope.target_ref.kind)),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "target_exists",
        current_item.is_some(),
        format!(
            "closeout {} review {} is still present",
            safe(&envelope.target_ref.closeout_id),
            safe(&envelope.target_ref.review_id)
        ),
        format!(
            "closeout {} review {} receipt {} is not in current accepted-truth recovery surface",
            safe(&envelope.target_ref.closeout_id),
            safe(&envelope.target_ref.review_id),
            safe(&envelope.target_ref.receipt_id)
        ),
    );

    if let Some(item) = current_item {
        let expected_project = value_text(item, "/project_key").unwrap_or_default();
        let expected_acceptance_status = value_text(item, "/acceptance_status").unwrap_or_default();
        let expected_reviewed_at = value_text(item, "/reviewed_at")
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc));
        let expected_command =
            accepted_truth_recovery_expected_command(item, &envelope.action_kind);

        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "project_key",
            envelope.project_key == expected_project,
            format!("project key matches {}", safe(expected_project)),
            format!(
                "expected project {}, got {}",
                safe(expected_project),
                safe(&envelope.project_key)
            ),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_acceptance_status",
            envelope.target_ref.acceptance_status == expected_acceptance_status,
            format!(
                "acceptance status matches {}",
                safe(expected_acceptance_status)
            ),
            format!(
                "expected acceptance_status {}, got {}",
                safe(expected_acceptance_status),
                safe(&envelope.target_ref.acceptance_status)
            ),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_reviewed_at",
            expected_reviewed_at == Some(envelope.target_ref.reviewed_at),
            envelope.target_ref.reviewed_at.to_rfc3339(),
            expected_reviewed_at
                .map(|value| {
                    format!(
                        "expected reviewed_at {}, got {}",
                        value.to_rfc3339(),
                        envelope.target_ref.reviewed_at.to_rfc3339()
                    )
                })
                .unwrap_or_else(|| "current item reviewed_at is missing or invalid".to_string()),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "allowed_command",
            expected_command
                .as_deref()
                .is_some_and(|expected| expected == envelope.allowed_command),
            "allowed command matches current recovery fallback".to_string(),
            expected_command
                .map(|expected| {
                    format!(
                        "expected {}, got {}",
                        safe(&expected),
                        safe(&envelope.allowed_command)
                    )
                })
                .unwrap_or_else(|| {
                    format!(
                        "no current fallback command is available for {}",
                        safe(&envelope.action_kind)
                    )
                }),
        );
    } else {
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "project_key",
            false,
            String::new(),
            "cannot verify project key without a current recovery item".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_acceptance_status",
            false,
            String::new(),
            "cannot verify acceptance status without a current recovery item".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "target_reviewed_at",
            false,
            String::new(),
            "cannot verify reviewed_at without a current recovery item".to_string(),
        );
        record_action_envelope_check(
            &mut checks,
            &mut blockers,
            "allowed_command",
            false,
            String::new(),
            "cannot verify allowed command without a current recovery item".to_string(),
        );
    }

    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "observed_hash",
        current_hash.as_deref() == Some(envelope.observed_hash.as_str()),
        format!(
            "observed hash matches {}",
            current_hash.as_deref().unwrap_or("missing")
        ),
        format!(
            "expected {}, got {}",
            current_hash.as_deref().unwrap_or("missing"),
            safe(&envelope.observed_hash)
        ),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "ttl",
        envelope.ttl == "PT10M",
        "ttl is PT10M".to_string(),
        format!("expected PT10M, got {}", safe(&envelope.ttl)),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "issued_at",
        envelope.issued_at.is_some(),
        envelope
            .issued_at
            .map(|value| format!("issued_at is {}", value.to_rfc3339()))
            .unwrap_or_default(),
        "issued_at is required for stale rejection".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "expires_at",
        envelope
            .expires_at
            .map(|expires_at| expires_at >= processed_at)
            .unwrap_or(false),
        envelope
            .expires_at
            .map(|value| format!("expires_at is {}", value.to_rfc3339()))
            .unwrap_or_default(),
        envelope
            .expires_at
            .map(|value| {
                format!(
                    "envelope expired at {}, processed at {}",
                    value.to_rfc3339(),
                    processed_at.to_rfc3339()
                )
            })
            .unwrap_or_else(|| "expires_at is required for stale rejection".to_string()),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "issued_before_expiry",
        envelope
            .issued_at
            .zip(envelope.expires_at)
            .map(|(issued_at, expires_at)| issued_at <= expires_at)
            .unwrap_or(false),
        "issued_at is not after expires_at".to_string(),
        "issued_at must be less than or equal to expires_at".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "idempotency_key",
        !envelope.idempotency_key.trim().is_empty(),
        "idempotency key is present".to_string(),
        "idempotency key is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "nonce",
        !envelope.nonce.trim().is_empty(),
        "nonce is present".to_string(),
        "nonce is required".to_string(),
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "preview",
        !envelope.preview.trim().is_empty(),
        "preview text is present".to_string(),
        "preview text is required".to_string(),
    );
    let all_forbidden_effects_present = [
        "project_file_mutation",
        "runtime_dispatch",
        "approval_ledger_mutation",
        "accepted_truth_mutation",
        "arbitrary_shell",
        "wiki_promotion",
        "file_movement",
    ]
    .iter()
    .all(|expected| {
        envelope
            .forbidden_effects
            .iter()
            .any(|effect| effect == expected)
    });
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "forbidden_effects",
        all_forbidden_effects_present,
        "forbidden effects include recovery mutation boundaries".to_string(),
        "forbidden effects must include project/runtime/approval/truth/shell/wiki/file boundaries"
            .to_string(),
    );
    let expected_confirmation = format!(
        "confirm {} {}",
        envelope.action_kind,
        action_envelope_slug(&envelope.target_ref.closeout_id)
    );
    record_action_envelope_check(
        &mut checks,
        &mut blockers,
        "confirmation_phrase",
        envelope.requires_confirmation && envelope.confirmation_phrase == expected_confirmation,
        "confirmation phrase matches current recovery action".to_string(),
        format!(
            "expected confirmation phrase {}, got {}",
            safe(&expected_confirmation),
            safe(&envelope.confirmation_phrase)
        ),
    );

    let stale = !blockers.is_empty();
    let result_status = if stale {
        "rejected"
    } else {
        "validated_preview"
    };
    let reason = if stale {
        format!(
            "{} Checks failed: {}",
            safe(&envelope.stale_rejection_reason),
            blockers.join("; ")
        )
    } else {
        "Envelope matches current accepted-truth recovery surface; processor remains read-only."
            .to_string()
    };
    let receipt_id = accepted_truth_recovery_action_receipt_id(
        &envelope.idempotency_key,
        result_status,
        current_hash.as_deref(),
        &reason,
        &envelope.observed_hash,
    );

    AcceptedTruthRecoveryActionReceipt {
        schema: ACCEPTED_TRUTH_RECOVERY_ACTION_RECEIPT_SCHEMA,
        receipt_id,
        action_id: safe(&envelope.action_id),
        action_kind: safe(&envelope.action_kind),
        profile: safe(&envelope.profile),
        project_key: safe(&envelope.project_key),
        closeout_id: safe(&envelope.target_ref.closeout_id),
        review_id: safe(&envelope.target_ref.review_id),
        receipt_source_id: safe(&envelope.target_ref.receipt_id),
        processed_at,
        result_status,
        stale,
        reason,
        observed_hash: safe(&envelope.observed_hash),
        current_hash,
        idempotency_key: safe(&envelope.idempotency_key),
        expected_receipt_schema: safe(&envelope.expected_receipt_schema),
        allowed_command: safe(&envelope.allowed_command),
        forbidden_effects: envelope
            .forbidden_effects
            .iter()
            .map(|effect| safe(effect))
            .collect(),
        checks,
    }
}

fn accepted_truth_recovery_expected_command(item: &Value, action_kind: &str) -> Option<String> {
    let field = match action_kind {
        "resolve_followup" => "resolve_command",
        "retire_closeout" => "retire_command",
        _ => return None,
    };
    item.get(field)
        .and_then(Value::as_str)
        .map(safe)
        .filter(|value| !value.trim().is_empty())
}

fn build_action_execution_preflight(
    profile: &str,
    requested_receipt_id: &str,
    source_receipt: Option<&StoredActionEnvelopeReceipt>,
    receipts: &[StoredActionEnvelopeReceipt],
    record: Option<&DecisionRecord>,
) -> ActionExecutionPreflight {
    let processed_at = Utc::now();
    let mut checks = Vec::new();
    let mut blockers = Vec::new();
    let expected_profile = safe(profile);
    let current_hash = source_receipt.and_then(|receipt| {
        record.map(|record| action_envelope_observed_hash(record, &receipt.action_kind))
    });
    let latest_matching_receipt_id = source_receipt.and_then(|receipt| {
        latest_action_envelope_receipt(receipt, receipts).map(|latest| latest.receipt_id.as_str())
    });

    record_action_preflight_check(
        &mut checks,
        &mut blockers,
        "source_receipt_exists",
        source_receipt.is_some(),
        format!("source receipt {} is present", safe(requested_receipt_id)),
        format!(
            "source receipt {} was not found",
            safe(requested_receipt_id)
        ),
    );

    if let Some(receipt) = source_receipt {
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "source_schema",
            receipt.schema == ACTION_ENVELOPE_RECEIPT_SCHEMA,
            format!("source schema is {ACTION_ENVELOPE_RECEIPT_SCHEMA}"),
            format!(
                "expected {ACTION_ENVELOPE_RECEIPT_SCHEMA}, got {}",
                safe(&receipt.schema)
            ),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "source_result_status",
            receipt.result_status == "validated_preview",
            "source receipt is validated_preview".to_string(),
            format!(
                "source receipt status must be validated_preview, got {}",
                safe(&receipt.result_status)
            ),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "source_not_stale",
            !receipt.stale,
            "source receipt is non-stale".to_string(),
            "source receipt is stale or rejected".to_string(),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "latest_receipt",
            latest_matching_receipt_id == Some(receipt.receipt_id.as_str()),
            "source receipt is the latest receipt for this action".to_string(),
            format!(
                "latest receipt for this action is {}",
                latest_matching_receipt_id.unwrap_or("missing")
            ),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "profile",
            receipt.profile == expected_profile,
            format!("profile matches {expected_profile}"),
            format!(
                "expected profile {expected_profile}, got {}",
                safe(&receipt.profile)
            ),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "action_id",
            !receipt.action_id.trim().is_empty(),
            "action id is present".to_string(),
            "action id is required".to_string(),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "action_kind",
            !receipt.action_kind.trim().is_empty(),
            "action kind is present".to_string(),
            "action kind is required".to_string(),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "idempotency_key",
            !receipt.idempotency_key.trim().is_empty(),
            "idempotency key is present".to_string(),
            "idempotency key is required".to_string(),
        );
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "decision_exists",
            record.is_some(),
            format!("decision {} is present", safe(&receipt.decision_id)),
            format!("decision {} was not found", safe(&receipt.decision_id)),
        );

        if let Some(record) = record {
            record_action_preflight_check(
                &mut checks,
                &mut blockers,
                "project_key",
                receipt.project_key == record.project_key,
                format!("project key matches {}", safe(&record.project_key)),
                format!(
                    "expected project {}, got {}",
                    safe(&record.project_key),
                    safe(&receipt.project_key)
                ),
            );
            let expected_command = format!(
                "forager offdesk decision show --json {}",
                operator_safe_text(&record.decision_id)
            );
            record_action_preflight_check(
                &mut checks,
                &mut blockers,
                "allowed_command",
                receipt.allowed_command == expected_command,
                "source receipt allowed command is the read-only decision inspector".to_string(),
                format!(
                    "expected {}, got {}",
                    expected_command,
                    safe(&receipt.allowed_command)
                ),
            );
        }

        let expected_hash = current_hash.as_deref().unwrap_or("missing");
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "current_hash",
            current_hash.as_deref() == receipt.current_hash.as_deref()
                && current_hash.as_deref() == Some(receipt.observed_hash.as_str()),
            format!("current hash still matches {expected_hash}"),
            format!(
                "current {}, receipt current {}, observed {}",
                expected_hash,
                receipt.current_hash.as_deref().unwrap_or("missing"),
                safe(&receipt.observed_hash)
            ),
        );
        let all_forbidden_effects_present = [
            "project_file_mutation",
            "runtime_dispatch",
            "approval_ledger_mutation",
            "accepted_truth_mutation",
            "arbitrary_shell",
        ]
        .iter()
        .all(|expected| {
            receipt
                .forbidden_effects
                .iter()
                .any(|effect| effect == expected)
        });
        record_action_preflight_check(
            &mut checks,
            &mut blockers,
            "forbidden_effects",
            all_forbidden_effects_present,
            "forbidden effects include mutation and arbitrary shell boundaries".to_string(),
            "forbidden effects must include project/runtime/approval/truth/arbitrary_shell boundaries"
                .to_string(),
        );
    } else {
        for name in [
            "source_schema",
            "source_result_status",
            "source_not_stale",
            "latest_receipt",
            "profile",
            "action_id",
            "action_kind",
            "idempotency_key",
            "decision_exists",
            "project_key",
            "allowed_command",
            "current_hash",
            "forbidden_effects",
        ] {
            record_action_preflight_check(
                &mut checks,
                &mut blockers,
                name,
                false,
                String::new(),
                "cannot verify without a source receipt".to_string(),
            );
        }
    }

    let blocked = !blockers.is_empty();
    let result_status = if blocked {
        "blocked"
    } else {
        "ready_for_executor"
    };
    let reason = if blocked {
        format!(
            "Action execution preflight blocked. Checks failed: {}",
            blockers.join("; ")
        )
    } else {
        "Validated receipt is current and non-stale; a separate action-specific executor is still required."
            .to_string()
    };
    let source_receipt_id = source_receipt
        .map(|receipt| safe(&receipt.receipt_id))
        .unwrap_or_else(|| safe(requested_receipt_id));
    let action_id = source_receipt
        .map(|receipt| safe(&receipt.action_id))
        .unwrap_or_default();
    let action_kind = source_receipt
        .map(|receipt| safe(&receipt.action_kind))
        .unwrap_or_else(|| "unknown".to_string());
    let project_key = source_receipt
        .map(|receipt| safe(&receipt.project_key))
        .unwrap_or_default();
    let decision_id = source_receipt
        .map(|receipt| safe(&receipt.decision_id))
        .unwrap_or_default();
    let idempotency_key = source_receipt
        .map(|receipt| safe(&receipt.idempotency_key))
        .unwrap_or_else(|| format!("missing:{}", safe(requested_receipt_id)));
    let receipt_current_hash = source_receipt.and_then(|receipt| {
        receipt
            .current_hash
            .as_ref()
            .map(|value| operator_safe_text(value))
    });
    let preflight_id = action_execution_preflight_id(
        &source_receipt_id,
        result_status,
        current_hash.as_deref(),
        &reason,
    );

    ActionExecutionPreflight {
        schema: ACTION_EXECUTION_PREFLIGHT_SCHEMA,
        preflight_id,
        source_receipt_id,
        action_id,
        action_kind,
        profile: expected_profile,
        project_key,
        decision_id,
        processed_at,
        result_status,
        executor_required: true,
        mutation_allowed_by_this_command: false,
        reason,
        current_hash,
        receipt_current_hash,
        idempotency_key,
        next_step: "Run an action-specific executor that requires this ready preflight id; do not execute from the preview envelope alone.".to_string(),
        checks,
    }
}

fn build_decision_action_execution(
    profile: &str,
    requested_preflight_id: &str,
    source_preflight: Option<&StoredActionExecutionPreflight>,
    record: Option<&DecisionRecord>,
    args: &ActionDecisionArgs,
) -> DecisionActionExecution {
    let executed_at = Utc::now();
    let mut checks = Vec::new();
    let mut blockers = Vec::new();
    let expected_profile = safe(profile);
    let decision = source_preflight
        .map(|preflight| normalize_action_decision_choice(&preflight.action_kind))
        .unwrap_or_else(|| "unknown".to_string());
    let current_hash = source_preflight.and_then(|preflight| {
        record.map(|record| action_envelope_observed_hash(record, &preflight.action_kind))
    });

    record_action_decision_check(
        &mut checks,
        &mut blockers,
        "source_preflight_exists",
        source_preflight.is_some(),
        format!(
            "source preflight {} is present",
            safe(requested_preflight_id)
        ),
        format!(
            "source preflight {} was not found",
            safe(requested_preflight_id)
        ),
    );

    if let Some(preflight) = source_preflight {
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "source_schema",
            preflight.schema == ACTION_EXECUTION_PREFLIGHT_SCHEMA,
            format!("source schema is {ACTION_EXECUTION_PREFLIGHT_SCHEMA}"),
            format!(
                "expected {ACTION_EXECUTION_PREFLIGHT_SCHEMA}, got {}",
                safe(&preflight.schema)
            ),
        );
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "source_result_status",
            preflight.result_status == "ready_for_executor",
            "source preflight is ready_for_executor".to_string(),
            format!(
                "source preflight status must be ready_for_executor, got {}",
                safe(&preflight.result_status)
            ),
        );
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "profile",
            preflight.profile == expected_profile,
            format!("profile matches {expected_profile}"),
            format!(
                "expected profile {expected_profile}, got {}",
                safe(&preflight.profile)
            ),
        );
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "supported_action",
            supported_decision_action(&decision),
            format!("decision action `{decision}` is supported"),
            format!(
                "unsupported decision action `{}`",
                safe(&preflight.action_kind)
            ),
        );
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "note",
            !decision_action_requires_note(&decision) || !args.note.trim().is_empty(),
            if decision_action_requires_note(&decision) {
                "required note is present".to_string()
            } else {
                "note is not required for this action".to_string()
            },
            format!("decision `{decision}` requires --note with bounded direction or blocker"),
        );
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "decision_exists",
            record.is_some(),
            format!("decision {} is present", safe(&preflight.decision_id)),
            format!("decision {} was not found", safe(&preflight.decision_id)),
        );

        if let Some(record) = record {
            record_action_decision_check(
                &mut checks,
                &mut blockers,
                "project_key",
                preflight.project_key == record.project_key,
                format!("project key matches {}", safe(&record.project_key)),
                format!(
                    "expected project {}, got {}",
                    safe(&record.project_key),
                    safe(&preflight.project_key)
                ),
            );
            record_action_decision_check(
                &mut checks,
                &mut blockers,
                "decision_status",
                decision_action_status_is_mutable(record.status),
                format!(
                    "decision status {} can receive an action",
                    record.status.as_str()
                ),
                format!(
                    "decision status {} cannot receive this action",
                    record.status.as_str()
                ),
            );
        }
        record_action_decision_check(
            &mut checks,
            &mut blockers,
            "current_hash",
            current_hash.as_deref() == preflight.current_hash.as_deref(),
            format!(
                "current hash still matches {}",
                current_hash.as_deref().unwrap_or("missing")
            ),
            format!(
                "current {}, preflight {}",
                current_hash.as_deref().unwrap_or("missing"),
                preflight.current_hash.as_deref().unwrap_or("missing")
            ),
        );
    } else {
        for name in [
            "source_schema",
            "source_result_status",
            "profile",
            "supported_action",
            "note",
            "decision_exists",
            "project_key",
            "decision_status",
            "current_hash",
        ] {
            record_action_decision_check(
                &mut checks,
                &mut blockers,
                name,
                false,
                String::new(),
                "cannot verify without a source preflight".to_string(),
            );
        }
    }

    let blocked = !blockers.is_empty();
    let result_status = if blocked { "blocked" } else { "applied" };
    let reason = if blocked {
        format!(
            "Decision action blocked. Checks failed: {}",
            blockers.join("; ")
        )
    } else {
        "Decision action applied to the canonical decision ledger as an execution handoff; no runtime work was dispatched.".to_string()
    };
    let preflight_id = source_preflight
        .map(|preflight| safe(&preflight.preflight_id))
        .unwrap_or_else(|| safe(requested_preflight_id));
    let source_receipt_id = source_preflight
        .map(|preflight| safe(&preflight.source_receipt_id))
        .unwrap_or_default();
    let action_id = source_preflight
        .map(|preflight| safe(&preflight.action_id))
        .unwrap_or_default();
    let action_kind = source_preflight
        .map(|preflight| safe(&preflight.action_kind))
        .unwrap_or_else(|| "unknown".to_string());
    let project_key = source_preflight
        .map(|preflight| safe(&preflight.project_key))
        .unwrap_or_default();
    let decision_id = source_preflight
        .map(|preflight| safe(&preflight.decision_id))
        .unwrap_or_default();
    let idempotency_key = source_preflight
        .map(|preflight| safe(&preflight.idempotency_key))
        .unwrap_or_else(|| format!("missing:{}", safe(requested_preflight_id)));
    let execution_id =
        decision_action_execution_id(&preflight_id, result_status, &decision, &reason);
    let handoff_id = if result_status == "applied" && decision_creates_handoff(&decision) {
        Some(decision_handoff_id(&execution_id))
    } else {
        None
    };

    DecisionActionExecution {
        schema: DECISION_ACTION_EXECUTION_SCHEMA,
        execution_id,
        preflight_id,
        source_receipt_id,
        action_id,
        action_kind,
        decision,
        profile: expected_profile,
        project_key,
        decision_id,
        executed_at,
        result_status,
        mutation_allowed_by_this_command: result_status == "applied",
        decision_appended: result_status == "applied",
        reason,
        handoff_id,
        current_hash,
        idempotency_key,
        checks,
    }
}

fn build_decision_action_closeout(
    profile: &str,
    requested_execution_id: &str,
    source_execution: Option<&StoredDecisionActionExecution>,
    record: Option<&DecisionRecord>,
    args: &ActionCloseoutArgs,
) -> DecisionActionCloseout {
    let recorded_at = Utc::now();
    let mut checks = Vec::new();
    let mut blockers = Vec::new();
    let expected_profile = safe(profile);
    let handoff_id = source_execution.and_then(|execution| execution.handoff_id.clone());

    record_action_closeout_check(
        &mut checks,
        &mut blockers,
        "source_execution_exists",
        source_execution.is_some(),
        format!(
            "source execution {} is present",
            safe(requested_execution_id)
        ),
        format!(
            "source execution {} was not found",
            safe(requested_execution_id)
        ),
    );

    if let Some(execution) = source_execution {
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "source_schema",
            execution.schema == DECISION_ACTION_EXECUTION_SCHEMA,
            format!("source schema is {DECISION_ACTION_EXECUTION_SCHEMA}"),
            format!(
                "expected {DECISION_ACTION_EXECUTION_SCHEMA}, got {}",
                safe(&execution.schema)
            ),
        );
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "source_result_status",
            execution.result_status == "applied",
            "source execution is applied".to_string(),
            format!(
                "source execution status must be applied, got {}",
                safe(&execution.result_status)
            ),
        );
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "source_mutation_scope",
            execution.mutation_allowed_by_this_command && execution.decision_appended,
            "source execution appended a decision handoff".to_string(),
            "source execution did not append a decision handoff".to_string(),
        );
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "profile",
            execution.profile == expected_profile,
            format!("profile matches {expected_profile}"),
            format!(
                "expected profile {expected_profile}, got {}",
                safe(&execution.profile)
            ),
        );
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "handoff_id",
            execution
                .handoff_id
                .as_deref()
                .is_some_and(|value| !value.trim().is_empty()),
            "source execution has a handoff id".to_string(),
            "source execution has no handoff id to receipt".to_string(),
        );
        record_action_closeout_check(
            &mut checks,
            &mut blockers,
            "decision_exists",
            record.is_some(),
            format!("decision {} is present", safe(&execution.decision_id)),
            format!("decision {} was not found", safe(&execution.decision_id)),
        );

        if let Some(record) = record {
            let matching_handoff = record
                .execution_handoff
                .as_ref()
                .map(|handoff| {
                    handoff.decision_id == execution.decision_id
                        && handoff.approved_direction == execution.decision
                        && execution
                            .handoff_id
                            .as_deref()
                            .is_some_and(|id| id == handoff.handoff_id)
                })
                .unwrap_or(false);
            record_action_closeout_check(
                &mut checks,
                &mut blockers,
                "project_key",
                execution.project_key == record.project_key,
                format!("project key matches {}", safe(&record.project_key)),
                format!(
                    "expected project {}, got {}",
                    safe(&record.project_key),
                    safe(&execution.project_key)
                ),
            );
            record_action_closeout_check(
                &mut checks,
                &mut blockers,
                "decision_status",
                record.status == DecisionStatus::HandoffReady,
                "decision is handoff_ready".to_string(),
                format!(
                    "decision status must be handoff_ready, got {}",
                    record.status.as_str()
                ),
            );
            record_action_closeout_check(
                &mut checks,
                &mut blockers,
                "execution_handoff",
                matching_handoff,
                "decision handoff matches the source execution".to_string(),
                "decision handoff is missing or does not match the source execution".to_string(),
            );
            record_action_closeout_check(
                &mut checks,
                &mut blockers,
                "decision_receipt_absent",
                record.decision_receipt.is_none(),
                "decision has no existing receipt".to_string(),
                "decision already has a receipt".to_string(),
            );
        }
    } else {
        for name in [
            "source_schema",
            "source_result_status",
            "source_mutation_scope",
            "profile",
            "handoff_id",
            "decision_exists",
            "project_key",
            "decision_status",
            "execution_handoff",
            "decision_receipt_absent",
        ] {
            record_action_closeout_check(
                &mut checks,
                &mut blockers,
                name,
                false,
                String::new(),
                "cannot verify without a source execution".to_string(),
            );
        }
    }

    let blocked = !blockers.is_empty();
    let result_status = if blocked { "blocked" } else { "receipted" };
    let receipt_result_status = safe(args.result_status.trim());
    let receipt_result_status = if receipt_result_status.trim().is_empty() {
        "closed".to_string()
    } else {
        receipt_result_status
    };
    let reason = if blocked {
        format!(
            "Decision action closeout blocked. Checks failed: {}",
            blockers.join("; ")
        )
    } else {
        "Decision action handoff was closed with a canonical decision receipt; no runtime work was dispatched.".to_string()
    };
    let execution_id = source_execution
        .map(|execution| safe(&execution.execution_id))
        .unwrap_or_else(|| safe(requested_execution_id));
    let closeout_id = decision_action_closeout_id(
        &execution_id,
        result_status,
        &receipt_result_status,
        &reason,
    );
    let receipt_id = if result_status == "receipted" {
        Some(decision_action_receipt_id(
            &execution_id,
            &receipt_result_status,
        ))
    } else {
        None
    };
    let evidence_summary = args
        .evidence_summary
        .iter()
        .map(|line| safe(line.trim()))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();
    let remaining_review = args
        .remaining_review
        .iter()
        .map(|line| safe(line.trim()))
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>();

    DecisionActionCloseout {
        schema: DECISION_ACTION_CLOSEOUT_SCHEMA,
        closeout_id,
        execution_id,
        preflight_id: source_execution
            .map(|execution| safe(&execution.preflight_id))
            .unwrap_or_default(),
        action_kind: source_execution
            .map(|execution| safe(&execution.action_kind))
            .unwrap_or_default(),
        decision: source_execution
            .map(|execution| safe(&execution.decision))
            .unwrap_or_default(),
        profile: expected_profile,
        project_key: source_execution
            .map(|execution| safe(&execution.project_key))
            .unwrap_or_default(),
        decision_id: source_execution
            .map(|execution| safe(&execution.decision_id))
            .unwrap_or_default(),
        recorded_at,
        result_status,
        receipt_result_status,
        mutation_allowed_by_this_command: result_status == "receipted",
        decision_appended: result_status == "receipted",
        reason,
        receipt_id,
        handoff_id,
        evidence_summary,
        remaining_review,
        checks,
    }
}

fn build_runtime_dispatch_preflight(
    profile: &str,
    requested_closeout_id: &str,
    source_closeout: Option<&StoredDecisionActionCloseout>,
    record: Option<&DecisionRecord>,
) -> RuntimeDispatchPreflight {
    let processed_at = Utc::now();
    let mut checks = Vec::new();
    let mut blockers = Vec::new();
    let expected_profile = safe(profile);

    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "source_closeout_exists",
        source_closeout.is_some(),
        format!("source closeout {} is present", safe(requested_closeout_id)),
        format!(
            "source closeout {} was not found",
            safe(requested_closeout_id)
        ),
    );

    if let Some(closeout) = source_closeout {
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "source_schema",
            closeout.schema == DECISION_ACTION_CLOSEOUT_SCHEMA,
            format!("source schema is {DECISION_ACTION_CLOSEOUT_SCHEMA}"),
            format!(
                "expected {DECISION_ACTION_CLOSEOUT_SCHEMA}, got {}",
                safe(&closeout.schema)
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "source_result_status",
            closeout.result_status == "receipted",
            "source closeout is receipted".to_string(),
            format!(
                "source closeout status must be receipted, got {}",
                safe(&closeout.result_status)
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "source_mutation_scope",
            closeout.mutation_allowed_by_this_command && closeout.decision_appended,
            "source closeout appended a canonical decision receipt".to_string(),
            "source closeout did not append a canonical decision receipt".to_string(),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "profile",
            closeout.profile == expected_profile,
            format!("profile matches {expected_profile}"),
            format!(
                "expected profile {expected_profile}, got {}",
                safe(&closeout.profile)
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "decision_exists",
            record.is_some(),
            format!("decision {} is present", safe(&closeout.decision_id)),
            format!("decision {} was not found", safe(&closeout.decision_id)),
        );

        if let Some(record) = record {
            let receipt = record.decision_receipt.as_ref();
            let receipt_matches = receipt
                .map(|receipt| {
                    closeout
                        .receipt_id
                        .as_deref()
                        .is_some_and(|id| id == receipt.receipt_id)
                        && closeout
                            .handoff_id
                            .as_deref()
                            .zip(receipt.applied_handoff_id.as_deref())
                            .is_some_and(|(closeout_handoff, receipt_handoff)| {
                                closeout_handoff == receipt_handoff
                            })
                        && receipt.final_decision == closeout.decision
                })
                .unwrap_or(false);
            record_runtime_dispatch_check(
                &mut checks,
                &mut blockers,
                "project_key",
                closeout.project_key == record.project_key,
                format!("project key matches {}", safe(&record.project_key)),
                format!(
                    "expected project {}, got {}",
                    safe(&record.project_key),
                    safe(&closeout.project_key)
                ),
            );
            record_runtime_dispatch_check(
                &mut checks,
                &mut blockers,
                "decision_status",
                record.status == DecisionStatus::Receipted,
                "decision is receipted".to_string(),
                format!(
                    "decision status must be receipted, got {}",
                    record.status.as_str()
                ),
            );
            record_runtime_dispatch_check(
                &mut checks,
                &mut blockers,
                "decision_receipt",
                receipt_matches,
                "decision receipt matches the source closeout".to_string(),
                "decision receipt is missing or does not match the source closeout".to_string(),
            );
        }
    } else {
        for name in [
            "source_schema",
            "source_result_status",
            "source_mutation_scope",
            "profile",
            "decision_exists",
            "project_key",
            "decision_status",
            "decision_receipt",
        ] {
            record_runtime_dispatch_check(
                &mut checks,
                &mut blockers,
                name,
                false,
                String::new(),
                "cannot verify without a source closeout".to_string(),
            );
        }
    }

    let blocked = !blockers.is_empty();
    let result_status = if blocked {
        "blocked"
    } else {
        "ready_for_runtime_dispatch"
    };
    let reason = if blocked {
        format!(
            "Runtime dispatch preflight blocked. Checks failed: {}",
            blockers.join("; ")
        )
    } else {
        "Receipted decision action closeout is current; a separate runtime dispatch receipt is still required.".to_string()
    };
    let source_closeout_id = source_closeout
        .map(|closeout| safe(&closeout.closeout_id))
        .unwrap_or_else(|| safe(requested_closeout_id));
    let source_execution_id = source_closeout
        .map(|closeout| safe(&closeout.execution_id))
        .unwrap_or_default();
    let receipt_id = record
        .and_then(|record| record.decision_receipt.as_ref())
        .map(|receipt| safe(&receipt.receipt_id));
    let source_task_id = record
        .map(|record| safe(&record.task_id))
        .unwrap_or_default();
    let request_id = record
        .map(|record| safe(&record.request_id))
        .unwrap_or_default();
    let preflight_id = runtime_dispatch_preflight_id(
        &source_closeout_id,
        result_status,
        receipt_id.as_deref(),
        &reason,
    );

    RuntimeDispatchPreflight {
        schema: RUNTIME_DISPATCH_PREFLIGHT_SCHEMA,
        preflight_id,
        source_closeout_id,
        source_execution_id,
        source_action_preflight_id: source_closeout
            .map(|closeout| safe(&closeout.preflight_id))
            .unwrap_or_default(),
        action_kind: source_closeout
            .map(|closeout| safe(&closeout.action_kind))
            .unwrap_or_default(),
        decision: source_closeout
            .map(|closeout| safe(&closeout.decision))
            .unwrap_or_default(),
        profile: expected_profile,
        project_key: source_closeout
            .map(|closeout| safe(&closeout.project_key))
            .unwrap_or_default(),
        decision_id: source_closeout
            .map(|closeout| safe(&closeout.decision_id))
            .unwrap_or_default(),
        request_id,
        source_task_id,
        processed_at,
        result_status,
        mutation_allowed_by_this_command: false,
        reason,
        receipt_id,
        handoff_id: source_closeout.and_then(|closeout| closeout.handoff_id.clone()),
        next_step: "Run `forager ondesk runtime-dispatch --preflight-id <ID> --runner <RUNNER> --cmd <CMD> --json`; runtime launch still happens later through `forager offdesk tick`."
            .to_string(),
        checks,
    }
}

fn build_runtime_dispatch_receipt(
    profile: &str,
    requested_preflight_id: &str,
    source_preflight: Option<&StoredRuntimeDispatchPreflight>,
    preflights: &[StoredRuntimeDispatchPreflight],
    existing_tasks: &[OffdeskTask],
    args: &RuntimeDispatchArgs,
) -> RuntimeDispatchReceipt {
    let recorded_at = Utc::now();
    let mut checks = Vec::new();
    let mut blockers = Vec::new();
    let expected_profile = safe(profile);
    let runner = BackgroundRunnerKind::from_str(&args.runner);
    let workdir = args
        .workdir
        .clone()
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let task_id = runtime_dispatch_task_id(requested_preflight_id, args.task_id.as_deref());
    let latest_matching_preflight_id = source_preflight.and_then(|preflight| {
        latest_runtime_dispatch_preflight(preflight, preflights)
            .map(|latest| latest.preflight_id.as_str())
    });

    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "source_preflight_exists",
        source_preflight.is_some(),
        format!(
            "source preflight {} is present",
            safe(requested_preflight_id)
        ),
        format!(
            "source preflight {} was not found",
            safe(requested_preflight_id)
        ),
    );

    if let Some(preflight) = source_preflight {
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "source_schema",
            preflight.schema == RUNTIME_DISPATCH_PREFLIGHT_SCHEMA,
            format!("source schema is {RUNTIME_DISPATCH_PREFLIGHT_SCHEMA}"),
            format!(
                "expected {RUNTIME_DISPATCH_PREFLIGHT_SCHEMA}, got {}",
                safe(&preflight.schema)
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "source_result_status",
            preflight.result_status == "ready_for_runtime_dispatch",
            "source preflight is ready_for_runtime_dispatch".to_string(),
            format!(
                "source preflight status must be ready_for_runtime_dispatch, got {}",
                safe(&preflight.result_status)
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "latest_preflight",
            latest_matching_preflight_id == Some(preflight.preflight_id.as_str()),
            "source preflight is the latest runtime preflight for this closeout".to_string(),
            format!(
                "latest preflight for this closeout is {}",
                latest_matching_preflight_id.unwrap_or("missing")
            ),
        );
        record_runtime_dispatch_check(
            &mut checks,
            &mut blockers,
            "profile",
            preflight.profile == expected_profile,
            format!("profile matches {expected_profile}"),
            format!(
                "expected profile {expected_profile}, got {}",
                safe(&preflight.profile)
            ),
        );
    } else {
        for name in [
            "source_schema",
            "source_result_status",
            "latest_preflight",
            "profile",
        ] {
            record_runtime_dispatch_check(
                &mut checks,
                &mut blockers,
                name,
                false,
                String::new(),
                "cannot verify without a source preflight".to_string(),
            );
        }
    }

    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "capability_id",
        args.capability_id.trim() == "dispatch.runtime",
        "capability is dispatch.runtime".to_string(),
        format!(
            "runtime dispatch currently only accepts dispatch.runtime, got {}",
            safe(&args.capability_id)
        ),
    );
    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "runner_kind",
        runner.is_ok(),
        "runner kind is supported".to_string(),
        runner
            .as_ref()
            .err()
            .cloned()
            .unwrap_or_else(|| "unknown runner kind".to_string()),
    );
    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "command",
        !args.command.trim().is_empty(),
        "command is present".to_string(),
        "command is required".to_string(),
    );
    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "workdir",
        workdir.is_dir(),
        format!(
            "workdir exists at {}",
            safe(workdir.to_string_lossy().as_ref())
        ),
        format!(
            "workdir must be an existing directory, got {}",
            safe(workdir.to_string_lossy().as_ref())
        ),
    );
    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "task_id",
        !task_id.trim().is_empty(),
        "task id is present".to_string(),
        "task id is required".to_string(),
    );
    record_runtime_dispatch_check(
        &mut checks,
        &mut blockers,
        "task_id_available",
        !existing_tasks.iter().any(|task| task.task_id == task_id),
        "task id is not already present in offdesk_tasks.json".to_string(),
        format!("task id {} already exists", safe(&task_id)),
    );

    let blocked = !blockers.is_empty();
    let result_status = if blocked { "blocked" } else { "queued" };
    let reason = if blocked {
        format!(
            "Runtime dispatch blocked. Checks failed: {}",
            blockers.join("; ")
        )
    } else {
        "Runtime work was queued as an Offdesk task; no process was launched by this command."
            .to_string()
    };
    let preflight_id = source_preflight
        .map(|preflight| safe(&preflight.preflight_id))
        .unwrap_or_else(|| safe(requested_preflight_id));
    let receipt_id = runtime_dispatch_receipt_id(
        &preflight_id,
        result_status,
        &task_id,
        &args.command,
        &reason,
    );

    RuntimeDispatchReceipt {
        schema: RUNTIME_DISPATCH_RECEIPT_SCHEMA,
        receipt_id,
        preflight_id,
        source_closeout_id: source_preflight
            .map(|preflight| safe(&preflight.source_closeout_id))
            .unwrap_or_default(),
        source_execution_id: source_preflight
            .map(|preflight| safe(&preflight.source_execution_id))
            .unwrap_or_default(),
        profile: expected_profile,
        project_key: source_preflight
            .map(|preflight| safe(&preflight.project_key))
            .unwrap_or_default(),
        decision_id: source_preflight
            .map(|preflight| safe(&preflight.decision_id))
            .unwrap_or_default(),
        request_id: source_preflight
            .map(|preflight| safe(&preflight.request_id))
            .unwrap_or_default(),
        task_id,
        capability_id: safe(&args.capability_id),
        runner_kind: runner.unwrap_or(BackgroundRunnerKind::LocalBackground),
        command: safe(&args.command),
        workdir: safe(workdir.to_string_lossy().as_ref()),
        recorded_at,
        result_status,
        mutation_allowed_by_this_command: result_status == "queued",
        task_enqueued: result_status == "queued",
        reason,
        next_step: if result_status == "queued" {
            "Run `forager offdesk tick --task-id <TASK_ID>` when ready to pass through the existing scheduler gate and launch path.".to_string()
        } else {
            "Fix failed checks, then create a fresh runtime preflight before retrying dispatch."
                .to_string()
        },
        provider_id: args.provider_id.as_deref().map(safe),
        model: args.model.as_deref().map(safe),
        checks,
    }
}

fn record_action_envelope_check(
    checks: &mut Vec<ActionEnvelopeCheck>,
    blockers: &mut Vec<String>,
    name: &'static str,
    passed: bool,
    pass_detail: String,
    fail_detail: String,
) {
    let detail = if passed { pass_detail } else { fail_detail };
    if !passed {
        blockers.push(format!("{name}: {detail}"));
    }
    checks.push(ActionEnvelopeCheck {
        name,
        status: if passed { "passed" } else { "failed" },
        detail,
    });
}

fn append_action_envelope_receipt(path: &Path, receipt: &ActionEnvelopeReceipt) -> Result<bool> {
    if action_envelope_receipt_exists(path, &receipt.receipt_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(receipt)?)?;
    Ok(true)
}

fn append_accepted_truth_recovery_action_receipt(
    path: &Path,
    receipt: &AcceptedTruthRecoveryActionReceipt,
) -> Result<bool> {
    if action_envelope_receipt_exists(path, &receipt.receipt_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(receipt)?)?;
    Ok(true)
}

fn action_envelope_receipt_exists(path: &Path, receipt_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read action envelope receipts {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("receipt_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == receipt_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn read_action_envelope_receipts(profile_dir: &Path) -> Result<Vec<StoredActionEnvelopeReceipt>> {
    let path = profile_dir.join(ACTION_ENVELOPE_RECEIPTS_FILE);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)
        .with_context(|| format!("read action envelope receipts {}", path.display()))?;
    let mut receipts = Vec::new();
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        receipts.push(
            serde_json::from_str::<StoredActionEnvelopeReceipt>(line).with_context(|| {
                format!(
                    "parse action envelope receipt {} line {}",
                    path.display(),
                    index + 1
                )
            })?,
        );
    }
    Ok(receipts)
}

fn latest_action_envelope_receipt<'a>(
    source: &StoredActionEnvelopeReceipt,
    receipts: &'a [StoredActionEnvelopeReceipt],
) -> Option<&'a StoredActionEnvelopeReceipt> {
    receipts
        .iter()
        .enumerate()
        .filter(|(_, receipt)| {
            receipt.action_id == source.action_id
                || receipt.idempotency_key == source.idempotency_key
        })
        .max_by_key(|(index, receipt)| (receipt.processed_at, *index))
        .map(|(_, receipt)| receipt)
}

fn record_action_preflight_check(
    checks: &mut Vec<ActionPreflightCheck>,
    blockers: &mut Vec<String>,
    name: &'static str,
    passed: bool,
    pass_detail: String,
    fail_detail: String,
) {
    let detail = if passed { pass_detail } else { fail_detail };
    if !passed {
        blockers.push(format!("{name}: {detail}"));
    }
    checks.push(ActionPreflightCheck {
        name,
        status: if passed { "passed" } else { "failed" },
        detail,
    });
}

fn append_action_execution_preflight(
    path: &Path,
    preflight: &ActionExecutionPreflight,
) -> Result<bool> {
    if action_execution_preflight_exists(path, &preflight.preflight_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(preflight)?)?;
    Ok(true)
}

fn action_execution_preflight_exists(path: &Path, preflight_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read action execution preflights {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("preflight_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == preflight_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn action_execution_preflight_id(
    source_receipt_id: &str,
    result_status: &str,
    current_hash: Option<&str>,
    reason: &str,
) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}",
        source_receipt_id,
        result_status,
        current_hash.unwrap_or("missing"),
        reason
    );
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "action-preflight-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn apply_decision_action(
    mut record: DecisionRecord,
    execution: &DecisionActionExecution,
    args: &ActionDecisionArgs,
) -> DecisionRecord {
    let decision = execution.decision.as_str();
    let by = safe(args.by.trim());
    record.updated_at = execution.executed_at;
    record.trace_refs.push(DecisionTraceRef {
        kind: "decision_action_execution".to_string(),
        label: by.clone(),
        reference: format!(
            "{} choice={} preflight={}",
            execution.execution_id, decision, execution.preflight_id
        ),
    });

    match decision {
        "deny" => {
            record.status = DecisionStatus::Denied;
            record.execution_handoff = None;
        }
        "defer" => {
            record.status = DecisionStatus::Deferred;
            record.execution_handoff = None;
        }
        _ => {
            record.status = DecisionStatus::HandoffReady;
            record.execution_handoff =
                Some(build_decision_action_handoff(&record, execution, args, by));
        }
    }
    record
}

fn apply_decision_action_closeout(
    mut record: DecisionRecord,
    closeout: &DecisionActionCloseout,
    args: &ActionCloseoutArgs,
) -> DecisionRecord {
    let by = safe(args.by.trim());
    let applied_handoff_id = record
        .execution_handoff
        .as_ref()
        .map(|handoff| handoff.handoff_id.clone());
    record.updated_at = closeout.recorded_at;
    record.status = DecisionStatus::Receipted;
    record.decision_receipt = Some(DecisionReceipt {
        receipt_id: closeout
            .receipt_id
            .clone()
            .unwrap_or_else(|| decision_action_receipt_id(&closeout.execution_id, &closeout.receipt_result_status)),
        decision_id: record.decision_id.clone(),
        resolved_by: by.clone(),
        resolved_at: closeout.recorded_at,
        final_decision: closeout.decision.clone(),
        applied_handoff_id,
        authorization_summary: "Receipt closes the Web/mobile decision action handoff; it does not authorize runtime mutation, cleanup, provider retargeting, accepted-truth changes, or wiki promotion.".to_string(),
        evidence_summary: closeout.evidence_summary.clone(),
        result_status: closeout.receipt_result_status.clone(),
        remaining_review: closeout.remaining_review.clone(),
    });
    record.trace_refs.push(DecisionTraceRef {
        kind: "decision_action_closeout".to_string(),
        label: by,
        reference: format!(
            "{} execution={}",
            closeout.closeout_id, closeout.execution_id
        ),
    });
    record
}

fn build_decision_action_handoff(
    record: &DecisionRecord,
    execution: &DecisionActionExecution,
    args: &ActionDecisionArgs,
    by: String,
) -> ExecutionHandoff {
    let decision = execution.decision.as_str();
    let note = safe(args.note.trim());
    let mut instructions = vec![
        format!("Operator selected `{decision}` through action preflight."),
        format!("Decision action execution: {}", execution.execution_id),
        format!("Source preflight: {}", execution.preflight_id),
    ];
    if !note.trim().is_empty() {
        instructions.push(format!("Operator note: {note}"));
    }
    instructions.push("Before execution, read the decision request, Council review, approval brief projection, action envelope receipt, and action execution preflight.".to_string());

    let non_authorized_actions = record.decision_request.non_authorized_scope.clone();
    let constraints = non_authorized_actions
        .iter()
        .map(|scope| format!("This handoff does not authorize {scope}."))
        .collect::<Vec<_>>();

    ExecutionHandoff {
        handoff_id: execution
            .handoff_id
            .clone()
            .unwrap_or_else(|| decision_handoff_id(&execution.execution_id)),
        decision_id: record.decision_id.clone(),
        target: args
            .target
            .as_deref()
            .map(safe)
            .filter(|target| !target.trim().is_empty())
            .unwrap_or_else(|| default_decision_action_target(decision).to_string()),
        approved_direction: decision.to_string(),
        approved_scope: record.decision_request.current_scope.clone(),
        instructions,
        constraints,
        verification_required: vec![
            "Record a decision receipt before treating this handoff as accepted.".to_string(),
            "Use separate approvals for runtime mutation, cleanup, provider retargeting, accepted-truth changes, or wiki promotion.".to_string(),
        ],
        non_authorized_actions: {
            let mut actions = non_authorized_actions;
            actions.push(format!(
                "This execution was recorded by {by}; it does not dispatch runtime work."
            ));
            actions
        },
    }
}

fn read_action_execution_preflights(
    profile_dir: &Path,
) -> Result<Vec<StoredActionExecutionPreflight>> {
    let path = profile_dir.join(ACTION_EXECUTION_PREFLIGHTS_FILE);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)
        .with_context(|| format!("read action execution preflights {}", path.display()))?;
    let mut preflights = Vec::new();
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        preflights.push(
            serde_json::from_str::<StoredActionExecutionPreflight>(line).with_context(|| {
                format!(
                    "parse action execution preflight {} line {}",
                    path.display(),
                    index + 1
                )
            })?,
        );
    }
    Ok(preflights)
}

fn find_decision_action_execution_for_preflight(
    path: &Path,
    preflight_id: &str,
) -> Result<Option<Value>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action executions {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("preflight_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == preflight_id)
            && value
                .get("result_status")
                .and_then(Value::as_str)
                .is_some_and(|value| value == "applied")
        {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

fn find_decision_action_execution(
    path: &Path,
    execution_id: &str,
) -> Result<Option<StoredDecisionActionExecution>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action executions {}", path.display()))?;
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        let execution =
            serde_json::from_str::<StoredDecisionActionExecution>(line).with_context(|| {
                format!(
                    "parse decision action execution {} line {}",
                    path.display(),
                    index + 1
                )
            })?;
        if execution.execution_id == execution_id {
            return Ok(Some(execution));
        }
    }
    Ok(None)
}

fn append_decision_action_execution(
    path: &Path,
    execution: &DecisionActionExecution,
) -> Result<bool> {
    if decision_action_execution_exists(path, &execution.execution_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(execution)?)?;
    Ok(true)
}

fn decision_action_execution_exists(path: &Path, execution_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action executions {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("execution_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == execution_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn find_decision_action_closeout_for_execution(
    path: &Path,
    execution_id: &str,
) -> Result<Option<Value>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action closeouts {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("execution_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == execution_id)
            && value
                .get("result_status")
                .and_then(Value::as_str)
                .is_some_and(|value| value == "receipted")
        {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

fn append_decision_action_closeout(path: &Path, closeout: &DecisionActionCloseout) -> Result<bool> {
    if decision_action_closeout_exists(path, &closeout.closeout_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(closeout)?)?;
    Ok(true)
}

fn decision_action_closeout_exists(path: &Path, closeout_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action closeouts {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("closeout_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == closeout_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn read_decision_action_closeouts(path: &Path) -> Result<Vec<StoredDecisionActionCloseout>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision action closeouts {}", path.display()))?;
    let mut closeouts = Vec::new();
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        closeouts.push(
            serde_json::from_str::<StoredDecisionActionCloseout>(line).with_context(|| {
                format!(
                    "parse decision action closeout {} line {}",
                    path.display(),
                    index + 1
                )
            })?,
        );
    }
    Ok(closeouts)
}

fn append_runtime_dispatch_preflight(
    path: &Path,
    preflight: &RuntimeDispatchPreflight,
) -> Result<bool> {
    if runtime_dispatch_preflight_exists(path, &preflight.preflight_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(preflight)?)?;
    Ok(true)
}

fn runtime_dispatch_preflight_exists(path: &Path, preflight_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read runtime dispatch preflights {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("preflight_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == preflight_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn read_runtime_dispatch_preflights(
    profile_dir: &Path,
) -> Result<Vec<StoredRuntimeDispatchPreflight>> {
    let path = profile_dir.join(RUNTIME_DISPATCH_PREFLIGHTS_FILE);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)
        .with_context(|| format!("read runtime dispatch preflights {}", path.display()))?;
    let mut preflights = Vec::new();
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        preflights.push(
            serde_json::from_str::<StoredRuntimeDispatchPreflight>(line).with_context(|| {
                format!(
                    "parse runtime dispatch preflight {} line {}",
                    path.display(),
                    index + 1
                )
            })?,
        );
    }
    Ok(preflights)
}

fn latest_runtime_dispatch_preflight<'a>(
    source: &StoredRuntimeDispatchPreflight,
    preflights: &'a [StoredRuntimeDispatchPreflight],
) -> Option<&'a StoredRuntimeDispatchPreflight> {
    preflights
        .iter()
        .rev()
        .find(|preflight| preflight.source_closeout_id == source.source_closeout_id)
}

fn find_runtime_dispatch_receipt_for_preflight(
    path: &Path,
    preflight_id: &str,
) -> Result<Option<Value>> {
    if !path.exists() {
        return Ok(None);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read runtime dispatch receipts {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("preflight_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == preflight_id)
            && value
                .get("result_status")
                .and_then(Value::as_str)
                .is_some_and(|value| value == "queued")
        {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

fn append_runtime_dispatch_receipt(path: &Path, receipt: &RuntimeDispatchReceipt) -> Result<bool> {
    if runtime_dispatch_receipt_exists(path, &receipt.receipt_id)? {
        return Ok(false);
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(receipt)?)?;
    Ok(true)
}

fn runtime_dispatch_receipt_exists(path: &Path, receipt_id: &str) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read runtime dispatch receipts {}", path.display()))?;
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if value
            .get("receipt_id")
            .and_then(Value::as_str)
            .is_some_and(|value| value == receipt_id)
        {
            return Ok(true);
        }
    }
    Ok(false)
}

fn runtime_dispatch_task(
    receipt: &RuntimeDispatchReceipt,
    args: &RuntimeDispatchArgs,
) -> OffdeskTask {
    OffdeskTask::new(
        OffdeskTaskInput {
            task_id: Some(receipt.task_id.clone()),
            request_id: receipt.request_id.clone(),
            project_key: receipt.project_key.clone(),
            capability_id: receipt.capability_id.clone(),
            runner_kind: receipt.runner_kind,
            command: receipt.command.clone(),
            workdir: receipt.workdir.clone(),
            execution_brief: None,
            not_before: None,
            mutation_class: Some("runtime_dispatch".to_string()),
            artifact_refs: Vec::new(),
            implementation_packet: None,
            artifact_kind: None,
            agent_mode: None,
            provider_id: args.provider_id.as_deref().map(safe),
            model: args.model.as_deref().map(safe),
            preview: format!(
                "Queued from {} for decision {}.",
                receipt.source_closeout_id, receipt.decision_id
            ),
            reason: receipt.reason.clone(),
            log_artifact_path: args
                .log_artifact
                .as_ref()
                .map(|path| safe(path.to_string_lossy().as_ref())),
            result_artifact_path: args
                .result_artifact
                .as_ref()
                .map(|path| safe(path.to_string_lossy().as_ref())),
        },
        receipt.recorded_at,
    )
}

fn record_action_decision_check(
    checks: &mut Vec<ActionDecisionCheck>,
    blockers: &mut Vec<String>,
    name: &'static str,
    passed: bool,
    pass_detail: String,
    fail_detail: String,
) {
    let detail = if passed { pass_detail } else { fail_detail };
    if !passed {
        blockers.push(format!("{name}: {detail}"));
    }
    checks.push(ActionDecisionCheck {
        name,
        status: if passed { "passed" } else { "failed" },
        detail,
    });
}

fn record_action_closeout_check(
    checks: &mut Vec<ActionCloseoutCheck>,
    blockers: &mut Vec<String>,
    name: &'static str,
    passed: bool,
    pass_detail: String,
    fail_detail: String,
) {
    let detail = if passed { pass_detail } else { fail_detail };
    if !passed {
        blockers.push(format!("{name}: {detail}"));
    }
    checks.push(ActionCloseoutCheck {
        name,
        status: if passed { "passed" } else { "failed" },
        detail,
    });
}

fn record_runtime_dispatch_check(
    checks: &mut Vec<RuntimeDispatchCheck>,
    blockers: &mut Vec<String>,
    name: &'static str,
    passed: bool,
    pass_detail: String,
    fail_detail: String,
) {
    let detail = if passed { pass_detail } else { fail_detail };
    if !passed {
        blockers.push(format!("{name}: {detail}"));
    }
    checks.push(RuntimeDispatchCheck {
        name,
        status: if passed { "passed" } else { "failed" },
        detail,
    });
}

fn normalize_action_decision_choice(value: &str) -> String {
    let normalized = value.trim().to_lowercase().replace([' ', '-'], "_");
    match normalized.as_str() {
        "go" | "ok" | "okay" | "yes" | "proceed" => "continue".to_string(),
        "retry" | "redo" => "revise".to_string(),
        "hold" => "block".to_string(),
        "cancel" | "abort" => "stop".to_string(),
        other => other.to_string(),
    }
}

fn supported_decision_action(decision: &str) -> bool {
    matches!(
        decision,
        "continue" | "revise" | "block" | "stop" | "deny" | "defer"
    )
}

fn decision_action_requires_note(decision: &str) -> bool {
    matches!(decision, "revise" | "block")
}

fn decision_action_status_is_mutable(status: DecisionStatus) -> bool {
    matches!(
        status,
        DecisionStatus::Draft
            | DecisionStatus::CouncilReview
            | DecisionStatus::UserPending
            | DecisionStatus::Deferred
    )
}

fn decision_creates_handoff(decision: &str) -> bool {
    !matches!(decision, "deny" | "defer")
}

fn default_decision_action_target(decision: &str) -> &'static str {
    match decision {
        "stop" => "closeout",
        _ => "agent",
    }
}

fn decision_handoff_id(execution_id: &str) -> String {
    format!("handoff-{}", safe(execution_id))
}

fn decision_action_execution_id(
    preflight_id: &str,
    result_status: &str,
    decision: &str,
    reason: &str,
) -> String {
    let canonical = format!("{preflight_id}\n{result_status}\n{decision}\n{reason}");
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "decision-action-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn decision_action_closeout_id(
    execution_id: &str,
    result_status: &str,
    receipt_result_status: &str,
    reason: &str,
) -> String {
    let canonical = format!("{execution_id}\n{result_status}\n{receipt_result_status}\n{reason}");
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "decision-action-closeout-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn decision_action_receipt_id(execution_id: &str, receipt_result_status: &str) -> String {
    let canonical = format!("{execution_id}\n{receipt_result_status}");
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "receipt-decision-action-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn runtime_dispatch_preflight_id(
    closeout_id: &str,
    result_status: &str,
    receipt_id: Option<&str>,
    reason: &str,
) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}",
        closeout_id,
        result_status,
        receipt_id.unwrap_or("missing"),
        reason
    );
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "runtime-preflight-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn runtime_dispatch_receipt_id(
    preflight_id: &str,
    result_status: &str,
    task_id: &str,
    command: &str,
    reason: &str,
) -> String {
    let canonical = format!("{preflight_id}\n{result_status}\n{task_id}\n{command}\n{reason}");
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "runtime-dispatch-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn runtime_dispatch_task_id(preflight_id: &str, override_task_id: Option<&str>) -> String {
    override_task_id
        .map(safe)
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| {
            let digest = action_envelope_sha256_hex(preflight_id.as_bytes());
            format!(
                "runtime-task-{}",
                digest
                    .strip_prefix("sha256:")
                    .unwrap_or(&digest)
                    .chars()
                    .take(12)
                    .collect::<String>()
            )
        })
}

fn action_envelope_receipt_id(
    idempotency_key: &str,
    result_status: &str,
    current_hash: Option<&str>,
    reason: &str,
    observed_hash: &str,
) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}\n{}",
        idempotency_key,
        result_status,
        current_hash.unwrap_or("missing"),
        reason,
        observed_hash
    );
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "action-receipt-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn accepted_truth_recovery_action_receipt_id(
    idempotency_key: &str,
    result_status: &str,
    current_hash: Option<&str>,
    reason: &str,
    observed_hash: &str,
) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}\n{}",
        idempotency_key,
        result_status,
        current_hash.unwrap_or("missing"),
        reason,
        observed_hash
    );
    let digest = action_envelope_sha256_hex(canonical.as_bytes());
    format!(
        "truth-recovery-receipt-{}",
        digest
            .strip_prefix("sha256:")
            .unwrap_or(&digest)
            .chars()
            .take(16)
            .collect::<String>()
    )
}

fn action_envelope_observed_hash(record: &DecisionRecord, action_kind: &str) -> String {
    let canonical = format!(
        "{}\n{}\n{}\n{}\n{}\n{}\n{}",
        record.decision_id,
        record.project_key,
        record.status.as_str(),
        record.materiality.as_str(),
        record.updated_at.to_rfc3339(),
        record.decision_request.kind,
        action_kind
    );
    action_envelope_sha256_hex(canonical.as_bytes())
}

fn action_envelope_slug(value: &str) -> String {
    let mut output = String::new();
    let mut last_was_separator = false;
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() {
            output.push(ch.to_ascii_lowercase());
            last_was_separator = false;
        } else if !last_was_separator && !output.is_empty() {
            output.push('_');
            last_was_separator = true;
        }
    }
    while output.ends_with('_') {
        output.pop();
    }
    if output.is_empty() {
        "action".to_string()
    } else {
        output
    }
}

fn action_envelope_sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("sha256:{:x}", hasher.finalize())
}

fn resolve_context(
    profile: &str,
    identifier: Option<&str>,
    project_key: Option<String>,
    mode: Option<String>,
) -> Result<ResolvedOndeskContext> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let instances = storage.load()?;
    let session = resolve_optional_session(identifier, &instances)?;
    let project_path = session
        .as_ref()
        .map(|session| PathBuf::from(&session.project_path))
        .unwrap_or(std::env::current_dir()?);
    let project_key = project_key
        .map(|value| safe(&value))
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default_project_key(session.as_ref()));
    let mode = mode
        .map(|value| safe(&value))
        .filter(|value| !value.trim().is_empty());

    Ok(ResolvedOndeskContext {
        profile: profile_name,
        profile_dir,
        session,
        project_path,
        project_key,
        mode,
    })
}

fn resolve_optional_session(
    identifier: Option<&str>,
    instances: &[Instance],
) -> Result<Option<Instance>> {
    if let Some(identifier) = identifier {
        return Ok(Some(super::resolve_session(identifier, instances)?.clone()));
    }

    if let Some(session_name) = std::env::var("TMUX_PANE")
        .ok()
        .and_then(|_| crate::tmux::get_current_session_name())
    {
        if let Some(instance) = instances
            .iter()
            .find(|instance| tmux_session_name_matches(instance, &session_name))
        {
            return Ok(Some(instance.clone()));
        }
    }

    let current_dir = std::env::current_dir()?.display().to_string();
    if let Some(instance) = instances
        .iter()
        .find(|instance| paths_match(&instance.project_path, &current_dir))
    {
        return Ok(Some(instance.clone()));
    }

    Ok(None)
}

fn tmux_session_name_matches(instance: &Instance, session_name: &str) -> bool {
    crate::tmux::Session::generate_name(&instance.id, &instance.title) == session_name
        || crate::tmux::Session::generate_legacy_name(&instance.id, &instance.title) == session_name
}

fn paths_match(left: &str, right: &str) -> bool {
    let left = normalize_path(left);
    let right = normalize_path(right);
    left == right
}

fn normalize_path(path: &str) -> String {
    fs::canonicalize(path)
        .map(|path| path.display().to_string())
        .unwrap_or_else(|_| path.to_string())
}

fn default_project_key(session: Option<&Instance>) -> String {
    let path = session
        .map(|session| PathBuf::from(&session.project_path))
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("default"));
    let key = path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .unwrap_or("default");
    safe(key)
}

fn append_note(path: &Path, record: &OndeskNoteRecord) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(record)?)?;
    Ok(())
}

fn load_notes(profile_dir: &Path) -> Result<Vec<OndeskNoteRecord>> {
    let path = profile_dir.join(NOTES_FILE);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)?;
    let mut notes = Vec::new();
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let note: OndeskNoteRecord = serde_json::from_str(line)
            .with_context(|| format!("failed to parse ondesk note in {}", path.display()))?;
        notes.push(note);
    }
    Ok(notes)
}

fn matching_recent_notes(
    profile_dir: &Path,
    context: &ResolvedOndeskContext,
) -> Result<Vec<OndeskNoteRecord>> {
    let mut notes: Vec<_> = load_notes(profile_dir)?
        .into_iter()
        .filter(|note| note_matches_context(note, context))
        .collect();
    notes.sort_by_key(|note| note.created_at);
    notes.reverse();
    notes.truncate(MAX_RECENT_NOTES);
    notes.reverse();
    Ok(notes)
}

fn note_matches_context(note: &OndeskNoteRecord, context: &ResolvedOndeskContext) -> bool {
    if let Some(mode) = &context.mode {
        if note.mode.as_ref() != Some(mode) {
            return false;
        }
    }

    if note.project_key == context.project_key {
        return true;
    }

    let Some(session) = &context.session else {
        return false;
    };

    note.session_id.as_deref() == Some(session.id.as_str())
        || note
            .session_path
            .as_deref()
            .is_some_and(|path| paths_match(path, &session.project_path))
}

fn capture_scrollback(session: Option<&Instance>, lines: usize) -> Result<(String, bool)> {
    let Some(session) = session else {
        return Ok((String::new(), false));
    };
    let tmux_session = session.tmux_session()?;
    let running = tmux_session.exists();
    if !running {
        return Ok((String::new(), false));
    }
    Ok((tmux_session.capture_pane(lines)?, true))
}

fn git_snapshot(path: &Path) -> Result<GitSnapshot> {
    if !path.exists() {
        return Ok(GitSnapshot {
            status_short: None,
            diff_stat: None,
            error: Some(safe(&format!(
                "project path does not exist: {}",
                path.display()
            ))),
        });
    }

    let status = read_git_output(path, &["status", "--short"])?;
    let diff_stat = read_git_output(path, &["diff", "--stat"])?;
    Ok(GitSnapshot {
        status_short: status,
        diff_stat,
        error: None,
    })
}

fn read_git_output(path: &Path, args: &[&str]) -> Result<Option<String>> {
    let output = Command::new("git").args(args).current_dir(path).output()?;
    let raw = if output.status.success() {
        String::from_utf8_lossy(&output.stdout).to_string()
    } else {
        String::from_utf8_lossy(&output.stderr).to_string()
    };
    let safe = safe(raw.trim());
    if safe.is_empty() {
        Ok(None)
    } else {
        let (text, truncated) = truncate_chars(&safe, MAX_GIT_CHARS);
        Ok(Some(if truncated {
            format!("{}\n[git output truncated]", text)
        } else {
            text
        }))
    }
}

fn load_capture_by_id(profile_dir: &Path, capture_id: &str) -> Result<OndeskCaptureRecord> {
    let captures_dir = profile_dir.join(CAPTURES_DIR);
    if !captures_dir.exists() {
        anyhow::bail!("No ondesk captures found");
    }

    for entry in fs::read_dir(&captures_dir)? {
        let entry = entry?;
        if !entry.path().is_dir() {
            continue;
        }
        let path = entry.path().join(CAPTURE_FILE);
        if !path.exists() {
            continue;
        }
        let capture: OndeskCaptureRecord = serde_json::from_str(&fs::read_to_string(&path)?)?;
        if capture.id == capture_id {
            return Ok(capture);
        }
    }

    anyhow::bail!("Ondesk capture not found: {}", capture_id)
}

fn latest_closeout_package(
    profile_dir: &Path,
    project_key: &str,
) -> Result<Option<OndeskCloseoutPackage>> {
    let closeouts_dir = profile_dir.join("offdesk_closeouts");
    if !closeouts_dir.exists() {
        return Ok(None);
    }

    let mut candidates = Vec::new();
    for entry in fs::read_dir(&closeouts_dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let plan_path = artifact_dir.join("closeout_plan.json");
        let Ok(plan_content) = fs::read_to_string(&plan_path) else {
            continue;
        };
        let Ok(plan) = serde_json::from_str::<Value>(&plan_content) else {
            continue;
        };
        if !closeout_plan_matches_project(&plan, project_key) {
            continue;
        }
        let generated_at = closeout_plan_generated_at(&plan);
        candidates.push((generated_at, artifact_dir, plan));
    }

    candidates.sort_by_key(|(generated_at, _, _)| *generated_at);
    let Some((generated_at, artifact_dir, plan)) = candidates.pop() else {
        return Ok(None);
    };

    let return_package_path = plan
        .pointer("/artifacts/return_package_markdown")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| artifact_dir.join("RETURN_PACKAGE.md"));
    let raw_return_package = fs::read_to_string(&return_package_path).unwrap_or_else(|_| {
        format!(
            "- Return package is missing at {}. Re-run `forager offdesk closeout --project-key {}`.",
            safe(return_package_path.to_string_lossy().as_ref()),
            safe(project_key)
        )
    });
    let safe_return_package = safe(&raw_return_package);
    let (return_package, return_package_truncated) =
        truncate_chars(&safe_return_package, MAX_CLOSEOUT_CHARS);
    let review = latest_closeout_review(&artifact_dir)?;
    let closeout_id = plan
        .get("closeout_id")
        .and_then(Value::as_str)
        .map(safe)
        .unwrap_or_else(|| "unknown".to_string());
    let audit_project_path = closeout_plan_audit_project_path(&plan, project_key);

    Ok(Some(OndeskCloseoutPackage {
        summary: OndeskCloseoutSummary {
            closeout_id,
            generated_at: generated_at.to_rfc3339(),
            artifact_dir: safe(artifact_dir.to_string_lossy().as_ref()),
            return_package_path: safe(return_package_path.to_string_lossy().as_ref()),
            return_package_truncated,
            review_verdict: review.as_ref().map(|review| review.verdict.clone()),
            review_record_path: review.as_ref().map(|review| review.record_path.clone()),
            receipt_status: review
                .as_ref()
                .and_then(|review| review.receipt_status.clone()),
            receipt_path: review.and_then(|review| review.receipt_path),
        },
        return_package,
        audit_project_path,
    }))
}

fn latest_project_initialization(
    profile_dir: &Path,
    project_key: &str,
) -> Result<Option<OndeskProjectInitializationPackage>> {
    let initializations_dir = profile_dir.join("project_initializations");
    if !initializations_dir.exists() {
        return Ok(None);
    }

    let mut candidates = Vec::new();
    for entry in fs::read_dir(&initializations_dir)? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let profile_path = artifact_dir.join("PROJECT_OPERATION_PROFILE.json");
        let Ok(profile_content) = fs::read_to_string(&profile_path) else {
            continue;
        };
        let Ok(profile) = serde_json::from_str::<Value>(&profile_content) else {
            continue;
        };
        if profile.get("project_key").and_then(Value::as_str) != Some(project_key) {
            continue;
        }
        let generated_at = profile
            .get("generated_at")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc))
            .unwrap_or(DateTime::<Utc>::UNIX_EPOCH);
        candidates.push((generated_at, artifact_dir, profile_path, profile));
    }

    candidates.sort_by_key(|(generated_at, _, _, _)| *generated_at);
    let Some((generated_at, artifact_dir, profile_path, profile)) = candidates.pop() else {
        return Ok(None);
    };

    let ondesk_start_package_path = profile
        .get("ondesk_start_package_path")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| artifact_dir.join("ONDESK_START_PACKAGE.md"));
    let offdesk_ready_check_path = profile
        .get("offdesk_ready_check_path")
        .and_then(Value::as_str)
        .map(PathBuf::from)
        .unwrap_or_else(|| artifact_dir.join("OFFDESK_READY_CHECK.json"));
    let module_operation_preflight_path_from_profile = profile
        .get("module_operation_preflight_path")
        .and_then(Value::as_str);
    let module_operation_preflight_path = module_operation_preflight_path_from_profile
        .map(PathBuf::from)
        .unwrap_or_else(|| artifact_dir.join("MODULE_OPERATION_PREFLIGHT.json"));
    let module_operation_preflight_path_summary = (module_operation_preflight_path_from_profile
        .is_some()
        || module_operation_preflight_path.exists())
    .then(|| safe(module_operation_preflight_path.to_string_lossy().as_ref()));
    let module_operation_preflight =
        summarize_module_operation_preflight(&module_operation_preflight_path);
    let ready_check = fs::read_to_string(&offdesk_ready_check_path)
        .ok()
        .and_then(|content| serde_json::from_str::<Value>(&content).ok());
    let raw_start_package = fs::read_to_string(&ondesk_start_package_path).unwrap_or_else(|_| {
        format!(
            "- Ondesk start package is missing at {}. Re-run `forager project init` for project_key {}.",
            safe(ondesk_start_package_path.to_string_lossy().as_ref()),
            safe(project_key)
        )
    });
    let safe_start_package = safe(&raw_start_package);
    let (start_package, start_package_truncated) =
        truncate_chars(&safe_start_package, MAX_PROJECT_INIT_CHARS);
    let operation_targets = profile
        .pointer("/scope_model/operation_targets")
        .and_then(Value::as_array)
        .map(|targets| {
            targets
                .iter()
                .filter_map(|target| target.get("scope_ref").and_then(Value::as_str))
                .map(safe)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    Ok(Some(OndeskProjectInitializationPackage {
        summary: OndeskProjectInitializationSummary {
            initialization_id: profile
                .get("id")
                .and_then(Value::as_str)
                .map(safe)
                .unwrap_or_else(|| "unknown".to_string()),
            generated_at: generated_at.to_rfc3339(),
            artifact_dir: safe(artifact_dir.to_string_lossy().as_ref()),
            operation_profile_path: safe(profile_path.to_string_lossy().as_ref()),
            ondesk_start_package_path: safe(ondesk_start_package_path.to_string_lossy().as_ref()),
            offdesk_ready_check_path: safe(offdesk_ready_check_path.to_string_lossy().as_ref()),
            module_operation_preflight_path: module_operation_preflight_path_summary,
            module_operation_preflight,
            operation_targets,
            ready_for_ondesk_start: ready_check
                .as_ref()
                .and_then(|value| value.get("ready_for_ondesk_start"))
                .and_then(Value::as_bool),
            ready_for_offdesk_runtime: ready_check
                .as_ref()
                .and_then(|value| value.get("ready_for_offdesk_runtime"))
                .and_then(Value::as_bool),
            requires_operator_review: ready_check
                .as_ref()
                .and_then(|value| value.get("requires_operator_review"))
                .and_then(Value::as_bool),
            start_package_truncated,
        },
        start_package,
    }))
}

fn prompt_audit_path_for_capture(capture: &OndeskCaptureRecord) -> Result<PathBuf> {
    if let Some(session) = &capture.session {
        return Ok(PathBuf::from(&session.path));
    }
    Ok(std::env::current_dir()?)
}

fn prompt_documentation_governance_project_path(
    closeout: Option<&OndeskCloseoutPackage>,
    fallback: &Path,
) -> PathBuf {
    closeout
        .and_then(|package| package.audit_project_path.clone())
        .unwrap_or_else(|| fallback.to_path_buf())
}

fn prompt_documentation_governance(
    include_doc_audit: bool,
    project_path: Option<&Path>,
    project_key: &str,
    closeout: Option<&OndeskCloseoutPackage>,
) -> OndeskDocumentationGovernanceSummary {
    let closeout_return_package_path =
        closeout.map(|package| package.summary.return_package_path.clone());
    let project_path_label = project_path.map(|path| safe(path.to_string_lossy().as_ref()));

    if include_doc_audit {
        let Some(project_path) = project_path else {
            return OndeskDocumentationGovernanceSummary {
                source: "fresh_project_audit_unavailable".to_string(),
                requested_fresh_audit: true,
                project_path: None,
                command: None,
                recommendation_count: 0,
                recommendations: Vec::new(),
                closeout_return_package_path,
                error: Some("no project path was available for documentation audit".to_string()),
            };
        };
        let command = format!(
            "forager project audit-docs {} --audit-profile standard --json",
            shell_arg(project_path_label.as_deref().unwrap_or_default())
        );
        if !project_path.exists() {
            return OndeskDocumentationGovernanceSummary {
                source: "fresh_project_audit_unavailable".to_string(),
                requested_fresh_audit: true,
                project_path: project_path_label,
                command: Some(command),
                recommendation_count: 0,
                recommendations: Vec::new(),
                closeout_return_package_path,
                error: Some("project path does not exist".to_string()),
            };
        }
        return match audit_recommendations_for_project(
            project_path,
            DocumentationAuditProfile::Standard,
            100_000,
        ) {
            Ok(recommendations) => {
                let recommendation_count = recommendations.len();
                OndeskDocumentationGovernanceSummary {
                    source: "fresh_project_audit".to_string(),
                    requested_fresh_audit: true,
                    project_path: project_path_label,
                    command: Some(command),
                    recommendation_count,
                    recommendations: recommendations
                        .into_iter()
                        .take(MAX_DOC_AUDIT_RECOMMENDATIONS)
                        .map(ondesk_documentation_recommendation)
                        .collect(),
                    closeout_return_package_path,
                    error: None,
                }
            }
            Err(error) => OndeskDocumentationGovernanceSummary {
                source: "fresh_project_audit_unavailable".to_string(),
                requested_fresh_audit: true,
                project_path: project_path_label,
                command: Some(command),
                recommendation_count: 0,
                recommendations: Vec::new(),
                closeout_return_package_path,
                error: Some(safe(&error.to_string())),
            },
        };
    }

    if let Some(closeout) = closeout {
        return OndeskDocumentationGovernanceSummary {
            source: "latest_closeout_return_package".to_string(),
            requested_fresh_audit: false,
            project_path: project_path_label,
            command: None,
            recommendation_count: 0,
            recommendations: Vec::new(),
            closeout_return_package_path: Some(closeout.summary.return_package_path.clone()),
            error: None,
        };
    }

    OndeskDocumentationGovernanceSummary {
        source: "not_requested".to_string(),
        requested_fresh_audit: false,
        project_path: project_path_label,
        command: Some(format!(
            "forager ondesk prompt-package --project-key {} --include-doc-audit",
            shell_arg(&safe(project_key))
        )),
        recommendation_count: 0,
        recommendations: Vec::new(),
        closeout_return_package_path: None,
        error: None,
    }
}

fn ondesk_documentation_recommendation(
    recommendation: AuditRecommendation,
) -> OndeskDocumentationRecommendation {
    OndeskDocumentationRecommendation {
        priority: recommendation.priority,
        kind: recommendation.kind,
        title: recommendation.title,
        suggested_action: recommendation.suggested_action,
        paths: recommendation
            .paths
            .into_iter()
            .take(MAX_DOC_AUDIT_PATHS)
            .collect(),
    }
}

fn summarize_module_operation_preflight(
    path: &Path,
) -> Option<OndeskModuleOperationPreflightSummary> {
    let content = fs::read_to_string(path).ok()?;
    let value = serde_json::from_str::<Value>(&content).ok()?;
    let blockers = safe_json_string_list(value.get("blockers"), MAX_MODULE_PREFLIGHT_BLOCKERS);
    let operation_targets = value
        .get("operation_targets")
        .and_then(Value::as_array)
        .map(|targets| {
            targets
                .iter()
                .take(MAX_MODULE_PREFLIGHT_TARGETS)
                .filter_map(summarize_module_operation_preflight_target)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    Some(OndeskModuleOperationPreflightSummary {
        path: safe(path.to_string_lossy().as_ref()),
        ready_for_offdesk_runtime: value
            .get("ready_for_offdesk_runtime")
            .and_then(Value::as_bool),
        blocker_count: value
            .get("blockers")
            .and_then(Value::as_array)
            .map_or(0, Vec::len),
        blockers,
        operation_targets,
    })
}

fn summarize_module_operation_preflight_target(
    value: &Value,
) -> Option<OndeskModuleOperationPreflightTargetSummary> {
    let scope_ref = value.get("scope_ref").and_then(Value::as_str).map(safe)?;
    Some(OndeskModuleOperationPreflightTargetSummary {
        scope_ref,
        readiness_level: value
            .get("readiness_level")
            .and_then(Value::as_str)
            .map(safe),
        recognized_profile_kind: value
            .get("recognized_profile_kind")
            .and_then(Value::as_str)
            .map(safe),
        profile_builder_available: value
            .get("profile_builder_available")
            .and_then(Value::as_bool),
        evidence_bundle_builder_available: value
            .get("evidence_bundle_builder_available")
            .and_then(Value::as_bool),
        evidence_review_builder_available: value
            .get("evidence_review_builder_available")
            .and_then(Value::as_bool),
        blockers: safe_json_string_list(value.get("blockers"), MAX_MODULE_PREFLIGHT_BLOCKERS),
        recommended_command_purposes: value
            .get("recommended_commands")
            .and_then(Value::as_array)
            .map(|commands| {
                commands
                    .iter()
                    .take(MAX_MODULE_PREFLIGHT_COMMANDS)
                    .filter_map(|command| command.get("purpose").and_then(Value::as_str))
                    .map(safe)
                    .filter(|purpose| !purpose.is_empty())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default(),
    })
}

fn closeout_plan_matches_project(plan: &Value, project_key: &str) -> bool {
    if plan
        .pointer("/filters/project_key")
        .and_then(Value::as_str)
        .is_some_and(|value| value == project_key)
    {
        return true;
    }

    plan.get("tasks")
        .and_then(Value::as_array)
        .is_some_and(|tasks| {
            tasks.iter().any(|task| {
                task.get("project_key")
                    .and_then(Value::as_str)
                    .is_some_and(|value| value == project_key)
            })
        })
}

fn closeout_plan_audit_project_path(plan: &Value, project_key: &str) -> Option<PathBuf> {
    plan.pointer("/documentation_governance/workdir")
        .and_then(Value::as_str)
        .and_then(non_empty_path)
        .or_else(|| {
            plan.get("tasks")
                .and_then(Value::as_array)
                .and_then(|tasks| {
                    tasks.iter().find_map(|task| {
                        if task.get("project_key").and_then(Value::as_str) != Some(project_key) {
                            return None;
                        }
                        task.get("workdir")
                            .and_then(Value::as_str)
                            .and_then(non_empty_path)
                    })
                })
        })
}

fn non_empty_path(value: &str) -> Option<PathBuf> {
    let value = value.trim();
    if value.is_empty() || value == "-" {
        None
    } else {
        Some(PathBuf::from(value))
    }
}

fn closeout_plan_generated_at(plan: &Value) -> DateTime<Utc> {
    plan.get("generated_at")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc))
        .unwrap_or(DateTime::<Utc>::UNIX_EPOCH)
}

struct OndeskCloseoutReview {
    reviewed_at: DateTime<Utc>,
    verdict: String,
    record_path: String,
    receipt_status: Option<String>,
    receipt_path: Option<String>,
}

fn latest_closeout_review(artifact_dir: &Path) -> Result<Option<OndeskCloseoutReview>> {
    let mut reviews = Vec::new();
    for entry in fs::read_dir(artifact_dir)? {
        let entry = entry?;
        let path = entry.path();
        let Some(filename) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if !filename.starts_with("closeout_review_") || !filename.ends_with(".json") {
            continue;
        }
        let Ok(content) = fs::read_to_string(&path) else {
            continue;
        };
        let Ok(value) = serde_json::from_str::<Value>(&content) else {
            continue;
        };
        let Some(reviewed_at) = value
            .get("reviewed_at")
            .and_then(Value::as_str)
            .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
            .map(|value| value.with_timezone(&Utc))
        else {
            continue;
        };
        let verdict = value
            .get("verdict")
            .and_then(Value::as_str)
            .map(safe)
            .unwrap_or_else(|| "unknown".to_string());
        let receipt_status = value
            .pointer("/closeout_receipt/acceptance_status")
            .and_then(Value::as_str)
            .map(safe);
        let receipt_path = value
            .pointer("/artifacts/closeout_receipt_json")
            .and_then(Value::as_str)
            .map(safe);
        reviews.push(OndeskCloseoutReview {
            reviewed_at,
            verdict,
            record_path: safe(path.to_string_lossy().as_ref()),
            receipt_status,
            receipt_path,
        });
    }

    reviews.sort_by_key(|review| review.reviewed_at);
    Ok(reviews.pop())
}

enum PromptPackageContext<'a> {
    Capture {
        capture: &'a OndeskCaptureRecord,
        closeout: Option<&'a OndeskCloseoutPackage>,
        project_initialization: Option<&'a OndeskProjectInitializationPackage>,
        review_surface: Option<&'a Value>,
        documentation_governance: &'a OndeskDocumentationGovernanceSummary,
    },
    Live {
        profile: &'a str,
        project_key: &'a str,
        mode: Option<&'a str>,
        session: Option<&'a SessionRef>,
        notes: &'a [OndeskNoteRecord],
        closeout: Option<&'a OndeskCloseoutPackage>,
        project_initialization: Option<&'a OndeskProjectInitializationPackage>,
        review_surface: Option<&'a Value>,
        documentation_governance: &'a OndeskDocumentationGovernanceSummary,
    },
}

fn render_prompt_package(context: PromptPackageContext<'_>) -> String {
    match context {
        PromptPackageContext::Capture {
            capture,
            closeout,
            project_initialization,
            review_surface,
            documentation_governance,
        } => render_prompt_package_parts(PromptPackageParts {
            profile: &capture.profile,
            project_key: &capture.project_key,
            mode: capture.mode.as_deref(),
            session: capture.session.as_ref(),
            notes: &capture.notes,
            scrollback: Some(&capture.scrollback),
            git: capture.git.as_ref(),
            capture_id: Some(&capture.id),
            closeout,
            project_initialization,
            review_surface,
            documentation_governance,
        }),
        PromptPackageContext::Live {
            profile,
            project_key,
            mode,
            session,
            notes,
            closeout,
            project_initialization,
            review_surface,
            documentation_governance,
        } => render_prompt_package_parts(PromptPackageParts {
            profile,
            project_key,
            mode,
            session,
            notes,
            scrollback: None,
            git: None,
            capture_id: None,
            closeout,
            project_initialization,
            review_surface,
            documentation_governance,
        }),
    }
}

fn closeout_receipt_acceptance_note(status: &str) -> &'static str {
    match status {
        "accepted" => "accepted truth recorded; still inspect the evidence before acting.",
        "approved_with_followups" => {
            "not accepted truth; review receipt follow-ups before treating output as final."
        }
        "revision_required" => "not accepted truth; revise before continuing.",
        "blocked" => "not accepted truth; blocker review is required before continuing.",
        _ => "not accepted truth; inspect the closeout receipt before continuing.",
    }
}

struct PromptPackageParts<'a> {
    profile: &'a str,
    project_key: &'a str,
    mode: Option<&'a str>,
    session: Option<&'a SessionRef>,
    notes: &'a [OndeskNoteRecord],
    scrollback: Option<&'a str>,
    git: Option<&'a GitSnapshot>,
    capture_id: Option<&'a str>,
    closeout: Option<&'a OndeskCloseoutPackage>,
    project_initialization: Option<&'a OndeskProjectInitializationPackage>,
    review_surface: Option<&'a Value>,
    documentation_governance: &'a OndeskDocumentationGovernanceSummary,
}

fn render_prompt_package_parts(parts: PromptPackageParts<'_>) -> String {
    let mut output = String::new();
    output.push_str("# Forager Ondesk Prompt Package\n\n");
    output.push_str("## Context\n");
    output.push_str(&format!("- profile: {}\n", parts.profile));
    output.push_str(&format!("- project_key: {}\n", parts.project_key));
    if let Some(mode) = parts.mode {
        output.push_str(&format!("- mode: {}\n", mode));
    }
    if let Some(capture_id) = parts.capture_id {
        output.push_str(&format!("- capture_id: {}\n", capture_id));
    }
    if let Some(session) = parts.session {
        output.push_str(&format!("- session: {} ({})\n", session.title, session.id));
        output.push_str(&format!("- path: {}\n", session.path));
        output.push_str(&format!("- tool: {}\n", session.tool));
    } else {
        output.push_str("- session: none\n");
    }

    if let Some(surface) = parts.review_surface {
        render_review_surface_prompt_section(&mut output, surface);
    }

    output.push_str("\n## Operator Notes\n");
    if parts.notes.is_empty() {
        output.push_str("- No recent ondesk notes recorded for this context.\n");
    } else {
        for note in parts.notes {
            let mode = note
                .mode
                .as_deref()
                .map(|mode| format!(" [{}]", mode))
                .unwrap_or_default();
            output.push_str(&format!(
                "- {}{}: {}\n",
                note.created_at.to_rfc3339(),
                mode,
                note.text.replace('\n', " ")
            ));
        }
    }

    if let Some(git) = parts.git {
        output.push_str("\n## Git Snapshot\n");
        if let Some(status) = &git.status_short {
            output.push_str("### git status --short\n");
            output.push_str(&fenced("text", status));
        }
        if let Some(diff_stat) = &git.diff_stat {
            output.push_str("### git diff --stat\n");
            output.push_str(&fenced("text", diff_stat));
        }
        if let Some(error) = &git.error {
            output.push_str(&format!("- git snapshot unavailable: {}\n", error));
        }
        if git.status_short.is_none() && git.diff_stat.is_none() && git.error.is_none() {
            output.push_str("- No git changes detected.\n");
        }
    }

    if let Some(scrollback) = parts.scrollback {
        output.push_str("\n## Captured Harness Scrollback\n");
        if scrollback.trim().is_empty() {
            output.push_str("- No live tmux scrollback was available.\n");
        } else {
            output.push_str(&fenced("text", scrollback));
        }
    }

    if let Some(initialization) = parts.project_initialization {
        output.push_str("\n## Latest Project Initialization\n");
        output.push_str(&format!(
            "- initialization_id: {}\n",
            initialization.summary.initialization_id
        ));
        output.push_str(&format!(
            "- generated_at: {}\n",
            initialization.summary.generated_at
        ));
        output.push_str(&format!(
            "- operation_profile: {}\n",
            initialization.summary.operation_profile_path
        ));
        output.push_str(&format!(
            "- ondesk_start_package: {}\n",
            initialization.summary.ondesk_start_package_path
        ));
        if let Some(path) = &initialization.summary.module_operation_preflight_path {
            output.push_str(&format!("- module_operation_preflight: {path}\n"));
        }
        if initialization.summary.operation_targets.is_empty() {
            output.push_str("- operation_targets: none selected\n");
        } else {
            output.push_str(&format!(
                "- operation_targets: {}\n",
                initialization.summary.operation_targets.join(", ")
            ));
        }
        if let Some(ready) = initialization.summary.ready_for_ondesk_start {
            output.push_str(&format!("- ready_for_ondesk_start: {ready}\n"));
        }
        if let Some(ready) = initialization.summary.ready_for_offdesk_runtime {
            output.push_str(&format!("- ready_for_offdesk_runtime: {ready}\n"));
        }
        if let Some(required) = initialization.summary.requires_operator_review {
            output.push_str(&format!("- requires_operator_review: {required}\n"));
        }
        output.push('\n');
        output.push_str(&fenced("markdown", &initialization.start_package));

        if let Some(preflight) = &initialization.summary.module_operation_preflight {
            output.push_str("\n## Latest Module Operation Preflight\n");
            output.push_str(&format!("- preflight: {}\n", preflight.path));
            if let Some(ready) = preflight.ready_for_offdesk_runtime {
                output.push_str(&format!("- ready_for_offdesk_runtime: {ready}\n"));
            }
            output.push_str(&format!("- blocker_count: {}\n", preflight.blocker_count));
            if preflight.blockers.is_empty() {
                output.push_str("- blockers: none recorded\n");
            } else {
                output.push_str(&format!("- blockers: {}\n", preflight.blockers.join(", ")));
            }
            if preflight.operation_targets.is_empty() {
                output.push_str("- operation_targets: none recorded\n");
            } else {
                for target in &preflight.operation_targets {
                    output.push_str(&format!("- target `{}`", target.scope_ref));
                    if let Some(readiness) = &target.readiness_level {
                        output.push_str(&format!(" readiness={readiness}"));
                    }
                    if let Some(kind) = &target.recognized_profile_kind {
                        output.push_str(&format!(" recognized_profile_kind={kind}"));
                    }
                    if let Some(available) = target.profile_builder_available {
                        output.push_str(&format!(" profile_builder_available={available}"));
                    }
                    if let Some(available) = target.evidence_bundle_builder_available {
                        output.push_str(&format!(" evidence_bundle_builder_available={available}"));
                    }
                    if let Some(available) = target.evidence_review_builder_available {
                        output.push_str(&format!(" evidence_review_builder_available={available}"));
                    }
                    output.push('\n');
                    if !target.blockers.is_empty() {
                        output.push_str(&format!("  blockers: {}\n", target.blockers.join(", ")));
                    }
                    if !target.recommended_command_purposes.is_empty() {
                        output.push_str(&format!(
                            "  recommended_command_purposes: {}\n",
                            target.recommended_command_purposes.join(", ")
                        ));
                    }
                }
            }
        }
    }

    render_documentation_governance_prompt_section(&mut output, parts.documentation_governance);

    if let Some(closeout) = parts.closeout {
        output.push_str("\n## Latest Offdesk Return Package\n");
        output.push_str(&format!(
            "- closeout_id: {}\n",
            closeout.summary.closeout_id
        ));
        output.push_str(&format!(
            "- generated_at: {}\n",
            closeout.summary.generated_at
        ));
        output.push_str(&format!(
            "- return_package: {}\n",
            closeout.summary.return_package_path
        ));
        if let Some(verdict) = &closeout.summary.review_verdict {
            output.push_str(&format!("- review_verdict: {verdict}\n"));
        } else {
            output.push_str("- review_verdict: none recorded\n");
        }
        if let Some(status) = &closeout.summary.receipt_status {
            output.push_str(&format!("- closeout_receipt_status: {status}\n"));
            output.push_str(&format!(
                "- closeout_acceptance: {}\n",
                closeout_receipt_acceptance_note(status)
            ));
        }
        if let Some(path) = &closeout.summary.receipt_path {
            output.push_str(&format!("- closeout_receipt: {path}\n"));
        }
        output.push('\n');
        output.push_str(&fenced("markdown", &closeout.return_package));
    }

    output.push_str("\n## Instructions For The Next Harness\n");
    output.push_str("- Treat this package as context, not proof that work is complete.\n");
    output.push_str("- Separate observations from inference before making claims.\n");
    output
        .push_str("- Preserve the user's current direction unless new evidence contradicts it.\n");
    output.push_str("- When useful, propose wiki-worthy knowledge as a candidate rather than silently mutating durable knowledge.\n");
    output
}

fn render_review_surface_prompt_section(output: &mut String, surface: &Value) {
    output.push_str("\n## Morning Review Surface\n");
    output.push_str(&format!(
        "- schema: {}\n",
        value_text(surface, "/schema").unwrap_or("review_surface.v1")
    ));
    output.push_str(&format!(
        "- status: {} ({})\n",
        value_text(surface, "/status/label").unwrap_or("unknown"),
        value_text(surface, "/status/severity").unwrap_or("unknown")
    ));
    if let Some(summary) = value_text(surface, "/status/summary") {
        output.push_str(&format!("- status_summary: {summary}\n"));
    }
    output.push_str(&format!(
        "- accepted_truth: {} via {}\n",
        value_text(surface, "/accepted_truth/status").unwrap_or("unknown"),
        value_text(surface, "/accepted_truth/source").unwrap_or("unknown")
    ));
    if let Some(status) = value_text(surface, "/accepted_truth/receipt_acceptance_status") {
        output.push_str(&format!("- receipt_acceptance_status: {status}\n"));
    }
    if let Some(receipt_id) = value_text(surface, "/accepted_truth/accepted_receipt_id") {
        output.push_str(&format!("- accepted_receipt_id: {receipt_id}\n"));
    }
    if let Some(closeout_id) = value_text(surface, "/accepted_truth/accepted_closeout_id") {
        output.push_str(&format!("- accepted_closeout_id: {closeout_id}\n"));
    }
    if let Some(reason) = value_text(surface, "/accepted_truth/reason") {
        output.push_str(&format!("- accepted_truth_reason: {reason}\n"));
    }
    output.push_str(&format!(
        "- closeout: execution={}, review={}\n",
        value_text(surface, "/closeout/execution_status").unwrap_or("unknown"),
        value_text(surface, "/closeout/review_status").unwrap_or("unknown")
    ));
    render_source_observation_prompt_section(output, surface);
    render_closeout_packet_coverage_prompt_section(output, surface);
    if let Some(packet_id) = value_text(surface, "/implementation_packet/packet_id") {
        let outcome = value_text(surface, "/implementation_packet/outcome").unwrap_or("unknown");
        let safe_to_delegate = surface
            .pointer("/implementation_packet/safe_to_delegate")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let worker =
            value_text(surface, "/implementation_packet/preferred_worker").unwrap_or("unknown");
        output.push_str("- implementation_packet:\n");
        output.push_str(&format!(
            "  - {}: outcome={}, safe_to_delegate={}, worker={}\n",
            safe(packet_id),
            safe(outcome),
            safe_to_delegate,
            safe(worker)
        ));
        if let Some(goal) = value_text(surface, "/implementation_packet/goal") {
            output.push_str(&format!("  - goal: {}\n", safe(goal)));
        }
        if let Some(success_state) = value_text(surface, "/implementation_packet/success_state") {
            output.push_str(&format!("  - success_state: {}\n", safe(success_state)));
        }
        output.push_str(&format!(
            "  - structure: {} slice(s), {} capability mapping(s), {} validation item(s), {} stop condition(s), {} expected artifact(s)\n",
            value_u64(surface, "/implementation_packet/work_slice_count").unwrap_or_default(),
            value_u64(surface, "/implementation_packet/capability_mapping_count").unwrap_or_default(),
            value_u64(surface, "/implementation_packet/validation_item_count").unwrap_or_default(),
            value_u64(surface, "/implementation_packet/stop_condition_count").unwrap_or_default(),
            value_u64(surface, "/implementation_packet/expected_artifact_count").unwrap_or_default()
        ));
        if let Some(revisions) = surface
            .pointer("/implementation_packet/required_revisions")
            .and_then(Value::as_array)
            .filter(|revisions| !revisions.is_empty())
        {
            output.push_str("  - required_revisions:\n");
            for revision in revisions.iter().take(5).filter_map(Value::as_str) {
                output.push_str(&format!("    - {}\n", safe(revision)));
            }
        }
        if let Some(missing_decisions) = surface
            .pointer("/implementation_packet/missing_decisions")
            .and_then(Value::as_array)
            .filter(|missing_decisions| !missing_decisions.is_empty())
        {
            output.push_str("  - missing_decisions:\n");
            for decision in missing_decisions.iter().take(5).filter_map(Value::as_str) {
                output.push_str(&format!("    - {}\n", safe(decision)));
            }
        }
    }
    if let Some(runtime) = value_text(surface, "/runtime/progress_summary") {
        output.push_str(&format!("- runtime: {runtime}\n"));
    }
    let judgment_open_decisions = value_u64(surface, "/decisions/open_count").unwrap_or_default();
    let closeout_receipt_open_decisions =
        value_u64(surface, "/closeout/receipt_open_decisions").unwrap_or_default();
    if closeout_receipt_open_decisions > 0 {
        output.push_str(&format!(
            "- open_decisions: {judgment_open_decisions} judgment-route, {closeout_receipt_open_decisions} closeout-receipt\n"
        ));
    } else {
        output.push_str(&format!("- open_decisions: {judgment_open_decisions}\n"));
    }
    if let Some(decisions) = surface
        .pointer("/decisions/recent")
        .and_then(Value::as_array)
        .filter(|decisions| !decisions.is_empty())
    {
        let mut rendered = 0usize;
        for decision in decisions {
            let Some(evaluator) = value_text(decision, "/judgment_route/evaluator") else {
                continue;
            };
            if rendered == 0 {
                output.push_str("- judgment_routes:\n");
            }
            let decision_id = value_text(decision, "/decision_id").unwrap_or("decision");
            let reason =
                value_text(decision, "/judgment_route/reason").unwrap_or("no route reason");
            output.push_str(&format!(
                "  - {}: {} ({})\n",
                safe(decision_id),
                safe(evaluator),
                safe(reason)
            ));
            rendered += 1;
            if rendered >= 3 {
                break;
            }
        }
    }
    output.push_str(&format!(
        "- adaptive_wiki: {} candidate(s), {} review-due entry(s)\n",
        value_u64(surface, "/adaptive_wiki/candidate_count").unwrap_or_default(),
        value_u64(surface, "/adaptive_wiki/review_due_count").unwrap_or_default()
    ));
    if surface.pointer("/artifacts/index/schema").is_some() {
        output.push_str(&format!(
            "- artifact_index: {} total, {} review-required, {} disposal/archive candidate(s)\n",
            value_u64(surface, "/artifacts/index/summary/total_entries").unwrap_or_default(),
            value_u64(surface, "/artifacts/index/summary/review_required_entries")
                .unwrap_or_default(),
            value_u64(
                surface,
                "/artifacts/index/summary/disposal_candidate_entries"
            )
            .unwrap_or_default()
        ));
    }
    if surface
        .pointer("/artifacts/retention_review/schema")
        .is_some()
    {
        output.push_str(&format!(
            "- retention_review: {} action-required, {} missing, {} unreferenced human-facing\n",
            value_u64(
                surface,
                "/artifacts/retention_review/summary/action_required_entries"
            )
            .unwrap_or_default(),
            value_u64(
                surface,
                "/artifacts/retention_review/summary/missing_entries"
            )
            .unwrap_or_default(),
            value_u64(
                surface,
                "/artifacts/retention_review/summary/unreferenced_human_facing_entries"
            )
            .unwrap_or_default()
        ));
        if let Some(items) = surface
            .pointer("/artifacts/retention_review/action_required")
            .and_then(Value::as_array)
            .filter(|items| !items.is_empty())
        {
            output.push_str("- retention_actions:\n");
            for item in items.iter().take(3) {
                output.push_str(&format!(
                    "  - {}: {} ({})\n",
                    value_text(item, "/label").unwrap_or("Artifact"),
                    value_text(item, "/recommended_action").unwrap_or("review_before_relying"),
                    value_text(item, "/reason").unwrap_or("Review before mutation.")
                ));
            }
        }
    }
    if let Some(actions) = surface
        .get("next_safe_actions")
        .and_then(Value::as_array)
        .filter(|actions| !actions.is_empty())
    {
        output.push_str("- next_safe_actions:\n");
        for action in actions.iter().take(3) {
            output.push_str(&format!("  - {}\n", prompt_next_safe_action(action)));
        }
    }
    if let Some(risks) = surface
        .pointer("/closeout/unresolved_risks")
        .and_then(Value::as_array)
        .filter(|risks| !risks.is_empty())
    {
        output.push_str("- unresolved_risks:\n");
        for risk in risks.iter().take(4).filter_map(Value::as_str) {
            output.push_str(&format!("  - {}\n", safe(risk)));
        }
    }
    if let Some(summaries) = surface
        .pointer("/artifacts/summary")
        .and_then(Value::as_array)
        .filter(|summaries| !summaries.is_empty())
    {
        output.push_str("- artifact_summaries:\n");
        for summary in summaries.iter().take(5) {
            output.push_str(&format!(
                "  - {}: {} [{}]\n",
                value_text(summary, "/label").unwrap_or("Artifact"),
                value_text(summary, "/why_it_matters").unwrap_or("Review before use."),
                value_text(summary, "/retention_class").unwrap_or("review")
            ));
        }
    }
    output.push_str("- artifact_refs: available in `review_surface` JSON, omitted here unless needed for audit.\n");
}

fn render_source_observation_prompt_section(output: &mut String, surface: &Value) {
    let Some(observation) = surface.pointer("/closeout/source_observation") else {
        return;
    };
    let status = value_text(observation, "/status").unwrap_or("unknown");
    let source_kind = value_text(observation, "/source_kind").unwrap_or("unknown");
    let base_ref = value_text(observation, "/base_ref").unwrap_or("unknown");
    output.push_str("- source_observation:\n");
    output.push_str(&format!(
        "  - status: {} from {} against {}\n",
        safe(status),
        safe(source_kind),
        safe(base_ref)
    ));
    output.push_str(
        "  - interpretation: read-only source context, not accepted truth or slice verification\n",
    );
    output.push_str(&format!(
        "  - changed_files: {}\n",
        value_u64(observation, "/changed_file_count").unwrap_or_default()
    ));
    if let Some(files) = observation
        .pointer("/changed_files")
        .and_then(Value::as_array)
        .filter(|files| !files.is_empty())
    {
        for file in files.iter().take(3) {
            let file_status = value_text(file, "/status").unwrap_or("unknown");
            let path = value_text(file, "/path").unwrap_or("unknown");
            output.push_str(&format!(
                "    - [{}] {} (+{} -{})\n",
                safe(file_status),
                safe(path),
                value_u64(file, "/additions").unwrap_or_default(),
                value_u64(file, "/deletions").unwrap_or_default()
            ));
        }
    }
    if let Some(warnings) = observation
        .pointer("/warnings")
        .and_then(Value::as_array)
        .filter(|warnings| !warnings.is_empty())
    {
        output.push_str("  - warnings:\n");
        for warning in warnings.iter().take(3).filter_map(Value::as_str) {
            output.push_str(&format!("    - {}\n", safe(warning)));
        }
    }
}

fn render_closeout_packet_coverage_prompt_section(output: &mut String, surface: &Value) {
    let Some(coverage) = surface.pointer("/closeout/implementation_packet_coverage") else {
        return;
    };
    output.push_str("- closeout_implementation_packet_coverage:\n");
    output.push_str(&format!(
        "  - packets: {} completed, {} deferred, {} missing, {} drifted / {} total\n",
        value_u64(coverage, "/completed").unwrap_or_default(),
        value_u64(coverage, "/deferred").unwrap_or_default(),
        value_u64(coverage, "/missing").unwrap_or_default(),
        value_u64(coverage, "/drifted").unwrap_or_default(),
        value_u64(coverage, "/packet_count").unwrap_or_default()
    ));
    output.push_str(&format!(
        "  - detail_items: {} completed, {} deferred, {} missing, {} drifted / {} total\n",
        value_u64(coverage, "/detail_items_completed").unwrap_or_default(),
        value_u64(coverage, "/detail_items_deferred").unwrap_or_default(),
        value_u64(coverage, "/detail_items_missing").unwrap_or_default(),
        value_u64(coverage, "/detail_items_drifted").unwrap_or_default(),
        value_u64(coverage, "/detail_items").unwrap_or_default()
    ));
    if let Some(items) = coverage.get("items").and_then(Value::as_array) {
        for item in items.iter().take(3) {
            let packet_id = value_text(item, "/packet_id").unwrap_or("unknown");
            let status = value_text(item, "/goal_status").unwrap_or("unknown");
            output.push_str(&format!(
                "  - packet {}: {}\n",
                safe(packet_id),
                safe(status)
            ));
            render_packet_coverage_detail_prompt_group(output, item, "work_slices");
            render_packet_coverage_detail_prompt_group(output, item, "validation_items");
            render_packet_coverage_detail_prompt_group(output, item, "expected_artifacts");
        }
    }
}

fn render_packet_coverage_detail_prompt_group(output: &mut String, item: &Value, key: &str) {
    let Some(details) = item.get(key).and_then(Value::as_array) else {
        return;
    };
    let attention = details
        .iter()
        .filter(|detail| {
            value_text(detail, "/status")
                .map(|status| status != "completed")
                .unwrap_or(false)
        })
        .collect::<Vec<_>>();
    let shown = if attention.is_empty() {
        details.iter().take(2).collect::<Vec<_>>()
    } else {
        attention.into_iter().take(2).collect::<Vec<_>>()
    };
    if shown.is_empty() {
        return;
    }
    output.push_str(&format!("    - {key}:"));
    for detail in shown {
        let status = value_text(detail, "/status").unwrap_or("unknown");
        let label = value_text(detail, "/label").unwrap_or("unknown");
        output.push_str(&format!(" [{}] {}", safe(status), safe(label)));
        if let Some(source_status) =
            value_text(detail, "/source_observation_status").filter(|status| !status.is_empty())
        {
            output.push_str(&format!(" (source: {})", safe(source_status)));
        }
        if status != "completed" {
            if let Some(next) = value_text(detail, "/next_safe_action") {
                if !next.is_empty() {
                    output.push_str(&format!(" (next: {})", safe(&truncate_chars(next, 120).0)));
                }
            } else if let Some(summary) = value_text(detail, "/summary") {
                if !summary.is_empty() {
                    output.push_str(&format!(
                        " (summary: {})",
                        safe(&truncate_chars(summary, 120).0)
                    ));
                }
            }
        }
    }
    output.push('\n');
}

fn render_documentation_governance_prompt_section(
    output: &mut String,
    governance: &OndeskDocumentationGovernanceSummary,
) {
    output.push_str("\n## Documentation Governance Source\n");
    output.push_str(&format!("- source: `{}`\n", governance.source));
    output.push_str(&format!(
        "- requested_fresh_audit: {}\n",
        governance.requested_fresh_audit
    ));
    if let Some(path) = &governance.project_path {
        output.push_str(&format!("- project_path: `{path}`\n"));
    }
    if let Some(path) = &governance.closeout_return_package_path {
        output.push_str(&format!("- closeout_return_package: `{path}`\n"));
    }
    if let Some(command) = &governance.command {
        output.push_str(&format!("- command: `{command}`\n"));
    }
    if let Some(error) = &governance.error {
        output.push_str(&format!("- audit_unavailable: {error}\n"));
        return;
    }

    if governance.recommendations.is_empty() {
        match governance.source.as_str() {
            "fresh_project_audit" => {
                output.push_str("- recommendations: none from the fresh project audit.\n");
            }
            "latest_closeout_return_package" => {
                output.push_str("- recommendations: use the focused list embedded in the return package below.\n");
            }
            _ => {
                output.push_str("- recommendations: not included. Re-run with `--include-doc-audit` when a fresh audit is needed.\n");
            }
        }
        return;
    }

    output.push_str(&format!(
        "- recommendation_count: {}\n",
        governance.recommendation_count
    ));
    for recommendation in &governance.recommendations {
        output.push_str(&format!(
            "- {}: {} (`{}`)\n  - action: {}\n",
            recommendation.priority,
            recommendation.title,
            recommendation.kind,
            recommendation.suggested_action
        ));
        if !recommendation.paths.is_empty() {
            output.push_str("  - focus:");
            for path in &recommendation.paths {
                output.push_str(&format!(" `{path}`"));
            }
            output.push('\n');
        }
    }
}

fn fenced(language: &str, body: &str) -> String {
    format!("```{}\n{}\n```\n", language, body.trim_end())
}

fn short_id(prefix: &str) -> String {
    let id = Uuid::new_v4().to_string();
    format!("{}-{}", prefix, &id[..8])
}

fn safe(value: &str) -> String {
    operator_safe_text(value).trim().to_string()
}

fn value_text<'a>(value: &'a Value, pointer: &str) -> Option<&'a str> {
    value.pointer(pointer).and_then(Value::as_str)
}

fn value_u64(value: &Value, pointer: &str) -> Option<u64> {
    value.pointer(pointer).and_then(Value::as_u64)
}

fn prompt_next_safe_action(action: &Value) -> String {
    if !action.is_object() {
        return action
            .as_str()
            .map(safe)
            .unwrap_or_else(|| action.to_string());
    }
    let kind = action
        .get("kind")
        .and_then(Value::as_str)
        .map(safe)
        .unwrap_or_else(|| "next".to_string());
    let detail = action
        .get("detail")
        .and_then(Value::as_str)
        .map(safe)
        .unwrap_or_default();
    let review = action
        .get("requires_operator_review")
        .and_then(Value::as_bool)
        .map(|value| {
            if value {
                "operator review required"
            } else {
                "monitoring step"
            }
        });
    let mut text = if detail.is_empty() {
        kind
    } else {
        format!("{kind}: {detail}")
    };
    if let Some(review) = review {
        text.push_str(&format!(" ({review})"));
    }
    text
}

fn shell_arg(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '/' | '.' | '_' | '-' | ':'))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn safe_json_string_list(value: Option<&Value>, max_items: usize) -> Vec<String> {
    value
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(max_items)
                .filter_map(Value::as_str)
                .map(safe)
                .filter(|item| !item.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

fn truncate_chars(value: &str, max_chars: usize) -> (String, bool) {
    let count = value.chars().count();
    if count <= max_chars {
        return (value.to_string(), false);
    }
    (value.chars().take(max_chars).collect(), true)
}
