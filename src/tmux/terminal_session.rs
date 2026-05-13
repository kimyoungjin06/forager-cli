//! Terminal session for paired terminal functionality

use anyhow::{bail, Result};
use std::process::Command;

use super::utils::sanitize_session_name;
use super::{refresh_session_cache, tmux_session_exists, LEGACY_TERMINAL_PREFIX, TERMINAL_PREFIX};
use crate::cli::truncate_id;
use crate::process;

pub struct TerminalSession {
    name: String,
    legacy_name: String,
}

impl TerminalSession {
    pub fn new(id: &str, title: &str) -> Result<Self> {
        Ok(Self {
            name: Self::generate_name(id, title),
            legacy_name: Self::generate_legacy_name(id, title),
        })
    }

    pub fn generate_name(id: &str, title: &str) -> String {
        let safe_title = sanitize_session_name(title);
        format!("{}{}_{}", TERMINAL_PREFIX, safe_title, truncate_id(id, 8))
    }

    pub fn generate_legacy_name(id: &str, title: &str) -> String {
        let safe_title = sanitize_session_name(title);
        format!(
            "{}{}_{}",
            LEGACY_TERMINAL_PREFIX,
            safe_title,
            truncate_id(id, 8)
        )
    }

    fn target_name(&self) -> &str {
        if tmux_session_exists(&self.name) {
            &self.name
        } else if tmux_session_exists(&self.legacy_name) {
            &self.legacy_name
        } else {
            &self.name
        }
    }

    pub fn exists(&self) -> bool {
        tmux_session_exists(&self.name) || tmux_session_exists(&self.legacy_name)
    }

    pub fn create(&self, working_dir: &str) -> Result<()> {
        self.create_with_size(working_dir, None, None)
    }

    pub fn create_with_size(
        &self,
        working_dir: &str,
        command: Option<&str>,
        size: Option<(u16, u16)>,
    ) -> Result<()> {
        if self.exists() {
            return Ok(());
        }

        let args = build_terminal_create_args(&self.name, working_dir, command, size);
        let output = Command::new("tmux").args(&args).output()?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            bail!("Failed to create terminal session: {}", stderr);
        }

        refresh_session_cache();

        Ok(())
    }

    pub fn kill(&self) -> Result<()> {
        if !self.exists() {
            return Ok(());
        }

        let target_name = self.target_name().to_string();

        // Kill the entire process tree first to ensure child processes are terminated
        if let Some(pane_pid) = process::get_pane_pid(&target_name) {
            process::kill_process_tree(pane_pid);
        }

        refresh_session_cache();
        if !tmux_session_exists(&target_name) {
            return Ok(());
        }

        let output = Command::new("tmux")
            .args(["kill-session", "-t", &target_name])
            .output()?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            bail!("Failed to kill terminal session: {}", stderr);
        }

        refresh_session_cache();

        Ok(())
    }

    pub fn get_pane_pid(&self) -> Option<u32> {
        process::get_pane_pid(self.target_name())
    }

    pub fn attach(&self) -> Result<()> {
        if !self.exists() {
            bail!("Terminal session does not exist: {}", self.name);
        }

        let target_name = self.target_name();
        if std::env::var("TMUX").is_ok() {
            let status = Command::new("tmux")
                .args(["switch-client", "-t", target_name])
                .status()?;

            if !status.success() {
                let status = Command::new("tmux")
                    .args(["attach-session", "-t", target_name])
                    .status()?;

                if !status.success() {
                    bail!("Failed to attach to terminal session");
                }
            }
        } else {
            let status = Command::new("tmux")
                .args(["attach-session", "-t", target_name])
                .status()?;

            if !status.success() {
                bail!("Failed to attach to terminal session");
            }
        }

        Ok(())
    }

    pub fn capture_pane(&self, lines: usize) -> Result<String> {
        if !self.exists() {
            return Ok(String::new());
        }

        let target_name = self.target_name();
        let output = Command::new("tmux")
            .args([
                "capture-pane",
                "-t",
                target_name,
                "-p",
                "-S",
                &format!("-{}", lines),
            ])
            .output()?;

        if output.status.success() {
            Ok(String::from_utf8_lossy(&output.stdout).to_string())
        } else {
            Ok(String::new())
        }
    }
}

