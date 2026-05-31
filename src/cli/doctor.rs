//! `forager doctor` diagnostics for rename and path migration readiness.

use anyhow::Result;
use clap::Args;
use serde::Serialize;
use std::path::{Path, PathBuf};

use crate::session::{app_dir_resolution, normalize_profile_name, repo_config, DEFAULT_PROFILE};

#[derive(Args)]
pub struct DoctorArgs {
    /// Repository path to inspect for .forager/.aoe config
    #[arg(long, default_value = ".", value_name = "PATH")]
    project: PathBuf,

    /// Output as JSON
    #[arg(long)]
    json: bool,
}

#[derive(Serialize)]
struct DoctorReport {
    profile: ProfileReport,
    global_data: GlobalDataReport,
    repo_config: RepoConfigReport,
    env: EnvReport,
}

#[derive(Serialize)]
struct ProfileReport {
    active: String,
    source: &'static str,
    dir: String,
    dir_source: &'static str,
    primary_dir: String,
    primary_exists: bool,
    legacy_dirs: Vec<PathExistence>,
}

#[derive(Serialize)]
struct EnvReport {
    forager_profile_set: bool,
    legacy_profile_set: bool,
    forager_debug_set: bool,
    legacy_debug_set: bool,
}

#[derive(Serialize)]
struct GlobalDataReport {
    active_path: String,
    active_source: &'static str,
    primary_path: String,
    primary_exists: bool,
    legacy_paths: Vec<PathExistence>,
}

#[derive(Serialize)]
struct RepoConfigReport {
    project_path: String,
    active_path: Option<String>,
    active_source: &'static str,
    primary_path: String,
    primary_exists: bool,
    legacy_path: String,
    legacy_exists: bool,
}

#[derive(Serialize)]
struct PathExistence {
    path: String,
    exists: bool,
}

pub async fn run(cli_profile: Option<&str>, args: DoctorArgs) -> Result<()> {
    let report = build_report(cli_profile, &args.project)?;

    if args.json {
        println!("{}", serde_json::to_string_pretty(&report)?);
    } else {
        print_human(&report);
    }

    Ok(())
}

fn build_report(cli_profile: Option<&str>, project_path: &Path) -> Result<DoctorReport> {
    let env = EnvReport {
        forager_profile_set: std::env::var_os("FORAGER_PROFILE").is_some(),
        legacy_profile_set: std::env::var_os("AGENT_OF_EMPIRES_PROFILE").is_some(),
        forager_debug_set: std::env::var_os("FORAGER_DEBUG").is_some(),
        legacy_debug_set: std::env::var_os("AGENT_OF_EMPIRES_DEBUG").is_some(),
    };

    let legacy_profile = std::env::var("AGENT_OF_EMPIRES_PROFILE").ok();
    let profile = if let Some(profile) = cli_profile {
        ProfileReport {
            active: normalize_profile_name(profile)?,
            source: "--profile/FORAGER_PROFILE",
            dir: String::new(),
            dir_source: "",
            primary_dir: String::new(),
            primary_exists: false,
            legacy_dirs: Vec::new(),
        }
    } else if let Some(profile) = legacy_profile {
        ProfileReport {
            active: normalize_profile_name(&profile)?,
            source: "AGENT_OF_EMPIRES_PROFILE",
            dir: String::new(),
            dir_source: "",
            primary_dir: String::new(),
            primary_exists: false,
            legacy_dirs: Vec::new(),
        }
    } else {
        ProfileReport {
            active: DEFAULT_PROFILE.to_string(),
            source: "default",
            dir: String::new(),
            dir_source: "",
            primary_dir: String::new(),
            primary_exists: false,
            legacy_dirs: Vec::new(),
        }
    };

    let app_dir = app_dir_resolution()?;
    let legacy_paths: Vec<PathExistence> = app_dir
        .legacy_paths
        .iter()
        .map(|path| PathExistence {
            exists: path.exists(),
            path: display_path(path),
        })
        .collect();
    let primary_profile_dir = app_dir.primary_path.join("profiles").join(&profile.active);
    let active_profile_dir = app_dir.active_path.join("profiles").join(&profile.active);
    let legacy_profile_dirs = app_dir
        .legacy_paths
        .iter()
        .map(|path| path.join("profiles").join(&profile.active))
        .map(|path| PathExistence {
            exists: path.exists(),
            path: display_path(path),
        })
        .collect::<Vec<_>>();
    let profile = ProfileReport {
        dir: display_path(active_profile_dir),
        dir_source: app_dir.active_source,
        primary_dir: display_path(&primary_profile_dir),
        primary_exists: primary_profile_dir.exists(),
        legacy_dirs: legacy_profile_dirs,
        ..profile
    };

    let project_path = normalize_project_path(project_path)?;
    let primary_repo_config = repo_config::primary_repo_config_path(&project_path);
    let legacy_repo_config = repo_config::legacy_repo_config_path(&project_path);
    let primary_repo_exists = primary_repo_config.exists();
    let legacy_repo_exists = legacy_repo_config.exists();
    let (active_repo_path, active_repo_source) = if primary_repo_exists {
        (Some(display_path(&primary_repo_config)), "primary")
    } else if legacy_repo_exists {
        (Some(display_path(&legacy_repo_config)), "legacy")
    } else {
        (None, "none")
    };

    Ok(DoctorReport {
        profile,
        global_data: GlobalDataReport {
            active_path: display_path(&app_dir.active_path),
            active_source: app_dir.active_source,
            primary_path: display_path(&app_dir.primary_path),
            primary_exists: app_dir.primary_exists,
            legacy_paths,
        },
        repo_config: RepoConfigReport {
            project_path: display_path(project_path),
            active_path: active_repo_path,
            active_source: active_repo_source,
            primary_path: display_path(primary_repo_config),
            primary_exists: primary_repo_exists,
            legacy_path: display_path(legacy_repo_config),
            legacy_exists: legacy_repo_exists,
        },
        env,
    })
}

