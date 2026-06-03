//! Design-first implementation packets for delegated agent work.
//!
//! These records describe intent, alignment, scope, execution boundaries, and
//! closeout criteria before a substantial task is delegated. They do not grant
//! runtime mutation authority by themselves.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use super::redaction::operator_safe_text;

pub const IMPLEMENTATION_PACKET_SCHEMA: &str = "implementation_packet.v1";
pub const RECURSIVE_ALIGNMENT_REVIEW_SCHEMA: &str = "recursive_alignment_review.v1";
pub const IMPLEMENTATION_PACKETS_DIR: &str = "implementation_packets";
pub const IMPLEMENTATION_PACKET_FILE: &str = "IMPLEMENTATION_PACKET.json";
pub const RECURSIVE_ALIGNMENT_REVIEW_FILE: &str = "RECURSIVE_ALIGNMENT_REVIEW.json";
pub const IMPLEMENTATION_PACKET_MD_FILE: &str = "IMPLEMENTATION_PACKET.md";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationPacket {
    pub schema: String,
    pub packet_id: String,
    pub created_at: DateTime<Utc>,
    pub project_key: String,
    pub project_root: String,
    pub source_intent: ImplementationSourceIntent,
    pub alignment: ImplementationAlignment,
    pub scope: ImplementationScope,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub capability_mapping: Vec<ImplementationCapabilityMapping>,
    pub design: ImplementationDesign,
    pub execution: ImplementationExecution,
    pub validation: ImplementationValidation,
    pub closeout: ImplementationCloseout,
    pub recursive_alignment_review: RecursiveAlignmentReview,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationPacketSummary {
    pub packet_id: String,
    pub created_at: String,
    pub project_key: String,
    pub artifact_dir: String,
    pub packet_path: String,
    pub alignment_review_path: String,
    pub markdown_path: String,
    pub goal: String,
    pub success_state: String,
    pub preferred_worker: String,
    pub safe_to_delegate: bool,
    pub outcome: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub required_revisions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub drift_signals: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub missing_decisions: Vec<String>,
    pub work_slice_count: usize,
    pub capability_mapping_count: usize,
    pub validation_item_count: usize,
    pub stop_condition_count: usize,
    pub expected_artifact_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LatestImplementationPacket {
    pub created_at: DateTime<Utc>,
    pub summary: ImplementationPacketSummary,
    pub packet_path: PathBuf,
    pub alignment_review_path: PathBuf,
    pub markdown_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationSourceIntent {
    pub user_goal: String,
    pub why_now: String,
    pub success_state: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationAlignment {
    pub north_star_fit: String,
    pub brand_fit: String,
    pub product_boundary: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub anti_drift_notes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationScope {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub included: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub excluded: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub allowed_files: Vec<String>,
    pub mutation_boundary: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub non_authorized_actions: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationCapabilityMapping {
    pub capability_id: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationDesign {
    pub approach: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub work_slices: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub interfaces: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub data_contracts: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub compatibility_notes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationExecution {
    pub preferred_worker: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub worker_requirements: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub commands: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub stop_conditions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rollback_or_recovery: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationValidation {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tests: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub smoke_checks: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub manual_review: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_required: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImplementationCloseout {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub expected_artifacts: Vec<String>,
    pub accepted_truth_rule: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub handoff_summary_requirements: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecursiveAlignmentReview {
    pub schema: String,
    pub reviewer: String,
    pub outcome: AlignmentReviewOutcome,
    pub checks: RecursiveAlignmentChecks,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub drift_signals: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub missing_decisions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub required_revisions: Vec<String>,
    pub safe_to_delegate: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AlignmentReviewOutcome {
    Pass,
    Revise,
    Block,
}

impl AlignmentReviewOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pass => "pass",
            Self::Revise => "revise",
            Self::Block => "block",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RecursiveAlignmentChecks {
    pub original_goal_coverage: String,
    pub north_star_alignment: String,
    pub brand_alignment: String,
    pub scope_balance: String,
    pub capability_coverage: String,
    pub evidence_sufficiency: String,
    pub completion_definition: String,
}

#[derive(Debug, Clone)]
pub struct ImplementationPacketDraftInput {
    pub packet_id: String,
    pub created_at: DateTime<Utc>,
    pub project_key: String,
    pub project_root: String,
    pub source_intent: ImplementationSourceIntent,
    pub alignment: ImplementationAlignment,
    pub scope: ImplementationScope,
    pub capability_mapping: Vec<ImplementationCapabilityMapping>,
    pub design: ImplementationDesign,
    pub execution: ImplementationExecution,
    pub validation: ImplementationValidation,
    pub closeout: ImplementationCloseout,
    pub reviewer: String,
}

pub fn draft_implementation_packet(input: ImplementationPacketDraftInput) -> ImplementationPacket {
    let recursive_alignment_review = review_alignment(&input);
    ImplementationPacket {
        schema: IMPLEMENTATION_PACKET_SCHEMA.to_string(),
        packet_id: input.packet_id,
        created_at: input.created_at,
        project_key: input.project_key,
        project_root: input.project_root,
        source_intent: input.source_intent,
        alignment: input.alignment,
        scope: input.scope,
        capability_mapping: input.capability_mapping,
        design: input.design,
        execution: input.execution,
        validation: input.validation,
        closeout: input.closeout,
        recursive_alignment_review,
    }
}

pub fn latest_implementation_packet_for_project(
    profile_dir: &Path,
    project_key: Option<&str>,
) -> Result<Option<LatestImplementationPacket>> {
    let packets_dir = profile_dir.join(IMPLEMENTATION_PACKETS_DIR);
    if !packets_dir.exists() {
        return Ok(None);
    }

    let mut candidates = Vec::new();
    for entry in fs::read_dir(&packets_dir).with_context(|| {
        format!(
            "read implementation packet directory {}",
            packets_dir.display()
        )
    })? {
        let entry = entry?;
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let artifact_dir = entry.path();
        let Ok(packet) = implementation_packet_from_artifact_dir(&artifact_dir) else {
            continue;
        };
        if !implementation_packet_matches_project(&packet, project_key) {
            continue;
        }
        candidates.push((packet.created_at, packet));
    }

    candidates.sort_by_key(|(created_at, _)| *created_at);
    Ok(candidates.pop().map(|(_, packet)| packet))
}

pub fn implementation_packet_from_path(path: &Path) -> Result<LatestImplementationPacket> {
    if path.is_dir() {
        return implementation_packet_from_artifact_dir(path);
    }
    let artifact_dir = path
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    implementation_packet_from_paths(
        path.to_path_buf(),
        artifact_dir.join(RECURSIVE_ALIGNMENT_REVIEW_FILE),
        artifact_dir.join(IMPLEMENTATION_PACKET_MD_FILE),
        artifact_dir,
    )
}

pub fn implementation_packet_record_from_path(path: &Path) -> Result<ImplementationPacket> {
    let packet_path = if path.is_dir() {
        path.join(IMPLEMENTATION_PACKET_FILE)
    } else {
        path.to_path_buf()
    };
    let packet_content = fs::read_to_string(&packet_path)
        .with_context(|| format!("read implementation packet {}", packet_path.display()))?;
    serde_json::from_str(&packet_content)
        .with_context(|| format!("parse implementation packet {}", packet_path.display()))
}

pub fn operator_safe_implementation_packet_summary(
    summary: &ImplementationPacketSummary,
) -> ImplementationPacketSummary {
    ImplementationPacketSummary {
        packet_id: operator_safe_text(&summary.packet_id),
        created_at: operator_safe_text(&summary.created_at),
        project_key: operator_safe_text(&summary.project_key),
        artifact_dir: operator_safe_text(&summary.artifact_dir),
        packet_path: operator_safe_text(&summary.packet_path),
        alignment_review_path: operator_safe_text(&summary.alignment_review_path),
        markdown_path: operator_safe_text(&summary.markdown_path),
        goal: operator_safe_text(&summary.goal),
        success_state: operator_safe_text(&summary.success_state),
        preferred_worker: operator_safe_text(&summary.preferred_worker),
        safe_to_delegate: summary.safe_to_delegate,
        outcome: operator_safe_text(&summary.outcome),
        required_revisions: summary
            .required_revisions
            .iter()
            .map(|value| operator_safe_text(value))
            .collect(),
        drift_signals: summary
            .drift_signals
            .iter()
            .map(|value| operator_safe_text(value))
            .collect(),
        missing_decisions: summary
            .missing_decisions
            .iter()
            .map(|value| operator_safe_text(value))
            .collect(),
        work_slice_count: summary.work_slice_count,
        capability_mapping_count: summary.capability_mapping_count,
        validation_item_count: summary.validation_item_count,
        stop_condition_count: summary.stop_condition_count,
        expected_artifact_count: summary.expected_artifact_count,
    }
}

fn implementation_packet_from_artifact_dir(
    artifact_dir: &Path,
) -> Result<LatestImplementationPacket> {
    implementation_packet_from_paths(
        artifact_dir.join(IMPLEMENTATION_PACKET_FILE),
        artifact_dir.join(RECURSIVE_ALIGNMENT_REVIEW_FILE),
        artifact_dir.join(IMPLEMENTATION_PACKET_MD_FILE),
        artifact_dir.to_path_buf(),
    )
}

fn implementation_packet_from_paths(
    packet_path: PathBuf,
    alignment_review_path: PathBuf,
    markdown_path: PathBuf,
    artifact_dir: PathBuf,
) -> Result<LatestImplementationPacket> {
    let packet = implementation_packet_record_from_path(&packet_path)?;
    let summary = summarize_implementation_packet(
        &packet,
        &artifact_dir,
        &packet_path,
        &alignment_review_path,
        &markdown_path,
    );
    Ok(LatestImplementationPacket {
        created_at: packet.created_at,
        summary,
        packet_path,
        alignment_review_path,
        markdown_path,
    })
}

fn implementation_packet_matches_project(
    packet: &LatestImplementationPacket,
    project_key: Option<&str>,
) -> bool {
    match project_key {
        Some(project_key) => packet.summary.project_key == project_key,
        None => true,
    }
}

fn summarize_implementation_packet(
    packet: &ImplementationPacket,
    artifact_dir: &Path,
    packet_path: &Path,
    alignment_review_path: &Path,
    markdown_path: &Path,
) -> ImplementationPacketSummary {
    ImplementationPacketSummary {
        packet_id: operator_safe_text(&packet.packet_id),
        created_at: packet.created_at.to_rfc3339(),
        project_key: operator_safe_text(&packet.project_key),
        artifact_dir: operator_safe_text(artifact_dir.to_string_lossy().as_ref()),
        packet_path: operator_safe_text(packet_path.to_string_lossy().as_ref()),
        alignment_review_path: operator_safe_text(alignment_review_path.to_string_lossy().as_ref()),
        markdown_path: operator_safe_text(markdown_path.to_string_lossy().as_ref()),
        goal: operator_safe_text(&packet.source_intent.user_goal),
        success_state: operator_safe_text(&packet.source_intent.success_state),
        preferred_worker: operator_safe_text(&packet.execution.preferred_worker),
        safe_to_delegate: packet.recursive_alignment_review.safe_to_delegate,
        outcome: packet
            .recursive_alignment_review
            .outcome
            .as_str()
            .to_string(),
        required_revisions: packet
            .recursive_alignment_review
            .required_revisions
            .iter()
            .map(|revision| operator_safe_text(revision))
            .collect(),
        drift_signals: packet
            .recursive_alignment_review
            .drift_signals
            .iter()
            .map(|signal| operator_safe_text(signal))
            .collect(),
        missing_decisions: packet
            .recursive_alignment_review
            .missing_decisions
            .iter()
            .map(|decision| operator_safe_text(decision))
            .collect(),
        work_slice_count: packet.design.work_slices.len(),
        capability_mapping_count: packet.capability_mapping.len(),
        validation_item_count: packet.validation.tests.len()
            + packet.validation.smoke_checks.len()
            + packet.validation.manual_review.len()
            + packet.validation.evidence_required.len(),
        stop_condition_count: packet.execution.stop_conditions.len(),
        expected_artifact_count: packet.closeout.expected_artifacts.len(),
    }
}

fn review_alignment(input: &ImplementationPacketDraftInput) -> RecursiveAlignmentReview {
    let mut required_revisions = Vec::new();
    let mut drift_signals = Vec::new();
    let mut missing_decisions = Vec::new();

    if input.source_intent.user_goal.trim().is_empty()
        || input.source_intent.success_state.trim().is_empty()
    {
        required_revisions.push("source_intent_missing".to_string());
    }
    if input.scope.included.is_empty() {
        required_revisions.push("included_scope_missing".to_string());
    }
    if input.scope.excluded.is_empty() {
        required_revisions.push("excluded_scope_missing".to_string());
        drift_signals.push("scope_may_expand_without_named_non_goals".to_string());
    }
    if input.design.work_slices.is_empty() {
        required_revisions.push("work_slices_missing".to_string());
    }
    if input.execution.stop_conditions.is_empty() {
        required_revisions.push("stop_conditions_missing".to_string());
    }
    if input.validation.tests.is_empty()
        && input.validation.smoke_checks.is_empty()
        && input.validation.manual_review.is_empty()
    {
        required_revisions.push("validation_plan_missing".to_string());
    }
    if input.closeout.expected_artifacts.is_empty() {
        required_revisions.push("expected_artifacts_missing".to_string());
    }
    if input.capability_mapping.is_empty() {
        missing_decisions.push("affected_functional_capabilities_not_mapped".to_string());
    }
    if input.alignment.north_star_fit.trim().is_empty() {
        required_revisions.push("north_star_fit_missing".to_string());
        drift_signals.push("north_star_alignment_not_explained".to_string());
    }
    if input.alignment.brand_fit.trim().is_empty() {
        required_revisions.push("brand_fit_missing".to_string());
        drift_signals.push("brand_alignment_not_explained".to_string());
    }

    let safe_to_delegate = required_revisions.is_empty();
    let outcome = if safe_to_delegate {
        AlignmentReviewOutcome::Pass
    } else if input.source_intent.user_goal.trim().is_empty() {
        AlignmentReviewOutcome::Block
    } else {
        AlignmentReviewOutcome::Revise
    };

    RecursiveAlignmentReview {
        schema: RECURSIVE_ALIGNMENT_REVIEW_SCHEMA.to_string(),
        reviewer: input.reviewer.clone(),
        outcome,
        checks: RecursiveAlignmentChecks {
            original_goal_coverage: if input.source_intent.user_goal.trim().is_empty()
                || input.source_intent.success_state.trim().is_empty()
            {
                "missing".to_string()
            } else {
                "complete".to_string()
            },
            north_star_alignment: alignment_strength(&input.alignment.north_star_fit),
            brand_alignment: alignment_strength(&input.alignment.brand_fit),
            scope_balance: if input.scope.included.is_empty() || input.scope.excluded.is_empty() {
                "partial".to_string()
            } else {
                "right_sized".to_string()
            },
            capability_coverage: if input.capability_mapping.is_empty() {
                "partial".to_string()
            } else {
                "complete".to_string()
            },
            evidence_sufficiency: if input.validation.evidence_required.is_empty() {
                "partial".to_string()
            } else {
                "sufficient".to_string()
            },
            completion_definition: if input.validation.tests.is_empty()
                && input.validation.smoke_checks.is_empty()
                && input.validation.manual_review.is_empty()
            {
                "missing".to_string()
            } else {
                "testable".to_string()
            },
        },
        drift_signals,
        missing_decisions,
        required_revisions,
        safe_to_delegate,
    }
}

fn alignment_strength(value: &str) -> String {
    if value.trim().is_empty() {
        "weak".to_string()
    } else {
        "acceptable".to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base_input() -> ImplementationPacketDraftInput {
        ImplementationPacketDraftInput {
            packet_id: "implementation-packet-test".to_string(),
            created_at: Utc::now(),
            project_key: "forager-cli".to_string(),
            project_root: "/workspace/forager-cli".to_string(),
            source_intent: ImplementationSourceIntent {
                user_goal: "Make delegated work preserve original intent.".to_string(),
                why_now: "Local workers need a bounded packet.".to_string(),
                success_state: "The worker can run from JSON without chat scrollback.".to_string(),
            },
            alignment: ImplementationAlignment {
                north_star_fit: "Returns to evidence, choices, and continuity.".to_string(),
                brand_fit: "Keeps Forager as a meta-harness.".to_string(),
                product_boundary: "Forager owns supervision, not agent reasoning.".to_string(),
                anti_drift_notes: Vec::new(),
            },
            scope: ImplementationScope {
                included: vec!["typed packet".to_string()],
                excluded: vec!["runtime approval".to_string()],
                allowed_files: Vec::new(),
                mutation_boundary: "Read-only packet generation.".to_string(),
                non_authorized_actions: vec!["cleanup".to_string()],
            },
            capability_mapping: vec![ImplementationCapabilityMapping {
                capability_id: "FD-016".to_string(),
                reason: "Implementation packet capability.".to_string(),
            }],
            design: ImplementationDesign {
                approach: "Add typed state and CLI JSON.".to_string(),
                work_slices: vec!["state".to_string(), "cli".to_string()],
                interfaces: Vec::new(),
                data_contracts: vec!["implementation_packet.v1".to_string()],
                compatibility_notes: Vec::new(),
            },
            execution: ImplementationExecution {
                preferred_worker: "deterministic_script".to_string(),
                worker_requirements: Vec::new(),
                commands: Vec::new(),
                stop_conditions: vec!["schema mismatch".to_string()],
                rollback_or_recovery: Vec::new(),
            },
            validation: ImplementationValidation {
                tests: vec!["cargo test".to_string()],
                smoke_checks: Vec::new(),
                manual_review: Vec::new(),
                evidence_required: vec!["json artifact".to_string()],
            },
            closeout: ImplementationCloseout {
                expected_artifacts: vec!["IMPLEMENTATION_PACKET.json".to_string()],
                accepted_truth_rule: "Execution completion is not acceptance.".to_string(),
                handoff_summary_requirements: Vec::new(),
            },
            reviewer: "deterministic_gate".to_string(),
        }
    }

    #[test]
    fn complete_packet_is_safe_to_delegate() {
        let packet = draft_implementation_packet(base_input());
        assert_eq!(packet.schema, IMPLEMENTATION_PACKET_SCHEMA);
        assert_eq!(
            packet.recursive_alignment_review.outcome,
            AlignmentReviewOutcome::Pass
        );
        assert!(packet.recursive_alignment_review.safe_to_delegate);
        assert!(packet
            .recursive_alignment_review
            .required_revisions
            .is_empty());
    }

    #[test]
    fn incomplete_packet_requires_revision() {
        let mut input = base_input();
        input.execution.stop_conditions.clear();
        input.validation.tests.clear();
        let packet = draft_implementation_packet(input);
        assert_eq!(
            packet.recursive_alignment_review.outcome,
            AlignmentReviewOutcome::Revise
        );
        assert!(!packet.recursive_alignment_review.safe_to_delegate);
        assert!(packet
            .recursive_alignment_review
            .required_revisions
            .contains(&"stop_conditions_missing".to_string()));
        assert!(packet
            .recursive_alignment_review
            .required_revisions
            .contains(&"validation_plan_missing".to_string()));
    }
}
