//! Explicit native adapter binding; controller identity is not model qualification.
use crate::{
    offdesk::{BackgroundRunnerKind, OffdeskShiftManifestV1, OffdeskShiftWindowV1, OffdeskTask},
    process::{self, FilesystemObjectSnapshot},
};
use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::Read,
    path::{Component, Path},
};

pub const PREFIX: &str = "native-worker-invocation-sha256:";
const CONTROLLER: &[u8] = include_bytes!("../../../scripts/offdesk_native_worker.py");
const BOOTSTRAP: &str = include_str!("../../../scripts/native_worker_bootstrap.py");
#[derive(Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
struct Root {
    path: String,
    device: u64,
    inode: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    path: String,
    sha256: String,
}
#[derive(Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
struct EntryPreflight {
    schema: String,
    timeout_seconds: u64,
    closeout_reserve_seconds: u64,
    minimum_worker_seconds: u64,
}
impl EntryPreflight {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == "offdesk_native_entry_preflight.v1"
                && (1..=3600).contains(&self.timeout_seconds)
                && (5..=3600).contains(&self.closeout_reserve_seconds)
                && (1..=86400).contains(&self.minimum_worker_seconds)
                && self.timeout_seconds
                    + self.closeout_reserve_seconds
                    + self.minimum_worker_seconds
                    <= 86400,
            "invalid native entry preflight/reserve policy"
        );
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Contract {
    schema: String,
    root: Root,
    task_id: String,
    request_id: String,
    project_key: String,
    controller: Input,
    source_manifest: Input,
    preflight: Input,
    prompt: Input,
    worker: Input,
    source_sha256: String,
    out: String,
    base_url: String,
    model: String,
    deadline: DateTime<Utc>,
    max_tool_calls: u64,
    max_repeated_errors: u64,
    command_timeout: u64,
    verify_commands: Vec<Vec<String>>,
    #[serde(default)]
    entry_preflight: Option<EntryPreflight>,
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(crate) fn execution_rules() -> Value {
    json!({"schema":"offdesk_native_execution_rules.v1",
        "technical_steps":["validate_bound_inputs","run_entry_checks_once","run_one_controller_worker","run_declared_verification","publish_pending_review_result"],
        "max_controller_worker_starts":1,"automatic_controller_retries":0,
        "checkpoint":"unsupported_no_automatic_resume",
        "failure_action":"preserve_failure_evidence_and_stop",
        "uncertain_call_action":"hold_without_replay_or_provider_switch",
        "return_disposition":"pending_ondesk_review_no_owning_task_acceptance",
        "unsupported_actions":["resume_checkpoint","retry_uncertain_call","switch_provider","auto_accept_predecessor","continue_arbitrary_scientific_steps"],
        "native_internal_http_retries":"not_controlled_by_this_contract",
        "worker_qualification_complete":false})
}

fn execution_contract(contract: &Contract, invocation_sha256: &str) -> Result<Option<Value>> {
    let Some(policy) = &contract.entry_preflight else {
        return Ok(None);
    };
    let mut result = execution_rules();
    let object = result.as_object_mut().unwrap();
    object.insert(
        "schema".into(),
        json!("offdesk_native_execution_contract.v1"),
    );
    for (key, value) in [
        ("task_id", json!(contract.task_id)),
        ("request_id", json!(contract.request_id)),
        ("project_key", json!(contract.project_key)),
        ("root", json!(contract.root.path)),
        ("invocation_sha256", json!(invocation_sha256)),
        ("controller_sha256", json!(contract.controller.sha256)),
        ("source_sha256", json!(contract.source_sha256)),
        ("deadline", json!(contract.deadline)),
        ("entry_preflight", json!(policy)),
        (
            "worker_deadline",
            json!(
                contract.deadline
                    - chrono::Duration::seconds(policy.closeout_reserve_seconds as i64)
            ),
        ),
        ("command_timeout_seconds", json!(contract.command_timeout)),
        ("max_tool_calls", json!(contract.max_tool_calls)),
        ("max_repeated_errors", json!(contract.max_repeated_errors)),
        ("verification_commands", json!(contract.verify_commands)),
        (
            "result_artifact_path",
            json!(Path::new(&contract.root.path).join(format!("{}.result.json", contract.out))),
        ),
        ("task_cost_or_token_ceiling", Value::Null),
    ] {
        object.insert(key.into(), value);
    }
    let hash = digest(&serde_json::to_vec(&result)?);
    result["contract_sha256"] = json!(hash);
    Ok(Some(result))
}
fn relative(path: &str, prefix: &str) -> Result<()> {
    let parsed = Path::new(path);
    ensure!(
        path.starts_with(prefix)
            && parsed
                .components()
                .all(|c| matches!(c, Component::Normal(_)))
            && parsed.to_str() == Some(path)
            && path
                .split('/')
                .all(|part| !part.is_empty() && part != "." && part != "..")
            && !path.contains("//")
            && !path.ends_with('/'),
        "invalid native invocation relative path"
    );
    Ok(())
}
fn read(root: &File, path: &str, limit: u64, contents: bool) -> Result<(Vec<u8>, String)> {
    ensure!(
        path.starts_with(".runtime/") || path.starts_with(".night/"),
        "native invocation input prefix"
    );
    relative(
        path,
        if path.starts_with(".runtime/") {
            ".runtime/"
        } else {
            ".night/"
        },
    )?;
    read_bound(root, path, limit, contents)
}

pub(crate) fn read_bound(
    root: &File,
    path: &str,
    limit: u64,
    contents: bool,
) -> Result<(Vec<u8>, String)> {
    let mut file = process::open_bounded_regular_file(root, Path::new(path))?;
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1)
            && file.metadata()?.len() <= limit,
        "invalid native invocation file size/link count"
    );
    if let Some(hash) = super::invocation_inputs::lookup(root, path, &before)? {
        if !contents {
            let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
            ensure!(
                before == after,
                "worker input changed during reuse: {}",
                crate::offdesk::operator_safe_text(path)
            );
            return Ok((Vec::new(), hash));
        }
    }
    let mut hash = Sha256::new();
    let mut output = Vec::new();
    let mut total = 0;
    let mut buffer = [0u8; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        total += count as u64;
        ensure!(total <= limit, "native invocation input exceeds limit");
        hash.update(&buffer[..count]);
        if contents {
            output.extend_from_slice(&buffer[..count]);
        }
    }
    let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let reopened = FilesystemObjectSnapshot::from_metadata(
        &process::open_bounded_regular_file(root, Path::new(path))?.metadata()?,
    );
    ensure!(before == after && before == reopened,
        "native invocation input changed during read: {} (before={before:?}, after={after:?}, reopened={reopened:?})",
        crate::offdesk::operator_safe_text(path));
    let hash = format!("{:x}", hash.finalize());
    if !contents {
        super::invocation_inputs::remember(root, path, &file, before, &hash)?;
    }
    Ok((output, hash))
}
pub(crate) fn quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', "'\"'\"'"))
}
fn canonical(root: &str, controller: &Input, contract: &str, hash: &str) -> String {
    [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-c",
        BOOTSTRAP,
        &format!("{root}/{}", controller.path),
        &controller.sha256,
        "run-bound",
        "--root",
        root,
        "--contract",
        contract,
        "--expected-contract-sha256",
        hash,
    ]
    .iter()
    .map(|s| quote(s))
    .collect::<Vec<_>>()
    .join(" ")
}

