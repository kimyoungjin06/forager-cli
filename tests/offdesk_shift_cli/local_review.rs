use super::proposal::Fixture;
use super::*;
use std::collections::BTreeMap;

#[cfg(target_os = "linux")]
#[path = "persistence_boundary.rs"]
mod persistence_boundary;

#[cfg(target_os = "linux")]
#[path = "metadata_review.rs"]
mod metadata_review;

#[cfg(target_os = "linux")]
#[path = "canonical_return.rs"]
mod canonical_return;

fn command(f: &Fixture) -> Command {
    let mut command = forager_command(f.home.path());
    command.env("FORAGER_PROJECT_REGISTRY", &f.registry);
    command
}

fn success(output: std::process::Output) -> Result<Value> {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn prepared() -> Result<Fixture> {
    prepared_with_controls(&[])
}

fn prepared_with_controls(controls: &[&str]) -> Result<Fixture> {
    prepared_with_task_choices(controls, false)
}

fn prepared_with_task_choices(controls: &[&str], declared: bool) -> Result<Fixture> {
    let f = Fixture::new()?;
    if declared {
        let mut tasks = f.queue()?;
        for task in tasks.as_array_mut().unwrap() {
            task["provider_id"] = json!("fixture-provider");
            task["model"] = json!("fixture-model");
            task["result_artifact_path"] =
                json!(Path::new(task["workdir"].as_str().unwrap()).join("result.json"));
        }
        f.save(&tasks)?;
    }
    let extra = controls
        .iter()
        .flat_map(|id| ["--require-preparation-control", *id])
        .collect::<Vec<_>>();
    let order = ["task_other_2", "task_shift_1"];
    let proposal = f.report(&order, &extra)?;
    let mut cmd = command(&f);
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
        "--serial-proposal-sha256",
        proposal["proposal_sha256"].as_str().unwrap(),
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--json",
    ]);
    for task in order {
        cmd.args(["--task-id", task]);
    }
    cmd.args(extra);
    success(cmd.output()?)?;
    Ok(f)
}

fn snapshot(root: &Path) -> Result<BTreeMap<PathBuf, Vec<u8>>> {
    let mut files = BTreeMap::new();
    for entry in fs::read_dir(root)? {
        let path = entry?.path();
        if path.is_dir() {
            files.extend(snapshot(&path)?);
        } else {
            files.insert(path.clone(), fs::read(path)?);
        }
    }
    Ok(files)
}

fn inspect(f: &Fixture, extra: &[&str]) -> Result<Value> {
    let before = snapshot(f.home.path())?;
    let output = command(f)
        .args(["offdesk", "shift", "start-review", "local-review", "--json"])
        .args(extra)
        .output()?;
    assert_eq!(
        snapshot(f.home.path())?,
        before,
        "start review changed stored state"
    );
    success(output)
}

const SCOPE: [&str; 4] = ["--project-key", "project", "--project-key", "other"];

fn request(f: &Fixture, review: &Value, extra: &[&str]) -> Result<std::process::Output> {
    Ok(command(f)
        .args([
            "offdesk",
            "shift",
            "request-arm-review",
            "local-review",
            "--expected-review-sha256",
            review["review_sha256"].as_str().unwrap(),
            "--json",
        ])
        .args(extra)
        .output()?)
}

#[test]
fn local_review_is_read_only_and_requires_exact_scope() -> Result<()> {
    let f = prepared()?;
    let empty = inspect(&f, &[])?;
    assert_eq!(empty["requestable"], false);
    assert_eq!(empty["preferences"]["project_keys"], json!([]));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &empty, &[])?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], true, "{review:#}");
    assert_eq!(review["tasks"][0]["binding"]["task_id"], "task_other_2");
    assert!(review["tasks"][0]["command"].is_string());
    assert!(review["tasks"][0]["workdir"].is_string());
    assert_eq!(review["task_usage"]["cost_micro_usd"], Value::Null);
    assert_eq!(
        review["task_usage"]["cost_ceiling"],
        "not_bound_by_this_review"
    );
    assert_eq!(review["tasks"][0]["provider"], "unknown");
    assert_eq!(review, inspect(&f, &SCOPE)?);
    assert_eq!(
        inspect(&f, &["--project-key", "project"])?["requestable"],
        false
    );
    assert_eq!(
        inspect(
            &f,
            &[
                "--project-key",
                "project",
                "--project-key",
                "other",
                "--project-key",
                "unselected"
            ]
        )?["requestable"],
        false
    );
    let text = command(&f)
        .args(["offdesk", "shift", "start-review", "local-review"])
        .args(SCOPE)
        .output()?;
    assert!(text.status.success());
    let text = String::from_utf8(text.stdout)?;
    assert!(text.contains("Task token/cost usage: UNKNOWN"));
    assert!(text.contains("Running tasks are not killed or checkpointed"));
    Ok(())
}

#[test]
fn night_defaults_merge_and_one_run_overrides_do_not_save() -> Result<()> {
    let f = prepared()?;
    fs::write(
        app_dir(f.home.path()).join("config.toml"),
        "[night]\nproject_keys = ['project','other']\napproval_ttl_minutes = 41\n",
    )?;
    fs::write(
        profile_dir(f.home.path()).join("config.toml"),
        "[night]\napproval_ttl_minutes = 12\n",
    )?;
    let inherited = inspect(&f, &[])?;
    assert_eq!(inherited["requestable"], true);
    assert_eq!(inherited["preferences"]["approval_ttl_minutes"], 12);
    assert_eq!(
        inherited["preferences"]["scope_origin"],
        "saved_global_profile"
    );
    let one_run = inspect(
        &f,
        &[
            "--project-key",
            "other",
            "--project-key",
            "project",
            "--approval-ttl-minutes",
            "8",
        ],
    )?;
    assert_eq!(one_run["preferences"]["approval_ttl_minutes"], 8);
    assert_eq!(one_run["preferences"]["scope_origin"], "one_run_override");
    assert_eq!(one_run["manifest"], inherited["manifest"]);
    fs::write(
        profile_dir(f.home.path()).join("config.toml"),
        "[night]\nproject_keys = []\n",
    )?;
    let cleared = inspect(&f, &[])?;
    assert_eq!(cleared["preferences"]["approval_ttl_minutes"], 41);
    assert_eq!(cleared["requestable"], false);
    assert_eq!(inspect(&f, &SCOPE)?["requestable"], true);
    let bad = command(&f)
        .args([
            "offdesk",
            "shift",
            "start-review",
            "local-review",
            "--approval-ttl-minutes",
            "0",
        ])
        .output()?;
    assert!(!bad.status.success());
    Ok(())
}

#[test]
fn local_arm_request_is_versioned_deduplicated_and_never_consumes_approval() -> Result<()> {
    let f = prepared()?;
    let review = inspect(&f, &SCOPE)?;
    let pending = success(request(&f, &review, &SCOPE)?)?;
    assert_eq!(pending["status"], "pending");
    let id = pending["approval_id"].as_str().unwrap();
    let before = snapshot(f.home.path())?;
    assert!(
        !request(&f, &review, &SCOPE)?.status.success(),
        "old screen should require refresh"
    );
    assert_eq!(snapshot(f.home.path())?, before);
    let refreshed = inspect(&f, &SCOPE)?;
    let repeated = success(request(&f, &refreshed, &SCOPE)?)?;
    assert_eq!(repeated, pending);
    success(command(&f).args(["offdesk", "ok", id, "--json"]).output()?)?;
    let approved = inspect(&f, &SCOPE)?;
    let requested = success(request(&f, &approved, &SCOPE)?)?;
    assert_eq!(requested["status"], "approved");
    assert_eq!(requested["approval_id"], id);
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert!(shown["arm_receipt"].is_null() && shown["state"].is_null());
    let queue = f.queue()?;
    for task in queue.as_array().unwrap() {
        assert_eq!(task["attempt_count"], 0);
        assert!(task["background_ticket_id"].is_null());
    }
    Ok(())
}

