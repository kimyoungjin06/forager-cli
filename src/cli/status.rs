//! `forager status` command implementation

use anyhow::Result;
use clap::Args;
use serde::Serialize;

use crate::offdesk::{load_offdesk_status_summary, ResumeStatus, TaskResumeStore};
use crate::session::{get_profile_dir, Status, Storage};

#[derive(Args)]
pub struct StatusArgs {
    /// Show detailed session list
    #[arg(short = 'v', long)]
    verbose: bool,

    /// Only output waiting count (for scripts)
    #[arg(short = 'q', long)]
    quiet: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Default)]
struct StatusCounts {
    running: usize,
    waiting: usize,
    idle: usize,
    error: usize,
    total: usize,
}

#[derive(Serialize)]
struct StatusJson {
    waiting: usize,
    running: usize,
    idle: usize,
    error: usize,
    total: usize,
    resume_pending_fresh: usize,
    resume_pending_stale: usize,
    pending_approvals: usize,
    queued_offdesk_tasks: usize,
    active_offdesk_tasks: usize,
    offdesk_tasks_pending_approval: usize,
    failed_offdesk_tasks: usize,
    resume_pending_offdesk_tasks: usize,
    cancelled_offdesk_tasks: usize,
    stale_background_runs: usize,
    failed_background_runs: usize,
}

#[derive(Default)]
struct ResumeCounts {
    fresh_pending: usize,
    stale_pending: usize,
}

pub async fn run(profile: &str, args: StatusArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let (mut instances, _) = storage.load_with_groups()?;

    if instances.is_empty() {
        if args.json {
            let resume_counts = count_resume_state(storage.profile());
            let offdesk_summary = count_offdesk_state(storage.profile());
            let status_json = StatusJson {
                waiting: 0,
                running: 0,
                idle: 0,
                error: 0,
                total: 0,
                resume_pending_fresh: resume_counts.fresh_pending,
                resume_pending_stale: resume_counts.stale_pending,
                pending_approvals: offdesk_summary.pending_approvals,
                queued_offdesk_tasks: offdesk_summary.tasks.queued,
                active_offdesk_tasks: offdesk_summary.tasks.active,
                offdesk_tasks_pending_approval: offdesk_summary.tasks.pending_approval,
                failed_offdesk_tasks: offdesk_summary.tasks.failed,
                resume_pending_offdesk_tasks: offdesk_summary.tasks.resume_pending,
                cancelled_offdesk_tasks: offdesk_summary.tasks.cancelled,
                stale_background_runs: offdesk_summary.background_stale,
                failed_background_runs: offdesk_summary.background_failed,
            };
            println!("{}", serde_json::to_string(&status_json)?);
        } else if args.quiet {
            println!("0");
        } else {
            println!("No sessions in profile '{}'.", storage.profile());
            let resume_counts = count_resume_state(storage.profile());
            let offdesk_summary = count_offdesk_state(storage.profile());
            if resume_counts.fresh_pending > 0 || resume_counts.stale_pending > 0 {
                println!(
                    "{} fresh resume • {} stale resume",
                    resume_counts.fresh_pending, resume_counts.stale_pending
                );
            }
            print_offdesk_summary(&offdesk_summary);
        }
        return Ok(());
    }

    // Refresh tmux session cache
    crate::tmux::refresh_session_cache();

    // Update status for all instances
    for inst in &mut instances {
        inst.update_status();
    }

    let counts = count_by_status(&instances);
    let resume_counts = count_resume_state(storage.profile());
    let offdesk_summary = count_offdesk_state(storage.profile());

    if args.json {
        let status_json = StatusJson {
            waiting: counts.waiting,
            running: counts.running,
            idle: counts.idle,
            error: counts.error,
            total: counts.total,
            resume_pending_fresh: resume_counts.fresh_pending,
            resume_pending_stale: resume_counts.stale_pending,
            pending_approvals: offdesk_summary.pending_approvals,
            queued_offdesk_tasks: offdesk_summary.tasks.queued,
            active_offdesk_tasks: offdesk_summary.tasks.active,
            offdesk_tasks_pending_approval: offdesk_summary.tasks.pending_approval,
            failed_offdesk_tasks: offdesk_summary.tasks.failed,
            resume_pending_offdesk_tasks: offdesk_summary.tasks.resume_pending,
            cancelled_offdesk_tasks: offdesk_summary.tasks.cancelled,
            stale_background_runs: offdesk_summary.background_stale,
            failed_background_runs: offdesk_summary.background_failed,
        };
        println!("{}", serde_json::to_string(&status_json)?);
    } else if args.quiet {
        println!("{}", counts.waiting);
    } else if args.verbose {
        print_status_group("WAITING", "◐", Status::Waiting, &instances);
        print_status_group("RUNNING", "●", Status::Running, &instances);
        print_status_group("IDLE", "○", Status::Idle, &instances);
        print_status_group("ERROR", "✕", Status::Error, &instances);
        println!(
            "Total: {} sessions in profile '{}'",
            counts.total,
            storage.profile()
        );
    } else {
        println!(
            "{} waiting • {} running • {} idle",
            counts.waiting, counts.running, counts.idle
        );
        if resume_counts.fresh_pending > 0 || resume_counts.stale_pending > 0 {
            println!(
                "{} fresh resume • {} stale resume",
                resume_counts.fresh_pending, resume_counts.stale_pending
            );
        }
        print_offdesk_summary(&offdesk_summary);
    }

    // Show update notice if available (skip for JSON/quiet output)
    if !args.json && !args.quiet {
        crate::update::print_update_notice().await;
    }

    Ok(())
}

