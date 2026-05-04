import configparser
import logging
import os
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtCore import Qt

logger = logging.getLogger(__name__)

FLATPAK_ID = "org.gameyfin.Gameyfin-Desktop"


def get_effective_icon(custom_path: str = None, theme: str = None, icon_theme_name: str = FLATPAK_ID) -> QIcon:
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
    except (OSError, configparser.Error) as e:
        logger.error("Error parsing %s: %s", path, e)
        return None


def copy_icon_from_source(source_dir: str, icon_name: str) -> str | None:
    """
    Finds the best available icon file from a source directory.

    Checks sizes in order: 256x256, 128x128, 64x64, 48x48, 32x32.
    Looks for both <icon>.png and the icon name as-is.
    Searches multiple possible locations where icons may be stored.

    Args:
        source_dir: Base directory (e.g. proton_shortcuts/ or drive_c/).
        icon_name: The icon name to search for.

    Returns:
        Path to the found icon file, or None if not found.
    """
    sizes_to_check = ["256x256", "128x128", "64x64", "48x48", "32x32"]

    # Possible locations to search for icons, relative to source_dir
    # source_dir is typically the proton_shortcuts/ directory containing the .desktop file
    search_dirs = [
        "icons",                                # Direct icons/ subdirectory
        "drive_c/icons",                        # drive_c/icons/
        "drive_c/proton_shortcuts/icons",       # drive_c/proton_shortcuts/icons/ (from game root)
        "../drive_c/proton_shortcuts/icons",    # proton_shortcuts/../drive_c/proton_shortcuts/icons/
    ]

    for search_base in search_dirs:
        for size in sizes_to_check:
            path_with_png = os.path.join(source_dir, search_base, size, "apps", f"{icon_name}.png")
            path_as_is = os.path.join(source_dir, search_base, size, "apps", icon_name)

            if os.path.exists(path_with_png):
                return path_with_png
            if os.path.exists(path_as_is):
                return path_as_is

    # Fallback: search without size directory
    for search_base in search_dirs:
        for ext in [".png", ""]:
            path_with_png = os.path.join(source_dir, search_base, "apps", f"{icon_name}{ext}")
            if os.path.exists(path_with_png):
                return path_with_png

    return None


def install_icon_for_shortcut(icon_path: str, icon_name: str) -> str | None:
    """
    Copies an icon file to the gameyfin icons directory and returns the absolute path.
    Using an absolute path in the Icon field is more reliable than theme lookup,
    especially inside Flatpak sandboxes where icon theme directories may not be accessible.

    Args:
        icon_path: Source path to the icon file.
        icon_name: Base name from the .desktop file (used for uniqueness).

    Returns:
        Absolute path to the installed icon file, or None if installation failed.
    """
    if not icon_path or not os.path.exists(icon_path):
        return None

    # Determine the icon size directory from the source path
    parts = icon_path.split(os.sep)
    size_dir = None
    for i, part in enumerate(parts):
        if part in ["256x256", "128x128", "64x64", "48x48", "32x32"]:
            size_dir = part
            break

    if not size_dir:
        size_dir = "256x256"

    # Install to ~/.local/share/icons/gameyfin/<size>/apps/
    icon_install_dir = os.path.join(
        os.path.expanduser("~"), ".local", "share", "icons", "gameyfin",
        size_dir, "apps"
    )
    os.makedirs(icon_install_dir, exist_ok=True)

    # Derive a unique name from the original icon filename (strip extension)
    base_name = os.path.splitext(os.path.basename(icon_path))[0]
    safe_name = base_name.replace("/", "_").replace("\\", "_")
    dest_filename = f"{safe_name}.png"
    dest_path = os.path.join(icon_install_dir, dest_filename)

    # Copy the icon file
    import shutil
    try:
        shutil.copy2(icon_path, dest_path)
        logger.info("Installed icon to: %s", dest_path)
        # Return absolute path — desktop environments support this in the Icon field
        return dest_path
    except (OSError, shutil.Error) as e:
        logger.error("Failed to install icon: %s", e)
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
    return f'flatpak run --command=sh {FLATPAK_ID} -c "{inner_cmd}"'


def resource_path(relative_path: str) -> str:
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)

    # Fallback for dev: assume relative_path is relative to the project root
    # utils.py is in gameyfin_frontend/
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def get_app_icon_path(custom_path: str | None = None, theme: str | None = None) -> str:
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

                    except (ValueError, IndexError):
                        # Found the key but the line was malformed
                        logger.warning("Malformed line in %s: %s", config_file_path, line)
                        return fallback_dir

    except OSError as e:
        logger.error("Error reading %s: %s", config_file_path, e)
        return fallback_dir

    # Key wasn't found in the file
    return fallback_dir


