//! `forager ondesk` subcommands for bridging live external harness work.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use clap::{Args, Subcommand};
use serde::{Deserialize, Serialize};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use uuid::Uuid;

use crate::offdesk::operator_safe_text;
use crate::session::{get_profile_dir, Instance, Storage};

const NOTES_FILE: &str = "ondesk_notes.jsonl";
const CAPTURES_DIR: &str = "ondesk_captures";
const PROMPT_CONTEXT_FILE: &str = "PROMPT_CONTEXT.md";
const CAPTURE_FILE: &str = "capture.json";
const MAX_CAPTURE_CHARS: usize = 30_000;
const MAX_GIT_CHARS: usize = 12_000;
const MAX_PROMPT_CHARS: usize = 40_000;
const MAX_RECENT_NOTES: usize = 20;

#[derive(Subcommand)]
pub enum OndeskCommands {
    /// Append a safe operator note for an ondesk session or project
    Note(NoteArgs),

    /// Capture live harness scrollback into an inspectable prompt package
    Capture(CaptureArgs),

    /// Build a markdown prompt package from recent notes and optional capture
    #[command(name = "prompt-package")]
    PromptPackage(PromptPackageArgs),
}

#[derive(Args)]
pub struct NoteArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Operator note text to persist
    #[arg(long)]
    text: String,

    /// Work mode label, e.g. planning, analysis, writing, critique
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key for grouping ondesk knowledge
    #[arg(long)]
    project_key: Option<String>,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct CaptureArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Number of tmux scrollback lines to capture
    #[arg(long, default_value_t = 200)]
    lines: usize,

    /// Work mode label, e.g. planning, analysis, writing, critique
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key for grouping ondesk knowledge
    #[arg(long)]
    project_key: Option<String>,

    /// Include read-only git status and diff-stat from the session/project path
    #[arg(long)]
    include_git: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct PromptPackageArgs {
    /// Session ID, title, or project path. Defaults to current tmux Forager session or cwd.
    identifier: Option<String>,

    /// Existing capture ID to render
    #[arg(long)]
    capture_id: Option<String>,

    /// Work mode label used to filter notes
    #[arg(long)]
    mode: Option<String>,

    /// Stable project key used to filter notes
    #[arg(long)]
    project_key: Option<String>,

    /// Write markdown package to a file instead of stdout
    #[arg(long)]
    output: Option<PathBuf>,

    /// Output metadata as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionRef {
    id: String,
    title: String,
    path: String,
    group: String,
    tool: String,
    command: String,
    status: String,
}

