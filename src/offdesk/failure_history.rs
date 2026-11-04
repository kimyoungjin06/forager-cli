//! Read-only evidence for retired failures, never acceptance or release authority.

use super::{BackgroundProbe, BackgroundRunnerPhase, OffdeskTask, OffdeskTaskStatus};
use anyhow::{ensure, Result};
use chrono::{DateTime, Utc};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Path, PathBuf},
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ClosedFailureHistory {
    pub state: &'static str,
    pub closeout_id: String,
    pub receipt_id: String,
    pub reviewed_at: DateTime<Utc>,
    pub ticket_id: String,
    pub review_sha256: String,
    pub plan_sha256: String,
    pub receipt_sha256: String,
    pub runtime_evidence: String,
    pub accepted_truth_authorized: bool,
    pub runtime_release_authorized: bool,
    pub automatic_resume: bool,
}

struct Record {
    path: PathBuf,
    value: Value,
    sha256: String,
}

#[derive(Default)]
pub(crate) struct RetirementIndex {
    records: Vec<Record>,
}

impl RetirementIndex {
    pub(crate) fn load(profile: &Path) -> Result<Self> {
        let directory = profile.join("offdesk_closeouts");
        if !directory.try_exists()? {
            return Ok(Self::default());
        }
        ensure!(
            !fs::symlink_metadata(&directory)?.file_type().is_symlink(),
            "linked closeouts"
        );
        let mut records = vec![];
        let mut directories = 0;
        let mut bytes = 0;
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            ensure!(
                !entry.file_type()?.is_symlink(),
                "linked closeout directory"
            );
            if !entry.file_type()?.is_dir() {
                continue;
            }
            directories += 1;
            ensure!(directories <= 256, "closeout scan exceeds bound");
            for entry in fs::read_dir(entry.path())? {
                let entry = entry?;
                let name = entry.file_name();
                let name = name.to_string_lossy();
                if !name.starts_with("closeout_review_") || !name.ends_with(".json") {
                    continue;
                }
                ensure!(records.len() < 512, "review scan exceeds bound");
                let (value, sha256, size) = read(profile, &entry.path())?;
                bytes += size;
                ensure!(bytes <= 4 * 1024 * 1024, "review bytes exceed bound");
                records.push(Record {
                    path: entry.path(),
                    value,
                    sha256,
                });
            }
        }
        Ok(Self { records })
    }

    pub(crate) fn closed(
        &self,
        profile: &Path,
        task: &OffdeskTask,
        probes: &[BackgroundProbe],
        now: DateTime<Utc>,
    ) -> Option<ClosedFailureHistory> {
        self.check(profile, task, probes, now).ok()
    }

    fn check(
        &self,
        profile: &Path,
        task: &OffdeskTask,
        probes: &[BackgroundProbe],
        now: DateTime<Utc>,
    ) -> Result<ClosedFailureHistory> {
        // Legacy plans do not bind attempt numbers. Support the exact first
        // cancelled attempt; a later/reused attempt must retain its review hold.
        ensure!(
            task.status == OffdeskTaskStatus::Cancelled && task.attempt_count == 1,
            "not a closed first attempt"
        );
        ensure!(
            !task
                .review_links
                .iter()
                .any(|link| link.preview.decision.disposition
                    == super::workflow::ondesk_plan::ReviewState::Accepted),
            "accepted review must remain distinct"
        );
        let ticket = task
            .background_ticket_id
            .as_deref()
            .ok_or_else(|| anyhow::anyhow!("ticket missing"))?;
        let matches: Vec<_> = probes.iter().filter(|p| p.ticket_id == ticket).collect();
        let [probe] = matches.as_slice() else {
            anyhow::bail!("ambiguous runtime ticket");
        };
        ensure!(
            probe.phase == BackgroundRunnerPhase::Failed
                && probe.runner_kind == task.runner_kind
                && probe.capability_id.as_deref() == Some(&task.capability_id),
            "not an exact failed probe"
        );
        let mut selected = None;
        let mut latest = None;
        for record in &self.records {
            let refs = record.value["applies_to_tasks"]
                .as_array()
                .ok_or_else(|| anyhow::anyhow!("review refs missing"))?;
            let matching: Vec<_> = refs
                .iter()
                .filter(|r| r["project_key"] == task.project_key && r["task_id"] == task.task_id)
                .collect();
            if matching.is_empty() {
                continue;
            }
            ensure!(
                matching.len() == 1 && matching[0]["request_id"] == task.request_id,
                "reused or ambiguous task"
            );
            let at = timestamp(&record.value["reviewed_at"])?;
            ensure!(at <= now, "future review");
            if latest.is_none_or(|time| at > time) {
                selected = Some(record);
                latest = Some(at);
            } else {
                ensure!(latest != Some(at), "ambiguous latest review");
            }
        }
        let record = selected.ok_or_else(|| anyhow::anyhow!("no retirement"))?;
        let reviewed_at = latest.unwrap();
        ensure!(reviewed_at >= task.updated_at, "stale retirement");
        let review = &record.value;
        let parent = record.path.parent().unwrap();
        let receipt = &review["closeout_receipt"];
        let task_ref = format!("{}:{}", task.project_key, task.task_id);
        ensure!(
            review["review_provider"] == "operator_closeout_retirement"
                && review["verdict"] == "revise"
                && receipt["verdict"] == "revise"
                && receipt["review_id"]
                    .as_str()
                    .is_some_and(|id| !id.trim().is_empty())
                && review["closeout_retirement"]["reviewer"] == review["reviewer"]
                && review["reviewer"]
                    .as_str()
                    .is_some_and(|name| !name.trim().is_empty())
                && review["closeout_retirement"]["reason"]
                    .as_str()
                    .is_some_and(|reason| !reason.trim().is_empty())
                && review["closeout_retirement"]["excluded_accepted_tasks"]
                    .as_array()
                    .is_some_and(|excluded| !excluded
                        .iter()
                        .any(|value| value.as_str() == Some(task_ref.as_str()))),
            "unsupported or contradictory retirement"
        );
        ensure!(
            review["profile"].as_str() == profile.file_name().and_then(|s| s.to_str())
                && review["artifact_dir"].as_str() == parent.to_str()
                && review["artifacts"]["review_record_json"].as_str() == record.path.to_str()
                && review["read_only_project_state"] == true
                && review["applies_file_operations"] == false,
            "retirement path or authority mismatch"
        );
        ensure!(
            receipt["schema"] == "closeout_receipt.v1"
                && receipt["acceptance_status"] == "retired_incomplete"
                && receipt["verification_status"] == "retired"
                && receipt["retention_review"] == "retired_incomplete"
                && receipt["stale_task_count"] == 0
                && receipt["wiki_promotion_state"] == "not_required"
                && review["closeout_id"] == receipt["closeout_id"]
                && review["review_id"] == receipt["review_id"]
                && timestamp(&receipt["reviewed_at"])? == reviewed_at
                && timestamp(&review["closeout_retirement"]["retired_at"])? == reviewed_at,
            "retirement receipt mismatch"
        );
        for key in [
            "open_decisions",
            "required_first_reads",
            "unsafe_operations",
            "missing_evidence",
        ] {
            ensure!(
                receipt[key].as_array().is_some_and(Vec::is_empty),
                "unresolved retirement"
            );
        }
        let plan_path = parent.join("closeout_plan.json");
        let receipt_path = Path::new(
            review["artifacts"]["closeout_receipt_json"]
                .as_str()
                .ok_or_else(|| anyhow::anyhow!("receipt path missing"))?,
        );
        ensure!(
            receipt_path.parent() == Some(parent)
                && review["artifacts"]["closeout_plan_json"].as_str() == plan_path.to_str(),
            "wrong artifact binding"
        );
        let (plan, plan_sha256, _) = read(profile, &plan_path)?;
        let (stored_receipt, receipt_sha256, _) = read(profile, receipt_path)?;
        ensure!(
            receipt["source_artifacts"]["review_record_json"].as_str() == record.path.to_str()
                && receipt["source_artifacts"]["closeout_plan_json"].as_str() == plan_path.to_str(),
            "receipt source binding changed"
        );
        ensure!(
            &stored_receipt == receipt
                && plan["closeout_id"] == review["closeout_id"]
                && timestamp(&plan["generated_at"])? >= task.updated_at
                && timestamp(&plan["generated_at"])? <= reviewed_at,
            "changed or stale closeout inputs"
        );
        let planned = plan["tasks"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("plan tasks missing"))?;
        let matched: Vec<_> = planned
            .iter()
            .filter(|t| t["project_key"] == task.project_key && t["task_id"] == task.task_id)
            .collect();
        ensure!(matched.len() == 1, "ambiguous plan task");
        let compact_shift = if let Some(hash) = matched[0].get("runtime_sha256") {
            ensure!(
                hash.as_str() == Some(super::workflow::review_link::runtime_sha256(task)?.as_str()),
                "retired task runtime binding changed"
            );
            None
        } else if matched[0].get("command").is_some() {
            let historical: OffdeskTask = serde_json::from_value(matched[0].clone())?;
            ensure!(
                super::offdesk_task_launch_spec_sha256(&historical)?
                    == super::offdesk_task_launch_spec_sha256(task)?,
                "retired task launch scope changed"
            );
            None
        } else {
            // CloseoutTask is a summary, not a serialized OffdeskTask. Recover
            // its missing launch scope only from an exact validated shift ticket.
            Some(compact_launch_scope(
                profile,
                task,
                matched[0],
                reviewed_at,
            )?)
        };
        let expected = serde_json::to_value(task)?;
        for key in [
            "project_key",
            "task_id",
            "request_id",
            "background_ticket_id",
            "runner_kind",
            "capability_id",
            "workdir",
            "status",
        ] {
            ensure!(
                matched[0][key] == expected[key],
                "plan task binding changed"
            );
        }
        let bg = plan["background_runs"]
            .as_array()
            .ok_or_else(|| anyhow::anyhow!("plan runtime missing"))?;
        let bg: Vec<_> = bg.iter().filter(|p| p["ticket_id"] == ticket).collect();
        ensure!(bg.len() == 1, "ambiguous plan runtime");
        let expected = serde_json::to_value(probe)?;
        for key in [
            "ticket_id",
            "project_key",
            "task_id",
            "request_id",
            "runner_kind",
            "working_dir",
            "phase",
        ] {
            ensure!(bg[0][key] == expected[key], "plan runtime changed");
        }
        // A denied/unavailable process observation is not proof of absence.
        ensure!(cfg!(target_os = "linux"), "unsupported process observation");
        fs::read_to_string("/proc/self/stat")?;
        let pid = probe
            .runtime_pid
            .ok_or_else(|| anyhow::anyhow!("supervisor pid missing"))?;
        match fs::read_to_string(format!("/proc/{pid}/stat")) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            _ => anyhow::bail!("supervisor alive or observation unavailable"),
        }
        let exit = probe
            .local_exit_receipt
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("exit receipt missing"))?;
        ensure!(exit.status.is_some(), "terminal exit status unknown");
        let mut current_exit = exit.clone();
        super::local_exit::refresh(Path::new(&task.workdir), &mut current_exit)?;
        ensure!(&current_exit == exit, "exit receipt content changed");
        let runtime = super::runtime_occupancy::inspect_failure_history(profile, task, probe)?;
        ensure!(
            runtime.release_verified && runtime.reason == "descendant_wait_receipt_verified",
            "runtime release not verified"
        );
        ensure!(
            read(profile, &record.path)?.1 == record.sha256,
            "retirement changed"
        );
        ensure!(
            read(profile, &plan_path)?.1 == plan_sha256
                && read(profile, receipt_path)?.1 == receipt_sha256,
            "retirement inputs changed during inspection"
        );
        if let Some(snapshot) = compact_shift {
            ensure!(
                super::OffdeskShiftStore::read_only_at_root(profile)?.snapshot()? == snapshot,
                "compact retirement shift evidence changed during inspection"
            );
        }
        let id = |key: &str| -> Result<String> {
            let id = receipt[key]
                .as_str()
                .filter(|s| !s.trim().is_empty())
                .ok_or_else(|| anyhow::anyhow!("receipt identity missing"))?;
            Ok(super::operator_safe_text(id))
        };
        Ok(ClosedFailureHistory {
            state: "retired_incomplete",
            closeout_id: id("closeout_id")?,
            receipt_id: id("receipt_id")?,
            reviewed_at,
            ticket_id: super::operator_safe_text(ticket),
            review_sha256: record.sha256.clone(),
            plan_sha256,
            receipt_sha256,
            runtime_evidence: runtime.reason.into(),
            accepted_truth_authorized: false,
            runtime_release_authorized: false,
            automatic_resume: false,
        })
    }
}

