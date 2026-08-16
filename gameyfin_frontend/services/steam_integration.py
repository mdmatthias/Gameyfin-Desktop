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

from gameyfin_frontend.config import SCRIPT_PERMISSION
from gameyfin_frontend.utils import build_flatpak_exec_command, sanitize_name

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
        icon: str = "",
    ) -> bool:
        """Add a non-Steam game entry to Steam via shortcuts.vdf.

        Reads the existing ``shortcuts.vdf``, assigns a new negative AppID,
        writes the shortcut entry, and saves the file back.  If the file does
        not exist (no Steam user data yet), returns False with a warning.

        Steam launches ``/bin/sh`` with the wrapper script's absolute path
        as its single (quoted) LaunchOptions argument — both the shell and
        the wrapper script only ever deal in absolute paths, so the working
        directory Steam starts in doesn't matter; "StartDir" is fixed to
        ``/bin``.

        Args:
            name: Display name for the game in Steam.
            exe: Absolute path to the launcher script (.sh).
            icon: Optional absolute path to an icon file.

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

        wrapper_path = self._write_steam_wrapper_script(exe, name)
        if not wrapper_path:
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
            new_entry: dict[str, Any] = {
                "appid": target_appid,
                "AppName": name,
                "Exe": "/bin/sh",
                "StartDir": "/bin",
                "icon": icon,
                "ShortcutPath": "/bin/sh",
                "LaunchOptions": f'"{wrapper_path}"',
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
                self._disable_compat_tool_override(wrapper_path, name)
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

            shortcut_entry: dict[str, Any] = {
                "appid": candidate,
                "AppName": name,
                "Exe": "/bin/sh",
                "StartDir": "/bin",
                "icon": icon,
                "ShortcutPath": "/bin/sh",
                "LaunchOptions": f'"{wrapper_path}"',
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

        self._disable_compat_tool_override(wrapper_path, name)
        return True

    def _write_steam_wrapper_script(self, exe: str, name: str) -> str | None:
        """Write a small shell wrapper that runs the game via flatpak, and return its path.

        Steam's non-Steam-shortcut launch previously pointed "Exe" directly
        at ``/usr/bin/flatpak``, with the flatpak invocation passed via
        "LaunchOptions". Pointing "Exe" at ``/bin/sh`` and "LaunchOptions" at
        this script instead means Steam launches a plain shell script, with
        the flatpak invocation as an implementation detail of that script
        rather than part of Steam's own command line.

        Stored under a dedicated ``steam_shortcut_scripts`` directory
        (rather than alongside the game's own launcher script) since these
        are wrappers Steam calls into, not something a user would run
        directly like the per-desktop-file launcher scripts.

        Args:
            exe: Absolute path to the game's launcher script.
            name: Game name, used to name the wrapper script uniquely.

        Returns:
            Absolute path to the wrapper script, or ``None`` on failure.
        """
        scripts_dir = os.path.join(self.settings.get_config_dir(), "steam_shortcut_scripts")
        try:
            os.makedirs(scripts_dir, exist_ok=True)
        except OSError as exc:
            logger.error("Failed to create %s: %s", scripts_dir, exc)
            return None

        wrapper_path = os.path.join(scripts_dir, f"{sanitize_name(name)}.sh")
        flatpak_exec = build_flatpak_exec_command(exe)
        content = (
            "#!/bin/sh\n\n"
            "# Steam starts this process with LC_ALL=C, which breaks handling\n"
            "# of non-ASCII characters (e.g. accented letters, en-dashes) in\n"
            "# game paths before flatpak even runs. Force UTF-8 back on before\n"
            "# handing off (see lutris/lutris#6837 for the same underlying issue).\n"
            "export LC_ALL=C.UTF-8\n"
            "export LANG=C.UTF-8\n\n"
            f"exec {flatpak_exec}\n"
        )

        try:
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.chmod(wrapper_path, SCRIPT_PERMISSION)
        except OSError as exc:
            logger.error("Failed to write Steam wrapper script %s: %s", wrapper_path, exc)
            return None

        return wrapper_path

    def _disable_compat_tool_override(self, exe: str, name: str) -> bool:
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
            exe: The Exe field value written for this shortcut (the wrapper
                script path from ``_write_steam_wrapper_script``).
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

        appid = str(_generate_shortcut_appid(exe, name))

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
