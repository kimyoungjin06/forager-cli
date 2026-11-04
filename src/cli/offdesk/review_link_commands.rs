use super::*;
use crate::offdesk::workflow::review_link::{self, ReviewLinkRequest};

#[derive(Args)]
pub struct ReviewLinkArgs {
    /// Exact request binding the runtime, result and existing committed decision
    request: PathBuf,
    /// Confirm only this review link, without accepting output or starting work
    #[arg(long, requires_all = ["expected_preview_sha256", "by"])]
    confirm: bool,
    #[arg(long)]
    expected_preview_sha256: Option<String>,
    #[arg(long)]
    by: Option<String>,
}

pub(super) fn run(profile: &str, args: ReviewLinkArgs) -> Result<()> {
    let bytes = review_link::read_artifact(&args.request)?;
    let request: ReviewLinkRequest = serde_json::from_slice(&bytes)?;
    let root = crate::session::profile_dir_path(profile)?;
    ensure!(root.is_dir(), "review-link requires an existing profile");
    let store = OffdeskTaskStore::new(&root);
    let tasks = store.load()?;
    let selected: Vec<_> = tasks
        .iter()
        .filter(|task| task.task_id == request.task_id)
        .collect();
    ensure!(selected.len() == 1, "review task missing or ambiguous");
    let preview = review_link::preview(&root, selected[0], &request)?;
    let hash = preview.sha256()?;
    let outcome = if args.confirm {
        store.link_review(
            &request,
            args.expected_preview_sha256
                .as_deref()
                .context("review hash required")?,
            args.by.as_deref().context("recorder required")?,
            Utc::now(),
        )?
    } else {
        if let Some(expected) = &args.expected_preview_sha256 {
            ensure!(*expected == hash, "review preview changed");
        }
        "preview"
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&operator_safe_json_value(serde_json::json!({
            "schema": "offdesk_review_link_report.v1", "outcome": outcome,
            "preview_sha256": hash, "preview": preview,
            "does_not_authorize": ["accept_science", "launch", "file_operations", "rewrite_historical_result", "deployment"],
            "compatibility": "Confirmed ordinary links use queue v2; historical-boundary links use queue v3; acknowledged-prefix links use queue v4; explicit narrative reconciliations use queue v5. Older readers refuse unsupported queues; qualify the selected consumers before use."
        })))?
    );
    Ok(())
}
