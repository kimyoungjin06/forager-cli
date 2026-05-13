use super::error::{DockerError, Result};
use std::process::Command;

pub(crate) struct Docker;

impl Docker {
    fn command(&self) -> Command {
        Command::new("docker")
    }

    pub(crate) fn does_container_exist(&self, name: &str) -> Result<bool> {
        let output = self
            .command()
            .args(["container", "inspect", name])
            .output()?;
        Ok(output.status.success())
    }

    pub(crate) fn remove(&self, name: &str, force: bool) -> Result<()> {
        let mut args = vec!["rm"];
        if force {
            args.push("-f");
        }
        args.push("-v");
        args.push(name);

        let output = self.command().args(args).output()?;

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            if stderr.contains("No such container") {
                return Err(DockerError::ContainerNotFound(name.to_string()));
            }
            return Err(DockerError::RemoveFailed(stderr.to_string()));
        }

        Ok(())
    }
}
