//! Canonical built-in enterprise policy profiles for bounded Offdesk shifts.

use anyhow::{ensure, Context, Result};
use serde::{Deserialize, Serialize};

use super::background::BackgroundRunnerKind;
use super::shift::{
    OffdeskShiftBudgetV1, OffdeskShiftRoutineBindingV1, OffdeskShiftRoutineKindV1,
    OffdeskShiftRoutineResourceBudgetV1, OffdeskShiftTaskBindingV1,
};

pub const OFFDESK_SHIFT_ENTERPRISE_POLICY_SCHEMA: &str = "offdesk_shift_enterprise_policy.v1";
pub const OFFDESK_SHIFT_ENTERPRISE_POLICY_REPORT_SCHEMA: &str =
    "offdesk_shift_enterprise_policy_report.v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftEnterpriseProfileKeyV1 {
    SingleOwnerLocal,
    MultiOperatorLocal,
    AirgappedLocal,
}

impl OffdeskShiftEnterpriseProfileKeyV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SingleOwnerLocal => "single_owner_local",
            Self::MultiOperatorLocal => "multi_operator_local",
            Self::AirgappedLocal => "airgapped_local",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftOperatorModeV1 {
    SingleOwner,
    MultiOperator,
}

impl OffdeskShiftOperatorModeV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SingleOwner => "single_owner",
            Self::MultiOperator => "multi_operator",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftNotificationRouteV1 {
    LocalEvidenceOnly,
    TelegramOwnerPrivate,
}

