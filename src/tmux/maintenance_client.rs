use std::process::{Command, ExitStatus};
use std::time::Duration;

use anyhow::Result;

/// Wait for this frontend's attach client; maintenance may detach that client.
pub(super) fn attach_status(command: &mut Command) -> Result<ExitStatus> {
    let mut child = command.spawn()?;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        if crate::maintenance::requested().unwrap_or(true) {
            let _ = detach_owned_client(child.id());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn detach_owned_client(pid: u32) -> Result<()> {
    let clients = Command::new("tmux")
        .args([
            "list-clients",
            "-F",
            "#{client_pid}|#{client_tty}|#{session_id}",
        ])
        .output()?;
    if !clients.status.success() {
        return Ok(());
    }
    let text = String::from_utf8(clients.stdout)?;
    for line in text.lines() {
        let fields = line.split('|').collect::<Vec<_>>();
        if fields.len() != 3 || fields[0].parse::<u32>().ok() != Some(pid) {
            continue;
        }
        let tty = fields[1];
        let session = fields[2];
        if !tty.starts_with("/dev/") || tty.chars().any(char::is_control) {
            return Ok(());
        }
        let destroy = Command::new("tmux")
            .args([
                "show-options",
                "-A",
                "-v",
                "-t",
                session,
                "destroy-unattached",
            ])
            .output()?;
        let exit = Command::new("tmux")
            .args(["show-options", "-s", "-v", "exit-unattached"])
            .output()?;
        // A client detach must never become a session/server destruction request.
        if !destroy.status.success()
            || !exit.status.success()
            || String::from_utf8_lossy(&destroy.stdout).trim() != "off"
            || String::from_utf8_lossy(&exit.stdout).trim() != "off"
        {
            return Ok(());
        }
        let current = Command::new("tmux")
            .args([
                "display-message",
                "-c",
                tty,
                "-p",
                "#{client_pid}|#{session_id}",
            ])
            .output()?;
        if current.status.success()
            && String::from_utf8_lossy(&current.stdout).trim() == format!("{pid}|{session}")
        {
            let _ = Command::new("tmux")
                .args(["detach-client", "-t", tty])
                .status()?;
        }
        return Ok(());
    }
    Ok(())
}
