//! `forager init` command implementation

use anyhow::{bail, Result};
use clap::Args;
use std::fs;
use std::path::PathBuf;

use crate::session::repo_config::{
    existing_repo_config_path, primary_repo_config_path, INIT_TEMPLATE,
};

#[derive(Args)]
pub struct InitArgs {
    /// Directory to initialize (defaults to current directory)
    #[arg(default_value = ".")]
    path: PathBuf,
}

pub async fn run(args: InitArgs) -> Result<()> {
    let path = if args.path.as_os_str() == "." {
        std::env::current_dir()?
    } else {
        args.path.canonicalize()?
    };

    if let Some(config_path) = existing_repo_config_path(&path) {
        bail!(
            "Repository config already exists at {}\nEdit it directly to make changes.",
            config_path.display()
        );
    }

    let config_path = primary_repo_config_path(&path);
    let config_dir = config_path
        .parent()
        .ok_or_else(|| anyhow::anyhow!("Invalid repo config path: {}", config_path.display()))?;
    fs::create_dir_all(config_dir)?;
    fs::write(&config_path, INIT_TEMPLATE)?;

    println!("Created .forager/config.toml at {}", path.display());
    println!("Edit the file to configure hooks and session defaults for this repo.");

    Ok(())
}
