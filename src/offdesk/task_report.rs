//! Current-file review detail for queue tasks outside saved bounded shifts.

use std::collections::HashSet;

use anyhow::{ensure, Result};
use chrono::Utc;

use super::{
    failure_history::{ClosedFailureHistory, RetirementIndex},
    result_observation::{self, ResultObservation},
    task_queue::OffdeskTaskView,
    BackgroundProbe, BackgroundRunStore, BackgroundRunnerPhase, OffdeskShiftRecordV1,
    OffdeskTaskStatus, OffdeskTaskStore,
};

pub(crate) struct TaskReport {
    pub task: OffdeskTaskView,
    pub runtime_identity: &'static str,
    pub persisted_phase: Option<BackgroundRunnerPhase>,
    pub result: ResultObservation,
    pub closed_history: Option<ClosedFailureHistory>,
}

pub(crate) struct TaskReports {
    pub tasks: Vec<Report>,
    pub omitted: usize,
}

pub(crate) enum Report {
    Queue(Box<TaskReport>),
    RuntimeOnly(Box<BackgroundProbe>),
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) enum ReportIdentity {
    Queue(String, String, String, Option<String>),
    Runtime(String, Option<String>, Option<String>, Option<String>),
}

impl Report {
    pub(crate) fn identity(&self) -> ReportIdentity {
        match self {
            Self::Queue(report) => {
                let task = &report.task;
                ReportIdentity::Queue(
                    task.project_key.clone(),
                    task.task_id.clone(),
                    task.request_id.clone(),
                    task.background_ticket_id.clone(),
                )
            }
            Self::RuntimeOnly(probe) => ReportIdentity::Runtime(
                probe.ticket_id.clone(),
                probe.project_key.clone(),
                probe.task_id.clone(),
                probe.request_id.clone(),
            ),
        }
    }
}

pub(crate) fn load(profile: &str, shifts: &[OffdeskShiftRecordV1]) -> Result<TaskReports> {
    let root = crate::session::profile_dir_path(profile)?;
    let tasks = OffdeskTaskStore::new(&root).load()?;
    let probes = BackgroundRunStore::new(&root).load()?;
    ensure!(
        tasks.len() <= 1024 && probes.len() <= 4096,
        "Task report selection exceeds bounds"
    );
    let mut ids = HashSet::new();
    ensure!(
        tasks.iter().all(|task| ids.insert(&task.task_id)),
        "Duplicate task identity in report queue"
    );
    let mut tickets = HashSet::new();
    ensure!(
        probes.iter().all(|probe| tickets.insert(&probe.ticket_id)),
        "Duplicate runtime ticket identity in report store"
    );
    let bound: HashSet<_> = shifts
        .iter()
        .flat_map(|shift| {
            shift.manifest.task_bindings.iter().flat_map(|binding| {
                shift
                    .task_launch_reservations
                    .iter()
                    .filter_map(move |launch| {
                        (launch.task_id == binding.task_id).then_some((
                            &binding.project_key,
                            &binding.task_id,
                            &binding.request_id,
                            Some(launch.background_ticket_id.as_str()),
                            Some(binding.launch_spec_sha256.as_str()),
                        ))
                    })
            })
        })
        .collect();
    let mut selected: Vec<_> = tasks
        .iter()
        .filter(|task| {
            let launch_hash = super::offdesk_task_launch_spec_sha256(task).ok();
            !bound.contains(&(
                &task.project_key,
                &task.task_id,
                &task.request_id,
                task.background_ticket_id.as_deref(),
                launch_hash.as_deref(),
            )) && (task.attempt_count > 0
                || task.background_ticket_id.is_some()
                || matches!(
                    task.status,
                    OffdeskTaskStatus::Completed
                        | OffdeskTaskStatus::Failed
                        | OffdeskTaskStatus::ResumePending
                        | OffdeskTaskStatus::Cancelled
                ))
        })
        .collect();
    selected.sort_by(|a, b| {
        (&a.project_key, &a.task_id, &a.request_id).cmp(&(
            &b.project_key,
            &b.task_id,
            &b.request_id,
        ))
    });
    let queue_tickets: HashSet<_> = tasks
        .iter()
        .filter_map(|task| task.background_ticket_id.as_deref())
        .collect();
    let mut orphans: Vec<_> = probes
        .iter()
        .filter(|probe| {
            !queue_tickets.contains(probe.ticket_id.as_str())
                && !shifts.iter().any(|shift| {
                    shift
                        .task_launch_reservations
                        .iter()
                        .any(|launch| launch.background_ticket_id == probe.ticket_id)
                })
        })
        .collect();
    orphans.sort_by(|a, b| {
        (&a.project_key, &a.task_id, &a.ticket_id).cmp(&(&b.project_key, &b.task_id, &b.ticket_id))
    });
    let omitted = (selected.len() + orphans.len()).saturating_sub(256);
    let retirements = RetirementIndex::load(&root).unwrap_or_default();
    let mut reports: Vec<_> = selected
        .into_iter()
        .take(256)
        .map(|task| {
            let matching: Vec<_> = probes
                .iter()
                .filter(|probe| {
                    Some(probe.ticket_id.as_str()) == task.background_ticket_id.as_deref()
                })
                .collect();
            let (identity, exact) = match matching.as_slice() {
                [] => ("missing", None),
                [probe]
                    if probe.task_id.as_deref() == Some(&task.task_id)
                        && probe.request_id.as_deref() == Some(&task.request_id)
                        && probe.project_key.as_deref() == Some(&task.project_key)
                        && probe.capability_id.as_deref() == Some(&task.capability_id)
                        && probe.runner_kind == task.runner_kind
                        && probe.working_dir.as_deref() == Some(&task.workdir)
                        && probe.result_artifact_path == task.result_artifact_path =>
                {
                    ("exact_persisted_metadata", Some(*probe))
                }
                [_] => ("mismatch", None),
                _ => ("ambiguous", None),
            };
            let result = exact
                .and_then(|probe| {
                    task.result_artifact_path.as_ref().map(|path| {
                        result_observation::observe(
                            std::path::Path::new(&task.workdir),
                            &std::path::Path::new(&task.workdir).join(path),
                            &task.task_id,
                            &task.request_id,
                            &probe.ticket_id,
                        )
                    })
                })
                .unwrap_or_default();
            Report::Queue(Box::new(TaskReport {
                task: task.operator_view_with_profile(&root),
                runtime_identity: identity,
                persisted_phase: exact.map(|probe| probe.phase),
                result,
                closed_history: retirements.closed(&root, task, &probes, Utc::now()),
            }))
        })
        .collect();
    reports.extend(
        orphans
            .into_iter()
            .take(256 - reports.len())
            .map(|probe| Report::RuntimeOnly(Box::new(probe.clone()))),
    );
    Ok(TaskReports {
        tasks: reports,
        omitted,
    })
}