#[test]
fn local_review_rejects_changed_defaults_and_source_before_any_request() -> Result<()> {
    let f = prepared()?;
    let review = inspect(&f, &SCOPE)?;
    fs::write(
        app_dir(f.home.path()).join("config.toml"),
        "[night]\napproval_ttl_minutes = 15\n",
    )?;
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    let fresh = inspect(&f, &SCOPE)?;
    let mut queue = f.queue()?;
    queue[0]["command"] = json!("changed-after-local-review");
    f.save(&queue)?;
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &fresh, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    assert_eq!(inspect(&f, &SCOPE)?["requestable"], false);
    Ok(())
}

#[test]
fn local_review_does_not_create_a_missing_profile_or_run_migrations() -> Result<()> {
    let home = tempdir()?;
    let output = forager_command(home.path())
        .args([
            "-p",
            "missing",
            "offdesk",
            "shift",
            "start-review",
            "absent",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(!app_dir(home.path()).exists());
    let f = prepared()?;
    fs::write(app_dir(f.home.path()).join(".schema_version"), "3")?;
    inspect(&f, &SCOPE)?;
    assert_eq!(
        fs::read_to_string(app_dir(f.home.path()).join(".schema_version"))?,
        "3"
    );
    Ok(())
}

#[test]
fn expired_local_review_cannot_create_approval() -> Result<()> {
    let f = prepared()?;
    // Advance only this inert fixture's window into the past without a clock sleep.
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    let manifest = ledger["manifests"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|m| m["shift_id"] == "local-review")
        .unwrap();
    manifest["window"]["starts_at"] = json!(rfc3339(Utc::now() - Duration::minutes(2)));
    manifest["window"]["stops_at"] = json!(rfc3339(Utc::now() - Duration::minutes(1)));
    manifest["created_at"] = json!(rfc3339(Utc::now() - Duration::minutes(3)));
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], false);
    assert!(review["holds"]
        .as_array()
        .unwrap()
        .contains(&json!("shift_window_expired")));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

fn pending_review(f: &Fixture) -> Result<(Value, String)> {
    let review = inspect(f, &SCOPE)?;
    let pending = success(request(f, &review, &SCOPE)?)?;
    Ok((
        inspect(f, &SCOPE)?,
        pending["approval_id"].as_str().unwrap().to_string(),
    ))
}

fn confirm(
    f: &Fixture,
    review: &Value,
    id: &str,
    acknowledge: bool,
) -> Result<std::process::Output> {
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
        "fixture-local-operator",
        "--json",
    ]);
    cmd.args(SCOPE);
    if acknowledge {
        cmd.arg("--acknowledge-unknown-task-usage");
    }
    Ok(cmd.output()?)
}

#[test]
fn local_confirmation_arms_exact_pending_without_dispatch_or_duplicate_receipt() -> Result<()> {
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, false)?.status.success());
    assert_eq!(
        snapshot(f.home.path())?,
        before,
        "unknown usage was silently accepted"
    );
    let armed = success(confirm(&f, &review, &id, true)?)?;
    assert_eq!(armed["approval_id"], id);
    assert_eq!(
        armed["arm_receipt"]["approved_by"],
        "fixture-local-operator"
    );
    assert_eq!(armed["state"]["status"], "armed");
    assert_eq!(armed["task_dispatch_requires_fresh_approval"], true);
    let after = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    assert_eq!(
        snapshot(f.home.path())?,
        after,
        "duplicate confirmation changed state"
    );
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["arm_receipt"], armed["arm_receipt"]);
    assert_eq!(shown["task_launch_reservations"], json!([]));
    success(
        command(&f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "local-review",
                "--task-id",
                "task_other_2",
                "--json",
            ])
            .output()?,
    )?;
    for task in f.queue()?.as_array().unwrap() {
        assert_eq!(task["attempt_count"], 0);
        assert!(task["background_ticket_id"].is_null());
    }
    let pending = success(
        command(&f)
            .args(["offdesk", "pending", "--json"])
            .output()?,
    )?;
    assert!(pending
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["action"] == "dispatch.runtime"));
    Ok(())
}

#[test]
fn local_confirmation_recovers_approved_request_without_rewriting_its_operator() -> Result<()> {
    let f = prepared()?;
    let (_, id) = pending_review(&f)?;
    success(
        command(&f)
            .args(["offdesk", "ok", &id, "--by", "prior-operator", "--json"])
            .output()?,
    )?;
    let review = inspect(&f, &SCOPE)?;
    for _ in 0..3 {
        assert_eq!(
            inspect(&f, &SCOPE)?,
            review,
            "equal approval content changed across processes"
        );
    }
    let armed = success(confirm(&f, &review, &id, true)?)?;
    assert_eq!(armed["arm_receipt"]["approved_by"], "prior-operator");
    assert_eq!(armed["state"]["status"], "armed");
    Ok(())
}

#[test]
fn local_confirmation_rejects_wrong_id_stale_preferences_and_source_before_approval() -> Result<()>
{
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, "unrelated-approval", true)?
        .status
        .success());
    assert_eq!(snapshot(f.home.path())?, before);
    fs::write(
        app_dir(f.home.path()).join("config.toml"),
        "[night]\napproval_ttl_minutes = 14\n",
    )?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    let fresh = inspect(&f, &SCOPE)?;
    let mut queue = f.queue()?;
    queue[0]["command"] = json!("changed-before-local-confirmation");
    f.save(&queue)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &fresh, &id, true)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    assert!(approvals
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["approval_id"] == id && a["status"] == "pending"));
    Ok(())
}

#[test]
fn local_confirmation_refuses_denied_expired_and_tampered_approval_versions() -> Result<()> {
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    let path = profile_dir(f.home.path()).join("pending_action_approvals.json");
    let original = fs::read(&path)?;
    for mode in ["denied", "expired", "tampered"] {
        let mut approvals: Value = serde_json::from_slice(&original)?;
        let approval = approvals
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|a| a["approval_id"] == id)
            .unwrap();
        match mode {
            "denied" => approval["status"] = json!("denied"),
            "expired" => {
                approval["created_at"] = json!(rfc3339(Utc::now() - Duration::minutes(3)));
                approval["expires_at"] = json!(rfc3339(Utc::now() - Duration::minutes(1)));
            }
            _ => approval["reason"] = json!("tampered manifest presentation"),
        }
        fs::write(&path, serde_json::to_vec(&approvals)?)?;
        let before = snapshot(f.home.path())?;
        assert!(!confirm(&f, &review, &id, true)?.status.success(), "{mode}");
        assert_eq!(snapshot(f.home.path())?, before);
        let refreshed = inspect(&f, &SCOPE)?;
        assert!(
            !confirm(&f, &refreshed, &id, true)?.status.success(),
            "fresh {mode}"
        );
        assert_eq!(snapshot(f.home.path())?, before);
    }
    Ok(())
}

#[test]
fn preparation_version_matches_inert_packet_review_and_approval() -> Result<()> {
    let f = prepared()?;
    let review = inspect(&f, &SCOPE)?;
    let binding = &review["preparation_review_binding"];
    assert_eq!(
        binding["report_sha256"],
        review["preparation"]["report_sha256"]
    );
    assert_eq!(binding["advisory_only"], true);
    assert_eq!(binding["runtime_policy_authorized"], false);
    assert_eq!(review["preparation"]["ready_for_launch"], false);
    let mut cmd = command(&f);
    cmd.args(["ondesk", "prepare-offdesk"]);
    for source in review["preparation"]["sources"].as_array().unwrap() {
        cmd.args([
            "--source",
            &format!(
                "{}={}",
                source["project_key"].as_str().unwrap(),
                source["path"].as_str().unwrap()
            ),
        ]);
    }
    for task in review["preparation"]["tasks"].as_array().unwrap() {
        cmd.args([
            "--task",
            &format!(
                "{}/{}",
                task["project_key"].as_str().unwrap(),
                task["task_id"].as_str().unwrap()
            ),
        ]);
    }
    let before = snapshot(f.home.path())?;
    let packet = success(cmd.output()?)?;
    assert_eq!(packet["report_sha256"], binding["report_sha256"]);
    assert_eq!(snapshot(f.home.path())?, before);
    let (fresh, id) = pending_review(&f)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let approval = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["approval_id"] == id)
        .unwrap();
    let context = &approval["metadata"]["approval_brief"]["context"];
    assert_eq!(
        context["preparation_report_sha256"],
        binding["report_sha256"]
    );
    assert_eq!(
        context["preparation_decision_rules_sha256"],
        binding["decision_rules_sha256"]
    );
    let armed = success(confirm(&f, &fresh, &id, true)?)?;
    assert_eq!(armed["state"]["status"], "armed");
    assert!(f
        .queue()?
        .as_array()
        .unwrap()
        .iter()
        .all(|task| task["attempt_count"] == 0));
    Ok(())
}

