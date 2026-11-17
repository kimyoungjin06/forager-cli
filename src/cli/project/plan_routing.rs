//! Bounded owner discovery and exact-preview, single-document routing changes.

use std::collections::BTreeSet;
use std::fs;
use std::io::Read;
use std::path::{Component, Path, PathBuf};

use anyhow::{bail, ensure, Context, Result};
use clap::Args;
use serde::Serialize;
use sha2::{Digest, Sha256};
use similar::TextDiff;

use super::wiki_context::{ObservedPath, ProjectBoundary};
use crate::process::{self, FilesystemObjectSnapshot};

const MAX_DOCUMENT: usize = 1024 * 1024;
const MAX_INSTRUCTIONS: usize = 64 * 1024;
const START: &str = "<!-- forager:plan-routing:start -->";
const END: &str = "<!-- forager:plan-routing:end -->";

#[derive(Args)]
pub struct ProjectPlanRoutingArgs {
    /// Explicit project root; only bounded documentation discovery is performed
    path: PathBuf,
    #[arg(long)]
    project_key: String,
    /// Explicit existing owning TODO, relative to the project root
    #[arg(long)]
    owning_plan: Option<String>,
    /// Select one previously declared logical scope instead of the whole project
    #[arg(long)]
    subproject_spec: Option<PathBuf>,
    /// Existing document location: working_notes, evidence, reports, or canonical=PATH
    #[arg(long = "document-route", value_name = "ROLE=PATH")]
    document_routes: Vec<String>,
    /// Preview establishment of an explicitly named missing plan (one file only)
    #[arg(long, requires = "owning_plan", conflicts_with = "subproject_spec")]
    create_plan: bool,
    /// Apply the exact reviewed preview; planning never authorizes Offdesk work
    #[arg(long, requires = "expected_preview_sha256")]
    reviewed: bool,
    #[arg(long, requires = "reviewed")]
    expected_preview_sha256: Option<String>,
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(super) struct Candidate {
    path: String,
    status: String,
    sha256: Option<String>,
    referenced_by: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct Link {
    source: String,
    target: String,
    status: String,
}

#[derive(Debug, Clone, Serialize)]
struct DocumentRoute {
    role: String,
    path: String,
    status: String,
}

impl RoutingPreview {
    pub(super) fn existing_candidate_paths(&self) -> Vec<String> {
        self.candidates
            .iter()
            .filter(|candidate| candidate.status == "present")
            .map(|candidate| candidate.path.clone())
            .collect()
    }

    pub(super) fn has_unresolved_candidates(&self) -> bool {
        !self.candidates.is_empty()
            || self
                .blockers
                .iter()
                .any(|blocker| blocker.contains("truncated"))
    }

    pub(super) fn selected_existing_owner(&self) -> Option<String> {
        (self.owner_status == "explicit_existing_owner")
            .then(|| self.selected_owning_plan.clone())
            .flatten()
    }
}

#[derive(Debug, Clone, Serialize)]
pub(super) struct RoutingPreview {
    schema: &'static str,
    project_key: String,
    project_root: String,
    root_identity: String,
    scope_anchor: String,
    subproject_spec_sha256: Option<String>,
    selected_owning_plan: Option<String>,
    owner_status: String,
    candidates: Vec<Candidate>,
    first_read_links: Vec<Link>,
    document_routes: Vec<DocumentRoute>,
    inputs: Vec<(String, String)>,
    claude_route: String,
    blockers: Vec<String>,
    action: String,
    target_path: Option<String>,
    before_sha256: Option<String>,
    after_sha256: Option<String>,
    content_preview: Option<String>,
    diff: String,
    read_only: bool,
    grants_runtime_authority: bool,
    #[serde(skip)]
    before: Option<ObservedPath>,
    #[serde(skip)]
    after: Option<Vec<u8>>,
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn relative(value: &str) -> Result<PathBuf> {
    let path = Path::new(value);
    ensure!(
        !value.is_empty()
            && !path.is_absolute()
            && path
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
        "document path must be a normalized project-relative path: {value}"
    );
    ensure!(
        !value
            .chars()
            .any(|c| c.is_control() || "`[]()<>\\".contains(c)),
        "document path contains unsupported routing characters"
    );
    Ok(path.to_path_buf())
}

fn read(root: &Path, name: &str, limit: usize) -> Result<Vec<u8>> {
    let path = relative(name)?;
    let descriptor = process::open_bounded_root(root)?;
    let mut file = process::open_bounded_regular_file(&descriptor, &path)?;
    let before = FilesystemObjectSnapshot::from_metadata(&file.metadata()?);
    ensure!(
        process::filesystem_link_count(&file.metadata()?) == Some(1),
        "document is hardlinked"
    );
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    ensure!(bytes.len() <= limit, "document exceeds inspection budget");
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&file.metadata()?),
        "document changed during read"
    );
    let reopened = process::open_bounded_regular_file(&descriptor, &path)?;
    ensure!(
        before == FilesystemObjectSnapshot::from_metadata(&reopened.metadata()?),
        "document replaced during read"
    );
    Ok(bytes)
}

fn plan_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    lower.ends_with(".md")
        && (lower.contains("todo")
            || lower.contains("backlog")
            || lower == "next_actions.md"
            || lower == "plan.md"
            || lower == "work_plan.md")
}

