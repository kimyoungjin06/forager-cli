# Installation

## Coordinated frontend replacement

For a reviewed local cutover, `scripts/forager_frontend_maintenance.py` supplies
`begin`, `status`, `guard`, `verify`, `finish`, and `cancel`. Begin records an
operation ID and requests cooperative frontend exit. New TUI, go, and attach
entrypoints hold before initialization. An attached client detaches only when
its session and server retain sessions after detachment; agent input is untouched.

Run the exact release-specific operator command under `guard`, after the
approved stopped-writer, backup, queue/marker compatibility, and delivery-hold
preflight. Declare every retained reader executable with `--reader-executable`.
The guard retains original image identities across selector changes, checks
before and after that command, and rechecks at finish. A late old reader keeps
admission held; `verify` inspects again without rerunning the cutover command.
State and offsets have no automatic rollback or replay path in this helper.

Unknown or failed command outcomes retain the gate and audit history. Releasing
such a gate requires an exact current record hash, an operator reason, and
`--confirm-state-reviewed`. An unattempted operation can be cancelled directly.
This is local operator coordination; Offdesk execution authority stays with the
existing task and approval records.

The legacy reader inventory currently supports Linux. It covers declared image
identities and their expected native process names. Unreadable candidate readers
hold the transition; other unreadable process-name observations remain counted.
Arbitrary process renaming, direct old binaries, unrelated editors, and OS-wide
write fencing remain outside this cooperative contract. Retain old payloads for
identity checks and apply the release-specific compatibility decision to rollback.

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
cd forager-cli
cargo build --release
```

The primary binary will be at `target/release/forager`; `target/release/aoe`
is also built as a legacy compatibility alias.

Release installations verify the published SHA-256 checksum before extracting
or replacing the local binary. A missing or malformed checksum stops the
installation. Releases beginning with v0.14.0 also publish
`release-provenance.json`, which binds every archive checksum to the exact Git
tag and source commit.

After installation, the script writes a private `forager_install_receipt.v1`
record containing the version, source commit when provenance is available,
archive checksum, installed binary checksum, and install path. The default
receipt is `$XDG_STATE_HOME/forager/install-receipt.txt` on Linux, falling back
to `~/.local/state/forager/install-receipt.txt`; on macOS it is
`~/.forager/install-receipt.txt`. Set `FORAGER_INSTALL_RECEIPT` to use
another path.

### Telegram Operator Bundle

Each release also publishes a platform-independent
`forager-operator-<version>.tar.gz` bundle. It contains the Telegram operator,
watchdog, service installer, Wiki helpers, and the operational documentation
needed to run them with an installed `forager` binary. Verify the adjacent
`.sha256` file, extract the bundle, and follow `docs/remote-operator.md` inside
the extracted directory.

The default Telegram credential file is
`$XDG_CONFIG_HOME/forager/telegram.env` on Linux, falling back to
`~/.config/forager/telegram.env`. On macOS it is
`~/.forager/telegram.env`. Set `OFFDESK_TELEGRAM_ENV` or pass `--env-file` to
use a different private file.

## Verify Installation

```bash
forager --version
cat "${XDG_STATE_HOME:-$HOME/.local/state}/forager/install-receipt.txt"
```

On macOS, inspect `~/.forager/install-receipt.txt` instead.

## Uninstall

To remove Forager:

```bash
forager uninstall
```

This will guide you through removing the binary, configuration, and tmux settings.
