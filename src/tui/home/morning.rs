//! Read-only local detail for bounded shifts and tasks outside saved shifts.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::Style,
    text::Line,
    widgets::{Block, Borders, Clear, Paragraph, Wrap},
    Frame,
};
use unicode_width::UnicodeWidthStr;

use crate::offdesk::{
    build_offdesk_shift_review_projection, operator_safe_text, OffdeskShiftReviewProjectionV1,
    OffdeskShiftStore, OffdeskShiftTaskReviewV1,
};
use crate::tui::styles::Theme;

pub struct MorningView {
    profile: String,
    shifts: Vec<String>,
    selected_shift_id: Option<String>,
    shift_cursor: usize,
    task_cursor: Option<usize>,
    selected_task_identity: Option<(String, String)>,
    selected_manifest_sha256: Option<String>,
    report: Option<OffdeskShiftReviewProjectionV1>,
    message: String,
    scroll: u16,
    outside_shifts: bool,
    other_tasks: Vec<crate::offdesk::task_report::Report>,
    other_cursor: Option<usize>,
    other_identity: Option<crate::offdesk::task_report::ReportIdentity>,
    other_message: String,
    other_omitted: usize,
}

impl MorningView {
    pub(super) fn new(profile: &str) -> Self {
        let mut view = Self {
            profile: profile.into(),
            shifts: vec![],
            selected_shift_id: None,
            shift_cursor: 0,
            task_cursor: None,
            selected_task_identity: None,
            selected_manifest_sha256: None,
            report: None,
            message: String::new(),
            scroll: 0,
            outside_shifts: false,
            other_tasks: vec![],
            other_cursor: None,
            other_identity: None,
            other_message: String::new(),
            other_omitted: 0,
        };
        view.reload();
        view.outside_shifts = view.shifts.is_empty();
        view
    }

    fn reload(&mut self) {
        let selected = self.selected_shift_id.clone();
        self.scroll = 0;
        let snapshot = match OffdeskShiftStore::read_only(&self.profile).and_then(|s| s.snapshot())
        {
            Ok(snapshot) => snapshot,
            Err(error) => {
                self.report = None;
                self.other_tasks.clear();
                self.other_cursor = None;
                self.other_message = format!("Task reports unavailable: {error:#}");
                self.message = format!("Report unavailable: {error:#}");
                return;
            }
        };
        self.other_tasks.clear();
        self.other_cursor = None;
        self.other_omitted = 0;
        match crate::offdesk::task_report::load(&self.profile, &snapshot.records) {
            Ok(reports) => {
                self.other_tasks = reports.tasks;
                self.other_omitted = reports.omitted;
                if let Some(identity) = &self.other_identity {
                    self.other_cursor = self
                        .other_tasks
                        .iter()
                        .position(|report| &other_identity(report) == identity);
                    self.other_message = if self.other_cursor.is_some() {
                        String::new()
                    } else {
                        "Selected task/ticket disappeared or changed. Use Left/Right or reopen to choose another report.".into()
                    };
                } else if !self.other_tasks.is_empty() {
                    self.select_other(0);
                } else {
                    self.other_message = "No task reports outside saved shifts.".into();
                }
            }
            Err(error) => self.other_message = format!("Task reports held: {error:#}"),
        }
        let preferred = selected.clone().or_else(|| {
            snapshot
                .active()
                .or_else(|| snapshot.latest())
                .map(|record| record.manifest.shift_id.clone())
        });
        self.shifts = snapshot
            .records
            .iter()
            .rev()
            .map(|record| record.manifest.shift_id.clone())
            .collect();
        self.shift_cursor = preferred
            .as_ref()
            .and_then(|id| self.shifts.iter().position(|shift| shift == id))
            .unwrap_or(0);
        if selected.is_some() && !self.shifts.iter().any(|id| Some(id) == selected.as_ref()) {
            self.message =
                "Selected shift disappeared. Use [/] or reopen reports to select another run."
                    .into();
            self.report = None;
            return;
        }
        self.inspect();
    }

    fn select_other(&mut self, index: usize) {
        if let Some(report) = self.other_tasks.get(index) {
            self.other_cursor = Some(index);
            self.other_identity = Some(other_identity(report));
            self.other_message.clear();
        }
        self.scroll = 0;
    }

    fn inspect(&mut self) {
        self.report = None;
        self.task_cursor = None;
        self.scroll = 0;
        let Some(id) = self.shifts.get(self.shift_cursor) else {
            self.message =
                "No bounded shifts. Reports appear here after a shift is prepared.".into();
            return;
        };
        self.selected_shift_id = Some(id.clone());
        match build_offdesk_shift_review_projection(
            &self.profile,
            Some(id),
            None,
            chrono::Utc::now(),
        ) {
            Ok(Some(mut report)) => {
                if self
                    .selected_manifest_sha256
                    .as_ref()
                    .is_some_and(|hash| hash != &report.manifest_sha256)
                {
                    self.message = "Selected shift manifest changed. Use [/] or reopen to explicitly select the current version.".into();
                    return;
                }
                self.selected_manifest_sha256 = Some(report.manifest_sha256.clone());
                report.tasks.sort_by(|a, b| {
                    (&a.project_key, &a.task_id).cmp(&(&b.project_key, &b.task_id))
                });
                self.task_cursor = if let Some(key) = &self.selected_task_identity {
                    report
                        .tasks
                        .iter()
                        .position(|task| (&task.project_key, &task.task_id) == (&key.0, &key.1))
                } else {
                    (!report.tasks.is_empty()).then_some(0)
                };
                if let Some(task) = self.task_cursor.and_then(|index| report.tasks.get(index)) {
                    self.selected_task_identity =
                        Some((task.project_key.clone(), task.task_id.clone()));
                    self.message.clear();
                } else {
                    self.message = "Selected task is unavailable. Use Left/Right or reopen to choose another report.".into();
                }
                self.report = Some(report);
            }
            Ok(None) => self.message = "Selected shift report is unavailable.".into(),
            Err(error) => self.message = format!("Report held: {error:#}"),
        }
    }

