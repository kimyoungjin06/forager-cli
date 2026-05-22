//! CLI argument definitions for documentation generation
//!
//! This module contains the CLI struct definitions used by clap.
//! They're separated from main.rs so xtask can generate documentation.

use clap::{Parser, Subcommand};
use clap_complete::Shell;

use super::add::AddArgs;
use super::doctor::DoctorArgs;
use super::group::GroupCommands;
use super::init::InitArgs;
use super::list::ListArgs;
use super::migrate::MigrateCommands;
use super::offdesk::OffdeskCommands;
use super::ondesk::OndeskCommands;
use super::profile::ProfileCommands;
use super::project::ProjectCommands;
use super::remove::RemoveArgs;
use super::session::SessionCommands;
use super::sounds::SoundsCommands;
use super::status::StatusArgs;
use super::tmux::TmuxCommands;
use super::uninstall::UninstallArgs;
use super::worktree::WorktreeCommands;

const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Parser)]
#[command(name = "forager")]
#[command(about = "Offdesk agent orchestration with approvals, recovery, and audit trails")]
#[command(version = VERSION)]
#[command(
    long_about = "Forager is an offdesk agent orchestration tool that uses tmux to help \
    you manage, monitor, approve, and recover AI coding agent work.\n\n\
    Run without arguments to launch the TUI dashboard. The legacy `aoe` binary remains available \
    as a compatibility alias and warns on human-facing commands."
)]
pub struct Cli {
    /// Profile to use (separate workspace with its own sessions)
    #[arg(short = 'p', long, global = true, env = "FORAGER_PROFILE")]
    pub profile: Option<String>,

    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Add a new session
    Add(AddArgs),

    /// Initialize .forager/config.toml in a repository
    Init(InitArgs),

    /// List all sessions
    #[command(alias = "ls")]
    List(ListArgs),

    /// Remove a session
    #[command(alias = "rm")]
    Remove(RemoveArgs),

    /// Show session status summary
    Status(StatusArgs),

    /// Diagnose Forager paths, profile env, and legacy AoE compatibility state
    Doctor(DoctorArgs),

    /// Migrate legacy AoE compatibility paths
    Migrate {
        #[command(subcommand)]
        command: MigrateCommands,
    },

    /// Manage session lifecycle (start, stop, attach, etc.)
    Session {
        #[command(subcommand)]
        command: SessionCommands,
    },

    /// Manage groups for organizing sessions
    Group {
        #[command(subcommand)]
        command: GroupCommands,
    },

    /// Manage profiles (separate workspaces)
    Profile {
        #[command(subcommand)]
        command: Option<ProfileCommands>,
    },

    /// Initialize and inspect project operation packets
    Project {
        #[command(subcommand)]
        command: ProjectCommands,
    },

    /// Manage git worktrees for parallel development
    Worktree {
        #[command(subcommand)]
        command: WorktreeCommands,
    },

    /// Manage offdesk approvals and recovery artifacts
    Offdesk {
        #[command(subcommand)]
        command: Box<OffdeskCommands>,
    },

    /// Capture ondesk notes and prompt context from external harness work
    Ondesk {
        #[command(subcommand)]
        command: OndeskCommands,
    },

    /// tmux integration utilities
    Tmux {
        #[command(subcommand)]
        command: TmuxCommands,
    },

    /// Manage sound effects for agent state transitions
    Sounds {
        #[command(subcommand)]
        command: SoundsCommands,
    },

    /// Uninstall Forager
    Uninstall(UninstallArgs),

    /// Generate shell completions
    Completion {
        /// Shell to generate completions for
        #[arg(value_enum)]
        shell: Shell,
    },
}
