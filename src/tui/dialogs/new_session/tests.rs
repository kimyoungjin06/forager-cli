use super::*;
use crate::session::{merge_configs, Config, ProfileConfig, SessionConfigOverride};
use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};

fn key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::NONE)
}

fn shift_key(code: KeyCode) -> KeyEvent {
    KeyEvent::new(code, KeyModifiers::SHIFT)
}

fn single_tool_dialog() -> NewSessionDialog {
    NewSessionDialog::new_with_tools(vec!["claude"], "/tmp/project".to_string())
}

fn multi_tool_dialog() -> NewSessionDialog {
    NewSessionDialog::new_with_tools(vec!["claude", "opencode"], "/tmp/project".to_string())
}

#[test]
fn test_initial_state() {
    let dialog = single_tool_dialog();
    assert_eq!(dialog.title.value(), "");
    assert_eq!(dialog.path.value(), "/tmp/project");
    assert_eq!(dialog.group.value(), "");
    assert_eq!(dialog.focused_field, 0);
    assert_eq!(dialog.tool_index, 0);
}

#[test]
fn test_esc_cancels() {
    let mut dialog = single_tool_dialog();
    let result = dialog.handle_key(key(KeyCode::Esc));
    assert!(matches!(result, DialogResult::Cancel));
}

#[test]
fn test_enter_submits_with_auto_title() {
    use crate::session::civilizations;

    let mut dialog = single_tool_dialog();
    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert!(
                civilizations::CIVILIZATIONS.contains(&data.title.as_str()),
                "Expected a civilization name, got: {}",
                data.title
            );
            assert_eq!(data.path, "/tmp/project");
            assert_eq!(data.group, "");
            assert_eq!(data.tool, "claude");
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn test_enter_preserves_custom_title() {
    let mut dialog = single_tool_dialog();
    dialog.title = Input::new("My Custom Title".to_string());
    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert_eq!(data.title, "My Custom Title");
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn test_tab_cycles_fields_single_tool() {
    let mut dialog = single_tool_dialog();
    assert_eq!(dialog.focused_field, 0);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 1);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 2); // yolo mode

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 3); // worktree branch

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 4); // group

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 0); // wrap to start
}

#[test]
fn test_tab_cycles_fields_single_tool_with_worktree() {
    let mut dialog = single_tool_dialog();
    dialog.worktree_branch = Input::new("feature".to_string());
    assert_eq!(dialog.focused_field, 0);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 1);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 2); // yolo mode

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 3); // worktree branch

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 4); // new branch checkbox (now visible)

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 5); // group

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 0); // wrap to start
}

#[test]
fn test_tab_cycles_fields_multi_tool() {
    let mut dialog = multi_tool_dialog();
    assert_eq!(dialog.focused_field, 0);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 1);

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 2); // tool selection

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 3); // yolo mode

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 4); // worktree branch

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 5); // group

    dialog.handle_key(key(KeyCode::Tab));
    assert_eq!(dialog.focused_field, 0); // wrap to start (no new_branch without worktree)
}

#[test]
fn test_backtab_cycles_fields_reverse() {
    let mut dialog = single_tool_dialog();
    assert_eq!(dialog.focused_field, 0);

    dialog.handle_key(shift_key(KeyCode::BackTab));
    assert_eq!(dialog.focused_field, 4); // group (last field without worktree/docker)

    dialog.handle_key(shift_key(KeyCode::BackTab));
    assert_eq!(dialog.focused_field, 3); // worktree branch

    dialog.handle_key(shift_key(KeyCode::BackTab));
    assert_eq!(dialog.focused_field, 2); // yolo mode

    dialog.handle_key(shift_key(KeyCode::BackTab));
    assert_eq!(dialog.focused_field, 1); // path

    dialog.handle_key(shift_key(KeyCode::BackTab));
    assert_eq!(dialog.focused_field, 0); // title
}

#[test]
fn test_char_input_to_title() {
    let mut dialog = single_tool_dialog();
    dialog.handle_key(key(KeyCode::Char('H')));
    dialog.handle_key(key(KeyCode::Char('i')));
    assert_eq!(dialog.title.value(), "Hi");
}

#[test]
fn test_char_input_to_path() {
    let mut dialog = single_tool_dialog();
    dialog.focused_field = 1;
    dialog.handle_key(key(KeyCode::Char('/')));
    dialog.handle_key(key(KeyCode::Char('a')));
    assert_eq!(dialog.path.value(), "/tmp/project/a");
}