fn compact_launch_scope(
    profile: &Path,
    task: &OffdeskTask,
    summary: &Value,
    reviewed_at: DateTime<Utc>,
) -> Result<super::OffdeskShiftStoreSnapshotV1> {
    let object = summary
        .as_object()
        .ok_or_else(|| anyhow::anyhow!("compact task missing"))?;
    let allowed = [
        "review",
        "task_id",
        "request_id",
        "project_key",
        "status",
        "capability_id",
        "runner_kind",
        "workdir",
        "agent_mode",
        "background_ticket_id",
        "result_artifact_path",
        "log_artifact_path",
        "artifact_refs",
        "implementation_packet",
        "preview",
        "reason",
    ];
    ensure!(
        object.keys().all(|key| allowed.contains(&key.as_str())),
        "unsupported compact task summary"
    );
    for (key, expected) in [
        ("agent_mode", serde_json::to_value(task.agent_mode)?),
        (
            "result_artifact_path",
            serde_json::to_value(&task.result_artifact_path)?,
        ),
        (
            "log_artifact_path",
            serde_json::to_value(&task.log_artifact_path)?,
        ),
        ("artifact_refs", serde_json::to_value(&task.artifact_refs)?),
        (
            "implementation_packet",
            serde_json::to_value(&task.implementation_packet)?,
        ),
    ] {
        ensure!(
            summary[key] == expected,
            "compact task artifact or mode binding changed"
        );
    }
    let snapshot = super::OffdeskShiftStore::read_only_at_root(profile)?.snapshot()?;
    let matches: Vec<_> = snapshot
        .records
        .iter()
        .flat_map(|record| {
            record
                .task_launch_reservations
                .iter()
                .map(move |launch| (record, launch))
        })
        .filter(|(_, launch)| {
            launch.task_id == task.task_id
                && Some(launch.background_ticket_id.as_str())
                    == task.background_ticket_id.as_deref()
        })
        .collect();
    let [(record, launch)] = matches.as_slice() else {
        anyhow::bail!("compact retirement needs one exact shift launch");
    };
    let binding = record
        .manifest
        .task_bindings
        .iter()
        .find(|binding| {
            binding.task_id == task.task_id
                && binding.request_id == task.request_id
                && binding.project_key == task.project_key
        })
        .ok_or_else(|| anyhow::anyhow!("compact retirement shift task mismatch"))?;
    ensure!(
        launch.attempt_number == 1
            && launch.attempt_number == task.attempt_count
            && launch.reserved_at <= reviewed_at
            && binding.launch_spec_sha256 == super::offdesk_task_launch_spec_sha256(task)?,
        "compact retirement launch scope changed"
    );
    ensure!(
        record
            .task_launch_ticket_receipts
            .iter()
            .any(|receipt| receipt.launch_id == launch.launch_id
                && receipt.recorded_at <= reviewed_at)
            && record
                .task_outcome_receipts
                .iter()
                .any(|outcome| outcome.launch_id == launch.launch_id
                    && outcome.completed_at <= reviewed_at
                    && outcome.outcome == super::OffdeskShiftTaskOutcomeV1::RecoveryRequired
                    && outcome.terminal_phase == Some(BackgroundRunnerPhase::Failed)),
        "compact retirement needs exact failed ticket and outcome receipts"
    );
    Ok(snapshot)
}

