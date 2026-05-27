"""Migrate data from legacy (~/.config/gameyfin) to new (~/.local/share/gameyfin) locations."""

from __future__ import annotations

import json
import logging
import os
import shutil
from typing import Any

logger = logging.getLogger(__name__)


class MigrationService:
    """One-time migration of settings and shortcut scripts from legacy to new locations.

    Wine prefixes are NOT migrated automatically — they're too large (GBs of DLLs/files).
    If a prefix exists only in the legacy location, users should move it manually when
    convenient. The app will still find it via SettingsManager.get_prefixes_dirs() which
    scans both locations during runtime.
    """

    DEFAULT_LEGACY_CONFIG_DIR = os.path.join(
        os.path.expanduser("~"), ".config", "gameyfin"
    )
    # Override class attribute in tests or set via env var before module load:
    #   export GF_LEGACY_CONFIG_DIR=/tmp/test-legacy; python ...
    LEGACY_CONFIG_DIR = os.environ.get("GF_LEGACY_CONFIG_DIR", DEFAULT_LEGACY_CONFIG_DIR)

    def __init__(self, settings_dir: str) -> None:
        """Initialize with the target (new) settings directory.

        Args:
            settings_dir: New app-data directory (e.g. ~/.local/share/gameyfin).
        """
        self.settings_dir = settings_dir
        self.migrated = False  # Track whether we've already run this session

    def migrate(self) -> dict[str, int]:
        """Run all migration steps once. Returns counts per category.

        Returns:
            Dict mapping category name to number of items migrated.
        """
        if self.migrated:
            return {}

        results: dict[str, int] = {
            "settings": 0,
            "shortcuts": 0,
        }

        try:
            results["settings"] = self._migrate_settings()
        except OSError as e:
            logger.error("Settings migration failed: %s", e)

        try:
            results["shortcuts"] = self._migrate_shortcuts()
        except OSError as e:
            logger.error("Shortcut scripts migration failed: %s", e)

        total = sum(results.values())
        if total > 0:
            logger.info(
                "Legacy data migration complete: %d settings, %d shortcut dirs",
                results["settings"],
                results["shortcuts"],
            )
        else:
            logger.debug("No legacy data found to migrate.")

        self.migrated = True
        return results

    def _legacy_exists(self) -> bool:
        """Check whether the legacy config directory exists at all."""
        return os.path.isdir(self.LEGACY_CONFIG_DIR)

    # ------------------------------------------------------------------
    # Settings file
    # ------------------------------------------------------------------

    def _migrate_settings(self) -> int:
        """Copy settings.json from legacy → new if new doesn't exist yet.

        Returns:
            1 if migrated, 0 otherwise.
        """
        if not self._legacy_exists():
            return 0

        src = os.path.join(self.LEGACY_CONFIG_DIR, "settings.json")
        dst = os.path.join(self.settings_dir, "settings.json")

        if os.path.exists(dst):
            logger.debug("Settings already in new location, skipping.")
            return 0

        if not os.path.exists(src):
            return 0

        try:
            with open(src, "r") as f:
                data: dict[str, Any] = json.load(f)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w") as f:
                json.dump(data, f, indent=4)
            logger.info("Migrated settings.json to %s", dst)
            return 1
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to migrate settings.json: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Shortcut scripts
    # ------------------------------------------------------------------

    def _migrate_shortcuts(self) -> int:
        """Copy each game's shortcut_scripts dir from legacy → new.

        Only copies a game's directory if it doesn't already exist in the
        new location. If both exist, assumes the new one is authoritative.

        Returns:
            Number of game directories migrated.
        """
        if not self._legacy_exists():
            return 0

        legacy_base = os.path.join(self.LEGACY_CONFIG_DIR, "shortcut_scripts")
        new_base = os.path.join(self.settings_dir, "shortcut_scripts")

        if not os.path.isdir(legacy_base):
            return 0

        count = 0
        try:
            for entry in sorted(os.listdir(legacy_base)):
                src_dir = os.path.join(legacy_base, entry)
                if not os.path.isdir(src_dir):
                    continue

                dst_dir = os.path.join(new_base, entry)
                if os.path.exists(dst_dir):
                    logger.debug(
                        "Shortcut scripts for '%s' already in new location, skipping.",
                        entry,
                    )
                    continue

                shutil.copytree(src_dir, dst_dir)
                logger.info("Migrated shortcut scripts for '%s'", entry)
                count += 1
        except OSError as e:
            logger.error("Error migrating shortcut scripts: %s", e)

        return count
