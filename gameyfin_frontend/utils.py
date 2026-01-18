import os
import sys
from pathlib import Path
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtCore import Qt


def resource_path(relative_path: str) -> str:
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)

    # Fallback for dev: assume relative_path is relative to the project root
    # utils.py is in gameyfin_frontend/
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_app_icon_path(custom_path: str = None, theme: str = None) -> str:
    """
    Returns the appropriate icon path based on the selected theme, 
    system theme (Light/Dark), or a custom path if provided.
    """
    if custom_path and os.path.exists(custom_path):
        return custom_path

    icon_name = "icon.png"

    # 1. Check if a qt-material theme is specified
    if theme and theme != "auto":
        if "light" in theme.lower():
            icon_name = "icon_light.png"
        else:
            icon_name = "icon.png"
    else:
        # 2. Fallback to system theme detection
        app = QGuiApplication.instance()
        if app:
            # Qt 6.5+ supports colorScheme detection
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                icon_name = "icon_light.png"

    return resource_path(os.path.join("gameyfin_frontend", icon_name))


def get_xdg_user_dir(dir_name: str) -> Path:
    """
    Finds a special XDG user directory (like DESKTOP, DOCUMENTS)
    in a language-independent way on Linux by reading the
    ~/.config/user-dirs.dirs file.

    Args:
        dir_name: The internal name of the directory (e.g., "DESKTOP",
                  "DOCUMENTS", "DOWNLOAD").
    """
    key_to_find = f"XDG_{dir_name.upper()}_DIR"

    config_home = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    config_file_path = Path(config_home) / "user-dirs.dirs"

    # Set a sensible fallback (e.g., $HOME/Desktop)
    fallback_dir = Path.home() / dir_name.capitalize()

    if not config_file_path.is_file():
        return fallback_dir

    try:
        with open(config_file_path, "r") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                if line.startswith(key_to_find):
                    try:
                        # Line looks like: XDG_DESKTOP_DIR="$HOME/Desktop"
                        value = line.split("=", 1)[1]
                        value = value.strip('"')

                        # Expand variables like $HOME
                        path = os.path.expandvars(value)

                        return Path(path)

                    except Exception:
                        # Found the key but the line was malformed
                        return fallback_dir

    except Exception as e:
        print(f"Error reading {config_file_path}: {e}")
        return fallback_dir

    # Key wasn't found in the file
    return fallback_dir