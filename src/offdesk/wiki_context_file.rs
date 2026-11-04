//! Descriptor-bound, atomic storage for a generated wiki brief.

use std::fs::File;
use std::io::{ErrorKind, Read, Write};
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::process::FilesystemObjectSnapshot;

pub(crate) const MAX_WIKI_BRIEF_RENDERED_BYTES: usize = 16 * 1024;
const ROOT_IDENTITY_HASH_DOMAIN: &[u8] = b"forager.wiki_context.project_root.v1\0";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WikiBriefWriteStatus {
    Fresh,
    Unchanged,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WikiBriefWriteResult {
    pub project_root_identity: String,
    pub brief_file_sha256: String,
    pub status: WikiBriefWriteStatus,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WikiBriefCurrentSnapshot {
    pub identity: WikiBriefWriteResult,
    pub bytes: Vec<u8>,
}

#[derive(Debug)]
pub(crate) struct WikiBriefOutputBoundary {
    root_path: PathBuf,
    root: File,
    root_identity: (u64, u64),
    root_identity_sha256: String,
    file_name: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum TargetSnapshot {
    Absent,
    Present {
        object: FilesystemObjectSnapshot,
        bytes: Vec<u8>,
    },
}

impl TargetSnapshot {
    fn exact_bytes(&self, expected: &[u8]) -> bool {
        matches!(self, Self::Present { bytes, .. } if bytes == expected)
    }

    fn content_sha256(&self) -> Option<String> {
        match self {
            Self::Absent => None,
            Self::Present { bytes, .. } => Some(sha256_hex(bytes)),
        }
    }
}

impl WikiBriefOutputBoundary {
    pub(crate) fn open(output_path: &Path) -> Result<Self> {
        let absolute = if output_path.is_absolute() {
            output_path.to_path_buf()
        } else {
            std::env::current_dir()?.join(output_path)
        };
        let file_name = absolute
            .file_name()
            .map(PathBuf::from)
            .context("wiki brief output file name is missing")?;
        ensure_single_name(&file_name)?;
        let parent = absolute
            .parent()
            .context("wiki brief output parent is missing")?;
        let root_path = parent.to_path_buf();
        let path_metadata = std::fs::symlink_metadata(&root_path)
            .with_context(|| format!("inspect wiki brief root {}", root_path.display()))?;
        ensure!(
            !path_metadata.file_type().is_symlink() && path_metadata.is_dir(),
            "wiki brief output root is not one safe directory"
        );
        let root = crate::process::open_bounded_root(&root_path)
            .with_context(|| format!("open wiki brief root {}", root_path.display()))?;
        let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("wiki brief root lacks a stable descriptor identity")?;
        ensure!(
            crate::process::filesystem_file_identity(&path_metadata) == Some(root_identity),
            "wiki brief root path and descriptor identities differ"
        );
        let root_identity_sha256 = wiki_context_root_identity_sha256(root_identity);
        let boundary = Self {
            root_path,
            root,
            root_identity,
            root_identity_sha256,
            file_name,
        };
        boundary.ensure_active_root()?;
        Ok(boundary)
    }

    pub(crate) fn write(&self, bytes: &[u8]) -> Result<WikiBriefWriteResult> {
        self.write_with_hook(bytes, |_| Ok(()))
    }

    pub(crate) fn project_root_identity(&self) -> &str {
        &self.root_identity_sha256
    }

    pub(crate) fn inspect_current(&self) -> Result<Option<WikiBriefCurrentSnapshot>> {
        self.ensure_active_root()?;
        let snapshot = self.read_target(&self.file_name)?;
        self.ensure_active_root()?;
        Ok(match snapshot {
            TargetSnapshot::Absent => None,
            TargetSnapshot::Present { bytes, .. } => Some(WikiBriefCurrentSnapshot {
                identity: WikiBriefWriteResult {
                    project_root_identity: self.root_identity_sha256.clone(),
                    brief_file_sha256: sha256_hex(&bytes),
                    status: WikiBriefWriteStatus::Unchanged,
                },
                bytes,
            }),
        })
    }

    fn write_with_hook<F>(&self, bytes: &[u8], hook: F) -> Result<WikiBriefWriteResult>
    where
        F: FnOnce(&Path) -> Result<()>,
    {
        ensure!(!bytes.is_empty(), "wiki brief output must not be empty");
        ensure!(
            bytes.len() <= MAX_WIKI_BRIEF_RENDERED_BYTES,
            "wiki brief rendered bytes exceed the output budget"
        );
        self.ensure_active_root()?;
        let initial = self.read_target(&self.file_name)?;
        if initial.exact_bytes(bytes) {
            self.ensure_active_root()?;
            return Ok(self.unchanged_result(&initial));
        }

        let temporary_name = PathBuf::from(format!(
            ".{}.{}.tmp",
            self.file_name.to_string_lossy(),
            Uuid::new_v4()
        ));
        let result = (|| -> Result<WikiBriefWriteResult> {
            let mut temporary =
                crate::process::create_new_bounded_regular_file(&self.root, &temporary_name)
                    .context("create bounded wiki brief temporary file")?;
            ensure_secure_temporary(&temporary)?;
            temporary
                .write_all(bytes)
                .context("write bounded wiki brief temporary file")?;
            temporary
                .sync_all()
                .context("sync bounded wiki brief temporary file")?;
            drop(temporary);

            hook(&temporary_name)?;
            self.ensure_active_root()?;
            let durable_temporary = self.read_target(&temporary_name)?;
            ensure!(
                durable_temporary.exact_bytes(bytes),
                "wiki brief temporary bytes changed before replacement"
            );

            let current = self.read_target(&self.file_name)?;
            if current != initial {
                if current.exact_bytes(bytes) {
                    self.ensure_active_root()?;
                    return Ok(self.unchanged_result(&current));
                }
                bail!("wiki brief target changed during refresh");
            }

            crate::process::replace_bounded_root_file(&self.root, &temporary_name, &self.file_name)
                .context("atomically replace bounded wiki brief")?;
            self.ensure_active_root()?;
            let committed = self.read_target(&self.file_name)?;
            ensure!(
                committed.exact_bytes(bytes),
                "durable wiki brief differs from rendered bytes"
            );
            self.ensure_active_root()?;
            Ok(WikiBriefWriteResult {
                project_root_identity: self.root_identity_sha256.clone(),
                brief_file_sha256: committed
                    .content_sha256()
                    .context("committed wiki brief is missing")?,
                status: WikiBriefWriteStatus::Fresh,
            })
        })();
        if result.is_err()
            || matches!(
                &result,
                Ok(WikiBriefWriteResult {
                    status: WikiBriefWriteStatus::Unchanged,
                    ..
                })
            )
        {
            let _ = crate::process::remove_bounded_root_file(&self.root, &temporary_name);
        }
        result
    }

    fn unchanged_result(&self, snapshot: &TargetSnapshot) -> WikiBriefWriteResult {
        WikiBriefWriteResult {
            project_root_identity: self.root_identity_sha256.clone(),
            brief_file_sha256: snapshot
                .content_sha256()
                .expect("unchanged target snapshot must be present"),
            status: WikiBriefWriteStatus::Unchanged,
        }
    }

    fn read_target(&self, name: &Path) -> Result<TargetSnapshot> {
        let mut file = match crate::process::open_bounded_regular_file(&self.root, name) {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(TargetSnapshot::Absent),
            Err(error) => {
                return Err(error).with_context(|| {
                    format!(
                        "wiki brief target is unsafe or unreadable: {}",
                        name.display()
                    )
                });
            }
        };
        ensure_single_link(&file)?;
        let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take((MAX_WIKI_BRIEF_RENDERED_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .context("read bounded wiki brief target")?;
        ensure!(
            bytes.len() <= MAX_WIKI_BRIEF_RENDERED_BYTES,
            "wiki brief target exceeds the read budget"
        );
        let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        ensure!(
            before == after,
            "wiki brief target changed while it was read"
        );
        let active = crate::process::open_bounded_regular_file(&self.root, name)
            .context("reopen active wiki brief target")?;
        ensure_single_link(&active)?;
        ensure!(
            FilesystemObjectSnapshot::from_metadata(&active.metadata()?) == after,
            "active wiki brief target was replaced while it was read"
        );
        Ok(TargetSnapshot::Present {
            object: after,
            bytes,
        })
    }

    fn ensure_active_root(&self) -> Result<()> {
        let path_metadata = std::fs::symlink_metadata(&self.root_path).with_context(|| {
            format!(
                "inspect active wiki brief root {}",
                self.root_path.display()
            )
        })?;
        ensure!(
            !path_metadata.file_type().is_symlink() && path_metadata.is_dir(),
            "active wiki brief root is not one safe directory"
        );
        let reopened = crate::process::open_bounded_root(&self.root_path).with_context(|| {
            format!("reopen active wiki brief root {}", self.root_path.display())
        })?;
        ensure!(
            crate::process::filesystem_file_identity(&path_metadata) == Some(self.root_identity)
                && crate::process::filesystem_file_identity(&reopened.metadata()?)
                    == Some(self.root_identity)
                && crate::process::filesystem_file_identity(&self.root.metadata()?)
                    == Some(self.root_identity),
            "active wiki brief root was replaced during refresh"
        );
        Ok(())
    }
}

fn ensure_single_name(path: &Path) -> Result<()> {
    ensure!(
        !path.is_absolute()
            && path.components().count() == 1
            && matches!(path.components().next(), Some(Component::Normal(_))),
        "wiki brief output name must be one normal path component"
    );
    Ok(())
}

fn ensure_single_link(file: &File) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(
            file.metadata()?.nlink() == 1,
            "wiki brief target must have exactly one filesystem link"
        );
    }
    #[cfg(not(unix))]
    let _ = file;
    Ok(())
}

fn ensure_secure_temporary(file: &File) -> Result<()> {
    ensure_single_link(file)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        ensure!(
            file.metadata()?.mode() & 0o077 == 0,
            "wiki brief temporary file must be owner-only"
        );
    }
    Ok(())
}

pub(crate) fn wiki_context_root_identity_sha256(identity: (u64, u64)) -> String {
    let mut hasher = Sha256::new();
    hasher.update(ROOT_IDENTITY_HASH_DOMAIN);
    hasher.update(identity.0.to_be_bytes());
    hasher.update(identity.1.to_be_bytes());
    format!("{:x}", hasher.finalize())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn bounded_writer_is_atomic_and_skips_exact_bytes() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().canonicalize()?;
        let target = root.join(".wiki-brief.md");
        let boundary = WikiBriefOutputBoundary::open(&target)?;
        let first = boundary.write(b"brief one\n")?;
        assert_eq!(first.status, WikiBriefWriteStatus::Fresh);
        let first_snapshot = FilesystemObjectSnapshot::from_metadata(&target.metadata()?);
        let second = boundary.write(b"brief one\n")?;
        assert_eq!(second.status, WikiBriefWriteStatus::Unchanged);
        assert_eq!(first.brief_file_sha256, second.brief_file_sha256);
        assert_eq!(
            first_snapshot,
            FilesystemObjectSnapshot::from_metadata(&target.metadata()?)
        );
        assert_eq!(std::fs::read(&target)?, b"brief one\n");
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn bounded_writer_rejects_symlink_and_hardlink_targets() -> Result<()> {
        use std::os::unix::fs::symlink;

        let temp = tempdir()?;
        let root = temp.path().canonicalize()?;
        let outside = root.join("outside");
        std::fs::write(&outside, b"outside")?;
        let target = root.join(".wiki-brief.md");
        symlink(&outside, &target)?;
        let boundary = WikiBriefOutputBoundary::open(&target)?;
        assert!(boundary.write(b"brief\n").is_err());
        assert_eq!(std::fs::read(&outside)?, b"outside");
        std::fs::remove_file(&target)?;
        std::fs::hard_link(&outside, &target)?;
        assert!(boundary.write(b"brief\n").is_err());
        assert_eq!(std::fs::read(&outside)?, b"outside");
        Ok(())
    }

    #[test]
    fn bounded_writer_rejects_root_replacement_and_partial_temp() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().join("project");
        std::fs::create_dir(&root)?;
        let root = root.canonicalize()?;
        let target = root.join(".wiki-brief.md");
        let boundary = WikiBriefOutputBoundary::open(&target)?;
        let displaced = temp.path().join("displaced");
        let error = boundary
            .write_with_hook(b"brief\n", |_| {
                std::fs::rename(&root, &displaced)?;
                std::fs::create_dir(&root)?;
                Ok(())
            })
            .expect_err("root replacement must fail");
        assert!(error.to_string().contains("root"));
        assert!(!root.join(".wiki-brief.md").exists());

        let partial_root = temp.path().join("partial");
        std::fs::create_dir(&partial_root)?;
        let partial_root = partial_root.canonicalize()?;
        let partial_target = partial_root.join(".wiki-brief.md");
        let partial_boundary = WikiBriefOutputBoundary::open(&partial_target)?;
        let error = partial_boundary
            .write_with_hook(b"complete\n", |temporary_name| {
                std::fs::write(partial_root.join(temporary_name), b"partial")?;
                Ok(())
            })
            .expect_err("partial temporary bytes must fail");
        assert!(error.to_string().contains("temporary bytes changed"));
        assert!(!partial_target.exists());
        Ok(())
    }

    #[test]
    fn bounded_writer_accepts_one_concurrent_exact_result() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().canonicalize()?;
        let target = root.join(".wiki-brief.md");
        let first = WikiBriefOutputBoundary::open(&target)?;
        let second = WikiBriefOutputBoundary::open(&target)?;
        let outer = first.write_with_hook(b"same\n", |_| {
            let inner = second.write(b"same\n")?;
            ensure!(inner.status == WikiBriefWriteStatus::Fresh);
            Ok(())
        })?;
        assert_eq!(outer.status, WikiBriefWriteStatus::Unchanged);
        assert_eq!(std::fs::read(target)?, b"same\n");
        Ok(())
    }

    #[test]
    fn bounded_writer_caps_existing_and_rendered_bytes() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().canonicalize()?;
        let target = root.join(".wiki-brief.md");
        std::fs::write(&target, vec![b'x'; MAX_WIKI_BRIEF_RENDERED_BYTES + 1])?;
        let boundary = WikiBriefOutputBoundary::open(&target)?;
        assert!(boundary.write(b"small\n").is_err());
        std::fs::remove_file(&target)?;
        assert!(boundary
            .write(&vec![b'x'; MAX_WIKI_BRIEF_RENDERED_BYTES + 1])
            .is_err());
        Ok(())
    }
}
