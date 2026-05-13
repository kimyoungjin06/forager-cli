use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    forager::run_cli().await
}