fn timestamp(value: &Value) -> Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(
        value
            .as_str()
            .ok_or_else(|| anyhow::anyhow!("timestamp missing"))?,
    )?
    .with_timezone(&Utc))
}

fn read(root: &Path, path: &Path) -> Result<(Value, String, usize)> {
    let relative = path.strip_prefix(root)?;
    let directory = crate::process::open_bounded_root(root)?;
    let file = crate::process::open_bounded_regular_file(&directory, relative)?;
    let before = crate::process::FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        crate::process::filesystem_link_count(&file.metadata()?) == Some(1),
        "linked history file"
    );
    let mut reader = file.take(1024 * 1024 + 1);
    let mut bytes = vec![];
    reader.read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= 1024 * 1024, "history file exceeds bound");
    ensure!(
        before
            == crate::process::FilesystemObjectSnapshot::from_metadata(
                &reader.get_ref().metadata()?
            ),
        "history changed"
    );
    let reopened = crate::process::open_bounded_regular_file(&directory, relative)?;
    ensure!(
        before == crate::process::FilesystemObjectSnapshot::from_metadata(&reopened.metadata()?),
        "history replaced"
    );
    let current = crate::process::open_bounded_root(root)?;
    ensure!(
        crate::process::filesystem_file_identity(&current.metadata()?)
            == crate::process::filesystem_file_identity(&directory.metadata()?),
        "history root replaced"
    );
    Ok((
        serde_json::from_slice(&bytes)?,
        format!("{:x}", Sha256::digest(&bytes)),
        bytes.len(),
    ))
}