#[test]
fn preparation_version_drift_refuses_request_and_direct_arm_without_writes() -> Result<()> {
    let f = prepared()?;
    let old = inspect(&f, &SCOPE)?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["preparation_review"]["report_sha256"] = json!("f".repeat(64));
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let fresh = inspect(&f, &SCOPE)?;
    assert_eq!(fresh["requestable"], false);
    assert!(fresh["holds"]
        .to_string()
        .contains("preparation review version changed"));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &old, &SCOPE)?.status.success());
    assert!(!request(&f, &fresh, &SCOPE)?.status.success());
    let output = command(&f)
        .args(["offdesk", "shift", "arm", "local-review", "--json"])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("preparation review version changed"));
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn preparation_rules_drift_refuses_dispatch_even_with_coherent_ledger_hashes() -> Result<()> {
    use forager::offdesk::{
        offdesk_shift_arm_receipt_sha256, offdesk_shift_manifest_sha256, OffdeskShiftArmReceiptV1,
        OffdeskShiftManifestV1,
    };
    let f = prepared()?;
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["preparation_review"]["decision_rules_sha256"] = json!("f".repeat(64));
    let manifest: OffdeskShiftManifestV1 = serde_json::from_value(ledger["manifests"][0].clone())?;
    let hash = offdesk_shift_manifest_sha256(&manifest)?;
    ledger["arm_receipts"][0]["manifest_sha256"] = json!(hash);
    let arm: OffdeskShiftArmReceiptV1 = serde_json::from_value(ledger["arm_receipts"][0].clone())?;
    let arm_hash = offdesk_shift_arm_receipt_sha256(&arm, &manifest)?;
    for key in ["states", "transitions"] {
        for record in ledger[key].as_array_mut().unwrap() {
            record["manifest_sha256"] = json!(hash);
            record["arm_receipt_sha256"] = json!(arm_hash);
        }
    }
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["task_launch_reservations"], json!([]));
    let before = snapshot(f.home.path())?;
    let output = command(&f)
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "local-review",
            "--task-id",
            "task_other_2",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("preparation review version changed"));
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn legacy_manifest_without_preparation_binding_retains_confirmation_support() -> Result<()> {
    let f = prepared()?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]
        .as_object_mut()
        .unwrap()
        .remove("preparation_review");
    let original = ledger["manifests"][0].clone();
    fs::write(&path, serde_json::to_vec(&ledger)?)?;
    assert!(inspect(&f, &SCOPE)?["preparation_review_binding"].is_null());
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    let after: Value = serde_json::from_slice(&fs::read(path)?)?;
    assert_eq!(after["manifests"][0], original);
    Ok(())
}

#[test]
fn telegram_confirmation_binds_the_same_preparation_version_without_dispatch() -> Result<()> {
    let f = prepared_with_task_choices(
        &[
            "stop_new_dispatch_at_window_end",
            "hold_failed_serial_predecessor",
            "require_declared_task_output",
            "require_declared_provider_model",
        ],
        true,
    )?;
    let review = inspect(&f, &SCOPE)?;
    let manifest: forager::offdesk::OffdeskShiftManifestV1 =
        serde_json::from_value(review["manifest"].clone())?;
    let hash = forager::offdesk::offdesk_shift_manifest_sha256(&manifest)?;
    let scripts = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let call = "import json,sys; sys.path.insert(0,sys.argv[1]); import offdesk_remote_operator_telegram as m; print(json.dumps(m.arm_confirmed_shift(sys.argv[2], 'default', 'local-review', sys.argv[3])))";
    let output = Command::new("python3")
        .env("HOME", f.home.path())
        .env("XDG_CONFIG_HOME", f.home.path().join(".config"))
        .env("FORAGER_PROJECT_REGISTRY", &f.registry)
        .env_remove("FORAGER_PROFILE")
        .env_remove("AGENT_OF_EMPIRES_PROFILE")
        .args([
            "-c",
            call,
            scripts.to_str().unwrap(),
            env!("CARGO_BIN_EXE_forager"),
            &hash,
        ])
        .output()?;
    success(output)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let arm = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["metadata"]["kind"] == "offdesk_shift_arm")
        .unwrap();
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["preparation_report_sha256"],
        review["preparation_review_binding"]["report_sha256"]
    );
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["preparation_required_controls"],
        "stop_new_dispatch_at_window_end,hold_failed_serial_predecessor,require_declared_task_output,require_declared_provider_model"
    );
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["task_execution_choice_review_sha256"],
        review["task_execution_choices"]["review_sha256"]
    );
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["state"]["status"], "armed");
    assert_eq!(shown["task_launch_reservations"], json!([]));
    Ok(())
}

#[test]
fn selected_preparation_controls_are_versioned_and_approved_without_dispatch() -> Result<()> {
    let controls = [
        "stop_new_dispatch_at_window_end",
        "hold_unaccepted_dependencies",
        "hold_source_drift",
        "hold_active_or_uncertain_workdir",
        "hold_uncertain_launch",
        "no_automatic_task_retry",
        "hold_failed_serial_predecessor",
    ];
    let f = prepared_with_controls(&controls)?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], true, "{review:#}");
    assert_eq!(
        review["preparation_capabilities"]["requirements_satisfied"],
        true
    );
    assert_eq!(
        review["manifest"]["preparation_requirements"]["required_controls"],
        json!(controls)
    );
    assert_eq!(review["preparation"]["ready_for_launch"], false);
    let (fresh, id) = pending_review(&f)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let approval = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["approval_id"] == id)
        .unwrap();
    assert_eq!(
        approval["metadata"]["approval_brief"]["context"]["preparation_required_controls"],
        controls.join(",")
    );
    assert_eq!(
        approval["metadata"]["approval_brief"]["context"]["preparation_capability_rules_sha256"],
        review["manifest"]["preparation_requirements"]["capability_rules_sha256"]
    );
    success(confirm(&f, &fresh, &id, true)?)?;
    success(
        command(&f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "local-review",
                "--task-id",
                "task_other_2",
                "--json",
            ])
            .output()?,
    )?;
    assert!(f
        .queue()?
        .as_array()
        .unwrap()
        .iter()
        .all(|t| t["attempt_count"] == 0));
    let pending = success(
        command(&f)
            .args(["offdesk", "pending", "--json"])
            .output()?,
    )?;
    assert!(pending
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["action"] == "dispatch.runtime"));
    Ok(())
}

#[test]
fn unsupported_or_unconfigured_preparation_controls_hold_before_any_approval() -> Result<()> {
    for (id, status) in [
        ("checkpoint_running_task", "unsupported"),
        ("unknown_control", "unsupported"),
        ("guard_weekly_provider_usage", "missing_selection"),
    ] {
        let f = prepared_with_controls(&[id])?;
        let review = inspect(&f, &SCOPE)?;
        assert_eq!(review["requestable"], false);
        assert_eq!(
            review["preparation_capabilities"]["controls"][0]["status"],
            status
        );
        let before = snapshot(f.home.path())?;
        assert!(!request(&f, &review, &SCOPE)?.status.success());
        let output = command(&f)
            .args(["offdesk", "shift", "arm", "local-review", "--json"])
            .output()?;
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("preparation requirements hold"));
        assert_eq!(snapshot(f.home.path())?, before);
    }
    Ok(())
}

