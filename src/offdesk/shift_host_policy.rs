//! Live-verified host policy used to wrap bounded shift task launches.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
#[cfg(target_os = "linux")]
use std::os::fd::AsRawFd;
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

use anyhow::{bail, ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::background::BackgroundRunnerKind;

pub const OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA: &str =
    "offdesk_shift_host_policy_attestation.v1";
pub const OFFDESK_SHIFT_HOST_POLICY_REPORT_SCHEMA: &str = "offdesk_shift_host_policy_report.v1";
pub const OFFDESK_SHIFT_HOST_POLICY_LAUNCH_EVIDENCE_SCHEMA: &str =
    "offdesk_shift_host_policy_launch_evidence.v1";

const LINUX_BWRAP_PATH: &str = "/usr/bin/bwrap";
const MAX_LAUNCHER_BYTES: u64 = 64 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftHostPolicyProviderV1 {
    LinuxBubblewrapExternalIpDeny,
    LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny,
}

impl OffdeskShiftHostPolicyProviderV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LinuxBubblewrapExternalIpDeny => "linux_bubblewrap_external_ip_deny",
            Self::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
                "linux_bubblewrap_external_ip_and_outside_workdir_write_deny"
            }
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftHostPolicyNetworkBoundaryV1 {
    IsolatedIpNetworkNamespace,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftHostPolicyFilesystemBoundaryV1 {
    Unrestricted,
    HostReadOnlyExceptWorkingDirectoryAndPrivateTmp,
}

impl OffdeskShiftHostPolicyFilesystemBoundaryV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unrestricted => "unrestricted",
            Self::HostReadOnlyExceptWorkingDirectoryAndPrivateTmp => {
                "host_read_only_except_working_directory_and_private_tmp"
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftHostFilesystemPolicyV1 {
    pub boundary: OffdeskShiftHostPolicyFilesystemBoundaryV1,
    pub host_filesystem_read_allowed: bool,
    pub host_filesystem_write_outside_working_directory_allowed: bool,
    pub private_tmp: bool,
    pub hardlink_aliases_outside_working_directory_covered: bool,
    pub nested_mounts_inside_working_directory_covered: bool,
    pub device_and_kernel_interfaces_covered: bool,
}

impl OffdeskShiftHostFilesystemPolicyV1 {
    fn restrictive() -> Self {
        Self {
            boundary: OffdeskShiftHostPolicyFilesystemBoundaryV1::HostReadOnlyExceptWorkingDirectoryAndPrivateTmp,
            host_filesystem_read_allowed: true,
            host_filesystem_write_outside_working_directory_allowed: false,
            private_tmp: true,
            hardlink_aliases_outside_working_directory_covered: false,
            nested_mounts_inside_working_directory_covered: false,
            device_and_kernel_interfaces_covered: false,
        }
    }

    fn validate_restrictive(&self) -> Result<()> {
        ensure!(
            self == &Self::restrictive(),
            "shift host filesystem policy is not canonical"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftHostPolicyAttestationV1 {
    pub schema: String,
    pub provider: OffdeskShiftHostPolicyProviderV1,
    pub observed_at: DateTime<Utc>,
    pub launcher_path: String,
    pub launcher_sha256: String,
    pub allowed_runners: Vec<BackgroundRunnerKind>,
    pub network_boundary: OffdeskShiftHostPolicyNetworkBoundaryV1,
    pub external_ip_network_allowed: bool,
    pub unix_domain_sockets_covered: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub filesystem_policy: Option<OffdeskShiftHostFilesystemPolicyV1>,
    pub forager_enforces_attested_launcher: bool,
    pub forager_controls_host_network_policy: bool,
}

impl OffdeskShiftHostPolicyAttestationV1 {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA,
            "shift host policy attestation schema must be {OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA}"
        );
        match self.provider {
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => ensure!(
                self.filesystem_policy.is_none(),
                "network-only host policy cannot claim filesystem isolation"
            ),
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => self
                .filesystem_policy
                .as_ref()
                .context("outside-workdir-write-deny policy omitted its filesystem policy")?
                .validate_restrictive()?,
        }
        ensure!(
            self.launcher_path == LINUX_BWRAP_PATH
                && self.allowed_runners == [BackgroundRunnerKind::LocalBackground]
                && self.network_boundary
                    == OffdeskShiftHostPolicyNetworkBoundaryV1::IsolatedIpNetworkNamespace
                && !self.external_ip_network_allowed
                && !self.unix_domain_sockets_covered
                && self.forager_enforces_attested_launcher
                && !self.forager_controls_host_network_policy,
            "shift host policy attestation is not one canonical supported policy"
        );
        validate_sha256(&self.launcher_sha256, "host policy launcher_sha256")
    }

    pub fn sha256(&self) -> Result<String> {
        self.validate()?;
        let bytes = serde_json::to_vec(self)?;
        Ok(format!("{:x}", Sha256::digest(bytes)))
    }

    pub fn filesystem_boundary(&self) -> OffdeskShiftHostPolicyFilesystemBoundaryV1 {
        self.filesystem_policy
            .as_ref()
            .map(|policy| policy.boundary)
            .unwrap_or(OffdeskShiftHostPolicyFilesystemBoundaryV1::Unrestricted)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftHostPolicyProbeStatusV1 {
    Verified,
    Unavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftHostPolicyReportAuthorityV1 {
    pub read_only: bool,
    pub creates_profile_or_migration_state: bool,
    pub selects_or_arms_shift: bool,
    pub authorizes_task_dispatch: bool,
    pub attestation_requires_manifest_binding: bool,
    pub live_reverification_required_at_arm_and_dispatch: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftHostPolicyReportV1 {
    pub schema: String,
    pub provider: OffdeskShiftHostPolicyProviderV1,
    pub observed_at: DateTime<Utc>,
    pub status: OffdeskShiftHostPolicyProbeStatusV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attestation: Option<OffdeskShiftHostPolicyAttestationV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unavailable_reason: Option<String>,
    pub authority: OffdeskShiftHostPolicyReportAuthorityV1,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftHostPolicyLaunchEvidenceV1 {
    pub schema: String,
    pub provider: OffdeskShiftHostPolicyProviderV1,
    pub attestation_sha256: String,
    pub launcher_sha256: String,
    pub verified_at: DateTime<Utc>,
    pub runner_kind: BackgroundRunnerKind,
    pub network_boundary: OffdeskShiftHostPolicyNetworkBoundaryV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub filesystem_policy: Option<OffdeskShiftHostFilesystemPolicyV1>,
    pub launcher_applied: bool,
}

impl OffdeskShiftHostPolicyLaunchEvidenceV1 {
    pub fn validate_against(
        &self,
        attestation: &OffdeskShiftHostPolicyAttestationV1,
    ) -> Result<()> {
        attestation.validate()?;
        ensure!(
            self.schema == OFFDESK_SHIFT_HOST_POLICY_LAUNCH_EVIDENCE_SCHEMA
                && self.provider == attestation.provider
                && self.attestation_sha256 == attestation.sha256()?
                && self.launcher_sha256 == attestation.launcher_sha256
                && self.runner_kind == BackgroundRunnerKind::LocalBackground
                && self.network_boundary == attestation.network_boundary
                && self.filesystem_policy == attestation.filesystem_policy
                && self.launcher_applied,
            "shift host policy launch evidence does not match its attestation"
        );
        Ok(())
    }
}

pub fn probe_offdesk_shift_host_policy(
    provider: OffdeskShiftHostPolicyProviderV1,
    observed_at: DateTime<Utc>,
) -> OffdeskShiftHostPolicyReportV1 {
    let (status, attestation, unavailable_reason) = match verify_provider(provider, observed_at) {
        Ok(verified) => (
            OffdeskShiftHostPolicyProbeStatusV1::Verified,
            Some(verified.attestation),
            None,
        ),
        Err(error) => (
            OffdeskShiftHostPolicyProbeStatusV1::Unavailable,
            None,
            Some(format!("{}: {error}", unavailable_reason(provider))),
        ),
    };
    OffdeskShiftHostPolicyReportV1 {
        schema: OFFDESK_SHIFT_HOST_POLICY_REPORT_SCHEMA.to_string(),
        provider,
        observed_at,
        status,
        attestation,
        unavailable_reason,
        authority: OffdeskShiftHostPolicyReportAuthorityV1 {
            read_only: true,
            creates_profile_or_migration_state: false,
            selects_or_arms_shift: false,
            authorizes_task_dispatch: false,
            attestation_requires_manifest_binding: true,
            live_reverification_required_at_arm_and_dispatch: true,
        },
    }
}

pub fn require_offdesk_shift_host_policy_attestation(
    provider: OffdeskShiftHostPolicyProviderV1,
    observed_at: DateTime<Utc>,
) -> Result<OffdeskShiftHostPolicyAttestationV1> {
    let report = probe_offdesk_shift_host_policy(provider, observed_at);
    report.attestation.with_context(|| {
        format!(
            "host policy {} is unavailable: {}",
            provider.as_str(),
            report.unavailable_reason.unwrap_or_default()
        )
    })
}

pub fn reverify_offdesk_shift_host_policy_attestation(
    attestation: &OffdeskShiftHostPolicyAttestationV1,
    observed_at: DateTime<Utc>,
) -> Result<()> {
    attestation.validate()?;
    let verified = verify_provider(attestation.provider, observed_at)?;
    ensure!(
        verified.attestation.launcher_path == attestation.launcher_path
            && verified.attestation.launcher_sha256 == attestation.launcher_sha256
            && verified.attestation.allowed_runners == attestation.allowed_runners
            && verified.attestation.network_boundary == attestation.network_boundary
            && verified.attestation.filesystem_policy == attestation.filesystem_policy,
        "live host policy no longer matches the manifest-bound attestation"
    );
    Ok(())
}

pub(crate) fn spawn_attested_local_background_command(
    attestation: &OffdeskShiftHostPolicyAttestationV1,
    command: &str,
    working_dir: &Path,
    stdout: Stdio,
    stderr: Stdio,
    verified_at: DateTime<Utc>,
) -> Result<(Child, OffdeskShiftHostPolicyLaunchEvidenceV1)> {
    attestation.validate()?;
    let verified = verify_provider(attestation.provider, verified_at)?;
    ensure!(
        verified.attestation.launcher_sha256 == attestation.launcher_sha256,
        "host policy launcher changed before task spawn"
    );
    let mut process = bubblewrap_command(
        &verified.launcher,
        attestation.provider,
        working_dir,
        command,
    )?;
    let child = process
        .stdin(Stdio::null())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .context("spawn task through the attested host policy launcher")?;
    let evidence = OffdeskShiftHostPolicyLaunchEvidenceV1 {
        schema: OFFDESK_SHIFT_HOST_POLICY_LAUNCH_EVIDENCE_SCHEMA.to_string(),
        provider: attestation.provider,
        attestation_sha256: attestation.sha256()?,
        launcher_sha256: attestation.launcher_sha256.clone(),
        verified_at,
        runner_kind: BackgroundRunnerKind::LocalBackground,
        network_boundary: attestation.network_boundary,
        filesystem_policy: attestation.filesystem_policy.clone(),
        launcher_applied: true,
    };
    evidence.validate_against(attestation)?;
    Ok((child, evidence))
}

struct VerifiedProvider {
    launcher: File,
    attestation: OffdeskShiftHostPolicyAttestationV1,
}

fn verify_provider(
    provider: OffdeskShiftHostPolicyProviderV1,
    observed_at: DateTime<Utc>,
) -> Result<VerifiedProvider> {
    match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny
        | OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            verify_linux_bubblewrap(provider, observed_at)
        }
    }
}

#[cfg(target_os = "linux")]
fn verify_linux_bubblewrap(
    provider: OffdeskShiftHostPolicyProviderV1,
    observed_at: DateTime<Utc>,
) -> Result<VerifiedProvider> {
    let (launcher, launcher_sha256) = open_and_hash_trusted_linux_launcher()?;
    let parent_namespace =
        std::fs::read_link("/proc/self/ns/net").context("observe parent network namespace")?;
    let working_dir = match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => PathBuf::from("/"),
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            let current = std::env::current_dir().context("observe host policy probe directory")?;
            ensure_restricted_working_directory(&current)?;
            ensure!(
                !current.to_string_lossy().chars().any(char::is_whitespace),
                "host policy probe directory cannot contain whitespace"
            );
            current
        }
    };
    let probe_script = match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => {
            "printf 'namespace=%s\\n' \"$(readlink /proc/self/ns/net)\"; for interface in /sys/class/net/*; do printf 'interface=%s\\n' \"${interface##*/}\"; done"
        }
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            "printf 'namespace=%s\\n' \"$(readlink /proc/self/ns/net)\"; for interface in /sys/class/net/*; do printf 'interface=%s\\n' \"${interface##*/}\"; done; awk -v target=\"$(pwd -P)\" '$5 == \"/\" { print \"root_mount_options=\" $6 } $5 == target { print \"workdir_mount_options=\" $6 } $5 == \"/tmp\" { print \"tmp_mount=\" $0 }' /proc/self/mountinfo"
        }
    };
    let output = bubblewrap_command(&launcher, provider, &working_dir, probe_script)?
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output()
        .context("run bubblewrap network namespace probe")?;
    ensure!(
        output.status.success(),
        "bubblewrap network namespace probe failed"
    );
    let stdout = String::from_utf8(output.stdout).context("decode bubblewrap probe output")?;
    let mut namespace = None;
    let mut interfaces = Vec::new();
    let mut root_mount_options = None;
    let mut workdir_mount_options = None;
    let mut tmp_mount = None;
    for line in stdout.lines() {
        if let Some(value) = line.strip_prefix("namespace=") {
            ensure!(
                namespace.replace(value.to_string()).is_none(),
                "duplicate namespace evidence"
            );
        } else if let Some(value) = line.strip_prefix("interface=") {
            interfaces.push(value.to_string());
        } else if let Some(value) = line.strip_prefix("root_mount_options=") {
            ensure!(
                root_mount_options.replace(value.to_string()).is_none(),
                "duplicate root mount evidence"
            );
        } else if let Some(value) = line.strip_prefix("workdir_mount_options=") {
            ensure!(
                workdir_mount_options.replace(value.to_string()).is_none(),
                "duplicate working directory mount evidence"
            );
        } else if let Some(value) = line.strip_prefix("tmp_mount=") {
            ensure!(
                tmp_mount.replace(value.to_string()).is_none(),
                "duplicate private tmp mount evidence"
            );
        } else if !line.trim().is_empty() {
            bail!("unexpected bubblewrap probe evidence");
        }
    }
    let namespace = namespace.context("bubblewrap probe omitted namespace evidence")?;
    ensure!(
        namespace != parent_namespace.to_string_lossy(),
        "bubblewrap did not create a distinct network namespace"
    );
    interfaces.sort();
    interfaces.dedup();
    ensure!(
        interfaces == ["lo"],
        "bubblewrap namespace exposed a non-loopback interface"
    );
    let filesystem_policy = match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => None,
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            let root_mount_options =
                root_mount_options.context("bubblewrap probe omitted root mount evidence")?;
            let workdir_mount_options = workdir_mount_options
                .context("bubblewrap probe omitted working directory mount evidence")?;
            let tmp_mount = tmp_mount.context("bubblewrap probe omitted private tmp evidence")?;
            ensure!(
                has_mount_option(&root_mount_options, "ro"),
                "bubblewrap host root is not read-only"
            );
            ensure!(
                has_mount_option(&workdir_mount_options, "rw"),
                "bubblewrap working directory is not writable"
            );
            ensure!(
                tmp_mount.contains(" - tmpfs tmpfs "),
                "bubblewrap /tmp is not a private tmpfs"
            );
            Some(OffdeskShiftHostFilesystemPolicyV1::restrictive())
        }
    };
    Ok(VerifiedProvider {
        launcher,
        attestation: OffdeskShiftHostPolicyAttestationV1 {
            schema: OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA.to_string(),
            provider,
            observed_at,
            launcher_path: LINUX_BWRAP_PATH.to_string(),
            launcher_sha256,
            allowed_runners: vec![BackgroundRunnerKind::LocalBackground],
            network_boundary: OffdeskShiftHostPolicyNetworkBoundaryV1::IsolatedIpNetworkNamespace,
            external_ip_network_allowed: false,
            unix_domain_sockets_covered: false,
            filesystem_policy,
            forager_enforces_attested_launcher: true,
            forager_controls_host_network_policy: false,
        },
    })
}

