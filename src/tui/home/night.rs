//! Local entry into the shared prepared-shift review and pending arm-request path.

use crossterm::event::{KeyCode, KeyEvent};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::Style;
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Frame;
use serde_json::Value;

use crate::cli::offdesk::local_review::{self, ReviewOptions};
use crate::offdesk::operator_safe_text;
use crate::tui::styles::Theme;

enum Confirmation {
    Request {
        hash: String,
    },
    Activate {
        hash: String,
        approval_id: String,
        acknowledge_unknown_usage: bool,
    },
}

pub struct NightView {
    profile: String,
    shifts: Vec<String>,
    cursor: usize,
    options: ReviewOptions,
    report: Option<Value>,
    message: String,
    confirmation: Option<Confirmation>,
    detail: bool,
    scroll: u16,
}

impl NightView {
    pub fn new(profile: &str) -> Self {
        let mut view = Self {
            profile: profile.into(),
            shifts: Vec::new(),
            cursor: 0,
            options: ReviewOptions::default(),
            report: None,
            message: String::new(),
            confirmation: None,
            detail: false,
            scroll: 0,
        };
        view.reload();
        view
    }

    fn reload(&mut self) {
        self.confirmation = None;
        self.report = None;
        self.scroll = 0;
        match local_review::list(&self.profile) {
            Ok(shifts) => {
                self.shifts = shifts;
                self.cursor = self.cursor.min(self.shifts.len().saturating_sub(1));
                self.inspect();
            }
            Err(error) => self.message = format!("Review unavailable: {error:#}"),
        }
    }

    fn inspect(&mut self) {
        self.confirmation = None;
        self.report = None;
        self.scroll = 0;
        let Some(id) = self.shifts.get(self.cursor) else {
            self.message = "No prepared shifts. Use shift propose, preview and prepare for exact reviewed work first.".into();
            return;
        };
        match local_review::inspect(&self.profile, id, &self.options) {
            Ok(report) => {
                self.report = Some(report);
                self.message =
                    "Review only. Saved preferences and scope overrides do not arm or launch work."
                        .into();
            }
            Err(error) => self.message = format!("Review held: {error:#}"),
        }
    }

