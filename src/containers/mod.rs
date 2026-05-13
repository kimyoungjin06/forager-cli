mod docker;
pub mod error;

use crate::cli::truncate_id;
use docker::Docker;
use error::Result;

pub const CONTAINER_PREFIX: &str = "forager-sandbox-";
pub const LEGACY_CONTAINER_PREFIX: &str = "aoe-sandbox-";

pub struct DockerContainer {
    pub name: String,
    pub image: String,
    fallback_names: Vec<String>,
    runtime: Docker,
}

impl DockerContainer {
    pub fn generate_name(session_id: &str) -> String {
        format!("{}{}", CONTAINER_PREFIX, truncate_id(session_id, 8))
    }

    pub fn generate_legacy_name(session_id: &str) -> String {
        format!("{}{}", LEGACY_CONTAINER_PREFIX, truncate_id(session_id, 8))
    }

    pub fn from_stored_name(session_id: &str, image: &str, stored_name: &str) -> Self {
        let primary_name = Self::generate_name(session_id);
        let legacy_name = Self::generate_legacy_name(session_id);
        let preferred_name = if stored_name.trim().is_empty() {
            primary_name.clone()
        } else {
            stored_name.to_string()
        };

        let mut fallback_names = Vec::new();
        for candidate in [primary_name, legacy_name] {
            if candidate != preferred_name && !fallback_names.contains(&candidate) {
                fallback_names.push(candidate);
            }
        }

        Self {
            name: preferred_name,
            image: image.to_string(),
            fallback_names,
            runtime: Docker,
        }
    }

    fn candidate_names(&self) -> Vec<&str> {
        let mut names = vec![self.name.as_str()];
        for fallback in &self.fallback_names {
            if !names.contains(&fallback.as_str()) {
                names.push(fallback);
            }
        }
        names
    }

    fn target_name(&self) -> Result<String> {
        for name in self.candidate_names() {
            if self.runtime.does_container_exist(name)? {
                return Ok(name.to_string());
            }
        }
        Ok(self.name.clone())
    }

    pub fn exists(&self) -> Result<bool> {
        for name in self.candidate_names() {
            if self.runtime.does_container_exist(name)? {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub fn remove(&self, force: bool) -> Result<()> {
        let target_name = self.target_name()?;
        self.runtime.remove(&target_name, force)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_container_generate_name_short_id() {
        let name = DockerContainer::generate_name("abc");
        assert_eq!(name, "forager-sandbox-abc");
    }

    #[test]
    fn test_container_generate_name_long_id() {
        let name = DockerContainer::generate_name("abcdefghijklmnop");
        assert_eq!(name, "forager-sandbox-abcdefgh");
    }

    #[test]
    fn test_container_generate_legacy_name() {
        let name = DockerContainer::generate_legacy_name("abcdefghijklmnop");
        assert_eq!(name, "aoe-sandbox-abcdefgh");
    }

    #[test]
    fn test_container_from_stored_name_prefers_legacy_name() {
        let container = DockerContainer::from_stored_name(
            "test1234567890ab",
            "ubuntu:latest",
            "aoe-sandbox-test1234",
        );
        assert_eq!(container.name, "aoe-sandbox-test1234");
        assert!(container
            .fallback_names
            .contains(&"forager-sandbox-test1234".to_string()));
    }
}