    pub(super) fn handle_key(&mut self, key: KeyEvent) -> bool {
        match key.code {
            KeyCode::Esc | KeyCode::Char('q') | KeyCode::Char('M') => return true,
            KeyCode::Char('r') => self.reload(),
            KeyCode::Tab => {
                self.outside_shifts = !self.outside_shifts;
                self.scroll = 0;
            }
            KeyCode::Char('[') | KeyCode::Char(']') => {
                if self.outside_shifts {
                    return false;
                }
                self.shift_cursor = if key.code == KeyCode::Char('[') {
                    self.shift_cursor.saturating_sub(1)
                } else {
                    (self.shift_cursor + 1).min(self.shifts.len().saturating_sub(1))
                };
                self.task_cursor = None;
                self.selected_task_identity = None;
                self.selected_manifest_sha256 = None;
                self.inspect();
            }
            KeyCode::Left | KeyCode::Right => {
                if self.outside_shifts {
                    let index = self.other_cursor.map_or(0, |index| {
                        if key.code == KeyCode::Left {
                            index.saturating_sub(1)
                        } else {
                            (index + 1).min(self.other_tasks.len().saturating_sub(1))
                        }
                    });
                    self.select_other(index);
                    return false;
                }
                let count = self.report.as_ref().map_or(0, |report| report.tasks.len());
                self.task_cursor = (count > 0).then(|| {
                    self.task_cursor.map_or(0, |index| {
                        if key.code == KeyCode::Left {
                            index.saturating_sub(1)
                        } else {
                            (index + 1).min(count - 1)
                        }
                    })
                });
                if let Some(task) = self
                    .report
                    .as_ref()
                    .and_then(|report| self.task_cursor.and_then(|index| report.tasks.get(index)))
                {
                    self.selected_task_identity =
                        Some((task.project_key.clone(), task.task_id.clone()));
                    self.message.clear();
                }
                self.scroll = 0;
            }
            KeyCode::Up | KeyCode::Char('k') => self.scroll = self.scroll.saturating_sub(1),
            KeyCode::Down | KeyCode::Char('j') => self.scroll = self.scroll.saturating_add(1),
            KeyCode::PageUp => self.scroll = self.scroll.saturating_sub(10),
            KeyCode::PageDown => self.scroll = self.scroll.saturating_add(10),
            KeyCode::Home => self.scroll = 0,
            _ => {}
        }
        false
    }

    fn text(&self) -> String {
        if self.outside_shifts {
            let mut lines = vec![
                format!("Profile: {} | Tasks outside saved shifts", self.profile),
                "Read only. Current queue/runtime records and file observations; no historical shift snapshot.".into(),
                "Viewing does not resume, approve or update TODOs.".into(),
            ];
            if self.shifts.is_empty() {
                lines.push("No bounded shifts.".into());
            }
            if let Some((index, report)) = self
                .other_cursor
                .and_then(|index| self.other_tasks.get(index).map(|report| (index, report)))
            {
                lines.push(format!("Task {}/{}", index + 1, self.other_tasks.len()));
                lines.extend(other_task_lines(report));
            } else {
                lines.push(self.other_message.clone());
            }
            if self.other_omitted > 0 {
                lines.push(format!(
                    "{} additional task reports omitted by the selection bound.",
                    self.other_omitted
                ));
            }
            return operator_safe_text(&lines.join("\n"));
        }
        let Some(report) = &self.report else {
            return operator_safe_text(&self.message);
        };
        let mut lines = vec![
            format!("Profile: {} | Shift: {}", self.profile, report.shift_id),
            format!(
                "Observed: {} | Review: {}",
                report.generated_at, report.review_status
            ),
            format!(
                "Run state: {} | {}",
                report.lifecycle_status, report.lifecycle_reason
            ),
            "Read only. Viewing does not resume, approve or update TODOs.".into(),
            format!("Attention: {}", report.attention_reasons.join(", ")),
        ];
        if let Some((index, task)) = self
            .task_cursor
            .and_then(|index| report.tasks.get(index).map(|task| (index, task)))
        {
            lines.push(format!("Task {}/{}", index + 1, report.tasks.len()));
            lines.extend(task_lines(task));
        } else {
            lines.push(self.message.clone());
        }
        operator_safe_text(&lines.join("\n"))
    }

    pub(super) fn render(&mut self, frame: &mut Frame, area: Rect, theme: &Theme) {
        frame.render_widget(Clear, area);
        let block = Block::default()
            .title(" Offdesk report detail ")
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme.border));
        let inner = block.inner(area);
        frame.render_widget(block, area);
        let [body, help] =
            Layout::vertical([Constraint::Min(1), Constraint::Length(3)]).areas(inner);
        let lines = wrapped_lines(&self.text(), body.width);
        self.scroll = self.scroll.min(
            lines
                .len()
                .saturating_sub(usize::from(body.height))
                .min(usize::from(u16::MAX)) as u16,
        );
        let paragraph = Paragraph::new(lines.join("\n"));
        frame.render_widget(paragraph.scroll((self.scroll, 0)), body);
        frame.render_widget(
            Paragraph::new("Tab shift/other tasks | Left/Right task | [/] shift | j/k scroll\nr refresh | Esc close | No session input or task actions")
                .wrap(Wrap { trim: false }),
            help,
        );
    }
}

fn other_identity(
    report: &crate::offdesk::task_report::Report,
) -> crate::offdesk::task_report::ReportIdentity {
    report.identity()
}

