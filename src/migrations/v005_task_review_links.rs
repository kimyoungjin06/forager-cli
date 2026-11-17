//! Opt-in queue envelope for decision links that older Vec readers must reject.
//!
//! Existing profiles are not rewritten at startup. The first explicitly
//! confirmed link write uses v2; all later writes preserve that envelope.

use anyhow::{ensure, Result};
use serde::{Deserialize, Serialize};

use crate::offdesk::OffdeskTask;

const SCHEMA: &str = "offdesk_task_queue.v2";

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Queue {
    schema: String,
    tasks: Vec<OffdeskTask>,
}

pub fn run() -> Result<()> {
    Ok(())
}

pub(crate) fn decode(content: &str) -> Result<Vec<OffdeskTask>> {
    if content.trim_start().starts_with('[') {
        let tasks: Vec<OffdeskTask> = serde_json::from_str(content)?;
        ensure!(
            tasks.iter().all(|task| task.review_links.is_empty()),
            "review links require a versioned queue envelope"
        );
        Ok(tasks)
    } else {
        let queue: Queue = serde_json::from_str(content)?;
        ensure!(
            queue.schema == SCHEMA
                || queue.schema == super::v009_historical_review_boundary::QUEUE_SCHEMA
                || queue.schema == super::v010_historical_review_prefix::QUEUE_SCHEMA
                || queue.schema == super::v011_narrative_review_reconciliation::QUEUE_SCHEMA,
            "unsupported task queue schema"
        );
        ensure!(
            queue.schema != SCHEMA || !has_historical_boundary(&queue.tasks),
            "historical boundaries require task queue v3"
        );
        ensure!(
            queue.schema == super::v010_historical_review_prefix::QUEUE_SCHEMA
                || queue.schema == super::v011_narrative_review_reconciliation::QUEUE_SCHEMA
                || !has_historical_prefix(&queue.tasks),
            "historical prefixes require task queue v4"
        );
        ensure!(
            queue.schema == super::v011_narrative_review_reconciliation::QUEUE_SCHEMA
                || !has_narrative_reconciliation(&queue.tasks),
            "narrative reconciliations require task queue v5"
        );
        Ok(queue.tasks)
    }
}

pub(crate) fn encode(tasks: &[OffdeskTask], existing: Option<&str>) -> Result<serde_json::Value> {
    let versioned = existing.is_some_and(|content| !content.trim_start().starts_with('['))
        || tasks.iter().any(|task| !task.review_links.is_empty());
    if let Some(content) = existing {
        decode(content)?;
    }
    let preserve_v3 = existing.is_some_and(|content| {
        serde_json::from_str::<serde_json::Value>(content).is_ok_and(|value| {
            value["schema"] == super::v009_historical_review_boundary::QUEUE_SCHEMA
        })
    });
    let preserve_v4 = existing.is_some_and(|content| {
        serde_json::from_str::<serde_json::Value>(content).is_ok_and(|value| {
            value["schema"] == super::v010_historical_review_prefix::QUEUE_SCHEMA
        })
    });
    let preserve_v5 = existing.is_some_and(|content| {
        serde_json::from_str::<serde_json::Value>(content).is_ok_and(|value| {
            value["schema"] == super::v011_narrative_review_reconciliation::QUEUE_SCHEMA
        })
    });
    if versioned {
        Ok(serde_json::to_value(Queue {
            schema: if preserve_v5 || has_narrative_reconciliation(tasks) {
                super::v011_narrative_review_reconciliation::QUEUE_SCHEMA
            } else if preserve_v4 || has_historical_prefix(tasks) {
                super::v010_historical_review_prefix::QUEUE_SCHEMA
            } else if preserve_v3 || has_historical_boundary(tasks) {
                super::v009_historical_review_boundary::QUEUE_SCHEMA
            } else {
                SCHEMA
            }
            .into(),
            tasks: tasks.to_vec(),
        })?)
    } else {
        Ok(serde_json::to_value(tasks)?)
    }
}

fn has_historical_boundary(tasks: &[OffdeskTask]) -> bool {
    tasks.iter().any(|task| {
        task.review_links.iter().any(|link| {
            link.preview.request.historical_boundary.is_some()
                || link.preview.historical_boundary.is_some()
        })
    })
}

fn has_historical_prefix(tasks: &[OffdeskTask]) -> bool {
    tasks.iter().any(|task| {
        task.review_links.iter().any(|link| {
            link.preview.request.historical_prefix.is_some()
                || link.preview.historical_prefix.is_some()
        })
    })
}

fn has_narrative_reconciliation(tasks: &[OffdeskTask]) -> bool {
    tasks.iter().any(|task| {
        task.review_links.iter().any(|link| {
            link.preview.request.narrative_reconciliation.is_some()
                || link.preview.narrative_reconciliation.is_some()
        })
    })
}
