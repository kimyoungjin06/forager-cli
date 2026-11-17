//! Exact deterministic metadata selection; no model or task acceptance authority.
use super::{
    codex_review_invocation::unique_map,
    native_worker_invocation::{quote, read_bound},
};
use crate::{
    offdesk::{read_policy::ReadPolicy, BackgroundRunnerKind, OffdeskShiftWindowV1, OffdeskTask},
    process,
};
use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path};

pub const PREFIX: &str = "metadata-consumer-invocation-sha256:";
const DISPATCH: &[u8] = include_bytes!("../../../scripts/offdesk_metadata_dispatch.py");
const CONSUMER: &[u8] = include_bytes!("../../../scripts/offdesk_metadata_consumer.py");
const PACKET: &[u8] = include_bytes!("../../../scripts/offdesk_metadata_packet.py");
const VALIDATOR: &[u8] = include_bytes!("../../../scripts/offdesk_resume_checkpoint.py");
const BOOTSTRAP: &str = include_str!("../../../scripts/native_worker_bootstrap.py");
fn digest(raw: &[u8]) -> String {
    format!("{:x}", Sha256::digest(raw))
}

fn valid_identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 100
        && !matches!(value, "." | "..")
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-' | b'.'))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    schema: String,
    executor_kind: String,
    project_key: String,
    task_id: String,
    request_id: String,
    root_device: u64,
    root_inode: u64,
    #[serde(deserialize_with = "unique_map")]
    runtime_files: BTreeMap<String, String>,
    #[serde(deserialize_with = "unique_map")]
    input_files: BTreeMap<String, String>,
    starts_at: f64,
    stops_at: f64,
    total_seconds: u64,
    child_seconds: u64,
    closeout_seconds: u64,
    rows: u64,
    batch_rows: u64,
    read_policy_sha256: String,
}