#[test]
fn changed_capability_rules_refuse_confirmation_without_mutation() -> Result<()> {
    let f = prepared_with_controls(&["stop_new_dispatch_at_window_end"])?;
    let (review, id) = pending_review(&f)?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["preparation_requirements"]["capability_rules_sha256"] =
        json!("f".repeat(64));
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    let fresh = inspect(&f, &SCOPE)?;
    assert_eq!(fresh["requestable"], false);
    assert!(fresh["preparation_capabilities"]["holds"]
        .to_string()
        .contains("capability rules changed"));
    assert!(!confirm(&f, &fresh, &id, true)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn unsupported_control_refuses_dispatch_after_coherent_ledger_rebinding() -> Result<()> {
    use forager::offdesk::{
        offdesk_shift_arm_receipt_sha256, offdesk_shift_manifest_sha256, OffdeskShiftArmReceiptV1,
        OffdeskShiftManifestV1,
    };
    let f = prepared_with_controls(&["stop_new_dispatch_at_window_end"])?;
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["preparation_requirements"]["required_controls"] =
        json!(["checkpoint_running_task"]);
    let manifest: OffdeskShiftManifestV1 = serde_json::from_value(ledger["manifests"][0].clone())?;
    let hash = offdesk_shift_manifest_sha256(&manifest)?;
    ledger["arm_receipts"][0]["manifest_sha256"] = json!(hash);
    let arm: OffdeskShiftArmReceiptV1 = serde_json::from_value(ledger["arm_receipts"][0].clone())?;
    let arm_hash = offdesk_shift_arm_receipt_sha256(&arm, &manifest)?;
    for key in ["states", "transitions"] {
        for record in ledger[key].as_array_mut().unwrap() {
            record["manifest_sha256"] = json!(hash);
            record["arm_receipt_sha256"] = json!(arm_hash);
        }
    }
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["task_launch_reservations"], json!([]));
    let before = snapshot(f.home.path())?;
    let output = command(&f)
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "local-review",
            "--task-id",
            "task_other_2",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("unsupported_preparation_control"));
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn serial_proposal_binds_required_controls_and_invalid_ids_never_panic() -> Result<()> {
    let f = Fixture::new()?;
    let order = ["task_other_2", "task_shift_1"];
    let first = f.report(
        &order,
        &[
            "--require-preparation-control",
            "stop_new_dispatch_at_window_end",
        ],
    )?;
    let changed = f.run(
        &order,
        &[
            "--require-preparation-control",
            "checkpoint_running_task",
            "--expected-proposal-sha256",
            first["proposal_sha256"].as_str().unwrap(),
        ],
    )?;
    assert!(!changed.status.success());
    assert!(String::from_utf8_lossy(&changed.stderr).contains("serial proposal changed"));
    let bad = f.run(&[], &["--require-preparation-control", "INVALID-id"])?;
    assert!(!bad.status.success());
    let stderr = String::from_utf8_lossy(&bad.stderr);
    assert!(stderr.contains("invalid preparation control ID"));
    assert!(!stderr.contains("panicked"));
    Ok(())
}

#[test]
fn task_execution_review_is_inert_and_keeps_undeclared_worker_choices_visible() -> Result<()> {
    let f = prepared()?;
    let review = inspect(&f, &SCOPE)?;
    let choices = &review["task_execution_choices"];
    assert_eq!(choices["all_task_identities_match"], true);
    assert_eq!(choices["worker_qualification_complete"], false);
    assert_eq!(choices["runtime_policy_authorized"], false);
    assert_eq!(choices["tasks"][0]["task_id"], "task_other_2");
    assert_eq!(choices["tasks"][0]["provider_model_declared"], false);
    assert_eq!(choices["tasks"][0]["output_declared"], false);
    assert_eq!(choices, &inspect(&f, &SCOPE)?["task_execution_choices"]);
    assert_eq!(review["preparation"]["ready_for_launch"], false);
    let text = command(&f)
        .args(["offdesk", "shift", "start-review", "local-review"])
        .args(SCOPE)
        .output()?;
    assert!(text.status.success());
    assert!(String::from_utf8_lossy(&text.stdout).contains("qualified worker contract: false"));
    Ok(())
}

#[test]
fn required_task_declarations_hold_before_approval_and_qualification_stays_unsupported(
) -> Result<()> {
    for (id, status) in [
        ("require_declared_task_output", "missing_selection"),
        ("require_declared_provider_model", "missing_selection"),
        ("require_selected_task_isolation", "missing_selection"),
        ("require_qualified_worker_contract", "unsupported"),
    ] {
        let f = prepared_with_controls(&[id])?;
        let review = inspect(&f, &SCOPE)?;
        assert_eq!(review["requestable"], false);
        assert_eq!(
            review["preparation_capabilities"]["controls"][0]["status"],
            status
        );
        let before = snapshot(f.home.path())?;
        assert!(!request(&f, &review, &SCOPE)?.status.success());
        let output = command(&f)
            .args(["offdesk", "shift", "arm", "local-review", "--json"])
            .output()?;
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("preparation requirements hold"));
        assert_eq!(snapshot(f.home.path())?, before);
    }
    Ok(())
}

#[test]
fn declared_task_choices_bind_local_approval_without_qualifying_the_worker() -> Result<()> {
    let f = prepared_with_task_choices(
        &[
            "require_declared_task_output",
            "require_declared_provider_model",
        ],
        true,
    )?;
    let (review, id) = pending_review(&f)?;
    assert_eq!(
        review["preparation_capabilities"]["requirements_satisfied"],
        true
    );
    assert_eq!(
        review["task_execution_choices"]["worker_qualification_complete"],
        false
    );
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let approval = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["approval_id"] == id)
        .unwrap();
    assert_eq!(
        approval["metadata"]["approval_brief"]["context"]["task_execution_choice_review_sha256"],
        review["task_execution_choices"]["review_sha256"]
    );
    let lines = approval["metadata"]["approval_brief"]["summary_lines"].to_string();
    assert!(lines.contains("fixture-model"));
    assert!(lines.contains("result.json"));
    success(confirm(&f, &review, &id, true)?)?;
    assert!(f
        .queue()?
        .as_array()
        .unwrap()
        .iter()
        .all(|t| t["attempt_count"] == 0));
    Ok(())
}

#[test]
fn changed_task_metadata_invalidates_choice_review_and_confirmation_without_writes() -> Result<()> {
    let f = prepared_with_task_choices(&["require_declared_provider_model"], true)?;
    let (review, id) = pending_review(&f)?;
    let mut tasks = f.queue()?;
    tasks[0]["model"] = json!("changed-after-review");
    f.save(&tasks)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    let fresh = inspect(&f, &SCOPE)?;
    assert_eq!(fresh["requestable"], false);
    assert_eq!(
        fresh["task_execution_choices"]["all_task_identities_match"],
        false
    );
    assert_ne!(
        fresh["task_execution_choices"]["review_sha256"],
        review["task_execution_choices"]["review_sha256"]
    );
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
fn removed_required_result_path_holds_dispatch_after_coherent_identity_rebinding() -> Result<()> {
    use forager::offdesk::{
        offdesk_shift_arm_receipt_sha256, offdesk_shift_manifest_sha256,
        offdesk_task_launch_spec_sha256, OffdeskShiftArmReceiptV1, OffdeskShiftManifestV1,
        OffdeskTask,
    };
    let f = prepared_with_task_choices(&["require_declared_task_output"], true)?;
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    let mut tasks = f.queue()?;
    let task = tasks
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|t| t["task_id"] == "task_other_2")
        .unwrap();
    task["result_artifact_path"] = Value::Null;
    let changed: OffdeskTask = serde_json::from_value(task.clone())?;
    f.save(&tasks)?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    let binding = ledger["manifests"][0]["task_bindings"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|t| t["task_id"] == "task_other_2")
        .unwrap();
    binding["launch_spec_sha256"] = json!(offdesk_task_launch_spec_sha256(&changed)?);
    let manifest: OffdeskShiftManifestV1 = serde_json::from_value(ledger["manifests"][0].clone())?;
    let hash = offdesk_shift_manifest_sha256(&manifest)?;
    ledger["arm_receipts"][0]["manifest_sha256"] = json!(hash);
    let arm: OffdeskShiftArmReceiptV1 = serde_json::from_value(ledger["arm_receipts"][0].clone())?;
    let arm_hash = offdesk_shift_arm_receipt_sha256(&arm, &manifest)?;
    for key in ["states", "transitions"] {
        for record in ledger[key].as_array_mut().unwrap() {
            record["manifest_sha256"] = json!(hash);
            record["arm_receipt_sha256"] = json!(arm_hash);
        }
    }
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["task_launch_reservations"], json!([]));
    let before = snapshot(f.home.path())?;
    let output = command(&f)
        .args([
            "offdesk",
            "tick",
            "--shift-id",
            "local-review",
            "--task-id",
            "task_other_2",
            "--json",
        ])
        .output()?;
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr)
        .contains("requires_declared_result_path_for_every_exact_task"));
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

