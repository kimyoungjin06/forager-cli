use std::path::Path;
use std::process::{Command, Stdio};

use anyhow::{ensure, Result};

/// Return true in the parent after the exact test passed in its private worker.
pub fn run(test_name: &str) -> Result<bool> {
    if std::env::var("FORAGER_PRIVATE_TEST_NAME").as_deref() == Ok(test_name) {
        let root = std::env::var("FORAGER_PRIVATE_TEST_ROOT")?;
        for (key, directory) in [
            ("HOME", "home"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_STATE_HOME", "state"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_HOME", "data"),
            ("TMUX_TMPDIR", "tmux"),
        ] {
            ensure!(
                std::env::var_os(key).as_deref()
                    == Some(Path::new(&root).join(directory).as_os_str()),
                "private test worker environment mismatch: {key}"
            );
        }
        ensure!(std::env::var_os("TMUX").is_none(), "inherited tmux target");
        return Ok(false);
    }
    let mut child = Command::new("python3")
        .arg(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/support/isolated_test_process.py"))
        .args(["--test-name", test_name, "--"])
        .arg(std::env::current_exe()?)
        .args(["--exact", test_name, "--include-ignored", "--nocapture"])
        .stdin(Stdio::piped())
        .spawn()?;
    // Holding this pipe lets the supervisor detect a panic or killed parent.
    let control = child.stdin.take();
    let status = child.wait()?;
    drop(control);
    ensure!(
        status.success(),
        "private test worker failed: {test_name}: {status}"
    );
    Ok(true)
}
