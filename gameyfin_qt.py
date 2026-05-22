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
from gameyfin_frontend.config import FLATPAK_ID

from gameyfin_frontend.services import MigrationService
from gameyfin_frontend.settings import SettingsManager

load_dotenv()

# Get settings instance early for logging config
settings = SettingsManager.get_instance()
log_level = settings.get("GF_LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.WARNING),
    format="%(asctime)s %(levelname)s %(name)s:%(filename)s:%(lineno)d %(message)s",
)

# Disable Web Security to bypass CORS issues with Authentik redirect
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-web-security"

# Run one-time legacy data migration (settings, shortcuts, prefixes)
logger.info("Starting legacy data migration...")
migration = MigrationService(settings.get_config_dir())
result = migration.migrate()
total_migrated = sum(result.values()) if result else 0
logger.info(
    "Legacy data migration complete: %d settings, %d shortcut dirs, %d prefixes",
    result.get("settings", 0),
    result.get("shortcuts", 0),
    result.get("prefixes", 0),
)
if total_migrated > 0:
    logger.info("%d items migrated — restart the app to use new locations.", total_migrated)

if __name__ == "__main__":
    logger.debug("Creating QApplication...")
    app = QApplication(sys.argv)
    logger.debug("QApplication created.")

    # Store default palette and font for "auto" theme fallback
    app.default_palette = app.palette()
    app.default_font = app.font()
    app.default_style_name = app.style().objectName()

    # Apply theme
    theme = settings.get("GF_THEME")
    if theme and theme != "auto":
        from qt_material import apply_stylesheet
        apply_stylesheet(app, theme=theme)

    umu_database = UmuDatabase(settings)

    app.setApplicationName("Gameyfin")
    app.setOrganizationName("Gameyfin")
    app.setDesktopFileName(FLATPAK_ID)

    # Set window icon
    custom_icon_path = settings.get("GF_ICON_PATH")
    app.setWindowIcon(get_effective_icon(custom_icon_path, theme=theme))

    window = GameyfinWindow(umu_database, settings)

    tray_app = GameyfinTray(app, window, settings)

    if int(settings.get("GF_START_MINIMIZED", 0)) == 0:

        window.show()

    sys.exit(app.exec())
