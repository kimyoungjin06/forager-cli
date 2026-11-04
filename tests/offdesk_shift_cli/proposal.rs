use super::*;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

pub(super) struct Fixture {
    pub(super) home: tempfile::TempDir,
    pub(super) plan: String,
    pub(super) review: String,
    pub(super) registry: PathBuf,
    pub(super) start: chrono::DateTime<Utc>,
    pub(super) stop: chrono::DateTime<Utc>,
}

fn snapshot(root: &Path) -> Result<BTreeMap<PathBuf, Vec<u8>>> {
    let mut result = BTreeMap::new();
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if path.is_dir() {
            result.extend(snapshot(&path)?);
        } else {
            result.insert(path.clone(), fs::read(path)?);
        }
    }
    Ok(result)
}

fn write_owner(path: &Path, project: &str, tasks: Value) -> Result<Value> {
    let source = format!(
        "# Fixture owning TODO\n\n```forager-plan\n{}\n```\n",
        serde_json::to_string_pretty(
            &json!({"schema":"ondesk_task_plan.v1", "project_key":project, "tasks":tasks})
        )?
    );
    fs::write(path, &source)?;
    Ok(
        json!({"artifact_id":format!("ondesk-plan-sha256:{:x}",Sha256::digest(source.as_bytes())),"path":path,"present":true}),
    )
}

fn source_task(id: &str, deps: &[&str], state: &str) -> Value {
    let evidence = if state == "accepted" {
        vec!["fixture:accepted-predecessor-result"]
    } else {
        Vec::new()
    };
    json!({"task_id":id,"goal":format!("fixture task {id}"),"completion_criteria":["persist a checked result"],
        "depends_on":deps,"review_state":state,"evidence_refs":evidence})
}

impl Fixture {
    pub(super) fn new() -> Result<Self> {
        let home = tempdir()?;
        let first = home.path().join("project");
        let second = home.path().join("secondary");
        fs::create_dir(&second)?;
        let start = Utc::now() - Duration::minutes(1);
        let stop = Utc::now() + Duration::hours(1);
        register_review_and_enqueue(home.path(), &first, start, stop)?;
        let multi_path = home.path().join("MULTI_PLAN.json");
        let mut input: Value =
            serde_json::from_slice(&fs::read(home.path().join("OVERNIGHT_PLAN.json"))?)?;
        input["objective"] = json!("Two explicitly selected fixture projects, serial work");
        fs::write(&multi_path, serde_json::to_vec(&input)?)?;
        let output = forager_command(home.path())
            .args(["offdesk", "plan", multi_path.to_str().unwrap(), "--json"])
            .output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let registration: Value = serde_json::from_slice(&output.stdout)?;
        let plan = Path::new(registration["artifacts"]["registry_dir"].as_str().unwrap())
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .to_string();
        let output = forager_command(home.path())
            .args([
                "offdesk",
                "plan-review",
                &plan,
                "--decision",
                "approved",
                "--reason",
                "Fixture multi-project review",
                "--json",
            ])
            .output()?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let review = serde_json::from_slice::<Value>(&output.stdout)?["review_id"]
            .as_str()
            .unwrap()
            .to_string();
        let queue_path = profile_dir(home.path()).join("offdesk_tasks.json");
        let mut queue: Value = serde_json::from_slice(&fs::read(&queue_path)?)?;
        let mut other = queue[0].clone();
        other["task_id"] = json!("task_other_2");
        other["project_key"] = json!("other");
        other["request_id"] = json!("request_other");
        other["workdir"] = json!(second);
        other["execution_brief"]["task_id"] = json!("task_other_2");
        other["execution_brief"]["project_key"] = json!("other");
        other["execution_brief"]["request_id"] = json!("request_other");
        queue[0]["artifact_refs"] = json!([write_owner(
            &first.join("TODO.md"),
            "project",
            json!([source_task("task_shift_1", &[], "unreviewed")])
        )?]);
        other["artifact_refs"] = json!([write_owner(
            &second.join("NEXT_ACTIONS.md"),
            "other",
            json!([source_task("task_other_2", &[], "unreviewed")])
        )?]);
        queue.as_array_mut().unwrap().push(other);
        fs::write(queue_path, serde_json::to_vec(&queue)?)?;
        let registry = home.path().join("projects.toml");
        fs::write(&registry,"schema = \"forager_project_registry.v1\"\n[projects.project]\nworkspace_patterns = [\"project\"]\n[projects.other]\nworkspace_patterns = [\"secondary\"]\n")?;
        Ok(Self {
            home,
            plan,
            review,
            registry,
            start,
            stop,
        })
    }

    pub(super) fn queue_path(&self) -> PathBuf {
        profile_dir(self.home.path()).join("offdesk_tasks.json")
    }
    pub(super) fn queue(&self) -> Result<Value> {
        Ok(serde_json::from_slice(&fs::read(self.queue_path())?)?)
    }
    pub(super) fn save(&self, queue: &Value) -> Result<()> {
        Ok(fs::write(self.queue_path(), serde_json::to_vec(queue)?)?)
    }