impl SessionRef {
    fn from_instance(instance: &Instance) -> Self {
        Self {
            id: safe(&instance.id),
            title: safe(&instance.title),
            path: safe(&instance.project_path),
            group: safe(&instance.group_path),
            tool: safe(&instance.tool),
            command: safe(&instance.command),
            status: format!("{:?}", instance.status).to_lowercase(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OndeskNoteRecord {
    id: String,
    created_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    text: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct GitSnapshot {
    #[serde(skip_serializing_if = "Option::is_none")]
    status_short: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    diff_stat: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct OndeskCaptureRecord {
    id: String,
    created_at: DateTime<Utc>,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    session: Option<SessionRef>,
    lines_requested: usize,
    session_running: bool,
    scrollback: String,
    scrollback_char_count: usize,
    scrollback_truncated: bool,
    notes: Vec<OndeskNoteRecord>,
    #[serde(skip_serializing_if = "Option::is_none")]
    git: Option<GitSnapshot>,
    artifact_dir: String,
    capture_path: String,
    prompt_package_path: String,
}

#[derive(Debug, Serialize)]
struct NoteOutput {
    id: String,
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    mode: Option<String>,
    notes_path: String,
}

#[derive(Debug, Serialize)]
struct CaptureOutput {
    id: String,
    profile: String,
    project_key: String,
    session_running: bool,
    scrollback_char_count: usize,
    scrollback_truncated: bool,
    note_count: usize,
    artifact_dir: String,
    capture_path: String,
    prompt_package_path: String,
}

#[derive(Debug, Serialize)]
struct PromptPackageOutput {
    profile: String,
    project_key: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    capture_id: Option<String>,
    note_count: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    output_path: Option<String>,
    content: String,
}

struct ResolvedOndeskContext {
    profile: String,
    profile_dir: PathBuf,
    session: Option<Instance>,
    project_key: String,
    mode: Option<String>,
}

pub async fn run(profile: &str, command: OndeskCommands) -> Result<()> {
    match command {
        OndeskCommands::Note(args) => note(profile, args).await,
        OndeskCommands::Capture(args) => capture(profile, args).await,
        OndeskCommands::PromptPackage(args) => prompt_package(profile, args).await,
    }
}

async fn note(profile: &str, args: NoteArgs) -> Result<()> {
    let context = resolve_context(
        profile,
        args.identifier.as_deref(),
        args.project_key,
        args.mode,
    )?;
    let notes_path = context.profile_dir.join(NOTES_FILE);
    let record = OndeskNoteRecord {
        id: short_id("ondesk-note"),
        created_at: Utc::now(),
        profile: context.profile.clone(),
        project_key: context.project_key.clone(),
        session_id: context.session.as_ref().map(|session| safe(&session.id)),
        session_title: context.session.as_ref().map(|session| safe(&session.title)),
        session_path: context
            .session
            .as_ref()
            .map(|session| safe(&session.project_path)),
        mode: context.mode.clone(),
        text: safe(&args.text),
    };

    append_note(&notes_path, &record)?;

    if args.json {
        let output = NoteOutput {
            id: record.id,
            profile: context.profile,
            project_key: context.project_key,
            session_id: record.session_id,
            mode: record.mode,
            notes_path: notes_path.display().to_string(),
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!("Recorded ondesk note: {}", record.id);
        println!("  Project: {}", context.project_key);
        if let Some(session) = &context.session {
            println!("  Session: {} ({})", session.title, session.id);
        }
        println!("  Path:    {}", notes_path.display());
    }

    Ok(())
}

async fn capture(profile: &str, args: CaptureArgs) -> Result<()> {
    let context = resolve_context(
        profile,
        args.identifier.as_deref(),
        args.project_key,
        args.mode,
    )?;
    let notes = matching_recent_notes(&context.profile_dir, &context)?;
    let (scrollback, session_running) = capture_scrollback(context.session.as_ref(), args.lines)?;
    let safe_scrollback = safe(&scrollback);
    let (scrollback, scrollback_truncated) = truncate_chars(&safe_scrollback, MAX_CAPTURE_CHARS);
    let scrollback_char_count = scrollback.chars().count();
    let git = if args.include_git {
        context
            .session
            .as_ref()
            .map(|session| git_snapshot(Path::new(&session.project_path)))
            .transpose()?
    } else {
        None
    };

    let capture_id = short_id("ondesk-cap");
    let now = Utc::now();
    let capture_dir = context.profile_dir.join(CAPTURES_DIR).join(format!(
        "{}_{}",
        now.format("%Y%m%dT%H%M%SZ"),
        capture_id
    ));
    fs::create_dir_all(&capture_dir)?;

    let capture_path = capture_dir.join(CAPTURE_FILE);
    let prompt_package_path = capture_dir.join(PROMPT_CONTEXT_FILE);
    let record = OndeskCaptureRecord {
        id: capture_id.clone(),
        created_at: now,
        profile: context.profile.clone(),
        project_key: context.project_key.clone(),
        mode: context.mode.clone(),
        session: context.session.as_ref().map(SessionRef::from_instance),
        lines_requested: args.lines,
        session_running,
        scrollback,
        scrollback_char_count,
        scrollback_truncated,
        notes,
        git,
        artifact_dir: capture_dir.display().to_string(),
        capture_path: capture_path.display().to_string(),
        prompt_package_path: prompt_package_path.display().to_string(),
    };

    let package = render_prompt_package(PromptPackageContext::Capture(&record));
    fs::write(&capture_path, serde_json::to_string_pretty(&record)?)?;
    fs::write(&prompt_package_path, package)?;

    if args.json {
        let output = CaptureOutput {
            id: record.id,
            profile: record.profile,
            project_key: record.project_key,
            session_running: record.session_running,
            scrollback_char_count: record.scrollback_char_count,
            scrollback_truncated: record.scrollback_truncated,
            note_count: record.notes.len(),
            artifact_dir: record.artifact_dir,
            capture_path: record.capture_path,
            prompt_package_path: record.prompt_package_path,
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else {
        println!("Captured ondesk context: {}", record.id);
        println!("  Project: {}", record.project_key);
        println!("  Running: {}", record.session_running);
        println!("  Notes:   {}", record.notes.len());
        println!("  Package: {}", record.prompt_package_path);
    }

    Ok(())
}

async fn prompt_package(profile: &str, args: PromptPackageArgs) -> Result<()> {
    let profile_dir = get_profile_dir(profile)?;
    let profile_name = Storage::new(profile)?.profile().to_string();
    let (content, project_key, note_count, capture_id) = if let Some(capture_id) = args.capture_id {
        let capture = load_capture_by_id(&profile_dir, &capture_id)?;
        let note_count = capture.notes.len();
        let project_key = capture.project_key.clone();
        (
            render_prompt_package(PromptPackageContext::Capture(&capture)),
            project_key,
            note_count,
            Some(capture.id),
        )
    } else {
        let context = resolve_context(
            profile,
            args.identifier.as_deref(),
            args.project_key,
            args.mode,
        )?;
        let notes = matching_recent_notes(&context.profile_dir, &context)?;
        let session_ref = context.session.as_ref().map(SessionRef::from_instance);
        let content = render_prompt_package(PromptPackageContext::Live {
            profile: &context.profile,
            project_key: &context.project_key,
            mode: context.mode.as_deref(),
            session: session_ref.as_ref(),
            notes: &notes,
        });
        (content, context.project_key, notes.len(), None)
    };

    let (content, truncated) = truncate_chars(&content, MAX_PROMPT_CHARS);
    let output_path = if let Some(path) = args.output {
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, &content)?;
        Some(path.display().to_string())
    } else {
        None
    };

    if args.json {
        let output = PromptPackageOutput {
            profile: profile_name,
            project_key,
            capture_id,
            note_count,
            output_path,
            content: if truncated {
                format!("{}\n\n[package truncated for CLI output]", content)
            } else {
                content
            },
        };
        println!("{}", serde_json::to_string_pretty(&output)?);
    } else if let Some(path) = output_path {
        println!("Wrote ondesk prompt package: {}", path);
    } else {
        print!("{}", content);
        if truncated {
            println!("\n\n[package truncated for CLI output]");
        }
    }

    Ok(())
}

fn resolve_context(
    profile: &str,
    identifier: Option<&str>,
    project_key: Option<String>,
    mode: Option<String>,
) -> Result<ResolvedOndeskContext> {
    let storage = Storage::new(profile)?;
    let profile_name = storage.profile().to_string();
    let profile_dir = get_profile_dir(&profile_name)?;
    let instances = storage.load()?;
    let session = resolve_optional_session(identifier, &instances)?;
    let project_key = project_key
        .map(|value| safe(&value))
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| default_project_key(session.as_ref()));
    let mode = mode
        .map(|value| safe(&value))
        .filter(|value| !value.trim().is_empty());

    Ok(ResolvedOndeskContext {
        profile: profile_name,
        profile_dir,
        session,
        project_key,
        mode,
    })
}

fn resolve_optional_session(
    identifier: Option<&str>,
    instances: &[Instance],
) -> Result<Option<Instance>> {
    if let Some(identifier) = identifier {
        return Ok(Some(super::resolve_session(identifier, instances)?.clone()));
    }

    if let Some(session_name) = std::env::var("TMUX_PANE")
        .ok()
        .and_then(|_| crate::tmux::get_current_session_name())
    {
        if let Some(instance) = instances
            .iter()
            .find(|instance| tmux_session_name_matches(instance, &session_name))
        {
            return Ok(Some(instance.clone()));
        }
    }

    let current_dir = std::env::current_dir()?.display().to_string();
    if let Some(instance) = instances
        .iter()
        .find(|instance| paths_match(&instance.project_path, &current_dir))
    {
        return Ok(Some(instance.clone()));
    }

    Ok(None)
}

fn tmux_session_name_matches(instance: &Instance, session_name: &str) -> bool {
    crate::tmux::Session::generate_name(&instance.id, &instance.title) == session_name
        || crate::tmux::Session::generate_legacy_name(&instance.id, &instance.title) == session_name
}

fn paths_match(left: &str, right: &str) -> bool {
    let left = normalize_path(left);
    let right = normalize_path(right);
    left == right
}

fn normalize_path(path: &str) -> String {
    fs::canonicalize(path)
        .map(|path| path.display().to_string())
        .unwrap_or_else(|_| path.to_string())
}

fn default_project_key(session: Option<&Instance>) -> String {
    let path = session
        .map(|session| PathBuf::from(&session.project_path))
        .or_else(|| std::env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("default"));
    let key = path
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .unwrap_or("default");
    safe(key)
}

fn append_note(path: &Path, record: &OndeskNoteRecord) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut file = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(file, "{}", serde_json::to_string(record)?)?;
    Ok(())
}

fn load_notes(profile_dir: &Path) -> Result<Vec<OndeskNoteRecord>> {
    let path = profile_dir.join(NOTES_FILE);
    if !path.exists() {
        return Ok(Vec::new());
    }
    let content = fs::read_to_string(&path)?;
    let mut notes = Vec::new();
    for line in content.lines().filter(|line| !line.trim().is_empty()) {
        let note: OndeskNoteRecord = serde_json::from_str(line)
            .with_context(|| format!("failed to parse ondesk note in {}", path.display()))?;
        notes.push(note);
    }
    Ok(notes)
}

fn matching_recent_notes(
    profile_dir: &Path,
    context: &ResolvedOndeskContext,
) -> Result<Vec<OndeskNoteRecord>> {
    let mut notes: Vec<_> = load_notes(profile_dir)?
        .into_iter()
        .filter(|note| note_matches_context(note, context))
        .collect();
    notes.sort_by_key(|note| note.created_at);
    notes.reverse();
    notes.truncate(MAX_RECENT_NOTES);
    notes.reverse();
    Ok(notes)
}

fn note_matches_context(note: &OndeskNoteRecord, context: &ResolvedOndeskContext) -> bool {
    if let Some(mode) = &context.mode {
        if note.mode.as_ref() != Some(mode) {
            return false;
        }
    }

    if note.project_key == context.project_key {
        return true;
    }

    let Some(session) = &context.session else {
        return false;
    };

    note.session_id.as_deref() == Some(session.id.as_str())
        || note
            .session_path
            .as_deref()
            .is_some_and(|path| paths_match(path, &session.project_path))
}

fn capture_scrollback(session: Option<&Instance>, lines: usize) -> Result<(String, bool)> {
    let Some(session) = session else {
        return Ok((String::new(), false));
    };
    let tmux_session = session.tmux_session()?;
    let running = tmux_session.exists();
    if !running {
        return Ok((String::new(), false));
    }
    Ok((tmux_session.capture_pane(lines)?, true))
}

fn git_snapshot(path: &Path) -> Result<GitSnapshot> {
    if !path.exists() {
        return Ok(GitSnapshot {
            status_short: None,
            diff_stat: None,
            error: Some(safe(&format!(
                "project path does not exist: {}",
                path.display()
            ))),
        });
    }

    let status = read_git_output(path, &["status", "--short"])?;
    let diff_stat = read_git_output(path, &["diff", "--stat"])?;
    Ok(GitSnapshot {
        status_short: status,
        diff_stat,
        error: None,
    })
}

fn read_git_output(path: &Path, args: &[&str]) -> Result<Option<String>> {
    let output = Command::new("git").args(args).current_dir(path).output()?;
    let raw = if output.status.success() {
        String::from_utf8_lossy(&output.stdout).to_string()
    } else {
        String::from_utf8_lossy(&output.stderr).to_string()
    };
    let safe = safe(raw.trim());
    if safe.is_empty() {
        Ok(None)
    } else {
        let (text, truncated) = truncate_chars(&safe, MAX_GIT_CHARS);
        Ok(Some(if truncated {
            format!("{}\n[git output truncated]", text)
        } else {
            text
        }))
    }
}

fn load_capture_by_id(profile_dir: &Path, capture_id: &str) -> Result<OndeskCaptureRecord> {
    let captures_dir = profile_dir.join(CAPTURES_DIR);
    if !captures_dir.exists() {
        anyhow::bail!("No ondesk captures found");
    }

    for entry in fs::read_dir(&captures_dir)? {
        let entry = entry?;
        if !entry.path().is_dir() {
            continue;
        }
        let path = entry.path().join(CAPTURE_FILE);
        if !path.exists() {
            continue;
        }
        let capture: OndeskCaptureRecord = serde_json::from_str(&fs::read_to_string(&path)?)?;
        if capture.id == capture_id {
            return Ok(capture);
        }
    }

    anyhow::bail!("Ondesk capture not found: {}", capture_id)
}

enum PromptPackageContext<'a> {
    Capture(&'a OndeskCaptureRecord),
    Live {
        profile: &'a str,
        project_key: &'a str,
        mode: Option<&'a str>,
        session: Option<&'a SessionRef>,
        notes: &'a [OndeskNoteRecord],
    },
}

fn render_prompt_package(context: PromptPackageContext<'_>) -> String {
    match context {
        PromptPackageContext::Capture(capture) => render_prompt_package_parts(PromptPackageParts {
            profile: &capture.profile,
            project_key: &capture.project_key,
            mode: capture.mode.as_deref(),
            session: capture.session.as_ref(),
            notes: &capture.notes,
            scrollback: Some(&capture.scrollback),
            git: capture.git.as_ref(),
            capture_id: Some(&capture.id),
        }),
        PromptPackageContext::Live {
            profile,
            project_key,
            mode,
            session,
            notes,
        } => render_prompt_package_parts(PromptPackageParts {
            profile,
            project_key,
            mode,
            session,
            notes,
            scrollback: None,
            git: None,
            capture_id: None,
        }),
    }
}

struct PromptPackageParts<'a> {
    profile: &'a str,
    project_key: &'a str,
    mode: Option<&'a str>,
    session: Option<&'a SessionRef>,
    notes: &'a [OndeskNoteRecord],
    scrollback: Option<&'a str>,
    git: Option<&'a GitSnapshot>,
    capture_id: Option<&'a str>,
}

fn render_prompt_package_parts(parts: PromptPackageParts<'_>) -> String {
    let mut output = String::new();
    output.push_str("# Forager Ondesk Prompt Package\n\n");
    output.push_str("## Context\n");
    output.push_str(&format!("- profile: {}\n", parts.profile));
    output.push_str(&format!("- project_key: {}\n", parts.project_key));
    if let Some(mode) = parts.mode {
        output.push_str(&format!("- mode: {}\n", mode));
    }
    if let Some(capture_id) = parts.capture_id {
        output.push_str(&format!("- capture_id: {}\n", capture_id));
    }
    if let Some(session) = parts.session {
        output.push_str(&format!("- session: {} ({})\n", session.title, session.id));
        output.push_str(&format!("- path: {}\n", session.path));
        output.push_str(&format!("- tool: {}\n", session.tool));
    } else {
        output.push_str("- session: none\n");
    }

    output.push_str("\n## Operator Notes\n");
    if parts.notes.is_empty() {
        output.push_str("- No recent ondesk notes recorded for this context.\n");
    } else {
        for note in parts.notes {
            let mode = note
                .mode
                .as_deref()
                .map(|mode| format!(" [{}]", mode))
                .unwrap_or_default();
            output.push_str(&format!(
                "- {}{}: {}\n",
                note.created_at.to_rfc3339(),
                mode,
                note.text.replace('\n', " ")
            ));
        }
    }

    if let Some(git) = parts.git {
        output.push_str("\n## Git Snapshot\n");
        if let Some(status) = &git.status_short {
            output.push_str("### git status --short\n");
            output.push_str(&fenced("text", status));
        }
        if let Some(diff_stat) = &git.diff_stat {
            output.push_str("### git diff --stat\n");
            output.push_str(&fenced("text", diff_stat));
        }
        if let Some(error) = &git.error {
            output.push_str(&format!("- git snapshot unavailable: {}\n", error));
        }
        if git.status_short.is_none() && git.diff_stat.is_none() && git.error.is_none() {
            output.push_str("- No git changes detected.\n");
        }
    }

    if let Some(scrollback) = parts.scrollback {
        output.push_str("\n## Captured Harness Scrollback\n");
        if scrollback.trim().is_empty() {
            output.push_str("- No live tmux scrollback was available.\n");
        } else {
            output.push_str(&fenced("text", scrollback));
        }
    }

    output.push_str("\n## Instructions For The Next Harness\n");
    output.push_str("- Treat this package as context, not proof that work is complete.\n");
    output.push_str("- Separate observations from inference before making claims.\n");
    output
        .push_str("- Preserve the user's current direction unless new evidence contradicts it.\n");
    output.push_str("- When useful, propose wiki-worthy knowledge as a candidate rather than silently mutating durable knowledge.\n");
    output
}

fn fenced(language: &str, body: &str) -> String {
    format!("```{}\n{}\n```\n", language, body.trim_end())
}

fn short_id(prefix: &str) -> String {
    let id = Uuid::new_v4().to_string();
    format!("{}-{}", prefix, &id[..8])
}

fn safe(value: &str) -> String {
    operator_safe_text(value).trim().to_string()
}

fn truncate_chars(value: &str, max_chars: usize) -> (String, bool) {
    let count = value.chars().count();
    if count <= max_chars {
        return (value.to_string(), false);
    }
    (value.chars().take(max_chars).collect(), true)
}
