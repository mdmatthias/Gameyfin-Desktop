"""Create and manage desktop shortcuts for game prefixes."""

from __future__ import annotations

import glob
import logging
import os
from typing import Any

from PyQt6.QtWidgets import QDialog, QMessageBox

from gameyfin_frontend.dialogs import SelectShortcutsDialog
from gameyfin_frontend.utils import create_shortcuts, resolve_shortcut_game_info, get_xdg_user_dir

logger = logging.getLogger(__name__)


class ShortcutService:
    """Handles desktop shortcut creation and management for game prefixes."""

    def __init__(self, settings: Any) -> None:
        """Initialize the shortcut service.

        Args:
            settings: SettingsManager instance providing app configuration.
        """
        self.settings = settings

    def detect_existing_shortcuts(self, desktop_files: list[str]) -> tuple[list[str], list[str]]:
        """Detect which desktop files already exist on Desktop and in Applications.

        Args:
            desktop_files: List of .desktop file paths to check.

        Returns:
            Tuple of (existing_desktop_basenames, existing_apps_basenames).
        """
        existing_desktop: list[str] = []
        existing_apps: list[str] = []
        home_dir = os.path.expanduser("~")
        desktop_dir = os.path.join(home_dir, get_xdg_user_dir("DESKTOP"))
        apps_dir = os.path.join(home_dir, ".local", "share", "applications")

        for df in desktop_files:
            bn = os.path.basename(df)
            if os.path.exists(os.path.join(desktop_dir, bn)):
                existing_desktop.append(bn)
            if os.path.exists(os.path.join(apps_dir, bn)):
                existing_apps.append(bn)

        return existing_desktop, existing_apps

    def show_shortcut_dialog(
        self,
        desktop_files: list[str],
        parent: object,
        existing_desktop: list[str] | None = None,
        existing_apps: list[str] | None = None,
    ) -> tuple[list[str], list[str]] | None:
        """Show the shortcut selection dialog and return user's choices.

        Args:
            desktop_files: List of .desktop file paths to select from.
            parent: Parent widget for dialog ownership.
            existing_desktop: List of basenames already on Desktop.
            existing_apps: List of basenames already in Applications.

        Returns:
            Tuple of (selected_desktop, selected_apps) or None if cancelled.
        """
        dialog = SelectShortcutsDialog(
            desktop_files,
            parent,
            existing_desktop=existing_desktop or [],
            existing_apps=existing_apps or [],
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.get_selected_files()
        return None

    def create_shortcuts_for_prefix(
        self,
        prefix_path: str,
        game_name: str,
        selected_desktop: list[str],
        selected_apps: list[str],
        parent: object,
    ) -> bool:
        """Create desktop shortcuts for a prefix using its stored install config.

        Loads config.json from the prefix's script directories, resolves game info,
        and calls ``create_shortcuts`` from utils.

        Args:
            prefix_path: Full filesystem path to the Wine prefix.
            game_name: Name of the game (for finding scripts dir).
            selected_desktop: Basenames to place on the user's Desktop.
            selected_apps: Basenames to place in ~/.local/share/applications.
            parent: Parent widget for error dialogs.

        Returns:
            True if shortcuts were created successfully, False otherwise.
        """
        # Load config.json from scripts directory
        scripts_dirs = self.settings.get_shortcuts_dirs(game_name)
        install_config: dict[str, Any] = {}
        scripts_dir: str | None = None

        for sd in scripts_dirs:
            config_path = os.path.join(sd, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, 'r') as f:
                        install_config = json.load(f)
                    scripts_dir = sd
                    break
                except (json.JSONDecodeError, OSError) as e:
                    logger.error("Error loading config for shortcuts: %s", e)

        if not scripts_dir:
            logger.warning("No scripts directory found for game '%s'", game_name)

        primary_scripts_dir = self.settings.get_shortcuts_dir(game_name)

        game_name_resolved, proton_path = resolve_shortcut_game_info(
            prefix_path, install_config
        )

        # Find all .desktop files in the prefix
        shortcuts_dir = os.path.join(prefix_path, "drive_c", "proton_shortcuts")
        if not os.path.isdir(shortcuts_dir):
            QMessageBox.warning(parent, "No Shortcuts Found",
                                f"The directory '{shortcuts_dir}' does not exist.\n\n"
                                "Shortcuts are usually captured during the installation process.")
            return False

        all_desktop_files = glob.glob(os.path.join(shortcuts_dir, "*.desktop"))
        if not all_desktop_files:
            QMessageBox.warning(parent, "No Shortcuts Found",
                                "No .desktop files found in the proton_shortcuts directory.")
            return False

        create_shortcuts(
            all_desktop_files=all_desktop_files,
            scripts_dir=primary_scripts_dir or "",
            wine_prefix=prefix_path,
            install_config=install_config,
            proton_path=proton_path,
            selected_desktop=selected_desktop,
            selected_apps=selected_apps,
            remove_unselected=True,
        )

        return True