#[cfg(all(test, target_os = "linux"))]
pub(crate) mod tests {
    use super::*;
    use crate::offdesk::{shift::*, *};
    use chrono::Duration;
    use serde_json::json;

    pub(crate) struct Fixture {
        pub home: tempfile::TempDir,
        pub profile: PathBuf,
        pub task: OffdeskTask,
        pub probe: BackgroundProbe,
        pub review: PathBuf,
        pub receipt: PathBuf,
        pub plan: PathBuf,
    }

    impl Fixture {
        pub(crate) fn new(runner: BackgroundRunnerKind) -> Self {
            let home = tempfile::tempdir().unwrap();
            let profile = home.path().join(".config/forager/profiles/history-fixture");
            let work = home.path().join("work");
            fs::create_dir_all(&profile).unwrap();
            fs::create_dir(&work).unwrap();
            let (mut command, mut exit) =
                crate::offdesk::local_exit::command(&work, "sleep 0.1; exit 7").unwrap();
            let mut child = command.spawn().unwrap();
            let pid = child.id();
            let identity = crate::process::process_identity(pid).unwrap();
            assert!(!child.wait().unwrap().success());
            crate::offdesk::local_exit::refresh(&work, &mut exit).unwrap();
            assert_eq!(exit.status, Some(7));
            let at = Utc::now();
            let task: OffdeskTask = serde_json::from_value(json!({
                "task_id":"history-task","request_id":"history-request","project_key":"history-project",
                "status":"cancelled","capability_id":"dispatch.runtime","runner_kind":runner,
                "command":"exit 7","workdir":work,"background_ticket_id":"history-ticket","attempt_count":1,
                "created_at":at-Duration::seconds(30),"updated_at":at-Duration::seconds(1)
            })).unwrap();
            let mut probe = BackgroundProbe::new("history-ticket", runner);
            probe.project_key = Some(task.project_key.clone());
            probe.task_id = Some(task.task_id.clone());
            probe.request_id = Some(task.request_id.clone());
            probe.capability_id = Some(task.capability_id.clone());
            probe.working_dir = Some(task.workdir.clone());
            probe.phase = BackgroundRunnerPhase::Failed;
            probe.runtime_pid = Some(pid);
            probe.runtime_identity = Some(identity);
            probe.local_exit_receipt = Some(exit);
            let directory = profile.join("offdesk_closeouts/closed-run");
            fs::create_dir_all(&directory).unwrap();
            let review = directory.join("closeout_review_retired.json");
            let receipt = directory.join("closeout_receipt.json");
            let plan = directory.join("closeout_plan.json");
            let receipt_value = json!({"schema":"closeout_receipt.v1","receipt_id":"retired-receipt",
                "closeout_id":"history-closeout","review_id":"history-retirement","reviewed_at":at,
                "acceptance_status":"retired_incomplete","verification_status":"retired",
                "retention_review":"retired_incomplete","stale_task_count":0,"wiki_promotion_state":"not_required",
                "verdict":"revise","missing_evidence":[],
                "source_artifacts":{"review_record_json":review,"closeout_plan_json":plan},
                "open_decisions":[],"required_first_reads":[],"unsafe_operations":[]});
            put(&receipt, &receipt_value);
            put(
                &plan,
                &json!({"closeout_id":"history-closeout","generated_at":task.updated_at,
                "tasks":[task],"background_runs":[probe]}),
            );
            put(
                &review,
                &json!({"profile":"history-fixture","artifact_dir":directory,
                "review_id":"history-retirement","closeout_id":"history-closeout","reviewed_at":at,
                "verdict":"revise","review_provider":"operator_closeout_retirement","reviewer":"fixture-only", "read_only_project_state":true,"applies_file_operations":false,
                "applies_to_tasks":[{"project_key":task.project_key,"task_id":task.task_id,"request_id":task.request_id}],
                "closeout_retirement":{"retired_at":at,"reviewer":"fixture-only","reason":"failed fixture closed without acceptance","excluded_accepted_tasks":[]},"closeout_receipt":receipt_value,
                "artifacts":{"review_record_json":review,"closeout_receipt_json":receipt,"closeout_plan_json":plan}}),
            );
            let fixture = Self {
                home,
                profile,
                task,
                probe,
                review,
                receipt,
                plan,
            };
            fixture.save();
            fixture
        }

