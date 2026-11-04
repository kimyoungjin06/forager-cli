//! CLI projection for approval-bound document lifecycle operations.

use anyhow::{Context, Result};
use chrono::Utc;
use clap::{Args, ValueEnum};
use std::path::PathBuf;

use crate::document_lifecycle::{
    apply_document_lifecycle, document_lifecycle_history, request_document_lifecycle,
    review_document_lifecycle, ActivityState, CatalogOptions, DocumentLifecycleReviewInput,
    LifecycleTransitionV1, StrictUtcTimestamp,
};
use crate::session::{get_profile_dir, profile_dir_path};

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum LifecycleTransitionArg {
    MintDocument,
    ActivityTransition,
    RevalidateObservation,
    RebindObservation,
}

impl From<LifecycleTransitionArg> for LifecycleTransitionV1 {
    fn from(value: LifecycleTransitionArg) -> Self {
        match value {
            LifecycleTransitionArg::MintDocument => Self::MintDocument,
            LifecycleTransitionArg::ActivityTransition => Self::ActivityTransition,
            LifecycleTransitionArg::RevalidateObservation => Self::RevalidateObservation,
            LifecycleTransitionArg::RebindObservation => Self::RebindObservation,
        }
    }
}

#[derive(Debug, Clone, Copy, ValueEnum)]
pub enum LifecycleActivityArg {
    Draft,
    Candidate,
    Active,
    ReviewDue,
    Superseded,
    Retired,
}

impl From<LifecycleActivityArg> for ActivityState {
    fn from(value: LifecycleActivityArg) -> Self {
        match value {
            LifecycleActivityArg::Draft => Self::Draft,
            LifecycleActivityArg::Candidate => Self::Candidate,
            LifecycleActivityArg::Active => Self::Active,
            LifecycleActivityArg::ReviewDue => Self::ReviewDue,
            LifecycleActivityArg::Superseded => Self::Superseded,
            LifecycleActivityArg::Retired => Self::Retired,
        }
    }
}

#[derive(Debug, Clone, Args)]
pub struct LifecycleProposalArgs {
    /// One explicit bounded project root
    path: PathBuf,

    /// Stable project key bound into the proposal and ledger
    #[arg(long)]
    project_key: String,

    /// Exact current observation selector, required for mint
    #[arg(long)]
    observation_id: Option<String>,

    /// Durable document selector, required for an existing document
    #[arg(long)]
    document_id: Option<String>,

    /// Lifecycle event class to review
    #[arg(value_enum)]
    transition: LifecycleTransitionArg,

    /// Reviewed activity for mint and activity transitions
    #[arg(long, value_enum)]
    activity_state: Option<LifecycleActivityArg>,

    /// Exact active successor for a superseded document
    #[arg(long)]
    successor_document_id: Option<String>,

    /// Exact active owner for this document
    #[arg(long)]
    active_owner_document_id: Option<String>,

    /// Canonical UTC review deadline with a Z suffix
    #[arg(long)]
    review_after: Option<String>,

    /// Operator-safe reason bound into the proposal hash
    #[arg(long)]
    reason: String,
}

impl LifecycleProposalArgs {
    fn review_input(&self) -> Result<DocumentLifecycleReviewInput> {
        Ok(DocumentLifecycleReviewInput {
            project_key: self.project_key.clone(),
            observation_id: self.observation_id.clone(),
            document_id: self.document_id.clone(),
            transition: self.transition.into(),
            activity_state: self.activity_state.map(Into::into),
            successor_document_id: self.successor_document_id.clone(),
            active_owner_document_id: self.active_owner_document_id.clone(),
            review_after: self
                .review_after
                .as_deref()
                .map(StrictUtcTimestamp::parse)
                .transpose()?,
            reason: self.reason.clone(),
            catalog_options: CatalogOptions::default(),
        })
    }
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentLifecycleReviewArgs {
    #[command(flatten)]
    proposal: LifecycleProposalArgs,

    /// Output strict machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentLifecycleRequestArgs {
    #[command(flatten)]
    proposal: LifecycleProposalArgs,

    /// Exact proposal hash returned by a fresh review
    #[arg(long)]
    expected_proposal_sha256: String,

    /// Optional bounded request identity
    #[arg(long)]
    request_id: Option<String>,

    /// Optional bounded task identity
    #[arg(long)]
    task_id: Option<String>,

    /// Output strict machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentLifecycleApplyArgs {
    /// One explicit bounded project root
    path: PathBuf,

    /// Approval ID from the active profile's canonical approval ledger
    #[arg(long)]
    approval_id: String,

    /// Output strict machine-readable JSON
    #[arg(long)]
    json: bool,
}

#[derive(Debug, Clone, Args)]
pub struct ProjectDocumentLifecycleHistoryArgs {
    /// One explicit bounded project root
    path: PathBuf,

