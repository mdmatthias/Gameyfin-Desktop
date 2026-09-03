"""System tab: basic app/system information and the exit action."""

import logging
import platform

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (QFormLayout, QGroupBox, QLabel, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget)

from ..config import APP_VERSION
from ..dialogs import UpdateDialog
from ..settings import SettingsManager

logger = logging.getLogger(__name__)


class SystemTabWidget(QWidget):
    """Tab showing basic system information and app-level actions.

    The "Application" section holds a "Check for Updates" button and an
    "Exit Gameyfin" button. Exiting is confirmed with a dialog; the widget
    itself never quits anything, it only emits ``quit_requested`` and lets
    the main window decide how to shut down (full cleanup, tray, event
    loop), the same way the tray's Quit action does.
    """

    quit_requested = pyqtSignal()

    def __init__(self, parent=None, settings: SettingsManager | None = None):
        """Create the System tab.

        Args:
            parent: Parent widget (the main window).
            settings: SettingsManager instance providing the app data location.
        """
        super().__init__(parent)
        self.settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self._build_info_section())
        layout.addStretch()
        layout.addWidget(self._build_application_section())

    def _build_info_section(self) -> QGroupBox:
        box = QGroupBox("System Information")
        form = QFormLayout(box)
        form.addRow("Version:", QLabel(APP_VERSION))
        form.addRow("Platform:", QLabel(platform.platform()))
        form.addRow("Python:", QLabel(platform.python_version()))
        if self.settings is not None:
            config_dir = self.settings.get_config_dir()
            if config_dir:
                form.addRow("Data directory:", QLabel(config_dir))
        return box

    def _build_application_section(self) -> QGroupBox:
        box = QGroupBox("Application")
        layout = QVBoxLayout(box)

        self.update_button = QPushButton("Check for Updates")
        self.update_button.clicked.connect(self.check_for_updates)
        layout.addWidget(self.update_button)

        self.exit_button = QPushButton("Exit Gameyfin")
        self.exit_button.clicked.connect(self._on_exit_clicked)
        layout.addWidget(self.exit_button)
        return box

    def check_for_updates(self) -> None:
        """Open the update dialog that checks GitHub and can install the latest release."""
        dialog = UpdateDialog(self, self.settings)
        dialog.exec()

    def _on_exit_clicked(self) -> None:
        reply = QMessageBox.question(
            self,
            "Exit Gameyfin",
            "Are you sure you want to exit Gameyfin?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        logger.info("Exit confirmed from the System tab")
        self.quit_requested.emit()
