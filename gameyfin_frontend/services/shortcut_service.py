"""Create and manage desktop shortcuts for game prefixes."""

from __future__ import annotations

import glob
import json
import logging
import os
from typing import Any

from PyQt6.QtWidgets import QDialog, QMessageBox

from gameyfin_frontend.dialogs import SelectShortcutsDialog
from gameyfin_frontend.services.steam_integration import SteamIntegrationService
from gameyfin_frontend.utils import create_shortcuts, resolve_shortcut_game_info, get_xdg_user_dir, sanitize_name

logger = logging.getLogger(__name__)


class ShortcutService:
    """Handles desktop shortcut creation and management for game prefixes."""

    def __init__(self, settings: Any, steam_service: SteamIntegrationService | None = None) -> None:
        """Initialize the shortcut service.

        Args:
            settings: SettingsManager instance providing app configuration.
            steam_service: Optional SteamIntegrationService for adding games to Steam library.
        """
        self.settings = settings
        self.steam_service = steam_service

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
        game_name: str = "",
    ) -> tuple[list[str], list[str], list[str]] | None:
        """Show the shortcut selection dialog and return user's choices.

        Args:
            desktop_files: List of .desktop file paths to select from.
            parent: Parent widget for dialog ownership.
            existing_desktop: List of basenames already on Desktop.
            existing_apps: List of basenames already in Applications.
            game_name: Game display name — used to pre-check Steam shortcuts
                       that are already present in the local Steam library.

        Returns:
            Tuple of (selected_desktop, selected_apps, steam_shortcuts) where
            *steam_shortcuts* is a list of .desktop file paths the user wants
            added to Steam, or ``None`` if cancelled.
        """
        # Read which games are currently in Steam so we can pre-check them.
        steam_names: set[str] = set()
        if game_name and self.steam_service:
            steam_names = self.steam_service.get_steam_names()

        dialog = SelectShortcutsDialog(
            desktop_files,
            parent,
            existing_desktop=existing_desktop or [],
            existing_apps=existing_apps or [],
            steam_names=steam_names,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            desktop_sel, apps_sel = dialog.get_selected_files()
            steam_sel = dialog.get_steam_shortcuts()
            return desktop_sel, apps_sel, steam_sel
        return None

    def create_shortcuts_for_prefix(
        self,
        prefix_path: str,
        game_name: str,
        selected_desktop: list[str],
        selected_apps: list[str],
        parent: object,
        steam_shortcuts: list[str] | None = None,
    ) -> bool:
        """Create desktop shortcuts for a prefix using its stored install config.

        Loads config.json from the prefix's script directories, resolves game info,
        and calls ``create_shortcuts`` from utils.  Optionally adds non-Steam game
        entries to the local Steam library for each selected shortcut.

        Args:
            prefix_path: Full filesystem path to the Wine prefix.
            game_name: Name of the game (for finding scripts dir).
            selected_desktop: Basenames to place on the user's Desktop.
            selected_apps: Basenames to place in ~/.local/share/applications.
            parent: Parent widget for error dialogs.
            steam_shortcuts: List of .desktop file basenames to also add to Steam.

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

        # Optionally add non-Steam game entries to the local Steam library
        logger.info("create_shortcuts_for_prefix(%s): steam_shortcuts=%s steam_service=%s",
                     game_name, steam_shortcuts, self.steam_service)
        if steam_shortcuts and self.steam_service:
            for desktop_bn in steam_shortcuts:
                sh_file = sanitize_name(os.path.splitext(desktop_bn)[0]) + ".sh"
                sh_path = os.path.join(primary_scripts_dir, sh_file)
                logger.info("Steam shortcut candidate: bn=%s sh_path=%s exists=%s",
                             desktop_bn, sh_path, os.path.isfile(sh_path))
                if os.path.isfile(sh_path):
                    try:
                        self.steam_service.add_game_to_steam(
                            name=os.path.splitext(sh_file)[0],
                            exe=sh_path,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to add '%s' to Steam: %s", sh_file, exc)

        return True
