#[test]
fn waiting_notification_offline_safety() {
    let output = std::process::Command::new("python3")
        .arg(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/support/test_waiting_notification_safety.py"
        ))
        .output()
        .expect("python3 is required for Telegram safety regressions");
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn session_stop_notification_offline_safety() {
    let output = std::process::Command::new("python3")
        .arg(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/support/test_session_stop_notification.py"
        ))
        .output()
        .expect("python3 is required for Telegram safety regressions");
    assert!(
        output.status.success(),
        "{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}