        pub(crate) fn save(&self) {
            put(
                &self.profile.join("offdesk_tasks.json"),
                &json!([self.task]),
            );
            put(
                &self.profile.join("background_runs.json"),
                &json!([self.probe]),
            );
        }

        pub(crate) fn closed(&self) -> Option<ClosedFailureHistory> {
            RetirementIndex::load(&self.profile)
                .unwrap_or_default()
                .closed(
                    &self.profile,
                    &self.task,
                    std::slice::from_ref(&self.probe),
                    Utc::now(),
                )
        }

        fn compact_plan(&self) {
            let mut plan: Value = serde_json::from_slice(&fs::read(&self.plan).unwrap()).unwrap();
            let task = &mut plan["tasks"][0];
            task["artifact_refs"] = serde_json::to_value(&self.task.artifact_refs).unwrap();
            task.as_object_mut().unwrap().retain(|key, _| {
                [
                    "task_id",
                    "request_id",
                    "project_key",
                    "status",
                    "capability_id",
                    "runner_kind",
                    "workdir",
                    "agent_mode",
                    "background_ticket_id",
                    "result_artifact_path",
                    "artifact_refs",
                    "preview",
                    "reason",
                ]
                .contains(&key.as_str())
            });
            put(&self.plan, &plan);
        }

