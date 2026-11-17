use anyhow::Result;
use nix::dir::{Dir, Type};
use nix::errno::Errno;
use nix::fcntl::{openat, OFlag};
use nix::sys::stat::Mode;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::fs::File;
use std::io::Read;
use std::os::unix::ffi::OsStrExt;
use std::path::{Component, Path, PathBuf};
use std::time::{Duration, Instant};

// Fixed inspection policy, not runtime settings. Coverage is always reported.
const MAX_ENTRIES: usize = 12_000;
const MAX_DEPTH: usize = 8;
const MAX_CONTENT_BYTES: usize = 16 * 1024 * 1024;
const MAX_TEXT_BYTES: usize = 1024 * 1024;
const MAX_HASH_BYTES: usize = 4 * 1024 * 1024;
const MAX_DURATION: Duration = Duration::from_secs(2);
const EXCLUDED_DIRS: &[&str] = &[
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "__pycache__",
    ".cache",
    ".forager",
    "data",
    "datasets",
];

pub(super) struct AuditIo {
    root: PathBuf,
    anchor: File,
    started: Instant,
    files: Vec<PathBuf>,
    entries: usize,
    bytes: usize,
    reasons: BTreeMap<String, usize>,
    samples: Vec<Value>,
    texts: BTreeMap<PathBuf, String>,
    hashes: BTreeMap<PathBuf, String>,
    entry_limit: usize,
    depth_limit: usize,
    byte_limit: usize,
    duration: Duration,
    inventory_complete: bool,
}

impl AuditIo {
    pub(super) fn new(root: &Path) -> Result<Self> {
        Ok(Self {
            root: root.to_path_buf(),
            anchor: File::open(root)?,
            started: Instant::now(),
            files: vec![],
            entries: 0,
            bytes: 0,
            reasons: BTreeMap::new(),
            samples: vec![],
            texts: BTreeMap::new(),
            hashes: BTreeMap::new(),
            entry_limit: MAX_ENTRIES,
            depth_limit: MAX_DEPTH,
            byte_limit: MAX_CONTENT_BYTES,
            duration: MAX_DURATION,
            inventory_complete: true,
        })
    }

    fn note(&mut self, reason: &str, path: &Path) {
        *self.reasons.entry(reason.into()).or_default() += 1;
        if self.samples.len() < 20 {
            self.samples
                .push(json!({"reason": reason, "path": super::rel_path(&self.root, path)}));
        }
    }

    fn available(&mut self, path: &Path) -> bool {
        if self.started.elapsed() >= self.duration {
            self.note("time_budget", path);
            return false;
        }
        true
    }

    // Walk relative components through directory descriptors. Replacement symlinks
    // and parent traversal cannot redirect content inspection outside the owner.
    fn open(&self, path: &Path) -> std::result::Result<File, Errno> {
        let relative = path.strip_prefix(&self.root).map_err(|_| Errno::EXDEV)?;
        let components = relative.components().collect::<Vec<_>>();
        if components.len() > self.depth_limit + 1 {
            return Err(Errno::ENAMETOOLONG);
        }
        let mut parent = self.anchor.try_clone().map_err(|_| Errno::EIO)?;
        for (index, component) in components.iter().enumerate() {
            let Component::Normal(name) = component else {
                return Err(Errno::EXDEV);
            };
            let mut flags =
                OFlag::O_RDONLY | OFlag::O_CLOEXEC | OFlag::O_NOFOLLOW | OFlag::O_NONBLOCK;
            if index + 1 < components.len() {
                flags |= OFlag::O_DIRECTORY;
            }
            parent = File::from(openat(&parent, Path::new(name), flags, Mode::empty())?);
        }
        Ok(parent)
    }

    pub(super) fn exists(&mut self, path: &Path) -> Option<bool> {
        if !self.available(path) {
            return None;
        }
        match self.open(path) {
            Ok(_) => Some(true),
            Err(Errno::ENOENT) => Some(false),
            Err(_) => {
                self.note("uninspected_reference", path);
                None
            }
        }
    }