    pub(super) fn run(&self, tasks: &[&str], extra: &[&str]) -> Result<std::process::Output> {
        let before = snapshot(self.home.path())?;
        let mut cmd = forager_command(self.home.path());
        cmd.env("FORAGER_PROJECT_REGISTRY", &self.registry).args([
            "offdesk",
            "shift",
            "propose",
            &self.plan,
            "--review-id",
            &self.review,
            "--project-key",
            "project",
            "--project-key",
            "other",
            "--starts-at",
            &rfc3339(self.start),
            "--stops-at",
            &rfc3339(self.stop),
            "--json",
        ]);
        for id in tasks {
            cmd.args(["--task-id", id]);
        }
        let output = cmd.args(extra).output()?;
        assert_eq!(
            snapshot(self.home.path())?,
            before,
            "proposal wrote fixture state"
        );
        Ok(output)
    }
    pub(super) fn report(&self, tasks: &[&str], extra: &[&str]) -> Result<Value> {
        let output = self.run(tasks, extra)?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        Ok(serde_json::from_slice(&output.stdout)?)
    }
}

#[test]
fn proposal_binds_two_owners_and_explicit_order_without_writes() -> Result<()> {
    let f = Fixture::new()?;
    let order = ["task_other_2", "task_shift_1"];
    let report = f.report(&order, &[])?;
    assert_eq!(report["selection_status"], "ready_for_review", "{report:#}");
    assert_eq!(report["serial_order"], json!(order));
    assert_ne!(
        report["tasks"][0]["source_sha256"],
        report["tasks"][1]["source_sha256"]
    );
    assert!(report["tasks"][0]["owning_plan_path"]
        .as_str()
        .unwrap()
        .ends_with("NEXT_ACTIONS.md"));
    assert_eq!(report["runtime_enforced"], false);
    assert_eq!(
        report["resource_review"]["status"],
        "required_before_dispatch"
    );
    assert_eq!(
        report,
        f.report(
            &order,
            &[
                "--expected-proposal-sha256",
                report["proposal_sha256"].as_str().unwrap()
            ]
        )?
    );
    let reverse = f.report(&["task_shift_1", "task_other_2"], &[])?;
    assert_ne!(report["proposal_sha256"], reverse["proposal_sha256"]);
    let mut queue = f.queue()?;
    queue[0]["command"] = json!("printf changed-fixture-command");
    f.save(&queue)?;
    assert!(!f
        .run(
            &order,
            &[
                "--expected-proposal-sha256",
                report["proposal_sha256"].as_str().unwrap()
            ]
        )?
        .status
        .success());
    assert_ne!(
        report["queue_sha256"],
        f.report(&order, &[])?["queue_sha256"]
    );
    Ok(())
}

#[test]
fn proposal_holds_changed_or_unbound_owners() -> Result<()> {
    let f = Fixture::new()?;
    let path = f.home.path().join("project/TODO.md");
    fs::write(
        &path,
        format!("{}\nchanged narrative\n", fs::read_to_string(&path)?),
    )?;
    let report = f.report(&["task_shift_1", "task_other_2"], &[])?;
    assert_eq!(report["selection_status"], "held");
    assert_eq!(report["serial_order"], json!([]));
    assert!(report["tasks"][0]["reasons"][0]
        .as_str()
        .unwrap()
        .contains("baseline changed"));
    assert_eq!(report["tasks"][1]["status"], "eligible_for_serial_proposal");
    let mut queue = f.queue()?;
    queue[0]["artifact_refs"] = json!([]);
    f.save(&queue)?;
    let report = f.report(&["task_shift_1"], &[])?;
    assert!(report["tasks"][0]["reasons"][0]
        .as_str()
        .unwrap()
        .contains("owning TODO binding required"));
    Ok(())
}

#[test]
fn proposal_keeps_selected_predecessor_unaccepted() -> Result<()> {
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    let path = f.home.path().join("project/TODO.md");
    let artifact = write_owner(
        &path,
        "project",
        json!([
            source_task("task_shift_1", &["task_other_2"], "unreviewed"),
            source_task("task_other_2", &[], "unreviewed")
        ]),
    )?;
    for i in 0..2 {
        queue[i]["artifact_refs"] = json!([artifact]);
    }
    queue[1]["project_key"] = json!("project");
    queue[1]["execution_brief"]["project_key"] = json!("project");
    queue[1]["workdir"] = queue[0]["workdir"].clone();
    f.save(&queue)?;
    let report = f.report(&["task_other_2", "task_shift_1"], &[])?;
    assert_eq!(report["selection_status"], "held");
    assert!(report["tasks"][1]["reasons"][0]
        .as_str()
        .unwrap()
        .contains("dependency is not accepted"));
    let artifact = write_owner(
        &path,
        "project",
        json!([
            source_task("task_shift_1", &["task_other_2"], "unreviewed"),
            source_task("task_other_2", &[], "accepted")
        ]),
    )?;
    for i in 0..2 {
        queue[i]["artifact_refs"] = json!([artifact]);
    }
    f.save(&queue)?;
    let accepted = f.report(&["task_shift_1"], &[])?;
    assert_eq!(
        accepted["selection_status"], "ready_for_review",
        "{accepted:#}"
    );
    Ok(())
}