fn other_task_lines(report: &crate::offdesk::task_report::Report) -> Vec<String> {
    let bounded = |text: &str| text.chars().take(2000).collect::<String>();
    let report = match report {
        crate::offdesk::task_report::Report::Queue(report) => report,
        crate::offdesk::task_report::Report::RuntimeOnly(probe) => return vec![
            format!("Unowned persisted runtime | Ticket: {}", bounded(&probe.ticket_id)),
            format!("Declared project: {} | Task: {} | Request: {}",
                bounded(probe.project_key.as_deref().unwrap_or("unknown")),
                bounded(probe.task_id.as_deref().unwrap_or("unknown")),
                bounded(probe.request_id.as_deref().unwrap_or("unknown"))),
            format!("Persisted phase: {:?} | Current process liveness: not checked", probe.phase),
            "Queue owner and saved shift: unavailable. Runtime metadata does not establish task ownership.".into(),
            format!("Declared result path: {}", bounded(probe.result_artifact_path.as_deref().unwrap_or("not recorded"))),
            "Result contents withheld until task ownership is recovered. No filesystem scan or process probe was performed.".into(),
            "Historical goal, original completion criteria and actual changes: not preserved or independently verified.".into(),
            "Task elapsed time: unknown | Task cost/tokens: unknown".into(),
            "Next action: recover exact queue/shift evidence before attributing results or authorizing any action.".into(),
        ],
    };
    let task = &report.task;
    let result = &report.result;
    let mut lines = vec![
        format!("Project: {} | Task: {}", task.project_key, task.task_id),
        format!(
            "Request: {} | Ticket: {}",
            task.request_id,
            task.background_ticket_id
                .as_deref()
                .unwrap_or("not recorded")
        ),
        format!(
            "Current queue state: {:?} | Attempt: {}",
            task.status, task.attempt_count
        ),
        format!(
            "Persisted runtime: {} | Identity: {}",
            report
                .persisted_phase
                .map(|phase| format!("{phase:?}"))
                .unwrap_or_else(|| "not recorded".into()),
            report.runtime_identity
        ),
        "Task elapsed time: unknown | Task cost/tokens: unknown".into(),
        "Historical goal and original completion criteria: not preserved in this current queue/runtime view.".into(),
        format!(
            "Task summary: {}",
            task.preview.chars().take(2000).collect::<String>()
        ),
        format!(
            "Current command: {}",
            task.command.chars().take(2000).collect::<String>()
        ),
        format!(
            "Result evidence: {} | Identity: {} | Reported status: {}",
            result.verification,
            result.identity,
            result.reported_status.as_deref().unwrap_or("unknown")
        ),
        "Actual changes and task-specific checks require independent evidence review.".into(),
    ];
    if let Some(history) = &report.closed_history {
        lines.push(format!(
            "Failure history: {} | Runtime: {}",
            history.state, history.runtime_evidence
        ));
        lines.push("Historical failure preserved. No resume or acceptance is authorized.".into());
    } else {
        lines.push(format!("Next action: {}", task.next_safe_action.detail));
        if matches!(
            report.persisted_phase,
            Some(crate::offdesk::BackgroundRunnerPhase::Failed)
        ) || report.runtime_identity != "exact_persisted_metadata"
            || result.identity != "self_reported_exact"
            || result.reported_status.as_deref() != Some("completed")
            || result.reported_exit_code.is_some_and(|code| code != 0)
            || result.evidence.iter().any(|entry| {
                entry.verification != "current_bytes_hash_match_not_claim_verification"
            })
        {
            lines.push("Review exact task/ticket evidence before acceptance or reuse.".into());
        }
    }
    if let Some(review) = &task.review {
        lines.push(format!(
            "Recorded agreement: {} | Validity: {}",
            review.state, review.validity
        ));
        lines.extend(
            review
                .remaining_work
                .iter()
                .map(|item| format!("Remaining work: {item}")),
        );
        lines.extend(
            review
                .unanswered_questions
                .iter()
                .map(|item| format!("Open question: {item}")),
        );
    }
    for entry in &result.evidence {
        lines.push(format!(
            "{}: {} | {} | {}",
            entry.kind, entry.description, entry.reference, entry.verification
        ));
    }
    if result.evidence_truncated {
        lines.push("Result evidence truncated by the shared observation bounds.".into());
    }
    lines
}

fn wrapped_lines(text: &str, width: u16) -> Vec<String> {
    if width == 0 {
        return vec![];
    }
    let mut lines = vec![];
    for line in text.lines() {
        let line = Line::from(line);
        let mut row = String::new();
        let mut used = 0;
        for grapheme in line.styled_graphemes(Style::default()) {
            let cells = grapheme.symbol.width();
            if used + cells > usize::from(width) && !row.is_empty() {
                lines.push(std::mem::take(&mut row));
                used = 0;
            }
            row.push_str(grapheme.symbol);
            used += cells;
        }
        lines.push(row);
    }
    lines
}

