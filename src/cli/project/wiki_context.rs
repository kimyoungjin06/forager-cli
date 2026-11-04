//! Read-only discovery lint and reviewed marker onboarding for local-agent wiki context.

use std::fs::File;
use std::io::{ErrorKind, Read, Write};
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use chrono::{DateTime, Utc};
use clap::Args;
use serde::Serialize;
use sha2::{Digest, Sha256};
use similar::TextDiff;
use uuid::Uuid;

use crate::offdesk::wiki_context_file::wiki_context_root_identity_sha256;
use crate::process::FilesystemObjectSnapshot;

const REPORT_SCHEMA: &str = "wiki_context_onboarding.v1";
const AGENTS_FILE: &str = "AGENTS.md";
const CLAUDE_FILE: &str = "CLAUDE.md";
const GITIGNORE_FILE: &str = ".gitignore";
const BRIEF_FILE: &str = ".wiki-brief.md";
const MAX_INSTRUCTION_BYTES: usize = 64 * 1024;
const MAX_BRIEF_BYTES: usize = 16 * 1024;
const INSTRUCTION_MARKER_START: &str = "<!-- forager:wiki-context:start -->";
const INSTRUCTION_MARKER_END: &str = "<!-- forager:wiki-context:end -->";
const GITIGNORE_MARKER_START: &str = "# forager:wiki-context:start";
const GITIGNORE_MARKER_END: &str = "# forager:wiki-context:end";
const INSTRUCTION_SNIPPET: &str = "<!-- forager:wiki-context:start -->\n\
## Forager Wiki Context\n\
\n\
At the start of each local-agent session, read `.wiki-brief.md` if it exists. \
Treat it as advisory context. Verify stale or conflicting guidance against \
current project state, and record counterexamples instead of silently following \
or ignoring them.\n\
<!-- forager:wiki-context:end -->";
const GITIGNORE_SNIPPET: &str =
    "# forager:wiki-context:start\n/.wiki-brief.md\n# forager:wiki-context:end";

#[derive(Args)]
pub struct ProjectWikiContextArgs {
    /// Project repository/root directory to inspect
    path: PathBuf,

    /// Stable project key expected in the generated wiki brief
    #[arg(long)]
    project_key: String,

    /// Also preview or update an existing regular CLAUDE.md; create it if missing
    #[arg(long)]
    include_claude: bool,

    /// Preview or add the managed .wiki-brief.md rule to .gitignore
    #[arg(long)]
    manage_gitignore: bool,

    /// Confirm review of the exact diff and apply selected marker changes
    #[arg(long)]
    reviewed: bool,

