//! Harness-wide orchestration signals shared by the TUI status bar and
//! `forager status`: overnight autonomy state and the wiki candidate queue
//! across every registered knowledge plane.

use crate::session::{profile_dir_path, project_registry};

use super::{OffdeskShiftStatusV1, OffdeskShiftStore};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct OrchestrationSignals {
    pub autonomy_armed: bool,
    pub registered_projects: usize,
    pub wiki_candidates: usize,
}

pub fn load_orchestration_signals(profile: &str) -> OrchestrationSignals {
    let now = chrono::Utc::now();
    let autonomy_armed = OffdeskShiftStore::read_only(profile)
        .and_then(|store| store.snapshot())
        .ok()
        .and_then(|snapshot| snapshot.active().cloned())
        .map(|record| {
            record.state.is_some_and(|state| {
                matches!(
                    state.status,
                    OffdeskShiftStatusV1::Armed | OffdeskShiftStatusV1::Running
                ) && now >= record.manifest.window.starts_at
                    && now <= record.manifest.window.stops_at
                    && record
                        .arm_receipt
                        .is_some_and(|receipt| now <= receipt.expires_at)
            })
        })
        .unwrap_or(false);

    let registry = project_registry::load_registry();
    let mut wiki_candidates = 0;
    for entry in &registry {
        let Some(wiki_profile) = &entry.wiki_profile else {
            continue;
        };
        let Ok(dir) = profile_dir_path(wiki_profile) else {
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