fn prepared_native() -> Result<Fixture> {
    prepared_native_mode("fixed")
}
fn prepared_native_mode(mode: &str) -> Result<Fixture> {
    prepared_native_controls(mode, &[])
}
fn prepared_native_controls(mode: &str, extra_controls: &[&str]) -> Result<Fixture> {
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        let output = Command::new("python3")
            .arg(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/support/native_invocation_fixture.py"),
            )
            .args([
                task["workdir"].as_str().unwrap(),
                task["task_id"].as_str().unwrap(),
                task["request_id"].as_str().unwrap(),
                task["project_key"].as_str().unwrap(),
                &rfc3339(f.stop - Duration::minutes(1)),
            ])
            .arg(mode)
            .output()?;
        let selected = success(output)?;
        for key in ["command", "provider_id", "model", "result_artifact_path"] {
            task[key] = selected[key].clone();
        }
        task["artifact_refs"]
            .as_array_mut()
            .unwrap()
            .push(selected["artifact_ref"].clone());
    }
    f.save(&queue)?;
    let order = ["task_other_2", "task_shift_1"];
    let mut controls = vec![
        "--require-preparation-control",
        "require_verified_native_worker_invocation",
    ];
    if mode != "fixed" {
        controls.extend([
            "--require-preparation-control",
            "require_native_entry_preflight_recheck",
        ]);
    }
    for control in extra_controls {
        controls.extend(["--require-preparation-control", *control]);
    }
    let proposal = f.report(&order, &controls)?;
    let mut cmd = command(&f);
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
        "--serial-proposal-sha256",
        proposal["proposal_sha256"].as_str().unwrap(),
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--json",
    ]);
    for task in order {
        cmd.args(["--task-id", task]);
    }
    success(cmd.args(controls).output()?)?;
    Ok(f)
}