#[cfg(not(target_os = "linux"))]
fn verify_linux_bubblewrap(
    _provider: OffdeskShiftHostPolicyProviderV1,
    _observed_at: DateTime<Utc>,
) -> Result<VerifiedProvider> {
    bail!("linux bubblewrap host policy is unsupported on this operating system")
}

#[cfg(target_os = "linux")]
fn open_and_hash_trusted_linux_launcher() -> Result<(File, String)> {
    let root = crate::process::open_bounded_root(Path::new("/usr/bin"))
        .context("open trusted launcher directory")?;
    let mut launcher = crate::process::open_bounded_regular_file(&root, Path::new("bwrap"))
        .context("open trusted bubblewrap launcher")?;
    let before = launcher.metadata()?;
    ensure_trusted_root_executable(&before)?;
    ensure!(
        before.len() <= MAX_LAUNCHER_BYTES,
        "host policy launcher exceeds its byte budget"
    );
    let mut hasher = Sha256::new();
    let bytes_read = std::io::copy(
        &mut Read::by_ref(&mut launcher).take(MAX_LAUNCHER_BYTES + 1),
        &mut hasher,
    )?;
    ensure!(
        bytes_read <= MAX_LAUNCHER_BYTES,
        "host policy launcher exceeds its byte budget"
    );
    let after = launcher.metadata()?;
    ensure!(
        crate::process::FilesystemObjectSnapshot::from_metadata(&before)
            == crate::process::FilesystemObjectSnapshot::from_metadata(&after),
        "host policy launcher changed while hashing"
    );
    let active = crate::process::open_bounded_regular_file(&root, Path::new("bwrap"))
        .context("reopen trusted bubblewrap launcher")?;
    ensure!(
        crate::process::FilesystemObjectSnapshot::from_metadata(&active.metadata()?)
            == crate::process::FilesystemObjectSnapshot::from_metadata(&after),
        "host policy launcher path changed while hashing"
    );
    launcher.seek(SeekFrom::Start(0))?;
    Ok((launcher, format!("{:x}", hasher.finalize())))
}

