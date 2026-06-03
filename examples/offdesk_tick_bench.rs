use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration as StdDuration, Instant};

use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use clap::{Parser, ValueEnum};
use forager::offdesk::{
    ApprovalLedger, ApprovalMode, ApprovalScope, ApprovalStatus, BackgroundRunnerKind, OffdeskTask,
    OffdeskTaskInput, OffdeskTaskStore, OffdeskTickOptions, OffdeskTickReport,
    PendingActionApproval, RiskLevel,
};
use serde::Serialize;
use uuid::Uuid;

const CAPABILITY_ID: &str = "dispatch.runtime";
const PROJECT_KEY: &str = "bench-project";
const REQUEST_ID: &str = "bench-request";

#[derive(Debug, Parser)]
#[command(
    name = "offdesk-tick-bench",
    about = "Benchmark offdesk tick approval-ledger scaling"
)]
struct Args {
    /// Comma-separated queued task counts.
    #[arg(long, value_delimiter = ',', default_value = "10,100,1000")]
    task_counts: Vec<usize>,

    /// Comma-separated approval row counts preloaded in the ledger.
    #[arg(long, value_delimiter = ',', default_value = "0,100,1000")]
    approval_rows: Vec<usize>,

    /// Scenario list. Defaults to all scenarios.
    #[arg(long, value_enum, value_delimiter = ',')]
    scenarios: Vec<Scenario>,

    /// Measured iterations per case.
    #[arg(long, default_value_t = 3)]
    iterations: usize,

    /// Warmup iterations per case.
    #[arg(long, default_value_t = 1)]
    warmups: usize,

    /// Use a small matrix intended for quick local checks.
    #[arg(long)]
    quick: bool,

    /// Path for the JSON result artifact.
    #[arg(long)]
    output: Option<PathBuf>,

    /// Keep generated offdesk state directories for inspection.
    #[arg(long)]
    keep_artifacts: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, ValueEnum)]
#[serde(rename_all = "snake_case")]
enum Scenario {
    /// No matching active approvals; tick creates pending approvals.
    CreatePending,
    /// Matching denied approvals force tasks back to failed.
    MatchDenied,
    /// Matching pending approvals keep tasks pending approval.
    MatchPending,
    /// Superseded non-matching rows create ledger noise before new pending approvals.
    SupersededNoise,
}

#[derive(Debug, Serialize)]
struct BenchOutput {
    generated_at: DateTime<Utc>,
    command: String,
    warmups: usize,
    iterations: usize,
    results: Vec<CaseResult>,
}

#[derive(Debug, Serialize)]
struct CaseResult {
    scenario: Scenario,
    task_count: usize,
    approval_rows: usize,
    matching_approvals: usize,
    warmups: usize,
    iterations: usize,
    min_ms: f64,
    median_ms: f64,
    p95_ms: f64,
    max_ms: f64,
    mean_ms: f64,
    samples_ms: Vec<f64>,
    report: TickReportSummary,
    approvals_bytes_before: u64,
    approvals_bytes_after: u64,
    tasks_bytes_before: u64,
    tasks_bytes_after: u64,
    artifact_dir: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
struct TickReportSummary {
    expired_approvals: usize,
    polled_background: usize,
    launched: usize,
    pending_approval: usize,
    completed: usize,
    failed: usize,
    resume_pending: usize,
    provider_deferred: usize,
    skipped: usize,
    stale_lock_replaced: bool,
    updated_task_ids: usize,
}

impl From<&OffdeskTickReport> for TickReportSummary {
    fn from(report: &OffdeskTickReport) -> Self {
        Self {
            expired_approvals: report.expired_approvals,
            polled_background: report.polled_background,
            launched: report.launched,
            pending_approval: report.pending_approval,
            completed: report.completed,
            failed: report.failed,
            resume_pending: report.resume_pending,
            provider_deferred: report.provider_deferred,
            skipped: report.skipped,
            stale_lock_replaced: report.stale_lock_replaced,
            updated_task_ids: report.updated_task_ids.len(),
        }
    }
}

#[derive(Debug)]
struct Sample {
    elapsed: StdDuration,
    report: OffdeskTickReport,
    approvals_bytes_before: u64,
    approvals_bytes_after: u64,
    tasks_bytes_before: u64,
    tasks_bytes_after: u64,
    artifact_dir: PathBuf,
}

fn main() -> Result<()> {
    let mut args = Args::parse();
    if args.quick {
        args.task_counts = vec![10, 100];
        args.approval_rows = vec![0, 100];
        args.iterations = args.iterations.min(2);
        args.warmups = args.warmups.min(1);
    }

    let scenarios = if args.scenarios.is_empty() {
        vec![
            Scenario::CreatePending,
            Scenario::MatchDenied,
            Scenario::MatchPending,
            Scenario::SupersededNoise,
        ]
    } else {
        args.scenarios.clone()
    };
    let iterations = args.iterations.max(1);
    let warmups = args.warmups;
    let output_path = args.output.unwrap_or_else(default_output_path);

    let mut results = Vec::new();
    for scenario in scenarios {
        for &task_count in &args.task_counts {
            for &approval_rows in &args.approval_rows {
                let result = bench_case(
                    scenario,
                    task_count,
                    approval_rows,
                    warmups,
                    iterations,
                    args.keep_artifacts,
                )?;
                print_case(&result);
                results.push(result);
            }
        }
    }

    let output = BenchOutput {
        generated_at: Utc::now(),
        command: std::env::args().collect::<Vec<_>>().join(" "),
        warmups,
        iterations,
        results,
    };
    if let Some(parent) = output_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&output_path, serde_json::to_string_pretty(&output)?)?;
    println!("wrote {}", output_path.display());
    Ok(())
}

