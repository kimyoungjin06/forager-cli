//! Tests for HomeView

use chrono::{Duration, Utc};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use ratatui::{backend::TestBackend, buffer::Buffer, Terminal};
use serde_json::json;
use serial_test::serial;
use std::fs;
use tempfile::TempDir;
use tui_input::Input;

use super::{HomeView, OffdeskResumeSummary, ViewMode};
use crate::offdesk::OffdeskNextSafeAction;
use crate::session::{Instance, Item, Storage};
use crate::tmux::AvailableTools;
use crate::tui::app::Action;
use crate::tui::dialogs::{InfoDialog, NewSessionDialog};
use crate::tui::styles::Theme;

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn key_with_mod(code: KeyCode, modifiers: KeyModifiers) -> KeyEvent {
    KeyEvent::new(code, modifiers)
}

fn setup_test_home(temp: &TempDir) {
    std::env::set_var("HOME", temp.path());
    #[cfg(target_os = "linux")]
    std::env::set_var("XDG_CONFIG_HOME", temp.path().join(".config"));
}

fn render_home_text(view: &mut HomeView, width: u16, height: u16) -> String {
    let backend = TestBackend::new(width, height);
    let mut terminal = Terminal::new(backend).unwrap();
    terminal
        .draw(|frame| view.render(frame, frame.area(), &Theme::default(), None))
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

struct TestEnv {
    _temp: TempDir,
    view: HomeView,
}

fn create_test_env_empty() -> TestEnv {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();
    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();
    TestEnv { _temp: temp, view }
}

fn create_test_env_with_sessions(count: usize) -> TestEnv {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();
    let mut instances = Vec::new();
    for i in 0..count {
        instances.push(Instance::new(
            &format!("session{}", i),
            &format!("/tmp/{}", i),
        ));
    }
    storage.save(&instances).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();
    TestEnv { _temp: temp, view }
}

fn create_test_env_with_groups() -> TestEnv {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();
    let mut instances = Vec::new();

    let inst1 = Instance::new("ungrouped", "/tmp/u");
    instances.push(inst1);

    let mut inst2 = Instance::new("work-project", "/tmp/work");
    inst2.group_path = "work".to_string();
    instances.push(inst2);

    let mut inst3 = Instance::new("personal-project", "/tmp/personal");
    inst3.group_path = "personal".to_string();
    instances.push(inst3);

    storage.save(&instances).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();
    TestEnv { _temp: temp, view }
}

#[test]
#[serial]
fn load_offdesk_summary_counts_recovery_tasks() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let profile_dir = crate::session::get_profile_dir("test").unwrap();
    fs::create_dir_all(&profile_dir).unwrap();
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "failed-task",
                "request_id": "request",
                "project_key": "project",
                "status": "failed",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": "true",
                "workdir": "/tmp",
                "created_at": now,
                "updated_at": now
            },
            {
                "task_id": "resume-task",
                "request_id": "request",
                "project_key": "project",
                "status": "resume_pending",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": "true",
                "workdir": "/tmp",
                "created_at": now,
                "updated_at": now
            },
            {
                "task_id": "cancelled-task",
                "request_id": "request",
                "project_key": "project",
                "status": "cancelled",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": "true",
                "workdir": "/tmp",
                "created_at": now,
                "updated_at": now
            }
        ]))
        .unwrap(),
    )
    .unwrap();

    let summary = super::load_offdesk_summary("test");

    assert_eq!(summary.failed_tasks, 1);
    assert_eq!(summary.resume_pending_tasks, 1);
    assert_eq!(summary.cancelled_tasks, 1);
    assert!(summary.needs_operator_attention());
    assert_eq!(summary.next_safe_actions[0].kind, "recovery_required");
    assert_eq!(
        summary.next_action_label(),
        "Recover: forager offdesk resume"
    );
}

#[test]
#[serial]
fn load_offdesk_summary_counts_completed_tasks_needing_closeout() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let profile_dir = crate::session::get_profile_dir("test").unwrap();
    fs::create_dir_all(&profile_dir).unwrap();
    let now = Utc::now();
    fs::write(
        profile_dir.join("offdesk_tasks.json"),
        serde_json::to_string_pretty(&json!([
            {
                "task_id": "completed-task",
                "request_id": "request",
                "project_key": "project",
                "status": "completed",
                "capability_id": "dispatch.runtime",
                "runner_kind": "local_background",
                "command": "true",
                "workdir": "/tmp",
                "created_at": now,
                "updated_at": now
            }
        ]))
        .unwrap(),
    )
    .unwrap();

    let summary = super::load_offdesk_summary("test");
    assert_eq!(summary.closeout_required, 1);
    assert!(summary.needs_operator_attention());
    assert_eq!(summary.focus_label(), "closeout required");
    assert_eq!(
        summary.next_action_label(),
        "Review: forager offdesk closeout"
    );
    assert_eq!(summary.next_safe_actions[0].kind, "review_required");

    let closeout_dir = profile_dir.join("offdesk_closeouts").join("latest");
    fs::create_dir_all(&closeout_dir).unwrap();
    fs::write(
        closeout_dir.join("closeout_plan.json"),
        serde_json::to_string_pretty(&json!({
            "generated_at": now + Duration::minutes(1)
        }))
        .unwrap(),
    )
    .unwrap();

    let summary = super::load_offdesk_summary("test");
    assert_eq!(summary.closeout_required, 1);
    assert!(summary.needs_operator_attention());

    fs::write(
        closeout_dir.join("closeout_review_20260521T000000Z.json"),
        serde_json::to_string_pretty(&json!({
            "reviewed_at": now + Duration::minutes(2),
            "verdict": "approved",
            "applies_to_tasks": [
                {
                    "project_key": "project",
                    "request_id": "request",
                    "task_id": "completed-task"
                }
            ]
        }))
        .unwrap(),
    )
    .unwrap();

    let summary = super::load_offdesk_summary("test");
    assert_eq!(summary.closeout_required, 0);
    assert!(!summary.needs_operator_attention());
}

