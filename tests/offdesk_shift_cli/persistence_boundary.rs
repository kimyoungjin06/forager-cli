use super::*;
use std::io::Write;
use std::os::unix::fs::MetadataExt;
use std::process::Stdio;

struct Injection {
    _directory: tempfile::TempDir,
    library: PathBuf,
    marker: PathBuf,
}

impl Injection {
    fn new() -> Result<Self> {
        let directory = tempdir()?;
        let library = directory.path().join("after-persist.so");
        let marker = directory.path().join("mutation.json");
        let mut compiler = Command::new("cc")
            .args([
                "-x", "c", "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", "-",
            ])
            .arg("-o")
            .arg(&library)
            .arg("-ldl")
            .stdin(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        compiler
            .stdin
            .take()
            .unwrap()
            .write_all(include_bytes!("../support/shift_persistence_interceptor.c"))?;
        let output = compiler.wait_with_output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(Self {
            _directory: directory,
            library,
            marker,
        })
    }

    fn apply(&self, cmd: &mut Command, target: &Path, offset: usize) {
        cmd.env("LD_PRELOAD", &self.library)
            .env("PERSISTENCE_TEST_TARGET", target)
            .env("PERSISTENCE_TEST_OFFSET", offset.to_string())
            .env("PERSISTENCE_TEST_MARKER", &self.marker);
    }

    fn assert_fired(&self, target: &Path, before: &fs::Metadata) -> Result<()> {
        let receipt: Value = serde_json::from_slice(&fs::read(&self.marker)?)?;
        assert_eq!(
            receipt,
            json!({"rename_completed":true,"mutation_fsynced":true})
        );
        let after = fs::metadata(target)?;
        assert_eq!(
            (
                before.ino(),
                before.len(),
                before.mtime(),
                before.mtime_nsec()
            ),
            (after.ino(), after.len(), after.mtime(), after.mtime_nsec())
        );
        Ok(())
    }
}

fn shown(f: &Fixture) -> Result<Value> {
    success(
        command(f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )
}

fn assert_no_launch(f: &Fixture) -> Result<()> {
    let record = shown(f)?;
    assert_eq!(record["task_launch_reservations"], json!([]));
    assert_eq!(record["task_launch_ticket_receipts"], json!([]));
    for task in f.queue()?.as_array().unwrap() {
        assert_eq!(task["attempt_count"], 0);
        assert!(task["background_ticket_id"].is_null());
        assert!(!Path::new(task["workdir"].as_str().unwrap())
            .join("fake-native-started")
            .exists());
    }
    let probes = profile_dir(f.home.path()).join("background_runs.json");
    if probes.exists() {
        assert_eq!(
            serde_json::from_slice::<Value>(&fs::read(probes)?)?,
            json!([])
        );
    }
    Ok(())
}

fn tick_rejected(f: &Fixture) -> Result<()> {
    let output = command(f)
        .args(["offdesk", "tick", "--shift-id", "local-review", "--json"])
        .output()?;
    assert!(
        !output.status.success(),
        "unexpected tick success: {}",
        String::from_utf8_lossy(&output.stdout)
    );
    eprintln!(
        "persistence admission refusal: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_no_launch(f)
}

fn prepare_command(f: &Fixture) -> Command {
    let mut cmd = command(f);
    cmd.args([
        "offdesk",
        "shift",
        "prepare",
        &f.plan,
        "--review-id",
        &f.review,
        "--shift-id",
        "local-review",
        "--starts-at",
        &rfc3339(f.start),
        "--stops-at",
        &rfc3339(f.stop),
        "--task-id",
        "task_shift_1",
        "--json",
    ]);
    cmd
}

fn confirm_command(f: &Fixture, review: &Value, id: &str) -> Command {
    let mut cmd = command(f);
    cmd.args([
        "offdesk",
        "shift",
        "confirm-arm-review",
        "local-review",
        "--expected-review-sha256",
        review["review_sha256"].as_str().unwrap(),
        "--approval-id",
        id,
        "--by",
        "persistence-fixture",
        "--acknowledge-unknown-task-usage",
        "--json",
    ])
    .args(SCOPE);
    cmd
}

fn queue_offset(bytes: &[u8]) -> usize {
    let text = std::str::from_utf8(bytes).unwrap();
    text.find("printf shift-safe").unwrap() + "printf shift-".len()
}

#[test]
fn saved_manifest_drift_is_inert_and_requires_exact_source_recovery() -> Result<()> {
    let f = Fixture::new()?;
    let queue = fs::read(f.queue_path())?;
    let metadata = fs::metadata(f.queue_path())?;
    let injection = Injection::new()?;
    let mut cmd = prepare_command(&f);
    injection.apply(&mut cmd, &f.queue_path(), queue_offset(&queue));
    let output = cmd.output()?;
    injection.assert_fired(&f.queue_path(), &metadata)?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("has a stored manifest"));
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shift source was replaced after observation"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let record = shown(&f)?;
    assert!(record["arm_receipt"].is_null() && record["state"].is_null());
    assert_no_launch(&f)?;
    let arm = command(&f)
        .args(["offdesk", "shift", "arm", "local-review", "--json"])
        .output()?;
    // A pending arm request cannot grant executable authority to the saved manifest.
    if arm.status.success() {
        assert!(shown(&f)?["arm_receipt"].is_null());
    }
    tick_rejected(&f)?;
    fs::write(f.queue_path(), queue)?;
    let restored = success(prepare_command(&f).output()?)?;
    assert_eq!(restored["changed"], false);
    eprintln!(
        "persistence prepare recovery: existing manifest retained, changed={}",
        restored["changed"]
    );
    assert!(shown(&f)?["arm_receipt"].is_null());
    assert_no_launch(&f)
}

#[test]
fn persisted_arm_after_queue_drift_cannot_dispatch_or_silently_reconfirm() -> Result<()> {
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    let queue = fs::read(f.queue_path())?;
    let metadata = fs::metadata(f.queue_path())?;
    let injection = Injection::new()?;
    let mut cmd = confirm_command(&f, &review, &id);
    injection.apply(&mut cmd, &f.queue_path(), queue_offset(&queue));
    let output = cmd.output()?;
    injection.assert_fired(&f.queue_path(), &metadata)?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("has a stored arm receipt"));
    assert!(
        String::from_utf8_lossy(&output.stderr)
            .contains("shift source was replaced after observation"),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let record = shown(&f)?;
    assert_eq!(record["state"]["status"], "armed");
    assert_eq!(record["arm_receipt"]["approval_id"], id);
    tick_rejected(&f)?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    let retry = command(&f)
        .args([
            "offdesk",
            "shift",
            "arm",
            "local-review",
            "--approval-id",
            &id,
            "--json",
        ])
        .output()?;
    assert!(!retry.status.success());
    assert_no_launch(&f)?;
    fs::write(f.queue_path(), queue)?;
    let recovered = success(
        command(&f)
            .args([
                "offdesk",
                "shift",
                "arm",
                "local-review",
                "--approval-id",
                &id,
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(recovered["changed"], false);
    assert_eq!(recovered["outcome"], "reconciled");
    assert_eq!(shown(&f)?["arm_receipt"], record["arm_receipt"]);
    let tick = success(
        command(&f)
            .args(["offdesk", "tick", "--shift-id", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(tick["launched"], 0);
    assert_eq!(tick["pending_approval"], 1);
    eprintln!(
        "persistence arm recovery: original receipt retained; launched={}, pending_approval={}",
        tick["launched"], tick["pending_approval"]
    );
    assert_no_launch(&f)
}

#[test]
fn persisted_arm_large_worker_mutation_is_caught_without_native_entry() -> Result<()> {
    let f = prepared_codex(true)?;
    let (review, id) = pending_review(&f)?;
    let target = f.home.path().join("secondary/.runtime/codex");
    let original = fs::read(&target)?;
    assert!(original.len() > 1024 * 1024);
    let metadata = fs::metadata(&target)?;
    let injection = Injection::new()?;
    let mut cmd = confirm_command(&f, &review, &id);
    injection.apply(&mut cmd, &target, original.len() - 2);
    let output = cmd.output()?;
    injection.assert_fired(&target, &metadata)?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("has a stored arm receipt"));
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("worker input changed since first validation"));
    assert_eq!(shown(&f)?["state"]["status"], "armed");
    tick_rejected(&f)?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    fs::write(&target, original)?;
    assert_no_launch(&f)
}

#[test]
fn small_owner_mutation_after_arm_persistence_is_rechecked_before_dispatch() -> Result<()> {
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    let target = f.home.path().join("project/TODO.md");
    let original = fs::read(&target)?;
    let offset = std::str::from_utf8(&original)?
        .find("fixture task")
        .unwrap();
    let metadata = fs::metadata(&target)?;
    let injection = Injection::new()?;
    let mut cmd = confirm_command(&f, &review, &id);
    injection.apply(&mut cmd, &target, offset);
    let output = cmd.output()?;
    injection.assert_fired(&target, &metadata)?;
    assert_eq!(shown(&f)?["state"]["status"], "armed");
    eprintln!(
        "persistence owner audit: confirmation_success={}, stderr={}",
        output.status.success(),
        String::from_utf8_lossy(&output.stderr)
    );
    tick_rejected(&f)?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    fs::write(&target, original)?;
    assert_no_launch(&f)
}
