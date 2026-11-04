//! Reuse large input digests only within one synchronous review operation.
use crate::process::{self, FilesystemObjectSnapshot};
use anyhow::{ensure, Result};
use std::{cell::RefCell, collections::HashMap, fs::File, marker::PhantomData, path::Path, rc::Rc};

type Key = ((u64, u64), String);
struct Entry {
    root: File,
    file: File,
    snapshot: FilesystemObjectSnapshot,
    digest: String,
}
impl Entry {
    fn ensure_current(&self, path: &str) -> Result<()> {
        let active = process::open_bounded_regular_file(&self.root, Path::new(path))?;
        let held = self.file.metadata()?;
        let reopened = active.metadata()?;
        ensure!(
            process::filesystem_link_count(&held) == Some(1)
                && process::filesystem_link_count(&reopened) == Some(1)
                && self.snapshot == FilesystemObjectSnapshot::from_metadata(&held)
                && self.snapshot == FilesystemObjectSnapshot::from_metadata(&reopened),
            "worker input changed since first validation: {}",
            crate::offdesk::operator_safe_text(path)
        );
        Ok(())
    }
}
thread_local! {
    static INPUTS: RefCell<Option<HashMap<Key, Entry>>> = const { RefCell::new(None) };
}

/// Nested frontend helpers share a scope; errors and unwinding discard it.
pub(crate) struct ReviewInputScope {
    owner: bool,
    _same_thread: PhantomData<Rc<()>>,
}
impl ReviewInputScope {
    pub(crate) fn begin() -> Self {
        let owner = INPUTS.with(|slot| {
            let mut slot = slot.borrow_mut();
            if slot.is_some() {
                false
            } else {
                *slot = Some(HashMap::new());
                true
            }
        });
        Self {
            owner,
            _same_thread: PhantomData,
        }
    }
    pub(crate) fn ensure_current() -> Result<()> {
        INPUTS.with(|slot| {
            if let Some(entries) = slot.borrow().as_ref() {
                for ((_, path), entry) in entries {
                    entry.ensure_current(path)?;
                }
            }
            Ok(())
        })
    }
}
impl Drop for ReviewInputScope {
    fn drop(&mut self) {
        if self.owner {
            INPUTS.with(|slot| *slot.borrow_mut() = None);
        }
    }
}

pub(crate) fn lookup(
    root: &File,
    path: &str,
    snapshot: &FilesystemObjectSnapshot,
) -> Result<Option<String>> {
    let Some(identity) = process::filesystem_file_identity(&root.metadata()?) else {
        return Ok(None);
    };
    INPUTS.with(|slot| {
        let slot = slot.borrow();
        let Some(entry) = slot
            .as_ref()
            .and_then(|entries| entries.get(&(identity, path.to_owned())))
        else {
            return Ok(None);
        };
        entry.ensure_current(path)?;
        ensure!(
            &entry.snapshot == snapshot,
            "worker input changed before reuse: {}",
            crate::offdesk::operator_safe_text(path)
        );
        Ok(Some(entry.digest.clone()))
    })
}

pub(crate) fn remember(
    root: &File,
    path: &str,
    file: &File,
    snapshot: FilesystemObjectSnapshot,
    digest: &str,
) -> Result<()> {
    if file.metadata()?.len() < 1024 * 1024 {
        return Ok(());
    }
    let Some(identity) = process::filesystem_file_identity(&root.metadata()?) else {
        return Ok(());
    };
    INPUTS.with(|slot| {
        let mut slot = slot.borrow_mut();
        if let Some(entries) = slot.as_mut() {
            // Bound descriptor use independently of the selected runtime count.
            if entries.len() < 64 {
                entries.insert(
                    (identity, path.to_owned()),
                    Entry {
                        root: root.try_clone()?,
                        file: file.try_clone()?,
                        snapshot,
                        digest: digest.to_owned(),
                    },
                );
            }
        }
        Ok(())
    })
}

#[cfg(all(test, unix))]
mod tests {
    use super::super::native_worker_invocation::read_bound;
    use super::*;
    use std::{fs, io::Write};

    fn fixture() -> (tempfile::TempDir, File) {
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join("input"), vec![b'a'; 1024 * 1024]).unwrap();
        let root = process::open_bounded_root(dir.path()).unwrap();
        (dir, root)
    }
    fn read(root: &File) -> Result<String> {
        Ok(read_bound(root, "input", 2 * 1024 * 1024, false)?.1)
    }

    #[test]
    fn mutation_with_same_size_and_restored_mtime_holds_cached_and_final_checks() {
        let (dir, root) = fixture();
        let _scope = ReviewInputScope::begin();
        let before = read(&root).unwrap();
        assert_eq!(read(&root).unwrap(), before);
        let path = dir.path().join("input");
        let modified = path.metadata().unwrap().modified().unwrap();
        let mut file = fs::OpenOptions::new().write(true).open(&path).unwrap();
        file.write_all(b"b").unwrap();
        file.set_times(fs::FileTimes::new().set_modified(modified))
            .unwrap();
        assert!(read(&root).is_err());
        assert!(ReviewInputScope::ensure_current().is_err());
    }

    #[test]
    fn replacements_aliases_and_missing_inputs_do_not_reuse_a_digest() {
        for mode in ["replace", "symlink", "hardlink", "missing"] {
            let (dir, root) = fixture();
            let _scope = ReviewInputScope::begin();
            read(&root).unwrap();
            let path = dir.path().join("input");
            if mode == "hardlink" {
                fs::hard_link(&path, dir.path().join("alias")).unwrap();
            } else {
                fs::rename(&path, dir.path().join("old")).unwrap();
                match mode {
                    "replace" => fs::write(&path, vec![b'a'; 1024 * 1024]).unwrap(),
                    "symlink" => std::os::unix::fs::symlink(dir.path().join("old"), &path).unwrap(),
                    _ => {}
                }
            }
            assert!(read(&root).is_err(), "{mode}");
            assert!(ReviewInputScope::ensure_current().is_err(), "{mode}");
        }
    }

    #[test]
    fn nested_helpers_preserve_the_outer_lease_and_new_operations_read_again() {
        let (dir, root) = fixture();
        let before;
        {
            let _outer = ReviewInputScope::begin();
            before = read(&root).unwrap();
            {
                let _inner = ReviewInputScope::begin();
                assert_eq!(read(&root).unwrap(), before);
            }
            fs::write(dir.path().join("input"), vec![b'b'; 1024 * 1024]).unwrap();
            assert!(ReviewInputScope::ensure_current().is_err());
        }
        let _next = ReviewInputScope::begin();
        assert_ne!(read(&root).unwrap(), before);
    }

    #[test]
    fn same_relative_path_cannot_cross_root_identity() {
        let (first, root1) = fixture();
        let (second, root2) = fixture();
        fs::write(second.path().join("input"), vec![b'b'; 1024 * 1024]).unwrap();
        let _scope = ReviewInputScope::begin();
        assert_ne!(read(&root1).unwrap(), read(&root2).unwrap());
        assert!(ReviewInputScope::ensure_current().is_ok());
        drop(first);
        assert!(ReviewInputScope::ensure_current().is_err());
    }

    #[test]
    fn unwind_discards_the_operation_cache() {
        let (dir, root) = fixture();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _scope = ReviewInputScope::begin();
            read(&root).unwrap();
            panic!("owned scope unwinding fixture");
        }));
        assert!(result.is_err());
        fs::write(dir.path().join("input"), vec![b'b'; 1024 * 1024]).unwrap();
        let _next = ReviewInputScope::begin();
        assert!(read(&root).is_ok());
    }
}