#[test]
fn offdesk_attention_includes_failed_and_resume_pending_tasks() {
    assert!(OffdeskResumeSummary {
        failed_tasks: 1,
        ..OffdeskResumeSummary::default()
    }
    .needs_operator_attention());
    assert!(OffdeskResumeSummary {
        resume_pending_tasks: 1,
        ..OffdeskResumeSummary::default()
    }
    .needs_operator_attention());
}

#[test]
#[serial]
fn load_offdesk_summary_adds_next_action_for_resume_store_only() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let profile_dir = crate::session::get_profile_dir("test").unwrap();
    fs::create_dir_all(&profile_dir).unwrap();
    let now = Utc::now();
    fs::write(
        profile_dir.join("task_resume_state.json"),
        serde_json::to_string_pretty(&json!([
            {
                "resume_id": "resume-test",
                "task_id": "task",
                "request_id": "request",
                "project_key": "project",
                "status": "resume_pending",
                "phase": "launched",
                "runner_target": "bg_task",
                "next_safe_resume_step": "inspect latest heartbeat before continuing",
                "interrupted_at": now,
                "interruption_reason": "test",
                "fresh_until": now + Duration::minutes(10)
            }
        ]))
        .unwrap(),
    )
    .unwrap();

    let summary = super::load_offdesk_summary("test");

    assert_eq!(summary.fresh_pending, 1);
    assert_eq!(summary.next_safe_actions[0].kind, "resume_review_required");
    assert_eq!(
        summary.next_action_label(),
        "Recover: forager offdesk resume"
    );
}

#[test]
fn offdesk_morning_review_includes_cancelled_and_resume_states() {
    let summary = OffdeskResumeSummary {
        cancelled_tasks: 1,
        ..OffdeskResumeSummary::default()
    };
    assert!(summary.has_offdesk_activity());
    assert!(summary.has_morning_review());
    assert!(!summary.needs_operator_attention());
    assert_eq!(summary.focus_label(), "cancelled work archived");
    assert_eq!(summary.next_action_label(), "Review: forager offdesk tasks");

    let resume_summary = OffdeskResumeSummary {
        fresh_pending: 1,
        ..OffdeskResumeSummary::default()
    };
    assert!(resume_summary.has_morning_review());
    assert!(resume_summary.needs_operator_attention());
    assert_eq!(resume_summary.focus_label(), "resume decision pending");
    assert_eq!(
        resume_summary.next_action_label(),
        "Recover: forager offdesk resume"
    );
}

#[test]
fn offdesk_morning_review_surfaces_closeout_as_attention() {
    let summary = OffdeskResumeSummary {
        closeout_required: 2,
        ..OffdeskResumeSummary::default()
    };

    assert!(summary.has_offdesk_activity());
    assert!(summary.has_morning_review());
    assert!(summary.needs_operator_attention());
    assert_eq!(summary.focus_label(), "closeout required");
    assert_eq!(
        summary.next_action_label(),
        "Closeout: forager offdesk closeout"
    );
}

#[test]
fn offdesk_morning_review_prioritizes_approvals_before_running_work() {
    let summary = OffdeskResumeSummary {
        pending_approvals: 1,
        active_tasks: 3,
        failed_tasks: 1,
        ..OffdeskResumeSummary::default()
    };

    assert_eq!(summary.focus_label(), "approvals waiting");
    assert_eq!(
        summary.next_action_label(),
        "Review: forager offdesk pending"
    );
}

#[test]
#[serial]
fn offdesk_morning_review_visual_smoke_renders_next_safe_actions() {
    struct Case {
        name: &'static str,
        summary: OffdeskResumeSummary,
        expected_focus: &'static str,
        expected_next: &'static str,
        expected_action: &'static str,
    }

    let cases = [
        Case {
            name: "approval",
            summary: OffdeskResumeSummary {
                pending_approvals: 1,
                active_tasks: 2,
                next_safe_actions: vec![OffdeskNextSafeAction::new(
                    "approval_pending",
                    "Review the approval before dispatch.",
                    vec!["forager offdesk pending".to_string()],
                    true,
                )],
                ..OffdeskResumeSummary::default()
            },
            expected_focus: "approvals waiting",
            expected_next: "Next:  Review: forager offdesk pending",
            expected_action: "Action forager offdesk pending",
        },
        Case {
            name: "recovery",
            summary: OffdeskResumeSummary {
                failed_tasks: 1,
                next_safe_actions: vec![OffdeskNextSafeAction::new(
                    "recovery_required",
                    "Review recovery evidence before retry.",
                    vec!["forager offdesk resume".to_string()],
                    true,
                )],
                ..OffdeskResumeSummary::default()
            },
            expected_focus: "failed work needs review",
            expected_next: "Next:  Recover: forager offdesk resume",
            expected_action: "Action forager offdesk resume",
        },
        Case {
            name: "closeout",
            summary: OffdeskResumeSummary {
                closeout_required: 1,
                next_safe_actions: vec![OffdeskNextSafeAction::new(
                    "review_required",
                    "Close out completed work before accepting output.",
                    vec!["forager offdesk closeout".to_string()],
                    true,
                )],
                ..OffdeskResumeSummary::default()
            },
            expected_focus: "closeout required",
            expected_next: "Next:  Review: forager offdesk closeout",
            expected_action: "Action forager offdesk closeout",
        },
        Case {
            name: "runtime",
            summary: OffdeskResumeSummary {
                active_tasks: 1,
                next_safe_actions: vec![OffdeskNextSafeAction::new(
                    "runtime_monitoring",
                    "Poll before judging completion.",
                    vec!["forager offdesk poll".to_string()],
                    false,
                )],
                ..OffdeskResumeSummary::default()
            },
            expected_focus: "active offdesk work",
            expected_next: "Next:  Monitor: forager offdesk poll",
            expected_action: "Action forager offdesk poll",
        },
    ];

    for case in cases {
        let mut env = create_test_env_empty();
        env.view.offdesk_resume = case.summary;

        let rendered = render_home_text(&mut env.view, 200, 32);

        assert!(
            rendered.contains("Offdesk Morning Review"),
            "{} should render the morning-review card:\n{}",
            case.name,
            rendered
        );
        assert!(
            rendered.contains(case.expected_focus),
            "{} should render focus `{}`:\n{}",
            case.name,
            case.expected_focus,
            rendered
        );
        assert!(
            rendered.contains(case.expected_next),
            "{} should render next action `{}`:\n{}",
            case.name,
            case.expected_next,
            rendered
        );
        assert!(
            rendered.contains(case.expected_action),
            "{} should render status-bar action `{}`:\n{}",
            case.name,
            case.expected_action,
            rendered
        );
    }
}

