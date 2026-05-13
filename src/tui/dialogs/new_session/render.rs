//! Rendering for NewSessionDialog

use ratatui::prelude::*;
use ratatui::widgets::*;

use super::{NewSessionDialog, FIELD_HELP, HELP_DIALOG_WIDTH, SPINNER_FRAMES};
use crate::tui::components::render_text_field;
use crate::tui::styles::Theme;

impl NewSessionDialog {
    pub fn render(&self, frame: &mut Frame, area: Rect, theme: &Theme) {
        // If loading, render the loading overlay instead
        if self.loading {
            self.render_loading(frame, area, theme);
            return;
        }

        let has_tool_selection = self.available_tools.len() > 1;
        let has_worktree = !self.worktree_branch.value().is_empty();
        let dialog_width = 80;

        // Build constraints dynamically based on visible fields only
        let mut constraints = vec![
            Constraint::Length(2), // Title
            Constraint::Length(2), // Path
            Constraint::Length(2), // Tool (always shown, interactive or not)
            Constraint::Length(2), // YOLO mode checkbox (always visible)
            Constraint::Length(2), // Worktree Branch
        ];
        if has_worktree {
            constraints.push(Constraint::Length(2)); // New Branch checkbox
        }
        constraints.push(Constraint::Length(2)); // Group (always, at the bottom)
        constraints.push(Constraint::Min(1)); // Hints/errors

        // Compute dialog height from actual constraints
        // border (2) + margin (2) + sum of field heights + hint line (2)
        let fields_height: u16 = constraints
            .iter()
            .map(|c| match c {
                Constraint::Length(n) => *n,
                Constraint::Min(n) => *n,
                _ => 0,
            })
            .sum();
        let dialog_height = fields_height + 4; // +2 border, +2 margin

        let dialog_area = crate::tui::dialogs::centered_rect(area, dialog_width, dialog_height);

        frame.render_widget(Clear, dialog_area);

        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme.accent))
            .title(" New Session ")
            .title_style(Style::default().fg(theme.title).bold());

        let inner = block.inner(dialog_area);
        frame.render_widget(block, dialog_area);

        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .margin(1)
            .constraints(constraints)
            .split(inner);

        // Render fields sequentially, tracking chunk index to match dynamic constraints
        let mut ci = 0; // chunk index

        // Title, Path (always visible)
        let path_placeholder = if self.focused_field == 1 {
            Some("(Ctrl+P to browse directories)")
        } else {
            None
        };

        let text_fields: [(&str, &tui_input::Input, Option<&str>); 2] = [
            ("Title:", &self.title, Some("(random civ)")),
            ("Path:", &self.path, path_placeholder),
        ];

        for (idx, (label, input, placeholder)) in text_fields.iter().enumerate() {
            render_text_field(
                frame,
                chunks[ci],
                label,
                input,
                idx == self.focused_field,
                *placeholder,
                theme,
            );
            ci += 1;
        }

        // Tool (always shown, interactive or read-only)
        let yolo_mode_field = if has_tool_selection { 3 } else { 2 };
        let worktree_field = yolo_mode_field + 1;
        let is_tool_focused = self.focused_field == 2;

        if has_tool_selection {
            let label_style = if is_tool_focused {
                Style::default().fg(theme.accent).underlined()
            } else {
                Style::default().fg(theme.text)
            };

            let mut tool_spans = vec![Span::styled("Tool:", label_style), Span::raw(" ")];

            for (idx, tool_name) in self.available_tools.iter().enumerate() {
                let is_selected = idx == self.tool_index;
                let style = if is_selected {
                    Style::default().fg(theme.accent).bold()
                } else {
                    Style::default().fg(theme.dimmed)
                };

                if idx > 0 {
                    tool_spans.push(Span::raw("  "));
                }
                tool_spans.push(Span::styled(if is_selected { "● " } else { "○ " }, style));
                tool_spans.push(Span::styled(*tool_name, style));
            }

            frame.render_widget(Paragraph::new(Line::from(tool_spans)), chunks[ci]);
        } else {
            let tool_style = Style::default().fg(theme.text);
            let tool_line = Line::from(vec![
                Span::styled("Tool:", tool_style),
                Span::raw(" "),
                Span::styled(self.available_tools[0], Style::default().fg(theme.accent)),
            ]);
            frame.render_widget(Paragraph::new(tool_line), chunks[ci]);
        }
        ci += 1;

        // YOLO Mode checkbox (always visible, right after tool)
        {
            let is_yolo_focused = self.focused_field == yolo_mode_field;
            let yolo_label_style = if is_yolo_focused {
                Style::default().fg(theme.accent).underlined()
            } else {
                Style::default().fg(theme.text)
            };

            let yolo_checkbox = if self.yolo_mode { "[x]" } else { "[ ]" };
            let yolo_checkbox_style = if self.yolo_mode {
                Style::default().fg(theme.accent).bold()
            } else {
                Style::default().fg(theme.dimmed)
            };

            let yolo_line = Line::from(vec![
                Span::styled("YOLO Mode:", yolo_label_style),
                Span::raw(" "),
                Span::styled(yolo_checkbox, yolo_checkbox_style),
                Span::styled(
                    " Skip permission prompts",
                    if self.yolo_mode {
                        Style::default().fg(theme.accent)
                    } else {
                        Style::default().fg(theme.dimmed)
                    },
                ),
            ]);
            frame.render_widget(Paragraph::new(yolo_line), chunks[ci]);
            ci += 1;
        }

        // Worktree Branch (always visible)
        let worktree_placeholder = if self.focused_field == worktree_field {
            Some("(leave empty to skip | Ctrl+P to browse branches)")
        } else {
            Some("(leave empty to skip worktree)")
        };
        render_text_field(
            frame,
            chunks[ci],
            "Worktree Branch:",
            &self.worktree_branch,
            self.focused_field == worktree_field,
            worktree_placeholder,
            theme,
        );
        ci += 1;

        // New Branch checkbox (only when worktree is set)
        let new_branch_field = worktree_field + 1;
        if has_worktree {
            let is_nb_focused = self.focused_field == new_branch_field;
            let nb_label_style = if is_nb_focused {
                Style::default().fg(theme.accent).underlined()
            } else {
                Style::default().fg(theme.text)
            };
            let checkbox = if self.create_new_branch { "[x]" } else { "[ ]" };
            let checkbox_style = if self.create_new_branch {
                Style::default().fg(theme.accent).bold()
            } else {
                Style::default().fg(theme.dimmed)
            };
            let nb_text = if self.create_new_branch {
                "Create new branch"
            } else {
                "Attach to existing branch"
            };
            let nb_line = Line::from(vec![
                Span::styled("New Branch:", nb_label_style),
                Span::raw(" "),
                Span::styled(checkbox, checkbox_style),
                Span::styled(
                    format!(" {}", nb_text),
                    if self.create_new_branch {
                        Style::default().fg(theme.accent)
                    } else {
                        Style::default().fg(theme.dimmed)
                    },
                ),
            ]);
            frame.render_widget(Paragraph::new(nb_line), chunks[ci]);
            ci += 1;
        }

        let next_field_idx = if has_worktree {
            new_branch_field + 1
        } else {
            worktree_field + 1
        };

        // Group (always visible, at the bottom before hints)
        let group_field = next_field_idx;
        let group_placeholder =
            if !self.existing_groups.is_empty() && self.focused_field == group_field {
                Some("(Ctrl+P to browse groups)")
            } else {
                None
            };
        render_text_field(
            frame,
            chunks[ci],
            "Group:",
            &self.group,
            self.focused_field == group_field,
            group_placeholder,
            theme,
        );
        ci += 1;

        // Hints/errors (last chunk)
        let hint_chunk = ci;
        if let Some(error) = &self.error_message {
            let error_text = format!("✗ Error: {}", error);
            let error_paragraph = Paragraph::new(error_text)
                .style(Style::default().fg(Color::Red))
                .wrap(Wrap { trim: true });
            frame.render_widget(error_paragraph, chunks[hint_chunk]);
        } else {
            let mut hint_spans = vec![
                Span::styled("Tab", Style::default().fg(theme.hint)),
                Span::raw(" next  "),
            ];
            if has_tool_selection {
                hint_spans.push(Span::styled("←/→", Style::default().fg(theme.hint)));
                hint_spans.push(Span::raw(" tool  "));
            }
            if self.focused_field == 1 {
                hint_spans.push(Span::styled("C-p", Style::default().fg(theme.hint)));
                hint_spans.push(Span::raw(" browse  "));
            }
            if self.focused_field == group_field && !self.existing_groups.is_empty() {
                hint_spans.push(Span::styled("C-p", Style::default().fg(theme.hint)));
                hint_spans.push(Span::raw(" groups  "));
            }
            if self.focused_field == worktree_field {
                hint_spans.push(Span::styled("C-p", Style::default().fg(theme.hint)));
                hint_spans.push(Span::raw(" branches  "));
            }
            hint_spans.push(Span::styled("Enter", Style::default().fg(theme.hint)));
            hint_spans.push(Span::raw(" create  "));
            hint_spans.push(Span::styled("?", Style::default().fg(theme.hint)));
            hint_spans.push(Span::raw(" help  "));
            hint_spans.push(Span::styled("Esc", Style::default().fg(theme.hint)));
            hint_spans.push(Span::raw(" cancel"));
            frame.render_widget(Paragraph::new(Line::from(hint_spans)), chunks[hint_chunk]);
        }

        if self.show_help {
            self.render_help_overlay(frame, area, theme);
        }

        if self.group_picker.is_active() {
            self.group_picker.render(frame, area, theme);
        }

        if self.branch_picker.is_active() {
            self.branch_picker.render(frame, area, theme);
        }

        if self.dir_picker.is_active() {
            self.dir_picker.render(frame, area, theme);
        }
    }

    fn render_help_overlay(&self, frame: &mut Frame, area: Rect, theme: &Theme) {
        let has_tool_selection = self.available_tools.len() > 1;

        let dialog_width: u16 = HELP_DIALOG_WIDTH;
        let base_height: u16 = 20; // includes YOLO Mode and Group (always visible)
        let dialog_height: u16 = base_height + if has_tool_selection { 3 } else { 0 };

        let dialog_area = crate::tui::dialogs::centered_rect(area, dialog_width, dialog_height);

        frame.render_widget(Clear, dialog_area);

        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme.border))
            .title(" New Session Help ")
            .title_style(Style::default().fg(theme.title).bold());

        let inner = block.inner(dialog_area);
        frame.render_widget(block, dialog_area);

        let mut lines: Vec<Line> = Vec::new();

        for (idx, help) in FIELD_HELP.iter().enumerate() {
            if idx == 2 && !has_tool_selection {
                continue;
            }

            lines.push(Line::from(Span::styled(
                help.name,
                Style::default().fg(theme.accent).bold(),
            )));
            lines.push(Line::from(Span::styled(
                format!("  {}", help.description),
                Style::default().fg(theme.text),
            )));
            lines.push(Line::from(""));
        }

        lines.push(Line::from(vec![
            Span::styled("Press ", Style::default().fg(theme.dimmed)),
            Span::styled("?", Style::default().fg(theme.hint)),
            Span::styled(" or ", Style::default().fg(theme.dimmed)),
            Span::styled("Esc", Style::default().fg(theme.hint)),
            Span::styled(" to close", Style::default().fg(theme.dimmed)),
        ]));

        frame.render_widget(Paragraph::new(lines), inner);
    }

    fn render_loading(&self, frame: &mut Frame, area: Rect, theme: &Theme) {
        let show_hook_output = self.has_hooks;
        let max_output_lines: usize = 6;

        let dialog_width: u16 = if show_hook_output { 70 } else { 50 };
        let dialog_height: u16 = if show_hook_output {
            // spinner line + command line + output lines + cancel hint + padding
            (6 + max_output_lines as u16).min(area.height)
        } else {
            7
        };

        let dialog_area = crate::tui::dialogs::centered_rect(area, dialog_width, dialog_height);

        frame.render_widget(Clear, dialog_area);

        let title = if show_hook_output {
            " Running Hooks "
        } else {
            " Creating Session "
        };

        let block = Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme.accent))
            .title(title)
            .title_style(Style::default().fg(theme.title).bold());

        let inner = block.inner(dialog_area);
        frame.render_widget(block, dialog_area);

        let spinner = SPINNER_FRAMES[self.spinner_frame];

        if show_hook_output {
            let mut lines = vec![];

            // Status line with spinner
            let status_text = if let Some(ref cmd) = self.current_hook {
                // Truncate long commands to fit the dialog
                let max_cmd_len = (dialog_width as usize).saturating_sub(12);
                if cmd.len() > max_cmd_len {
                    let truncated: String =
                        cmd.chars().take(max_cmd_len.saturating_sub(3)).collect();
                    format!("{}...", truncated)
                } else {
                    cmd.clone()
                }
            } else {
                "Preparing...".to_string()
            };

            lines.push(Line::from(vec![
                Span::styled(
                    format!(" {} ", spinner),
                    Style::default().fg(theme.accent).bold(),
                ),
                Span::styled(status_text, Style::default().fg(theme.text)),
            ]));

            // Show last N output lines
            let output_start = self.hook_output.len().saturating_sub(max_output_lines);
            let visible_lines = &self.hook_output[output_start..];
            let inner_width = (dialog_width as usize).saturating_sub(6);

            for line in visible_lines {
                let truncated = if line.len() > inner_width {
                    let t: String = line.chars().take(inner_width.saturating_sub(3)).collect();
                    format!("{}...", t)
                } else {
                    line.clone()
                };
                lines.push(Line::from(Span::styled(
                    format!("  {}", truncated),
                    Style::default().fg(theme.dimmed),
                )));
            }

            // Pad remaining lines so cancel hint stays at bottom
            let used = 1 + visible_lines.len(); // status + output
            let available = dialog_height.saturating_sub(4) as usize; // borders + cancel line
            for _ in used..available {
                lines.push(Line::from(""));
            }

            lines.push(Line::from(vec![
                Span::styled(" Press ", Style::default().fg(theme.dimmed)),
                Span::styled("Esc", Style::default().fg(theme.hint)),
                Span::styled(" to cancel", Style::default().fg(theme.dimmed)),
            ]));

            frame.render_widget(Paragraph::new(lines), inner);
        } else {
            let mut lines = vec![
                Line::from(""),
                Line::from(vec![
                    Span::styled(
                        format!("  {} ", spinner),
                        Style::default().fg(theme.accent).bold(),
                    ),
                    Span::styled("Creating session...", Style::default().fg(theme.text)),
                ]),
            ];

            lines.push(Line::from(""));
            lines.push(Line::from(vec![
                Span::styled("  Press ", Style::default().fg(theme.dimmed)),
                Span::styled("Esc", Style::default().fg(theme.hint)),
                Span::styled(" to cancel", Style::default().fg(theme.dimmed)),
            ]));

            frame.render_widget(Paragraph::new(lines), inner);
        }
    }
}
