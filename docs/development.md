# Development

## Building

```bash
cargo build                    # Debug build
cargo build --release          # Release build (with LTO)
cargo build --profile dev-release  # Optimized build without LTO (faster compile)
```

The primary release binary is at `target/release/forager`; `target/release/aoe`
is kept as a legacy compatibility alias.

## Running

```bash
cargo run --release            # Run from source
FORAGER_DEBUG=1 cargo run --bin forager  # With debug logging
RUST_LOG=forager=debug cargo run  # With env_logger debug output
```

Requires `tmux` to be installed.

## Testing

```bash
cargo test       # Unit + integration tests
cargo fmt        # Format code
cargo clippy     # Lint
cargo check      # Fast type-check
```

Some integration tests require `tmux` to be available and will skip if it's not installed.

## Building the public website

The website combines Astro pages and mdBook documentation under `/forager-cli/`.
Use Node.js 22.12 or later, npm, mdBook, and the Rust toolchain:

```bash
cargo run --locked -p xtask -- gen-docs
bash scripts/build-site.sh
python3 tests/support/test_site_links.py
python3 scripts/check_site_links.py dist
npm --prefix website audit --audit-level=high
npm --prefix website run test:visual
```

The build script installs locked website dependencies and writes the assembled
site to `dist/`. The link checker validates local routes, assets and anchors in
that output; it does not request or certify external websites. GitHub Pages
checks dependencies and the assembled links before uploading its artifact.

The public workspace routes use sample data. Local exports and the action
bridge are separate from the published site. Keep review data in its existing
local source location when preparing public changes.

## Generating the Demo GIF

The demo GIF in the docs is created using [VHS](https://github.com/charmbracelet/vhs).

```bash
# Install VHS
brew install vhs

# Clear the demo profile
rm -rf ~/.forager/profiles/demo

# Ensure demo directories exist
mkdir -p /tmp/demo-projects/api-server /tmp/demo-projects/web-app

# Generate the GIF (from repo root)
vhs assets/demo.tape
```

This creates an uncommitted `docs/assets/demo.gif`. The committed static preview
is `docs/assets/tui.png`, and the demo uses `-p demo` to run in a separate
profile.