fn local_links(text: &str) -> Vec<String> {
    // Only first-read local Markdown/backtick paths, never URLs or document bodies recursively.
    let mut paths = BTreeSet::new();
    for line in text.lines().take(128) {
        let mut tail = line;
        while let Some((_, after)) = tail.split_once("](") {
            let Some((path, rest)) = after.split_once(')') else {
                break;
            };
            let path = path
                .strip_prefix('<')
                .and_then(|path| path.strip_suffix('>'))
                .unwrap_or(path);
            let path = path.split('#').next().unwrap_or_default();
            if !path.is_empty() && !path.contains(':') && !path.starts_with('#') {
                paths.insert(path.to_string());
            }
            tail = rest;
        }
        for token in line.split('`').skip(1).step_by(2) {
            if token.ends_with(".md") && !token.contains(':') && !token.contains(' ') {
                paths.insert(token.to_string());
            }
        }
    }
    paths.into_iter().collect()
}

fn normalize_link(source: &str, target: &str) -> Option<String> {
    let mut parts = Vec::new();
    let combined = Path::new(source).parent()?.join(target);
    for part in combined.components() {
        match part {
            Component::Normal(name) => parts.push(name.to_str()?.to_string()),
            Component::CurDir => (),
            Component::ParentDir => {
                parts.pop()?;
            }
            _ => return None,
        }
    }
    Some(parts.join("/"))
}

fn markdown_link(label: &str, target: &str) -> String {
    if target.contains(' ') {
        format!("[{label}](<{target}>)")
    } else {
        format!("[{label}]({target})")
    }
}

fn snippet(owner: &str, routes: &[DocumentRoute], anchor: &str) -> String {
    let prefix = if anchor == "." {
        String::new()
    } else {
        "../".repeat(Path::new(anchor).components().count())
    };
    let owner_link = markdown_link(owner, &format!("{prefix}{owner}"));
    let mut text = format!("{START}\n## Forager Ondesk and TODO Routing\n\nOwning plan for this scope: {owner_link}.\n\nDefault to Ondesk. Read the owning plan and current project state before editing.\nUpdate this existing plan instead of creating a parallel TODO. Resolve ambiguous\nownership before writing. Use outcome-oriented work packages, normally 30 to\n120 minutes including verification; do not invent work to fill a time window.\nPlanning and these instructions grant no Offdesk runtime or session authority.\n\nReview returned evidence, changes, limits and remaining work with the user.\nRecord agreed accept, partial accept, rework or defer dispositions in a proposal.\nUse `ondesk plan-adopt-proposal` for selected plain TODO tasks and `ondesk plan-apply`\nfor authorized, baseline-checked updates. Results stay provisional until agreement.\nPreserve authored documents and evidence; no automatic migration or cleanup.\n\n");
    if routes.is_empty() {
        text.push_str("Document locations are undeclared. Resolve existing working notes, evidence,\nreports and canonical documents before writing to them; do not create duplicate stores.\n");
    } else {
        for route in routes {
            text.push_str(&format!(
                "- {}: {}\n",
                route.role,
                markdown_link(&route.path, &format!("{prefix}{}", route.path))
            ));
        }
        text.push_str(
            "Undeclared document roles require an explicit ownership decision before writing.\n",
        );
    }
    text.push_str(&format!(
        "\nThese are operating instructions; runtime enforcement is separate.\n{END}\n"
    ));
    text
}

