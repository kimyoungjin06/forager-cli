#[cfg(target_os = "linux")]
#[test]
fn metadata_policy_adds_only_boot_identity_without_changing_v1() -> anyhow::Result<()> {
    use forager::offdesk::read_policy::{apply_to_command, ReadPolicy};
    use std::{fs, process::Command};
    let temporary = tempfile::tempdir()?;
    let root = temporary.path().join("task");
    fs::create_dir(&root)?;
    let outside = temporary.path().join("outside");
    fs::write(&outside, b"private canary")?;
    let old = ReadPolicy::canonical();
    let new = ReadPolicy::metadata();
    assert_ne!(old.sha256()?, new.sha256()?);
    assert!(!old
        .runtime_readonly_paths
        .iter()
        .any(|p| p.starts_with("/proc")));
    assert_eq!(
        new.runtime_readonly_paths.last().unwrap(),
        "/proc/sys/kernel/random/boot_id"
    );
    let mut broadened = new.clone();
    broadened.runtime_readonly_paths.push("/proc".into());
    assert!(broadened.validate().is_err());
    for (policy, allowed) in [(&old, "no"), (&new, "yes")] {
        let mut command = Command::new("/usr/bin/python3");
        command.args(["-I", "-S", "-c", r#"
import errno, pathlib, subprocess, sys
boot = '/proc/sys/kernel/random/boot_id'
try:
    value = pathlib.Path(boot).read_text().strip()
except PermissionError as error:
    assert sys.argv[1] == 'no' and error.errno == errno.EACCES
else:
    assert sys.argv[1] == 'yes' and len(value) == 36
for path in [sys.argv[2], '/etc/passwd', '/proc/sys/kernel/random/uuid']:
    try:
        pathlib.Path(path).read_bytes()
    except PermissionError as error:
        assert error.errno == errno.EACCES
    else:
        raise AssertionError('unrelated host read allowed')
try:
    list(pathlib.Path('/proc/sys/kernel/random').iterdir())
except PermissionError:
    pass
else:
    raise AssertionError('parent /proc enumeration allowed')
pathlib.Path('allowed').write_text('local')
child = subprocess.run(['/usr/bin/python3','-I','-S','-c',
    'from pathlib import Path; Path("/proc/sys/kernel/random/boot_id").read_bytes()'], capture_output=True)
assert (child.returncode == 0) == (sys.argv[1] == 'yes')
print('PASS')
"#]).arg(allowed).arg(&outside);
        let evidence = apply_to_command(policy, &mut command, &root)?;
        let output = command.output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout, b"PASS\n");
        evidence.validate_against(policy, &evidence.working_directory_identity_sha256)?;
    }
    assert_eq!(ReadPolicy::canonical(), old);
    assert_eq!(fs::read(outside)?, b"private canary");
    Ok(())
}
