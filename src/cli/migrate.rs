//! `forager migrate` compatibility migrations.

use anyhow::{bail, Context, Result};
use clap::{Args, Subcommand};
use serde::Serialize;
use std::fs;
use std::path::{Path, PathBuf};

use crate::session::{legacy_app_dir_paths, primary_app_dir_path, repo_config};

#[derive(Subcommand)]
pub enum MigrateCommands {
    /// Copy legacy AoE paths into Forager primary paths
    Aoe(AoeMigrationArgs),
}

#[derive(Args)]
pub struct AoeMigrationArgs {
    /// Repository path to inspect for .aoe/.forager config
    #[arg(long, default_value = ".", value_name = "PATH")]
    project: PathBuf,

    /// Show the migration plan without copying files
    #[arg(long)]
    dry_run: bool,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
struct MigrationReport {
    migration: &'static str,
    mode: &'static str,
    dry_run: bool,
    has_conflicts: bool,
    operations: Vec<MigrationOperation>,
}

#[derive(Clone, Serialize)]
struct MigrationOperation {
    scope: &'static str,
    action: &'static str,
    status: &'static str,
    reason: Option<&'static str>,
    sources: Vec<String>,
    target: String,
}

#[derive(Clone, Copy)]
enum CopyKind {
    Directory,
    File,
}

struct PlannedOperation {
    report: MigrationOperation,
    copy_kind: Option<CopyKind>,
}

pub async fn run(command: MigrateCommands) -> Result<()> {
    match command {
        MigrateCommands::Aoe(args) => run_aoe(args).await,
    }
}

async fn run_aoe(args: AoeMigrationArgs) -> Result<()> {
    let mut plan = build_aoe_plan(&args.project, args.dry_run)?;
    let has_conflicts = plan
        .iter()
        .any(|operation| operation.report.status == "conflict");

    if has_conflicts {
        mark_pending_as_blocked(&mut plan);
    } else if !args.dry_run {
        apply_plan(&mut plan)?;
    }

    let report = MigrationReport {
        migration: "aoe",
        mode: "copy",
        dry_run: args.dry_run,
        has_conflicts,
        operations: plan
            .iter()
            .map(|operation| operation.report.clone())
            .collect(),
    };

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print_human(&report);
    }

    if has_conflicts {
        bail!("migration has conflicts; no changes were applied");
    }

    Ok(())
}

fn mark_pending_as_blocked(plan: &mut [PlannedOperation]) {
    for operation in plan {
        if operation.report.status == "pending" {
            operation.report.status = "blocked";
            operation.report.reason = Some("conflict_in_plan");
        }
    }
}

fn build_aoe_plan(project_path: &Path, dry_run: bool) -> Result<Vec<PlannedOperation>> {
    Ok(vec![
        plan_global_data_migration(dry_run)?,
        plan_repo_config_migration(project_path, dry_run)?,
    ])
}

fn plan_global_data_migration(dry_run: bool) -> Result<PlannedOperation> {
    let target = primary_app_dir_path()?;
    let sources: Vec<PathBuf> = legacy_app_dir_paths()
        .into_iter()
        .filter(|path| path.exists())
        .collect();

    let (status, reason, copy_kind) = if sources.is_empty() {
        ("skipped", Some("legacy_missing"), None)
    } else if target.exists() {
        ("conflict", Some("target_exists"), None)
    } else if sources.len() > 1 {
        ("conflict", Some("multiple_legacy_sources"), None)
    } else if dry_run {
        ("would_copy", None, Some(CopyKind::Directory))
    } else {
        ("pending", None, Some(CopyKind::Directory))
    };

    Ok(PlannedOperation {
        report: MigrationOperation {
            scope: "global_data",
            action: "copy_directory",
            status,
            reason,
            sources: display_paths(&sources),
            target: display_path(&target),
        },
        copy_kind,
    })
}

fn plan_repo_config_migration(project_path: &Path, dry_run: bool) -> Result<PlannedOperation> {
    let project_path = normalize_project_path(project_path)?;
    let source = repo_config::legacy_repo_config_path(&project_path);
    let target = repo_config::primary_repo_config_path(&project_path);

    let (status, reason, copy_kind) = if !source.exists() {
        ("skipped", Some("legacy_missing"), None)
    } else if target.exists() {
        ("conflict", Some("target_exists"), None)
    } else if dry_run {
        ("would_copy", None, Some(CopyKind::File))
    } else {
        ("pending", None, Some(CopyKind::File))
    };

    Ok(PlannedOperation {
        report: MigrationOperation {
            scope: "repo_config",
            action: "copy_file",
            status,
            reason,
            sources: vec![display_path(&source)],
            target: display_path(&target),
        },
        copy_kind,
    })
}

