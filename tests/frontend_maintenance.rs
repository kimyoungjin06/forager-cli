#[test]
#[cfg(unix)]
fn maintenance_preserves_agent_identity_and_holds_racing_or_failed_cutovers() {
    let status = std::process::Command::new("python3")
        .arg(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/support/test_frontend_maintenance.py"),
        )
        .env(
            "FORAGER_MAINTENANCE_TEST_BINARY",
            env!("CARGO_BIN_EXE_forager"),
        )
        .env("FORAGER_MAINTENANCE_TEST_ALIAS", env!("CARGO_BIN_EXE_aoe"))
        .status()
        .expect("start maintenance regression fixtures");
    assert!(status.success(), "frontend maintenance regression failed");
}
