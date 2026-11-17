//! Opt-in queue v4 stores explicitly acknowledged journal prefixes.
//! Startup never rewrites existing records or grants historical authority.
pub(crate) const QUEUE_SCHEMA: &str = "offdesk_task_queue.v4";

pub fn run() -> anyhow::Result<()> {
    Ok(())
}
