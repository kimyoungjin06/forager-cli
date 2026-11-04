use super::proposal::Fixture;
use super::*;

const ORDER: [&str; 2] = ["task_other_2", "task_shift_1"];

fn command(f: &Fixture) -> Command {
    let mut command = forager_command(f.home.path());
    command.env("FORAGER_PROJECT_REGISTRY", &f.registry);
    command
}

fn prepare(f: &Fixture, hash: &str, order: &[&str]) -> Result<std::process::Output> {
    let mut cmd = command(f);
    cmd.args([
        "offdesk",
        "shift",
        "prepare",
        &f.plan,
        "--review-id",
        &f.review,
        "--shift-id",
        "serial-fixture",
        "--starts-at",
        &rfc3339(f.start),
        "--stops-at",
        &rfc3339(f.stop),
        "--serial-proposal-sha256",
        hash,
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--json",
    ]);
    for id in order {
        cmd.args(["--task-id", id]);
    }
    Ok(cmd.output()?)
}

fn success(output: std::process::Output) -> Result<Value> {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

pub(super) fn arm(f: &Fixture) -> Result<String> {
    let pending = success(
        command(f)
            .args(["offdesk", "shift", "arm", "serial-fixture", "--json"])
            .output()?,
    )?;
    let id = pending[0]["approval_id"].as_str().unwrap().to_string();
    let text = serde_json::to_string(&pending)?;
    assert!(text.contains("Serial order: task_other_2 -> task_shift_1"));
    success(command(f).args(["offdesk", "ok", &id, "--json"]).output()?)?;
    for _ in 0..2 {
        success(
            command(f)
                .args([
                    "offdesk",
                    "shift",
                    "arm",
                    "serial-fixture",
                    "--approval-id",
                    &id,
                    "--json",
                ])
                .output()?,
        )?;
    }
    Ok(id)
}

pub(super) fn tick(f: &Fixture, id: &str) -> Result<Value> {
    success(
        command(f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "serial-fixture",
                "--task-id",
                id,
                "--json",
            ])
            .output()?,
    )
}

pub(super) fn approve_dispatch(f: &Fixture, task: &str) -> Result<()> {
    let pending = success(command(f).args(["offdesk", "pending", "--json"]).output()?)?;
    let pending: Vec<_> = pending
        .as_array()
        .unwrap()
        .iter()
        .filter(|item| item["action"] == "dispatch.runtime")
        .collect();
    assert_eq!(pending.len(), 1, "{pending:#?}");
    assert_eq!(pending[0]["task_id"], task);
    success(
        command(f)
            .args([
                "offdesk",
                "ok",
                pending[0]["approval_id"].as_str().unwrap(),
                "--json",
            ])
            .output()?,
    )?;
    Ok(())
}

pub(super) fn wait_for_completed(f: &Fixture, task: &str) -> Result<()> {
    for _ in 0..60 {
        success(command(f).args(["offdesk", "poll", "--json"]).output()?)?;
        let queue = f.queue()?;
        if queue
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["task_id"] == task && entry["status"] == "completed")
        {
            return Ok(());
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    anyhow::bail!(
        "fixture task did not complete: {task}; queue={:#}; probes={}",
        f.queue()?,
        fs::read_to_string(profile_dir(f.home.path()).join("background_runs.json"))?
    )
}

#[test]
fn serial_prepare_is_version_bound_and_repeatable() -> Result<()> {
    let f = Fixture::new()?;
    let proposal = f.report(&ORDER, &[])?;
    let hash = proposal["proposal_sha256"].as_str().unwrap();
    let wrong = prepare(&f, hash, &[ORDER[1], ORDER[0]])?;
    assert!(!wrong.status.success());
    assert!(!profile_dir(f.home.path())
        .join("offdesk_shifts.v1.json")
        .exists());
    let first = success(prepare(&f, hash, &ORDER)?)?;
    let repeated = success(prepare(&f, hash, &ORDER)?)?;
    assert_eq!(first["record"]["manifest"], repeated["record"]["manifest"]);
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "serial-fixture", "--json"])
            .output()?,
    )?;
    assert!(serde_json::to_string(&shown)?.contains(hash));
    let before = fs::read(profile_dir(f.home.path()).join("offdesk_shifts.v1.json"))?;
    let mut queue = f.queue()?;
    queue[0]["command"] = json!("printf changed-before-prepare");
    f.save(&queue)?;
    assert!(!prepare(&f, hash, &ORDER)?.status.success());
    assert_eq!(
        fs::read(profile_dir(f.home.path()).join("offdesk_shifts.v1.json"))?,
        before
    );
    Ok(())
}