pub(super) fn inspect(
    root: &Path,
    key: &str,
    owner: Option<&str>,
    anchor: &str,
    document_routes: &[String],
    create_plan: bool,
) -> Result<RoutingPreview> {
    let root_descriptor = process::open_bounded_root(root)?;
    let root_identity = format!(
        "{:?}",
        process::filesystem_file_identity(&root_descriptor.metadata()?)
    );
    let scope = if anchor == "." {
        root.to_path_buf()
    } else {
        root.join(relative(anchor)?)
    };
    let boundary = ProjectBoundary::open(&scope)?;
    let agents = boundary.inspect("AGENTS.md", MAX_INSTRUCTIONS)?;
    let claude = boundary.inspect("CLAUDE.md", MAX_INSTRUCTIONS)?;
    let mut report = RoutingPreview {
        schema: "forager_plan_routing.v1",
        project_key: key.to_string(),
        project_root: root
            .to_str()
            .context("project path is not UTF-8")?
            .to_string(),
        root_identity,
        scope_anchor: anchor.to_string(),
        subproject_spec_sha256: None,
        selected_owning_plan: owner.map(str::to_string),
        owner_status: "missing".into(),
        candidates: Vec::new(),
        first_read_links: Vec::new(),
        document_routes: Vec::new(),
        inputs: Vec::new(),
        claude_route: match &claude {
            ObservedPath::Missing => "missing: direct Claude entrypoint not established".into(),
            ObservedPath::Symlink { target, .. } if target == Path::new("AGENTS.md") => {
                "symlink_to_agents_preserved".into()
            }
            ObservedPath::Regular { .. } => {
                "authored_regular_file_preserved: Claude routing requires separate review".into()
            }
            _ => "unresolved_claude_entrypoint_preserved".into(),
        },
        blockers: Vec::new(),
        action: "held".into(),
        target_path: None,
        before_sha256: None,
        after_sha256: None,
        content_preview: None,
        diff: String::new(),
        read_only: true,
        grants_runtime_authority: false,
        before: None,
        after: None,
    };
    // Keep input receipts bounded and avoid copying authored instruction bodies into JSON.
    report.inputs = [(&agents, "AGENTS.md"), (&claude, "CLAUDE.md")]
        .into_iter()
        .map(|(observed, name)| (name.into(), observation_receipt(observed)))
        .collect();
    report.inputs.push((
        "scope_directory".into(),
        format!(
            "{:?}",
            process::filesystem_file_identity(&fs::metadata(&scope)?)
        ),
    ));
    let mut names = BTreeSet::new();
    let mut directories = vec![scope.clone()];
    let docs = scope.join("docs");
    if fs::symlink_metadata(&docs).is_ok_and(|m| m.is_dir() && !m.is_symlink()) {
        directories.push(docs);
    }
    let mut count = 0;
    while let Some(directory) = directories.pop() {
        let mut entries = fs::read_dir(&directory)?
            .take(257)
            .collect::<std::io::Result<Vec<_>>>()?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            count += 1;
            if count > 256 {
                report
                    .blockers
                    .push("bounded owner scan truncated; select a narrower scope".into());
                break;
            }
            let path = entry.path();
            let name = path
                .strip_prefix(root)?
                .to_str()
                .context("documentation path is not UTF-8")?
                .to_string();
            let meta = fs::symlink_metadata(&path)?;
            if meta.is_dir() && !meta.is_symlink() && directory == scope.join("docs") {
                directories.push(path);
            } else if plan_name(&entry.file_name().to_string_lossy()) {
                names.insert(name);
            }
        }
        if count > 256 {
            break;
        }
    }
    for entrypoint in ["PROJECT_STATE.md", "AGENTS.md", "CLAUDE.md"] {
        let name = if anchor == "." {
            entrypoint.into()
        } else {
            format!("{anchor}/{entrypoint}")
        };
        let entrypoint_budget = if entrypoint == "PROJECT_STATE.md" {
            MAX_DOCUMENT
        } else {
            MAX_INSTRUCTIONS
        };
        match read(root, &name, entrypoint_budget) {
            Ok(bytes) => {
                report.inputs.push((name.clone(), digest(&bytes)));
                let text = std::str::from_utf8(&bytes).context("entrypoint is not UTF-8")?;
                let owner_links: BTreeSet<String> = text
                    .lines()
                    .take(128)
                    .filter(|line| {
                        let lower = line.to_ascii_lowercase();
                        ["owning", "authoritative", "todo", "backlog", "plan"]
                            .iter()
                            .any(|word| lower.contains(word))
                    })
                    .flat_map(local_links)
                    .collect();
                for link in local_links(text) {
                    let owner_reference = owner_links.contains(&link);
                    let resolved = normalize_link(&name, &link);
                    let status = if let Some(target) = &resolved {
                        if let Ok(bytes) = read(root, target, MAX_DOCUMENT) {
                            report.inputs.push((target.clone(), digest(&bytes)));
                            "present"
                        } else if let Ok(directory) =
                            process::open_bounded_subdirectory(&root_descriptor, Path::new(target))
                        {
                            report.inputs.push((
                                target.clone(),
                                format!(
                                    "directory:{:?}",
                                    process::filesystem_file_identity(&directory.metadata()?)
                                ),
                            ));
                            "present_directory"
                        } else {
                            "missing_or_unsafe"
                        }
                    } else {
                        "outside_or_unsupported"
                    };
                    let target = resolved.unwrap_or(link);
                    let is_entrypoint = Path::new(&target)
                        .file_name()
                        .and_then(|name| name.to_str())
                        .is_some_and(|name| {
                            ["AGENTS.md", "CLAUDE.md", "PROJECT_STATE.md", "README.md"]
                                .contains(&name)
                        });
                    if (plan_name(&target) || (owner_reference && !is_entrypoint))
                        && status != "outside_or_unsupported"
                    {
                        names.insert(target.clone());
                    }
                    report.first_read_links.push(Link {
                        source: name.clone(),
                        target,
                        status: status.into(),
                    });
                }
            }
            Err(error) => {
                if entrypoint != "CLAUDE.md" && fs::symlink_metadata(root.join(&name)).is_ok() {
                    report
                        .blockers
                        .push(format!("unsafe first-read entrypoint {name}: {error}"));
                }
            }
        }
    }
    if let Some(owner) = owner {
        relative(owner)?;
        names.insert(owner.into());
    }
    for name in names {
        let observed = read(root, &name, MAX_DOCUMENT);
        let referenced_by = report
            .first_read_links
            .iter()
            .filter(|link| link.target == name)
            .map(|link| link.source.clone())
            .collect();
        report.candidates.push(Candidate {
            path: name,
            status: if observed.is_ok() {
                "present"
            } else {
                "missing_or_unsafe"
            }
            .into(),
            sha256: observed.ok().map(|bytes| digest(&bytes)),
            referenced_by,
        });
    }
    let mut roles = BTreeSet::new();
    for route in document_routes {
        let (role, path) = route
            .split_once('=')
            .context("document route requires ROLE=PATH")?;
        ensure!(
            ["working_notes", "evidence", "reports", "canonical"].contains(&role),
            "unknown document role: {role}"
        );
        ensure!(roles.insert(role), "duplicate document role: {role}");
        relative(path)?;
        let status = match process::open_bounded_subdirectory(&root_descriptor, Path::new(path)) {
            Ok(directory) => format!(
                "directory:{:?}",
                process::filesystem_file_identity(&directory.metadata()?)
            ),
            Err(_) => match read(root, path, MAX_DOCUMENT) {
                Ok(bytes) => format!("file:{}", digest(&bytes)),
                Err(_) => "missing_or_unsafe".into(),
            },
        };
        if status == "missing_or_unsafe" {
            report.blockers.push(format!(
                "document route is missing or unsafe: {role}={path}"
            ));
        }
        report.document_routes.push(DocumentRoute {
            role: role.into(),
            path: path.into(),
            status,
        });
    }
    report.document_routes.sort_by(|a, b| a.role.cmp(&b.role));
    let Some(owner) = owner else {
        let present = report
            .candidates
            .iter()
            .filter(|c| c.status == "present")
            .count();
        report.owner_status = match present {
            0 => "missing",
            1 => "candidate_requires_selection",
            _ => "ambiguous_requires_selection",
        }
        .into();
        report
            .blockers
            .push("select --owning-plan explicitly after reviewing existing owners".into());
        return Ok(report);
    };
    let owner_candidate = report
        .candidates
        .iter()
        .find(|c| c.path == owner)
        .expect("selected owner was inventoried");
    if owner_candidate.status != "present" {
        report.owner_status = "selected_missing_or_unsafe".into();
        if !create_plan || fs::symlink_metadata(root.join(owner)).is_ok() {
            report.blockers.push(
                "selected plan is missing or unsafe; do not replace an existing object".into(),
            );
            return Ok(report);
        }
        if report
            .candidates
            .iter()
            .any(|candidate| candidate.path != owner)
        {
            report.blockers.push(
                "other plan candidates require ownership resolution before creating another plan"
                    .into(),
            );
            return Ok(report);
        }
        let relative_parent = Path::new(owner)
            .parent()
            .context("owning plan parent is missing")?;
        if !relative_parent.as_os_str().is_empty() {
            process::open_bounded_subdirectory(&root_descriptor, relative_parent)
                .context("owning plan parent is missing or unsafe")?;
        }
        let parent = root
            .join(owner)
            .parent()
            .context("owning plan parent is missing")?
            .to_path_buf();
        let target_boundary = ProjectBoundary::open(&parent)?;
        report.inputs.push((
            format!("target_parent:{owner}"),
            format!(
                "{:?}",
                process::filesystem_file_identity(&fs::metadata(&parent)?)
            ),
        ));
        let filename = Path::new(owner)
            .file_name()
            .and_then(|v| v.to_str())
            .context("invalid owner filename")?;
        let before = target_boundary.inspect(filename, MAX_INSTRUCTIONS)?;
        ensure!(
            matches!(before, ObservedPath::Missing),
            "owner appeared during preview"
        );
        let after = format!("# Owning Plan: {key}\n\nThis is the authoritative TODO for the selected project scope.\nDefault mode: Ondesk. No Offdesk work is authorized by this empty plan.\n\n## Current work\n\nNo work packages have been agreed yet. Add real work and acceptance checks here.\n").into_bytes();
        set_operation(
            &mut report,
            "establish_plan_then_review_routing",
            owner,
            before,
            after,
        )?;
        return Ok(report);
    }
    report.owner_status = "explicit_existing_owner".into();
    if create_plan {
        report
            .blockers
            .push("plan already exists; omit --create-plan to review instruction routing".into());
        return Ok(report);
    }
    let before_text = match &agents {
        ObservedPath::Missing => "",
        ObservedPath::Regular { bytes, .. } => {
            std::str::from_utf8(bytes).context("AGENTS.md is not UTF-8")?
        }
        _ => {
            report
                .blockers
                .push("AGENTS.md must be a safe regular file or missing".into());
            return Ok(report);
        }
    };
    let section = snippet(owner, &report.document_routes, anchor);
    let after = if before_text.contains(START) || before_text.contains(END) {
        if before_text.matches(START).count() != 1
            || before_text.matches(END).count() != 1
            || !before_text.contains(&section)
        {
            report.blockers.push("generated routing section differs or is malformed; review drift without overwriting".into());
            return Ok(report);
        }
        before_text.as_bytes().to_vec()
    } else {
        let separator = if before_text.is_empty() || before_text.ends_with("\n\n") {
            ""
        } else if before_text.ends_with('\n') {
            "\n"
        } else {
            "\n\n"
        };
        format!("{before_text}{separator}{section}").into_bytes()
    };
    ensure!(
        after.len() <= MAX_INSTRUCTIONS,
        "instruction result exceeds write budget"
    );
    let target = if anchor == "." {
        "AGENTS.md".into()
    } else {
        format!("{anchor}/AGENTS.md")
    };
    set_operation(
        &mut report,
        if agents.bytes() == Some(&after) {
            "unchanged"
        } else {
            "append_instructions"
        },
        &target,
        agents,
        after,
    )?;
    Ok(report)
}

