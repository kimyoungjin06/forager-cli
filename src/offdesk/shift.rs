//! Immutable authority and state-transition contracts for a bounded Offdesk shift.
//!
//! A shift manifest selects exact queued work and fixed routine kinds. It does
//! not replace the existing per-task approval gate. Arming is a separate
//! receipt bound to the manifest hash, while mutable lifecycle is kept in a
//! sequence-checked state record.

use std::collections::HashSet;

use anyhow::{ensure, Context, Result};
use chrono::{DateTime, Datelike, Duration, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::approval::ExecutionBrief;
use super::background::{BackgroundProbe, BackgroundRunnerKind, BackgroundRunnerPhase};
use super::shift_enterprise::OffdeskShiftNotificationRouteV1;
use super::task_queue::{OffdeskTask, OffdeskTaskStatus};

pub const OFFDESK_SHIFT_MANIFEST_SCHEMA: &str = "offdesk_shift_manifest.v1";
pub const OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_SCHEMA: &str =
    "offdesk_shift_template_instantiation.v1";
pub const OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA: &str = "offdesk_shift_arm_receipt.v1";
pub const OFFDESK_SHIFT_STATE_SCHEMA: &str = "offdesk_shift_state.v1";
pub const OFFDESK_SHIFT_TRANSITION_RECEIPT_SCHEMA: &str = "offdesk_shift_transition_receipt.v1";
pub const OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA: &str =
    "offdesk_shift_task_launch_reservation.v1";
pub const OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA: &str =
    "offdesk_shift_task_launch_ticket_receipt.v1";
pub const OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA: &str = "offdesk_shift_task_outcome_receipt.v1";
pub const OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA: &str =
    "offdesk_shift_routine_run_reservation.v1";
pub const OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA: &str =
    "offdesk_shift_routine_outcome_receipt.v1";
pub const OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA: &str = "offdesk_shift_routine_usage.v1";
pub const OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA: &str =
    "offdesk_shift_routine_recovery_acknowledgement.v1";

const OFFDESK_SHIFT_MANIFEST_HASH_DOMAIN: &[u8] = b"forager.offdesk-shift.manifest.v1\0";
const OFFDESK_SHIFT_ARM_RECEIPT_HASH_DOMAIN: &[u8] = b"forager.offdesk-shift.arm-receipt.v1\0";
const OFFDESK_TASK_LAUNCH_SPEC_HASH_DOMAIN: &[u8] = b"forager.offdesk-shift.task-launch-spec.v1\0";
const EXECUTION_BRIEF_HASH_DOMAIN: &[u8] = b"forager.offdesk-shift.execution-brief.v1\0";
const OFFDESK_SHIFT_TASK_TICKET_RECEIPT_HASH_DOMAIN: &[u8] =
    b"forager.offdesk-shift.task-ticket-receipt.v1\0";
const OFFDESK_SHIFT_BACKGROUND_PROBE_HASH_DOMAIN: &[u8] =
    b"forager.offdesk-shift.background-probe.v1\0";

const MAX_SHIFT_DURATION: Duration = Duration::hours(24);
const MAX_TASK_BINDINGS: usize = 128;
const MAX_ROUTINE_BINDINGS: usize = 64;
const MAX_ROUTINE_RUNS: u16 = 512;
const MAX_DENIALS: usize = 32;
const MAX_IDENTIFIER_CHARS: usize = 256;
const MAX_PATH_CHARS: usize = 4096;
const MAX_REASON_CHARS: usize = 2_000;
const MAX_MUTATION_CLASSES: usize = 64;
const MAX_ROUTINE_EVIDENCE_REFS: usize = 16;
const MAX_TEMPLATE_KEY_CHARS: usize = 48;

pub const OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS: [&str; 4] = [
    "routine_reexecution",
    "successful_outcome",
    "accepted_truth",
    "external_side_effect",
];

pub const OFFDESK_SHIFT_REQUIRED_DENIALS: [&str; 9] = [
    "task_dispatch_without_fresh_approval",
    "command_or_workdir_change",
    "provider_retargeting",
    "canonical_mutation",
    "destructive_action",
    "unlisted_external_side_effect",
    "wiki_promotion",
    "accepted_truth",
    "plan_revision_without_review",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftTemplateCadenceV1 {
    Daily,
    Weekly,
}

impl OffdeskShiftTemplateCadenceV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Daily => "daily",
            Self::Weekly => "weekly",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTemplateInstantiationV1 {
    pub schema: String,
    pub template_key: String,
    pub cadence: OffdeskShiftTemplateCadenceV1,
    pub slot: String,
    pub slot_basis: String,
    pub recurrence_authorized: bool,
    pub next_occurrence_requires_new_manifest: bool,
    pub arm_approval_required: bool,
}

impl OffdeskShiftTemplateInstantiationV1 {
    pub fn new(
        template_key: &str,
        cadence: OffdeskShiftTemplateCadenceV1,
        starts_at: DateTime<Utc>,
    ) -> Result<Self> {
        Ok(Self {
            schema: OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_SCHEMA.to_string(),
            template_key: normalize_offdesk_shift_template_key(template_key)?,
            cadence,
            slot: offdesk_shift_template_slot(cadence, starts_at),
            slot_basis: "window_starts_at_utc".to_string(),
            recurrence_authorized: false,
            next_occurrence_requires_new_manifest: true,
            arm_approval_required: true,
        })
    }

    fn validate_against(&self, shift_id: &str, starts_at: DateTime<Utc>) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_SCHEMA,
            "shift template instantiation schema must be {OFFDESK_SHIFT_TEMPLATE_INSTANTIATION_SCHEMA}"
        );
        ensure!(
            self.template_key == normalize_offdesk_shift_template_key(&self.template_key)?,
            "shift template key must be canonical"
        );
        ensure!(
            self.slot == offdesk_shift_template_slot(self.cadence, starts_at),
            "shift template slot does not match the manifest window"
        );
        ensure!(
            self.slot_basis == "window_starts_at_utc",
            "shift template slot basis must be window_starts_at_utc"
        );
        ensure!(
            !self.recurrence_authorized,
            "shift template instantiation must not authorize recurrence"
        );
        ensure!(
            self.next_occurrence_requires_new_manifest,
            "shift template next occurrence must require a new manifest"
        );
        ensure!(
            self.arm_approval_required,
            "shift template instantiation must preserve arm approval"
        );
        ensure!(
            shift_id
                == offdesk_shift_template_shift_id(&self.template_key, self.cadence, starts_at,)?,
            "shift template manifest ID is not canonical"
        );
        Ok(())
    }
}

pub fn normalize_offdesk_shift_template_key(value: &str) -> Result<String> {
    let value = value.trim().to_ascii_lowercase();
    ensure!(
        !value.is_empty() && value.chars().count() <= MAX_TEMPLATE_KEY_CHARS,
        "shift template key must contain 1 to {MAX_TEMPLATE_KEY_CHARS} characters"
    );
    ensure!(
        value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-'),
        "shift template key may contain only lowercase ASCII letters, digits, and hyphens"
    );
    ensure!(
        value
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_alphanumeric())
            && value
                .bytes()
                .last()
                .is_some_and(|byte| byte.is_ascii_alphanumeric()),
        "shift template key must start and end with an ASCII letter or digit"
    );
    Ok(value)
}

pub fn offdesk_shift_template_slot(
    cadence: OffdeskShiftTemplateCadenceV1,
    starts_at: DateTime<Utc>,
) -> String {
    match cadence {
        OffdeskShiftTemplateCadenceV1::Daily => starts_at.format("%Y-%m-%d").to_string(),
        OffdeskShiftTemplateCadenceV1::Weekly => {
            let week = starts_at.iso_week();
            format!("{:04}-W{:02}", week.year(), week.week())
        }
    }
}

