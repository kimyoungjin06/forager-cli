//! Record support for optional serial-selection bindings without rewriting evidence.
//!
//! Missing bindings remain None and serialize exactly as before, preserving old
//! manifest hashes and approvals. New bindings are part of the immutable manifest
//! hash. Older readers reject their unknown field, so downgrade requires retaining
//! a reader that understands these records. No historical run gains serial authority.

pub fn run() -> anyhow::Result<()> {
    // The migration runner advances the capability version. Existing ledgers,
    // receipts and approvals require no byte transformation or new defaults.
    Ok(())
}