fn task_lines(task: &OffdeskShiftTaskReviewV1) -> Vec<String> {
    let report = &task.closeout_report;
    let observation = &report.result_observation;
    let mut lines = vec![
        format!("Project: {} | Task: {}", task.project_key, task.task_id),
        format!(
            "{}: {} | Review: {}",
            if report.closed_failure_history.is_some() {
                "Execution history"
            } else {
                "Execution"
            },
            report.execution_status,
            report.review_disposition
        ),
        format!(
            "Current queue state: {}",
            task.current_status
                .map(|state| format!("{state:?}"))
                .unwrap_or_else(|| "not recorded".into())
        ),
        format!(
            "Current queue ticket: {} | Attribution: {}",
            task.current_background_ticket_id
                .as_deref()
                .unwrap_or("not recorded"),
            task.current_attempt_attribution
        ),
        "Task elapsed time: unknown | Task cost/tokens: unknown".into(),
        format!(
            "Goal: {}",
            report.original_goal.as_deref().unwrap_or("not preserved")
        ),
        format!(
            "Scope evidence: {} | Current source: {}",
            report.historical_scope_status, report.source_status
        ),
        format!("Actual changes: {}", report.actual_changes_status),
        format!(
            "Result evidence: {} | Identity: {} | Reported status: {}",
            observation.verification,
            observation.identity,
            observation.reported_status.as_deref().unwrap_or("unknown")
        ),
    ];
    for criterion in &report.original_completion_criteria {
        lines.push(format!("Completion criterion: {criterion}"));
    }
    if let Some(history) = &report.closed_failure_history {
        lines.push(format!(
            "Failure history: {} | Runtime: {}",
            history.state, history.runtime_evidence
        ));
        lines.push(format!(
            "Retirement receipt: {} | Reviewed: {}",
            history.receipt_id, history.reviewed_at
        ));
        lines.push("Historical failure preserved. No acceptance, resume or resource release is authorized.".into());
    }
    if let Some(scope) = &report.requested_scope {
        lines.push(format!("Bound execution scope: {scope}"));
    }
    for attempt in &task.attempts {
        lines.push(format!(
            "Attempt {} | Ticket: {} | Outcome: {} | Validation: {} | Live identity: {}",
            attempt.attempt_number,
            attempt.background_ticket_id,
            attempt
                .outcome
                .map(|outcome| format!("{outcome:?}"))
                .unwrap_or_else(|| "not recorded".into()),
            attempt.validation_status,
            attempt.live_probe_identity
        ));
    }
    if let Some(review) = &report.recorded_review {
        lines.push(format!(
            "Recorded agreement: {} | Validity: {}",
            review.state, review.validity
        ));
        lines.push(format!(
            "Owning TODO: {} | Decision: {}",
            review.owning_plan, review.decision_ref
        ));
        for remaining in &review.remaining_work {
            lines.push(format!("Remaining work: {remaining}"));
        }
        for question in &review.unanswered_questions {
            lines.push(format!("Open question: {question}"));
        }
    }
    for check in &report.validation_checks {
        lines.push(format!("Check observation: {check}"));
    }
    for evidence in &observation.evidence {
        lines.push(format!(
            "{}: {} | {} | {}",
            evidence.kind, evidence.description, evidence.reference, evidence.verification
        ));
    }
    for reference in &report.evidence_references {
        lines.push(format!("Evidence reference: {reference}"));
    }
    for action in &report.next_actions {
        lines.push(format!("Next action: {action}"));
    }
    for limitation in &report.limitations {
        lines.push(format!("Limitation: {limitation}"));
    }
    if report.historical_scope_truncated
        || report.requested_scope_truncated
        || report.evidence_references_truncated
        || observation.evidence_truncated
    {
        lines.push(
            "Detail truncated by the shared report bounds; inspect bound evidence for full scope."
                .into(),
        );
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;
    use ratatui::{backend::TestBackend, Terminal};
    use serde_json::{json, Value};
    use serial_test::serial;
    use std::{collections::BTreeMap, fs, path::Path};
    use tempfile::TempDir;

    fn key(code: KeyCode) -> KeyEvent {
        KeyEvent::new(code, KeyModifiers::NONE)
    }

    fn unshifted_task(
        profile: &Path,
        project: &str,
        id: &str,
        status: &str,
    ) -> crate::offdesk::OffdeskTask {
        let task: crate::offdesk::OffdeskTask = serde_json::from_value(json!({
            "task_id":id,"request_id":format!("request-{id}"),"project_key":project,
            "status":"completed","capability_id":"dispatch.runtime","runner_kind":"local_background",
            "command":"fixture command token=sk-secretsecretsecretsecret","workdir":profile,
            "background_ticket_id":format!("ticket-{id}"),"attempt_count":1,
            "created_at":"2026-10-27T00:00:00Z","updated_at":"2026-10-27T00:01:00Z",
            "preview":format!("Fixture task {id}"),"reason":"saved task fixture",
            "result_artifact_path":format!("{id}.json")
        })).unwrap();
        let mut tasks = crate::offdesk::OffdeskTaskStore::new(profile)
            .load()
            .unwrap();
        tasks.push(task.clone());
        crate::offdesk::OffdeskTaskStore::new(profile)
            .save(&tasks)
            .unwrap();
        let mut probe = crate::offdesk::BackgroundProbe::new(
            format!("ticket-{id}"),
            crate::offdesk::BackgroundRunnerKind::LocalBackground,
        );
        probe.task_id = Some(id.into());
        probe.request_id = Some(format!("request-{id}"));
        probe.project_key = Some(project.into());
        probe.capability_id = Some("dispatch.runtime".into());
        probe.working_dir = Some(profile.display().to_string());
        probe.result_artifact_path = task.result_artifact_path.clone();
        probe.phase = crate::offdesk::BackgroundRunnerPhase::Completed;
        crate::offdesk::BackgroundRunStore::new(profile)
            .upsert(probe)
            .unwrap();
        fs::write(profile.join(format!("{id}.json")),serde_json::to_vec(&json!({
            "task_id":id,"request_id":format!("request-{id}"),"ticket_id":format!("ticket-{id}"),"status":status
        })).unwrap()).unwrap();
        task
    }

    #[test]
    #[serial]
    fn outside_shift_reports_reopen_and_preserve_contradictory_results_without_writes() {
        let (home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        fs::remove_file(&ledger).unwrap();
        unshifted_task(profile, "z-project", "outside-z", "failed");
        unshifted_task(profile, "a-project", "outside-a", "completed");
        let before = files(home.path());
        let mut view = MorningView::new("morning-fixture");
        assert!(view.outside_shifts);
        assert!(view.text().contains("Project: a-project | Task: outside-a"));
        assert!(view.text().contains("self_reported_exact"));
        assert!(!view.text().contains("sk-secret"));
        for width in [80, 120] {
            assert!(render(&mut view, width).contains("outside-a"));
        }
        view.handle_key(key(KeyCode::Right));
        assert!(view.text().contains("Project: z-project | Task: outside-z"));
        assert!(view.text().contains("Reported status: failed"));
        assert!(view.text().contains("Review exact task/ticket evidence"));
        for code in [
            KeyCode::Enter,
            KeyCode::Char('a'),
            KeyCode::Char('A'),
            KeyCode::Char('y'),
            KeyCode::Char('r'),
        ] {
            assert!(!view.handle_key(key(code)));
        }
        assert!(view.text().contains("outside-z"));
        drop(view);
        assert!(MorningView::new("morning-fixture")
            .text()
            .contains("outside-a"));
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    fn unlaunched_manifest_binding_does_not_hide_a_ticketed_task() {
        let (home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        unshifted_task(profile, "a-project", "task-a", "completed");
        unshifted_task(profile, "a-project", "outside-a", "completed");
        let before = files(home.path());
        let mut view = MorningView::new("morning-fixture");
        assert!(!view.outside_shifts);
        assert_eq!(view.other_tasks.len(), 2);
        view.handle_key(key(KeyCode::Tab));
        assert!(view.text().contains("Task: outside-a"));
        let selected = view.selected_shift_id.clone();
        view.handle_key(key(KeyCode::Char(']')));
        assert_eq!(view.selected_shift_id, selected);
        view.handle_key(key(KeyCode::Tab));
        assert!(view.text().contains("Shift: saved-shift"));
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    fn outside_shift_refresh_reobserves_evidence_and_holds_replaced_ticket() {
        let (_home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        fs::remove_file(&ledger).unwrap();
        let mut task = unshifted_task(profile, "a-project", "outside-a", "completed");
        let mut result: Value =
            serde_json::from_slice(&fs::read(profile.join("outside-a.json")).unwrap()).unwrap();
        use sha2::{Digest, Sha256};
        fs::write(profile.join("change.txt"), "original").unwrap();
        result["evidence"] = json!([{"kind":"change","path":"change.txt","sha256":format!("{:x}",Sha256::digest(b"original")),"description":"bounded change"}]);
        fs::write(
            profile.join("outside-a.json"),
            serde_json::to_vec(&result).unwrap(),
        )
        .unwrap();
        let mut view = MorningView::new("morning-fixture");
        assert!(view
            .text()
            .contains("current_bytes_hash_match_not_claim_verification"));
        fs::write(profile.join("change.txt"), "changed").unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("content_hash_mismatch"));
        fs::remove_file(profile.join("outside-a.json")).unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("unavailable_or_unsafe"));
        task.background_ticket_id = Some("replacement-ticket".into());
        crate::offdesk::OffdeskTaskStore::new(profile)
            .save(&[task])
            .unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view
            .text()
            .contains("Selected task/ticket disappeared or changed"));
        assert!(!view.text().contains("Current command:"));
        view.handle_key(key(KeyCode::Right));
        assert!(view.text().contains("replacement-ticket"));
        assert!(view.text().contains("Identity: missing"));
        assert!(!view.text().contains("Reported status: completed"));
    }

    #[test]
    #[serial]
    fn outside_shift_wrong_or_ambiguous_runtime_cannot_supply_result_content() {
        let (_home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        fs::remove_file(&ledger).unwrap();
        unshifted_task(profile, "a-project", "outside-a", "completed");
        let store = crate::offdesk::BackgroundRunStore::new(profile);
        let mut probes = store.load().unwrap();
        probes[0].project_key = Some("other-project".into());
        store.save(&probes).unwrap();
        let mut view = MorningView::new("morning-fixture");
        assert!(view.text().contains("Identity: mismatch"));
        assert!(!view.text().contains("Reported status: completed"));
        probes[0].project_key = Some("a-project".into());
        probes.push(probes[0].clone());
        store.save(&probes).unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Duplicate runtime ticket identity"));
        assert!(!view.text().contains("Reported status: completed"));
    }

    #[test]
    #[serial]
    fn outside_shift_invalid_queue_clears_old_detail_and_is_visible_without_shifts() {
        let (_home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        fs::remove_file(&ledger).unwrap();
        let task = unshifted_task(profile, "a-project", "outside-a", "completed");
        let mut view = MorningView::new("morning-fixture");
        assert!(view.text().contains("Current command:"));
        let store = crate::offdesk::OffdeskTaskStore::new(profile);
        fs::write(store.path(), b"invalid-json").unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Task reports held"));
        assert!(!view.text().contains("Current command:"));
        let reopened = MorningView::new("morning-fixture");
        assert!(reopened.text().contains("Task reports held"));
        fs::write(
            store.path(),
            serde_json::to_vec(&vec![task.clone(), task]).unwrap(),
        )
        .unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Duplicate task identity"));
    }

    fn fixture() -> (TempDir, std::path::PathBuf) {
        let home = TempDir::new().unwrap();
        std::env::set_var("HOME", home.path());
        std::env::set_var("XDG_CONFIG_HOME", home.path().join(".config"));
        let profile = crate::session::profile_dir_path("morning-fixture").unwrap();
        fs::create_dir_all(&profile).unwrap();
        let bindings: Vec<Value> = [("z-project", "task-z"), ("a-project", "task-a")]
            .into_iter()
            .map(|(project, task)| {
                json!({
                    "task_id":task,"request_id":format!("request-{task}"),"project_key":project,
                    "capability_id":"dispatch.runtime","runner_kind":"local_background",
                    "project_root_identity_sha256":"a".repeat(64),
                    "launch_spec_sha256":"b".repeat(64),"execution_brief_sha256":"c".repeat(64)
                })
            })
            .collect();
        let manifest = json!({
            "schema":"offdesk_shift_manifest.v1","shift_id":"saved-shift","profile":"morning-fixture",
            "created_at":"2026-08-29T00:00:00Z",
            "window":{"starts_at":"2026-08-29T00:00:00Z","stops_at":"2026-08-29T09:00:00Z","timezone":"Asia/Seoul"},
            "plan_id":"fixture-plan","plan_review_id":"fixture-review",
            "plan_source_sha256":"d".repeat(64),"plan_review_sha256":"e".repeat(64),
            "task_bindings":bindings,"routine_bindings":[],
            "budget":{"max_concurrent_tasks":1,"max_task_launches":2,"max_attempts_per_task":1,"max_routine_runs":0},
            "authority":{"task_dispatch_requires_fresh_approval":true,"task_launch_spec_is_immutable":true,
                "wiki_writes_are_candidate_only":true,"plan_revision_requires_review":true,"acceptance_requires_ondesk_review":true,
                "does_not_authorize":crate::offdesk::shift::OFFDESK_SHIFT_REQUIRED_DENIALS}
        });
        let ledger = profile.join("offdesk_shifts.v1.json");
        fs::write(
            &ledger,
            serde_json::to_vec_pretty(&json!({
                "schema":"offdesk_shift_ledger.v1","manifests":[manifest],
                "arm_receipts":[],"states":[],"transitions":[]
            }))
            .unwrap(),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&ledger, fs::Permissions::from_mode(0o600)).unwrap();
        }
        (home, ledger)
    }

    #[test]
    #[serial]
    fn selected_saved_task_survives_a_report_error_and_recovery() {
        let (home, ledger) = fixture();
        let mut view = MorningView::new("morning-fixture");
        view.handle_key(key(KeyCode::Right));
        assert!(view.text().contains("Task: task-z"));
        let queue = ledger.parent().unwrap().join("offdesk_tasks.json");
        fs::write(&queue, b"invalid queue").unwrap();
        let broken = files(home.path());
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Report held"));
        assert_eq!(files(home.path()), broken);
        fs::remove_file(queue).unwrap();
        let restored = files(home.path());
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Task: task-z"));
        assert_eq!(files(home.path()), restored);
    }

    #[test]
    #[serial]
    fn changed_manifest_is_held_even_across_a_ledger_error() {
        let (home, ledger) = fixture();
        let mut view = MorningView::new("morning-fixture");
        view.handle_key(key(KeyCode::Right));
        let mut replacement: Value = serde_json::from_slice(&fs::read(&ledger).unwrap()).unwrap();
        replacement["manifests"][0]["task_bindings"]
            .as_array_mut()
            .unwrap()
            .retain(|task| task["task_id"] != "task-z");
        replacement["manifests"][0]["budget"]["max_task_launches"] = json!(1);
        fs::write(&ledger, "broken ledger").unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.report.is_none());
        fs::write(&ledger, serde_json::to_vec(&replacement).unwrap()).unwrap();
        let before = files(home.path());
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.text().contains("Selected shift manifest changed"));
        assert!(!view.text().contains("Task: task-a"));
        view.handle_key(key(KeyCode::Char(']')));
        assert!(view.text().contains("Task: task-a"));
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    fn runtime_without_queue_owner_is_visible_but_its_result_is_withheld() {
        let (home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        let _task = unshifted_task(profile, "orphan-project", "orphan-task", "completed");
        crate::offdesk::OffdeskTaskStore::new(profile)
            .save(&[])
            .unwrap();
        let before = files(home.path());
        let mut view = MorningView::new("morning-fixture");
        view.handle_key(key(KeyCode::Tab));
        let text = view.text();
        assert!(text.contains("Unowned persisted runtime | Ticket: ticket-orphan-task"));
        assert!(text.contains("Queue owner and saved shift: unavailable"));
        assert!(text.contains("Result contents withheld"));
        assert!(!text.contains("Reported status: completed"));
        assert_eq!(files(home.path()), before);
        unshifted_task(profile, "orphan-project", "orphan-task", "completed");
        let recovered = files(home.path());
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view
            .text()
            .contains("Selected task/ticket disappeared or changed"));
        view.handle_key(key(KeyCode::Right));
        assert!(view.text().contains("Reported status: completed"));
        assert_eq!(files(home.path()), recovered);
    }

    #[test]
    #[serial]
    fn unowned_runtime_reports_share_the_selection_bound_and_allow_unknown_identity() {
        let (home, ledger) = fixture();
        let profile = ledger.parent().unwrap();
        let probes: Vec<_> = (0..300)
            .map(|index| {
                crate::offdesk::BackgroundProbe::new(
                    format!("unowned-{index:03}"),
                    crate::offdesk::BackgroundRunnerKind::LocalBackground,
                )
            })
            .collect();
        crate::offdesk::BackgroundRunStore::new(profile)
            .save(&probes)
            .unwrap();
        let before = files(home.path());
        let mut view = MorningView::new("morning-fixture");
        view.handle_key(key(KeyCode::Tab));
        assert_eq!(view.other_tasks.len(), 256);
        assert_eq!(view.other_omitted, 44);
        assert!(view.text().contains("Ticket: unowned-000"));
        assert!(view.text().contains("Declared project: unknown"));
        assert!(view.text().contains("44 additional task reports omitted"));
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    #[cfg(target_os = "linux")]
    fn saved_attempt_is_not_duplicated_when_its_queue_row_is_missing() {
        use crate::offdesk::{failure_history::tests::Fixture, BackgroundRunnerKind};
        let saved = Fixture::new(BackgroundRunnerKind::LocalBackground);
        saved.save_shift();
        std::env::set_var("HOME", saved.home.path());
        std::env::set_var("XDG_CONFIG_HOME", saved.home.path().join(".config"));
        assert!(MorningView::new("history-fixture").other_tasks.is_empty());
        crate::offdesk::OffdeskTaskStore::new(&saved.profile)
            .save(&[])
            .unwrap();
        let before = files(saved.home.path());
        let view = MorningView::new("history-fixture");
        assert!(view.other_tasks.is_empty());
        assert!(view.text().contains("Task: history-task"));
        assert!(view.text().contains("task_record_missing"));
        assert_eq!(files(saved.home.path()), before);
    }

    #[test]
    #[serial]
    #[cfg(target_os = "linux")]
    fn drifted_launch_spec_is_visible_separately_from_saved_scope() {
        use crate::offdesk::{failure_history::tests::Fixture, BackgroundRunnerKind};
        let mut saved = Fixture::new(BackgroundRunnerKind::LocalBackground);
        saved.save_shift();
        saved.task.command = "changed current command".into();
        saved.save();
        std::env::set_var("HOME", saved.home.path());
        std::env::set_var("XDG_CONFIG_HOME", saved.home.path().join(".config"));
        let before = files(saved.home.path());
        let mut view = MorningView::new("history-fixture");
        assert!(view.text().contains("task_record_drifted"));
        assert!(!view
            .text()
            .contains("Bound execution scope: changed current command"));
        view.handle_key(key(KeyCode::Tab));
        assert!(view
            .text()
            .contains("Current command: changed current command"));
        assert!(!view.text().contains("Failure history: retired_incomplete"));
        assert_eq!(files(saved.home.path()), before);
    }

    #[test]
    #[serial]
    #[cfg(target_os = "linux")]
    fn later_ticket_is_visible_without_relabeling_the_saved_attempt_result() {
        use crate::offdesk::{failure_history::tests::Fixture, BackgroundRunnerKind};
        let mut saved = Fixture::new(BackgroundRunnerKind::LocalBackground);
        saved.task.result_artifact_path = Some("result.json".into());
        saved.probe.result_artifact_path = saved.task.result_artifact_path.clone();
        saved.save();
        saved.save_shift();
        saved.task.background_ticket_id = Some("later-ticket".into());
        saved.task.attempt_count = 2;
        saved.save();
        let mut later = saved.probe.clone();
        later.ticket_id = "later-ticket".into();
        later.phase = crate::offdesk::BackgroundRunnerPhase::Completed;
        crate::offdesk::BackgroundRunStore::new(&saved.profile)
            .upsert(later)
            .unwrap();
        fs::write(
            Path::new(&saved.task.workdir).join("result.json"),
            br#"{"status":"completed","summary":"later result without identity"}"#,
        )
        .unwrap();
        std::env::set_var("HOME", saved.home.path());
        std::env::set_var("XDG_CONFIG_HOME", saved.home.path().join(".config"));
        let before = files(saved.home.path());
        let mut view = MorningView::new("history-fixture");
        let report = &view.report.as_ref().unwrap().tasks[0];
        assert!(
            report
                .closeout_report
                .result_observation
                .reported_status
                .is_none(),
            "a later file must not be attributed to the saved ticket"
        );
        assert!(view.text().contains("later-ticket"));
        view.handle_key(key(KeyCode::Tab));
        assert!(view.text().contains("Ticket: later-ticket"));
        assert!(view.text().contains("Reported status: completed"));
        assert!(!view.text().contains("Failure history: retired_incomplete"));
        assert_eq!(files(saved.home.path()), before);
    }

    #[test]
    #[serial]
    #[cfg(target_os = "linux")]
    fn outside_shift_retired_history_does_not_cover_a_later_attempt() {
        use crate::offdesk::failure_history::tests::Fixture;
        let mut saved = Fixture::new(crate::offdesk::BackgroundRunnerKind::LocalBackground);
        std::env::set_var("HOME", saved.home.path());
        std::env::set_var("XDG_CONFIG_HOME", saved.home.path().join(".config"));
        let before = files(saved.home.path());
        let mut view = MorningView::new("history-fixture");
        assert!(view.outside_shifts);
        assert!(view.text().contains("Failure history: retired_incomplete"));
        assert!(view.text().contains("Historical failure preserved"));
        assert_eq!(files(saved.home.path()), before);
        saved.task.attempt_count = 2;
        saved.save();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(!view.text().contains("Failure history: retired_incomplete"));
        assert!(view.text().contains("Persisted runtime: Failed"));
        assert!(view.text().contains("Review exact task/ticket evidence"));
    }

    fn files(root: &Path) -> BTreeMap<std::path::PathBuf, Vec<u8>> {
        let mut result = BTreeMap::new();
        for entry in fs::read_dir(root).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                result.insert(path.clone(), vec![]);
                result.extend(files(&path));
            } else {
                result.insert(path.clone(), fs::read(&path).unwrap());
            }
        }
        result
    }

    fn render(view: &mut MorningView, width: u16) -> String {
        let mut terminal = Terminal::new(TestBackend::new(width, 24)).unwrap();
        terminal
            .draw(|f| view.render(f, f.area(), &Theme::default()))
            .unwrap();
        let b = terminal.backend().buffer();
        (0..24)
            .map(|y| (0..width).map(|x| b[(x, y)].symbol()).collect::<String>())
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn wrapping_preserves_korean_and_combining_graphemes_at_small_widths() {
        let text = "가나다 e\u{301}🙂";
        for width in [2, 4, 80, 120] {
            let lines = wrapped_lines(text, width);
            assert_eq!(lines.concat(), text);
            assert!(lines.iter().all(|line| line.width() <= usize::from(width)));
            assert!(!lines.iter().any(|line| line.starts_with('\u{301}')));
        }
        assert!(wrapped_lines(text, 0).is_empty());
    }

    #[test]
    fn unavailable_report_errors_are_redacted_before_rendering() {
        let view = MorningView {
            profile: "fixture".into(),
            shifts: vec![],
            selected_shift_id: None,
            shift_cursor: 0,
            task_cursor: None,
            selected_task_identity: None,
            selected_manifest_sha256: None,
            report: None,
            scroll: 0,
            message: "Report unavailable: token=sk-secretsecretsecretsecret".into(),
            outside_shifts: false,
            other_tasks: vec![],
            other_cursor: None,
            other_identity: None,
            other_message: String::new(),
            other_omitted: 0,
        };
        let text = view.text();
        assert!(text.contains("Report unavailable"));
        assert!(!text.contains("sk-secret"));
    }

    #[test]
    #[serial]
    #[cfg(target_os = "linux")]
    fn retired_failure_reopens_as_history_but_live_runtime_still_needs_recovery() {
        use crate::offdesk::{failure_history::tests::Fixture, BackgroundRunnerKind};
        let mut f = Fixture::new(BackgroundRunnerKind::LocalTmux);
        f.save_shift();
        std::env::set_var("HOME", f.home.path());
        std::env::set_var("XDG_CONFIG_HOME", f.home.path().join(".config"));
        let before = files(f.home.path());
        let mut view = MorningView::new("history-fixture");
        let report = view.report.as_ref().expect("stored shift report");
        assert_eq!(report.review_status, "closed_failure_history");
        assert_eq!(report.summary.task_attempts_requiring_recovery, 0);
        assert_eq!(report.summary.task_attempts_closed_history, 1);
        assert_eq!(report.summary.task_attempts_completed, 0);
        assert_eq!(
            report.tasks[0].closeout_report.execution_status,
            "recovery_required"
        );
        assert!(!report.authority.accepted_truth_authorized);
        assert!(view
            .text()
            .contains("inspect_preserved_failure_history_no_resume"));
        assert!(view.text().contains("Execution history: recovery_required"));
        assert!(!view.text().contains("resolve_recovery_before_retry"));
        for width in [80, 120] {
            let rendered = render(&mut view, width);
            assert!(rendered.contains("Review: closed_failure_history"));
        }
        let summary = super::super::load_offdesk_summary("history-fixture");
        assert!(!summary.needs_operator_attention());
        assert_eq!(summary.focus_label(), "closed failure history");
        assert!(!summary.next_action_label().contains("Recover"));
        drop(view);
        let reopened = MorningView::new("history-fixture");
        assert_eq!(
            reopened.report.unwrap().review_status,
            "closed_failure_history"
        );
        assert_eq!(files(f.home.path()), before);

        f.probe.runtime_pid = Some(std::process::id());
        f.probe.runtime_identity = crate::process::process_identity(std::process::id());
        f.save();
        let mut held = MorningView::new("history-fixture");
        assert_eq!(
            held.report.as_ref().unwrap().review_status,
            "needs_attention"
        );
        assert_eq!(
            held.report
                .as_ref()
                .unwrap()
                .summary
                .task_attempts_closed_history,
            0
        );
        assert!(held
            .text()
            .contains("inspect_exact_attempt_and_resolve_recovery_before_retry"));
        let summary = super::super::load_offdesk_summary("history-fixture");
        assert!(summary.needs_operator_attention());
        assert!(summary.next_action_label().contains("Recover"));
        for width in [80, 120] {
            assert!(render(&mut held, width).contains("Review: needs_attention"));
        }
    }

    #[test]
    #[serial]
    fn opening_missing_profile_creates_no_profile_or_approval_state() {
        let home = TempDir::new().unwrap();
        std::env::set_var("HOME", home.path());
        std::env::set_var("XDG_CONFIG_HOME", home.path().join(".config"));
        let before = files(home.path());
        let mut view = MorningView::new("missing-profile");
        view.handle_key(key(KeyCode::Char('r')));
        render(&mut view, 80);
        assert!(view.report.is_none());
        assert!(!crate::session::profile_dir_path("missing-profile")
            .unwrap()
            .exists());
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    fn saved_reports_reopen_sort_projects_and_never_write_or_act() {
        let (home, _) = fixture();
        let before = files(home.path());
        let mut view = MorningView::new("morning-fixture");
        assert_eq!(view.report.as_ref().unwrap().shift_id, "saved-shift");
        assert!(view.text().contains("Project: a-project | Task: task-a"));
        for width in [80, 120] {
            let text = render(&mut view, width);
            assert!(text.contains("Offdesk report detail"));
            assert!(text.contains("Task elapsed time: unknown | Task cost/tokens: unknown"));
        }
        assert!(!view.handle_key(key(KeyCode::Right)));
        assert!(view.text().contains("Project: z-project | Task: task-z"));
        assert!(!view.handle_key(key(KeyCode::Char('r'))));
        assert!(view.text().contains("Project: z-project | Task: task-z"));
        for code in [
            KeyCode::Enter,
            KeyCode::Char('a'),
            KeyCode::Char('A'),
            KeyCode::Char('y'),
            KeyCode::Char('N'),
        ] {
            assert!(!view.handle_key(key(code)));
        }
        drop(view);
        let reopened = MorningView::new("morning-fixture");
        assert!(reopened
            .text()
            .contains("Project: a-project | Task: task-a"));
        assert_eq!(files(home.path()), before);
    }

    #[test]
    #[serial]
    fn refresh_refuses_removed_or_invalid_selected_run_without_old_detail() {
        let (_home, ledger) = fixture();
        let mut view = MorningView::new("morning-fixture");
        let mut saved: Value = serde_json::from_slice(&fs::read(&ledger).unwrap()).unwrap();
        let mut second = saved["manifests"][0].clone();
        second["shift_id"] = json!("other-shift");
        saved["manifests"].as_array_mut().unwrap().push(second);
        fs::write(&ledger, serde_json::to_vec(&saved).unwrap()).unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert_eq!(view.report.as_ref().unwrap().shift_id, "saved-shift");
        view.handle_key(key(KeyCode::Char(']')));
        assert_eq!(view.report.as_ref().unwrap().shift_id, "other-shift");
        fs::write(&ledger, b"invalid-json").unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.report.is_none());
        assert!(view.text().contains("Report unavailable"));
        assert!(!view.text().contains("Project: a-project"));
        saved["manifests"].as_array_mut().unwrap().pop();
        fs::write(&ledger, serde_json::to_vec(&saved).unwrap()).unwrap();
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.report.is_none());
        assert!(view.text().contains("Selected shift disappeared"));
        view.handle_key(key(KeyCode::Char('r')));
        assert!(view.report.is_none());
        assert!(view.text().contains("Selected shift disappeared"));
        view.handle_key(key(KeyCode::Char(']')));
        assert_eq!(view.report.as_ref().unwrap().shift_id, "saved-shift");
    }

    #[test]
    #[serial]
    fn evidence_drift_failure_and_recorded_review_stay_distinct() {
        let (home, _) = fixture();
        let mut view = MorningView::new("morning-fixture");
        let evidence = home.path().join("change.txt");
        fs::write(&evidence, "original").unwrap();
        use sha2::{Digest, Sha256};
        let result = home.path().join("result.json");
        fs::write(&result, serde_json::to_vec(&json!({
            "task_id":"task-a","request_id":"request-task-a","ticket_id":"ticket-a",
            "status":"failed","evidence":[{"kind":"change","path":"change.txt",
                "sha256":format!("{:x}",Sha256::digest(b"original")),"description":"token=sk-secretsecretsecretsecret"}]
        })).unwrap()).unwrap();
        let task = &mut view.report.as_mut().unwrap().tasks[0];
        task.closeout_report.result_observation = crate::offdesk::result_observation::observe(
            home.path(),
            &result,
            "task-a",
            "request-task-a",
            "ticket-a",
        );
        task.closeout_report.execution_status = "terminal_completed".into();
        task.closeout_report.review_disposition = "pending_user_review".into();
        task.closeout_report.recorded_review =
            Some(crate::offdesk::task_queue::TaskReviewProjection {
                state: "partial".into(),
                validity: "stale".into(),
                decision_ref: "fixture-only".into(),
                owning_plan: "TODO.md".into(),
                remaining_work: vec!["independent validation".into()],
                version: "fixture-version".into(),
                unanswered_questions: vec!["changed evidence?".into()],
            });
        let text = view.text();
        assert!(text.contains("Execution: terminal_completed | Review: pending_user_review"));
        assert!(text.contains("Reported status: failed"));
        assert!(text.contains("Recorded agreement: partial | Validity: stale"));
        assert!(text.contains("Remaining work: independent validation"));
        assert!(text.contains("current_bytes_hash_match_not_claim_verification"));
        assert!(!text.contains("sk-secret"));
        fs::write(&evidence, "changed").unwrap();
        view.report.as_mut().unwrap().tasks[0]
            .closeout_report
            .result_observation = crate::offdesk::result_observation::observe(
            home.path(),
            &result,
            "task-a",
            "request-task-a",
            "ticket-a",
        );
        assert!(view.text().contains("content_hash_mismatch"));
        fs::remove_file(&result).unwrap();
        view.report.as_mut().unwrap().tasks[0]
            .closeout_report
            .result_observation = crate::offdesk::result_observation::observe(
            home.path(),
            &result,
            "task-a",
            "request-task-a",
            "ticket-a",
        );
        assert!(view.text().contains("unavailable_or_unsafe"));
        view.scroll = u16::MAX;
        for width in [80, 120] {
            render(&mut view, width);
            assert!(view.scroll < u16::MAX);
        }
    }
}