#[test]
#[serial]
fn test_initial_cursor_position() {
    let env = create_test_env_with_sessions(3);
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_q_returns_quit_action() {
    let mut env = create_test_env_empty();
    let action = env.view.handle_key(key(KeyCode::Char('q')));
    assert_eq!(action, Some(Action::Quit));
}

#[test]
#[serial]
fn test_question_mark_opens_help() {
    let mut env = create_test_env_empty();
    assert!(!env.view.show_help);
    env.view.handle_key(key(KeyCode::Char('?')));
    assert!(env.view.show_help);
}

#[test]
#[serial]
fn test_help_closes_on_esc() {
    let mut env = create_test_env_empty();
    env.view.show_help = true;
    env.view.handle_key(key(KeyCode::Esc));
    assert!(!env.view.show_help);
}

#[test]
#[serial]
fn test_help_closes_on_question_mark() {
    let mut env = create_test_env_empty();
    env.view.show_help = true;
    env.view.handle_key(key(KeyCode::Char('?')));
    assert!(!env.view.show_help);
}

#[test]
#[serial]
fn test_help_closes_on_q() {
    let mut env = create_test_env_empty();
    env.view.show_help = true;
    env.view.handle_key(key(KeyCode::Char('q')));
    assert!(!env.view.show_help);
}

#[test]
#[serial]
fn test_has_dialog_returns_true_for_help() {
    let mut env = create_test_env_empty();
    assert!(!env.view.has_dialog());
    env.view.show_help = true;
    assert!(env.view.has_dialog());
}

#[test]
#[serial]
fn test_n_opens_new_dialog() {
    let mut env = create_test_env_empty();
    assert!(env.view.new_dialog.is_none());
    env.view.handle_key(key(KeyCode::Char('n')));
    assert!(env.view.new_dialog.is_some());
}

#[test]
#[serial]
fn test_has_dialog_returns_true_for_new_dialog() {
    let mut env = create_test_env_empty();
    env.view.new_dialog = Some(NewSessionDialog::new(
        AvailableTools::with_tools(&["claude"]),
        Vec::new(),
        Vec::new(),
        "default",
    ));
    assert!(env.view.has_dialog());
}

#[test]
#[serial]
fn test_cursor_down_j() {
    let mut env = create_test_env_with_sessions(5);
    assert_eq!(env.view.cursor, 0);
    env.view.handle_key(key(KeyCode::Char('j')));
    assert_eq!(env.view.cursor, 1);
}

#[test]
#[serial]
fn test_cursor_down_arrow() {
    let mut env = create_test_env_with_sessions(5);
    assert_eq!(env.view.cursor, 0);
    env.view.handle_key(key(KeyCode::Down));
    assert_eq!(env.view.cursor, 1);
}

#[test]
#[serial]
fn test_cursor_up_k() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 3;
    env.view.handle_key(key(KeyCode::Char('k')));
    assert_eq!(env.view.cursor, 2);
}

#[test]
#[serial]
fn test_cursor_up_arrow() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 3;
    env.view.handle_key(key(KeyCode::Up));
    assert_eq!(env.view.cursor, 2);
}

#[test]
#[serial]
fn test_cursor_bounds_at_top() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 0;
    env.view.handle_key(key(KeyCode::Up));
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_cursor_bounds_at_bottom() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 4;
    env.view.handle_key(key(KeyCode::Down));
    assert_eq!(env.view.cursor, 4);
}

#[test]
#[serial]
fn test_page_down() {
    let mut env = create_test_env_with_sessions(20);
    env.view.cursor = 0;
    env.view.handle_key(key(KeyCode::PageDown));
    assert_eq!(env.view.cursor, 10);
}

#[test]
#[serial]
fn test_page_up() {
    let mut env = create_test_env_with_sessions(20);
    env.view.cursor = 15;
    env.view.handle_key(key(KeyCode::PageUp));
    assert_eq!(env.view.cursor, 5);
}

#[test]
#[serial]
fn test_page_down_clamps_to_end() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 0;
    env.view.handle_key(key(KeyCode::PageDown));
    assert_eq!(env.view.cursor, 4);
}

#[test]
#[serial]
fn test_ctrl_tab_cycles_to_next_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();
    assert_eq!(sessions.len(), 3);

    let action = env
        .view
        .handle_key(key_with_mod(KeyCode::Tab, KeyModifiers::CONTROL));

    assert_eq!(action, Some(Action::AttachSession(sessions[1].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[1].as_str())
    );
}

#[test]
#[serial]
fn test_ctrl_backtab_cycles_to_previous_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();
    assert_eq!(sessions.len(), 3);

    let action = env
        .view
        .handle_key(key_with_mod(KeyCode::BackTab, KeyModifiers::CONTROL));

    assert_eq!(action, Some(Action::AttachSession(sessions[2].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[2].as_str())
    );
}

#[test]
#[serial]
fn test_tab_cycles_to_next_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();

    let action = env.view.handle_key(key(KeyCode::Tab));

    assert_eq!(action, Some(Action::AttachSession(sessions[1].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[1].as_str())
    );
}

#[test]
#[serial]
fn test_shift_backtab_cycles_to_previous_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();

    let action = env
        .view
        .handle_key(key_with_mod(KeyCode::BackTab, KeyModifiers::SHIFT));

    assert_eq!(action, Some(Action::AttachSession(sessions[2].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[2].as_str())
    );
}

#[test]
#[serial]
fn test_number_jumps_to_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();

    let action = env.view.handle_key(key(KeyCode::Char(char::from(50))));

    assert_eq!(action, Some(Action::AttachSession(sessions[1].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[1].as_str())
    );
}

