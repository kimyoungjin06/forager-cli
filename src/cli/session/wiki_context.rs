//! Session-facing presentation of the bounded project wiki brief.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{bail, ensure, Context, Result};
use chrono::{DateTime, Utc};
use clap::{Args, ValueEnum};
use serde::Serialize;
use sha2::{Digest, Sha256};
use tokio::time::sleep;

use crate::offdesk::wiki_context_file::{WikiBriefCurrentSnapshot, WikiBriefOutputBoundary};
use crate::offdesk::wiki_context_receipts::WikiContextReceiptStore;
use crate::offdesk::{
    AdaptiveWikiAgentMode, WikiContextAgentAcknowledgementV1, WikiContextAgentMode,
    WikiContextAgentTool, WikiContextManifestV1, WikiContextPresentationReceiptV1,
    WikiContextPresentationStatus, WikiContextProjectionV1,
    WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA,
};
use crate::session::project_registry::{
    load_registry, resolve_unique_project_for_path, ProjectRegistryEntry,
};
use crate::session::{Instance, Storage};

const SESSION_STATUS_SCHEMA: &str = "wiki_context_session_status.v1";
const SYNC_RESULT_SCHEMA: &str = "wiki_context_sync_result.v1";
const BRIEF_FILE: &str = ".wiki-brief.md";
const STARTUP_PROMPT_TIMEOUT: Duration = Duration::from_secs(15);
const PANE_TRANSITION_TIMEOUT: Duration = Duration::from_secs(3);
const POLL_INTERVAL: Duration = Duration::from_millis(150);

#[derive(Debug, Clone, Copy, PartialEq, Eq, ValueEnum)]
pub(super) enum ContextAgentModeArg {
    Shared,
    Planning,
    Development,
    Analysis,
    Writing,
    Critique,
    Review,
    Maintenance,
}

impl ContextAgentModeArg {
    fn projection_mode(self) -> Option<AdaptiveWikiAgentMode> {
        match self {
            Self::Shared => None,
            Self::Planning => Some(AdaptiveWikiAgentMode::Planning),
            Self::Development => Some(AdaptiveWikiAgentMode::Development),
            Self::Analysis => Some(AdaptiveWikiAgentMode::Analysis),
            Self::Writing => Some(AdaptiveWikiAgentMode::Writing),
            Self::Critique => Some(AdaptiveWikiAgentMode::Critique),
            Self::Review => Some(AdaptiveWikiAgentMode::Review),
            Self::Maintenance => Some(AdaptiveWikiAgentMode::Maintenance),
        }
    }
}

#[derive(Args)]
pub struct ContextStatusArgs {
    /// Exact session ID, unique prefix, title, or project path
    identifier: String,

    /// Output the read-only status envelope as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Args)]
pub struct ContextSyncArgs {
    /// Exact session ID, unique prefix, title, or project path
    identifier: String,

    /// Work mode used to select reviewed wiki entries
    #[arg(long, value_enum, default_value = "shared")]
    agent_mode: ContextAgentModeArg,

    /// Refuse to use the last exact brief if refresh fails
    #[arg(long)]
    require_fresh: bool,

