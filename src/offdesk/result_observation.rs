//! Bounded current-file observations, not trusted process-exit receipts.

use std::{io::Read, path::Path};

use anyhow::{ensure, Result};
use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::process::{self, FilesystemObjectSnapshot};

const MAX_RESULT_BYTES: usize = 64 * 1024;
const MAX_EVIDENCE_BYTES: usize = 16 * 1024 * 1024;
const MAX_TOTAL_EVIDENCE_BYTES: usize = 32 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ResultObservation {
    pub verification: String,
    pub content_sha256: Option<String>,
    pub identity: String,
    pub reported_status: Option<String>,
    pub reported_exit_code: Option<i64>,
    pub process_exit_verified: bool,
    pub evidence: Vec<EvidenceObservation>,
    pub evidence_truncated: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub coexisting_failure: Option<FailureObservation>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FailureObservation {
    pub reference: String,
    pub verification: String,
    pub content_sha256: Option<String>,
    pub identity: String,
    pub reported_status: Option<String>,
    pub error_kind: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct EvidenceObservation {
    pub kind: String,
    pub description: String,
    pub reference: String,
    pub content_sha256: Option<String>,
    pub verification: String,
}

impl Default for ResultObservation {
    fn default() -> Self {
        Self {
            verification: "not_observed".into(),
            content_sha256: None,
            identity: "not_checked".into(),
            reported_status: None,
            reported_exit_code: None,
            process_exit_verified: false,
            evidence: vec![],
            evidence_truncated: false,
            coexisting_failure: None,
        }
    }
}

pub fn observe(
    root: &Path,
    path: &Path,
    task: &str,
    request: &str,
    ticket: &str,
) -> ResultObservation {
    let mut observation = match read(root, path) {
        Ok(bytes) => {
            let mut observation = parse(&bytes, task, request, ticket);
            if observation.identity == "self_reported_exact" {
                attach_evidence(root, &bytes, &mut observation);
            }
            observation
        }
        Err(_) => ResultObservation {
            verification: "unavailable_or_unsafe".into(),
            ..Default::default()
        },
    };
    let failure_path = root.join("FAILURE.json");
    if !failure_path
        .symlink_metadata()
        .is_err_and(|e| e.kind() == std::io::ErrorKind::NotFound)
    {
        let failure = match read(root, &failure_path) {
            Ok(bytes) => {
                let declared = parse(&bytes, task, request, ticket);
                let value = serde_json::from_slice::<serde_json::Value>(&bytes).ok();
                FailureObservation {
                    reference: "FAILURE.json".into(),
                    verification: declared.verification,
                    content_sha256: declared.content_sha256,
                    identity: declared.identity,
                    reported_status: declared.reported_status,
                    error_kind: value
                        .as_ref()
                        .and_then(|v| v["error_kind"].as_str())
                        .map(|v| super::operator_safe_text(v).chars().take(200).collect()),
                }
            }
            Err(_) => FailureObservation {
                reference: "FAILURE.json".into(),
                verification: "unavailable_or_unsafe".into(),
                content_sha256: None,
                identity: "not_checked".into(),
                reported_status: None,
                error_kind: None,
            },
        };
        observation.coexisting_failure = Some(failure);
        observation.verification = "coexisting_failure_requires_review".into();
    }
    observation
}

fn attach_evidence(root: &Path, bytes: &[u8], observation: &mut ResultObservation) {
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(bytes) else {
        return;
    };
    let Some(evidence) = value.get("evidence").and_then(|value| value.as_array()) else {
        return;
    };
    observation.evidence_truncated = evidence.len() > 16;
    let mut remaining = MAX_TOTAL_EVIDENCE_BYTES;
    for item in evidence.iter().take(16) {
        let path = item
            .get("path")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let expected = item
            .get("sha256")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let description = item
            .get("description")
            .and_then(|value| value.as_str())
            .unwrap_or("");
        let safe_description = super::operator_safe_text(description);
        let safe_path = super::operator_safe_text(path);
        observation.evidence_truncated |=
            safe_description.chars().count() > 2000 || safe_path.chars().count() > 2000;
        let mut entry = EvidenceObservation {
            kind: match item.get("kind").and_then(|value| value.as_str()) {
                Some("change") => "reported_change",
                Some("check") => "reported_check",
                _ => "unspecified",
            }
            .into(),
            description: safe_description.chars().take(2000).collect(),
            reference: safe_path.chars().take(2000).collect(),
            content_sha256: None,
            verification: "invalid_reference".into(),
        };
        if !path.is_empty()
            && expected.len() == 64
            && expected
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            match evidence_digest(root, &root.join(path), &mut remaining) {
                Ok(digest) => {
                    entry.verification = if digest == expected {
                        "current_bytes_hash_match_not_claim_verification"
                    } else {
                        "content_hash_mismatch"
                    }
                    .into();
                    entry.content_sha256 = Some(digest);
                }
                Err(_) => entry.verification = "unavailable_or_unsafe".into(),
            }
        }
        observation.evidence.push(entry);
    }
}

fn read(root_path: &Path, path: &Path) -> Result<Vec<u8>> {
    read_checked(root_path, path, MAX_RESULT_BYTES, |reader| {
        let mut bytes = Vec::new();
        reader.read_to_end(&mut bytes)?;
        Ok(bytes)
    })
}

fn evidence_digest(root: &Path, path: &Path, remaining: &mut usize) -> Result<String> {
    read_checked(root, path, MAX_EVIDENCE_BYTES.min(*remaining), |reader| {
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 64 * 1024];
        loop {
            let count = reader.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            // Charge bytes even if later identity or mutation checks fail.
            *remaining -= count;
            hash.update(&buffer[..count]);
        }
        Ok(format!("{:x}", hash.finalize()))
    })
}

