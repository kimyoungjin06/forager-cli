//! Bounded, owner-only storage for local-agent wiki presentation receipts.
//!
//! The ledger records transport observations only. It is profile-local,
//! compacted to a fixed event count, and never stores prompt text or wiki
//! entry bodies.

use std::fs::{self, File};
use std::io::{ErrorKind, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::offdesk::WikiContextPresentationReceiptV1;
use crate::process::FilesystemObjectSnapshot;

const LEDGER_SCHEMA: &str = "wiki_context_presentation_ledger.v1";
const LEDGER_FILE: &str = "wiki_context_presentations.v1.json";
const LOCK_FILE: &str = ".wiki_context_presentations.v1.lock";
const MAX_LEDGER_BYTES: usize = 2 * 1024 * 1024;
const MAX_RECEIPTS: usize = 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WikiContextPresentationLedgerV1 {
    schema: String,
    receipts: Vec<WikiContextPresentationReceiptV1>,
}

impl Default for WikiContextPresentationLedgerV1 {
    fn default() -> Self {
        Self {
            schema: LEDGER_SCHEMA.to_string(),
            receipts: Vec::new(),
        }
    }
}

impl WikiContextPresentationLedgerV1 {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == LEDGER_SCHEMA,
            "wiki context presentation ledger schema must be {LEDGER_SCHEMA}"
        );
        ensure!(
            self.receipts.len() <= MAX_RECEIPTS,
            "wiki context presentation ledger exceeds its receipt budget"
        );
        let mut ids = std::collections::HashSet::new();
        for receipt in &self.receipts {
            receipt.validate()?;
            ensure!(
                ids.insert(receipt.receipt_id.as_str()),
                "wiki context presentation receipt IDs must be unique"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub(crate) struct WikiContextReceiptStore {
    profile_root: PathBuf,
}

impl WikiContextReceiptStore {
    pub(crate) fn read_only(profile: &str) -> Result<Self> {
        Ok(Self {
            profile_root: crate::session::profile_dir_path(profile)?,
        })
    }

    pub(crate) fn writable(profile: &str) -> Result<Self> {
        Ok(Self {
            profile_root: crate::session::get_profile_dir(profile)?,
        })
    }

    #[cfg(test)]
    fn for_root(profile_root: PathBuf) -> Self {
        Self { profile_root }
    }

    pub(crate) fn history_for_session(
        &self,
        session_id: &str,
    ) -> Result<Vec<WikiContextPresentationReceiptV1>> {
        ensure_safe_session_id(session_id)?;
        Ok(self
            .load_read_only()?
            .receipts
            .into_iter()
            .filter(|receipt| receipt.session_id.as_deref() == Some(session_id))
            .collect())
    }

    pub(crate) fn append(&self, receipt: WikiContextPresentationReceiptV1) -> Result<()> {
        receipt.validate()?;
        let session_id = receipt
            .session_id
            .as_deref()
            .context("session presentation receipt requires a session ID")?;
        ensure_safe_session_id(session_id)?;
        fs::create_dir_all(&self.profile_root).with_context(|| {
            format!(
                "create wiki context receipt profile root {}",
                self.profile_root.display()
            )
        })?;
        let root = open_exact_profile_root(&self.profile_root)?;
        let lock = open_or_create_lock(&root)?;
        FileExt::lock_exclusive(&lock).context("lock wiki context presentation ledger")?;

        let result = (|| -> Result<()> {
            ensure_active_root(&self.profile_root, &root)?;
            let mut ledger = read_ledger_from_root(&root)?;
            ensure!(
                !ledger
                    .receipts
                    .iter()
                    .any(|existing| existing.receipt_id == receipt.receipt_id),
                "wiki context presentation receipt already exists"
            );
            ledger.receipts.push(receipt);
            if ledger.receipts.len() > MAX_RECEIPTS {
                let excess = ledger.receipts.len() - MAX_RECEIPTS;
                ledger.receipts.drain(..excess);
            }
            ledger.validate()?;
            let bytes = loop {
                let bytes = serde_json::to_vec_pretty(&ledger)?;
                if bytes.len() <= MAX_LEDGER_BYTES {
                    break bytes;
                }
                ensure!(
                    ledger.receipts.len() > 1,
                    "one wiki context presentation receipt exceeds the ledger byte budget"
                );
                ledger.receipts.remove(0);
            };
            replace_ledger(&root, &self.profile_root, &bytes)
        })();
        let _ = FileExt::unlock(&lock);
        result
    }

    fn load_read_only(&self) -> Result<WikiContextPresentationLedgerV1> {
        match fs::symlink_metadata(&self.profile_root) {
            Err(error) if error.kind() == ErrorKind::NotFound => {
                return Ok(WikiContextPresentationLedgerV1::default())
            }
            Err(error) => return Err(error).context("inspect wiki context receipt profile root"),
            Ok(metadata) => ensure!(
                metadata.is_dir() && !metadata.file_type().is_symlink(),
                "wiki context receipt profile root is not one safe directory"
            ),
        }
        let root = open_exact_profile_root(&self.profile_root)?;
        let ledger = read_ledger_from_root(&root)?;
        ensure_active_root(&self.profile_root, &root)?;
        Ok(ledger)
    }
}

fn ensure_safe_session_id(session_id: &str) -> Result<()> {
    ensure!(
        !session_id.is_empty() && session_id.trim() == session_id && session_id.len() <= 256,
        "session ID is invalid for wiki context receipts"
    );
    ensure!(
        !session_id.contains(['/', '\\', '\0']),
        "session ID contains an unsafe path character"
    );
    Ok(())
}

fn open_exact_profile_root(path: &Path) -> Result<File> {
    let path_metadata = fs::symlink_metadata(path)
        .with_context(|| format!("inspect wiki context receipt root {}", path.display()))?;
    ensure!(
        path_metadata.is_dir() && !path_metadata.file_type().is_symlink(),
        "wiki context receipt profile root is not one safe directory"
    );
    let root = crate::process::open_bounded_root(path)
        .with_context(|| format!("open wiki context receipt root {}", path.display()))?;
    ensure!(
        crate::process::filesystem_file_identity(&path_metadata)
            == crate::process::filesystem_file_identity(&root.metadata()?),
        "wiki context receipt root path and descriptor identities differ"
    );
    Ok(root)
}

fn ensure_active_root(path: &Path, root: &File) -> Result<()> {
    let reopened = crate::process::open_bounded_root(path)
        .with_context(|| format!("reopen wiki context receipt root {}", path.display()))?;
    ensure!(
        crate::process::filesystem_file_identity(&reopened.metadata()?)
            == crate::process::filesystem_file_identity(&root.metadata()?),
        "active profile root changed during wiki context receipt access"
    );
    Ok(())
}

fn open_or_create_lock(root: &File) -> Result<File> {
    let path = Path::new(LOCK_FILE);
    let file = match crate::process::create_new_bounded_regular_file(root, path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            crate::process::open_bounded_regular_file(root, path)
                .context("open wiki context receipt lock")?
        }
        Err(error) => return Err(error).context("create wiki context receipt lock"),
    };
    ensure_single_link_owner_only(&file, LOCK_FILE, true)?;
    Ok(file)
}

fn read_ledger_from_root(root: &File) -> Result<WikiContextPresentationLedgerV1> {
    let mut file = match crate::process::open_bounded_regular_file(root, Path::new(LEDGER_FILE)) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            return Ok(WikiContextPresentationLedgerV1::default())
        }
        Err(error) => return Err(error).context("open wiki context presentation ledger"),
    };
    ensure_single_link_owner_only(&file, LEDGER_FILE, false)?;
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take((MAX_LEDGER_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .context("read wiki context presentation ledger")?;
    ensure!(
        bytes.len() <= MAX_LEDGER_BYTES,
        "wiki context presentation ledger exceeds its byte budget"
    );
    let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        before == after,
        "wiki context presentation ledger changed while read"
    );
    let active = crate::process::open_bounded_regular_file(root, Path::new(LEDGER_FILE))
        .context("reopen active wiki context presentation ledger")?;
    ensure_single_link_owner_only(&active, LEDGER_FILE, false)?;
    ensure!(
        FilesystemObjectSnapshot::from_metadata(&active.metadata()?) == after,
        "active wiki context presentation ledger was replaced while read"
    );
    let ledger: WikiContextPresentationLedgerV1 =
        serde_json::from_slice(&bytes).context("parse wiki context presentation ledger")?;
    ledger.validate()?;
    Ok(ledger)
}

fn replace_ledger(root: &File, root_path: &Path, bytes: &[u8]) -> Result<()> {
    let temporary_name = PathBuf::from(format!(".{LEDGER_FILE}.{}.tmp", Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut temporary = crate::process::create_new_bounded_regular_file(root, &temporary_name)
            .context("create wiki context presentation ledger temporary file")?;
        set_owner_only(&temporary)?;
        temporary
            .write_all(bytes)
            .context("write wiki context presentation ledger temporary file")?;
        temporary
            .sync_all()
            .context("sync wiki context presentation ledger temporary file")?;
        drop(temporary);
        ensure_active_root(root_path, root)?;
        crate::process::replace_bounded_root_file(root, &temporary_name, Path::new(LEDGER_FILE))
            .context("atomically replace wiki context presentation ledger")?;
        ensure_active_root(root_path, root)?;
        let committed = read_ledger_from_root(root)?;
        ensure!(
            serde_json::to_vec_pretty(&committed)? == bytes,
            "committed wiki context presentation ledger bytes differ"
        );
        root.sync_all()
            .context("sync wiki context presentation ledger directory")?;
        ensure_active_root(root_path, root)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = crate::process::remove_bounded_root_file(root, &temporary_name);
    }
    result
}

fn ensure_single_link_owner_only(file: &File, label: &str, allow_fix: bool) -> Result<()> {
    let metadata = file
        .metadata()
        .with_context(|| format!("inspect {label}"))?;
    ensure!(metadata.is_file(), "{label} is not a regular file");
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(metadata.nlink() == 1, "{label} must have exactly one link");
        if metadata.permissions().mode() & 0o777 != 0o600 {
            if !allow_fix {
                bail!("{label} is not owner-only");
            }
            set_owner_only(file)?;
        }
    }
    Ok(())
}

fn set_owner_only(file: &File) -> Result<()> {
    #[cfg(unix)]
    file.set_permissions(fs::Permissions::from_mode(0o600))
        .context("set owner-only wiki context receipt permissions")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{
        WikiContextAgentMode, WikiContextAgentTool, WikiContextPresentationStatus,
        WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA,
    };
    use chrono::{TimeZone, Utc};

    fn receipt(session_id: &str, suffix: &str) -> WikiContextPresentationReceiptV1 {
        WikiContextPresentationReceiptV1 {
            schema: WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("wiki_context_receipt_{suffix}"),
            session_id: Some(session_id.to_string()),
            session_runtime_identity_sha256: Some("e".repeat(64)),
            agent_tool: WikiContextAgentTool::Codex,
            project_key: "demo".to_string(),
            wiki_profile: "demo-wiki".to_string(),
            project_root_identity: "r".repeat(64),
            agent_mode: WikiContextAgentMode::Shared,
            projection_sha256: "a".repeat(64),
            brief_file_sha256: "b".repeat(64),
            prepared_at: Utc.with_ymd_and_hms(2026, 8, 21, 0, 0, 0).unwrap(),
            attempted_at: None,
            presented_at: None,
            presentation_status: WikiContextPresentationStatus::PreparedOnly,
            pane_before_sha256: None,
            pane_after_sha256: None,
            agent_acknowledgement: None,
            operator_verification: None,
        }
    }

    #[test]
    fn missing_store_is_read_only_and_empty() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let missing = temp.path().join("missing");
        let store = WikiContextReceiptStore::for_root(missing.clone());
        assert!(store.history_for_session("session-1")?.is_empty());
        assert!(!missing.exists());
        Ok(())
    }

    #[test]
    fn append_preserves_history_and_latest_receipt() -> Result<()> {
        let temp = tempfile::tempdir()?;
        let root = temp.path().join("profile");
        fs::create_dir(&root)?;
        let store = WikiContextReceiptStore::for_root(root);
        store.append(receipt("session-1", "one"))?;
        store.append(receipt("session-1", "two"))?;
        store.append(receipt("session-2", "other"))?;

        let history = store.history_for_session("session-1")?;
        assert_eq!(history.len(), 2);
        assert_eq!(
            history.last().unwrap().receipt_id,
            "wiki_context_receipt_two"
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn read_rejects_world_readable_or_hardlinked_ledger() -> Result<()> {
        use std::os::unix::fs::PermissionsExt;

        let temp = tempfile::tempdir()?;
        let root = temp.path().join("profile");
        fs::create_dir(&root)?;
        let store = WikiContextReceiptStore::for_root(root.clone());
        store.append(receipt("session-1", "one"))?;
        let ledger = root.join(LEDGER_FILE);
        fs::set_permissions(&ledger, fs::Permissions::from_mode(0o644))?;
        assert!(store.history_for_session("session-1").is_err());
        fs::set_permissions(&ledger, fs::Permissions::from_mode(0o600))?;
        fs::hard_link(&ledger, root.join("alias.json"))?;
        assert!(store.history_for_session("session-1").is_err());
        Ok(())
    }
}
