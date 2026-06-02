import logging
import os
from typing import Any

from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction

from .settings import SettingsManager
from .utils import get_effective_icon

logger = logging.getLogger(__name__)


class GameyfinTray:
    def __init__(self, app, window, settings: SettingsManager):
        """Create and show the system tray icon with menu actions.

        Args:
            app: The QApplication instance.
            window: The main GameyfinWindow instance.
            settings: SettingsManager instance providing app configuration.
        """
        self.app = app
        self.window = window
        self.settings = settings
        self.tray = QSystemTrayIcon()

        custom_icon_path = settings.get("GF_ICON_PATH")
        theme = settings.get("GF_THEME")

        self.tray.setIcon(get_effective_icon(custom_icon_path, theme=theme))
        self.menu = QMenu()

        self.show_action = QAction("Gameyfin")
        self.downloads_action = QAction("Downloads")
        self.settings_action = QAction("Settings")
        self.quit_action = QAction("Quit")

        self.menu.addAction(self.show_action)
        self.menu.addAction(self.downloads_action)
        self.menu.addAction(self.settings_action)

        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Gameyfin")

        self.show_action.triggered.connect(self.window.show_main_tab)
        self.quit_action.triggered.connect(self.quit_app)

        self.downloads_action.triggered.connect(self.window.show_downloads_tab)
        self.settings_action.triggered.connect(self.window.show_settings_tab)

        self.tray.activated.connect(self.icon_clicked)
        self.tray.show()

        # Wire tray into download manager and all its existing widgets
        if hasattr(self.window, "download_manager"):
            dm = self.window.download_manager
            dm.tray = self
            for child in dm.widget_map:
                child.tray = self

    def show_notification(self, title: str, message: str, enabled_key: str | None = None) -> None:
        """Show a desktop notification via the system tray icon.

        Respects the user's notification preference stored under *enabled_key*
        (defaults to ``GF_DOWNLOAD_NOTIFICATIONS``).  Notifications are only
        shown when the tray is visible — if the user has quit the app there's
        nobody to notify.

        Args:
            title: Notification title.
            message: Notification body text.
            enabled_key: Settings key that controls whether notifications are
                enabled.  Pass ``None`` to always show regardless of setting.
        """

        if not self.tray.isVisible():
            return

        if enabled_key is not None:
            if int(self.settings.get(enabled_key, 1)) == 0:
                logger.debug("Notifications disabled by setting %s", enabled_key)
                return

        logger.info("Showing notification: %s — %s", title, message)
        # Informational urgency (lowest) — suitable for download/install events
        self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def icon_clicked(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handles single-click on the tray icon — toggles window visibility."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.show_main_tab()

    def quit_app(self) -> None:
        """Performs a full application quit — hides tray, closes window, exits app."""
        self.tray.hide()
        self.window.is_really_quitting = True
        self.window.close()
        self.app.exit()
