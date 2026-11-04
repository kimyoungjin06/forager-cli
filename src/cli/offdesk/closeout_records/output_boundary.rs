use anyhow::{ensure, Context, Result};
use std::fs::File;
use std::io::{ErrorKind, Read, Write};
use std::path::{Component, Path, PathBuf};

use crate::process::{self, FilesystemObjectSnapshot};

const RETURN_NAME: &str = "RETURN_PACKAGE.md";
const MAX_RETURN_BYTES: usize = 1_048_576;

#[derive(PartialEq, Eq)]
pub(super) struct ObservedReturn {
    pub text: String,
    snapshot: Option<FilesystemObjectSnapshot>,
}

pub(super) struct OutputBoundary {
    path: PathBuf,
    root: File,
}

pub(super) fn absolute(path: &Path) -> Result<PathBuf> {
    let path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut normalized = PathBuf::new();
    for component in path.components() {
        ensure!(
            component != Component::ParentDir,
            "closeout paths must not traverse parents"
        );
        if component != Component::CurDir {
            normalized.push(component.as_os_str());
        }
    }
    Ok(normalized)
}

impl OutputBoundary {
    pub(super) fn open(path: &Path) -> Result<Self> {
        let path = absolute(path)?;
        let root = process::open_bounded_root(&path).context("open closeout output boundary")?;
        let result = Self { path, root };
        result.active()?;
        Ok(result)
    }

    pub(super) fn return_path(&self, declared: Option<&Path>) -> Result<PathBuf> {
        let expected = self.path.join(RETURN_NAME);
        if let Some(declared) = declared {
            ensure!(
                absolute(declared)? == expected,
                "closeout return package is outside its selected artifact slot"
            );
        }
        self.read_return()?;
        Ok(expected)
    }

    fn active(&self) -> Result<()> {
        let active = process::open_bounded_root(&self.path)?;
        let held = process::filesystem_file_identity(&self.root.metadata()?);
        ensure!(
            held.is_some() && held == process::filesystem_file_identity(&active.metadata()?),
            "closeout output directory changed"
        );
        Ok(())
    }

