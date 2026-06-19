//! Preview panel component

use ratatui::prelude::*;
use ratatui::widgets::*;

use crate::offdesk::operator_safe_text;
use crate::session::{Instance, Status};
use crate::tui::styles::Theme;

pub struct Preview;

impl Preview {
    pub fn render_terminal_preview(
        frame: &mut Frame,
        area: Rect,
        instance: &Instance,
        terminal_running: bool,
        cached_output: &str,
        theme: &Theme,
    ) {
        let info_height = if instance.sandbox_info.as_ref().is_some_and(|s| s.enabled) {
            5
        } else {
            4
        };
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(info_height), // Minimal info section
                Constraint::Min(1),              // Output section
            ])
            .split(area);

        // Minimal info for terminal view
        let mut info_lines = vec![
            Line::from(vec![
                Span::styled("Title:   ", Style::default().fg(theme.dimmed)),
                Span::styled(&instance.title, Style::default().fg(theme.text).bold()),
            ]),
            Line::from(vec![
                Span::styled("Path:    ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    shorten_path(&instance.project_path),
                    Style::default().fg(theme.text),
                ),
            ]),
            Line::from(vec![
                Span::styled("Status:  ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    if terminal_running {
                        "Running"
                    } else {
                        "Not started"
                    },
                    Style::default().fg(if terminal_running {
                        theme.terminal_active
                    } else {
                        theme.dimmed
                    }),
                ),
            ]),
        ];
        if let Some(sandbox) = &instance.sandbox_info {
            if sandbox.enabled {
                info_lines.push(Line::from(vec![
                    Span::styled("Legacy sandbox: ", Style::default().fg(theme.dimmed)),
                    Span::styled(&sandbox.container_name, Style::default().fg(Color::Magenta)),
                ]));
            }
        }
        let paragraph = Paragraph::new(info_lines);
        frame.render_widget(paragraph, chunks[0]);

        // Output section
        let block = Block::default()
            .borders(Borders::TOP)
            .border_style(Style::default().fg(theme.border))
            .title(" Terminal Output ")
            .title_style(Style::default().fg(theme.dimmed));

        let inner = block.inner(chunks[1]);
        frame.render_widget(block, chunks[1]);

        if !terminal_running {
            let hint = Paragraph::new("Press Enter to start terminal")
                .style(Style::default().fg(theme.dimmed))
                .alignment(Alignment::Center);
            frame.render_widget(hint, inner);
        } else if cached_output.is_empty() {
            let hint = Paragraph::new("No output available")
                .style(Style::default().fg(theme.dimmed))
                .alignment(Alignment::Center);
            frame.render_widget(hint, inner);
        } else {
            let output_lines: Vec<Line> = cached_output
                .lines()
                .map(|line| Line::from(Span::raw(line)))
                .collect();

            let line_count = output_lines.len();
            let visible_height = inner.height as usize;

            let scroll_offset = if line_count > visible_height {
                (line_count - visible_height) as u16
            } else {
                0
            };

            let paragraph = Paragraph::new(output_lines)
                .style(Style::default().fg(theme.text))
                .scroll((scroll_offset, 0));

            frame.render_widget(paragraph, inner);
        }
    }

    pub fn render_with_cache(
        frame: &mut Frame,
        area: Rect,
        instance: &Instance,
        cached_output: &str,
        theme: &Theme,
    ) {
        // Adjust height based on whether worktree info is present
        let info_height = if instance.worktree_info.is_some() {
            10 // Expanded to show worktree details
        } else {
            6 // Standard height
        };

        let summary = build_preview_summary(instance, cached_output);
        let show_summary = summary.is_some() && area.height >= info_height + 8;

        if show_summary {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(info_height), // Info section
                    Constraint::Length(5),           // Semantic state
                    Constraint::Min(1),              // Output section
                ])
                .split(area);

            Self::render_info(frame, chunks[0], instance, theme);
            Self::render_summary(frame, chunks[1], summary.as_ref().unwrap(), theme);
            Self::render_output_cached(frame, chunks[2], instance, cached_output, theme);
        } else {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(info_height), // Info section
                    Constraint::Min(1),              // Output section
                ])
                .split(area);

            Self::render_info(frame, chunks[0], instance, theme);
            Self::render_output_cached(frame, chunks[1], instance, cached_output, theme);
        }
    }

    fn render_info(frame: &mut Frame, area: Rect, instance: &Instance, theme: &Theme) {
        let mut info_lines = vec![
            Line::from(vec![
                Span::styled("Title:   ", Style::default().fg(theme.dimmed)),
                Span::styled(&instance.title, Style::default().fg(theme.text).bold()),
            ]),
            Line::from(vec![
                Span::styled("Path:    ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    shorten_path(&instance.project_path),
                    Style::default().fg(theme.text),
                ),
            ]),
            Line::from(vec![
                Span::styled("Tool:    ", Style::default().fg(theme.dimmed)),
                Span::styled(&instance.tool, Style::default().fg(theme.accent)),
            ]),
            Line::from(vec![
                Span::styled("Status:  ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    format!("{:?}", instance.status),
                    Style::default().fg(match instance.status {
                        crate::session::Status::Running => theme.running,
                        crate::session::Status::Waiting => theme.waiting,
                        crate::session::Status::Idle => theme.idle,
                        crate::session::Status::Stopped => theme.dimmed,
                        crate::session::Status::Error => theme.error,
                        crate::session::Status::Starting => theme.dimmed,
                        crate::session::Status::Deleting => theme.waiting,
                    }),
                ),
            ]),
            Line::from(vec![
                Span::styled("Group:   ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    if instance.group_path.is_empty() {
                        "(none)"
                    } else {
                        &instance.group_path
                    },
                    Style::default().fg(theme.group),
                ),
            ]),
        ];

        // Add worktree information if present
        if let Some(wt_info) = &instance.worktree_info {
            info_lines.push(Line::from(""));
            info_lines.push(Line::from(vec![
                Span::styled("─", Style::default().fg(theme.border)),
                Span::styled(" Worktree ", Style::default().fg(theme.dimmed)),
                Span::styled("─", Style::default().fg(theme.border)),
            ]));
            info_lines.push(Line::from(vec![
                Span::styled("Branch:  ", Style::default().fg(theme.dimmed)),
                Span::styled(&wt_info.branch, Style::default().fg(Color::Cyan)),
            ]));
            info_lines.push(Line::from(vec![
                Span::styled("Main:    ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    shorten_path(&wt_info.main_repo_path),
                    Style::default().fg(theme.text),
                ),
            ]));

            let managed_text = if wt_info.managed_by_forager {
                "Yes (delete branch on Forager session delete)"
            } else {
                "No (manual worktree)"
            };
            info_lines.push(Line::from(vec![
                Span::styled("Managed: ", Style::default().fg(theme.dimmed)),
                Span::styled(
                    managed_text,
                    Style::default().fg(if wt_info.managed_by_forager {
                        Color::Green
                    } else {
                        Color::Yellow
                    }),
                ),
            ]));
        }

        let paragraph = Paragraph::new(info_lines);
        frame.render_widget(paragraph, area);
    }

    fn render_summary(frame: &mut Frame, area: Rect, summary: &PreviewSummary, theme: &Theme) {
        let block = Block::default()
            .borders(Borders::TOP)
            .border_style(Style::default().fg(theme.border))
            .title(" Review Summary ")
            .title_style(Style::default().fg(theme.title));
        let inner = block.inner(area);
        frame.render_widget(block, area);

        let label_style = Style::default().fg(theme.dimmed);
        let state_style = Style::default().fg(summary.state_color(theme)).bold();
        let text_style = Style::default().fg(theme.text);
        let action_style = Style::default().fg(theme.accent).bold();

        let lines = vec![
            Line::from(vec![
                Span::styled("State:   ", label_style),
                Span::styled(&summary.state, state_style),
            ]),
            Line::from(vec![
                Span::styled("Focus:   ", label_style),
                Span::styled(&summary.focus, text_style),
            ]),
            Line::from(vec![
                Span::styled("Action:  ", label_style),
                Span::styled(&summary.action, action_style),
            ]),
            Line::from(vec![
                Span::styled("Artifact:", label_style),
                Span::raw(" "),
                Span::styled(&summary.artifact, text_style),
            ]),
        ];
        let paragraph = Paragraph::new(lines).wrap(Wrap { trim: true });
        frame.render_widget(paragraph, inner);
    }

    fn render_output_cached(
        frame: &mut Frame,
        area: Rect,
        instance: &Instance,
        cached_output: &str,
        theme: &Theme,
    ) {
        let block = Block::default()
            .borders(Borders::TOP)
            .border_style(Style::default().fg(theme.border))
            .title(" Output ")
            .title_style(Style::default().fg(theme.dimmed));

        let inner = block.inner(area);
        frame.render_widget(block, area);

        if let Some(error) = &instance.last_error {
            if !cached_output.is_empty() {
                Self::render_cached_output(frame, inner, cached_output, theme);
                return;
            }

            let error_lines: Vec<Line> = vec![
                Line::from(Span::styled(
                    "Error:",
                    Style::default().fg(theme.error).bold(),
                )),
                Line::from(""),
                Line::from(Span::styled(
                    error.as_str(),
                    Style::default().fg(theme.error),
                )),
            ];
            let paragraph = Paragraph::new(error_lines).wrap(Wrap { trim: false });
            frame.render_widget(paragraph, inner);
            return;
        }

        if cached_output.is_empty() {
            let hint = Paragraph::new("No output available")
                .style(Style::default().fg(theme.dimmed))
                .alignment(Alignment::Center);
            frame.render_widget(hint, inner);
        } else {
            Self::render_cached_output(frame, inner, cached_output, theme);
        }
    }

    fn render_cached_output(frame: &mut Frame, area: Rect, cached_output: &str, theme: &Theme) {
        let output_lines: Vec<Line> = cached_output
            .lines()
            .map(|line| Line::from(Span::raw(line)))
            .collect();

        let line_count = output_lines.len();
        let visible_height = area.height as usize;

        // Scroll to show the bottom of the content
        let scroll_offset = if line_count > visible_height {
            (line_count - visible_height) as u16
        } else {
            0
        };

        let paragraph = Paragraph::new(output_lines)
            .style(Style::default().fg(theme.text))
            .scroll((scroll_offset, 0));

        frame.render_widget(paragraph, area);
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PreviewSummary {
    severity: PreviewSummarySeverity,
    state: String,
    focus: String,
    action: String,
    artifact: String,
}

impl PreviewSummary {
    fn state_color(&self, theme: &Theme) -> Color {
        match self.severity {
            PreviewSummarySeverity::Error => theme.error,
            PreviewSummarySeverity::Waiting => theme.waiting,
            PreviewSummarySeverity::Active => theme.running,
            PreviewSummarySeverity::Info => theme.text,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PreviewSummarySeverity {
    Error,
    Waiting,
    Active,
    Info,
}

fn build_preview_summary(instance: &Instance, cached_output: &str) -> Option<PreviewSummary> {
    let output = cached_output.to_lowercase();
    let output_has_review_signal = output.contains("offdesk")
        || output.contains("approval")
        || output.contains("decision")
        || output.contains("closeout")
        || output.contains("blocked")
        || output.contains("failed")
        || output.contains("next action")
        || output.contains("next safe action");
    let has_review_state = instance.last_error.is_some()
        || matches!(
            instance.status,
            Status::Error | Status::Waiting | Status::Deleting
        )
        || output_has_review_signal;

    if !has_review_state {
        return None;
    }

    let (severity, state) = match instance.status {
        Status::Error => (PreviewSummarySeverity::Error, "Error requires review"),
        Status::Waiting => (
            PreviewSummarySeverity::Waiting,
            "Waiting for operator input",
        ),
        Status::Running | Status::Starting => (PreviewSummarySeverity::Active, "Running"),
        Status::Stopped => (PreviewSummarySeverity::Info, "Stopped; review before reuse"),
        Status::Idle => (PreviewSummarySeverity::Info, "Idle"),
        Status::Deleting => (PreviewSummarySeverity::Waiting, "Deleting"),
    };

    let focus = instance
        .last_error
        .as_deref()
        .map(clean_preview_line)
        .or_else(|| {
            find_output_line(
                cached_output,
                &["Decision:", "Approval:", "Blocked:", "Failed:"],
            )
        })
        .or_else(|| keyword_focus(&output))
        .unwrap_or_else(|| "No explicit blocker found in the preview output.".to_string());

    let action = find_output_line(
        cached_output,
        &["Next action:", "Next safe action:", "Action:", "다음 조치:"],
    )
    .unwrap_or_else(|| fallback_action(instance.status));

    let artifact = find_output_line(
        cached_output,
        &[
            "Artifact:",
            "Artifacts:",
            "Closeout:",
            "Review packet:",
            "Result:",
        ],
    )
    .unwrap_or_else(|| "Open raw output for full artifact details.".to_string());

    Some(PreviewSummary {
        severity,
        state: state.to_string(),
        focus,
        action,
        artifact,
    })
}

fn keyword_focus(output: &str) -> Option<String> {
    if output.contains("approval") {
        Some("Approval is mentioned in the latest output.".to_string())
    } else if output.contains("closeout") {
        Some("Closeout review appears in the latest output.".to_string())
    } else if output.contains("blocked") {
        Some("The latest output reports a blocked state.".to_string())
    } else if output.contains("failed") {
        Some("The latest output reports a failure.".to_string())
    } else if output.contains("decision") {
        Some("A decision point appears in the latest output.".to_string())
    } else {
        None
    }
}

fn fallback_action(status: Status) -> String {
    match status {
        Status::Error => "Inspect the error before restarting the session.".to_string(),
        Status::Waiting => "Answer the pending prompt or approval request.".to_string(),
        Status::Running | Status::Starting => {
            "Wait for the next checkpoint before intervening.".to_string()
        }
        Status::Stopped => "Review the final output before launching again.".to_string(),
        Status::Idle => "Start or attach when ready.".to_string(),
        Status::Deleting => "Wait for deletion to finish.".to_string(),
    }
}

fn find_output_line(output: &str, prefixes: &[&str]) -> Option<String> {
    output.lines().find_map(|line| {
        let trimmed = line.trim();
        prefixes.iter().find_map(|prefix| {
            trimmed
                .strip_prefix(prefix)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(clean_preview_line)
        })
    })
}

fn clean_preview_line(line: &str) -> String {
    let compact = operator_safe_text(line)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    truncate_chars(&compact, 120)
}

fn truncate_chars(value: &str, limit: usize) -> String {
    let mut chars = value.chars();
    let truncated: String = chars.by_ref().take(limit).collect();
    if chars.next().is_some() {
        format!("{truncated}...")
    } else {
        truncated
    }
}

fn shorten_path(path: &str) -> String {
    let path_buf = std::path::PathBuf::from(path);

    if let Some(home) = dirs::home_dir() {
        if let (Ok(canonical_path), Ok(canonical_home)) =
            (path_buf.canonicalize(), home.canonicalize())
        {
            if let Some(shortened) = format_home_relative(&canonical_path, &canonical_home, path) {
                return shortened;
            }
            return canonical_path.to_string_lossy().into_owned();
        }

        if let Some(shortened) = format_home_relative(&path_buf, &home, path) {
            return shortened;
        }
    }
    path.to_string()
}

fn format_home_relative(
    path: &std::path::Path,
    home: &std::path::Path,
    original: &str,
) -> Option<String> {
    let relative = path.strip_prefix(home).ok()?;
    let mut shortened = if relative.as_os_str().is_empty() {
        "~".to_string()
    } else {
        format!("~/{}", relative.to_string_lossy())
    };

    if has_trailing_separator(original) && shortened != "~" && !shortened.ends_with('/') {
        shortened.push('/');
    }

    Some(shortened)
}

fn has_trailing_separator(path: &str) -> bool {
    path.ends_with('/') || path.ends_with('\\')
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::{backend::TestBackend, buffer::Buffer, Terminal};
    use serial_test::serial;

    fn render_preview_text(
        instance: &Instance,
        cached_output: &str,
        width: u16,
        height: u16,
    ) -> String {
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).unwrap();
        terminal
            .draw(|frame| {
                Preview::render_with_cache(
                    frame,
                    frame.area(),
                    instance,
                    cached_output,
                    &Theme::default(),
                )
            })
            .unwrap();
        buffer_text(terminal.backend().buffer())
    }

    fn buffer_text(buffer: &Buffer) -> String {
        let mut text = String::new();
        for y in buffer.area.y..buffer.area.y + buffer.area.height {
            for x in buffer.area.x..buffer.area.x + buffer.area.width {
                if let Some(cell) = buffer.cell((x, y)) {
                    text.push_str(cell.symbol());
                }
            }
            text.push('\n');
        }
        text
    }

    #[test]
    fn preview_summary_detects_offdesk_next_action_before_raw_output() {
        let mut instance = Instance::new("overnight work", "/tmp/project");
        instance.status = Status::Stopped;
        let summary = build_preview_summary(
            &instance,
            "Closeout: 1 review pending\nNext action: Review: forager offdesk closeout\nArtifact: return package ready",
        )
        .expect("preview summary");

        assert_eq!(summary.state, "Stopped; review before reuse");
        assert_eq!(
            summary.focus,
            "Closeout review appears in the latest output."
        );
        assert_eq!(summary.action, "Review: forager offdesk closeout");
        assert_eq!(summary.artifact, "1 review pending");
    }

    #[test]
    fn preview_summary_skips_unrelated_idle_output() {
        let instance = Instance::new("notes", "/tmp/project");
        assert!(build_preview_summary(&instance, "hello world").is_none());
    }

    #[test]
    fn preview_summary_skips_plain_running_output() {
        let mut instance = Instance::new("active coding", "/tmp/project");
        instance.status = Status::Running;
        assert!(build_preview_summary(&instance, "compiling crate").is_none());
    }

    #[test]
    fn render_with_cache_surfaces_error_summary_and_keeps_raw_output_visible() {
        let mut instance = Instance::new("agent outage", "/tmp/project");
        instance.status = Status::Error;
        instance.last_error = Some("token=sk-secretsecretsecretsecret provider failed".to_string());

        let rendered = render_preview_text(
            &instance,
            "Next action: Review outage before retry\nArtifact: closeout receipt packet\nraw terminal line",
            96,
            24,
        );

        assert!(rendered.contains("Review Summary"));
        assert!(rendered.contains("Error requires review"));
        assert!(rendered.contains("[REDACTED]"));
        assert!(!rendered.contains("sk-secretsecretsecretsecret"));
        assert!(rendered.contains("Review outage before retry"));
        assert!(rendered.contains("closeout receipt packet"));
        assert!(rendered.contains("raw terminal line"));
    }

    #[test]
    #[serial]
    fn test_shorten_path_with_home() {
        if let Some(home) = dirs::home_dir() {
            if let Some(home_str) = home.to_str() {
                let path = format!("{}/projects/myapp", home_str);
                let shortened = shorten_path(&path);
                assert_eq!(shortened, "~/projects/myapp");
            }
        }
    }

    #[test]
    #[serial]
    fn test_shorten_path_without_home_prefix() {
        let path = "/tmp/some/path";
        let shortened = shorten_path(path);
        assert_eq!(shortened, "/tmp/some/path");
    }

    #[test]
    #[serial]
    fn test_shorten_path_exact_home() {
        if let Some(home) = dirs::home_dir() {
            if let Some(home_str) = home.to_str() {
                let shortened = shorten_path(home_str);
                assert_eq!(shortened, "~");
            }
        }
    }

    #[test]
    #[serial]
    fn test_shorten_path_relative() {
        let path = "relative/path";
        let shortened = shorten_path(path);
        assert_eq!(shortened, "relative/path");
    }

    #[test]
    #[serial]
    fn test_shorten_path_empty() {
        let path = "";
        let shortened = shorten_path(path);
        assert_eq!(shortened, "");
    }

    #[test]
    #[serial]
    fn test_shorten_path_similar_prefix_not_home() {
        if let Some(home) = dirs::home_dir() {
            if let Some(home_str) = home.to_str() {
                let path = format!("{}extra/not/home", home_str);
                let shortened = shorten_path(&path);
                assert_eq!(shortened, path);
            }
        }
    }

    #[test]
    #[serial]
    fn test_shorten_path_preserves_trailing_slash() {
        if let Some(home) = dirs::home_dir() {
            if let Some(home_str) = home.to_str() {
                let path = format!("{}/projects/", home_str);
                let shortened = shorten_path(&path);
                assert_eq!(shortened, "~/projects/");
            }
        }
    }
}
