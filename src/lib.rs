//! Forager library - Core functionality for offdesk agent orchestration

pub mod agents;
pub mod cli;
pub mod containers;
pub mod entrypoint;
pub mod git;
pub mod migrations;
pub mod offdesk;
pub mod process;
pub mod session;
pub mod sound;
pub mod terminal;
pub mod tmux;
pub mod tui;
pub mod update;

pub use entrypoint::run_cli;