#[test]
#[cfg(target_os = "linux")]
fn selected_worker_execution_contract_is_shared_with_approval_and_actual_stub_execution(
) -> Result<()> {
    let f = prepared_native_controls("stale", &["require_bound_worker_execution_contract"])?;
    let before = snapshot(f.home.path())?;
    let review = inspect(&f, &SCOPE)?;
    let choices = &review["task_execution_choices"];
    assert_eq!(review["requestable"], true);
    assert_eq!(choices["all_worker_execution_contracts_bound"], true);
    let hash = choices["worker_execution_contracts_sha256"]
        .as_str()
        .unwrap()
        .to_string();
    let contract = &choices["tasks"][0]["worker_execution_contract"];
    assert_eq!(contract["schema"], "offdesk_native_execution_contract.v1");
    assert_eq!(contract["max_controller_worker_starts"], 1);
    assert_eq!(contract["automatic_controller_retries"], 0);
    assert_eq!(contract["checkpoint"], "unsupported_no_automatic_resume");
    assert_eq!(contract["entry_preflight"]["closeout_reserve_seconds"], 10);
    assert_eq!(contract["worker_qualification_complete"], false);
    assert_eq!(snapshot(f.home.path())?, before);
    complete_native_tasks(&f)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let arm = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["metadata"]["kind"] == "offdesk_shift_arm")
        .unwrap();
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["worker_execution_contracts_sha256"],
        hash
    );
    let tasks = forager::offdesk::OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let after = inspect(&f, &SCOPE)?;
    assert_eq!(
        after["task_execution_choices"]["worker_execution_contracts_sha256"],
        hash
    );
    assert!(tasks.iter().all(|task| task.attempt_count == 1));
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn bound_execution_contract_does_not_admit_fixed_or_unsupported_worker_actions() -> Result<()> {
    for (mode, extras) in [
        ("fixed", vec!["require_bound_worker_execution_contract"]),
        (
            "stale",
            vec![
                "require_bound_worker_execution_contract",
                "checkpoint_running_task",
            ],
        ),
        (
            "stale",
            vec![
                "require_bound_worker_execution_contract",
                "require_qualified_worker_contract",
            ],
        ),
    ] {
        let f = prepared_native_controls(mode, &extras)?;
        let before = snapshot(f.home.path())?;
        let review = inspect(&f, &SCOPE)?;
        assert_eq!(review["requestable"], false, "{mode}: {extras:?}");
        assert!(!request(&f, &review, &SCOPE)?.status.success());
        assert_eq!(snapshot(f.home.path())?, before);
        assert!(!f
            .home
            .path()
            .join("secondary/.night/fake-worker-started")
            .exists());
    }
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn telegram_confirmation_binds_selected_worker_contract_without_dispatch() -> Result<()> {
    let f = prepared_native_controls("stale", &["require_bound_worker_execution_contract"])?;
    let review = inspect(&f, &SCOPE)?;
    let scripts = Path::new(env!("CARGO_MANIFEST_DIR")).join("scripts");
    let call = "import json,sys; sys.path.insert(0,sys.argv[1]); import offdesk_remote_operator_telegram as m; print(json.dumps(m.arm_confirmed_shift(sys.argv[2], 'default', 'local-review', sys.argv[3])))";
    let output = Command::new("python3")
        .env("HOME", f.home.path())
        .env("XDG_CONFIG_HOME", f.home.path().join(".config"))
        .env("FORAGER_PROJECT_REGISTRY", &f.registry)
        .env_remove("FORAGER_PROFILE")
        .env_remove("AGENT_OF_EMPIRES_PROFILE")
        .args([
            "-c",
            call,
            scripts.to_str().unwrap(),
            env!("CARGO_BIN_EXE_forager"),
            review["manifest_sha256"].as_str().unwrap(),
        ])
        .output()?;
    success(output)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let arm = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["metadata"]["kind"] == "offdesk_shift_arm")
        .unwrap();
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["worker_execution_contracts_sha256"],
        review["task_execution_choices"]["worker_execution_contracts_sha256"]
    );
    let shown = success(
        command(&f)
            .args(["offdesk", "shift", "show", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(shown["task_launch_reservations"], json!([]));
    assert!(!f
        .home
        .path()
        .join("secondary/.night/fake-worker-started")
        .exists());
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn selected_worker_limit_drift_refuses_confirmation_without_consuming_approval() -> Result<()> {
    let f = prepared_native_controls("stale", &["require_bound_worker_execution_contract"])?;
    let (review, id) = pending_review(&f)?;
    let path = f.home.path().join("secondary/.runtime/invocation.json");
    let mut contract: Value = serde_json::from_slice(&fs::read(&path)?)?;
    contract["command_timeout"] = json!(31);
    fs::write(&path, serde_json::to_vec(&contract)?)?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    let fresh = inspect(&f, &SCOPE)?;
    assert_eq!(fresh["requestable"], false);
    assert_eq!(
        fresh["task_execution_choices"]["all_worker_execution_contracts_bound"],
        false
    );
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn bound_native_invocations_complete_two_disposable_tasks_without_provider_calls_or_acceptance(
) -> Result<()> {
    complete_native_tasks(&prepared_native()?)
}

fn prepared_codex(read_policy: bool) -> Result<Fixture> {
    prepared_codex_version(read_policy, false)
}

fn prepared_codex_version(read_policy: bool, selectors: bool) -> Result<Fixture> {
    prepared_codex_format(
        read_policy,
        if selectors {
            "selectors-v2"
        } else {
            "literal-v1"
        },
    )
}

fn prepared_codex_format(read_policy: bool, format: &str) -> Result<Fixture> {
    use forager::offdesk::workflow::codex_review_invocation;
    let f = Fixture::new()?;
    let mut queue = f.queue()?;
    for task in queue.as_array_mut().unwrap() {
        let root = Path::new(task["workdir"].as_str().unwrap());
        let mut helper = Command::new("/usr/bin/python3");
        helper
            .args(["-I", "-B", "tests/support/codex_review_fixture.py"])
            .args([
                root.to_str().unwrap(),
                task["task_id"].as_str().unwrap(),
                task["request_id"].as_str().unwrap(),
                task["project_key"].as_str().unwrap(),
            ]);
        if format != "literal-v1" {
            helper.arg(format);
        }
        let output = helper.output()?;
        let selected = success(output)?;
        let hash = selected["selection_sha256"].as_str().unwrap();
        let root = root.to_path_buf();
        task["command"] = json!(codex_review_invocation::canonical(
            root.to_str().unwrap(),
            hash
        ));
        task["provider_id"] = json!("gpt");
        task["model"] = json!("gpt-6.1-sol");
        task["result_artifact_path"] = json!(root.join("result.json"));
        task["artifact_refs"].as_array_mut().unwrap().push(json!({
            "artifact_id":format!("{}{hash}",codex_review_invocation::PREFIX),
            "path":root.join("REVIEW_SELECTION.json"),"present":true}));
    }
    f.save(&queue)?;
    let controls = [
        "--require-preparation-control",
        "require_bound_worker_execution_contract",
    ];
    let order = ["task_other_2", "task_shift_1"];
    let mut extra = controls.to_vec();
    if read_policy {
        extra.extend(["--read-policy", "linux-landlock-workdir-and-runtime-only"]);
    }
    let proposal = f.report(&order, &controls)?;
    assert_eq!(
        proposal["selection_status"], "ready_for_review",
        "{proposal:#}"
    );
    let mut cmd = command(&f);
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
        "--serial-proposal-sha256",
        proposal["proposal_sha256"].as_str().unwrap(),
        "--serial-project-key",
        "project",
        "--serial-project-key",
        "other",
        "--json",
    ]);
    for task in order {
        cmd.args(["--task-id", task]);
    }
    let output = cmd.args(extra).output()?;
    success(output)?;
    Ok(f)
}

#[test]
#[cfg(target_os = "linux")]
fn codex_contract_uses_admitted_ticket_and_completes_two_fake_reviews_without_bridge() -> Result<()>
{
    complete_codex_reviews(false)
}

#[test]
#[cfg(target_os = "linux")]
fn codex_contract_selector_reports_extract_host_quotes_for_two_projects() -> Result<()> {
    complete_codex_reviews(true)
}

#[test]
#[cfg(target_os = "linux")]
fn codex_contract_evidence_ids_bind_full_supplied_catalog_for_two_projects() -> Result<()> {
    complete_codex_reviews_format("evidence-v3")
}

fn complete_codex_reviews(selectors: bool) -> Result<()> {
    complete_codex_reviews_format(if selectors {
        "selectors-v2"
    } else {
        "literal-v1"
    })
}

fn complete_codex_reviews_format(format: &str) -> Result<()> {
    complete_codex_fixture(format).map(|_| ())
}

fn complete_codex_fixture(format: &str) -> Result<Fixture> {
    let f = prepared_codex_format(true, format)?;
    let before = snapshot(f.home.path())?;
    assert_eq!(inspect(&f, &SCOPE)?["requestable"], true);
    assert_eq!(snapshot(f.home.path())?, before);
    let (review, id) = pending_review(&f)?;
    assert_eq!(
        review["task_execution_choices"]["all_worker_execution_contracts_bound"],
        true
    );
    let hash = review["task_execution_choices"]["worker_execution_contracts_sha256"].clone();
    let contract = &review["task_execution_choices"]["tasks"][0]["worker_execution_contract"];
    assert_eq!(contract["schema"], "offdesk_codex_execution_contract.v1");
    assert_eq!(contract["worker_qualification_complete"], false);
    assert_eq!(
        contract["runtime_binding"],
        "dispatcher_environment_before_spawn"
    );
    assert!(before.keys().all(|p| !p.ends_with("RUNTIME_BINDING.json")));
    success(confirm(&f, &review, &id, true)?)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let arm = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["metadata"]["kind"] == "offdesk_shift_arm")
        .unwrap();
    assert_eq!(
        arm["metadata"]["approval_brief"]["context"]["worker_execution_contracts_sha256"],
        hash
    );
    let owners = [
        f.home.path().join("project/TODO.md"),
        f.home.path().join("secondary/NEXT_ACTIONS.md"),
    ];
    let owner_bytes = owners
        .iter()
        .map(fs::read)
        .collect::<std::io::Result<Vec<_>>>()?;
    for task in ["task_other_2", "task_shift_1"] {
        success(
            command(&f)
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "local-review",
                    "--task-id",
                    task,
                    "--json",
                ])
                .output()?,
        )?;
        super::serial::approve_dispatch(&f, task)?;
        let launched = success(
            command(&f)
                .env("FORAGER_OFFDESK_RUNTIME_BINDING", "forged inherited value")
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "local-review",
                    "--task-id",
                    task,
                    "--json",
                ])
                .output()?,
        )?;
        assert_eq!(launched["launched"], 1, "{launched:#}");
        super::serial::wait_for_completed(&f, task)?;
    }
    let tasks = forager::offdesk::OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    for task in tasks {
        let root = Path::new(&task.workdir);
        let result: Value = serde_json::from_slice(&fs::read(root.join("result.json"))?)?;
        assert_eq!(
            result["ticket_id"].as_str(),
            task.background_ticket_id.as_deref()
        );
        assert_eq!(result["project_key"], task.project_key);
        assert_eq!(result["status"], "pending_user_review");
        assert_eq!(task.attempt_count, 1);
        assert!(root.join("fake-native-started").exists());
        assert!(!root.join("start_worker.py").exists());
        if format != "literal-v1" {
            let raw: Value = serde_json::from_slice(&fs::read(root.join("MODEL_REPORT.json"))?)?;
            let quoted: Value =
                serde_json::from_slice(&fs::read(root.join("HOST_QUOTED_REPORT.json"))?)?;
            let bound: Value = serde_json::from_slice(&fs::read(root.join("BOUND_REVIEW.json"))?)?;
            assert_eq!(
                raw["schema"],
                if format == "evidence-v3" {
                    "offdesk_codex_evidence_report.v3"
                } else {
                    "offdesk_codex_selector_report.v2"
                }
            );
            assert!(raw["findings"][0]["evidence"][0].get("quote").is_none());
            assert_eq!(
                bound["schema"],
                if format == "evidence-v3" {
                    "offdesk_codex_bound_review.v3"
                } else {
                    "offdesk_codex_bound_review.v2"
                }
            );
            assert_eq!(bound["report"], quoted);
            assert_eq!(
                quoted["findings"][0]["evidence"][0]["quote"],
                if format == "evidence-v3" {
                    "    first = '가'\n\tsecond = '나'\n\n649: literal source line"
                } else {
                    "    first = '가'\n\tsecond = '나'\n"
                }
            );
            if format == "evidence-v3" {
                assert!(raw["findings"][0]["evidence"][0]
                    .get("line_start")
                    .is_none());
                assert_eq!(
                    bound["model_report_schema"],
                    "offdesk_codex_evidence_report.v3"
                );
                assert!(bound["evidence_catalog_sha256"]
                    .as_str()
                    .is_some_and(|v| v.len() == 64));
                assert!(bound["evidence_helper_sha256"]
                    .as_str()
                    .is_some_and(|v| v.len() == 64));
            }
            assert_eq!(
                bound["quote_provenance"]["origin"],
                "host_extracted_from_pinned_supplied_source"
            );
            assert_eq!(bound["semantic_acceptance"], false);
            assert_eq!(result["plan_update_authorized"], false);
        }
    }
    assert_eq!(
        inspect(&f, &SCOPE)?["task_execution_choices"]["worker_execution_contracts_sha256"],
        hash
    );
    assert_eq!(
        owners
            .iter()
            .map(fs::read)
            .collect::<std::io::Result<Vec<_>>>()?,
        owner_bytes
    );
    let again = success(
        command(&f)
            .args(["offdesk", "tick", "--shift-id", "local-review", "--json"])
            .output()?,
    )?;
    assert_eq!(again["launched"], 0);
    Ok(f)
}