fn bench_case(
    scenario: Scenario,
    task_count: usize,
    approval_rows: usize,
    warmups: usize,
    iterations: usize,
    keep_artifacts: bool,
) -> Result<CaseResult> {
    let mut measured = Vec::new();
    for sample_index in 0..(warmups + iterations) {
        let sample = run_sample(scenario, task_count, approval_rows)?;
        if sample_index >= warmups {
            measured.push(sample);
        } else {
            cleanup_sample(&sample, keep_artifacts)?;
        }
    }

    let first = measured
        .first()
        .expect("iterations is clamped to at least one measured sample");
    let samples_ms = measured
        .iter()
        .map(|sample| duration_ms(sample.elapsed))
        .collect::<Vec<_>>();
    let summary = summarize(&samples_ms);
    let artifact_dir = if keep_artifacts {
        Some(first.artifact_dir.clone())
    } else {
        None
    };
    for sample in &measured {
        cleanup_sample(sample, keep_artifacts)?;
    }

    Ok(CaseResult {
        scenario,
        task_count,
        approval_rows,
        matching_approvals: matching_approval_count(scenario, task_count, approval_rows),
        warmups,
        iterations,
        min_ms: summary.min_ms,
        median_ms: summary.median_ms,
        p95_ms: summary.p95_ms,
        max_ms: summary.max_ms,
        mean_ms: summary.mean_ms,
        samples_ms,
        report: TickReportSummary::from(&first.report),
        approvals_bytes_before: first.approvals_bytes_before,
        approvals_bytes_after: first.approvals_bytes_after,
        tasks_bytes_before: first.tasks_bytes_before,
        tasks_bytes_after: first.tasks_bytes_after,
        artifact_dir,
    })
}

fn run_sample(scenario: Scenario, task_count: usize, approval_rows: usize) -> Result<Sample> {
    let root = std::env::temp_dir().join(format!("forager-offdesk-bench-{}", Uuid::new_v4()));
    fs::create_dir_all(&root)?;
    let now = Utc::now();
    seed_tasks(&root, task_count, now)?;
    seed_approvals(&root, scenario, task_count, approval_rows, now)?;

    let approvals_path = root.join("pending_action_approvals.json");
    let tasks_path = root.join("offdesk_tasks.json");
    let approvals_bytes_before = file_size(&approvals_path);
    let tasks_bytes_before = file_size(&tasks_path);

    let mut options = OffdeskTickOptions::new(now + Duration::seconds(1));
    options.limit = task_count.max(1);
    let started = Instant::now();
    let report = forager::offdesk::run_offdesk_tick(&root, options)?;
    let elapsed = started.elapsed();

    Ok(Sample {
        elapsed,
        report,
        approvals_bytes_before,
        approvals_bytes_after: file_size(&approvals_path),
        tasks_bytes_before,
        tasks_bytes_after: file_size(&tasks_path),
        artifact_dir: root,
    })
}

fn seed_tasks(root: &Path, task_count: usize, now: DateTime<Utc>) -> Result<()> {
    let store = OffdeskTaskStore::new(root);
    let tasks = (0..task_count)
        .map(|index| {
            OffdeskTask::new(
                OffdeskTaskInput {
                    task_id: Some(task_id(index)),
                    request_id: REQUEST_ID.to_string(),
                    project_key: PROJECT_KEY.to_string(),
                    capability_id: CAPABILITY_ID.to_string(),
                    runner_kind: BackgroundRunnerKind::LocalBackground,
                    command: "true".to_string(),
                    workdir: root.to_string_lossy().into_owned(),
                    execution_brief: None,
                    not_before: None,
                    mutation_class: None,
                    artifact_refs: Vec::new(),
                    implementation_packet: None,
                    artifact_kind: None,
                    agent_mode: None,
                    provider_id: None,
                    model: None,
                    preview: String::new(),
                    reason: String::new(),
                    log_artifact_path: None,
                    result_artifact_path: None,
                },
                now,
            )
        })
        .collect::<Vec<_>>();
    store.save(&tasks)
}