    /// Stable project key bound into the lifecycle ledger
    #[arg(long)]
    project_key: String,

    /// Exact durable document ID
    #[arg(long)]
    document_id: String,

    /// Output strict machine-readable JSON
    #[arg(long)]
    json: bool,
}

pub fn run_review(profile: &str, args: ProjectDocumentLifecycleReviewArgs) -> Result<()> {
    let profile_root = profile_dir_path(profile)?;
    let input = args.proposal.review_input()?;
    let proposal = review_document_lifecycle(&profile_root, &args.proposal.path, &input)?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&proposal)?);
    } else {
        println!("Status: proposal ready");
        println!("Target: {}", proposal.payload.observation.relative_path);
        println!(
            "State: {:?} -> {:?}",
            proposal
                .payload
                .before
                .as_ref()
                .map(|state| state.activity_state),
            proposal.payload.after.activity_state
        );
        println!("Evidence fresh: true");
        println!("Proposal: {}", proposal.proposal_sha256);
        println!(
            "Approval request authorized: {}",
            proposal.authority_boundary.approval_request_authorized
        );
        println!("File mutation: not authorized");
    }
    Ok(())
}

pub fn run_request(profile: &str, args: ProjectDocumentLifecycleRequestArgs) -> Result<()> {
    let profile_root = get_profile_dir(profile)?;
    let input = args.proposal.review_input()?;
    let result = request_document_lifecycle(
        &profile_root,
        &args.proposal.path,
        &input,
        &args.expected_proposal_sha256,
        args.request_id.as_deref(),
        args.task_id.as_deref(),
        Utc::now(),
    )?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else {
        println!("Status: {:?}", result.status);
        println!("Proposal: {}", result.proposal_sha256);
        println!("Approval: {}", result.approval_id);
        println!("Reused existing: {}", result.reused_existing);
        println!("File mutation: not authorized");
    }
    Ok(())
}

pub fn run_apply(profile: &str, args: ProjectDocumentLifecycleApplyArgs) -> Result<()> {
    let profile_root = get_profile_dir(profile)?;
    let result =
        apply_document_lifecycle(&profile_root, &args.path, &args.approval_id, Utc::now())?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&result)?);
    } else {
        println!("Status: applied");
        println!("Target: {}", result.event.payload.observation.relative_path);
        println!(
            "State: {:?} -> {:?}",
            result
                .event
                .payload
                .before
                .as_ref()
                .map(|state| state.activity_state),
            result.event.payload.after.activity_state
        );
        println!("Evidence fresh: true");
        println!("Approval: {}", result.approval_id);
        println!("Event: {}", result.event.payload.event_id);
        println!("Reused existing: {}", result.reused_existing);
        println!("File mutation: not authorized");
    }
    Ok(())
}

pub fn run_history(profile: &str, args: ProjectDocumentLifecycleHistoryArgs) -> Result<()> {
    let profile_root = profile_dir_path(profile)?;
    let history = document_lifecycle_history(
        &profile_root,
        &args.path,
        &args.project_key,
        &args.document_id,
        Utc::now(),
    )
    .context("load document lifecycle history")?;
    if args.json {
        println!("{}", serde_json::to_string_pretty(&history)?);
    } else {
        println!("Status: {:?}", history.ledger_status);
        println!("Target: {}", history.document_id);
        println!("Events: {}", history.events.len());
        for event in &history.events {
            println!(
                "- {} {:?}: {:?} -> {:?}",
                event.payload.event_id,
                event.payload.event_kind,
                event
                    .payload
                    .before
                    .as_ref()
                    .map(|state| state.activity_state),
                event.payload.after.activity_state
            );
        }
        println!("Document bodies: not included");
        println!("File mutation: not authorized");
    }
    Ok(())
}