    /// Returns true only to close this view. Enter and mouse input never attach a session.
    pub fn handle_key(&mut self, key: KeyEvent) -> bool {
        if self.confirmation.is_some() {
            match key.code {
                KeyCode::Char('y') => {
                    let confirmation = self.confirmation.take().expect("confirmation exists");
                    // Approval delivery may have succeeded even if its reply is unavailable.
                    // Require a fresh review after every attempt, including a refusal.
                    self.report = None;
                    if let Some(id) = self.shifts.get(self.cursor) {
                        self.message = match confirmation {
                            Confirmation::Request { hash } => match local_review::request_approval(&self.profile, id, &self.options, &hash) {
                            Ok(result) => format!("Approval {}: {}. Expires {}.\nNo arm or dispatch. Inspect offdesk pending; approve with offdesk ok <id>, then shift arm <shift-id> --approval-id <id>. Refresh before another request.", result["approval_id"], result["status"], result["expires_at"]),
                            Err(error) => format!("Request unresolved or refused: {error:#}. Inspect offdesk pending and shift show before any retry."),
                            },
                            Confirmation::Activate { hash, approval_id, acknowledge_unknown_usage } => {
                                match local_review::confirm_arm(&self.profile, id, &self.options, &hash, &approval_id, "local_tui", acknowledge_unknown_usage) {
                                    Ok(result) => format!("Shift {} armed with approval {}. No tick invoked; each task still needs fresh dispatch approval. Press r to inspect state.", result["shift_id"], result["approval_id"]),
                                    Err(error) => format!("Confirmation unresolved or refused: {error:#}. Inspect shift show and offdesk pending before any explicit retry; an approval may already have been recorded."),
                                }
                            }
                        };
                    }
                }
                _ => {
                    self.confirmation = None;
                    self.message = "Confirmation cancelled. No action performed.".into();
                }
            }
            return false;
        }
        match key.code {
            KeyCode::Esc | KeyCode::Char('q') | KeyCode::Char('N') => return true,
            KeyCode::Char('r') => self.reload(),
            KeyCode::Enter | KeyCode::Tab => self.detail = !self.detail,
            KeyCode::Up | KeyCode::Char('k') if self.detail => {
                self.scroll = self.scroll.saturating_sub(1)
            }
            KeyCode::Down | KeyCode::Char('j') if self.detail => {
                self.scroll = self.scroll.saturating_add(1)
            }
            KeyCode::Up | KeyCode::Char('k') => {
                self.cursor = self.cursor.saturating_sub(1);
                self.inspect();
            }
            KeyCode::Down | KeyCode::Char('j') => {
                self.cursor = (self.cursor + 1).min(self.shifts.len().saturating_sub(1));
                self.inspect();
            }
            KeyCode::Char('s') => {
                if let Some(report) = &self.report {
                    self.options.project_keys = Some(
                        report["projects"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .filter_map(|p| p.as_str().map(str::to_string))
                            .collect(),
                    );
                    self.inspect();
                }
            }
            KeyCode::Char('+') | KeyCode::Char('-') => {
                if let Some(ttl) = self
                    .report
                    .as_ref()
                    .and_then(|r| r["preferences"]["approval_ttl_minutes"].as_u64())
                {
                    self.options.approval_ttl_minutes = Some(if key.code == KeyCode::Char('+') {
                        ttl.saturating_add(5).min(1440)
                    } else {
                        ttl.saturating_sub(5).max(1)
                    });
                    self.inspect();
                }
            }
            KeyCode::Char('d') => {
                self.options = ReviewOptions::default();
                self.inspect();
            }
            KeyCode::Char('a') => {
                if let Some(report) = self.report.as_ref().filter(|r| r["requestable"] == true) {
                    self.confirmation =
                        report["review_sha256"]
                            .as_str()
                            .map(|hash| Confirmation::Request {
                                hash: hash.to_string(),
                            });
                    self.message = "Request a pending shift-arm approval for the displayed version? y confirms; any other key cancels. This does not approve, arm or dispatch tasks.".into();
                } else {
                    self.message =
                        "Request held. Review scope, source versions and window above.".into();
                }
            }
            KeyCode::Char('A') => {
                if let Some(report) = &self.report {
                    if let (Some(hash), Some(id)) = (
                        report["review_sha256"].as_str(),
                        report["activation"]["approval_id"].as_str(),
                    ) {
                        let unknown = report["activation"]
                            ["requires_unknown_task_usage_acknowledgement"]
                            == true;
                        self.confirmation = Some(Confirmation::Activate {
                            hash: hash.to_string(),
                            approval_id: id.to_string(),
                            acknowledge_unknown_usage: unknown,
                        });
                        self.message = if unknown {
                            "Task usage/cost UNKNOWN; no new spend cap. y accepts this and approves/activates the displayed shift. Task dispatch still requires its own approval. Any other key cancels."
                        } else {
                            "y approves/activates the displayed shift and exact arm approval. This does not invoke tick. Any other key cancels."
                        }.into();
                    } else {
                        self.message =
                            "Activation held. Create an arm request and refresh the review first."
                                .into();
                    }
                }
            }
            _ => {}
        }
        false
    }

    pub fn render(&mut self, frame: &mut Frame, area: Rect, theme: &Theme) {
        frame.render_widget(Clear, area);
        let block = Block::default()
            .title(" Night review: prepared shifts ")
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme.border));
        let inner = block.inner(area);
        frame.render_widget(block, area);
        let [list, detail, message, help] = Layout::vertical([
            Constraint::Length(3),
            Constraint::Min(1),
            Constraint::Length(4),
            Constraint::Length(2),
        ])
        .areas(inner);
        let items = self
            .shifts
            .iter()
            .map(|id| ListItem::new(operator_safe_text(id)))
            .collect::<Vec<_>>();
        let mut state =
            ListState::default().with_selected((!self.shifts.is_empty()).then_some(self.cursor));
        frame.render_stateful_widget(
            List::new(items)
                .highlight_symbol("> ")
                .highlight_style(Style::default().fg(theme.running)),
            list,
            &mut state,
        );
        let body = self
            .report
            .as_ref()
            .map(local_review::text)
            .unwrap_or_default();
        frame.render_widget(
            Paragraph::new(operator_safe_text(&body))
                .wrap(Wrap { trim: false })
                .scroll((self.scroll, 0)),
            detail,
        );
        frame.render_widget(
            Paragraph::new(operator_safe_text(&self.message))
                .wrap(Wrap { trim: false })
                .style(Style::default().fg(theme.waiting)),
            message,
        );
        frame.render_widget(Paragraph::new("j/k select | Enter scroll | s scope | +/- TTL | d defaults | r refresh\na request approval | A approve/activate exact review | Esc close").wrap(Wrap {trim:false}), help);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crossterm::event::KeyModifiers;
    use ratatui::{backend::TestBackend, Terminal};
    use serde_json::json;

