//! Optional immutable weekly-usage binding; no historical ledger rewrites.
//! New manifests require a compatible retained reader. Old manifests omit the
//! field and retain their hashes and approvals. Startup does not create budgets.
pub fn run() -> anyhow::Result<()> {
    Ok(())
}