fn read_checked<T>(
    root_path: &Path,
    path: &Path,
    limit: usize,
    consume: impl FnOnce(&mut dyn Read) -> Result<T>,
) -> Result<T> {
    ensure!(path.is_absolute(), "result path must be absolute");
    let relative = path.strip_prefix(root_path)?;
    let root = process::open_bounded_root(root_path)?;
    let file = process::open_bounded_regular_file(&root, relative)?;
    let metadata = file.metadata()?;
    ensure!(
        process::filesystem_link_count(&metadata) == Some(1),
        "result must have one link"
    );
    ensure!(metadata.len() <= limit as u64, "artifact too large");
    let before = FilesystemObjectSnapshot::from_metadata(&metadata);
    let mut reader = file.take(limit as u64);
    let output = consume(&mut reader)?;
    ensure!(
        limit as u64 - reader.limit() == metadata.len(),
        "incomplete artifact read"
    );
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&reader.get_ref().metadata()?),
        "result changed during read"
    );
    let current = process::open_bounded_regular_file(&root, relative)?;
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&current.metadata()?),
        "result replaced during read"
    );
    let current_root = process::open_bounded_root(root_path)?;
    ensure!(
        process::filesystem_file_identity(&root.metadata()?)
            == process::filesystem_file_identity(&current_root.metadata()?),
        "result root replaced"
    );
    Ok(output)
}