fn observation_receipt(observed: &ObservedPath) -> String {
    match observed {
        ObservedPath::Regular { snapshot, bytes } => format!("{snapshot:?}:{}", digest(bytes)),
        _ => format!("{observed:?}"),
    }
}

fn set_operation(
    report: &mut RoutingPreview,
    action: &str,
    path: &str,
    before: ObservedPath,
    after: Vec<u8>,
) -> Result<()> {
    let old = std::str::from_utf8(before.bytes().unwrap_or_default())?;
    let new = std::str::from_utf8(&after)?;
    report.diff = TextDiff::from_lines(old, new)
        .unified_diff()
        .header(path, path)
        .to_string();
    report.action = action.into();
    report.target_path = Some(path.into());
    report.before_sha256 = before.bytes().map(digest);
    report.after_sha256 = Some(digest(&after));
    // Preview only the generated addition, preserving authored instruction privacy.
    report.content_preview = Some(if action == "establish_plan_then_review_routing" {
        new.into()
    } else {
        new.split_once(START)
            .map(|(_, body)| format!("{START}{body}"))
            .unwrap_or_default()
    });
    report.before = Some(before);
    report.after = Some(after);
    Ok(())
}

pub(super) fn run(args: ProjectPlanRoutingArgs) -> Result<()> {
    let key = super::sanitize_required("project key", &args.project_key)?;
    let root = args.path.canonicalize()?;
    let mut anchor = ".".to_string();
    let mut owner = args.owning_plan.clone();
    let mut spec_sha = None;
    if let Some(path) = &args.subproject_spec {
        let before = spec_bytes(path)?;
        let scopes =
            super::load_subproject_candidates(std::slice::from_ref(path), &key, &root, &[])?;
        ensure!(
            before == spec_bytes(path)?,
            "subproject declaration changed during validation"
        );
        let scope = scopes
            .first()
            .context("subproject declaration produced no scope")?;
        if let Some(explicit) = &owner {
            ensure!(
                Some(explicit) == scope.owning_plan_path.as_ref(),
                "explicit owner conflicts with declared subproject owner"
            );
        }
        anchor = scope.path.clone();
        owner = scope.owning_plan_path.clone();
        spec_sha = Some(digest(&before));
    }
    let mut preview = inspect(
        &root,
        &key,
        owner.as_deref(),
        &anchor,
        &args.document_routes,
        args.create_plan,
    )?;
    preview.subproject_spec_sha256 = spec_sha;
    let preview_sha256 = digest(&serde_json::to_vec(&preview)?);
    let mut applied = false;
    if args.reviewed {
        ensure!(
            args.expected_preview_sha256.as_deref() == Some(&preview_sha256),
            "preview hash changed; inspect the current proposal before applying"
        );
        ensure!(
            preview.blockers.is_empty(),
            "routing proposal is blocked: {}",
            preview.blockers.join("; ")
        );
        if let (Some(target), Some(before), Some(after)) =
            (&preview.target_path, &preview.before, &preview.after)
        {
            // One durable file per apply. Missing-plan establishment precedes a fresh routing preview.
            let target = root.join(target);
            let parent = target.parent().context("missing target parent")?;
            let boundary = ProjectBoundary::open(parent)?;
            let name = target
                .file_name()
                .and_then(|v| v.to_str())
                .context("invalid target name")?;
            // Rebuild immediately before commit to recheck the full owner/entrypoint/link receipt.
            let mut current = inspect(
                &root,
                &key,
                owner.as_deref(),
                &anchor,
                &args.document_routes,
                args.create_plan,
            )?;
            if let Some(spec) = &args.subproject_spec {
                ensure!(
                    Some(digest(&spec_bytes(spec)?)) == preview.subproject_spec_sha256,
                    "subproject declaration changed before apply"
                );
            }
            current.subproject_spec_sha256 = preview.subproject_spec_sha256.clone();
            ensure!(
                digest(&serde_json::to_vec(&current)?) == preview_sha256,
                "project documents changed before routing apply"
            );
            applied = boundary.write_exact(name, before, after)?;
        } else {
            bail!("routing proposal has no operation");
        }
    }
    if args.json {
        println!(
            "{}",
            serde_json::to_string_pretty(
                &serde_json::json!({"preview":preview,"preview_sha256":preview_sha256,"reviewed":args.reviewed,"applied":applied,"writes_project_state":applied})
            )?
        );
    } else {
        println!(
            "Owner: {}\nAction: {}\nPreview SHA256: {preview_sha256}\nApplied: {applied}",
            preview.owner_status, preview.action
        );
        for blocker in &preview.blockers {
            println!("Hold: {blocker}");
        }
        print!("{}", preview.diff);
    }
    Ok(())
}

fn spec_bytes(path: &Path) -> Result<Vec<u8>> {
    let absolute = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let parent = absolute
        .parent()
        .context("missing declaration parent")?
        .canonicalize()?;
    let name = absolute
        .file_name()
        .and_then(|name| name.to_str())
        .context("invalid declaration filename")?;
    read(&parent, name, MAX_INSTRUCTIONS)
}