    pub(super) fn path_references(&mut self, text: &str, path: &Path) -> Vec<String> {
        let mut paths = super::extract_backtick_paths(text);
        if paths.len() > 256 {
            self.note("reference_budget", path);
            paths.truncate(256);
        }
        paths
    }

    pub(super) fn sibling_inventory_complete(&mut self, parent: &Path) -> bool {
        let inside = parent.strip_prefix(&self.root).is_ok_and(|relative| {
            !relative
                .components()
                .any(|part| EXCLUDED_DIRS.iter().any(|name| part.as_os_str() == *name))
        });
        if !inside {
            self.note("alias_outside_inventory", parent);
        }
        inside && self.inventory_complete
    }

    pub(super) fn metadata(&mut self, path: &Path) -> Option<std::fs::Metadata> {
        if !self.available(path) {
            return None;
        }
        match self
            .open(path)
            .and_then(|f| f.metadata().map_err(|_| Errno::EIO))
        {
            Ok(metadata) => Some(metadata),
            Err(Errno::ENOENT) => None,
            Err(_) => {
                self.note("uninspected_metadata", path);
                None
            }
        }
    }

    pub(super) fn file_size(&mut self, path: &Path) -> u64 {
        self.metadata(path).map(|m| m.len()).unwrap_or_default()
    }

    pub(super) fn scan(&mut self) {
        let mut pending = vec![(self.root.clone(), 0)];
        while let Some((path, depth)) = pending.pop() {
            if !self.available(&path) {
                self.inventory_complete = false;
                break;
            }
            let dir = self.open(&path).and_then(|file| Dir::from_fd(file.into()));
            let Ok(mut dir) = dir else {
                self.note("unreadable_directory", &path);
                self.inventory_complete = false;
                continue;
            };
            let mut entries = vec![];
            for entry in dir.iter() {
                if self.entries >= self.entry_limit || !self.available(&path) {
                    self.note("entry_or_time_budget", &path);
                    self.inventory_complete = false;
                    break;
                }
                let Ok(entry) = entry else {
                    self.note("unreadable_entry", &path);
                    self.inventory_complete = false;
                    continue;
                };
                let name = entry.file_name().to_bytes();
                if name == b"." || name == b".." {
                    continue;
                }
                self.entries += 1;
                entries.push((path.join(OsStr::from_bytes(name)), entry.file_type()));
            }
            entries.sort_by(|a, b| a.0.cmp(&b.0));
            for (child, kind) in entries {
                match kind {
                    Some(Type::Directory) => {
                        let name = child
                            .file_name()
                            .and_then(OsStr::to_str)
                            .unwrap_or_default();
                        if EXCLUDED_DIRS.contains(&name) {
                            self.note("excluded_directory", &child);
                        } else if depth >= self.depth_limit {
                            self.note("depth_budget", &child);
                            self.inventory_complete = false;
                        } else {
                            pending.push((child, depth + 1));
                        }
                    }
                    Some(Type::File) => self.files.push(child),
                    Some(Type::Symlink) => {
                        self.note("symlink_not_followed", &child);
                        self.inventory_complete = false;
                    }
                    _ => {
                        self.note("special_or_unknown_entry", &child);
                        self.inventory_complete = false;
                    }
                }
            }
            if self.entries >= self.entry_limit {
                self.inventory_complete = false;
                break;
            }
        }
        self.files.sort();
    }

    pub(super) fn files(&self) -> &[PathBuf] {
        &self.files
    }
    #[cfg(test)]
    pub(super) fn inventory_complete(&self) -> bool {
        self.inventory_complete
    }

