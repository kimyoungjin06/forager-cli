//! Cooperative admission and leases for frontend replacement across profiles.
use std::fs::{self, File};
use std::io::{ErrorKind, Read};
use std::path::Path;

use anyhow::{ensure, Context, Result};
use fs2::FileExt;

const GATE: &str = "maintenance.v1.json";

pub struct FrontendLease(File);

impl Drop for FrontendLease {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.0);
    }
}

struct Admission(File);
impl Drop for Admission {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.0);
    }
}

fn root(create: bool) -> Result<Option<File>> {
    let data = dirs::data_local_dir().context("local data directory unavailable")?;
    if !data.exists() {
        if !create {
            return Ok(None);
        }
        fs::create_dir_all(&data)?;
    }
    let mut path = data.canonicalize()?;
    for component in ["forager", "frontend-maintenance"] {
        path.push(component);
        if !path.exists() && !path.is_symlink() {
            if !create {
                return Ok(None);
            }
            match fs::create_dir(&path) {
                Ok(()) => {
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
                    }
                }
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error.into()),
            }
        }
        let meta = fs::symlink_metadata(&path)?;
        ensure!(
            meta.is_dir(),
            "maintenance directory must be a real directory"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            ensure!(
                meta.uid() == nix::unistd::getuid().as_raw(),
                "maintenance directory owner changed"
            );
        }
    }
    Ok(Some(crate::process::open_bounded_root(&path)?))
}

fn lock(root: &File, name: &str) -> Result<File> {
    let path = Path::new(name);
    let file = match crate::process::open_bounded_regular_file(root, path) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => {
            match crate::process::create_new_bounded_regular_file(root, path) {
                Ok(file) => file,
                Err(error) if error.kind() == ErrorKind::AlreadyExists => {
                    crate::process::open_bounded_regular_file(root, path)?
                }
                Err(error) => return Err(error.into()),
            }
        }
        Err(error) => return Err(error.into()),
    };
    ensure!(
        crate::process::filesystem_link_count(&file.metadata()?) == Some(1),
        "maintenance lock has hard links"
    );
    Ok(file)
}

fn active(root: &File) -> Result<bool> {
    let file = match crate::process::open_bounded_regular_file(root, Path::new(GATE)) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    ensure!(
        crate::process::filesystem_link_count(&file.metadata()?) == Some(1),
        "maintenance record has hard links"
    );
    let mut bytes = Vec::new();
    file.take(65537).read_to_end(&mut bytes)?;
    ensure!(
        bytes.len() <= 65536,
        "maintenance record exceeds byte budget"
    );
    let record: serde_json::Value = serde_json::from_slice(&bytes)?;
    ensure!(
        record["schema"] == "forager.frontend_maintenance.v1",
        "unknown maintenance record"
    );
    ensure!(
        record["operation_id"]
            .as_str()
            .is_some_and(|id| !id.is_empty()
                && id.len() <= 128
                && id
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))),
        "invalid maintenance operation identity"
    );
    Ok(true)
}

pub fn admit_frontend() -> Result<FrontendLease> {
    let root = root(true)?.context("maintenance directory missing")?;
    ensure!(
        !active(&root)?,
        "Forager frontend maintenance is active; reconnect after the operator finishes the cutover"
    );
    let admission = Admission(lock(&root, "admission.lock")?);
    FileExt::lock_exclusive(&admission.0)?;
    ensure!(
        !active(&root)?,
        "Forager frontend maintenance is active; reconnect after the operator finishes the cutover"
    );
    let readers = lock(&root, "readers.lock")?;
    FileExt::lock_shared(&readers)?;
    Ok(FrontendLease(readers))
}

pub fn requested() -> Result<bool> {
    let Some(root) = root(false)? else {
        return Ok(false);
    };
    // The marker is atomically replaced. Do not wait on the coordinator's
    // admission lock while still holding the frontend lease it needs to drain.
    active(&root)
}