        pub(crate) fn save_shift(&self) {
            let at = self.task.created_at;
            let end = at + Duration::minutes(5);
            let manifest:OffdeskShiftManifestV1=serde_json::from_value(json!({
                "schema":OFFDESK_SHIFT_MANIFEST_SCHEMA,"shift_id":"history-shift","profile":"history-fixture",
                "created_at":at,"window":{"starts_at":at,"stops_at":end,"timezone":"Asia/Seoul"},
                "plan_id":"history-plan","plan_review_id":"history-plan-review","plan_source_sha256":"a".repeat(64),"plan_review_sha256":"b".repeat(64),
                "task_bindings":[{"task_id":self.task.task_id,"request_id":self.task.request_id,
                    "project_key":self.task.project_key,"capability_id":self.task.capability_id,"runner_kind":self.task.runner_kind,
                    "project_root_identity_sha256":"c".repeat(64),"launch_spec_sha256":offdesk_task_launch_spec_sha256(&self.task).unwrap(),"execution_brief_sha256":"d".repeat(64)}],
                "routine_bindings":[],"budget":{"max_concurrent_tasks":1,"max_task_launches":1,"max_attempts_per_task":1,"max_routine_runs":0},
                "authority":{"task_dispatch_requires_fresh_approval":true,"task_launch_spec_is_immutable":true,
                    "wiki_writes_are_candidate_only":true,"plan_revision_requires_review":true,"acceptance_requires_ondesk_review":true,
                    "does_not_authorize":OFFDESK_SHIFT_REQUIRED_DENIALS}
            })).unwrap();
            manifest.validate().unwrap();
            let arm = OffdeskShiftArmReceiptV1 {
                schema: OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA.into(),
                receipt_id: "history-arm".into(),
                shift_id: manifest.shift_id.clone(),
                manifest_sha256: offdesk_shift_manifest_sha256(&manifest).unwrap(),
                approval_id: "fixture-approval".into(),
                approved_at: at,
                expires_at: end,
                approved_by: "fixture-only".into(),
                task_dispatch_still_requires_fresh_approval: true,
            };
            let arm_hash = offdesk_shift_arm_receipt_sha256(&arm, &manifest).unwrap();
            let state = |status, sequence| OffdeskShiftStateV1 {
                schema: OFFDESK_SHIFT_STATE_SCHEMA.into(),
                shift_id: manifest.shift_id.clone(),
                manifest_sha256: arm.manifest_sha256.clone(),
                arm_receipt_sha256: arm_hash.clone(),
                sequence,
                status,
                updated_at: at,
                reason: "fixture lifecycle".into(),
                acceptance_receipt_id: None,
                acceptance_receipt_sha256: None,
            };
            let initial = state(OffdeskShiftStatusV1::Armed, 1);
            let disarmed = state(OffdeskShiftStatusV1::Disarmed, 2);
            let transitions = [
                OffdeskShiftTransitionReceiptV1::from_state_transition(
                    "fixture-arm",
                    None,
                    &initial,
                )
                .unwrap(),
                OffdeskShiftTransitionReceiptV1::from_state_transition(
                    "fixture-disarm",
                    Some(&initial),
                    &disarmed,
                )
                .unwrap(),
            ];
            let binding = &manifest.task_bindings[0];
            let launch = OffdeskShiftTaskLaunchReservationV1 {
                schema: OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA.into(),
                launch_id: "history-ticket".into(),
                shift_id: manifest.shift_id.clone(),
                manifest_sha256: arm.manifest_sha256.clone(),
                arm_receipt_sha256: arm_hash.clone(),
                task_id: self.task.task_id.clone(),
                background_ticket_id: "history-ticket".into(),
                runner_kind: self.task.runner_kind,
                launch_spec_sha256: binding.launch_spec_sha256.clone(),
                execution_brief_sha256: binding.execution_brief_sha256.clone(),
                project_root_identity_sha256: binding.project_root_identity_sha256.clone(),
                host_policy_attestation_sha256: None,
                attempt_number: 1,
                reserved_at: at,
            };
            let ticket = OffdeskShiftTaskLaunchTicketReceiptV1 {
                schema: OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA.into(),
                receipt_id: "shift_task_ticket_history-ticket".into(),
                launch_id: launch.launch_id.clone(),
                shift_id: manifest.shift_id.clone(),
                manifest_sha256: arm.manifest_sha256.clone(),
                arm_receipt_sha256: arm_hash.clone(),
                task_id: self.task.task_id.clone(),
                attempt_number: 1,
                background_ticket_id: launch.background_ticket_id.clone(),
                runner_kind: self.task.runner_kind,
                background_probe_sha256: offdesk_shift_background_probe_sha256(&self.probe)
                    .unwrap(),
                recorded_at: at,
            };
            let outcome = OffdeskShiftTaskOutcomeReceiptV1 {
                schema: OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA.into(),
                receipt_id: "shift_task_outcome_history-ticket".into(),
                launch_id: launch.launch_id.clone(),
                shift_id: manifest.shift_id.clone(),
                manifest_sha256: arm.manifest_sha256.clone(),
                arm_receipt_sha256: arm_hash,
                task_id: self.task.task_id.clone(),
                attempt_number: 1,
                background_ticket_id: launch.background_ticket_id.clone(),
                outcome: OffdeskShiftTaskOutcomeV1::RecoveryRequired,
                terminal_phase: Some(BackgroundRunnerPhase::Failed),
                launch_ticket_receipt_sha256: Some(
                    offdesk_shift_task_ticket_receipt_sha256(&ticket, &launch, &manifest, &arm)
                        .unwrap(),
                ),
                background_probe_sha256: Some(
                    offdesk_shift_background_probe_sha256(&self.probe).unwrap(),
                ),
                completed_at: at,
                summary: "failed fixture".into(),
            };
            put(
                &self.profile.join("offdesk_shifts.v1.json"),
                &json!({"schema":"offdesk_shift_ledger.v1","manifests":[manifest],
                "arm_receipts":[arm],"states":[disarmed],"transitions":transitions,"task_launch_reservations":[launch],"task_launch_ticket_receipts":[ticket],"task_outcome_receipts":[outcome]}),
            );
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(
                self.profile.join("offdesk_shifts.v1.json"),
                fs::Permissions::from_mode(0o600),
            )
            .unwrap();
        }
    }

    pub(crate) fn put(path: &Path, value: &Value) {
        fs::write(path, serde_json::to_vec_pretty(value).unwrap()).unwrap();
    }