pub fn inspect(
    task: &OffdeskTask,
    window: Option<&OffdeskShiftWindowV1>,
    now: DateTime<Utc>,
) -> Result<Option<Value>> {
    let refs = task
        .artifact_refs
        .iter()
        .filter(|a| a.artifact_id.starts_with("native-worker-invocation"))
        .collect::<Vec<_>>();
    ensure!(
        refs.len() <= 1,
        "multiple native worker invocation bindings"
    );
    let Some(binding) = refs.first() else {
        return Ok(None);
    };
    ensure!(
        cfg!(target_os = "linux") && task.runner_kind == BackgroundRunnerKind::LocalBackground,
        "bound native invocation requires Linux local-background"
    );
    let expected = binding
        .artifact_id
        .strip_prefix(PREFIX)
        .context("invalid native invocation binding prefix")?;
    let admitted = task.attempt_count == 1
        && task.background_ticket_id.is_some()
        && matches!(
            task.status,
            crate::offdesk::OffdeskTaskStatus::Launched
                | crate::offdesk::OffdeskTaskStatus::Running
                | crate::offdesk::OffdeskTaskStatus::Completed
                | crate::offdesk::OffdeskTaskStatus::Failed
                | crate::offdesk::OffdeskTaskStatus::ResumePending
                | crate::offdesk::OffdeskTaskStatus::Cancelled
        );
    let root_path = Path::new(&task.workdir);
    ensure!(
        root_path.is_absolute() && root_path.canonicalize()? == root_path,
        "native invocation root must be canonical"
    );
    let root = process::open_bounded_root(root_path)?;
    let identity = process::filesystem_file_identity(&root.metadata()?)
        .context("native invocation root identity unavailable")?;
    let path = Path::new(
        binding
            .path
            .as_deref()
            .context("native invocation contract path missing")?,
    )
    .strip_prefix(root_path)?
    .to_str()
    .context("native invocation path is not UTF-8")?;
    relative(path, ".runtime/")?;
    let (raw, hash) = read(&root, path, 65536, true)?;
    ensure!(hash == expected, "native invocation contract hash changed");
    let contract: Contract = serde_json::from_slice(&raw)?;
    let has_policy = serde_json::from_slice::<Value>(&raw)?
        .get("entry_preflight")
        .is_some();
    ensure!(
        (contract.schema == "offdesk_native_invocation.v1" && !has_policy)
            || (contract.schema == "offdesk_native_invocation.v2"
                && contract.entry_preflight.is_some()),
        "native invocation schema/policy mismatch"
    );
    let admission_time = window
        .map(|window| now.max(window.starts_at))
        .unwrap_or(now);
    if let Some(policy) = &contract.entry_preflight {
        policy.validate()?;
        if !admitted {
            let required = policy.timeout_seconds
                + policy.closeout_reserve_seconds
                + policy.minimum_worker_seconds;
            ensure!((contract.deadline-admission_time).num_milliseconds()>=required as i64*1000,
                "native invocation has insufficient time for entry preflight, worker and closeout reserve");
        }
    }

    ensure!(
        contract.root
            == Root {
                path: task.workdir.clone(),
                device: identity.0,
                inode: identity.1
            },
        "native invocation schema/root changed"
    );
    ensure!(
        contract.task_id == task.task_id
            && contract.request_id == task.request_id
            && contract.project_key == task.project_key,
        "native invocation task/request/project mismatch"
    );
    ensure!(
        task.provider_id.as_deref() == Some("ollama")
            && task.model.as_deref() == Some(contract.model.as_str())
            && !contract.model.trim().is_empty(),
        "native invocation provider/model mismatch"
    );
    ensure!(
        (admitted
            || (contract.deadline > admission_time
                && contract.deadline <= now + chrono::Duration::hours(24)))
            && window.is_none_or(|window| contract.deadline <= window.stops_at),
        "native invocation deadline is expired or exceeds shift"
    );
    ensure!(
        (1..=4096).contains(&contract.max_tool_calls)
            && (1..=100).contains(&contract.max_repeated_errors)
            && (1..=86400).contains(&contract.command_timeout),
        "invalid native invocation limits"
    );
    ensure!(
        !contract.verify_commands.is_empty()
            && contract.verify_commands.iter().all(|c| !c.is_empty()
                && !c[0].trim().is_empty()
                && c.iter().all(|s| !s.contains('\0'))),
        "native invocation verification commands missing"
    );
    let endpoint = reqwest::Url::parse(&contract.base_url)?;
    ensure!(
        ["http", "https"].contains(&endpoint.scheme())
            && endpoint.host_str().is_some()
            && endpoint.username().is_empty()
            && endpoint.password().is_none()
            && endpoint.query().is_none()
            && endpoint.fragment().is_none(),
        "invalid native invocation endpoint"
    );
    ensure!(
        contract.controller.path == ".runtime/offdesk_native_worker.py"
            && contract.controller.sha256 == digest(CONTROLLER)
            && contract.source_manifest.path == ".runtime/source_manifest.json",
        "native invocation controller/source manifest differs from supported adapter"
    );
    relative(&contract.prompt.path, ".night/")?;
    relative(&contract.preflight.path, ".runtime/")?;
    relative(&contract.worker.path, ".runtime/")?;
    relative(&contract.out, ".night/")?;
    let mut preflight = Vec::new();
    for (name, input, limit) in [
        ("controller", &contract.controller, 262144),
        ("source_manifest", &contract.source_manifest, 1048576),
        ("preflight", &contract.preflight, 1048576),
        ("prompt", &contract.prompt, 1048576),
        ("worker", &contract.worker, 268435456),
    ] {
        let (data, hash) = read(&root, &input.path, limit, name == "preflight")?;
        ensure!(
            hash == input.sha256,
            "native invocation {name} input hash changed"
        );
        if name == "preflight" {
            preflight = data;
        }
    }
    let receipt: Value = serde_json::from_slice(&preflight)?;
    let completed = receipt["completed_at"]
        .as_f64()
        .context("native invocation preflight completion missing")?;
    let age = now.timestamp_millis() as f64 / 1000.0 - completed;
    ensure!(
        receipt["schema"] == "offdesk_native_preflight.v1"
            && receipt["passed"] == true
            && receipt["tests_passed"].as_u64().is_some_and(|n| n > 0)
            && (admitted || (age >= 0.0 && (contract.entry_preflight.is_some() || age <= 900.0)))
            && receipt["controller_sha256"] == contract.controller.sha256
            && receipt["source_sha256"] == contract.source_sha256
            && receipt["root"]
                == json!({"path":task.workdir,"device":identity.0,"inode":identity.1}),
        "native invocation preflight failed, stale or misbound"
    );
    if contract.entry_preflight.is_some() {
        let checks = receipt["checks"]
            .as_array()
            .context("native entry preflight check argv missing")?;
        ensure!(
            !checks.is_empty()
                && checks.len() <= 128
                && checks.iter().all(|check| check["exit_code"] == 0
                    && check["timed_out"] != true
                    && check["argv"].as_array().is_some_and(|argv| !argv.is_empty()
                        && argv
                            .iter()
                            .all(|part| part.as_str().is_some_and(|text| !text.contains('\0')))
                        && argv[0].as_str().is_some_and(|text| !text.trim().is_empty()))),
            "native entry preflight requires recorded successful check argv"
        );
        if !admitted {
            for suffix in [
                ".entry-preflight.json",
                ".entry-preflight.json.progress.json",
                ".entry-preflight.json.partial",
                ".entry-preflight.json.progress.json.partial",
                ".result.json",
            ] {
                match std::fs::symlink_metadata(root_path.join(format!("{}{suffix}",contract.out))) {
                    Ok(_)=>anyhow::bail!("native entry preflight/output evidence already exists; reconcile without replay"),
                    Err(error) if error.kind()==std::io::ErrorKind::NotFound=>{},Err(error)=>return Err(error.into()),
                }
            }
        }
    }
    let output = root_path.join(&contract.out);
    let parent = output
        .parent()
        .context("native invocation output parent missing")?;
    ensure!(
        parent.is_dir() && parent.canonicalize()? == parent,
        "native invocation output parent missing or linked"
    );
    let output_meta = match std::fs::symlink_metadata(&output) {
        Ok(value) => Some(value),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error.into()),
    };
    ensure!(
        output_meta.is_none()
            || (admitted
                && output_meta
                    .as_ref()
                    .is_some_and(|m| m.is_dir() && !m.file_type().is_symlink())),
        "fresh native invocation output required before admission"
    );
    let entry_path = root_path.join(format!("{}.invocation-entry.json", contract.out));
    let entry_observed = match std::fs::symlink_metadata(&entry_path) {
        Ok(_) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => return Err(error.into()),
    };
    let entry_pending = match std::fs::symlink_metadata(
        root_path.join(format!("{}.invocation-entry.json.partial", contract.out)),
    ) {
        Ok(_) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => return Err(error.into()),
    };
    ensure!(
        admitted || !entry_pending,
        "native invocation entry already reserved; publication pending, reconcile without replay"
    );
    if admitted && entry_observed {
        let (raw, _) = read(
            &root,
            &format!("{}.invocation-entry.json", contract.out),
            65536,
            true,
        )?;
        let entry: Value = serde_json::from_slice(&raw)?;
        ensure!(
            entry["schema"] == "offdesk_native_invocation_entry.v1"
                && entry["invocation_sha256"] == hash
                && entry["task_id"] == task.task_id
                && entry["request_id"] == task.request_id,
            "native invocation entry identity changed"
        );
    } else {
        ensure!(
            admitted || !entry_observed,
            "native invocation entry already reserved; reconcile outcome without replay"
        );
    }
    ensure!(
        task.result_artifact_path.as_deref()
            == root_path
                .join(format!("{}.result.json", contract.out))
                .to_str(),
        "native invocation result path mismatch"
    );
    ensure!(
        task.command == canonical(&task.workdir, &contract.controller, path, expected),
        "native invocation command is not the exact supported bootstrap"
    );
    ensure!(
        process::filesystem_file_identity(&process::open_bounded_root(root_path)?.metadata()?)
            == Some(identity),
        "native invocation root replaced"
    );
    let worker_execution_contract = execution_contract(&contract, &hash)?;
    Ok(Some(
        json!({"schema":"offdesk_native_invocation_review.v1","task_id":task.task_id,"invocation_sha256":hash,
        "controller_sha256":contract.controller.sha256,"deadline":contract.deadline,"model":contract.model,"worker_execution_contract":worker_execution_contract,
        "status":"verified_invocation_adapter_rechecks_source_at_entry","worker_qualification_complete":false,
        "runtime_policy_authorized":false,"source_entry_recheck_required":true,"entry_observed":entry_observed,"entry_publication_pending":entry_pending,"already_admitted":admitted,
        "limits":{"max_tool_calls":contract.max_tool_calls,"max_repeated_errors":contract.max_repeated_errors,"command_timeout":contract.command_timeout},"result_disposition":"pending_ondesk_review","entry_preflight":contract.entry_preflight,
        "preflight_policy":if contract.entry_preflight.is_some() {"rerun_sealed_checks_once_at_entry"} else {"fixed_fresh_receipt"}}),
    ))
}

pub fn validate_selected(
    manifest: &OffdeskShiftManifestV1,
    tasks: &[OffdeskTask],
    now: DateTime<Utc>,
) -> Result<()> {
    for binding in &manifest.task_bindings {
        let task = tasks
            .iter()
            .find(|t| t.task_id == binding.task_id)
            .context("native invocation selected task missing")?;
        if inspect(task, Some(&manifest.window), now)?.is_some() {
            ensure!(
                manifest.budget.max_attempts_per_task == 1,
                "bound native invocation requires one task attempt"
            );
        }
    }
    Ok(())
}
