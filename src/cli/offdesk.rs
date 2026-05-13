//! `forager offdesk` operator commands.

use anyhow::{bail, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Args, Subcommand};
use serde::Serialize;
use std::path::PathBuf;

use crate::offdesk::{
    default_capability_registry, launch_background_command, launch_background_run,
    poll_background_runs, run_offdesk_tick, ApprovalLedger, ApprovalStatus,
    BackgroundLaunchRequest, BackgroundProbe, BackgroundRecoveryDecision, BackgroundRunStore,
    BackgroundRunnerKind, CapabilityDescriptor, ExecutionBrief, LocalCommandLaunchSpec,
    OffdeskTask, OffdeskTaskInput, OffdeskTaskLifecycleReport, OffdeskTaskStatus, OffdeskTaskStore,
    OffdeskTaskView, OffdeskTickOptions, PendingActionApproval, ResumeStatus, SchedulerGate,
    SchedulerGateRequest, SchedulerGateStatus, TaskResumeState, TaskResumeStore,
};
use crate::session::get_profile_dir;

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
    Tasks(JsonArgs),

    /// Mark a durable task cancelled without stopping its background runner
    CancelTask(CancelTaskArgs),

    /// Requeue a failed, resume-pending, or cancelled durable task
    RetryTask(RetryTaskArgs),

    /// Accept recovery for a resume-pending task and requeue it
    ResumeTask(TaskLifecycleArgs),

    /// Discard a failed or resume-pending task
    AbandonTask(TaskLifecycleArgs),

    /// Poll background runner probes and persist phase transitions
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

#[derive(Serialize)]
struct BackgroundProbeStatus {
    probe: BackgroundProbe,
    decision: BackgroundRecoveryDecision,
}

#[derive(Serialize)]
struct RetryTaskLifecycleReport<'a> {
    #[serde(flatten)]
    report: &'a OffdeskTaskLifecycleReport,
    superseded_denied_approvals: usize,
}

pub async fn run(profile: &str, command: OffdeskCommands) -> Result<()> {
    match command {
        OffdeskCommands::Pending(args) => pending(profile, args).await,
        OffdeskCommands::Gate(args) => gate(profile, args).await,
        OffdeskCommands::Launch(args) => launch(profile, args).await,
        OffdeskCommands::Enqueue(args) => enqueue(profile, args).await,
        OffdeskCommands::Tick(args) => tick(profile, args).await,
        OffdeskCommands::Tasks(args) => tasks(profile, args).await,
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
    if report.skipped > 0 {
        println!("  skipped by limit: {}", report.skipped);
    }
    Ok(())
}

async fn tasks(profile: &str, args: JsonArgs) -> Result<()> {
    let task_views: Vec<OffdeskTaskView> = task_store(profile)?
        .load()?
        .into_iter()
        .map(|task| task.operator_view())
        .collect();

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
    request.preview = args.preview;
    request.reason = args.reason;
    request.source_surface = args.source_surface;
    request.ttl = Duration::minutes(args.ttl_minutes.max(1));

    let outcome = SchedulerGate::new(approval_ledger(profile)?).evaluate(
        request,
        brief.as_ref(),
        Utc::now(),
    )?;

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
    let json = args.json;
    let brief = load_execution_brief(args.brief.as_ref())?;
    let mut gate_request = SchedulerGateRequest::new(
        args.capability_id,
        args.project_key,
        args.request_id,
        args.task_id,
    );
    gate_request.mutation_class = args.mutation_class;
    gate_request.preview = args.preview;
    gate_request.reason = args.reason;
    gate_request.source_surface = args.source_surface;
    gate_request.ttl = Duration::minutes(args.ttl_minutes.max(1));

    let mut launch_request = BackgroundLaunchRequest::new(gate_request, args.runner);
    launch_request.ticket_id = args.ticket_id;
    launch_request.launch_spec_summary = args.launch_spec;
    launch_request.runtime_handle_alive = args.runtime_alive;
    launch_request.provider_launch_spec_reconstructable = args.provider_launch_spec_reconstructable;
    launch_request.ack_timeout_sec = args.ack_timeout_sec;

    let gate = SchedulerGate::new(approval_ledger(profile)?);
    let store = background_store(profile)?;
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

    if json {
        println!("{}", serde_json::to_string_pretty(&outcome)?);
        return Ok(());
    }

    print_gate_outcome(&outcome.gate);
    if let Some(probe) = outcome.probe {
        println!("  ticket_id: {}", probe.ticket_id);
        println!("  runner:    {:?}", probe.runner_kind);
        println!("  phase:     {:?}", probe.phase);
    }
    Ok(())
}

async fn poll(profile: &str, args: PollArgs) -> Result<()> {
    let notification_cooldown = args
        .notify_cooldown_minutes
        .map(|minutes| Duration::minutes(minutes.max(1)));
    let outcomes = poll_background_runs(
        &background_store(profile)?,
        args.ticket_id.as_deref(),
        Utc::now(),
        notification_cooldown,
    )?;

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

fn parse_background_runner_kind(value: &str) -> std::result::Result<BackgroundRunnerKind, String> {
    value.parse()
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
        }
    }
}

fn print_approvals(approvals: &[PendingActionApproval]) {
    println!(
        "{:<44} {:<10} {:<18} {:<24} ACTION",
        "APPROVAL ID", "STATUS", "RISK", "TASK"
    );
    for approval in approvals {
        println!(
            "{:<44} {:<10} {:<18} {:<24} {}",
            approval.approval_id,
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
        println!("  next:    {}", recommended_task_command(task));
    }
}

fn is_terminal_task_status(status: OffdeskTaskStatus) -> bool {
    matches!(
        status,
        OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled
    )
}

fn status_label(status: OffdeskTaskStatus) -> String {
    format!("{:?}", status).to_lowercase()
}

fn recommended_task_command(task: &OffdeskTaskView) -> String {
    match task.status {
        OffdeskTaskStatus::Failed => format!(
            "forager offdesk retry-task {} | forager offdesk retry-task {} --new-approval",
            task.task_id, task.task_id
        ),
        OffdeskTaskStatus::ResumePending => format!(
            "forager offdesk resume-task {} | forager offdesk retry-task {} | forager offdesk abandon-task {}",
            task.task_id, task.task_id, task.task_id
        ),
        OffdeskTaskStatus::Queued
        | OffdeskTaskStatus::PendingApproval
        | OffdeskTaskStatus::Launched
        | OffdeskTaskStatus::Running => {
            format!("forager offdesk cancel-task {}", task.task_id)
        }
        OffdeskTaskStatus::Completed | OffdeskTaskStatus::Cancelled => {
            "terminal: no action needed".to_string()
        }
    }
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
