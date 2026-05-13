//! Profile-scoped offdesk tick lock.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Duration, Utc};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{ErrorKind, Read, Seek, SeekFrom, Write};
use std::path::Path;

const TICK_LOCK_FILE: &str = "offdesk_tick.lock";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OffdeskTickLockMetadata {
    pub pid: u32,
    pub acquired_at: DateTime<Utc>,
    pub stale_after_sec: i64,
}

#[derive(Debug)]
pub struct OffdeskTickLockGuard {
    file: File,
    stale_metadata_replaced: bool,
}

impl OffdeskTickLockGuard {
    pub fn acquire(
        profile_dir: impl AsRef<Path>,
        now: DateTime<Utc>,
        stale_after: Duration,
    ) -> Result<Self> {
        let path = profile_dir.as_ref().join(TICK_LOCK_FILE);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }

        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&path)
            .with_context(|| format!("failed to open {}", path.display()))?;

        if let Err(error) = file.try_lock_exclusive() {
            if error.kind() == ErrorKind::WouldBlock {
                let metadata = read_metadata(&mut file).ok().flatten();
                bail!("{}", locked_message(&path, metadata.as_ref()));
            }
            return Err(error.into());
        }

        let stale_metadata_replaced = read_metadata(&mut file)?
            .is_some_and(|metadata| metadata.acquired_at + stale_after <= now);
        let metadata = OffdeskTickLockMetadata {
            pid: std::process::id(),
            acquired_at: now,
            stale_after_sec: stale_after.num_seconds().max(1),
        };
        write_metadata(&mut file, &metadata)?;

        Ok(Self {
            file,
            stale_metadata_replaced,
        })
    }

    pub fn stale_metadata_replaced(&self) -> bool {
        self.stale_metadata_replaced
    }
}

impl Drop for OffdeskTickLockGuard {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

fn read_metadata(file: &mut File) -> Result<Option<OffdeskTickLockMetadata>> {
    file.seek(SeekFrom::Start(0))?;
    let mut content = String::new();
    file.read_to_string(&mut content)?;
    if content.trim().is_empty() {
        return Ok(None);
    }
    Ok(Some(serde_json::from_str(&content)?))
}

fn write_metadata(file: &mut File, metadata: &OffdeskTickLockMetadata) -> Result<()> {
    file.seek(SeekFrom::Start(0))?;
    file.set_len(0)?;
    file.write_all(serde_json::to_string_pretty(metadata)?.as_bytes())?;
    file.flush()?;
    Ok(())
}

fn locked_message(path: &Path, metadata: Option<&OffdeskTickLockMetadata>) -> String {
    if let Some(metadata) = metadata {
        format!(
            "offdesk tick already running: {} held by pid {} since {}",
            path.display(),
            metadata.pid,
            metadata.acquired_at
        )
    } else {
        format!("offdesk tick already running: {}", path.display())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn concurrent_lock_is_rejected() -> Result<()> {
        let temp = tempdir()?;
        let now = Utc::now();
        let _first = OffdeskTickLockGuard::acquire(temp.path(), now, Duration::minutes(30))?;

        let second = OffdeskTickLockGuard::acquire(temp.path(), now, Duration::minutes(30));

        assert!(second.is_err());
        assert!(format!("{:#}", second.unwrap_err()).contains("already running"));
        Ok(())
    }

    #[test]
    fn old_metadata_is_marked_replaced_when_lock_is_free() -> Result<()> {
        let temp = tempdir()?;
        let path = temp.path().join(TICK_LOCK_FILE);
        fs::write(
            &path,
            serde_json::to_string_pretty(&OffdeskTickLockMetadata {
                pid: 123,
                acquired_at: Utc::now() - Duration::minutes(60),
                stale_after_sec: 60,
            })?,
        )?;

        let guard = OffdeskTickLockGuard::acquire(temp.path(), Utc::now(), Duration::minutes(30))?;

        assert!(guard.stale_metadata_replaced());
        Ok(())
    }
}
