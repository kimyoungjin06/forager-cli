//! Opt-in queue v3 stores explicitly acknowledged historical review boundaries.
//! Startup never rewrites a queue or acknowledges missing journal history.
pub(crate) const QUEUE_SCHEMA: &str = "offdesk_task_queue.v3";

pub fn run() -> anyhow::Result<()> {
    Ok(())
}