def create_shortcuts(
    all_desktop_files: list[str],
    scripts_dir: str,
    wine_prefix: str,
    install_config: dict[str, Any],
    proton_path: str = "GE-Proton",
    selected_desktop: list[str] | None = None,
    selected_apps: list[str] | None = None,
    remove_unselected: bool = False,
) -> None:
    """
    Creates helper .sh scripts and system .desktop shortcuts for a game.

    Args:
        all_desktop_files: List of paths to detected .desktop files.
        scripts_dir: Directory where helper .sh scripts are stored.
        wine_prefix: WINEPREFIX path for the game.
        install_config: Dict of install configuration (env vars, USE_HOST_UMU, etc.).
        proton_path: Proton version string. Defaults to "GE-Proton".
        selected_desktop: Desktop files to place on the user's Desktop.
        selected_apps: Desktop files to place in ~/.local/share/applications.
        remove_unselected: If True, removes system shortcuts not in selected lists.
    """
    os.makedirs(scripts_dir, exist_ok=True)

    # 1. Create/update .sh helper scripts for ALL detected desktop files
    for original_path in all_desktop_files:
        try:
            config_parser = parse_desktop_file(original_path)
            if config_parser is None:
                continue

            entry = config_parser["Desktop Entry"]
            working_dir = entry.get("Path")
            exe_name = entry.get("StartupWMClass")
            if not exe_name:
                exe_name = entry.get("Name", "game") + ".exe"

            if not working_dir:
                continue

            exe_path = os.path.join(working_dir, exe_name)
            env_prefix = build_umu_env_prefix(proton_path, wine_prefix, install_config)
            command_to_run = f"{env_prefix} umu-run \"{exe_path}\""

            script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
            script_path = os.path.join(scripts_dir, script_name)
            script_content = f"#!/bin/sh\n\n# Auto-generated by Gameyfin\n{command_to_run}\n"

            with open(script_path, "w") as f:
                f.write(script_content)
            os.chmod(script_path, 0o755)
            logger.info("Created/Updated helper script: %s", script_path)

        except (OSError, configparser.Error) as e:
            logger.error("Failed to create helper script for %s: %s", original_path, e)

    # 2. Manage system .desktop files (Desktop + Applications)
    home_dir = os.path.expanduser("~")
    locs = [
        (os.path.join(home_dir, get_xdg_user_dir("DESKTOP")), selected_desktop or []),
        (os.path.join(home_dir, ".local", "share", "applications"), selected_apps or []),
    ]

    for target_dir, selected_list in locs:
        os.makedirs(target_dir, exist_ok=True)

        # Remove those NOT selected for this specific location
        if remove_unselected:
            to_remove = [f for f in all_desktop_files if f not in selected_list]
            for original_path in to_remove:
                target_path = os.path.join(target_dir, os.path.basename(original_path))
                if os.path.exists(target_path):
                    try:
                        os.remove(target_path)
                        logger.info("Removed system shortcut: %s", target_path)
                    except OSError as e:
                        logger.error("Failed to remove system shortcut %s: %s", target_path, e)

        # Create/Update those selected for this specific location
        for original_path in selected_list:
            try:
                config_parser = parse_desktop_file(original_path)
                if config_parser is None:
                    continue


                # Icon handling - find and install icon to system directory
                icon_name = entry.get("Icon")
                if icon_name:
                    source_dir = os.path.dirname(original_path)
                    found_icon_path = copy_icon_from_source(source_dir, icon_name)
                    if found_icon_path:
                        installed_icon = install_icon_for_shortcut(found_icon_path, icon_name)
                        if installed_icon:
                            config_parser.set("Desktop Entry", "Icon", installed_icon)

                script_name = os.path.splitext(os.path.basename(original_path))[0] + ".sh"
                script_path = os.path.join(scripts_dir, script_name)

                use_host_umu = install_config.get("USE_HOST_UMU", "0")

                if use_host_umu == "0":
                    flatpak_exec = build_flatpak_exec_command(script_path)
                    config_parser.set("Desktop Entry", "Exec", flatpak_exec)
                else:
                    config_parser.set("Desktop Entry", 'Exec', f'"{script_path}"')

                config_parser.set("Desktop Entry", "Type", "Application")
                config_parser.set("Desktop Entry", "Categories", "Application;Game;")

                new_file_path = os.path.join(target_dir, os.path.basename(original_path))
                with open(new_file_path, "w") as f:
                    config_parser.write(f)
                os.chmod(new_file_path, 0o755)
                logger.info("Successfully created system shortcut at: %s", new_file_path)

            except (OSError, configparser.Error) as e:
                logger.error("Failed to process system shortcut %s: %s", original_path, e)