# Installation

## Prerequisites

- [tmux](https://github.com/tmux/tmux/wiki) (required)

## Install Forager

### Quick Install (Recommended)

Run the install script:

```bash
curl -fsSL \
  https://raw.githubusercontent.com/kimyoungjin06/forager-cli/main/scripts/install.sh \
  | bash
```

### Build from Source

```bash
git clone https://github.com/kimyoungjin06/forager-cli
cd forager
cargo build --release
```

The primary binary will be at `target/release/forager`; `target/release/aoe`
is also built as a legacy compatibility alias.

## Verify Installation

```bash
forager --version
```

## Uninstall

To remove Forager:

```bash
forager uninstall
```

This will guide you through removing the binary, configuration, and tmux settings.
