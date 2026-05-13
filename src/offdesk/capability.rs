//! Task Team capability registry for planning and worker gating.

use serde::{Deserialize, Serialize};

use super::approval::{ApprovalMode, RiskLevel};

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
    pub dashboard_label: String,
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
    dashboard_label: &'a str,
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
            dashboard_label: "Inspect Status",
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
            dashboard_label: "Dispatch Runtime Work",
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
            dashboard_label: "Retry Runtime Work",
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
            dashboard_label: "Replan Runtime Work",
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
            dashboard_label: "Launch Background Runner",
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
            dashboard_label: "Poll Background Runner",
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
            dashboard_label: "Sync Back Canonical State",
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
            dashboard_label: "Apply Canonical Mutation",
        }),
    ])
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
        dashboard_label: seed.dashboard_label.to_string(),
    }
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
}
