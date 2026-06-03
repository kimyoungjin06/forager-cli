//! `forager project` subcommands for project-level operation initialization.

use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use clap::{Args, Subcommand, ValueEnum};
use serde::Serialize;
use std::collections::BTreeSet;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

use super::artifact_index::{
    self, ProjectArtifactIndexArgs, ProjectRetentionApplyArgs, ProjectRetentionPromoteArgs,
    ProjectRetentionRequestArgs, ProjectRetentionReviewArgs,
};
use super::project_audit::{run_audit_docs, ProjectAuditDocsArgs};
use crate::offdesk::operator_safe_text;
use crate::session::get_profile_dir;

const PROFILE_FILE: &str = "PROJECT_OPERATION_PROFILE.json";
const ONBOARDING_FILE: &str = "PROJECT_ONBOARDING.md";
const MODULE_CANDIDATES_FILE: &str = "MODULE_CANDIDATES.json";
const MODULE_PREFLIGHT_FILE: &str = "MODULE_OPERATION_PREFLIGHT.json";
const EVIDENCE_PLAN_FILE: &str = "EVIDENCE_COLLECTOR_PLAN.md";
const GOVERNANCE_HINTS_FILE: &str = "GOVERNANCE_SURFACE_HINTS.md";
const WIKI_SEEDS_FILE: &str = "WIKI_SEED_CANDIDATES.json";
const ONDESK_PACKAGE_FILE: &str = "ONDESK_START_PACKAGE.md";
const OFFDESK_READY_FILE: &str = "OFFDESK_READY_CHECK.json";

#[derive(Subcommand)]
pub enum ProjectCommands {
    /// Create a read-only project operation initialization packet
    Init(ProjectInitArgs),

    /// Apply reviewed governance surface templates to a project
    ApplyGovernanceHints(ProjectApplyGovernanceHintsArgs),

    /// Audit documentation and human-facing artifact governance surfaces
    AuditDocs(ProjectAuditDocsArgs),

    /// Build a read-only project/profile artifact index
    #[command(name = "artifact-index")]
    ArtifactIndex(ProjectArtifactIndexArgs),

    /// Build a read-only artifact retention review packet
    #[command(name = "retention-review")]
    RetentionReview(ProjectRetentionReviewArgs),

    /// Create an approval-only artifact retention follow-up request
    #[command(name = "retention-request")]
    RetentionRequest(ProjectRetentionRequestArgs),

    /// Consume an approved artifact retention decision into a profile receipt
    #[command(name = "retention-apply")]
    RetentionApply(ProjectRetentionApplyArgs),

    /// Promote a retained artifact into DELIVERABLES.md with snapshot evidence
    #[command(name = "retention-promote")]
    RetentionPromote(ProjectRetentionPromoteArgs),
}

#[derive(Args)]
pub struct ProjectInitArgs {
    /// Project repository/root directory to initialize for Forager operation
    path: PathBuf,

    /// Stable project key used by Ondesk, Offdesk, and adaptive wiki records
    #[arg(long)]
    project_key: String,

    /// Module path/id to mark as a prioritized operation target
    #[arg(long, value_name = "MODULE_PATH_OR_ID")]
    operation_target: Vec<String>,

    /// Write the initialization packet to this directory
    #[arg(long)]
    out: Option<PathBuf>,

    /// Include read-only git branch/status/diff-stat evidence
    #[arg(long)]
    include_git: bool,

    /// Overwrite known initialization files when --out already contains files
    #[arg(long)]
    force: bool,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ProjectApplyGovernanceHintsArgs {
    /// Project repository/root directory to update
    path: PathBuf,

    /// Stable project key to render into newly created surfaces
    #[arg(long)]
    project_key: String,

    /// Surface role to create. Repeat to limit scope; defaults to all missing surfaces
    #[arg(long, value_enum)]
    surface: Vec<ProjectGovernanceSurfaceRole>,

    /// Confirm that the operator reviewed the hints and approves creating missing files
    #[arg(long)]
    reviewed: bool,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Copy, Eq, PartialEq, Ord, PartialOrd, ValueEnum)]
pub enum ProjectGovernanceSurfaceRole {
    #[value(name = "current-state")]
    CurrentState,
    #[value(name = "next-actions")]
    NextActions,
    Decisions,
    Deliverables,
}

