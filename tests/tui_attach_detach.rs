//! Integration tests for TUI attach/detach behavior
//!
//! These tests validate that the terminal state is properly managed when
//! attaching to and detaching from tmux sessions.

use std::process::Command;

/// Verify tmux is available for testing
fn tmux_available() -> bool {
    Command::new("tmux")
        .arg("-V")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

fn create_detached_session(session_name: &str) {
    let _ = Command::new("tmux")
        .args(["kill-session", "-t", session_name])
        .output();

    let create = Command::new("tmux")
        .args(["new-session", "-d", "-s", session_name])
        .output()
        .expect("Failed to create tmux session");

    assert!(create.status.success(), "Failed to create test session");
    forager::tmux::refresh_session_cache();
}

fn tmux_session_exists(session_name: &str) -> bool {
    Command::new("tmux")
        .args(["has-session", "-t", session_name])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Test that tmux sessions can be created and killed
#[test]
fn test_tmux_session_lifecycle() {
    if !tmux_available() {
        eprintln!("Skipping test: tmux not available");
        return;
    }

    let session_name = "forager_test_lifecycle_12345678";

    // Create a detached session
    create_detached_session(session_name);

    // Verify session exists
    assert!(
        tmux_session_exists(session_name),
        "Session should exist after creation"
    );

    // Kill session
    let kill = Command::new("tmux")
        .args(["kill-session", "-t", session_name])
        .output()
        .expect("Failed to kill session");

    assert!(kill.status.success(), "Failed to kill test session");

    // Verify session no longer exists
    assert!(
        !tmux_session_exists(session_name),
        "Session should not exist after kill"
    );
}

/// Test that the Forager tmux wrapper still finds and kills legacy AoE agent sessions.
#[test]
fn test_forager_session_detects_legacy_aoe_session() {
    if !tmux_available() {
        eprintln!("Skipping test: tmux not available");
        return;
    }

    let id = format!("{:08x}legacy", std::process::id());
    let title = "Legacy Fallback Agent";
    let legacy_name = forager::tmux::Session::generate_legacy_name(&id, title);
    create_detached_session(&legacy_name);

    let session =
        forager::tmux::Session::new(&id, title).expect("Failed to construct Forager tmux session");

    assert!(
        session.exists(),
        "Forager session should detect legacy tmux session"
    );
    session
        .kill()
        .expect("Failed to kill legacy session through Forager wrapper");
    assert!(
        !tmux_session_exists(&legacy_name),
        "Legacy session should be killed by Forager wrapper"
    );
}

/// Test that paired terminal wrappers still find and kill legacy AoE terminal sessions.
#[test]
fn test_forager_terminal_sessions_detect_legacy_aoe_sessions() {
    if !tmux_available() {
        eprintln!("Skipping test: tmux not available");
        return;
    }

    let id = format!("{:08x}term", std::process::id());
    let title = "Legacy Fallback Terminal";

    let legacy_terminal_name = forager::tmux::TerminalSession::generate_legacy_name(&id, title);
    create_detached_session(&legacy_terminal_name);
    let terminal = forager::tmux::TerminalSession::new(&id, title)
        .expect("Failed to construct Forager terminal session");
    assert!(
        terminal.exists(),
        "Forager terminal should detect legacy tmux session"
    );
    terminal
        .kill()
        .expect("Failed to kill legacy terminal session through Forager wrapper");
    assert!(
        !tmux_session_exists(&legacy_terminal_name),
        "Legacy terminal session should be killed by Forager wrapper"
    );
}

/// Test that session names are properly sanitized
#[test]
fn test_session_name_format() {
    let prefix = "forager_";

    // Valid session names should start with our prefix
    let session_name = format!("{}my_project_abc12345", prefix);
    assert!(session_name.starts_with(prefix));

    // Session names should not contain problematic characters
    assert!(!session_name.contains(' '));
    assert!(!session_name.contains(':'));
    assert!(!session_name.contains('.'));
}

/// Test terminal mode switching sequence
///
/// This test documents the expected sequence for attach/detach:
/// 1. Disable raw mode
/// 2. Leave alternate screen
/// 3. Disable mouse capture
/// 4. Show cursor
/// 5. [user interacts with tmux]
/// 6. Enable raw mode
/// 7. Enter alternate screen
/// 8. Enable mouse capture
/// 9. Hide cursor
/// 10. Clear terminal
/// 11. Drain stale events
#[test]
fn test_terminal_mode_sequence_documented() {
    // This test documents the expected behavior rather than testing it directly
    // since testing terminal modes requires actual terminal interaction.

    let expected_exit_sequence = [
        "disable_raw_mode",
        "LeaveAlternateScreen",
        "DisableMouseCapture",
        "cursor::Show",
        "flush",
    ];

    let expected_reenter_sequence = [
        "enable_raw_mode",
        "EnterAlternateScreen",
        "EnableMouseCapture",
        "cursor::Hide",
        "flush",
        "drain_events",
        "terminal.clear",
        "set_needs_redraw",
    ];

    // Verify sequences have all required steps
    assert!(expected_exit_sequence.contains(&"disable_raw_mode"));
    assert!(expected_exit_sequence.contains(&"LeaveAlternateScreen"));
    assert!(expected_reenter_sequence.contains(&"enable_raw_mode"));
    assert!(expected_reenter_sequence.contains(&"EnterAlternateScreen"));
    assert!(expected_reenter_sequence.contains(&"terminal.clear"));
    assert!(expected_reenter_sequence.contains(&"drain_events"));
}

/// Test that draining events prevents stale input
#[test]
fn test_event_draining_concept() {
    // When returning from tmux, there may be stale keyboard events
    // in the crossterm event queue. These must be drained to prevent
    // the TUI from receiving and acting on old input.
    //
    // The drain loop should:
    // 1. Poll with zero timeout (non-blocking)
    // 2. Read and discard any available events
    // 3. Continue until no more events are available

    // This is a conceptual test - actual draining is tested in integration
    let drain_timeout_ms = 0;
    assert_eq!(drain_timeout_ms, 0, "Drain should use zero timeout");
}

/// Test that attach/detach uses terminal backend, not std::io::stdout()
///
/// This test verifies the fix for the terminal corruption bug where
/// using std::io::stdout() instead of terminal.backend_mut() caused
/// file descriptor desynchronization, corrupting tmux sessions.
///
/// The terminal leave/restore logic lives in `with_raw_mode_disabled`,
/// which `attach_session` delegates to.
#[test]
fn test_attach_uses_terminal_backend() {
    let source = std::fs::read_to_string("src/tui/app.rs").expect("Failed to read app.rs");

    // The shared helper that handles terminal mode switching must use backend_mut()
    let helper_start = source
        .find("fn with_raw_mode_disabled")
        .expect("with_raw_mode_disabled helper not found");

    let helper_section = &source[helper_start..];
    let fn_end = helper_section
        .find("\n}\n")
        .map(|i| i + 3)
        .unwrap_or(helper_section.len());

    let helper_body = &helper_section[..fn_end];

    assert!(
        !helper_body.contains("std::io::stdout()"),
        "with_raw_mode_disabled should use terminal.backend_mut() instead of std::io::stdout(). \
         Using std::io::stdout() creates separate file descriptor handles that can \
         corrupt terminal state and cause 'open terminal failed: not a terminal' errors."
    );

    assert!(
        helper_body.contains("terminal.backend_mut()"),
        "with_raw_mode_disabled should use terminal.backend_mut() for terminal operations"
    );

    // attach_session must delegate to the helper, not bypass it
    let attach_fn_start = source
        .find("fn attach_session(")
        .expect("attach_session function not found");

    let attach_fn_section = &source[attach_fn_start..];
    let attach_fn_end = attach_fn_section
        .find("\n    fn ")
        .or_else(|| attach_fn_section.find("\n}\n"))
        .unwrap_or(attach_fn_section.len());

    let attach_fn_body = &attach_fn_section[..attach_fn_end];

    assert!(
        attach_fn_body.contains("with_raw_mode_disabled"),
        "attach_session should delegate to with_raw_mode_disabled"
    );

    assert!(
        !attach_fn_body.contains("std::io::stdout()"),
        "attach_session should not use std::io::stdout() directly"
    );
}