#[test]
fn test_char_input_to_group() {
    let mut dialog = single_tool_dialog();
    dialog.focused_field = 4; // group is at the bottom (single tool: yolo=2, worktree=3, group=4)
    dialog.handle_key(key(KeyCode::Char('w')));
    dialog.handle_key(key(KeyCode::Char('o')));
    dialog.handle_key(key(KeyCode::Char('r')));
    dialog.handle_key(key(KeyCode::Char('k')));
    assert_eq!(dialog.group.value(), "work");
}

#[test]
fn test_backspace_removes_char() {
    let mut dialog = single_tool_dialog();
    dialog.title = Input::new("Hello".to_string());
    dialog.handle_key(key(KeyCode::Backspace));
    assert_eq!(dialog.title.value(), "Hell");
}

#[test]
fn test_backspace_on_empty_field() {
    let mut dialog = single_tool_dialog();
    dialog.handle_key(key(KeyCode::Backspace));
    assert_eq!(dialog.title.value(), "");
}

#[test]
fn test_tool_selection_left_right() {
    let mut dialog = multi_tool_dialog();
    dialog.focused_field = 2; // tool field
    assert_eq!(dialog.tool_index, 0);

    dialog.handle_key(key(KeyCode::Right));
    assert_eq!(dialog.tool_index, 1);

    dialog.handle_key(key(KeyCode::Right));
    assert_eq!(dialog.tool_index, 0);

    dialog.handle_key(key(KeyCode::Left));
    assert_eq!(dialog.tool_index, 1);
}

#[test]
fn test_tool_selection_space() {
    let mut dialog = multi_tool_dialog();
    dialog.focused_field = 2; // tool field
    assert_eq!(dialog.tool_index, 0);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert_eq!(dialog.tool_index, 1);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert_eq!(dialog.tool_index, 0);
}

#[test]
fn test_tool_selection_ignored_on_text_field() {
    let mut dialog = multi_tool_dialog();
    dialog.focused_field = 0;
    dialog.handle_key(key(KeyCode::Char(' ')));
    assert_eq!(dialog.title.value(), " ");
    assert_eq!(dialog.tool_index, 0);
}

#[test]
fn test_tool_selection_ignored_single_tool() {
    let mut dialog = single_tool_dialog();
    dialog.focused_field = 2; // yolo in single-tool mode (tool not interactive)
    dialog.handle_key(key(KeyCode::Left));
    assert_eq!(dialog.tool_index, 0);
}

