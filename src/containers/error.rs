use thiserror::Error;

#[derive(Debug, Error)]
pub enum DockerError {
    #[error("Container not found: {0}")]
    ContainerNotFound(String),

    #[error("Failed to remove container: {0}")]
    RemoveFailed(String),

    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),
}

pub type Result<T> = std::result::Result<T, DockerError>;