fn count_offdesk_state(profile: &str) -> crate::offdesk::OffdeskStatusSummary {
    let Ok(profile_dir) = get_profile_dir(profile) else {
        return crate::offdesk::OffdeskStatusSummary::default();
    };
    load_offdesk_status_summary(profile_dir, chrono::Utc::now()).unwrap_or_default()
}

fn print_offdesk_summary(summary: &crate::offdesk::OffdeskStatusSummary) {
    if summary.pending_approvals == 0
        && summary.tasks.queued == 0
        && summary.tasks.active == 0
        && summary.tasks.pending_approval == 0
        && summary.tasks.failed == 0
        && summary.tasks.resume_pending == 0
        && summary.background_stale == 0
        && summary.background_failed == 0
    {
        return;
    }
    println!(
        "{} approvals • {} queued offdesk • {} active offdesk • {} resume-pending offdesk • {} failed offdesk • {} stale background • {} failed background",
        summary.pending_approvals,
        summary.tasks.queued,
        summary.tasks.active + summary.tasks.pending_approval,
        summary.tasks.resume_pending,
        summary.tasks.failed,
        summary.background_stale,
        summary.background_failed
    );
    if summary.tasks.failed > 0 || summary.tasks.resume_pending > 0 {
        println!("Recovery: run `forager offdesk tasks` for retry, resume, and abandon commands.");
    }
}

fn count_resume_state(profile: &str) -> ResumeCounts {
    let Ok(profile_dir) = get_profile_dir(profile) else {
        return ResumeCounts::default();
    };
    let Ok(states) = TaskResumeStore::new(profile_dir).load() else {
        return ResumeCounts::default();
    };
    let now = chrono::Utc::now();
    states
        .iter()
        .filter(|state| state.status == ResumeStatus::ResumePending)
        .fold(ResumeCounts::default(), |mut counts, state| {
            if state.is_fresh_at(now) {
                counts.fresh_pending += 1;
            } else {
                counts.stale_pending += 1;
            }
            counts
        })
}

fn count_by_status(instances: &[crate::session::Instance]) -> StatusCounts {
    let mut counts = StatusCounts::default();
    for inst in instances {
        match inst.status {
            Status::Running => counts.running += 1,
            Status::Waiting => counts.waiting += 1,
            Status::Idle => counts.idle += 1,
            Status::Error => counts.error += 1,
            Status::Starting => counts.idle += 1,
            Status::Deleting => {}
        }
        counts.total += 1;
    }
    counts
}

fn print_status_group(
    label: &str,
    symbol: &str,
    status: Status,
    instances: &[crate::session::Instance],
) {
    let matching: Vec<_> = instances.iter().filter(|i| i.status == status).collect();
    if matching.is_empty() {
        return;
    }

    println!("{} ({}):", label, matching.len());
    for inst in matching {
        let path = shorten_path(&inst.project_path);
        println!("  {} {:<16} {:<10} {}", symbol, inst.title, inst.tool, path);
    }
    println!();
}

fn shorten_path(path: &str) -> String {
    if let Some(home) = dirs::home_dir() {
        if let Some(home_str) = home.to_str() {
            if let Some(stripped) = path.strip_prefix(home_str) {
                return format!("~{}", stripped);
            }
        }
    }
    path.to_string()
}