pub fn offdesk_shift_template_shift_id(
    template_key: &str,
    cadence: OffdeskShiftTemplateCadenceV1,
    starts_at: DateTime<Utc>,
) -> Result<String> {
    let template_key = normalize_offdesk_shift_template_key(template_key)?;
    let slot = offdesk_shift_template_slot(cadence, starts_at).replace('-', "");
    Ok(format!(
        "shift_template_{template_key}_{}_{slot}",
        cadence.as_str()
    ))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftWindowV1 {
    pub starts_at: DateTime<Utc>,
    pub stops_at: DateTime<Utc>,
    pub timezone: String,
}

impl OffdeskShiftWindowV1 {
    fn validate(&self) -> Result<()> {
        validate_text(&self.timezone, "shift timezone", MAX_IDENTIFIER_CHARS)?;
        ensure!(
            self.stops_at > self.starts_at,
            "shift stop time must be after its start time"
        );
        ensure!(
            self.stops_at - self.starts_at <= MAX_SHIFT_DURATION,
            "shift duration must not exceed 24 hours"
        );
        Ok(())
    }

    fn contains(&self, instant: DateTime<Utc>) -> bool {
        instant >= self.starts_at && instant <= self.stops_at
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftBudgetV1 {
    pub max_concurrent_tasks: u16,
    pub max_task_launches: u16,
    pub max_attempts_per_task: u16,
    pub max_routine_runs: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineResourceBudgetV1 {
    pub max_process_starts: u64,
    pub max_external_calls: u64,
    pub max_disk_bytes_written: u64,
    pub max_llm_tokens: u64,
    pub max_cost_micro_usd: u64,
    pub llm_cost_rate_micro_usd_per_million_tokens: u64,
}

impl OffdeskShiftRoutineResourceBudgetV1 {
    pub(crate) fn validate(&self, routine_count: usize) -> Result<()> {
        ensure!(
            routine_count > 0,
            "routine resource budget requires a routine"
        );
        ensure!(
            self.max_process_starts > 0 && self.max_process_starts <= 4096,
            "routine process budget must be between 1 and 4096"
        );
        ensure!(
            self.max_external_calls <= 4096,
            "routine external-call budget exceeds 4096"
        );
        ensure!(
            self.max_disk_bytes_written <= 1024 * 1024 * 1024,
            "routine disk budget exceeds 1 GiB"
        );
        ensure!(
            self.max_llm_tokens <= 100_000_000,
            "routine LLM token budget exceeds 100 million"
        );
        ensure!(
            self.max_cost_micro_usd <= 1_000_000_000,
            "routine cost budget exceeds 1000 USD"
        );
        ensure!(
            self.llm_cost_rate_micro_usd_per_million_tokens <= 1_000_000_000_000,
            "routine LLM cost rate exceeds its bound"
        );
        Ok(())
    }
}

impl OffdeskShiftBudgetV1 {
    fn validate(&self, task_count: usize, routine_count: usize) -> Result<()> {
        if task_count == 0 {
            ensure!(
                self.max_concurrent_tasks == 0
                    && self.max_task_launches == 0
                    && self.max_attempts_per_task == 0,
                "task budgets must be zero when a shift has no task bindings"
            );
        } else {
            ensure!(
                self.max_concurrent_tasks > 0,
                "max_concurrent_tasks must be positive when tasks are bound"
            );
            ensure!(
                usize::from(self.max_concurrent_tasks) <= task_count,
                "max_concurrent_tasks cannot exceed bound task count"
            );
            ensure!(
                self.max_task_launches > 0 && usize::from(self.max_task_launches) <= task_count,
                "max_task_launches must be positive and cannot exceed bound task count"
            );
            ensure!(
                self.max_concurrent_tasks <= self.max_task_launches,
                "max_concurrent_tasks cannot exceed max_task_launches"
            );
            ensure!(
                (1..=10).contains(&self.max_attempts_per_task),
                "max_attempts_per_task must be between 1 and 10"
            );
        }

        if routine_count == 0 {
            ensure!(
                self.max_routine_runs == 0,
                "max_routine_runs must be zero when a shift has no routine bindings"
            );
        } else {
            ensure!(
                self.max_routine_runs > 0 && self.max_routine_runs <= MAX_ROUTINE_RUNS,
                "max_routine_runs must be between 1 and {MAX_ROUTINE_RUNS}"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTaskBindingV1 {
    pub task_id: String,
    pub request_id: String,
    pub project_key: String,
    pub capability_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub project_root_identity_sha256: String,
    pub launch_spec_sha256: String,
    pub execution_brief_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ondesk_source_snapshot: Option<super::workflow::ondesk_task_binding::OndeskSourceSnapshot>,
}

impl OffdeskShiftTaskBindingV1 {
    fn validate(&self) -> Result<()> {
        validate_text(&self.task_id, "task_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.request_id, "request_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.capability_id, "capability_id", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(
            &self.project_root_identity_sha256,
            "project_root_identity_sha256",
        )?;
        validate_sha256(&self.launch_spec_sha256, "launch_spec_sha256")?;
        validate_sha256(&self.execution_brief_sha256, "execution_brief_sha256")?;
        if let Some(snapshot) = &self.ondesk_source_snapshot {
            snapshot.task(&self.project_key, &self.task_id)?;
        }
        Ok(())
    }

    pub fn from_task(
        task: &OffdeskTask,
        project_root_identity_sha256: &str,
        at: DateTime<Utc>,
    ) -> Result<Self> {
        let brief = task
            .execution_brief
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("shift task is missing an execution brief"))?;
        let binding = Self {
            task_id: task.task_id.clone(),
            request_id: task.request_id.clone(),
            project_key: task.project_key.clone(),
            capability_id: task.capability_id.clone(),
            runner_kind: task.runner_kind,
            project_root_identity_sha256: project_root_identity_sha256.to_string(),
            launch_spec_sha256: offdesk_task_launch_spec_sha256(task)?,
            execution_brief_sha256: execution_brief_sha256(brief)?,
            ondesk_source_snapshot: super::workflow::ondesk_task_binding::capture_task_source(
                task,
            )?,
        };
        binding.validate_against_task(task, project_root_identity_sha256, at)?;
        Ok(binding)
    }

    pub fn validate_against_task(
        &self,
        task: &OffdeskTask,
        current_project_root_identity_sha256: &str,
        at: DateTime<Utc>,
    ) -> Result<()> {
        self.validate_identity_against_task(task, current_project_root_identity_sha256)?;
        super::workflow::ondesk_task_binding::validate_task_source(task)?;
        ensure!(
            matches!(
                task.status,
                OffdeskTaskStatus::Queued | OffdeskTaskStatus::PendingApproval
            ),
            "shift task must still be queued or pending approval"
        );
        let brief = task
            .execution_brief
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("shift task is missing an execution brief"))?;
        ensure!(brief.approved, "shift task execution brief is not approved");
        ensure!(
            brief
                .fresh_until
                .is_none_or(|fresh_until| fresh_until >= at),
            "shift task execution brief is not fresh"
        );
        Ok(())
    }

    pub fn validate_identity_against_task(
        &self,
        task: &OffdeskTask,
        current_project_root_identity_sha256: &str,
    ) -> Result<()> {
        self.validate()?;
        validate_sha256(
            current_project_root_identity_sha256,
            "current project_root_identity_sha256",
        )?;
        ensure!(self.task_id == task.task_id, "shift task_id does not match");
        ensure!(
            self.request_id == task.request_id,
            "shift request_id does not match"
        );
        ensure!(
            self.project_key == task.project_key,
            "shift project_key does not match"
        );
        ensure!(
            self.capability_id == task.capability_id,
            "shift capability_id does not match"
        );
        ensure!(
            self.runner_kind == task.runner_kind,
            "shift runner_kind does not match"
        );
        ensure!(
            self.project_root_identity_sha256 == current_project_root_identity_sha256,
            "shift project root identity is no longer current"
        );
        ensure!(
            self.launch_spec_sha256 == offdesk_task_launch_spec_sha256(task)?,
            "shift launch spec is not the exact queued task"
        );
        if let Some(snapshot) = &self.ondesk_source_snapshot {
            ensure!(
                task.artifact_refs
                    .iter()
                    .any(|artifact| artifact.artifact_id
                        == format!(
                            "{}{}",
                            super::workflow::ondesk_task_binding::BINDING_PREFIX,
                            snapshot.source_sha256
                        )),
                "Ondesk snapshot is not bound to the approved task source"
            );
        }

        let brief = task
            .execution_brief
            .as_ref()
            .ok_or_else(|| anyhow::anyhow!("shift task is missing an execution brief"))?;
        ensure!(
            brief.task_id == task.task_id
                && brief.request_id == task.request_id
                && brief.project_key == task.project_key,
            "shift task execution brief does not match the queued task"
        );
        ensure!(
            self.execution_brief_sha256 == execution_brief_sha256(brief)?,
            "shift execution brief is not the exact queued brief"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftRoutineKindV1 {
    DispatchTick,
    WikiCandidateDistillation,
    MorningReviewBrief,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "schedule_kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum OffdeskShiftRoutineScheduleV1 {
    Once {
        at: DateTime<Utc>,
    },
    Interval {
        starts_at: DateTime<Utc>,
        stops_at: DateTime<Utc>,
        every_minutes: u16,
    },
}

impl OffdeskShiftRoutineScheduleV1 {
    fn validate(&self, window: &OffdeskShiftWindowV1) -> Result<()> {
        match self {
            Self::Once { at } => ensure!(
                window.contains(*at),
                "one-time routine must be scheduled inside the shift window"
            ),
            Self::Interval {
                starts_at,
                stops_at,
                every_minutes,
            } => {
                ensure!(
                    *every_minutes >= 5 && *every_minutes <= 360,
                    "routine interval must be between 5 and 360 minutes"
                );
                ensure!(
                    stops_at > starts_at,
                    "routine interval stop time must be after its start time"
                );
                ensure!(
                    window.contains(*starts_at) && window.contains(*stops_at),
                    "routine interval must stay inside the shift window"
                );
            }
        }
        Ok(())
    }

    pub fn latest_due_occurrence(&self, now: DateTime<Utc>) -> Option<DateTime<Utc>> {
        match self {
            Self::Once { at } => (*at <= now).then_some(*at),
            Self::Interval {
                starts_at,
                stops_at,
                every_minutes,
            } => {
                if now < *starts_at {
                    return None;
                }
                let observed_at = now.min(*stops_at);
                let elapsed_minutes = (observed_at - *starts_at).num_minutes();
                let occurrence = elapsed_minutes / i64::from(*every_minutes);
                Some(*starts_at + Duration::minutes(occurrence * i64::from(*every_minutes)))
            }
        }
    }

    fn contains_occurrence(&self, scheduled_at: DateTime<Utc>) -> bool {
        match self {
            Self::Once { at } => scheduled_at == *at,
            Self::Interval {
                starts_at,
                stops_at,
                every_minutes,
            } => {
                scheduled_at >= *starts_at
                    && scheduled_at <= *stops_at
                    && (scheduled_at - *starts_at).num_seconds() % (i64::from(*every_minutes) * 60)
                        == 0
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineBindingV1 {
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub schedule: OffdeskShiftRoutineScheduleV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wiki_profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_root_identity_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub project_root_path: Option<String>,
}

impl OffdeskShiftRoutineBindingV1 {
    fn validate(&self, window: &OffdeskShiftWindowV1) -> Result<()> {
        validate_text(&self.routine_id, "routine_id", MAX_IDENTIFIER_CHARS)?;
        self.schedule.validate(window)?;
        match self.routine_kind {
            OffdeskShiftRoutineKindV1::WikiCandidateDistillation => {
                validate_optional_required_text(
                    self.project_key.as_deref(),
                    "wiki routine project_key",
                )?;
                validate_optional_required_text(
                    self.wiki_profile.as_deref(),
                    "wiki routine wiki_profile",
                )?;
                validate_sha256(
                    self.project_root_identity_sha256
                        .as_deref()
                        .unwrap_or_default(),
                    "wiki routine project_root_identity_sha256",
                )?;
                validate_project_root_path(self.project_root_path.as_deref())?;
            }
            OffdeskShiftRoutineKindV1::DispatchTick
            | OffdeskShiftRoutineKindV1::MorningReviewBrief => ensure!(
                self.project_key.is_none()
                    && self.wiki_profile.is_none()
                    && self.project_root_identity_sha256.is_none()
                    && self.project_root_path.is_none(),
                "global shift routines must not carry project or wiki targets"
            ),
        }
        Ok(())
    }

    pub fn validate_against_project(
        &self,
        project_key: &str,
        wiki_profile: &str,
        current_project_root_identity_sha256: &str,
    ) -> Result<()> {
        ensure!(
            self.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
            "only a wiki candidate distillation routine has a project target"
        );
        validate_text(project_key, "current project_key", MAX_IDENTIFIER_CHARS)?;
        validate_text(wiki_profile, "current wiki_profile", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(
            current_project_root_identity_sha256,
            "current project_root_identity_sha256",
        )?;
        ensure!(
            self.project_key.as_deref() == Some(project_key)
                && self.wiki_profile.as_deref() == Some(wiki_profile)
                && self.project_root_identity_sha256.as_deref()
                    == Some(current_project_root_identity_sha256),
            "wiki routine target is no longer the exact reviewed project"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftAuthorityV1 {
    pub task_dispatch_requires_fresh_approval: bool,
    pub task_launch_spec_is_immutable: bool,
    pub wiki_writes_are_candidate_only: bool,
    pub plan_revision_requires_review: bool,
    pub acceptance_requires_ondesk_review: bool,
    pub does_not_authorize: Vec<String>,
}

impl OffdeskShiftAuthorityV1 {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.task_dispatch_requires_fresh_approval,
            "shift authority must preserve the per-task approval gate"
        );
        ensure!(
            self.task_launch_spec_is_immutable,
            "shift authority must bind immutable task launch specs"
        );
        ensure!(
            self.wiki_writes_are_candidate_only,
            "shift authority must keep wiki writes candidate-only"
        );
        ensure!(
            self.plan_revision_requires_review,
            "shift authority must require review for plan revision"
        );
        ensure!(
            self.acceptance_requires_ondesk_review,
            "shift authority must require Ondesk acceptance review"
        );
        ensure!(
            self.does_not_authorize.len() <= MAX_DENIALS,
            "shift authority denials exceed the limit"
        );
        let mut denials = HashSet::new();
        for denial in &self.does_not_authorize {
            validate_text(denial, "shift authority denial", MAX_IDENTIFIER_CHARS)?;
            ensure!(
                denials.insert(denial.as_str()),
                "shift authority denials must be unique"
            );
        }
        for required in OFFDESK_SHIFT_REQUIRED_DENIALS {
            ensure!(
                denials.contains(required),
                "shift authority is missing required denial: {required}"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftSerialSelectionV1 {
    pub proposal_sha256: String,
    pub registry_sha256: String,
    pub queue_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftManifestV1 {
    pub schema: String,
    pub shift_id: String,
    pub profile: String,
    pub created_at: DateTime<Utc>,
    pub window: OffdeskShiftWindowV1,
    pub plan_id: String,
    pub plan_review_id: String,
    pub plan_source_sha256: String,
    pub plan_review_sha256: String,
    pub task_bindings: Vec<OffdeskShiftTaskBindingV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub serial_selection: Option<OffdeskShiftSerialSelectionV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preparation_review: Option<super::workflow::offdesk_preparation::PreparationReviewBinding>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preparation_requirements:
        Option<super::workflow::preparation_capabilities::PreparationRequirementsV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub weekly_usage: Option<super::weekly_usage::WeeklyUsageBinding>,
    pub routine_bindings: Vec<OffdeskShiftRoutineBindingV1>,
    pub budget: OffdeskShiftBudgetV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub enterprise_policy: Option<super::shift_enterprise::OffdeskShiftEnterprisePolicyV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_policy_attestation:
        Option<super::shift_host_policy::OffdeskShiftHostPolicyAttestationV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub read_policy: Option<super::read_policy::ReadPolicy>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub template_instantiation: Option<OffdeskShiftTemplateInstantiationV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub routine_resource_budget: Option<OffdeskShiftRoutineResourceBudgetV1>,
    pub authority: OffdeskShiftAuthorityV1,
}

#[derive(Debug, Clone, Copy)]
pub struct OffdeskShiftTaskObservation<'a> {
    pub task: &'a OffdeskTask,
    pub project_root_identity_sha256: &'a str,
}

impl OffdeskShiftManifestV1 {
    pub fn validate(&self) -> Result<()> {
        if let Some(requirements) = &self.preparation_requirements {
            requirements.validate()?;
        }
        if let Some(binding) = &self.preparation_review {
            binding.validate()?;
            ensure!(
                !self.task_bindings.is_empty()
                    && self
                        .task_bindings
                        .iter()
                        .all(|task| task.ondesk_source_snapshot.is_some()),
                "preparation review binding requires exact owning task sources"
            );
        }
        ensure!(
            self.schema == OFFDESK_SHIFT_MANIFEST_SCHEMA,
            "shift manifest schema must be {OFFDESK_SHIFT_MANIFEST_SCHEMA}"
        );
        validate_text(&self.shift_id, "shift_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.profile, "profile", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.plan_id, "plan_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.plan_review_id, "plan_review_id", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(&self.plan_source_sha256, "plan_source_sha256")?;
        validate_sha256(&self.plan_review_sha256, "plan_review_sha256")?;
        if let Some(serial) = &self.serial_selection {
            validate_sha256(&serial.proposal_sha256, "serial proposal_sha256")?;
            validate_sha256(&serial.registry_sha256, "serial registry_sha256")?;
            validate_sha256(&serial.queue_sha256, "serial queue_sha256")?;
            ensure!(
                !self.task_bindings.is_empty()
                    && self.budget.max_concurrent_tasks == 1
                    && self
                        .task_bindings
                        .iter()
                        .all(|task| task.ondesk_source_snapshot.is_some()
                            && task.runner_kind == BackgroundRunnerKind::LocalBackground),
                "serial selection requires source-bound local-background tasks and concurrency one"
            );
        }
        self.window.validate()?;
        if let Some(weekly) = &self.weekly_usage {
            weekly.validate(self.window.stops_at)?;
        }
        ensure!(
            self.created_at <= self.window.stops_at,
            "shift manifest cannot be created after its stop time"
        );
        if let Some(template) = &self.template_instantiation {
            template.validate_against(&self.shift_id, self.window.starts_at)?;
        }
        ensure!(
            !self.task_bindings.is_empty() || !self.routine_bindings.is_empty(),
            "shift manifest must bind at least one task or routine"
        );
        ensure!(
            self.task_bindings.len() <= MAX_TASK_BINDINGS,
            "shift task bindings exceed the limit"
        );
        ensure!(
            self.routine_bindings.len() <= MAX_ROUTINE_BINDINGS,
            "shift routine bindings exceed the limit"
        );

        let mut task_ids = HashSet::new();
        let mut task_requests = HashSet::new();
        for task in &self.task_bindings {
            task.validate()?;
            ensure!(
                task_ids.insert(task.task_id.as_str()),
                "shift task_ids must be unique"
            );
            ensure!(
                task_requests.insert((task.request_id.as_str(), task.task_id.as_str())),
                "shift request/task bindings must be unique"
            );
        }

        let mut routine_ids = HashSet::new();
        for routine in &self.routine_bindings {
            routine.validate(&self.window)?;
            ensure!(
                routine_ids.insert(routine.routine_id.as_str()),
                "shift routine_ids must be unique"
            );
        }

        self.budget
            .validate(self.task_bindings.len(), self.routine_bindings.len())?;
        if let Some(resource_budget) = &self.routine_resource_budget {
            resource_budget.validate(self.routine_bindings.len())?;
        }
        if let Some(policy) = &self.enterprise_policy {
            policy.validate_manifest_components(
                &self.task_bindings,
                &self.routine_bindings,
                &self.budget,
                self.routine_resource_budget.as_ref(),
            )?;
        }
        if let Some(attestation) = &self.host_policy_attestation {
            attestation.validate()?;
            ensure!(
                self.enterprise_policy.is_some(),
                "shift host policy attestation requires an enterprise policy"
            );
            ensure!(
                !self.task_bindings.is_empty()
                    && self
                        .task_bindings
                        .iter()
                        .all(|binding| attestation.allowed_runners.contains(&binding.runner_kind)),
                "shift host policy attestation must cover every bound task runner"
            );
            ensure!(
                attestation.observed_at <= self.created_at,
                "shift host policy attestation cannot postdate its manifest"
            );
        }
        if let Some(policy) = &self.read_policy {
            super::read_policy::validate_runners(
                policy,
                self.task_bindings.iter().map(|b| b.runner_kind),
            )?;
            ensure!(
                self.host_policy_attestation.is_none(),
                "read policy cannot be combined with host policy"
            );
        }
        self.authority.validate()
    }

    pub fn enterprise_policy(
        &self,
    ) -> Result<&super::shift_enterprise::OffdeskShiftEnterprisePolicyV1> {
        self.enterprise_policy
            .as_ref()
            .context("shift manifest has no enterprise policy and cannot gain new authority")
    }

    pub fn validate_against_tasks(
        &self,
        tasks: &[OffdeskShiftTaskObservation<'_>],
        at: DateTime<Utc>,
    ) -> Result<()> {
        self.validate()?;
        ensure!(
            at <= self.window.stops_at,
            "shift task validation happened after the shift stop time"
        );
        ensure!(
            tasks.len() == self.task_bindings.len(),
            "supplied task set must exactly match the shift bindings"
        );
        for binding in &self.task_bindings {
            let task = tasks
                .iter()
                .find(|observation| observation.task.task_id == binding.task_id)
                .ok_or_else(|| anyhow::anyhow!("bound shift task is missing"))?;
            binding.validate_against_task(task.task, task.project_root_identity_sha256, at)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftArmReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub approval_id: String,
    pub approved_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub approved_by: String,
    pub task_dispatch_still_requires_fresh_approval: bool,
}

impl OffdeskShiftArmReceiptV1 {
    pub fn validate_against_manifest(&self, manifest: &OffdeskShiftManifestV1) -> Result<()> {
        manifest.validate()?;
        ensure!(
            self.schema == OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA,
            "shift arm receipt schema must be {OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA}"
        );
        validate_text(&self.receipt_id, "receipt_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.shift_id, "shift_id", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(&self.manifest_sha256, "manifest_sha256")?;
        validate_text(&self.approval_id, "approval_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.approved_by, "approved_by", MAX_IDENTIFIER_CHARS)?;
        ensure!(
            self.task_dispatch_still_requires_fresh_approval,
            "arming a shift must not replace fresh per-task approval"
        );
        ensure!(
            self.shift_id == manifest.shift_id,
            "shift_id does not match"
        );
        ensure!(
            self.manifest_sha256 == offdesk_shift_manifest_sha256(manifest)?,
            "arm receipt is not bound to the exact shift manifest"
        );
        ensure!(
            self.expires_at > self.approved_at,
            "shift arm receipt must expire after approval"
        );
        ensure!(
            self.approved_at >= manifest.created_at,
            "shift cannot be approved before its manifest exists"
        );
        ensure!(
            self.approved_at <= manifest.window.stops_at,
            "shift cannot be armed after its stop time"
        );
        ensure!(
            self.expires_at <= manifest.window.stops_at,
            "shift arm receipt cannot extend the manifest window"
        );
        ensure!(
            self.expires_at >= manifest.window.starts_at,
            "shift arm receipt must remain valid when the shift window starts"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftStatusV1 {
    Armed,
    Running,
    AttentionRequired,
    ReviewReady,
    RevisionRequired,
    Blocked,
    Disarmed,
    Accepted,
}

impl OffdeskShiftStatusV1 {
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Accepted | Self::Blocked | Self::Disarmed)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftStateV1 {
    pub schema: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub sequence: u64,
    pub status: OffdeskShiftStatusV1,
    pub updated_at: DateTime<Utc>,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub acceptance_receipt_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub acceptance_receipt_sha256: Option<String>,
}

impl OffdeskShiftStateV1 {
    pub fn validate_against(
        &self,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        arm_receipt.validate_against_manifest(manifest)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_STATE_SCHEMA,
            "shift state schema must be {OFFDESK_SHIFT_STATE_SCHEMA}"
        );
        validate_text(&self.shift_id, "shift_id", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(&self.manifest_sha256, "manifest_sha256")?;
        validate_sha256(&self.arm_receipt_sha256, "arm_receipt_sha256")?;
        validate_text(&self.reason, "shift state reason", MAX_REASON_CHARS)?;
        ensure!(self.sequence > 0, "shift state sequence must be positive");
        ensure!(
            self.shift_id == manifest.shift_id,
            "shift state_id does not match"
        );
        ensure!(
            self.manifest_sha256 == arm_receipt.manifest_sha256,
            "shift state manifest hash does not match its arm receipt"
        );
        ensure!(
            self.arm_receipt_sha256 == offdesk_shift_arm_receipt_sha256(arm_receipt, manifest)?,
            "shift state is not bound to the exact arm receipt"
        );
        ensure!(
            self.updated_at >= arm_receipt.approved_at,
            "shift state predates its arm approval"
        );
        if matches!(
            self.status,
            OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running
        ) {
            ensure!(
                self.updated_at <= arm_receipt.expires_at,
                "an active shift state cannot outlive its arm receipt"
            );
        }
        if self.status == OffdeskShiftStatusV1::Running {
            ensure!(
                self.updated_at >= manifest.window.starts_at,
                "a shift cannot run before its window starts"
            );
        }

        if self.status == OffdeskShiftStatusV1::Accepted {
            validate_optional_required_text(
                self.acceptance_receipt_id.as_deref(),
                "acceptance_receipt_id",
            )?;
            validate_sha256(
                self.acceptance_receipt_sha256
                    .as_deref()
                    .unwrap_or_default(),
                "acceptance_receipt_sha256",
            )?;
        } else {
            ensure!(
                self.acceptance_receipt_id.is_none() && self.acceptance_receipt_sha256.is_none(),
                "only accepted shift state may carry an acceptance receipt"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTransitionReceiptV1 {
    pub schema: String,
    pub transition_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub sequence: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_status: Option<OffdeskShiftStatusV1>,
    pub status: OffdeskShiftStatusV1,
    pub recorded_at: DateTime<Utc>,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub acceptance_receipt_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub acceptance_receipt_sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTaskLaunchReservationV1 {
    pub schema: String,
    pub launch_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub task_id: String,
    pub background_ticket_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub launch_spec_sha256: String,
    pub execution_brief_sha256: String,
    pub project_root_identity_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host_policy_attestation_sha256: Option<String>,
    pub attempt_number: u32,
    pub reserved_at: DateTime<Utc>,
}

impl OffdeskShiftTaskLaunchReservationV1 {
    pub fn validate_against(
        &self,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        arm_receipt.validate_against_manifest(manifest)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA,
            "shift task launch reservation schema must be {OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA}"
        );
        validate_text(&self.launch_id, "shift launch_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.shift_id,
            "shift launch shift_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(&self.manifest_sha256, "shift launch manifest_sha256")?;
        validate_sha256(&self.arm_receipt_sha256, "shift launch arm_receipt_sha256")?;
        validate_text(&self.task_id, "shift launch task_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.background_ticket_id,
            "shift launch background_ticket_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(&self.launch_spec_sha256, "shift launch launch_spec_sha256")?;
        validate_sha256(
            &self.execution_brief_sha256,
            "shift launch execution_brief_sha256",
        )?;
        validate_sha256(
            &self.project_root_identity_sha256,
            "shift launch project_root_identity_sha256",
        )?;
        if let Some(value) = &self.host_policy_attestation_sha256 {
            validate_sha256(value, "shift launch host_policy_attestation_sha256")?;
        }
        ensure!(
            self.shift_id == manifest.shift_id
                && self.manifest_sha256 == offdesk_shift_manifest_sha256(manifest)?
                && self.arm_receipt_sha256
                    == offdesk_shift_arm_receipt_sha256(arm_receipt, manifest)?,
            "shift task launch reservation authority does not match the shift"
        );
        ensure!(
            self.reserved_at >= manifest.window.starts_at
                && self.reserved_at <= manifest.window.stops_at
                && self.reserved_at <= arm_receipt.expires_at,
            "shift task launch reservation is outside active authority"
        );
        ensure!(
            self.attempt_number > 0
                && self.attempt_number <= u32::from(manifest.budget.max_attempts_per_task),
            "shift task launch reservation exceeds the attempt budget"
        );
        let binding = manifest
            .task_bindings
            .iter()
            .find(|binding| binding.task_id == self.task_id)
            .context("shift task launch reservation has no task binding")?;
        ensure!(
            self.launch_spec_sha256 == binding.launch_spec_sha256
                && self.execution_brief_sha256 == binding.execution_brief_sha256
                && self.project_root_identity_sha256 == binding.project_root_identity_sha256
                && self.runner_kind == binding.runner_kind,
            "shift task launch reservation does not match its task binding"
        );
        let expected_attestation_sha256 = manifest
            .host_policy_attestation
            .as_ref()
            .map(super::shift_host_policy::OffdeskShiftHostPolicyAttestationV1::sha256)
            .transpose()?;
        ensure!(
            self.host_policy_attestation_sha256 == expected_attestation_sha256,
            "shift task launch reservation host policy does not match its manifest"
        );
        ensure!(
            self.background_ticket_id == self.launch_id,
            "shift task launch reservation ticket ID is not canonical"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTaskLaunchTicketReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    pub launch_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub task_id: String,
    pub attempt_number: u32,
    pub background_ticket_id: String,
    pub runner_kind: BackgroundRunnerKind,
    pub background_probe_sha256: String,
    pub recorded_at: DateTime<Utc>,
}

impl OffdeskShiftTaskLaunchTicketReceiptV1 {
    pub fn validate_against(
        &self,
        reservation: &OffdeskShiftTaskLaunchReservationV1,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        reservation.validate_against(manifest, arm_receipt)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA,
            "shift task ticket receipt schema must be {OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA}"
        );
        validate_text(
            &self.receipt_id,
            "shift task ticket receipt_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.launch_id,
            "shift task ticket launch_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.shift_id,
            "shift task ticket shift_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.task_id,
            "shift task ticket task_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.background_ticket_id,
            "shift task ticket background_ticket_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(
            &self.background_probe_sha256,
            "shift task ticket background_probe_sha256",
        )?;
        ensure!(
            self.receipt_id == format!("shift_task_ticket_{}", reservation.launch_id)
                && self.launch_id == reservation.launch_id
                && self.shift_id == reservation.shift_id
                && self.manifest_sha256 == reservation.manifest_sha256
                && self.arm_receipt_sha256 == reservation.arm_receipt_sha256
                && self.task_id == reservation.task_id
                && self.attempt_number == reservation.attempt_number
                && self.background_ticket_id == reservation.background_ticket_id
                && self.runner_kind == reservation.runner_kind,
            "shift task ticket receipt does not match its reservation"
        );
        ensure!(
            self.recorded_at >= reservation.reserved_at,
            "shift task ticket receipt predates its reservation"
        );
        Ok(())
    }

    pub fn validate_probe(
        &self,
        probe: &BackgroundProbe,
        reservation: &OffdeskShiftTaskLaunchReservationV1,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        self.validate_against(reservation, manifest, arm_receipt)?;
        super::read_policy::validate_launch_evidence(
            manifest.read_policy.as_ref(),
            probe.read_policy_launch_evidence.as_ref(),
            &reservation.project_root_identity_sha256,
        )?;
        let binding = manifest
            .task_bindings
            .iter()
            .find(|binding| binding.task_id == reservation.task_id)
            .context("shift task ticket has no task binding")?;
        ensure!(
            probe.ticket_id == reservation.background_ticket_id
                && probe.task_id.as_deref() == Some(binding.task_id.as_str())
                && probe.request_id.as_deref() == Some(binding.request_id.as_str())
                && probe.project_key.as_deref() == Some(binding.project_key.as_str())
                && probe.capability_id.as_deref() == Some(binding.capability_id.as_str())
                && probe.runner_kind == binding.runner_kind,
            "background probe identity does not match the exact shift launch"
        );
        ensure!(
            self.background_probe_sha256 == offdesk_shift_background_probe_sha256(probe)?,
            "shift task ticket receipt does not match the observed background probe"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftTaskOutcomeV1 {
    Completed,
    RecoveryRequired,
    Terminated,
    LaunchUnresolved,
    IdentityDrift,
}

impl OffdeskShiftTaskOutcomeV1 {
    pub fn requires_attention(self) -> bool {
        !matches!(self, Self::Completed)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftTaskOutcomeReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    pub launch_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub task_id: String,
    pub attempt_number: u32,
    pub background_ticket_id: String,
    pub outcome: OffdeskShiftTaskOutcomeV1,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub terminal_phase: Option<BackgroundRunnerPhase>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub launch_ticket_receipt_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub background_probe_sha256: Option<String>,
    pub completed_at: DateTime<Utc>,
    pub summary: String,
}

impl OffdeskShiftTaskOutcomeReceiptV1 {
    pub fn validate_against(
        &self,
        reservation: &OffdeskShiftTaskLaunchReservationV1,
        ticket: Option<&OffdeskShiftTaskLaunchTicketReceiptV1>,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        reservation.validate_against(manifest, arm_receipt)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA,
            "shift task outcome schema must be {OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA}"
        );
        validate_text(
            &self.receipt_id,
            "shift task outcome receipt_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.launch_id,
            "shift task outcome launch_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.shift_id,
            "shift task outcome shift_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.task_id,
            "shift task outcome task_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.background_ticket_id,
            "shift task outcome background_ticket_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.summary,
            "shift task outcome summary",
            MAX_REASON_CHARS,
        )?;
        ensure!(
            self.receipt_id == format!("shift_task_outcome_{}", reservation.launch_id)
                && self.launch_id == reservation.launch_id
                && self.shift_id == reservation.shift_id
                && self.manifest_sha256 == reservation.manifest_sha256
                && self.arm_receipt_sha256 == reservation.arm_receipt_sha256
                && self.task_id == reservation.task_id
                && self.attempt_number == reservation.attempt_number
                && self.background_ticket_id == reservation.background_ticket_id,
            "shift task outcome does not match its reservation"
        );
        ensure!(
            self.completed_at >= reservation.reserved_at,
            "shift task outcome predates its reservation"
        );
        match self.outcome {
            OffdeskShiftTaskOutcomeV1::LaunchUnresolved => ensure!(
                ticket.is_none()
                    && self.terminal_phase.is_none()
                    && self.launch_ticket_receipt_sha256.is_none()
                    && self.background_probe_sha256.is_none(),
                "unresolved launch outcome must not claim a background ticket observation"
            ),
            OffdeskShiftTaskOutcomeV1::IdentityDrift => ensure!(
                ticket.is_none()
                    && self.launch_ticket_receipt_sha256.is_none()
                    && self.background_probe_sha256.is_some(),
                "identity drift outcome must preserve only the conflicting probe hash"
            ),
            OffdeskShiftTaskOutcomeV1::Completed
            | OffdeskShiftTaskOutcomeV1::RecoveryRequired
            | OffdeskShiftTaskOutcomeV1::Terminated => {
                let ticket = ticket.context("terminal shift task outcome has no ticket receipt")?;
                ticket.validate_against(reservation, manifest, arm_receipt)?;
                let ticket_hash = offdesk_shift_task_ticket_receipt_sha256(
                    ticket,
                    reservation,
                    manifest,
                    arm_receipt,
                )?;
                ensure!(
                    self.launch_ticket_receipt_sha256.as_deref() == Some(ticket_hash.as_str())
                        && self.background_probe_sha256.is_some(),
                    "terminal shift task outcome is not bound to its ticket observation"
                );
                let phase = self
                    .terminal_phase
                    .context("terminal shift task outcome has no background phase")?;
                let phase_matches = match self.outcome {
                    OffdeskShiftTaskOutcomeV1::Completed => matches!(
                        phase,
                        BackgroundRunnerPhase::Completed | BackgroundRunnerPhase::ResultReceived
                    ),
                    OffdeskShiftTaskOutcomeV1::RecoveryRequired => matches!(
                        phase,
                        BackgroundRunnerPhase::Failed
                            | BackgroundRunnerPhase::StaleNoAck
                            | BackgroundRunnerPhase::StaleLostCallback
                            | BackgroundRunnerPhase::Reconstructable
                    ),
                    OffdeskShiftTaskOutcomeV1::Terminated => {
                        phase == BackgroundRunnerPhase::Terminated
                    }
                    OffdeskShiftTaskOutcomeV1::LaunchUnresolved
                    | OffdeskShiftTaskOutcomeV1::IdentityDrift => false,
                };
                ensure!(
                    phase_matches,
                    "shift task outcome and terminal phase disagree"
                );
            }
        }
        if let Some(hash) = self.background_probe_sha256.as_deref() {
            validate_sha256(hash, "shift task outcome background_probe_sha256")?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineRunReservationV1 {
    pub schema: String,
    pub run_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub scheduled_at: DateTime<Utc>,
    pub reserved_at: DateTime<Utc>,
}

impl OffdeskShiftRoutineRunReservationV1 {
    pub fn validate_against(
        &self,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        arm_receipt.validate_against_manifest(manifest)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA,
            "shift routine reservation schema must be {OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA}"
        );
        validate_text(&self.run_id, "shift routine run_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(
            &self.shift_id,
            "shift routine shift_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_sha256(&self.manifest_sha256, "shift routine manifest_sha256")?;
        validate_sha256(&self.arm_receipt_sha256, "shift routine arm_receipt_sha256")?;
        validate_text(
            &self.routine_id,
            "shift routine routine_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        ensure!(
            self.shift_id == manifest.shift_id
                && self.manifest_sha256 == offdesk_shift_manifest_sha256(manifest)?
                && self.arm_receipt_sha256
                    == offdesk_shift_arm_receipt_sha256(arm_receipt, manifest)?,
            "shift routine reservation authority does not match the shift"
        );
        let binding = manifest
            .routine_bindings
            .iter()
            .find(|binding| binding.routine_id == self.routine_id)
            .context("shift routine reservation has no routine binding")?;
        ensure!(
            self.routine_kind == binding.routine_kind
                && binding.schedule.contains_occurrence(self.scheduled_at),
            "shift routine reservation does not match one scheduled occurrence"
        );
        ensure!(
            self.reserved_at >= self.scheduled_at
                && self.reserved_at <= manifest.window.stops_at
                && self.reserved_at <= arm_receipt.expires_at,
            "shift routine reservation is outside active authority"
        );
        ensure!(
            self.run_id
                == offdesk_shift_routine_run_id(
                    &manifest.shift_id,
                    &binding.routine_id,
                    self.scheduled_at,
                ),
            "shift routine reservation run ID is not canonical"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftRoutineOutcomeV1 {
    Succeeded,
    Failed,
    Skipped,
    BudgetExhausted,
    IdentityDrift,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineEvidenceV1 {
    pub reference: String,
    pub sha256: String,
    pub size_bytes: u64,
}

impl OffdeskShiftRoutineEvidenceV1 {
    fn validate(&self) -> Result<()> {
        validate_text(
            &self.reference,
            "shift routine evidence reference",
            MAX_PATH_CHARS,
        )?;
        validate_sha256(&self.sha256, "shift routine evidence sha256")?;
        ensure!(
            self.size_bytes <= 16 * 1024 * 1024,
            "shift routine evidence exceeds the byte budget"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineUsageV1 {
    pub schema: String,
    pub measurement_complete: bool,
    pub process_starts: u64,
    pub external_calls: u64,
    pub disk_bytes_written: u64,
    pub llm_tokens: u64,
    pub cost_micro_usd: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cost_rate_micro_usd_per_million_tokens: Option<u64>,
}

impl OffdeskShiftRoutineUsageV1 {
    pub(crate) fn validate_against(
        &self,
        routine_kind: OffdeskShiftRoutineKindV1,
        outcome: OffdeskShiftRoutineOutcomeV1,
        evidence: &[OffdeskShiftRoutineEvidenceV1],
        notification_route: OffdeskShiftNotificationRouteV1,
    ) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA,
            "shift routine usage schema must be {OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA}"
        );
        ensure!(
            self.measurement_complete,
            "shift routine usage measurement must be complete"
        );
        let evidence_bytes = evidence.iter().try_fold(0_u64, |total, item| {
            total
                .checked_add(item.size_bytes)
                .context("shift routine evidence byte total overflow")
        })?;
        ensure!(
            self.disk_bytes_written == evidence_bytes,
            "shift routine disk usage must equal its durable evidence bytes"
        );
        match self.cost_rate_micro_usd_per_million_tokens {
            Some(rate) => {
                let numerator = u128::from(self.llm_tokens)
                    .checked_mul(u128::from(rate))
                    .context("shift routine cost calculation overflow")?;
                let expected = numerator
                    .checked_add(999_999)
                    .context("shift routine cost rounding overflow")?
                    / 1_000_000;
                ensure!(
                    u128::from(self.cost_micro_usd) == expected,
                    "shift routine cost does not match its declared token rate"
                );
            }
            None => ensure!(
                self.llm_tokens == 0 && self.cost_micro_usd == 0,
                "shift routine LLM usage requires an explicit cost rate"
            ),
        }
        if self.external_calls > 0
            && routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
        {
            ensure!(
                self.llm_tokens > 0 && self.cost_rate_micro_usd_per_million_tokens.is_some(),
                "Wiki routine external usage requires measured tokens and cost rate"
            );
        }
        if outcome == OffdeskShiftRoutineOutcomeV1::Succeeded {
            match routine_kind {
                OffdeskShiftRoutineKindV1::DispatchTick => ensure!(
                    self.process_starts >= 1
                        && self.external_calls == 0
                        && self.llm_tokens == 0
                        && self.cost_micro_usd == 0,
                    "successful dispatch routine usage is not canonical"
                ),
                OffdeskShiftRoutineKindV1::WikiCandidateDistillation => ensure!(
                    self.process_starts == 2
                        && self.external_calls <= 1
                        && self.disk_bytes_written > 0
                        && (self.external_calls > 0 || self.llm_tokens == 0),
                    "successful Wiki routine usage is not canonical"
                ),
                OffdeskShiftRoutineKindV1::MorningReviewBrief => ensure!(
                    self.process_starts == 1
                        && self.external_calls
                            == u64::from(
                                notification_route
                                    == OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
                            )
                        && self.disk_bytes_written > 0
                        && self.llm_tokens == 0
                        && self.cost_micro_usd == 0,
                    "successful morning routine usage is not canonical"
                ),
            }
        }
        Ok(())
    }
}

impl OffdeskShiftRoutineOutcomeV1 {
    pub fn requires_attention(self) -> bool {
        !matches!(self, Self::Succeeded)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineRecoveryAcknowledgementV1 {
    pub schema: String,
    pub acknowledged_at: DateTime<Utc>,
    pub acknowledged_by: String,
    pub reason: String,
    pub execution_result_unknown: bool,
    pub success_not_asserted: bool,
    pub does_not_authorize: Vec<String>,
}

impl OffdeskShiftRoutineRecoveryAcknowledgementV1 {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA,
            "shift routine recovery acknowledgement schema must be {OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA}"
        );
        validate_text(
            &self.acknowledged_by,
            "shift routine recovery acknowledged_by",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.reason,
            "shift routine recovery reason",
            MAX_REASON_CHARS,
        )?;
        ensure!(
            self.execution_result_unknown && self.success_not_asserted,
            "shift routine recovery must preserve unknown execution and deny success"
        );
        ensure!(
            self.does_not_authorize.len() == OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS.len(),
            "shift routine recovery authority denials are incomplete"
        );
        let denials = self
            .does_not_authorize
            .iter()
            .map(String::as_str)
            .collect::<HashSet<_>>();
        ensure!(
            OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS
                .iter()
                .all(|required| denials.contains(required)),
            "shift routine recovery authority denials are incomplete"
        );
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftRoutineOutcomeReceiptV1 {
    pub schema: String,
    pub receipt_id: String,
    pub run_id: String,
    pub shift_id: String,
    pub manifest_sha256: String,
    pub arm_receipt_sha256: String,
    pub routine_id: String,
    pub routine_kind: OffdeskShiftRoutineKindV1,
    pub scheduled_at: DateTime<Utc>,
    pub reserved_at: DateTime<Utc>,
    pub completed_at: DateTime<Utc>,
    pub outcome: OffdeskShiftRoutineOutcomeV1,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<OffdeskShiftRoutineEvidenceV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub usage: Option<OffdeskShiftRoutineUsageV1>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery_acknowledgement: Option<OffdeskShiftRoutineRecoveryAcknowledgementV1>,
}

impl OffdeskShiftRoutineOutcomeReceiptV1 {
    pub fn validate_against(
        &self,
        reservation: &OffdeskShiftRoutineRunReservationV1,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        reservation.validate_against(manifest, arm_receipt)?;
        ensure!(
            self.schema == OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA,
            "shift routine outcome schema must be {OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA}"
        );
        validate_text(
            &self.receipt_id,
            "shift routine receipt_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.run_id,
            "shift routine outcome run_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.routine_id,
            "shift routine outcome routine_id",
            MAX_IDENTIFIER_CHARS,
        )?;
        validate_text(
            &self.summary,
            "shift routine outcome summary",
            MAX_REASON_CHARS,
        )?;
        ensure!(
            self.evidence.len() <= MAX_ROUTINE_EVIDENCE_REFS,
            "shift routine outcome evidence exceeds its budget"
        );
        for evidence in &self.evidence {
            evidence.validate()?;
        }
        if let Some(resource_budget) = &manifest.routine_resource_budget {
            if self.recovery_acknowledgement.is_some() {
                ensure!(
                    self.usage.is_none(),
                    "unknown-execution recovery cannot assert measured resource usage"
                );
            } else {
                let usage = self
                    .usage
                    .as_ref()
                    .context("shift routine outcome is missing measured resource usage")?;
                let notification_route = manifest
                    .enterprise_policy
                    .as_ref()
                    .map(|policy| policy.notification_route)
                    .unwrap_or(OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate);
                usage.validate_against(
                    self.routine_kind,
                    self.outcome,
                    &self.evidence,
                    notification_route,
                )?;
                if usage.llm_tokens > 0
                    || (self.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
                        && usage.external_calls > 0)
                {
                    ensure!(
                        usage.cost_rate_micro_usd_per_million_tokens
                            == Some(resource_budget.llm_cost_rate_micro_usd_per_million_tokens),
                        "shift routine usage cost rate does not match the manifest"
                    );
                }
                ensure!(
                    usage.process_starts <= resource_budget.max_process_starts
                        && usage.external_calls <= resource_budget.max_external_calls
                        && usage.disk_bytes_written <= resource_budget.max_disk_bytes_written
                        && usage.llm_tokens <= resource_budget.max_llm_tokens
                        && usage.cost_micro_usd <= resource_budget.max_cost_micro_usd,
                    "shift routine usage exceeds the manifest resource budget"
                );
            }
        } else {
            ensure!(
                self.usage.is_none(),
                "legacy shift routine outcome cannot add unbound resource usage"
            );
        }
        if let Some(recovery) = &self.recovery_acknowledgement {
            recovery.validate()?;
            ensure!(
                self.outcome == OffdeskShiftRoutineOutcomeV1::Failed
                    && self.completed_at == recovery.acknowledged_at,
                "shift routine recovery may only close one unknown execution as failed"
            );
        }
        ensure!(
            self.run_id == reservation.run_id
                && self.shift_id == reservation.shift_id
                && self.manifest_sha256 == reservation.manifest_sha256
                && self.arm_receipt_sha256 == reservation.arm_receipt_sha256
                && self.routine_id == reservation.routine_id
                && self.routine_kind == reservation.routine_kind
                && self.scheduled_at == reservation.scheduled_at
                && self.reserved_at == reservation.reserved_at,
            "shift routine outcome does not match its exact reservation"
        );
        ensure!(
            self.receipt_id == format!("shift_routine_outcome_{}", reservation.run_id),
            "shift routine outcome receipt ID is not canonical"
        );
        ensure!(
            self.completed_at >= reservation.reserved_at,
            "shift routine outcome predates its reservation"
        );
        Ok(())
    }
}

impl OffdeskShiftTransitionReceiptV1 {
    pub fn from_state_transition(
        transition_id: impl Into<String>,
        previous: Option<&OffdeskShiftStateV1>,
        next: &OffdeskShiftStateV1,
    ) -> Result<Self> {
        let receipt = Self {
            schema: OFFDESK_SHIFT_TRANSITION_RECEIPT_SCHEMA.to_string(),
            transition_id: transition_id.into(),
            shift_id: next.shift_id.clone(),
            manifest_sha256: next.manifest_sha256.clone(),
            arm_receipt_sha256: next.arm_receipt_sha256.clone(),
            sequence: next.sequence,
            previous_status: previous.map(|state| state.status),
            status: next.status,
            recorded_at: next.updated_at,
            reason: next.reason.clone(),
            acceptance_receipt_id: next.acceptance_receipt_id.clone(),
            acceptance_receipt_sha256: next.acceptance_receipt_sha256.clone(),
        };
        receipt.validate_shape()?;
        Ok(receipt)
    }

    pub fn state(&self) -> OffdeskShiftStateV1 {
        OffdeskShiftStateV1 {
            schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
            shift_id: self.shift_id.clone(),
            manifest_sha256: self.manifest_sha256.clone(),
            arm_receipt_sha256: self.arm_receipt_sha256.clone(),
            sequence: self.sequence,
            status: self.status,
            updated_at: self.recorded_at,
            reason: self.reason.clone(),
            acceptance_receipt_id: self.acceptance_receipt_id.clone(),
            acceptance_receipt_sha256: self.acceptance_receipt_sha256.clone(),
        }
    }

    pub fn validate_initial(
        &self,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        self.validate_shape()?;
        ensure!(
            self.sequence == 1
                && self.previous_status.is_none()
                && self.status == OffdeskShiftStatusV1::Armed,
            "initial shift transition must record sequence 1 armed state"
        );
        ensure!(
            self.recorded_at == arm_receipt.approved_at,
            "initial shift transition time must equal arm approval time"
        );
        self.state().validate_against(manifest, arm_receipt)
    }

    pub fn validate_after(
        &self,
        previous: &OffdeskShiftStateV1,
        manifest: &OffdeskShiftManifestV1,
        arm_receipt: &OffdeskShiftArmReceiptV1,
    ) -> Result<()> {
        self.validate_shape()?;
        ensure!(
            self.previous_status == Some(previous.status),
            "shift transition previous status does not match replay state"
        );
        validate_offdesk_shift_state_transition(previous, &self.state(), manifest, arm_receipt)
    }

    fn validate_shape(&self) -> Result<()> {
        ensure!(
            self.schema == OFFDESK_SHIFT_TRANSITION_RECEIPT_SCHEMA,
            "shift transition receipt schema must be {OFFDESK_SHIFT_TRANSITION_RECEIPT_SCHEMA}"
        );
        validate_text(&self.transition_id, "transition_id", MAX_IDENTIFIER_CHARS)?;
        validate_text(&self.shift_id, "shift_id", MAX_IDENTIFIER_CHARS)?;
        validate_sha256(&self.manifest_sha256, "manifest_sha256")?;
        validate_sha256(&self.arm_receipt_sha256, "arm_receipt_sha256")?;
        validate_text(&self.reason, "shift transition reason", MAX_REASON_CHARS)?;
        ensure!(
            self.sequence > 0,
            "shift transition sequence must be positive"
        );
        if self.status == OffdeskShiftStatusV1::Accepted {
            validate_optional_required_text(
                self.acceptance_receipt_id.as_deref(),
                "acceptance_receipt_id",
            )?;
            validate_sha256(
                self.acceptance_receipt_sha256
                    .as_deref()
                    .unwrap_or_default(),
                "acceptance_receipt_sha256",
            )?;
        } else {
            ensure!(
                self.acceptance_receipt_id.is_none() && self.acceptance_receipt_sha256.is_none(),
                "only accepted transition may carry an acceptance receipt"
            );
        }
        Ok(())
    }
}

pub fn validate_offdesk_shift_state_transition(
    previous: &OffdeskShiftStateV1,
    next: &OffdeskShiftStateV1,
    manifest: &OffdeskShiftManifestV1,
    arm_receipt: &OffdeskShiftArmReceiptV1,
) -> Result<()> {
    previous.validate_against(manifest, arm_receipt)?;
    next.validate_against(manifest, arm_receipt)?;
    ensure!(
        next.sequence == previous.sequence + 1,
        "shift state sequence must advance by one"
    );
    ensure!(
        next.updated_at >= previous.updated_at,
        "shift state time cannot move backwards"
    );
    ensure!(
        allowed_transition(previous.status, next.status),
        "invalid shift state transition: {:?} -> {:?}",
        previous.status,
        next.status
    );
    Ok(())
}

pub fn validate_offdesk_shift_dispatch_authority(
    manifest: &OffdeskShiftManifestV1,
    arm_receipt: &OffdeskShiftArmReceiptV1,
    state: &OffdeskShiftStateV1,
    at: DateTime<Utc>,
) -> Result<()> {
    manifest.enterprise_policy()?.validate()?;
    state.validate_against(manifest, arm_receipt)?;
    ensure!(
        matches!(
            state.status,
            OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running
        ),
        "shift state does not authorize task dispatch"
    );
    ensure!(
        at >= manifest.window.starts_at,
        "shift dispatch window has not started"
    );
    ensure!(
        at <= manifest.window.stops_at && at <= arm_receipt.expires_at,
        "shift dispatch authority has expired"
    );
    Ok(())
}

fn allowed_transition(previous: OffdeskShiftStatusV1, next: OffdeskShiftStatusV1) -> bool {
    use OffdeskShiftStatusV1::{
        Accepted, Armed, AttentionRequired, Blocked, Disarmed, ReviewReady, RevisionRequired,
        Running,
    };

    matches!(
        (previous, next),
        (
            Armed,
            Running | AttentionRequired | ReviewReady | Blocked | Disarmed
        ) | (
            Running,
            AttentionRequired | ReviewReady | RevisionRequired | Blocked | Disarmed
        ) | (
            AttentionRequired,
            Running | ReviewReady | RevisionRequired | Blocked | Disarmed
        ) | (
            ReviewReady,
            Accepted | RevisionRequired | Blocked | Disarmed
        ) | (RevisionRequired, Disarmed)
    )
}

pub fn offdesk_shift_manifest_sha256(manifest: &OffdeskShiftManifestV1) -> Result<String> {
    manifest.validate()?;
    hash_serializable(OFFDESK_SHIFT_MANIFEST_HASH_DOMAIN, manifest)
}

pub fn offdesk_shift_arm_receipt_sha256(
    receipt: &OffdeskShiftArmReceiptV1,
    manifest: &OffdeskShiftManifestV1,
) -> Result<String> {
    receipt.validate_against_manifest(manifest)?;
    hash_serializable(OFFDESK_SHIFT_ARM_RECEIPT_HASH_DOMAIN, receipt)
}

pub fn offdesk_shift_task_ticket_receipt_sha256(
    receipt: &OffdeskShiftTaskLaunchTicketReceiptV1,
    reservation: &OffdeskShiftTaskLaunchReservationV1,
    manifest: &OffdeskShiftManifestV1,
    arm_receipt: &OffdeskShiftArmReceiptV1,
) -> Result<String> {
    receipt.validate_against(reservation, manifest, arm_receipt)?;
    hash_serializable(OFFDESK_SHIFT_TASK_TICKET_RECEIPT_HASH_DOMAIN, receipt)
}

pub fn offdesk_shift_background_probe_sha256(probe: &BackgroundProbe) -> Result<String> {
    hash_serializable(OFFDESK_SHIFT_BACKGROUND_PROBE_HASH_DOMAIN, probe)
}

pub fn offdesk_shift_routine_run_id(
    shift_id: &str,
    routine_id: &str,
    scheduled_at: DateTime<Utc>,
) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"forager.offdesk-shift.routine-run.v1\0");
    hasher.update(shift_id.as_bytes());
    hasher.update([0]);
    hasher.update(routine_id.as_bytes());
    hasher.update([0]);
    hasher.update(scheduled_at.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true));
    format!("shift_routine_{:x}", hasher.finalize())
}

pub fn execution_brief_sha256(brief: &ExecutionBrief) -> Result<String> {
    validate_execution_brief(brief)?;
    hash_serializable(EXECUTION_BRIEF_HASH_DOMAIN, brief)
}

#[derive(Serialize)]
struct OffdeskTaskLaunchSpecHashInput<'a> {
    task_id: &'a str,
    request_id: &'a str,
    project_key: &'a str,
    capability_id: &'a str,
    runner_kind: super::background::BackgroundRunnerKind,
    command: &'a str,
    workdir: &'a str,
    execution_brief: &'a Option<ExecutionBrief>,
    not_before: &'a Option<DateTime<Utc>>,
    mutation_class: &'a Option<String>,
    artifact_refs: &'a [super::capability::CapabilityArtifactRef],
    implementation_packet: &'a Option<super::implementation_packet::ImplementationPacketSummary>,
    artifact_kind: &'a Option<String>,
    agent_mode: &'a Option<super::adaptive_wiki::AdaptiveWikiAgentMode>,
    provider_id: &'a Option<String>,
    model: &'a Option<String>,
    log_artifact_path: &'a Option<String>,
    result_artifact_path: &'a Option<String>,
}

pub fn offdesk_task_launch_spec_sha256(task: &OffdeskTask) -> Result<String> {
    validate_text(&task.task_id, "task_id", MAX_IDENTIFIER_CHARS)?;
    validate_text(&task.request_id, "request_id", MAX_IDENTIFIER_CHARS)?;
    validate_text(&task.project_key, "project_key", MAX_IDENTIFIER_CHARS)?;
    validate_text(&task.capability_id, "capability_id", MAX_IDENTIFIER_CHARS)?;
    if let Some(brief) = &task.execution_brief {
        validate_execution_brief(brief)?;
    }
    let input = OffdeskTaskLaunchSpecHashInput {
        task_id: &task.task_id,
        request_id: &task.request_id,
        project_key: &task.project_key,
        capability_id: &task.capability_id,
        runner_kind: task.runner_kind,
        command: &task.command,
        workdir: &task.workdir,
        execution_brief: &task.execution_brief,
        not_before: &task.not_before,
        mutation_class: &task.mutation_class,
        artifact_refs: &task.artifact_refs,
        implementation_packet: &task.implementation_packet,
        artifact_kind: &task.artifact_kind,
        agent_mode: &task.agent_mode,
        provider_id: &task.provider_id,
        model: &task.model,
        log_artifact_path: &task.log_artifact_path,
        result_artifact_path: &task.result_artifact_path,
    };
    hash_serializable(OFFDESK_TASK_LAUNCH_SPEC_HASH_DOMAIN, &input)
}

fn validate_execution_brief(brief: &ExecutionBrief) -> Result<()> {
    validate_text(&brief.request_id, "brief request_id", MAX_IDENTIFIER_CHARS)?;
    validate_text(&brief.task_id, "brief task_id", MAX_IDENTIFIER_CHARS)?;
    validate_text(
        &brief.project_key,
        "brief project_key",
        MAX_IDENTIFIER_CHARS,
    )?;
    validate_mutation_classes(&brief.allowed_runtime_mutations, "allowed runtime mutation")?;
    validate_mutation_classes(
        &brief.allowed_canonical_mutations,
        "allowed canonical mutation",
    )
}

fn validate_mutation_classes(values: &[String], label: &str) -> Result<()> {
    ensure!(
        values.len() <= MAX_MUTATION_CLASSES,
        "execution brief mutation classes exceed the limit"
    );
    let mut unique = HashSet::new();
    for value in values {
        validate_text(value, label, MAX_IDENTIFIER_CHARS)?;
        ensure!(
            unique.insert(value.as_str()),
            "execution brief mutation classes must be unique"
        );
    }
    Ok(())
}

fn validate_optional_required_text(value: Option<&str>, label: &str) -> Result<()> {
    let value = value.ok_or_else(|| anyhow::anyhow!("{label} is required"))?;
    validate_text(value, label, MAX_IDENTIFIER_CHARS)
}

fn validate_project_root_path(value: Option<&str>) -> Result<()> {
    let value =
        value.ok_or_else(|| anyhow::anyhow!("wiki routine project_root_path is required"))?;
    validate_text(value, "wiki routine project_root_path", MAX_PATH_CHARS)?;
    ensure!(
        std::path::Path::new(value).is_absolute(),
        "wiki routine project_root_path must be absolute"
    );
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

fn hash_serializable(domain: &[u8], value: &impl Serialize) -> Result<String> {
    let canonical = serde_json::to_vec(value)?;
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update(canonical);
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::{BackgroundRunnerKind, OffdeskTaskInput};
    use chrono::TimeZone;

    fn at(hour: u32, minute: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2026, 8, 28, hour, minute, 0)
            .single()
            .expect("valid timestamp")
    }

    fn approved_task() -> OffdeskTask {
        OffdeskTask::new(
            OffdeskTaskInput {
                task_id: Some("task_nightly_1".to_string()),
                request_id: "request_nightly_1".to_string(),
                project_key: "forager".to_string(),
                capability_id: "dispatch.runtime".to_string(),
                runner_kind: BackgroundRunnerKind::LocalTmux,
                command: "cargo test --lib".to_string(),
                workdir: "/workspace/forager".to_string(),
                execution_brief: Some(ExecutionBrief {
                    request_id: "request_nightly_1".to_string(),
                    task_id: "task_nightly_1".to_string(),
                    project_key: "forager".to_string(),
                    approved: true,
                    allowed_runtime_mutations: vec!["dispatch.runtime".to_string()],
                    allowed_canonical_mutations: Vec::new(),
                    fresh_until: Some(at(9, 0)),
                }),
                not_before: Some(at(0, 0)),
                mutation_class: Some("dispatch.runtime".to_string()),
                artifact_refs: Vec::new(),
                implementation_packet: None,
                artifact_kind: Some("implementation".to_string()),
                agent_mode: None,
                provider_id: Some("local".to_string()),
                model: Some("bounded-model".to_string()),
                preview: "nightly test run".to_string(),
                reason: "approved todo".to_string(),
                log_artifact_path: Some("/tmp/nightly.log".to_string()),
                result_artifact_path: Some("/tmp/nightly-result.json".to_string()),
            },
            at(0, 0),
        )
    }

    fn authority() -> OffdeskShiftAuthorityV1 {
        OffdeskShiftAuthorityV1 {
            task_dispatch_requires_fresh_approval: true,
            task_launch_spec_is_immutable: true,
            wiki_writes_are_candidate_only: true,
            plan_revision_requires_review: true,
            acceptance_requires_ondesk_review: true,
            does_not_authorize: OFFDESK_SHIFT_REQUIRED_DENIALS
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
        }
    }

    #[test]
    fn serial_binding_roundtrip_preserves_legacy_bytes_and_binds_order() -> Result<()> {
        let mut value = manifest();
        let legacy = serde_json::to_vec(&value)?;
        assert!(!String::from_utf8_lossy(&legacy).contains("serial_selection"));
        assert!(!String::from_utf8_lossy(&legacy).contains("preparation_review"));
        assert!(!String::from_utf8_lossy(&legacy).contains("preparation_requirements"));
        let restored: OffdeskShiftManifestV1 = serde_json::from_slice(&legacy)?;
        assert_eq!(serde_json::to_vec(&restored)?, legacy);
        assert_eq!(
            offdesk_shift_manifest_sha256(&restored)?,
            offdesk_shift_manifest_sha256(&value)?
        );
        let mut second = value.task_bindings[0].clone();
        second.task_id = "fixture-second".into();
        value.task_bindings.push(second);
        value.budget.max_task_launches = 2;
        for binding in &mut value.task_bindings {
            binding.runner_kind = BackgroundRunnerKind::LocalBackground;
            let source = format!(
                "```forager-plan\n{}\n```\n",
                serde_json::json!({
                    "schema":"ondesk_task_plan.v1", "project_key":binding.project_key,
                    "tasks":[{"task_id":binding.task_id,"goal":"fixture","completion_criteria":["checked"],
                        "depends_on":[],"review_state":"unreviewed","evidence_refs":[]}]
                })
            );
            binding.ondesk_source_snapshot = Some(
                super::super::workflow::ondesk_task_binding::OndeskSourceSnapshot {
                    source_sha256: format!("{:x}", Sha256::digest(source.as_bytes())),
                    source,
                },
            );
        }
        value.budget.max_concurrent_tasks = 1;
        let unbound_hash = offdesk_shift_manifest_sha256(&value)?;
        value.serial_selection = Some(OffdeskShiftSerialSelectionV1 {
            proposal_sha256: "a".repeat(64),
            registry_sha256: "b".repeat(64),
            queue_sha256: "c".repeat(64),
        });
        let roundtrip: OffdeskShiftManifestV1 =
            serde_json::from_slice(&serde_json::to_vec(&value)?)?;
        assert_eq!(value, roundtrip);
        assert_ne!(unbound_hash, offdesk_shift_manifest_sha256(&roundtrip)?);
        value.task_bindings.swap(0, 1);
        assert_ne!(
            offdesk_shift_manifest_sha256(&value)?,
            offdesk_shift_manifest_sha256(&roundtrip)?
        );
        value.budget.max_concurrent_tasks = 2;
        assert!(value.validate().is_err());
        value.budget.max_concurrent_tasks = 1;
        value.task_bindings[0].runner_kind = BackgroundRunnerKind::LocalTmux;
        assert!(value.validate().is_err());
        value.task_bindings[0].runner_kind = BackgroundRunnerKind::LocalBackground;
        value.task_bindings[0].ondesk_source_snapshot = None;
        assert!(value.validate().is_err());
        Ok(())
    }

    fn manifest() -> OffdeskShiftManifestV1 {
        let task = approved_task();
        OffdeskShiftManifestV1 {
            serial_selection: None,
            preparation_review: None,
            preparation_requirements: None,
            weekly_usage: None,
            schema: OFFDESK_SHIFT_MANIFEST_SCHEMA.to_string(),
            shift_id: "shift_20260828".to_string(),
            profile: "forager-ops".to_string(),
            created_at: at(0, 0),
            window: OffdeskShiftWindowV1 {
                starts_at: at(0, 0),
                stops_at: at(9, 0),
                timezone: "Asia/Seoul".to_string(),
            },
            plan_id: "plan_overnight_1".to_string(),
            plan_review_id: "review_overnight_1".to_string(),
            plan_source_sha256: "a".repeat(64),
            plan_review_sha256: "c".repeat(64),
            task_bindings: vec![OffdeskShiftTaskBindingV1::from_task(
                &task,
                &"b".repeat(64),
                at(0, 0),
            )
            .expect("task binding")],
            routine_bindings: vec![OffdeskShiftRoutineBindingV1 {
                routine_id: "routine_tick".to_string(),
                routine_kind: OffdeskShiftRoutineKindV1::DispatchTick,
                schedule: OffdeskShiftRoutineScheduleV1::Interval {
                    starts_at: at(0, 0),
                    stops_at: at(9, 0),
                    every_minutes: 10,
                },
                project_key: None,
                wiki_profile: None,
                project_root_identity_sha256: None,
                project_root_path: None,
            }],
            budget: OffdeskShiftBudgetV1 {
                max_concurrent_tasks: 1,
                max_task_launches: 1,
                max_attempts_per_task: 2,
                max_routine_runs: 64,
            },
            enterprise_policy: Some(crate::offdesk::OffdeskShiftEnterprisePolicyV1::built_in(
                crate::offdesk::OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
            )),
            host_policy_attestation: None,
            read_policy: None,
            template_instantiation: None,
            routine_resource_budget: Some(OffdeskShiftRoutineResourceBudgetV1 {
                max_process_starts: 64,
                max_external_calls: 0,
                max_disk_bytes_written: 0,
                max_llm_tokens: 0,
                max_cost_micro_usd: 0,
                llm_cost_rate_micro_usd_per_million_tokens: 0,
            }),
            authority: authority(),
        }
    }

    #[test]
    fn read_policy_changes_manifest_authority_and_preserves_legacy_shape() -> Result<()> {
        let mut manifest = manifest();
        let mut task = approved_task();
        task.runner_kind = BackgroundRunnerKind::LocalBackground;
        manifest.task_bindings[0] =
            OffdeskShiftTaskBindingV1::from_task(&task, &"b".repeat(64), at(0, 0))?;
        let old = offdesk_shift_manifest_sha256(&manifest)?;
        assert!(serde_json::to_value(&manifest)?
            .get("read_policy")
            .is_none());
        manifest.read_policy = Some(super::super::read_policy::ReadPolicy::canonical());
        manifest.validate()?;
        assert_ne!(old, offdesk_shift_manifest_sha256(&manifest)?);
        manifest.read_policy.as_mut().unwrap().network_isolated = true;
        assert!(manifest.validate().is_err());
        Ok(())
    }

    fn arm_receipt(manifest: &OffdeskShiftManifestV1) -> OffdeskShiftArmReceiptV1 {
        OffdeskShiftArmReceiptV1 {
            schema: OFFDESK_SHIFT_ARM_RECEIPT_SCHEMA.to_string(),
            receipt_id: "shift_arm_1".to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(manifest).expect("manifest hash"),
            approval_id: "approval_shift_1".to_string(),
            approved_at: at(0, 0),
            expires_at: at(9, 0),
            approved_by: "operator".to_string(),
            task_dispatch_still_requires_fresh_approval: true,
        }
    }

    fn state(
        manifest: &OffdeskShiftManifestV1,
        receipt: &OffdeskShiftArmReceiptV1,
        status: OffdeskShiftStatusV1,
        sequence: u64,
        updated_at: DateTime<Utc>,
    ) -> OffdeskShiftStateV1 {
        let accepted = status == OffdeskShiftStatusV1::Accepted;
        OffdeskShiftStateV1 {
            schema: OFFDESK_SHIFT_STATE_SCHEMA.to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: receipt.manifest_sha256.clone(),
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(receipt, manifest)
                .expect("receipt hash"),
            sequence,
            status,
            updated_at,
            reason: "state transition evidence".to_string(),
            acceptance_receipt_id: accepted.then(|| "closeout_1".to_string()),
            acceptance_receipt_sha256: accepted.then(|| "c".repeat(64)),
        }
    }

    #[test]
    fn valid_manifest_binds_exact_task_and_hashes_deterministically() {
        let manifest = manifest();
        let task = approved_task();

        manifest.validate().expect("valid manifest");
        manifest
            .validate_against_tasks(
                &[OffdeskShiftTaskObservation {
                    task: &task,
                    project_root_identity_sha256: &"b".repeat(64),
                }],
                at(0, 0),
            )
            .expect("exact task binding");
        assert_eq!(
            offdesk_shift_manifest_sha256(&manifest).expect("first hash"),
            offdesk_shift_manifest_sha256(&manifest).expect("second hash")
        );
    }

    #[test]
    fn task_launch_mutation_invalidates_the_manifest_binding() {
        let manifest = manifest();
        let mut task = approved_task();
        task.command = "cargo test --all".to_string();

        let error = manifest
            .validate_against_tasks(
                &[OffdeskShiftTaskObservation {
                    task: &task,
                    project_root_identity_sha256: &"b".repeat(64),
                }],
                at(0, 0),
            )
            .expect_err("changed launch spec must fail");
        assert!(error.to_string().contains("launch spec"));
    }

    #[test]
    fn ondesk_plan_binding_is_rechecked_for_launch_not_historical_identity() {
        use crate::offdesk::workflow::ondesk_plan::inspect;
        use crate::offdesk::workflow::ondesk_task_binding::BINDING_PREFIX;
        let directory =
            tempfile::tempdir_in(std::fs::canonicalize(std::env::temp_dir()).unwrap()).unwrap();
        let path = directory.path().join("plan.md");
        let source = format!(
            "```forager-plan\n{}\n```\n",
            serde_json::json!({
            "schema":"ondesk_task_plan.v1", "project_key":"forager", "tasks":[{
                "task_id":"task_nightly_1", "goal":"bounded work", "completion_criteria":["tests pass"],
                "depends_on":[], "review_state":"unreviewed", "evidence_refs":[]
            }]})
        );
        std::fs::write(&path, &source).unwrap();
        let mut task = approved_task();
        task.workdir = directory.path().display().to_string();
        task.artifact_refs
            .push(crate::offdesk::CapabilityArtifactRef {
                artifact_id: format!(
                    "{BINDING_PREFIX}{}",
                    inspect(&source, "forager").unwrap().source_sha256
                ),
                path: Some(path.display().to_string()),
                present: true,
            });
        let root_hash = "a".repeat(64);
        let binding = OffdeskShiftTaskBindingV1::from_task(&task, &root_hash, at(1, 0)).unwrap();
        assert_eq!(
            binding
                .ondesk_source_snapshot
                .as_ref()
                .unwrap()
                .task("forager", "task_nightly_1")
                .unwrap()
                .goal,
            "bounded work"
        );
        let mut tampered = binding.clone();
        tampered
            .ondesk_source_snapshot
            .as_mut()
            .unwrap()
            .source
            .push('\n');
        assert!(tampered.validate().is_err());
        let mut legacy = binding.clone();
        legacy.ondesk_source_snapshot = None;
        let serialized = serde_json::to_value(&legacy).unwrap();
        assert!(serialized.get("ondesk_source_snapshot").is_none());
        assert_eq!(
            serde_json::from_value::<OffdeskShiftTaskBindingV1>(serialized).unwrap(),
            legacy
        );
        let mut approved_manifest = manifest();
        approved_manifest.task_bindings = vec![binding.clone()];
        let preserved_hash = offdesk_shift_manifest_sha256(&approved_manifest).unwrap();
        approved_manifest.task_bindings = vec![legacy];
        assert_ne!(
            preserved_hash,
            offdesk_shift_manifest_sha256(&approved_manifest).unwrap()
        );
        std::fs::write(&path, format!("{source}\n")).unwrap();
        assert!(binding
            .validate_against_task(&task, &root_hash, at(1, 1))
            .is_err());
        assert!(binding
            .validate_identity_against_task(&task, &root_hash)
            .is_ok());
        task.artifact_refs.clear();
        assert!(binding
            .validate_identity_against_task(&task, &root_hash)
            .is_err());
    }

    #[test]
    fn project_root_replacement_invalidates_task_and_wiki_bindings() {
        let manifest = manifest();
        let task = approved_task();
        assert!(manifest
            .validate_against_tasks(
                &[OffdeskShiftTaskObservation {
                    task: &task,
                    project_root_identity_sha256: &"d".repeat(64),
                }],
                at(0, 0),
            )
            .is_err());

        let wiki_routine = OffdeskShiftRoutineBindingV1 {
            routine_id: "routine_wiki".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
            schedule: OffdeskShiftRoutineScheduleV1::Once { at: at(2, 0) },
            project_key: Some("forager".to_string()),
            wiki_profile: Some("forager-ops".to_string()),
            project_root_identity_sha256: Some("b".repeat(64)),
            project_root_path: Some("/workspace/forager".to_string()),
        };
        assert!(wiki_routine
            .validate_against_project("forager", "forager-ops", &"d".repeat(64))
            .is_err());
    }

    #[test]
    fn manifest_rejects_missing_authority_and_out_of_window_routine() {
        let mut missing_denial = manifest();
        missing_denial.authority.does_not_authorize.pop();
        assert!(missing_denial.validate().is_err());

        let mut out_of_window = manifest();
        out_of_window.routine_bindings[0].schedule =
            OffdeskShiftRoutineScheduleV1::Once { at: at(10, 0) };
        assert!(out_of_window.validate().is_err());
    }

    #[test]
    fn manifest_rejects_duplicate_tasks_and_unbounded_budget() {
        let mut duplicate = manifest();
        duplicate
            .task_bindings
            .push(duplicate.task_bindings[0].clone());
        duplicate.budget.max_task_launches = 2;
        assert!(duplicate.validate().is_err());

        let mut excessive_attempts = manifest();
        excessive_attempts.budget.max_attempts_per_task = 11;
        assert!(excessive_attempts.validate().is_err());
    }

    #[test]
    fn routine_usage_recomputes_durable_bytes_and_rounded_cost() {
        let evidence = vec![OffdeskShiftRoutineEvidenceV1 {
            reference: "/tmp/wiki-prereview.json".to_string(),
            sha256: "d".repeat(64),
            size_bytes: 100,
        }];
        let usage = OffdeskShiftRoutineUsageV1 {
            schema: OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA.to_string(),
            measurement_complete: true,
            process_starts: 2,
            external_calls: 1,
            disk_bytes_written: 100,
            llm_tokens: 1_500,
            cost_micro_usd: 1,
            cost_rate_micro_usd_per_million_tokens: Some(333),
        };
        usage
            .validate_against(
                OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
                OffdeskShiftRoutineOutcomeV1::Succeeded,
                &evidence,
                OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
            )
            .expect("exact usage");

        let mut wrong_cost = usage.clone();
        wrong_cost.cost_micro_usd = 0;
        assert!(wrong_cost
            .validate_against(
                OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
                OffdeskShiftRoutineOutcomeV1::Succeeded,
                &evidence,
                OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
            )
            .is_err());

        let mut wrong_bytes = usage;
        wrong_bytes.disk_bytes_written = 99;
        assert!(wrong_bytes
            .validate_against(
                OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
                OffdeskShiftRoutineOutcomeV1::Succeeded,
                &evidence,
                OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
            )
            .is_err());
    }

    #[test]
    fn routine_receipt_rejects_a_cost_rate_not_bound_by_the_manifest() {
        let mut manifest = manifest();
        manifest.routine_bindings = vec![OffdeskShiftRoutineBindingV1 {
            routine_id: "routine_wiki".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
            schedule: OffdeskShiftRoutineScheduleV1::Once { at: at(1, 0) },
            project_key: Some("forager".to_string()),
            wiki_profile: Some("forager-ops".to_string()),
            project_root_identity_sha256: Some("b".repeat(64)),
            project_root_path: Some("/workspace/forager".to_string()),
        }];
        manifest.budget.max_routine_runs = 1;
        manifest.routine_resource_budget = Some(OffdeskShiftRoutineResourceBudgetV1 {
            max_process_starts: 2,
            max_external_calls: 1,
            max_disk_bytes_written: 100,
            max_llm_tokens: 2_000,
            max_cost_micro_usd: 1,
            llm_cost_rate_micro_usd_per_million_tokens: 333,
        });
        manifest.validate().expect("resource-bound manifest");
        let arm_receipt = arm_receipt(&manifest);
        let reservation = OffdeskShiftRoutineRunReservationV1 {
            schema: OFFDESK_SHIFT_ROUTINE_RUN_RESERVATION_SCHEMA.to_string(),
            run_id: offdesk_shift_routine_run_id(&manifest.shift_id, "routine_wiki", at(1, 0)),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: arm_receipt.manifest_sha256.clone(),
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(&arm_receipt, &manifest)
                .expect("arm receipt hash"),
            routine_id: "routine_wiki".to_string(),
            routine_kind: OffdeskShiftRoutineKindV1::WikiCandidateDistillation,
            scheduled_at: at(1, 0),
            reserved_at: at(1, 0),
        };
        let evidence = vec![OffdeskShiftRoutineEvidenceV1 {
            reference: "/tmp/wiki-prereview.json".to_string(),
            sha256: "d".repeat(64),
            size_bytes: 100,
        }];
        let mut outcome = OffdeskShiftRoutineOutcomeReceiptV1 {
            schema: OFFDESK_SHIFT_ROUTINE_OUTCOME_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("shift_routine_outcome_{}", reservation.run_id),
            run_id: reservation.run_id.clone(),
            shift_id: reservation.shift_id.clone(),
            manifest_sha256: reservation.manifest_sha256.clone(),
            arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
            routine_id: reservation.routine_id.clone(),
            routine_kind: reservation.routine_kind,
            scheduled_at: reservation.scheduled_at,
            reserved_at: reservation.reserved_at,
            completed_at: at(1, 1),
            outcome: OffdeskShiftRoutineOutcomeV1::Succeeded,
            summary: "candidate-only prereview complete".to_string(),
            evidence,
            usage: Some(OffdeskShiftRoutineUsageV1 {
                schema: OFFDESK_SHIFT_ROUTINE_USAGE_SCHEMA.to_string(),
                measurement_complete: true,
                process_starts: 2,
                external_calls: 1,
                disk_bytes_written: 100,
                llm_tokens: 1_500,
                cost_micro_usd: 1,
                cost_rate_micro_usd_per_million_tokens: Some(444),
            }),
            recovery_acknowledgement: None,
        };
        let error = outcome
            .validate_against(&reservation, &manifest, &arm_receipt)
            .expect_err("unbound rate must fail");
        assert!(
            error.to_string().contains("cost rate"),
            "unexpected validation error: {error}"
        );

        outcome
            .usage
            .as_mut()
            .expect("usage")
            .cost_rate_micro_usd_per_million_tokens = Some(333);
        outcome
            .validate_against(&reservation, &manifest, &arm_receipt)
            .expect("manifest-bound rate");
    }

    #[test]
    fn arm_receipt_cannot_extend_or_switch_the_manifest() {
        let manifest = manifest();
        let mut receipt = arm_receipt(&manifest);
        receipt.expires_at = at(10, 0);
        assert!(receipt.validate_against_manifest(&manifest).is_err());

        let mut switched = manifest.clone();
        switched.plan_source_sha256 = "d".repeat(64);
        let receipt = arm_receipt(&manifest);
        assert!(receipt.validate_against_manifest(&switched).is_err());
    }

    #[test]
    fn task_launch_reservation_is_exact_and_attempt_bounded() {
        let manifest = manifest();
        let receipt = arm_receipt(&manifest);
        let binding = &manifest.task_bindings[0];
        let reservation = OffdeskShiftTaskLaunchReservationV1 {
            schema: OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA.to_string(),
            launch_id: "shift_launch_exact".to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(&manifest).unwrap(),
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(&receipt, &manifest).unwrap(),
            task_id: binding.task_id.clone(),
            background_ticket_id: "shift_launch_exact".to_string(),
            runner_kind: binding.runner_kind,
            launch_spec_sha256: binding.launch_spec_sha256.clone(),
            execution_brief_sha256: binding.execution_brief_sha256.clone(),
            project_root_identity_sha256: binding.project_root_identity_sha256.clone(),
            host_policy_attestation_sha256: None,
            attempt_number: 1,
            reserved_at: at(1, 30),
        };
        reservation
            .validate_against(&manifest, &receipt)
            .expect("exact reservation");

        let mut changed = reservation.clone();
        changed.launch_spec_sha256 = "f".repeat(64);
        assert!(changed.validate_against(&manifest, &receipt).is_err());

        let mut exhausted = reservation;
        exhausted.attempt_number = 3;
        assert!(exhausted.validate_against(&manifest, &receipt).is_err());
    }

    #[test]
    fn task_ticket_and_terminal_outcome_are_bound_to_the_exact_probe() {
        let manifest = manifest();
        let arm_receipt = arm_receipt(&manifest);
        let binding = &manifest.task_bindings[0];
        let reservation = OffdeskShiftTaskLaunchReservationV1 {
            schema: OFFDESK_SHIFT_TASK_LAUNCH_RESERVATION_SCHEMA.to_string(),
            launch_id: "shift_launch_ticket_1".to_string(),
            shift_id: manifest.shift_id.clone(),
            manifest_sha256: offdesk_shift_manifest_sha256(&manifest).unwrap(),
            arm_receipt_sha256: offdesk_shift_arm_receipt_sha256(&arm_receipt, &manifest).unwrap(),
            task_id: binding.task_id.clone(),
            background_ticket_id: "shift_launch_ticket_1".to_string(),
            runner_kind: binding.runner_kind,
            launch_spec_sha256: binding.launch_spec_sha256.clone(),
            execution_brief_sha256: binding.execution_brief_sha256.clone(),
            project_root_identity_sha256: binding.project_root_identity_sha256.clone(),
            host_policy_attestation_sha256: None,
            attempt_number: 1,
            reserved_at: at(1, 30),
        };
        let mut probe = BackgroundProbe::new(
            reservation.background_ticket_id.clone(),
            reservation.runner_kind,
        );
        probe.task_id = Some(binding.task_id.clone());
        probe.request_id = Some(binding.request_id.clone());
        probe.project_key = Some(binding.project_key.clone());
        probe.capability_id = Some(binding.capability_id.clone());
        let ticket = OffdeskShiftTaskLaunchTicketReceiptV1 {
            schema: OFFDESK_SHIFT_TASK_LAUNCH_TICKET_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("shift_task_ticket_{}", reservation.launch_id),
            launch_id: reservation.launch_id.clone(),
            shift_id: reservation.shift_id.clone(),
            manifest_sha256: reservation.manifest_sha256.clone(),
            arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
            task_id: reservation.task_id.clone(),
            attempt_number: reservation.attempt_number,
            background_ticket_id: reservation.background_ticket_id.clone(),
            runner_kind: reservation.runner_kind,
            background_probe_sha256: offdesk_shift_background_probe_sha256(&probe).unwrap(),
            recorded_at: at(1, 31),
        };
        ticket
            .validate_probe(&probe, &reservation, &manifest, &arm_receipt)
            .expect("exact task ticket");

        let mut completed_probe = probe.clone();
        completed_probe.phase = BackgroundRunnerPhase::Completed;
        let outcome = OffdeskShiftTaskOutcomeReceiptV1 {
            schema: OFFDESK_SHIFT_TASK_OUTCOME_RECEIPT_SCHEMA.to_string(),
            receipt_id: format!("shift_task_outcome_{}", reservation.launch_id),
            launch_id: reservation.launch_id.clone(),
            shift_id: reservation.shift_id.clone(),
            manifest_sha256: reservation.manifest_sha256.clone(),
            arm_receipt_sha256: reservation.arm_receipt_sha256.clone(),
            task_id: reservation.task_id.clone(),
            attempt_number: reservation.attempt_number,
            background_ticket_id: reservation.background_ticket_id.clone(),
            outcome: OffdeskShiftTaskOutcomeV1::Completed,
            terminal_phase: Some(BackgroundRunnerPhase::Completed),
            launch_ticket_receipt_sha256: Some(
                offdesk_shift_task_ticket_receipt_sha256(
                    &ticket,
                    &reservation,
                    &manifest,
                    &arm_receipt,
                )
                .unwrap(),
            ),
            background_probe_sha256: Some(
                offdesk_shift_background_probe_sha256(&completed_probe).unwrap(),
            ),
            completed_at: at(1, 32),
            summary: "exact result artifact observed".to_string(),
        };
        outcome
            .validate_against(&reservation, Some(&ticket), &manifest, &arm_receipt)
            .expect("exact terminal outcome");

        let mut wrong_probe = probe;
        wrong_probe.task_id = Some("different_task".to_string());
        assert!(ticket
            .validate_probe(&wrong_probe, &reservation, &manifest, &arm_receipt)
            .is_err());

        let mut wrong_outcome = outcome;
        wrong_outcome.launch_ticket_receipt_sha256 = Some("f".repeat(64));
        assert!(wrong_outcome
            .validate_against(&reservation, Some(&ticket), &manifest, &arm_receipt,)
            .is_err());
    }

    #[test]
    fn state_machine_requires_review_before_acceptance() {
        let manifest = manifest();
        let receipt = arm_receipt(&manifest);
        let armed = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Armed,
            1,
            at(0, 0),
        );
        let running = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Running,
            2,
            at(0, 1),
        );
        let review_ready = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::ReviewReady,
            3,
            at(9, 0),
        );
        let accepted = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Accepted,
            4,
            at(9, 1),
        );

        validate_offdesk_shift_state_transition(&armed, &running, &manifest, &receipt)
            .expect("arm to running");
        validate_offdesk_shift_state_transition(&running, &review_ready, &manifest, &receipt)
            .expect("running to review ready");
        validate_offdesk_shift_state_transition(&review_ready, &accepted, &manifest, &receipt)
            .expect("review ready to accepted");

        let direct_accept = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Accepted,
            2,
            at(0, 1),
        );
        assert!(validate_offdesk_shift_state_transition(
            &armed,
            &direct_accept,
            &manifest,
            &receipt
        )
        .is_err());
    }

    #[test]
    fn running_state_cannot_outlive_arm_receipt() {
        let manifest = manifest();
        let receipt = arm_receipt(&manifest);
        let running = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Running,
            2,
            at(9, 1),
        );
        assert!(running.validate_against(&manifest, &receipt).is_err());
    }

    #[test]
    fn dispatch_authority_is_inert_before_start_after_expiry_and_in_terminal_state() {
        let mut manifest = manifest();
        manifest.window.starts_at = at(1, 0);
        manifest.routine_bindings[0].schedule = OffdeskShiftRoutineScheduleV1::Interval {
            starts_at: at(1, 0),
            stops_at: at(9, 0),
            every_minutes: 10,
        };
        let receipt = arm_receipt(&manifest);
        let armed = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Armed,
            1,
            at(0, 30),
        );
        assert!(
            validate_offdesk_shift_dispatch_authority(&manifest, &receipt, &armed, at(0, 45))
                .is_err()
        );
        assert!(
            validate_offdesk_shift_dispatch_authority(&manifest, &receipt, &armed, at(1, 30))
                .is_ok()
        );
        assert!(
            validate_offdesk_shift_dispatch_authority(&manifest, &receipt, &armed, at(9, 1))
                .is_err()
        );

        let disarmed = state(
            &manifest,
            &receipt,
            OffdeskShiftStatusV1::Disarmed,
            2,
            at(2, 0),
        );
        assert!(validate_offdesk_shift_dispatch_authority(
            &manifest,
            &receipt,
            &disarmed,
            at(2, 1)
        )
        .is_err());
    }

    #[test]
    fn routine_recovery_acknowledgement_denies_success_and_new_effects() {
        let acknowledgement = OffdeskShiftRoutineRecoveryAcknowledgementV1 {
            schema: OFFDESK_SHIFT_ROUTINE_RECOVERY_ACKNOWLEDGEMENT_SCHEMA.to_string(),
            acknowledged_at: at(2, 0),
            acknowledged_by: "local_operator".to_string(),
            reason: "timer exited after reserving".to_string(),
            execution_result_unknown: true,
            success_not_asserted: true,
            does_not_authorize: OFFDESK_SHIFT_ROUTINE_RECOVERY_REQUIRED_DENIALS
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
        };
        acknowledgement.validate().expect("strict acknowledgement");

        let mut success_claim = acknowledgement.clone();
        success_claim.success_not_asserted = false;
        assert!(success_claim.validate().is_err());

        let mut missing_denial = acknowledgement.clone();
        missing_denial.does_not_authorize.pop();
        assert!(missing_denial.validate().is_err());

        let mut duplicate_denial = acknowledgement;
        duplicate_denial.does_not_authorize[3] = "accepted_truth".to_string();
        assert!(duplicate_denial.validate().is_err());
    }

    #[test]
    fn template_instantiation_is_one_inert_canonical_utc_slot() {
        let starts_at = at(0, 0);
        let template = OffdeskShiftTemplateInstantiationV1::new(
            "Nightly-Review",
            OffdeskShiftTemplateCadenceV1::Daily,
            starts_at,
        )
        .expect("daily template");
        assert_eq!(template.template_key, "nightly-review");
        assert_eq!(template.slot, "2026-08-28");
        assert!(!template.recurrence_authorized);
        assert!(template.next_occurrence_requires_new_manifest);
        assert!(template.arm_approval_required);

        let mut manifest = manifest();
        manifest.shift_id =
            offdesk_shift_template_shift_id(&template.template_key, template.cadence, starts_at)
                .expect("template shift ID");
        manifest.template_instantiation = Some(template.clone());
        manifest.validate().expect("template-bound manifest");

        let mut recurring = manifest.clone();
        recurring
            .template_instantiation
            .as_mut()
            .expect("template provenance")
            .recurrence_authorized = true;
        assert!(recurring.validate().is_err());

        let mut wrong_slot = manifest.clone();
        wrong_slot
            .template_instantiation
            .as_mut()
            .expect("template provenance")
            .slot = "2026-08-29".to_string();
        assert!(wrong_slot.validate().is_err());

        let weekly = OffdeskShiftTemplateInstantiationV1::new(
            "knowledge-review",
            OffdeskShiftTemplateCadenceV1::Weekly,
            starts_at,
        )
        .expect("weekly template");
        assert_eq!(weekly.slot, "2026-W35");
        assert!(
            offdesk_shift_template_shift_id(&weekly.template_key, weekly.cadence, starts_at)
                .expect("weekly shift ID")
                .ends_with("weekly_2026W35")
        );
    }
}