    #[test]
    #[serial_test::serial]
    fn refused_confirmation_consumes_review_until_explicit_refresh() {
        struct RestoreEnv(Vec<(&'static str, Option<std::ffi::OsString>)>);
        impl Drop for RestoreEnv {
            fn drop(&mut self) {
                for (key, value) in &self.0 {
                    match value {
                        Some(value) => std::env::set_var(key, value),
                        None => std::env::remove_var(key),
                    }
                }
            }
        }
        let _restore = RestoreEnv(
            ["HOME", "XDG_CONFIG_HOME"]
                .into_iter()
                .map(|key| (key, std::env::var_os(key)))
                .collect(),
        );
        let home = tempfile::tempdir().unwrap();
        std::env::set_var("HOME", home.path());
        std::env::set_var("XDG_CONFIG_HOME", home.path().join(".config"));
        let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
        for action in ['a', 'A'] {
            let mut view = NightView::new("missing-confirmation-fixture");
            view.shifts = vec!["missing-shift".into()];
            view.report = Some(json!({
                "review_sha256":"fixture-version", "requestable":true,
                "activation":{"approval_id":"fixture-approval","requires_unknown_task_usage_acknowledgement":true}
            }));
            view.handle_key(key(KeyCode::Char(action)));
            assert!(view.confirmation.is_some());
            view.handle_key(key(KeyCode::Char('y')));
            assert!(view.message.contains("unresolved or refused"));
            assert!(view.confirmation.is_none());
            assert!(
                view.report.is_none(),
                "consumed {action} review remained actionable"
            );
            for repeated in ['y', 'a', 'A', 'y'] {
                view.handle_key(key(KeyCode::Char(repeated)));
                assert!(view.confirmation.is_none());
                assert!(view.report.is_none());
            }
            view.handle_key(key(KeyCode::Char('r')));
            assert!(view.report.is_none());
            assert!(view.confirmation.is_none());
            assert!(view.message.contains("No prepared shifts"));
        }
        assert!(std::fs::read_dir(home.path()).unwrap().next().is_none());
    }

    #[test]
    fn activation_warns_about_unknown_usage_and_cancels_without_touching_state() {
        let mut view = NightView {
            profile: "fixture-unused-profile".into(),
            shifts: vec!["fixture-shift".into()],
            cursor: 0,
            options: ReviewOptions::default(),
            report: Some(json!({
                "review_sha256":"fixture-review-version", "requestable":true,
                "activation":{"approval_id":"fixture-approval","requires_unknown_task_usage_acknowledgement":true}
            })),
            message: String::new(),
            confirmation: None,
            detail: false,
            scroll: 0,
        };
        let key = |code| KeyEvent::new(code, KeyModifiers::NONE);
        assert!(!view.handle_key(key(KeyCode::Char('A'))));
        assert!(matches!(
            view.confirmation,
            Some(Confirmation::Activate {
                acknowledge_unknown_usage: true,
                ..
            })
        ));
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        terminal
            .draw(|f| view.render(f, f.area(), &Theme::default()))
            .unwrap();
        let buffer = terminal.backend().buffer();
        let text = (0..24)
            .map(|y| (0..80).map(|x| buffer[(x, y)].symbol()).collect::<String>())
            .collect::<Vec<_>>()
            .join("\n");
        assert!(text.contains("Task usage/cost UNKNOWN; no new spend cap"));
        assert!(
            !view.handle_key(key(KeyCode::Char('A'))),
            "duplicate activation key must only cancel"
        );
        assert!(view.confirmation.is_none());
        assert!(view.report.is_some());
        assert!(view.handle_key(key(KeyCode::Esc)));
    }
}