impl OffdeskShiftNotificationRouteV1 {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LocalEvidenceOnly => "local_evidence_only",
            Self::TelegramOwnerPrivate => "telegram_owner_private",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftForagerNetworkEffectV1 {
    LoopbackModel,
    TelegramBotApi,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OffdeskShiftChildTaskNetworkBoundaryV1 {
    HostPolicyRequired,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftEnterpriseBudgetCeilingV1 {
    pub max_concurrent_tasks: u16,
    pub max_task_launches: u16,
    pub max_attempts_per_task: u16,
    pub max_routine_runs: u16,
    pub max_routine_process_starts: u64,
    pub max_routine_external_calls: u64,
    pub max_routine_disk_bytes: u64,
    pub max_routine_llm_tokens: u64,
    pub max_routine_cost_micro_usd: u64,
    pub max_llm_cost_rate_micro_usd_per_million_tokens: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftMandatoryReviewsV1 {
    pub operator_arm_required: bool,
    pub fresh_task_dispatch_approval_required: bool,
    pub ondesk_acceptance_required: bool,
    pub plan_revision_review_required: bool,
    pub retention_mutation_approval_required: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OffdeskShiftEnterprisePolicyV1 {
    pub schema: String,
    pub profile_key: OffdeskShiftEnterpriseProfileKeyV1,
    pub operator_mode: OffdeskShiftOperatorModeV1,
    pub allowed_runners: Vec<BackgroundRunnerKind>,
    pub allowed_forager_network_effects: Vec<OffdeskShiftForagerNetworkEffectV1>,
    pub notification_route: OffdeskShiftNotificationRouteV1,
    pub child_task_network_boundary: OffdeskShiftChildTaskNetworkBoundaryV1,
    pub forager_controls_child_task_network: bool,
    pub budget_ceiling: OffdeskShiftEnterpriseBudgetCeilingV1,
    pub mandatory_reviews: OffdeskShiftMandatoryReviewsV1,
}

impl OffdeskShiftEnterprisePolicyV1 {
    pub fn built_in(key: OffdeskShiftEnterpriseProfileKeyV1) -> Self {
        let (operator_mode, notification_route, allowed_forager_network_effects) = match key {
            OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal => (
                OffdeskShiftOperatorModeV1::SingleOwner,
                OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
                vec![
                    OffdeskShiftForagerNetworkEffectV1::LoopbackModel,
                    OffdeskShiftForagerNetworkEffectV1::TelegramBotApi,
                ],
            ),
            OffdeskShiftEnterpriseProfileKeyV1::MultiOperatorLocal => (
                OffdeskShiftOperatorModeV1::MultiOperator,
                OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate,
                vec![
                    OffdeskShiftForagerNetworkEffectV1::LoopbackModel,
                    OffdeskShiftForagerNetworkEffectV1::TelegramBotApi,
                ],
            ),
            OffdeskShiftEnterpriseProfileKeyV1::AirgappedLocal => (
                OffdeskShiftOperatorModeV1::SingleOwner,
                OffdeskShiftNotificationRouteV1::LocalEvidenceOnly,
                vec![OffdeskShiftForagerNetworkEffectV1::LoopbackModel],
            ),
        };
        Self {
            schema: OFFDESK_SHIFT_ENTERPRISE_POLICY_SCHEMA.to_string(),
            profile_key: key,
            operator_mode,
            allowed_runners: vec![
                BackgroundRunnerKind::LocalBackground,
                BackgroundRunnerKind::LocalTmux,
            ],
            allowed_forager_network_effects,
            notification_route,
            child_task_network_boundary: OffdeskShiftChildTaskNetworkBoundaryV1::HostPolicyRequired,
            forager_controls_child_task_network: false,
            budget_ceiling: OffdeskShiftEnterpriseBudgetCeilingV1 {
                max_concurrent_tasks: 8,
                max_task_launches: 64,
                max_attempts_per_task: 3,
                max_routine_runs: 512,
                max_routine_process_starts: 1024,
                max_routine_external_calls: 512,
                max_routine_disk_bytes: 512 * 1024 * 1024,
                max_routine_llm_tokens: 1_000_000,
                max_routine_cost_micro_usd: 100_000_000,
                max_llm_cost_rate_micro_usd_per_million_tokens: 100_000_000,
            },
            mandatory_reviews: OffdeskShiftMandatoryReviewsV1 {
                operator_arm_required: true,
                fresh_task_dispatch_approval_required: true,
                ondesk_acceptance_required: true,
                plan_revision_review_required: true,
                retention_mutation_approval_required: true,
            },
        }
    }

    pub fn all_built_in() -> Vec<Self> {
        [
            OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
            OffdeskShiftEnterpriseProfileKeyV1::MultiOperatorLocal,
            OffdeskShiftEnterpriseProfileKeyV1::AirgappedLocal,
        ]
        .into_iter()
        .map(Self::built_in)
        .collect()
    }

    pub fn validate(&self) -> Result<()> {
        ensure!(
            self == &Self::built_in(self.profile_key),
            "shift enterprise policy must equal one canonical built-in profile"
        );
        Ok(())
    }

    pub(crate) fn validate_manifest_components(
        &self,
        task_bindings: &[OffdeskShiftTaskBindingV1],
        routine_bindings: &[OffdeskShiftRoutineBindingV1],
        budget: &OffdeskShiftBudgetV1,
        routine_budget: Option<&OffdeskShiftRoutineResourceBudgetV1>,
    ) -> Result<()> {
        self.validate()?;
        ensure!(
            task_bindings
                .iter()
                .all(|binding| self.allowed_runners.contains(&binding.runner_kind)),
            "shift task runner is not allowed by the enterprise policy"
        );
        let ceiling = &self.budget_ceiling;
        ensure!(
            budget.max_concurrent_tasks <= ceiling.max_concurrent_tasks
                && budget.max_task_launches <= ceiling.max_task_launches
                && budget.max_attempts_per_task <= ceiling.max_attempts_per_task
                && budget.max_routine_runs <= ceiling.max_routine_runs,
            "shift execution budget exceeds the enterprise policy ceiling"
        );
        if routine_bindings.is_empty() {
            ensure!(
                routine_budget.is_none(),
                "task-only enterprise shift must not carry a routine budget"
            );
        } else {
            let routine_budget =
                routine_budget.context("enterprise shift routines require a resource budget")?;
            ensure!(
                routine_budget.max_process_starts <= ceiling.max_routine_process_starts
                    && routine_budget.max_external_calls <= ceiling.max_routine_external_calls
                    && routine_budget.max_disk_bytes_written <= ceiling.max_routine_disk_bytes
                    && routine_budget.max_llm_tokens <= ceiling.max_routine_llm_tokens
                    && routine_budget.max_cost_micro_usd <= ceiling.max_routine_cost_micro_usd
                    && routine_budget.llm_cost_rate_micro_usd_per_million_tokens
                        <= ceiling.max_llm_cost_rate_micro_usd_per_million_tokens,
                "shift routine budget exceeds the enterprise policy ceiling"
            );
        }
        if routine_bindings.iter().any(|binding| {
            binding.routine_kind == OffdeskShiftRoutineKindV1::WikiCandidateDistillation
        }) {
            ensure!(
                self.allowed_forager_network_effects
                    .contains(&OffdeskShiftForagerNetworkEffectV1::LoopbackModel),
                "Wiki routine requires the enterprise loopback-model effect"
            );
        }
        if routine_bindings
            .iter()
            .any(|binding| binding.routine_kind == OffdeskShiftRoutineKindV1::MorningReviewBrief)
            && self.notification_route == OffdeskShiftNotificationRouteV1::TelegramOwnerPrivate
        {
            ensure!(
                self.allowed_forager_network_effects
                    .contains(&OffdeskShiftForagerNetworkEffectV1::TelegramBotApi),
                "Telegram notification route requires the enterprise Telegram network effect"
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftEnterprisePolicyReportAuthorityV1 {
    pub read_only: bool,
    pub selects_or_arms_shift: bool,
    pub verifies_host_network_policy: bool,
    pub authorizes_child_task_network: bool,
    pub does_not_authorize: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OffdeskShiftEnterprisePolicyReportV1 {
    pub schema: String,
    pub default_profile_key: OffdeskShiftEnterpriseProfileKeyV1,
    pub profiles: Vec<OffdeskShiftEnterprisePolicyV1>,
    pub authority: OffdeskShiftEnterprisePolicyReportAuthorityV1,
}

pub fn build_offdesk_shift_enterprise_policy_report() -> OffdeskShiftEnterprisePolicyReportV1 {
    OffdeskShiftEnterprisePolicyReportV1 {
        schema: OFFDESK_SHIFT_ENTERPRISE_POLICY_REPORT_SCHEMA.to_string(),
        default_profile_key: OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
        profiles: OffdeskShiftEnterprisePolicyV1::all_built_in(),
        authority: OffdeskShiftEnterprisePolicyReportAuthorityV1 {
            read_only: true,
            selects_or_arms_shift: false,
            verifies_host_network_policy: false,
            authorizes_child_task_network: false,
            does_not_authorize: vec![
                "shift_prepare".to_string(),
                "shift_arm".to_string(),
                "task_dispatch".to_string(),
                "child_task_network".to_string(),
                "notification_delivery".to_string(),
            ],
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn built_in_profiles_keep_multi_operator_explicit_and_airgap_local_only() {
        let profiles = OffdeskShiftEnterprisePolicyV1::all_built_in();
        assert_eq!(profiles.len(), 3);
        assert_eq!(
            profiles[0].profile_key,
            OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal
        );
        assert_eq!(
            profiles[1].operator_mode,
            OffdeskShiftOperatorModeV1::MultiOperator
        );
        assert_eq!(
            profiles[2].notification_route,
            OffdeskShiftNotificationRouteV1::LocalEvidenceOnly
        );
        assert!(!profiles[2]
            .allowed_forager_network_effects
            .contains(&OffdeskShiftForagerNetworkEffectV1::TelegramBotApi));
        assert!(profiles
            .iter()
            .all(|profile| !profile.forager_controls_child_task_network));
    }

    #[test]
    fn built_in_profile_rejects_remote_runner_and_budget_over_ceiling() {
        let policy = OffdeskShiftEnterprisePolicyV1::built_in(
            OffdeskShiftEnterpriseProfileKeyV1::SingleOwnerLocal,
        );
        let remote = OffdeskShiftTaskBindingV1 {
            task_id: "task_remote".to_string(),
            request_id: "request_remote".to_string(),
            project_key: "project".to_string(),
            capability_id: "dispatch.runtime".to_string(),
            runner_kind: BackgroundRunnerKind::RemoteWorker,
            project_root_identity_sha256: "a".repeat(64),
            launch_spec_sha256: "b".repeat(64),
            execution_brief_sha256: "c".repeat(64),
            ondesk_source_snapshot: None,
        };
        let budget = OffdeskShiftBudgetV1 {
            max_concurrent_tasks: 1,
            max_task_launches: 1,
            max_attempts_per_task: 1,
            max_routine_runs: 0,
        };
        assert!(policy
            .validate_manifest_components(&[remote], &[], &budget, None)
            .is_err());

        let over_budget = OffdeskShiftBudgetV1 {
            max_attempts_per_task: policy.budget_ceiling.max_attempts_per_task + 1,
            ..budget
        };
        assert!(policy
            .validate_manifest_components(&[], &[], &over_budget, None)
            .is_err());
    }
}
