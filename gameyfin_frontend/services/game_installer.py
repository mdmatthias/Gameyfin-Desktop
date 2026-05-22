"""Game installation orchestration — UMU detection and config."""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
from typing import Any

from PyQt6.QtWidgets import QDialog

from gameyfin_frontend.dialogs import InstallConfigDialog, SelectUmuIdDialog
from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.settings import SettingsManager

logger = logging.getLogger(__name__)


class GameInstaller:
    """Detect UMU game IDs, prompt for install config, and return the config dict."""

    def __init__(
        self,
        umu_database: UmuDatabase,
        settings: SettingsManager,
        parent: object,
    ) -> None:
        """Initialize the installer service.

        Args:
            umu_database: UmuDatabase instance for UMU game lookups.
            settings: SettingsManager instance providing app configuration.
            parent: Parent widget for dialog ownership.
        """
        self.umu_database = umu_database
        self.settings = settings
        self.parent = parent

    def detect_umu_game_id(self, target_dir: str) -> tuple[str, str]:
        """Search the UMU database for a matching game by codename or title.

        Tries product_*.json codename first, then falls back to filename-based
        title search. If multiple results are found, opens a selection dialog.

        Args:
            target_dir: Download target directory containing the game.

        Returns:
            Tuple of (umu_id, store).
        """
        default_game_id = "umu-default"
        default_store = "none"
        results: list[dict[str, Any]] = []

        try:
            json_files = glob.glob(os.path.join(target_dir, "product_*.json"))
            if json_files:
                product_json_path = json_files[0]
                logger.info("Found product info: %s", product_json_path)

                with open(product_json_path, 'r') as f:
                    product_data = json.load(f)

                codename = product_data.get("id")
                if codename:
                    logger.info("Found codename: %s", codename)
                    results = self.umu_database.get_game_by_codename(str(codename))
                    logger.info("API results (by codename): %s", results)

            if not results:
                filename = ""
                if hasattr(self.settings, 'get'):
                    filename = self.settings.get("filename", "") or ""
                    if not filename:
                        path = self.settings.get("path")
                        if path:
                            filename = os.path.basename(path)
                zip_name_base = os.path.splitext(filename)[0]
                search_title = zip_name_base.replace('_', ' ').replace('-', ' ').strip()

                if search_title:
                    logger.info("No results from codename. Fallback: searching by title: '%s'", search_title)
                    results = self.umu_database.search_by_partial_title(search_title)
                    logger.info("API results (by title): %s", results)
                else:
                    logger.info("No codename found and filename was empty. Skipping UMU search.")

            selected_entry = None
            if isinstance(results, list) and len(results) > 0:
                if len(results) == 1:
                    selected_entry = results[0]
                    logger.info("One matching entry found.")
                else:
                    logger.info("Multiple matching entries found, showing dialog.")
                    umu_dialog = SelectUmuIdDialog(results, self.parent)
                    if umu_dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_entry = umu_dialog.get_selected_entry()
                    else:
                        logger.info("User cancelled UMU ID selection.")

                if selected_entry:
                    default_game_id = selected_entry.get("umu_id", default_game_id)
                    default_store = selected_entry.get("store", default_store)
                    logger.info("Using: umu_id=%s, store=%s", default_game_id, default_store)

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Error during UMU auto-detection: %s", e)

        return default_game_id, default_store

    def prompt_install_config(
        self,
        umu_id: str,
        store: str,
        wine_prefix_path: str,
    ) -> dict[str, Any] | None:
        """Show the install configuration dialog and return the user's choices.

        Args:
            umu_id: UMU game ID to pre-fill.
            store: Store platform to pre-fill.
            wine_prefix_path: Path to the Wine prefix directory.

        Returns:
            Config dict if accepted, ``None`` if cancelled.
        """
        dialog = InstallConfigDialog(
            umu_database=self.umu_database,
            parent=self.parent,
            default_game_id=umu_id,
            default_store=store,
            wine_prefix_path=wine_prefix_path,
            settings=self.settings,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.get_config()

    def build_wine_prefix(self, target_dir: str) -> str:
        """Derive a Wine prefix path from the download target directory name.

        Args:
            target_dir: Download target directory.

        Returns:
            Full path to the Wine prefix.
        """
        folder_name = os.path.basename(target_dir)
        pfx_name = f"{folder_name.lower()}_pfx"
        prefixes_dir = self.settings.get_prefixes_dir() if self.settings else ""
        return os.path.join(prefixes_dir, pfx_name) if prefixes_dir else ""