impl ProjectGovernanceSurfaceRole {
    fn role(self) -> &'static str {
        match self {
            ProjectGovernanceSurfaceRole::CurrentState => "current_state",
            ProjectGovernanceSurfaceRole::NextActions => "next_actions",
            ProjectGovernanceSurfaceRole::Decisions => "decisions",
            ProjectGovernanceSurfaceRole::Deliverables => "deliverables",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct ProjectInitOutput {
    kind: String,
    version: u32,
    id: String,
    profile: String,
    project_key: String,
    project_root: String,
    artifact_dir: String,
    read_only_project_state: bool,
    requires_operator_review: bool,
    artifacts: ProjectInitArtifacts,
    summary: ProjectInitSummary,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectInitArtifacts {
    operation_profile_json: String,
    onboarding_markdown: String,
    module_candidates_json: String,
    module_operation_preflight_json: String,
    evidence_collector_plan_markdown: String,
    governance_surface_hints_markdown: String,
    wiki_seed_candidates_json: String,
    ondesk_start_package_markdown: String,
    offdesk_ready_check_json: String,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectInitSummary {
    module_candidate_count: usize,
    entrypoint_count: usize,
    evidence_source_count: usize,
    warning_count: usize,
    operation_target_count: usize,
    module_operation_preflight_blocker_count: usize,
    governance_surface_missing_count: usize,
    ready_for_ondesk_start: bool,
    ready_for_offdesk_runtime: bool,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectApplyGovernanceHintsOutput {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    project_key: String,
    project_root: String,
    reviewed: bool,
    writes_target_project_state: bool,
    requires_operator_review: bool,
    planned_count: usize,
    created_count: usize,
    skipped_existing_count: usize,
    operations: Vec<ProjectGovernanceSurfaceOperation>,
    audit_command: String,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectGovernanceSurfaceOperation {
    role: String,
    path: String,
    status: String,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    content_preview: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectOperationProfile {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    id: String,
    project_key: String,
    project_root: String,
    read_only_project_state: bool,
    scope_model: ScopeModel,
    initialization_policy: InitializationPolicy,
    project_scan: ProjectScan,
    agent_modes: Vec<AgentModeContract>,
    ondesk_bridge: OndeskBridge,
    offdesk_policy: OffdeskProjectPolicy,
    safety_policy: ProjectSafetyPolicy,
    module_candidates_path: String,
    module_operation_preflight_path: String,
    evidence_plan_path: String,
    governance_surface_hints_path: String,
    wiki_seed_candidates_path: String,
    ondesk_start_package_path: String,
    offdesk_ready_check_path: String,
}

#[derive(Debug, Clone, Serialize)]
struct InitializationPolicy {
    grants_execution_authority: bool,
    grants_wiki_promotion_authority: bool,
    grants_file_cleanup_authority: bool,
    operator_review_required_before_offdesk_runtime: bool,
}

#[derive(Debug, Clone, Serialize)]
struct ScopeModel {
    project_target: ScopeTarget,
    operation_targets: Vec<ScopeTarget>,
    module_candidates: Vec<ScopeTarget>,
    artifact_scopes: Vec<ScopeTarget>,
    policy: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ScopeTarget {
    scope_kind: String,
    scope_ref: String,
    label: String,
    role: String,
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    parent_scope_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    parent_scope_ref: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    path: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectScan {
    root_markers: Vec<PathSignal>,
    documentation_sources: Vec<PathSignal>,
    entrypoints: Vec<EntryPointCandidate>,
    artifact_roots: Vec<PathSignal>,
    git: Option<GitSnapshot>,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct PathSignal {
    path: String,
    exists: bool,
    kind: String,
}

#[derive(Debug, Clone, Serialize)]
struct EntryPointCandidate {
    path: String,
    kind: String,
    command_hint: Option<String>,
}

#[derive(Debug, Clone)]
struct GovernanceSurfaceHint {
    role: String,
    recommended_path: String,
    alternatives: Vec<String>,
    existing_paths: Vec<String>,
    purpose: String,
}

#[derive(Debug, Clone, Serialize)]
struct GitSnapshot {
    is_repo: bool,
    branch: Option<String>,
    head: Option<String>,
    status_short: Option<String>,
    diff_stat: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
struct AgentModeContract {
    mode: String,
    purpose: String,
    first_reads: Vec<String>,
    output_contract: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct OndeskBridge {
    default_start_package: String,
    first_reads: Vec<String>,
    capture_policy: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct OffdeskProjectPolicy {
    runtime_requires_approval: bool,
    long_runs_prefer_local_tmux: bool,
    closeout_required: bool,
    default_runner_kind: String,
    required_artifacts: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ProjectSafetyPolicy {
    forbidden_without_separate_approval: Vec<String>,
    target_repo_default_mode: String,
}

#[derive(Debug, Clone, Serialize)]
struct ModuleCandidateReport {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    project_key: String,
    project_root: String,
    candidates: Vec<ModuleCandidate>,
}

#[derive(Debug, Clone, Serialize)]
struct ModuleCandidate {
    module_id: String,
    label: String,
    path: String,
    scope_kind: String,
    scope_ref: String,
    parent_project_key: String,
    selected_operation_target: bool,
    confidence: String,
    signals: Vec<String>,
    entrypoints: Vec<EntryPointCandidate>,
    documentation_sources: Vec<PathSignal>,
    operation_profile_status: String,
}

#[derive(Debug, Clone, Serialize)]
struct ModuleOperationPreflightReport {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    project_key: String,
    project_root: String,
    read_only_project_state: bool,
    ready_for_offdesk_runtime: bool,
    operation_targets: Vec<ModuleOperationPreflightTarget>,
    blockers: Vec<String>,
    recommended_next_steps: Vec<ModuleOperationPreflightCommand>,
}

#[derive(Debug, Clone, Serialize)]
struct ModuleOperationPreflightTarget {
    module_id: String,
    scope_ref: String,
    label: String,
    path: String,
    selected_operation_target: bool,
    readiness_level: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    recognized_profile_kind: Option<String>,
    module_contract_exists: bool,
    module_docs_exist: bool,
    module_entrypoint_count: usize,
    profile_builder_available: bool,
    evidence_bundle_builder_available: bool,
    evidence_review_builder_available: bool,
    blockers: Vec<String>,
    recommended_commands: Vec<ModuleOperationPreflightCommand>,
    required_operator_decisions: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ModuleOperationPreflightCommand {
    purpose: String,
    command: String,
    writes_target_project_state: bool,
    requires_runtime_approval: bool,
}

#[derive(Debug, Clone, Serialize)]
struct WikiSeedCandidateReport {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    project_key: String,
    activation_policy: String,
    candidates: Vec<WikiSeedCandidate>,
}

#[derive(Debug, Clone, Serialize)]
struct WikiSeedCandidate {
    title: String,
    scope: String,
    scope_ref: String,
    signal_kind: String,
    claim: String,
    human_summary: String,
    suggested_tags: Vec<String>,
    review_reason: String,
}

#[derive(Debug, Clone, Serialize)]
struct OffdeskReadyCheck {
    kind: String,
    version: u32,
    generated_at: DateTime<Utc>,
    project_key: String,
    project_root: String,
    ready_for_ondesk_start: bool,
    ready_for_offdesk_runtime: bool,
    requires_operator_review: bool,
    gates: Vec<ReadinessGate>,
    warnings: Vec<String>,
    blockers: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct ReadinessGate {
    gate: String,
    passed: bool,
    detail: String,
}

pub async fn run(profile: &str, command: ProjectCommands) -> Result<()> {
    match command {
        ProjectCommands::Init(args) => run_init(profile, args).await,
        ProjectCommands::ApplyGovernanceHints(args) => run_apply_governance_hints(args),
        ProjectCommands::AuditDocs(args) => run_audit_docs(args),
        ProjectCommands::ArtifactIndex(args) => artifact_index::run(profile, args).await,
        ProjectCommands::RetentionReview(args) => {
            artifact_index::run_retention_review(profile, args).await
        }
        ProjectCommands::RetentionRequest(args) => {
            artifact_index::run_retention_request(profile, args).await
        }
        ProjectCommands::RetentionApply(args) => {
            artifact_index::run_retention_apply(profile, args).await
        }
        ProjectCommands::RetentionPromote(args) => {
            artifact_index::run_retention_promote(profile, args).await
        }
    }
}

async fn run_init(profile: &str, args: ProjectInitArgs) -> Result<()> {
    let project_key = sanitize_required("project key", &args.project_key)?;
    let project_root = args
        .path
        .expanduser()
        .canonicalize()
        .with_context(|| format!("resolve project path {}", args.path.display()))?;
    if !project_root.is_dir() {
        bail!(
            "project path is not a directory: {}",
            project_root.display()
        );
    }

    let profile_name = if profile.is_empty() {
        "default"
    } else {
        profile
    };
    let generated_at = Utc::now();
    let id = format!("project-init-{}", short_uuid());
    let artifact_dir = resolve_artifact_dir(profile_name, &project_key, generated_at, &args)?;
    let artifacts = artifact_paths(&artifact_dir);

    let root_markers = collect_root_markers(&project_root);
    let documentation_sources = collect_documentation_sources(&project_root);
    let entrypoints = collect_entrypoints(&project_root, &project_root);
    let artifact_roots = collect_artifact_roots(&project_root);
    let mut module_candidates = discover_module_candidates(&project_key, &project_root);
    apply_operation_targets(&mut module_candidates, &args.operation_target)?;
    let operation_target_count = module_candidates
        .iter()
        .filter(|candidate| candidate.selected_operation_target)
        .count();
    let warnings = scan_warnings(&documentation_sources, &entrypoints);
    let git = if args.include_git {
        Some(git_snapshot(&project_root))
    } else {
        None
    };

    let project_scan = ProjectScan {
        root_markers,
        documentation_sources: documentation_sources.clone(),
        entrypoints: entrypoints.clone(),
        artifact_roots,
        git,
        warnings: warnings.clone(),
    };
    let module_report = ModuleCandidateReport {
        kind: "forager_module_candidate_report".to_string(),
        version: 1,
        generated_at,
        project_key: project_key.clone(),
        project_root: safe_path(&project_root),
        candidates: module_candidates,
    };
    let scope_model = scope_model(&project_key, &project_root, &module_report);
    let module_operation_preflight = module_operation_preflight(
        generated_at,
        &project_key,
        &project_root,
        &artifact_dir,
        &module_report,
    );
    let wiki_report = WikiSeedCandidateReport {
        kind: "forager_wiki_seed_candidates".to_string(),
        version: 1,
        generated_at,
        project_key: project_key.clone(),
        activation_policy: "candidate_only_until_operator_review".to_string(),
        candidates: wiki_seed_candidates(&project_key),
    };
    let ready_check = ready_check(
        generated_at,
        &project_key,
        &project_root,
        &project_scan,
        &module_report,
    );
    let governance_hints = governance_surface_hints(&project_root);
    let governance_surface_missing_count = governance_hints
        .iter()
        .filter(|hint| hint.existing_paths.is_empty())
        .count();
    let profile_record = ProjectOperationProfile {
        kind: "forager_project_operation_profile".to_string(),
        version: 1,
        generated_at,
        id: id.clone(),
        project_key: project_key.clone(),
        project_root: safe_path(&project_root),
        read_only_project_state: true,
        scope_model,
        initialization_policy: InitializationPolicy {
            grants_execution_authority: false,
            grants_wiki_promotion_authority: false,
            grants_file_cleanup_authority: false,
            operator_review_required_before_offdesk_runtime: true,
        },
        project_scan,
        agent_modes: agent_mode_contracts(),
        ondesk_bridge: OndeskBridge {
            default_start_package: artifacts.ondesk_start_package_markdown.clone(),
            first_reads: vec![
                PROFILE_FILE.to_string(),
                ONDESK_PACKAGE_FILE.to_string(),
                GOVERNANCE_HINTS_FILE.to_string(),
                EVIDENCE_PLAN_FILE.to_string(),
                MODULE_CANDIDATES_FILE.to_string(),
                MODULE_PREFLIGHT_FILE.to_string(),
            ],
            capture_policy: vec![
                "Capture live harness context as Ondesk notes or captures; do not rely on raw resume alone.".to_string(),
                "Treat initialization output as context, not proof of project state completion.".to_string(),
            ],
        },
        offdesk_policy: OffdeskProjectPolicy {
            runtime_requires_approval: true,
            long_runs_prefer_local_tmux: true,
            closeout_required: true,
            default_runner_kind: "local-tmux".to_string(),
            required_artifacts: vec![
                "manifest.json".to_string(),
                "progress.jsonl".to_string(),
                "heartbeat.json".to_string(),
                "result.json".to_string(),
                "REPORT.md".to_string(),
                "runner log".to_string(),
            ],
        },
        safety_policy: safety_policy(),
        module_candidates_path: artifacts.module_candidates_json.clone(),
        module_operation_preflight_path: artifacts.module_operation_preflight_json.clone(),
        evidence_plan_path: artifacts.evidence_collector_plan_markdown.clone(),
        governance_surface_hints_path: artifacts.governance_surface_hints_markdown.clone(),
        wiki_seed_candidates_path: artifacts.wiki_seed_candidates_json.clone(),
        ondesk_start_package_path: artifacts.ondesk_start_package_markdown.clone(),
        offdesk_ready_check_path: artifacts.offdesk_ready_check_json.clone(),
    };

    write_json(
        Path::new(&artifacts.operation_profile_json),
        &profile_record,
    )?;
    write_json(Path::new(&artifacts.module_candidates_json), &module_report)?;
    write_json(
        Path::new(&artifacts.module_operation_preflight_json),
        &module_operation_preflight,
    )?;
    write_json(
        Path::new(&artifacts.wiki_seed_candidates_json),
        &wiki_report,
    )?;
    write_json(Path::new(&artifacts.offdesk_ready_check_json), &ready_check)?;
    write_text(
        Path::new(&artifacts.onboarding_markdown),
        &render_onboarding(&profile_record, &module_report, &ready_check),
    )?;
    write_text(
        Path::new(&artifacts.evidence_collector_plan_markdown),
        &render_evidence_plan(&profile_record, &module_report),
    )?;
    write_text(
        Path::new(&artifacts.governance_surface_hints_markdown),
        &render_governance_surface_hints(
            generated_at,
            &project_key,
            &project_root,
            &governance_hints,
        ),
    )?;
    write_text(
        Path::new(&artifacts.ondesk_start_package_markdown),
        &render_ondesk_start_package(&profile_record, &module_report, &ready_check),
    )?;

    let output = ProjectInitOutput {
        kind: "forager_project_initialization".to_string(),
        version: 1,
        id,
        profile: profile_name.to_string(),
        project_key,
        project_root: safe_path(&project_root),
        artifact_dir: safe_path(&artifact_dir),
        read_only_project_state: true,
        requires_operator_review: true,
        artifacts,
        summary: ProjectInitSummary {
            module_candidate_count: module_report.candidates.len(),
            entrypoint_count: entrypoints.len(),
            evidence_source_count: documentation_sources
                .iter()
                .filter(|item| item.exists)
                .count(),
            warning_count: warnings.len(),
            operation_target_count,
            module_operation_preflight_blocker_count: module_operation_preflight.blockers.len(),
            governance_surface_missing_count,
            ready_for_ondesk_start: ready_check.ready_for_ondesk_start,
            ready_for_offdesk_runtime: ready_check.ready_for_offdesk_runtime,
        },
    };

    if args.json {
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        print_project_init_output(&output);
    }

    Ok(())
}

fn run_apply_governance_hints(args: ProjectApplyGovernanceHintsArgs) -> Result<()> {
    let project_key = sanitize_required("project key", &args.project_key)?;
    let project_root = args
        .path
        .expanduser()
        .canonicalize()
        .with_context(|| format!("resolve project path {}", args.path.display()))?;
    if !project_root.is_dir() {
        bail!(
            "project path is not a directory: {}",
            project_root.display()
        );
    }

    let generated_at = Utc::now();
    let selected_roles: BTreeSet<String> = args
        .surface
        .iter()
        .map(|role| role.role().to_string())
        .collect();
    let selected_all = selected_roles.is_empty();
    let hints = governance_surface_hints(&project_root);
    let mut operations = Vec::new();
    let mut planned_count = 0usize;
    let mut created_count = 0usize;
    let mut skipped_existing_count = 0usize;

    for hint in hints {
        if !selected_all && !selected_roles.contains(&hint.role) {
            continue;
        }

        if let Some(existing) = hint.existing_paths.first() {
            skipped_existing_count += 1;
            operations.push(ProjectGovernanceSurfaceOperation {
                role: hint.role,
                path: existing.clone(),
                status: "skipped_existing".to_string(),
                reason: "A governance surface for this role already exists; existing files are never overwritten.".to_string(),
                content_preview: None,
            });
            continue;
        }

        planned_count += 1;
        let content = governance_template(
            &hint.role,
            &hint.recommended_path,
            &project_key,
            generated_at,
        );
        let path = project_root.join(&hint.recommended_path);
        let status = if args.reviewed {
            write_new_text(&path, &content)?;
            created_count += 1;
            "created"
        } else {
            "planned_create"
        };
        operations.push(ProjectGovernanceSurfaceOperation {
            role: hint.role,
            path: hint.recommended_path,
            status: status.to_string(),
            reason: if args.reviewed {
                "Operator reviewed the hints and approved creating this missing surface.".to_string()
            } else {
                "Dry run only. Re-run with --reviewed after operator approval to create this missing surface.".to_string()
            },
            content_preview: Some(markdown_preview(&content, 12)),
        });
    }

    let audit_command = format!(
        "forager project audit-docs {} --audit-profile standard --json",
        shell_arg(&safe_path(&project_root))
    );
    let output = ProjectApplyGovernanceHintsOutput {
        kind: "forager_project_governance_hints_application".to_string(),
        version: 1,
        generated_at,
        project_key,
        project_root: safe_path(&project_root),
        reviewed: args.reviewed,
        writes_target_project_state: args.reviewed && created_count > 0,
        requires_operator_review: !args.reviewed,
        planned_count,
        created_count,
        skipped_existing_count,
        operations,
        audit_command,
    };

    if args.json {
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        print_project_apply_governance_hints_output(&output);
    }

    Ok(())
}

fn resolve_artifact_dir(
    profile: &str,
    project_key: &str,
    generated_at: DateTime<Utc>,
    args: &ProjectInitArgs,
) -> Result<PathBuf> {
    let path = if let Some(out) = &args.out {
        if out.is_absolute() {
            out.clone()
        } else {
            std::env::current_dir()?.join(out)
        }
    } else {
        let timestamp = generated_at.format("%Y%m%dT%H%M%SZ").to_string();
        get_profile_dir(profile)?
            .join("project_initializations")
            .join(format!("{}_{}", timestamp, slug(project_key)))
    };

    if path.exists() && !args.force && has_any_entry(&path)? {
        bail!(
            "output directory is not empty: {}\nUse --force to overwrite known initialization files.",
            path.display()
        );
    }
    fs::create_dir_all(&path).with_context(|| format!("create {}", path.display()))?;
    Ok(path.canonicalize().unwrap_or(path))
}

fn artifact_paths(artifact_dir: &Path) -> ProjectInitArtifacts {
    ProjectInitArtifacts {
        operation_profile_json: safe_path(&artifact_dir.join(PROFILE_FILE)),
        onboarding_markdown: safe_path(&artifact_dir.join(ONBOARDING_FILE)),
        module_candidates_json: safe_path(&artifact_dir.join(MODULE_CANDIDATES_FILE)),
        module_operation_preflight_json: safe_path(&artifact_dir.join(MODULE_PREFLIGHT_FILE)),
        evidence_collector_plan_markdown: safe_path(&artifact_dir.join(EVIDENCE_PLAN_FILE)),
        governance_surface_hints_markdown: safe_path(&artifact_dir.join(GOVERNANCE_HINTS_FILE)),
        wiki_seed_candidates_json: safe_path(&artifact_dir.join(WIKI_SEEDS_FILE)),
        ondesk_start_package_markdown: safe_path(&artifact_dir.join(ONDESK_PACKAGE_FILE)),
        offdesk_ready_check_json: safe_path(&artifact_dir.join(OFFDESK_READY_FILE)),
    }
}

fn collect_root_markers(root: &Path) -> Vec<PathSignal> {
    [
        ("AGENTS.md", "agent_instructions"),
        ("README.md", "readme"),
        ("Cargo.toml", "rust_manifest"),
        ("pyproject.toml", "python_manifest"),
        ("package.json", "node_manifest"),
        ("Makefile", "makefile"),
        ("justfile", "justfile"),
        (".forager/config.toml", "forager_repo_config"),
    ]
    .into_iter()
    .map(|(path, kind)| path_signal(root, path, kind))
    .collect()
}

fn collect_documentation_sources(root: &Path) -> Vec<PathSignal> {
    [
        ("AGENTS.md", "agent_instructions"),
        ("README.md", "readme"),
        ("CLAUDE.md", "harness_instructions"),
        ("docs/README.md", "docs_readme"),
        ("docs/index.md", "docs_index"),
        ("docs/operations/RunLog.md", "runlog"),
    ]
    .into_iter()
    .map(|(path, kind)| path_signal(root, path, kind))
    .collect()
}

fn collect_artifact_roots(root: &Path) -> Vec<PathSignal> {
    [
        ("target", "build_output"),
        ("runs", "run_artifacts"),
        ("outputs", "project_outputs"),
        ("data/metadata", "metadata_artifacts"),
        ("docs/operations", "operation_docs"),
    ]
    .into_iter()
    .map(|(path, kind)| path_signal(root, path, kind))
    .collect()
}

fn governance_surface_hints(root: &Path) -> Vec<GovernanceSurfaceHint> {
    vec![
        GovernanceSurfaceHint {
            role: "current_state".to_string(),
            recommended_path: "PROJECT_STATE.md".to_string(),
            alternatives: vec!["CURRENT_STATE.md".to_string()],
            existing_paths: existing_root_paths(root, &["PROJECT_STATE.md", "CURRENT_STATE.md"]),
            purpose: "Small current surface: what is true enough to act on now.".to_string(),
        },
        GovernanceSurfaceHint {
            role: "next_actions".to_string(),
            recommended_path: "NEXT_ACTIONS.md".to_string(),
            alternatives: Vec::new(),
            existing_paths: existing_root_paths(root, &["NEXT_ACTIONS.md"]),
            purpose: "Bounded work queue, blockers, and handoff-ready steps.".to_string(),
        },
        GovernanceSurfaceHint {
            role: "decisions".to_string(),
            recommended_path: "DECISIONS.md".to_string(),
            alternatives: Vec::new(),
            existing_paths: existing_root_paths(root, &["DECISIONS.md"]),
            purpose: "Durable choices with rationale, source, and operational effect.".to_string(),
        },
        GovernanceSurfaceHint {
            role: "deliverables".to_string(),
            recommended_path: "DELIVERABLES.md".to_string(),
            alternatives: Vec::new(),
            existing_paths: existing_root_paths(root, &["DELIVERABLES.md"]),
            purpose: "Human-facing outputs selected for inspection, handoff, or sharing."
                .to_string(),
        },
    ]
}

fn existing_root_paths(root: &Path, candidates: &[&str]) -> Vec<String> {
    candidates
        .iter()
        .filter(|path| root.join(path).exists())
        .map(|path| (*path).to_string())
        .collect()
}

fn path_signal(root: &Path, rel: &str, kind: &str) -> PathSignal {
    PathSignal {
        path: rel.to_string(),
        exists: root.join(rel).exists(),
        kind: kind.to_string(),
    }
}

fn collect_entrypoints(root: &Path, base: &Path) -> Vec<EntryPointCandidate> {
    let mut paths = BTreeSet::new();
    for rel in [
        "Cargo.toml",
        "pyproject.toml",
        "package.json",
        "Makefile",
        "justfile",
        "scripts/run.sh",
        "scripts/run_module_03.sh",
        "scripts/test.sh",
    ] {
        let path = base.join(rel);
        if path.exists() {
            paths.insert(path);
        }
    }

    let scripts_dir = base.join("scripts");
    if let Ok(entries) = fs::read_dir(scripts_dir) {
        for entry in entries.flatten().take(40) {
            let path = entry.path();
            if path.is_file()
                && path
                    .extension()
                    .and_then(|value| value.to_str())
                    .is_some_and(|ext| matches!(ext, "sh" | "py" | "js" | "ts"))
            {
                paths.insert(path);
            }
        }
    }

    paths
        .into_iter()
        .map(|path| entrypoint_candidate(root, &path))
        .collect()
}

fn entrypoint_candidate(root: &Path, path: &Path) -> EntryPointCandidate {
    let rel = rel_path(root, path);
    let file_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    let kind = match file_name {
        "Cargo.toml" => "rust_manifest",
        "pyproject.toml" => "python_manifest",
        "package.json" => "node_manifest",
        "Makefile" => "makefile",
        "justfile" => "justfile",
        _ if rel.contains("/scripts/") || rel.starts_with("scripts/") => "script",
        _ => "entrypoint",
    };
    let command_hint = match file_name {
        "Cargo.toml" => Some("cargo test".to_string()),
        "pyproject.toml" => Some("uv run pytest".to_string()),
        "package.json" => Some("npm test".to_string()),
        "Makefile" => Some("make test".to_string()),
        "justfile" => Some("just --list".to_string()),
        _ if file_name.ends_with(".sh") => Some(rel.clone()),
        _ => None,
    };
    EntryPointCandidate {
        path: rel,
        kind: kind.to_string(),
        command_hint,
    }
}

fn discover_module_candidates(project_key: &str, root: &Path) -> Vec<ModuleCandidate> {
    let mut candidates = Vec::new();
    for parent in ["modules", "apps", "packages", "crates"] {
        let parent_path = root.join(parent);
        let Ok(entries) = fs::read_dir(parent_path) else {
            continue;
        };
        for entry in entries.flatten().take(80) {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let rel = rel_path(root, &path);
            let module_id = module_id_for(parent, &rel);
            let label = module_label(&rel);
            let entrypoints = collect_entrypoints(root, &path);
            let docs = module_docs(root, &path);
            let mut signals = Vec::new();
            if !entrypoints.is_empty() {
                signals.push("entrypoints_detected".to_string());
            }
            if docs.iter().any(|doc| doc.exists) {
                signals.push("documentation_detected".to_string());
            }
            if path.join("contract.yaml").exists() {
                signals.push("contract_detected".to_string());
            }
            if path.join("tests").exists() {
                signals.push("tests_detected".to_string());
            }
            let confidence = match (entrypoints.is_empty(), signals.len()) {
                (false, count) if count >= 2 => "high",
                (false, _) => "medium",
                (true, count) if count >= 2 => "medium",
                _ => "low",
            };
            candidates.push(ModuleCandidate {
                module_id: module_id.clone(),
                label,
                path: rel,
                scope_kind: "module".to_string(),
                scope_ref: module_id,
                parent_project_key: project_key.to_string(),
                selected_operation_target: false,
                confidence: confidence.to_string(),
                signals,
                entrypoints,
                documentation_sources: docs,
                operation_profile_status: "candidate_requires_operator_review".to_string(),
            });
        }
    }
    candidates.sort_by(|left, right| left.path.cmp(&right.path));
    candidates
}

fn apply_operation_targets(
    candidates: &mut [ModuleCandidate],
    requested_targets: &[String],
) -> Result<()> {
    for requested in requested_targets {
        let requested = operator_safe_text(requested.trim());
        if requested.is_empty() {
            continue;
        }
        let mut matched = false;
        for candidate in candidates.iter_mut() {
            if module_target_matches(candidate, &requested) {
                candidate.selected_operation_target = true;
                candidate.operation_profile_status =
                    "operation_target_requires_module_profile_review".to_string();
                matched = true;
            }
        }
        if !matched {
            bail!(
                "operation target not found among module candidates: {}",
                requested
            );
        }
    }
    Ok(())
}

fn module_target_matches(candidate: &ModuleCandidate, requested: &str) -> bool {
    candidate.path == requested
        || candidate.module_id == requested
        || candidate.scope_ref == requested
        || candidate.label.eq_ignore_ascii_case(requested)
        || Path::new(&candidate.path)
            .file_name()
            .and_then(|value| value.to_str())
            == Some(requested)
}

fn scope_model(
    project_key: &str,
    project_root: &Path,
    module_report: &ModuleCandidateReport,
) -> ScopeModel {
    let module_candidates = module_report
        .candidates
        .iter()
        .map(module_scope_target)
        .collect::<Vec<_>>();
    let operation_targets = module_report
        .candidates
        .iter()
        .filter(|candidate| candidate.selected_operation_target)
        .map(module_scope_target)
        .collect::<Vec<_>>();
    ScopeModel {
        project_target: ScopeTarget {
            scope_kind: "project".to_string(),
            scope_ref: project_key.to_string(),
            label: project_key.to_string(),
            role: "project_context_target".to_string(),
            status: "operator_confirmed_project_key".to_string(),
            parent_scope_kind: None,
            parent_scope_ref: None,
            path: Some(safe_path(project_root)),
        },
        operation_targets,
        module_candidates,
        artifact_scopes: vec![
            ScopeTarget {
                scope_kind: "artifact_kind".to_string(),
                scope_ref: "evidence_bundle".to_string(),
                label: "Evidence Bundle".to_string(),
                role: "deterministic_evidence_scope".to_string(),
                status: "collector_plan_candidate".to_string(),
                parent_scope_kind: Some("project".to_string()),
                parent_scope_ref: Some(project_key.to_string()),
                path: None,
            },
            ScopeTarget {
                scope_kind: "artifact_kind".to_string(),
                scope_ref: "ondesk_return".to_string(),
                label: "Ondesk Return".to_string(),
                role: "handoff_scope".to_string(),
                status: "initialization_packet_candidate".to_string(),
                parent_scope_kind: Some("project".to_string()),
                parent_scope_ref: Some(project_key.to_string()),
                path: None,
            },
        ],
        policy: vec![
            "Project target owns overall objective, wiki scope, closeout, and Ondesk return context.".to_string(),
            "Module operation targets own canonical commands, module evidence gates, and module-specific reportability vocabulary.".to_string(),
            "Artifact scopes own concrete evidence bundles and should not replace project or module scope.".to_string(),
            "A module operation target remains a candidate until an operator reviews or creates its module operation profile.".to_string(),
        ],
    }
}

fn module_scope_target(candidate: &ModuleCandidate) -> ScopeTarget {
    ScopeTarget {
        scope_kind: "module".to_string(),
        scope_ref: candidate.scope_ref.clone(),
        label: candidate.label.clone(),
        role: if candidate.selected_operation_target {
            "module_operation_target".to_string()
        } else {
            "module_candidate".to_string()
        },
        status: candidate.operation_profile_status.clone(),
        parent_scope_kind: Some("project".to_string()),
        parent_scope_ref: Some(candidate.parent_project_key.clone()),
        path: Some(candidate.path.clone()),
    }
}

fn module_operation_preflight(
    generated_at: DateTime<Utc>,
    project_key: &str,
    project_root: &Path,
    artifact_dir: &Path,
    modules: &ModuleCandidateReport,
) -> ModuleOperationPreflightReport {
    let operation_targets = modules
        .candidates
        .iter()
        .filter(|candidate| candidate.selected_operation_target)
        .map(|candidate| {
            module_operation_preflight_target(project_key, project_root, artifact_dir, candidate)
        })
        .collect::<Vec<_>>();
    let mut blockers = operation_targets
        .iter()
        .flat_map(|target| target.blockers.iter().cloned())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    if operation_targets.is_empty() {
        blockers.push("no_operation_target_selected".to_string());
    }
    let recommended_next_steps = operation_targets
        .iter()
        .flat_map(|target| target.recommended_commands.iter().cloned())
        .collect::<Vec<_>>();

    ModuleOperationPreflightReport {
        kind: "forager_module_operation_preflight".to_string(),
        version: 1,
        generated_at,
        project_key: project_key.to_string(),
        project_root: safe_path(project_root),
        read_only_project_state: true,
        ready_for_offdesk_runtime: false,
        operation_targets,
        blockers,
        recommended_next_steps,
    }
}

fn module_operation_preflight_target(
    project_key: &str,
    project_root: &Path,
    artifact_dir: &Path,
    candidate: &ModuleCandidate,
) -> ModuleOperationPreflightTarget {
    let module_contract_exists = project_root
        .join(&candidate.path)
        .join("contract.yaml")
        .exists();
    let module_docs_exist = candidate
        .documentation_sources
        .iter()
        .any(|source| source.exists);
    let recognized_profile_kind = recognized_module_profile_kind(project_key, candidate);
    let mut blockers = vec![
        "operator_review_required_before_runtime_enqueue".to_string(),
        "module_operation_profile_requires_review".to_string(),
        "evidence_bundle_requires_review".to_string(),
    ];
    let mut required_operator_decisions = vec![
        "Confirm this module target is the active operation scope.".to_string(),
        "Review or create the module operation profile before Offdesk runtime.".to_string(),
        "Review deterministic evidence before reportability or completion claims.".to_string(),
    ];
    let recognized = recognized_profile_kind.is_some();
    if !recognized {
        blockers.push("no_known_module_profile_builder".to_string());
        blockers.push("no_known_evidence_bundle_builder".to_string());
        required_operator_decisions.push(
            "Author a project-specific module profile/evidence collector contract.".to_string(),
        );
    }

    ModuleOperationPreflightTarget {
        module_id: candidate.module_id.clone(),
        scope_ref: candidate.scope_ref.clone(),
        label: candidate.label.clone(),
        path: candidate.path.clone(),
        selected_operation_target: candidate.selected_operation_target,
        readiness_level: if recognized {
            "known_profile_builder_available".to_string()
        } else {
            "manual_profile_authoring_required".to_string()
        },
        recognized_profile_kind,
        module_contract_exists,
        module_docs_exist,
        module_entrypoint_count: candidate.entrypoints.len(),
        profile_builder_available: recognized,
        evidence_bundle_builder_available: recognized,
        evidence_review_builder_available: recognized,
        blockers,
        recommended_commands: module_operation_preflight_commands(
            project_root,
            artifact_dir,
            candidate,
            recognized,
        ),
        required_operator_decisions,
    }
}

fn recognized_module_profile_kind(
    project_key: &str,
    candidate: &ModuleCandidate,
) -> Option<String> {
    if project_key == "twinpaper"
        && (candidate.scope_ref == "module03_regspec_machine"
            || candidate.path == "modules/03_regspec_machine")
    {
        return Some("twinpaper_module03_regspec_machine".to_string());
    }
    None
}

fn module_operation_preflight_commands(
    project_root: &Path,
    artifact_dir: &Path,
    candidate: &ModuleCandidate,
    recognized: bool,
) -> Vec<ModuleOperationPreflightCommand> {
    if !recognized {
        return vec![ModuleOperationPreflightCommand {
            purpose: "author_module_operation_profile".to_string(),
            command: format!(
                "Create and review a module operation profile for {} before Offdesk runtime.",
                candidate.scope_ref
            ),
            writes_target_project_state: false,
            requires_runtime_approval: false,
        }];
    }

    let repo = shell_arg(&safe_path(project_root));
    let evidence_bundle = shell_arg(&safe_path(&artifact_dir.join("evidence_bundle.json")));
    let evidence_review = shell_arg(&safe_path(&artifact_dir.join("evidence_review.json")));
    let module_profile = shell_arg(&safe_path(
        &artifact_dir.join("module03_operation_profile.json"),
    ));
    vec![
        ModuleOperationPreflightCommand {
            purpose: "build_evidence_bundle".to_string(),
            command: format!(
                "scripts/build_twinpaper_evidence_bundle.py --repo {repo} --out {evidence_bundle}"
            ),
            writes_target_project_state: false,
            requires_runtime_approval: false,
        },
        ModuleOperationPreflightCommand {
            purpose: "review_evidence_bundle".to_string(),
            command: format!(
                "scripts/review_evidence_bundle.py --bundle {evidence_bundle} --out {evidence_review}"
            ),
            writes_target_project_state: false,
            requires_runtime_approval: false,
        },
        ModuleOperationPreflightCommand {
            purpose: "build_module_operation_profile".to_string(),
            command: format!(
                "scripts/build_twinpaper_module03_operation_profile.py --repo {repo} --evidence-bundle {evidence_bundle} --include-git --out {module_profile}"
            ),
            writes_target_project_state: false,
            requires_runtime_approval: false,
        },
        ModuleOperationPreflightCommand {
            purpose: "prepare_offdesk_task_after_review".to_string(),
            command: format!(
                "scripts/prepare_twinpaper_offdesk_task.py --repo {repo} --project-key twinpaper --role-gate-result latest --review-artifact latest"
            ),
            writes_target_project_state: false,
            requires_runtime_approval: true,
        },
    ]
}

fn module_docs(root: &Path, module_path: &Path) -> Vec<PathSignal> {
    ["README.md", "AGENTS.md", "contract.yaml", "docs/README.md"]
        .into_iter()
        .map(|rel| {
            let path = module_path.join(rel);
            PathSignal {
                path: rel_path(root, &path),
                exists: path.exists(),
                kind: match rel {
                    "contract.yaml" => "module_contract",
                    "AGENTS.md" => "agent_instructions",
                    _ => "module_docs",
                }
                .to_string(),
            }
        })
        .collect()
}

fn scan_warnings(docs: &[PathSignal], entrypoints: &[EntryPointCandidate]) -> Vec<String> {
    let mut warnings = Vec::new();
    if !docs.iter().any(|item| item.exists) {
        warnings.push("no_documentation_sources_detected".to_string());
    }
    if entrypoints.is_empty() {
        warnings.push("no_root_entrypoints_detected".to_string());
    }
    warnings
}

fn ready_check(
    generated_at: DateTime<Utc>,
    project_key: &str,
    project_root: &Path,
    scan: &ProjectScan,
    modules: &ModuleCandidateReport,
) -> OffdeskReadyCheck {
    let docs_present = scan.documentation_sources.iter().any(|item| item.exists);
    let entrypoints_present = !scan.entrypoints.is_empty();
    let module_candidates_present = !modules.candidates.is_empty();
    let operation_targets_present = modules
        .candidates
        .iter()
        .any(|candidate| candidate.selected_operation_target);
    let mut warnings = scan.warnings.clone();
    if !module_candidates_present {
        warnings.push("no_module_candidates_detected".to_string());
    }
    OffdeskReadyCheck {
        kind: "forager_offdesk_ready_check".to_string(),
        version: 1,
        generated_at,
        project_key: project_key.to_string(),
        project_root: safe_path(project_root),
        ready_for_ondesk_start: true,
        ready_for_offdesk_runtime: false,
        requires_operator_review: true,
        gates: vec![
            ReadinessGate {
                gate: "target_exists".to_string(),
                passed: project_root.exists(),
                detail: safe_path(project_root),
            },
            ReadinessGate {
                gate: "documentation_sources_detected".to_string(),
                passed: docs_present,
                detail: format!(
                    "{} documentation source(s) exist",
                    scan.documentation_sources
                        .iter()
                        .filter(|item| item.exists)
                        .count()
                ),
            },
            ReadinessGate {
                gate: "root_entrypoints_detected".to_string(),
                passed: entrypoints_present,
                detail: format!("{} root entrypoint(s) detected", scan.entrypoints.len()),
            },
            ReadinessGate {
                gate: "module_candidates_detected".to_string(),
                passed: module_candidates_present,
                detail: format!("{} module candidate(s) detected", modules.candidates.len()),
            },
            ReadinessGate {
                gate: "operation_target_scope_declared".to_string(),
                passed: operation_targets_present,
                detail: format!(
                    "{} operation target(s) selected",
                    modules
                        .candidates
                        .iter()
                        .filter(|candidate| candidate.selected_operation_target)
                        .count()
                ),
            },
            ReadinessGate {
                gate: "runtime_requires_approval".to_string(),
                passed: true,
                detail: "Offdesk runtime must still pass dispatch.runtime approval.".to_string(),
            },
            ReadinessGate {
                gate: "closeout_required".to_string(),
                passed: true,
                detail: "Offdesk work must finish with closeout and Ondesk return artifacts."
                    .to_string(),
            },
        ],
        warnings,
        blockers: readiness_blockers(operation_targets_present),
    }
}

fn readiness_blockers(operation_targets_present: bool) -> Vec<String> {
    let mut blockers = vec!["operator_review_required_before_runtime_enqueue".to_string()];
    if operation_targets_present {
        blockers.push("operation_targets_require_module_profile_review".to_string());
    } else {
        blockers.push("module_operation_profiles_are_candidates_until_reviewed".to_string());
    }
    blockers
}

fn agent_mode_contracts() -> Vec<AgentModeContract> {
    vec![
        AgentModeContract {
            mode: "planning".to_string(),
            purpose: "Build or revise execution plans without mutating target project state."
                .to_string(),
            first_reads: vec![PROFILE_FILE.to_string(), MODULE_CANDIDATES_FILE.to_string()],
            output_contract: vec![
                "state assumptions".to_string(),
                "candidate operations".to_string(),
                "approval and evidence requirements".to_string(),
            ],
        },
        AgentModeContract {
            mode: "development".to_string(),
            purpose:
                "Implement scoped code changes after the operation/evidence boundary is clear."
                    .to_string(),
            first_reads: vec![PROFILE_FILE.to_string(), EVIDENCE_PLAN_FILE.to_string()],
            output_contract: vec![
                "changed files".to_string(),
                "verification commands".to_string(),
                "remaining risk".to_string(),
            ],
        },
        AgentModeContract {
            mode: "analysis".to_string(),
            purpose: "Collect and compare evidence before making a claim.".to_string(),
            first_reads: vec![EVIDENCE_PLAN_FILE.to_string(), PROFILE_FILE.to_string()],
            output_contract: vec![
                "evidence used".to_string(),
                "uncertainties".to_string(),
                "next evidence needed".to_string(),
            ],
        },
        AgentModeContract {
            mode: "writing".to_string(),
            purpose: "Draft or revise human-facing text while preserving evidence status."
                .to_string(),
            first_reads: vec![
                ONDESK_PACKAGE_FILE.to_string(),
                EVIDENCE_PLAN_FILE.to_string(),
            ],
            output_contract: vec![
                "claim status".to_string(),
                "source/evidence references".to_string(),
                "open decisions".to_string(),
            ],
        },
        AgentModeContract {
            mode: "critique".to_string(),
            purpose: "Find weak assumptions, missing evidence, and overclaiming.".to_string(),
            first_reads: vec![PROFILE_FILE.to_string(), EVIDENCE_PLAN_FILE.to_string()],
            output_contract: vec![
                "findings first".to_string(),
                "severity".to_string(),
                "file or artifact references".to_string(),
            ],
        },
        AgentModeContract {
            mode: "maintenance".to_string(),
            purpose: "Review wiki, closeout, artifacts, and machine state without broad cleanup."
                .to_string(),
            first_reads: vec![OFFDESK_READY_FILE.to_string(), WIKI_SEEDS_FILE.to_string()],
            output_contract: vec![
                "proposed file operations".to_string(),
                "wiki candidates".to_string(),
                "operator approval requirements".to_string(),
            ],
        },
    ]
}

fn safety_policy() -> ProjectSafetyPolicy {
    ProjectSafetyPolicy {
        target_repo_default_mode: "read_only_until_operator_review".to_string(),
        forbidden_without_separate_approval: vec![
            "delete, move, archive, or clean project files".to_string(),
            "reboot, shutdown, service restart, storage, RAID, NVMe, mount, driver, firmware, or BIOS mutation".to_string(),
            "package installation, permission changes, or credential changes".to_string(),
            "wiki promotion beyond seed candidates".to_string(),
            "provider/model retargeting".to_string(),
        ],
    }
}

fn wiki_seed_candidates(project_key: &str) -> Vec<WikiSeedCandidate> {
    vec![
        WikiSeedCandidate {
            title: "Project objective must be operator-defined".to_string(),
            scope: "project".to_string(),
            scope_ref: project_key.to_string(),
            signal_kind: "initialization_policy".to_string(),
            claim: "Do not infer the project objective from file names alone.".to_string(),
            human_summary: "The operator should confirm the project objective before Offdesk runtime.".to_string(),
            suggested_tags: vec!["policy".to_string(), "project-objective".to_string()],
            review_reason: "Seed candidate only; requires operator confirmation.".to_string(),
        },
        WikiSeedCandidate {
            title: "Project and module scopes stay separate".to_string(),
            scope: "project".to_string(),
            scope_ref: project_key.to_string(),
            signal_kind: "scope_policy".to_string(),
            claim: "Use project scope for overall objective and handoff context; use module scope for canonical commands and module evidence gates.".to_string(),
            human_summary: "Do not collapse a module operation target into the project target unless the operator explicitly splits it into a separate project.".to_string(),
            suggested_tags: vec!["scope".to_string(), "module-operation".to_string()],
            review_reason: "Generic scope policy candidate for new project bootstrap.".to_string(),
        },
        WikiSeedCandidate {
            title: "Evidence before claims".to_string(),
            scope: "project".to_string(),
            scope_ref: project_key.to_string(),
            signal_kind: "evidence_policy".to_string(),
            claim: "Writing and critique modes should distinguish observed evidence from inferred conclusions.".to_string(),
            human_summary: "Keep claim status explicit until an evidence review marks it sufficient.".to_string(),
            suggested_tags: vec!["evidence".to_string(), "claim-governance".to_string()],
            review_reason: "Generic policy candidate for new project bootstrap.".to_string(),
        },
        WikiSeedCandidate {
            title: "Unsafe operations need separate approval".to_string(),
            scope: "project".to_string(),
            scope_ref: project_key.to_string(),
            signal_kind: "safety_policy".to_string(),
            claim: "Initialization does not authorize cleanup, deletion, service changes, or runtime execution.".to_string(),
            human_summary: "Treat destructive/system operations as separate approval-gated work.".to_string(),
            suggested_tags: vec!["safety".to_string(), "approval".to_string()],
            review_reason: "Generic policy candidate for new project bootstrap.".to_string(),
        },
    ]
}

fn render_onboarding(
    profile: &ProjectOperationProfile,
    modules: &ModuleCandidateReport,
    ready: &OffdeskReadyCheck,
) -> String {
    let mut output = String::new();
    output.push_str("# Project Operation Initialization\n\n");
    output.push_str(&format!("- project_key: `{}`\n", profile.project_key));
    output.push_str(&format!("- project_root: `{}`\n", profile.project_root));
    output.push_str("- read_only_project_state: `true`\n");
    output.push_str("- grants_execution_authority: `false`\n");
    output.push_str("- requires_operator_review: `true`\n\n");
    output.push_str("## First Reads\n\n");
    for item in &profile.ondesk_bridge.first_reads {
        output.push_str(&format!("- `{}`\n", item));
    }
    output.push_str("\n## Scope Model\n\n");
    output.push_str(&format!(
        "- project target: `{}` role=`{}`\n",
        profile.scope_model.project_target.scope_ref, profile.scope_model.project_target.role
    ));
    if profile.scope_model.operation_targets.is_empty() {
        output.push_str("- operation targets: none selected yet\n");
    } else {
        for target in &profile.scope_model.operation_targets {
            output.push_str(&format!(
                "- operation target: `{}` label=`{}` path=`{}` status=`{}`\n",
                target.scope_ref,
                target.label,
                target.path.as_deref().unwrap_or(""),
                target.status
            ));
        }
    }
    output.push_str("\n## Module Candidates\n\n");
    if modules.candidates.is_empty() {
        output.push_str("- No module candidates were detected by shallow scan.\n");
    } else {
        for candidate in &modules.candidates {
            output.push_str(&format!(
                "- `{}`: `{}` confidence=`{}` status=`{}`\n",
                candidate.module_id,
                candidate.path,
                candidate.confidence,
                candidate.operation_profile_status
            ));
        }
    }
    output.push_str("\n## Offdesk Readiness\n\n");
    output.push_str(&format!(
        "- ready_for_ondesk_start: `{}`\n",
        ready.ready_for_ondesk_start
    ));
    output.push_str(&format!(
        "- ready_for_offdesk_runtime: `{}`\n",
        ready.ready_for_offdesk_runtime
    ));
    output.push_str("\nOffdesk runtime remains blocked until an operator reviews this packet and selects a scoped operation.\n");
    output
}

fn render_evidence_plan(
    profile: &ProjectOperationProfile,
    modules: &ModuleCandidateReport,
) -> String {
    let mut output = String::new();
    output.push_str("# Evidence Collector Plan\n\n");
    output.push_str(
        "This is a read-only collector plan. It does not authorize runtime execution.\n\n",
    );
    output.push_str("## Source Candidates\n\n");
    for source in &profile.project_scan.documentation_sources {
        output.push_str(&format!(
            "- `{}` kind=`{}` exists=`{}`\n",
            source.path, source.kind, source.exists
        ));
    }
    output.push_str("\n## Artifact Roots\n\n");
    for source in &profile.project_scan.artifact_roots {
        output.push_str(&format!(
            "- `{}` kind=`{}` exists=`{}`\n",
            source.path, source.kind, source.exists
        ));
    }
    output.push_str("\n## Module-Specific Collector Work\n\n");
    if modules.candidates.is_empty() {
        output.push_str("- Add module evidence contracts after operator review.\n");
    } else {
        for candidate in &modules.candidates {
            output.push_str(&format!(
                "- `{}` scope=`module:{}` target=`{}`: define required docs, run summaries, reportability metrics, and stale-evidence rules.\n",
                candidate.path, candidate.scope_ref, candidate.selected_operation_target
            ));
        }
    }
    output.push_str("\n## Review Contract\n\n");
    output.push_str("- Collector writes evidence only.\n");
    output
        .push_str("- Reviewer decides sufficient, insufficient, conflicting, or needs_operator.\n");
    output.push_str("- Mode agents must not treat missing bundle evidence as proof of absence.\n");
    output
}

fn render_governance_surface_hints(
    generated_at: DateTime<Utc>,
    project_key: &str,
    project_root: &Path,
    hints: &[GovernanceSurfaceHint],
) -> String {
    let mut output = String::new();
    output.push_str("# Governance Surface Hints\n\n");
    output.push_str(
        "This packet is read-only guidance. It does not write templates into the target project.\n\n",
    );
    output.push_str(&format!("- project_key: `{}`\n", project_key));
    output.push_str(&format!("- project_root: `{}`\n", safe_path(project_root)));
    output.push_str(&format!("- generated_at: `{}`\n\n", generated_at));
    output.push_str("## Surface Status\n\n");
    output.push_str("| Role | Status | Recommended path | Existing path(s) | Purpose |\n");
    output.push_str("| --- | --- | --- | --- | --- |\n");
    for hint in hints {
        let status = if hint.existing_paths.is_empty() {
            "missing"
        } else {
            "present"
        };
        let existing = if hint.existing_paths.is_empty() {
            "-".to_string()
        } else {
            hint.existing_paths
                .iter()
                .map(|path| format!("`{path}`"))
                .collect::<Vec<_>>()
                .join(", ")
        };
        output.push_str(&format!(
            "| `{}` | `{}` | `{}` | {} | {} |\n",
            hint.role, status, hint.recommended_path, existing, hint.purpose
        ));
    }

    output.push_str("\n## Suggested First Step\n\n");
    output.push_str(
        "Create or refresh only the surfaces that are missing or stale. Keep these files compact and link out to raw logs, run folders, and generated outputs.\n\n",
    );
    output.push_str("After adding or updating surfaces, run:\n\n");
    output.push_str("```bash\n");
    output.push_str(&format!(
        "forager project audit-docs {} --audit-profile standard --json\n",
        shell_arg(&safe_path(project_root))
    ));
    output.push_str("```\n\n");

    output.push_str("## Template Sketches\n\n");
    for hint in hints {
        output.push_str(&format!("### `{}`\n\n", hint.recommended_path));
        if !hint.alternatives.is_empty() {
            output.push_str(&format!(
                "Alternative path(s): {}.\n\n",
                hint.alternatives
                    .iter()
                    .map(|path| format!("`{path}`"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        output.push_str("```markdown\n");
        output.push_str(&governance_template(
            &hint.role,
            &hint.recommended_path,
            project_key,
            generated_at,
        ));
        output.push_str("```\n\n");
    }
    output
}

fn governance_template(
    role: &str,
    recommended_path: &str,
    project_key: &str,
    generated_at: DateTime<Utc>,
) -> String {
    let updated = generated_at.format("%Y-%m-%d").to_string();
    match role {
        "current_state" => format!(
            "# Project State\n\nUpdated: {updated}\n\nThis is the small current surface for `{project_key}`.\n\n## Current Focus\n\n- ...\n\n## First Reads\n\n- `AGENTS.md`\n- `README.md`\n\n## Current Gaps\n\n- ...\n\n## Next Work Candidates\n\n1. ...\n\n## Refresh Rule\n\nRefresh this file when the active focus, accepted decision, or next safe action changes.\n"
        ),
        "next_actions" => format!(
            "# Next Actions\n\nUpdated: {updated}\n\n## Active Queue\n\n1. ...\n\n## Blockers\n\n- ...\n\n## Done Recently\n\n- ...\n"
        ),
        "decisions" => format!(
            "# Decisions\n\nUpdated: {updated}\n\n| Decision | Status | Source | Operational effect |\n| --- | --- | --- | --- |\n| Add first reviewed decision here. | candidate | source reference | operational effect |\n\n## Refresh Rule\n\nUpdate this file when a decision changes authority, workflow, safety, or delivery semantics.\n"
        ),
        "deliverables" => format!(
            "# Deliverables\n\nUpdated: {updated}\n\nThis is the compact inspection surface for `{project_key}` outputs.\n\n## Human-Facing Outputs\n\nAdd selected output paths here after review, with one sentence explaining why a human should inspect each.\n\n## Local Build Outputs\n\nList local validation artifacts only when they actually exist and are useful for inspection.\n\n## Promotion Rule\n\nAdd a path here when it is useful for inspection, handoff, release review, or external sharing.\n"
        ),
        _ => format!(
            "# {}\n\nUpdated: {updated}\n\nAdd compact, current content for `{project_key}`.\n",
            recommended_path.trim_end_matches(".md")
        ),
    }
}

fn render_ondesk_start_package(
    profile: &ProjectOperationProfile,
    modules: &ModuleCandidateReport,
    ready: &OffdeskReadyCheck,
) -> String {
    let mut output = String::new();
    output.push_str("# Ondesk Start Package\n\n");
    output.push_str(&format!("Project key: `{}`\n\n", profile.project_key));
    output.push_str("## Required First Reads\n\n");
    for item in &profile.ondesk_bridge.first_reads {
        output.push_str(&format!("- `{}`\n", item));
    }
    output.push_str("\n## Mode Boundary\n\n");
    for mode in &profile.agent_modes {
        output.push_str(&format!("- `{}`: {}\n", mode.mode, mode.purpose));
    }
    output.push_str("\n## Module Candidates\n\n");
    for candidate in &modules.candidates {
        output.push_str(&format!(
            "- `{}` at `{}` scope=`module:{}` confidence=`{}` target=`{}`\n",
            candidate.module_id,
            candidate.path,
            candidate.scope_ref,
            candidate.confidence,
            candidate.selected_operation_target
        ));
    }
    if modules.candidates.is_empty() {
        output.push_str("- No module candidates were detected.\n");
    }
    output.push_str("\n## Offdesk Boundary\n\n");
    output.push_str(&format!(
        "- ready_for_offdesk_runtime: `{}`\n",
        ready.ready_for_offdesk_runtime
    ));
    output.push_str(
        "- Runtime execution, wiki promotion, and file cleanup require separate review/approval.\n",
    );
    output
}

fn print_project_init_output(output: &ProjectInitOutput) {
    println!("Project initialization packet");
    println!("  id:          {}", output.id);
    println!("  profile:     {}", output.profile);
    println!("  project_key: {}", output.project_key);
    println!("  project:     {}", output.project_root);
    println!("  artifacts:   {}", output.artifact_dir);
    println!(
        "  modules:     {} candidate(s)",
        output.summary.module_candidate_count
    );
    println!(
        "  offdesk:     runtime_ready={} operator_review_required={}",
        output.summary.ready_for_offdesk_runtime, output.requires_operator_review
    );
    println!(
        "  governance:  {} missing suggested surface(s)",
        output.summary.governance_surface_missing_count
    );
    println!(
        "  start:       {}",
        output.artifacts.ondesk_start_package_markdown
    );
}

fn print_project_apply_governance_hints_output(output: &ProjectApplyGovernanceHintsOutput) {
    println!("Project governance surface application");
    println!("  project_key: {}", output.project_key);
    println!("  project:     {}", output.project_root);
    println!(
        "  mode:        {}",
        if output.reviewed {
            "reviewed-apply"
        } else {
            "dry-run"
        }
    );
    println!("  planned:     {}", output.planned_count);
    println!("  created:     {}", output.created_count);
    println!("  skipped:     {}", output.skipped_existing_count);
    for operation in &output.operations {
        println!(
            "  - {} {} ({})",
            operation.status, operation.path, operation.role
        );
    }
    println!("  audit:       {}", output.audit_command);
}

fn git_snapshot(root: &Path) -> GitSnapshot {
    let inside = git_output(root, &["rev-parse", "--is-inside-work-tree"]);
    let is_repo = inside.as_deref() == Some("true");
    if !is_repo {
        return GitSnapshot {
            is_repo: false,
            branch: None,
            head: None,
            status_short: None,
            diff_stat: None,
        };
    }
    GitSnapshot {
        is_repo: true,
        branch: git_output(root, &["branch", "--show-current"]),
        head: git_output(root, &["rev-parse", "HEAD"]),
        status_short: git_output(root, &["status", "--short"]),
        diff_stat: git_output(root, &["diff", "--stat"]),
    }
}

fn git_output(root: &Path, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(root)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    Some(operator_safe_text(&text))
}

fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value)?;
    write_text(path, &format!("{}\n", String::from_utf8(bytes)?))
}

fn write_text(path: &Path, content: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    fs::write(path, content).with_context(|| format!("write {}", path.display()))
}

fn write_new_text(path: &Path, content: &str) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .with_context(|| format!("create {}", path.display()))?;
    file.write_all(content.as_bytes())
        .with_context(|| format!("write {}", path.display()))
}

fn has_any_entry(path: &Path) -> Result<bool> {
    if !path.exists() {
        return Ok(false);
    }
    Ok(fs::read_dir(path)?.next().is_some())
}

fn sanitize_required(label: &str, value: &str) -> Result<String> {
    let safe = operator_safe_text(value.trim());
    if safe.is_empty() {
        bail!("{label} cannot be empty");
    }
    Ok(safe)
}

fn safe_path(path: &Path) -> String {
    operator_safe_text(path.to_string_lossy().as_ref())
}

fn markdown_preview(content: &str, max_lines: usize) -> String {
    let lines = content.lines().take(max_lines).collect::<Vec<_>>();
    let mut preview = lines.join("\n");
    if content.lines().count() > max_lines {
        preview.push_str("\n...");
    }
    preview
}

fn shell_arg(value: &str) -> String {
    if value
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '/' | '.' | '_' | '-' | ':'))
    {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
}

fn rel_path(root: &Path, path: &Path) -> String {
    let rel = path
        .strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    operator_safe_text(&rel)
}

fn module_id_for(parent: &str, rel: &str) -> String {
    let base = Path::new(rel)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(rel);
    let normalized = slug(base).replace(['-', '.'], "_");
    let parts = normalized.splitn(2, '_').collect::<Vec<_>>();
    if parent == "modules" && parts.first().is_some_and(|part| digits_only(part)) {
        if let Some(rest) = parts.get(1).filter(|part| !part.is_empty()) {
            return format!("module{}_{}", parts[0], rest);
        }
        return format!("module{}", parts[0]);
    }
    format!("{}_{}", singular_parent(parent), normalized)
}

fn singular_parent(parent: &str) -> &str {
    match parent {
        "apps" => "app",
        "packages" => "package",
        "crates" => "crate",
        "modules" => "module",
        value => value,
    }
}

fn digits_only(value: &str) -> bool {
    !value.is_empty() && value.chars().all(|ch| ch.is_ascii_digit())
}

fn module_label(rel: &str) -> String {
    let base = Path::new(rel)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or(rel);
    let cleaned = base
        .trim_start_matches(|ch: char| ch.is_ascii_digit())
        .trim_start_matches(['_', '-', '.'])
        .replace(['_', '-'], " ");
    let label = cleaned
        .split_whitespace()
        .map(title_word)
        .collect::<Vec<_>>()
        .join(" ");
    if label.is_empty() {
        rel.to_string()
    } else {
        label
    }
}

fn title_word(word: &str) -> String {
    match word.to_ascii_lowercase().as_str() {
        "regspec" => "RegSpec".to_string(),
        "api" => "API".to_string(),
        "ui" => "UI".to_string(),
        _ => {
            let mut chars = word.chars();
            match chars.next() {
                Some(first) => {
                    format!(
                        "{}{}",
                        first.to_ascii_uppercase(),
                        chars.as_str().to_ascii_lowercase()
                    )
                }
                None => String::new(),
            }
        }
    }
}

fn slug(value: &str) -> String {
    let mut out = String::new();
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() {
            out.push(ch.to_ascii_lowercase());
        } else if matches!(ch, '-' | '_' | '.') {
            out.push(ch);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let trimmed = out.trim_matches('-').to_string();
    if trimmed.is_empty() {
        "project".to_string()
    } else {
        trimmed
    }
}

fn short_uuid() -> String {
    Uuid::new_v4().to_string()[..8].to_string()
}

trait ExpandUser {
    fn expanduser(&self) -> PathBuf;
}

impl ExpandUser for PathBuf {
    fn expanduser(&self) -> PathBuf {
        let Some(raw) = self.to_str() else {
            return self.clone();
        };
        if raw == "~" {
            if let Some(home) = dirs::home_dir() {
                return home;
            }
        }
        if let Some(stripped) = raw.strip_prefix("~/") {
            if let Some(home) = dirs::home_dir() {
                return home.join(stripped);
            }
        }
        self.clone()
    }
}
