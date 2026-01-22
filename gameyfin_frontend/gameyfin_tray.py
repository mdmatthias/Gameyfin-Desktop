import os
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from .settings import settings_manager
from .utils import get_app_icon_path


class GameyfinTray:
    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.tray = QSystemTrayIcon()
        
        icon_name = "org.gameyfin.Gameyfin-Desktop"
        
        custom_icon_path = settings_manager.get("GF_ICON_PATH")
        theme = settings_manager.get("GF_THEME")
        
        internal_icon_path = get_app_icon_path(custom_icon_path, theme=theme)
        
        is_light_variant = "icon_light.png" in internal_icon_path
        has_custom_path = custom_icon_path is not None and custom_icon_path != ""

        if has_custom_path or is_light_variant:
             icon = QIcon(internal_icon_path)
        else:
             # Try to use the theme icon (especially for Flatpak), fall back to file path
             icon = QIcon.fromTheme(icon_name)
             if icon.isNull():
                 icon = QIcon(internal_icon_path)
            
        self.tray.setIcon(icon)
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

    def icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.show_main_tab()

    def quit_app(self):
        self.tray.hide()
        self.window.is_really_quitting = True
        self.window.close()
        self.app.exit()