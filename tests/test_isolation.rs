#[test]
#[cfg(unix)]
fn private_test_worker_cleans_failure_and_interruption_without_touching_other_sessions() {
    let status = std::process::Command::new("python3")
        .arg(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/support/test_isolated_test_process.py"),
        )
        .status()
        .expect("start isolation regression suite");
    assert!(
        status.success(),
        "private test worker isolation regression failed"
    );
}