fn seed_approvals(
    root: &Path,
    scenario: Scenario,
    task_count: usize,
    approval_rows: usize,
    now: DateTime<Utc>,
) -> Result<()> {
    let approvals = (0..approval_rows)
        .map(|index| {
            let status = match scenario {
                Scenario::CreatePending | Scenario::SupersededNoise => ApprovalStatus::Superseded,
                Scenario::MatchDenied => ApprovalStatus::Denied,
                Scenario::MatchPending => ApprovalStatus::Pending,
            };
            let matching = matches!(scenario, Scenario::MatchDenied | Scenario::MatchPending)
                && index < task_count;
            let task_id = if matching {
                task_id(index)
            } else {
                format!("noise_task_{index}")
            };
            approval(index, status, task_id, now)
        })
        .collect::<Vec<_>>();
    ApprovalLedger::new(root).save(&approvals)
}

fn approval(
    index: usize,
    status: ApprovalStatus,
    task_id: String,
    now: DateTime<Utc>,
) -> PendingActionApproval {
    PendingActionApproval {
        approval_id: format!("approval_{index}"),
        action_id: format!("action_{index}"),
        status,
        scope: ApprovalScope::Once,
        project_key: PROJECT_KEY.to_string(),
        request_id: REQUEST_ID.to_string(),
        task_id,
        action: CAPABILITY_ID.to_string(),
        risk_level: RiskLevel::RuntimeMutation,
        approval_mode: ApprovalMode::OperatorRequired,
        preview: String::new(),
        reason: String::new(),
        created_at: now,
        expires_at: now + Duration::minutes(30),
        resolved_at: if status == ApprovalStatus::Pending {
            None
        } else {
            Some(now)
        },
        resolved_by: if status == ApprovalStatus::Pending {
            None
        } else {
            Some("bench".to_string())
        },
        source_surface: "offdesk.tick.bench".to_string(),
        metadata: None,
    }
}

fn cleanup_sample(sample: &Sample, keep_artifacts: bool) -> Result<()> {
    if !keep_artifacts && sample.artifact_dir.exists() {
        fs::remove_dir_all(&sample.artifact_dir)?;
    }
    Ok(())
}

fn matching_approval_count(scenario: Scenario, task_count: usize, approval_rows: usize) -> usize {
    if matches!(scenario, Scenario::MatchDenied | Scenario::MatchPending) {
        task_count.min(approval_rows)
    } else {
        0
    }
}

fn task_id(index: usize) -> String {
    format!("task_{index:06}")
}

fn file_size(path: &Path) -> u64 {
    path.metadata().map_or(0, |metadata| metadata.len())
}

fn duration_ms(duration: StdDuration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

#[derive(Debug)]
struct Summary {
    min_ms: f64,
    median_ms: f64,
    p95_ms: f64,
    max_ms: f64,
    mean_ms: f64,
}

fn summarize(samples: &[f64]) -> Summary {
    let mut sorted = samples.to_vec();
    sorted.sort_by(|a, b| a.total_cmp(b));
    let sum = sorted.iter().sum::<f64>();
    Summary {
        min_ms: sorted[0],
        median_ms: percentile(&sorted, 0.50),
        p95_ms: percentile(&sorted, 0.95),
        max_ms: sorted[sorted.len() - 1],
        mean_ms: sum / sorted.len() as f64,
    }
}

fn percentile(sorted: &[f64], percentile: f64) -> f64 {
    let last = sorted.len() - 1;
    let rank = (last as f64 * percentile).ceil() as usize;
    sorted[rank.min(last)]
}

fn print_case(result: &CaseResult) {
    println!(
        "{:?} tasks={} approvals={} matched={} median={:.3}ms p95={:.3}ms report=launched:{} pending:{} failed:{} skipped:{}",
        result.scenario,
        result.task_count,
        result.approval_rows,
        result.matching_approvals,
        result.median_ms,
        result.p95_ms,
        result.report.launched,
        result.report.pending_approval,
        result.report.failed,
        result.report.skipped
    );
}

fn default_output_path() -> PathBuf {
    PathBuf::from("target")
        .join("offdesk-benchmarks")
        .join(format!(
            "offdesk_tick_bench_{}.json",
            Utc::now().format("%Y%m%dT%H%M%SZ")
        ))
}