#[test]
fn test_submit_with_selected_tool() {
    let mut dialog = multi_tool_dialog();
    dialog.focused_field = 2; // tool field
    dialog.handle_key(key(KeyCode::Right));
    dialog.title = Input::new("Test".to_string());

    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert_eq!(data.tool, "opencode");
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn test_unknown_key_continues() {
    let mut dialog = single_tool_dialog();
    let result = dialog.handle_key(key(KeyCode::F(1)));
    assert!(matches!(result, DialogResult::Continue));
}

#[test]
fn test_error_clears_on_input() {
    let mut dialog = single_tool_dialog();
    dialog.error_message = Some("Some error".to_string());

    dialog.handle_key(key(KeyCode::Char('a')));
    assert_eq!(dialog.error_message, None);
}

#[test]
fn test_esc_clears_error() {
    let mut dialog = single_tool_dialog();
    dialog.error_message = Some("Some error".to_string());

    let result = dialog.handle_key(key(KeyCode::Esc));
    assert!(matches!(result, DialogResult::Cancel));
    assert_eq!(dialog.error_message, None);
}

#[test]
fn test_new_branch_checkbox_default_true() {
    let dialog = single_tool_dialog();
    assert!(dialog.create_new_branch);
}

#[test]
fn test_new_branch_checkbox_toggle() {
    let mut dialog = single_tool_dialog();
    dialog.worktree_branch = Input::new("feature-branch".to_string());
    dialog.focused_field = 4; // new_branch checkbox field (single tool, with worktree set: yolo=2, worktree=3, new_branch=4)
    assert!(dialog.create_new_branch);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert!(!dialog.create_new_branch);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert!(dialog.create_new_branch);
}

#[test]
fn test_submit_respects_create_new_branch() {
    let mut dialog = single_tool_dialog();
    dialog.worktree_branch = Input::new("feature-branch".to_string());
    dialog.focused_field = 4; // new_branch (yolo=2, worktree=3, new_branch=4)
    dialog.handle_key(key(KeyCode::Char(' '))); // Toggle off

    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert!(!data.create_new_branch);
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn test_new_branch_field_hidden_without_worktree() {
    let mut dialog = single_tool_dialog();
    assert_eq!(dialog.focused_field, 0);

    // Tab through all fields: title(0) -> path(1) -> yolo(2) -> worktree(3) -> group(4) -> wrap to 0
    dialog.handle_key(key(KeyCode::Tab)); // 1
    dialog.handle_key(key(KeyCode::Tab)); // 2 (yolo)
    dialog.handle_key(key(KeyCode::Tab)); // 3 (worktree)
    dialog.handle_key(key(KeyCode::Tab)); // 4 (group)
    assert_eq!(dialog.focused_field, 4);
    dialog.handle_key(key(KeyCode::Tab)); // Should wrap to 0
    assert_eq!(dialog.focused_field, 0);
}

#[test]
fn test_yolo_mode_disabled_by_default() {
    let dialog = multi_tool_dialog();
    assert!(!dialog.yolo_mode);
}

#[test]
fn test_yolo_mode_toggle() {
    let mut dialog = multi_tool_dialog();
    dialog.focused_field = 3; // yolo mode field (tool=2, yolo=3)
    assert!(!dialog.yolo_mode);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert!(dialog.yolo_mode);

    dialog.handle_key(key(KeyCode::Char(' ')));
    assert!(!dialog.yolo_mode);
}

#[test]
fn test_submit_with_yolo_mode_enabled() {
    let mut dialog = multi_tool_dialog();
    dialog.yolo_mode = true;
    dialog.title = Input::new("Test".to_string());

    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert!(data.yolo_mode);
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn test_yolo_mode_submission_without_sandbox_fields() {
    let mut dialog = multi_tool_dialog();
    dialog.yolo_mode = true;
    dialog.title = Input::new("Test".to_string());

    let result = dialog.handle_key(key(KeyCode::Enter));
    match result {
        DialogResult::Submit(data) => {
            assert!(data.yolo_mode);
        }
        _ => panic!("Expected Submit"),
    }
}

#[test]
fn help_content_fits_in_dialog() {
    const BORDER_WIDTH: u16 = 2;
    const INDENT: usize = 2;
    let available_width = (HELP_DIALOG_WIDTH - BORDER_WIDTH) as usize;

    for help in FIELD_HELP {
        let line_width = INDENT + help.description.len();
        assert!(
            line_width <= available_width,
            "Help for '{}': description '{}' exceeds dialog width ({} > {})",
            help.name,
            help.description,
            line_width,
            available_width
        );
    }
}

#[test]
fn test_profile_override_sets_default_tool() {
    let global = Config::default();
    let profile_config = ProfileConfig {
        session: Some(SessionConfigOverride {
            default_tool: Some("opencode".to_string()),
            yolo_mode_default: None,
            ..Default::default()
        }),
        ..Default::default()
    };

    let resolved = merge_configs(global, &profile_config);
    let dialog = NewSessionDialog::new_with_config(
        vec!["claude", "opencode"],
        "/tmp/project".to_string(),
        resolved,
    );

    assert_eq!(
        dialog.tool_index, 1,
        "Profile override should select opencode (index 1)"
    );
    assert_eq!(dialog.available_tools[dialog.tool_index], "opencode");
}

#[test]
fn test_profile_override_beats_global_default_tool() {
    let mut global = Config::default();
    global.session.default_tool = Some("claude".to_string());

    let profile_config = ProfileConfig {
        session: Some(SessionConfigOverride {
            default_tool: Some("opencode".to_string()),
            yolo_mode_default: None,
            ..Default::default()
        }),
        ..Default::default()
    };

    let resolved = merge_configs(global, &profile_config);
    assert_eq!(
        resolved.session.default_tool.as_deref(),
        Some("opencode"),
        "Profile override should take precedence over global default"
    );

    let dialog = NewSessionDialog::new_with_config(
        vec!["claude", "opencode"],
        "/tmp/project".to_string(),
        resolved,
    );

    assert_eq!(
        dialog.tool_index, 1,
        "Profile override should select opencode over global claude"
    );
    assert_eq!(dialog.available_tools[dialog.tool_index], "opencode");
}