#[test]
#[serial]
fn test_alt_number_jumps_to_session_and_attaches() {
    let mut env = create_test_env_with_sessions(3);
    let sessions = env.view.visible_session_ids();

    let action = env
        .view
        .handle_key(key_with_mod(KeyCode::Char('2'), KeyModifiers::ALT));

    assert_eq!(action, Some(Action::AttachSession(sessions[1].clone())));
    assert_eq!(
        env.view.selected_session.as_deref(),
        Some(sessions[1].as_str())
    );
}

#[test]
#[serial]
fn test_alt_number_attaches_terminal_in_terminal_view() {
    let mut env = create_test_env_with_sessions(2);
    env.view.view_mode = ViewMode::Terminal;
    let sessions = env.view.visible_session_ids();

    let action = env
        .view
        .handle_key(key_with_mod(KeyCode::Char('1'), KeyModifiers::ALT));

    assert_eq!(action, Some(Action::AttachTerminal(sessions[0].clone())));
}

#[test]
#[serial]
fn test_page_up_clamps_to_start() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 3;
    env.view.handle_key(key(KeyCode::PageUp));
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_home_key() {
    let mut env = create_test_env_with_sessions(10);
    env.view.cursor = 7;
    env.view.handle_key(key(KeyCode::Home));
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_end_key() {
    let mut env = create_test_env_with_sessions(10);
    env.view.cursor = 3;
    env.view.handle_key(key(KeyCode::End));
    assert_eq!(env.view.cursor, 9);
}

#[test]
#[serial]
fn test_g_key_goes_to_start() {
    let mut env = create_test_env_with_sessions(10);
    env.view.cursor = 7;
    env.view.handle_key(key(KeyCode::Char('g')));
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_uppercase_g_goes_to_end() {
    let mut env = create_test_env_with_sessions(10);
    env.view.cursor = 3;
    env.view.handle_key(key(KeyCode::Char('G')));
    assert_eq!(env.view.cursor, 9);
}

#[test]
#[serial]
fn test_cursor_movement_on_empty_list() {
    let mut env = create_test_env_empty();
    env.view.handle_key(key(KeyCode::Down));
    assert_eq!(env.view.cursor, 0);
    env.view.handle_key(key(KeyCode::Up));
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_enter_on_session_returns_attach_action() {
    let mut env = create_test_env_with_sessions(3);
    env.view.cursor = 1;
    env.view.update_selected();
    let action = env.view.handle_key(key(KeyCode::Enter));
    assert!(matches!(action, Some(Action::AttachSession(_))));
}

#[test]
#[serial]
fn test_slash_enters_search_mode() {
    let mut env = create_test_env_with_sessions(3);
    assert!(!env.view.search_active);
    env.view.handle_key(key(KeyCode::Char('/')));
    assert!(env.view.search_active);
    assert!(env.view.search_query.value().is_empty());
}

#[test]
#[serial]
fn test_search_mode_captures_chars() {
    let mut env = create_test_env_with_sessions(3);
    env.view.handle_key(key(KeyCode::Char('/')));
    env.view.handle_key(key(KeyCode::Char('t')));
    env.view.handle_key(key(KeyCode::Char('e')));
    env.view.handle_key(key(KeyCode::Char('s')));
    env.view.handle_key(key(KeyCode::Char('t')));
    assert_eq!(env.view.search_query.value(), "test");
}

#[test]
#[serial]
fn test_search_mode_backspace() {
    let mut env = create_test_env_with_sessions(3);
    env.view.handle_key(key(KeyCode::Char('/')));
    env.view.handle_key(key(KeyCode::Char('a')));
    env.view.handle_key(key(KeyCode::Char('b')));
    env.view.handle_key(key(KeyCode::Backspace));
    assert_eq!(env.view.search_query.value(), "a");
}

#[test]
#[serial]
fn test_search_mode_esc_exits_and_clears() {
    let mut env = create_test_env_with_sessions(3);
    env.view.handle_key(key(KeyCode::Char('/')));
    env.view.handle_key(key(KeyCode::Char('x')));
    env.view.handle_key(key(KeyCode::Esc));
    assert!(!env.view.search_active);
    assert!(env.view.search_query.value().is_empty());
    assert!(env.view.filtered_items.is_none());
}

#[test]
#[serial]
fn test_search_mode_enter_exits_keeps_filter() {
    let mut env = create_test_env_with_sessions(3);
    env.view.handle_key(key(KeyCode::Char('/')));
    env.view.handle_key(key(KeyCode::Char('s')));
    env.view.handle_key(key(KeyCode::Enter));
    assert!(!env.view.search_active);
    assert_eq!(env.view.search_query.value(), "s");
}

#[test]
#[serial]
fn test_d_on_session_opens_delete_dialog() {
    let mut env = create_test_env_with_sessions(3);
    env.view.update_selected();
    assert!(env.view.unified_delete_dialog.is_none());
    env.view.handle_key(key(KeyCode::Char('d')));
    assert!(env.view.unified_delete_dialog.is_some());
}

#[test]
#[serial]
fn test_d_on_group_with_sessions_opens_group_delete_options_dialog() {
    let mut env = create_test_env_with_groups();
    env.view.cursor = 1;
    env.view.update_selected();
    assert!(env.view.selected_group.is_some());
    assert!(env.view.group_delete_options_dialog.is_none());
    env.view.handle_key(key(KeyCode::Char('d')));
    assert!(env.view.group_delete_options_dialog.is_some());
}

#[test]
#[serial]
fn test_selected_session_updates_on_cursor_move() {
    let mut env = create_test_env_with_sessions(3);
    let first_id = env.view.selected_session.clone();
    env.view.handle_key(key(KeyCode::Down));
    assert_ne!(env.view.selected_session, first_id);
}

#[test]
#[serial]
fn test_selected_group_set_when_on_group() {
    let mut env = create_test_env_with_groups();
    for i in 0..env.view.flat_items.len() {
        env.view.cursor = i;
        env.view.update_selected();
        if matches!(env.view.flat_items.get(i), Some(Item::Group { .. })) {
            assert!(env.view.selected_group.is_some());
            assert!(env.view.selected_session.is_none());
            return;
        }
    }
    panic!("No group found in flat_items");
}

#[test]
#[serial]
fn test_filter_matches_session_title() {
    let mut env = create_test_env_with_sessions(5);
    env.view.search_query = Input::new("session2".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());
    let filtered = env.view.filtered_items.as_ref().unwrap();
    assert_eq!(filtered.len(), 1);
}

#[test]
#[serial]
fn test_filter_case_insensitive() {
    let mut env = create_test_env_with_sessions(5);
    env.view.search_query = Input::new("SESSION2".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());
    let filtered = env.view.filtered_items.as_ref().unwrap();
    assert_eq!(filtered.len(), 1);
}

#[test]
#[serial]
fn test_filter_matches_path() {
    let mut env = create_test_env_with_sessions(5);
    env.view.search_query = Input::new("/tmp/3".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());
    let filtered = env.view.filtered_items.as_ref().unwrap();
    assert_eq!(filtered.len(), 1);
}

#[test]
#[serial]
fn test_filter_matches_group_name() {
    let mut env = create_test_env_with_groups();
    env.view.search_query = Input::new("work".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());
    let filtered = env.view.filtered_items.as_ref().unwrap();
    assert!(!filtered.is_empty());
}

#[test]
#[serial]
fn test_filter_empty_query_clears_filter() {
    let mut env = create_test_env_with_sessions(5);
    env.view.search_query = Input::new("session".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());

    env.view.search_query = Input::default();
    env.view.update_filter();
    assert!(env.view.filtered_items.is_none());
}

#[test]
#[serial]
fn test_filter_resets_cursor() {
    let mut env = create_test_env_with_sessions(5);
    env.view.cursor = 3;
    env.view.search_query = Input::new("session".to_string());
    env.view.update_filter();
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_filter_no_matches() {
    let mut env = create_test_env_with_sessions(5);
    env.view.search_query = Input::new("nonexistent".to_string());
    env.view.update_filter();
    assert!(env.view.filtered_items.is_some());
    let filtered = env.view.filtered_items.as_ref().unwrap();
    assert_eq!(filtered.len(), 0);
}

#[test]
#[serial]
fn test_cursor_moves_within_filtered_list() {
    let mut env = create_test_env_with_sessions(10);
    env.view.search_query = Input::new("session".to_string());
    env.view.update_filter();
    let filtered_count = env.view.filtered_items.as_ref().unwrap().len();

    env.view.cursor = 0;
    for _ in 0..(filtered_count + 5) {
        env.view.handle_key(key(KeyCode::Down));
    }
    assert_eq!(env.view.cursor, filtered_count - 1);
}

#[test]
#[serial]
fn test_r_opens_rename_dialog() {
    let mut env = create_test_env_with_sessions(3);
    env.view.update_selected();
    assert!(env.view.rename_dialog.is_none());
    env.view.handle_key(key(KeyCode::Char('r')));
    assert!(env.view.rename_dialog.is_some());
}

#[test]
#[serial]
fn test_rename_dialog_not_opened_on_group() {
    let mut env = create_test_env_with_groups();
    env.view.cursor = 1;
    env.view.update_selected();
    assert!(env.view.selected_group.is_some());
    assert!(env.view.rename_dialog.is_none());
    env.view.handle_key(key(KeyCode::Char('r')));
    assert!(env.view.rename_dialog.is_none());
}

#[test]
#[serial]
fn test_has_dialog_returns_true_for_rename_dialog() {
    let mut env = create_test_env_with_sessions(1);
    env.view.update_selected();
    assert!(!env.view.has_dialog());
    env.view.handle_key(key(KeyCode::Char('r')));
    assert!(env.view.has_dialog());
}

#[test]
#[serial]
fn test_select_session_by_id() {
    let mut env = create_test_env_with_sessions(3);
    let session_id = env.view.instances[1].id.clone();

    assert_eq!(env.view.cursor, 0);

    env.view.select_session_by_id(&session_id);

    assert_eq!(env.view.cursor, 1);
    assert_eq!(env.view.selected_session, Some(session_id));
}

#[test]
#[serial]
fn test_select_session_by_id_nonexistent() {
    let mut env = create_test_env_with_sessions(3);

    assert_eq!(env.view.cursor, 0);
    env.view.select_session_by_id("nonexistent-id");
    assert_eq!(env.view.cursor, 0);
}

#[test]
#[serial]
fn test_get_next_profile_single_profile_returns_none() {
    let env = create_test_env_empty();
    assert!(env.view.get_next_profile().is_none());
}

#[test]
#[serial]
fn test_get_next_profile_cycles_through_profiles() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);

    crate::session::create_profile("alpha").unwrap();
    crate::session::create_profile("beta").unwrap();
    crate::session::create_profile("gamma").unwrap();

    let storage = Storage::new("alpha").unwrap();
    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();

    // From alpha -> beta
    assert_eq!(view.get_next_profile(), Some("beta".to_string()));
}

#[test]
#[serial]
fn test_get_next_profile_wraps_around() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);

    crate::session::create_profile("alpha").unwrap();
    crate::session::create_profile("beta").unwrap();

    // Start on beta (last alphabetically)
    let storage = Storage::new("beta").unwrap();
    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();

    // From beta -> alpha (wraps)
    assert_eq!(view.get_next_profile(), Some("alpha".to_string()));
}

