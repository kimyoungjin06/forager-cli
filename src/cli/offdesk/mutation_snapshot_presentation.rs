//! Offdesk mutation-snapshot presentation.
//!
//! Command handlers retain snapshot-store reads, live verification, and
//! restore-plan construction. This module only builds the list read model and
//! renders completed results through the final operator-safe boundary.

use anyhow::{bail, Result};
use chrono::{DateTime, Utc};
use serde::Serialize;

use super::operator_safe_json_value;
use crate::offdesk::{
    operator_safe_text, MutationRestoreOperation, MutationRestorePlan, MutationSnapshot,
    MutationSnapshotVerification,
};

#[derive(Serialize)]
pub(super) struct MutationSnapshotListItem {
    mutation_id: String,
    target_path: String,
    mutation_kind: String,
    created_at: DateTime<Utc>,
    rollback_available: bool,
    blockers: Vec<String>,
}

pub(super) fn mutation_snapshot_list_item(
    snapshot: MutationSnapshot,
    verification: MutationSnapshotVerification,
) -> MutationSnapshotListItem {
    MutationSnapshotListItem {
        mutation_id: snapshot.mutation_id,
        target_path: snapshot.target_path,
        mutation_kind: snapshot.mutation_kind,
        created_at: snapshot.created_at,
        rollback_available: verification.rollback_available,
        blockers: verification.blockers,
    }
}

pub(super) fn present_mutation_snapshot_list(
    items: &[MutationSnapshotListItem],
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(items)?;
        return Ok(());
    }

    if items.is_empty() {
        println!("No mutation snapshots found.");
        return Ok(());
    }

    println!(
        "{:<44} {:<14} {:<9} TARGET",
        "MUTATION ID", "KIND", "ROLLBACK"
    );
    for item in items {
        println!(
            "{:<44} {:<14} {:<9} {}",
            operator_safe_text(&item.mutation_id),
            operator_safe_text(&item.mutation_kind),
            if item.rollback_available { "yes" } else { "no" },
            operator_safe_text(&item.target_path)
        );
        if !item.blockers.is_empty() {
            let blockers = item
                .blockers
                .iter()
                .map(|blocker| operator_safe_text(blocker))
                .collect::<Vec<_>>();
            println!("  blockers: {}", blockers.join("; "));
        }
    }
    Ok(())
}

pub(super) fn present_mutation_snapshot_verification(
    verification: &MutationSnapshotVerification,
    json: bool,
) -> Result<()> {
    if json {
        present_operator_safe_json(verification)?;
        return Ok(());
    }

    let Some(snapshot) = verification.snapshot.as_ref() else {
        return present_missing_mutation_snapshot(&verification.mutation_id);
    };
    println!("Snapshot {}", operator_safe_text(&snapshot.mutation_id));
    println!(
        "  target:              {}",
        operator_safe_text(&snapshot.target_path)
    );
    println!(
        "  mutation_kind:       {}",
        operator_safe_text(&snapshot.mutation_kind)
    );
    println!("  rollback_available:  {}", verification.rollback_available);
    println!(
        "  target_exists_now:   {}",
        verification
            .target_exists_now
            .map(|exists| exists.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
    println!(
        "  target_matches_before: {}",
        verification
            .target_current_matches_before
            .map(|matches| matches.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
    if let Some(path) = verification.before_snapshot_path.as_deref() {
        println!("  before_snapshot:     {}", operator_safe_text(path));
    }
    if !verification.blockers.is_empty() {
        let blockers = verification
            .blockers
            .iter()
            .map(|blocker| operator_safe_text(blocker))
            .collect::<Vec<_>>();
        println!("  blockers:            {}", blockers.join("; "));
    }
    Ok(())
}

pub(super) fn present_mutation_restore_plan(plan: &MutationRestorePlan, json: bool) -> Result<()> {
    if json {
        present_operator_safe_json(plan)?;
        return Ok(());
    }

    println!("Restore plan {}", operator_safe_text(&plan.mutation_id));
    println!(
        "  target:             {}",
        operator_safe_text(&plan.target_path)
    );
    println!("  operation:          {:?}", plan.operation);
    println!("  rollback_available: {}", plan.rollback_available);
    match plan.operation {
        MutationRestoreOperation::RestoreFile => {
            if let Some(path) = plan.before_snapshot_path.as_deref() {
                println!("  source:             {}", operator_safe_text(path));
            } else {
                println!("  source:             empty file");
            }
        }
        MutationRestoreOperation::DeleteFile => {
            println!("  source:             target did not exist before mutation");
        }
        MutationRestoreOperation::Unavailable => {}
    }
    if !plan.blockers.is_empty() {
        let blockers = plan
            .blockers
            .iter()
            .map(|blocker| operator_safe_text(blocker))
            .collect::<Vec<_>>();
        println!("  blockers:           {}", blockers.join("; "));
    }
    Ok(())
}

pub(super) fn present_missing_mutation_snapshot(mutation_id: &str) -> Result<()> {
    bail!(
        "Mutation snapshot not found: {}",
        operator_safe_text(mutation_id)
    )
}

fn present_operator_safe_json(value: &(impl Serialize + ?Sized)) -> Result<()> {
    let value = operator_safe_json_value(serde_json::to_value(value)?);
    println!("{}", serde_json::to_string_pretty(&value)?);
    Ok(())
}
