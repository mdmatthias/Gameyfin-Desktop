"""Add non-Steam game shortcuts to the local Steam library on Linux.

Steam tracks non-Steam games in a binary VDF file located at
``~/.local/share/Steam/userdata/<steamid>/config/shortcuts.vdf``.
This module reads/writes that file using the ``vdf`` package so games
appear in Steam's Big Picture / Library under "Non-Steam Games".

This mirrors what Heroic Games Launcher does via its ``@node-steam/vdf``
package.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

try:
    import vdf as _vdf_lib
except ImportError:
    _vdf_lib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Standard Steam base directories (relative to $HOME).
# Each contains a ``userdata/<steamid>/config/`` subdirectory.
_STEAM_BASE_PATHS: list[str] = [
    ".local/share/Steam",                 # Native install / Flatpak host share
    ".var/app/com.valvesoftware.Steam",   # Flatpak sandbox
    ".steam/steam",                       # Legacy
]


def _find_steam_shortcuts_vdf() -> str | None:
    """Return the absolute path to ``shortcuts.vdf`` if found.

    Scans standard Steam locations for any user data directory containing
    a ``config/shortcuts.vdf`` file.
    """
    home = os.path.expanduser("~")
    for rel in _STEAM_BASE_PATHS:
        base = os.path.join(home, rel)
        userdata = os.path.join(base, "userdata")
        if not os.path.isdir(userdata):
            continue
        # Find first steamid subdir with config/shortcuts.vdf
        try:
            for entry in sorted(os.listdir(userdata)):
                config_dir = os.path.join(userdata, entry, "config")
                vdf_path = os.path.join(config_dir, "shortcuts.vdf")
                if os.path.isfile(vdf_path):
                    return vdf_path
        except OSError:
            pass
    return None


class SteamIntegrationService:
    """Manage adding/removing non-Steam games from the local Steam library.

    Writes entries into Steam's ``shortcuts.vdf`` binary VDF file so games
    appear in Big Picture mode under "Non-Steam Games".

    Args:
        settings: SettingsManager instance providing app configuration.
    """

    def __init__(self, settings: Any) -> None:
        """Initialize the service.

        Args:
            settings: SettingsManager instance providing app configuration.
        """
        self.settings = settings

    def add_game_to_steam(
        self,
        name: str,
        exe: str,
        start_dir: str,
        icon: str = "",
        launch_options: str = "",
    ) -> bool:
        """Add a non-Steam game entry to Steam via shortcuts.vdf.

        Reads the existing ``shortcuts.vdf``, assigns a new negative AppID,
        writes the shortcut entry, and saves the file back.  If the file does
        not exist (no Steam user data yet), returns False with a warning.

        Args:
            name: Display name for the game in Steam.
            exe: Absolute path to the launcher script (.sh).
            start_dir: Working directory for the game.
            icon: Optional absolute path to an icon file.
            launch_options: Extra command-line options (empty string for none).

        Returns:
            True if the shortcut was written successfully.
        """
        if _vdf_lib is None:
            logger.warning("python-vdf package not installed — cannot write Steam shortcuts.")
            return False

        vdf_path = _find_steam_shortcuts_vdf()
        if not vdf_path:
            logger.warning(
                "Steam shortcuts.vdf not found. Open Steam at least once to create your user profile."
            )
            return False

        logger.info("Writing Steam shortcut '%s' to %s", name, vdf_path)

        # Load existing shortcuts
        try:
            with open(vdf_path, "rb") as f:
                data = _vdf_lib.binary_load(f)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read shortcuts.vdf: %s", exc)
            return False

        shortcuts = data.get("shortcuts", {})

        # Check if an entry with this AppName already exists — reuse it (update in-place).
        target_key: str | None = None
        target_appid: int | None = None
        for k, entry in shortcuts.items():
            if entry.get("AppName") == name:
                target_key = k
                target_appid = entry.get("appid")
                break

        if target_key is not None:
            # Update existing entry in place; skip writing if nothing changed.
            safe_exe = exe.replace("'", "'\\''")
            app_id = "org.gameyfin.Gameyfin-Desktop"
            inner_cmd = f"exec '{safe_exe}'"
            escaped_inner = inner_cmd.replace("'", "'\\''")
            flatpak_exec = (
                f"run --command=sh {app_id} -c '{escaped_inner}'"
            )

            new_entry: dict[str, Any] = {
                "appid": target_appid,
                "AppName": name,
                "Exe": "/usr/bin/flatpak",
                "StartDir": start_dir,
                "icon": icon,
                "ShortcutPath": "/usr/bin/flatpak",
                "LaunchOptions": flatpak_exec,
                "IsHidden": 1,
                "AllowDesktopConfig": 1,
                "AllowOverlay": 1,
                "OpenVR": 0,
                "Devkit": 0,
                "DevkitGameID": "",
                "DevkitOverrideAppID": 0,
                "LastPlayTime": 0,
                "FlatpakAppID": app_id,
                "sortas": "",
                "tags": {},
            }

            if shortcuts[target_key] == new_entry:
                logger.info("Shortcut '%s' unchanged in Steam library.", name)
                return True

            shortcuts[target_key] = new_entry
            logger.info("Updated '%s' in Steam library (AppID=%d).", name, target_appid)
        else:
            # Allocate a new negative AppID.
            used_ids: set[int] = set()
            for e in shortcuts.values():
                appid = e.get("appid")
                if isinstance(appid, int):
                    used_ids.add(appid)
            candidate = -1
            while candidate in used_ids:
                candidate -= 1

            key = str(candidate)

            safe_exe = exe.replace("'", "'\\''")
            app_id = "org.gameyfin.Gameyfin-Desktop"
            inner_cmd = f"exec '{safe_exe}'"
            escaped_inner = inner_cmd.replace("'", "'\\''")
            flatpak_exec = (
                f"run --command=sh {app_id} -c '{escaped_inner}'"
            )

            shortcut_entry: dict[str, Any] = {
                "appid": candidate,
                "AppName": name,
                "Exe": "/usr/bin/flatpak",
                "StartDir": start_dir,
                "icon": icon,
                "ShortcutPath": "/usr/bin/flatpak",
                "LaunchOptions": flatpak_exec,
                "IsHidden": 1,
                "AllowDesktopConfig": 1,
                "AllowOverlay": 1,
                "OpenVR": 0,
                "Devkit": 0,
                "DevkitGameID": "",
                "DevkitOverrideAppID": 0,
                "LastPlayTime": 0,
                "FlatpakAppID": app_id,
                "sortas": "",
                "tags": {},
            }

            shortcuts[key] = shortcut_entry
            logger.info("Added '%s' to Steam library (AppID=%d).", name, candidate)

        data["shortcuts"] = shortcuts

        # Write back atomically via temp file
        tmp_path = vdf_path + ".tmp"
        try:
            with open(tmp_path, "wb") as f:
                _vdf_lib.binary_dump(data, f)
            os.replace(tmp_path, vdf_path)
            return True
        except OSError as exc:
            logger.error("Failed to write shortcuts.vdf: %s", exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

    def remove_game_from_steam(self, name: str) -> bool:
        """Remove a previously-added non-Steam game by deleting its entry.

        Scans ``shortcuts.vdf`` for a matching ``AppName`` and removes it.

        Args:
            name: Display name of the game to remove.

        Returns:
            True if a matching entry was deleted.
        """
        if _vdf_lib is None:
            return False

        vdf_path = _find_steam_shortcuts_vdf()
        if not vdf_path:
            return False

        try:
            with open(vdf_path, "rb") as f:
                data = _vdf_lib.binary_load(f)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read shortcuts.vdf: %s", exc)
            return False

        shortcuts = data.get("shortcuts", {})
        removed = False
        keys_to_delete: list[str] = []

        for key, entry in shortcuts.items():
            app_name = entry.get("AppName", "")
            if isinstance(app_name, str) and app_name == name:
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del shortcuts[key]
            removed = True

        if removed:
            data["shortcuts"] = shortcuts
            tmp_path = vdf_path + ".tmp"
            try:
                with open(tmp_path, "wb") as f:
                    _vdf_lib.binary_dump(data, f)
                os.replace(tmp_path, vdf_path)
                logger.info("Removed '%s' from Steam shortcuts.", name)
            except OSError as exc:
                logger.error("Failed to write shortcuts.vdf: %s", exc)
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        return removed

    def get_steam_names(self) -> set[str]:
        """Return the set of ``AppName`` values currently present in Steam.

        Returns an empty set when the vdf package is unavailable or the
        ``shortcuts.vdf`` file does not exist.
        """
        if _vdf_lib is None:
            return set()

        vdf_path = _find_steam_shortcuts_vdf()
        if not vdf_path:
            return set()

        try:
            with open(vdf_path, "rb") as f:
                data = _vdf_lib.binary_load(f)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to read shortcuts.vdf for listing: %s", exc)
            return set()

        names: set[str] = set()
        for entry in data.get("shortcuts", {}).values():
            app_name = entry.get("AppName")
            if isinstance(app_name, str):
                # Strip trailing .sh so it matches the checkbox label
                # (script basename). Some launchers store AppName with .sh,
                # others don't — handle both.
                if app_name.endswith(".sh"):
                    app_name = app_name[:-3]
                names.add(app_name)
        return names
