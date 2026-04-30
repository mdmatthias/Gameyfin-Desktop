import configparser
import os
import sys
from pathlib import Path
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtCore import Qt


def get_effective_icon(custom_path: str = None, theme: str = None, icon_theme_name: str = "org.gameyfin.Gameyfin-Desktop") -> QIcon:
    """
    Returns the appropriate QIcon based on the selected theme,
    system theme (Light/Dark), or a custom path if provided.

    Args:
        custom_path: Optional custom icon file path.
        theme: Theme string (e.g. "auto", "material_light", etc.).
        icon_theme_name: Fallback QIcon.fromTheme name.

    Returns:
        A QIcon ready to use.
    """
    internal_icon_path = get_app_icon_path(custom_path, theme=theme)

    is_light_variant = "icon_light.png" in internal_icon_path
    has_custom_path = custom_path is not None and custom_path != ""

    if has_custom_path or is_light_variant:
        return QIcon(internal_icon_path)

    icon = QIcon.fromTheme(icon_theme_name)
    if icon.isNull():
        icon = QIcon(internal_icon_path)
    return icon


def build_umu_command(proton_path: str, wine_prefix: str, config: dict, command: str) -> str:
    """
    Builds a shell command string with UMU environment variables prepended.

    Args:
        proton_path: Proton version (e.g. "GE-Proton").
        wine_prefix: WINEPREFIX path.
        config: Dict of additional environment variables.
        command: The command to execute (e.g. "umu-run /path/to/exe").

    Returns:
        A string like: PROTONPATH="GE-Proton" WINEPREFIX="/path" KEY="val" umu-run /path/to/exe
    """
    env_prefix = f'PROTONPATH="{proton_path}" WINEPREFIX="{wine_prefix}" '
    for key, value in config.items():
        if key not in ("PROTONPATH", "WINEPREFIX"):
            env_prefix += f'{key}="{value}" '
    return f"{env_prefix}{command}"


def build_umu_env_prefix(proton_path: str, wine_prefix: str, config: dict) -> str:
    """
    Builds just the environment variable prefix string for UMU commands.

    Args:
        proton_path: Proton version (e.g. "GE-Proton").
        wine_prefix: WINEPREFIX path.
        config: Dict of additional environment variables.

    Returns:
        Environment prefix string.
    """
    env_prefix = f'PROTONPATH="{proton_path}" WINEPREFIX="{wine_prefix}" '
    for key, value in config.items():
        if key not in ("PROTONPATH", "WINEPREFIX"):
            env_prefix += f'{key}="{value}" '
    return env_prefix


def parse_desktop_file(path: str) -> configparser.ConfigParser | None:
    """
    Parses a .desktop file, adding [Desktop Entry] header if missing.

    Args:
        path: Path to the .desktop file.

    Returns:
        A ConfigParser object with the parsed content, or None on failure.
    """
    try:
        with open(path, 'r') as f:
            content = f.read()
        if not content.strip().startswith('[Desktop Entry]'):
            content = '[Desktop Entry]\n' + content

        config_parser = configparser.ConfigParser(strict=False)
        config_parser.optionxform = str
        config_parser.read_string(content)

        if 'Desktop Entry' not in config_parser:
            return None

        return config_parser
    except Exception as e:
        print(f"Error parsing {path}: {e}")
        return None


def copy_icon_from_source(source_dir: str, icon_name: str) -> str | None:
    """
    Finds the best available icon file from a source directory.

    Checks sizes in order: 256x256, 128x128, 64x64, 48x48, 32x32.
    Looks for both <icon>.png and the icon name as-is.

    Args:
        source_dir: Base directory containing icons/<size>/apps/ structure.
        icon_name: The icon name to search for.

    Returns:
        Path to the found icon file, or None if not found.
    """
    sizes_to_check = ["256x256", "128x128", "64x64", "48x48", "32x32"]

    for size in sizes_to_check:
        path_with_png = os.path.join(source_dir, size, "apps", f"{icon_name}.png")
        path_as_is = os.path.join(source_dir, size, "apps", icon_name)

        if os.path.exists(path_with_png):
            return path_with_png
        if os.path.exists(path_as_is):
            return path_as_is

    return None


def build_flatpak_exec_command(inner_cmd: str) -> str:
    """
    Builds a flatpak Exec command string with proper escaping.

    Args:
        inner_cmd: The command to run inside the flatpak container.

    Returns:
        A properly escaped flatpak exec string.
    """
    # Escape special characters for the flatpak -c shell context
    for char in ('\\', '"', '$', '`'):
        inner_cmd = inner_cmd.replace(char, f'\\{char}')
    return f'flatpak run --command=sh org.gameyfin.Gameyfin-Desktop -c "{inner_cmd}"'


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