    /// Output machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum BriefStatus {
    Present,
    Missing,
    Mismatched,
    Unsafe,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum RouteStatus {
    Routed,
    InheritedFromAgents,
    LinkedToAgents,
    NotRouted,
    Missing,
    Unsafe,
}

impl RouteStatus {
    fn is_routed(self) -> bool {
        matches!(self, Self::Routed | Self::InheritedFromAgents)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum DirectLaunchCoverage {
    Supported,
    Partial,
    Absent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum GitIgnoreStatus {
    Ignored,
    Tracked,
    MissingPolicy,
}

#[derive(Debug, Clone, Serialize)]
struct AgentRoutingStatus {
    agents_md: RouteStatus,
    claude_md: RouteStatus,
    direct_launch_coverage: DirectLaunchCoverage,
    projected_coverage_after_apply: DirectLaunchCoverage,
}

#[derive(Debug, Clone, Serialize)]
struct MarkerSnippets {
    instruction: &'static str,
    gitignore: &'static str,
}

#[derive(Debug, Clone, Serialize)]
struct OnboardingOperation {
    path: String,
    selected: bool,
    status: String,
    applied: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    before_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    after_sha256: Option<String>,
    diff: String,
    rollback: String,
    #[serde(skip)]
    before: ObservedPath,
    #[serde(skip)]
    after: Option<Vec<u8>>,
}

#[derive(Debug, Clone, Serialize)]
struct WikiContextOnboardingReport {
    schema: &'static str,
    generated_at: DateTime<Utc>,
    project_key: String,
    project_root_identity: String,
    reviewed: bool,
    read_only: bool,
    writes_project_state: bool,
    grants_session_input_authority: bool,
    grants_mcp_authority: bool,
    brief_status: BriefStatus,
    git_ignore_status: GitIgnoreStatus,
    routing: AgentRoutingStatus,
    snippets: MarkerSnippets,
    planned_count: usize,
    applied_count: usize,
    blocked_count: usize,
    operations: Vec<OnboardingOperation>,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum ObservedPath {
    Missing,
    Regular {
        snapshot: FilesystemObjectSnapshot,
        bytes: Vec<u8>,
    },
    Symlink {
        snapshot: FilesystemObjectSnapshot,
        target: PathBuf,
    },
    Unsafe {
        snapshot: Option<FilesystemObjectSnapshot>,
        reason: String,
    },
}

impl ObservedPath {
    pub(super) fn bytes(&self) -> Option<&[u8]> {
        match self {
            Self::Regular { bytes, .. } => Some(bytes),
            _ => None,
        }
    }

    fn sha256(&self) -> Option<String> {
        self.bytes().map(sha256_hex)
    }
}

pub(super) struct ProjectBoundary {
    root_path: PathBuf,
    root: File,
    root_identity: (u64, u64),
    root_identity_sha256: String,
}

impl ProjectBoundary {
    pub(super) fn open(path: &Path) -> Result<Self> {
        let root_path = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir()?.join(path)
        };
        let path_metadata = std::fs::symlink_metadata(&root_path)
            .with_context(|| format!("inspect project root {}", root_path.display()))?;
        ensure!(
            !path_metadata.file_type().is_symlink() && path_metadata.is_dir(),
            "project root must be one safe directory"
        );
        let root = crate::process::open_bounded_root(&root_path)
            .with_context(|| format!("open bounded project root {}", root_path.display()))?;
        let root_identity = crate::process::filesystem_file_identity(&root.metadata()?)
            .context("project root lacks a stable descriptor identity")?;
        ensure!(
            crate::process::filesystem_file_identity(&path_metadata) == Some(root_identity),
            "project root path and descriptor identities differ"
        );
        let boundary = Self {
            root_path,
            root,
            root_identity,
            root_identity_sha256: wiki_context_root_identity_sha256(root_identity),
        };
        boundary.ensure_active_root()?;
        Ok(boundary)
    }

    pub(super) fn inspect(&self, name: &str, max_bytes: usize) -> Result<ObservedPath> {
        ensure_single_name(Path::new(name))?;
        let path = self.root_path.join(name);
        let path_metadata = match std::fs::symlink_metadata(&path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == ErrorKind::NotFound => {
                self.ensure_active_root()?;
                ensure!(
                    matches!(
                        std::fs::symlink_metadata(&path),
                        Err(ref error) if error.kind() == ErrorKind::NotFound
                    ),
                    "{} appeared during wiki-context inspection",
                    name
                );
                return Ok(ObservedPath::Missing);
            }
            Err(error) => {
                return Err(error).with_context(|| format!("inspect project file {name}"));
            }
        };
        let path_snapshot = FilesystemObjectSnapshot::from_metadata(&path_metadata);
        if path_metadata.file_type().is_symlink() {
            let target = std::fs::read_link(&path)
                .with_context(|| format!("read project symlink {name}"))?;
            let after = std::fs::symlink_metadata(&path)
                .with_context(|| format!("reinspect project symlink {name}"))?;
            ensure!(
                FilesystemObjectSnapshot::from_metadata(&after) == path_snapshot,
                "{} symlink changed during wiki-context inspection",
                name
            );
            self.ensure_active_root()?;
            return Ok(ObservedPath::Symlink {
                snapshot: path_snapshot,
                target,
            });
        }
        if !path_metadata.is_file() {
            self.ensure_active_root()?;
            return Ok(ObservedPath::Unsafe {
                snapshot: Some(path_snapshot),
                reason: format!("{name} is not a regular file"),
            });
        }

        let mut file = match crate::process::open_bounded_regular_file(&self.root, Path::new(name))
        {
            Ok(file) => file,
            Err(error) => {
                self.ensure_active_root()?;
                return Ok(ObservedPath::Unsafe {
                    snapshot: Some(path_snapshot),
                    reason: format!("{name} cannot be opened without following links: {error}"),
                });
            }
        };
        if !has_single_link(&file)? {
            self.ensure_active_root()?;
            return Ok(ObservedPath::Unsafe {
                snapshot: Some(path_snapshot),
                reason: format!("{name} has more than one filesystem link"),
            });
        }
        let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        let mut bytes = Vec::new();
        Read::by_ref(&mut file)
            .take((max_bytes + 1) as u64)
            .read_to_end(&mut bytes)
            .with_context(|| format!("read bounded project file {name}"))?;
        if bytes.len() > max_bytes {
            self.ensure_active_root()?;
            return Ok(ObservedPath::Unsafe {
                snapshot: Some(before),
                reason: format!("{name} exceeds the {max_bytes}-byte inspection budget"),
            });
        }
        let after = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
        ensure!(
            before == after,
            "{} changed while wiki-context inspected it",
            name
        );
        let active = crate::process::open_bounded_regular_file(&self.root, Path::new(name))
            .with_context(|| format!("reopen active project file {name}"))?;
        ensure!(has_single_link(&active)?, "{name} acquired another link");
        ensure!(
            FilesystemObjectSnapshot::from_metadata(&active.metadata()?) == after,
            "{} was replaced while wiki-context inspected it",
            name
        );
        self.ensure_active_root()?;
        Ok(ObservedPath::Regular {
            snapshot: after,
            bytes,
        })
    }

    pub(super) fn write_exact(
        &self,
        name: &str,
        before: &ObservedPath,
        after: &[u8],
    ) -> Result<bool> {
        self.write_exact_with_hook(name, before, after, |_| Ok(()))
    }

    fn write_exact_with_hook<F>(
        &self,
        name: &str,
        before: &ObservedPath,
        after: &[u8],
        hook: F,
    ) -> Result<bool>
    where
        F: FnOnce(&Path) -> Result<()>,
    {
        ensure_single_name(Path::new(name))?;
        ensure!(
            after.len() <= MAX_INSTRUCTION_BYTES,
            "managed project file exceeds the write budget"
        );
        self.ensure_active_root()?;
        let current = self.inspect(name, MAX_INSTRUCTION_BYTES)?;
        if current.bytes() == Some(after) {
            return Ok(false);
        }
        ensure!(
            &current == before,
            "{name} changed after the reviewed diff was prepared"
        );

        let temporary_name = PathBuf::from(format!(".{name}.{}.tmp", Uuid::new_v4()));
        let result = (|| -> Result<bool> {
            let mut temporary =
                crate::process::create_new_bounded_regular_file(&self.root, &temporary_name)
                    .with_context(|| format!("create bounded temporary for {name}"))?;
            ensure!(has_single_link(&temporary)?, "temporary file is hardlinked");
            temporary
                .write_all(after)
                .with_context(|| format!("write bounded temporary for {name}"))?;
            temporary
                .sync_all()
                .with_context(|| format!("sync bounded temporary for {name}"))?;
            drop(temporary);

            hook(&temporary_name)?;
            self.ensure_active_root()?;
            let durable_temporary = self.inspect(
                temporary_name
                    .to_str()
                    .context("temporary name is not UTF-8")?,
                MAX_INSTRUCTION_BYTES,
            )?;
            ensure!(
                durable_temporary.bytes() == Some(after),
                "durable temporary bytes for {name} changed"
            );
            let current = self.inspect(name, MAX_INSTRUCTION_BYTES)?;
            if current.bytes() == Some(after) {
                return Ok(false);
            }
            ensure!(
                &current == before,
                "{name} changed before atomic replacement"
            );

            if matches!(before, ObservedPath::Missing) {
                crate::process::finalize_bounded_file_noreplace(
                    &self.root,
                    Path::new(""),
                    &temporary_name,
                    Path::new(name),
                )
                .with_context(|| format!("create reviewed project file {name}"))?;
            } else {
                crate::process::replace_bounded_root_file(
                    &self.root,
                    &temporary_name,
                    Path::new(name),
                )
                .with_context(|| format!("replace reviewed project file {name}"))?;
            }
            self.ensure_active_root()?;
            let committed = self.inspect(name, MAX_INSTRUCTION_BYTES)?;
            ensure!(
                committed.bytes() == Some(after),
                "committed project file {name} differs from the reviewed bytes"
            );
            Ok(true)
        })();
        if result.is_err() || matches!(&result, Ok(false)) {
            let _ = crate::process::remove_bounded_root_file(&self.root, &temporary_name);
        }
        result
    }

    fn ensure_active_root(&self) -> Result<()> {
        let path_metadata = std::fs::symlink_metadata(&self.root_path)
            .context("inspect active wiki-context project root")?;
        ensure!(
            !path_metadata.file_type().is_symlink() && path_metadata.is_dir(),
            "active wiki-context project root is unsafe"
        );
        let reopened = crate::process::open_bounded_root(&self.root_path)
            .context("reopen active wiki-context project root")?;
        ensure!(
            crate::process::filesystem_file_identity(&path_metadata) == Some(self.root_identity)
                && crate::process::filesystem_file_identity(&reopened.metadata()?)
                    == Some(self.root_identity)
                && crate::process::filesystem_file_identity(&self.root.metadata()?)
                    == Some(self.root_identity),
            "wiki-context project root was replaced"
        );
        Ok(())
    }
}

pub fn run_wiki_context(args: ProjectWikiContextArgs) -> Result<()> {
    let project_key = validate_project_key(&args.project_key)?;
    let boundary = ProjectBoundary::open(&args.path)?;
    let brief = boundary.inspect(BRIEF_FILE, MAX_BRIEF_BYTES)?;
    let agents = boundary.inspect(AGENTS_FILE, MAX_INSTRUCTION_BYTES)?;
    let claude = boundary.inspect(CLAUDE_FILE, MAX_INSTRUCTION_BYTES)?;
    let gitignore = boundary.inspect(GITIGNORE_FILE, MAX_INSTRUCTION_BYTES)?;
    let (mut git_ignore_status, mut git_warning) = inspect_git_ignore_status(&boundary.root_path);

    let brief_status = brief_status(&brief, &project_key);
    let agents_route = regular_route_status(&agents);
    let claude_route = claude_route_status(&claude, agents_route);
    let mut operations = Vec::new();
    operations.push(plan_instruction_operation(AGENTS_FILE, &agents, true));
    operations.push(plan_claude_operation(&claude, args.include_claude));
    operations.push(plan_gitignore_operation(
        &gitignore,
        args.manage_gitignore,
        git_ignore_status,
    ));
    let blocked_count = operations
        .iter()
        .filter(|operation| operation.selected && operation.status == "blocked")
        .count();
    if args.reviewed && blocked_count > 0 {
        bail!(
            "reviewed wiki-context apply is blocked by {} unsafe or malformed target(s)",
            blocked_count
        );
    }

    let mut applied_count = 0usize;
    if args.reviewed {
        for operation in &mut operations {
            let Some(after) = operation.after.as_deref() else {
                continue;
            };
            if !operation.selected {
                continue;
            }
            if boundary.write_exact(&operation.path, &operation.before, after)? {
                operation.applied = true;
                operation.status = "applied".to_string();
                applied_count += 1;
            } else {
                operation.status = "unchanged".to_string();
            }
        }
    }
    boundary.ensure_active_root()?;

    let projected_agents = projected_route(agents_route, &operations[0]);
    let projected_claude = if matches!(
        claude,
        ObservedPath::Symlink { ref target, .. } if target == Path::new(AGENTS_FILE)
    ) {
        if projected_agents.is_routed() {
            RouteStatus::InheritedFromAgents
        } else {
            RouteStatus::LinkedToAgents
        }
    } else {
        projected_route(claude_route, &operations[1])
    };
    let projected_coverage =
        direct_launch_coverage(brief_status, projected_agents, projected_claude);
    let (reported_agents, reported_claude) = if args.reviewed {
        let final_agents = boundary.inspect(AGENTS_FILE, MAX_INSTRUCTION_BYTES)?;
        let final_claude = boundary.inspect(CLAUDE_FILE, MAX_INSTRUCTION_BYTES)?;
        let final_agents_route = regular_route_status(&final_agents);
        let final_claude_route = claude_route_status(&final_claude, final_agents_route);
        (git_ignore_status, git_warning) = inspect_git_ignore_status(&boundary.root_path);
        (final_agents_route, final_claude_route)
    } else {
        (agents_route, claude_route)
    };
    let reported_coverage = direct_launch_coverage(brief_status, reported_agents, reported_claude);
    let planned_count = operations
        .iter()
        .filter(|operation| operation.after.is_some() && operation.selected)
        .count();
    let mut warnings = Vec::new();
    if let Some(warning) = git_warning {
        warnings.push(warning);
    }
    if git_ignore_status == GitIgnoreStatus::Tracked {
        warnings.push(
            ".wiki-brief.md is tracked; an ignore marker does not remove it from the Git index"
                .to_string(),
        );
    }
    if matches!(brief_status, BriefStatus::Missing | BriefStatus::Mismatched) {
        warnings.push(
            "direct launches remain incomplete until a current .wiki-brief.md is generated"
                .to_string(),
        );
    }
    if brief_status == BriefStatus::Unsafe {
        warnings.push(".wiki-brief.md is unsafe and was not followed".to_string());
    }
    if !args.include_claude && !reported_claude.is_routed() {
        warnings.push(
            "CLAUDE.md remains lint-only; rerun with --include-claude to preview its marker"
                .to_string(),
        );
    }

    let report = WikiContextOnboardingReport {
        schema: REPORT_SCHEMA,
        generated_at: Utc::now(),
        project_key,
        project_root_identity: boundary.root_identity_sha256.clone(),
        reviewed: args.reviewed,
        read_only: !args.reviewed,
        writes_project_state: applied_count > 0,
        grants_session_input_authority: false,
        grants_mcp_authority: false,
        brief_status,
        git_ignore_status,
        routing: AgentRoutingStatus {
            agents_md: reported_agents,
            claude_md: reported_claude,
            direct_launch_coverage: reported_coverage,
            projected_coverage_after_apply: if args.reviewed {
                reported_coverage
            } else {
                projected_coverage
            },
        },
        snippets: MarkerSnippets {
            instruction: INSTRUCTION_SNIPPET,
            gitignore: GITIGNORE_SNIPPET,
        },
        planned_count,
        applied_count,
        blocked_count,
        operations,
        warnings,
    };
    present_report(&report, args.json)
}

fn plan_instruction_operation(
    path: &str,
    observed: &ObservedPath,
    selected: bool,
) -> OnboardingOperation {
    plan_marker_operation(
        path,
        observed,
        selected,
        INSTRUCTION_MARKER_START,
        INSTRUCTION_MARKER_END,
        INSTRUCTION_SNIPPET,
        true,
    )
}

fn plan_claude_operation(observed: &ObservedPath, selected: bool) -> OnboardingOperation {
    if let ObservedPath::Symlink { target, .. } = observed {
        let links_to_agents = target == Path::new(AGENTS_FILE);
        return unchanged_operation(
            CLAUDE_FILE,
            observed,
            selected,
            if links_to_agents {
                "symlink_to_agents"
            } else {
                "blocked"
            },
            if links_to_agents {
                "Rollback: no CLAUDE.md bytes were changed; it remains a symlink to AGENTS.md."
            } else {
                "Rollback: no CLAUDE.md bytes were changed; review the existing symlink manually."
            },
        );
    }
    plan_instruction_operation(CLAUDE_FILE, observed, selected)
}

fn plan_gitignore_operation(
    observed: &ObservedPath,
    selected: bool,
    status: GitIgnoreStatus,
) -> OnboardingOperation {
    if status == GitIgnoreStatus::Ignored
        && !has_complete_marker(observed, GITIGNORE_MARKER_START, GITIGNORE_MARKER_END)
    {
        return unchanged_operation(
            GITIGNORE_FILE,
            observed,
            selected,
            "already_ignored",
            "Rollback: no .gitignore bytes were changed.",
        );
    }
    plan_marker_operation(
        GITIGNORE_FILE,
        observed,
        selected,
        GITIGNORE_MARKER_START,
        GITIGNORE_MARKER_END,
        GITIGNORE_SNIPPET,
        false,
    )
}

fn plan_marker_operation(
    path: &str,
    observed: &ObservedPath,
    selected: bool,
    start: &str,
    end: &str,
    snippet: &str,
    accept_existing_route: bool,
) -> OnboardingOperation {
    let Some(bytes) = observed.bytes() else {
        return match observed {
            ObservedPath::Missing => {
                planned_operation(path, observed, selected, snippet.as_bytes())
            }
            _ => unchanged_operation(
                path,
                observed,
                selected,
                "blocked",
                &format!("Rollback: no {path} bytes were changed."),
            ),
        };
    };
    let Ok(text) = std::str::from_utf8(bytes) else {
        return unchanged_operation(
            path,
            observed,
            selected,
            "blocked",
            &format!("Rollback: no {path} bytes were changed."),
        );
    };
    let starts = text.match_indices(start).collect::<Vec<_>>();
    let ends = text.match_indices(end).collect::<Vec<_>>();
    if starts.is_empty() && ends.is_empty() {
        if accept_existing_route && text.contains(BRIEF_FILE) {
            return unchanged_operation(
                path,
                observed,
                selected,
                "existing_route",
                &format!("Rollback: no {path} bytes were changed."),
            );
        }
        let after = append_block(text, snippet);
        return planned_operation(path, observed, selected, after.as_bytes());
    }
    if starts.len() != 1 || ends.len() != 1 || starts[0].0 >= ends[0].0 {
        return unchanged_operation(
            path,
            observed,
            selected,
            "blocked",
            &format!(
                "Rollback: no {path} bytes were changed because its managed marker is malformed."
            ),
        );
    }
    let end_offset = ends[0].0 + end.len();
    let mut after = String::with_capacity(text.len() + snippet.len());
    after.push_str(&text[..starts[0].0]);
    after.push_str(snippet);
    after.push_str(&text[end_offset..]);
    if after.as_bytes() == bytes {
        unchanged_operation(
            path,
            observed,
            selected,
            "unchanged",
            &rollback_instruction(path, observed),
        )
    } else {
        planned_operation(path, observed, selected, after.as_bytes())
    }
}

fn planned_operation(
    path: &str,
    before: &ObservedPath,
    selected: bool,
    after: &[u8],
) -> OnboardingOperation {
    let before_text = before
        .bytes()
        .and_then(|bytes| std::str::from_utf8(bytes).ok())
        .unwrap_or("");
    let after_text = std::str::from_utf8(after).unwrap_or("");
    OnboardingOperation {
        path: path.to_string(),
        selected,
        status: if matches!(before, ObservedPath::Missing) {
            "planned_create".to_string()
        } else {
            "planned_marker_update".to_string()
        },
        applied: false,
        before_sha256: before.sha256(),
        after_sha256: Some(sha256_hex(after)),
        diff: exact_diff(path, before_text, after_text),
        rollback: rollback_instruction(path, before),
        before: before.clone(),
        after: Some(after.to_vec()),
    }
}

fn unchanged_operation(
    path: &str,
    before: &ObservedPath,
    selected: bool,
    status: &str,
    rollback: &str,
) -> OnboardingOperation {
    OnboardingOperation {
        path: path.to_string(),
        selected,
        status: status.to_string(),
        applied: false,
        before_sha256: before.sha256(),
        after_sha256: before.sha256(),
        diff: String::new(),
        rollback: rollback.to_string(),
        before: before.clone(),
        after: None,
    }
}

fn projected_route(current: RouteStatus, operation: &OnboardingOperation) -> RouteStatus {
    if operation.selected
        && operation.after.as_deref().is_some_and(|bytes| {
            bytes
                .windows(BRIEF_FILE.len())
                .any(|window| window == BRIEF_FILE.as_bytes())
        })
    {
        RouteStatus::Routed
    } else {
        current
    }
}

fn regular_route_status(observed: &ObservedPath) -> RouteStatus {
    match observed {
        ObservedPath::Missing => RouteStatus::Missing,
        ObservedPath::Regular { bytes, .. } => match std::str::from_utf8(bytes) {
            Ok(text) if text.contains(BRIEF_FILE) => RouteStatus::Routed,
            Ok(_) => RouteStatus::NotRouted,
            Err(_) => RouteStatus::Unsafe,
        },
        ObservedPath::Symlink { .. } | ObservedPath::Unsafe { .. } => RouteStatus::Unsafe,
    }
}

fn claude_route_status(observed: &ObservedPath, agents_route: RouteStatus) -> RouteStatus {
    match observed {
        ObservedPath::Symlink { target, .. } if target == Path::new(AGENTS_FILE) => {
            if agents_route.is_routed() {
                RouteStatus::InheritedFromAgents
            } else {
                RouteStatus::LinkedToAgents
            }
        }
        _ => regular_route_status(observed),
    }
}

fn direct_launch_coverage(
    brief: BriefStatus,
    agents: RouteStatus,
    claude: RouteStatus,
) -> DirectLaunchCoverage {
    if brief == BriefStatus::Present && agents.is_routed() && claude.is_routed() {
        DirectLaunchCoverage::Supported
    } else if brief == BriefStatus::Present || agents.is_routed() || claude.is_routed() {
        DirectLaunchCoverage::Partial
    } else {
        DirectLaunchCoverage::Absent
    }
}

fn brief_status(observed: &ObservedPath, project_key: &str) -> BriefStatus {
    match observed {
        ObservedPath::Missing => BriefStatus::Missing,
        ObservedPath::Regular { bytes, .. } => match std::str::from_utf8(bytes) {
            Ok(text)
                if text.lines().any(|line| line == "Schema: wiki_brief.v1")
                    && text
                        .lines()
                        .any(|line| line == format!("Project: {project_key}"))
                    && text.lines().any(|line| {
                        line.strip_prefix("Projection: sha256:")
                            .is_some_and(is_sha256)
                    }) =>
            {
                BriefStatus::Present
            }
            Ok(_) => BriefStatus::Mismatched,
            Err(_) => BriefStatus::Unsafe,
        },
        ObservedPath::Symlink { .. } | ObservedPath::Unsafe { .. } => BriefStatus::Unsafe,
    }
}

fn inspect_git_ignore_status(root: &Path) -> (GitIgnoreStatus, Option<String>) {
    let repository = match git2::Repository::discover(root) {
        Ok(repository) => repository,
        Err(_) => {
            return (
                GitIgnoreStatus::MissingPolicy,
                Some("project root is not inside a Git worktree".to_string()),
            );
        }
    };
    let Some(workdir) = repository.workdir() else {
        return (
            GitIgnoreStatus::MissingPolicy,
            Some("bare Git repositories have no .wiki-brief.md ignore policy".to_string()),
        );
    };
    let relative_root = match root.strip_prefix(workdir) {
        Ok(relative) => relative,
        Err(_) => {
            return (
                GitIgnoreStatus::MissingPolicy,
                Some("project root could not be bound to the Git worktree".to_string()),
            );
        }
    };
    let relative = relative_root.join(BRIEF_FILE);
    if repository
        .index()
        .is_ok_and(|index| index.get_path(&relative, 0).is_some())
    {
        return (GitIgnoreStatus::Tracked, None);
    }
    match repository.status_should_ignore(&relative) {
        Ok(true) => (GitIgnoreStatus::Ignored, None),
        Ok(false) => (GitIgnoreStatus::MissingPolicy, None),
        Err(error) => (
            GitIgnoreStatus::MissingPolicy,
            Some(format!("Git ignore status could not be read: {error}")),
        ),
    }
}

fn has_complete_marker(observed: &ObservedPath, start: &str, end: &str) -> bool {
    observed
        .bytes()
        .and_then(|bytes| std::str::from_utf8(bytes).ok())
        .is_some_and(|text| text.matches(start).count() == 1 && text.matches(end).count() == 1)
}

fn append_block(text: &str, snippet: &str) -> String {
    let mut result = text.to_string();
    if !result.is_empty() && !result.ends_with('\n') {
        result.push('\n');
    }
    if !result.is_empty() && !result.ends_with("\n\n") {
        result.push('\n');
    }
    result.push_str(snippet);
    result.push('\n');
    result
}

fn exact_diff(path: &str, before: &str, after: &str) -> String {
    TextDiff::from_lines(before, after)
        .unified_diff()
        .header(&format!("a/{path}"), &format!("b/{path}"))
        .to_string()
}

fn rollback_instruction(path: &str, before: &ObservedPath) -> String {
    match before.sha256() {
        Some(hash) => format!(
            "Rollback: remove only the managed marker block from {path}, or restore the reviewed before SHA-256 {hash}."
        ),
        None => format!(
            "Rollback: remove {path} only if it still contains exactly the generated managed marker block."
        ),
    }
}

fn validate_project_key(value: &str) -> Result<String> {
    let trimmed = value.trim();
    ensure!(!trimmed.is_empty(), "project key cannot be empty");
    ensure!(
        trimmed.chars().count() <= 256
            && !trimmed.chars().any(char::is_control)
            && !trimmed.contains(['/', '\\']),
        "project key is unsafe"
    );
    Ok(trimmed.to_string())
}

fn ensure_single_name(path: &Path) -> Result<()> {
    ensure!(
        !path.is_absolute()
            && path.components().count() == 1
            && matches!(path.components().next(), Some(Component::Normal(_))),
        "managed project path must be one normal component"
    );
    Ok(())
}

fn has_single_link(file: &File) -> Result<bool> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(file.metadata()?.nlink() == 1)
    }
    #[cfg(not(unix))]
    {
        let _ = file;
        Ok(true)
    }
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

fn present_report(report: &WikiContextOnboardingReport, json: bool) -> Result<()> {
    if json {
        println!("{}", serde_json::to_string_pretty(report)?);
        return Ok(());
    }
    println!(
        "Wiki context onboarding: {:?} (brief: {:?}, ignore: {:?})",
        report.routing.direct_launch_coverage, report.brief_status, report.git_ignore_status
    );
    println!(
        "planned: {}, applied: {}, blocked: {}",
        report.planned_count, report.applied_count, report.blocked_count
    );
    for operation in &report.operations {
        println!("\n{}: {}", operation.path, operation.status);
        if !operation.diff.is_empty() {
            println!("{}", operation.diff);
        }
        println!("{}", operation.rollback);
    }
    for warning in &report.warnings {
        println!("warning: {warning}");
    }
    if !report.reviewed && report.planned_count > 0 {
        println!("\nDry run. Review the exact diff, then rerun with --reviewed to apply it.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn marker_update_preserves_every_byte_outside_the_managed_block() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().join("project");
        std::fs::create_dir(&root)?;
        let original = format!(
            "prefix without normalization\n{INSTRUCTION_MARKER_START}\nold managed text\n{INSTRUCTION_MARKER_END}\nsuffix without normalization\n"
        );
        std::fs::write(root.join(AGENTS_FILE), &original)?;
        let boundary = ProjectBoundary::open(&root)?;
        let observed = boundary.inspect(AGENTS_FILE, MAX_INSTRUCTION_BYTES)?;
        let operation = plan_instruction_operation(AGENTS_FILE, &observed, true);
        let after = std::str::from_utf8(operation.after.as_deref().context("planned bytes")?)?;
        assert!(after.starts_with("prefix without normalization\n"));
        assert!(after.ends_with("\nsuffix without normalization\n"));
        assert!(after.contains(INSTRUCTION_SNIPPET));
        assert!(!after.contains("old managed text"));
        Ok(())
    }

    #[test]
    fn reviewed_writer_rejects_root_replacement_before_commit() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().join("project");
        std::fs::create_dir(&root)?;
        std::fs::write(root.join(AGENTS_FILE), "original\n")?;
        let boundary = ProjectBoundary::open(&root)?;
        let before = boundary.inspect(AGENTS_FILE, MAX_INSTRUCTION_BYTES)?;
        let displaced = temp.path().join("displaced");
        let error = boundary
            .write_exact_with_hook(AGENTS_FILE, &before, b"changed\n", |_| {
                std::fs::rename(&root, &displaced)?;
                std::fs::create_dir(&root)?;
                Ok(())
            })
            .expect_err("root replacement must fail");
        assert!(error.to_string().contains("root"));
        assert!(!root.join(AGENTS_FILE).exists());
        assert_eq!(std::fs::read(displaced.join(AGENTS_FILE))?, b"original\n");
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn instruction_inspection_rejects_hardlinks() -> Result<()> {
        let temp = tempdir()?;
        let root = temp.path().join("project");
        std::fs::create_dir(&root)?;
        let outside = temp.path().join("outside");
        std::fs::write(&outside, "outside\n")?;
        std::fs::hard_link(&outside, root.join(AGENTS_FILE))?;
        let boundary = ProjectBoundary::open(&root)?;
        assert!(matches!(
            boundary.inspect(AGENTS_FILE, MAX_INSTRUCTION_BYTES)?,
            ObservedPath::Unsafe { .. }
        ));
        assert_eq!(std::fs::read_to_string(outside)?, "outside\n");
        Ok(())
    }
}
