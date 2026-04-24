#!/usr/bin/env bash
set -e

MANIFEST="org.gameyfin.Gameyfin-Desktop.yaml"
BUILD_DIR="build-dir"
APP_ID="org.gameyfin.Gameyfin-Desktop"

flatpak-builder --user --install --force-clean "$BUILD_DIR" "$MANIFEST"

flatpak run "$APP_ID"