#[test]
fn proposal_rejects_missing_duplicate_or_wrong_project_selections() -> Result<()> {
    let f = Fixture::new()?;
    assert!(!f.run(&["unknown"], &[])?.status.success());
    assert!(!f
        .run(&["task_shift_1", "task_shift_1"], &[])?
        .status
        .success());
    assert!(!f
        .run(&["task_shift_1"], &["--max-concurrent-tasks", "2"])?
        .status
        .success());
    let original = f.queue()?;
    let mut queue = original.clone();
    queue[0]["project_key"] = json!("outside");
    f.save(&queue)?;
    assert!(!f.run(&["task_shift_1"], &[])?.status.success());
    let mut queue = original.clone();
    queue[0]["workdir"] = queue[1]["workdir"].clone();
    f.save(&queue)?;
    assert_eq!(
        f.report(&["task_shift_1"], &[])?["selection_status"],
        "held"
    );
    let mut queue = original;
    let duplicate = queue[0].clone();
    queue.as_array_mut().unwrap().push(duplicate);
    f.save(&queue)?;
    assert!(!f.run(&["task_shift_1"], &[])?.status.success());
    Ok(())
}

#[test]
fn proposal_retains_active_and_unknown_resource_holds() -> Result<()> {
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    let mut active = queue[0].clone();
    active["task_id"] = json!("external-running");
    active["project_key"] = json!("outside");
    active["status"] = json!("running");
    queue.as_array_mut().unwrap().push(active);
    f.save(&queue)?;
    let report = f.report(&["task_shift_1", "task_other_2"], &[])?;
    assert_eq!(report["selection_status"], "held");
    assert_eq!(
        report["tasks"][0]["reasons"],
        json!(["active_or_uncertain_queue_occupancy"])
    );
    assert_eq!(report["tasks"][1]["status"], "eligible_for_serial_proposal");
    queue[2]["workdir"] = json!(f.home.path().join("missing-running-root"));
    f.save(&queue)?;
    let report = f.report(&["task_other_2"], &[])?;
    assert_eq!(report["queue_conflicts"]["status"], "incomplete");
    assert_eq!(report["selection_status"], "held");
    Ok(())
}

#[test]
fn proposal_empty_selection_and_stale_registry_review_are_inert() -> Result<()> {
    let mut f = Fixture::new()?;
    assert_eq!(f.report(&[], &[])?["selection_status"], "no_selected_work");
    let report = f.report(&["task_shift_1", "task_other_2"], &[])?;
    let hash = report["proposal_sha256"].as_str().unwrap();
    fs::write(
        &f.registry,
        format!(
            "{}\n# fixture registry version\n",
            fs::read_to_string(&f.registry)?
        ),
    )?;
    assert!(!f
        .run(
            &["task_shift_1", "task_other_2"],
            &["--expected-proposal-sha256", hash]
        )?
        .status
        .success());
    let queue = f.queue()?;
    f.save(&json!([]))?;
    assert_eq!(f.report(&[], &[])?["serial_order"], json!([]));
    f.save(&queue)?;
    f.review = "missing-review".into();
    assert!(!f.run(&["task_shift_1"], &[])?.status.success());
    Ok(())
}

#[test]
fn proposal_refuses_isolation_probes_and_missing_profiles_and_holds_ambiguous_roots() -> Result<()>
{
    let f = Fixture::new()?;
    for extra in [
        vec!["--host-policy", "linux-bubblewrap-external-ip-deny"],
        vec!["--read-policy", "linux-landlock-workdir-and-runtime-only"],
        vec!["--profile", "missing-proposal-profile"],
    ] {
        assert!(!f.run(&["task_shift_1"], &extra)?.status.success());
    }
    assert!(!app_dir(f.home.path())
        .join("profiles/missing-proposal-profile")
        .exists());
    fs::write(
        &f.registry,
        format!(
            "{}\n[projects.overlap]\nworkspace_patterns = [\"project\"]\n",
            fs::read_to_string(&f.registry)?
        ),
    )?;
    let report = f.report(&["task_shift_1"], &[])?;
    assert_eq!(report["selection_status"], "held");
    assert!(report["tasks"][0]["reasons"][0]
        .as_str()
        .unwrap()
        .contains("multiple registry entries"));
    Ok(())
}