#[test]
#[serial]
fn test_uppercase_p_returns_switch_profile_action() {
    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);

    crate::session::create_profile("first").unwrap();
    crate::session::create_profile("second").unwrap();

    let storage = Storage::new("first").unwrap();
    let tools = AvailableTools::with_tools(&["claude"]);
    let mut view = HomeView::new(storage, tools).unwrap();

    let action = view.handle_key(key(KeyCode::Char('P')));
    assert_eq!(action, Some(Action::SwitchProfile("second".to_string())));
}

#[test]
#[serial]
fn test_uppercase_p_does_nothing_with_single_profile() {
    let env = create_test_env_empty();
    let mut view = env.view;

    let action = view.handle_key(key(KeyCode::Char('P')));
    assert_eq!(action, None);
}

#[test]
#[serial]
fn test_t_toggles_view_mode() {
    let env = create_test_env_empty();
    let mut view = env.view;

    assert_eq!(view.view_mode, ViewMode::Agent);

    view.handle_key(key(KeyCode::Char('t')));
    assert_eq!(view.view_mode, ViewMode::Terminal);

    view.handle_key(key(KeyCode::Char('t')));
    assert_eq!(view.view_mode, ViewMode::Agent);
}

#[test]
#[serial]
fn test_enter_returns_attach_terminal_in_terminal_view() {
    let env = create_test_env_with_sessions(1);
    let mut view = env.view;

    // In Agent view, Enter returns AttachSession
    let action = view.handle_key(key(KeyCode::Enter));
    assert!(matches!(action, Some(Action::AttachSession(_))));

    // Switch to Terminal view
    view.handle_key(key(KeyCode::Char('t')));
    assert_eq!(view.view_mode, ViewMode::Terminal);

    // In Terminal view, Enter returns AttachTerminal
    let action = view.handle_key(key(KeyCode::Enter));
    assert!(matches!(action, Some(Action::AttachTerminal(_))));
}

