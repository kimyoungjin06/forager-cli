//! Exact copied-input Codex adapter binding, independent of model qualification.
use super::native_worker_invocation::{quote, read_bound};
use crate::{
    offdesk::{BackgroundRunnerKind, OffdeskShiftWindowV1, OffdeskTask},
    process,
};
use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Component, Path},
};

pub const PREFIX: &str = "codex-review-invocation-sha256:";
const ADAPTER: &[u8] = include_bytes!("../../../scripts/offdesk_codex_review.py");
const USAGE: &[u8] = include_bytes!("../../../scripts/offdesk_usage_guard.py");
const EVIDENCE: &[u8] = include_bytes!("../../../scripts/offdesk_evidence_contract.py");
const BOOTSTRAP: &str = include_str!("../../../scripts/native_worker_bootstrap.py");
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(crate) fn unique_map<'de, D, T>(
    deserializer: D,
) -> std::result::Result<BTreeMap<String, T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Map<T>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>> serde::de::Visitor<'de> for Map<T> {
        type Value = BTreeMap<String, T>;
        fn expecting(&self, formatter: &mut std::fmt::Formatter) -> std::fmt::Result {
            formatter.write_str("unique object keys")
        }
        fn visit_map<A: serde::de::MapAccess<'de>>(
            self,
            mut access: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            let mut result = BTreeMap::new();
            while let Some((key, value)) = access.next_entry()? {
                if result.insert(key, value).is_some() {
                    return Err(serde::de::Error::custom("duplicate JSON key"));
                }
            }
            Ok(result)
        }
    }
    deserializer.deserialize_map(Map(std::marker::PhantomData))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Source {
    path: String,
    sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    schema: String,
    model: String,
    task_id: String,
    request_id: String,
    project_key: String,
    operator_runtime_approved: bool,
    root_device: u64,
    root_inode: u64,
    #[serde(deserialize_with = "unique_map")]
    runtime_files: BTreeMap<String, String>,
    codex: String,
    #[serde(deserialize_with = "unique_map")]
    sources: BTreeMap<String, Source>,
    #[serde(deserialize_with = "unique_map")]
    artifacts: BTreeMap<String, String>,
    starts_at: f64,
    stops_at: f64,
    worker_seconds: u64,
    closeout_seconds: u64,
    weekly: Value,
    auth_sha256: String,
}
fn relative(path: &str) -> Result<()> {
    ensure!(
        !path.is_empty()
            && Path::new(path)
                .components()
                .all(|c| matches!(c, Component::Normal(_)))
            && !path
                .split('/')
                .any(|p| p.is_empty() || p == "." || p == ".."),
        "unsafe Codex review input path"
    );
    Ok(())
}
pub fn canonical(root: &str, expected: &str) -> String {
    [
        "/usr/bin/python3",
        "-I",
        "-S",
        "-c",
        BOOTSTRAP,
        &format!("{root}/.runtime/offdesk_codex_review.py"),
        &digest(ADAPTER),
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
pub(crate) fn execution_rules() -> Value {
    json!({"schema":"offdesk_codex_execution_rules.v1",
        "technical_steps":["validate_bound_selection_and_inputs","validate_dispatched_ticket","recheck_weekly_auth_and_read_boundary","reserve_one_attempt","run_one_tool_free_codex_turn","validate_events_and_literal_quotes","publish_pending_review_result"],
        "max_controller_worker_starts":1,"automatic_controller_retries":0,
        "checkpoint":"unsupported_no_automatic_resume","failure_action":"preserve_failure_evidence_and_stop",
        "uncertain_call_action":"hold_without_replay_or_provider_switch",
        "return_disposition":"pending_ondesk_review_no_owning_task_acceptance",
        "native_internal_http_retries":"not_controlled_by_this_contract","worker_qualification_complete":false,
        "required_isolation":"canonical_shift_read_policy","runtime_binding":"dispatcher_environment_before_spawn"})
}
pub fn inspect(
    task: &OffdeskTask,
    window: Option<&OffdeskShiftWindowV1>,
    now: DateTime<Utc>,
) -> Result<Option<Value>> {
    let refs = task
        .artifact_refs
        .iter()
        .filter(|a| a.artifact_id.starts_with("codex-review-invocation"))
        .collect::<Vec<_>>();
    ensure!(refs.len() <= 1, "multiple Codex review invocation bindings");
    let Some(binding) = refs.first() else {
        return Ok(None);
    };
    ensure!(
        cfg!(target_os = "linux") && task.runner_kind == BackgroundRunnerKind::LocalBackground,
        "Codex review requires Linux local-background"
    );
    let expected = binding
        .artifact_id
        .strip_prefix(PREFIX)
        .context("invalid Codex review binding prefix")?;
    let root_path = Path::new(&task.workdir);
    ensure!(
        root_path.is_absolute() && root_path.canonicalize()? == root_path,
        "Codex root must be canonical"
    );
    ensure!(
        binding.path.as_deref().map(Path::new)
            == Some(root_path.join("REVIEW_SELECTION.json").as_path()),
        "Codex selection path differs"
    );
    let root = process::open_bounded_root(root_path)?;
    let identity = process::filesystem_file_identity(&root.metadata()?)
        .context("Codex root identity unavailable")?;
    let (raw, hash) = read_bound(&root, "REVIEW_SELECTION.json", 131072, true)?;
    ensure!(hash == expected, "Codex selection hash changed");
    let cfg: Selection = serde_json::from_slice(&raw)?;
    ensure!(
        matches!(
            cfg.schema.as_str(),
            "offdesk_codex_readonly_review.v1"
                | "offdesk_codex_readonly_review.v2"
                | "offdesk_codex_readonly_review.v3"
        ) && cfg.model == "gpt-6.1-sol"
            && cfg.operator_runtime_approved
            && cfg.worker_seconds == 600
            && cfg.closeout_seconds == 300,
        "unsupported or unapproved Codex selection"
    );
    ensure!(
        identity.0 == cfg.root_device && identity.1 == cfg.root_inode,
        "Codex root identity changed"
    );
    ensure!(
        cfg.task_id == task.task_id
            && cfg.request_id == task.request_id
            && cfg.project_key == task.project_key
            && task.provider_id.as_deref() == Some("gpt")
            && task.model.as_deref() == Some(cfg.model.as_str()),
        "Codex task/request/project/provider/model differs"
    );
    let admitted = task.attempt_count == 1 && task.background_ticket_id.is_some();
    ensure!(
        cfg.starts_at.is_finite()
            && cfg.stops_at.is_finite()
            && cfg.starts_at < cfg.stops_at
            && cfg.stops_at - cfg.starts_at <= 900.0
            && (admitted || cfg.stops_at > now.timestamp() as f64),
        "Codex window expired or invalid"
    );
    if let Some(window) = window {
        ensure!(
            cfg.starts_at >= window.starts_at.timestamp() as f64
                && cfg.stops_at <= window.stops_at.timestamp() as f64,
            "Codex window exceeds shift"
        );
    }
    ensure!(
        (1..=128).contains(&cfg.runtime_files.len())
            && cfg.codex.starts_with(".runtime/")
            && cfg.runtime_files.contains_key(&cfg.codex)
            && cfg.runtime_files.get(".runtime/offdesk_codex_review.py") == Some(&digest(ADAPTER))
            && cfg.runtime_files.get(".runtime/offdesk_usage_guard.py") == Some(&digest(USAGE)),
        "Codex runtime differs from supported adapter"
    );
    if cfg.schema == "offdesk_codex_readonly_review.v3" {
        ensure!(
            cfg.runtime_files
                .get(".runtime/offdesk_evidence_contract.py")
                == Some(&digest(EVIDENCE)),
            "Codex evidence helper differs from compiled source"
        );
    }
    for (path, expected) in &cfg.runtime_files {
        relative(path)?;
        ensure!(
            path.starts_with(".runtime/"),
            "Codex runtime outside selected subtree"
        );
        ensure!(
            read_bound(&root, path, 512 * 1024 * 1024, false)?.1 == *expected,
            "Codex runtime input hash changed"
        );
    }
    ensure!(
        (1..=32).contains(&cfg.sources.len()),
        "Codex source inventory missing"
    );
    let mut paths = std::collections::BTreeSet::new();
    for (key, source) in &cfg.sources {
        relative(&source.path)?;
        ensure!(
            !key.is_empty()
                && key.len() <= 40
                && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
                && source.path.starts_with("input/")
                && paths.insert(&source.path),
            "invalid or duplicate Codex source"
        );
        ensure!(
            read_bound(&root, &source.path, 2 * 1024 * 1024, false)?.1 == source.sha256,
            "Codex source changed"
        );
    }
    let expected_artifacts = if cfg.schema == "offdesk_codex_readonly_review.v3" {
        vec![
            "EVIDENCE_CATALOG.json",
            "INPUT_PACKET.json",
            "MODEL_SCHEMA.json",
            "PROMPT.txt",
        ]
    } else {
        vec!["INPUT_PACKET.json", "MODEL_SCHEMA.json", "PROMPT.txt"]
    };
    ensure!(
        cfg.artifacts
            .keys()
            .map(String::as_str)
            .eq(expected_artifacts),
        "Codex artifact set differs"
    );
    for (path, expected) in &cfg.artifacts {
        ensure!(
            read_bound(
                &root,
                path,
                if path == "PROMPT.txt" { 4096 } else { 196608 },
                false
            )?
            .1 == *expected,
            "Codex review artifact changed"
        );
    }
    ensure!(
        cfg.weekly["module"] == ".runtime/offdesk_usage_guard.py" && cfg.auth_sha256.len() == 64,
        "Codex weekly/auth binding missing"
    );
    for name in ["state", "sample"] {
        relative(
            cfg.weekly[name]
                .as_str()
                .context("Codex weekly input missing")?,
        )?;
    }
    if !admitted {
        for name in [
            "RUNTIME_BINDING.json",
            "ATTEMPT.json",
            "INVOCATION.json",
            "NATIVE_EVENTS.jsonl",
            "MODEL_REPORT.json",
            "HOST_QUOTED_REPORT.json",
            "BOUND_REVIEW.json",
            "result.json",
            "FAILURE.json",
        ] {
            for path in [name.to_owned(), format!("{name}.partial")] {
                ensure!(
                    root_path
                        .join(path)
                        .symlink_metadata()
                        .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
                    "Codex attempt/output already reserved; no replay"
                );
            }
        }
    } else if root_path
        .join("RUNTIME_BINDING.json")
        .symlink_metadata()
        .is_ok()
    {
        let (raw, _) = read_bound(&root, "RUNTIME_BINDING.json", 16384, true)?;
        let binding: Value = serde_json::from_slice(&raw)?;
        ensure!(
            binding["task_id"] == task.task_id
                && binding["request_id"] == task.request_id
                && binding["ticket_id"].as_str() == task.background_ticket_id.as_deref(),
            "Codex runtime ticket differs"
        );
    }
    ensure!(
        task.command == canonical(&task.workdir, expected),
        "Codex command is not the supported bootstrap"
    );
    ensure!(
        task.result_artifact_path.as_deref().map(Path::new)
            == Some(root_path.join("result.json").as_path()),
        "Codex result path differs"
    );
    ensure!(
        process::filesystem_file_identity(&process::open_bounded_root(root_path)?.metadata()?)
            == Some(identity),
        "Codex root replaced"
    );
    let mut contract = execution_rules();
    contract["selection_schema"] = json!(cfg.schema);
    contract["model_reference_format"] = json!(match cfg.schema.as_str() {
        "offdesk_codex_readonly_review.v3" => "offdesk_codex_evidence_report.v3",
        "offdesk_codex_readonly_review.v2" => "offdesk_codex_selector_report.v2",
        _ => "model_literal_quote.v1",
    });
    let object = contract.as_object_mut().unwrap();
    for (key, value) in [
        ("schema", json!("offdesk_codex_execution_contract.v1")),
        ("task_id", json!(task.task_id)),
        ("request_id", json!(task.request_id)),
        ("project_key", json!(task.project_key)),
        ("root", json!(task.workdir)),
        ("selection_sha256", json!(hash)),
        ("controller_sha256", json!(digest(ADAPTER))),
        (
            "deadline",
            json!(DateTime::from_timestamp(cfg.stops_at as i64, 0)
                .context("invalid Codex deadline")?),
        ),
        (
            "worker_deadline",
            json!(
                DateTime::from_timestamp(cfg.stops_at as i64 - cfg.closeout_seconds as i64, 0)
                    .context("invalid worker deadline")?
            ),
        ),
        ("command_timeout_seconds", json!(cfg.worker_seconds)),
        ("closeout_reserve_seconds", json!(cfg.closeout_seconds)),
        (
            "source_hashes",
            json!(cfg
                .sources
                .iter()
                .map(|(k, s)| (k, &s.sha256))
                .collect::<BTreeMap<_, _>>()),
        ),
        ("result_artifact_path", json!(task.result_artifact_path)),
        ("task_cost_or_token_ceiling", Value::Null),
    ] {
        object.insert(key.into(), value);
    }
    contract["contract_sha256"] = json!(digest(&serde_json::to_vec(&contract)?));
    Ok(Some(
        json!({"schema":"offdesk_codex_invocation_review.v1","adapter":"codex_tool_free_review","task_id":task.task_id,
        "status":"verified_codex_review_adapter_rechecks_source_at_entry","worker_execution_contract":contract,"worker_qualification_complete":false}),
    ))
}
