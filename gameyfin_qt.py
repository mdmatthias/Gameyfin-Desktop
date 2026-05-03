import sys
import os
import logging

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from gameyfin_frontend import GameyfinTray
from gameyfin_frontend import GameyfinWindow
from dotenv import load_dotenv

from gameyfin_frontend.umu_database import UmuDatabase
from gameyfin_frontend.utils import get_effective_icon

from gameyfin_frontend.settings import settings_manager



load_dotenv()

log_level = settings_manager.get("GF_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s:%(filename)s:%(lineno)d %(message)s",
)

# Disable Web Security to bypass CORS issues with Authentik redirect
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-web-security"

if __name__ == "__main__":

    app = QApplication(sys.argv)

    # Store default palette and font for "auto" theme fallback
    app.default_palette = app.palette()
    app.default_font = app.font()
    app.default_style_name = app.style().objectName()

    # Apply theme
    theme = settings_manager.get("GF_THEME")
    if theme and theme != "auto":
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme=theme)

    umu_database = UmuDatabase()

    app.setApplicationName("Gameyfin")
    app.setOrganizationName("Gameyfin")
    app.setDesktopFileName("org.gameyfin.Gameyfin-Desktop")

    # Set window icon
    custom_icon_path = settings_manager.get("GF_ICON_PATH")
    app.setWindowIcon(get_effective_icon(custom_icon_path, theme=theme))

    window = GameyfinWindow(umu_database)

    tray_app = GameyfinTray(app, window)

    if int(settings_manager.get("GF_START_MINIMIZED", 0)) == 0:

        window.show()

    sys.exit(app.exec())