    /// Output the sync receipt and resulting status as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct WikiContextSessionStatusV1 {
    schema: &'static str,
    session_id: String,
    session_title: String,
    tool: String,
    project_path: String,
    running: bool,
    brief_status: &'static str,
    presentation_status: &'static str,
    fresh_for_session: bool,
    sync_required: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    project_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    wiki_profile: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    projection_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    brief_file_sha256: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    receipt_id: Option<String>,
    acknowledgement_observed: bool,
    read_only: bool,
    writes_performed: bool,
    warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
struct WikiContextSyncResultV1 {
    schema: &'static str,
    receipt: WikiContextPresentationReceiptV1,
    status: WikiContextSessionStatusV1,
    refresh_fallback_used: bool,
    input_authority: &'static str,
}

#[derive(Debug, Clone)]
struct PreparedContext {
    projection: WikiContextProjectionV1,
    entry_ids: Vec<String>,
    refresh_fallback_used: bool,
}

struct ReceiptObservation {
    status: WikiContextPresentationStatus,
    attempted_at: Option<DateTime<Utc>>,
    pane_before_sha256: Option<String>,
    pane_after_sha256: Option<String>,
}

impl ReceiptObservation {
    fn without_attempt(status: WikiContextPresentationStatus) -> Self {
        Self {
            status,
            attempted_at: None,
            pane_before_sha256: None,
            pane_after_sha256: None,
        }
    }
}

pub(super) async fn run_context_status(profile: &str, args: ContextStatusArgs) -> Result<()> {
    let storage = Storage::new_read_only(profile)?;
    let (instances, _) = storage.load_with_groups()?;
    let instance = super::super::resolve_session(&args.identifier, &instances)?;
    let status = build_session_context_status(profile, instance, true);
    if args.json {
        println!("{}", serde_json::to_string_pretty(&status)?);
    } else {
        present_status(&status);
    }
    Ok(())
}

pub(super) async fn run_context_sync(profile: &str, args: ContextSyncArgs) -> Result<()> {
    let storage = Storage::new(profile)?;
    let (instances, _) = storage.load_with_groups()?;
    let instance = super::super::resolve_session(&args.identifier, &instances)?;
    let tmux_session = crate::tmux::Session::new(&instance.id, &instance.title)?;
    ensure!(
        tmux_session.exists(),
        "session is not running; no brief refresh or context input was performed"
    );
    let context = prepare_context(
        profile,
        instance,
        args.agent_mode.projection_mode(),
        args.require_fresh,
    )
    .await?;
    let receipt = present_context(profile, instance, &context, false, false).await?;
    let status = build_session_context_status(profile, instance, true);
    let result = WikiContextSyncResultV1 {
        schema: SYNC_RESULT_SCHEMA,
        receipt,
        status,
        refresh_fallback_used: context.refresh_fallback_used,
        input_authority: "explicit_local_cli_context_sync",
    };
    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else {
        println!(
            "Wiki context sync: {}",
            presentation_status_label(result.receipt.presentation_status)
        );
        println!(
            "  Session:    {}",
            result.receipt.session_id.as_deref().unwrap_or("-")
        );
        println!("  Projection: {}", result.receipt.projection_sha256);
        println!("  Brief:      {}", result.receipt.brief_file_sha256);
        println!("  Receipt:    {}", result.receipt.receipt_id);
        if result.refresh_fallback_used {
            println!("  Refresh:    fallback to last exact brief");
        }
    }
    Ok(())
}

pub(crate) async fn auto_present_new_go_session(
    profile: &str,
    instance: &Instance,
    manifest: &WikiContextManifestV1,
) -> Result<Option<WikiContextPresentationReceiptV1>> {
    let Some(projection) = manifest.projection() else {
        return Ok(None);
    };
    let root = PathBuf::from(&instance.project_path)
        .canonicalize()
        .context("canonicalize go session root for wiki context presentation")?;
    let project = exact_registered_project(&root)?;
    ensure!(
        project.key == projection.project_key
            && project.wiki_profile.as_deref() == Some(projection.wiki_profile.as_str()),
        "go wiki context manifest no longer matches the unique project registry binding"
    );
    let context = PreparedContext {
        projection: projection.clone(),
        entry_ids: projection.entry_ids.clone(),
        refresh_fallback_used: false,
    };
    present_context(profile, instance, &context, true, true)
        .await
        .map(Some)
}

pub(crate) fn context_summary_for_tui(profile: &str, instance: &Instance) -> String {
    let status = build_session_context_status(profile, instance, false);
    match (
        status.brief_status,
        status.presentation_status,
        status.sync_required,
    ) {
        ("ready", "agent_acknowledged", false) => "acknowledged · current".to_string(),
        ("ready", "presented", false) => "presented · current".to_string(),
        ("ready", _, true) => "brief changed · sync required".to_string(),
        ("ready", _, false) => "brief ready · not presented".to_string(),
        ("missing", _, _) => "brief missing".to_string(),
        ("unsafe", _, _) => "unavailable · inspect context-status".to_string(),
        _ => "unavailable".to_string(),
    }
}

fn build_session_context_status(
    profile: &str,
    instance: &Instance,
    inspect_acknowledgement: bool,
) -> WikiContextSessionStatusV1 {
    let mut warnings = Vec::new();
    let tmux_session = crate::tmux::Session::new(&instance.id, &instance.title).ok();
    let running = tmux_session
        .as_ref()
        .is_some_and(|session| session.exists());
    let runtime_identity_sha256 = if running {
        match tmux_session
            .as_ref()
            .context("missing tmux session")
            .and_then(|session| session.runtime_identity())
        {
            Ok(identity) => Some(sha256_hex(identity.as_bytes())),
            Err(error) => {
                warnings.push(format!("session runtime identity unavailable: {error}"));
                None
            }
        }
    } else {
        None
    };
    let project_root = PathBuf::from(&instance.project_path);
    let warnings_before_brief = warnings.len();
    let current = inspect_brief(&project_root, &mut warnings);
    let brief_unsafe = current.is_none() && warnings.len() > warnings_before_brief;
    let project = match exact_registered_project(&project_root) {
        Ok(project) => Some(project),
        Err(error) => {
            warnings.push(format!("project registry binding unavailable: {error}"));
            None
        }
    };
    if project
        .as_ref()
        .is_some_and(|entry| entry.wiki_profile.is_none())
    {
        warnings.push("registered project has no wiki profile".to_string());
    }
    let expected_tool = match agent_tool(&instance.tool) {
        Ok(tool) => Some(tool),
        Err(error) => {
            warnings.push(error.to_string());
            None
        }
    };
    let expected_project_binding = project.as_ref().and_then(|entry| {
        entry
            .wiki_profile
            .as_deref()
            .map(|wiki_profile| (entry.key.as_str(), wiki_profile))
    });
    let history = match WikiContextReceiptStore::read_only(profile)
        .and_then(|store| store.history_for_session(&instance.id))
    {
        Ok(history) => history,
        Err(error) => {
            warnings.push(format!("presentation receipt unavailable: {error}"));
            Vec::new()
        }
    };
    let latest = history.last();
    let current_identity = current.as_ref().map(|snapshot| &snapshot.identity);
    let matching_receipt = |receipt: &&WikiContextPresentationReceiptV1| {
        current_identity.is_some_and(|identity| {
            expected_tool == Some(receipt.agent_tool)
                && expected_project_binding.is_some_and(|(project_key, wiki_profile)| {
                    receipt.project_key == project_key && receipt.wiki_profile == wiki_profile
                })
                && receipt.project_root_identity == identity.project_root_identity
                && receipt.brief_file_sha256 == identity.brief_file_sha256
                && receipt.session_runtime_identity_sha256 == runtime_identity_sha256
                && runtime_identity_sha256.is_some()
        })
    };
    let latest_current = history.iter().rev().find(matching_receipt);
    let acknowledged = history.iter().rev().find(|receipt| {
        matching_receipt(receipt)
            && receipt.presentation_status == WikiContextPresentationStatus::AgentAcknowledged
    });
    let presented = history.iter().rev().find(|receipt| {
        matching_receipt(receipt)
            && matches!(
                receipt.presentation_status,
                WikiContextPresentationStatus::Presented
                    | WikiContextPresentationStatus::AgentAcknowledged
                    | WikiContextPresentationStatus::OperatorVerified
            )
    });
    let fresh_for_session = presented.is_some();
    let sync_required = current.is_some() && !fresh_for_session;
    let presentation_status = if acknowledged.is_some() {
        "agent_acknowledged"
    } else if presented.is_some() {
        "presented"
    } else if latest_current.is_some_and(|receipt| {
        receipt.presentation_status == WikiContextPresentationStatus::DeferredBusy
    }) {
        "deferred_busy"
    } else if latest_current.is_some_and(|receipt| {
        receipt.presentation_status == WikiContextPresentationStatus::NoEffect
    }) {
        "no_effect"
    } else if latest_current.is_some() {
        "prepared"
    } else if current.is_some() && latest.is_some() {
        "pending_sync"
    } else if current.is_some() {
        "not_presented"
    } else {
        "unavailable"
    };

    let evidence = acknowledged.or(presented).or(latest_current).or(latest);
    let acknowledgement_observed = inspect_acknowledgement
        && running
        && presented.is_some_and(|receipt| {
            tmux_session.as_ref().is_some_and(|session| {
                session.capture_pane(80).is_ok_and(|pane| {
                    acknowledgement_visible(
                        &pane,
                        &receipt.projection_sha256,
                        current
                            .as_ref()
                            .map(|snapshot| parse_brief_entry_ids(&snapshot.bytes))
                            .as_deref()
                            .unwrap_or_default(),
                    )
                })
            })
        });
    WikiContextSessionStatusV1 {
        schema: SESSION_STATUS_SCHEMA,
        session_id: instance.id.clone(),
        session_title: instance.title.clone(),
        tool: instance.tool.clone(),
        project_path: instance.project_path.clone(),
        running,
        brief_status: if current.is_some() {
            "ready"
        } else if brief_unsafe {
            "unsafe"
        } else {
            "missing"
        },
        presentation_status,
        fresh_for_session,
        sync_required,
        project_key: project
            .as_ref()
            .map(|entry| entry.key.clone())
            .or_else(|| evidence.map(|receipt| receipt.project_key.clone())),
        wiki_profile: project
            .as_ref()
            .and_then(|entry| entry.wiki_profile.clone())
            .or_else(|| evidence.map(|receipt| receipt.wiki_profile.clone())),
        projection_sha256: evidence.map(|receipt| receipt.projection_sha256.clone()),
        brief_file_sha256: current_identity.map(|identity| identity.brief_file_sha256.clone()),
        receipt_id: evidence.map(|receipt| receipt.receipt_id.clone()),
        acknowledgement_observed,
        read_only: true,
        writes_performed: false,
        warnings,
    }
}

async fn prepare_context(
    profile: &str,
    instance: &Instance,
    agent_mode: Option<AdaptiveWikiAgentMode>,
    require_fresh: bool,
) -> Result<PreparedContext> {
    ensure_supported_tool(&instance.tool)?;
    let root = PathBuf::from(&instance.project_path)
        .canonicalize()
        .context("canonicalize session project root for wiki context")?;
    let project = exact_registered_project(&root)?;
    let wiki_profile = project
        .wiki_profile
        .as_deref()
        .context("registered project has no wiki profile")?;
    let args = crate::cli::offdesk::WikiBriefArgs::scoped_to_file_with_mode(
        project.key.clone(),
        root.join(BRIEF_FILE),
        agent_mode,
    );
    match crate::cli::offdesk::wiki_brief(wiki_profile, args).await {
        Ok(Some(manifest)) => {
            let projection = manifest
                .projection()
                .context("wiki brief refresh returned no usable projection")?
                .clone();
            ensure!(
                projection.project_key == project.key && projection.wiki_profile == wiki_profile,
                "wiki brief refresh returned a different project or wiki profile binding"
            );
            Ok(PreparedContext {
                entry_ids: projection.entry_ids.clone(),
                projection,
                refresh_fallback_used: false,
            })
        }
        Ok(None) => bail!("wiki brief refresh returned no manifest"),
        Err(error) if require_fresh => Err(error).context("required wiki brief refresh failed"),
        Err(error) => fallback_prepared_context(
            profile,
            instance,
            &project.key,
            wiki_profile,
            wiki_context_agent_mode(agent_mode),
        )
        .with_context(|| {
            format!("wiki brief refresh failed and no exact fallback was available: {error}")
        }),
    }
}

fn fallback_prepared_context(
    profile: &str,
    instance: &Instance,
    project_key: &str,
    wiki_profile: &str,
    agent_mode: WikiContextAgentMode,
) -> Result<PreparedContext> {
    let store = WikiContextReceiptStore::read_only(profile)?;
    let receipt = store
        .history_for_session(&instance.id)?
        .into_iter()
        .rev()
        .find(|receipt| {
            receipt.project_key == project_key
                && receipt.wiki_profile == wiki_profile
                && receipt.agent_mode == agent_mode
                && matches!(
                    receipt.presentation_status,
                    WikiContextPresentationStatus::PreparedOnly
                        | WikiContextPresentationStatus::Presented
                        | WikiContextPresentationStatus::UnchangedNoMessage
                        | WikiContextPresentationStatus::DeferredBusy
                        | WikiContextPresentationStatus::NoEffect
                        | WikiContextPresentationStatus::AgentAcknowledged
                        | WikiContextPresentationStatus::OperatorVerified
                )
        })
        .context("no prior exact wiki context receipt")?;
    let root = PathBuf::from(&instance.project_path);
    let current = inspect_brief_strict(&root)?.context("wiki brief file is missing")?;
    ensure!(
        current.identity.project_root_identity == receipt.project_root_identity
            && current.identity.brief_file_sha256 == receipt.brief_file_sha256,
        "prior receipt does not bind the current wiki brief"
    );
    let entry_ids = parse_brief_entry_ids(&current.bytes);
    Ok(PreparedContext {
        entry_ids: entry_ids.clone(),
        projection: WikiContextProjectionV1 {
            project_key: receipt.project_key,
            wiki_profile: receipt.wiki_profile,
            project_root_identity: receipt.project_root_identity,
            agent_mode: receipt.agent_mode,
            projection_sha256: receipt.projection_sha256,
            brief_file_sha256: receipt.brief_file_sha256,
            entry_count: entry_ids.len(),
            entry_ids,
            stale_entry_count: 0,
            warnings: vec!["wiki refresh failed; using the last exact bound brief".to_string()],
        },
        refresh_fallback_used: true,
    })
}

async fn present_context(
    profile: &str,
    instance: &Instance,
    context: &PreparedContext,
    force_for_new_process: bool,
    wait_for_startup_prompt: bool,
) -> Result<WikiContextPresentationReceiptV1> {
    ensure_supported_tool(&instance.tool)?;
    context.projection.validate()?;
    let tool = agent_tool(&instance.tool)?;
    let store = WikiContextReceiptStore::writable(profile)?;
    let tmux_session = crate::tmux::Session::new(&instance.id, &instance.title)?;
    ensure!(
        tmux_session.exists(),
        "session is not running; no context input was sent"
    );
    let runtime_identity = tmux_session.runtime_identity()?;
    let runtime_identity_sha256 = sha256_hex(runtime_identity.as_bytes());
    let prepared_at = Utc::now();
    let prepared = receipt_for(
        instance,
        tool,
        &runtime_identity_sha256,
        &context.projection,
        prepared_at,
        ReceiptObservation::without_attempt(WikiContextPresentationStatus::PreparedOnly),
    )?;
    store.append(prepared.clone())?;

    let history = store.history_for_session(&instance.id)?;
    let prior_presented = history.iter().rev().find(|receipt| {
        receipt.receipt_id != prepared.receipt_id
            && receipt.project_root_identity == context.projection.project_root_identity
            && receipt.projection_sha256 == context.projection.projection_sha256
            && receipt.brief_file_sha256 == context.projection.brief_file_sha256
            && receipt.agent_mode == context.projection.agent_mode
            && receipt.session_runtime_identity_sha256.as_deref()
                == Some(runtime_identity_sha256.as_str())
            && matches!(
                receipt.presentation_status,
                WikiContextPresentationStatus::Presented
                    | WikiContextPresentationStatus::AgentAcknowledged
                    | WikiContextPresentationStatus::OperatorVerified
            )
    });
    if !force_for_new_process {
        if let Some(previous) = prior_presented {
            let pane = tmux_session.capture_pane(80)?;
            if acknowledgement_visible(
                &pane,
                &context.projection.projection_sha256,
                &context.entry_ids,
            ) && previous.presentation_status != WikiContextPresentationStatus::AgentAcknowledged
            {
                let mut acknowledged = previous.clone();
                acknowledged.receipt_id = new_receipt_id();
                acknowledged.presentation_status = WikiContextPresentationStatus::AgentAcknowledged;
                acknowledged.agent_acknowledgement = Some(WikiContextAgentAcknowledgementV1 {
                    challenge_token: context.projection.projection_sha256.clone(),
                    acknowledged_at: Utc::now(),
                });
                acknowledged.operator_verification = None;
                acknowledged.validate()?;
                store.append(acknowledged.clone())?;
                return Ok(acknowledged);
            }
            let unchanged = receipt_for(
                instance,
                tool,
                &runtime_identity_sha256,
                &context.projection,
                prepared_at,
                ReceiptObservation::without_attempt(
                    WikiContextPresentationStatus::UnchangedNoMessage,
                ),
            )?;
            store.append(unchanged.clone())?;
            return Ok(unchanged);
        }
    }

    verify_current_context(&instance.project_path, &context.projection)?;
    let first_pane = if wait_for_startup_prompt {
        wait_for_prompt(&tmux_session, &instance.tool, STARTUP_PROMPT_TIMEOUT).await?
    } else {
        Some(tmux_session.capture_pane(80)?)
    };
    let Some(first_pane) = first_pane else {
        return Ok(prepared);
    };
    let first_hash = sha256_hex(first_pane.as_bytes());
    if !crate::tmux::status_detection::wiki_context_prompt_ready(&first_pane, &instance.tool) {
        let deferred = receipt_for(
            instance,
            tool,
            &runtime_identity_sha256,
            &context.projection,
            prepared_at,
            ReceiptObservation {
                status: WikiContextPresentationStatus::DeferredBusy,
                attempted_at: None,
                pane_before_sha256: Some(first_hash),
                pane_after_sha256: None,
            },
        )?;
        store.append(deferred.clone())?;
        return Ok(deferred);
    }

    verify_current_context(&instance.project_path, &context.projection)?;
    let before = tmux_session.capture_pane(80)?;
    let before_hash = sha256_hex(before.as_bytes());
    if before_hash != first_hash
        || !crate::tmux::status_detection::wiki_context_prompt_ready(&before, &instance.tool)
    {
        let deferred = receipt_for(
            instance,
            tool,
            &runtime_identity_sha256,
            &context.projection,
            prepared_at,
            ReceiptObservation {
                status: WikiContextPresentationStatus::DeferredBusy,
                attempted_at: None,
                pane_before_sha256: Some(before_hash),
                pane_after_sha256: None,
            },
        )?;
        store.append(deferred.clone())?;
        return Ok(deferred);
    }

    let message = build_context_message(&context.projection);
    let attempted_at = Utc::now();
    ensure!(
        tmux_session.runtime_identity()? == runtime_identity,
        "session runtime changed before wiki context input"
    );
    tmux_session.send_literal_line(&message, &runtime_identity)?;
    let after = wait_for_pane_transition(&tmux_session, &before_hash).await?;
    ensure!(
        tmux_session.runtime_identity()? == runtime_identity,
        "session runtime changed during wiki context input"
    );
    verify_current_context(&instance.project_path, &context.projection)?;
    let after_hash = sha256_hex(after.as_bytes());
    let status = if after_hash == before_hash {
        WikiContextPresentationStatus::NoEffect
    } else {
        WikiContextPresentationStatus::Presented
    };
    let final_receipt = receipt_for(
        instance,
        tool,
        &runtime_identity_sha256,
        &context.projection,
        prepared_at,
        ReceiptObservation {
            status,
            attempted_at: Some(attempted_at),
            pane_before_sha256: Some(before_hash),
            pane_after_sha256: Some(after_hash),
        },
    )?;
    store.append(final_receipt.clone())?;
    Ok(final_receipt)
}

async fn wait_for_prompt(
    session: &crate::tmux::Session,
    tool: &str,
    timeout: Duration,
) -> Result<Option<String>> {
    let started = Instant::now();
    loop {
        let pane = session.capture_pane(80)?;
        if crate::tmux::status_detection::wiki_context_prompt_ready(&pane, tool) {
            return Ok(Some(pane));
        }
        if crate::tmux::detect_status_from_content(&pane, tool, None)
            == crate::session::Status::Waiting
        {
            return Ok(Some(pane));
        }
        if started.elapsed() >= timeout {
            return Ok(None);
        }
        sleep(POLL_INTERVAL).await;
    }
}

async fn wait_for_pane_transition(
    session: &crate::tmux::Session,
    before_hash: &str,
) -> Result<String> {
    let started = Instant::now();
    loop {
        let pane = session.capture_pane(80)?;
        if sha256_hex(pane.as_bytes()) != before_hash
            || started.elapsed() >= PANE_TRANSITION_TIMEOUT
        {
            return Ok(pane);
        }
        sleep(POLL_INTERVAL).await;
    }
}

fn receipt_for(
    instance: &Instance,
    agent_tool: WikiContextAgentTool,
    session_runtime_identity_sha256: &str,
    projection: &WikiContextProjectionV1,
    prepared_at: DateTime<Utc>,
    observation: ReceiptObservation,
) -> Result<WikiContextPresentationReceiptV1> {
    let presented_at =
        (observation.status == WikiContextPresentationStatus::Presented).then_some(Utc::now());
    let receipt = WikiContextPresentationReceiptV1 {
        schema: WIKI_CONTEXT_PRESENTATION_RECEIPT_SCHEMA.to_string(),
        receipt_id: new_receipt_id(),
        session_id: Some(instance.id.clone()),
        session_runtime_identity_sha256: Some(session_runtime_identity_sha256.to_string()),
        agent_tool,
        project_key: projection.project_key.clone(),
        wiki_profile: projection.wiki_profile.clone(),
        project_root_identity: projection.project_root_identity.clone(),
        agent_mode: projection.agent_mode,
        projection_sha256: projection.projection_sha256.clone(),
        brief_file_sha256: projection.brief_file_sha256.clone(),
        prepared_at,
        attempted_at: observation.attempted_at,
        presented_at,
        presentation_status: observation.status,
        pane_before_sha256: observation.pane_before_sha256,
        pane_after_sha256: observation.pane_after_sha256,
        agent_acknowledgement: None,
        operator_verification: None,
    };
    receipt.validate()?;
    Ok(receipt)
}

fn verify_current_context(project_path: &str, projection: &WikiContextProjectionV1) -> Result<()> {
    let current = inspect_brief_strict(Path::new(project_path))?
        .context("wiki brief disappeared before presentation")?;
    ensure!(
        current.identity.project_root_identity == projection.project_root_identity,
        "project root changed before wiki context presentation"
    );
    ensure!(
        current.identity.brief_file_sha256 == projection.brief_file_sha256,
        "wiki brief changed before presentation"
    );
    Ok(())
}

fn inspect_brief(
    project_root: &Path,
    warnings: &mut Vec<String>,
) -> Option<WikiBriefCurrentSnapshot> {
    match inspect_brief_strict(project_root) {
        Ok(snapshot) => snapshot,
        Err(error) => {
            warnings.push(format!("wiki brief unavailable: {error}"));
            None
        }
    }
}

fn inspect_brief_strict(project_root: &Path) -> Result<Option<WikiBriefCurrentSnapshot>> {
    WikiBriefOutputBoundary::open(&project_root.join(BRIEF_FILE))?.inspect_current()
}

fn exact_registered_project(project_root: &Path) -> Result<ProjectRegistryEntry> {
    let canonical = project_root
        .canonicalize()
        .context("canonicalize project root for registry resolution")?;
    resolve_unique_project_for_path(&canonical, &load_registry())?
        .context("session project is not registered for wiki context")
}

fn build_context_message(projection: &WikiContextProjectionV1) -> String {
    format!(
        "Forager context prepared. Read .wiki-brief.md before continuing. In your next reply, cite FORAGER_CONTEXT_ACK:{}:<one listed wiki_entry ID or entries=0>. This context is advisory; verify STALE or conflicting lines against current project evidence.",
        projection.projection_sha256
    )
}

fn acknowledgement_visible(pane: &str, projection_sha256: &str, entry_ids: &[String]) -> bool {
    let compact = pane
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect::<String>();
    let challenge = format!("FORAGER_CONTEXT_ACK:{projection_sha256}:");
    compact.match_indices(&challenge).count() >= 2
        && if entry_ids.is_empty() {
            compact.contains(&format!("{challenge}entries=0"))
        } else {
            entry_ids
                .iter()
                .any(|entry_id| compact.contains(&format!("{challenge}{entry_id}")))
        }
}

fn parse_brief_entry_ids(bytes: &[u8]) -> Vec<String> {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return Vec::new();
    };
    text.lines()
        .filter_map(|line| {
            let start = line.rfind("(wiki_entry_")? + 1;
            let end = line[start..].find(')')? + start;
            let id = &line[start..end];
            (id.len() <= 256
                && id
                    .chars()
                    .all(|character| character.is_ascii_alphanumeric() || "_-".contains(character)))
            .then(|| id.to_string())
        })
        .take(12)
        .collect()
}

fn ensure_supported_tool(tool: &str) -> Result<()> {
    ensure!(
        matches!(tool, "claude" | "codex"),
        "wiki context presentation currently supports exact claude or codex sessions"
    );
    Ok(())
}

fn agent_tool(tool: &str) -> Result<WikiContextAgentTool> {
    match tool {
        "claude" => Ok(WikiContextAgentTool::Claude),
        "codex" => Ok(WikiContextAgentTool::Codex),
        _ => bail!("unsupported wiki context agent tool: {tool}"),
    }
}

fn wiki_context_agent_mode(mode: Option<AdaptiveWikiAgentMode>) -> WikiContextAgentMode {
    match mode {
        None => WikiContextAgentMode::Shared,
        Some(AdaptiveWikiAgentMode::Planning) => WikiContextAgentMode::Planning,
        Some(AdaptiveWikiAgentMode::Development) => WikiContextAgentMode::Development,
        Some(AdaptiveWikiAgentMode::Analysis) => WikiContextAgentMode::Analysis,
        Some(AdaptiveWikiAgentMode::Writing) => WikiContextAgentMode::Writing,
        Some(AdaptiveWikiAgentMode::Critique) => WikiContextAgentMode::Critique,
        Some(AdaptiveWikiAgentMode::Review) => WikiContextAgentMode::Review,
        Some(AdaptiveWikiAgentMode::Maintenance) => WikiContextAgentMode::Maintenance,
    }
}

pub(crate) fn presentation_status_label(status: WikiContextPresentationStatus) -> &'static str {
    match status {
        WikiContextPresentationStatus::PreparedOnly => "prepared_only",
        WikiContextPresentationStatus::Presented => "presented",
        WikiContextPresentationStatus::UnchangedNoMessage => "unchanged_no_message",
        WikiContextPresentationStatus::DeferredBusy => "deferred_busy",
        WikiContextPresentationStatus::TargetAmbiguous => "target_ambiguous",
        WikiContextPresentationStatus::NoEffect => "no_effect",
        WikiContextPresentationStatus::AgentAcknowledged => "agent_acknowledged",
        WikiContextPresentationStatus::OperatorVerified => "operator_verified",
    }
}