/// Build the argument list for tmux new-session command (terminal sessions).
/// Extracted for testability.
fn build_terminal_create_args(
    session_name: &str,
    working_dir: &str,
    command: Option<&str>,
    size: Option<(u16, u16)>,
) -> Vec<String> {
    let mut args = vec![
        "new-session".to_string(),
        "-d".to_string(),
        "-s".to_string(),
        session_name.to_string(),
        "-c".to_string(),
        working_dir.to_string(),
    ];

    if let Some((width, height)) = size {
        args.push("-x".to_string());
        args.push(width.to_string());
        args.push("-y".to_string());
        args.push(height.to_string());
    }

    if let Some(cmd) = command {
        args.push(cmd.to_string());
    }

    args
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tmux::{Session, LEGACY_TERMINAL_PREFIX, SESSION_PREFIX};

    #[test]
    fn test_terminal_session_generate_name() {
        let name = TerminalSession::generate_name("abc123def456", "My Project");
        assert!(name.starts_with(TERMINAL_PREFIX));
        assert!(name.contains("My_Project"));
        assert!(name.contains("abc123de"));
    }

    #[test]
    fn test_terminal_session_generate_legacy_name() {
        let name = TerminalSession::generate_legacy_name("abc123def456", "My Project");
        assert!(name.starts_with(LEGACY_TERMINAL_PREFIX));
        assert!(name.contains("My_Project"));
        assert!(name.contains("abc123de"));
    }

    #[test]
    fn test_terminal_session_name_differs_from_agent_session() {
        let agent_name = Session::generate_name("abc123def456", "My Project");
        let terminal_name = TerminalSession::generate_name("abc123def456", "My Project");
        assert_ne!(agent_name, terminal_name);
        assert!(agent_name.starts_with(SESSION_PREFIX));
        assert!(terminal_name.starts_with(TERMINAL_PREFIX));
    }

    #[test]
    fn test_build_terminal_create_args_without_size() {
        let args = build_terminal_create_args("test_terminal", "/tmp/work", None, None);
        assert_eq!(
            args,
            vec![
                "new-session",
                "-d",
                "-s",
                "test_terminal",
                "-c",
                "/tmp/work"
            ]
        );
        assert!(!args.contains(&"-x".to_string()));
        assert!(!args.contains(&"-y".to_string()));
    }

    #[test]
    fn test_build_terminal_create_args_with_size() {
        let args = build_terminal_create_args("test_terminal", "/tmp/work", None, Some((100, 30)));
        assert!(args.contains(&"-x".to_string()));
        assert!(args.contains(&"100".to_string()));
        assert!(args.contains(&"-y".to_string()));
        assert!(args.contains(&"30".to_string()));

        // Verify order: -x should come before width, -y before height
        let x_idx = args.iter().position(|a| a == "-x").unwrap();
        let y_idx = args.iter().position(|a| a == "-y").unwrap();
        assert_eq!(args[x_idx + 1], "100");
        assert_eq!(args[y_idx + 1], "30");
    }

    #[test]
    fn test_build_terminal_create_args_with_command() {
        let args = build_terminal_create_args(
            "test_terminal",
            "/tmp/work",
            Some("bash -lc 'echo ready'"),
            None,
        );
        assert_eq!(args.last().unwrap(), "bash -lc 'echo ready'");
    }

    #[test]
    fn test_build_terminal_create_args_with_size_and_command() {
        let args = build_terminal_create_args(
            "test_terminal",
            "/tmp/work",
            Some("bash -lc 'echo ready'"),
            Some((80, 24)),
        );

        // Size args should be present
        assert!(args.contains(&"-x".to_string()));
        assert!(args.contains(&"80".to_string()));
        assert!(args.contains(&"-y".to_string()));
        assert!(args.contains(&"24".to_string()));

        // Command should be last
        assert_eq!(args.last().unwrap(), "bash -lc 'echo ready'");
    }
}
