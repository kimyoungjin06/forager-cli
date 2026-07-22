//! Harness-wide orchestration signals shared by the TUI status bar and
//! `forager status`: overnight autonomy state and the wiki candidate queue
//! across every registered knowledge plane.

use crate::session::{get_profile_dir, project_registry};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OrchestrationSignals {
    pub autonomy_armed: bool,
    pub registered_projects: usize,
    pub wiki_candidates: usize,
}

pub fn load_orchestration_signals(profile: &str) -> OrchestrationSignals {
    let autonomy_armed = get_profile_dir(profile)
        .ok()
        .map(|dir| dir.join("offdesk_autonomy_armed.json"))
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
        .map(|state| {
            state
                .get("armed")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
                && state
                    .get("until")
                    .and_then(|v| v.as_str())
                    .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                    .is_some_and(|until| until > chrono::Utc::now())
        })
        .unwrap_or(false);

    let registry = project_registry::load_registry();
    let mut wiki_candidates = 0;
    for entry in &registry {
        let Some(wiki_profile) = &entry.wiki_profile else {
            continue;
        };
        let Ok(dir) = get_profile_dir(wiki_profile) else {
            continue;
        };
        if let Ok(state) = super::AdaptiveWikiStore::new(dir).load_candidates() {
            wiki_candidates += state.candidates.len();
        }
    }
    OrchestrationSignals {
        autonomy_armed,
        registered_projects: registry.len(),
        wiki_candidates,
    }
}