    fn read_bytes(&mut self, path: &Path, per_file_limit: usize) -> Option<Vec<u8>> {
        if !self.available(path) {
            return None;
        }
        let Ok(mut file) = self.open(path) else {
            self.note("unreadable_or_unsafe_content", path);
            return None;
        };
        let Ok(before) = file.metadata() else {
            self.note("metadata_error", path);
            return None;
        };
        if !before.is_file() {
            self.note("non_regular_content", path);
            return None;
        }
        if before.len() >= per_file_limit as u64
            || self.bytes + before.len() as usize + 1 > self.byte_limit
        {
            self.note("content_budget", path);
            return None;
        }
        let mut bytes = Vec::new();
        // Read in chunks so the cooperative deadline also bounds hashing/log reads.
        loop {
            if !self.available(path) {
                return None;
            }
            let mut buffer = [0u8; 16 * 1024];
            let allowance = (per_file_limit - bytes.len())
                .min(self.byte_limit - self.bytes)
                .min(buffer.len());
            if allowance == 0 {
                self.note("content_budget", path);
                return None;
            }
            let Ok(count) = file.read(&mut buffer[..allowance]) else {
                self.note("content_read_error", path);
                return None;
            };
            self.bytes += count;
            if count == 0 {
                break;
            }
            bytes.extend_from_slice(&buffer[..count]);
        }
        let Ok(after) = file.metadata() else {
            self.note("metadata_error", path);
            return None;
        };
        if before.len() != after.len()
            || before.modified().ok() != after.modified().ok()
            || bytes.len() as u64 != after.len()
        {
            self.note("content_changed", path);
            return None;
        }
        Some(bytes)
    }

    pub(super) fn read_text(&mut self, path: &Path) -> Option<String> {
        if let Some(text) = self.texts.get(path) {
            return Some(text.clone());
        }
        let text = String::from_utf8_lossy(&self.read_bytes(path, MAX_TEXT_BYTES)?).into_owned();
        self.texts.insert(path.to_path_buf(), text.clone());
        Some(text)
    }

    pub(super) fn hash(&mut self, path: &Path) -> Option<String> {
        if let Some(hash) = self.hashes.get(path) {
            return Some(hash.clone());
        }
        let hash = format!(
            "{:x}",
            Sha256::digest(self.read_bytes(path, MAX_HASH_BYTES)?)
        );
        self.hashes.insert(path.to_path_buf(), hash.clone());
        Some(hash)
    }

