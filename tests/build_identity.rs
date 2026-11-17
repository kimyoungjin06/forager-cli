use anyhow::Result;
use sha2::{Digest, Sha256};
use std::fs;
use std::process::Command;

#[test]
fn doctor_reports_each_alias_image_without_creating_user_state() -> Result<()> {
    let temp = tempfile::tempdir()?;
    for binary in [env!("CARGO_BIN_EXE_forager"), env!("CARGO_BIN_EXE_aoe")] {
        let output = Command::new(binary)
            .args(["doctor", "--json", "--project"])
            .arg(temp.path())
            .env("HOME", temp.path().join("home"))
            .env("XDG_CONFIG_HOME", temp.path().join("config"))
            .env_remove("FORAGER_PROFILE")
            .env_remove("AGENT_OF_EMPIRES_PROFILE")
            .current_dir(temp.path())
            .output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let report: serde_json::Value = serde_json::from_slice(&output.stdout)?;
        assert_eq!(report["build"]["version"], env!("CARGO_PKG_VERSION"));
        // CI debug images can exceed the diagnostic hasher's 256 MiB budget.
        if fs::metadata(binary)?.len() > 256 * 1024 * 1024 {
            assert!(report["build"]["executable_sha256"].is_null());
            assert_eq!(report["build"]["running_image_verified"], false);
            assert_eq!(
                report["build"]["unavailable_reason"],
                "image_unavailable_changed_or_exceeds_byte_budget"
            );
        } else {
            assert_eq!(
                report["build"]["executable_sha256"],
                format!("{:x}", Sha256::digest(fs::read(binary)?))
            );
            assert_eq!(
                report["build"]["running_image_verified"],
                cfg!(target_os = "linux")
            );
            assert!(report["build"]["unavailable_reason"].is_null());
        }
        assert!(!temp.path().join("home").exists());
        assert!(!temp.path().join("config").exists());
    }
    Ok(())
}
