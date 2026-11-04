//! Explicit, journaled updates to an existing owning plan; no runtime queue.

use std::fs::{File, Permissions};
use std::io::{ErrorKind, Read, Write};
use std::path::{Path, PathBuf};

use anyhow::{ensure, Context, Result};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use super::{
    ondesk_plan::{inspect, preview, MAX_PLAN_BYTES},
    ondesk_plan_adoption,
};
use crate::process::{self, FilesystemObjectSnapshot};

const MAX_JOURNAL_BYTES: usize = 1_048_576;
const MAX_REQUESTS: usize = 512;

struct UnlockOnDrop<'a>(&'a File);

impl Drop for UnlockOnDrop<'_> {
    fn drop(&mut self) {
        // A concurrently forked child can retain the open file description
        // until exec. Closing our descriptor alone need not release flock.
        let _ = FileExt::unlock(self.0);
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanChangeRequest {
    pub schema: String,
    pub request_id: String,
    pub project_key: String,
    /// Exact absolute owning file, not a selector resolved later.
    pub source_path: PathBuf,
    pub baseline_sha256: String,
    pub candidate_sha256: String,
    /// Attribution to the operator's decision, not a cryptographic credential.
    pub decision_ref: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub review_record: Option<super::ondesk_review::ReviewDecisions>,
}

#[derive(Debug, Serialize)]
pub struct PlanApplyResult {
    pub schema: &'static str,
    pub request_id: String,
    pub outcome: &'static str,
    pub current_sha256: String,
    pub does_not_authorize: Vec<&'static str>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    request: PlanChangeRequest,
    committed: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    schema: String,
    source_path: PathBuf,
    entries: Vec<Entry>,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

pub(super) fn read(root: &File, name: &Path, limit: usize) -> Result<Vec<u8>> {
    let file = process::open_bounded_regular_file(root, name)?;
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "plan artifacts must have a single hard link"
    );
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    let mut bytes = Vec::new();
    let mut reader = file.take(limit as u64 + 1);
    reader.read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= limit, "plan artifact exceeds byte budget");
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?),
        "plan artifact changed during read"
    );
    let active = process::open_bounded_regular_file(root, name)?;
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&active.metadata()?),
        "plan artifact replaced during read"
    );
    Ok(bytes)
}

pub(super) fn active_root(path: &Path, root: &File) -> Result<()> {
    let active = process::open_bounded_root(path)?;
    ensure!(
        process::filesystem_file_identity(&active.metadata()?)
            == process::filesystem_file_identity(&root.metadata()?),
        "plan directory replaced"
    );
    Ok(())
}

fn replace(
    root: &File,
    parent: &Path,
    name: &Path,
    bytes: &[u8],
    permissions: Option<Permissions>,
) -> Result<()> {
    let temporary = PathBuf::from(format!(".forager-plan-{}.tmp", Uuid::new_v4()));
    let result = (|| {
        let mut file = process::create_new_bounded_regular_file(root, &temporary)?;
        if let Some(permissions) = permissions {
            file.set_permissions(permissions)?;
        }
        file.write_all(bytes)?;
        file.sync_all()?;
        active_root(parent, root)?;
        process::replace_bounded_root_file(root, &temporary, name)?;
        root.sync_all()?;
        active_root(parent, root)?;
        Ok(())
    })();
    if result.is_err() {
        // Only remove this call's newly allocated temporary artifact.
        let _ = process::remove_bounded_root_file(root, &temporary);
    }
    result
}