#[cfg(target_os = "linux")]
fn ensure_trusted_root_executable(metadata: &std::fs::Metadata) -> Result<()> {
    ensure!(
        metadata.is_file(),
        "host policy launcher must be a regular file"
    );
    ensure!(
        metadata.uid() == 0,
        "host policy launcher must be owned by root"
    );
    ensure!(
        metadata.nlink() == 1,
        "host policy launcher must have one link"
    );
    ensure!(
        metadata.mode() & 0o022 == 0,
        "host policy launcher must not be group or world writable"
    );
    ensure!(
        metadata.mode() & 0o111 != 0,
        "host policy launcher must be executable"
    );
    Ok(())
}

#[cfg(target_os = "linux")]
fn bubblewrap_command(
    launcher: &File,
    provider: OffdeskShiftHostPolicyProviderV1,
    working_dir: &Path,
    command: &str,
) -> Result<Command> {
    ensure!(
        working_dir.is_absolute(),
        "attested task working directory must be absolute"
    );
    let root = crate::process::open_bounded_root(working_dir)
        .context("open exact attested task working directory")?;
    let (device, inode) = crate::process::filesystem_file_identity(&root.metadata()?)
        .context("attested task working directory has no stable identity")?;
    let executable = PathBuf::from(format!("/proc/self/fd/{}", launcher.as_raw_fd()));
    let mut process = Command::new(executable);
    process.args(["--die-with-parent", "--new-session", "--unshare-net"]);
    match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => {
            process.args(["--bind", "/", "/"]);
        }
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            ensure_restricted_working_directory(working_dir)?;
            process.args(["--ro-bind", "/", "/", "--tmpfs", "/tmp", "--bind"]);
            process.arg(working_dir);
            process.arg(working_dir);
        }
    }
    process.args(["--proc", "/proc", "--dev", "/dev", "--chdir"]);
    process.arg(working_dir);
    process.args([
        "/bin/sh",
        "-c",
        "actual=$(/usr/bin/stat -Lc '%d:%i' -- .) || exit 125; [ \"$actual\" = \"$1\" ] || exit 125; exec /bin/sh -c \"$2\"",
        "forager-host-policy",
        &format!("{device}:{inode}"),
        command,
    ]);
    Ok(process)
}

