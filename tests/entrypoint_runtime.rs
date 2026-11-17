use std::process::Command;

use anyhow::Result;

const PROBE_WORKERS: &str = "FORAGER_TEST_EXPECTED_RUNTIME_WORKERS";

// Run each environment-dependent probe in its own process, without mutating
// environment variables underneath parallel tests or existing runtimes.
fn probe_runtime(override_value: Option<&str>, expected: usize) -> Result<()> {
    let mut command = Command::new(std::env::current_exe()?);
    command
        .args(["--exact", "runtime_child_probe", "--nocapture"])
        .env(PROBE_WORKERS, expected.to_string())
        .env_remove("TOKIO_WORKER_THREADS");
    if let Some(value) = override_value {
        command.env("TOKIO_WORKER_THREADS", value);
    }
    let output = command.output()?;
    assert!(
        output.status.success(),
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(())
}

#[test]
fn runtime_child_probe() -> Result<()> {
    let Some(expected) = std::env::var_os(PROBE_WORKERS) else {
        return Ok(());
    };
    let expected: usize = expected.to_str().unwrap().parse()?;
    let runtime = forager::entrypoint::build_cli_runtime()?;
    assert_eq!(runtime.metrics().num_workers(), expected);
    runtime.block_on(async {
        let background = tokio::spawn(async {
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
            42
        });
        let migration = tokio::task::spawn_blocking(|| 7);
        let values = tokio::time::timeout(std::time::Duration::from_secs(5), async {
            (background.await.unwrap(), migration.await.unwrap())
        })
        .await
        .expect("async and blocking work must make progress");
        assert_eq!(values, (42, 7));
    });
    Ok(())
}

#[test]
fn default_runtime_is_bounded_and_drives_background_work() -> Result<()> {
    let expected = std::thread::available_parallelism()
        .map_or(1, std::num::NonZeroUsize::get)
        .min(2);
    probe_runtime(None, expected)
}

#[test]
fn explicit_worker_override_preserves_single_and_larger_pools() -> Result<()> {
    probe_runtime(Some("1"), 1)?;
    probe_runtime(Some("4"), 4)
}

#[test]
fn both_executables_reject_invalid_overrides_before_loading_profile() -> Result<()> {
    for binary in [env!("CARGO_BIN_EXE_forager"), env!("CARGO_BIN_EXE_aoe")] {
        for value in ["", "0", "-1", "invalid", "1.5", "184467440737095516160"] {
            let home = tempfile::tempdir()?;
            let output = Command::new(binary)
                .arg("--help")
                .env("HOME", home.path())
                .env("XDG_CONFIG_HOME", home.path().join(".config"))
                .env("TOKIO_WORKER_THREADS", value)
                .output()?;
            assert_eq!(output.status.code(), Some(1));
            assert!(String::from_utf8_lossy(&output.stderr)
                .contains("TOKIO_WORKER_THREADS must be a positive integer"));
            assert!(output.stdout.is_empty());
            assert_eq!(std::fs::read_dir(home.path())?.count(), 0);
        }
    }
    Ok(())
}

#[test]
fn both_executables_start_with_default_and_explicit_override() -> Result<()> {
    for binary in [env!("CARGO_BIN_EXE_forager"), env!("CARGO_BIN_EXE_aoe")] {
        for value in [None, Some("4")] {
            let home = tempfile::tempdir()?;
            let mut command = Command::new(binary);
            command
                .arg("--help")
                .env("HOME", home.path())
                .env("XDG_CONFIG_HOME", home.path().join(".config"))
                .env_remove("TOKIO_WORKER_THREADS");
            if let Some(value) = value {
                command.env("TOKIO_WORKER_THREADS", value);
            }
            let output = command.output()?;
            assert!(output.status.success());
            assert!(String::from_utf8_lossy(&output.stdout).contains("Usage:"));
            assert_eq!(std::fs::read_dir(home.path())?.count(), 0);
        }
    }
    Ok(())
}

#[cfg(unix)]
#[test]
fn non_unicode_override_is_rejected_without_echoing_its_bytes() -> Result<()> {
    use std::os::unix::ffi::OsStringExt;
    let output = Command::new(env!("CARGO_BIN_EXE_forager"))
        .arg("--help")
        .env(
            "TOKIO_WORKER_THREADS",
            std::ffi::OsString::from_vec(vec![0xff]),
        )
        .output()?;
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        String::from_utf8(output.stderr)?,
        "Error: TOKIO_WORKER_THREADS must be a positive integer\n"
    );
    Ok(())
}