fn normalize_project_path(path: &Path) -> Result<PathBuf> {
    if path == Path::new(".") {
        return Ok(std::env::current_dir()?);
    }
    Ok(path.canonicalize()?)
}

fn display_path(path: impl AsRef<Path>) -> String {
    path.as_ref().display().to_string()
}

fn print_human(report: &DoctorReport) {
    println!("Forager doctor\n");

    println!("Profile");
    println!(
        "  active: {} ({})",
        report.profile.active, report.profile.source
    );
    println!(
        "  dir:    {} ({})",
        report.profile.dir, report.profile.dir_source
    );
    println!(
        "  primary dir: {} ({})",
        report.profile.primary_dir,
        exists_label(report.profile.primary_exists)
    );
    for legacy in &report.profile.legacy_dirs {
        println!(
            "  legacy dir: {} ({})",
            legacy.path,
            exists_label(legacy.exists)
        );
    }
    println!(
        "  env: FORAGER_PROFILE={} AGENT_OF_EMPIRES_PROFILE={}",
        set_label(report.env.forager_profile_set),
        set_label(report.env.legacy_profile_set)
    );
    if report.profile.dir_source == "legacy" {
        println!(
            "  hint: this profile is currently loaded from legacy AoE storage; run `forager migrate aoe` after reviewing conflicts."
        );
    }

    println!("\nGlobal data");
    println!(
        "  active: {} ({})",
        report.global_data.active_path, report.global_data.active_source
    );
    println!(
        "  primary: {} ({})",
        report.global_data.primary_path,
        exists_label(report.global_data.primary_exists)
    );
    for legacy in &report.global_data.legacy_paths {
        println!(
            "  legacy: {} ({})",
            legacy.path,
            exists_label(legacy.exists)
        );
    }
    if report.global_data.active_source == "legacy" {
        println!(
            "  hint: existing legacy data is still in use for compatibility; primary Forager data will take precedence after migration."
        );
    }

    println!("\nRepository config");
    println!("  project: {}", report.repo_config.project_path);
    match &report.repo_config.active_path {
        Some(path) => println!("  active: {} ({})", path, report.repo_config.active_source),
        None => println!("  active: none"),
    }
    println!(
        "  primary: {} ({})",
        report.repo_config.primary_path,
        exists_label(report.repo_config.primary_exists)
    );
    println!(
        "  legacy: {} ({})",
        report.repo_config.legacy_path,
        exists_label(report.repo_config.legacy_exists)
    );
    if report.repo_config.active_source == "legacy" {
        println!("  hint: .aoe/config.toml is still in use for compatibility.");
    }
}

fn set_label(set: bool) -> &'static str {
    if set {
        "set"
    } else {
        "unset"
    }
}

fn exists_label(exists: bool) -> &'static str {
    if exists {
        "exists"
    } else {
        "missing"
    }
}
