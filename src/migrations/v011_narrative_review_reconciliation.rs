//! Opt-in queue v5 separates an approved snapshot from an equal-task current document.
//! Startup never rewrites queues or grants a new plan disposition.
pub(crate) const QUEUE_SCHEMA: &str = "offdesk_task_queue.v5";

pub fn run() -> anyhow::Result<()> {
    Ok(())
}
