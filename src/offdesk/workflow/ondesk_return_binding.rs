//! Explicit read-only provenance between an execution copy and its owning plan.
use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};

use super::{
    codex_review_invocation::unique_map,
    ondesk_plan, ondesk_plan_store,
    ondesk_return::{MorningReturnInput, PlanJournal},
    ondesk_review::{read_shift_review, ShiftReviewReference},
    ondesk_task_binding, review_link,
};
use crate::offdesk::{
    offdesk_shift_manifest_sha256, runtime_occupancy, shift_runtime::ProjectRootBoundary,
    OffdeskShiftStore, OffdeskTaskStatus,
};

const SCHEMA: &str = "ondesk_canonical_return_binding.v1";
const EFFECT: &str = "read_only_pre_agreement_no_plan_write_or_task_acceptance";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactPin {
    pub path: PathBuf,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RootPin {
    pub path: PathBuf,
    pub identity_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskMapping {
    pub task_id: String,
    pub request_id: String,
    pub ticket_id: String,
    pub runtime_sha256: String,
    pub execution_root: RootPin,
    pub execution_plan: ArtifactPin,
    pub source_bindings: ArtifactPin,
    pub source_artifacts: Vec<ArtifactPin>,
    pub result: ArtifactPin,
    pub result_evidence: Vec<ArtifactPin>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanonicalReturnBinding {
    pub schema: String,
    pub effect: String,
    pub recorded_by: String,
    pub decision_ref: String,
    pub profile_root: RootPin,
    pub shift_review: ShiftReviewReference,
    pub shift_record_sha256: String,
    pub project_key: String,
    pub canonical_root: RootPin,
    pub owning_plan: ArtifactPin,
    pub owning_journal: Option<ArtifactPin>,
    pub tasks: Vec<TaskMapping>,
}

#[derive(Debug, Clone, Serialize)]
pub struct VerifiedReturnBinding {
    pub reference: ArtifactPin,
    pub binding: CanonicalReturnBinding,
    pub provenance_status: &'static str,
}

#[derive(Deserialize)]
struct OriginRow {
    original_path: PathBuf,
    original_sha256: String,
    copied_path: PathBuf,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn root_pin(boundary: &ProjectRootBoundary) -> RootPin {
    RootPin {
        path: boundary.path().into(),
        identity_sha256: boundary.identity_sha256.clone(),
    }
}

fn read_relative(
    root: &ProjectRootBoundary,
    path: &Path,
    limit: usize,
) -> Result<(ArtifactPin, Vec<u8>)> {
    let relative = if path.is_absolute() {
        path.strip_prefix(root.path())
            .context("mapped artifact is outside its bound root")?
    } else {
        path
    };
    let bytes = ondesk_plan_store::read(&root.descriptor()?, relative, limit)?;
    Ok((
        ArtifactPin {
            path: root.path().join(relative),
            sha256: digest(&bytes),
        },
        bytes,
    ))
}

pub fn read_binding(reference: &ArtifactPin) -> Result<CanonicalReturnBinding> {
    ensure!(
        reference.path.is_absolute(),
        "return binding path must be absolute"
    );
    let parent = reference
        .path
        .parent()
        .context("return binding parent missing")?;
    let boundary = ProjectRootBoundary::open(parent)?;
    let (_, bytes) = read_relative(&boundary, &reference.path, ondesk_plan::MAX_PLAN_BYTES)?;
    ensure!(
        digest(&bytes) == reference.sha256,
        "return binding artifact hash changed"
    );
    let binding = serde_json::from_slice(&bytes)?;
    boundary.ensure_current()?;
    Ok(binding)
}

fn observe(
    input: &MorningReturnInput<'_>,
    recorded_by: &str,
    decision_ref: &str,
) -> Result<CanonicalReturnBinding> {
    ensure!(
        !recorded_by.trim().is_empty()
            && recorded_by.len() <= 256
            && !decision_ref.trim().is_empty()
            && decision_ref.len() <= 4096,
        "return binding needs bounded operator attribution"
    );
    ensure!(
        input.workdir.is_absolute() && input.owning_plan.is_absolute(),
        "absolute canonical root and owning plan required"
    );
    let canonical = ProjectRootBoundary::open(input.workdir)?;
    let (owning_plan, plan_bytes) =
        read_relative(&canonical, input.owning_plan, ondesk_plan::MAX_PLAN_BYTES)?;
    let current = ondesk_plan::inspect(std::str::from_utf8(&plan_bytes)?, input.project_key)?;
    let stem = input
        .owning_plan
        .file_name()
        .and_then(|v| v.to_str())
        .context("owning plan filename missing")?;
    let journal_path = input
        .owning_plan
        .with_file_name(format!(".{stem}.forager-plan-journal.json"));
    let owning_journal = match read_relative(&canonical, &journal_path, ondesk_plan::MAX_PLAN_BYTES)
    {
        Ok((pin, bytes)) => {
            let journal: PlanJournal = serde_json::from_slice(&bytes)?;
            ensure!(
                journal.schema == "ondesk_plan_change_journal.v1"
                    && journal.source_path == input.owning_plan
                    && journal.entries.len() <= 512,
                "mapped owning journal identity or bounds differ"
            );
            let mut ids = std::collections::HashSet::new();
            for entry in journal.entries {
                ondesk_plan_store::validate_request(&entry.request)?;
                ensure!(
                    entry.committed
                        && entry.request.source_path == input.owning_plan
                        && ids.insert(entry.request.request_id),
                    "mapped owning journal has pending, wrong or duplicate writes"
                );
            }
            Some(pin)
        }
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == std::io::ErrorKind::NotFound) =>
        {
            None
        }
        Err(error) => return Err(error),
    };
    let profile = ProjectRootBoundary::open(&crate::session::profile_dir_path(input.profile)?)?;
    let (_, queue_bytes) = read_relative(
        &profile,
        Path::new("offdesk_tasks.json"),
        ondesk_plan::MAX_PLAN_BYTES,
    )?;
    let queue =
        crate::migrations::v005_task_review_links::decode(std::str::from_utf8(&queue_bytes)?)?;
    let report_bytes = read_shift_review(&input.reference)?;
    let report: serde_json::Value = serde_json::from_slice(&report_bytes)?;
    let store = OffdeskShiftStore::read_only(input.profile)?;
    let record = store
        .record(&input.reference.shift_id)?
        .context("mapped shift record missing")?;
    ensure!(
        offdesk_shift_manifest_sha256(&record.manifest)? == input.reference.manifest_sha256,
        "mapped shift manifest differs"
    );
    let selected: Vec<_> = record
        .manifest
        .task_bindings
        .iter()
        .filter(|v| v.project_key == input.project_key)
        .collect();
    ensure!(
        !selected.is_empty() && selected.len() <= 32,
        "mapped project task count outside bounds"
    );
    let report_tasks = report["tasks"]
        .as_array()
        .context("mapped report tasks missing")?;
    ensure!(
        report_tasks
            .iter()
            .filter(|v| v["project_key"] == input.project_key)
            .count()
            == selected.len(),
        "mapped report task set differs"
    );
    let mut tasks = Vec::new();
    for selected in selected {
        let matches: Vec<_> = queue
            .iter()
            .filter(|v| v.task_id == selected.task_id)
            .collect();
        ensure!(
            matches.len() == 1,
            "mapped runtime task missing or duplicated"
        );
        let task = matches[0];
        ensure!(
            task.status == OffdeskTaskStatus::Completed
                && runtime_occupancy::released(profile.path(), task)?,
            "mapped result requires completed task and verified runtime release"
        );
        let execution = ProjectRootBoundary::open(Path::new(&task.workdir))?;
        ensure!(
            execution.path() != canonical.path()
                && execution.identity_sha256 != canonical.identity_sha256,
            "canonical return mapping requires a distinct execution root"
        );
        selected.validate_identity_against_task(task, &execution.identity_sha256)?;
        let snapshot = selected
            .ondesk_source_snapshot
            .as_ref()
            .context("mapped historical source missing")?;
        snapshot.task(input.project_key, &task.task_id)?;
        ensure!(
            ondesk_task_binding::capture_task_source(task)?.as_ref() == Some(snapshot),
            "mapped copied plan changed"
        );
        ensure!(
            ondesk_plan::inspect(&snapshot.source, input.project_key)?.plan == current.plan,
            "mapped structured task plan changed; explicit reconciliation required"
        );
        let sources: Vec<_> = task
            .artifact_refs
            .iter()
            .filter(|v| {
                v.artifact_id
                    .starts_with(ondesk_task_binding::BINDING_PREFIX)
            })
            .collect();
        ensure!(
            sources.len() == 1,
            "mapped task needs one copied owning plan"
        );
        let source_path = Path::new(
            sources[0]
                .path
                .as_deref()
                .context("mapped source path missing")?,
        );
        let (execution_plan, _) =
            read_relative(&execution, source_path, ondesk_plan::MAX_PLAN_BYTES)?;
        ensure!(
            execution_plan.sha256 == snapshot.source_sha256,
            "mapped execution source hash differs"
        );
        let (source_bindings, origins_bytes) = read_relative(
            &execution,
            Path::new("SOURCE_BINDINGS.json"),
            ondesk_plan::MAX_PLAN_BYTES,
        )?;
        let mut decoder = serde_json::Deserializer::from_slice(&origins_bytes);
        let origins: BTreeMap<String, OriginRow> = unique_map(&mut decoder)?;
        decoder.end()?;
        ensure!(
            !origins.is_empty() && origins.len() <= 128,
            "mapped source-origin record exceeds bounds"
        );
        let mut source_artifacts = Vec::new();
        let mut source_bytes = 0;
        for origin in origins.values() {
            let (pin, bytes) = read_relative(&execution, &origin.copied_path, 2 * 1024 * 1024)?;
            source_bytes += bytes.len();
            ensure!(
                source_bytes <= 32 * 1024 * 1024 && pin.sha256 == origin.original_sha256,
                "mapped copied source changed or exceeds bound"
            );
            source_artifacts.push(pin);
        }
        let origins: Vec<_> = origins
            .values()
            .filter(|v| execution.path().join(&v.copied_path) == execution_plan.path)
            .collect();
        ensure!(
            origins.len() == 1
                && origins[0].original_path == input.owning_plan
                && origins[0].original_sha256 == snapshot.source_sha256,
            "copied source does not declare this original owning plan"
        );
        let ticket = task
            .background_ticket_id
            .as_ref()
            .context("mapped task ticket missing")?;
        let (result, bytes) = read_relative(
            &execution,
            Path::new(
                task.result_artifact_path
                    .as_deref()
                    .context("mapped task result path missing")?,
            ),
            ondesk_plan::MAX_PLAN_BYTES,
        )?;
        let result_value: serde_json::Value = serde_json::from_slice(&bytes)?;
        ensure!(
            result_value["project_key"] == input.project_key
                && result_value["task_id"] == task.task_id
                && result_value["request_id"] == task.request_id
                && result_value["ticket_id"] == *ticket
                && result_value["status"] == "pending_user_review"
                && result_value["exit_code"] == 0
                && result_value["plan_update_authorized"] == false,
            "mapped result identity or authority differs"
        );
        ensure!(
            execution
                .path()
                .join("FAILURE.json")
                .symlink_metadata()
                .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound),
            "mapped result has failure or uncertain failure evidence"
        );
        let reports: Vec<_> = report_tasks
            .iter()
            .filter(|v| v["project_key"] == input.project_key && v["task_id"] == task.task_id)
            .collect();
        ensure!(
            reports.len() == 1,
            "mapped report task missing or duplicated"
        );
        let closeout = &reports[0]["closeout_report"];
        ensure!(
            closeout["schema"] == "offdesk_task_closeout_report.v1"
                && closeout["historical_scope_status"] == "preserved_source_hash_verified"
                && closeout["historical_source_sha256"] == snapshot.source_sha256
                && closeout["review_disposition"] == "pending_user_review"
                && closeout["plan_update_authorized"] == false,
            "mapped report historical scope or authority differs"
        );
        ensure!(
            closeout["result_observation"]["content_sha256"] == result.sha256,
            "mapped result does not match saved closeout content hash"
        );
        let evidence = result_value["evidence"]
            .as_array()
            .context("mapped result evidence missing")?;
        ensure!(
            !evidence.is_empty() && evidence.len() <= 32,
            "mapped result evidence count outside bounds"
        );
        let mut total = 0;
        let mut result_evidence = Vec::new();
        for entry in evidence {
            let path = Path::new(
                entry["path"]
                    .as_str()
                    .context("mapped evidence path missing")?,
            );
            let (pin, bytes) = read_relative(&execution, path, 16 * 1024 * 1024)?;
            total += bytes.len();
            ensure!(
                total <= 32 * 1024 * 1024 && entry["sha256"] == pin.sha256,
                "mapped result evidence changed or exceeds bound"
            );
            ensure!(
                !result_evidence
                    .iter()
                    .any(|v: &ArtifactPin| v.path == pin.path),
                "duplicate mapped result evidence"
            );
            result_evidence.push(pin);
            if path
                .file_name()
                .is_some_and(|name| name == "BOUND_REVIEW.json")
            {
                let bound: serde_json::Value = serde_json::from_slice(&bytes)?;
                if bound["schema"] == "offdesk_codex_bound_review.v3" {
                    ensure!(
                        bound["semantic_acceptance"] == false,
                        "bound review grants acceptance"
                    );
                    let (selection_pin, selection_bytes) = read_relative(
                        &execution,
                        Path::new("REVIEW_SELECTION.json"),
                        ondesk_plan::MAX_PLAN_BYTES,
                    )?;
                    ensure!(
                        result_value["selection_sha256"] == selection_pin.sha256
                            && task.artifact_refs.iter().any(|v| v.artifact_id
                                == format!(
                                    "codex-review-invocation-sha256:{}",
                                    selection_pin.sha256
                                )),
                        "mapped review selection is not bound to the saved task and result"
                    );
                    let selection: serde_json::Value = serde_json::from_slice(&selection_bytes)?;
                    let sources = selection["sources"]
                        .as_object()
                        .context("mapped review sources missing")?;
                    let source_hashes = bound["source_sha256"]
                        .as_object()
                        .context("bound review source hashes missing")?;
                    ensure!(
                        !sources.is_empty()
                            && sources.len() <= 32
                            && source_hashes.len() == sources.len(),
                        "mapped review source inventory differs"
                    );
                    for (key, source) in sources {
                        let source_path = execution.path().join(
                            source["path"]
                                .as_str()
                                .context("mapped review source path missing")?,
                        );
                        ensure!(
                            source_hashes.get(key) == Some(&source["sha256"])
                                && source_artifacts
                                    .iter()
                                    .any(|pin| pin.path == source_path
                                        && source["sha256"] == pin.sha256),
                            "source-origin record omits or changes a selected review source"
                        );
                    }
                    result_evidence.push(selection_pin);
                    for (name, expected) in [
                        ("MODEL_REPORT.json", &bound["raw_selector_report_sha256"]),
                        (
                            "HOST_QUOTED_REPORT.json",
                            &bound["quote_provenance"]["quoted_report_sha256"],
                        ),
                        ("EVIDENCE_CATALOG.json", &bound["evidence_catalog_sha256"]),
                        (
                            ".runtime/offdesk_evidence_contract.py",
                            &bound["evidence_helper_sha256"],
                        ),
                    ] {
                        let (pin, bytes) =
                            read_relative(&execution, Path::new(name), 16 * 1024 * 1024)?;
                        total += bytes.len();
                        ensure!(
                            total <= 32 * 1024 * 1024 && *expected == pin.sha256,
                            "mapped bound-review provenance changed"
                        );
                        result_evidence.push(pin);
                    }
                }
            }
        }
        execution.ensure_current()?;
        tasks.push(TaskMapping {
            task_id: task.task_id.clone(),
            request_id: task.request_id.clone(),
            ticket_id: ticket.clone(),
            runtime_sha256: review_link::runtime_sha256(task)?,
            execution_root: root_pin(&execution),
            execution_plan,
            source_bindings,
            source_artifacts,
            result,
            result_evidence,
        });
    }
    canonical.ensure_current()?;
    profile.ensure_current()?;
    Ok(CanonicalReturnBinding {
        schema: SCHEMA.into(),
        effect: EFFECT.into(),
        recorded_by: recorded_by.into(),
        decision_ref: decision_ref.into(),
        profile_root: root_pin(&profile),
        shift_review: input.reference.clone(),
        shift_record_sha256: digest(&serde_json::to_vec(&record)?),
        project_key: input.project_key.into(),
        canonical_root: root_pin(&canonical),
        owning_plan,
        owning_journal,
        tasks,
    })
}

pub fn propose(
    input: &MorningReturnInput<'_>,
    recorded_by: &str,
    decision_ref: &str,
) -> Result<CanonicalReturnBinding> {
    let binding = observe(input, recorded_by, decision_ref)?;
    ensure!(
        observe(input, recorded_by, decision_ref)? == binding,
        "return binding inputs changed during proposal"
    );
    Ok(binding)
}

pub fn verify(
    input: &MorningReturnInput<'_>,
    reference: &ArtifactPin,
) -> Result<VerifiedReturnBinding> {
    let binding = read_binding(reference)?;
    ensure!(
        binding.schema == SCHEMA && binding.effect == EFFECT,
        "unsupported canonical return binding or authority"
    );
    ensure!(observe(input, &binding.recorded_by, &binding.decision_ref)? == binding,
        "canonical return binding is stale or mismatches current roots, plan, journal, task or result");
    Ok(VerifiedReturnBinding {
        reference: reference.clone(),
        binding,
        provenance_status: "operator_attributed_host_origin_record_not_producer_authentication",
    })
}
