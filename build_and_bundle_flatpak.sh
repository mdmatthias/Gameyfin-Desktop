#!/usr/bin/env bash

set -e

MANIFEST="org.gameyfin.Gameyfin-Desktop.yaml"
APP_ID="org.gameyfin.Gameyfin-Desktop"
BUILD_DIR="build-dir"
REPO_DIR="repo"
OUTPUT_BUNDLE="${APP_ID}.flatpak"

if [ ! -f "$MANIFEST" ]; then
    echo "Error: Manifest file $MANIFEST not found."
    exit 1
fi

echo "Building Flatpak app..."

flatpak-builder --force-clean --repo="$REPO_DIR" "$BUILD_DIR" "$MANIFEST"

echo "Creating Flatpak bundle: $OUTPUT_BUNDLE"

flatpak build-bundle "$REPO_DIR" "$OUTPUT_BUNDLE" "$APP_ID"

echo "Build complete: $OUTPUT_BUNDLE"