#[cfg(not(target_os = "linux"))]
fn bubblewrap_command(
    _launcher: &File,
    _provider: OffdeskShiftHostPolicyProviderV1,
    _working_dir: &Path,
    _command: &str,
) -> Result<Command> {
    bail!("linux bubblewrap host policy is unsupported on this operating system")
}

fn unavailable_reason(provider: OffdeskShiftHostPolicyProviderV1) -> &'static str {
    match provider {
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny => {
            "the fixed root-owned bubblewrap launcher or a live isolated IP namespace probe is unavailable"
        }
        OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny => {
            "the fixed root-owned bubblewrap launcher or live network and read-only host mount probes are unavailable"
        }
    }
}

#[cfg(target_os = "linux")]
fn ensure_restricted_working_directory(working_dir: &Path) -> Result<()> {
    ensure!(
        working_dir != Path::new("/")
            && !working_dir.starts_with("/tmp")
            && !working_dir.starts_with("/proc")
            && !working_dir.starts_with("/dev"),
        "outside-workdir-write-deny policy requires a working directory outside /, /tmp, /proc, and /dev"
    );
    Ok(())
}

#[cfg(target_os = "linux")]
fn has_mount_option(options: &str, expected: &str) -> bool {
    options.split(',').any(|option| option == expected)
}

