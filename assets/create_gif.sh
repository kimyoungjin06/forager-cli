#!/bin/bash
# Setup script for generating the demo GIF. Meant to be run from the root of the repo on a mac
# Requires: vhs, tmux, cargo

set -ex
cd "$(dirname "$0")/.."

DEMO_DIR="${HOME}/demo-projects"

cleanup() {
    rm -rf "$DEMO_DIR"
    rm -rf ~/.forager/profiles/demo
    rm -rf ~/.config/forager/profiles/demo
    # Also clear the legacy profile path so old demo runs do not bleed into captures.
    rm -rf ~/.agent-of-empires/profiles/demo
}

trap cleanup EXIT

# build the project
cargo build --release

# Clean and recreate demo project directories
rm -rf "$DEMO_DIR"
mkdir -p "$DEMO_DIR/api-server" "$DEMO_DIR/web-app" "$DEMO_DIR/chat-app"

pushd "$DEMO_DIR/api-server"
git init -q
touch README.md
git add .
git commit -q -m "Initial commit"
popd

pushd "$DEMO_DIR/web-app"
git init -q
touch README.md
git add .
git commit -q -m "Initial commit"
popd

pushd "$DEMO_DIR/chat-app"
git init -q
touch README.md
git add .
git commit -q -m "Initial commit"
popd

vhs assets/demo.tape