#[test]
#[serial]
fn test_d_shows_info_dialog_in_terminal_view() {
    let env = create_test_env_with_sessions(1);
    let mut view = env.view;

    // Switch to Terminal view
    view.handle_key(key(KeyCode::Char('t')));
    assert_eq!(view.view_mode, ViewMode::Terminal);

    // Press 'd' - should show info dialog, not delete dialog
    assert!(view.info_dialog.is_none());
    view.handle_key(key(KeyCode::Char('d')));
    assert!(view.info_dialog.is_some());
    assert!(view.unified_delete_dialog.is_none());
}

#[test]
#[serial]
fn test_has_dialog_includes_info_dialog() {
    let env = create_test_env_empty();
    let mut view = env.view;

    assert!(!view.has_dialog());

    view.info_dialog = Some(InfoDialog::new("Test", "Test message"));
    assert!(view.has_dialog());
}

#[test]
#[serial]
fn test_has_dialog_includes_settings_view() {
    use crate::tui::settings::SettingsView;

    let env = create_test_env_empty();
    let mut view = env.view;

    assert!(!view.has_dialog());

    view.settings_view = Some(SettingsView::new("test", None).unwrap());
    assert!(view.has_dialog());
}

#[test]
#[serial]
fn test_s_opens_settings_view() {
    let mut env = create_test_env_empty();
    assert!(env.view.settings_view.is_none());
    env.view.handle_key(key(KeyCode::Char('s')));
    assert!(env.view.settings_view.is_some());
}

// Group deletion tests

fn create_test_env_with_group_sessions() -> TestEnv {
    use crate::session::{GroupTree, SandboxInfo};

    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();
    let mut instances = Vec::new();

    // Ungrouped session
    let inst1 = Instance::new("ungrouped", "/tmp/u");
    instances.push(inst1);

    // Sessions in "work" group
    let mut inst2 = Instance::new("work-session-1", "/tmp/work1");
    inst2.group_path = "work".to_string();
    instances.push(inst2);

    let mut inst3 = Instance::new("work-session-2", "/tmp/work2");
    inst3.group_path = "work".to_string();
    inst3.sandbox_info = Some(SandboxInfo {
        enabled: true,
        container_id: None,
        image: "ubuntu:latest".to_string(),
        container_name: "test-container".to_string(),
        created_at: None,
        extra_env_keys: None,
        extra_env_values: None,
    });
    instances.push(inst3);

    // Session in nested group
    let mut inst4 = Instance::new("work-nested", "/tmp/work/nested");
    inst4.group_path = "work/projects".to_string();
    instances.push(inst4);

    // Build group tree from instances and save with groups
    let group_tree = GroupTree::new_with_groups(&instances, &[]);
    storage.save_with_groups(&instances, &group_tree).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();
    TestEnv { _temp: temp, view }
}