pub fn canonical(root: &str, expected: &str) -> String {
    [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-B",
        "-c",
        BOOTSTRAP,
        &format!("{root}/.runtime/offdesk_metadata_dispatch.py"),
        &digest(DISPATCH),
        "run-dispatched",
        "--root",
        root,
        "--expected-selection-sha256",
        expected,
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
    let refs: Vec<_> = task
        .artifact_refs
        .iter()
        .filter(|a| a.artifact_id.starts_with("metadata-consumer-invocation"))
        .collect();
    ensure!(refs.len() <= 1, "multiple metadata invocation bindings");
    let Some(binding) = refs.first() else {
        return Ok(None);
    };
    ensure!(
        cfg!(target_os = "linux") && task.runner_kind == BackgroundRunnerKind::LocalBackground,
        "metadata requires Linux local-background"
    );
    ensure!(
        task.provider_id.is_none() && task.model.is_none(),
        "deterministic metadata cannot declare a model/provider"
    );
    let expected = binding
        .artifact_id
        .strip_prefix(PREFIX)
        .context("invalid metadata binding prefix")?;
    let path = Path::new(&task.workdir);
    ensure!(
        path.is_absolute() && path.canonicalize()? == path,
        "metadata root must be canonical"
    );
    ensure!(
        binding.path.as_deref().map(Path::new)
            == Some(path.join("METADATA_SELECTION.json").as_path()),
        "metadata selection path differs"
    );
    let root = process::open_bounded_root(path)?;
    let identity = process::filesystem_file_identity(&root.metadata()?)
        .context("metadata root identity unavailable")?;
    let (raw, hash) = read_bound(&root, "METADATA_SELECTION.json", 65536, true)?;
    ensure!(hash == expected, "metadata selection hash changed");
    let cfg: Selection = serde_json::from_slice(&raw)?;
    ensure!(
        cfg.schema == "offdesk_metadata_selection.v1"
            && cfg.executor_kind == "deterministic_local_no_model"
            && (cfg.root_device, cfg.root_inode) == identity
            && cfg.task_id == task.task_id
            && cfg.request_id == task.request_id
            && cfg.project_key == task.project_key
            && [&cfg.project_key, &cfg.task_id, &cfg.request_id]
                .into_iter()
                .all(|v| valid_identity(v)),
        "metadata selection/root/task identity differs"
    );
    ensure!(
        (1..=128).contains(&cfg.rows)
            && (1..=128).contains(&cfg.batch_rows)
            && cfg.rows.div_ceil(cfg.batch_rows) <= 8,
        "metadata row/batch bound exceeded"
    );
    ensure!(
        (1..=20).contains(&cfg.child_seconds)
            && (5..=20).contains(&cfg.closeout_seconds)
            && cfg.total_seconds <= 120
            && cfg.child_seconds + cfg.closeout_seconds <= cfg.total_seconds
            && cfg.starts_at.is_finite()
            && cfg.stops_at.is_finite()
            && cfg.starts_at >= 0.0
            && 0.0 < cfg.stops_at - cfg.starts_at
            && cfg.stops_at - cfg.starts_at <= cfg.total_seconds as f64,
        "invalid metadata time limits"
    );
    let admitted = task.attempt_count == 1 && task.background_ticket_id.is_some();
    ensure!(
        admitted || cfg.stops_at > now.timestamp() as f64,
        "metadata selection expired"
    );
    if let Some(window) = window {
        ensure!(
            cfg.starts_at >= window.starts_at.timestamp() as f64
                && cfg.stops_at <= window.stops_at.timestamp() as f64,
            "metadata window exceeds shift"
        );
    }
    ensure!(
        cfg.read_policy_sha256 == ReadPolicy::metadata().sha256()?,
        "metadata policy binding differs"
    );
    let runtime = BTreeMap::from([
        (
            ".runtime/offdesk_metadata_dispatch.py".to_string(),
            digest(DISPATCH),
        ),
        (
            ".runtime/offdesk_metadata_consumer.py".to_string(),
            digest(CONSUMER),
        ),
        (
            ".runtime/offdesk_metadata_packet.py".to_string(),
            digest(PACKET),
        ),
        (
            ".runtime/offdesk_resume_checkpoint.py".to_string(),
            digest(VALIDATOR),
        ),
        (
            ".runtime/native_worker_bootstrap.py".to_string(),
            digest(BOOTSTRAP.as_bytes()),
        ),
    ]);
    ensure!(
        cfg.runtime_files == runtime,
        "metadata runtime differs from compiled sources"
    );
    ensure!(
        cfg.input_files.keys().map(String::as_str).eq([
            "adapter.py",
            "configuration.json",
            "consumer.py",
            "input.jsonl",
            "manifest.json",
            "policy.json",
            "row_provenance.jsonl",
            "validator.py"
        ]),
        "metadata input set differs"
    );
    for (name, pin) in cfg.runtime_files.iter().chain(&cfg.input_files) {
        ensure!(
            read_bound(&root, name, 8 * 1024 * 1024, false)?.1 == *pin,
            "metadata source/input changed"
        );
    }
    for (name, bytes) in [
        ("consumer.py", CONSUMER),
        ("validator.py", VALIDATOR),
        ("adapter.py", PACKET),
    ] {
        ensure!(
            cfg.input_files[name] == digest(bytes),
            "metadata component copy differs"
        );
    }
    let config: Value =
        serde_json::from_slice(&read_bound(&root, "configuration.json", 65536, true)?.0)?;
    ensure!(
        config["schema"] == "forager.metadata_consumer.v2"
            && config["batch_rows"] == cfg.batch_rows
            && config["limits"]["total_seconds"] == cfg.total_seconds
            && config["limits"]["work_seconds"]
                .as_u64()
                .zip(config["limits"]["shutdown_grace_seconds"].as_u64())
                .is_some_and(|(work, grace)| work > 0
                    && work
                        .checked_add(grace)
                        .is_some_and(|n| n <= cfg.child_seconds)),
        "metadata component/controller limits differ"
    );
    if !admitted {
        for entry in std::fs::read_dir(path)? {
            let name = entry?.file_name();
            let name = name.to_string_lossy();
            ensure!(
                ![
                    "ATTEMPT.json",
                    "RUNTIME_BINDING.json",
                    "result.json",
                    "FAILURE.json"
                ]
                .contains(&name.as_ref())
                    && !["seed.", "batch-", "entry-", "child-", ".checkpoint-"]
                        .iter()
                        .any(|p| name.starts_with(p)),
                "metadata attempt/output exists; no replay"
            );
        }
    } else if path.join("RUNTIME_BINDING.json").try_exists()? {
        let value: Value =
            serde_json::from_slice(&read_bound(&root, "RUNTIME_BINDING.json", 16384, true)?.0)?;
        ensure!(
            value["task_id"] == task.task_id
                && value["request_id"] == task.request_id
                && value["project_key"] == task.project_key
                && value["ticket_id"].as_str() == task.background_ticket_id.as_deref(),
            "metadata runtime ticket differs"
        );
    }
    ensure!(
        task.command == canonical(&task.workdir, expected)
            && task.result_artifact_path.as_deref().map(Path::new)
                == Some(path.join("result.json").as_path()),
        "metadata command/result path differs"
    );
    ensure!(
        process::filesystem_file_identity(&process::open_bounded_root(path)?.metadata()?)
            == Some(identity),
        "metadata root replaced"
    );
    let mut contract = json!({"schema":"offdesk_metadata_execution_contract.v1", "task_id":task.task_id,
        "request_id":task.request_id,"project_key":task.project_key,"root":task.workdir,
        "executor_kind":"deterministic_local_no_model", "provider_calls":0,
        "selection_sha256":hash,"controller_sha256":digest(DISPATCH),"source_hashes":cfg.input_files,
        "read_policy_sha256":cfg.read_policy_sha256,
        "deadline": DateTime::from_timestamp_millis((cfg.stops_at * 1000.0) as i64).context("invalid metadata deadline")?,
        "worker_deadline": DateTime::from_timestamp_millis(((cfg.stops_at - cfg.closeout_seconds as f64) * 1000.0) as i64).context("invalid metadata worker deadline")?,
        "total_seconds":cfg.total_seconds,"max_child_calls":cfg.rows.div_ceil(cfg.batch_rows)+2,
        "command_timeout_seconds":cfg.child_seconds,"closeout_reserve_seconds":cfg.closeout_seconds,
        "max_controller_worker_starts":1,"automatic_controller_retries":0,
        "checkpoint":"committed_explicit_batches_within_one_attempt_no_dispatcher_restart",
        "runtime_binding":"dispatcher_environment_before_spawn",
        "identity_grammar":"ascii_alphanumeric_underscore_hyphen_dot_1_to_100_not_dot_or_dotdot",
        "timing_anchor":"controller_run_entry_before_initial_inspection",
        "publication_observation":"required_external_sha256_and_original_window_boot",
        "child_timing":"parent_observed_through_process_exit",
        "required_isolation":"canonical_metadata_read_policy",
        "failure_action":"preserve_failure_evidence_and_stop","uncertain_call_action":"hold_without_replay",
        "task_cost_or_token_ceiling":null,
        "technical_steps":["validate_selection_and_packet", "validate_admitted_ticket", "reserve_once", "initialize_and_explicit_bounded_batches", "verify_completion_and_publish_pending_review"],
        "result_artifact_path":task.result_artifact_path,"return_disposition":"pending_ondesk_review_no_owning_task_acceptance",
        "worker_qualification_complete":false});
    contract["contract_sha256"] = json!(digest(&serde_json::to_vec(&contract)?));
    Ok(Some(
        json!({"schema":"offdesk_metadata_invocation_review.v1","adapter":"deterministic_metadata",
        "task_id":task.task_id,"status":"verified_metadata_adapter_rechecks_source_at_entry", "worker_execution_contract":contract,"worker_qualification_complete":false}),
    ))
}

#[cfg(test)]
mod identity_tests {
    use super::valid_identity;

    #[test]
    fn metadata_identity_accepts_dotted_owning_task_ids_with_the_entry_grammar() {
        for value in [
            "C6.3",
            "TODO-2026102107",
            "request_1",
            "a.b-c_d",
            &"x".repeat(100),
        ] {
            assert!(valid_identity(value), "{value}");
        }
        for value in [
            "",
            ".",
            "..",
            "has space",
            "slash/id",
            "한글",
            &"x".repeat(101),
        ] {
            assert!(!valid_identity(value), "{value}");
        }
    }
}