fn apply_plan(plan: &mut [PlannedOperation]) -> Result<()> {
    for operation in plan {
        let Some(copy_kind) = operation.copy_kind else {
            continue;
        };
        let source = operation
            .report
            .sources
            .first()
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("missing migration source"))?;
        let target = PathBuf::from(&operation.report.target);

        match copy_kind {
            CopyKind::Directory => copy_dir_recursive(&source, &target)?,
            CopyKind::File => copy_file(&source, &target)?,
        }

        operation.report.status = "copied";
    }

    Ok(())
}

fn copy_file(source: &Path, target: &Path) -> Result<()> {
    if target.exists() {
        bail!("target already exists: {}", target.display());
    }
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create {}", parent.display()))?;
    }
    fs::copy(source, target).with_context(|| {
        format!(
            "Failed to copy {} to {}",
            source.display(),
            target.display()
        )
    })?;
    Ok(())
}

fn copy_dir_recursive(source: &Path, target: &Path) -> Result<()> {
    if target.exists() {
        bail!("target already exists: {}", target.display());
    }
    copy_path(source, target)
}

fn copy_path(source: &Path, target: &Path) -> Result<()> {
    let metadata = fs::symlink_metadata(source)
        .with_context(|| format!("Failed to inspect {}", source.display()))?;
    if metadata.file_type().is_symlink() {
        copy_symlink(source, target)?;
    } else if metadata.is_dir() {
        fs::create_dir_all(target)
            .with_context(|| format!("Failed to create {}", target.display()))?;
        for entry in
            fs::read_dir(source).with_context(|| format!("Failed to read {}", source.display()))?
        {
            let entry = entry?;
            copy_path(&entry.path(), &target.join(entry.file_name()))?;
        }
    } else if metadata.is_file() {
        copy_file(source, target)?;
    } else {
        bail!("unsupported file type: {}", source.display());
    }
    Ok(())
}

#[cfg(unix)]
fn copy_symlink(source: &Path, target: &Path) -> Result<()> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create {}", parent.display()))?;
    }
    let link_target = fs::read_link(source)
        .with_context(|| format!("Failed to read symlink {}", source.display()))?;
    std::os::unix::fs::symlink(&link_target, target).with_context(|| {
        format!(
            "Failed to copy symlink {} to {}",
            source.display(),
            target.display()
        )
    })?;
    Ok(())
}

#[cfg(windows)]
fn copy_symlink(source: &Path, target: &Path) -> Result<()> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("Failed to create {}", parent.display()))?;
    }
    let link_target = fs::read_link(source)
        .with_context(|| format!("Failed to read symlink {}", source.display()))?;
    let metadata = fs::metadata(source)
        .with_context(|| format!("Failed to inspect symlink target {}", source.display()))?;
    if metadata.is_dir() {
        std::os::windows::fs::symlink_dir(&link_target, target)
    } else {
        std::os::windows::fs::symlink_file(&link_target, target)
    }
    .with_context(|| {
        format!(
            "Failed to copy symlink {} to {}",
            source.display(),
            target.display()
        )
    })?;
    Ok(())
}

fn normalize_project_path(path: &Path) -> Result<PathBuf> {
    if path == Path::new(".") {
        return Ok(std::env::current_dir()?);
    }
    Ok(path.canonicalize()?)
}

fn display_paths(paths: &[PathBuf]) -> Vec<String> {
    paths.iter().map(display_path).collect()
}

fn display_path(path: impl AsRef<Path>) -> String {
    path.as_ref().display().to_string()
}

fn print_human(report: &MigrationReport) {
    println!("Forager AoE migration");
    println!("  mode: {}", report.mode);
    println!("  dry-run: {}", if report.dry_run { "yes" } else { "no" });
    println!();

    for operation in &report.operations {
        println!(
            "{}: {}{}",
            operation.scope,
            operation.status,
            operation
                .reason
                .map(|reason| format!(" ({reason})"))
                .unwrap_or_default()
        );
        if operation.sources.is_empty() {
            println!("  source: none");
        } else {
            for source in &operation.sources {
                println!("  source: {source}");
            }
        }
        println!("  target: {}", operation.target);
    }

    if report.has_conflicts {
        println!("\nResolve conflicts and rerun the command. No changes were applied.");
    } else if report.dry_run {
        println!("\nDry run only. Rerun without --dry-run to copy legacy paths.");
    } else {
        println!("\nLegacy paths were preserved as backups.");
    }
}
