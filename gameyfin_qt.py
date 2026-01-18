import sys
import os

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from gameyfin_frontend import GameyfinTray
from gameyfin_frontend import GameyfinWindow
from dotenv import load_dotenv

from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import resource_path, get_app_icon_path

from gameyfin_frontend.settings import settings_manager



load_dotenv()



if __name__ == "__main__":

    app = QApplication(sys.argv)

    umu_database = UmuDatabase()

    app.setApplicationName("Gameyfin")
    app.setOrganizationName("Gameyfin")
    app.setDesktopFileName("org.gameyfin.Gameyfin-Desktop")

    # Set window icon
    app_icon = QIcon.fromTheme("org.gameyfin.Gameyfin-Desktop")
    if app_icon.isNull():
        icon_path = get_app_icon_path(settings_manager.get("GF_ICON_PATH"))
        app_icon = QIcon(icon_path)
    app.setWindowIcon(app_icon)

    window = GameyfinWindow(umu_database)

    tray_app = GameyfinTray(app, window)

    if int(settings_manager.get("GF_START_MINIMIZED", 0)) == 0:

        window.show()

    sys.exit(app.exec())
