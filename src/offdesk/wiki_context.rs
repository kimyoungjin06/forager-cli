//! Strict, side-effect-free contracts for presenting adaptive-wiki context.
//!
//! These types describe prepared context and observed presentation outcomes.
//! They do not grant wiki mutation authority and do not claim that an agent
//! understood or followed an entry.

use std::collections::HashSet;

use anyhow::{ensure, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const WIKI_CONTEXT_MANIFEST_SCHEMA: &str = "wiki_context_manifest.v1";
pub const WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA: &str = "wiki_context_presentation_receipt.v1";
const WIKI_CONTEXT_PROJECTION_HASH_SCHEMA: &str = "wiki_context_projection_hash.v1";

pub(crate) const MAX_CONTEXT_ENTRIES: usize = 12;
const MAX_WARNINGS: usize = 16;
const MAX_IDENTIFIER_CHARS: usize = 256;
const MAX_ENTRY_TEXT_CHARS: usize = 4096;
const MAX_WARNING_CHARS: usize = 512;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WikiContextAgentMode {
    Shared,
    Planning,
    Development,
    Analysis,
    Writing,
    Critique,
    Review,
    Maintenance,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextManifestV1 {
    pub schema: String,
    pub generated_at: DateTime<Utc>,
    pub outcome: WikiContextManifestOutcomeV1,
}

impl WikiContextManifestV1 {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == WIKI_CONTEXT_MANIFEST_SCHEMA,
            "wiki context manifest schema must be {WIKI_CONTEXT_MANIFEST_SCHEMA}"
        );
        match &self.outcome {
            WikiContextManifestOutcomeV1::Fresh(projection) => {
                projection.validate()?;
                ensure!(
                    projection.stale_entry_count == 0,
                    "fresh wiki context cannot contain stale entries"
                );
            }
            WikiContextManifestOutcomeV1::Unchanged(projection) => projection.validate()?,
            WikiContextManifestOutcomeV1::StaleEntriesPresent(projection) => {
                projection.validate()?;
                ensure!(
                    projection.stale_entry_count > 0,
                    "stale_entries_present requires at least one stale entry"
                );
            }
            WikiContextManifestOutcomeV1::UnregisteredProject(detail) => detail.validate()?,
            WikiContextManifestOutcomeV1::WikiProfileUnavailable(detail)
            | WikiContextManifestOutcomeV1::ScopeMismatch(detail)
            | WikiContextManifestOutcomeV1::UnsafeOutputPath(detail)
            | WikiContextManifestOutcomeV1::RefreshFailed(detail) => detail.validate()?,
        }
        Ok(())
    }

    /// Return the exact bounded projection when this manifest represents a
    /// usable brief. Failure outcomes deliberately expose no projection.
    pub fn projection(&self) -> Option<&WikiContextProjectionV1> {
        match &self.outcome {
            WikiContextManifestOutcomeV1::Fresh(projection)
            | WikiContextManifestOutcomeV1::Unchanged(projection)
            | WikiContextManifestOutcomeV1::StaleEntriesPresent(projection) => Some(projection),
            WikiContextManifestOutcomeV1::UnregisteredProject(_)
            | WikiContextManifestOutcomeV1::WikiProfileUnavailable(_)
            | WikiContextManifestOutcomeV1::ScopeMismatch(_)
            | WikiContextManifestOutcomeV1::UnsafeOutputPath(_)
            | WikiContextManifestOutcomeV1::RefreshFailed(_) => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "projection_status",
    content = "detail",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum WikiContextManifestOutcomeV1 {
    Fresh(WikiContextProjectionV1),
    Unchanged(WikiContextProjectionV1),
    StaleEntriesPresent(WikiContextProjectionV1),
    UnregisteredProject(WikiContextUnregisteredProjectV1),
    WikiProfileUnavailable(WikiContextBoundFailureV1),
    ScopeMismatch(WikiContextBoundFailureV1),
    UnsafeOutputPath(WikiContextBoundFailureV1),
    RefreshFailed(WikiContextBoundFailureV1),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextProjectionV1 {
    pub project_key: String,
    pub wiki_profile: String,
    pub project_root_identity: String,
    pub agent_mode: WikiContextAgentMode,
    pub projection_sha256: String,
    pub brief_file_sha256: String,
    pub entry_ids: Vec<String>,
    pub entry_count: usize,
    pub stale_entry_count: usize,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub warnings: Vec<String>,
}

impl WikiContextProjectionV1 {
    pub fn validate(&self) -> Result<()> {
        validate_text(&self.project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.wiki_profile, "wiki_profile", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.project_root_identity,
            "project_root_identity",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(&self.projection_sha256, "projection_sha256")?;
        validate_sha256(&self.brief_file_sha256, "brief_file_sha256")?;
        ensure!(
            self.entry_count == self.entry_ids.len(),
            "entry_count must equal entry_ids length"
        );
        ensure!(
            self.entry_count <= MAX_CONTEXT_ENTRIES,
            "entry_count exceeds the wiki context limit"
        );
        ensure!(
            self.stale_entry_count <= self.entry_count,
            "stale_entry_count cannot exceed entry_count"
        );
        let mut unique_ids = HashSet::new();
        for entry_id in &self.entry_ids {
            validate_text(entry_id, "entry_id", MAX_IDENTIFIER_CHARS)?;
            ensure!(
                unique_ids.insert(entry_id),
                "entry_ids must not contain duplicates"
            );
        }
        validate_warnings(&self.warnings, false)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextUnregisteredProjectV1 {
    pub requested_project_root_sha256: String,
    pub warnings: Vec<String>,
}

impl WikiContextUnregisteredProjectV1 {
    fn validate(&self) -> Result<()> {
        validate_sha256(
            &self.requested_project_root_sha256,
            "requested_project_root_sha256",
        )?;
        validate_warnings(&self.warnings, true)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextBoundFailureV1 {
    pub project_key: String,
    pub wiki_profile: String,
    pub project_root_identity: String,
    pub warnings: Vec<String>,
}

impl WikiContextBoundFailureV1 {
    fn validate(&self) -> Result<()> {
        validate_text(&self.project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.wiki_profile, "wiki_profile", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.project_root_identity,
            "project_root_identity",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_warnings(&self.warnings, true)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextProjectionEntryV1 {
    pub id: String,
    pub kind: String,
    pub text: String,
    pub confidence: String,
    pub evidence_count: usize,
    pub stale: bool,
}

impl WikiContextProjectionEntryV1 {
    fn validate(&self) -> Result<()> {
        validate_text(&self.id, "entry id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.kind, "entry kind", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.text, "entry text", MAX_ENTRY_TEXT_CHARS)?;
        validate_text(&self.confidence, "entry confidence", MAX_IDENTIFIER_CHARS)
    }
}

#[derive(Serialize)]
struct WikiContextProjectionHashInput<'a> {
    schema: &'static str,
    project_key: &'a str,
    wiki_profile: &'a str,
    agent_mode: WikiContextAgentMode,
    entries: Vec<&'a WikiContextProjectionEntryV1>,
}

/// Hash the canonical selected-entry projection without generation time.
///
/// Sorting by entry ID makes the identity independent of store enumeration.
/// The stale bit remains part of the hash because crossing a review boundary
/// changes the advisory context even when canonical entry text is unchanged.
pub fn wiki_context_projection_sha256(
    project_key: &str,
    wiki_profile: &str,
    agent_mode: WikiContextAgentMode,
    entries: &[WikiContextProjectionEntryV1],
) -> Result<String> {
    validate_text(project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
    validate_text(wiki_profile, "wiki_profile", MAX_IDENTIFIER_CHARS)?;
    ensure!(
        entries.len() <= MAX_CONTEXT_ENTRIES,
        "projection entries exceed the wiki context limit"
    );
    for entry in entries {
        entry.validate()?;
    }
    let mut canonical_entries = entries.iter().collect::<Vec<_>>();
    canonical_entries.sort_by(|left, right| left.id.cmp(&right.id));
    for pair in canonical_entries.windows(2) {
        ensure!(
            pair[0].id != pair[1].id,
            "projection entry IDs must be unique"
        );
    }
    let canonical = serde_json::to_vec(&WikiContextProjectionHashInput {
        schema: WIKI_CONTEXT_PROJECTION_HASH_SCHEMA,
        project_key,
        wiki_profile,
        agent_mode,
        entries: canonical_entries,
    })?;
    Ok(sha256_hex(&canonical))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WikiContextAgentTool {
    Claude,
    Codex,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WikiContextPresentationStatus {
    PreparedOnly,
    Presented,
    UnchangedNoMessage,
    DeferredBusy,
    TargetAmbiguous,
    NoEffect,
    AgentAcknowledged,
    OperatorVerified,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextAgentAcknowledgementV1 {
    pub challenge_token: String,
    pub acknowledged_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextOperatorVerificationV1 {
    pub verified_by: String,
    pub verified_at: DateTime<Utc>,
    pub note: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WikiContextPresentationReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_runtime_identity_sha256: Option<String>,
    pub agent_tool: WikiContextAgentTool,
    pub project_key: String,
    pub wiki_profile: String,
    pub project_root_identity: String,
    pub agent_mode: WikiContextAgentMode,
    pub projection_sha256: String,
    pub brief_file_sha256: String,
    pub prepared_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempted_at: Option<DateTime<Utc>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub presented_at: Option<DateTime<Utc>>,
    pub presentation_status: WikiContextPresentationStatus,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pane_before_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pane_after_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_acknowledgement: Option<WikiContextAgentAcknowledgementV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operator_verification: Option<WikiContextOperatorVerificationV1>,
}

impl WikiContextPresentationReceiptV1 {
    pub fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA,
            "wiki context presentation receipt schema must be {WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA}"
        );
        validate_text(&self.receipt_id, "receipt_id", MAX_IDENTIFIER_CHARS)?;
        ensure!(
            self.receipt_id.starts_with("wiki_context_receipt_"),
            "receipt_id must use the wiki_context_receipt_ prefix"
        );
        if let Some(session_id) = &self.session_id {
            validate_text(session_id, "session_id", MAX_IDENTIFIER_CHARS)?;
        }
        if let Some(identity) = &self.session_runtime_identity_sha256 {
            validate_sha256(identity, "session_runtime_identity_sha256")?;
        }
        validate_text(&self.project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.wiki_profile, "wiki_profile", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.project_root_identity,
            "project_root_identity",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(&self.projection_sha256, "projection_sha256")?;
        validate_sha256(&self.brief_file_sha256, "brief_file_sha256")?;
        if let Some(attempted_at) = self.attempted_at {
            ensure!(
                attempted_at >= self.prepared_at,
                "attempted_at cannot precede prepared_at"
            );
        }
        if let Some(presented_at) = self.presented_at {
            let attempted_at = self
                .attempted_at
                .ok_or_else(|| anyhow::anyhow!("presented_at requires attempted_at"))?;
            ensure!(
                presented_at >= attempted_at,
                "presented_at cannot precede attempted_at"
            );
        }
        for (label, hash) in [
            ("pane_before_sha256", &self.pane_before_sha256),
            ("pane_after_sha256", &self.pane_after_sha256),
        ] {
            if let Some(hash) = hash {
                validate_sha256(hash, label)?;
            }
        }
        if let Some(acknowledgement) = &self.agent_acknowledgement {
            validate_text(
                &acknowledgement.challenge_token,
                "agent acknowledgement challenge_token",
                MAX_IDENTIFIER_CHARS,
            )?;
            ensure!(
                acknowledgement.challenge_token == self.projection_sha256,
                "agent acknowledgement challenge_token must equal projection_sha256"
            );
            let presented_at = self
                .presented_at
                .ok_or_else(|| anyhow::anyhow!("agent acknowledgement requires presented_at"))?;
            ensure!(
                acknowledgement.acknowledged_at >= presented_at,
                "acknowledged_at cannot precede presented_at"
            );
        }
        if let Some(verification) = &self.operator_verification {
            validate_text(
                &verification.verified_by,
                "operator verification verified_by",
                MAX_IDENTIFIER_CHARS,
            )?;
            validate_text(
                &verification.note,
                "operator verification note",
                MAX_WARNING_CHARS,
            )?;
            let presented_at = self
                .presented_at
                .ok_or_else(|| anyhow::anyhow!("operator verification requires presented_at"))?;
            ensure!(
                verification.verified_at >= presented_at,
                "verified_at cannot precede presented_at"
            );
        }
        self.validate_status_shape()
    }

    fn validate_status_shape(&self) -> Result<()> {
        let has_session = self.session_id.is_some();
        let has_runtime_identity = self.session_runtime_identity_sha256.is_some();
        let has_attempted_at = self.attempted_at.is_some();
        let has_presented_at = self.presented_at.is_some();
        let has_before = self.pane_before_sha256.is_some();
        let has_after = self.pane_after_sha256.is_some();
        let has_ack = self.agent_acknowledgement.is_some();
        let has_verification = self.operator_verification.is_some();
        ensure!(
            has_session == has_runtime_identity,
            "an exact session receipt must carry its tmux runtime identity"
        );
        match self.presentation_status {
            WikiContextPresentationStatus::PreparedOnly
            | WikiContextPresentationStatus::UnchangedNoMessage => ensure!(
                has_session
                    && !has_attempted_at
                    && !has_presented_at
                    && !has_before
                    && !has_after
                    && !has_ack
                    && !has_verification,
                "non-presented session receipt contains presentation evidence"
            ),
            WikiContextPresentationStatus::DeferredBusy => ensure!(
                has_session
                    && !has_attempted_at
                    && !has_presented_at
                    && has_before
                    && !has_after
                    && !has_ack
                    && !has_verification,
                "deferred_busy receipt has an invalid evidence shape"
            ),
            WikiContextPresentationStatus::TargetAmbiguous => ensure!(
                !has_session
                    && !has_runtime_identity
                    && !has_attempted_at
                    && !has_presented_at
                    && !has_before
                    && !has_after
                    && !has_ack
                    && !has_verification,
                "target_ambiguous receipt must not claim an exact session or presentation"
            ),
            WikiContextPresentationStatus::Presented => {
                ensure!(
                    has_session
                        && has_attempted_at
                        && has_presented_at
                        && has_before
                        && has_after
                        && !has_ack
                        && !has_verification,
                    "presented receipt has an invalid evidence shape"
                );
                ensure!(
                    self.pane_before_sha256 != self.pane_after_sha256,
                    "presented receipt requires an observed pane transition"
                );
            }
            WikiContextPresentationStatus::NoEffect => ensure!(
                has_session
                    && has_attempted_at
                    && !has_presented_at
                    && has_before
                    && has_after
                    && self.pane_before_sha256 == self.pane_after_sha256
                    && !has_ack
                    && !has_verification,
                "no_effect receipt requires equal before and after pane hashes"
            ),
            WikiContextPresentationStatus::AgentAcknowledged => ensure!(
                has_session
                    && has_attempted_at
                    && has_presented_at
                    && has_before
                    && has_after
                    && self.pane_before_sha256 != self.pane_after_sha256
                    && has_ack
                    && !has_verification,
                "agent_acknowledged receipt has an invalid evidence shape"
            ),
            WikiContextPresentationStatus::OperatorVerified => ensure!(
                has_session
                    && has_attempted_at
                    && has_presented_at
                    && has_before
                    && has_after
                    && self.pane_before_sha256 != self.pane_after_sha256
                    && has_verification,
                "operator_verified receipt has an invalid evidence shape"
            ),
        }
        Ok(())
    }
}

fn validate_warnings(warnings: &[String], required: bool) -> Result<()> {
    if required {
        ensure!(!warnings.is_empty(), "failure detail requires a warning");
    }
    ensure!(
        warnings.len() <= MAX_WARNINGS,
        "warnings exceed the wiki context limit"
    );
    for warning in warnings {
        validate_text(warning, "warning", MAX_WARNING_CHARS)?;
    }
    Ok(())
}

fn validate_text(value: &str, label: &str, max_chars: usize) -> Result<()> {
    ensure!(!value.is_empty(), "{label} must not be empty");
    ensure!(value.trim() == value, "{label} must be trimmed");
    ensure!(
        value.chars().count() <= max_chars,
        "{label} exceeds its character limit"
    );
    ensure!(
        !value.chars().any(char::is_control),
        "{label} must not contain control characters"
    );
    Ok(())
}

fn validate_sha256(value: &str, label: &str) -> Result<()> {
    ensure!(
        value.len() == 64
            && value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
        "{label} must be 64 lowercase hexadecimal characters"
    );
    Ok(())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn timestamp(second: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 21, 1, 2, second)
            .single()
            .expect("valid timestamp")
    }

    fn hash(character: char) -> String {
        std::iter::repeat_n(character, 64).collect()
    }

    fn projection(stale_entry_count: usize) -> WikiContextProjectionV1 {
        WikiContextProjectionV1 {
            project_key: "forager".to_string(),
            wiki_profile: "forager-ops".to_string(),
            project_root_identity: "unix:1:2".to_string(),
            agent_mode: WikiContextAgentMode::Shared,
            projection_sha256: hash('a'),
            brief_file_sha256: hash('b'),
            entry_ids: vec!["wiki_entry_one".to_string()],
            entry_count: 1,
            stale_entry_count,
            warnings: Vec::new(),
        }
    }

    fn bound_failure() -> WikiContextBoundFailureV1 {
        WikiContextBoundFailureV1 {
            project_key: "forager".to_string(),
            wiki_profile: "forager-ops".to_string(),
            project_root_identity: "unix:1:2".to_string(),
            warnings: vec!["bounded fixture failure".to_string()],
        }
    }

    #[test]
    fn every_manifest_status_has_a_valid_deterministic_fixture() {
        let outcomes = vec![
            WikiContextManifestOutcomeV1::Fresh(projection(0)),
            WikiContextManifestOutcomeV1::Unchanged(projection(0)),
            WikiContextManifestOutcomeV1::StaleEntriesPresent(projection(1)),
            WikiContextManifestOutcomeV1::UnregisteredProject(WikiContextUnregisteredProjectV1 {
                requested_project_root_sha256: hash('c'),
                warnings: vec!["project is not registered".to_string()],
            }),
            WikiContextManifestOutcomeV1::WikiProfileUnavailable(bound_failure()),
            WikiContextManifestOutcomeV1::ScopeMismatch(bound_failure()),
            WikiContextManifestOutcomeV1::UnsafeOutputPath(bound_failure()),
            WikiContextManifestOutcomeV1::RefreshFailed(bound_failure()),
        ];
        for outcome in outcomes {
            let manifest = WikiContextManifestV1 {
                schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
                generated_at: timestamp(3),
                outcome,
            };
            manifest.validate().expect("valid manifest fixture");
            let encoded = serde_json::to_vec(&manifest).expect("serialize manifest");
            let decoded: WikiContextManifestV1 =
                serde_json::from_slice(&encoded).expect("deserialize manifest");
            assert_eq!(decoded, manifest);
        }
    }

    #[test]
    fn manifest_rejects_status_contradictions_and_unknown_fields() {
        let mut manifest = WikiContextManifestV1 {
            schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
            generated_at: timestamp(3),
            outcome: WikiContextManifestOutcomeV1::Fresh(projection(1)),
        };
        assert!(manifest.validate().is_err());
        manifest.outcome = WikiContextManifestOutcomeV1::StaleEntriesPresent(projection(0));
        assert!(manifest.validate().is_err());

        let value = serde_json::json!({
            "schema": WIKI_CONTEXT_MANIFEST_SCHEMA,
            "generated_at": timestamp(3),
            "outcome": {
                "projection_status": "unregistered_project",
                "detail": {
                    "requested_project_root_sha256": hash('c'),
                    "warnings": ["project is not registered"],
                    "unexpected": true
                }
            }
        });
        assert!(serde_json::from_value::<WikiContextManifestV1>(value).is_err());

        let mut unknown_mode = serde_json::to_value(WikiContextManifestV1 {
            schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
            generated_at: timestamp(3),
            outcome: WikiContextManifestOutcomeV1::Fresh(projection(0)),
        })
        .expect("serialize manifest");
        unknown_mode["outcome"]["detail"]["agent_mode"] =
            serde_json::Value::String("all".to_string());
        assert!(serde_json::from_value::<WikiContextManifestV1>(unknown_mode).is_err());
    }

    #[test]
    fn empty_projection_is_explicit_and_valid() {
        let mut empty = projection(0);
        empty.entry_ids.clear();
        empty.entry_count = 0;
        WikiContextManifestV1 {
            schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
            generated_at: timestamp(3),
            outcome: WikiContextManifestOutcomeV1::Fresh(empty),
        }
        .validate()
        .expect("valid empty projection");
    }

    #[test]
    fn projection_hash_is_order_and_generation_time_independent() -> Result<()> {
        let first = WikiContextProjectionEntryV1 {
            id: "wiki_entry_a".to_string(),
            kind: "procedure".to_string(),
            text: "Check current evidence.".to_string(),
            confidence: "explicit".to_string(),
            evidence_count: 2,
            stale: false,
        };
        let second = WikiContextProjectionEntryV1 {
            id: "wiki_entry_b".to_string(),
            kind: "fact".to_string(),
            text: "The projection is advisory.".to_string(),
            confidence: "inferred".to_string(),
            evidence_count: 1,
            stale: true,
        };
        let forward = wiki_context_projection_sha256(
            "forager",
            "forager-ops",
            WikiContextAgentMode::Shared,
            &[first.clone(), second.clone()],
        )?;
        let reversed = wiki_context_projection_sha256(
            "forager",
            "forager-ops",
            WikiContextAgentMode::Shared,
            &[second.clone(), first.clone()],
        )?;
        assert_eq!(forward, reversed);

        let early = WikiContextManifestV1 {
            schema: WIKI_CONTEXT_MANIFEST_SCHEMA.to_string(),
            generated_at: timestamp(3),
            outcome: WikiContextManifestOutcomeV1::Unchanged(WikiContextProjectionV1 {
                projection_sha256: forward.clone(),
                ..projection(1)
            }),
        };
        let late = WikiContextManifestV1 {
            generated_at: timestamp(4),
            ..early.clone()
        };
        early.validate()?;
        late.validate()?;
        let early_projection = match early.outcome {
            WikiContextManifestOutcomeV1::Unchanged(value) => value.projection_sha256,
            _ => unreachable!(),
        };
        let late_projection = match late.outcome {
            WikiContextManifestOutcomeV1::Unchanged(value) => value.projection_sha256,
            _ => unreachable!(),
        };
        assert_eq!(early_projection, late_projection);

        let mode_specific = wiki_context_projection_sha256(
            "forager",
            "forager-ops",
            WikiContextAgentMode::Development,
            &[first.clone(), second],
        )?;
        assert_ne!(forward, mode_specific);
        assert!(wiki_context_projection_sha256(
            "forager",
            "forager-ops",
            WikiContextAgentMode::Shared,
            &[first.clone(), first],
        )
        .is_err());
        Ok(())
    }

    fn receipt(status: WikiContextPresentationStatus) -> WikiContextPresentationReceiptV1 {
        let mut receipt = WikiContextPresentationReceiptV1 {
            schema: WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA.to_string(),
            receipt_id: "wiki_context_receipt_fixture".to_string(),
            session_id: Some("session-one".to_string()),
            session_runtime_identity_sha256: Some(hash('e')),
            agent_tool: WikiContextAgentTool::Codex,
            project_key: "forager".to_string(),
            wiki_profile: "forager-ops".to_string(),
            project_root_identity: "unix:1:2".to_string(),
            agent_mode: WikiContextAgentMode::Shared,
            projection_sha256: hash('a'),
            brief_file_sha256: hash('b'),
            prepared_at: timestamp(1),
            attempted_at: None,
            presented_at: None,
            presentation_status: status,
            pane_before_sha256: None,
            pane_after_sha256: None,
            agent_acknowledgement: None,
            operator_verification: None,
        };
        match status {
            WikiContextPresentationStatus::PreparedOnly
            | WikiContextPresentationStatus::UnchangedNoMessage => {}
            WikiContextPresentationStatus::DeferredBusy => {
                receipt.pane_before_sha256 = Some(hash('c'));
            }
            WikiContextPresentationStatus::TargetAmbiguous => {
                receipt.session_id = None;
                receipt.session_runtime_identity_sha256 = None;
            }
            WikiContextPresentationStatus::Presented => {
                receipt.attempted_at = Some(timestamp(2));
                receipt.presented_at = Some(timestamp(2));
                receipt.pane_before_sha256 = Some(hash('c'));
                receipt.pane_after_sha256 = Some(hash('d'));
            }
            WikiContextPresentationStatus::NoEffect => {
                receipt.attempted_at = Some(timestamp(2));
                receipt.pane_before_sha256 = Some(hash('c'));
                receipt.pane_after_sha256 = Some(hash('c'));
            }
            WikiContextPresentationStatus::AgentAcknowledged => {
                receipt.attempted_at = Some(timestamp(2));
                receipt.presented_at = Some(timestamp(2));
                receipt.pane_before_sha256 = Some(hash('c'));
                receipt.pane_after_sha256 = Some(hash('d'));
                receipt.agent_acknowledgement = Some(WikiContextAgentAcknowledgementV1 {
                    challenge_token: hash('a'),
                    acknowledged_at: timestamp(3),
                });
            }
            WikiContextPresentationStatus::OperatorVerified => {
                receipt.attempted_at = Some(timestamp(2));
                receipt.presented_at = Some(timestamp(2));
                receipt.pane_before_sha256 = Some(hash('c'));
                receipt.pane_after_sha256 = Some(hash('d'));
                receipt.operator_verification = Some(WikiContextOperatorVerificationV1 {
                    verified_by: "local-owner".to_string(),
                    verified_at: timestamp(3),
                    note: "Observed the entry ID in the response.".to_string(),
                });
            }
        }
        receipt
    }

    #[test]
    fn every_presentation_status_has_a_valid_deterministic_fixture() {
        let statuses = [
            WikiContextPresentationStatus::PreparedOnly,
            WikiContextPresentationStatus::Presented,
            WikiContextPresentationStatus::UnchangedNoMessage,
            WikiContextPresentationStatus::DeferredBusy,
            WikiContextPresentationStatus::TargetAmbiguous,
            WikiContextPresentationStatus::NoEffect,
            WikiContextPresentationStatus::AgentAcknowledged,
            WikiContextPresentationStatus::OperatorVerified,
        ];
        for status in statuses {
            let fixture = receipt(status);
            fixture.validate().expect("valid receipt fixture");
            let encoded = serde_json::to_vec(&fixture).expect("serialize receipt");
            let decoded: WikiContextPresentationReceiptV1 =
                serde_json::from_slice(&encoded).expect("deserialize receipt");
            assert_eq!(decoded, fixture);
        }
    }

    #[test]
    fn receipt_rejects_false_presentation_and_comprehension_claims() {
        let mut prepared = receipt(WikiContextPresentationStatus::PreparedOnly);
        prepared.presented_at = Some(timestamp(2));
        assert!(prepared.validate().is_err());

        let mut presented = receipt(WikiContextPresentationStatus::Presented);
        presented.pane_after_sha256 = presented.pane_before_sha256.clone();
        assert!(presented.validate().is_err());

        let mut acknowledged = receipt(WikiContextPresentationStatus::AgentAcknowledged);
        acknowledged.agent_acknowledgement = None;
        assert!(acknowledged.validate().is_err());

        let mut wrong_challenge = receipt(WikiContextPresentationStatus::AgentAcknowledged);
        wrong_challenge
            .agent_acknowledgement
            .as_mut()
            .unwrap()
            .challenge_token = hash('f');
        assert!(wrong_challenge.validate().is_err());

        let mut missing_runtime = receipt(WikiContextPresentationStatus::PreparedOnly);
        missing_runtime.session_runtime_identity_sha256 = None;
        assert!(missing_runtime.validate().is_err());

        let mut verified = receipt(WikiContextPresentationStatus::OperatorVerified);
        verified.operator_verification = None;
        assert!(verified.validate().is_err());

        let mut ambiguous = receipt(WikiContextPresentationStatus::TargetAmbiguous);
        ambiguous.session_runtime_identity_sha256 = Some(hash('e'));
        assert!(ambiguous.validate().is_err());

        let mut unknown_field =
            serde_json::to_value(receipt(WikiContextPresentationStatus::PreparedOnly))
                .expect("serialize receipt");
        unknown_field["unexpected"] = serde_json::Value::Bool(true);
        assert!(serde_json::from_value::<WikiContextPresentationReceiptV1>(unknown_field).is_err());
    }
}
