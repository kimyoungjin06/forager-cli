//! Explicit diagnostics for the selected executable, independent of version tags.
use std::fs::File;
use std::io::Read;
use std::path::Path;

use serde::Serialize;
use sha2::{Digest, Sha256};

#[derive(Debug, Serialize)]
pub struct BuildIdentity {
    pub version: &'static str,
    pub target_os: &'static str,
    pub target_arch: &'static str,
    pub executable_path: Option<String>,
    pub executable_sha256: Option<String>,
    pub identity_source: &'static str,
    pub running_image_verified: bool,
    pub unavailable_reason: Option<&'static str>,
}

pub fn report() -> BuildIdentity {
    let path = std::env::current_exe().ok();
    #[cfg(target_os = "linux")]
    let (read_path, source) = (
        Some(std::path::PathBuf::from("/proc/self/exe")),
        "linux_proc_self_exe",
    );
    #[cfg(not(target_os = "linux"))]
    let (read_path, source) = (path.clone(), "current_executable_path");
    let digest = read_path
        .as_deref()
        .and_then(|p| hash_file(p, 256 * 1024 * 1024).ok());
    BuildIdentity {
        version: env!("CARGO_PKG_VERSION"),
        target_os: std::env::consts::OS,
        target_arch: std::env::consts::ARCH,
        executable_path: path.map(|p| p.to_string_lossy().into_owned()),
        running_image_verified: cfg!(target_os = "linux") && digest.is_some(),
        unavailable_reason: digest
            .is_none()
            .then_some("image_unavailable_changed_or_exceeds_byte_budget"),
        executable_sha256: digest,
        identity_source: source,
    }
}

fn hash_file(path: &Path, max_bytes: u64) -> anyhow::Result<String> {
    let mut file = File::open(path)?;
    let before = file.metadata()?;
    anyhow::ensure!(
        before.is_file() && before.len() <= max_bytes,
        "image byte budget"
    );
    let mut hash = Sha256::new();
    let mut bytes = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        bytes += read as u64;
        anyhow::ensure!(bytes <= max_bytes, "image grew beyond byte budget");
        hash.update(&buffer[..read]);
    }
    anyhow::ensure!(
        bytes == before.len()
            && crate::process::FilesystemObjectSnapshot::from_metadata(&before)
                == crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?),
        "image changed during hashing"
    );
    Ok(format!("{:x}", hash.finalize()))
}

#[cfg(test)]
mod tests {
    #[test]
    fn unavailable_or_oversized_image_has_no_substitute_digest() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("image");
        std::fs::write(&file, b"image").unwrap();
        assert!(super::hash_file(&file, 4).is_err());
        assert!(super::hash_file(&root.path().join("missing"), 256).is_err());
    }
}
