#!/usr/bin/env bash
# Build, install (per-user) and run Gameyfin Desktop as a Flatpak.
# Works on a clean machine: installs flatpak itself if missing (needs sudo),
# adds the Flathub remote, and pulls the runtime, SDK, base app and SDK
# extensions. The 32-bit GL drivers are left to the app, which offers to
# install them on startup (as it does for users of a .flatpak bundle).
set -euo pipefail

cd "$(dirname "$0")"

MANIFEST="org.gameyfin.Gameyfin-Desktop.yaml"
BUILD_DIR="build-dir"
APP_ID="org.gameyfin.Gameyfin-Desktop"
FLATHUB_URL="https://dl.flathub.org/repo/flathub.flatpakrepo"

if [ ! -f "$MANIFEST" ]; then
    echo "Error: Manifest file $MANIFEST not found."
    exit 1
fi

# --- 1. flatpak itself -------------------------------------------------------
if ! command -v flatpak >/dev/null 2>&1; then
    echo "flatpak not found, installing it (sudo required)..."
    if command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y flatpak
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y flatpak
    elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --needed --noconfirm flatpak
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper --non-interactive install flatpak
    else
        echo "Error: could not detect a package manager. Install flatpak manually: https://flatpak.org/setup/"
        exit 1
    fi
fi

# --- 2. Flathub remote -------------------------------------------------------
flatpak remote-add --user --if-not-exists flathub "$FLATHUB_URL"

# --- 3. flatpak-builder --------------------------------------------------------
# Always use org.flatpak.Builder from Flathub: it bundles its own flatpak, so
# distro regressions don't break the build (Fedora's flatpak 1.18.2 fails
# build-init with a base app: "lsetxattr(security.selinux): Operation not
# supported").
flatpak install --user -y --noninteractive flathub org.flatpak.Builder
BUILDER=(flatpak run org.flatpak.Builder)

# --- 4. Build and install ----------------------------------------------------
# --install-deps-from pulls the runtime, SDK, PyQt base app and sdk-extensions.
"${BUILDER[@]}" --user --install --force-clean \
    --install-deps-from=flathub \
    "$BUILD_DIR" "$MANIFEST"

# --- 5. Run ------------------------------------------------------------------
flatpak run "$APP_ID"