#[test]
#[cfg(target_os = "linux")]
fn codex_contract_evidence_ids_refuse_rebound_helper_or_missing_catalog() -> Result<()> {
    use forager::offdesk::workflow::codex_review_invocation;
    use sha2::{Digest, Sha256};
    for mode in ["helper", "catalog"] {
        let f = prepared_codex_format(true, "evidence-v3")?;
        let mut task = forager::offdesk::OffdeskTaskStore::new(profile_dir(f.home.path()))
            .load()?
            .into_iter()
            .next()
            .unwrap();
        let root = Path::new(&task.workdir);
        let mut cfg: Value =
            serde_json::from_slice(&fs::read(root.join("REVIEW_SELECTION.json"))?)?;
        if mode == "helper" {
            fs::write(
                root.join(".runtime/offdesk_evidence_contract.py"),
                "# unqualified replacement\n",
            )?;
            cfg["runtime_files"][".runtime/offdesk_evidence_contract.py"] = json!(format!(
                "{:x}",
                Sha256::digest(b"# unqualified replacement\n")
            ));
        } else {
            cfg["artifacts"]
                .as_object_mut()
                .unwrap()
                .remove("EVIDENCE_CATALOG.json");
        }
        let bytes = serde_json::to_vec(&cfg)?;
        let hash = format!("{:x}", Sha256::digest(&bytes));
        fs::write(root.join("REVIEW_SELECTION.json"), bytes)?;
        task.command = codex_review_invocation::canonical(&task.workdir, &hash);
        task.artifact_refs
            .iter_mut()
            .find(|a| a.artifact_id.starts_with(codex_review_invocation::PREFIX))
            .unwrap()
            .artifact_id = format!("{}{hash}", codex_review_invocation::PREFIX);
        let error = codex_review_invocation::inspect(&task, None, Utc::now())
            .unwrap_err()
            .to_string();
        assert!(
            error.contains(if mode == "helper" {
                "evidence helper differs"
            } else {
                "artifact set differs"
            }),
            "{error}"
        );
        assert!(!root.join("ATTEMPT.json").exists());
    }
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn codex_contract_requires_read_isolation_and_refuses_source_or_ticket_drift() -> Result<()> {
    let held = prepared_codex(false)?;
    assert_eq!(inspect(&held, &SCOPE)?["requestable"], false);
    let f = prepared_codex(true)?;
    let (review, id) = pending_review(&f)?;
    let root = f.home.path().join("project");
    fs::write(root.join("input/code.py"), "changed after review")?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    assert_eq!(inspect(&f, &SCOPE)?["requestable"], false);
    assert!(!root.join("ATTEMPT.json").exists());
    let tasks = forager::offdesk::OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let mut task = tasks
        .iter()
        .find(|t| t.project_key == "other")
        .unwrap()
        .clone();
    task.attempt_count = 1;
    task.background_ticket_id = Some("current-ticket".into());
    fs::write(
        Path::new(&task.workdir).join("RUNTIME_BINDING.json"),
        serde_json::to_vec(&json!({
        "task_id":task.task_id,"request_id":task.request_id,"ticket_id":"old-ticket"}))?,
    )?;
    assert!(
        forager::offdesk::workflow::worker_invocation::inspect(&task, None, Utc::now()).is_err()
    );
    Ok(())
}

fn complete_native_tasks(f: &Fixture) -> Result<()> {
    let (review, id) = pending_review(f)?;
    assert_eq!(
        review["preparation_capabilities"]["requirements_satisfied"],
        true
    );
    assert_eq!(
        review["preparation_capabilities"]["native_invocations"][0]
            ["worker_qualification_complete"],
        false
    );
    success(confirm(f, &review, &id, true)?)?;
    let owners = [
        f.home.path().join("project/TODO.md"),
        f.home.path().join("secondary/NEXT_ACTIONS.md"),
    ];
    let before = owners
        .iter()
        .map(fs::read)
        .collect::<std::io::Result<Vec<_>>>()?;
    for task in ["task_other_2", "task_shift_1"] {
        let mut pending = false;
        for _ in 0..60 {
            success(
                command(f)
                    .args([
                        "offdesk",
                        "tick",
                        "--shift-id",
                        "local-review",
                        "--task-id",
                        task,
                        "--json",
                    ])
                    .output()?,
            )?;
            let approvals = success(command(f).args(["offdesk", "pending", "--json"]).output()?)?;
            if approvals
                .as_array()
                .unwrap()
                .iter()
                .any(|a| a["task_id"] == task && a["action"] == "dispatch.runtime")
            {
                pending = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        assert!(pending);
        super::serial::approve_dispatch(f, task)?;
        let result = success(
            command(f)
                .args([
                    "offdesk",
                    "tick",
                    "--shift-id",
                    "local-review",
                    "--task-id",
                    task,
                    "--json",
                ])
                .output()?,
        )?;
        assert_eq!(result["launched"], 1, "{result:#}");
        super::serial::wait_for_completed(f, task)?;
    }
    assert_eq!(
        owners
            .iter()
            .map(fs::read)
            .collect::<std::io::Result<Vec<_>>>()?,
        before
    );
    for root in [
        f.home.path().join("project"),
        f.home.path().join("secondary"),
    ] {
        assert!(root.join(".night/fake-worker-started").exists());
        let result: Value =
            serde_json::from_slice(&fs::read(root.join(".night/run/result.json"))?)?;
        assert_eq!(result["verification_state"], "passed");
        assert_eq!(result["review_state"], "pending_user_review");
    }
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn native_invocation_input_drift_blocks_confirmation_and_nonshift_dispatch() -> Result<()> {
    let f = prepared_native()?;
    let (review, id) = pending_review(&f)?;
    fs::write(
        f.home.path().join("secondary/.night/prompt.txt"),
        "changed after review",
    )?;
    let before = snapshot(f.home.path())?;
    assert!(!confirm(&f, &review, &id, true)?.status.success());
    let fresh = inspect(&f, &SCOPE)?;
    assert_eq!(fresh["requestable"], false);
    assert!(fresh["holds"].to_string().contains("input hash changed"));
    let tick = command(&f)
        .args(["offdesk", "tick", "--task-id", "task_other_2", "--json"])
        .output()?;
    assert!(!tick.status.success());
    assert!(String::from_utf8_lossy(&tick.stderr).contains("input hash changed"));
    assert!(!f
        .home
        .path()
        .join("secondary/.night/fake-worker-started")
        .exists());
    // The normal tick may create its profile lock; it must not consume approval or change the task queue.
    let after = snapshot(f.home.path())?;
    for (path, bytes) in before {
        if path
            .file_name()
            .is_some_and(|name| name == "offdesk_tick.lock")
        {
            continue;
        }
        assert_eq!(after.get(&path), Some(&bytes), "{}", path.display());
    }
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn native_entry_reservation_holds_even_without_preparation_requirements() -> Result<()> {
    let f = prepared_native()?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]
        .as_object_mut()
        .unwrap()
        .remove("preparation_requirements");
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    fs::write(
        f.home
            .path()
            .join("secondary/.night/run.invocation-entry.json"),
        "unresolved entry",
    )?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], false);
    assert!(review["holds"]
        .to_string()
        .contains("entry already reserved"));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn pending_native_entry_publication_holds_fresh_approval_without_consuming_partial_bytes(
) -> Result<()> {
    let f = prepared_native()?;
    let path = f
        .home
        .path()
        .join("secondary/.night/run.invocation-entry.json.partial");
    fs::write(&path, "{")?;
    let before = snapshot(f.home.path())?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], false);
    assert!(review["holds"].to_string().contains("publication pending"));
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    let mut tasks = forager::offdesk::OffdeskTaskStore::new(profile_dir(f.home.path())).load()?;
    let task = tasks
        .iter_mut()
        .find(|task| task.task_id == "task_other_2")
        .unwrap();
    task.status = forager::offdesk::OffdeskTaskStatus::Running;
    task.attempt_count = 1;
    task.background_ticket_id = Some("already-admitted-fixture".into());
    let observed =
        forager::offdesk::workflow::native_worker_invocation::inspect(task, None, Utc::now())?
            .unwrap();
    assert_eq!(observed["entry_publication_pending"], true);
    assert_eq!(observed["entry_observed"], false);
    assert_eq!(observed["worker_qualification_complete"], false);
    assert_eq!(snapshot(f.home.path())?, before);
    assert!(!f
        .home
        .path()
        .join("secondary/.night/fake-worker-started")
        .exists());
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn stale_native_preflight_is_rechecked_once_inside_each_approved_project_task() -> Result<()> {
    let f = prepared_native_mode("stale")?;
    let roots = [
        f.home.path().join("project"),
        f.home.path().join("secondary"),
    ];
    let originals = roots
        .iter()
        .map(|r| fs::read(r.join(".runtime/preflight.json")))
        .collect::<std::io::Result<Vec<_>>>()?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], true, "{review:#}");
    assert_eq!(
        review["preparation_capabilities"]["native_invocations"][0]["preflight_policy"],
        "rerun_sealed_checks_once_at_entry"
    );
    // Preview and confirmation must not run project commands.
    for root in &roots {
        assert_eq!(
            fs::read_to_string(root.join(".night/preflight-invocations"))?
                .lines()
                .count(),
            1
        );
    }
    let text = command(&f)
        .args(["offdesk", "shift", "start-review", "local-review"])
        .args(SCOPE)
        .output()?;
    assert!(text.status.success());
    assert!(String::from_utf8_lossy(&text.stdout)
        .contains("Entry preflight: at most 10s | minimum worker 5s | closeout reserve 10s"));
    complete_native_tasks(&f)?;
    let approvals: Value = serde_json::from_slice(&fs::read(
        profile_dir(f.home.path()).join("pending_action_approvals.json"),
    )?)?;
    let arm = approvals
        .as_array()
        .unwrap()
        .iter()
        .find(|a| a["metadata"]["kind"] == "offdesk_shift_arm")
        .unwrap();
    let summary = arm["metadata"]["approval_brief"]["summary_lines"].to_string();
    assert!(summary.contains("minimum worker 5s; closeout reserve 10s"));

    for (root, original) in roots.iter().zip(originals) {
        assert_eq!(fs::read(root.join(".runtime/preflight.json"))?, original);
        assert_eq!(
            fs::read_to_string(root.join(".night/preflight-invocations"))?
                .lines()
                .count(),
            3
        );
        let fresh: Value =
            serde_json::from_slice(&fs::read(root.join(".night/run.entry-preflight.json"))?)?;
        let host_result: Value =
            serde_json::from_slice(&fs::read(root.join(".night/run.result.json"))?)?;
        assert_eq!(fresh["passed"], true);
        assert_eq!(fresh["tests_passed"], 1);
        assert_eq!(host_result["entry_preflight_tests_passed"], 1);
        assert_eq!(host_result["worker_qualification_complete"], false);
    }
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn failed_entry_preflight_preserves_failure_and_never_starts_the_native_worker_or_next_project(
) -> Result<()> {
    let f = prepared_native_mode("stale")?;
    let (review, id) = pending_review(&f)?;
    success(confirm(&f, &review, &id, true)?)?;
    let root = f.home.path().join("secondary");
    fs::write(
        root.join(".night/fail-entry-preflight"),
        "selected runtime check failure",
    )?;
    let original = fs::read(root.join(".runtime/preflight.json"))?;
    success(
        command(&f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "local-review",
                "--task-id",
                "task_other_2",
                "--json",
            ])
            .output()?,
    )?;
    super::serial::approve_dispatch(&f, "task_other_2")?;
    let launched = success(
        command(&f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "local-review",
                "--task-id",
                "task_other_2",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(launched["launched"], 1);
    let mut failed = false;
    for _ in 0..60 {
        success(command(&f).args(["offdesk", "poll", "--json"]).output()?)?;
        if f.queue()?
            .as_array()
            .unwrap()
            .iter()
            .any(|t| t["task_id"] == "task_other_2" && t["status"] == "resume_pending")
        {
            failed = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
    assert!(failed, "{}", f.queue()?);
    assert!(!root.join(".night/fake-worker-started").exists());
    assert!(!root.join(".night/run.result.json").exists());
    let fresh: Value =
        serde_json::from_slice(&fs::read(root.join(".night/run.entry-preflight.json"))?)?;
    assert_eq!(fresh["passed"], false);
    assert_eq!(fs::read(root.join(".runtime/preflight.json"))?, original);
    let next = success(
        command(&f)
            .args([
                "offdesk",
                "tick",
                "--shift-id",
                "local-review",
                "--task-id",
                "task_shift_1",
                "--json",
            ])
            .output()?,
    )?;
    assert_eq!(next["launched"], 0);
    let queue = f.queue()?;
    assert!(queue
        .as_array()
        .unwrap()
        .iter()
        .any(|t| t["task_id"] == "task_shift_1" && t["attempt_count"] == 0));
    assert!(root.join(".night/run.invocation-entry.json").exists());
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn fixed_native_invocations_do_not_implicitly_select_entry_recheck() -> Result<()> {
    let f = prepared_native()?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["preparation_requirements"]["required_controls"]
        .as_array_mut()
        .unwrap()
        .push(json!("require_native_entry_preflight_recheck"));
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], false);
    assert!(review["holds"]
        .to_string()
        .contains("requires_explicit_native_entry_preflight_policy_for_every_task"));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
#[cfg(target_os = "linux")]
fn future_shift_start_must_leave_the_selected_entry_preflight_and_closeout_window() -> Result<()> {
    let f = prepared_native_mode("stale")?;
    let path = profile_dir(f.home.path()).join("offdesk_shifts.v1.json");
    let mut ledger: Value = serde_json::from_slice(&fs::read(&path)?)?;
    ledger["manifests"][0]["window"]["starts_at"] = json!(rfc3339(
        f.stop - Duration::minutes(1) - Duration::seconds(5)
    ));
    fs::write(path, serde_json::to_vec(&ledger)?)?;
    let review = inspect(&f, &SCOPE)?;
    assert_eq!(review["requestable"], false);
    assert!(review["preparation_capabilities"]["holds"]
        .to_string()
        .contains("insufficient time for entry preflight"));
    let before = snapshot(f.home.path())?;
    assert!(!request(&f, &review, &SCOPE)?.status.success());
    assert_eq!(snapshot(f.home.path())?, before);
    Ok(())
}

#[test]
#[cfg(not(target_os = "linux"))]
fn native_publication_refuses_missing_linux_atomic_primitive_without_worker_execution() -> Result<()>
{
    let f = Fixture::new()?;
    let before = f.queue()?;
    let task = &before[0];
    let root = Path::new(task["workdir"].as_str().unwrap());
    let out = Command::new("python3")
        .arg(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/support/native_invocation_fixture.py"),
        )
        .args([
            root.to_str().unwrap(),
            task["task_id"].as_str().unwrap(),
            task["request_id"].as_str().unwrap(),
            task["project_key"].as_str().unwrap(),
            &rfc3339(f.stop - Duration::minutes(1)),
        ])
        .output()?;
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("requires Linux renameat2"));
    assert!(!root.join(".runtime/invocation.json").exists());
    assert!(!root.join(".night/fake-worker-started").exists());
    assert_eq!(f.queue()?, before);
    Ok(())
}