fn drain_deletion_results(view: &mut HomeView, expected: usize) {
    let mut observed = 0;
    for _ in 0..100 {
        if view.apply_deletion_results() {
            observed += 1;
            if observed >= expected {
                return;
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    panic!("timed out waiting for {expected} deletion results; observed {observed}");
}

#[test]
#[serial]
fn test_group_has_managed_worktrees() {
    use crate::session::WorktreeInfo;
    use chrono::Utc;

    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();

    let mut inst1 = Instance::new("work-session", "/tmp/work");
    inst1.group_path = "work".to_string();
    inst1.worktree_info = Some(WorktreeInfo {
        branch: "feature-branch".to_string(),
        main_repo_path: "/tmp/main".to_string(),
        managed_by_forager: true,
        created_at: Utc::now(),
        cleanup_on_delete: true,
    });

    let mut inst2 = Instance::new("other-session", "/tmp/other");
    inst2.group_path = "other".to_string();

    storage.save(&[inst1, inst2]).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();

    assert!(view.group_has_managed_worktrees("work", "work/"));
    assert!(!view.group_has_managed_worktrees("other", "other/"));
}

#[test]
#[serial]
fn test_group_has_containers() {
    use crate::session::SandboxInfo;

    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();

    let mut inst1 = Instance::new("work-session", "/tmp/work");
    inst1.group_path = "work".to_string();
    inst1.sandbox_info = Some(SandboxInfo {
        enabled: true,
        container_id: None,
        image: "ubuntu:latest".to_string(),
        container_name: "test-container".to_string(),
        created_at: None,
        extra_env_keys: None,
        extra_env_values: None,
    });

    let mut inst2 = Instance::new("other-session", "/tmp/other");
    inst2.group_path = "other".to_string();

    storage.save(&[inst1, inst2]).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let view = HomeView::new(storage, tools).unwrap();

    assert!(view.group_has_containers("work", "work/"));
    assert!(!view.group_has_containers("other", "other/"));
}

#[test]
#[serial]
fn test_delete_selected_group_updates_groups_field() {
    let mut env = create_test_env_with_group_sessions();

    // Select the "work" group
    for (i, item) in env.view.flat_items.iter().enumerate() {
        if let Item::Group { path, .. } = item {
            if path == "work" {
                env.view.cursor = i;
                env.view.update_selected();
                break;
            }
        }
    }

    assert!(env.view.selected_group.is_some());
    assert!(env.view.group_tree.group_exists("work"));

    // Delete the group (this moves sessions to default)
    env.view.delete_selected_group().unwrap();

    // Verify the group is removed from group_tree
    assert!(!env.view.group_tree.group_exists("work"));

    // Verify self.groups is updated (this is the bug fix)
    let group_paths: Vec<_> = env.view.groups.iter().map(|g| g.path.as_str()).collect();
    assert!(!group_paths.contains(&"work"));
    assert!(!group_paths.contains(&"work/projects"));
}

#[test]
#[serial]
fn test_delete_group_with_sessions_updates_groups_field() {
    use crate::session::Status;
    use crate::tui::dialogs::GroupDeleteOptions;

    let mut env = create_test_env_with_group_sessions();

    // Select the "work" group
    for (i, item) in env.view.flat_items.iter().enumerate() {
        if let Item::Group { path, .. } = item {
            if path == "work" {
                env.view.cursor = i;
                env.view.update_selected();
                break;
            }
        }
    }

    assert!(env.view.selected_group.is_some());
    let initial_instance_count = env.view.instances.len();

    // Delete the group with all sessions
    let options = GroupDeleteOptions {
        delete_sessions: true,
        delete_worktrees: false,
        delete_branches: false,
        force_delete_branches: false,
        delete_containers: false,
        force_delete_worktrees: false,
    };
    env.view.delete_group_with_sessions(&options).unwrap();

    // The group remains visible while deletion is pending.
    assert!(env.view.group_tree.group_exists("work"));
    assert!(env.view.group_tree.group_exists("work/projects"));

    // The persisted groups list is only updated after deletion succeeds.
    let group_paths: Vec<_> = env.view.groups.iter().map(|g| g.path.as_str()).collect();
    assert!(group_paths.contains(&"work"));
    assert!(group_paths.contains(&"work/projects"));

    // Verify sessions are marked as deleting
    let deleting_count = env
        .view
        .instances
        .iter()
        .filter(|i| i.status == Status::Deleting)
        .count();
    // Should have 3 sessions in the work group marked as deleting
    assert_eq!(deleting_count, 3);

    // Instance count should remain the same (they're marked as deleting, not removed yet)
    assert_eq!(env.view.instances.len(), initial_instance_count);

    drain_deletion_results(&mut env.view, 3);

    // Once all deletion requests succeed, the empty group path is removed.
    assert!(!env.view.group_tree.group_exists("work"));
    assert!(!env.view.group_tree.group_exists("work/projects"));
    let group_paths: Vec<_> = env.view.groups.iter().map(|g| g.path.as_str()).collect();
    assert!(!group_paths.contains(&"work"));
    assert!(!group_paths.contains(&"work/projects"));
}

#[test]
#[serial]
fn test_delete_group_with_sessions_respects_worktree_option() {
    use crate::session::WorktreeInfo;
    use crate::tui::dialogs::GroupDeleteOptions;
    use chrono::Utc;

    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();

    let mut inst1 = Instance::new("work-session", "/tmp/work");
    inst1.group_path = "work".to_string();
    inst1.worktree_info = Some(WorktreeInfo {
        branch: "feature".to_string(),
        main_repo_path: "/tmp/main".to_string(),
        managed_by_forager: true,
        created_at: Utc::now(),
        cleanup_on_delete: true,
    });

    storage.save(&[inst1]).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let mut view = HomeView::new(storage, tools).unwrap();

    // Select the work group
    view.cursor = 0;
    view.update_selected();
    assert!(view.selected_group.is_some());

    // Delete with worktrees option enabled
    let options = GroupDeleteOptions {
        delete_sessions: true,
        delete_worktrees: true,
        delete_branches: false,
        force_delete_branches: false,
        delete_containers: false,
        force_delete_worktrees: false,
    };
    view.delete_group_with_sessions(&options).unwrap();
    drain_deletion_results(&mut view, 1);

    // We can't easily verify the deletion request was sent with the right flags
    // without mocking, but we can verify the group is deleted after success.
    assert!(!view.group_tree.group_exists("work"));
}

#[test]
#[serial]
fn test_delete_group_with_sessions_respects_container_option() {
    use crate::session::SandboxInfo;
    use crate::tui::dialogs::GroupDeleteOptions;

    let temp = TempDir::new().unwrap();
    setup_test_home(&temp);
    let storage = Storage::new("test").unwrap();

    let mut inst1 = Instance::new("work-session", "/tmp/work");
    inst1.group_path = "work".to_string();
    inst1.sandbox_info = Some(SandboxInfo {
        enabled: true,
        container_id: None,
        image: "ubuntu:latest".to_string(),
        container_name: "test-container".to_string(),
        created_at: None,
        extra_env_keys: None,
        extra_env_values: None,
    });

    storage.save(&[inst1]).unwrap();

    let tools = AvailableTools::with_tools(&["claude"]);
    let mut view = HomeView::new(storage, tools).unwrap();

    // Select the work group
    view.cursor = 0;
    view.update_selected();
    assert!(view.selected_group.is_some());

    // Delete with containers option enabled
    let options = GroupDeleteOptions {
        delete_sessions: true,
        delete_worktrees: false,
        delete_branches: false,
        force_delete_branches: false,
        delete_containers: true,
        force_delete_worktrees: false,
    };
    view.delete_group_with_sessions(&options).unwrap();
    drain_deletion_results(&mut view, 1);

    // Verify the group was deleted
    assert!(!view.group_tree.group_exists("work"));
}

#[test]
#[serial]
fn test_delete_group_includes_nested_groups() {
    use crate::tui::dialogs::GroupDeleteOptions;

    let mut env = create_test_env_with_group_sessions();

    // Select the "work" group
    for (i, item) in env.view.flat_items.iter().enumerate() {
        if let Item::Group { path, .. } = item {
            if path == "work" {
                env.view.cursor = i;
                env.view.update_selected();
                break;
            }
        }
    }

    // Verify nested group exists
    assert!(env.view.group_tree.group_exists("work/projects"));

    // Delete the group with all sessions
    let options = GroupDeleteOptions {
        delete_sessions: true,
        delete_worktrees: false,
        delete_branches: false,
        force_delete_branches: false,
        delete_containers: false,
        force_delete_worktrees: false,
    };
    env.view.delete_group_with_sessions(&options).unwrap();
    drain_deletion_results(&mut env.view, 3);

    // Verify both parent and nested groups are removed
    assert!(!env.view.group_tree.group_exists("work"));
    assert!(!env.view.group_tree.group_exists("work/projects"));
}

#[test]
#[serial]
fn test_groups_field_stays_in_sync_with_storage() {
    let mut env = create_test_env_with_group_sessions();

    // Get initial group count
    let initial_group_count = env.view.groups.len();
    assert!(initial_group_count > 0);

    // Select and delete the work group
    for (i, item) in env.view.flat_items.iter().enumerate() {
        if let Item::Group { path, .. } = item {
            if path == "work" {
                env.view.cursor = i;
                env.view.update_selected();
                break;
            }
        }
    }

    env.view.delete_selected_group().unwrap();

    // After deletion, groups field should be smaller
    assert!(env.view.groups.len() < initial_group_count);

    // Reload from storage and verify groups match
    env.view.reload().unwrap();
    let reloaded_groups: Vec<_> = env.view.groups.iter().map(|g| g.path.clone()).collect();
    let tree_groups: Vec<_> = env
        .view
        .group_tree
        .get_all_groups()
        .iter()
        .map(|g| g.path.clone())
        .collect();
    assert_eq!(reloaded_groups, tree_groups);
}

#[test]
#[serial]
fn test_group_collapsed_state_persists_across_reload() {
    let mut env = create_test_env_with_groups();

    // Find a group and verify it starts expanded
    let group_idx = env
        .view
        .flat_items
        .iter()
        .position(|item| matches!(item, Item::Group { .. }))
        .expect("should have a group");

    if let Item::Group { collapsed, .. } = &env.view.flat_items[group_idx] {
        assert!(!collapsed, "group should start expanded");
    }

    // Move cursor to group and collapse it with Enter
    env.view.cursor = group_idx;
    env.view.update_selected();
    env.view.handle_key(key(KeyCode::Enter));

    // Verify it's collapsed
    if let Item::Group { collapsed, .. } = &env.view.flat_items[group_idx] {
        assert!(*collapsed, "group should be collapsed after Enter");
    }

    // Reload (simulates the 5-second periodic refresh)
    env.view.reload().unwrap();

    // Find the group again (index may change after reload)
    let group_idx_after = env
        .view
        .flat_items
        .iter()
        .position(|item| matches!(item, Item::Group { .. }))
        .expect("should still have a group");

    // Verify it's still collapsed after reload
    if let Item::Group { collapsed, .. } = &env.view.flat_items[group_idx_after] {
        assert!(*collapsed, "group should remain collapsed after reload");
    }
}

#[test]
#[serial]
fn test_group_collapsed_state_saved_to_storage() {
    use crate::session::GroupTree;

    let mut env = create_test_env_with_groups();

    // Find a group
    let group_path = env
        .view
        .flat_items
        .iter()
        .find_map(|item| {
            if let Item::Group { path, .. } = item {
                Some(path.clone())
            } else {
                None
            }
        })
        .expect("should have a group");

    // Move cursor to group and collapse it
    let group_idx = env
        .view
        .flat_items
        .iter()
        .position(|item| matches!(item, Item::Group { path, .. } if path == &group_path))
        .unwrap();
    env.view.cursor = group_idx;
    env.view.update_selected();
    env.view.handle_key(key(KeyCode::Enter));

    // Load fresh from storage to verify persistence
    let (_, groups) = env.view.storage.load_with_groups().unwrap();
    let fresh_tree = GroupTree::new_with_groups(&env.view.instances, &groups);
    let all_groups = fresh_tree.get_all_groups();

    let saved_group = all_groups
        .iter()
        .find(|g| g.path == group_path)
        .expect("group should exist in storage");

    assert!(
        saved_group.collapsed,
        "collapsed state should be persisted to storage"
    );
}

#[test]
#[serial]
fn test_list_width_default() {
    let env = create_test_env_empty();
    assert_eq!(env.view.list_width, 35);
}

#[test]
#[serial]
fn test_shrink_list() {
    let mut env = create_test_env_empty();
    env.view.shrink_list();
    assert_eq!(env.view.list_width, 30);
}

#[test]
#[serial]
fn test_grow_list() {
    let mut env = create_test_env_empty();
    env.view.grow_list();
    assert_eq!(env.view.list_width, 40);
}

#[test]
#[serial]
fn test_shrink_list_clamps_at_minimum() {
    let mut env = create_test_env_empty();
    env.view.list_width = 12;
    env.view.shrink_list();
    assert_eq!(env.view.list_width, 10);
    env.view.shrink_list();
    assert_eq!(env.view.list_width, 10);
}

#[test]
#[serial]
fn test_grow_list_clamps_at_maximum() {
    let mut env = create_test_env_empty();
    env.view.list_width = 78;
    env.view.grow_list();
    assert_eq!(env.view.list_width, 80);
    env.view.grow_list();
    assert_eq!(env.view.list_width, 80);
}

#[test]
#[serial]
fn test_uppercase_h_shrinks_list() {
    let mut env = create_test_env_empty();
    assert_eq!(env.view.list_width, 35);
    env.view.handle_key(key(KeyCode::Char('H')));
    assert_eq!(env.view.list_width, 30);
}

#[test]
#[serial]
fn test_uppercase_l_grows_list() {
    let mut env = create_test_env_empty();
    assert_eq!(env.view.list_width, 35);
    env.view.handle_key(key(KeyCode::Char('L')));
    assert_eq!(env.view.list_width, 40);
}
