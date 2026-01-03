import os
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from .settings import settings_manager


class GameyfinTray:
    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.tray = QSystemTrayIcon()
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        self.tray.setIcon(QIcon(settings_manager.get("GF_ICON_PATH", icon_path)))
        self.menu = QMenu()

        self.show_action = QAction("Show")
        self.downloads_action = QAction("Show Downloads")
        self.quit_action = QAction("Quit")

        self.menu.addAction(self.show_action)
        self.menu.addAction(self.downloads_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)

        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Gameyfin")

        self.show_action.triggered.connect(self.window.show)
        self.quit_action.triggered.connect(self.quit_app)

        self.downloads_action.triggered.connect(self.window.show_downloads_tab)

        self.tray.activated.connect(self.icon_clicked)
        self.tray.show()

    def icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible():
                self.window.hide()
            else:
                self.window.show()

    def quit_app(self):
        self.tray.hide()
        self.window.is_really_quitting = True
        self.window.close()
        self.app.exit()