pub(super) fn validate_request(request: &PlanChangeRequest) -> Result<()> {
    if request.schema == ondesk_plan_adoption::REQUEST_SCHEMA {
        ensure!(
            request.review_record.is_none(),
            "adoption cannot record review dispositions"
        );
    }
    if let Some(review) = &request.review_record {
        ensure!(
            serde_json::to_vec(review)?.len() <= MAX_PLAN_BYTES,
            "review record exceeds byte budget"
        );
        ensure!(
            review.request_id == request.request_id
                && review.project_key == request.project_key
                && review.baseline_sha256 == request.baseline_sha256
                && review.decision_ref == request.decision_ref,
            "review record does not match write request"
        );
    }
    ensure!(
        request.schema == "ondesk_plan_change_request.v1"
            || request.schema == ondesk_plan_adoption::REQUEST_SCHEMA,
        "unsupported change request schema"
    );
    ensure!(
        !request.request_id.is_empty()
            && request.request_id.len() <= 128
            && request
                .request_id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)),
        "invalid request ID"
    );
    ensure!(
        !request.decision_ref.trim().is_empty() && request.decision_ref.len() <= 1024,
        "decision reference required"
    );
    ensure!(
        request.source_path.is_absolute(),
        "absolute source path required"
    );
    for hash in [&request.baseline_sha256, &request.candidate_sha256] {
        ensure!(
            hash.len() == 64
                && hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
            "invalid change hash"
        );
    }
    Ok(())
}