    #[test]
    fn compact_retirement_uses_exact_immutable_shift_evidence() {
        let mut f = Fixture::new(BackgroundRunnerKind::LocalTmux);
        f.save_shift();
        f.compact_plan();
        let original = fs::read(&f.plan).unwrap();
        assert!(
            f.closed().is_some(),
            "the production compact closeout format must be supported"
        );
        assert_eq!(fs::read(&f.plan).unwrap(), original);
        f.task.command = "changed command".into();
        assert!(f.closed().is_none());
        f.task.command = "exit 7".into();
        f.task.result_artifact_path = Some("other-result.json".into());
        assert!(f.closed().is_none());
    }

    #[test]
    fn compact_retirement_without_launch_evidence_keeps_recovery_visible() {
        let f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        f.compact_plan();
        assert!(f.closed().is_none());
    }

    #[test]
    fn compact_retirement_keeps_missing_changed_or_ambiguous_evidence_held() {
        let mut f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        f.save_shift();
        f.compact_plan();
        let ledger = f
            .profile
            .join(super::super::shift_store::OFFDESK_SHIFT_LEDGER_FILE);
        let original = fs::read(&ledger).unwrap();
        let original_plan = fs::read(&f.plan).unwrap();
        assert!(f.closed().is_some());
        for change in [
            "missing_ticket",
            "missing_outcome",
            "duplicate_launch",
            "wrong_profile",
            "corrupt",
        ] {
            let mut value: Value = serde_json::from_slice(&original).unwrap();
            match change {
                "missing_ticket" => value["task_launch_ticket_receipts"] = json!([]),
                "missing_outcome" => value["task_outcome_receipts"] = json!([]),
                "duplicate_launch" => {
                    let duplicate = value["task_launch_reservations"][0].clone();
                    value["task_launch_reservations"]
                        .as_array_mut()
                        .unwrap()
                        .push(duplicate);
                }
                "wrong_profile" => value["manifests"][0]["profile"] = json!("wrong-profile"),
                "corrupt" => value = json!({}),
                _ => unreachable!(),
            }
            put(&ledger, &value);
            assert!(f.closed().is_none(), "{change}");
        }
        fs::write(&ledger, original).unwrap();
        for change in [
            "wrong_summary_result",
            "partial_full_task",
            "invalid_runtime_hash",
        ] {
            let mut value: Value = serde_json::from_slice(&original_plan).unwrap();
            match change {
                "wrong_summary_result" => {
                    value["tasks"][0]["result_artifact_path"] = json!("wrong.json")
                }
                "partial_full_task" => value["tasks"][0]["command"] = json!("exit 7"),
                "invalid_runtime_hash" => value["tasks"][0]["runtime_sha256"] = json!(false),
                _ => unreachable!(),
            }
            put(&f.plan, &value);
            assert!(f.closed().is_none(), "{change}");
        }
        fs::write(&f.plan, original_plan).unwrap();
        f.task.attempt_count = 2;
        assert!(f.closed().is_none());
        f.task.attempt_count = 1;
        f.probe.runtime_pid = Some(std::process::id());
        assert!(f.closed().is_none());
        f.probe.runtime_pid = None;
        assert!(f.closed().is_none());
    }

    #[test]
    fn changed_scope_cannot_inherit_a_legacy_or_runtime_bound_retirement() {
        for operator_view in [false, true] {
            let mut f = Fixture::new(BackgroundRunnerKind::LocalBackground);
            if operator_view {
                let mut plan: Value = serde_json::from_slice(&fs::read(&f.plan).unwrap()).unwrap();
                plan["tasks"][0] =
                    serde_json::to_value(f.task.operator_view_with_profile(&f.profile)).unwrap();
                put(&f.plan, &plan);
            }
            assert!(f.closed().is_some());
            f.task.command = "changed command".into();
            assert!(f.closed().is_none());
            f.task.command = "exit 7".into();
            assert!(f.closed().is_some());
            f.task.result_artifact_path = Some("other-result.json".into());
            assert!(f.closed().is_none());
        }
    }

    #[test]
    fn verified_retired_failure_is_history_for_background_and_tmux() {
        for runner in [
            BackgroundRunnerKind::LocalBackground,
            BackgroundRunnerKind::LocalTmux,
        ] {
            let f = Fixture::new(runner);
            let history = f.closed().unwrap();
            assert_eq!(history.state, "retired_incomplete");
            assert!(!history.accepted_truth_authorized);
            assert!(!history.runtime_release_authorized);
            assert!(!history.automatic_resume);
            let summary = load_offdesk_status_summary(&f.profile, Utc::now()).unwrap();
            assert_eq!(summary.background_failed, 0);
            assert_eq!(summary.background_failed_history, 1);
            assert!(!summary
                .next_safe_actions
                .iter()
                .any(|a| a.kind == "recovery_required"));
            assert_eq!(
                BackgroundRunStore::new(&f.profile).load().unwrap()[0].phase,
                BackgroundRunnerPhase::Failed
            );
            if runner == BackgroundRunnerKind::LocalTmux {
                assert!(
                    !crate::offdesk::runtime_occupancy::released(&f.profile, &f.task).unwrap(),
                    "presentation must not grant scheduler release"
                );
            }
        }
    }