fn present_status(status: &WikiContextSessionStatusV1) {
    println!("Wiki context: {}", status.session_title);
    println!("  Session:       {}", status.session_id);
    println!("  Tool:          {}", status.tool);
    println!("  Brief:         {}", status.brief_status);
    println!("  Presentation:  {}", status.presentation_status);
    println!(
        "  Fresh:         {}",
        if status.fresh_for_session {
            "yes"
        } else {
            "no"
        }
    );
    println!(
        "  Sync required: {}",
        if status.sync_required { "yes" } else { "no" }
    );
    if let Some(projection) = &status.projection_sha256 {
        println!("  Projection:    {projection}");
    }
    if status.acknowledgement_observed && status.presentation_status != "agent_acknowledged" {
        println!("  Acknowledged:  observed, run context-sync to record without resending");
    }
    for warning in &status.warnings {
        println!("  Warning:       {warning}");
    }
}

fn new_receipt_id() -> String {
    format!("wiki_context_receipt_{}", uuid::Uuid::new_v4())
}

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offdesk::WikiContextAgentMode;

    #[test]
    fn message_is_tool_independent_and_contains_only_projection_challenge() {
        let projection = WikiContextProjectionV1 {
            project_key: "demo".to_string(),
            wiki_profile: "demo-wiki".to_string(),
            project_root_identity: "r".repeat(64),
            agent_mode: WikiContextAgentMode::Shared,
            projection_sha256: "a".repeat(64),
            brief_file_sha256: "b".repeat(64),
            entry_ids: vec!["wiki_entry_one".to_string()],
            entry_count: 1,
            stale_entry_count: 0,
            warnings: Vec::new(),
        };
        let message = build_context_message(&projection);
        assert!(message.contains(&projection.projection_sha256));
        assert!(message.contains(".wiki-brief.md"));
        assert!(!message.contains("codex"));
        assert!(!message.contains("claude"));
        assert!(!message.contains("wiki_entry_one"));
        assert!(!message.contains('\n'));
    }

    #[test]
    fn acknowledgement_requires_hash_and_one_current_entry() {
        let entries = vec!["wiki_entry_one".to_string()];
        assert!(!acknowledgement_visible(
            "FORAGER_CONTEXT_ACK:hash: prompt only",
            "hash",
            &entries
        ));
        assert!(!acknowledgement_visible(
            "wiki_entry_one only",
            "hash",
            &entries
        ));
        assert!(!acknowledgement_visible(
            "FORAGER_CONTEXT_ACK:hash:<entry>; repeated FORAGER_CONTEXT_ACK:hash:<entry>; old wiki_entry_one",
            "hash",
            &entries
        ));
        assert!(acknowledgement_visible(
            "FORAGER_CONTEXT_ACK:hash:<entry>; reply FORAGER_CONTEXT_ACK:hash:wiki_entry_one",
            "hash",
            &entries
        ));
        assert!(!acknowledgement_visible(
            "FORAGER_CONTEXT_ACK:hash:entries=0",
            "hash",
            &[]
        ));
        assert!(acknowledgement_visible(
            "FORAGER_CONTEXT_ACK:hash:entries=0; reply FORAGER_CONTEXT_ACK:hash:entries=0",
            "hash",
            &[]
        ));
    }

    #[test]
    fn parses_only_bounded_canonical_entry_ids() {
        let ids = parse_brief_entry_ids(
            b"- first (wiki_entry_one)\n- second (wiki_entry_two)\n- no (candidate_three)\n",
        );
        assert_eq!(ids, vec!["wiki_entry_one", "wiki_entry_two"]);
    }
}