fn validate_sha256(value: &str, label: &str) -> Result<()> {
    ensure!(
        value.len() == 64
            && value
                .chars()
                .all(|character| character.is_ascii_digit() || ('a'..='f').contains(&character)),
        "{label} must be one lowercase SHA-256 digest"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn canonical_attestation() -> OffdeskShiftHostPolicyAttestationV1 {
        OffdeskShiftHostPolicyAttestationV1 {
            schema: OFFDESK_SHIFT_HOST_POLICY_ATTESTATION_SCHEMA.to_string(),
            provider: OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny,
            observed_at: Utc::now(),
            launcher_path: LINUX_BWRAP_PATH.to_string(),
            launcher_sha256: "a".repeat(64),
            allowed_runners: vec![BackgroundRunnerKind::LocalBackground],
            network_boundary: OffdeskShiftHostPolicyNetworkBoundaryV1::IsolatedIpNetworkNamespace,
            external_ip_network_allowed: false,
            unix_domain_sockets_covered: false,
            filesystem_policy: None,
            forager_enforces_attested_launcher: true,
            forager_controls_host_network_policy: false,
        }
    }

    #[test]
    fn canonical_attestation_is_strict_and_hashable() -> Result<()> {
        let attestation = canonical_attestation();
        attestation.validate()?;
        assert_eq!(attestation.sha256()?.len(), 64);

        let mut forged = attestation;
        forged.unix_domain_sockets_covered = true;
        assert!(forged.validate().is_err());

        let mut forged = canonical_attestation();
        forged.filesystem_policy = Some(OffdeskShiftHostFilesystemPolicyV1::restrictive());
        assert!(forged.validate().is_err());
        Ok(())
    }

    #[test]
    fn legacy_network_only_attestation_keeps_its_original_serialized_shape() -> Result<()> {
        let value = serde_json::to_value(canonical_attestation())?;
        assert!(value.get("filesystem_policy").is_none());
        let attestation: OffdeskShiftHostPolicyAttestationV1 = serde_json::from_value(value)?;
        attestation.validate()?;
        assert_eq!(
            attestation.filesystem_boundary(),
            OffdeskShiftHostPolicyFilesystemBoundaryV1::Unrestricted
        );
        assert!(attestation.filesystem_policy.is_none());
        Ok(())
    }

    #[test]
    fn legacy_network_only_launch_evidence_keeps_its_original_serialized_shape() -> Result<()> {
        let attestation = canonical_attestation();
        let evidence = OffdeskShiftHostPolicyLaunchEvidenceV1 {
            schema: OFFDESK_SHIFT_HOST_POLICY_LAUNCH_EVIDENCE_SCHEMA.to_string(),
            provider: attestation.provider,
            attestation_sha256: attestation.sha256()?,
            launcher_sha256: attestation.launcher_sha256.clone(),
            verified_at: Utc::now(),
            runner_kind: BackgroundRunnerKind::LocalBackground,
            network_boundary: attestation.network_boundary,
            filesystem_policy: None,
            launcher_applied: true,
        };
        let value = serde_json::to_value(&evidence)?;
        assert!(value.get("filesystem_policy").is_none());
        let evidence: OffdeskShiftHostPolicyLaunchEvidenceV1 = serde_json::from_value(value)?;
        evidence.validate_against(&attestation)
    }

    #[test]
    fn restrictive_attestation_cannot_overclaim_filesystem_or_socket_coverage() -> Result<()> {
        let mut attestation = canonical_attestation();
        attestation.provider =
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny;
        attestation.filesystem_policy = Some(OffdeskShiftHostFilesystemPolicyV1::restrictive());
        attestation.validate()?;

        attestation
            .filesystem_policy
            .as_mut()
            .context("filesystem policy")?
            .hardlink_aliases_outside_working_directory_covered = true;
        assert!(attestation.validate().is_err());
        let policy = attestation
            .filesystem_policy
            .as_mut()
            .context("filesystem policy")?;
        policy.hardlink_aliases_outside_working_directory_covered = false;
        policy.nested_mounts_inside_working_directory_covered = true;
        assert!(attestation.validate().is_err());
        attestation
            .filesystem_policy
            .as_mut()
            .context("filesystem policy")?
            .nested_mounts_inside_working_directory_covered = false;
        attestation.unix_domain_sockets_covered = true;
        assert!(attestation.validate().is_err());
        Ok(())
    }

    #[test]
    fn host_policy_probe_never_grants_shift_authority() {
        for provider in [
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny,
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny,
        ] {
            let report = probe_offdesk_shift_host_policy(provider, Utc::now());
            assert!(report.authority.read_only);
            assert!(!report.authority.creates_profile_or_migration_state);
            assert!(!report.authority.selects_or_arms_shift);
            assert!(!report.authority.authorizes_task_dispatch);
            assert_eq!(
                report.attestation.is_some(),
                report.status == OffdeskShiftHostPolicyProbeStatusV1::Verified
            );
        }
    }

    #[test]
    fn verified_provider_wraps_the_actual_child_when_available() -> Result<()> {
        let observed_at = Utc::now();
        let report = probe_offdesk_shift_host_policy(
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpDeny,
            observed_at,
        );
        let Some(attestation) = report.attestation else {
            return Ok(());
        };
        let workdir = tempfile::tempdir()?;
        let (child, evidence) = spawn_attested_local_background_command(
            &attestation,
            "printf attested-child",
            workdir.path(),
            Stdio::piped(),
            Stdio::piped(),
            Utc::now(),
        )?;
        let output = child.wait_with_output()?;
        assert!(output.status.success());
        assert_eq!(output.stdout, b"attested-child");
        evidence.validate_against(&attestation)?;
        Ok(())
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn restrictive_provider_writes_only_to_workdir_and_private_tmp_when_available() -> Result<()> {
        let report = probe_offdesk_shift_host_policy(
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny,
            Utc::now(),
        );
        let Some(attestation) = report.attestation else {
            return Ok(());
        };
        let current = std::env::current_dir()?;
        let root = tempfile::Builder::new()
            .prefix(".host-policy-test-")
            .tempdir_in(current)?;
        let workdir = root.path().join("workdir");
        std::fs::create_dir(&workdir)?;
        let outside = root.path().join("outside.txt");
        std::fs::write(&outside, b"unchanged")?;
        let private_tmp_name = format!("forager-host-policy-{}.txt", uuid::Uuid::new_v4());
        let host_tmp = Path::new("/tmp").join(&private_tmp_name);
        ensure!(!host_tmp.exists(), "private tmp test path already exists");
        let command = format!(
            "printf inside > inside.txt; if printf changed > ../outside.txt 2>/dev/null; then exit 71; fi; printf private > /tmp/{private_tmp_name}; printf restrictive-child"
        );
        let (child, evidence) = spawn_attested_local_background_command(
            &attestation,
            &command,
            &workdir,
            Stdio::piped(),
            Stdio::piped(),
            Utc::now(),
        )?;
        let output = child.wait_with_output()?;
        assert!(
            output.status.success(),
            "restrictive child failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout, b"restrictive-child");
        assert_eq!(std::fs::read(workdir.join("inside.txt"))?, b"inside");
        assert_eq!(std::fs::read(outside)?, b"unchanged");
        assert!(!host_tmp.exists());
        let policy = evidence
            .filesystem_policy
            .as_ref()
            .context("launch filesystem policy")?;
        assert_eq!(
            policy.boundary,
            OffdeskShiftHostPolicyFilesystemBoundaryV1::HostReadOnlyExceptWorkingDirectoryAndPrivateTmp
        );
        assert!(!policy.host_filesystem_write_outside_working_directory_allowed);
        assert!(policy.private_tmp);
        evidence.validate_against(&attestation)?;
        Ok(())
    }

    #[test]
    #[cfg(target_os = "linux")]
    fn restrictive_provider_rejects_workdirs_masked_by_private_tmp() -> Result<()> {
        let launcher = File::open(LINUX_BWRAP_PATH)?;
        let workdir = tempfile::tempdir()?;
        let error = bubblewrap_command(
            &launcher,
            OffdeskShiftHostPolicyProviderV1::LinuxBubblewrapExternalIpAndOutsideWorkdirWriteDeny,
            workdir.path(),
            "true",
        )
        .expect_err("workdir beneath /tmp must be rejected");
        assert!(error
            .to_string()
            .contains("outside /, /tmp, /proc, and /dev"));
        Ok(())
    }
}