pub fn apply(
    request: &PlanChangeRequest,
    candidate: &str,
    confirmed: bool,
) -> Result<PlanApplyResult> {
    apply_inner(request, candidate, confirmed, |_| Ok(()))
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Boundary {
    Prepared,
    Replaced,
}

fn apply_inner(
    request: &PlanChangeRequest,
    candidate: &str,
    confirmed: bool,
    boundary: impl Fn(Boundary) -> Result<()>,
) -> Result<PlanApplyResult> {
    ensure!(confirmed, "explicit plan-write confirmation required");
    validate_request(request)?;
    let candidate_plan = inspect(candidate, &request.project_key)?;
    let adoption = request.schema == ondesk_plan_adoption::REQUEST_SCHEMA;
    if adoption {
        ondesk_plan_adoption::validate_initial_tasks(&candidate_plan.plan)?;
    }
    ensure!(
        digest(candidate.as_bytes()) == request.candidate_sha256,
        "candidate hash mismatch"
    );
    let parent = request
        .source_path
        .parent()
        .context("source parent missing")?;
    let name = Path::new(
        request
            .source_path
            .file_name()
            .context("source filename missing")?,
    );
    let root = process::open_bounded_root(parent)?;
    // Validate the source before creating any journal or lock.
    let initial = read(&root, name, MAX_PLAN_BYTES)?;
    if adoption && digest(&initial) == request.baseline_sha256 {
        ondesk_plan_adoption::validate_append(
            std::str::from_utf8(&initial)?,
            candidate,
            &request.project_key,
        )?;
    } else {
        inspect(std::str::from_utf8(&initial)?, &request.project_key)?;
    }
    let stem = name.to_str().context("source filename must be UTF-8")?;
    let lock_name = PathBuf::from(format!(".{stem}.forager-plan.lock"));
    let journal_name = PathBuf::from(format!(".{stem}.forager-plan-journal.json"));
    let lock = match process::create_new_bounded_regular_file(&root, &lock_name) {
        Ok(file) => file,
        Err(error) if error.kind() == ErrorKind::AlreadyExists => {
            process::open_bounded_regular_file(&root, &lock_name)?
        }
        Err(error) => return Err(error.into()),
    };
    lock.try_lock_exclusive()
        .context("plan writer busy; retry after inspecting ownership")?;
    let _unlock = UnlockOnDrop(&lock);
    ensure!(
        process::filesystem_link_count(&lock.metadata()?) == Some(1),
        "plan lock must have a single hard link"
    );
    let active_lock = process::open_bounded_regular_file(&root, &lock_name)?;
    ensure!(
        process::filesystem_file_identity(&active_lock.metadata()?)
            == process::filesystem_file_identity(&lock.metadata()?),
        "plan lock replaced"
    );
    active_root(parent, &root)?;
    let mut journal = match read(&root, &journal_name, MAX_JOURNAL_BYTES) {
        Ok(bytes) => serde_json::from_slice::<Journal>(&bytes).context("invalid plan journal")?,
        Err(error)
            if error
                .downcast_ref::<std::io::Error>()
                .is_some_and(|e| e.kind() == ErrorKind::NotFound) =>
        {
            Journal {
                schema: "ondesk_plan_change_journal.v1".into(),
                source_path: request.source_path.clone(),
                entries: vec![],
            }
        }
        Err(error) => return Err(error),
    };
    ensure!(
        journal.schema == "ondesk_plan_change_journal.v1"
            && journal.source_path == request.source_path,
        "journal identity mismatch"
    );
    ensure!(
        journal.entries.len() <= MAX_REQUESTS,
        "journal request budget exceeded"
    );
    let mut ids = std::collections::BTreeSet::new();
    for entry in &journal.entries {
        validate_request(&entry.request)?;
        ensure!(
            entry.request.source_path == request.source_path
                && ids.insert(&entry.request.request_id),
            "invalid journal request identity"
        );
    }
    let current = read(&root, name, MAX_PLAN_BYTES)?;
    let hash = digest(&current);
    let existing = journal
        .entries
        .iter()
        .position(|e| e.request.request_id == request.request_id);
    if let Some(index) = existing {
        ensure!(
            journal.entries[index].request == *request,
            "request ID reused with different content"
        );
        if journal.entries[index].committed {
            return Ok(result(request, "already_applied", hash));
        }
    }
    ensure!(
        !journal
            .entries
            .iter()
            .any(|e| !e.committed && e.request.request_id != request.request_id),
        "another plan update needs recovery"
    );
    let save = |journal: &Journal| -> Result<()> {
        let bytes = serde_json::to_vec_pretty(journal)?;
        ensure!(
            bytes.len() <= MAX_JOURNAL_BYTES,
            "journal exceeds byte budget"
        );
        replace(&root, parent, &journal_name, &bytes, None)
    };
    if let Some(index) = existing {
        if hash == request.candidate_sha256 {
            journal.entries[index].committed = true;
            save(&journal)?;
            return Ok(result(request, "recovered_content_match", hash));
        }
    }
    if adoption {
        ensure!(hash == request.baseline_sha256, "stale plan baseline");
        ondesk_plan_adoption::validate_append(
            std::str::from_utf8(&current)?,
            candidate,
            &request.project_key,
        )?;
    } else {
        preview(
            std::str::from_utf8(&current)?,
            candidate,
            &request.project_key,
            &request.baseline_sha256,
        )?;
    }
    if let Some(review) = &request.review_record {
        let proposal = super::ondesk_review::propose(
            std::str::from_utf8(&current)?,
            &request.source_path,
            review.clone(),
        )?;
        ensure!(
            proposal.candidate_markdown == candidate,
            "candidate does not implement the recorded review"
        );
    }
    let index = match existing {
        Some(index) => index,
        None => {
            ensure!(
                journal.entries.len() < MAX_REQUESTS,
                "journal full; reviewed retention required"
            );
            journal.entries.push(Entry {
                request: request.clone(),
                committed: false,
            });
            save(&journal)?;
            journal.entries.len() - 1
        }
    };
    boundary(Boundary::Prepared)?;
    let current_lock = process::open_bounded_regular_file(&root, &lock_name)?;
    ensure!(
        process::filesystem_file_identity(&current_lock.metadata()?)
            == process::filesystem_file_identity(&lock.metadata()?),
        "plan lock replaced before commit"
    );
    if let Some(review) = &request.review_record {
        super::ondesk_review::validate_shift_review(review)?;
    }
    ensure!(
        digest(&read(&root, name, MAX_PLAN_BYTES)?) == request.baseline_sha256,
        "plan changed before replacement"
    );
    let permissions = process::open_bounded_regular_file(&root, name)?
        .metadata()?
        .permissions();
    replace(&root, parent, name, candidate.as_bytes(), Some(permissions))?;
    boundary(Boundary::Replaced)?;
    ensure!(
        digest(&read(&root, name, MAX_PLAN_BYTES)?) == request.candidate_sha256,
        "plan changed after replacement; inspect journal"
    );
    journal.entries[index].committed = true;
    save(&journal)?;
    Ok(result(request, "applied", request.candidate_sha256.clone()))
}

fn result(request: &PlanChangeRequest, outcome: &'static str, hash: String) -> PlanApplyResult {
    PlanApplyResult {
        schema: "ondesk_plan_apply_result.v1",
        request_id: request.request_id.clone(),
        outcome,
        current_sha256: hash,
        does_not_authorize: vec!["launch", "deployment", "wiki_promotion", "accepted_truth"],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    #[test]
    fn failed_transaction_releases_lock_with_an_inherited_descriptor_alive() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("lock");
        let lock = File::create(&path)?;
        let inherited = lock.try_clone()?;
        let failed = (|| -> Result<()> {
            lock.try_lock_exclusive()?;
            let _unlock = UnlockOnDrop(&lock);
            anyhow::bail!("interrupted transaction")
        })();
        assert!(failed.is_err());
        drop(lock);
        let next_writer = File::open(path)?;
        next_writer.try_lock_exclusive()?;
        assert!(inherited.metadata().is_ok());
        FileExt::unlock(&next_writer)?;
        Ok(())
    }

    fn document(goal: &str) -> String {
        format!(
            "# Plan\n```forager-plan\n{}\n```\n",
            json!({"schema":"ondesk_task_plan.v1","project_key":"demo","tasks":[{"task_id":"T-1","goal":goal,"completion_criteria":["verified"],"depends_on":[],"review_state":"pending_review","evidence_refs":[]}]})
        )
    }
    fn setup() -> (tempfile::TempDir, PlanChangeRequest, String) {
        let dir = tempfile::tempdir_in(fs::canonicalize(std::env::temp_dir()).unwrap()).unwrap();
        let source = document("before");
        let candidate = document("after");
        let path = dir.path().join("plan.md");
        fs::write(&path, &source).unwrap();
        let request = PlanChangeRequest {
            schema: "ondesk_plan_change_request.v1".into(),
            request_id: "r1".into(),
            project_key: "demo".into(),
            source_path: path,
            baseline_sha256: digest(source.as_bytes()),
            candidate_sha256: digest(candidate.as_bytes()),
            decision_ref: "operator:fixture".into(),
            review_record: None,
        };
        (dir, request, candidate)
    }
    #[test]
    fn apply_replay_and_request_collision() {
        let (_dir, mut request, candidate) = setup();
        assert!(apply(&request, &candidate, false).is_err());
        assert_eq!(
            apply(&request, &candidate, true).unwrap().outcome,
            "applied"
        );
        assert_eq!(
            apply(&request, &candidate, true).unwrap().outcome,
            "already_applied"
        );
        request.decision_ref = "changed".into();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), candidate);
    }
    #[test]
    fn stale_candidate_and_baseline_do_not_write() {
        let (_dir, request, candidate) = setup();
        assert!(apply(&request, "wrong", true).is_err());
        let other = document("other writer");
        fs::write(&request.source_path, &other).unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), other);
    }
    #[test]
    fn recovery_at_both_durable_boundaries() {
        for stop in [Boundary::Prepared, Boundary::Replaced] {
            let (_dir, request, candidate) = setup();
            assert!(apply_inner(&request, &candidate, true, |at| {
                ensure!(at != stop, "simulated interruption");
                Ok(())
            })
            .is_err());
            let recovered = apply(&request, &candidate, true).unwrap();
            assert_eq!(
                recovered.outcome,
                if stop == Boundary::Prepared {
                    "applied"
                } else {
                    "recovered_content_match"
                }
            );
            assert_eq!(fs::read_to_string(&request.source_path).unwrap(), candidate);
        }
    }
    #[test]
    fn adoption_recovers_at_both_durable_boundaries_and_replay_never_appends_again() {
        for stop in [Boundary::Prepared, Boundary::Replaced] {
            let dir =
                tempfile::tempdir_in(fs::canonicalize(std::env::temp_dir()).unwrap()).unwrap();
            let source = "# Existing TODO\n- [ ] selected task\n";
            let path = dir.path().join("TODO.md");
            fs::write(&path, source).unwrap();
            let mut plan = inspect(&document("selected task"), "demo").unwrap().plan;
            plan.tasks[0].review_state = super::super::ondesk_plan::ReviewState::Unreviewed;
            let proposal = ondesk_plan_adoption::propose(
                &path,
                plan,
                "demo",
                &digest(source.as_bytes()),
                "adopt-1".into(),
                "operator:adoption".into(),
            )
            .unwrap();
            let request = proposal.change_request;
            let candidate = proposal.candidate_markdown;
            assert!(apply_inner(&request, &candidate, true, |at| {
                ensure!(at != stop, "simulated adoption interruption");
                Ok(())
            })
            .is_err());
            let result = apply(&request, &candidate, true).unwrap();
            assert_eq!(
                result.outcome,
                if stop == Boundary::Prepared {
                    "applied"
                } else {
                    "recovered_content_match"
                }
            );
            assert_eq!(
                apply(&request, &candidate, true).unwrap().outcome,
                "already_applied"
            );
            assert_eq!(fs::read_to_string(&path).unwrap(), candidate);
        }
    }
    #[test]
    fn interrupted_request_blocks_other_requests_and_preserves_conflicts() {
        let (_dir, request, candidate) = setup();
        assert!(apply_inner(&request, &candidate, true, |_| anyhow::bail!("interrupted")).is_err());
        let mut other_request = request.clone();
        other_request.request_id = "r2".into();
        assert!(apply(&other_request, &candidate, true).is_err());
        let other = document("external edit");
        fs::write(&request.source_path, &other).unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), other);
    }
    #[test]
    fn report_change_after_preparation_holds_write_until_exact_artifact_returns() {
        for reconcile in [false, true] {
            qualify_report_interruption(reconcile);
        }
    }

    fn qualify_report_interruption(reconcile: bool) {
        use super::super::ondesk_review;
        let (dir, request, _) = setup();
        let execution_source = fs::read_to_string(&request.source_path).unwrap();
        let source = if reconcile {
            format!("{execution_source}\nUpdated operator note\n")
        } else {
            execution_source.clone()
        };
        fs::write(&request.source_path, &source).unwrap();
        let report_path = dir.path().join("night.json");
        let report = serde_json::to_vec(&json!({
            "schema":"offdesk_shift_review_projection.v1", "shift_id":"night-1",
            "manifest_sha256":"a".repeat(64), "tasks":[{
                "project_key":"demo", "task_id":"T-1", "closeout_report":{
                    "schema":"offdesk_task_closeout_report.v1",
                    "historical_scope_status":"preserved_source_hash_verified",
                    "historical_source_sha256":request.baseline_sha256,
                    "review_disposition":"pending_user_review", "plan_update_authorized":false
                }
            }]
        }))
        .unwrap();
        fs::write(&report_path, &report).unwrap();
        let mut decisions: ondesk_review::ReviewDecisions = serde_json::from_value(json!({
            "request_id":"bound-review-1", "project_key":"demo", "baseline_sha256":digest(source.as_bytes()),
            "decision_ref":"operator:review", "decided_by":"local operator",
            "shift_review":{"path":report_path,"sha256":digest(&report),"shift_id":"night-1","manifest_sha256":"a".repeat(64)},
            "tasks":[{"task_id":"T-1","disposition":"accepted","evidence_refs":["check:reviewed"],"remaining_work":[],"unanswered_questions":[]}]
        })).unwrap();
        if reconcile {
            decisions.reconciliation = Some(ondesk_review::NarrativeReconciliation::new(
                execution_source,
                decisions.baseline_sha256.clone(),
                "Narrative-only reconciliation".into(),
            ));
        }
        let proposal = ondesk_review::propose(&source, &request.source_path, decisions).unwrap();
        let error = apply_inner(
            &proposal.change_request,
            &proposal.candidate_markdown,
            true,
            |boundary| {
                if boundary == Boundary::Prepared {
                    fs::write(&report_path, b"changed")?;
                }
                Ok(())
            },
        )
        .unwrap_err();
        assert!(error
            .to_string()
            .contains("shift review artifact hash changed"));
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), source);
        let journal: serde_json::Value = serde_json::from_slice(
            &fs::read(dir.path().join(".plan.md.forager-plan-journal.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(journal["entries"][0]["committed"], false);
        fs::write(&report_path, &report).unwrap();
        let error = apply_inner(
            &proposal.change_request,
            &proposal.candidate_markdown,
            true,
            |boundary| {
                if boundary == Boundary::Replaced {
                    anyhow::bail!("fixture interruption after replacement");
                }
                Ok(())
            },
        )
        .unwrap_err();
        assert!(error.to_string().contains("fixture interruption"));
        assert_eq!(
            fs::read_to_string(&request.source_path).unwrap(),
            proposal.candidate_markdown
        );
        fs::remove_file(&report_path).unwrap();
        assert_eq!(
            apply(&proposal.change_request, &proposal.candidate_markdown, true)
                .unwrap()
                .outcome,
            "recovered_content_match"
        );
    }

    #[test]
    fn concurrent_cooperating_writer_is_refused() {
        let (dir, request, candidate) = setup();
        let root = process::open_bounded_root(dir.path()).unwrap();
        let lock = process::create_new_bounded_regular_file(
            &root,
            Path::new(".plan.md.forager-plan.lock"),
        )
        .unwrap();
        lock.try_lock_exclusive().unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(
            digest(&fs::read(&request.source_path).unwrap()),
            request.baseline_sha256
        );
    }

    #[test]
    fn later_applied_work_is_not_rolled_back_by_an_old_replay() {
        let (_dir, request, candidate) = setup();
        apply(&request, &candidate, true).unwrap();
        let later = document("later accepted update");
        let mut next = request.clone();
        next.request_id = "r2".into();
        next.baseline_sha256 = request.candidate_sha256.clone();
        next.candidate_sha256 = digest(later.as_bytes());
        apply(&next, &later, true).unwrap();
        let replay = apply(&request, &candidate, true).unwrap();
        assert_eq!(replay.outcome, "already_applied");
        assert_eq!(replay.current_sha256, next.candidate_sha256);
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), later);
    }

    #[test]
    fn edit_after_reservation_is_detected_before_replacement() {
        let (_dir, request, candidate) = setup();
        let other = document("changed after reservation");
        assert!(apply_inner(&request, &candidate, true, |at| {
            if at == Boundary::Prepared {
                fs::write(&request.source_path, &other)?;
            }
            Ok(())
        })
        .is_err());
        assert_eq!(fs::read_to_string(&request.source_path).unwrap(), other);
    }

    #[test]
    fn hardlink_alias_and_malformed_journal_fail_closed() {
        let (dir, request, candidate) = setup();
        fs::hard_link(&request.source_path, dir.path().join("alias.md")).unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        let (dir, request, candidate) = setup();
        let journal = dir.path().join(".plan.md.forager-plan-journal.json");
        fs::write(&journal, "invalid journal").unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(fs::read_to_string(&journal).unwrap(), "invalid journal");
        assert_eq!(
            digest(&fs::read(&request.source_path).unwrap()),
            request.baseline_sha256
        );
    }

    #[cfg(unix)]
    #[test]
    fn symbolic_source_and_lock_are_not_followed() {
        use std::os::unix::fs::symlink;
        let (dir, mut request, candidate) = setup();
        let original = request.source_path.clone();
        let alias = dir.path().join("alias.md");
        symlink(&original, &alias).unwrap();
        request.source_path = alias;
        assert!(apply(&request, &candidate, true).is_err());
        request.source_path = original;
        symlink(
            &request.source_path,
            dir.path().join(".plan.md.forager-plan.lock"),
        )
        .unwrap();
        assert!(apply(&request, &candidate, true).is_err());
        assert_eq!(
            digest(&fs::read(&request.source_path).unwrap()),
            request.baseline_sha256
        );
    }
}
