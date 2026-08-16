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
import zlib
from pathlib import Path
from typing import Any

try:
    import vdf as _vdf_lib
except ImportError:
    _vdf_lib = None  # type: ignore[assignment]

from gameyfin_frontend.utils import build_flatpak_exec_command

logger = logging.getLogger(__name__)

# Every Gameyfin shortcut uses this as its "Exe" field (see add_game_to_steam
# below) — kept as one constant since it must match exactly what's written
# to shortcuts.vdf for the CRC-based AppID in _generate_shortcut_appid to
# resolve to the same entry Steam itself computes.
_SHORTCUT_EXE = "/usr/bin/flatpak"

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


def _find_steam_config_vdf() -> str | None:
    """Return the absolute path to Steam's shared ``config.vdf`` if found.

    Unlike ``shortcuts.vdf``, this file isn't per-user data — it lives
    directly under the Steam install directory.
    """
    home = os.path.expanduser("~")
    for rel in _STEAM_BASE_PATHS:
        vdf_path = os.path.join(home, rel, "config", "config.vdf")
        if os.path.isfile(vdf_path):
            return vdf_path
    return None


def _generate_shortcut_appid(exe: str, name: str) -> int:
    """Compute Steam's canonical 32-bit AppID for a non-Steam shortcut.

    Steam derives a shortcut's real AppID from a CRC32 hash of its Exe +
    AppName fields (with the top bit set), not from the small placeholder
    integer Gameyfin writes into ``shortcuts.vdf`` for its own bookkeeping.
    This mirrors that formula (also used by tools like Heroic Games
    Launcher and steam-rom-manager) so a compatibility-tool override can be
    written against the same AppID Steam itself will resolve.
    """
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


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
            full_cmd = build_flatpak_exec_command(exe)
            flatpak_exec = full_cmd[len("flatpak "):]  # Strip "flatpak " prefix for Steam LaunchOptions

            new_entry: dict[str, Any] = {
                "appid": target_appid,
                "AppName": name,
                "Exe": _SHORTCUT_EXE,
                "StartDir": start_dir,
                "icon": icon,
                "ShortcutPath": _SHORTCUT_EXE,
                "LaunchOptions": flatpak_exec,
                "IsHidden": 1,
                "AllowDesktopConfig": 1,
                "AllowOverlay": 1,
                "OpenVR": 0,
                "Devkit": 0,
                "DevkitGameID": "",
                "DevkitOverrideAppID": 0,
                "LastPlayTime": 0,
                "FlatpakAppID": "",
                "sortas": "",
                "tags": {},
            }

            if shortcuts[target_key] == new_entry:
                logger.info("Shortcut '%s' unchanged in Steam library.", name)
                self._disable_compat_tool_override(name)
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

            full_cmd = build_flatpak_exec_command(exe)
            flatpak_exec = full_cmd[len("flatpak "):]  # Strip "flatpak " prefix for Steam LaunchOptions

            shortcut_entry: dict[str, Any] = {
                "appid": candidate,
                "AppName": name,
                "Exe": _SHORTCUT_EXE,
                "StartDir": start_dir,
                "icon": icon,
                "ShortcutPath": _SHORTCUT_EXE,
                "LaunchOptions": flatpak_exec,
                "IsHidden": 1,
                "AllowDesktopConfig": 1,
                "AllowOverlay": 1,
                "OpenVR": 0,
                "Devkit": 0,
                "DevkitGameID": "",
                "DevkitOverrideAppID": 0,
                "LastPlayTime": 0,
                "FlatpakAppID": "",
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
        except OSError as exc:
            logger.error("Failed to write shortcuts.vdf: %s", exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

        self._disable_compat_tool_override(name)
        return True

    def _disable_compat_tool_override(self, name: str) -> bool:
        """Force Steam to skip Steam Play/Proton wrapping for this shortcut.

        Our shortcut already manages its own compatibility layer via an
        embedded umu-run/Proton invocation. If the user has "Enable Steam
        Play for all other titles" turned on globally (SteamOS/Steam Deck
        defaults to this), Steam wraps the shortcut in its own Proton on
        top of ours, which breaks the launch. There's no way to opt a
        single shortcut out of that from Steam Deck's simplified Gaming
        Mode settings, so this writes the override directly into
        ``config.vdf``'s ``CompatToolMapping``, keyed by Steam's own
        CRC32-derived AppID (see ``_generate_shortcut_appid``).

        This is best-effort: failures are logged and swallowed rather than
        failing the whole shortcut-creation flow, since the shortcut itself
        was already written successfully at this point.

        Args:
            name: AppName of the shortcut just written.

        Returns:
            True if the override was written (or already present).
        """
        if _vdf_lib is None:
            return False

        config_path = _find_steam_config_vdf()
        if not config_path:
            logger.warning("Steam config.vdf not found — cannot set compat tool override.")
            return False

        appid = str(_generate_shortcut_appid(_SHORTCUT_EXE, name))

        try:
            with open(config_path, "r", encoding="utf-8", errors="replace") as f:
                data = _vdf_lib.load(f)
        except OSError as exc:
            logger.error("Failed to read config.vdf: %s", exc)
            return False

        try:
            steam_section = (
                data.setdefault("InstallConfigStore", {})
                .setdefault("Software", {})
                .setdefault("Valve", {})
                .setdefault("Steam", {})
            )
            compat_mapping = steam_section.setdefault("CompatToolMapping", {})

            existing = compat_mapping.get(appid)
            if existing is not None and existing.get("name", "") == "":
                logger.info("Compat tool override already set for AppID %s.", appid)
                return True

            compat_mapping[appid] = {"name": "", "config": "", "priority": "250"}
        except AttributeError as exc:
            logger.error("Unexpected config.vdf structure — not overriding compat tool: %s", exc)
            return False

        tmp_path = config_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                _vdf_lib.dump(data, f, pretty=True)
            os.replace(tmp_path, config_path)
        except OSError as exc:
            logger.error("Failed to write config.vdf: %s", exc)
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False

        logger.info("Set 'no compatibility tool' override for AppID %s in config.vdf", appid)
        return True

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
