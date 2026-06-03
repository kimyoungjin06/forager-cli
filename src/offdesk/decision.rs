//! Canonical Offdesk decision records.
//!
//! Decision records sit above approval briefs and pending approvals. They
//! describe what needs to be decided and how the decision was routed; they do
//! not authorize runtime or canonical mutation by themselves.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use super::approval::ApprovalBrief;

pub const DECISION_RECORD_SCHEMA: &str = "decision_record.v1";
pub const JUDGMENT_ROUTE_SCHEMA: &str = "judgment_route.v1";
const DECISIONS_FILE: &str = "offdesk_decisions.jsonl";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionRaisedBy {
    Agent,
    Council,
    Runtime,
    Closeout,
    Operator,
}

impl DecisionRaisedBy {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Agent => "agent",
            Self::Council => "council",
            Self::Runtime => "runtime",
            Self::Closeout => "closeout",
            Self::Operator => "operator",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionMateriality {
    Low,
    Medium,
    High,
}

impl DecisionMateriality {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Low => "low",
            Self::Medium => "medium",
            Self::High => "high",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionStatus {
    Draft,
    CouncilReview,
    AutoResolved,
    UserPending,
    Approved,
    Revised,
    Denied,
    Deferred,
    HandoffReady,
    Applied,
    Receipted,
}

impl DecisionStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Draft => "draft",
            Self::CouncilReview => "council_review",
            Self::AutoResolved => "auto_resolved",
            Self::UserPending => "user_pending",
            Self::Approved => "approved",
            Self::Revised => "revised",
            Self::Denied => "denied",
            Self::Deferred => "deferred",
            Self::HandoffReady => "handoff_ready",
            Self::Applied => "applied",
            Self::Receipted => "receipted",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionRouteTarget {
    Agent,
    User,
    ApprovalLedger,
    Closeout,
}

impl DecisionRouteTarget {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Agent => "agent",
            Self::User => "user",
            Self::ApprovalLedger => "approval_ledger",
            Self::Closeout => "closeout",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JudgmentEvaluator {
    Council,
    SingleHarness,
    DeterministicGate,
    User,
}

impl JudgmentEvaluator {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Council => "council",
            Self::SingleHarness => "single_harness",
            Self::DeterministicGate => "deterministic_gate",
            Self::User => "user",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionRecord {
    pub schema: String,
    pub decision_id: String,
    pub project_key: String,
    pub request_id: String,
    pub task_id: String,
    pub raised_by: DecisionRaisedBy,
    pub source_surface: String,
    pub materiality: DecisionMateriality,
    pub status: DecisionStatus,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub decision_request: DecisionRequest,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub council_review: Option<CouncilReview>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub judgment_route: Option<JudgmentRoute>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub route: Option<DecisionRoute>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approval_brief: Option<ApprovalBrief>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub execution_handoff: Option<ExecutionHandoff>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decision_receipt: Option<DecisionReceipt>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub trace_refs: Vec<DecisionTraceRef>,
}

impl DecisionRecord {
    pub fn validation_issues(&self) -> Vec<DecisionValidationIssue> {
        let mut issues = Vec::new();
        if self.schema != DECISION_RECORD_SCHEMA {
            issues.push(DecisionValidationIssue::error(
                "schema_mismatch",
                format!("expected {DECISION_RECORD_SCHEMA}, found {}", self.schema),
            ));
        }
        if self.decision_id.trim().is_empty() {
            issues.push(DecisionValidationIssue::error(
                "decision_id_missing",
                "decision_id is required",
            ));
        }
        if self.project_key.trim().is_empty() {
            issues.push(DecisionValidationIssue::error(
                "project_key_missing",
                "project_key is required",
            ));
        }
        if self.request_id.trim().is_empty() {
            issues.push(DecisionValidationIssue::error(
                "request_id_missing",
                "request_id is required",
            ));
        }
        if self.task_id.trim().is_empty() {
            issues.push(DecisionValidationIssue::error(
                "task_id_missing",
                "task_id is required",
            ));
        }
        if self.status == DecisionStatus::UserPending && self.approval_brief.is_none() {
            issues.push(DecisionValidationIssue::error(
                "user_pending_without_approval_brief",
                "user_pending decisions need an approval_brief projection",
            ));
        }
        if self.status == DecisionStatus::HandoffReady && self.execution_handoff.is_none() {
            issues.push(DecisionValidationIssue::error(
                "handoff_ready_without_execution_handoff",
                "handoff_ready decisions need an execution_handoff",
            ));
        }
        if self.status == DecisionStatus::Applied && self.execution_handoff.is_none() {
            issues.push(DecisionValidationIssue::error(
                "applied_without_execution_handoff",
                "applied decisions need the handoff that was consumed",
            ));
        }
        if self.status == DecisionStatus::Receipted && self.decision_receipt.is_none() {
            issues.push(DecisionValidationIssue::error(
                "receipted_without_decision_receipt",
                "receipted decisions need a decision_receipt",
            ));
        }
        issues
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionRequest {
    pub kind: String,
    pub summary: String,
    pub decision_needed: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub why_now: Vec<String>,
    pub current_scope: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub non_authorized_scope: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub options: Vec<DecisionOption>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_refs: Vec<DecisionTraceRef>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub trace_refs: Vec<DecisionTraceRef>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionOption {
    pub id: String,
    pub label: String,
    pub description: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub impact: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub natural_input_prompt: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CouncilReview {
    pub recommendation: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agreement: Option<bool>,
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub reviewer_decisions: BTreeMap<String, String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_gaps: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub risk_notes: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub option_assessment: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JudgmentRoute {
    pub schema: String,
    pub evaluator: JudgmentEvaluator,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub policy_basis: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_refs: Vec<DecisionTraceRef>,
    pub selected_by: String,
    pub selected_at: DateTime<Utc>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_if_no_reply: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionRoute {
    pub materiality: DecisionMateriality,
    pub target: DecisionRouteTarget,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub policy_basis: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default_if_no_reply: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionHandoff {
    pub handoff_id: String,
    pub decision_id: String,
    pub target: String,
    pub approved_direction: String,
    pub approved_scope: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub instructions: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub constraints: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub verification_required: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub non_authorized_actions: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionReceipt {
    pub receipt_id: String,
    pub decision_id: String,
    pub resolved_by: String,
    pub resolved_at: DateTime<Utc>,
    pub final_decision: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub applied_handoff_id: Option<String>,
    pub authorization_summary: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_summary: Vec<String>,
    pub result_status: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub remaining_review: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionTraceRef {
    pub kind: String,
    pub label: String,
    pub reference: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionValidationSeverity {
    Error,
    Warning,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DecisionValidationIssue {
    pub severity: DecisionValidationSeverity,
    pub code: String,
    pub detail: String,
}

impl DecisionValidationIssue {
    fn error(code: impl Into<String>, detail: impl Into<String>) -> Self {
        Self {
            severity: DecisionValidationSeverity::Error,
            code: code.into(),
            detail: detail.into(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DecisionRecordView {
    pub record: DecisionRecord,
    pub validation_issues: Vec<DecisionValidationIssue>,
}

impl From<DecisionRecord> for DecisionRecordView {
    fn from(record: DecisionRecord) -> Self {
        let validation_issues = record.validation_issues();
        Self {
            record,
            validation_issues,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DecisionLedger {
    root: PathBuf,
}

impl DecisionLedger {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }

    pub fn path(&self) -> PathBuf {
        self.root.join(DECISIONS_FILE)
    }

    pub fn load(&self) -> Result<Vec<DecisionRecord>> {
        read_decision_records(&self.path())
    }

    pub fn append(&self, record: &DecisionRecord) -> Result<()> {
        append_jsonl(&self.path(), record)
    }

    pub fn find(&self, decision_id: &str) -> Result<Option<DecisionRecord>> {
        Ok(self
            .load()?
            .into_iter()
            .rev()
            .find(|record| record.decision_id == decision_id))
    }
}

fn read_decision_records(path: &Path) -> Result<Vec<DecisionRecord>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(path)
        .with_context(|| format!("read decision ledger {}", path.display()))?;
    let mut records = Vec::new();
    for (index, line) in content
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .enumerate()
    {
        records.push(serde_json::from_str(line).with_context(|| {
            format!(
                "parse decision ledger {} line {}",
                path.display(),
                index + 1
            )
        })?);
    }
    Ok(records)
}

fn append_jsonl<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(value)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use tempfile::tempdir;

    fn sample_record(status: DecisionStatus) -> DecisionRecord {
        let now = Utc::now();
        DecisionRecord {
            schema: DECISION_RECORD_SCHEMA.to_string(),
            decision_id: "decision-one".to_string(),
            project_key: "project".to_string(),
            request_id: "request".to_string(),
            task_id: "task".to_string(),
            raised_by: DecisionRaisedBy::Agent,
            source_surface: "test".to_string(),
            materiality: DecisionMateriality::High,
            status,
            created_at: now,
            updated_at: now,
            decision_request: DecisionRequest {
                kind: "council_escalation".to_string(),
                summary: "Council needs input.".to_string(),
                decision_needed: "Choose next direction.".to_string(),
                why_now: vec!["Council did not return continue.".to_string()],
                current_scope: "Next episode only.".to_string(),
                non_authorized_scope: vec!["cleanup".to_string()],
                options: Vec::new(),
                evidence_refs: Vec::new(),
                trace_refs: Vec::new(),
            },
            council_review: None,
            judgment_route: Some(JudgmentRoute {
                schema: JUDGMENT_ROUTE_SCHEMA.to_string(),
                evaluator: JudgmentEvaluator::Council,
                reason: "Council review is needed before user escalation.".to_string(),
                policy_basis: vec!["materiality=high".to_string()],
                evidence_refs: Vec::new(),
                selected_by: "test".to_string(),
                selected_at: now,
                default_if_no_reply: Some("defer".to_string()),
            }),
            route: Some(DecisionRoute {
                materiality: DecisionMateriality::High,
                target: DecisionRouteTarget::User,
                reason: "Changes next direction.".to_string(),
                policy_basis: Vec::new(),
                default_if_no_reply: Some("defer".to_string()),
                expires_at: None,
            }),
            approval_brief: None,
            execution_handoff: None,
            decision_receipt: None,
            trace_refs: Vec::new(),
        }
    }

    #[test]
    fn validation_requires_projection_for_user_pending() {
        let issues = sample_record(DecisionStatus::UserPending).validation_issues();
        assert!(issues
            .iter()
            .any(|issue| issue.code == "user_pending_without_approval_brief"));
    }

    #[test]
    fn ledger_reads_jsonl_records() -> Result<()> {
        let temp = tempdir()?;
        let ledger = DecisionLedger::new(temp.path());
        ledger.append(&sample_record(DecisionStatus::AutoResolved))?;
        ledger.append(&sample_record(DecisionStatus::Deferred))?;

        let records = ledger.load()?;
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].status, DecisionStatus::AutoResolved);
        assert_eq!(records[1].status, DecisionStatus::Deferred);
        assert_eq!(
            serde_json::to_value(DecisionRecordView::from(records[0].clone()))?
                ["validation_issues"],
            json!([])
        );
        Ok(())
    }
}
