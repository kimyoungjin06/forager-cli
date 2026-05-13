#!/bin/bash
set -e

REPO="kimyoungjin06/forager-cli"
INSTALL_DIR="${INSTALL_DIR:-/usr/local/bin}"
BINARY_NAME="forager"
LEGACY_BINARY_NAME="aoe"

info() { printf "\033[34m[info]\033[0m %s\n" "$1"; }
success() { printf "\033[32m[ok]\033[0m %s\n" "$1"; }
error() { printf "\033[31m[error]\033[0m %s\n" "$1" >&2; exit 1; }

detect_platform() {
    local os arch
    os=$(uname -s | tr '[:upper:]' '[:lower:]')
    arch=$(uname -m)

    case "$os" in
        linux) os="linux" ;;
        darwin) os="darwin" ;;
        *) error "Unsupported OS: $os" ;;
    esac

    case "$arch" in
        x86_64|amd64) arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        *) error "Unsupported architecture: $arch" ;;
    esac

    echo "${os}-${arch}"
}

get_latest_version() {
    curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
        | grep '"tag_name"' \
        | sed -E 's/.*"([^"]+)".*/\1/'
}

main() {
    info "Detecting platform..."
    platform=$(detect_platform)
    success "Platform: $platform"

    info "Fetching latest version..."
    version=$(get_latest_version)
    if [ -z "$version" ]; then
        error "Failed to fetch latest version"
    fi
    success "Latest version: $version"

    download_url="https://github.com/${REPO}/releases/download/${version}/forager-${platform}.tar.gz"
    legacy_download_url="https://github.com/${REPO}/releases/download/${version}/aoe-${platform}.tar.gz"
    archive_member="forager-${platform}"
    info "Downloading from: $download_url"

    tmp_dir=$(mktemp -d)
    trap 'rm -rf "$tmp_dir"' EXIT

    if ! curl -fsSL "$download_url" -o "$tmp_dir/forager.tar.gz"; then
        info "Primary Forager artifact not found; trying legacy artifact: $legacy_download_url"
        archive_member="aoe-${platform}"
        curl -fsSL "$legacy_download_url" -o "$tmp_dir/forager.tar.gz" || error "Download failed"
    fi
    success "Downloaded successfully"

    info "Extracting..."
    tar xzf "$tmp_dir/forager.tar.gz" -C "$tmp_dir"

    info "Installing to $INSTALL_DIR..."
    if [ -w "$INSTALL_DIR" ]; then
        mv "$tmp_dir/$archive_member" "$INSTALL_DIR/$BINARY_NAME"
        ln -sf "$BINARY_NAME" "$INSTALL_DIR/$LEGACY_BINARY_NAME"
        chmod +x "$INSTALL_DIR/$BINARY_NAME"
    else
        sudo mv "$tmp_dir/$archive_member" "$INSTALL_DIR/$BINARY_NAME"
        sudo ln -sf "$BINARY_NAME" "$INSTALL_DIR/$LEGACY_BINARY_NAME"
        sudo chmod +x "$INSTALL_DIR/$BINARY_NAME"
    fi

    success "Installed $BINARY_NAME $version to $INSTALL_DIR/$BINARY_NAME"
    info "Legacy alias available at $INSTALL_DIR/$LEGACY_BINARY_NAME"

    if ! command -v tmux &> /dev/null; then
        info ""
        info "Note: tmux is required but not installed."
        info "Install it with:"
        info "  Debian/Ubuntu: sudo apt install tmux"
        info "  Fedora/RHEL:   sudo dnf install tmux"
        info "  Arch:          sudo pacman -S tmux"
        info "  macOS:         brew install tmux"
    fi

    echo ""
    success "Run 'forager' to get started!"
    echo ""
    info "For shell completions, run: forager completion --help"
}

main "$@"
