//! Persistent operator pause: a global halt on new offdesk dispatch.
//!
//! When paused, `run_offdesk_tick` still polls and reconciles existing
//! background runs (monitoring continues) but launches no new work, leaving
//! queued tasks queued until the operator resumes. This is the emergency
//! "stop everything" switch, distinct from cancelling a single task.

use anyhow::{Context, Result};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

pub const OPERATOR_PAUSE_FILE: &str = "offdesk_operator_pause.json";
pub const OPERATOR_PAUSE_SCHEMA: &str = "offdesk_operator_pause.v1";

fn default_schema() -> String {
    OPERATOR_PAUSE_SCHEMA.to_string()
}

/// The current operator pause state for a profile.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OperatorPauseState {
    #[serde(default = "default_schema")]
    pub schema: String,
    #[serde(default)]
    pub paused: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub by: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub updated_at: Option<DateTime<Utc>>,
}

impl Default for OperatorPauseState {
    fn default() -> Self {
        Self {
            schema: default_schema(),
            paused: false,
            reason: None,
            by: None,
            updated_at: None,
        }
    }
}

/// Reads and writes `offdesk_operator_pause.json` in a profile directory.
pub struct OperatorPauseStore {
    path: PathBuf,
}

impl OperatorPauseStore {
    pub fn new(profile_dir: impl AsRef<Path>) -> Self {
        Self {
            path: profile_dir.as_ref().join(OPERATOR_PAUSE_FILE),
        }
    }

    /// Load the current state, defaulting to not-paused when no file exists.
    pub fn load(&self) -> Result<OperatorPauseState> {
        if !self.path.exists() {
            return Ok(OperatorPauseState::default());
        }
        let raw = fs::read_to_string(&self.path)
            .with_context(|| format!("reading {}", self.path.display()))?;
        let state: OperatorPauseState = serde_json::from_str(&raw)
            .with_context(|| format!("parsing {}", self.path.display()))?;
        Ok(state)
    }

    fn save(&self, state: &OperatorPauseState) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).with_context(|| format!("creating {}", parent.display()))?;
        }
        let body = serde_json::to_string_pretty(state)?;
        fs::write(&self.path, format!("{body}\n"))
            .with_context(|| format!("writing {}", self.path.display()))?;
        Ok(())
    }

    /// Engage the global pause. Idempotent: pausing when already paused keeps
    /// the existing reason unless a new one is given.
    pub fn pause(
        &self,
        reason: Option<&str>,
        by: Option<&str>,
        now: DateTime<Utc>,
    ) -> Result<OperatorPauseState> {
        let mut state = self.load()?;
        let reason = reason.map(str::trim).filter(|value| !value.is_empty());
        state.schema = default_schema();
        state.paused = true;
        if let Some(reason) = reason {
            state.reason = Some(reason.to_string());
        }
        state.by = by
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string);
        state.updated_at = Some(now);
        self.save(&state)?;
        Ok(state)
    }

    /// Clear the global pause so new dispatch can proceed again.
    pub fn resume(&self, by: Option<&str>, now: DateTime<Utc>) -> Result<OperatorPauseState> {
        let state = OperatorPauseState {
            schema: default_schema(),
            paused: false,
            reason: None,
            by: by
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_string),
            updated_at: Some(now),
        };
        self.save(&state)?;
        Ok(state)
    }
}
