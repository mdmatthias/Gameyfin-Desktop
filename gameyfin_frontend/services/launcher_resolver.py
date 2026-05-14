"""Resolve game executables from downloaded game directories."""

from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QDialog, QMessageBox

from gameyfin_frontend.dialogs import SelectLauncherDialog

logger = logging.getLogger(__name__)


class LauncherResolver:
    """Walks a game directory, finds .exe files, and lets the user pick one."""

    def find_launcher_paths(self, target_dir: str) -> list[str]:
        """Walk ``target_dir`` and collect all .exe files.

        Args:
            target_dir: Root directory to search.

        Returns:
            List of absolute paths to .exe files.
        """
        launcher_paths: list[str] = []
        try:
            for root, _dirs, files in os.walk(target_dir):
                for file in files:
                    if file.lower().endswith(".exe"):
                        launcher_paths.append(os.path.join(root, file))
        except OSError as e:
            logger.error("Error searching for launcher: %s", e)
        return launcher_paths

    def handle_launcher_selection(
        self,
        target_dir: str,
        parent: object,
        on_no_exe: object | None = None,
        on_no_launcher: object | None = None,
        on_cancelled: object | None = None,
    ) -> str | None:
        """Search for .exe files and let the user select one if multiple found.

        Args:
            target_dir: Directory containing game files.
            parent: Parent widget for dialog ownership.
            on_no_exe: Callable invoked when no .exe is found.
            on_no_launcher: Callable invoked when user selects nothing.
            on_cancelled: Callable invoked when user cancels the dialog.

        Returns:
            Selected launcher path, or ``None`` on error/cancel.
        """
        launcher_paths = self.find_launcher_paths(target_dir)

        if not launcher_paths:
            logger.info("No .exe files found in %s", target_dir)
            if on_no_exe:
                on_no_exe()
            return None

        if len(launcher_paths) == 1:
            return launcher_paths[0]

        dialog = SelectLauncherDialog(target_dir, launcher_paths, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            launcher_to_run = dialog.get_selected_launcher()
            if not launcher_to_run:
                logger.info("User selected no launcher from %d candidates", len(launcher_paths))
                if on_no_launcher:
                    on_no_launcher()
                return None
            return launcher_to_run
        else:
            logger.info("User cancelled launcher selection")
            if on_cancelled:
                on_cancelled()
            return None