    pub(super) fn coverage(&self) -> Value {
        json!({
            "policy": "bounded_owner_documentation_v1", "complete": self.inventory_complete && self.reasons.keys().all(|reason| reason == "excluded_directory"),
            "inventory_complete": self.inventory_complete, "entries_inspected": self.entries,
            "regular_files": self.files.len(), "content_bytes_read": self.bytes,
            "elapsed_ms": self.started.elapsed().as_millis(), "skipped_counts": self.reasons,
            "skipped_sample": self.samples, "limits": {"entries": self.entry_limit,
            "depth": self.depth_limit, "content_bytes": self.byte_limit,
            "text_bytes_per_file": MAX_TEXT_BYTES, "hash_bytes_per_file": MAX_HASH_BYTES,
            "cooperative_duration_ms": self.duration.as_millis()},
            "excluded_directory_names": EXCLUDED_DIRS, "follows_symlinks": false,
            "time_limit_note": "Checked between filesystem calls; a stalled filesystem call can exceed this cooperative deadline."
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;
    use tempfile::tempdir;

    #[test]
    fn symlink_cycle_external_directory_and_reference_are_not_followed() -> Result<()> {
        let owner = tempdir()?;
        let external = tempdir()?;
        fs::write(external.path().join("AGENT_LOG.md"), "outside\n")?;
        fs::create_dir(owner.path().join("docs"))?;
        symlink(owner.path(), owner.path().join("docs/loop"))?;
        symlink(external.path(), owner.path().join("outputs"))?;
        let mut io = AuditIo::new(owner.path())?;
        io.scan();
        assert!(io.files().is_empty());
        assert_eq!(io.exists(&owner.path().join("outputs/AGENT_LOG.md")), None);
        assert!(io
            .read_text(&owner.path().join("outputs/AGENT_LOG.md"))
            .is_none());
        assert!(io.read_text(&owner.path().join("../outside")).is_none());
        assert_eq!(io.coverage()["content_bytes_read"], 0);
        assert_eq!(io.coverage()["skipped_counts"]["symlink_not_followed"], 2);
        Ok(())
    }

    #[test]
    fn oversized_logs_and_aliases_are_unknown_without_content_reads() -> Result<()> {
        let root = tempdir()?;
        let file = File::create(root.path().join("AGENT_LOG.md"))?;
        file.set_len(MAX_HASH_BYTES as u64 + 1)?;
        let mut io = AuditIo::new(root.path())?;
        assert!(io.read_text(&root.path().join("AGENT_LOG.md")).is_none());
        assert!(io.hash(&root.path().join("AGENT_LOG.md")).is_none());
        assert_eq!(io.coverage()["content_bytes_read"], 0);
        assert_eq!(io.coverage()["complete"], false);
        Ok(())
    }

    #[test]
    fn expected_policy_exclusions_do_not_raise_an_incomplete_audit_alarm() -> Result<()> {
        let root = tempdir()?;
        std::fs::create_dir(root.path().join(".git"))?;
        std::fs::write(root.path().join(".git/AGENT_LOG.md"), "excluded evidence")?;
        let mut io = AuditIo::new(root.path())?;
        io.scan();
        assert!(io.files().is_empty());
        assert_eq!(io.coverage()["complete"], true);
        assert_eq!(io.coverage()["skipped_counts"]["excluded_directory"], 1);
        Ok(())
    }

    #[test]
    fn entry_depth_byte_and_time_limits_report_incomplete_coverage() -> Result<()> {
        let root = tempdir()?;
        fs::create_dir_all(root.path().join("a/b/c"))?;
        for i in 0..12 {
            fs::write(root.path().join(format!("file{i}")), "12345678")?;
        }
        let mut io = AuditIo::new(root.path())?;
        io.entry_limit = 4;
        io.scan();
        assert!(io.entries <= 4);
        assert!(!io.inventory_complete());
        let mut io = AuditIo::new(root.path())?;
        io.depth_limit = 0;
        io.scan();
        assert_eq!(io.coverage()["skipped_counts"]["depth_budget"], 1);
        let mut io = AuditIo::new(root.path())?;
        io.byte_limit = 10;
        assert!(io.read_text(&root.path().join("file0")).is_some());
        assert!(io.read_text(&root.path().join("file1")).is_none());
        assert!(io.bytes <= 10);
        let mut io = AuditIo::new(root.path())?;
        io.duration = Duration::ZERO;
        io.scan();
        assert!(!io.inventory_complete());
        assert_eq!(io.bytes, 0);
        Ok(())
    }

    #[test]
    fn replaced_parent_and_fifo_cannot_be_read_as_documentation() -> Result<()> {
        let owner = tempdir()?;
        let external = tempdir()?;
        fs::create_dir(owner.path().join("docs"))?;
        fs::write(owner.path().join("docs/report.md"), "old")?;
        fs::write(external.path().join("report.md"), "outside")?;
        let mut io = AuditIo::new(owner.path())?;
        io.scan();
        fs::rename(owner.path().join("docs"), owner.path().join("saved"))?;
        symlink(external.path(), owner.path().join("docs"))?;
        assert!(io.read_text(&owner.path().join("docs/report.md")).is_none());
        nix::unistd::mkfifo(
            &owner.path().join("AGENT_LOG.md"),
            Mode::S_IRUSR | Mode::S_IWUSR,
        )?;
        assert!(io.read_text(&owner.path().join("AGENT_LOG.md")).is_none());
        assert_eq!(io.bytes, 0);
        Ok(())
    }
}
