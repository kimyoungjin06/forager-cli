//! Task Team capability registry for planning and worker gating.

use serde::{Deserialize, Serialize};
use std::path::Path;

use super::approval::{ApprovalMode, ApprovalScope, RiskLevel};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CapabilityRisk {
    Safe,
    RuntimeMutation,
    CanonicalMutation,
    Destructive,
    ExternalSideEffect,
}

impl From<CapabilityRisk> for RiskLevel {
    fn from(value: CapabilityRisk) -> Self {
        match value {
            CapabilityRisk::Safe => RiskLevel::Safe,
            CapabilityRisk::RuntimeMutation => RiskLevel::RuntimeMutation,
            CapabilityRisk::CanonicalMutation => RiskLevel::CanonicalMutation,
            CapabilityRisk::Destructive => RiskLevel::Destructive,
            CapabilityRisk::ExternalSideEffect => RiskLevel::ExternalSideEffect,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityDescriptor {
    pub capability_id: String,
    pub owner_module: String,
    pub runner_backend: String,
    pub risk_level: CapabilityRisk,
    pub read_scope: String,
    pub write_scope: String,
    #[serde(default)]
    pub required_env_names: Vec<String>,
    pub offdesk_allowed: bool,
    pub approval_requirement: ApprovalMode,
    #[serde(default = "default_approval_scope")]
    pub approval_scope: ApprovalScope,
    pub dashboard_label: String,
    #[serde(default)]
    pub operator_label: String,
    #[serde(default)]
    pub retry_eligible: bool,
    #[serde(default)]
    pub resume_eligible: bool,
    #[serde(default)]
    pub required_artifacts: Vec<CapabilityArtifactContract>,
    #[serde(default)]
    pub produced_artifacts: Vec<CapabilityArtifactContract>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityArtifactContract {
    pub artifact_id: String,
    pub path_hint: String,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityArtifactRef {
    pub artifact_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    #[serde(default)]
    pub present: bool,
}

impl CapabilityArtifactRef {
    pub fn new(artifact_id: impl Into<String>, path: Option<impl Into<String>>) -> Self {
        let path = path.map(Into::into);
        let present = path
            .as_deref()
            .map(|path| Path::new(path).exists())
            .unwrap_or(true);
        Self {
            artifact_id: artifact_id.into(),
            path,
            present,
        }
    }

    fn satisfies(&self, artifact_id: &str) -> bool {
        if self.artifact_id != artifact_id {
            return false;
        }
        if let Some(path) = self.path.as_deref() {
            Path::new(path).exists()
        } else {
            self.present
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityArtifactCheck {
    pub capability_id: String,
    pub satisfied: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub required_artifacts: Vec<CapabilityArtifactContract>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub missing_artifact_ids: Vec<String>,
}

struct CapabilitySeed<'a> {
    capability_id: &'a str,
    owner_module: &'a str,
    runner_backend: &'a str,
    risk_level: CapabilityRisk,
    read_scope: &'a str,
    write_scope: &'a str,
    offdesk_allowed: bool,
    approval_requirement: ApprovalMode,
    approval_scope: ApprovalScope,
    dashboard_label: &'a str,
    operator_label: &'a str,
    retry_eligible: bool,
    resume_eligible: bool,
    required_artifacts: &'a [ArtifactSeed<'a>],
    produced_artifacts: &'a [ArtifactSeed<'a>],
}

#[derive(Debug, Clone, Copy)]
struct ArtifactSeed<'a> {
    artifact_id: &'a str,
    path_hint: &'a str,
    description: &'a str,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CapabilityGate {
    Allowed,
    Blocked(String),
    RequiresApproval(ApprovalMode),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityRegistry {
    capabilities: Vec<CapabilityDescriptor>,
}

impl CapabilityRegistry {
    pub fn new(capabilities: Vec<CapabilityDescriptor>) -> Self {
        Self { capabilities }
    }

    pub fn all(&self) -> &[CapabilityDescriptor] {
        &self.capabilities
    }

    pub fn get(&self, capability_id: &str) -> Option<&CapabilityDescriptor> {
        self.capabilities
            .iter()
            .find(|capability| capability.capability_id == capability_id)
    }

    pub fn gate_offdesk(&self, capability_id: &str) -> CapabilityGate {
        let Some(capability) = self.get(capability_id) else {
            return CapabilityGate::Blocked(format!("unknown capability: {capability_id}"));
        };
        if !capability.offdesk_allowed {
            return CapabilityGate::Blocked(format!(
                "{} is not allowed in offdesk mode",
                capability.capability_id
            ));
        }
        if capability.risk_level == CapabilityRisk::Safe
            && capability.approval_requirement == ApprovalMode::EnvelopeAuto
        {
            CapabilityGate::Allowed
        } else {
            CapabilityGate::RequiresApproval(capability.approval_requirement)
        }
    }

    pub fn validate_required_artifacts(
        &self,
        capability_id: &str,
        provided: &[CapabilityArtifactRef],
    ) -> CapabilityArtifactCheck {
        let Some(capability) = self.get(capability_id) else {
            return CapabilityArtifactCheck {
                capability_id: capability_id.to_string(),
                satisfied: false,
                required_artifacts: Vec::new(),
                missing_artifact_ids: vec![format!("unknown capability: {capability_id}")],
            };
        };
        capability.validate_required_artifacts(provided)
    }
}

impl CapabilityDescriptor {
    pub fn validate_required_artifacts(
        &self,
        provided: &[CapabilityArtifactRef],
    ) -> CapabilityArtifactCheck {
        let missing_artifact_ids = self
            .required_artifacts
            .iter()
            .filter(|required| {
                !provided
                    .iter()
                    .any(|artifact| artifact.satisfies(&required.artifact_id))
            })
            .map(|artifact| artifact.artifact_id.clone())
            .collect::<Vec<_>>();
        CapabilityArtifactCheck {
            capability_id: self.capability_id.clone(),
            satisfied: missing_artifact_ids.is_empty(),
            required_artifacts: self.required_artifacts.clone(),
            missing_artifact_ids,
        }
    }
}

pub fn default_capability_registry() -> CapabilityRegistry {
    CapabilityRegistry::new(vec![
        capability(CapabilitySeed {
            capability_id: "inspect.status",
            owner_module: "session.status",
            runner_backend: "local",
            risk_level: CapabilityRisk::Safe,
            read_scope: "sessions/background state",
            write_scope: "none",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::EnvelopeAuto,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Inspect Status",
            operator_label: "Inspect status",
            retry_eligible: false,
            resume_eligible: false,
            required_artifacts: &[],
            produced_artifacts: &[],
        }),
        capability(CapabilitySeed {
            capability_id: "dispatch.runtime",
            owner_module: "offdesk.scheduler",
            runner_backend: "local_background",
            risk_level: CapabilityRisk::RuntimeMutation,
            read_scope: "execution brief",
            write_scope: "background_runs.json",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Dispatch Runtime Work",
            operator_label: "Dispatch runtime work",
            retry_eligible: true,
            resume_eligible: true,
            required_artifacts: &[],
            produced_artifacts: &[artifact(
                "background_run",
                "background_runs.json",
                "background runner ticket and recovery probe",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "dispatch.provider_fallback",
            owner_module: "offdesk.scheduler",
            runner_backend: "local",
            risk_level: CapabilityRisk::RuntimeMutation,
            read_scope: "provider capacity and fallback metadata",
            write_scope: "offdesk task provider/model targets",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Apply Provider Fallback",
            operator_label: "Apply provider fallback",
            retry_eligible: false,
            resume_eligible: false,
            required_artifacts: &[],
            produced_artifacts: &[artifact(
                "offdesk_task",
                "offdesk_tasks.json",
                "retargeted provider/model for matching queued tasks",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "retry.runtime",
            owner_module: "offdesk.scheduler",
            runner_backend: "local_background",
            risk_level: CapabilityRisk::RuntimeMutation,
            read_scope: "background_runs.json",
            write_scope: "background_runs.json",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Retry Runtime Work",
            operator_label: "Retry runtime work",
            retry_eligible: true,
            resume_eligible: true,
            required_artifacts: &[],
            produced_artifacts: &[artifact(
                "background_run",
                "background_runs.json",
                "new background runner ticket for retry",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "replan.runtime",
            owner_module: "offdesk.planner",
            runner_backend: "local",
            risk_level: CapabilityRisk::RuntimeMutation,
            read_scope: "task state",
            write_scope: "task_resume_state.json",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Replan Runtime Work",
            operator_label: "Replan runtime work",
            retry_eligible: false,
            resume_eligible: true,
            required_artifacts: &[artifact(
                "task_resume_state",
                "task_resume_state.json",
                "resume evidence for interrupted task",
            )],
            produced_artifacts: &[artifact(
                "offdesk_task",
                "offdesk_tasks.json",
                "updated task queue state",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "background.launch",
            owner_module: "offdesk.runner",
            runner_backend: "local_tmux",
            risk_level: CapabilityRisk::RuntimeMutation,
            read_scope: "execution brief",
            write_scope: "background_runs.json",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Launch Background Runner",
            operator_label: "Launch background runner",
            retry_eligible: true,
            resume_eligible: true,
            required_artifacts: &[],
            produced_artifacts: &[artifact(
                "background_run",
                "background_runs.json",
                "background runner ticket and recovery probe",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "background.poll",
            owner_module: "offdesk.runner",
            runner_backend: "local",
            risk_level: CapabilityRisk::Safe,
            read_scope: "background_runs.json",
            write_scope: "notification counters",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::EnvelopeAuto,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Poll Background Runner",
            operator_label: "Poll background runner",
            retry_eligible: false,
            resume_eligible: false,
            required_artifacts: &[],
            produced_artifacts: &[artifact(
                "background_recovery_evidence",
                "background_runs.json",
                "updated background recovery evidence",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "canonical.syncback",
            owner_module: "offdesk.sync",
            runner_backend: "local",
            risk_level: CapabilityRisk::CanonicalMutation,
            read_scope: "mutation snapshots",
            write_scope: "project artifacts",
            offdesk_allowed: true,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Sync Back Canonical State",
            operator_label: "Sync back canonical state",
            retry_eligible: false,
            resume_eligible: false,
            required_artifacts: &[artifact(
                "mutation_snapshot",
                "mutation_snapshots/<mutation_id>.json",
                "pre-mutation snapshot and rollback verification evidence",
            )],
            produced_artifacts: &[artifact(
                "project_artifact",
                "project artifacts",
                "operator-approved canonical syncback artifact",
            )],
        }),
        capability(CapabilitySeed {
            capability_id: "canonical.apply",
            owner_module: "offdesk.sync",
            runner_backend: "local",
            risk_level: CapabilityRisk::CanonicalMutation,
            read_scope: "mutation snapshots",
            write_scope: "project workspace",
            offdesk_allowed: false,
            approval_requirement: ApprovalMode::OperatorRequired,
            approval_scope: ApprovalScope::Once,
            dashboard_label: "Apply Canonical Mutation",
            operator_label: "Apply canonical mutation",
            retry_eligible: false,
            resume_eligible: false,
            required_artifacts: &[
                artifact(
                    "mutation_snapshot",
                    "mutation_snapshots/<mutation_id>.json",
                    "pre-mutation snapshot and rollback verification evidence",
                ),
                artifact(
                    "restore_plan",
                    "restore-plan output",
                    "operator-reviewed dry-run restore plan",
                ),
            ],
            produced_artifacts: &[artifact(
                "project_workspace_change",
                "project workspace",
                "direct canonical workspace mutation",
            )],
        }),
    ])
}

const fn artifact<'a>(
    artifact_id: &'a str,
    path_hint: &'a str,
    description: &'a str,
) -> ArtifactSeed<'a> {
    ArtifactSeed {
        artifact_id,
        path_hint,
        description,
    }
}

fn capability(seed: CapabilitySeed<'_>) -> CapabilityDescriptor {
    CapabilityDescriptor {
        capability_id: seed.capability_id.to_string(),
        owner_module: seed.owner_module.to_string(),
        runner_backend: seed.runner_backend.to_string(),
        risk_level: seed.risk_level,
        read_scope: seed.read_scope.to_string(),
        write_scope: seed.write_scope.to_string(),
        required_env_names: Vec::new(),
        offdesk_allowed: seed.offdesk_allowed,
        approval_requirement: seed.approval_requirement,
        approval_scope: seed.approval_scope,
        dashboard_label: seed.dashboard_label.to_string(),
        operator_label: seed.operator_label.to_string(),
        retry_eligible: seed.retry_eligible,
        resume_eligible: seed.resume_eligible,
        required_artifacts: seed
            .required_artifacts
            .iter()
            .map(|artifact| capability_artifact(*artifact))
            .collect(),
        produced_artifacts: seed
            .produced_artifacts
            .iter()
            .map(|artifact| capability_artifact(*artifact))
            .collect(),
    }
}

fn capability_artifact(seed: ArtifactSeed<'_>) -> CapabilityArtifactContract {
    CapabilityArtifactContract {
        artifact_id: seed.artifact_id.to_string(),
        path_hint: seed.path_hint.to_string(),
        description: seed.description.to_string(),
    }
}

fn default_approval_scope() -> ApprovalScope {
    ApprovalScope::Once
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unsafe_capabilities_require_approval_metadata() {
        let registry = default_capability_registry();
        for capability in registry.all() {
            if capability.risk_level != CapabilityRisk::Safe {
                assert_eq!(
                    capability.approval_requirement,
                    ApprovalMode::OperatorRequired
                );
                assert_eq!(capability.approval_scope, ApprovalScope::Once);
                assert!(!capability.operator_label.trim().is_empty());
            }
        }
    }

    #[test]
    fn offdesk_blocks_capability_marked_not_allowed() {
        let registry = default_capability_registry();
        let gate = registry.gate_offdesk("canonical.apply");
        assert!(matches!(gate, CapabilityGate::Blocked(_)));
    }

    #[test]
    fn safe_capability_can_run_offdesk_without_operator_approval() {
        let registry = default_capability_registry();
        assert_eq!(
            registry.gate_offdesk("inspect.status"),
            CapabilityGate::Allowed
        );
    }

    #[test]
    fn canonical_syncback_declares_snapshot_requirement() {
        let registry = default_capability_registry();
        let capability = registry
            .get("canonical.syncback")
            .expect("canonical syncback capability");

        assert!(capability
            .required_artifacts
            .iter()
            .any(|artifact| artifact.artifact_id == "mutation_snapshot"));
        assert!(!capability.retry_eligible);
        assert!(!capability.resume_eligible);
    }

    #[test]
    fn runtime_capabilities_expose_background_artifact_contracts() {
        let registry = default_capability_registry();
        for capability_id in ["dispatch.runtime", "background.launch", "retry.runtime"] {
            let capability = registry.get(capability_id).expect("runtime capability");
            assert!(capability.retry_eligible);
            assert!(capability.resume_eligible);
            assert!(capability
                .produced_artifacts
                .iter()
                .any(|artifact| artifact.artifact_id == "background_run"));
        }
    }

    #[test]
    fn provider_fallback_capability_requires_operator_runtime_approval() {
        let registry = default_capability_registry();
        let capability = registry
            .get("dispatch.provider_fallback")
            .expect("provider fallback capability");

        assert_eq!(capability.risk_level, CapabilityRisk::RuntimeMutation);
        assert_eq!(
            capability.approval_requirement,
            ApprovalMode::OperatorRequired
        );
        assert!(capability.offdesk_allowed);
        assert!(!capability.retry_eligible);
        assert!(!capability.resume_eligible);
    }

    #[test]
    fn required_artifact_validation_reports_missing_and_satisfied_refs() {
        let registry = default_capability_registry();
        let missing = registry.validate_required_artifacts("canonical.syncback", &[]);
        assert!(!missing.satisfied);
        assert_eq!(missing.missing_artifact_ids, vec!["mutation_snapshot"]);

        let present = registry.validate_required_artifacts(
            "canonical.syncback",
            &[CapabilityArtifactRef {
                artifact_id: "mutation_snapshot".to_string(),
                path: None,
                present: true,
            }],
        );
        assert!(present.satisfied);
    }
}