    #[test]
    fn live_unknown_reused_or_changed_attempts_keep_recovery_visible() {
        let f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        let index = RetirementIndex::load(&f.profile).unwrap();
        for change in [
            "running",
            "later_attempt",
            "new_request",
            "new_ticket",
            "later_update",
            "live_supervisor",
            "unknown_pid",
            "wrong_identity",
            "missing_receipt",
            "wrong_scope",
        ] {
            let mut task = f.task.clone();
            let mut probe = f.probe.clone();
            match change {
                "running" => task.status = OffdeskTaskStatus::Running,
                "later_attempt" => task.attempt_count = 2,
                "new_request" => task.request_id = "new".into(),
                "new_ticket" => task.background_ticket_id = Some("new".into()),
                "later_update" => task.updated_at = Utc::now() + Duration::seconds(1),
                "live_supervisor" => {
                    probe.runtime_pid = Some(std::process::id());
                    probe.runtime_identity = crate::process::process_identity(std::process::id());
                }
                "unknown_pid" => probe.runtime_pid = None,
                "wrong_identity" => probe.task_id = Some("other".into()),
                "missing_receipt" => probe.local_exit_receipt = None,
                "wrong_scope" => probe.local_exit_receipt.as_mut().unwrap().wait_scope = None,
                _ => unreachable!(),
            }
            assert!(
                index
                    .closed(&f.profile, &task, &[probe], Utc::now())
                    .is_none(),
                "{change}"
            );
        }
    }

    #[test]
    fn missing_changed_linked_and_stale_artifacts_never_quiet_a_failure() {
        use std::os::unix::fs::symlink;
        let f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        let original = fs::read(&f.receipt).unwrap();
        fs::write(&f.receipt, b"{}").unwrap();
        assert!(f.closed().is_none());
        fs::remove_file(&f.receipt).unwrap();
        assert!(f.closed().is_none());
        let outside = f.home.path().join("outside.json");
        fs::write(&outside, &original).unwrap();
        symlink(&outside, &f.receipt).unwrap();
        assert!(f.closed().is_none());
        fs::remove_file(&f.receipt).unwrap();
        fs::hard_link(&outside, &f.receipt).unwrap();
        assert!(f.closed().is_none());
        fs::remove_file(&f.receipt).unwrap();
        fs::write(&f.receipt, original).unwrap();
        assert!(f.closed().is_some());
        let mut plan: Value = serde_json::from_slice(&fs::read(&f.plan).unwrap()).unwrap();
        plan["tasks"][0]["request_id"] = json!("other");
        put(&f.plan, &plan);
        assert!(f.closed().is_none());
        fs::remove_file(&f.plan).unwrap();
        assert!(f.closed().is_none());
    }

    #[test]
    fn exit_status_drift_and_ambiguous_probes_keep_the_failure_actionable() {
        let f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        let index = RetirementIndex::load(&f.profile).unwrap();
        assert!(index
            .closed(
                &f.profile,
                &f.task,
                &[f.probe.clone(), f.probe.clone()],
                Utc::now()
            )
            .is_none());
        let receipt = Path::new(&f.task.workdir)
            .join(&f.probe.local_exit_receipt.as_ref().unwrap().relative_path);
        let original = fs::read(&receipt).unwrap();
        let mut changed: Value = serde_json::from_slice(&original).unwrap();
        changed["status"] = json!(0);
        put(&receipt, &changed);
        assert!(f.closed().is_none());
        let summary = load_offdesk_status_summary(&f.profile, Utc::now()).unwrap();
        assert_eq!(summary.background_failed, 1);
        assert_eq!(summary.background_failed_history, 0);
        fs::write(receipt, original).unwrap();
        assert!(f.closed().is_some());
    }

    #[test]
    fn index_revalidates_bytes_and_newer_nonretirement_overrides_old_history() {
        let f = Fixture::new(BackgroundRunnerKind::LocalBackground);
        let index = RetirementIndex::load(&f.profile).unwrap();
        let original = fs::read(&f.review).unwrap();
        let mut changed: Value = serde_json::from_slice(&original).unwrap();
        changed["closeout_id"] = json!("different");
        put(&f.review, &changed);
        assert!(index
            .closed(
                &f.profile,
                &f.task,
                std::slice::from_ref(&f.probe),
                Utc::now()
            )
            .is_none());
        fs::write(&f.review, original).unwrap();
        let mut newer: Value = serde_json::from_slice(&fs::read(&f.review).unwrap()).unwrap();
        newer["reviewed_at"] = json!(Utc::now());
        newer["closeout_receipt"]["acceptance_status"] = json!("revision_required");
        put(
            &f.review.with_file_name("closeout_review_newer.json"),
            &newer,
        );
        assert!(f.closed().is_none());
    }
}