    pub(super) fn read_return(&self) -> Result<ObservedReturn> {
        self.active()?;
        let file = match process::open_bounded_regular_file(&self.root, Path::new(RETURN_NAME)) {
            Ok(file) => file,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                self.active()?;
                return Ok(ObservedReturn {
                    text: "# Ondesk Return Package\n\n".into(),
                    snapshot: None,
                });
            }
            Err(error) => return Err(error).context("read safe closeout return package"),
        };
        ensure!(
            process::filesystem_link_count(&file.metadata()?) == Some(1),
            "closeout return package must have one hard link"
        );
        let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        let mut bytes = Vec::new();
        let mut reader = file.take(MAX_RETURN_BYTES as u64 + 1);
        reader.read_to_end(&mut bytes)?;
        ensure!(
            bytes.len() <= MAX_RETURN_BYTES,
            "closeout return package exceeds byte budget"
        );
        let current = process::open_bounded_regular_file(&self.root, Path::new(RETURN_NAME))?;
        ensure!(
            before == FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?)
                && before == FilesystemObjectSnapshot::from_metadata(&current.metadata()?),
            "closeout return package changed during read"
        );
        self.active()?;
        Ok(ObservedReturn {
            text: String::from_utf8(bytes)?,
            snapshot: Some(before),
        })
    }

    pub(super) fn new_slot(&self, path: &Path, prefix: &str) -> Result<PathBuf> {
        let path = absolute(path)?;
        ensure!(
            path.parent() == Some(self.path.as_path()),
            "closeout record is outside its selected directory"
        );
        let name = path.file_name().context("closeout output name missing")?;
        let text = name.to_str().context("closeout output name is not UTF-8")?;
        ensure!(
            text.starts_with(prefix) && text.ends_with(".json"),
            "unexpected closeout output slot"
        );
        self.active()?;
        match process::open_bounded_regular_file(&self.root, Path::new(name)) {
            Err(error) if error.kind() == ErrorKind::NotFound => Ok(PathBuf::from(name)),
            _ => anyhow::bail!("closeout output slot already exists or is unsafe"),
        }
    }

    pub(super) fn write_new(&self, name: &Path, bytes: &[u8]) -> Result<()> {
        self.active()?;
        let mut file = process::create_new_bounded_regular_file(&self.root, name)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        self.root.sync_all()?;
        self.active()
    }

    pub(super) fn replace_return(&self, before: &ObservedReturn, bytes: &[u8]) -> Result<()> {
        ensure!(
            bytes.len() <= MAX_RETURN_BYTES,
            "closeout return package exceeds byte budget"
        );
        self.active()?;
        let temporary = PathBuf::from(format!(".closeout-return-{}.tmp", uuid::Uuid::new_v4()));
        let mut file = process::create_new_bounded_regular_file(&self.root, &temporary)?;
        let result = (|| {
            if before.snapshot.is_some() {
                let current =
                    process::open_bounded_regular_file(&self.root, Path::new(RETURN_NAME))?;
                file.set_permissions(current.metadata()?.permissions())?;
            }
            file.write_all(bytes)?;
            file.sync_all()?;
            self.active()?;
            ensure!(
                &self.read_return()? == before,
                "closeout return package changed before replacement"
            );
            let staged = process::open_bounded_regular_file(&self.root, &temporary)?;
            ensure!(
                process::filesystem_link_count(&staged.metadata()?) == Some(1)
                    && FilesystemObjectSnapshot::from_metadata(&staged.metadata()?)
                        == FilesystemObjectSnapshot::from_metadata(&file.metadata()?),
                "closeout return staging file changed"
            );
            if before.snapshot.is_some() {
                process::replace_bounded_root_file(&self.root, &temporary, Path::new(RETURN_NAME))?;
            } else {
                process::finalize_bounded_file_noreplace(
                    &self.root,
                    Path::new(""),
                    &temporary,
                    Path::new(RETURN_NAME),
                )?;
            }
            self.active()
        })();
        if result.is_err() {
            if let Ok(staged) = process::open_bounded_regular_file(&self.root, &temporary) {
                if process::filesystem_file_identity(&staged.metadata()?)
                    == process::filesystem_file_identity(&file.metadata()?)
                {
                    let _ = process::remove_bounded_root_file(&self.root, &temporary);
                }
            }
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn external_or_parent_traversing_destinations_refuse_without_writes() -> Result<()> {
        let dir = tempfile::tempdir()?;
        let selected = dir.path().join("selected");
        std::fs::create_dir(&selected)?;
        let outside = dir.path().join("outside.md");
        std::fs::write(&outside, "sentinel")?;
        let boundary = OutputBoundary::open(&selected)?;
        assert!(boundary.return_path(Some(&outside)).is_err());
        assert!(boundary
            .return_path(Some(&selected.join("../outside.md")))
            .is_err());
        assert_eq!(std::fs::read_to_string(outside)?, "sentinel");
        assert_eq!(std::fs::read_dir(selected)?.count(), 0);
        Ok(())
    }

    #[test]
    fn unsafe_or_changed_return_and_root_refuse_and_preserve_other_bytes() -> Result<()> {
        let dir = tempfile::tempdir()?;
        let root = dir.path().join("selected");
        std::fs::create_dir(&root)?;
        let path = root.join(RETURN_NAME);
        std::fs::write(&path, "before")?;
        let boundary = OutputBoundary::open(&root)?;
        let before = boundary.read_return()?;
        std::fs::write(&path, "concurrent")?;
        assert!(boundary.replace_return(&before, b"new").is_err());
        assert_eq!(std::fs::read_to_string(&path)?, "concurrent");
        let alias = dir.path().join("alias");
        std::fs::hard_link(&path, &alias)?;
        assert!(boundary.read_return().is_err());
        std::fs::remove_file(alias)?;
        std::fs::rename(&root, dir.path().join("saved"))?;
        std::fs::create_dir(&root)?;
        assert!(boundary.read_return().is_err());
        assert!(boundary
            .write_new(Path::new("closeout_review_test.json"), b"{}")
            .is_err());
        assert_eq!(std::fs::read_dir(root)?.count(), 0);
        Ok(())
    }
}
