//! Filesystem restrictions installed only in the child immediately before exec.
use std::ffi::CString;
use std::fs::File;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::Command;

use nix::libc;

const READ: u64 = (1 << 0) | (1 << 2) | (1 << 3);
const FILE_READ: u64 = (1 << 0) | (1 << 2);
const HANDLED: u64 = (1 << 16) - 1;
const WORKDIR: u64 = HANDLED & !((1 << 6) | (1 << 11) | (1 << 15));

#[repr(C)]
struct RulesetAttr {
    handled_access_fs: u64,
}

#[repr(C, packed)]
struct PathBeneath {
    allowed_access: u64,
    parent_fd: i32,
}

pub fn abi() -> io::Result<u32> {
    let result = unsafe { libc::syscall(libc::SYS_landlock_create_ruleset, 0, 0, 1) };
    if result < 0 {
        return Err(io::Error::last_os_error());
    }
    let version = result as u32;
    if version < super::super::READ_POLICY_MIN_ABI {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "Landlock ABI 5 or newer required; no downgrade",
        ));
    }
    Ok(version)
}

fn path_fd(path: &Path) -> io::Result<File> {
    let name = CString::new(path.as_os_str().as_bytes())?;
    let fd = unsafe { libc::open(name.as_ptr(), libc::O_PATH | libc::O_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn add_rule(ruleset: &File, path: &File, rights: u64) -> io::Result<()> {
    let rule = PathBeneath {
        allowed_access: rights,
        parent_fd: path.as_raw_fd(),
    };
    let result = unsafe {
        libc::syscall(
            libc::SYS_landlock_add_rule,
            ruleset.as_raw_fd(),
            1,
            &rule,
            0,
        )
    };
    if result != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub fn restrict_command(
    command: &mut Command,
    working_dir: &Path,
    root: File,
    metadata_boot_identity: bool,
) -> io::Result<u32> {
    let version = abi()?;
    if !working_dir.is_absolute()
        || working_dir == Path::new("/")
        || ["/proc", "/sys", "/dev"]
            .iter()
            .any(|p| working_dir.starts_with(p))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "read policy requires an absolute task directory below root",
        ));
    }
    let identity = root.metadata()?;
    let expected = (identity.dev(), identity.ino());
    let attr = RulesetAttr {
        handled_access_fs: HANDLED,
    };
    let fd = unsafe {
        libc::syscall(
            libc::SYS_landlock_create_ruleset,
            &attr,
            std::mem::size_of::<RulesetAttr>(),
            0,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let ruleset = unsafe { File::from_raw_fd(fd as i32) };
    if unsafe { libc::fcntl(ruleset.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
        return Err(io::Error::last_os_error());
    }
    add_rule(&ruleset, &root, WORKDIR)?;
    for name in super::super::READ_POLICY_RUNTIME_PATHS {
        let path = Path::new(name);
        match path_fd(path) {
            Ok(file) => {
                let canonical = std::fs::canonicalize(path)?;
                if canonical.starts_with(working_dir) || working_dir.starts_with(&canonical) {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "task directory overlaps a read-only runtime path",
                    ));
                }
                let rights = if file.metadata()?.is_dir() {
                    READ
                } else {
                    FILE_READ
                };
                add_rule(&ruleset, &file, rights)?;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    if metadata_boot_identity {
        let file = path_fd(Path::new("/proc/sys/kernel/random/boot_id"))?;
        if !file.metadata()?.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "boot identity is not a file",
            ));
        }
        add_rule(&ruleset, &file, 1 << 2)?;
    }
    for name in ["/dev/null", "/dev/urandom", "/dev/random"] {
        let file = path_fd(Path::new(name))?;
        let rights = if name == "/dev/null" {
            (1 << 1) | (1 << 2)
        } else {
            1 << 2
        };
        add_rule(&ruleset, &file, rights)?;
    }
    command
        .current_dir(working_dir)
        .env_clear()
        .env("PATH", "/usr/local/bin:/usr/bin:/bin")
        .env("HOME", working_dir)
        .env("TMPDIR", working_dir)
        .env("LANG", "C.UTF-8");
    // All descriptors owned by this closure are close-on-exec. Only the caller's
    // explicit stdin/stdout/stderr remain available to the executed task.
    // Arbitrary descriptors inherited from the embedding process are outside
    // this filesystem-open policy and must be controlled by that process.
    unsafe {
        command.pre_exec(move || {
            let mut current: libc::stat = std::mem::zeroed();
            if libc::fstatat(libc::AT_FDCWD, c".".as_ptr(), &mut current, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            if (current.st_dev, current.st_ino) != expected {
                return Err(io::Error::from_raw_os_error(libc::ESTALE));
            }
            if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            if libc::syscall(libc::SYS_landlock_restrict_self, ruleset.as_raw_fd(), 0) != 0 {
                return Err(io::Error::last_os_error());
            }
            // Keep the root pinned until the restrictions have been installed.
            let _ = root.as_raw_fd();
            Ok(())
        });
    }
    Ok(version)
}