fn parse(bytes: &[u8], task: &str, request: &str, ticket: &str) -> ResultObservation {
    let mut result = ResultObservation {
        verification: "invalid_json".into(),
        content_sha256: Some(format!("{:x}", Sha256::digest(bytes))),
        ..Default::default()
    };
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(bytes) else {
        return result;
    };
    let Some(object) = value.as_object() else {
        return result;
    };
    result.verification = "current_file_parsed_not_historical_receipt".into();
    let ids = [
        ("task_id", task),
        ("request_id", request),
        ("ticket_id", ticket),
    ];
    result.identity = if ids.iter().any(|(key, expected)| {
        object
            .get(*key)
            .is_some_and(|v| v.as_str() != Some(*expected))
    }) {
        "mismatch"
    } else if ids.iter().all(|(key, _)| object.contains_key(*key)) {
        "self_reported_exact"
    } else {
        "missing"
    }
    .into();
    result.reported_status =
        object
            .get("status")
            .and_then(|v| v.as_str())
            .map(|status| match status {
                "completed"
                | "failed"
                | "cancelled"
                | "running"
                | "blocked"
                | "pending_user_review"
                | "revision_required" => status.to_string(),
                "failed_review_pending" => status.to_string(),
                _ => "unknown".into(),
            });
    result.reported_exit_code = object.get("exit_code").and_then(|v| v.as_i64());
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn content_is_reported_not_trusted_exit_evidence() {
        let result = parse(
            br#"{"task_id":"t","request_id":"r","ticket_id":"b","status":"failed","exit_code":7}"#,
            "t",
            "r",
            "b",
        );
        assert_eq!(result.identity, "self_reported_exact");
        assert_eq!(result.reported_status.as_deref(), Some("failed"));
        assert_eq!(result.reported_exit_code, Some(7));
        assert!(!result.process_exit_verified);
        assert_eq!(
            parse(br#"{"task_id":"other"}"#, "t", "r", "b").identity,
            "mismatch"
        );
        assert_eq!(
            parse(br#"{"status":"completed"}"#, "t", "r", "b").identity,
            "missing"
        );
        assert_eq!(parse(b"{", "t", "r", "b").verification, "invalid_json");
        assert_eq!(
            parse(
                br#"{"task_id":"t","request_id":"r","ticket_id":"b","status":"pending_user_review"}"#,
                "t",
                "r",
                "b"
            )
            .reported_status
            .as_deref(),
            Some("pending_user_review")
        );
    }

    #[test]
    fn coexisting_failure_preserves_result_bytes_and_never_becomes_exit_proof() -> Result<()> {
        let root = tempfile::tempdir()?;
        let result = root.path().join("result.json");
        let bytes=br#"{"task_id":"t","request_id":"r","ticket_id":"b","status":"pending_user_review","exit_code":0}"#;
        std::fs::write(&result, bytes)?;
        assert!(observe(root.path(), &result, "t", "r", "b")
            .coexisting_failure
            .is_none());
        let failure = root.path().join("FAILURE.json");
        std::fs::write(&failure,br#"{"task_id":"t","request_id":"r","ticket_id":"b","status":"failed_review_pending","error_kind":"ValueError"}"#)?;
        let observed = observe(root.path(), &result, "t", "r", "b");
        assert_eq!(observed.verification, "coexisting_failure_requires_review");
        assert_eq!(
            observed.reported_status.as_deref(),
            Some("pending_user_review")
        );
        assert_eq!(observed.reported_exit_code, Some(0));
        assert!(!observed.process_exit_verified);
        assert_eq!(
            observed.coexisting_failure.as_ref().unwrap().identity,
            "self_reported_exact"
        );
        assert_eq!(
            observed
                .coexisting_failure
                .as_ref()
                .unwrap()
                .error_kind
                .as_deref(),
            Some("ValueError")
        );
        assert_eq!(std::fs::read(&result)?, bytes);
        std::fs::write(&failure, b"not JSON")?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b")
                .coexisting_failure
                .unwrap()
                .verification,
            "invalid_json"
        );
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn unsafe_failure_artifact_still_holds_without_following_its_target() -> Result<()> {
        let root = tempfile::tempdir()?;
        let outside = tempfile::tempdir()?;
        let target = outside.path().join("secret");
        std::fs::write(&target, "private")?;
        std::os::unix::fs::symlink(&target, root.path().join("FAILURE.json"))?;
        let observed = observe(
            root.path(),
            &root.path().join("missing.json"),
            "t",
            "r",
            "b",
        );
        let failure = observed.coexisting_failure.unwrap();
        assert_eq!(failure.verification, "unavailable_or_unsafe");
        assert_eq!(failure.content_sha256, None);
        Ok(())
    }

    #[test]
    fn evidence_checks_bytes_without_certifying_claims() -> Result<()> {
        let root = tempfile::tempdir()?;
        let artifact = root.path().join("check.txt");
        std::fs::write(&artifact, b"checks passed")?;
        let result = root.path().join("result.json");
        let source = serde_json::json!({"task_id":"t","request_id":"r","ticket_id":"b","status":"completed","evidence":[
            {"kind":"check","description":"check report","path":"check.txt","sha256":format!("{:x}",Sha256::digest(b"checks passed"))},
            {"kind":"change","path":"check.txt","sha256":"0".repeat(64)},
            {"kind":"check","path":"../outside","sha256":"0".repeat(64)}
        ]});
        std::fs::write(&result, serde_json::to_vec(&source)?)?;
        let observed = observe(root.path(), &result, "t", "r", "b");
        assert_eq!(
            observed.evidence[0].verification,
            "current_bytes_hash_match_not_claim_verification"
        );
        assert_eq!(observed.evidence[1].verification, "content_hash_mismatch");
        assert_eq!(observed.evidence[2].verification, "unavailable_or_unsafe");
        assert!(observe(root.path(), &result, "wrong", "r", "b")
            .evidence
            .is_empty());
        assert!(!observed.process_exit_verified);
        Ok(())
    }

    #[test]
    fn file_reader_is_bounded_and_rejects_external_paths() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("result.json");
        std::fs::write(&path, br#"{"status":"completed"}"#)?;
        assert_eq!(
            observe(directory.path(), &path, "t", "r", "b")
                .reported_status
                .as_deref(),
            Some("completed")
        );
        std::fs::write(&path, vec![b' '; MAX_RESULT_BYTES + 1])?;
        assert_eq!(
            observe(directory.path(), &path, "t", "r", "b").verification,
            "unavailable_or_unsafe"
        );
        assert!(read(directory.path(), Path::new("/outside/result.json")).is_err());
        Ok(())
    }
    fn evidence_result(root: &Path, files: &[(&str, String)]) -> Result<std::path::PathBuf> {
        let path = root.join("result.json");
        let evidence: Vec<_> = files
            .iter()
            .map(|(name, sha)| {
                serde_json::json!({
            "kind":"check","path":name,"sha256":sha})
            })
            .collect();
        std::fs::write(
            &path,
            serde_json::to_vec(&serde_json::json!({
            "task_id":"t","request_id":"r","ticket_id":"b","status":"pending_user_review",
            "exit_code":0,"evidence":evidence}))?,
        )?;
        Ok(path)
    }

    #[test]
    fn large_evidence_uses_full_hash_and_detects_a_change_beyond_envelope_limit() -> Result<()> {
        let root = tempfile::tempdir()?;
        let mut bytes = vec![b'a'; 70869];
        let file = root.path().join("BOUND_REVIEW.json");
        std::fs::write(&file, &bytes)?;
        let result = evidence_result(
            root.path(),
            &[("BOUND_REVIEW.json", format!("{:x}", Sha256::digest(&bytes)))],
        )?;
        let original = std::fs::read(&result)?;
        let observed = observe(root.path(), &result, "t", "r", "b");
        assert_eq!(
            observed.evidence[0].verification,
            "current_bytes_hash_match_not_claim_verification"
        );
        assert!(!observed.process_exit_verified);
        bytes[70868] = b'b';
        std::fs::write(&file, &bytes)?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].verification,
            "content_hash_mismatch"
        );
        assert_eq!(original, std::fs::read(&result)?);
        Ok(())
    }

    #[test]
    fn per_artifact_limit_matches_producer_while_envelope_remains_small() -> Result<()> {
        let root = tempfile::tempdir()?;
        let file = root.path().join("large.bin");
        std::fs::File::create(&file)?.set_len(MAX_EVIDENCE_BYTES as u64)?;
        let expected = format!("{:x}", Sha256::digest(vec![0u8; MAX_EVIDENCE_BYTES]));
        let result = evidence_result(root.path(), &[("large.bin", expected)])?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].verification,
            "current_bytes_hash_match_not_claim_verification"
        );
        std::fs::File::options()
            .write(true)
            .open(&file)?
            .set_len(MAX_EVIDENCE_BYTES as u64 + 1)?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].verification,
            "unavailable_or_unsafe"
        );
        std::fs::write(&result, vec![b' '; MAX_RESULT_BYTES + 1])?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").verification,
            "unavailable_or_unsafe"
        );
        Ok(())
    }

    #[test]
    fn aggregate_budget_is_shared_across_evidence_including_hash_mismatches() -> Result<()> {
        let root = tempfile::tempdir()?;
        let expected = format!("{:x}", Sha256::digest(vec![0u8; MAX_EVIDENCE_BYTES]));
        for name in ["first.bin", "second.bin", "third.bin"] {
            std::fs::File::create(root.path().join(name))?.set_len(MAX_EVIDENCE_BYTES as u64)?;
        }
        let result = evidence_result(
            root.path(),
            &[
                ("first.bin", "0".repeat(64)),
                ("second.bin", expected.clone()),
                ("third.bin", expected),
            ],
        )?;
        let observed = observe(root.path(), &result, "t", "r", "b");
        assert_eq!(observed.evidence[0].verification, "content_hash_mismatch");
        assert_eq!(
            observed.evidence[1].verification,
            "current_bytes_hash_match_not_claim_verification"
        );
        assert_eq!(observed.evidence[2].verification, "unavailable_or_unsafe");
        assert_eq!(observed.evidence[2].content_sha256, None);
        Ok(())
    }

    #[test]
    fn evidence_budget_is_debited_by_actual_read_bytes_without_reset() -> Result<()> {
        let root = tempfile::tempdir()?;
        let file = root.path().join("report.bin");
        std::fs::write(&file, vec![b'x'; 70869])?;
        let mut remaining = 128 * 1024;
        evidence_digest(root.path(), &file, &mut remaining)?;
        assert_eq!(remaining, 128 * 1024 - 70869);
        assert!(evidence_digest(root.path(), &file, &mut remaining).is_err());
        assert_eq!(remaining, 128 * 1024 - 70869);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn large_evidence_keeps_symlink_and_hardlink_refusals() -> Result<()> {
        let root = tempfile::tempdir()?;
        let outside = tempfile::tempdir()?;
        let bytes = vec![b'x'; 70869];
        let expected = format!("{:x}", Sha256::digest(&bytes));
        let target = outside.path().join("target.bin");
        std::fs::write(&target, &bytes)?;
        let file = root.path().join("report.bin");
        std::os::unix::fs::symlink(&target, &file)?;
        let result = evidence_result(root.path(), &[("report.bin", expected.clone())])?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].verification,
            "unavailable_or_unsafe"
        );
        std::fs::remove_file(&file)?;
        std::fs::write(&file, bytes)?;
        let alias = root.path().join("alias.bin");
        std::fs::hard_link(&file, &alias)?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].verification,
            "unavailable_or_unsafe"
        );
        std::fs::remove_file(alias)?;
        assert_eq!(
            observe(root.path(), &result, "t", "r", "b").evidence[0].content_sha256,
            Some(expected)
        );
        Ok(())
    }
}