#[test]
fn serial_confirmation_refuses_changed_queue_before_new_approval() -> Result<()> {
    let f = Fixture::new()?;
    let proposal = f.report(&ORDER, &[])?;
    success(prepare(
        &f,
        proposal["proposal_sha256"].as_str().unwrap(),
        &ORDER,
    )?)?;
    let mut queue = f.queue()?;
    queue[0]["preview"] = json!("changed after preview");
    f.save(&queue)?;
    let rejected = command(&f)
        .args(["offdesk", "shift", "arm", "serial-fixture", "--json"])
        .output()?;
    assert!(!rejected.status.success());
    assert!(String::from_utf8_lossy(&rejected.stderr).contains("serial queue changed"));
    assert_eq!(
        success(
            command(&f)
                .args(["offdesk", "pending", "--json"])
                .output()?
        )?,
        json!([])
    );
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn serial_dispatch_blocks_skips_and_waits_for_exact_release_without_accepting_todos() -> Result<()>
{
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        task["command"] = json!("printf fixture-result > result.json");
        task["result_artifact_path"] =
            json!(Path::new(task["workdir"].as_str().unwrap()).join("result.json"));
    }
    f.save(&queue)?;
    let before = [
        fs::read(f.home.path().join("project/TODO.md"))?,
        fs::read(f.home.path().join("secondary/NEXT_ACTIONS.md"))?,
    ];
    let proposal = f.report(&ORDER, &[])?;
    success(prepare(
        &f,
        proposal["proposal_sha256"].as_str().unwrap(),
        &ORDER,
    )?)?;
    arm(&f)?;
    let held = tick(&f, ORDER[1])?;
    assert!(serde_json::to_string(&held)?.contains("serial_predecessor_not_completed"));
    assert_eq!(
        success(
            command(&f)
                .args(["offdesk", "pending", "--json"])
                .output()?
        )?,
        json!([])
    );
    tick(&f, ORDER[0])?;
    tick(&f, ORDER[1])?;
    approve_dispatch(&f, ORDER[0])?;
    assert_eq!(tick(&f, ORDER[0])?["launched"], 1);
    wait_for_completed(&f, ORDER[0])?;
    // A completion status without a supported descendant-wait receipt must not
    // unlock the next task, even if its workdir is disjoint.
    let path = profile_dir(f.home.path()).join("background_runs.json");
    let original = fs::read(&path)?;
    let mut probes: Value = serde_json::from_slice(&original)?;
    for probe in probes.as_array_mut().unwrap() {
        probe["local_exit_receipt"] = Value::Null;
    }
    fs::write(&path, serde_json::to_vec(&probes)?)?;
    let held = tick(&f, ORDER[1])?;
    assert!(serde_json::to_string(&held)?.contains("serial_predecessor_release_unverified"));
    fs::write(&path, original)?;
    let mut pending = false;
    for _ in 0..60 {
        tick(&f, ORDER[1])?;
        let result = success(
            command(&f)
                .args(["offdesk", "pending", "--json"])
                .output()?,
        )?;
        if result
            .as_array()
            .unwrap()
            .iter()
            .any(|entry| entry["task_id"] == ORDER[1])
        {
            pending = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(
        pending,
        "verified predecessor release did not unlock the next task"
    );
    approve_dispatch(&f, ORDER[1])?;
    assert_eq!(tick(&f, ORDER[1])?["launched"], 1);
    wait_for_completed(&f, ORDER[1])?;
    success(command(&f).args(["offdesk", "poll", "--json"]).output()?)?;
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    let reservations = ledger["task_launch_reservations"].as_array().unwrap();
    assert_eq!(reservations.len(), 2);
    assert_eq!(reservations[0]["task_id"], ORDER[0]);
    assert_eq!(reservations[1]["task_id"], ORDER[1]);
    assert_eq!(ledger["task_outcome_receipts"].as_array().unwrap().len(), 2);
    assert_eq!(fs::read(f.home.path().join("project/TODO.md"))?, before[0]);
    assert_eq!(
        fs::read(f.home.path().join("secondary/NEXT_ACTIONS.md"))?,
        before[1]
    );
    Ok(())
}

#[test]
fn serial_dispatch_holds_cancelled_predecessor_and_changed_sources() -> Result<()> {
    let f = Fixture::new()?;
    let proposal = f.report(&ORDER, &[])?;
    success(prepare(
        &f,
        proposal["proposal_sha256"].as_str().unwrap(),
        &ORDER,
    )?)?;
    arm(&f)?;
    let mut queue = f.queue()?;
    queue[1]["status"] = json!("completed");
    f.save(&queue)?;
    let held = tick(&f, ORDER[1])?;
    assert!(serde_json::to_string(&held)?.contains("serial_predecessor_launch_unverified"));
    queue[1]["status"] = json!("cancelled");
    f.save(&queue)?;
    let held = tick(&f, ORDER[1])?;
    assert!(serde_json::to_string(&held)?.contains("serial_predecessor_not_completed"));
    let path = f.home.path().join("project/TODO.md");
    fs::write(
        &path,
        format!("{}\nfixture drift", fs::read_to_string(&path)?),
    )?;
    assert!(!command(&f)
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "serial-fixture",
            "--task-id",
            ORDER[1],
            "--json"
        ])
        .output()?
        .status
        .success());
    assert_eq!(
        success(
            command(&f)
                .args(["offdesk", "pending", "--json"])
                .output()?
        )?,
        json!([])
    );
    Ok(())
}

#[test]
fn serial_confirmation_rechecks_after_approval_and_refuses_registry_drift() -> Result<()> {
    let f = Fixture::new()?;
    let proposal = f.report(&ORDER, &[])?;
    success(prepare(
        &f,
        proposal["proposal_sha256"].as_str().unwrap(),
        &ORDER,
    )?)?;
    let pending = success(
        command(&f)
            .args(["offdesk", "shift", "arm", "serial-fixture", "--json"])
            .output()?,
    )?;
    let id = pending[0]["approval_id"].as_str().unwrap();
    success(command(&f).args(["offdesk", "ok", id, "--json"]).output()?)?;
    fs::write(
        &f.registry,
        format!("{}\n# registry drift", fs::read_to_string(&f.registry)?),
    )?;
    assert!(!command(&f)
        .args([
            "offdesk",
            "shift",
            "arm",
            "serial-fixture",
            "--approval-id",
            id,
            "--json"
        ])
        .output()?
        .status
        .success());
    let ledger: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("offdesk_shifts.v1.json"),
    )?)?;
    assert!(ledger["arm_receipts"].as_array().unwrap().is_empty());
    assert!(ledger["task_launch_reservations"]
        .as_array()
        .unwrap()
        .is_empty());
    Ok(())
}
